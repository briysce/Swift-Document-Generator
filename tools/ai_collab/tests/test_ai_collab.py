"""Shared Gemini↔Claude client + Meedo learning (no live API calls)."""

from __future__ import annotations

import json
from pathlib import Path

from tools.ai_collab.advisor import Advice, advise, advise_top_failures
from tools.ai_collab.deliberate import deliberate, merge_plans
from tools.ai_collab.learn import (
    Lesson,
    mark_applied_offline,
    persist_from_deliberation,
    persist_lesson,
    recall_lessons,
)
from tools.ai_collab.improve_hook import enrich_summary


def test_merge_plans_claude_wins_on_disagreement():
    g = {
        "diagnosis": "gemini says tighten blur",
        "priority": 2,
        "actions": ["blur 0.8"],
        "method": "tighten preprocess",
        "notes": "g",
    }
    c = {
        "diagnosis": "claude says sectional",
        "priority": 1,
        "actions": ["split regions"],
        "method": "sectional color-split before filter",
        "notes": "disagree",
        "agree_with_gemini": False,
    }
    d = merge_plans(g, c, domain="logo_restore")
    assert d is not None
    assert d.diagnosis == "claude says sectional"
    assert d.priority == 1
    assert "blur 0.8" in d.actions and "split regions" in d.actions
    assert set(d.providers_used) == {"gemini", "claude"}
    assert d.agree is False


def test_deliberate_with_injected_fns():
    def g_fn(_prompt: str):
        return {
            "diagnosis": "plateau on arc tagline",
            "priority": 1,
            "actions": ["sectional"],
            "method": "color-split tagline band",
            "notes": "g",
        }

    def c_fn(_prompt: str):
        return {
            "diagnosis": "plateau on arc tagline",
            "priority": 1,
            "actions": ["drop min_area"],
            "method": "color-split tagline band; min_area=8",
            "agree_with_gemini": True,
            "notes": "agree + tighten",
        }

    d = deliberate(
        domain="logo_restore",
        context={"case": "arc__tagline"},
        gemini_fn=g_fn,
        claude_fn=c_fn,
    )
    assert d is not None
    assert "sectional" in d.actions
    assert "drop min_area" in d.actions
    assert d.agree is True


def test_persist_and_recall_offline(tmp_path, monkeypatch):
    path = tmp_path / "meedo_ai_lessons.json"
    monkeypatch.setattr("tools.ai_collab.learn.DEFAULT_PATH", path)
    # Avoid writing real episodes during unit test.
    monkeypatch.setattr(
        "tools.ai_collab.learn.persist_lesson.__defaults__",
        None,
    )
    lesson = persist_lesson(
        domain="meedo_advisor",
        problem="Arc tagline letters dropped after sectional",
        diagnosis="navy channel merge",
        method="sectional color-split before min_area filter",
        actions=["split tagline band", "min_area=8"],
        providers=["gemini", "claude"],
        cases=["arc__tagline"],
        tags=["logo_restore"],
        mirror_episode=False,
        path=path,
    )
    assert lesson.id.startswith("AL")
    hits = recall_lessons(
        "Arc tagline letters dropped after sectional color merge",
        domain="meedo_advisor",
        cases=["arc__tagline"],
        path=path,
        min_score=3.0,
    )
    assert hits and hits[0]["id"] == lesson.id
    mark_applied_offline(lesson.id, path=path)
    again = recall_lessons(
        "Arc tagline letters dropped",
        domain="meedo_advisor",
        cases=["arc__tagline"],
        path=path,
        min_score=3.0,
    )
    assert again[0]["times_applied_offline"] >= 1


def test_advise_prefers_recall_over_live(tmp_path, monkeypatch):
    path = tmp_path / "meedo_ai_lessons.json"
    monkeypatch.setattr("tools.ai_collab.learn.DEFAULT_PATH", path)
    monkeypatch.setenv("MEEDO_AI_RECALL_MIN", "3.0")
    persist_lesson(
        domain="app_ux",
        problem="history wipe on open after cold start",
        diagnosis="prune-on-open regression",
        method="skip prune when snapshot present; retain last N",
        actions=["guard prune_history_without_snapshots"],
        providers=["gemini", "claude"],
        cases=["history_open_no_prune"],
        mirror_episode=False,
        path=path,
    )

    called = {"g": 0, "c": 0}

    def g_fn(_p):
        called["g"] += 1
        return {"diagnosis": "should not run", "actions": [], "method": "x"}

    def c_fn(_p):
        called["c"] += 1
        return {"diagnosis": "should not run", "actions": [], "method": "x"}

    advice = advise(
        domain="app_ux",
        problem="history wipe on open after cold start",
        cases=["history_open_no_prune"],
        gemini_fn=g_fn,
        claude_fn=c_fn,
        journal=False,
        persist=False,
    )
    assert advice is not None
    assert advice.offline is True
    assert advice.source == "recalled"
    assert called["g"] == 0 and called["c"] == 0


