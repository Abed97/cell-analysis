import hashlib
import io
import json
import plistlib
import sys
import zipfile

import pytest

from pipfucci import updater as up


def fake_bundle(root, version, name="PIP-FUCCI.app"):
    app = root / name
    (app / "Contents" / "MacOS").mkdir(parents=True)
    exe = app / "Contents" / "MacOS" / "PIP-FUCCI"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    with open(app / "Contents" / "Info.plist", "wb") as fh:
        plistlib.dump({"CFBundleShortVersionString": version}, fh)
    return app


def zip_bytes(app):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in app.rglob("*"):
            z.write(p, p.relative_to(app.parent))
    return buf.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPFUCCI_SUPPORT_DIR", str(tmp_path / "support"))
    monkeypatch.delenv("PIPFUCCI_LAUNCHED", raising=False)
    monkeypatch.delenv("PIPFUCCI_NO_UPDATES", raising=False)
    monkeypatch.setattr(up, "enabled", lambda: True)
    monkeypatch.setattr(up, "__version__", "0.1.5")
    monkeypatch.setattr(up, "GITHUB_REPO", "owner/repo")
    return tmp_path


def test_version_order():
    assert up.vt("0.1.10") > up.vt("0.1.9") > up.vt("0.1") and up.vt("v1.0.0") == (1, 0, 0)


def test_handoff_picks_newest_and_skips_bad(env, monkeypatch):
    vd = up.versions_dir()
    fake_bundle(vd, "0.1.7", "PIP-FUCCI-0.1.7.app")
    fake_bundle(vd, "0.1.6", "PIP-FUCCI-0.1.6.app")
    fake_bundle(vd, "0.1.4", "PIP-FUCCI-0.1.4.app")
    calls = []

    class Execd(Exception):
        pass

    def fake_exec(exe, argv, e):  # a real exec never returns
        calls.append(exe)
        raise Execd()

    monkeypatch.setattr(up.os, "execve", fake_exec)
    for _ in range(3):  # 0.1.7 never comes up: after two tries it falls back to 0.1.6
        with pytest.raises(Execd):
            up.maybe_launch_newer(["app"])
    assert "PIP-FUCCI-0.1.7.app" in calls[0] and "PIP-FUCCI-0.1.7.app" in calls[1]
    assert "PIP-FUCCI-0.1.6.app" in calls[2]
    assert "0.1.7" in up.load_state()["bad"]


def test_no_handoff_when_disabled_or_older(env, monkeypatch):
    fake_bundle(up.versions_dir(), "0.1.4", "PIP-FUCCI-0.1.4.app")
    calls = []
    monkeypatch.setattr(up.os, "execve", lambda exe, argv, e: calls.append(exe))
    up.maybe_launch_newer(["app"])
    up.maybe_launch_newer(["app", "--selftest"])
    assert calls == []


def test_mark_healthy_reports_update_once(env, monkeypatch):
    up.save_state({"last_run": "0.1.4", "attempts": {"0.1.5": 1}})
    assert up.mark_healthy() == "0.1.5"
    assert up.load_state()["attempts"]["0.1.5"] == 0
    assert up.mark_healthy() is None


