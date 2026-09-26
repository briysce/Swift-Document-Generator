"""Merge Meedo-Me's memory when two agents wrote to it on different branches.

Claude Code and Cursor work the same repository at the same time, each on its
own branch, and both add to Meedo-Me's memory: episodes, observations,
proposals, decisions, reviews. A line-based merge of those JSON files conflicts
on every concurrent append. This merges them by meaning instead:

  * episodes, observations and reviews are unioned by identity;
  * a proposal decided on one side and still open on the other keeps the
    decision; decided on both, the later decision stands;
  * two different episodes that took the same id (both sides wrote "E0026")
    both survive — the incoming one is renumbered.

Installed as a git merge driver by scripts/setup_collab.sh:

    git config merge.meedo.driver "python3 -m tools.logo_vectorizer.meedo_merge %O %A %B"
    # .gitattributes: qa_logos/synthetic/meedo_*.json merge=meedo

As a driver it reads base (%O), ours (%A) and theirs (%B), writes the result
over ours, and exits 0.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_DECIDED = ("accepted", "rejected", "helped", "hurt", "no_change", "abandoned")
_RANK = {"open": 0, "escalated": 0, "accepted": 1, "rejected": 1, "helped": 2, "hurt": 2, "no_change": 2, "abandoned": 2}


def _load(p: str | Path) -> dict | None:
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _review_key(r: dict) -> tuple:
    return (r.get("ts"), r.get("case"), r.get("candidate"), r.get("run_id"))


def merge_episodes(ours: dict, theirs: dict) -> dict:
    mine = list(ours.get("episodes", []))
    ids = {e["id"]: e for e in mine}

    def next_id() -> str:
        n = max((int(m.group(1)) for i in ids if (m := re.match(r"E(\d+)", i))), default=0) + 1
        return f"E{n:04d}"

    for e in theirs.get("episodes", []):
        have = ids.get(e["id"])
        if have is None:
            mine.append(e)
            ids[e["id"]] = e
        elif have != e and have.get("problem") != e.get("problem"):
            e = dict(e, id=next_id(), renumbered_from=e["id"])
            mine.append(e)
            ids[e["id"]] = e
        elif have != e:
            # Same episode, edited on both sides (e.g. closed on one): keep the
            # side that knows more — a method beats none, closed beats open.
            if (e.get("method") and not have.get("method")) or (have.get("outcome") == "open" and e.get("outcome") != "open"):
                mine[mine.index(have)] = e
                ids[e["id"]] = e
    mine.sort(key=lambda e: (e.get("ts", ""), e["id"]))
    return {**ours, "episodes": mine}


def merge_ledger(ours: dict, theirs: dict) -> dict:
    out = dict(ours)
    obs = {o["run_id"]: o for o in ours.get("observations", [])}
    for o in theirs.get("observations", []):
        obs.setdefault(o["run_id"], o)
    out["observations"] = sorted(obs.values(), key=lambda o: str(o.get("run_id")))

    props = {p["id"]: p for p in ours.get("proposals", [])}
    for p in theirs.get("proposals", []):
        have = props.get(p["id"])
        if have is None:
            props[p["id"]] = p
            continue
        a, b = _RANK.get(have.get("status", "open"), 0), _RANK.get(p.get("status", "open"), 0)
        if b > a or (b == a and str(p.get("decided_at", "")) > str(have.get("decided_at", ""))):
            props[p["id"]] = p
    out["proposals"] = list(props.values())

    reviews = {_review_key(r): r for r in ours.get("reviews", [])}
    for r in theirs.get("reviews", []):
        k = _review_key(r)
        if k not in reviews or (r.get("retracted") and not reviews[k].get("retracted")):
            reviews[k] = r
    out["reviews"] = sorted(reviews.values(), key=lambda r: str(r.get("ts", "")))
    return out


def merge_journal(ours: dict, theirs: dict) -> dict:
    """Union work-journal entries by id; never drop either agent's units."""
    mine = list(ours.get("entries", []))
    ids = {e.get("id"): e for e in mine if e.get("id")}
    for e in theirs.get("entries", []):
        eid = e.get("id")
        if not eid or eid not in ids:
            mine.append(e)
            if eid:
                ids[eid] = e
        elif ids[eid] != e:
            # Same id, richer evidence wins (more evidence keys / longer summary).
            have = ids[eid]
            if len(e.get("evidence") or {}) > len(have.get("evidence") or {}) or (
                len(e.get("summary") or "") > len(have.get("summary") or "")
            ):
                mine[mine.index(have)] = e
                ids[eid] = e
    mine.sort(key=lambda e: (e.get("ts", ""), e.get("id", "")))
    return {**ours, "version": ours.get("version", 1), "entries": mine}


def merge(ours_path: str, theirs_path: str) -> bool:
    ours, theirs = _load(ours_path), _load(theirs_path)
    if ours is None or theirs is None:
        return False
    if "lessons" in ours or "lessons" in theirs:
        # meedo_ai_lessons.json — union by lesson id / problem+method.
        result = merge_ai_lessons(
            ours or {"version": 1, "lessons": []},
            theirs or {"version": 1, "lessons": []},
        )
    elif "episodes" in ours or "episodes" in theirs:
        result = merge_episodes(ours, theirs)
    elif "entries" in ours or "entries" in theirs:
        result = merge_journal(ours or {"version": 1, "entries": []},
                               theirs or {"version": 1, "entries": []})
    else:
        result = merge_ledger(ours, theirs)
    Path(ours_path).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return True


def merge_ai_lessons(ours: dict, theirs: dict) -> dict:
    """Union Gemini↔Claude lessons by id; richer counters / method wins."""
    mine = list(ours.get("lessons", []))
    ids = {L.get("id"): L for L in mine if L.get("id")}
    for L in theirs.get("lessons", []):
        lid = L.get("id")
        if not lid or lid not in ids:
            mine.append(L)
            if lid:
                ids[lid] = L
            continue
        have = ids[lid]
        if have == L:
            continue
        # Prefer the side that was applied offline more (hand-off progress)
        # or has a longer method.
        score_have = int(have.get("times_applied_offline") or 0) * 10 + len(have.get("method") or "")
        score_new = int(L.get("times_applied_offline") or 0) * 10 + len(L.get("method") or "")
        if score_new > score_have:
            mine[mine.index(have)] = L
            ids[lid] = L
        else:
            # Merge counters at least.
            have["times_recalled"] = max(
                int(have.get("times_recalled") or 0), int(L.get("times_recalled") or 0)
            )
            have["times_applied_offline"] = max(
                int(have.get("times_applied_offline") or 0),
                int(L.get("times_applied_offline") or 0),
            )
    mine.sort(key=lambda e: (e.get("ts", ""), e.get("id", "")))
    return {**ours, "version": ours.get("version", 1), "lessons": mine}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 3:
        print("usage: meedo_merge BASE OURS THEIRS", file=sys.stderr)
        return 2
    _base, ours, theirs = argv[:3]
    return 0 if merge(ours, theirs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
