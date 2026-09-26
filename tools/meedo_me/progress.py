"""Meedo-Me's progress update: what the agents did in the last hour, in a
message short enough for a phone — delivered via Meedo's **fused** OpenClaw
runtime (``python -m tools.meedo_me.runtime``), not a global OpenClaw install.

Read from Meedo-Me's own memory — the work journal, the consultation memory,
the episodes — and from the commits pushed and the board, so the update says
what was actually done and checked, not what anyone meant to do. The memory is
read from the pushed branches (both agents', unioned), so the machine running
the fused runtime reports work pushed from anywhere, whatever it has checked out:

    python -m tools.meedo_me.progress --hours 1                 # print
    python -m tools.meedo_me.progress --agent claude --hours 1  # one agent
    python -m tools.meedo_me.progress --send                    # one-off send via fused OpenClaw
    python -m tools.meedo_me.progress register-hourly [--dry-run]

Hourly delivery is an OpenClaw command automation: it runs the generated
wrapper (fetch, then print the update) and OpenClaw's announce delivers the
printed text — one message, no model in the path. The number comes from
MEEDO_WHATSAPP_TO / OPENCLAW_WHATSAPP_TO in a gitignored .env; never commit it.
Both agents wrote a version of this on the same morning; this is the merge.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MEMORY = "qa_logos/synthetic"
CLAUDE_BRANCH = "claude/relaxed-babbage-igbk0v"
CURSOR_BRANCH = "cursor/logo-engine-collab-d4c9"
BRANCHES = {"claude": CLAUDE_BRANCH, "cursor": CURSOR_BRANCH}
COORD = ROOT / "COORDINATION.md"
WRAPPER = ROOT / "scripts" / "meedo_openclaw_hourly_progress.sh"
TZ = "America/Edmonton"
MAX_CHARS = 1800     # one WhatsApp screen or two; longer goes unread
PER_AGENT = 4        # lines per agent before the rest is only counted
ICON = {"done": "✅", "finding": "🔎", "blocked": "⛔", "handoff": "↪️", "claim": "▶️",
        "pause": "⏸", "resume": "⏯"}
NAMES = {"claude": "Claude Code", "cursor": "Cursor", "meedo": "Meedo-Me"}


def _load_env() -> None:
    try:
        from tools.ai_collab.env import load_env
    except Exception:
        from tools.logo_vectorizer.env_loader import load_env  # type: ignore
    try:
        load_env()
    except Exception:
        pass


def whatsapp_to() -> str:
    """E.164 target from env. Accepts NANP dashed forms and normalizes."""
    raw = (os.environ.get("MEEDO_WHATSAPP_TO") or os.environ.get("OPENCLAW_WHATSAPP_TO") or "").strip()
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("1") and len(digits) == 11:
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    if raw.startswith("+") and digits:
        return f"+{digits}"
    return raw


# --------------------------------------------------------------------------
# reading the memory
# --------------------------------------------------------------------------


def _git(*args: str, timeout: int = 60) -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                              timeout=timeout, check=True).stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def _fetch(branch: str) -> None:
    _git("fetch", "-q", "origin", branch)


def _read(name: str, ref: str = "") -> dict:
    """One of Meedo-Me's memory files, from the working tree or from `ref`."""
    rel = f"{MEMORY}/{name}"
    try:
        text = _git("show", f"{ref}:{rel}") if ref else (ROOT / rel).read_text(encoding="utf-8")
        return json.loads(text) if text.strip() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _union(name: str, key: str, refs: list[str]) -> dict:
    """A memory file as all known branches have it, unioned by id: the update
    is right whether or not the agents have merged each other yet."""
    items: dict[str, dict] = {}
    for ref in refs:
        for x in _read(name, ref).get(key, []):
            if x.get("id"):
                items.setdefault(x["id"], x)
    return {key: sorted(items.values(), key=lambda x: (x.get("ts", ""), x["id"]))}


def _parse(ts: str) -> datetime:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.fromtimestamp(0, timezone.utc)


def _parse_ts(ts: str) -> float:
    return _parse(ts).timestamp()


