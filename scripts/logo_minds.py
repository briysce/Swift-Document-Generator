"""Let Gemini and Claude take over the logos the engine is stuck on — and judge them.

For each case: the engine's best output (Meedo-Me's reviewer first, then
composite_v2) is critiqued by both minds; then each mind draws the logo itself
as an SVG, Gemini's image model repaints a clean raster for the engine to
trace, each mind critiques the other's drawing, and the author revises once.
Every candidate is scored against the clean master with the improve loop's own
scorer and reviewed against the sketch. A mind's drawing wins only if the
reviewer passes it and it beats the engine; nothing here replaces a shipped
file — it reports, and it judges every consultation for Meedo-Me
(`meedo_consult`), so each mind's track record says what its answers were
worth.

    python scripts/logo_minds.py --cases swift_orange_solid__import_combo,arc__blur_crush
    python scripts/logo_minds.py --auto 4          # the four weakest engine results
    python scripts/logo_minds.py --auto 4 --no-redraw --minds claude

Writes qa_logos/synthetic/minds/<run>/: each candidate's SVG, summary.json, and
a comparison sheet to look at.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from logo_golden_suite import score_pair, score_pair_v2  # noqa: E402

from tools.logo_vectorizer import meedo_consult as C  # noqa: E402
from tools.logo_vectorizer import meedo_review as R  # noqa: E402
from tools.logo_vectorizer.ai_advisors import minds as M  # noqa: E402

SYN = ROOT / "qa_logos" / "synthetic"
OUT = SYN / "minds"
INPUTS = OUT / "inputs"
# The target is a vector: the minds' drawings are measured against the
# engine's vector output. A raster upscale scores best on raster similarity by
# staying blurry-faithful to the sketch — beating it is not the goal.
ENGINES = ("vectorize",)
_UNSAFE = re.compile(r"<\s*(image|script|foreignObject|iframe|use\b[^>]*href\s*=\s*['\"]https?:)|href\s*=\s*['\"](?:https?:|file:|data:)",
                     re.I)


def _png(path: Path) -> Image.Image:
    return Image.open(path).convert("RGBA")


def _hexes(palette) -> str:
    cols = [c for c, share in palette if share >= 0.02 and not R._is_page_white(c)]
    return ", ".join("#{:02X}{:02X}{:02X}".format(*c) for c in cols) or "the sketch's own colours"


def render_svg(svg: str, size: tuple[int, int]) -> Image.Image | None:
    """Render a mind's SVG, refusing anything that reaches outside itself."""
    if not svg or _UNSAFE.search(svg):
        return None
    import cairosvg

    try:
        raw = cairosvg.svg2png(bytestring=svg.encode(), output_width=size[0], output_height=size[1], unsafe=False)
    except Exception:
        return None
    return Image.open(io.BytesIO(raw)).convert("RGBA")


def _ink_box(arr: np.ndarray) -> tuple[int, int, int, int] | None:
    """Bounding box of what is drawn: alpha where there is alpha, otherwise
    whatever differs from the corner (page) colour."""
    if arr[..., 3].min() < 250:
        ink = arr[..., 3] > 127
    else:
        page = np.median(np.concatenate([arr[:4, :4, :3].reshape(-1, 3), arr[-4:, -4:, :3].reshape(-1, 3)]), axis=0)
        ink = np.abs(arr[..., :3].astype(int) - page).sum(axis=2) > 60
    ys, xs = np.nonzero(ink)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def anchor_redraw(redraw: Image.Image, sketch: Image.Image, scale: float = 4.0) -> Image.Image:
    """Put an image model's repaint back on the sketch's frame and palette.

    The repaint keeps the letterforms but not the frame (the model letterboxes
    to its own aspect ratio) nor the inks (it darkens or brightens them). A
    designer redrawing over a scan keeps the scan's frame and the brand's
    colours, so: map the repaint's ink box onto the sketch's ink box, and snap
    every pixel to the nearest of the sketch's own inks or the page."""
    s = np.asarray(sketch.convert("RGBA"))
    r = np.asarray(redraw.convert("RGBA"))
    sb, rb = _ink_box(s), _ink_box(r)
    if sb is None or rb is None:
        return redraw
    W, H = int(round(s.shape[1] * scale)), int(round(s.shape[0] * scale))
    x0, y0, x1, y1 = (int(round(v * scale)) for v in sb)
    crop = Image.fromarray(r).crop(rb).resize((max(1, x1 - x0), max(1, y1 - y0)), Image.LANCZOS)
    canvas = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    canvas.alpha_composite(crop.convert("RGBA"), (x0, y0))
    inks = [c for c, share in R.palette(sketch) if share >= 0.01]
    targets = np.array(inks + [(255, 255, 255)], dtype=float)
    px = np.asarray(canvas)[..., :3].astype(float)
    d = ((px[:, :, None, :] - targets[None, None]) ** 2).sum(-1)
    snapped = targets[d.argmin(-1)].astype(np.uint8)
    return Image.fromarray(np.dstack([snapped, np.full(snapped.shape[:2], 255, np.uint8)]), "RGBA")


