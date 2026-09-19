"""Professional PNG → SVG vectorizer.

Two flavours are exposed at the top level:

    - `vectorize_ensemble` / `vectorize_ensemble_file`
      Multi-backend race + AI advisors + hole preservation (legacy pipeline
      used for the wordmark exports).

    - `vectorize_sectional` / `vectorize_sectional_file`
      Manual-quality per-raster tracer with sectional decomposition.
      Given a raster, it auto-analyses palette + edges + symmetry, splits
      the image into named regions (`swift-orange`, `swift-shadow`, ...),
      and traces each region independently with a Schneider-style Bezier
      fitter that mimics the anchor-placement decisions of a designer
      doing a manual trace.
"""

from .ai_advisors import AIConfig
from .analyze import RasterAnalysis, analyze_raster, color_masks
from .ensemble import EnsembleResult, vectorize_ensemble, vectorize_ensemble_file
from .manual_trace import ManualTraceConfig, ManualTraceResult, trace_mask
from .qa import verify_p_counters
from .sectional import (
    SectionalResult,
    SectionTrace,
    rasterize_svg,
    trace_section,
    vectorize_sectional,
    vectorize_sectional_file,
)
from .sections import (
    Section,
    SectionSet,
    decompose_by_color,
    decompose_swift_supply,
)
from .vectorize import VectorizeOptions, VectorizeResult, vectorize_file, vectorize_raster

__all__ = [
    "AIConfig",
    "EnsembleResult",
    "ManualTraceConfig",
    "ManualTraceResult",
    "RasterAnalysis",
    "Section",
    "SectionSet",
    "SectionTrace",
    "SectionalResult",
    "VectorizeOptions",
    "VectorizeResult",
    "analyze_raster",
    "color_masks",
    "decompose_by_color",
    "decompose_swift_supply",
    "rasterize_svg",
    "trace_mask",
    "trace_section",
    "vectorize_ensemble",
    "vectorize_ensemble_file",
    "vectorize_file",
    "vectorize_raster",
    "vectorize_sectional",
    "vectorize_sectional_file",
    "verify_p_counters",
]
