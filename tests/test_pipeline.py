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


def test_colonies_and_debris_filter(exp):
    from pipfucci import analysis
    from pipfucci.pipeline import object_filter

    m = next(iter(exp.meta["movies"].values()))
    assert any(n for n in m["colonies"]) and any(a for a in m["colony_area"])
    # the filter drops small specks only
    mask = np.zeros((60, 60), bool)
    mask[5:25, 5:25] = True  # 400 px object
    mask[40:42, 40:42] = True  # 4 px speck
    lab, sizes, keep = object_filter(mask, 10)
    assert keep.sum() == 1 and sizes[keep][0] == 400
    before = analysis.population(exp)["conditions"][0]
    r = exp.update_settings({"min_object_um2": 5000})
    assert r["action"] == "remeasure" and exp.stale is None
    exp.remeasure()
    after = analysis.population(exp)["conditions"][0]
    n0 = np.nanmean([v for v in before["colonies"]["mean"] if v is not None])
    n1 = np.nanmean([v for v in after["colonies"]["mean"] if v is not None] or [0])
    c0 = np.nanmean([v for v in before["confluence"]["mean"] if v is not None])
    c1 = np.nanmean([v for v in after["confluence"]["mean"] if v is not None] or [0])
    assert n1 < n0 and c1 <= c0
    exp.update_settings({"min_object_um2": 50})
    exp.remeasure()


def test_cancelled_rerun_keeps_previous_masks(exp):
    import threading

    from pipfucci.experiment import Cancelled

    mdir = exp.results_dir / "masks"
    before = sorted(p.name for p in mdir.rglob("*.npz"))
    cancel = threading.Event()
    seen = {"n": 0}

    def progress(**kw):
        if kw.get("stage") == "Segmenting" and kw.get("done", 0) >= 3:
            cancel.set()

    with pytest.raises(Cancelled):
        exp.run(progress=progress, cancel=cancel, workers=1)
    assert sorted(p.name for p in mdir.rglob("*.npz")) == before
    assert not (exp.results_dir / "masks.new").exists()
    from pipfucci import render

    w, f = exp.tracks.iloc[0]["well"], int(exp.tracks.iloc[0]["field"])
    render._masks.clear()
    assert render.masks_for(exp, w, f).cells(3) is not None


def _square(job):
    return job["x"] * job["x"]


def test_worker_pool_job_error_is_reported():
    import threading

    from pipfucci.experiment import WorkerPool

    with WorkerPool(2) as pool:
        assert pool.map(_square, [{"x": i} for i in range(5)], threading.Event()) == [0, 1, 4, 9, 16]
    with pytest.raises(KeyError):
        with WorkerPool(2) as pool:
            pool.map(_square, [{"x": 1}, {"y": 2}], threading.Event())


def _die_once(job):
    import os

    from pathlib import Path

    marker = Path(job["marker"])
    if job["x"] == 2 and not marker.exists():
        marker.write_text("x")
        os._exit(1)  # like a worker killed for running out of memory
    return job["x"] * 10


def test_worker_pool_survives_a_dead_worker(tmp_path):
    import threading

    from pipfucci.experiment import WorkerPool

    jobs = [{"x": i, "marker": str(tmp_path / "died")} for i in range(6)]
    with WorkerPool(4) as pool:
        assert pool.map(_die_once, jobs, threading.Event()) == [0, 10, 20, 30, 40, 50]
    assert (tmp_path / "died").exists()


def test_excluding_wells_and_fields(exp):
    from pipfucci import compare

    pop = analysis.population(exp)
    assert [c["wells"] for c in pop["conditions"]] == [["B2"], ["C2"]]
    exp.update_settings({"excluded": ["B2"]})
    try:
        assert exp.excluded_wells() == {"B2"}
        pop = analysis.population(exp)
        assert [c["wells"] for c in pop["conditions"]] == [["C2"]]
        assert all(exp.tracks.loc[k, "well"] == "B2" or exp.flags.get(k, {}).get("excluded")
                   for k in exp.excluded_tracks())
        rows = analysis.tracks_overview(exp)["rows"]
        assert rows and not any(r["key"].startswith("B2-") for r in rows)
        fig = compare.figures([exp])
        assert [c["name"] for c in fig["conditions"] if c["n"]] == ["Fast cycling"]
        dist = compare.colony_dist([exp])
        assert [c["name"] for c in dist["conditions"]] == ["Fast cycling"]
        # the viewer still shows the excluded field's own cells on the gate plot
        assert analysis.scatter(exp, 3, well="B2", field=1)["n"] > 0
        # a single field: here the only field, so the whole well drops out
        exp.update_settings({"excluded": ["C2_1"]})
        assert exp.excluded_wells() == {"C2"}
        assert [c["wells"] for c in analysis.population(exp)["conditions"]] == [["B2"]]
    finally:
        exp.update_settings({"excluded": []})
    assert len(analysis.population(exp)["conditions"]) == 2


def test_colony_box_plot_data(exp):
    from pipfucci import compare

    d = compare.colony_dist(exp_list := [exp], every_h=6)
    assert d["times"] == [0.0, 6.0, 12.0, 18.0, 24.0]
    for c in d["conditions"]:
        st = c["areas"][0]["stats"]
        assert st and st["n"] >= 1 and st["q1"] <= st["med"] <= st["q3"]
        assert c["counts"][0]["values"] and c["counts"][0]["labels"][0].endswith("field 1")
    assert exp_list
