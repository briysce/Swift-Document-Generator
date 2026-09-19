"""Shared plate knockout, halo strip, and palette lock for logo engines.

Used by vectorize / ESRGAN / cubic baseline so synthetic scoring and the app
agree on matte cleanup. Score-gated: keep thresholds conservative for Swift.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image


def load_rgba(path: Path | str) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGBA"), dtype=np.uint8).copy()


def save_rgba(path: Path | str, arr: np.ndarray) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr, "RGBA").save(path)


def _corner_plate_mode(arr: np.ndarray) -> str:
    """Detect outer plate hue from corners: 'white', 'black', or 'none'."""
    h, w = arr.shape[:2]
    coords = [
        (0, 0),
        (0, w - 1),
        (h - 1, 0),
        (h - 1, w - 1),
        (0, w // 2),
        (h - 1, w // 2),
        (h // 2, 0),
        (h // 2, w - 1),
    ]
    white = black = opaque = 0
    for y, x in coords:
        r, g, b, a = [int(v) for v in arr[y, x]]
        if a < 40:
            continue
        opaque += 1
        lum = (r + g + b) / 3.0
        sat = max(r, g, b) - min(r, g, b)
        if lum >= 230 and sat <= 22:
            white += 1
        elif _is_near_black_canvas(r, g, b):
            black += 1
    if opaque == 0:
        return "none"
    if white >= max(2, opaque // 2):
        return "white"
    if black >= max(2, opaque // 2):
        return "black"
    return "none"


def _plate_mask(arr: np.ndarray) -> np.ndarray:
    """Near-white / near-black low-chroma canvas (JPEG-tolerant).

    Only punches the plate mode present at the corners so Swift black strokes
    that touch a cropped AABB are not treated as a dark plate.
    """
    a = arr[:, :, 3]
    rgb = arr[:, :, :3].astype(np.int32)
    lum = rgb.mean(axis=2)
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    mode = _corner_plate_mode(arr)
    # Dart `_isEmptyPixel` light plate: lum >= 200 and sat <= 28.
    white = (a > 40) & (lum >= 200) & (sat <= 28)
    # Dart _isNearBlackCanvas: lum <= 40 and sat <= 16.
    black = (a > 40) & (lum <= 40) & (sat <= 16)
    if mode == "white":
        return white
    if mode == "black":
        return black
    # Transparent corners already — still clear obvious white JPEG leftovers.
    return white


def _dilate4(m: np.ndarray) -> np.ndarray:
    up = np.zeros_like(m)
    dn = np.zeros_like(m)
    lf = np.zeros_like(m)
    rt = np.zeros_like(m)
    up[1:, :] = m[:-1, :]
    dn[:-1, :] = m[1:, :]
    lf[:, 1:] = m[:, :-1]
    rt[:, :-1] = m[:, 1:]
    return m | up | dn | lf | rt


def _erode4(m: np.ndarray) -> np.ndarray:
    return ~_dilate4(~m)


def raw_contrast_mask(arr: np.ndarray, *, core: bool = False) -> np.ndarray:
    """Foreground ink vs plate — computed on the *raw* raster (pre-knockout).

    ``core=True`` excludes light JPEG fringe so the medial axis stays inside
    real strokes instead of walking the halo ring.
    """
    a = arr[:, :, 3]
    rgb = arr[:, :, :3].astype(np.int32)
    lum = rgb.mean(axis=2)
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    mode = _corner_plate_mode(arr)
    opaque = a >= 40
    if mode == "black":
        fg = opaque & ~((lum <= 28) & (sat <= 18))
    else:
        # White / unknown plate: anything with contrast against the canvas.
        fg = opaque & ~((lum >= 230) & (sat <= 22)) & ((lum < 220) | (sat > 18))
    if core:
        fg = fg & ((sat >= 18) | (lum <= 175))
        fg = fg & (lum <= 215)
        fg = fg & ~((lum >= 190) & (sat <= 28))
    return fg


def foreground_bbox_density(mask: np.ndarray) -> float:
    """Ink area / AABB area. Thin strokes sit well below solid fills."""
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return 1.0
    bw = int(xs.max() - xs.min()) + 1
    bh = int(ys.max() - ys.min()) + 1
    return float(mask.sum()) / float(max(1, bw * bh))


def is_thin_stroke_mark(arr: np.ndarray, mask: np.ndarray | None = None) -> bool:
    """Thin-stroke geometry: foreground / AABB density < 0.18."""
    fg = raw_contrast_mask(arr) if mask is None else mask
    n = int(fg.sum())
    if n < 80:
        return False
    # Arc ~0.09–0.15; Trialta/Propak/GCM fills sit ~0.28–0.45.
    return foreground_bbox_density(fg) < 0.18


def _skeletonize_cv2(mask: np.ndarray) -> np.ndarray:
    """Fast morphological skeleton via OpenCV erode/open (no ximgproc)."""
    import cv2

    img = (mask.astype(np.uint8) * 255)
    kernel = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    skel = np.zeros_like(img)
    while True:
        eroded = cv2.erode(img, kernel)
        leftover = cv2.subtract(img, cv2.dilate(eroded, kernel))
        skel = cv2.bitwise_or(skel, leftover)
        img = eroded
        if cv2.countNonZero(img) == 0:
            break
    return skel > 0


def _skeletonize_numpy(mask: np.ndarray) -> np.ndarray:
    """Morphological skeleton (Lantuéjoul) when OpenCV is absent."""
    img = mask.astype(bool)
    skel = np.zeros_like(img)
    while img.any():
        eroded = _erode4(img)
        leftover = img & ~_dilate4(eroded)
        skel |= leftover
        img = eroded
    return skel


def structural_centerline(mask: np.ndarray) -> np.ndarray:
    """Medial-axis / morphological skeleton of a binary contrast mask."""
    if int(mask.sum()) < 20:
        return np.zeros_like(mask, dtype=bool)
    try:
        import cv2

        src = (mask.astype(np.uint8) * 255)
        closed = cv2.morphologyEx(
            src, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
        )
        thinning = getattr(getattr(cv2, "ximgproc", None), "thinning", None)
        if thinning is not None:
            return thinning(closed) > 0
        return _skeletonize_cv2(closed > 0)
    except Exception:
        pass
    closed = _erode4(_dilate4(mask.astype(bool)))
    try:
        from skimage.morphology import skeletonize

        return np.asarray(skeletonize(closed), dtype=bool)
    except Exception:
        return _skeletonize_numpy(closed)


def centerline_protect_mask(arr: np.ndarray) -> np.ndarray | None:
    """Skeleton of the raw contrast mask — only when bbox density < 0.18."""
    contrast = raw_contrast_mask(arr)
    if not is_thin_stroke_mark(arr, contrast):
        return None
    core = raw_contrast_mask(arr, core=True)
    skel = structural_centerline(core if int(core.sum()) >= 40 else contrast)
    return skel if skel.any() else None


def knockout_border_plate(
    arr: np.ndarray, protect: np.ndarray | None = None
) -> np.ndarray:
    """Punch border-connected plate pixels to transparent.

    Hard rule: pixels on the structural centerline never receive alpha=0.
    """
    out = arr.copy()
    plate = _plate_mask(out)
    h, w = plate.shape
    q: deque[tuple[int, int]] = deque()
    seen = np.zeros((h, w), dtype=bool)
    for x in range(w):
        for y in (0, h - 1):
            if plate[y, x]:
                q.append((x, y))
                seen[y, x] = True
    for y in range(h):
        for x in (0, w - 1):
            if plate[y, x] and not seen[y, x]:
                q.append((x, y))
                seen[y, x] = True
    # Dart `_removeSolidBackground`: 8-way on light plates, 4-way on black
    # so dark brand fills that kiss the AABB are not eaten as canvas.
    eight = _corner_plate_mode(arr) != "black"
    neigh = (
        ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1))
        if eight
        else ((-1, 0), (1, 0), (0, -1), (0, 1))
    )
    while q:
        x, y = q.popleft()
        if protect is None or not protect[y, x]:
            out[y, x, 3] = 0
        for dx, dy in neigh:
            nx, ny = x + dx, y + dy
            if nx < 0 or ny < 0 or nx >= w or ny >= h:
                continue
            if seen[ny, nx] or not plate[ny, nx]:
                continue
            seen[ny, nx] = True
            q.append((nx, ny))
    return out


def has_meaningful_transparency(arr: np.ndarray) -> bool:
    """Dart `_hasMeaningfulTransparency`: ≥35% of the border is already clear."""
    h, w = arr.shape[:2]
    if h < 2 or w < 2:
        return False
    a = arr[:, :, 3]
    border = np.concatenate(
        [a[0, :], a[-1, :], a[1:-1, 0], a[1:-1, -1]]
    )
    return border.size > 0 and float((border < 12).mean()) >= 0.35


def strip_halo_fringe(
    arr: np.ndarray, protect: np.ndarray | None = None
) -> np.ndarray:
    """Punch light gray JPEG halo between chromatic ink and empty canvas.

    Thin wordmarks: require stronger fringe/chroma separation so washed brand
    strokes (sat ~25–45) are not eaten as halo.

    Hard rule: structural centerline pixels never receive alpha=0.
    """
    out = arr.copy()
    r = out[:, :, 0].astype(np.int16)
    g = out[:, :, 1].astype(np.int16)
    b = out[:, :, 2].astype(np.int16)
    a = out[:, :, 3]
    sat = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    lum = (r.astype(np.int32) + g.astype(np.int32) + b.astype(np.int32)) / 3.0
    thin = is_thin_wordmark(arr)
    chroma_floor = 28 if thin else 45
    fringe_sat = 28 if thin else 42
    fringe_lum = 110 if thin else 88
    chromatic = (a >= 80) & (sat > chroma_floor)
    empty = (a < 40) | ((lum <= 40) & (sat <= 16) & (a > 0))
    fringe = (a >= 40) & (sat < fringe_sat) & (lum > fringe_lum)

    near_ink = _dilate4(chromatic)
    near_empty = _dilate4(empty)
    punch = fringe & near_ink & near_empty
    if protect is not None:
        punch = punch & ~protect
    out[:, :, 3] = np.where(punch, 0, a)
    return out


def prune_ink_speckles(
    arr: np.ndarray,
    min_px: int = 18,
    min_frac: float = 0.012,
    protect: np.ndarray | None = None,
) -> np.ndarray:
    """Drop tiny disconnected ink islands left by JPEG plate / import noise."""
    try:
        from scipy import ndimage
    except ImportError:
        return arr
    out = arr.copy()
    ink = out[:, :, 3] >= 48
    n_ink = int(ink.sum())
    if n_ink < 80:
        return arr
    lab, n = ndimage.label(ink)
    if n <= 1:
        return arr
    sizes = np.bincount(lab.ravel())
    largest = int(sizes[1:].max())
    thr = max(min_px, int(largest * min_frac))
    drop = sizes[lab] < thr
    # Keep glyph-sized islands even when pixel count is low vs the main
    # cluster (Trialta/GCM JPEG can isolate a letter). Only drop crumbs
    # whose AABB is tiny relative to the full ink box.
    ys, xs = np.where(ink)
    bw = max(1, int(xs.max() - xs.min()) + 1)
    bh = max(1, int(ys.max() - ys.min()) + 1)
    keep_w, keep_h = max(6, int(bw * 0.08)), max(6, int(bh * 0.08))
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    for i in range(1, n + 1):
        if sizes[i] >= thr:
            continue
        iy, ix = np.where(lab == i)
        # Never drop a component that defines the current ink AABB — that
        # collapsed Trialta/GCM aspect when JPEG isolated an edge glyph.
        if (
            int(ix.min()) <= x0
            or int(ix.max()) >= x1
            or int(iy.min()) <= y0
            or int(iy.max()) >= y1
        ):
            drop[lab == i] = False
            continue
        if (int(ix.max() - ix.min()) + 1) >= keep_w or (
            int(iy.max() - iy.min()) + 1
        ) >= keep_h:
            drop[lab == i] = False
    if protect is not None:
        drop = drop & ~protect
    out[drop & ink, 3] = 0
    return out


def crop_to_ink(arr: np.ndarray, pad: int = 2, min_alpha: int = 48) -> np.ndarray:
    ink = arr[:, :, 3] >= min_alpha
    ys, xs = np.where(ink)
    if len(xs) == 0:
        return arr
    h, w = arr.shape[:2]
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(w - 1, x1 + pad), min(h - 1, y1 + pad)
    return arr[y0 : y1 + 1, x0 : x1 + 1].copy()


def is_thin_wordmark(arr: np.ndarray) -> bool:
    """Wide short lockups with sparse ink — Arc-style wordmarks (not Propak fills)."""
    h, w = arr.shape[:2]
    if h < 8 or w < 8:
        return False
    if w / float(h) < 2.4:
        return False
    a = arr[:, :, 3]
    rgb = arr[:, :, :3].astype(np.int32)
    lum = rgb.mean(axis=2)
    ink = (a >= 48) & (lum < 235)
    coverage = float(ink.mean()) if ink.size else 0.0
    # Propak/GCM fills often sit ~0.25–0.35; Arc cores are ~0.06–0.15.
    return 0.04 <= coverage <= 0.18


def _rgb_to_hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized RGB (0–255) → H in degrees [0, 360), S and V in [0, 1]."""
    x = rgb.astype(np.float32) / 255.0
    r, g, b = x[..., 0], x[..., 1], x[..., 2]
    mx = x.max(axis=-1)
    mn = x.min(axis=-1)
    df = mx - mn
    h = np.zeros_like(mx)
    m_r = (mx == r) & (df > 1e-8)
    m_g = (mx == g) & (df > 1e-8) & ~m_r
    m_b = (mx == b) & (df > 1e-8) & ~m_r & ~m_g
    h[m_r] = (60.0 * ((g[m_r] - b[m_r]) / df[m_r]) + 360.0) % 360.0
    h[m_g] = 60.0 * ((b[m_g] - r[m_g]) / df[m_g]) + 120.0
    h[m_b] = 60.0 * ((r[m_b] - g[m_b]) / df[m_b]) + 240.0
    s = np.where(mx > 1e-8, df / np.maximum(mx, 1e-8), 0.0)
    return h, s, mx


