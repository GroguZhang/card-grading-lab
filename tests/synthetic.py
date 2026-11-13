"""Synthetic card generator used by the accuracy checks.

Real cards are not distributable, so the test suite builds cards with *known*
centering and then asks the detector to recover the number.  A synthetic card
is a best case for edge detection (clean, high-contrast edges), which means
passing here is necessary but not sufficient — the checker also injects blur,
noise and perspective so the detector cannot rely on pristine gradients.

Important: the returned ratios are the *quantised* truth, i.e. the ratio that
the drawn pixels actually express, not the ratio that was requested.  A border
span of 105 px cannot represent 50.0 % exactly; it rounds to 52/105 = 49.52 %.
Comparing a measurement against the requested value would therefore flag a
perfect detector as broken.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from src.centering_calculator import CARD_ASPECT


@dataclass(frozen=True)
class SyntheticCard:
    image: np.ndarray
    lr: tuple[float, float]
    tb: tuple[float, float]

    @property
    def left_pct(self) -> float:
        return self.lr[0]

    @property
    def top_pct(self) -> float:
        return self.tb[0]


def make_card(
    left_pct: float = 55.0,
    top_pct: float = 52.0,
    *,
    card_width: int = 1000,
    border_frac: float = 0.17,
    noise: float = 4.0,
    blur: int = 0,
    seed: int = 0,
) -> SyntheticCard:
    """Render a bordered card on a contrasting background.

    Parameters
    ----------
    left_pct, top_pct
        Requested centering of the *artwork* inside the card, in percent of the
        border span.  The opposite sides are the complements.
    border_frac
        Total border span as a fraction of the card dimension.
    noise, blur
        Degradations applied after drawing, to keep the detector honest.
    """
    rng = np.random.default_rng(seed)
    card_h = int(round(card_width / CARD_ASPECT))

    # Card body: white border, dark artwork panel.
    card = np.full((card_h, card_width, 3), 246, dtype=np.uint8)

    bw = int(round(card_width * border_frac))
    bh = int(round(card_h * border_frac))
    left = int(round(bw * left_pct / 100.0))
    right = bw - left
    top = int(round(bh * top_pct / 100.0))
    bottom = bh - top

    art = card.copy()
    art[top:card_h - bottom, left:card_width - right] = (38, 44, 58)
    cv2.rectangle(
        art,
        (left, top),
        (card_width - right - 1, card_h - bottom - 1),
        (20, 24, 34),
        thickness=max(2, card_width // 220),
    )
    # Some internal structure so the inner edge is not the only strong gradient.
    cv2.putText(
        art, "SYNTHETIC", (left + 16, top + 62),
        cv2.FONT_HERSHEY_SIMPLEX, 1.1, (210, 210, 210), 3, cv2.LINE_AA,
    )

    if noise > 0:
        speckle = rng.normal(0, noise, art.shape)
        art = np.clip(art.astype(np.float32) + speckle, 0, 255).astype(np.uint8)

    if blur > 0:
        k = blur * 2 + 1
        art = cv2.GaussianBlur(art, (k, k), 0)

    # Mount on a mid-grey surface with a margin, like a real photograph.
    margin = int(card_width * 0.12)
    canvas = np.full(
        (card_h + 2 * margin, card_width + 2 * margin, 3), 118, dtype=np.uint8
    )
    # Uneven lighting so Otsu/adaptive thresholding has something to fight.
    yy = np.linspace(0.82, 1.12, canvas.shape[0])[:, None, None]
    canvas = np.clip(canvas * yy, 0, 255).astype(np.uint8)
    canvas[margin:margin + card_h, margin:margin + card_width] = art

    return SyntheticCard(
        image=canvas,
        lr=(100.0 * left / bw, 100.0 * right / bw),
        tb=(100.0 * top / bh, 100.0 * bottom / bh),
    )


def apply_perspective(
    canvas: np.ndarray,
    *,
    tilt_x: float = 0.0,
    tilt_y: float = 0.0,
) -> np.ndarray:
    """Warp the canvas so the card is photographed at an angle.

    ``tilt_x`` / ``tilt_y`` are fractions of the card size by which the far
    corners are pulled inward, producing a trapezoid.
    """
    h, w = canvas.shape[:2]
    src = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    dx = w * tilt_x
    dy = h * tilt_y
    dst = np.array(
        [[0 + dx, 0 + dy], [w - 1 - dx, 0 + dy],
         [w - 1 - dx, h - 1 - dy], [0 + dx, h - 1 - dy]],
        dtype=np.float32,
    )
    m = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(canvas, m, (w, h), borderValue=(118, 118, 118))


if __name__ == "__main__":  # manual visual check
    import os

    os.makedirs("examples", exist_ok=True)
    card = make_card(58, 47, noise=5, blur=1, seed=3)
    cv2.imwrite("examples/synthetic_58_47.png", card.image)
    cv2.imwrite(
        "examples/synthetic_tilted.png",
        apply_perspective(card.image, tilt_x=0.06, tilt_y=0.03),
    )
    print(
        f"wrote examples/synthetic_*.png {card.image.shape} "
        f"true L/R {card.lr[0]:.2f}/{card.lr[1]:.2f}  "
        f"T/B {card.tb[0]:.2f}/{card.tb[1]:.2f}"
    )
