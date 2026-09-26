"""HTTP + JSON helpers shared by Gemini/Claude text clients (fail-open friendly)."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any


class AiQuotaExhausted(RuntimeError):
    """API key missing, billed quota empty, or token budget exhausted."""


_QUOTA_MARKERS = (
    "resource_exhausted",
    "insufficient_quota",
    "quota exceeded",
    "quota_exceeded",
    "rate_limit_exceeded",
    "billing",
    "out of tokens",
    "token limit",
    "context_length_exceeded",
    "max_tokens",
    "credit",
    "payment required",
)


def looks_like_quota_or_token_error(status: int | None, body: str = "") -> bool:
    if status in (401, 402, 403):
        return True
    blob = (body or "").lower()
    if status == 429 and any(m in blob for m in _QUOTA_MARKERS):
        return True
    if status == 429 and not blob:
        return False
    return any(m in blob for m in _QUOTA_MARKERS)


def extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {"value": data}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, dict) else {"value": data}
        raise


def http_post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int = 90,
    *,
    max_retries: int = 3,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    last_err: Exception | None = None
    for attempt in range(max_retries):
        req = urllib.request.Request(
            url,
            data=body,
            headers={**headers, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            if looks_like_quota_or_token_error(exc.code, detail):
                raise AiQuotaExhausted(
                    f"AI quota/token exhausted (HTTP {exc.code}): {detail}"
                ) from exc
            last_err = RuntimeError(f"HTTP {exc.code}: {detail}")
            if exc.code in (429, 500, 502, 503, 504) and attempt + 1 < max_retries:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay = float(retry_after) if retry_after else (1.5 * (2**attempt))
                except ValueError:
                    delay = 1.5 * (2**attempt)
                time.sleep(min(max(delay, 0.5), 20.0))
                continue
            raise last_err from exc
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, AiQuotaExhausted):
                raise
            last_err = exc
            if attempt + 1 < max_retries:
                time.sleep(1.5 * (2**attempt))
                continue
            raise
    raise last_err or RuntimeError("http_post_json failed")