def _score(clean: Path, png: Path, sketch: Path) -> dict:
    m = score_pair(clean, png)
    m.update(score_pair_v2(clean, png, m))
    rv = R.review(_png(png), _png(sketch))
    m["review_passed"] = rv.passed
    m["review"] = [f.detail for f in rv.findings]
    return m


def engine_best(case: str) -> dict | None:
    clean, sketch = SYN / "clean" / f"{case.split('__')[0]}.png", SYN / "degraded" / f"{case}.png"
    best = None
    for eng in ENGINES:
        p = SYN / "restored" / f"{case}__{eng}.png"
        if not p.is_file():
            continue
        s = _score(clean, p, sketch)
        key = (s["review_passed"], s.get("composite_v2", s["composite"]))
        if best is None or key > best["key"]:
            best = {"engine": eng, "path": p, "scores": s, "key": key}
    return best


def weakest(n: int) -> list[str]:
    pairs = json.loads((SYN / "pairs.json").read_text())["pairs"]
    ranked = []
    for p in pairs:
        b = engine_best(p["id"])
        if b:
            ranked.append((b["key"], p["id"]))
    return [c for _, c in sorted(ranked)[:n]]


def _brand_notes(case: str) -> str:
    try:
        from tools.logo_vectorizer.ai_advisors.collab_mind import load_brand_ref

        ref = load_brand_ref(case) or {}
    except Exception:
        ref = {}
    b = ref.get("branding") or {}
    if not ref:
        return ""
    bits = [f"This is the logo of {ref.get('name')}."]
    if b.get("wordmark"):
        bits.append(f"Design: {b['wordmark']}.")
    if b.get("must_keep"):
        bits.append("It must keep: " + "; ".join(b["must_keep"]) + ".")
    bits.append("(Brand notes are background; the sketch decides what this logo looks like.)")
    return " ".join(bits)


def _judge(cid: str, result: str, detail: str, delta: float | None = None) -> None:
    if not cid:
        return
    try:
        C.judge(cid, result, detail, delta=delta, by="logo_minds (auto)")
    except Exception:
        pass


