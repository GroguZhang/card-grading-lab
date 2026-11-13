"""Pixel-accurate card centering measurement.

This module turns a *rectified* card photo into L/R and T/B centering ratios,
then maps those ratios onto a grading company's published tolerances.

Design notes
------------
The measurement itself is trivial arithmetic once the eight border
coordinates are known::

    left  = |inner_left  - outer_left|
    right = |outer_right - inner_right|
    left_pct = left / (left + right) * 100

Everything hard about this problem lives in *finding* those eight coordinates,
so the module ships a projection-profile edge finder that is deliberately
robust to noise, plus sub-pixel refinement so that a 1 px error on a 900 px
card does not move the ratio by a full percentage point.

Why projection profiles instead of contour tracing
--------------------------------------------------
A card border is, by construction, a long straight line.  Summing horizontal
gradient magnitude down every column collapses that line into a single spike,
which means a border is detected only if *many* pixels agree on it.  Isolated
scratches, dust and sensor noise never accumulate enough votes to form a peak,
so they are rejected for free — no morphological clean-up required.

The trade-off is that the method assumes the card is already axis-aligned.
Run :func:`src.perspective_align.auto_align` first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

import cv2
import numpy as np

from .grade_standards import GradeStandard, GradeTier, get_standard

__all__ = [
    "CARD_WIDTH_MM",
    "CARD_HEIGHT_MM",
    "BorderLines",
    "CenteringReport",
    "measure_from_borders",
    "measure_from_ratios",
    "detect_border_lines",
    "gradient_profiles",
    "subpixel_peak",
    "subpixel_centroid",
]

#: Nominal dimensions of a standard trading card (2.5" x 3.5").
CARD_WIDTH_MM = 63.5
CARD_HEIGHT_MM = 88.9
CARD_ASPECT = CARD_WIDTH_MM / CARD_HEIGHT_MM  # ~0.7143


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class BorderLines:
    """Eight border coordinates, in pixels, on a rectified card image.

    ``outer_*`` describe the cut edge of the card; ``inner_*`` describe the
    printed frame / artwork boundary that centering is judged against.
    """

    outer_left: float
    inner_left: float
    inner_right: float
    outer_right: float
    outer_top: float
    inner_top: float
    inner_bottom: float
    outer_bottom: float

    def __post_init__(self) -> None:
        if not self.outer_left < self.inner_left <= self.inner_right < self.outer_right:
            raise ValueError(
                "horizontal borders must satisfy outer_left < inner_left <= "
                f"inner_right < outer_right, got {self.outer_left}, "
                f"{self.inner_left}, {self.inner_right}, {self.outer_right}"
            )
        if not self.outer_top < self.inner_top <= self.inner_bottom < self.outer_bottom:
            raise ValueError(
                "vertical borders must satisfy outer_top < inner_top <= "
                f"inner_bottom < outer_bottom, got {self.outer_top}, "
                f"{self.inner_top}, {self.inner_bottom}, {self.outer_bottom}"
            )

    @property
    def card_width_px(self) -> float:
        return self.outer_right - self.outer_left

    @property
    def card_height_px(self) -> float:
        return self.outer_bottom - self.outer_top

    @property
    def pixel_aspect(self) -> float:
        """Width/height of the detected card outline, in pixels."""
        return self.card_width_px / self.card_height_px if self.card_height_px else float("nan")

    def as_list(self) -> list[float]:
        return [
            self.outer_left, self.inner_left, self.inner_right, self.outer_right,
            self.outer_top, self.inner_top, self.inner_bottom, self.outer_bottom,
        ]

    def scaled(self, factor: float, dx: float = 0.0, dy: float = 0.0) -> "BorderLines":
        """Rescale coordinates, e.g. from the detector's working copy to the original."""
        return BorderLines(
            outer_left=self.outer_left * factor + dx,
            inner_left=self.inner_left * factor + dx,
            inner_right=self.inner_right * factor + dx,
            outer_right=self.outer_right * factor + dx,
            outer_top=self.outer_top * factor + dy,
            inner_top=self.inner_top * factor + dy,
            inner_bottom=self.inner_bottom * factor + dy,
            outer_bottom=self.outer_bottom * factor + dy,
        )


