"""Meedo-Me's memory of every question put to another mind, and how it went.

Gemini and Claude critique the engine's work, guide it, and take over when it
is stuck. Each time either is asked, Meedo-Me keeps the question, the images
it was about, the answer, and — once something shows it — whether the answer
was right: the take-over passed review and beat the engine, the critique named
a fault the reviewer confirmed, the advice moved the score. That record is how
Meedo-Me learns from them:

  * each mind earns a track record per role, so Meedo-Me learns whom to ask
    about what, and when the two disagree, whom the outcome sided with;
  * advice that helped becomes an episode, so `recall` offers it the next time
    the same kind of problem comes up — the method is kept, not only the answer;
  * judged consultations export as a dataset, and a local model (Meedo-Me's
    own, through Ollama) can answer the same questions in shadow: how often it
    agrees with the answer that proved right is its readiness to take the job.

The app writes its own consultations (order acknowledgements, logo restores,
address look-ups) in the same shape; `ingest` brings them into this memory.

    python -m tools.logo_vectorizer.meedo_consult report
    python -m tools.logo_vectorizer.meedo_consult judge <id> helped|hurt|no_change|wrong "<detail>"
    python -m tools.logo_vectorizer.meedo_consult export dataset.jsonl
    python -m tools.logo_vectorizer.meedo_consult ingest app_consultations.jsonl
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSULTATIONS = ROOT / "qa_logos" / "synthetic" / "meedo_consultations.json"

# What an answer turned out to be. `helped`: acting on it made things better
# (or, for a critique, the fault it named was real). `hurt`: acting on it made
# things worse. `no_change`: acted on, nothing moved. `wrong`: not acted on
# because it was plainly mistaken (a hallucinated element, another company's
# logo, a fault that is not there).
RESULTS = ("helped", "hurt", "no_change", "wrong")
MAX_TEXT = 48_000       # an SVG take-over can be long; beyond this it is noise
SHADOW = "meedo"        # the local model answering in shadow


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(path: Path | None = None) -> list[dict]:
    p = Path(path or CONSULTATIONS)
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("consultations", [])
    except (OSError, json.JSONDecodeError):
        return []


def _save(items: list[dict], path: Path | None = None) -> None:
    p = Path(path or CONSULTATIONS)
    p.parent.mkdir(parents=True, exist_ok=True)
    items = sorted(items, key=lambda c: (c["ts"], c["id"]))
    p.write_text(json.dumps({"version": 1, "consultations": items}, indent=2) + "\n", encoding="utf-8")


def _rel(p: str | Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(p)


def record(
    *,
    mind: str,
    model: str,
    role: str,
    question: str,
    answer: str,
    parsed: dict | None = None,
    case: str = "",
    images: list[str | Path] | None = None,
    source: str = "engine",
    session: str = "",
    shadow_of: str = "",
    latency_s: float | None = None,
    error: str = "",
    ts: str = "",
    path: Path | None = None,
) -> dict:
    """Keep one question and its answer. Never raises on a bad answer: a
    failed call is recorded too (`error`), because which mind fails how often
    is part of what Meedo-Me learns."""
    items = load(path)
    ts = ts or _now()
    base = cid = f"{ts}-{mind}-{role}"
    taken = {c["id"] for c in items}
    n = 1
    while cid in taken:
        n += 1
        cid = f"{base}-{n}"
    c = {
        "id": cid,
        "ts": ts,
        "mind": mind,
        "model": model,
        "role": role,
        "source": source,
        "case": case,
        # A session groups the questions asked about one problem at one time,
        # so two minds' answers to the same question can be compared.
        "session": session,
        "question": question[:MAX_TEXT],
        "images": [_rel(i) for i in (images or [])],
        "answer": (answer or "")[:MAX_TEXT],
        "parsed": parsed or {},
        "outcome": None,
    }
    if shadow_of:
        c["shadow_of"] = shadow_of
    if latency_s is not None:
        c["latency_s"] = round(float(latency_s), 2)
    if error:
        c["error"] = error[:2000]
    items.append(c)
    _save(items, path)
    return c


def judge(cid: str, result: str, detail: str = "", *, delta: float | None = None, by: str = "",
          lesson: str = "", path: Path | None = None) -> dict:
    """Say what an answer turned out to be. A `helped` answer that carries a
    method (`lesson`, or the answer's own `advice`) becomes an episode."""
    if result not in RESULTS:
        raise ValueError(f"result must be one of {RESULTS}")
    items = load(path)
    c = next((x for x in items if x["id"] == cid), None)
    if c is None:
        raise KeyError(cid)
    c["outcome"] = {"result": result, "detail": detail.strip(), "judged_at": _now(), "by": by}
    if delta is not None:
        c["outcome"]["delta"] = round(float(delta), 4)
    method = lesson.strip() or str((c.get("parsed") or {}).get("advice", "")).strip()
    if result == "helped" and method and not c.get("lesson"):
        try:
            from . import meedo_episodes as E

            ep_path = Path(path).parent / "meedo_episodes.json" if path else None
            ep = E.record(
                problem=f"{c['role']} on {c['case'] or 'a case'}: {detail or c['question'][:160]}",
                method=method,
                outcome="success",
                workstream="minds",
                evidence=f"{c['mind']} ({c['model']}) consultation {cid}; {detail}".strip(),
                verified=f"judged helped by {by or 'meedo'}" + (f", delta {delta:+.4f}" if delta is not None else ""),
                tags=[c["mind"], c["role"], "learned-from-" + c["mind"]],
                cases=[c["case"]] if c["case"] else [],
                source=c["mind"],
                path=ep_path,
            )
            c["lesson"] = ep["id"]
        except Exception:
            pass
    _save(items, path)
    return c


def track_record(items: list[dict] | None = None, *, path: Path | None = None) -> dict:
    """Per mind and role: asked, failed, judged, and how the judged answers went."""
    items = load(path) if items is None else items
    out: dict[str, dict] = {}
    for c in items:
        key = f"{c['mind']}/{c['role']}"
        r = out.setdefault(key, {"asked": 0, "failed": 0, "judged": 0, **{k: 0 for k in RESULTS}})
        r["asked"] += 1
        if c.get("error"):
            r["failed"] += 1
        res = (c.get("outcome") or {}).get("result")
        if res:
            r["judged"] += 1
            r[res] += 1
    for r in out.values():
        r["hit_rate"] = round(r["helped"] / r["judged"], 3) if r["judged"] else None
    return out


def disagreements(items: list[dict] | None = None, *, path: Path | None = None) -> list[dict]:
    """Sessions where two minds answered the same role and the outcomes split —
    the cases that teach whom to trust."""
    items = load(path) if items is None else items
    groups: dict[tuple, list[dict]] = {}
    for c in items:
        if c.get("session") and not c.get("shadow_of"):
            groups.setdefault((c["session"], c["role"]), []).append(c)
    out = []
    for (session, role), cs in groups.items():
        results = {c["mind"]: (c.get("outcome") or {}).get("result") for c in cs}
        judged = {m: r for m, r in results.items() if r}
        if len(judged) >= 2 and len(set(judged.values())) > 1:
            out.append({"session": session, "role": role, "case": cs[0].get("case", ""), "results": judged})
    return out


def readiness(items: list[dict] | None = None, *, path: Path | None = None) -> dict:
    """How often Meedo-Me's own answer, given in shadow, agreed with the
    answer that proved right. Per role; the hand-off is earned role by role."""
    items = load(path) if items is None else items
    by_id = {c["id"]: c for c in items}
    out: dict[str, dict] = {}
    for c in items:
        if c.get("mind") != SHADOW or not c.get("shadow_of"):
            continue
        ref = by_id.get(c["shadow_of"])
        if not ref or (ref.get("outcome") or {}).get("result") not in ("helped", "wrong"):
            continue
        r = out.setdefault(c["role"], {"compared": 0, "agreed": 0})
        r["compared"] += 1
        right = ref["outcome"]["result"] == "helped"
        same = _same_call(c.get("parsed") or {}, ref.get("parsed") or {})
        if same == right:
            r["agreed"] += 1
    for r in out.values():
        r["rate"] = round(r["agreed"] / r["compared"], 3) if r["compared"] else None
    return out


def _same_call(a: dict, b: dict) -> bool:
    """Whether two answers made the same call: the same verdict, or the same
    next action. Wording is not compared — only the decision."""
    for k in ("verdict", "same_logo", "action"):
        if k in a or k in b:
            return a.get(k) == b.get(k)
    return False


def unjudged(items: list[dict] | None = None, *, path: Path | None = None) -> list[dict]:
    items = load(path) if items is None else items
    return [c for c in items if not c.get("outcome") and not c.get("error") and not c.get("shadow_of")]


def export(dest: Path, *, only_helped: bool = False, path: Path | None = None) -> int:
    """Judged consultations as JSONL: the dataset a local model learns from."""
    rows = 0
    with Path(dest).open("w", encoding="utf-8") as f:
        for c in load(path):
            res = (c.get("outcome") or {}).get("result")
            if not res or c.get("shadow_of") or (only_helped and res != "helped"):
                continue
            f.write(json.dumps({"role": c["role"], "case": c["case"], "images": c["images"],
                                "question": c["question"], "answer": c["answer"], "parsed": c["parsed"],
                                "mind": c["mind"], "model": c["model"], "outcome": res}) + "\n")
            rows += 1
    return rows


def ingest(src: Path, *, source: str = "app", path: Path | None = None) -> int:
    """Bring in consultations another part of the project wrote as JSONL (one
    record per line, the same fields `record` takes). Already-present ids are
    skipped, so ingesting the same file twice adds nothing."""
    items = load(path)
    have = {c["id"] for c in items}
    added = 0
    for line in Path(src).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            c = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not {"mind", "role", "question", "answer"} <= c.keys():
            continue
        c.setdefault("ts", _now())
        c.setdefault("id", f"{c['ts']}-{c['mind']}-{c['role']}")
        if c["id"] in have:
            continue
        c.setdefault("source", source)
        c.setdefault("model", "")
        c.setdefault("case", "")
        c.setdefault("session", "")
        c.setdefault("images", [])
        c.setdefault("parsed", {})
        c.setdefault("outcome", None)
        c["question"], c["answer"] = c["question"][:MAX_TEXT], c["answer"][:MAX_TEXT]
        items.append(c)
        have.add(c["id"])
        added += 1
    _save(items, path)
    return added


def report(path: Path | None = None) -> dict:
    items = load(path)
    return {
        "consultations": len(items),
        "track_record": track_record(items),
        "disagreements": disagreements(items)[-10:],
        "readiness": readiness(items),
        "awaiting_judgement": len(unjudged(items)),
    }


def standup_lines(path: Path | None = None) -> list[str]:
    items = load(path)
    if not items:
        return []
    lines = ["Minds (Gemini, Claude) — what their answers proved to be:"]
    for key, r in sorted(track_record(items).items()):
        rate = f"{r['hit_rate']:.0%}" if r["hit_rate"] is not None else "unjudged"
        lines.append(f"  {key}: asked {r['asked']}, judged {r['judged']}, helped {r['helped']} ({rate}), "
                     f"wrong {r['wrong']}, failed {r['failed']}")
    waiting = unjudged(items)
    if waiting:
        lines.append(f"  {len(waiting)} answer(s) await judgement — judge with meedo_consult judge <id> …")
    for role, r in sorted(readiness(items).items()):
        lines.append(f"  Meedo-Me in shadow, {role}: agreed with the right answer {r['agreed']}/{r['compared']}")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("report")
    j = sub.add_parser("judge")
    j.add_argument("id")
    j.add_argument("result", choices=RESULTS)
    j.add_argument("detail", nargs="?", default="")
    j.add_argument("--by", default="")
    j.add_argument("--lesson", default="")
    e = sub.add_parser("export")
    e.add_argument("dest", type=Path)
    e.add_argument("--only-helped", action="store_true")
    i = sub.add_parser("ingest")
    i.add_argument("src", type=Path)
    i.add_argument("--source", default="app")
    a = ap.parse_args(argv)
    if a.command == "report":
        print(json.dumps(report(), indent=2))
    elif a.command == "judge":
        c = judge(a.id, a.result, a.detail, by=a.by, lesson=a.lesson)
        print(f"judged {c['id']}: {a.result}" + (f" -> episode {c['lesson']}" if c.get("lesson") else ""))
    elif a.command == "export":
        print(f"{export(a.dest, only_helped=a.only_helped)} rows -> {a.dest}")
    else:
        print(f"ingested {ingest(a.src, source=a.source)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
