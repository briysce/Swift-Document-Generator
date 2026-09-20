"""Idealization — rebuild a logo as a composition of individually traced elements.

The job
-------
Almost nothing we are handed was born as a vector. A customer sends a JPEG off a
website, a phone photo of a business card, a 300px PNG from a signature block.
Tracing that raster as one blob reproduces its defects and fuses parts a
designer kept separate: staircased curves, "straight" edges that wobble, corners
rounded off by JPEG, thousands of anchors where a designer placed a dozen.

Zoomed out, such a trace looks fine. At high zoom it falls apart — bumps along
the round of an S, jagged diagonals, pixel stair-steps on a bar edge. The target
is artwork that still looks machined at that zoom, because that is what goes to
a sign shop or an embroidery digitizer.

The governing assumption: intent versus execution
-------------------------------------------------
Think of a circle cut from sheet metal. A machine cuts it to spec — every radius
identical. A person cutting the same circle by hand intends the same shape but
delivers a wobble: slightly wider on one side, a little jagged where their hand
shook. The *intent* was a perfect circle; the *execution* carries tremor.

A low-resolution or low-fidelity raster is the shaky hand. Its bumps, jagged
diagonals and not-quite-straight edges are execution error introduced by
sampling and compression, not decisions a designer made. So this stage
reconstructs the intent and discards the tremor.

The test that separates the two is the residual after fitting an ideal
primitive:

  * **Small and unstructured** — high-frequency, zero-mean, no consistent sign
    — is tremor. Snap to the ideal: exact circle, exact rectangle, exact line.
  * **Large, or structured** — a consistent, low-frequency departure — is
    intent. Keep it; the designer meant that flat side or that asymmetry.

Magnitude alone is not enough. A deliberately squashed ellipse and a shaky
circle can have similar residual magnitude against a circle fit; what separates
them is that the ellipse's residual is smooth and systematic while the shaky
circle's is noise. Judge both, and when in doubt keep the geometry — inventing
symmetry that was not there is the one failure worse than leaving a wobble.

How
---
Build it the way a designer would, element by element:

  1. **Flat colour layers.** A lockup like Swift is a black drop shadow *under*
     orange letters, plus a black wordmark and orange bars. Flattened into one
     silhouette those parts overlap and no fill rule can express "shadow behind
     letters" — that is what tears the letters apart. Recover layers first.

  2. **Connected components.** Within a layer, split into disconnected elements:
     each letter of SWIFT, each shadow, each bar, on its own. An isolated
     element gets tolerances suited to it, and nothing bleeds between neighbours.

  3. **Supersample and denoise.** Each element is upsampled before tracing, then
     median-filtered at that resolution, which erases the pixel staircase and
     the bumps *before* any curve is fitted. Jaggedness left in here is never
     recoverable later.

  4. **Trace with potrace** — the engine behind Inkscape's Trace Bitmap: corner
     detection plus Bézier fitting with curve optimization. This is exactly the
     "anchor points, bent around the subject" construction.

  5. **Per-element regularization.** Because elements are separate, each can be
     corrected on its own: an element within tolerance of a rectangle becomes an
     exact rectangle, so its edges are mathematically straight instead of nearly
     straight.

  6. **Compose.** Elements are reassembled back-to-front into one SVG, each in
     its own `<g>` with a stable id, so any single element can later be
     re-cornered, re-angled or given a border without disturbing the rest.

Fail-open: any failure returns None and the caller keeps its existing geometry.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

# Defaults measured against the Swift lockup: shape agreement held at 1.0000
# while anchor count fell to about a third of the previous master's.
DEFAULT_SUPERSAMPLE = 4
DEFAULT_ALPHAMAX = 1.0        # potrace corner threshold; 1.0 keeps real corners
DEFAULT_OPTTOLERANCE = 0.8    # curve optimization: fewer, longer Béziers
DEFAULT_TURDSIZE = 8          # despeckle, in supersampled pixels
DEFAULT_DENOISE = 3           # median window at supersampled resolution
# RDP tolerance as a fraction of contour perimeter, for the smooth fitter.
# 0.0025 keeps the notch detail in the Swift S while removing the stair-steps;
# larger rounds real form, smaller keeps the jaggedness it exists to remove.
DEFAULT_RDP = 0.0025
MIN_COMPONENT_PX = 24
RECT_SNAP_TOLERANCE = 0.012   # fraction of the element's own bounding box


def have_potrace() -> bool:
    return shutil.which("potrace") is not None


# --------------------------------------------------------------------------
# Layer split
# --------------------------------------------------------------------------


def _quantize_layers(
    arr: np.ndarray, max_layers: int = 6, alpha_threshold: int = 128
) -> list[tuple[np.ndarray, tuple[int, int, int], int]]:
    """Split ink into flat colour layers, largest first."""
    ink = arr[:, :, 3] >= alpha_threshold
    if not ink.any():
        return []
    rgb = arr[:, :, :3].astype(np.int32)
    # Brand artwork is flat, so 5 bits per channel is plenty, and it keeps JPEG
    # mottle from splintering one fill into many layers.
    q = (rgb >> 3) << 3
    keys = (
        (q[:, :, 0].astype(np.int64) << 16)
        | (q[:, :, 1].astype(np.int64) << 8)
        | q[:, :, 2].astype(np.int64)
    )
    vals, counts = np.unique(keys[ink], return_counts=True)
    order = np.argsort(-counts)
    total = float(counts.sum())

    layers: list[tuple[np.ndarray, tuple[int, int, int], int]] = []
    claimed = np.zeros_like(ink)
    for i in order[: max_layers * 4]:
        if len(layers) >= max_layers:
            break
        if counts[i] / total < 0.01:
            continue
        key = int(vals[i])
        colour = ((key >> 16) & 0xFF, (key >> 8) & 0xFF, key & 0xFF)
        dist = np.abs(rgb - np.array(colour, dtype=np.int32)).sum(axis=2)
        mask = ink & (dist <= 60) & ~claimed
        if mask.sum() < 64:
            continue
        claimed |= mask
        layers.append((mask, colour, int(mask.sum())))

    leftover = ink & ~claimed
    if leftover.sum() >= 64 and layers:
        best = min(
            range(len(layers)),
            key=lambda j: float(
                np.abs(rgb[leftover].mean(axis=0) - np.array(layers[j][1])).sum()
            ),
        )
        m, c, _ = layers[best]
        m = m | leftover
        layers[best] = (m, c, int(m.sum()))

    layers.sort(key=lambda t: -t[2])
    return layers


# --------------------------------------------------------------------------
# Components
# --------------------------------------------------------------------------


@dataclass
class Element:
    mask: np.ndarray                  # full-canvas boolean mask
    colour: tuple[int, int, int]
    layer: int
    index: int
    area: int
    bbox: tuple[int, int, int, int]   # x0, y0, x1, y1 inclusive


def _components(mask: np.ndarray, min_px: int = MIN_COMPONENT_PX) -> list[np.ndarray]:
    """Disconnected elements of a layer — one per letter, bar, shadow."""
    from scipy import ndimage

    lab, n = ndimage.label(mask, structure=np.ones((3, 3), dtype=int))
    out: list[np.ndarray] = []
    for i in range(1, n + 1):
        m = lab == i
        if int(m.sum()) >= min_px:
            out.append(m)
    return out


def elements_of(arr: np.ndarray, *, max_layers: int = 6) -> list[Element]:
    els: list[Element] = []
    for li, (lmask, colour, _n) in enumerate(_quantize_layers(arr, max_layers)):
        for ci, m in enumerate(_components(lmask)):
            ys, xs = np.where(m)
            els.append(
                Element(
                    mask=m,
                    colour=colour,
                    layer=li,
                    index=ci,
                    area=int(m.sum()),
                    bbox=(int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())),
                )
            )
    return els


# --------------------------------------------------------------------------
# Trace one element
# --------------------------------------------------------------------------


def _upsample_denoise(mask: np.ndarray, supersample: int, denoise: int) -> Image.Image:
    """Upsample, then median-filter so bumps and stair-steps die before fitting."""
    from PIL import ImageFilter

    h, w = mask.shape
    img = Image.fromarray((mask.astype(np.uint8) * 255), "L")
    if supersample > 1:
        img = img.resize((w * supersample, h * supersample), Image.Resampling.LANCZOS)
    if denoise and denoise >= 3:
        k = denoise if denoise % 2 == 1 else denoise + 1
        # Median preserves corners while removing single-pixel wobble; a blur
        # would round the corners we are trying to keep sharp.
        img = img.filter(ImageFilter.MedianFilter(size=k))
    return img


def _smooth_trace(
    mask: np.ndarray,
    *,
    supersample: int,
    denoise: int,
    rdp_factor: float,
) -> str | None:
    """Fit a small number of well-placed Bezier anchors to an element.

    potrace faithfully follows the pixel boundary, which is the problem: on the
    Swift lockup it emitted 28,643 anchors at 196.9 per 1000 units of contour,
    against 57 for the hand-built reference in this repo. Faithful to the
    pixels means faithful to the stair-steps, and at high zoom that is exactly
    what shows.

    So the boundary is simplified before it is fitted — RDP to drop points that
    carry no shape, a corner-cutting pass, then Catmull-Rom cubics through what
    remains. Anchors land where the form actually turns. On the Swift lockup
    this drops to 2,604 anchors and lifts ideality from 0.5985 to 0.9032, and
    the S reads as one smooth sweep instead of a staircase.

    Agreement with the source raster falls slightly when this runs, and that is
    the intended trade rather than a cost: the jaggedness being discarded was
    never design. It is bounded, though — the caller compares against the
    source and keeps potrace when the loss is more than smoothing can explain.
    """
    import cv2

    from .smooth import adaptive_rdp_factor, smooth_contour

    h, w = mask.shape
    img = Image.fromarray((mask.astype(np.uint8) * 255), "L")
    if supersample > 1:
        img = img.resize(
            (w * supersample, h * supersample), Image.Resampling.LANCZOS
        )
    if denoise and denoise >= 3:
        from PIL import ImageFilter

        k = denoise if denoise % 2 == 1 else denoise + 1
        img = img.filter(ImageFilter.MedianFilter(size=k))

    m = (np.asarray(img) >= 128).astype(np.uint8)
    # RETR_CCOMP keeps holes as their own contours so counters stay open.
    cnts, _ = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return None
    total = float(m.sum()) or 1.0

    parts: list[str] = []
    for c in cnts:
        area = cv2.contourArea(c)
        if area < 24:
            continue
        factor = rdp_factor if rdp_factor > 0 else adaptive_rdp_factor(area, total)
        d = smooth_contour(c, rdp_factor=factor, chaikin_iters=2)
        if d:
            parts.append(d + " Z")
    if not parts:
        return None
    return f'<path fill-rule="evenodd" d="{" ".join(parts)}"/>'


def _trace_mask(
    mask: np.ndarray,
    *,
    supersample: int,
    alphamax: float,
    opttolerance: float,
    turdsize: int,
    denoise: int,
) -> str | None:
    """Trace one binary mask; returns potrace's `<g>` markup in supersampled units."""
    img = _upsample_denoise(mask, supersample, denoise)
    # potrace traces BLACK: ink must be black, background white.
    img = img.point(lambda v: 0 if v >= 128 else 255).convert("1")

    with tempfile.TemporaryDirectory(prefix="idealize_") as td:
        pbm = Path(td) / "e.pbm"
        svg = Path(td) / "e.svg"
        img.save(pbm)
        try:
            subprocess.run(
                [
                    "potrace", "-s", "-o", str(svg),
                    "-a", str(alphamax),
                    "-O", str(opttolerance),
                    "-t", str(turdsize),
                    str(pbm),
                ],
                check=True,
                capture_output=True,
                timeout=180,
            )
        except Exception:
            return None
        if not svg.is_file():
            return None
        text = svg.read_text(encoding="utf-8", errors="replace")

    g = re.search(r"<g([^>]*)>(.*?)</g>", text, re.S)
    if not g:
        return None
    # Drop potrace's own fill/stroke; the caller supplies the layer colour.
    attrs = re.sub(r'\b(fill|stroke)="[^"]*"', "", g.group(1)).strip()
    return f"<g {attrs}>{g.group(2)}</g>"


