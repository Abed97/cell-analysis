"""Local HTTP server (127.0.0.1 only) that the app window talks to. Standard library only."""

from __future__ import annotations

import json
import math
import mimetypes
import os
import platform
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

from . import APP_NAME, GITHUB_REPO, __version__
from . import analysis, render
from .settings import load_json, save_json

WEB_DIR = Path(__file__).parent / "web"


def app_data_dir() -> Path:
    if sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / APP_NAME
    elif os.name == "nt":
        d = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    else:
        d = Path.home() / ".pip-fucci"
    d.mkdir(parents=True, exist_ok=True)
    return d


def demo_dir() -> Path:
    docs = Path.home() / "Documents"
    base = docs if docs.is_dir() else Path.home()
    return base / "PIP-FUCCI demo data"


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return _clean(o.tolist())
    if isinstance(o, Path):
        return str(o)
    return o


class AppState:
    def __init__(self):
        self.exp = None
        self.window = None  # pywebview window, when running as an app
        self.job = None
        self.progress = {"running": False}
        self.cancel = threading.Event()
        self.lock = threading.RLock()
        self.update_info = None
        self.restart_requested = False
        self.healthy = False

    # ---------------- recent experiments
    def recent(self) -> list:
        items = load_json(app_data_dir() / "recent.json", []) or []
        return [r for r in items if Path(r.get("folder", "")).is_dir()][:8]

    def add_recent(self, exp) -> None:
        items = [r for r in self.recent() if r.get("folder") != str(exp.folder)]
        items.insert(0, {"folder": str(exp.folder), "name": exp.settings.experiment_name or exp.folder.name,
                         "opened": time.strftime("%Y-%m-%d %H:%M")})
        save_json(app_data_dir() / "recent.json", items[:8])

    # ---------------- background jobs
    def start_job(self, name: str, fn) -> None:
        with self.lock:
            if self.progress.get("running"):
                raise RuntimeError("Something is already running. Wait for it to finish or cancel it.")
            self.cancel.clear()
            self.progress = {"running": True, "job": name, "stage": "Starting", "fraction": 0.0, "eta_s": None,
                             "started": time.time()}

        def report(**kw):
            self.progress.update(kw)

        def target():
            try:
                fn(report)
                self.progress.update({"running": False, "fraction": 1.0, "stage": "Done", "error": None,
                                      "finished": time.time()})
            except Exception as e:  # noqa: BLE001
                from .experiment import Cancelled

                if isinstance(e, Cancelled):
                    self.progress.update({"running": False, "stage": "Cancelled", "error": None, "cancelled": True})
                else:
                    traceback.print_exc()
                    self.progress.update({"running": False, "stage": "Failed", "error": str(e)})

        self.job = threading.Thread(target=target, daemon=True)
        self.job.start()


STATE = AppState()


def experiment_state(exp) -> dict:
    if exp is None:
        return None
    ok, msg = exp.channels_ok()
    s = exp.settings
    return {
        "summary": exp.summary(),
        "settings": s.to_dict(),
        "derived": s.derived(),
        "has_results": exp.has_results,
        "stale": exp.stale,
        "channels_ok": ok,
        "channels_msg": msg,
        "movies": [{"well": m["well"], "field": m["field"], "shape": m.get("shape")}
                   for m in exp.meta.get("movies", {}).values()] if exp.has_results else [],
        "hours": exp.frame_hours(),
        "n_tracks": int(len(exp.tracks)) if exp.has_results else 0,
        "n_cells": int(len(exp.cells)) if exp.has_results else 0,
        "results_dir": str(exp.results_dir),
        "run": exp.meta.get("run", {}).get("finished") if exp.has_results else None,
        "floor": exp.meta.get("floor"),
    }


def state() -> dict:
    from .segment import cellpose_available
    from .updater import UPDATER, mark_healthy

    if not STATE.healthy:  # the window loaded and asked for state: this version works
        STATE.healthy = True
        try:
            just = mark_healthy()
            if just:
                UPDATER._set(just_updated=just)
        except Exception:
            pass
    return {
        "app": APP_NAME, "version": __version__, "repo": GITHUB_REPO, "platform": sys.platform,
        "native_dialogs": STATE.window is not None or sys.platform == "darwin",
        "cellpose": cellpose_available(),
        "experiment": experiment_state(STATE.exp),
        "progress": STATE.progress,
        "recent": STATE.recent(),
        "demo_folder": str(demo_dir()),
    }


