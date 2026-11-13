"""Render a :class:`GradeReport` into HTML, then into PDF.

The report is a template, not a generated essay. Every sentence in it is a
function of the numbers above it — which is also true of the commercial report
this project was modelled on, and is the reason a report can be audited.

PDF export goes through a real browser print, the same path a vendor takes when
its "Download PDF" button hands the job to the OS. That means the layout is
CSS, and fixing a layout bug never requires touching PDF drawing code.
"""

from __future__ import annotations

import base64
import datetime as _dt
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .defect_model import (
    ZONE_LABELS,
    Category,
    DefectSignal,
    Dimension,
    GradeReport,
    Severity,
    Side,
)

__all__ = ["ReportRenderer", "report_to_context"]

_HERE = Path(__file__).resolve().parent
_TEMPLATES = _HERE / "templates"
_STATIC = _HERE / "static"

_DEFECT_ZH = {
    "corner_touch": "角部触碰",
    "whitening": "露白",
    "chipping": "分层",
    "fraying": "毛边",
    "nick": "缺口",
    "scratch": "划痕",
    "print_line": "印刷线",
    "dimple": "凹点",
    "stain": "污渍",
    "crease": "折痕",
}

_CATEGORY_ZH = {"corner": "角部", "edge": "边缘", "surface": "表面", "centering": "居中"}

_DIM_ZH = {
    "centering": "居中 CENTERING",
    "corners": "四角 CORNERS",
    "edges": "边缘 EDGES",
    "surface": "表面 SURFACE",
}

_SIDE_ZH = {"front": "正面", "back": "背面"}

_SEV_ZH = {"minor": "轻微", "major": "明显", "critical": "严重"}

_DIM_CAT = {
    "centering": Category.CENTERING,
    "corners": Category.CORNER,
    "edges": Category.EDGE,
    "surface": Category.SURFACE,
}

#: Diagnostics are produced by the engine in English; the report is Chinese.
#: Anything unmatched is passed through rather than hidden.
_WARN_ZH = (
    (
        r"specular highlights cover ([\d.]+)% of the frame",
        "强反光覆盖画面 {0}%，表面判定可能受影响，建议补拍无闪光照片",
    ),
    (
        r"low edge confidence \(([\d.]+)\); verify the guide lines",
        "边缘检测置信度偏低（{0}），建议人工核对测量线",
    ),
    (
        r"card outline aspect is ([\d.]+) vs expected ([\d.]+) \(([\d.]+)% off\)",
        "卡片轮廓长宽比 {0} 与标准 {1} 相差 {2}%，可能存在残余透视或裁切，建议重新校正",
    ),
    (
        r"thinnest border is ([\d.]+) mm",
        "最窄边框仅 {0} mm，低于分辨率下限，读数误差可能达到 ±1 个百分点",
    ),
    (
        r"borderless / full-bleed artwork",
        "满版无边框画面，至少一个方向没有可测量的边框，居中无法判定",
    ),
)


def _warning_zh(text: str) -> str:
    for pattern, template in _WARN_ZH:
        m = re.search(pattern, text)
        if m:
            return template.format(*m.groups())
    return text


def _data_uri(image: np.ndarray, quality: int = 88) -> str:
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


def _grade_class(value: float) -> str:
    if value >= 9.5:
        return ""
    if value >= 8.5:
        return "low"
    return "bad"


def _zone_class(value: float) -> str:
    if value >= 9.95:
        return "hi"
    if value >= 9.0:
        return "mid"
    return "lo"


def _ratio_class(pct: float, limit: float) -> str:
    """Green when comfortably inside the reference tolerance, amber at the
    edge of it, red once outside."""
    if pct <= 52:
        return "ok"
    if pct <= limit:
        return "warn"
    return "bad"


