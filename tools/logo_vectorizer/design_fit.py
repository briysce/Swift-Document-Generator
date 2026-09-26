"""Designed geometry from a sketch: the path a designer would draw over it.

A raster is a sketch, not the target. A one-pixel outline in a blurry raster is
a grey smear; in the vector it is a crisp line. A hand-shaky curve in the
sketch is one clean curve in the vector. So nothing here copies pixels. The
sketch is read for what it *means*, and the path is drawn to that meaning:

1. Coverage, not colour. Each pixel is unmixed into how much of each ink it
   holds (`unmix`). A blurred edge is a ramp from 1 to 0; the true edge is
   where coverage crosses one half, found to a fraction of a pixel. This is
   the principled form of the "blur, then threshold" trick.
2. Corners are found where the outline turns sharply over a short run.
3. Between corners, each run is drawn as a straight line if it is straight,
   otherwise as the fewest smooth cubic curves that stay within a tolerance
   band of the edge (a fraction of a source pixel) — the pen-tool rule of
   minimum anchors.
4. Corners the blur rounded off are restored: neighbouring lines are extended
   to meet where the designer's corner was.
5. Intent is imposed: near-horizontals become horizontal, and stems that are
   nearly parallel share one slant (`snap_lines`).

The result is judged by two things only: does it explain the sketch when
rendered at the sketch's resolution, and is it made the way a designer makes
a vector (few anchors, straight lines straight, curves fair).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# --------------------------------------------------------------------------
# coverage
# --------------------------------------------------------------------------


def unmix(rgb: np.ndarray, inks: list[tuple[int, int, int]]) -> np.ndarray:
    """Per-pixel share of each ink, summing to one (H x W x K).

    Least squares with the sum-to-one constraint, then clipped. With the page
    among the inks, a blurred edge pixel between orange and white reads as,
    say, 0.4 orange and 0.6 page — which is exactly the coverage the edge
    needs.
    """
    e = np.asarray(inks, np.float64)                  # K x 3
    k = len(e)
    x = rgb.reshape(-1, 3).astype(np.float64)
    # Eliminate the constraint: w_k = 1 - sum(w_0..k-2).
    base = e[-1]
    a = (e[:-1] - base).T                             # 3 x (K-1)
    b = (x - base).T                                  # 3 x N
    w, *_ = np.linalg.lstsq(a, b, rcond=None)         # (K-1) x N
    w = np.vstack([w, 1.0 - w.sum(axis=0)]).T
    w = np.clip(w, 0.0, None)
    w /= np.maximum(w.sum(axis=1, keepdims=True), 1e-9)
    return w.reshape(rgb.shape[:2] + (k,))


def contours(cov: np.ndarray, level: float = 0.5, min_len: float = 12.0) -> list[np.ndarray]:
    """Closed sub-pixel outlines where coverage crosses `level`, as (N, 2) x,y."""
    from skimage import measure

    padded = np.pad(cov, 1, constant_values=0.0)
    out = []
    for c in measure.find_contours(padded, level):
        # Pixel (i, j) covers [j, j+1] in the drawing, so its centre is j + 0.5.
        xy = np.stack([c[:, 1] - 0.5, c[:, 0] - 0.5], axis=1)
        if np.hypot(*(xy[0] - xy[-1])) > 1.0:
            continue                                  # open: touches nothing we pad against
        if _length(xy) >= min_len:
            out.append(xy[:-1])
    return out


def _length(p: np.ndarray) -> float:
    d = np.diff(np.vstack([p, p[:1]]), axis=0)
    return float(np.hypot(d[:, 0], d[:, 1]).sum())


def resample(p: np.ndarray, step: float = 0.5) -> np.ndarray:
    """Closed polyline resampled at a uniform arc-length step."""
    q = np.vstack([p, p[:1]])
    seg = np.hypot(*np.diff(q, axis=0).T)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    n = max(8, int(s[-1] / step))
    t = np.linspace(0.0, s[-1], n, endpoint=False)
    return np.stack([np.interp(t, s, q[:, 0]), np.interp(t, s, q[:, 1])], axis=1)


# --------------------------------------------------------------------------
# corners
# --------------------------------------------------------------------------


def corners(p: np.ndarray, *, step: float, window: float = 2.5, angle: float = 32.0) -> list[int]:
    """Indices where the outline turns by more than `angle` degrees over
    `window` pixels either side — a corner the blur may have rounded."""
    n = len(p)
    k = max(2, int(round(window / step)))
    back = p - np.roll(p, k, axis=0)
    fwd = np.roll(p, -k, axis=0) - p
    a1 = np.arctan2(back[:, 1], back[:, 0])
    a2 = np.arctan2(fwd[:, 1], fwd[:, 0])
    turn = np.degrees(np.abs((a2 - a1 + np.pi) % (2 * np.pi) - np.pi))
    idx = []
    for i in range(n):
        if turn[i] < angle:
            continue
        lo = [turn[(i + j) % n] for j in range(-k, k + 1)]
        if turn[i] >= max(lo) and not any(abs(i - j) < k or abs(i - j) > n - k for j in idx):
            idx.append(i)
    return sorted(idx)


# --------------------------------------------------------------------------
# segments
# --------------------------------------------------------------------------


@dataclass
class Seg:
    """A line (two points) or a cubic Bezier (four points)."""

    pts: np.ndarray

    @property
    def is_line(self) -> bool:
        return len(self.pts) == 2

    @property
    def start(self) -> np.ndarray:
        return self.pts[0]

    @property
    def end(self) -> np.ndarray:
        return self.pts[-1]


def _bezier(ctrl: np.ndarray, t: np.ndarray) -> np.ndarray:
    t = t[:, None]
    mt = 1 - t
    return (mt ** 3) * ctrl[0] + 3 * (mt ** 2) * t * ctrl[1] + 3 * mt * (t ** 2) * ctrl[2] + (t ** 3) * ctrl[3]


def _chord_params(pts: np.ndarray) -> np.ndarray:
    d = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    return d / d[-1] if d[-1] > 0 else np.linspace(0, 1, len(pts))


def _fit_cubic(pts: np.ndarray, t0: np.ndarray, t1: np.ndarray) -> np.ndarray:
    """Least-squares cubic with fixed ends and end tangent directions."""
    u = _chord_params(pts)
    for _ in range(4):
        b0, b1, b2, b3 = (1 - u) ** 3, 3 * u * (1 - u) ** 2, 3 * u ** 2 * (1 - u), u ** 3
        a1 = b1[:, None] * t0
        a2 = b2[:, None] * t1
        c = np.array([[np.sum(a1 * a1), np.sum(a1 * a2)], [np.sum(a1 * a2), np.sum(a2 * a2)]])
        r = pts - (b0 + b1)[:, None] * pts[0] - (b2 + b3)[:, None] * pts[-1]
        x = np.array([np.sum(a1 * r), np.sum(a2 * r)])
        chord = float(np.hypot(*(pts[-1] - pts[0])))
        try:
            al, be = np.linalg.solve(c, x)
        except np.linalg.LinAlgError:
            al = be = chord / 3.0
        if al <= 1e-6 or be <= 1e-6:
            al = be = chord / 3.0
        ctrl = np.array([pts[0], pts[0] + al * t0, pts[-1] + be * t1, pts[-1]])
        # One Newton step per point to re-parameterize (Schneider).
        q = _bezier(ctrl, u)
        d1 = 3 * ((1 - u) ** 2)[:, None] * (ctrl[1] - ctrl[0]) + 6 * ((1 - u) * u)[:, None] * (ctrl[2] - ctrl[1]) + 3 * (u ** 2)[:, None] * (ctrl[3] - ctrl[2])
        d2 = 6 * (1 - u)[:, None] * (ctrl[2] - 2 * ctrl[1] + ctrl[0]) + 6 * u[:, None] * (ctrl[3] - 2 * ctrl[2] + ctrl[1])
        num = np.sum((q - pts) * d1, axis=1)
        den = np.sum(d1 * d1 + (q - pts) * d2, axis=1)
        u = np.clip(u - np.where(np.abs(den) > 1e-9, num / den, 0.0), 0.0, 1.0)
        u[0], u[-1] = 0.0, 1.0
    return ctrl


def _max_error(ctrl: np.ndarray, pts: np.ndarray) -> tuple[float, int]:
    dense = _bezier(ctrl, np.linspace(0, 1, 200))
    d = np.min(np.hypot(pts[:, None, 0] - dense[None, :, 0], pts[:, None, 1] - dense[None, :, 1]), axis=1)
    i = int(np.argmax(d))
    return float(d[i]), i


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.hypot(*v))
    return v / n if n > 1e-9 else v


def _line_error(pts: np.ndarray) -> float:
    a, b = pts[0], pts[-1]
    d = b - a
    n = float(np.hypot(*d))
    if n < 1e-9:
        return float(np.max(np.hypot(*(pts - a).T)))
    v = pts - a
    return float(np.max(np.abs(d[0] * v[:, 1] - d[1] * v[:, 0])) / n)


def _tls_line(pts: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Total-least-squares line: (point, unit direction, max perpendicular error)."""
    c = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - c)
    d = vt[0]
    v = pts - c
    err = float(np.max(np.abs(d[0] * v[:, 1] - d[1] * v[:, 0]))) if len(pts) > 2 else 0.0
    return c, d, err


