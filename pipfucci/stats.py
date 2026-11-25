"""Statistics for the box plots: phase durations (G1, S, G2/M) and colony number and area.

The unit of comparison matters more than the test. Cells (or images, or colonies) from one well
are not independent, so treating thousands of them as separate samples makes tiny differences
"significant". Three units are offered, and Auto takes the most independent one with enough of it:

  experiments  one value per independent experiment (biological replicates, from Compare)
  wells        one value per well (technical replicates within an experiment)
  cells / images / colonies   every measurement on its own (descriptive only)

Per unit the usual Prism tests:
  cells/images/colonies: Kruskal-Wallis with Dunn's multiple comparisons (Holm-adjusted);
                         two conditions: Mann-Whitney
  wells, or experiments that do not all contain every condition:
                         ordinary one-way ANOVA with Tukey's (all pairs) or Dunnett's (each vs a
                         control) multiple comparisons; two conditions: Welch's t test
  experiments that contain every condition (paired by experiment):
                         repeated-measures one-way ANOVA (Greenhouse-Geisser) with Holm-adjusted
                         paired t tests; two conditions: paired t test
Summaries per well or experiment: median of the cells' durations, mean colonies per image,
median colony area.
"""
from __future__ import annotations

import csv
import io
import math
from itertools import combinations

import numpy as np
from scipy import stats as ss

MIN_CELLS = 5      # a well or experiment needs this many durations to have a median
MIN_COLONIES = 3   # ... and this many colonies for a median area
AUTO_N = 3         # Auto picks a replicate unit only with at least this many per condition
PH = ("G1", "S", "G2M")
PH_LABEL = {"G1": "G1", "S": "S", "G2M": "G2/M"}

OBJ = {"durations": ("cells", "cell", "cells"), "count": ("images", "image", "images"),
       "area": ("colonies", "colony", "colonies"), "arrest": ("cells", "cell", "cells")}
MIN_JUDGED = 5     # a well or experiment needs this many cells with a known G1 outcome for a percent


def stars(p) -> str:
    if p is None or not np.isfinite(p):
        return ""
    return "****" if p < 1e-4 else "***" if p < 1e-3 else "**" if p < 0.01 else "*" if p < 0.05 else "ns"


def fmt_p(p) -> str:
    if p is None or not np.isfinite(p):
        return "–"
    if p < 1e-4:
        return "<0.0001"
    return f"{p:.4f}" if p < 0.01 else f"{p:.3f}"


def holm(ps: list[float]) -> list[float]:
    """Holm-Bonferroni adjusted p values, in the input order."""
    m = len(ps)
    order = np.argsort(ps)
    adj = np.empty(m)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * ps[i]))
        adj[i] = running
    return adj.tolist()


# ------------------------------------------------------------ tests
def _pairs(k: int, control: int | None) -> list[tuple[int, int]]:
    if control is None:
        return list(combinations(range(k), 2))
    return [(control, j) for j in range(k) if j != control]


def rank_tests(samples: list[np.ndarray], control: int | None) -> dict:
    """Kruskal-Wallis + Dunn (Holm); Mann-Whitney for two groups. Differences of medians."""
    k = len(samples)
    pairs = _pairs(k, control)
    out_pairs = []
    if k == 2:
        a, b = samples
        try:
            u, p = ss.mannwhitneyu(a, b, alternative="two-sided")
        except ValueError:
            u, p = float("nan"), 1.0
        out_pairs.append({"i": 0, "j": 1, "p": float(p), "diff": float(np.median(b) - np.median(a))})
        return {"test": "Mann-Whitney test", "stat_label": "U", "stat": float(u), "p": float(p),
                "post": None, "pairs": out_pairs, "center": "median"}
    try:
        h, p = ss.kruskal(*samples)
    except ValueError:  # all values equal
        h, p = 0.0, 1.0
    allv = np.concatenate(samples)
    n_tot = len(allv)
    ranks = ss.rankdata(allv)
    _, counts = np.unique(allv, return_counts=True)
    tie = float(((counts ** 3) - counts).sum())
    var = n_tot * (n_tot + 1) / 12.0 - tie / (12.0 * (n_tot - 1))
    bounds = np.cumsum([0] + [len(s) for s in samples])
    mean_rank = [ranks[bounds[i]:bounds[i + 1]].mean() for i in range(k)]
    raw = []
    for i, j in pairs:
        se = math.sqrt(max(var, 1e-12) * (1 / len(samples[i]) + 1 / len(samples[j])))
        z = (mean_rank[i] - mean_rank[j]) / se
        raw.append(float(2 * ss.norm.sf(abs(z))))
    for (i, j), pa in zip(pairs, holm(raw) if raw else []):
        out_pairs.append({"i": i, "j": j, "p": pa, "diff": float(np.median(samples[j]) - np.median(samples[i]))})
    return {"test": "Kruskal-Wallis test", "stat_label": f"H({k - 1})", "stat": float(h), "p": float(p),
            "post": "Dunn's multiple comparisons, Holm-adjusted", "pairs": out_pairs, "center": "median"}


