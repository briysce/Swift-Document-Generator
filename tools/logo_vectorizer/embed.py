"""Write self-contained SVGs that embed a PNG via base64 <image>."""

from __future__ import annotations

import base64
from pathlib import Path

from PIL import Image


def write_embedded_png_svg(png_path: Path, svg_path: Path) -> tuple[int, int]:
    """Embed *png_path* in *svg_path* as a pixel-perfect, self-contained SVG."""
    png_path = Path(png_path)
    svg_path = Path(svg_path)

    with Image.open(png_path) as img:
        width, height = img.size

    b64 = base64.standard_b64encode(png_path.read_bytes()).decode("ascii")
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'style="background:transparent">'
        f'<image width="{width}" height="{height}" '
        f'href="data:image/png;base64,{b64}"/>'
        f"</svg>"
    )
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path.write_text(svg, encoding="utf-8")
    return width, height
