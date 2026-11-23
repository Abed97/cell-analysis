"""Processing in two stages, both able to run in worker processes.

1. ``segment_chunk``: a few consecutive frames of one movie: load, top-hat background removal,
   nuclear segmentation, quantification, whole-cell mask (phase contrast), frame-to-frame drift.
   Chunks of every movie are spread over all cores, so even a single long movie is parallel.
2. ``track_job``: one movie: registration, tracking (gap closing, divisions, colourless
   stretches), and intensity measurements for the filled-in colourless positions.
"""

from __future__ import annotations

import io as _io
import time
import zipfile
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

from . import io_incucyte as io
from . import segment as seg
from .track import track_movie

DET_KEYS = ("frame", "label", "x", "y", "area", "red", "green", "red_med", "green_med", "edge")


def display_range(img: np.ndarray | None):
    if img is None:
        return None
    lo, hi = np.percentile(img[::4, ::4], [0.5, 99.8])
    if hi <= lo:
        hi = lo + 1
    return [float(lo), float(hi)]


def _put(zf, name, arr):
    buf = _io.BytesIO()
    np.save(buf, arr)
    zf.writestr(name, buf.getvalue())


class MovieMasks:
    """Labels and whole-cell masks of one movie, stored as chunk files of lazily loaded arrays."""

    def __init__(self, path: Path):
        self.files = {}
        path = Path(path)
        if path.is_dir():
            sources = sorted(path.glob("chunk_*.npz"))
        elif path.with_suffix(".npz").exists():  # results from before chunking
            sources = [path.with_suffix(".npz")]
        else:
            sources = []
        self._open = {}
        for src in sources:
            try:
                with zipfile.ZipFile(src) as z:
                    for name in z.namelist():
                        self.files[name[:-4]] = src
            except Exception:
                continue

    def _get(self, key):
        src = self.files.get(key)
        if src is None:
            return None
        z = self._open.get(src)
        if z is None:
            z = self._open[src] = np.load(src)
        return z[key]

    def labels(self, frame: int):
        return self._get(f"f{frame}")

    def cells(self, frame: int):
        m = self._get(f"c{frame}")
        return None if m is None else m.astype(bool)


def segment_chunk(job: dict) -> dict:
    """job: movie, frames [f...], paths {f: {channel: path}}, prev {channel: path} | None,
    settings, mask_path, conf_thr, progress_q."""
    t0 = time.time()
    s = job["settings"]
    nucleus_px = s["nucleus_px"]
    bg_r = s.get("bg_radius_px", nucleus_px)
    det = {k: [] for k in DET_KEYS}
    steps, conf = {}, {}
    ranges = {}
    shape = None
    mask_path = Path(job["mask_path"])
    mask_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = mask_path.with_suffix(".tmp")
    zf = zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1)

    def reg_image(paths):
        ph = io.read_channel(paths.get("phase"))
        if ph is not None:
            return ph
        r = seg.bg_subtract(io.read_channel(paths.get("red")), bg_r)
        g = seg.bg_subtract(io.read_channel(paths.get("green")), bg_r)
        return np.nan_to_num(r if g is None else (g if r is None else r + g))

    prev_reg = reg_image(job["prev"]) if job.get("prev") else None
    for k, f in enumerate(job["frames"]):
        paths = job["paths"][f]
        red = io.read_channel(paths.get("red"))
        green = io.read_channel(paths.get("green"))
        phase = io.read_channel(paths.get("phase"))
        if red is None and green is None:
            raise ValueError(f"Frame {f} of {job['movie'][0]} field {job['movie'][1]} has no red or green image")
        r_bg = seg.bg_subtract(red, bg_r)
        g_bg = seg.bg_subtract(green, bg_r)
        if k == 0:
            for name, im in (("red", r_bg), ("green", g_bg), ("phase", phase)):
                rr = display_range(im)
                if rr is not None:
                    ranges[name] = rr
        if s.get("method") == "cellpose" and seg.cellpose_available():
            labels = seg.segment_cellpose(r_bg, g_bg, nucleus_px)
        else:
            labels = seg.segment_classic(r_bg, g_bg, nucleus_px, s.get("sensitivity", 0.5))
        shape = labels.shape
        q = seg.quantify(labels, r_bg, g_bg, s["edge_margin_px"])
        nq = len(q["label"])
        det["frame"].append(np.full(nq, f, dtype=np.int32))
        for key in DET_KEYS[1:]:
            det[key].append(np.asarray(q[key]))
        c, _, cmask = seg.confluence(phase, nucleus_px, job.get("conf_ref"), nuclei=labels, **conf_kw(s))
        conf[f] = c
        reg = phase if phase is not None else np.nan_to_num(r_bg if g_bg is None else (g_bg if r_bg is None else r_bg + g_bg))
        if prev_reg is not None:
            steps[f] = seg.drift(prev_reg, reg)
        prev_reg = reg
        _put(zf, f"f{f}.npy", labels.astype(np.uint16 if labels.max() < 65535 else np.uint32))
        if cmask is not None:
            _put(zf, f"c{f}.npy", cmask.astype(np.uint8))
        q_ = job.get("progress_q")
        if q_ is not None:
            try:
                q_.put(1)
            except Exception:
                pass
    zf.close()
    tmp.replace(mask_path)
    cat = {k: (np.concatenate(v) if v else np.zeros(0)) for k, v in det.items()}
    return {"movie": job["movie"], "frames": list(job["frames"]), "det": cat, "steps": steps, "conf": conf,
            "ranges": ranges, "shape": list(shape) if shape else None, "seconds": time.time() - t0}


