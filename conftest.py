"""Tests never write Meedo-Me's real memory.

Meedo-Me learns from its memory files (episodes, ledger, journal,
consultations, AI lessons, traces). A test that writes them teaches it things
that never happened: collab_mind's tests once added the same fake Arc
escalation ("palette mush") as a new episode and lesson on every run — seven
by the time anyone noticed. Every test gets its own empty memory, and the
session fails if a real memory file changed anyway.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
MEMORY = ROOT / "qa_logos" / "synthetic"
REAL = [MEMORY / n for n in ("meedo_episodes.json", "meedo_ledger.json", "meedo_journal.json",
                             "meedo_consultations.json", "meedo_ai_lessons.json", "meedo_procedures.json")]

# (module, attribute, file name) for every place a memory path lives.
_PATHS = [
    ("tools.logo_vectorizer.meedo_episodes", "EPISODES", "meedo_episodes.json"),
    ("tools.logo_vectorizer.meedo_ledger", "LEDGER", "meedo_ledger.json"),
    ("tools.logo_vectorizer.meedo_journal", "JOURNAL", "meedo_journal.json"),
    ("tools.logo_vectorizer.meedo_consult", "CONSULTATIONS", "meedo_consultations.json"),
    ("tools.ai_collab.learn", "DEFAULT_PATH", "meedo_ai_lessons.json"),
    ("tools.ai_collab.learn", "CANONICAL_LESSONS_PATH", "meedo_ai_lessons.json"),
    ("tools.ai_collab.procedures", "DEFAULT_PATH", "meedo_procedures.json"),
    ("tools.meedo_me.trace", "TRACES", "meedo_traces"),
]


def _digest() -> dict[str, str]:
    out = {}
    for p in REAL:
        out[p.name] = hashlib.sha1(p.read_bytes()).hexdigest() if p.is_file() else ""
    traces = MEMORY / "meedo_traces"
    if traces.is_dir():
        for p in sorted(traces.glob("*.jsonl")):
            out[f"traces/{p.name}"] = hashlib.sha1(p.read_bytes()).hexdigest()
    return out


@pytest.fixture(autouse=True)
def _private_meedo_memory(tmp_path_factory, monkeypatch):
    import importlib

    mem = tmp_path_factory.mktemp("meedo_memory")
    for mod_name, attr, name in _PATHS:
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, mem / name)
    yield


@pytest.fixture(autouse=True, scope="session")
def _real_memory_untouched():
    before = _digest()
    yield
    changed = [k for k, v in _digest().items() if before.get(k) != v]
    if changed:
        pytest.fail(f"tests wrote Meedo-Me's real memory: {changed}", pytrace=False)
