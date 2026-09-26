"""The app's Swift logos are exports of the rebuilt vector, never stale copies."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("export_swift_app_logos", ROOT / "scripts" / "export_swift_app_logos.py")
X = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(X)


def test_committed_assets_are_current_exports_of_the_master():
    assert X.main(["--check"]) == 0


def test_placement_is_one_uniform_scale_inside_the_box():
    paths = X.read_master()
    s, tx, ty = X.placement(paths)
    moved = np.vstack([X._points(X.transform(d, s, tx, ty)) for d in paths.values()])
    w, h = X.BOX
    assert moved.min() >= X.MARGIN - 0.01
    assert moved[:, 0].max() <= w - X.MARGIN + 0.01 and moved[:, 1].max() <= h - X.MARGIN + 0.01
    # Centred: equal space either side on the axis that does not fill.
    assert abs(moved[:, 0].min() - (w - moved[:, 0].max())) < 0.05


def test_every_variant_is_paths_only():
    paths = X.read_master()
    place = X.placement(paths)
    for name in X.VARIANTS:
        svg = X.variant_svg(name, paths, place)
        assert "<image" not in svg and f'viewBox="0 0 {X.BOX[0]} {X.BOX[1]}"' in svg
