"""Ensemble orchestrator: multi-backend race + scoring + AI advisors + cache."""

from __future__ import annotations

import io
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import backends as be
from .ai_advisors import (
    AIConfig,
    AIContext,
    analyze_source_multi,
    apply_hints_to_preprocess,
    backend_priority,
    critique_render_multi,
    resolve_providers,
)
from .backends import TraceCandidate, generate_candidates
from .cache import image_hash, load_cached, save_cached
from .preprocess import DEFAULT_VARIANTS, PreprocessConfig, build_mask
from .refine import refine_svg_paths
from .score import ScoredCandidate, _heuristic_rank, score_all


@dataclass
class EnsembleResult:
    svg: str
    method: str
    preprocess: PreprocessConfig
    score: object  # ScoreReport
    contour_count: int
    hole_count: int
    candidates_tried: int
    source_size: tuple[int, int]
    ai: AIContext = field(default_factory=AIContext)


def _cfg_from_cache(entry: dict) -> PreprocessConfig | None:
    try:
        return PreprocessConfig(
            upscale=int(entry["upscale"]),
            blur_radius=float(entry["blur_radius"]),
            alpha_threshold=int(entry["alpha_threshold"]),
            min_area=float(entry["min_area"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _run_single(img: Image.Image, cfg: PreprocessConfig, fill_hex: str, backend: str) -> TraceCandidate | None:
    mask, trace_size, source_size = build_mask(img, cfg)
    fns = {
        "opencv-tree": lambda: be._opencv_trace(mask, trace_size, source_size, cfg, fill_hex, mode="tree"),
        "opencv-ccomp": lambda: be._opencv_trace(mask, trace_size, source_size, cfg, fill_hex, mode="ccomp"),
        "potrace": lambda: be._potrace_trace(mask, trace_size, source_size, cfg, fill_hex),
        "vtracer": lambda: be._vtracer_trace(mask, trace_size, source_size, cfg, fill_hex),
        "inkscape": lambda: be._inkscape_trace(mask, trace_size, source_size, cfg, fill_hex),
    }
    fn = fns.get(backend)
    if not fn:
        return None
    try:
        return fn()
    except Exception:
        return None


def _build_variants(hints_override: tuple[int, float, int, float] | None) -> tuple[PreprocessConfig, ...]:
    if hints_override is None:
        return DEFAULT_VARIANTS
    upscale, blur, threshold, min_area = hints_override
    ai_cfg = PreprocessConfig(
        upscale=upscale,
        blur_radius=blur,
        alpha_threshold=threshold,
        min_area=min_area,
    )
    # AI-suggested config first, then defaults (deduped by key)
    seen = {ai_cfg.key()}
    out = [ai_cfg]
    for v in DEFAULT_VARIANTS:
        if v.key() not in seen:
            out.append(v)
            seen.add(v.key())
    return tuple(out)


def _pick_winner(
    ranked: list[ScoredCandidate],
    *,
    ai_providers: list[str],
    ref_img: Image.Image,
    ai_ctx: AIContext,
) -> ScoredCandidate | None:
    """Pick best candidate; AI critique may reject top scorer."""
    passing = [s for s in ranked if s.report.passes_gates()]
    pool = passing or ranked

    if not ai_providers:
        return pool[0] if pool else None

    for item in pool[:5]:
        if item.rendered is None:
            continue
        critique = critique_render_multi(
            ref_img,
            item.rendered,
            ai_providers,
            backend=item.candidate.backend,
            alpha_iou=item.report.alpha_iou,
            p_hollow=item.report.p_hollow,
            holes=item.candidate.hole_count,
        )
        if critique is None:
            continue
        ai_ctx.critiques.append(critique)
        if not critique.reject_candidate and not critique.missing_counters:
            return item
        print(
            f"[ai] rejected {item.candidate.backend} ({item.candidate.preprocess.key()}), trying next",
            file=sys.stderr,
        )

    # All AI-rejected or no AI response — fall back to top scorer with holes
    for item in pool:
        if item.candidate.hole_count >= 2 and item.report.p_hollow >= 0.35:
            return item
    return pool[0] if pool else None


def vectorize_ensemble(
    img: Image.Image,
    fill_hex: str = "#FFFFFF",
    *,
    use_cache: bool = True,
    is_orange: bool = True,
    ai: AIConfig | None = None,
) -> EnsembleResult:
    ai = ai or AIConfig()
    ai_ctx = AIContext()
    ai_providers: list[str] = []

    if ai.enabled:
        ai_providers = resolve_providers(ai.providers)
        if ai_providers:
            ai_ctx.providers_used = ai_providers
            ai_ctx.hints = analyze_source_multi(img, ai_providers)
        else:
            print("[ai] enabled but no API keys found — running offline ensemble", file=sys.stderr)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    cache_key = image_hash(buf.getvalue())

    hints_override = apply_hints_to_preprocess(ai_ctx.hints)
    variants = _build_variants(hints_override)
    backend_order = backend_priority(ai_ctx.hints)

    candidates: list[TraceCandidate] = []
    if use_cache:
        cached = load_cached(cache_key)
        if cached and "backend" in cached:
            cfg = _cfg_from_cache(cached)
            if cfg:
                c = _run_single(img, cfg, fill_hex, cached["backend"])
                if c:
                    candidates.append(c)

    candidates.extend(
        generate_candidates(img, fill_hex, preprocess_variants=variants, backend_order=backend_order)
    )

    if not candidates:
        raise RuntimeError("ensemble produced no candidates")

    score_pool = _heuristic_rank(candidates)
    require_holes = ai_ctx.hints.has_holes if ai_ctx.hints else True
    ranked = score_all(score_pool, img, is_orange=is_orange, require_holes=require_holes)
    if not ranked:
        raise RuntimeError("no candidates scored successfully")

    winner = _pick_winner(ranked, ai_providers=ai_providers, ref_img=img, ai_ctx=ai_ctx)
    if winner is None:
        raise RuntimeError("no candidate passed scoring")

    score = winner.report
    refined_svg = refine_svg_paths(winner.candidate.svg, snap=False)

    if use_cache:
        save_cached(
            cache_key,
            {
                "backend": winner.candidate.backend,
                "upscale": winner.candidate.preprocess.upscale,
                "blur_radius": winner.candidate.preprocess.blur_radius,
                "alpha_threshold": winner.candidate.preprocess.alpha_threshold,
                "min_area": winner.candidate.preprocess.min_area,
                "score": score.total,
            },
        )

    return EnsembleResult(
        svg=refined_svg,
        method=f"ensemble/{winner.candidate.backend}",
        preprocess=winner.candidate.preprocess,
        score=score,
        contour_count=winner.candidate.contour_count,
        hole_count=winner.candidate.hole_count,
        candidates_tried=len(candidates),
        source_size=img.size,
        ai=ai_ctx,
    )


def vectorize_ensemble_file(
    input_path: Path,
    output_path: Path | None = None,
    *,
    fill_hex: str = "#FFFFFF",
    use_cache: bool = True,
    ai: AIConfig | None = None,
) -> EnsembleResult:
    img = Image.open(input_path).convert("RGBA")
    is_orange = "orange" in input_path.name.lower()
    result = vectorize_ensemble(
        img, fill_hex, use_cache=use_cache, is_orange=is_orange, ai=ai
    )
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result.svg, encoding="utf-8")
    return result