# ------------------------------------------------------------------ actions
def open_folder(path: str):
    from .experiment import Experiment

    p = Path(path).expanduser()
    if not p.is_dir():
        raise ValueError(f"Folder not found: {p}")
    exp = Experiment(p)
    STATE.exp = exp
    exp.save()
    STATE.add_recent(exp)
    return state()


def pick_folder():
    w = STATE.window
    if w is not None:
        try:
            import webview

            kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None)
            if kind is None:
                kind = webview.FOLDER_DIALOG
            res = w.create_file_dialog(kind)
            if res:
                return res[0] if isinstance(res, (list, tuple)) else res
            return None
        except Exception:
            traceback.print_exc()
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["osascript", "-e", 'POSIX path of (choose folder with prompt "Choose the Incucyte export folder")'],
                                 capture_output=True, text=True, timeout=600)
            p = out.stdout.strip()
            return p or None
        except Exception:
            return None
    return None


def reveal(path: str):
    p = Path(path)
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)] if p.is_file() else ["open", str(p)])
    elif os.name == "nt":
        os.startfile(str(p))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(p)])


def open_url(url: str):
    if not url.startswith("https://github.com/"):
        raise ValueError("Only GitHub links can be opened")
    import webbrowser

    webbrowser.open(url)


def check_update():
    if STATE.update_info is not None or not GITHUB_REPO:
        return STATE.update_info or {"available": False}
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": APP_NAME})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode())
        latest = str(data.get("tag_name", "")).lstrip("v")

        def vt(v):
            return tuple(int(x) for x in v.split(".") if x.isdigit())

        avail = bool(latest) and vt(latest) > vt(__version__)
        STATE.update_info = {"available": avail, "latest": latest, "url": data.get("html_url")}
    except Exception:
        STATE.update_info = {"available": False}
    return STATE.update_info


def need_exp():
    if STATE.exp is None:
        raise ValueError("Open an experiment folder first.")
    return STATE.exp


def need_results():
    exp = need_exp()
    if not exp.has_results:
        raise ValueError("Run the analysis first.")
    return exp


def run_analysis():
    exp = need_exp()

    def job(report):
        exp.run(progress=report, cancel=STATE.cancel)

    STATE.start_job("analysis", job)
    return {"ok": True}


def run_retrack():
    exp = need_results()

    def job(report):
        report(stage="Re-linking tracks", fraction=0.3)
        exp.retrack()

    STATE.start_job("retrack", job)
    return {"ok": True}


