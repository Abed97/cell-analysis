"""Image rendering for the viewer: channel composites, mask outlines coloured by phase, montages."""

from __future__ import annotations

import io as _io
from collections import OrderedDict
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

from . import io_incucyte as io

PHASE_RGB = {0: (63, 191, 106), 1: (229, 72, 110), 2: (242, 201, 76), 3: (140, 148, 158)}


class _LRU(OrderedDict):
    def __init__(self, n):
        super().__init__()
        self.n = n

    def get_or(self, key, fn):
        if key in self:
            self.move_to_end(key)
            return self[key]
        v = fn()
        self[key] = v
        if len(self) > self.n:
            self.popitem(last=False)
        return v


_masks = _LRU(2)
_images = _LRU(48)


def masks_for(exp, well, field):
    path = Path(exp.results_dir) / "masks" / f"{well}_{field}.npz"

    def load():
        if not path.exists():
            return None
        return np.load(path)  # lazy: one member per frame ("f12")

    return _masks.get_or((str(path), path.stat().st_mtime if path.exists() else 0), load)


def channel(exp, well, field, frame, ch):
    mf = exp.movie_files()
    p = mf.path((well, field), frame, ch)
    return _images.get_or((p, ch), lambda: io.read_channel(p)) if p else None


def _stretch(img, rng):
    lo, hi = rng
    return np.clip((img - lo) / (hi - lo), 0, 1)


def composite(exp, well: str, field: int, frame: int, layers: set, select_track: str | None = None,
              max_width: int = 1600) -> np.ndarray:
    meta = exp.meta.get("movies", {}).get(f"{well}_{field}", {})
    ranges = meta.get("ranges", {})
    imgs = {c: channel(exp, well, field, frame, c) for c in ("phase", "red", "green")}
    shape = next((im.shape for im in imgs.values() if im is not None), (512, 512))
    rgb = np.zeros(shape + (3,), dtype=np.float32)
    if "phase" in layers and imgs["phase"] is not None:
        rng = ranges.get("phase") or [float(np.percentile(imgs["phase"], 0.5)), float(np.percentile(imgs["phase"], 99.8))]
        p = _stretch(imgs["phase"], rng) * 0.75
        rgb += p[..., None]
    if "red" in layers and imgs["red"] is not None:
        rng = ranges.get("red") or [float(np.percentile(imgs["red"], 0.5)), float(np.percentile(imgs["red"], 99.8))]
        rgb[..., 0] += _stretch(imgs["red"], rng)
        rgb[..., 2] += _stretch(imgs["red"], rng) * 0.25
    if "green" in layers and imgs["green"] is not None:
        rng = ranges.get("green") or [float(np.percentile(imgs["green"], 0.5)), float(np.percentile(imgs["green"], 99.8))]
        rgb[..., 1] += _stretch(imgs["green"], rng)
    rgb = np.clip(rgb, 0, 1)
    if ("masks" in layers or select_track) and exp.has_results:
        m = masks_for(exp, well, field)
        if m is not None:
            if f"f{frame}" in m.files:
                lab = m[f"f{frame}"].astype(np.int32)
                c = exp.cells
                sub = c[(c["well"] == well) & (c["field"] == field) & (c["frame"] == frame)]
                lut = np.zeros((int(lab.max()) + 1, 3), dtype=np.float32)
                sel_lut = np.zeros(int(lab.max()) + 1, bool)
                ex = exp.excluded_tracks()
                for lbl, phs, trk in zip(sub["label"].values, sub["phase"].values, sub["track"].values):
                    if lbl <= lab.max():
                        col = PHASE_RGB[3] if trk in ex else PHASE_RGB[int(phs)]
                        lut[lbl] = np.array(col) / 255.0
                        if select_track and trk == select_track:
                            sel_lut[lbl] = True
                er = ndi.grey_erosion(lab, size=3)
                edge = (lab != er) & (lab > 0)
                if "masks" in layers:
                    rgb[edge] = lut[lab[edge]]
                if select_track and sel_lut.any():
                    sel = sel_lut[lab]
                    ring = ndi.binary_dilation(sel, iterations=3) & ~ndi.binary_dilation(sel, iterations=1)
                    rgb[ring] = 1.0
    if rgb.shape[1] > max_width:
        f = max_width / rgb.shape[1]
        rgb = ndi.zoom(rgb, (f, f, 1), order=1)
    return (rgb * 255).astype(np.uint8)


def encode(arr: np.ndarray, fmt: str = "JPEG", quality: int = 90) -> bytes:
    from PIL import Image

    buf = _io.BytesIO()
    im = Image.fromarray(arr)
    if fmt == "JPEG":
        im.save(buf, format="JPEG", quality=quality)
    else:
        im.save(buf, format="PNG", compress_level=3)
    return buf.getvalue()


def montage_picks(exp, key: str, n: int = 8):
    c = exp.cells
    sub = c[c["track"] == key].sort_values("frame")
    if len(sub) == 0:
        return sub, []
    pick = np.unique(np.linspace(0, len(sub) - 1, num=min(n, len(sub))).round().astype(int))
    rows = sub.iloc[pick]
    return rows, [int(f) for f in rows["frame"].values]


def montage(exp, key: str, n: int = 8, tile: int = 72):
    """Rows: red, green, phase, merge; columns: n timepoints along the track."""
    t = exp.tracks.loc[key]
    rows, _ = montage_picks(exp, key, n)
    if len(rows) == 0:
        return np.zeros((tile, tile, 3), np.uint8), []
    well, field = t["well"], int(t["field"])
    meta = exp.meta.get("movies", {}).get(f"{well}_{field}", {})
    ranges = meta.get("ranges", {})
    half = int(max(12, exp.settings.nucleus_px * 1.6))
    out = np.zeros((4 * tile + 3 * 2, len(rows) * tile + (len(rows) - 1) * 2, 3), dtype=np.uint8)
    out[:] = 18
    from PIL import Image

    for j, (_, r) in enumerate(rows.iterrows()):
        cx, cy, fr = int(round(r["x"])), int(round(r["y"])), int(r["frame"])
        tiles = {}
        for ch in ("red", "green", "phase"):
            im = channel(exp, well, field, fr, ch)
            if im is None:
                tiles[ch] = np.zeros((2 * half, 2 * half))
                continue
            pad = np.pad(im, half, mode="constant", constant_values=float(np.percentile(im, 1)))
            crop = pad[cy:cy + 2 * half, cx:cx + 2 * half]
            rng = ranges.get(ch) or [float(np.percentile(im, 0.5)), float(np.percentile(im, 99.8))]
            tiles[ch] = _stretch(crop, rng)
        z = np.zeros((2 * half, 2 * half))
        red_rgb = np.stack([tiles["red"], z, tiles["red"] * 0.25], -1)
        green_rgb = np.stack([z, tiles["green"], z], -1)
        phase_rgb = np.repeat(tiles["phase"][..., None], 3, -1)
        merge = np.clip(np.stack([tiles["red"], tiles["green"], tiles["red"] * 0.25], -1), 0, 1)
        for i, arr in enumerate((red_rgb, green_rgb, phase_rgb, merge)):
            im8 = Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8)).resize((tile, tile), Image.BILINEAR)
            y0 = i * (tile + 2)
            x0 = j * (tile + 2)
            out[y0:y0 + tile, x0:x0 + tile] = np.asarray(im8)
    return out, [int(f) for f in rows["frame"].values]
