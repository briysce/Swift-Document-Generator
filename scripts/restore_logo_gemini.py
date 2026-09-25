"""Live Gemini logo restore matching mobile/lib/gemini_client.dart + LogoRestorer.finalizeRestoredPng."""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
MIN_H = 3000
MODELS = [
    "gemini-3.1-flash-image",
    "gemini-2.5-flash-image",
    "gemini-3-pro-image",
]
# Match mobile/lib/gemini_client.dart — conservator restore, every logo.
PROMPT = """
Restore this company logo the way a conservator restores a photograph: enhance
what is already there into a clean, high-resolution PNG. Do not invent a new
design.

Enhance the existing pixels — super-resolve and de-blur. Keep every letterform,
icon, curve, thickness, and proportion identical to the source.

Repair edges: remove JPEG stair-steps, pixelation, frayed rims, and white/gray
compression halo. Do not over-sharpen, cartoon, outline, or thicken strokes.

Repair blotchy patches: where a region is meant to be one solid brand color,
even it out. Do not posterize real gradients, photos, or intentional texture.

True alpha — never bake a black, white, gray, or checkerboard plate.
COLORS: copy the source hues exactly — do not warm, cool, neon-shift, or invent
fills.
If the source letters have a dark/black border or outline, keep that stroke.
No extra padding, mockups, drop shadows, or new text.
Never add Gemini, Google, Spark, Imagen, or any AI watermark, badge, sparkle,
or wordmark. The logo is unbranded — do not stamp a generator mark.
Do not invent a company name, tagline, or second lockup that is not in the source.
Output a high-definition PNG, 3000 pixels tall. Image only.
"""