def _cen_verdict(lr_worst: float, tb_worst: float) -> tuple[str, str]:
    """One plain-language line for a reader who does not think in percentages.

    The bands are the ones the web demo uses, anchored on the fact that a
    perfectly centred card still reads slightly worse than 50 % after
    quantisation — that is why the first band ends at 50.3, not 50.
    """
    w = max(lr_worst, tb_worst)
    if w <= 50.3:
        return f"完美居中（最差方向 {w:.1f}%）", "ok"
    if w <= 52.5:
        return f"接近完美居中（最差方向 {w:.1f}%）", "ok"
    if w <= 55:
        return f"达到多数机构的 10 档容差（最差方向 {w:.1f}%）", "ok"
    if w <= 60:
        return f"超出 10 档，落在 9 档区间（最差方向 {w:.1f}%）", "warn"
    return f"明显偏边（最差方向 {w:.1f}%）", "bad"


def _format_signals(signals: list[DefectSignal]) -> list[dict[str, Any]]:
    rows = []
    order = {Category.CORNER: 0, Category.EDGE: 1, Category.SURFACE: 2}
    for s in sorted(signals, key=lambda x: (order.get(x.category, 9), x.side.value, x.zone)):
        rows.append(
            {
                "severity": _SEV_ZH.get(s.severity.value, s.severity.value),
                "severity_cls": s.severity.value if s.severity is not Severity.MINOR else "",
                "side_label": _SIDE_ZH.get(s.side.value, s.side.value),
                "zone_label": ZONE_LABELS.get(s.zone, s.zone),
                "category_text": _CATEGORY_ZH.get(s.category.value, s.category.value),
                "defect_text": _DEFECT_ZH.get(s.defect, s.defect.replace("_", " ")),
                "source_text": {
                    "ai_flagged": "AI 判定",
                    "cv_measured": "CV 测量",
                    "manual": "人工",
                }.get(s.source.value, s.source.value),
                "src_cls": "cv" if s.source.value == "cv_measured" else "",
            }
        )
    return rows


def _count(signals: list[DefectSignal], category: Category, side: Side | None = None) -> int:
    return sum(
        1
        for s in signals
        if s.category is category and (side is None or s.side is side)
    )


def _zone_analysis(report: GradeReport, has_vision: bool = True) -> list[dict[str, str]]:
    """One short paragraph per dimension, assembled from the same data.

    Nothing here is a fixed sentence: every claim is a function of the signals
    and subgrades, so a report can never say "all dimensions are excellent"
    next to a 9.0.
    """
    out: list[dict[str, str]] = []

    cen = report.dimension("centering")
    if cen is not None and report.centering:
        c = report.centering
        bits = [f"正视 {c.get('lr_ratio', '—')}、{c.get('tb_ratio', '—')}"]
        if c.get("back_lr_ratio"):
            bits.append(f"背面 {c['back_lr_ratio']}、{c['back_tb_ratio']}")
        tail = (
            "两组比例均落在对应机构顶级档的容差之内。"
            if cen.subgrade >= 9.95
            else "已超出顶级档容差，需按机构分档重新评估。"
        )
        out.append(
            {
                "label": _DIM_ZH["centering"],
                "grade": f"{cen.subgrade:g}",
                "text": f"居中子分 <b>{cen.subgrade:g}</b>。" + "；".join(bits) + "。" + tail,
            }
        )

    corners = report.dimension("corners")
    if corners is not None:
        bad = [s for s in report.signals if s.category is Category.CORNER]
        if bad:
            zones = sorted({(s.side.value, s.zone) for s in bad})
            desc = "、".join(f"{_SIDE_ZH[sd]}{ZONE_LABELS[z]}" for sd, z in zones)
            kinds = "、".join(sorted({_DEFECT_ZH.get(s.defect, s.defect) for s in bad}))
            text = (
                f"角部子分 <b>{corners.subgrade:g}</b>。{desc} 共 {len(zones)} 个角区出现{kinds}，"
                f"正面 {_count(report.signals, Category.CORNER, Side.FRONT)} 条、"
                f"背面 {_count(report.signals, Category.CORNER, Side.BACK)} 条。"
                "四角是最容易被放大的维度：单区扣分不重，但多数角区同时受损会叠加"
                "一致性惩罚，把子分再压低一档。"
            )
        else:
            text = f"角部子分 <b>{corners.subgrade:g}</b>。" + (
                "未检测到角部缺陷。" if has_vision else "本次未启用视觉判定，角部未做检查。"
            )
        out.append({"label": _DIM_ZH["corners"], "grade": f"{corners.subgrade:g}", "text": text})

    edges = report.dimension("edges")
    if edges is not None:
        n = _count(report.signals, Category.EDGE)
        text = f"边缘子分 <b>{edges.subgrade:g}</b>。" + (
            "未检测到毛边、缺口或分层。"
            if n == 0 and has_vision
            else ("检出 {0} 处边缘缺陷。".format(n) if n else "本次未启用视觉判定，边缘未做检查。")
        )
        out.append({"label": _DIM_ZH["edges"], "grade": f"{edges.subgrade:g}", "text": text})

    surf = report.dimension("surface")
    if surf is not None:
        bad = [s for s in report.signals if s.category is Category.SURFACE]
        if bad:
            kinds = "、".join(sorted({_DEFECT_ZH.get(s.defect, s.defect) for s in bad}))
            text = (
                f"表面子分 <b>{surf.subgrade:g}</b>。检出 {len(bad)} 处{kinds}，"
                "程度未到影响子分的量级。"
            )
        else:
            text = f"表面子分 <b>{surf.subgrade:g}</b>。" + (
                "未检测到表面缺陷。" if has_vision else "本次未启用视觉判定，表面未做检查。"
            )
        out.append({"label": _DIM_ZH["surface"], "grade": f"{surf.subgrade:g}", "text": text})

    return out


