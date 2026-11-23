"""Experiment settings. Frame interval and pixel size are inputs, never constants:
every time- and distance-dependent parameter is derived from them here."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

DEFAULT_COLORS = ["#7AB0FF", "#F59E5B", "#C59BFF", "#5FD4C4", "#F27FB2", "#B8C46A", "#A0A8B2"]


REMOVED_KINDS = ("object", "track", "patch")
MAX_REMOVED = 20000


def clean_removed(items) -> list:
    """Valid, de-duplicated hand removals, in the order they were made."""
    out, seen = [], set()
    for r in items if isinstance(items, list) else []:
        if not isinstance(r, dict):
            continue
        try:
            mv = str(r["movie"])
            w, _, f = mv.rpartition("_")
            fr, x, y = int(r["frame"]), float(r["x"]), float(r["y"])
            what = str(r.get("what", "object"))
        except Exception:
            continue
        if not (w and f.isdigit() and fr >= -1 and what in REMOVED_KINDS
                and math.isfinite(x) and math.isfinite(y) and x >= 0 and y >= 0):
            continue
        if what != "patch" and fr < 0:
            continue
        e = {"movie": mv, "frame": fr, "x": round(x, 1), "y": round(y, 1), "what": what}
        k = (mv, fr, e["x"], e["y"], what)
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out[-MAX_REMOVED:]


def _excluded_order(x: str):
    """B2 < B2_1 < B2_1@3 < B2_1@12 < B10."""
    mv, _, fr = x.partition("@")
    well, _, fld = mv.partition("_")
    num = "".join(ch for ch in well[1:] if ch.isdigit())
    return (well[:1], int(num or 0), int(fld) if fld.isdigit() else -1, int(fr) if fr.isdigit() else -1, x)


@dataclass
class Settings:
    interval_min: float = 60.0
    pixel_um: float = 0.62
    nucleus_um: float = 12.0
    max_speed_um_h: float = 30.0
    max_gap_h: float = 2.0
    colourless_h: float = 12.0  # follow cells through stretches with neither reporter on, up to this long
    min_object_um2: float = 50.0  # confluence mask: objects smaller than this are debris, not cells
    conf_sensitivity: float = 0.5  # confluence mask: 0..1, higher finds fainter cells
    conf_bridge_um: float | None = None  # confluence mask: join gaps up to this size (None: half a nucleus)
    conf_adjust_um: float = 0.0  # confluence mask: grow (+) or shrink (-) the edge
    bg_radius_um: float | None = None  # top-hat background radius; None = one nucleus diameter
    smoothing_h: float = 1.0
    sensitivity: float = 0.5  # segmentation: 0 = strict, 1 = picks up dim cells
    method: str = "classic"  # "classic" or "cellpose"
    # channel group key -> "phase" | "green" | "red" | "ignore"
    channel_map: dict = field(default_factory=dict)
    conditions: list = field(default_factory=list)  # [{"id","name","color","dashed"}]
    wells: dict = field(default_factory=dict)  # well -> condition id
    parental_well: str | None = None
    gate_green: float | None = None  # in normalised units
    gate_red: float | None = None
    experiment_name: str = ""
    linked: list = field(default_factory=list)  # other analysed experiment folders shown alongside this one
    # left out of results (blurry): a well "B2", a field "B2_1" (every time point) or one image "B2_1@12" (frame 12)
    excluded: list = field(default_factory=list)
    # removed by hand in the viewer, by position (so they survive a re-run or re-tracking):
    # {"movie": "B2_1", "frame": 12 (-1 = every image), "x": px, "y": px, "what": object|track|patch}
    removed: list = field(default_factory=list)
    scatter_hours: list = field(default_factory=list)  # time points of the red-vs-green plots; empty = automatic
    fig_axes: dict = field(default_factory=dict)  # figure axes set by hand: {figure: {axis: {min, max, step}}}
    styles: dict = field(default_factory=dict)  # figure style per condition name: color, dashed, order, hidden

    # ---------- derived ----------
    @property
    def interval_h(self) -> float:
        return self.interval_min / 60.0

    @property
    def nucleus_px(self) -> float:
        return self.nucleus_um / self.pixel_um

    @property
    def search_radius_px(self) -> float:
        # speed x interval / pixel size, never smaller than one nucleus diameter
        return max(self.max_speed_um_h * self.interval_h / self.pixel_um, 1.0 * self.nucleus_px)

    @property
    def conf_bridge_px(self) -> float:
        um = self.conf_bridge_um if self.conf_bridge_um else self.nucleus_um / 2
        return um / self.pixel_um

    def mask_params(self) -> dict:
        """What the confluence masks depend on (besides the images)."""
        return {"sens": round(self.conf_sensitivity, 3), "bridge_px": round(self.conf_bridge_px, 2),
                "adjust_px": round(self.conf_adjust_um / self.pixel_um, 2), "nucleus_px": round(self.nucleus_px, 2)}

    @property
    def bg_radius_px(self) -> float:
        um = self.bg_radius_um if self.bg_radius_um else self.nucleus_um
        return max(um / self.pixel_um, 0.75 * self.nucleus_px)

    @property
    def colourless_frames(self) -> int:
        return max(0, int(round(self.colourless_h / self.interval_h)))

    @property
    def daughter_radius_px(self) -> float:
        # at long intervals there is no rounded mitotic frame, so daughters can be further away
        factor = 1.5 if self.interval_min <= 30 else 2.0
        return max(self.search_radius_px * factor, 1.6 * self.nucleus_px)

    @property
    def max_gap_frames(self) -> int:
        return max(0, int(round(self.max_gap_h / self.interval_h)))

    @property
    def smoothing_frames(self) -> int:
        return max(1, int(round(self.smoothing_h / self.interval_h)))

    @property
    def edge_margin_px(self) -> float:
        return 0.5 * self.nucleus_px

    def warnings(self) -> list[str]:
        out = []
        if self.interval_min > 60:
            out.append("Frame interval is above 60 min. Phase calls and division detection get unreliable; 30 to 60 min is recommended.")
        if self.nucleus_px < 10:
            out.append("Nuclei will be under 10 px across at this pixel size, so dim transition cells will be hard to segment.")
        if self.max_gap_frames > 4:
            out.append("A max gap of more than 4 frames can join tracks that belong to different cells.")
        return out

    def derived(self) -> dict:
        return {
            "interval_h": self.interval_h,
            "nucleus_px": round(self.nucleus_px, 1),
            "search_radius_px": round(self.search_radius_px, 1),
            "daughter_radius_px": round(self.daughter_radius_px, 1),
            "max_gap_frames": self.max_gap_frames,
            "colourless_frames": self.colourless_frames,
            "bg_radius_px": round(self.bg_radius_px, 1),
            "bg_radius_um": round(self.bg_radius_px * self.pixel_um, 1),
            "min_object_px": round(self.min_object_um2 / self.pixel_um ** 2),
            "conf_bridge_um": round(self.conf_bridge_px * self.pixel_um, 1),
            "min_object_diam_um": round(2 * math.sqrt(self.min_object_um2 / math.pi), 1),
            "smoothing_frames": self.smoothing_frames,
            "precision_min": self.interval_min,
            "division_rule": (
                f"At {self.interval_min:g} min, daughters are searched within {self.daughter_radius_px:.0f} px of the parent."
                if self.interval_min <= 30
                else f"At {self.interval_min:g} min, divisions are accepted with no rounded frame in between "
                f"(one cell at t, two daughters at t + 1), and the daughter search radius is widened to {self.daughter_radius_px:.0f} px."
            ),
            "warnings": self.warnings(),
        }

    def condition_of(self, well: str) -> dict | None:
        cid = self.wells.get(well)
        for c in self.conditions:
            if c["id"] == cid:
                return c
        return None

    # ---------- persistence ----------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in (d or {}).items() if k in known}
        s = cls(**clean)
        s.validate()
        return s

    def update(self, d: dict) -> None:
        for k, v in (d or {}).items():
            if k in self.__dataclass_fields__:
                setattr(self, k, v)
        self.validate()

    def validate(self) -> None:
        def pos(name, lo, default):
            try:
                v = float(getattr(self, name))
                if not math.isfinite(v) or v < lo:
                    raise ValueError
            except Exception:
                v = default
            setattr(self, name, v)

        pos("interval_min", 0.5, 60.0)
        pos("pixel_um", 0.01, 0.62)
        pos("nucleus_um", 1.0, 12.0)
        pos("max_speed_um_h", 0.1, 20.0)
        pos("max_gap_h", 0.0, 2.0)
        pos("colourless_h", 0.0, 12.0)
        pos("min_object_um2", 0.0, 50.0)
        try:
            self.conf_sensitivity = float(np.clip(float(self.conf_sensitivity), 0.0, 1.0))
        except (TypeError, ValueError):
            self.conf_sensitivity = 0.5
        try:
            self.conf_adjust_um = float(np.clip(float(self.conf_adjust_um or 0.0), -20.0, 20.0))
        except (TypeError, ValueError):
            self.conf_adjust_um = 0.0
        if self.conf_bridge_um is not None:
            try:
                v = float(self.conf_bridge_um)
                self.conf_bridge_um = v if 0 < v <= 100 else None
            except (TypeError, ValueError):
                self.conf_bridge_um = None
        if self.bg_radius_um is not None:
            try:
                v = float(self.bg_radius_um)
                self.bg_radius_um = v if math.isfinite(v) and v > 0 else None
            except Exception:
                self.bg_radius_um = None
        pos("smoothing_h", 0.0, 1.0)
        try:
            self.sensitivity = float(min(1.0, max(0.0, float(self.sensitivity))))
        except Exception:
            self.sensitivity = 0.5
        if not isinstance(self.excluded, list):
            self.excluded = []
        self.excluded = sorted({str(x) for x in self.excluded if isinstance(x, str) and x}, key=_excluded_order)
        self.removed = clean_removed(self.removed)
        if not isinstance(self.scatter_hours, list):
            self.scatter_hours = []
        hs = []
        for h in self.scatter_hours:
            try:
                h = round(float(h), 3)
            except (TypeError, ValueError):
                continue
            if h >= 0 and h not in hs:
                hs.append(h)
        self.scatter_hours = sorted(hs)[:8]
        axes = {}
        for fig, spec in (self.fig_axes.items() if isinstance(self.fig_axes, dict) else []):
            if not isinstance(spec, dict):
                continue
            out = {}
            for ax, lim in spec.items():
                if not isinstance(lim, dict):
                    continue
                vals = {}
                for k in ("min", "max", "step"):
                    try:
                        v = float(lim.get(k)) if lim.get(k) not in (None, "") else None
                    except (TypeError, ValueError):
                        v = None
                    if v is not None and np.isfinite(v) and not (k == "step" and v <= 0):
                        vals[k] = v
                if vals:
                    out[str(ax)] = vals
            if out:
                axes[str(fig)] = out
        self.fig_axes = axes
        if not isinstance(self.styles, dict):
            self.styles = {}
        if not isinstance(self.linked, list):
            self.linked = []
        self.linked = [str(p) for p in dict.fromkeys(self.linked) if isinstance(p, str) and p]
        if self.method not in ("classic", "cellpose"):
            self.method = "classic"
        for gate in ("gate_green", "gate_red"):
            v = getattr(self, gate)
            if v is not None:
                try:
                    v = float(v)
                    setattr(self, gate, v if math.isfinite(v) and v > 0 else None)
                except Exception:
                    setattr(self, gate, None)


def default_conditions() -> list:
    return [
        {"id": "c1", "name": "Condition 1", "color": DEFAULT_COLORS[0]},
        {"id": "c2", "name": "Condition 2", "color": DEFAULT_COLORS[1]},
    ]


def save_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(path)


def load_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default
