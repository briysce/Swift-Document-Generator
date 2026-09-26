"""Two agents, two branches, one memory: nothing either learned may be lost."""

from __future__ import annotations

import json

from tools.logo_vectorizer.meedo_merge import merge


def _w(p, data):
    p.write_text(json.dumps(data))
    return str(p)


def test_both_agents_episodes_survive_even_with_the_same_id(tmp_path):
    base = [{"id": "E0025", "ts": "1", "problem": "old", "outcome": "success", "method": "m"}]
    ours = _w(tmp_path / "a", {"version": 1, "episodes": base + [{"id": "E0026", "ts": "2", "problem": "claude's", "outcome": "open"}]})
    theirs = _w(tmp_path / "b", {"version": 1, "episodes": base + [{"id": "E0026", "ts": "3", "problem": "cursor's", "outcome": "open"}]})
    assert merge(ours, theirs)
    eps = json.loads((tmp_path / "a").read_text())["episodes"]
    assert [e["problem"] for e in eps] == ["old", "claude's", "cursor's"]
    assert len({e["id"] for e in eps}) == 3


def test_a_decision_on_either_side_is_kept(tmp_path):
    p = {"id": "p1", "status": "open", "headline": "h"}
    ours = _w(tmp_path / "a", {"observations": [{"run_id": "r1"}], "proposals": [p], "reviews": []})
    theirs = _w(tmp_path / "b", {"observations": [{"run_id": "r1"}, {"run_id": "r2"}],
                                 "proposals": [dict(p, status="rejected", decided_at="t")],
                                 "reviews": [{"ts": "t", "case": "c", "candidate": "x", "passed": True}]})
    assert merge(ours, theirs)
    led = json.loads((tmp_path / "a").read_text())
    assert [o["run_id"] for o in led["observations"]] == ["r1", "r2"]
    assert led["proposals"][0]["status"] == "rejected"
    assert len(led["reviews"]) == 1


def test_a_closed_episode_wins_over_its_open_copy(tmp_path):
    e = {"id": "E0030", "ts": "1", "problem": "p", "outcome": "open"}
    ours = _w(tmp_path / "a", {"episodes": [e]})
    theirs = _w(tmp_path / "b", {"episodes": [dict(e, outcome="success", method="do x")]})
    merge(ours, theirs)
    got = json.loads((tmp_path / "a").read_text())["episodes"]
    assert len(got) == 1 and got[0]["method"] == "do x"


def test_both_agents_journal_entries_survive(tmp_path):
    ours = _w(tmp_path / "a", {"version": 1, "entries": [
        {"id": "Ja", "ts": "1", "agent": "claude", "kind": "claim", "summary": "swift"},
    ]})
    theirs = _w(tmp_path / "b", {"version": 1, "entries": [
        {"id": "Jb", "ts": "2", "agent": "cursor", "kind": "finding", "summary": "propak"},
    ]})
    assert merge(ours, theirs)
    ids = {e["id"] for e in json.loads((tmp_path / "a").read_text())["entries"]}
    assert ids == {"Ja", "Jb"}
