"""Gemini and Claude as working minds beside the engine.

The engine is a tool: it traces, fits and reconstructs, but it cannot see that
it is stuck or reason about why. These two can. Each can be asked to

  critique   look at the sketch and the engine's result and say what a designer
             would say: same logo or not, what is missing, what is drawn that
             should not be, which lines should be straight, which curves round;
  take over  draw the logo themselves, as an SVG;
  redraw     (Gemini's image model) paint a clean, sharp version of the sketch
             for the engine to trace — the re-creation step a designer does
             before tracing;
  revise     redo their own drawing after the other mind has critiqued it.

Every question and answer goes into Meedo-Me's consultation memory
(`meedo_consult`), with the images it was about, so Meedo-Me can judge the
answers against what happened and learn from both. A mind's drawing is a
candidate like any other: it ships only if Meedo-Me's reviewer passes it and
it beats the engine (scripts/logo_minds.py).

Keys come from the environment or the gitignored .env.local (env_loader);
nothing here writes a key anywhere. Models are set by CLAUDE_MIND_MODEL,
GEMINI_MIND_MODEL and GEMINI_IMAGE_MODEL.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .base import encode_png, env_key, extract_json, http_post_json

CLAUDE_MODEL = "claude-opus-5-5"
GEMINI_MODEL = "gemini-pro-latest"
GEMINI_IMAGE_MODEL = "gemini-3-pro-image"
MEEDO_OLLAMA = "http://127.0.0.1:11434"

GOAL = """You are helping restore a company's logo. The raster you are shown is a
damaged sketch of the logo (blurred, downscaled, JPEG-crushed, or imported
badly), never the target. The target is the vector a professional designer
would draw over it: straight lines exactly straight, circles exactly round,
corners sharp where the design has corners, every element the sketch shows
present (letters, dots, bars, marks, taglines, outlines, shadows), the brand
colours flat and correct, and the page, the blur, the JPEG noise and the
anti-aliasing NOT drawn. "Closer to the raster" is not "better": a blurry copy
of a blurry sketch is wrong. Never invent elements, never change the letters or
the design, never substitute a different company's logo."""


def _keys_loaded() -> None:
    try:
        from ..env_loader import load_env

        load_env()
    except Exception:
        pass


def model_of(mind: str) -> str:
    _keys_loaded()
    if mind == "claude":
        return os.environ.get("CLAUDE_MIND_MODEL", "").strip() or CLAUDE_MODEL
    if mind == "gemini":
        return os.environ.get("GEMINI_MIND_MODEL", "").strip() or GEMINI_MODEL
    if mind == "gemini-image":
        return os.environ.get("GEMINI_IMAGE_MODEL", "").strip() or GEMINI_IMAGE_MODEL
    if mind == "meedo":
        return os.environ.get("MEEDO_VISION_MODEL", "").strip()
    raise KeyError(mind)


def available(mind: str) -> bool:
    _keys_loaded()
    if mind == "claude":
        return env_key("ANTHROPIC_API_KEY") is not None
    if mind in ("gemini", "gemini-image"):
        return env_key("GEMINI_API_KEY", "GOOGLE_API_KEY") is not None
    if mind == "meedo":
        return bool(model_of("meedo"))
    return False


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------