def _wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def _newcombe(k1: int, n1: int, k2: int, n2: int) -> list[float]:
    """95% CI of p2 - p1 (Newcombe's hybrid score method), in percentage points."""
    l1, u1 = _wilson(k1, n1)
    l2, u2 = _wilson(k2, n2)
    p1, p2 = k1 / n1, k2 / n2
    d = p2 - p1
    return [100 * (d - math.sqrt((p2 - l2) ** 2 + (u1 - p1) ** 2)), 100 * (d + math.sqrt((u2 - p2) ** 2 + (p1 - l1) ** 2))]


def proportion_tests(counts: list[tuple[int, int]], control: int | None) -> dict:
    """counts: (yes, total) per group. Chi-square test of the k x 2 table + Holm-adjusted Fisher's
    exact tests per pair; Fisher's exact test for two groups. Differences in percentage points."""
    k = len(counts)
    pairs = _pairs(k, control)

    def fisher(i, j):
        (a, n1), (b, n2) = counts[i], counts[j]
        return float(ss.fisher_exact([[a, n1 - a], [b, n2 - b]])[1])

    out_pairs = []
    raw = [fisher(i, j) for i, j in pairs]
    adj = raw if k == 2 else holm(raw)
    for (i, j), pa in zip(pairs, adj):
        (a, n1), (b, n2) = counts[i], counts[j]
        out_pairs.append({"i": i, "j": j, "p": pa, "diff": 100.0 * (b / n2 - a / n1), "ci": _newcombe(a, n1, b, n2)})
    if k == 2:
        return {"test": "Fisher's exact test", "stat_label": "", "stat": float("nan"), "p": raw[0], "post": None,
                "pairs": out_pairs, "center": "percent"}
    table = np.array([[a, n - a] for a, n in counts], float)
    if (table.sum(axis=0) == 0).any():
        chi, p, dof = 0.0, 1.0, k - 1
    else:
        chi, p, dof, _ = ss.chi2_contingency(table)
    return {"test": "Chi-square test", "stat_label": f"χ²({dof})", "stat": float(chi), "p": float(p),
            "post": "Fisher's exact tests, Holm-adjusted", "pairs": out_pairs, "center": "percent"}


def mean_tests(samples: list[np.ndarray], control: int | None) -> dict:
    """One-way ANOVA + Tukey / Dunnett; Welch's t test for two groups. Differences of means + 95% CI."""
    k = len(samples)
    if k == 2:
        a, b = samples
        r = ss.ttest_ind(b, a, equal_var=False)
        ci = r.confidence_interval(0.95)
        return {"test": "Welch's t test", "stat_label": f"t({r.df:.1f})", "stat": float(r.statistic),
                "p": float(r.pvalue), "post": None, "center": "mean",
                "pairs": [{"i": 0, "j": 1, "p": float(r.pvalue), "diff": float(b.mean() - a.mean()),
                           "ci": [float(ci.low), float(ci.high)]}]}
    f, p = ss.f_oneway(*samples)
    n_tot = sum(len(s) for s in samples)
    out_pairs = []
    if control is None:
        res = ss.tukey_hsd(*samples)
        ci = res.confidence_interval(0.95)
        for i, j in _pairs(k, None):  # tukey's statistic[i, j] is mean_i - mean_j
            out_pairs.append({"i": i, "j": j, "p": float(res.pvalue[i, j]), "diff": float(-res.statistic[i, j]),
                              "ci": [float(-ci.high[i, j]), float(-ci.low[i, j])]})
        post = "Tukey's multiple comparisons"
    else:
        others = [j for j in range(k) if j != control]
        res = ss.dunnett(*[samples[j] for j in others], control=samples[control])
        ci = res.confidence_interval(0.95)
        for n, j in enumerate(others):
            out_pairs.append({"i": control, "j": j, "p": float(res.pvalue[n]),
                              "diff": float(samples[j].mean() - samples[control].mean()),
                              "ci": [float(ci.low[n]), float(ci.high[n])]})
        post = "Dunnett's multiple comparisons with the control"
    return {"test": "One-way ANOVA", "stat_label": f"F({k - 1}, {n_tot - k})", "stat": float(f), "p": float(p),
            "post": post, "pairs": out_pairs, "center": "mean"}


