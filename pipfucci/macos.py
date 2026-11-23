"""macOS extras, all optional: keep working at full speed while the window is hidden (App Nap),
a progress badge on the Dock icon, a bounce and a notification when an analysis ends.

Every function quietly does nothing on other systems, or when a framework is missing, so the
analysis never depends on any of this.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import uuid

MAC = sys.platform == "darwin"
_tokens: dict = {}
_lock = threading.Lock()
_notify_state = {"asked": False, "allowed": None}


# ------------------------------------------------------------------ App Nap / sleep
def begin_activity(reason: str, key: str = "default") -> bool:
    """Tell macOS that user-requested work is running: no App Nap throttling and no idle sleep
    (the display may still sleep). Lasts until end_activity(key) or the process ends."""
    if not MAC:
        return False
    with _lock:
        if key in _tokens:
            return True
        try:
            import Foundation

            opts = getattr(Foundation, "NSActivityUserInitiated", 0x00FFFFFF | (1 << 20))
            tok = Foundation.NSProcessInfo.processInfo().beginActivityWithOptions_reason_(opts, reason)
            _tokens[key] = tok
            return True
        except Exception:
            return False


def end_activity(key: str = "default") -> None:
    with _lock:
        tok = _tokens.pop(key, None)
    if tok is None:
        return
    try:
        import Foundation

        Foundation.NSProcessInfo.processInfo().endActivity_(tok)
    except Exception:
        pass


class activity:
    """with activity("Analysing images"): ..."""

    def __init__(self, reason: str):
        self.reason = reason
        self.key = "job-" + uuid.uuid4().hex

    def __enter__(self):
        begin_activity(self.reason, self.key)
        return self

    def __exit__(self, *exc):
        end_activity(self.key)
        return False


# ------------------------------------------------------------------ Dock
def _on_main(fn) -> None:
    """AppKit calls belong on the main thread, where the window's event loop runs."""
    if not MAC:
        return
    try:
        from PyObjCTools import AppHelper

        def safe():
            try:
                fn()
            except Exception:
                pass

        AppHelper.callAfter(safe)
    except Exception:
        pass


def set_badge(label: str | None) -> None:
    """Text on the Dock icon ("42%", "✓"); None removes it."""

    def f():
        from AppKit import NSApplication

        NSApplication.sharedApplication().dockTile().setBadgeLabel_(label or None)

    _on_main(f)


def set_app_icon() -> None:
    """Show the current icon in the Dock and app switcher, also when the copy in Applications was
    installed under an older name with an older icon (it hands over to the downloaded version)."""
    from pathlib import Path

    path = Path(__file__).parent / "web" / "icon.png"
    if not path.is_file():
        return

    def f():
        from AppKit import NSApplication, NSImage

        img = NSImage.alloc().initWithContentsOfFile_(str(path))
        if img is not None:
            NSApplication.sharedApplication().setApplicationIconImage_(img)

    _on_main(f)


def _bundle_icon(contents) -> "Path | None":
    """The .icns file a bundle shows (Contents/Info.plist CFBundleIconFile)."""
    import plistlib
    from pathlib import Path

    try:
        info = plistlib.loads((Path(contents) / "Info.plist").read_bytes())
        name = str(info.get("CFBundleIconFile") or "")
        if name and not name.endswith(".icns"):
            name += ".icns"
        p = Path(contents) / "Resources" / name
        return p if name and p.is_file() else None
    except Exception:
        return None


def refresh_installed_icon(now: bool = False, force: bool = False) -> str:
    """Give the copy she opens from Applications (or wherever she keeps it) the current icon, once.

    Copies installed under an older name keep the icon they came with, because updates are
    downloaded next to them and started from there. Finder's own custom-icon mechanism is used
    (NSWorkspace setIcon): it adds an empty Icon file plus extended attributes, which leaves the
    code signature intact, and it is what Finder does when someone pastes an icon in Get Info.
    Tried once per icon: the outcome is remembered so a refusal never repeats."""
    import hashlib
    import json
    import os
    import sys
    from pathlib import Path

    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return "not a Mac app"
    from .updater import support_dir, versions_dir

    own = Path(sys.executable).resolve().parents[1]  # .../X.app/Contents
    icon = _bundle_icon(own)
    if icon is None:
        return "no icon"
    stub = os.environ.get("PIPFUCCI_STUB")
    target = (Path(stub).resolve().parents[2] if stub else own.parent)
    if target.suffix != ".app":
        return "not in an app"
    try:
        if Path(versions_dir()).resolve() in target.parents:
            return "update copy"
    except Exception:
        pass
    digest = hashlib.sha256(icon.read_bytes()).hexdigest()[:16]
    old = _bundle_icon(target / "Contents")
    if not force and old is not None and hashlib.sha256(old.read_bytes()).hexdigest()[:16] == digest:
        return "same icon"
    marker = Path(support_dir()) / "installed_icon.json"
    try:
        done = json.loads(marker.read_text())
    except Exception:
        done = {}
    if not force and done.get("path") == str(target) and done.get("icon") == digest:
        return "already " + str(done.get("result"))
    if not os.access(target, os.W_OK):
        marker.write_text(json.dumps({"path": str(target), "icon": digest, "result": "read-only"}))
        return "read-only"

    def f():
        result = "failed"
        try:
            from AppKit import NSImage, NSWorkspace

            img = NSImage.alloc().initWithContentsOfFile_(str(icon))
            if img is not None and NSWorkspace.sharedWorkspace().setIcon_forFile_options_(img, str(target), 0):
                result = "set"
                NSWorkspace.sharedWorkspace().noteFileSystemChanged_(str(target))
        except Exception:
            pass
        try:
            marker.write_text(json.dumps({"path": str(target), "icon": digest, "result": result}))
        except Exception:
            pass
        return result

    if now:  # command line (build checks): no run loop to wait for
        return f()
    _on_main(f)
    return "requested"


