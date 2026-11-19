"""App entry point: starts the local server and opens the app window (no browser)."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time


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
        make_demo(d, wells={"B2": "slow", "C2": "fast"}, n_frames=10, shape=(200, 260))
        exp = Experiment(d)
        exp.run(workers=2)
        assert exp.has_results and len(exp.tracks) > 0, "no tracks"
        pop = analysis.population(exp)
        assert pop["conditions"], "no population output"
        render.encode(render.composite(exp, "B2", 1, 3, {"phase", "red", "green", "masks"}))
        out = export_all(exp)
        assert (out / "Tracks.csv").exists()
        srv, port = serve(0)
        st = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=10).read())
        assert st["app"] == "PIP-FUCCI"
        html = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10).read()
        assert b"PIP-FUCCI" in html
        js = urllib.request.urlopen(f"http://127.0.0.1:{port}/static/app.js", timeout=10).read()
        assert len(js) > 1000
        srv.shutdown()
    print("SELFTEST OK", flush=True)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pip-fucci")
    p.add_argument("--browser", action="store_true", help="open in the default browser instead of an app window")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--folder", help="open this experiment folder at start")
    p.add_argument("--whoami", action="store_true", help="print the running executable and exit (update test)")
    # macOS passes -psn_* when launched from Finder on old systems
    args, _ = p.parse_known_args(argv)

    if args.selftest:
        return selftest()
    if args.whoami:
        print(sys.executable, flush=True)
        return 0

    _setup_logging()
    from .server import STATE, open_folder, serve

    from .updater import UPDATER

    srv, port = serve(args.port)
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
            "PIP-FUCCI", url, width=1440, height=940, min_size=(1024, 700),
            background_color="#0E1012", text_select=True,
        )
        STATE.window = window
        webview.start()
        if STATE.restart_requested:
            from .updater import restart_into_newest

            try:
                restart_into_newest()
            except Exception as e:  # noqa: BLE001
                print("restart failed:", e, flush=True)
        os._exit(0)  # stop worker processes and the server with the window
    else:
        import webbrowser

        print("PIP-FUCCI running at", url, flush=True)
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    return 0