def run_case(case: str, minds: list[str], *, redraw: bool, rounds: int, where: Path, session: str,
             svg: bool = True) -> dict:
    clean = SYN / "clean" / f"{case.split('__')[0]}.png"
    sketch_p = SYN / "degraded" / f"{case}.png"
    sketch = _png(sketch_p)
    best = engine_best(case)
    if best is None:
        return {"case": case, "error": "no engine output"}
    eng_img = _png(best["path"])
    size = eng_img.size
    notes = _brand_notes(case)
    palette = _hexes(R.palette(sketch))
    kw = dict(case=case, session=session, image_dir=INPUTS)
    out = {"case": case, "engine": {"name": best["engine"], **best["scores"]}, "critiques": {}, "candidates": []}
    base_v2 = best["scores"].get("composite_v2", best["scores"]["composite"])

    crit = {}
    for m in minds:
        a = M.critique(m, sketch, eng_img, notes=notes, **kw)
        crit[m] = a
        out["critiques"][m] = {"verdict": a.parsed.get("verdict"), "advice": a.parsed.get("advice"),
                               "error": a.error or None, "id": a.consultation}

    def add(name: str, author: str, ans: M.Answer | None, img: Image.Image | None, svg: str = "") -> None:
        cand = {"name": name, "author": author, "consultation": ans.consultation if ans else ""}
        if img is None:
            cand["error"] = (ans.error if ans and ans.error else "no usable drawing")
            out["candidates"].append(cand)
            _judge(cand["consultation"], "wrong", f"{name}: {cand['error'][:200]}")
            return
        png = where / f"{case}__{name}.png"
        img.save(png)
        if svg:
            (where / f"{case}__{name}.svg").write_text(svg, encoding="utf-8")
        s = _score(clean, png, sketch_p)
        v2 = s.get("composite_v2", s["composite"])
        cand.update(s, png=str(png.relative_to(ROOT)), beats_engine=bool(s["review_passed"] and v2 > base_v2))
        out["candidates"].append(cand)
        if not s["review_passed"]:
            _judge(cand["consultation"], "wrong", f"{name} blocked by the reviewer: {'; '.join(s['review'])[:200]}",
                   delta=v2 - base_v2)
        elif cand["beats_engine"]:
            _judge(cand["consultation"], "helped", f"{name} passed review and beat the engine "
                   f"({best['engine']}) on composite_v2", delta=v2 - base_v2)
        else:
            _judge(cand["consultation"], "no_change", f"{name} passed review but did not beat the engine",
                   delta=v2 - base_v2)

    drawings: dict[str, tuple[str, Image.Image | None]] = {}
    for m in (minds if svg else []):
        a = M.takeover(m, sketch, palette=palette, notes=notes, **kw)
        svg = M.svg_from(a)
        img = render_svg(svg, size)
        drawings[m] = (svg, img)
        add(f"{m}_svg", m, a, img, svg)

    if redraw and M.available("gemini-image"):
        a = M.redraw(sketch, palette=palette, **kw)
        img = None
        if a.image is not None:
            a.image.save(where / f"{case}__gemini_redraw_raw.png")
            src = where / f"{case}__gemini_redraw_src.png"
            anchor_redraw(a.image, sketch).save(src)
            sys.path.insert(0, str(ROOT / "scripts"))
            from logo_restore_improve_loop import restore

            os.environ["LOGO_COLLAB_MIND"] = "0"  # no nested escalation inside the trace
            dest = where / f"{case}__gemini_redraw_trace.png"
            ok, _ = restore("vectorize", src, dest, max(size[1], 1200))
            img = _png(dest) if ok else None
        add("gemini_redraw_trace", "gemini-image", a, img)

    for _ in range(max(0, rounds)):
        for m in minds:
            svg, img = drawings.get(m, ("", None))
            other = next((o for o in minds if o != m), None)
            if img is None or other is None:
                continue
            review = M.critique(other, sketch, img, notes=notes + f" IMAGE 2 is {m}'s drawing.", **kw)
            a = M.revise(m, sketch, img, svg, M.critique_text(review), **kw)
            new_svg = M.svg_from(a)
            new_img = render_svg(new_svg, size)
            add(f"{m}_svg_revised", m, a, new_img, new_svg)
            before = next(c for c in out["candidates"] if c["name"] == f"{m}_svg")
            after = out["candidates"][-1]
            if "composite_v2" in after and "composite_v2" in before:
                better = after["review_passed"] and after["composite_v2"] > before["composite_v2"]
                _judge(review.consultation, "helped" if better else "no_change",
                       f"{other}'s critique of {m}'s drawing: revision "
                       f"{'improved' if better else 'did not improve'} it",
                       delta=after["composite_v2"] - before["composite_v2"])

    winners = [c for c in out["candidates"] if c.get("beats_engine")]
    for m, a in crit.items():
        verdict = (a.parsed or {}).get("verdict")
        if a.error or not verdict:
            continue
        engine_ok = best["scores"]["review_passed"]
        if verdict == "ship":
            res = "helped" if engine_ok and not winners else "wrong"
        else:
            res = "helped" if (winners or not engine_ok) else "no_change"
        _judge(a.consultation, res, f"critique said {verdict}; engine review "
               f"{'passed' if engine_ok else 'blocked'}; {len(winners)} mind drawing(s) beat the engine")
    out["winner"] = max(winners, key=lambda c: c["composite_v2"])["name"] if winners else best["engine"]
    return out


