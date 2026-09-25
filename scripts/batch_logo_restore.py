#!/usr/bin/env python3
"""Batch-run logo_restorer.py on every customer logo; write QA report."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGOS = ROOT / "customer_logos"
OUT_DIR = LOGOS / "restored_batch"
REPORT = ROOT / "qa_logs" / "batch_logo_restore_report.txt"
RESTORER = ROOT / "logo_restorer.py"
MIN_DIM = 3000

SKIP_SUFFIXES = ("_restored", "_restored_flat", "_processed")
SKIP_NAMES = {"logo_restore_cache.json", "murrays_trucking_restored.json"}


def is_source(path: Path) -> bool:
    if path.name in SKIP_NAMES:
        return False
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        return False
    stem = path.stem.lower()
    return not any(s in stem for s in SKIP_SUFFIXES)


def main() -> int:
    if not RESTORER.is_file():
        print(f"Missing {RESTORER}", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT.parent.mkdir(parents=True, exist_ok=True)

    sources = sorted(p for p in LOGOS.iterdir() if p.is_file() and is_source(p))
    lines = [f"batch_logo_restore {datetime.now(timezone.utc).isoformat()}", ""]
    ok = fail = 0

    for src in sources:
        dest = OUT_DIR / f"{src.stem}_restored.png"
        cmd = [
            sys.executable,
            str(RESTORER),
            str(src),
            str(dest),
            "--min-dimension",
            str(MIN_DIM),
        ]
        print(f"Restoring {src.name} -> {dest.name}", flush=True)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if proc.returncode != 0:
                fail += 1
                lines.append(f"FAIL {src.name}: rc={proc.returncode}")
                if proc.stderr.strip():
                    lines.append(f"  stderr: {proc.stderr.strip()[:500]}")
                continue
            if not dest.is_file() or dest.stat().st_size < 500:
                fail += 1
                lines.append(f"FAIL {src.name}: empty output")
                continue
            from PIL import Image

            before = Image.open(src)
            after = Image.open(dest)
            lines.append(
                f"OK   {src.name}: {before.size} -> {after.size} "
                f"({dest.stat().st_size // 1024} KB)"
            )
            ok += 1
        except subprocess.TimeoutExpired:
            fail += 1
            lines.append(f"FAIL {src.name}: timeout")
        except Exception as exc:
            fail += 1
            lines.append(f"FAIL {src.name}: {exc}")

    lines.extend(["", f"Summary: {ok} OK, {fail} failed, {len(sources)} total"])
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[-5:]), flush=True)
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
