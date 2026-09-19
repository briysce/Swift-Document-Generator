"""Optional Inkscape Trace Bitmap backend."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image


def find_inkscape() -> Path | None:
    for name in ("inkscape", "inkscape.exe"):
        found = shutil.which(name)
        if found:
            return Path(found)
    for candidate in (
        Path(r"C:\Program Files\Inkscape\bin\inkscape.exe"),
        Path(r"C:\Program Files\Inkscape\inkscape.exe"),
    ):
        if candidate.is_file():
            return candidate
    return None


def _postprocess_inkscape_svg(
    svg: str,
    fill_hex: str,
    source_size: tuple[int, int],
    trace_size: tuple[int, int],
) -> str:
    trace_w, trace_h = trace_size
    src_w, src_h = source_size
    scale = src_w / trace_w if trace_w else 1.0

    # Force evenodd on all paths and unify fill
    def fix_path(match: re.Match[str]) -> str:
        tag = match.group(0)
        if 'fill-rule="' not in tag:
            tag = tag.replace("<path ", '<path fill-rule="evenodd" ', 1)
        tag = re.sub(r'fill="[^"]*"', f'fill="{fill_hex}"', tag)
        if 'fill="' not in tag:
            tag = tag.replace("<path ", f'<path fill="{fill_hex}" ', 1)
        return tag

    svg = re.sub(r"<path\s[^>]+/>", fix_path, svg)
    svg = re.sub(r"<path\s[^>]+>", fix_path, svg)

    if scale != 1.0:
        svg = re.sub(
            rf'width="{trace_w}"\s+height="{trace_h}"',
            f'width="{src_w}" height="{src_h}"',
            svg,
            count=1,
        )
        if 'viewBox="' not in svg:
            svg = svg.replace(
                "<svg ",
                f'<svg viewBox="0 0 {trace_w} {trace_h}" ',
                1,
            )

    if 'style="background:' not in svg:
        svg = svg.replace("<svg ", '<svg style="background:transparent" ', 1)
    return svg


def trace_with_inkscape(
    binary: Image.Image,
    trace_size: tuple[int, int],
    source_size: tuple[int, int],
    fill_hex: str,
) -> str | None:
    inkscape = find_inkscape()
    if inkscape is None:
        return None

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        bmp_path = tmp / "trace.png"
        svg_path = tmp / "trace.svg"
        binary.save(bmp_path)
        actions = "select-all;selection-trace;export-type:svg;export-filename:trace.svg"
        result = subprocess.run(
            [str(inkscape), str(bmp_path), f"--actions={actions}", "--batch-process"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0 or not svg_path.is_file():
            return None
        raw = svg_path.read_text(encoding="utf-8")
    return _postprocess_inkscape_svg(raw, fill_hex, source_size, trace_size)
