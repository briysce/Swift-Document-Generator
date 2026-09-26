"""Meedo-Me ledger: it must accumulate honestly and never flatter itself."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.logo_vectorizer.meedo_advisor import Run, Suggestion  # noqa: E402
from tools.logo_vectorizer.meedo_ledger import (  # noqa: E402
    case_knowledge, decide, hit_rate, knowledge_report, load, observe, propose,
    save, standup, trend,
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


def _accept(led, reason="doing it"):
    pid = load(led)["proposals"][0]["id"]
    decide(pid, "accept", reason, by="test", path=led)


def test_a_proposal_is_judged_by_a_later_run_than_its_acceptance(tmp_path):
    led = tmp_path / "ledger.json"
    observe(_runs(0.50), path=led)
    propose([Suggestion(1, "Fix p::vectorize", "because")], "r0", path=led)
    _accept(led)
    # The run it was accepted on cannot also judge it.
    assert load(led)["proposals"][0]["status"] == "accepted"
    observe(_runs(0.50, 0.70), path=led)
    p = load(led)["proposals"][0]
    assert p["status"] == "helped" and p["delta"] == 0.20


def test_advice_nobody_took_is_never_judged(tmp_path):
    """The bug behind Meedo-Me's 0%: 28 untaken proposals scored as failures."""
    led = tmp_path / "ledger.json"
    observe(_runs(0.50), path=led)
    propose([Suggestion(1, "Fix p::vectorize", "because")], "r0", path=led)
    observe(_runs(0.50, 0.50), path=led)
    observe(_runs(0.50, 0.50, 0.50), path=led)
    assert load(led)["proposals"][0]["status"] == "open"
    assert hit_rate(led)["judged"] == 0


def test_a_proposal_that_changed_nothing_counts_against_the_rate(tmp_path):
    led = tmp_path / "ledger.json"
    observe(_runs(0.50), path=led)
    propose([Suggestion(2, "Fix p::vectorize", "because")], "r0", path=led)
    _accept(led)
    observe(_runs(0.50, 0.50), path=led)
    assert load(led)["proposals"][0]["status"] == "no_change"
    assert hit_rate(led)["overall"] == 0.0


def test_a_proposal_that_made_things_worse_is_recorded_as_hurt(tmp_path):
    led = tmp_path / "ledger.json"
    observe(_runs(0.60), path=led)
    propose([Suggestion(1, "Fix p::vectorize", "because")], "r0", path=led)
    _accept(led)
    observe(_runs(0.60, 0.40), path=led)
    assert load(led)["proposals"][0]["status"] == "hurt"


def test_repeated_advice_escalates_instead_of_piling_up(tmp_path):
    led = tmp_path / "ledger.json"
    for i in range(3):
        observe(_runs(*([0.44] * (i + 1))), path=led)
        propose([Suggestion(3, f"p::vectorize has not moved and sits at 0.44{i}", "x")],
                f"r{i}", path=led)
    props = load(led)["proposals"]
    assert len(props) == 1, "the same advice must be one proposal, not three"
    assert props[0]["raised"] == 3 and props[0]["escalated"]
    assert standup(led)[0]["id"] == props[0]["id"]


def test_escalated_advice_leads_the_standup_over_higher_priority(tmp_path):
    led = tmp_path / "ledger.json"
    for i in range(3):
        propose([Suggestion(3, "Fix a::vectorize", "old")], f"r{i}", path=led)
    propose([Suggestion(1, "Fix b::vectorize", "new")], "r3", path=led)
    assert standup(led)[0]["case"] == "a::vectorize"


def test_a_decision_needs_a_reason_and_a_rejection_is_not_reopened(tmp_path):
    led = tmp_path / "ledger.json"
    propose([Suggestion(2, "Fix p::vectorize", "x")], "r0", path=led)
    pid = load(led)["proposals"][0]["id"]
    try:
        decide(pid, "reject", "   ", path=led)
        raise AssertionError("an empty reason must be refused")
    except ValueError:
        pass
    decide(pid, "reject", "fixed upstream by another change", path=led)
    propose([Suggestion(2, "Fix p::vectorize", "x")], "r1", path=led)
    props = load(led)["proposals"]
    assert len(props) == 1 and props[0]["status"] == "rejected"
    assert standup(led) == []


