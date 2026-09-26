"""Meedo-Me — the engine's memory and advisor.

Meedo's Codified Likeness Utility: a partner for improving the restoration and
vectorization engine, rather than another stage inside it.

What it is for
--------------
The engine already produces a lot of evidence about itself — every improve-loop
run appends scored rows to `improve_log.jsonl`, every run snapshot lands in
`training_lessons.json`, and durable lessons accumulate alongside them. That
record is the most valuable thing in the repository and almost none of it gets
read back. A regression that was diagnosed three months ago gets rediagnosed
from scratch; a parameter that was tried and rejected gets tried again.

So this module reads the record and answers three questions:

  1. **What changed?** Which cases moved between runs, and by how much.
  2. **What do we already know?** Which recorded lessons bear on the cases that
     are failing now, recalled by similarity rather than by remembering the
     right search term.
  3. **What should we try next?** Concrete, ranked experiments grounded in the
     measured history — never a guess dressed up as a finding.

The hard rule
-------------
The advisor proposes; the improve loop measures. Nothing here ever writes a
score, claims an improvement, or edits engine behaviour. Every number it reports
is one the loop actually recorded. This separation is the whole point: an
advisor that could mark its own homework would be worse than no advisor, and
this codebase has already been bitten twice by metrics that flattered
themselves — a composite that could not score a perfect restoration, and a
damage score that rated pristine artwork as the most damaged thing in the suite.

Where the model fits
--------------------
A local model can suggest experiments, and `suggest()` will consult one when a
Meedo-Me runtime is reachable. But its suggestions are grounded in the measured
record passed to it, it is asked for options rather than conclusions, and its
absence changes nothing: the deterministic analysis below stands on its own and
is what runs by default. Fail-open, like every other optional stage.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[2]
SYN = ROOT / "qa_logos" / "synthetic"
LOG = SYN / "improve_log.jsonl"
LESSONS = SYN / "training_lessons.json"
SUMMARY = SYN / "improve_summary_latest.json"

# A case has to move by more than this to count as a change rather than noise.
NOISE_FLOOR = 0.002


# --------------------------------------------------------------------------
# reading the record
# --------------------------------------------------------------------------


@dataclass
class Run:
    run_id: str
    rows: list[dict] = field(default_factory=list)

    def scored(self) -> list[dict]:
        return [r for r in self.rows if r.get("ok") and "composite" in r]

    def by_case(self) -> dict[str, dict]:
        """Best row per (pair, engine) — the product path's result."""
        out: dict[str, dict] = {}
        for r in self.scored():
            key = f"{r.get('pair_id')}::{r.get('engine')}"
            prev = out.get(key)
            if prev is None or r["composite"] > prev["composite"]:
                out[key] = r
        return out

    def mean_composite(self) -> float:
        s = self.scored()
        return mean(r["composite"] for r in s) if s else 0.0

    def anchor_mean(self) -> float:
        a = [r for r in self.scored() if r.get("anchor")]
        return mean(r["composite"] for r in a) if a else 0.0

    def config(self) -> dict:
        """The settings that change the numbers. Two runs are comparable only
        when these match; a missing setting is unknown, not a default."""
        r = self.rows[0] if self.rows else {}
        engines = r.get("engines")
        return {
            "min_height": r.get("min_height"),
            "engines": sorted(engines) if isinstance(engines, list) else None,
            "idealize": r.get("idealize"),
            # The minds (collab_mind) first existed on 2026-09-26; no run before
            # that could have had them, so a row without the field had them off.
            "minds": bool(r.get("minds", False)),
            # Minds ranking traced vs idealize (LOGO_MINDS_RANK); same default.
            "minds_rank": bool(r.get("minds_rank", False)),
            # Which inputs were measured. The degraded images are generated
            # locally from pairs.json; regenerating it with another seed once
            # swapped six pairs and reseeded the other twelve, and a Swift mean
            # on the new inputs was read against the old 0.9269 baseline.
            "corpus": self.corpus(),
        }

    def corpus(self) -> str | None:
        """Fingerprint of the pairs this run measured (id and degrade seed,
        both recorded on every row), so runs on different inputs never compare."""
        import hashlib
        import json as _json

        pairs = sorted({(r.get("pair_id"), r.get("seed")) for r in self.rows if r.get("pair_id")},
                       key=lambda t: (str(t[0]), str(t[1])))
        if not pairs:
            return None
        key = [list(t) for t in pairs]
        degrader = int(self.rows[0].get("degrader", 1) or 1)
        if degrader != 1:  # v1 fingerprints stay what they always were
            key.append(["degrader", degrader])
        return hashlib.sha1(_json.dumps(key).encode()).hexdigest()[:10]


