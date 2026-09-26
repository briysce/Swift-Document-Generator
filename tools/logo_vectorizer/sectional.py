"""
Sectional (layered) manual-quality vectorization.

Given a raster + a `SectionSet` decomposition, this module traces each
section independently with the manual-quality Bezier fitter and composes
the results into a clean, layered SVG:

    <svg viewBox="0 0 W H">
      <g id="bar-top" ...>
        <path fill="#CE4E30" fill-rule="evenodd" d="..."/>
      </g>
      <g id="swift-shadow" ...>
        <path fill="#111111" fill-rule="evenodd" d="..."/>
      </g>
      ...
    </svg>

Each section can also be exported to its own file for review, and the
overall SVG can be rasterized back to PNG (cairosvg → PyMuPDF; Chrome last-resort).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
from PIL import Image, ImageFilter

from .analyze import RasterAnalysis, analyze_raster
from .manual_trace import ManualTraceConfig, ManualTraceResult, trace_mask
from .sections import Section, SectionSet


@dataclass
class SectionTrace:
    """Result of tracing one section."""

    section: Section
    trace: ManualTraceResult
    trace_config: ManualTraceConfig
    raster_size: tuple[int, int]

    @property
    def combined_d(self) -> str:
        return self.trace.combined_d

    def path_length(self) -> int:
        return len(self.combined_d)


@dataclass
class SectionalResult:
    """Full sectional vectorization output."""

    svg: str
    per_section: list[SectionTrace] = field(default_factory=list)
    analysis: RasterAnalysis | None = None
    source_size: tuple[int, int] = (0, 0)

    def total_anchors(self) -> int:
        """Approximate anchor count = sum of M/L/H/V/C commands."""
        total = 0
        for st in self.per_section:
            d = st.combined_d
            for c in "MLHVCZ":
                total += d.count(c)
        return total


# ---------------------------------------------------------------------------
# Tracing a single section
# ---------------------------------------------------------------------------

def _upscale_mask(mask: np.ndarray, factor: int, blur_radius: float, threshold: int) -> np.ndarray:
    """Upscale a binary mask with LANCZOS + blur + rethreshold for subpixel edges."""
    if factor <= 1 and blur_radius == 0:
        return mask
    src = Image.fromarray(mask)
    if factor > 1:
        src = src.resize(
            (src.width * factor, src.height * factor),
            Image.Resampling.LANCZOS,
        )
    if blur_radius > 0:
        src = src.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    arr = np.array(src)
    return (arr >= threshold).astype(np.uint8) * 255


def trace_section(
    section: Section,
    analysis: RasterAnalysis,
    *,
    override_cfg: ManualTraceConfig | None = None,
) -> SectionTrace:
    """
    Trace one section with parameters recommended by *analysis*.

    The section's mask is upscaled + blurred + rethresholded before tracing
    so contour points sit on sub-pixel positions — same trick a designer
    uses when zooming in before dropping anchors.
    """
    factor = analysis.recommended_upscale
    blur = analysis.recommended_blur
    thr = analysis.recommended_threshold
    upscaled = _upscale_mask(section.mask, factor, blur, thr)

    cfg = override_cfg or analysis.to_manual_config()
    # Override output scale to match the actual factor we used.
    cfg = ManualTraceConfig(
        corner_angle_deg=cfg.corner_angle_deg,
        max_error_px=cfg.max_error_px,
        straight_dev_px=cfg.straight_dev_px,
        axis_snap_deg=cfg.axis_snap_deg,
        smooth_sigma=cfg.smooth_sigma,
        max_arc_between_anchors=cfg.max_arc_between_anchors,
        min_area=cfg.min_area,
        output_scale=float(factor),
        coord_precision=cfg.coord_precision,
        corner_window=cfg.corner_window,
        max_bezier_recursion=cfg.max_bezier_recursion,
    )
    result = trace_mask(upscaled, cfg)
    h, w = section.mask.shape[:2]
    return SectionTrace(
        section=section,
        trace=result,
        trace_config=cfg,
        raster_size=(w, h),
    )


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

def compose_sectional_svg(
    traces: Iterable[SectionTrace], *, source_size: tuple[int, int]
) -> str:
    """Wrap per-section paths into a single layered SVG."""
    w, h = source_size
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'style="background:transparent">'
    ]

    traces_list = list(traces)
    # Render in z-order (lower first = drawn underneath).
    for st in sorted(traces_list, key=lambda t: t.section.z_index):
        d = st.combined_d
        if not d:
            continue
        parts.append(
            f'<g id="{st.section.name}">'
            f'<path fill="{st.section.fill_hex}" fill-rule="evenodd" d="{d}"/>'
            f'</g>'
        )
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Top-level pipeline
# ---------------------------------------------------------------------------

def vectorize_sectional(
    img: Image.Image,
    sections: SectionSet,
    *,
    analysis: RasterAnalysis | None = None,
) -> SectionalResult:
    """Analyze the raster, trace each section, and compose a layered SVG."""
    analysis = analysis or analyze_raster(img)
    per_section: list[SectionTrace] = []
    for section in sections.trace_order:
        if not section.mask.any():
            continue
        per_section.append(trace_section(section, analysis))
    w, h = img.size
    svg = compose_sectional_svg(per_section, source_size=(w, h))
    return SectionalResult(
        svg=svg,
        per_section=per_section,
        analysis=analysis,
        source_size=(w, h),
    )


def vectorize_sectional_file(
    input_png: Path,
    output_svg: Path,
    *,
    sections: SectionSet | None = None,
    per_section_dir: Path | None = None,
) -> SectionalResult:
    """Convenience wrapper: read PNG, run sectional trace, write SVG."""
    from .sections import decompose_swift_supply

    img = Image.open(input_png).convert("RGBA")
    sections = sections or decompose_swift_supply(img)
    result = vectorize_sectional(img, sections)
    output_svg.parent.mkdir(parents=True, exist_ok=True)
    output_svg.write_text(result.svg, encoding="utf-8")

    if per_section_dir is not None:
        per_section_dir.mkdir(parents=True, exist_ok=True)
        for st in result.per_section:
            single_svg = compose_sectional_svg([st], source_size=result.source_size)
            (per_section_dir / f"{st.section.name}.svg").write_text(
                single_svg, encoding="utf-8"
            )
    return result


# ---------------------------------------------------------------------------
# SVG -> PNG rasterization
# ---------------------------------------------------------------------------

def _render_svg_pymupdf(
    svg_path: Path,
    png_path: Path,
    *,
    width: int | None = None,
) -> Path:
    """Cross-platform SVG→PNG via PyMuPDF (no Chrome / cairo required)."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz  # type: ignore

    doc = fitz.open(str(svg_path))
    try:
        page = doc[0]
        rect = page.rect
        if rect.width <= 0 or rect.height <= 0:
            raise RuntimeError("empty SVG page")
        if width is None:
            zoom = 1.0
        else:
            zoom = float(width) / float(rect.width)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=True)
        png_path.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(png_path))
    finally:
        doc.close()
    if not png_path.is_file() or png_path.stat().st_size <= 0:
        raise RuntimeError("pymupdf wrote empty PNG")
    # Normalize to RGBA via Pillow.
    Image.open(png_path).convert("RGBA").save(png_path)
    return png_path


