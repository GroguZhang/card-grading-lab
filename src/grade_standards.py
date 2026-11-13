"""Centering tolerance tables for the major trading-card grading companies.

Semantics
---------
A tier's ``front`` / ``back`` value is the **maximum allowed percentage of the
larger of the two opposing borders**.  A value of ``55`` therefore means the
tolerance is ``55/45``: the wider border may occupy at most 55 % of the
left+right (or top+bottom) span, and the narrower one at least 45 %.

Because the two borders are complementary (``left% + right% == 100``),
``max(left%, right%) <= 55`` is identical to ``55 <= left% <= 45`` in the
symmetric reading — both reduce to the same single number.  That is why every
tool in this space stores one scalar per side instead of a pair.

Sources
-------
The tables below are cross-checked against two independent public sources and
against the tolerance object found in Edge Grading's shipped JavaScript
(``app.settings.center.gradingStandards``).  Where the public sources disagree
the difference is kept as a separate profile rather than silently merged —
see ``CGC_PRISTINE`` vs ``CGC_GEM_MINT``.

Nothing here is authoritative.  Grading companies change standards, and
centering is only one of four grading categories.  Always verify against the
company's own published scale before spending money on a submission.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "GradeTier",
    "GradeStandard",
    "STANDARDS",
    "get_standard",
    "list_companies",
    "grade_from_percentage",
]


@dataclass(frozen=True, slots=True)
class GradeTier:
    """One row of a company's centering tolerance table."""

    label: str
    front: float
    back: float

    def limit_for(self, side: str) -> float:
        side = side.lower()
        if side in ("front", "obverse"):
            return self.front
        if side in ("back", "reverse"):
            return self.back
        raise ValueError(f"side must be 'front' or 'back', got {side!r}")


@dataclass(frozen=True, slots=True)
class GradeStandard:
    """A company's full centering tolerance ladder, best grade first."""

    company: str
    tiers: tuple[GradeTier, ...]
    source: str

    def __post_init__(self) -> None:
        # The ladder must be monotonically non-decreasing in tolerance, otherwise
        # "first tier that fits" would return the wrong grade.
        for prev, nxt in zip(self.tiers, self.tiers[1:]):
            if nxt.front < prev.front or nxt.back < prev.back:
                raise ValueError(
                    f"{self.company}: tiers must be ordered worst-tolerance-last "
                    f"({prev.label} -> {nxt.label} is out of order)"
                )

    @property
    def best(self) -> GradeTier:
        return self.tiers[0]

    def grade(self, worst_pct: float, side: str = "front") -> GradeTier:
        """Return the best tier whose tolerance still covers ``worst_pct``."""
        for tier in self.tiers:
            if worst_pct <= tier.limit_for(side) + 1e-9:
                return tier
        return self.tiers[-1]


# --------------------------------------------------------------------------- #
# PSA — tightened in early 2025 from 60/40 to 55/45 on the front of a Gem Mint 10
# --------------------------------------------------------------------------- #
PSA = GradeStandard(
    company="PSA",
    tiers=(
        GradeTier("10", 55, 75),
        GradeTier("9", 60, 90),
        GradeTier("8", 65, 90),
        GradeTier("7", 70, 90),
        GradeTier("6", 80, 90),
        GradeTier("5", 85, 90),
        GradeTier("4", 85, 90),
        GradeTier("3", 90, 90),
        GradeTier("2", 90, 90),
        GradeTier("1", 90, 90),
    ),
    source="psacard.com grading scale; PSA tightened GM10 front to 55/45 in 2025",
)

# --------------------------------------------------------------------------- #
# BGS / Beckett — 10 is "Black Label", 9.5 is the practical Gem Mint
# --------------------------------------------------------------------------- #
BGS = GradeStandard(
    company="BGS",
    tiers=(
        GradeTier("10", 50, 55),      # Black Label / Pristine
        GradeTier("9.5", 55, 60),     # Gem Mint
        GradeTier("9", 55, 70),
        GradeTier("8", 60, 80),
        GradeTier("7", 65, 90),
        GradeTier("6", 70, 95),
        GradeTier("5", 75, 95),
        GradeTier("4", 80, 100),
        GradeTier("3", 85, 100),
        GradeTier("2", 90, 100),
        GradeTier("1", 100, 100),
    ),
    source="beckett.com grading services; back-side 'Any' rows encoded as 100",
)

# --------------------------------------------------------------------------- #
# CGC — publishes two distinct grade-10 references, so both are kept
# --------------------------------------------------------------------------- #
CGC_PRISTINE = GradeStandard(
    company="CGC (Pristine 10)",
    tiers=(
        GradeTier("10 Pristine", 50, 50),
        GradeTier("9.5", 55, 75),
        GradeTier("9", 60, 90),
        GradeTier("8", 65, 90),
        GradeTier("7", 70, 90),
        GradeTier("6", 75, 90),
        GradeTier("5", 80, 90),
        GradeTier("4", 85, 90),
        GradeTier("3", 90, 90),
        GradeTier("2", 90, 90),
        GradeTier("1", 90, 90),
    ),
    source="cgccards.com; Pristine 10 = 50/50 both sides",
)