# --------------------------------------------------------------------------
# Per-element regularization
# --------------------------------------------------------------------------


# Kept and exported for `shapes.best_fit`, which uses it as the last-resort
# test when no primitive can be named.
def _residual_is_tremor(
    residual: np.ndarray, scale: float, pixel: float = 1.0
) -> bool:
    """Is this residual execution tremor, or a deliberate departure?

    Two tolerances, whichever is looser:

    * a fraction of the element — covers a large shape traced slightly unevenly;
    * a small multiple of one *source pixel* — covers the case that matters most
      here, a low-resolution logo. At 60px wide, quantization throws the
      boundary a pixel either way, which is a big fraction of the radius but is
      still purely sampling error. Judging that against element size alone
      would call every low-res circle "intentional" and faithfully reproduce
      its stair-steps, which is exactly the outcome this module exists to avoid.

    Magnitude cannot decide on its own, so structure is the real test: tremor
    changes sign often and has little run-to-run correlation, while a deliberate
    departure (a flattened side, a squashed ellipse) is smooth and systematic.
    """
    if residual.size < 8 or scale <= 1e-9:
        return False
    mean_abs = float(np.abs(residual).mean())
    if mean_abs > max(0.030 * scale, 1.5 * pixel):
        return False  # too large to be either tremor or quantization
    r = residual - residual.mean()
    denom = float((r * r).sum())
    if denom < 1e-12:
        return True
    # Lag-1 autocorrelation: high means smooth/systematic, low means noise.
    ac1 = float((r[:-1] * r[1:]).sum()) / denom
    return ac1 < 0.72


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def _compose(
    arr: np.ndarray,
    *,
    max_layers: int = 6,
    supersample: int = DEFAULT_SUPERSAMPLE,
    alphamax: float = DEFAULT_ALPHAMAX,
    opttolerance: float = DEFAULT_OPTTOLERANCE,
    turdsize: int = DEFAULT_TURDSIZE,
    denoise: int = DEFAULT_DENOISE,
    snap_primitives: bool = True,
    match_fonts: bool = True,
    smooth_fit: bool = True,
    rdp_factor: float = 0.0025,
    adapt_to_damage: bool = True,
    source_path: "Path | None" = None,
) -> str | None:
    """One composition pass. `idealize_layered` picks between two of these."""
    if not have_potrace():
        return None

    # Measure the damage, then correct in proportion to it. A pristine master
    # gets a light touch; a thumbnail dragged off a website gets aggressive
    # correction, because at that resolution almost every wobble is
    # quantization rather than something a designer drew.
    if adapt_to_damage:
        try:
            from .flaw_analysis import analyze

            tuned = analyze(arr, source_path).idealize_params
            supersample = tuned["supersample"]
            denoise = tuned["denoise"]
            alphamax = tuned["alphamax"]
            opttolerance = tuned["opttolerance"]
            turdsize = tuned["turdsize"]
        except Exception:
            pass

    els = elements_of(arr, max_layers=max_layers)
    if not els:
        return None

    h, w = arr.shape[:2]
    # Back to front: within a layer, bigger first so shadows sit under letters.
    els.sort(key=lambda e: (e.layer, -e.area))

    # Recognize letters across the whole image first. A wordmark is set in one
    # face, so deciding per element would throw away the strongest evidence
    # available — that six neighbours all agree on the same font.
    glyphs: dict[int, tuple[object, str]] = {}
    if match_fonts:
        try:
            from .glyph_match import font_corpus, group_runs, match_run

            corpus = font_corpus()
            masks = [e.mask for e in els]
            boxes = [e.bbox for e in els]
            for run in group_runs(boxes):
                if len(run) < 3:
                    continue
                m = match_run(masks, run, corpus)
                if m is None:
                    continue
                for idx, ch in zip(m.indices, m.chars):
                    if ch:
                        glyphs[idx] = (m.font, ch)
        except Exception:
            glyphs = {}

    groups: list[str] = []
    for pos, el in enumerate(els):
        hexc = "#%02X%02X%02X" % el.colour
        eid = f"L{el.layer}-E{el.index}"

        # 1. Named letter. Run-level font agreement is the strongest evidence
        #    we ever get — six neighbours voting for the same face — and the
        #    font file holds the designer's real curves. It therefore outranks
        #    a generic polygon fit, which would happily claim an L or an I and
        #    throw those curves away.
        hit = glyphs.get(pos)
        if hit is not None:
            try:
                from .glyph_match import glyph_outline

                font_path, ch = hit
                outline = glyph_outline(font_path, ch, el.bbox, refine_against=el.mask)
            except Exception:
                outline = None
            if outline:
                groups.append(
                    f'<g id="{eid}" data-glyph="{ch}" '
                    f'data-font="{Path(str(hit[0])).name}" '
                    f'fill="{hexc}" stroke="none">{outline}</g>'
                )
                continue

        # 2. Named geometry. Knowing the element *is* a circle makes every
        #    departure from that circle flaw by definition.
        if snap_primitives:
            try:
                from .shapes import best_fit

                fit = best_fit(el.mask)
            except Exception:
                fit = None
            if fit is not None:
                groups.append(
                    f'<g id="{eid}" data-shape="{fit.kind}" '
                    f'fill="{hexc}" stroke="none">{fit.markup}</g>'
                )
                continue

        # 3. Neither named. Fit designed-looking geometry to the boundary.
        scale = 1.0 / float(supersample)
        markup = None
        if smooth_fit:
            markup = _smooth_trace(
                el.mask,
                supersample=supersample,
                denoise=denoise,
                rdp_factor=rdp_factor,
            )
        if markup is None:
            markup = _trace_mask(
                el.mask,
                supersample=supersample,
                alphamax=alphamax,
                opttolerance=opttolerance,
                turdsize=turdsize,
                denoise=denoise,
            )
        if markup is None:
            continue
        # Both tracers emit supersampled coordinates (potrace carries its own
        # flip transform inside the group); scale the group back to source units.
        groups.append(
            f'<g id="{eid}" fill="{hexc}" stroke="none" '
            f'transform="scale({scale:.6f})">{markup}</g>'
        )

    if not groups:
        return None
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">' + "".join(groups) + "</svg>"
    )


