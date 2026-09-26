"""Meedo-Me's memory, served over MCP — one mind, any number of faces.

Meedo-Me's mind lives in this repository: the ledger (runs, proposals and the
decisions on them), the reviewer (verdicts on outputs), and the episodes (how
problems were actually solved). Its faces are several — the Meedo-Me desktop
app, Claude Code, Meedo messaging (WhatsApp/gateway) — and each would otherwise
need its own copy of that memory, which would drift. Served over the Model
Context Protocol, every one of them reads and writes the same memory.

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
    "(Claude Code and Cursor). Start a work session with meedo_cycle (ledger + "
    "journal + snapshot) and decide what it raises. Log every work unit with "
    "meedo_journal_log (claim / finding / handoff / done / blocked). Before "
    "diagnosing a problem, call meedo_recall — the same kind of problem has "
    "often been seen before, and the episode says what the first guess got "
    "wrong. Never report an improvement to a logo without meedo_review passing. "
    "When a problem is solved, record the method with meedo_record_episode. "
    "Claude Code and Cursor both refine Meedo-Me itself when tools are wrong, "
    "thin, or awkward — fix in-session and record under workstream meedo-me. "
    "Gemini and Claude APIs also advise via meedo_ai_advise; Meedo stores those "
    "lessons (meedo_ai_lessons) and prefers recalled offline advice when the "
    "same problem returns — the hand-off path toward Meedo owning decisions. "
    "Every question put to Gemini or Claude is kept with what its answer proved "
    "to be (meedo_minds): each mind's track record, and Meedo's readiness to "
    "answer in its place. "
    "Every face (Gemini/Claude/Serper/Cursor/Claude Code/Meedo messaging/improve loops) "
    "should record via observe → journal + lessons + episodes + procedures. "
    "Call meedo_study to see what APIs did that Meedo cannot yet own offline."
)


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [],
            "additionalProperties": False}


_S = {"type": "string"}
_SL = {"type": "array", "items": {"type": "string"}}

READ_TOOLS = {
    "meedo_cycle": {
        "description": "Full start-of-cycle view: ledger proposals awaiting decision, journal "
                       "assessment (recent units, stale claims, thin dones), and a knowledge snapshot. "
                       "Prefer this over calling standup tools separately.",
        "inputSchema": _obj({}),
    },
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
    "meedo_minds": {
        "description": "What Gemini and Claude have been asked, and what their answers proved to "
                       "be: track record per mind and role, disagreements the outcome settled, and "
                       "how often Meedo-Me's own shadow answers agreed with the right one.",
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
    "meedo_ai_lessons": {
        "description": "Recall lessons Meedo learned from Gemini+Claude collaborations. "
                       "Prefer this before asking the live APIs again — high-score hits mean "
                       "Meedo can own the decision offline.",
        "inputSchema": _obj({"problem": _S, "domain": _S, "cases": _SL,
                             "top": {"type": "integer", "minimum": 1, "maximum": 10}},
                            ["problem"]),
    },
    "meedo_procedures": {
        "description": "Recall fine-grained procedures Meedo studied from every face "
                       "(Serper query patterns, preprocess knobs, WhatsApp digest shape, "
                       "collab merge rules, brand must_keep). Prefer offline_ready hits.",
        "inputSchema": _obj({"query": _S, "domain": _S, "face": _S,
                             "top": {"type": "integer", "minimum": 1, "maximum": 10}},
                            ["query"]),
    },
    "meedo_study": {
        "description": "What faces (Gemini/Claude/Serper/Cursor/…) did that Meedo cannot "
                       "yet own offline — blind spots, live-dependent lessons/procedures, "
                       "journal dones missing episodes, offline-ready confidence.",
        "inputSchema": _obj({}),
    },
    "meedo_claude_progress": {
        "description": "Hourly-style progress digest for Claude Code (or cursor): board rows "
                       "they own, journal units in the window, and recent commits. Use for "
                       "Meedo messaging → WhatsApp updates. Returns text + structured fields.",
        "inputSchema": _obj({
            "agent": _S,
            "hours": {"type": "number"},
            "polish": {"type": "boolean"},
        }),
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
                       "done/blocked/pause/resume). A done answers each house rule in its evidence — "
                       "commit, tests, looked, review, measured, episode — with 'n/a: why' when one "
                       "does not apply.",
        "inputSchema": _obj({
            "agent": _S, "kind": {"type": "string", "enum": ["claim", "finding", "handoff", "done", "blocked",
                                                             "pause", "resume"]},
            "summary": _S, "task": {"type": "integer"},
            "commit": _S, "tests": _S, "review": _S, "looked": _S, "measured": _S, "episode": _S,
            "images_looked_at": {"type": "boolean"},
            "measured_vs_previous": _S, "branch": _S, "files": _SL,
        }, ["agent", "kind", "summary"]),
    },
    "meedo_ai_advise": {
        "description": "Ask Gemini then Claude (or Meedo's recalled AI lessons) for guidance on a "
                       "stuck problem. Persists structured lessons so Meedo can eventually own "
                       "similar decisions offline. Fail-open when APIs are dark.",
        "inputSchema": _obj({
            "problem": _S, "domain": _S, "cases": _SL, "tags": _SL,
            "force_live": {"type": "boolean"},
            "context_json": _S,
        }, ["problem"]),
    },
    "meedo_observe": {
        "description": "Record what any face tried (method/evidence/outcome/do-not-regress) into "
                       "Meedo's one memory: journal + ai_lessons + episodes + procedure playbook.",
        "inputSchema": _obj({
            "face": _S, "domain": _S, "tried": _S, "evidence": _S, "method": _S,
            "outcome": {"type": "string", "enum": ["success", "failure", "partial", "open"]},
            "steps": _SL, "do_not_regress": _SL, "tags": _SL, "cases": _SL,
            "task": {"type": "integer"},
        }, ["face", "tried"]),
    },
}


# MCP tool annotations: clients such as the Meedo messaging gateway use them to decide which calls
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

    if name == "meedo_cycle":
        from tools.logo_vectorizer.meedo_cycle import collect, format_cycle

        data = collect()
        data["text"] = format_cycle(data)
        return data
    if name == "meedo_claude_progress":
        from tools.meedo_me.progress import collect, format_digest, polish_with_ai

        agent = str(args.get("agent") or "claude")
        hours = float(args.get("hours") or 1.0)
        data = collect(agent=agent, hours=hours)
        text = format_digest(data)
        if args.get("polish"):
            text = polish_with_ai(text, data)
        data["text"] = text
        return data
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
        try:
            from tools.ai_collab.study import collect_study_report

            r["study"] = collect_study_report()
        except Exception:
            r["study"] = {}
        return r
    if name == "meedo_minds":
        from tools.logo_vectorizer import meedo_consult as C

        return C.report()
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
    if name == "meedo_ai_lessons":
        from tools.ai_collab.learn import recall_lessons

        return recall_lessons(
            args["problem"],
            domain=args.get("domain", ""),
            cases=args.get("cases"),
            top=int(args.get("top", 3)),
            mark_recalled=False,
        )
    if name == "meedo_procedures":
        from tools.ai_collab.procedures import recall_procedures

        return recall_procedures(
            args["query"],
            domain=args.get("domain", ""),
            face=args.get("face", ""),
            top=int(args.get("top", 5)),
            mark_recalled=False,
        )
    if name == "meedo_study":
        from tools.ai_collab.study import collect_study_report, format_study_report

        data = collect_study_report()
        data["text"] = format_study_report(data)
        return data
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
            looked=args.get("looked", ""),
            measured=args.get("measured", ""),
            episode=args.get("episode", ""),
            files=args.get("files"),
            source="mcp",
        )
    if name == "meedo_ai_advise":
        from tools.ai_collab.advisor import advise

        ctx = {}
        raw_ctx = args.get("context_json") or ""
        if raw_ctx:
            try:
                ctx = json.loads(raw_ctx) if isinstance(raw_ctx, str) else dict(raw_ctx)
            except (TypeError, ValueError, json.JSONDecodeError):
                ctx = {"note": str(raw_ctx)[:500]}
        advice = advise(
            domain=args.get("domain") or "general",
            problem=args["problem"],
            context=ctx,
            cases=args.get("cases"),
            tags=args.get("tags") or ["mcp"],
            force_live=bool(args.get("force_live", False)),
            journal=True,
        )
        return advice.to_dict() if advice else {"status": "unavailable", "fail_open": True}
    if name == "meedo_observe":
        from tools.ai_collab.observe import observe

        result = observe(
            face=args["face"],
            domain=args.get("domain") or "general",
            tried=args["tried"],
            evidence=args.get("evidence", ""),
            method=args.get("method", ""),
            outcome=args.get("outcome") or "open",
            steps=args.get("steps"),
            do_not_regress=args.get("do_not_regress"),
            tags=args.get("tags"),
            cases=args.get("cases"),
            task=args.get("task", 3),
            source="mcp",
        )
        return result.to_dict()
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
