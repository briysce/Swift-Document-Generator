"""Fine-grained procedure playbook — what Meedo can later execute offline.

Lessons capture *transferable judgment* (diagnosis → method). Procedures
capture *reproducible steps* (query patterns, preprocess knobs, digest
shape, merge rules, brand must_keep checks) so Meedo can one day run them
without Gemini / Claude / Serper / Cursor / Claude Code.

Stored in ``qa_logos/synthetic/meedo_procedures.json``, merged via the same
``merge=meedo`` driver as other Meedo memory files.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "qa_logos" / "synthetic" / "meedo_procedures.json"
MAX_PROCEDURES = 600

FACES = (
    "gemini",
    "claude",
    "serper",
    "cursor",
    "claude_code",
    "collab_mind",
    "ai_collab",
    "meedo",
    "openclaw",
    "improve_loop",
)

_STOP = set(
    "a an the and or of to in on for with is was were be been it its this that "
    "as at by from into not no but so if then than there their they we i you "
    "what which when where how why all any can could would should will".split()
)


def procedures_path() -> Path:
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
class Procedure:
    id: str
    face: str
    domain: str
    title: str
    steps: list[str] = field(default_factory=list)
    knobs: dict[str, Any] = field(default_factory=dict)
    do_not_regress: list[str] = field(default_factory=list)
    evidence: str = ""
    outcome: str = "open"  # success | failure | partial | open
    offline_ready: bool = False
    confidence: float = 0.0  # 0..1 Meedo confidence it can own this offline
    tags: list[str] = field(default_factory=list)
    cases: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    source: str = "observe"
    times_recalled: int = 0
    times_applied_offline: int = 0
    ts: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "face": self.face,
            "domain": self.domain,
            "title": self.title,
            "steps": list(self.steps),
            "knobs": dict(self.knobs),
            "do_not_regress": list(self.do_not_regress),
            "evidence": self.evidence,
            "outcome": self.outcome,
            "offline_ready": bool(self.offline_ready),
            "confidence": round(float(self.confidence), 3),
            "tags": list(self.tags),
            "cases": list(self.cases),
            "providers": list(self.providers),
            "source": self.source,
            "times_recalled": self.times_recalled,
            "times_applied_offline": self.times_applied_offline,
            "ts": self.ts,
            "raw": self.raw,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Procedure:
        return cls(
            id=str(data.get("id") or f"P{uuid.uuid4().hex[:8]}"),
            face=str(data.get("face") or "meedo"),
            domain=str(data.get("domain") or "general"),
            title=str(data.get("title") or ""),
            steps=[str(s) for s in (data.get("steps") or [])],
            knobs=dict(data.get("knobs") or {}),
            do_not_regress=[str(d) for d in (data.get("do_not_regress") or [])],
            evidence=str(data.get("evidence") or ""),
            outcome=str(data.get("outcome") or "open"),
            offline_ready=bool(data.get("offline_ready")),
            confidence=float(data.get("confidence") or 0.0),
            tags=[str(t) for t in (data.get("tags") or [])],
            cases=[str(c) for c in (data.get("cases") or [])],
            providers=[str(p) for p in (data.get("providers") or [])],
            source=str(data.get("source") or "observe"),
            times_recalled=int(data.get("times_recalled") or 0),
            times_applied_offline=int(data.get("times_applied_offline") or 0),
            ts=str(data.get("ts") or ""),
            raw=dict(data.get("raw") or {}),
        )


def load(path: Path | None = None) -> list[Procedure]:
    p = path or DEFAULT_PATH
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = data.get("procedures") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    return [Procedure.from_dict(r) for r in rows if isinstance(r, dict)]


def _save(rows: list[Procedure], path: Path | None = None) -> None:
    p = path or DEFAULT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "purpose": (
            "Fine-grained procedures Meedo studies from every face "
            "(Gemini/Claude/Serper/Cursor/Claude Code/OpenClaw) so it can "
            "eventually execute them offline."
        ),
        "procedures": [r.to_dict() for r in rows[-MAX_PROCEDURES:]],
    }
    p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _score(query: str, proc: Procedure, domain: str = "") -> float:
    q = _tokens(query)
    if not q:
        return 0.0
    score = 0.0
    score += 3.0 * len(q & _tokens(proc.title))
    score += 2.0 * len(q & _tokens(" ".join(proc.steps)))
    score += 1.5 * len(q & _tokens(proc.evidence))
    score += 1.0 * len(q & set(t.lower() for t in proc.tags))
    if domain and proc.domain == domain:
        score += 2.0
    score += proc.confidence * 2.0
    score += min(proc.times_applied_offline, 5) * 0.3
    return score


def recall_procedures(
    query: str,
    *,
    domain: str = "",
    face: str = "",
    top: int = 5,
    min_score: float = 3.0,
    path: Path | None = None,
    mark_recalled: bool = False,
) -> list[dict[str, Any]]:
    rows = load(path)
    ranked: list[tuple[float, Procedure]] = []
    for proc in rows:
        if face and proc.face != face:
            continue
        s = _score(query, proc, domain=domain)
        if s >= min_score:
            ranked.append((s, proc))
    ranked.sort(key=lambda x: (-x[0], x[1].ts))
    out: list[dict[str, Any]] = []
    for s, proc in ranked[:top]:
        if mark_recalled:
            proc.times_recalled += 1
        d = proc.to_dict()
        d["recall_score"] = round(s, 3)
        out.append(d)
    if mark_recalled and out:
        _save(rows, path)
    return out


def persist_procedure(
    *,
    face: str,
    domain: str,
    title: str,
    steps: list[str] | None = None,
    knobs: dict[str, Any] | None = None,
    do_not_regress: list[str] | None = None,
    evidence: str = "",
    outcome: str = "open",
    offline_ready: bool | None = None,
    confidence: float | None = None,
    tags: list[str] | None = None,
    cases: list[str] | None = None,
    providers: list[str] | None = None,
    source: str = "observe",
    raw: dict[str, Any] | None = None,
    path: Path | None = None,
) -> Procedure:
    """Store or refresh a procedure. Dedupes on face+domain+title."""
    if not title.strip():
        raise ValueError("procedure needs a title")
    face = (face or "meedo").strip().lower()
    domain = (domain or "general").strip() or "general"
    steps = [str(s).strip() for s in (steps or []) if str(s).strip()]
    if not steps and not knobs:
        raise ValueError("procedure needs steps or knobs")

    # Heuristic confidence: successful + ≥2 concrete steps → more offline-ready.
    conf = confidence
    if conf is None:
        conf = 0.25
        if outcome == "success":
            conf += 0.35
        elif outcome == "partial":
            conf += 0.15
        conf += min(len(steps), 5) * 0.06
        if knobs:
            conf += 0.1
        conf = min(conf, 0.95)
    ready = offline_ready if offline_ready is not None else (conf >= 0.55 and outcome in ("success", "partial"))

    rows = load(path)
    key = (face, domain, title.strip()[:200])
    existing = None
    for p in rows:
        if (p.face, p.domain, p.title[:200]) == key:
            existing = p
            break
    if existing is not None:
        existing.steps = steps or existing.steps
        existing.knobs = dict(knobs) if knobs is not None else existing.knobs
        existing.do_not_regress = list(
            dict.fromkeys(existing.do_not_regress + list(do_not_regress or []))
        )
        existing.evidence = evidence or existing.evidence
        existing.outcome = outcome or existing.outcome
        existing.offline_ready = ready
        existing.confidence = max(existing.confidence, float(conf))
        existing.tags = list(dict.fromkeys(existing.tags + list(tags or [])))
        existing.cases = list(dict.fromkeys(existing.cases + list(cases or [])))
        existing.providers = list(dict.fromkeys(existing.providers + list(providers or [])))
        existing.source = source or existing.source
        existing.raw = raw or existing.raw
        existing.ts = _now()
        proc = existing
    else:
        proc = Procedure(
            id=f"P{uuid.uuid4().hex[:8]}",
            face=face,
            domain=domain,
            title=title.strip(),
            steps=steps,
            knobs=dict(knobs or {}),
            do_not_regress=list(do_not_regress or []),
            evidence=evidence.strip(),
            outcome=outcome,
            offline_ready=ready,
            confidence=float(conf),
            tags=list(tags or []),
            cases=list(cases or []),
            providers=list(providers or []),
            source=source,
            ts=_now(),
            raw=dict(raw or {}),
        )
        rows.append(proc)
    _save(rows, path)
    return proc


def mark_applied_offline(proc_id: str, path: Path | None = None) -> Procedure | None:
    rows = load(path)
    for p in rows:
        if p.id == proc_id:
            p.times_applied_offline += 1
            p.times_recalled += 1
            p.confidence = min(0.99, p.confidence + 0.05)
            if p.times_applied_offline >= 2 and p.outcome in ("success", "partial"):
                p.offline_ready = True
            _save(rows, path)
            return p
    return None


def offline_ready_summary(path: Path | None = None) -> dict[str, Any]:
    rows = load(path)
    by_face: dict[str, dict[str, int]] = {}
    for p in rows:
        slot = by_face.setdefault(p.face, {"total": 0, "offline_ready": 0, "live_only": 0})
        slot["total"] += 1
        if p.offline_ready:
            slot["offline_ready"] += 1
        else:
            slot["live_only"] += 1
    return {
        "total": len(rows),
        "offline_ready": sum(1 for p in rows if p.offline_ready),
        "by_face": by_face,
        "top_ready": [
            p.to_dict()
            for p in sorted(rows, key=lambda x: (-x.confidence, x.ts))
            if p.offline_ready
        ][:8],
        "still_needs_api": [
            p.to_dict()
            for p in sorted(rows, key=lambda x: (x.confidence, x.ts))
            if not p.offline_ready
        ][:8],
    }