def paired_tests(mat: np.ndarray, control: int | None) -> dict:
    """Experiments x conditions. Repeated-measures ANOVA (Greenhouse-Geisser) + Holm paired t
    tests; paired t test for two conditions."""
    n, k = mat.shape
    if k == 2:
        r = ss.ttest_rel(mat[:, 1], mat[:, 0])
        ci = r.confidence_interval(0.95)
        return {"test": "Paired t test", "stat_label": f"t({n - 1})", "stat": float(r.statistic), "p": float(r.pvalue),
                "post": None, "center": "mean",
                "pairs": [{"i": 0, "j": 1, "p": float(r.pvalue), "diff": float((mat[:, 1] - mat[:, 0]).mean()),
                           "ci": [float(ci.low), float(ci.high)]}]}
    gm = mat.mean()
    ss_cond = n * ((mat.mean(axis=0) - gm) ** 2).sum()
    ss_subj = k * ((mat.mean(axis=1) - gm) ** 2).sum()
    ss_err = ((mat - gm) ** 2).sum() - ss_cond - ss_subj
    df1, df2 = k - 1, (k - 1) * (n - 1)
    f = (ss_cond / df1) / (ss_err / df2) if ss_err > 0 else float("inf")
    cov = np.cov(mat, rowvar=False)
    dc = cov - cov.mean(axis=0) - cov.mean(axis=1)[:, None] + cov.mean()
    eps = float(np.trace(dc) ** 2 / ((k - 1) * (dc ** 2).sum())) if (dc ** 2).sum() > 0 else 1.0
    eps = min(1.0, max(1.0 / (k - 1), eps))
    p = float(ss.f.sf(f, eps * df1, eps * df2)) if np.isfinite(f) else 0.0
    pairs = _pairs(k, control)
    raw, diffs, cis = [], [], []
    for i, j in pairs:
        r = ss.ttest_rel(mat[:, j], mat[:, i])
        raw.append(float(r.pvalue) if np.isfinite(r.pvalue) else 1.0)
        diffs.append(float((mat[:, j] - mat[:, i]).mean()))
        c = r.confidence_interval(0.95)
        cis.append([float(c.low), float(c.high)])
    out_pairs = [{"i": i, "j": j, "p": pa, "diff": d, "ci": c} for (i, j), pa, d, c in zip(pairs, holm(raw), diffs, cis)]
    return {"test": "Repeated-measures one-way ANOVA (Greenhouse-Geisser)", "stat_label": f"F({eps * df1:.2f}, {eps * df2:.2f})",
            "stat": float(f), "p": p, "post": "paired t tests, Holm-adjusted", "pairs": out_pairs, "center": "mean",
            "epsilon": eps}