def _find_chrome() -> Path | None:
    # Prefer real binaries over PATH wrappers. Cursor cloud images wrap
    # /usr/local/bin/google-chrome with a fixed --user-data-dir +
    # --remote-debugging-port that makes headless --screenshot hang.
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path("/opt/google/chrome/chrome"),
        Path("/usr/bin/google-chrome-stable"),
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/chromium"),
        Path("/usr/bin/chromium-browser"),
        Path("/snap/bin/chromium"),
        Path("/usr/local/bin/chromium"),
        # Wrappers last (may inject conflicting flags).
        Path("/usr/local/bin/google-chrome"),
        Path("/usr/local/bin/chrome"),
    ]
    for c in candidates:
        if c.is_file():
            return c
    import shutil

    for name in (
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "google-chrome",
        "chrome",
    ):
        hit = shutil.which(name)
        if hit:
            return Path(hit)
    return None


def rasterize_svg(
    svg_path: Path,
    png_path: Path,
    *,
    width: int | None = None,
    background: str = "white",
    prefer_chrome: bool = False,
) -> Path:
    """
    Render an SVG to PNG.

    Default order (portable, no Chrome dependency):
        cairosvg → PyMuPDF → optional headless Chrome last resort.

    Chrome is intentionally not preferred: cloud/CI wrappers hang, and the
    approved Swift restore mean (0.9269) was proven with cairosvg. Pass
    prefer_chrome=True only for explicit experiments. Set LOGO_NO_CHROME=1
    to skip Chrome entirely.
    """
    import os

    png_path.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    # Optional experimental Chrome-first path (off by default).
    if prefer_chrome and os.environ.get("LOGO_NO_CHROME", "").strip().lower() not in {
        "1", "true", "yes",
    }:
        chrome_exe = _find_chrome()
        if chrome_exe is not None:
            try:
                if background == "transparent":
                    return _render_svg_transparent_via_chrome(
                        svg_path, png_path, width=width
                    )
                return _render_svg_chrome_html(
                    svg_path, png_path, width=width, background=background
                )
            except Exception as e:
                errors.append(f"chrome: {e}")

    if background == "transparent":
        try:
            import cairosvg  # type: ignore

            kwargs: dict[str, object] = {
                "url": str(svg_path),
                "write_to": str(png_path),
            }
            if width is not None:
                kwargs["output_width"] = width
            cairosvg.svg2png(**kwargs)  # type: ignore[arg-type]
            return png_path
        except Exception as e:
            errors.append(f"cairosvg: {e}")

    try:
        return _render_svg_pymupdf(svg_path, png_path, width=width)
    except Exception as e:
        errors.append(f"pymupdf: {e}")

    if os.environ.get("LOGO_NO_CHROME", "").strip().lower() not in {"1", "true", "yes"}:
        chrome_exe = _find_chrome()
        if chrome_exe is not None:
            try:
                if background == "transparent":
                    return _render_svg_transparent_via_chrome(
                        svg_path, png_path, width=width
                    )
                return _render_svg_chrome_html(
                    svg_path, png_path, width=width, background=background
                )
            except Exception as e:
                errors.append(f"chrome: {e}")

    raise RuntimeError("SVG rasterize failed (" + "; ".join(errors) + ")")


