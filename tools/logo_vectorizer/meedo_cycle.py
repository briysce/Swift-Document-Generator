"""One command for the start (and end) of every agent work cycle.

Both Claude Code and Cursor must keep Meedo-Me in the loop. Running three
separate standups is easy to skip; this prints ledger proposals, journal
assessment, and a short knowledge snapshot together so the next unit starts
from what Meedo-Me already knows.

Optional Gemini↔Claude enrichment (``AI_COLLAB`` / keys in gitignored ``.env``)
adds recall-first advice on waiting proposals so Meedo can learn and eventually
own those decisions offline. Always fail-open.
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def _ai_enrich_proposals(waiting: list[dict]) -> list[dict]:
    """Attach Gemini+Claude (or recalled) advice to open proposals. Fail-open."""
    if not waiting:
        return []
    flag = os.environ.get("MEEDO_CYCLE_AI", "").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return []
    try:
        from tools.ai_collab.advise import advise
    except Exception as exc:  # noqa: BLE001
        print(f"[meedo_cycle] ai_collab unavailable: {exc}", file=sys.stderr)
        return []

    out: list[dict] = []
    # Cap live calls — prefer the highest-priority / escalated proposals.
    ranked = sorted(
        waiting,
        key=lambda p: (0 if p.get("escalated") else 1, int(p.get("priority") or 9)),
    )
    for prop in ranked[:2]:
        problem = f"{prop.get('headline', '')} — {prop.get('rationale', '')}".strip(" —")
        if not problem:
            continue
        try:
            advice = advise(
                domain="meedo_advisor",
                problem=problem,
                context={"proposal": prop},
                cases=[str(prop.get("case") or "")] if prop.get("case") else None,
                tags=["meedo_cycle", "board3"],
                journal=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[meedo_cycle] advise skipped: {exc}", file=sys.stderr)
            continue
        if advice is None:
            continue
        out.append(
            {
                "proposal_id": prop.get("id"),
                "case": prop.get("case"),
                **advice.to_dict(),
            }
        )
    return out


def collect(*, with_ai: bool | None = None) -> dict:
    from . import meedo_episodes as E
    from . import meedo_journal as J
    from . import meedo_ledger as L

    waiting = L.standup()
    journal = J.standup()
    report = L.knowledge_report()
    report["workstreams"] = E.workstreams()
    ai_advice: list[dict] = []
    want_ai = with_ai if with_ai is not None else True
    if want_ai:
        ai_advice = _ai_enrich_proposals(waiting)
    return {
        "proposals_awaiting": waiting,
        "journal": journal,
        "report": {
            "observations": report.get("observations"),
            "cases_tracked": report.get("cases_tracked"),
            "trend": report.get("trend"),
            "advice": report.get("advice"),
            "workstreams": report.get("workstreams"),
        },
        "ai_collab": ai_advice,
    }


def format_cycle(out: dict) -> str:
    from . import meedo_episodes as E
    from . import meedo_journal as J

    lines: list[str] = []
    waiting = out.get("proposals_awaiting") or []
    if not waiting:
        lines.append("Meedo-Me ledger standup: nothing awaiting a decision")
    else:
        lines.append(
            f"Meedo-Me ledger standup — {len(waiting)} proposal(s) awaiting a decision"
        )
        for q in waiting:
            flag = "ESCALATED " if q.get("escalated") else ""
            lines.append(
                f"  [{q['id']}] {flag}P{q.get('priority')} x{q.get('raised', 1)}  {q['headline']}"
            )
            lines.append(f"           {q.get('rationale', '')}")
            for ep in E.recall(
                q["headline"] + " " + q.get("rationale", ""),
                cases=[q.get("case", "")],
                top=1,
            ):
                if ep.get("method"):
                    lines.append(
                        f"           remembered {ep['id']} ({ep['outcome']}): "
                        f"{ep['method'][:180]}"
                    )
        lines.append(
            '\ndecide with: python -m tools.logo_vectorizer.meedo_ledger '
            'decide --by <you> <id> accept|reject "<reason>"'
        )

    lines.append("")
    lines.append(J.format_standup(out.get("journal") or {}))

    report = out.get("report") or {}
    t = report.get("trend") or {}
    lines.append("")
    lines.append(
        f"Meedo-Me snapshot — {report.get('observations', 0)} run(s), "
        f"{report.get('cases_tracked', 0)} case(s) tracked"
    )
    if t.get("samples", 0) >= 2:
        lines.append(
            f"  trend over {t['samples']} runs: "
            f"mean {t.get('mean_composite_first')} -> {t.get('mean_composite_last')}"
        )
    adv = report.get("advice") or {}
    if adv:
        lines.append(
            f"  advice judged: {adv.get('judged', 0)}  helped-rate {adv.get('helped_rate', 0)}  "
            f"(awaiting {adv.get('awaiting_decision', 0)}, in progress {adv.get('in_progress', 0)})"
        )
    ai = out.get("ai_collab") or []
    if ai:
        lines.append("")
        lines.append(f"Gemini↔Claude / Meedo recall — {len(ai)} advice item(s)")
        for a in ai:
            src = a.get("source") or "?"
            offline = " (offline)" if a.get("offline") else ""
            lines.append(
                f"  [{a.get('proposal_id')}] via {src}{offline}  P{a.get('priority', a.get('raw', {}).get('priority', '?'))}"
            )
            if a.get("diagnosis"):
                lines.append(f"           {a['diagnosis'][:200]}")
            if a.get("method"):
                lines.append(f"           method: {a['method'][:180]}")
            for step in (a.get("actions") or [])[:3]:
                lines.append(f"           - {step}")
    elif out.get("proposals_awaiting"):
        lines.append("")
        lines.append(
            "Gemini↔Claude: no advice this cycle (keys dark, disabled, or fail-open)."
        )

    lines.append(
        "\nRefine Meedo-Me when anything here is wrong, thin, or silent — "
        "journal friction, bad recall, false review, stale advice. Both agents own that."
    )
    return "\n".join(lines)


def run(*, as_json: bool = False, with_ai: bool | None = None) -> dict:
    out = collect(with_ai=with_ai)
    if as_json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(format_cycle(out))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Meedo-Me full cycle standup")
    p.add_argument("--json", action="store_true")
    p.add_argument(
        "--no-ai",
        action="store_true",
        help="Skip Gemini↔Claude enrichment (still prints ledger/journal)",
    )
    a = p.parse_args(argv)
    run(as_json=a.json, with_ai=False if a.no_ai else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
