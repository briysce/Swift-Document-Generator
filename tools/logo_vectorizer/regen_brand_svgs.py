"""Fast brand SVG regen: opencv-tree + evenodd (verified P counters)."""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.logo_vectorizer.backends import _opencv_trace
from tools.logo_vectorizer.preprocess import PreprocessConfig, build_mask
from tools.logo_vectorizer.qa import verify_p_counters

BRAND = ROOT / "assets" / "brand"
CFG = PreprocessConfig(upscale=4, blur_radius=1.2, alpha_threshold=80, min_area=500)


def regen(name: str, fill: str) -> bool:
    png = BRAND / f"swift_supply_logo_{name}.png"
    svg = BRAND / f"swift_supply_logo_{name}.svg"
    qa = BRAND / f"swift_supply_logo_{name}_qa_p_crop.png"
    img = Image.open(png).convert("RGBA")
    mask, ts, ss = build_mask(img, CFG)
    cand = _opencv_trace(mask, ts, ss, CFG, fill, mode="tree")
    if not cand:
        print(f"{name}: trace failed")
        return False
    svg.write_text(cand.svg, encoding="utf-8")
    report = verify_p_counters(svg, png, qa_crop_path=qa)
    print(f"{name}: {svg.stat().st_size}b holes={cand.hole_count} QA={report.get('ok')}")
    return bool(report.get("ok"))


def main() -> int:
    ok = regen("white", "#FFFFFF") and regen("orange", "#CE4E30")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
