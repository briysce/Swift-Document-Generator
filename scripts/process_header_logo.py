"""Convert Swift Supply logo to white-on-transparent for the app header and brand exports."""

from __future__ import annotations

import io
import sys
from collections import deque
from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.logo_vectorizer.ai_advisors import AIConfig  # noqa: E402
from tools.logo_vectorizer.ai_advisors.orchestrator import resolve_providers  # noqa: E402
from tools.logo_vectorizer.embed import write_embedded_png_svg  # noqa: E402
from tools.logo_vectorizer.ensemble import vectorize_ensemble  # noqa: E402
from tools.logo_vectorizer.env_loader import gemini_configured, load_env  # noqa: E402
from tools.logo_vectorizer.qa import verify_p_counters  # noqa: E402
from tools.logo_vectorizer.sectional import (  # noqa: E402
    rasterize_svg,
    vectorize_sectional,
)
from tools.logo_vectorizer.sections import decompose_swift_supply  # noqa: E402
from tools.logo_vectorizer.two_stage import run_two_stage  # noqa: E402

DEFAULT_SRC = (
    Path.home()
    / ".cursor/projects/c-Users-Brice-OneDrive-Documents-swift-document-generator/assets"
    / "c__Users_Brice_AppData_Roaming_Cursor_User_workspaceStorage_empty-window_images_Picture1-8286020e-676c-4f45-9b4c-ebf312b31051.png"
)
HI_RES_SRC = ROOT / "mobile" / "assets" / "images" / "swift_supply_logo.png"
FALLBACK_SRC = ROOT / "customer_logos" / "swift_supply_logo_opt.png"
OUT = ROOT / "mobile" / "assets" / "images" / "swift_supply_header_white.png"
BRAND_DIR = ROOT / "assets" / "brand"

# 3× the original 743 px header asset — crisp when scaled down in Flutter.
TARGET_WIDTH = 2229
BRAND_TARGET_WIDTH = 3000
PREVIEW_WIDTH = 1200

# App accent from mobile/lib/theme.dart / ShippingLabelPdf.swift
APP_ORANGE_HEX = "#CE4E30"
APP_ORANGE_RGB = (0xCE, 0x4E, 0x30)

# SWIFT drop shadow sits down-right of the orange letter cores.
SHADOW_DX = (2, 18)
SHADOW_DY = (2, 18)

def _two_stage_brand_svg(png_path: Path, dest: Path, fill_hex: str) -> str:
    """Stage A embed + Stage B geometric polish; fallback to embed when QA fails."""
    qa_crop = BRAND_DIR / f"{dest.stem}_qa_p_crop.png"
    result = run_two_stage(
        png_path,
        dest,
        fill_hex=fill_hex,
        qa=True,
        qa_crop_path=qa_crop,
    )
    sc = result.score
    iou = sc.alpha_iou if sc else 0.0
    p_h = sc.p_hollow if sc else 0.0
    rq = result.roundness_qa or {}
    print(
        f"  two-stage/{result.stage} "
        f"QA={'PASS' if result.passed_qa else 'FAIL'} "
        f"iou={iou:.3f} p_hollow={p_h:.3f} "
        f"holes={result.hole_count} "
        f"peri_drop={rq.get('perimeter_drop', 0):.4f} "
        f"circ_gain={rq.get('circularity_gain', 0):.4f}"
    )
    if not result.passed_qa:
        print("  WARN: polish QA failed — kept embed-PNG SVG")
    return f"two-stage/{result.stage}"


