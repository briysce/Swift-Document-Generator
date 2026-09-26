"""Meedo-Me work journal — every agent unit is logged, assessed, and recalled.

Claude Code and Cursor both work this repo. The user asked that Meedo-Me log,
hear, assess, and learn from everything either agent does. Episodes already
capture *how a problem was solved*; the ledger captures improve-loop runs and
advice. This journal captures the *work unit* itself: who claimed what, what
they found, what they handed off, and whether a `done` entry carries the
house-rule evidence (tests, review, images looked at, measured vs previous).

    python -m tools.logo_vectorizer.meedo_journal log \\
        --agent cursor --task 7 --kind finding \\
        --summary "…" --commit HEAD --tests "…" --images-looked-at 1

    python -m tools.logo_vectorizer.meedo_journal standup
    python -m tools.logo_vectorizer.meedo_journal recent --agent cursor

Stored in `qa_logos/synthetic/meedo_journal.json` and merged by the same
`merge=meedo` driver as episodes/ledger (see `meedo_merge.py`).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JOURNAL = ROOT / "qa_logos" / "synthetic" / "meedo_journal.json"

KINDS = ("claim", "finding", "handoff", "done", "blocked")
AGENTS = ("claude", "cursor", "human", "meedo")
STALE_SECONDS = 45 * 60  # COORDINATION.md take-over window


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_sha() -> str:
    try:
        import subprocess

        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
    except Exception:
        return ""


def load(path: Path | None = None) -> dict:
    p = path or JOURNAL
    if not p.is_file():
        return {"version": 1, "entries": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "entries": []}
    if not isinstance(data, dict):
        return {"version": 1, "entries": []}
    data.setdefault("version", 1)
    data.setdefault("entries", [])
    return data


def save(data: dict, path: Path | None = None) -> None:
    p = path or JOURNAL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _uid() -> str:
    return f"J{uuid.uuid4().hex[:8]}"


def log(
    *,
    agent: str,
    kind: str,
    summary: str,
    task: int | str | None = None,
    evidence: dict | None = None,
    commit: str = "",
    tests: str = "",
    review: str = "",
    images_looked_at: bool | int | None = None,
    measured_vs_previous: str = "",
    branch: str = "",
    source: str = "manual",
    path: Path | None = None,
) -> dict:
    """Append one work-unit entry."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    agent = (agent or "").strip().lower()
    if not agent:
        raise ValueError("agent is required")
    if not summary.strip():
        raise ValueError("summary is required")

    ev = dict(evidence or {})
    if commit:
        ev["commit"] = commit if commit != "HEAD" else _git_sha()
    elif "commit" not in ev:
        ev["commit"] = _git_sha()
    if tests:
        ev["tests"] = tests
    if review:
        ev["review"] = review
    if images_looked_at is not None:
        ev["images_looked_at"] = bool(images_looked_at)
    if measured_vs_previous:
        ev["measured_vs_previous"] = measured_vs_previous
    if branch:
        ev["branch"] = branch

    entry = {
        "id": _uid(),
        "ts": _now(),
        "agent": agent,
        "task": None if task in (None, "") else int(task),
        "kind": kind,
        "summary": summary.strip(),
        "evidence": ev,
        "source": source,
    }
    data = load(path)
    data["entries"].append(entry)
    save(data, path)

    # Done units without an episode leave Meedo blind — prompt strongly and
    # auto-record an open episode when a method string is present in evidence.
    if kind == "done":
        _prompt_episode_on_done(entry, path=path)

    return entry


