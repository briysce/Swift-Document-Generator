# Full logo QA: copy assets, download missing carriers, generate PDFs, restore.
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$log = Join-Path $repo 'qa_logs\batch_logo_qa_run.log'
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
$flutter = Join-Path $repo '.tools\flutter\bin\flutter.bat'

function Log($msg) {
  $line = "$(Get-Date -Format o) $msg"
  Add-Content -Path $log -Value $line
  Write-Host $line
}

Log '=== batch logo QA start ==='

# Copy user-attached logos into customer_logos (skip if already present).
$assets = Join-Path $env:USERPROFILE '.cursor\projects\c-Users-Brice-Projects-swift-document-generator\assets'
$destDir = Join-Path $repo 'customer_logos'
$map = @{
  '*trialta*'                = 'Trialta Projects.png'
  '*wpw-logo*'               = 'WPW Pipeline and Facility Construction.png'
  '*Spartan_Delta*'          = 'Spartan Delta Corp.png'
  '*WHITECAP*'               = 'WHITECAP RESOURCES INC.png'
  '*Worley_logo*'            = 'Worley logo.png'
}

foreach ($pattern in $map.Keys) {
  $target = Join-Path $destDir $map[$pattern]
  if (Test-Path $target) { continue }
  $src = Get-ChildItem -Path $assets -Filter $pattern -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($src) {
    Copy-Item $src.FullName $target -Force
    Log "Copied $($src.Name) -> $($map[$pattern])"
  }
}

# Download carrier logos (best-effort).
$downloads = @(
  @{
    Name = 'BFL Fabricators.png'
    Url  = 'https://www.bflfabricators.com/wp-content/uploads/2019/03/BFL-Logo-2019.png'
  },
  @{
    Name = 'DHV.png'
    Url  = 'https://images.seeklogo.com/logo-png/40/1/dhv-industries-logo-png_seeklogo-407414.png'
  }
)

foreach ($d in $downloads) {
  $out = Join-Path $destDir $d.Name
  if (-not (Test-Path $out)) {
    try {
      Invoke-WebRequest -Uri $d.Url -OutFile $out -UseBasicParsing -TimeoutSec 60
      Log "Downloaded $($d.Name)"
    } catch {
      Log "WARN download $($d.Name): $($_.Exception.Message)"
    }
  }
}

# Murray's — reuse existing file or download from known source.
$murrays = Join-Path $destDir "Murray's Trucking Edmonton.png"
if (-not (Test-Path $murrays)) {
  $existing = Join-Path $destDir 'murrays_trucking.png'
  if (Test-Path $existing) {
    Copy-Item $existing $murrays -Force
    Log "Copied murrays_trucking.png -> Murray's Trucking Edmonton.png"
  }
}

Push-Location (Join-Path $repo 'mobile')
try {
  Log 'Running batch_logo_qa_test.dart ...'
  & $flutter test test/batch_logo_qa_test.dart --reporter expanded 2>&1 | Tee-Object -FilePath $log -Append
  if ($LASTEXITCODE -ne 0) { Log "FAIL flutter test exit $LASTEXITCODE" }
  else { Log 'OK batch_logo_qa_test.dart' }
} finally {
  Pop-Location
}

Log 'Running batch_logo_restore.py ...'
python (Join-Path $repo 'scripts\batch_logo_restore.py') 2>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { Log "FAIL restore exit $LASTEXITCODE" }
else { Log 'OK batch_logo_restore.py' }

Log '=== batch logo QA done ==='
