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

from . import APP_NAME, DISPLAY_NAME, GITHUB_REPO, __version__
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
        self.server = None
        self.is_child = False  # True when started by "Update now" (backend without a window)
        self.shell_pid = os.getpid()
        self.retired = False
        self.handing_over = False  # "Update now" is starting the newer copy
        self.remote_worker = None

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
    def start_job(self, name: str, fn, label: str | None = None, where: str | None = None) -> None:
        with self.lock:
            if self.progress.get("running"):
                raise RuntimeError("Something is already running. Wait for it to finish or cancel it.")
            self.cancel.clear()
            exp = self.exp
            self.progress = {"running": True, "job": name, "stage": "Starting", "fraction": 0.0, "eta_s": None,
                             "started": time.time(), "where": where, "remote": bool(where),
                             "experiment": label or ((exp.settings.experiment_name or exp.folder.name) if exp else None)}

        def report(**kw):
            self.progress.update(kw)

        def target():
            from .macos import activity

            try:
                with activity("Analysing images"):  # full speed while the window is hidden
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


def _is_worker() -> bool:
    """This Mac analyses what the other Macs send (Settings) and the shared folder is here."""
    from . import remote

    try:
        pr = remote.prefs()
        return bool(pr["worker"] and remote.shared_dir(pr["shared"]) is not None)
    except Exception:  # noqa: BLE001
        return False


def _plan_info(exp) -> dict:
    from . import remote

    try:
        return remote.plan_info(exp)
    except Exception:  # noqa: BLE001
        return {"use": False}


def experiment_state(exp) -> dict:
    if exp is None:
        return None
    ok, msg = exp.channels_ok()
    s = exp.settings
    return {
        "summary": exp.summary(),
        "remote": _plan_info(exp),
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
        "old_format": bool(exp.has_results and "interp" not in exp.cells.columns),
        "mask_stale": exp.mask_settings_changed(),
        "excluded_wells": sorted(exp.excluded_wells()),
        "linked": linked_info(exp),
        "saved_at": _saved_at(exp),
    }


def _saved_at(exp):
    try:
        return time.strftime("%H:%M", time.localtime((exp.results_dir / "experiment.json").stat().st_mtime))
    except OSError:
        return None


# ------------------------------------------------------------------ other experiments to compare with
_LINKED: dict = {}


def _stamp(folder: Path) -> tuple:
    rd = folder / "PIP-FUCCI results"
    out = []
    for n in ("experiment.json", "cells.pkl", "tracks.pkl", "track_flags.json"):
        try:
            out.append((rd / n).stat().st_mtime)
        except OSError:
            out.append(0)
    return tuple(out)


def load_linked(path: str):
    """An analysed experiment, read-only, cached until its files change."""
    from .experiment import Experiment

    p = Path(path)
    if not (p / "PIP-FUCCI results" / "cells.pkl").exists():
        return None
    st = _stamp(p)
    hit = _LINKED.get(str(p))
    if hit and hit[0] == st:
        return hit[1]
    try:
        e = Experiment(p)
    except Exception:
        return None
    if not e.has_results:
        return None
    _LINKED[str(p)] = (st, e)
    return e


def linked_info(exp) -> list:
    out = []
    for p in exp.settings.linked:
        e = load_linked(p)
        if e is None:
            out.append({"folder": p, "name": Path(p).name, "ok": False,
                        "msg": "Folder not analysed yet" if Path(p).is_dir() else "Folder not found (moved or renamed?)"})
            continue
        conds = [c["name"] for c in analysis._conditions(e)]
        out.append({"folder": p, "name": e.settings.experiment_name or e.folder.name, "ok": True,
                    "conditions": conds, "n_wells": len(e.index["wells"]), "interval_min": e.settings.interval_min})
    return out


def all_exps(exp) -> list:
    return [exp] + [e for e in (load_linked(p) for p in exp.settings.linked) if e is not None]