def _prompt_episode_on_done(entry: dict, *, path: Path | None = None) -> None:
    """Fail-open: nudge / auto-capture method so Meedo studies agent dones."""
    ev = entry.get("evidence") or {}
    if ev.get("episode") or ev.get("episode_id"):
        return
    method = str(ev.get("method") or ev.get("episode_method") or "").strip()
    summary = entry.get("summary") or ""
    try:
        if method:
            from . import meedo_episodes as E

            ep = E.record(
                problem=summary[:400],
                method=method[:600],
                outcome="success",
                workstream="meedo-me" if entry.get("task") == 3 else "logo-engine",
                first_read=f"journal done by {entry.get('agent')}",
                evidence=str(ev.get("tests") or ev.get("measured_vs_previous") or "")[:400],
                verified=str(ev.get("tests") or "")[:200],
                tags=["journal_done", str(entry.get("agent") or "")],
                cases=[],
                source="journal_done_auto",
            )
            # Refresh evidence pointer on the journal entry.
            data = load(path)
            for e in data.get("entries") or []:
                if e.get("id") == entry.get("id"):
                    e.setdefault("evidence", {})["episode_id"] = ep.get("id")
                    e["evidence"]["episode_auto"] = True
                    break
            save(data, path)
            entry.setdefault("evidence", {})["episode_id"] = ep.get("id")
            print(
                f"[meedo_journal] done {entry.get('id')} → auto episode {ep.get('id')}",
                file=sys.stderr,
            )
        else:
            print(
                f"[meedo_journal] done {entry.get('id')} missing episode — "
                "record with meedo_record_episode or pass evidence.method / "
                "evidence.episode_id so Meedo studies the method.",
                file=sys.stderr,
            )
            # Soft flag for standup.
            entry.setdefault("evidence", {})["needs_episode"] = True
            data = load(path)
            for e in data.get("entries") or []:
                if e.get("id") == entry.get("id"):
                    e.setdefault("evidence", {})["needs_episode"] = True
                    break
            save(data, path)
    except Exception as exc:  # noqa: BLE001
        print(f"[meedo_journal] episode prompt skipped: {exc}", file=sys.stderr)


def recent(
    *,
    agent: str = "",
    task: int | None = None,
    limit: int = 12,
    path: Path | None = None,
) -> list[dict]:
    entries = load(path)["entries"]
    if agent:
        entries = [e for e in entries if e.get("agent") == agent.lower()]
    if task is not None:
        entries = [e for e in entries if e.get("task") == int(task)]
    return list(reversed(entries[-limit:]))


def _parse_ts(ts: str) -> float:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return 0.0


def _done_missing_evidence(e: dict) -> list[str]:
    """House rules for a `done` unit — what CLAUDE.md / COORDINATION require."""
    if e.get("kind") != "done":
        return []
    ev = e.get("evidence") or {}
    missing = []
    if not ev.get("commit"):
        missing.append("commit")
    if not ev.get("tests"):
        missing.append("tests")
    if "images_looked_at" not in ev:
        missing.append("images_looked_at")
    # measured_vs_previous is required for engine score claims; soft for docs-only.
    if e.get("task") in (4, 7, 8, 9, 10) and not ev.get("measured_vs_previous"):
        missing.append("measured_vs_previous")
    # Method capture: dones should leave an episode (or explicit method) for Meedo.
    if not ev.get("episode") and not ev.get("episode_id") and not ev.get("method"):
        if ev.get("needs_episode"):
            missing.append("episode")
    return missing