def _trace_mask_to_svg(img: Image.Image, dest: Path, fill_hex: str) -> str:
    """Ensemble vector trace with P-counter QA; AI advisors when keys present."""
    ref_png = dest.with_suffix(".png")
    qa_crop = BRAND_DIR / f"{dest.stem}_qa_p_crop.png"
    is_orange = fill_hex.upper() == APP_ORANGE_HEX

    load_env()
    providers = resolve_providers(["gemini", "claude", "openai"])
    # AI is additive; disable when no providers or AI_ADVISORS=0
    import os

    ai_enabled = bool(providers) and os.environ.get("AI_ADVISORS", "1") != "0"
    ai_cfg = AIConfig(enabled=ai_enabled, providers=providers or ["gemini"])

    result = vectorize_ensemble(img, fill_hex, is_orange=is_orange, ai=ai_cfg)
    dest.write_text(result.svg, encoding="utf-8")
    sc = result.score
    print(
        f"  ensemble/{result.method.split('/')[-1]} "
        f"score={sc.total:.3f} holes={result.hole_count} "
        f"({result.candidates_tried} candidates, {result.preprocess.key()})"
    )

    if ref_png.is_file():
        report = verify_p_counters(dest, ref_png, qa_crop_path=qa_crop)
        if not report.get("ok"):
            raise RuntimeError(f"P-counter QA failed: {report}")
        print(f"  QA P counters OK: {report['scores']}")

    return result.method


def _pick_source(explicit: Path | None) -> Path:
    if explicit and explicit.is_file():
        return explicit
    candidates = [DEFAULT_SRC, HI_RES_SRC, FALLBACK_SRC]
    return max(
        (p for p in candidates if p.is_file()),
        key=lambda p: Image.open(p).size[0],
        default=DEFAULT_SRC,
    )


def _is_background(r: int, g: int, b: int) -> bool:
    return r > 232 and g > 232 and b > 232


def _is_orange(r: int, g: int, b: int) -> bool:
    return r > 130 and g > 40 and b < 150 and r > g and r > b + 25


def _is_dark(r: int, g: int, b: int) -> bool:
    return max(r, g, b) < 110


def _logo_strength(r: int, g: int, b: int, a: int) -> float:
    """0–1 score: how strongly this pixel belongs to the logo mark."""
    if a < 8 or _is_background(r, g, b):
        return 0.0
    if _is_orange(r, g, b):
        sat = min(1.0, (r - max(g, b)) / 100.0)
        lum = min(1.0, max(r, g, b) / 255.0)
        return 0.55 + 0.45 * sat * lum
    if _is_dark(r, g, b):
        # SUPPLY wordmark and SWIFT outlines — solid black letterforms.
        lum = 1.0 - max(r, g, b) / 110.0
        return 0.75 + 0.25 * lum
    # Anti-aliased fringe between orange letterform and white background.
    if r > g and r > b and max(r, g, b) > 90:
        edge = (r - max(g, b)) / 140.0
        if edge > 0.08:
            return min(0.85, edge)
    return 0.0


def _find_supply_rows(
    px, w: int, h: int, letter: list[list[bool]]
) -> tuple[int, int] | None:
    """Rows where SUPPLY lives: mostly dark, little orange (not SWIFT/shadow)."""
    best_start = best_end = 0
    best_score = 0
    run_start: int | None = None
    for y in range(h):
        dark = orange = 0
        for x in range(w):
            r, g, b, a = px[x, y]
            if a < 16 or _is_background(r, g, b):
                continue
            if _is_dark(r, g, b):
                dark += 1
            elif letter[y][x]:
                orange += 1
        is_supply_row = dark >= 120 and orange <= dark // 4
        if is_supply_row:
            if run_start is None:
                run_start = y
        elif run_start is not None:
            run_len = y - run_start
            if run_len > best_score and run_start > h // 5:
                best_score = run_len
                best_start, best_end = run_start, y - 1
            run_start = None
    if run_start is not None:
        run_len = h - run_start
        if run_len > best_score and run_start > h // 5:
            best_start, best_end = run_start, h - 1
            best_score = run_len
    if best_score < 8:
        return None
    return best_start, best_end


def _build_outline_mask(
    px, w: int, h: int, letter: list[list[bool]], supply_rows: tuple[int, int] | None
) -> list[list[bool]]:
    """Thin black stroke around orange SWIFT cores (not the drop shadow)."""
    outline = [[False] * w for _ in range(h)]
    for y in range(h):
        if _in_supply(y, supply_rows):
            continue
        for x in range(w):
            r, g, b, a = px[x, y]
            if a < 16 or not _is_dark(r, g, b):
                continue
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h and letter[ny][nx]:
                        outline[y][x] = True
                        break
                else:
                    continue
                break
    return outline