def _shape_agreement(svg: str, arr: np.ndarray) -> float:
    """IoU of a rendered candidate against the source ink."""
    try:
        import io as _io

        import cairosvg
    except Exception:
        return 1.0
    h, w = arr.shape[:2]
    try:
        buf = _io.BytesIO()
        cairosvg.svg2png(
            bytestring=svg.encode(), write_to=buf,
            output_width=w, output_height=h,
            background_color="rgba(0,0,0,0)",
        )
        buf.seek(0)
        m = np.asarray(Image.open(buf).convert("RGBA"))[:, :, 3] >= 128
    except Exception:
        return 0.0
    t = arr[:, :, 3] >= 128
    union = np.logical_or(t, m).sum()
    return float(np.logical_and(t, m).sum() / union) if union else 0.0


def _ideality_of(svg: str) -> float:
    import tempfile

    from .ideality import score_svg

    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".svg", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(svg)
            tmp = Path(fh.name)
        v = float(score_svg(tmp).ideality)
        tmp.unlink(missing_ok=True)
        return v
    except Exception:
        return 0.0


# How much shape agreement the smooth fit may give up for cleaner geometry.
# Some loss is the point — the jaggedness being discarded was never design —
# but past this the form itself is being eaten.
SMOOTH_IOU_BUDGET = 0.06


