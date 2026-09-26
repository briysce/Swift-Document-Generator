"""Connect Meedo-Me's faces to its one memory, and check the local model.

The memory is the MCP server in this directory. Each face is configured to
start it, and nothing else — no copies of the memory, no second server:

    python -m tools.meedo_me.connect status
    python -m tools.meedo_me.connect ollama
    python -m tools.meedo_me.connect app      [--data-folder DIR] [--read-only]
    python -m tools.meedo_me.connect messaging [--config PATH] [--model ollama/qwen3:8b] [--allow-writes]
    python -m tools.meedo_me.connect whatsapp [--to <number>] [--agent claude] [--dry-run]   # hourly progress

Meedo messaging (WhatsApp / gateway) is fused as a local npm tree under
``tools/meedo_me/runtime/openclaw``. Install/run it with:

    python -m tools.meedo_me.runtime ensure
    python -m tools.meedo_me.runtime gateway …

Never install a separate global messaging product — Meedo owns the runtime.
Upstream package credits: ``tools/meedo_me/THIRD_PARTY.md``.

Claude Code needs nothing: `.mcp.json` at the repository root registers the
server for any session opened here.

Access follows who is in the loop. The Meedo-Me app asks the user before every
tool call, so it gets the whole server. The messaging gateway acts on its own
from chat, email and the web — text anyone can send it — so it is connected
read-only unless --allow-writes is passed: a prompt-injected agent that can
accept proposals or write episodes could rewrite what Meedo-Me believes.

Ollama is the local model for both. The messaging gateway talks to it through its native
API (its docs warn the /v1 route breaks tool calling); the app uses /v1. Either
way the model must support tool calls, or it cannot reach Meedo-Me at all, so
`ollama` reports which pulled models can.

The interpreter running this script is the one written into each config: it is
the one known to have the engine's dependencies (numpy, Pillow, OpenCV), which
the reviewer needs.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SERVER = HERE / "mcp_server.py"
SKILLS = HERE / "skills"
OLLAMA = "http://127.0.0.1:11434"
APP_NAME = "Meedo-Me"   # the app's productName; its data folder is named after it
APP_SERVER_KEY = "Meedo-Me"
OPENCLAW_SERVER_KEY = "meedo-me"
SUGGESTED_MODEL = "qwen3:8b"


class NeedsManualMerge(RuntimeError):
    """The config exists but is not plain JSON (gateway accepts JSON5). Editing
    it by guesswork could lose the user's comments or settings, so hand over the
    snippet instead."""

    def __init__(self, path: Path, snippet: dict):
        self.path, self.snippet = path, snippet
        super().__init__(f"{path} is not plain JSON; merge this by hand:\n"
                         + json.dumps(snippet, indent=2))


def server_command(read_only: bool) -> tuple[str, list[str]]:
    return sys.executable, [str(SERVER)] + (["--read-only"] if read_only else [])


# --------------------------------------------------------------------------
# files
# --------------------------------------------------------------------------


def _write_json(path: Path, data: dict) -> None:
    """Back up once, then replace atomically: a half-written config would stop
    the app or agent from starting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(path.name + ".before-meedo")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _nested(d: dict, *keys: str) -> dict:
    for k in keys:
        nxt = d.get(k)
        if not isinstance(nxt, dict):
            nxt = d[k] = {}
        d = nxt
    return d


# --------------------------------------------------------------------------
# the Meedo-Me app (desktop face)
# --------------------------------------------------------------------------


def app_data_folder_candidates() -> list[Path]:
    """Where the app keeps `mcp_config.json`: `<OS data dir>/Meedo-Me/data`
    unless the user moved it (the app honours JAN_DATA_FOLDER)."""
    if os.environ.get("JAN_DATA_FOLDER"):
        return [Path(os.environ["JAN_DATA_FOLDER"])]
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return [base / APP_NAME / "data"]


