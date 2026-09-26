"""Predictive-edge rebuild for graphic / text logos.

RealESRGAN keeps JPEG stair-steps. This classifies fill vs outline vs plate,
estimates tilt, optionally matches a system font + OCR text, then traces
smooth masks at print size (interpolate binary → blur → re-threshold).
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

WINDOWS_FONTS = Path(r"C:\Windows\Fonts")
FONT_FILES = [
    "comicbd.ttf",
    "comic.ttf",
    "Inkfree.ttf",
    "segoeprb.ttf",
    "segoepr.ttf",
    "segoescb.ttf",
    "segoesc.ttf",
    "BRADHITC.TTF",
    "ITCEDSCR.TTF",
    "Gabriola.ttf",
    "arialbd.ttf",
    "arial.ttf",
    "impact.ttf",
    "georgia.ttf",
    "georgiab.ttf",
    "tahoma.ttf",
    "tahomabd.ttf",
    "verdanab.ttf",
    "verdana.ttf",
]


def _is_plate(bgr: np.ndarray, alpha: np.ndarray | None, y: int, x: int) -> bool:
    if alpha is not None and int(alpha[y, x]) < 40:
        return True
    if alpha is not None:
        return False
    b, g, r = (int(v) for v in bgr[y, x])
    if r + g + b >= 720 and max(r, g, b) - min(r, g, b) < 36:
        return True
    lum = (r + g + b) / 3.0
    sat = max(r, g, b) - min(r, g, b)
    if lum <= 40 and sat <= 16:
        return True
    return False


def estimate_tilt_deg(mask: np.ndarray) -> float:
    ys, xs = np.where(mask > 0)
    if len(xs) < 30:
        return 0.0
    pts = np.column_stack([xs.astype(np.float32), ys.astype(np.float32)])
    _rect = cv2.minAreaRect(pts)
    angle = float(_rect[2])
    w, h = _rect[1]
    if w < h:
        angle += 90.0
    while angle > 45:
        angle -= 90
    while angle < -45:
        angle += 90
    return angle


def _ink_layers(
    bgr: np.ndarray, alpha: np.ndarray | None, k: int = 4
) -> list[tuple[tuple[int, int, int], np.ndarray]]:
    h, w = bgr.shape[:2]
    ink = np.zeros((h, w), np.uint8)
    for y in range(h):
        for x in range(w):
            if not _is_plate(bgr, alpha, y, x):
                ink[y, x] = 255
    if int(ink.sum()) // 255 < 40:
        return []
    interior = cv2.erode(ink, np.ones((3, 3), np.uint8), iterations=1)
    if int(interior.sum()) // 255 < 40:
        interior = ink
    ys, xs = np.where(interior > 0)
    data = bgr[ys, xs].astype(np.float32)
    kk = int(min(k, max(2, len(data) // 80)))
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    _, labels, centers = cv2.kmeans(
        data, kk, None, criteria, 4, cv2.KMEANS_PP_CENTERS
    )
    centers = centers.astype(np.float32)
    keep = [True] * len(centers)
    for i in range(len(centers)):
        if not keep[i]:
            continue
        for j in range(i + 1, len(centers)):
            if not keep[j]:
                continue
            if np.linalg.norm(centers[i] - centers[j]) < 38:
                keep[j] = False
    live = [i for i, kflag in enumerate(keep) if kflag]
    iy, ix = np.where(ink > 0)
    pix = bgr[iy, ix].astype(np.float32)
    d2 = [np.sum((pix - centers[i]) ** 2, axis=1) for i in live]
    best = np.argmin(np.stack(d2, axis=0), axis=0)
    ink_n = int(ink.sum()) // 255
    layers: list[tuple[tuple[int, int, int], np.ndarray]] = []
    for li, ci in enumerate(live):
        mask = np.zeros((h, w), np.uint8)
        sel = best == li
        mask[iy[sel], ix[sel]] = 255
        area = int(mask.sum()) // 255
        if area < 20:
            continue
        b, g, r = (int(round(v)) for v in centers[ci])
        sat = max(r, g, b) - min(r, g, b)
        lum = (r + g + b) / 3.0
        if sat < 28 and 80 < lum < 220:
            continue
        # Tiny muddy clusters are JPEG fringe, not a brand fill.
        if lum > 80 and area < ink_n * 0.04:
            continue
        layers.append(((b, g, r), mask))

    def darkness(item: tuple[tuple[int, int, int], np.ndarray]) -> int:
        b, g, r = item[0]
        return r + g + b

    layers.sort(key=darkness)
    return layers


def _smooth_mask(mask: np.ndarray, out_w: int, out_h: int) -> np.ndarray:
    """Print-size coverage: smooth contour, then a 2px anti-aliased rim.

    Cubic-upscale the source mask so 1-pixel stairs become a curve, threshold
    to a clean silhouette, then distance-transform a short AA ramp. A hard
    0/255 cut looked frayed at 1:1; a long blur looked fuzzy.
    """
    cleaned = mask
    if max(mask.shape) >= 12:
        opened = cv2.morphologyEx(
            mask,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
        )
        if int(opened.sum()) >= 20 * 255:
            cleaned = opened
    src = cleaned.astype(np.float32) / 255.0
    scaled = cv2.resize(src, (out_w, out_h), interpolation=cv2.INTER_CUBIC)
    binary = (scaled > 0.5).astype(np.uint8) * 255
    dist_in = cv2.distanceTransform(binary, cv2.DIST_L2, 5)
    dist_out = cv2.distanceTransform(255 - binary, cv2.DIST_L2, 5)
    aa_px = 2.2
    coverage = np.clip(0.5 + (dist_in - dist_out) / (2.0 * aa_px), 0.0, 1.0)
    return (coverage * 255.0).astype(np.uint8)


def _try_ocr(bgr: np.ndarray) -> str:
    try:
        import pytesseract  # type: ignore
    except Exception:
        return ""
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    try:
        text = pytesseract.image_to_string(rgb, config="--psm 6")
    except Exception:
        return ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)


def _load_font(name: str, size: int) -> ImageFont.FreeTypeFont | None:
    path = WINDOWS_FONTS / name
    if not path.is_file():
        return None
    try:
        return ImageFont.truetype(str(path), size=size)
    except Exception:
        return None


def _render_text_logo(
    text: str,
    fill: tuple[int, int, int],
    stroke: tuple[int, int, int],
    angle: float,
    font_file: str,
    out_h: int,
) -> tuple[np.ndarray, np.ndarray] | None:
    font = _load_font(font_file, max(48, out_h // 6))
    if font is None:
        return None
    lines = [ln for ln in text.split("\n") if ln]
    if not lines:
        return None
    tmp = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    dr = ImageDraw.Draw(tmp)
    widths = []
    heights = []
    for ln in lines:
        box = dr.textbbox((0, 0), ln, font=font, stroke_width=max(2, out_h // 180))
        widths.append(box[2] - box[0])
        heights.append(box[3] - box[1])
    pad = out_h // 12
    w = max(widths) + pad * 2
    h = sum(heights) + pad * 2 + 8 * (len(lines) - 1)
    im = Image.new("RGBA", (max(w, 8), max(h, 8)), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    y = pad
    stroke_w = max(2, out_h // 180)
    fill_rgb = (fill[2], fill[1], fill[0], 255)
    stroke_rgb = (stroke[2], stroke[1], stroke[0], 255)
    for i, ln in enumerate(lines):
        x = pad + (max(widths) - widths[i]) // 2
        dr.text(
            (x, y),
            ln,
            font=font,
            fill=fill_rgb,
            stroke_width=stroke_w,
            stroke_fill=stroke_rgb,
        )
        y += heights[i] + 8
    if abs(angle) > 1.5:
        im = im.rotate(-angle, resample=Image.Resampling.BICUBIC, expand=True)
    arr = np.array(im)
    bgr = cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2BGR)
    return bgr, arr[:, :, 3]


def _mask_from_bgr(bgr: np.ndarray, alpha: np.ndarray | None) -> np.ndarray:
    h, w = bgr.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    for y in range(h):
        for x in range(w):
            if not _is_plate(bgr, alpha, y, x):
                mask[y, x] = 255
    return mask


def try_font_recreate(
    bgr: np.ndarray,
    alpha: np.ndarray | None,
    layers: list[tuple[tuple[int, int, int], np.ndarray]],
    out_h: int,
) -> tuple[np.ndarray, np.ndarray, dict] | None:
    text = _try_ocr(bgr)
    if len(text.replace("\n", "").strip()) < 3:
        return None
    ink = _mask_from_bgr(bgr, alpha)
    angle = estimate_tilt_deg(ink)
    fill = layers[-1][0] if layers else (40, 40, 200)
    stroke = layers[0][0] if layers else (0, 0, 0)
    src_gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    src_small = cv2.resize(src_gray, (128, 128))
    best = None
    best_score = 1e18
    best_font = ""
    for font_file in FONT_FILES:
        rendered = _render_text_logo(text, fill, stroke, angle, font_file, 512)
        if rendered is None:
            continue
        rb, ra = rendered
        rg = cv2.cvtColor(rb, cv2.COLOR_BGR2GRAY)
        rg = cv2.resize(rg, (128, 128))
        score = float(np.mean((src_small.astype(np.float32) - rg.astype(np.float32)) ** 2))
        if score < best_score:
            best_score = score
            best = rendered
            best_font = font_file
    if best is None or best_score > 9000:
        return None
    rb, ra = best
    scale = out_h / max(1, rb.shape[0])
    out_w = max(1, int(round(rb.shape[1] * scale)))
    rb = cv2.resize(rb, (out_w, out_h), interpolation=cv2.INTER_CUBIC)
    ra = cv2.resize(ra, (out_w, out_h), interpolation=cv2.INTER_CUBIC)
    meta = {
        "method": "font-recreate",
        "text": text,
        "angle_deg": round(angle, 2),
        "font_file": best_font,
        "match_mse": round(best_score, 1),
    }
    return rb, ra, meta


def predictive_trace_rebuild(
    bgr: np.ndarray,
    alpha: np.ndarray | None,
    min_height: int,
) -> tuple[np.ndarray, np.ndarray, dict] | None:
    layers = _ink_layers(bgr, alpha)
    if not layers:
        return None
    ink = _mask_from_bgr(bgr, alpha)
    ys, xs = np.where(ink > 0)
    if len(xs) < 20:
        return None
    pad = 4
    x0, x1 = max(0, int(xs.min()) - pad), min(bgr.shape[1], int(xs.max()) + pad + 1)
    y0, y1 = max(0, int(ys.min()) - pad), min(bgr.shape[0], int(ys.max()) + pad + 1)
    crop_bgr = bgr[y0:y1, x0:x1]
    crop_a = None if alpha is None else alpha[y0:y1, x0:x1]
    crop_layers = [(c, m[y0:y1, x0:x1]) for c, m in layers]
    ch, cw = crop_bgr.shape[:2]
    scale = min_height / max(1, ch)
    out_h = min_height
    out_w = max(1, int(round(cw * scale)))

    fonted = try_font_recreate(crop_bgr, crop_a, crop_layers, out_h)
    if fonted is not None:
        return fonted

    canvas = np.zeros((out_h, out_w, 3), np.uint8)
    out_alpha = np.zeros((out_h, out_w), np.uint8)
    for color, mask in crop_layers:
        src_area = max(1, int((mask > 0).sum()))
        sm = _smooth_mask(mask, out_w, out_h)
        expected = src_area * (out_w / max(1, cw)) * (out_h / max(1, ch))
        if int((sm > 32).sum()) > expected * 2.8:
            continue
        stronger = sm > out_alpha
        canvas[stronger] = np.array(color, dtype=np.uint8)
        out_alpha = np.maximum(out_alpha, sm)
    meta = {
        "method": "predictive-trace",
        "angle_deg": round(estimate_tilt_deg(ink), 2),
        "layers": len(crop_layers),
        "size": [out_w, out_h],
    }
    return canvas, out_alpha, meta


def detect_letter_outline(
    bgr: np.ndarray, alpha: np.ndarray | None = None
) -> tuple[tuple[int, int, int], float] | None:
    """Return ((B,G,R), width_frac) when a dark stroke hugs chromatic fills."""
    h, w = bgr.shape[:2]
    if h < 8 or w < 8:
        return None
    fill_n = 0
    ring_n = 0
    acc = np.zeros(3, np.float64)

    def is_bg(y: int, x: int) -> bool:
        if alpha is not None and int(alpha[y, x]) < 40:
            return True
        b, g, r = (int(v) for v in bgr[y, x])
        return r + g + b >= 720 and max(r, g, b) - min(r, g, b) < 36

    def is_fill(y: int, x: int) -> bool:
        if is_bg(y, x):
            return False
        b, g, r = (int(v) for v in bgr[y, x])
        lum = (r + g + b) / 3.0
        sat = max(r, g, b) - min(r, g, b)
        return sat > 40 and lum > 55

    def is_dark(y: int, x: int) -> bool:
        if is_bg(y, x):
            return False
        b, g, r = (int(v) for v in bgr[y, x])
        lum = (r + g + b) / 3.0
        sat = max(r, g, b) - min(r, g, b)
        return lum < 70 and sat < 55

    for y in range(1, h - 1):
        for x in range(1, w - 1):
            if is_fill(y, x):
                fill_n += 1
            if not is_dark(y, x):
                continue
            nxt_fill = nxt_bg = False
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    ny, nx = y + dy, x + dx
                    if is_fill(ny, nx):
                        nxt_fill = True
                    if is_bg(ny, nx):
                        nxt_bg = True
            if nxt_fill:
                ring_n += 1
                acc += bgr[y, x]

    if ring_n < 16 or fill_n < 30 or ring_n < fill_n * 0.03:
        return None
    color = tuple(int(round(v)) for v in (acc / ring_n))
    if sum(color) / 3 < 90:
        color = (0, 0, 0)
    width_frac = float(np.clip(2.4 / min(w, h), 0.006, 0.03))
    return color, width_frac  # type: ignore[return-value]


def apply_letter_outline(
    bgr: np.ndarray,
    alpha: np.ndarray | None,
    color_bgr: tuple[int, int, int],
    width_frac: float,
) -> tuple[np.ndarray, np.ndarray | None]:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    fill = (hsv[:, :, 1] > 40) & (hsv[:, :, 2] > 55)
    if alpha is not None:
        fill &= alpha > 40
    radius = max(2, int(round(min(bgr.shape[:2]) * width_frac)))
    k = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1)
    )
    dil = cv2.dilate((fill.astype(np.uint8) * 255), k)
    ring = (dil > 0) & (~fill)
    out = bgr.copy()
    out[ring] = np.array(color_bgr, dtype=np.uint8)
    out_a = None if alpha is None else alpha.copy()
    if out_a is not None:
        out_a[ring] = 255
    return out, out_a


def write_meta(path: str, meta: dict) -> None:
    Path(path).write_text(json.dumps(meta, indent=2), encoding="utf-8")
