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


def test_ai_lessons_union_prefers_offline_progress(tmp_path):
    a = {
        "id": "AL1",
        "domain": "logo_restore",
        "problem": "arc tagline",
        "method": "sectional",
        "times_applied_offline": 0,
        "times_recalled": 1,
        "ts": "1",
    }
    b = {
        "id": "AL1",
        "domain": "logo_restore",
        "problem": "arc tagline",
        "method": "sectional color-split before min_area",
        "times_applied_offline": 3,
        "times_recalled": 5,
        "ts": "2",
    }
    ours = _w(
        tmp_path / "a",
        {
            "version": 1,
            "lessons": [a, {"id": "AL2", "problem": "ours only", "method": "m", "ts": "1"}],
        },
    )
    theirs = _w(
        tmp_path / "b",
        {
            "version": 1,
            "lessons": [b, {"id": "AL3", "problem": "theirs only", "method": "n", "ts": "2"}],
        },
    )
    assert merge(ours, theirs)
    lessons = {L["id"]: L for L in json.loads((tmp_path / "a").read_text())["lessons"]}
    assert set(lessons) == {"AL1", "AL2", "AL3"}
    assert lessons["AL1"]["times_applied_offline"] == 3
    assert "color-split" in lessons["AL1"]["method"]


def test_only_colliding_episodes_move_and_merging_back_duplicates_nothing(tmp_path):
    """Both agents wrote E0026-E0028 at once; the other side went on to E0044.
    Renumbering from one side's max cascaded through every later id, and the
    renumbered copies came back as strangers on the return merge."""
    ep = lambda i, who: {"id": f"E{i:04d}", "ts": f"t{i}{who}", "problem": f"{who} {i}", "outcome": "open"}
    ours = [ep(i, "claude") for i in (26, 27, 28)]
    theirs = [ep(i, "cursor") for i in range(26, 45)]
    a = _w(tmp_path / "a", {"episodes": ours})
    b = _w(tmp_path / "b", {"episodes": theirs})
    assert merge(a, b)
    got = json.loads((tmp_path / "a").read_text())["episodes"]
    moved = {e["renumbered_from"]: e["id"] for e in got if e.get("renumbered_from")}
    assert moved == {"E0026": "E0045", "E0027": "E0046", "E0028": "E0047"}
    assert {e["id"] for e in got if e["problem"] == "cursor 30"} == {"E0030"}
    back = _w(tmp_path / "c", {"episodes": theirs})
    assert merge(back, str(tmp_path / "a"))
    assert len(json.loads((tmp_path / "c").read_text())["episodes"]) == 22