def _claude(model: str, images: list[Image.Image], text: str, max_tokens: int) -> str:
    content: list[dict] = []
    for im in images:
        b64, mime = encode_png(im)
        content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}})
    content.append({"type": "text", "text": text})
    data = http_post_json(
        "https://api.anthropic.com/v1/messages",
        {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": content}]},
        headers={"x-api-key": env_key("ANTHROPIC_API_KEY") or "", "anthropic-version": "2023-06-01"},
        timeout=600,
    )
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def _gemini_parts(images: list[Image.Image], text: str) -> list[dict]:
    parts: list[dict] = []
    for im in images:
        b64, mime = encode_png(im)
        parts.append({"inline_data": {"mime_type": mime, "data": b64}})
    parts.append({"text": text})
    return parts


def _gemini(model: str, images: list[Image.Image], text: str, max_tokens: int, *, as_json: bool) -> str:
    cfg: dict = {"maxOutputTokens": max_tokens, "temperature": 0.2}
    if as_json:
        cfg["responseMimeType"] = "application/json"
    data = http_post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {"contents": [{"parts": _gemini_parts(images, text)}], "generationConfig": cfg},
        headers={"x-goog-api-key": env_key("GEMINI_API_KEY", "GOOGLE_API_KEY") or ""},
        timeout=600,
    )
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _gemini_image(model: str, image: Image.Image, text: str) -> tuple[Image.Image | None, str]:
    aspect = image.width / max(1, image.height)
    token = "16:9" if aspect >= 1.6 else "4:3" if aspect >= 1.25 else "3:4" if aspect <= 0.8 else "1:1"
    payload = {"contents": [{"parts": _gemini_parts([image], text)}],
               "generationConfig": {"responseModalities": ["TEXT", "IMAGE"], "temperature": 0.1,
                                    "imageConfig": {"aspectRatio": token}}}
    headers = {"x-goog-api-key": env_key("GEMINI_API_KEY", "GOOGLE_API_KEY") or ""}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    try:
        data = http_post_json(url, payload, headers=headers, timeout=300)
    except RuntimeError:
        payload["generationConfig"].pop("imageConfig")
        data = http_post_json(url, payload, headers=headers, timeout=300)
    note, out = "", None
    for p in (data.get("candidates") or [{}])[0].get("content", {}).get("parts", []):
        blob = p.get("inline_data") or p.get("inlineData")
        if blob and blob.get("data"):
            out = Image.open(io.BytesIO(base64.b64decode(blob["data"]))).convert("RGBA")
        elif p.get("text"):
            note += p["text"]
    return out, note


