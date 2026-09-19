"""Layer budget — how many paths does this drawing actually need?

The problem
-----------
Anchor economy is the weakest term in `ideality`, and it is the one that most
separates designed artwork from a trace. A hand-built wordmark runs about 57
anchors per 1000 units of contour; the raster-derived brand files in this repo
run 115 and 308. A tracer emits whatever the pixels suggest and stops there — it
has no notion of "that path earns its keep" or "those two paths are the same
shape twice".

Three ideas are borrowed here, each implemented natively rather than pulled in
as a dependency.

**LIVE (Layer-wise Image Vectorization)** builds a drawing by adding shapes one
at a time, largest and most significant first, and stops when adding more stops
improving the reconstruction. The transferable idea is a *budget*: a path only
belongs in the output if removing it measurably hurts. We come at it from the
other end — the tracer has already produced candidate paths, so we test each
one's contribution and drop the ones that do not pay for themselves. Same
criterion, applied subtractively, and far cheaper than re-optimizing from
scratch.

**DiffVG** makes rasterization differentiable so control points can be optimized
by gradient descent against a raster target. We want the same render-compare-
refine loop without the CUDA extension and the several gigabytes of torch that
come with it. Because our geometry is already close and the parameter sets are
tiny — a transform per element, not thousands of free control points — a
gradient-free coordinate search over that handful of parameters converges in a
few dozen renders and needs nothing beyond cairosvg. (This is the same
refinement that lifted recognized glyphs from 0.9479 to 0.9776 agreement; it is
generalized here to any element.)

**DeepSVG** represents a drawing as a hierarchy of paths, each a canonical
sequence of commands. The idea worth keeping is normalization for comparison:
once paths are canonicalized, exact and near duplicates become detectable, and
a tracer that emitted the same contour twice under slightly different numbers
can be cleaned up.

Everything is measured against the source before it is kept. Nothing here is
allowed to trade away shape agreement for a tidier path count: a prune that
costs more than its budget is reverted.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

# A path must cost at least this much IoU when removed to be worth keeping.
DEFAULT_PRUNE_BUDGET = 0.0015
# Total agreement we refuse to drop below, however tempting the saving.
DEFAULT_FLOOR = 0.985

_GROUP = re.compile(r"<g\b[^>]*>.*?</g>", re.S)
_D_ATTR = re.compile(r'\bd\s*=\s*"([^"]*)"', re.S)


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def _render_mask(svg: str, w: int, h: int) -> np.ndarray | None:
    try:
        import cairosvg
    except Exception:
        return None
    try:
        buf = io.BytesIO()
        cairosvg.svg2png(
            bytestring=svg.encode(), write_to=buf,
            output_width=w, output_height=h,
            background_color="rgba(0,0,0,0)",
        )
        buf.seek(0)
        return np.asarray(Image.open(buf).convert("RGBA"))[:, :, 3] >= 128
    except Exception:
        return None


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 0.0


_G_OPEN = re.compile(r"<g\b[^>]*>")
_G_CLOSE = re.compile(r"</g\s*>")


def _split_groups(svg: str) -> tuple[str, list[str], str]:
    """(header, top-level groups, footer) so groups recombine in any subset.

    Depth-counted rather than regex-matched. The idealizer nests potrace's own
    `<g>` inside each element group, and a non-greedy `<g>...</g>` pattern
    closes on the *inner* tag — which silently produced malformed markup that
    rendered empty, so every prune looked like it destroyed the drawing.
    """
    events = [(m.start(), m.end(), 1) for m in _G_OPEN.finditer(svg)]
    events += [(m.start(), m.end(), -1) for m in _G_CLOSE.finditer(svg)]
    events.sort(key=lambda e: e[0])

    groups: list[str] = []
    depth = 0
    start = None
    for a, b, delta in events:
        if delta == 1:
            if depth == 0:
                start = a
            depth += 1
        else:
            depth -= 1
            if depth == 0 and start is not None:
                groups.append(svg[start:b])
                start = None
            if depth < 0:  # malformed input; bail rather than guess
                return svg, [], ""
    if not groups or depth != 0:
        return svg, [], ""
    first = svg.index(groups[0])
    last = svg.rindex(groups[-1]) + len(groups[-1])
    return svg[:first], groups, svg[last:]


def _rebuild(header: str, groups: list[str], footer: str) -> str:
    return header + "".join(groups) + footer


# --------------------------------------------------------------------------
# DeepSVG-style normalization and dedup
# --------------------------------------------------------------------------


def _canonical(d: str) -> str:
    """Coarse canonical form of a path, for equality up to numeric noise."""
    nums = re.findall(r"-?\d*\.?\d+(?:[eE][-+]?\d+)?", d)
    cmds = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]", d)
    if not nums:
        return "|".join(cmds)
    vals = [float(n) for n in nums]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    # Normalize position and scale so the same shape drawn at a different
    # offset or size still compares equal.
    quant = [int(round(((v - lo) / span) * 256)) for v in vals]
    return "".join(cmds) + "|" + ",".join(map(str, quant))


_TRANSFORM = re.compile(r'\btransform\s*=\s*"([^"]*)"')


def dedupe_groups(groups: list[str]) -> tuple[list[str], int]:
    """Drop groups that draw the same geometry in the same place.

    Position is part of the identity. `_canonical` deliberately normalizes a
    path's offset and scale so the same shape compares equal wherever it sits —
    which is what makes it useful for recognition, and exactly wrong for
    deduplication. Keyed on shape alone, the two P's of SUPPLY hash
    identically and the second one is deleted; measured, that cost 0.9793 ->
    0.8049 agreement. So the transforms that place the group are part of the
    key, and only a genuinely redundant redraw is removed.
    """
    seen: set[str] = set()
    out: list[str] = []
    dropped = 0
    for g in groups:
        ds = _D_ATTR.findall(g)
        if not ds:
            out.append(g)
            continue
        shape = "&".join(_canonical(d) for d in ds)
        place = "&".join(_TRANSFORM.findall(g))
        key = f"{shape}@{place}"
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        out.append(g)
    return out, dropped


# --------------------------------------------------------------------------
# LIVE-style path budget
# --------------------------------------------------------------------------


def prune_groups(
    svg: str,
    target: np.ndarray,
    *,
    budget: float = DEFAULT_PRUNE_BUDGET,
    floor: float = DEFAULT_FLOOR,
) -> tuple[str, dict]:
    """Drop every element that does not pay for itself in reconstruction.

    Each element is removed in turn and the result re-rendered. If agreement
    with the source barely moves, the element was not carrying meaning and the
    removal stands. Elements are tested smallest-first, since the cheapest
    candidates are where the unnecessary detail accumulates.
    """
    h, w = target.shape
    header, groups, footer = _split_groups(svg)
    if len(groups) < 2:
        return svg, {"pruned": 0, "kept": len(groups)}

    base = _render_mask(svg, w, h)
    if base is None:
        return svg, {"pruned": 0, "kept": len(groups), "note": "no_renderer"}
    base_iou = _iou(target, base)
    # The floor has to be relative to what this drawing actually achieves. An
    # absolute bar is unreachable for a heavily degraded source — a trace that
    # tops out at 0.79 can never clear 0.985, so every prune was rejected and
    # the whole pass silently did nothing. Protect the agreement we have.
    eff_floor = min(floor, base_iou - budget)

    # Smallest rendered contribution first.
    order = sorted(range(len(groups)), key=lambda i: len(groups[i]))
    keep = list(range(len(groups)))
    pruned = 0
    cur_iou = base_iou
    for i in order:
        if len(keep) <= 1:
            break
        trial = [groups[j] for j in keep if j != i]
        m = _render_mask(_rebuild(header, trial, footer), w, h)
        if m is None:
            continue
        trial_iou = _iou(target, m)
        if trial_iou >= cur_iou - budget and trial_iou >= eff_floor:
            keep.remove(i)
            cur_iou = trial_iou
            pruned += 1

    out = _rebuild(header, [groups[i] for i in keep], footer)
    return out, {
        "pruned": pruned,
        "kept": len(keep),
        "iou_before": round(base_iou, 4),
        "iou_after": round(cur_iou, 4),
    }


# --------------------------------------------------------------------------
# DiffVG-style render / compare / refine
# --------------------------------------------------------------------------


def refine_group_placement(
    svg: str,
    target: np.ndarray,
    *,
    max_shift: float = 2.0,
    steps: int = 2,
) -> tuple[str, dict]:
    """Nudge the whole drawing to best overlap the source.

    A gradient-free coordinate search over a translation, which is all the
    freedom a composed drawing needs once its elements are individually placed.
    Cheap: a couple of dozen renders, no autograd and no CUDA extension.
    """
    h, w = target.shape
    best = _render_mask(svg, w, h)
    if best is None:
        return svg, {"refined": False}
    best_iou = _iou(target, best)
    best_svg = svg
    best_off = (0.0, 0.0)

    header, groups, footer = _split_groups(svg)
    if not groups:
        return svg, {"refined": False}

    shift = max_shift
    for _ in range(steps):
        improved = False
        for dx, dy in (
            (shift, 0.0), (-shift, 0.0), (0.0, shift), (0.0, -shift),
            (shift, shift), (-shift, -shift), (shift, -shift), (-shift, shift),
        ):
            ox, oy = best_off[0] + dx, best_off[1] + dy
            trial = (
                header
                + f'<g transform="translate({ox:.3f} {oy:.3f})">'
                + "".join(groups)
                + "</g>"
                + footer
            )
            m = _render_mask(trial, w, h)
            if m is None:
                continue
            iou = _iou(target, m)
            if iou > best_iou + 1e-6:
                best_iou, best_svg, best_off = iou, trial, (ox, oy)
                improved = True
        if not improved:
            shift /= 2.0
    return best_svg, {
        "refined": best_off != (0.0, 0.0),
        "offset": [round(best_off[0], 3), round(best_off[1], 3)],
        "iou": round(best_iou, 4),
    }


# --------------------------------------------------------------------------
# public
# --------------------------------------------------------------------------


@dataclass
class CompactResult:
    svg: str
    report: dict = field(default_factory=dict)


def compact(
    svg: str,
    arr: np.ndarray,
    *,
    budget: float = DEFAULT_PRUNE_BUDGET,
    floor: float = DEFAULT_FLOOR,
    refine: bool = True,
) -> CompactResult:
    """Dedupe, prune to a budget, then refine placement — measured throughout.

    Returns the original SVG unchanged if any step would cost more agreement
    than it saves. Fails open on a missing renderer.
    """
    if not svg:
        return CompactResult(svg, {"note": "empty"})
    target = arr[:, :, 3] >= 128 if arr.ndim == 3 and arr.shape[2] == 4 else None
    if target is None:
        return CompactResult(svg, {"note": "no_alpha_target"})
    h, w = target.shape

    before = _render_mask(svg, w, h)
    if before is None:
        return CompactResult(svg, {"note": "no_renderer"})
    iou_before = _iou(target, before)

    header, groups, footer = _split_groups(svg)
    groups, dropped = dedupe_groups(groups)
    work = _rebuild(header, groups, footer) if groups else svg

    work, prune_report = prune_groups(work, target, budget=budget, floor=floor)

    refine_report: dict = {}
    if refine:
        work, refine_report = refine_group_placement(work, target)

    after = _render_mask(work, w, h)
    iou_after = _iou(target, after) if after is not None else 0.0
    # Same relative rule as the prune loop: never drop below what we started
    # with, but do not demand a bar this drawing was never going to clear.
    if after is None or iou_after < min(floor, iou_before - budget):
        # Never trade agreement for tidiness.
        return CompactResult(
            svg,
            {
                "note": "reverted",
                "deduped": dropped,
                **prune_report,
                **refine_report,
                "iou_before": round(iou_before, 4),
                "iou_after": round(iou_before, 4),  # unchanged: original kept
                "iou_rejected": round(iou_after, 4),
            },
        )

    return CompactResult(
        work,
        {
            "deduped": dropped,
            **prune_report,
            **refine_report,
            "iou_before": round(iou_before, 4),
            "iou_after": round(iou_after, 4),
        },
    )


__all__ = [
    "CompactResult",
    "compact",
    "prune_groups",
    "dedupe_groups",
    "refine_group_placement",
]
