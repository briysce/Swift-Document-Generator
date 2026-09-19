"""
Unified restoration + vectorization engine (Swift Document Generator + briyszier).

This is the single orchestration map both apps share, and the one entry point
worth calling: `reconstruct()`.

What this engine is for
-----------------------
We are not going vector to raster. We are going raster to vector, and the output
is not a copy of the input's pixels — it is the artwork the input is a degraded
rendering *of*. A customer sends a JPEG off a website, a phone photo of a
business card, a 300px PNG from a signature block. Tracing that faithfully
reproduces its defects. Zoomed out it looks fine; at high zoom the round of an S
has bumps, diagonals are jagged, a bar edge stair-steps. The target is artwork
that still looks machined at that zoom, because that is what goes to a sign shop
or an embroidery digitizer.

Intent versus execution
-----------------------
Think of a circle cut from sheet metal. A machine cuts every radius identical. A
person cutting by hand intends the same circle but delivers a wobble. A
low-resolution raster is the shaky hand: its imperfections are execution error
introduced by sampling and compression, not decisions a designer made. So the
engine reconstructs intent and discards tremor.

It establishes intent from the strongest evidence available, in this order,
because each step knows more than the one after it:

  1. **A named letter.** If a run of elements agrees on one font, the font file
     holds the designer's actual curves. Nothing inferred can beat that.
  2. **A named shape.** If an element *is* a circle, every departure from that
     circle is flaw by definition — no statistics required.
  3. **Residual character.** Otherwise, judge the leftover after fitting: small
     and unstructured is tremor, large or systematic is intent.

Composition
-----------
Built the way a designer builds: flat colour layers (a drop shadow sits *under*
the letters, and no fill rule can express that from a flattened silhouette),
then connected components so each letter, bar and shadow is handled alone, then
reassembled back-to-front with stable ids so any single element can be corrected
later without disturbing the rest.

On borrowed ideas
-----------------
Techniques are taken as principles and implemented natively, not bolted on as
whole foreign packages. What was measured and kept:

  * OpenCV — contours, moments, minAreaRect, polygon approximation. The backbone
    of `shapes`.
  * RANSAC consensus (pyransac3d) — a mean-radius fit is least-squares, so every
    outlier pulls it. On damaged artwork RANSAC cut circle radius error from
    7.32px to 0.45px and centre error from 4.34px to 1.50px, and is identical on
    clean input.
  * potrace — corner detection plus Bézier fitting with curve optimization; the
    same engine behind Inkscape's Trace Bitmap.
  * Google Fonts — you can only recognize a face you have. `font_fetch` pulls a
    real corpus; it lifted the Swift SUPPLY run from 0.608 to 0.851.
  * Error Level Analysis (Forensically) — recompress and difference.
  * ExifTool — how the file was actually produced.

What was measured and rejected, deliberately:

  * BRISQUE (imquality) — trained on natural photographs; on this repo's own
    anchors it scores the clean logo 171.46 and the degraded ones 93.68/96.88,
    ranking pristine artwork as the worst. A backwards signal is worse than
    none. Reachable via `flaw_analysis.brisque_score` for comparison only.
  * YOLOv8 / Ultralytics — a COCO-pretrained detector cannot find circles,
    rectangles or glyphs without training. 1.2GB of torch for no gain here.
  * LogoGuard — a fake-logo classifier. Its task is brand authenticity, not
    raster reconstruction.
  * Logodetect — useful in principle for locating a logo inside a photo, but it
    ships no weights (its `models/` is empty) and needs torch. Wired fail-open
    in `flaw_analysis.detect_logo_regions` for a checkout that has them.

Legacy paths (still live)
-------------------------
  1. Prepare / knockout — `logo_raster_finish.prepare_for_engine`
  2. Optional raster polish — classical Gigapixel / Upscayl / Remacri /
     UltraSharp-inspired OpenCV, then Real-ESRGAN family, then optional
     GFPGAN / InstructIR / DeOldify. Gate: ink IoU vs prepared >= 0.92.
  3. Sectional Bezier (DejaType-style anchors) / VTracer + Inkscape ensemble
  4. Real-ESRGAN SR fallback for photo-like sources
  5. Optional Gemini redraw (opt-in; token exhaustion fails open)

Every stage fails open: a missing optional tool degrades the result, never the
run, and AI quota exhaustion can never weaken the local pipeline. SVG to PNG
uses cairosvg then PyMuPDF; Chrome is last-resort only (LOGO_NO_CHROME=1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class EngineCapabilities:
    """What this checkout can actually run right now."""

    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.details)


def probe_capabilities() -> EngineCapabilities:
    from .raster_polish import describe_available_backends

    caps = describe_available_backends()
    for name, probe in (
        ("vtracer", lambda: __import__("vtracer")),
        ("cairosvg", lambda: __import__("cairosvg")),
        ("scipy", lambda: __import__("scipy")),
        ("skimage", lambda: __import__("skimage")),
        ("fonttools", lambda: __import__("fontTools")),
        ("pyransac3d", lambda: __import__("pyransac3d")),
    ):
        try:
            probe()
            caps[name] = True
        except Exception:
            caps[name] = False

    try:
        from .inkscape import find_inkscape

        caps["inkscape"] = find_inkscape() is not None
    except Exception:
        caps["inkscape"] = caps.get("inkscape", False)
    try:
        import pymupdf  # noqa: F401

        caps["pymupdf"] = True
    except Exception:
        try:
            import fitz  # noqa: F401

            caps["pymupdf"] = True
        except Exception:
            caps["pymupdf"] = False

    try:
        from .idealize import have_potrace

        caps["potrace"] = have_potrace()
    except Exception:
        caps["potrace"] = False

    import shutil

    caps["exiftool"] = shutil.which("exiftool") is not None
    caps["imagemagick"] = shutil.which("convert") is not None

    try:
        from .glyph_match import font_corpus

        caps["font_corpus_size"] = len(font_corpus())
    except Exception:
        caps["font_corpus_size"] = 0

    caps["sectional_bezier"] = True
    caps["chrome_optional_last_resort"] = True
    return EngineCapabilities(details=caps)


def pipeline_stages() -> list[str]:
    return [
        "measure_damage",
        "prepare_knockout",
        "optional_raster_polish",
        "split_colour_layers",
        "split_connected_elements",
        "recognize_glyph_runs",
        "recognize_geometric_primitives",
        "trace_and_regularize_remainder",
        "compose",
        "legacy_sectional_or_ensemble",
        "realesrgan_family_sr",
        "lanczos_conservator",
        "optional_gemini_opt_in",
    ]


@dataclass
class Reconstruction:
    svg: str | None
    degradation: float
    ideality: float
    elements: int
    glyphs: int
    shapes: int
    report: dict = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "degradation": round(self.degradation, 4),
            "ideality": round(self.ideality, 4),
            "elements": self.elements,
            "glyphs": self.glyphs,
            "shapes": self.shapes,
            **self.report,
        }


def reconstruct(
    arr: np.ndarray,
    *,
    source_path: Path | None = None,
    match_fonts: bool = True,
    snap_primitives: bool = True,
    adapt_to_damage: bool = True,
    compact_layers: bool = True,
    use_memory: bool = False,
    store_memory: bool = False,
) -> Reconstruction:
    """Rebuild `arr` as the vector a designer would have drawn.

    Measures the damage, sets correction strength from it, resolves every
    element by the strongest evidence available, and composes the result. Fails
    open: on any failure the SVG is None and the caller keeps its own geometry.
    """
    from .flaw_analysis import analyze
    from .idealize import idealize_layered
    from .ideality import score_svg

    try:
        damage = analyze(arr, source_path)
    except Exception:
        damage = None

    # Have we already solved this mark? Only reuse a stored answer that beats
    # what we are about to produce; recall must never lower quality.
    #
    # OFF BY DEFAULT. A full-corpus sweep caught the first version returning
    # PROPAK's artwork for gcm and swift_orange inputs, because heavy
    # degradation collapses different lockups into similar silhouettes. The
    # descriptor and gating have since been rebuilt, but recall stays opt-in
    # until a cross-brand test proves zero false positives on a given corpus —
    # emitting another company's logo is not a bug you ship and fix later.
    recalled = None
    if use_memory:
        try:
            from .memory import recall

            recalled = recall(arr)
        except Exception:
            recalled = None

    try:
        svg = idealize_layered(
            arr,
            match_fonts=match_fonts,
            snap_primitives=snap_primitives,
            adapt_to_damage=adapt_to_damage,
            source_path=source_path,
        )
    except Exception:
        svg = None

    # Layer budget: drop anything that does not pay for itself, then settle
    # placement against the source. Reverts itself if it would cost agreement.
    if svg and compact_layers:
        try:
            from .layerwise import compact

            res = compact(svg, arr)
            svg = res.svg
        except Exception:
            pass

    ideality = 0.0
    if svg:
        import tempfile

        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".svg", delete=False, encoding="utf-8"
            ) as fh:
                fh.write(svg)
                tmp = Path(fh.name)
            ideality = float(score_svg(tmp).ideality)
            tmp.unlink(missing_ok=True)
        except Exception:
            ideality = 0.0

    if recalled is not None and recalled.ideality > ideality:
        svg, ideality = recalled.svg, recalled.ideality
    elif store_memory and svg:
        try:
            from .memory import remember

            remember(arr, svg, ideality, source=str(source_path or ""))
        except Exception:
            pass

    return Reconstruction(
        svg=svg,
        degradation=float(damage.degradation) if damage else 0.0,
        ideality=ideality,
        elements=(svg.count('<g id="') if svg else 0),
        glyphs=(svg.count("data-glyph=") if svg else 0),
        shapes=(svg.count("data-shape=") if svg else 0),
        report=(damage.as_dict() if damage else {}),
    )


__all__ = [
    "EngineCapabilities",
    "Reconstruction",
    "probe_capabilities",
    "pipeline_stages",
    "reconstruct",
]
