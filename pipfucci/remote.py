"""Analyses run on another Mac through a folder both Macs sync (OneDrive; iCloud Drive or Dropbox work
the same way).

The Mac that asks (her MacBook) writes a request into "<shared folder>/Mitosight jobs". The Mac
that works (her Mac mini, with "Run analyses sent from my other Macs" on) picks it up, analyses the
experiment, which lives in the shared folder too, and writes its progress next to the request
every few seconds. Results land in the experiment's own results folder, so the sync brings them
to every Mac; when the asking Mac has received all of them (checked against a list of files and
checksums the worker writes last) it loads them.

Every file has one writer, so the sync never has two versions of a file to reconcile:
  <id>.request.json, <id>.cancel.json   written by the asking Mac
  <id>.status.json                      written by the working Mac
  worker <name>.json                    each working Mac's heartbeat
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import socket
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

QUEUE = "Mitosight jobs"
REMOTE_JOBS = {"analysis": "run", "remask": "remask", "measure": "remeasure"}
HEARTBEAT_S = 30      # a working Mac says it is there this often
ONLINE_S = 300        # seen within 5 minutes counts as on (the sync can lag a minute or two)
STATUS_EVERY_S = 4    # progress written this often while working
POLL_S = 2            # the asking Mac looks for news this often
KEEP_DAYS = 14        # finished requests are tidied away after this
KEY_FILES = ("cells.pkl", "tracks.pkl", "meta.json")
FINISHED = ("done", "failed", "cancelled")

_name_cache: list = []


def computer_name() -> str:
    """The name she gave this Mac ("Sara's Mac mini"), else the host name."""
    if _name_cache:
        return _name_cache[0]
    name = os.environ.get("PIPFUCCI_COMPUTER_NAME", "")  # tests run two "Macs" on one machine
    if name:
        _name_cache.append(name)
        return name
    if sys.platform == "darwin":
        try:
            name = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True,
                                  timeout=5).stdout.strip()
        except Exception:
            name = ""
    name = name or socket.gethostname().split(".")[0] or "this Mac"
    _name_cache.append(name)
    return name


def prefs() -> dict:
    from .updater import load_prefs

    p = load_prefs()
    return {"shared": p.get("shared_folder") or "", "run_on": p.get("run_on") or "here",
            "worker": bool(p.get("worker"))}


def shared_dir(path: str | None = None) -> Path | None:
    p = path if path is not None else prefs()["shared"]
    if not p:
        return None
    d = Path(p).expanduser()
    return d.resolve() if d.is_dir() else None


def queue_dir(shared: Path, create: bool = False) -> Path:
    q = shared / QUEUE
    if create:
        q.mkdir(exist_ok=True)
    return q


def rel_of(folder: Path, shared: Path | None) -> str | None:
    """The experiment's place inside the shared folder, the same on every Mac; None if outside."""
    if shared is None:
        return None
    try:
        return Path(folder).resolve().relative_to(shared).as_posix()
    except ValueError:
        return None


def write_json(path: Path, obj) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(obj, indent=1))
    os.replace(tmp, path)


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-%04x" % random.getrandbits(16)


def _version() -> str:
    from . import __version__

    return __version__


