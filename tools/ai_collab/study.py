"""Meedo study analysis — what faces did that Meedo cannot yet own offline.

Surfaces for standup / cycle / report:
  * blind spots (silent or thin recording paths)
  * procedures still live-API-dependent
  * lessons with low offline application
  * journal dones without episodes
"""

from __future__ import annotations

from typing import Any


# Known faces Meedo should be studying. If a face acts without procedures /
# lessons tagged to it, the study report calls that out.
KNOWN_FACES = (
    "gemini",
    "claude",
    "serper",
    "cursor",
    "claude_code",
    "collab_mind",
    "ai_collab",
    "openclaw",
    "improve_loop",
)


def collect_study_report() -> dict[str, Any]:
    """Build the study/offline-handoff report (fail-open to empty sections)."""
    out: dict[str, Any] = {
        "faces_studied": {},
        "offline_ready": {},
        "cannot_yet_own": [],
        "done_without_episode": [],
        "blind_spots": [],
        "confidence": {},
    }

    # Procedures
    try:
        from tools.ai_collab.procedures import load as load_procs
        from tools.ai_collab.procedures import offline_ready_summary

        procs = [p for p in load_procs() if not p.retracted]
        summary = offline_ready_summary()
        out["offline_ready"] = {
            "procedures_total": summary.get("total", 0),
            "procedures_ready": summary.get("offline_ready", 0),
            "by_face": summary.get("by_face", {}),
            "top_ready": summary.get("top_ready", [])[:5],
        }
        still = summary.get("still_needs_api") or []
        for p in still[:10]:
            out["cannot_yet_own"].append(
                {
                    "kind": "procedure",
                    "id": p.get("id"),
                    "face": p.get("face"),
                    "domain": p.get("domain"),
                    "title": p.get("title"),
                    "confidence": p.get("confidence"),
                    "why": "procedure not yet offline_ready — still needs live face or more verified runs",
                }
            )
        face_counts = {f: 0 for f in KNOWN_FACES}
        for p in procs:
            face_counts[p.face] = face_counts.get(p.face, 0) + 1
        out["faces_studied"]["procedures"] = face_counts
    except Exception as exc:  # noqa: BLE001
        out["blind_spots"].append(f"procedures_load:{exc}")

    # AI lessons
    try:
        from tools.ai_collab.learn import load as load_lessons

        lessons = load_lessons()
        offline_n = sum(1 for L in lessons if L.times_applied_offline > 0)
        live_only = [
            L
            for L in lessons
            if L.times_applied_offline == 0 and set(L.providers) & {"gemini", "claude"}
        ]
        out["offline_ready"]["lessons_total"] = len(lessons)
        out["offline_ready"]["lessons_applied_offline"] = offline_n
        out["confidence"]["lesson_offline_rate"] = (
            round(offline_n / len(lessons), 3) if lessons else 0.0
        )
        for L in live_only[:8]:
            out["cannot_yet_own"].append(
                {
                    "kind": "lesson",
                    "id": L.id,
                    "domain": L.domain,
                    "problem": L.problem[:160],
                    "providers": L.providers,
                    "times_applied_offline": L.times_applied_offline,
                    "why": "Gemini/Claude lesson never applied offline yet",
                }
            )
        providers: dict[str, int] = {}
        for L in lessons:
            for p in L.providers or ["unknown"]:
                providers[p] = providers.get(p, 0) + 1
        out["faces_studied"]["lessons_by_provider"] = providers
    except Exception as exc:  # noqa: BLE001
        out["blind_spots"].append(f"lessons_load:{exc}")

    # Journal dones without matching episode / method prompt
    try:
        from tools.logo_vectorizer.meedo_journal import load as load_journal
        from tools.logo_vectorizer import meedo_episodes as E

        entries = load_journal().get("entries") or []
        episodes = E.load()
        ep_problems = " ".join(
            (e.get("problem") or "") + " " + (e.get("method") or "") for e in episodes
        ).lower()
        for e in entries:
            if e.get("kind") != "done":
                continue
            summary = (e.get("summary") or "").lower()
            ev = e.get("evidence") or {}
            has_ep = bool(ev.get("episode") or ev.get("episode_id"))
            # Soft lexical overlap — not a hard join.
            overlap = any(
                tok in ep_problems
                for tok in summary.split()
                if len(tok) > 5
            ) if summary else False
            if not has_ep and not overlap:
                out["done_without_episode"].append(
                    {
                        "id": e.get("id"),
                        "agent": e.get("agent"),
                        "task": e.get("task"),
                        "summary": (e.get("summary") or "")[:160],
                        "prompt": (
                            "Record meedo_record_episode with method + outcome "
                            "so Meedo studies this unit."
                        ),
                    }
                )
        out["done_without_episode"] = out["done_without_episode"][-12:]
    except Exception as exc:  # noqa: BLE001
        out["blind_spots"].append(f"journal_done:{exc}")

    # Structural blind spots — faces with zero procedures recorded
    studied = out.get("faces_studied", {}).get("procedures") or {}
    for face in KNOWN_FACES:
        if studied.get(face, 0) == 0:
            out["blind_spots"].append(
                f"no_procedures_for_face:{face} — wire observe() when that face acts"
            )

    # Confidence rollup
    ready = out.get("offline_ready") or {}
    proc_total = int(ready.get("procedures_total") or 0)
    proc_ready = int(ready.get("procedures_ready") or 0)
    out["confidence"]["procedure_offline_rate"] = (
        round(proc_ready / proc_total, 3) if proc_total else 0.0
    )
    out["confidence"]["handoff_growing"] = proc_ready > 0 or int(
        ready.get("lessons_applied_offline") or 0
    ) > 0
    out["confidence"]["summary"] = (
        f"{proc_ready}/{proc_total} procedures offline-ready; "
        f"{ready.get('lessons_applied_offline', 0)}/"
        f"{ready.get('lessons_total', 0)} lessons applied offline; "
        f"{len(out['cannot_yet_own'])} items Meedo cannot yet own; "
        f"{len(out['done_without_episode'])} dones lacking episodes."
    )
    return out


