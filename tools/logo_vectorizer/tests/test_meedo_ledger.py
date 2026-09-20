"""Meedo-Me ledger: it must accumulate honestly and never flatter itself."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.logo_vectorizer.meedo_advisor import Run, Suggestion  # noqa: E402
from tools.logo_vectorizer.meedo_ledger import (  # noqa: E402
    case_knowledge, hit_rate, knowledge_report, load, observe, propose, trend,
)


def _row(run, pair, engine, composite, **kw):
    return {"run_id": run, "pair_id": pair, "engine": engine, "ok": True,
            "composite": composite, **kw}


def _runs(*scores):
    return [
        Run(f"r{i}", [_row(f"r{i}", "p", "vectorize", s)])
        for i, s in enumerate(scores)
    ]


def test_observe_records_the_latest_run_and_is_idempotent(tmp_path):
    led = tmp_path / "ledger.json"
    # observe() records the run that just finished, so history is built by
    # calling it once per run — which is how the improve loop uses it.
    observe(_runs(0.50), path=led)
    observe(_runs(0.50, 0.60), path=led)
    observe(_runs(0.50, 0.60), path=led)  # re-observing must not duplicate
    data = load(led)
    assert len(data["observations"]) == 2
    assert [o["run_id"] for o in data["observations"]] == ["r0", "r1"]


def test_observe_records_what_moved(tmp_path):
    led = tmp_path / "ledger.json"
    observe(_runs(0.50), path=led)
    observe(_runs(0.50, 0.62), path=led)
    latest = load(led)["observations"][-1]
    assert latest["moved"]["p::vectorize"] == 0.12


def test_a_proposal_is_judged_by_a_later_run_not_its_own(tmp_path):
    led = tmp_path / "ledger.json"
    observe(_runs(0.50), path=led)
    propose([Suggestion(1, "Fix p::vectorize", "because")], "r0", path=led)
    # The run that produced the advice cannot also judge it.
    assert load(led)["proposals"][0]["status"] == "open"
    observe(_runs(0.50, 0.70), path=led)
    p = load(led)["proposals"][0]
    assert p["status"] == "helped" and p["delta"] == 0.20


def test_a_proposal_that_changed_nothing_counts_against_the_rate(tmp_path):
    led = tmp_path / "ledger.json"
    observe(_runs(0.50), path=led)
    propose([Suggestion(2, "Fix p::vectorize", "because")], "r0", path=led)
    observe(_runs(0.50, 0.50), path=led)
    assert load(led)["proposals"][0]["status"] == "no_change"
    assert hit_rate(led)["overall"] == 0.0


def test_a_proposal_that_made_things_worse_is_recorded_as_hurt(tmp_path):
    led = tmp_path / "ledger.json"
    observe(_runs(0.60), path=led)
    propose([Suggestion(1, "Fix p::vectorize", "because")], "r0", path=led)
    observe(_runs(0.60, 0.40), path=led)
    assert load(led)["proposals"][0]["status"] == "hurt"


def test_suggestions_naming_no_case_are_not_logged(tmp_path):
    # Unmeasurable advice must not be able to inflate the hit rate.
    led = tmp_path / "ledger.json"
    n = propose([Suggestion(1, "Think harder about colour", "vibes")], "r0", path=led)
    assert n == 0 and load(led)["proposals"] == []


def test_never_moved_distinguishes_attacked_from_untouched(tmp_path):
    led = tmp_path / "ledger.json"
    for i in range(3):
        observe(_runs(*([0.44] * (i + 1))), path=led)
    k = case_knowledge(led)["p::vectorize"]
    assert k.never_moved
    assert "no recorded attempt has targeted it" in k.notes[0]

    propose([Suggestion(2, "Fix p::vectorize", "x")], "r0", path=led)
    observe(_runs(0.44, 0.44, 0.44, 0.44), path=led)
    k2 = case_knowledge(led)["p::vectorize"]
    assert "none moved it" in k2.notes[0]


def test_trend_reports_flat_when_the_engine_did_not_move(tmp_path):
    led = tmp_path / "ledger.json"
    for i in range(3):
        observe(_runs(*([0.7384] * (i + 1))), path=led)
    t = trend(led)
    assert t["flat"] is True and t["mean_composite_change"] == 0.0


def test_a_corrupt_ledger_starts_clean_rather_than_raising(tmp_path):
    led = tmp_path / "ledger.json"
    led.write_text("{ not json", encoding="utf-8")
    assert load(led)["observations"] == []
    observe(_runs(0.5), path=led)
    assert len(load(led)["observations"]) == 1


def test_ledger_never_invents_a_score(tmp_path):
    led = tmp_path / "ledger.json"
    observe(_runs(0.6180), path=led)
    obs = load(led)["observations"][0]
    assert obs["cases"]["p::vectorize"] == 0.618
    assert obs["mean_composite"] == 0.618
    # and the report surfaces only recorded values
    rep = knowledge_report(led)
    assert rep["cases_tracked"] == 1
