"""Results across experiments: conditions with the same name are pooled.

An experiment can link other analysed folders (another plate, another day, a condition that
was exported on its own). Population plots, figures and duration plots then show every
condition from all of them; wells are the replicates, so statistics stay at well level.
Time is in hours, so experiments with different frame intervals line up.
"""

from __future__ import annotations

import numpy as np

from . import analysis as A
from .experiment import Experiment

CAT_NAMES = ["G1", "S", "G2M", "Colourless", "Unclassified"]


def cond_key(name: str) -> str:
    return " ".join(str(name).lower().split())


def exp_labels(exps: list[Experiment]) -> list[str]:
    names = [e.settings.experiment_name or e.folder.name for e in exps]
    return [f"{n} ({e.folder.name})" if names.count(n) > 1 and n != e.folder.name else n
            for n, e in zip(names, exps)]


def groups(exps: list[Experiment], include_hidden: bool = False) -> list[dict]:
    """Conditions across experiments, merged by name (case and spaces ignored), in the order and
    style set on the Figures page of the first (open) experiment."""
    out, index = [], {}
    labels = exp_labels(exps)
    for e, label in zip(exps, labels):
        conds = A._conditions(e)
        only_unassigned = len(conds) == 1 and conds[0]["id"] == "_unassigned"
        for c in conds:
            name = c["name"]
            if c["id"] == "_unassigned" and (only_unassigned or len(exps) > 1):
                name = label  # a folder exported per condition
            key = cond_key(name)
            if key not in index:
                index[key] = len(out)
                out.append({"key": key, "name": name, "color": c.get("color", "#A0A8B2"),
                            "dashed": bool(c.get("dashed")), "members": [], "experiments": []})
            out[index[key]]["members"].append((e, c["wells"]))
            out[index[key]]["experiments"].append(label)
    styles = exps[0].settings.styles if exps else {}
    for i, g in enumerate(out):
        st = styles.get(g["key"]) or {}
        g["color"] = st.get("color") or g["color"]
        g["dashed"] = bool(st.get("dashed", g["dashed"]))
        g["hidden"] = bool(st.get("hidden", False))
        g["order"] = st.get("order", 1000 + i)
    out.sort(key=lambda g: g["order"])
    return out if include_hidden else [g for g in out if not g["hidden"]]


def grid(exps: list[Experiment]) -> np.ndarray:
    steps = [e.settings.interval_h for e in exps if e.has_results]
    end = max((e.frame_hours()[-1] for e in exps if e.has_results and e.frame_hours()), default=0.0)
    step = min(steps) if steps else 1.0
    return np.round(np.arange(0, end + step * 0.5, step), 4)


def _resample(hours: list[float], values: np.ndarray, g: np.ndarray, fill=np.nan):
    """Value at the latest frame at or before each grid time; `fill` past the end of the movie."""
    h = np.asarray(hours)
    idx = np.searchsorted(h, g + 1e-6, side="right") - 1
    ok = (idx >= 0) & (g <= h[-1] + 1e-6)
    out = np.full((len(g),) + values.shape[1:], fill, dtype=float)
    out[ok] = values[idx[ok]]
    return out


def population(exps: list[Experiment]) -> dict:
    g = grid(exps)
    lab = {id(e): n for e, n in zip(exps, exp_labels(exps))}
    conds = []
    for grp in groups(exps, include_hidden=True):
        series = {k: [] for k in A.CALLS + ["count_norm", "confluence", "total", "colonies", "colony_area"]}
        wells = []
        for e, ws_names in grp["members"]:
            ws = A.well_series(e)
            hours = e.frame_hours()
            for w in ws_names:
                if w not in ws:
                    continue
                wells.append(w if len(exps) == 1 else f"{lab[id(e)]}: {w}")
                for i, p in enumerate(A.CALLS):
                    series[p].append(_resample(hours, ws[w]["frac"][:, i], g))
                for k in ("count_norm", "confluence", "total", "colonies", "colony_area"):
                    series[k].append(_resample(hours, ws[w][k], g))
        if not wells:
            continue
        entry = {"id": grp["key"], "name": grp["name"], "color": grp["color"], "dashed": grp["dashed"], "wells": wells}
        for k, arrs in series.items():
            m, sem = A._mean_sem(np.vstack(arrs))
            entry[k] = {"mean": A._clean(m), "sem": A._clean(sem)}
        conds.append(entry)
    return {"hours": [round(float(x), 3) for x in g], "conditions": conds}