def ago(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    s = max(0, int(seconds))
    if s < 90:
        return f"{s} s ago"
    if s < 5400:
        return f"{round(s / 60)} min ago"
    if s < 172800:
        return f"{round(s / 3600)} h ago"
    return f"{round(s / 86400)} days ago"


# ------------------------------------------------------------ who is there
def workers(shared: Path | None) -> list[dict]:
    """Working Macs that have written a heartbeat into the shared folder, most recent first."""
    if shared is None:
        return []
    out = []
    now = time.time()
    for p in queue_dir(shared).glob("worker *.json"):
        d = read_json(p)
        if not isinstance(d, dict) or not d.get("name"):
            continue
        age = now - float(d.get("updated", 0))
        out.append({**d, "age_s": round(age), "online": age < ONLINE_S, "seen": ago(age)})
    return sorted(out, key=lambda d: d["age_s"])


def plan(exp) -> dict | None:
    """Where a heavy job on this experiment runs: None = here; else {shared, rel, target, ...}."""
    pr = prefs()
    if pr["run_on"] != "remote":
        return None
    shared = shared_dir(pr["shared"])
    rel = rel_of(exp.folder, shared)
    if rel is None:
        return None
    me = computer_name()
    ws = [w for w in workers(shared) if w["name"] != me]
    target = ws[0] if ws else None
    return {"shared": shared, "rel": rel, "target": target["name"] if target else "the other Mac",
            "online": bool(target and target["online"]), "seen": target["seen"] if target else "never"}


def plan_info(exp) -> dict:
    """For the window: where Analyse will run and why."""
    pr = prefs()
    if pr["run_on"] != "remote":
        return {"use": False}
    shared = shared_dir(pr["shared"])
    if shared is None:
        return {"use": False, "why": "The shared folder is not set or not found on this Mac (Settings)."}
    if exp is None or rel_of(exp.folder, shared) is None:
        return {"use": False, "why": f"This experiment is not inside the shared folder ({shared.name}), so it is analysed on this Mac."}
    p = plan(exp)
    return {"use": True, "target": p["target"], "online": p["online"], "seen": p["seen"]}


# ------------------------------------------------------------ results arriving through the sync
def manifest(results_dir: Path) -> dict:
    """What the asking Mac must have before it loads the results: checksums of the main files and
    the name and size of every mask file."""
    files = {}
    for name in KEY_FILES:
        p = results_dir / name
        if p.is_file():
            files[name] = _sha(p)
    masks = {}
    mdir = results_dir / "masks"
    if mdir.is_dir():
        for p in mdir.rglob("*.npz"):
            masks[p.relative_to(mdir).as_posix()] = p.stat().st_size
    return {"files": files, "masks": masks}


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:24]


class Arrivals:
    """How much of a manifest has reached this Mac (re-hashes a file only when it changed)."""

    def __init__(self, results_dir: Path, man: dict):
        self.dir, self.man, self.seen = results_dir, man, {}

    def check(self) -> tuple[int, int]:
        have, total = 0, len(self.man.get("files", {})) + len(self.man.get("masks", {}))
        for name, sha in self.man.get("files", {}).items():
            p = self.dir / name
            try:
                st = p.stat()
            except OSError:
                continue
            key = (st.st_size, st.st_mtime_ns)
            if self.seen.get(name, (None,))[0] != key:
                try:
                    self.seen[name] = (key, _sha(p))
                except OSError:
                    continue
            have += self.seen[name][1] == sha
        mdir = self.dir / "masks"
        for rel, size in self.man.get("masks", {}).items():
            try:
                have += (mdir / rel).stat().st_size == size
            except OSError:
                pass
        return have, total


# ------------------------------------------------------------ the asking Mac
def submit(exp, job: str, shared: Path) -> str:
    rel = rel_of(exp.folder, shared)
    if rel is None:
        raise ValueError("This experiment is not inside the shared folder.")
    rid = new_id()  # the settings travel in the request; the working Mac writes the experiment's files
    write_json(queue_dir(shared, create=True) / f"{rid}.request.json",
               {"id": rid, "job": job, "rel": rel, "settings": exp.settings.to_dict(), "from": computer_name(),
                "version": _version(), "created": time.time(), "experiment": exp.settings.experiment_name or exp.folder.name})
    return rid


def pending(shared: Path | None, rel: str | None) -> dict | None:
    """The newest unfinished request for this experiment (to keep following it after a restart)."""
    if shared is None or rel is None:
        return None
    q = queue_dir(shared)
    if not q.is_dir():
        return None
    for p in sorted(q.glob("*.request.json"), reverse=True):
        r = read_json(p)
        if not isinstance(r, dict) or r.get("rel") != rel:
            continue
        if time.time() - float(r.get("created", 0)) > KEEP_DAYS * 86400:
            return None
        st = read_json(q / f"{r['id']}.status.json") or {}
        if st.get("state") in FINISHED and not (st.get("state") == "done" and not r.get("loaded")):
            return None
        if (q / f"{r['id']}.cancel.json").exists():
            return None
        return r
    return None


