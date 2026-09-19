"""Multi-backend SVG tracers with evenodd hole preservation."""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .inkscape import trace_with_inkscape
from .preprocess import PreprocessConfig, build_mask, potrace_binary, vtracer_rgb
from .smooth import adaptive_rdp_factor, smooth_contour


@dataclass
class TraceCandidate:
    svg: str
    backend: str
    preprocess: PreprocessConfig
    contour_count: int = 0
    hole_count: int = 0


def _scale_path_d(d: str, scale: float) -> str:
    if scale == 1.0:
        return d

    def repl(match: re.Match[str]) -> str:
        return f"{float(match.group(0)) / scale:.3f}"

    return re.sub(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", repl, d)


def _wrap_svg(body: str, source_size: tuple[int, int], fill_hex: str, evenodd: bool = True) -> str:
    w, h = source_size
    rule = ' fill-rule="evenodd"' if evenodd else ""
    return (
        f'<svg style="background:transparent" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">'
        f'<path fill="{fill_hex}"{rule} d="{body}"/>'
        f"</svg>"
    )


def _opencv_trace(
    mask: np.ndarray,
    trace_size: tuple[int, int],
    source_size: tuple[int, int],
    cfg: PreprocessConfig,
    fill_hex: str,
    *,
    mode: str,
) -> TraceCandidate | None:
    retr = cv2.RETR_TREE if mode == "tree" else cv2.RETR_CCOMP
    contours, hierarchy = cv2.findContours(mask, retr, cv2.CHAIN_APPROX_NONE)
    if hierarchy is None or not contours:
        return None

    h = hierarchy[0]
    trace_area = float(trace_size[0] * trace_size[1])
    scale = float(cfg.upscale)
    subpaths: list[str] = []
    holes = 0
    kept = 0

    for i, contour in enumerate(contours):
        area = cv2.contourArea(contour)
        if area < cfg.min_area:
            continue
        kept += 1
        if h[i][3] != -1:
            holes += 1
        rdp = adaptive_rdp_factor(area, trace_area)
        d = smooth_contour(contour, rdp_factor=rdp, chaikin_iters=2)
        if d:
            subpaths.append(_scale_path_d(d, scale))

    if not subpaths:
        return None

    return TraceCandidate(
        svg=_wrap_svg("".join(subpaths), source_size, fill_hex),
        backend=f"opencv-{mode}",
        preprocess=cfg,
        contour_count=kept,
        hole_count=holes,
    )


def _potrace_curve_to_d(curve) -> str:
    parts = [f"M{curve.start_point.x:.2f},{curve.start_point.y:.2f}"]
    for segment in curve.segments:
        if segment.is_corner:
            a, b = segment.c, segment.end_point
            parts.append(f"L{a.x:.2f},{a.y:.2f}L{b.x:.2f},{b.y:.2f}")
        else:
            a, b, c = segment.c1, segment.c2, segment.end_point
            parts.append(f"C{a.x:.2f},{a.y:.2f} {b.x:.2f},{b.y:.2f} {c.x:.2f},{c.y:.2f}")
    parts.append("Z")
    return "".join(parts)


def _potrace_trace(
    mask: np.ndarray,
    trace_size: tuple[int, int],
    source_size: tuple[int, int],
    cfg: PreprocessConfig,
    fill_hex: str,
) -> TraceCandidate | None:
    try:
        from potrace import POTRACE_TURNPOLICY_MINORITY, Bitmap
    except ImportError:
        return None

    binary = potrace_binary(mask)
    plist = Bitmap(binary, blacklevel=0.5).trace(
        turdsize=2,
        turnpolicy=POTRACE_TURNPOLICY_MINORITY,
        alphamax=1.0,
        opticurve=True,
        opttolerance=0.25,
    )
    if not plist:
        return None

    scale = float(cfg.upscale)
    subpaths = [_scale_path_d(_potrace_curve_to_d(c), scale) for c in plist]
    holes = sum(1 for c in plist if getattr(c, "child", None))

    return TraceCandidate(
        svg=_wrap_svg("".join(subpaths), source_size, fill_hex),
        backend="potrace",
        preprocess=cfg,
        contour_count=len(plist),
        hole_count=holes,
    )


def _vtracer_trace(
    mask: np.ndarray,
    trace_size: tuple[int, int],
    source_size: tuple[int, int],
    cfg: PreprocessConfig,
    fill_hex: str,
) -> TraceCandidate | None:
    try:
        import vtracer
    except ImportError:
        return None

    rgb = vtracer_rgb(mask)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        Image.fromarray(rgb).save(tmp_path)
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as svg_tmp:
            svg_path = Path(svg_tmp.name)
        try:
            vtracer.convert_image_to_svg_py(
                str(tmp_path),
                str(svg_path),
                colormode="binary",
                hierarchical="cutout",
                mode="spline",
                filter_speckle=2,
                corner_threshold=55,
                length_threshold=3.5,
                splice_threshold=45,
                path_precision=6,
            )
            raw = svg_path.read_text(encoding="utf-8")
        finally:
            svg_path.unlink(missing_ok=True)
    finally:
        tmp_path.unlink(missing_ok=True)

    svg = _normalize_external_svg(raw, fill_hex, trace_size, source_size)
    if not svg:
        return None
    hole_count = raw.count('fill-rule="evenodd"') + raw.lower().count("evenodd")
    return TraceCandidate(
        svg=svg,
        backend="vtracer",
        preprocess=cfg,
        contour_count=raw.count("<path"),
        hole_count=max(hole_count, 0),
    )


def _normalize_external_svg(
    raw: str,
    fill_hex: str,
    trace_size: tuple[int, int],
    source_size: tuple[int, int],
) -> str | None:
    """Merge external tracer paths into one evenodd path at source dimensions."""
    trace_w, trace_h = trace_size
    src_w, src_h = source_size
    scale = src_w / trace_w if trace_w else 1.0

    ds = re.findall(r'd="([^"]+)"', raw)
    if not ds:
        return None

    combined = "".join(_scale_path_d(d, 1.0 / scale if scale != 1.0 else trace_w / src_w) for d in ds)
    # vtracer outputs at image px; scale down if viewBox differs
    dim = re.search(r'viewBox="[\d.]+ [\d.]+ ([\d.]+) ([\d.]+)"', raw)
    if dim:
        vb_w, vb_h = float(dim.group(1)), float(dim.group(2))
        if abs(vb_w - src_w) > 1:
            combined = _scale_path_d(combined, vb_w / src_w)

    return _wrap_svg(combined, source_size, fill_hex)


def _inkscape_trace(
    mask: np.ndarray,
    trace_size: tuple[int, int],
    source_size: tuple[int, int],
    cfg: PreprocessConfig,
    fill_hex: str,
) -> TraceCandidate | None:
    binary = potrace_binary(mask)
    svg = trace_with_inkscape(binary, trace_size, source_size, fill_hex)
    if not svg:
        return None
    # Ensure evenodd single path if possible
    ds = re.findall(r'd="([^"]+)"', svg)
    if ds:
        svg = _wrap_svg("".join(ds), source_size, fill_hex)
    return TraceCandidate(
        svg=svg,
        backend="inkscape",
        preprocess=cfg,
        contour_count=len(ds),
        hole_count=0,
    )


BACKEND_FNS = (
    lambda m, ts, ss, c, f: _opencv_trace(m, ts, ss, c, f, mode="tree"),
    lambda m, ts, ss, c, f: _opencv_trace(m, ts, ss, c, f, mode="ccomp"),
    _potrace_trace,
    _vtracer_trace,
    _inkscape_trace,
)


def generate_candidates(
    img: Image.Image,
    fill_hex: str,
    *,
    preprocess_variants: tuple[PreprocessConfig, ...] | None = None,
    backend_order: list[str] | None = None,
) -> list[TraceCandidate]:
    """Run backend × preprocess combinations; skip failures."""
    from .preprocess import DEFAULT_VARIANTS

    variants = preprocess_variants or DEFAULT_VARIANTS
    fn_map = {
        "opencv-tree": lambda m, ts, ss, c, f: _opencv_trace(m, ts, ss, c, f, mode="tree"),
        "opencv-ccomp": lambda m, ts, ss, c, f: _opencv_trace(m, ts, ss, c, f, mode="ccomp"),
        "potrace": _potrace_trace,
        "vtracer": _vtracer_trace,
        "inkscape": _inkscape_trace,
    }
    order = backend_order or list(fn_map.keys())
    fns = [fn_map[b] for b in order if b in fn_map]
    if not fns:
        fns = list(fn_map.values())

    out: list[TraceCandidate] = []
    seen: set[str] = set()

    for cfg in variants:
        mask, trace_size, source_size = build_mask(img, cfg)
        for fn in fns:
            try:
                cand = fn(mask, trace_size, source_size, cfg, fill_hex)
            except Exception:
                cand = None
            if cand is None:
                continue
            key = f"{cand.backend}:{cfg.key()}"
            if key in seen:
                continue
            seen.add(key)
            out.append(cand)
    return out
