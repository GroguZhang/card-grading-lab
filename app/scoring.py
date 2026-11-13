"""Scoring engine: defects -> zone scores -> subgrades -> overall grade.

The four-layer decomposition is not decoration. Each layer answers a different
question, and collapsing them loses information users need:

    defects     哪里、什么、多严重
    zone score  这个局部看起来几分
    subgrade    这个维度能不能拿 10
    overall     最终几档、被谁拖累

The one non-obvious rule is the **consistency penalty** at layer 3. A card with
one light corner touch and a card with four light corner touches have the same
worst-zone score, but they are not the same card, and no grading company treats
them the same. Four corners showing wear is evidence of handling, not of a
single unlucky bump, so the subgrade is pushed down further than the per-zone
arithmetic alone would suggest.

That rule is inferred, not observed. It is the only way to reconcile a report
that shows zone scores of 9.7 alongside a Corners subgrade of 9 — min() would
give 9.7, rounding would give 10, neither gives 9. The parameters below are
tunable precisely because this is a reconstruction, not a specification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .defect_model import (
    ZONES_BY_DIMENSION,
    Category,
    DefectSignal,
    Dimension,
    DimensionScore,
    GradeReport,
    Severity,
    Side,
    ZoneScore,
    new_report_id,
)

__all__ = [
    "ScoringConfig",
    "DEFAULT_CONFIG",
    "GRADE_TIERS",
    "tier_for",
    "zone_scores",
    "score_dimension",
    "predict_institutions",
    "build_report",
]

#: PSA-style tier names, high to low.
GRADE_TIERS: tuple[tuple[float, str], ...] = (
    (10.0, "GEM MINT"),
    (9.0, "MINT"),
    (8.0, "NM-MT"),
    (7.0, "NM"),
    (6.0, "EX-MT"),
    (5.0, "EX"),
    (4.0, "VG-EX"),
    (3.0, "VG"),
    (2.0, "GOOD"),
    (1.0, "POOR"),
)


def tier_for(grade: float) -> str:
    for threshold, name in GRADE_TIERS:
        if grade + 1e-9 >= threshold:
            return name
    return "POOR"


@dataclass(slots=True)
class ScoringConfig:
    """All tunable numbers in one place.

    Changing any of these changes every historical report's comparability, so
    reports stamp ``threshold_version`` and the config's version string.
    """

    #: Grade points deducted per defect, by severity.
    severity_weight: dict[str, float] = field(
        default_factory=lambda: {
            Severity.MINOR.value: 0.15,
            Severity.MAJOR.value: 0.60,
            Severity.CRITICAL.value: 1.50,
        }
    )

    #: (minimum affected zones, extra deduction) — evaluated high to low.
    #: Four worn corners is systemic; one worn corner is an accident.
    consistency_penalty: tuple[tuple[int, float], ...] = (
        (4, 0.70),
        (2, 0.30),
        (1, 0.00),
    )

    #: Subgrades land on this grid (0.5 keeps BGS-style half points available).
    round_step: float = 0.5

    #: Which company's tolerance table decides the centering subgrade.
    centering_company: str = "PSA"

    #: How each company's prediction relates to the self-assessed tier.
    #: These are *style* offsets, not tolerances: BGS splits a whole PSA grade
    #: into two half grades, CGC grades more leniently on condition defects.
    prediction_offsets: dict[str, float] = field(
        default_factory=lambda: {"PSA": 0.0, "BGS": 0.5, "CGC": 1.0}
    )

    version: str = "2026.11"

    def weight(self, severity: Severity | str) -> float:
        key = severity.value if isinstance(severity, Severity) else severity
        return self.severity_weight.get(key, 0.0)

    def penalty_for(self, affected_zones: int) -> float:
        for minimum, penalty in self.consistency_penalty:
            if affected_zones >= minimum:
                return penalty
        return 0.0

    def quantise(self, value: float) -> float:
        """Snap to the report grid, to **nearest**.

        Rounding up is deliberate, and it is what the reference report does: it
        lists a surface scratch and still awards Surface 10. A single hairline
        defect does not disqualify the top tier — that is the tolerance policy
        the whole threshold table encodes.

        The honest-reporting burden therefore moves to the text: a dimension
        that reports 10 while carrying signals must say so, which
        ``report_render._zone_analysis`` does. Rounding *down* instead would
        have looked stricter while quietly diverging from the observed model.
        """
        step = self.round_step
        if step <= 0:
            return value
        return round(round(value / step) * step, 4)


DEFAULT_CONFIG = ScoringConfig()


# --------------------------------------------------------------------------- #
# Layer 2: zone scores
# --------------------------------------------------------------------------- #
def zone_scores(
    signals: Iterable[DefectSignal],
    side: Side,
    config: ScoringConfig = DEFAULT_CONFIG,
) -> dict[str, float]:
    """Per-zone score for one side. Untouched zones score a clean 10."""
    buckets: dict[str, list[DefectSignal]] = {}
    for s in signals:
        if s.side is not side:
            continue
        buckets.setdefault(s.zone, []).append(s)

    out: dict[str, float] = {}
    for zone in set(ZONES_BY_DIMENSION[Dimension.CORNERS]) | set(
        ZONES_BY_DIMENSION[Dimension.EDGES]
    ) | set(ZONES_BY_DIMENSION[Dimension.SURFACE]):
        deduction = sum(config.weight(s.severity) for s in buckets.get(zone, ()))
        out[zone] = max(0.0, 10.0 - deduction)
    return out


# --------------------------------------------------------------------------- #
# Layer 3: dimension subgrades
# --------------------------------------------------------------------------- #
def score_dimension(
    signals: Sequence[DefectSignal],
    side: Side,
    dimension: Dimension,
    config: ScoringConfig = DEFAULT_CONFIG,
) -> float:
    """Aggregate one dimension on one side.

    Two steps, and the order matters:

    1. Take the **worst** zone, not the average. A single damaged corner cannot
       be averaged away by three clean ones — that is the whole point of
       grading by the weakest point.
    2. Subtract a consistency penalty proportional to how many zones are
       affected at all.
    """
    zones = ZONES_BY_DIMENSION[dimension]
    if dimension is Dimension.CENTERING:
        raise ValueError("centering is scored from measurements, not signals")

    scores = zone_scores(signals, side, config)
    local = {z: scores.get(z, 10.0) for z in zones}

    worst = min(local.values())
    affected = sum(1 for v in local.values() if v < 10.0 - 1e-9)
    adjusted = worst - config.penalty_for(affected)

    return config.quantise(max(0.0, adjusted))


def centering_subgrade(
    lr_worst: float,
    tb_worst: float,
    side: Side,
    config: ScoringConfig = DEFAULT_CONFIG,
) -> float:
    """Map measured centering onto the 10-point subgrade scale.

    Unlike the other dimensions this one is fully determined by published
    numbers: we look the worst axis up in the company's own tolerance ladder.
    Front and back use different ladders, which is why a 59/41 reverse can still
    score a 10 while the same numbers on the front would not.
    """
    from src.grade_standards import get_standard

    worst = max(lr_worst, tb_worst)
    tier = get_standard(config.centering_company).grade(
        worst, "front" if side is Side.FRONT else "back"
    )
    try:
        return float(tier.label.split()[0].rstrip("P"))
    except ValueError:
        return 10.0


# --------------------------------------------------------------------------- #
# Layer 4: institution predictions
# --------------------------------------------------------------------------- #
def predict_institutions(
    overall: float,
    config: ScoringConfig = DEFAULT_CONFIG,
) -> dict[str, float]:
    """Project the self-assessed grade onto each company's scale.

    This is a *style* mapping, not a tolerance calculation. It reproduces the
    well-known fact that the same card lands at PSA 9 / BGS 9.5 / CGC 10, but it
    carries no authority: only the centering component is derived from published
    thresholds, the rest is patterned after each company's observed behaviour.
    """
    out: dict[str, float] = {}
    for company, offset in config.prediction_offsets.items():
        out[company] = min(10.0, config.quantise(overall + offset))
    return out


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_report(
    *,
    card_name: str,
    signals: Sequence[DefectSignal],
    centering_metrics: dict[str, object] | None = None,
    config: ScoringConfig = DEFAULT_CONFIG,
    report_id: str | None = None,
    warnings: Sequence[str] = (),
    model_version: str = "local-0.1",
    assessed_dimensions: Sequence[str] = (),
) -> GradeReport:
    """Run all four layers and return a render-ready report.

    ``centering_metrics`` is the dict produced by
    :func:`src.centering_calculator.measure_from_borders().to_dict()`; when it is
    absent the centering dimension is skipped and the overall grade is the
    minimum of the remaining three.
    """
    dims: list[DimensionScore] = []
    zone_rows: list[ZoneScore] = []

    scores_f, scores_b = zone_scores(signals, Side.FRONT, config), zone_scores(
        signals, Side.BACK, config
    )
    for side, table in ((Side.FRONT, scores_f), (Side.BACK, scores_b)):
        for zone, value in table.items():
            zone_rows.append(
                ZoneScore(
                    zone=zone,
                    side=side,
                    score=value,
                    signals=sum(
                        1 for s in signals if s.side is side and s.zone == zone
                    ),
                )
            )

    for dim in (
        Dimension.CORNERS,
        Dimension.EDGES,
        Dimension.SURFACE,
    ):
        f = score_dimension(signals, Side.FRONT, dim, config)
        b = score_dimension(signals, Side.BACK, dim, config)
        dims.append(
            DimensionScore(dimension=dim, front=f, back=b, subgrade=min(f, b))
        )

    if centering_metrics:
        lr = float(centering_metrics.get("lr_worst_pct", 50.0))  # type: ignore[arg-type]
        tb = float(centering_metrics.get("tb_worst_pct", 50.0))  # type: ignore[arg-type]
        cf = centering_subgrade(lr, tb, Side.FRONT, config)
        cb = centering_subgrade(
            float(centering_metrics.get("back_lr_worst_pct", lr)),  # type: ignore[arg-type]
            float(centering_metrics.get("back_tb_worst_pct", tb)),  # type: ignore[arg-type]
            Side.BACK,
            config,
        )
        dims.insert(
            0,
            DimensionScore(
                dimension=Dimension.CENTERING,
                front=cf,
                back=cb,
                subgrade=min(cf, cb),
            ),
        )

    overall = min(d.subgrade for d in dims)
    limiting = next((d.dimension for d in dims if d.subgrade == overall), None)

    return GradeReport(
        report_id=report_id or new_report_id(),
        card_name=card_name,
        overall=overall,
        tier=tier_for(overall),
        limiting_factor=limiting,
        dimensions=dims,
        zones=zone_rows,
        signals=list(signals),
        predictions=predict_institutions(overall, config),
        centering=dict(centering_metrics) if centering_metrics else None,
        model_version=model_version,
        threshold_version=config.version,
        warnings=list(warnings),
        assessed_dimensions=list(assessed_dimensions),
    )