def _load_key() -> str:
    for path in (ROOT / ".env", ROOT / ".env.local"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() in ("GEMINI_API_KEY", "GOOGLE_API_KEY") and v.strip():
                return v.strip().strip('"').strip("'")
    return (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()


def _extract_image(body: dict) -> bytes | None:
    for cand in body.get("candidates") or []:
        parts = ((cand or {}).get("content") or {}).get("parts") or []
        for part in parts:
            inline = part.get("inlineData") or part.get("inline_data") or {}
            data = inline.get("data") or ""
            if data:
                return base64.b64decode(data)
    return None


def _prepare_source_png(src: Path) -> bytes:
    """Knock out baked plates and halo so Gemini sees the lockup, not the JPEG matte."""
    import numpy as np

    im = _knockout_gemini_plate(Image.open(src).convert("RGBA"))
    im = _strip_halo_fringe(im)
    arr = np.array(im)
    a = arr[:, :, 3]
    rgb = arr[:, :, :3]
    lum = rgb.astype(np.int32).sum(axis=2) / 3.0
    sat = np.maximum(np.maximum(rgb[:, :, 0], rgb[:, :, 1]), rgb[:, :, 2]) - np.minimum(
        np.minimum(rgb[:, :, 0], rgb[:, :, 1]), rgb[:, :, 2]
    )
    ink = (
        (a >= 96)
        & ~((lum <= 40) & (sat <= 16))
        & ~((rgb[:, :, 0] >= 240) & (rgb[:, :, 1] >= 240) & (rgb[:, :, 2] >= 240))
    )
    ys, xs = np.where(ink)
    if ys.size:
        pad = 4
        miny, maxy = max(0, int(ys.min()) - pad), min(arr.shape[0], int(ys.max()) + pad + 1)
        minx, maxx = max(0, int(xs.min()) - pad), min(arr.shape[1], int(xs.max()) + pad + 1)
        arr = arr[miny:maxy, minx:maxx]
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def restore(src: Path) -> bytes:
    key = _load_key()
    if not key:
        raise SystemExit("GEMINI_API_KEY missing")
    prepared = _prepare_source_png(src)
    b64 = base64.b64encode(prepared).decode("ascii")
    mime = "image/png"
    with Image.open(io.BytesIO(prepared)) as im:
        aspect = im.width / max(im.height, 1)
    aspect_token = (
        "16:9" if aspect >= 1.6 else "4:3" if aspect >= 1.25 else "3:4" if aspect <= 0.8 else "1:1"
    )
    prompt = PROMPT + _catalog_addenda()
    try:
        import cv2
        import numpy as np
        import sys as _sys
        if str(ROOT) not in _sys.path:
            _sys.path.insert(0, str(ROOT))
        from logo_trace import detect_letter_outline

        arr = np.array(Image.open(src).convert("RGBA"))
        bgr = cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2BGR)
        hint = detect_letter_outline(bgr, arr[:, :, 3])
        if hint is not None:
            color, _wf = hint
            prompt += (
                f"\nCRITICAL: The source has a dark BGR{color} border around the letters. "
                "Recreate that stroke on every glyph, including inner holes. It is not background.\n"
            )
            print("detected letter outline", color, flush=True)
    except Exception as e:
        print(f"outline detect skipped ({e})", flush=True)
    payload = {
        "contents": [
            {
                "parts": [
                    {"inline_data": {"mime_type": mime, "data": b64}},
                    {"text": prompt},
                ]
            }
        ],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "temperature": 0.1,
            "imageConfig": {"aspectRatio": aspect_token},
        },
    }
    last = None
    for model in MODELS:
        uri = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={key}"
        )
        body = dict(payload)
        req = urllib.request.Request(
            uri,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                status = resp.status
                raw_json = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            status = e.code
            err_body = e.read().decode("utf-8", errors="replace")
            if status == 429:
                print(f"{model} HTTP 429 — waiting 20s", flush=True)
                import time
                time.sleep(20)
                last = err_body[:400]
                continue
            if status >= 400 and "imageConfig" in body["generationConfig"]:
                body["generationConfig"].pop("imageConfig", None)
                req2 = urllib.request.Request(
                    uri,
                    data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(req2, timeout=180) as resp:
                        status = resp.status
                        raw_json = json.loads(resp.read().decode("utf-8"))
                except urllib.error.HTTPError as e2:
                    print(f"{model} HTTP {e2.code}", flush=True)
                    last = e2.read().decode("utf-8", errors="replace")[:400]
                    continue
            else:
                print(f"{model} HTTP {status}", flush=True)
                last = err_body[:400]
                continue
        print(f"{model} HTTP {status}", flush=True)
        if status == 200:
            img_bytes = _extract_image(raw_json)
            if img_bytes:
                return img_bytes
        last = str(raw_json)[:400]
    raise SystemExit(f"Gemini restore failed: {last}")


def _knockout_gemini_plate(im: Image.Image) -> Image.Image:
    """Gemini often returns an opaque white/black/checkerboard canvas. Punch it to alpha."""
    import numpy as np
    import cv2

    arr = np.array(im.convert("RGBA"))
    r = arr[:, :, 0].astype(np.int16)
    g = arr[:, :, 1].astype(np.int16)
    b = arr[:, :, 2].astype(np.int16)
    a = arr[:, :, 3]
    white = (r >= 235) & (g >= 235) & (b >= 235)
    checker = (np.abs(r - g) < 12) & (np.abs(g - b) < 12) & (r >= 170) & (r <= 250)
    lum = (r.astype(np.int32) + g.astype(np.int32) + b.astype(np.int32)) / 3.0
    sat = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    black = (lum <= 40) & (sat <= 16)
    opaque = a > 32
    if opaque.mean() > 0.80 and white.mean() > 0.18:
        a = np.where(white | checker, 0, a)
    a = _punch_border_component(a, white | checker | black)
    arr[:, :, 3] = a
    return Image.fromarray(arr)


def _punch_border_component(alpha: "object", plate: "object") -> "object":
    """Clear plate pixels whose connected component touches the canvas border."""
    import cv2
    import numpy as np

    mask = ((plate) & (alpha > 32)).astype(np.uint8)
    if mask.mean() < 0.02:
        return alpha
    num, labels = cv2.connectedComponents(mask, connectivity=8)
    h, w = mask.shape
    border = np.unique(
        np.concatenate(
            [
                labels[0, :],
                labels[-1, :],
                labels[:, 0],
                labels[:, -1],
            ]
        )
    )
    out = alpha.copy()
    for lab in border:
        if lab == 0:
            continue
        out[labels == lab] = 0
    return out


def _clip_to_source_ink(arr, src: Path):
    """Drop invented plates that sit outside the source lockup silhouette."""
    import cv2
    import numpy as np

    src_im = _strip_halo_fringe(
        _knockout_gemini_plate(Image.open(src).convert("RGBA"))
    )
    s = np.array(src_im)
    s_a = s[:, :, 3]
    rgb = s[:, :, :3]
    lum = rgb.astype(np.int32).sum(axis=2) / 3.0
    sat = np.maximum(np.maximum(rgb[:, :, 0], rgb[:, :, 1]), rgb[:, :, 2]) - np.minimum(
        np.minimum(rgb[:, :, 0], rgb[:, :, 1]), rgb[:, :, 2]
    )
    ink = (
        (s_a >= 96)
        & ~((lum <= 40) & (sat <= 16))
        & ~((rgb[:, :, 0] >= 240) & (rgb[:, :, 1] >= 240) & (rgb[:, :, 2] >= 240))
    )
    if int(ink.sum()) < 30:
        return arr
    h, w = arr.shape[:2]
    mask = cv2.resize(
        ink.astype(np.uint8) * 255,
        (w, h),
        interpolation=cv2.INTER_LINEAR,
    )
    rad = max(3, h // 70)
    if rad % 2 == 0:
        rad += 1
    mask = cv2.dilate(
        mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (rad, rad))
    )
    arr = arr.copy()
    arr[:, :, 3] = np.where(mask > 32, arr[:, :, 3], 0)
    return arr


def _strip_halo_fringe(im: Image.Image) -> Image.Image:
    """Punch light gray/white JPEG halo between brand ink and empty canvas."""
    import cv2
    import numpy as np

    arr = np.array(im.convert("RGBA"))
    r = arr[:, :, 0].astype(np.int16)
    g = arr[:, :, 1].astype(np.int16)
    b = arr[:, :, 2].astype(np.int16)
    a = arr[:, :, 3]
    sat = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
    lum = (r.astype(np.int32) + g.astype(np.int32) + b.astype(np.int32)) / 3.0
    chromatic = (a >= 80) & (sat > 45)
    empty = (a < 40) | ((lum <= 40) & (sat <= 16) & (a > 0))
    fringe = (a >= 40) & (sat < 42) & (lum > 88)
    kernel = np.ones((3, 3), np.uint8)
    near_ink = cv2.dilate(chromatic.astype(np.uint8), kernel, iterations=1) > 0
    near_empty = cv2.dilate(empty.astype(np.uint8), kernel, iterations=1) > 0
    punch = fringe & near_ink & near_empty
    arr[:, :, 3] = np.where(punch, 0, a)
    return Image.fromarray(arr)


def _working_rgba(arr, max_side: int = 400):
    import cv2
    import numpy as np

    h, w = arr.shape[:2]
    m = max(h, w)
    if m <= max_side:
        return arr
    scale = max_side / m
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    rgb = cv2.resize(arr[:, :, :3], (nw, nh), interpolation=cv2.INTER_AREA)
    alpha = cv2.resize(arr[:, :, 3], (nw, nh), interpolation=cv2.INTER_AREA)
    return np.dstack([rgb, alpha])


def _strip_foreign_marks(im: Image.Image) -> Image.Image:
    """Punch disconnected corner / bottom Gemini watermarks."""
    import numpy as np
    import cv2

    arr = np.array(im.convert("RGBA"))
    h, w = arr.shape[:2]
    if w < 16 or h < 16:
        return im
    ink = arr[:, :, 3] >= 80
    num, labels, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=4)
    if num < 3:
        return im
    areas = stats[1:, cv2.CC_STAT_AREA]
    main_i = 1 + int(np.argmax(areas))
    main_area = max(1, int(stats[main_i, cv2.CC_STAT_AREA]))
    mx, my, mw, mh = (
        stats[main_i, cv2.CC_STAT_LEFT],
        stats[main_i, cv2.CC_STAT_TOP],
        stats[main_i, cv2.CC_STAT_WIDTH],
        stats[main_i, cv2.CC_STAT_HEIGHT],
    )
    pad_x, pad_y = max(8, int(w * 0.08)), max(8, int(h * 0.08))
    for i in range(1, num):
        if i == main_i:
            continue
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area > main_area * 0.12:
            continue
        x, y, bw, bh = (
            stats[i, cv2.CC_STAT_LEFT],
            stats[i, cv2.CC_STAT_TOP],
            stats[i, cv2.CC_STAT_WIDTH],
            stats[i, cv2.CC_STAT_HEIGHT],
        )
        near = (
            x + bw >= mx - pad_x
            and x <= mx + mw + pad_x
            and y + bh >= my - pad_y
            and y <= my + mh + pad_y
        )
        if near:
            continue
        cx, cy = x + bw / 2, y + bh / 2
        in_corner = (cx < w * 0.22 or cx > w * 0.78) and (cy < h * 0.22 or cy > h * 0.78)
        along_bottom = cy > h * 0.86 and area < main_area * 0.08
        if not in_corner and not along_bottom:
            continue
        arr[:, :, 3][labels == i] = 0
    return Image.fromarray(arr)


def finalize(png: bytes, dest: Path, src: Path) -> None:
    """Studio finish after Gemini: plates/halo out, even blotchy fills, 3000px PNG.

    Does not re-trace or cartoon the mark — Gemini already repaired edges.
    """
    import numpy as np
    import cv2
    import importlib.util
    import sys as _sys

    im = _knockout_gemini_plate(Image.open(io.BytesIO(png)).convert("RGBA"))
    im = _strip_foreign_marks(im)
    im = _strip_halo_fringe(im)
    arr = np.array(im)
    a = arr[:, :, 3]
    rgb = arr[:, :, :3]
    lum = rgb.astype(np.int32).sum(axis=2) / 3.0
    sat = np.maximum(np.maximum(rgb[:, :, 0], rgb[:, :, 1]), rgb[:, :, 2]) - np.minimum(
        np.minimum(rgb[:, :, 0], rgb[:, :, 1]), rgb[:, :, 2]
    )
    ink = (
        (a >= 96)
        & ~((rgb[:, :, 0] >= 240) & (rgb[:, :, 1] >= 240) & (rgb[:, :, 2] >= 240))
        & ~((lum <= 40) & (sat <= 16))
    )
    ys, xs = np.where(ink)
    if ys.size:
        pad = 2
        miny = max(0, int(ys.min()) - pad)
        maxy = min(arr.shape[0], int(ys.max()) + pad + 1)
        minx = max(0, int(xs.min()) - pad)
        maxx = min(arr.shape[1], int(xs.max()) + pad + 1)
        im = Image.fromarray(arr[miny:maxy, minx:maxx])
    dest.parent.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location("logo_restorer", ROOT / "logo_restorer.py")
    lr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lr)
    arr = np.array(im.convert("RGBA"))
    bgr = cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2BGR)
    alpha = arr[:, :, 3]
    work = _working_rgba(arr, 1600)
    wbgr = cv2.cvtColor(work[:, :, :3], cv2.COLOR_RGB2BGR)
    walpha = work[:, :, 3]
    wbgr = lr._flatten_solid_areas(wbgr)
    if str(ROOT) not in _sys.path:
        _sys.path.insert(0, str(ROOT))
    try:
        from logo_trace import apply_letter_outline, detect_letter_outline

        src_arr = np.array(Image.open(src).convert("RGBA"))
        src_bgr = cv2.cvtColor(src_arr[:, :, :3], cv2.COLOR_RGB2BGR)
        hint = detect_letter_outline(src_bgr, src_arr[:, :, 3])
        restored_hint = detect_letter_outline(wbgr, walpha)
        if hint is not None and restored_hint is None:
            color, wf = hint
            wbgr, walpha = apply_letter_outline(wbgr, walpha, color, wf)
            print("applied missing letter outline", color, flush=True)
    except Exception as e:
        print(f"outline apply skipped ({e})", flush=True)
    bgr, alpha = wbgr, walpha
    if bgr.shape[0] < MIN_H:
        scale = MIN_H / bgr.shape[0]
        bgr = cv2.resize(
            bgr,
            (max(1, int(round(bgr.shape[1] * scale))), MIN_H),
            interpolation=cv2.INTER_LANCZOS4,
        )
        alpha = cv2.resize(
            alpha,
            (bgr.shape[1], bgr.shape[0]),
            interpolation=cv2.INTER_LANCZOS4,
        )
    lr._save_png(str(dest), bgr, alpha)
    print(f"wrote {dest} {bgr.shape[1]}x{bgr.shape[0]} PNG", flush=True)
    _record_catalog(src.name, True)


