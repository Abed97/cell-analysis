"""Reading exports laid out like a real Incucyte export (names, LZW compression, 30 min frames)."""

import numpy as np
import tifffile

from pipfucci import io_incucyte as io


def write_export(folder, frames=4):
    rng = np.random.default_rng(1)
    for f in range(frames):
        mins = f * 30
        stamp = f"{mins // 1440:02d}d{(mins % 1440) // 60:02d}h{mins % 60:02d}m"
        for prefix in ("No HGF_No Osi", "Green No HGF_No Osi", "Red No HGF_No Osi"):
            img = (rng.random((104, 140)) * 3000).astype(np.uint16)
            tifffile.imwrite(folder / f"{prefix}_A1_1_{stamp}.tif", img, compression="lzw")


def test_export_like_hers(tmp_path):
    write_export(tmp_path)
    idx = io.scan_folder(tmp_path)
    assert idx["wells"] == ["A1"] and idx["n_frames"] == 4
    assert idx["suggested_interval_min"] == 30
    cmap = io.default_channel_map(idx)
    assert sorted(cmap.values()) == ["green", "phase", "red"]
    assert cmap["No HGF_No Osi"] == "phase"  # no channel word in the name -> phase
    mf = io.MovieFiles(idx, cmap)
    img = io.read_channel(mf.path(("A1", 1), 0, "red"))
    assert img.shape == (104, 140) and img.max() > 0


def test_lzw_falls_back_to_pillow_without_imagecodecs(tmp_path, monkeypatch):
    write_export(tmp_path, frames=1)
    path = next(tmp_path.glob("Red*.tif"))
    expected = tifffile.imread(path)

    def no_codec(*a, **k):
        raise ValueError("<COMPRESSION.LZW: 5> requires the 'imagecodecs' package")

    monkeypatch.setattr(tifffile, "imread", no_codec)
    got = io.read_channel(str(path))
    assert np.array_equal(got, expected.astype(np.float32))