def connect_app(data_folder: Path | None = None, read_only: bool = False) -> Path:
    folders = [Path(data_folder)] if data_folder else app_data_folder_candidates()
    config = next((f / "mcp_config.json" for f in folders if (f / "mcp_config.json").is_file()), None)
    if config is None:
        # Creating it here would stop the app writing its own defaults on first
        # start, losing its other servers and settings.
        raise FileNotFoundError(
            "no mcp_config.json in " + ", ".join(map(str, folders))
            + " — open Meedo-Me once so it writes one, or pass --data-folder")
    data = json.loads(config.read_text(encoding="utf-8"))
    command, args = server_command(read_only)
    entry = _nested(data, "mcpServers", APP_SERVER_KEY)
    entry.update({"command": command, "args": args, "active": True})
    entry.setdefault("env", {})
    _write_json(config, data)
    return config


# --------------------------------------------------------------------------
# Meedo messaging runtime (fused — not a separate product)
# --------------------------------------------------------------------------


def openclaw_config_path() -> Path:
    from tools.meedo_me.runtime.launcher import openclaw_config_path as _fused

    return _fused()


def openclaw_bin_path() -> Path | None:
    """Local Meedo messaging CLI, or None if ``runtime ensure`` has not run."""
    try:
        from tools.meedo_me.runtime.launcher import openclaw_bin

        return openclaw_bin(ensure=False)
    except Exception:
        return None


def openclaw_snippet(allow_writes: bool = False, model: str = "") -> dict:
    command, args = server_command(read_only=not allow_writes)
    snippet: dict = {
        "mcp": {"servers": {OPENCLAW_SERVER_KEY: {"command": command, "args": args, "cwd": str(ROOT)}}},
        "skills": {"load": {"extraDirs": [str(SKILLS)]}},
    }
    if model:
        snippet["agents"] = {"defaults": {"model": {"primary": model}}}
    return snippet


def connect_openclaw(config: Path | None = None, allow_writes: bool = False, model: str = "") -> Path:
    path = Path(config) if config else openclaw_config_path()
    snippet = openclaw_snippet(allow_writes, model)
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise NeedsManualMerge(path, snippet) from None
        if not isinstance(data, dict):
            raise NeedsManualMerge(path, snippet)
    _nested(data, "mcp", "servers")[OPENCLAW_SERVER_KEY] = snippet["mcp"]["servers"][OPENCLAW_SERVER_KEY]
    load = _nested(data, "skills", "load")
    dirs = load.get("extraDirs") if isinstance(load.get("extraDirs"), list) else []
    load["extraDirs"] = dirs + [d for d in snippet["skills"]["load"]["extraDirs"] if d not in dirs]
    if model:
        _nested(data, "agents", "defaults", "model")["primary"] = model
    _write_json(path, data)
    return path


# --------------------------------------------------------------------------
# WhatsApp progress updates
# --------------------------------------------------------------------------

def _e164(number: str) -> str:
    digits = "".join(ch for ch in number if ch.isdigit())
    if len(digits) == 10:          # North American number without the country code
        digits = "1" + digits
    if len(digits) < 11:
        raise ValueError(f"{number!r} is not a full phone number")
    return "+" + digits


def connect_whatsapp(to: str, config: Path | None = None) -> tuple[Path, str]:
    """Admit only this number on Meedo's WhatsApp channel and return the
    automation that sends Meedo-Me's hourly update to it.

    Inbound WhatsApp text is untrusted, so the channel is an allowlist of the
    user's own number; Meedo-Me's server stays read-only for the messaging
    gateway (connect messaging). The update itself is a command, not a model turn."""
    number = _e164(to)
    path = Path(config) if config else openclaw_config_path()
    snippet = {"channels": {"whatsapp": {"dmPolicy": "allowlist", "allowFrom": [number], "selfChatMode": True}}}
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise NeedsManualMerge(path, snippet) from None
        if not isinstance(data, dict):
            raise NeedsManualMerge(path, snippet)
    wa = _nested(data, "channels", "whatsapp")
    wa["dmPolicy"] = "allowlist"
    allow = wa.get("allowFrom") if isinstance(wa.get("allowFrom"), list) else []
    wa["allowFrom"] = allow + ([number] if number not in allow else [])
    wa.setdefault("selfChatMode", True)
    _write_json(path, data)
    from tools.meedo_me.progress import automation_command

    automation = " ".join(json.dumps(c) if (" " in c or "*" in c) else c for c in automation_command(number))
    return path, automation


