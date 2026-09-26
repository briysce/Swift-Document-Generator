"""Meedo-Me work journal — every agent's unit of work, logged, assessed, recalled.

Claude Code and Cursor both work this repo, and the user asked that Meedo-Me
log, hear, assess and learn from everything either agent does. Episodes hold
*how a problem was solved*; the ledger holds improve-loop runs and advice.
This journal holds the *work unit* itself: who claimed what, what they found,
what they handed off, and whether a `done` answered the house rules.

Both agents wrote this module on the same morning (Cursor 13:19, Claude Code
13:32); this is the merge of the two, and it reads entries written by either:

  kinds     claim · finding · handoff · done · blocked · pause · resume
  evidence  one answer per house rule (CLAUDE.md), "n/a: <why>" when a rule
            does not apply:
              commit    what was pushed
              tests     what ran and passed
              looked    which outputs were looked at (also `images_looked_at`)
              review    Meedo-Me's reviewer on changed logo outputs
              measured  against the previous engine/state (also `measured_vs_previous`)
              episode   the episode recording the method, when a problem was solved

    python -m tools.logo_vectorizer.meedo_journal log --agent cursor --task 7 --kind done \\
        --summary "…" --tests "…" --looked "…" --review "…" --measured "…" --episode E00NN
    python -m tools.logo_vectorizer.meedo_journal standup
    python -m tools.logo_vectorizer.meedo_journal recent [--agent cursor] [--hours 3]
    python -m tools.logo_vectorizer.meedo_journal assess

Stored in `qa_logos/synthetic/meedo_journal.json`, merged by the `merge=meedo`
driver (union by id; see `meedo_merge.py`).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JOURNAL = ROOT / "qa_logos" / "synthetic" / "meedo_journal.json"

KINDS = ("claim", "finding", "handoff", "done", "blocked", "pause", "resume")
AGENTS = ("claude", "cursor", "human", "meedo")
RULES = ("commit", "tests", "looked", "review", "measured", "episode")
STALE_SECONDS = 45 * 60  # COORDINATION.md take-over window
STALE_AFTER = timedelta(seconds=STALE_SECONDS)
_CLOSES = ("done", "handoff", "blocked")


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> str:
    return _ts(_now_dt())


def _parse(ts: str) -> datetime:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.fromtimestamp(0, timezone.utc)


def _parse_ts(ts: str) -> float:
    return _parse(ts).timestamp()


def _as_dt(now) -> datetime:
    if now is None:
        return _now_dt()
    if isinstance(now, (int, float)):
        return datetime.fromtimestamp(now, timezone.utc)
    return now


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _task(task) -> int | str | None:
    if task in (None, ""):
        return None
    t = str(task).strip().lstrip("#")
    return int(t) if t.isdigit() else t


def _same_task(a, b) -> bool:
    return str(a) == str(b)


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------


def load(path: Path | None = None) -> dict:
    p = Path(path or JOURNAL)
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
    p = Path(path or JOURNAL)
    p.parent.mkdir(parents=True, exist_ok=True)
    data["entries"] = sorted(data.get("entries", []), key=lambda e: (e.get("ts", ""), e.get("id", "")))
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def evidence(e: dict) -> dict:
    """An entry's answers to the house rules, whichever agent's vocabulary it
    was written in."""
    ev = dict(e.get("evidence") or {})
    out = {k: str(ev.get(k, "")).strip() for k in ("tests", "review")}
    out["episode"] = str(ev.get("episode") or ev.get("episode_id") or "").strip()
    out["commit"] = str(e.get("commit") or ev.get("commit") or "").strip()
    looked = str(ev.get("looked", "")).strip()
    if not looked and ev.get("images_looked_at") is True:
        looked = "yes"
    out["looked"] = looked
    out["measured"] = str(ev.get("measured") or ev.get("measured_vs_previous") or "").strip()
    return out


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
    looked: str = "",
    measured: str = "",
    episode: str = "",
    images_looked_at: bool | int | None = None,
    measured_vs_previous: str = "",
    branch: str = "",
    files: list[str] | None = None,
    source: str = "manual",
    at: datetime | None = None,
    path: Path | None = None,
) -> dict:
    """Append one unit of work."""
    agent = (agent or "").strip().lower()
    if not agent:
        raise ValueError("agent is required")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if not (summary or "").strip():
        raise ValueError("summary is required")
    when = at or _now_dt()
    if when > _now_dt() + timedelta(minutes=1):
        # A backfill dated after "now" makes its agent look busy in the future
        # and every claim look fresh; it happened on the first backfill.
        raise ValueError(f"{_ts(when)} is in the future")

    ev = dict(evidence or {})
    if commit in ("", "HEAD"):
        ev.setdefault("commit", _git_sha())
    else:
        ev["commit"] = commit
    for k, v in (("tests", tests), ("review", review), ("looked", looked), ("measured", measured),
                 ("episode", episode), ("measured_vs_previous", measured_vs_previous), ("branch", branch)):
        if str(v).strip():
            ev[k] = str(v).strip()
    if images_looked_at is not None:
        ev["images_looked_at"] = bool(images_looked_at)

    data = load(path)
    # Two agents on two branches append at once: the id is the moment and the
    # agent, so the union never collides; one agent twice in a second gets a
    # suffix.
    base = eid = f"{_ts(when)}-{agent}"
    taken = {e.get("id") for e in data["entries"]}
    n = 1
    while eid in taken:
        n += 1
        eid = f"{base}-{n}"
    entry = {"id": eid, "ts": _ts(when), "agent": agent, "task": _task(task), "kind": kind,
             "summary": summary.strip(), "evidence": {k: v for k, v in ev.items() if v not in ("", None)},
             "source": source}
    if files:
        entry["files"] = sorted(set(files))
    data["entries"].append(entry)
    save(data, path)
    # Done units without an episode leave Meedo-Me blind to the method (Cursor):
    # record one from evidence.method, or flag the entry for the standup.
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


def recent(*, agent: str = "", task=None, limit: int | None = 12, hours: float | None = None,
           path: Path | None = None, now=None) -> list[dict]:
    """Newest first."""
    entries = load(path)["entries"]
    if agent:
        entries = [e for e in entries if e.get("agent") == agent.lower()]
    if task is not None:
        entries = [e for e in entries if _same_task(e.get("task"), _task(task))]
    if hours is not None:
        since = _as_dt(now) - timedelta(hours=hours)
        entries = [e for e in entries if _parse(e.get("ts", "")) >= since]
    entries = list(reversed(entries))
    return entries[:limit] if limit else entries


# --------------------------------------------------------------------------
# assessment
# --------------------------------------------------------------------------


def gaps(e: dict) -> list[str]:
    """House rules a `done` entry leaves unanswered."""
    if e.get("kind") != "done":
        return []
    ev = evidence(e)
    return [k for k in RULES if not ev.get(k)]


_done_missing_evidence = gaps  # Cursor's name


def open_claims(entries: list[dict], now=None) -> list[dict]:
    """Claims not yet closed by their holder, and whether each has gone quiet
    past the take-over window."""
    now = _as_dt(now)
    last_word: dict[str, str] = {}
    for e in entries:
        last_word[e["agent"]] = max(last_word.get(e["agent"], e["ts"]), e["ts"])
    latest: dict[tuple, dict] = {}
    for c in (e for e in entries if e.get("kind") == "claim"):
        latest[(c["agent"], str(c.get("task")))] = c
    out = []
    for c in latest.values():
        if any(e["agent"] == c["agent"] and _same_task(e.get("task"), c.get("task")) and e["kind"] in _CLOSES
               and e["ts"] >= c["ts"] for e in entries):
            continue
        # A later claim on the same task by another agent is a take-over
        # (COORDINATION.md rule 5): the task has one holder, the latest.
        if any(e.get("kind") == "claim" and e["agent"] != c["agent"] and _same_task(e.get("task"), c.get("task"))
               and e["ts"] > c["ts"] for e in entries):
            continue
        quiet = now - _parse(last_word[c["agent"]])
        minutes = int(quiet.total_seconds() // 60)
        out.append({"agent": c["agent"], "task": c.get("task"), "claim_id": c.get("id"), "since": c["ts"],
                    "summary": c.get("summary", ""), "quiet_minutes": minutes, "age_minutes": minutes,
                    "stale": quiet > STALE_AFTER})
    return sorted(out, key=lambda c: c["since"])


def assess(entries: list[dict] | None = None, *, path: Path | None = None, now=None) -> dict:
    """How each agent's work has gone: units finished, which house rules their
    finished units answered, what is still claimed, and what went quiet."""
    entries = load(path)["entries"] if entries is None else entries
    agents: dict[str, dict] = {}
    for e in entries:
        a = agents.setdefault(e.get("agent", "?"), {"done": 0, "complete": 0, "findings": 0, "blocked": 0,
                                                   "gaps": {k: 0 for k in RULES}, "incomplete": []})
        if e["kind"] == "finding":
            a["findings"] += 1
        elif e["kind"] == "blocked":
            a["blocked"] += 1
        elif e["kind"] == "done":
            a["done"] += 1
            g = gaps(e)
            if not g:
                a["complete"] += 1
            else:
                for k in g:
                    a["gaps"][k] += 1
                a["incomplete"].append({"id": e["id"], "task": e.get("task"), "summary": e["summary"], "missing": g})
    for a in agents.values():
        a["evidence_rate"] = round(a["complete"] / a["done"], 3) if a["done"] else None
        a["incomplete"] = a["incomplete"][-5:]
    return {"agents": agents, "open_claims": open_claims(entries, now)}


def standup(path: Path | None = None, now=None) -> dict:
    """Assess the journal for the next work cycle: recent units per agent,
    claims gone quiet, `done` entries missing house-rule evidence."""
    entries = load(path)["entries"]
    by_agent: dict[str, list[dict]] = {}
    for e in entries:
        by_agent.setdefault(e.get("agent", "?"), []).append(e)
    claims = open_claims(entries, now)
    thin = [{"id": e["id"], "agent": e.get("agent"), "task": e.get("task"), "summary": e.get("summary", ""),
             "missing": gaps(e)} for e in entries if gaps(e)]
    return {
        "recent_by_agent": {a: list(reversed(es[-5:])) for a, es in sorted(by_agent.items())},
        "open_claims": claims,
        "stale_claims": [c for c in claims if c["stale"]],
        "done_missing_evidence": thin[-10:],
        "agents": assess(entries, now=now)["agents"],
        "entry_count": len(entries),
    }


def format_standup(report: dict) -> str:
    lines = [f"Meedo-Me journal standup — {report.get('entry_count', 0)} unit(s) logged"]
    for c in report.get("open_claims") or []:
        flag = "STALE — may be taken over" if c["stale"] else f"quiet {c['quiet_minutes']} min"
        task = c["task"] if c["task"] is not None else "-"
        lines.append(f"  #{task} held by {c['agent']} since {c['since']} ({flag}): {c['summary'][:90]}")
    for name, a in sorted((report.get("agents") or {}).items()):
        if a["done"]:
            lines.append(f"  {name}: {a['done']} unit(s) done, {a['complete']} with every house rule answered")
    thin = report.get("done_missing_evidence") or []
    for d in thin[-5:]:
        lines.append(f"    {d['id']} #{d.get('task')} {d.get('agent')}: missing {', '.join(d['missing'])}")
    if not report.get("open_claims") and not thin:
        lines.append("  no open claims; no thin done entries")
    return "\n".join(lines)


def standup_lines(path: Path | None = None, now=None) -> list[str]:
    """The journal's part of the ledger standup."""
    if not load(path)["entries"]:
        return []
    return ["Work journal:"] + format_standup(standup(path, now)).splitlines()[1:]


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Meedo-Me work journal")
    sub = p.add_subparsers(dest="cmd", required=True)

    lg = sub.add_parser("log", help="append a work-unit entry")
    lg.add_argument("text", nargs="?", default="", help="the summary (or --summary)")
    lg.add_argument("--summary", default="")
    lg.add_argument("--agent", required=True)
    lg.add_argument("--kind", required=True, choices=KINDS)
    lg.add_argument("--task", default="")
    lg.add_argument("--commit", default="HEAD")
    lg.add_argument("--branch", default="")
    lg.add_argument("--files", nargs="*", default=None)
    for k in ("tests", "review", "looked", "measured", "episode"):
        lg.add_argument(f"--{k}", default="")
    lg.add_argument("--images-looked-at", action="store_true")
    lg.add_argument("--measured-vs-previous", default="")

    sub.add_parser("standup", help="recent units, open and stale claims, thin dones")
    sub.add_parser("assess", help="per-agent assessment as JSON")
    rec = sub.add_parser("recent", help="print recent units")
    rec.add_argument("--agent", default="")
    rec.add_argument("--task", default=None)
    rec.add_argument("--limit", type=int, default=12)
    rec.add_argument("--hours", type=float, default=None)
    a = p.parse_args(argv)

    if a.cmd == "log":
        e = log(agent=a.agent, kind=a.kind, summary=a.summary or a.text, task=a.task or None, commit=a.commit,
                tests=a.tests, review=a.review, looked=a.looked, measured=a.measured, episode=a.episode,
                images_looked_at=True if a.images_looked_at else None,
                measured_vs_previous=a.measured_vs_previous, branch=a.branch, files=a.files, source="cli")
        g = gaps(e)
        print(f"{e['id']} logged: [{e['kind']}] task={e.get('task')} {e['summary'][:100]}"
              + (f" — unanswered house rules: {', '.join(g)}" if g else ""))
        return 0
    if a.cmd == "standup":
        print(format_standup(standup()))
        return 0
    if a.cmd == "assess":
        print(json.dumps(assess(), indent=2))
        return 0
    for e in reversed(recent(agent=a.agent, task=a.task, limit=None if a.hours else a.limit, hours=a.hours)):
        task = f"#{e['task']} " if e.get("task") is not None else ""
        print(f"{e['ts']} {e['agent']:7s} [{e['kind']:7s}] {task}{e['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
