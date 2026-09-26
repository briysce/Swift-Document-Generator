"""Stage B: high-fidelity contour trace — match PNG geometry without aesthetic rounding."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

from .preprocess import PreprocessConfig, build_mask


@dataclass
class PolishConfig:
    upscale: int = 4
    blur_radius: float = 1.2
    alpha_threshold: int = 80
    min_area: float = 500
    fill_hex: str = "#FFFFFF"
    max_deviation_px: float = 0.75  # approxPolyDP epsilon cap at trace resolution
    straight_snap_deg: float = 4.0
    straight_deviation_px: float = 0.6
    curve_fit_max_error_px: float = 1.0

    def preprocess(self) -> PreprocessConfig:
        return PreprocessConfig(
            upscale=self.upscale,
            blur_radius=self.blur_radius,
            alpha_threshold=self.alpha_threshold,
            min_area=self.min_area,
        )


@dataclass
class PolishResult:
    svg: str
    contour_count: int
    hole_count: int
    trace_size: tuple[int, int]
    source_size: tuple[int, int]
    perimeter_trace: float = 0.0
    circularity_trace: float = 0.0


def _wrap_svg(body: str, source_size: tuple[int, int], fill_hex: str) -> str:
    w, h = source_size
    return (
        f'<svg style="background:transparent" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">'
        f'<path fill="{fill_hex}" fill-rule="evenodd" d="{body}"/>'
        f"</svg>"
    )


def _scale_path_d(d: str, scale: float) -> str:
    if scale == 1.0:
        return d

    def repl(match: re.Match[str]) -> str:
        return f"{float(match.group(0)) / scale:.3f}"

    return re.sub(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", repl, d)


def _contour_ring(contour: np.ndarray) -> np.ndarray:
    pts = contour.reshape(-1, 2).astype(np.float64)
    if len(pts) < 3:
        return pts
    if not np.allclose(pts[0], pts[-1]):
        pts = np.vstack([pts, pts[0]])
    return pts


def _nearest_contour_index(ring: np.ndarray, point: np.ndarray) -> int:
    dists = np.sum((ring - point) ** 2, axis=1)
    return int(np.argmin(dists))


def _point_line_distance(point: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    ab = b - a
    length_sq = float(np.dot(ab, ab))
    if length_sq < 1e-12:
        return float(np.linalg.norm(point - a))
    t = float(np.clip(np.dot(point - a, ab) / length_sq, 0.0, 1.0))
    proj = a + t * ab
    return float(np.linalg.norm(point - proj))


def _max_deviation_from_line(points: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    if len(points) == 0:
        return 0.0
    return max(_point_line_distance(p, a, b) for p in points)


def _snap_axis(dx: float, dy: float, snap_deg: float) -> tuple[float, float]:
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return dx, dy
    angle = math.degrees(math.atan2(dy, dx)) % 180
    targets = (0.0, 45.0, 90.0, 135.0)
    best = min(targets, key=lambda t: min(abs(angle - t), abs(angle - t - 180)))
    if min(abs(angle - best), abs(angle - best - 180)) > snap_deg:
        return dx, dy
    length = math.hypot(dx, dy)
    if best in (0.0, 180.0):
        return math.copysign(length, dx), 0.0
    if best == 90.0:
        return 0.0, math.copysign(length, dy)
    s = length / math.sqrt(2)
    return math.copysign(s, dx), math.copysign(s, dy)


def _fit_cubic_bezier(
    points: np.ndarray,
    *,
    max_error: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
    """Least-squares cubic Bézier fit; returns None if error exceeds max_error."""
    if len(points) < 2:
        return None
    p0 = points[0]
    p3 = points[-1]
    if len(points) == 2:
        return p0, p0, p3, p3

    # Uniform parameterization
    t = np.linspace(0.0, 1.0, len(points))
    # Cubic Bernstein basis for interior control points (fixed endpoints)
    # B0=(1-t)^3, B1=3(1-t)^2 t, B2=3(1-t)t^2, B3=t^3
    a1 = 3 * (1 - t) ** 2 * t
    a2 = 3 * (1 - t) * t ** 2
    b0 = (1 - t) ** 3
    b3 = t ** 3
    rhs_x = points[:, 0] - b0 * p0[0] - b3 * p3[0]
    rhs_y = points[:, 1] - b0 * p0[1] - b3 * p3[1]

    mat = np.column_stack([a1, a2])
    try:
        cx, _, _, _ = np.linalg.lstsq(mat, rhs_x, rcond=None)
        cy, _, _, _ = np.linalg.lstsq(mat, rhs_y, rcond=None)
    except np.linalg.LinAlgError:
        return None
    p1 = np.array([cx[0], cy[0]], dtype=np.float64)
    p2 = np.array([cx[1], cy[1]], dtype=np.float64)

    # Measure max fit error
    for i, ti in enumerate(t):
        pt = (
            (1 - ti) ** 3 * p0
            + 3 * (1 - ti) ** 2 * ti * p1
            + 3 * (1 - ti) * ti ** 2 * p2
            + ti ** 3 * p3
        )
        err = float(np.linalg.norm(pt - points[i]))
        if err > max_error:
            return None
    return p0, p1, p2, p3


def _contour_slice(ring: np.ndarray, i0: int, i1: int) -> np.ndarray:
    """Points along closed ring from i0 to i1 inclusive (forward)."""
    n = len(ring) - 1  # drop duplicate close
    if i0 <= i1:
        idx = list(range(i0, i1 + 1))
    else:
        idx = list(range(i0, n)) + list(range(0, i1 + 1))
    return ring[idx]


def _contour_to_path_d(contour: np.ndarray, cfg: PolishConfig) -> str:
    """
    Tight contour → SVG subpath: straight H/V/L where mask is straight,
    cubic Béziers only when needed to hug curves within max error.
    """
    ring = _contour_ring(contour)
    if len(ring) < 4:
        return ""

    perimeter = cv2.arcLength(contour, True)
    epsilon = min(cfg.max_deviation_px, max(0.5, perimeter * 0.0004))
    simplified = cv2.approxPolyDP(
        ring[:-1].reshape(-1, 1, 2).astype(np.float32),
        epsilon,
        closed=True,
    ).reshape(-1, 2).astype(np.float64)

    if len(simplified) < 3:
        return ""

    # Map simplified vertices back to contour indices
    indices = [_nearest_contour_index(ring, pt) for pt in simplified]
    n = len(simplified)
    parts: list[str] = []
    start = simplified[0]
    parts.append(f"M{start[0]:.3f},{start[1]:.3f}")
    cursor = start.copy()

    for k in range(n):
        i0 = indices[k]
        i1 = indices[(k + 1) % n]
        a = simplified[k]
        b = simplified[(k + 1) % n]
        segment = _contour_slice(ring, i0, i1)
        max_dev = _max_deviation_from_line(segment, a, b)

        if max_dev <= cfg.straight_deviation_px:
            dx, dy = b[0] - cursor[0], b[1] - cursor[1]
            sdx, sdy = _snap_axis(dx, dy, cfg.straight_snap_deg)
            end = np.array([cursor[0] + sdx, cursor[1] + sdy], dtype=np.float64)
            if abs(sdy) < 1e-6:
                parts.append(f"H{end[0]:.3f}")
            elif abs(sdx) < 1e-6:
                parts.append(f"V{end[1]:.3f}")
            else:
                parts.append(f"L{end[0]:.3f},{end[1]:.3f}")
            cursor = end
            continue

        fit = _fit_cubic_bezier(segment, max_error=cfg.curve_fit_max_error_px)
        if fit is not None:
            _, p1, p2, p3 = fit
            parts.append(
                f"C{p1[0]:.3f},{p1[1]:.3f} {p2[0]:.3f},{p2[1]:.3f} {p3[0]:.3f},{p3[1]:.3f}"
            )
            cursor = p3.copy()
        else:
            # Fall back to dense polyline — preserves geometry exactly
            for pt in segment[1:]:
                parts.append(f"L{pt[0]:.3f},{pt[1]:.3f}")
                cursor = pt.copy()

    parts.append("Z")
    return "".join(parts)


def _silhouette_metrics(contours: list[np.ndarray], min_area: float) -> tuple[float, float]:
    """Return (total perimeter, mean circularity) for kept contours."""
    perimeters: list[float] = []
    circularities: list[float] = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue
        peri = cv2.arcLength(contour, True)
        if peri <= 0:
            continue
        perimeters.append(peri)
        circularities.append(float(4.0 * math.pi * area / (peri * peri)))
    total_peri = sum(perimeters)
    mean_circ = sum(circularities) / len(circularities) if circularities else 0.0
    return total_peri, mean_circ


def polish_png(
    img: Image.Image,
    cfg: PolishConfig | None = None,
) -> PolishResult:
    """
    Stage B: PNG alpha → high-fidelity path SVG.

    Pipeline: upscale + blur + rethreshold → RETR_CCOMP contours → tight
    approxPolyDP + axis straight snaps + error-bounded cubics → evenodd SVG.
    No Chaikin smoothing, no circle-arc hole replacement.
    """
    cfg = cfg or PolishConfig()
    mask, trace_size, source_size = build_mask(img, cfg.preprocess())
    scale = float(cfg.upscale)

    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if hierarchy is None or not contours:
        raise RuntimeError("no contours found for geometric polish")

    h = hierarchy[0]
    raw_subpaths: list[str] = []
    holes = 0
    kept = 0
    kept_contours: list[np.ndarray] = []

    for i, contour in enumerate(contours):
        area = cv2.contourArea(contour)
        if area < cfg.min_area:
            continue
        kept += 1
        kept_contours.append(contour)
        if h[i][3] != -1:
            holes += 1
        d = _contour_to_path_d(contour, cfg)
        if d:
            raw_subpaths.append(d)

    if not raw_subpaths:
        raise RuntimeError("geometric polish produced no subpaths")

    peri_trace, circ_trace = _silhouette_metrics(kept_contours, cfg.min_area)
    subpaths = [_scale_path_d(d, scale) for d in raw_subpaths]
    svg = _wrap_svg("".join(subpaths), source_size, cfg.fill_hex)
    return PolishResult(
        svg=svg,
        contour_count=kept,
        hole_count=holes,
        trace_size=trace_size,
        source_size=source_size,
        perimeter_trace=peri_trace / scale,
        circularity_trace=circ_trace,
    )


def polish_file(
    png_path,
    *,
    fill_hex: str = "#FFFFFF",
    upscale: int = 4,
) -> PolishResult:
    from pathlib import Path

    img = Image.open(Path(png_path)).convert("RGBA")
    return polish_png(
        img,
        PolishConfig(upscale=upscale, fill_hex=fill_hex),
    )
