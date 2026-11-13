"""Verify the scoring engine reproduces the numbers observed in a real report.

The fixture is a reconstruction of a commercial report's signal set. If the
engine is right, it must land on the same overall grade, the same limiting
dimension and the same institution predictions — not approximately, exactly.

Run::

    python -m tests.test_scoring
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.pipeline import load_signal_pack  # noqa: E402
from app.scoring import (  # noqa: E402
    DEFAULT_CONFIG,
    ScoringConfig,
    build_report,
    predict_institutions,
    score_dimension,
    tier_for,
    zone_scores,
)
from app.defect_model import Dimension, Side  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "fixtures", "sample_signals.json")


def _report():
    pack = load_signal_pack(FIXTURE)
    return pack, build_report(
        card_name=pack["card_name"],
        signals=pack["signals"],
        centering_metrics=pack["centering"],
    )


def test_zone_scores() -> bool:
    pack, _ = _report()
    scores = zone_scores(pack["signals"], Side.FRONT)
    expected = pack["expected"]["zone_scores"]
    ok = (
        abs(scores["top_left"] - expected["corner"]) < 1e-9
        and abs(scores["top"] - expected["edge"]) < 1e-9
    )
    print(f"  zone top_left={scores['top_left']:.2f} (want {expected['corner']}) "
          f"top={scores['top']:.2f} (want {expected['edge']})  {'OK' if ok else 'FAIL'}")
    return ok


def test_subgrades() -> bool:
    pack, rep = _report()
    want = pack["expected"]["subgrades"]
    got = rep.subgrades
    ok = all(abs(got[k] - v) < 1e-9 for k, v in want.items())
    for k in ("centering", "corners", "edges", "surface"):
        mark = "OK" if abs(got[k] - want[k]) < 1e-9 else "FAIL"
        print(f"  {k:10} got {got[k]:>4.1f}  want {want[k]:>4.1f}  {mark}")
    return ok


def test_overall_and_limiting() -> bool:
    pack, rep = _report()
    exp = pack["expected"]
    ok = (
        abs(rep.overall - exp["overall"]) < 1e-9
        and rep.tier == exp["tier"]
        and rep.limiting_factor is not None
        and rep.limiting_factor.value == exp["limiting_factor"]
    )
    print(f"  overall={rep.overall:g} tier={rep.tier} limiting="
          f"{rep.limiting_factor.value if rep.limiting_factor else None}  "
          f"{'OK' if ok else 'FAIL'}")
    return ok


def test_predictions() -> bool:
    pack, rep = _report()
    want = pack["expected"]["predictions"]
    ok = all(abs(rep.predictions[k] - v) < 1e-9 for k, v in want.items())
    print(f"  predictions={ {k: rep.predictions[k] for k in want} }  "
          f"want={want}  {'OK' if ok else 'FAIL'}")
    return ok


def test_penalty_sensitivity() -> bool:
    """Removing the consistency penalty must break the reproduction — proof it matters."""
    pack, _ = _report()
    flat = ScoringConfig(consistency_penalty=((1, 0.0),))
    without = score_dimension(pack["signals"], Side.FRONT, Dimension.CORNERS, flat)
    with_penalty = score_dimension(pack["signals"], Side.FRONT, Dimension.CORNERS)
    ok = abs(with_penalty - 9.0) < 1e-9 and abs(without - 9.5) < 1e-9
    print(f"  corners with penalty={with_penalty:g}  without={without:g}  "
          f"{'OK' if ok else 'FAIL'}")
    return ok


def test_tier_boundaries() -> bool:
    cases = [(10, "GEM MINT"), (9.5, "MINT"), (9, "MINT"), (8.5, "NM-MT"), (1, "POOR")]
    ok = all(tier_for(g) == t for g, t in cases)
    print(f"  tiers {'OK' if ok else 'FAIL'} :: "
          + ", ".join(f"{g}->{tier_for(g)}" for g, _ in cases))
    return ok


def main() -> int:
    print("=" * 66)
    print("SCORING ENGINE — reproducing an observed report")
    print("=" * 66)

    checks = [
        ("zone scores", test_zone_scores),
        ("subgrades", test_subgrades),
        ("overall + limiting factor", test_overall_and_limiting),
        ("institution predictions", test_predictions),
        ("consistency penalty sensitivity", test_penalty_sensitivity),
        ("tier boundaries", test_tier_boundaries),
    ]
    results = []
    for name, fn in checks:
        print(f"\n[{name}]")
        try:
            results.append((name, fn()))
        except Exception as exc:
            print(f"  ERROR: {exc}")
            results.append((name, False))

    passed = sum(1 for _, ok in results if ok)
    print("\n" + "=" * 66)
    print(f"{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
