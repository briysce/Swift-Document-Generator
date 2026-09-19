# Logo Vectorizer — Manual-quality + Sectional tracing

Two-flavour PNG → SVG vectorizer:

- **Wordmark pipeline** (legacy) — multi-backend race (OpenCV / potrace / vtracer
  / Inkscape) + optional AI advisors + hole-preserving evenodd paths. Great
  for single-color glyph rasters where every letter counter must stay open.

- **Manual-quality pipeline** (new default for logos) — analyses each
  raster individually, splits it into named *sections* (SWIFT-orange,
  drop shadow, SUPPLY, bars…), and traces every section with a
  Schneider-style least-squares cubic Bezier fitter that mimics the
  anchor-placement choices of a designer doing a manual trace.

## Install

```powershell
pip install -r tools/logo_vectorizer/requirements.txt
```

Optional: `winget install Inkscape.Inkscape` for the legacy Inkscape backend.

## CLI

### Manual-quality sectional (the "as a designer would trace it" path)

```powershell
python -m tools.logo_vectorizer `
  --input assets/brand/swift_supply_source.png `
  --output assets/brand/swift_supply_logo_document.svg `
  --sectional --decomposer swift-supply `
  --render-png assets/brand/swift_supply_logo_document.png `
  --render-width 2987 --render-background transparent `
  --sections-dir assets/brand/_document_sections
```

### Wordmark ensemble (legacy)

```powershell
python -m tools.logo_vectorizer --input assets/brand/swift_supply_logo_orange.png `
  --output out.svg --fill "#CE4E30" --qa
```

## Brand document logo regen

```powershell
python scripts/process_header_logo.py --document
```

Emits `assets/brand/swift_supply_logo_document.{svg,png}` and syncs the PNG
into `mobile/assets/images/` so the Flutter PDF builders pick it up.

## Manual-quality pipeline in detail

For every raster we receive, before touching a fitter we run
`analyze_raster(img)`:

1. **Palette** — dominant RGBA clusters (quantised to 16-step buckets) so
   anti-alias fringe pixels don't pollute the color list.
2. **Edge softness** — median transition width from Sobel magnitude;
   drives blur / threshold / upscale choice.
3. **Stroke width** — distance-transform of the foreground → picks
   `min_area` and blur radius that fit the geometry.
4. **Corner density** — RDP polygonalisation ratio; if the raster looks
   typographic we tighten corner-angle threshold and Bezier fit error.
5. **Symmetry** — IoU of the mask vs its H/V mirror; when strong,
   axis-snap tolerance is raised so straight strokes come out perfectly
   horizontal/vertical (helps logo bars).

The `RasterAnalysis` result seeds `ManualTraceConfig`. The tracer then:

1. **Extracts dense subpixel contours** via `RETR_CCOMP + CHAIN_APPROX_NONE`
   from a Gaussian-blurred + rethresholded mask upscaled 2×–6×.
2. **Detects corners** where the local turn angle exceeds threshold in
   *both* a small (jitter-sensitive) and a wider (semantics-preserving)
   window, then applies non-maximum suppression so a rounded corner
   doesn't turn into three anchors.
3. **Adds anchors at extrema** (tangent aligned with H/V) and at
   **inflection points** (curvature sign change). These are exactly the
   places a designer drops anchors — top/side of an arc, S-curve
   transitions.
4. **Coalesces collinear anchors** — any anchor that lies close to the
   chord between its two neighbours (and isn't itself a corner) is
   removed, so long flat strokes emit as a single line instead of many
   collinear H/V hops.
5. **Enforces max arc between anchors** so no single Bezier covers a run
   long enough for interior deviation to grow.
6. **Per-segment fitting**:
   - Straight test — max deviation from chord ≤ `straight_dev_px` →
     line, snapped to nearest H/V/±45° axis within `axis_snap_deg`.
   - Schneider least-squares cubic Bezier fit with tangent constraints;
     two Newton-Raphson reparameterisations if the initial fit misses
     `max_error_px`.
   - If the fit still fails, split at the max-error index and recurse.
7. **Emits compact SVG** with M/L/H/V/C/Z commands and `fill-rule="evenodd"`
   so holes/counters subtract correctly.

## Sectional decomposition

`decompose_swift_supply(img)` splits the full lockup into named layers:

| Section              | Contents                                              | z-index |
|----------------------|-------------------------------------------------------|---------|
| `bar-top`            | Top orange rectangle + thin black borders             | 10      |
| `bar-top-border`     | Just the thin black outline lines on the top bar      | 11      |
| `bar-bottom`         | Bottom orange rectangle + thin black borders          | 20      |
| `bar-bottom-border`  | Just the thin black outline lines on the bottom bar   | 21      |
| `swift-shadow`       | Hard drop shadow + thin black outline behind SWIFT    | 30      |
| `swift-orange`       | Italic SWIFT letterforms (orange fill only)           | 40      |
| `supply-black`       | Black SUPPLY wordmark                                 | 50      |

Regions are found by combining color masks (orange / black / white) with
horizontal-band position analysis (bars are the two rows with orange
density > 55 % of image width; SUPPLY is the black CCs below the SWIFT
band; SWIFT-shadow is everything else that isn't a bar or SUPPLY).

The order used for **tracing** matches the user-requested workflow
(1. SWIFT orange, 2. shadow, 3. SUPPLY, 4. bar top, 5. bar bottom). The
order used for **rendering** in the composed SVG is z-index — so the
shadow paints under the orange fills, the SUPPLY paints on top of the
white background, and bars sit safely at the extremes.

Generic fallback: `decompose_by_color(img, include=("orange", "black"))`
produces one section per color group. Add your own decomposer by writing
a function that returns a `SectionSet` — the composer, tracer, and CLI
work uniformly with any decomposition.

## Python API

```python
from PIL import Image
from tools.logo_vectorizer import (
    analyze_raster,
    decompose_swift_supply,
    vectorize_sectional,
    rasterize_svg,
)

img = Image.open("assets/brand/swift_supply_source.png").convert("RGBA")
sections = decompose_swift_supply(img)
result = vectorize_sectional(img, sections)

# `result.svg`         → the composed layered SVG (source of truth)
# `result.per_section` → list of SectionTrace (name, mask, d, contours…)
# `result.analysis`    → RasterAnalysis used to seed the fitter
```

Rasterize back to PNG (Chrome / cairosvg / transparent-via-difference):

```python
rasterize_svg(Path("...svg"), Path("...png"), width=2987, background="transparent")
```

## Notes

- The document logo (`swift_supply_logo_document.svg`) is the source of
  truth for print artwork. Its PNG derivative is what shipping / BOL /
  receiving generators load — Python and Flutter both fall back to the
  wordmark `swift_supply_logo_orange.png` when the document PNG is
  missing.
- Existing wordmark exports (`swift_supply_logo_orange.{png,svg}` and
  `swift_supply_logo_white.{png,svg}`) still ship — they're used for the
  app header and any tight lockup that can't accommodate the bars.
