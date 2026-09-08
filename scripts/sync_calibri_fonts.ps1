# Copy Calibri from Windows Fonts into project font folders.
# Calibri is Microsoft-proprietary and is NOT committed to git.
# Oswald (SIL OFL) is committed and does not need this script.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$winFonts = Join-Path $env:WINDIR "Fonts"

$pairs = @(
    @{ Src = "calibri.ttf"; Dest = "Calibri.ttf" },
    @{ Src = "calibrib.ttf"; Dest = "Calibri-Bold.ttf" }
)

$targets = @(
    (Join-Path $root "fonts"),
    (Join-Path $root "mobile\assets\fonts")
)

$appDataMobile = Join-Path $env:LOCALAPPDATA "swift-document-generator-mobile\assets\fonts"
if (Test-Path (Split-Path $appDataMobile -Parent)) {
    $targets += $appDataMobile
}

foreach ($dir in $targets) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    foreach ($pair in $pairs) {
        $src = Join-Path $winFonts $pair.Src
        $dest = Join-Path $dir $pair.Dest
        if (-not (Test-Path $src)) {
            Write-Warning "Windows font not found: $src"
            continue
        }
        Copy-Item -Force $src $dest
        Write-Host "Synced $($pair.Dest) -> $dir"
    }
}

Write-Host "Done. Calibri is local-only (gitignored)."
