import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from gt_eval import evaluate  # noqa: E402

from pipfucci import analysis  # noqa: E402
from pipfucci.demo import make_demo  # noqa: E402
from pipfucci.experiment import Experiment  # noqa: E402
from pipfucci.io_incucyte import parse_name  # noqa: E402


@pytest.fixture(scope="module")
def exp(tmp_path_factory):
    folder = tmp_path_factory.mktemp("demo")
    make_demo(folder, wells={"B2": "slow", "C2": "fast"}, n_frames=49)
    e = Experiment(folder)
    e.run(workers=1)
    return e


def test_parse_names():
    assert parse_name("VID1_B2_1_02d04h30m")[:3] == ("B2", 1, 2 * 1440 + 4 * 60 + 30)
    w, f, m, a, rest = parse_name("Plate 1_C10_3_2026y11m19d_14h30m")
    assert (w, f, m) == ("C10", 3, None) and a.hour == 14
    assert parse_name("random.tif") is None


def test_accuracy_on_synthetic(exp):
    r = evaluate(exp, exp.folder / "_ground_truth" / "cells.csv")
    # demo data has green autofluorescence, colourless spells and 30 min frames, like her movies
    assert r["seg_recall"] > 0.95
    assert r["seg_precision"] > 0.93
    assert r["phase_smoothed_acc"] > 0.88
    assert r["link_acc"] > 0.95
    assert r["track_completeness"] > 0.7
    assert r["division_recall"] > 0.5


def test_outputs(exp):
    pop = analysis.population(exp)
    assert len(pop["conditions"]) == 2
    c = pop["conditions"][0]
    assert len(c["G1"]["mean"]) == len(pop["hours"]) == 49
    ov = analysis.tracks_overview(exp, condition="c1")
    assert ov["rows"] and len(ov["rows"][0]["states"]) == 49
    key = ov["rows"][0]["key"]
    d = analysis.track_detail(exp, key)
    assert d["frames"] and d["durations"]["segments"]
    dur = analysis.durations_by_condition(exp)
    slow, fast = dur["conditions"]
    assert slow["median"]["G1"] > fast["median"]["G1"]  # slow cycling has the longer G1


def test_gate_change_reclassifies(exp):
    before = exp.cells["raw"].copy()
    exp.update_settings({"gate_green": 0.9})
    assert (exp.cells["raw"] != before).any()
    exp.update_settings({"gate_green": None})


def test_retrack(exp):
    exp.update_settings({"max_speed_um_h": 15})
    assert exp.stale == "tracking"
    exp.retrack()
    assert exp.stale is None and len(exp.tracks) > 0


def test_lineage_and_frame_cells(exp):
    parents = exp.meta["parents"]
    child = next(iter(parents))
    lin = analysis.lineage(exp, child)
    keys = {n["key"] for n in lin["nodes"]}
    assert parents[child] in keys and child in keys
    root = next(n for n in lin["nodes"] if n["key"] == lin["root"])
    assert root["parent"] == "" or root["parent"] not in keys
    t = exp.tracks.loc[child]
    fc = analysis.frame_cells(exp, t["well"], int(t["field"]), int(t["start"]))
    assert any(c["key"] == child for c in fc["cells"])


def test_viewer_layers(exp):
    from pipfucci import render

    w, f = exp.tracks.iloc[0]["well"], int(exp.tracks.iloc[0]["field"])
    plain = render.composite(exp, w, f, 5, {"phase", "red", "green"})
    bg = render.composite(exp, w, f, 5, {"phase", "red", "green", "bgsub"})
    conf = render.composite(exp, w, f, 5, {"phase", "confluence"})
    assert plain.shape == bg.shape == conf.shape
    # the autofluorescent haze in the demo is removed: less green in the background-subtracted view
    assert bg[..., 1].mean() < plain[..., 1].mean()


def test_figures_and_comparison(exp, tmp_path):
    from pipfucci import compare

    fig = compare.figures([exp])
    assert [c["name"] for c in fig["conditions"]] == ["Slow cycling", "Fast cycling"]
    for c in fig["conditions"]:
        assert c["n"] > 20
        comp = np.array([c["composition"][k] for k in compare.CAT_NAMES], dtype=float)
        assert np.allclose(comp.sum(axis=0), 100.0)
        assert c["composition"]["Unclassified"][0] == 0.0  # everyone is classified at t = 0
        assert c["durations"]["G1"]
    # a second experiment with one matching condition and one new one
    other = tmp_path / "other"
    make_demo(other, wells={"B2": "fast"}, n_frames=12, shape=(220, 280))
    o = Experiment(other)
    o.update_settings({"conditions": [{"id": "c1", "name": "fast  CYCLING", "color": "#000000"}], "wells": {"B2": "c1"}})
    o.run(workers=1)
    pop = compare.population([exp, o])
    names = [c["name"] for c in pop["conditions"]]
    assert names == ["Slow cycling", "Fast cycling"]
    fast = pop["conditions"][1]
    assert len(fast["wells"]) == 2  # pooled by name, wells are the replicates
    assert len(pop["hours"]) == 49
    # figure style: order, colour, dashed, hidden
    exp.update_settings({"styles": {"fast cycling": {"order": 0, "dashed": True, "color": "#123456"},
                                    "slow cycling": {"order": 1, "hidden": True}}})
    fig = compare.figures([exp, o])
    shown = [c for c in fig["conditions"] if not c["hidden"]]
    assert [c["name"] for c in shown] == ["Fast cycling"] and shown[0]["dashed"] and shown[0]["color"] == "#123456"
    exp.update_settings({"styles": {}})


def test_export_has_colourless_and_figure_data(exp):
    from pipfucci.export import export_all

    out = export_all(exp)
    assert (out / "Prism tables" / "Phase fraction Colourless (%).csv").exists()
    assert list((out / "Figure data").glob("Composition Slow cycling*.csv"))
    import pandas as pd

    cells = pd.read_csv(out / "Cells per frame.csv", nrows=50)
    assert {"phase_call", "position_interpolated"} <= set(cells.columns)
