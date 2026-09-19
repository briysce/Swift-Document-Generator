#!/usr/bin/env python3
"""Score logo restore engines against qa_logos/golden seed cases.

Usage:
  python scripts/logo_golden_suite.py [--mode all|baseline|vectorize|esrgan|gemini-off|gemini-on]
  python scripts/logo_golden_suite.py --mode baseline --out qa_logos/golden/scores_latest.json

Modes exercise available local tools. Gemini-on only runs when an API key is
present and LOGO_RESTORE_USE_GEMINI logic is simulated here (optional call).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "qa_logos" / "golden"
CASES = GOLDEN / "cases"
VECTORIZE = ROOT / "scripts" / "logo_vectorize.py"
ESRGAN = ROOT / "logo_restorer.py"


def _load_rgba(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGBA"))


def _ink_mask(arr: np.ndarray, core: int = 96) -> np.ndarray:
    return arr[:, :, 3] >= core


def _resize(arr: np.ndarray, wh: tuple[int, int]) -> np.ndarray:
    return np.asarray(
        Image.fromarray(arr, "RGBA").resize(wh, Image.Resampling.BILINEAR)
    )


def _ink_aabb(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def aspect_drift(src: np.ndarray, dst: np.ndarray) -> float:
    sa = _ink_aabb(_ink_mask(src))
    sb = _ink_aabb(_ink_mask(dst))
    if sa is None or sb is None:
        return 1.0
    aw = max(1, sa[2] - sa[0] + 1)
    ah = max(1, sa[3] - sa[1] + 1)
    bw = max(1, sb[2] - sb[0] + 1)
    bh = max(1, sb[3] - sb[1] + 1)
    aa, ba = aw / ah, bw / bh
    return abs(aa - ba) / max(aa, 0.01)


def ink_iou(src: np.ndarray, dst: np.ndarray) -> float:
    """Ink-mask IoU after cropping both to opaque AABB, then matching size."""
    def crop(arr: np.ndarray) -> np.ndarray:
        m = _ink_mask(arr)
        box = _ink_aabb(m)
        if box is None:
            return arr
        x0, y0, x1, y1 = box
        return arr[y0 : y1 + 1, x0 : x1 + 1]

    sa = crop(src)
    da = crop(dst)
    h, w = sa.shape[:2]
    d = _resize(da, (w, h))
    a = _ink_mask(sa)
    b = _ink_mask(d)
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    return float(inter / union) if union else 0.0


def palette_fidelity(src: np.ndarray, dst: np.ndarray, tol: int = 48) -> float:
    """Fraction of restored ink pixels near a quantized source brand color."""
    h, w = src.shape[:2]
    d = _resize(dst, (w, h))
    sm = _ink_mask(src)
    dm = _ink_mask(d)
    if not sm.any() or not dm.any():
        return 0.0
    q = (src[sm, :3] // 16).astype(np.int32)
    keys = q[:, 0] * 4096 + q[:, 1] * 64 + q[:, 2]
    uniq, counts = np.unique(keys, return_counts=True)
    order = np.argsort(-counts)
    top = uniq[order[:24]]
    palette = []
    for k in top:
        r = (int(k) // 4096) * 16 + 8
        g = ((int(k) // 64) % 64) * 16 + 8
        b = (int(k) % 64) * 16 + 8
        palette.append(np.array([r, g, b], dtype=np.int32))
    if not palette:
        return 0.0
    pal = np.stack(palette, axis=0)
    pix = d[dm, :3].astype(np.int32)
    # Distance to nearest palette entry
    diffs = np.abs(pix[:, None, :] - pal[None, :, :]).sum(axis=2)
    best = diffs.min(axis=1)
    return float((best <= tol).mean())


def alpha_clean(arr: np.ndarray) -> float:
    """1 - fraction of leftover plate (near-white/near-black low-chroma outer)."""
    a = arr[:, :, 3]
    rgb = arr[:, :, :3].astype(np.int32)
    lum = rgb.mean(axis=2)
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    # Opaque plate leftovers: solid white/black that should be transparent
    plate = (a > 200) & (
        ((lum > 245) & (sat < 12)) | ((lum < 18) & (sat < 12))
    )
    # Only count plate near edges (outer ring)
    h, w = a.shape
    edge = np.zeros_like(plate, dtype=bool)
    band = max(2, min(h, w) // 20)
    edge[:band, :] = True
    edge[-band:, :] = True
    edge[:, :band] = True
    edge[:, -band:] = True
    leftover = plate & edge
    denom = max(1, int(edge.sum()))
    return float(1.0 - leftover.sum() / denom)


def edge_energy(arr: np.ndarray) -> float:
    gray = arr[:, :, :3].astype(np.float32).mean(axis=2)
    a = arr[:, :, 3].astype(np.float32) / 255.0
    g = gray * a
    gx = np.abs(np.diff(g, axis=1)).mean() if g.shape[1] > 1 else 0.0
    gy = np.abs(np.diff(g, axis=0)).mean() if g.shape[0] > 1 else 0.0
    return float(gx + gy)


def edge_energy_ratio(src: np.ndarray, dst: np.ndarray) -> float:
    se = edge_energy(src)
    if se < 1e-6:
        return 1.0
    h, w = src.shape[:2]
    return edge_energy(_resize(dst, (w, h))) / se


def composite(m: dict) -> float:
    # Higher better. Penalize aspect drift.
    return float(
        0.35 * m["ink_iou"]
        + 0.25 * m["palette_fidelity"]
        + 0.20 * m["alpha_clean"]
        + 0.10 * min(m["edge_energy_ratio"], 2.0) / 2.0
        + 0.10 * max(0.0, 1.0 - m["aspect_drift"] / 0.5)
    )


def score_pair(src_path: Path, out_path: Path) -> dict:
    src = _load_rgba(src_path)
    dst = _load_rgba(out_path)
    m = {
        "aspect_drift": round(aspect_drift(src, dst), 4),
        "ink_iou": round(ink_iou(src, dst), 4),
        "palette_fidelity": round(palette_fidelity(src, dst), 4),
        "alpha_clean": round(alpha_clean(dst), 4),
        "edge_energy_ratio": round(edge_energy_ratio(src, dst), 4),
    }
    m["composite"] = round(composite(m), 4)
    return m


def _find_python() -> list[str]:
    for cmd in (["py", "-3"], ["python"], ["python3"]):
        try:
            r = subprocess.run(
                [*cmd, "-c", "import sys; print(sys.version)"],
                capture_output=True,
                text=True,
                timeout=8,
            )
            if r.returncode == 0:
                return cmd
        except Exception:
            pass
    return [sys.executable]


def _cubic_upscale(src: Path, dest: Path, min_height: int = 1200) -> Path:
    """Lightweight baseline: plate/halo knockout + upscale (no torch)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from logo_raster_finish import (
        finalize_restore,
        is_thin_wordmark,
        load_rgba,
        prepare_for_engine,
    )

    source = load_rgba(src)
    prepared = prepare_for_engine(source)
    out = Image.fromarray(prepared, "RGBA")
    # Thin wordmarks: LANCZOS preserves stroke edges better than BICUBIC.
    interp = (
        Image.Resampling.LANCZOS
        if is_thin_wordmark(prepared)
        else Image.Resampling.BICUBIC
    )
    if out.height < min_height:
        scale = min_height / out.height
        out = out.resize(
            (max(1, int(out.width * scale)), min_height),
            interp,
        )
    # Soft palette lock (do not reject baseline — it is the safety net).
    arr = np.asarray(out, dtype=np.uint8).copy()
    try:
        arr = finalize_restore(arr, prepared, min_palette=0.05)
    except RuntimeError:
        pass
    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr, "RGBA").save(dest)
    return dest


