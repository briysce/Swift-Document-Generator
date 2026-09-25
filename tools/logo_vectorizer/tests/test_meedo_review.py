"""Meedo-Me's reviewer: strict about deletion, lenient about restoration.

Every case here is something the reviewer has actually faced — the deletion it
exists to catch, and the two false alarms its first version raised.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from tools.logo_vectorizer.meedo_review import catches, record, review

BLUE = (20, 60, 120)
RED = (190, 40, 45)


def _logo(fills, size=(160, 80), page=None) -> np.ndarray:
    """Flat colour blocks on a transparent (or opaque `page`) canvas."""
    w, h = size
    a = np.zeros((h, w, 4), dtype=np.uint8)
    if page is not None:
        a[:, :, :3] = page
        a[:, :, 3] = 255
    for (x0, y0, x1, y1), colour in fills:
        a[y0:y1, x0:x1, :3] = colour
        a[y0:y1, x0:x1, 3] = 255
    return a


SKETCH = _logo([((10, 20, 70, 60), BLUE), ((100, 20, 140, 60), RED)])


def test_an_unchanged_logo_passes():
    assert review(SKETCH, SKETCH).passed


def test_a_deleted_brand_colour_is_blocked():
    """The GCM monogram: red was 11% of the sketch and 0% of the output."""
    no_red = _logo([((10, 20, 70, 60), BLUE)])
    rv = review(no_red, SKETCH)
    assert not rv.passed
    assert any(f.check == "brand_colour" for f in rv.findings)


def test_a_collapsed_output_is_blocked():
    rv = review(np.zeros_like(SKETCH), SKETCH)
    assert not rv.passed


def test_a_washed_colour_brought_back_saturated_passes():
    """Restoration is supposed to change colour; that is not deletion."""
    washed = _logo([((10, 20, 70, 60), BLUE), ((100, 20, 140, 60), (200, 150, 150))])
    assert review(SKETCH, washed).passed


def test_an_element_shrunk_by_losing_its_fringe_passes():
    thinner = _logo([((10, 20, 70, 60), BLUE), ((100, 20, 120, 60), RED)])
    assert review(thinner, SKETCH).passed


def test_a_larger_output_is_compared_at_the_sketch_size():
    big = np.asarray(Image.fromarray(SKETCH, "RGBA").resize((640, 320), Image.NEAREST))
    assert review(big, SKETCH).passed


def test_the_page_is_never_required():
    """A sketch flattened onto white; the output rightly leaves the page empty."""
    on_paper = _logo([((10, 20, 70, 60), BLUE), ((100, 20, 140, 60), RED)], page=(250, 250, 250))
    assert review(SKETCH, on_paper).passed


def test_near_black_with_noise_is_still_black():
    """False alarm #1: HSV called (2,0,0) a fully saturated red.

    A correct Swift restoration was blocked for "deleting" its black shadow.
    """
    orange = (200, 72, 48)
    sketch = _logo([((10, 20, 70, 60), orange), ((100, 20, 140, 60), (1, 1, 1))])
    out = _logo([((10, 20, 70, 60), orange), ((100, 20, 140, 60), (2, 0, 0))])
    assert review(out, sketch).passed


def test_a_crushed_dark_colour_restored_to_its_hue_passes():
    """False alarm #2: GCM's navy text crushed to (0,1,12) read as black.

    The restoration brought it back blue, which is right, and was blocked.
    """
    sketch = _logo([((10, 20, 70, 60), (0, 1, 12)), ((100, 20, 140, 60), RED)])
    out = _logo([((10, 20, 70, 60), (16, 63, 116)), ((100, 20, 140, 60), RED)])
    assert review(out, sketch).passed


def test_true_black_is_not_satisfied_by_a_colour():
    """The leniency above must not let a real black element be replaced."""
    orange = (200, 72, 48)
    sketch = _logo([((10, 20, 70, 60), orange), ((100, 20, 140, 60), (0, 0, 0))])
    out = _logo([((10, 20, 70, 60), orange), ((100, 20, 140, 60), orange)])
    assert not review(out, sketch).passed


def test_a_second_sketch_can_supply_what_the_first_lost():
    """arc__blur_crush: the prepared sketch had lost the teal text entirely."""
    damaged = _logo([((100, 20, 140, 60), RED)])
    raw = SKETCH
    no_blue = _logo([((100, 20, 140, 60), RED)])
    assert review(no_blue, damaged).passed
    assert not review(no_blue, damaged, raw).passed


def test_verdicts_are_remembered(tmp_path):
    ledger = tmp_path / "ledger.json"
    record(review(SKETCH, SKETCH), case="c1", candidate="traced", path=ledger)
    record(review(np.zeros_like(SKETCH), SKETCH), case="c1", candidate="idealize", path=ledger)
    got = catches(ledger)
    assert got["reviewed"] == 2
    assert got["blocked"] == 1
    assert got["by_check"].get("collapse", 0) >= 1
