"""Meedo-Me's MCP server: speaks the protocol, and cannot be turned against its memory."""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

from tools.logo_vectorizer import meedo_episodes as E
from tools.logo_vectorizer import meedo_ledger as L
from tools.logo_vectorizer.meedo_advisor import Run, Suggestion
from tools.meedo_me import mcp_server as S


def _call(name, args, read_only=False):
    r = S.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                  "params": {"name": name, "arguments": args}}, read_only)
    return r["result"]["isError"], r["result"]["content"][0]["text"]


def _memory(tmp_path, monkeypatch):
    """Point the server at a throwaway ledger and episode file, never the real ones."""
    monkeypatch.setattr(L, "LEDGER", tmp_path / "meedo_ledger.json")
    monkeypatch.setattr(E, "EPISODES", tmp_path / "meedo_episodes.json")
    run = lambda i, v: Run(f"r{i}", [{"run_id": f"r{i}", "pair_id": "p", "engine": "vectorize",
                                      "ok": True, "composite": v}])
    L.observe([run(0, 0.5)])
    L.propose([Suggestion(1, "Fix p::vectorize", "because")], "r0")
    return L.load()["proposals"][0]["id"]


def test_handshake_agrees_on_a_version_the_client_speaks():
    for asked in ("2024-11-05", "2025-06-18"):
        r = S.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": asked}}, False)["result"]
        assert r["protocolVersion"] == asked and r["serverInfo"]["name"] == "meedo-me"
    newer = S.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2099-01-01"}}, False)["result"]
    assert newer["protocolVersion"] == S.PROTOCOL_VERSIONS[0]


def test_notifications_get_no_reply_and_unknown_methods_an_error():
    assert S.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, False) is None
    assert S.handle({"jsonrpc": "2.0", "id": 2, "method": "resources/list"}, False)["error"]["code"] == -32601
    assert S.handle([{"id": 3}], False)["error"]["code"] == -32600
    assert S.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                     "params": {"name": "rm_rf"}}, False)["error"]["code"] == -32602


def test_read_only_offers_no_tool_that_writes():
    listed = lambda ro: {t["name"] for t in S.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, ro)["result"]["tools"]}
    assert listed(True) == set(S.READ_TOOLS)
    assert listed(False) == set(S.READ_TOOLS) | set(S.WRITE_TOOLS)


def test_every_tool_says_whether_it_changes_memory():
    tools = S.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, False)["result"]["tools"]
    hints = {t["name"]: t["annotations"]["readOnlyHint"] for t in tools}
    assert hints == {**{n: True for n in S.READ_TOOLS}, **{n: False for n in S.WRITE_TOOLS}}


def test_read_only_refuses_a_write_even_when_asked_by_name(tmp_path, monkeypatch):
    pid = _memory(tmp_path, monkeypatch)
    err, text = _call("meedo_decide", {"id": pid, "decision": "accept", "reason": "injected"}, True)
    assert err and "read-only" in text
    assert L.load()["proposals"][0]["status"] == "open"


def test_a_decision_made_over_mcp_lands_in_the_ledger(tmp_path, monkeypatch):
    pid = _memory(tmp_path, monkeypatch)
    err, _ = _call("meedo_standup", {})
    assert not err
    err, text = _call("meedo_decide", {"id": pid, "decision": "reject", "reason": "not now", "by": "jan"})
    assert not err and json.loads(text)["status"] == "rejected"
    assert L.load()["proposals"][0]["decided_by"] == "jan"


def test_an_episode_recorded_over_mcp_is_recalled(tmp_path, monkeypatch):
    _memory(tmp_path, monkeypatch)
    err, text = _call("meedo_record_episode", {"outcome": "success", "problem": "i-dots dropped on gcm",
                                               "method": "count elements before and after"})
    assert not err, text
    err, text = _call("meedo_recall", {"problem": "dots dropped"})
    assert json.loads(text)[0]["method"] == "count elements before and after"
    err, text = _call("meedo_record_episode", {"outcome": "success", "problem": "no lesson"})
    assert err  # a closed episode with no method is refused, and the refusal is readable


def test_a_small_models_sloppy_arguments_are_cleaned_not_fatal(tmp_path, monkeypatch):
    _memory(tmp_path, monkeypatch)
    # Exactly what qwen3:0.6b sent through Ollama's native API.
    err, _ = _call("meedo_standup", {"most pressing first (escalated, then priority)": [{"case": "case1"}]})
    assert not err
    _call("meedo_record_episode", {"outcome": "success", "problem": "halo on propak",
                                   "method": "group by hue family", "cases": ["propak"]})
    err, text = _call("meedo_recall", {"problem": "halo", "cases": "propak", "top": "2", "invented": 1})
    assert not err and json.loads(text)[0]["method"] == "group by hue family"
    assert S._clean(S.READ_TOOLS["meedo_recall"]["inputSchema"],
                    {"cases": "propak", "top": "2", "x": 1}) == {"cases": ["propak"], "top": 2}