def wait(exp_folder: Path, shared: Path, rid: str, report, cancel: threading.Event) -> dict:
    """Follow a request until its results are on this Mac. Raises Cancelled or RuntimeError."""
    from .experiment import Cancelled

    q = queue_dir(shared)
    me = computer_name()
    cancel_sent = None
    arrivals = None
    while True:
        st = read_json(q / f"{rid}.status.json") or {}
        state = st.get("state")
        if cancel.is_set() and cancel_sent is None and state not in FINISHED:
            write_json(q / f"{rid}.cancel.json", {"id": rid, "from": me, "at": time.time()})
            cancel_sent = time.time()
        if cancel_sent is not None and (state in FINISHED or time.time() - cancel_sent > 20 or state is None):
            raise Cancelled()
        if state == "failed":
            raise RuntimeError(f"On {st.get('host', 'the other Mac')}: {st.get('error') or 'failed'}")
        if state == "cancelled":
            raise Cancelled()
        if state == "done":
            if arrivals is None:
                arrivals = Arrivals(Path(exp_folder) / _results_dirname(), st.get("manifest") or {})
            have, total = arrivals.check()
            if have >= total:
                return st
            report(stage=f"Getting the results from {st.get('host', 'the other Mac')} through the shared folder",
                   fraction=have / max(1, total), done=have, total=total, unit="files", eta_s=None,
                   where=st.get("host"), remote=True)
        elif state in ("claimed", "running"):
            age = time.time() - float(st.get("updated", time.time()))
            report(stage=st.get("stage") or "Working", fraction=float(st.get("fraction") or 0.0),
                   done=st.get("done"), total=st.get("total"), unit=st.get("unit"), eta_s=st.get("eta_s"),
                   workers=st.get("workers"), where=st.get("host"), remote=True, remote_seen=ago(age))
        else:
            ws = [w for w in workers(shared) if w["name"] != me]
            w = ws[0] if ws else None
            who = w["name"] if w else "the other Mac"
            if w and w["online"]:
                stage = f"Sent to {who}; waiting for it to start"
            elif w:
                stage = f"Waiting for {who} (last seen {w['seen']}). It starts when that Mac is on with Mitosight open"
            else:
                stage = "Waiting for a Mac with “Run analyses sent from my other Macs” on"
            report(stage=stage, fraction=0.0, done=None, total=None, eta_s=None, where=who, remote=True)
        cancel.wait(POLL_S)


def mark_loaded(shared: Path, rid: str) -> None:
    p = queue_dir(shared) / f"{rid}.request.json"
    r = read_json(p)
    if isinstance(r, dict):
        r["loaded"] = time.time()
        write_json(p, r)


def _results_dirname() -> str:
    from .io_incucyte import RESULTS_DIRNAME

    return RESULTS_DIRNAME


# ------------------------------------------------------------ the working Mac
def next_request(shared: Path, me: str) -> dict | None:
    """The oldest request nobody has taken yet (requests from this Mac itself are left alone)."""
    q = queue_dir(shared)
    if not q.is_dir():
        return None
    for p in sorted(q.glob("*.request.json")):
        r = read_json(p)
        if not isinstance(r, dict) or r.get("from") == me or r.get("job") not in REMOTE_JOBS:
            continue
        sp = q / f"{r['id']}.status.json"
        st = read_json(sp)
        if st is not None:
            # taken by this Mac before a restart and never finished: take it again
            stale = (st.get("host") == me and st.get("state") in ("claimed", "running")
                     and time.time() - float(st.get("updated", 0)) > 120)
            if not stale:
                continue
        if (q / f"{r['id']}.cancel.json").exists():
            write_json(sp, {"id": r["id"], "state": "cancelled", "host": me, "updated": time.time()})
            continue
        return r
    return None