CGC_GEM_MINT = GradeStandard(
    company="CGC (Gem Mint 10)",
    tiers=(
        GradeTier("10", 55, 75),
        GradeTier("9.5", 55, 75),
        GradeTier("9", 60, 90),
        GradeTier("8", 65, 90),
        GradeTier("7", 70, 90),
        GradeTier("6", 75, 90),
        GradeTier("5", 80, 90),
        GradeTier("4", 85, 90),
        GradeTier("3", 90, 90),
        GradeTier("2", 90, 90),
        GradeTier("1", 90, 90),
    ),
    source="cgccards.com; Gem Mint 10 = 55/45 front, 75/25 back",
)

# --------------------------------------------------------------------------- #
# SGC — unusually strict on the reverse for a 10 (55/45 both sides)
# --------------------------------------------------------------------------- #
SGC = GradeStandard(
    company="SGC",
    tiers=(
        GradeTier("10 Pristine", 50, 50),
        GradeTier("10", 55, 55),
        GradeTier("9", 60, 60),
        GradeTier("8", 65, 65),
        GradeTier("7", 70, 70),
        GradeTier("6", 75, 75),
        GradeTier("5", 80, 80),
        GradeTier("4", 85, 85),
        GradeTier("3", 90, 90),
        GradeTier("2", 90, 90),
        GradeTier("1", 90, 90),
    ),
    source="gosgc.com; SGC applies the same tolerance front and back",
)

# --------------------------------------------------------------------------- #
# TAG — publishes a fine-grained ladder, including 51/49 for a Pristine 10
# --------------------------------------------------------------------------- #
TAG = GradeStandard(
    company="TAG",
    tiers=(
        GradeTier("10 P", 51, 52),
        GradeTier("10", 55, 65),
        GradeTier("9", 60, 75),
        GradeTier("8.5", 63, 85),
        GradeTier("8", 65, 95),
        GradeTier("7.5", 68, 100),
        GradeTier("7", 70, 100),
        GradeTier("6.5", 73, 100),
        GradeTier("6", 75, 100),
        GradeTier("5.5", 78, 100),
        GradeTier("5", 80, 100),
        GradeTier("4.5", 83, 100),
        GradeTier("4", 85, 100),
        GradeTier("3.5", 88, 100),
        GradeTier("3", 90, 100),
        GradeTier("2.5", 93, 100),
        GradeTier("2", 95, 100),
        GradeTier("1.5", 98, 100),
    ),
    source="taggrading.com published centering ladder",
)

# --------------------------------------------------------------------------- #
# EDGE Grading — the company's own scale, recovered from its shipped settings
# object (``app.settings.center.gradingStandards.edge``).  Included because it
# is the only reference here taken from a live implementation rather than a
# marketing page.
# --------------------------------------------------------------------------- #
EDGE = GradeStandard(
    company="EDGE",
    tiers=(
        GradeTier("10+", 52, 65),
        GradeTier("10", 55, 70),
        GradeTier("9", 60, 80),
        GradeTier("8", 70, 90),
        GradeTier("7", 75, 90),
        GradeTier("6", 100, 100),
    ),
    source="edgegrading.com shipped JS: app.settings.center.gradingStandards.edge",
)


STANDARDS: dict[str, GradeStandard] = {
    "PSA": PSA,
    "BGS": BGS,
    "CGC": CGC_GEM_MINT,
    "CGC_PRISTINE": CGC_PRISTINE,
    "SGC": SGC,
    "TAG": TAG,
    "EDGE": EDGE,
}

_ALIASES = {
    "beckett": "BGS",
    "cgc gem mint": "CGC",
    "cgc pristine": "CGC_PRISTINE",
    "psa card": "PSA",
    "edgegrading": "EDGE",
}


def get_standard(company: str) -> GradeStandard:
    """Look up a standard by name, case- and alias-insensitively."""
    key = company.strip().upper().replace(" ", "_")
    if key in STANDARDS:
        return STANDARDS[key]
    lowered = company.strip().lower()
    if lowered in _ALIASES:
        return STANDARDS[_ALIASES[lowered]]
    raise KeyError(
        f"unknown grading company {company!r}; available: {', '.join(STANDARDS)}"
    )


def list_companies() -> list[str]:
    return list(STANDARDS)


def grade_from_percentage(
    worst_pct: float,
    company: str,
    side: str = "front",
) -> tuple[str, float]:
    """Return ``(grade label, tolerance used)`` for a worst-case centering value."""
    standard = get_standard(company)
    tier = standard.grade(worst_pct, side)
    return tier.label, tier.limit_for(side)
