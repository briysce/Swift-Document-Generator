"""Meedo-Me advisor: it must read the record faithfully and never invent one."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.logo_vectorizer.meedo_advisor import (  # noqa: E402
    Run, analyse, diff_runs, guardrails, load_runs, recall_lessons,
    stuck_cases, suggest,
)


def _row(run, pair, engine, composite, **kw):
    return {"run_id": run, "pair_id": pair, "engine": engine, "ok": True,
            "composite": composite, **kw}


def test_diff_reports_regressions_and_improvements():
    a = Run("r1", [_row("r1", "p", "vectorize", 0.90), _row("r1", "q", "vectorize", 0.50)])
    b = Run("r2", [_row("r2", "p", "vectorize", 0.80), _row("r2", "q", "vectorize", 0.60)])
    regs, imps = diff_runs(a, b)
    assert [d.case for d in regs] == ["p::vectorize"]
    assert abs(regs[0].change + 0.10) < 1e-9
    assert [d.case for d in imps] == ["q::vectorize"]


def test_noise_below_the_floor_is_not_a_regression():
    a = Run("r1", [_row("r1", "p", "vectorize", 0.9000)])
    b = Run("r2", [_row("r2", "p", "vectorize", 0.8995)])
    regs, imps = diff_runs(a, b)
    assert regs == [] and imps == []


def test_stuck_needs_a_full_window_of_identical_scores():
    same = [Run(f"r{i}", [_row(f"r{i}", "p", "vectorize", 0.44)]) for i in range(3)]
    assert stuck_cases(same) == ["p::vectorize"]
    moved = same[:2] + [Run("r9", [_row("r9", "p", "vectorize", 0.45)])]
    assert stuck_cases(moved) == []
    # Too little history is not evidence of being stuck.
    assert stuck_cases(same[:2]) == []


def test_recall_prefers_a_lesson_that_names_the_case():
    lessons = [
        {"title": "Passing mention", "lesson": "something about blur_crush generally",
         "case_ids": [], "tags": []},
        {"title": "Names the case", "lesson": "arc specifics",
         "case_ids": ["arc__blur_crush"], "tags": []},
    ]
    hits = recall_lessons("arc__blur_crush::vectorize", lessons)
    assert hits and hits[0]["title"] == "Names the case"


def test_recall_drops_incidental_overlap():
    # A lesson that merely shares a common word must not be cited as evidence.
    lessons = [{"title": "Unrelated", "lesson": "the vectorize stage is fast",
                "case_ids": ["something_else"], "tags": []}]
    assert recall_lessons("arc__blur_crush::baseline", lessons) == []


def test_guardrails_collects_every_do_not_regress():
    lessons = [
        {"do_not_regress": "swift mean 0.9269"},
        {"do_not_regress": ""},
        {"title": "no clause"},
    ]
    assert guardrails(lessons) == ["swift mean 0.9269"]


def test_analyse_handles_an_empty_record():
    assert analyse([], [])["runs"] == 0


def test_suggestions_are_ranked_and_cite_evidence():
    a = Run("r1", [_row("r1", "p", "vectorize", 0.90)])
    b = Run("r2", [_row("r2", "p", "vectorize", 0.70, ink_iou=0.3,
                         palette_fidelity=0.01)])
    ideas = suggest(analyse([a, b], []), [])
    assert ideas, "a 0.20 drop must produce a suggestion"
    assert ideas[0].priority == 1, "a regression outranks everything"
    assert "p::vectorize" in ideas[0].headline
    assert ideas == sorted(ideas, key=lambda s: s.priority)


def test_load_runs_survives_a_corrupt_log(tmp_path):
    log = tmp_path / "improve_log.jsonl"
    log.write_text(
        json.dumps(_row("r1", "p", "vectorize", 0.5)) + "\n"
        + "{not json at all\n"
        + "\n"
        + json.dumps(_row("r1", "q", "vectorize", 0.6)) + "\n",
        encoding="utf-8",
    )
    runs = load_runs(log=log)
    assert len(runs) == 1 and len(runs[0].scored()) == 2


def test_load_runs_on_a_missing_log_is_empty_not_an_error(tmp_path):
    assert load_runs(log=tmp_path / "nope.jsonl") == []


def test_advisor_never_reports_a_score_the_log_did_not_contain():
    a = Run("r1", [_row("r1", "p", "vectorize", 0.6180)])
    report = analyse([a], [])
    assert report["mean_composite"] == 0.618
    assert report["worst_cases"][0]["composite"] == 0.618
