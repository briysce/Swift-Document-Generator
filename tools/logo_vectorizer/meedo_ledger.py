"""Meedo-Me's ledger — how an embedded advisor actually gets smarter.

The premise
-----------
Meedo-Me starts out weaker than a frontier hosted model, and on raw reasoning it
will stay that way. Its advantage is different: it sits inside one engine's
feedback loop and sees every run first-hand. A hosted model is told about this
engine in a prompt; Meedo-Me accumulates what actually happened to it.

But that advantage is only real if it can *write*. `meedo_advisor` reads the
record and proposes experiments, and that alone never compounds — it would give
the same advice on run 200 as on run 2. Three things have to be recorded for the
knowledge to build:

  1. **What changed, and what moved.** Each run is recorded against the commit
     it ran on, so a score change has something to be attributed to. Without
     that, the advisor can see that a case moved but never why.

  2. **Whether a suggestion paid off.** Proposals are logged when made and
     resolved on a later run. Over time this yields an honest hit rate per kind
     of suggestion — engine-specific knowledge that no hosted model can have,
     because it is about *this* engine's history.

  3. **What has never worked.** A case that has been attacked repeatedly and has
     not moved is a different problem from one nobody has touched. Knowing which
     is which is most of knowing where to spend an afternoon.

What this is careful not to do
------------------------------
Correlation is recorded as correlation. A commit that coincided with a score
moving is *evidence*, not a cause, and the ledger says so — several commits and
an unrelated environment change can land between two runs. The advisor may
report "every palette attempt on this case has failed"; it may not report
"palette attempts cause failure".

And nothing here ever writes a score. Scores come from the improve loop, which
is the only thing that measures. The ledger records observations about scores
the loop already produced.
"""

from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from .meedo_advisor import (
    NOISE_FLOOR,
    Run,
    Suggestion,
    diff_runs,
    load_runs,
)

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "qa_logos" / "synthetic" / "meedo_ledger.json"