def _board_rows() -> list[dict]:
    if not COORD.is_file():
        return []
    rows = []
    for line in COORD.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or "Task" in line or re.match(r"^\|\s*-+", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4 or not cells[0].isdigit():
            continue
        rows.append({"n": int(cells[0]), "task": cells[1][:80], "owner": cells[2], "status": cells[3],
                     "notes": cells[4][:100] if len(cells) > 4 else ""})
    return rows


def collect(agent: str = "", hours: float = 1.0, *, refs: list[str] | None = None, fetch: bool = True,
            now: datetime | None = None) -> dict:
    """Everything the update says, as data."""
    now = now or datetime.now(timezone.utc)
    agent = (agent or "").strip().lower()
    if refs is None:
        refs = [""]
        for a, b in BRANCHES.items():
            if fetch:
                _fetch(b)
            if _git("rev-parse", "--verify", "-q", f"origin/{b}").strip():
                refs.append(f"origin/{b}")
    since = now - timedelta(hours=hours)
    journal = _union("meedo_journal.json", "entries", refs)
    consultations = _union("meedo_consultations.json", "consultations", refs)
    episodes = _union("meedo_episodes.json", "episodes", refs)
    commits: list[str] = []
    for a, b in BRANCHES.items():
        if agent and a != agent:
            continue
        ref = f"origin/{b}" if f"origin/{b}" in refs else ("HEAD" if a == "claude" else "")
        if ref:
            commits += [c for c in _git("log", ref, f"--since={since.isoformat()}", "--max-count=8",
                                        "--format=%h %s").splitlines() if c.strip() and c not in commits]
    board = _board_rows()
    return {
        "agent": agent or "all",
        "hours": hours,
        "now": now,
        "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "branch": BRANCHES.get(agent, "all branches"),
        "entries": journal["entries"],
        "consultations": consultations["consultations"],
        "episodes": episodes["episodes"],
        "commits": commits,
        "board": [r for r in board if not agent or agent in r["owner"].lower()],
    }


# --------------------------------------------------------------------------
# writing the update
# --------------------------------------------------------------------------


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
    return text[: n - 1].rsplit(" ", 1)[0].rstrip(" ,;:—-(") + "…"


def _rules(e: dict) -> str:
    if e.get("kind") != "done":
        return ""
    try:
        from tools.logo_vectorizer.meedo_journal import gaps

        missing = gaps(e)
    except Exception:
        return ""
    return " — house rules answered" if not missing else f" — missing {', '.join(missing)}"


def format_digest(data: dict) -> str:
    """Plain text for WhatsApp."""
    now = data.get("now") or _parse(data.get("ts", ""))
    hours = data.get("hours", 1)
    agent = data.get("agent") or "all"
    since = now - timedelta(hours=hours)
    who = f"{agent} progress" if agent != "all" else "progress"
    lines = [f"Meedo-Me · {who} {_local(now)} (last {hours:g}h)"]

    entries = data.get("entries")
    if entries is None:  # the shape Cursor's first version passed: this window's units only
        entries = [dict(e, ts=e.get("ts", data.get("ts", ""))) for e in data.get("journal") or []]
        since = datetime.fromtimestamp(0, timezone.utc)
    recent = [e for e in entries if _parse(e.get("ts", "")) >= since
              and (agent == "all" or e.get("agent", agent) == agent)]
    head_at = len(lines)
    shown = [e for e in recent if e.get("kind") != "claim"]
    by_agent: dict[str, list[dict]] = {}
    for e in shown:
        by_agent.setdefault(e.get("agent", agent), []).append(e)
    if not shown:
        lines.append("No units finished or findings logged this hour.")
    for a in sorted(by_agent, key=lambda a: (a != "claude", a)):
        lines.append(f"\n{NAMES.get(a, a)}:")
        # A busy hour: what was finished first, then what was found; the rest
        # is counted, not listed — a phone screen, not a log.
        units = sorted(by_agent[a], key=lambda e: (e.get("kind") != "done", e.get("ts", "")))
        for e in units[:PER_AGENT]:
            task = f"#{e['task']} " if e.get("task") not in (None, "") else ""
            lines.append(f"{ICON.get(e.get('kind'), '•')} {task}{_clip(e.get('summary', ''), 90)}{_rules(e)}")
        if len(units) > PER_AGENT:
            lines.append(f"  +{len(units) - PER_AGENT} more (python -m tools.logo_vectorizer.meedo_journal recent --agent {a})")

    try:
        from tools.logo_vectorizer.meedo_journal import open_claims

        held = open_claims(entries, now=now) if data.get("entries") is not None else []
    except Exception:
        held = []
    held = [c for c in held if agent == "all" or c["agent"] == agent]
    if held:
        lines.append("\nWorking on:")
        for c in held:
            quiet = f" — quiet {c['quiet_minutes']} min" if c["stale"] else ""
            lines.append(f"• #{c['task']} {c['agent']}: {_clip(c['summary'], 90)}{quiet}")

    board = [r for r in data.get("board") or [] if r["status"].startswith(("in progress", "blocked"))]
    if board:
        lines.append("Board: " + "; ".join(f"#{r['n']} {r['status'][:20]} ({r['owner']})" for r in board[:8]))

    thin = [e for e in recent if e.get("kind") == "done" and "missing" in _rules(e)]
    if thin:
        lines.append(f"Meedo-Me flags: {len(thin)} finished unit(s) skipped a house rule.")
    # The headline goes first, so a long hour loses detail, not the headline.
    summary: list[str] = []
    done = sum(1 for e in shown if e.get("kind") == "done")
    found = sum(1 for e in shown if e.get("kind") == "finding")
    if shown:
        summary.append(f"{done} unit(s) done, {found} finding(s).")
    commits = data.get("commits") or []
    if commits:
        summary.append(f"Pushed {len(commits)} commit(s): " + "; ".join(_clip(c, 70) for c in commits[:3])
                     + (" …" if len(commits) > 3 else ""))

    new_eps = [e for e in data.get("episodes") or [] if e.get("ts") and _parse(e["ts"]) >= since]
    if new_eps:
        summary.append("Learned: " + "; ".join(f"{e['id']} {_clip(e['problem'], 70)}" for e in new_eps[:3]))

    asked = [c for c in data.get("consultations") or [] if _parse(c.get("ts", "")) >= since]
    if asked:
        judged = [c for c in asked if c.get("outcome")]
        helped = sum(1 for c in judged if c["outcome"].get("result") == "helped")
        summary.append(f"Minds ({', '.join(sorted({c['mind'] for c in asked}))}): asked {len(asked)}, "
                     f"judged {len(judged)}, helped {helped}")

    lines[head_at:head_at] = summary
    text = "\n".join(lines)
    return text if len(text) <= MAX_CHARS else text[: MAX_CHARS - 1] + "…"


def digest(*, hours: float = 1.0, agent: str = "", now: datetime | None = None, journal: dict | None = None,
           consultations: dict | None = None, episodes: dict | None = None, commits: list[str] | None = None,
           refs: list[str] | None = None) -> str:
    """The update as text. The memory can be passed in (tests) or read."""
    if journal is None:
        data = collect(agent, hours, refs=refs, now=now)
    else:
        now = now or datetime.now(timezone.utc)
        data = {"agent": agent or "all", "hours": hours, "now": now, "entries": journal.get("entries", []),
                "consultations": (consultations or {}).get("consultations", []),
                "episodes": (episodes or {}).get("episodes", []), "commits": commits or [], "board": []}
    return format_digest(data)


def polish_with_ai(text: str, data: dict) -> str:
    """Optional Gemini+Claude polish for WhatsApp tone. Fail-open to the text."""
    try:
        from tools.ai_collab.advisor import advise

        _load_env()
        advice = advise(
            domain="meedo_progress",
            problem=("Rewrite this Meedo-Me progress digest as a concise WhatsApp update (≤1200 chars). "
                     "Keep all board #, statuses, and commit SHAs. No fluff. Return the message body only "
                     "in method or diagnosis."),
            context={"digest": text[:3500], "agent": data.get("agent")},
            tags=["openclaw", "whatsapp", "progress"],
            journal=False,
        )
        body = ((advice.method or advice.diagnosis or "") if advice is not None else "").strip()
        # Meedo studies the digest's shape so it can own the polish offline (Cursor).
        try:
            from tools.ai_collab.observe import observe

            observe(
                face="openclaw", domain="meedo_progress",
                tried="Polish hourly Claude/Cursor progress for WhatsApp",
                evidence=f"agent={data.get('agent')} hours={data.get('hours')}",
                method=("Headline first; ≤1800 chars; keep board #/status/SHA; no fluff; E.164 target from "
                        "MEEDO_WHATSAPP_TO in gitignored .env only."),
                outcome="success",
                steps=["collect(agent, hours) from both branches' memory + COORDINATION + git",
                       "format_digest → optional Gemini↔Claude polish",
                       "print; OpenClaw's announce delivers (or --send once)"],
                knobs={"max_chars": MAX_CHARS, "channel": "whatsapp",
                       "env_keys": ["MEEDO_WHATSAPP_TO", "OPENCLAW_WHATSAPP_TO"]},
                do_not_regress=["never commit phone numbers or API keys",
                                "fail-open to unpolished digest when APIs dark"],
                providers=list(getattr(advice, "providers_used", []) or []),
                tags=["whatsapp", "openclaw", "progress"], task=6, journal=False, lesson=True,
                episode=False, procedure=True, offline_ready=True, confidence=0.65, source="progress_polish",
            )
        except Exception:
            pass
        return body[:1500] + "\n" if len(body) >= 40 else text
    except Exception as exc:  # noqa: BLE001
        print(f"[progress] AI polish skipped: {exc}", file=sys.stderr)
        return text


# --------------------------------------------------------------------------
# delivery
# --------------------------------------------------------------------------


def send_whatsapp(message: str, *, to: str = "") -> tuple[bool, str]:
    """One-off delivery via fused OpenClaw ``message send``. Returns (ok, detail)."""
    target = to or whatsapp_to()
    if not target:
        return False, "MEEDO_WHATSAPP_TO / OPENCLAW_WHATSAPP_TO not set"
    try:
        from tools.meedo_me.runtime.launcher import openclaw_bin, openclaw_env, require_node
        oc = openclaw_bin(ensure=True)
        env = openclaw_env()
        node = require_node()
    except Exception as exc:  # noqa: BLE001
        return False, f"fused OpenClaw runtime unavailable ({exc}); run: python -m tools.meedo_me.runtime ensure"
    try:
        if oc.suffix in (".mjs", ".js") or oc.name.endswith(".mjs"):
            cmd = [node, str(oc), "message", "send", "--channel", "whatsapp", "--to", target,
                   "--message", message]
        else:
            cmd = [str(oc), "message", "send", "--channel", "whatsapp", "--to", target,
                   "--message", message]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "").strip()[:400] or f"exit {proc.returncode}"
    return True, (proc.stdout or "sent").strip()[:200]


