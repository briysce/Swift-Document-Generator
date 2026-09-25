"""Anthropic Claude vision advisor."""

from __future__ import annotations

import sys

from PIL import Image

from .base import CritiqueResult, SourceHints, encode_png, env_key, extract_json, http_post_json

MODEL = "claude-sonnet-4-20250514"

SOURCE_PROMPT = """Analyze this logo PNG for vector tracing. Focus on letter counters (holes), especially P eyes in SUPPLY.

Return JSON only:
{
  "has_holes": true,
  "hole_descriptions": ["..."],
  "letter_regions": [{"name": "SUPPLY", "y_frac": [0.62, 0.92]}],
  "recommended_backends": ["opencv-tree", "potrace"],
  "recommended_preprocess": {"upscale": 4, "blur_radius": 1.2, "alpha_threshold": 80},
  "straight_vs_curve": "...",
  "notes": "..."
}"""

CRITIQUE_PROMPT = """Critique this vector trace. Image 1 = reference PNG, Image 2 = SVG render on black.
Backend={backend}, alpha_iou={alpha_iou:.3f}, p_hollow={p_hollow:.3f}, detected_holes={holes}.

Flag missing P counters (filled orange instead of transparent) and jagged stairstep edges.

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
    return env_key("ANTHROPIC_API_KEY") is not None


def _call_claude(content: list[dict]) -> str:
    key = env_key("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    payload = {
        "model": MODEL,
        "max_tokens": 1024,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": content}],
    }
    data = http_post_json(
        "https://api.anthropic.com/v1/messages",
        payload,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
    )
    try:
        return data["content"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected Claude response: {data!r}") from exc


def analyze_source(img: Image.Image) -> SourceHints | None:
    try:
        b64, mime = encode_png(img)
        text = _call_claude(
            [
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                {"type": "text", "text": SOURCE_PROMPT},
            ]
        )
        return SourceHints.from_dict(extract_json(text), provider="claude")
    except Exception as exc:
        print(f"[ai/claude] source analysis failed: {exc}", file=sys.stderr)
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
        text = _call_claude(
            [
                {"type": "text", "text": prompt},
                {"type": "image", "source": {"type": "base64", "media_type": ref_mime, "data": ref_b64}},
                {"type": "image", "source": {"type": "base64", "media_type": ren_mime, "data": ren_b64}},
            ]
        )
        return CritiqueResult.from_dict(extract_json(text), provider="claude")
    except Exception as exc:
        print(f"[ai/claude] critique failed: {exc}", file=sys.stderr)
        return None
