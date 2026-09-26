"""Meedo progress digests for OpenClaw WhatsApp — no live OpenClaw required."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.meedo_me.progress import (  # noqa: E402
    format_digest,
    register_hourly,
    whatsapp_to,
)


def test_whatsapp_to_normalizes_na_numbers(monkeypatch):
    monkeypatch.setenv("MEEDO_WHATSAPP_TO", "555-123-4567")
    assert whatsapp_to() == "+15551234567"
    monkeypatch.setenv("MEEDO_WHATSAPP_TO", "+15551234567")
    assert whatsapp_to() == "+15551234567"
    monkeypatch.delenv("MEEDO_WHATSAPP_TO", raising=False)
    monkeypatch.setenv("OPENCLAW_WHATSAPP_TO", "15551234567")
    assert whatsapp_to() == "+15551234567"


def test_format_digest_includes_board_and_commits():
    data = {
        "agent": "claude",
        "hours": 1,
        "ts": "2026-09-26T13:00:00Z",
        "branch": "claude/relaxed-babbage-igbk0v",
        "board": [
            {"n": 1, "task": "Put Swift logo in app", "owner": "claude", "status": "in progress"}
        ],
        "journal": [
            {
                "kind": "finding",
                "task": 1,
                "summary": "SVG path ready",
            }
        ],
        "commits": ["abc1234 Wire Swift assets"],
    }
    text = format_digest(data)
    assert "Meedo-Me · claude progress" in text
    assert "#1" in text
    assert "SVG path ready" in text
    assert "abc1234" in text


def test_register_hourly_dry_run_writes_wrapper(tmp_path, monkeypatch):
    monkeypatch.setenv("MEEDO_WHATSAPP_TO", "+15551234567")
    # Point wrapper into tmp via monkeypatch on module constant
    import tools.meedo_me.progress as P

    wrap = tmp_path / "hourly.sh"
    monkeypatch.setattr(P, "WRAPPER", wrap)
    out = P.register_hourly(agent="claude", every="1h", dry_run=True)
    assert wrap.is_file()
    body = wrap.read_text(encoding="utf-8")
    assert "tools.meedo_me.progress" in body
    assert "--send" in body
    assert "openclaw" in out.lower()
    assert "+15551234567" in out
