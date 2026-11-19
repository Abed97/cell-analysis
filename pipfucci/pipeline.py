"""Per-movie processing (one well/field): load, background, segment, quantify, register, track.

``process_movie`` is a top-level function so it can run in worker processes.
"""

from __future__ import annotations

import io as _io
import time
import zipfile
from pathlib import Path

import numpy as np

from . import io_incucyte as io
from . import segment as seg
from .track import track_movie


def display_range(img: np.ndarray | None):
    if img is None:
        return None
    lo, hi = np.percentile(img[::4, ::4], [0.5, 99.8])
    if hi <= lo:
        hi = lo + 1
    return [float(lo), float(hi)]


def process_movie(job: dict) -> dict:
    """job: {movie, frames: {frame: {channel: path}}, settings: dict(derived numbers), mask_path, progress_q}"""
    t_start = time.time()
    s = job["settings"]
    nucleus_px = s["nucleus_px"]
    frames = sorted(job["frames"].keys())
    det = {k: [] for k in ("frame", "label", "x", "y", "area", "red", "green", "red_med", "green_med", "edge")}
    conf = []
    shifts = [(0.0, 0.0)]
    mask_path = Path(job["mask_path"])
    mask_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_mask = mask_path.with_suffix(".tmp")
    # masks are written frame by frame (lazy-loadable .npz) so memory stays flat on long movies
    zf = zipfile.ZipFile(tmp_mask, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1)
    conf_thr = None
    prev_reg = None
    ranges = {}
    shape = None
    cum = np.zeros(2)
    for k, f in enumerate(frames):
        paths = job["frames"][f]
        red = io.read_channel(paths.get("red"))
        green = io.read_channel(paths.get("green"))
        phase = io.read_channel(paths.get("phase"))
        if red is None and green is None:
            raise ValueError(f"Frame {f} of {job['movie']} has no red or green image")
        if k == 0 or k == len(frames) // 2:
            for name, im in (("red", red), ("green", green), ("phase", phase)):
                r = display_range(im)
                if r is not None:
                    old = ranges.get(name)
                    ranges[name] = r if old is None else [min(old[0], r[0]), max(old[1], r[1])]
        r_bg = seg.bg_subtract(red, nucleus_px)
        g_bg = seg.bg_subtract(green, nucleus_px)
        if s.get("method") == "cellpose" and seg.cellpose_available():
            labels = seg.segment_cellpose(r_bg, g_bg, nucleus_px)
        else:
            labels = seg.segment_classic(r_bg, g_bg, nucleus_px, s.get("sensitivity", 0.5))
        shape = labels.shape
        q = seg.quantify(labels, r_bg, g_bg, s["edge_margin_px"])
        nq = len(q["label"])
        det["frame"].append(np.full(nq, f, dtype=np.int32))
        for key in ("label", "x", "y", "area", "red", "green", "red_med", "green_med", "edge"):
            det[key].append(np.asarray(q[key]))
        c, conf_thr = seg.confluence(phase, nucleus_px, conf_thr)
        conf.append(c)
        reg_img = phase if phase is not None else (np.nan_to_num(r_bg) + np.nan_to_num(g_bg) if r_bg is not None and g_bg is not None else (r_bg if r_bg is not None else g_bg))
        if prev_reg is not None:
            dy, dx = seg.drift(prev_reg, reg_img)
            cum = cum + np.array([dy, dx])
            shifts.append((float(cum[0]), float(cum[1])))
        prev_reg = reg_img
        buf = _io.BytesIO()
        np.save(buf, labels.astype(np.uint16 if labels.max() < 65535 else np.uint32))
        zf.writestr(f"f{f}.npy", buf.getvalue())
        q_ = job.get("progress_q")
        if q_ is not None:
            try:
                q_.put(1)
            except Exception:
                pass

    cat = {k: (np.concatenate(v) if v else np.zeros(0)) for k, v in det.items()}
    # registration: drift is small and global; remove large jumps that are likely failures
    sh = np.array(shifts)
    if len(sh) > 1:
        steps = np.diff(sh, axis=0)
        bad = np.hypot(steps[:, 0], steps[:, 1]) > 0.5 * min(shape) if shape else np.zeros(len(steps), bool)
        steps[bad] = 0
        sh = np.vstack([[0, 0], np.cumsum(steps, axis=0)])
    fidx = {f: i for i, f in enumerate(frames)}
    if cat["frame"].size:
        rows = np.array([fidx[int(f)] for f in cat["frame"]])
        xr = cat["x"] - sh[rows, 1]
        yr = cat["y"] - sh[rows, 0]
    else:
        xr = yr = np.zeros(0)

    zf.close()
    tmp_mask.replace(mask_path)

    tr = track_movie(cat["frame"].astype(np.int64), np.stack([xr, yr], axis=1) if xr.size else np.zeros((0, 2)),
                     cat["red"], cat["green"], cat["area"], s["search_radius_px"], s["max_gap_frames"],
                     s["daughter_radius_px"], nucleus_px, s.get("min_cycle_frames", 4))
    return {
        "movie": job["movie"],
        "det": cat,
        "x_reg": xr.astype(np.float32),
        "y_reg": yr.astype(np.float32),
        "track_id": tr["track_id"],
        "parent": tr["parent"],
        "low_conf": sorted(tr["low_conf"]),
        "frames": frames,
        "confluence": conf,
        "shifts": sh.tolist(),
        "ranges": ranges,
        "shape": list(shape) if shape else None,
        "seconds": time.time() - t_start,
    }


def retrack(det_frame, x_reg, y_reg, red, green, area, s: dict, nucleus_px: float):
    return track_movie(np.asarray(det_frame, dtype=np.int64), np.stack([x_reg, y_reg], axis=1), red, green, area,
                       s["search_radius_px"], s["max_gap_frames"], s["daughter_radius_px"], nucleus_px,
                       s.get("min_cycle_frames", 4))
