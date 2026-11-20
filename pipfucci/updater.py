"""Automatic updates from GitHub Releases, with no Apple account and no admin password.

The app the user installed (in Applications) is never modified. New versions are downloaded
into ~/Library/Application Support/PIP-FUCCI/versions/, and at launch the installed app hands
over to the newest downloaded version (exec, same process). Files downloaded by the app itself
carry no quarantine flag, so macOS does not ask again, and nothing inside /Applications is
written, so macOS's app-management protection is not involved.

A version that fails to start twice in a row is skipped, so a bad build can't lock her out.
This module only uses the standard library: it runs before anything heavy is imported.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from . import APP_NAME, GITHUB_REPO, __version__

EXE_REL = Path("Contents") / "MacOS" / APP_NAME
CHECK_EVERY_S = 15 * 60
_NAME = re.compile(r"^PIP-FUCCI-(\d+(?:\.\d+)*)\.app$")


# ------------------------------------------------------------------ paths and versions
def support_dir() -> Path:
    if os.environ.get("PIPFUCCI_SUPPORT_DIR"):
        d = Path(os.environ["PIPFUCCI_SUPPORT_DIR"])
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        d = Path.home() / ".pip-fucci"
    d.mkdir(parents=True, exist_ok=True)
    return d


def versions_dir() -> Path:
    d = support_dir() / "versions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def vt(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4]) or (0,)


def arch_label() -> str:
    return "AppleSilicon" if platform.machine() == "arm64" else "Intel"


def enabled() -> bool:
    return (sys.platform == "darwin" and getattr(sys, "frozen", False) and bool(GITHUB_REPO)
            and not os.environ.get("PIPFUCCI_NO_UPDATES"))


def installed_versions() -> list[tuple[str, Path]]:
    out = []
    for p in versions_dir().iterdir():
        m = _NAME.match(p.name)
        if m and (p / EXE_REL).is_file():
            out.append((m.group(1), p))
    out.sort(key=lambda x: vt(x[0]), reverse=True)
    return out


PREFS_DEFAULT = {"auto_check": True, "auto_install": True, "workers": 0, "notify": True}


def load_prefs() -> dict:
    """App preferences (not per experiment): updates and processing cores."""
    try:
        d = json.loads((support_dir() / "prefs.json").read_text())
    except Exception:
        d = {}
    out = dict(PREFS_DEFAULT)
    for k, v in d.items():
        if k in out and isinstance(v, type(out[k])):
            out[k] = v
    return out


def save_prefs(patch: dict) -> dict:
    cur = load_prefs()
    for k, v in (patch or {}).items():
        if k in ("auto_check", "auto_install", "notify"):
            cur[k] = bool(v)
        elif k == "workers":
            try:
                cur[k] = max(0, min(64, int(v)))
            except Exception:
                pass
    p = support_dir() / "prefs.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur, indent=1))
    tmp.replace(p)
    return cur


def _state_path() -> Path:
    return versions_dir() / "state.json"


def load_state() -> dict:
    try:
        return json.loads(_state_path().read_text())
    except Exception:
        return {}


def save_state(st: dict) -> None:
    p = _state_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    tmp.replace(p)


def running_bundle() -> Path | None:
    exe = Path(sys.executable).resolve()
    for parent in exe.parents:
        if parent.suffix == ".app":
            return parent
    return None


def register_running() -> None:
    """Mark the running copy as in use so another process never deletes it."""
    rb = running_bundle()
    if rb is None or rb.parent.resolve() != versions_dir().resolve():
        return
    m = _NAME.match(rb.name)
    if m:
        (versions_dir() / f".inuse-{m.group(1)}-{os.getpid()}").write_text("")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return True


def in_use_versions() -> set:
    used = set()
    for p in versions_dir().glob(".inuse-*"):
        try:
            _, ver, pid = p.name.split("-", 2)
            if _pid_alive(int(pid)):
                used.add(ver)
            else:
                p.unlink(missing_ok=True)
        except Exception:
            p.unlink(missing_ok=True)
    return used


def note_attempt(ver: str) -> None:
    st = load_state()
    a = st.setdefault("attempts", {})
    a[ver] = a.get(ver, 0) + 1
    save_state(st)


def note_failed(ver: str) -> None:
    st = load_state()
    if st.setdefault("attempts", {}).get(ver, 0) >= 2:
        st["bad"] = sorted(set(st.get("bad", [])) | {ver}, key=vt)
    save_state(st)


def spawn_backend(cmd: list[str], ready_file: Path, timeout: float = 150.0) -> int:
    """Start a backend (``--serve``) and wait until it reports its port. Raises on failure."""
    import subprocess as sp

    ready_file.unlink(missing_ok=True)
    env = dict(os.environ, PIPFUCCI_LAUNCHED="1")
    proc = sp.Popen(cmd, env=env, stdin=sp.DEVNULL, stdout=sp.DEVNULL, stderr=sp.DEVNULL, start_new_session=True)
    t0 = time.time()
    while time.time() - t0 < timeout:
        if ready_file.exists():
            txt = ready_file.read_text().strip()
            if txt.isdigit():
                ready_file.unlink(missing_ok=True)
                return int(txt)
        if proc.poll() is not None:
            raise RuntimeError(f"the new version stopped while starting (code {proc.returncode})")
        time.sleep(0.25)
    proc.kill()
    raise RuntimeError("the new version took too long to start")


# ------------------------------------------------------------------ launch handoff
def maybe_launch_newer(argv: list[str]) -> None:
    """Called first thing at startup: if a newer downloaded version exists, become it."""
    if not enabled() or "--selftest" in argv or os.environ.get("PIPFUCCI_LAUNCHED"):
        return
    if not load_prefs().get("auto_install", True):  # she installs from the "Update now" button
        return
    st = load_state()
    bad = set(st.get("bad", []))
    attempts = st.setdefault("attempts", {})
    for ver, path in installed_versions():
        if vt(ver) <= vt(__version__):
            break
        if ver in bad:
            continue
        if attempts.get(ver, 0) >= 2:  # started twice without coming up: skip it
            bad.add(ver)
            st["bad"] = sorted(bad, key=vt)
            save_state(st)
            continue
        attempts[ver] = attempts.get(ver, 0) + 1
        save_state(st)
        exe = str(path / EXE_REL)
        env = dict(os.environ, PIPFUCCI_LAUNCHED="1", PIPFUCCI_STUB=sys.executable)
        try:
            os.execve(exe, [exe] + argv[1:], env)
        except OSError:
            bad.add(ver)
            st["bad"] = sorted(bad, key=vt)
            save_state(st)


def mark_healthy() -> str | None:
    """The app came up properly. Returns the version just updated to, once, if any."""
    if not enabled():
        return None
    st = load_state()
    st.setdefault("attempts", {})[__version__] = 0
    prev = st.get("last_run")
    just = __version__ if prev and vt(__version__) > vt(prev) else None
    st["last_run"] = __version__
    save_state(st)
    return just


def restart_into_newest() -> None:
    """Re-run the installed app, which hands over to the newest downloaded version."""
    stub = os.environ.get("PIPFUCCI_STUB") or sys.executable
    env = {k: v for k, v in os.environ.items() if k not in ("PIPFUCCI_LAUNCHED", "PIPFUCCI_STUB")}
    os.execve(stub, [stub], env)


# ------------------------------------------------------------------ background updater
def _get(url: str, timeout: float = 20):
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{__version__}",
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def extract_app(zip_path: Path, dest: Path) -> Path:
    """Unzip a .app (ditto keeps symlinks, permissions and signatures); returns the bundle path."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    if shutil.which("ditto"):
        subprocess.run(["ditto", "-x", "-k", str(zip_path), str(dest)], check=True, capture_output=True)
    else:  # tests on Linux
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(dest)
        for p in dest.rglob("*"):
            if p.is_file() and p.parent.name == "MacOS":
                p.chmod(0o755)
    apps = [p for p in dest.iterdir() if p.suffix == ".app"]
    if len(apps) != 1:
        raise RuntimeError("update archive does not contain exactly one app")
    return apps[0]


