"""Hierarchy-aware PNG → SVG vectorizer with hole preservation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageFilter

from .smooth import adaptive_rdp_factor, smooth_contour

DEFAULT_UPSCALE = 4
DEFAULT_BLUR = 1.2
DEFAULT_ALPHA_THRESHOLD = 80
DEFAULT_MIN_AREA = 500  # at trace resolution; filters anti-alias speckle


@dataclass
class VectorizeOptions:
    upscale: int = DEFAULT_UPSCALE
    blur_radius: float = DEFAULT_BLUR
    alpha_threshold: int = DEFAULT_ALPHA_THRESHOLD
    min_area: float = DEFAULT_MIN_AREA
    fill_hex: str = "#FFFFFF"
    chaikin_iters: int = 2


@dataclass
class VectorizeResult:
    svg: str
    method: str
    trace_size: tuple[int, int]
    source_size: tuple[int, int]
    contour_count: int
    hole_count: int


def _binary_mask(img: Image.Image, opts: VectorizeOptions) -> tuple[np.ndarray, tuple[int, int], tuple[int, int]]:
    source_size = img.size
    if opts.upscale > 1:
        work = img.resize(
            (img.width * opts.upscale, img.height * opts.upscale),
            Image.Resampling.LANCZOS,
        )
    else:
        work = img

    alpha = np.array(work.split()[3], dtype=np.uint8)
    alpha_img = Image.fromarray(alpha)
    if opts.blur_radius > 0:
        alpha_img = alpha_img.filter(ImageFilter.GaussianBlur(radius=opts.blur_radius))
    letter = (np.array(alpha_img) >= opts.alpha_threshold).astype(np.uint8) * 255
    return letter, work.size, source_size


def _scale_path_d(d: str, scale: float) -> str:
    """Scale numeric coordinates in path ``d`` back to source resolution."""
    if scale == 1.0:
        return d
    import re

    def repl(match: re.Match[str]) -> str:
        val = float(match.group(0))
        return f"{val / scale:.3f}"

    return re.sub(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", repl, d)


def vectorize_raster(
    img: Image.Image,
    opts: VectorizeOptions | None = None,
) -> VectorizeResult:
    """Trace transparent PNG to SVG with ``fill-rule=\"evenodd\"`` hole cut-outs."""
    opts = opts or VectorizeOptions()
    mask, trace_size, source_size = _binary_mask(img, opts)

    contours, hierarchy = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_NONE)
    if hierarchy is None or not contours:
        raise RuntimeError("no contours found in logo mask")

    h = hierarchy[0]
    trace_area = float(trace_size[0] * trace_size[1])
    scale = opts.upscale if opts.upscale else 1.0

    subpaths: list[str] = []
    hole_count = 0
    kept = 0

    for i, contour in enumerate(contours):
        area = cv2.contourArea(contour)
        if area < opts.min_area:
            continue
        kept += 1
        if h[i][3] != -1:
            hole_count += 1
        rdp = adaptive_rdp_factor(area, trace_area)
        d = smooth_contour(contour, rdp_factor=rdp, chaikin_iters=opts.chaikin_iters)
        if not d:
            continue
        subpaths.append(_scale_path_d(d, scale))

    if not subpaths:
        raise RuntimeError("all contours filtered or failed smoothing")

    src_w, src_h = source_size
    combined_d = "".join(subpaths)
    svg = (
        f'<svg style="background:transparent" width="{src_w}" height="{src_h}" '
        f'viewBox="0 0 {src_w} {src_h}" xmlns="http://www.w3.org/2000/svg">'
        f'<path fill="{opts.fill_hex}" fill-rule="evenodd" d="{combined_d}"/>'
        f"</svg>"
    )
    return VectorizeResult(
        svg=svg,
        method="opencv-hierarchy",
        trace_size=trace_size,
        source_size=source_size,
        contour_count=kept,
        hole_count=hole_count,
    )


def vectorize_file(
    input_path: Path,
    output_path: Path | None = None,
    *,
    fill_hex: str = "#FFFFFF",
    **kwargs: object,
) -> VectorizeResult:
    img = Image.open(input_path).convert("RGBA")
    opts = VectorizeOptions(fill_hex=fill_hex, **{k: v for k, v in kwargs.items() if hasattr(VectorizeOptions, k)})  # type: ignore[arg-type]
    result = vectorize_raster(img, opts)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result.svg, encoding="utf-8")
    return result