def figures(exps: list[Experiment]) -> dict:
    """Data for the publication figures: per condition, a cohort of founder cells followed
    through time (state of each at every time point) and the observed phase durations."""
    g = grid(exps)
    out = []
    for grp in groups(exps, include_hidden=True):
        paths = []
        dur = {p: [] for p in A.PH}
        dkeys = {p: [] for p in A.PH}  # cell of each duration (only the open experiment's can be opened)
        cycles = []
        for e, wells in grp["members"]:
            hours = e.frame_hours()
            for pth in A.cohort_paths(e, wells):
                paths.append(_resample(hours, pth.astype(float), g, fill=-1))
            d = A.durations_for(e, wells)
            for p in A.PH:
                dur[p].extend(d["values"][p])
                dkeys[p].extend(d["keys"][p] if e is exps[0] else [None] * len(d["values"][p]))
            cycles.extend(d["cycle"])
        n = len(paths)
        comp = {k: [] for k in CAT_NAMES}
        if n:
            P = np.vstack(paths)
            for i, k in enumerate(CAT_NAMES):
                code = -1 if k == "Unclassified" else i
                comp[k] = A._clean(100.0 * (P == code).sum(axis=0) / n)
        out.append({"id": grp["key"], "name": grp["name"], "color": grp["color"], "dashed": grp["dashed"],
                    "hidden": grp["hidden"], "experiments": sorted(set(grp["experiments"])),
                    "n": n, "composition": comp, "durations": dur, "duration_keys": dkeys, "cycle": cycles})
    return {"hours": [round(float(x), 3) for x in g], "conditions": out, "experiments": exp_labels(exps)}


# ------------------------------------------------------------------ colonies as box plots
_SIZES: dict = {}


def _sizes(exp: Experiment, movie: str) -> dict:
    from .pipeline import load_colony_sizes

    d = exp.results_dir / "masks" / movie
    p = d / "colony_sizes.npz"
    try:
        st = p.stat().st_mtime
    except OSError:
        return {}
    hit = _SIZES.get(str(p))
    if hit and hit[0] == st:
        return hit[1]
    v = load_colony_sizes(d)
    _SIZES[str(p)] = (st, v)
    if len(_SIZES) > 2000:
        _SIZES.pop(next(iter(_SIZES)))
    return v


def _box_stats(v: np.ndarray) -> dict | None:
    v = np.asarray(v, dtype=float)
    v = v[np.isfinite(v)]
    if not len(v):
        return None
    q1, med, q3 = np.percentile(v, [25, 50, 75])
    iqr = q3 - q1
    lo = float(v[v >= q1 - 1.5 * iqr].min())
    hi = float(v[v <= q3 + 1.5 * iqr].max())
    return {"n": int(len(v)), "q1": float(q1), "med": float(med), "q3": float(q3), "lo": lo, "hi": hi,
            "mean": float(v.mean()), "min": float(v.min()), "max": float(v.max())}


def colony_dist(exps: list[Experiment], every_h: float | None = None, max_dots: int = 400) -> dict:
    """Colony number per image and the area of every colony at regular time points, per
    condition, for box plots. Excluded wells and fields are left out."""
    g = grid(exps)
    hmax = float(g[-1]) if len(g) else 0.0
    if not every_h or every_h <= 0:
        every_h = next((s for s in (1, 2, 3, 4, 6, 8, 12, 24, 48, 72, 96) if hmax / s <= 8), 96)
    times = [round(float(t), 3) for t in np.arange(0, hmax + 1e-6, every_h)]
    rng = np.random.default_rng(0)
    lab = {id(e): n for e, n in zip(exps, exp_labels(exps))}
    out = []
    for grp in groups(exps, include_hidden=True):
        counts = [[] for _ in times]
        count_labels = [[] for _ in times]
        areas = [[] for _ in times]
        for e, wells in grp["members"]:
            hours = np.asarray(e.frame_hours())
            if not len(hours):
                continue
            ws = set(wells)
            for key, m in e.meta.get("movies", {}).items():
                if m["well"] not in ws or e.movie_excluded(m["well"], m["field"]):
                    continue
                sizes = _sizes(e, key)
                frames = m["frames"]
                cols = m.get("colonies") or [None] * len(frames)
                for ti, t in enumerate(times):
                    if t > hours[-1] + 1e-6:
                        continue
                    f = int(np.argmin(np.abs(hours - t)))
                    if f in frames:
                        i = frames.index(f)
                        if cols[i] is not None:
                            counts[ti].append(int(cols[i]))
                            where = f"{m['well']} field {m['field']}"
                            count_labels[ti].append(where if len(exps) == 1 else f"{lab[id(e)]}: {where}")
                    if f in sizes:
                        areas[ti].append(sizes[f])
        c_out, a_out = [], []
        for ti in range(len(times)):
            c_out.append({"stats": _box_stats(counts[ti]), "values": counts[ti], "labels": count_labels[ti]})
            a = np.concatenate(areas[ti]) if areas[ti] else np.zeros(0)
            sample = a if len(a) <= max_dots else rng.choice(a, max_dots, replace=False)
            a_out.append({"stats": _box_stats(a), "sample": [int(x) for x in sample]})
        out.append({"id": grp["key"], "name": grp["name"], "color": grp["color"], "dashed": grp["dashed"],
                    "hidden": grp["hidden"], "counts": c_out, "areas": a_out})
    return {"times": times, "every_h": every_h, "conditions": out}