def bundle_version(app: Path) -> str | None:
    try:
        with open(app / "Contents" / "Info.plist", "rb") as fh:
            return plistlib.load(fh).get("CFBundleShortVersionString")
    except Exception:
        return None


class Updater:
    def __init__(self):
        self.lock = threading.Lock()
        self.status = {"enabled": enabled(), "state": "idle", "current": __version__, "latest": None,
                       "ready_version": None, "progress": 0.0, "message": None, "just_updated": None,
                       "checked_at": None}
        self._thread = None
        self.stopped = False
        self.wake = threading.Event()
        self.force = False

    def stop(self) -> None:
        self.stopped = True
        self.wake.set()

    def snapshot(self) -> dict:
        with self.lock:
            s = dict(self.status)
            self.status["just_updated"] = None  # shown once
        return s

    def _set(self, **kw):
        with self.lock:
            self.status.update(kw)

    def start(self, delay: float = 3.0) -> None:
        if not self.status["enabled"] or self._thread:
            return
        if self.status["state"] == "idle":  # a version downloaded earlier shows at once
            for ver, _ in installed_versions():
                if vt(ver) > vt(__version__) and ver not in set(load_state().get("bad", [])):
                    self._set(state="ready", ready_version=ver, latest=ver)
                break
        self._thread = threading.Thread(target=self._loop, args=(delay,), daemon=True)
        self._thread.start()

    def check_now(self) -> None:
        """"Check for updates" from the settings: runs even when automatic checks are off."""
        self.force = True
        self.wake.set()

    def _loop(self, delay):
        self.wake.wait(delay)
        while not self.stopped:
            self.wake.clear()
            force, self.force = self.force, False
            if force or load_prefs().get("auto_check", True):
                try:
                    self.check_and_download()
                except Exception as e:  # noqa: BLE001 - never let updates break the app
                    rv = self.status.get("ready_version")
                    still = bool(rv) and (versions_dir() / f"{APP_NAME}-{rv}.app" / EXE_REL).is_file()
                    self._set(state="ready" if still else "error", message=str(e))
                self._set(checked_at=time.time())
            elif self.status["state"] not in ("ready",):
                self._set(state="off")
            self.wake.wait(CHECK_EVERY_S)

    def check_and_download(self) -> None:
        """One check, with a lock so two running copies never download at the same time."""
        try:
            import fcntl
        except ImportError:  # pragma: no cover
            return self._check_and_download()
        with open(versions_dir() / ".lock", "w") as lk:
            try:
                fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return None
            try:
                return self._check_and_download()
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)

    def _check_and_download(self) -> None:
        if self.status["state"] != "ready":
            self._set(state="checking", message=None)
        with _get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest") as r:
            rel = json.loads(r.read().decode())
        latest = str(rel.get("tag_name", "")).lstrip("v")
        self._set(latest=latest)
        if not latest or vt(latest) <= vt(__version__):
            self._set(state="up_to_date")
            self.cleanup()
            return
        if latest in set(load_state().get("bad", [])):
            self._set(state="up_to_date")
            return
        if any(v == latest for v, _ in installed_versions()):
            self._set(state="ready", ready_version=latest)
            return
        name = f"{APP_NAME}-{arch_label()}.app.zip"
        assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
        if name not in assets:
            self._set(state="up_to_date", message="no update file for this Mac yet")
            return
        expected = None
        if name + ".sha256" in assets:
            with _get(assets[name + ".sha256"]) as r:
                expected = r.read().decode().split()[0].strip().lower()
        self._set(state="downloading", progress=0.0)
        vd = versions_dir()
        part = vd / f".download-{latest}.zip"
        h = hashlib.sha256()
        with _get(assets[name], timeout=60) as r, open(part, "wb") as fh:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if total:
                    self._set(progress=done / total)
        if expected and h.hexdigest() != expected:
            part.unlink(missing_ok=True)
            raise RuntimeError("downloaded update is corrupted (checksum mismatch)")
        self._set(state="installing")
        tmp = vd / f".extract-{latest}"
        app = extract_app(part, tmp)
        bv = bundle_version(app)
        if bv and vt(bv) != vt(latest):
            raise RuntimeError(f"update archive is version {bv}, expected {latest}")
        if shutil.which("xattr"):
            subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(app)], capture_output=True)
        final = vd / f"{APP_NAME}-{latest}.app"
        if final.exists():
            shutil.rmtree(final)
        os.replace(app, final)
        shutil.rmtree(tmp, ignore_errors=True)
        part.unlink(missing_ok=True)
        self._set(state="ready", ready_version=latest, progress=1.0)
        self.cleanup()

    def cleanup(self) -> None:
        """Keep the newest downloaded version and the one running now; remove the rest."""
        keep = set()
        vs = installed_versions()
        if vs and vt(vs[0][0]) > vt(__version__):
            keep.add(vs[0][1].resolve())
        rb = running_bundle()
        if rb:
            keep.add(rb.resolve())
        used = in_use_versions()
        for v, p in vs:
            if v in used:
                continue
            if p.resolve() not in keep:
                shutil.rmtree(p, ignore_errors=True)
        for p in versions_dir().glob(".extract-*"):
            shutil.rmtree(p, ignore_errors=True)


UPDATER = Updater()
