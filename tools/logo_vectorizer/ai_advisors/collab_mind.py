"""Collaborative Gemini ↔ Claude mind — escalate when the local engine is stuck.

The ensemble / idealize / recreate paths are tools. When they plateau, fail
gates, or AI critiques reject every candidate, Gemini and Claude deliberate:

1. Gemini diagnoses the source + failure metrics and proposes a plan.
2. Claude critiques that plan (or proposes independently if Gemini is dark).
3. We merge into actionable ``CollabGuidance`` the engine can apply on the fly
   (preprocess / backends / path switch) — fail-open if either API is down.

Keys come from gitignored ``.env`` via ``env_loader``; never hardcode secrets.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from PIL import Image

from .base import (
    CritiqueResult,
    SourceHints,
    encode_png,
    env_key,
    extract_json,
    http_post_json,
)
from . import claude as claude_mod
from . import gemini as gemini_mod

# Paths the engine understands when a takeover is requested.
PATHS = ("ensemble", "retry_ensemble", "idealize", "recreate", "sectional", "keep")

GEMINI_PLAN_PROMPT = """You are an active co-pilot for a logo restore/vectorize engine.
The local tools (vtracer, Inkscape, OpenCV, potrace, sectional Bezier, idealize,
customer recreate) are stuck or unable to improve. Diagnose and propose a plan.

Stuck context (JSON):
{context_json}

Return JSON only:
{{
  "diagnosis": "what failed and why (1-3 sentences)",
  "takeover": true,
  "preferred_path": "retry_ensemble|idealize|recreate|sectional|keep",
  "recommended_backends": ["vtracer", "inkscape", "opencv-tree"],
  "recommended_preprocess": {{"upscale": 4, "blur_radius": 1.0, "alpha_threshold": 80, "min_area": 500}},
  "brand_name": "",
  "font_family_guess": "",
  "dominant_colors_hex": [],
  "actions": ["concrete step 1", "concrete step 2"],
  "notes": "anything the other model should check"
}}

Rules:
- Prefer retry_ensemble with tighter preprocess when counters/holes are the issue.
- Prefer recreate for multi-color customer logos with bad bg strip / palette.
- Prefer idealize when letters are dropping but ink islands exist in the prep.
- Prefer sectional for flat multi-color lockups with clear regions.
- Never invent brand colors that are not visible in the image.
- Keep preprocess floors: upscale >= 4, blur_radius <= 1.2.
"""

CLAUDE_CRITIQUE_PROMPT = """You are Claude collaborating with Gemini on a stuck logo restore.
Image = source logo. Gemini's plan (JSON) follows. Critique and refine it.

Gemini plan:
{gemini_plan}

Stuck context:
{context_json}