def sheet(result: dict, where: Path) -> Path | None:
    case = result["case"]
    tiles = [("sketch", _png(SYN / "degraded" / f"{case}.png"), ""),
             (f"engine: {result['engine']['name']}", _png(SYN / "restored" / f"{case}__{result['engine']['name']}.png"),
              f"v2 {result['engine'].get('composite_v2', 0):.3f} {'✓' if result['engine']['review_passed'] else 'BLOCKED'}")]
    for c in result["candidates"]:
        if c.get("png"):
            tiles.append((c["name"], _png(ROOT / c["png"]),
                          f"v2 {c.get('composite_v2', 0):.3f} {'✓' if c['review_passed'] else 'BLOCKED'}"
                          + (" WINS" if c.get("beats_engine") else "")))
    W = 900
    rows = []
    for title, im, sub in tiles:
        im = im.resize((W, max(1, int(im.height * W / im.width))), Image.LANCZOS)
        bg = Image.new("RGBA", (W, im.height + 40), "white")
        bg.alpha_composite(im, (0, 40))
        d = ImageDraw.Draw(bg)
        d.text((8, 8), f"{title}   {sub}", fill="black")
        rows.append(bg.convert("RGB"))
    H = sum(r.height + 6 for r in rows)
    out = Image.new("RGB", (W, H), (150, 150, 150))
    y = 0
    for r in rows:
        out.paste(r, (0, y))
        y += r.height + 6
    p = where / f"{case}__sheet.png"
    out.save(p)
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cases", default="")
    ap.add_argument("--auto", type=int, default=0, help="the N weakest engine results")
    ap.add_argument("--minds", default="claude,gemini")
    ap.add_argument("--no-redraw", action="store_true")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--no-svg", action="store_true", help="skip the minds' own SVG drawings")
    ap.add_argument("--engines", default="vectorize",
                    help="engine outputs the minds must beat (a vector target: rasters win raster metrics)")
    a = ap.parse_args(argv)
    global ENGINES
    ENGINES = tuple(e for e in a.engines.split(",") if e)
    cases = [c for c in a.cases.split(",") if c] or (weakest(a.auto) if a.auto else [])
    if not cases:
        ap.error("give --cases or --auto N")
    minds = [m for m in a.minds.split(",") if m and M.available(m)]
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    where = OUT / run
    where.mkdir(parents=True, exist_ok=True)
    results = []
    for case in cases:
        t0 = time.time()
        r = run_case(case, minds, redraw=not a.no_redraw, rounds=a.rounds, where=where, session=f"{run}-{case}",
                     svg=not a.no_svg)
        r["seconds"] = round(time.time() - t0, 1)
        if "error" not in r:
            r["sheet"] = str(sheet(r, where).relative_to(ROOT))
        results.append(r)
        print(json.dumps({"case": case, "winner": r.get("winner"), "engine_v2": r.get("engine", {}).get("composite_v2"),
                          "candidates": [(c["name"], c.get("composite_v2"), c.get("review_passed"), c.get("error"))
                                         for c in r.get("candidates", [])]}), flush=True)
    (where / "summary.json").write_text(json.dumps({"run": run, "minds": minds, "results": results}, indent=2,
                                                   default=str), encoding="utf-8")
    print(f"summary: {where / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