class FakeResp(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def serve(monkeypatch, files):
    monkeypatch.setattr(up, "_get", lambda url, timeout=20: FakeResp(files[url]))


def release(version, payload, sha=None):
    name = f"PIP-FUCCI-{up.arch_label()}.app.zip"
    rel = {"tag_name": "v" + version, "assets": [
        {"name": name, "browser_download_url": "https://x/" + name},
        {"name": name + ".sha256", "browser_download_url": "https://x/" + name + ".sha256"}]}
    return {
        "https://api.github.com/repos/owner/repo/releases/latest": json.dumps(rel).encode(),
        "https://x/" + name: payload,
        "https://x/" + name + ".sha256": ((sha or hashlib.sha256(payload).hexdigest()) + "  f\n").encode(),
    }


def test_download_and_install(env, monkeypatch):
    payload = zip_bytes(fake_bundle(env / "build", "0.1.9"))
    serve(monkeypatch, release("0.1.9", payload))
    u = up.Updater()
    u.check_and_download()
    assert u.status["state"] == "ready" and u.status["ready_version"] == "0.1.9"
    assert [v for v, _ in up.installed_versions()] == ["0.1.9"]
    u.check_and_download()  # already downloaded: no second download
    assert u.status["state"] == "ready"


def test_corrupt_download_rejected(env, monkeypatch):
    payload = zip_bytes(fake_bundle(env / "build", "0.1.9"))
    serve(monkeypatch, release("0.1.9", payload, sha="0" * 64))
    with pytest.raises(RuntimeError, match="checksum"):
        up.Updater().check_and_download()
    assert up.installed_versions() == []


def test_up_to_date_and_cleanup(env, monkeypatch):
    vd = up.versions_dir()
    fake_bundle(vd, "0.1.3", "PIP-FUCCI-0.1.3.app")  # older than the running 0.1.5
    serve(monkeypatch, release("0.1.5", b"unused"))
    u = up.Updater()
    u.check_and_download()
    assert u.status["state"] == "up_to_date"
    assert up.installed_versions() == []


def test_running_copies_are_never_cleaned_up(env, monkeypatch):
    vd = up.versions_dir()
    fake_bundle(vd, "0.1.6", "PIP-FUCCI-0.1.6.app")
    fake_bundle(vd, "0.1.9", "PIP-FUCCI-0.1.9.app")
    (vd / f".inuse-0.1.6-{up.os.getpid()}").write_text("")  # a window still runs 0.1.6
    (vd / ".inuse-0.1.9-999999").write_text("")  # stale marker from a dead process
    up.Updater().cleanup()
    assert sorted(v for v, _ in up.installed_versions()) == ["0.1.6", "0.1.9"]
    assert not (vd / ".inuse-0.1.9-999999").exists()


def test_backend_starts_and_reports_port(tmp_path, monkeypatch):
    """The real --serve mode (as started by "Update now") comes up and answers."""
    import urllib.request
    from pathlib import Path

    monkeypatch.setenv("PIPFUCCI_SUPPORT_DIR", str(tmp_path / "support"))
    monkeypatch.setenv("HOME", str(tmp_path))
    root = Path(__file__).resolve().parents[1]
    ready = tmp_path / "ready"
    code = f"import sys; sys.path.insert(0, {str(root)!r}); from pipfucci.app import main; sys.exit(main(sys.argv[1:]))"
    cmd = [sys.executable, "-c", code, "--serve", "--parent-pid", str(up.os.getpid()), "--ready-file", str(ready)]
    port = up.spawn_backend(cmd, ready, timeout=60)
    st = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=10).read())
    assert st["app"] == "PIP-FUCCI"


def test_backend_that_dies_is_reported(tmp_path):
    with pytest.raises(RuntimeError, match="stopped"):
        up.spawn_backend([sys.executable, "-c", "raise SystemExit(3)"], tmp_path / "ready", timeout=20)


def test_prefs_roundtrip_and_auto_install_off(env, monkeypatch):
    assert up.load_prefs() == {"auto_check": True, "auto_install": True, "workers": 0}
    p = up.save_prefs({"auto_install": False, "workers": "3", "bogus": 1})
    assert p == {"auto_check": True, "auto_install": False, "workers": 3}
    # with automatic installs off, launching never switches to a downloaded version
    fake_bundle(up.versions_dir(), "0.1.9", name="PIP-FUCCI-0.1.9.app")
    called = []
    monkeypatch.setattr(up.os, "execve", lambda *a: called.append(a))
    up.maybe_launch_newer(["PIP-FUCCI"])
    assert not called


def test_downloaded_version_shows_as_ready_at_start(env, monkeypatch):
    fake_bundle(up.versions_dir(), "0.1.9", name="PIP-FUCCI-0.1.9.app")
    u = up.Updater()
    monkeypatch.setattr(u, "_loop", lambda delay: None)
    u.start()
    assert u.status["state"] == "ready" and u.status["ready_version"] == "0.1.9"
