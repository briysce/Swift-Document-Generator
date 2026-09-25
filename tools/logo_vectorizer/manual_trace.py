"""
Manual-quality raster→path fitting.

Mimics how a professional designer manually traces a raster:
  1. Extract dense subpixel contour from a binary mask.
  2. Detect *sharp corners* (turn angle above threshold) so straight-line joins
     stay crisp instead of getting smoothed into blobs.
  3. Insert extra *anchor* points at curvature-inflection and tangent extrema
     (points where the tangent aligns with H/V) — the same places a designer
     would drop a Bezier anchor when tracing.
  4. Fit each segment between anchors:
     - short/near-straight runs  → line (H / V / L, with axis snap)
     - smooth curved runs        → single cubic Bezier (Schneider least-squares)
     - large residual            → recursive split at max-error point
  5. Emit compact SVG path data. Result is few well-placed anchors, sharp
     corners where intended, smooth Beziers where intended.

The fitter is intentionally geometry-only; upstream `analyze.py` picks the
config per raster (blur, corner-angle, error tolerance, etc.).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class ManualTraceConfig:
    """Per-raster tunables for the manual-quality fitter."""

    corner_angle_deg: float = 42.0
    """Local turn angle above which a point is treated as a *sharp corner*."""

    corner_window: int = 3
    """Neighborhood radius (in contour samples) for measuring the turn angle."""

    max_error_px: float = 0.9
    """Max allowed Bezier fit error at trace resolution (before Newton iterations)."""

    straight_dev_px: float = 0.6
    """Max chord deviation to treat an arc as a straight line segment."""

    axis_snap_deg: float = 1.75
    """Straight lines within this angle of H/V/±45° snap to that axis."""

    smooth_sigma: float = 0.9
    """Gaussian sigma (contour samples) used to denoise before analysis."""

    max_arc_between_anchors: float = 55.0
    """Cap segment arc-length so no single Bezier hugs too much curve."""

    min_area: float = 12.0
    """Discard contours smaller than this (raster-scale pixels)."""

    output_scale: float = 1.0
    """Coordinates are divided by this before formatting (upscale → source px)."""

    coord_precision: int = 3
    """Decimal digits for coordinates in the emitted SVG."""

    max_bezier_recursion: int = 5


# ---------------------------------------------------------------------------
# Contour prep
# ---------------------------------------------------------------------------

def _extract_contours(
    mask: np.ndarray, min_area: float
) -> tuple[list[np.ndarray], np.ndarray]:
    """Return (contours, hierarchy) using RETR_CCOMP for evenodd hole support."""
    if mask.dtype != np.uint8:
        mask = mask.astype(np.uint8)
    if mask.max() == 1:
        mask = mask * 255
    contours, hierarchy = cv2.findContours(
        mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE
    )
    if hierarchy is None or not contours:
        return [], np.empty((0,))
    kept: list[np.ndarray] = []
    kept_h: list[np.ndarray] = []
    for i, c in enumerate(contours):
        area = cv2.contourArea(c)
        if area < min_area:
            continue
        kept.append(c.reshape(-1, 2).astype(np.float64))
        kept_h.append(hierarchy[0][i])
    return kept, np.array(kept_h) if kept_h else np.empty((0,))


def _smooth_ring(ring: np.ndarray, sigma: float) -> np.ndarray:
    """Circular Gaussian smoothing of a closed contour (keeps corners crisp
    because the kernel is small; sub-pixel jitter is removed)."""
    n = len(ring)
    if n < 6 or sigma <= 0:
        return ring
    radius = max(1, int(math.ceil(3.0 * sigma)))
    k = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-(k * k) / (2.0 * sigma * sigma))
    kernel /= kernel.sum()
    pad = np.concatenate([ring[-radius:], ring, ring[:radius]], axis=0)
    xs = np.convolve(pad[:, 0], kernel, mode="valid")
    ys = np.convolve(pad[:, 1], kernel, mode="valid")
    return np.stack([xs, ys], axis=1)


# ---------------------------------------------------------------------------
# Corner + extrema detection
# ---------------------------------------------------------------------------

def _turn_angles(ring: np.ndarray, window: int) -> np.ndarray:
    """Angle (rad, 0..pi) between incoming and outgoing tangents at each vertex."""
    n = len(ring)
    idx = np.arange(n)
    prev_i = (idx - window) % n
    next_i = (idx + window) % n
    a = ring[idx] - ring[prev_i]
    b = ring[next_i] - ring[idx]
    la = np.linalg.norm(a, axis=1) + 1e-12
    lb = np.linalg.norm(b, axis=1) + 1e-12
    cos = np.clip((a * b).sum(axis=1) / (la * lb), -1.0, 1.0)
    return np.arccos(cos)


def _detect_corners(
    ring: np.ndarray, cfg: ManualTraceConfig
) -> list[int]:
    """Return indices of sharp corner points (local maxima above threshold).

    A point counts as a corner only when the turn is above threshold *and*
    the two neighbouring pixels form an actual angle in geometry (i.e. the
    tangent flips within a small stretch). Micro-jitter on nominally flat
    edges from anti-aliasing is filtered out by requiring that the turn is
    still substantial when computed across a wider window.
    """
    n = len(ring)
    if n < 8:
        return []
    thr = math.radians(cfg.corner_angle_deg)
    win_small = cfg.corner_window
    win_large = max(2 * win_small, 5)
    angles = _turn_angles(ring, win_small)
    coarse = _turn_angles(ring, win_large)
    # Require both windows to agree so pixel jitter doesn't produce a corner.
    above = (angles > thr) & (coarse > (thr * 0.6))
    if not above.any():
        return []

    # Non-maximum suppression in a small window so a rounded corner isn't
    # emitted as many corners.
    nms_win = max(2, win_large)
    corners: list[int] = []
    used = np.zeros(n, dtype=bool)
    order = np.argsort(-angles)
    for i in order:
        if not above[i] or used[i]:
            continue
        corners.append(int(i))
        for j in range(-nms_win, nms_win + 1):
            used[(i + j) % n] = True
    corners.sort()
    return corners


def _tangent(ring: np.ndarray, i: int, window: int) -> np.ndarray:
    n = len(ring)
    p0 = ring[(i - window) % n]
    p1 = ring[(i + window) % n]
    v = p1 - p0
    ln = np.linalg.norm(v)
    if ln < 1e-9:
        return np.array([1.0, 0.0])
    return v / ln


def _detect_extrema_and_inflections(
    ring: np.ndarray, cfg: ManualTraceConfig, corner_idx: set[int]
) -> list[int]:
    """
    Between corners, add anchors at:
        - tangent extrema (points where dy=0 or dx=0 — same anchors a
          designer would use for the top/side of an arc)
        - curvature-sign inflections (S-curve transitions)
    """
    n = len(ring)
    if n < 12:
        return []

    win = cfg.corner_window
    tangents = np.zeros((n, 2), dtype=np.float64)
    for i in range(n):
        tangents[i] = _tangent(ring, i, win)

    dx = tangents[:, 0]
    dy = tangents[:, 1]

    extras: list[int] = []
    # Zero-crossings of dx and dy → tangent aligns with vertical / horizontal.
    for series in (dx, dy):
        # Sign array with zero-treated-as-positive.
        s = np.sign(series)
        s[s == 0] = 1
        prev = np.roll(s, 1)
        crosses = np.where(prev != s)[0]
        for i in crosses:
            if i in corner_idx or ((i - 1) % n) in corner_idx:
                continue
            extras.append(int(i))

    # Curvature sign changes → inflection anchors on S-shapes.
    curvature = np.zeros(n, dtype=np.float64)
    for i in range(n):
        pa = ring[(i - win) % n]
        pb = ring[i]
        pc = ring[(i + win) % n]
        ab = pb - pa
        bc = pc - pb
        curvature[i] = ab[0] * bc[1] - ab[1] * bc[0]
    csign = np.sign(curvature)
    csign[csign == 0] = 1
    prev = np.roll(csign, 1)
    for i in np.where(prev != csign)[0]:
        if i in corner_idx or ((i - 1) % n) in corner_idx:
            continue
        extras.append(int(i))

    # De-duplicate near-neighbors.
    extras.sort()
    if not extras:
        return []
    out: list[int] = [extras[0]]
    for i in extras[1:]:
        if i - out[-1] > max(4, cfg.corner_window):
            out.append(i)
    # Also make sure we don't double-report positions right next to a corner.
    return [i for i in out if not any(abs(i - c) <= win for c in corner_idx)]


def _coalesce_collinear(
    ring: np.ndarray,
    anchors: list[int],
    tolerance_px: float,
    corner_set: set[int],
) -> list[int]:
    """
    Drop anchors that lie on an approximately straight run between neighbours.

    This is what makes long flat edges (bar rectangles, letter stems) collapse
    into a single line command instead of dozens of collinear H/V hops.
    Corners are always preserved.
    """
    if len(anchors) < 3:
        return anchors
    changed = True
    kept = list(anchors)
    while changed and len(kept) > 3:
        changed = False
        new_kept: list[int] = []
        n = len(kept)
        for i in range(n):
            idx = kept[i]
            prev_idx = kept[(i - 1) % n]
            next_idx = kept[(i + 1) % n]
            if idx in corner_set:
                new_kept.append(idx)
                continue
            seg = _segment_points(ring, prev_idx, next_idx)
            if len(seg) < 3:
                new_kept.append(idx)
                continue
            if _max_dev_from_chord(seg) <= tolerance_px:
                changed = True
                continue
            new_kept.append(idx)
        if len(new_kept) < 3:
            break
        kept = new_kept
    return kept


def _enforce_max_arc(
    ring: np.ndarray, anchors: list[int], max_arc: float
) -> list[int]:
    """Insert extra anchors so no smooth run exceeds max_arc pixels."""
    if not anchors or max_arc <= 0:
        return anchors
    n = len(ring)
    seg_lengths = np.linalg.norm(np.diff(ring, axis=0, append=ring[:1]), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg_lengths)])

    def arc_between(i0: int, i1: int) -> float:
        if i0 <= i1:
            return cum[i1] - cum[i0]
        return (cum[-1] - cum[i0]) + cum[i1]

    out = list(anchors)
    changed = True
    while changed:
        changed = False
        new_anchors: list[int] = []
        for k, i0 in enumerate(out):
            i1 = out[(k + 1) % len(out)]
            new_anchors.append(i0)
            arc = arc_between(i0, i1)
            if arc > max_arc:
                # Add midpoint by arc-length.
                target = (cum[i0] + arc / 2.0) if i0 <= i1 else (cum[i0] + arc / 2.0) % cum[-1]
                mid = int(np.searchsorted(cum, target) % n)
                if mid != i0 and mid != i1 and mid not in new_anchors:
                    new_anchors.append(mid)
                    changed = True
        out = sorted(set(new_anchors))
    return out


# ---------------------------------------------------------------------------
# Segment fitting
# ---------------------------------------------------------------------------

def _segment_points(ring: np.ndarray, i0: int, i1: int) -> np.ndarray:
    n = len(ring)
    if i0 == i1:
        return ring[[i0]]
    if i0 < i1:
        return ring[i0 : i1 + 1]
    return np.concatenate([ring[i0:], ring[: i1 + 1]], axis=0)


def _max_dev_from_chord(pts: np.ndarray) -> float:
    """Max perpendicular distance from the chord pts[0]→pts[-1]."""
    if len(pts) < 3:
        return 0.0
    a, b = pts[0], pts[-1]
    ab = b - a
    L = np.linalg.norm(ab)
    if L < 1e-9:
        return float(np.linalg.norm(pts - a, axis=1).max())
    n = np.array([-ab[1], ab[0]]) / L
    return float(np.abs((pts - a) @ n).max())


def _snap_axis(a: np.ndarray, b: np.ndarray, snap_deg: float) -> np.ndarray:
    """Rotate segment endpoint towards nearest axis (0/45/90/135) if within snap_deg."""
    v = b - a
    L = math.hypot(v[0], v[1])
    if L < 1e-9:
        return b
    ang = math.degrees(math.atan2(v[1], v[0])) % 180.0
    targets = (0.0, 45.0, 90.0, 135.0)
    best = min(targets, key=lambda t: min(abs(ang - t), abs(ang - t - 180)))
    if min(abs(ang - best), abs(ang - best - 180)) > snap_deg:
        return b
    sign_x = 1.0 if v[0] >= 0 else -1.0
    sign_y = 1.0 if v[1] >= 0 else -1.0
    if best in (0.0, 180.0):
        return np.array([a[0] + sign_x * L, a[1]])
    if best == 90.0:
        return np.array([a[0], a[1] + sign_y * L])
    s = L / math.sqrt(2.0)
    return np.array([a[0] + sign_x * s, a[1] + sign_y * s])


def _chord_length_params(pts: np.ndarray) -> np.ndarray:
    if len(pts) < 2:
        return np.array([0.0])
    d = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(d)])
    total = cum[-1] if cum[-1] > 1e-9 else 1.0
    return cum / total


def _fit_bezier(
    pts: np.ndarray,
    t0: np.ndarray,
    t1: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float]:
    """
    Schneider-style least-squares cubic Bezier fit with tangent constraints.

    Returns (P0, P1, P2, P3, max_error) where P0..P3 are the four control
    points of the fitted cubic Bezier that passes through pts[0]/pts[-1] and
    matches the supplied unit tangents at both ends.
    """
    if len(pts) < 2:
        raise ValueError("need at least two points")
    P0 = pts[0]
    P3 = pts[-1]
    if len(pts) == 2:
        return P0, P0.copy(), P3.copy(), P3, 0.0

    u = _chord_length_params(pts)
    # Bernstein basis
    b0 = (1 - u) ** 3
    b1 = 3 * (1 - u) ** 2 * u
    b2 = 3 * (1 - u) * u ** 2
    b3 = u ** 3

    # A[i, 0] = b1 * t0 ; A[i, 1] = b2 * t1
    # rhs = pts[i] - b0*P0 - b3*P3
    A00 = (b1 * b1)[:, None] * (t0 * t0).sum()
    # We solve for alpha0, alpha1 (scalar arm lengths) using normal equations.
    Ct0 = b1[:, None] * t0
    Ct1 = b2[:, None] * t1
    # 2x2 normal-eq matrix
    C = np.zeros((2, 2))
    C[0, 0] = (Ct0 * Ct0).sum()
    C[0, 1] = (Ct0 * Ct1).sum()
    C[1, 0] = C[0, 1]
    C[1, 1] = (Ct1 * Ct1).sum()

    X = np.zeros(2)
    tmp = pts - (b0[:, None] * P0 + b3[:, None] * P3)
    X[0] = (Ct0 * tmp).sum()
    X[1] = (Ct1 * tmp).sum()

    det = C[0, 0] * C[1, 1] - C[0, 1] * C[1, 0]
    if abs(det) < 1e-9:
        # Fallback to chord-length heuristic
        chord = float(np.linalg.norm(P3 - P0))
        alpha0 = alpha1 = chord / 3.0
    else:
        inv = np.array([[C[1, 1], -C[0, 1]], [-C[1, 0], C[0, 0]]]) / det
        alphas = inv @ X
        alpha0, alpha1 = float(alphas[0]), float(alphas[1])
        # Guard degenerate control arms.
        chord = float(np.linalg.norm(P3 - P0))
        eps = max(1e-6, chord * 1e-3)
        if alpha0 < eps or alpha1 < eps or alpha0 > chord * 4 or alpha1 > chord * 4:
            alpha0 = alpha1 = chord / 3.0

    P1 = P0 + alpha0 * t0
    P2 = P3 + alpha1 * t1

    # Compute max fit error (Euclidean).
    max_err = 0.0
    for i, ui in enumerate(u):
        v = (
            (1 - ui) ** 3 * P0
            + 3 * (1 - ui) ** 2 * ui * P1
            + 3 * (1 - ui) * ui ** 2 * P2
            + ui ** 3 * P3
        )
        e = float(np.linalg.norm(v - pts[i]))
        if e > max_err:
            max_err = e
    return P0, P1, P2, P3, max_err


def _newton_reparam(
    pts: np.ndarray,
    P0: np.ndarray,
    P1: np.ndarray,
    P2: np.ndarray,
    P3: np.ndarray,
    u: np.ndarray,
) -> np.ndarray:
    """One Newton-Raphson step refining chord parameters to reduce fit error."""
    new_u = u.copy()
    for i, ui in enumerate(u):
        # Q(u)
        Q = (
            (1 - ui) ** 3 * P0
            + 3 * (1 - ui) ** 2 * ui * P1
            + 3 * (1 - ui) * ui ** 2 * P2
            + ui ** 3 * P3
        )
        # Q'(u)
        Q1 = (
            3 * (1 - ui) ** 2 * (P1 - P0)
            + 6 * (1 - ui) * ui * (P2 - P1)
            + 3 * ui ** 2 * (P3 - P2)
        )
        # Q''(u)
        Q2 = 6 * (1 - ui) * (P2 - 2 * P1 + P0) + 6 * ui * (P3 - 2 * P2 + P1)
        diff = Q - pts[i]
        num = float(diff @ Q1)
        den = float(Q1 @ Q1 + diff @ Q2)
        if abs(den) < 1e-12:
            continue
        new_u[i] = np.clip(ui - num / den, 0.0, 1.0)
    return np.sort(new_u)


def _fit_bezier_iterative(
    pts: np.ndarray, t0: np.ndarray, t1: np.ndarray, cfg: ManualTraceConfig
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float] | None:
    """Fit with up to a couple of Newton-Raphson reparameterizations."""
    P0, P1, P2, P3, err = _fit_bezier(pts, t0, t1)
    if err <= cfg.max_error_px:
        return P0, P1, P2, P3, err
    # Try two Newton refinement rounds.
    u = _chord_length_params(pts)
    for _ in range(2):
        u = _newton_reparam(pts, P0, P1, P2, P3, u)
        # Rebuild fit with refined params (linear least-squares under new u).
        b0 = (1 - u) ** 3
        b1 = 3 * (1 - u) ** 2 * u
        b2 = 3 * (1 - u) * u ** 2
        b3 = u ** 3
        Ct0 = b1[:, None] * t0
        Ct1 = b2[:, None] * t1
        C = np.zeros((2, 2))
        C[0, 0] = (Ct0 * Ct0).sum()
        C[0, 1] = (Ct0 * Ct1).sum()
        C[1, 0] = C[0, 1]
        C[1, 1] = (Ct1 * Ct1).sum()
        tmp = pts - (b0[:, None] * P0 + b3[:, None] * P3)
        X = np.array([(Ct0 * tmp).sum(), (Ct1 * tmp).sum()])
        det = C[0, 0] * C[1, 1] - C[0, 1] * C[1, 0]
        if abs(det) < 1e-9:
            break
        inv = np.array([[C[1, 1], -C[0, 1]], [-C[1, 0], C[0, 0]]]) / det
        alphas = inv @ X
        chord = float(np.linalg.norm(P3 - P0))
        eps = max(1e-6, chord * 1e-3)
        if alphas[0] < eps or alphas[1] < eps or alphas[0] > chord * 4 or alphas[1] > chord * 4:
            break
        P1 = P0 + alphas[0] * t0
        P2 = P3 + alphas[1] * t1
        # Re-measure error
        err = 0.0
        for i, ui in enumerate(u):
            v = (
                (1 - ui) ** 3 * P0
                + 3 * (1 - ui) ** 2 * ui * P1
                + 3 * (1 - ui) * ui ** 2 * P2
                + ui ** 3 * P3
            )
            e = float(np.linalg.norm(v - pts[i]))
            if e > err:
                err = e
        if err <= cfg.max_error_px:
            return P0, P1, P2, P3, err
    if err <= cfg.max_error_px * 2.0:
        # Accept somewhat looser fit before falling back to recursion.
        return P0, P1, P2, P3, err
    return None


def _max_err_index(
    pts: np.ndarray, P0: np.ndarray, P1: np.ndarray, P2: np.ndarray, P3: np.ndarray
) -> int:
    u = _chord_length_params(pts)
    worst = 0
    worst_err = -1.0
    for i, ui in enumerate(u):
        v = (
            (1 - ui) ** 3 * P0
            + 3 * (1 - ui) ** 2 * ui * P1
            + 3 * (1 - ui) * ui ** 2 * P2
            + ui ** 3 * P3
        )
        e = float(np.linalg.norm(v - pts[i]))
        if e > worst_err:
            worst_err = e
            worst = i
    return max(1, min(len(pts) - 2, worst))


def _segment_unit_tangent(pts: np.ndarray, at_start: bool) -> np.ndarray:
    """Unit tangent at first / last point using a small forward diff."""
    if len(pts) < 2:
        return np.array([1.0, 0.0])
    look = min(3, len(pts) - 1)
    if at_start:
        v = pts[look] - pts[0]
    else:
        v = pts[-1] - pts[-1 - look]
    n = np.linalg.norm(v)
    if n < 1e-9:
        return np.array([1.0, 0.0])
    return v / n


def _fit_recursive(
    pts: np.ndarray,
    t0: np.ndarray,
    t1: np.ndarray,
    cfg: ManualTraceConfig,
    depth: int = 0,
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    """Return list of cubic Bezier segments approximating pts."""
    fit = _fit_bezier_iterative(pts, t0, t1, cfg)
    if fit is not None:
        return [(fit[0], fit[1], fit[2], fit[3])]
    if depth >= cfg.max_bezier_recursion or len(pts) < 6:
        # Give up on curve fit; emit as polyline is caller's job.
        return []
    # Estimate raw fit purely for split-index (ignore tangent constraints).
    P0, P1, P2, P3, _ = _fit_bezier(pts, t0, t1)
    split = _max_err_index(pts, P0, P1, P2, P3)
    left = pts[: split + 1]
    right = pts[split:]
    # Tangent at split matches curve direction there.
    t_mid = _segment_unit_tangent(right, at_start=True)
    left_fits = _fit_recursive(left, t0, t_mid, cfg, depth + 1)
    right_fits = _fit_recursive(right, t_mid, t1, cfg, depth + 1)
    return left_fits + right_fits


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------

def _fmt(v: float, prec: int) -> str:
    s = f"{v:.{prec}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _emit_line(
    cursor: np.ndarray, end: np.ndarray, cfg: ManualTraceConfig
) -> tuple[str, np.ndarray]:
    scale = cfg.output_scale
    prec = cfg.coord_precision
    ex = end[0] / scale
    ey = end[1] / scale
    cx = cursor[0] / scale
    cy = cursor[1] / scale
    if abs(ey - cy) < 10 ** (-prec):
        return f"H{_fmt(ex, prec)}", end
    if abs(ex - cx) < 10 ** (-prec):
        return f"V{_fmt(ey, prec)}", end
    return f"L{_fmt(ex, prec)},{_fmt(ey, prec)}", end


def _emit_cubic(
    P1: np.ndarray, P2: np.ndarray, P3: np.ndarray, cfg: ManualTraceConfig
) -> str:
    s = cfg.output_scale
    p = cfg.coord_precision
    return (
        f"C{_fmt(P1[0]/s, p)},{_fmt(P1[1]/s, p)} "
        f"{_fmt(P2[0]/s, p)},{_fmt(P2[1]/s, p)} "
        f"{_fmt(P3[0]/s, p)},{_fmt(P3[1]/s, p)}"
    )


# ---------------------------------------------------------------------------
# Top-level per-contour trace
# ---------------------------------------------------------------------------

def _trace_ring(ring: np.ndarray, cfg: ManualTraceConfig) -> str:
    ring = _smooth_ring(ring, cfg.smooth_sigma)
    if len(ring) < 6:
        return ""

    corner_idx = _detect_corners(ring, cfg)
    corner_set = set(corner_idx)
    extras = _detect_extrema_and_inflections(ring, cfg, corner_set)
    anchors = sorted(set(corner_idx + extras))
    if not anchors:
        # Very smooth blob with no strong corners — split into 4 by arc-length
        # so we still fit sensibly.
        n = len(ring)
        anchors = [0, n // 4, n // 2, (3 * n) // 4]

    # Coalesce collinear anchors before capping arc length so long flat edges
    # emit as a single line, not dozens of little H/V hops.
    anchors = _coalesce_collinear(
        ring, anchors, cfg.straight_dev_px, corner_set
    )
    anchors = _enforce_max_arc(ring, anchors, cfg.max_arc_between_anchors)
    if not anchors:
        return ""

    scale = cfg.output_scale
    prec = cfg.coord_precision
    parts: list[str] = []
    start = ring[anchors[0]] / scale
    parts.append(f"M{_fmt(start[0], prec)},{_fmt(start[1], prec)}")
    cursor = ring[anchors[0]].copy()

    for k, i0 in enumerate(anchors):
        i1 = anchors[(k + 1) % len(anchors)]
        seg = _segment_points(ring, i0, i1)
        if len(seg) < 2:
            continue

        # Straight test — chord deviation
        max_dev = _max_dev_from_chord(seg)
        end_point = ring[i1].copy()
        if max_dev <= cfg.straight_dev_px:
            snapped = _snap_axis(cursor, end_point, cfg.axis_snap_deg)
            cmd, _ = _emit_line(cursor, snapped, cfg)
            parts.append(cmd)
            cursor = snapped
            continue

        is_corner_start = i0 in corner_set
        is_corner_end = i1 in corner_set
        # Tangent constraints: at a corner, use chord direction of this segment.
        # Otherwise, use finite-difference tangent so adjacent segments join smoothly.
        if is_corner_start:
            v = seg[min(2, len(seg) - 1)] - seg[0]
            n_ = np.linalg.norm(v)
            t0 = v / n_ if n_ > 1e-9 else np.array([1.0, 0.0])
        else:
            t0 = _tangent(ring, i0, cfg.corner_window)
        if is_corner_end:
            v = seg[-1] - seg[max(0, len(seg) - 3)]
            n_ = np.linalg.norm(v)
            t1 = v / n_ if n_ > 1e-9 else np.array([1.0, 0.0])
        else:
            # Tangent at end should point OUT of the segment (curve derivative);
            # we express control arm as P2 = P3 + alpha1 * t1 with alpha1 > 0,
            # so t1 points *backwards* along the outgoing direction.
            t_forward = _tangent(ring, i1, cfg.corner_window)
            t1 = -t_forward

        # If the "smooth" tangent came out reversed (contour orientation),
        # normalize sign vs segment chord.
        chord = seg[-1] - seg[0]
        if float(t0 @ chord) < 0:
            t0 = -t0
        if float(t1 @ (-chord)) < 0:
            t1 = -t1

        fits = _fit_recursive(seg, t0, t1, cfg)
        if not fits:
            # Fall back to polyline through interior sample points — matches
            # geometry exactly, worst case.
            step = max(1, len(seg) // 8)
            for j in range(step, len(seg), step):
                snapped = _snap_axis(cursor, seg[j], cfg.axis_snap_deg)
                cmd, _ = _emit_line(cursor, snapped, cfg)
                parts.append(cmd)
                cursor = snapped
            continue

        for _, P1, P2, P3 in fits:
            parts.append(_emit_cubic(P1, P2, P3, cfg))
            cursor = P3.copy()

    parts.append("Z")
    return "".join(parts)


@dataclass
class ManualTraceResult:
    """Manual-quality trace output for a single binary mask."""

    d_by_contour: list[str] = field(default_factory=list)
    contour_count: int = 0
    hole_count: int = 0

    @property
    def combined_d(self) -> str:
        return "".join(self.d_by_contour)


def trace_mask(
    mask: np.ndarray, cfg: ManualTraceConfig | None = None
) -> ManualTraceResult:
    """Trace a binary mask with manual-quality Bezier fitting."""
    cfg = cfg or ManualTraceConfig()
    contours, hierarchy = _extract_contours(mask, cfg.min_area)
    result = ManualTraceResult(contour_count=len(contours))
    for i, contour in enumerate(contours):
        d = _trace_ring(contour, cfg)
        if d:
            result.d_by_contour.append(d)
        if hierarchy.size and hierarchy[i][3] != -1:
            result.hole_count += 1
    return result
