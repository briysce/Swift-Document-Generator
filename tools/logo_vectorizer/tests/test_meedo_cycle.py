"""Full-cycle standup returns ledger + journal + snapshot together."""

from __future__ import annotations

from tools.logo_vectorizer.meedo_cycle import collect, format_cycle, run


def test_cycle_collect_and_format():
    out = collect(with_ai=False)
    assert "proposals_awaiting" in out and "journal" in out and "report" in out
    assert "ai_collab" in out
    text = format_cycle(out)
    assert "Meedo-Me ledger standup" in text
    assert "Meedo-Me journal standup" in text
    assert "Refine Meedo-Me" in text


def test_cycle_cli_print(capsys):
    run(as_json=False, with_ai=False)
    assert "Refine Meedo-Me" in capsys.readouterr().out


def test_cycle_ai_enrich_with_doubles(monkeypatch):
    """Injected advise attaches to proposals without live APIs."""
    fake_waiting = [
        {
            "id": "P99",
            "priority": 1,
            "escalated": True,
            "headline": "Arc tagline stuck",
            "rationale": "glyph_match 0",
            "case": "arc__tagline",
            "raised": 2,
        }
    ]

    class _Adv:
        def to_dict(self):
            return {
                "domain": "meedo_advisor",
                "problem": "Arc tagline stuck",
                "diagnosis": "sectional color-split",
                "method": "split before min_area",
                "actions": ["sectional"],
                "risks": [],
                "providers_used": ["gemini", "claude"],
                "source": "gemini,claude",
                "lesson_id": "ALtest",
                "offline": False,
                "agree": True,
            }

    monkeypatch.setattr(
        "tools.logo_vectorizer.meedo_ledger.standup", lambda: fake_waiting
    )
    monkeypatch.setattr(
        "tools.logo_vectorizer.meedo_journal.standup",
        lambda: {
            "entry_count": 0,
            "recent_by_agent": {},
            "stale_claims": [],
            "done_missing_evidence": [],
        },
    )
    monkeypatch.setattr(
        "tools.logo_vectorizer.meedo_ledger.knowledge_report",
        lambda: {"observations": 0, "cases_tracked": 0, "trend": {}, "advice": {}},
    )
    monkeypatch.setattr(
        "tools.logo_vectorizer.meedo_episodes.workstreams", lambda: {}
    )
    monkeypatch.setattr(
        "tools.ai_collab.advisor.advise", lambda **kwargs: _Adv()
    )
    out = collect(with_ai=True)
    assert len(out["ai_collab"]) == 1
    assert out["ai_collab"][0]["proposal_id"] == "P99"
    text = format_cycle(out)
    assert "Gemini↔Claude" in text
    assert "sectional color-split" in text
