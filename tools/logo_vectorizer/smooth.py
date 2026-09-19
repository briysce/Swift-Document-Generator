"""Contour smoothing: RDP simplification, Chaikin, Catmull-Rom cubic Béziers."""

from __future__ import annotations

import math

import cv2
import numpy as np


def contour_to_points(contour: np.ndarray) -> np.ndarray:
    """Nx2 float array from OpenCV contour."""
    pts = contour.reshape(-1, 2).astype(np.float64)
    if len(pts) < 3:
        return pts
    if not np.allclose(pts[0], pts[-1]):
        pts = np.vstack([pts, pts[0]])
    return pts


def rdp_simplify(points: np.ndarray, epsilon: float) -> np.ndarray:
    """Ramer–Douglas–Peucker via OpenCV."""
    if len(points) < 4:
        return points
    closed = np.allclose(points[0], points[-1])
    work = points[:-1] if closed else points
    approx = cv2.approxPolyDP(
        work.reshape(-1, 1, 2).astype(np.float32),
        epsilon,
        closed=True,
    ).reshape(-1, 2).astype(np.float64)
    if closed:
        approx = np.vstack([approx, approx[0]])
    return approx


def chaikin(points: np.ndarray, iterations: int = 2) -> np.ndarray:
    """Corner-cutting smooth; preserves closed loop."""
    if len(points) < 4:
        return points
    closed = np.allclose(points[0], points[-1])
    ring = points[:-1] if closed else points
    for _ in range(iterations):
        n = len(ring)
        nxt: list[np.ndarray] = []
        for i in range(n):
            p0 = ring[i]
            p1 = ring[(i + 1) % n]
            nxt.append(0.75 * p0 + 0.25 * p1)
            nxt.append(0.25 * p0 + 0.75 * p1)
        ring = np.array(nxt, dtype=np.float64)
    if closed:
        ring = np.vstack([ring, ring[0]])
    return ring


def _catmull_rom_to_beziers(points: np.ndarray) -> list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    """Convert closed/open polyline to cubic Bézier segments."""
    if len(points) < 2:
        return []
    closed = np.allclose(points[0], points[-1])
    ring = points[:-1] if closed else points
    n = len(ring)
    if n < 2:
        return []

    def pt(i: int) -> np.ndarray:
        if closed:
            return ring[i % n]
        return ring[min(max(i, 0), n - 1)]

    segments: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = []
    last = n if closed else n - 1
    for i in range(last):
        p0, p1, p2, p3 = pt(i - 1), pt(i), pt(i + 1), pt(i + 2)
        b0 = p1
        b1 = p1 + (p2 - p0) / 6.0
        b2 = p2 - (p3 - p1) / 6.0
        b3 = p2
        segments.append((b0, b1, b2, b3))
    return segments


def beziers_to_path_d(segments: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]) -> str:
    if not segments:
        return ""
    parts: list[str] = []
    b0, _, _, _ = segments[0]
    parts.append(f"M{b0[0]:.3f},{b0[1]:.3f}")
    for _, b1, b2, b3 in segments:
        parts.append(
            f"C{b1[0]:.3f},{b1[1]:.3f} {b2[0]:.3f},{b2[1]:.3f} {b3[0]:.3f},{b3[1]:.3f}"
        )
    parts.append("Z")
    return "".join(parts)


def smooth_contour(
    contour: np.ndarray,
    *,
    rdp_factor: float = 0.0025,
    chaikin_iters: int = 2,
) -> str:
    """Full smooth pipeline → SVG subpath ``d`` fragment."""
    pts = contour_to_points(contour)
    if len(pts) < 4:
        return ""

    perimeter = cv2.arcLength(contour, True)
    epsilon = max(1.5, perimeter * rdp_factor)
    pts = rdp_simplify(pts, epsilon)
    if len(pts) < 4:
        return ""

    pts = chaikin(pts, iterations=chaikin_iters)
    segments = _catmull_rom_to_beziers(pts)
    return beziers_to_path_d(segments)


def adaptive_rdp_factor(area: float, trace_area: float) -> float:
    """Smaller epsilon for large shapes (SWIFT), tighter for letter bowls."""
    ratio = area / trace_area
    if ratio > 0.05:
        return 0.0018
    if ratio > 0.005:
        return 0.0025
    return 0.004
