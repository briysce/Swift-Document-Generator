"""Meedo-Me learning from Gemini + Claude — persist so Meedo can own decisions.

When either API gives guidance, we store a structured lesson. Next time a
similar problem appears, ``recall_lessons`` can return that lesson with high
enough confidence that callers skip live API calls (hand-off path).

Lessons live in ``qa_logos/synthetic/meedo_ai_lessons.json`` and are also
mirrored into Meedo episodes (``source=ai_collab``) so MCP ``meedo_recall``
sees them.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "qa_logos" / "synthetic" / "meedo_ai_lessons.json"
# Canonical on-disk ledger — used to decide episode mirroring even when tests
# monkeypatch DEFAULT_PATH.
CANONICAL_LESSONS_PATH = ROOT / "qa_logos" / "synthetic" / "meedo_ai_lessons.json"
MAX_LESSONS = 800

_STOP = set(
    "a an the and or of to in on for with is was were be been it its this that "
    "as at by from into not no but so if then than there their they we i you "
    "what which when where how why all any can could would should will".split()
)


def lessons_path() -> Path:
    return DEFAULT_PATH


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tokens(text: str) -> set[str]:
    return {
        t
        for t in re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split()
        if len(t) > 2 and t not in _STOP
    }


@dataclass
class Lesson:
    id: str
    domain: str
    problem: str
    diagnosis: str
    method: str
    actions: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    cases: list[str] = field(default_factory=list)
    agree: bool | None = None
    source: str = "ai_collab"
    ts: str = ""
    times_recalled: int = 0
    times_applied_offline: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "domain": self.domain,
            "problem": self.problem,
            "diagnosis": self.diagnosis,
            "method": self.method,
            "actions": list(self.actions),
            "risks": list(self.risks),
            "providers": list(self.providers),
            "tags": list(self.tags),
            "cases": list(self.cases),
            "agree": self.agree,
            "source": self.source,
            "ts": self.ts,
            "times_recalled": self.times_recalled,
            "times_applied_offline": self.times_applied_offline,
            "raw": self.raw,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Lesson:
        return cls(
            id=str(data.get("id") or f"AL{uuid.uuid4().hex[:8]}"),
            domain=str(data.get("domain") or "general"),
            problem=str(data.get("problem") or ""),
            diagnosis=str(data.get("diagnosis") or ""),
            method=str(data.get("method") or ""),
            actions=[str(a) for a in (data.get("actions") or [])],
            risks=[str(r) for r in (data.get("risks") or [])],
            providers=[str(p) for p in (data.get("providers") or [])],
            tags=[str(t) for t in (data.get("tags") or [])],
            cases=[str(c) for c in (data.get("cases") or [])],
            agree=data.get("agree"),
            source=str(data.get("source") or "ai_collab"),
            ts=str(data.get("ts") or ""),
            times_recalled=int(data.get("times_recalled") or 0),
            times_applied_offline=int(data.get("times_applied_offline") or 0),
            raw=dict(data.get("raw") or {}),
        )


def load(path: Path | None = None) -> list[Lesson]:
    p = path or DEFAULT_PATH
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = data.get("lessons") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    return [Lesson.from_dict(r) for r in rows if isinstance(r, dict)]


def _save(lessons: list[Lesson], path: Path | None = None) -> None:
    p = path or DEFAULT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "lessons": [L.to_dict() for L in lessons[-MAX_LESSONS:]],
    }
    p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _score(query: str, lesson: Lesson, domain: str = "", cases: list[str] | None = None) -> float:
    q = _tokens(query)
    if not q:
        return 0.0
    score = 0.0
    score += 3.0 * len(q & _tokens(lesson.problem))
    score += 2.0 * len(q & _tokens(lesson.diagnosis))
    score += 2.5 * len(q & _tokens(lesson.method))
    score += 1.5 * len(q & _tokens(" ".join(lesson.actions)))
    score += 1.0 * len(q & set(t.lower() for t in lesson.tags))
    if domain and lesson.domain == domain:
        score += 2.0
    if cases:
        overlap = set(cases) & set(lesson.cases)
        score += 4.0 * len(overlap)
    # Prefer lessons that already worked offline (hand-off progress).
    score += min(lesson.times_applied_offline, 5) * 0.3
    return score


def recall_lessons(
    problem: str,
    *,
    domain: str = "",
    cases: list[str] | None = None,
    top: int = 3,
    min_score: float = 4.0,
    path: Path | None = None,
    mark_recalled: bool = False,
) -> list[dict[str, Any]]:
    """Return stored AI lessons most like ``problem`` (strongest first)."""
    lessons = load(path)
    ranked: list[tuple[float, Lesson]] = []
    for lesson in lessons:
        s = _score(problem, lesson, domain=domain, cases=cases)
        if s >= min_score:
            ranked.append((s, lesson))
    ranked.sort(key=lambda x: (-x[0], x[1].ts))
    out: list[dict[str, Any]] = []
    for s, lesson in ranked[:top]:
        if mark_recalled:
            lesson.times_recalled += 1
        d = lesson.to_dict()
        d["recall_score"] = round(s, 3)
        out.append(d)
    if mark_recalled and out:
        _save(lessons, path)
    return out


def persist_lesson(
    *,
    domain: str,
    problem: str,
    diagnosis: str,
    method: str,
    actions: list[str] | None = None,
    risks: list[str] | None = None,
    providers: list[str] | None = None,
    tags: list[str] | None = None,
    cases: list[str] | None = None,
    agree: bool | None = None,
    raw: dict[str, Any] | None = None,
    mirror_episode: bool = True,
    path: Path | None = None,
) -> Lesson:
    """Store a structured lesson and optionally mirror into Meedo episodes."""
    if not problem.strip():
        raise ValueError("lesson needs a problem")
    lessons = load(path)
    # Dedup: same domain+problem+method → refresh rather than duplicate.
    key = (domain.strip(), problem.strip()[:200], (method or "").strip()[:200])
    existing = None
    for L in lessons:
        if (L.domain, L.problem[:200], L.method[:200]) == key:
            existing = L
            break
    if existing is not None:
        existing.diagnosis = diagnosis or existing.diagnosis
        existing.actions = list(actions or existing.actions)
        existing.risks = list(risks or existing.risks)
        existing.providers = list(providers or existing.providers)
        existing.tags = list(dict.fromkeys(existing.tags + list(tags or [])))
        existing.cases = list(dict.fromkeys(existing.cases + list(cases or [])))
        existing.agree = agree if agree is not None else existing.agree
        existing.raw = raw or existing.raw
        existing.ts = _now()
        lesson = existing
    else:
        lesson = Lesson(
            id=f"AL{uuid.uuid4().hex[:8]}",
            domain=domain or "general",
            problem=problem.strip(),
            diagnosis=(diagnosis or "").strip(),
            method=(method or "").strip() or (diagnosis or "").strip()[:240],
            actions=list(actions or []),
            risks=list(risks or []),
            providers=list(providers or []),
            tags=list(tags or []),
            cases=list(cases or []),
            agree=agree,
            ts=_now(),
            raw=dict(raw or {}),
        )
        lessons.append(lesson)
    _save(lessons, path)

    # Mirror into Meedo episodes only when writing the real lessons ledger.
    # Unit tests monkeypatch DEFAULT_PATH / pass a custom path — never pollute.
    effective = path or DEFAULT_PATH
    do_mirror = bool(mirror_episode) and Path(effective).resolve() == CANONICAL_LESSONS_PATH.resolve()
    if do_mirror and os.environ.get("MEEDO_AI_MIRROR_EPISODES", "1").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        do_mirror = False
    if do_mirror and lesson.method:
        try:
            from tools.logo_vectorizer import meedo_episodes as E

            E.record(
                problem=lesson.problem[:400],
                method=lesson.method[:600],
                outcome="open",
                workstream="meedo-me" if domain.startswith("meedo") else domain or "ai-collab",
                first_read=f"Gemini+Claude ({','.join(lesson.providers) or 'offline'})",
                evidence=lesson.diagnosis[:400],
                cause="",
                fix="; ".join(lesson.actions)[:400],
                tags=list(dict.fromkeys(["ai_collab", *lesson.tags, *lesson.providers])),
                cases=list(lesson.cases),
                source="ai_collab",
            )
        except Exception:
            pass  # fail-open — lessons file is the source of truth

    return lesson


def mark_applied_offline(lesson_id: str, path: Path | None = None) -> Lesson | None:
    """Bump counter when Meedo used a recalled lesson instead of live APIs."""
    lessons = load(path)
    for L in lessons:
        if L.id == lesson_id:
            L.times_applied_offline += 1
            L.times_recalled += 1
            _save(lessons, path)
            return L
    return None


def persist_from_deliberation(
    deliberation: Any,
    *,
    problem: str,
    cases: list[str] | None = None,
    tags: list[str] | None = None,
    path: Path | None = None,
) -> Lesson | None:
    """Convenience: turn a Deliberation into a stored Lesson."""
    if deliberation is None:
        return None
    d = deliberation.to_dict() if hasattr(deliberation, "to_dict") else dict(deliberation)
    method = str(d.get("method") or "").strip()
    if not method:
        actions = d.get("actions") or []
        method = "; ".join(str(a) for a in actions[:3]) if actions else str(d.get("diagnosis") or "")
    if not method.strip():
        return None
    return persist_lesson(
        domain=str(d.get("domain") or "general"),
        problem=problem,
        diagnosis=str(d.get("diagnosis") or ""),
        method=method,
        actions=list(d.get("actions") or []),
        risks=list(d.get("risks") or []),
        providers=list(d.get("providers_used") or d.get("providers") or []),
        tags=tags,
        cases=cases,
        agree=d.get("agree"),
        raw=d,
        path=path,
    )
