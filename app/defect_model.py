"""Data model for a card condition report.

The shape here deliberately mirrors the information architecture of a
commercial AI grading report, because that architecture is well designed:
a flat list of located defect signals, aggregated into zone scores, then into
four dimension subgrades, then into one overall grade.

Keeping the layers separate is what makes the report explainable. If you only
store the final grade you can never answer "why is it not a 10", which is the
one question every user actually asks.

All scores are on the 1-10 scale used by the major grading companies.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "Side",
    "Category",
    "Severity",
    "SignalSource",
    "Dimension",
    "ZONE_LABELS",
    "ZONES_BY_DIMENSION",
    "DefectSignal",
    "ZoneScore",
    "DimensionScore",
    "GradeReport",
    "new_report_id",
]


class Side(str, Enum):
    FRONT = "front"
    BACK = "back"

    @property
    def short(self) -> str:
        return "F" if self is Side.FRONT else "B"


class Category(str, Enum):
    CORNER = "corner"
    EDGE = "edge"
    SURFACE = "surface"
    CENTERING = "centering"


class Severity(str, Enum):
    """How much a single defect costs, in grade points.

    ``MINOR`` is the only level a phone photo can support with confidence:
    a hairline whitening on a corner, a single 2 mm scratch. ``MAJOR`` means
    visible without magnification. ``CRITICAL`` means the card would be
    rejected outright (crease, missing corner, water damage).
    """

    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class SignalSource(str, Enum):
    """Who reported the defect.

    The distinction matters: a vision model can hallucinate a defect that is
    not there, whereas a pixel measurement cannot. Reports that mix the two
    without labelling them are not verifiable.
    """

    AI_FLAGGED = "ai_flagged"
    CV_MEASURED = "cv_measured"
    MANUAL = "manual"


class Dimension(str, Enum):
    CENTERING = "centering"
    CORNERS = "corners"
    EDGES = "edges"
    SURFACE = "surface"

    @property
    def label(self) -> str:
        return self.value.upper()


#: Human-readable zone names. Keys are stable identifiers used in JSON.
ZONE_LABELS: dict[str, str] = {
    "top_left": "左上角",
    "top_right": "右上角",
    "bottom_left": "左下角",
    "bottom_right": "右下角",
    "top": "上边",
    "bottom": "下边",
    "left": "左边",
    "right": "右边",
    "center": "表面",
}

#: Which zones feed which dimension.
ZONES_BY_DIMENSION: dict[Dimension, tuple[str, ...]] = {
    Dimension.CORNERS: ("top_left", "top_right", "bottom_left", "bottom_right"),
    Dimension.EDGES: ("top", "bottom", "left", "right"),
    Dimension.SURFACE: ("center",),
}


def new_report_id() -> str:
    """Short opaque id, uppercase hex, matching the convention users expect."""
    return uuid.uuid4().hex[:8].upper()


@dataclass(slots=True)
class DefectSignal:
    """One located defect observation.

    ``zone`` + ``side`` + ``category`` is the addressing scheme; ``defect``
    is the free-form type; ``source`` records provenance. That is exactly the
    four-part tuple a grading report needs to be auditable.
    """

    severity: Severity
    side: Side
    zone: str
    category: Category
    defect: str
    source: SignalSource = SignalSource.AI_FLAGGED
    confidence: float = 1.0
    bbox: tuple[int, int, int, int] | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.zone not in ZONE_LABELS:
            raise ValueError(f"unknown zone {self.zone!r}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be within [0, 1]")

    @property
    def location(self) -> str:
        """``"Front · Top left · corner"`` — the breadcrumb used in reports."""
        side = "Front" if self.side is Side.FRONT else "Back"
        return f"{side} · {ZONE_LABELS[self.zone]} · {self.category.value}"

    def to_dict(self) -> dict[str, Any]:
        d = {
            "severity": self.severity.value,
            "side": self.side.value,
            "zone": self.zone,
            "zone_label": ZONE_LABELS[self.zone],
            "category": self.category.value,
            "defect": self.defect,
            "source": self.source.value,
            "confidence": round(self.confidence, 3),
            "location": self.location,
        }
        if self.note:
            d["note"] = self.note
        return d


@dataclass(slots=True)
class ZoneScore:
    """Score for one of the nine local regions of one side of the card."""

    zone: str
    side: Side
    score: float
    signals: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "zone": self.zone,
            "zone_label": ZONE_LABELS[self.zone],
            "side": self.side.value,
            "score": round(self.score, 2),
            "signals": self.signals,
        }


@dataclass(slots=True)
class DimensionScore:
    """One of the four grading dimensions, with its front/back breakdown."""

    dimension: Dimension
    front: float
    back: float
    subgrade: float
    detail: str = ""

    @property
    def label(self) -> str:
        return self.dimension.label

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension.value,
            "label": self.label,
            "front": round(self.front, 2),
            "back": round(self.back, 2),
            "subgrade": round(self.subgrade, 2),
            "detail": self.detail,
        }


@dataclass(slots=True)
class GradeReport:
    """Everything needed to render one report, and nothing else.

    The renderer must be able to work from this object alone — no globals, no
    database lookups. That constraint is what keeps the PDF step honest: if the
    report renders wrong, the data was wrong.
    """

    report_id: str
    card_name: str
    overall: float
    tier: str
    limiting_factor: Dimension | None
    dimensions: list[DimensionScore]
    zones: list[ZoneScore]
    signals: list[DefectSignal]
    predictions: dict[str, float] = field(default_factory=dict)
    centering: dict[str, Any] | None = None
    dimensions_in: dict[str, float] | None = None
    model_version: str = "local-0.1"
    threshold_version: str = "2026.11"
    warnings: list[str] = field(default_factory=list)

    # -- derived --------------------------------------------------------- #
    @property
    def subgrades(self) -> dict[str, float]:
        return {d.dimension.value: d.subgrade for d in self.dimensions}

    def dimension(self, name: str) -> DimensionScore | None:
        for d in self.dimensions:
            if d.dimension.value == name:
                return d
        return None

    def signals_by(self, dimension: Dimension) -> list[DefectSignal]:
        cats = {
            Dimension.CORNERS: {Category.CORNER},
            Dimension.EDGES: {Category.EDGE},
            Dimension.SURFACE: {Category.SURFACE},
            Dimension.CENTERING: {Category.CENTERING},
        }[dimension]
        return [s for s in self.signals if s.category in cats]

    def count_by_severity(self, dimension: Dimension, side: Side) -> int:
        return sum(
            1
            for s in self.signals_by(dimension)
            if s.side is side and s.severity is Severity.MINOR
        )

    def story(self) -> str:
        """One-paragraph rationale, generated from the data (not by a model)."""
        if self.limiting_factor is None:
            return (
                f"This card was assigned an overall grade of {self.overall:g} "
                f"({self.tier}). No dimension limited the result."
            )
        dim = self.dimension(self.limiting_factor.value)
        assert dim is not None
        weak = "front" if dim.front <= dim.back else "back"
        n = sum(
            1 for s in self.signals_by(self.limiting_factor) if s.side.value == weak
        )
        return (
            f"This card was assigned an overall grade of {self.overall:g} "
            f"({self.tier}). Centering, edges and surface held up, but "
            f"{self.limiting_factor.value} on the {weak} side produced "
            f"{n} minor signals, which capped the overall grade at "
            f"{self.overall:g}."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "card_name": self.card_name,
            "overall": self.overall,
            "tier": self.tier,
            "limiting_factor": (
                self.limiting_factor.value if self.limiting_factor else None
            ),
            "dimensions": [d.to_dict() for d in self.dimensions],
            "zones": [z.to_dict() for z in self.zones],
            "signals": [s.to_dict() for s in self.signals],
            "predictions": {k: round(v, 2) for k, v in self.predictions.items()},
            "centering": self.centering,
            "model_version": self.model_version,
            "threshold_version": self.threshold_version,
            "warnings": list(self.warnings),
        }
