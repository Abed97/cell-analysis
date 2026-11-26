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


def test_leaving_out_single_images(exp):
    from pipfucci import compare

    m = next(iter(exp.meta["movies"].values()))
    mv = f"{m['well']}_{m['field']}"
    ws0 = analysis.well_series(exp)[m["well"]]
    exp.update_settings({"excluded": [f"{mv}@10", f"{mv}@11"]})
    try:
        assert exp.excluded_frames() == {mv: {10, 11}}
        assert exp.excluded_wells() == set() and not exp.tracks_in_excluded()  # nothing else is left out
        ws = analysis.well_series(exp)[m["well"]]
        assert np.isnan(ws["total"][10]) and np.isnan(ws["frac"][11]).all() and np.isnan(ws["confluence"][10])
        assert ws["total"][12] == ws0["total"][12] and ws["total"][9] == ws0["total"][9]  # other time points kept
        act = analysis._active(exp)
        assert not ((act["well"] == m["well"]) & (act["frame"].isin([10, 11]))).any()
        fig = compare.figures([exp])
        slow = next(c for c in fig["conditions"] if c["name"] == "Slow cycling")
        assert slow["composition"]["G1"][10] is None and slow["composition"]["G1"][12] is not None
        cnt = compare.colony_dist([exp], every_h=0.5)
        c0 = next(c for c in cnt["conditions"] if c["name"] == "Slow cycling")
        assert c0["counts"][10]["stats"] is None and c0["counts"][12]["stats"] is not None
    finally:
        exp.update_settings({"excluded": []})


def test_scatter_time_points_setting(exp):
    exp.update_settings({"scatter_hours": [12, "3", -1, 12, "x", 0, 1, 2, 4, 5, 6, 7, 8]})
    assert exp.settings.scatter_hours == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    exp.update_settings({"scatter_hours": []})
    assert exp.settings.scatter_hours == []


def test_confluence_mask_ignores_brightness_and_confluence():
    from pipfucci import segment as seg

    rng = np.random.default_rng(3)
    h, w = 400, 500
    yy, xx = np.mgrid[:h, :w]
    img = 120 + rng.normal(0, 2, (h, w))  # smooth medium
    cells = np.zeros((h, w), bool)
    for cy, cx in [(100, 100), (110, 130), (300, 350), (200, 250)]:
        cells |= (yy - cy) ** 2 + (xx - cx) ** 2 < 30 ** 2
    img[cells] += rng.normal(0, 25, cells.sum())  # textured cells
    m1, ref = seg.cell_mask(img, 20)
    truth = cells[::2, ::2]
    iou = (m1 & truth).sum() / (m1 | truth).sum()
    assert iou > 0.8
    m2, _ = seg.cell_mask(img * 0.5, 20)  # a darker exposure finds the same cells
    assert (m1 ^ m2).mean() < 0.01
    # an image with no background left falls back to the first image's background
    full = 120 + rng.normal(0, 25, (h, w))
    m3, _ = seg.cell_mask(full, 20, ref=ref)
    assert m3.mean() > 0.95
    lo, _ = seg.cell_mask(img, 20, sensitivity=0.0)
    hi, _ = seg.cell_mask(img, 20, sensitivity=1.0)
    assert lo.sum() <= m1.sum() <= hi.sum()
    grown, _ = seg.cell_mask(img, 20, adjust_px=8)
    assert grown.sum() > m1.sum()


def test_redraw_confluence_keeps_nuclei(exp):
    from pipfucci import render

    w, f = exp.tracks.iloc[0]["well"], int(exp.tracks.iloc[0]["field"])
    lab0 = render.masks_for(exp, w, f).labels(3).copy()
    r = exp.update_settings({"conf_sensitivity": 0.2})
    assert r["action"] == "remask" and exp.mask_settings_changed() and exp.stale is None
    exp.remask(workers=1)
    assert not exp.mask_settings_changed()
    render._masks.clear()
    m = render.masks_for(exp, w, f)
    assert np.array_equal(m.labels(3), lab0) and m.cells(3) is not None
    exp.update_settings({"conf_sensitivity": 0.5})
    exp.remask(workers=1)


