# Third-party software in Meedo-Me

Meedo-Me includes and builds on open-source software. Product branding is
**Meedo-Me** only. Upstream project names appear here for license compliance
and developer credit — not as co-branded products in the app UI.

## Jan (desktop shell)

- Project: Jan — https://github.com/menloresearch/jan
- Copyright: Menlo Research
- License: Apache License 2.0 (see `LICENSE` / Meedo-Me `NOTICE`)
- Role: Meedo-Me’s desktop face is a fork of Jan. User-visible marks (logos,
  window titles, publisher strings) are Meedo-Me’s; technical package scopes
  (`@janhq/*`), Hugging Face model ids, and code-internal string compares may
  still contain the upstream name where renaming would break the app.

## OpenClaw (messaging gateway)

- Package: `openclaw` / `@openclaw/whatsapp` (npm)
- License: see the package’s LICENSE after `python -m tools.meedo_me.runtime ensure`
- Role: Fused **Meedo messaging runtime** under `tools/meedo_me/runtime/openclaw`.
  Agents must not install a separate global OpenClaw product. The npm package
  name is a technical dependency id, not Meedo product branding.

## Ollama and other local models

Optional local inference; trademarks belong to their owners. Meedo-Me does not
claim them.
