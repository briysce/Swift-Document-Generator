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
