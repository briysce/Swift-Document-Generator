"""Lightweight cache of best ensemble params per image hash."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent / ".cache"


def image_hash(img_bytes: bytes) -> str:
    return hashlib.sha256(img_bytes).hexdigest()[:16]


def load_cached(key: str) -> dict | None:
    path = CACHE_DIR / f"{key}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_cached(key: str, entry: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{key}.json"
    path.write_text(json.dumps(entry, indent=2), encoding="utf-8")
