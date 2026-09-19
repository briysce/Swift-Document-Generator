"""
Customer-logo "Recreate" pipeline.

Given an arbitrary customer logo raster (PNG / JPG, transparent or on a
solid studio background), produce a clean, layered SVG plus a rendered
PNG suitable for print. Reuses the manual-quality Bezier tracer and
sectional composer from `tools/logo_vectorizer`.

Pipeline
--------

1.  **Background strip.** Detect and clear the outer background:
    - already-transparent PNGs are left alone.
    - solid / studio backgrounds are flood-filled to transparent from the
      four corners with an adaptive color tolerance.
    - near-white / near-black backgrounds fall through to a luminance
      threshold so we always get *something* transparent to work with.

2.  **Palette clustering.** Group opaque foreground pixels into up to
    `max_colors` clusters via numpy-based k-means (no scikit-learn
    requirement). Small clusters are merged into the nearest larger
    cluster and near-duplicate colors (Euclidean distance < 24) are
    collapsed. This yields anywhere from 1..N distinct fill colors, the
    typical count for a real logo.

3.  **Sectional trace.** Build one `Section` per color group and hand it
    off to `vectorize_sectional`, which runs the manual-quality Bezier
    fitter on every section independently and composes them into a
    single layered SVG (`<g id="color-#hhhhhh">…</g>` per fill).

4.  **Rasterize.** Render the SVG back to PNG via
    `sectional.rasterize_svg` at a designer-friendly width so the PNG
    derivative is usable in documents without touching Inkscape.

Public API
----------

    recreate_customer_logo(input_path, output_svg=None, output_png=None,
                            *, max_colors=6, render_width=2000)

Returns a `RecreateResult` with the SVG string, per-section trace
statistics, and output paths.

Design notes
------------

- We deliberately avoid third-party ML dependencies. The k-means loop
  runs on a stratified sample of at most 50 000 pixels and typically
  converges in fewer than 12 iterations.
- Background detection is intentionally conservative: if a raster has
  transparent alpha already, we trust it; otherwise we run a corner
  flood fill and *only* zero pixels connected to a corner region. This
  preserves intentional white / black elements that sit inside the
  artwork (e.g. white ink on a colored circle).
- The section fill hex uses the *cluster median* rather than the
  centroid, which stays truer to human perception when the cluster
  covers a slight gradient.
"""

from __future__ import annotations

import io
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from PIL import Image

# Support running as a module (python -m tools.logo_vectorizer.customer_recreate)
# even when the package parent isn't on sys.path yet.
if __package__ in (None, ""):
    ROOT = Path(__file__).resolve().parents[2]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from tools.logo_vectorizer.analyze import analyze_raster  # noqa: E402
    from tools.logo_vectorizer.sectional import (  # noqa: E402
        SectionalResult,
        rasterize_svg,
        vectorize_sectional,
    )
    from tools.logo_vectorizer.sections import Section, SectionSet  # noqa: E402
else:
    from .analyze import analyze_raster
    from .sectional import SectionalResult, rasterize_svg, vectorize_sectional
    from .sections import Section, SectionSet


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class RecreateResult:
    """Outcome of `recreate_customer_logo`."""

    svg_path: Path | None
    png_path: Path | None
    svg_text: str
    section_count: int
    palette_hex: list[str]
    source_size: tuple[int, int]
    background_stripped: bool
    total_anchors: int
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Background removal
# ---------------------------------------------------------------------------


ALPHA_HAS_HOLES = 32
"""Alpha value below which we treat a pixel as "already transparent"."""


def _has_meaningful_transparency(alpha: np.ndarray) -> bool:
    """True when a substantial fraction of the border is already transparent."""
    h, w = alpha.shape[:2]
    if w < 4 or h < 4:
        return False
    ring = np.concatenate([
        alpha[0, :], alpha[-1, :], alpha[:, 0], alpha[:, -1],
    ])
    return float((ring < ALPHA_HAS_HOLES).mean()) >= 0.35


