"""Export the app's Swift logos from the rebuilt vector master.

The master, assets/brand/swift_supply_logo_rebuilt.svg (scripts/rebuild_swift_logo.py),
is the logo in the source's own coordinates (743x230) and the scanned inks.
The app draws it in its brand palette (#CE4E30 and black, the same orange as
the PDF stripes printed beside it) inside a 2987x910 box: the PDF painter
scales by the viewBox width and ignores its origin, and the PNG fallback, the
ink metrics and the chrome sizing all assume that box. So each variant is the
master's paths moved into that box by one uniform scale (centred; nothing is
stretched) and recoloured, and its PNG is rendered from that same SVG, so the
vector and the raster can never disagree.

  orange  PDF/document lockup: orange SWIFT and bars; black outline, shadow
          and SUPPLY.
  solid   app chrome and splash: the lockup in one orange, no outline or shadow.
  white   the one-colour lockup in white.

    python scripts/export_swift_app_logos.py [--check]

--check exits 1 if any committed asset differs from a fresh export.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "assets" / "brand" / "swift_supply_logo_rebuilt.svg"
BRAND = ROOT / "assets" / "brand"
MOBILE = ROOT / "mobile" / "assets" / "images"

BOX = (2987, 910)       # the app's logo box (w, h)
MARGIN = 2.0            # px inside the box, so edge anti-aliasing is not clipped
APP_ORANGE, BLACK, WHITE = "#CE4E30", "#000000", "#FFFFFF"

# Paint order and ink for each master path, per variant.
VARIANTS: dict[str, list[tuple[str, str]]] = {
    "orange": [("bar-borders", BLACK), ("shadow-and-outline", BLACK), ("supply", BLACK), ("swift-and-bars", APP_ORANGE)],
    "solid": [("bar-borders", APP_ORANGE), ("supply", APP_ORANGE), ("swift-and-bars", APP_ORANGE)],
    "white": [("bar-borders", WHITE), ("supply", WHITE), ("swift-and-bars", WHITE)],
}

# Where each variant goes. The silhouette file is a legacy alias of the solid one.
TARGETS: dict[str, list[Path]] = {
    "orange": [BRAND / "swift_supply_logo_orange", MOBILE / "swift_supply_logo_orange"],
    "solid": [BRAND / "swift_supply_logo_orange_solid", MOBILE / "swift_supply_logo_orange_solid.png"],
    "white": [BRAND / "swift_supply_logo_white"],
}
ALIASES = {BRAND / "swift_supply_logo_orange_silhouette.png": BRAND / "swift_supply_logo_orange_solid.png"}

_TOKEN = re.compile(r"[MLCZ]|-?\d*\.?\d+(?:e-?\d+)?")


def read_master(path: Path = MASTER) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    return dict(re.findall(r'<path id="([^"]+)"[^>]* d="([^"]*)"', text))


def _commands(d: str):
    """Absolute M/L/C/Z path data -> [(cmd, [(x, y), ...])]."""
    toks = _TOKEN.findall(d)
    out, i = [], 0
    arity = {"M": 1, "L": 1, "C": 3, "Z": 0}
    while i < len(toks):
        cmd = toks[i]
        if cmd not in arity:
            raise ValueError(f"unsupported path data near {toks[i:i + 4]}")
        i += 1
        pts = []
        for _ in range(arity[cmd]):
            pts.append((float(toks[i]), float(toks[i + 1])))
            i += 2
        out.append((cmd, pts))
    return out


def _points(d: str) -> np.ndarray:
    """Points on the outline (curves sampled), for the exact ink extent."""
    pts, cur = [], (0.0, 0.0)
    t = np.linspace(0, 1, 33)[:, None]
    for cmd, p in _commands(d):
        if cmd in "ML":
            cur = p[0]
            pts.append([cur])
        elif cmd == "C":
            b = np.array([cur, *p])
            pts.append(((1 - t) ** 3) * b[0] + 3 * ((1 - t) ** 2) * t * b[1] + 3 * (1 - t) * t * t * b[2] + t ** 3 * b[3])
            cur = p[2]
    return np.vstack([np.asarray(q, float).reshape(-1, 2) for q in pts])


def placement(paths: dict[str, str]) -> tuple[float, float, float]:
    """(scale, tx, ty): the full lockup fitted into BOX, uniform scale, centred.
    One placement for every variant, so switching variants never moves the bars."""
    pts = np.vstack([_points(d) for d in paths.values()])
    (x0, y0), (x1, y1) = pts.min(0), pts.max(0)
    w, h = BOX
    s = min((w - 2 * MARGIN) / (x1 - x0), (h - 2 * MARGIN) / (y1 - y0))
    return s, (w - s * (x1 - x0)) / 2 - s * x0, (h - s * (y1 - y0)) / 2 - s * y0


def _num(v: float) -> str:
    r = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if r in ("-0", "") else r


def transform(d: str, s: float, tx: float, ty: float) -> str:
    out = []
    for cmd, pts in _commands(d):
        out.append(cmd + " ".join(f"{_num(s * x + tx)} {_num(s * y + ty)}" for x, y in pts))
    return "".join(out)


def variant_svg(name: str, paths: dict[str, str], place: tuple[float, float, float]) -> str:
    w, h = BOX
    body = "".join(
        f'  <path id="{pid}" fill="{ink}" fill-rule="evenodd" d="{transform(paths[pid], *place)}"/>\n'
        for pid, ink in VARIANTS[name]
    )
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">\n'
            f'  <title>Swift Supply</title>\n{body}</svg>\n')


def render_png(svg: str) -> bytes:
    import cairosvg
    from PIL import Image

    w, h = BOX
    raw = cairosvg.svg2png(bytestring=svg.encode(), output_width=w, output_height=h)
    buf = io.BytesIO()
    Image.open(io.BytesIO(raw)).convert("RGBA").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def export() -> dict[Path, bytes]:
    paths = read_master()
    missing = {pid for v in VARIANTS.values() for pid, _ in v} - paths.keys()
    if missing:
        raise SystemExit(f"master lacks paths {sorted(missing)}: rerun scripts/rebuild_swift_logo.py")
    place = placement(paths)
    files: dict[Path, bytes] = {}
    for name, stems in TARGETS.items():
        svg = variant_svg(name, paths, place)
        png = render_png(svg)
        for stem in stems:
            # A bare stem gets both files; a named file (the app bundles only
            # the chrome PNG) gets just that one.
            if stem.suffix in ("", ".svg"):
                files[stem.with_suffix(".svg")] = svg.encode()
            if stem.suffix in ("", ".png"):
                files[stem.with_suffix(".png")] = png
    for alias, src in ALIASES.items():
        files[alias] = files[src]
    return files


def _current(path: Path, data: bytes) -> bool:
    """SVGs must match byte for byte. PNGs must match in pixels: another cairo
    or zlib may encode the same image differently, or anti-alias an edge
    pixel one level apart."""
    if not path.is_file():
        return False
    have = path.read_bytes()
    if have == data or path.suffix != ".png":
        return have == data
    from PIL import Image

    a = np.asarray(Image.open(io.BytesIO(have)).convert("RGBA"), dtype=np.int16)
    b = np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"), dtype=np.int16)
    return a.shape == b.shape and float(np.abs(a - b).mean()) < 0.05 and int(np.abs(a - b).max()) <= 8


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if a committed asset is stale")
    a = ap.parse_args(argv)
    files = export()
    stale = [p for p, b in files.items() if not _current(p, b)]
    if a.check:
        for p in stale:
            print(f"stale: {p.relative_to(ROOT)}")
        return 1 if stale else 0
    for p in stale:
        p.write_bytes(files[p])
        print(f"wrote {p.relative_to(ROOT)} ({len(files[p])} bytes)")
    print(f"{len(files) - len(stale)} already current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
