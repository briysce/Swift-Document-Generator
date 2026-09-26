# Meedo WhatsApp Linked Devices — run on a normal Windows PC (home/office network).
# Do NOT run this on Cursor Cloud / AWS / GCP / Azure: WhatsApp rejects those IPs.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) { throw "Python 3 is required on PATH (python or py)." }

Write-Host "== Meedo messaging: ensure fused runtime =="
& $py.Source -m tools.meedo_me.runtime ensure
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "== Link WhatsApp (phone: Linked devices -> Link a device) =="
Write-Host "   Scan within ~15 seconds of each QR refresh."
Write-Host ""
& $py.Source -m tools.meedo_me.runtime gateway channels login --channel whatsapp
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "== Status =="
& $py.Source -m tools.meedo_me.runtime gateway channels status
exit $LASTEXITCODE
