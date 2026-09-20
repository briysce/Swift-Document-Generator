"""Two rules that decide whether an element gets named, and how.

Both are here because the engine got them wrong in ways that measured well.
"""

from __future__ import annotations

import numpy as np
import pytest

from tools.logo_vectorizer.shapes import ShapeFit, best_fit, rank


def _bar(width: int = 300, height: int = 40, pad: int = 6) -> np.ndarray:
    m = np.zeros((height + 2 * pad, width + 2 * pad), dtype=bool)
    m[pad : pad + height, pad : pad + width] = True
    return m


# --------------------------------------------------------------------------
# A few stray pixels must not resize the rectangle they sit on
# --------------------------------------------------------------------------


def test_clean_bar_is_named_a_rectangle():
    fit = best_fit(_bar())
    assert fit is not None
    assert fit.kind == "rect"
    assert fit.coverage > 0.99


def test_antialiasing_crumbs_do_not_inflate_the_rectangle():
    """The defect this guards: minAreaRect must *enclose* every pixel.

    On the solid Swift lockup the top bar was a clean 2980x89 slab carrying
    three stray pixels — one row above it and one row below — and bounding them
    stretched the rectangle down its whole length, costing 6,152 phantom pixels
    and dropping coverage from 0.9993 to 0.9776. That fell under the gate, so
    the bar was refused and traced as a curve instead of emitted as a rect.
    """
    clean = _bar()
    crumbed = clean.copy()
    pad = 6
    crumbed[pad - 1, 100] = True  # one row above
    crumbed[pad - 1, 101] = True
    crumbed[pad + 40, 200] = True  # one row below

    assert crumbed.sum() == clean.sum() + 3

    fit = best_fit(crumbed)
    assert fit is not None, "3 crumbs in 12,000 pixels cost the bar its name"
    assert fit.kind == "rect"
    assert fit.coverage > 0.99


def test_trim_does_not_shrink_an_honest_rectangle():
    """Trimming is validated by coverage, so a clean rect keeps its real size."""
    fit = best_fit(_bar(width=300, height=40))
    assert fit is not None
    # Emitted width/height must still describe a 300x40 bar, not a trimmed one.
    nums = [float(v) for v in fit.markup.replace('"', " ").split() if _isnum(v)]
    assert max(nums) == pytest.approx(300, abs=2)


def _isnum(v: str) -> bool:
    try:
        float(v)
    except ValueError:
        return False
    return True


# --------------------------------------------------------------------------
# "Prefer the more constrained shape" only holds when both actually fit
# --------------------------------------------------------------------------


def test_marginally_better_fit_does_not_unseat_the_constrained_shape():
    """A polygon that shaves noise off a circle is noise deciding, not evidence."""
    won = rank(
        [
            ShapeFit("circle", "", 0.9698, 0.1),
            ShapeFit("polygon", "", 0.9715, 0.1),
        ]
    )
    assert won.kind == "circle"


def test_a_shape_that_does_not_explain_the_element_loses_its_privilege():
    """The real GCM element: a rect covering 0.80 outranked a polygon at 0.97.

    Clearing the coverage floor is not the same as fitting — the floor is a
    minimum and relaxes to 0.80 on small elements. Ranking on degrees of
    freedom alone therefore asserted a rectangle where there plainly was none.
    """
    won = rank(
        [
            ShapeFit("rect", "", 0.8000, 0.1),
            ShapeFit("polygon", "", 0.9728, 0.1),
        ]
    )
    assert won.kind == "polygon"


def test_promotion_still_prefers_the_most_constrained_candidate():
    """Once a circle is unseated, an ellipse beats a polygon that ties it."""
    won = rank(
        [
            ShapeFit("circle", "", 0.80, 0.1),
            ShapeFit("ellipse", "", 0.98, 0.1),
            ShapeFit("polygon", "", 0.99, 0.1),
        ]
    )
    assert won.kind == "ellipse"


def test_a_perfect_constrained_fit_is_never_unseated():
    won = rank(
        [
            ShapeFit("circle", "", 1.0, 0.0),
            ShapeFit("polygon", "", 1.0, 0.0),
        ]
    )
    assert won.kind == "circle"