def test_the_reviewer_reads_only_inside_the_repository(tmp_path, monkeypatch):
    outside = tmp_path / "logo.png"
    Image.fromarray(np.full((20, 20, 3), 200, np.uint8)).save(outside)
    err, text = _call("meedo_review", {"output": str(outside), "sketch": str(outside)})
    assert err and "PermissionError" in text
    err, _ = _call("meedo_review", {"output": "/etc/passwd", "sketch": "/etc/passwd"})
    assert err
    err, _ = _call("meedo_review", {"output": "qa_logos/../../etc/passwd", "sketch": "x"})
    assert err
    monkeypatch.setenv("MEEDO_ALLOWED_DIRS", str(tmp_path))
    err, text = _call("meedo_review", {"output": str(outside), "sketch": str(outside)})
    assert not err and json.loads(text)["passed"]


def test_a_link_inside_the_repository_cannot_reach_outside(tmp_path):
    link = S.ROOT / "qa_logos" / f".mcp_test_link_{tmp_path.name}"
    link.symlink_to("/etc/passwd")
    try:
        err, text = _call("meedo_review", {"output": str(link), "sketch": str(link)})
        assert err and "PermissionError" in text
    finally:
        link.unlink()


def test_relative_paths_mean_the_repository_wherever_the_client_runs(tmp_path, monkeypatch):
    # clean/ is gitignored — synthesise a tiny PNG under the repo so the test
    # does not depend on a local corpus seed.
    rel = Path("qa_logos") / f".mcp_rel_test_{tmp_path.name}.png"
    abs_path = S.ROOT / rel
    Image.fromarray(np.full((24, 24, 3), 180, np.uint8)).save(abs_path)
    try:
        monkeypatch.chdir(tmp_path)
        err, text = _call("meedo_review", {"output": str(rel), "sketch": str(rel)})
        assert not err, text
    finally:
        abs_path.unlink(missing_ok=True)


def test_stdout_carries_only_protocol_even_when_a_tool_prints(monkeypatch, capsys):
    real = S._call
    monkeypatch.setattr(S, "_call", lambda *a: (print("engine chatter"), real(*a))[1])
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "meedo_playbook", "arguments": {}}}]
    S.serve(False, io.StringIO("\n".join(json.dumps(m) for m in msgs) + "\nnot json\n"))
    out, err = capsys.readouterr()
    replies = [json.loads(line) for line in out.splitlines()]  # every stdout line is protocol
    assert [r.get("id") for r in replies] == [1, 2, None]
    assert replies[2]["error"]["code"] == -32700
    assert "engine chatter" in err


def test_ai_advise_and_lessons_tools(tmp_path, monkeypatch):
    from tools.ai_collab import learn as learn_mod

    monkeypatch.setattr(learn_mod, "DEFAULT_PATH", tmp_path / "meedo_ai_lessons.json")
    monkeypatch.setenv("MEEDO_AI_RECALL_MIN", "99")

    class _Adv:
        def to_dict(self):
            return {
                "domain": "general",
                "problem": "test stuck",
                "diagnosis": "injected",
                "method": "do the thing",
                "actions": ["a"],
                "risks": [],
                "providers_used": ["gemini", "claude"],
                "source": "gemini,claude",
                "lesson_id": "ALinj",
                "offline": False,
                "agree": True,
            }

    monkeypatch.setattr(
        "tools.ai_collab.advisor.advise",
        lambda **kwargs: _Adv(),
    )
    err, text = _call(
        "meedo_ai_advise",
        {"problem": "test stuck", "domain": "general"},
    )
    assert not err
    assert json.loads(text)["method"] == "do the thing"

    learn_mod.persist_lesson(
        domain="general",
        problem="test stuck case for recall",
        diagnosis="injected",
        method="do the thing",
        mirror_episode=False,
        path=tmp_path / "meedo_ai_lessons.json",
    )
    err, text = _call("meedo_ai_lessons", {"problem": "test stuck case for recall", "domain": "general"})
    assert not err
    hits = json.loads(text)
    assert hits and "do the thing" in hits[0]["method"]

    # Read-only refuses the write tool.
    err, text = _call("meedo_ai_advise", {"problem": "x"}, True)
    assert err and "read-only" in text
