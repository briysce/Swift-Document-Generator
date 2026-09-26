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
    comparable_previous,
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
# The same advice raised this many times without anyone deciding on it stops
# being one more line in a list and goes to the top of the standup.
ESCALATE_AFTER = 3
# Statuses in which a proposal is still one piece of advice, not a new one.
_LIVE = ("open", "accepted", "rejected")


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
    _migrate(data)
    if path is not None:
        data["_path"] = str(path)
    return data


def _kind(headline: str, case: str) -> str:
    """The advice with its subject and its numbers taken out.

    "X::y has not moved and sits at 0.4021" and "... sits at 0.4019" are the
    same advice about the same case; the score in the sentence is not what is
    being advised.
    """
    import re

    text = headline.replace(case, "")
    text = re.sub(r"[-+]?\d+\.\d+", "#", text)
    return " ".join(text.split()).strip(" :")


def _pid(case: str, kind: str) -> str:
    import hashlib

    return hashlib.sha1(f"{case}|{kind}".encode()).hexdigest()[:8]


def _migrate(data: dict) -> None:
    """Bring older proposals under the decide-then-judge lifecycle.

    Before it, every proposal was judged by whether its case moved on the next
    run, whether or not anyone acted on it. Twenty-eight proposals went into a
    file nobody read, were all scored no_change, and gave Meedo-Me a 0% hit
    rate that measured whether problems fixed themselves. Advice nobody took
    was never tested, so it goes back to awaiting a decision; and the same
    advice filed six times is one proposal raised six times.
    """
    props = data.get("proposals", [])
    if all("id" in p for p in props):
        return
    merged: dict[str, dict] = {}
    for p in props:
        case = p.get("case", "")
        kind = _kind(p.get("headline", ""), case)
        pid = _pid(case, kind)
        if not p.get("accepted_run"):
            for k in ("delta", "resolved_run"):
                p.pop(k, None)
            if p.get("status") in ("helped", "hurt", "no_change"):
                p["status"] = "open"
        cur = merged.get(pid)
        if cur is None:
            p.update({"id": pid, "kind": kind, "raised": 1,
                      "last_raised": p.get("run_id")})
            merged[pid] = p
            continue
        if p.get("run_id") != cur.get("last_raised"):
            cur["raised"] = cur.get("raised", 1) + 1
            cur["last_raised"] = max(str(cur.get("last_raised")), str(p.get("run_id")))
    for cur in merged.values():
        if cur["raised"] >= ESCALATE_AFTER and cur.get("status") == "open":
            cur["escalated"] = True
    data["proposals"] = list(merged.values())


