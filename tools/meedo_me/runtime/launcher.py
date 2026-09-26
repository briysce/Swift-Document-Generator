"""Fused OpenClaw runtime for Meedo-Me.

OpenClaw ships as a local npm tree under ``tools/meedo_me/runtime/openclaw``.
Agents and scripts must call this launcher — never ``npm install -g openclaw``
and never assume a bare ``openclaw`` on PATH.

State and config live under the Meedo data directory (``OPENCLAW_HOME``), so
the fused runtime does not depend on a hand-managed ``~/.openclaw`` product
install. An existing ``~/.openclaw`` is still readable as a one-time fallback
when the Meedo home has no config yet.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME_NPM = HERE / "openclaw"
PACKAGE_JSON = RUNTIME_NPM / "package.json"
LOCKFILE = RUNTIME_NPM / "package-lock.json"
NODE_MODULES = RUNTIME_NPM / "node_modules"
OPENCLAW_PKG = NODE_MODULES / "openclaw"
BIN_NAME = "openclaw"

# OpenClaw 2026.9.6 engines field.
_NODE_OK = re.compile(
    r"^(?:24\.(?:1[6-9]|[2-9]\d)\.\d+|24\.\d{3,}\.\d+|25\.\d+\.\d+|2[6-9]\.\d+\.\d+|[3-9]\d+\.\d+\.\d+)$"
)


class RuntimeError_(RuntimeError):
    """Fused OpenClaw runtime is missing or misconfigured."""


def runtime_root() -> Path:
    return RUNTIME_NPM


def meedo_openclaw_home() -> Path:
    """Directory Meedo owns for OpenClaw config + WhatsApp session state."""
    override = (os.environ.get("OPENCLAW_HOME") or "").strip()
    if override:
        return Path(override).expanduser()
    xdg = (os.environ.get("XDG_DATA_HOME") or "").strip()
    if xdg:
        return Path(xdg).expanduser() / "Meedo-Me" / "openclaw"
    if sys.platform == "win32":
        appdata = (os.environ.get("APPDATA") or "").strip()
        if appdata:
            return Path(appdata) / "Meedo-Me" / "openclaw"
    return Path.home() / ".local" / "share" / "Meedo-Me" / "openclaw"


def legacy_openclaw_home() -> Path:
    return Path.home() / ".openclaw"


def openclaw_config_path() -> Path:
    """Config path for the fused runtime (Meedo-owned)."""
    explicit = (os.environ.get("OPENCLAW_CONFIG_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    meedo = meedo_openclaw_home() / "openclaw.json"
    if meedo.is_file():
        return meedo
    legacy = legacy_openclaw_home() / "openclaw.json"
    if legacy.is_file() and not meedo.exists():
        # Compat: use existing session until migrate; still prefer writing Meedo path
        # when connect creates a fresh config.
        return legacy
    return meedo


def _node_bin() -> str:
    node = shutil.which("node")
    if not node:
        raise RuntimeError_(
            "Node.js is required for Meedo's fused OpenClaw runtime. "
            "Install Node >=24.16 (nvm install 24) and retry."
        )
    return node


def node_version(node: str | None = None) -> str:
    node = node or _node_bin()
    try:
        out = subprocess.check_output([node, "--version"], text=True, timeout=10).strip()
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError_(f"Could not read Node version ({e})") from e
    return out.lstrip("v")


def require_node() -> str:
    node = _node_bin()
    ver = node_version(node)
    if not _NODE_OK.match(ver):
        # Also accept 24.16+ via numeric compare for clarity on common cases.
        try:
            major, minor, *_ = (int(x) for x in ver.split("."))
        except ValueError:
            major = minor = 0
        ok = (major == 24 and minor >= 16) or major >= 26
        if not ok:
            raise RuntimeError_(
                f"Meedo's fused OpenClaw needs Node >=24.16 (or >=26); found {ver} at {node}. "
                "Use nvm: `nvm install 24 && nvm use 24`."
            )
    return node


def openclaw_bin(*, ensure: bool = False) -> Path:
    """Absolute path to the local openclaw CLI shim."""
    if ensure:
        ensure_install()
    # Prefer .bin shim; fall back to package entry.
    candidates = [
        NODE_MODULES / ".bin" / BIN_NAME,
        OPENCLAW_PKG / "openclaw.mjs",
        OPENCLAW_PKG / "dist" / "entry.js",
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise RuntimeError_(
        f"Fused OpenClaw binary missing under {RUNTIME_NPM}. "
        "Run: python -m tools.meedo_me.runtime ensure"
    )


def openclaw_env() -> dict[str, str]:
    """Env for every fused OpenClaw spawn."""
    env = os.environ.copy()
    home = meedo_openclaw_home()
    home.mkdir(parents=True, exist_ok=True)
    env["OPENCLAW_HOME"] = str(home)
    cfg = openclaw_config_path()
    # If we resolved a legacy path for reading, keep writing preference on Meedo home
    # when no config exists anywhere yet.
    if not cfg.exists():
        cfg = home / "openclaw.json"
    env["OPENCLAW_CONFIG_PATH"] = str(cfg)
    # Prefer Node 24 from nvm when present and current node is too old.
    nvm24 = Path.home() / ".nvm" / "versions" / "node"
    if nvm24.is_dir():
        for child in sorted(nvm24.iterdir(), reverse=True):
            if child.name.startswith("v24.") or child.name.startswith("v26."):
                bin_dir = child / "bin"
                if (bin_dir / "node").is_file():
                    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
                    break
    return env


def run_openclaw(argv: list[str], *, ensure: bool = True, check: bool = False,
                 capture: bool = False, timeout: float | None = None) -> subprocess.CompletedProcess:
    """Run the fused OpenClaw CLI with Meedo env."""
    bin_path = openclaw_bin(ensure=ensure)
    env = openclaw_env()
    node = require_node()
    if bin_path.suffix in (".mjs", ".js") or str(bin_path).endswith(".mjs"):
        cmd = [node, str(bin_path), *argv]
    else:
        cmd = [str(bin_path), *argv]
    return subprocess.run(
        cmd,
        env=env,
        check=check,
        capture_output=capture,
        text=True,
        timeout=timeout,
    )


def ensure_install(*, force: bool = False) -> Path:
    """``npm ci`` into the fused tree when ``node_modules`` is missing."""
    if not PACKAGE_JSON.is_file() or not LOCKFILE.is_file():
        raise RuntimeError_(
            f"Fused OpenClaw package files missing in {RUNTIME_NPM} "
            "(expected package.json + package-lock.json)."
        )
    marker = NODE_MODULES / ".bin" / BIN_NAME
    if marker.is_file() and not force and OPENCLAW_PKG.is_dir():
        return openclaw_bin()
    node = require_node()
    npm = shutil.which("npm")
    # Prefer npm beside the validated node (nvm 24).
    sibling_npm = Path(node).parent / "npm"
    if sibling_npm.is_file():
        npm = str(sibling_npm)
    if not npm:
        raise RuntimeError_("npm not found; install Node 24+ with npm.")
    RUNTIME_NPM.mkdir(parents=True, exist_ok=True)
    env = openclaw_env()
    env["PATH"] = f"{Path(node).parent}{os.pathsep}{env.get('PATH', '')}"
    # npm 11+ gates lifecycle scripts; OpenClaw needs them (postinstall plugins).
    attempts = [
        [npm, "ci", "--dangerously-allow-all-scripts"],
        [npm, "install", "--no-save", "--dangerously-allow-all-scripts"],
        [npm, "ci"],
        [npm, "install", "--no-save"],
    ]
    last: subprocess.CompletedProcess | None = None
    for cmd in attempts:
        last = subprocess.run(
            cmd,
            cwd=str(RUNTIME_NPM),
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if last.returncode == 0:
            break
    if last is None or last.returncode != 0:
        detail = ((last.stderr if last else "") or (last.stdout if last else "") or "").strip()[-800:]
        raise RuntimeError_(f"npm ci failed for fused OpenClaw:\n{detail}")
    return openclaw_bin()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Early dispatch so OpenClaw flags like --version are not eaten by argparse.
    if argv and argv[0] == "openclaw":
        oc_args = argv[1:]
        if oc_args and oc_args[0] == "--":
            oc_args = oc_args[1:]
        try:
            bin_path = openclaw_bin(ensure=True)
            env = openclaw_env()
            node = require_node()
            if bin_path.suffix in (".mjs", ".js") or str(bin_path).endswith(".mjs"):
                cmd = [node, str(bin_path), *oc_args]
            else:
                cmd = [str(bin_path), *oc_args]
            os.execvpe(cmd[0], cmd, env)
        except RuntimeError_ as e:
            print(e, file=sys.stderr)
            return 1
        return 0

    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m tools.meedo_me.runtime",
        description="Meedo-Me fused OpenClaw runtime (no global npm install).",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("ensure", help="npm ci the local OpenClaw tree if needed")
    e.add_argument("--force", action="store_true")
    sub.add_parser("bin", help="print the local openclaw binary path")
    sub.add_parser("home", help="print OPENCLAW_HOME (Meedo data dir)")
    sub.add_parser("config", help="print OPENCLAW_CONFIG_PATH")
    sub.add_parser("openclaw", help="run fused openclaw (pass args after this word)")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "ensure":
            path = ensure_install(force=args.force)
            print(f"Fused OpenClaw ready: {path}")
            print(f"OPENCLAW_HOME={meedo_openclaw_home()}")
            return 0
        if args.cmd == "bin":
            print(openclaw_bin(ensure=False))
            return 0
        if args.cmd == "home":
            print(meedo_openclaw_home())
            return 0
        if args.cmd == "config":
            print(openclaw_config_path())
            return 0
        if args.cmd == "openclaw":
            print("Pass OpenClaw args after the word openclaw, e.g.\n"
                  "  python -m tools.meedo_me.runtime openclaw --version", file=sys.stderr)
            return 2
    except RuntimeError_ as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