def _story_zh(report: GradeReport, has_vision: bool = True) -> str:
    head = f"本卡总评为 <b>{report.overall:g}</b>（{report.tier}）。"
    lf = report.limiting_factor
    if lf is None:
        return head + "四个维度都没有构成瓶颈，未触发降档。"

    dim = report.dimension(lf.value)
    cat = _DIM_CAT[lf.value]
    front_n = _count(report.signals, cat, Side.FRONT)
    back_n = _count(report.signals, cat, Side.BACK)

    strong = [
        _DIM_ZH[d.dimension.value].split(" ")[0]
        for d in report.dimensions
        if d.dimension is not lf
        and d.subgrade >= 9.95
        and (d.dimension is Dimension.CENTERING or has_vision)
    ]

    clauses: list[str] = []
    if strong:
        clauses.append("、".join(strong) + "达到顶级档水准")
    dist = f"正面 {front_n} 条" + (f"、背面 {back_n} 条" if back_n else "")
    clauses.append(
        f"但{_DIM_ZH[lf.value]}子分只有 <b>{dim.subgrade:g}</b>（{dist}原始信号）"
        if dim
        else f"但{_DIM_ZH[lf.value]}构成瓶颈"
    )
    return head + "，".join(clauses) + f"，因此总评被<b>保守地</b>封顶在 {report.overall:g}。"


