"""
Per-raster analysis for the manual-quality tracer.

For every input PNG the tracer receives, we automatically inspect the raster
to decide *how* it should be traced — the same first pass a designer does
before dropping their first anchor point:

    - What is the palette? Solid brand fills vs anti-aliased edges?
    - How wide is the anti-alias fringe? (drives blur/threshold + upscale)
    - Does the shape look like typography (many holes, straight-ish edges,
      sharp corners) or an icon/mark (few holes, more curves)?
    - Is there strong left-right or up-down symmetry (logos often are)?
    - What corner-angle threshold and Bezier fit tolerance suits this raster?

The resulting `RasterAnalysis` object is used to construct
`ManualTraceConfig` — so the same fitter engine adapts to each raster
without hand-tuned per-image code paths.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import cv2
import numpy as np
from PIL import Image

from .manual_trace import ManualTraceConfig


BRAND_ORANGE_HEX = "#CE4E30"
BRAND_ORANGE_RGB = (0xCE, 0x4E, 0x30)


@dataclass
class RasterAnalysis:
    """Summary of what a raster looks like + params tuned for it."""

    size: tuple[int, int]
    """(width, height) of the source raster."""

    dominant_colors: list[tuple[int, int, int, int]] = field(default_factory=list)
    """Top-N RGBA color clusters (by pixel count)."""

    alpha_levels: int = 1
    """Distinct alpha values → indicates anti-aliased vs binary edges."""

    edge_softness_px: float = 0.0
    """Median anti-alias fringe width in raster pixels."""

    is_anti_aliased: bool = False
    is_binary_alpha: bool = False
    is_solid_background: bool = False

    letter_like: bool = False
    """True when the raster looks like typography (many CCs, straight edges)."""

    stroke_median_px: float = 0.0
    """Rough foreground stroke width used to pick blur radius / min-area."""

    corner_density: float = 0.0
    """Corners per 100 perimeter pixels — indicates typography vs freeform."""

    horizontal_symmetry: float = 0.0
    """0..1 score for left-right mirror symmetry of the alpha mask."""

    vertical_symmetry: float = 0.0

    holes: int = 0
    connected_components: int = 0

    recommended_upscale: int = 4
    recommended_blur: float = 1.1
    recommended_threshold: int = 96
    corner_angle_deg: float = 42.0
    fit_error_px: float = 0.9
    straight_dev_px: float = 0.55
    axis_snap_deg: float = 1.5
    smooth_sigma: float = 0.9
    max_arc_between_anchors: float = 55.0
    min_area: float = 12.0

    notes: list[str] = field(default_factory=list)

    def to_manual_config(self) -> ManualTraceConfig:
        return ManualTraceConfig(
            corner_angle_deg=self.corner_angle_deg,
            max_error_px=self.fit_error_px,
            straight_dev_px=self.straight_dev_px,
            axis_snap_deg=self.axis_snap_deg,
            smooth_sigma=self.smooth_sigma,
            max_arc_between_anchors=self.max_arc_between_anchors,
            min_area=self.min_area,
            output_scale=float(self.recommended_upscale),
        )


def _quantized_palette(arr: np.ndarray, top_n: int = 5) -> list[tuple[int, int, int, int]]:
    """Return top-N most frequent RGBA colors (with a small tolerance)."""
    if arr.ndim != 3 or arr.shape[2] < 4:
        return []
    # Quantize to 8-step buckets so anti-aliased pixels don't dominate.
    q = (arr // 16 * 16).astype(np.int64)
    flat = q.reshape(-1, 4)
    view = np.ascontiguousarray(flat).view(
        np.dtype((np.void, flat.dtype.itemsize * 4))
    )
    _, idx, counts = np.unique(view, return_index=True, return_counts=True)
    order = np.argsort(-counts)[:top_n]
    return [tuple(int(x) for x in flat[idx[i]]) for i in order]


def _edge_softness(alpha: np.ndarray) -> float:
    """Median transition width (in px) between mostly-transparent and mostly-opaque."""
    # Sobel gradient magnitude — larger values in narrow bands = harder edges.
    sob = cv2.Sobel(alpha, cv2.CV_32F, 1, 0) ** 2
    sob += cv2.Sobel(alpha, cv2.CV_32F, 0, 1) ** 2
    grad = np.sqrt(sob)
    edge_mask = grad > 20
    if not edge_mask.any():
        return 0.0
    # Approximate transition width = 255 / max local gradient.
    peaks = grad[edge_mask]
    peaks = np.clip(peaks, 1.0, 255.0 * 6)
    widths = 255.0 / peaks
    return float(np.median(widths))


def _stroke_width_median(binary: np.ndarray) -> float:
    """Estimate stroke width via distance transform of the foreground."""
    if binary.dtype != np.uint8:
        binary = binary.astype(np.uint8)
    if binary.max() != 255:
        binary = (binary > 0).astype(np.uint8) * 255
    inv = cv2.bitwise_not(binary)
    dist = cv2.distanceTransform(binary, cv2.DIST_L2, 3)
    # Median distance value along the medial axis approximates half stroke width.
    axis = dist > np.percentile(dist[dist > 0], 60) if (dist > 0).any() else None
    if axis is None or not axis.any():
        return float(dist.max() or 1.0)
    return float(2.0 * np.median(dist[axis]))


def _symmetry_score(mask: np.ndarray, axis: str) -> float:
    """Return 0..1 mirror-symmetry score along axis ('x' or 'y')."""
    if not mask.any():
        return 0.0
    if axis == "x":
        flipped = mask[:, ::-1]
    else:
        flipped = mask[::-1, :]
    inter = np.logical_and(mask, flipped).sum()
    union = np.logical_or(mask, flipped).sum()
    return float(inter / union) if union else 0.0


def _corner_density_estimate(binary: np.ndarray) -> tuple[float, float]:
    """Return (corners_per_100_perimeter, total_perimeter)."""
    contours, _ = cv2.findContours(
        binary.astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE
    )
    if not contours:
        return 0.0, 0.0
    total_peri = 0.0
    total_corners = 0
    for c in contours:
        peri = cv2.arcLength(c, True)
        if peri < 20:
            continue
        total_peri += peri
        # approxPolyDP epsilon = 2 px counts sharp corners.
        approx = cv2.approxPolyDP(c, 2.0, True)
        total_corners += len(approx)
    if total_peri < 1:
        return 0.0, 0.0
    return total_corners * 100.0 / total_peri, total_peri


def analyze_raster(img: Image.Image) -> RasterAnalysis:
    """Automatically classify a raster and recommend fitter parameters."""
    rgba = img.convert("RGBA")
    arr = np.array(rgba)
    h, w = arr.shape[:2]
    alpha = arr[:, :, 3]

    palette = _quantized_palette(arr)

    alpha_uniq = int(len(np.unique(alpha)))
    softness = _edge_softness(alpha)
    is_binary_alpha = alpha_uniq <= 2
    is_anti_aliased = alpha_uniq > 4 or softness > 1.4

    # Determine effective foreground mask.
    if is_binary_alpha and alpha.max() > 0:
        fg = (alpha > 0).astype(np.uint8) * 255
        is_solid_background = False
    elif alpha.max() > 0 and alpha.min() < 32:
        fg = (alpha >= 96).astype(np.uint8) * 255
        is_solid_background = False
    else:
        # Assume white background (common for scanned/print logos).
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        fg = (~((r > 232) & (g > 232) & (b > 232))).astype(np.uint8) * 255
        is_solid_background = True

    stroke = _stroke_width_median(fg)
    corner_density, perimeter = _corner_density_estimate(fg)
    num_labels, _labels, stats, _cent = cv2.connectedComponentsWithStats(
        fg, connectivity=8
    )
    cc_count = int(max(0, num_labels - 1))
    holes_estimate = 0
    contours, hier = cv2.findContours(fg, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if hier is not None:
        holes_estimate = int(((hier[0][:, 3] != -1)).sum())

    h_sym = _symmetry_score(fg > 0, "x")
    v_sym = _symmetry_score(fg > 0, "y")

    letter_like = (
        cc_count >= 3
        and corner_density > 2.5
        and stroke <= max(w, h) / 12
    )

    ra = RasterAnalysis(
        size=(w, h),
        dominant_colors=palette,
        alpha_levels=alpha_uniq,
        edge_softness_px=softness,
        is_anti_aliased=is_anti_aliased,
        is_binary_alpha=is_binary_alpha,
        is_solid_background=is_solid_background,
        letter_like=letter_like,
        stroke_median_px=stroke,
        corner_density=corner_density,
        horizontal_symmetry=h_sym,
        vertical_symmetry=v_sym,
        holes=holes_estimate,
        connected_components=cc_count,
    )

    # ---- Tuning heuristics (real designers pick these too, per raster) ----
    long_side = max(w, h)
    if long_side < 400:
        ra.recommended_upscale = 6
    elif long_side < 900:
        ra.recommended_upscale = 4
    elif long_side < 2000:
        ra.recommended_upscale = 3
    else:
        ra.recommended_upscale = 2

    if is_binary_alpha and not is_solid_background:
        ra.recommended_blur = 0.6
        ra.recommended_threshold = 128
    elif softness < 1.5:
        ra.recommended_blur = 0.8
        ra.recommended_threshold = 110
    elif softness < 3.5:
        ra.recommended_blur = 1.1
        ra.recommended_threshold = 96
    else:
        ra.recommended_blur = 1.4
        ra.recommended_threshold = 80

    if letter_like:
        ra.corner_angle_deg = 35.0
        ra.fit_error_px = 0.75
        ra.straight_dev_px = 0.5
        ra.axis_snap_deg = 2.0
        ra.smooth_sigma = 0.8
        ra.max_arc_between_anchors = 42.0
        ra.notes.append("letter-like: tighter corner detection")
    else:
        ra.corner_angle_deg = 48.0
        ra.fit_error_px = 1.0
        ra.straight_dev_px = 0.65
        ra.axis_snap_deg = 1.25
        ra.smooth_sigma = 1.0

    if h_sym > 0.985 or v_sym > 0.985:
        ra.notes.append(
            f"high symmetry (H={h_sym:.3f} V={v_sym:.3f}) — enabling axis snap"
        )
        ra.axis_snap_deg = max(ra.axis_snap_deg, 2.5)

    ra.min_area = max(6.0, long_side * ra.recommended_upscale * 0.006)
    if is_solid_background:
        ra.notes.append("white background — extracting foreground by luminance")

    return ra


def color_masks(
    img: Image.Image,
    palette: Sequence[tuple[int, int, int]] | None = None,
    tolerance: int = 32,
) -> dict[str, np.ndarray]:
    """
    Split raster by color into binary masks (uint8 0/255).

    Returns { 'orange': mask, 'black': mask, 'white': mask } for the common
    Swift palette; extend as needed via *palette*.
    """
    arr = np.array(img.convert("RGBA"))
    r, g, b, a = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2], arr[:, :, 3]

    masks: dict[str, np.ndarray] = {}
    # Orange (#CE4E30) with generous saturation window.
    orange = (
        (r > 140)
        & (g < 140)
        & (b < 140)
        & (r.astype(int) - g.astype(int) > 40)
        & (r.astype(int) - b.astype(int) > 40)
    )
    if a.max() < 255:
        orange = orange & (a > 32)
    masks["orange"] = (orange.astype(np.uint8)) * 255

    black = (r < 90) & (g < 90) & (b < 90)
    if a.max() < 255:
        black = black & (a > 32)
    masks["black"] = (black.astype(np.uint8)) * 255

    white_bg = (r > 232) & (g > 232) & (b > 232)
    masks["white"] = (white_bg.astype(np.uint8)) * 255

    return masks