def _meedo(model: str, images: list[Image.Image], text: str, max_tokens: int) -> str:
    """Meedo-Me's own local model (Ollama), answering in shadow."""
    import urllib.request

    body = {"model": model, "stream": False, "format": "json",
            "options": {"num_predict": max_tokens, "temperature": 0.2},
            "messages": [{"role": "user", "content": text,
                          "images": [encode_png(im)[0] for im in images]}]}
    base = os.environ.get("OLLAMA_HOST", MEEDO_OLLAMA).rstrip("/")
    req = urllib.request.Request(f"{base}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # local: never through a proxy
    with opener.open(req, timeout=600) as r:
        return json.loads(r.read().decode()).get("message", {}).get("content", "")


# --------------------------------------------------------------------------
# asking, and remembering the answer
# --------------------------------------------------------------------------


@dataclass
class Answer:
    mind: str
    model: str
    role: str
    text: str = ""
    parsed: dict = field(default_factory=dict)
    image: Image.Image | None = None
    error: str = ""
    consultation: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def _save_images(images: list[Image.Image], where: Path | None) -> list[Path]:
    """Keep the images a question was about, once each: named by content, so
    the same sketch asked of two minds (or twenty times) is stored once."""
    if where is None:
        return []
    import hashlib

    where.mkdir(parents=True, exist_ok=True)
    out = []
    for im in images:
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        data = buf.getvalue()
        path = where / f"{hashlib.sha1(data).hexdigest()[:16]}.png"
        if not path.exists():
            path.write_bytes(data)
        out.append(path)
    return out


def ask(mind: str, role: str, images: list[Image.Image], text: str, *, case: str = "", session: str = "",
        max_tokens: int = 4000, as_json: bool = True, image_dir: Path | None = None,
        shadow_of: str = "", memory: Path | None = None) -> Answer:
    """Put one question to one mind and keep it in Meedo-Me's memory.
    Never raises: a failed call is an answer with `error`, recorded as such."""
    from .. import meedo_consult as C

    model = model_of(mind)
    ans = Answer(mind=mind, model=model, role=role)
    t0 = time.time()
    try:
        if not available(mind):
            raise RuntimeError(f"{mind} is not configured")
        if mind == "claude":
            ans.text = _claude(model, images, text, max_tokens)
        elif mind == "gemini":
            ans.text = _gemini(model, images, text, max_tokens, as_json=as_json)
        elif mind == "gemini-image":
            ans.image, ans.text = _gemini_image(model, images[0], text)
            if ans.image is None:
                raise RuntimeError(f"no image returned: {ans.text[:200]}")
        elif mind == "meedo":
            ans.text = _meedo(model, images, text, max_tokens)
        else:
            raise KeyError(mind)
        if as_json and ans.text:
            try:
                ans.parsed = extract_json(ans.text)
            except Exception:
                ans.parsed = {}
    except Exception as exc:  # noqa: BLE001 — quota, network, refusal, parse
        ans.error = f"{type(exc).__name__}: {exc}"
    saved = _save_images(images + ([ans.image] if ans.image is not None else []), image_dir)
    try:
        rec = C.record(mind=mind, model=model, role=role, question=text, answer=ans.text, parsed=ans.parsed,
                       case=case, images=saved, session=session, shadow_of=shadow_of,
                       latency_s=time.time() - t0, error=ans.error, path=memory)
        ans.consultation = rec["id"]
    except Exception:
        pass
    return ans


# --------------------------------------------------------------------------
# the roles
# --------------------------------------------------------------------------

CRITIQUE = GOAL + """

IMAGE 1 is the damaged sketch. IMAGE 2 is the engine's restoration of it.
{context}
Judge IMAGE 2 as a designer reviewing a junior's vector redraw. Return JSON only:
{{
  "same_logo": true,
  "missing": ["elements in the sketch that IMAGE 2 lacks, e.g. 'i-dot on Services'"],
  "extra": ["things IMAGE 2 draws that are not part of the design, e.g. 'grey halo around letters'"],
  "geometry": ["lines that should be straight, curves that should be fair, corners that should be sharp"],
  "colour": ["colours that are wrong, washed or missing"],
  "verdict": "ship | redo | takeover",
  "advice": "one sentence: the method that would fix the worst fault",
  "confidence": 0.0
}}
"ship" = a designer would sign it off. "redo" = the engine should try again.
"takeover" = the engine cannot get there; the logo should be drawn afresh."""

TAKEOVER = GOAL + """

IMAGE 1 is the damaged sketch, {w} by {h} pixels. {context}
Draw the logo yourself as a clean SVG: viewBox="0 0 {w} {h}", positioned over
the sketch exactly (same place, same size, same proportions), flat fills only,
no <image>, no filters, no gradients unless the design plainly has one, no
<text> elements (draw letters as paths), background transparent. Use these ink
colours unless the sketch plainly shows others: {palette}.
Build it the way a designer would: geometry first (rectangles, circles, lines,
arcs), fair Bezier curves for letters, shared baselines and cap heights,
consistent stroke weights.
Reply with the SVG only, starting with <svg and ending with </svg>."""

REVISE = GOAL + """

IMAGE 1 is the damaged sketch. IMAGE 2 is your SVG drawing of it, rendered.
Another reviewer said: {critique}
Fix what is right in that critique and ignore what is not; keep everything
that was already right. Same rules: viewBox="0 0 {w} {h}", flat fills, letters
as paths, no <text>, no <image>.
Your previous SVG:
{svg}
Reply with the revised SVG only, starting with <svg and ending with </svg>."""

REDRAW = GOAL + """

Repaint IMAGE 1 as the clean original artwork at high resolution: the same
logo, the same letters, the same layout and proportions, the same brand
colours ({palette}), every element present — only sharp and flat, as if
exported from the designer's file. Plain white background. Do not add,
remove, restyle or re-letter anything."""


def _context(notes: str) -> str:
    return f"What is known so far: {notes}\n" if notes else ""


def critique(mind: str, sketch: Image.Image, output: Image.Image, *, notes: str = "", **kw) -> Answer:
    return ask(mind, "critique", [sketch, output], CRITIQUE.format(context=_context(notes)), **kw)


def _svg_of(text: str) -> str:
    m = re.search(r"<svg\b.*?</svg>", text or "", re.S | re.I)
    return m.group(0) if m else ""


def takeover(mind: str, sketch: Image.Image, *, palette: str, notes: str = "", **kw) -> Answer:
    prompt = TAKEOVER.format(w=sketch.width, h=sketch.height, palette=palette, context=_context(notes))
    ans = ask(mind, "takeover", [sketch], prompt, as_json=False, max_tokens=32000, **kw)
    ans.parsed = {"svg": bool(_svg_of(ans.text))}
    return ans


def revise(mind: str, sketch: Image.Image, render: Image.Image, svg: str, critique_text: str, **kw) -> Answer:
    prompt = REVISE.format(w=sketch.width, h=sketch.height, critique=critique_text, svg=svg)
    ans = ask(mind, "revise", [sketch, render], prompt, as_json=False, max_tokens=32000, **kw)
    ans.parsed = {"svg": bool(_svg_of(ans.text))}
    return ans


def redraw(sketch: Image.Image, *, palette: str, **kw) -> Answer:
    return ask("gemini-image", "redraw", [sketch], REDRAW.format(palette=palette), as_json=False, **kw)


def svg_from(ans: Answer) -> str:
    return _svg_of(ans.text)


def critique_text(ans: Answer) -> str:
    """A critique, as the other mind will read it."""
    p = ans.parsed or {}
    bits = []
    for k in ("missing", "extra", "geometry", "colour"):
        v = p.get(k) or []
        if v:
            bits.append(f"{k}: " + "; ".join(str(x) for x in v))
    if p.get("advice"):
        bits.append(f"advice: {p['advice']}")
    return " | ".join(bits) or (ans.text or "")[:1500]


COMPARE = GOAL + """

IMAGE 1 is the damaged sketch. IMAGE 2 is restoration A. IMAGE 3 is restoration B.
{context}
Which is the better restoration of this logo — the one a designer would sign
off? Judge identity first (every element present, the right letters, the right
colours, nothing invented, the page not drawn), then craftsmanship (straight
lines straight, curves fair, corners sharp, no halos, staircases, blobs or
blur). Return JSON only:
{{"better": "A | B | same", "identity": "which keeps the logo more completely, and why",
  "craft": "which is better drawn, and why", "confidence": 0.0}}"""


def compare(mind: str, sketch: Image.Image, a: Image.Image, b: Image.Image, *, notes: str = "", **kw) -> Answer:
    """Which of two restorations is better. Ask both ways round (a, b) and
    (b, a) to see position bias rather than be fooled by it."""
    return ask(mind, "compare", [sketch, a, b], COMPARE.format(context=_context(notes)), **kw)


def compare_both_ways(mind: str, sketch: Image.Image, first: Image.Image, second: Image.Image, **kw) -> dict:
    """{'pick': 'first'|'second'|'split'|'same'|None, 'answers': [...]}: a pick
    only when both orders agree."""
    ab = compare(mind, sketch, first, second, **kw)
    ba = compare(mind, sketch, second, first, **kw)
    x = str((ab.parsed or {}).get("better", "")).strip().upper()[:1]
    y = str((ba.parsed or {}).get("better", "")).strip().upper()[:1]
    one = {"A": "first", "B": "second", "S": "same"}.get(x)
    two = {"A": "second", "B": "first", "S": "same"}.get(y)
    pick = one if one == two else (None if not one or not two else "split")
    return {"pick": pick, "answers": [ab, ba]}
