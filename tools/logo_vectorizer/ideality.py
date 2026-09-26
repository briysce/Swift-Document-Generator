"""Vector ideality — does this SVG look *natively constructed*, or traced?

The problem with raster-fidelity scoring
----------------------------------------
Every metric in `logo_golden_suite.py` — legacy `composite` and `composite_v2`
alike — compares the restored raster against a reference raster. That is the
wrong question for this engine.

The QA anchors are hard-alpha PNGs: `qa_logos/synthetic/clean/swift_orange.png`
has exactly two alpha values, 0 and 255, and zero anti-aliased pixels. Its
curves are staircases. So raster IoU against it *rewards reproducing the
staircase* and penalizes the smooth curve that a real vector would have.

We are not going vector → raster. We are going raster → vector, and the right
output is not a copy of the input's pixels. It is the artwork the input is a
degraded rendering *of*: exact straight lines, continuous curvature, corners
only where the design has corners, and few, well-placed anchors.

What this module measures
-------------------------
Given an SVG, it scores how much the geometry looks designed rather than traced:

  anchor_economy   Anchors per unit contour length. A hand-built mark places a
                   node at each corner and curvature extreme — tens of nodes. A
                   raster trace emits one per pixel step — thousands.

  straightness     Long, near-collinear runs should be *exactly* collinear.
                   Measures residual off-axis wobble on segments that are
                   within a few degrees of straight.

  smoothness       Curvature continuity along contours, excluding intentional
                   corners. Traced outlines ripple at the pixel period; designed
                   curves do not.

  no_staircase     Direct detector for the raster-trace signature: alternating
                   axis-aligned steps of ~1 source pixel. This is the single
                   most diagnostic term.

These are properties of the *output geometry alone* — no reference raster — so
a perfect score is actually reachable, which is the point. Shape agreement with
the source is still needed, and still comes from the fidelity metrics; the two
are reported side by side. A smooth blob would ace ideality and fail fidelity;
a pixel-perfect trace does the reverse. Good work scores well on both.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

# --------------------------------------------------------------------------
# Minimal SVG path parsing (absolute/relative M L H V C S Q T A Z)
# --------------------------------------------------------------------------

_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|-?\d*\.?\d+(?:[eE][-+]?\d+)?")
_D_ATTR = re.compile(r'\bd\s*=\s*"([^"]*)"', re.S)

_ARGC = {
    "M": 2, "L": 2, "H": 1, "V": 1,
    "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0,
}


def _parse_path(d: str) -> list[list[tuple[float, float]]]:
    """Return subpaths as polylines of on-curve anchor points.

    Control points are dropped: we are counting and measuring *anchors*, which
    is what "constructed with anchor points" means.
    """
    toks = _TOKEN.findall(d)
    subpaths: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    x = y = 0.0
    sx = sy = 0.0
    cmd = None
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in ("Z", "z"):
                if len(cur) > 1:
                    subpaths.append(cur)
                cur = []
                x, y = sx, sy
            continue
        if cmd is None:
            i += 1
            continue
        up = cmd.upper()
        rel = cmd.islower()
        n = _ARGC.get(up, 0)
        if n == 0 or i + n > len(toks):
            i += 1
            continue
        try:
            vals = [float(v) for v in toks[i : i + n]]
        except ValueError:
            i += n
            continue
        i += n

        if up == "M":
            x, y = (x + vals[0], y + vals[1]) if rel else (vals[0], vals[1])
            if len(cur) > 1:
                subpaths.append(cur)
            cur = [(x, y)]
            sx, sy = x, y
            cmd = "l" if rel else "L"  # implicit lineto for repeats
            continue
        if up == "L":
            x, y = (x + vals[0], y + vals[1]) if rel else (vals[0], vals[1])
        elif up == "H":
            x = x + vals[0] if rel else vals[0]
        elif up == "V":
            y = y + vals[0] if rel else vals[0]
        elif up == "C":
            x, y = (x + vals[4], y + vals[5]) if rel else (vals[4], vals[5])
        elif up == "S":
            x, y = (x + vals[2], y + vals[3]) if rel else (vals[2], vals[3])
        elif up == "Q":
            x, y = (x + vals[2], y + vals[3]) if rel else (vals[2], vals[3])
        elif up == "T":
            x, y = (x + vals[0], y + vals[1]) if rel else (vals[0], vals[1])
        elif up == "A":
            x, y = (x + vals[5], y + vals[6]) if rel else (vals[5], vals[6])
        cur.append((x, y))
    if len(cur) > 1:
        subpaths.append(cur)
    return subpaths


def _viewbox_scale(svg_text: str) -> float:
    """Normalize measurements to a 1000-unit-tall canvas so size cancels out."""
    m = re.search(r'viewBox\s*=\s*"([^"]+)"', svg_text)
    if m:
        parts = [p for p in re.split(r"[\s,]+", m.group(1).strip()) if p]
        if len(parts) == 4:
            try:
                h = float(parts[3])
                if h > 0:
                    return 1000.0 / h
            except ValueError:
                pass
    m = re.search(r'\bheight\s*=\s*"([\d.]+)', svg_text)
    if m:
        try:
            h = float(m.group(1))
            if h > 0:
                return 1000.0 / h
        except ValueError:
            pass
    return 1.0


# --------------------------------------------------------------------------
# Geometry measures
# --------------------------------------------------------------------------


def _seg_lengths(pts: np.ndarray) -> np.ndarray:
    d = np.diff(pts, axis=0)
    return np.hypot(d[:, 0], d[:, 1])


def _turn_angles(pts: np.ndarray) -> np.ndarray:
    """Exterior turn angle (radians) at each interior vertex."""
    v = np.diff(pts, axis=0)
    n = np.hypot(v[:, 0], v[:, 1])
    keep = n > 1e-9
    v, n = v[keep], n[keep]
    if len(v) < 2:
        return np.zeros(0)
    u = v / n[:, None]
    dot = np.clip((u[:-1] * u[1:]).sum(axis=1), -1.0, 1.0)
    return np.arccos(dot)



_GROUP_OPEN = re.compile(r"<g\b([^>]*)>", re.S)
_SCALE = re.compile(r"scale\(\s*([-\d.eE]+)")


def _paths_with_scale(text: str) -> list[tuple[str, float]]:
    """Each path's `d` plus the cumulative `scale(...)` of its enclosing groups.

    Element tracers emit geometry in supersampled coordinates and scale the
    group back down. Measuring the raw numbers would understate anchors per
    unit length by exactly that factor, which would flatter a dense trace.
    """
    events: list[tuple[int, str, str]] = []
    for m in _GROUP_OPEN.finditer(text):
        events.append((m.start(), "open", m.group(1)))
    for m in re.finditer(r"</g\s*>", text):
        events.append((m.start(), "close", ""))
    for m in _D_ATTR.finditer(text):
        events.append((m.start(), "path", m.group(1)))
    events.sort(key=lambda e: e[0])

    out: list[tuple[str, float]] = []
    stack: list[float] = [1.0]
    for _pos, kind, payload in events:
        if kind == "open":
            sm = _SCALE.search(payload)
            factor = 1.0
            if sm:
                try:
                    factor = abs(float(sm.group(1))) or 1.0
                except ValueError:
                    factor = 1.0
            stack.append(stack[-1] * factor)
        elif kind == "close":
            if len(stack) > 1:
                stack.pop()
        else:
            out.append((payload, stack[-1]))
    return out



_RECT = re.compile(r"<rect\b([^>]*)>")
_CIRCLE = re.compile(r"<circle\b([^>]*)>")
_ELLIPSE = re.compile(r"<ellipse\b([^>]*)>")
_POLY = re.compile(r"<polygon\b([^>]*)>")


def _attr(tag: str, name: str) -> float | None:
    m = re.search(rf'\b{name}\s*=\s*"([-\d.eE]+)"', tag)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _primitives(text: str, scale: float) -> list[tuple[int, float]]:
    """Native primitive elements as (anchors, perimeter) in normalized units.

    `<rect>`, `<circle>`, `<ellipse>` and `<polygon>` carry no `d` attribute, so
    a path-only parser cannot see them at all. That is not a cosmetic gap: these
    are the *most* ideal geometry the engine can emit — a rectangle is four
    exact corners, a circle is one radius — and leaving them uncounted made
    ideality DROP when a traced bar was replaced by a perfect rect, penalizing
    precisely the outcome we want.
    """
    out: list[tuple[int, float]] = []
    for tag in _RECT.findall(text):
        w, h = _attr(tag, "width"), _attr(tag, "height")
        if w and h:
            out.append((4, 2.0 * (w + h) * scale))
    for tag in _CIRCLE.findall(text):
        r = _attr(tag, "r")
        if r:
            # Four on-curve anchors is how a circle is actually drawn in a
            # vector editor (four quarter arcs).
            out.append((4, 2.0 * math.pi * r * scale))
    for tag in _ELLIPSE.findall(text):
        rx, ry = _attr(tag, "rx"), _attr(tag, "ry")
        if rx and ry:
            out.append((4, math.pi * (3 * (rx + ry) - math.sqrt(
                max((3 * rx + ry) * (rx + 3 * ry), 0.0))) * scale))
    for tag in _POLY.findall(text):
        m = re.search(r'\bpoints\s*=\s*"([^"]*)"', tag)
        if not m:
            continue
        nums = [float(v) for v in re.findall(r"-?\d*\.?\d+", m.group(1))]
        pts = np.asarray(nums[: len(nums) // 2 * 2], dtype=np.float64).reshape(-1, 2)
        if len(pts) < 3:
            continue
        closed = np.vstack([pts, pts[:1]]) * scale
        perim = float(np.hypot(*np.diff(closed, axis=0).T).sum())
        out.append((len(pts), perim))
    return out


@dataclass
class IdealityReport:
    anchors: int
    contour_length: float
    anchors_per_1k: float
    anchor_economy: float
    straightness: float
    smoothness: float
    no_staircase: float
    ideality: float

    def as_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in asdict(self).items()}


# Calibrated against the one genuinely hand-built vector in this repo:
# mobile/assets/images/briysce_wordmark_paper.svg measures ~57 anchors per 1000
# units of contour. That is what "drawn by a person" costs for a wordmark, so it
# anchors the good end. The raster-derived brand files sit at 115-308, which
# anchors the bad end. These numbers are empirical, not chosen to flatter a
# result — re-derive them if a better reference vector lands in the repo.
_ECONOMY_GOOD = 60.0
_ECONOMY_BAD = 300.0


def score_svg(svg_path: Path) -> IdealityReport:
    text = Path(svg_path).read_text(encoding="utf-8", errors="replace")
    scale = _viewbox_scale(text)

    subpaths: list[np.ndarray] = []
    for d, local in _paths_with_scale(text):
        for poly in _parse_path(d):
            if len(poly) >= 3:
                subpaths.append(np.asarray(poly, dtype=np.float64) * scale * local)

    if not subpaths and not _primitives(text, scale):
        return IdealityReport(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    prims = _primitives(text, scale)
    total_anchors = sum(len(p) for p in subpaths) + sum(n for n, _ in prims)
    total_len = float(sum(_seg_lengths(p).sum() for p in subpaths)) + float(
        sum(L for _, L in prims)
    )
    per_1k = (total_anchors / total_len * 1000.0) if total_len > 1e-9 else 0.0

    # --- anchor economy -------------------------------------------------
    if per_1k <= _ECONOMY_GOOD:
        economy = 1.0
    elif per_1k >= _ECONOMY_BAD:
        economy = 0.0
    else:
        economy = 1.0 - (math.log(per_1k / _ECONOMY_GOOD)
                         / math.log(_ECONOMY_BAD / _ECONOMY_GOOD))

    # --- staircase, straightness, smoothness ----------------------------
    step_hits = 0
    step_total = 0
    straight_res: list[float] = []
    curv_jumps: list[float] = []

    for p in subpaths:
        segs = _seg_lengths(p)
        if len(segs) < 3:
            continue
        v = np.diff(p, axis=0)

        # Staircase: consecutive short segments alternating axis-aligned.
        short = segs < 3.0  # ~1 source px once normalized to a 1000-tall canvas
        horiz = np.abs(v[:, 1]) <= 1e-6
        vert = np.abs(v[:, 0]) <= 1e-6
        axis = horiz | vert
        cand = short & axis
        if len(cand) > 1:
            alt = cand[:-1] & cand[1:] & (horiz[:-1] != horiz[1:])
            step_hits += int(alt.sum())
            step_total += int(len(alt))

        turns = _turn_angles(p)
        if len(turns) == 0:
            continue
        # Straightness: vertices that are nearly collinear should be exactly so.
        near_straight = turns < math.radians(8.0)
        if near_straight.any():
            straight_res.append(float(turns[near_straight].mean()))
        # Smoothness: curvature continuity away from intentional corners.
        soft = turns[turns < math.radians(60.0)]
        if len(soft) >= 2:
            curv_jumps.append(float(np.abs(np.diff(soft)).mean()))

    # Native primitives are exact by construction — a rect has no staircase and
    # perfectly straight sides — so they count as clean boundary length.
    prim_len = float(sum(L for _, L in prims))
    no_staircase = 1.0 - (step_hits / step_total) if step_total else 1.0
    straightness = (
        1.0 - min(1.0, float(np.mean(straight_res)) / math.radians(8.0))
        if straight_res
        else 1.0
    )
    smoothness = (
        1.0 - min(1.0, float(np.mean(curv_jumps)) / math.radians(30.0))
        if curv_jumps
        else 1.0
    )
    # Blend the traced measures toward perfect in proportion to how much of the
    # drawing is native primitives rather than traced contour.
    if total_len > 1e-9 and prim_len > 0:
        w = min(1.0, prim_len / total_len)
        straightness = straightness * (1.0 - w) + 1.0 * w
        smoothness = smoothness * (1.0 - w) + 1.0 * w
        no_staircase = no_staircase * (1.0 - w) + 1.0 * w

    ideality = float(
        0.35 * no_staircase
        + 0.30 * economy
        + 0.20 * smoothness
        + 0.15 * straightness
    )

    return IdealityReport(
        anchors=total_anchors,
        contour_length=total_len,
        anchors_per_1k=per_1k,
        anchor_economy=economy,
        straightness=straightness,
        smoothness=smoothness,
        no_staircase=no_staircase,
        ideality=ideality,
    )


__all__ = ["IdealityReport", "score_svg"]
