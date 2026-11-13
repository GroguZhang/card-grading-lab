"""Offline acceptance for the vision layer — no API key, no network.

A fake provider stands in for DeepSeek so the parts that actually break in
production get exercised here: caching, partial failure, vocabulary
enforcement, and the mapping from model output to scoring signals.

Run::

    python -m tests.test_vision
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import VisionSettings  # noqa: E402
from app.pipeline import analyze  # noqa: E402
from app.report_render import _zone_analysis, report_to_context  # noqa: E402
from app.vision import findings_to_signals, inspect_zones  # noqa: E402
from app.vision.deepseek import DeepSeekVisionProvider, VisionError  # noqa: E402
from app.vision.runner import build_requests  # noqa: E402
from app.vision.types import VisionUsage, ZoneFinding, ZoneRequest  # noqa: E402
from app.zone_splitter import split_both_sides  # noqa: E402
from tests.synthetic import make_card  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[{name}] {detail}  OK")
    else:
        FAIL += 1
        print(f"[{name}] {detail}  FAIL")


class FakeProvider:
    """Reports one whitening chip at the top-left corner and nothing elsewhere."""

    name = "fake"
    model = "fake-vlm-1"

    def __init__(self, fail_zones: set[str] | None = None) -> None:
        self.calls = 0
        self.fail_zones = fail_zones or set()

    def inspect(self, req: ZoneRequest) -> tuple[list[ZoneFinding], VisionUsage]:
        self.calls += 1
        if req.key in self.fail_zones:
            raise VisionError("simulated 429")
        findings = []
        if req.zone == "top_left":
            findings.append(
                ZoneFinding(
                    zone=req.zone,
                    side=req.side,
                    defect="whitening",
                    severity="minor",
                    confidence=0.81,
                    evidence="角尖约 2mm 白色磨损",
                )
            )
        return findings, VisionUsage(requests=1, images=1, prompt_tokens=700, completion_tokens=60)


class QuietProvider:
    """Finds nothing anywhere — the "card is clean" case."""

    name = "fake"
    model = "fake-vlm-1"

    def inspect(self, req: ZoneRequest) -> tuple[list[ZoneFinding], VisionUsage]:
        return [], VisionUsage(requests=1, images=1, prompt_tokens=700, completion_tokens=20)


def main() -> int:
    card = make_card(49, 45, noise=4, blur=1, seed=7)
    crops = split_both_sides(card.image, None, out_size=704)
    reqs = build_requests(crops)
    check("request count", len(reqs) == 9, f"{len(reqs)} zones")

    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp) / "cache"

        # -- run 1: real calls ------------------------------------------- #
        prov = FakeProvider()
        out = inspect_zones(reqs, prov, concurrency=4, cache_dir=cache)
        check("zones inspected", len(out.zones_inspected) == 9, f"{len(out.zones_inspected)}")
        check("findings", len(out.findings) == 1 and out.findings[0].defect == "whitening",
              str([f.as_dict() for f in out.findings]))
        check("usage summed", out.usage.prompt_tokens == 9 * 700,
              f"{out.usage.prompt_tokens} prompt tokens")
        check("cost computed", 0 < out.usage.cost_usd() < 0.01, f"${out.usage.cost_usd():.6f}")
        calls_after_first = prov.calls

        # -- run 2: everything must come from cache ---------------------- #
        prov2 = FakeProvider()
        out2 = inspect_zones(reqs, prov2, concurrency=4, cache_dir=cache)
        check("cache reuse", prov2.calls == 0 and out2.usage.cache_hits == 9,
              f"{prov2.calls} calls, {out2.usage.cache_hits} hits")
        check("cache identical", [f.as_dict() for f in out2.findings] == [f.as_dict() for f in out.findings],
              "same findings after cache")
        check("first run did call", calls_after_first == 9, f"{calls_after_first} calls")

        # -- failure isolation ------------------------------------------- #
        cache2 = Path(tmp) / "cache2"
        broken = FakeProvider(fail_zones={"front:bottom_right"})
        out3 = inspect_zones(reqs, broken, concurrency=4, cache_dir=cache2)
        check("one zone failed", len(out3.errors) == 1 and len(out3.zones_inspected) == 8,
              f"{len(out3.errors)} errors / {len(out3.zones_inspected)} ok")
        check("failure is loud", "bottom_right" in out3.errors[0], out3.errors[0][:60])

    # -- vocabulary enforcement ------------------------------------------ #
    prov = DeepSeekVisionProvider.__new__(DeepSeekVisionProvider)
    req = ZoneRequest(zone="top_left", side="front", image=crops["front:top_left"].image)
    ok_reject = False
    try:
        DeepSeekVisionProvider._validate({"findings": [{"defect": "worn corner"}]}, req)
    except VisionError:
        ok_reject = True
    check("rejects invented defect", ok_reject, "'worn corner' refused")

    kept = DeepSeekVisionProvider._validate(
        {"findings": [{"defect": "whitening", "severity": "weird", "confidence": 3}]}, req
    )
    check("clamps severity/confidence",
          kept[0].severity == "minor" and kept[0].confidence == 1.0,
          f"{kept[0].severity}/{kept[0].confidence}")

    # -- signal mapping --------------------------------------------------- #
    sigs = findings_to_signals(
        [
            ZoneFinding("top_left", "front", "whitening", "minor", 0.8),
            ZoneFinding("left", "front", "fraying", "minor", 0.6),
            ZoneFinding("center", "front", "scratch", "major", 0.7),
        ]
    )
    check("map to categories",
          [s.category.value for s in sigs] == ["corner", "edge", "surface"],
          str([s.category.value for s in sigs]))
    check("source is ai_flagged", all(s.source.value == "ai_flagged" for s in sigs), "ai_flagged")

    # -- end to end through the pipeline ---------------------------------- #
    settings = VisionSettings(provider="fake", model="fake-vlm-1", api_key="x", zone_size=704)
    result = analyze(
        card.image,
        None,
        card_name="vision smoke",
        vision=True,
        vision_settings=settings,
        provider=FakeProvider(),
    )
    rep = result.report
    corners = rep.dimension("corners")
    zone = next(z for z in rep.zones if z.side.value == "front" and z.zone == "top_left")
    check("vision reached zone scoring", zone.score < 10, f"top_left zone={zone.score:g}")
    check("subgrade keeps top tier", corners is not None and corners.subgrade == 10.0,
          f"corners={corners.subgrade if corners else '-'} (9.85 rounds to 10 by policy)")
    check("single signal in report", len(rep.signals) == 1, f"{len(rep.signals)} signals")
    notes = " ".join(z["text"] for z in _zone_analysis(rep))
    check("report states the tolerance", "容差之内" in notes, "10 未被读成无瑕")
    check("report credits model", rep.model_version == "fake/fake-vlm-1", rep.model_version)
    check("report discloses upload",
          any("已上传" in w for w in rep.warnings),
          next((w[:40] for w in rep.warnings if "已上传" in w), "missing"))

    # -- a clean card must not read as "never inspected" ------------------ #
    quiet = analyze(
        card.image,
        None,
        card_name="clean card",
        vision=True,
        vision_settings=settings,
        provider=QuietProvider(),
    )
    ctx = report_to_context(quiet.report)
    check("clean run is still assessed",
          all(d["assessed"] for d in ctx["dims"]),
          str([(d["label"], d["assessed"]) for d in ctx["dims"]]))
    cell_values = [s["front"] for s in ctx["scan"] if s["title"] != "居中"]
    check("clean run says 未检出 not 未评估",
          cell_values == ["未检出"] * 3, str(cell_values))

    # -- offline (no provider) must say 未评估 ---------------------------- #
    offline = analyze(card.image, None, card_name="offline", vision=False)
    octx = report_to_context(offline.report)
    check("offline marks dimensions unscored",
          [d["assessed"] for d in octx["dims"]] == [True, False, False, False],
          str([(d["label"], d["assessed"]) for d in octx["dims"]]))

    print()
    print("=" * 66)
    print(f"{PASS}/{PASS + FAIL} checks passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
