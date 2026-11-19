import sys
from pathlib import Path

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
    make_demo(folder, wells={"B2": "slow", "C2": "fast"}, n_frames=25)
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
    assert r["seg_recall"] > 0.95
    assert r["seg_precision"] > 0.93
    assert r["phase_smoothed_acc"] > 0.9
    assert r["link_acc"] > 0.95
    assert r["division_recall"] > 0.6


def test_outputs(exp):
    pop = analysis.population(exp)
    assert len(pop["conditions"]) == 2
    c = pop["conditions"][0]
    assert len(c["G1"]["mean"]) == len(pop["hours"]) == 25
    ov = analysis.tracks_overview(exp, condition="c1")
    assert ov["rows"] and len(ov["rows"][0]["states"]) == 25
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
