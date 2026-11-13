"""Perspective rectification for card photographs.

A phone held "roughly" above a card introduces enough perspective that one
border can read 1.3x wider than its opposite.  Since centering is a ratio of
opposing border widths, that error goes straight into the result — a perfectly
centered card photographed at 8 degrees can measure as 58/42.

This module removes the problem in three stages:

1. **Coarse detection** — find the card as the largest bright quadrilateral.
2. **Sub-pixel refinement** — fit a line to the edge pixels under each of the
   four coarse sides and intersect them, which recovers corner positions to a
   fraction of a pixel even when the coarse quad was a few pixels off.
3. **Warp** — map those four corners onto an exact 63.5 : 88.9 rectangle.

Only stage 3 changes pixel values; stages 1 and 2 are measurement, and they
report their own confidence so a failed detection never silently produces a
plausible-looking wrong answer.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .centering_calculator import CARD_ASPECT

__all__ = [
    "AlignResult",
    "order_quad",
    "detect_card_quad",
    "refine_quad",
    "warp_card",
    "auto_align",
    "estimate_skew",
    "detect_glare_mask",
    "inpaint_glare",
    "clahe_normalise",
]


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #
def order_quad(points: np.ndarray) -> np.ndarray:
    """Order four points as top-left, top-right, bottom-right, bottom-left.

    Uses coordinate sums/differences rather than angles so it is stable for
    quads that are rotated up to ~45 degrees.
    """
    pts = np.asarray(points, dtype=np.float32).reshape(4, 2)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()  # y - x

    ordered = np.zeros((4, 2), dtype=np.float32)
    ordered[0] = pts[np.argmin(s)]   # top-left  -> smallest x+y
    ordered[2] = pts[np.argmax(s)]   # bottom-right -> largest x+y
    ordered[1] = pts[np.argmin(d)]   # top-right -> smallest y-x
    ordered[3] = pts[np.argmax(d)]   # bottom-left -> largest y-x
    return ordered


def _quad_area(q: np.ndarray) -> float:
    return float(cv2.contourArea(q.reshape(-1, 1, 2).astype(np.float32)))


def estimate_skew(quad: np.ndarray) -> float:
    """Rotation of the card's top edge relative to horizontal, in degrees."""
    q = order_quad(quad)
    dx = q[1][0] - q[0][0]
    dy = q[1][1] - q[0][1]
    return float(np.degrees(np.arctan2(dy, dx)))


# --------------------------------------------------------------------------- #
# Coarse detection
# --------------------------------------------------------------------------- #
def _candidate_quads(gray: np.ndarray) -> list[np.ndarray]:
    """Collect plausible card quadrilaterals from several segmentation strategies.

    Different card/background combinations fail different strategies, so we try
    a handful and let :func:`detect_card_quad` rank the survivors.  Cheap, and
    far more reliable than tuning one pipeline.
    """
    h, w = gray.shape[:2]
    min_area = 0.08 * h * w
    out: list[np.ndarray] = []

    blur = cv2.GaussianBlur(gray, (5, 5), 0)

    def collect(binary: np.ndarray) -> None:
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in sorted(contours, key=cv2.contourArea, reverse=True)[:12]:
            if cv2.contourArea(c) < min_area:
                continue
            peri = cv2.arcLength(c, True)
            for eps in (0.02, 0.03, 0.05, 0.08):
                approx = cv2.approxPolyDP(c, eps * peri, True)
                if len(approx) == 4 and cv2.isContourConvex(approx):
                    out.append(approx.reshape(4, 2).astype(np.float32))
                    break

    # Strategy A: Canny on a blurred copy.
    edges = cv2.Canny(blur, 40, 120)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    collect(cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)))

    # Strategy B: Otsu — assumes the card differs in brightness from the surface.
    _, otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    collect(cv2.morphologyEx(otsu, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8)))
    collect(cv2.morphologyEx(255 - otsu, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8)))

    # Strategy C: adaptive threshold — survives uneven lighting.
    adaptive = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 51, 5
    )
    collect(cv2.morphologyEx(adaptive, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8)))

    return out