def link_experiment(path: str) -> dict:
    exp = need_exp()
    p = Path(path).expanduser().resolve()
    if p == exp.folder:
        raise ValueError("That is the experiment that is already open.")
    if not p.is_dir():
        raise ValueError(f"Folder not found: {p}")
    if load_linked(str(p)) is None:
        raise ValueError("That folder has not been analysed yet. Open it, run the analysis, then add it here.")
    if str(p) not in exp.settings.linked:
        exp.update_settings({"linked": exp.settings.linked + [str(p)]})
    return state()


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
        "app": APP_NAME, "display_name": DISPLAY_NAME, "version": __version__, "repo": GITHUB_REPO, "platform": sys.platform,
        "native_dialogs": STATE.window is not None or sys.platform == "darwin",
        "remote_worker": _is_worker(),
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
    if not (exp.results_dir / "experiment.json").exists():
        exp.save()
    STATE.add_recent(exp)
    try:
        resume_remote(exp)
    except Exception:  # noqa: BLE001 - optional
        traceback.print_exc()
    # analysed before colonies existed: measure them from the saved masks (seconds, no re-run)
    if (exp.has_results and exp.stale is None and exp.meta.get("colony_sizes") != 1
            and (exp.results_dir / "masks").is_dir() and not STATE.progress.get("running")):
        try:
            run_remeasure()
        except Exception:  # noqa: BLE001 - optional
            pass
    return state()


def _core_info() -> dict:
    """Cores on this Mac, its memory and what Auto would use (for the open experiment's image size)."""
    from .experiment import WORKER_GB, worker_count

    try:
        per = STATE.exp.worker_gb() if STATE.exp is not None else WORKER_GB
    except Exception:
        per = WORKER_GB
    try:
        mem = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30)
    except Exception:
        mem = None
    return {"cpus": os.cpu_count() or 1, "auto_workers": worker_count(per_worker_gb=per), "mem_gb": mem}


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
        from .net import urlopen

        with urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode())
        latest = str(data.get("tag_name", "")).lstrip("v")

        def vt(v):
            return tuple(int(x) for x in v.split(".") if x.isdigit())

        avail = bool(latest) and vt(latest) > vt(__version__)
        STATE.update_info = {"available": avail, "latest": latest, "url": data.get("html_url")}
    except Exception:
        STATE.update_info = {"available": False}
    return STATE.update_info


def update_now() -> dict:
    """Start the downloaded version as a backend, hand the window over to it, then retire."""
    from .updater import (EXE_REL, UPDATER, note_attempt, note_failed, spawn_backend, versions_dir)

    if STATE.progress.get("running"):
        raise ValueError("Wait for the analysis to finish, then update.")
    st = UPDATER.status
    ver = st.get("ready_version")
    if st.get("state") != "ready" or not ver:
        raise ValueError("No update is ready yet.")
    exe = versions_dir() / f"PIP-FUCCI-{ver}.app" / EXE_REL
    if not exe.is_file():
        raise ValueError("The downloaded update is missing. It will download again shortly.")
    ready = versions_dir() / f".ready-{os.getpid()}-{int(time.time() * 1000)}"
    cmd = [str(exe), "--serve", "--parent-pid", str(STATE.shell_pid), "--ready-file", str(ready)]
    if STATE.exp is not None:
        cmd += ["--folder", str(STATE.exp.folder)]
    note_attempt(ver)
    STATE.handing_over = True  # no new work for the other Macs from this copy
    try:
        port = spawn_backend(cmd, ready)
    except Exception:
        STATE.handing_over = False
        note_failed(ver)
        raise
    UPDATER.stop()

    def retire():
        STATE.retired = True
        if STATE.is_child:
            os._exit(0)
        if STATE.server is not None:
            STATE.server.shutdown()

    threading.Timer(15.0, retire).start()
    return {"url": f"http://127.0.0.1:{port}/", "version": ver}


