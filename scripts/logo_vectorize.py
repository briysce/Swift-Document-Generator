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
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

# Shared plate / palette finish (same as ESRGAN path).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from logo_raster_finish import (  # noqa: E402
    edge_energy,
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
        if thin:
            # Keep stroke bounds / sharp terminators the spline may have rounded.
            a = restored[:, :, 3]
            restored[:, :, 3] = np.where(
                a >= 64, 255, np.where(a < 24, 0, a)
            ).astype(np.uint8)
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
        save_rgba(dest, finished)
        if vtrace_accepted and svg_out is not None:
            svg_out.write_bytes(svg.read_bytes())
    return dest


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
        )
        print(out)
        return 0
    except Exception as e:
        print(f"vectorize failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
