"""OpenAI ChatGPT vision advisor."""

from __future__ import annotations

import sys

from PIL import Image

from .base import CritiqueResult, SourceHints, encode_png, env_key, extract_json, http_post_json

MODEL = "gpt-4o"

SOURCE_PROMPT = """Analyze this logo PNG for vector tracing. Detect holes/counters (P letter eyes), curves vs straight segments.

Return JSON:
{
  "has_holes": true,
  "hole_descriptions": ["P counters in SUPPLY"],
  "letter_regions": [{"name": "SUPPLY", "y_frac": [0.62, 0.92]}],
  "recommended_backends": ["opencv-tree", "potrace", "vtracer"],
  "recommended_preprocess": {"upscale": 4, "blur_radius": 1.2, "alpha_threshold": 80},
  "straight_vs_curve": "...",
  "notes": "use fill-rule evenodd for holes"
}"""

CRITIQUE_PROMPT = """Compare reference PNG (first image) vs SVG render (second image).
backend={backend}, alpha_iou={alpha_iou:.3f}, p_hollow={p_hollow:.3f}, holes={holes}

Reject if P counters are filled solid or edges are jagged/pixelated.

Return JSON:
{
  "passes_qa": true,
  "missing_counters": false,
  "jagged_regions": false,
  "confidence": 0.9,
  "issues": [],
  "reject_candidate": false,
  "simplification_notes": "optional path hints"
}"""


def available() -> bool:
    return env_key("OPENAI_API_KEY") is not None


def _call_openai(content: list[dict]) -> str:
    key = env_key("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set")

    payload = {
        "model": MODEL,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": content}],
    }
    data = http_post_json(
        "https://api.openai.com/v1/chat/completions",
        payload,
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected OpenAI response: {data!r}") from exc


def analyze_source(img: Image.Image) -> SourceHints | None:
    try:
        b64, mime = encode_png(img)
        data_url = f"data:{mime};base64,{b64}"
        text = _call_openai(
            [
                {"type": "text", "text": SOURCE_PROMPT},
                {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
            ]
        )
        return SourceHints.from_dict(extract_json(text), provider="openai")
    except Exception as exc:
        print(f"[ai/openai] source analysis failed: {exc}", file=sys.stderr)
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
        text = _call_openai(
            [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{ref_mime};base64,{ref_b64}", "detail": "high"}},
                {"type": "image_url", "image_url": {"url": f"data:{ren_mime};base64,{ren_b64}", "detail": "high"}},
            ]
        )
        return CritiqueResult.from_dict(extract_json(text), provider="openai")
    except Exception as exc:
        print(f"[ai/openai] critique failed: {exc}", file=sys.stderr)
        return None
