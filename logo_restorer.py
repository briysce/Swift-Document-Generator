"""Restore low-resolution logos to a clean ultra-high-resolution PNG.

Pipeline:
  1. Predictive trace (smooth masks + optional font/tilt recreate)
  2. Else RealESRGAN 4x upscale
  3. Lanczos to min height if needed
  4. Flatten solid fills last

Dependencies:
  pip install torch torchvision realesrgan basicsr opencv-python-headless numpy Pillow
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

WEIGHTS_URL = (
    "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/"
    "RealESRGAN_x4plus.pth"
)
WEIGHTS_NAME = "RealESRGAN_x4plus.pth"


def _weights_path() -> Path:
    cache = Path(__file__).resolve().parent / ".cache" / "realesrgan"
    cache.mkdir(parents=True, exist_ok=True)
    return cache / WEIGHTS_NAME


def _ensure_weights(path: Path) -> Path:
    if path.is_file() and path.stat().st_size > 1_000_000:
        return path
    print(f"Downloading RealESRGAN weights to {path} ...", file=sys.stderr)
    tmp = path.with_suffix(".pth.part")
    urllib.request.urlretrieve(WEIGHTS_URL, tmp)
    tmp.replace(path)
    return path


def _load_bgr_alpha(input_path: str) -> tuple[np.ndarray, np.ndarray | None]:
    """Load image as BGR uint8 plus optional alpha uint8."""
    img = cv2.imdecode(np.fromfile(input_path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        pil = Image.open(input_path)
        pil = pil.convert("RGBA") if "A" in pil.getbands() else pil.convert("RGB")
        arr = np.array(pil)
        if arr.ndim == 2:
            bgr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
            return bgr, None
        if arr.shape[2] == 4:
            bgr = cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2BGR)
            return bgr, arr[:, :, 3]
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR), None

    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR), None
    if img.shape[2] == 4:
        return img[:, :, :3], img[:, :, 3]
    return img, None


def _save_png(output_path: str, bgr: np.ndarray, alpha: np.ndarray | None) -> None:
    if alpha is not None:
        if alpha.shape[:2] != bgr.shape[:2]:
            alpha = cv2.resize(
                alpha, (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_LANCZOS4
            )
        out = cv2.merge([bgr, alpha])
    else:
        out = bgr
    ext = os.path.splitext(output_path)[1].lower() or ".png"
    ok, buf = cv2.imencode(".png" if ext != ".png" else ext, out)
    if not ok:
        raise RuntimeError(f"Failed to encode PNG for {output_path}")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(buf.tobytes())


_UPSAMPLER_CACHE: dict[str, object] = {}


def _realesrgan_upsampler(tile: int):
    """Cached RealESRGANer — reloading weights every pass was a major timeout source."""
    import sys

    import torch
    import torchvision.transforms.functional as tvF

    # basicsr still imports the removed torchvision.transforms.functional_tensor.
    sys.modules.setdefault("torchvision.transforms.functional_tensor", tvF)

    from basicsr.archs.rrdbnet_arch import RRDBNet
    from realesrgan import RealESRGANer

    device = "cuda" if torch.cuda.is_available() else "cpu"
    key = f"{device}:tile{int(tile)}"
    cached = _UPSAMPLER_CACHE.get(key)
    if cached is not None:
        return cached

    weights = _ensure_weights(_weights_path())
    model = RRDBNet(
        num_in_ch=3,
        num_out_ch=3,
        num_feat=64,
        num_block=23,
        num_grow_ch=32,
        scale=4,
    )
    upsampler = RealESRGANer(
        scale=4,
        model_path=str(weights),
        model=model,
        tile=int(tile),
        tile_pad=10,
        pre_pad=0,
        half=device == "cuda",
        device=device,
    )
    _UPSAMPLER_CACHE[key] = upsampler
    return upsampler


def _adaptive_tile(h: int, w: int) -> int:
    """Tile large intermediates on CPU; tiny inputs stay whole-frame."""
    import torch

    pixels = int(h) * int(w)
    if torch.cuda.is_available():
        return 0 if pixels < 1_500_000 else 400
    # CPU: whole-frame on huge tensors hangs (trialta plate_halo 600s timeout).
    if pixels < 80_000:
        return 0
    if pixels < 250_000:
        return 256
    return 192


def _realesrgan_upscale_4x(bgr: np.ndarray) -> np.ndarray:
    """4x RealESRGAN upscale. Input/output BGR uint8."""
    h, w = bgr.shape[:2]
    tile = _adaptive_tile(h, w)
    upsampler = _realesrgan_upsampler(tile)
    output, _ = upsampler.enhance(bgr, outscale=4)
    return output


def _lanczos_rgba(arr: np.ndarray, min_height: int) -> np.ndarray:
    h0 = arr.shape[0]
    if h0 >= min_height:
        return arr
    scale = min_height / float(h0)
    nw = max(1, int(round(arr.shape[1] * scale)))
    return np.asarray(
        Image.fromarray(arr, "RGBA").resize((nw, min_height), Image.Resampling.LANCZOS)
    )


def _pre_upscale_tiny_bgr(
    bgr: np.ndarray,
    alpha: np.ndarray | None,
    target_h: int = 128,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Lanczos-lift sub-64px marks before ESRGAN (SR on 21px Trialta invents mush)."""
    h = bgr.shape[0]
    if h >= target_h:
        return bgr, alpha
    scale = target_h / float(h)
    nw = max(1, int(round(bgr.shape[1] * scale)))
    bgr = cv2.resize(bgr, (nw, target_h), interpolation=cv2.INTER_LANCZOS4)
    if alpha is not None:
        alpha = cv2.resize(alpha, (nw, target_h), interpolation=cv2.INTER_LANCZOS4)
    return bgr, alpha


