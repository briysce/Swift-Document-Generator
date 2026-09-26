"""Meedo-Me's progress update: what the agents did in the last hour, in a
message short enough for a phone.

Read from Meedo-Me's own memory — the work journal, the consultation memory,
the episodes — and from the commits pushed, so the update says what was
actually done and checked, not what anyone meant to do. It can read all of it
from a git ref, so a machine that only fetches (the PC running OpenClaw) can
report on work pushed from anywhere:

    python -m tools.meedo_me.progress --ref origin/claude/relaxed-babbage-igbk0v --hours 1

OpenClaw runs it every hour as a command automation and delivers the output
to WhatsApp (see `python -m tools.meedo_me.connect whatsapp`). No model is in
that path: the update is the memory's own account, not a paraphrase of it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MEMORY = "qa_logos/synthetic"
TZ = "America/Edmonton"
MAX_CHARS = 1800     # one WhatsApp screen or two; longer goes unread
ICON = {"done": "✅", "finding": "🔎", "blocked": "⛔", "handoff": "↪️", "claim": "▶️",
        "pause": "⏸", "resume": "⏯"}


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                              timeout=60, check=True).stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def _read(name: str, ref: str) -> dict:
    """One of Meedo-Me's memory files, from the working tree or from `ref`."""
    rel = f"{MEMORY}/{name}"
    try:
        text = _git("show", f"{ref}:{rel}") if ref else (ROOT / rel).read_text(encoding="utf-8")
        return json.loads(text) if text.strip() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _local(dt: datetime) -> str:
    try:
        from zoneinfo import ZoneInfo

        return dt.astimezone(ZoneInfo(TZ)).strftime("%H:%M")
    except Exception:
        return dt.strftime("%H:%M UTC")


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    if len(text) <= n:
        return text
    cut = text[: n - 1].rsplit(" ", 1)[0].rstrip(" ,;:—-(")
    return cut + "…"


def _evidence(e: dict) -> str:
    if e.get("kind") != "done":
        return ""
    ev = e.get("evidence") or {}
    missing = [k for k in ("tests", "looked", "review", "measured", "episode") if not ev.get(k)]
    return " — house rules answered" if not missing else f" — missing {', '.join(missing)}"


def digest(*, hours: float = 1.0, ref: str = "", agent: str = "", now: datetime | None = None,
           journal: dict | None = None, consultations: dict | None = None, episodes: dict | None = None,
           commits: list[str] | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    journal = _read("meedo_journal.json", ref) if journal is None else journal
    consultations = _read("meedo_consultations.json", ref) if consultations is None else consultations
    episodes = _read("meedo_episodes.json", ref) if episodes is None else episodes
    entries = journal.get("entries", [])
    if commits is None:
        commits = [c for c in _git("log", ref or "HEAD", f"--since={since.isoformat()}",
                                   "--format=%h %s").splitlines() if c.strip()]

    recent = [e for e in entries if _parse(e["ts"]) >= since and (not agent or e["agent"] == agent)]
    lines = [f"Meedo-Me · update {_local(now)} (last {hours:g}h)"]
    by_agent: dict[str, list[dict]] = {}
    for e in recent:
        by_agent.setdefault(e["agent"], []).append(e)
    order = sorted(by_agent, key=lambda a: (a != "claude", a))
    if not any(e["kind"] != "claim" for e in recent):
        lines.append("No units finished or findings logged this hour.")
    for a in order:
        if all(e["kind"] == "claim" for e in by_agent[a]):
            continue
        name = {"claude": "Claude Code", "cursor": "Cursor", "meedo": "Meedo-Me"}.get(a, a)
        lines.append(f"\n{name}:")
        for e in by_agent[a]:
            if e["kind"] == "claim":
                continue  # shown under "Working on" while held; its done line says the rest
            task = f"#{e['task']} " if e.get("task") else ""
            lines.append(f"{ICON.get(e['kind'], '•')} {task}{_clip(e['summary'], 110)}{_evidence(e)}")

    # What is held right now, and what went quiet.
    try:
        from tools.logo_vectorizer.meedo_journal import open_claims

        held = open_claims(entries, now=now)
    except Exception:
        held = []
    if held:
        lines.append("\nWorking on:")
        for c in held:
            if agent and c["agent"] != agent:
                continue
            quiet = f" — quiet {c['quiet_minutes']} min" if c["stale"] else ""
            lines.append(f"• #{c['task'] or '-'} {c['agent']}: {_clip(c['summary'], 90)}{quiet}")

    if commits:
        lines.append(f"\nPushed {len(commits)} commit(s): " + "; ".join(_clip(c.split(' ', 1)[-1], 70) for c in commits[:3])
                     + (" …" if len(commits) > 3 else ""))

    new_eps = [e for e in episodes.get("episodes", []) if e.get("ts") and _parse(e["ts"]) >= since]
    if new_eps:
        lines.append("Learned: " + "; ".join(f"{e['id']} {_clip(e['problem'], 70)}" for e in new_eps[:3]))

    asked = [c for c in consultations.get("consultations", []) if _parse(c["ts"]) >= since]
    if asked:
        judged = [c for c in asked if c.get("outcome")]
        helped = sum(1 for c in judged if c["outcome"].get("result") == "helped")
        minds = sorted({c["mind"] for c in asked})
        lines.append(f"Minds ({', '.join(minds)}): asked {len(asked)}, judged {len(judged)}, helped {helped}")

    gaps = [e for e in recent if e.get("kind") == "done" and any(
        not (e.get("evidence") or {}).get(k) for k in ("tests", "looked", "review", "measured", "episode"))]
    if gaps:
        lines.append(f"Meedo-Me flags: {len(gaps)} finished unit(s) skipped a house rule.")
    text = "\n".join(lines)
    return text if len(text) <= MAX_CHARS else text[: MAX_CHARS - 1] + "…"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--hours", type=float, default=1.0)
    ap.add_argument("--ref", default="", help="read Meedo-Me's memory from this git ref (fetch it first)")
    ap.add_argument("--agent", default="", help="only this agent's units")
    a = ap.parse_args(argv)
    print(digest(hours=a.hours, ref=a.ref, agent=a.agent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
