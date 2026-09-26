"""Load gitignored .env files for AI advisor API keys (never committed).

Delegates to ``tools.ai_collab.env`` so Meedo / improve loops / logo advisors
share one secret path (``tools/logo_vectorizer/.env`` first).
"""

from __future__ import annotations

import os
from pathlib import Path

from tools.ai_collab.env import (  # noqa: F401 — re-export
    ENV_CANDIDATES,
    ai_configured,
    claude_configured,
    gemini_configured,
    load_env,
)

TOOL_DIR = Path(__file__).resolve().parent
ROOT = TOOL_DIR.parents[1]


def ai_advisors_configured() -> bool:
    return ai_configured() or bool(os.environ.get("OPENAI_API_KEY", "").strip())
