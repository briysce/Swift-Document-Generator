"""Recall-first advise: Meedo learns from Gemini+Claude, then owns repeats.

Flow:
1. Recall stored AI lessons for this problem.
2. If a strong match exists, return it offline (no API) and mark applied.
3. Else deliberate (Gemini plan → Claude critique), persist lesson, return.
4. Always fail-open to None when both memory and APIs are empty.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

from .deliberate import Deliberation, deliberate
from .learn import mark_applied_offline, persist_from_deliberation, recall_lessons


@dataclass
class Advice:
    domain: str
    problem: str
    diagnosis: str = ""
    method: str = ""
    actions: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    providers_used: list[str] = field(default_factory=list)
    source: str = ""  # recalled | gemini,claude | gemini | claude
    lesson_id: str = ""
    offline: bool = False
    agree: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "problem": self.problem,
            "diagnosis": self.diagnosis,
            "method": self.method,
            "actions": list(self.actions),
            "risks": list(self.risks),
            "providers_used": list(self.providers_used),
            "source": self.source,
            "lesson_id": self.lesson_id,
            "offline": self.offline,
            "agree": self.agree,
        }


def _offline_threshold() -> float:
    try:
        return float(os.environ.get("MEEDO_AI_RECALL_MIN", "6.0"))
    except ValueError:
        return 6.0


def advise(
    *,
    domain: str,
    problem: str,
    context: dict[str, Any] | None = None,
    cases: list[str] | None = None,
    tags: list[str] | None = None,
    force_live: bool = False,
    gemini_fn: Callable[[str], dict[str, Any] | None] | None = None,
    claude_fn: Callable[[str], dict[str, Any] | None] | None = None,
    persist: bool = True,
    journal: bool = True,
) -> Advice | None:
    """Return advice from memory or live Gemini+Claude. Fail-open → None."""
    domain = (domain or "general").strip() or "general"
    problem = (problem or "").strip()
    if not problem:
        return None

    ctx = dict(context or {})
    ctx.setdefault("problem", problem)
    if cases:
        ctx.setdefault("cases", cases)

    if not force_live:
        hits = recall_lessons(
            problem,
            domain=domain,
            cases=cases,
            top=1,
            min_score=_offline_threshold(),
            mark_recalled=True,
        )
        if hits:
            hit = hits[0]
            mark_applied_offline(hit["id"])
            advice = Advice(
                domain=domain,
                problem=problem,
                diagnosis=str(hit.get("diagnosis") or ""),
                method=str(hit.get("method") or ""),
                actions=list(hit.get("actions") or []),
                risks=list(hit.get("risks") or []),
                providers_used=list(hit.get("providers") or ["meedo_recall"]),
                source="recalled",
                lesson_id=str(hit.get("id") or ""),
                offline=True,
                agree=hit.get("agree"),
                raw=hit,
            )
            if journal:
                _journal(advice, kind="finding")
            return advice

    deliberation: Deliberation | None = deliberate(
        domain=domain,
        context=ctx,
        gemini_fn=gemini_fn,
        claude_fn=claude_fn,
    )
    if deliberation is None:
        return None

    lesson_id = ""
    if persist:
        lesson = persist_from_deliberation(
            deliberation,
            problem=problem,
            cases=cases,
            tags=tags or [domain, "ai_collab"],
        )
        if lesson is not None:
            lesson_id = lesson.id

    advice = Advice(
        domain=domain,
        problem=problem,
        diagnosis=deliberation.diagnosis,
        method=deliberation.method,
        actions=list(deliberation.actions),
        risks=list(deliberation.risks),
        providers_used=list(deliberation.providers_used),
        source=",".join(deliberation.providers_used) or "ai_collab",
        lesson_id=lesson_id,
        offline=False,
        agree=deliberation.agree,
        raw=deliberation.to_dict(),
    )
    if journal:
        _journal(advice, kind="finding")
    # Study the face: persist procedure playbook for offline hand-off.
    try:
        from tools.ai_collab.observe import observe_deliberation

        observe_deliberation(
            advice,
            face="ai_collab" if advice.offline else (
                "gemini" if advice.providers_used == ["gemini"]
                else "claude" if advice.providers_used == ["claude"]
                else "ai_collab"
            ),
            domain=domain,
            problem=problem,
            cases=cases,
            tags=tags or [domain],
            outcome="open",
        )
    except Exception:
        pass
    return advice


def _journal(advice: Advice, *, kind: str = "finding") -> None:
    try:
        from tools.logo_vectorizer.meedo_journal import log as journal_log

        summary = (
            f"ai_collab {'offline' if advice.offline else 'live'} "
            f"domain={advice.domain} source={advice.source}"
        )
        if advice.diagnosis:
            summary += f" — {advice.diagnosis[:160]}"
        journal_log(
            agent="meedo",
            kind=kind,
            summary=summary,
            task=3,
            evidence={"ai_collab": advice.to_dict()},
            source="ai_collab",
        )
    except Exception:
        pass


def advise_top_failures(
    *,
    domain: str,
    top_failures: list[dict[str, Any]],
    extra_context: dict[str, Any] | None = None,
    max_items: int = 3,
    **kwargs: Any,
) -> list[Advice]:
    """Advise on improve-loop top_failures (logo / shipping / bol / app)."""
    out: list[Advice] = []
    for row in (top_failures or [])[:max_items]:
        case_id = str(row.get("case_id") or row.get("case") or row.get("pair_id") or "")
        reason = str(
            row.get("reason")
            or row.get("error")
            or row.get("note")
            or row.get("worst_metric")
            or ""
        )
        composite = row.get("composite")
        problem = f"{domain} failure case={case_id} composite={composite} reason={reason}".strip()
        ctx = {
            "case": row,
            "top_failures": top_failures[:5],
            **(extra_context or {}),
        }
        advice = advise(
            domain=domain,
            problem=problem,
            context=ctx,
            cases=[case_id] if case_id else None,
            tags=[domain, "improve_loop"],
            **kwargs,
        )
        if advice is not None:
            out.append(advice)
    return out