Return JSON only (same schema), with your verdict:
{{
  "diagnosis": "...",
  "takeover": true,
  "preferred_path": "retry_ensemble|idealize|recreate|sectional|keep",
  "recommended_backends": ["vtracer", "inkscape", "opencv-tree"],
  "recommended_preprocess": {{"upscale": 4, "blur_radius": 1.0, "alpha_threshold": 80, "min_area": 500}},
  "brand_name": "",
  "font_family_guess": "",
  "dominant_colors_hex": [],
  "actions": ["..."],
  "notes": "agree / disagree with Gemini and why",
  "agree_with_gemini": true,
  "overrides": ["fields you changed and why"]
}}
"""


@dataclass
class StuckSignal:
    stuck: bool
    reasons: list[str] = field(default_factory=list)
    severity: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stuck": self.stuck,
            "reasons": list(self.reasons),
            "severity": self.severity,
            "metrics": dict(self.metrics),
        }


@dataclass
class CollabGuidance:
    takeover: bool = False
    preferred_path: str = "keep"
    recommended_backends: list[str] = field(default_factory=list)
    recommended_preprocess: dict[str, float | int] = field(default_factory=dict)
    brand_name: str = ""
    font_family_guess: str = ""
    dominant_colors_hex: list[str] = field(default_factory=list)
    diagnosis: str = ""
    actions: list[str] = field(default_factory=list)
    gemini_notes: str = ""
    claude_notes: str = ""
    providers_used: list[str] = field(default_factory=list)
    agree: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "takeover": self.takeover,
            "preferred_path": self.preferred_path,
            "recommended_backends": list(self.recommended_backends),
            "recommended_preprocess": dict(self.recommended_preprocess),
            "brand_name": self.brand_name,
            "font_family_guess": self.font_family_guess,
            "dominant_colors_hex": list(self.dominant_colors_hex),
            "diagnosis": self.diagnosis,
            "actions": list(self.actions),
            "gemini_notes": self.gemini_notes,
            "claude_notes": self.claude_notes,
            "providers_used": list(self.providers_used),
            "agree": self.agree,
        }

    def to_source_hints(self) -> SourceHints:
        return SourceHints(
            has_holes=True,
            recommended_backends=list(self.recommended_backends) or ["vtracer", "inkscape"],
            recommended_preprocess=dict(self.recommended_preprocess),
            notes=self.diagnosis or " | ".join(self.actions),
            brand_name=self.brand_name,
            font_family_guess=self.font_family_guess,
            dominant_colors_hex=list(self.dominant_colors_hex),
            layout_summary=self.preferred_path,
            provider=",".join(self.providers_used) or "collab_mind",
            raw=self.to_dict(),
        )


def available() -> bool:
    return bool(env_key("GOOGLE_API_KEY", "GEMINI_API_KEY") or env_key("ANTHROPIC_API_KEY"))


def collab_enabled() -> bool:
    """On by default when keys exist; disable with LOGO_COLLAB_MIND=0."""
    flag = os.environ.get("LOGO_COLLAB_MIND", "").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    if flag in {"1", "true", "yes", "on"}:
        return True
    return available()


def detect_stuck(
    *,
    score_total: float | None = None,
    alpha_iou: float | None = None,
    p_hollow: float | None = None,
    hole_count: int | None = None,
    passes_gates: bool | None = None,
    critiques: list[CritiqueResult] | None = None,
    candidates_tried: int = 0,
    all_ai_rejected: bool = False,
    fell_to_lanczos: bool = False,
    review_blocked: bool = False,
    plateau_composite: float | None = None,
) -> StuckSignal:
    """Heuristic: local engine cannot improve / is failing identity or QA."""
    reasons: list[str] = []
    severity = 0.0
    metrics: dict[str, Any] = {
        "score_total": score_total,
        "alpha_iou": alpha_iou,
        "p_hollow": p_hollow,
        "hole_count": hole_count,
        "passes_gates": passes_gates,
        "candidates_tried": candidates_tried,
        "all_ai_rejected": all_ai_rejected,
        "fell_to_lanczos": fell_to_lanczos,
        "review_blocked": review_blocked,
        "plateau_composite": plateau_composite,
    }

    if passes_gates is False:
        reasons.append("failed_score_gates")
        severity = max(severity, 0.7)
    if alpha_iou is not None and alpha_iou < 0.82:
        reasons.append(f"low_alpha_iou={alpha_iou:.3f}")
        severity = max(severity, 0.65)
    if p_hollow is not None and p_hollow < 0.35 and (hole_count or 0) < 2:
        reasons.append(f"missing_counters p_hollow={p_hollow:.3f}")
        severity = max(severity, 0.75)
    if score_total is not None and score_total < 0.55:
        reasons.append(f"low_score={score_total:.3f}")
        severity = max(severity, 0.8)
    if all_ai_rejected:
        reasons.append("all_ai_critiques_rejected")
        severity = max(severity, 0.85)
    if fell_to_lanczos:
        reasons.append("vectorize_fell_to_lanczos")
        severity = max(severity, 0.9)
    if review_blocked:
        reasons.append("meedo_review_blocked")
        severity = max(severity, 0.7)
    if plateau_composite is not None and plateau_composite < 0.85:
        reasons.append(f"plateau_composite={plateau_composite:.4f}")
        severity = max(severity, 0.6)
    if critiques:
        hard = [c for c in critiques if c.reject_candidate or c.missing_counters]
        if hard and len(hard) >= max(1, len(critiques) // 2):
            reasons.append("majority_critique_reject")
            severity = max(severity, 0.8)

    # Need a real failure signal — not "stuck" just because AI is quiet.
    stuck = bool(reasons) and severity >= 0.6
    return StuckSignal(stuck=stuck, reasons=reasons, severity=severity, metrics=metrics)


def _sanitize_preprocess(raw: dict[str, Any] | None) -> dict[str, float | int]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float | int] = {}
    try:
        if "upscale" in raw:
            out["upscale"] = max(int(raw["upscale"]), 4)
        if "blur_radius" in raw:
            out["blur_radius"] = min(float(raw["blur_radius"]), 1.2)
        if "alpha_threshold" in raw:
            out["alpha_threshold"] = int(raw["alpha_threshold"])
        if "min_area" in raw:
            out["min_area"] = float(raw["min_area"])
    except (TypeError, ValueError):
        return {}
    return out


def _guidance_from_dict(
    data: dict[str, Any],
    *,
    providers: list[str],
    gemini_notes: str = "",
    claude_notes: str = "",
    agree: bool | None = None,
    raw: dict[str, Any] | None = None,
) -> CollabGuidance:
    path = str(data.get("preferred_path") or "keep").strip().lower()
    if path not in PATHS:
        path = "keep"
    backends = data.get("recommended_backends") or []
    if not isinstance(backends, list):
        backends = []
    actions = data.get("actions") or []
    if not isinstance(actions, list):
        actions = []
    colors = data.get("dominant_colors_hex") or []
    if not isinstance(colors, list):
        colors = []
    return CollabGuidance(
        takeover=bool(data.get("takeover", True)),
        preferred_path=path,
        recommended_backends=[str(b) for b in backends],
        recommended_preprocess=_sanitize_preprocess(data.get("recommended_preprocess")),
        brand_name=str(data.get("brand_name") or ""),
        font_family_guess=str(data.get("font_family_guess") or ""),
        dominant_colors_hex=[str(c) for c in colors if str(c).strip()],
        diagnosis=str(data.get("diagnosis") or ""),
        actions=[str(a) for a in actions],
        gemini_notes=gemini_notes or str(data.get("notes") or ""),
        claude_notes=claude_notes,
        providers_used=providers,
        agree=agree,
        raw=raw or data,
    )


def _merge_plans(
    gemini: dict[str, Any] | None,
    claude: dict[str, Any] | None,
) -> CollabGuidance:
    """Claude wins on path/takeover when they disagree; union backends/actions."""
    providers: list[str] = []
    if gemini:
        providers.append("gemini")
    if claude:
        providers.append("claude")

    if not gemini and not claude:
        return CollabGuidance(takeover=False, preferred_path="keep", providers_used=[])

    if gemini and not claude:
        return _guidance_from_dict(
            gemini,
            providers=providers,
            gemini_notes=str(gemini.get("notes") or ""),
            agree=None,
            raw={"gemini": gemini},
        )
    if claude and not gemini:
        return _guidance_from_dict(
            claude,
            providers=providers,
            claude_notes=str(claude.get("notes") or ""),
            agree=None,
            raw={"claude": claude},
        )

    assert gemini is not None and claude is not None
    agree = claude.get("agree_with_gemini")
    if agree is None:
        agree = (
            str(gemini.get("preferred_path", "")).lower()
            == str(claude.get("preferred_path", "")).lower()
        )
    # Prefer Claude's path when disagree; union the rest.
    base = dict(gemini)
    base.update({k: v for k, v in claude.items() if v not in (None, "", [], {})})
    # Explicit path / takeover from Claude on disagreement.
    if not agree:
        base["preferred_path"] = claude.get("preferred_path") or gemini.get("preferred_path")
        base["takeover"] = bool(claude.get("takeover", gemini.get("takeover", True)))
    backends = list(
        dict.fromkeys(
            list(gemini.get("recommended_backends") or [])
            + list(claude.get("recommended_backends") or [])
        )
    )
    base["recommended_backends"] = backends
    actions = list(
        dict.fromkeys(
            [str(a) for a in (gemini.get("actions") or [])]
            + [str(a) for a in (claude.get("actions") or [])]
        )
    )
    base["actions"] = actions
    colors = list(
        dict.fromkeys(
            list(gemini.get("dominant_colors_hex") or [])
            + list(claude.get("dominant_colors_hex") or [])
        )
    )
    base["dominant_colors_hex"] = colors
    g_notes = str(gemini.get("notes") or "")
    c_notes = str(claude.get("notes") or "")
    base["diagnosis"] = str(claude.get("diagnosis") or gemini.get("diagnosis") or "")
    return _guidance_from_dict(
        base,
        providers=providers,
        gemini_notes=g_notes,
        claude_notes=c_notes,
        agree=bool(agree),
        raw={"gemini": gemini, "claude": claude},
    )


def _gemini_plan(img: Image.Image, context: dict[str, Any]) -> dict[str, Any] | None:
    if not gemini_mod.available():
        return None
    try:
        b64, mime = encode_png(img)
        prompt = GEMINI_PLAN_PROMPT.format(
            context_json=json.dumps(context, indent=2, default=str)[:4000]
        )
        text = gemini_mod._call_gemini(
            [
                {"inline_data": {"mime_type": mime, "data": b64}},
                {"text": prompt},
            ]
        )
        return extract_json(text)
    except Exception as exc:  # noqa: BLE001 — fail-open
        print(f"[collab_mind] gemini plan failed: {exc}", file=sys.stderr)
        return None


def _claude_critique(
    img: Image.Image,
    context: dict[str, Any],
    gemini_plan: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not claude_mod.available():
        return None
    try:
        b64, mime = encode_png(img)
        prompt = CLAUDE_CRITIQUE_PROMPT.format(
            gemini_plan=json.dumps(gemini_plan or {"status": "gemini_unavailable"}, indent=2)[:3500],
            context_json=json.dumps(context, indent=2, default=str)[:2500],
        )
        text = claude_mod._call_claude(
            [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime, "data": b64},
                },
                {"type": "text", "text": prompt},
            ]
        )
        return extract_json(text)
    except Exception as exc:  # noqa: BLE001
        print(f"[collab_mind] claude critique failed: {exc}", file=sys.stderr)
        return None


def collaborate(
    img: Image.Image,
    signal: StuckSignal,
    *,
    case_id: str = "",
    extra_context: dict[str, Any] | None = None,
    gemini_fn: Callable[..., dict[str, Any] | None] | None = None,
    claude_fn: Callable[..., dict[str, Any] | None] | None = None,
) -> CollabGuidance | None:
    """Run Gemini→Claude deliberation. Fail-open: returns None if both dark."""
    if not signal.stuck:
        return None
    # Injected fns (tests / offline doubles) always run; live path needs keys.
    if gemini_fn is None and claude_fn is None and not collab_enabled():
        return None

    context: dict[str, Any] = {
        "case_id": case_id,
        "stuck": signal.to_dict(),
        **(extra_context or {}),
    }
    g_fn = gemini_fn or _gemini_plan
    c_fn = claude_fn or _claude_critique

    gemini_plan = None
    claude_plan = None
    try:
        gemini_plan = g_fn(img, context)
    except Exception as exc:  # noqa: BLE001
        print(f"[collab_mind] gemini skipped: {exc}", file=sys.stderr)
    try:
        claude_plan = c_fn(img, context, gemini_plan)
    except Exception as exc:  # noqa: BLE001
        print(f"[collab_mind] claude skipped: {exc}", file=sys.stderr)

    guidance = _merge_plans(gemini_plan, claude_plan)
    if not guidance.providers_used:
        return None

    print(
        f"[collab_mind] {'TAKEOVER' if guidance.takeover else 'advise'} "
        f"path={guidance.preferred_path} via {','.join(guidance.providers_used)} "
        f"severity={signal.severity:.2f}",
        file=sys.stderr,
    )
    if guidance.diagnosis:
        print(f"[collab_mind] diagnosis: {guidance.diagnosis[:240]}", file=sys.stderr)
    if guidance.actions:
        print(f"[collab_mind] actions: {guidance.actions[:4]}", file=sys.stderr)
    return guidance


def journal_escalation(
    guidance: CollabGuidance,
    signal: StuckSignal,
    *,
    case_id: str = "",
    task: int | None = 3,
) -> dict[str, Any] | None:
    """Append a Meedo journal finding — shared memory for Cursor ↔ Claude Code."""
    try:
        from tools.logo_vectorizer.meedo_journal import log as journal_log
    except Exception as exc:  # noqa: BLE001
        print(f"[collab_mind] journal skipped: {exc}", file=sys.stderr)
        return None

    summary = (
        f"collab_mind escalate path={guidance.preferred_path} "
        f"providers={','.join(guidance.providers_used)} "
        f"severity={signal.severity:.2f}"
    )
    if case_id:
        summary += f" case={case_id}"
    if guidance.diagnosis:
        summary += f" — {guidance.diagnosis[:160]}"

    try:
        return journal_log(
            agent="meedo",
            kind="finding",
            summary=summary,
            task=task,
            evidence={
                "collab_mind": guidance.to_dict(),
                "stuck": signal.to_dict(),
                "case_id": case_id,
            },
            source="collab_mind",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[collab_mind] journal log failed: {exc}", file=sys.stderr)
        return None


def escalate_if_stuck(
    img: Image.Image,
    *,
    case_id: str = "",
    journal: bool = True,
    task: int | None = 3,
    extra_context: dict[str, Any] | None = None,
    **stuck_kwargs: Any,
) -> CollabGuidance | None:
    """Detect stuck → collaborate → optional Meedo journal. Single entry point."""
    signal = detect_stuck(**stuck_kwargs)
    if not signal.stuck:
        return None
    guidance = collaborate(img, signal, case_id=case_id, extra_context=extra_context)
    if guidance is None:
        return None
    if journal:
        journal_escalation(guidance, signal, case_id=case_id, task=task)
    return guidance


__all__ = [
    "CollabGuidance",
    "StuckSignal",
    "available",
    "collab_enabled",
    "collaborate",
    "detect_stuck",
    "escalate_if_stuck",
    "journal_escalation",
]