# ------------------------------------------------------------ one comparison
def compare(groups: list[dict], unit: str, control_key: str | None, kind: str) -> dict:
    """groups: [{key, name, color, obj: values, wells: {(exp, well): value}, exps: {exp: value}}].
    unit: auto | objects | wells | experiments."""
    obj_name = OBJ[kind][0]
    groups = [g for g in groups if len(g["obj"])]
    res = {"kind": kind, "groups": [], "pairs": [], "notes": [], "ok": False}
    if len(groups) < 2:
        res["why"] = "Needs at least two conditions with data."
        return res
    n_obj = min(len(g["obj"]) for g in groups)
    n_wells = min(len(g["wells"]) for g in groups)
    n_exps = min(len(g["exps"]) for g in groups)
    res["available"] = {"objects": n_obj, "wells": n_wells, "experiments": n_exps}
    if unit == "auto":
        unit = "experiments" if n_exps >= AUTO_N else "wells" if n_wells >= AUTO_N else "objects"
    res["unit"] = unit
    if unit == "objects" and n_obj < 3:
        res["why"] = f"Needs at least three {obj_name} per condition (the smallest condition has {n_obj})."
        return res
    if unit != "objects" and {"wells": n_wells, "experiments": n_exps}[unit] < 2:
        res["why"] = (f"Needs at least two {'wells' if unit == 'wells' else 'experiments'} per condition"
                      f" (the smallest condition has {n_wells if unit == 'wells' else n_exps}).")
        return res
    keys = [g["key"] for g in groups]
    control = keys.index(control_key) if control_key in keys else None
    if unit == "objects" and kind == "arrest":
        samples = [np.asarray(g["obj"], float) for g in groups]
        out = proportion_tests([(int(sum(g["obj"])), len(g["obj"])) for g in groups], control)
        res["unit_label"] = obj_name
    elif unit == "objects":
        samples = [np.asarray(g["obj"], float) for g in groups]
        out = rank_tests(samples, control)
        res["unit_label"] = obj_name
    elif unit == "wells":
        samples = [np.asarray(list(g["wells"].values()), float) for g in groups]
        out = mean_tests(samples, control)
        res["unit_label"] = "wells"
    else:
        common = sorted(set.intersection(*[set(g["exps"]) for g in groups]))
        if len(common) >= 2 and all(set(g["exps"]) == set(common) for g in groups):
            mat = np.array([[g["exps"][e] for g in groups] for e in common], float)
            samples = [mat[:, i] for i in range(len(groups))]
            out = paired_tests(mat, control)
            res["paired"] = True
        else:
            samples = [np.asarray(list(g["exps"].values()), float) for g in groups]
            out = mean_tests(samples, control)
            res["paired"] = False
            if len(common) >= 2:
                res["notes"].append("Not every experiment has every condition, so experiments are not paired.")
        res["unit_label"] = "experiments"
    res.update({k: out[k] for k in ("test", "stat_label", "stat", "p", "post", "center")})
    if "epsilon" in out:
        res["epsilon"] = out["epsilon"]
    for g, s in zip(groups, samples):
        units = []
        if unit == "wells":
            units = [{"label": k, "v": float(v)} for k, v in g["wells"].items()]
        elif unit == "experiments":
            units = [{"label": k, "v": float(v)} for k, v in g["exps"].items() if not res.get("paired") or k in common]
        res["groups"].append({"key": g["key"], "name": g["name"], "color": g.get("color"), "n": int(len(s)), "units": units,
                              "n_objects": int(len(g["obj"])), "median": float(np.median(s)),
                              "mean": float(np.mean(s)) * (100.0 if out["center"] == "percent" else 1.0),
                              "sd": float(np.std(s, ddof=1)) if len(s) > 1 else None,
                              "sem": float(np.std(s, ddof=1) / math.sqrt(len(s))) if len(s) > 1 else None,
                              "censored": g.get("censored")})
        if kind == "arrest":
            k_, n_ = int(sum(g["obj"])), len(g["obj"])
            lo, hi = _wilson(k_, n_)
            res["groups"][-1].update({"arrested": k_, "judged": n_, "unknown": g.get("unknown", 0),
                                      "pooled": 100.0 * k_ / n_ if n_ else None, "wilson": [100 * lo, 100 * hi]})
    for pr in out["pairs"]:
        a, b = groups[pr["i"]], groups[pr["j"]]
        res["pairs"].append({"a": a["key"], "b": b["key"], "a_name": a["name"], "b_name": b["name"], "p": pr["p"],
                             "stars": stars(pr["p"]), "diff": pr["diff"], "ci": pr.get("ci")})
    res["stars"] = stars(res["p"])
    res["ok"] = True
    if unit == "objects":
        each = "Each cell is" if kind in ("durations", "arrest") else "Each image and each colony is"
        res["notes"].append(f"{each} counted as independent, but those from one well are not: with many of them even "
                            "tiny differences come out significant. Use wells or experiments for the statistics you report.")
    elif unit == "wells":
        res["notes"].append("Wells of one plate are technical replicates: this tests whether the difference holds "
                            "across wells, not whether it repeats in independent experiments.")
    return res


