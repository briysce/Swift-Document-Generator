"""Load gitignored .env files for AI advisor API keys (never committed)."""

from __future__ import annotations

import os
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
ROOT = TOOL_DIR.parents[1]

ENV_CANDIDATES = (
    TOOL_DIR / ".env",
    ROOT / ".env.local",
    ROOT / ".env",
)


def load_env(*, override: bool = False) -> list[Path]:
    """Load KEY=VALUE pairs from local env files. Returns paths loaded."""
    loaded: list[Path] = []
    for path in ENV_CANDIDATES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key:
                continue
            if override or key not in os.environ:
                os.environ[key] = value
        loaded.append(path)
    return loaded


def gemini_configured() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
