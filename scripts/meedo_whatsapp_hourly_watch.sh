#!/usr/bin/env bash
# Fallback digest printer when Meedo messaging automations are unavailable.
# NEVER --send here: the gateway announce automation delivers the printed
# digest. A parallel --send double-messages the phone.
# Register with: python3 -m tools.meedo_me.connect whatsapp
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/home/ubuntu/.nvm/versions/node/v24.21.0/bin:/home/ubuntu/.local/bin:$PATH"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
cd "$ROOT"
LOG=/tmp/meedo_whatsapp_hourly.log
# Meedo-owned messaging home (fused runtime); legacy ~/.openclaw only as fallback.
MEEDO_OC_HOME="${OPENCLAW_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/Meedo-Me/openclaw}"
while true; do
  now=$(date +%s)
  next=$(( (now / 3600 + 1) * 3600 ))
  sleep $(( next - now ))
  cfg=""
  if [[ -f "$MEEDO_OC_HOME/openclaw.json" ]]; then
    cfg="$MEEDO_OC_HOME/openclaw.json"
  elif [[ -f "$HOME/.openclaw/openclaw.json" ]]; then
    cfg="$HOME/.openclaw/openclaw.json"
  fi
  if [[ -n "$cfg" ]]; then
    export OPENCLAW_GATEWAY_TOKEN="$(python3 -c "import json;print(json.load(open('$cfg'))['gateway']['auth']['token'])" 2>/dev/null || true)"
  fi
  {
    echo "==== $(date -u +%Y-%m-%dT%H:%M:%SZ) hourly digest (print-only; announce delivers) ===="
    # Print only. Delivery is Meedo messaging announce on meedo-*-hourly-whatsapp.
    python3 -m tools.meedo_me.progress --hours 1 || true
  } >>"$LOG" 2>&1
done