# ------------------------------------------------------------ data from the experiments
def _well_id(label: str, well: str, n_exps: int) -> str:
    return well if n_exps == 1 else f"{label}: {well}"


def duration_groups(exps) -> dict:
    """Per phase: conditions with every fully observed duration, per well and per experiment."""
    from . import analysis as A
    from .compare import exp_labels, groups

    labels = exp_labels(exps)
    lab = {id(e): n for e, n in zip(exps, labels)}
    out = {p: [] for p in PH}
    for grp in groups(exps):
        per = {p: {"obj": [], "wells": {}, "exps": {}, "censored": 0} for p in PH}
        exp_vals = {p: {} for p in PH}
        for e, wells in grp["members"]:
            for w in wells:
                d = A.durations_for(e, [w])
                for p in PH:
                    v = d["values"][p]
                    per[p]["obj"].extend(v)
                    per[p]["censored"] += d["censored"][p]
                    exp_vals[p].setdefault(lab[id(e)], []).extend(v)
                    if len(v) >= MIN_CELLS:
                        per[p]["wells"][_well_id(lab[id(e)], w, len(exps))] = float(np.median(v))
        for p in PH:
            per[p]["exps"] = {k: float(np.median(v)) for k, v in exp_vals[p].items() if len(v) >= MIN_CELLS}
            out[p].append({"key": grp["key"], "name": grp["name"], "color": grp["color"], **per[p]})
    return out


def arrest_groups(exps, cutoff_h: float) -> list[dict]:
    """Per condition: 1/0 for every cell whose G1 outcome is known (arrested or not), and the
    percent arrested per well and per experiment (with at least MIN_JUDGED such cells)."""
    from . import analysis as A
    from .compare import exp_labels, groups

    labels = exp_labels(exps)
    lab = {id(e): n for e, n in zip(exps, labels)}
    out = []
    for grp in groups(exps):
        obj, wells, unknown = [], {}, 0
        per_exp: dict = {}
        for e, ws in grp["members"]:
            for w in ws:
                r = A.g1_arrest_for(e, [w], cutoff_h)
                yes, no = len(r["arrested"]), len(r["not"])
                unknown += len(r["unknown"])
                obj.extend([1] * yes + [0] * no)
                tot = per_exp.setdefault(lab[id(e)], [0, 0])
                tot[0] += yes
                tot[1] += yes + no
                if yes + no >= MIN_JUDGED:
                    wells[_well_id(lab[id(e)], w, len(exps))] = 100.0 * yes / (yes + no)
        exps_pct = {k: 100.0 * a / n for k, (a, n) in per_exp.items() if n >= MIN_JUDGED}
        out.append({"key": grp["key"], "name": grp["name"], "color": grp["color"], "obj": obj, "wells": wells,
                    "exps": exps_pct, "unknown": unknown})
    return out


