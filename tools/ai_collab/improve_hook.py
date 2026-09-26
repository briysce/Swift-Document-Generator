"""Hook improve-loop summaries into Gemini↔Claude → Meedo learning.

Call after writing ``improve_summary_latest.json``. Fail-open always.
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
        return summary
    top = summary.get("top_failures") or summary.get("gate_fails") or []
    if not top:
        summary.setdefault("ai_collab", [])
        return summary
    try:
        from tools.ai_collab.advisor import advise_top_failures
    except Exception as exc:  # noqa: BLE001
        print(f"[improve_ai] unavailable: {exc}", file=sys.stderr)
        summary["ai_collab"] = []
        return summary

    try:
        advice = advise_top_failures(
            domain=domain,
            top_failures=list(top),
            extra_context={
                "mean_composite": summary.get("mean_composite"),
                "run_id": summary.get("run_id"),
                "approved_lock_reminder": summary.get("approved_lock_reminder"),
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
    except Exception as exc:  # noqa: BLE001
        print(f"[improve_ai] skipped: {exc}", file=sys.stderr)
        summary["ai_collab"] = []
    return summary
