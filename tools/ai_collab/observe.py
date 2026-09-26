"""Unified observation layer — every face writes into one Meedo memory.

Faces (Gemini, Claude API, Serper, Cursor, Claude Code, collab_mind,
improve loops, OpenClaw) call ``observe`` with what was tried, evidence,
method, outcome, and do-not-regress. This fans out into:

  * journal finding (shared work log)
  * ai_lessons (judgment Meedo can recall offline)
  * episodes (method for meedo_recall)
  * procedures (fine-grained executable playbook)

Fail-open always — observation never breaks the caller.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ObservationResult:
    journal_id: str = ""
    lesson_id: str = ""
    episode_id: str = ""
    procedure_id: str = ""
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "journal_id": self.journal_id,
            "lesson_id": self.lesson_id,
            "episode_id": self.episode_id,
            "procedure_id": self.procedure_id,
            "errors": list(self.errors),
        }


def observe(
    *,
    face: str,
    domain: str,
    tried: str,
    evidence: str = "",
    method: str = "",
    outcome: str = "open",
    do_not_regress: list[str] | None = None,
    steps: list[str] | None = None,
    knobs: dict[str, Any] | None = None,
    actions: list[str] | None = None,
    risks: list[str] | None = None,
    providers: list[str] | None = None,
    tags: list[str] | None = None,
    cases: list[str] | None = None,
    task: int | None = 3,
    agent: str = "meedo",
    journal: bool = True,
    lesson: bool = True,
    episode: bool = True,
    procedure: bool = True,
    agree: bool | None = None,
    offline_ready: bool | None = None,
    confidence: float | None = None,
    source: str = "",
    raw: dict[str, Any] | None = None,
) -> ObservationResult:
    """Record one structured observation from any face into Meedo memory."""
    out = ObservationResult()
    face = (face or "meedo").strip().lower()
    domain = (domain or "general").strip() or "general"
    tried = (tried or "").strip()
    method = (method or "").strip()
    if not tried:
        out.errors.append("empty tried")
        return out

    src = source or f"observe:{face}"
    tags_all = list(dict.fromkeys([face, domain, *(tags or [])]))
    providers_all = list(dict.fromkeys(list(providers or []) + ([face] if face not in ("meedo", "cursor", "claude_code") else [])))
    dnr = list(do_not_regress or risks or [])
    method_text = method or ("; ".join((actions or [])[:3]) if actions else tried[:240])
    steps_all = list(steps or actions or [])
    if method_text and method_text not in steps_all:
        # Keep method as the first transferable step when no explicit steps.
        if not steps_all:
            steps_all = [method_text]

    if journal:
        try:
            from tools.logo_vectorizer.meedo_journal import log as journal_log

            summary = f"[{face}] {domain}: {tried[:200]}"
            if method_text:
                summary += f" → {method_text[:120]}"
            entry = journal_log(
                agent=agent if agent in ("claude", "cursor", "human", "meedo") else "meedo",
                kind="finding",
                summary=summary,
                task=task,
                evidence={
                    "observe": {
                        "face": face,
                        "domain": domain,
                        "tried": tried[:500],
                        "evidence": evidence[:500],
                        "method": method_text[:500],
                        "outcome": outcome,
                        "do_not_regress": dnr[:8],
                        "providers": providers_all,
                    },
                    **(raw or {}),
                },
                source=src,
            )
            out.journal_id = str(entry.get("id") or "")
        except Exception as exc:  # noqa: BLE001
            out.errors.append(f"journal:{exc}")
            print(f"[observe] journal skipped: {exc}", file=sys.stderr)

    if lesson and method_text:
        try:
            from tools.ai_collab.learn import persist_lesson

            L = persist_lesson(
                domain=domain,
                problem=tried[:400],
                diagnosis=evidence[:400] or tried[:200],
                method=method_text[:600],
                actions=list(actions or steps_all)[:12],
                risks=dnr[:8],
                providers=providers_all,
                tags=tags_all,
                cases=cases,
                agree=agree,
                raw=raw,
                mirror_episode=False,  # episode handled below once
            )
            out.lesson_id = L.id
        except Exception as exc:  # noqa: BLE001
            out.errors.append(f"lesson:{exc}")
            print(f"[observe] lesson skipped: {exc}", file=sys.stderr)

    if episode and method_text:
        try:
            from tools.logo_vectorizer import meedo_episodes as E

            ep_outcome = outcome if outcome in ("success", "failure", "partial", "open") else "open"
            # Closed episodes require a method — we already gated on method_text.
            ep = E.record(
                problem=tried[:400],
                method=method_text[:600],
                outcome=ep_outcome if ep_outcome != "open" or method_text else "open",
                workstream="meedo-me" if domain.startswith("meedo") else domain or "ai-collab",
                first_read=f"observed via {face}",
                evidence=(evidence or "")[:400],
                cause="",
                fix="; ".join((actions or steps_all)[:4])[:400],
                tags=tags_all,
                cases=list(cases or []),
                source=src,
            )
            out.episode_id = str(ep.get("id") or "")
        except Exception as exc:  # noqa: BLE001
            out.errors.append(f"episode:{exc}")
            print(f"[observe] episode skipped: {exc}", file=sys.stderr)

    if procedure and (steps_all or knobs):
        try:
            from tools.ai_collab.procedures import persist_procedure

            P = persist_procedure(
                face=face,
                domain=domain,
                title=tried[:200],
                steps=steps_all[:20],
                knobs=knobs,
                do_not_regress=dnr[:12],
                evidence=evidence[:400],
                outcome=outcome if outcome in ("success", "failure", "partial", "open") else "open",
                offline_ready=offline_ready,
                confidence=confidence,
                tags=tags_all,
                cases=cases,
                providers=providers_all,
                source=src,
                raw=raw,
            )
            out.procedure_id = P.id
        except Exception as exc:  # noqa: BLE001
            out.errors.append(f"procedure:{exc}")
            print(f"[observe] procedure skipped: {exc}", file=sys.stderr)

    return out


def observe_deliberation(
    deliberation: Any,
    *,
    face: str = "ai_collab",
    domain: str = "general",
    problem: str = "",
    cases: list[str] | None = None,
    tags: list[str] | None = None,
    task: int | None = 3,
    outcome: str = "open",
) -> ObservationResult:
    """Bridge a Deliberation / Advice dict into observe()."""
    if deliberation is None:
        return ObservationResult(errors=["no deliberation"])
    d = deliberation.to_dict() if hasattr(deliberation, "to_dict") else dict(deliberation)
    providers = list(d.get("providers_used") or d.get("providers") or [])
    return observe(
        face=face,
        domain=str(d.get("domain") or domain),
        tried=problem or str(d.get("problem") or d.get("diagnosis") or "")[:400],
        evidence=str(d.get("diagnosis") or ""),
        method=str(d.get("method") or ""),
        outcome=outcome,
        actions=list(d.get("actions") or []),
        risks=list(d.get("risks") or []),
        providers=providers,
        tags=tags,
        cases=cases,
        task=task,
        agree=d.get("agree"),
        journal=False,  # advisor/collab already journal when wanted
        lesson=False,  # advisor already persists lesson
        episode=True,
        procedure=True,
        steps=list(d.get("actions") or []),
        knobs={"priority": d.get("priority")} if d.get("priority") is not None else None,
        source=f"observe:{face}",
        raw=d,
    )
