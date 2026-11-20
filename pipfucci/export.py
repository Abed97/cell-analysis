"""Exports: Prism-ready tables, per-cell and per-track tables, and the settings/versions record."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import analysis
from .experiment import Experiment, run_info
from .settings import save_json

PH_NAMES = {"G1": "G1", "S": "S", "G2M": "G2-M", "Colourless": "Colourless"}


def _prism_table(exp: Experiment, ws: dict, key: str, col: int | None = None) -> pd.DataFrame:
    """Prism grouped layout: Time (h), then one column per well, grouped by condition."""
    hours = exp.frame_hours()
    df = pd.DataFrame({"Time (h)": np.round(hours, 3)})
    for cnd in analysis._conditions(exp):
        for w in cnd["wells"]:
            if w not in ws:
                continue
            v = ws[w][key]
            if col is not None:
                v = v[:, col]
            df[f"{cnd['name']} | {w}"] = np.round(v, 4)
    return df


def _safe(name: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9 _().+-]+", "_", str(name)).strip() or "condition"


def export_figure_data(exps: list[Experiment], out: Path) -> None:
    """The numbers behind the Figures page (cohort composition and per-cell phase durations)."""
    from . import compare

    fig = compare.figures(exps)
    fd = out / "Figure data"
    fd.mkdir(parents=True, exist_ok=True)
    for c in fig["conditions"]:
        if not c["n"]:
            continue
        df = pd.DataFrame({"Time (h)": fig["hours"]})
        for k in compare.CAT_NAMES:
            df[f"{k} (% of cells)"] = np.round(np.asarray(c["composition"][k], dtype=float), 3)
        df.to_csv(fd / f"Composition {_safe(c['name'])} (n={c['n']}).csv", index=False)
    for p in analysis.PH:
        cols = {f"{c['name']} (n={len(c['durations'][p])})": pd.Series(c["durations"][p], dtype=float)
                for c in fig["conditions"]}
        pd.DataFrame(cols).to_csv(fd / f"Hours per observed stage {PH_NAMES[p]}.csv", index=False)
    if len(exps) > 1:
        (fd / "Experiments included.txt").write_text("\n".join(str(e.folder) for e in exps) + "\n")


def export_all(exp: Experiment, others: list | None = None) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out = exp.results_dir / "exports" / stamp
    prism = out / "Prism tables"
    prism.mkdir(parents=True, exist_ok=True)
    ws = analysis.well_series(exp)

    for i, p in enumerate(analysis.CALLS):
        _prism_table(exp, ws, "frac", i).to_csv(prism / f"Phase fraction {PH_NAMES[p]} (%).csv", index=False)
    _prism_table(exp, ws, "count_norm").to_csv(prism / "Cell number (fold of t0).csv", index=False)
    _prism_table(exp, ws, "total").to_csv(prism / "Cell number (detected nuclei).csv", index=False)
    _prism_table(exp, ws, "confluence").to_csv(prism / "Confluence (%).csv", index=False)

    # durations, one column per condition (Prism column table)
    dur = analysis.durations_by_condition(exp)
    for p in analysis.PH + ["cycle"]:
        cols = {}
        for c in dur["conditions"]:
            vals = c["cycle"] if p == "cycle" else c["values"][p]
            cols[c["name"]] = pd.Series(vals, dtype=float)
        name = "Full cycle" if p == "cycle" else PH_NAMES[p]
        pd.DataFrame(cols).to_csv(prism / f"Duration {name} (h), fully observed cells.csv", index=False)

    # mean +/- SEM summary
    pop = analysis.population(exp)
    rows = []
    for c in pop["conditions"]:
        for metric in ["G1", "S", "G2M", "Colourless", "count_norm", "confluence"]:
            for h, m, s in zip(pop["hours"], c[metric]["mean"], c[metric]["sem"]):
                rows.append({"condition": c["name"], "metric": metric, "time_h": h, "mean": m, "sem": s,
                             "n_wells": len(c["wells"])})
    pd.DataFrame(rows).to_csv(out / "Summary mean and SEM per condition.csv", index=False)

    # per cell per frame
    cells = exp.cells.copy()
    hours = np.array(exp.frame_hours())
    cells.insert(3, "time_h", np.round(hours[cells["frame"].values], 3))
    cells["condition"] = cells["well"].map(lambda w: (exp.settings.condition_of(w) or {}).get("name", "Unassigned"))
    names = {0: "G1", 1: "S", 2: "G2-M", 3: "Colourless"}
    cells["phase_call"] = cells["call" if "call" in cells else "phase"].map(names)
    cells["smoothed_phase"] = cells["phase"].map(names)
    cells["raw_gate_call"] = cells["raw"].map({0: "G1", 1: "S", 2: "G2-M", 3: "dim"})
    if "interp" in cells:
        cells["position_interpolated"] = cells.pop("interp").astype(bool)
    parents = exp.meta.get("parents", {})
    cells["parent_track"] = cells["track"].map(parents).fillna("")
    ex = exp.excluded_tracks()
    cells["excluded"] = cells["track"].isin(ex)
    cells = cells.rename(columns={"red": "red_mean_bgcorr", "green": "green_mean_bgcorr", "red_med": "red_median_bgcorr",
                                  "green_med": "green_median_bgcorr", "r_n": "red_normalised", "g_n": "green_normalised",
                                  "x_reg": "x_registered", "y_reg": "y_registered", "edge": "at_field_edge"})
    cells.drop(columns=["raw", "phase", "call", "tid"], errors="ignore").to_csv(out / "Cells per frame.csv", index=False)

    # per track
    t = exp.tracks.copy()
    ih = exp.settings.interval_h
    seg_cols = {"G1_h": [], "S_h": [], "G2M_h": [], "cycle_h": []}
    for key in t.index:
        d = analysis.phase_durations(exp, key)
        found = {p: None for p in analysis.PH}
        for sgm in d["segments"]:
            if sgm["observed"] and found[sgm["phase"]] is None:
                found[sgm["phase"]] = sgm["hours"]
        for p in analysis.PH:
            seg_cols[p + "_h"].append(found[p])
        seg_cols["cycle_h"].append(d["cycle_h"])
    for k, v in seg_cols.items():
        t[k] = v
    t["start_h"] = np.round(hours[t["start"].values], 3)
    t["end_h"] = np.round(hours[t["end"].values], 3)
    t["excluded"] = t.index.isin(ex)
    t["reviewed"] = [bool(exp.flags.get(k, {}).get("reviewed")) for k in t.index]
    t["duration_uncertainty_h"] = ih
    t.index.name = "track"
    t.to_csv(out / "Tracks.csv")

    export_figure_data([exp] + list(others or []), out)
    save_json(out / "Settings and versions.json", run_info(exp))
    (out / "README.txt").write_text(
        "PIP-FUCCI export\n\n"
        "Prism tables/: one column per well, grouped by condition. Paste into a Prism grouped table.\n"
        "Phase calls are smoothed per track (order G1 -> S -> G2-M enforced). 'Colourless' means neither reporter\n"
        "was on (typically just after division); those cells are followed on phase contrast and their\n"
        "position_interpolated rows are not counted as detected nuclei. Durations are only for phases\n"
        f"fully observed inside the movie and carry +/- {ih:g} h (one frame). Cells present at the first frame,\n"
        "ending at the last frame, or at the field edge are censored and left out of durations.\n"
        "Statistics should be run on well means (biological replicates), never on individual cells.\n"
        "Settings and versions.json records everything used to make these numbers.\n"
    )
    return out
