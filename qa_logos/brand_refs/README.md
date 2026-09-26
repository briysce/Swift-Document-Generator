# Brand references for logo QA

Web + official-site references for logos under test. Used as a visual and
structural north star when scoring restore/vectorize (especially Arc tagline,
Propak Services i-dot, GCM Modification, Trialta TRI/ALTA split).

## Refresh

```bash
# Same Serper.dev Images API as Flutter LogoFinder — key in gitignored .env
python scripts/serper_logo_brand_refs.py --journal
python scripts/serper_logo_brand_refs.py --slugs arc,propak --journal
# Official URLs only (no Serper key):
python scripts/serper_logo_brand_refs.py --official-only --journal
```

`catalog.json` is tracked. Per-slug image downloads are gitignored.
