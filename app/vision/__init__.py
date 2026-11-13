"""Pluggable defect-detection layer.

``src/`` measures geometry; ``app/scoring.py`` turns signals into grades; this
package is the only part that talks to a model. Keeping it behind one function —
:func:`findings_to_signals` — is what allows the whole pipeline to run with
vision switched off and behave as if it had never existed.
"""

from __future__ import annotations

from typing import Iterable

from ..config import VisionSettings, settings_from_env
from ..defect_model import Category, DefectSignal, Severity, Side, SignalSource
from .prompts import PROMPT_VERSION, ZONE_ZH
from .runner import VisionProvider, build_requests, inspect_zones
from .types import VisionResult, VisionUsage, ZoneFinding, ZoneRequest

__all__ = [
    "VisionProvider",
    "VisionResult",
    "VisionSettings",
    "ZoneFinding",
    "ZoneRequest",
    "build_provider",
    "build_requests",
    "findings_to_signals",
    "get_settings",
    "inspect_zones",
    "PROMPT_VERSION",
    "ZONE_ZH",
    "category_for_zone",
]

_CORNER_ZONES = {"top_left", "top_right", "bottom_left", "bottom_right"}
_EDGE_ZONES = {"top", "bottom", "left", "right"}


def category_for_zone(zone: str) -> Category:
    if zone in _CORNER_ZONES:
        return Category.CORNER
    if zone in _EDGE_ZONES:
        return Category.EDGE
    return Category.SURFACE


def get_settings() -> VisionSettings:
    """Read settings fresh each call so env changes need no restart."""
    return settings_from_env()


def build_provider(settings: VisionSettings | None = None) -> VisionProvider | None:
    """Return a provider, or ``None`` when vision is off/misconfigured.

    Misconfiguration degrades to ``None`` on purpose: a missing key should cost
    the user the four corners dimension, not the entire report.
    """
    st = settings or get_settings()
    if not st.ready:
        return None
    if st.provider == "deepseek":
        from .deepseek import DeepSeekVisionProvider

        return DeepSeekVisionProvider(
            st.api_key,
            model=st.model,
            base_url=st.base_url,
            detail=st.detail,
            timeout_s=st.timeout_s,
            max_retries=st.max_retries,
        )
    raise ValueError(f"unknown vision provider: {st.provider!r}")


def findings_to_signals(findings: Iterable[ZoneFinding]) -> list[DefectSignal]:
    """Map model output onto the signal model, with the model's confidence kept.

    The confidence is preserved rather than rounded away: the report shows it,
    and a downstream rule may choose to ignore findings below a threshold.
    """
    signals: list[DefectSignal] = []
    for f in findings:
        try:
            severity = Severity(f.severity)
        except ValueError:
            severity = Severity.MINOR
        signals.append(
            DefectSignal(
                severity=severity,
                side=Side(f.side) if f.side in {s.value for s in Side} else Side.FRONT,
                zone=f.zone,
                category=category_for_zone(f.zone),
                defect=f.defect,
                source=SignalSource.AI_FLAGGED,
                confidence=f.confidence,
                note=f.evidence,
            )
        )
    return signals