def save_figure(b: dict) -> dict:
    import base64
    import re

    exp = need_results()
    name = re.sub(r"[^A-Za-z0-9 _().+-]+", "_", str(b.get("name") or "figure")).strip() or "figure"
    out = exp.results_dir / "figures"
    stamp = " " + time.strftime("%Y-%m-%d %H%M%S")
    if b.get("folder"):  # "Export all": one dated folder, plain file names
        sub = re.sub(r"[^A-Za-z0-9 _().+-]+", "_", str(b["folder"])).strip() or "All graphs"
        out = out / sub
        stamp = ""
    out.mkdir(parents=True, exist_ok=True)
    written = []
    if b.get("svg"):
        p = out / f"{name}{stamp}.svg"
        p.write_text(b["svg"], encoding="utf-8")
        written.append(p)
    if b.get("png"):
        data = b["png"].split(",", 1)[-1]
        p = out / f"{name}{stamp}.png"
        p.write_bytes(base64.b64decode(data))
        written.append(p)
    if not written:
        raise ValueError("Nothing to save.")
    return {"path": str(written[-1]), "folder": str(out)}


def need_exp():
    if STATE.exp is None:
        raise ValueError("Open an experiment folder first.")
    return STATE.exp


def export_image(b: dict) -> dict:
    """Annotated images of the viewer's field for a paper or a talk (see annotate.py)."""
    from . import annotate

    exp = need_results()
    well, field = str(b["well"]), int(b["field"])
    if f"{well}_{field}" not in exp.meta.get("movies", {}):
        raise ValueError("No such field.")
    hours = exp.frame_hours()
    if b.get("hours"):
        frames = []
        for h in b["hours"]:
            f = int(np.argmin(np.abs(np.asarray(hours) - float(h))))
            if f not in frames:
                frames.append(f)
    else:
        frames = [int(f) for f in b.get("frames") or [] if 0 <= int(f) < len(hours)]
    allowed = {"phase", "red", "green", "bgsub", "masks", "confluence"}
    layers = {x for x in b.get("layers") or [] if x in allowed}
    crop = float(b["crop_um"]) if b.get("crop_um") and b.get("sel") else None
    if crop is not None and not 5 <= crop <= 5000:
        raise ValueError("The square should be between 5 and 5000 µm.")
    return annotate.export(exp, well, field, frames, layers, fmt=str(b.get("fmt", "png")), scale=int(b.get("scale", 2)),
                           numbers=bool(b.get("numbers")), select_track=b.get("sel") or None, crop_um=crop,
                           strip=bool(b.get("strip")), label=str(b.get("label", "field")), time=bool(b.get("time", True)),
                           bar=bool(b.get("bar", True)), legend=bool(b.get("legend")), text=str(b.get("text", "medium")))


def hand_remove(b: dict) -> dict:
    """Take one object out of the counts by hand: a nucleus in this image ("object"), its whole
    track ("track"), or a confluence area ("patch", in this image or with every=True in all)."""
    exp = need_results()
    what = b.get("what")
    if what not in ("object", "track", "patch"):
        raise ValueError("Unknown kind of object.")
    well, field, frame = str(b["well"]), int(b["field"]), int(b["frame"])
    x, y = float(b["x"]), float(b["y"])
    movie = f"{well}_{field}"
    if movie not in exp.meta.get("movies", {}):
        raise ValueError("No such field.")
    if what == "patch":
        if STATE.progress.get("running"):
            raise ValueError("Wait until the current analysis has finished.")
        from .pipeline import MovieMasks

        m = MovieMasks(exp.results_dir / "masks" / movie).cells(frame)
        if m is None:
            raise ValueError("No confluence mask for this image.")
        xi, yi = int(x / 2), int(y / 2)
        win = m[max(0, yi - 4):yi + 5, max(0, xi - 4):xi + 5]
        if not win.any():
            raise ValueError("There is no confluence area here. Click inside a blue area.")
        entry = {"movie": movie, "frame": -1 if b.get("every") else frame, "x": x, "y": y, "what": "patch"}
    else:
        lbl = exp.nearest_cell(well, field, frame, x, y)
        if lbl is None:
            raise ValueError("There is no nucleus here.")
        c = exp.cells.loc[lbl]
        entry = {"movie": movie, "frame": frame, "x": float(c["x"]), "y": float(c["y"]), "what": what}
    with exp.lock:
        exp.settings.update({"removed": exp.settings.removed + [entry]})
        exp.save()
    if what == "patch":
        exp.measure_frames(movie, None if entry["frame"] < 0 else [frame])
    return {"ok": True, "removed": len(exp.settings.removed)}