def format_study_report(report: dict[str, Any] | None = None) -> str:
    r = report or collect_study_report()
    lines = ["Meedo study — what faces did that Meedo cannot yet own"]
    conf = r.get("confidence") or {}
    if conf.get("summary"):
        lines.append(f"  {conf['summary']}")
    cannot = r.get("cannot_yet_own") or []
    if cannot:
        lines.append(f"  still live-dependent ({len(cannot)}):")
        for item in cannot[:6]:
            label = item.get("title") or item.get("problem") or item.get("id")
            lines.append(
                f"    [{item.get('kind')}] {item.get('face') or item.get('domain')}: "
                f"{str(label)[:120]}"
            )
    dones = r.get("done_without_episode") or []
    if dones:
        lines.append(f"  journal dones without episodes ({len(dones)}):")
        for d in dones[:5]:
            lines.append(
                f"    {d.get('id')} #{d.get('task')} {d.get('agent')}: "
                f"{str(d.get('summary') or '')[:100]}"
            )
    blinds = r.get("blind_spots") or []
    # Prefer structural face gaps over load errors in the short view.
    face_blinds = [b for b in blinds if b.startswith("no_procedures_for_face:")]
    if face_blinds:
        lines.append(f"  blind faces (no procedures yet): {len(face_blinds)}")
        for b in face_blinds[:6]:
            lines.append(f"    {b.split(' — ')[0]}")
    ready = r.get("offline_ready") or {}
    top = ready.get("top_ready") or []
    if top:
        lines.append("  offline-ready samples:")
        for p in top[:3]:
            lines.append(
                f"    {p.get('id')} [{p.get('face')}] conf={p.get('confidence')} "
                f"{str(p.get('title') or '')[:100]}"
            )
    return "\n".join(lines)
