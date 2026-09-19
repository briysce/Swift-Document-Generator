#!/usr/bin/env python3
"""Synthetic degrade → restore → score loop (Phase 4 executable scaffolding).

Runs current restore engines against qa_logos/synthetic pairs, scores vs clean
using the same metrics as scripts/logo_golden_suite.py, and appends results to
qa_logos/synthetic/improve_log.jsonl.

Usage (repo root):
  python scripts/logo_restore_improve_loop.py
  python scripts/logo_restore_improve_loop.py --degrade-first
  python scripts/logo_restore_improve_loop.py --engines baseline,vectorize --top 8
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SYN = ROOT / "qa_logos" / "synthetic"
CLEAN = SYN / "clean"
DEGRADED = SYN / "degraded"
RESTORED = SYN / "restored"
PAIRS = SYN / "pairs.json"
LOG = SYN / "improve_log.jsonl"
DEGRADE = ROOT / "scripts" / "logo_synthetic_degrade.py"
VECTORIZE = ROOT / "scripts" / "logo_vectorize.py"
ESRGAN = ROOT / "logo_restorer.py"

# Reuse golden suite metrics (do not fork scoring).
sys.path.insert(0, str(ROOT / "scripts"))
from improve_loop_training import record_run_snapshot  # noqa: E402
from logo_golden_suite import (  # noqa: E402
    _cubic_upscale,
    score_pair,
)


def _find_python() -> list[str]:
    for cmd in (["py", "-3"], ["python"], ["python3"]):
        try:
            r = subprocess.run(
                [*cmd, "-c", "import sys; print(sys.version)"],
                capture_output=True,
                text=True,
                timeout=8,
            )
            if r.returncode == 0:
                return cmd
        except Exception:
            pass
    return [sys.executable]


def _run_script(script: Path, src: Path, dest: Path, min_h: int) -> tuple[bool, str]:
    if not script.is_file():
        return False, "missing_script"
    py = _find_python()
    cmd = [*py, str(script), str(src), str(dest)]
    if script.name == "logo_vectorize.py":
        cmd.extend(["--min-height", str(min_h)])
    elif script.name == "logo_restorer.py":
        cmd.extend(["--min-dimension", str(min_h)])
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(ROOT),
        )
        if r.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
            return True, "ok"
        err = (r.stderr or r.stdout or "").strip().splitlines()
        return False, (err[-1] if err else f"exit_{r.returncode}")
    except Exception as e:
        return False, str(e)


def _ensure_pairs(degrade_first: bool) -> dict:
    if degrade_first or not PAIRS.is_file():
        py = _find_python()
        r = subprocess.run(
            [*py, str(DEGRADE), "--seed-from-clean"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=600,
        )
        if r.returncode != 0:
            raise RuntimeError(f"degrade failed: {r.stderr or r.stdout}")
        print(r.stdout, end="", flush=True)
    return json.loads(PAIRS.read_text(encoding="utf-8"))


def restore(engine: str, src: Path, dest: Path, min_h: int) -> tuple[bool, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if engine == "baseline":
        _cubic_upscale(src, dest, min_h)
        return dest.is_file(), "prepare+cubic"
    if engine == "vectorize":
        return _run_script(VECTORIZE, src, dest, min_h)
    if engine == "esrgan":
        return _run_script(ESRGAN, src, dest, min_h)
    raise ValueError(engine)


def run_loop(
    engines: list[str],
    min_h: int = 1200,
    degrade_first: bool = False,
    top_n: int = 10,
) -> dict:
    manifest = _ensure_pairs(degrade_first)
    pairs = manifest.get("pairs") or []
    if not pairs:
        raise RuntimeError(f"no pairs in {PAIRS}")

    RESTORED.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rows: list[dict] = []
    t0 = time.time()

    for pair in pairs:
        slug = pair["slug"]
        clean = SYN / pair["clean"]
        deg = SYN / pair["degraded"]
        if not clean.is_file() or not deg.is_file():
            rows.append(
                {
                    "run_id": run_id,
                    "ts": ts,
                    "pair_id": pair.get("id"),
                    "slug": slug,
                    "ok": False,
                    "note": "missing_files",
                }
            )
            continue
        for engine in engines:
            out = RESTORED / f"{pair['id']}__{engine}.png"
            ok, note = restore(engine, deg, out, min_h)
            entry: dict = {
                "run_id": run_id,
                "ts": ts,
                "pair_id": pair.get("id"),
                "slug": slug,
                "recipe": pair.get("recipe"),
                "seed": pair.get("seed"),
                "engine": engine,
                "anchor": bool(pair.get("anchor")),
                "clean": pair["clean"],
                "degraded": pair["degraded"],
                "restored": str(out.relative_to(SYN)).replace("\\", "/"),
                "ok": ok,
                "note": note,
            }
            if ok:
                metrics = score_pair(clean, out)
                entry.update(metrics)
            rows.append(entry)
            status = "ok" if ok else "FAIL"
            comp = entry.get("composite", "-")
            print(
                f"[{status}] {pair['id']} engine={engine} composite={comp} ({note})",
                flush=True,
            )

    # Append JSONL
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    scored = [r for r in rows if r.get("ok") and "composite" in r]
    failed = [r for r in rows if not r.get("ok")]
    # Lowest composite = biggest restore gap vs clean
    worst = sorted(scored, key=lambda r: r["composite"])[:top_n]
    best = sorted(scored, key=lambda r: -r["composite"])[: min(5, top_n)]

    by_engine: dict[str, list[float]] = {}
    by_pair_best: dict[str, float] = {}
    for r in scored:
        by_engine.setdefault(r["engine"], []).append(r["composite"])
        pid = str(r.get("pair_id") or "")
        by_pair_best[pid] = max(by_pair_best.get(pid, 0.0), float(r["composite"]))
    engine_means = {
        e: round(float(np.mean(v)), 4) for e, v in sorted(by_engine.items())
    }

    anchors = [r for r in scored if r.get("anchor")]
    anchor_mean = (
        round(float(np.mean([r["composite"] for r in anchors])), 4) if anchors else None
    )
    best_engine_mean = (
        round(float(np.mean(list(by_pair_best.values()))), 4) if by_pair_best else None
    )
    non_arc_best = [
        v for pid, v in by_pair_best.items() if not pid.startswith("arc__")
    ]
    non_arc_best_mean = (
        round(float(np.mean(non_arc_best)), 4) if non_arc_best else None
    )

    summary = {
        "run_id": run_id,
        "ts": ts,
        "n_pairs": len(pairs),
        "n_rows": len(rows),
        "n_scored": len(scored),
        "n_engine_fail": len(failed),
        "mean_composite": round(float(np.mean([r["composite"] for r in scored])), 4)
        if scored
        else None,
        "mean_best_engine": best_engine_mean,
        "mean_best_engine_non_arc": non_arc_best_mean,
        "engine_mean_composite": engine_means,
        "anchor_mean_composite": anchor_mean,
        "elapsed_s": round(time.time() - t0, 2),
        "top_failures": [
            {
                "pair_id": r["pair_id"],
                "engine": r["engine"],
                "composite": r["composite"],
                "ink_iou": r.get("ink_iou"),
                "palette_fidelity": r.get("palette_fidelity"),
                "alpha_clean": r.get("alpha_clean"),
                "aspect_drift": r.get("aspect_drift"),
            }
            for r in worst
        ],
        "top_wins": [
            {
                "pair_id": r["pair_id"],
                "engine": r["engine"],
                "composite": r["composite"],
            }
            for r in best
        ],
        "engine_errors": [
            {"pair_id": r.get("pair_id"), "engine": r.get("engine"), "note": r.get("note")}
            for r in failed
        ][:20],
        "log": str(LOG.relative_to(ROOT)).replace("\\", "/"),
        "quality_bar": {
            "north_star_anchors": 0.92,
            "interim_suite_target": 0.85,
            "note": (
                "Swift anchors define the quality class. mean_composite averages "
                "all engines; mean_best_engine is product-path quality."
            ),
        },
        "next": (
            "Read qa_logos/synthetic/training_lessons.json; improve the "
            "lowest-composite engine/recipe gaps; re-run this script; promote "
            "only techniques that raise scores (esp. swift_orange anchors); "
            "append score-proven lessons. Neural fine-tune only if suite plateaus."
        ),
    }

    record_run_snapshot("logo", summary)
    summary_path = SYN / "improve_summary_latest.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n=== improve loop summary ===", flush=True)
    print(json.dumps({k: summary[k] for k in summary if k != "next"}, indent=2), flush=True)
    print("\nTop failures (fix next):", flush=True)
    for w in summary["top_failures"]:
        print(
            f"  {w['pair_id']} / {w['engine']}: composite={w['composite']} "
            f"iou={w['ink_iou']} palette={w['palette_fidelity']} alpha={w['alpha_clean']}",
            flush=True,
        )
    print(f"\nAppended {len(rows)} rows -> {LOG.relative_to(ROOT)}", flush=True)
    print(summary["next"], flush=True)
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Logo restore synthetic improve loop")
    p.add_argument(
        "--engines",
        default="baseline,vectorize,esrgan",
        help="Comma list: baseline,vectorize,esrgan",
    )
    p.add_argument("--min-height", type=int, default=1200)
    p.add_argument(
        "--degrade-first",
        action="store_true",
        help="Regenerate degraded/ + pairs.json before restore",
    )
    p.add_argument("--top", type=int, default=10, help="How many worst pairs to print")
    args = p.parse_args(argv)

    if not CLEAN.is_dir() or not any(CLEAN.glob("*.png")):
        print(f"No clean logos in {CLEAN} — copy PNG anchors first.", file=sys.stderr)
        return 2

    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    # Skip esrgan quietly if deps missing? Still attempt; failures log as note.
    run_loop(engines, args.min_height, args.degrade_first, args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
