"""Meedo-Me's episode memory: it keeps method, and gives it back when relevant."""

from __future__ import annotations

import numpy as np

from tools.logo_vectorizer.meedo_advisor import Run, Suggestion
from tools.logo_vectorizer.meedo_episodes import (
    close, load, playbook, recall, record, workstreams,
)
from tools.logo_vectorizer.meedo_ledger import decide, observe, propose
from tools.logo_vectorizer.meedo_ledger import load as load_ledger
from tools.logo_vectorizer.meedo_review import record as record_review
from tools.logo_vectorizer.meedo_review import review


def test_a_closed_episode_must_say_what_to_do_next_time(tmp_path):
    ep = tmp_path / "e.json"
    try:
        record(problem="colour vanished", outcome="success", path=ep)
        raise AssertionError("a success with no method is a result, not a lesson")
    except ValueError:
        pass
    record(problem="colour vanished", outcome="open", path=ep)
    assert load(ep)[0]["outcome"] == "open"


def test_an_open_episode_is_closed_with_its_method(tmp_path):
    ep = tmp_path / "e.json"
    e = record(problem="gcm red missing", outcome="open", path=ep)
    close(e["id"], outcome="success", method="pool colour bins before seeding layers", path=ep)
    assert load(ep)[0]["method"].startswith("pool colour bins")


def test_recall_brings_back_the_episode_about_the_same_case(tmp_path):
    ep = tmp_path / "e.json"
    record(problem="halo around letters", method="group by hue family", outcome="success",
           cases=["propak__import_combo::vectorize"], tags=["halo"], path=ep)
    record(problem="font matching slow", method="render once, keep a store", outcome="success",
           tags=["performance"], path=ep)
    hit = recall("propak__import_combo::vectorize is ringed in cyan", path=ep)
    assert hit and hit[0]["method"] == "group by hue family"


def test_understood_episodes_outrank_ones_only_seen(tmp_path):
    ep = tmp_path / "e.json"
    record(problem="red element dropped on gcm", outcome="open", tags=["colour"], path=ep)
    record(problem="red element dropped on gcm", method="review every output", outcome="success",
           tags=["colour"], path=ep)
    assert recall("red element dropped", path=ep)[0]["outcome"] == "success"


def test_judged_advice_writes_its_own_episode(tmp_path):
    led = tmp_path / "ledger.json"
    rows = lambda *s: [Run(f"r{i}", [{"run_id": f"r{i}", "pair_id": "p", "engine": "vectorize",
                                      "ok": True, "composite": v}]) for i, v in enumerate(s)]
    observe(rows(0.50), path=led)
    propose([Suggestion(1, "Fix p::vectorize", "because")], "r0", path=led)
    decide(load_ledger(led)["proposals"][0]["id"], "accept", "trying it", path=led)
    observe(rows(0.50, 0.70), path=led)
    eps = load(tmp_path / "meedo_episodes.json")
    assert len(eps) == 1 and eps[0]["outcome"] == "success" and eps[0]["source"] == "proposal"


def test_a_blocked_output_becomes_an_open_problem_once(tmp_path):
    led = tmp_path / "ledger.json"
    sketch = np.zeros((40, 80, 4), np.uint8)
    sketch[5:35, 5:35] = (20, 60, 120, 255)
    sketch[5:35, 45:75] = (190, 40, 45, 255)
    no_red = sketch.copy()
    no_red[5:35, 45:75] = 0
    rv = review(no_red, sketch)
    assert not rv.passed
    record_review(rv, case="c1", candidate="idealize", path=led)
    record_review(rv, case="c1", candidate="idealize", path=led)
    eps = load(tmp_path / "meedo_episodes.json")
    assert len(eps) == 1 and eps[0]["outcome"] == "open" and eps[0]["source"] == "review"


def test_workstreams_give_the_picture_across_lines_of_work(tmp_path):
    ep = tmp_path / "e.json"
    record(problem="a", method="m", outcome="success", workstream="logo-engine", path=ep)
    record(problem="b", method="m", outcome="failure", workstream="logo-engine", path=ep)
    record(problem="c", method="m", outcome="success", workstream="meedo-me", path=ep)
    ws = workstreams(ep)
    assert ws["logo-engine"]["total"] == 2 and ws["logo-engine"]["failure"] == 1
    assert ws["meedo-me"]["success"] == 1
    assert len(playbook(ep, workstream="meedo-me")) == 1


def test_a_retracted_episode_is_kept_but_never_taught(tmp_path):
    from tools.logo_vectorizer import meedo_episodes as E

    p = tmp_path / "e.json"
    ep = E.record(problem="arc tagline palette mush", method="recreate", outcome="success", cases=["arc"], path=p)
    assert E.recall("arc tagline", cases=["arc"], path=p)
    E.retract(ep["id"], "test fixture", path=p)
    assert E.recall("arc tagline", cases=["arc"], path=p) == []
    assert E.playbook(path=p) == []
    assert E.load(p)[0]["retracted"] == "test fixture"
