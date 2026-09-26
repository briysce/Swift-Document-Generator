"""Meedo-Me learns from Gemini and Claude by keeping every answer and judging it."""

from __future__ import annotations

import json

import pytest

from tools.logo_vectorizer import meedo_consult as C
from tools.logo_vectorizer import meedo_episodes as E
from tools.logo_vectorizer.meedo_merge import merge


def _ask(p, mind, role="critique", verdict="redo", session="s1", **kw):
    return C.record(mind=mind, model=f"{mind}-model", role=role, question="q", answer="a",
                    parsed={"verdict": verdict, "advice": f"{mind} method"}, case="swift", session=session,
                    path=p, **kw)


def test_a_helpful_answer_becomes_an_episode_meedo_can_recall(tmp_path):
    p = tmp_path / "meedo_consultations.json"
    c = _ask(p, "claude")
    got = C.judge(c["id"], "helped", "faults were real", by="claude-code", path=p)
    assert got["lesson"]
    ep = E.load(tmp_path / "meedo_episodes.json")[0]
    assert ep["method"] == "claude method" and ep["source"] == "claude" and "learned-from-claude" in ep["tags"]


def test_track_record_and_the_disagreements_that_teach_whom_to_trust(tmp_path):
    p = tmp_path / "meedo_consultations.json"
    a, b = _ask(p, "claude"), _ask(p, "gemini", verdict="ship")
    C.judge(a["id"], "helped", path=p)
    C.judge(b["id"], "wrong", path=p)
    C.record(mind="gemini", model="g", role="critique", question="q", answer="", error="HTTP 503", path=p)
    tr = C.track_record(path=p)
    assert tr["claude/critique"]["hit_rate"] == 1.0
    assert tr["gemini/critique"]["wrong"] == 1 and tr["gemini/critique"]["failed"] == 1
    assert C.disagreements(path=p) == [{"session": "s1", "role": "critique", "case": "swift",
                                        "results": {"claude": "helped", "gemini": "wrong"}}]


def test_meedo_in_shadow_is_scored_on_agreeing_with_the_answer_that_proved_right(tmp_path):
    p = tmp_path / "meedo_consultations.json"
    right, wrong = _ask(p, "claude", session="s1"), _ask(p, "gemini", verdict="ship", session="s2")
    C.judge(right["id"], "helped", path=p)
    C.judge(wrong["id"], "wrong", path=p)
    _ask(p, "meedo", verdict="redo", shadow_of=right["id"])     # agreed with the right answer
    _ask(p, "meedo", verdict="ship", shadow_of=wrong["id"])     # repeated the wrong one
    assert C.readiness(path=p)["critique"] == {"compared": 2, "agreed": 1, "rate": 0.5}


def test_judged_answers_export_as_a_dataset_and_the_app_can_ingest(tmp_path):
    p = tmp_path / "meedo_consultations.json"
    c = _ask(p, "claude")
    _ask(p, "gemini")  # unjudged: not in the dataset
    C.judge(c["id"], "helped", path=p)
    out = tmp_path / "d.jsonl"
    assert C.export(out, path=p) == 1 and json.loads(out.read_text())["outcome"] == "helped"
    app = tmp_path / "app.jsonl"
    app.write_text(json.dumps({"id": "x1", "mind": "gemini", "role": "order_ack", "question": "q", "answer": "a"}) + "\n")
    assert C.ingest(app, path=p) == 1 and C.ingest(app, path=p) == 0
    assert next(x for x in C.load(p) if x["id"] == "x1")["source"] == "app"
    with pytest.raises(ValueError):
        C.judge(c["id"], "great", path=p)


def test_both_branches_consultations_survive_and_a_judgement_wins(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    c = _ask(a, "claude")
    _ask(b, "gemini")
    b_items = C.load(b) + [dict(c, outcome={"result": "helped"})]
    b.write_text(json.dumps({"consultations": b_items}))
    assert merge(str(a), str(b))
    got = {x["mind"]: x for x in json.loads(a.read_text())["consultations"]}
    assert set(got) == {"claude", "gemini"} and got["claude"]["outcome"]["result"] == "helped"