def comparable_previous(runs: list[Run]) -> Run | None:
    """The latest run before the last one with the same configuration.

    Comparing across configurations reports settings as regressions: a re-run
    at --min-height 3000 once read as a regression against 1200, and a
    reconstruction run was once read as the shipping path.
    """
    if len(runs) < 2:
        return None
    want = runs[-1].config()
    for r in reversed(runs[:-1]):
        if r.config() == want:
            return r
    return None


def load_runs(limit: int = 12, log: Path | None = None) -> list[Run]:
    """Most recent runs, oldest first. Missing or corrupt log yields []."""
    path = log or LOG
    if not path.is_file():
        return []
    grouped: dict[str, list[dict]] = defaultdict(list)
    order: list[str] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rid = str(row.get("run_id") or "")
                if not rid:
                    continue
                if rid not in grouped:
                    order.append(rid)
                grouped[rid].append(row)
    except OSError:
        return []
    keep = order[-limit:]
    return [Run(run_id=r, rows=grouped[r]) for r in keep]


def load_lessons(path: Path | None = None) -> list[dict]:
    p = path or LESSONS
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    lessons = data.get("lessons")
    return lessons if isinstance(lessons, list) else []


# --------------------------------------------------------------------------
# 1. what changed
# --------------------------------------------------------------------------


@dataclass
class Delta:
    case: str
    before: float
    after: float

    @property
    def change(self) -> float:
        return self.after - self.before


def diff_runs(previous: Run, current: Run) -> tuple[list[Delta], list[Delta]]:
    """(regressions, improvements) between two runs, worst first."""
    a, b = previous.by_case(), current.by_case()
    regressions: list[Delta] = []
    improvements: list[Delta] = []
    for key, after in b.items():
        before = a.get(key)
        if before is None:
            continue
        d = Delta(key, before["composite"], after["composite"])
        if d.change < -NOISE_FLOOR:
            regressions.append(d)
        elif d.change > NOISE_FLOOR:
            improvements.append(d)
    regressions.sort(key=lambda d: d.change)
    improvements.sort(key=lambda d: -d.change)
    return regressions, improvements


def stuck_cases(runs: list[Run], window: int = 3) -> list[str]:
    """Cases that have not moved at all across the last `window` runs.

    A case pinned to the same value run after run is either solved or stuck,
    and the two look identical in a summary. Cross-referencing against its
    score tells you which.
    """
    if len(runs) < window:
        return []
    recent = runs[-window:]
    maps = [r.by_case() for r in recent]
    common = set(maps[0])
    for m in maps[1:]:
        common &= set(m)
    out = []
    for key in common:
        vals = [m[key]["composite"] for m in maps]
        if max(vals) - min(vals) <= 1e-9:
            out.append(key)
    return sorted(out)


# --------------------------------------------------------------------------
# 2. what we already know
# --------------------------------------------------------------------------


def _tokens(text: str) -> set[str]:
    return {
        t
        for t in "".join(c.lower() if c.isalnum() else " " for c in text).split()
        if len(t) > 2
    }