def colony_groups(exps, times: list[float]) -> dict:
    """Per time point: colonies per image and colony areas, per well and per experiment."""
    from .compare import _sizes, exp_labels, groups

    labels = exp_labels(exps)
    lab = {id(e): n for e, n in zip(exps, labels)}
    count = {t: [] for t in times}
    area = {t: [] for t in times}
    for grp in groups(exps):
        c_obj = {t: [] for t in times}
        c_w = {t: {} for t in times}
        c_e = {t: {} for t in times}
        a_obj = {t: [] for t in times}
        a_w = {t: {} for t in times}
        a_e = {t: {} for t in times}
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
                skip = e.excluded_frames().get(key, set())
                cols = m.get("colonies") or [None] * len(frames)
                wid = _well_id(lab[id(e)], m["well"], len(exps))
                for t in times:
                    if t > hours[-1] + 1e-6:
                        continue
                    f = int(np.argmin(np.abs(hours - t)))
                    if f in skip:
                        continue
                    if f in frames and cols[frames.index(f)] is not None:
                        v = int(cols[frames.index(f)])
                        c_obj[t].append(v)
                        c_w[t].setdefault(wid, []).append(v)
                        c_e[t].setdefault(lab[id(e)], []).append(v)
                    if f in sizes and len(sizes[f]):
                        a = np.asarray(sizes[f], float)
                        a_obj[t].extend(a.tolist())
                        a_w[t].setdefault(wid, []).extend(a.tolist())
                        a_e[t].setdefault(lab[id(e)], []).extend(a.tolist())
        for t in times:
            base = {"key": grp["key"], "name": grp["name"], "color": grp["color"]}
            count[t].append({**base, "obj": c_obj[t], "wells": {k: float(np.mean(v)) for k, v in c_w[t].items()},
                             "exps": {k: float(np.mean(v)) for k, v in c_e[t].items()}})
            area[t].append({**base, "obj": a_obj[t],
                            "wells": {k: float(np.median(v)) for k, v in a_w[t].items() if len(v) >= MIN_COLONIES},
                            "exps": {k: float(np.median(v)) for k, v in a_e[t].items() if len(v) >= MIN_COLONIES}})
    return {"count": count, "area": area}


# ------------------------------------------------------------ what the window asks for
def run(exps, what: str, unit: str = "auto", control: str | None = None, times: list[float] | None = None,
        cutoff_h: float = 22.0) -> dict:
    import warnings

    with warnings.catch_warnings():  # scipy warns about near-identical values (durations come in whole frames)
        warnings.simplefilter("ignore", RuntimeWarning)
        return _run(exps, what, unit, control, times, cutoff_h)


def _run(exps, what: str, unit: str, control: str | None, times: list[float] | None, cutoff_h: float = 22.0) -> dict:
    unit = unit if unit in ("auto", "objects", "wells", "experiments") else "auto"
    fams = []
    if what == "arrest":
        r = compare(arrest_groups(exps, cutoff_h), unit, control, "arrest")
        r.update({"id": "arrest", "title": f"G1 arrest (G1 ≥ {cutoff_h:g} h)", "units_of": "%", "cutoff_h": cutoff_h})
        fams.append(r)
    elif what == "durations":
        dg = duration_groups(exps)
        for p in PH:
            r = compare(dg[p], unit, control, "durations")
            r.update({"id": p, "title": f"{PH_LABEL[p]} duration", "units_of": "h"})
            fams.append(r)
    else:
        cg = colony_groups(exps, times or [])
        for kind, title, u in (("count", "Colonies per image", ""), ("area", "Colony area", "µm²")):
            for t in times or []:
                r = compare(cg[kind][t], unit, control, kind)
                r.update({"id": f"{kind}@{t}", "title": f"{title} at {t:g} h", "time": t, "measure": kind, "units_of": u})
                fams.append(r)
    return {"what": what, "families": fams, "methods": methods(what, fams), "control": control}


