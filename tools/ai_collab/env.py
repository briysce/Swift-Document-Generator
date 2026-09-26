"""Load gitignored API keys for every Python caller (Meedo, scripts, vectorizer).

Canonical search order (first file wins per key unless override=True):

1. ``tools/logo_vectorizer/.env``  — existing logo advisor keys
2. ``tools/ai_collab/.env``         — optional shared overlay
3. repo ``.env.local`` / ``.env``

Never commit these files. Rotate any key that was pasted into chat.
"""

from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
VECTORIZER_DIR = ROOT / "tools" / "logo_vectorizer"

ENV_CANDIDATES = (
    VECTORIZER_DIR / ".env",
    HERE / ".env",
    ROOT / ".env.local",
    ROOT / ".env",
)


def load_env(*, override: bool = False) -> list[Path]:
    """Load KEY=VALUE pairs. Returns paths that contributed at least one line."""
    loaded: list[Path] = []
    for path in ENV_CANDIDATES:
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        any_key = False
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key:
                continue
            if override or key not in os.environ or not os.environ.get(key, "").strip():
                os.environ[key] = value
            any_key = True
        if any_key:
            loaded.append(path)
    return loaded


def env_key(*names: str) -> str | None:
    load_env()
    for name in names:
        val = os.environ.get(name, "").strip()
        if val:
            return val
    return None


def gemini_configured() -> bool:
    return env_key("GOOGLE_API_KEY", "GEMINI_API_KEY") is not None


def claude_configured() -> bool:
    return env_key("ANTHROPIC_API_KEY") is not None


def ai_configured() -> bool:
    return gemini_configured() or claude_configured()
