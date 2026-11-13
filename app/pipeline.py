"""Pipeline: images + signals -> a render-ready report plus its evidence images.

The pipeline is deliberately split so that the expensive, non-deterministic
part (vision) can be swapped or skipped without touching the deterministic
parts (geometry, scoring, rendering). At M1 the signal list comes from a JSON
fixture; at M2 the same slot is filled by per-zone VLM calls. Nothing else
changes, which is why the report template can be finalised before any model is
installed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import cv2
import numpy as np

from src import annotate, auto_align, detect_border_lines, measure_from_borders
from src.centering_calculator import BorderLines

from .defect_model import Category, DefectSignal, GradeReport, Severity, Side, SignalSource
from .scoring import DEFAULT_CONFIG, ScoringConfig, build_report
from .zone_splitter import ZoneCrop, split_both_sides

__all__ = [
    "AnalysisResult",
    "signal_from_dict",
    "load_signal_pack",
    "analyze",
    "annotate_centering",
    "vision_meta",
]


@dataclass(slots=True)
class AnalysisResult:
    """Everything the renderer and the API need, produced in one pass."""

    report: GradeReport
    front_aligned: np.ndarray
    back_aligned: np.ndarray | None = None
    borders_front: BorderLines | None = None
    borders_back: BorderLines | None = None
    crops: dict[str, ZoneCrop] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    evidence: dict[str, np.ndarray] = field(default_factory=dict)
    vision: Any | None = None


def signal_from_dict(raw: dict[str, Any]) -> DefectSignal:
    """Build a :class:`DefectSignal` from a loose dict (JSON, VLM output, form)."""
    return DefectSignal(
        severity=Severity(str(raw["severity"]).lower()),
        side=Side(str(raw.get("side", "front")).lower()),
        zone=str(raw["zone"]).lower(),
        category=Category(str(raw["category"]).lower()),
        defect=str(raw.get("defect", "unknown")),
        source=SignalSource(str(raw.get("source", "ai_flagged")).lower()),
        confidence=float(raw.get("confidence", 1.0)),
        note=str(raw.get("note", "")),
    )


def load_signal_pack(path: str | Path) -> dict[str, Any]:
    """Load a fixture: card name, centering metrics and a signal list."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data["signals"] = [signal_from_dict(s) for s in data.get("signals", [])]
    return data


