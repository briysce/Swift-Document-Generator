"""Connecting Meedo-Me's faces: each starts the one server, with the access its loop deserves."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from tools.meedo_me import connect as C


def _app_config(folder, servers=None):
    folder.mkdir(parents=True, exist_ok=True)
    cfg = {"mcpServers": servers or {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"],
                                              "env": {}, "active": False}},
           "mcpSettings": {"toolCallTimeoutSeconds": 30}}
    (folder / "mcp_config.json").write_text(json.dumps(cfg))
    return folder / "mcp_config.json"


def test_the_app_gets_the_whole_server_and_keeps_its_other_servers(tmp_path):
    cfg = _app_config(tmp_path / "data")
    C.connect_app(tmp_path / "data")
    data = json.loads(cfg.read_text())
    entry = data["mcpServers"]["Meedo-Me"]
    assert entry["active"] and entry["args"] == [str(C.SERVER)] and entry["command"] == sys.executable
    assert data["mcpServers"]["fetch"]["command"] == "uvx" and data["mcpSettings"]["toolCallTimeoutSeconds"] == 30
    assert (tmp_path / "data" / "mcp_config.json.before-meedo").is_file()


def test_the_app_placeholder_is_replaced_not_duplicated(tmp_path):
    cfg = _app_config(tmp_path / "d", {"Meedo-Me": {"command": "python3", "active": False, "official": True,
                                                   "args": ["/path/to/Swift-Document-Generator/x.py"]}})
    C.connect_app(tmp_path / "d", read_only=True)
    C.connect_app(tmp_path / "d", read_only=True)
    entry = json.loads(cfg.read_text())["mcpServers"]["Meedo-Me"]
    assert entry["args"] == [str(C.SERVER), "--read-only"] and entry["official"] is True


def test_the_app_config_is_never_invented(tmp_path):
    # The app writes its defaults only when the file is missing; creating it here would lose them.
    with pytest.raises(FileNotFoundError):
        C.connect_app(tmp_path / "nothing")
    assert not (tmp_path / "nothing" / "mcp_config.json").exists()


def test_openclaw_is_read_only_unless_writes_are_allowed(tmp_path):
    path = C.connect_openclaw(tmp_path / "openclaw.json")
    entry = json.loads(path.read_text())["mcp"]["servers"]["meedo-me"]
    assert entry["args"][-1] == "--read-only" and entry["cwd"] == str(C.ROOT)
    C.connect_openclaw(tmp_path / "openclaw.json", allow_writes=True)
    assert "--read-only" not in json.loads(path.read_text())["mcp"]["servers"]["meedo-me"]["args"]


def test_openclaw_merge_keeps_the_users_settings_and_loads_the_skill_once(tmp_path):
    path = tmp_path / "openclaw.json"
    path.write_text(json.dumps({"channels": {"telegram": {"enabled": True}},
                                "mcp": {"servers": {"github": {"command": "gh-mcp"}}},
                                "skills": {"load": {"extraDirs": ["/mine"]}}}))
    C.connect_openclaw(path, model="ollama/qwen3:8b")
    C.connect_openclaw(path, model="ollama/qwen3:8b")
    data = json.loads(path.read_text())
    assert data["channels"]["telegram"]["enabled"] and data["mcp"]["servers"]["github"]["command"] == "gh-mcp"
    assert data["skills"]["load"]["extraDirs"] == ["/mine", str(C.SKILLS)]
    assert data["agents"]["defaults"]["model"]["primary"] == "ollama/qwen3:8b"
    assert (C.SKILLS / "meedo-me" / "SKILL.md").is_file()


def test_a_json5_openclaw_config_is_left_alone(tmp_path):
    path = tmp_path / "openclaw.json"
    original = "{\n  // my channels\n  channels: { telegram: { enabled: true } },\n}\n"
    path.write_text(original)
    with pytest.raises(C.NeedsManualMerge) as e:
        C.connect_openclaw(path)
    assert path.read_text() == original
    assert e.value.snippet["mcp"]["servers"]["meedo-me"]["args"][-1] == "--read-only"


def test_ollama_reports_which_models_can_reach_meedo_me():
    replies = {
        "http://h:11434/api/version": {"version": "0.9.0"},
        "http://h:11434/api/tags": {"models": [{"name": "qwen3:8b", "size": 5.2e9},
                                                {"name": "gemma2:2b", "size": 1.6e9}]},
    }
    shows = {"qwen3:8b": ["completion", "tools", "thinking"], "gemma2:2b": ["completion"]}

    def get(url, body=None):
        return {"capabilities": shows[body["model"]]} if body else replies[url]

    st = C.ollama_status("http://h:11434/v1", get=get)
    assert st["reachable"] and st["version"] == "0.9.0"
    assert st["tool_models"] == ["qwen3:8b"]


def test_ollama_down_is_a_status_not_a_crash():
    def get(url, body=None):
        raise ConnectionRefusedError("refused")

    st = C.ollama_status(get=get)
    assert not st["reachable"] and "refused" in st["error"]


def test_the_server_starts_from_its_path_in_any_directory(tmp_path):
    # The app and OpenClaw launch the file directly, from wherever they run.
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    command, args = C.server_command(read_only=True)
    out = subprocess.run([command, *args], input="\n".join(map(json.dumps, msgs)) + "\n",
                         capture_output=True, text=True, cwd=tmp_path, timeout=60)
    replies = [json.loads(line) for line in out.stdout.splitlines()]
    assert replies[0]["result"]["serverInfo"]["name"] == "meedo-me"
    assert {t["name"] for t in replies[1]["result"]["tools"]} == set(
        __import__("tools.meedo_me.mcp_server", fromlist=["READ_TOOLS"]).READ_TOOLS)
