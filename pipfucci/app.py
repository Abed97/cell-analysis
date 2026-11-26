"""App entry point: starts the local server and opens the app window (no browser)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from . import DISPLAY_NAME


def _setup_logging():
    from .server import app_data_dir

    try:
        log = open(app_data_dir() / "pip-fucci.log", "a", buffering=1)
        if getattr(sys, "frozen", False) or sys.stdout is None:
            sys.stdout = log
            sys.stderr = log
        print(f"\n--- start {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
    except Exception:
        pass


def selftest() -> int:
    """Headless end-to-end check used by the build: demo data -> analysis (2 worker processes) -> export -> server."""
    import json
    import urllib.request

    from . import analysis, render
    from .demo import make_demo
    from .experiment import Experiment
    from .export import export_all
    from .server import serve

    with tempfile.TemporaryDirectory() as d:
        # LZW-compressed like Incucyte exports: fails here if the TIFF codecs are not bundled
        make_demo(d, wells={"B2": "slow", "C2": "fast"}, n_frames=10, shape=(200, 260), compression="lzw")
        import tifffile

        probe = next(Path(d).rglob("*.tif"))
        assert tifffile.TiffFile(probe).pages[0].compression == 5, "demo images are not LZW"
        tifffile.imread(probe)  # decoded by imagecodecs, not the Pillow fallback
        exp = Experiment(d)
        exp.run(workers=2)
        assert exp.has_results and len(exp.tracks) > 0, "no tracks"
        pop = analysis.population(exp)
        assert pop["conditions"], "no population output"
        render.encode(render.composite(exp, "B2", 1, 3, {"phase", "red", "green", "masks", "bgsub", "confluence"}))
        from . import compare

        fig = compare.figures([exp])
        assert fig["conditions"] and fig["conditions"][0]["n"] > 0, "no figure data"
        key = exp.tracks.index[0]
        assert analysis.lineage(exp, key)["nodes"], "no lineage"
        assert analysis.frame_cells(exp, "B2", 1, 3)["cells"], "no cells in frame"
        out = export_all(exp)
        assert (out / "Tracks.csv").exists() and (out / "Figure data").is_dir()
        from . import annotate  # annotated images: fonts with µ, TIFF writer, vector contours

        lay = {"phase", "red", "green", "masks", "confluence"}
        ra = annotate.export(exp, "B2", 1, [3, 6], lay, fmt="tif", scale=2, numbers=True, strip=True)
        rs = annotate.export(exp, "B2", 1, [3], lay, fmt="svg")
        assert len(ra["paths"]) == 3 and "nucleus-outlines" in Path(rs["paths"][0]).read_text()
        print("annotated image font:", getattr(annotate._font(20), "path", "built-in"), flush=True)
        rm = annotate.track_montage(exp, key, n=6, fmt="tif")  # one cell over time, for the track card
        assert Path(rm["path"]).stat().st_size > 1000 and rm["columns"] >= 1
        assert len(analysis.no_reporter(exp)) == len(exp.cells)
        import numpy as np

        from . import stats  # box-plot statistics: scipy's tests in the bundle (Tukey, Dunnett, ...)

        sd = stats.run([exp], "durations")
        assert len(sd["families"]) == 3 and stats.mean_tests([np.arange(4.0), np.arange(4.0) + 1, np.arange(4.0) + 2], 0)["pairs"]
        print("statistics:", "; ".join(f"{f['title']}: {f.get('test', f.get('why'))}" for f in sd["families"]), flush=True)
        srv, port = serve(0)
        st = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=10).read())
        assert st["app"] == "PIP-FUCCI" and st["display_name"] == DISPLAY_NAME
        html = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10).read()
        assert DISPLAY_NAME.encode() in html
        from . import macos

        print("macos:", macos.selftest(), flush=True)
        js = urllib.request.urlopen(f"http://127.0.0.1:{port}/static/app.js", timeout=10).read()
        assert len(js) > 1000
        srv.shutdown()
    print("SELFTEST OK", flush=True)
    return 0


def watch_jobs(window, port: int) -> None:
    """In the window's process: Dock badge with the progress, keep the Mac from napping while an
    analysis runs, and a notification + Dock bounce when it ends. Follows the window to a newer
    backend after "Update now"."""
    from urllib.parse import urlsplit

    from . import macos
    from .updater import load_prefs

    last = None
    badge = None
    done_badge_at = None
    while True:
        time.sleep(1.5)
        try:
            u = urlsplit(window.get_current_url() or "")
            base = f"{u.scheme}://{u.netloc}" if u.scheme.startswith("http") else f"http://127.0.0.1:{port}"
        except Exception:
            base = f"http://127.0.0.1:{port}"
        try:
            p = json.loads(urllib.request.urlopen(base + "/api/progress", timeout=5).read())
        except Exception:
            continue
        long_job = p.get("job") in ("analysis", "retrack", "measure", "remask", "remote")
        if p.get("running") and long_job:
            if p.get("job") == "analysis":
                macos.request_notifications()
            macos.begin_activity("Analysing images", "watch")
            label = f"{int(round(100 * (p.get('fraction') or 0)))}%"
            if label != badge:
                macos.set_badge(label)
                badge = label
            done_badge_at = None
        elif last is not None and last.get("running") and last.get("started") == p.get("started"):
            macos.end_activity("watch")
            name = p.get("experiment") or "Your experiment"
            job = last.get("job")
            if p.get("cancelled") or job == "measure":
                macos.set_badge(None)
                badge = None
            else:
                failed = bool(p.get("error"))
                badge = "!" if failed else "✓"
                if macos.is_active():
                    macos.set_badge(None)
                    badge = None
                else:
                    macos.set_badge(badge)
                    macos.bounce()
                if load_prefs().get("notify", True):
                    if failed:
                        macos.notify("Analysis stopped", f"{name}: {p.get('error')}")
                    else:
                        what = {"retrack": "Tracks updated", "remask": "Confluence redrawn"}.get(job, "Analysis finished")
                        mins = (p.get("finished", 0) - p.get("started", 0)) / 60 if p.get("finished") else None
                        took = f" in {mins:.0f} min" if mins and mins >= 1 else ""
                        macos.notify(what, f"{name} is ready{took}. Open {DISPLAY_NAME} to see the results.")
        elif badge in ("✓", "!") and macos.is_active():  # she has seen it
            done_badge_at = done_badge_at or time.time()
            if time.time() - done_badge_at > 2:
                macos.set_badge(None)
                badge = None
        last = p


class ShellApi:
    """Called from the page as window.pywebview.api.*; lives in the window's process, so it keeps
    working after the page has moved to a newer version's backend."""

    def pick_folder(self):
        from .server import pick_folder

        return pick_folder()


