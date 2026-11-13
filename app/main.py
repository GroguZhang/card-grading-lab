"""FastAPI surface for the local grading pipeline.

Routes are intentionally thin: every one of them delegates to ``pipeline`` or
``report_render``. If a route grows business logic, that logic belongs in a
module instead, because the CLI must be able to produce the same result.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import ROOT
from .db import Database, default_db_path
from .pipeline import AnalysisResult, analyze, load_signal_pack, vision_meta
from .report_render import ReportRenderer, _data_uri

app = FastAPI(title="Card Grading Engine", version="0.1.0")

DATA = ROOT / "data"
UPLOADS = DATA / "uploads"
REPORTS = DATA / "reports"
for d in (UPLOADS, REPORTS):
    d.mkdir(parents=True, exist_ok=True)

db = Database(default_db_path(ROOT))
renderer = ReportRenderer()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _read_upload(file: UploadFile | None) -> np.ndarray | None:
    if file is None:
        return None
    raw = file.file.read()
    if not raw:
        return None
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, f"cannot decode image: {file.filename}")
    return img


def _parse_signals(raw: str | None):
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"signals_json is not valid JSON: {exc}") from exc
    from .pipeline import signal_from_dict

    if not isinstance(items, list):
        raise HTTPException(400, "signals_json must be a JSON array")
    return [signal_from_dict(x) for x in items]


def _persist(result: AnalysisResult) -> tuple[str, dict[str, str]]:
    rid = result.report.report_id
    folder = UPLOADS / rid
    folder.mkdir(parents=True, exist_ok=True)

    images: dict[str, str] = {}
    front_path = folder / "front.jpg"
    cv2.imwrite(str(front_path), result.front_aligned)
    images["card"] = _data_uri(result.front_aligned, 90)

    back_path = None
    if result.back_aligned is not None:
        back_path = folder / "back.jpg"
        cv2.imwrite(str(back_path), result.back_aligned)

    ev_path = None
    if "centering_front" in result.evidence:
        ev_path = folder / "centering_front.jpg"
        cv2.imwrite(str(ev_path), result.evidence["centering_front"])
        images["centering_front"] = _data_uri(result.evidence["centering_front"], 90)

    # Store the vision accounting alongside the report so /api/report/{id} can
    # answer "which model judged this, and what did it cost" after the fact.
    payload = result.report.to_dict()
    meta = vision_meta(result)
    if meta is not None:
        payload["vision"] = meta

    db.save_report(
        payload,
        front_image=front_path.relative_to(ROOT).as_posix(),
        back_image=back_path.relative_to(ROOT).as_posix() if back_path else None,
        evidence_image=ev_path.relative_to(ROOT).as_posix() if ev_path else None,
    )
    return rid, images


def _build_and_store(
    front: np.ndarray,
    back: np.ndarray | None,
    card_name: str,
    signals,
    centering_override: dict[str, Any] | None = None,
    use_vision: bool | None = None,
) -> tuple[str, dict[str, str], AnalysisResult]:
    result = analyze(
        front,
        back,
        card_name=card_name,
        signals=signals,
        centering_override=centering_override,
        vision=use_vision,
    )
    rid, images = _persist(result)
    return rid, images, result


def _parse_switch(raw: str | None) -> bool | None:
    """Tri-state form field: absent = follow the environment."""
    if raw is None or raw.strip() == "":
        return None
    return raw.strip().lower() in {"1", "true", "on", "yes"}


# --------------------------------------------------------------------------- #
# pages
# --------------------------------------------------------------------------- #
_INDEX_CSS = """
body{background:#0b0d11;color:#e7ecf3;font-family:system-ui,"Microsoft YaHei",sans-serif;margin:0;padding:48px}
.wrap{max-width:780px;margin:0 auto}
h1{font-size:20px;margin-bottom:6px}
p{color:#8a95a5;font-size:13px;line-height:1.8}
.card{background:#12161d;border:1px solid #232a35;border-radius:12px;padding:20px;margin-top:22px}
a{color:#f5c451}
code{background:#1b212b;padding:2px 6px;border-radius:4px;font-size:12px}
form{display:flex;flex-direction:column;gap:10px;margin-top:14px}
input,select,button{font:inherit;padding:9px 12px;border-radius:8px;border:1px solid #232a35;background:#171c25;color:#e7ecf3}
button{background:#f5c451;color:#111;font-weight:600;cursor:pointer;border:none}
.state{font-size:12px;font-family:ui-monospace,Consolas,monospace}
.state.on{color:#4ade80}
.state.off{color:#8a95a5}
"""


def _index_page(vision_state: str, vision_cls: str) -> str:
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>Card Grading Engine</title>
<style>{_INDEX_CSS}</style></head><body><div class="wrap">
<h1>Card Grading Engine</h1>
<p>本地评分报告服务。默认<b>不上传任何图片</b>：居中由本地几何测量，四角/边缘/表面标记为「未评估」。
启用云端判定后，只有九张分区裁图会发送到 DeepSeek，原图与居中几何数据不出本机。</p>
<p class="state {vision_cls}">视觉判定：{vision_state}</p>
<div class="card">
  <b>上传评分</b>
  <form method="post" action="/api/grade" enctype="multipart/form-data">
    <input type="text" name="card_name" placeholder="卡名，如 Stephen Curry">
    <input type="file" name="front" accept="image/*" required>
    <input type="file" name="back" accept="image/*">
    <select name="use_vision">
      <option value="">视觉判定：跟随服务端配置</option>
      <option value="1">视觉判定：本次强制开启（上传 9 张分区图，约 $0.001）</option>
      <option value="0">视觉判定：本次强制关闭（纯离线）</option>
    </select>
    <button type="submit">生成报告</button>
  </form>
</div>
<div class="card">
  <b>演示报告（无需上传）</b>
  <p>用合成卡 + 样本缺陷集生成，用于验证模板与评分引擎。</p>
  <p><a href="/demo">打开演示报告 →</a></p>
</div>
<div class="card">
  <b>最近报告</b>
  <p><a href="/api/reports">GET /api/reports</a> · <a href="/docs">API 文档</a></p>
</div>
</div></body></html>
"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    from .vision import get_settings

    st = get_settings()
    return _index_page(st.describe(), "on" if st.ready else "off")


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
@app.post("/api/grade")
async def grade(
    front: UploadFile = File(...),
    back: UploadFile | None = File(None),
    card_name: str = Form("Untitled card"),
    signals_json: str | None = Form(None),
    use_vision: str | None = Form(None),
) -> JSONResponse:
    img_front = _read_upload(front)
    if img_front is None:
        raise HTTPException(400, "front image is required")
    img_back = _read_upload(back)
    signals = _parse_signals(signals_json)

    rid, _, result = _build_and_store(
        img_front, img_back, card_name, signals, use_vision=_parse_switch(use_vision)
    )
    return JSONResponse(
        {
            "report_id": rid,
            "report_url": f"/report/{rid}",
            "pdf_url": f"/report/{rid}.pdf",
            "json_url": f"/api/report/{rid}",
            "overall": result.report.overall,
            "tier": result.report.tier,
            "limiting_factor": (
                result.report.limiting_factor.value
                if result.report.limiting_factor
                else None
            ),
            "vision": vision_meta(result),
        }
    )


@app.get("/demo", response_class=HTMLResponse)
def demo() -> HTMLResponse:
    """Render a full report from the sample fixture and a synthetic card."""
    from tests.synthetic import make_card

    pack = load_signal_pack(ROOT / "fixtures" / "sample_signals.json")
    card = make_card(49, 45, noise=4, blur=1, seed=7)

    result = analyze(
        card.image,
        None,
        card_name=pack["card_name"],
        signals=pack["signals"],
        centering_override=pack["centering"],
    )
    # Override the id so repeated demo calls stay comparable.
    result.report.report_id = "DEMO0001"
    if "centering_front" not in result.evidence:
        from .pipeline import annotate_centering

        result.evidence["centering_front"] = annotate_centering(
            result.front_aligned, result.borders_front
        )
    images = {
        "card": _data_uri(result.front_aligned, 90),
        "centering_front": _data_uri(result.evidence["centering_front"], 90),
    }
    return HTMLResponse(renderer.render_html(result.report, images=images))


@app.get("/api/report/{report_id}")
def get_report(report_id: str) -> JSONResponse:
    row = db.get_report(report_id)
    if row is None:
        raise HTTPException(404, "report not found")
    payload = row["payload"]
    payload["_files"] = {
        "front_image": row["front_image"],
        "back_image": row["back_image"],
        "evidence_image": row["evidence_image"],
        "pdf_path": row["pdf_path"],
    }
    return JSONResponse(payload)


@app.get("/api/reports")
def list_reports(limit: int = 50) -> JSONResponse:
    return JSONResponse({"items": db.list_reports(limit)})


@app.get("/report/{report_id}.pdf")
def report_pdf(report_id: str) -> FileResponse:
    """Register before ``/report/{report_id}``: FastAPI matches in declaration
    order, and the HTML route would otherwise swallow ``ABC.pdf`` as an id."""
    row = db.get_report(report_id)
    if row is None:
        raise HTTPException(404, "report not found")

    cached = row.get("pdf_path")
    if cached:
        p = ROOT / cached
        if p.exists():
            return FileResponse(p, media_type="application/pdf", filename=f"{report_id}.pdf")

    html = report_html(report_id).body.decode("utf-8")
    out = REPORTS / f"{report_id}.pdf"
    ok, msg = renderer.render_pdf(html, out)
    if not ok:
        raise HTTPException(
            503,
            f"PDF 生成失败：{msg}。可先访问 /report/{report_id} 用浏览器打印为 PDF。",
        )
    db.set_pdf(report_id, out.relative_to(ROOT).as_posix())
    return FileResponse(out, media_type="application/pdf", filename=f"{report_id}.pdf")


@app.get("/report/{report_id}", response_class=HTMLResponse)
def report_html(report_id: str) -> HTMLResponse:
    row = db.get_report(report_id)
    if row is None:
        raise HTTPException(404, "report not found")
    from .defect_model import DefectSignal, DimensionScore, GradeReport, ZoneScore, Dimension, Severity, Side, Category, SignalSource

    payload = row["payload"]
    signals = [
        DefectSignal(
            severity=Severity(s["severity"]),
            side=Side(s["side"]),
            zone=s["zone"],
            category=Category(s["category"]),
            defect=s["defect"],
            source=SignalSource(s["source"]),
            confidence=float(s.get("confidence", 1.0)),
        )
        for s in payload.get("signals", [])
    ]
    dims = [
        DimensionScore(
            dimension=Dimension(d["dimension"]),
            front=float(d["front"]),
            back=float(d["back"]),
            subgrade=float(d["subgrade"]),
        )
        for d in payload.get("dimensions", [])
    ]
    zones = [
        ZoneScore(zone=z["zone"], side=Side(z["side"]), score=float(z["score"]))
        for z in payload.get("zones", [])
    ]
    report = GradeReport(
        report_id=payload["report_id"],
        card_name=payload["card_name"],
        overall=float(payload["overall"]),
        tier=payload["tier"],
        limiting_factor=(
            Dimension(payload["limiting_factor"])
            if payload.get("limiting_factor")
            else None
        ),
        dimensions=dims,
        zones=zones,
        signals=signals,
        predictions={k: float(v) for k, v in payload.get("predictions", {}).items()},
        centering=payload.get("centering"),
        warnings=payload.get("warnings", []),
        model_version=payload.get("model_version", "local-0.1"),
        threshold_version=payload.get("threshold_version", "2026.11"),
        assessed_dimensions=payload.get("assessed_dimensions", []),
    )

    images: dict[str, str] = {}
    front_rel = row["front_image"]
    if front_rel:
        p = ROOT / front_rel
        if p.exists():
            images["card"] = _data_uri(cv2.imread(str(p)), 90)
    ev_rel = row["evidence_image"]
    if ev_rel:
        p = ROOT / ev_rel
        if p.exists():
            images["centering_front"] = _data_uri(cv2.imread(str(p)), 90)

    return HTMLResponse(renderer.render_html(report, images=images))


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "reports": len(db.list_reports(1000))}