def methods(what: str, fams: list[dict]) -> str:
    """One paragraph for the methods section, from what was actually done (phases or measures that
    ended up with different units or tests are described separately)."""
    ok = [f for f in fams if f.get("ok")]
    if not ok:
        return ""
    parts, groups = [], {}
    for f in ok:
        groups.setdefault((f["unit"], f["test"], f.get("post") or ""), []).append(f)
    for (unit, test, post), fs in groups.items():
        ns = [g["n"] for f in fs for g in f["groups"]]
        nr = f"{min(ns)}" if min(ns) == max(ns) else f"{min(ns)}–{max(ns)}"
        if what == "arrest":
            cut = fs[0].get("cutoff_h", 22.0)
            thing = f"The percentage of cells in G1 arrest (in G1 for at least {cut:g} h)"
            per = {"objects": f"individual cells (n = {nr} cells per condition with a known G1 outcome)",
                   "wells": f"the percentage of each well (n = {nr} wells per condition; wells with at least {MIN_JUDGED} cells with a known outcome)",
                   "experiments": f"the percentage of each independent experiment (n = {nr} experiments)"}[unit]
        elif what == "durations":
            names = [PH_LABEL[f["id"]] for f in fs]
            thing = (", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]) + \
                (" durations" if len(names) > 1 else " duration")
            per = {"objects": f"individual cells (n = {nr} per condition)",
                   "wells": f"the median duration of each well (n = {nr} wells per condition; wells with at least {MIN_CELLS} cells)",
                   "experiments": f"the median duration of each independent experiment (n = {nr} experiments)"}[unit]
        else:
            measures = [m for m in ("count", "area") if any(f["measure"] == m for f in fs)]
            thing = " and ".join({"count": "Colony number per image", "area": "colony area"}[m] for m in measures)
            thing = thing[0].upper() + thing[1:] + " at each time point"
            per = {"objects": f"individual images and colonies (n = {nr} per condition)",
                   "wells": f"the mean colony number and median colony area of each well (n = {nr} wells per condition)",
                   "experiments": f"the mean colony number and median colony area of each independent experiment (n = {nr})"}[unit]
        lead = test[0].lower() + test[1:] if test.split()[0] in ("One-way", "Repeated-measures", "Paired") else test
        verb = "was" if what == "arrest" or (what == "durations" and " and " not in thing) else "were"
        sentence = f"{thing} {verb} compared between conditions using {per}, by {lead}{f' followed by {post}' if post else ''}."
        if unit == "objects":
            sentence += (" Cells" if what == "durations" else " Images and colonies") + \
                " from the same well were treated as independent, so these p values are descriptive."
        parts.append(sentence)
    if what == "arrest":
        cut = ok[0].get("cutoff_h", 22.0)
        parts.insert(0, f"A cell counted as G1-arrested when it was seen in G1 for at least {cut:g} h, and as not "
                        f"arrested when its whole G1 was seen and lasted less; cells whose G1 was cut off by the movie "
                        f"or that were lost before {cut:g} h were left out.")
    elif what == "durations":
        parts.insert(0, "Only phases observed from start to end within the movie were included.")
    else:
        parts.append("Each time point was tested separately.")
    parts.append("P < 0.05 was considered significant (* P < 0.05, ** P < 0.01, *** P < 0.001, **** P < 0.0001). "
                 "Analysis in Mitosight with SciPy.")
    return " ".join(parts)


def to_csv(res: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Measure", "Unit", "Test", "Statistic", "P", "Summary", "Comparison", "Difference", "95% CI low",
                "95% CI high", "Adjusted P", "Summary (pair)", "n per condition", "Note"])
    for f in res["families"]:
        if not f.get("ok"):
            w.writerow([f["title"], "", "", "", "", "", "", "", "", "", "", "", "", f.get("why", "")])
            continue
        ns = "; ".join(f"{g['name']}: {g['n']}" for g in f["groups"])
        head = [f["title"], f["unit_label"], f["test"] + (f"; {f['post']}" if f.get("post") else ""),
                f"{f['stat_label']} = {f['stat']:.3f}", fmt_p(f["p"]), f["stars"]]
        for i, pr in enumerate(f["pairs"]):
            ci = pr.get("ci") or [None, None]
            lead = head if i == 0 else [""] * len(head)
            w.writerow(lead + [
                f"{pr['b_name']} vs {pr['a_name']}", f"{pr['diff']:.4g}",
                "" if ci[0] is None else f"{ci[0]:.4g}", "" if ci[1] is None else f"{ci[1]:.4g}",
                fmt_p(pr["p"]), pr["stars"], ns if i == 0 else "", " ".join(f["notes"]) if i == 0 else ""])
    for f in res["families"]:
        if f.get("ok") and f["groups"] and "arrested" in f["groups"][0]:
            w.writerow([])
            w.writerow(["Condition", "Arrested", "Judged", "% arrested", "95% CI low", "95% CI high", "Could not be judged",
                        f"G1 for at least {f.get('cutoff_h', 22):g} h"])
            for g in f["groups"]:
                w.writerow([g["name"], g["arrested"], g["judged"], f"{g['pooled']:.2f}", f"{g['wilson'][0]:.2f}",
                            f"{g['wilson'][1]:.2f}", g["unknown"]])
            for g in f["groups"]:
                for u in g.get("units") or []:
                    w.writerow([g["name"], "", "", f"{u['v']:.2f}", "", "", "", u["label"]])
    w.writerow([])
    w.writerow(["Methods", res.get("methods", "")])
    return buf.getvalue()
