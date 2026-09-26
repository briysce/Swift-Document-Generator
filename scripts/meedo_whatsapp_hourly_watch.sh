#!/usr/bin/env bash
# Fallback digest printer when OpenClaw automations are unavailable.
# NEVER --send here: OpenClaw's announce automation delivers the printed
# digest. A parallel --send double-messages the phone (Claude's merge fix).
# If no OpenClaw automation is registered, register one with:
#   python3 -m tools.meedo_me.connect whatsapp
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/home/ubuntu/.nvm/versions/node/v24.21.0/bin:/home/ubuntu/.local/bin:$PATH"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
cd "$ROOT"
LOG=/tmp/meedo_whatsapp_hourly.log
while true; do
  now=$(date +%s)
  next=$(( (now / 3600 + 1) * 3600 ))
  sleep $(( next - now ))
  if [[ -f "$HOME/.openclaw/openclaw.json" ]]; then
    export OPENCLAW_GATEWAY_TOKEN="$(python3 -c "import json;print(json.load(open('$HOME/.openclaw/openclaw.json'))['gateway']['auth']['token'])" 2>/dev/null || true)"
  fi
  {
    echo "==== $(date -u +%Y-%m-%dT%H:%M:%SZ) hourly digest (print-only; announce delivers) ===="
    # Print only. Delivery is OpenClaw announce on meedo-*-hourly-whatsapp.
    python3 -m tools.meedo_me.progress --hours 1 || true
  } >>"$LOG" 2>&1
done
