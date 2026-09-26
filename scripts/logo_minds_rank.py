"""Can Gemini or Claude pick the better of the engine's two candidates?

The vectorizer builds a trace and a reconstruction and must ship one. Measured
over the corpus, no signal it has ranks them within a pair once the trace has
a vector (see `_prefer_reconstruction`), so the trace keeps it and about half
the available gain is left on the table (oracle 0.7688 vs 0.7567). A vision
model's judgement is a candidate for that missing signal — this measures it
before anything trusts it: each mind compares the two candidates against the
sketch, both ways round, and its pick is checked against the clean master the
engine never sees. Every answer is judged for Meedo-Me.

    LOGO_IDEALIZE=1 LOGO_KEEP_CANDIDATES=<dir> python scripts/logo_vectorize.py ...   # per pair, first
    python scripts/logo_minds_rank.py <dir> [--minds claude,gemini]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from logo_golden_suite import score_pair, score_pair_v2  # noqa: E402
from PIL import Image  # noqa: E402

from tools.logo_vectorizer import meedo_consult as C  # noqa: E402
from tools.logo_vectorizer.ai_advisors import minds as M  # noqa: E402

SYN = ROOT / "qa_logos" / "synthetic"
TIE = 0.003          # composite_v2 differences smaller than this are not a winner
COLLAPSE_FLOOR = 0.05


def _v2(clean: Path, png: Path) -> float:
    m = score_pair(clean, png)
    return float(score_pair_v2(clean, png, m).get("composite_v2", m["composite"]))


def _judge_answer(ans: M.Answer, first_is: str, truth: str) -> None:
    """`first_is` is which candidate was shown as A in this answer."""
    x = str((ans.parsed or {}).get("better", "")).strip().upper()[:1]
    said = {"A": first_is, "B": "ideal" if first_is == "traced" else "traced", "S": "same"}.get(x)
    if not ans.consultation or ans.error or said is None:
        return
    if truth == "tie":
        res = "helped" if said == "same" else "no_change"
    else:
        res = "helped" if said == truth else ("no_change" if said == "same" else "wrong")
    try:
        C.judge(ans.consultation, res, f"picked {said}; clean master says {truth}", by="logo_minds_rank (auto)")
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cands", type=Path)
    ap.add_argument("--minds", default="claude,gemini")
    a = ap.parse_args(argv)
    minds = [m for m in a.minds.split(",") if M.available(m)]
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rows = []
    for meta in sorted(a.cands.glob("*__candidates.json")):
        case = meta.name.replace("__candidates.json", "")
        info = json.loads(meta.read_text())
        t, i = a.cands / f"{case}__traced.png", a.cands / f"{case}__ideal.png"
        if not (t.is_file() and i.is_file() and "ideal" in info):
            continue
        clean = SYN / "clean" / f"{case.split('__')[0]}.png"
        sketch = Image.open(SYN / "degraded" / f"{case}.png").convert("RGBA")
        vt, vi = _v2(clean, t), _v2(clean, i)
        truth = "tie" if abs(vt - vi) < TIE else ("traced" if vt > vi else "ideal")
        # Pair-level flags sit beside role dicts in candidates.json.
        pair_flags = {
            k: v for k, v in info.items() if not isinstance(v, dict)
        }
        role_flat = {
            f"{k}_{n}": v
            for n, d in info.items()
            if isinstance(d, dict)
            for k, v in d.items()
            if k != "review"
        }
        # Recompute ideal_lost_strictly_less from review dicts when missing
        # (older KEEP_CANDIDATES dumps only wrote per-role review blobs).
        if "ideal_lost_strictly_less" not in pair_flags:
            try:
                from tools.logo_vectorizer.meedo_review import Review, lost_no_more

                ir = Review.from_dict((info.get("ideal") or {}).get("review"))
                tr = Review.from_dict((info.get("traced") or {}).get("review"))
                pair_flags["ideal_lost_strictly_less"] = bool(
                    lost_no_more(ir, tr) and not lost_no_more(tr, ir)
                )
            except Exception:
                pair_flags["ideal_lost_strictly_less"] = False
        row = {
            "case": case,
            "traced_v2": round(vt, 4),
            "ideal_v2": round(vi, 4),
            "truth": truth,
            **role_flat,
            **pair_flags,
        }
        for m in minds:
            r = M.compare_both_ways(m, sketch, Image.open(t).convert("RGBA"), Image.open(i).convert("RGBA"),
                                    case=case, session=f"rank-{run}-{case}", image_dir=SYN / "minds" / "inputs")
            _judge_answer(r["answers"][0], "traced", truth)
            _judge_answer(r["answers"][1], "ideal", truth)
            pick = {"first": "traced", "second": "ideal"}.get(r["pick"], r["pick"])
            row[f"{m}_pick"] = pick
        rows.append(row)
        print(json.dumps(row), flush=True)

    def mean(xs):
        return round(sum(xs) / len(xs), 4) if xs else None

    def derived(r):  # today's rule, identity first (see convert / _prefer_reconstruction)
        if r["passed_review_ideal"] and not r["passed_review_traced"]:
            return "ideal"
        # Strict-less loss is recorded when LOGO_KEEP_CANDIDATES writes
        # review finding counts; without it, fall through to has_vector rule.
        if (
            not r["passed_review_ideal"]
            and not r["passed_review_traced"]
            and r.get("ideal_lost_strictly_less")
        ):
            return "ideal"
        if r["passed_review_ideal"] and not r["has_vector_traced"] and r["agreement_ideal"] >= COLLAPSE_FLOOR:
            return "ideal"
        return "traced"

    report = {"run": run, "pairs": len(rows), "minds": {}}
    report["derived_mean_v2"] = mean([r[f"{derived(r)}_v2"] for r in rows])
    report["oracle_mean_v2"] = mean([max(r["traced_v2"], r["ideal_v2"]) for r in rows])
    report["always_traced_v2"] = mean([r["traced_v2"] for r in rows])
    for m in minds:
        picks = [(r, r.get(f"{m}_pick")) for r in rows]
        decisive = [(r, p) for r, p in picks if r["truth"] != "tie" and p in ("traced", "ideal")]
        correct = sum(1 for r, p in decisive if p == r["truth"])

        def with_mind(r, p=None, m=m):
            p = r.get(f"{m}_pick")
            base = derived(r)
            # Identity first stays: a mind may only move between two candidates
            # the reviewer passed, and only when both orders agree.
            if p in ("traced", "ideal") and r["passed_review_traced"] and r["passed_review_ideal"]:
                return p
            return base

        chosen = [r[f"{with_mind(r)}_v2"] for r in rows]
        worse = [r["case"] for r in rows if r[f"{with_mind(r)}_v2"] < r[f"{derived(r)}_v2"] - TIE]
        report["minds"][m] = {"decisive": len(decisive), "correct": correct,
                              "splits": sum(1 for _, p in picks if p == "split"),
                              "said_same": sum(1 for _, p in picks if p == "same"),
                              "mean_v2_if_followed": mean(chosen), "pairs_made_worse": worse}
    out = SYN / "minds" / f"rank_{run}.json"
    out.write_text(json.dumps({"report": report, "rows": rows}, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
