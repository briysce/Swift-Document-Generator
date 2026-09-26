"""Meedo-Me's record of how the work was actually done, step by step.

The journal says what each unit of work achieved; episodes say the method that
solved a problem. Neither shows the procedure — the sequence of commands, files
read and edited, checks run, failures and retries — that an agent actually
followed. That is what Meedo-Me has to study to one day do the work itself,
so this keeps it: one compact line per tool call, per agent, per day, in
qa_logos/synthetic/meedo_traces/<agent>-<date>.jsonl.

Kept: the tool, its target (command, file, pattern, API), whether it failed,
and the agent's own short note of what it was doing. Not kept: file contents
(already in git) and the user's messages (they can carry pasted, private
material). Every string passes through `redact`: API keys (the values in the
local .env files and anything shaped like a key) and phone numbers never reach
the trace.

Two ways in, one format:

    # Claude Code hook (PostToolUse) — one line per call, as it happens
    python3 tools/meedo_me/trace.py hook --agent claude

    # From a session transcript — idempotent, safe to re-run
    python3 -m tools.meedo_me.trace import ~/.claude/projects/<proj>/<session>.jsonl --agent claude

    python3 -m tools.meedo_me.trace study [--agent claude]   # what the procedures look like
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRACES = ROOT / "qa_logos" / "synthetic" / "meedo_traces"
ENV_FILES = (ROOT / ".env.local", ROOT / ".env", ROOT / "tools" / "logo_vectorizer" / ".env", ROOT / "mobile" / ".env")

_KEYLIKE = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"\bAQ\.[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\b([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD))\s*[=:]\s*['\"]?[^\s'\"]{6,}"),
    re.compile(r"(?i)(x-api-key|x-goog-api-key|authorization)\s*:\s*[^\s'\"]+(?:\s+[^\s'\"]+)?"),
]
_PHONE = re.compile(r"(?<![\w.])\+?1?[\s\-.(]*\d{3}[\s\-.)]*\d{3}[\s\-.]*\d{4}(?![\w])")
_SECRET_VALUES: list[str] | None = None


def _secret_values() -> list[str]:
    global _SECRET_VALUES
    if _SECRET_VALUES is None:
        vals = []
        for f in ENV_FILES:
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    k, sep, v = line.partition("=")
                    v = v.strip().strip("'\"")
                    if sep and not k.strip().startswith("#") and len(v) >= 8:
                        vals.append(v)
            except OSError:
                continue
        _SECRET_VALUES = sorted(set(vals), key=len, reverse=True)
    return _SECRET_VALUES


def redact(text: str) -> str:
    text = str(text)
    for v in _secret_values():
        text = text.replace(v, "«secret»")
    for rx in _KEYLIKE:
        text = rx.sub(lambda m: (m.group(1) + "=«secret»") if m.lastindex else "«secret»", text)
    return _PHONE.sub("«phone»", text)


def _short(v, n: int = 300) -> str:
    s = " ".join(str(v).split())
    return redact(s if len(s) <= n else s[: n - 1] + "…")


def compact(tool: str, inp: dict) -> dict:
    """What a tool call did, without its payload."""
    inp = inp if isinstance(inp, dict) else {}
    if tool == "Bash":
        return {"cmd": _short(inp.get("command", ""), 500), "why": _short(inp.get("description", ""), 160),
                **({"background": True} if inp.get("run_in_background") else {})}
    if tool in ("Read", "NotebookRead"):
        return {"file": _short(inp.get("file_path", ""), 200)}
    if tool == "Write":
        return {"file": _short(inp.get("file_path", ""), 200), "chars": len(str(inp.get("content", "")))}
    if tool == "Edit":
        return {"file": _short(inp.get("file_path", ""), 200), "removed": len(str(inp.get("old_string", ""))),
                "added": len(str(inp.get("new_string", "")))}
    if tool in ("Grep", "Glob"):
        return {"pattern": _short(inp.get("pattern", ""), 200), "path": _short(inp.get("path", ""), 200)}
    out = {}
    for k, v in inp.items():
        if isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, str):
            out[k] = _short(v, 120) if len(v) <= 400 else f"«{len(v)} chars»"
        else:
            out[k] = f"«{type(v).__name__}»"
    return out


def _file_for(agent: str, ts: str) -> Path:
    return TRACES / f"{agent}-{ts[:10]}.jsonl"


def _ids_in(p: Path) -> set[str]:
    if not p.is_file():
        return set()
    out = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.add(json.loads(line)["id"])
        except (json.JSONDecodeError, KeyError):
            continue
    return out


def _append(agent: str, rows: list[dict]) -> int:
    by_file: dict[Path, list[dict]] = {}
    for r in rows:
        by_file.setdefault(_file_for(agent, r["ts"]), []).append(r)
    added = 0
    for p, rs in by_file.items():
        have = _ids_in(p)
        new = [r for r in rs if r["id"] not in have]
        if not new:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            for r in sorted(new, key=lambda r: r["ts"]):
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        added += len(new)
    return added


def from_transcript(path: Path, agent: str) -> int:
    """Every tool call in a Claude Code session transcript, with whether it
    failed and the note the agent wrote just before it."""
    calls: dict[str, dict] = {}
    failed: set[str] = set()
    note = ""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = (d.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            t = b.get("type")
            if d.get("type") == "assistant" and t == "text" and b.get("text", "").strip():
                note = b["text"]
            elif t == "tool_use":
                calls[b["id"]] = {"id": b["id"], "ts": (d.get("timestamp") or "")[:19] + "Z", "agent": agent,
                                  "tool": b.get("name", ""), **compact(b.get("name", ""), b.get("input") or {}),
                                  **({"note": _short(note, 400)} if note else {})}
                note = ""
            elif t == "tool_result" and b.get("is_error"):
                failed.add(b.get("tool_use_id", ""))
    for cid in failed & calls.keys():
        calls[cid]["failed"] = True
    return _append(agent, [c for c in calls.values() if len(c["ts"]) == 20])


def from_hook(event: dict, agent: str) -> int:
    from datetime import datetime, timezone

    resp = event.get("tool_response")
    bad = isinstance(resp, dict) and (resp.get("is_error") or resp.get("interrupted"))
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = {"id": event.get("tool_use_id") or f"{ts}-{event.get('tool_name', '')}", "ts": ts, "agent": agent,
           "tool": event.get("tool_name", ""), **compact(event.get("tool_name", ""), event.get("tool_input") or {}),
           **({"failed": True} if bad else {})}
    return _append(agent, [row])


def study(agent: str = "") -> dict:
    """What the procedures look like: which tools, which commands, which files,
    how often things failed — the shape of the work, per agent."""
    rows = []
    for p in sorted(TRACES.glob("*.jsonl")):
        if agent and not p.name.startswith(agent + "-"):
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    heads = Counter()
    for r in rows:
        if r.get("cmd"):
            first = r["cmd"].split("&&")[-1].strip().split()
            heads[" ".join(first[:3])] += 1
    return {
        "calls": len(rows),
        "days": sorted({r["ts"][:10] for r in rows}),
        "by_tool": Counter(r["tool"] for r in rows).most_common(15),
        "failed": sum(1 for r in rows if r.get("failed")),
        "commands": heads.most_common(20),
        "files_edited": Counter(r["file"] for r in rows if r.get("tool") in ("Edit", "Write") and r.get("file")).most_common(15),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    h = sub.add_parser("hook")
    h.add_argument("--agent", default="claude")
    i = sub.add_parser("import")
    i.add_argument("transcript", type=Path)
    i.add_argument("--agent", default="claude")
    s = sub.add_parser("study")
    s.add_argument("--agent", default="")
    a = ap.parse_args(argv)
    if a.command == "hook":
        try:  # a hook must never get in the way of the work it records
            from_hook(json.load(sys.stdin), a.agent)
        except Exception:
            pass
        return 0
    if a.command == "import":
        print(f"traced {from_transcript(a.transcript, a.agent)} new call(s)")
        return 0
    print(json.dumps(study(a.agent), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
