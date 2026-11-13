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
) -> AnalysisResult:
    """Run geometry, then score whatever signals were supplied.

    ``centering_override`` lets a caller pass measured ratios instead of having
    the pipeline measure them — used by the fixture path so a report can be
    reproduced without the original photo.
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

    report = build_report(
        card_name=card_name,
        signals=signals,
        centering_metrics=metrics or None,
        config=config,
        warnings=notes,
    )

    crops: dict[str, ZoneCrop] = {}
    try:
        crops = split_both_sides(
            front_aligned,
            back_aligned if back_aligned is not None else None,
        )
    except Exception as exc:  # pragma: no cover - defensive
        notes.append(f"zone split skipped: {exc}")

    return AnalysisResult(
        report=report,
        front_aligned=front_aligned,
        back_aligned=back_aligned,
        borders_front=borders_front,
        borders_back=borders_back,
        crops=crops,
        notes=notes,
        evidence=evidence,
    )


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