def _quad_score(quad: np.ndarray, shape: tuple[int, int]) -> float:
    """Rank a candidate: big, card-shaped, and reasonably axis-aligned wins."""
    h, w = shape
    area = _quad_area(quad)
    if area <= 0:
        return -1.0

    area_frac = area / float(h * w)

    # Aspect after ordering: long side / short side should approach 88.9/63.5.
    q = order_quad(quad)
    top = np.linalg.norm(q[1] - q[0])
    right = np.linalg.norm(q[2] - q[1])
    long_side = max(top, right)
    short_side = min(top, right) or 1e-6
    aspect = long_side / short_side
    aspect_err = abs(aspect - 1.0 / CARD_ASPECT) / (1.0 / CARD_ASPECT)

    skew = abs(estimate_skew(quad))
    skew_penalty = min(skew / 45.0, 1.0)

    # Reward filling the frame: a card occupying 10 % of the photo has too few
    # pixels per border to measure.
    fill = min(area_frac / 0.55, 1.0)

    return (
        0.45 * fill
        + 0.40 * max(0.0, 1.0 - aspect_err)
        + 0.15 * (1.0 - skew_penalty)
        - 0.5 * max(0.0, aspect_err - 0.35)
    )


def detect_card_quad(
    image: np.ndarray,
    *,
    working_width: int = 900,
) -> tuple[np.ndarray, float]:
    """Detect the card outline. Returns ``(quad_in_original_pixels, score)``.

    Raises ``ValueError`` when nothing card-shaped is found — callers should
    surface that to the user rather than guessing.
    """
    if image is None or image.size == 0:
        raise ValueError("empty image")

    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    scale = 1.0
    if gray.shape[1] > working_width:
        scale = working_width / gray.shape[1]
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    cands = _candidate_quads(gray)
    if not cands:
        # Last resort: min-area rectangle around the largest contour.
        edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 120)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            raise ValueError("no card-shaped contour found")
        biggest = max(contours, key=cv2.contourArea)
        box = cv2.boxPoints(cv2.minAreaRect(biggest)).astype(np.float32)
        cands = [box]

    scored = [(_quad_score(q, gray.shape[:2]), q) for q in cands]
    scored.sort(key=lambda t: t[0], reverse=True)
    best_score, best = scored[0]

    return best / scale, float(best_score)