# Observations are cheap but not free; the same cap the training memory uses.
MAX_OBSERVATIONS = 200
# A proposal older than this without a verdict is abandoned rather than left
# open forever — an unresolved proposal pollutes the hit rate.
STALE_AFTER_RUNS = 6


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_sha() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _git_subject() -> str:
    try:
        r = subprocess.run(
            ["git", "log", "-1", "--pretty=%s"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------


def load(path: Path | None = None) -> dict:
    p = path or LEDGER
    if not p.is_file():
        return {"version": 1, "observations": [], "proposals": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A corrupt ledger must not stop a run. Start clean rather than raise;
        # the improve log is the source of truth and can rebuild this.
        return {"version": 1, "observations": [], "proposals": []}
    data.setdefault("observations", [])
    data.setdefault("proposals", [])
    return data


def save(data: dict, path: Path | None = None) -> None:
    p = path or LEDGER
    data["observations"] = data.get("observations", [])[-MAX_OBSERVATIONS:]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# 1. record what happened
# --------------------------------------------------------------------------


def observe(
    runs: list[Run] | None = None,
    path: Path | None = None,
    note: str = "",
) -> dict:
    """Record the latest run against the commit it ran on.

    Idempotent: observing the same run twice updates the entry rather than
    duplicating it, so this is safe to call after every loop.
    """
    runs = runs if runs is not None else load_runs()
    if not runs:
        return {"recorded": False, "reason": "no runs"}

    current = runs[-1]
    previous = runs[-2] if len(runs) > 1 else None
    regressions, improvements = (
        diff_runs(previous, current) if previous else ([], [])
    )

    scored = current.scored()
    entry = {
        "run_id": current.run_id,
        "ts": _now(),
        "git_sha": _git_sha(),
        "git_subject": _git_subject(),
        "note": note,
        "mean_composite": round(current.mean_composite(), 4),
        "anchor_mean": round(current.anchor_mean(), 4),
        "n_scored": len(scored),
        "moved": {
            d.case: round(d.change, 4)
            for d in (regressions + improvements)
        },
        "cases": {
            f"{r.get('pair_id')}::{r.get('engine')}": round(r["composite"], 4)
            for r in scored
        },
    }

    data = load(path)
    obs = [o for o in data["observations"] if o.get("run_id") != current.run_id]
    obs.append(entry)
    obs.sort(key=lambda o: str(o.get("run_id")))
    data["observations"] = obs

    resolved = _resolve_proposals(data, current)
    save(data, path)
    return {
        "recorded": True,
        "run_id": current.run_id,
        "moved": len(entry["moved"]),
        "resolved_proposals": resolved,
    }


# --------------------------------------------------------------------------
# 2. track whether advice paid off
# --------------------------------------------------------------------------


def propose(
    suggestions: list[Suggestion],
    run_id: str,
    path: Path | None = None,
) -> int:
    """Log proposals so a later run can judge them.

    Only suggestions naming a case can be resolved — a proposal with no
    measurable subject cannot be scored, so it is not recorded and cannot
    inflate the hit rate.
    """
    data = load(path)
    existing = {(p["run_id"], p["headline"]) for p in data["proposals"]}
    added = 0
    for s in suggestions:
        case = _case_from_headline(s.headline)
        if not case:
            continue
        key = (run_id, s.headline)
        if key in existing:
            continue
        data["proposals"].append(
            {
                "run_id": run_id,
                "ts": _now(),
                "priority": s.priority,
                "headline": s.headline,
                "case": case,
                "rationale": s.rationale,
                "status": "open",
            }
        )
        added += 1
    save(data, path)
    return added


def _case_from_headline(headline: str) -> str:
    for token in headline.replace(",", " ").split():
        if "::" in token:
            return token.strip(".:")
    return ""


def _resolve_proposals(data: dict, current: Run) -> int:
    """Judge open proposals against the run that just landed."""
    cases = {
        f"{r.get('pair_id')}::{r.get('engine')}": r["composite"]
        for r in current.scored()
    }
    order = [o.get("run_id") for o in data["observations"]]
    resolved = 0

    for prop in data["proposals"]:
        if prop.get("status") != "open":
            continue
        if prop["run_id"] == current.run_id:
            continue  # cannot judge a proposal by the run that produced it

        before = _score_at(data, prop["run_id"], prop["case"])
        after = cases.get(prop["case"])
        if before is None or after is None:
            # Still unjudgeable. Abandon once it is too old to attribute.
            try:
                age = len(order) - 1 - order.index(prop["run_id"])
            except ValueError:
                age = len(order)
            if age > STALE_AFTER_RUNS:
                prop["status"] = "abandoned"
                resolved += 1
            continue

        delta = after - before
        prop["delta"] = round(delta, 4)
        prop["resolved_run"] = current.run_id
        if delta > NOISE_FLOOR:
            prop["status"] = "helped"
        elif delta < -NOISE_FLOOR:
            prop["status"] = "hurt"
        else:
            prop["status"] = "no_change"
        resolved += 1
    return resolved


def _score_at(data: dict, run_id: str, case: str) -> float | None:
    for o in data["observations"]:
        if o.get("run_id") == run_id:
            v = (o.get("cases") or {}).get(case)
            return float(v) if v is not None else None
    return None


# --------------------------------------------------------------------------
# 3. what has been learned
# --------------------------------------------------------------------------


@dataclass
class CaseKnowledge:
    case: str
    samples: int
    best: float
    worst: float
    latest: float
    times_moved: int
    attempts: int = 0
    helped: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def never_moved(self) -> bool:
        return self.samples >= 3 and self.times_moved == 0

    def as_dict(self) -> dict:
        return {
            "case": self.case,
            "samples": self.samples,
            "best": round(self.best, 4),
            "worst": round(self.worst, 4),
            "latest": round(self.latest, 4),
            "times_moved": self.times_moved,
            "attempts": self.attempts,
            "helped": self.helped,
            "never_moved": self.never_moved,
            "notes": self.notes,
        }


def case_knowledge(path: Path | None = None) -> dict[str, CaseKnowledge]:
    """Everything the ledger knows about each case, from first-hand history."""
    data = load(path)
    series: dict[str, list[float]] = defaultdict(list)
    moves: dict[str, int] = defaultdict(int)
    for o in data["observations"]:
        for case, score in (o.get("cases") or {}).items():
            series[case].append(float(score))
        for case in (o.get("moved") or {}):
            moves[case] += 1

    attempts: dict[str, int] = defaultdict(int)
    helped: dict[str, int] = defaultdict(int)
    for p in data["proposals"]:
        if p.get("status") in ("helped", "hurt", "no_change"):
            attempts[p["case"]] += 1
            if p["status"] == "helped":
                helped[p["case"]] += 1

    out: dict[str, CaseKnowledge] = {}
    for case, vals in series.items():
        if not vals:
            continue
        k = CaseKnowledge(
            case=case,
            samples=len(vals),
            best=max(vals),
            worst=min(vals),
            latest=vals[-1],
            times_moved=moves.get(case, 0),
            attempts=attempts.get(case, 0),
            helped=helped.get(case, 0),
        )
        if k.never_moved and k.attempts:
            k.notes.append(
                f"{k.attempts} proposal(s) recorded against it, none moved it"
            )
        elif k.never_moved:
            k.notes.append("no recorded attempt has targeted it")
        out[case] = k
    return out


def hit_rate(path: Path | None = None) -> dict:
    """How often advice has actually helped, overall and by priority.

    Recorded honestly: a proposal that resolved to no_change counts against the
    rate. An advisor whose suggestions never move anything should be able to
    see that in its own ledger.
    """
    data = load(path)
    judged = [
        p for p in data["proposals"]
        if p.get("status") in ("helped", "hurt", "no_change")
    ]
    by_priority: dict[int, list[str]] = defaultdict(list)
    for p in judged:
        by_priority[int(p.get("priority", 0))].append(p["status"])

    def rate(statuses: list[str]) -> float:
        return (
            round(sum(1 for s in statuses if s == "helped") / len(statuses), 3)
            if statuses else 0.0
        )

    return {
        "judged": len(judged),
        "open": sum(1 for p in data["proposals"] if p.get("status") == "open"),
        "abandoned": sum(
            1 for p in data["proposals"] if p.get("status") == "abandoned"
        ),
        "overall": rate([p["status"] for p in judged]),
        "by_priority": {
            str(k): {"n": len(v), "helped_rate": rate(v)}
            for k, v in sorted(by_priority.items())
        },
    }


def trend(path: Path | None = None, window: int = 5) -> dict:
    """Whether the engine as a whole is moving, over the recent window."""
    data = load(path)
    obs = data["observations"][-window:]
    if len(obs) < 2:
        return {"samples": len(obs)}
    means = [float(o.get("mean_composite") or 0.0) for o in obs]
    anchors = [float(o.get("anchor_mean") or 0.0) for o in obs]
    return {
        "samples": len(obs),
        "mean_composite_first": means[0],
        "mean_composite_last": means[-1],
        "mean_composite_change": round(means[-1] - means[0], 4),
        "anchor_first": anchors[0],
        "anchor_last": anchors[-1],
        "anchor_change": round(anchors[-1] - anchors[0], 4),
        "flat": abs(means[-1] - means[0]) <= NOISE_FLOOR,
        "mean_of_window": round(mean(means), 4),
    }


def knowledge_report(path: Path | None = None) -> dict:
    """The accumulated, engine-specific picture."""
    knowledge = case_knowledge(path)
    never = sorted(
        (k for k in knowledge.values() if k.never_moved),
        key=lambda k: k.latest,
    )
    try:
        from .meedo_review import catches

        caught = catches(path)
    except Exception:
        caught = {"reviewed": 0, "blocked": 0, "by_check": {}, "recent_blocks": []}
    return {
        "cases_tracked": len(knowledge),
        "observations": len(load(path)["observations"]),
        "trend": trend(path),
        "hit_rate": hit_rate(path),
        "never_moved": [k.as_dict() for k in never[:10]],
        # What Meedo-Me has stopped from shipping or being reported. The
        # measure of the reviewer is here, not in any score.
        "review": caught,
    }


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .meedo_advisor import analyse, load_lessons, suggest

    p = argparse.ArgumentParser(
        description="Meedo-Me ledger — record runs and learn from outcomes"
    )
    p.add_argument(
        "command",
        choices=["observe", "report", "propose"],
        help="observe: record the latest run; propose: log current advice; "
             "report: what has been learned",
    )
    p.add_argument("--note", default="", help="What changed before this run")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    if a.command == "observe":
        res = observe(note=a.note)
        print(json.dumps(res, indent=2) if a.json else
              f"recorded {res.get('run_id')}: {res.get('moved', 0)} case(s) moved, "
              f"{res.get('resolved_proposals', 0)} proposal(s) resolved")
        return 0

    if a.command == "propose":
        runs = load_runs()
        if not runs:
            print("no runs yet")
            return 0
        ideas = suggest(analyse(runs, load_lessons()), load_lessons())
        n = propose(ideas, runs[-1].run_id)
        print(f"logged {n} proposal(s) against {runs[-1].run_id}")
        return 0

    report = knowledge_report()
    if a.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"Meedo-Me ledger — {report['observations']} run(s) observed, "
          f"{report['cases_tracked']} case(s) tracked")
    t = report["trend"]
    if t.get("samples", 0) >= 2:
        print(f"  trend over {t['samples']} runs: "
              f"mean {t['mean_composite_first']} -> {t['mean_composite_last']} "
              f"({t['mean_composite_change']:+.4f})"
              + ("  [flat]" if t["flat"] else ""))
    h = report["hit_rate"]
    print(f"  advice judged: {h['judged']}  helped-rate {h['overall']}  "
          f"(open {h['open']}, abandoned {h['abandoned']})")
    for pri, stats in h["by_priority"].items():
        print(f"    P{pri}: {stats['n']} judged, helped {stats['helped_rate']}")
    if report["never_moved"]:
        print("\n  Never moved in recorded history:")
        for k in report["never_moved"]:
            note = f" — {k['notes'][0]}" if k["notes"] else ""
            print(f"    {k['latest']:.4f}  {k['case']}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CaseKnowledge",
    "LEDGER",
    "load",
    "save",
    "observe",
    "propose",
    "case_knowledge",
    "hit_rate",
    "trend",
    "knowledge_report",
]
