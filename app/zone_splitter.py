"""Split a rectified card into the nine regions a report talks about.

Why overlap matters: a corner defect does not respect an arbitrary grid. If the
top-left crop ends exactly at 18 % of the card, a whitening chip that straddles
that line appears as half a defect in both the corner crop and the top-edge
crop, and a vision model may report neither. Expanding every crop outward by a
margin removes that blind spot at the cost of a little duplicated context.

Crops are returned at a fixed working size so the downstream model sees a
consistent scale regardless of input resolution — a 12 MP photo and a 2 MP
photo should not produce different verdicts.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

__all__ = ["ZoneCrop", "split_zones", "split_both_sides", "ZONE_ORDER"]

#: Fixed iteration order so reports and logs are stable.
ZONE_ORDER: tuple[str, ...] = (
    "top_left",
    "top_right",
    "bottom_left",
    "bottom_right",
    "top",
    "bottom",
    "left",
    "right",
    "center",
)


@dataclass(slots=True)
class ZoneCrop:
    """One crop, plus where it came from so evidence can be drawn back on the card."""

    zone: str
    side: str
    image: np.ndarray
    rect: tuple[int, int, int, int]  # x, y, w, h in card coordinates

    @property
    def key(self) -> str:
        return f"{self.side}:{self.zone}"


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def split_zones(
    card: np.ndarray,
    *,
    side: str = "front",
    corner_ratio: float = 0.20,
    edge_thickness_ratio: float = 0.14,
    edge_length_ratio: float = 0.52,
    center_ratio: float = 0.50,
    overlap: float = 0.25,
    out_size: int = 384,
) -> dict[str, ZoneCrop]:
    """Cut the card into 4 corners, 4 edges and a surface centre.

    Parameters are ratios of the card's own dimensions so the result is
    resolution-independent. ``overlap`` expands each crop by that fraction of
    its own size on every side.
    """
    if card is None or card.size == 0:
        raise ValueError("empty card image")

    h, w = card.shape[:2]
    cw = max(8, int(round(w * corner_ratio)))
    ch = max(8, int(round(h * corner_ratio)))
    et = max(6, int(round(h * edge_thickness_ratio)))   # horizontal band thickness
    el = int(round(w * edge_length_ratio))              # horizontal band length
    ev = max(6, int(round(w * edge_thickness_ratio)))   # vertical band thickness
    eh = int(round(h * edge_length_ratio))              # vertical band length

    def pad(x: int, y: int, bw: int, bh: int) -> tuple[int, int, int, int]:
        px = int(round(bw * overlap))
        py = int(round(bh * overlap))
        x0 = _clamp(x - px, 0, w - 1)
        y0 = _clamp(y - py, 0, h - 1)
        x1 = _clamp(x + bw + px, x0 + 4, w)
        y1 = _clamp(y + bh + py, y0 + 4, h)
        return x0, y0, x1 - x0, y1 - y0

    raw: dict[str, tuple[int, int, int, int]] = {
        "top_left": pad(0, 0, cw, ch),
        "top_right": pad(w - cw, 0, cw, ch),
        "bottom_left": pad(0, h - ch, cw, ch),
        "bottom_right": pad(w - cw, h - ch, cw, ch),
        "top": pad((w - el) // 2, 0, el, et),
        "bottom": pad((w - el) // 2, h - et, el, et),
        "left": pad(0, (h - eh) // 2, ev, eh),
        "right": pad(w - ev, (h - eh) // 2, ev, eh),
        "center": pad(
            int(w * (0.5 - center_ratio / 2)),
            int(h * (0.5 - center_ratio / 2)),
            int(w * center_ratio),
            int(h * center_ratio),
        ),
    }

    out: dict[str, ZoneCrop] = {}
    for zone in ZONE_ORDER:
        x, y, bw, bh = raw[zone]
        patch = card[y:y + bh, x:x + bw]
        if patch.size == 0:
            continue
        interp = cv2.INTER_AREA if patch.shape[1] > out_size else cv2.INTER_CUBIC
        resized = cv2.resize(patch, (out_size, out_size), interpolation=interp)
        out[zone] = ZoneCrop(zone=zone, side=side, image=resized, rect=raw[zone])
    return out


def split_both_sides(
    front: np.ndarray,
    back: np.ndarray | None = None,
    **kwargs,
) -> dict[str, ZoneCrop]:
    """Convenience wrapper producing ``{"front:top_left": ...}`` style keys.

    Keying by ``side:zone`` is not cosmetic: with plain zone names the back
    crops silently overwrite the front ones, and every downstream step then
    inspects the wrong side while believing it has both.
    """
    crops: dict[str, ZoneCrop] = {}
    for side, card in (("front", front), ("back", back)):
        if card is None or not getattr(card, "size", 0):
            continue
        crops.update({c.key: c for c in split_zones(card, side=side, **kwargs).values()})
    return crops


def contact_sheet(crops: dict[str, ZoneCrop], cols: int = 3, gap: int = 6) -> np.ndarray:
    """Tile crops into one image for quick visual review or prompt batching."""
    if not crops:
        raise ValueError("no crops")
    ordered = [crops[k] for k in sorted(crops)]
    size = ordered[0].image.shape[0]
    rows = (len(ordered) + cols - 1) // cols
    sheet = np.full(
        (rows * size + (rows + 1) * gap, cols * size + (cols + 1) * gap, 3),
        24,
        dtype=np.uint8,
    )
    for i, crop in enumerate(ordered):
        r, c = divmod(i, cols)
        y = gap + r * (size + gap)
        x = gap + c * (size + gap)
        img = crop.image
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        sheet[y:y + size, x:x + size] = img
    return sheet
