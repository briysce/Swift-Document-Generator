"""Professional PNG → SVG vectorizer.

Two flavours are exposed at the top level:

    - `vectorize_ensemble` / `vectorize_ensemble_file`
      Multi-backend race + AI advisors + hole preservation (legacy pipeline
      used for the wordmark exports). VTracer + Inkscape lead the race.

    - `vectorize_sectional` / `vectorize_sectional_file`
      Manual-quality per-raster tracer with sectional decomposition
      (DejaType-style per-element anchor placement). Given a raster, it
      auto-analyses palette + edges + symmetry, splits the image into named
      regions (`swift-orange`, `swift-shadow`, ...), and traces each region
      independently with a Schneider-style Bezier fitter.

    - `polish_raster` / `polish_file`
      Hierarchical pre-vectorize polish: classical Gigapixel/Upscayl/Remacri/
      UltraSharp-inspired OpenCV path, then Real-ESRGAN family weights,
      optional GFPGAN / InstructIR / DeOldify / Upscayl CLI (all fail-open).
"""

from .ai_advisors import AIConfig
from .analyze import RasterAnalysis, analyze_raster, color_masks
from .engine import EngineCapabilities, pipeline_stages, probe_capabilities
from .ensemble import EnsembleResult, vectorize_ensemble, vectorize_ensemble_file
from .manual_trace import ManualTraceConfig, ManualTraceResult, trace_mask
from .qa import verify_p_counters
from .raster_polish import (
    PolishResult,
    describe_available_backends,
    polish_file,
    polish_raster,
)
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
    "EngineCapabilities",
    "EnsembleResult",
    "ManualTraceConfig",
    "ManualTraceResult",
    "PolishResult",
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
    "describe_available_backends",
    "pipeline_stages",
    "polish_file",
    "polish_raster",
    "probe_capabilities",
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
