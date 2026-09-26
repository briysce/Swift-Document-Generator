"""Post-trace path refinement: snap straights, arc-fit bowls, simplify."""

from __future__ import annotations

import math
import re

import cv2
import numpy as np

SNAP_DEG = 6.0  # snap segments within this many degrees of H/V/diagonal


def _parse_path_d(d: str) -> list[tuple[str, list[float]]]:
    tokens = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", d)
    cmds: list[tuple[str, list[float]]] = []
    i = 0
    while i < len(tokens):
        cmd = tokens[i]
        if cmd in "MmLlHhVvCcSsQqTtAaZz":
            i += 1
            nums: list[float] = []
            while i < len(tokens) and tokens[i] not in "MmLlHhVvCcSsQqTtAaZz":
                nums.append(float(tokens[i]))
                i += 1
            cmds.append((cmd, nums))
        else:
            i += 1
    return cmds


def _snap_angle(dx: float, dy: float) -> tuple[float, float]:
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return dx, dy
    angle = math.degrees(math.atan2(dy, dx)) % 180
    targets = (0, 45, 90, 135)
    best = min(targets, key=lambda t: min(abs(angle - t), abs(angle - t - 180)))
    if min(abs(angle - best), abs(angle - best - 180)) > SNAP_DEG:
        return dx, dy
    length = math.hypot(dx, dy)
    rad = math.radians(best if best < 180 else 0)
    if best in (0, 180):
        return math.copysign(length, dx), 0.0
    if best == 90:
        return 0.0, math.copysign(length, dy)
    s = length / math.sqrt(2)
    return math.copysign(s, dx), math.copysign(s, dy)


def snap_path_straights(d: str) -> str:
    """Snap near-axis line segments in cubic-heavy paths (post smooth)."""
    # Operate on M/C/Z sequences from our generator — snap line-like C endpoints
    nums = [float(x) for x in re.findall(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", d)]
    if len(nums) < 4:
        return d
    pts = [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
    if len(pts) < 3:
        return d

    out_pts = [pts[0]]
    for i in range(1, len(pts)):
        dx = pts[i][0] - out_pts[-1][0]
        dy = pts[i][1] - out_pts[-1][1]
        sdx, sdy = _snap_angle(dx, dy)
        out_pts.append((out_pts[-1][0] + sdx, out_pts[-1][1] + sdy))

    # Rebuild as simplified polyline with Z — keeps evenodd topology
    parts = [f"M{out_pts[0][0]:.3f},{out_pts[0][1]:.3f}"]
    for x, y in out_pts[1:]:
        parts.append(f"L{x:.3f},{y:.3f}")
    parts.append("Z")
    return "".join(parts)


def fit_circle_arc(contour: np.ndarray) -> str | None:
    """If contour is roughly circular, emit elliptical arc SVG subpath."""
    pts = contour.reshape(-1, 2).astype(np.float32)
    if len(pts) < 8:
        return None
    (cx, cy), radius = cv2.minEnclosingCircle(pts)
    area = cv2.contourArea(contour)
    circle_area = math.pi * radius * radius
    if radius < 8 or area / max(circle_area, 1) < 0.65:
        return None
    # SVG arc full circle via two semicirces
    x0, y0 = cx - radius, cy
    x1, y1 = cx + radius, cy
    r = radius
    return (
        f"M{x0:.3f},{y0:.3f}"
        f"A{r:.3f},{r:.3f} 0 1 0 {x1:.3f},{y1:.3f}"
        f"A{r:.3f},{r:.3f} 0 1 0 {x0:.3f},{y0:.3f}Z"
    )


def refine_svg_paths(svg: str, *, snap: bool = False) -> str:
    """Light post-refinement; snap disabled by default to preserve Bézier smoothness."""
    if not snap:
        return svg
    def refine_d(match: re.Match[str]) -> str:
        d = match.group(1)
        d = snap_path_straights(d)
        return f'd="{d}"'

    return re.sub(r'd="([^"]+)"', refine_d, svg)


def refine_opencv_holes(
    mask: np.ndarray,
    subpaths: list[str],
    contours: list[np.ndarray],
    hierarchy: np.ndarray,
    min_area: float,
) -> list[str]:
    """Replace small hole contours with circle arcs when fitting well."""
    h = hierarchy[0]
    refined = list(subpaths)
    idx = 0
    for i, contour in enumerate(contours):
        area = cv2.contourArea(contour)
        if area < min_area:
            continue
        if h[i][3] == -1:
            idx += 1
            continue
        # hole — try arc fit for P-bowl-like counters
        if 8000 < area < 200000:
            arc = fit_circle_arc(contour)
            if arc and idx < len(refined):
                refined[idx] = arc
        idx += 1
    return refined
