"""
Hierarchical raster polish / upscale before vectorization.

Goal: clean and enlarge soft / low-res logo rasters so sectional Bezier,
Inkscape, and VTracer see crisp fills — without inventing brand geometry.

Suggested order (any step fail-opens to the next):

  1. Classical "Gigapixel / Upscayl / Remacri / UltraSharp"-inspired
     OpenCV path — edge-aware upscale, bilateral denoise, unsharp.
  2. Real-ESRGAN with multiple Upscayl-compatible weights when present
     (x4plus, anime, Remacri, UltraSharp, ultramix, …).
  3. GFPGAN (optional detail pass) when installed + weights present.
  4. InstructIR / DeOldify when those packages are importable.
  5. Upscayl CLI on PATH (uses the same family of RRDB weights).

Neural steps are **optional**. Missing torch / weights / CLI never fails the
engine — callers always get a best-effort RGBA (possibly unchanged).

This module deliberately does **not** call cloud AI; token exhaustion cannot
touch this path.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import cv2
import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
WEIGHTS_DIR = ROOT / ".cache" / "realesrgan"

# Upscayl / community RRDB weight names we look for (any subset is enough).
# Download into .cache/realesrgan/ — never required at import time.
UPSCAYL_STYLE_WEIGHTS: tuple[tuple[str, str], ...] = (
    # (filename, role)
    ("RealESRGAN_x4plus.pth", "realesrgan_x4plus"),
    ("RealESRGAN_x4plus_anime_6B.pth", "realesrgan_anime"),
    ("4x_foolhardy_Remacri.pth", "remacri"),
    ("4x-UltraSharp.pth", "ultrasharp"),
    ("4x_NMKD-Siax_200k.pth", "nmkd_siax"),
    ("4x_NMKD-Superscale-SP_178000_G.pth", "nmkd_superscale"),
    ("4x-AnimeSharp.pth", "animesharp"),
    ("ultramix_balanced.pth", "ultramix"),
)


@dataclass
class PolishStep:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class PolishResult:
    image: Image.Image
    steps: list[PolishStep] = field(default_factory=list)
    engine: str = "identity"

    @property
    def touched(self) -> bool:
        return any(s.ok for s in self.steps if s.name != "identity")


def _rgba(img: Image.Image) -> Image.Image:
    return img.convert("RGBA")


def _to_bgr_alpha(arr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if arr.shape[2] == 4:
        rgb, a = arr[:, :, :3], arr[:, :, 3]
    else:
        rgb, a = arr[:, :, :3], np.full(arr.shape[:2], 255, dtype=np.uint8)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    return bgr, a


def _from_bgr_alpha(bgr: np.ndarray, alpha: np.ndarray) -> Image.Image:
    if alpha.shape[:2] != bgr.shape[:2]:
        alpha = cv2.resize(
            alpha, (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_LANCZOS4
        )
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(np.dstack([rgb, alpha]), "RGBA")


# ---------------------------------------------------------------------------
# 1) Classical Gigapixel / Remacri / UltraSharp-inspired path
# ---------------------------------------------------------------------------

def classical_gigapixel_polish(
    img: Image.Image,
    *,
    target_long_side: int | None = None,
) -> tuple[Image.Image, PolishStep]:
    """
    Local approximation of commercial upscalers' *principles*:

    - Upscayl / Remacri: denoise flat regions before sharpening
    - UltraSharp: strong but edge-limited unsharp
    - Topaz Gigapixel-like: separate alpha, multi-stage enlarge, suppress
      ringing on fills
    """
    arr = np.asarray(_rgba(img), dtype=np.uint8)
    bgr, alpha = _to_bgr_alpha(arr)
    h, w = bgr.shape[:2]
    long_side = max(h, w)
    scale = 1.0
    if target_long_side and long_side > 0 and long_side < target_long_side:
        scale = min(4.0, float(target_long_side) / float(long_side))
    if scale > 1.01:
        nh, nw = int(round(h * scale)), int(round(w * scale))
        bgr = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_LANCZOS4)
        alpha = cv2.resize(alpha, (nw, nh), interpolation=cv2.INTER_LANCZOS4)

    # Remacri-like: bilateral on chroma, preserve edges.
    den = cv2.bilateralFilter(bgr, d=5, sigmaColor=35, sigmaSpace=5)
    # Keep high-frequency edges from the sharp resize.
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 140)
    edge_m = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1) > 0
    mixed = den.copy()
    mixed[edge_m] = bgr[edge_m]

    # UltraSharp-like: unsharp with clip to avoid halos on flat fills.
    blur = cv2.GaussianBlur(mixed, (0, 0), sigmaX=1.1)
    sharp = cv2.addWeighted(mixed, 1.45, blur, -0.45, 0)
    # Suppress sharpening where alpha is soft or near empty.
    soft = alpha < 200
    sharp[soft] = mixed[soft]

    out = _from_bgr_alpha(sharp, alpha)
    return out, PolishStep(
        name="classical_gigapixel",
        ok=True,
        detail=f"scale={scale:.2f} remacri-bilateral+ultrasharp-unsharp",
    )


# ---------------------------------------------------------------------------
# 2) Real-ESRGAN + Upscayl-family weights
# ---------------------------------------------------------------------------

def _list_available_weights() -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    search_dirs = [
        WEIGHTS_DIR,
        ROOT / ".cache" / "upscayl",
        Path.home() / ".cache" / "realesrgan",
    ]
    env = os.environ.get("LOGO_SR_WEIGHTS_DIR", "").strip()
    if env:
        search_dirs.insert(0, Path(env))
    for directory in search_dirs:
        if not directory.is_dir():
            continue
        for fname, role in UPSCAYL_STYLE_WEIGHTS:
            p = directory / fname
            if p.is_file() and p.stat().st_size > 1_000_000:
                found.append((p, role))
        # Also accept any *.pth so user-dropped Upscayl models work.
        for p in sorted(directory.glob("*.pth")):
            if p.stat().st_size > 1_000_000 and all(p != f for f, _ in found):
                found.append((p, p.stem.lower()))
    return found


def _realesrgan_upscale_with_weights(
    bgr: np.ndarray, weights: Path, *, tile: int = 0
) -> np.ndarray:
    import sys

    import torch
    import torchvision.transforms.functional as tvF

    sys.modules.setdefault("torchvision.transforms.functional_tensor", tvF)
    from basicsr.archs.rrdbnet_arch import RRDBNet
    from realesrgan import RealESRGANer

    # Anime 6B uses fewer blocks; detect by filename.
    name = weights.name.lower()
    num_block = 6 if "anime_6b" in name or "anime-6b" in name else 23
    model = RRDBNet(
        num_in_ch=3,
        num_out_ch=3,
        num_feat=64,
        num_block=num_block,
        num_grow_ch=32,
        scale=4,
    )
    device = "cuda" if torch.cuda.is_available() else "cpu"
    upsampler = RealESRGANer(
        scale=4,
        model_path=str(weights),
        model=model,
        tile=tile,
        tile_pad=10,
        pre_pad=0,
        half=False,
        device=device,
    )
    out, _ = upsampler.enhance(bgr, outscale=4)
    return out


def realesrgan_family_polish(
    img: Image.Image,
    *,
    preferred_roles: Sequence[str] = ("ultrasharp", "remacri", "realesrgan_x4plus", "ultramix"),
) -> tuple[Image.Image | None, PolishStep]:
    weights = _list_available_weights()
    if not weights:
        return None, PolishStep(
            name="realesrgan_family",
            ok=False,
            detail="no Upscayl/RealESRGAN weights in .cache/realesrgan",
        )
    # Prefer UltraSharp / Remacri / x4plus when present.
    role_rank = {r: i for i, r in enumerate(preferred_roles)}
    weights.sort(key=lambda pair: role_rank.get(pair[1], 100))
    arr = np.asarray(_rgba(img), dtype=np.uint8)
    bgr, alpha = _to_bgr_alpha(arr)
    last_err = ""
    for path, role in weights[:3]:
        try:
            up = _realesrgan_upscale_with_weights(bgr, path)
            if alpha is not None:
                alpha = cv2.resize(
                    alpha, (up.shape[1], up.shape[0]), interpolation=cv2.INTER_LANCZOS4
                )
            return _from_bgr_alpha(up, alpha), PolishStep(
                name=f"realesrgan:{role}",
                ok=True,
                detail=str(path.name),
            )
        except Exception as exc:  # noqa: BLE001
            last_err = f"{role}: {exc}"
            continue
    return None, PolishStep(
        name="realesrgan_family",
        ok=False,
        detail=last_err or "all weights failed",
    )


# ---------------------------------------------------------------------------
# 3) GFPGAN
# ---------------------------------------------------------------------------

def gfpgan_polish(img: Image.Image) -> tuple[Image.Image | None, PolishStep]:
    """Optional GFPGAN pass. Flat logo fills usually skip; useful for soft photo marks."""
    try:
        from gfpgan import GFPGANer  # type: ignore
    except Exception as exc:
        return None, PolishStep(name="gfpgan", ok=False, detail=f"import: {exc}")

    # Prefer a cached GFPGAN weight if the user dropped one.
    candidates = [
        WEIGHTS_DIR / "GFPGANv1.4.pth",
        WEIGHTS_DIR / "GFPGANv1.3.pth",
        Path.home() / ".cache" / "gfpgan" / "GFPGANv1.4.pth",
    ]
    model_path = next((p for p in candidates if p.is_file()), None)
    if model_path is None:
        return None, PolishStep(name="gfpgan", ok=False, detail="weights missing")

    arr = np.asarray(_rgba(img), dtype=np.uint8)
    bgr, alpha = _to_bgr_alpha(arr)
    try:
        restorer = GFPGANer(
            model_path=str(model_path),
            upscale=1,
            arch="clean",
            channel_multiplier=2,
            bg_upsampler=None,
        )
        _c, _r, restored = restorer.enhance(
            bgr, has_aligned=False, only_center_face=False, paste_back=True
        )
        if restored is None:
            return None, PolishStep(name="gfpgan", ok=False, detail="empty output")
        if restored.shape[:2] != alpha.shape[:2]:
            alpha = cv2.resize(
                alpha,
                (restored.shape[1], restored.shape[0]),
                interpolation=cv2.INTER_LANCZOS4,
            )
        return _from_bgr_alpha(restored, alpha), PolishStep(
            name="gfpgan", ok=True, detail=model_path.name
        )
    except Exception as exc:  # noqa: BLE001
        return None, PolishStep(name="gfpgan", ok=False, detail=str(exc))


# ---------------------------------------------------------------------------
# 4) InstructIR / DeOldify (optional packages)
# ---------------------------------------------------------------------------

def instructir_polish(img: Image.Image) -> tuple[Image.Image | None, PolishStep]:
    try:
        import instructir  # type: ignore  # noqa: F401
    except Exception as exc:
        return None, PolishStep(name="instructir", ok=False, detail=f"import: {exc}")
    # Official InstructIR APIs vary by install; keep fail-open stub that
    # documents the hook without inventing a broken call.
    return None, PolishStep(
        name="instructir",
        ok=False,
        detail="package present but no stable logo API wired — skip",
    )


def deoldify_polish(img: Image.Image) -> tuple[Image.Image | None, PolishStep]:
    """Colorize only when the mark is near-gray; logos with chroma skip."""
    arr = np.asarray(_rgba(img), dtype=np.uint8)
    rgb = arr[:, :, :3].astype(np.int16)
    a = arr[:, :, 3] > 40
    if not a.any():
        return None, PolishStep(name="deoldify", ok=False, detail="empty")
    sat = (rgb.max(axis=2) - rgb.min(axis=2))[a]
    if float(np.median(sat)) > 18:
        return None, PolishStep(
            name="deoldify", ok=False, detail="chroma present — skip colorize"
        )
    try:
        # DeOldify artistic/stable weights are heavy; hook only if user installed.
        from deoldify.visualize import get_image_colorizer  # type: ignore
    except Exception as exc:
        return None, PolishStep(name="deoldify", ok=False, detail=f"import: {exc}")
    try:
        colorizer = get_image_colorizer(artistic=True)
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.png"
            dst_dir = Path(td) / "out"
            dst_dir.mkdir()
            _rgba(img).save(src)
            out_path = colorizer.plot_transformed_image(
                path=str(src), results_dir=dst_dir, render_factor=35, watermarked=False
            )
            if not out_path:
                return None, PolishStep(name="deoldify", ok=False, detail="no output")
            colored = Image.open(out_path).convert("RGBA")
            # Reattach source alpha.
            ca = np.asarray(colored)
            ca[:, :, 3] = arr[:, :, 3] if ca.shape[:2] == arr.shape[:2] else cv2.resize(
                arr[:, :, 3], (ca.shape[1], ca.shape[0]), interpolation=cv2.INTER_LANCZOS4
            )
            return Image.fromarray(ca, "RGBA"), PolishStep(
                name="deoldify", ok=True, detail="gray→color"
            )
    except Exception as exc:  # noqa: BLE001
        return None, PolishStep(name="deoldify", ok=False, detail=str(exc))


# ---------------------------------------------------------------------------
# 5) Upscayl CLI
# ---------------------------------------------------------------------------

def upscayl_cli_polish(img: Image.Image) -> tuple[Image.Image | None, PolishStep]:
    exe = shutil.which("upscayl-bin") or shutil.which("upscayl")
    if not exe:
        return None, PolishStep(name="upscayl_cli", ok=False, detail="not on PATH")
    with tempfile.TemporaryDirectory() as td:
        inp = Path(td) / "in.png"
        out = Path(td) / "out.png"
        _rgba(img).save(inp)
        try:
            subprocess.run(
                [exe, "-i", str(inp), "-o", str(out), "-s", "4"],
                check=True,
                capture_output=True,
                text=True,
                timeout=180,
            )
        except Exception as exc:  # noqa: BLE001
            return None, PolishStep(name="upscayl_cli", ok=False, detail=str(exc))
        if not out.is_file():
            return None, PolishStep(name="upscayl_cli", ok=False, detail="no output")
        return Image.open(out).convert("RGBA"), PolishStep(
            name="upscayl_cli", ok=True, detail=exe
        )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def polish_raster(
    img: Image.Image,
    *,
    target_long_side: int | None = 2048,
    use_neural: bool = True,
    use_gfpgan: bool = False,
    use_instructir: bool = True,
    use_deoldify: bool = True,
    use_upscayl_cli: bool = True,
) -> PolishResult:
    """
    Run the hierarchical polish chain. Always returns an image.

    Neural backends are skipped when deps/weights are missing (fail-open).
    Classical Gigapixel/Remacri/UltraSharp-inspired path always runs when
    enlargement or soft edges are needed.
    """
    work = _rgba(img)
    steps: list[PolishStep] = []
    engine = "identity"
    w, h = work.size
    needs_enlarge = target_long_side is not None and max(w, h) < int(target_long_side)

    # Classical first — cheap, always available, Upscayl/Gigapixel principles.
    if needs_enlarge:
        work, step = classical_gigapixel_polish(work, target_long_side=target_long_side)
        steps.append(step)
        if step.ok:
            engine = step.name
    else:
        # Light Remacri/UltraSharp denoise+sharpen without enlarge.
        work, step = classical_gigapixel_polish(work, target_long_side=None)
        steps.append(step)
        if step.ok:
            engine = step.name

    if use_neural:
        neural, step = realesrgan_family_polish(work)
        steps.append(step)
        if neural is not None and step.ok:
            work = neural
            engine = step.name

    if use_upscayl_cli and engine.startswith("classical"):
        cli_img, step = upscayl_cli_polish(work)
        steps.append(step)
        if cli_img is not None and step.ok:
            work = cli_img
            engine = step.name

    if use_gfpgan:
        g_img, step = gfpgan_polish(work)
        steps.append(step)
        if g_img is not None and step.ok:
            work = g_img
            engine = f"{engine}+gfpgan"

    if use_instructir:
        _, step = instructir_polish(work)
        steps.append(step)

    if use_deoldify:
        d_img, step = deoldify_polish(work)
        steps.append(step)
        if d_img is not None and step.ok:
            work = d_img
            engine = f"{engine}+deoldify"

    if not steps:
        steps.append(PolishStep(name="identity", ok=True, detail="unchanged"))

    return PolishResult(image=work, steps=steps, engine=engine)


def polish_file(
    input_path: Path,
    output_path: Path,
    **kwargs: object,
) -> PolishResult:
    img = Image.open(input_path)
    result = polish_raster(img, **kwargs)  # type: ignore[arg-type]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.image.save(output_path)
    return result


def describe_available_backends() -> dict[str, object]:
    """Introspection for docs / training lessons."""
    return {
        "classical_gigapixel": True,
        "realesrgan_weights": [role for _, role in _list_available_weights()],
        "gfpgan": (WEIGHTS_DIR / "GFPGANv1.4.pth").is_file(),
        "upscayl_cli": bool(shutil.which("upscayl-bin") or shutil.which("upscayl")),
        "instructir": _can_import("instructir"),
        "deoldify": _can_import("deoldify"),
        "vtracer": _can_import("vtracer"),
        "inkscape": bool(shutil.which("inkscape")),
    }


def _can_import(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False
