#!/usr/bin/env python3
"""App-level improve loop: harness → score → log → summary.

1. Run mobile/test/app_improve_loop_test.dart via repo .tools/flutter
2. Score qa_app/synthetic/harness_results.json with app_improve_score.py
3. Append qa_app/synthetic/improve_log.jsonl
4. Write qa_app/synthetic/improve_summary_latest.json

Usage (repo root):
  python scripts/app_improve_loop.py
  python scripts/app_improve_loop.py --skip-harness
  python scripts/app_improve_loop.py --top 8
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYN = ROOT / "qa_app" / "synthetic"
LOG = SYN / "improve_log.jsonl"
SUMMARY = SYN / "improve_summary_latest.json"
MOBILE = ROOT / "mobile"
FLUTTER = ROOT / ".tools" / "flutter" / "bin" / (
    "flutter.bat" if sys.platform.startswith("win") else "flutter"
)

sys.path.insert(0, str(ROOT / "scripts"))
from app_improve_score import score_all  # noqa: E402
from improve_loop_training import record_run_snapshot  # noqa: E402


def _run_harness(timeout_s: int = 600) -> tuple[bool, str]:
    if not FLUTTER.is_file():
        return False, f"missing_flutter:{FLUTTER}"
    cmd = [
        str(FLUTTER),
        "test",
        "test/app_improve_loop_test.dart",
    ]
    try:
        r = subprocess.run(
            cmd,
            cwd=str(MOBILE),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        out = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
        if r.returncode != 0:
            tail = "\n".join(out.strip().splitlines()[-50:])
            return False, f"flutter_test_exit_{r.returncode}\n{tail}"
        return True, "ok"
    except subprocess.TimeoutExpired:
        return False, "flutter_test_timeout"
    except Exception as e:
        return False, str(e)


def run_loop(skip_harness: bool = False, top_n: int = 10) -> dict:
    SYN.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    t0 = time.time()

    harness_ok = True
    harness_note = "skipped"
    if not skip_harness:
        print("=== app harness (flutter test) ===", flush=True)
        harness_ok, harness_note = _run_harness()
        print(harness_note if not harness_ok else "harness ok", flush=True)
        if not harness_ok:
            summary = {
                "run_id": run_id,
                "ts": ts,
                "ok": False,
                "harness_ok": False,
                "harness_note": harness_note,
                "mean_composite": None,
                "top_failures": [],
                "gate_fails": [],
                "elapsed_s": round(time.time() - t0, 2),
                "next": "Fix harness errors, then re-run this script.",
            }
            SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            with LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps({**summary, "event": "harness_fail"}) + "\n")
            return summary

    print("=== score harness results ===", flush=True)
    scored = score_all()
    cases = scored.get("cases") or []
    ok_rows = [c for c in cases if c.get("composite") is not None]

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        for row in ok_rows:
            entry = {
                "run_id": run_id,
                "ts": ts,
                "case_id": row["case_id"],
                "composite": row["composite"],
                "metrics": row.get("metrics"),
                "gates": row.get("gates"),
                "duration_ms": row.get("duration_ms"),
                "ok": row.get("ok"),
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if not ok_rows:
            f.write(
                json.dumps(
                    {
                        "run_id": run_id,
                        "ts": ts,
                        "ok": False,
                        "note": "no_scored_cases",
                        "harness_ok": harness_ok,
                        "harness_note": harness_note,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    worst = sorted(ok_rows, key=lambda r: r["composite"])[:top_n]
    best = sorted(ok_rows, key=lambda r: -r["composite"])[: min(5, top_n)]
    gate_fails = scored.get("gate_fails") or []

    metric_fails: list[dict] = []
    for r in ok_rows:
        for m, v in (r.get("metrics") or {}).items():
            if v < 0.85:
                metric_fails.append(
                    {
                        "case_id": r["case_id"],
                        "metric": m,
                        "score": v,
                        "composite": r["composite"],
                    }
                )
    metric_fails.sort(key=lambda x: x["score"])

    summary = {
        "run_id": run_id,
        "ts": ts,
        "ok": bool(scored.get("ok")),
        "harness_ok": harness_ok,
        "harness_note": harness_note,
        "n_cases": scored.get("n_cases"),
        "n_scored": scored.get("n_scored"),
        "mean_composite": scored.get("mean_composite"),
        "elapsed_s": round(time.time() - t0, 2),
        "gate_fails": gate_fails,
        "top_failures": [
            {
                "case_id": r["case_id"],
                "composite": r["composite"],
                "metrics": r.get("metrics"),
                "gates": r.get("gates"),
                "duration_ms": r.get("duration_ms"),
            }
            for r in worst
        ],
        "metric_hotspots": metric_fails[:15],
        "top_wins": [
            {"case_id": r["case_id"], "composite": r["composite"]} for r in best
        ],
        "notes": scored.get("notes") or [],
        "budgets_ms": scored.get("budgets_ms"),
        "approved_lock_reminder": {
            "after_pill_gap": 11.0,
            "so_show_rule": False,
            "contact_label_to_value": 3.0,
            "note": "Never change Shipping SO/Contact locks to chase app scores.",
        },
        "log": str(LOG.relative_to(ROOT)).replace("\\", "/"),
        "next": (
            "Read this summary + qa_app/synthetic/training_lessons.json; fix "
            "top_failures that are real app bugs (generate integrity, "
            "prune-on-open, restore/address regressions, rapid-loop crashes) "
            "without changing Shipping SO/Contact locks or inventing logo "
            "redraws; expand curriculum for new glitches; re-run; append "
            "score-proven lessons. Adjust budgets only when the machine class "
            "proves a band is wrong."
        ),
    }
    # Gemini↔Claude / Meedo recall on app harness stuck cases — fail-open.
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from tools.ai_collab.improve_hook import enrich_summary as _ai_enrich

        _ai_enrich(summary, domain="app_ux", max_items=2)
    except Exception as e:  # pragma: no cover
        print(f"ai_collab improve hook skipped ({e})", flush=True)

    record_run_snapshot("app", summary)
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n=== improve loop summary ===", flush=True)
    print(
        json.dumps(
            {
                "mean_composite": summary["mean_composite"],
                "n_scored": summary["n_scored"],
                "gate_fails": [
                    g.get("case_id") for g in (summary["gate_fails"] or [])
                ],
                "elapsed_s": summary["elapsed_s"],
            },
            indent=2,
        ),
        flush=True,
    )
    print("\nTop failures (fix next):", flush=True)
    for w in summary["top_failures"]:
        print(
            f"  {w['case_id']}: composite={w['composite']} "
            f"ms={w.get('duration_ms')} metrics={w['metrics']}",
            flush=True,
        )
    if summary["metric_hotspots"]:
        print("\nMetric hotspots:", flush=True)
        for m in summary["metric_hotspots"][:8]:
            print(
                f"  {m['case_id']} / {m['metric']}={m['score']}",
                flush=True,
            )
    print(f"\nAppended -> {LOG.relative_to(ROOT)}", flush=True)
    print(f"Summary -> {SUMMARY.relative_to(ROOT)}", flush=True)
    print(summary["next"], flush=True)
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="App-level improve loop")
    p.add_argument(
        "--skip-harness",
        action="store_true",
        help="Score existing harness_results.json only",
    )
    p.add_argument("--top", type=int, default=10, help="How many worst cases to list")
    args = p.parse_args(argv)
    summary = run_loop(skip_harness=args.skip_harness, top_n=args.top)
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
