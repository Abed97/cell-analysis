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
from .pipeline import segment_chunk, track_job
from .settings import Settings, default_conditions, load_json, save_json

SEG_KEYS = {"channel_map", "nucleus_um", "pixel_um", "sensitivity", "method", "bg_radius_um"}
TRACK_KEYS = {"interval_min", "max_speed_um_h", "max_gap_h", "colourless_h"}
CLASSIFY_KEYS = {"smoothing_h", "gate_green", "gate_red", "parental_well"}


CHUNK = 6  # frames per parallel job


class Cancelled(Exception):
    pass


def worker_count(n_jobs: int = 10 ** 6, setting=None) -> int:
    """Worker processes: all cores but one, capped by memory (about 1 GB each for 1408x1040 movies)."""
    if setting not in (None, "auto"):
        try:
            return max(1, min(int(setting), n_jobs))
        except Exception:
            pass
    cpus = os.cpu_count() or 2
    n = max(1, cpus - 1)
    try:
        mem_gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
        n = min(n, max(1, int(mem_gb // 1.0) - 1))
    except Exception:
        pass
    return max(1, min(n, 12, n_jobs))


class WorkerPool:
    """Process pool with per-frame progress and cancel; falls back to running in this process."""

    def __init__(self, workers: int):
        self.workers = workers
        self.ex = None
        self.manager = None

    def __enter__(self):
        if self.workers > 1:
            try:
                import multiprocessing as mp

                ctx = mp.get_context("spawn")
                self.manager = ctx.Manager()
                self.q = self.manager.Queue()
                self.ex = ProcessPoolExecutor(max_workers=self.workers, mp_context=ctx)
            except Exception:
                self.ex = None
        return self

    def __exit__(self, *exc):
        if self.ex is not None:
            self.ex.shutdown(wait=exc[0] is None, cancel_futures=True)
        if self.manager is not None:
            try:
                self.manager.shutdown()
            except Exception:
                pass
        return False

    def _kill(self):
        for proc in list(getattr(self.ex, "_processes", {}).values()):
            try:
                proc.terminate()
            except Exception:
                pass

    def map(self, fn, jobs, cancel, on_frame=None):
        if self.ex is not None:
            try:
                return self._map_pool(fn, jobs, cancel, on_frame)
            except Cancelled:
                raise
            except Exception:
                self._kill()
                self.ex.shutdown(wait=False, cancel_futures=True)
                self.ex = None
        out = []

        class Q:
            def put(_self, _):
                if on_frame:
                    on_frame()
                if cancel.is_set():
                    raise Cancelled()

        for j in jobs:
            if cancel.is_set():
                raise Cancelled()
            out.append(fn({**j, "progress_q": Q()}))
        return out

    def _map_pool(self, fn, jobs, cancel, on_frame):
        futs = [self.ex.submit(fn, {**j, "progress_q": self.q}) for j in jobs]
        results = [None] * len(futs)
        pending = dict(enumerate(futs))
        while pending:
            if cancel.is_set():
                for f in futs:
                    f.cancel()
                self._kill()
                raise Cancelled()
            for i, f in list(pending.items()):
                if f.done():
                    results[i] = f.result()
                    del pending[i]
            while True:
                try:
                    self.q.get_nowait()
                except Exception:
                    break
                if on_frame:
                    on_frame()
            time.sleep(0.2)
        return results


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
        }

    # ------------------------------------------------------------ run
    def _jobs(self):
        mf = self.movie_files()
        movies = mf.movies()
        if not movies:
            raise ValueError("No movies found with the current channel assignment.")
        sd = self.derived_numbers()
        mask_root = self.results_dir / "masks"
        chunk_jobs, movie_info = [], {}
        for mv in movies:
            frames = mf.frames(mv)
            paths = {f: dict(mf.files[mv][f]) for f in frames}
            mdir = mask_root / movie_key(*mv)
            if mdir.exists():
                shutil.rmtree(mdir, ignore_errors=True)
            old = mask_root / (movie_key(*mv) + ".npz")
            if old.exists():
                old.unlink()
            # one confluence threshold per movie, from the first and middle frames
            thr = []
            for f in (frames[0], frames[len(frames) // 2]):
                ph_img = io.read_channel(paths[f].get("phase"))
                if ph_img is not None:
                    from .segment import cell_mask

                    thr.append(cell_mask(ph_img, sd["nucleus_px"])[1])
            conf_thr = float(np.mean(thr)) if thr else None
            movie_info[mv] = {"frames": frames, "paths": paths, "mask_dir": str(mdir)}
            for i in range(0, len(frames), CHUNK):
                fr = frames[i:i + CHUNK]
                chunk_jobs.append({"movie": mv, "frames": fr, "paths": {f: paths[f] for f in fr},
                                   "prev": paths[frames[i - 1]] if i > 0 else None, "settings": sd,
                                   "mask_path": str(mdir / f"chunk_{fr[0]:05d}.npz"), "conf_thr": conf_thr})
        return chunk_jobs, movie_info, sd

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

        def report(stage, frac=None):
            if frac is None:
                frac = 0.9 * state["done"] / max(1, total)
            el = time.time() - t0
            eta = el / frac - el if frac > 0.03 else None
            progress(stage=stage, done=state["done"], total=total, fraction=min(frac, 0.99), eta_s=eta,
                     workers=workers)

        workers = workers or worker_count(len(chunk_jobs))
        report("Segmenting")
        with WorkerPool(workers) as pool:
            seg_results = pool.map(segment_chunk, chunk_jobs, cancel,
                                   on_frame=lambda: (state.__setitem__("done", state["done"] + 1), report("Segmenting")))
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
                track_jobs.append({"movie": mv, "frames": info["frames"], "det": det, "steps": steps, "conf": conf,
                                   "shape": parts[0]["shape"], "settings": sd, "mask_dir": info["mask_dir"],
                                   "paths": info["paths"], "ranges": parts[0]["ranges"]})
            report("Tracking", 0.9)
            results = pool.map(track_job, track_jobs, cancel)
        progress(stage="Calling phases", done=total, total=total, fraction=0.99, eta_s=0)
        self._assemble(results)
        self.stale = None
        self.flags = {}
        self.save_flags()
        self.classify()
        self._save_results()
        self.save()
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
                "shifts": r["shifts"], "ranges": r["ranges"], "shape": r["shape"], "seconds": r["seconds"],
            }
        cells = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        cells = cells.sort_values(["well", "field", "tid", "frame"], kind="stable").reset_index(drop=True)
        self.cells = cells
        self.meta = {
            "movies": movies_meta,
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

    def excluded_tracks(self) -> set:
        return {k for k, v in self.flags.items() if v.get("excluded")}


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
