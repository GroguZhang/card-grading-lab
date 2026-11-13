"""Fan-out of zone crops to a provider, with caching and partial failure.

Three properties matter more than speed here:

* **A failing zone must not fail the card.** Nine requests in parallel means
  nine chances for a 429 or a blank JSON; the report should still be produced,
  with the failure recorded and the affected dimension marked as partially
  assessed, rather than surfacing a stack trace.
* **The same crop is never paid for twice.** Crops are hashed; a re-run of the
  same card reads from disk and costs nothing.
* **Every call is accounted.** Token usage is summed so the report can state
  what the analysis cost instead of hand-waving.
"""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, Protocol, Sequence

import numpy as np

from ..zone_splitter import ZONE_ORDER, ZoneCrop
from .deepseek import encode_jpeg
from .prompts import PROMPT_VERSION
from .types import VisionResult, VisionUsage, ZoneFinding, ZoneRequest

__all__ = ["VisionProvider", "build_requests", "inspect_zones"]


class VisionProvider(Protocol):
    name: str
    model: str

    def inspect(self, req: ZoneRequest) -> tuple[list[ZoneFinding], VisionUsage]: ...


def build_requests(
    crops: dict[str, ZoneCrop],
    *,
    max_zones: int = 9,
    include_back: bool = False,
) -> list[ZoneRequest]:
    """Pick which crops to send, in a stable order.

    Front corners and edges first: they are where the grade is actually lost.
    The centre crop is last because surface defects weigh least in the model.
    """
    sides = ("front", "back") if include_back else ("front",)
    out: list[ZoneRequest] = []
    for side in sides:
        for zone in ZONE_ORDER:
            crop = crops.get(f"{side}:{zone}")
            if crop is None:
                continue
            out.append(ZoneRequest(zone=zone, side=side, image=crop.image))
    return out[: max(1, max_zones) * len(sides)]


def _cache_key(req: ZoneRequest, model: str) -> str:
    b64 = encode_jpeg(req.image)
    h = hashlib.sha256()
    h.update(b64.encode("ascii"))
    h.update(f"|{model}|{PROMPT_VERSION}|{req.zone}|{req.side}".encode("utf-8"))
    return h.hexdigest()[:32]


def _read_cache(cache_dir: Path | None, key: str) -> list[ZoneFinding] | None:
    if cache_dir is None:
        return None
    path = cache_dir / f"{key}.json"
    if not path.exists():
        return None
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return [
        ZoneFinding(
            zone=r["zone"],
            side=r["side"],
            defect=r["defect"],
            severity=r["severity"],
            confidence=float(r["confidence"]),
            evidence=r.get("evidence", ""),
        )
        for r in rows
    ]


def _write_cache(cache_dir: Path | None, key: str, findings: Iterable[ZoneFinding]) -> None:
    if cache_dir is None:
        return
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        rows = [f.as_dict() for f in findings]
        (cache_dir / f"{key}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except OSError:
        pass  # a cache miss next time is not worth failing a report over


def inspect_zones(
    requests: Sequence[ZoneRequest],
    provider: VisionProvider,
    *,
    concurrency: int = 4,
    cache_dir: Path | None = None,
    attempts_per_zone: int = 2,
) -> VisionResult:
    """Inspect every request, collecting findings and tolerating failures."""
    result = VisionResult(provider=getattr(provider, "name", "?"), model=provider.model)
    lock = threading.Lock()

    def work(req: ZoneRequest) -> tuple[ZoneRequest, list[ZoneFinding], VisionUsage | None, str | None]:
        key = _cache_key(req, provider.model)
        cached = _read_cache(cache_dir, key)
        if cached is not None:
            return req, cached, None, None

        last_err: str | None = None
        for _ in range(max(1, attempts_per_zone)):
            try:
                findings, usage = provider.inspect(req)
            except Exception as exc:  # noqa: BLE001 - provider errors are data
                last_err = f"{req.key}: {type(exc).__name__}: {exc}"
                continue
            _write_cache(cache_dir, key, findings)
            return req, findings, usage, None
        return req, [], None, last_err

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [pool.submit(work, r) for r in requests]
        for fut in as_completed(futures):
            req, findings, usage, err = fut.result()
            with lock:
                if err:
                    result.errors.append(err)
                else:
                    result.zones_inspected.append(req.key)
                    result.findings.extend(findings)
                    if usage is None:
                        result.usage.cache_hits += 1
                    else:
                        result.usage.add(usage)
                        result.usage.images += 0  # already counted per request

    # Stable order so two runs of the same card produce identical JSON/report.
    result.findings.sort(key=lambda f: (f.side, ZONE_ORDER.index(f.zone), f.defect))
    return result