def _corner_flood_bg(
    rgba: np.ndarray, *, tolerance: int = 26
) -> tuple[np.ndarray, bool]:
    """
    Flood-fill from the four corners in color space; return an (H, W) bool
    mask of pixels considered background, and whether stripping is warranted.
    """
    h, w = rgba.shape[:2]
    if w < 4 or h < 4:
        return np.zeros((h, w), dtype=bool), False

    rgb = rgba[:, :, :3].astype(np.int16)
    corners = [
        (0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1),
    ]
    corner_colors = np.array(
        [rgb[y, x] for y, x in corners], dtype=np.int16
    )
    median = np.median(corner_colors, axis=0).astype(np.int16)
    # Check that most corners agree with the median. If not, the image is
    # already busy at the border (e.g. rendered on a photo), so stripping
    # by flood fill would eat into the logo — leave as-is.
    diffs = np.max(np.abs(corner_colors - median), axis=1)
    if (diffs <= tolerance).sum() < 3:
        return np.zeros((h, w), dtype=bool), False

    # Fast approximate flood: every pixel within tolerance of median AND
    # 4-connected to a corner. Using cv2.floodFill for perf.
    mask_out = np.zeros((h + 2, w + 2), dtype=np.uint8)
    seed_img = rgba[:, :, :3].astype(np.uint8).copy()
    for y, x in corners:
        try:
            cv2.floodFill(
                seed_img,
                mask_out,
                seedPoint=(x, y),
                newVal=(0, 0, 0),
                loDiff=(tolerance, tolerance, tolerance),
                upDiff=(tolerance, tolerance, tolerance),
                flags=cv2.FLOODFILL_MASK_ONLY | (4 | (255 << 8)),
            )
        except cv2.error:
            continue
    bg_mask = mask_out[1:-1, 1:-1] > 0
    return bg_mask, bool(bg_mask.any())


def _luminance_bg_fallback(rgba: np.ndarray) -> tuple[np.ndarray, bool]:
    """If corners are dark or ambiguous, still peel off a near-uniform border."""
    h, w = rgba.shape[:2]
    if w < 4 or h < 4:
        return np.zeros((h, w), dtype=bool), False
    rgb = rgba[:, :, :3]
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    # Very light background (studio white)
    white_like = (r > 232) & (g > 232) & (b > 232)
    # Very dark background (studio black)
    black_like = (r < 20) & (g < 20) & (b < 20)
    # Only strip when the border is dominated by one of these.
    ring = np.zeros_like(r, dtype=bool)
    ring[0, :] = True
    ring[-1, :] = True
    ring[:, 0] = True
    ring[:, -1] = True
    if float(white_like[ring].mean()) >= 0.6:
        return white_like, True
    if float(black_like[ring].mean()) >= 0.6:
        return black_like, True
    return np.zeros((h, w), dtype=bool), False


def _looks_like_jpeg_source(img: Image.Image) -> bool:
    """
    JPEG-origin rasters have no alpha (all 255) but often carry compression
    noise. We treat that as a signal to lightly smooth before clustering.
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    a = np.array(img.split()[3])
    return bool(a.min() == 255 and a.max() == 255)


def _preclean_for_clustering(img: Image.Image) -> Image.Image:
    """Bilateral-smooth JPEG-origin images so k-means doesn't chase artifacts."""
    if not _looks_like_jpeg_source(img):
        return img
    rgba = np.array(img.convert("RGBA"))
    bgr = cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_RGB2BGR)
    smoothed = cv2.bilateralFilter(bgr, d=5, sigmaColor=35, sigmaSpace=5)
    rgb = cv2.cvtColor(smoothed, cv2.COLOR_BGR2RGB)
    out = np.dstack([rgb, rgba[:, :, 3]])
    return Image.fromarray(out, mode="RGBA")