# A lesson that names a case is direct evidence about it. A lesson that merely
# mentions the same words in passing is not, and treating them alike produces
# confident-looking citations that do not support the claim — e.g. a lesson
# about choosing damage metrics being cited as the reason `arc__blur_crush` is
# stuck, purely because it says "blur_crush" somewhere in its body.
_WEIGHT_CASE_ID = 6.0
_WEIGHT_TAG = 2.0
_WEIGHT_TITLE = 1.5
_WEIGHT_BODY = 0.5
_MIN_RELEVANCE = 1.0


def recall_lessons(query: str, lessons: list[dict], top: int = 4) -> list[dict]:
    """Lessons most relevant to `query`, strongest evidence first.

    Deliberately simple. The Qdrant store in `memory.py` is for reconstructions
    keyed by image descriptor; lessons are a few dozen short documents, where a
    vector index would be more machinery than the problem needs. If this ever
    grows past a few hundred lessons, move it there.
    """
    q = _tokens(query)
    if not q:
        return []
    # The case id as written in the log, e.g. "arc__blur_crush".
    case_key = query.split("::")[0].strip().lower()

    scored: list[tuple[float, dict]] = []
    for lesson in lessons:
        score = 0.0

        cases = [str(c).strip().lower() for c in (lesson.get("case_ids") or [])]
        if case_key and case_key in cases:
            score += _WEIGHT_CASE_ID
        elif case_key and any(case_key in c or c in case_key for c in cases):
            score += _WEIGHT_CASE_ID / 2.0

        tags = _tokens(" ".join(str(t) for t in (lesson.get("tags") or [])))
        score += _WEIGHT_TAG * len(q & tags) / max(1, len(q))

        title = _tokens(str(lesson.get("title", "")))
        score += _WEIGHT_TITLE * len(q & title) / max(1, len(q))

        body = _tokens(
            f"{lesson.get('lesson', '')} {lesson.get('do_not_regress', '')}"
        )
        score += _WEIGHT_BODY * len(q & body) / max(1, len(q))

        if score >= _MIN_RELEVANCE:
            scored.append((score, lesson))

    scored.sort(key=lambda t: -t[0])
    return [lesson for _score, lesson in scored[:top]]


def guardrails(lessons: list[dict]) -> list[str]:
    """Every recorded do-not-regress clause.

    These are the promises previous work made. Anything proposed has to be
    checked against them before it is tried, not after it has broken one.
    """
    out = []
    for lesson in lessons:
        g = str(lesson.get("do_not_regress") or "").strip()
        if g:
            out.append(g)
    return out


# --------------------------------------------------------------------------
# 3. what to try next
# --------------------------------------------------------------------------


@dataclass
class Suggestion:
    priority: int          # 1 = highest
    headline: str
    rationale: str
    evidence: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "priority": self.priority,
            "headline": self.headline,
            "rationale": self.rationale,
            "evidence": self.evidence,
        }


def analyse(runs: list[Run] | None = None, lessons: list[dict] | None = None) -> dict:
    """The full deterministic read of the record."""
    runs = runs if runs is not None else load_runs()
    lessons = lessons if lessons is not None else load_lessons()
    if not runs:
        return {"runs": 0, "note": "no run history"}

    current = runs[-1]
    previous = comparable_previous(runs)
    regressions, improvements = (
        diff_runs(previous, current) if previous else ([], [])
    )

    worst = sorted(current.scored(), key=lambda r: r["composite"])[:8]
    return {
        "runs": len(runs),
        "current_run": current.run_id,
        "mean_composite": round(current.mean_composite(), 4),
        "anchor_mean": round(current.anchor_mean(), 4),
        "regressions": [
            {"case": d.case, "from": round(d.before, 4), "to": round(d.after, 4),
             "change": round(d.change, 4)}
            for d in regressions
        ],
        "improvements": [
            {"case": d.case, "from": round(d.before, 4), "to": round(d.after, 4),
             "change": round(d.change, 4)}
            for d in improvements
        ],
        "worst_cases": [
            {"case": f"{r.get('pair_id')}::{r.get('engine')}",
             "composite": round(r["composite"], 4),
             "ink_iou": r.get("ink_iou"),
             "palette_fidelity": r.get("palette_fidelity")}
            for r in worst
        ],
        "stuck": stuck_cases(runs),
        "guardrails": guardrails(lessons),
        "lesson_count": len(lessons),
    }


