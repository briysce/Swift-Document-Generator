"""Build the multi-color orange Swift Supply logo with SUPPLY in black and a hard
black drop shadow behind SWIFT.

Brand asset naming (``assets/brand/``):
- ``swift_supply_logo_orange_solid.{png,svg}`` — archived all-orange wordmark
  (SWIFT + SUPPLY + bars in #CE4E30; no black shadow, no black SUPPLY, no
  outlines). Preserved for future use; not used on generated documents.
- ``swift_supply_logo_orange.{png,svg}`` — current document logo (shadow + black
  SUPPLY + thin black outlines around the orange bars and each SWIFT letter).
  Synced to ``mobile/assets/images/swift_supply_logo_orange.png``.

Reads the solid all-orange PNG, then:

1. Splits it into row bands (top bar / SWIFT / SUPPLY / bottom bar).
2. Keeps the bars in the app orange (#CE4E30) with a thin black outline drawn
   underneath (morphological dilate of the bar mask).
3. Recolors the SUPPLY rows to solid black (no outline).
4. Composites a hard-edge black drop shadow (offset bottom-right) behind SWIFT,
   draws a thin black outline (dilated SWIFT mask) so each letter reads with a
   clean stroke, then paints SWIFT in orange on top.

Writes the same PNG path back and regenerates the base64 embed-PNG SVG so the whole
document pipeline (PDFs, Flutter, previews) picks up the new artwork with no other
changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Tuple

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
ORANGE_SOLID_PNG = ROOT / "assets" / "brand" / "swift_supply_logo_orange_solid.png"
ORANGE_SOLID_SVG = ROOT / "assets" / "brand" / "swift_supply_logo_orange_solid.svg"
# Legacy alias kept in sync with ORANGE_SOLID_PNG for older scripts.
SILHOUETTE_PNG = ROOT / "assets" / "brand" / "swift_supply_logo_orange_silhouette.png"
ORANGE_PNG = ROOT / "assets" / "brand" / "swift_supply_logo_orange.png"
ORANGE_SVG = ROOT / "assets" / "brand" / "swift_supply_logo_orange.svg"
ORANGE_PREVIEW = ROOT / "assets" / "brand" / "swift_supply_logo_orange_preview.png"
ORANGE_CHAT = ROOT / "assets" / "brand" / "swift_supply_logo_orange_chat.png"
MOBILE_PNG = ROOT / "mobile" / "assets" / "images" / "swift_supply_logo_orange.png"

APP_ORANGE = (0xCE, 0x4E, 0x30)
BLACK = (0, 0, 0)

# Hard-edge shadow offset for SWIFT — bottom-right, ~8% of SWIFT letter height on
# the 910 px tall master. That matches the visual weight of the reference lockup
# without swallowing the letterforms.
SHADOW_DX = 28
SHADOW_DY = 28

# Thin black outline around the orange bars and each SWIFT letter. Implemented as
# a morphological dilation of the shape's alpha mask painted black under the
# orange fill, leaving a visible ring at the perimeter. 6 px on the 2987×910
# master reads as a clean logo outline (~1% of SWIFT letter height) — not a
# heavy border. SUPPLY has no outline (stays black), matching the brief.
OUTLINE_PX = 6

# Row-band detection thresholds tuned to the current 2987×910 silhouette but
# expressed as fractions so re-runs at different sizes still work.
BAR_COVERAGE = 0.85          # a row is a "bar" if ≥85% of the width is opaque
MIN_TEXT_COVERAGE = 0.02     # a row is a text row if it has any real ink

Row = int
Band = Tuple[Row, Row]


def _row_ink(px, width: int, height: int) -> List[int]:
    """Number of opaque pixels per row."""
    counts: List[int] = []
    for y in range(height):
        n = 0
        for x in range(width):
            if px[x, y][3] >= 16:
                n += 1
        counts.append(n)
    return counts


def _find_bars(row_ink: List[int], width: int) -> List[Band]:
    """Return (start, end_inclusive) for every solid horizontal bar band."""
    thresh = int(width * BAR_COVERAGE)
    bands: List[Band] = []
    start = None
    for y, n in enumerate(row_ink):
        is_bar = n >= thresh
        if is_bar and start is None:
            start = y
        elif not is_bar and start is not None:
            bands.append((start, y - 1))
            start = None
    if start is not None:
        bands.append((start, len(row_ink) - 1))
    return bands


def _find_text_bands(
    row_ink: List[int], width: int, exclude: Iterable[Band]
) -> List[Band]:
    """Contiguous runs of rows that carry ink but are not solid bars."""
    excl = list(exclude)
    thresh = max(1, int(width * MIN_TEXT_COVERAGE))
    bar_thresh = int(width * BAR_COVERAGE)
    bands: List[Band] = []
    start = None
    for y, n in enumerate(row_ink):
        in_excl = any(a <= y <= b for a, b in excl)
        is_ink = (n >= thresh) and (n < bar_thresh) and not in_excl
        if is_ink and start is None:
            start = y
        elif not is_ink and start is not None:
            bands.append((start, y - 1))
            start = None
    if start is not None:
        bands.append((start, len(row_ink) - 1))
    return bands


def _band_mask(src: Image.Image, y0: int, y1: int) -> Image.Image:
    """Full-canvas binary L mask (255 opaque, 0 clear) for rows ``y0..y1``."""
    w, h = src.size
    alpha = src.split()[3]
    band = alpha.crop((0, y0, w, y1 + 1)).point(lambda a: 255 if a >= 16 else 0)
    mask = Image.new("L", (w, h), 0)
    mask.paste(band, (0, y0))
    return mask


def _dilate(mask: Image.Image, radius: int) -> Image.Image:
    """Morphologically dilate a binary L mask by ``radius`` pixels."""
    if radius <= 0:
        return mask
    size = 2 * radius + 1
    return mask.filter(ImageFilter.MaxFilter(size))


def _translate(mask: Image.Image, dx: int, dy: int) -> Image.Image:
    """Shift a full-canvas L mask by (dx, dy), zero-padding the exposed edges."""
    if dx == 0 and dy == 0:
        return mask
    shifted = Image.new("L", mask.size, 0)
    shifted.paste(mask, (dx, dy))
    return shifted


def _paint_mask(
    dst: Image.Image,
    mask: Image.Image,
    color: Tuple[int, int, int],
) -> None:
    """Paint solid ``color`` (fully opaque) onto ``dst`` wherever ``mask`` > 0."""
    fill = Image.new("RGBA", dst.size, (*color, 255))
    dst.paste(fill, (0, 0), mask)


def build_shadowed_orange_logo(
    src_png: Path,
    dst_png: Path,
    *,
    shadow_dx: int = SHADOW_DX,
    shadow_dy: int = SHADOW_DY,
    outline_px: int = OUTLINE_PX,
) -> Tuple[Band, Band, List[Band]]:
    """Compose SWIFT (orange + hard shadow + outline) + SUPPLY (black) + bars
    (orange + outline)."""
    src = Image.open(src_png).convert("RGBA")
    w, h = src.size
    px = src.load()

    row_ink = _row_ink(px, w, h)
    bars = _find_bars(row_ink, w)
    if len(bars) < 2:
        raise RuntimeError(
            f"Expected two horizontal bars in {src_png}, found {len(bars)}: {bars}"
        )
    top_bar, bottom_bar = bars[0], bars[-1]
    text = _find_text_bands(row_ink, w, exclude=bars)
    if len(text) < 2:
        raise RuntimeError(
            f"Expected SWIFT and SUPPLY text bands in {src_png}, found {text}"
        )
    swift_band = text[0]
    supply_band = text[-1]

    print(f"  top bar    : rows {top_bar[0]}..{top_bar[1]}")
    print(f"  SWIFT band : rows {swift_band[0]}..{swift_band[1]}")
    print(f"  SUPPLY band: rows {supply_band[0]}..{supply_band[1]}")
    print(f"  bottom bar : rows {bottom_bar[0]}..{bottom_bar[1]}")
    print(f"  shadow     : dx={shadow_dx}, dy={shadow_dy}")
    print(f"  outline    : {outline_px} px (dilated black under orange fill)")

    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    top_mask = _band_mask(src, top_bar[0], top_bar[1])
    bottom_mask = _band_mask(src, bottom_bar[0], bottom_bar[1])
    swift_mask = _band_mask(src, swift_band[0], swift_band[1])
    supply_mask = _band_mask(src, supply_band[0], supply_band[1])

    # Bars: dilated black outline behind orange fill (top + bottom).
    _paint_mask(out, _dilate(top_mask, outline_px), BLACK)
    _paint_mask(out, _dilate(bottom_mask, outline_px), BLACK)
    _paint_mask(out, top_mask, APP_ORANGE)
    _paint_mask(out, bottom_mask, APP_ORANGE)

    # SWIFT: hard drop shadow (offset), then dilated black outline, then orange
    # fill. Outline sits under the fill so each letter reads with a clean stroke.
    _paint_mask(out, _translate(swift_mask, shadow_dx, shadow_dy), BLACK)
    _paint_mask(out, _dilate(swift_mask, outline_px), BLACK)
    _paint_mask(out, swift_mask, APP_ORANGE)

    # SUPPLY: solid black, no outline.
    _paint_mask(out, supply_mask, BLACK)

    dst_png.parent.mkdir(parents=True, exist_ok=True)
    out.save(dst_png, optimize=True)
    print(f"Wrote {dst_png} ({out.width}x{out.height})")
    return swift_band, supply_band, bars


def _write_preview(src_png: Path, dst_png: Path, target_width: int = 1200) -> None:
    img = Image.open(src_png).convert("RGBA")
    if img.width > target_width:
        scale = target_width / img.width
        img = img.resize(
            (target_width, max(1, round(img.height * scale))),
            Image.Resampling.LANCZOS,
        )
    dst_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst_png, optimize=True)
    print(f"Wrote {dst_png}")


def _ensure_solid_archive() -> Path:
    """Return the all-orange source PNG, recovering from legacy paths if needed."""
    if ORANGE_SOLID_PNG.is_file():
        return ORANGE_SOLID_PNG
    if SILHOUETTE_PNG.is_file():
        ORANGE_SOLID_PNG.write_bytes(SILHOUETTE_PNG.read_bytes())
        print(f"Archived solid -> {ORANGE_SOLID_PNG}")
        return ORANGE_SOLID_PNG
    # First shadow build — current orange PNG may still be all-orange.
    ORANGE_SOLID_PNG.write_bytes(ORANGE_PNG.read_bytes())
    print(f"Archived solid -> {ORANGE_SOLID_PNG}")
    return ORANGE_SOLID_PNG


def _sync_solid_svg(solid_png: Path) -> None:
    from tools.logo_vectorizer.embed import write_embedded_png_svg

    w, hh = write_embedded_png_svg(solid_png, ORANGE_SOLID_SVG)
    print(f"Wrote {ORANGE_SOLID_SVG} (embed-png {w}x{hh})")
    SILHOUETTE_PNG.write_bytes(solid_png.read_bytes())
    print(f"Synced legacy -> {SILHOUETTE_PNG}")


def _sync_svg_and_previews(src_png: Path) -> None:
    from tools.logo_vectorizer.embed import write_embedded_png_svg

    w, hh = write_embedded_png_svg(src_png, ORANGE_SVG)
    print(f"Wrote {ORANGE_SVG} (embed-png {w}x{hh})")

    _write_preview(src_png, ORANGE_PREVIEW)
    _write_preview(src_png, ORANGE_CHAT)

    if MOBILE_PNG.parent.exists():
        MOBILE_PNG.write_bytes(src_png.read_bytes())
        print(f"Synced       -> {MOBILE_PNG}")


def main() -> int:
    import sys

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if "--overwrite-vector-master" not in sys.argv:
        print("The Swift brand logos are exported from the vector master by "
              "scripts/export_swift_app_logos.py. This script rebuilds them from a raster "
              "and would replace that vector; pass --overwrite-vector-master to do it anyway.", file=sys.stderr)
        return 2

    solid = _ensure_solid_archive()
    _sync_solid_svg(solid)
    build_shadowed_orange_logo(solid, ORANGE_PNG)
    _sync_svg_and_previews(ORANGE_PNG)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