def hand_unremove(b: dict) -> dict:
    """Count objects again: one removal (index), or every removal in a field (movie)."""
    exp = need_results()
    rem = list(exp.settings.removed)
    if "index" in b:
        i = int(b["index"])
        if not 0 <= i < len(rem):
            raise ValueError("Already put back.")
        gone = [rem.pop(i)]
    else:
        movie = str(b.get("movie", ""))
        gone = [r for r in rem if r["movie"] == movie]
        rem = [r for r in rem if r["movie"] != movie]
    with exp.lock:
        exp.settings.update({"removed": rem})
        exp.save()
    for movie in {r["movie"] for r in gone if r["what"] == "patch"}:
        frames = {r["frame"] for r in gone if r["what"] == "patch" and r["movie"] == movie}
        exp.measure_frames(movie, None if -1 in frames else sorted(frames))
    return {"ok": True, "removed": len(rem)}


def need_results():
    exp = need_exp()
    if not exp.has_results:
        raise ValueError("Run the analysis first.")
    return exp


def _remote_or_here(name: str, exp, local) -> dict:
    """Run a heavy job here, or on the other Mac through the shared folder (Settings)."""
    from . import remote

    try:
        p = remote.plan(exp)
    except Exception:  # noqa: BLE001 - a broken shared folder never blocks working here
        traceback.print_exc()
        p = None
    if p is None:
        STATE.start_job(name, local)
        return {"ok": True}
    ok, msg = exp.channels_ok()
    if not ok:
        raise ValueError(msg)
    rid = remote.submit(exp, name, p["shared"])
    STATE.start_job(name, lambda report: follow_remote(exp.folder, p["shared"], rid, report), where=p["target"])
    return {"ok": True, "remote": p["target"]}


def follow_remote(folder, shared, rid: str, report) -> None:
    """Wait for the other Mac, then load the results it made (they arrive through the sync)."""
    from . import remote
    from .experiment import Experiment

    remote.wait(folder, shared, rid, report, STATE.cancel)
    report(stage="Loading the results", fraction=1.0)
    fresh = Experiment(folder)
    if STATE.exp is not None and Path(STATE.exp.folder) == Path(fresh.folder):
        STATE.exp = fresh
    remote.mark_loaded(shared, rid)


def resume_remote(exp) -> None:
    """Opening an experiment that the other Mac is (or was) analysing: keep following it."""
    from . import remote

    if STATE.progress.get("running"):
        return
    shared = remote.shared_dir()
    r = remote.pending(shared, remote.rel_of(exp.folder, shared))
    if r is None:
        return
    st = remote.read_json(remote.queue_dir(shared) / f"{r['id']}.status.json") or {}
    STATE.start_job(r.get("job", "analysis"), lambda report: follow_remote(exp.folder, shared, r["id"], report),
                    where=st.get("host") or "the other Mac")


def start_remote_worker() -> None:
    """On every Mac: listens for analyses sent from her other Macs, when Settings say so."""
    from . import remote
    from .experiment import Experiment

    if STATE.remote_worker is not None:
        return

    def busy():
        return bool(STATE.progress.get("running")) or STATE.retired or STATE.handing_over

    def done(folder):
        if STATE.exp is not None and Path(STATE.exp.folder) == Path(folder).resolve():
            STATE.exp = Experiment(folder)

    STATE.remote_worker = remote.WorkerLoop(busy, lambda n, fn, label: STATE.start_job(n, fn, label=label),
                                            STATE.cancel, done, alive=lambda: not STATE.retired)
    STATE.remote_worker.start()


def remote_state() -> dict:
    from . import remote

    pr = remote.prefs()
    shared = remote.shared_dir(pr["shared"])
    w = STATE.remote_worker
    return {"me": remote.computer_name(), "shared": pr["shared"], "shared_ok": shared is not None,
            "shared_name": shared.name if shared else None, "run_on": pr["run_on"], "worker": pr["worker"],
            "workers": remote.workers(shared), "plan": remote.plan_info(STATE.exp),
            "working_on": getattr(w, "current", None)}