def _has_orange_offset(
    x: int,
    y: int,
    letter: list[list[bool]],
    w: int,
    h: int,
) -> bool:
    """True when an orange letter core sits up-left (shadow cast direction)."""
    for dy in range(SHADOW_DY[0], SHADOW_DY[1] + 1):
        for dx in range(SHADOW_DX[0], SHADOW_DX[1] + 1):
            ox, oy = x - dx, y - dy
            if 0 <= ox < w and 0 <= oy < h and letter[oy][ox]:
                return True
    return False


def _in_supply(y: int, supply_rows: tuple[int, int] | None) -> bool:
    return bool(supply_rows and supply_rows[0] <= y <= supply_rows[1])


def _build_shadow_mask(
    px,
    w: int,
    h: int,
    letter: list[list[bool]],
    outline: list[list[bool]],
    supply_rows: tuple[int, int] | None,
) -> list[list[bool]]:
    """Morphological shadow mask: dark CCs offset from SWIFT, not letter outlines."""
    shadow = [[False] * w for _ in range(h)]
    seeds: list[tuple[int, int]] = []

    for y in range(h):
        if _in_supply(y, supply_rows):
            continue
        for x in range(w):
            r, g, b, a = px[x, y]
            if a < 16 or not _is_dark(r, g, b):
                continue
            if outline[y][x]:
                continue
            if _has_orange_offset(x, y, letter, w, h):
                seeds.append((x, y))

    # Flood-fill through dark pixels that are not letter outlines or SUPPLY.
    for sx, sy in seeds:
        if shadow[sy][sx]:
            continue
        q: deque[tuple[int, int]] = deque([(sx, sy)])
        shadow[sy][sx] = True
        while q:
            x, y = q.popleft()
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if not (0 <= nx < w and 0 <= ny < h):
                    continue
                if _in_supply(ny, supply_rows) or outline[ny][nx] or shadow[ny][nx]:
                    continue
                r, g, b, a = px[nx, ny]
                if a < 16 or not _is_dark(r, g, b):
                    continue
                shadow[ny][nx] = True
                q.append((nx, ny))

    # Absorb anti-aliased fringe and a thin halo — never swallow orange letter cores.
    for _pass in range(3):
        changed = False
        for y in range(h):
            if _in_supply(y, supply_rows):
                continue
            for x in range(w):
                if shadow[y][x] or outline[y][x]:
                    continue
                r, g, b, a = px[x, y]
                if a < 16 or _is_background(r, g, b) or _is_orange(r, g, b):
                    continue
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < w and 0 <= ny < h and shadow[ny][nx]:
                            shadow[y][x] = True
                            changed = True
                            break
                    else:
                        continue
                    break
        if not changed:
            break

    return shadow


def _trim_transparent(img: Image.Image, pad: int = 3) -> Image.Image:
    bbox = img.getbbox()
    if not bbox:
        return img
    x0, y0, x1, y1 = bbox
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(img.width, x1 + pad)
    y1 = min(img.height, y1 + pad)
    return img.crop((x0, y0, x1, y1))


def _upscale_to_target(img: Image.Image, target_width: int) -> Image.Image:
    if img.width >= target_width:
        return img
    scale = target_width / img.width
    new_h = max(1, round(img.height * scale))
    return img.resize((target_width, new_h), Image.Resampling.LANCZOS)


