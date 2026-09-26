#!/usr/bin/env python3
"""Vectorize a flat logo raster via vtracer, then rasterize to a print PNG.

Usage:
  python scripts/logo_vectorize.py input.png output.png [--min-height 3000]

Capability limit: best for flat / low-color lockups. Photo-like marks should
use Real-ESRGAN instead. Requires: pip install vtracer pillow
Optional: cairosvg (preferred SVG→PNG). Falls back to a coarse path fill if
cairosvg is missing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

# Shared plate / palette finish (same as ESRGAN path).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from logo_raster_finish import (  # noqa: E402
    edge_energy,
    elongated_thin_components,
    finalize_restore,
    ink_mask_iou,
    is_thin_stroke_mark,
    is_thin_wordmark,
    load_rgba,
    prepare_for_engine,
    quantize_thin_path,
    save_rgba,
    stamp_centerline,
)


def _vectorize_to_svg(src: Path, svg: Path, *, thin_mark: bool = False) -> None:
    import vtracer

    # Thin strokes: spline on a 2–4 color quantized raster; low speckle +
    # high color precision keep centerlines. Sharp terminators via a lower
    # corner threshold (more corners, less rounded caps).
    # Flat fills: spline + mild filtering for clean solids (Swift/Propak).
    vtracer.convert_image_to_svg_py(
        str(src),
        str(svg),
        colormode="color",
        hierarchical="stacked",
        mode="spline",
        filter_speckle=1 if thin_mark else 4,
        color_precision=8 if thin_mark else 6,
        layer_difference=16,
        corner_threshold=40 if thin_mark else 60,
        length_threshold=2.0 if thin_mark else 4.0,
        max_iterations=12 if thin_mark else 10,
        splice_threshold=30 if thin_mark else 45,
        path_precision=4 if thin_mark else 3,
    )


def _is_thin_wordmark(arr: np.ndarray) -> bool:
    """Wide short lockups with sparse ink — Arc-style wordmarks."""
    return is_thin_wordmark(arr)


def _lanczos_to_height(arr: np.ndarray, min_height: int) -> np.ndarray:
    h0 = arr.shape[0]
    if h0 >= min_height:
        return arr
    scale = min_height / float(h0)
    nw = max(1, int(round(arr.shape[1] * scale)))
    return np.asarray(
        Image.fromarray(arr, "RGBA").resize((nw, min_height), Image.Resampling.LANCZOS)
    )


def _rasterize_svg(svg: Path, out: Path, min_height: int) -> bool:
    # PyMuPDF works on Windows without cairo DLLs.
    try:
        import fitz

        doc = fitz.open(str(svg))
        page = doc[0]
        # Scale so page height ≈ min_height.
        rect = page.rect
        if rect.height <= 0:
            return False
        zoom = min_height / float(rect.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=True)
        pix.save(str(out))
        doc.close()
        if out.is_file() and out.stat().st_size > 0:
            Image.open(out).convert("RGBA").save(out)
            return True
    except Exception as e:
        print(f"pymupdf rasterize failed ({e})", file=sys.stderr)

    try:
        from reportlab.graphics import renderPM
        from svglib.svglib import svg2rlg

        drawing = svg2rlg(str(svg))
        if drawing is None:
            raise RuntimeError("svg2rlg returned None")
        if drawing.height and drawing.height > 0:
            scale = min_height / float(drawing.height)
            drawing.width *= scale
            drawing.height *= scale
            drawing.scale(scale, scale)
        renderPM.drawToFile(drawing, str(out), fmt="PNG", bg=0x00000000)
        if out.is_file() and out.stat().st_size > 0:
            Image.open(out).convert("RGBA").save(out)
            return True
    except Exception as e:
        print(f"svglib rasterize failed ({e})", file=sys.stderr)

    try:
        import cairosvg  # type: ignore

        cairosvg.svg2png(
            url=str(svg),
            write_to=str(out),
            output_height=min_height,
            background_color="rgba(0,0,0,0)",
        )
        return out.is_file() and out.stat().st_size > 0
    except Exception as e:
        print(f"cairosvg unavailable ({e})", file=sys.stderr)
        return False


def _is_flat_enough(arr: np.ndarray, max_colors: int = 80, coverage: float = 0.70) -> bool:
    """Skip vectorize when ink cannot be explained by a small palette.

    Softened from 48/0.82 — GCM/Propak/Trialta JPEG mottling still vectorizes
    well after plate knockout; the old gate skipped them entirely.
    """
    alpha = arr[:, :, 3]
    rgb = arr[:, :, :3].astype(np.int32)
    lum = rgb.mean(axis=2)
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    ink = (alpha > 40) & ~((lum < 28) & (sat < 18)) & ~((lum > 245) & (sat < 12))
    n_ink = int(ink.sum())
    if n_ink < 80:
        return False
    q = (rgb[ink] // 16).astype(np.int32)
    keys = q[:, 0] * 4096 + q[:, 1] * 64 + q[:, 2]
    uniq, counts = np.unique(keys, return_counts=True)
    order = np.argsort(-counts)
    covered = counts[order[:max_colors]].sum() / float(n_ink)
    return covered >= coverage or len(uniq) <= max_colors


def _looks_like_swift_lockup(arr: np.ndarray) -> bool:
    """Orange + black flat lockup (Swift document bars + wordmark).

    Solid chrome (orange-only) stays on decompose_by_color — Swift sectional
    regresses solid import_combo (~0.929 → ~0.916).
    """
    alpha = arr[:, :, 3]
    rgb = arr[:, :, :3].astype(np.int32)
    ink = alpha > 40
    if int(ink.sum()) < 200:
        return False
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    orange = ink & (r > 140) & (g < 140) & (b < 120) & ((r - g) > 40)
    black = ink & (r < 60) & (g < 60) & (b < 60)
    o = float(orange.sum()) / float(ink.sum())
    k = float(black.sum()) / float(ink.sum())
    return o >= 0.12 and k >= 0.05


def _is_plate_mush(arr: np.ndarray) -> bool:
    """Blur/JPEG crush that filled the ink bbox into a near-solid plate."""
    alpha = arr[:, :, 3]
    ink = alpha > 40
    n_ink = int(ink.sum())
    if n_ink < 400:
        return True
    ys, xs = np.where(ink)
    if ys.size == 0:
        return True
    bbox = float((int(ys.max()) - int(ys.min()) + 1) * (int(xs.max()) - int(xs.min()) + 1))
    dens = float(n_ink) / max(bbox, 1.0)
    # Healthy Swift dens ~0.42–0.55; blur_crush plates sit >0.85.
    return dens > 0.70


# Below this, the reconstruction did not clean the mark up — it collapsed.
# Every genuine one on the corpus lands above 0.43; the failures land at 0.002,
# 0.021 and 0.000, so there is nothing delicate about where this sits. Anywhere
# from 0.20 to 0.50 selects identically.
COLLAPSE_FLOOR = 0.30

# When the two reconstructions agree with the source this closely, agreement is
# not telling them apart and build quality decides. On gcm__import_combo they
# sit 0.004 apart and differ by 0.105 in what they actually score; picking on
# agreement alone takes the worse one. 0.01 and 0.02 behave identically here.
IDEALITY_TIE = 0.02


@dataclass
class Candidate:
    """One finished restoration, with the two numbers that decide between them."""

    name: str
    finished: np.ndarray
    svg: Path | None
    agreement: float
    ideality: float
    review: object | None = None

    @property
    def passed_review(self) -> bool:
        """Meedo-Me found nothing that disqualifies it (fails open if it could not look)."""
        return self.review is None or bool(getattr(self.review, "passed", True))

    @property
    def has_vector(self) -> bool:
        """False when this is a raster fallback with no vector behind it."""
        return self.svg is not None


def _agreement(finished: np.ndarray, prepared: np.ndarray) -> float:
    """Ink IoU against the source, measured at the source's own scale."""
    h0, w0 = prepared.shape[:2]
    small = np.asarray(
        Image.fromarray(finished, "RGBA").resize((w0, h0), Image.Resampling.LANCZOS)
    )
    return float(ink_mask_iou(prepared, small))