@dataclass(frozen=True, slots=True)
class CenteringReport:
    """Result of one centering measurement.

    Percentages are stored **unrounded**.  Rounding to whole numbers is what
    every consumer tool in this space does, and it is lossy in exactly the
    place it matters: a card at 55.4/44.6 gets reported as 55/45 and passes a
    PSA 10 check that it should fail.  :meth:`ratio` rounds for display only.
    """

    left_pct: float
    right_pct: float
    top_pct: float
    bottom_pct: float
    left_mm: float
    right_mm: float
    top_mm: float
    bottom_mm: float
    confidence: float = 1.0
    full_bleed_suspected: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    # -- derived ---------------------------------------------------------- #
    @property
    def lr_worst_pct(self) -> float:
        return max(self.left_pct, self.right_pct)

    @property
    def tb_worst_pct(self) -> float:
        return max(self.top_pct, self.bottom_pct)

    @property
    def worst_pct(self) -> float:
        """The number that actually decides the grade."""
        return max(self.lr_worst_pct, self.tb_worst_pct)

    @property
    def worst_side(self) -> str:
        if self.left_pct > self.right_pct and self.left_pct >= self.top_pct and self.left_pct >= self.bottom_pct:
            return "left"
        if self.right_pct >= self.top_pct and self.right_pct >= self.bottom_pct:
            return "right"
        return "top" if self.top_pct >= self.bottom_pct else "bottom"

    @property
    def worst_axis(self) -> str:
        return "L/R" if self.lr_worst_pct >= self.tb_worst_pct else "T/B"

    def ratio(self, axis: str = "lr", decimals: int = 1) -> str:
        """Format as ``"53.2/46.8"``; ``decimals=0`` reproduces legacy tools."""
        axis = axis.lower()
        if axis in ("lr", "left", "left/right", "l/r"):
            a, b = self.left_pct, self.right_pct
        elif axis in ("tb", "top", "top/bottom", "t/b"):
            a, b = self.top_pct, self.bottom_pct
        else:
            raise ValueError(f"axis must be 'lr' or 'tb', got {axis!r}")
        return f"{a:.{decimals}f}/{b:.{decimals}f}"

    # -- grading ---------------------------------------------------------- #
    def grade(self, company: str, side: str = "front") -> GradeTier:
        """Best tier this card can reach at ``company`` on the given side."""
        return get_standard(company).grade(self.worst_pct, side)

    def grade_per_axis(
        self, company: str, side: str = "front"
    ) -> tuple[GradeTier, GradeTier]:
        """Grade the two axes independently, as Centering Check's docs describe.

        "Your estimated grade is the minimum of the Left/Right and Top/Bottom
        grades."  On a monotonic ladder this is equivalent to grading
        :attr:`worst_pct`, but returning both makes the limiting axis obvious.
        """
        std = get_standard(company)
        return std.grade(self.lr_worst_pct, side), std.grade(self.tb_worst_pct, side)

    def grade_table(
        self,
        companies: Iterable[str] = ("PSA", "BGS", "CGC", "SGC", "TAG"),
        side: str = "front",
    ) -> dict[str, str]:
        return {c: self.grade(c, side).label for c in companies}

    def headroom(self, company: str, side: str = "front") -> float:
        """How many percentage points of slack remain before dropping a tier.

        Positive = inside the current tier's tolerance.  Negative = already out.
        """
        standard = get_standard(company)
        current = standard.grade(self.worst_pct, side)
        return current.limit_for(side) - self.worst_pct

    def is_borderline(self, company: str, side: str = "front", eps: float = 0.5) -> bool:
        """True when rounding to whole percent could flip the verdict."""
        return abs(self.headroom(company, side)) <= eps

    # -- serialisation ---------------------------------------------------- #
    def to_dict(self) -> dict:
        return {
            "lr_ratio": self.ratio("lr", 1),
            "tb_ratio": self.ratio("tb", 1),
            "left_pct": round(self.left_pct, 2),
            "right_pct": round(self.right_pct, 2),
            "top_pct": round(self.top_pct, 2),
            "bottom_pct": round(self.bottom_pct, 2),
            "left_mm": round(self.left_mm, 2),
            "right_mm": round(self.right_mm, 2),
            "top_mm": round(self.top_mm, 2),
            "bottom_mm": round(self.bottom_mm, 2),
            "worst_pct": round(self.worst_pct, 2),
            "worst_side": self.worst_side,
            "confidence": round(self.confidence, 3),
            "full_bleed_suspected": self.full_bleed_suspected,
            "warnings": list(self.warnings),
        }

    def summary(self) -> str:
        lines = [
            f"L/R {self.ratio('lr', 1)}   T/B {self.ratio('tb', 1)}",
            f"Worst: {self.worst_pct:.1f}% on the {self.worst_side} ({self.worst_axis} axis)",
        ]
        if self.warnings:
            lines.append("Warnings: " + "; ".join(self.warnings))
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Measurement
# --------------------------------------------------------------------------- #
def measure_from_borders(
    borders: BorderLines,
    *,
    card_width_mm: float = CARD_WIDTH_MM,
    card_height_mm: float = CARD_HEIGHT_MM,
    confidence: float = 1.0,
    tolerance_mm: float = 0.15,
) -> CenteringReport:
    """Compute centering ratios and physical border widths from border pixels."""
    left_px = borders.inner_left - borders.outer_left
    right_px = borders.outer_right - borders.inner_right
    top_px = borders.inner_top - borders.outer_top
    bottom_px = borders.outer_bottom - borders.inner_bottom

    lr_total = left_px + right_px
    tb_total = top_px + bottom_px
    if lr_total <= 0 or tb_total <= 0:
        raise ValueError("degenerate borders: inner frame has zero width or height")

    left_pct = left_px / lr_total * 100.0
    right_pct = 100.0 - left_pct
    top_pct = top_px / tb_total * 100.0
    bottom_pct = 100.0 - top_pct

    # Millimetres are *inferred*, not measured: we assume the detected card
    # outline spans the nominal 63.5 x 88.9 mm.  A card that is short by 0.4 mm,
    # or a scan where the detector clipped a hair inside the cut edge, shifts
    # every border width proportionally.
    card_w = borders.card_width_px or 1.0
    card_h = borders.card_height_px or 1.0
    left_mm = left_px / card_w * card_width_mm
    right_mm = right_px / card_w * card_width_mm
    top_mm = top_px / card_h * card_height_mm
    bottom_mm = bottom_px / card_h * card_height_mm

    warnings: list[str] = []

    # A card photographed with residual perspective shows up as a distorted
    # aspect ratio long before the ratio itself looks wrong.  Cardboard is
    # printed to 63.5 x 88.9; anything past ~4 % is a geometry problem, not a
    # centering problem.
    aspect_err = abs(borders.pixel_aspect - CARD_ASPECT) / CARD_ASPECT
    if aspect_err > 0.04:
        warnings.append(
            f"card outline aspect is {borders.pixel_aspect:.3f} vs expected "
            f"{CARD_ASPECT:.3f} ({aspect_err * 100:.1f}% off) — residual perspective "
            "or a non-uniform crop; re-run perspective correction"
        )

    # Borders thinner than a third of a millimetre cannot be resolved reliably
    # at typical phone-camera resolution.
    thinnest = min(left_mm, right_mm, top_mm, bottom_mm)
    if thinnest < tolerance_mm:
        warnings.append(
            f"thinnest border is {thinnest:.2f} mm — below the {tolerance_mm} mm "
            "resolution floor, expect +/-1 pp error"
        )

    full_bleed = (left_px < 1.5 and right_px < 1.5) or (top_px < 1.5 and bottom_px < 1.5)
    if full_bleed:
        warnings.append(
            "borderless / full-bleed artwork: no measurable frame on at least one "
            "axis, centering cannot be judged — use an artwork anchor instead"
        )

    if confidence < 0.4:
        warnings.append(f"low edge confidence ({confidence:.2f}); verify the guide lines")

    return CenteringReport(
        left_pct=left_pct,
        right_pct=right_pct,
        top_pct=top_pct,
        bottom_pct=bottom_pct,
        left_mm=left_mm,
        right_mm=right_mm,
        top_mm=top_mm,
        bottom_mm=bottom_mm,
        confidence=confidence,
        full_bleed_suspected=full_bleed,
        warnings=tuple(warnings),
    )