def _ensure_min_height(
    bgr: np.ndarray,
    alpha: np.ndarray | None,
    min_height: int,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Scale so height is at least [min_height]; width follows aspect ratio."""
    h, w = bgr.shape[:2]
    if h >= min_height:
        return bgr, alpha
    scale = min_height / float(h)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    bgr = cv2.resize(bgr, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
    if alpha is not None:
        alpha = cv2.resize(alpha, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
    return bgr, alpha


def _flatten_solid_areas(bgr: np.ndarray) -> np.ndarray:
    """Collapse near-uniform fills while keeping edges."""
    # Bilateral keeps edges; a light mean-shift-style blur flattens poster noise.
    smoothed = cv2.bilateralFilter(bgr, d=9, sigmaColor=70, sigmaSpace=50)
    gray = cv2.cvtColor(smoothed, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 40, 120)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    edge_mask = edges > 0

    # Cluster every near-uniform fill (any hue) down to a few solid colors.
    data = smoothed.reshape((-1, 3)).astype(np.float32)
    k = 8
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, labels, centers = cv2.kmeans(
        data, k, None, criteria, 3, cv2.KMEANS_PP_CENTERS
    )
    quantized = centers[labels.flatten()].reshape(smoothed.shape).astype(np.uint8)
    return quantized


def _clean_edge_noise(bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    # Remove speckles along edges without rounding geometry.
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    cleaned_edges = cv2.morphologyEx(edges, cv2.MORPH_OPEN, kernel, iterations=1)
    denoise = cv2.fastNlMeansDenoisingColored(bgr, None, 3, 3, 7, 21)
    mask = cleaned_edges > 0
    out = bgr.copy()
    # Blend denoise into non-edge regions; keep original edge pixels sharper.
    non_edge = ~mask
    out[non_edge] = denoise[non_edge]
    return out


def _unsharp_mask(bgr: np.ndarray, amount: float = 1.15, radius: int = 3) -> np.ndarray:
    blur = cv2.GaussianBlur(bgr, (0, 0), sigmaX=radius, sigmaY=radius)
    sharp = cv2.addWeighted(bgr, 1.0 + amount, blur, -amount, 0)
    return np.clip(sharp, 0, 255).astype(np.uint8)


def _opencv_cleanup(bgr: np.ndarray) -> np.ndarray:
    cleaned = _clean_edge_noise(bgr)
    return _unsharp_mask(cleaned)


def _rgba_from_bgr_alpha(bgr: np.ndarray, alpha: np.ndarray | None) -> np.ndarray:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    if alpha is None:
        alpha = np.full(bgr.shape[:2], 255, dtype=np.uint8)
    elif alpha.shape[:2] != bgr.shape[:2]:
        alpha = cv2.resize(
            alpha, (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_LANCZOS4
        )
    return np.dstack([rgb, alpha]).astype(np.uint8)


def _bgr_alpha_from_rgba(arr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    bgr = cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2BGR)
    return bgr, arr[:, :, 3]


def restore_logo(
    input_path: str,
    output_path: str,
    min_dimension: int = 3000,
) -> str:
    """Restore a low-res logo and write a PNG at least [min_dimension] px tall.

    Width is scaled with the same ratio (square → ~3000×3000, wide → 3000×wider).
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input image not found: {input_path}")
    if min_dimension < 1:
        raise ValueError("min_dimension must be >= 1")

    # Plate knockout + crop before SR so white/black mattes are not upscaled.
    sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
    from logo_raster_finish import (
        finalize_restore,
        is_thin_wordmark,
        load_rgba,
        prepare_for_engine,
    )

    source_rgba = load_rgba(input_path)
    prepared = prepare_for_engine(source_rgba)
    bgr, alpha = _bgr_alpha_from_rgba(prepared)
    src_h = int(bgr.shape[0])

    # Honest floor: RealESRGAN on ~20–32px phone crops invents warped mush
    # (trialta__import_combo / downscale_jpeg). Lanczos+palette lock wins there.
    # Keep ESRGAN at ~40px+ (trialta__plate_halo scored 0.749 with 1-pass SR).
    if src_h <= 32:
        print(
            f"tiny-mark skip ESRGAN ({src_h}px <= 32); Lanczos restore",
            file=sys.stderr,
        )
        rgba = finalize_restore(
            _lanczos_rgba(prepared, min_dimension), prepared, min_palette=0.05
        )
        _save_png(output_path, *_bgr_alpha_from_rgba(rgba))
        return output_path

    try:
        from logo_trace import predictive_trace_rebuild, write_meta

        traced = predictive_trace_rebuild(bgr, alpha, min_dimension)
        if traced is not None:
            tbgr, talpha, meta = traced
            tbgr = _flatten_solid_areas(tbgr)
            rgba = _rgba_from_bgr_alpha(tbgr, talpha)
            try:
                # Same ink-IoU honesty gate as ESRGAN — Arc plate_halo trace was
                # accepting stroke collapse with inflated chroma scores.
                thin = is_thin_wordmark(prepared)
                max_drift = 0.22 if src_h < 64 else 0.35
                min_iou = 0.58 if thin else 0.38
                rgba = finalize_restore(
                    rgba,
                    prepared,
                    min_palette=0.15,
                    max_aspect_drift=max_drift,
                    min_ink_iou=min_iou,
                )
            except RuntimeError as e:
                print(f"trace rebuild rejected ({e}); falling back", file=sys.stderr)
                raise RuntimeError("trace_rejected") from e
            _save_png(output_path, *_bgr_alpha_from_rgba(rgba))
            write_meta(str(Path(output_path).with_suffix(".json")), meta)
            print(f"trace rebuild: {meta}", file=sys.stderr)
            return output_path
    except Exception as e:
        print(f"trace rebuild skipped ({e}); falling back to RealESRGAN", file=sys.stderr)

    # Tiny / warped phone crops: Lanczos-lift before SR, cap passes.
    # Multi-pass whole-frame ESRGAN on ~40px Trialta plate_halo timed out at 600s.
    if src_h < 64:
        bgr, alpha = _pre_upscale_tiny_bgr(bgr, alpha, target_h=128)
        print(
            f"tiny-mark pre-upscale: {src_h}px -> {bgr.shape[0]}px before ESRGAN",
            file=sys.stderr,
        )
    max_passes = 1 if src_h < 48 else (2 if src_h < 96 else 3)

    # Super-resolve in 4x steps until we are close to target height, then
    # Lanczos to exact min height. A single 4x on a 100px-tall logo only
    # reaches 400px — that looked like "sharper" without a real size jump.
    esrgan_passes = 0
    while bgr.shape[0] < min_dimension and esrgan_passes < max_passes:
        before_h = bgr.shape[0]
        # Stop before a CPU pass that would explode memory/time; Lanczos rest.
        if before_h >= 512 and before_h * 4 > min_dimension * 1.25:
            break
        try:
            bgr = _realesrgan_upscale_4x(bgr)
            print(
                f"RealESRGAN pass {esrgan_passes + 1}: {before_h} -> {bgr.shape[0]}px",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"RealESRGAN unavailable ({e}); continuing without it", file=sys.stderr)
            break
        if alpha is not None:
            h, w = bgr.shape[:2]
            alpha = cv2.resize(alpha, (w, h), interpolation=cv2.INTER_LANCZOS4)
        esrgan_passes += 1
        if bgr.shape[0] <= before_h:
            break

    bgr = _opencv_cleanup(bgr)
    bgr, alpha = _ensure_min_height(bgr, alpha, min_dimension)
    # Flatten last so denoise / unsharp / Lanczos cannot reintroduce splotch.
    # Skip aggressive k-means on tiny-origin marks — it gray-washes Trialta/Arc.
    if src_h >= 48:
        bgr = _flatten_solid_areas(bgr)
    rgba = _rgba_from_bgr_alpha(bgr, alpha)
    try:
        # Tighter aspect gate after SR — Trialta import_combo warped under 0.35.
        # Ink-IoU gate: Arc thin wordmarks can get high palette scores from SR
        # hallucination while strokes collapse (plate_halo iou≈0.23 vs prep).
        thin = is_thin_wordmark(prepared)
        max_drift = 0.22 if src_h < 64 else 0.35
        min_iou = 0.58 if thin else 0.38
        rgba = finalize_restore(
            rgba,
            prepared,
            min_palette=0.15,
            max_aspect_drift=max_drift,
            min_ink_iou=min_iou,
        )
    except RuntimeError as e:
        # Palette collapse / plate mush / ink collapse — fall back to Lanczos.
        print(f"ESRGAN finish rejected ({e}); Lanczos fallback", file=sys.stderr)
        rgba = finalize_restore(
            _lanczos_rgba(prepared, min_dimension), prepared, min_palette=0.05
        )
    _save_png(output_path, *_bgr_alpha_from_rgba(rgba))
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Restore a low-resolution logo to a 3000px+ PNG via RealESRGAN."
    )
    parser.add_argument("input", help="Path to the source logo image")
    parser.add_argument(
        "output",
        nargs="?",
        help="Output PNG path (default: <input>_restored.png)",
    )
    parser.add_argument(
        "--min-dimension",
        type=int,
        default=3000,
        help="Minimum output height in pixels; width follows aspect (default: 3000)",
    )
    args = parser.parse_args(argv)
    output = args.output
    if not output:
        stem = Path(args.input).stem
        output = str(Path(args.input).with_name(f"{stem}_restored.png"))
    path = restore_logo(args.input, output, min_dimension=args.min_dimension)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
