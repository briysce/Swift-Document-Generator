"""Tests for Meedo unified observation + procedure playbook + study report."""

from __future__ import annotations

import json

from tools.ai_collab.observe import observe
from tools.ai_collab.procedures import (
    persist_procedure,
    recall_procedures,
    offline_ready_summary,
)
from tools.ai_collab.study import collect_study_report, format_study_report
from tools.ai_collab.improve_hook import enrich_summary


def test_persist_and_recall_procedure(tmp_path, monkeypatch):
    path = tmp_path / "meedo_procedures.json"
    monkeypatch.setattr("tools.ai_collab.procedures.DEFAULT_PATH", path)
    p = persist_procedure(
        face="serper",
        domain="logo_restore",
        title="Serper brand-ref crawl for arc",
        steps=[
            "POST serper images with ARC Resources Ltd logo",
            "Prefer official domain assets",
            "Record must_keep RESOURCES LTD. tagline",
        ],
        knobs={"queries": ["ARC Resources Ltd logo"]},
        do_not_regress=["RESOURCES LTD. teal tagline"],
        outcome="success",
        offline_ready=True,
        confidence=0.8,
        cases=["arc"],
        path=path,
    )
    assert p.id.startswith("P")
    hits = recall_procedures(
        "Serper brand-ref crawl arc RESOURCES tagline",
        domain="logo_restore",
        face="serper",
        path=path,
        min_score=2.0,
    )
    assert hits and hits[0]["id"] == p.id
    summary = offline_ready_summary(path)
    assert summary["offline_ready"] >= 1


def test_observe_fans_out_to_all_memory(tmp_path, monkeypatch):
    lessons = tmp_path / "meedo_ai_lessons.json"
    procs = tmp_path / "meedo_procedures.json"
    journal = tmp_path / "meedo_journal.json"
    episodes = tmp_path / "meedo_episodes.json"
    monkeypatch.setattr("tools.ai_collab.learn.DEFAULT_PATH", lessons)
    monkeypatch.setattr("tools.ai_collab.procedures.DEFAULT_PATH", procs)
    monkeypatch.setattr("tools.logo_vectorizer.meedo_journal.JOURNAL", journal)
    monkeypatch.setattr("tools.logo_vectorizer.meedo_episodes.EPISODES", episodes)

    result = observe(
        face="cursor",
        domain="meedo-me",
        tried="Wire unified observe so every face records into one memory",
        evidence="Audit showed receiving/BOL/Serper silent",
        method="observe() → journal + lessons + episodes + procedures",
        outcome="success",
        steps=[
            "Call observe from face hooks",
            "Fail-open when memory write fails",
            "Prefer one MCP brain",
        ],
        do_not_regress=["never commit API keys", "Shipping SO/Contact lock"],
        tags=["observe", "board3"],
        task=3,
        agent="cursor",
    )
    assert result.journal_id
    assert result.lesson_id
    assert result.episode_id
    assert result.procedure_id
    assert not result.errors

    assert json.loads(lessons.read_text())["lessons"]
    assert json.loads(procs.read_text())["procedures"]
    assert json.loads(journal.read_text())["entries"]
    assert json.loads(episodes.read_text())["episodes"]


def test_improve_hook_records_procedure_even_when_ai_off(tmp_path, monkeypatch):
    procs = tmp_path / "meedo_procedures.json"
    monkeypatch.setattr("tools.ai_collab.procedures.DEFAULT_PATH", procs)
    monkeypatch.setenv("IMPROVE_AI_COLLAB", "0")
    summary = {
        "mean_composite": 0.91,
        "run_id": "test-run",
        "n_scored": 4,
        "top_failures": [{"case_id": "long_so", "composite": 0.7}],
        "gate_fails": [],
        "do_not_touch_shipping_lock": {
            "after_pill_gap": 11.0,
            "so_show_rule": False,
            "contact_label_to_value": 3.0,
        },
    }
    enrich_summary(summary, domain="shipping_pdf")
    data = json.loads(procs.read_text())
    assert data["procedures"]
    assert any("afterPillGap" in " ".join(p.get("steps") or []) for p in data["procedures"])


def test_study_report_lists_cannot_yet_own(tmp_path, monkeypatch):
    lessons = tmp_path / "meedo_ai_lessons.json"
    procs = tmp_path / "meedo_procedures.json"
    journal = tmp_path / "meedo_journal.json"
    episodes = tmp_path / "meedo_episodes.json"
    monkeypatch.setattr("tools.ai_collab.learn.DEFAULT_PATH", lessons)
    monkeypatch.setattr("tools.ai_collab.procedures.DEFAULT_PATH", procs)
    monkeypatch.setattr("tools.logo_vectorizer.meedo_journal.JOURNAL", journal)
    monkeypatch.setattr("tools.logo_vectorizer.meedo_episodes.EPISODES", episodes)

    persist_procedure(
        face="gemini",
        domain="logo_restore",
        title="Live-only Gemini plan without offline proof",
        steps=["ask gemini"],
        outcome="open",
        offline_ready=False,
        confidence=0.2,
        providers=["gemini"],
        path=procs,
    )
    from tools.ai_collab.learn import persist_lesson

    persist_lesson(
        domain="logo_restore",
        problem="live lesson never applied offline",
        diagnosis="needs more runs",
        method="sectional split",
        providers=["gemini", "claude"],
        mirror_episode=False,
        path=lessons,
    )

    report = collect_study_report()
    assert report["cannot_yet_own"]
    text = format_study_report(report)
    assert "cannot yet own" in text.lower() or "live-dependent" in text.lower()
    assert "confidence" in report


def test_journal_done_auto_episode_when_method_present(tmp_path, monkeypatch):
    journal = tmp_path / "meedo_journal.json"
    episodes = tmp_path / "meedo_episodes.json"
    monkeypatch.setattr("tools.logo_vectorizer.meedo_journal.JOURNAL", journal)
    monkeypatch.setattr("tools.logo_vectorizer.meedo_episodes.EPISODES", episodes)

    from tools.logo_vectorizer.meedo_journal import log

    entry = log(
        agent="cursor",
        kind="done",
        summary="Unified Meedo observe layer landed",
        task=3,
        tests="pytest observe+procedures",
        images_looked_at=True,
        evidence={"method": "observe fans out to journal/lessons/episodes/procedures"},
        source="test",
    )
    assert entry["evidence"].get("episode_id")
    eps = json.loads(episodes.read_text())["episodes"]
    assert any(e.get("method") for e in eps)
