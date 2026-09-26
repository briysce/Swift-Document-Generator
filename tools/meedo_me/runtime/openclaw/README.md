# Meedo messaging runtime (fused)

This directory is Meedo-Me’s **messaging / WhatsApp gateway** runtime. It is
not a second product to install globally.

- Install: `python -m tools.meedo_me.runtime ensure`
- Run: `python -m tools.meedo_me.runtime gateway …`  
  (`openclaw` remains a technical alias that forwards to the upstream CLI)
- State: Meedo data dir (`OPENCLAW_HOME` / XDG `Meedo-Me/openclaw`)

Upstream Node packages are pinned in `package.json` for license and
reproducibility. See `tools/meedo_me/THIRD_PARTY.md`. Do **not**
`npm install -g openclaw`.
