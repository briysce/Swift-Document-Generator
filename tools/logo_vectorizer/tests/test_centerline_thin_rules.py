"""Centre-line protect/stamp for thin rules inside dense lockups (board #9)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from logo_raster_finish import (  # noqa: E402
    centerline_protect_mask,
    elongated_thin_components,
    is_thin_stroke_mark,
    prepare_for_engine,
    stamp_centerline,
)
from tools.logo_vectorizer.shapes import best_fit, fit_centerline_stroke  # noqa: E402

PROPAK_CLEAN = ROOT / "qa_logos" / "synthetic" / "clean" / "propak.png"


@pytest.mark.skipif(not PROPAK_CLEAN.is_file(), reason="propak clean missing")
def test_propak_is_not_a_thin_wordmark_but_has_thin_rule_components():
    """Density gate must stay closed for Propak fills (lessons); rule still found."""
    arr = np.asarray(Image.open(PROPAK_CLEAN).convert("RGBA"))
    prep = prepare_for_engine(arr)
    assert not is_thin_stroke_mark(prep)
    rules = elongated_thin_components(prep)
    assert rules is not None and int(rules.sum()) >= 200
    protect = centerline_protect_mask(prep)
    assert protect is not None and protect.any()
    # Protect should sit on the red rule row (near bottom of prep), not flood fills.
    ys, xs = np.where(protect)
    assert ys.max() >= int(prep.shape[0] * 0.50)


def test_stamp_restores_dropped_thin_rule_centerline():
    """After a fake vectorize that erases a thin rule, stamp puts it back."""
    h, w = 60, 200
    prepared = np.zeros((h, w, 4), dtype=np.uint8)
    # Letter fill (dense) + thin red rule.
    prepared[10:40, 20:80, :] = (40, 100, 160, 255)
    prepared[48:51, 10:190, :] = (213, 56, 40, 255)
    assert elongated_thin_components(prepared) is not None

    restored = prepared.copy()
    restored[48:51, 10:190, 3] = 0  # vectorize dropped the rule
    out = stamp_centerline(restored, prepared)
    assert int((out[48:51, 10:190, 3] >= 200).sum()) >= 100
    # Letter fill unchanged.
    assert np.array_equal(out[10:40, 20:80, :3], prepared[10:40, 20:80, :3])


def test_one_pixel_border_is_named_a_centerline_stroke():
    """A 2 px × 40 rule that fails the rect area floor is still a stroke."""
    m = np.zeros((20, 60), dtype=bool)
    m[8:10, 5:50] = True  # 2×45, area 90 — thick<=3 path
    # Shrink to under 40 px to hit the small-element stroke path.
    m2 = np.zeros((20, 60), dtype=bool)
    m2[9:11, 10:28] = True  # 2×18 = 36 px
    fit = best_fit(m2)
    assert fit is not None
    assert fit.kind == "stroke"
    assert "stroke-width" in fit.markup


def test_fit_centerline_stroke_rejects_chunky_fills():
    m = np.zeros((40, 40), dtype=bool)
    m[5:35, 5:35] = True
    assert fit_centerline_stroke(m) is None