def make_demo_job():
    from .demo import make_demo

    target = demo_dir()

    def job(report):
        if not (target / "PIP-FUCCI results" / "experiment.json").exists():
            report(stage="Creating demo images", fraction=0.02)
            make_demo(target, progress=lambda f: report(stage="Creating demo images", fraction=0.5 * f))
        open_folder(str(target))
        exp = STATE.exp
        if not exp.has_results:
            exp.run(progress=lambda **kw: report(**{**kw, "fraction": 0.5 + 0.5 * kw.get("fraction", 0)}),
                    cancel=STATE.cancel)

    STATE.start_job("demo", job)
    return {"ok": True}


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "PIPFUCCI/1"

    def log_message(self, fmt, *args):  # quiet
        pass

    def _send(self, code, body: bytes, ctype: str, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(_clean(obj)).encode(), "application/json")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode() or "{}")
        except Exception:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"):
                return self._static("index.html")
            if u.path.startswith("/static/"):
                return self._static(u.path[len("/static/"):])
            if not u.path.startswith("/api/"):
                return self._send(404, b"not found", "text/plain")
            ep = u.path[5:]
            if ep == "state":
                return self._json(state())
            if ep == "progress":
                return self._json(STATE.progress)
            if ep == "frame":
                exp = need_exp()
                layers = set((q.get("layers") or "phase,red,green,masks").split(","))
                arr = render.composite(exp, q["well"], int(q["field"]), int(q["frame"]), layers, q.get("sel") or None)
                return self._send(200, render.encode(arr, "JPEG", 88), "image/jpeg")
            if ep == "montage":
                exp = need_results()
                arr, _ = render.montage(exp, q["key"])
                return self._send(200, render.encode(arr, "PNG"), "image/png")
            if ep == "scatter":
                exp = need_results()
                fld = int(q["field"]) if q.get("field") else None
                return self._json(analysis.scatter(exp, int(q["frame"]), q.get("well") or None, fld, q.get("condition") or None))
            if ep == "stats":
                exp = need_results()
                return self._json(analysis.detection_stats(exp, q["well"], int(q["field"]), int(q["frame"])))
            if ep == "population":
                return self._json(analysis.population(need_results()))
            if ep == "tracks":
                exp = need_results()
                return self._json(analysis.tracks_overview(exp, q.get("condition") or None, q.get("well") or None,
                                                           q.get("sort") or "division"))
            if ep == "track":
                exp = need_results()
                d = analysis.track_detail(exp, q["key"])
                _, picks = render.montage_picks(exp, q["key"])
                d["montage_frames"] = picks
                return self._json(d)
            if ep == "durations":
                return self._json(analysis.durations_by_condition(need_results()))
            if ep == "update":
                from .updater import UPDATER

                if UPDATER.status["enabled"]:
                    return self._json(UPDATER.snapshot())
                return self._json({**check_update(), "enabled": False})
            return self._send(404, b"unknown endpoint", "text/plain")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": str(e)}, 400)

    def do_POST(self):
        u = urlparse(self.path)
        b = self._body()
        try:
            ep = u.path[5:] if u.path.startswith("/api/") else ""
            if ep == "open":
                return self._json(open_folder(b.get("path", "")))
            if ep == "pick_folder":
                return self._json({"path": pick_folder()})
            if ep == "close":
                STATE.exp = None
                return self._json(state())
            if ep == "settings":
                exp = need_exp()
                r = exp.update_settings(b)
                if exp.has_results and ("wells" in b or "conditions" in b):
                    exp._build_tracks()
                return self._json({"result": r, "state": state()})
            if ep == "gates":
                exp = need_results()
                if b.get("auto"):
                    exp.settings.gate_green = None
                    exp.settings.gate_red = None
                    exp.classify()
                    exp._save_results()
                    exp.save()
                else:
                    exp.update_settings({k: b[k] for k in ("gate_green", "gate_red") if k in b})
                return self._json({"gate_green": exp.settings.gate_green, "gate_red": exp.settings.gate_red})
            if ep == "run":
                return self._json(run_analysis())
            if ep == "retrack":
                return self._json(run_retrack())
            if ep == "demo":
                return self._json(make_demo_job())
            if ep == "cancel":
                STATE.cancel.set()
                return self._json({"ok": True})
            if ep == "track_flag":
                exp = need_results()
                kw = {k: b[k] for k in ("excluded", "reviewed") if k in b}
                return self._json(exp.track_flag(b["key"], **kw))
            if ep == "export":
                from .export import export_all

                out = export_all(need_results())
                return self._json({"path": str(out)})
            if ep == "reveal":
                reveal(b.get("path") or str(need_exp().results_dir))
                return self._json({"ok": True})
            if ep == "restart":
                from .updater import UPDATER

                if STATE.progress.get("running"):
                    raise ValueError("Wait for the analysis to finish before restarting.")
                if UPDATER.status.get("state") != "ready":
                    raise ValueError("No update is ready yet.")
                if STATE.window is None:
                    raise ValueError("Restart is only available in the app window.")
                STATE.restart_requested = True
                threading.Timer(0.3, STATE.window.destroy).start()
                return self._json({"ok": True})
            if ep == "open_url":
                open_url(b.get("url", ""))
                return self._json({"ok": True})
            return self._send(404, b"unknown endpoint", "text/plain")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": str(e)}, 400)

    def _static(self, rel: str):
        p = (WEB_DIR / rel).resolve()
        if WEB_DIR.resolve() not in p.parents and p != WEB_DIR.resolve() or not p.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        if p.suffix == ".js":
            ctype = "text/javascript"
        return self._send(200, p.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))


def serve(port: int = 0) -> tuple[ThreadingHTTPServer, int]:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]
