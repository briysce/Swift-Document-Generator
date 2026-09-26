"""Hook improve-loop summaries into Gemini↔Claude → Meedo learning.

Call after writing ``improve_summary_latest.json``. Fail-open always.
Also records procedure playbook entries so Meedo studies the loop method.
"""

from __future__ import annotations

import os
import sys
from typing import Any


def enrich_summary(
    summary: dict[str, Any],
    *,
    domain: str,
    max_items: int = 2,
) -> dict[str, Any]:
    """Attach ``ai_collab`` advice for top_failures; mutate + return summary."""
    flag = os.environ.get("IMPROVE_AI_COLLAB", "").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        summary.setdefault("ai_collab", [])
        _record_loop_procedure(summary, domain=domain, advice=[])
        return summary
    top = summary.get("top_failures") or summary.get("gate_fails") or []
    if not top:
        summary.setdefault("ai_collab", [])
        _record_loop_procedure(summary, domain=domain, advice=[])
        return summary
    try:
        from tools.ai_collab.advisor import advise_top_failures
    except Exception as exc:  # noqa: BLE001
        print(f"[improve_ai] unavailable: {exc}", file=sys.stderr)
        summary["ai_collab"] = []
        _record_loop_procedure(summary, domain=domain, advice=[])
        return summary

    try:
        advice = advise_top_failures(
            domain=domain,
            top_failures=list(top),
            extra_context={
                "mean_composite": summary.get("mean_composite"),
                "run_id": summary.get("run_id"),
                "approved_lock_reminder": summary.get("approved_lock_reminder"),
                "do_not_touch_shipping_lock": summary.get("do_not_touch_shipping_lock"),
            },
            max_items=max_items,
            journal=True,
        )
        summary["ai_collab"] = [a.to_dict() for a in advice]
        if advice:
            print(
                f"[improve_ai] {domain}: {len(advice)} advice item(s) "
                f"(sources={[a.source for a in advice]})",
                file=sys.stderr,
            )
            # Persist fine-grained procedures from each advice item.
            try:
                from tools.ai_collab.observe import observe_deliberation

                for a in advice:
                    observe_deliberation(
                        a,
                        face="improve_loop",
                        domain=domain,
                        problem=a.problem,
                        cases=None,
                        tags=[domain, "improve_loop"],
                        outcome="open",
                    )
            except Exception as exc:  # noqa: BLE001
                print(f"[improve_ai] observe skipped: {exc}", file=sys.stderr)
        _record_loop_procedure(summary, domain=domain, advice=advice)
    except Exception as exc:  # noqa: BLE001
        print(f"[improve_ai] skipped: {exc}", file=sys.stderr)
        summary["ai_collab"] = []
        _record_loop_procedure(summary, domain=domain, advice=[])
    return summary


def _record_loop_procedure(
    summary: dict[str, Any],
    *,
    domain: str,
    advice: list[Any],
) -> None:
    """Always capture the improve-loop itself as a Meedo procedure (fail-open)."""
    try:
        from tools.ai_collab.observe import observe

        top = summary.get("top_failures") or []
        cases = [
            str(r.get("case_id") or r.get("case") or "")
            for r in top[:5]
            if r.get("case_id") or r.get("case")
        ]
        steps = [
            "Read improve_summary_latest.json + training_lessons.json",
            "Fix real top_failures / gate_fails only",
            "Re-run domain improve loop; promote only score-proven changes",
            "Append training_lessons; expand cases for new failure modes",
        ]
        if domain == "shipping_pdf":
            steps.append(
                "Never change afterPillGap=11.0 / showRule=false / contactLabelToValue=3.0"
            )
        dnr = [
            "do not loosen harness gates to hide regressions",
            "do not invent brand colors from gray",
        ]
        lock = summary.get("do_not_touch_shipping_lock") or {}
        if lock:
            dnr.append(
                f"Shipping lock: afterPillGap={lock.get('after_pill_gap', 11.0)} "
                f"showRule={lock.get('so_show_rule', False)} "
                f"contact={lock.get('contact_label_to_value', 3.0)}"
            )
        methods = [getattr(a, "method", None) or (a.get("method") if isinstance(a, dict) else "") for a in advice]
        methods = [m for m in methods if m]
        observe(
            face="improve_loop",
            domain=domain,
            tried=(
                f"{domain} improve loop mean={summary.get('mean_composite')} "
                f"top_failures={len(top)}"
            ),
            evidence=f"run_id={summary.get('run_id')} gates={summary.get('gate_fails')}",
            method=methods[0] if methods else "score→fix→re-score→lesson→expand cases",
            outcome="partial" if top else "success",
            steps=steps,
            knobs={
                "mean_composite": summary.get("mean_composite"),
                "n_scored": summary.get("n_scored"),
                "max_advice_items": len(advice),
            },
            do_not_regress=dnr,
            cases=cases,
            tags=[domain, "improve_loop", "curriculum"],
            task=3,
            journal=False,  # advice path already journals when live
            lesson=bool(methods),
            episode=False,
            procedure=True,
            source="improve_hook",
            raw={"advice_sources": [getattr(a, "source", "") for a in advice]},
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[improve_ai] procedure record skipped: {exc}", file=sys.stderr)