def bounce() -> None:
    """Bounce the Dock icon once (only when the app is in the background)."""

    def f():
        from AppKit import NSApplication

        NSApplication.sharedApplication().requestUserAttention_(10)  # NSInformationalRequest

    _on_main(f)


def is_active() -> bool:
    if not MAC:
        return True
    try:
        from AppKit import NSApplication

        return bool(NSApplication.sharedApplication().isActive())
    except Exception:
        return True


# ------------------------------------------------------------------ notifications
def _center():
    import UserNotifications as UN

    return UN, UN.UNUserNotificationCenter.currentNotificationCenter()


def request_notifications() -> None:
    """Asks once ("Mitosight would like to send notifications"); called when the first analysis
    starts, so the question comes at a moment that makes sense."""
    if not MAC or _notify_state["asked"]:
        return
    _notify_state["asked"] = True
    try:
        UN, center = _center()

        def done(granted, error):
            _notify_state["allowed"] = bool(granted)

        center.requestAuthorizationWithOptions_completionHandler_(
            UN.UNAuthorizationOptionAlert | UN.UNAuthorizationOptionSound, done)
    except Exception:
        _notify_state["allowed"] = False


def _osascript_notify(title: str, body: str) -> None:
    def q(s):
        return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'

    try:
        subprocess.Popen(["/usr/bin/osascript", "-e",
                          f"display notification {q(body)} with title {q(title)} sound name \"Glass\""],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def notify(title: str, body: str) -> None:
    """A macOS notification: the app's own when she allowed them, otherwise a plain system one."""
    if not MAC:
        return
    if _notify_state["allowed"] is False:
        _osascript_notify(title, body)
        return
    try:
        UN, center = _center()

        def post():
            content = UN.UNMutableNotificationContent.alloc().init()
            content.setTitle_(title)
            content.setBody_(body)
            content.setSound_(UN.UNNotificationSound.defaultSound())
            req = UN.UNNotificationRequest.requestWithIdentifier_content_trigger_(uuid.uuid4().hex, content, None)

            def done(error):
                if error is not None:
                    _notify_state["allowed"] = False
                    _osascript_notify(title, body)

            center.addNotificationRequest_withCompletionHandler_(req, done)

        def got(settings):
            try:
                status = int(settings.authorizationStatus())
            except Exception:
                status = -1
            if status == 1:  # she turned Mitosight's notifications off in System Settings: respect it
                return
            if status in (2, 3, 4):  # authorized, provisional, ephemeral
                try:
                    post()
                    return
                except Exception:
                    pass
            _notify_state["allowed"] = False
            _osascript_notify(title, body)

        center.getNotificationSettingsWithCompletionHandler_(got)
    except Exception:
        _notify_state["allowed"] = False
        _osascript_notify(title, body)


def selftest() -> str:
    """Exercised by the packaged app's selftest on the build machine: nothing may crash."""
    out = []
    out.append(f"activity={begin_activity('selftest', 'selftest')}")
    end_activity("selftest")
    try:
        import UserNotifications  # noqa: F401

        out.append("usernotifications=yes")
    except Exception as e:  # noqa: BLE001
        out.append(f"usernotifications=no ({e})")
    try:
        from PyObjCTools import AppHelper  # noqa: F401
        from AppKit import NSApplication  # noqa: F401

        out.append("appkit=yes")
    except Exception as e:  # noqa: BLE001
        out.append(f"appkit=no ({e})")
    return " ".join(out)
