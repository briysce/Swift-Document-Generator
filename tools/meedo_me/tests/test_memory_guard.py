"""The test-session guard stops a test writing Meedo-Me's real memory."""

from __future__ import annotations

from pathlib import Path

import pytest

MEMORY = Path(__file__).resolve().parents[3] / "qa_logos" / "synthetic"


def test_opening_real_memory_for_writing_fails_in_tests():
    real = MEMORY / "meedo_ledger.json"
    with pytest.raises(AssertionError, match="real memory"):
        open(real, "a")  # append mode: writes nothing even if the guard failed
    with pytest.raises(AssertionError, match="real memory"):
        real.open("a")
    with pytest.raises(AssertionError, match="real memory"):
        (MEMORY / "meedo_traces" / "claude-2000-01-01.jsonl").write_text("x")
    assert not (MEMORY / "meedo_traces" / "claude-2000-01-01.jsonl").exists()


def test_reading_real_memory_is_fine(tmp_path):
    assert (MEMORY / "meedo_ledger.json").read_text()
    (tmp_path / "x.json").write_text("{}")
