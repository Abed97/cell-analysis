"""Experiment settings. Frame interval and pixel size are inputs, never constants:
every time- and distance-dependent parameter is derived from them here."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_COLORS = ["#7AB0FF", "#F59E5B", "#C59BFF", "#5FD4C4", "#F27FB2", "#B8C46A", "#A0A8B2"]


@dataclass
class Settings:
    interval_min: float = 60.0
    pixel_um: float = 0.62
    nucleus_um: float = 12.0
    max_speed_um_h: float = 20.0
    max_gap_h: float = 2.0
    smoothing_h: float = 1.0
    sensitivity: float = 0.5  # segmentation: 0 = strict, 1 = picks up dim cells
    method: str = "classic"  # "classic" or "cellpose"
    # channel group key -> "phase" | "green" | "red" | "ignore"
    channel_map: dict = field(default_factory=dict)
    conditions: list = field(default_factory=list)  # [{"id","name","color"}]
    wells: dict = field(default_factory=dict)  # well -> condition id
    parental_well: str | None = None
    gate_green: float | None = None  # in normalised units
    gate_red: float | None = None
    experiment_name: str = ""

    # ---------- derived ----------
    @property
    def interval_h(self) -> float:
        return self.interval_min / 60.0

    @property
    def nucleus_px(self) -> float:
        return self.nucleus_um / self.pixel_um

    @property
    def search_radius_px(self) -> float:
        # speed x interval / pixel size, never smaller than half a nucleus
        return max(self.max_speed_um_h * self.interval_h / self.pixel_um, 0.5 * self.nucleus_px)

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
        pos("smoothing_h", 0.0, 1.0)
        try:
            self.sensitivity = float(min(1.0, max(0.0, float(self.sensitivity))))
        except Exception:
            self.sensitivity = 0.5
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
