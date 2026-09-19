"""QA helpers: render SVG and verify SUPPLY P counters stay open."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image


def supply_p_ranges(img: Image.Image) -> list[tuple[int, int]]:
    """X-ranges of SUPPLY letter columns from alpha band."""
    h = img.height
    y0, y1 = int(h * 0.62), int(h * 0.92)
    band = np.array(img.convert("RGBA"))[y0:y1, :, 3]
    col = band.sum(axis=0)
    if col.max() == 0:
        return []
    thr = col.max() * 0.15
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for x, active in enumerate(col > thr):
        if active and start is None:
            start = x
        elif not active and start is not None:
            if x - start > 30:
                runs.append((start, x - 1))
            start = None
    if start is not None and img.width - start > 30:
        runs.append((start, img.width - 1))
    return runs


def _svg_dimensions(svg: str, width: int | None = None) -> tuple[int, int]:
    import re

    m_w = re.search(r'width="(\d+(?:\.\d+)?)"', svg)
    m_h = re.search(r'height="(\d+(?:\.\d+)?)"', svg)
    svg_w = int(float(m_w.group(1))) if m_w else 2987
    svg_h = int(float(m_h.group(1))) if m_h else max(200, int(svg_w * 0.35))
    if width is None:
        return svg_w, svg_h
    scale = width / svg_w if svg_w else 1.0
    return width, max(1, round(svg_h * scale))


def render_svg_chrome(
    svg_path: Path,
    png_path: Path,
    *,
    width: int | None = None,
    height: int | None = None,
    chrome: Path | None = None,
) -> None:
    """Headless Chrome screenshot of SVG on black background."""
    svg = svg_path.read_text(encoding="utf-8")
    if width is None and height is None:
        width, height = _svg_dimensions(svg)
    elif width is not None and height is None:
        width, height = _svg_dimensions(svg, width=width)
    elif height is not None and width is None:
        svg_w, svg_h = _svg_dimensions(svg)
        width = max(1, round(svg_w * (height / svg_h))) if svg_h else height
    else:
        width = int(width)  # type: ignore[arg-type]
        height = int(height)  # type: ignore[arg-type]

    chrome_exe = chrome or Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    if not chrome_exe.is_file():
        raise FileNotFoundError(f"Chrome not found: {chrome_exe}")

    png_path = png_path.resolve()

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tmp:
        tmp.write(
            "<!DOCTYPE html><html><body style='margin:0;background:#000'>"
            f"{svg}</body></html>"
        )
        html_path = Path(tmp.name)

    try:
        result = subprocess.run(
            [
                str(chrome_exe),
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                f"--window-size={width},{height}",
                f"--screenshot={png_path}",
                html_path.as_uri(),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if not png_path.is_file():
            raise RuntimeError(result.stderr or "Chrome did not write screenshot")
    finally:
        html_path.unlink(missing_ok=True)


def render_svg_cairosvg(svg_path: Path, png_path: Path, *, width: int | None = None) -> None:
    import cairosvg

    kwargs: dict[str, object] = {"url": str(svg_path), "write_to": str(png_path)}
    if width is not None:
        kwargs["output_width"] = width
    cairosvg.svg2png(**kwargs)  # type: ignore[arg-type]


def counter_hollow_score(
    rendered: Image.Image,
    letter_range: tuple[int, int],
    *,
    is_orange: bool = True,
) -> tuple[float, int, int]:
    """
    Score P counter hollowness in a letter column.

    Returns (hollow_ratio, hollow_pixels, total_interior_pixels).
    Interior = middle 40% x, upper 55% y of letter bbox (P bowl / eye).
    Hollow = dark background visible (low alpha or non-orange).
    """
    arr = np.array(rendered.convert("RGBA"))
    h, w = arr.shape[:2]
    y0, y1 = int(h * 0.62), int(h * 0.92)
    x0, x1 = letter_range
    width = x1 - x0
    ix0 = x0 + int(width * 0.25)
    ix1 = x0 + int(width * 0.75)
    iy0 = y0 + int((y1 - y0) * 0.15)
    iy1 = y0 + int((y1 - y0) * 0.55)

    region = arr[iy0:iy1, ix0:ix1]
    if region.size == 0:
        return 0.0, 0, 0

    total = region.shape[0] * region.shape[1]
    r, g, b, a = region[:, :, 0], region[:, :, 1], region[:, :, 2], region[:, :, 3]

    if is_orange:
        # Orange fill ~ (206, 78, 48)
        is_fill = (a > 64) & (r > 150) & (g < 120) & (b < 100)
        hollow = ~is_fill
    else:
        # White fill on black bg — counter shows as black/dark, not white letterform
        is_fill = (a > 64) & (r > 200) & (g > 200) & (b > 200)
        hollow = ~is_fill

    hollow_count = int(hollow.sum())
    return hollow_count / total, hollow_count, total


def verify_p_counters(
    svg_path: Path,
    ref_png: Path,
    *,
    min_hollow_ratio: float = 0.35,
    qa_crop_path: Path | None = None,
    render_path: Path | None = None,
) -> dict[str, object]:
    """Render SVG and verify both SUPPLY P letters have open counters."""
    ref = Image.open(ref_png).convert("RGBA")
    runs = supply_p_ranges(ref)
    if len(runs) < 3:
        return {"ok": False, "reason": f"expected >=3 SUPPLY letters, got {len(runs)}"}

    p_indices = [2, 3]  # SUPPLY: S-U-P-P-L-Y → P at index 2 and 3
    p_ranges = [runs[i] for i in p_indices if i < len(runs)]

    tmp_render = (render_path or svg_path.with_suffix(".qa_render.png")).resolve()
    rendered_ok = False
    try:
        render_svg_chrome(svg_path, tmp_render, width=ref.width, height=ref.height)
        rendered_ok = tmp_render.is_file()
    except (FileNotFoundError, subprocess.CalledProcessError):
        rendered_ok = False
    if not rendered_ok:
        try:
            render_svg_cairosvg(svg_path, tmp_render, width=ref.width)
            rendered_ok = tmp_render.is_file()
        except OSError:
            rendered_ok = False
    if not rendered_ok:
        return {"ok": False, "reason": "could not render SVG for QA"}

    rendered = Image.open(tmp_render).convert("RGBA")
    is_orange = "orange" in ref_png.name.lower()

    scores: list[dict[str, object]] = []
    all_ok = True
    for idx, p_range in zip(p_indices, p_ranges):
        ratio, hollow, total = counter_hollow_score(rendered, p_range, is_orange=is_orange)
        ok = ratio >= min_hollow_ratio
        all_ok = all_ok and ok
        scores.append(
            {"letter_index": idx, "range": p_range, "hollow_ratio": ratio, "ok": ok}
        )

    if qa_crop_path and len(p_ranges) >= 1:
        qa_crop_path.parent.mkdir(parents=True, exist_ok=True)
        x0, x1 = p_ranges[0]
        pad = 40
        y0, y1 = int(ref.height * 0.58), int(ref.height * 0.94)
        crop = rendered.crop(
            (max(0, x0 - pad), y0, min(rendered.width, x1 + pad), y1)
        )
        # Side-by-side with reference crop
        ref_crop = ref.crop((max(0, x0 - pad), y0, min(ref.width, x1 + pad), y1))
        combo = Image.new("RGBA", (crop.width + ref_crop.width + 8, max(crop.height, ref_crop.height)))
        combo.paste(ref_crop, (0, 0))
        combo.paste(rendered.crop((max(0, x0 - pad), y0, min(rendered.width, x1 + pad), y1)), (ref_crop.width + 8, 0))
        combo.save(qa_crop_path)

    return {
        "ok": all_ok,
        "scores": scores,
        "render_path": str(tmp_render),
        "letter_count": len(runs),
    }