def _run_script(script: Path, src: Path, dest: Path, min_h: int) -> bool:
    if not script.is_file():
        return False
    py = _find_python()
    try:
        r = subprocess.run(
            [*py, str(script), str(src), str(dest), "--min-height", str(min_h)],
            capture_output=True,
            text=True,
            timeout=240,
            cwd=str(ROOT),
        )
        return r.returncode == 0 and dest.is_file() and dest.stat().st_size > 0
    except Exception:
        return False


def _gemini_restore(src: Path, dest: Path) -> bool:
    """Optional: call mobile Gemini via a tiny Python stub if key present.

    The app path is Dart; this suite marks gemini-on as skipped unless
    scripts/restore_logo_gemini.py exists. Prefer measuring gemini-off default.
    """
    helper = ROOT / "scripts" / "restore_logo_gemini.py"
    if not helper.is_file():
        return False
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        return False
    return _run_script(helper, src, dest, 1200)


def list_cases() -> list[Path]:
    if not CASES.is_dir():
        return []
    out = []
    for d in sorted(CASES.iterdir()):
        orig = d / "original.png"
        if d.is_dir() and orig.is_file():
            out.append(d)
    return out


def run_mode(mode: str, min_h: int = 1200) -> dict:
    cases = list_cases()
    results = []
    skipped = 0
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="golden_logo_") as td:
        td_path = Path(td)
        for case_dir in cases:
            slug = case_dir.name
            src = case_dir / "original.png"
            out = td_path / f"{slug}_{mode}.png"
            ok = False
            note = ""
            if mode in ("baseline", "gemini-off"):
                _cubic_upscale(src, out, min_h)
                ok = out.is_file()
                note = "prepare+cubic"
            elif mode == "vectorize":
                ok = _run_script(VECTORIZE, src, out, min_h)
                note = "vectorize" if ok else "vectorize_failed"
                if not ok:
                    skipped += 1
                    results.append(
                        {
                            "slug": slug,
                            "mode": mode,
                            "ok": False,
                            "note": note,
                        }
                    )
                    continue
            elif mode == "esrgan":
                ok = _run_script(ESRGAN, src, out, min_h)
                note = "esrgan" if ok else "esrgan_failed"
                if not ok:
                    skipped += 1
                    results.append(
                        {
                            "slug": slug,
                            "mode": mode,
                            "ok": False,
                            "note": note,
                        }
                    )
                    continue
            elif mode == "gemini-on":
                ok = _gemini_restore(src, out)
                if not ok:
                    skipped += 1
                    results.append(
                        {
                            "slug": slug,
                            "mode": mode,
                            "ok": False,
                            "note": "gemini_unavailable",
                        }
                    )
                    continue
                note = "gemini"
            else:
                raise ValueError(mode)

            metrics = score_pair(src, out)
            # Self-consistency floors for prepare path
            metrics["slug"] = slug
            metrics["mode"] = mode
            metrics["ok"] = True
            metrics["note"] = note
            # Anchor: both Swift orange variants should stay near-perfect
            if slug in ("swift_orange", "swift_orange_solid") and mode in (
                "baseline",
                "gemini-off",
            ):
                metrics["anchor"] = "pinnacle_north_star"
            results.append(metrics)
    composites = [r["composite"] for r in results if r.get("ok") and "composite" in r]
    summary = {
        "mode": mode,
        "n_cases": len(cases),
        "n_scored": len(composites),
        "n_skipped": skipped,
        "mean_composite": round(float(np.mean(composites)), 4) if composites else None,
        "mean_ink_iou": round(
            float(np.mean([r["ink_iou"] for r in results if r.get("ok") and "ink_iou" in r])),
            4,
        )
        if composites
        else None,
        "mean_palette": round(
            float(
                np.mean(
                    [
                        r["palette_fidelity"]
                        for r in results
                        if r.get("ok") and "palette_fidelity" in r
                    ]
                )
            ),
            4,
        )
        if composites
        else None,
        "mean_alpha_clean": round(
            float(
                np.mean(
                    [r["alpha_clean"] for r in results if r.get("ok") and "alpha_clean" in r]
                )
            ),
            4,
        )
        if composites
        else None,
        "elapsed_s": round(time.time() - t0, 2),
        "cases": results,
    }
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Logo golden suite scorer")
    p.add_argument(
        "--mode",
        default="all",
        choices=["all", "baseline", "vectorize", "esrgan", "gemini-off", "gemini-on"],
    )
    p.add_argument(
        "--out",
        default=str(GOLDEN / "scores_latest.json"),
    )
    p.add_argument("--min-height", type=int, default=1200)
    args = p.parse_args(argv)

    if not CASES.is_dir():
        print(f"No golden cases at {CASES}", file=sys.stderr)
        return 2

    modes = (
        ["baseline", "vectorize", "esrgan", "gemini-off", "gemini-on"]
        if args.mode == "all"
        else [args.mode]
    )
    # gemini-off aliases baseline prepare+cubic (app default without Gemini)
    report = {
        "pipeline_note": "Gemini demoted by default in app (LOGO_RESTORE_USE_GEMINI=1 to enable)",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "modes": {},
    }
    for m in modes:
        print(f"=== mode={m} ===", flush=True)
        report["modes"][m] = run_mode(m, args.min_height)
        s = report["modes"][m]
        print(
            f"  scored={s['n_scored']} skipped={s['n_skipped']} "
            f"mean_composite={s['mean_composite']} iou={s['mean_ink_iou']}",
            flush=True,
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    csv_path = out_path.with_suffix(".csv")
    rows = []
    for mode, block in report["modes"].items():
        for c in block.get("cases", []):
            if not c.get("ok"):
                rows.append(
                    {
                        "mode": mode,
                        "slug": c.get("slug"),
                        "ok": False,
                        "note": c.get("note"),
                    }
                )
                continue
            rows.append(
                {
                    "mode": mode,
                    "slug": c["slug"],
                    "ok": True,
                    "composite": c.get("composite"),
                    "aspect_drift": c.get("aspect_drift"),
                    "ink_iou": c.get("ink_iou"),
                    "palette_fidelity": c.get("palette_fidelity"),
                    "alpha_clean": c.get("alpha_clean"),
                    "edge_energy_ratio": c.get("edge_energy_ratio"),
                    "note": c.get("note"),
                }
            )
    if rows:
        fields = sorted({k for r in rows for k in r.keys()})
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)

    print(f"Wrote {out_path}")
    print(f"Wrote {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
