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

# An output carrying less ink than this share of its sketch's has collapsed.
# Every genuine restoration on the corpus keeps well over half; the failures
# that prompted this kept 0.2% to 2%.
MIN_INK_RATIO = 0.10


@dataclass
class Finding:
    check: str
    severity: str  # "block" or "warn"
    detail: str
    colour: tuple[int, int, int] | None = None

    def as_dict(self) -> dict:
        d = {"check": self.check, "severity": self.severity, "detail": self.detail}
        if self.colour is not None:
            d["colour"] = list(self.colour)
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


def palette(img) -> list[tuple[tuple[int, int, int], float]]:
    """The colours a designer would name, each with its share of the ink.

    Uses the reconstruction's own layer reading, so "brand colour" means the
    same thing to the reviewer as to the engine: colours pooled as the eye
    pools them, the page removed, soft edges and halos resolved rather than
    counted.
    """
    from .idealize import _quantize_layers

    arr = _as_rgba(img)
    layers = _quantize_layers(arr, max_layers=8)
    total = float(sum(n for _, _, n in layers))
    if total <= 0:
        return []
    return [(tuple(int(v) for v in c), n / total) for _, c, n in layers]


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
    hw, _sw, lw = _hue(want)
    hh, sh, lh = _hue(have)
    if _has_hue(want):
        if sh < DARK_TINT:
            return False
        d = abs(hw - hh) % 360.0
        return min(d, 360.0 - d) <= HUE_FAMILY
    return sh < 0.20 and abs(lw - lh) <= LIGHTNESS_MATCH


# --------------------------------------------------------------------------
# checks — each one is something the engine has actually done
# --------------------------------------------------------------------------


def _check_brand_colours(sketch_pal, output_pal) -> list[Finding]:
    """Every colour the sketch shows must still be in the drawing.

    The GCM monogram: 11% of the sketch's ink, 0% of the output's, scored as
    the best result in the corpus.
    """
    out: list[Finding] = []
    for colour, share in sketch_pal:
        if share < REQUIRED_SHARE or _is_page_white(colour):
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
    sp = palette(s)
    op = palette(o)
    rv = Review(sketch_palette=sp, output_palette=op)
    rv.findings += _check_collapse(s, o, sp, op)
    rv.findings += _check_brand_colours(sp, op)
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
    except Exception:
        pass


def catches(path: Path | None = None) -> dict:
    """What Meedo-Me has stopped, by kind — the visible measure of its use."""
    from .meedo_ledger import load

    data = load(path)
    reviews = data.get("reviews", [])
    blocked = [r for r in reviews if not r.get("passed")]
    by_check: dict[str, int] = {}
    for r in blocked:
        for f in r.get("findings", []):
            if f.get("severity") == "block":
                by_check[f["check"]] = by_check.get(f["check"], 0) + 1
    return {
        "reviewed": len(reviews),
        "blocked": len(blocked),
        "by_check": by_check,
        "recent_blocks": [
            {"case": r["case"], "candidate": r["candidate"], "why": [f["detail"] for f in r["findings"]]}
            for r in blocked[-5:]
        ],
    }


__all__ = ["Finding", "Review", "catches", "palette", "record", "review"]
