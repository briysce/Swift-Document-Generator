"""Meedo-Me's work journal: what every agent did, and whether it was done right.

Claude Code and Cursor work this repository at the same time. Meedo-Me's other
memory holds engine runs, advice and decisions, reviews, and the methods that
solved problems; none of it holds the work itself: who took which task, what
they found, what they finished, and whether they kept the house rules doing it.
This does, so Meedo-Me can manage the work and not only the engine.

An entry is one unit of work, by one agent:

  claim     starting a board task (COORDINATION.md)
  finding   something learned on the way: a cause, a false alarm, a measurement
  done      a unit finished and pushed
  handoff   stopping mid-task; `summary` is where the next agent starts
  blocked   cannot go on; `summary` says on what
  pause / resume   credits ran out / came back

A `done` carries its evidence, one field per house rule (CLAUDE.md):

  tests     what ran and passed
  looked    which outputs were looked at
  review    Meedo-Me's reviewer verdict on changed logo outputs
  measured  the change against the previous engine / previous state
  episode   the episode that records the method, when a problem was solved

"n/a: <why>" answers a rule that does not apply. An empty field is a gap.
Nothing is refused for a gap: the assessment lists gaps per agent in the
standup, so the pattern is visible and Meedo-Me can say whose units skip what.

    python -m tools.logo_vectorizer.meedo_journal log --agent claude --task 1 --kind done \\
        --tests "..." --looked "..." --review passed --measured "..." --episode E0026 "summary"
    python -m tools.logo_vectorizer.meedo_journal recent [--hours 24] [--agent cursor]
    python -m tools.logo_vectorizer.meedo_journal assess
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JOURNAL = ROOT / "qa_logos" / "synthetic" / "meedo_journal.json"

KINDS = ("claim", "finding", "done", "handoff", "blocked", "pause", "resume")
EVIDENCE = ("tests", "looked", "review", "measured", "episode")
# A claim whose holder has written nothing for this long may be taken over
# (COORDINATION.md, rule 5).
STALE_AFTER = timedelta(minutes=45)
_CLOSES = ("done", "handoff", "blocked")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def load(path: Path | None = None) -> list[dict]:
    p = Path(path or JOURNAL)
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("entries", [])
    except (OSError, json.JSONDecodeError):
        return []


def _save(entries: list[dict], path: Path | None = None) -> None:
    p = Path(path or JOURNAL)
    p.parent.mkdir(parents=True, exist_ok=True)
    entries = sorted(entries, key=lambda e: (e["ts"], e["id"]))
    p.write_text(json.dumps({"version": 1, "entries": entries}, indent=2) + "\n", encoding="utf-8")


def log(
    *,
    agent: str,
    kind: str,
    summary: str,
    task: str = "",
    commit: str = "",
    files: list[str] | None = None,
    at: datetime | None = None,
    path: Path | None = None,
    **evidence: str,
) -> dict:
    """Append one unit of work. `evidence` takes the EVIDENCE fields."""
    agent = agent.strip().lower()
    if not agent:
        raise ValueError("an entry needs the agent that did the work")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if not summary.strip():
        raise ValueError("an entry needs a summary")
    unknown = set(evidence) - set(EVIDENCE)
    if unknown:
        raise ValueError(f"unknown evidence {sorted(unknown)}; use {EVIDENCE}")
    if not commit:
        try:
            from .meedo_ledger import _git_sha

            commit = _git_sha()
        except Exception:
            commit = ""
    when = at or _now()
    if when > _now() + timedelta(minutes=1):
        # A backfill dated after "now" makes its agent look busy in the future
        # and every claim look fresh; it happened on the first backfill.
        raise ValueError(f"{_ts(when)} is in the future")
    entries = load(path)
    # Two agents on two branches append at once: the id must not collide, so
    # it is the agent and the moment, not a counter; one agent logging twice
    # in a second gets a suffix.
    base = eid = f"{_ts(when)}-{agent}"
    taken = {e["id"] for e in entries}
    n = 1
    while eid in taken:
        n += 1
        eid = f"{base}-{n}"
    entry = {
        "id": eid,
        "ts": _ts(when),
        "agent": agent,
        "task": str(task).strip().lstrip("#"),
        "kind": kind,
        "summary": summary.strip(),
        "commit": commit,
    }
    if files:
        entry["files"] = sorted(set(files))
    ev = {k: str(v).strip() for k, v in evidence.items() if str(v).strip()}
    if ev:
        entry["evidence"] = ev
    entries.append(entry)
    _save(entries, path)
    return entry


def recent(*, hours: float = 24, agent: str = "", path: Path | None = None, now: datetime | None = None) -> list[dict]:
    since = (now or _now()) - timedelta(hours=hours)
    return [e for e in load(path)
            if _parse(e["ts"]) >= since and (not agent or e["agent"] == agent.lower())]


def gaps(entry: dict) -> list[str]:
    """House rules a `done` entry leaves unanswered."""
    if entry.get("kind") != "done":
        return []
    ev = entry.get("evidence", {})
    return [k for k in EVIDENCE if not ev.get(k)]


def open_claims(entries: list[dict], now: datetime | None = None) -> list[dict]:
    """Claims not yet closed by their holder, with whether each has gone stale."""
    now = now or _now()
    last_word = {}
    for e in entries:
        last_word[e["agent"]] = max(last_word.get(e["agent"], e["ts"]), e["ts"])
    out = []
    for c in (e for e in entries if e["kind"] == "claim"):
        closed = any(e["agent"] == c["agent"] and e["task"] == c["task"] and e["kind"] in _CLOSES
                     and e["ts"] >= c["ts"] for e in entries)
        if closed:
            continue
        quiet = now - _parse(last_word[c["agent"]])
        out.append({"agent": c["agent"], "task": c["task"], "since": c["ts"], "summary": c["summary"],
                    "quiet_minutes": int(quiet.total_seconds() // 60), "stale": quiet > STALE_AFTER})
    return out


def assess(entries: list[dict] | None = None, *, path: Path | None = None, now: datetime | None = None) -> dict:
    """How each agent's work has gone: units finished, which house rules their
    finished units answered, what is still claimed, and what went quiet."""
    entries = load(path) if entries is None else entries
    agents: dict[str, dict] = {}
    for e in entries:
        a = agents.setdefault(e["agent"], {"done": 0, "complete": 0, "findings": 0, "blocked": 0,
                                           "gaps": {k: 0 for k in EVIDENCE}, "incomplete": []})
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
                a["incomplete"].append({"id": e["id"], "task": e["task"], "summary": e["summary"], "missing": g})
    for a in agents.values():
        a["evidence_rate"] = round(a["complete"] / a["done"], 3) if a["done"] else None
        a["incomplete"] = a["incomplete"][-5:]
    return {"agents": agents, "open_claims": open_claims(entries, now)}


def standup_lines(path: Path | None = None, now: datetime | None = None) -> list[str]:
    """The journal's part of the standup: what is held, what went quiet, and
    which finished units skipped a house rule."""
    entries = load(path)
    if not entries:
        return []
    r = assess(entries, now=now)
    lines = ["Work journal:"]
    for c in r["open_claims"]:
        flag = "STALE — may be taken over" if c["stale"] else f"quiet {c['quiet_minutes']} min"
        lines.append(f"  #{c['task'] or '-'} held by {c['agent']} since {c['since']} ({flag}): {c['summary'][:90]}")
    for name, a in sorted(r["agents"].items()):
        if a["done"]:
            lines.append(f"  {name}: {a['done']} unit(s) done, {a['complete']} with every house rule answered")
        for u in a["incomplete"][-3:]:
            lines.append(f"    {u['id']} #{u['task'] or '-'} missing {', '.join(u['missing'])}: {u['summary'][:70]}")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    lg = sub.add_parser("log", help="record a unit of work")
    lg.add_argument("summary")
    lg.add_argument("--agent", required=True)
    lg.add_argument("--kind", required=True, choices=KINDS)
    lg.add_argument("--task", default="")
    lg.add_argument("--commit", default="")
    lg.add_argument("--files", nargs="*", default=None)
    for k in EVIDENCE:
        lg.add_argument(f"--{k}", default="")
    rc = sub.add_parser("recent", help="the timeline")
    rc.add_argument("--hours", type=float, default=24)
    rc.add_argument("--agent", default="")
    sub.add_parser("assess", help="per-agent assessment")
    a = ap.parse_args(argv)

    if a.command == "log":
        e = log(agent=a.agent, kind=a.kind, summary=a.summary, task=a.task, commit=a.commit,
                files=a.files, **{k: getattr(a, k) for k in EVIDENCE})
        g = gaps(e)
        print(f"logged {e['id']}" + (f" — unanswered house rules: {', '.join(g)}" if g else ""))
        return 0
    if a.command == "recent":
        for e in recent(hours=a.hours, agent=a.agent):
            print(f"{e['ts']} {e['agent']:<7} {e['kind']:<8} #{e['task'] or '-':<3} {e['summary']}")
        return 0
    print(json.dumps(assess(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