def _local_signal(img: np.ndarray, x: float, y: float, r: float) -> float:
    """Mean in a disk minus the median of a surrounding ring (local background)."""
    h, w = img.shape
    R = int(np.ceil(r * 2.6)) + 1
    xi, yi = int(round(x)), int(round(y))
    x0, x1, y0, y1 = max(0, xi - R), min(w, xi + R + 1), max(0, yi - R), min(h, yi + R + 1)
    if x0 >= x1 or y0 >= y1:
        return np.nan
    win = img[y0:y1, x0:x1]
    yy, xx = np.ogrid[y0:y1, x0:x1]
    d2 = (xx - x) ** 2 + (yy - y) ** 2
    inner = win[d2 <= r * r]
    ring = win[(d2 >= (1.6 * r) ** 2) & (d2 <= (2.6 * r) ** 2)]
    if inner.size == 0 or ring.size == 0:
        return np.nan
    return float(inner.mean() - np.median(ring))


def object_filter(mask: np.ndarray, min_px: float):
    """Split a cell mask into separate objects (colonies; touching cells are one object) and mark
    those at least min_px pixels big. Returns (labels, sizes, keep) with index 0 = background."""
    lab, n = ndi.label(mask, structure=np.ones((3, 3), bool))
    sizes = np.bincount(lab.ravel(), minlength=n + 1)
    sizes[0] = 0
    keep = sizes >= max(1.0, float(min_px))
    keep[0] = False
    return lab, sizes, keep


def measure_movie(mask_dir, frames, min_halfpx: float, halfpx_um2: float, progress_q=None) -> dict:
    """Confluence, colony number and mean colony area per frame from the stored cell masks
    (half resolution), ignoring objects smaller than the debris size."""
    masks = MovieMasks(mask_dir)
    out = {"confluence": [], "colonies": [], "colony_area": []}
    each = {}
    for f in frames:
        m = masks.cells(f)
        if m is None:
            for k in out:
                out[k].append(None)
        else:
            _, sizes, keep = object_filter(m.astype(bool), min_halfpx)
            k = int(keep.sum())
            tot = float(sizes[keep].sum())
            out["confluence"].append(tot / m.size)
            out["colonies"].append(k)
            out["colony_area"].append(tot * halfpx_um2 / k if k else None)
            each[f"f{int(f)}"] = np.round(sizes[keep] * halfpx_um2).astype(np.int32)
        if progress_q is not None:
            try:
                progress_q.put(1)
            except Exception:
                pass
    save_colony_sizes(mask_dir, each)
    return out


def save_colony_sizes(mask_dir, each: dict) -> None:
    """Area (µm²) of every colony in every frame, for box plots: colony_sizes.npz next to the masks."""
    d = Path(mask_dir)
    if not d.is_dir():
        return
    tmp = d / "colony_sizes.tmp.npz"
    np.savez_compressed(tmp, **each)
    tmp.replace(d / "colony_sizes.npz")


