"""Google Gemini vision advisor."""

from __future__ import annotations

import os
import sys
from typing import Any

from PIL import Image

from .base import CritiqueResult, SourceHints, encode_png, env_key, extract_json, http_post_json

# Prefer flash multimodal — override with GEMINI_MODEL env if needed.
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash").strip() or "gemini-2.0-flash"
PROJECT_NUMBER = os.environ.get("GEMINI_PROJECT_NUMBER", "").strip()
if PROJECT_NUMBER:
    # Visible in Fly / local logs for ops correlation only.
    pass

SOURCE_PROMPT = """Analyze this logo PNG for high-quality vector tracing and cleanup.

Identify:
- Exact brand / company name text if visible
- Likely font family classification (e.g. geometric sans, slab serif, script)
- Dominant brand colors as #RRGGBB hex (2–6 colors, ignore anti-alias fringe)
- Letter counters/holes (especially P letter eyes), straight vs curved strokes
- Geometric layout / shape boundaries useful for background removal
- Preprocessing recommendations for vectorization

Return JSON only:
{
  "brand_name": "ACME Energy",
  "font_family_guess": "geometric sans-serif",
  "dominant_colors_hex": ["#CE4E30", "#111111"],
  "layout_summary": "wordmark left of icon; horizontal lockup",
  "has_holes": true,
  "hole_descriptions": ["P counters in wordmark"],
  "letter_regions": [{"name": "ACME", "y_frac": [0.2, 0.8]}],
  "recommended_backends": ["opencv-tree", "potrace", "vtracer"],
  "recommended_preprocess": {"upscale": 4, "blur_radius": 1.2, "alpha_threshold": 80},
  "straight_vs_curve": "bold curves on icon; straight stems on wordmark",
  "notes": "preserve evenodd holes; strip studio backdrop"
}"""

VALIDATE_PROMPT = """You are filtering scraped web images for a corporate logo picker.

Decide if this image is a clean usable company logo / brand mark suitable for a
shipping label (wordmark, icon, or lockup). Reject photos of people, trucks,
equipment, warehouses, screenshots of articles, memes, or low-quality artifacts.

Return JSON only:
{
  "is_valid_logo": true,
  "has_transparent_or_solid_background": true,
  "confidence_score": 0.92,
  "reason": "Clean wordmark on transparent background"
}"""

CRITIQUE_PROMPT = """Compare the logo reference (image 1) vs SVG trace render (image 2).
Metrics: backend={backend}, alpha_iou={alpha_iou:.3f}, p_hollow={p_hollow:.3f}, holes={holes}

Check: Are P letter counters transparent/open (not filled)? Jagged stairstep edges?

Return JSON only:
{
  "passes_qa": true,
  "missing_counters": false,
  "jagged_regions": false,
  "confidence": 0.9,
  "issues": [],
  "reject_candidate": false
}"""


def available() -> bool:
    return env_key("GOOGLE_API_KEY", "GEMINI_API_KEY") is not None


def _call_gemini(parts: list[dict[str, Any]]) -> str:
    key = env_key("GOOGLE_API_KEY", "GEMINI_API_KEY")
    if not key:
        raise RuntimeError("Gemini API key not set (GOOGLE_API_KEY or GEMINI_API_KEY)")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{MODEL}:generateContent?key={key}"
    )
    payload: dict[str, Any] = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.2,
        },
    }

    data = http_post_json(url, payload, headers={}, timeout=60, max_retries=3)
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected Gemini response: {data!r}") from exc


def analyze_source(img: Image.Image) -> SourceHints | None:
    try:
        b64, mime = encode_png(img)
        text = _call_gemini(
            [
                {"inline_data": {"mime_type": mime, "data": b64}},
                {"text": SOURCE_PROMPT},
            ]
        )
        return SourceHints.from_dict(extract_json(text), provider="gemini")
    except Exception as exc:
        print(f"[ai/gemini] source analysis failed: {exc}", file=sys.stderr)
        return None


def validate_logo_candidate(img: Image.Image) -> dict[str, Any] | None:
    """Return validation JSON for crawler filtering, or None on failure."""
    try:
        b64, mime = encode_png(img, max_width=1024)
        text = _call_gemini(
            [
                {"inline_data": {"mime_type": mime, "data": b64}},
                {"text": VALIDATE_PROMPT},
            ]
        )
        data = extract_json(text)
        return {
            "is_valid_logo": bool(data.get("is_valid_logo", False)),
            "has_transparent_or_solid_background": bool(
                data.get("has_transparent_or_solid_background", False)
            ),
            "confidence_score": float(data.get("confidence_score", 0.0)),
            "reason": str(data.get("reason", "")),
            "raw": data,
        }
    except Exception as exc:
        print(f"[ai/gemini] logo validation failed: {exc}", file=sys.stderr)
        return None


def critique_render(
    ref_img: Image.Image,
    render_img: Image.Image,
    *,
    backend: str,
    alpha_iou: float,
    p_hollow: float,
    holes: int,
) -> CritiqueResult | None:
    try:
        ref_b64, ref_mime = encode_png(ref_img)
        ren_b64, ren_mime = encode_png(render_img)
        prompt = CRITIQUE_PROMPT.format(
            backend=backend,
            alpha_iou=alpha_iou,
            p_hollow=p_hollow,
            holes=holes,
        )
        text = _call_gemini(
            [
                {"text": prompt},
                {"text": "Image 1 — reference PNG:"},
                {"inline_data": {"mime_type": ref_mime, "data": ref_b64}},
                {"text": "Image 2 — SVG render:"},
                {"inline_data": {"mime_type": ren_mime, "data": ren_b64}},
            ]
        )
        return CritiqueResult.from_dict(extract_json(text), provider="gemini")
    except Exception as exc:
        print(f"[ai/gemini] critique failed: {exc}", file=sys.stderr)
        return None