def process(req: dict, shared: Path, report, cancel: threading.Event, workers_n=None) -> Path:
    """Analyse one request here, writing its status for the asking Mac. Returns the experiment folder."""
    from .experiment import Cancelled, Experiment
    from .settings import Settings

    q = queue_dir(shared)
    rid, me = req["id"], computer_name()
    sp = q / f"{rid}.status.json"
    status = {"id": rid, "state": "claimed", "host": me, "version": _version(), "updated": time.time(),
              "stage": "Starting", "fraction": 0.0}
    write_json(sp, status)
    last = [0.0]

    def push(force=False, **kw):
        status.update({k: v for k, v in kw.items() if k in ("stage", "fraction", "eta_s", "done", "total", "unit",
                                                             "workers", "state", "error", "manifest")})
        now = time.time()
        if force or now - last[0] >= STATUS_EVERY_S:
            last[0] = now
            status["updated"] = now
            try:
                write_json(sp, status)
            except OSError:
                pass
            if (q / f"{rid}.cancel.json").exists():
                cancel.set()

    def rep(**kw):
        report(**kw)
        push(state="running", **kw)

    folder = shared / req["rel"]
    try:
        t0 = time.time()
        while not folder.is_dir():  # the sync may still be bringing the folder over
            if time.time() - t0 > 600:
                raise RuntimeError(f"{me} cannot find “{req['rel']}” in its copy of the shared folder.")
            rep(stage="Waiting for the experiment to arrive through the shared folder", fraction=0.0)
            if cancel.wait(5):
                raise Cancelled()
        exp = Experiment(folder)
        exp.settings = Settings.from_dict(req.get("settings") or {})
        exp.save()
        method = getattr(exp, REMOTE_JOBS[req["job"]])
        method(progress=rep, cancel=cancel, workers=workers_n)
        push(force=True, stage="Listing the results for the other Mac", fraction=1.0)
        push(force=True, state="done", stage="Done", fraction=1.0, manifest=manifest(exp.results_dir))
        return folder
    except Cancelled:
        push(force=True, state="cancelled", stage="Cancelled")
        raise
    except Exception as e:  # noqa: BLE001
        push(force=True, state="failed", stage="Failed", error=str(e))
        raise


def heartbeat(shared: Path, busy: str | None = None, cores: int | None = None) -> None:
    me = computer_name()
    write_json(queue_dir(shared, create=True) / f"worker {_safe(me)}.json",
               {"name": me, "version": _version(), "updated": time.time(), "busy": busy, "cores": cores})


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in " -_'" else "_" for c in name)[:80] or "Mac"


def tidy(shared: Path) -> None:
    """Remove requests finished more than KEEP_DAYS ago, with their status and cancel files."""
    q = queue_dir(shared)
    cutoff = time.time() - KEEP_DAYS * 86400
    for p in q.glob("*.request.json"):
        r = read_json(p) or {}
        if float(r.get("created", time.time())) < cutoff:
            rid = r.get("id") or p.name.split(".")[0]
            for f in (p, q / f"{rid}.status.json", q / f"{rid}.cancel.json"):
                try:
                    f.unlink()
                except OSError:
                    pass


class WorkerLoop(threading.Thread):
    """On the working Mac: heartbeat, and run the next request whenever nothing else is running.
    start_job(name, fn, label) runs fn(report) the same way as a job started in the window."""

    def __init__(self, busy, start_job, cancel_event, on_done=None, alive=lambda: True):
        super().__init__(daemon=True, name="remote-worker")
        self.busy, self.start_job, self.cancel, self.on_done = busy, start_job, cancel_event, on_done
        self.alive = alive
        self.stopped = False
        self.current: str | None = None
        self._beat = 0.0
        self._tidied = 0.0

    def run(self) -> None:
        from . import macos

        awake = False
        while not self.stopped and self.alive():
            try:
                pr = prefs()
                shared = shared_dir(pr["shared"]) if pr["worker"] else None
                if shared is None:
                    if awake:
                        macos.end_activity("remote-worker")
                        awake = False
                    time.sleep(10)
                    continue
                if not awake:  # keep the Mac from sleeping while it waits for work (the display may sleep)
                    awake = macos.begin_activity("Waiting for analyses from your other Macs", "remote-worker")
                now = time.time()
                if now - self._beat >= HEARTBEAT_S:
                    heartbeat(shared, self.current, os.cpu_count())
                    self._beat = now
                if now - self._tidied > 3600:
                    tidy(shared)
                    self._tidied = now
                if not self.busy():
                    self.current = None
                    req = next_request(shared, computer_name())
                    if req is not None:
                        self._start(req, shared)
            except Exception:  # noqa: BLE001 - keep listening whatever happens
                traceback.print_exc()
            time.sleep(5)
        if awake:
            macos.end_activity("remote-worker")

    def _start(self, req: dict, shared: Path) -> None:
        from .updater import load_prefs

        self.current = req["id"]
        heartbeat(shared, self.current, os.cpu_count())
        self._beat = time.time()
        label = f"{req.get('experiment') or req['rel']} (from {req.get('from', 'another Mac')})"

        def fn(report):
            folder = process(req, shared, report, self.cancel, load_prefs().get("workers") or None)
            if self.on_done:
                self.on_done(folder)

        self.start_job("remote", fn, label)
