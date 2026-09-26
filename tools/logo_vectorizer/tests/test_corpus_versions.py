"""The corpus is versioned and reproducible from its manifest (E0134)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
from PIL import Image

from tools.logo_vectorizer.meedo_advisor import Run, comparable_previous

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("degrade", ROOT / "scripts" / "logo_synthetic_degrade.py")
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)


def _flat(colour):
    a = np.full((4, 4, 4), 255, np.uint8)
    a[1:3, 1:3, :3] = colour
    return a


def test_v2_keeps_grey_ink_that_v1_made_transparent():
    grey = _flat((107, 107, 107))
    assert D._jpeg_reconstruct_alpha(grey, 1)[1, 1, 3] == 0      # the bug, kept for corpus v1
    assert D._jpeg_reconstruct_alpha(grey, 2)[1, 1, 3] == 255    # grey ink is ink
    assert D._jpeg_reconstruct_alpha(grey, 2)[0, 0, 3] == 255    # the white plate stays opaque, as before
    orange = _flat((206, 78, 48))
    assert (D._jpeg_reconstruct_alpha(orange, 1) == D._jpeg_reconstruct_alpha(orange, 2)).all()


def test_a_corpus_regenerates_exactly_from_its_manifest(tmp_path, monkeypatch):
    clean = tmp_path / "clean"
    clean.mkdir()
    img = np.zeros((40, 80, 4), np.uint8)
    img[10:30, 10:70] = (107, 107, 107, 255)
    Image.fromarray(img, "RGBA").save(clean / "g.png")
    monkeypatch.setattr(D, "SYN", tmp_path)
    manifest = {"pairs": [{"id": "g__downscale_jpeg", "slug": "g", "clean": "clean/g.png", "recipe": "downscale_jpeg",
                           "seed": 7, "params": dict(D.RECIPES["downscale_jpeg"])}]}
    (tmp_path / "pairs.json").write_text(json.dumps(manifest))
    a = D.from_manifest(tmp_path / "pairs.json", tmp_path / "a", degrader=2)
    D.from_manifest(tmp_path / "pairs.json", tmp_path / "b", degrader=2)
    x = np.asarray(Image.open(tmp_path / "a" / "g__downscale_jpeg.png"))
    y = np.asarray(Image.open(tmp_path / "b" / "g__downscale_jpeg.png"))
    assert (x == y).all() and a["degrader"] == 2 and a["pairs"][0]["seed"] == 7


def _run(rid, degrader=None):
    row = {"run_id": rid, "pair_id": "trialta__import_combo", "seed": 150, "engine": "vectorize", "ok": True,
           "composite": 0.5, "min_height": 1200, "engines": ["vectorize"], "idealize": False}
    if degrader is not None:
        row["degrader"] = degrader
    return Run(rid, [row])


def test_a_v2_run_is_never_compared_with_a_v1_run_and_v1_fingerprints_do_not_move():
    assert comparable_previous([_run("r0"), _run("r1", degrader=2)]) is None
    assert _run("r0").corpus() == _run("r1", degrader=1).corpus()
    assert comparable_previous([_run("r0", degrader=2), _run("r1", degrader=2)]).run_id == "r0"