def _ideality(svg: Path | None) -> float:
    """Reference-free vector quality, or 0.0 when there is no vector at all."""
    if svg is None or not svg.is_file() or svg.stat().st_size < 32:
        return 0.0
    try:
        from tools.logo_vectorizer.ideality import score_svg

        return float(score_svg(svg).ideality)
    except Exception:
        return 0.0


def _candidate(
    name: str, png: Path, svg: Path | None, prepared: np.ndarray
) -> Candidate:
    finished = load_rgba(png)
    return Candidate(
        name=name,
        finished=finished,
        svg=svg if (svg is not None and svg.is_file() and svg.stat().st_size >= 32) else None,
        agreement=_agreement(finished, prepared),
        ideality=_ideality(svg),
    )


def _meedo_review(cand: Candidate, prepared: np.ndarray, source: np.ndarray, case: str) -> Candidate:
    """Have Meedo-Me check a candidate against the sketch it came from.

    Failing open: if the reviewer cannot run, the candidate is judged as it
    would have been without it. A missing reviewer must never block a restore.
    """
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from tools.logo_vectorizer.meedo_review import record, review

        rv = review(cand.finished, prepared, source)
        cand.review = rv
        record(rv, case=case, candidate=cand.name, context="convert")
        if not rv.passed:
            print(f"{cand.name}: {rv.summary()}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 — fail open
        print(f"Meedo-Me review unavailable ({e})", file=sys.stderr)
    return cand


def _lost_no_more(a: Candidate, b: Candidate) -> bool:
    """`a` lost nothing that `b` kept (see meedo_review.lost_no_more)."""
    try:
        from tools.logo_vectorizer.meedo_review import lost_no_more

        return lost_no_more(a.review, b.review)
    except Exception:  # noqa: BLE001 — without a reviewer, nothing is known
        return False


def _brand_colour_blocks(cand: Candidate) -> int:
    """How many brand-colour deletions Meedo-Me blocked on this candidate."""
    if cand.review is None:
        return 0
    return sum(
        1
        for f in cand.review.findings
        if getattr(f, "check", None) == "brand_colour"
        and getattr(f, "severity", None) == "block"
    )


def _minds_rank_enabled() -> bool:
    """Read LOGO_MINDS_RANK the same way the improve loop records it."""
    try:
        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from tools.logo_vectorizer.ai_advisors.minds import minds_rank_enabled

        return bool(minds_rank_enabled())
    except Exception:
        return False


def _minds_rank_pick(
    sketch: Image.Image,
    traced: Candidate,
    ideal: Candidate,
    *,
    case: str,
) -> str | None:
    """Ask available minds which candidate to ship. Returns 'traced'|'ideal'|None.

    Both presentation orders must agree (see minds.compare_both_ways). A split
    or unavailable mind does not move the derived winner. Fail-open always.
    """
    try:
        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from tools.logo_vectorizer.ai_advisors import minds as M
    except Exception as e:  # noqa: BLE001
        print(f"minds_rank unavailable ({e})", file=sys.stderr)
        return None

    minds = [m for m in ("claude", "gemini") if M.available(m)]
    if not minds:
        print("minds_rank: no mind available; keep derived winner", file=sys.stderr)
        return None

    picks: list[str] = []
    t_img = Image.fromarray(traced.finished, "RGBA")
    i_img = Image.fromarray(ideal.finished, "RGBA")
    for mind in minds:
        try:
            r = M.compare_both_ways(
                mind,
                sketch,
                t_img,
                i_img,
                case=case,
                session=f"minds-rank-{case}",
            )
        except Exception as e:  # noqa: BLE001
            print(f"minds_rank {mind} failed ({e})", file=sys.stderr)
            continue
        # compare_both_ways labels first=traced, second=ideal
        pick = {"first": "traced", "second": "ideal"}.get(r.get("pick") or "", r.get("pick"))
        print(f"minds_rank {mind}: pick={pick}", file=sys.stderr)
        if pick in ("traced", "ideal"):
            picks.append(pick)
    if not picks:
        return None
    # All decisive minds must agree; otherwise leave the derived rule alone.
    if len(set(picks)) == 1:
        return picks[0]
    print(
        f"minds_rank: minds disagreed {picks}; keep derived winner",
        file=sys.stderr,
    )
    return None


def _prefer_reconstruction(ideal: Candidate, traced: Candidate) -> bool:
    """Should the reconstruction ship instead of the trace?

    Derived from the corpus rather than argued. Every candidate on all 18 pairs
    was built and scored against the clean original it never gets to see, so a
    rule could be chosen on what predicts quality instead of what sounds right.

    Two things that sound right are not:

    Agreement with the source does not rank candidates within a pair. It is the
    strongest signal across the corpus (r=+0.57), but that mostly measures which
    pair is easy. On trialta__import_combo the reconstruction agrees at 0.180
    against the trace's 0.914 and still scores better, 0.5787 to 0.5045. Worse,
    agreement structurally rewards doing nothing: where tracing fails it falls
    back to a Lanczos upscale, which is the degraded source enlarged and so
    agrees with it almost perfectly while being no restoration at all.

    Nor does ideality, on its own (r=+0.08): a well-built drawing of the wrong
    shape is still wrong.

    What does separate them is whether the trace produced a vector at all. When
    it fell through to Lanczos, the reconstruction is better on 8 of those 9
    pairs — and the exception is one where the reconstruction itself collapsed
    to 0.002 agreement, which COLLAPSE_FLOOR catches. When the trace did
    produce a vector the signals available here genuinely cannot pick a winner
    (5 of 9 favour the reconstruction, with no signal separating them), so the
    trace keeps it and the opportunity is left on the table rather than guessed
    at.

    Measured over the corpus against always keeping the trace:

        composite     0.7433 -> 0.7567
        composite_v2  0.7455 -> 0.7584
        reconstruction ships on 7 of 18, and no pair gets worse

    The oracle that always picks the best candidate reaches 0.7688, so this
    takes about half of what is there and none of the risk. Closing the rest
    needs a signal that ranks candidates inside a pair, which none of the three
    measured here does.
    """
    if traced.has_vector:
        return False
    return ideal.agreement >= COLLAPSE_FLOOR


def _build_idealize(
    prepared: np.ndarray,
    source: np.ndarray,
    out_dir: Path,
    *,
    min_height: int,
    source_path: Path | None = None,
) -> Candidate | None:
    """Reconstruction candidate — rebuild the mark as designed geometry.

    Every other path in this file traces the boundary it is given: vtracer and
    potrace both answer "what curve follows these pixels?". That question has a
    ceiling built into it, because the pixels carry the flaws. A bar that was
    drawn as a rectangle arrives with a ragged edge, and a faithful trace
    faithfully reproduces the rag.

    This asks the other question — "what did someone draw?" — and emits that
    instead: a rectangle as `<rect>`, a circle as `<circle>`, a letter as the
    real outline from the font it was set in, and only what it cannot name as a
    fitted curve.

    It is built from both the prepared raster and the untouched source, and the
    closer of the two is kept. `prepare_for_engine` is tuned for tracing — it
    knocks out plates, punches counters, strips halos and re-snaps the palette
    — and that preprocessing cuts both ways here. On gcm__downscale_jpeg it
    costs the reconstruction 0.9894 agreement down to 0.7835, because this
    engine does its own layer decomposition and would rather have the original.
    On propak and trialta, whose degraded alpha is genuinely broken, removing
    the knockout collapses the result to near zero. Neither input wins
    everywhere, so both are built and measured.
    """
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from tools.logo_vectorizer.idealize import idealize_layered
        from tools.logo_vectorizer.sectional import rasterize_svg  # type: ignore
    except Exception as e:  # noqa: BLE001 — fail-open, tracing still runs
        print(f"idealize unavailable ({e})", file=sys.stderr)
        return None

    candidates: list[Candidate] = []
    for tag, arr in (("prepared", prepared), ("source", source)):
        svg_path = out_dir / f"ideal_{tag}.svg"
        png_path = out_dir / f"ideal_{tag}.png"
        try:
            svg = idealize_layered(arr, source_path=source_path)
            if not svg:
                continue
            svg_path.write_text(svg, encoding="utf-8")
            rasterize_svg(
                svg_path,
                png_path,
                width=max(min_height, prepared.shape[1]),
                background="transparent",
                prefer_chrome=False,
            )
            finished = finalize_restore(
                load_rgba(png_path),
                prepared,
                min_palette=0.14,
                max_aspect_drift=0.20,
            )
            if finished.shape[0] < min_height:
                finished = _lanczos_to_height(finished, min_height)
                finished = finalize_restore(
                    finished, prepared, min_palette=0.10, max_aspect_drift=0.25
                )
            save_rgba(png_path, finished)
            cand = _candidate(f"idealize/{tag}", png_path, svg_path, prepared)
        except Exception as e:  # noqa: BLE001 — fail-open
            print(f"idealize from {tag} failed ({e})", file=sys.stderr)
            continue
        print(
            f"idealize/{tag}: agreement={cand.agreement:.4f} "
            f"ideality={cand.ideality:.4f}",
            file=sys.stderr,
        )
        candidates.append(cand)

    if not candidates:
        return None
    # Meedo-Me first. On gcm__downscale_jpeg the variant built from the raw
    # source agreed with its sketch better (0.858 against 0.767) and had
    # deleted the red monogram; agreement alone chose it. A variant that has
    # dropped an element does not get to compete with one that has not.
    case = source_path.stem if source_path is not None else "unknown"
    candidates = [_meedo_review(c, prepared, source, case) for c in candidates]
    clean = [c for c in candidates if c.passed_review]
    if clean:
        candidates = clean
    top = max(candidates, key=lambda c: c.agreement)
    near = [c for c in candidates if c.agreement >= top.agreement - IDEALITY_TIE]
    return max(near, key=lambda c: c.ideality)


def _try_sectional_briyszier(
    prepared: np.ndarray,
    dest: Path,
    *,
    min_height: int,
    svg_out: Path | None,
    target_iou: float = 0.90,
) -> bool:
    """briyszier sectional path — prefer when it beats target IoU vs prepared.

    Uses the restore-safe decomposer (no residual AA scoop, no recreate
    oversmooth tuning). Document export keeps residual+tuning separately in
    process_header_logo — that path must not regress this restore gate.
    """
    if _is_plate_mush(prepared):
        print("sectional skipped (plate mush density)", file=sys.stderr)
        return False
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from tools.logo_vectorizer.sectional import (  # type: ignore
            rasterize_svg,
            vectorize_sectional,
        )
        from tools.logo_vectorizer.sections import (  # type: ignore
            decompose_by_color,
            decompose_swift_supply,
        )
    except Exception as e:
        print(f"sectional unavailable ({e})", file=sys.stderr)
        return False

    img = Image.fromarray(prepared, "RGBA")
    with tempfile.TemporaryDirectory(prefix="swift_sectional_") as td:
        td_path = Path(td)
        svg_path = td_path / "out.svg"
        png_path = td_path / "out.png"
        try:
            if _looks_like_swift_lockup(prepared):
                # Restore-safe: no residual scoop (locks JPEG mush).
                sections = decompose_swift_supply(img, scoop_residual=False)
            else:
                sections = decompose_by_color(img)
            result = vectorize_sectional(img, sections)
            svg_path.write_text(result.svg, encoding="utf-8")
            # cairosvg-first (engine default). Chrome is last-resort only and
            # skipped when LOGO_NO_CHROME=1 — never gate quality on Chrome.
            rasterize_svg(
                svg_path,
                png_path,
                width=max(min_height, prepared.shape[1]),
                background="transparent",
                prefer_chrome=False,
            )
            restored = load_rgba(png_path)
            finished = finalize_restore(
                restored,
                prepared,
                min_palette=0.14,
                max_aspect_drift=0.20,
            )
            # Resize-compare IoU at prepared size (briyszier gate).
            h0, w0 = prepared.shape[:2]
            small = np.asarray(
                Image.fromarray(finished, "RGBA").resize(
                    (w0, h0), Image.Resampling.LANCZOS
                )
            )
            iou = float(ink_mask_iou(prepared, small))
            if iou < target_iou:
                print(
                    f"sectional IoU {iou:.3f} < {target_iou}; fall through",
                    file=sys.stderr,
                )
                return False
            # Upscale to print height if needed.
            if finished.shape[0] < min_height:
                finished = _lanczos_to_height(finished, min_height)
                finished = finalize_restore(
                    finished, prepared, min_palette=0.10, max_aspect_drift=0.25
                )
            save_rgba(dest, finished)
            if svg_out is not None:
                svg_out.write_text(result.svg, encoding="utf-8")
            print(f"sectional accepted (iou={iou:.3f})", file=sys.stderr)
            return True
        except Exception as e:
            print(f"sectional failed ({e}); fall through", file=sys.stderr)
            return False


def convert(
    src: Path,
    dest: Path,
    min_height: int = 3000,
    svg_out: Path | None = None,
    *,
    polish: bool = False,
    idealize: bool = False,
) -> Path:
    if not src.is_file():
        raise FileNotFoundError(src)

    source = load_rgba(src)
    prepared = prepare_for_engine(source)
    if not _is_flat_enough(prepared):
        raise RuntimeError("source not flat enough for vectorize")

    # Optional hierarchical polish (Gigapixel/Upscayl/Remacri/UltraSharp/
    # Real-ESRGAN/GFPGAN/…) BEFORE sectional / VTracer. Default off for the
    # improve-loop restore path so Swift mean 0.9269 cannot regress; enable
    # via --polish or LOGO_PRE_POLISH=1 for low-res customer imports.
    if polish or os.environ.get("LOGO_PRE_POLISH", "").strip() in {
        "1",
        "true",
        "yes",
    }:
        try:
            from tools.logo_vectorizer.raster_polish import polish_raster

            polished = polish_raster(
                Image.fromarray(prepared, "RGBA"),
                target_long_side=max(min_height, 2048),
                use_neural=True,
                use_gfpgan=False,  # faces ≠ logos by default
            )
            cand = np.asarray(polished.image.convert("RGBA"), dtype=np.uint8)
            # Honesty gate: polish must not dissolve ink vs prepared.
            h0, w0 = prepared.shape[:2]
            small = np.asarray(
                Image.fromarray(cand, "RGBA").resize(
                    (w0, h0), Image.Resampling.LANCZOS
                )
            )
            if ink_mask_iou(prepared, small) >= 0.92:
                prepared = prepare_for_engine(cand)
                print(
                    f"pre-polish accepted ({polished.engine})",
                    file=sys.stderr,
                )
            else:
                print(
                    f"pre-polish rejected ({polished.engine}); keep prepared",
                    file=sys.stderr,
                )
        except Exception as exc:  # noqa: BLE001 — fail-open
            print(f"pre-polish skipped ({exc})", file=sys.stderr)

    if not (
        idealize
        or os.environ.get("LOGO_IDEALIZE", "").strip() in {"1", "true", "yes"}
    ):
        return _build_traced(prepared, dest, min_height=min_height, svg_out=svg_out)

    # Both candidates, then pick. See `_prefer_reconstruction`.
    with tempfile.TemporaryDirectory(prefix="swift_compare_") as cmp_td:
        cmp_path = Path(cmp_td)
        t_png, t_svg = cmp_path / "traced.png", cmp_path / "traced.svg"
        _build_traced(prepared, t_png, min_height=min_height, svg_out=t_svg)
        traced = _candidate("traced", t_png, t_svg, prepared)

        ideal = _build_idealize(
            prepared,
            source,
            cmp_path,
            min_height=min_height,
            source_path=src,
        )

        _meedo_review(traced, prepared, source, src.stem)

        # Keep both candidates for studying the choice between them (who picks
        # the better one, and by what signal). Off unless asked for; it only
        # copies files and never changes what ships.
        keep = os.environ.get("LOGO_KEEP_CANDIDATES", "").strip()
        if keep:
            kdir = Path(keep)
            kdir.mkdir(parents=True, exist_ok=True)
            kept = {"traced": traced, "ideal": ideal}
            for role, cand in kept.items():
                if cand is not None:
                    save_rgba(kdir / f"{src.stem}__{role}.png", cand.finished)
            ideal_less = False
            if ideal is not None and traced is not None:
                ideal_less = _lost_no_more(ideal, traced) and not _lost_no_more(
                    traced, ideal
                )
            payload = {
                role: {
                    "name": c.name,
                    "has_vector": c.has_vector,
                    "agreement": c.agreement,
                    "ideality": c.ideality,
                    "passed_review": c.passed_review,
                    "review": None if c.review is None else c.review.as_dict(),
                }
                for role, c in kept.items()
                if c is not None
            }
            # Pair-level flag so logo_minds_rank.derived() matches convert().
            payload["ideal_lost_strictly_less"] = bool(ideal_less)
            (kdir / f"{src.stem}__candidates.json").write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )

        # Identity first. A candidate Meedo-Me passed beats one it blocked,
        # whatever the metric says: on arc__downscale_jpeg the trace drops the
        # whole "RESOURCES LTD." line and still out-scores the reconstruction
        # that kept it, because the score blends a missing element into one
        # term among five. Only between two candidates that are both still the
        # logo does the derived rule decide.
        #
        # When both are blocked but ideal lost *strictly less* (tagline letters
        # retained, trace dropped them), ship ideal even if the trace has a
        # vector — `_prefer_reconstruction` alone would keep the wrong red
        # trace forever once has_vector is true.
        #
        # Brand-colour deletions outrank other blocks for selection: on
        # arc__import_combo the trace painted RESOURCES LTD. red (brand_colour
        # block) while ideal kept teal but missed a period (small_element).
        # lost_no_more cannot compare different checks, so brand_colour must
        # decide explicitly.
        winner = traced
        if ideal is not None:
            ideal_less = _lost_no_more(ideal, traced) and not _lost_no_more(
                traced, ideal
            )
            if ideal.passed_review and not traced.passed_review:
                winner = ideal
            elif (
                not ideal.passed_review
                and not traced.passed_review
                and ideal_less
            ):
                winner = ideal
            elif (
                _brand_colour_blocks(traced) > _brand_colour_blocks(ideal)
                and ideal.agreement >= COLLAPSE_FLOOR
            ):
                winner = ideal
            elif ideal.passed_review and _prefer_reconstruction(ideal, traced):
                winner = ideal
            elif (
                not ideal.passed_review
                and not traced.passed_review
                and _lost_no_more(ideal, traced)
                and _prefer_reconstruction(ideal, traced)
            ):
                # Both blocked for the same loss (GCM: the i-dots are erased
                # before either engine runs). The block cannot choose between
                # them, so the derived rule does, as if neither were blocked.
                winner = ideal
        # Opt-in minds ranking (LOGO_MINDS_RANK): when both still pass review,
        # Gemini/Claude may override the derived rule — the in-pair signal
        # board #4 is measuring. Fail-open: split/error/unavailable keeps
        # `winner` as derived. Identity first stays absolute above.
        if (
            ideal is not None
            and ideal.passed_review
            and traced.passed_review
            and _minds_rank_enabled()
        ):
            pick = _minds_rank_pick(
                # The sketch as it came in — what the reviewer judges against
                # and what scripts/logo_minds_rank.py measured the minds on.
                Image.fromarray(source, "RGBA"),
                traced,
                ideal,
                case=src.stem,
            )
            if pick == "ideal":
                winner = ideal
            elif pick == "traced":
                winner = traced

        if not winner.passed_review:
            print(
                f"Meedo-Me: no candidate passed review; shipping {winner.name} "
                "as the conservative default",
                file=sys.stderr,
            )
        print(
            f"shipping {winner.name} "
            f"(agreement={winner.agreement:.4f} ideality={winner.ideality:.4f})",
            file=sys.stderr,
        )
        save_rgba(dest, winner.finished)
        if svg_out is not None and winner.svg is not None:
            svg_out.write_bytes(winner.svg.read_bytes())
    return dest


