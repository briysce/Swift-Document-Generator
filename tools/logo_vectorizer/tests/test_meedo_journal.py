"""Work journal: units are logged, claims and stale claims surface, thin dones are flagged —
whichever agent wrote them, in either vocabulary."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from tools.logo_vectorizer import meedo_journal as J
from tools.logo_vectorizer.meedo_merge import merge

T0 = datetime(2026, 9, 26, 13, 0, tzinfo=timezone.utc)
FULL = dict(tests="pytest 30 passed", looked="PDFs at 8x", review="passed", measured="0/54 verdicts changed",
            episode="E0026")


def test_a_done_unit_answers_every_house_rule_or_says_why_not(tmp_path):
    p = tmp_path / "j.json"
    full = J.log(agent="claude", kind="done", summary="a", task="1", at=T0, path=p, commit="x", **FULL)
    assert J.gaps(full) == []
    na = J.log(agent="claude", kind="done", summary="b", at=T0 + timedelta(seconds=1), path=p, commit="x",
               **dict(FULL, looked="n/a: no visual output"))
    assert J.gaps(na) == []
    bare = J.log(agent="cursor", kind="done", summary="c", at=T0, path=p, commit="x", tests="flutter 3 passed")
    assert J.gaps(bare) == ["looked", "review", "measured", "episode"]


def test_nothing_is_refused_for_a_gap_but_the_standup_shows_it(tmp_path):
    p = tmp_path / "j.json"
    J.log(agent="cursor", kind="done", summary="fixed the dot", task="7", at=T0, path=p, commit="x")
    lines = J.standup_lines(path=p, now=T0)
    assert any("cursor: 1 unit(s) done, 0 with every house rule answered" in ln for ln in lines)
    assert any("missing tests, looked, review, measured, episode" in ln for ln in lines)


def test_a_claim_is_open_until_its_holder_closes_it_and_stale_after_45_quiet_minutes(tmp_path):
    p = tmp_path / "j.json"
    J.log(agent="cursor", kind="claim", summary="PROPAK dot", task="#7", at=T0, path=p, commit="x")
    J.log(agent="claude", kind="claim", summary="Swift into app", task="1", at=T0, path=p, commit="x")
    J.log(agent="claude", kind="done", summary="Swift into app", task="1", at=T0 + timedelta(minutes=30),
          path=p, commit="x", **FULL)
    claims = J.open_claims(J.load(p)["entries"], now=T0 + timedelta(minutes=50))
    assert [(c["agent"], c["task"], c["stale"]) for c in claims] == [("cursor", 7, True)]
    fresh = J.open_claims(J.load(p)["entries"], now=T0 + timedelta(minutes=20))
    assert not fresh[0]["stale"]


def test_the_assessment_counts_each_agents_units(tmp_path):
    p = tmp_path / "j.json"
    J.log(agent="claude", kind="done", summary="a", at=T0, path=p, commit="x", **FULL)
    J.log(agent="claude", kind="done", summary="b", at=T0 + timedelta(minutes=1), path=p, commit="x")
    J.log(agent="claude", kind="finding", summary="reviewer false alarm", at=T0 + timedelta(minutes=2), path=p,
          commit="x")
    a = J.assess(path=p, now=T0)["agents"]["claude"]
    assert (a["done"], a["complete"], a["findings"], a["evidence_rate"]) == (2, 1, 1, 0.5)
    assert a["gaps"]["tests"] == 1


def test_bad_entries_are_refused(tmp_path):
    p = tmp_path / "j.json"
    with pytest.raises(ValueError):
        J.log(agent="", kind="done", summary="x", path=p, commit="x")
    with pytest.raises(ValueError):
        J.log(agent="claude", kind="finished", summary="x", path=p, commit="x")
    with pytest.raises(TypeError):
        J.log(agent="claude", kind="done", summary="x", path=p, commit="x", vibes="good")


def test_both_agents_histories_survive_a_merge(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    J.log(agent="claude", kind="claim", summary="x", at=T0, path=a, commit="x")
    J.log(agent="cursor", kind="claim", summary="y", at=T0, path=b, commit="x")  # same moment, other agent
    J.log(agent="claude", kind="claim", summary="x", at=T0, path=b, commit="x")  # already on both sides
    assert merge(str(a), str(b))
    got = json.loads(a.read_text())["entries"]
    assert sorted((e["agent"], e["summary"]) for e in got) == [("claude", "x"), ("cursor", "y")]


def test_one_agent_logging_twice_in_a_second_keeps_both(tmp_path):
    p = tmp_path / "j.json"
    a = J.log(agent="claude", kind="done", summary="a", at=T0, path=p, commit="x")
    b = J.log(agent="claude", kind="done", summary="b", at=T0, path=p, commit="x")
    assert a["id"] != b["id"] and len(J.load(p)["entries"]) == 2


def test_a_backfill_dated_in_the_future_is_refused(tmp_path):
    with pytest.raises(ValueError, match="future"):
        J.log(agent="claude", kind="finding", summary="x", path=tmp_path / "j.json", commit="x",
              at=datetime.now(timezone.utc) + timedelta(minutes=30))


def test_log_and_recent(tmp_path):
    path = tmp_path / "meedo_journal.json"
    e = J.log(
        agent="cursor",
        kind="claim",
        summary="hold #7",
        task=7,
        branch="cursor/logo-engine-collab-d4c9",
        path=path,
    )
    assert e["id"].endswith("-cursor")
    J.log(agent="cursor", kind="finding", summary="stage trace", task=7, path=path)
    got = J.recent(agent="cursor", path=path, limit=5)
    assert [g["kind"] for g in got] == ["finding", "claim"]


def test_standup_flags_stale_claim_and_thin_done(tmp_path):
    path = tmp_path / "meedo_journal.json"
    # Backdate a claim by writing then patching ts.
    c = J.log(agent="claude", kind="claim", summary="swift app", task=1, path=path)
    data = J.load(path)
    data["entries"][0]["ts"] = "2026-09-26T10:00:00Z"
    J.save(data, path)

    J.log(
        agent="cursor",
        kind="done",
        summary="docs only",
        task=7,
        path=path,
        # deliberately omit tests / images / measured
        commit="",
    )
    # Clear auto commit on the thin done
    data = J.load(path)
    for e in data["entries"]:
        if e["kind"] == "done":
            e["evidence"] = {}
    J.save(data, path)

    now = time.time()
    rep = J.standup(path=path, now=now)
    assert any(s["task"] == 1 and s["agent"] == "claude" for s in rep["stale_claims"])
    assert any(d["task"] == 7 and "tests" in d["missing"] for d in rep["done_missing_evidence"])


def test_entries_in_either_vocabulary_answer_the_same_rules(tmp_path):
    """Cursor's first entries said images_looked_at / measured_vs_previous and
    kept the commit in evidence; Claude Code's said looked / measured."""
    cursor_style = {"id": "Jabc", "ts": "2026-09-26T13:00:00Z", "agent": "cursor", "task": 7, "kind": "done",
                    "summary": "x", "evidence": {"commit": "abc", "tests": "t", "images_looked_at": True,
                                                 "measured_vs_previous": "m", "review": "n/a: docs",
                                                 "episode": "E0026"}}
    claude_style = {"id": "2026-09-26T13:00:00Z-claude", "ts": "2026-09-26T13:00:00Z", "agent": "claude",
                    "task": "1", "kind": "done", "summary": "y", "commit": "def",
                    "evidence": {"tests": "t", "looked": "PDFs", "review": "passed", "measured": "m",
                                 "episode": "E0027"}}
    assert J.gaps(cursor_style) == [] and J.gaps(claude_style) == []
    assert J.gaps(dict(cursor_style, evidence={"commit": "abc", "images_looked_at": False})) == [
        "tests", "looked", "review", "measured", "episode"]


def test_a_later_claim_by_another_agent_takes_the_task_over(tmp_path):
    p = tmp_path / "j.json"
    J.log(agent="claude", kind="claim", summary="minds", task=12, at=T0, path=p, commit="x")
    J.log(agent="cursor", kind="claim", summary="taken over", task=12, at=T0 + timedelta(hours=1), path=p, commit="x")
    J.log(agent="claude", kind="claim", summary="taken back", task=12, at=T0 + timedelta(hours=8), path=p, commit="x")
    held = J.open_claims(J.load(p)["entries"], now=T0 + timedelta(hours=8, minutes=5))
    assert [(c["agent"], c["summary"]) for c in held] == [("claude", "taken back")]
