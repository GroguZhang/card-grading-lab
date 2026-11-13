"""Probe the vision layer without a server and without spending money.

Usage::

    python -m tests.vision_probe                # offline: show what would be sent
    python -m tests.vision_probe --image card.jpg
    python -m tests.vision_probe --image card.jpg --call   # actually calls the API

``--call`` requires ``DEEPSEEK_API_KEY``. Without it the probe still runs and
prints the exact request footprint (crop sizes, jpeg bytes, token ceiling), which
is the part worth checking before you pay for anything.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2  # noqa: E402

from app.pipeline import analyze  # noqa: E402
from app.vision import build_requests, get_settings, inspect_zones  # noqa: E402
from app.vision.deepseek import encode_jpeg  # noqa: E402
from tests.synthetic import make_card  # noqa: E402


def _load(path: str | None):
    if path:
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            raise SystemExit(f"cannot read image: {path}")
        return img, Path(path).stem
    return make_card(49, 45, noise=4, blur=1, seed=7).image, "synthetic"


def main() -> int:
    ap = argparse.ArgumentParser(description="DeepSeek 视觉层探测")
    ap.add_argument("--image", help="卡片照片路径；省略则用合成卡")
    ap.add_argument("--call", action="store_true", help="真的调用 API（需要 DEEPSEEK_API_KEY）")
    ap.add_argument("--zones", type=int, default=9, help="送检分区数")
    args = ap.parse_args()

    settings = get_settings()
    print("=" * 68)
    print("视觉配置")
    print("=" * 68)
    print(f"  provider     : {settings.provider}  ({settings.describe()})")
    print(f"  model        : {settings.model}")
    print(f"  detail       : {settings.detail}")
    print(f"  zone_size    : {settings.zone_size} px")
    print(f"  concurrency  : {settings.concurrency}")
    print(f"  key present  : {'yes' if settings.api_key else 'no'}")

    img, name = _load(args.image)
    print(f"\n  输入图像     : {name}  {img.shape[1]}x{img.shape[0]}")

    result = analyze(img, None, card_name=name, vision=False)
    print(f"  校正后卡片   : {result.front_aligned.shape[1]}x{result.front_aligned.shape[0]}")
    print(f"  居中测量     : {result.report.centering.get('lr_ratio')} / "
          f"{result.report.centering.get('tb_ratio')}")

    # Split at the configured size, not the 384 px evidence size: the point of
    # this probe is the exact footprint that would be uploaded.
    from app.zone_splitter import split_both_sides

    crops = split_both_sides(result.front_aligned, None, out_size=settings.zone_size)
    reqs = build_requests(crops, max_zones=args.zones)
    print(f"\n  分区裁图     : {len(reqs)} 张")
    total_kb = 0
    for r in reqs:
        b64 = encode_jpeg(r.image)
        kb = len(b64) * 3 / 4 / 1024
        total_kb += kb
        print(f"    {r.key:<18} {r.image.shape[1]}x{r.image.shape[0]}  {kb:6.1f} KB")
    print(f"  合计         : {total_kb:.1f} KB（上限 32 MiB/张，48 MiB/请求）")
    print(f"  预计图片 token: ≤ {len(reqs) * 1024}（官方上限 1024/张）")

    if not args.call:
        print("\n  未加 --call，到此为止，没有发出任何网络请求。")
        return 0

    if not settings.api_key:
        print("\n  ⚠ 没有 DEEPSEEK_API_KEY，跳过实际调用。")
        print("    PowerShell:  $env:DEEPSEEK_API_KEY='sk-...'")
        print("    bash:        export DEEPSEEK_API_KEY='sk-...'")
        return 2

    from app.vision import build_provider

    settings = get_settings()
    provider = build_provider(settings)
    assert provider is not None
    print(f"\n  调用 {provider.name}/{provider.model} …")
    out = inspect_zones(
        reqs, provider, concurrency=settings.concurrency, cache_dir=settings.cache_dir
    )
    print(f"  检查分区     : {len(out.zones_inspected)}")
    print(f"  信号         : {len(out.findings)}")
    for f in out.findings:
        print(f"    {f.side}:{f.zone:<12} {f.defect:<12} {f.severity:<8} "
              f"conf={f.confidence:.2f}  {f.evidence}")
    print(f"  用量         : {out.usage.summary()}")
    print(f"  估算成本     : ${out.usage.cost_usd():.5f}")
    for e in out.errors:
        print(f"  ✗ {e}")
    return 0 if not out.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
