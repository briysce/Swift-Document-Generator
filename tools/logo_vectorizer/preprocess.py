"""Preprocessing variants for ensemble tracing."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter


@dataclass(frozen=True)
class PreprocessConfig:
    upscale: int = 4
    blur_radius: float = 1.2
    alpha_threshold: int = 80
    min_area: float = 500

    def key(self) -> str:
        return f"u{self.upscale}_b{self.blur_radius:.1f}_t{self.alpha_threshold}_a{self.min_area:.0f}"


DEFAULT_VARIANTS: tuple[PreprocessConfig, ...] = (
    PreprocessConfig(upscale=4, blur_radius=1.2, alpha_threshold=80, min_area=500),
    PreprocessConfig(upscale=4, blur_radius=0.8, alpha_threshold=72, min_area=400),
    PreprocessConfig(upscale=3, blur_radius=1.0, alpha_threshold=80, min_area=350),
    PreprocessConfig(upscale=2, blur_radius=1.4, alpha_threshold=88, min_area=250),
)


def build_mask(img: Image.Image, cfg: PreprocessConfig) -> tuple[np.ndarray, tuple[int, int], tuple[int, int]]:
    """Return binary letter mask, trace size, source size."""
    source_size = img.size
    if cfg.upscale > 1:
        work = img.resize(
            (img.width * cfg.upscale, img.height * cfg.upscale),
            Image.Resampling.LANCZOS,
        )
    else:
        work = img

    alpha = np.array(work.split()[3], dtype=np.uint8)
    alpha_img = Image.fromarray(alpha)
    if cfg.blur_radius > 0:
        alpha_img = alpha_img.filter(ImageFilter.GaussianBlur(radius=cfg.blur_radius))
    letter = (np.array(alpha_img) >= cfg.alpha_threshold).astype(np.uint8) * 255
    return letter, work.size, source_size


def potrace_binary(mask: np.ndarray) -> Image.Image:
    from PIL import Image as PILImage

    return PILImage.fromarray(255 - mask, mode="L")


def vtracer_rgb(mask: np.ndarray) -> np.ndarray:
    letter = mask >= 128
    rgb = np.full((mask.shape[0], mask.shape[1], 3), 255, dtype=np.uint8)
    rgb[letter] = 0
    return rgb
