"""Accuracy and sanity checks. Run from the project root::

    python -m tests.run_checks

No pytest dependency: this is a readout you can eyeball, and it exits non-zero
if any tolerance is violated so it also works as a CI gate.
"""

from __future__ import annotations

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import (  # noqa: E402
    auto_align,
    detect_border_lines,
    get_standard,
    measure_from_borders,
    measure_from_ratios,
)
from tests.synthetic import apply_perspective, make_card  # noqa: E402

LR_TOL = 0.75   # percentage points
TB_TOL = 0.75
PERSP_TOL = 2.5  # perspective leaves interpolation error behind


def measure(img: np.ndarray):
    borders, conf, _ = detect_border_lines(img)
    return measure_from_borders(borders, confidence=conf), borders, conf


def check_straight_cases() -> list[tuple]:
    rows = []
    for left in (50, 52, 55, 58, 60, 65, 70):
        for top in (50, 53, 58):
            card = make_card(left, top, noise=4, blur=1, seed=int(left + top))
            rep, borders, conf = measure(card.image)
            lr_err = abs(rep.left_pct - card.left_pct)
            tb_err = abs(rep.top_pct - card.top_pct)
            rows.append(
                ("flat", card.left_pct, card.top_pct, rep.left_pct, rep.top_pct, lr_err, tb_err, conf)
            )
    return rows


def check_perspective_cases() -> list[tuple]:
    rows = []
    for left, top, tx, ty in (
        (55, 52, 0.05, 0.03),
        (58, 47, 0.07, 0.02),
        (60, 55, 0.04, 0.05),
        (50, 50, 0.06, 0.04),
    ):
        card = make_card(left, top, noise=4, blur=1, seed=int(left * 3 + top))
        tilted = apply_perspective(card.image, tilt_x=tx, tilt_y=ty)
        aligned = auto_align(tilted, out_width=900)
        rep, borders, conf = measure(aligned.warped)
        lr_err = abs(rep.left_pct - card.left_pct)
        tb_err = abs(rep.top_pct - card.top_pct)
        rows.append(
            ("tilted", card.left_pct, card.top_pct, rep.left_pct, rep.top_pct, lr_err, tb_err, conf)
        )
    return rows


def check_standard_ladder() -> bool:
    ok = True
    for name in ("PSA", "BGS", "CGC", "SGC", "TAG", "EDGE"):
        std = get_standard(name)
        assert std.tiers, name
        # A 50/50 card must top out the ladder; a 95/5 card must not.
        top = std.grade(50.0, "front")
        bottom = std.grade(95.0, "front")
        if top is not std.tiers[0]:
            print(f"  FAIL {name}: 50/50 gave {top.label}, expected {std.tiers[0].label}")
            ok = False
        if bottom is std.tiers[0]:
            print(f"  FAIL {name}: 95/5 still scored the best tier")
            ok = False
    return ok


def check_ratio_roundtrip() -> bool:
    rep = measure_from_ratios((52, 48), (47, 53))
    ok = (
        abs(rep.lr_worst_pct - 52) < 1e-9
        and abs(rep.tb_worst_pct - 53) < 1e-9
        and rep.worst_axis == "T/B"
        and rep.grade("PSA", "front").label == "10"
    )
    if not ok:
        print("  FAIL ratio roundtrip:", rep.to_dict())
    return ok


def main() -> int:
    print("=" * 78)
    print("CENTERING DETECTOR ACCURACY")
    print("=" * 78)

    rows = check_straight_cases() + check_perspective_cases()
    print(f"{'case':8} {'true L/T':>10} {'meas L/T':>14} {'err L':>7} {'err T':>7} {'conf':>6}")
    print("-" * 78)

    worst_lr = worst_tb = 0.0
    failures = 0
    for case, tl, tt, ml, mt, elr, etb, conf in rows:
        tol = LR_TOL if case == "flat" else PERSP_TOL
        flag = "" if (elr <= tol and etb <= tol) else "   <-- OUT"
        if elr > tol or etb > tol:
            failures += 1
        worst_lr = max(worst_lr, elr)
        worst_tb = max(worst_tb, etb)
        print(
            f"{case:8} {tl:5.1f}/{tt:<5.1f} {ml:6.2f}/{mt:<6.2f} {elr:6.2f} {etb:6.2f} {conf:6.2f}{flag}"
        )

    print("-" * 78)
    print(f"worst L/R error {worst_lr:.2f} pp   worst T/B error {worst_tb:.2f} pp")

    print("\n" + "=" * 78)
    print("STANDARD LADDER")
    print("=" * 78)
    ladder_ok = check_standard_ladder()
    roundtrip_ok = check_ratio_roundtrip()
    print(f"ladder monotonic + boundaries: {'PASS' if ladder_ok else 'FAIL'}")
    print(f"ratio roundtrip:               {'PASS' if roundtrip_ok else 'FAIL'}")

    # Show the headline table the README quotes.
    print("\n" + "=" * 78)
    print("SAMPLE REPORT  (55.4 / 44.6 front)")
    print("=" * 78)
    rep = measure_from_ratios((55.4, 44.6), (49.8, 50.2))
    print(rep.summary())
    print("grades (front):", rep.grade_table())
    print(f"PSA headroom: {rep.headroom('PSA'):+.2f} pp  "
          f"borderline={rep.is_borderline('PSA')}")
    print("note: rounding 55.4 to 55 would report a false PASS" if rep.left_pct > 55 else "")

    ok = failures == 0 and ladder_ok and roundtrip_ok
    print("\n" + ("ALL CHECKS PASSED" if ok else f"{failures} CASE(S) OUT OF TOLERANCE"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