def suggest(report: dict | None = None, lessons: list[dict] | None = None) -> list[Suggestion]:
    """Ranked experiments, each tied to something in the record.

    Every suggestion cites the evidence that motivated it. A suggestion with no
    evidence behind it is not worth an engineer's afternoon.
    """
    report = report if report is not None else analyse()
    lessons = lessons if lessons is not None else load_lessons()
    out: list[Suggestion] = []

    # A regression outranks everything. Something that used to work now does
    # not, which is a bug with a known-good starting point — far cheaper to
    # chase than a case that has never worked.
    for reg in report.get("regressions", [])[:3]:
        case = reg["case"]
        related = recall_lessons(case.replace("::", " "), lessons, top=2)
        out.append(
            Suggestion(
                priority=1,
                headline=f"Bisect the regression on {case}",
                rationale=(
                    f"Dropped {abs(reg['change']):.4f} "
                    f"({reg['from']:.4f} -> {reg['to']:.4f}). It passed before, "
                    "so the change that broke it is in recent history."
                ),
                evidence=[f"lesson: {lesson.get('title')}" for lesson in related],
            )
        )

    # A case pinned at a low score across runs is not being worked on by
    # accident — every recent change has missed it entirely.
    worst = {w["case"]: w for w in report.get("worst_cases", [])}
    for case in report.get("stuck", []):
        w = worst.get(case)
        if w is None or w["composite"] > 0.75:
            continue  # stuck high is solved, not stuck
        related = recall_lessons(case.replace("::", " "), lessons, top=2)
        known = [
            lesson.get("title")
            for lesson in related
            if "honest-limit" in (lesson.get("tags") or [])
        ]
        out.append(
            Suggestion(
                priority=2 if not known else 4,
                headline=f"{case} has not moved and sits at {w['composite']:.4f}",
                rationale=(
                    "Recorded as an honest limit — confirm before spending on it."
                    if known
                    else "Unchanged across recent runs, so nothing tried lately "
                    "touched it. Needs a different approach, not more tuning."
                ),
                evidence=[f"lesson: {t}" for t in known]
                or [f"palette_fidelity={w.get('palette_fidelity')}",
                    f"ink_iou={w.get('ink_iou')}"],
            )
        )

    # Palette collapse and geometry collapse have different fixes, and the
    # sub-metrics say which one you are looking at.
    for w in report.get("worst_cases", [])[:4]:
        pal = w.get("palette_fidelity")
        iou = w.get("ink_iou")
        if pal is not None and pal < 0.05 and (iou or 0) > 0.2:
            out.append(
                Suggestion(
                    priority=3,
                    headline=f"{w['case']}: palette is gone, geometry survived",
                    rationale=(
                        f"palette_fidelity={pal}, ink_iou={iou}. The shape is "
                        "recoverable and the colour is not, so this is a "
                        "colour-recovery problem. Do not invent a brand colour "
                        "from grey — that is already a recorded lesson."
                    ),
                    evidence=[f"case: {w['case']}"],
                )
            )

    out.sort(key=lambda s: s.priority)
    return out


# --------------------------------------------------------------------------
# optional model consultation
# --------------------------------------------------------------------------


