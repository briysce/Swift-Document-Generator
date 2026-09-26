"""Meedo-Me's review — nothing is reported until it has been checked.

Why this exists
---------------
The engine once deleted a brand colour and the metric called it the best result
in the corpus. On all three degraded variants of the GCM logo, reconstruction
dropped the red C/G monogram entirely — red went from 11% of the ink to 0% —
and `composite` scored those outputs ABOVE the correct ones. The improve loop
reported them as the three largest wins. They were the worst failures it had
produced, and they were caught only because someone looked at the images.

A score cannot be trusted to catch that, because a score blends many things
into one number and a missing colour is only part of one term. A person
reviewing the work would catch it instantly: the logo has a red element and the
output doesn't. So that is what this does — it looks at the output the way a
reviewer would, and asks whether it is still the same logo.

What it is and isn't
--------------------
It returns verdicts, never scores. Like the ledger, it does not measure quality;
it checks the output against things we have already learned must never happen.
Each check here exists because the engine did that thing and it cost something.

It needs no reference artwork, because production has none. Every check compares
the output with the sketch it was made from.

It is deliberately strict about deletion and deliberately lenient about change.
A restoration is supposed to differ from its sketch — a washed-out red should
come back saturated, a halo should disappear, a soft edge should become a hard
one — so colours are matched by hue family, not by value, and a colour may
shrink as its fringe is removed. What it may not do is vanish.

Every review is written into Meedo-Me's ledger, so its record of what it has
caught grows with every run.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

# A colour carrying at least this share of the sketch's ink is part of the
# brand, and the output must still have it. GCM's red is 11% of the ink; the
# thinnest real element on the corpus, PROPAK's red rule at low resolution, is
# about 4%.
REQUIRED_SHARE = 0.02

# Of a required colour's share, the output must keep at least this much. Loose
# on purpose: removing a colour's halo and resolving its soft edges genuinely
# shrinks it. Deletion is 0%, which is what this exists to catch.
MIN_RETAINED = 0.25

# Below this saturation a colour's hue is noise, and it is matched on lightness
# instead. Low on purpose: a blurred red can be washed to (162,122,122) and it
# is still red, just faded, and it should be recognized when the restoration
# brings it back.
HUE_MEANINGFUL = 0.08
HUE_FAMILY = 30.0          # degrees
# A dark colour carrying at least this much chroma is tinted, not black. Above
# JPEG noise on true black, which a pure (0,0,0) keeps near 0.01.
DARK_LUM = 60.0
DARK_TINT = 0.03
LIGHTNESS_MATCH = 70.0     # 0-255, for neutrals
# Hysteresis on DARK_TINT: a dark sketch colour judged tinted is matched by a
# dark output colour carrying at least this much chroma in the same hue family.
# False alarm #3: the Swift master's charcoal read (32,32,39) after resampling
# and the sketch's (33,31,39) — chroma 0.027 and 0.031, either side of 0.03 —
# so the one outline ink was "tinted" in one and "black" in the other, and the
# whole outline was reported deleted. A threshold is a line one grey level can
# cross; the match must not flip on it. True black (chroma ~0) stays apart
# from crushed navy, which the quantiser reads as (0,0,8). Only for colours
# of about the same lightness: Arc's teal (43,60,62) crushed to (10,16,17)
# keeps a trace of its hue but is not the same ink.
DARK_TINT_KEPT = DARK_TINT / 2
DARK_TINT_LUM = 12.0

# An output carrying less ink than this share of its sketch's has collapsed.
# Every genuine restoration on the corpus keeps well over half; the failures
# that prompted this kept 0.2% to 2%.
MIN_INK_RATIO = 0.10

# Small elements colour share cannot see: GCM's "Modification" lost its i-dots
# and navy barely moved. A small element is judged by correspondence, not by
# count — the output must still have ink where the sketch has the dot. Counting
# was tried first and failed both ways on the corpus: JPEG specks around an
# ESRGAN output's letters stood in for the two dots it had lost, and on Trialta,
# which has no dots at all, noise crumbs were counted as dots and blocked eight
# correct outputs.
#
# What makes a dot a dot and a crumb a crumb is how it is drawn: a dot is
# compact, solid (as dark as the letters) and stands apart from them; a crumb is
# faint, ragged, or touching the stroke it broke from. Every threshold is
# relative to the logo's own letters, so it holds at any resolution and on any
# background.
DOT_MIN_PX = 6             # below this, a blob is sampling noise
DOT_MAX_FRAC = 0.25        # of the median letter-sized element in its colour
DOT_MIN_FILL = 0.55        # of its bounding box: compact, not a sliver
DOT_MAX_ASPECT = 2.5
DOT_SOLID = 0.75           # peak darkness against the letters' typical darkness
DOT_KEPT = 0.30            # share of the dot the output must still cover
# Whole elements — letters, bars, marks — by the same correspondence. Arc's
# reconstruction kept every colour and dropped "RESOURCES LTD."'s letters,
# which no colour or dot check can see. An element is letter-sized when it is
# at least this share of the median element in its colour. It is kept when the
# output has ink along this share of its skeleton: presence, not area — a
# restoration that draws a bold blurry letter thin and crisp has kept it.
ELEMENT_MIN_FRAC = 0.3
ELEMENT_KEPT = 0.4


@dataclass
class Finding:
    check: str
    severity: str  # "block" or "warn"
    detail: str
    colour: tuple[int, int, int] | None = None
    n: int | None = None  # how many were lost, where the check counts

    def as_dict(self) -> dict:
        d = {"check": self.check, "severity": self.severity, "detail": self.detail}
        if self.colour is not None:
            d["colour"] = list(self.colour)
        if self.n is not None:
            d["n"] = self.n
        return d


@dataclass
class Review:
    findings: list[Finding] = field(default_factory=list)
    sketch_palette: list[tuple[tuple[int, int, int], float]] = field(default_factory=list)
    output_palette: list[tuple[tuple[int, int, int], float]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(f.severity == "block" for f in self.findings)

    def summary(self) -> str:
        if self.passed and not self.findings:
            return "Meedo-Me: passed"
        head = "Meedo-Me: passed with notes" if self.passed else "Meedo-Me: BLOCKED"
        return head + " — " + "; ".join(f.detail for f in self.findings)

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "findings": [f.as_dict() for f in self.findings],
            "sketch_palette": [[list(c), round(s, 4)] for c, s in self.sketch_palette],
            "output_palette": [[list(c), round(s, 4)] for c, s in self.output_palette],
        }


# --------------------------------------------------------------------------
# reading a palette
# --------------------------------------------------------------------------


def _as_rgba(img) -> np.ndarray:
    if isinstance(img, (str, Path)):
        img = Image.open(img)
    if isinstance(img, Image.Image):
        return np.asarray(img.convert("RGBA"))
    arr = np.asarray(img)
    if arr.ndim == 3 and arr.shape[2] == 3:
        alpha = np.full(arr.shape[:2] + (1,), 255, dtype=arr.dtype)
        arr = np.concatenate([arr, alpha], axis=2)
    return arr


def _layers(arr: np.ndarray):
    from .idealize import _quantize_layers

    return _quantize_layers(arr, max_layers=8)


def _palette_of(layers) -> list[tuple[tuple[int, int, int], float]]:
    total = float(sum(n for _, _, n in layers))
    if total <= 0:
        return []
    return [(tuple(int(v) for v in c), n / total) for _, c, n in layers]


def palette(img) -> list[tuple[tuple[int, int, int], float]]:
    """The colours a designer would name, each with its share of the ink.

    Uses the reconstruction's own layer reading, so "brand colour" means the
    same thing to the reviewer as to the engine: colours pooled as the eye
    pools them, the page removed, soft edges and halos resolved rather than
    counted.
    """
    return _palette_of(_layers(_as_rgba(img)))


def _hue(c) -> tuple[float, float, float]:
    """(hue in degrees, chroma 0-1, lightness 0-255).

    Chroma rather than HSV saturation, because saturation explodes near black.
    The first version of this reviewer used saturation and blocked a correct
    Swift restoration for "deleting" its black shadow: the output's black read
    as (2,0,0), saturation 1.0, and so was taken for red.
    """
    r, g, b = (float(v) / 255.0 for v in c)
    h, _s, _v = colorsys.rgb_to_hsv(r, g, b)
    return h * 360.0, (max(c) - min(c)) / 255.0, float(np.mean(c))


def _is_page_white(c) -> bool:
    """Near-white neutrals are counters or paper, not ink anyone must draw.

    GCM's monogram has a white gap between the red C and the blue G. In a
    vector that gap is often simply empty, which is correct, so it is never
    required of the output.
    """
    _h, s, lum = _hue(c)
    return s <= 0.08 and lum >= 225


def _has_hue(c) -> bool:
    """Whether a colour's hue means something.

    Chroma scales with lightness, so a dark colour is low-chroma even when it is
    unmistakably coloured. GCM's text arrives from a crushed import as (0,1,12):
    chroma 0.047, which reads as black, but its tint is plainly blue — it is the
    navy text with the shadows crushed. Taken for black, the reviewer blocked the
    restoration for bringing the text back blue, which is the restoration being
    right. Among dark colours, any detectable tint is a hue.
    """
    _h, ch, lum = _hue(c)
    return ch >= HUE_MEANINGFUL or (lum < DARK_LUM and ch >= DARK_TINT)


def _matches(want, have) -> bool:
    hw, sw, lw = _hue(want)
    hh, sh, lh = _hue(have)
    if _has_hue(want):
        dark_tint = sw < HUE_MEANINGFUL and lw < DARK_LUM and abs(lw - lh) <= DARK_TINT_LUM
        if sh < (DARK_TINT_KEPT if dark_tint else DARK_TINT):
            return False
        d = abs(hw - hh) % 360.0
        return min(d, 360.0 - d) <= HUE_FAMILY
    return sh < 0.20 and abs(lw - lh) <= LIGHTNESS_MATCH


# --------------------------------------------------------------------------
# checks — each one is something the engine has actually done
# --------------------------------------------------------------------------


def _flatten(arr: np.ndarray) -> np.ndarray:
    """What a viewer sees: the image over a white page, as float RGB."""
    rgb = arr[:, :, :3].astype(np.float32)
    a = arr[:, :, 3:4].astype(np.float32) / 255.0
    return rgb * a + 255.0 * (1.0 - a)


def _small_elements(arr: np.ndarray, layers) -> list[tuple[np.ndarray, tuple[int, int, int, int]]]:
    """Dots, accents and marks: small, compact, solid and standing apart.

    Returns (mask, bbox) for each. See DOT_* for why each property is there.
    """
    import cv2

    flat = _flatten(arr)
    border = np.concatenate([flat[0], flat[-1], flat[:, 0], flat[:, -1]])
    page = np.median(border, axis=0)
    dark = np.abs(flat - page).sum(axis=2)          # distance from the page
    ink = np.zeros(arr.shape[:2], bool)
    for m, c, _n in layers:
        if not _is_page_white(c):
            ink |= m.astype(bool)
    out = []
    for m, c, _n in layers:
        if _is_page_white(c):
            continue
        k, lab, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
        areas = st[1:, cv2.CC_STAT_AREA]
        letters = areas[areas >= 4 * DOT_MIN_PX]
        if len(letters) < 2:
            continue
        median = float(np.median(letters))
        big = np.isin(lab, 1 + np.flatnonzero(areas >= median * 0.5))
        typical = float(np.median(dark[big])) if big.any() else 0.0
        if typical <= 0:
            continue
        for i in range(1, k):
            x, y, w, h, area = (int(v) for v in st[i])
            if area < DOT_MIN_PX or area > median * DOT_MAX_FRAC:
                continue
            if area / float(w * h) < DOT_MIN_FILL or max(w, h) > DOT_MAX_ASPECT * min(w, h):
                continue
            mask = lab == i
            if np.percentile(dark[mask], 90) < DOT_SOLID * typical:
                continue                                   # faint: fringe, not ink
            g = max(2, int(np.ceil(0.5 * max(w, h))))
            y0, y1, x0, x1 = max(0, y - g), min(ink.shape[0], y + h + g), max(0, x - g), min(ink.shape[1], x + w + g)
            if (ink[y0:y1, x0:x1] & ~mask[y0:y1, x0:x1]).any():
                continue                                   # touching a stroke: a broken piece
            out.append((mask, (x, y, w, h)))
    return out


def _elements(layers) -> list[tuple[np.ndarray, tuple[int, int, int, int]]]:
    """Letter-sized elements of all the ink together: (mask, bbox).

    All colours at once: presence is judged colour-blind, and one ink can be
    split across layers — Arc's teal tagline came back as two layers, and per
    layer most of its letters fell under the size floor and were never checked.
    """
    import cv2

    ink = np.zeros(layers[0][0].shape, bool) if layers else None
    if ink is None:
        return []
    for m, c, _n in layers:
        if not _is_page_white(c):
            ink |= m.astype(bool)
    k, lab, st, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    areas = st[1:, cv2.CC_STAT_AREA]
    sized = areas[areas >= 4 * DOT_MIN_PX]
    if not len(sized):
        return []
    floor = max(4 * DOT_MIN_PX, ELEMENT_MIN_FRAC * float(np.median(sized)))
    out = []
    for i in np.flatnonzero(areas >= floor) + 1:
        x, y, w, h = (int(v) for v in st[i, :4])
        out.append((lab == i, (x, y, w, h)))
    return out


def _register(s_bulk: np.ndarray, o_bulk: np.ndarray, s_ink: np.ndarray, o_ink: np.ndarray) -> np.ndarray:
    """Where each sketch pixel lands in the output: x' = sx*x + tx, y' = sy*y + ty.

    Outputs are cropped to their ink and resized, so only stretch and shift
    are possible — no shear, no rotation. Several starting points (ink
    extents, moments of the bulk ink, the identity), each refined by a small
    search on the correlation of blurred ink, and the best kept. One start is
    not enough: ESRGAN's letters come back in fragments, the moments start
    read the x-scale as 0.51 against a true 1.04, and ECC from there converged
    to a sheared nonsense transform that called present letters missing.
    """
    import cv2

    sb = cv2.GaussianBlur(s_ink.astype(np.float32), (0, 0), 1.5)
    ob = cv2.GaussianBlur(o_ink.astype(np.float32), (0, 0), 1.5)
    h, w = sb.shape
    sn = float(np.sqrt((sb * sb).sum())) or 1.0

    def score(p):
        sx, sy, tx, ty = p
        m = np.array([[sx, 0.0, tx], [0.0, sy, ty]], np.float32)
        warped = cv2.warpAffine(ob, m, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
        wn = float(np.sqrt((warped * warped).sum())) or 1.0
        return float((sb * warped).sum()) / (sn * wn)

    starts = [(1.0, 1.0, 0.0, 0.0)]
    for a, b in ((s_ink, o_ink), (s_bulk, o_bulk)):
        ay, ax = np.nonzero(a)
        by, bx = np.nonzero(b)
        if len(ax) and len(bx):
            sx = (bx.max() - bx.min() + 1) / float(ax.max() - ax.min() + 1)
            sy = (by.max() - by.min() + 1) / float(ay.max() - ay.min() + 1)
            starts.append((sx, sy, bx.min() - sx * ax.min(), by.min() - sy * ay.min()))
    ay, ax = np.nonzero(s_bulk)
    by, bx = np.nonzero(o_bulk)
    if len(ax) > 1 and len(bx) > 1:
        sx = float(bx.std()) / max(float(ax.std()), 1e-6)
        sy = float(by.std()) / max(float(ay.std()), 1e-6)
        starts.append((sx, sy, bx.mean() - sx * ax.mean(), by.mean() - sy * ay.mean()))

    best, best_s = None, -1.0
    for p in starts:
        p = list(p)
        cur = score(p)
        for step in (4.0, 2.0, 1.0, 0.5, 0.25):
            improved = True
            while improved:
                improved = False
                for i, d in ((2, step), (2, -step), (3, step), (3, -step),
                             (0, step / 100), (0, -step / 100), (1, step / 100), (1, -step / 100)):
                    q = list(p)
                    q[i] = q[i] * (1 + d) if i < 2 else q[i] + d
                    sc = score(q)
                    if sc > cur + 1e-6:
                        p, cur, improved = q, sc, True
        if cur > best_s:
            best, best_s = p, cur
    sx, sy, tx, ty = best
    return np.array([[sx, 0.0, tx], [0.0, sy, ty]], np.float32)


def _check_small_elements(sketch: np.ndarray, output: np.ndarray, sketch_layers, output_layers) -> list[Finding]:
    """Every dot, accent and mark the sketch shows must still be drawn.

    GCM "Modification": the baseline lost one i-dot and ESRGAN lost two, and
    neither moved the navy share enough for the colour check to see.
    """
    import cv2

    dots = _small_elements(sketch, sketch_layers)
    elements = _elements(sketch_layers)
    if not dots and not elements:
        return []

    def union(layers, shape):
        m = np.zeros(shape, bool)
        for lm, c, _n in layers:
            if not _is_page_white(c):
                m |= lm.astype(bool)
        return m

    def bulk(m):
        """The big pieces only, so dots present on one side and missing on the
        other cannot pull the alignment either way."""
        k, lab, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
        if k <= 1:
            return m
        areas = st[1:, cv2.CC_STAT_AREA]
        return np.isin(lab, 1 + np.flatnonzero(areas >= 0.02 * areas.max()))

    ink = union(output_layers, output.shape[:2])
    s_bulk = bulk(union(sketch_layers, sketch.shape[:2]))
    o_bulk = bulk(ink)
    if not o_bulk.any() or not s_bulk.any():
        return []
    warp = _register(s_bulk, o_bulk, union(sketch_layers, sketch.shape[:2]), ink)

    def to_output(px, py):
        return (warp[0, 0] * px + warp[0, 1] * py + warp[0, 2],
                warp[1, 0] * px + warp[1, 1] * py + warp[1, 2])

    scale = abs(float(warp[0, 0] * warp[1, 1] - warp[0, 1] * warp[1, 0]))

    def kept(mask, box, share, pad):
        x, y, w, h = box
        ax, ay = to_output(x, y)
        bx, by = to_output(x + w, y + h)
        x0, x1 = int(round(min(ax, bx))) - pad, int(round(max(ax, bx))) + pad
        y0, y1 = int(round(min(ay, by))) - pad, int(round(max(ay, by))) + pad
        win = ink[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
        return win.sum() >= share * mask.sum() * scale

    findings = []
    missing = [f"({x + w // 2},{y + h // 2})" for mask, (x, y, w, h) in dots if not kept(mask, (x, y, w, h), DOT_KEPT, 1)]
    if missing:
        findings.append(Finding(
            "small_element", "block",
            f"{len(missing)} of the {len(dots)} small elements the sketch shows "
            f"(dots, accents, marks) are missing, at {', '.join(missing)} in sketch pixels",
            n=len(missing)))
    from skimage.morphology import skeletonize

    near_ink = cv2.dilate(ink.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)

    def present(mask) -> bool:
        ys, xs = np.nonzero(skeletonize(mask))
        if not len(xs):
            return True
        px = np.round(warp[0, 0] * xs + warp[0, 1] * ys + warp[0, 2]).astype(int)
        py = np.round(warp[1, 0] * xs + warp[1, 1] * ys + warp[1, 2]).astype(int)
        inside = (px >= 0) & (py >= 0) & (px < ink.shape[1]) & (py < ink.shape[0])
        hit = np.zeros(len(xs), bool)
        hit[inside] = near_ink[py[inside], px[inside]]
        return hit.mean() >= ELEMENT_KEPT

    gone = [f"({x + w // 2},{y + h // 2})" for mask, (x, y, w, h) in elements if not present(mask)]
    if gone:
        findings.append(Finding(
            "element", "block",
            f"{len(gone)} of the {len(elements)} elements the sketch shows (letters, bars, marks) "
            f"are missing, at {', '.join(gone[:8])}{' ...' if len(gone) > 8 else ''} in sketch pixels",
            n=len(gone)))
    return findings


def _is_fringe_neutral_layer(mask, colour, other_ink=None) -> bool:
    """Near-neutral AA fringe quantized as its own 'brand' colour.

    Arc's prepare palette invents a near-black layer (~12% of ink) made of
    hundreds of 1–4 px crumbs around red/teal edges. Official artwork has
    only red ARC + teal tagline — requiring that fringe of every candidate
    blocked both the trace and the reconstruction on the same false alarm,
    so identity-first could not choose. A real neutral letterform (black
    wordmark) has a few letter-sized components, not crumb soup — and it
    does not hug other chromatic ink as a one-pixel halo.
    """
    if _has_hue(colour) or _is_page_white(colour):
        return False
    import cv2

    k, _lab, st, _ = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8
    )
    areas = st[1:, cv2.CC_STAT_AREA] if k > 1 else np.array([], dtype=np.int32)
    crumb_soup = (
        k >= 25
        and len(areas)
        and float(np.median(areas)) <= 4.0
        and int(np.percentile(areas, 90)) <= 12
    )
    if crumb_soup:
        return True
    # Merged AA ring: near-neutral mass that almost entirely touches other
    # chromatic ink (a one-pixel halo), not a freestanding black wordmark.
    if other_ink is None or not other_ink.any() or not mask.any():
        return False
    dil = cv2.dilate(other_ink.astype(np.uint8), np.ones((3, 3), np.uint8))
    touch = mask.astype(bool) & dil.astype(bool)
    return float(touch.sum()) >= 0.85 * float(mask.sum()) and int(mask.sum()) < int(
        other_ink.sum()
    )


def _check_brand_colours(sketch_pal, output_pal, sketch_layers=None) -> list[Finding]:
    """Every colour the sketch shows must still be in the drawing.

    The GCM monogram: 11% of the sketch's ink, 0% of the output's, scored as
    the best result in the corpus.
    """
    layer_by_colour = {}
    other_by_colour = {}
    if sketch_layers:
        for m, c, _n in sketch_layers:
            key = tuple(int(v) for v in c)
            layer_by_colour[key] = m
        for key, m in layer_by_colour.items():
            rest = np.zeros(m.shape, dtype=bool)
            for k2, m2 in layer_by_colour.items():
                if k2 == key:
                    continue
                # Only chromatic "other" ink — page white is not a neighbour
                # that makes a black wordmark look like AA fringe.
                if _is_page_white(k2):
                    continue
                if not _has_hue(k2):
                    continue
                rest |= m2.astype(bool)
            other_by_colour[key] = rest
    out: list[Finding] = []
    for colour, share in sketch_pal:
        if share < REQUIRED_SHARE or _is_page_white(colour):
            continue
        key = tuple(colour)
        mask = layer_by_colour.get(key)
        if mask is not None and _is_fringe_neutral_layer(
            mask, colour, other_by_colour.get(key)
        ):
            continue
        kept = sum(s for c, s in output_pal if _matches(colour, c))
        if kept < share * MIN_RETAINED:
            out.append(
                Finding(
                    "brand_colour",
                    "block",
                    f"colour {colour} is {share:.1%} of the sketch but "
                    f"{kept:.1%} of the output — an element has been dropped",
                    colour=colour,
                )
            )
    return out


def _ink(arr: np.ndarray) -> float:
    return float((arr[:, :, 3] >= 128).mean())


def _check_collapse(sketch: np.ndarray, output: np.ndarray, sketch_pal, output_pal) -> list[Finding]:
    """The output must still contain the mark.

    Reconstructions that collapsed kept 0.2% to 2% of their sketch's agreement
    — a few stray fragments where a logo had been.
    """
    si, oi = _ink(sketch), _ink(output)
    # Compare ink that is actually drawn. A sketch flattened onto an opaque
    # page is 100% "ink" by alpha, so fall back to the palette's measure.
    if si >= 0.98 or oi >= 0.98:
        if sketch_pal and not output_pal:
            return [Finding("collapse", "block", "the output contains no drawn colour at all")]
        return []
    if si > 0 and oi / si < MIN_INK_RATIO:
        return [
            Finding(
                "collapse",
                "block",
                f"the output carries {oi / si:.1%} of its sketch's ink — the mark has collapsed",
            )
        ]
    return []


def _review_one(o_full: np.ndarray, sketch) -> Review:
    s = _as_rgba(sketch)
    o = o_full
    if o.shape[:2] != s.shape[:2]:
        h, w = s.shape[:2]
        o = np.asarray(
            Image.fromarray(o, "RGBA").resize((w, h), Image.Resampling.LANCZOS)
        )
    sl, ol = _layers(s), _layers(o)
    sp, op = _palette_of(sl), _palette_of(ol)
    rv = Review(sketch_palette=sp, output_palette=op)
    rv.findings += _check_collapse(s, o, sp, op)
    rv.findings += _check_brand_colours(sp, op, sketch_layers=sl)
    rv.findings += _check_small_elements(s, o, sl, ol)
    return rv


def review(output, sketch, *also) -> Review:
    """Is `output` still the logo that the sketch shows?

    `sketch` is what the engine was given; `output` is what it produced, at any
    size. Neither needs to be the original artwork.

    Pass the raw file as well as the prepared one wherever both exist. Each can
    show what the other has lost: on arc__blur_crush, prepare_for_engine damaged
    the sketch so badly that its palette read as one pink at 100%, and a
    restoration that dropped the teal "RESOURCES LTD." passed, because nothing
    it was shown had teal in it. The raw file still did. Measured over every
    candidate on the corpus against the clean masters, reviewing against the
    prepared sketch alone caught 13 of 14 deletions; against both, all 14, with
    no false alarms either way.
    """
    o = _as_rgba(output)
    first = _review_one(o, sketch)
    for extra in also:
        if extra is None:
            continue
        for f in _review_one(o, extra).findings:
            if not any(_same_finding(f, g) for g in first.findings):
                first.findings.append(f)
    return first


def lost_no_more(a: Review | None, b: Review | None) -> bool:
    """Did `a` lose nothing that `b` kept?

    When every candidate is blocked for the same loss, the block cannot choose
    between them. GCM's i-dots are erased before any engine runs, so every
    candidate lacked them and every one was blocked; falling back to the trace
    then threw away a reconstruction that had lost nothing the trace kept.
    """
    fa = [f for f in (a.findings if a else []) if f.severity == "block"]
    fb = [f for f in (b.findings if b else []) if f.severity == "block"]
    for f in fa:
        same = [g for g in fb if _same_finding(f, g)]
        if not same:
            return False
        if f.n is not None and any(g.n is not None and f.n > g.n for g in same):
            return False
    return True


def _same_finding(a: Finding, b: Finding) -> bool:
    """One deletion seen from two sketches is one finding.

    The raw and prepared sketches read the same element slightly differently —
    GCM's red is (167,31,40) in one and (155,28,42) in the other — so the
    comparison is by colour match, not by text. Counting it twice would double
    every catch in Meedo-Me's record.
    """
    if a.check != b.check:
        return False
    if a.colour is None or b.colour is None:
        return True
    return _matches(a.colour, b.colour) and _matches(b.colour, a.colour)


# --------------------------------------------------------------------------
# memory
# --------------------------------------------------------------------------

MAX_REVIEWS = 2000


def record(
    rv: Review,
    *,
    case: str,
    candidate: str,
    run_id: str = "",
    context: str = "",
    path: Path | None = None,
) -> None:
    """Write a verdict into Meedo-Me's ledger.

    Failing open: the ledger is memory, and a memory write must never be the
    reason a restoration fails.
    """
    try:
        from .meedo_ledger import _now, load, save

        data = load(path)
        reviews = data.setdefault("reviews", [])
        reviews.append(
            {
                "ts": _now(),
                "run_id": run_id,
                "case": case,
                "candidate": candidate,
                "context": context,
                "passed": rv.passed,
                "findings": [f.as_dict() for f in rv.findings],
            }
        )
        data["reviews"] = reviews[-MAX_REVIEWS:]
        save(data, path)
        if not rv.passed:
            from .meedo_episodes import from_review_block

            ep_path = Path(path).parent / "meedo_episodes.json" if path else None
            from_review_block(case, candidate, [f.as_dict() for f in rv.findings], path=ep_path)
    except Exception:
        pass


def retract(check: str, reason: str, *, path: Path | None = None) -> int:
    """Withdraw blocks a check made in error, keeping them on file.

    A false alarm deleted from the record teaches nothing, and one left in it
    inflates what Meedo-Me claims to have caught. So each stays, marked with why
    it was wrong, and stops counting as a catch. Returns how many were withdrawn.
    """
    from .meedo_ledger import _now, load, save

    data = load(path)
    n = 0
    for r in data.get("reviews", []):
        if r.get("passed") or r.get("retracted"):
            continue
        blocks = [f for f in r.get("findings", []) if f.get("severity") == "block"]
        if blocks and all(f.get("check") == check for f in blocks):
            r["retracted"] = {"reason": reason, "at": _now()}
            n += 1
    if n:
        save(data, path)
    return n


def catches(path: Path | None = None) -> dict:
    """What Meedo-Me has stopped, by kind — the visible measure of its use."""
    from .meedo_ledger import load

    data = load(path)
    reviews = data.get("reviews", [])
    retracted = [r for r in reviews if r.get("retracted")]
    reviews = [r for r in reviews if not r.get("retracted")]
    blocked = [r for r in reviews if not r.get("passed")]
    by_check: dict[str, int] = {}
    for r in blocked:
        for f in r.get("findings", []):
            if f.get("severity") == "block":
                by_check[f["check"]] = by_check.get(f["check"], 0) + 1
    return {
        "reviewed": len(reviews),
        "blocked": len(blocked),
        "retracted": len(retracted),
        "by_check": by_check,
        "recent_blocks": [
            {"case": r["case"], "candidate": r["candidate"], "why": [f["detail"] for f in r["findings"]]}
            for r in blocked[-5:]
        ],
    }


__all__ = ["Finding", "Review", "catches", "lost_no_more", "palette", "record", "retract", "review"]
