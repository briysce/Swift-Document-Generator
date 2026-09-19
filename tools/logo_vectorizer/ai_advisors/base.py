"""Shared types and helpers for vision AI advisors."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from PIL import Image


@dataclass
class SourceHints:
    """Structured hints from pre-trace vision analysis."""

    has_holes: bool = True
    hole_descriptions: list[str] = field(default_factory=list)
    letter_regions: list[dict[str, Any]] = field(default_factory=list)
    recommended_backends: list[str] = field(default_factory=lambda: ["opencv-tree", "potrace"])
    recommended_preprocess: dict[str, float | int] = field(default_factory=dict)
    straight_vs_curve: str = ""
    notes: str = ""
    brand_name: str = ""
    font_family_guess: str = ""
    dominant_colors_hex: list[str] = field(default_factory=list)
    layout_summary: str = ""
    provider: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any], provider: str = "") -> SourceHints:
        preprocess = data.get("recommended_preprocess") or {}
        if not isinstance(preprocess, dict):
            preprocess = {}
        backends = data.get("recommended_backends") or []
        if not isinstance(backends, list):
            backends = []
        holes = data.get("hole_descriptions") or []
        if not isinstance(holes, list):
            holes = []
        regions = data.get("letter_regions") or []
        if not isinstance(regions, list):
            regions = []
        colors = data.get("dominant_colors_hex") or data.get("brand_colors_hex") or []
        if not isinstance(colors, list):
            colors = []
        return cls(
            has_holes=bool(data.get("has_holes", True)),
            hole_descriptions=[str(x) for x in holes],
            letter_regions=regions,
            recommended_backends=[str(x) for x in backends],
            recommended_preprocess=preprocess,
            straight_vs_curve=str(data.get("straight_vs_curve", "")),
            notes=str(data.get("notes", "")),
            brand_name=str(data.get("brand_name", "") or ""),
            font_family_guess=str(
                data.get("font_family_guess", "") or data.get("font_family", "") or ""
            ),
            dominant_colors_hex=[str(x) for x in colors if str(x).strip()],
            layout_summary=str(data.get("layout_summary", "") or ""),
            provider=provider,
            raw=data,
        )

    def merged(self, other: SourceHints) -> SourceHints:
        """Combine hints from multiple providers (union backends, OR has_holes)."""
        backends = list(dict.fromkeys(self.recommended_backends + other.recommended_backends))
        preprocess = self.recommended_preprocess or other.recommended_preprocess
        colors = list(dict.fromkeys(self.dominant_colors_hex + other.dominant_colors_hex))
        return SourceHints(
            has_holes=self.has_holes or other.has_holes,
            hole_descriptions=list(dict.fromkeys(self.hole_descriptions + other.hole_descriptions)),
            letter_regions=self.letter_regions or other.letter_regions,
            recommended_backends=backends,
            recommended_preprocess=preprocess,
            straight_vs_curve=self.straight_vs_curve or other.straight_vs_curve,
            notes=" | ".join(filter(None, [self.notes, other.notes])),
            brand_name=self.brand_name or other.brand_name,
            font_family_guess=self.font_family_guess or other.font_family_guess,
            dominant_colors_hex=colors,
            layout_summary=self.layout_summary or other.layout_summary,
            provider=f"{self.provider},{other.provider}".strip(","),
            raw={"a": self.raw, "b": other.raw},
        )


@dataclass
class CritiqueResult:
    """Post-score vision critique of a traced candidate."""

    passes_qa: bool = True
    missing_counters: bool = False
    jagged_regions: bool = False
    confidence: float = 0.5
    issues: list[str] = field(default_factory=list)
    reject_candidate: bool = False
    provider: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any], provider: str = "") -> CritiqueResult:
        issues = data.get("issues") or []
        if not isinstance(issues, list):
            issues = []
        return cls(
            passes_qa=bool(data.get("passes_qa", True)),
            missing_counters=bool(data.get("missing_counters", False)),
            jagged_regions=bool(data.get("jagged_regions", False)),
            confidence=float(data.get("confidence", 0.5)),
            issues=[str(x) for x in issues],
            reject_candidate=bool(data.get("reject_candidate", False)),
            provider=provider,
            raw=data,
        )

    def merged(self, others: list[CritiqueResult]) -> CritiqueResult:
        all_r = [self, *others]
        reject = any(r.reject_candidate or r.missing_counters for r in all_r)
        jagged = any(r.jagged_regions for r in all_r)
        passes = all(r.passes_qa for r in all_r) and not reject
        conf = sum(r.confidence for r in all_r) / len(all_r)
        issues: list[str] = []
        for r in all_r:
            issues.extend(r.issues)
        return CritiqueResult(
            passes_qa=passes,
            missing_counters=any(r.missing_counters for r in all_r),
            jagged_regions=jagged,
            confidence=conf,
            issues=list(dict.fromkeys(issues)),
            reject_candidate=reject,
            provider=",".join(r.provider for r in all_r if r.provider),
        )


def encode_png(img: Image.Image, max_width: int = 1600) -> tuple[str, str]:
    """Return (base64, mime) for vision APIs; downscale large images."""
    work = img.convert("RGBA")
    if work.width > max_width:
        scale = max_width / work.width
        work = work.resize(
            (max_width, max(1, round(work.height * scale))),
            Image.Resampling.LANCZOS,
        )
    buf = io.BytesIO()
    work.save(buf, format="PNG", optimize=True)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii"), "image/png"


def extract_json(text: str) -> dict[str, Any]:
    """Parse JSON from model output, tolerating markdown fences."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def http_post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int = 90,
    *,
    max_retries: int = 3,
) -> dict[str, Any]:
    """POST JSON with retries on rate-limit (429) and transient 5xx errors."""
    body = json.dumps(payload).encode("utf-8")
    last_err: Exception | None = None
    for attempt in range(max_retries):
        req = urllib.request.Request(
            url,
            data=body,
            headers={**headers, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            last_err = RuntimeError(f"HTTP {exc.code}: {detail}")
            if exc.code in (429, 500, 502, 503, 504) and attempt + 1 < max_retries:
                # Honor Retry-After when present; otherwise exponential backoff.
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay = float(retry_after) if retry_after else (1.5 * (2**attempt))
                except ValueError:
                    delay = 1.5 * (2**attempt)
                delay = min(max(delay, 0.5), 20.0)
                import time

                time.sleep(delay)
                continue
            raise last_err from exc
        except Exception as exc:  # noqa: BLE001 — network blips
            last_err = exc
            if attempt + 1 < max_retries:
                import time

                time.sleep(1.5 * (2**attempt))
                continue
            raise
    raise last_err or RuntimeError("http_post_json failed")


def env_key(*names: str) -> str | None:
    for name in names:
        val = os.environ.get(name, "").strip()
        if val:
            return val
    return None
