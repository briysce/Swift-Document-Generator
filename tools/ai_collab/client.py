"""Thin Gemini + Claude text clients for app-wide use (no vision required).

Vision advisors stay in ``tools/logo_vectorizer/ai_advisors``; this module is
the shared text path Meedo / improve loops / scripts call so secrets and
fail-open behaviour live in one place.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from .env import claude_configured, env_key, gemini_configured, load_env
from .http_util import AiQuotaExhausted, extract_json, http_post_json

# Defaults match logo collab_mind (override via env).
GEMINI_MODEL = (
    os.environ.get("GEMINI_MODEL", "gemini-flash-latest").strip() or "gemini-flash-latest"
)
CLAUDE_MODEL = (
    os.environ.get("ANTHROPIC_MODEL")
    or os.environ.get("CLAUDE_MODEL")
    or "claude-sonnet-4-5"
).strip() or "claude-sonnet-4-5"


def gemini_available() -> bool:
    load_env()
    return gemini_configured()


def claude_available() -> bool:
    load_env()
    return claude_configured()


def available() -> bool:
    return gemini_available() or claude_available()


def gemini_text(
    prompt: str,
    *,
    model: str | None = None,
    temperature: float = 0.2,
    timeout: int = 60,
) -> str | None:
    """Return Gemini text, or None on any failure (fail-open)."""
    load_env()
    key = env_key("GOOGLE_API_KEY", "GEMINI_API_KEY")
    if not key:
        return None
    use = (model or os.environ.get("GEMINI_MODEL") or GEMINI_MODEL).strip()
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{use}:generateContent?key={key}"
    )
    payload: dict[str, Any] = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": temperature,
        },
    }
    try:
        data = http_post_json(url, payload, headers={}, timeout=timeout, max_retries=3)
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except AiQuotaExhausted as exc:
        print(f"[ai_collab] gemini quota: {exc}", file=sys.stderr)
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"[ai_collab] gemini failed: {exc}", file=sys.stderr)
        return None


def claude_text(
    prompt: str,
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1600,
    timeout: int = 90,
) -> str | None:
    """Return Claude text, or None on any failure (fail-open)."""
    load_env()
    key = env_key("ANTHROPIC_API_KEY")
    if not key:
        return None
    use = (
        model
        or os.environ.get("ANTHROPIC_MODEL")
        or os.environ.get("CLAUDE_MODEL")
        or CLAUDE_MODEL
    ).strip()
    try:
        data = http_post_json(
            "https://api.anthropic.com/v1/messages",
            {
                "model": use,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": "user", "content": prompt}],
            },
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            },
            timeout=timeout,
            max_retries=3,
        )
        parts = data.get("content") or []
        texts = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"]
        out = "\n".join(t for t in texts if t).strip()
        return out or None
    except AiQuotaExhausted as exc:
        print(f"[ai_collab] claude quota: {exc}", file=sys.stderr)
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"[ai_collab] claude failed: {exc}", file=sys.stderr)
        return None


def gemini_json(prompt: str, **kwargs: Any) -> dict[str, Any] | None:
    text = gemini_text(prompt, **kwargs)
    if not text:
        return None
    try:
        return extract_json(text)
    except Exception as exc:  # noqa: BLE001
        print(f"[ai_collab] gemini json parse failed: {exc}", file=sys.stderr)
        return None


def claude_json(prompt: str, **kwargs: Any) -> dict[str, Any] | None:
    text = claude_text(prompt, **kwargs)
    if not text:
        return None
    try:
        return extract_json(text)
    except Exception as exc:  # noqa: BLE001
        print(f"[ai_collab] claude json parse failed: {exc}", file=sys.stderr)
        return None
