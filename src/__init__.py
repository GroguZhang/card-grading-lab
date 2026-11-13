"""card-grading-engine — open centering measurement for trading cards.

Quick start
-----------
::

    from src import auto_align, detect_border_lines, measure_from_borders

    aligned = auto_align("card.jpg")
    borders, confidence, _ = detect_border_lines(aligned.warped)
    report = measure_from_borders(borders, confidence=confidence)
    print(report.ratio("lr"), report.grade_table())

Everything is pure Python + OpenCV + NumPy; there is no model download, no
network call, and no card image ever leaves the machine.
"""

from __future__ import annotations

from .centering_calculator import (
    CARD_ASPECT,
    CARD_HEIGHT_MM,
    CARD_WIDTH_MM,
    BorderLines,
    CenteringReport,
    annotate,
    detect_border_lines,
    gradient_profiles,
    measure_from_borders,
    measure_from_ratios,
    subpixel_peak,
)
from .grade_standards import (
    STANDARDS,
    GradeStandard,
    GradeTier,
    get_standard,
    grade_from_percentage,
    list_companies,
)
from .perspective_align import (
    AlignResult,
    auto_align,
    clahe_normalise,
    detect_card_quad,
    detect_glare_mask,
    estimate_skew,
    inpaint_glare,
    order_quad,
    refine_quad,
    warp_card,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # centering
    "BorderLines",
    "CenteringReport",
    "measure_from_borders",
    "measure_from_ratios",
    "detect_border_lines",
    "gradient_profiles",
    "subpixel_peak",
    "annotate",
    "CARD_WIDTH_MM",
    "CARD_HEIGHT_MM",
    "CARD_ASPECT",
    # standards
    "GradeStandard",
    "GradeTier",
    "STANDARDS",
    "get_standard",
    "grade_from_percentage",
    "list_companies",
    # geometry
    "AlignResult",
    "auto_align",
    "detect_card_quad",
    "refine_quad",
    "warp_card",
    "order_quad",
    "estimate_skew",
    "detect_glare_mask",
    "inpaint_glare",
    "clahe_normalise",
]
