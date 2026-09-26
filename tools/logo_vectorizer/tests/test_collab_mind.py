"""collab_mind: stuck detection + Gemini↔Claude merge (no live API calls)."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.logo_vectorizer.ai_advisors.collab_mind import (  # noqa: E402
    CollabGuidance,
    collaborate,
    detect_stuck,
    escalate_if_stuck,
    _merge_plans,
    _sanitize_preprocess,
)
from tools.logo_vectorizer.ai_advisors.base import CritiqueResult  # noqa: E402


def test_detect_stuck_needs_real_failure():
    quiet = detect_stuck(score_total=0.92, passes_gates=True)
    assert not quiet.stuck

    gates = detect_stuck(passes_gates=False, alpha_iou=0.5, p_hollow=0.1)
    assert gates.stuck and gates.severity >= 0.6
    assert "failed_score_gates" in gates.reasons


def test_detect_stuck_lanczos_and_ai_reject():
    sig = detect_stuck(fell_to_lanczos=True, all_ai_rejected=True)
    assert sig.stuck and sig.severity >= 0.85


def test_detect_stuck_majority_critique_reject():
    critiques = [
        CritiqueResult(reject_candidate=True, missing_counters=True, provider="g"),
        CritiqueResult(reject_candidate=True, provider="c"),
        CritiqueResult(passes_qa=True, provider="o"),
    ]
    sig = detect_stuck(critiques=critiques)
    assert sig.stuck
    assert "majority_critique_reject" in sig.reasons


def test_sanitize_preprocess_floors():
    p = _sanitize_preprocess({"upscale": 2, "blur_radius": 3.0, "alpha_threshold": 90})
    assert p["upscale"] == 4
    assert p["blur_radius"] == 1.2
    assert p["alpha_threshold"] == 90


def test_merge_plans_claude_wins_on_disagreement():
    gemini = {
        "takeover": True,
        "preferred_path": "retry_ensemble",
        "recommended_backends": ["vtracer"],
        "actions": ["tighten blur"],
        "diagnosis": "gemini view",
        "notes": "g",
    }
    claude = {
        "takeover": True,
        "preferred_path": "recreate",
        "recommended_backends": ["inkscape"],
        "actions": ["strip bg"],
        "diagnosis": "claude view",
        "notes": "disagree — multi-color",
        "agree_with_gemini": False,
    }
    g = _merge_plans(gemini, claude)
    assert g.preferred_path == "recreate"
    assert g.agree is False
    assert "vtracer" in g.recommended_backends and "inkscape" in g.recommended_backends
    assert "tighten blur" in g.actions and "strip bg" in g.actions
    assert set(g.providers_used) == {"gemini", "claude"}


def test_merge_single_provider():
    g = _merge_plans({"preferred_path": "idealize", "takeover": True, "diagnosis": "letters"}, None)
    assert g.preferred_path == "idealize"
    assert g.providers_used == ["gemini"]


def test_collaborate_uses_injected_fns(tmp_path):
    img = Image.new("RGBA", (64, 32), (200, 40, 30, 255))
    signal = detect_stuck(fell_to_lanczos=True)

    def fake_gemini(_img, _ctx):
        return {
            "takeover": True,
            "preferred_path": "retry_ensemble",
            "recommended_backends": ["vtracer", "opencv-tree"],
            "recommended_preprocess": {"upscale": 6, "blur_radius": 0.8},
            "actions": ["retry with sharper mask"],
            "diagnosis": "holes collapsed",
            "notes": "try tree first",
        }

    def fake_claude(_img, _ctx, plan):
        assert plan is not None
        return {
            "takeover": True,
            "preferred_path": "retry_ensemble",
            "agree_with_gemini": True,
            "recommended_backends": ["inkscape"],
            "actions": ["keep evenodd"],
            "diagnosis": "agree — counters",
            "notes": "ok",
        }

    guidance = collaborate(
        img,
        signal,
        case_id="unit_test",
        gemini_fn=fake_gemini,
        claude_fn=fake_claude,
    )
    assert guidance is not None
    assert guidance.takeover
    assert guidance.preferred_path == "retry_ensemble"
    assert guidance.agree is True
    hints = guidance.to_source_hints()
    assert hints.recommended_preprocess.get("upscale") == 6


def test_escalate_if_stuck_journals(tmp_path, monkeypatch):
    journal = tmp_path / "meedo_journal.json"
    monkeypatch.setenv("LOGO_COLLAB_MIND", "1")

    img = Image.new("RGBA", (40, 20), (10, 10, 10, 255))

    def fake_gemini(_img, _ctx):
        return {
            "takeover": True,
            "preferred_path": "recreate",
            "diagnosis": "palette mush",
            "actions": ["recreate"],
        }

    def fake_claude(_img, _ctx, _plan):
        return None

    import tools.logo_vectorizer.meedo_journal as mj
    from tools.logo_vectorizer.ai_advisors import collab_mind as cm

    monkeypatch.setattr(mj, "JOURNAL", journal)
    monkeypatch.setattr(cm, "_gemini_plan", fake_gemini)
    monkeypatch.setattr(cm, "_claude_critique", fake_claude)

    g = escalate_if_stuck(
        img,
        case_id="arc__tagline",
        journal=True,
        fell_to_lanczos=True,
    )
    assert g is not None
    assert g.preferred_path == "recreate"
    assert journal.is_file()
    data = journal.read_text(encoding="utf-8")
    assert "collab_mind" in data
    assert "recreate" in data


def test_guidance_to_dict_roundtrip():
    g = CollabGuidance(
        takeover=True,
        preferred_path="idealize",
        diagnosis="drop tagline",
        providers_used=["gemini", "claude"],
    )
    d = g.to_dict()
    assert d["preferred_path"] == "idealize"
    assert d["takeover"] is True


def test_load_brand_ref_matches_slug_prefix():
    from tools.logo_vectorizer.ai_advisors.collab_mind import load_brand_ref

    ref = load_brand_ref("arc__tagline")
    if ref is None:
        # Catalog may be absent in sparse checkouts — skip, don't fail CI.
        return
    assert ref["slug"] == "arc"
    assert "RESOURCES" in str(ref.get("branding", {})).upper() or "tagline" in str(
        ref.get("branding", {})
    ).lower()