def _render_svg_transparent_via_chrome(
    svg_path: Path, png_path: Path, *, width: int | None
) -> Path:
    """Render on white + black then extract alpha from the difference."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpd:
        w_path = Path(tmpd) / "w.png"
        b_path = Path(tmpd) / "b.png"
        _render_svg_chrome_html(svg_path, w_path, width=width, background="#FFFFFF")
        _render_svg_chrome_html(svg_path, b_path, width=width, background="#000000")
        # High-zoom renders can exceed PIL's default 178 MP safety limit
        # (e.g. 15000×15000). We produced these images ourselves so trust
        # them and disable the DoS guard.
        prev_limit = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = None
        try:
            white_img = Image.open(w_path).convert("RGB")
            black_img = Image.open(b_path).convert("RGB")
        finally:
            Image.MAX_IMAGE_PIXELS = prev_limit
        w_arr = np.array(white_img, dtype=np.int32)
        b_arr = np.array(black_img, dtype=np.int32)
        # a = 255 - (white_r - black_r)  (identical across channels)
        alpha = 255 - np.clip(w_arr[..., 0] - b_arr[..., 0], 0, 255)
        alpha = np.clip(alpha, 0, 255).astype(np.uint8)
        # Recover source RGB (associated / premultiplied by alpha in black_img).
        color = np.zeros_like(w_arr, dtype=np.uint8)
        with np.errstate(divide="ignore", invalid="ignore"):
            for c in range(3):
                color[..., c] = np.where(
                    alpha > 0,
                    np.clip(b_arr[..., c] * 255 / np.maximum(alpha, 1), 0, 255).astype(np.uint8),
                    0,
                )
        rgba = np.dstack([color, alpha])
        Image.fromarray(rgba, mode="RGBA").save(png_path, optimize=True)
    return png_path


def _render_svg_chrome_html(
    svg_path: Path,
    png_path: Path,
    *,
    width: int | None,
    background: str,
) -> Path:
    """Chrome-headless SVG render with an explicit CSS background color."""
    import subprocess
    import tempfile

    svg = svg_path.read_text(encoding="utf-8")
    import re

    m_w = re.search(r'width="(\d+(?:\.\d+)?)"', svg)
    m_h = re.search(r'height="(\d+(?:\.\d+)?)"', svg)
    svg_w = float(m_w.group(1)) if m_w else 1024.0
    svg_h = float(m_h.group(1)) if m_h else svg_w
    if width is None:
        target_w = int(svg_w)
        target_h = int(svg_h)
    else:
        target_w = int(width)
        target_h = int(round(width * svg_h / svg_w))

    body_bg = background if background != "transparent" else "#000"
    chrome_exe = _find_chrome()
    if chrome_exe is None:
        raise FileNotFoundError("Chrome/Chromium not found on PATH or known locations")

    # Force the SVG to fill the rendered viewport (Chrome would otherwise use
    # the SVG's declared pixel width, which is the raster's native size).
    svg_scaled = re.sub(r'width="[^"]+"', f'width="{target_w}"', svg, count=1)
    svg_scaled = re.sub(
        r'height="[^"]+"', f'height="{target_h}"', svg_scaled, count=1
    )

    with tempfile.TemporaryDirectory(prefix="swift_chrome_") as udir:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".html", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(
                "<!DOCTYPE html><html><body style='margin:0;background:"
                f"{body_bg}'>{svg_scaled}</body></html>"
            )
            html_path = Path(tmp.name)
        try:
            # Cloud / container Linux needs no-sandbox + private user-data-dir
            # or headless Chrome hangs / refuses DevTools. virtual-time-budget
            # keeps SVG screenshots from waiting on idle network timers.
            subprocess.run(
                [
                    str(chrome_exe),
                    "--headless=new",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    "--no-default-browser-check",
                    f"--user-data-dir={udir}",
                    "--virtual-time-budget=5000",
                    "--hide-scrollbars",
                    f"--window-size={target_w},{target_h}",
                    f"--screenshot={png_path.resolve()}",
                    html_path.as_uri(),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        finally:
            html_path.unlink(missing_ok=True)
    if not png_path.is_file() or png_path.stat().st_size <= 0:
        raise RuntimeError("chrome wrote empty PNG")
    return png_path
