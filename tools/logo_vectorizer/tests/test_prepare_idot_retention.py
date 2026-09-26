"""Regression: i-dots must survive prepare_for_engine / prune_ink_speckles.

Board #7 / episode E0022 fixed GCM mark retention in prune_ink_speckles and
idealize._components. Clean PROPAK's "Services" tittle already survives every
prepare stage (47 px island → elements_of area 34). These tests lock that
prepare-path behaviour so a future size-floor tweak cannot erase them again.

Degraded synthetic recipes that already merge the tittle in the RAW degrade
are out of scope here — that residual belongs with reconstruction / design_fit
(board #4), not more despeckle looseness.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from logo_raster_finish import prepare_for_engine, prune_ink_speckles  # noqa: E402
from tools.logo_vectorizer.idealize import elements_of  # noqa: E402

PROPAK_CLEAN = ROOT / "qa_logos" / "synthetic" / "clean" / "propak.png"
GCM_GOLDEN = ROOT / "qa_logos" / "golden" / "cases" / "gcm" / "original.png"


def _load_rgba(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGBA"))


def _small_islands(
    arr: np.ndarray, *, min_area: int = 8, max_area: int = 120
) -> list[tuple[int, int, int, int, int, tuple[float, float, float]]]:
    """Return (area, x0, y0, x1, y1, mean_rgb) for compact ink islands."""
    from scipy import ndimage

    ink = arr[:, :, 3] >= 48
    lab, n = ndimage.label(ink)
    out: list[tuple[int, int, int, int, int, tuple[float, float, float]]] = []
    for i in range(1, n + 1):
        ys, xs = np.where(lab == i)
        area = int(len(ys))
        if area < min_area or area > max_area:
            continue
        rgb = tuple(float(x) for x in arr[ys, xs, :3].mean(axis=0))
        out.append(
            (area, int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()), rgb)
        )
    return out


@pytest.mark.skipif(not PROPAK_CLEAN.is_file(), reason="propak clean fixture missing")
def test_clean_propak_services_idot_survives_prepare_for_engine():
    """PROPAK's blue Services tittle is a separate island through prepare."""
    raw = _load_rgba(PROPAK_CLEAN)
    raw_dots = [
        t
        for t in _small_islands(raw, min_area=20, max_area=80)
        if t[5][2] > t[5][0] and t[5][2] > 60
    ]
    assert raw_dots, "clean PROPAK fixture should carry a separate Services i-dot"

    prep = prepare_for_engine(raw)
    prep_dots = [
        t
        for t in _small_islands(prep, min_area=20, max_area=80)
        if t[5][2] > t[5][0] and t[5][2] > 60
    ]
    assert prep_dots, (
        "prepare_for_engine dropped PROPAK's Services i-dot "
        f"(raw had {raw_dots}; prep small islands={_small_islands(prep)})"
    )
    # The clean master keeps ~47 px; after crop/snap, area stays in the same band.
    assert any(30 <= t[0] <= 70 for t in prep_dots)

    els = elements_of(prep)
    idot_els = [
        e
        for e in els
        if 20 <= e.area <= 80
        and e.colour[2] > e.colour[0]
        and (e.bbox[3] - e.bbox[1]) <= 12
        and (e.bbox[2] - e.bbox[0]) <= 12
    ]
    assert idot_els, (
        "elements_of should still see the Services i-dot after prepare "
        f"(got areas={[e.area for e in els if e.area < 100]})"
    )


@pytest.mark.skipif(not GCM_GOLDEN.is_file(), reason="gcm golden fixture missing")
def test_gcm_modification_idots_survive_prepare_for_engine():
    """GCM's three Modification tittles must not be pruned as speckles."""
    raw = _load_rgba(GCM_GOLDEN)
    prep = prepare_for_engine(raw)
    # Modification sits on the lower word row; tittles are compact navy islands.
    dots = [
        t
        for t in _small_islands(prep, min_area=20, max_area=120)
        if t[5][2] > t[5][0] + 30
        and t[5][2] > 60
        and (t[4] - t[2]) <= 14
        and (t[3] - t[1]) <= 14
        and t[2] >= int(prep.shape[0] * 0.55)
    ]
    assert len(dots) >= 3, (
        "expected >=3 Modification i-dots after prepare; "
        f"got {len(dots)}: {dots}"
    )


def test_prune_ink_speckles_keeps_compact_mark_beside_stem():
    """Synthetic stand-in for E0022: a 5x5 tittle beside a stem must survive."""
    h, w = 80, 120
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    # Stem (~400 px) and tittle (~25 px) in the same navy — size floor would
    # drop the tittle at 1.2% of the largest island without _is_small_mark.
    arr[20:70, 30:38, :] = (21, 35, 69, 255)
    arr[10:15, 31:36, :] = (21, 35, 69, 255)

    out = prune_ink_speckles(arr)
    from scipy import ndimage

    ink = out[:, :, 3] >= 48
    lab, n = ndimage.label(ink)
    sizes = sorted(int((lab == i).sum()) for i in range(1, n + 1))
    assert any(20 <= s <= 40 for s in sizes), f"tittle missing; sizes={sizes}"
    assert any(s >= 300 for s in sizes), f"stem missing; sizes={sizes}"


def test_color_split_prune_keeps_small_teal_beside_large_red():
    """Board #8: teal tagline letters must not be sized against red ARC area."""
    h, w = 80, 200
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    # Large red ARC stand-in (~3500 px) — global 1.2% floor ≈ 42 px.
    arr[15:65, 10:80, :] = (226, 45, 62, 255)
    # Small teal RESOURCES stand-ins (~30 px each), well under a global
    # floor keyed off the red block, but safe vs teal's own largest island.
    for x0 in (100, 120, 140, 160):
        arr[30:40, x0 : x0 + 3, :] = (45, 70, 75, 255)

    # Global prune (single mask) would drop the teal letters; color-split
    # must keep them.
    out = prune_ink_speckles(arr, min_px=18, min_frac=0.012)
    teal = (
        (out[:, :, 3] >= 48)
        & (out[:, :, 2].astype(int) > out[:, :, 0].astype(int) + 10)
    )
    from scipy import ndimage

    lab, n = ndimage.label(teal)
    sizes = sorted(int((lab == i).sum()) for i in range(1, n + 1))
    assert n >= 4, f"expected >=4 teal islands; sizes={sizes}"
    assert all(s >= 20 for s in sizes), f"teal letters pruned; sizes={sizes}"
