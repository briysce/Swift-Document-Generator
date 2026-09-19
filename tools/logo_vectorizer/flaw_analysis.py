"""Flaw analysis — measure how damaged the raster is, and correct proportionally.

Why the engine needs this
-------------------------
`idealize` treats raster defects as execution error and reconstructs the intent.
How hard it should push is not a constant. A 3000px master needs a light touch;
a 60px favicon dragged off a website needs aggressive correction, because almost
every wobble in it is quantization rather than design.

So measure the damage first, then set the correction strength from it. That is
the difference between "assume flaw when it looks like noise" and *knowing* the
source could not have represented the detail in question.

Why not BRISQUE
---------------
`imquality`'s BRISQUE is a no-reference quality model trained on natural
photographs, and flat vector artwork is far outside that distribution. Measured
on this repo's own anchors it ranks them backwards:

    clean swift_orange          171.46
    degraded downscale_jpeg      93.68
    degraded blur_crush          96.88

Lower is supposed to mean better, and 0-100 is the nominal range. It calls the
pristine logo the worst of the three. The package also needs three separate
compatibility shims to run at all on current scikit-image and scipy. It is
available through `brisque_score()` for comparison, but it is deliberately NOT
part of the degradation score — a backwards signal is worse than no signal.

Choosing the measures empirically
---------------------------------
The first cut of this module made exactly the mistake it accuses BRISQUE of. It
scored the clean anchor as MORE damaged (0.476) than blur_crush (0.306), because
three of its five measures invert on flat artwork:

  * posterization  — flat brand art legitimately has 2-3 colours, so "few
                     colours" reads as damage when it is the design.
  * staircase      — the clean anchors are hard-alpha renders, so 100% of their
                     boundary steps sit on the pixel grid. That is a property of
                     the render, not of damage. (It is still a useful *vector
                     quality* signal, and lives in `ideality` where it belongs.)
  * spectral cutoff — JPEG noise ADDS high-frequency energy, so a damaged image
                     measures as having more resolution, not less.

So the measures were chosen by testing candidates against the suite's known
severity ordering, across every slug, and keeping only what tracked it:

  ink_speckle       Share of ink components too small to be design. Compression
                    debris and broken strokes shed these; clean artwork has
                    none. Monotonic on every slug tested — swift_orange
                    0.00/0.71/0.91, gcm 0.00/0.80/0.96, propak 0.04/0.80/0.93,
                    trialta 0.00/0.71/0.95 for clean/mild/severe. This is the
                    backbone of the score.
  jpeg_blockiness   Energy concentrated on the 8x8 DCT grid: direct evidence of
                    compression, and of how hard.
  ela_energy        Error Level Analysis, the Forensically technique —
                    recompress and difference. Detail that survives
                    recompression unchanged was already gone.

Rejected after measurement, and deliberately not part of the score:
hf_noise (gcm clean 0.377 > its degraded 0.268), contour roughness (clean often
higher), chroma variance (saturates at 1.0 nearly everywhere), and BRISQUE.

Known limit of ink_speckle
--------------------------
Speckle is strong *positive* evidence — fragments that small are debris, not
design. Its absence proves much less. A source degraded only by clean
downsampling and mild JPEG, with no plate, halo or banding, sheds no fragments
and scores near zero despite real detail loss; measured that way a 0.18x / q40
rescale of a wordmark still reports ~0.10. So treat a high score as "definitely
damaged, correct aggressively" and a low score as "no damage detected here",
never as "pristine". Because the score only ever *raises* correction strength
from a safe baseline, a false low is a missed opportunity rather than a
regression — which is the right way round for a signal with this asymmetry.

Everything fails open: a missing optional tool degrades the report, never the run.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from dataclasses import dataclass, asdict, field
from pathlib import Path

import numpy as np
from PIL import Image


@dataclass
class FlawReport:
    ink_speckle: float
    jpeg_blockiness: float
    ela_energy: float
    edge_softness: float
    degradation: float
    provenance: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        return {
            k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()
        }

    @property
    def idealize_params(self) -> dict:
        """Correction strength implied by the measured damage.

        Interpolates between a light touch on clean artwork and aggressive
        correction on a destroyed source. The mapping is deliberately explicit
        rather than buried in the idealizer, so it can be inspected and argued
        with.
        """
        d = max(0.0, min(1.0, self.degradation))
        return {
            # Heavier supersampling buys sub-pixel boundary on a small source.
            "supersample": int(round(4 + 4 * d)),
            # Wider median window as quantization noise grows.
            "denoise": int(round(3 + 4 * d)) | 1,
            # A damaged source needs a blunter corner test: at low resolution a
            # real corner is rounded off, and every rounded spot would otherwise
            # be read as an intentional curve.
            "alphamax": round(1.0 + 0.334 * d, 3),
            # Let potrace merge harder — fewer, longer curves.
            "opttolerance": round(0.2 + 1.6 * d, 3),
            "turdsize": int(round(4 + 12 * d)),
        }


# --------------------------------------------------------------------------
# individual measures
# --------------------------------------------------------------------------


def _gray(arr: np.ndarray) -> np.ndarray:
    a = arr[:, :, :3].astype(np.float32) if arr.ndim == 3 else arr.astype(np.float32)
    return a.mean(axis=2) if a.ndim == 3 else a


def jpeg_blockiness(arr: np.ndarray) -> float:
    """Energy on the 8x8 grid relative to energy everywhere else."""
    g = _gray(arr)
    h, w = g.shape
    if h < 24 or w < 24:
        return 0.0
    dh = np.abs(np.diff(g, axis=0))
    dv = np.abs(np.diff(g, axis=1))
    rows = dh.mean(axis=1)
    cols = dv.mean(axis=0)

    def ratio(profile: np.ndarray) -> float:
        idx = np.arange(len(profile))
        on = profile[(idx % 8) == 7]
        off = profile[(idx % 8) != 7]
        if len(on) == 0 or len(off) == 0 or off.mean() < 1e-6:
            return 0.0
        return float(on.mean() / off.mean())

    r = (ratio(rows) + ratio(cols)) / 2.0
    # 1.0 means no grid preference; clamp the excess into 0..1.
    return float(max(0.0, min(1.0, (r - 1.0) / 1.5)))


def ela_energy(arr: np.ndarray, quality: int = 90) -> float:
    """Error Level Analysis: recompress and difference (Forensically's method).

    An already-compressed region barely changes when recompressed, so low
    residual means the detail was destroyed before we ever saw it.
    """
    rgb = arr[:, :, :3] if arr.ndim == 3 else np.dstack([arr] * 3)
    im = Image.fromarray(rgb.astype(np.uint8), "RGB")
    buf = io.BytesIO()
    try:
        im.save(buf, "JPEG", quality=quality)
        buf.seek(0)
        again = np.asarray(Image.open(buf).convert("RGB")).astype(np.float32)
    except Exception:
        return 0.0
    diff = np.abs(rgb.astype(np.float32) - again)
    return float(min(1.0, diff.mean() / 12.0))


def edge_softness(arr: np.ndarray) -> float:
    """Fraction of the boundary that is a soft ramp rather than a step."""
    if arr.ndim == 3 and arr.shape[2] == 4:
        a = arr[:, :, 3]
        soft = ((a > 24) & (a < 231)).sum()
        edge = ((a > 8) & (a < 247)).sum()
        return float(soft / edge) if edge else 0.0
    g = _gray(arr)
    gx = np.abs(np.diff(g, axis=1))
    if gx.size == 0:
        return 0.0
    strong = (gx > 40).sum()
    mid = ((gx > 8) & (gx <= 40)).sum()
    return float(mid / max(1, strong + mid))


# --------------------------------------------------------------------------
# optional / external
# --------------------------------------------------------------------------


def brisque_score(arr: np.ndarray) -> float | None:
    """imquality's BRISQUE, with the shims it needs on current deps.

    Provided for comparison only. See the module docstring: it ranks flat
    artwork backwards and is not part of `degradation`.
    """
    try:
        import scipy
        import skimage.color as C
        import skimage.transform as T

        if not hasattr(scipy, "ndarray"):
            scipy.ndarray = np.ndarray  # libsvm predates its removal

        _rs = T.rescale

        def _rescale(image, scale, **kw):
            if "multichannel" in kw:
                mc = kw.pop("multichannel")
                kw["channel_axis"] = -1 if (mc and getattr(image, "ndim", 2) == 3) else None
            return _rs(image, scale, **kw)

        T.rescale = _rescale
        _g = C.rgb2gray

        def _rgb2gray(a, *ar, **kw):
            a = np.asarray(a)
            return a if a.ndim == 2 else _g(a, *ar, **kw)

        C.rgb2gray = _rgb2gray

        import imquality.brisque as brisque

        rgb = arr[:, :, :3] if arr.ndim == 3 else np.dstack([arr] * 3)
        return float(brisque.score(Image.fromarray(rgb.astype(np.uint8), "RGB")))
    except Exception:
        return None


def provenance(path: Path | None) -> dict:
    """ExifTool metadata: how this file was actually produced."""
    if path is None or not Path(path).is_file():
        return {}
    exe = shutil.which("exiftool")
    if not exe:
        return {}
    try:
        r = subprocess.run(
            [exe, "-json", "-n", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return {}
        data = json.loads(r.stdout)
    except Exception:
        return {}
    if not isinstance(data, list) or not data:
        return {}
    d = data[0]
    keep = (
        "FileType", "ImageWidth", "ImageHeight", "BitDepth", "ColorType",
        "Software", "Creator", "ProfileDescription", "JPEGQualityEstimate",
        "EncodingProcess", "YCbCrSubSampling", "Compression",
    )
    return {k: d[k] for k in keep if k in d}


def detect_logo_regions(path: Path) -> list | None:
    """Optional Logodetect pass — locate logo regions inside a larger image.

    Fail-open by design, exactly like the Real-ESRGAN / GFPGAN hooks in
    `raster_polish`. Logodetect needs torch plus pretrained weights that it does
    not bundle (its `models/` directory ships empty), so on a checkout without
    them this returns None and the caller proceeds with the whole image.
    """
    try:
        from logodetect.recognizer import Recognizer  # type: ignore
    except Exception:
        return None
    try:
        rec = Recognizer()
        return rec.predict_image(str(path))  # pragma: no cover - needs weights
    except Exception:
        return None


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------


def ink_speckle(arr: np.ndarray) -> float:
    """Share of ink components too small to be intentional.

    The single most reliable damage signal for flat artwork: compression debris
    and broken strokes shed tiny fragments that no designer drew, and clean
    artwork has essentially none.
    """
    try:
        import cv2
    except Exception:
        return 0.0
    if arr.ndim != 3 or arr.shape[2] < 4:
        return 0.0
    m = (arr[:, :, 3] >= 128).astype(np.uint8)
    if m.sum() < 64:
        return 0.0
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return 0.0
    areas = stats[1:, cv2.CC_STAT_AREA]
    if len(areas) == 0:
        return 0.0
    return float((areas < 64).sum() / len(areas))


def analyze(arr: np.ndarray, path: Path | None = None) -> FlawReport:
    speck = ink_speckle(arr)
    blk = jpeg_blockiness(arr)
    ela = ela_energy(arr)

    # Speckle carries the score because it is the measure that actually tracked
    # severity across every slug; blockiness and ELA refine it. Nothing else
    # earned a weight — see the module docstring for what was tried and cut.
    degradation = float(min(1.0, 0.70 * speck + 0.20 * blk + 0.10 * (1.0 - ela)))

    return FlawReport(
        ink_speckle=speck,
        jpeg_blockiness=blk,
        ela_energy=ela,
        edge_softness=edge_softness(arr),
        degradation=degradation,
        provenance=provenance(path),
    )


__all__ = [
    "FlawReport",
    "analyze",
    "ink_speckle",
    "jpeg_blockiness",
    "ela_energy",
    "edge_softness",
    "brisque_score",
    "provenance",
    "detect_logo_regions",
]
