"""Find and parse Incucyte image exports.

Incucyte exports one TIFF per channel per timepoint per well and field, named like
``Prefix_B2_1_02d04h30m.tif`` or ``Prefix_B2_1_2026y11m19d_14h30m.tif``. The channel is
usually in a folder name or in the prefix ("Phase", "Green", "Red"). Files are grouped by
everything that is not well/field/time, and each group is mapped to a channel.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

RESULTS_DIRNAME = "PIP-FUCCI results"

_TIME_REL = r"(?P<dd>\d{1,3})d(?P<hh>\d{1,2})h(?P<mm>\d{1,2})m"
_TIME_ABS = r"(?P<Y>\d{4})y(?P<Mo>\d{1,2})m(?P<D>\d{1,2})d_(?P<h>\d{1,2})h(?P<mi>\d{1,2})m"
_PATTERN = re.compile(
    r"(?:^|[_\-\s.])(?P<well>[A-Pa-p]0?(?:[1-9]|1[0-9]|2[0-4]))[_\-](?P<field>\d{1,3})[_\-](?:" + _TIME_ABS + "|" + _TIME_REL + ")"
)

CHANNEL_WORDS = {
    "phase": {"phase", "ph", "pc", "bright", "brightfield", "bf", "phasecontrast", "p"},
    "green": {"green", "gfp", "grn", "venus", "mvenus", "yfp", "g", "pip"},
    "red": {"red", "rfp", "mcherry", "mch", "cherry", "r", "geminin", "gem"},
}

TIFF_EXT = {".tif", ".tiff"}


@dataclass
class ImageRecord:
    path: str
    well: str
    field: int
    minutes: float
    group: str  # channel group key


def _norm_well(w: str) -> str:
    w = w.upper()
    return w[0] + str(int(w[1:]))


def parse_name(name: str):
    m = None
    for m in _PATTERN.finditer(name):
        pass  # keep the last match (prefix may contain look-alikes)
    if m is None:
        return None
    if m.group("dd") is not None:
        minutes = int(m.group("dd")) * 1440 + int(m.group("hh")) * 60 + int(m.group("mm"))
        absolute = None
    else:
        absolute = datetime(int(m.group("Y")), int(m.group("Mo")), int(m.group("D")), int(m.group("h")), int(m.group("mi")))
        minutes = None
    rest = (name[: m.start()] + "|" + name[m.end():]).strip("_- .|")
    return _norm_well(m.group("well")), int(m.group("field")), minutes, absolute, rest


def guess_channel(group_key: str) -> str | None:
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", group_key.lower()) if t]
    hits = set()
    for ch, words in CHANNEL_WORDS.items():
        if any(t in words for t in tokens):
            hits.add(ch)
    # single-letter tokens are weak evidence; only trust them when unique
    if len(hits) == 1:
        return hits.pop()
    strong = set()
    for ch, words in CHANNEL_WORDS.items():
        if any(t in words and len(t) > 1 for t in tokens):
            strong.add(ch)
    return strong.pop() if len(strong) == 1 else None


def scan_folder(folder: str | Path, max_files: int = 400000) -> dict:
    folder = Path(folder).expanduser()
    if not folder.is_dir():
        raise FileNotFoundError(f"Folder not found: {folder}")
    records: list[ImageRecord] = []
    absolute_times = []
    unparsed = []
    n = 0
    for p in sorted(folder.rglob("*")):
        if RESULTS_DIRNAME in p.parts:
            continue
        if p.suffix.lower() not in TIFF_EXT or p.name.startswith("._"):
            continue
        n += 1
        if n > max_files:
            break
        parsed = parse_name(p.stem)
        if parsed is None:
            if len(unparsed) < 10:
                unparsed.append(str(p.relative_to(folder)))
            continue
        well, fld, minutes, absolute, rest = parsed
        rel_parent = str(p.parent.relative_to(folder)) if p.parent != folder else ""
        group = (rel_parent + "/" if rel_parent else "") + (rest or "(no prefix)")
        records.append(ImageRecord(str(p), well, fld, minutes if minutes is not None else -1, group))
        absolute_times.append(absolute)

    if any(a is not None for a in absolute_times):
        t0 = min(a for a in absolute_times if a is not None)
        for r, a in zip(records, absolute_times):
            if a is not None:
                r.minutes = (a - t0).total_seconds() / 60.0

    return build_index(folder, records, unparsed)


def build_index(folder: Path, records: list[ImageRecord], unparsed: list[str]) -> dict:
    groups = defaultdict(int)
    for r in records:
        groups[r.group] += 1
    group_info = [{"key": g, "count": c, "guess": guess_channel(g)} for g, c in sorted(groups.items())]

    times = sorted({round(r.minutes, 3) for r in records})
    wells = sorted({r.well for r in records}, key=lambda w: (w[0], int(w[1:])))
    fields = defaultdict(set)
    for r in records:
        fields[r.well].add(r.field)
    interval = None
    if len(times) > 1:
        interval = float(np.median(np.diff(times)))

    image_info = {}
    issues = []
    if records:
        image_info = probe_image(records[0].path)
        if image_info.get("rgb"):
            issues.append(
                "These look like merged colour exports. Export raw single-channel images (Phase, Green, Red) "
                "from the Incucyte instead; merged colour images lose intensity information."
            )
        if image_info.get("dtype") == "uint8":
            issues.append("Images are 8-bit. 16-bit, unscaled exports keep dim cells separable from background.")
    if unparsed:
        issues.append(
            "Some TIFF files did not match the Incucyte naming pattern (Prefix_Well_Field_00d00h00m.tif) and were skipped."
        )
    if not records:
        issues.append("No Incucyte TIFF images were found in this folder.")

    return {
        "folder": str(folder),
        "n_files": len(records),
        "groups": group_info,
        "wells": wells,
        "fields": {w: sorted(f) for w, f in fields.items()},
        "n_frames": len(times),
        "times_min": times,
        "suggested_interval_min": interval,
        "image": image_info,
        "issues": issues,
        "unparsed_examples": unparsed,
        "records": [r.__dict__ for r in records],
    }


def probe_image(path: str) -> dict:
    import tifffile

    try:
        with tifffile.TiffFile(path) as tf:
            page = tf.pages[0]
            shape = page.shape
            dtype = str(page.dtype)
            spp = getattr(page, "samplesperpixel", 1)
    except Exception as e:  # pragma: no cover
        return {"error": str(e)}
    rgb = (len(shape) == 3 and shape[-1] in (3, 4)) or spp >= 3
    h, w = shape[:2]
    return {"width": int(w), "height": int(h), "dtype": dtype, "rgb": bool(rgb)}


def default_channel_map(index: dict) -> dict:
    cmap = {}
    used = set()
    for g in index["groups"]:
        guess = g["guess"]
        if guess and guess not in used:
            cmap[g["key"]] = guess
            used.add(guess)
        else:
            cmap[g["key"]] = "ignore"
    # Incucyte often names phase images with no channel word ("Plate_B2_1_..." next to
    # "Green Plate_B2_1_..." and "Red Plate_B2_1_..."): one unnamed group left -> the missing channel
    unknown = [g["key"] for g in index["groups"] if cmap[g["key"]] == "ignore" and not g["guess"]]
    missing = [c for c in ("phase", "green", "red") if c not in used]
    if len(unknown) == 1 and len(missing) == 1:
        cmap[unknown[0]] = missing[0]
    # merged colour exports: one group holds everything
    if len(index["groups"]) == 1 and index.get("image", {}).get("rgb"):
        cmap[index["groups"][0]["key"]] = "rgb"
    return cmap


class MovieFiles:
    """Lookup of file paths for one (well, field): frame -> channel -> path."""

    def __init__(self, index: dict, channel_map: dict):
        self.times = index["times_min"]
        tpos = {t: i for i, t in enumerate(self.times)}
        self.files = defaultdict(lambda: defaultdict(dict))
        for r in index["records"]:
            ch = channel_map.get(r["group"], "ignore")
            if ch == "ignore":
                continue
            f = tpos.get(round(r["minutes"], 3))
            if f is None:
                continue
            if ch == "rgb":
                for c in ("red", "green", "phase"):
                    self.files[(r["well"], r["field"])][f][c] = r["path"] + "#" + c
            else:
                self.files[(r["well"], r["field"])][f][ch] = r["path"]

    def movies(self):
        return sorted(self.files.keys(), key=lambda k: (k[0][0], int(k[0][1:]), k[1]))

    def frames(self, movie):
        return sorted(self.files[movie].keys())

    def path(self, movie, frame, channel):
        return self.files[movie].get(frame, {}).get(channel)


def _read_tiff(path: str) -> np.ndarray:
    """tifffile first (handles LZW etc. through imagecodecs); Pillow as a fallback decoder."""
    import tifffile

    try:
        return np.asarray(tifffile.imread(path))
    except Exception as first:
        try:
            from PIL import Image

            with Image.open(path) as im:
                im.seek(0)
                return np.asarray(im)
        except Exception:
            raise first from None


def read_channel(path: str | None) -> np.ndarray | None:
    if not path:
        return None
    sub = None
    if "#" in path:
        path, sub = path.rsplit("#", 1)
    try:
        img = _read_tiff(path)
    except Exception as e:
        raise RuntimeError(f"Could not read image {Path(path).name}: {e}") from None
    if img.ndim == 3 and img.shape[-1] in (3, 4):
        if sub == "red":
            img = img[..., 0]
        elif sub == "green":
            img = img[..., 1]
        else:
            img = img[..., :3].mean(axis=-1)
    elif img.ndim == 3:
        img = img[0]
    return img.astype(np.float32, copy=False)
