#!/usr/bin/env python3
"""Retry Gemini restores that timed out."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGOS = ROOT / "customer_logos"
OUT = LOGOS / "gemini_restored"

spec = importlib.util.spec_from_file_location(
    "restore_logo_gemini", ROOT / "scripts" / "restore_logo_gemini.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

NAMES = [
    "1000113412.png",
    "ARJAE.png",
    "Worley Cord LP.png",
]


def main() -> int:
    fail = 0
    for name in NAMES:
        src = LOGOS / name
        dest = OUT / f"{src.stem}_gemini.png"
        print(f"retry {name}", flush=True)
        try:
            png = mod.restore(src)
            mod.finalize(png, dest, src)
            print(f"OK {dest}", flush=True)
        except Exception as exc:
            fail += 1
            print(f"FAIL {name}: {exc}", flush=True)
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