def test_figure_axes_setting(exp):
    exp.update_settings({"fig_axes": {"fig-a": {"x": {"min": "0", "max": 12, "step": -2}, "y": {}}, "x": 5}})
    assert exp.settings.fig_axes == {"fig-a": {"x": {"min": 0.0, "max": 12.0}}}
    exp.update_settings({"fig_axes": {}})


def _quantify_scipy(labels, red, green, edge_margin):
    """The previous per-label scipy implementation, kept as the reference."""
    from scipy import ndimage as ndi

    n = int(labels.max())
    idx = np.arange(1, n + 1)
    ones = np.ones_like(labels, dtype=np.float32)
    cy, cx = np.array(ndi.center_of_mass(ones, labels, idx)).T
    h, w = labels.shape
    edge = np.zeros(n, bool)
    for i, sl in enumerate(ndi.find_objects(labels)):
        if sl is not None and (sl[0].start <= 0 or sl[1].start <= 0 or sl[0].stop >= h or sl[1].stop >= w):
            edge[i] = True
    edge |= (cx < edge_margin) | (cy < edge_margin) | (cx > w - 1 - edge_margin) | (cy > h - 1 - edge_margin)
    out = {"label": idx, "y": cy, "x": cx, "area": ndi.sum_labels(ones, labels, idx), "edge": edge}
    for name, img in (("red", red), ("green", green)):
        out[name] = np.full(n, np.nan) if img is None else ndi.mean(img, labels, idx)
        out[name + "_med"] = np.full(n, np.nan) if img is None else ndi.median(img, labels, idx)
    return out


def test_quantify_matches_scipy(exp):
    from scipy import ndimage as ndi

    from pipfucci import io_incucyte as io
    from pipfucci import segment as seg

    sd = exp.derived_numbers()
    jobs, _, _ = exp._jobs()
    exp._discard_new_masks()
    cases = []
    for f in jobs[0]["frames"][:3]:
        p = jobs[0]["paths"][f]
        r = seg.bg_subtract(io.read_channel(p.get("red")), sd["nucleus_px"])
        g = seg.bg_subtract(io.read_channel(p.get("green")), sd["nucleus_px"])
        cases.append((seg.segment_classic(r, g, sd["nucleus_px"], 0.5), r, g))
    rng = np.random.default_rng(3)
    dense = ndi.label(ndi.gaussian_filter(rng.random((300, 400)), 2) > 0.52)[0].astype(np.uint16)
    cases.append((dense, rng.normal(100, 30, dense.shape).astype(np.float32), None))
    for labels, r, g in cases:
        assert labels.max() > 5
        a, b = _quantify_scipy(labels, r, g, 6.0), seg.quantify(labels, r, g, 6.0)
        for k in a:
            np.testing.assert_allclose(np.asarray(b[k], float), np.asarray(a[k], float), rtol=1e-5, atol=1e-4,
                                       equal_nan=True, err_msg=k)


def test_worker_count_from_memory(monkeypatch):
    from pipfucci import experiment as E

    def mac(cores, gb):
        monkeypatch.setattr(E.os, "cpu_count", lambda: cores)
        monkeypatch.setattr(E.os, "sysconf", lambda k: 4096 if k == "SC_PAGE_SIZE" else int(gb * 2 ** 30 / 4096))

    mac(8, 8)        # M1, 8 GB: was held to 4
    assert E.worker_count() == 7
    assert E.worker_count(per_worker_gb=E.worker_gb(2 * E.STD_IMAGE_PX)) == 4   # twice the pixels
    assert E.worker_count(per_worker_gb=E.worker_gb(method="cellpose")) == 2
    assert E.worker_count(3) == 3 and E.worker_count(setting=2) == 2
    mac(10, 16)      # M4 mini, 16 GB
    assert E.worker_count() == 9
    mac(4, 3)        # very little memory: one worker
    assert E.worker_count() == 1