def render_white_logo(src: Path, target_width: int = TARGET_WIDTH) -> Image.Image:
    """Flat white SWIFT+SUPPLY+bars on transparent — no drop shadow."""
    img = _upscale_to_target(Image.open(src).convert("RGBA"), target_width)
    w, h = img.size
    px = img.load()

    letter = [[False] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a < 16:
                continue
            if _is_orange(r, g, b):
                letter[y][x] = True

    supply_rows = _find_supply_rows(px, w, h, letter)
    outline = _build_outline_mask(px, w, h, letter, supply_rows)
    shadow = _build_shadow_mask(px, w, h, letter, outline, supply_rows)

    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out_px = out.load()

    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            strength = _logo_strength(r, g, b, a)
            if strength <= 0 or shadow[y][x]:
                continue
            alpha = min(255, max(0, round(strength * 255)))
            if alpha < 12:
                continue
            out_px[x, y] = (255, 255, 255, alpha)

    return _trim_transparent(out)


def recolor_logo(img: Image.Image, rgb: tuple[int, int, int]) -> Image.Image:
    out = img.copy()
    out_px = out.load()
    for y in range(out.height):
        for x in range(out.width):
            _, _, _, a = out_px[x, y]
            if a:
                out_px[x, y] = (*rgb, a)
    return out


def _preview_png(img: Image.Image, dest: Path, preview_width: int = PREVIEW_WIDTH) -> None:
    if img.width <= preview_width:
        preview = img.copy()
    else:
        scale = preview_width / img.width
        preview = img.resize(
            (preview_width, max(1, round(img.height * scale))),
            Image.Resampling.LANCZOS,
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    preview.save(dest, optimize=True)


def export_brand_assets(
    src: Path,
    *,
    trace_vector: bool = False,
    two_stage: bool = True,
) -> dict[str, Path]:
    """Write high-res brand PNG/SVG exports plus chat-friendly previews.

    Document PDFs use ``swift_supply_logo_orange`` (shadow + black SUPPLY via
    ``scripts/build_orange_shadow_logo.py``). The all-orange archive lives at
    ``swift_supply_logo_orange_solid.{png,svg}`` — do not overwrite with shadow.
    """
    white = render_white_logo(src, BRAND_TARGET_WIDTH)
    orange = recolor_logo(white, APP_ORANGE_RGB)

    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, Path] = {
        "white_png": BRAND_DIR / "swift_supply_logo_white.png",
        "white_svg": BRAND_DIR / "swift_supply_logo_white.svg",
        # Shadow build overwrites orange_png/svg — run build_orange_shadow_logo after.
        "orange_png": BRAND_DIR / "swift_supply_logo_orange.png",
        "orange_svg": BRAND_DIR / "swift_supply_logo_orange.svg",
        "white_preview": BRAND_DIR / "swift_supply_logo_white_preview.png",
        "orange_preview": BRAND_DIR / "swift_supply_logo_orange_preview.png",
    }

    white.save(outputs["white_png"], optimize=True)
    orange.save(outputs["orange_png"], optimize=True)
    print(
        f"Wrote {outputs['white_png']} ({white.width}x{white.height}) "
        f"from {src.name}"
    )
    print(
        f"Wrote {outputs['orange_png']} ({orange.width}x{orange.height}) "
        f"fill {APP_ORANGE_HEX}"
    )

    if two_stage:
        white_method = _two_stage_brand_svg(outputs["white_png"], outputs["white_svg"], "#FFFFFF")
        orange_method = _two_stage_brand_svg(
            outputs["orange_png"], outputs["orange_svg"], APP_ORANGE_HEX
        )
        print(
            f"Wrote {outputs['white_svg']} ({outputs['white_svg'].stat().st_size} bytes) "
            f"via {white_method}"
        )
        print(
            f"Wrote {outputs['orange_svg']} ({outputs['orange_svg'].stat().st_size} bytes) "
            f"via {orange_method}"
        )
    elif trace_vector:
        white_method = _trace_mask_to_svg(white, outputs["white_svg"], "#FFFFFF")
        orange_method = _trace_mask_to_svg(orange, outputs["orange_svg"], APP_ORANGE_HEX)
        print(
            f"Wrote {outputs['white_svg']} ({outputs['white_svg'].stat().st_size} bytes) "
            f"via {white_method}"
        )
        print(
            f"Wrote {outputs['orange_svg']} ({outputs['orange_svg'].stat().st_size} bytes) "
            f"via {orange_method}"
        )
    else:
        w_dims = write_embedded_png_svg(outputs["white_png"], outputs["white_svg"])
        o_dims = write_embedded_png_svg(outputs["orange_png"], outputs["orange_svg"])
        print(
            f"Wrote {outputs['white_svg']} ({outputs['white_svg'].stat().st_size} bytes, "
            f"embed-png {w_dims[0]}x{w_dims[1]})"
        )
        print(
            f"Wrote {outputs['orange_svg']} ({outputs['orange_svg'].stat().st_size} bytes, "
            f"embed-png {o_dims[0]}x{o_dims[1]})"
        )

    _preview_png(white, outputs["white_preview"])
    _preview_png(orange, outputs["orange_preview"])
    print(f"Wrote {outputs['white_preview']}")
    print(f"Wrote {outputs['orange_preview']}")

    return outputs


def process_logo(src: Path, dest: Path, target_width: int = TARGET_WIDTH) -> None:
    out = render_white_logo(src, target_width)
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.save(dest, optimize=True)
    print(f"Wrote {dest} ({out.width}x{out.height}) from {src.name}")


def export_document_logo(
    source_png: Path,
    *,
    out_svg: Path,
    out_png: Path,
    render_width: int = 2987,
    per_section_dir: Path | None = None,
) -> dict[str, Path]:
    """
    Manual-quality sectional vectorization of the Swift full lockup.

    - Traces SWIFT-orange, drop shadow, SUPPLY, and both bars as separate
      named layers via the manual Bezier fitter.
    - Writes the composed SVG (source of truth) and rasterizes the PNG from
      that SVG at ``render_width`` for use on generated documents.
    """
    from PIL import Image as _PILImage

    img = _PILImage.open(source_png).convert("RGBA")
    sections = decompose_swift_supply(img)
    result = vectorize_sectional(img, sections)
    out_svg.parent.mkdir(parents=True, exist_ok=True)
    out_svg.write_text(result.svg, encoding="utf-8")

    if per_section_dir is not None:
        from tools.logo_vectorizer.sectional import compose_sectional_svg

        per_section_dir.mkdir(parents=True, exist_ok=True)
        for st in result.per_section:
            single = compose_sectional_svg([st], source_size=result.source_size)
            (per_section_dir / f"{st.section.name}.svg").write_text(
                single, encoding="utf-8"
            )

    out_png.parent.mkdir(parents=True, exist_ok=True)
    rasterize_svg(out_svg, out_png, width=render_width, background="transparent")
    print(
        f"Document logo -> {out_svg} ({out_svg.stat().st_size} bytes, "
        f"{result.total_anchors()} anchors, "
        f"{len(result.per_section)} sections)"
    )
    print(f"Document PNG  -> {out_png} ({out_png.stat().st_size} bytes)")
    return {"svg": out_svg, "png": out_png}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    brand_mode = "--brand" in sys.argv
    document_mode = "--document" in sys.argv
    trace_vector = "--trace-vector" in sys.argv

    explicit = Path(args[0]) if args else None

    if document_mode:
        # For the sectional lockup we prefer an explicit path (or the copy
        # under `assets/brand/swift_supply_source.png`) rather than the
        # wordmark source.
        candidates = [
            explicit,
            ROOT / "assets" / "brand" / "swift_supply_source.png",
        ]
        src = next((p for p in candidates if p and p.is_file()), None)
        if src is None:
            print("Document source PNG not found.", file=sys.stderr)
            return 1
        out_svg = BRAND_DIR / "swift_supply_logo_document.svg"
        out_png = BRAND_DIR / "swift_supply_logo_document.png"
        per_section_dir = BRAND_DIR / "_document_sections"
        export_document_logo(
            src,
            out_svg=out_svg,
            out_png=out_png,
            render_width=2987,
            per_section_dir=per_section_dir,
        )
        # Sync into the Flutter bundle so PDFs can load it.
        mobile_dest = ROOT / "mobile" / "assets" / "images" / out_png.name
        if mobile_dest.parent.exists():
            mobile_dest.write_bytes(out_png.read_bytes())
            print(f"Synced       -> {mobile_dest}")
        return 0

    try:
        src = _pick_source(explicit)
    except ValueError:
        print("Source logo not found.", file=sys.stderr)
        return 1
    if not src.is_file():
        print(f"Source logo not found: {src}", file=sys.stderr)
        return 1

    if brand_mode:
        export_brand_assets(src, trace_vector=trace_vector)
        return 0

    process_logo(src, OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
