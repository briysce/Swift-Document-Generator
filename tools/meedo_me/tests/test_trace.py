"""The procedure trace keeps the steps, never the secrets or the payloads."""

from __future__ import annotations

import json

from tools.meedo_me import trace as T


def test_keys_and_phone_numbers_never_reach_the_trace(monkeypatch):
    monkeypatch.setattr(T, "_SECRET_VALUES", ["local-env-value-123"])
    text = T.redact("sk-ant-api03-abcdefghij AIzaSyA1234567890abcdefghijk AQ.FAKEfakeFAKEfakeFAKEfake00 "
                    "GEMINI_API_KEY=abcdef123 x-api-key: abc123 local-env-value-123 call 780-555-0123 "
                    "or +1 (780) 555 0123")
    for leak in ("abcdefghij", "1234567890abcdefghijk", "FAKEfake", "abcdef123", "abc123", "local-env-value", "555"):
        assert leak not in text, leak


def test_edits_keep_the_file_and_size_not_the_contents():
    r = T.compact("Write", {"file_path": "a.py", "content": "SECRET CODE" * 10})
    assert r == {"file": "a.py", "chars": 110}
    r = T.compact("Edit", {"file_path": "a.py", "old_string": "x", "new_string": "yy"})
    assert r == {"file": "a.py", "removed": 1, "added": 2}


def test_a_transcript_imports_once_with_failures_and_notes(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "TRACES", tmp_path / "traces")
    lines = [
        {"type": "assistant", "timestamp": "2026-09-26T13:00:00.000Z", "message": {"content": [
            {"type": "text", "text": "Run the tests."},
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest -q", "description": "tests"}}]}},
        {"type": "user", "timestamp": "2026-09-26T13:00:05.000Z", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "boom"}]}},
        {"type": "user", "timestamp": "2026-09-26T13:00:06.000Z", "message": {"content": "user words stay out"}},
    ]
    src = tmp_path / "s.jsonl"
    src.write_text("\n".join(json.dumps(x) for x in lines))
    assert T.from_transcript(src, "claude") == 1
    assert T.from_transcript(src, "claude") == 0          # idempotent
    row = json.loads((tmp_path / "traces" / "claude-2026-09-26.jsonl").read_text())
    assert row["cmd"] == "pytest -q" and row["failed"] and row["note"] == "Run the tests."
    assert "user words" not in (tmp_path / "traces" / "claude-2026-09-26.jsonl").read_text()
