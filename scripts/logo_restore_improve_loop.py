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
    score_pair_v2,
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
    env = {**dict(__import__("os").environ), "LOGO_NO_CHROME": "1"}
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(ROOT),
            env=env,
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


def _meedo_review_final(out: Path, degraded: Path, *, case: str, engine: str, run_id: str) -> dict:
    """Meedo-Me's verdict on a finished restoration, before it is reported.

    The loop's own scores cannot be trusted to notice a deleted element: on the
    three degraded GCM variants the reconstruction dropped the red monogram and
    `composite` ranked those outputs ABOVE the correct ones, so they were
    reported as the run's largest wins. Every output is now reviewed against the
    sketch it came from, raw and prepared, and a blocked one says so on its row.

    Fails open to "not reviewed" — never to "passed" and never by stopping a run.
    """
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from logo_raster_finish import load_rgba, prepare_for_engine
        from tools.logo_vectorizer.meedo_review import record, review

        raw = load_rgba(degraded)
        rv = review(out, prepare_for_engine(raw), raw)
        record(rv, case=case, candidate=engine, run_id=run_id, context="report")
        return {"passed": rv.passed, "findings": [f.as_dict() for f in rv.findings]}
    except Exception as e:  # noqa: BLE001
        return {"passed": None, "findings": [], "error": repr(e)[:160]}


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
                # Settings that change the numbers, recorded beside them.
                # Without these a run is not comparable to any other run, and
                # nothing says so: a re-run at --min-height 3000 read as a
                # 0.0035 regression against a 1200 baseline until the configs
                # were checked by hand. A score is only a score next to what
                # produced it.
                "min_height": int(min_h),
                "engines": list(engines),
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
                # Same run, second scale: `composite` cannot reach 1.0 even for
                # an exact copy of the reference, so `composite_v2` (identity
                # == 1.0) is what "0.99 fidelity" is actually measured on.
                # Legacy `composite` stays untouched as the continuity guard.
                entry.update(score_pair_v2(clean, out, metrics))
                entry["meedo_review"] = _meedo_review_final(
                    out, deg, case=str(pair.get("id")), engine=engine, run_id=run_id
                )
            rows.append(entry)
            status = "ok" if ok else "FAIL"
            comp = entry.get("composite", "-")
            mr = entry.get("meedo_review") or {}
            flag = "  [BLOCKED by Meedo-Me]" if mr.get("passed") is False else ""
            print(
                f"[{status}] {pair['id']} engine={engine} composite={comp} ({note}){flag}",
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
    anchor_mean_v2 = (
        round(float(np.mean([r["composite_v2"] for r in anchors])), 4)
        if anchors
        else None
    )
    by_engine_v2: dict[str, list[float]] = {}
    for r in scored:
        if "composite_v2" in r:
            by_engine_v2.setdefault(r["engine"], []).append(r["composite_v2"])
    engine_means_v2 = {
        e: round(float(np.mean(v)), 4) for e, v in sorted(by_engine_v2.items())
    }
    # Headroom left on the legacy scale: ceiling (identity score) - achieved.
    headrooms = [r["headroom"] for r in scored if "headroom" in r]
    mean_headroom = round(float(np.mean(headrooms)), 4) if headrooms else None
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
        # See the per-row note: comparing two runs means nothing unless these
        # match.
        "min_height": int(min_h),
        "engines": list(engines),
        "n_pairs": len(pairs),
        "n_rows": len(rows),
        "n_scored": len(scored),
        "n_engine_fail": len(failed),
        # A score next to a blocked output is not a result. These are listed
        # so a "win" that deleted part of the logo is never read as one.
        "meedo_blocked": [
            {
                "pair_id": r.get("pair_id"),
                "engine": r.get("engine"),
                "composite": r.get("composite"),
                "why": [f["detail"] for f in (r.get("meedo_review") or {}).get("findings", [])],
            }
            for r in scored
            if (r.get("meedo_review") or {}).get("passed") is False
        ],
        "meedo_reviewed": sum(
            1 for r in scored if (r.get("meedo_review") or {}).get("passed") is not None
        ),
        "mean_composite": round(float(np.mean([r["composite"] for r in scored])), 4)
        if scored
        else None,
        "mean_best_engine": best_engine_mean,
        "mean_best_engine_non_arc": non_arc_best_mean,
        "engine_mean_composite": engine_means,
        "anchor_mean_composite": anchor_mean,
        "engine_mean_composite_v2": engine_means_v2,
        "anchor_mean_composite_v2": anchor_mean_v2,
        "mean_legacy_headroom": mean_headroom,
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
            "north_star_anchors_v2": 0.99,
            "note": (
                "Swift anchors define the quality class. mean_composite averages "
                "all engines; mean_best_engine is product-path quality. "
                "NOTE: `composite` cannot reach 1.0 — feeding a clean reference "
                "back in as its own restoration scores ~0.93-0.95 "
                "(see composite_ceiling / headroom per row). Judge 0.99-class "
                "fidelity on composite_v2, where the identity scores exactly 1.0."
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

    # Hand the run to Meedo-Me. Recording has to be automatic to compound: an
    # advisor that only learns when someone remembers to tell it is an advisor
    # that mostly does not learn. It reads the log we just appended to, judges
    # any advice it gave on earlier runs, and records this one against the
    # commit it ran on. Fail-open — a ledger problem must never fail a run that
    # already produced its scores.
    try:
        # Running as `python scripts/logo_restore_improve_loop.py` puts
        # scripts/ on sys.path, not the repo root, so the package import needs
        # ROOT added explicitly. Without this the import fails and the
        # fail-open below hides it — recording would silently never happen.
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from tools.logo_vectorizer.meedo_ledger import observe as _meedo_observe

        led = _meedo_observe(note=f"improve loop {', '.join(engines)}")
        if led.get("recorded"):
            print(
                f"\nMeedo-Me: recorded {led['run_id']} — "
                f"{led.get('moved', 0)} case(s) moved, "
                f"{led.get('resolved_proposals', 0)} earlier proposal(s) judged",
                flush=True,
            )
    except Exception as e:  # pragma: no cover - never fail a scored run
        print(f"\nMeedo-Me ledger skipped ({e})", flush=True)

    print("\n=== improve loop summary ===", flush=True)
    print(json.dumps({k: summary[k] for k in summary if k != "next"}, indent=2), flush=True)
    print("\nTop failures (fix next):", flush=True)
    for w in summary["top_failures"]:
        print(
            f"  {w['pair_id']} / {w['engine']}: composite={w['composite']} "
            f"iou={w['ink_iou']} palette={w['palette_fidelity']} alpha={w['alpha_clean']}",
            flush=True,
        )
    blocked = summary.get("meedo_blocked") or []
    print(
        f"\nMeedo-Me review: {len(blocked)} of {summary.get('meedo_reviewed', 0)} "
        "outputs blocked — their scores are not results",
        flush=True,
    )
    for b in blocked:
        print(f"  {b['pair_id']} / {b['engine']} (composite {b['composite']}):", flush=True)
        for why in b["why"]:
            print(f"      {why}", flush=True)
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
