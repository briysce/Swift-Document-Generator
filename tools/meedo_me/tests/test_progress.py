"""The hourly update is Meedo-Me's own account of the hour, short enough for a phone."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from tools.meedo_me import connect
from tools.meedo_me.progress import MAX_CHARS, digest

NOW = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)


def _j(*entries):
    return {"entries": list(entries)}


def _e(ts, agent, kind, summary, task="1", **ev):
    e = {"id": f"{ts}-{agent}", "ts": ts, "agent": agent, "kind": kind, "task": task, "summary": summary}
    if ev:
        e["evidence"] = ev
    return e


FULL = dict(tests="t", looked="l", review="r", measured="m", episode="n/a: none")


def test_the_hour_is_reported_and_older_work_is_not():
    j = _j(_e("2026-09-26T13:00:00Z", "claude", "done", "old unit", **FULL),
           _e("2026-09-26T14:30:00Z", "claude", "done", "Swift logo into the app", **FULL),
           _e("2026-09-26T14:40:00Z", "cursor", "finding", "dot lost in prepare"))
    text = digest(now=NOW, journal=j, consultations={}, episodes={}, commits=["abc Fix x"])
    assert "Swift logo into the app — house rules answered" in text
    assert "old unit" not in text and "Cursor:" in text and "Pushed 1 commit" in text


def test_a_skipped_house_rule_is_named():
    j = _j(_e("2026-09-26T14:30:00Z", "claude", "done", "quick fix", tests="t"))
    text = digest(now=NOW, journal=j, consultations={}, episodes={}, commits=[])
    assert "missing looked, review, measured, episode" in text and "Meedo-Me flags: 1" in text


def test_a_quiet_hour_still_says_what_is_held():
    j = _j(_e("2026-09-26T12:00:00Z", "cursor", "claim", "PROPAK dot", task="7"))
    text = digest(now=NOW, journal=j, consultations={}, episodes={}, commits=[])
    assert "No units finished" in text and "#7 cursor: PROPAK dot — quiet 180 min" in text


def test_it_fits_a_phone_screen():
    j = _j(*[_e(f"2026-09-26T14:{m:02d}:00Z", "claude", "finding", "x" * 300) for m in range(50)])
    assert len(digest(now=NOW, journal=j, consultations={}, episodes={}, commits=[])) <= MAX_CHARS


def test_whatsapp_admits_only_the_users_number_and_schedules_hourly(tmp_path):
    cfg = tmp_path / "openclaw.json"
    cfg.write_text(json.dumps({"channels": {"whatsapp": {"allowFrom": ["+15550001111"], "dmPolicy": "open"}}}))
    path, automation = connect.connect_whatsapp("780-555-0123", cfg)
    wa = json.loads(path.read_text())["channels"]["whatsapp"]
    assert wa["dmPolicy"] == "allowlist" and wa["allowFrom"] == ["+15550001111", "+17805550123"]
    assert '"0 * * * *"' in automation and '--to "+17805550123"' in automation
    assert "tools.meedo_me.progress" in automation and "--announce --channel whatsapp" in automation
    with pytest.raises(ValueError):
        connect.connect_whatsapp("12345", cfg)
