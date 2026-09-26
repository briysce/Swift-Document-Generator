"""Fused OpenClaw runtime: Meedo owns a local npm tree — never require a global CLI."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tools.meedo_me.runtime import launcher as L


def test_meedo_openclaw_home_is_under_meedo_data(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENCLAW_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    home = L.meedo_openclaw_home()
    assert home == tmp_path / "xdg" / "Meedo-Me" / "openclaw"
    assert "Meedo-Me" in str(home)


def test_openclaw_home_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_HOME", str(tmp_path / "oc"))
    assert L.meedo_openclaw_home() == tmp_path / "oc"


def test_config_prefers_meedo_home_when_present(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_HOME", str(tmp_path / "oc"))
    monkeypatch.delenv("OPENCLAW_CONFIG_PATH", raising=False)
    cfg = tmp_path / "oc" / "openclaw.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{}")
    assert L.openclaw_config_path() == cfg


def test_config_falls_back_to_legacy_when_meedo_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_HOME", str(tmp_path / "oc"))
    monkeypatch.delenv("OPENCLAW_CONFIG_PATH", raising=False)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "openclaw.json").write_text("{}")
    monkeypatch.setattr(L, "legacy_openclaw_home", lambda: legacy)
    assert L.openclaw_config_path() == legacy / "openclaw.json"


def test_openclaw_bin_requires_ensure_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "RUNTIME_NPM", tmp_path / "runtime")
    monkeypatch.setattr(L, "NODE_MODULES", tmp_path / "runtime" / "node_modules")
    monkeypatch.setattr(L, "OPENCLAW_PKG", tmp_path / "runtime" / "node_modules" / "openclaw")
    with pytest.raises(L.RuntimeError_, match="ensure"):
        L.openclaw_bin(ensure=False)


def test_openclaw_env_sets_home_and_config(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_HOME", str(tmp_path / "oc"))
    monkeypatch.delenv("OPENCLAW_CONFIG_PATH", raising=False)
    env = L.openclaw_env()
    assert env["OPENCLAW_HOME"] == str(tmp_path / "oc")
    assert env["OPENCLAW_CONFIG_PATH"].endswith("openclaw.json")
    assert (tmp_path / "oc").is_dir()


def test_automation_command_uses_fused_launcher_not_global_openclaw():
    from tools.meedo_me.progress import automation_command

    cmd = automation_command("+15551234567")
    assert cmd[1:4] == ["-m", "tools.meedo_me.runtime", "openclaw"]
    assert "automations" in cmd
    assert cmd[0].endswith("python") or "python" in Path(cmd[0]).name
