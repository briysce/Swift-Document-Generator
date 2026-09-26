"""One command for the start (and end) of every agent work cycle.

Both Claude Code and Cursor must keep Meedo-Me in the loop. Running three
separate standups is easy to skip; this prints ledger proposals, journal
assessment, and a short knowledge snapshot together so the next unit starts
from what Meedo-Me already knows.
"""

from __future__ import annotations

import argparse
import json
import sys


def collect() -> dict:
    from . import meedo_episodes as E
    from . import meedo_journal as J
    from . import meedo_ledger as L

    waiting = L.standup()
    journal = J.standup()
    report = L.knowledge_report()
    report["workstreams"] = E.workstreams()
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
    lines.append(
        "\nRefine Meedo-Me when anything here is wrong, thin, or silent — "
        "journal friction, bad recall, false review, stale advice. Both agents own that."
    )
    return "\n".join(lines)


def run(*, as_json: bool = False) -> dict:
    out = collect()
    if as_json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(format_cycle(out))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Meedo-Me full cycle standup")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    run(as_json=a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
