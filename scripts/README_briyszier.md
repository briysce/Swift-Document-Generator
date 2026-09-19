# Logo tooling lives in-repo again

Raster→SVG restore/vectorize (`tools/logo_vectorizer`, `scripts/logo_vectorize.py`,
`scripts/logo_restore_improve_loop.py`, etc.) is **back in this repository**.

briyszier remains the standalone Windows WinForms shell for interactive use; its
`engine/` package was forked from this tree. Gains from briyszier (sectional
Bezier recreate, IoU ≥ 0.975 gate, customer recreate) are ported back here so
Document Generator's `LogoRestorer` / `LogoVectorize` stay formidable.

```powershell
python -m tools.logo_vectorizer -i logo.png -o out.svg --sectional --decomposer swift-supply --render-png out.png
python scripts/logo_restore_improve_loop.py --engines baseline,vectorize --top 8
```
