"""Candidate SVG scoring vs source PNG."""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .backends import TraceCandidate
from .preprocess import PreprocessConfig
from .qa import counter_hollow_score, render_svg_chrome, supply_p_ranges


@dataclass
class ScoreReport:
    total: float
    alpha_iou: float
    edge_score: float
    ssim: float
    p_hollow: float
    hole_bonus: float
    details: dict[str, float]

    def passes_gates(self) -> bool:
        return self.p_hollow >= 0.35 and self.alpha_iou >= 0.82


def _alpha_mask(img: Image.Image, threshold: int = 64) -> np.ndarray:
    return (np.array(img.convert("RGBA"))[:, :, 3] > threshold).astype(np.uint8)


def _render_mask(img: Image.Image, *, is_orange: bool = True, threshold: int = 64) -> np.ndarray:
    """Extract logo pixels from a Chrome RGB(A) screenshot on black."""
    arr = np.array(img.convert("RGBA"))
    r, g, b, a = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2], arr[:, :, 3]
    if is_orange:
        return ((a > threshold) & (r > 100) & (g < 150) & (b < 120)).astype(np.uint8)
    return ((a > threshold) & (r > 200) & (g > 200) & (b > 200)).astype(np.uint8)


def _canny_edges(mask: np.ndarray) -> np.ndarray:
    blurred = cv2.GaussianBlur(mask.astype(np.uint8) * 255, (3, 3), 0)
    return cv2.Canny(blurred, 50, 150)


def alpha_iou(ref: np.ndarray, render: np.ndarray) -> float:
    if ref.shape != render.shape:
        render = cv2.resize(render, (ref.shape[1], ref.shape[0]), interpolation=cv2.INTER_NEAREST)
    inter = np.logical_and(ref, render).sum()
    union = np.logical_or(ref, render).sum()
    return float(inter / union) if union else 0.0


def trace_aligned_iou(ref_img: Image.Image, rendered: Image.Image, *, cfg: PreprocessConfig | None = None, is_orange: bool = True) -> float:
    """
    Compare rendered SVG to the same binarized mask used for tracing.

    PNG alpha alone understates logo area (soft 240–249 alpha); the trace mask
    is the fair reference for vector QA.
    """
    from .preprocess import DEFAULT_VARIANTS, build_mask

    cfg = cfg or DEFAULT_VARIANTS[0]
    mask, _, source_size = build_mask(ref_img, cfg)
    scale = cfg.upscale
    ref_bin = cv2.resize(
        (mask > 0).astype(np.uint8),
        source_size,
        interpolation=cv2.INTER_NEAREST,
    )
    render_bin = _render_mask(rendered, is_orange=is_orange)
    return alpha_iou(ref_bin, render_bin)


def edge_overlap(ref_edges: np.ndarray, render_edges: np.ndarray) -> float:
    if ref_edges.shape != render_edges.shape:
        render_edges = cv2.resize(render_edges, (ref_edges.shape[1], ref_edges.shape[0]))
    dilated = cv2.dilate(ref_edges, np.ones((3, 3), np.uint8))
    hits = np.logical_and(render_edges > 0, dilated > 0).sum()
    total = max(int(render_edges.sum() / 255), 1)
    return float(hits / total)


def simple_ssim(ref: np.ndarray, render: np.ndarray) -> float:
    """Lightweight SSIM on alpha masks."""
    if ref.shape != render.shape:
        render = cv2.resize(render.astype(np.float32), (ref.shape[1], ref.shape[0]))
    ref = ref.astype(np.float64)
    render = render.astype(np.float64)
    c1, c2 = (0.01 * 1) ** 2, (0.03 * 1) ** 2
    mu_x, mu_y = ref.mean(), render.mean()
    var_x, var_y = ref.var(), render.var()
    cov = ((ref - mu_x) * (render - mu_y)).mean()
    num = (2 * mu_x * mu_y + c1) * (2 * cov + c2)
    den = (mu_x ** 2 + mu_y ** 2 + c1) * (var_x + var_y + c2)
    return float(num / den) if den else 0.0


