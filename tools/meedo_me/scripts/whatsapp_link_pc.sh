#!/usr/bin/env bash
# Meedo WhatsApp Linked Devices — run on a normal PC (home/office network).
# Do NOT run this on Cursor Cloud / AWS / GCP / Azure: WhatsApp rejects those IPs.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "Python 3 is required." >&2
  exit 1
fi

echo "== Meedo messaging: ensure fused runtime =="
"$PY" -m tools.meedo_me.runtime ensure

echo
echo "== Link WhatsApp (scan QR in phone: Linked devices → Link a device) =="
echo "   Scan within ~15 seconds of each QR refresh."
echo
"$PY" -m tools.meedo_me.runtime gateway channels login --channel whatsapp

echo
echo "== Status =="
"$PY" -m tools.meedo_me.runtime gateway channels status
