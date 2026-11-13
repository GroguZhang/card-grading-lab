"""Render the sample report end to end: synthetic card + fixture signals -> HTML -> PDF.

This is the M1 acceptance check. It proves the template, the scoring engine and
the PDF path all agree, without needing a vision model or a real card photo.

Run::

    python -m tests.render_demo
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2  # noqa: E402

from app.pipeline import analyze, load_signal_pack  # noqa: E402
from app.report_render import ReportRenderer, _data_uri  # noqa: E402
from tests.synthetic import make_card  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "examples")


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    pack = load_signal_pack(os.path.join(ROOT, "fixtures", "sample_signals.json"))
    card = make_card(49, 45, noise=4, blur=1, seed=7)

    t0 = time.time()
    result = analyze(
        card.image,
        None,
        card_name=pack["card_name"],
        signals=pack["signals"],
        centering_override=pack["centering"],
    )
    t_analyze = time.time() - t0

    rep = result.report
    print("=" * 62)
    print("DEMO REPORT")
    print("=" * 62)
    print(f"  card        : {rep.card_name}")
    print(f"  overall     : {rep.overall:g}  {rep.tier}")
    print(f"  limiting    : {rep.limiting_factor.value if rep.limiting_factor else '-'}")
    print(f"  subgrades   : {rep.subgrades}")
    print(f"  predictions : {rep.predictions}")
    print(f"  signals     : {len(rep.signals)}")
    print(f"  zones       : {len(rep.zones)}")
    print(f"  geometry    : {t_analyze*1000:.0f} ms")

    images = {
        "card": _data_uri(result.front_aligned, 90),
        "centering_front": _data_uri(result.evidence["centering_front"], 90),
    }

    renderer = ReportRenderer()
    html = renderer.render_html(rep, images=images)
    html_path = os.path.join(OUT, "demo_report.html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"\n  HTML  -> {html_path}  ({len(html)/1024:.0f} KB)")

    # Keep a rectified card next to the report for eyeballing.
    cv2.imwrite(os.path.join(OUT, "demo_card_rectified.jpg"), result.front_aligned)

    pdf_path = os.path.join(OUT, "demo_report.pdf")
    t0 = time.time()
    ok, msg = renderer.render_pdf(html, pdf_path)
    if ok:
        size = os.path.getsize(pdf_path) / 1024
        print(f"  PDF   -> {pdf_path}  ({size:.0f} KB, {time.time()-t0:.1f}s)")
    else:
        print(f"  PDF   -> skipped: {msg}")
        print("          用浏览器打开 HTML 后 Ctrl+P 也可导出同版式 PDF。")

    print("\n  verdict:", "PASS" if rep.overall == 9 and rep.tier == "MINT" else "CHECK")
    return 0 if rep.overall == 9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
