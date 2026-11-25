"""Analysing on another Mac through a shared folder: the asking Mac and the working Mac are
simulated in one process by switching the computer name."""
import shutil
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipfucci import remote  # noqa: E402
from pipfucci.demo import make_demo  # noqa: E402
from pipfucci.experiment import Cancelled, Experiment  # noqa: E402


def as_mac(name):
    remote._name_cache[:] = [name]


@pytest.fixture()
def shared(tmp_path):
    d = tmp_path / "OneDrive" / "Lab"
    make_demo(d / "Exp 1", wells={"B2": "slow"}, n_frames=13)
    return d.resolve()


def test_request_runs_on_the_other_mac_and_results_arrive(shared):
    folder = shared / "Exp 1"
    as_mac("MacBook")
    exp = Experiment(folder)
    exp.settings.experiment_name = "Palbo run"
    rid = remote.submit(exp, "analysis", shared)
    assert remote.pending(shared, "Exp 1")["id"] == rid
    assert remote.next_request(shared, "MacBook") is None  # never your own request

    as_mac("Mac mini")
    remote.heartbeat(shared, None, 8)
    req = remote.next_request(shared, "Mac mini")
    assert req["id"] == rid and req["settings"]["experiment_name"] == "Palbo run"
    seen = []
    remote.process(req, shared, lambda **kw: seen.append(kw.get("stage")), threading.Event(), 1)
    st = remote.read_json(remote.queue_dir(shared) / f"{rid}.status.json")
    assert st["state"] == "done" and st["host"] == "Mac mini" and st["manifest"]["files"]["cells.pkl"]
    assert st["manifest"]["masks"] and any(seen)
    assert remote.next_request(shared, "Mac mini") is None  # taken

    as_mac("MacBook")
    ws = remote.workers(shared)
    assert ws[0]["name"] == "Mac mini" and ws[0]["online"]
    reports = []
    remote.wait(folder, shared, rid, lambda **kw: reports.append(kw), threading.Event())
    fresh = Experiment(folder)
    assert fresh.has_results and fresh.settings.experiment_name == "Palbo run"
    assert remote.pending(shared, "Exp 1")["id"] == rid  # done but not loaded yet
    remote.mark_loaded(shared, rid)
    assert remote.pending(shared, "Exp 1") is None


def test_results_are_awaited_until_the_sync_brings_them(shared, tmp_path):
    folder = shared / "Exp 1"
    as_mac("MacBook")
    rid = remote.submit(Experiment(folder), "analysis", shared)
    as_mac("Mac mini")
    remote.process(remote.next_request(shared, "Mac mini"), shared, lambda **kw: None, threading.Event(), 1)
    # the asking Mac's copy has not received the new cells table yet
    cells = folder / "PIP-FUCCI results" / "cells.pkl"
    real = cells.read_bytes()
    cells.write_bytes(b"old")
    as_mac("MacBook")
    reports, cancel = [], threading.Event()

    def arrive():
        cells.write_bytes(real)

    timer = threading.Timer(2.5, arrive)
    timer.start()
    remote.wait(folder, shared, rid, lambda **kw: reports.append(kw), cancel)
    assert any("Getting the results" in (r.get("stage") or "") for r in reports)
    assert Experiment(folder).has_results


def test_cancel_before_and_during(shared):
    folder = shared / "Exp 1"
    as_mac("MacBook")
    rid = remote.submit(Experiment(folder), "analysis", shared)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        remote.wait(folder, shared, rid, lambda **kw: None, cancel)
    assert (remote.queue_dir(shared) / f"{rid}.cancel.json").exists()
    as_mac("Mac mini")
    assert remote.next_request(shared, "Mac mini") is None  # cancelled before it was taken
    assert remote.read_json(remote.queue_dir(shared) / f"{rid}.status.json")["state"] == "cancelled"
    # cancelled while running: the working Mac stops
    as_mac("MacBook")
    rid2 = remote.submit(Experiment(folder), "analysis", shared)
    as_mac("Mac mini")
    req = remote.next_request(shared, "Mac mini")
    remote.write_json(remote.queue_dir(shared) / f"{rid2}.cancel.json", {"id": rid2})
    with pytest.raises(Cancelled):
        remote.process(req, shared, lambda **kw: None, threading.Event(), 1)
    assert remote.read_json(remote.queue_dir(shared) / f"{rid2}.status.json")["state"] == "cancelled"


def test_failure_is_reported_to_the_asking_mac(shared, monkeypatch):
    folder = shared / "Exp 1"
    as_mac("MacBook")
    rid = remote.submit(Experiment(folder), "analysis", shared)
    as_mac("Mac mini")

    def boom(self, **kw):
        raise RuntimeError("out of disk space")

    monkeypatch.setattr(Experiment, "run", boom)
    with pytest.raises(RuntimeError):
        remote.process(remote.next_request(shared, "Mac mini"), shared, lambda **kw: None, threading.Event(), 1)
    as_mac("MacBook")
    with pytest.raises(RuntimeError, match="On Mac mini: out of disk space"):
        remote.wait(folder, shared, rid, lambda **kw: None, threading.Event())
    assert remote.pending(shared, "Exp 1") is None


def test_experiments_outside_the_shared_folder_stay_here(shared, tmp_path, monkeypatch):
    monkeypatch.setattr(remote, "prefs", lambda: {"shared": str(shared), "run_on": "remote", "worker": False})
    as_mac("MacBook")
    inside = Experiment(shared / "Exp 1")
    other = tmp_path / "Desktop exp"
    shutil.copytree(shared / "Exp 1", other)
    assert remote.plan(inside)["rel"] == "Exp 1"
    assert remote.plan(Experiment(other)) is None
    info = remote.plan_info(Experiment(other))
    assert not info["use"] and "not inside the shared folder" in info["why"]
    monkeypatch.setattr(remote, "prefs", lambda: {"shared": str(shared), "run_on": "here", "worker": False})
    assert remote.plan(inside) is None
