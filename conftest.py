"""Tests never write Meedo-Me's real memory.

Meedo-Me learns from its memory files (episodes, ledger, journal,
consultations, AI lessons, traces). A test that writes them teaches it things
that never happened: collab_mind's tests once added the same fake Arc
escalation ("palette mush") as a new episode and lesson on every run — seven
by the time anyone noticed. Every test gets its own empty memory, and a
write to a real memory file from a test fails where it happens.
"""

from __future__ import annotations

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


def _real_targets() -> set[Path]:
    out = {q.resolve() for q in REAL}
    return out


def _is_real(target) -> bool:
    try:
        q = Path(target).resolve()
    except (TypeError, OSError):
        return False
    return q in _REAL or (q.parent == _TRACES and q.suffix == ".jsonl")


_REAL: set[Path] = set()
_TRACES = (MEMORY / "meedo_traces").resolve()


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
def _real_memory_is_read_only():
    """Any write from this process to a real memory file fails at the write,
    naming it. Other processes may write real memory while tests run (an
    engine run records its reviews), so comparing the files before and after
    would blame the tests for work that was not theirs — it did, once."""
    import builtins
    import io

    _REAL.update(_real_targets())
    real_open, path_open = builtins.open, Path.open

    def guarded(file, mode="r", *a, **kw):
        if any(c in mode for c in "wax+") and _is_real(file):
            raise AssertionError(f"a test wrote Meedo-Me's real memory: {file}")
        return real_open(file, mode, *a, **kw)

    def guarded_path_open(self, mode="r", *a, **kw):
        if any(c in mode for c in "wax+") and _is_real(self):
            raise AssertionError(f"a test wrote Meedo-Me's real memory: {self}")
        return path_open(self, mode, *a, **kw)

    builtins.open = guarded
    io.open = guarded
    Path.open = guarded_path_open
    try:
        yield
    finally:
        builtins.open, io.open, Path.open = real_open, real_open, path_open
