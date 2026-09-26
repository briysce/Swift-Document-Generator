"""Colour-segregated font runs — Trialta gray TRI + green ALTA."""

from __future__ import annotations

from tools.logo_vectorizer.idealize import (
    _colours_same_family,
    _letterlike_element,
)


class _El:
    def __init__(self, colour, bbox):
        self.colour = colour
        self.bbox = bbox


def test_gray_and_green_are_different_run_families():
    assert not _colours_same_family((96, 96, 96), (0, 184, 64))


def test_near_black_scrap_does_not_join_gray_letters():
    """Trialta (96,96,96) vs (50,50,50) must not share a family."""
    assert not _colours_same_family((96, 96, 96), (50, 50, 50))


def test_hairline_noise_is_not_letterlike():
    assert not _letterlike_element(_El((96, 96, 96), (0, 0, 0, 40)))  # w=1
    assert not _letterlike_element(_El((96, 96, 96), (0, 0, 48, 1)))  # h=2
    assert _letterlike_element(_El((96, 96, 96), (0, 0, 31, 40)))


def test_a_line_takes_the_face_that_keeps_every_reading():
    """TRI+ALTA share a line: pick one face, never one that misreads a letter."""
    from pathlib import Path

    import numpy as np

    from tools.logo_vectorizer.glyph_match import RunMatch
    from tools.logo_vectorizer.idealize import _one_face_per_line

    oswald, sansita = Path("Oswald-700.ttf"), Path("Sansita-700.ttf")
    boxes = [(0, 0, 9, 19), (12, 0, 21, 19), (24, 0, 33, 19), (36, 0, 45, 19), (48, 0, 57, 19)]
    masks = [np.ones((20, 10), bool)] * 5
    cache = {
        (0, oswald): (0.95, "T"), (1, oswald): (0.91, "R"), (2, oswald): (0.84, "A"), (3, oswald): (0.83, "L"),
        (0, sansita): (0.90, "T"), (1, sansita): (0.93, "n"), (2, sansita): (0.90, "A"), (3, sansita): (0.90, "L"),
    }
    tri = RunMatch(oswald, ["T", "R"], [0, 1], 0.93)
    alta = RunMatch(sansita, ["A", "L"], [2, 3], 0.90)
    out = _one_face_per_line([tri, alta], masks, boxes, cache)
    assert {m.font for m in out} == {oswald}
    assert [c for m in out for c in m.chars] == ["T", "R", "A", "L"]
