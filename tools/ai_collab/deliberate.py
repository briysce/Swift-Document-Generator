"""Domain-agnostic Gemini plans → Claude critiques (text-only).

Used by Meedo cycle, improve loops, and PDF/app QA — not logo vision
(``collab_mind``). Same collaboration pattern: Gemini proposes, Claude
refines; Claude wins on disagreement for the actionable fields.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from .client import claude_json, gemini_json, available
from .env import load_env

DOMAINS = (
    "meedo_advisor",
    "logo_restore",
    "shipping_pdf",
    "receiving_pdf",
    "bol_pdf",
    "app_ux",
    "generate_assist",
    "general",
)

GEMINI_PROMPT = """You are Gemini collaborating with Claude for the Swift Document Generator.
Domain: {domain}
Problem / stuck context (JSON):
{context_json}

Propose a concrete plan Meedo-Me or an agent can act on. Return JSON only:
{{
  "diagnosis": "1-3 sentences",
  "priority": 1,
  "actions": ["concrete step 1", "concrete step 2"],
  "method": "transferable lesson for next time this looks similar",
  "risks": ["what not to break"],
  "notes": "anything Claude should check",
  "handoff_to_meedo": true
}}

Rules:
- Prefer specific file/stage/case names over vague advice.
- Never invent brand colors from gray sources.
- Never change Shipping Label locked SO/Contact constants (afterPillGap 11.0, showRule false, contactLabelToValue 3.0).
- Failures may be honest capability limits — say so instead of inventing redraws.
"""

CLAUDE_PROMPT = """You are Claude collaborating with Gemini for the Swift Document Generator.
Domain: {domain}
Gemini's plan:
{gemini_plan}
Stuck context:
{context_json}