def test_old_ledgers_are_migrated_without_losing_what_was_said(tmp_path):
    led = tmp_path / "ledger.json"
    old = {"version": 1, "observations": [], "proposals": [
        {"run_id": f"r{i}", "headline": "t::vectorize: palette is gone", "case": "t::vectorize",
         "priority": 3, "rationale": "x", "status": "no_change", "delta": 0.0}
        for i in range(4)]}
    led.write_text(json.dumps(old))
    props = load(led)["proposals"]
    assert len(props) == 1
    assert props[0]["status"] == "open", "never accepted, so never really judged"
    assert props[0]["raised"] == 4 and props[0]["escalated"]


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

    # Advice that was only filed is not an attempt.
    propose([Suggestion(2, "Fix p::vectorize", "x")], "r0", path=led)
    observe(_runs(0.44, 0.44, 0.44, 0.44), path=led)
    assert "no recorded attempt has targeted it" in case_knowledge(led)["p::vectorize"].notes[0]

    # Advice someone acted on is.
    _accept(led)
    observe(_runs(0.44, 0.44, 0.44, 0.44, 0.44), path=led)
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


def test_meedo_stops_proposing_where_its_advice_keeps_being_declined(tmp_path):
    """Seven of eleven first-standup rejections were 'that engine is the control'."""
    led = tmp_path / "ledger.json"
    propose([Suggestion(2, "Fix a::baseline", "x"), Suggestion(2, "Fix b::baseline", "x")], "r0", path=led)
    for p in load(led)["proposals"]:
        decide(p["id"], "reject", "baseline is the control", path=led)
    n = propose([Suggestion(2, "Fix c::baseline", "x"), Suggestion(2, "Fix c::vectorize", "x")], "r1", path=led)
    assert n == 1, "the vectorize advice still goes through"
    assert load(led)["withheld_last"]["count"] == 1


def test_accepting_advice_on_an_engine_lifts_the_block(tmp_path):
    led = tmp_path / "ledger.json"
    propose([Suggestion(2, "Fix a::esrgan", "x"), Suggestion(2, "Fix b::esrgan", "x"),
             Suggestion(2, "Fix c::esrgan", "x")], "r0", path=led)
    ids = [p["id"] for p in load(led)["proposals"]]
    decide(ids[0], "reject", "raster", path=led)
    decide(ids[1], "reject", "raster", path=led)
    decide(ids[2], "accept", "changed my mind", path=led)
    assert propose([Suggestion(2, "Fix d::esrgan", "x")], "r1", path=led) == 1


def test_runs_can_be_rebuilt_from_the_ledger_when_the_log_is_gone(tmp_path):
    """Fresh clones have no improve_log.jsonl; propose must not go silent."""
    from tools.logo_vectorizer.meedo_ledger import _runs_from_ledger

    led = tmp_path / "ledger.json"
    observe(_runs(0.50), path=led)
    observe(_runs(0.50, 0.60), path=led)
    rebuilt = _runs_from_ledger(led)
    assert [r.run_id for r in rebuilt] == ["r0", "r1"]
    assert abs(rebuilt[-1].mean_composite() - 0.60) < 1e-9


def test_runs_are_only_compared_with_runs_configured_the_same(tmp_path):
    """A reconstruction run read as the shipping path became a "regression" to
    bisect: Swift solid import_combo 0.9465 (reconstruction) vs 0.9292."""
    from tools.logo_vectorizer.meedo_advisor import analyse

    def run(rid, v, idealize):
        return Run(rid, [{"run_id": rid, "pair_id": "swift_orange_solid__import_combo",
                          "engine": "vectorize", "ok": True, "composite": v,
                          "min_height": 1200, "engines": ["vectorize"], "idealize": idealize}])

    runs = [run("r0", 0.9292, False), run("r1", 0.9465, True), run("r2", 0.9292, False)]
    assert analyse(runs, lessons=[])["regressions"] == []
    led = tmp_path / "ledger.json"
    observe(runs, path=led)
    obs = load(led)["observations"][-1]
    assert obs["config"] == {"min_height": 1200, "engines": ["vectorize"], "idealize": False}
    assert obs["moved"] == {}

