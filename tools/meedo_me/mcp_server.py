"""Meedo-Me's memory, served over MCP — one mind, any number of faces.

Meedo-Me's mind lives in this repository: the ledger (runs, proposals and the
decisions on them), the reviewer (verdicts on outputs), and the episodes (how
problems were actually solved). Its faces are several — the Meedo-Me app built
from Jan, Claude Code, agents like OpenClaw — and each would otherwise need its
own copy of that memory, which would drift. Served over the Model Context
Protocol, every one of them reads and writes the same memory.

Zero dependencies, on purpose. MCP over stdio is newline-delimited JSON-RPC
2.0, and a server needs four methods; the official SDK would bring a web stack
with it. This file is the whole server and can be read end to end. It is
tested for compliance against the official SDK's client, which stays a test
tool rather than becoming a dependency.

Read-only mode (--read-only, or MEEDO_READ_ONLY=1) removes every tool that can
change Meedo-Me's memory. Use it for any agent that reads untrusted input —
email, chat, web pages. Such an agent can be prompt-injected, and one that can
accept proposals or write episodes could then rewrite what Meedo-Me believes.

Files the reviewer reads must sit under this repository or a directory listed
in MEEDO_ALLOWED_DIRS (os.pathsep-separated). A reviewer that reads any path
it is handed is a file-reading primitive for whoever controls the client.

    python -m tools.meedo_me.mcp_server              # full access
    python -m tools.meedo_me.mcp_server --read-only  # for untrusted agents
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SERVER = {"name": "meedo-me", "version": "0.1.0"}
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = (
    "Meedo-Me is the project manager for our products: its memory of runs, the "
    "advice it has given and what was decided, the outputs it has reviewed, the "
    "methods that solved past problems, and the work journal of every agent unit "
    "(Claude Code and Cursor). Start a work session with meedo_standup and "
    "decide what it raises; also call meedo_journal_standup so stale claims and "
    "thin `done` entries are visible. Log every work unit with meedo_journal_log "
    "(claim / finding / handoff / done / blocked). Before diagnosing a problem, "
    "call meedo_recall — the same kind of problem has often been seen before, "
    "and the episode says what the first guess got wrong. Never report an "
    "improvement to a logo without meedo_review passing. When a problem is "
    "solved, record the method with meedo_record_episode so the next one is faster."
)


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [],
            "additionalProperties": False}


_S = {"type": "string"}
_SL = {"type": "array", "items": {"type": "string"}}

READ_TOOLS = {
    "meedo_standup": {
        "description": "Proposals awaiting a decision, most pressing first (escalated, then "
                       "priority), each with the method Meedo-Me remembers from a similar problem.",
        "inputSchema": _obj({}),
    },
    "meedo_recall": {
        "description": "Past problem-solving episodes most like a new problem: what it first "
                       "looked like, what evidence turned it, the cause, and the method.",
        "inputSchema": _obj({"problem": _S, "cases": _SL, "workstream": _S,
                             "top": {"type": "integer", "minimum": 1, "maximum": 10}},
                            ["problem"]),
    },
    "meedo_playbook": {
        "description": "Every method Meedo-Me has learned, with how each was earned.",
        "inputSchema": _obj({"workstream": _S}),
    },
    "meedo_report": {
        "description": "What Meedo-Me knows: run trend, how its advice has fared, what its "
                       "reviewer has blocked, cases that never moved, and every workstream.",
        "inputSchema": _obj({}),
    },
    "meedo_review": {
        "description": "Is a restored logo still the logo its sketch shows? Returns a verdict "
                       "(never a score): blocked if a brand colour was dropped or the mark "
                       "collapsed. Paths must be inside the repository or MEEDO_ALLOWED_DIRS.",
        "inputSchema": _obj({"output": _S, "sketch": _S, "raw": _S}, ["output", "sketch"]),
    },
    "meedo_journal_standup": {
        "description": "Work-journal assessment: recent units per agent, claims quiet ≥45 min, "
                       "and `done` entries missing house-rule evidence (tests, images, measure).",
        "inputSchema": _obj({}),
    },
    "meedo_journal_recent": {
        "description": "Recent work-journal units, optionally filtered by agent or task number.",
        "inputSchema": _obj({"agent": _S, "task": {"type": "integer"}, "limit": {"type": "integer"}}),
    },
}

WRITE_TOOLS = {
    "meedo_decide": {
        "description": "Accept or reject a standup proposal, with a reason. Accept only what "
                       "you will act on now — accepted advice is judged by later runs.",
        "inputSchema": _obj({"id": _S, "decision": {"type": "string", "enum": ["accept", "reject"]},
                             "reason": _S, "by": _S}, ["id", "decision", "reason"]),
    },
    "meedo_record_episode": {
        "description": "Record how a problem was solved so Meedo-Me carries the method forward. "
                       "A closed episode (success/failure/partial) needs a method.",
        "inputSchema": _obj({
            "problem": _S, "method": _S,
            "outcome": {"type": "string", "enum": ["success", "failure", "partial", "open"]},
            "workstream": _S, "first_read": _S, "evidence": _S, "cause": _S,
            "fix": _S, "verified": _S, "tags": _SL, "cases": _SL,
        }, ["problem", "outcome"]),
    },
    "meedo_journal_log": {
        "description": "Log one agent work unit into Meedo-Me's journal (claim/finding/handoff/"
                       "done/blocked) with optional evidence: commit, tests, review, images, measure.",
        "inputSchema": _obj({
            "agent": _S, "kind": {"type": "string", "enum": ["claim", "finding", "handoff", "done", "blocked"]},
            "summary": _S, "task": {"type": "integer"},
            "commit": _S, "tests": _S, "review": _S,
            "images_looked_at": {"type": "boolean"},
            "measured_vs_previous": _S, "branch": _S,
        }, ["agent", "kind", "summary"]),
    },
}


# MCP tool annotations: clients such as OpenClaw use them to decide which calls
# need a person's approval. Everything here acts only on Meedo-Me's own memory
# (closed world); the writers add decisions and episodes but delete nothing.
for _name, _spec in READ_TOOLS.items():
    _spec["annotations"] = {"readOnlyHint": True, "openWorldHint": False}
for _name, _spec in WRITE_TOOLS.items():
    _spec["annotations"] = {"readOnlyHint": False, "destructiveHint": False,
                            "idempotentHint": False, "openWorldHint": False}


def _allowed(path: str) -> Path:
    p = Path(path).expanduser()
    p = (p if p.is_absolute() else ROOT / p).resolve()  # relative to the repo, not the client's cwd
    roots = [ROOT] + [Path(d).expanduser().resolve()
                      for d in os.environ.get("MEEDO_ALLOWED_DIRS", "").split(os.pathsep) if d]
    if not any(p == r or r in p.parents for r in roots):
        raise PermissionError(f"{path} is outside the repository and MEEDO_ALLOWED_DIRS")
    if not p.is_file():
        raise FileNotFoundError(path)
    return p


def _clean(schema: dict, args: dict) -> dict:
    """Keep only declared arguments, and fix the shapes small local models get
    wrong: a bare string where a list belongs, a number sent as text. Seen in
    practice: a 0.6B model called meedo_standup with keys it had invented."""
    props = schema.get("properties", {})
    out = {}
    for k, v in (args if isinstance(args, dict) else {}).items():
        kind = props.get(k, {}).get("type")
        if kind is None:
            continue
        if kind == "array" and isinstance(v, str):
            v = [v]
        elif kind == "integer" and isinstance(v, str) and v.strip().isdigit():
            v = int(v)
        out[k] = v
    return out


def _call(name: str, args: dict, read_only: bool) -> object:
    from tools.logo_vectorizer import meedo_episodes as E
    from tools.logo_vectorizer import meedo_journal as J
    from tools.logo_vectorizer import meedo_ledger as L

    if name == "meedo_standup":
        out = []
        for p in L.standup():
            mem = next((e for e in E.recall(p["headline"] + " " + p.get("rationale", ""),
                                            cases=[p.get("case", "")], top=1)
                        if e.get("method")), None)
            out.append({k: p.get(k) for k in ("id", "priority", "raised", "escalated",
                                               "headline", "rationale", "case")}
                       | ({"remembered": {"id": mem["id"], "outcome": mem["outcome"],
                                          "method": mem["method"]}} if mem else {}))
        return out
    if name == "meedo_recall":
        return E.recall(args["problem"], cases=args.get("cases"),
                        workstream=args.get("workstream", ""), top=int(args.get("top", 3)))
    if name == "meedo_playbook":
        return E.playbook(workstream=args.get("workstream", ""))
    if name == "meedo_report":
        r = L.knowledge_report()
        r["workstreams"] = E.workstreams()
        r["journal"] = J.standup()
        return r
    if name == "meedo_review":
        from tools.logo_vectorizer.meedo_review import review

        extra = [_allowed(args["raw"])] if args.get("raw") else []
        rv = review(_allowed(args["output"]), _allowed(args["sketch"]), *extra)
        return {"passed": rv.passed, "summary": rv.summary(),
                "findings": [f.as_dict() for f in rv.findings]}
    if name == "meedo_journal_standup":
        return J.standup()
    if name == "meedo_journal_recent":
        task = args.get("task")
        return J.recent(
            agent=args.get("agent", ""),
            task=int(task) if task is not None else None,
            limit=int(args.get("limit", 12)),
        )
    if read_only:
        raise PermissionError(f"{name} changes Meedo-Me's memory and this server is read-only")
    if name == "meedo_decide":
        p = L.decide(args["id"], args["decision"], args["reason"], by=args.get("by", "mcp"))
        return {"id": p["id"], "status": p["status"], "reason": p["decision_reason"]}
    if name == "meedo_record_episode":
        keys = ("problem", "method", "outcome", "workstream", "first_read", "evidence",
                "cause", "fix", "verified", "tags", "cases")
        return E.record(source="mcp", **{k: args[k] for k in keys if k in args})
    if name == "meedo_journal_log":
        return J.log(
            agent=args["agent"],
            kind=args["kind"],
            summary=args["summary"],
            task=args.get("task"),
            commit=args.get("commit", "HEAD"),
            tests=args.get("tests", ""),
            review=args.get("review", ""),
            images_looked_at=args.get("images_looked_at"),
            measured_vs_previous=args.get("measured_vs_previous", ""),
            branch=args.get("branch", ""),
            source="mcp",
        )
    raise KeyError(name)


def _result(id_, result) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _error(id_, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def handle(msg: dict, read_only: bool) -> dict | None:
    """One JSON-RPC message in, at most one out. Notifications get no reply."""
    if not isinstance(msg, dict):
        return _error(None, -32600, "invalid request: expected one JSON-RPC object")
    method, id_ = msg.get("method"), msg.get("id")
    params = msg.get("params") or {}
    tools = dict(READ_TOOLS) if read_only else {**READ_TOOLS, **WRITE_TOOLS}

    if method == "initialize":
        asked = params.get("protocolVersion")
        return _result(id_, {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER,
            "instructions": INSTRUCTIONS + (" This server is read-only." if read_only else ""),
        })
    if method == "ping":
        return _result(id_, {})
    if method == "tools/list":
        return _result(id_, {"tools": [{"name": n, **spec} for n, spec in tools.items()]})
    if method == "tools/call":
        name = params.get("name", "")
        if name not in {**READ_TOOLS, **WRITE_TOOLS}:
            return _error(id_, -32602, f"unknown tool {name}")
        try:
            spec = {**READ_TOOLS, **WRITE_TOOLS}[name]["inputSchema"]
            out = _call(name, _clean(spec, params.get("arguments") or {}), read_only)
            return _result(id_, {"content": [{"type": "text",
                                              "text": json.dumps(out, indent=2, default=str)}],
                                 "isError": False})
        except Exception as e:  # a tool failure is a result the model can read, not a crash
            return _result(id_, {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}],
                                 "isError": True})
    if id_ is None:
        return None  # notifications/initialized and any other notification
    return _error(id_, -32601, f"method not found: {method}")


def serve(read_only: bool, stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    # stdout carries the protocol and nothing else. Anything the engine prints while a
    # tool runs would corrupt the stream, so ordinary prints go to stderr.
    before, sys.stdout = sys.stdout, sys.stderr
    try:
        _serve(read_only, stdin, stdout)
    finally:
        sys.stdout = before


def _serve(read_only: bool, stdin, stdout) -> None:
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = _error(None, -32700, "parse error")
        else:
            try:
                reply = handle(msg, read_only)
            except Exception:  # never let one bad message end the session
                traceback.print_exc(file=sys.stderr)
                reply = _error(msg.get("id") if isinstance(msg, dict) else None,
                               -32603, "internal error")
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    read_only = "--read-only" in argv or os.environ.get("MEEDO_READ_ONLY", "") in ("1", "true", "yes")
    serve(read_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
