"""Full-cycle standup returns ledger + journal + snapshot together."""

from __future__ import annotations

from tools.logo_vectorizer.meedo_cycle import collect, format_cycle, run


def test_cycle_collect_and_format():
    out = collect()
    assert "proposals_awaiting" in out and "journal" in out and "report" in out
    text = format_cycle(out)
    assert "Meedo-Me ledger standup" in text
    assert "Meedo-Me journal standup" in text
    assert "Refine Meedo-Me" in text


def test_cycle_cli_print(capsys):
    run(as_json=False)
    assert "Refine Meedo-Me" in capsys.readouterr().out
