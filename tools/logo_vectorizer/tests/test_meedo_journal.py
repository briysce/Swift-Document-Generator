"""Meedo-Me's work journal: every unit of work, and whether the house rules held."""

from __future__ import annotations

import json
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
    claims = J.open_claims(J.load(p), now=T0 + timedelta(minutes=50))
    assert [(c["agent"], c["task"], c["stale"]) for c in claims] == [("cursor", "7", True)]
    fresh = J.open_claims(J.load(p), now=T0 + timedelta(minutes=20))
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
    with pytest.raises(ValueError):
        J.log(agent="claude", kind="done", summary="x", path=p, commit="x", vibes="good")


def test_both_agents_histories_survive_a_merge(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    J.log(agent="claude", kind="claim", summary="x", at=T0, path=a, commit="x")
    J.log(agent="cursor", kind="claim", summary="y", at=T0, path=b, commit="x")  # same moment, other agent
    J.log(agent="claude", kind="claim", summary="x", at=T0, path=b, commit="x")  # already on both sides
    assert merge(str(a), str(b))
    got = json.loads(a.read_text())["entries"]
    assert sorted((e["agent"], e["summary"]) for e in got) == [("claude", "x"), ("cursor", "y")]
