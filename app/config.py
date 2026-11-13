"""Runtime configuration, read once from the environment.

Nothing here touches the network or the disk. The point is that the *same*
code path can run with vision switched off (offline, deterministic, free) or on
(cloud model, costs money, sends crops out), and the caller can tell which one
it got without guessing.

Secrets are read from the environment and never logged, echoed, serialised into
a report, or written to the database.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["VisionSettings", "settings_from_env"]


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class VisionSettings:
    """How the defect-detection model is reached.

    ``provider="none"`` is the default and means: no images leave the machine,
    corners/edges/surface are reported as *not assessed* rather than as perfect.
    """

    provider: str = "none"
    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"
    detail: str = "high"
    zone_size: int = 704          # px per side of a zone crop sent to the model
    max_zones: int = 9
    concurrency: int = 4
    timeout_s: float = 60.0
    max_retries: int = 3
    include_back: bool = False
    cache_dir: Path | None = None
    api_key: str = field(default="", repr=False)

    @property
    def enabled(self) -> bool:
        return self.provider not in {"", "none", "off", "disabled"}

    @property
    def ready(self) -> bool:
        """Enabled *and* actually able to run (a key exists, when required)."""
        if not self.enabled:
            return False
        if self.provider in {"deepseek"}:
            return bool(self.api_key)
        return True

    def describe(self) -> str:
        if not self.enabled:
            return "未启用（离线模式）"
        if not self.ready:
            return f"{self.provider}/{self.model}（缺少 API Key）"
        return f"{self.provider}/{self.model}"

    def public(self) -> dict[str, object]:
        """Safe to serialise into a report or an API response."""
        return {
            "provider": self.provider,
            "model": self.model,
            "detail": self.detail,
            "zone_size": self.zone_size,
            "enabled": self.enabled,
            "ready": self.ready,
        }


def settings_from_env() -> VisionSettings:
    """Build settings from ``CARD_VISION_*`` / ``DEEPSEEK_API_KEY``.

    Deliberately tolerant: a typo in an env var should degrade to "vision off",
    never crash a report that could still be produced offline.
    """
    cache = os.environ.get("CARD_VISION_CACHE_DIR", "data/cache/vision")
    return VisionSettings(
        provider=os.environ.get("CARD_VISION_PROVIDER", "none").strip().lower(),
        model=os.environ.get("CARD_VISION_MODEL", "deepseek-flash").strip(),
        base_url=os.environ.get("CARD_VISION_BASE_URL", "https://api.deepseek.com").strip(),
        detail=os.environ.get("CARD_VISION_DETAIL", "high").strip(),
        zone_size=_env_int("CARD_VISION_ZONE_SIZE", 704),
        max_zones=_env_int("CARD_VISION_MAX_ZONES", 9),
        concurrency=max(1, _env_int("CARD_VISION_CONCURRENCY", 4)),
        timeout_s=float(_env_int("CARD_VISION_TIMEOUT", 60)),
        max_retries=max(0, _env_int("CARD_VISION_RETRIES", 3)),
        include_back=_env_bool("CARD_VISION_INCLUDE_BACK", False),
        cache_dir=Path(cache) if cache else None,
        api_key=os.environ.get("DEEPSEEK_API_KEY", "").strip(),
    )
