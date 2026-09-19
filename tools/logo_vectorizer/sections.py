"""
Sectional/layered decomposition of a composite raster logo.

Instead of collapsing every pixel into one traced blob, this module splits a
raster into *sections* (named regions) so each section can be traced
independently with the manual-quality fitter and composed back as a
layered SVG (`<g id="section-name">…</g>` per section).

Two entry points are provided:

    - `decompose_swift_supply(...)`  — bespoke decomposer for the Swift Supply
      full-lockup (orange bars + SWIFT + shadow + SUPPLY). Regions are
      identified by color + horizontal band position, so slight artwork
      changes still work.
    - `decompose_by_color(...)`  — generic fallback: split by dominant color
      groups and, within each color, by connected-component geometry.

Every decomposer returns a `SectionSet` — an *ordered* list of `Section`
objects (order defines requested tracing order; z-order is a separate
attribute so we can trace shadow before SWIFT-orange but still render
shadow *below* the orange fill).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

import cv2
import numpy as np
from PIL import Image

from .analyze import color_masks


ORANGE_FILL_HEX = "#CE4E30"
BLACK_FILL_HEX = "#111111"


@dataclass
class Section:
    """One layer of a sectional decomposition."""

    name: str
    """Slug used as SVG group id (e.g. `swift-orange`)."""

    mask: np.ndarray
    """Binary uint8 mask (0/255) sized like the source raster."""

    fill_hex: str
    """SVG fill for the whole section."""

    z_index: int = 0
    """Render order (lower = drawn first / behind)."""

    description: str = ""
    """Human-readable note carried into logs and README-style output."""

    def area_px(self) -> int:
        return int((self.mask > 0).sum())


@dataclass
class SectionSet:
    """A sequence of sections + the requested tracing order."""

    trace_order: list[Section] = field(default_factory=list)

    @property
    def render_order(self) -> list[Section]:
        return sorted(self.trace_order, key=lambda s: s.z_index)


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------

def _open_close(mask: np.ndarray, radius: int = 1) -> np.ndarray:
    """Morphological open+close to remove single-pixel specks and pinholes."""
    if radius <= 0:
        return mask
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    m = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    return cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return mask
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    return cv2.dilate(mask, k)


def _components_bboxes(mask: np.ndarray) -> list[tuple[int, int, int, int, int, np.ndarray]]:
    """Return per-CC (x, y, w, h, area, component_mask)."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(
        (mask > 0).astype(np.uint8), connectivity=8
    )
    out: list[tuple[int, int, int, int, int, np.ndarray]] = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        comp_mask = (labels == i).astype(np.uint8) * 255
        out.append((int(x), int(y), int(w), int(h), int(area), comp_mask))
    return out


def _union(masks: Iterable[np.ndarray]) -> np.ndarray:
    it = iter(masks)
    try:
        out = next(it).copy()
    except StopIteration:
        raise ValueError("empty iterable")
    for m in it:
        out = np.maximum(out, m)
    return out


# ---------------------------------------------------------------------------
# Swift Supply full-lockup decomposer
# ---------------------------------------------------------------------------

