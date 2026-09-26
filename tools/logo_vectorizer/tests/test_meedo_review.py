"""Meedo-Me's reviewer: strict about deletion, lenient about restoration.

Every case here is something the reviewer has actually faced — the deletion it
exists to catch, and the two false alarms its first version raised.
"""

from __future__ import annotations

import numpy as np
import pytest
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


NAVY = (20, 40, 90)
WORD = [((10, 30, 100, 55), NAVY), ((120, 30, 150, 55), NAVY)]
DOTS = [((40, 18, 46, 24), NAVY), ((70, 18, 76, 24), NAVY)]


def _scaled(fills, k, page):
    return _logo([((x0 * k, y0 * k, x1 * k, y1 * k), c) for (x0, y0, x1, y1), c in fills],
                 size=(160 * k, 80 * k), page=page)


@pytest.mark.parametrize("page", [None, (255, 255, 255)])
@pytest.mark.parametrize("k", [1, 3, 8])
def test_dropped_dots_are_blocked_on_any_background_at_any_size(page, k):
    """GCM "Modification" i-dots: same navy as the word, so colour share
    barely moves. Opaque pages matter: real sketches are mostly opaque, and the
    first version of this check found no dots at all on them."""
    sketch = _scaled(WORD + DOTS, k, page)
    assert review(sketch, sketch).passed
    rv = review(_scaled(WORD, k, page), sketch)
    assert not rv.passed
    assert [f.check for f in rv.findings] == ["small_element"]


def test_specks_elsewhere_do_not_stand_in_for_a_lost_dot():
    """ESRGAN on GCM lost both dots but scattered specks round its letters;
    counting components let the specks pass for the dots."""
    specks = [((x, 60, x + 3, 63), NAVY) for x in (12, 30, 60, 90, 125)]
    rv = review(_logo(WORD + specks, size=(160, 80)), _logo(WORD + DOTS, size=(160, 80)))
    assert not rv.passed


def test_removing_faint_fringe_is_not_deletion():
    """Trialta has no dots; noise crumbs counted as dots blocked eight correct
    outputs. Fringe is faint and hugs the stroke it bled from."""
    haze = (150, 160, 185)
    crumbs = [((x, 27, x + 3, 30), haze) for x in (15, 40, 65, 90)] + [((101, 40, 104, 43), NAVY)]
    sketch = _logo(WORD + crumbs, size=(160, 80), page=(255, 255, 255))
    assert review(_logo(WORD, size=(160, 80), page=(255, 255, 255)), sketch).passed


def test_verdicts_are_remembered(tmp_path):
    ledger = tmp_path / "ledger.json"
    record(review(SKETCH, SKETCH), case="c1", candidate="traced", path=ledger)
    record(review(np.zeros_like(SKETCH), SKETCH), case="c1", candidate="idealize", path=ledger)
    got = catches(ledger)
    assert got["reviewed"] == 2
    assert got["blocked"] == 1
    assert got["by_check"].get("collapse", 0) >= 1


def test_a_false_alarm_is_withdrawn_not_erased(tmp_path):
    from tools.logo_vectorizer.meedo_ledger import load
    from tools.logo_vectorizer.meedo_review import retract

    ledger = tmp_path / "ledger.json"
    rv = review(_logo(WORD, size=(160, 80)), _logo(WORD + DOTS, size=(160, 80)))
    record(rv, case="trialta", candidate="traced", path=ledger)
    record(review(np.zeros_like(SKETCH), SKETCH), case="c1", candidate="idealize", path=ledger)
    assert retract("small_element", "trialta has no dots", path=ledger) == 1
    got = catches(ledger)
    assert got["blocked"] == 1 and got["retracted"] == 1 and "small_element" not in got["by_check"]
    assert len(load(ledger)["reviews"]) == 2


def test_a_shared_loss_does_not_choose_between_candidates():
    from tools.logo_vectorizer.meedo_review import lost_no_more

    sketch = _logo(WORD + DOTS, size=(160, 80))
    no_dots = review(_logo(WORD, size=(160, 80)), sketch)
    one_dot = review(_logo(WORD + DOTS[:1], size=(160, 80)), sketch)
    no_word = review(_logo(DOTS, size=(160, 80)), sketch)
    assert lost_no_more(no_dots, no_dots)
    assert lost_no_more(one_dot, no_dots) and not lost_no_more(no_dots, one_dot)
    assert not lost_no_more(no_word, no_dots)



def test_dots_are_found_on_an_output_cropped_to_its_ink():
    """Preparation crops to the ink, so outputs are not on the sketch's canvas.
    Stretching them onto it moved GCM's third dot far enough to misjudge it."""
    page = (255, 255, 255)
    sketch = _logo(WORD + DOTS, size=(160, 80), page=page)
    cropped = sketch[18:55, 10:150]                  # the output frame: ink only
    big = np.asarray(Image.fromarray(cropped).resize((140 * 6, 37 * 6), Image.Resampling.LANCZOS))
    assert review(big, sketch).passed
    no_dots = _logo(WORD, size=(160, 80), page=page)[18:55, 10:150]
    big = np.asarray(Image.fromarray(no_dots).resize((140 * 6, 37 * 6), Image.Resampling.LANCZOS))
    assert not review(big, sketch).passed
