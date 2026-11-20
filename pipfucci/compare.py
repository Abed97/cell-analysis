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
        series = {k: [] for k in A.CALLS + ["count_norm", "confluence", "total"]}
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
                for k in ("count_norm", "confluence", "total"):
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
        cycles = []
        for e, wells in grp["members"]:
            hours = e.frame_hours()
            for pth in A.cohort_paths(e, wells):
                paths.append(_resample(hours, pth.astype(float), g, fill=-1))
            d = A.durations_for(e, wells)
            for p in A.PH:
                dur[p].extend(d["values"][p])
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
                    "n": n, "composition": comp, "durations": dur, "cycle": cycles})
    return {"hours": [round(float(x), 3) for x in g], "conditions": out, "experiments": exp_labels(exps)}