# --------------------------------------------------------------------------- #
# Sub-pixel refinement
# --------------------------------------------------------------------------- #
def _fit_line_through_band(
    gray: np.ndarray, p0: np.ndarray, p1: np.ndarray, band: float
) -> tuple[np.ndarray, np.ndarray] | None:
    """Fit a straight line to the strongest gradient ridge inside a band.

    The band is the strip of pixels within ``band`` px of segment ``p0 -> p1``.
    We take the gradient magnitude, keep the local maxima along the band's
    normal direction, and fit a line through them with a Huber loss so a few
    outlier pixels (a serial number, a foil sparkle) cannot tilt the fit.
    """
    h, w = gray.shape[:2]
    x0, y0 = float(p0[0]), float(p0[1])
    x1, y1 = float(p1[0]), float(p1[1])
    length = float(np.hypot(x1 - x0, y1 - y0))
    if length < 20:
        return None

    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    nx, ny = -uy, ux

    # Sample points along the segment, then walk +/- band along the normal.
    steps = int(np.clip(length, 40, 600))
    ts = np.linspace(0.0, 1.0, steps)
    base_x = x0 + (x1 - x0) * ts
    base_y = y0 + (y1 - y0) * ts

    offsets = np.arange(-band, band + 1, 1.0)
    sx = (base_x[:, None] + nx * offsets[None, :]).astype(np.int32)
    sy = (base_y[:, None] + ny * offsets[None, :]).astype(np.int32)

    valid = (sx >= 0) & (sx < w) & (sy >= 0) & (sy < h)
    if valid.sum() < 0.5 * sx.size:
        return None

    g = gray.astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.hypot(gx, gy)

    samples = np.zeros(sx.shape, dtype=np.float32)
    samples[valid] = mag[sy[valid], sx[valid]]

    # Ridge per row: strongest offset, then sub-pixel via parabola.
    best_i = np.argmax(samples, axis=1)
    strengths = samples[np.arange(samples.shape[0]), best_i]
    keep = strengths >= np.percentile(strengths, 60)  # drop weak rows

    sub = np.empty_like(best_i, dtype=np.float32)
    for k in range(samples.shape[0]):
        i = int(best_i[k])
        if 0 < i < samples.shape[1] - 1:
            a, b, c = samples[k, i - 1], samples[k, i], samples[k, i + 1]
            denom = a - 2 * b + c
            sub[k] = i + (0.5 * (a - c) / denom if abs(denom) > 1e-9 else 0.0)
        else:
            sub[k] = float(i)

    offs = sub[keep] - band
    px = base_x[keep] + nx * offs
    py = base_y[keep] + ny * offs

    if px.size < 20:
        return None

    pts = np.stack([px, py], axis=1).astype(np.float32)
    vx, vy, x, y = cv2.fitLine(pts, cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
    return np.array([float(vx), float(vy)]), np.array([float(x), float(y)])


def _intersect(l1, l2) -> np.ndarray:
    """Intersect two lines given as (direction, point_on_line)."""
    d1, p1 = l1
    d2, p2 = l2
    # Line 1: p1 + t*d1 ; Line 2: p2 + s*d2 -> solve 2x2
    a = np.array([[d1[0], -d2[0]], [d1[1], -d2[1]]], dtype=np.float64)
    b = np.array([p2[0] - p1[0], p2[1] - p1[1]], dtype=np.float64)
    det = np.linalg.det(a)
    if abs(det) < 1e-9:
        return np.array([np.nan, np.nan])
    t, _ = np.linalg.solve(a, b)
    return (p1 + t * d1).astype(np.float32)


def refine_quad(gray: np.ndarray, quad: np.ndarray, band: float = 6.0) -> tuple[np.ndarray, int]:
    """Snap a coarse quad onto the true edges by fitting and intersecting lines.

    Returns ``(refined_quad, lines_fitted)``.  Falls back to the input quad for
    any side that could not be fitted, so a partial refinement is still usable.
    """
    q = order_quad(quad)
    sides = [(0, 1), (1, 2), (2, 3), (3, 0)]  # tl-tr, tr-br, br-bl, bl-tl

    lines = []
    fitted = 0
    for a, b in sides:
        res = _fit_line_through_band(gray, q[a], q[b], band)
        if res is not None:
            lines.append(res)
            fitted += 1
        else:
            # Synthetic line straight through the coarse side.
            d = q[b] - q[a]
            n = float(np.linalg.norm(d)) or 1.0
            lines.append((d / n, q[a].astype(np.float32)))

    corners = []
    for i in range(4):
        # Corner i is the intersection of side (i-1) and side i.
        prev_line = lines[(i - 1) % 4]
        cur_line = lines[i]
        pt = _intersect(prev_line, cur_line)
        if not np.all(np.isfinite(pt)):
            pt = q[i]
        corners.append(pt)

    refined = np.array(corners, dtype=np.float32)

    # Reject a refinement that collapses or explodes the quad.
    if _quad_area(refined) < 0.5 * _quad_area(q) or _quad_area(refined) > 2.0 * _quad_area(q):
        return q, fitted
    return refined, fitted


# --------------------------------------------------------------------------- #
# Glare handling
# --------------------------------------------------------------------------- #
def detect_glare_mask(image: np.ndarray, v_thresh: int = 242, s_thresh: int = 38) -> np.ndarray:
    """Return a binary mask of specular highlights (bright AND desaturated)."""
    if image.ndim == 2:
        bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    else:
        bgr = image
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    v, s = hsv[:, :, 2], hsv[:, :, 1]
    mask = ((v >= v_thresh) & (s <= s_thresh)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return mask


def inpaint_glare(image: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray:
    """Replace specular highlights with plausible surroundings.

    This helps *visual* inspection and the loupe, but note the honest caveat:
    inpainting fabricates pixels.  Never grade surface condition on an
    inpainted image — only use it to unblock border detection.
    """
    if mask is None:
        mask = detect_glare_mask(image)
    if not mask.any():
        return image
    return cv2.inpaint(image, mask, 3, cv2.INPAINT_TELEA)


def clahe_normalise(image: np.ndarray, clip: float = 2.0, grid: int = 8) -> np.ndarray:
    """Even out backlight gradients before edge detection.

    Applied to the L channel only, so border colour stays intact.
    """
    if image.ndim == 2:
        return cv2.createCLAHE(clipLimit=clip, tileGridSize=(grid, grid)).apply(image)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=clip, tileGridSize=(grid, grid)).apply(l)
    return cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)


# --------------------------------------------------------------------------- #
# Warp
# --------------------------------------------------------------------------- #
def warp_card(
    image: np.ndarray,
    quad: np.ndarray,
    *,
    out_width: int = 1000,
    preserve_aspect: bool = True,
    margin_px: int = 28,
    pad_value: int = 118,
) -> np.ndarray:
    """Rectify ``quad`` onto a clean rectangle.

    With ``preserve_aspect`` the output is forced to 63.5 : 88.9, which makes
    the pixel-to-mm conversion in :mod:`src.centering_calculator` exact.  Set it
    to ``False`` only if you are measuring a non-standard card.

    ``margin_px`` wraps the rectified card in a flat neutral border.  This is
    not cosmetic: the edge detector locates the card's cut edge by finding a
    gradient, and a card flush against the image boundary has no gradient to
    find.  Keeping a margin also mirrors how the photo was taken, so the
    downstream geometry is unchanged.
    """
    q = order_quad(quad)
    if preserve_aspect:
        out_height = int(round(out_width / CARD_ASPECT))
    else:
        w_px = float(np.linalg.norm(q[1] - q[0]))
        h_px = float(np.linalg.norm(q[3] - q[0]))
        out_height = int(round(out_width * (h_px / max(w_px, 1e-6))))

    dst = np.array(
        [[0, 0], [out_width - 1, 0], [out_width - 1, out_height - 1], [0, out_height - 1]],
        dtype=np.float32,
    )
    m = cv2.getPerspectiveTransform(q, dst)
    warped = cv2.warpPerspective(
        image, m, (out_width, out_height),
        flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE,
    )
    if margin_px > 0:
        warped = cv2.copyMakeBorder(
            warped, margin_px, margin_px, margin_px, margin_px,
            cv2.BORDER_CONSTANT, value=(pad_value, pad_value, pad_value)
            if warped.ndim == 3 else pad_value,
        )
    return warped


# --------------------------------------------------------------------------- #
# One-shot pipeline
# --------------------------------------------------------------------------- #
@dataclass
class AlignResult:
    warped: np.ndarray
    quad: np.ndarray
    refine_score: float
    skew_deg: float
    glare_fraction: float
    notes: tuple[str, ...]

    def __repr__(self) -> str:  # keep notebooks readable
        h, w = self.warped.shape[:2]
        return (
            f"AlignResult({w}x{h}, skew={self.skew_deg:.2f}deg, "
            f"refine={self.refine_score:.0f} lines, glare={self.glare_fraction:.1%})"
        )


def auto_align(
    image: np.ndarray,
    *,
    out_width: int = 1000,
    use_clahe: bool = True,
    fix_glare: bool = False,
    manual_quad: np.ndarray | None = None,
) -> AlignResult:
    """Detect → refine → warp, with an honest record of what happened.

    ``manual_quad`` skips detection entirely; the four points are still refined,
    which is usually what you want after a user drags the handles into place.
    """
    notes: list[str] = []
    if image is None or image.size == 0:
        raise ValueError("empty image")

    work = clahe_normalise(image) if use_clahe else image

    if manual_quad is not None:
        quad = order_quad(manual_quad)
        coarse_score = float("nan")
        notes.append("quad supplied manually")
    else:
        quad, coarse_score = detect_card_quad(work)
        if coarse_score < 0.55:
            notes.append(f"weak coarse detection (score {coarse_score:.2f}); verify corners")

    gray = work if work.ndim == 2 else cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    refined, fitted = refine_quad(gray, quad, band=max(3.0, gray.shape[1] / 220.0))
    if fitted < 4:
        notes.append(f"only {fitted}/4 sides refined; corners may be a few px off")

    skew = estimate_skew(refined)
    if abs(skew) > 3.0:
        notes.append(f"card was rotated {skew:+.1f} degrees in frame")

    glare_frac = float(detect_glare_mask(work).mean() / 255.0)
    if glare_frac > 0.01:
        notes.append(f"specular highlights cover {glare_frac:.1%} of the frame")

    src = inpaint_glare(work) if (fix_glare and glare_frac > 0.01) else work
    warped = warp_card(src, refined, out_width=out_width)

    return AlignResult(
        warped=warped,
        quad=refined,
        refine_score=float(fitted),
        skew_deg=skew,
        glare_fraction=glare_frac,
        notes=tuple(notes),
    )
