"""
Unified restoration + vectorization engine (Swift Document Generator + briyszier).

This is the single orchestration map both apps share. Callers pick a facade
(`logo_vectorize.convert`, `logo_restorer.restore`, `polish_raster`,
`vectorize_ensemble`, `vectorize_sectional`) — they all compose these stages.

Pipeline (every stage fail-opens; AI out-of-tokens never blocks local work):

  1. Prepare / knockout
       logo_raster_finish.prepare_for_engine

  2. Optional hierarchical raster polish (low-res / soft imports)
       classical Gigapixel / Upscayl / Remacri / UltraSharp-inspired OpenCV
       → Real-ESRGAN family weights (Upscayl .pth in .cache/realesrgan)
       → optional GFPGAN / InstructIR / DeOldify / Upscayl CLI
       Honesty gate: ink IoU vs prepared ≥ 0.92 or discard polish.

  3. Vectorize (flat / low-color lockups)
       a. Sectional Bezier (DejaType-style per-element anchors: corners,
          tangent extrema, inflections) via decompose_* + manual_trace
          — restore path: scoop_residual=False, cairosvg rasterize, IoU≥0.90
          — document path: scoop_residual=True, recreate tuning, high IoU
       b. Ensemble race led by VTracer + Inkscape, then OpenCV / potrace
       c. Lanczos conservator if fidelity rejects oversmooth redraws

  4. Structure-aware SR fallback (photo-like / mottled)
       logo_restorer.py → polish + Real-ESRGAN loop + flatten

  5. Optional Gemini redraw (opt-in only; demoted by default)

SVG→PNG uses cairosvg → PyMuPDF; Chrome is last-resort only (disable with
LOGO_NO_CHROME=1). Do not depend on Chrome for quality gates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EngineCapabilities:
    """What this checkout can actually run right now."""

    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.details)


def probe_capabilities() -> EngineCapabilities:
    from .raster_polish import describe_available_backends

    caps = describe_available_backends()
    # Ensemble tracers
    try:
        import vtracer  # noqa: F401

        caps["vtracer"] = True
    except Exception:
        caps["vtracer"] = False
    try:
        from .inkscape import find_inkscape

        caps["inkscape"] = find_inkscape() is not None
    except Exception:
        caps["inkscape"] = caps.get("inkscape", False)
    try:
        import cairosvg  # noqa: F401

        caps["cairosvg"] = True
    except Exception:
        caps["cairosvg"] = False
    try:
        import pymupdf  # noqa: F401

        caps["pymupdf"] = True
    except Exception:
        try:
            import fitz  # noqa: F401

            caps["pymupdf"] = True
        except Exception:
            caps["pymupdf"] = False
    caps["sectional_bezier"] = True
    caps["chrome_optional_last_resort"] = True
    return EngineCapabilities(details=caps)


def pipeline_stages() -> list[str]:
    return [
        "prepare_knockout",
        "optional_raster_polish",
        "sectional_bezier_dejatype_anchors",
        "ensemble_vtracer_inkscape",
        "realesrgan_family_sr",
        "lanczos_conservator",
        "optional_gemini_opt_in",
    ]


__all__ = [
    "EngineCapabilities",
    "probe_capabilities",
    "pipeline_stages",
]