def remote_prefs(b: dict) -> dict:
    from . import remote
    from .updater import save_prefs

    patch = {}
    if "shared_folder" in b:
        v = str(b["shared_folder"] or "")
        if v and not Path(v).expanduser().is_dir():
            raise ValueError(f"Folder not found: {v}")
        patch["shared_folder"] = v
    if "run_on" in b:
        patch["run_on"] = "remote" if b["run_on"] == "remote" else "here"
    if "worker" in b:
        patch["worker"] = bool(b["worker"])
    save_prefs(patch)
    pr = remote.prefs()
    shared = remote.shared_dir(pr["shared"])
    if pr["worker"] and shared is not None:
        try:
            remote.heartbeat(shared, None, os.cpu_count())
        except OSError:
            pass
    return remote_state()


def add_login_item() -> dict:
    """Open Mitosight when this Mac starts (Login Items), for a Mac that works for the others."""
    import os as _os

    stub = _os.environ.get("PIPFUCCI_STUB")
    exe = Path(stub) if stub else Path(sys.executable)
    app = next((p for p in [exe, *exe.parents] if p.suffix == ".app"), None)
    if sys.platform != "darwin" or app is None or not getattr(sys, "frozen", False):
        raise ValueError("Only the Mac app can open itself at login.")
    script = ('tell application "System Events" to if not (exists login item "%s") then '
              'make login item at end with properties {path:"%s", hidden:false}' % (app.stem, str(app)))
    out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise ValueError("macOS did not allow it. Add Mitosight in System Settings › General › Login Items "
                         "(the + button), or allow Mitosight to control System Events when asked.")
    return {"ok": True, "app": str(app)}


def box_stats(q: dict) -> dict:
    """Statistics between conditions for the duration and colony box plots (see stats.py)."""
    from . import compare, stats

    exps = all_exps(need_results())
    what = q.get("what") if q.get("what") in ("colonies", "arrest") else "durations"
    times = None
    if what == "colonies":
        ev = float(q["every"]) if q.get("every") else None
        times = compare.colony_dist(exps, ev)["times"]
        if q.get("times"):
            want = [float(x) for x in str(q["times"]).split(",") if x.strip()]
            times = [t for t in times if any(abs(t - w) < 1e-6 for w in want)] or times
    cutoff = float(q["cutoff"]) if q.get("cutoff") else exps[0].settings.g1_arrest_h
    res = stats.run(exps, what, q.get("unit") or "auto", q.get("control") or None, times, cutoff)
    if q.get("save"):
        import re

        name = {"durations": "Statistics - phase durations", "colonies": "Statistics - colonies",
                "arrest": "Statistics - G1 arrest"}[what]
        folder = need_results().results_dir / "figures"
        if q.get("folder"):  # "Export all": the same dated folder as the figures
            folder = folder / (re.sub(r"[^A-Za-z0-9 _().+-]+", "_", str(q["folder"])).strip() or "All graphs")
        else:
            name += " " + time.strftime("%Y-%m-%d %H%M%S")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}.csv"
        path.write_text(stats.to_csv(res), encoding="utf-8")
        res["saved"] = str(path)
    return res


def run_analysis():
    exp = need_exp()

    from .updater import load_prefs

    workers = load_prefs().get("workers") or None

    def job(report):
        exp.run(progress=report, cancel=STATE.cancel, workers=workers)

    return _remote_or_here("analysis", exp, job)


def run_remask():
    exp = need_results()
    from .updater import load_prefs

    workers = load_prefs().get("workers") or None
    return _remote_or_here("remask", exp, lambda report: exp.remask(progress=report, cancel=STATE.cancel, workers=workers))