def report_to_context(
    report: GradeReport,
    *,
    images: dict[str, str] | None = None,
    css: str = "",
) -> dict[str, Any]:
    """Flatten the report into exactly what the template needs."""
    images = images or {}
    has_vision = any(s.source.value != "manual" for s in report.signals)

    dims = []
    for d in report.dimensions:
        # Centering always has evidence (geometry). The other three only have
        # evidence when a signal was reported: with zero signals and no vision
        # pass, a 10 is a default, not a finding — so it is shown as unscored.
        assessed = d.dimension is Dimension.CENTERING or has_vision
        dims.append(
            {
                "label": _DIM_ZH.get(d.dimension.value, d.dimension.label),
                "subgrade": d.subgrade,
                "pct": max(4.0, min(100.0, d.subgrade * 10)) if assessed else 0.0,
                "cls": _grade_class(d.subgrade) if assessed else "none",
                "assessed": assessed,
            }
        )

    preds = [
        {"company": k, "value": v, "lead": False}
        for k, v in sorted(report.predictions.items())
    ]

    # scan summary: "4 MINOR" means four affected zones, not four signals.
    def affected(dim: Dimension, side: Side) -> int:
        cats = {
            Dimension.CORNERS: Category.CORNER,
            Dimension.EDGES: Category.EDGE,
            Dimension.SURFACE: Category.SURFACE,
        }[dim]
        return len(
            {
                s.zone
                for s in report.signals
                if s.category is cats and s.side is side
            }
        )

    def cell_text(dim: Dimension, side: Side) -> tuple[str, str]:
        n = affected(dim, side)
        if n:
            return f"{n} 处轻微", "warn"
        # Zero signals only means "clean" if something actually looked.
        return ("未检出", "ok") if has_vision else ("未评估", "na")

    cf, cfc = cell_text(Dimension.CORNERS, Side.FRONT)
    cb, cbc = cell_text(Dimension.CORNERS, Side.BACK)
    ef, efc = cell_text(Dimension.EDGES, Side.FRONT)
    eb, ebc = cell_text(Dimension.EDGES, Side.BACK)
    sf, sfc = cell_text(Dimension.SURFACE, Side.FRONT)
    sb, sbc = cell_text(Dimension.SURFACE, Side.BACK)

    c = report.centering or {}
    scan = [
        {"title": "四角", "front": cf, "back": cb, "front_cls": cfc, "back_cls": cbc},
        {"title": "边缘", "front": ef, "back": eb, "front_cls": efc, "back_cls": ebc},
        {
            "title": "居中",
            "front": f"{c.get('lr_ratio', '—')} {c.get('tb_ratio', '—')}",
            "back": f"{c.get('back_lr_ratio', '—')} {c.get('back_tb_ratio', '—')}",
            "front_cls": "ok",
            "back_cls": "ok",
        },
        {"title": "表面", "front": sf, "back": sb, "front_cls": sfc, "back_cls": sbc},
    ]

    front_zones = {z.zone: z.score for z in report.zones if z.side is Side.FRONT}
    grid_order = [
        "top_left", "top", "top_right",
        "left", "center", "right",
        "bottom_left", "bottom", "bottom_right",
    ]
    zone_grid = []
    for z in grid_order:
        if z == "center" or z not in front_zones:
            zone_grid.append({"text": "", "cls": "", "empty": True})
            continue
        score = front_zones[z]
        zone_grid.append(
            {
                "text": f"{score:g}",
                "cls": _zone_class(score),
                "empty": False,
            }
        )

    scored = [(z, s) for z, s in front_zones.items() if z != "center"]
    lowest = min(scored, key=lambda t: t[1]) if scored else None

    cen_block = None
    if c:
        verdict, verdict_cls = _cen_verdict(
            float(c.get("lr_worst_pct", 50)), float(c.get("tb_worst_pct", 50))
        )
        cen_block = {
            "lr_ratio": c.get("lr_ratio", "—"),
            "tb_ratio": c.get("tb_ratio", "—"),
            "back_lr_ratio": c.get("back_lr_ratio"),
            "back_tb_ratio": c.get("back_tb_ratio"),
            "lr_cls": _ratio_class(float(c.get("lr_worst_pct", 50)), 55),
            "tb_cls": _ratio_class(float(c.get("tb_worst_pct", 50)), 55),
            "back_lr_cls": _ratio_class(float(c.get("back_lr_worst_pct", 50)), 75),
            "back_tb_cls": _ratio_class(float(c.get("back_tb_worst_pct", 50)), 75),
            "verdict": verdict,
            "verdict_cls": verdict_cls,
        }

    return {
        "css": css,
        "report_id": report.report_id,
        "card_name": report.card_name,
        "overall": report.overall,
        "tier": report.tier,
        "dims": dims,
        "preds": preds,
        "limiting": _DIM_ZH[report.limiting_factor.value] if report.limiting_factor else None,
        "scan": scan,
        "centering": cen_block,
        "zone_grid": zone_grid,
        "lowest_zone": (
            {"label": ZONE_LABELS.get(lowest[0], lowest[0]), "score": f"{lowest[1]:g}"}
            if lowest
            else None
        ),
        "signals": _format_signals(report.signals),
        "story": _story_zh(report, has_vision),
        "zone_analysis": _zone_analysis(report, has_vision),
        "images": images,
        "warnings": [_warning_zh(w) for w in report.warnings],
        "model_version": report.model_version,
        "threshold_version": report.threshold_version,
        "generated_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "has_vision": has_vision,
        "unassessed": [d["label"] for d in dims if not d["assessed"]],
    }


