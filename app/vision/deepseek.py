"""DeepSeek vision provider — ``deepseek-flash`` over the OpenAI-compatible API.

Why this provider exists: it removes the single biggest barrier for this
project. A local 7B VLM needs a GPU, 6 GB of VRAM and a 5 GB download before the
first card is graded; DeepSeek's hosted model needs an API key.

Two facts shape the implementation, both from the official docs:

* An image is resized to roughly a 1300x1300 equivalent before inference and
  costs at most **1024 tokens**, so sending a whole 4000 px card wastes
  resolution that gets thrown away. We send **zone crops** (~704 px) instead:
  same token ceiling, far more millimetres per pixel.
* JSON mode can legitimately return near-empty content (the API blanks
  non-JSON text rather than erroring), so an empty parse is retried rather than
  treated as "no defects".

The HTTP call uses ``urllib`` on purpose: no SDK, no extra dependency, and the
request body stays visible in one place for anyone auditing what leaves the
machine.
"""

from __future__ import annotations

import base64
import json
import random
import socket
import time
import urllib.error
import urllib.request
from typing import Any

import cv2
import numpy as np

from .prompts import SYSTEM_PROMPT, build_user_prompt
from .types import DEFECTS, SEVERITIES, VisionUsage, ZoneFinding, ZoneRequest

__all__ = ["DeepSeekVisionProvider", "VisionError"]


class VisionError(RuntimeError):
    """A single zone failed; the caller may continue with the others."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def encode_jpeg(image: np.ndarray, quality: int = 88) -> str:
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise VisionError("cannot encode crop as jpeg")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _strip_fences(text: str) -> str:
    """Models occasionally wrap JSON in ```json fences despite being told not to."""
    t = text.strip()
    if not t.startswith("```"):
        return t
    t = t.split("\n", 1)[1] if "\n" in t else t
    if t.rstrip().endswith("```"):
        t = t.rstrip()[:-3]
    return t.strip()


class DeepSeekVisionProvider:
    """Calls ``POST /chat/completions`` with one image and expects strict JSON."""

    name = "deepseek"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "deepseek-flash",
        base_url: str = "https://api.deepseek.com",
        detail: str = "high",
        timeout_s: float = 60.0,
        max_retries: int = 3,
        max_tokens: int = 600,
        thinking: bool = False,
    ) -> None:
        if not api_key:
            raise VisionError("DEEPSEEK_API_KEY is empty")
        self._key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.detail = detail
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        self.thinking = thinking

    # -- public ---------------------------------------------------------- #

    def inspect(self, req: ZoneRequest) -> tuple[list[ZoneFinding], VisionUsage]:
        payload = self._payload(req)
        usage = VisionUsage(requests=1, images=1)

        raw = self._post_with_retry(payload, usage)
        data = self._parse(raw)

        usage.prompt_tokens += int(data.get("usage", {}).get("prompt_tokens", 0))
        usage.completion_tokens += int(data.get("usage", {}).get("completion_tokens", 0))

        content = (
            data.get("choices", [{}])[0].get("message", {}).get("content") or ""
        ).strip()
        if not content.strip():
            # JSON mode blanks non-JSON output instead of failing; ask again.
            raise VisionError("model returned empty content (json mode blanked it)")

        body = json.loads(_strip_fences(content))
        return self._validate(body, req), usage

    # -- request --------------------------------------------------------- #

    def _payload(self, req: ZoneRequest) -> dict[str, Any]:
        b64 = encode_jpeg(req.image)
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": build_user_prompt(req.zone, req.side)},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64}",
                                "detail": self.detail,
                            },
                        },
                    ],
                },
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": self.max_tokens,
            "temperature": 0,
            "stream": False,
        }
        if not self.thinking:
            body["thinking"] = {"type": "disabled"}
        return body

    def _post_with_retry(self, payload: dict[str, Any], usage: VisionUsage) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if attempt:
                usage.retries += 1
                time.sleep(min(8.0, 0.8 * (2 ** (attempt - 1))) + random.random() * 0.3)
            try:
                return self._post(payload)
            except VisionError as exc:
                last = exc
                if not exc.retryable:
                    raise
            except (socket.timeout, TimeoutError) as exc:
                last = exc
        raise VisionError(f"retries exhausted: {last}")

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._key}",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                err = json.loads(exc.read().decode("utf-8"))
                detail = (err.get("error") or {}).get("message", "") or str(err)[:200]
            except Exception:  # noqa: BLE001 - error body is best effort
                detail = exc.reason
            retryable = exc.code in (408, 409, 429) or exc.code >= 500
            raise VisionError(
                f"HTTP {exc.code}: {detail}", status=exc.code, retryable=retryable
            ) from exc
        except urllib.error.URLError as exc:
            # DNS / TLS / connection reset — all worth one more try.
            raise VisionError(f"network: {exc.reason}", retryable=True) from exc

    # -- response -------------------------------------------------------- #

    @staticmethod
    def _parse(data: dict[str, Any]) -> dict[str, Any]:
        if "error" in data:
            raise VisionError(f"api error: {data['error']}")
        if not data.get("choices"):
            raise VisionError("response has no choices")
        return data

    @staticmethod
    def _validate(body: dict[str, Any], req: ZoneRequest) -> list[ZoneFinding]:
        """Reject anything outside the closed vocabulary instead of trusting it.

        A model that emits ``"worn corner"`` is telling you it ignored the
        contract; silently mapping that to ``corner_touch`` would hide the
        failure, so the finding is dropped and counted as an error upstream.
        """
        findings: list[ZoneFinding] = []
        for item in body.get("findings") or []:
            if not isinstance(item, dict):
                continue
            defect = str(item.get("defect", "")).strip().lower()
            if defect not in DEFECTS:
                raise VisionError(f"unknown defect name: {defect!r}")
            severity = str(item.get("severity", "minor")).strip().lower()
            if severity not in SEVERITIES:
                severity = "minor"
            try:
                confidence = float(item.get("confidence", 0.5))
            except (TypeError, ValueError):
                confidence = 0.5
            findings.append(
                ZoneFinding(
                    zone=req.zone,
                    side=req.side,
                    defect=defect,
                    severity=severity,
                    confidence=max(0.0, min(1.0, confidence)),
                    evidence=str(item.get("evidence", "")).strip()[:160],
                )
            )
        return findings