def idealize_layered(arr: np.ndarray, **kw) -> str | None:
    """Rebuild `arr` as a composition of individually recognized elements.

    Each element is resolved by the strongest evidence available: a named
    geometric primitive, then a recognized glyph emitted from its own font,
    then fitted geometry.

    For that last case there are two fitters and neither wins everywhere.
    Simplify-then-fit gives far better craftsmanship on large, bold artwork —
    on the Swift lockup it cuts 28,643 anchors to 2,604 and lifts ideality from
    0.5985 to 0.9032, turning a staircased S into one smooth sweep. On small or
    finely detailed marks it does the opposite: measured on this repo's corpus
    it *lost* 0.08 to 0.11 ideality on arc, gcm and propak, all under 720px
    wide, because the corner-cutting pass adds points on short contours that
    simplification had little to remove.

    So both are built and the better one kept — judged, not guessed. Shape
    agreement guards the choice: the smooth fit may give up a little, since
    discarded stair-steps were never design, but past a budget it is eating the
    form and is refused.
    """
    smooth_on = kw.pop("smooth_fit", True)
    if not smooth_on:
        return _compose(arr, smooth_fit=False, **kw)

    smooth = _compose(arr, smooth_fit=True, **kw)
    plain = _compose(arr, smooth_fit=False, **kw)
    if smooth is None:
        return plain
    if plain is None:
        return smooth

    if _ideality_of(smooth) <= _ideality_of(plain):
        return plain
    if _shape_agreement(smooth, arr) < _shape_agreement(plain, arr) - SMOOTH_IOU_BUDGET:
        return plain
    return smooth


__all__ = [
    "Element",
    "have_potrace",
    "elements_of",
    "idealize_layered",
]