# --------------------------------------------------------------------------
# Ollama
# --------------------------------------------------------------------------

# Never through a proxy: a local model server is on this machine or its LAN.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(url: str, body: dict | None = None, timeout: float = 3.0) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with _opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ollama_status(base: str = OLLAMA, get=_get) -> dict:
    """Is Ollama up, and which pulled models can call tools? A model without
    tool support can chat but cannot reach Meedo-Me."""
    base = base.rstrip("/").removesuffix("/v1")
    try:
        version = get(f"{base}/api/version").get("version", "?")
        tags = get(f"{base}/api/tags").get("models", [])
    except (OSError, ValueError) as e:
        return {"reachable": False, "base": base, "error": str(e)}
    models = []
    for m in tags:
        name = m.get("name") or m.get("model", "")
        try:
            caps = get(f"{base}/api/show", {"model": name}).get("capabilities") or []
        except (OSError, ValueError):
            caps = []
        models.append({"name": name, "capabilities": caps, "tools": "tools" in caps,
                       "size_gb": round(m.get("size", 0) / 1e9, 1)})
    return {"reachable": True, "base": base, "version": version, "models": models,
            "tool_models": [m["name"] for m in models if m["tools"]]}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _print_ollama(st: dict) -> None:
    if not st["reachable"]:
        print(f"Ollama: not reachable at {st['base']} ({st['error']}).")
        print("  Install it from https://ollama.com, then: ollama serve")
        return
    print(f"Ollama {st['version']} at {st['base']}")
    for m in st["models"]:
        print(f"  {m['name']:<32} {m['size_gb']:>5} GB  tools: {'yes' if m['tools'] else 'NO'}")
    if not st["tool_models"]:
        print(f"  No pulled model can call tools, so none can reach Meedo-Me. Try: ollama pull {SUGGESTED_MODEL}")
    print("  Tool schemas and Meedo-Me's answers need room: if replies lose the thread, raise the "
          "context (e.g. OLLAMA_CONTEXT_LENGTH=16384 ollama serve).")