def strip_background(img: Image.Image) -> tuple[Image.Image, bool]:
    """
    Return an RGBA image with the background flood-filled to transparent.

    Returns `(image, stripped)` — `stripped` is False if the input was
    already meaningfully transparent (nothing changed).
    """
    rgba = np.array(img.convert("RGBA"))
    alpha = rgba[:, :, 3]

    if _has_meaningful_transparency(alpha):
        return Image.fromarray(rgba, mode="RGBA"), False

    h, w = rgba.shape[:2]
    total_px = float(h * w)

    bg_mask, ok = _corner_flood_bg(rgba)
    # If the corner flood only nibbled at a tiny sliver, fall through to
    # the luminance fallback: many JPEG-encoded logos have compression
    # noise at the border that the flood tolerance can't jump.
    if ok and total_px > 0 and float(bg_mask.sum()) / total_px < 0.05:
        lb_mask, lb_ok = _luminance_bg_fallback(rgba)
        if lb_ok and float(lb_mask.sum()) > float(bg_mask.sum()):
            bg_mask, ok = lb_mask, lb_ok
    if not ok:
        bg_mask, ok = _luminance_bg_fallback(rgba)

    if not ok:
        return Image.fromarray(rgba, mode="RGBA"), False

    rgba_out = rgba.copy()
    rgba_out[bg_mask, 3] = 0
    # Anti-alias fringe cleanup: after killing solid bg, feather 1 px so
    # remaining halo pixels fade instead of staying as a hard 255 ring.
    edge_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    fringe = cv2.dilate(bg_mask.astype(np.uint8), edge_kernel) & ~bg_mask
    if fringe.any():
        rgba_out[fringe.astype(bool), 3] = (
            rgba_out[fringe.astype(bool), 3].astype(np.int16) * 3 // 4
        ).astype(np.uint8)
    return Image.fromarray(rgba_out, mode="RGBA"), True


# ---------------------------------------------------------------------------
# Palette clustering (numpy k-means)
# ---------------------------------------------------------------------------


FG_ALPHA_THRESHOLD = 64
"""Alpha value at/above which a pixel counts as foreground.

Kept low so anti-aliased punctuation (periods, hyphens) and thin strokes
survive clustering — extremely small dots often have very few pixels above
alpha 128 and would otherwise get treated as background.
"""


def _sample_foreground_pixels(
    rgba: np.ndarray, max_samples: int = 50_000
) -> np.ndarray:
    """Return (N, 3) RGB samples from opaque foreground pixels."""
    alpha = rgba[:, :, 3]
    fg = alpha >= FG_ALPHA_THRESHOLD
    if not fg.any():
        return np.empty((0, 3), dtype=np.float32)
    rgb = rgba[:, :, :3][fg].astype(np.float32)
    if rgb.shape[0] > max_samples:
        idx = np.random.RandomState(0).choice(
            rgb.shape[0], size=max_samples, replace=False
        )
        rgb = rgb[idx]
    return rgb


def _kmeans(
    samples: np.ndarray, k: int, *, max_iter: int = 24, tol: float = 1.5
) -> tuple[np.ndarray, np.ndarray]:
    """
    Tiny k-means++ over RGB samples. Returns (labels, centroids).

    We start with k-means++ seeding so the palette doesn't collapse when
    two color groups are close in RGB space.
    """
    n = samples.shape[0]
    if n <= k:
        return np.arange(n), samples.copy()

    rng = np.random.RandomState(0)
    # k-means++ seeding
    first = rng.randint(0, n)
    centroids = [samples[first]]
    dist2 = np.sum((samples - centroids[0]) ** 2, axis=1).astype(np.float64)
    for _ in range(1, k):
        total = float(dist2.sum())
        if total <= 1e-9:
            idx = int(rng.randint(0, n))
        else:
            probs = dist2 / total
            # Renormalize so np.random.choice sees exactly sum==1.0.
            probs = probs / probs.sum()
            probs[-1] = max(0.0, 1.0 - probs[:-1].sum())
            idx = int(rng.choice(n, p=probs))
        centroids.append(samples[idx])
        new_d = np.sum((samples - centroids[-1]) ** 2, axis=1)
        dist2 = np.minimum(dist2, new_d)
    centroids = np.stack(centroids)

    labels = np.zeros(n, dtype=np.int32)
    for _ in range(max_iter):
        # Assign
        d2 = (
            (samples[:, None, :] - centroids[None, :, :]) ** 2
        ).sum(axis=2)
        new_labels = np.argmin(d2, axis=1)
        # Update
        moved = 0.0
        new_centroids = centroids.copy()
        for c in range(k):
            m = new_labels == c
            if m.any():
                nc = samples[m].mean(axis=0)
                moved = max(moved, float(np.linalg.norm(nc - centroids[c])))
                new_centroids[c] = nc
        labels = new_labels
        centroids = new_centroids
        if moved < tol:
            break
    return labels, centroids


