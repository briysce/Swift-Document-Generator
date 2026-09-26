"""Rebuild the Swift Supply logo as the vector its designer drew.

The only true source is assets/brand/swift_supply_source.png, 743x230. Every
larger "master" in the repo is an enlargement of it, and the SVG the app ships
is that PNG in an SVG wrapper. So the logo is rebuilt the way a designer
rebuilds a mark from a small scan — by its construction, not its pixels:

  * the letters are one set of shapes: fair curves, sharp serifs, straight
    stems on one italic slant, bases and cap lines on shared heights;
  * the dark outline is those shapes offset by one stroke width;
  * the shadow is the outlined letters extruded along one direction;
  * the bars are rectangles with a border and a shadow of their own;
  * SUPPLY is drawn the same way as the letters, in its own ink.

Widths, offsets and directions are not guessed: each is fitted against the
source's own ink, by rendering the construction at the source's resolution and
comparing coverage (the vector must *explain* the sketch, not copy it).

    python scripts/rebuild_swift_logo.py [--out assets/brand/swift_supply_logo_rebuilt.svg]
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.logo_vectorizer import design_fit as D  # noqa: E402

SOURCE = ROOT / "assets" / "brand" / "swift_supply_source.png"
ORANGE, DARK, PAGE = (221, 77, 42), (34, 33, 40), (255, 255, 255)
SS = 4          # supersampling for fitting renders


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------


def _flatten(loop: D.Loop, per_curve: int = 24) -> np.ndarray:
    pts = []
    for s in loop.segs:
        if s.is_line:
            pts.append(s.pts[0])
        else:
            t = np.linspace(0, 1, per_curve, endpoint=False)
            pts.extend(D._bezier(s.pts, t))
    return np.asarray(pts)


def shape_of(loops: list[D.Loop]):
    """Loops -> one shapely geometry, even-odd (nested loops are holes)."""
    from shapely.geometry import Polygon

    geom = None
    for lp in loops:
        poly = Polygon(_flatten(lp)).buffer(0)
        geom = poly if geom is None else geom.symmetric_difference(poly)
    return geom


def extrude(geom, dx: float, dy: float):
    """Minkowski sum with the segment [0, (dx, dy)]: the solid a shape sweeps
    when pushed along one direction — a block shadow."""
    from shapely import affinity
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    parts = [geom, affinity.translate(geom, dx, dy)]
    polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    for p in polys:
        for ring in [p.exterior, *p.interiors]:
            c = np.asarray(ring.coords)
            for a, b in zip(c[:-1], c[1:]):
                q = Polygon([a, b, b + (dx, dy), a + (dx, dy)])
                if q.area > 1e-9:
                    parts.append(q)
    return unary_union(parts)


def loops_of(geom, *, tol: float = 0.12) -> list[D.Loop]:
    """Exact geometry back to designed paths (lines and fair curves)."""
    polys = [geom] if geom.geom_type == "Polygon" else [g for g in geom.geoms if g.geom_type == "Polygon"]
    out = []
    for p in polys:
        for ring in [p.exterior, *p.interiors]:
            c = np.asarray(ring.coords)[:-1]
            if len(c) >= 3:
                out.append(D.fit_loop(c, tol=tol, step=0.25, corner_blur=0.3, corner_window=1.2))
    return out


# --------------------------------------------------------------------------
# fitting against the source
# --------------------------------------------------------------------------


def _render_geom(geom, shape: tuple[int, int], ss: int = SS) -> np.ndarray:
    """Coverage (0-1) of a shapely geometry at the source grid, supersampled."""
    import cairosvg

    h, w = shape
    polys = [geom] if geom.geom_type == "Polygon" else [g for g in geom.geoms if g.geom_type == "Polygon"]
    d = ""
    for p in polys:
        for ring in [p.exterior, *p.interiors]:
            c = np.asarray(ring.coords)
            d += "M" + "L".join(f"{x:.3f} {y:.3f}" for x, y in c) + "Z"
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w * ss}" height="{h * ss}" viewBox="0 0 {w} {h}">'
           f'<path d="{d}" fill="black" fill-rule="evenodd"/></svg>')
    a = np.asarray(Image.open(io.BytesIO(cairosvg.svg2png(bytestring=svg.encode()))).convert("RGBA"))[..., 3]
    return a.reshape(h, ss, w, ss).mean(axis=(1, 3)) / 255.0


def fit_outline_and_shadow(fill, dark_cov: np.ndarray, rows: slice, *, ws, dxs, dys) -> tuple[float, float, float, float]:
    """(error, width, dx, dy) that best explain the dark ink in `rows`: the
    fill offset by `width`, swept along (dx, dy), minus the fill itself."""
    from shapely import affinity  # noqa: F401  (import check)

    h, w = dark_cov.shape
    fill_cov = _render_geom(fill, (h, w))
    best = None
    for wd in ws:
        outlined = fill.buffer(wd, join_style="mitre", mitre_limit=2.5)
        for dx in dxs:
            for dy in dys:
                dark = extrude(outlined, dx, dy)
                pred = np.clip(_render_geom(dark, (h, w)) - fill_cov, 0, 1)
                e = float(np.mean((pred[rows] - dark_cov[rows]) ** 2))
                if best is None or e < best[0]:
                    best = (e, float(wd), float(dx), float(dy))
    return best


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------


def _hex(c) -> str:
    return "#{:02X}{:02X}{:02X}".format(*c)


def build(source: Path = SOURCE, *, quick: bool = False) -> tuple[str, dict]:
    src = np.asarray(Image.open(source).convert("RGBA")).astype(float)
    rgb = src[..., :3] * (src[..., 3:] / 255) + 255 * (1 - src[..., 3:] / 255)
    h, w = rgb.shape[:2]
    cov = D.unmix(rgb, [ORANGE, DARK, PAGE])
    cov_o, cov_d = cov[..., 0], cov[..., 1]

    orange = [D.fit_loop(c, tol=0.25) for c in D.contours(cov_o, 0.5, min_len=20)]
    D.snap_lines(orange)

    def span(lp):
        xs = np.concatenate([s.pts[:, 0] for s in lp.segs])
        return xs.max() - xs.min()

    bar_loops = [lp for lp in orange if span(lp) >= 0.8 * w]
    letter_loops = [lp for lp in orange if span(lp) < 0.8 * w]
    letters = shape_of(letter_loops)
    bars = shape_of(bar_loops)

    # Letters: outline and shadow, fitted in the letters' band.
    ys = np.concatenate([s.pts[:, 1] for lp in letter_loops for s in lp.segs])
    band = slice(int(ys.min()) - 2, int(ys.max()) + 10)
    grid = dict(ws=[2.1, 2.3, 2.5], dxs=[7.25, 7.75, 8.25], dys=[4.25, 4.625, 5.0]) if quick else \
        dict(ws=np.arange(1.9, 2.71, 0.1), dxs=np.arange(7.0, 8.51, 0.25), dys=np.arange(4.0, 5.26, 0.125))
    le, lw, ldx, ldy = fit_outline_and_shadow(letters, cov_d, band, **grid)
    letter_dark = extrude(letters.buffer(lw, join_style="mitre", mitre_limit=2.5), ldx, ldy)

    # Bars: their own border and shadow, fitted in their own rows.
    bys = [np.concatenate([s.pts[:, 1] for s in lp.segs]) for lp in bar_loops]
    rows = np.zeros(h, bool)
    for y in bys:
        rows[max(0, int(y.min()) - 4): min(h, int(y.max()) + 6)] = True
    be, bw, bdx, bdy = fit_outline_and_shadow(bars, cov_d, rows, ws=np.arange(0.4, 2.01, 0.2),
                                              dxs=np.arange(0.0, 3.01, 0.5), dys=np.arange(0.0, 3.01, 0.5))
    bar_dark = extrude(bars.buffer(bw, join_style="mitre", mitre_limit=2.5), bdx, bdy)

    # SUPPLY: the dark ink below the letters' shadow and above the bottom bar.
    top = int(max(y.max() for y in [np.concatenate([s.pts[:, 1] for lp in letter_loops for s in lp.segs])])) + 12
    bottom = int(min(y.min() for y in bys if y.min() > h / 2)) - 6
    supply_cov = np.zeros_like(cov_d)
    supply_cov[top:bottom] = cov_d[top:bottom]
    supply = [D.fit_loop(c, tol=0.25) for c in D.contours(supply_cov, 0.5, min_len=12)]
    D.snap_lines(supply)

    dark_loops = loops_of(bar_dark) + loops_of(letter_dark)
    paths = {
        "dark": "".join(D.loop_path(lp) for lp in dark_loops),
        "supply": "".join(D.loop_path(lp) for lp in supply),
        "orange": "".join(D.loop_path(lp) for lp in bar_loops + letter_loops),
    }
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w * 4}" height="{h * 4}">\n'
           f'  <title>Swift Supply</title>\n'
           f'  <path id="shadow-and-outline" fill="{_hex(DARK)}" fill-rule="evenodd" d="{paths["dark"]}"/>\n'
           f'  <path id="supply" fill="{_hex(DARK)}" fill-rule="evenodd" d="{paths["supply"]}"/>\n'
           f'  <path id="swift-and-bars" fill="{_hex(ORANGE)}" fill-rule="evenodd" d="{paths["orange"]}"/>\n'
           f'</svg>\n')
    report = {
        "letters": {"outline": round(lw, 3), "shadow": (round(ldx, 3), round(ldy, 3)), "error": round(le, 5)},
        "bars": {"border": round(bw, 3), "shadow": (round(bdx, 3), round(bdy, 3)), "error": round(be, 5)},
        "anchors": {"orange": D.anchors(bar_loops + letter_loops), "dark": D.anchors(dark_loops),
                    "supply": D.anchors(supply)},
        "bytes": len(svg),
    }
    return svg, report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", type=Path, default=SOURCE)
    ap.add_argument("--out", type=Path, default=ROOT / "assets" / "brand" / "swift_supply_logo_rebuilt.svg")
    ap.add_argument("--quick", action="store_true", help="coarse parameter grid")
    a = ap.parse_args(argv)
    svg, report = build(a.source, quick=a.quick)
    a.out.write_text(svg, encoding="utf-8")
    print(a.out, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
