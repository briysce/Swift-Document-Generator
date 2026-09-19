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


def _circle_snap(el: Element) -> str | None:
    """Emit an exact circle for an element a machine would have cut as one.

    Fits a circle to the element's boundary and asks whether what is left over
    is tremor. If so the intent was a circle, and a circle is what we emit —
    every radius identical, rather than a Bézier chain that remembers the shake.
    """
    from scipy import ndimage

    x0, y0, x1, y1 = el.bbox
    bw, bh = (x1 - x0 + 1), (y1 - y0 + 1)
    if bw < 8 or bh < 8:
        return None
    # A circle's bounding box is square; bail early otherwise.
    if abs(bw - bh) / float(max(bw, bh)) > 0.06:
        return None

    edge = el.mask & ~ndimage.binary_erosion(el.mask, np.ones((3, 3), dtype=bool))
    ys, xs = np.where(edge)
    if len(xs) < 24:
        return None
    cx, cy = float(xs.mean()), float(ys.mean())
    radii = np.hypot(xs - cx, ys - cy)
    r_mean = float(radii.mean())
    if r_mean <= 1e-6:
        return None
    # Order the boundary by angle so "consecutive" means adjacent on the rim;
    # otherwise the autocorrelation test reads raster order, which is meaningless.
    order = np.argsort(np.arctan2(ys - cy, xs - cx))
    if not _residual_is_tremor(radii[order] - r_mean, r_mean):
        return None
    return f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r_mean:.2f}"/>'


def _rect_snap(el: Element) -> str | None:
    """Emit an exact rectangle for an element that is one in all but noise.

    A bar traced as "nearly straight" still shows wobble at high zoom. When the
    element genuinely fills its bounding box, the honest reconstruction is an
    exact rectangle: mathematically straight edges rather than a Bézier
    approximation of them.
    """
    x0, y0, x1, y1 = el.bbox
    bw, bh = (x1 - x0 + 1), (y1 - y0 + 1)
    if bw < 4 or bh < 4:
        return None
    if el.area / float(bw * bh) < (1.0 - RECT_SNAP_TOLERANCE):
        return None
    return f'<rect x="{x0}" y="{y0}" width="{bw}" height="{bh}"/>'


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def idealize_layered(
    arr: np.ndarray,
    *,
    max_layers: int = 6,
    supersample: int = DEFAULT_SUPERSAMPLE,
    alphamax: float = DEFAULT_ALPHAMAX,
    opttolerance: float = DEFAULT_OPTTOLERANCE,
    turdsize: int = DEFAULT_TURDSIZE,
    denoise: int = DEFAULT_DENOISE,
    snap_primitives: bool = True,
) -> str | None:
    """Rebuild `arr` as a composition of individually traced, corrected elements."""
    if not have_potrace():
        return None
    els = elements_of(arr, max_layers=max_layers)
    if not els:
        return None

    h, w = arr.shape[:2]
    # Back to front: within a layer, bigger first so shadows sit under letters.
    els.sort(key=lambda e: (e.layer, -e.area))

    groups: list[str] = []
    for el in els:
        hexc = "#%02X%02X%02X" % el.colour
        eid = f"L{el.layer}-E{el.index}"

        if snap_primitives:
            prim = _circle_snap(el) or _rect_snap(el)
            if prim is not None:
                groups.append(f'<g id="{eid}" fill="{hexc}" stroke="none">{prim}</g>')
                continue

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
        # potrace emits supersampled coordinates (with its own flip transform
        # inside the group); scale the group back to source units.
        s = 1.0 / float(supersample)
        groups.append(
            f'<g id="{eid}" fill="{hexc}" stroke="none" '
            f'transform="scale({s:.6f})">{markup}</g>'
        )

    if not groups:
        return None
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">' + "".join(groups) + "</svg>"
    )


def idealize_rgba(arr: np.ndarray, **kw) -> str | None:
    """Alias for callers that do not care about the layering detail."""
    return idealize_layered(arr, **kw)


__all__ = [
    "Element",
    "have_potrace",
    "elements_of",
    "idealize_layered",
    "idealize_rgba",
]
