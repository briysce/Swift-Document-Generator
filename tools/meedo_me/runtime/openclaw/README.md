# Meedo-Me OpenClaw runtime (fused)

This directory owns the OpenClaw Node packages Meedo uses for WhatsApp and
gateway messaging. It is **not** a second product.

- Install: `python -m tools.meedo_me.runtime ensure` (runs `npm ci` here)
- Run: `python -m tools.meedo_me.runtime openclaw …`
- State/config live under the Meedo data dir (`OPENCLAW_HOME`), not a global
  `~/.openclaw` install you manage by hand.

Do **not** `npm install -g openclaw` on cloud agents or the PC.