def p_hollow_mean(rendered: Image.Image, ref: Image.Image, *, is_orange: bool) -> float:
    runs = supply_p_ranges(ref)
    if len(runs) < 4:
        return 0.0
    ratios = []
    for idx in (2, 3):
        if idx < len(runs):
            r, _, _ = counter_hollow_score(rendered, runs[idx], is_orange=is_orange)
            ratios.append(r)
    return sum(ratios) / len(ratios) if ratios else 0.0


def render_svg_string(svg: str, width: int, height: int) -> Image.Image | None:
    with tempfile.TemporaryDirectory() as tmpdir:
        svg_path = Path(tmpdir) / "c.svg"
        png_path = Path(tmpdir) / "c.png"
        svg_path.write_text(svg, encoding="utf-8")
        try:
            render_svg_chrome(svg_path, png_path, width=width, height=height)
            return Image.open(png_path).convert("RGBA")
        except Exception:
            return None


@dataclass
class ScoredCandidate:
    candidate: TraceCandidate
    report: ScoreReport
    rendered: Image.Image | None = None


def score_candidate(
    candidate: TraceCandidate,
    ref_img: Image.Image,
    *,
    is_orange: bool = True,
) -> ScoreReport | None:
    w, h = ref_img.size
    rendered = render_svg_string(candidate.svg, w, h)
    if rendered is None:
        return None

    ref_alpha = _alpha_mask(ref_img)
    ren_alpha = _render_mask(rendered, is_orange=is_orange)
    iou = alpha_iou(ref_alpha, ren_alpha)
    ssim = simple_ssim(ref_alpha, ren_alpha)
    edge = edge_overlap(_canny_edges(ref_alpha), _canny_edges(ren_alpha))

    p_h = p_hollow_mean(rendered, ref_img, is_orange=is_orange)

    hole_bonus = 0.0
    if candidate.hole_count >= 2:
        hole_bonus = 0.08
    elif candidate.hole_count >= 1:
        hole_bonus = 0.04

    # Weighted total — P counters are mandatory gate, heavily weighted
    total = (
        0.30 * iou
        + 0.20 * ssim
        + 0.15 * edge
        + 0.30 * p_h
        + hole_bonus
    )

    return ScoredCandidate(
        candidate=candidate,
        report=ScoreReport(
            total=total,
            alpha_iou=iou,
            edge_score=edge,
            ssim=ssim,
            p_hollow=p_h,
            hole_bonus=hole_bonus,
            details={
                "contours": float(candidate.contour_count),
                "holes": float(candidate.hole_count),
            },
        ),
        rendered=rendered,
    )


def _heuristic_rank(candidates: list[TraceCandidate]) -> list[TraceCandidate]:
    """Pre-rank without Chrome render to limit expensive scoring."""

    def key(c: TraceCandidate) -> float:
        s = 0.0
        if c.hole_count >= 2:
            s += 0.5
        elif c.hole_count >= 1:
            s += 0.2
        if "opencv" in c.backend:
            s += 0.15
        if c.backend == "potrace":
            s += 0.1
        s -= c.contour_count * 0.001
        return s

    ranked = sorted(candidates, key=key, reverse=True)
    # Always include diverse backends in scoring pool
    pool: list[TraceCandidate] = []
    seen_backends: set[str] = set()
    for c in ranked:
        if len(pool) >= 8:
            break
        pool.append(c)
        seen_backends.add(c.backend)
    for c in ranked:
        if c.backend not in seen_backends and len(pool) < 10:
            pool.append(c)
            seen_backends.add(c.backend)
    return pool


def score_all(
    candidates: list[TraceCandidate],
    ref_img: Image.Image,
    *,
    is_orange: bool = True,
    require_holes: bool = True,
) -> list[ScoredCandidate]:
    """Score candidates once; skip zero-hole traces when holes required."""
    scored: list[ScoredCandidate] = []
    for cand in candidates:
        if require_holes and cand.hole_count < 1:
            continue
        item = score_candidate(cand, ref_img, is_orange=is_orange)
        if item is not None:
            scored.append(item)
    scored.sort(key=lambda s: s.report.total, reverse=True)
    return scored