def load_colony_sizes(mask_dir) -> dict:
    p = Path(mask_dir) / "colony_sizes.npz"
    if not p.exists():
        return {}
    with np.load(p) as z:
        return {int(k[1:]): z[k] for k in z.files}


def conf_kw(s: dict) -> dict:
    """Confluence-mask options from the job settings."""
    c = s.get("conf") or {}
    return {"sensitivity": c.get("sens", 0.5), "bridge_px": c.get("bridge_px"), "adjust_px": c.get("adjust_px", 0.0)}


def threshold_job(job: dict):
    """Background texture (level, spread) of a field's first phase image (the middle one if the first
    is missing): used for images too confluent to show any background of their own."""
    ref = None
    for p in job["paths"]:
        img = io.read_channel(p) if p else None
        if img is not None:
            sd = seg.texture(img, job["nucleus_px"])
            level, spread, _ = seg.background_stats(sd)
            ref = (float(level), float(spread))
            break
    q_ = job.get("progress_q")
    if q_ is not None:
        try:
            q_.put(1)
        except Exception:
            pass
    return ref


def remask_job(job: dict) -> dict:
    """New confluence masks for one chunk of saved masks, from the phase images, keeping the
    nuclei. job: movie, chunk (npz path), phase {frame: path}, ref, settings, progress_q."""
    s = job["settings"]
    path = Path(job["chunk"])
    with np.load(path) as z:
        arrays = {k: z[k] for k in z.files}
    frames = sorted(int(k[1:]) for k in arrays if k.startswith("f"))
    for f in frames:
        img = io.read_channel(job["phase"].get(f)) if job["phase"].get(f) else None
        _, _, cm = seg.confluence(img, s["nucleus_px"], job.get("ref"), nuclei=arrays[f"f{f}"], **conf_kw(s))
        if cm is not None:
            arrays[f"c{f}"] = cm.astype(np.uint8)
        q_ = job.get("progress_q")
        if q_ is not None:
            try:
                q_.put(1)
            except Exception:
                pass
    tmp = path.with_suffix(".tmp")
    zf = zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1)
    for k, v in arrays.items():
        _put(zf, f"{k}.npy", v)
    zf.close()
    tmp.replace(path)
    return {"movie": job["movie"], "frames": frames}


def measure_job(job: dict) -> dict:
    s = job["settings"]
    return {"movie": job["movie"], **measure_movie(job["mask_dir"], job["frames"], s["min_object_halfpx"],
                                                     s["halfpx_um2"], job.get("progress_q"))}