def automation_command(target: str, *, agent: str = "", cron: str = "0 * * * *") -> list[str]:
    """OpenClaw automation via the fused Meedo runtime (never a global ``openclaw``)."""
    return [
        sys.executable, "-m", "tools.meedo_me.runtime", "openclaw",
        "automations", "create", cron,
        "--name", f"meedo-{agent or 'all'}-hourly-whatsapp",
        "--tz", TZ, "--command", str(WRAPPER), "--command-cwd", str(ROOT),
        "--announce", "--channel", "whatsapp", "--to", target,
    ]


def register_hourly(*, agent: str = "", every: str = "1h", dry_run: bool = False) -> str:
    """Write the wrapper and register the hourly automation (or print it where
    the fused runtime is not installed yet). The wrapper prints the update and
    does not send it itself: OpenClaw's announce delivers what it prints, so the
    user gets one message an hour, not two."""
    target = whatsapp_to()
    if not target:
        raise RuntimeError("Set MEEDO_WHATSAPP_TO=+1XXXXXXXXXX in gitignored .env before registering")
    agent_arg = f" --agent {agent}" if agent else ""
    WRAPPER.parent.mkdir(parents=True, exist_ok=True)
    WRAPPER.write_text(
        "#!/usr/bin/env bash\n"
        "# Generated by `python -m tools.meedo_me.progress register-hourly` — Meedo-Me hourly update.\n"
        "# Prints the update; OpenClaw's announce delivers it (one message).\n"
        "set -euo pipefail\n"
        f'ROOT="{ROOT}"\n'
        'cd "$ROOT"\n'
        'export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"\n'
        f"exec python3 -m tools.meedo_me.progress{agent_arg} --hours 1\n",
        encoding="utf-8",
    )
    WRAPPER.chmod(0o755)
    cron = "0 * * * *" if every in ("1h", "60m", "hourly") else f"*/{every.rstrip('m')} * * * *" if every.endswith("m") else "0 * * * *"
    cmd = automation_command(target, agent=agent, cron=cron)
    shown = " ".join(json.dumps(c) if " " in c or "*" in c else c for c in cmd)
    fused_ok = False
    try:
        from tools.meedo_me.runtime.launcher import openclaw_bin
        openclaw_bin(ensure=False)
        fused_ok = True
    except Exception:
        fused_ok = False
    if dry_run or not fused_ok:
        return (f"Wrapper written: {WRAPPER}\n"
                "Run after `python -m tools.meedo_me.runtime ensure` on the host "
                "where WhatsApp is linked "
                "(`python -m tools.meedo_me.runtime openclaw channels status --channel whatsapp --probe`):\n\n"
                + shown + "\n")
    # remove unused imports in register_hourly
    from tools.meedo_me.runtime.launcher import openclaw_env
    env = openclaw_env()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env, cwd=str(ROOT))
    if proc.returncode == 0:
        return (proc.stdout or "registered").strip()
    return f"register failed:\n{(proc.stderr or proc.stdout or '').strip()}\n\nTry manually:\n{shown}"


