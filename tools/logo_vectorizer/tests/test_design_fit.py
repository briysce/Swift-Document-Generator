"""Designed geometry: recover the shape that was drawn, not the pixels that show it.

Each case draws a known shape exactly, degrades it into a small blurry raster,
and asks the fitter for the shape back. It is judged against the true shape,
never against the degraded pixels.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image, ImageFilter

cairosvg = pytest.importorskip("cairosvg")
pytest.importorskip("skimage")

from tools.logo_vectorizer import design_fit as D  # noqa: E402


def _svg(body: str, w: int, h: int, ss: int) -> np.ndarray:
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{w * ss}" height="{h * ss}" viewBox="0 0 {w} {h}">{body}</svg>'
    return np.asarray(Image.open(io.BytesIO(cairosvg.svg2png(bytestring=svg.encode()))).convert("RGBA"))[..., 3]


def _sketch(body, w, h, blur=0.8):
    a = _svg(body, w, h, 8)
    small = np.asarray(Image.fromarray(a).resize((w, h), Image.Resampling.BOX))
    return np.asarray(Image.fromarray(small).filter(ImageFilter.GaussianBlur(blur))).astype(float) / 255


def _fit(cov, **kw):
    loops = [D.fit_loop(c, tol=0.3) for c in D.contours(cov)]
    D.snap_lines(loops, **kw)
    return loops


def _iou(loops, body, w, h):
    path = "".join(D.loop_path(lp) for lp in loops)
    got = _svg(f'<path d="{path}" fill="black"/>', w, h, 8) > 127
    want = _svg(body, w, h, 8) > 127
    return (got & want).sum() / (got | want).sum()


def test_an_italic_stem_comes_back_as_four_straight_lines():
    body = '<polygon points="14,34 22,6 30,6 22,34" fill="black"/>'
    loops = _fit(_sketch(body, 44, 40))
    assert [s.is_line for lp in loops for s in lp.segs] == [True] * 4
    assert _iou(loops, body, 44, 40) > 0.995


def test_a_wedge_serif_keeps_its_points_sharp():
    body = '<polygon points="6,30 34,30 20,8" fill="black"/>'
    loops = _fit(_sketch(body, 40, 40))
    corners = sorted(tuple(np.round(s.start, 1)) for lp in loops for s in lp.segs)
    assert len(corners) == 3
    for got, want in zip(corners, sorted([(6, 30), (34, 30), (20, 8)])):
        assert abs(got[0] - want[0]) < 0.25 and abs(got[1] - want[1]) < 0.25


def test_a_blurred_circle_is_a_few_smooth_curves_not_a_staircase():
    body = '<circle cx="20" cy="20" r="11" fill="black"/>'
    loops = _fit(_sketch(body, 40, 40))
    assert D.anchors(loops) <= 8
    assert _iou(loops, body, 40, 40) > 0.99


def test_parallel_stems_share_one_slant_and_bases_share_one_height():
    body = ('<polygon points="4,34 12,6 18,6 10,34" fill="black"/>'
            '<polygon points="24,34.4 32,6 38,6 30,34.4" fill="black"/>')
    loops = _fit(_sketch(body, 44, 40))
    slants = [D._angle(s) for lp in loops for s in lp.segs if s.is_line and not abs(s.pts[0, 1] - s.pts[1, 1]) < 1e-9]
    assert max(slants) - min(slants) < 1e-6
    bases = sorted({round(float(s.pts[0, 1]), 6) for lp in loops for s in lp.segs
                    if s.is_line and abs(s.pts[0, 1] - s.pts[1, 1]) < 1e-9 and s.pts[0, 1] > 30})
    assert len(bases) == 1


def test_coverage_unmixing_finds_the_edge_of_a_blend():
    rgb = np.zeros((1, 3, 3))
    rgb[0, 0] = (221, 77, 42)
    rgb[0, 1] = (0.4 * np.array((221, 77, 42)) + 0.6 * 255)
    rgb[0, 2] = (255, 255, 255)
    w = D.unmix(rgb, [(221, 77, 42), (34, 33, 40), (255, 255, 255)])
    assert np.allclose(w[0, :, 0], [1.0, 0.4, 0.0], atol=0.02)