def _build_traced(
    prepared: np.ndarray,
    dest: Path,
    *,
    min_height: int,
    svg_out: Path | None,
) -> Path:
    """The tracing path, exactly as it was: sectional, then vtracer, then Lanczos."""
    # briyszier sectional first for Swift / flat multi-color lockups (restore-
    # safe settings). AI advisors are never required here — local vtracer +
    # Inkscape (ensemble) are the fallbacks when sectional declines.
    if _try_sectional_briyszier(
        prepared, dest, min_height=min_height, svg_out=svg_out
    ):
        return dest

    thin = is_thin_stroke_mark(prepared)
    if thin:
        prepared = quantize_thin_path(prepared, max_colors=4)
    with tempfile.TemporaryDirectory(prefix="swift_vtrace_") as td:
        td_path = Path(td)
        work = td_path / "in.png"
        save_rgba(work, prepared)
        svg = td_path / "out.svg"
        _vectorize_to_svg(work, svg, thin_mark=thin)
        if not svg.is_file() or svg.stat().st_size < 32:
            raise RuntimeError("vtracer produced empty SVG")
        tmp_png = td_path / "out.png"
        if not _rasterize_svg(svg, tmp_png, min_height):
            raise RuntimeError("SVG rasterize failed (install cairosvg)")
        restored = load_rgba(tmp_png)
        # Stamp dropped medial-axis ink: full skeleton for thin wordmarks,
        # elongated thin-rule components only for dense lockups (PROPAK).
        if thin:
            # Keep stroke bounds / sharp terminators the spline may have rounded.
            a = restored[:, :, 3]
            restored[:, :, 3] = np.where(
                a >= 64, 255, np.where(a < 24, 0, a)
            ).astype(np.uint8)
            restored = stamp_centerline(restored, prepared)
        elif elongated_thin_components(prepared) is not None:
            restored = stamp_centerline(restored, prepared)
        # Palette lock + plate cleanup; reject collapsed redraws → Lanczos prep.
        # Thin Arc wordmarks: require ink geometry vs prepared source (vectorize
        # can mush strokes while still locking washed fills).
        max_drift = 0.14 if thin else 0.35
        min_iou = 0.55 if thin else None
        # Only ever hand back the traced SVG when vtracer's own output is the
        # thing we actually shipped — never the Lanczos fallback, which has
        # no vector path behind it at all.
        vtrace_accepted = False
        try:
            finished = finalize_restore(
                restored,
                prepared,
                min_palette=0.14 if thin else 0.18,
                max_aspect_drift=max_drift,
                min_ink_iou=min_iou,
            )
            # Oversmooth: vectorize can lock fills (high IoU vs prep) while
            # killing edge energy. Apply to thin + non-thin — GCM is thin and
            # was skipping the old non-thin-only gate. Do NOT reject low-IoU
            # soft redraws (gcm__downscale vectorize beats Lanczos on clean IoU).
            h0, w0 = prepared.shape[:2]
            small = np.asarray(
                Image.fromarray(finished, "RGBA").resize(
                    (w0, h0), Image.Resampling.LANCZOS
                )
            )
            ve = edge_energy(small)
            pe = edge_energy(prepared)
            iou_v = ink_mask_iou(prepared, finished)
            if pe > 1e-6 and ve < 0.82 * pe and iou_v >= 0.85:
                raise RuntimeError(
                    f"oversmooth (edge_ratio={ve / pe:.3f}, iou={iou_v:.3f})"
                )
            vtrace_accepted = True
        except RuntimeError as e:
            print(f"vectorize fidelity reject ({e}); Lanczos fallback", file=sys.stderr)
            finished = finalize_restore(
                _lanczos_to_height(prepared, min_height),
                prepared,
                min_palette=0.05,
            )
            # Active mind: Gemini+Claude diagnose when the local vector path
            # could not ship a vector. Fail-open; never block Lanczos.
            try:
                from tools.logo_vectorizer.ai_advisors.collab_mind import (
                    escalate_if_stuck,
                )
                from tools.logo_vectorizer.env_loader import load_env

                load_env()
                guidance = escalate_if_stuck(
                    Image.fromarray(prepared, "RGBA"),
                    case_id="",
                    journal=True,
                    fell_to_lanczos=True,
                    score_total=0.0,
                    passes_gates=False,
                    extra_context={"reject": str(e), "path": "vectorize"},
                )
                if guidance is not None and guidance.takeover:
                    rescued = _collab_rescue(
                        prepared,
                        dest,
                        min_height=min_height,
                        svg_out=svg_out,
                        guidance=guidance,
                    )
                    if rescued is not None:
                        return rescued
            except Exception as collab_exc:  # noqa: BLE001
                print(f"collab_mind skipped ({collab_exc})", file=sys.stderr)
        save_rgba(dest, finished)
        if vtrace_accepted and svg_out is not None:
            svg_out.write_bytes(svg.read_bytes())
    return dest


