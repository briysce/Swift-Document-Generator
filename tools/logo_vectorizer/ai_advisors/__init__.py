"""Optional vision AI advisors (Gemini, Claude, OpenAI)."""

from .base import CritiqueResult, SourceHints
from .orchestrator import (
    AIConfig,
    AIContext,
    analyze_source_multi,
    apply_hints_to_preprocess,
    backend_priority,
    critique_render_multi,
    resolve_providers,
)

__all__ = [
    "AIConfig",
    "AIContext",
    "CritiqueResult",
    "SourceHints",
    "analyze_source_multi",
    "apply_hints_to_preprocess",
    "backend_priority",
    "critique_render_multi",
    "resolve_providers",
]
