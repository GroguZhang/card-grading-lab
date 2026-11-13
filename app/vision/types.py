"""Types shared by every vision provider.

The provider contract is deliberately narrow: hand it one crop and the tag of
the region it came from, get back zero or more findings with a severity and a
short justification. No provider is allowed to return a *grade* — grading stays
in ``app/scoring.py`` where it is deterministic and auditable. A model that
could return "8.5" would be a model whose arithmetic you have to trust.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

__all__ = [
    "DEFECTS",
    "SEVERITIES",
    "ZoneRequest",
    "ZoneFinding",
    "VisionUsage",
    "VisionResult",
]

#: The only defect names the model may emit. Keeping this closed is what makes
#: the output comparable across runs; a free-form string would produce
#: "whitening", "white edge wear" and "corner wear" for the same chip.
DEFECTS: dict[str, str] = {
    "corner_touch": "角部触碰（角尖有轻微压痕/钝化）",
    "whitening": "露白（印刷层磨损露出纸底）",
    "chipping": "分层（纸板边缘起层）",
    "fraying": "毛边（边缘纤维翘起）",
    "nick": "缺口（边缘有小块缺失）",
    "scratch": "划痕（表面线状损伤）",
    "print_line": "印刷线（印刷缺陷直纹）",
    "dimple": "凹点（表面小凹坑）",
    "stain": "污渍（表面污点）",
    "crease": "折痕（纸板结构性折线）",
}

SEVERITIES: dict[str, str] = {
    "minor": "需放大才能确认，通常小于 2mm，不影响整体观感",
    "major": "肉眼直接可见，约 2-5mm",
    "critical": "结构性损伤（折痕、缺角、水渍浸透），评级会明显掉档",
}


@dataclass(slots=True)
class ZoneRequest:
    """One crop on its way to a model."""

    zone: str
    side: str
    image: np.ndarray
    location_hint: str = ""

    @property
    def key(self) -> str:
        return f"{self.side}:{self.zone}"


@dataclass(slots=True)
class ZoneFinding:
    zone: str
    side: str
    defect: str
    severity: str
    confidence: float
    evidence: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "zone": self.zone,
            "side": self.side,
            "defect": self.defect,
            "severity": self.severity,
            "confidence": round(self.confidence, 3),
            "evidence": self.evidence,
        }


@dataclass(slots=True)
class VisionUsage:
    """Token accounting, so cost per card is a measured number, not a guess."""

    requests: int = 0
    images: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hits: int = 0
    retries: int = 0

    def add(self, other: "VisionUsage") -> None:
        self.requests += other.requests
        self.images += other.images
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.cache_hits += other.cache_hits
        self.retries += other.retries

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def cost_usd(self, price_in: float = 0.15, price_out: float = 0.60) -> float:
        """Off-peak ``deepseek-flash`` rates per 1M tokens by default."""
        return (
            self.prompt_tokens / 1_000_000 * price_in
            + self.completion_tokens / 1_000_000 * price_out
        )

    def summary(self) -> str:
        return (
            f"{self.requests} 次请求 / {self.images} 张图 / "
            f"{self.prompt_tokens} 输入 + {self.completion_tokens} 输出 token"
            + (f" / 缓存命中 {self.cache_hits}" if self.cache_hits else "")
        )


@dataclass(slots=True)
class VisionResult:
    findings: list[ZoneFinding] = field(default_factory=list)
    usage: VisionUsage = field(default_factory=VisionUsage)
    provider: str = "none"
    model: str = ""
    errors: list[str] = field(default_factory=list)
    zones_inspected: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.zones_inspected) and not self.errors

    def describe(self) -> str:
        return (
            f"{self.provider}/{self.model}：检查 {len(self.zones_inspected)} 个分区，"
            f"{len(self.findings)} 条信号，{self.usage.summary()}"
        )
