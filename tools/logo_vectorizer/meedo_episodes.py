"""Meedo-Me's episodes — how problems actually got solved.

Scores tell Meedo-Me what happened. Decisions tell it what its manager wanted.
Neither tells it how a problem was solved: which first guess was wrong, what
measurement turned it, which obvious fix would have quietly broken something.
That is the knowledge that makes the next problem faster, and it is the easiest
to lose — it lives in whoever solved it and in commit messages nobody re-reads.

An episode is one problem, from symptom to verified outcome:

    problem     what was observed
    first_read  what it first looked like — often wrong, which is the lesson
    evidence    the measurement that changed the picture
    cause       what was actually wrong
    fix         what was done about it
    verified    how that was confirmed
    method      the transferable part: what to do next time something looks
                like this. This is what Meedo-Me carries forward.
    outcome     success | failure | partial | open
    workstream  which line of work it belongs to — the engine, Meedo-Me,
                the app — so it can hold a picture across all of them

Episodes are recorded two ways. Some write themselves: when advice Meedo-Me
gave is judged, and when its reviewer blocks an output, the event becomes an
episode with no one having to remember to write it. The rest — the method
lessons, which need understanding rather than bookkeeping — are written by
whoever solved the problem.

And they come back when they are relevant. `recall` ranks past episodes against
a description of a new problem, and the standup shows, beside each proposal,
the episode most like it.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EPISODES = ROOT / "qa_logos" / "synthetic" / "meedo_episodes.json"
MAX_EPISODES = 1000

OUTCOMES = ("success", "failure", "partial", "open")
FIELDS = ("problem", "first_read", "evidence", "cause", "fix", "verified", "method")

_STOP = set(
    "a an the and or of to in on for with is was were be been it its this that "
    "as at by from into not no but so if then than there their they we i you "
    "what which when where how why all any can could would should will".split()
)


def load(path: Path | None = None) -> list[dict]:
    p = path or EPISODES
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data.get("episodes", []) if isinstance(data, dict) else []


def _save(episodes: list[dict], path: Path | None = None) -> None:
    p = path or EPISODES
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"version": 1, "episodes": episodes[-MAX_EPISODES:]}, indent=2) + "\n",
        encoding="utf-8",
    )


def _eid(episodes: list[dict]) -> str:
    return f"E{len(episodes) + 1:04d}"


def record(
    *,
    problem: str,
    method: str = "",
    outcome: str = "open",
    workstream: str = "logo-engine",
    first_read: str = "",
    evidence: str = "",
    cause: str = "",
    fix: str = "",
    verified: str = "",
    tags: list[str] | None = None,
    cases: list[str] | None = None,
    commit: str = "",
    source: str = "manual",
    path: Path | None = None,
) -> dict:
    """Write an episode into Meedo-Me's memory.

    A success without a method is a result, not a lesson; a failure without one
    is a regret. `method` may only be left empty for an episode that is still
    open — the problem has been seen and not yet understood.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}")
    if not problem.strip():
        raise ValueError("an episode needs a problem")
    if outcome != "open" and not method.strip():
        raise ValueError("a closed episode needs a method — what to do next time")
    try:
        from .meedo_ledger import _git_sha, _now

        ts, sha = _now(), (commit or _git_sha())
    except Exception:
        ts, sha = "", commit
    episodes = load(path)
    ep = {
        "id": _eid(episodes),
        "ts": ts,
        "commit": sha,
        "workstream": workstream,
        "outcome": outcome,
        "problem": problem.strip(),
        "first_read": first_read.strip(),
        "evidence": evidence.strip(),
        "cause": cause.strip(),
        "fix": fix.strip(),
        "verified": verified.strip(),
        "method": method.strip(),
        "tags": sorted(set(tags or [])),
        "cases": sorted(set(cases or [])),
        "source": source,
    }
    episodes.append(ep)
    _save(episodes, path)
    return ep


def retract(eid: str, reason: str, path: Path | None = None) -> dict:
    """Withdraw an episode that must not be taught — kept, marked, never
    recalled. Deleting it would not stick: the union merge brings it back."""
    if not reason.strip():
        raise ValueError("a retraction needs a reason")
    episodes = load(path)
    ep = next((e for e in episodes if e["id"] == eid), None)
    if ep is None:
        raise KeyError(eid)
    ep["retracted"] = reason.strip()
    _save(episodes, path)
    return ep


def close(eid: str, *, outcome: str, method: str, cause: str = "", fix: str = "",
          verified: str = "", path: Path | None = None) -> dict:
    """Finish an open episode once the problem is understood."""
    if outcome not in ("success", "failure", "partial"):
        raise ValueError("close with success, failure or partial")
    if not method.strip():
        raise ValueError("closing an episode needs a method")
    episodes = load(path)
    ep = next((e for e in episodes if e["id"] == eid), None)
    if ep is None:
        raise KeyError(eid)
    ep.update({"outcome": outcome, "method": method.strip()})
    for k, v in (("cause", cause), ("fix", fix), ("verified", verified)):
        if v.strip():
            ep[k] = v.strip()
    _save(episodes, path)
    return ep


