"""Claude Code (and Cursor) progress digests for OpenClaw → WhatsApp.

Builds a short, WhatsApp-friendly update from Meedo journal + COORDINATION
board + recent git on Claude's branch. Optionally asks Gemini/Claude to
tighten wording. Delivery uses OpenClaw's WhatsApp channel when available:

    python -m tools.meedo_me.progress --agent claude --hours 1 --print
    python -m tools.meedo_me.progress --agent claude --hours 1 --send
    python -m tools.meedo_me.progress register-hourly   # OpenClaw cron

Phone number: ``MEEDO_WHATSAPP_TO`` / ``OPENCLAW_WHATSAPP_TO`` in gitignored
``.env`` as E.164 (e.g. +1XXXXXXXXXX). Never commit the number.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CLAUDE_BRANCH = "claude/relaxed-babbage-igbk0v"
CURSOR_BRANCH = "cursor/logo-engine-collab-d4c9"
COORD = ROOT / "COORDINATION.md"
WRAPPER = ROOT / "scripts" / "meedo_openclaw_hourly_progress.sh"


def _load_env() -> None:
    try:
        from tools.ai_collab.env import load_env
    except Exception:
        from tools.logo_vectorizer.env_loader import load_env  # type: ignore
    load_env()


def whatsapp_to() -> str:
    """E.164 target from env. Accepts NANP dashed forms and normalizes."""
    raw = (
        os.environ.get("MEEDO_WHATSAPP_TO")
        or os.environ.get("OPENCLAW_WHATSAPP_TO")
        or ""
    ).strip()
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


def _parse_ts(ts: str) -> float:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        ).timestamp()
    except Exception:
        return 0.0


def _board_rows() -> list[dict]:
    if not COORD.is_file():
        return []
    rows = []
    for line in COORD.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or "Task" in line or re.match(r"^\|\s*-+", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        num, task, owner, status = cells[0], cells[1], cells[2], cells[3]
        if not num.isdigit():
            continue
        rows.append(
            {
                "n": int(num),
                "task": task[:80],
                "owner": owner,
                "status": status,
                "notes": cells[4][:100] if len(cells) > 4 else "",
            }
        )
    return rows


def _git_log(branch: str, since_hours: float, limit: int = 8) -> list[str]:
    since = f"{max(1, int(since_hours * 60))} minutes ago"
    try:
        out = subprocess.check_output(
            [
                "git",
                "log",
                f"--since={since}",
                f"--max-count={limit}",
                "--pretty=format:%h %s",
                branch,
            ],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except Exception:
        return []
    return [ln for ln in out.splitlines() if ln.strip()]


def _fetch_branch(branch: str) -> None:
    try:
        subprocess.check_call(
            ["git", "fetch", "origin", branch, "--quiet"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
    except Exception:
        pass


def collect(agent: str = "claude", hours: float = 1.0) -> dict:
    """Structured progress for one agent over the last ``hours``."""
    from tools.logo_vectorizer import meedo_journal as J

    agent = (agent or "claude").strip().lower()
    now = time.time()
    cutoff = now - hours * 3600
    entries = [
        e
        for e in J.load().get("entries", [])
        if e.get("agent") == agent and _parse_ts(e.get("ts", "")) >= cutoff
    ]
    # Also include slightly older open claims for context.
    claims = [
        e
        for e in J.load().get("entries", [])
        if e.get("agent") == agent and e.get("kind") == "claim"
    ]
    latest_claim_by_task: dict[int, dict] = {}
    for e in claims:
        if e.get("task") is None:
            continue
        t = int(e["task"])
        if t not in latest_claim_by_task or _parse_ts(e.get("ts", "")) > _parse_ts(
            latest_claim_by_task[t].get("ts", "")
        ):
            latest_claim_by_task[t] = e

    board = _board_rows()
    if agent == "claude":
        owned = [r for r in board if "claude" in r["owner"].lower()]
        branch = CLAUDE_BRANCH
    elif agent == "cursor":
        owned = [r for r in board if "cursor" in r["owner"].lower()]
        branch = CURSOR_BRANCH
    else:
        owned = board
        branch = "HEAD"

    _fetch_branch(branch)
    commits = _git_log(f"origin/{branch}" if branch != "HEAD" else "HEAD", hours)

    return {
        "agent": agent,
        "hours": hours,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "branch": branch,
        "journal": entries[-12:],
        "active_claims": list(latest_claim_by_task.values()),
        "board": owned,
        "commits": commits,
    }


def format_digest(data: dict) -> str:
    """Plain-text WhatsApp body (keep short)."""
    agent = data["agent"]
    hours = data["hours"]
    lines = [
        f"Meedo-Me · {agent} progress (last {hours:g}h)",
        f"{data['ts']} · {data['branch']}",
        "",
    ]
    board = data.get("board") or []
    if board:
        lines.append("Board:")
        for r in board:
            lines.append(f"  #{r['n']} [{r['status']}] {r['task'][:60]}")
        lines.append("")
    journal = data.get("journal") or []
    if journal:
        lines.append("Journal:")
        for e in journal[-6:]:
            task = f"#{e['task']} " if e.get("task") is not None else ""
            lines.append(f"  [{e.get('kind')}] {task}{e.get('summary', '')[:90]}")
        lines.append("")
    else:
        lines.append("Journal: no new units in this window.")
        lines.append("")
    commits = data.get("commits") or []
    if commits:
        lines.append("Commits:")
        for c in commits[:6]:
            lines.append(f"  {c[:100]}")
    else:
        lines.append("Commits: none in this window.")
    return "\n".join(lines).strip() + "\n"


def polish_with_ai(digest: str, data: dict) -> str:
    """Optional Gemini+Claude polish for WhatsApp tone. Fail-open to digest."""
    try:
        from tools.ai_collab.advisor import advise
        from tools.ai_collab.env import load_env

        load_env()
        advice = advise(
            domain="meedo_progress",
            problem=(
                "Rewrite this Meedo-Me progress digest as a concise WhatsApp "
                "update (≤1200 chars). Keep all board #, statuses, and commit "
                "SHAs. No fluff. Return the message body only in method or diagnosis."
            ),
            context={"digest": digest[:3500], "agent": data.get("agent")},
            tags=["openclaw", "whatsapp", "progress"],
            journal=False,
        )
        if advice is None:
            return digest
        body = (advice.method or advice.diagnosis or "").strip()
        if len(body) < 40:
            return digest
        # Meedo studies WhatsApp digest shape so it can own polish offline.
        try:
            from tools.ai_collab.observe import observe

            observe(
                face="openclaw",
                domain="meedo_progress",
                tried="Polish hourly Claude/Cursor progress for WhatsApp",
                evidence=f"agent={data.get('agent')} hours={data.get('hours')}",
                method=(
                    "Keep board #/status/SHA; ≤1200 chars; no fluff; "
                    "E.164 target from MEEDO_WHATSAPP_TO in gitignored .env only."
                ),
                outcome="success",
                steps=[
                    "collect(agent, hours) from journal + COORDINATION + git",
                    "format_digest → optional Gemini↔Claude polish",
                    "openclaw message send --channel whatsapp --target $MEEDO_WHATSAPP_TO",
                ],
                knobs={
                    "max_chars": 1200,
                    "channel": "whatsapp",
                    "env_keys": ["MEEDO_WHATSAPP_TO", "OPENCLAW_WHATSAPP_TO"],
                },
                do_not_regress=[
                    "never commit phone numbers or API keys",
                    "fail-open to unpolished digest when APIs dark",
                ],
                providers=list(advice.providers_used),
                tags=["whatsapp", "openclaw", "progress"],
                task=6,
                journal=False,
                lesson=True,
                episode=False,
                procedure=True,
                offline_ready=True,
                confidence=0.65,
                source="progress_polish",
            )
        except Exception:
            pass
        return body[:1500] + ("\n" if not body.endswith("\n") else "")
    except Exception as exc:  # noqa: BLE001
        print(f"[progress] AI polish skipped: {exc}", file=sys.stderr)
        return digest


def send_whatsapp(message: str, *, to: str = "") -> tuple[bool, str]:
    """Deliver via ``openclaw message send``. Returns (ok, detail)."""
    target = to or whatsapp_to()
    if not target:
        return False, "MEEDO_WHATSAPP_TO / OPENCLAW_WHATSAPP_TO not set"
    oc = shutil.which("openclaw")
    if not oc:
        return False, "openclaw CLI not on PATH (install/link on the PC running WhatsApp)"
    try:
        proc = subprocess.run(
            [
                oc,
                "message",
                "send",
                "--channel",
                "whatsapp",
                "--to",
                target,
                "--message",
                message,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:400]
        return False, err or f"exit {proc.returncode}"
    return True, (proc.stdout or "sent").strip()[:200]


def register_hourly(
    *,
    agent: str = "claude",
    every: str = "1h",
    dry_run: bool = False,
) -> str:
    """Register OpenClaw automation: hourly progress → WhatsApp.

    Prefers a command payload that runs our script (deterministic), falling
    back to printing the exact CLI for the user when OpenClaw is absent.
    """
    target = whatsapp_to()
    if not target:
        raise RuntimeError(
            "Set MEEDO_WHATSAPP_TO=+1XXXXXXXXXX in gitignored .env before registering"
        )

    WRAPPER.parent.mkdir(parents=True, exist_ok=True)
    WRAPPER.write_text(
        "#!/usr/bin/env bash\n"
        f"# Generated — Meedo hourly progress for {agent}\n"
        "set -euo pipefail\n"
        f'ROOT="{ROOT}"\n'
        'cd "$ROOT"\n'
        'export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"\n'
        f'exec python3 -m tools.meedo_me.progress --agent {agent} --hours 1 --send --polish\n',
        encoding="utf-8",
    )
    WRAPPER.chmod(0o755)

    name = f"meedo-{agent}-hourly-whatsapp"
    # OpenClaw automations CLI (cron is an alias). Command payload runs our script;
    # announce still delivers any leftover agent text if the CLI shape differs.
    cmd = [
        "openclaw",
        "automations",
        "add",
        "--name",
        name,
        "--every",
        every,
        "--session",
        "isolated",
        "--announce",
        "--channel",
        "whatsapp",
        "--to",
        target,
        "--message",
        (
            f"Meedo-Me hourly: run `{WRAPPER}` (or "
            f"`python -m tools.meedo_me.progress --agent {agent} --hours 1 --send --polish`) "
            "and ensure the WhatsApp update was delivered. Summarize only if send failed."
        ),
    ]
    # Also try --command form when supported by the installed CLI.
    cmd_alt = [
        "openclaw",
        "automations",
        "add",
        "--name",
        name,
        "--every",
        every,
        "--session",
        "isolated",
        "--announce",
        "--channel",
        "whatsapp",
        "--to",
        target,
        "--command",
        str(WRAPPER),
    ]

    if dry_run or not shutil.which("openclaw"):
        return (
            "OpenClaw CLI not available here (expected on the PC with WhatsApp linked).\n"
            f"Wrapper written: {WRAPPER}\n"
            "Run on that machine after `openclaw channels status --probe` shows WhatsApp ok:\n\n"
            + " ".join(cmd_alt)
            + "\n\n# fallback agent-turn form:\n"
            + " ".join(cmd)
            + "\n"
        )

    for attempt in (cmd_alt, cmd):
        proc = subprocess.run(attempt, capture_output=True, text=True, timeout=60)
        if proc.returncode == 0:
            return (proc.stdout or "registered").strip()
        last_err = (proc.stderr or proc.stdout or "").strip()
    return f"register failed:\n{last_err}\n\nTry manually:\n{' '.join(cmd_alt)}"


def run_once(
    *,
    agent: str = "claude",
    hours: float = 1.0,
    polish: bool = False,
    send: bool = False,
) -> str:
    data = collect(agent=agent, hours=hours)
    digest = format_digest(data)
    if polish:
        digest = polish_with_ai(digest, data)
    if send:
        ok, detail = send_whatsapp(digest)
        print(f"[progress] whatsapp {'ok' if ok else 'FAIL'}: {detail}", file=sys.stderr)
        if not ok:
            # Always print digest so cron logs still capture the update.
            print(digest)
            raise SystemExit(2)
    return digest


def main(argv: list[str] | None = None) -> int:
    _load_env()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")

    reg = sub.add_parser("register-hourly", help="Register OpenClaw hourly WhatsApp job")
    reg.add_argument("--agent", default="claude")
    reg.add_argument("--every", default="1h")
    reg.add_argument("--dry-run", action="store_true")

    # default / once
    ap.add_argument("--agent", default="claude")
    ap.add_argument("--hours", type=float, default=1.0)
    ap.add_argument("--print", dest="do_print", action="store_true", default=True)
    ap.add_argument("--no-print", dest="do_print", action="store_false")
    ap.add_argument("--send", action="store_true", help="Send via openclaw WhatsApp")
    ap.add_argument("--polish", action="store_true", help="Polish with Gemini/Claude")
    ap.add_argument("--json", action="store_true", help="Emit structured JSON")

    args = ap.parse_args(argv)

    if args.cmd == "register-hourly":
        print(register_hourly(agent=args.agent, every=args.every, dry_run=args.dry_run))
        return 0

    data = collect(agent=args.agent, hours=args.hours)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    digest = format_digest(data)
    if args.polish:
        digest = polish_with_ai(digest, data)
    if args.send:
        ok, detail = send_whatsapp(digest)
        print(f"[progress] whatsapp {'ok' if ok else 'FAIL'}: {detail}", file=sys.stderr)
        if args.do_print:
            print(digest)
        return 0 if ok else 2
    if args.do_print:
        print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