def _hsv_to_rgb(h: np.ndarray, s: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Vectorized HSV (H degrees, S/V 0–1) → RGB 0–255 float."""
    c = v * s
    hp = (h % 360.0) / 60.0
    x = c * (1.0 - np.abs(hp % 2.0 - 1.0))
    m = v - c
    zeros = np.zeros_like(c)
    rgb = np.zeros(c.shape + (3,), dtype=np.float32)
    i0 = (hp >= 0) & (hp < 1)
    i1 = (hp >= 1) & (hp < 2)
    i2 = (hp >= 2) & (hp < 3)
    i3 = (hp >= 3) & (hp < 4)
    i4 = (hp >= 4) & (hp < 5)
    i5 = (hp >= 5) | (hp < 0)
    for sl, ch in (
        (i0, (c, x, zeros)),
        (i1, (x, c, zeros)),
        (i2, (zeros, c, x)),
        (i3, (zeros, x, c)),
        (i4, (x, zeros, c)),
        (i5, (c, zeros, x)),
    ):
        rgb[sl, 0] = ch[0][sl]
        rgb[sl, 1] = ch[1][sl]
        rgb[sl, 2] = ch[2][sl]
    return np.clip((rgb + m[..., None]) * 255.0, 0, 255)


def _hue_delta(h: np.ndarray, anchor: float) -> np.ndarray:
    d = np.abs(h - anchor) % 360.0
    return np.minimum(d, 360.0 - d)


def _hue_consistent_chroma_recover(arr: np.ndarray) -> np.ndarray:
    """Lock washed JPEG-bleed edges onto the observed chromatic hue anchor.

    High-confidence opaque ink votes for a dominant hue. Boundary pixels whose
    HSV saturation collapsed to 8–25% are reprojected to that cluster's median
    S/V *only* when their hue is within ±25°. No unobserved brand colors.
    """
    out = arr.copy()
    rgb = out[:, :, :3].astype(np.float32)
    a = out[:, :, 3]
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    sat = mx - mn
    lum = rgb.mean(axis=2)
    plate = (lum >= 230) & (sat <= 22)
    black = (lum <= 28) & (sat <= 18)
    ink = (a >= 48) & ~plate & ~black
    if int(ink.sum()) < 80:
        return out

    h, s, v = _rgb_to_hsv(rgb)
    # Confident core: Saturation ≥ 35%, Alpha ≥ 200 (spec). No weaker fallback
    # — that invented anchors on multi-fill lockups.
    high = ink & (a >= 200) & (s >= 0.35) & (lum >= 35) & (lum <= 200)
    high = high & ~((lum > 175) & (s < 0.40))
    if int(high.sum()) < 12:
        return out

    rad = np.deg2rad(h[high])
    hue_anchor = float(
        np.rad2deg(np.arctan2(np.mean(np.sin(rad)), np.mean(np.cos(rad)))) % 360.0
    )
    cluster = high & (_hue_delta(h, hue_anchor) <= 25.0)
    if int(cluster.sum()) < 8:
        return out
    s_med = float(np.median(s[cluster]))
    v_med = float(np.median(v[cluster]))
    if s_med < 0.35:
        return out

    # JPEG 4:2:0 bleach ring: saturation collapsed into [8%, 35%).
    bg = ~ink
    near_bg = _dilate4(_dilate4(bg))
    boundary = ink & near_bg
    washed = boundary & (s >= 0.08) & (s < 0.35)
    match = washed & (_hue_delta(h, hue_anchor) <= 25.0)
    if not match.any():
        return out

    proj = _hsv_to_rgb(
        np.full(int(match.sum()), hue_anchor, dtype=np.float32),
        np.full(int(match.sum()), s_med, dtype=np.float32),
        np.full(int(match.sum()), v_med, dtype=np.float32),
    )
    rgb2 = rgb.copy()
    rgb2[match] = proj
    out[:, :, :3] = np.clip(rgb2, 0, 255).astype(np.uint8)
    # Recolor only — do not invent ink by raising alpha on bleed pixels.
    return out


def recover_residual_chroma(
    arr: np.ndarray,
    *,
    min_accent_sat: int = 36,
    min_accent_px: int = 24,
) -> np.ndarray:
    """Amplify washed same-hue ink toward strong residual accents.

    Honest: no-op when the source is fully gray / only near-white JPEG pink
    fringe remains. Does not invent brand colors from nothing. Hue-consistent
    projection runs first on thin strokes so JPEG chroma-subsample bleed can
    lock to the observed cluster without a new hue (multi-fill lockups skip
    this — a single hue anchor collapses Trialta-class marks).
    """
    out = arr.copy()
    if is_thin_stroke_mark(arr):
        out = _hue_consistent_chroma_recover(out)
    rgb = out[:, :, :3].astype(np.float32)
    a = out[:, :, 3]
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    sat = mx - mn
    lum = rgb.mean(axis=2)
    plate = (lum >= 230) & (sat <= 22)
    black = (lum <= 28) & (sat <= 18)
    ink = (a >= 48) & ~plate & ~black
    n_ink = int(ink.sum())
    if n_ink < 80:
        return out

    # Scale accent floor with crop size — Arc downscale is ~1.7k ink px; a fixed
    # 24px floor was fine, but tiny Trialta crops need a lower absolute gate.
    accent_px = min(min_accent_px, max(8, n_ink // 80))

    # Real residual chroma (mid-luma, sat above pink-fringe mush).
    strong = ink & (sat >= min_accent_sat) & (lum >= 35) & (lum <= 200)
    if int(strong.sum()) < accent_px:
        # One more chance at slightly washed brand fills (Arc red ~sat 35–40).
        strong = ink & (sat >= max(28, min_accent_sat - 8)) & (lum >= 35) & (lum <= 210)
        if int(strong.sum()) < accent_px:
            return out
    # Need accents to cover a meaningful share of ink — tiny JPEG speckles skip.
    if strong.sum() < max(accent_px, int(n_ink * 0.003)):
        return out

    pix = rgb[strong].astype(np.int32)
    q = pix // 24
    keys = q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]
    uniq, counts = np.unique(keys, return_counts=True)
    accents: list[tuple[float, np.ndarray, float]] = []
    for k, c in zip(uniq, counts):
        if int(c) < max(6, int(strong.sum() * 0.03)):
            continue
        ki = int(k)
        r = (ki // 1024) * 24 + 12
        g = ((ki // 32) % 32) * 24 + 12
        b = (ki % 32) * 24 + 12
        sat_c = max(r, g, b) - min(r, g, b)
        lum_c = (r + g + b) / 3.0
        if sat_c < max(28, min_accent_sat - 8) or lum_c > 205 or lum_c < 35:
            continue
        # Reject washed near-white pink JPEG fringe pretending to be brand.
        if lum_c > 175 and sat_c < 70:
            continue
        accents.append(
            (float(c) * sat_c, np.array([r, g, b], dtype=np.float32), float(sat_c))
        )
    if not accents:
        return out
    accents.sort(key=lambda t: -t[0])
    accents = accents[:3]

    work = ink & (lum > 40) & (lum < 230)
    if not work.any():
        return out
    wp = rgb[work]
    wl = lum[work]
    ws = sat[work]
    new = wp.copy()
    for _, accent, a_sat in accents:
        a_mid = float(accent.mean())
        a_ch = accent - a_mid
        want = min(185.0, a_sat * 1.45 + 24.0)
        boosted = np.clip(a_mid + a_ch * (want / max(1.0, a_sat)), 0, 255)
        p_ch = wp - wl[:, None]
        denom = (np.linalg.norm(p_ch, axis=1) + 1e-3) * (
            float(np.linalg.norm(a_ch)) + 1e-3
        )
        cos = (p_ch @ a_ch) / denom
        scale = a_mid / np.maximum(wl, 1.0)
        d = np.abs(wp * scale[:, None] - accent).sum(axis=1)
        match = ((cos > 0.42) & (ws >= 5)) | ((d < 65) & (ws >= 8))
        match &= (cos > 0.18) | (d < 55)
        idxs = np.where(match)[0]
        for i in idxs:
            strength = float(
                np.clip(0.52 + 0.42 * (1.0 - ws[i] / max(a_sat, 1.0)), 0.38, 0.90)
            )
            tgt = boosted.copy()
            tmid = float(tgt.mean())
            if tmid > 1:
                tgt = np.clip(tgt * (wl[i] / tmid), 0, 255)
            new[i] = (1.0 - strength) * wp[i] + strength * tgt

    rgb2 = rgb.copy()
    rgb2[work] = new
    out[:, :, :3] = np.clip(rgb2, 0, 255).astype(np.uint8)
    return out


def is_near_black_canvas(r: int, g: int, b: int) -> bool:
    """Dart `_isNearBlackCanvas`: low luma and low sat — not a dark brand fill."""
    sat = max(r, g, b) - min(r, g, b)
    lum = (r + g + b) / 3.0
    return lum <= 40 and sat <= 16


def _is_near_black_canvas(r: int, g: int, b: int) -> bool:
    return is_near_black_canvas(r, g, b)


def _chebyshev(r1: int, g1: int, b1: int, r2: int, g2: int, b2: int) -> int:
    return max(abs(r1 - r2), abs(g1 - g2), abs(b1 - b2))


def _estimate_plate_rgb(arr: np.ndarray) -> tuple[int, int, int] | None:
    """Dart `_estimateBackgroundColor`: 3×3 corner median when 75% agree."""
    h, w = arr.shape[:2]
    samples: list[tuple[int, int, int]] = []
    radius = min(3, min(h, w))
    for cx, cy in ((0, 0), (max(0, w - 3), 0), (0, max(0, h - 3)), (max(0, w - 3), max(0, h - 3))):
        for dy in range(radius):
            for dx in range(radius):
                x = min(w - 1, cx + dx)
                y = min(h - 1, cy + dy)
                r, g, b, a = [int(v) for v in arr[y, x]]
                if a < 12:
                    continue
                samples.append((r, g, b))
    if not samples:
        return None
    rs = sorted(s[0] for s in samples)
    gs = sorted(s[1] for s in samples)
    bs = sorted(s[2] for s in samples)
    mid = len(samples) // 2
    mr, mg, mb = rs[mid], gs[mid], bs[mid]
    agree = sum(1 for r, g, b in samples if _chebyshev(r, g, b, mr, mg, mb) <= 26)
    if agree / len(samples) < 0.75:
        return None
    return mr, mg, mb


def _plate_fill_mask(arr: np.ndarray, plate: tuple[int, int, int] | None) -> np.ndarray:
    """Dart `_isPlateFill` — only the estimated outer plate hue."""
    a = arr[:, :, 3]
    rgb = arr[:, :, :3].astype(np.int32)
    if plate is None:
        return np.zeros(a.shape, dtype=bool)
    d = np.maximum(
        np.maximum(np.abs(rgb[:, :, 0] - plate[0]), np.abs(rgb[:, :, 1] - plate[1])),
        np.abs(rgb[:, :, 2] - plate[2]),
    )
    if is_near_black_canvas(*plate):
        lum = rgb.mean(axis=2)
        sat = rgb.max(axis=2) - rgb.min(axis=2)
        return (a >= 12) & (lum <= 40) & (sat <= 16) & (d <= 14)
    return (a >= 12) & (d <= 26)


_PLATE_UNSET = object()
_HOLE_DIRS = (
    (1, 0),
    (-1, 0),
    (0, 1),
    (0, -1),
    (1, 1),
    (1, -1),
    (-1, 1),
    (-1, -1),
)


def _label_8conn(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """8-way connected components (Dart flood in `_punchEnclosedPlateHoles`)."""
    h, w = mask.shape
    lab = np.zeros((h, w), dtype=np.int32)
    n = 0
    ys, xs = np.where(mask)
    for y, x in zip(ys.tolist(), xs.tolist()):
        if lab[y, x] != 0:
            continue
        n += 1
        stack = [(int(x), int(y))]
        lab[y, x] = n
        while stack:
            cx, cy = stack.pop()
            for dx, dy in _HOLE_DIRS:
                nx, ny = cx + dx, cy + dy
                if nx < 0 or ny < 0 or nx >= w or ny >= h:
                    continue
                if not mask[ny, nx] or lab[ny, nx] != 0:
                    continue
                lab[ny, nx] = n
                stack.append((nx, ny))
    return lab, n


def punch_enclosed_plate_holes(
    arr: np.ndarray,
    protect: np.ndarray | None = None,
    plate: tuple[int, int, int] | None | object = _PLATE_UNSET,
) -> np.ndarray:
    """Dart `_punchEnclosedPlateHoles`: punch letter counters, not brand fills.

    Pass [plate] from the *pre-knockout* raster — after the outer flood the
    corners are transparent and a fresh estimate would be None (Dart keeps the
    original `_estimateBackgroundColor` result).
    """
    h, w = arr.shape[:2]
    if h < 8 or w < 8:
        return arr
    if plate is _PLATE_UNSET:
        plate = _estimate_plate_rgb(arr)
    fill = _plate_fill_mask(arr, plate)  # type: ignore[arg-type]
    a = arr[:, :, 3]
    ink = (a >= 80) & ~fill
    ink_count = int(ink.sum())
    if ink_count < 40:
        return arr

    lab, n = _label_8conn(fill)
    if n == 0:
        return arr
    out = arr.copy()
    for i in range(1, n + 1):
        ys, xs = np.where(lab == i)
        if ys.size == 0:
            continue
        if (
            int(xs.min()) == 0
            or int(ys.min()) == 0
            or int(xs.max()) == w - 1
            or int(ys.max()) == h - 1
        ):
            continue
        size = int(ys.size)
        if size < 8:
            continue
        if size > ink_count * 0.22:
            continue
        bw = int(xs.max() - xs.min()) + 1
        bh = int(ys.max() - ys.min()) + 1
        if bw <= 0 or bh <= 0:
            continue
        if bw > bh * 3.5 or bh > bw * 3.5:
            continue
        if size / float(bw * bh) < 0.32:
            continue
        # Dart: every component pixel votes its 8-neighbors (OOB = clear).
        ink_n = 0
        clear_n = 0
        for y, x in zip(ys.tolist(), xs.tolist()):
            for dx, dy in _HOLE_DIRS:
                nx, ny = int(x) + dx, int(y) + dy
                if nx < 0 or ny < 0 or nx >= w or ny >= h:
                    clear_n += 1
                    continue
                if int(a[ny, nx]) < 80:
                    clear_n += 1
                    continue
                if fill[ny, nx]:
                    continue
                ink_n += 1
        boundary = ink_n + clear_n
        if boundary == 0:
            continue
        if ink_n < boundary * 0.7 or ink_n <= clear_n:
            continue
        punch = lab == i
        if protect is not None:
            punch = punch & ~protect
        out[punch, 3] = 0
    return out


def punch_interior_plate(
    arr: np.ndarray,
    lum_thr: int = 220,
    sat_thr: int = 26,
    protect: np.ndarray | None = None,
    plate: tuple[int, int, int] | None | object = _PLATE_UNSET,
) -> np.ndarray:
    """Dart-parity wrapper — enclosed counters only, not a blanket white punch."""
    del lum_thr, sat_thr
    return punch_enclosed_plate_holes(arr, protect=protect, plate=plate)


def refine_gray_thin_ink(arr: np.ndarray) -> np.ndarray:
    """Drop mid-gray mush on gray thin wordmarks (Arc). Skip chromatic fills."""
    if not is_thin_wordmark(arr):
        return arr
    rgb = arr[:, :, :3].astype(np.int32)
    a = arr[:, :, 3]
    lum = rgb.mean(axis=2)
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    ink = a >= 48
    if not ink.any():
        return arr
    chroma_frac = float(((ink) & (sat >= 28)).sum() / max(1, int(ink.sum())))
    if chroma_frac >= 0.12:
        return arr  # Propak/GCM blues — do not punch fills
    out = arr.copy()
    mush = ink & (lum >= 192) & (sat <= 20)
    protect = centerline_protect_mask(arr)
    if protect is not None:
        mush = mush & ~protect
    out[mush, 3] = 0
    return out


def reinforce_thin_chromatic_strokes(arr: np.ndarray) -> np.ndarray:
    """1px dilate of chromatic thin strokes to reconnect JPEG-broken geometry.

    Copies nearest chromatic RGB into adjacent low-sat ink/empty fringe only.
    Does not invent hues — only spreads existing brand pixels.
    """
    if not is_thin_wordmark(arr):
        return arr
    out = arr.copy()
    rgb = out[:, :, :3].astype(np.int32)
    a = out[:, :, 3]
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    lum = rgb.mean(axis=2)
    chroma = (a >= 96) & (sat >= 22) & (lum >= 30) & (lum <= 210)
    if int(chroma.sum()) < 20:
        return arr
    h, w = a.shape
    add = np.zeros((h, w), dtype=bool)
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        shifted = np.zeros_like(chroma)
        if dy == 1:
            shifted[1:, :] = chroma[:-1, :]
        elif dy == -1:
            shifted[:-1, :] = chroma[1:, :]
        elif dx == 1:
            shifted[:, 1:] = chroma[:, :-1]
        else:
            shifted[:, :-1] = chroma[:, 1:]
        # Fill only soft fringe / near-empty next to chroma.
        fringe = shifted & (
            (a < 96) | ((sat <= 20) & (lum >= 80) & (lum <= 230) & (a >= 40))
        )
        add |= fringe
    if not add.any():
        return out
    # Paint from a 4-neigh chromatic donor.
    ys, xs = np.where(add)
    for y, x in zip(ys.tolist(), xs.tolist()):
        donor = None
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            ny, nx = y + dy, x + dx
            if ny < 0 or nx < 0 or ny >= h or nx >= w:
                continue
            if chroma[ny, nx]:
                donor = (ny, nx)
                break
        if donor is None:
            continue
        out[y, x, :3] = out[donor[0], donor[1], :3]
        out[y, x, 3] = 255
    return out


def quantize_thin_path(arr: np.ndarray, max_colors: int = 4) -> np.ndarray:
    """Solid 2–4 color raster for thin-stroke vtracer (no new hues)."""
    out = arr.copy()
    a = out[:, :, 3]
    rgb = out[:, :, :3].astype(np.int32)
    ink = a >= 48
    if int(ink.sum()) < 40:
        return out
    pal = _brand_palette(arr, max_colors=max(2, max_colors))
    lum = rgb.mean(axis=2)
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    extras: list[np.ndarray] = []
    if ((ink) & (lum < 32) & (sat < 20)).any():
        extras.append(np.array([0, 0, 0], dtype=np.int32))
    if ((ink) & (lum > 240) & (sat < 14)).any():
        extras.append(np.array([255, 255, 255], dtype=np.int32))
    if not pal and not extras:
        return out
    colors = pal + extras
    # Dedup and cap at 4; keep at least 2 when a second extreme exists.
    uniq: list[np.ndarray] = []
    for c in colors:
        if any(int(np.abs(c - u).sum()) <= 12 for u in uniq):
            continue
        uniq.append(c)
        if len(uniq) >= 4:
            break
    if len(uniq) < 2 and extras:
        for e in extras:
            if all(int(np.abs(e - u).sum()) > 12 for u in uniq):
                uniq.append(e)
            if len(uniq) >= 2:
                break
    if not uniq:
        return out
    stack = np.stack(uniq, axis=0)
    pix = rgb[ink]
    diffs = np.abs(pix[:, None, :] - stack[None, :, :]).sum(axis=2)
    out[ink, :3] = stack[diffs.argmin(axis=1)].astype(np.uint8)
    out[ink, 3] = 255
    out[~ink, 3] = 0
    return out


def prepare_for_engine(arr: np.ndarray) -> np.ndarray:
    """Knockout + halo + residual chroma recover + crop before vectorize / ESRGAN.

    Thin strokes: compute the raw-contrast medial axis *before* knockout/halo
    and forbid those passes from punching the centerline.
    """
    protect = centerline_protect_mask(arr)
    src_plate = _estimate_plate_rgb(arr)
    # Dart `_knockOutOuterPlate` falls back to white so O/B/D/P still punch
    # when corner samples disagree (thin frame / checker).
    hole_plate = src_plate if src_plate is not None else (255, 255, 255)
    out = knockout_border_plate(arr, protect=protect)
    # Same plate estimate Dart keeps across `_knockOutOuterPlate` + holes.
    out = punch_enclosed_plate_holes(out, protect=protect, plate=hole_plate)
    out = strip_halo_fringe(out, protect=protect)
    out = prune_ink_speckles(out, protect=protect)
    thin = is_thin_stroke_mark(out) or is_thin_wordmark(out)
    # Thin marks: recover washed brand strokes with a lower accent floor.
    if thin:
        out = recover_residual_chroma(out, min_accent_sat=22, min_accent_px=8)
    else:
        out = recover_residual_chroma(out, min_accent_sat=28, min_accent_px=12)
    out = punch_enclosed_plate_holes(out, protect=protect, plate=hole_plate)
    out = refine_gray_thin_ink(out)
    out = reinforce_thin_chromatic_strokes(out)
    out = crop_to_ink(out)
    # Self-quantize JPEG mottling. Skip aggressive harden here — it ate thin
    # chromatic strokes and dropped suite baseline (see training_lessons).
    out = snap_to_source_palette(out, out, max_dist=36 if thin else 40)
    # Dart punches after crop/pad. Protect is source-sized — do not reuse it
    # after crop. Snap can also re-tint leftover counters onto the plate hue.
    out = punch_enclosed_plate_holes(out, protect=None, plate=hole_plate)
    return out


def _brand_palette(arr: np.ndarray, max_colors: int = 16) -> list[np.ndarray]:
    a = arr[:, :, 3]
    rgb = arr[:, :, :3].astype(np.int32)
    lum = rgb.mean(axis=2)
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    ink = (a >= 96) & ~((lum < 28) & (sat < 18)) & ~((lum > 245) & (sat < 12))
    if int(ink.sum()) < 40:
        ink = a >= 96
    if not ink.any():
        return []
    q = (rgb[ink] // 16).astype(np.int32)
    keys = q[:, 0] * 4096 + q[:, 1] * 64 + q[:, 2]
    uniq, counts = np.unique(keys, return_counts=True)
    order = np.argsort(-counts)

    def bin_rgb(k: int) -> tuple[int, int, int, float, int]:
        r = (k // 4096) * 16 + 8
        g = ((k // 64) % 64) * 16 + 8
        b = (k % 64) * 16 + 8
        return r, g, b, (r + g + b) / 3.0, max(r, g, b) - min(r, g, b)

    entries: list[tuple[int, np.ndarray, int]] = []  # sat, rgb, count
    for idx in order:
        k = int(uniq[idx])
        r, g, b, lum_c, sat_c = bin_rgb(k)
        if lum_c >= 240 and sat_c <= 14:
            continue
        if lum_c <= 22 and sat_c <= 14:
            continue
        entries.append((sat_c, np.array([r, g, b], dtype=np.int32), int(counts[idx])))

    if not entries:
        return []

    # Prefer chromatic brand fills when any exist (avoid locking to JPEG gray mush).
    chromatic = [e for e in entries if e[0] >= 28]
    pool = chromatic if chromatic else entries
    # Rank by sat*count so residual Arc red beats washed pink majority bins.
    pool_sorted = sorted(pool, key=lambda e: -(e[0] * e[2]))
    return [e[1] for e in pool_sorted[:max_colors]]


def harden_flat_edges(arr: np.ndarray) -> np.ndarray:
    """Collapse JPEG/AA mush at ink boundaries toward solid neighbor fills.

    Universal for flat lockups with soft fringes. Skips fully opaque solid
    pixels (protects grey/silver letter fills). Does not invent hues.
    """
    h, w = arr.shape[:2]
    if h < 4 or w < 4:
        return arr
    out = arr.copy()
    rgb = out[:, :, :3].astype(np.int32)
    a = out[:, :, 3]
    opaque = a >= 180
    if int(opaque.sum()) < 40:
        return arr

    lum = rgb.mean(axis=2)
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    # Mid-alpha fringe only — never rewrite fully opaque interiors.
    soft = (a >= 40) & (a < 230)
    if not soft.any():
        return arr

    hard = opaque & ((sat >= 24) | (lum <= 32) | (lum >= 240))
    ys, xs = np.where(soft)
    for y, x in zip(ys.tolist(), xs.tolist()):
        votes: list[np.ndarray] = []
        near_empty = False
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                ny, nx = y + dy, x + dx
                if ny < 0 or nx < 0 or ny >= h or nx >= w:
                    near_empty = True
                    continue
                if a[ny, nx] < 80:
                    near_empty = True
                    continue
                if not hard[ny, nx]:
                    continue
                votes.append(rgb[ny, nx])
        if not votes:
            continue
        if not near_empty and a[y, x] >= 200:
            continue
        stack = np.stack(votes, axis=0)
        med = np.median(stack, axis=0).astype(np.uint8)
        out[y, x, :3] = med
        if a[y, x] < 220:
            out[y, x, 3] = 255
    return out


def stamp_centerline(restored: np.ndarray, prepared: np.ndarray) -> np.ndarray:
    """Re-ink the structural centerline from a prepared raster into a result.

    Used after SVG rasterize so spline smoothing cannot drop thin terminators.
    """
    h, w = restored.shape[:2]
    src = np.asarray(
        Image.fromarray(prepared, "RGBA").resize((w, h), Image.Resampling.NEAREST)
    )
    core = raw_contrast_mask(prepared, core=True)
    contrast = raw_contrast_mask(prepared)
    skel = structural_centerline(core if int(core.sum()) >= 40 else contrast)
    if not skel.any():
        return restored
    protect = (
        np.asarray(
            Image.fromarray((skel.astype(np.uint8) * 255), "L").resize(
                (w, h), Image.Resampling.NEAREST
            )
        )
        > 127
    )
    if not protect.any():
        return restored
    out = restored.copy()
    # Fill dropped centerline only — do not paint extra halo ink onto empty canvas.
    dead = protect & (out[:, :, 3] < 80)
    if not dead.any():
        return out
    out[dead] = src[dead]
    out[dead, 3] = 255
    return out


def snap_to_source_palette(
    restored: np.ndarray,
    source: np.ndarray,
    max_dist: int = 56,
) -> np.ndarray:
    """Remap restored ink onto nearest source brand fills (palette lock)."""
    palette = _brand_palette(source)
    if not palette:
        return restored
    # Include black/white extremes so AA fringe on light-on-dark marks can lock
    # without inventing mid-tone brand colors.
    pal = np.stack(
        palette
        + [
            np.array([0, 0, 0], dtype=np.int32),
            np.array([255, 255, 255], dtype=np.int32),
        ],
        axis=0,
    )
    out = restored.copy()
    a = out[:, :, 3]
    rgb = out[:, :, :3].astype(np.int32)
    ink = a >= 40
    if not ink.any():
        return out
    pix = rgb[ink]
    lum = pix.mean(axis=1)
    sat = pix.max(axis=1) - pix.min(axis=1)
    # Keep hard black / white and solid grey/silver fills (do not recolor type).
    keep = ((lum < 28) & (sat < 18)) | ((lum > 245) & (sat < 12)) | (
        (sat <= 28) & (lum >= 55) & (lum <= 210)
    )
    work = pix[~keep]
    if len(work) == 0:
        return out
    diffs = np.abs(work[:, None, :] - pal[None, :, :]).sum(axis=2)
    best_i = diffs.argmin(axis=1)
    best_d = diffs.min(axis=1)
    snapped = work.copy()
    ok = best_d <= max_dist
    snapped[ok] = pal[best_i[ok]]
    pix2 = pix.copy()
    pix2[~keep] = snapped
    out_rgb = out[:, :, :3].copy()
    out_rgb[ink] = pix2.astype(np.uint8)
    out[:, :, :3] = out_rgb
    return out


def palette_fidelity_vs(
    source: np.ndarray,
    restored: np.ndarray,
    tol: int = 48,
) -> float:
    """Fraction of restored ink near a quantized source brand color."""
    src_clean = knockout_border_plate(source)
    palette = _brand_palette(src_clean) or _brand_palette(source)
    extremes = [
        np.array([0, 0, 0], dtype=np.int32),
        np.array([255, 255, 255], dtype=np.int32),
    ]
    if palette:
        pal = np.stack(palette + extremes, axis=0)
    else:
        pal = np.stack(extremes, axis=0)
    dm = restored[:, :, 3] >= 96
    if not dm.any():
        return 0.0
    pix = restored[dm, :3].astype(np.int32)
    diffs = np.abs(pix[:, None, :] - pal[None, :, :]).sum(axis=2)
    best = diffs.min(axis=1)
    return float((best <= tol).mean())


def aspect_drift(src: np.ndarray, dst: np.ndarray) -> float:
    def aabb(mask: np.ndarray) -> tuple[int, int, int, int] | None:
        ys, xs = np.where(mask)
        if len(xs) == 0:
            return None
        return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())

    sa = aabb(src[:, :, 3] >= 96)
    sb = aabb(dst[:, :, 3] >= 96)
    if sa is None or sb is None:
        return 1.0
    aw = max(1, sa[2] - sa[0] + 1)
    ah = max(1, sa[3] - sa[1] + 1)
    bw = max(1, sb[2] - sb[0] + 1)
    bh = max(1, sb[3] - sb[1] + 1)
    aa, ba = aw / ah, bw / bh
    return abs(aa - ba) / max(aa, 0.01)


def _ink_aabb(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def ink_mask_iou(src: np.ndarray, dst: np.ndarray) -> float:
    """Ink-mask IoU after cropping both to opaque AABB, then matching size.

    Same geometry check as the golden suite — used to reject ESRGAN/vectorize
    redraws that collapse thin wordmark strokes (Arc plate_halo).
    """
    def crop(arr: np.ndarray) -> np.ndarray:
        box = _ink_aabb(arr[:, :, 3] >= 96)
        if box is None:
            return arr
        x0, y0, x1, y1 = box
        return arr[y0 : y1 + 1, x0 : x1 + 1]

    sa = crop(src)
    da = crop(dst)
    h, w = sa.shape[:2]
    if h < 1 or w < 1:
        return 0.0
    d = np.asarray(
        Image.fromarray(da, "RGBA").resize((w, h), Image.Resampling.NEAREST)
    )
    a = sa[:, :, 3] >= 96
    b = d[:, :, 3] >= 96
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    return float(inter / union) if union else 0.0


def edge_energy(arr: np.ndarray) -> float:
    """Cheap alpha-weighted gradient energy (oversmooth detector)."""
    gray = arr[:, :, :3].astype(np.float32).mean(axis=2)
    a = arr[:, :, 3].astype(np.float32) / 255.0
    g = gray * a
    gx = np.abs(np.diff(g, axis=1)).mean() if g.shape[1] > 1 else 0.0
    gy = np.abs(np.diff(g, axis=0)).mean() if g.shape[0] > 1 else 0.0
    return float(gx + gy)


def fit_aspect_to_source(restored: np.ndarray, source: np.ndarray) -> np.ndarray:
    """Non-uniform scale restored ink AABB to match source aspect (no chroma invent).

    Used when vectorize/SVG rasterization stretches a lockup (Propak) while the
    prepared source aspect is still trustworthy.
    """
    sa = _ink_aabb(source[:, :, 3] >= 96)
    sb = _ink_aabb(restored[:, :, 3] >= 96)
    if sa is None or sb is None:
        return restored
    aw = max(1, sa[2] - sa[0] + 1)
    ah = max(1, sa[3] - sa[1] + 1)
    bw = max(1, sb[2] - sb[0] + 1)
    bh = max(1, sb[3] - sb[1] + 1)
    aa = aw / float(ah)
    ba = bw / float(bh)
    if abs(aa - ba) / max(aa, 0.01) < 0.04:
        return restored
    # Keep height; adjust width to source aspect.
    target_w = max(1, int(round(bh * aa)))
    if target_w == bw:
        return restored
    x0, y0, x1, y1 = sb
    crop = restored[y0 : y1 + 1, x0 : x1 + 1]
    return np.asarray(
        Image.fromarray(crop, "RGBA").resize(
            (target_w, crop.shape[0]), Image.Resampling.LANCZOS
        )
    )


def finalize_restore(
    restored: np.ndarray,
    source: np.ndarray,
    *,
    min_palette: float = 0.18,
    max_aspect_drift: float = 0.35,
    min_ink_iou: float | None = None,
    fit_aspect: bool = False,
) -> np.ndarray:
    """Plate/halo cleanup + palette lock. Raises if palette/geometry collapsed."""
    # Protect from the *source* raster only. Density on an upscaled restore
    # is unstable (Trialta BICUBIC fringe looked like a thin stroke).
    protect_s = centerline_protect_mask(source)
    protect_r = None
    if protect_s is not None:
        rh, rw = restored.shape[:2]
        sh, sw = protect_s.shape[:2]
        if (rh, rw) == (sh, sw):
            protect_r = protect_s
        else:
            protect_r = (
                np.asarray(
                    Image.fromarray((protect_s.astype(np.uint8) * 255), "L").resize(
                        (rw, rh), Image.Resampling.NEAREST
                    )
                )
                > 127
            )
    out = knockout_border_plate(restored, protect=protect_r)
    out = strip_halo_fringe(out, protect=protect_r)
    out = crop_to_ink(out)
    src_clean = knockout_border_plate(source, protect=protect_s)
    src_clean = strip_halo_fringe(src_clean, protect=protect_s)
    if fit_aspect:
        out = fit_aspect_to_source(out, src_clean)
        out = crop_to_ink(out)
    out = snap_to_source_palette(out, src_clean)
    fid = palette_fidelity_vs(src_clean, out)
    if fid < min_palette:
        raise RuntimeError(f"palette collapse (fidelity={fid:.3f})")
    drift = aspect_drift(src_clean, out)
    if drift > max_aspect_drift:
        raise RuntimeError(f"aspect warp (drift={drift:.3f})")
    if min_ink_iou is not None:
        iou = ink_mask_iou(src_clean, out)
        if iou < min_ink_iou:
            raise RuntimeError(f"ink geometry collapse (iou={iou:.3f})")
    return out
