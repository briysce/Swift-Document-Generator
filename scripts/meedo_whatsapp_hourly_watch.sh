#!/usr/bin/env bash
# Fallback hourly sender when OpenClaw automations or system cron are unavailable.
# Sleeps until the next top-of-hour, then runs progress --send. Safe to run
# alongside OpenClaw announce (progress --send is the direct path; OpenClaw
# automations use print-only wrapper — they do not double-send).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/home/ubuntu/.nvm/versions/node/v24.21.0/bin:/home/ubuntu/.local/bin:$PATH"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
cd "$ROOT"
LOG=/tmp/meedo_whatsapp_hourly.log
while true; do
  now=$(date +%s)
  # next top of hour
  next=$(( (now / 3600 + 1) * 3600 ))
  sleep $(( next - now ))
  # refresh gateway token if present
  if [[ -f "$HOME/.openclaw/openclaw.json" ]]; then
    export OPENCLAW_GATEWAY_TOKEN="$(python3 -c "import json;print(json.load(open('$HOME/.openclaw/openclaw.json'))['gateway']['auth']['token'])" 2>/dev/null || true)"
  fi
  {
    echo "==== $(date -u +%Y-%m-%dT%H:%M:%SZ) hourly send ===="
    python3 -m tools.meedo_me.progress --hours 1 --send || true
  } >>"$LOG" 2>&1
done
