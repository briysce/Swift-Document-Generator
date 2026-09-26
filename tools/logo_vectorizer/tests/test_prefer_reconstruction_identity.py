"""Identity-first beat has_vector when ideal lost strictly less (board #8)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from tools.logo_vectorizer.meedo_review import Finding, Review, lost_no_more  # noqa: E402


def _cand(name: str, *, has_vector: bool, review: Review | None, agreement: float = 0.5):
    return SimpleNamespace(
        name=name,
        svg=Path("/tmp/x.svg") if has_vector else None,
        agreement=agreement,
        ideality=0.8,
        review=review,
        passed_review=review is None or review.passed,
        has_vector=has_vector,
        finished=np.zeros((8, 8, 4), np.uint8),
    )


def _pick(ideal, traced):
    """Mirror convert()'s identity-first choice (keep in sync with logo_vectorize)."""
    from logo_vectorize import _lost_no_more, _prefer_reconstruction

    winner = traced
    if ideal is not None:
        ideal_less = _lost_no_more(ideal, traced) and not _lost_no_more(traced, ideal)
        if ideal.passed_review and not traced.passed_review:
            winner = ideal
        elif not ideal.passed_review and not traced.passed_review and ideal_less:
            winner = ideal
        elif ideal.passed_review and _prefer_reconstruction(ideal, traced):
            winner = ideal
        elif (
            not ideal.passed_review
            and not traced.passed_review
            and _lost_no_more(ideal, traced)
            and _prefer_reconstruction(ideal, traced)
        ):
            winner = ideal
    return winner.name


def test_lost_no_more_asymmetric_on_element_counts():
    both = Review(findings=[Finding("element", "block", "gone", n=3)])
    fewer = Review(findings=[Finding("element", "block", "gone", n=1)])
    assert lost_no_more(fewer, both)
    assert not lost_no_more(both, fewer)


def test_identity_ships_ideal_when_trace_lost_more_even_with_vector():
    """Wrong-red vector must not beat a tagline-retaining reconstruction."""
    traced_rv = Review(
        findings=[Finding("element", "block", "tagline gone", n=8)]
    )
    ideal_rv = Review(
        findings=[Finding("element", "block", "tagline gone", n=1)]
    )
    traced = _cand("traced", has_vector=True, review=traced_rv, agreement=0.9)
    ideal = _cand("idealize/prepared", has_vector=True, review=ideal_rv, agreement=0.4)
    assert _pick(ideal, traced) == "idealize/prepared"


def test_shared_loss_keeps_trace_when_it_has_vector():
    """GCM-style shared block: derived rule still prefers vectorized trace."""
    shared = Review(findings=[Finding("small_element", "block", "dots", n=3)])
    traced = _cand("traced", has_vector=True, review=shared, agreement=0.8)
    ideal = _cand("idealize/prepared", has_vector=True, review=shared, agreement=0.5)
    assert _pick(ideal, traced) == "traced"
