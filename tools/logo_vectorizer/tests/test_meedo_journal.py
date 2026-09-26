"""Work journal: units are logged, stale claims surface, thin dones are flagged."""

from __future__ import annotations

import time

from tools.logo_vectorizer import meedo_journal as J


def test_log_and_recent(tmp_path):
    path = tmp_path / "meedo_journal.json"
    e = J.log(
        agent="cursor",
        kind="claim",
        summary="hold #7",
        task=7,
        branch="cursor/logo-engine-collab-d4c9",
        path=path,
    )
    assert e["id"].startswith("J")
    J.log(agent="cursor", kind="finding", summary="stage trace", task=7, path=path)
    got = J.recent(agent="cursor", path=path, limit=5)
    assert [g["kind"] for g in got] == ["finding", "claim"]


def test_standup_flags_stale_claim_and_thin_done(tmp_path):
    path = tmp_path / "meedo_journal.json"
    # Backdate a claim by writing then patching ts.
    c = J.log(agent="claude", kind="claim", summary="swift app", task=1, path=path)
    data = J.load(path)
    data["entries"][0]["ts"] = "2026-09-26T10:00:00Z"
    J.save(data, path)

    J.log(
        agent="cursor",
        kind="done",
        summary="docs only",
        task=7,
        path=path,
        # deliberately omit tests / images / measured
        commit="",
    )
    # Clear auto commit on the thin done
    data = J.load(path)
    for e in data["entries"]:
        if e["kind"] == "done":
            e["evidence"] = {}
    J.save(data, path)

    now = time.time()
    rep = J.standup(path=path, now=now)
    assert any(s["task"] == 1 and s["agent"] == "claude" for s in rep["stale_claims"])
    assert any(d["task"] == 7 and "tests" in d["missing"] for d in rep["done_missing_evidence"])
