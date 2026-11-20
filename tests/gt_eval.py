"""Compare pipeline output with the synthetic ground truth."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from scipy.spatial import cKDTree

GT_PHASE = {"G1": 0, "S": 1, "G2": 2, "M": 2}


def match(exp, gt: pd.DataFrame, radius: float = 6.0) -> pd.DataFrame:
    """Return GT rows with the matched detection index (or -1)."""
    cells = exp.cells
    out = []
    for (well, fld, fr), g in gt.groupby(["well", "field", "frame"]):
        d = cells[(cells.well == well) & (cells.field == fld) & (cells.frame == fr)]
        m = np.full(len(g), -1)
        if len(d):
            D = np.hypot(g.x.values[:, None] - d.x.values[None], g.y.values[:, None] - d.y.values[None])
            D[D > radius] = 1e6
            r, c = linear_sum_assignment(D)
            for a, b in zip(r, c):
                if D[a, b] < 1e6:
                    m[a] = d.index.values[b]
        gg = g.copy()
        gg["det"] = m
        out.append(gg)
    return pd.concat(out)


def evaluate(exp, gt_path) -> dict:
    gt = pd.read_csv(gt_path)
    gt["field"] = gt["field"].astype(int)
    h, w = exp.meta["movies"][next(iter(exp.meta["movies"]))]["shape"]
    m = 8
    inside = (gt.x > m) & (gt.x < w - m) & (gt.y > m) & (gt.y < h - m)
    gt = gt[inside].copy()
    mt = match(exp, gt)
    vis = mt[(mt.silent == 0) & (mt.get("colourless", 0) == 0)] if "colourless" in mt else mt[mt.silent == 0]
    recall = float((vis.det >= 0).mean())
    real = exp.cells[~exp.cells["interp"]] if "interp" in exp.cells else exp.cells
    n_det = len(real[(real.x > m) & (real.x < w - m) & (real.y > m) & (real.y < h - m)])
    real_hits = mt[(mt.det >= 0)]
    real_hits = real_hits[~exp.cells.loc[real_hits.det.values, "interp"].values] if "interp" in exp.cells else real_hits
    precision = float(len(real_hits) / max(1, n_det))
    hit = vis[vis.det >= 0]
    det = exp.cells.loc[hit.det.values]
    truth = hit.phase.map(GT_PHASE).values
    raw = det["raw"].values
    smooth = det["phase"].values
    raw_acc = float(np.mean(raw == truth))
    raw_acc_assigned = float(np.mean(raw[raw < 3] == truth[raw < 3]))
    smooth_acc = float(np.mean(smooth == truth))
    # tracking: consecutive-frame identity
    hit = hit.assign(track=det["track"].values)
    key = hit.set_index(["well", "field", "frame", "cell"])["track"]
    ok = tot = 0
    for (well, fld, fr, cell), tr in key.items():
        nxt = key.get((well, fld, fr + 1, cell))
        if nxt is not None:
            tot += 1
            ok += int(nxt == tr)
    link_acc = ok / max(1, tot)
    # divisions: daughters whose parent is matched at the previous frame
    parents = exp.meta.get("parents", {})
    births = gt[(gt.parent > 0)].sort_values("frame").groupby(["well", "field", "cell"]).head(1)
    found = total = 0
    for _, b in births.iterrows():
        dt = hit[(hit.well == b.well) & (hit.field == b.field) & (hit.frame == b.frame) & (hit.cell == b.cell)]
        pt = hit[(hit.well == b.well) & (hit.field == b.field) & (hit.frame == b.frame - 1) & (hit.cell == b.parent)]
        if len(dt) and len(pt):
            total += 1
            dtrack = dt.track.iloc[0]
            ptrack = pt.track.iloc[0]
            found += int(parents.get(dtrack) == ptrack)
    div_recall = found / max(1, total)
    n_div_detected = len(set(parents.values()))
    n_div_true = gt[gt.parent > 0].groupby(["well", "field"]).parent.nunique().sum()
    # completeness: share of each cell's life (inside the field) carried by its single best track,
    # counting colourless frames too (the cell is there; it just has no colour)
    allm = mt.copy()
    allm["track"] = None
    hit_all = allm[allm.det >= 0]
    allm.loc[hit_all.index, "track"] = exp.cells.loc[hit_all.det.values, "track"].values
    comp = []
    for (well, fld, cell), g in allm.groupby(["well", "field", "cell"]):
        if g.silent.max() or len(g) < 6:
            continue
        best = g.track.dropna().value_counts()
        comp.append((best.iloc[0] if len(best) else 0) / len(g))
    completeness = float(np.mean(comp)) if comp else 0.0
    cl = mt[mt.get("colourless", 0) == 1] if "colourless" in mt else mt.iloc[0:0]
    cl_found = float((cl.det >= 0).mean()) if len(cl) else float("nan")
    return {
        "track_completeness": round(completeness, 3),
        "colourless_frames_found": round(cl_found, 3) if cl_found == cl_found else None,
        "seg_recall": round(recall, 3), "seg_precision": round(precision, 3),
        "phase_raw_acc": round(raw_acc, 3), "phase_raw_acc_assigned": round(raw_acc_assigned, 3),
        "phase_smoothed_acc": round(smooth_acc, 3),
        "link_acc": round(link_acc, 3), "division_recall": round(div_recall, 3),
        "divisions_detected": int(n_div_detected), "divisions_true": int(n_div_true),
    }