class ReportRenderer:
    """Render HTML, and optionally PDF, from a report."""

    #: Tried in order by :meth:`render_pdf`. The first one that works wins.
    #: Playwright-first with a locally installed Chrome/Edge means a fresh
    #: clone prints PDFs without a 150 MB browser download.
    LAUNCH_MODES = (
        {"channel": "chrome"},
        {"channel": "msedge"},
        {"channel": "chromium"},
        {},
    )

    BROWSER_CANDIDATES = (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    )

    def __init__(self) -> None:
        self.env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES)),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.css = (_STATIC / "report.css").read_text(encoding="utf-8")

    def render_html(
        self,
        report: GradeReport,
        *,
        images: dict[str, str] | None = None,
    ) -> str:
        ctx = report_to_context(report, images=images, css=self.css)
        return self.env.get_template("report.html.j2").render(**ctx)

    # -- PDF ---------------------------------------------------------------

    def find_browser(self) -> str | None:
        """Locate a Chromium-family binary on this machine, if any."""
        env_hint = os.environ.get("REPORT_BROWSER")
        if env_hint and Path(env_hint).exists():
            return env_hint
        for name in ("chrome", "msedge", "chromium", "google-chrome"):
            found = shutil.which(name)
            if found:
                return found
        for path in self.BROWSER_CANDIDATES:
            if Path(path).exists():
                return path
        return None

    def render_pdf(self, html: str, out_path: str | Path) -> tuple[bool, str]:
        """Print the report to PDF. Returns ``(ok, message)``.

        Two engines, tried in order:

        1. Playwright's bundled Chromium — pixel-identical across machines.
        2. A locally installed Chrome/Edge in headless mode — no 150 MB
           download, and on this project's target machine it is what makes
           PDF export work at all.

        A failure is reported verbatim, never swallowed: a silently missing
        PDF is worse than an honest "print it yourself".
        """
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        ok, msg = self._pdf_via_playwright(html, out_path)
        if ok:
            return True, msg
        first = msg

        ok, msg = self._pdf_via_browser(html, out_path)
        if ok:
            return True, msg
        return False, f"{first}; fallback: {msg}"

    def _pdf_via_playwright(self, html: str, out_path: Path) -> tuple[bool, str]:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            return False, f"playwright unavailable ({type(exc).__name__})"

        last = ""
        with sync_playwright() as p:
            for kwargs in self.LAUNCH_MODES:
                try:
                    browser = p.chromium.launch(args=["--force-color-profile=srgb"], **kwargs)
                except Exception as exc:
                    last = str(exc).splitlines()[0][:110]
                    continue
                try:
                    page = browser.new_page(viewport={"width": 760, "height": 1200})
                    page.set_content(html, wait_until="load")
                    page.emulate_media(media="print")
                    # Paper size and margins live in `@page`, so every engine
                    # produces the same document.
                    page.pdf(
                        path=str(out_path),
                        print_background=True,
                        prefer_css_page_size=True,
                    )
                    return True, f"{out_path} (playwright/{kwargs.get('channel', 'bundled')})"
                except Exception as exc:
                    last = str(exc).splitlines()[0][:110]
                finally:
                    browser.close()
        return False, f"playwright launch failed ({last})"

    def _pdf_via_browser(self, html: str, out_path: Path) -> tuple[bool, str]:
        exe = self.find_browser()
        if not exe:
            return False, "no local Chrome/Edge found"

        # The browser needs a URL, not a string. Write beside the output so the
        # file survives long enough for the print job and is easy to inspect.
        tmp_dir = Path(tempfile.mkdtemp(prefix="report-print-", dir=str(out_path.parent)))
        src = tmp_dir / "report.html"
        src.write_text(html, encoding="utf-8")
        try:
            proc = subprocess.run(
                [
                    exe,
                    "--headless=new",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--no-pdf-header-footer",
                    "--force-color-profile=srgb",
                    "--virtual-time-budget=6000",
                    f"--print-to-pdf={out_path}",
                    src.as_uri(),
                ],
                capture_output=True,
                timeout=180,
            )
            if out_path.exists() and out_path.stat().st_size > 1024:
                return True, f"{out_path} ({Path(exe).stem})"
            err = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            return False, f"{Path(exe).stem} produced no pdf ({err[-1][:120] if err else 'silent'})"
        except subprocess.TimeoutExpired:
            return False, "browser print timed out after 180s"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

