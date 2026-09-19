"""Orchestrate Gemini, Claude, and OpenAI vision advisors."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Callable

from PIL import Image

from . import claude, gemini, openai_advisor
from .base import CritiqueResult, SourceHints
from .claude import critique_render as claude_critique
from .claude import analyze_source as claude_analyze
from .gemini import critique_render as gemini_critique
from .gemini import analyze_source as gemini_analyze
from .openai_advisor import critique_render as openai_critique
from .openai_advisor import analyze_source as openai_analyze

PROVIDERS: dict[str, dict] = {
    "gemini": {"available": gemini.available, "analyze": gemini_analyze, "critique": gemini_critique},
    "claude": {"available": claude.available, "analyze": claude_analyze, "critique": claude_critique},
    "openai": {"available": openai_advisor.available, "analyze": openai_analyze, "critique": openai_critique},
}


@dataclass
class AIConfig:
    enabled: bool = False
    providers: list[str] = field(default_factory=lambda: ["gemini", "claude", "openai"])


@dataclass
class AIContext:
    hints: SourceHints | None = None
    critiques: list[CritiqueResult] = field(default_factory=list)
    providers_used: list[str] = field(default_factory=list)


def resolve_providers(requested: list[str] | None = None) -> list[str]:
    wanted = requested or list(PROVIDERS.keys())
    out: list[str] = []
    for name in wanted:
        name = name.strip().lower()
        if name not in PROVIDERS:
            continue
        if PROVIDERS[name]["available"]():
            out.append(name)
    return out


def analyze_source_multi(img: Image.Image, providers: list[str]) -> SourceHints | None:
    merged: SourceHints | None = None
    used: list[str] = []
    for name in providers:
        fn: Callable[[Image.Image], SourceHints | None] = PROVIDERS[name]["analyze"]
        result = fn(img)
        if result is None:
            continue
        used.append(name)
        merged = result if merged is None else merged.merged(result)
    if merged and used:
        print(f"[ai] source hints from: {', '.join(used)}", file=sys.stderr)
        if merged.recommended_backends:
            print(f"[ai] recommended backends: {merged.recommended_backends}", file=sys.stderr)
    return merged


def critique_render_multi(
    ref_img: Image.Image,
    render_img: Image.Image,
    providers: list[str],
    *,
    backend: str,
    alpha_iou: float,
    p_hollow: float,
    holes: int,
) -> CritiqueResult | None:
    results: list[CritiqueResult] = []
    for name in providers:
        fn = PROVIDERS[name]["critique"]
        r = fn(
            ref_img,
            render_img,
            backend=backend,
            alpha_iou=alpha_iou,
            p_hollow=p_hollow,
            holes=holes,
        )
        if r is not None:
            results.append(r)
    if not results:
        return None
    base = results[0]
    merged = base.merged(results[1:]) if len(results) > 1 else base
    print(
        f"[ai] critique ({merged.provider}): pass={merged.passes_qa} "
        f"reject={merged.reject_candidate} conf={merged.confidence:.2f}",
        file=sys.stderr,
    )
    if merged.issues:
        print(f"[ai] issues: {merged.issues}", file=sys.stderr)
    return merged


def apply_hints_to_preprocess(hints: SourceHints | None) -> tuple[int, float, int, float] | None:
    """Return (upscale, blur, threshold, min_area) override if hints specify preprocess."""
    if not hints or not hints.recommended_preprocess:
        return None
    p = hints.recommended_preprocess
    try:
        return (
            int(p.get("upscale", 4)),
            float(p.get("blur_radius", 1.2)),
            int(p.get("alpha_threshold", 80)),
            float(p.get("min_area", 500)),
        )
    except (TypeError, ValueError):
        return None


def backend_priority(hints: SourceHints | None) -> list[str]:
    if hints and hints.recommended_backends:
        return hints.recommended_backends
    return ["opencv-tree", "opencv-ccomp", "potrace", "vtracer", "inkscape"]
