"""Optional vision AI advisors (Gemini, Claude, OpenAI) + collab_mind."""

from .base import CritiqueResult, SourceHints
from .collab_mind import (
    CollabGuidance,
    StuckSignal,
    collaborate,
    detect_stuck,
    escalate_if_stuck,
)
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
    "CollabGuidance",
    "CritiqueResult",
    "SourceHints",
    "StuckSignal",
    "analyze_source_multi",
    "apply_hints_to_preprocess",
    "backend_priority",
    "collaborate",
    "critique_render_multi",
    "detect_stuck",
    "escalate_if_stuck",
    "resolve_providers",
]