def save(data: dict, path: Path | None = None) -> None:
    p = path or LEDGER
    data.pop("_path", None)
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
    previous = comparable_previous(runs)
    regressions, improvements = (
        diff_runs(previous, current) if previous else ([], [])
    )

    scored = current.scored()
    entry = {
        "run_id": current.run_id,
        "ts": _now(),
        "config": current.config(),
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
    added = 0
    declined = declined_engines(data)
    withheld = 0
    for s in suggestions:
        case = _case_from_headline(s.headline)
        if not case:
            continue
        if case.split("::")[-1] in declined:
            withheld += 1
            continue
        kind = _kind(s.headline, case)
        pid = _pid(case, kind)
        prior = next(
            (p for p in data["proposals"]
             if p.get("id") == pid and p.get("status") in _LIVE),
            None,
        )
        if prior is not None:
            # Already said. Saying it again does not add a proposal — it adds
            # weight to the one already waiting, and past a point it escalates.
            # A rejected proposal is not reopened by repetition; the reason it
            # was rejected stands until someone changes their mind.
            if prior.get("last_raised") != run_id:
                prior["raised"] = prior.get("raised", 1) + 1
                prior["last_raised"] = run_id
                if (prior["raised"] >= ESCALATE_AFTER
                        and prior.get("status") == "open"
                        and not prior.get("escalated")):
                    prior["escalated"] = True
                    prior["escalated_at"] = run_id
            continue
        data["proposals"].append(
            {
                "id": pid,
                "run_id": run_id,
                "ts": _now(),
                "priority": s.priority,
                "headline": s.headline,
                "case": case,
                "kind": kind,
                "rationale": s.rationale,
                "status": "open",
                "raised": 1,
                "last_raised": run_id,
            }
        )
        added += 1
    data["withheld_last"] = {"run_id": run_id, "count": withheld,
                             "engines": sorted(declined)}
    save(data, path)
    return added


def declined_engines(data: dict) -> set[str]:
    """Engines Meedo-Me has learned not to propose work on.

    Its first standup rejected seven of eleven proposals for one of two
    reasons: the engine was the control (improving it moves the yardstick, not
    the product), or it produced a raster when the product is the vector. An
    advisor that keeps making a suggestion its manager keeps rejecting for the
    same reason is not advising, it is nagging. Once an engine has at least two
    rejected proposals and none accepted, advice about it is withheld — and
    counted as withheld, so it stays visible rather than going silent.
    Accepting any proposal on that engine reverses it.
    """
    rejected: dict[str, int] = defaultdict(int)
    taken: dict[str, int] = defaultdict(int)
    for p in data.get("proposals", []):
        engine = str(p.get("case", "")).split("::")[-1]
        if p.get("status") == "rejected":
            rejected[engine] += 1
        elif p.get("status") in ("accepted", "helped", "hurt", "no_change"):
            taken[engine] += 1
    return {e for e, n in rejected.items() if n >= 2 and not taken.get(e)}


def decide(
    pid: str,
    decision: str,
    reason: str,
    by: str = "",
    path: Path | None = None,
) -> dict:
    """Accept or reject a proposal, with a reason.

    Advice is only tested once someone acts on it, so only accepted proposals
    are judged by later runs, and they are judged from the run on which they
    were accepted — that is when the work starts. A rejection needs a reason:
    "no" with nothing after it teaches Meedo-Me nothing.
    """
    if decision not in ("accept", "reject"):
        raise ValueError("decision must be 'accept' or 'reject'")
    if not reason.strip():
        raise ValueError("a decision needs a reason")
    data = load(path)
    prop = next((p for p in data["proposals"] if p.get("id") == pid
                 and p.get("status") in _LIVE), None)
    if prop is None:
        raise KeyError(f"no live proposal {pid}")
    runs = [o.get("run_id") for o in data["observations"]]
    prop["status"] = "accepted" if decision == "accept" else "rejected"
    prop["decision_reason"] = reason.strip()
    prop["decided_by"] = by
    prop["decided_at"] = _now()
    if decision == "accept":
        prop["accepted_run"] = runs[-1] if runs else prop.get("run_id")
    prop.pop("escalated", None)
    save(data, path)
    return prop


def standup(path: Path | None = None) -> list[dict]:
    """What is waiting on a decision, most pressing first.

    Escalated advice leads — it has been raised repeatedly and nobody has
    decided. Then by priority (1 is highest), then by how often it was raised.
    """
    data = load(path)
    waiting = [p for p in data["proposals"] if p.get("status") == "open"]
    waiting.sort(key=lambda p: (not p.get("escalated"), int(p.get("priority", 9)),
                                -int(p.get("raised", 1))))
    return waiting


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
        # Only advice someone acted on is tested. Judging an open proposal by
        # whether its case happened to move measures whether problems fix
        # themselves, and scored 28 untaken suggestions as failures.
        if prop.get("status") != "accepted":
            continue
        start = prop.get("accepted_run") or prop["run_id"]
        if start == current.run_id:
            continue  # cannot judge a proposal by the run it was accepted on

        before = _score_at(data, start, prop["case"])
        after = cases.get(prop["case"])
        if before is None or after is None:
            # Still unjudgeable. Abandon once it is too old to attribute.
            try:
                age = len(order) - 1 - order.index(start)
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
        # Every judged piece of advice becomes an episode on its own, so
        # Meedo-Me learns what its advice does without anyone writing it down.
        try:
            from .meedo_episodes import from_judged_proposal

            from_judged_proposal(prop, path=_episodes_path(data))
        except Exception:
            pass
    return resolved


def _episodes_path(data: dict) -> Path | None:
    """Episodes live beside whichever ledger is in use (tests use a temp one)."""
    lp = data.get("_path")
    return Path(lp).parent / "meedo_episodes.json" if lp else None


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
        "awaiting_decision": sum(1 for p in data["proposals"] if p.get("status") == "open"),
        "escalated": sum(1 for p in data["proposals"] if p.get("escalated")),
        "accepted_in_progress": sum(1 for p in data["proposals"] if p.get("status") == "accepted"),
        "rejected": sum(1 for p in data["proposals"] if p.get("status") == "rejected"),
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


def _runs_from_ledger(path: Path | None = None) -> list[Run]:
    """Rebuild Run objects from ledger observations when improve_log is absent.

    The log is gitignored; agents on a fresh clone still need propose/standup.
    Each observation's per-case composites become scored rows for analyse().
    """
    data = load(path)
    out: list[Run] = []
    for o in data.get("observations") or []:
        rid = o.get("run_id") or ""
        rows = []
        for case, comp in (o.get("cases") or {}).items():
            if "::" not in str(case):
                continue
            pair, engine = str(case).rsplit("::", 1)
            slug = pair.split("__", 1)[0]
            rows.append(
                {
                    "run_id": rid,
                    "pair_id": pair,
                    "slug": slug,
                    "engine": engine,
                    "anchor": slug.startswith("swift_orange"),
                    "ok": True,
                    "composite": float(comp),
                    **(o.get("config") or {}),
                }
            )
        if rows:
            out.append(Run(rid, rows))
    return out


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
        choices=["observe", "report", "propose", "standup", "decide"],
        help="observe: record the latest run; propose: log current advice; "
             "report: what has been learned; standup: what awaits a decision; "
             "decide: accept or reject a proposal",
    )
    p.add_argument("args", nargs="*", help="decide: <id> accept|reject <reason>")
    p.add_argument("--note", default="", help="What changed before this run")
    p.add_argument("--by", default="", help="decide: who decided")
    p.add_argument("--json", action="store_true")
    # Intermixed, so options may sit anywhere: `decide --by claude <id> ...`
    # otherwise leaves the reason unparsed.
    a = p.parse_intermixed_args(argv)

    if a.command == "standup":
        waiting = standup()
        if a.json:
            print(json.dumps(waiting, indent=2))
            return 0
        if not waiting:
            print("Meedo-Me standup: nothing awaiting a decision")
            return 0
        print(f"Meedo-Me standup — {len(waiting)} proposal(s) awaiting a decision")
        try:
            from .meedo_episodes import recall
        except Exception:
            recall = None
        for q in waiting:
            flag = "ESCALATED " if q.get("escalated") else ""
            print(f"  [{q['id']}] {flag}P{q.get('priority')} x{q.get('raised', 1)}  {q['headline']}")
            print(f"           {q.get('rationale', '')}")
            # What Meedo-Me remembers about problems like this one.
            if recall is not None:
                for ep in recall(q["headline"] + " " + q.get("rationale", ""),
                                 cases=[q.get("case", "")], top=1):
                    if ep.get("method"):
                        print(f"           remembered {ep['id']} ({ep['outcome']}): {ep['method'][:180]}")
        print("\ndecide with: python -m tools.logo_vectorizer.meedo_ledger decide <id> accept|reject \"<reason>\"")
        return 0

    if a.command == "decide":
        if len(a.args) < 3:
            print("usage: decide <id> accept|reject <reason>")
            return 2
        prop = decide(a.args[0], a.args[1], " ".join(a.args[2:]), by=a.by)
        print(f"{prop['id']} -> {prop['status']}: {prop['decision_reason']}")
        return 0

    if a.command == "observe":
        res = observe(note=a.note)
        print(json.dumps(res, indent=2) if a.json else
              f"recorded {res.get('run_id')}: {res.get('moved', 0)} case(s) moved, "
              f"{res.get('resolved_proposals', 0)} proposal(s) resolved")
        return 0

    if a.command == "propose":
        runs = load_runs()
        if not runs:
            # Cloud/CI checkouts often lack gitignored improve_log.jsonl.
            # Rebuild thin Run rows from the ledger so Meedo-Me can still
            # advise instead of going silent ("no runs yet").
            runs = _runs_from_ledger()
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
          f"(awaiting decision {h['awaiting_decision']}, escalated {h['escalated']}, "
          f"in progress {h['accepted_in_progress']}, rejected {h['rejected']}, "
          f"abandoned {h['abandoned']})")
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
    "decide",
    "declined_engines",
    "standup",
    "case_knowledge",
    "hit_rate",
    "trend",
    "knowledge_report",
]