def run_remeasure():
    exp = need_results()
    from .updater import load_prefs

    workers = load_prefs().get("workers") or None
    return _remote_or_here("measure", exp, lambda report: exp.remeasure(progress=report, cancel=STATE.cancel, workers=workers))


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
        for k, v in {"Cache-Control": "no-store", **(extra or {})}.items():
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
            if ep == "remote":
                return self._json(remote_state())
            if ep == "progress":
                return self._json(STATE.progress)
            if ep == "frame":
                exp = need_exp()
                layers = set((q.get("layers") or "phase,red,green,masks").split(","))
                arr = render.composite(exp, q["well"], int(q["field"]), int(q["frame"]), layers, q.get("sel") or None)
                # cacheable: the page puts a version in the address that changes with gates and results
                return self._send(200, render.encode(arr, "JPEG", 88), "image/jpeg",
                                  {"Cache-Control": "private, max-age=900"})
            if ep == "montage":
                exp = need_results()
                arr, _ = render.montage(exp, q["key"])
                return self._send(200, render.encode(arr, "PNG"), "image/png")
            if ep == "scatter":
                exp = need_results()
                fld = int(q["field"]) if q.get("field") else None
                return self._json(analysis.scatter(exp, int(q["frame"]), q.get("well") or None, fld, q.get("condition") or None,
                                                   max_points=int(q.get("max") or 3000)))
            if ep == "stats":
                exp = need_results()
                return self._json(analysis.detection_stats(exp, q["well"], int(q["field"]), int(q["frame"])))
            if ep == "population":
                from . import compare

                return self._json(compare.population(all_exps(need_results())))
            if ep == "colony_dist":
                from . import compare

                ev = float(q["every"]) if q.get("every") else None
                return self._json(compare.colony_dist(all_exps(need_results()), ev))
            if ep == "box_stats":
                return self._json(box_stats(q))
            if ep == "figures":
                from . import compare

                return self._json(compare.figures(all_exps(need_results())))
            if ep == "frame_cells":
                exp = need_results()
                return self._json(analysis.frame_cells(exp, q["well"], int(q["field"]), int(q["frame"])))
            if ep == "lineage":
                return self._json(analysis.lineage(need_results(), q["key"]))
            if ep == "prefs":
                from .updater import load_prefs

                return self._json({**load_prefs(), **_core_info()})
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
                if r.get("action") == "remeasure":
                    try:
                        run_remeasure()
                    except RuntimeError:  # something else is running; it is measured on the next run
                        r["action"] = None
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
            if ep == "remote_prefs":
                return self._json(remote_prefs(b))
            if ep == "pick_shared":
                pth = pick_folder()
                return self._json(remote_prefs({"shared_folder": pth}) if pth else remote_state())
            if ep == "login_item":
                return self._json(add_login_item())
            if ep == "export_image":
                return self._json(export_image(b))
            if ep == "remove":
                return self._json(hand_remove(b))
            if ep == "unremove":
                return self._json(hand_unremove(b))
            if ep == "track_flag":
                exp = need_results()
                kw = {k: b[k] for k in ("excluded", "reviewed") if k in b}
                return self._json(exp.track_flag(b["key"], **kw))
            if ep == "export":
                from .export import export_all

                exp = need_results()
                out = export_all(exp, all_exps(exp)[1:])
                return self._json({"path": str(out)})
            if ep == "reveal":
                reveal(b.get("path") or str(need_exp().results_dir))
                return self._json({"ok": True})
            if ep == "update_now":
                return self._json(update_now())
            if ep == "prefs":
                from .updater import save_prefs

                return self._json({**save_prefs(b), **_core_info()})
            if ep == "check_updates":
                from .updater import UPDATER

                if not UPDATER.status["enabled"]:
                    STATE.update_info = None
                    return self._json({**check_update(), "enabled": False})
                UPDATER.check_now()
                return self._json({"ok": True})
            if ep == "link":
                return self._json(link_experiment(b.get("path", "")))
            if ep == "unlink":
                exp = need_exp()
                exp.update_settings({"linked": [p for p in exp.settings.linked if p != b.get("path")]})
                return self._json(state())
            if ep == "remask":
                return self._json(run_remask())
            if ep == "save_figure":
                return self._json(save_figure(b))
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