def decompose_swift_supply(
    img: Image.Image,
    *,
    outline_dilation_px: int = 3,
) -> SectionSet:
    """
    Split the Swift Supply full lockup into five named layers:

        1. `swift-orange`    — the italic SWIFT letterforms (orange fill)
        2. `swift-shadow`    — hard drop shadow + thin black outline behind SWIFT
        3. `supply-black`    — the SUPPLY wordmark under SWIFT
        4. `bar-top`         — top orange bar + its thin black borders
        5. `bar-bottom`      — bottom orange bar + its thin black borders

    Regions are identified by color plus horizontal position of orange bars,
    which lets this decomposer track modest artwork tweaks.

    Returns a `SectionSet` whose `trace_order` follows the sequence above.
    """
    rgba = img.convert("RGBA")
    arr = np.array(rgba)
    h, w = arr.shape[:2]

    masks = color_masks(rgba)
    orange = _open_close(masks["orange"], radius=1)
    black = _open_close(masks["black"], radius=0)

    row_orange = (orange > 0).sum(axis=1)
    row_black = (black > 0).sum(axis=1)

    # ------- 1. Detect orange bars (rows where orange spans most of width).
    bar_threshold = int(w * 0.55)
    orange_rows = np.where(row_orange > bar_threshold)[0]
    bar_row_groups: list[tuple[int, int]] = []
    if len(orange_rows):
        start = orange_rows[0]
        prev = start
        for y in orange_rows[1:]:
            if y - prev > 2:
                bar_row_groups.append((int(start), int(prev)))
                start = int(y)
            prev = int(y)
        bar_row_groups.append((int(start), int(prev)))

    # Merge consecutive "bar-y" groups that are within 4 px of each other
    # so a thin black separator between orange rows doesn't split one bar.
    merged: list[tuple[int, int]] = []
    for band in bar_row_groups:
        if merged and band[0] - merged[-1][1] <= 4:
            merged[-1] = (merged[-1][0], band[1])
        else:
            merged.append(band)
    # Pick the top-most and bottom-most bars.
    top_band = merged[0] if merged else (0, 0)
    bottom_band = merged[-1] if merged else (0, 0)

    def _bar_mask(band: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
        """Return (orange_mask_bar, black_border_mask_bar)."""
        y0, y1 = band
        pad = 4  # capture the thin black border immediately above/below
        y_lo = max(0, y0 - pad)
        y_hi = min(h - 1, y1 + pad)
        m_orange = np.zeros_like(orange)
        m_orange[y_lo : y_hi + 1] = orange[y_lo : y_hi + 1]
        m_border = np.zeros_like(black)
        m_border[y_lo : y_hi + 1] = black[y_lo : y_hi + 1]
        return m_orange, m_border

    top_orange, top_border = _bar_mask(top_band)
    bot_orange, bot_border = _bar_mask(bottom_band)

    bar_rows_mask = np.zeros(h, dtype=bool)
    for band in (top_band, bottom_band):
        y0, y1 = band
        bar_rows_mask[max(0, y0 - 4) : min(h, y1 + 5)] = True

    # ------- 2. SWIFT orange (all orange NOT in bar bands).
    swift_orange_mask = orange.copy()
    swift_orange_mask[bar_rows_mask] = 0

    # ------- 3. Black split: shadow (behind SWIFT) vs SUPPLY vs bar borders
    black_no_bars = black.copy()
    black_no_bars[bar_rows_mask] = 0

    # Determine SUPPLY band: it is the middle group of black connected
    # components that sit BELOW the SWIFT orange bounding rows.
    if swift_orange_mask.any():
        swift_rows = np.where((swift_orange_mask > 0).any(axis=1))[0]
        swift_y1 = int(swift_rows.max())
    else:
        swift_y1 = int(h * 0.55)

    # Any black CC whose top-y is BELOW swift_y1 - (small) is SUPPLY.
    supply_mask = np.zeros_like(black_no_bars)
    shadow_mask = np.zeros_like(black_no_bars)
    for x, y, ww, hh, area, m in _components_bboxes(black_no_bars):
        if area < 20:
            continue
        if y >= swift_y1 - 3 and hh < h * 0.35:
            supply_mask = np.maximum(supply_mask, m)
        else:
            shadow_mask = np.maximum(shadow_mask, m)

    # The SWIFT outline is thin black ring around orange; include it in shadow.
    swift_dilated = _dilate(swift_orange_mask, outline_dilation_px)
    outline_pixels = np.minimum(black_no_bars, swift_dilated)
    shadow_mask = np.maximum(shadow_mask, outline_pixels)

    # Build final sections.
    sections = SectionSet()
    sections.trace_order = [
        Section(
            name="swift-orange",
            mask=swift_orange_mask,
            fill_hex=ORANGE_FILL_HEX,
            z_index=40,
            description="Orange italic SWIFT letterforms (letters only).",
        ),
        Section(
            name="swift-shadow",
            mask=shadow_mask,
            fill_hex=BLACK_FILL_HEX,
            z_index=30,
            description="Hard drop shadow + thin outline behind SWIFT letters.",
        ),
        Section(
            name="supply-black",
            mask=supply_mask,
            fill_hex=BLACK_FILL_HEX,
            z_index=50,
            description="Black SUPPLY wordmark.",
        ),
        Section(
            name="bar-top",
            mask=_union([top_orange, top_border]),
            fill_hex=ORANGE_FILL_HEX,
            z_index=10,
            description="Top orange bar with thin black borders.",
        ),
        Section(
            name="bar-bottom",
            mask=_union([bot_orange, bot_border]),
            fill_hex=ORANGE_FILL_HEX,
            z_index=20,
            description="Bottom orange bar with thin black borders.",
        ),
    ]
    # Attach the black bar borders as sub-sections so compose can render them.
    sections.trace_order.append(
        Section(
            name="bar-top-border",
            mask=top_border,
            fill_hex=BLACK_FILL_HEX,
            z_index=11,
            description="Thin black outline on the top orange bar.",
        )
    )
    sections.trace_order.append(
        Section(
            name="bar-bottom-border",
            mask=bot_border,
            fill_hex=BLACK_FILL_HEX,
            z_index=21,
            description="Thin black outline on the bottom orange bar.",
        )
    )
    return sections


# ---------------------------------------------------------------------------
# Generic color-based decomposer (fallback)
# ---------------------------------------------------------------------------

def decompose_by_color(
    img: Image.Image,
    *,
    include: Sequence[str] = ("orange", "black"),
) -> SectionSet:
    """Fallback: one section per color group present in the raster."""
    masks = color_masks(img.convert("RGBA"))
    sections = SectionSet()
    z = 10
    for name in include:
        m = masks.get(name)
        if m is None or not m.any():
            continue
        fill = ORANGE_FILL_HEX if name == "orange" else BLACK_FILL_HEX
        sections.trace_order.append(
            Section(
                name=name,
                mask=_open_close(m, 1),
                fill_hex=fill,
                z_index=z,
                description=f"All {name} pixels grouped as one layer.",
            )
        )
        z += 10
    return sections
