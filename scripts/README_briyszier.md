# Logo tooling lives in-repo again

Raster→SVG restore/vectorize (`tools/logo_vectorizer`, `scripts/logo_vectorize.py`,
`scripts/logo_restore_improve_loop.py`, `logo_restorer.py`, etc.) is **back in
this repository** and is the shared engine for **Swift Document Generator** and
**briyszier**.

## Unified pipeline (`tools.logo_vectorizer.engine`)

```
prepare/knockout
  → optional polish (Gigapixel/Upscayl/Remacri/UltraSharp-inspired + Real-ESRGAN family)
  → sectional Bezier (DejaType per-element anchors)  OR  VTracer/Inkscape ensemble
  → Real-ESRGAN SR fallback
  → Lanczos conservator
  → optional Gemini (opt-in only; fail-open on token exhaustion)
```

Probe what this machine can run:

```powershell
python -c "from tools.logo_vectorizer import probe_capabilities, pipeline_stages; print(pipeline_stages()); print(probe_capabilities().as_dict())"
```

## Quality locks

| Path | Rule |
|------|------|
| Restore improve-loop (Swift) | mean vectorize ≥ **0.9269**; `scoop_residual=False`; cairosvg rasterize; no Chrome dependency |
| Document SVG | `scoop_residual=True` + recreate tuning; true-ink IoU ~**0.997** @ native via cairosvg |
| AI | Out-of-tokens / quota → local fallbacks; never weaken preprocess hints |

## Commands

```powershell
python -m tools.logo_vectorizer -i logo.png -o out.svg --sectional --decomposer swift-supply --render-png out.png
python scripts/logo_vectorize.py in.png out.png --min-height 1200
python scripts/logo_vectorize.py in.png out.png --polish   # pre-vectorize polish
python scripts/logo_restore_improve_loop.py --engines baseline,vectorize --top 8
$env:LOGO_NO_CHROME=1  # skip Chrome entirely (recommended in CI/cloud)
```

Drop Upscayl-compatible weights into `.cache/realesrgan/` (`4x-UltraSharp.pth`,
`4x_foolhardy_Remacri.pth`, `RealESRGAN_x4plus.pth`, …) when available — the
engine fail-opens without them.
