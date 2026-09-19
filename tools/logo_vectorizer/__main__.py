"""CLI: python -m tools.logo_vectorizer --input logo.png --output out.svg
       [--fill #HEX] [--qa] [--two-stage] [--sectional] [--decomposer swift-supply|color]
       [--ai]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from tools.logo_vectorizer.ai_advisors import AIConfig  # noqa: E402
from tools.logo_vectorizer.ai_advisors.orchestrator import resolve_providers  # noqa: E402
from tools.logo_vectorizer.customer_recreate import recreate_customer_logo  # noqa: E402
from tools.logo_vectorizer.ensemble import vectorize_ensemble_file  # noqa: E402
from tools.logo_vectorizer.env_loader import load_env  # noqa: E402
from tools.logo_vectorizer.qa import verify_p_counters  # noqa: E402
from tools.logo_vectorizer.sectional import (  # noqa: E402
    rasterize_svg,
    vectorize_sectional,
)
from tools.logo_vectorizer.sections import (  # noqa: E402
    decompose_by_color,
    decompose_swift_supply,
)
from tools.logo_vectorizer.two_stage import run_two_stage  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ensemble vectorizer: multi-backend race + scoring + optional AI advisors."
    )
    parser.add_argument("--input", "-i", type=Path, required=True, help="Input transparent PNG")
    parser.add_argument("--output", "-o", type=Path, help="Output SVG path")
    parser.add_argument("--fill", default="#FFFFFF", help="SVG fill color (default #FFFFFF)")
    parser.add_argument("--qa", action="store_true", help="Run P-counter QA + overlay crop")
    parser.add_argument("--qa-crop", type=Path, help="Save SUPPLY P zoom QA crop PNG")
    parser.add_argument(
        "--two-stage",
        action="store_true",
        help="Stage A embed-PNG + Stage B geometric polish with QA gate",
    )
    parser.add_argument("--no-cache", action="store_true", help="Skip .cache/ param memory")
    parser.add_argument("--ai", action="store_true", help="Enable vision AI advisors (env API keys)")
    parser.add_argument(
        "--ai-providers",
        default="gemini,claude,openai",
        help="Comma-separated AI providers to use (default: all available)",
    )
    parser.add_argument(
        "--sectional",
        action="store_true",
        help="Sectional trace: manual-quality Bezier fitter per named region",
    )
    parser.add_argument(
        "--decomposer",
        default="swift-supply",
        choices=("swift-supply", "color"),
        help="Section decomposer for --sectional mode",
    )
    parser.add_argument(
        "--render-png",
        type=Path,
        help="After tracing, rasterize the SVG to PNG at this path",
    )
    parser.add_argument(
        "--render-width",
        type=int,
        help="Width (px) for --render-png output",
    )
    parser.add_argument(
        "--render-background",
        default="white",
        help="Background for --render-png (color or 'transparent')",
    )
    parser.add_argument(
        "--sections-dir",
        type=Path,
        help="If set with --sectional, write each layer to its own SVG here",
    )
    parser.add_argument(
        "--recreate-customer",
        action="store_true",
        help=(
            "Recreate a customer logo: strip background, cluster colors, "
            "manually-quality trace each color group, emit SVG + PNG."
        ),
    )
    parser.add_argument(
        "--max-colors",
        type=int,
        default=6,
        help="Maximum palette size for --recreate-customer (default 6)",
    )
    args = parser.parse_args()

    if not args.input.is_file():
        print(f"Input not found: {args.input}", file=sys.stderr)
        return 1

    out = args.output or args.input.with_suffix(".svg")

    if args.recreate_customer:
        png_out = args.render_png or out.with_suffix(".png")
        # Customer recreate always wants a transparent canvas unless the
        # caller explicitly passed --render-background (including "white").
        # argparse default is "white" for generic --render-png; for recreate
        # we treat that default as transparent so local Windows matches Fly.
        render_bg = args.render_background
        if render_bg == "white" and "--render-background" not in sys.argv:
            render_bg = "transparent"
        result = recreate_customer_logo(
            args.input,
            output_svg=out,
            output_png=png_out,
            max_colors=args.max_colors,
            render_width=args.render_width or 2000,
            render_background=render_bg or "transparent",
            # --ai forces on; otherwise auto when GEMINI_API_KEY is set.
            use_ai=True if args.ai else None,
            ai_providers=[
                p.strip()
                for p in (args.ai_providers or "gemini").split(",")
                if p.strip()
            ],
        )
        print(
            f"Recreate -> {out} ({out.stat().st_size} bytes)\n"
            f"  sections={result.section_count} "
            f"palette=[{', '.join(result.palette_hex)}]\n"
            f"  bg_stripped={result.background_stripped} "
            f"anchors~{result.total_anchors}"
        )
        if result.png_path is not None:
            print(f"  PNG -> {result.png_path} ({result.png_path.stat().st_size} bytes)")
        return 0

    if args.sectional:
        img = Image.open(args.input).convert("RGBA")
        if args.decomposer == "swift-supply":
            sections = decompose_swift_supply(img)
        else:
            sections = decompose_by_color(img)
        result = vectorize_sectional(img, sections)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result.svg, encoding="utf-8")
        print(
            f"Sectional trace -> {out} ({out.stat().st_size} bytes)\n"
            f"  sections={len(result.per_section)} anchors~{result.total_anchors()}"
        )
        for st in result.per_section:
            print(
                f"    {st.section.name:22s} d={len(st.combined_d):>6d}b "
                f"contours={st.trace.contour_count:3d} holes={st.trace.hole_count:2d}"
            )
        if args.sections_dir is not None:
            args.sections_dir.mkdir(parents=True, exist_ok=True)
            from tools.logo_vectorizer.sectional import compose_sectional_svg

            for st in result.per_section:
                single = compose_sectional_svg(
                    [st], source_size=result.source_size
                )
                (args.sections_dir / f"{st.section.name}.svg").write_text(
                    single, encoding="utf-8"
                )
            print(f"  per-section SVGs -> {args.sections_dir}")
        if args.render_png is not None:
            rasterize_svg(
                out,
                args.render_png,
                width=args.render_width,
                background=args.render_background,
            )
            print(f"  rendered PNG -> {args.render_png}")
        return 0

    if args.two_stage:
        qa_crop = args.qa_crop or out.with_name(out.stem + "_qa_p_crop.png")
        result = run_two_stage(
            args.input,
            out,
            fill_hex=args.fill,
            qa=args.qa or True,
            qa_crop_path=qa_crop,
        )
        sc = result.score
        print(
            f"Two-stage -> {out} ({out.stat().st_size} bytes)\n"
            f"  stage: {result.stage} (QA {'PASS' if result.passed_qa else 'FAIL'})\n"
            f"  contours={result.contour_count} holes={result.hole_count}"
        )
        if sc:
            print(
                f"  score={sc.total:.3f} iou={sc.alpha_iou:.3f} ssim={sc.ssim:.3f} "
                f"edge={sc.edge_score:.3f} p_hollow={sc.p_hollow:.3f}"
            )
        if result.roundness_qa:
            rq = result.roundness_qa
            print(
                f"  roundness: peri_drop={rq.get('perimeter_drop', 0):.4f} "
                f"circ_gain={rq.get('circularity_gain', 0):.4f}"
            )
        if result.p_qa:
            print(f"  P-counter QA: {result.p_qa}")
        if not result.passed_qa:
            print(
                "  WARN: geometric polish failed QA — kept embed-PNG SVG (pixel-perfect fallback).",
                file=sys.stderr,
            )
            return 2 if args.qa else 0
        return 0

    loaded = load_env()
    if loaded:
        print(f"Loaded env from: {', '.join(str(p.name) for p in loaded)}", file=sys.stderr)

    ai_cfg = AIConfig(
        enabled=args.ai or bool(resolve_providers(["gemini", "claude", "openai"])),
        providers=[p.strip() for p in args.ai_providers.split(",") if p.strip()],
    )
    if args.ai and not ai_cfg.enabled:
        print("[ai] --ai set but no API keys found in env", file=sys.stderr)

    result = vectorize_ensemble_file(
        args.input,
        out,
        fill_hex=args.fill,
        use_cache=not args.no_cache,
        ai=ai_cfg,
    )
    sc = result.score
    print(
        f"Wrote {out} ({out.stat().st_size} bytes)\n"
        f"  winner: {result.method} ({result.candidates_tried} candidates)\n"
        f"  preprocess: {result.preprocess.key()}\n"
        f"  contours={result.contour_count} holes={result.hole_count}\n"
        f"  score={sc.total:.3f} iou={sc.alpha_iou:.3f} ssim={sc.ssim:.3f} "
        f"edge={sc.edge_score:.3f} p_hollow={sc.p_hollow:.3f}"
    )
    if result.ai.providers_used:
        print(f"  AI providers: {', '.join(result.ai.providers_used)}")
    if result.ai.hints and result.ai.hints.recommended_backends:
        print(f"  AI backends hint: {result.ai.hints.recommended_backends}")

    if args.qa:
        qa_crop = args.qa_crop or out.with_name(out.stem + "_qa_p_crop.png")
        report = verify_p_counters(out, args.input, qa_crop_path=qa_crop)
        print("QA:", report)
        if not report.get("ok"):
            return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
