"""Idealize must not hard-fail when potrace is missing — smooth_fit can draw."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.logo_vectorizer import idealize as iz  # noqa: E402


def _tiny_two_blob() -> np.ndarray:
    """Two solid rectangles on transparent canvas — enough for elements_of."""
    arr = np.zeros((40, 80, 4), dtype=np.uint8)
    arr[8:32, 8:28] = (200, 40, 40, 255)
    arr[8:32, 50:72] = (30, 60, 90, 255)
    return arr


def test_compose_fail_open_without_potrace(monkeypatch):
    monkeypatch.setattr(iz, "have_potrace", lambda: False)
    arr = _tiny_two_blob()
    els = iz.elements_of(arr)
    assert len(els) >= 2
    # Plain (potrace-only) path must decline.
    assert iz._compose(arr, smooth_fit=False, els=els, glyphs={}, adapt_to_damage=False) is None
    # Smooth fit proceeds without potrace.
    svg = iz._compose(
        arr,
        smooth_fit=True,
        els=els,
        glyphs={},
        adapt_to_damage=False,
        snap_primitives=False,
    )
    assert svg is not None
    assert "<svg" in svg
    assert svg.count("<g ") >= 1


def test_idealize_layered_without_potrace_uses_smooth(monkeypatch):
    monkeypatch.setattr(iz, "have_potrace", lambda: False)
    arr = _tiny_two_blob()
    svg = iz.idealize_layered(arr, match_fonts=False, adapt_to_damage=False)
    assert svg is not None
    assert "<path" in svg or "polygon" in svg or "rect" in svg