def run_once(*, agent: str = "", hours: float = 1.0, polish: bool = False, send: bool = False) -> str:
    data = collect(agent=agent, hours=hours)
    text = format_digest(data)
    if polish:
        text = polish_with_ai(text, data)
    if send:
        ok, detail = send_whatsapp(text)
        print(f"[progress] whatsapp {'ok' if ok else 'FAIL'}: {detail}", file=sys.stderr)
        if not ok:
            print(text)
            raise SystemExit(2)
    return text


def main(argv: list[str] | None = None) -> int:
    _load_env()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    reg = sub.add_parser("register-hourly", help="Register the OpenClaw hourly WhatsApp automation")
    reg.add_argument("--agent", default="")
    reg.add_argument("--every", default="1h")
    reg.add_argument("--dry-run", action="store_true")
    ap.add_argument("--agent", default="", help="only this agent (default: everyone)")
    ap.add_argument("--hours", type=float, default=1.0)
    ap.add_argument("--print", dest="do_print", action="store_true", default=True)
    ap.add_argument("--no-print", dest="do_print", action="store_false")
    ap.add_argument("--no-fetch", action="store_true", help="read the branches as last fetched")
    ap.add_argument("--send", action="store_true", help="send once via openclaw WhatsApp")
    ap.add_argument("--polish", action="store_true", help="polish with Gemini/Claude")
    ap.add_argument("--json", action="store_true", help="emit structured JSON")
    args = ap.parse_args(argv)

    if args.cmd == "register-hourly":
        print(register_hourly(agent=args.agent, every=args.every, dry_run=args.dry_run))
        return 0
    data = collect(agent=args.agent, hours=args.hours, fetch=not args.no_fetch)
    if args.json:
        print(json.dumps({k: v for k, v in data.items() if k != "now"}, indent=2, default=str))
        return 0
    text = format_digest(data)
    if args.polish:
        text = polish_with_ai(text, data)
    if args.send:
        ok, detail = send_whatsapp(text)
        print(f"[progress] whatsapp {'ok' if ok else 'FAIL'}: {detail}", file=sys.stderr)
        if args.do_print:
            print(text)
        return 0 if ok else 2
    if args.do_print:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