def annotate_centering(image: np.ndarray, borders: BorderLines) -> np.ndarray:
    """Red = card edge, green = artwork edge — the visual proof users trust most."""
    out = annotate(
        image,
        borders,
        color_outer=(60, 60, 235),   # BGR red
        color_inner=(90, 200, 90),   # BGR green
        thickness=max(2, image.shape[1] // 450),
    )
    # Dim the card slightly so the guides read clearly on bright borders.
    return cv2.addWeighted(out, 1.0, np.zeros_like(out), 0.0, 0)


def _centering_metrics(
    front_report: Any,
    back_report: Any | None,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "lr_ratio": front_report.ratio("lr", 1),
        "tb_ratio": front_report.ratio("tb", 1),
        "left_pct": round(front_report.left_pct, 2),
        "right_pct": round(front_report.right_pct, 2),
        "top_pct": round(front_report.top_pct, 2),
        "bottom_pct": round(front_report.bottom_pct, 2),
        "lr_worst_pct": round(front_report.lr_worst_pct, 2),
        "tb_worst_pct": round(front_report.tb_worst_pct, 2),
        "confidence": round(front_report.confidence, 3),
    }
    if back_report is not None:
        metrics.update(
            {
                "back_lr_ratio": back_report.ratio("lr", 1),
                "back_tb_ratio": back_report.ratio("tb", 1),
                "back_left_pct": round(back_report.left_pct, 2),
                "back_right_pct": round(back_report.right_pct, 2),
                "back_top_pct": round(back_report.top_pct, 2),
                "back_bottom_pct": round(back_report.bottom_pct, 2),
                "back_lr_worst_pct": round(back_report.lr_worst_pct, 2),
                "back_tb_worst_pct": round(back_report.tb_worst_pct, 2),
            }
        )
    return metrics


def analyze(
    front: np.ndarray,
    back: np.ndarray | None = None,
    *,
    card_name: str = "Untitled card",
    signals: Sequence[DefectSignal] = (),
    centering_override: dict[str, Any] | None = None,
    config: ScoringConfig = DEFAULT_CONFIG,
    make_evidence: bool = True,
    vision: bool | None = None,
    vision_settings: Any | None = None,
    provider: Any | None = None,
) -> AnalysisResult:
    """Run geometry, then score whatever signals were supplied.

    ``centering_override`` lets a caller pass measured ratios instead of having
    the pipeline measure them — used by the fixture path so a report can be
    reproduced without the original photo.

    ``vision`` controls the defect-detection model: ``None`` follows the
    environment, ``False`` forces the offline path, ``True`` forces an attempt.
    Explicit ``signals`` win over vision — a fixture must stay reproducible
    without spending tokens.
    """
    notes: list[str] = []
    evidence: dict[str, np.ndarray] = {}

    # Geometry always runs: even when the caller supplies measured ratios, the
    # report still needs a rectified card and an annotated evidence image.
    # ``centering_override`` replaces only the *numbers*, never the processing.
    aligned = auto_align(front)
    front_aligned = aligned.warped
    notes.extend(aligned.notes)
    borders_front, conf, _ = detect_border_lines(front_aligned)
    front_rep = measure_from_borders(borders_front, confidence=conf)
    notes.extend(front_rep.warnings)
    if make_evidence:
        evidence["centering_front"] = annotate_centering(front_aligned, borders_front)

    back_aligned = None
    borders_back = None
    back_rep = None
    if back is not None and back.size:
        aligned_b = auto_align(back)
        back_aligned = aligned_b.warped
        borders_back, conf_b, _ = detect_border_lines(back_aligned)
        back_rep = measure_from_borders(borders_back, confidence=conf_b)
        if make_evidence:
            evidence["centering_back"] = annotate_centering(
                back_aligned, borders_back
            )

    if centering_override is not None:
        metrics = dict(centering_override)
    else:
        metrics = _centering_metrics(front_rep, back_rep)

    # ---- vision pass (optional, and never on top of explicit signals) ---- #
    settings = vision_settings
    if settings is None:
        try:
            from .config import settings_from_env

            settings = settings_from_env()
        except Exception:  # pragma: no cover - config must never break a report
            settings = None

    use_vision = settings is not None and settings.enabled if vision is None else bool(vision)
    if settings is not None and not settings.ready:
        use_vision = False
        if vision:
            notes.append("视觉判定已请求但未配置 API Key，四角/边缘/表面按未评估处理。")

    zone_size = getattr(settings, "zone_size", 704) if use_vision else 384
    crops: dict[str, ZoneCrop] = {}
    try:
        crops = split_both_sides(front_aligned, back_aligned, out_size=zone_size)
    except Exception as exc:  # pragma: no cover - defensive
        notes.append(f"zone split skipped: {exc}")

    vision_result = None
    all_signals: list[DefectSignal] = list(signals)
    model_version = "local-0.1"

    if use_vision and not signals and crops:
        from .vision import build_provider, build_requests, findings_to_signals, inspect_zones

        vision_result, vision_signals, vision_notes = _run_vision(
            crops,
            settings,
            provider=provider,
            build_provider=build_provider,
            build_requests=build_requests,
            inspect_zones=inspect_zones,
        )
        all_signals.extend(findings_to_signals(vision_signals))
        notes.extend(vision_notes)
        if vision_result is not None and vision_result.provider not in {"", "none"}:
            model_version = f"{vision_result.provider}/{vision_result.model or settings.model}"

    # Which dimensions can honestly claim to have been looked at. Centering is
    # always measured; the other three only when a defect pass actually ran,
    # even if that pass concluded "nothing here".
    assessed = ["centering"]
    if all_signals or (vision_result is not None and vision_result.zones_inspected):
        assessed += ["corners", "edges", "surface"]

    report = build_report(
        card_name=card_name,
        signals=all_signals,
        centering_metrics=metrics or None,
        config=config,
        warnings=notes,
        model_version=model_version,
        assessed_dimensions=assessed,
    )

    return AnalysisResult(
        report=report,
        front_aligned=front_aligned,
        back_aligned=back_aligned,
        borders_front=borders_front,
        borders_back=borders_back,
        crops=crops,
        notes=notes,
        evidence=evidence,
        vision=vision_result,
    )


def _run_vision(
    crops: dict[str, ZoneCrop],
    settings: Any,
    *,
    provider: Any,
    build_provider: Any,
    build_requests: Any,
    inspect_zones: Any,
) -> tuple[Any, list[Any], list[str]]:
    """Isolated so a vision failure degrades to notes instead of a 500."""
    notes: list[str] = []
    try:
        prov = provider or build_provider(settings)
    except Exception as exc:  # noqa: BLE001
        return None, [], [f"视觉判定未启动：{exc}"]

    if prov is None:
        return None, [], ["视觉判定未启动：提供方不可用，四角/边缘/表面按未评估处理。"]

    reqs = build_requests(
        crops,
        max_zones=getattr(settings, "max_zones", 9),
        include_back=getattr(settings, "include_back", False),
    )
    result = inspect_zones(
        reqs,
        prov,
        concurrency=getattr(settings, "concurrency", 4),
        cache_dir=getattr(settings, "cache_dir", None),
    )

    if result.findings or result.zones_inspected:
        notes.append(
            f"视觉判定由 {result.provider}/{result.model} 完成："
            f"检查 {len(result.zones_inspected)} 个分区，得到 {len(result.findings)} 条信号；"
            f"{result.usage.summary()}，约 ${result.usage.cost_usd():.4f}。"
        )
        notes.append(
            f"本报告的四角、边缘、表面由云端模型判定，"
            f"{len(result.zones_inspected)} 张分区裁图已上传至 {getattr(settings, 'base_url', 'API')}。"
            "居中比例仍为本地几何测量，未经上传。"
        )
    for err in result.errors[:4]:
        notes.append(f"分区判定失败（按未评估处理）：{err}")
    return result, result.findings, notes



def vision_meta(result: AnalysisResult) -> dict[str, Any] | None:
    """Serialisable summary of the vision pass, including what it cost."""
    v = result.vision
    if v is None:
        return None
    return {
        "provider": v.provider,
        "model": v.model,
        "zones_inspected": len(v.zones_inspected),
        "findings": len(v.findings),
        "usage": {
            "requests": v.usage.requests,
            "images": v.usage.images,
            "prompt_tokens": v.usage.prompt_tokens,
            "completion_tokens": v.usage.completion_tokens,
            "cache_hits": v.usage.cache_hits,
            "retries": v.usage.retries,
        },
        "cost_usd": round(v.usage.cost_usd(), 6),
        "errors": v.errors[:5],
    }


def analyze_from_paths(
    front_path: str | Path,
    back_path: str | Path | None = None,
    **kwargs: Any,
) -> AnalysisResult:
    front = cv2.imread(str(front_path), cv2.IMREAD_COLOR)
    if front is None:
        raise FileNotFoundError(f"cannot read image: {front_path}")
    back = None
    if back_path:
        back = cv2.imread(str(back_path), cv2.IMREAD_COLOR)
    return analyze(front, back, **kwargs)
