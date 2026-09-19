#!/usr/bin/env python3
"""Live Gemini restore for every customer logo; write QA report."""

from __future__ import annotations

import importlib.util
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
LOGOS = ROOT / "customer_logos"
OUT_DIR = LOGOS / "gemini_restored"
REPORT = ROOT / "qa_logs" / "batch_gemini_restore_report.txt"
MIN_H = 3000

SKIP_SUFFIXES = ("_restored", "_restored_flat", "_processed")
SKIP_NAMES = {"logo_restore_cache.json", "murrays_trucking_restored.json"}

spec = importlib.util.spec_from_file_location(
    "restore_logo_gemini", ROOT / "scripts" / "restore_logo_gemini.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def is_source(path: Path) -> bool:
    if path.name in SKIP_NAMES:
        return False
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        return False
    stem = path.stem.lower()
    return not any(s in stem for s in SKIP_SUFFIXES)


def qa_pair(src: Path, dest: Path) -> list[str]:
    issues: list[str] = []
    before = Image.open(src)
    after = Image.open(dest)
    if after.height < MIN_H:
        issues.append(f"height {after.height} < {MIN_H}")
    src_aspect = before.width / max(before.height, 1)
    dst_aspect = after.width / max(after.height, 1)
    if abs(src_aspect - dst_aspect) / max(src_aspect, 0.01) > 0.35:
        issues.append(f"aspect {src_aspect:.2f} -> {dst_aspect:.2f}")
    if dest.stat().st_size < 8000:
        issues.append(f"tiny file {dest.stat().st_size} B")
    if after.mode not in ("RGBA", "LA", "P"):
        issues.append(f"mode {after.mode} (expected alpha)")
    da = np.array(after.convert("RGBA"))
    opaque = (da[:, :, 3] > 32).mean()
    white = (
        (da[:, :, 0] > 235) & (da[:, :, 1] > 235) & (da[:, :, 2] > 235) & (da[:, :, 3] > 80)
    ).mean()
    if opaque > 0.92 and white > 0.20:
        issues.append(f"opaque plate (opaque={opaque:.2f} white={white:.2f})")
    return issues


def main() -> int:
    if not mod._load_key():
        print("GEMINI_API_KEY missing", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT.parent.mkdir(parents=True, exist_ok=True)

    sources = sorted(p for p in LOGOS.iterdir() if p.is_file() and is_source(p))
    lines = [f"batch_gemini_restore {datetime.now(timezone.utc).isoformat()}", ""]
    ok = fail = 0

    for i, src in enumerate(sources):
        dest = OUT_DIR / f"{src.stem}_gemini.png"
        print(f"[{i + 1}/{len(sources)}] Gemini restore {src.name}", flush=True)
        sys.stdout.flush()
        if dest.is_file() and dest.stat().st_size > 8000:
            issues = qa_pair(src, dest)
            if not issues:
                ok += 1
                before = Image.open(src)
                after = Image.open(dest)
                lines.append(
                    f"OK   {src.name}: {before.size} -> {after.size} "
                    f"(cached, {dest.stat().st_size // 1024} KB)"
                )
                print(f"  skip cached {dest.name}", flush=True)
                continue
        try:
            png = mod.restore(src)
            mod.finalize(png, dest, src)
            issues = qa_pair(src, dest)
            before = Image.open(src)
            after = Image.open(dest)
            note = f"{before.size} -> {after.size} ({dest.stat().st_size // 1024} KB)"
            if issues:
                fail += 1
                lines.append(f"FAIL {src.name}: {note}; {'; '.join(issues)}")
            else:
                ok += 1
                lines.append(f"OK   {src.name}: {note}")
        except SystemExit as exc:
            fail += 1
            lines.append(f"FAIL {src.name}: {exc}")
        except Exception as exc:
            fail += 1
            lines.append(f"FAIL {src.name}: {exc}")
        time.sleep(2)

    lines.extend(["", f"Summary: {ok} OK, {fail} failed, {len(sources)} total"])
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[-8:]), flush=True)
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