def fit_edge(pts: np.ndarray, tol: float, trim: int) -> list[Seg]:
    """A corner-to-corner run, judged on its middle.

    Blur rounds a corner over a pixel or two, so the ends of every run bend
    toward the next one. Judged whole, no edge next to a corner is straight.
    The rounded ends are set aside, the middle decides line or curve, and the
    ends are recovered afterwards by meeting the neighbours (`_sharpen`).
    """
    t = min(trim, max(1, (len(pts) - 3) // 4))    # short edges keep at least half
    core = pts[t: len(pts) - t]
    c, d, err = _tls_line(core)
    if len(core) >= 3 and err <= tol:
        if np.dot(core[-1] - core[0], d) < 0:
            d = -d
        a = c + np.dot(core[0] - c, d) * d
        b = c + np.dot(core[-1] - c, d) * d
        return [Seg(np.array([a, b]))]
    return fit_run(core, tol)


def fit_run(pts: np.ndarray, tol: float, t0: np.ndarray | None = None, t1: np.ndarray | None = None) -> list[Seg]:
    """A run as a line, or the fewest cubics within `tol`."""
    if len(pts) < 3 or _line_error(pts) <= tol:
        return [Seg(np.array([pts[0], pts[-1]]))]
    k = min(len(pts) - 1, max(2, len(pts) // 8))
    t0 = _unit(pts[k] - pts[0]) if t0 is None else t0
    t1 = _unit(pts[-1 - k] - pts[-1]) if t1 is None else t1
    ctrl = _fit_cubic(pts, t0, t1)
    err, i = _max_error(ctrl, pts)
    if err <= tol or len(pts) < 8:
        return [Seg(ctrl)]
    i = int(np.clip(i, 3, len(pts) - 4))
    tm = _unit(pts[min(len(pts) - 1, i + 2)] - pts[max(0, i - 2)])
    return fit_run(pts[: i + 1], tol, t0, -tm) + fit_run(pts[i:], tol, tm, t1)


# --------------------------------------------------------------------------
# loops
# --------------------------------------------------------------------------


@dataclass
class Loop:
    segs: list[Seg] = field(default_factory=list)


def _intersect(p1, d1, p2, d2):
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-9:
        return None
    t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / den
    return p1 + t * d1


def fit_loop(outline: np.ndarray, *, tol: float = 0.3, step: float = 0.35, corner_window: float = 2.5,
             corner_angle: float = 32.0, corner_blur: float = 1.6, sharpen_reach: float = 3.0) -> Loop:
    """One closed outline as designed segments.

    `corner_blur` is how far (px) blur rounds a corner; that much of each run's
    ends is set aside when deciding what the run is.
    """
    p = resample(outline, step)
    cs = corners(p, step=step, window=corner_window, angle=corner_angle)
    if not cs:
        # No corner: one smooth closed shape.
        return Loop(fit_run(np.vstack([p, p[:1]]), tol))
    trim = max(1, int(round(corner_blur / step)))
    segs: list[Seg] = []
    for a, b in zip(cs, cs[1:] + [cs[0] + len(p)]):
        segs += fit_edge(p[np.arange(a, b + 1) % len(p)], tol, trim)
    loop = Loop(segs)
    _sharpen(loop, sharpen_reach)
    _close_gaps(loop)
    return loop


def _close_gaps(loop: Loop) -> None:
    """Any neighbours still apart (no nearby corner to meet at) join halfway."""
    n = len(loop.segs)
    for i in range(n):
        a, b = loop.segs[i], loop.segs[(i + 1) % n]
        if float(np.hypot(*(a.end - b.start))) > 1e-6:
            m = (a.end + b.start) / 2.0
            _move_end(a, m)
            _move_start(b, m)


def _end_dir(s: Seg, at_end: bool) -> np.ndarray:
    if s.is_line:
        d = s.pts[1] - s.pts[0]
    else:
        d = (s.pts[3] - s.pts[2]) if at_end else (s.pts[1] - s.pts[0])
        if float(np.hypot(*d)) < 1e-6:
            d = s.pts[3] - s.pts[0]
    return _unit(d)


def _sharpen(loop: Loop, reach: float) -> None:
    """Put back the corners blur rounded off: where two segments meet at a turn,
    move the shared point to where their end tangents intersect, if that is
    close by. Two straight serif edges meeting at a soft 2-px arc become the
    point they were drawn to."""
    n = len(loop.segs)
    for i in range(n):
        a, b = loop.segs[i], loop.segs[(i + 1) % n]
        da, db = _end_dir(a, True), _end_dir(b, False)
        turn = math.degrees(math.acos(float(np.clip(np.dot(da, db), -1.0, 1.0))))
        if turn < 20.0:
            continue
        x = _intersect(a.end, da, b.start, db)
        if x is None:
            continue
        # The corner must lie ahead of both ends, within reach of each.
        if np.dot(x - a.end, da) < -0.5 or np.dot(x - b.start, db) > 0.5:
            continue
        if float(np.hypot(*(x - a.end))) > reach or float(np.hypot(*(x - b.start))) > reach:
            continue
        _move_start(b, x)
        _move_end(a, x)


def _move_end(s: Seg, x: np.ndarray) -> None:
    delta = x - s.pts[-1]
    s.pts = s.pts.copy()
    s.pts[-1] = x
    if not s.is_line:
        s.pts[2] = s.pts[2] + delta


def _move_start(s: Seg, x: np.ndarray) -> None:
    delta = x - s.pts[0]
    s.pts = s.pts.copy()
    s.pts[0] = x
    if not s.is_line:
        s.pts[1] = s.pts[1] + delta


# --------------------------------------------------------------------------
# intent
# --------------------------------------------------------------------------


def _angle(s: Seg) -> float:
    d = s.pts[1] - s.pts[0]
    return math.degrees(math.atan2(d[1], d[0])) % 180.0


def snap_lines(loops: list[Loop], *, axis_tol: float = 4.0, slant_tol: float = 5.0,
               min_len: float = 3.0, level_tol: float = 0.8) -> dict:
    """Make near-horizontals horizontal, near-verticals vertical, and give
    nearly parallel stems one shared slant; then put horizontals that nearly
    share a height on exactly that height (baselines, cap lines, serifs).

    Only lines long enough to carry intent are touched. Returns what it did.
    """
    lines = [(li, si, s) for li, lp in enumerate(loops) for si, s in enumerate(lp.segs)
             if s.is_line and float(np.hypot(*(s.pts[1] - s.pts[0]))) >= min_len]
    report = {"horizontal": 0, "vertical": 0, "slant": None, "slanted": 0, "levels": 0}
    others = []
    for _li, _si, s in lines:
        a = _angle(s)
        if min(a, 180 - a) <= axis_tol:
            y = float(np.mean(s.pts[:, 1]))
            s.pts = s.pts.copy(); s.pts[:, 1] = y
            report["horizontal"] += 1
        elif abs(a - 90) <= axis_tol:
            x = float(np.mean(s.pts[:, 0]))
            s.pts = s.pts.copy(); s.pts[:, 0] = x
            report["vertical"] += 1
        else:
            others.append(s)
    if len(others) >= 3:
        # The dominant slant: the length-weighted angle most lines agree with.
        angs = np.array([_angle(s) for s in others])
        lens = np.array([np.hypot(*(s.pts[1] - s.pts[0])) for s in others])
        best, score = None, -1.0
        for a in angs:
            m = np.abs((angs - a + 90) % 180 - 90) <= slant_tol
            if lens[m].sum() > score:
                score, best = float(lens[m].sum()), m
        slant = float(np.average(angs[best], weights=lens[best]))
        report["slant"] = round(slant, 2)
        for s, keep in zip(others, best):
            if not keep:
                continue
            mid = s.pts.mean(axis=0)
            half = float(np.hypot(*(s.pts[1] - s.pts[0]))) / 2.0
            d = np.array([math.cos(math.radians(slant)), math.sin(math.radians(slant))])
            if np.dot(s.pts[1] - s.pts[0], d) < 0:
                d = -d
            s.pts = np.array([mid - half * d, mid + half * d])
            report["slanted"] += 1
    # Shared heights.
    hs = [s for _li, _si, s in lines if abs(s.pts[0, 1] - s.pts[1, 1]) < 1e-9]
    ys = sorted(float(s.pts[0, 1]) for s in hs)
    groups: list[list[float]] = []
    for y in ys:
        if groups and y - groups[-1][-1] <= level_tol:
            groups[-1].append(y)
        else:
            groups.append([y])
    for g in groups:
        if len(g) < 2:
            continue
        level = float(np.mean(g))
        for s in hs:
            if g[0] - 1e-9 <= s.pts[0, 1] <= g[-1] + 1e-9:
                s.pts = s.pts.copy(); s.pts[:, 1] = level
        report["levels"] += 1
    for lp in loops:
        _reconnect(lp)
    return report


def _reconnect(loop: Loop) -> None:
    """After snapping, make each loop continuous again: two lines meet at their
    intersection; a curve follows the line it joins."""
    n = len(loop.segs)
    for i in range(n):
        a, b = loop.segs[i], loop.segs[(i + 1) % n]
        if float(np.hypot(*(a.end - b.start))) < 1e-6:
            continue
        if a.is_line and b.is_line:
            x = _intersect(a.pts[0], _unit(a.pts[1] - a.pts[0]), b.pts[0], _unit(b.pts[1] - b.pts[0]))
            if x is not None and float(np.hypot(*(x - a.end))) < 6.0:
                _move_end(a, x); _move_start(b, x)
                continue
        if a.is_line:
            _move_start(b, a.end.copy())
        else:
            _move_end(a, b.start.copy())


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------


def loop_path(loop: Loop, scale: float = 1.0, digits: int = 2) -> str:
    def f(v):
        return f"{v * scale:.{digits}f}".rstrip("0").rstrip(".")

    s0 = loop.segs[0].start
    out = [f"M{f(s0[0])} {f(s0[1])}"]
    for s in loop.segs:
        if s.is_line:
            out.append(f"L{f(s.end[0])} {f(s.end[1])}")
        else:
            c1, c2, e = s.pts[1], s.pts[2], s.pts[3]
            out.append(f"C{f(c1[0])} {f(c1[1])} {f(c2[0])} {f(c2[1])} {f(e[0])} {f(e[1])}")
    out.append("Z")
    return "".join(out)


def anchors(loops: list[Loop]) -> int:
    return sum(len(lp.segs) for lp in loops)


__all__ = ["Loop", "Seg", "anchors", "contours", "corners", "fit_edge", "fit_loop", "fit_run", "loop_path",
           "resample", "snap_lines", "unmix"]
