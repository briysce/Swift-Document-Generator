"""Geometric primitive recognition — name the shape, and flaw defines itself.

Why this beats a residual test
------------------------------
`idealize._residual_is_tremor` decides flaw-versus-intent from local statistics:
small and unstructured means tremor, large or systematic means intent. That
works, but it is inference from noise character alone and it is easy to fool.

Recognizing the shape is far stronger. Once we can say "this element *is* a
circle", every departure from that circle is flaw by definition — no statistics
required. The shape class supplies the intent, and the pixels only supply the
parameters.

So this module tries to name each element from a vocabulary of constructions a
designer would actually have drawn:

    circle, ellipse (rotated), rectangle, rotated rectangle / parallelogram,
    regular polygon, rounded rectangle

and, when one fits, emits it exactly — a circle with one radius, a rectangle
with four right angles, a hexagon with six equal sides.

Choosing between candidates
---------------------------
Two rules, in order:

1. **It has to actually fit.** Coverage (IoU of the reconstructed primitive
   against the element) must clear a high bar, and the leftover must still read
   as tremor rather than a systematic departure. A squashed ellipse offered a
   circle fit fails here, as it should.

2. **Prefer the more constrained shape.** A circle is an ellipse with two fewer
   degrees of freedom; a square is a rectangle with one fewer. When both fit
   within tolerance, the tighter one is the stronger claim about intent, and it
   is also the one that removes more flaw. Ties go to the shape with fewer free
   parameters, not to the one with the marginally better coverage — otherwise
   noise decides, and a circle degrades into an ellipse that merely remembers
   the wobble.

Everything fails open: no confident fit returns None and the caller falls back
to tracing the element normally.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# Coverage a primitive must reach against the element before we believe it.
# This is a *relative* bar — see `_coverage_floor`. A fixed threshold is
# size-blind: a 44px circle tops out near 0.938 no matter how good the fit,
# because its 1px rim is ~6% of its area. Judging it against 0.972 rejects
# every small shape, which is exactly the low-resolution case that matters most.
MIN_COVERAGE = 0.972
# How much of the theoretical boundary loss we tolerate on top of itself.
COVERAGE_SLACK = 1.6
# Free parameters per shape — the tie-break toward the stronger claim.
_DOF = {
    "circle": 3,
    "regular_polygon": 5,
    "square": 3,
    "ellipse": 5,
    "rect": 4,
    "rotated_rect": 5,
    "rounded_rect": 5,
    # A general polygon costs two parameters per vertex, so it always ranks
    # behind the constrained shapes and is only chosen when none of them fit.
    "polygon": 99,
}


@dataclass
class ShapeFit:
    kind: str
    markup: str
    coverage: float
    residual: float

    @property
    def dof(self) -> int:
        return _DOF.get(self.kind, 9)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------




def _boundary_residual(outline: np.ndarray, poly: np.ndarray) -> float:
    """Mean distance, in pixels, from the real boundary to a fitted polygon.

    One definition for every shape, so the accept/reject threshold means the
    same thing whichever primitive proposed the fit. The outline is sampled
    rather than walked in full — 240 points characterizes the departure and
    keeps this cheap on large elements.
    """
    import cv2

    if len(outline) == 0:
        return 0.0
    step = max(1, len(outline) // 240)
    pf = np.asarray(poly, dtype=np.float32)
    dists = [
        abs(cv2.pointPolygonTest(pf, (float(px), float(py)), True))
        for px, py in outline[::step]
    ]
    return float(np.mean(dists)) if dists else 0.0


def _coverage_floor(mask: np.ndarray, pixel: float) -> float:
    """Best coverage a *correct* primitive could reach on an element this size.

    Rasterizing any smooth boundary costs roughly half a pixel along its whole
    perimeter, so the achievable ceiling is 1 - (perimeter * 0.5 * pixel / area).
    On a 400px circle that is a fraction of a percent; on a 44px circle it is
    about 6%. Gate against this, not a constant, so small and large elements are
    held to the same standard of fit rather than the same number.
    """
    import cv2

    area = float(mask.sum())
    if area <= 0:
        return MIN_COVERAGE
    cnts, _ = cv2.findContours(
        mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
    )
    if not cnts:
        return MIN_COVERAGE
    perim = float(max(cv2.arcLength(c, True) for c in cnts))
    loss = (perim * 0.5 * pixel) / area
    return max(0.80, 1.0 - COVERAGE_SLACK * loss)


def _coverage(mask: np.ndarray, rendered: np.ndarray) -> float:
    union = np.logical_or(mask, rendered).sum()
    return float(np.logical_and(mask, rendered).sum() / union) if union else 0.0


def _render_poly(shape: tuple[int, int], pts: np.ndarray) -> np.ndarray:
    import cv2

    canvas = np.zeros(shape, dtype=np.uint8)
    cv2.fillPoly(canvas, [np.round(pts).astype(np.int32)], 1)
    return canvas.astype(bool)


def _render_ellipse(
    shape: tuple[int, int], cx: float, cy: float, ax: float, ay: float, ang: float
) -> np.ndarray:
    import cv2

    canvas = np.zeros(shape, dtype=np.uint8)
    cv2.ellipse(
        canvas,
        (int(round(cx)), int(round(cy))),
        (max(1, int(round(ax))), max(1, int(round(ay)))),
        ang, 0, 360, 1, -1,
    )
    return canvas.astype(bool)


def _outline(mask: np.ndarray) -> np.ndarray | None:
    """Largest external contour as an (N,2) float array of (x, y)."""
    import cv2

    cnts, _ = cv2.findContours(
        mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
    )
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    return c.reshape(-1, 2).astype(np.float64)


def _fmt(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _poly_markup(pts: np.ndarray) -> str:
    return (
        '<polygon points="'
        + " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in pts)
        + '"/>'
    )


# --------------------------------------------------------------------------
# Robust fitting (RANSAC)
# --------------------------------------------------------------------------


def _ransac_circle(
    outline: np.ndarray, thresh: float, iterations: int = 600
) -> tuple[float, float, float] | None:
    """Circle centre and radius from the consensus of the boundary.

    A mean-radius fit is a least-squares estimate, so every outlier pulls it.
    That is the wrong estimator for damaged artwork: a chunk bitten out by JPEG,
    a bled edge, or a neighbouring element touching the rim drags the centre and
    radius away from the shape a designer drew. RANSAC instead finds the circle
    that the *largest agreeing subset* of the boundary supports and ignores the
    rest, which is exactly how we want damage treated — as outliers, not as
    evidence.

    Falls back to the least-squares estimate if pyransac3d is unavailable.
    """
    try:
        import pyransac3d
    except Exception:
        return None
    if len(outline) < 12:
        return None
    # pyransac3d works in 3D; a planar lift at z=0 makes its circle fitter a
    # 2D fitter without reimplementing the consensus loop.
    pts = np.column_stack(
        [outline[:, 0], outline[:, 1], np.zeros(len(outline))]
    ).astype(np.float64)
    try:
        # pyransac3d divides by zero on degenerate 3-point samples, which is
        # normal inside a consensus loop — the sample is simply discarded. Keep
        # those warnings out of engine output.
        with np.errstate(divide="ignore", invalid="ignore"):
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                res = pyransac3d.Circle().fit(
                    pts, thresh=max(0.5, thresh), maxIteration=iterations
                )
    except Exception:
        return None
    centre = getattr(res, "center", None)
    radius = getattr(res, "radius", None)
    if centre is None or radius is None:
        return None
    try:
        cx, cy = float(centre[0]), float(centre[1])
        r = float(radius)
    except (TypeError, IndexError, ValueError):
        return None
    if not np.isfinite([cx, cy, r]).all() or r <= 1e-6:
        return None
    return cx, cy, r


# --------------------------------------------------------------------------
# individual fits
# --------------------------------------------------------------------------


def fit_circle(mask: np.ndarray, *, pixel: float = 1.0) -> ShapeFit | None:
    out = _outline(mask)
    if out is None or len(out) < 24:
        return None

    # Least-squares starting estimate.
    cx = float(out[:, 0].mean())
    cy = float(out[:, 1].mean())
    r = float(np.hypot(out[:, 0] - cx, out[:, 1] - cy).mean())
    if r < 3:
        return None
    best = (cx, cy, r)
    best_cov = _coverage(mask, _render_ellipse(mask.shape, cx, cy, r, r, 0.0))

    # Robust estimate — keep it only if it actually explains the shape better.
    rob = _ransac_circle(out, thresh=max(1.0, 0.02 * r))
    if rob is not None:
        rcx, rcy, rr = rob
        rcov = _coverage(mask, _render_ellipse(mask.shape, rcx, rcy, rr, rr, 0.0))
        if rcov > best_cov:
            best, best_cov = rob, rcov

    cx, cy, r = best
    radii = np.hypot(out[:, 0] - cx, out[:, 1] - cy)
    return ShapeFit(
        "circle",
        f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(r)}"/>',
        best_cov,
        float(np.abs(radii - r).mean()),
    )


def fit_ellipse(mask: np.ndarray) -> ShapeFit | None:
    import cv2

    out = _outline(mask)
    if out is None or len(out) < 24:
        return None
    (cx, cy), (w, h), ang = cv2.fitEllipse(out.astype(np.float32))
    if w < 4 or h < 4:
        return None
    ax, ay = w / 2.0, h / 2.0
    rendered = _render_ellipse(mask.shape, cx, cy, ax, ay, ang)
    # Residual: distance from each boundary point to the ellipse, measured in
    # the ellipse's own frame so rotation does not smear it.
    t = math.radians(ang)
    dx, dy = out[:, 0] - cx, out[:, 1] - cy
    u = (dx * math.cos(t) + dy * math.sin(t)) / max(ax, 1e-6)
    v = (-dx * math.sin(t) + dy * math.cos(t)) / max(ay, 1e-6)
    residual = float(np.abs(np.hypot(u, v) - 1.0).mean() * (ax + ay) / 2.0)
    markup = (
        f'<ellipse cx="{_fmt(cx)}" cy="{_fmt(cy)}" rx="{_fmt(ax)}" ry="{_fmt(ay)}"'
        + (f' transform="rotate({_fmt(ang)} {_fmt(cx)} {_fmt(cy)})"' if abs(ang) > 0.5 else "")
        + "/>"
    )
    return ShapeFit("ellipse", markup, _coverage(mask, rendered), residual)


def fit_rotated_rect(mask: np.ndarray) -> ShapeFit | None:
    """Rectangle at any angle — covers bars, slabs and diagonal stripes."""
    import cv2

    out = _outline(mask)
    if out is None or len(out) < 8:
        return None
    rect = cv2.minAreaRect(out.astype(np.float32))
    (cx, cy), (w, h), ang = rect
    if w < 3 or h < 3:
        return None
    box = cv2.boxPoints(rect).astype(np.float64)
    rendered = _render_poly(mask.shape, box)

    axis_aligned = (abs(ang) < 0.75) or (abs(abs(ang) - 90.0) < 0.75)
    square = abs(w - h) / max(w, h) < 0.02
    if axis_aligned:
        x0, y0 = cx - w / 2.0, cy - h / 2.0
        markup = (
            f'<rect x="{_fmt(x0)}" y="{_fmt(y0)}" '
            f'width="{_fmt(w)}" height="{_fmt(h)}"/>'
        )
        kind = "square" if square else "rect"
    else:
        markup = _poly_markup(box)
        kind = "rotated_rect"

    residual = _boundary_residual(out, box)
    return ShapeFit(kind, markup, _coverage(mask, rendered), residual)


def fit_regular_polygon(mask: np.ndarray) -> ShapeFit | None:
    """Triangle, pentagon, hexagon… — equal sides and equal turns."""
    import cv2

    out = _outline(mask)
    if out is None or len(out) < 16:
        return None
    peri = cv2.arcLength(out.astype(np.float32), True)
    best: ShapeFit | None = None
    for eps in (0.010, 0.015, 0.022, 0.030):
        approx = cv2.approxPolyDP(out.astype(np.float32), eps * peri, True)
        pts = approx.reshape(-1, 2).astype(np.float64)
        n = len(pts)
        if n < 3 or n > 10:
            continue
        sides = np.hypot(*np.diff(np.vstack([pts, pts[:1]]), axis=0).T)
        if sides.min() <= 1e-6:
            continue
        # Regular means equal sides AND equal turns. Sides alone are not
        # enough: a kite has four near-equal sides (std/mean 0.018) and is
        # nowhere near regular — its turn angles spread over 15 degrees.
        if sides.std() / sides.mean() > 0.06:
            continue
        v = np.diff(np.vstack([pts, pts[:2]]), axis=0)
        norms = np.hypot(v[:, 0], v[:, 1])
        if norms.min() <= 1e-9:
            continue
        u = v / norms[:, None]
        turns = np.degrees(
            np.arccos(np.clip((u[:-1] * u[1:]).sum(axis=1), -1.0, 1.0))
        )
        if float(turns.std()) > 3.0:
            continue
        rendered = _render_poly(mask.shape, pts)
        cov = _coverage(mask, rendered)
        # Residual must mean the same thing for every shape: how far the real
        # boundary strays from the fitted one, in pixels. Side-length spread is
        # not that — on a large triangle it reads ~15 while the edges sit within
        # a pixel of the fit, which wrongly rejected a clean triangle.
        residual = _boundary_residual(out, pts)
        cand = ShapeFit("regular_polygon", _poly_markup(pts), cov, residual)
        if best is None or cand.coverage > best.coverage:
            best = cand
    return best


def fit_rounded_rect(mask: np.ndarray) -> ShapeFit | None:
    """Rectangle with a uniform corner radius — very common in badges/buttons."""
    import cv2

    out = _outline(mask)
    if out is None or len(out) < 24:
        return None
    x, y, w, h = cv2.boundingRect(out.astype(np.float32))
    if w < 8 or h < 8:
        return None
    area = float(mask.sum())
    box_area = float(w * h)
    if box_area <= 0:
        return None
    # A rounded rect loses (4 - pi) r^2 relative to its bounding box.
    missing = box_area - area
    if missing <= 0:
        return None
    r = math.sqrt(missing / (4.0 - math.pi))
    if r < 1.5 or r > 0.5 * min(w, h):
        return None
    canvas = np.zeros(mask.shape, dtype=np.uint8)
    ri = int(round(r))
    cv2.rectangle(canvas, (x + ri, y), (x + w - ri, y + h), 1, -1)
    cv2.rectangle(canvas, (x, y + ri), (x + w, y + h - ri), 1, -1)
    for cxp, cyp in (
        (x + ri, y + ri), (x + w - ri, y + ri),
        (x + ri, y + h - ri), (x + w - ri, y + h - ri),
    ):
        cv2.circle(canvas, (cxp, cyp), ri, 1, -1)
    rendered = canvas.astype(bool)
    markup = (
        f'<rect x="{_fmt(x)}" y="{_fmt(y)}" width="{_fmt(w)}" height="{_fmt(h)}" '
        f'rx="{_fmt(r)}" ry="{_fmt(r)}"/>'
    )
    return ShapeFit("rounded_rect", markup, _coverage(mask, rendered), 0.0)


def fit_polygon(mask: np.ndarray, max_vertices: int = 12) -> ShapeFit | None:
    """Any straight-edged polygon — triangles, chevrons, parallelograms, arrows.

    Regularity is a strong claim and most logo geometry does not meet it: an
    isoceles triangle has equal-length sides but unequal turns, a parallelogram
    neither. What those shapes *do* share is that their edges are straight, and
    straight is the property worth reconstructing exactly — it is where raster
    stair-stepping is most visible when you zoom in.

    So this accepts any low-vertex-count polygon whose edges genuinely are
    straight, and emits it with exact vertices. It carries a high parameter
    count on purpose, so a circle or rectangle always wins when it also fits.
    """
    import cv2

    out = _outline(mask)
    if out is None or len(out) < 12:
        return None
    peri = cv2.arcLength(out.astype(np.float32), True)
    if peri <= 0:
        return None

    best: ShapeFit | None = None
    for eps in (0.006, 0.010, 0.015, 0.022):
        approx = cv2.approxPolyDP(out.astype(np.float32), eps * peri, True)
        pts = approx.reshape(-1, 2).astype(np.float64)
        n = len(pts)
        if n < 3 or n > max_vertices:
            continue
        rendered = _render_poly(mask.shape, pts)
        cov = _coverage(mask, rendered)
        residual = _boundary_residual(out, pts)
        cand = ShapeFit("polygon", _poly_markup(pts), cov, residual)
        # Fewer vertices is the better story when coverage is comparable.
        if best is None or (cand.coverage > best.coverage + 0.004):
            best = cand
    return best


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------


def best_fit(
    mask: np.ndarray,
    *,
    pixel: float = 1.0,
    min_coverage: float = MIN_COVERAGE,
) -> ShapeFit | None:
    """Name the element, or decline.

    Returns the most constrained primitive that both covers the element and
    leaves only tremor behind.
    """
    from .idealize import _residual_is_tremor

    if mask.sum() < 40:
        return None
    scale = math.sqrt(float(mask.sum()))
    min_coverage = min(min_coverage, _coverage_floor(mask, pixel))

    cands: list[ShapeFit] = []
    for fn in (
        fit_circle,
        fit_regular_polygon,
        fit_rotated_rect,
        fit_ellipse,
        fit_rounded_rect,
        fit_polygon,
    ):
        try:
            f = fn(mask)
        except Exception:
            f = None
        if f is not None and f.coverage >= min_coverage:
            cands.append(f)
    if not cands:
        return None

    # The leftover must still read as tremor, not a systematic departure.
    # No lenient fallback here: if nothing passes, we genuinely do not know the
    # shape, and tracing it honestly beats asserting a primitive we cannot
    # justify. Inventing regularity that was not there is the worse failure.
    keep = [
        f
        for f in cands
        if f.residual <= max(0.030 * scale, 1.5 * pixel)
    ]
    if not keep:
        return None

    # Most constrained first; coverage only breaks ties among equals.
    keep.sort(key=lambda f: (f.dof, -f.coverage))
    return keep[0]


__all__ = [
    "ShapeFit",
    "best_fit",
    "fit_circle",
    "fit_ellipse",
    "fit_rotated_rect",
    "fit_regular_polygon",
    "fit_rounded_rect",
    "fit_polygon",
]
