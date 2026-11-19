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
    STATE.shell_pid = args.parent_pid or os.getppid()
    register_running()
    if args.folder:
        try:
            open_folder(args.folder)
        except Exception as e:  # noqa: BLE001
            print("could not open", args.folder, e)
    UPDATER.start(delay=60)
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
    if args.serve:
        return serve_only(args)

    _setup_logging()
    from .server import STATE, open_folder, serve
    from .updater import UPDATER, register_running

    srv, port = serve(args.port)
    STATE.server = srv
    STATE.shell_pid = os.getpid()
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
            "PIP-FUCCI", url, width=1440, height=940, min_size=(1024, 700),
            background_color="#0E1012", text_select=True, js_api=ShellApi(),
        )
        STATE.window = window
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

        print("PIP-FUCCI running at", url, flush=True)
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    return 0