def consult_model(report: dict, timeout: int = 90) -> str | None:
    """Ask a local Meedo-Me runtime for ideas, grounded in `report`.

    Returns None when no runtime is reachable, which is the common case and not
    an error. The model is asked for candidate experiments only; it is never
    asked whether something improved, because only the loop can answer that.
    """
    try:
        import urllib.error
        import urllib.request
    except Exception:
        return None

    import os

    base = os.environ.get("MEEDO_ME_BASE_URL", "http://localhost:1337/v1").rstrip("/")
    model = os.environ.get("MEEDO_ME_MODEL", "qwen3:8b")
    prompt = (
        "You advise a raster-to-vector logo restoration engine. Below is the "
        "measured record of its recent runs. Propose at most three concrete "
        "experiments worth trying next.\n\n"
        "Rules:\n"
        "- Only reason from the numbers given. Do not assert an improvement; "
        "you cannot measure one.\n"
        "- Respect every guardrail listed; they are promises earlier work made.\n"
        "- Prefer a specific change to a named stage over general advice.\n\n"
        f"{json.dumps(report, indent=2)}\n"
    )
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
            "max_tokens": 700,
            "stream": False,
        }
    ).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        choices = data.get("choices") or []
        if not choices:
            return None
        text = (choices[0].get("message") or {}).get("content")
        return text if isinstance(text, str) and text.strip() else None
    except Exception:
        return None


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="Meedo-Me — read the engine's own record and advise"
    )
    p.add_argument("--runs", type=int, default=12, help="How many runs to read")
    p.add_argument("--json", action="store_true", help="Machine-readable output")
    p.add_argument(
        "--ask-model",
        action="store_true",
        help="Also consult a local Meedo-Me runtime (fails open when absent)",
    )
    p.add_argument("--recall", default="", help="Recall lessons matching this text")
    a = p.parse_args(argv)

    runs = load_runs(limit=a.runs)
    lessons = load_lessons()

    if a.recall:
        hits = recall_lessons(a.recall, lessons)
        if a.json:
            print(json.dumps(hits, indent=2))
        else:
            print(f"Lessons matching {a.recall!r}:\n")
            for lesson in hits:
                print(f"  - {lesson.get('title')}")
                print(f"      {str(lesson.get('lesson'))[:200]}...")
                if lesson.get("do_not_regress"):
                    print(f"      do-not-regress: {lesson['do_not_regress']}")
            if not hits:
                print("  (nothing recorded on that)")
        return 0

    report = analyse(runs, lessons)
    ideas = suggest(report, lessons)

    if a.json:
        print(
            json.dumps(
                {"report": report, "suggestions": [s.as_dict() for s in ideas]},
                indent=2,
            )
        )
        return 0

    if not report.get("runs"):
        print("No run history yet — run scripts/logo_restore_improve_loop.py first.")
        return 0

    print(f"Meedo-Me — {report['runs']} runs read, latest {report['current_run']}")
    print(f"  mean composite {report['mean_composite']}   "
          f"anchor mean {report['anchor_mean']}")
    print(f"  {report['lesson_count']} lessons on record\n")

    if report["regressions"]:
        print("REGRESSIONS since the previous run:")
        for r in report["regressions"]:
            print(f"  {r['change']:+.4f}  {r['case']}  ({r['from']} -> {r['to']})")
    else:
        print("No regressions since the previous run.")

    if report["improvements"]:
        print("\nImprovements:")
        for r in report["improvements"][:6]:
            print(f"  {r['change']:+.4f}  {r['case']}")

    print("\nWhat to try next:")
    if not ideas:
        print("  Nothing pressing in the record.")
    for s in ideas:
        print(f"  [P{s.priority}] {s.headline}")
        print(f"         {s.rationale}")
        for e in s.evidence:
            print(f"         - {e}")

    if report["guardrails"]:
        print("\nGuardrails any change must respect:")
        for g in report["guardrails"][:8]:
            print(f"  - {g}")

    if a.ask_model:
        print("\nModel consultation:")
        text = consult_model(report)
        print("  " + (text.replace("\n", "\n  ") if text
                      else "(no Meedo-Me runtime reachable — analysis above stands)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "Run",
    "Delta",
    "Suggestion",
    "load_runs",
    "load_lessons",
    "diff_runs",
    "stuck_cases",
    "recall_lessons",
    "guardrails",
    "analyse",
    "suggest",
    "consult_model",
]
