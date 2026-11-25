"""Statistics for the box plots: the tests against reference implementations (values checked with
scikit-posthocs and pingouin), the choice of unit, and the numbers from a real analysis."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipfucci import stats as S  # noqa: E402
from pipfucci.demo import make_demo  # noqa: E402
from pipfucci.experiment import Experiment  # noqa: E402

rng = np.random.default_rng(1)
A = np.round(rng.normal(10, 2, 40) * 2) / 2
B = np.round(rng.normal(11, 2, 35) * 2) / 2
C = np.round(rng.normal(13, 2, 30) * 2) / 2


def test_dunn_matches_scikit_posthocs():
    r = S.rank_tests([A, B, C], None)
    assert r["test"] == "Kruskal-Wallis test"
    # scikit_posthocs.posthoc_dunn(..., p_adjust="holm"): 0.107056, 0.0 (<1e-6), 0.000135
    got = [p["p"] for p in r["pairs"]]
    assert got[0] == pytest.approx(0.107056, abs=2e-6) and got[1] < 1e-6 and got[2] == pytest.approx(0.000135, abs=2e-6)


def test_repeated_measures_anova_matches_pingouin():
    g = np.random.default_rng(1)
    g.normal(size=105)
    m = np.column_stack([g.normal(10, 1, 6), g.normal(10.5, 1.5, 6), g.normal(12, 0.6, 6), g.normal(11, 2, 6)])
    m += g.normal(0, 3, (6, 1))
    r = S.paired_tests(m, None)
    # pingouin.rm_anova(correction=True): F 1.428345, eps 0.438750, p-GG 0.287802
    assert r["stat"] == pytest.approx(1.428345, abs=1e-5)
    assert r["epsilon"] == pytest.approx(0.438750, abs=1e-5)
    assert r["p"] == pytest.approx(0.287802, abs=1e-5)


def test_two_groups_and_controls():
    r = S.rank_tests([A, C], None)
    assert r["test"] == "Mann-Whitney test" and len(r["pairs"]) == 1 and r["pairs"][0]["diff"] > 0
    w = S.mean_tests([A[:5], C[:5]], None)
    assert w["test"] == "Welch's t test" and w["pairs"][0]["ci"][0] < w["pairs"][0]["diff"] < w["pairs"][0]["ci"][1]
    t = S.mean_tests([A[:6], B[:6], C[:6]], None)
    assert t["post"] == "Tukey's multiple comparisons" and len(t["pairs"]) == 3
    d = S.mean_tests([A[:6], B[:6], C[:6]], 0)
    assert d["post"].startswith("Dunnett") and [(p["i"], p["j"]) for p in d["pairs"]] == [(0, 1), (0, 2)]
    assert S.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert [S.stars(p) for p in (0.2, 0.04, 0.009, 0.0009, 0.00001)] == ["ns", "*", "**", "***", "****"]


def _groups(n_obj, n_wells, n_exps, shift=0.0):
    g = np.random.default_rng(5)
    out = []
    for i, name in enumerate(["Control", "Drug"]):
        mu = 10 + shift * i
        out.append({"key": name.lower(), "name": name, "color": "#000",
                    "obj": list(g.normal(mu, 2, n_obj)),
                    "wells": {f"W{i}{k}": float(g.normal(mu, 0.5)) for k in range(n_wells)},
                    "exps": {f"Exp {k}": float(g.normal(mu, 0.5) + k) for k in range(n_exps)}})
    return out


def test_auto_unit_prefers_independent_replicates():
    assert S.compare(_groups(200, 1, 1), "auto", None, "durations")["unit"] == "objects"
    assert S.compare(_groups(200, 4, 1), "auto", None, "durations")["unit"] == "wells"
    r = S.compare(_groups(200, 4, 3, shift=3), "auto", None, "durations")
    assert r["unit"] == "experiments" and r["paired"] and r["test"] == "Paired t test" and r["p"] < 0.05
    # every cell on its own: works but says it is descriptive
    r = S.compare(_groups(200, 1, 1), "objects", None, "durations")
    assert r["ok"] and "not" in r["notes"][0]
    # not enough wells
    r = S.compare(_groups(200, 1, 1), "wells", None, "durations")
    assert not r["ok"] and "two wells" in r["why"]


@pytest.fixture(scope="module")
def six_wells(tmp_path_factory):
    folder = tmp_path_factory.mktemp("stats")
    make_demo(folder, n_frames=37)  # B2-B4 slow, C2-C4 fast
    e = Experiment(folder)
    e.run(workers=1)
    return e


def test_statistics_from_an_analysis(six_wells):
    r = S.run([six_wells], "durations")
    sp = next(f for f in r["families"] if f["id"] == "S")
    assert sp["ok"] and sp["unit"] == "wells" and [g["n"] for g in sp["groups"]] == [3, 3]
    assert sp["test"] == "Welch's t test" and "Welch's t test" in r["methods"]
    # G1 of the slow cells (15 h) rarely fits in 18 h: too few per well, so Auto uses cells, and says so
    g1 = next(f for f in r["families"] if f["id"] == "G1")
    assert g1["ok"] and g1["unit"] in ("objects", "wells")
    cells = S.run([six_wells], "durations", unit="objects")
    sc = next(f for f in cells["families"] if f["id"] == "S")
    assert sc["test"] == "Mann-Whitney test" and sc["groups"][0]["n"] > 10
    col = S.run([six_wells], "colonies", times=[0.0, 12.0])
    assert [f["id"] for f in col["families"]] == ["count@0.0", "count@12.0", "area@0.0", "area@12.0"]
    assert all(f["ok"] and f["unit"] == "wells" for f in col["families"])
    csv = S.to_csv(r)
    assert csv.startswith("Measure,Unit,Test") and "Methods" in csv


def test_endpoint_saves_the_table(six_wells):
    from pipfucci import server

    server.STATE.exp = six_wells
    r = server.box_stats({"what": "durations", "unit": "auto", "save": "1"})
    assert Path(r["saved"]).exists() and r["families"][0]["ok"]
    r = server.box_stats({"what": "colonies", "every": "12", "save": "1", "folder": "../../etc"})
    assert Path(r["saved"]).parent.name == ".._.._etc" or ".." not in Path(r["saved"]).parent.name
    assert six_wells.results_dir in Path(r["saved"]).parents