def _status() -> None:
    print(f"Meedo-Me server: {SERVER}\n  python: {sys.executable}")
    mcp = ROOT / ".mcp.json"
    print(f"Claude Code: {'registered by ' + str(mcp) if mcp.is_file() else 'no .mcp.json'}")
    for folder in app_data_folder_candidates():
        cfg = folder / "mcp_config.json"
        if cfg.is_file():
            entry = json.loads(cfg.read_text(encoding="utf-8")).get("mcpServers", {}).get(APP_SERVER_KEY)
            state = ("connected" + (" (read-only)" if "--read-only" in entry.get("args", []) else "")
                     if entry and entry.get("active") and str(SERVER) in entry.get("args", [])
                     else "not connected — run: connect app")
            print(f"Meedo-Me app: {state} ({cfg})")
            break
    else:
        print("Meedo-Me app: no config found (open the app once)")
    oc = openclaw_config_path()
    oc_bin = openclaw_bin_path()
    if oc_bin is None:
        print("Meedo messaging: runtime not installed — run: python -m tools.meedo_me.runtime ensure")
        print("  (Do not install a separate global gateway; Meedo owns the local tree.)")
    elif oc.is_file():
        try:
            data = json.loads(oc.read_text(encoding="utf-8"))
            entry = (data.get("mcp") or {}).get("servers", {}).get(OPENCLAW_SERVER_KEY)
            state = ("connected" + (" (read-only)" if "--read-only" in entry.get("args", []) else " (WRITES ALLOWED)")
                     if entry else "config present — run: connect messaging to register Meedo MCP")
            wa = (data.get("channels") or {}).get("whatsapp") or {}
            allow = wa.get("allowFrom") or []
            print(f"Meedo messaging: {state} ({oc})")
            print(f"  fused CLI: {oc_bin}")
            print(f"WhatsApp hourly: allowFrom={allow or '(none)'}; "
                  f"link with `python -m tools.meedo_me.runtime gateway channels login --channel whatsapp` then "
                  f"`python -m tools.meedo_me.connect whatsapp`")
        except json.JSONDecodeError:
            print(f"Meedo messaging: config is JSON5; check by hand ({oc})")
    else:
        print(f"Meedo messaging: CLI ready ({oc_bin}); no config yet at {oc}")
        print("WhatsApp hourly: run: python -m tools.meedo_me.connect whatsapp")
    _print_ollama(ollama_status())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.meedo_me.connect", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="what is connected, and the local model")
    o = sub.add_parser("ollama", help="is Ollama up, and which models can call tools")
    o.add_argument("--base", default=OLLAMA)
    a = sub.add_parser("app", help="connect the Meedo-Me app")
    a.add_argument("--data-folder", type=Path)
    a.add_argument("--read-only", action="store_true")
    c = sub.add_parser("messaging", aliases=["openclaw", "gateway"],
                       help="connect Meedo messaging gateway (read-only unless --allow-writes)")
    c.add_argument("--config", type=Path)
    c.add_argument("--model", default="", help=f"e.g. ollama/{SUGGESTED_MODEL}")
    c.add_argument("--allow-writes", action="store_true")
    w = sub.add_parser("whatsapp", aliases=["whatsapp-progress"],
                       help="hourly Meedo-Me progress to one WhatsApp number via Meedo messaging")
    w.add_argument("--to", default="", help="the number that receives the updates (default: MEEDO_WHATSAPP_TO)")
    w.add_argument("--agent", default="", help="only this agent's work (default: everyone)")
    w.add_argument("--config", type=Path)
    w.add_argument("--dry-run", action="store_true", help="print the automation instead of registering it")
    args = ap.parse_args(argv)

    if args.cmd == "status":
        _status()
    elif args.cmd == "ollama":
        _print_ollama(ollama_status(args.base))
    elif args.cmd == "app":
        try:
            path = connect_app(args.data_folder, args.read_only)
        except FileNotFoundError as e:
            print(e, file=sys.stderr)
            return 1
        print(f"Connected the Meedo-Me app: {path}\nRestart the app, then enable the Meedo-Me server "
              "under Settings → MCP Servers if it is not already on.")
    elif args.cmd in ("openclaw", "messaging", "gateway"):
        try:
            path = connect_openclaw(args.config, args.allow_writes, args.model)
        except NeedsManualMerge as e:
            print(e, file=sys.stderr)
            return 1
        print(f"Connected Meedo messaging: {path}" + ("" if args.allow_writes else " (read-only)"))
        if args.model.startswith("ollama/"):
            print("Messaging uses Ollama once you opt in: export OLLAMA_API_KEY=ollama-local")
        print("Ensure messaging runtime: python -m tools.meedo_me.runtime ensure")
        print("Check it: python -m tools.meedo_me.runtime gateway mcp doctor meedo-me --probe")
        print("Hourly WhatsApp progress: python -m tools.meedo_me.connect whatsapp")
    elif args.cmd in ("whatsapp", "whatsapp-progress"):
        from tools.meedo_me import progress as P

        P._load_env()
        to = args.to or P.whatsapp_to()
        if not to:
            print("Pass --to, or set MEEDO_WHATSAPP_TO=+1XXXXXXXXXX in a gitignored .env. "
                  "Never commit the number.", file=sys.stderr)
            return 1
        try:
            path, _ = connect_whatsapp(to, args.config)
        except (NeedsManualMerge, ValueError) as e:
            print(e, file=sys.stderr)
            return 1
        os.environ["MEEDO_WHATSAPP_TO"] = _e164(to)
        print(f"WhatsApp admits only that number: {path}")
        print("1. Ensure messaging runtime: python -m tools.meedo_me.runtime ensure")
        print("2. Link WhatsApp once (scan the QR: WhatsApp > Linked devices):")
        print("   python -m tools.meedo_me.runtime gateway channels login --channel whatsapp")
        print("3. Preview the update:  python -m tools.meedo_me.progress --hours 1")
        print("4. Hourly automation:")
        print(P.register_hourly(agent=args.agent, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