def measure_from_ratios(
    lr: Sequence[float] = (50, 50),
    tb: Sequence[float] = (50, 50),
) -> CenteringReport:
    """Build a report from ratios typed in by hand (no image required)."""
    if len(lr) != 2 or len(tb) != 2:
        raise ValueError("lr and tb must each be two numbers")
    l, r = (float(v) for v in lr)
    t, b = (float(v) for v in tb)
    for name, pair in (("lr", (l, r)), ("tb", (t, b))):
        if abs(sum(pair) - 100.0) > 0.5:
            raise ValueError(f"{name} must sum to 100, got {sum(pair)}")

    def to_mm(pct: float, span_mm: float) -> float:
        return pct / 100.0 * span_mm

    return CenteringReport(
        left_pct=l, right_pct=r, top_pct=t, bottom_pct=b,
        left_mm=to_mm(l, CARD_WIDTH_MM), right_mm=to_mm(r, CARD_WIDTH_MM),
        top_mm=to_mm(t, CARD_HEIGHT_MM), bottom_mm=to_mm(b, CARD_HEIGHT_MM),
        confidence=1.0,
        full_bleed_suspected=False,
        warnings=(),
    )


# --------------------------------------------------------------------------- #
# Edge detection
# --------------------------------------------------------------------------- #
def gradient_profiles(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(column_profile, row_profile)`` of gradient magnitude sums.

    ``column_profile[x]`` is high where a vertical edge runs through column x.
    Both profiles are normalised by the number of rows/columns they summed, so
    values are comparable across image sizes and between the two axes.
    """
    if gray.ndim != 2:
        raise ValueError("gradient_profiles expects a single-channel image")
    g = gray.astype(np.float32, copy=False)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)

    col = np.abs(gx).mean(axis=0)   # average over rows -> per-column score
    row = np.abs(gy).mean(axis=1)   # average over columns -> per-row score

    # A 5-tap moving average suppresses single-pixel spikes without moving peaks.
    kernel = np.ones(5, dtype=np.float32) / 5.0
    col = np.convolve(col, kernel, mode="same")
    row = np.convolve(row, kernel, mode="same")
    return col, row


def subpixel_peak(profile: np.ndarray, index: int) -> float:
    """Parabolic interpolation around ``index`` for a fractional peak position.

    Fitting ``y = a*x^2 + b*x + c`` through the peak and its two neighbours puts
    the vertex at ``x = i + 0.5*(y[i-1] - y[i+1]) / (y[i-1] - 2*y[i] + y[i+1])``.
    On a smooth, symmetric edge this recovers roughly 1/10 px accuracy.

    Prefer :func:`subpixel_centroid` for step edges — see its docstring.
    """
    if index <= 0 or index >= len(profile) - 1:
        return float(index)
    a, b, c = float(profile[index - 1]), float(profile[index]), float(profile[index + 1])
    denom = a - 2.0 * b + c
    if abs(denom) < 1e-12:
        return float(index)
    delta = 0.5 * (a - c) / denom
    # Guard against a runaway fit on a flat or noisy shoulder.
    return float(index) + float(np.clip(delta, -0.5, 0.5))


def subpixel_centroid(profile: np.ndarray, index: int, radius: int = 2) -> float:
    """Gradient-weighted centroid of the peak region around ``index``.

    A 3x3 Sobel operator does not produce a single spike on a step edge: it
    produces a symmetric *pair* of responses straddling the edge, one on each
    side.  Picking whichever of the two is larger biases every measurement
    toward the outside of the card, and because the bias is a fixed fraction of
    a pixel it grows in percentage terms as borders get narrower — which shows
    up as a slope error (a 70/30 card reading 0.7 pp wide, a 50/50 card 0.06 pp).

    Averaging the pair by intensity cancels that asymmetry: for a symmetric
    doublet the centroid lands exactly on the edge, and for a single spike it
    degrades gracefully to the peak position.
    """
    lo = max(0, index - radius)
    hi = min(len(profile), index + radius + 1)
    window = profile[lo:hi].astype(np.float64)
    total = window.sum()
    if total <= 0 or window.size < 2:
        return float(index)
    xs = np.arange(lo, hi, dtype=np.float64)
    return float((window * xs).sum() / total)


def _local_maxima(profile: np.ndarray, min_value: float) -> np.ndarray:
    """Indices that are the maximum within +/- ``search_floor`` and above ``min_value``."""
    vals = profile
    left = np.concatenate(([vals[0]], vals[:-1]))
    right = np.concatenate((vals[1:], [vals[-1]]))
    mask = (vals >= left) & (vals >= right) & (vals >= min_value)
    return np.flatnonzero(mask)


def _find_pair(
    profile: np.ndarray,
    *,
    peak_ratio: float,
    min_gap_frac: float,
    from_start: bool,
    inner_ratio: float = 0.55,
    max_inner_frac: float = 0.32,
) -> tuple[float, float, float, float]:
    """Locate the outer and inner edge on one side of the card.

    Returns ``(outer_pos, inner_pos, outer_strength, inner_strength)``.

    The outer edge is the first strong peak inside the outermost band.  The
    inner edge is the *first* strong peak encountered when walking inward from
    there — not the global maximum of the remaining profile.  That distinction
    matters: taking the global maximum makes the inner search jump across the
    card to the opposite border, which then reports ~90/10 on a card that is
    actually 50/50.
    """
    n = len(profile)
    span = n
    peak = float(profile.max()) or 1.0
    threshold = peak * peak_ratio

    # Search the outer 18 % of the image for the card's cut edge.  Photographs
    # are supposed to include a margin, so the true edge is never at x == 0.
    band = int(max(8, span * 0.18))
    outer_slice = slice(0, band) if from_start else slice(n - band, n)

    cand = _local_maxima(profile[outer_slice], threshold) + outer_slice.start
    if cand.size == 0:
        # Nothing above threshold: fall back to the strongest point in the band.
        cand = np.array([int(np.argmax(profile[outer_slice]) + outer_slice.start)])

    # Prefer the candidate closest to the border (first edge from the outside),
    # breaking ties by strength.
    candidates = sorted(cand.tolist(), key=(lambda i: i) if from_start else (lambda i: -i))
    outer_idx = candidates[0]
    outer_strength = float(profile[outer_idx])

    gap = int(max(4, span * min_gap_frac))
    reach = int(max(gap * 2, span * max_inner_frac))

    if from_start:
        lo, hi = outer_idx + gap, min(n, outer_idx + reach)
    else:
        lo, hi = max(0, outer_idx - reach), outer_idx - gap
    if hi - lo < 4:
        return (float(outer_idx), float(outer_idx), outer_strength, 0.0)

    window = profile[lo:hi]
    # Inner frame lines are usually a little weaker than the cut edge, so use a
    # lower bar than the outer search.
    peaks = _local_maxima(window, peak * inner_ratio)
    if peaks.size:
        rel = peaks[0] if from_start else peaks[-1]
        inner_idx = lo + int(rel)
    else:
        inner_idx = lo + int(np.argmax(window))
    inner_strength = float(profile[inner_idx])

    return (
        subpixel_centroid(profile, outer_idx),
        subpixel_centroid(profile, inner_idx),
        outer_strength,
        inner_strength,
    )


def detect_border_lines(
    image: np.ndarray,
    *,
    peak_ratio: float = 0.35,
    min_gap_frac: float = 0.03,
    working_width: int = 1200,
) -> tuple[BorderLines, float, np.ndarray]:
    """Detect the four outer and four inner border coordinates.

    Parameters
    ----------
    image
        BGR or grayscale, ideally already rectified by
        :func:`src.perspective_align.auto_align`.
    peak_ratio
        A candidate edge must reach this fraction of the strongest edge in the
        profile.  0.35 keeps printed frame lines while rejecting paper texture.
    min_gap_frac
        Minimum distance between the outer and the inner edge, as a fraction of
        the card span.  Guards against locking onto the same line twice.
    working_width
        Images are downscaled to this width before analysis.  A 4000 px photo
        carries no extra centering information — borders are several hundred
        pixels wide — and the Sobel pass is O(W*H).

    Returns
    -------
    ``(borders, confidence, col_profile)`` where ``confidence`` in [0, 1]
    reflects how much the detected peaks stand out from the profile median.
    """
    if image is None or image.size == 0:
        raise ValueError("empty image")

    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    scale = 1.0
    if gray.shape[1] > working_width:
        scale = working_width / gray.shape[1]
        gray = cv2.resize(
            gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
        )

    # Light denoise; a 3x3 median removes salt-and-pepper from phone sensors
    # without softening a high-contrast printed border.
    gray = cv2.medianBlur(gray, 3)

    col, row = gradient_profiles(gray)
    h, w = gray.shape[:2]

    ol, il, ol_s, il_s = _find_pair(col, peak_ratio=peak_ratio, min_gap_frac=min_gap_frac, from_start=True)
    orr, irr, or_s, ir_s = _find_pair(col, peak_ratio=peak_ratio, min_gap_frac=min_gap_frac, from_start=False)
    ot, it, ot_s, it_s = _find_pair(row, peak_ratio=peak_ratio, min_gap_frac=min_gap_frac, from_start=True)
    ob, ib, ob_s, ib_s = _find_pair(row, peak_ratio=peak_ratio, min_gap_frac=min_gap_frac, from_start=False)

    # Clamp to the image and force a strict ordering. Without this a symmetric
    # full-bleed card (inner == outer) raises inside BorderLines.
    eps = 0.5
    ol, orr = max(0.0, ol), min(float(w - 1), orr)
    ot, ob = max(0.0, ot), min(float(h - 1), ob)
    il = min(max(il, ol + eps), irr - eps)
    irr_i = max(min(irr, orr - eps), il + eps)
    it = min(max(it, ot + eps), ib - eps)
    ib_i = max(min(ib, ob - eps), it + eps)

    borders = BorderLines(
        outer_left=ol, inner_left=il, inner_right=irr_i, outer_right=orr,
        outer_top=ot, inner_top=it, inner_bottom=ib_i, outer_bottom=ob,
    )

    # Confidence: how far the four outer peaks rise above the profile floor.
    strengths = np.array([ol_s, or_s, ot_s, ob_s], dtype=np.float64)
    floors = np.array(
        [np.median(col), np.median(col), np.median(row), np.median(row)],
        dtype=np.float64,
    )
    contrast = np.divide(
        strengths - floors, strengths + 1e-9, out=np.zeros_like(strengths), where=strengths > 0
    )
    confidence = float(np.clip(contrast.mean(), 0.0, 1.0))

    # Inner edges that are almost as strong as the outer ones usually mean the
    # card is borderless; flag it so the caller can ask for a manual anchor.
    if min(il_s, ir_s, it_s, ib_s) < 0.25 * max(ol_s, or_s, ot_s, ob_s):
        confidence *= 0.6

    return borders.scaled(1.0 / scale if scale != 1.0 else 1.0), confidence, col


def annotate(
    image: np.ndarray,
    borders: BorderLines,
    *,
    color_outer: tuple[int, int, int] = (0, 215, 255),
    color_inner: tuple[int, int, int] = (255, 96, 0),
    thickness: int = 2,
) -> np.ndarray:
    """Draw the detected border lines onto a copy of ``image``."""
    out = image.copy()
    if out.ndim == 2:
        out = cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)
    h, w = out.shape[:2]
    for x in (borders.outer_left, borders.outer_right):
        cv2.line(out, (int(x), 0), (int(x), h - 1), color_outer, thickness)
    for y in (borders.outer_top, borders.outer_bottom):
        cv2.line(out, (0, int(y)), (w - 1, int(y)), color_outer, thickness)
    for x in (borders.inner_left, borders.inner_right):
        cv2.line(out, (int(x), 0), (int(x), h - 1), color_inner, thickness)
    for y in (borders.inner_top, borders.inner_bottom):
        cv2.line(out, (0, int(y)), (w - 1, int(y)), color_inner, thickness)
    return out