def _collab_rescue(
    prepared: np.ndarray,
    dest: Path,
    *,
    min_height: int,
    svg_out: Path | None,
    guidance,
) -> Path | None:
    """Apply collab_mind preferred_path when vectorize fell to Lanczos."""
    path = (guidance.preferred_path or "").strip().lower()
    if path == "recreate":
        try:
            from tools.logo_vectorizer.customer_recreate import recreate_customer_logo

            with tempfile.TemporaryDirectory(prefix="swift_collab_") as td:
                td_path = Path(td)
                src_png = td_path / "in.png"
                save_rgba(src_png, prepared)
                out_svg = td_path / "out.svg"
                out_png = td_path / "out.png"
                recreate_customer_logo(
                    src_png,
                    output_svg=out_svg,
                    output_png=out_png,
                    render_width=max(min_height, 2000),
                    render_background="transparent",
                    use_ai=True,
                    ai_providers=["gemini", "claude"],
                )
                if out_png.is_file():
                    finished = finalize_restore(
                        load_rgba(out_png),
                        prepared,
                        min_palette=0.05,
                    )
                    save_rgba(dest, finished)
                    if svg_out is not None and out_svg.is_file():
                        svg_out.write_bytes(out_svg.read_bytes())
                    print(
                        f"collab_mind rescue: recreate shipped ({guidance.diagnosis[:120]})",
                        file=sys.stderr,
                    )
                    return dest
        except Exception as exc:  # noqa: BLE001
            print(f"collab_mind recreate rescue failed ({exc})", file=sys.stderr)
            return None
    if path in ("retry_ensemble", "ensemble"):
        try:
            from tools.logo_vectorizer.ai_advisors import AIConfig
            from tools.logo_vectorizer.ensemble import vectorize_ensemble
            from tools.logo_vectorizer.sectional import rasterize_svg

            with tempfile.TemporaryDirectory(prefix="swift_collab_ens_") as td:
                td_path = Path(td)
                out_svg = td_path / "out.svg"
                result = vectorize_ensemble(
                    Image.fromarray(prepared, "RGBA"),
                    fill_hex="#FFFFFF",
                    use_cache=False,
                    is_orange=False,
                    ai=AIConfig(enabled=True, providers=["gemini", "claude"]),
                    case_id="collab_rescue",
                )
                out_svg.write_text(result.svg, encoding="utf-8")
                out_png = td_path / "out.png"
                try:
                    rasterize_svg(out_svg, out_png, width=max(min_height, 2000))
                except Exception:
                    return None
                if not out_png.is_file():
                    return None
                finished = finalize_restore(
                    load_rgba(out_png),
                    prepared,
                    min_palette=0.05,
                )
                save_rgba(dest, finished)
                if svg_out is not None:
                    svg_out.write_bytes(out_svg.read_bytes())
                print(
                    f"collab_mind rescue: ensemble/{result.method}",
                    file=sys.stderr,
                )
                return dest
        except Exception as exc:  # noqa: BLE001
            print(f"collab_mind ensemble rescue failed ({exc})", file=sys.stderr)
            return None
    # idealize / sectional / keep — leave Lanczos; idealize is owned by convert()
    print(
        f"collab_mind noted path={path} (no inline rescue; keep Lanczos)",
        file=sys.stderr,
    )
    return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Vectorize logo → print PNG")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--min-height", type=int, default=3000)
    p.add_argument(
        "--polish",
        action="store_true",
        help=(
            "Run hierarchical raster polish (Gigapixel/Upscayl/Remacri/"
            "UltraSharp/Real-ESRGAN/…) before vectorize. Also via LOGO_PRE_POLISH=1."
        ),
    )
    p.add_argument(
        "--idealize",
        action="store_true",
        help=(
            "Reconstruct named geometry (rects, circles, real glyph outlines) "
            "instead of tracing the boundary. Also via LOGO_IDEALIZE=1."
        ),
    )
    p.add_argument(
        "--svg-out",
        type=str,
        default=None,
        help="Also write the accepted vtracer SVG here (skipped on Lanczos fallback).",
    )
    args = p.parse_args(argv)
    try:
        out = convert(
            Path(args.input),
            Path(args.output),
            args.min_height,
            Path(args.svg_out) if args.svg_out else None,
            polish=bool(args.polish),
            idealize=bool(args.idealize),
        )
        print(out)
        return 0
    except Exception as e:
        print(f"vectorize failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
