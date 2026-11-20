"""Synthetic PIP-FUCCI Incucyte-style movies with known ground truth.

Used for the "Try with demo data" button and for tests. Files are written in Incucyte export
style: Phase/Green/Red folders, names like Demo_B2_1_01d02h00m.tif, 16-bit.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .settings import save_json

PHASE_ORDER = ["G1", "S", "G2", "M"]


def _intensity(phase: str, p: float):
    if phase == "G1":
        return 0.5 + 0.5 * min(1.0, p * 3.0), 0.04
    if phase == "S":
        return 0.9 * max(0.0, 1.0 - p * 6.0) + 0.04, 0.12 + 0.6 * p
    if phase == "G2":
        return 0.2 + 0.65 * min(1.0, p * 2.0), 0.78 + 0.1 * p
    return 0.45, 0.25 + 0.55 * (1 - p)  # M: signal dispersed, red dropping


class _Cell:
    __slots__ = ("id", "parent", "x", "y", "ang", "phase", "t", "dur", "arrested", "silent", "bright", "ax", "born",
                 "cl", "cl_dur")

    def __init__(self, cid, parent, x, y, rng, params, phase="G1", t=0.0, born=0):
        self.id, self.parent, self.x, self.y = cid, parent, x, y
        self.ang = rng.uniform(0, np.pi)
        self.phase, self.t = phase, t
        self.arrested = rng.random() < params["arrest"] if phase == "G1" else False
        self.silent = rng.random() < 0.04
        self.bright = rng.uniform(0.7, 1.15)
        self.ax = rng.uniform(0.75, 0.95)
        self.born = born
        self.dur = _durations(rng, params)
        # colourless spell at the start of G1 (both reporters below detection); arrested cells may stay colourless
        self.cl = rng.random() < params.get("colourless", 0.0)
        self.cl_dur = float(rng.uniform(3.0, 10.0)) if not self.arrested else 1e9


def _durations(rng, params):
    return {
        "G1": max(3.0, rng.normal(params["g1"], params["g1_sd"])),
        "S": max(4.0, rng.normal(8.0, 1.2)),
        "G2": max(2.0, rng.normal(4.0, 0.8)),
        "M": 1.0,
    }


def simulate_movie(rng, params, n_frames, interval_h, shape, nucleus_px, n0=55, cap=220):
    h, w = shape
    cells = []
    nid = [0]

    def new_id():
        nid[0] += 1
        return nid[0]

    for _ in range(n0):
        ph = rng.choice(PHASE_ORDER[:3], p=[0.55, 0.3, 0.15])
        c = _Cell(new_id(), 0, rng.uniform(10, w - 10), rng.uniform(10, h - 10), rng, params, ph)
        c.t = rng.uniform(0, c.dur[ph] * 0.95)
        cells.append(c)
    _repel(cells, 1.05 * nucleus_px, iters=20)
    frames = []
    speed_px = params["speed_um_h"] / params["pixel_um"]
    for f in range(n_frames):
        snap = []
        for c in cells:
            p = 0.5 if c.arrested else min(1.0, c.t / c.dur[c.phase])
            g, r = _intensity(c.phase, p)
            colourless = bool(c.cl and c.phase == "G1" and (c.arrested or c.t < c.cl_dur))
            if colourless:
                g, r = 0.025, 0.02
            snap.append((c.id, c.parent, c.x, c.y, c.ang, c.ax, c.phase, g, r, c.silent, c.bright, colourless))
        frames.append(snap)
        # advance
        nxt = []
        crowded = len(cells) > cap
        for c in cells:
            step = speed_px * interval_h
            c.x = float(np.clip(c.x + rng.normal(0, step * 0.6), -5, w + 5))
            c.y = float(np.clip(c.y + rng.normal(0, step * 0.6), -5, h + 5))
            if c.arrested:
                c.t += interval_h
                nxt.append(c)
                continue
            c.t += interval_h
            divided = False
            while c.t >= c.dur[c.phase]:
                c.t -= c.dur[c.phase]
                i = PHASE_ORDER.index(c.phase)
                if c.phase == "M":
                    divided = True
                    break
                c.phase = PHASE_ORDER[i + 1]
            if divided:
                a = rng.uniform(0, np.pi)
                off = 0.75 * nucleus_px
                for sgn in (1, -1):
                    d = _Cell(new_id(), c.id, c.x + sgn * off * np.cos(a), c.y + sgn * off * np.sin(a), rng, params,
                              "G1", c.t, born=f + 1)
                    if crowded:
                        d.arrested = True
                    nxt.append(d)
            else:
                nxt.append(c)
        cells = [c for c in nxt if -nucleus_px < c.x < w + nucleus_px and -nucleus_px < c.y < h + nucleus_px]
        _repel(cells, 1.05 * nucleus_px)
    return frames


def _repel(cells, dmin, iters=6):
    """Nuclei in a monolayer touch but do not overlap: push apart pairs closer than dmin."""
    from scipy.spatial import cKDTree

    if len(cells) < 2:
        return
    for _ in range(iters):
        pts = np.array([[c.x, c.y] for c in cells])
        pairs = cKDTree(pts).query_pairs(dmin, output_type="ndarray")
        if len(pairs) == 0:
            break
        move = np.zeros_like(pts)
        for i, j in pairs:
            d = pts[j] - pts[i]
            dist = float(np.hypot(*d)) or 1e-3
            push = (dmin - dist) / 2.0 * d / dist
            move[i] -= push
            move[j] += push
        for c, m in zip(cells, move):
            c.x += m[0]
            c.y += m[1]


def autofluorescence_field(shape, rng, strength=1.0):
    """Uneven green background (medium, plastic, debris): broad blobs, fixed over the movie."""
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    f = np.zeros(shape)
    for _ in range(7):
        cy, cx = rng.uniform(0, h), rng.uniform(0, w)
        sg = rng.uniform(0.12, 0.35) * max(h, w)
        f += rng.uniform(0.4, 1.0) * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * sg * sg))
    f /= f.max() or 1.0
    return strength * 700.0 * f


def render(snap, shape, nucleus_px, rng, frame_idx, green_bg=None, autofluor=0.0):
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    bg_r = 220 + 60 * (xx / w) + 40 * (yy / h)
    bg_g = 260 + 50 * (1 - xx / w) + 30 * (yy / h)
    red = bg_r.astype(np.float64)
    green = bg_g.astype(np.float64)
    if green_bg is not None:
        green = green + green_bg
    phase = np.full(shape, 1000.0) + 30 * np.sin(xx / 37.0) * np.cos(yy / 53.0)
    bleach = np.exp(-0.002 * frame_idx)
    R = nucleus_px / 2.0
    amp = 2600.0 * bleach
    for (cid, par, x, y, ang, ax, ph, g, r, silent, bright, cl) in snap:
        rad = R * (1.15 if ph == "M" else 1.0)
        k = int(rad * 3) + 3
        x0, x1 = max(0, int(x) - k), min(w, int(x) + k + 1)
        y0, y1 = max(0, int(y) - k), min(h, int(y) + k + 1)
        if x0 >= x1 or y0 >= y1:
            continue
        X = xx[y0:y1, x0:x1] - x
        Y = yy[y0:y1, x0:x1] - y
        ca, sa = np.cos(ang), np.sin(ang)
        u = (X * ca + Y * sa) / rad
        v = (-X * sa + Y * ca) / (rad * ax)
        d = np.sqrt(u * u + v * v)
        prof = 1.0 / (1.0 + np.exp((d - 1.0) * 9.0))
        body = 1.0 / (1.0 + np.exp((d / 1.9 - 1.0) * 6.0))
        if not silent:
            scale = (0.6 if ph == "M" else 1.0) * bright * amp
            red[y0:y1, x0:x1] += prof * r * scale
            green[y0:y1, x0:x1] += prof * g * scale
        if autofluor:  # cytoplasmic green autofluorescence of every cell, coloured or not
            green[y0:y1, x0:x1] += body * 0.07 * autofluor * amp
        ring = np.exp(-((d / 1.9 - 1.0) ** 2) / 0.02)
        if ph == "M":
            phase[y0:y1, x0:x1] += 260 * prof + 180 * ring
        else:
            phase[y0:y1, x0:x1] += -90 * body + 140 * ring
    red = red + rng.normal(0, 22, shape)
    green = green + rng.normal(0, 22, shape)
    phase = phase + rng.normal(0, 25, shape)
    to16 = lambda a: np.clip(a, 0, 65535).astype(np.uint16)
    return to16(red), to16(green), to16(phase)


def make_demo(out_dir: str | Path, wells: dict | None = None, fields: int = 1, n_frames: int = 49,
              interval_min: float = 30.0, shape=(400, 560), seed: int = 7, write_settings: bool = True,
              progress=None, compression: str | None = None, autofluor: float = 1.0,
              colourless: dict | None = None, speed_um_h: dict | None = None) -> Path:
    import tifffile

    out = Path(out_dir)
    for ch in ("Phase", "Green", "Red"):
        (out / ch).mkdir(parents=True, exist_ok=True)
    pixel_um, nucleus_um = 0.62, 12.0
    nucleus_px = nucleus_um / pixel_um
    colourless = colourless or {"slow": 0.35, "fast": 0.12}
    speed_um_h = speed_um_h or {"slow": 9.0, "fast": 11.0}
    conds = {
        "slow": {"g1": 15.0, "g1_sd": 5.0, "arrest": 0.30, "speed_um_h": speed_um_h["slow"], "pixel_um": pixel_um,
                 "colourless": colourless["slow"]},
        "fast": {"g1": 9.0, "g1_sd": 2.5, "arrest": 0.06, "speed_um_h": speed_um_h["fast"], "pixel_um": pixel_um,
                 "colourless": colourless["fast"]},
    }
    if wells is None:
        wells = {"B2": "slow", "B3": "slow", "B4": "slow", "C2": "fast", "C3": "fast", "C4": "fast"}
    rng = np.random.default_rng(seed)
    gt_rows = []
    ih = interval_min / 60.0
    jobs = [(w, f) for w in wells for f in range(1, fields + 1)]
    for k, (well, fld) in enumerate(jobs):
        frames = simulate_movie(rng, conds[wells[well]], n_frames, ih, shape, nucleus_px)
        gbg = autofluorescence_field(shape, rng, autofluor) if autofluor else None
        for fi, snap in enumerate(frames):
            red, green, phase = render(snap, shape, nucleus_px, rng, fi, gbg, autofluor)
            mins = int(round(fi * interval_min))
            stamp = f"{mins // 1440:02d}d{(mins % 1440) // 60:02d}h{mins % 60:02d}m"
            name = f"Demo_{well}_{fld}_{stamp}.tif"
            tifffile.imwrite(out / "Red" / name, red, compression=compression)
            tifffile.imwrite(out / "Green" / name, green, compression=compression)
            tifffile.imwrite(out / "Phase" / name, phase, compression=compression)
            for (cid, par, x, y, ang, ax, ph, g, r, silent, bright, cl) in snap:
                gt_rows.append((well, fld, fi, cid, par, round(x, 2), round(y, 2), ph, int(silent), int(cl)))
        if progress:
            progress((k + 1) / len(jobs))
    gt_dir = out / "_ground_truth"
    gt_dir.mkdir(exist_ok=True)
    with open(gt_dir / "cells.csv", "w") as fh:
        fh.write("well,field,frame,cell,parent,x,y,phase,silent,colourless\n")
        for row in gt_rows:
            fh.write(",".join(str(v) for v in row) + "\n")
    if write_settings:
        from .settings import Settings

        s = Settings(interval_min=interval_min, pixel_um=pixel_um, nucleus_um=nucleus_um, max_speed_um_h=20.0,
                     experiment_name="Demo: slow vs fast cycling")
        s.conditions = [{"id": "c1", "name": "Slow cycling", "color": "#7AB0FF"},
                        {"id": "c2", "name": "Fast cycling", "color": "#F59E5B"}]
        s.wells = {w: ("c1" if v == "slow" else "c2") for w, v in wells.items()}
        s.channel_map = {"Green/Demo": "green", "Red/Demo": "red", "Phase/Demo": "phase"}
        save_json(out / "PIP-FUCCI results" / "experiment.json", {"settings": s.to_dict(), "stale": None})
    return out
