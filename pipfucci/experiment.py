"""An experiment: one Incucyte export folder, its settings and its results.

Results live next to the data in "PIP-FUCCI results/": the per-cell-per-frame table,
the track table, masks, and a record of the settings and software versions used.
"""

from __future__ import annotations

import os
import platform
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
from .pipeline import process_movie, retrack
from .settings import Settings, default_conditions, load_json, save_json

SEG_KEYS = {"channel_map", "nucleus_um", "pixel_um", "sensitivity", "method"}
TRACK_KEYS = {"interval_min", "max_speed_um_h", "max_gap_h"}
CLASSIFY_KEYS = {"smoothing_h", "gate_green", "gate_red", "parental_well"}


class Cancelled(Exception):
    pass


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
            "min_cycle_frames": max(2, int(round(10.0 / s.interval_h))),
            "sensitivity": s.sensitivity,
            "method": s.method,
        }

    # ------------------------------------------------------------ run
    def run(self, progress=None, cancel: threading.Event | None = None, workers: int | None = None) -> None:
        ok, msg = self.channels_ok()
        if not ok:
            raise ValueError(msg)
        progress = progress or (lambda **kw: None)
        cancel = cancel or threading.Event()
        mf = self.movie_files()
        movies = mf.movies()
        if not movies:
            raise ValueError("No movies found with the current channel assignment.")
        sd = self.derived_numbers()
        mask_dir = self.results_dir / "masks"
        jobs = []
        for mv in movies:
            frames = {f: dict(mf.files[mv][f]) for f in mf.frames(mv)}
            jobs.append({"movie": mv, "frames": frames, "settings": sd,
                         "mask_path": str(mask_dir / (movie_key(*mv) + ".npz"))})
        total = sum(len(j["frames"]) for j in jobs)
        done_frames = 0
        t0 = time.time()
        results = []

        def report(stage, extra=0):
            frac = (done_frames + extra) / max(1, total)
            el = time.time() - t0
            eta = el / frac - el if frac > 0.02 else None
            progress(stage=stage, done=done_frames + extra, total=total, fraction=min(frac, 0.99), eta_s=eta)

        report("Segmenting and tracking")
        if workers is None:
            workers = max(1, min(4, (os.cpu_count() or 2) - 1))
        workers = min(workers, len(jobs))
        used_pool = False
        if workers > 1:
            try:
                import multiprocessing as mp

                ctx = mp.get_context("spawn")
                manager = ctx.Manager()
                q = manager.Queue()
                for j in jobs:
                    j["progress_q"] = q
                with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
                    futs = [ex.submit(process_movie, j) for j in jobs]
                    pending = set(futs)
                    extra = 0
                    while pending:
                        if cancel.is_set():
                            for f in pending:
                                f.cancel()
                            for proc in list(getattr(ex, "_processes", {}).values()):
                                try:
                                    proc.terminate()
                                except Exception:
                                    pass
                            ex.shutdown(wait=False, cancel_futures=True)
                            manager.shutdown()
                            raise Cancelled()
                        done_now = [f for f in pending if f.done()]
                        for f in done_now:
                            pending.discard(f)
                            results.append(f.result())
                        while True:
                            try:
                                q.get_nowait()
                                extra += 1
                            except Exception:
                                break
                        done_frames = min(extra, total)
                        report("Segmenting and tracking")
                        time.sleep(0.25)
                manager.shutdown()
                used_pool = True
            except Cancelled:
                raise
            except Exception:
                try:
                    manager.shutdown()
                except Exception:
                    pass
                results = []
                done_frames = 0
                used_pool = False
        if not used_pool:
            class Q:
                def put(_self, _):
                    nonlocal done_frames
                    done_frames += 1
                    report("Segmenting and tracking")
                    if cancel.is_set():
                        raise Cancelled()

            for j in jobs:
                j["progress_q"] = Q()
                results.append(process_movie(j))
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
        self.cells = cells
        self.meta = {
            "movies": movies_meta,
            "parents": parents,
            "low_conf": sorted(low_conf),
            "times_min": self.index["times_min"],
            "run": {"finished": datetime.now().isoformat(timespec="seconds"), "versions": versions(),
                    "settings": self.settings.to_dict(), "derived": self.settings.derived()},
        }

    def retrack(self) -> None:
        with self.lock:
            s = self.settings
            sd = self.derived_numbers()
            parents, low_conf = {}, set()
            for (well, fld), sub in self.cells.groupby(["well", "field"], sort=False):
                tr = retrack(sub["frame"].values, sub["x_reg"].values, sub["y_reg"].values, sub["red"].values,
                             sub["green"].values, sub["area"].values, sd, s.nucleus_px)
                pre = f"{well}-{fld}-"
                self.cells.loc[sub.index, "tid"] = tr["track_id"]
                for c, p in tr["parent"].items():
                    parents[pre + str(c)] = pre + str(p)
                low_conf.update(pre + str(c) for c in tr["low_conf"])
            self.cells["track"] = self.cells["well"] + "-" + self.cells["field"].astype(str) + "-" + self.cells["tid"].astype(str)
            self.meta["parents"] = parents
            self.meta["low_conf"] = sorted(low_conf)
            self.meta.setdefault("run", {})["settings"] = s.to_dict()
            self.meta["run"]["derived"] = s.derived()
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
            r_all = c["red"].values
            g_all = c["green"].values
            _, _, scales = ph.normalise(r_all[fm], g_all[fm])
            r = np.clip(np.nan_to_num(r_all) / scales["red"], 0, None).astype(np.float32)
            g = np.clip(np.nan_to_num(g_all) / scales["green"], 0, None).astype(np.float32)
            c["r_n"] = r
            c["g_n"] = g
            floor = None
            if self.settings.parental_well and (~fm).any():
                floor = {"red": float(np.percentile(r[~fm], 99)), "green": float(np.percentile(g[~fm], 99))}
            s = self.settings
            if s.gate_green is None:
                s.gate_green = round(ph.auto_gate(g[fm], floor["green"] if floor else None), 3)
            if s.gate_red is None:
                s.gate_red = round(ph.auto_gate(r[fm], floor["red"] if floor else None), 3)
            c["raw"] = ph.raw_calls(r, g, s.gate_red, s.gate_green)
            em = ph.emissions(r, g, s.gate_red, s.gate_green)
            smoothed = np.zeros(len(c), dtype=np.int8)
            for _, sub in c.groupby(["well", "field"], sort=False):
                idx = sub.index.values
                codes, uniq = pd.factorize(sub["tid"])
                smoothed[idx] = ph.smooth_tracks(codes.astype(np.int64), sub["frame"].values, em[idx],
                                                 s.interval_h, s.smoothing_frames)
            c["phase"] = smoothed
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
