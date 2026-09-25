"""Font identification — recognize the letter, and the outline comes for free.

Why this matters for flaw-versus-intent
---------------------------------------
`shapes.best_fit` can say "this element is a circle", and then every departure
from that circle is flaw by definition. Letters deserve the same treatment, and
they are most of what a wordmark is made of.

If we can say "this element is Oswald SemiBold 'S'", we no longer have to guess
which bumps along its spine were intended. None of them were. The font file
holds the designer's actual curves — hinted, mathematically exact, drawn once by
a type designer — so the right reconstruction is not a cleaned-up trace of the
pixels but *the glyph itself*, placed where the pixels say it goes.

That is also what a person would do at a design desk: recognize the face, set
the text in it, and match the position. It is the strongest possible prior,
because the intended geometry is known exactly rather than inferred.

Matching
--------
Per-glyph matching alone is fragile — at low resolution an 'O' and a '0', or an
'I' and an 'l', are genuinely ambiguous. Two things make it reliable:

* **Scale-normalized shape comparison.** Both the element and the candidate
  glyph are cropped to their ink and resampled to a common *square* grid, so
  matching depends on neither point size nor proportion, and a soft
  (anti-aliased) comparison keeps a one-pixel boundary disagreement from
  dominating the score.

  This is also why aspect ratio must not be used as a hard reject. An earlier
  version prefiltered candidates whose proportions differed by more than 0.22
  in log space, which sounds harmless and was not: logos stretch and condense
  type constantly, and the filter threw away correct glyphs on a dimension the
  scorer had already normalized away. On the Swift SUPPLY lockup it dropped the
  run from 0.9094 to 0.8538 and turned a correct "SUPPLY" into "suPPLY", by
  rejecting the true uppercase match and leaving a wrong-case lookalike as the
  best survivor. Aspect is now only a very loose sanity bound.

* **Run-level agreement.** A wordmark is set in *one* face. Elements that share
  a baseline and cap height are grouped into a run, and the font is chosen to
  maximize agreement across the whole run rather than per letter. A face that
  wins six letters convincingly beats one that wins a single letter by a hair,
  which is what rules out the near-miss lookalikes.

Everything fails open: no confident match returns None and the caller traces the
element as ordinary geometry.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]

# Grid for scale-normalized comparison. Large enough to separate letterforms,
# small enough that a whole corpus sweep stays cheap.
GRID = 64
# Score a single glyph must reach to be considered at all.
MIN_GLYPH_SCORE = 0.82
# Score a run must average before we replace traced geometry with font outlines.
MIN_RUN_SCORE = 0.86
# Loose sanity bound on proportion difference (log ratio). Comparison is already
# scale- and proportion-normalized, so this only skips absurd candidates; a tight
# value here silently rejects correct glyphs in stretched or condensed lockups.
MAX_ASPECT_LOG_RATIO = 0.95
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789&.-+"


# --------------------------------------------------------------------------
# corpus
# --------------------------------------------------------------------------


def font_corpus(extra_dirs: list[Path] | None = None) -> list[Path]:
    """Fonts available to match against, project fonts first."""
    dirs = [
        # Downloaded matching corpus first — it is the broadest and the one
        # `font_fetch` maintains. Project fonts follow, then the system.
        ROOT / "tools" / "logo_vectorizer" / ".cache" / "fonts",
        ROOT / "mobile" / "assets" / "fonts",
        ROOT / "fonts",
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path.home() / ".fonts",
        Path("/Library/Fonts"),
        Path("C:/Windows/Fonts"),
    ]
    if extra_dirs:
        dirs = list(extra_dirs) + dirs
    seen: set[str] = set()
    out: list[Path] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for pat in ("*.ttf", "*.otf", "*.TTF", "*.OTF"):
            for f in sorted(d.rglob(pat)):
                key = f.name.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(f)
    return out


# --------------------------------------------------------------------------
# normalization + scoring
# --------------------------------------------------------------------------


def _crop_ink(a: np.ndarray) -> np.ndarray | None:
    ys, xs = np.where(a > 0)
    if len(xs) == 0:
        return None
    return a[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]


def _normalize(a: np.ndarray) -> tuple[np.ndarray, float] | None:
    """Crop to ink, record aspect, resample to a common soft grid."""
    c = _crop_ink(a)
    if c is None or c.shape[0] < 2 or c.shape[1] < 2:
        return None
    aspect = c.shape[1] / float(c.shape[0])
    img = Image.fromarray((c > 0).astype(np.uint8) * 255, "L").resize(
        (GRID, GRID), Image.Resampling.BILINEAR
    )
    return np.asarray(img, dtype=np.float32) / 255.0, aspect


def _soft_score(a: np.ndarray, b: np.ndarray) -> float:
    """Soft IoU. Grey boundaries stop a one-pixel disagreement dominating."""
    inter = float(np.minimum(a, b).sum())
    union = float(np.maximum(a, b).sum())
    return inter / union if union > 1e-6 else 0.0


def _render_glyph(font_path: Path, ch: str, px: int = 256) -> np.ndarray | None:
    from PIL import ImageDraw, ImageFont

    try:
        font = ImageFont.truetype(str(font_path), px)
    except Exception:
        return None
    img = Image.new("L", (px * 3, px * 3), 0)
    d = ImageDraw.Draw(img)
    try:
        d.text((px, px), ch, fill=255, font=font)
    except Exception:
        return None
    return np.asarray(img)


_GLYPH_CACHE: dict[tuple[str, str], tuple[np.ndarray, float] | None] = {}

# Normalized glyphs, kept on disk between processes.
#
# Matching a wordmark means comparing it against every character of every font
# in the corpus, and rendering those is what the work actually is: on a cold
# cache a single image spends 265s of a 265s run rasterizing 1,562 fonts across
# a 66-character alphabet, and only seconds comparing them. The in-process dict
# above makes the second image in a batch cheap, but logo_vectorize.py is a
# fresh process per logo, so in production every logo paid the full 265s.
#
# Rendering smaller is not the way out. Glyphs are compared on a GRID x GRID
# grid, so 256px looks like wasted precision, but dropping to 128px moves the
# normalized bitmap by enough to change matches — self-agreement against the
# 256px render is 0.9603 mean, 0.8510 at the 1st percentile, against a
# MIN_RUN_SCORE of 0.86 and a winning run score of 0.9545 — and buys only 2.1x.
# The antialiasing that a large render puts into the downsample is doing real
# work in the soft-IoU comparison.
#
# So render once, at full quality, and keep the result. The store is memory
# mapped, so a process reads only the glyphs it actually touches.
_STORE_DIR = Path(__file__).resolve().parent / ".cache" / "glyphs"
_STORE_GRID = _STORE_DIR / f"grid{GRID}.npy"
_STORE_META = _STORE_DIR / f"grid{GRID}.json"
_STORE: tuple[dict, object, list] | None = None
# One build attempt per process. A failed build leaves the store absent, and
# without this every run in the image would try again and fail again.
_STORE_BUILD_TRIED = False


def _store_key(font_path: Path, ch: str) -> str:
    return f"{Path(font_path).name}|{ch}"


def _load_store() -> tuple[dict, object, list]:
    """Index, memory-mapped grids, aspects. Empty triple when unavailable."""
    global _STORE
    if _STORE is not None:
        return _STORE
    try:
        meta = json.loads(_STORE_META.read_text(encoding="utf-8"))
        grids = np.load(_STORE_GRID, mmap_mode="r")
        _STORE = (meta["index"], grids, meta["aspect"])
    except Exception:
        _STORE = ({}, None, [])
    return _STORE


def build_glyph_store(
    fonts: "list[Path] | None" = None, alphabet: str = ALPHABET
) -> int:
    """Render the corpus once and write it where later processes can read it.

    Returns the number of glyphs stored. Safe to re-run: it rebuilds from
    scratch, so adding fonts to the corpus means rebuilding, and a stale store
    simply misses and falls back to rendering.
    """
    fonts = list(fonts if fonts is not None else font_corpus())
    index: dict[str, int] = {}
    aspects: list[float] = []
    grids: list[np.ndarray] = []
    for fp in fonts:
        for ch in alphabet:
            key = _store_key(fp, ch)
            if key in index:
                continue
            g = _render_glyph(fp, ch)
            n = _normalize(g) if g is not None else None
            if n is None:
                continue
            index[key] = len(grids)
            # Lossless, despite appearing not to be. _normalize divides a uint8
            # bilinear resize by 255, so every value is exactly k/255 for an
            # integer k, and k/255*255 recovers k for all 256 of them in
            # float32. Checked rather than assumed: a store that shifted a
            # glyph by one level could move a match, and nothing would say so.
            grids.append((n[0] * 255.0).astype(np.uint8))
            aspects.append(float(n[1]))
    if not grids:
        return 0
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    # Write then rename. A reader that catches a half-written store would not
    # fail loudly — it would match against whatever glyphs happened to be in
    # it, and quietly choose a different font.
    tmp_grid = _STORE_GRID.with_suffix(".npy.tmp")
    tmp_meta = _STORE_META.with_suffix(".json.tmp")
    np.save(tmp_grid, np.stack(grids))
    tmp_meta.write_text(
        json.dumps({"index": index, "aspect": aspects}), encoding="utf-8"
    )
    tmp_grid.replace(_STORE_GRID)
    tmp_meta.replace(_STORE_META)
    global _STORE
    _STORE = None
    return len(grids)


def _glyph_norm(font_path: Path, ch: str) -> tuple[np.ndarray, float] | None:
    key = (str(font_path), ch)
    if key not in _GLYPH_CACHE:
        index, grids, aspects = _load_store()
        row = index.get(_store_key(font_path, ch)) if grids is not None else None
        if row is not None:
            arr = np.asarray(grids[row], dtype=np.float32) / 255.0
            _GLYPH_CACHE[key] = (arr, float(aspects[row]))
        else:
            g = _render_glyph(font_path, ch)
            _GLYPH_CACHE[key] = _normalize(g) if g is not None else None
    return _GLYPH_CACHE[key]


@dataclass
class GlyphMatch:
    font: Path
    char: str
    score: float
    bbox: tuple[int, int, int, int]


def match_glyph(
    mask: np.ndarray,
    fonts: list[Path],
    *,
    alphabet: str = ALPHABET,
    min_score: float = MIN_GLYPH_SCORE,
) -> GlyphMatch | None:
    """Best (font, character) for one element."""
    norm = _normalize(mask.astype(np.uint8) * 255)
    if norm is None:
        return None
    target, aspect = norm
    ys, xs = np.where(mask)
    bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))

    best: GlyphMatch | None = None
    for fp in fonts:
        for ch in alphabet:
            g = _glyph_norm(fp, ch)
            if g is None:
                continue
            gnorm, gaspect = g
            # Loose sanity bound only — see the module docstring. Comparison is
            # already proportion-independent, so this exists purely to skip
            # absurd mismatches, not to judge shape.
            if aspect <= 0 or gaspect <= 0:
                continue
            if abs(math.log(aspect / gaspect)) > MAX_ASPECT_LOG_RATIO:
                continue
            sc = _soft_score(target, gnorm)
            if best is None or sc > best.score:
                best = GlyphMatch(fp, ch, sc, bbox)
    if best is None or best.score < min_score:
        return None
    return best


# --------------------------------------------------------------------------
# run-level agreement
# --------------------------------------------------------------------------


def group_runs(
    boxes: list[tuple[int, int, int, int]], *, tol: float = 0.28
) -> list[list[int]]:
    """Group element indices into text runs by cap height and baseline."""
    runs: list[list[int]] = []
    order = sorted(range(len(boxes)), key=lambda i: boxes[i][0])
    for i in order:
        x0, y0, x1, y1 = boxes[i]
        h = y1 - y0 + 1
        placed = False
        for run in runs:
            rx0, ry0, rx1, ry1 = boxes[run[-1]]
            rh = ry1 - ry0 + 1
            if abs(h - rh) / max(h, rh) > tol:
                continue
            # Baselines must line up within a fraction of the cap height.
            if abs(y1 - ry1) > tol * max(h, rh):
                continue
            run.append(i)
            placed = True
            break
        if not placed:
            runs.append([i])
    return [r for r in runs if len(r) >= 2]


@dataclass
class RunMatch:
    font: Path
    chars: list[str]
    indices: list[int]
    mean_score: float


def match_run(
    masks: list[np.ndarray],
    indices: list[int],
    fonts: list[Path],
    *,
    alphabet: str = ALPHABET,
    min_run_score: float = MIN_RUN_SCORE,
) -> RunMatch | None:
    """Pick the single font that best explains a whole run of elements."""
    norms = []
    for i in indices:
        n = _normalize(masks[i].astype(np.uint8) * 255)
        if n is None:
            return None
        norms.append(n)

    # First run on a machine pays to render the corpus; every run after it
    # reads the store instead. Without this the store only ever exists if
    # somebody builds it by hand, and logo_vectorize.py — a fresh process per
    # logo — would keep paying full price forever.
    global _STORE_BUILD_TRIED
    if not _STORE_BUILD_TRIED and _load_store()[1] is None:
        _STORE_BUILD_TRIED = True
        try:
            build_glyph_store(fonts, alphabet)
        except Exception:
            pass

    best: RunMatch | None = None
    n = len(norms)
    for fp in fonts:
        chars: list[str] = []
        total = 0.0
        pruned = False
        for k, (target, aspect) in enumerate(norms):
            # Branch and bound. The run's score is the mean over its elements,
            # so a perfect 1.0 on every element still to come caps what this
            # font can finish at. Once that cap cannot beat the incumbent —
            # or cannot clear the threshold that would let any font be
            # returned — the rest of the run is wasted work.
            #
            # This changes no answer, only how long it takes to reach it. It
            # matters because the corpus is 1,459 fonts and most of them are
            # hopeless on the first letter, while the loop was scoring all 66
            # characters against every element of the run regardless: on a
            # 301x77 mark, 284s of a 284s run, to match nothing.
            cap = (total + (n - k)) / n
            if best is not None:
                if cap <= best.mean_score:
                    pruned = True
                    break
            elif cap < min_run_score:
                pruned = True
                break

            bs, bc = 0.0, ""
            for ch in alphabet:
                g = _glyph_norm(fp, ch)
                if g is None:
                    continue
                gnorm, gaspect = g
                if aspect <= 0 or gaspect <= 0:
                    continue
                if abs(math.log(aspect / gaspect)) > MAX_ASPECT_LOG_RATIO:
                    continue
                sc = _soft_score(target, gnorm)
                if sc > bs:
                    bs, bc = sc, ch
            chars.append(bc)
            total += bs
        if pruned or not chars:
            continue
        mean = total / n
        if best is None or mean > best.mean_score:
            best = RunMatch(fp, chars, indices, mean)
    if best is None or best.mean_score < min_run_score:
        return None
    return best


# --------------------------------------------------------------------------
# outline emission
# --------------------------------------------------------------------------


def glyph_outline(
    font_path: Path,
    ch: str,
    bbox: tuple[int, int, int, int],
    refine_against: np.ndarray | None = None,
) -> str | None:
    """The font's own outline for `ch`, placed to fill `bbox`.

    This is the payoff: real type-designer curves rather than a trace of a
    degraded raster.
    """
    try:
        from fontTools.pens.boundsPen import BoundsPen
        from fontTools.pens.svgPathPen import SVGPathPen
        from fontTools.ttLib import TTFont
    except Exception:
        return None
    try:
        font = TTFont(str(font_path), fontNumber=0, lazy=True)
        cmap = font.getBestCmap()
        name = cmap.get(ord(ch))
        if name is None:
            return None
        gs = font.getGlyphSet()
        g = gs[name]
        bp = BoundsPen(gs)
        g.draw(bp)
        if bp.bounds is None:
            return None
        gx0, gy0, gx1, gy1 = bp.bounds
        gw, gh = (gx1 - gx0), (gy1 - gy0)
        if gw <= 0 or gh <= 0:
            return None
        pen = SVGPathPen(gs)
        g.draw(pen)
        d = pen.getCommands()
        font.close()
    except Exception:
        return None
    if not d:
        return None

    x0, y0, x1, y1 = bbox
    tw, th = (x1 - x0 + 1), (y1 - y0 + 1)
    if refine_against is not None:
        dx, dy, kx, ky, ang = _refine_placement(
            refine_against, bbox, d, (gx0, gy0, gx1, gy1)
        )
    else:
        dx, dy, kx, ky, ang = 0.0, 0.0, 1.0, 1.0, 0.0
    sx, sy = (tw * kx) / gw, (th * ky) / gh
    # Font space is y-up with the origin on the baseline; SVG is y-down. Flip,
    # then map the glyph's own ink box onto the element's box.
    tx = x0 - gx0 * sx + dx
    ty = y0 + gy1 * sy + dy
    cx, cy = x0 + tw / 2.0, y0 + th / 2.0
    rot = f"rotate({ang:.3f} {cx:.3f} {cy:.3f}) " if abs(ang) > 1e-6 else ""
    return (
        f'<path transform="{rot}translate({tx:.3f} {ty:.3f}) '
        f'scale({sx:.5f} {-sy:.5f})" d="{d}"/>'
    )


def _render_path(d: str, transform: str, shape: tuple[int, int]) -> np.ndarray | None:
    import io as _io

    try:
        import cairosvg
    except Exception:
        return None
    h, w = shape
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}"><path transform="{transform}" d="{d}" '
        f'fill="#000"/></svg>'
    )
    try:
        buf = _io.BytesIO()
        cairosvg.svg2png(
            bytestring=svg.encode(), write_to=buf,
            output_width=w, output_height=h,
            background_color="rgba(0,0,0,0)",
        )
        buf.seek(0)
        return np.asarray(Image.open(buf).convert("RGBA"))[:, :, 3] >= 128
    except Exception:
        return None


def _refine_placement(
    mask: np.ndarray,
    bbox: tuple[int, int, int, int],
    d: str,
    gbounds: tuple[float, float, float, float],
) -> tuple[float, float, float, float, float]:
    """Overlay the real glyph on the raster and adjust until it fits.

    This is the step a designer does by hand: drop the letter in the identified
    face on top of the locked raster, then nudge, resize and re-angle it until
    it sits exactly over the original, and only then throw the raster away. We
    are not tracing the pixels — the geometry is already correct, drawn by a
    type designer — so all that is left is to find the transform that lands it
    in the right place.

    The search covers translation, independent horizontal and vertical scale,
    and rotation. Independent scale matters because logos stretch and condense
    type constantly; rotation matters because plenty of lockups set text on an
    angle, and without it a slanted wordmark can never be seated properly no
    matter how good the glyph match is.

    Coordinate descent over a handful of parameters, scored by overlap against
    the element. Cheap enough to run per glyph, and it needs no autograd.

    Returns (dx, dy, kx, ky, angle_degrees).
    """
    x0, y0, x1, y1 = bbox
    tw, th = (x1 - x0 + 1), (y1 - y0 + 1)
    gx0, gy0, gx1, gy1 = gbounds
    gw, gh = (gx1 - gx0), (gy1 - gy0)
    if gw <= 0 or gh <= 0:
        return (0.0, 0.0, 1.0, 1.0, 0.0)

    cx, cy = x0 + tw / 2.0, y0 + th / 2.0
    best = (0.0, 0.0, 1.0, 1.0, 0.0)

    def score(params) -> float:
        dx, dy, kx, ky, ang = params
        sx, sy = (tw * kx) / gw, (th * ky) / gh
        tx = x0 - gx0 * sx + dx
        ty = y0 + gy1 * sy + dy
        xf = (
            (f"rotate({ang:.3f} {cx:.3f} {cy:.3f}) " if abs(ang) > 1e-6 else "")
            + f"translate({tx:.3f} {ty:.3f}) scale({sx:.6f} {-sy:.6f})"
        )
        r = _render_path(d, xf, mask.shape)
        if r is None:
            return -1.0
        union = np.logical_or(mask, r).sum()
        return float(np.logical_and(mask, r).sum() / union) if union else 0.0

    best_score = score(best)
    if best_score < 0:
        return best

    step = max(0.5, min(tw, th) * 0.02)
    # Order matters: seat the letter before stretching or turning it.
    schedule = [
        (0, step), (1, step),            # translate
        (2, 0.03), (3, 0.03),            # stretch each axis
        (4, 2.0),                        # rotate
        (0, step / 2), (1, step / 2),
        (2, 0.015), (3, 0.015),
        (4, 0.75),
    ]
    for idx, delta in schedule:
        improved = True
        rounds = 0
        while improved and rounds < 6:
            improved = False
            rounds += 1
            for sign in (1.0, -1.0):
                trial = list(best)
                trial[idx] += sign * delta
                # Keep the search physically sensible.
                if idx in (2, 3) and not (0.75 <= trial[idx] <= 1.35):
                    continue
                if idx == 4 and abs(trial[idx]) > 30.0:
                    continue
                sc = score(tuple(trial))
                if sc > best_score + 1e-6:
                    best_score, best = sc, tuple(trial)
                    improved = True
    return best


__all__ = [
    "GlyphMatch",
    "RunMatch",
    "font_corpus",
    "match_glyph",
    "match_run",
    "group_runs",
    "glyph_outline",
]