def serve_only(args) -> int:
    """Backend without a window: started by an older copy's "Update now", which then points its
    window here. Exits when the window's process goes away or a newer backend takes over."""
    _setup_logging()
    from .server import STATE, open_folder, serve
    from .updater import UPDATER, _pid_alive, register_running

    srv, port = serve(0)
    STATE.server = srv
    STATE.is_child = True
    from .server import start_remote_worker

    start_remote_worker()
    STATE.shell_pid = args.parent_pid or os.getppid()
    register_running()
    if args.folder:
        try:
            open_folder(args.folder)
        except Exception as e:  # noqa: BLE001
            print("could not open", args.folder, e)
    UPDATER.start(delay=30)
    if args.ready_file:
        tmp = args.ready_file + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(str(port))
        os.replace(tmp, args.ready_file)
    print(f"backend {os.getpid()} serving on {port} for window {STATE.shell_pid}", flush=True)
    while _pid_alive(STATE.shell_pid) and not STATE.retired:
        time.sleep(2)
    os._exit(0)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pip-fucci")
    p.add_argument("--browser", action="store_true", help="open in the default browser instead of an app window")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--folder", help="open this experiment folder at start")
    p.add_argument("--whoami", action="store_true", help="print the running executable and exit (update test)")
    p.add_argument("--check-ssl", metavar="URL", nargs="?", const="https://api.github.com/",
                   help="fetch a https address with the app's certificate handling and exit")
    p.add_argument("--refresh-icon", nargs="?", const="auto", choices=["auto", "force"],
                   help="give the installed copy the current icon now and exit (build check)")
    p.add_argument("--serve", action="store_true", help="backend only, for in-place updates")
    p.add_argument("--parent-pid", type=int, help="with --serve: exit when this process ends")
    p.add_argument("--ready-file", help="with --serve: write the port here once ready")
    # macOS passes -psn_* when launched from Finder on old systems
    args, _ = p.parse_known_args(argv)

    if args.selftest:
        return selftest()
    if args.whoami:
        print(sys.executable, flush=True)
        return 0
    if args.refresh_icon:
        from .macos import refresh_installed_icon

        print("icon:", refresh_installed_icon(now=True, force=args.refresh_icon == "force"), flush=True)
        return 0
    if args.check_ssl:
        from .net import check

        print("ssl ok via", check(args.check_ssl), flush=True)
        return 0
    if args.serve:
        return serve_only(args)

    _setup_logging()
    from .server import STATE, open_folder, serve
    from .updater import UPDATER, register_running

    srv, port = serve(args.port)
    STATE.server = srv
    STATE.shell_pid = os.getpid()
    from .server import start_remote_worker

    start_remote_worker()
    register_running()
    UPDATER.start()
    url = f"http://127.0.0.1:{port}/"
    if args.folder:
        try:
            open_folder(args.folder)
        except Exception as e:  # noqa: BLE001
            print("could not open", args.folder, e)

    use_window = not args.browser
    if use_window:
        try:
            import webview  # noqa: F401
        except Exception as e:  # noqa: BLE001
            print("pywebview not available, using the browser:", e)
            use_window = False

    if use_window:
        import webview

        window = webview.create_window(
            DISPLAY_NAME, url, width=1440, height=940, min_size=(1024, 700),
            background_color="#0E1012", text_select=True, js_api=ShellApi(),
        )
        STATE.window = window
        import threading

        threading.Thread(target=watch_jobs, args=(window, port), daemon=True, name="watch-jobs").start()
        from . import macos

        macos.set_app_icon()
        try:
            macos.refresh_installed_icon()
        except Exception:
            pass
        webview.start()
        if STATE.restart_requested:
            from .updater import restart_into_newest

            try:
                restart_into_newest()
            except Exception as e:  # noqa: BLE001
                print("restart failed:", e, flush=True)
        os._exit(0)  # stop worker processes, servers and any newer backend with the window
    else:
        import webbrowser

        print(DISPLAY_NAME, "running at", url, flush=True)
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    return 0