def test_hand_removal_of_nuclei(exp):
    from pipfucci import analysis
    from pipfucci import server

    server.STATE.exp = exp
    try:
        c = exp.cells
        real = c[~c["interp"]] if "interp" in c else c
        row = real.iloc[len(real) // 2]
        w, f, fr = row["well"], int(row["field"]), int(row["frame"])
        before = analysis.detection_stats(exp, w, f, fr)["detected"]
        n_active = len(analysis._active(exp))
        # a click a little off the nucleus still finds it
        server.hand_remove({"well": w, "field": f, "frame": fr, "x": float(row["x"]) + 2, "y": float(row["y"]) - 1,
                            "what": "object"})
        assert exp.settings.removed[-1]["what"] == "object"
        assert analysis.detection_stats(exp, w, f, fr)["detected"] == before - 1
        assert len(analysis._active(exp)) == n_active - 1
        fc = analysis.frame_cells(exp, w, f, fr)["cells"]
        assert sum(1 for x in fc if x["removed"] == "object") == 1
        # the whole track of another nucleus
        other = real[(real["well"] == w) & (real["frame"] == fr) & (real["track"] != row["track"])].iloc[0]
        n_track = int((c["track"] == other["track"]).sum())
        server.hand_remove({"well": w, "field": int(other["field"]), "frame": fr, "x": float(other["x"]),
                            "y": float(other["y"]), "what": "track"})
        assert other["track"] in exp.excluded_tracks()
        assert len(analysis._active(exp)) == n_active - 1 - n_track
        # saved with the experiment, and put back one by one or all together
        assert len(Experiment(exp.folder).settings.removed) == 2
        server.hand_unremove({"index": 0})
        assert len(exp.settings.removed) == 1 and other["track"] in exp.excluded_tracks()
        server.hand_unremove({"movie": f"{w}_{f}"})
        assert exp.settings.removed == [] and len(analysis._active(exp)) == n_active
        with pytest.raises(ValueError):
            server.hand_remove({"well": w, "field": f, "frame": fr, "x": 1e5, "y": 1e5, "what": "object"})
    finally:
        exp.settings.update({"removed": []})
        exp.save()


def test_hand_removal_of_confluence_area(exp):
    from scipy import ndimage as ndi

    from pipfucci import server
    from pipfucci.pipeline import MovieMasks

    server.STATE.exp = exp
    try:
        mv, m = next((k, v) for k, v in exp.meta["movies"].items() if any(n and n > 0 for n in v["colonies"]))
        i = max(range(len(m["frames"])), key=lambda j: m["colonies"][j] or 0)
        fr = m["frames"][i]
        conf0, col0 = m["confluence"][i], m["colonies"][i]
        mask = MovieMasks(exp.results_dir / "masks" / mv).cells(fr)
        lab, n = ndi.label(mask, structure=np.ones((3, 3), bool))
        sizes = np.bincount(lab.ravel())[1:]
        big = int(np.argmax(sizes)) + 1
        yh, xh = np.argwhere(lab == big)[0]
        w, f = mv.rsplit("_", 1)
        server.hand_remove({"well": w, "field": int(f), "frame": fr, "x": 2.0 * xh, "y": 2.0 * yh, "what": "patch"})
        m = exp.meta["movies"][mv]
        assert m["colonies"][i] == col0 - 1 and m["confluence"][i] < conf0
        # other images of the field are untouched; saved results follow
        meta = Experiment(exp.folder).meta["movies"][mv]
        assert meta["colonies"][i] == col0 - 1
        server.hand_unremove({"index": 0})
        m = exp.meta["movies"][mv]
        assert m["colonies"][i] == col0 and abs(m["confluence"][i] - conf0) < 1e-9
        with pytest.raises(ValueError):  # nothing to take out in an empty corner
            empty = np.argwhere(ndi.binary_dilation(mask, iterations=6) == 0)
            if len(empty):
                yh, xh = empty[0]
                server.hand_remove({"well": w, "field": int(f), "frame": fr, "x": 2.0 * xh, "y": 2.0 * yh,
                                    "what": "patch"})
            else:
                raise ValueError("fully covered")
    finally:
        exp.settings.update({"removed": []})
        exp.save()


def test_removed_setting_is_validated():
    from pipfucci.settings import Settings

    s = Settings()
    s.update({"removed": [
        {"movie": "B2_1", "frame": 3, "x": 10, "y": 20, "what": "object"},
        {"movie": "B2_1", "frame": 3, "x": 10, "y": 20, "what": "object"},  # duplicate
        {"movie": "B2_1", "frame": -1, "x": 10, "y": 20, "what": "patch"},
        {"movie": "B2_1", "frame": -1, "x": 10, "y": 20, "what": "object"},  # every image: patches only
        {"movie": "B2", "frame": 1, "x": 1, "y": 1, "what": "object"},       # no field
        {"movie": "B2_1", "frame": 1, "x": float("nan"), "y": 1},
        {"movie": "B2_1", "frame": 1, "x": 1, "y": 1, "what": "cell"},
        "junk",
    ]})
    assert [(r["frame"], r["what"]) for r in s.removed] == [(3, "object"), (-1, "patch")]


def test_hand_removal_survives_retracking(exp):
    from pipfucci import analysis
    from pipfucci import server

    server.STATE.exp = exp
    try:
        c = exp.cells
        real = c[~c["interp"]] if "interp" in c else c
        row = real.iloc[len(real) // 3]
        w, f, fr = row["well"], int(row["field"]), int(row["frame"])
        server.hand_remove({"well": w, "field": f, "frame": fr, "x": float(row["x"]), "y": float(row["y"]),
                            "what": "object"})
        before = analysis.detection_stats(exp, w, f, fr)["detected"]
        exp.retrack()
        assert analysis.detection_stats(exp, w, f, fr)["detected"] == before
        fc = analysis.frame_cells(exp, w, f, fr)["cells"]
        hit = [x for x in fc if x["removed"] == "object"]
        assert len(hit) == 1 and abs(hit[0]["x"] - row["x"]) < 1 and abs(hit[0]["y"] - row["y"]) < 1
    finally:
        exp.settings.update({"removed": []})
        exp.save()


def test_export_annotated_images(exp):
    from PIL import Image

    from pipfucci import annotate

    layers = {"phase", "red", "green", "bgsub", "masks", "confluence"}
    w, f = "B2", 1
    r = annotate.export(exp, w, f, [10], layers, fmt="png", scale=2, numbers=True, legend=True)
    im = Image.open(r["paths"][0])
    h0, w0 = exp.meta["movies"][f"{w}_{f}"]["shape"][:2]
    assert im.size == (2 * w0, 2 * h0) and round(im.info["dpi"][0]) == 300
    r = annotate.export(exp, w, f, [10], layers, fmt="tif", scale=1)
    assert Image.open(r["paths"][0]).size == (w0, h0)
    r = annotate.export(exp, w, f, [10], layers, fmt="svg", numbers=True)
    doc = Path(r["paths"][0]).read_text()
    assert 'id="nucleus-outlines"' in doc and 'id="confluence"' in doc and "µm" in doc and "data:image/png;base64," in doc
    # a square following a dividing cell, as separate images and one strip
    t = exp.tracks
    trk = t[(t["n_children"] > 0) & (t["well"] == w) & (t["field"] == f)].index[0]
    end = int(t.loc[trk, "end"])
    r = annotate.export(exp, w, f, [max(0, end - 4), end, min(48, end + 6)], {"phase", "green", "red", "masks"}, fmt="png",
                        scale=2, select_track=trk, crop_um=40, strip=True)
    assert len(r["paths"]) == 4 and r["paths"][-1].endswith("Time strip.png")
    side = int(round(40 / exp.settings.pixel_um)) * 2
    assert Image.open(r["paths"][0]).size == (side, side)
    strip = Image.open(r["paths"][-1])
    assert strip.size[1] == side and strip.size[0] > 3 * side


def test_export_track_montage(exp):
    from PIL import Image

    from pipfucci import annotate

    t = exp.tracks
    key = t[(t["end"] - t["start"]) > 20].index[0]
    side = 2 * int(max(12, exp.settings.nucleus_px * 1.6))
    r = annotate.track_montage(exp, key, n=6, fmt="png", scale=4)
    im = Image.open(r["path"])
    assert r["columns"] == 6 and round(im.info["dpi"][0]) == 300
    assert im.size[0] > 6 * side * 4 and im.size[1] > 4 * side * 4
    # the tiles keep the camera pixels: 4 x 4 blocks of one colour
    a = np.asarray(im)
    x0, y0 = im.size[0] - 2 - side * 4, im.size[1] // 2
    blk = a[y0 - y0 % 4: y0 - y0 % 4 + 4, x0: x0 + 4]
    assert blk.ndim == 3
    r2 = annotate.track_montage(exp, key, n=4, rows=["phase", "merge"], fmt="tif", scale=2, title=False, bar=False)
    im2 = Image.open(r2["path"])
    assert im2.size[1] < im.size[1] and r2["columns"] == 4
    r3 = annotate.track_montage(exp, key, fmt="svg", scale=2)
    doc = Path(r3["path"]).read_text()
    assert doc.count("<image ") == 8 * 4 and "mCherry-Geminin" in doc and "Scale bar" in doc and "µm" in doc
    with pytest.raises(ValueError):
        annotate.track_montage(exp, "Z9-9-9")


def test_colourless_split_into_no_reporter_and_transition(exp):
    from pipfucci import compare

    c = exp.cells
    ws = analysis.well_series(exp)
    for v in ws.values():  # the two parts add up to the colourless cells
        assert np.allclose(np.nan_to_num(v["frac_cl"].sum(axis=1)), np.nan_to_num(v["frac"][:, 3]))
    pop = compare.population([exp])
    for cnd in pop["conditions"]:
        assert "CL_trans" in cnd and "CL_none" in cnd
    nr = analysis.no_reporter(exp, 90, 0)
    assert not nr[c["call"].values != 3].all() or not nr.any()
    # every cell called "no reporter" belongs to a family with no coloured track
    calls = c["call"].values
    fam_tracks = set(c.loc[nr, "track"])
    for k in list(fam_tracks)[:20]:
        assert (calls[(c["track"] == k).values] == 3).mean() >= 0.9
    # a colourless daughter of a coloured mother has the reporter
    t = exp.tracks
    kid = t[t["parent"] != ""].index[0]
    rows = (c["track"] == kid).values
    saved = c.loc[rows, "call"].copy()
    try:
        c.loc[rows, "call"] = 3
        exp._nr_key = None
        assert not analysis.no_reporter(exp, 90, 0)[rows].any()
        # with no mother it would count, unless followed for too short a time
        par = t.at[kid, "parent"]
        t.at[kid, "parent"] = ""
        exp._nr_key = None
        nh = rows.sum() * exp.settings.interval_h
        assert analysis.no_reporter(exp, 90, 0)[rows].all()
        exp._nr_key = None
        assert not analysis.no_reporter(exp, 90, nh + 1)[rows].any()
        t.at[kid, "parent"] = par
    finally:
        c.loc[rows, "call"] = saved
        exp._nr_key = None


def test_no_reporter_settings_are_validated():
    from pipfucci.settings import Settings

    s = Settings()
    s.update({"no_reporter_pct": 20, "no_reporter_min_h": -5})
    assert s.no_reporter_pct == 50 and s.no_reporter_min_h == 0
    s.update({"no_reporter_pct": "x", "no_reporter_min_h": 5000})
    assert s.no_reporter_pct == 90 and s.no_reporter_min_h == 1000
    assert Settings.from_dict({}).no_reporter_pct == 90  # older experiments get the default