def track_job(job: dict) -> dict:
    """job: movie, frames, det (arrays), steps {f: (dy, dx)}, conf {f: c}, shape, settings,
    mask_dir, paths {f: {channel: path}}, ranges."""
    t0 = time.time()
    s = job["settings"]
    nucleus_px = s["nucleus_px"]
    frames = sorted(job["frames"])
    shape = job["shape"]
    cat = job["det"]
    # cumulative drift; drop implausible jumps (failed registration)
    sh = {frames[0]: (0.0, 0.0)}
    cy = cx = 0.0
    lim = 0.25 * min(shape) if shape else 1e9
    for f in frames[1:]:
        dy, dx = job["steps"].get(f, (0.0, 0.0))
        if np.hypot(dy, dx) > lim:
            dy = dx = 0.0
        cy, cx = cy + dy, cx + dx
        sh[f] = (cy, cx)
    shy = np.array([sh[int(f)][0] for f in cat["frame"]]) if cat["frame"].size else np.zeros(0)
    shx = np.array([sh[int(f)][1] for f in cat["frame"]]) if cat["frame"].size else np.zeros(0)
    xr = cat["x"] - shx
    yr = cat["y"] - shy

    masks = MovieMasks(job["mask_dir"])
    cache = {}

    def present(t, x, y):
        m = cache.get(t)
        if m is None:
            m = masks.cells(t)
            cache[t] = m if m is not None else False
        if m is False or m is None:
            return False
        ys, xs = sh.get(t, (0.0, 0.0))
        xi, yi = int((x + xs) / 2), int((y + ys) / 2)
        if 0 <= yi < m.shape[0] and 0 <= xi < m.shape[1]:
            return bool(m[yi, xi])
        return False

    has_cells = any(k.startswith("c") for k in masks.files)
    tr = track_movie(cat["frame"].astype(np.int64), np.stack([xr, yr], axis=1) if xr.size else np.zeros((0, 2)),
                     cat["red"], cat["green"], cat["area"], s["search_radius_px"], s["max_gap_frames"],
                     s["daughter_radius_px"], nucleus_px, s.get("min_cycle_frames", 4),
                     colourless_frames=s.get("colourless_frames", 0) if has_cells else 0,
                     present=present if has_cells else None, last_frame=frames[-1])

    # measure the filled-in colourless positions (local background, like the top-hat)
    virt = tr["virtual"]
    vrows = {k: [] for k in DET_KEYS}
    vtid, vxr, vyr = [], [], []
    if virt:
        by_f = {}
        for sid, f, x, y in virt:
            by_f.setdefault(int(f), []).append((sid, x, y))
        h, w = shape
        margin = s["edge_margin_px"]
        for f, pts in sorted(by_f.items()):
            paths = job["paths"].get(f, {})
            red = io.read_channel(paths.get("red"))
            green = io.read_channel(paths.get("green"))
            ys, xs = sh.get(f, (0.0, 0.0))
            for sid, x, y in pts:
                xi, yi = x + xs, y + ys
                vrows["frame"].append(f)
                vrows["label"].append(0)
                vrows["x"].append(xi)
                vrows["y"].append(yi)
                vrows["area"].append(np.nan)
                rv = _local_signal(red, xi, yi, nucleus_px / 2) if red is not None else np.nan
                gv = _local_signal(green, xi, yi, nucleus_px / 2) if green is not None else np.nan
                vrows["red"].append(rv)
                vrows["green"].append(gv)
                vrows["red_med"].append(rv)
                vrows["green_med"].append(gv)
                vrows["edge"].append(xi < margin or yi < margin or xi > w - 1 - margin or yi > h - 1 - margin)
                vtid.append(sid)
                vxr.append(x)
                vyr.append(y)
    conf = [job["conf"].get(f) for f in frames]
    meas = {"colonies": [None] * len(frames), "colony_area": [None] * len(frames)}
    if has_cells and "min_object_halfpx" in s:
        meas = measure_movie(job["mask_dir"], frames, s["min_object_halfpx"], s["halfpx_um2"])
        conf = [m if m is not None else c for m, c in zip(meas["confluence"], conf)]
    q_ = job.get("progress_q")
    if q_ is not None:
        try:
            q_.put(1)  # one movie tracked
        except Exception:
            pass
    n_real = len(cat["frame"])
    out = {}
    for k in DET_KEYS:
        v = np.asarray(vrows[k]) if vrows[k] else np.zeros(0)
        out[k] = np.concatenate([np.asarray(cat[k]), v.astype(np.asarray(cat[k]).dtype if cat[k].size else v.dtype)])
    interp = np.concatenate([np.zeros(n_real, bool), np.ones(len(vtid), bool)])
    return {
        "movie": job["movie"],
        "det": out,
        "interp": interp,
        "x_reg": np.concatenate([xr, np.asarray(vxr, dtype=float)]).astype(np.float32),
        "y_reg": np.concatenate([yr, np.asarray(vyr, dtype=float)]).astype(np.float32),
        "track_id": np.concatenate([tr["track_id"], np.asarray(vtid, dtype=np.int64)]),
        "parent": tr["parent"],
        "low_conf": sorted(tr["low_conf"]),
        "frames": frames,
        "confluence": conf,
        "colonies": meas["colonies"],
        "colony_area": meas["colony_area"],
        "shifts": [list(sh[f]) for f in frames],
        "ranges": job["ranges"],
        "shape": shape,
        "seconds": time.time() - t0,
    }


def retrack(det_frame, x_reg, y_reg, red, green, area, s: dict, nucleus_px: float):
    return track_movie(np.asarray(det_frame, dtype=np.int64), np.stack([x_reg, y_reg], axis=1), red, green, area,
                       s["search_radius_px"], s["max_gap_frames"], s["daughter_radius_px"], nucleus_px,
                       s.get("min_cycle_frames", 4))