def _merge_similar(
    centroids: np.ndarray, counts: np.ndarray, min_dist: float = 24.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Greedily merge centroids that are within `min_dist` RGB units,
    keeping the larger cluster's centroid. Returns (map, centroids, counts)
    where `map[i]` is the new group id for original group `i`.
    """
    k = centroids.shape[0]
    order = np.argsort(-counts)
    kept: list[int] = []
    remap = np.full(k, -1, dtype=np.int32)
    for orig in order:
        placed = False
        for keep in kept:
            if np.linalg.norm(centroids[orig] - centroids[keep]) < min_dist:
                remap[orig] = kept.index(keep)
                counts[keep] += counts[orig]
                placed = True
                break
        if not placed:
            kept.append(int(orig))
            remap[orig] = len(kept) - 1
    new_centroids = np.stack([centroids[i] for i in kept])
    new_counts = np.array([counts[i] for i in kept], dtype=np.int64)
    return remap, new_centroids, new_counts


def _drop_tiny(
    remap: np.ndarray,
    centroids: np.ndarray,
    counts: np.ndarray,
    min_fraction: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Fold any group below `min_fraction` of total pixels into the nearest kept group."""
    total = counts.sum()
    if total == 0:
        return remap, centroids, counts
    threshold = max(int(total * min_fraction), 1)
    small = counts < threshold
    if not small.any() or small.all():
        return remap, centroids, counts
    kept_idx = np.where(~small)[0]
    if kept_idx.size == 0:
        return remap, centroids, counts
    id_remap = {}
    for orig_id in range(len(counts)):
        if not small[orig_id]:
            id_remap[orig_id] = int(np.where(kept_idx == orig_id)[0][0])
        else:
            # merge into nearest kept centroid
            nearest = kept_idx[
                np.argmin(
                    np.linalg.norm(
                        centroids[kept_idx] - centroids[orig_id], axis=1
                    )
                )
            ]
            id_remap[orig_id] = int(np.where(kept_idx == nearest)[0][0])
    new_remap = np.array(
        [id_remap[int(v)] for v in remap], dtype=np.int32
    )
    new_centroids = centroids[kept_idx]
    new_counts = np.zeros(kept_idx.size, dtype=np.int64)
    for old_id, new_id in id_remap.items():
        new_counts[new_id] += counts[old_id]
    return new_remap, new_centroids, new_counts


def _merge_analog_neighbors(
    remap: np.ndarray,
    centroids: np.ndarray,
    counts: np.ndarray,
    *,
    max_dist: float,
    small_ratio: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Absorb small near-analog clusters (anti-alias fringes) into the nearest
    larger cluster. A cluster is "small" if its pixel count is below
    `small_ratio * largest_count`, and "analog" if the RGB distance to a
    larger cluster is under `max_dist`. Runs iteratively until stable.
    """
    if centroids.shape[0] <= 1:
        return remap, centroids, counts

    working_map = dict(enumerate(remap.tolist()))
    active_ids = list(range(centroids.shape[0]))
    active_centroids = centroids.copy()
    active_counts = counts.copy()

    changed = True
    while changed and len(active_ids) > 1:
        changed = False
        order = np.argsort(-active_counts)
        largest = active_counts[order[0]]
        if largest <= 0:
            break
        threshold = largest * small_ratio
        for pos in order[::-1]:
            if active_counts[pos] >= threshold:
                continue
            # Find nearest larger cluster within max_dist.
            best = -1
            best_d = max_dist
            for other in order:
                if other == pos:
                    continue
                if active_counts[other] <= active_counts[pos]:
                    continue
                d = float(
                    np.linalg.norm(active_centroids[pos] - active_centroids[other])
                )
                if d < best_d:
                    best_d = d
                    best = int(other)
            if best < 0:
                continue
            # Merge pos → best in the working map.
            src_id = active_ids[pos]
            dst_id = active_ids[best]
            for orig, cur in list(working_map.items()):
                if cur == src_id:
                    working_map[orig] = dst_id
            active_counts[best] += active_counts[pos]
            # Remove pos.
            active_ids.pop(pos)
            active_centroids = np.delete(active_centroids, pos, axis=0)
            active_counts = np.delete(active_counts, pos, axis=0)
            changed = True
            break

    # Compact: remap old ids to 0..N-1 in size-descending order.
    order = np.argsort(-active_counts)
    id_translate = {
        active_ids[old_pos]: new_pos for new_pos, old_pos in enumerate(order)
    }
    new_remap = np.array(
        [id_translate[working_map[i]] for i in range(remap.shape[0])],
        dtype=np.int32,
    )
    new_centroids = active_centroids[order]
    new_counts = active_counts[order]
    return new_remap, new_centroids, new_counts


def cluster_palette(
    img: Image.Image,
    *,
    max_colors: int = 6,
    min_fraction: float = 0.01,
) -> tuple[list[tuple[int, int, int]], np.ndarray, np.ndarray]:
    """
    Cluster opaque foreground pixels into a compact palette.

    Returns (palette_rgb, labels_map, alpha_mask):
      - palette_rgb: list of (R, G, B) tuples, one per section, ordered by
        cluster size descending.
      - labels_map: uint8 (H, W) label image; 0 = background, i+1 = group i.
      - alpha_mask: uint8 (H, W) alpha map used for section masks.
    """
    rgba = np.array(img.convert("RGBA"))
    h, w = rgba.shape[:2]
    alpha = rgba[:, :, 3]

    samples = _sample_foreground_pixels(rgba)
    if samples.size == 0:
        return [], np.zeros((h, w), dtype=np.uint8), alpha

    k = max(1, min(max_colors, samples.shape[0]))
    labels, centroids = _kmeans(samples, k)

    counts = np.bincount(labels, minlength=k).astype(np.int64)
    remap, centroids, counts = _merge_similar(centroids, counts, min_dist=24)
    remap, centroids, counts = _drop_tiny(remap, centroids, counts, min_fraction)
    # Absorb near-analog anti-alias fringes: any cluster that is < 30% of
    # the largest AND within 60 RGB units of another cluster gets folded in.
    remap, centroids, counts = _merge_analog_neighbors(
        remap, centroids, counts, max_dist=60.0, small_ratio=0.30
    )

    # Re-assign every opaque pixel in the full image to nearest centroid.
    fg = alpha >= FG_ALPHA_THRESHOLD
    rgb = rgba[:, :, :3].astype(np.int32)
    d2 = np.zeros((h, w, centroids.shape[0]), dtype=np.int32)
    for i, c in enumerate(centroids.astype(np.int32)):
        d2[..., i] = (
            (rgb[..., 0] - c[0]) ** 2
            + (rgb[..., 1] - c[1]) ** 2
            + (rgb[..., 2] - c[2]) ** 2
        )
    label_img = np.argmin(d2, axis=2).astype(np.uint8) + 1
    label_img[~fg] = 0

    # Reorder groups by pixel count (largest first).
    order = np.argsort(-counts)
    palette = [tuple(int(x) for x in centroids[i]) for i in order]
    reorder_map = np.zeros(len(order) + 1, dtype=np.uint8)
    for new_id, old_id in enumerate(order):
        reorder_map[old_id + 1] = new_id + 1
    label_img = reorder_map[label_img]
    return palette, label_img, alpha


def _tune_analysis_for_recreate(analysis) -> None:  # type: ignore[no-untyped-def]
    """
    Raise trace fidelity for the Recreate pipeline. Low-resolution sources
    (e.g. a 500-pixel-wide raster) blow up jagged at extreme zoom unless we
    aggressively upscale the mask + smooth the contour before Bezier fit.
    """
    w, h = analysis.size
    long_side = max(w, h)
    if long_side < 400:
        analysis.recommended_upscale = max(analysis.recommended_upscale, 8)
        analysis.recommended_blur = max(analysis.recommended_blur, 1.8)
        analysis.smooth_sigma = max(analysis.smooth_sigma, 2.2)
    elif long_side < 900:
        analysis.recommended_upscale = max(analysis.recommended_upscale, 6)
        analysis.recommended_blur = max(analysis.recommended_blur, 1.6)
        analysis.smooth_sigma = max(analysis.smooth_sigma, 1.8)
    elif long_side < 2000:
        analysis.recommended_upscale = max(analysis.recommended_upscale, 4)
        analysis.recommended_blur = max(analysis.recommended_blur, 1.4)
        analysis.smooth_sigma = max(analysis.smooth_sigma, 1.4)
    else:
        analysis.recommended_upscale = max(analysis.recommended_upscale, 3)
        analysis.recommended_blur = max(analysis.recommended_blur, 1.2)
        analysis.smooth_sigma = max(analysis.smooth_sigma, 1.1)
    # Tighter Bezier fit tolerance so the curves hug the smoothed silhouette
    # closely without collapsing into polyline segments.
    analysis.fit_error_px = min(analysis.fit_error_px, 0.7)
    # Slightly wider straight tolerance so long clean strokes emit as lines.
    analysis.straight_dev_px = min(max(analysis.straight_dev_px, 0.5), 0.75)
    analysis.notes.append(
        f"recreate tuning: upscale={analysis.recommended_upscale} "
        f"blur={analysis.recommended_blur:.2f} "
        f"smooth_sigma={analysis.smooth_sigma:.2f}"
    )


def _hex(color: tuple[int, int, int]) -> str:
    r, g, b = (int(max(0, min(255, c))) for c in color)
    return f"#{r:02X}{g:02X}{b:02X}"


# ---------------------------------------------------------------------------
# Section building
# ---------------------------------------------------------------------------


def build_sections_from_palette(
    label_img: np.ndarray,
    palette: list[tuple[int, int, int]],
    *,
    min_area_px: int | None = None,
) -> SectionSet:
    """One `Section` per color group; small components are dropped."""
    h, w = label_img.shape[:2]
    if min_area_px is None:
        # Preserve punctuation-sized details (periods, hyphens): tight floor
        # and modest ceiling so a real 8×8-pixel period in a 3000-pixel-wide
        # logo survives even after morphological cleanup. JPEG grain is
        # handled up-stream by the bilateral filter in `_preclean_for_clustering`.
        min_area_px = int(max(6, min(48, (h * w) * 0.00002)))

    # After each morphology pass, discard sections that lose too much area
    # (usually pure JPEG-artifact clusters).
    min_final_ratio = 0.05

    sections = SectionSet()
    z = 10
    for i, rgb in enumerate(palette):
        section_id = i + 1
        mask = (label_img == section_id).astype(np.uint8) * 255
        raw_area = int((mask > 0).sum())
        if raw_area < min_area_px:
            continue
        # Morphological close to fuse anti-alias holes. We deliberately skip
        # MORPH_OPEN here — opening erodes small legitimate features like
        # periods, apostrophes, and thin stems that we want to preserve.
        # The `min_area_px` component filter below still drops speckle grain.
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
        # Drop connected components below the min-area threshold entirely.
        num, labels, stats, _ = cv2.connectedComponentsWithStats(
            (mask > 0).astype(np.uint8), connectivity=8
        )
        cleaned = np.zeros_like(mask)
        for comp_id in range(1, num):
            if int(stats[comp_id, cv2.CC_STAT_AREA]) >= min_area_px:
                cleaned[labels == comp_id] = 255
        mask = cleaned
        final_area = int((mask > 0).sum())
        if final_area == 0:
            continue
        if raw_area > 0 and final_area / raw_area < min_final_ratio:
            # Section was mostly speckle noise — skip it entirely.
            continue
        name = f"color-{_hex(rgb).lstrip('#').lower()}"
        sections.trace_order.append(
            Section(
                name=name,
                mask=mask,
                fill_hex=_hex(rgb),
                z_index=z,
                description=(
                    f"Recreated color group #{i + 1} "
                    f"(rgb={rgb}, pixels={final_area})"
                ),
            )
        )
        z += 10
    return sections


# ---------------------------------------------------------------------------
# Top-level pipeline
# ---------------------------------------------------------------------------


def recreate_customer_logo(
    input_path: Path,
    *,
    output_svg: Path | None = None,
    output_png: Path | None = None,
    max_colors: int = 6,
    render_width: int = 2000,
    render_background: str = "transparent",
    progress: Callable[[str], None] | None = None,
    use_ai: bool | None = None,
    ai_providers: list[str] | None = None,
) -> RecreateResult:
    """
    Full "Recreate" pipeline for a customer logo raster.

    - Strips flat / studio backgrounds.
    - Clusters foreground into up to `max_colors` colors.
    - Traces each color group with the manual-quality Bezier fitter.
    - Composes a layered SVG and, if requested, a PNG derivative.

    When Gemini (or other AI advisors) are configured, optionally inspects the
    raster first for brand colors / layout hints that tune clustering.
    """

    def _log(msg: str) -> None:
        if progress is not None:
            try:
                progress(msg)
            except Exception:
                pass
        print(msg, file=sys.stderr, flush=True)

    input_path = Path(input_path)
    if not input_path.is_file():
        raise FileNotFoundError(f"Recreate input not found: {input_path}")

    _log(f"[recreate] loading {input_path.name}")
    with Image.open(input_path) as src:
        src.load()
        img = src.convert("RGBA")

    # Auto-enable AI when a Gemini key is present unless explicitly disabled.
    ai_hints = None
    if use_ai is None:
        try:
            from tools.logo_vectorizer.env_loader import load_env, gemini_configured

            load_env()
            use_ai = gemini_configured()
        except Exception:
            use_ai = False
    if use_ai:
        try:
            from tools.logo_vectorizer.ai_advisors.orchestrator import (
                analyze_source_multi,
                resolve_providers,
            )

            providers = resolve_providers(ai_providers or ["gemini"])
            if providers:
                _log(f"[recreate] Gemini/AI source analysis ({', '.join(providers)})")
                ai_hints = analyze_source_multi(img, providers)
                if ai_hints is not None:
                    if ai_hints.brand_name:
                        _log(f"[recreate] AI brand_name={ai_hints.brand_name}")
                    if ai_hints.font_family_guess:
                        _log(
                            f"[recreate] AI font_family={ai_hints.font_family_guess}"
                        )
                    if ai_hints.dominant_colors_hex:
                        _log(
                            "[recreate] AI colors="
                            + ",".join(ai_hints.dominant_colors_hex)
                        )
                        # Prefer AI color count when it is a tighter palette.
                        suggested = len(ai_hints.dominant_colors_hex)
                        if 1 <= suggested <= max_colors:
                            max_colors = max(suggested, 2)
                    if ai_hints.layout_summary:
                        _log(f"[recreate] AI layout={ai_hints.layout_summary}")
        except Exception as exc:
            _log(f"[recreate] AI assist skipped: {exc}")

    _log("[recreate] stripping background")
    stripped_img, stripped = strip_background(img)
    _log(
        "[recreate] " + (
            "background stripped" if stripped else "kept existing transparency"
        )
    )

    _log("[recreate] clustering palette")
    cluster_input = _preclean_for_clustering(stripped_img)
    palette, label_img, _ = cluster_palette(cluster_input, max_colors=max_colors)
    if not palette:
        raise RuntimeError("no foreground pixels detected after bg strip")
    _log(f"[recreate] palette: {', '.join(_hex(c) for c in palette)}")

    _log("[recreate] tracing sections")
    sections = build_sections_from_palette(label_img, palette)
    if not sections.trace_order:
        raise RuntimeError("palette clustering left no traceable section")

    analysis = analyze_raster(stripped_img)
    _tune_analysis_for_recreate(analysis)
    result = vectorize_sectional(stripped_img, sections, analysis=analysis)
    _log(
        f"[recreate] traced {len(result.per_section)} sections, "
        f"~{result.total_anchors()} anchors"
    )

    svg_path = output_svg
    final_svg = result.svg
    if ai_hints and (ai_hints.brand_name or ai_hints.notes or ai_hints.dominant_colors_hex):
        meta = (
            f"<!-- gemini brand={ai_hints.brand_name} "
            f"font={ai_hints.font_family_guess} "
            f"colors={','.join(ai_hints.dominant_colors_hex)} -->\n"
        )
        final_svg = meta + final_svg
    if svg_path is not None:
        svg_path = Path(svg_path)
        svg_path.parent.mkdir(parents=True, exist_ok=True)
        svg_path.write_text(final_svg, encoding="utf-8")
        _log(f"[recreate] wrote SVG -> {svg_path}")

    png_path = None
    if output_png is not None:
        png_path = Path(output_png)
        # rasterize_svg needs an SVG file on disk; use a tempfile if the
        # caller didn't also request the SVG.
        svg_source = svg_path
        cleanup_tmp: Path | None = None
        if svg_source is None:
            import tempfile

            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".svg", delete=False, encoding="utf-8"
            )
            tmp.write(final_svg)
            tmp.close()
            svg_source = Path(tmp.name)
            cleanup_tmp = svg_source
        try:
            rasterize_svg(
                svg_source,
                png_path,
                width=render_width,
                background=render_background,
            )
            _log(f"[recreate] wrote PNG -> {png_path}")
        finally:
            if cleanup_tmp is not None:
                cleanup_tmp.unlink(missing_ok=True)

    notes = list(analysis.notes) if analysis else []
    if ai_hints is not None:
        if ai_hints.brand_name:
            notes.append(f"gemini_brand={ai_hints.brand_name}")
        if ai_hints.font_family_guess:
            notes.append(f"gemini_font={ai_hints.font_family_guess}")

    return RecreateResult(
        svg_path=svg_path,
        png_path=png_path,
        svg_text=final_svg,
        section_count=len(result.per_section),
        palette_hex=[_hex(c) for c in palette],
        source_size=result.source_size,
        background_stripped=stripped,
        total_anchors=result.total_anchors(),
        notes=notes,
    )


# ---------------------------------------------------------------------------
# CLI entry point (also reachable via `python -m tools.logo_vectorizer.customer_recreate`)
# ---------------------------------------------------------------------------


def _cli(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Recreate a customer logo: strip background, cluster colors, "
            "trace each color group with manual-quality Beziers, and emit "
            "a layered SVG + rendered PNG."
        )
    )
    parser.add_argument("--input", "-i", type=Path, required=True)
    parser.add_argument("--output-svg", type=Path)
    parser.add_argument("--output-png", type=Path)
    parser.add_argument("--max-colors", type=int, default=6)
    parser.add_argument("--render-width", type=int, default=2000)
    parser.add_argument(
        "--render-background",
        default="transparent",
        help="Background for the PNG render ('transparent' or a CSS color).",
    )
    args = parser.parse_args(argv)

    result = recreate_customer_logo(
        args.input,
        output_svg=args.output_svg,
        output_png=args.output_png,
        max_colors=args.max_colors,
        render_width=args.render_width,
        render_background=args.render_background,
    )
    print(
        f"Recreate OK: sections={result.section_count} "
        f"palette=[{', '.join(result.palette_hex)}] "
        f"bg_stripped={result.background_stripped} "
        f"anchors~{result.total_anchors}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
