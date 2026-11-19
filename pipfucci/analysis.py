"""Population (bulk) and single-cell outputs computed from the per-cell-per-frame table.

Statistics are summarised to the well first; conditions are mean +/- SEM across wells.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .experiment import Experiment

PH = ["G1", "S", "G2M"]


def _active(exp: Experiment) -> pd.DataFrame:
    c = exp.cells
    ex = exp.excluded_tracks()
    if ex:
        c = c[~c["track"].isin(ex)]
    return c


def _conditions(exp: Experiment):
    s = exp.settings
    out = []
    present = set(exp.index["wells"])
    for cnd in s.conditions:
        wells = [w for w, cid in s.wells.items() if cid == cnd["id"] and w in present and w != s.parental_well]
        wells.sort(key=lambda w: (w[0], int(w[1:])))
        if wells:
            out.append({**cnd, "wells": wells})
    assigned = set(s.wells)
    rest = [w for w in exp.index["wells"] if w not in assigned and w != s.parental_well]
    if rest:
        out.append({"id": "_unassigned", "name": "Unassigned", "color": "#A0A8B2", "wells": rest})
    return out


def well_series(exp: Experiment) -> dict:
    """Per well and frame: counts by phase, total, confluence (mean over fields)."""
    c = _active(exp)
    n_frames = len(exp.index["times_min"])
    out = {}
    for well, sub in c.groupby("well", sort=False):
        counts = np.zeros((n_frames, 3))
        tab = sub.groupby(["frame", "phase"]).size()
        for (f, p), v in tab.items():
            if 0 <= f < n_frames and 0 <= p < 3:
                counts[f, p] = v
        total = counts.sum(axis=1)
        conf = np.full(n_frames, np.nan)
        confs = []
        for m in exp.meta.get("movies", {}).values():
            if m["well"] == well:
                arr = np.full(n_frames, np.nan)
                for f, v in zip(m["frames"], m["confluence"]):
                    if v is not None and 0 <= f < n_frames:
                        arr[f] = v
                confs.append(arr)
        if confs:
            with np.errstate(all="ignore"):
                conf = np.nanmean(np.vstack(confs), axis=0) * 100
        with np.errstate(divide="ignore", invalid="ignore"):
            frac = counts / total[:, None] * 100
            norm = total / total[0] if total[0] > 0 else np.full(n_frames, np.nan)
        out[well] = {"counts": counts, "total": total, "frac": frac, "count_norm": norm, "confluence": conf}
    return out


def _mean_sem(stack: np.ndarray):
    with np.errstate(all="ignore"):
        m = np.nanmean(stack, axis=0)
        n = np.sum(np.isfinite(stack), axis=0)
        sd = np.nanstd(stack, axis=0, ddof=1) if stack.shape[0] > 1 else np.zeros(stack.shape[1])
        sem = np.where(n > 1, sd / np.sqrt(np.maximum(n, 1)), 0.0)
    return m, sem


def _clean(a):
    return [None if (v is None or not np.isfinite(v)) else round(float(v), 4) for v in a]


def population(exp: Experiment) -> dict:
    ws = well_series(exp)
    hours = exp.frame_hours()
    conds = []
    for cnd in _conditions(exp):
        wells = [w for w in cnd["wells"] if w in ws]
        if not wells:
            continue
        entry = {"id": cnd["id"], "name": cnd["name"], "color": cnd["color"], "wells": wells}
        for i, p in enumerate(PH):
            m, s = _mean_sem(np.vstack([ws[w]["frac"][:, i] for w in wells]))
            entry[p] = {"mean": _clean(m), "sem": _clean(s)}
        for key in ("count_norm", "confluence", "total"):
            m, s = _mean_sem(np.vstack([ws[w][key] for w in wells]))
            entry[key] = {"mean": _clean(m), "sem": _clean(s)}
        conds.append(entry)
    return {"hours": [round(h, 3) for h in hours], "conditions": conds}


def scatter(exp: Experiment, frame: int, well: str | None = None, field: int | None = None,
            condition: str | None = None, max_points: int = 3000) -> dict:
    c = _active(exp)
    sub = c[c["frame"] == frame]
    if well:
        sub = sub[sub["well"] == well]
    if field is not None:
        sub = sub[sub["field"] == field]
    if condition:
        wells = next((x["wells"] for x in _conditions(exp) if x["id"] == condition), [])
        sub = sub[sub["well"].isin(wells)]
    n = len(sub)
    counts = np.bincount(sub["raw"].values.astype(int), minlength=4)
    if n > max_points:
        sub = sub.sample(max_points, random_state=0)
    raw = sub["raw"].values.astype(int)
    return {
        "r": [round(float(v), 4) for v in sub["r_n"].values],
        "g": [round(float(v), 4) for v in sub["g_n"].values],
        "raw": raw.tolist(),
        "n": int(n),
        "counts": counts[:4].tolist(),
        "gate_green": exp.settings.gate_green,
        "gate_red": exp.settings.gate_red,
        "floor": exp.meta.get("floor"),
    }


def _segments(states: np.ndarray, frames: np.ndarray):
    segs = []
    if len(states) == 0:
        return segs
    start = 0
    for i in range(1, len(states) + 1):
        if i == len(states) or states[i] != states[start]:
            segs.append((int(states[start]), int(frames[start]), int(frames[i - 1])))
            start = i
    return segs


def track_phases(exp: Experiment, key: str):
    c = exp.cells
    sub = c[c["track"] == key].sort_values("frame")
    return sub


def phase_durations(exp: Experiment, key: str, sub: pd.DataFrame | None = None) -> dict:
    t = exp.tracks.loc[key]
    if sub is None:
        sub = track_phases(exp, key)
    segs = _segments(sub["phase"].values, sub["frame"].values)
    ih = exp.settings.interval_h
    out = []
    for i, (p, a, b) in enumerate(segs):
        start_ok = i > 0 or bool(t["start_known"])
        end_ok = i < len(segs) - 1 or bool(t["end_known"])
        out.append({"phase": PH[p], "start": a, "end": b, "hours": round((b - a + 1) * ih, 3),
                    "observed": bool(start_ok and end_ok)})
    cycle = None
    if t["start_known"] and t["end_known"]:
        cycle = round((int(t["end"]) - int(t["start"]) + 1) * ih, 3)
    return {"segments": out, "cycle_h": cycle}


def tracks_overview(exp: Experiment, condition: str | None = None, well: str | None = None,
                    sort: str = "division", min_frames: int = 3, limit: int = 400) -> dict:
    t = exp.tracks
    sel = t[t["n"] >= min_frames]
    if well:
        sel = sel[sel["well"] == well]
    elif condition:
        wells = next((x["wells"] for x in _conditions(exp) if x["id"] == condition), [])
        sel = sel[sel["well"].isin(wells)]
    ex = exp.excluded_tracks()
    c = exp.cells
    sub = c[c["track"].isin(sel.index)]
    g1 = sub[sub["phase"] == 0].groupby("track").size()
    sel = sel.assign(g1_frames=g1.reindex(sel.index).fillna(0).values)
    if sort == "g1":
        sel = sel.sort_values(["g1_frames", "start"], ascending=[False, True])
    elif sort == "start":
        sel = sel.sort_values(["start", "end"])
    else:
        div = np.where(sel["divided"], sel["end"], 10 ** 6)
        sel = sel.assign(_d=div).sort_values(["_d", "g1_frames"], ascending=[True, False])
    total = len(sel)
    sel = sel.head(limit)
    n_frames = len(exp.index["times_min"])
    sub = c[c["track"].isin(sel.index)][["track", "frame", "phase"]]
    states = {k: [-1] * n_frames for k in sel.index}
    for k, f, p in zip(sub["track"].values, sub["frame"].values, sub["phase"].values):
        if 0 <= f < n_frames:
            states[k][f] = int(p)
    flags = exp.flags
    rows = []
    for k, r in sel.iterrows():
        rows.append({
            "key": k, "label": f"{r['well']}·{r['field']}·{int(r['tid'])}", "states": states[k],
            "fate": r["fate"], "parent": r["parent"], "low_conf": bool(r["low_conf"]),
            "excluded": k in ex, "reviewed": bool(flags.get(k, {}).get("reviewed")),
        })
    return {"rows": rows, "total": int(total), "n_frames": n_frames, "hours": exp.frame_hours()}


def track_detail(exp: Experiment, key: str) -> dict:
    t = exp.tracks.loc[key]
    sub = track_phases(exp, key)
    d = phase_durations(exp, key, sub)
    hours = exp.frame_hours()
    parents = exp.meta.get("parents", {})
    children = [k for k, p in parents.items() if p == key]
    flags = []
    if t["low_conf"]:
        flags.append("Low-confidence parentage: review")
    if not t["start_known"]:
        flags.append("Start censored" + (" (present at first frame)" if t["founder"] else ""))
    if not t["end_known"]:
        flags.append("End censored")
    if t["start_edge"] or t["end_edge"]:
        flags.append("At the field edge: excluded from durations")
    f = exp.flags.get(key, {})
    return {
        "key": key, "well": t["well"], "field": int(t["field"]), "tid": int(t["tid"]),
        "condition": t["condition"], "parent": t["parent"], "children": children,
        "start": int(t["start"]), "end": int(t["end"]), "fate": t["fate"],
        "frames": sub["frame"].astype(int).tolist(),
        "hours": [round(hours[int(fr)], 3) if int(fr) < len(hours) else None for fr in sub["frame"].values],
        "r": [round(float(v), 4) for v in sub["r_n"].values],
        "g": [round(float(v), 4) for v in sub["g_n"].values],
        "raw": sub["raw"].astype(int).tolist(),
        "phase": sub["phase"].astype(int).tolist(),
        "x": [round(float(v), 1) for v in sub["x"].values],
        "y": [round(float(v), 1) for v in sub["y"].values],
        "durations": d, "flags": flags,
        "excluded": bool(f.get("excluded")), "reviewed": bool(f.get("reviewed")),
        "precision_h": exp.settings.interval_h,
    }


def durations_by_condition(exp: Experiment) -> dict:
    t = exp.tracks
    ex = exp.excluded_tracks()
    c = _active(exp)
    ih = exp.settings.interval_h
    out = []
    for cnd in _conditions(exp):
        sel = t[t["well"].isin(cnd["wells"]) & ~t.index.isin(ex)]
        sub = c[c["track"].isin(sel.index)].sort_values(["track", "frame"])
        vals = {p: [] for p in PH}
        censored = {p: 0 for p in PH}
        cycles = []
        for key, g in sub.groupby("track", sort=False):
            r = sel.loc[key]
            segs = _segments(g["phase"].values, g["frame"].values)
            for i, (p, a, b) in enumerate(segs):
                ok = (i > 0 or r["start_known"]) and (i < len(segs) - 1 or r["end_known"])
                if ok:
                    vals[PH[p]].append(round((b - a + 1) * ih, 3))
                else:
                    censored[PH[p]] += 1
            if r["start_known"] and r["end_known"]:
                cycles.append(round((int(r["end"]) - int(r["start"]) + 1) * ih, 3))
        med = {p: (float(np.median(v)) if v else None) for p, v in vals.items()}
        out.append({"id": cnd["id"], "name": cnd["name"], "color": cnd["color"], "values": vals,
                    "cycle": cycles, "median": med, "median_cycle": float(np.median(cycles)) if cycles else None,
                    "censored": censored, "n_tracks": int(len(sel))})
    return {"conditions": out, "precision_h": ih}


def detection_stats(exp: Experiment, well: str, field: int, frame: int) -> dict:
    c = exp.cells
    sub = c[(c["well"] == well) & (c["field"] == field) & (c["frame"] == frame)]
    conf = None
    m = exp.meta.get("movies", {}).get(f"{well}_{field}")
    if m and frame in m["frames"]:
        conf = m["confluence"][m["frames"].index(frame)]
    return {"detected": int(len(sub)), "confluence": None if conf is None else round(conf * 100, 1)}