def test_advise_live_persists_lesson(tmp_path, monkeypatch):
    path = tmp_path / "meedo_ai_lessons.json"
    monkeypatch.setattr("tools.ai_collab.learn.DEFAULT_PATH", path)
    monkeypatch.setenv("MEEDO_AI_RECALL_MIN", "99")  # force live
    monkeypatch.setenv("MEEDO_AI_MIRROR_EPISODES", "0")

    def g_fn(_p):
        return {
            "diagnosis": "SO overflow on long PO",
            "priority": 2,
            "actions": ["shrink-to-fit PO"],
            "method": "wrap long PO; never clip",
            "notes": "g",
        }

    def c_fn(_p):
        return {
            "diagnosis": "SO overflow on long PO",
            "priority": 2,
            "actions": ["keep afterPillGap 11.0"],
            "method": "wrap long PO; never clip; preserve SO/Contact lock",
            "agree_with_gemini": True,
            "notes": "lock safe",
        }

    advice = advise(
        domain="shipping_pdf",
        problem="long PO overflows shipping value box",
        cases=["long_po"],
        gemini_fn=g_fn,
        claude_fn=c_fn,
        journal=False,
        persist=True,
    )
    assert advice is not None and not advice.offline
    assert "gemini" in advice.source
    assert advice.lesson_id
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert any(L["id"] == advice.lesson_id for L in stored["lessons"])


def test_advise_top_failures_and_improve_hook(tmp_path, monkeypatch):
    path = tmp_path / "meedo_ai_lessons.json"
    monkeypatch.setattr("tools.ai_collab.learn.DEFAULT_PATH", path)
    monkeypatch.setenv("MEEDO_AI_RECALL_MIN", "99")
    monkeypatch.setenv("MEEDO_AI_MIRROR_EPISODES", "0")

    def g_fn(_p):
        return {
            "diagnosis": "generate empty pdf",
            "actions": ["check PdfDocument bytes"],
            "method": "assert non-empty PDF bytes before history write",
        }

    def c_fn(_p):
        return {
            "diagnosis": "generate empty pdf",
            "actions": ["retain previous bytes on fail"],
            "method": "assert non-empty PDF bytes before history write",
            "agree_with_gemini": True,
        }

    items = advise_top_failures(
        domain="app_ux",
        top_failures=[{"case_id": "shipping_generate", "composite": 0.0, "reason": "empty"}],
        gemini_fn=g_fn,
        claude_fn=c_fn,
        journal=False,
    )
    assert len(items) == 1
    assert items[0].domain == "app_ux"

    summary = {
        "mean_composite": 0.5,
        "top_failures": [{"case_id": "shipping_generate", "composite": 0.0}],
    }

    def _fake_advise(**kwargs):
        return items

    monkeypatch.setattr(
        "tools.ai_collab.advisor.advise_top_failures",
        lambda **kwargs: items,
    )
    enrich_summary(summary, domain="app_ux", max_items=1)
    assert summary.get("ai_collab") and summary["ai_collab"][0]["domain"] == "app_ux"

    monkeypatch.setenv("IMPROVE_AI_COLLAB", "0")
    summary2 = {"top_failures": [{"case_id": "x", "composite": 0}]}
    enrich_summary(summary2, domain="app_ux")
    assert summary2["ai_collab"] == []


def test_fail_open_when_both_dark():
    assert (
        deliberate(
            domain="general",
            context={"x": 1},
            gemini_fn=lambda _p: None,
            claude_fn=lambda _p: None,
        )
        is None
    )
    assert (
        advise(
            domain="general",
            problem="nothing",
            gemini_fn=lambda _p: None,
            claude_fn=lambda _p: None,
            journal=False,
            force_live=True,
        )
        is None
    )