def _catalog_addenda() -> str:
    path = ROOT / "customer_logos" / "logo_restore_lessons.json"
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    lines = []
    for e in data.get("promptAddenda") or []:
        if isinstance(e, str) and e.strip():
            lines.append(e.strip())
    if not lines:
        return ""
    return "\n" + "\n".join(lines[:8]) + "\n"


def _record_catalog(source_name: str, ok: bool) -> None:
    path = ROOT / "customer_logos" / "logo_restore_lessons.json"
    data = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    techniques = data.setdefault("techniques", {})
    for tid in (
        "gemini_primary",
        "plate_knockout",
        "no_watermark",
        "halo_strip",
        "solid_fills",
        "studio_finish",
        "color_lock",
        "aspect_match",
    ):
        t = techniques.setdefault(tid, {"notes": tid, "uses": 0, "wins": 0, "fails": 0})
        t["uses"] = int(t.get("uses", 0)) + 1
        if ok:
            t["wins"] = int(t.get("wins", 0)) + 1
        else:
            t["fails"] = int(t.get("fails", 0)) + 1
    addenda = data.setdefault("promptAddenda", [])
    for line in (
        "Never draw Gemini, Google, Spark, Imagen, or any AI watermark, badge, or wordmark.",
        "Do not add extra company names, taglines, or lockups that are not in the source.",
        "Keep true alpha — never bake a black, white, gray, or checkerboard plate.",
        "Enhance existing pixels; repair frayed edges and blotchy solids without redrawing or over-sharpening.",
        "Leave real gradients and texture; only even regions that should be one brand fill.",
    ):
        if line not in addenda:
            addenda.append(line)
    hist = data.setdefault("history", [])
    from datetime import datetime, timezone

    hist.append(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "source": source_name,
            "grade": "favourable" if ok else "fail",
            "techniques": [
                "gemini_primary",
                "plate_knockout",
                "no_watermark",
                "halo_strip",
                "solid_fills",
                "studio_finish",
            ],
        }
    )
    data["history"] = hist[-80:]
    data["version"] = 2
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def main() -> None:
    if len(sys.argv) >= 4 and sys.argv[1] == "--refinalize":
        gemini_png = Path(sys.argv[2])
        src = Path(sys.argv[3])
        finalize(gemini_png.read_bytes(), gemini_png, src)
        return
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "customer_logos" / "murrays_trucking.png"
    if len(sys.argv) > 2:
        dest = Path(sys.argv[2])
    else:
        dest = src.with_name(f"{src.stem}_restored.png")
    png = restore(src)
    finalize(png, dest, src)


if __name__ == "__main__":
    main()