def standup(path: Path | None = None, now: float | None = None) -> dict:
    """Assess the journal for the next work cycle.

    Returns recent units per agent, stale claims (no follow-up push within
    45 minutes), and `done` entries missing house-rule evidence.
    """
    now = time.time() if now is None else now
    entries = load(path)["entries"]
    by_agent: dict[str, list[dict]] = {}
    for e in entries:
        by_agent.setdefault(e.get("agent", "?"), []).append(e)

    recent_by_agent = {
        a: list(reversed(es[-5:])) for a, es in sorted(by_agent.items())
    }

    # Latest claim per (agent, task); stale if no later entry from that agent
    # on the same task within STALE_SECONDS.
    latest_claim: dict[tuple[str, int], dict] = {}
    for e in entries:
        if e.get("kind") != "claim" or e.get("task") is None:
            continue
        latest_claim[(e["agent"], int(e["task"]))] = e

    stale = []
    for (agent, task), claim in latest_claim.items():
        later = [
            e
            for e in entries
            if e.get("agent") == agent
            and e.get("task") == task
            and e.get("id") != claim["id"]
            and _parse_ts(e.get("ts", "")) >= _parse_ts(claim.get("ts", ""))
        ]
        # A done/handoff clears the claim.
        if any(e.get("kind") in ("done", "handoff") for e in later):
            continue
        age = now - _parse_ts(claim.get("ts", ""))
        if age >= STALE_SECONDS and not later:
            stale.append(
                {
                    "agent": agent,
                    "task": task,
                    "claim_id": claim["id"],
                    "age_minutes": int(age // 60),
                    "summary": claim.get("summary", ""),
                }
            )
        elif age >= STALE_SECONDS and later:
            # Had findings but claim still open and quiet since last entry.
            last = max(later, key=lambda e: _parse_ts(e.get("ts", "")))
            quiet = now - _parse_ts(last.get("ts", ""))
            if quiet >= STALE_SECONDS:
                stale.append(
                    {
                        "agent": agent,
                        "task": task,
                        "claim_id": claim["id"],
                        "age_minutes": int(quiet // 60),
                        "summary": last.get("summary") or claim.get("summary", ""),
                        "last_kind": last.get("kind"),
                    }
                )

    thin_done = []
    for e in entries:
        miss = _done_missing_evidence(e)
        if miss:
            thin_done.append(
                {
                    "id": e["id"],
                    "agent": e.get("agent"),
                    "task": e.get("task"),
                    "summary": e.get("summary", ""),
                    "missing": miss,
                }
            )

    return {
        "recent_by_agent": recent_by_agent,
        "stale_claims": stale,
        "done_missing_evidence": thin_done[-10:],
        "entry_count": len(entries),
    }


def format_standup(report: dict) -> str:
    lines = [
        f"Meedo-Me journal standup — {report['entry_count']} unit(s) logged",
    ]
    for agent, es in report.get("recent_by_agent", {}).items():
        lines.append(f"  {agent}:")
        for e in es:
            task = f"#{e['task']} " if e.get("task") is not None else ""
            lines.append(f"    [{e['kind']}] {task}{e.get('summary', '')[:140]}")
    stale = report.get("stale_claims") or []
    if stale:
        lines.append("  stale claims (≥45 min quiet):")
        for s in stale:
            lines.append(
                f"    #{s['task']} {s['agent']} quiet {s['age_minutes']}m — {s.get('summary', '')[:100]}"
            )
    thin = report.get("done_missing_evidence") or []
    if thin:
        lines.append("  done entries missing house-rule evidence:")
        for d in thin:
            lines.append(
                f"    {d['id']} #{d.get('task')} {d.get('agent')}: missing {', '.join(d['missing'])}"
            )
    if not stale and not thin:
        lines.append("  no stale claims; no thin done entries")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Meedo-Me work journal")
    sub = p.add_subparsers(dest="cmd", required=True)

    log_p = sub.add_parser("log", help="append a work-unit entry")
    log_p.add_argument("--agent", required=True)
    log_p.add_argument("--kind", required=True, choices=KINDS)
    log_p.add_argument("--summary", required=True)
    log_p.add_argument("--task", default="")
    log_p.add_argument("--commit", default="HEAD")
    log_p.add_argument("--tests", default="")
    log_p.add_argument("--review", default="")
    log_p.add_argument("--images-looked-at", action="store_true")
    log_p.add_argument("--measured-vs-previous", default="")
    log_p.add_argument("--branch", default="")

    sub.add_parser("standup", help="assess journal: recent units, stale claims, thin dones")

    rec = sub.add_parser("recent", help="print recent units")
    rec.add_argument("--agent", default="")
    rec.add_argument("--task", type=int, default=None)
    rec.add_argument("--limit", type=int, default=12)

    a = p.parse_args(argv)

    if a.cmd == "log":
        e = log(
            agent=a.agent,
            kind=a.kind,
            summary=a.summary,
            task=a.task or None,
            commit=a.commit,
            tests=a.tests,
            review=a.review,
            images_looked_at=True if a.images_looked_at else None,
            measured_vs_previous=a.measured_vs_previous,
            branch=a.branch,
            source="cli",
        )
        print(f"{e['id']} logged: [{e['kind']}] task={e.get('task')} {e['summary'][:120]}")
        return 0

    if a.cmd == "standup":
        print(format_standup(standup()))
        return 0

    if a.cmd == "recent":
        for e in recent(agent=a.agent, task=a.task, limit=a.limit):
            task = f"#{e['task']} " if e.get("task") is not None else ""
            print(f"{e['ts']} {e['agent']:7s} [{e['kind']:7s}] {task}{e['summary']}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