# --------------------------------------------------------------------------
# recall
# --------------------------------------------------------------------------


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9_:]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in _STOP}


def recall(query: str, *, top: int = 3, workstream: str = "", cases: list[str] | None = None,
           path: Path | None = None) -> list[dict]:
    """Past episodes most like a new problem, most useful first.

    Ranked by overlap with the problem, cause and method, with a named case or
    tag counting for much more than a shared word — the same case having been
    here before is the strongest hint there is. Episodes with a method outrank
    open ones: something that was understood is worth more than something that
    was only seen.
    """
    q = _tokens(query)
    want_cases = set(cases or [])
    scored = []
    for ep in load(path):
        if ep.get("retracted") or (workstream and ep.get("workstream") != workstream):
            continue
        body = _tokens(" ".join(ep.get(f, "") for f in FIELDS))
        tags = {t.lower() for t in ep.get("tags", [])}
        score = 0.5 * len(q & body) + 2.0 * len(q & tags)
        score += 6.0 * len(want_cases & set(ep.get("cases", [])))
        score += 4.0 * sum(1 for c in ep.get("cases", []) if c.lower() in query.lower())
        if score < 1.0:
            continue
        if ep.get("method"):
            score *= 1.25
        scored.append((score, ep))
    scored.sort(key=lambda t: -t[0])
    return [ep for _, ep in scored[:top]]


def playbook(path: Path | None = None, workstream: str = "") -> list[dict]:
    """The methods Meedo-Me has learned, with how they were earned.

    One line per method, with the episodes behind it and how they turned out,
    so a method backed by five successes reads differently from one inferred
    from a single failure.
    """
    out = []
    for ep in load(path):
        if not ep.get("method") or ep.get("retracted") or (workstream and ep.get("workstream") != workstream):
            continue
        out.append({
            "id": ep["id"],
            "workstream": ep.get("workstream"),
            "outcome": ep.get("outcome"),
            "method": ep["method"],
            "tags": ep.get("tags", []),
        })
    return out


def workstreams(path: Path | None = None) -> dict:
    """Every line of work Meedo-Me has seen, with its record.

    This is the cross-product view: what has been worked on, how it went, and
    what is still open, across the engine, Meedo-Me itself and the app.
    """
    by: dict[str, Counter] = defaultdict(Counter)
    last: dict[str, str] = {}
    for ep in load(path):
        if ep.get("retracted"):
            continue
        ws = ep.get("workstream", "unknown")
        by[ws][ep.get("outcome", "open")] += 1
        last[ws] = max(last.get(ws, ""), ep.get("ts", ""))
    return {ws: {**dict(c), "total": sum(c.values()), "last": last.get(ws, "")}
            for ws, c in sorted(by.items())}


# --------------------------------------------------------------------------
# episodes that write themselves
# --------------------------------------------------------------------------


def from_judged_proposal(prop: dict, path: Path | None = None) -> dict | None:
    """Advice that was taken and judged becomes an episode automatically.

    This is how Meedo-Me learns from its own advice without anyone writing it
    down: what it suggested, why the suggestion was accepted, and what happened
    to the case afterwards.
    """
    status = prop.get("status")
    if status not in ("helped", "hurt", "no_change"):
        return None
    outcome = {"helped": "success", "hurt": "failure", "no_change": "failure"}[status]
    delta = prop.get("delta", 0.0)
    kind = prop.get("kind") or prop.get("headline", "")
    verb = {"helped": "helped", "hurt": "made it worse", "no_change": "did not move it"}[status]
    return record(
        problem=f"{prop.get('case')}: {kind}",
        first_read=prop.get("rationale", ""),
        fix=prop.get("decision_reason", ""),
        verified=f"judged on {prop.get('resolved_run')}: {delta:+.4f}",
        method=f"Advice of the kind '{kind}' {verb} on {prop.get('case')} ({delta:+.4f}).",
        outcome=outcome,
        tags=["advice", str(prop.get("case", "")).split("::")[-1]],
        cases=[str(prop.get("case", ""))],
        source="proposal",
        path=path,
    )


def from_review_block(case: str, candidate: str, findings: list[dict], path: Path | None = None) -> dict | None:
    """An output Meedo-Me blocked becomes an open problem in its memory.

    Deduplicated: the same case losing the same kind of thing again adds
    nothing new, so it is not written twice.
    """
    if not findings:
        return None
    checks = sorted({f.get("check", "") for f in findings})
    problem = f"{case}/{candidate}: blocked by review ({', '.join(checks)})"
    for ep in load(path):
        if ep.get("problem") == problem and ep.get("outcome") == "open":
            return None
    return record(
        problem=problem,
        evidence="; ".join(f.get("detail", "") for f in findings)[:600],
        outcome="open",
        tags=["review"] + checks,
        cases=[case],
        source="review",
        path=path,
    )


__all__ = [
    "EPISODES", "close", "from_judged_proposal", "from_review_block", "load",
    "playbook", "recall", "record", "workstreams",
]