Critique and refine. Return JSON only (same schema) plus:
{{
  "diagnosis": "...",
  "priority": 1,
  "actions": ["..."],
  "method": "...",
  "risks": ["..."],
  "notes": "agree / disagree with Gemini and why",
  "agree_with_gemini": true,
  "overrides": ["fields you changed"],
  "handoff_to_meedo": true
}}
"""


@dataclass
class Deliberation:
    domain: str
    diagnosis: str = ""
    priority: int = 3
    actions: list[str] = field(default_factory=list)
    method: str = ""
    risks: list[str] = field(default_factory=list)
    gemini_notes: str = ""
    claude_notes: str = ""
    providers_used: list[str] = field(default_factory=list)
    agree: bool | None = None
    handoff_to_meedo: bool = True
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "diagnosis": self.diagnosis,
            "priority": self.priority,
            "actions": list(self.actions),
            "method": self.method,
            "risks": list(self.risks),
            "gemini_notes": self.gemini_notes,
            "claude_notes": self.claude_notes,
            "providers_used": list(self.providers_used),
            "agree": self.agree,
            "handoff_to_meedo": self.handoff_to_meedo,
        }


def collab_enabled() -> bool:
    """On by default when keys exist; disable with AI_COLLAB=0 or MEEDO_AI_COLLAB=0."""
    load_env()
    for flag_name in ("AI_COLLAB", "MEEDO_AI_COLLAB"):
        flag = os.environ.get(flag_name, "").strip().lower()
        if flag in {"0", "false", "no", "off"}:
            return False
        if flag in {"1", "true", "yes", "on"}:
            return True
    return available()


def _as_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def _from_dict(
    data: dict[str, Any],
    *,
    domain: str,
    providers: list[str],
    gemini_notes: str = "",
    claude_notes: str = "",
    agree: bool | None = None,
    raw: dict[str, Any] | None = None,
) -> Deliberation:
    try:
        priority = int(data.get("priority") or 3)
    except (TypeError, ValueError):
        priority = 3
    priority = max(1, min(priority, 5))
    return Deliberation(
        domain=domain,
        diagnosis=str(data.get("diagnosis") or ""),
        priority=priority,
        actions=_as_list(data.get("actions")),
        method=str(data.get("method") or ""),
        risks=_as_list(data.get("risks")),
        gemini_notes=gemini_notes or str(data.get("notes") or ""),
        claude_notes=claude_notes,
        providers_used=providers,
        agree=agree,
        handoff_to_meedo=bool(data.get("handoff_to_meedo", True)),
        raw=raw or data,
    )


def merge_plans(
    gemini: dict[str, Any] | None,
    claude: dict[str, Any] | None,
    *,
    domain: str,
) -> Deliberation | None:
    providers: list[str] = []
    if gemini:
        providers.append("gemini")
    if claude:
        providers.append("claude")
    if not providers:
        return None

    if gemini and not claude:
        return _from_dict(
            gemini,
            domain=domain,
            providers=providers,
            gemini_notes=str(gemini.get("notes") or ""),
            raw={"gemini": gemini},
        )
    if claude and not gemini:
        return _from_dict(
            claude,
            domain=domain,
            providers=providers,
            claude_notes=str(claude.get("notes") or ""),
            raw={"claude": claude},
        )

    assert gemini is not None and claude is not None
    agree = claude.get("agree_with_gemini")
    if agree is None:
        agree = str(gemini.get("diagnosis", ""))[:80] == str(claude.get("diagnosis", ""))[:80]
    base = dict(gemini)
    base.update({k: v for k, v in claude.items() if v not in (None, "", [], {})})
    if not agree:
        # Claude wins on actionable fields when they disagree.
        for key in ("diagnosis", "method", "priority", "handoff_to_meedo"):
            if claude.get(key) not in (None, "", []):
                base[key] = claude[key]
    base["actions"] = list(
        dict.fromkeys(_as_list(gemini.get("actions")) + _as_list(claude.get("actions")))
    )
    base["risks"] = list(
        dict.fromkeys(_as_list(gemini.get("risks")) + _as_list(claude.get("risks")))
    )
    return _from_dict(
        base,
        domain=domain,
        providers=providers,
        gemini_notes=str(gemini.get("notes") or ""),
        claude_notes=str(claude.get("notes") or ""),
        agree=bool(agree),
        raw={"gemini": gemini, "claude": claude},
    )


def deliberate(
    *,
    domain: str,
    context: dict[str, Any],
    gemini_fn: Callable[[str], dict[str, Any] | None] | None = None,
    claude_fn: Callable[[str], dict[str, Any] | None] | None = None,
) -> Deliberation | None:
    """Run Gemini→Claude deliberation. Fail-open: None if both dark / disabled."""
    domain = (domain or "general").strip() or "general"
    if gemini_fn is None and claude_fn is None and not collab_enabled():
        return None

    ctx_json = json.dumps(context, indent=2, default=str)[:4500]
    g_prompt = GEMINI_PROMPT.format(domain=domain, context_json=ctx_json)
    gemini_plan: dict[str, Any] | None = None
    claude_plan: dict[str, Any] | None = None

    try:
        if gemini_fn is not None:
            gemini_plan = gemini_fn(g_prompt)
        else:
            gemini_plan = gemini_json(g_prompt)
    except Exception as exc:  # noqa: BLE001
        print(f"[ai_collab] gemini skipped: {exc}", file=sys.stderr)

    c_prompt = CLAUDE_PROMPT.format(
        domain=domain,
        gemini_plan=json.dumps(gemini_plan or {"status": "gemini_unavailable"}, indent=2)[:3500],
        context_json=ctx_json[:2500],
    )
    try:
        if claude_fn is not None:
            claude_plan = claude_fn(c_prompt)
        else:
            claude_plan = claude_json(c_prompt)
    except Exception as exc:  # noqa: BLE001
        print(f"[ai_collab] claude skipped: {exc}", file=sys.stderr)

    result = merge_plans(gemini_plan, claude_plan, domain=domain)
    if result is None:
        return None
    print(
        f"[ai_collab] deliberate domain={domain} via {','.join(result.providers_used)} "
        f"P{result.priority}",
        file=sys.stderr,
    )
    return result
