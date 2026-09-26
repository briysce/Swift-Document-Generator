"""App-wide Gemini ↔ Claude collaboration + Meedo-Me learning.

Shared by Meedo-Me, improve loops, and (via thin adapters) logo_vectorizer.
Keys load only from gitignored ``.env`` files — never commit secrets.
All network paths fail open when APIs are dark or quota-exhausted.
"""

from __future__ import annotations

from .advise import Advice, advise
from .client import available, claude_available, gemini_available, gemini_text, claude_text
from .deliberate import Deliberation, deliberate
from .env import load_env, ai_configured
from .learn import Lesson, persist_lesson, recall_lessons, lessons_path

__all__ = [
    "Advice",
    "Deliberation",
    "Lesson",
    "advise",
    "ai_configured",
    "available",
    "claude_available",
    "claude_text",
    "deliberate",
    "gemini_available",
    "gemini_text",
    "lessons_path",
    "load_env",
    "persist_lesson",
    "recall_lessons",
]
