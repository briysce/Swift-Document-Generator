"""The minds' drawings are anchored, sandboxed, and only trusted when they agree with themselves."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
from PIL import Image

from tools.logo_vectorizer.ai_advisors import minds as M

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("logo_minds", ROOT / "scripts" / "logo_minds.py")
LM = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(LM)


def _logo(w, h, box, colour, page=(255, 255, 255)):
    a = np.zeros((h, w, 4), np.uint8)
    a[..., :3] = page
    a[..., 3] = 255
    x0, y0, x1, y1 = box
    a[y0:y1, x0:x1, :3] = colour
    return Image.fromarray(a, "RGBA")


def test_a_repaint_goes_back_on_the_sketchs_frame_and_inks():
    sketch = _logo(200, 60, (20, 10, 180, 50), (206, 78, 48))
    # The image model letterboxed it and darkened the orange.
    repaint = _logo(400, 400, (40, 150, 360, 230), (170, 60, 30))
    out = np.asarray(LM.anchor_redraw(repaint, sketch, scale=2.0))
    assert out.shape[:2] == (120, 400)
    ink = (out[..., :3] != 255).any(axis=2)
    ys, xs = np.nonzero(ink)
    assert (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1) == (40, 20, 360, 100)
    colours = {tuple(int(v) for v in c) for c in out[ink][:, :3]}
    got = [int(v) for v in colours.pop()] if len(colours) == 1 else None
    assert got is not None and max(abs(a - b) for a, b in zip(got, (206, 78, 48))) <= 8


def test_an_svg_that_reaches_outside_itself_is_not_rendered():
    for bad in ('<svg xmlns="http://www.w3.org/2000/svg"><image href="https://x/y.png"/></svg>',
                '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
                '<svg xmlns="http://www.w3.org/2000/svg"><use href="file:///etc/passwd"/></svg>'):
        assert LM.render_svg(bad, (10, 10)) is None
    ok = LM.render_svg('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="5" height="5"/></svg>',
                       (10, 10))
    assert ok is not None and ok.size == (10, 10)


def test_a_pick_counts_only_when_both_orders_agree(monkeypatch):
    img = Image.new("RGBA", (4, 4))
    replies = iter([{"better": "A"}, {"better": "B"}, {"better": "A"}, {"better": "A"}])

    def fake(mind, sketch, a, b, **kw):
        return M.Answer(mind=mind, model="m", role="compare", parsed=next(replies))

    monkeypatch.setattr(M, "compare", fake)
    assert M.compare_both_ways("claude", img, img, img)["pick"] == "first"   # A then (swapped) B = first both times
    assert M.compare_both_ways("claude", img, img, img)["pick"] == "split"   # A then A = position bias


def test_an_answer_that_comes_back_as_a_list_is_read_as_its_object():
    assert M._as_object([{"better": "B"}]) == {"better": "B"}
    assert M._as_object([1, 2]) == {"items": [1, 2]}
    assert M._as_object("x") == {} and M._as_object({"better": "A"}) == {"better": "A"}
