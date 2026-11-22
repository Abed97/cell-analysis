"""An experiment: one Incucyte export folder, its settings and its results.

Results live next to the data in "PIP-FUCCI results/": the per-cell-per-frame table,
the track table, masks, and a record of the settings and software versions used.
"""

from __future__ import annotations

import os
import platform
import shutil
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import __version__
from . import io_incucyte as io
from . import phases as ph
from .pipeline import measure_job, segment_chunk, threshold_job, track_job
from .settings import Settings, default_conditions, load_json, save_json

SEG_KEYS = {"channel_map", "nucleus_um", "pixel_um", "sensitivity", "method", "bg_radius_um"}
TRACK_KEYS = {"interval_min", "max_speed_um_h", "max_gap_h", "colourless_h"}
MEASURE_KEYS = {"min_object_um2"}  # only the confluence / colony numbers: re-measured from saved masks
CLASSIFY_KEYS = {"smoothing_h", "gate_green", "gate_red", "parental_well"}


CHUNK = 6  # frames per parallel job


class Cancelled(Exception):
    pass


def worker_count(n_jobs: int = 10 ** 6, setting=None) -> int:
    """Worker processes: all cores but one, capped by memory (about 1.5 GB each for 1408x1040 movies)."""
    if setting not in (None, "auto"):
        try:
            return max(1, min(int(setting), n_jobs))
        except Exception:
            pass
    cpus = os.cpu_count() or 2
    n = max(1, cpus - 1)
    try:
        mem_gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
        n = min(n, max(1, int((mem_gb - 2.0) // 1.5)))
    except Exception:
        pass
    return max(1, min(n, 12, n_jobs))


def _worker_init():
    """In each worker process: keep macOS from slowing it down (App Nap) while it works."""
    try:
        from .macos import begin_activity

        begin_activity("Analysing images")
    except Exception:
        pass


class WorkerPool:
    """Process pool with per-frame progress and cancel. If a worker dies (for example the Mac ran
    out of memory), the unfinished jobs are retried with fewer workers, then in this process."""

    def __init__(self, workers: int):
        self.workers = workers
        self.ex = None
        self.manager = None
        self.q = None

    def _start(self, n):
        import multiprocessing as mp

        ctx = mp.get_context("spawn")
        if self.manager is None:
            self.manager = ctx.Manager()
            self.q = self.manager.Queue()
        self.ex = ProcessPoolExecutor(max_workers=n, mp_context=ctx, initializer=_worker_init)
        self.workers = n

    def __enter__(self):
        if self.workers > 1:
            try:
                self._start(self.workers)
            except Exception:
                self.ex = None
        return self

    def __exit__(self, *exc):
        if self.ex is not None:
            if exc[0] is not None:
                self._kill()
            self.ex.shutdown(wait=exc[0] is None, cancel_futures=True)
        if self.manager is not None:
            try:
                self.manager.shutdown()
            except Exception:
                pass
        return False

    def _kill(self):
        for proc in list((getattr(self.ex, "_processes", None) or {}).values()):
            try:
                proc.terminate()
            except Exception:
                pass

    def map(self, fn, jobs, cancel, on_frame=None):
        jobs = list(jobs)
        results = [None] * len(jobs)
        todo = list(range(len(jobs)))
        while self.ex is not None and todo:
            try:
                futs = {i: self.ex.submit(fn, {**jobs[i], "progress_q": self.q}) for i in todo}
            except Exception:  # the pool cannot take jobs: run them here
                self._kill()
                self.ex.shutdown(wait=False, cancel_futures=True)
                self.ex = None
                break
            todo = self._wait(futs, results, cancel, on_frame)
            if todo:  # a worker died: retry the rest with fewer workers
                self._kill()
                self.ex.shutdown(wait=False, cancel_futures=True)
                self.ex = None
                n = self.workers // 2
                if n >= 2:
                    try:
                        self._start(n)
                    except Exception:
                        self.ex = None

        class Q:
            def put(_self, _):
                if on_frame:
                    on_frame()
                if cancel.is_set():
                    raise Cancelled()

        for i in todo:
            if cancel.is_set():
                raise Cancelled()
            results[i] = fn({**jobs[i], "progress_q": Q()})
        return results

    def _wait(self, futs, results, cancel, on_frame):
        """Collects results; returns the jobs lost because a worker process died. An error inside
        a job (a broken image, say) stops everything and is reported."""
        from concurrent.futures.process import BrokenProcessPool

        failed = []
        while futs:
            if cancel.is_set():
                for f in futs.values():
                    f.cancel()
                self._kill()
                raise Cancelled()
            for i, f in list(futs.items()):
                if f.done():
                    del futs[i]
                    try:
                        results[i] = f.result()
                    except BrokenProcessPool:
                        failed.append(i)
                    except BaseException:
                        for g in futs.values():
                            g.cancel()
                        self._kill()
                        raise
            while True:
                try:
                    self.q.get_nowait()
                except Exception:
                    break
                if on_frame:
                    on_frame()
            time.sleep(0.2)
        return failed


def versions() -> dict:
    out = {"pip-fucci": __version__, "python": platform.python_version(), "platform": platform.platform()}
    for mod in ("numpy", "scipy", "skimage", "pandas", "tifffile", "cellpose"):
        try:
            m = __import__(mod)
            out[mod] = getattr(m, "__version__", "?")
        except Exception:
            pass
    return out


def movie_key(well: str, field: int) -> str:
    return f"{well}_{field}"


class Experiment:
    def __init__(self, folder: str | Path):
        self.folder = Path(folder).expanduser().resolve()
        self.results_dir = self.folder / io.RESULTS_DIRNAME
        self.index = io.scan_folder(self.folder)
        self.lock = threading.RLock()
        saved = load_json(self.results_dir / "experiment.json", None)
        if saved:
            self.settings = Settings.from_dict(saved.get("settings", {}))
            self.stale = saved.get("stale")
        else:
            self.settings = Settings()
            if self.index.get("suggested_interval_min"):
                self.settings.interval_min = round(float(self.index["suggested_interval_min"]), 2)
            self.settings.conditions = default_conditions()
            self.settings.experiment_name = self.folder.name
            self.stale = None
        # make sure every channel group has a mapping
        guessed = io.default_channel_map(self.index)
        for k, v in guessed.items():
            self.settings.channel_map.setdefault(k, v)
        self.settings.channel_map = {k: v for k, v in self.settings.channel_map.items() if k in guessed}
        self.cells: pd.DataFrame | None = None
        self.tracks: pd.DataFrame | None = None
        self.meta: dict = {}
        self.flags: dict = load_json(self.results_dir / "track_flags.json", {}) or {}
        self._load_results()

    # ------------------------------------------------------------ persistence
    def save(self) -> None:
        save_json(self.results_dir / "experiment.json", {"settings": self.settings.to_dict(), "stale": self.stale,
                                                         "saved": datetime.now().isoformat(timespec="seconds")})

    def _load_results(self) -> None:
        cp = self.results_dir / "cells.pkl"
        tp = self.results_dir / "tracks.pkl"
        if cp.exists() and tp.exists():
            try:
                self.cells = pd.read_pickle(cp)
                self.tracks = pd.read_pickle(tp)
                self.meta = load_json(self.results_dir / "meta.json", {}) or {}
            except Exception:
                self.cells = self.tracks = None
                self.meta = {}

    def _save_results(self) -> None:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.cells.to_pickle(self.results_dir / "cells.pkl")
        self.tracks.to_pickle(self.results_dir / "tracks.pkl")
        save_json(self.results_dir / "meta.json", self.meta)

    def save_flags(self) -> None:
        save_json(self.results_dir / "track_flags.json", self.flags)

    @property
    def has_results(self) -> bool:
        return self.cells is not None and self.tracks is not None

    # ------------------------------------------------------------ info
    def channels_ok(self) -> tuple[bool, str]:
        vals = set(self.settings.channel_map.values())
        if "rgb" in vals:
            return True, ""
        missing = [c for c in ("red", "green") if c not in vals]
        if missing:
            return False, "Assign a " + " and a ".join(missing) + " channel before running."
        return True, ""

    def movie_files(self) -> io.MovieFiles:
        return io.MovieFiles(self.index, self.settings.channel_map)

    def summary(self) -> dict:
        idx = self.index
        fields_per_well = sorted({len(v) for v in idx["fields"].values()}) if idx["fields"] else []
        return {
            "folder": str(self.folder),
            "name": self.settings.experiment_name or self.folder.name,
            "n_files": idx["n_files"],
            "wells": idx["wells"],
            "fields": idx["fields"],
            "fields_per_well": fields_per_well,
            "n_frames": idx["n_frames"],
            "groups": idx["groups"],
            "image": idx["image"],
            "issues": idx["issues"],
            "unparsed_examples": idx["unparsed_examples"],
            "suggested_interval_min": idx["suggested_interval_min"],
        }

    # ------------------------------------------------------------ settings
    def update_settings(self, d: dict) -> dict:
        with self.lock:
            before = self.settings.to_dict()
            self.settings.update(d)
            after = self.settings.to_dict()
            changed = {k for k in after if before.get(k) != after.get(k)}
            action = None
            if self.has_results:
                if changed & SEG_KEYS:
                    self.stale = "segmentation"
                elif changed & TRACK_KEYS and self.stale != "segmentation":
                    self.stale = "tracking"
                if changed & CLASSIFY_KEYS and self.stale is None:
                    self.classify()
                    self._save_results()
                    action = "reclassified"
                elif changed & MEASURE_KEYS and self.stale is None:
                    action = "remeasure"
            self.save()
            return {"changed": sorted(changed), "stale": self.stale, "action": action}

    def derived_numbers(self) -> dict:
        s = self.settings
        return {
            "nucleus_px": s.nucleus_px,
            "search_radius_px": s.search_radius_px,
            "daughter_radius_px": s.daughter_radius_px,
            "max_gap_frames": s.max_gap_frames,
            "edge_margin_px": s.edge_margin_px,
            "bg_radius_px": s.bg_radius_px,
            "colourless_frames": s.colourless_frames,
            "min_cycle_frames": max(2, int(round(10.0 / s.interval_h))),
            "sensitivity": s.sensitivity,
            "method": s.method,
            "min_object_halfpx": s.min_object_um2 / (2 * s.pixel_um) ** 2,  # cell masks are stored at half size
            "halfpx_um2": (2 * s.pixel_um) ** 2,
        }

    # ------------------------------------------------------------ run
    def _jobs(self):
        """Chunk jobs for segmentation. Masks of a new run go to masks.new and replace the old ones
        only when the run has finished, so a cancelled re-run leaves the previous results intact."""
        mf = self.movie_files()
        movies = mf.movies()
        if not movies:
            raise ValueError("No movies found with the current channel assignment.")
        sd = self.derived_numbers()
        mask_root = self.results_dir / "masks.new"
        if mask_root.exists():
            shutil.rmtree(mask_root, ignore_errors=True)
        chunk_jobs, movie_info = [], {}
        for mv in movies:
            frames = mf.frames(mv)
            paths = {f: dict(mf.files[mv][f]) for f in frames}
            mdir = mask_root / movie_key(*mv)
            movie_info[mv] = {"frames": frames, "paths": paths, "mask_dir": str(mdir),
                              "thr_paths": [paths[f].get("phase") for f in (frames[0], frames[len(frames) // 2])]}
            for i in range(0, len(frames), CHUNK):
                fr = frames[i:i + CHUNK]
                chunk_jobs.append({"movie": mv, "frames": fr, "paths": {f: paths[f] for f in fr},
                                   "prev": paths[frames[i - 1]] if i > 0 else None, "settings": sd,
                                   "mask_path": str(mdir / f"chunk_{fr[0]:05d}.npz"), "conf_thr": None})
        return chunk_jobs, movie_info, sd

    def _swap_masks(self) -> None:
        new, cur, old = (self.results_dir / n for n in ("masks.new", "masks", "masks.old"))
        if not new.exists():
            return
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
        if cur.exists():
            cur.rename(old)
        new.rename(cur)
        shutil.rmtree(old, ignore_errors=True)

    def _discard_new_masks(self) -> None:
        shutil.rmtree(self.results_dir / "masks.new", ignore_errors=True)

    def run(self, progress=None, cancel: threading.Event | None = None, workers: int | None = None) -> None:
        ok, msg = self.channels_ok()
        if not ok:
            raise ValueError(msg)
        progress = progress or (lambda **kw: None)
        cancel = cancel or threading.Event()
        chunk_jobs, movie_info, sd = self._jobs()
        total = sum(len(j["frames"]) for j in chunk_jobs)
        t0 = time.time()
        state = {"done": 0}

        def report(stage):
            seg_frac = state["done"] / max(1, total)
            el = time.time() - t0
            # segmentation is ~90% of the work; tracking the rest
            eta = (el / seg_frac - el) / 0.9 if seg_frac > 0.02 else None
            progress(stage=stage, done=state["done"], total=total, unit="frames",
                     fraction=min(0.02 + 0.88 * seg_frac, 0.99), eta_s=eta, workers=workers)

        workers = workers or worker_count(len(chunk_jobs))
        try:
            with WorkerPool(workers) as pool:
                # one confluence threshold per field, from its first and middle frames
                n_mov = len(movie_info)
                prep = {"n": 0}

                def prepared():
                    prep["n"] += 1
                    progress(stage="Preparing", done=prep["n"], total=n_mov, unit="fields",
                             fraction=0.02 * prep["n"] / n_mov, eta_s=None, workers=workers)

                progress(stage="Preparing", done=0, total=n_mov, unit="fields", fraction=0.0, eta_s=None,
                         workers=workers)
                mvs = list(movie_info)
                thrs = pool.map(threshold_job, [{"movie": mv, "paths": movie_info[mv]["thr_paths"],
                                                 "nucleus_px": sd["nucleus_px"]} for mv in mvs], cancel,
                                on_frame=prepared)
                thr_of = dict(zip(mvs, thrs))
                for j in chunk_jobs:
                    j["conf_thr"] = thr_of.get(j["movie"])
                t0 = time.time()
                report("Segmenting")
                seg_results = pool.map(segment_chunk, chunk_jobs, cancel,
                                       on_frame=lambda: (state.__setitem__("done", state["done"] + 1),
                                                         report("Segmenting")))
                by_movie = {}
                for r in seg_results:
                    by_movie.setdefault(r["movie"], []).append(r)
                track_jobs = []
                for mv, parts in by_movie.items():
                    parts.sort(key=lambda r: r["frames"][0])
                    det = {k: np.concatenate([p["det"][k] for p in parts]) for k in parts[0]["det"]}
                    steps, conf = {}, {}
                    for p in parts:
                        steps.update(p["steps"])
                        conf.update(p["conf"])
                    info = movie_info[mv]
                    track_jobs.append({"movie": mv, "frames": info["frames"], "det": det, "steps": steps,
                                       "conf": conf, "shape": parts[0]["shape"], "settings": sd,
                                       "mask_dir": info["mask_dir"], "paths": info["paths"],
                                       "ranges": parts[0]["ranges"]})
                state["movies"] = 0
                n_tr = max(1, len(track_jobs))

                def tracked():
                    state["movies"] += 1
                    progress(stage="Tracking", done=state["movies"], total=n_tr, unit="fields",
                             fraction=0.9 + 0.08 * state["movies"] / n_tr, eta_s=None, workers=workers)

                progress(stage="Tracking", done=0, total=n_tr, unit="fields", fraction=0.9, eta_s=None,
                         workers=workers)
                results = pool.map(track_job, track_jobs, cancel, on_frame=tracked)
            progress(stage="Calling phases", done=total, total=total, unit="frames", fraction=0.99, eta_s=0)
            self._assemble(results)
            self.stale = None
            self.flags = {}
            self.save_flags()
            self.classify()
            self._swap_masks()
            self._save_results()
            self.save()
        except BaseException:
            self._discard_new_masks()
            raise
        progress(stage="Done", done=total, total=total, fraction=1.0, eta_s=0)

    def remeasure(self, progress=None, cancel: threading.Event | None = None, workers: int | None = None) -> None:
        """Confluence and colonies again from the saved cell masks (after the debris size changed)."""
        progress = progress or (lambda **kw: None)
        cancel = cancel or threading.Event()
        with self.lock:
            sd = self.derived_numbers()
            jobs = [{"movie": k, "frames": m["frames"], "settings": sd,
                     "mask_dir": str(self.results_dir / "masks" / k)} for k, m in self.meta.get("movies", {}).items()]
        total = sum(len(j["frames"]) for j in jobs)
        state = {"done": 0}

        def tick():
            state["done"] += 1
            progress(stage="Measuring confluence", done=state["done"], total=total, unit="frames",
                     fraction=min(0.99, state["done"] / max(1, total)), eta_s=None)

        progress(stage="Measuring confluence", done=0, total=total, unit="frames", fraction=0.0, eta_s=None)
        workers = workers or worker_count(len(jobs))
        with WorkerPool(workers if total > 400 else 1) as pool:
            res = pool.map(measure_job, jobs, cancel, on_frame=tick)
        with self.lock:
            for r in res:
                m = self.meta["movies"].get(r["movie"])
                if m is None:
                    continue
                old = m.get("confluence") or [None] * len(r["confluence"])
                m["confluence"] = [a if a is not None else b for a, b in zip(r["confluence"], old)]
                m["colonies"] = r["colonies"]
                m["colony_area"] = r["colony_area"]
            self.meta["measured_min_object_um2"] = self.settings.min_object_um2
            self.meta["colony_sizes"] = 1
            save_json(self.results_dir / "meta.json", self.meta)
        progress(stage="Done", done=total, total=total, fraction=1.0, eta_s=0)

    def _assemble(self, results: list[dict]) -> None:
        parts = []
        movies_meta = {}
        parents = {}
        low_conf = set()
        for r in sorted(results, key=lambda r: (r["movie"][0][0], int(r["movie"][0][1:]), r["movie"][1])):
            well, fld = r["movie"]
            d = r["det"]
            n = len(d["frame"])
            df = pd.DataFrame({
                "well": np.full(n, well), "field": np.full(n, fld, dtype=np.int16),
                "frame": d["frame"].astype(np.int32), "label": d["label"].astype(np.int32),
                "x": d["x"].astype(np.float32), "y": d["y"].astype(np.float32),
                "x_reg": r["x_reg"], "y_reg": r["y_reg"], "area": d["area"].astype(np.float32),
                "red": d["red"].astype(np.float32), "green": d["green"].astype(np.float32),
                "red_med": d["red_med"].astype(np.float32), "green_med": d["green_med"].astype(np.float32),
                "edge": d["edge"].astype(bool), "tid": r["track_id"].astype(np.int64),
                "interp": np.asarray(r.get("interp", np.zeros(n, bool)), dtype=bool),
            })
            pre = f"{well}-{fld}-"
            df["track"] = pre + df["tid"].astype(str)
            parts.append(df)
            for c, p in r["parent"].items():
                parents[pre + str(c)] = pre + str(p)
            low_conf.update(pre + str(c) for c in r["low_conf"])
            movies_meta[movie_key(well, fld)] = {
                "well": well, "field": int(fld), "frames": [int(f) for f in r["frames"]],
                "confluence": [None if (c is None or not np.isfinite(c)) else float(c) for c in r["confluence"]],
                "colonies": [None if c is None else int(c) for c in r.get("colonies") or [None] * len(r["frames"])],
                "colony_area": [None if (c is None or not np.isfinite(c)) else round(float(c), 1)
                                for c in r.get("colony_area") or [None] * len(r["frames"])],
                "shifts": r["shifts"], "ranges": r["ranges"], "shape": r["shape"], "seconds": r["seconds"],
            }
        cells = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        cells = cells.sort_values(["well", "field", "tid", "frame"], kind="stable").reset_index(drop=True)
        self.cells = cells
        self.meta = {
            "movies": movies_meta,
            "measured_min_object_um2": self.settings.min_object_um2,
            "colony_sizes": 1,
            "parents": parents,
            "low_conf": sorted(low_conf),
            "times_min": self.index["times_min"],
            "run": {"finished": datetime.now().isoformat(timespec="seconds"), "versions": versions(),
                    "settings": self.settings.to_dict(), "derived": self.settings.derived()},
        }

    def retrack(self, progress=None) -> None:
        """Re-link tracks from the saved detections (segmentation is kept)."""
        with self.lock:
            sd = self.derived_numbers()
            mf = self.movie_files()
            real = self.cells[~self.cells.get("interp", pd.Series(False, index=self.cells.index))]
            jobs = []
            for (well, fld), sub in real.groupby(["well", "field"], sort=False):
                m = self.meta["movies"][movie_key(well, fld)]
                frames = m["frames"]
                cum = {f: tuple(v) for f, v in zip(frames, m["shifts"])}
                steps = {}
                for a, b in zip(frames[:-1], frames[1:]):
                    steps[b] = (cum[b][0] - cum[a][0], cum[b][1] - cum[a][1])
                sub = sub.sort_values("frame", kind="stable")
                det = {k: sub[k].values for k in ("frame", "label", "x", "y", "area", "red", "green", "red_med",
                                                   "green_med", "edge")}
                mv = (well, int(fld))
                jobs.append({"movie": (well, int(fld)), "frames": frames, "det": det, "steps": steps,
                             "conf": dict(zip(frames, m["confluence"])), "shape": m["shape"], "settings": sd,
                             "mask_dir": str(self.results_dir / "masks" / movie_key(*mv)),
                             "paths": {f: dict(mf.files[mv][f]) for f in mf.frames(mv)}, "ranges": m["ranges"]})
            results = [track_job(j) for j in jobs]
            keep_meta = self.meta
            self._assemble(results)
            self.meta["run"] = keep_meta.get("run", self.meta["run"])
            self.meta["run"]["settings"] = self.settings.to_dict()
            self.meta["run"]["derived"] = self.settings.derived()
            self.stale = None
            self.flags = {}
            self.save_flags()
            self.classify()
            self._save_results()
            self.save()

    # ------------------------------------------------------------ phases
    def fucci_mask(self) -> np.ndarray:
        if self.settings.parental_well:
            return (self.cells["well"] != self.settings.parental_well).values
        return np.ones(len(self.cells), bool)

    def classify(self) -> None:
        with self.lock:
            c = self.cells
            if c is None or len(c) == 0:
                return
            fm = self.fucci_mask()
            interp = c["interp"].values if "interp" in c else np.zeros(len(c), bool)
            fm_real = fm & ~interp  # filled-in colourless positions don't set the scales or gates
            r_all = c["red"].values
            g_all = c["green"].values
            _, _, scales = ph.normalise(r_all[fm_real], g_all[fm_real])
            r = np.clip(np.nan_to_num(r_all) / scales["red"], 0, None).astype(np.float32)
            g = np.clip(np.nan_to_num(g_all) / scales["green"], 0, None).astype(np.float32)
            c["r_n"] = r
            c["g_n"] = g
            floor = None
            if self.settings.parental_well and (~fm).any():
                floor = {"red": float(np.percentile(r[~fm], 99)), "green": float(np.percentile(g[~fm], 99))}
            s = self.settings
            if s.gate_green is None:
                s.gate_green = round(ph.auto_gate(g[fm_real], floor["green"] if floor else None), 3)
            if s.gate_red is None:
                s.gate_red = round(ph.auto_gate(r[fm_real], floor["red"] if floor else None), 3)
            c["raw"] = ph.raw_calls(r, g, s.gate_red, s.gate_green)
            em = ph.emissions(r, g, s.gate_red, s.gate_green)
            smoothed = np.zeros(len(c), dtype=np.int8)
            for _, sub in c.groupby(["well", "field"], sort=False):
                idx = sub.index.values
                codes, uniq = pd.factorize(sub["tid"])
                smoothed[idx] = ph.smooth_tracks(codes.astype(np.int64), sub["frame"].values, em[idx],
                                                 s.interval_h, s.smoothing_frames)
            c["phase"] = smoothed
            # what the cell looks like at that frame: its phase, or Colourless when neither reporter is on
            c["call"] = np.where(c["raw"].values == 3, 3, smoothed).astype(np.int8)
            self.meta["scales"] = scales
            self.meta["floor"] = floor
            self._build_tracks()
            self.save()

    def _build_tracks(self) -> None:
        c = self.cells
        grp = c.groupby("track", sort=False)
        first = grp.head(1).set_index("track")
        agg = grp.agg(well=("well", "first"), field=("field", "first"), tid=("tid", "first"),
                      start=("frame", "min"), end=("frame", "max"), n=("frame", "size"))
        c_sorted = c.sort_values(["track", "frame"])
        firsts = c_sorted.groupby("track").head(1).set_index("track")
        lasts = c_sorted.groupby("track").tail(1).set_index("track")
        t = agg
        t["start_edge"] = firsts["edge"].reindex(t.index).values
        t["end_edge"] = lasts["edge"].reindex(t.index).values
        parents = self.meta.get("parents", {})
        t["parent"] = [parents.get(k, "") for k in t.index]
        children = {}
        for ch_, p in parents.items():
            children.setdefault(p, []).append(ch_)
        t["n_children"] = [len(children.get(k, [])) for k in t.index]
        low = set(self.meta.get("low_conf", []))
        t["low_conf"] = [k in low for k in t.index]
        first_frame = {}
        last_frame = {}
        for mk, m in self.meta.get("movies", {}).items():
            first_frame[(m["well"], m["field"])] = min(m["frames"]) if m["frames"] else 0
            last_frame[(m["well"], m["field"])] = max(m["frames"]) if m["frames"] else 0
        t["founder"] = [st == first_frame.get((w, int(f)), 0) for st, w, f in zip(t["start"], t["well"], t["field"])]
        t["at_end"] = [en == last_frame.get((w, int(f)), 0) for en, w, f in zip(t["end"], t["well"], t["field"])]
        t["divided"] = t["n_children"] > 0
        t["start_known"] = (t["parent"] != "") & ~t["start_edge"]
        t["end_known"] = t["divided"]
        fate = np.where(t["divided"], "Divided",
                        np.where(t["at_end"], "Present at end",
                                 np.where(t["end_edge"], "Left field", "Lost")))
        t["fate"] = fate
        cond = {w: (self.settings.condition_of(w) or {}).get("name", "Unassigned") for w in t["well"].unique()}
        t["condition"] = t["well"].map(cond)
        self.tracks = t
        del first

    # ------------------------------------------------------------ helpers
    def time_h(self, frame: int) -> float:
        times = self.index["times_min"]
        if 0 <= frame < len(times) and times:
            return (times[frame] - times[0]) / 60.0
        return frame * self.settings.interval_h

    def frame_hours(self) -> list[float]:
        times = self.index["times_min"]
        if not times:
            return []
        return [(t - times[0]) / 60.0 for t in times]

    def track_flag(self, key: str, **kw) -> dict:
        f = self.flags.get(key, {})
        f.update({k: bool(v) for k, v in kw.items()})
        self.flags[key] = f
        self.save_flags()
        return f

    def excluded_wells(self) -> set:
        """Wells left out of results: listed whole, or with every field excluded."""
        ex = set(self.settings.excluded)
        out = {x for x in ex if "_" not in x}
        fields: dict = {}
        for m in self.meta.get("movies", {}).values() if self.meta else []:
            fields.setdefault(m["well"], []).append(f"{m['well']}_{m['field']}")
        for w, fs in fields.items():
            if fs and all(f in ex for f in fs):
                out.add(w)
        return out

    def movie_excluded(self, well: str, field) -> bool:
        ex = self.settings.excluded
        return well in ex or f"{well}_{int(field)}" in ex or well in self.excluded_wells()

    def tracks_in_excluded(self) -> set:
        """Tracks in wells or fields marked as excluded (blurry images), cached per settings."""
        key = (tuple(self.settings.excluded), id(self.tracks), len(self.tracks) if self.tracks is not None else 0)
        if getattr(self, "_ex_key", None) == key:
            return self._ex_set
        out = set()
        if self.tracks is not None and len(self.tracks) and self.settings.excluded:
            t = self.tracks
            wells = self.excluded_wells()
            fld = t["well"].astype(str) + "_" + t["field"].astype(int).astype(str)
            out = set(t.index[t["well"].isin(wells) | fld.isin(set(self.settings.excluded))])
        self._ex_key, self._ex_set = key, out
        return out

    def excluded_tracks(self) -> set:
        """Tracks left out of results: flagged one by one, or in an excluded well or field."""
        flagged = {k for k, v in self.flags.items() if v.get("excluded")}
        ex = self.tracks_in_excluded()
        return flagged | ex if ex else flagged


def run_info(exp: Experiment) -> dict:
    return {
        "experiment": exp.settings.experiment_name,
        "folder": str(exp.folder),
        "exported": datetime.now().isoformat(timespec="seconds"),
        "settings": exp.settings.to_dict(),
        "derived": exp.settings.derived(),
        "intensity_scales": exp.meta.get("scales"),
        "parental_floor": exp.meta.get("floor"),
        "analysis_run": exp.meta.get("run"),
        "versions_now": versions(),
    }
