#!/usr/bin/env bash
# Linux/macOS counterpart to sync_calibri_fonts.ps1.
#
# Calibri is Microsoft-proprietary and is NOT committed to git, so a fresh
# clone cannot build the Flutter asset bundle (pubspec declares
# assets/fonts/Calibri*.ttf). On Windows, sync_calibri_fonts.ps1 copies the
# real font out of C:\Windows\Fonts. Off Windows there is no Calibri to copy,
# so we install Carlito instead: SIL OFL, and metrically compatible with
# Calibri, so PDF line breaks and label layout stay identical.
#
# Real Calibri, when present (e.g. a mounted Windows partition or a macOS
# install of Office), always wins — pass its directory as $1.
#
# Usage:  scripts/sync_calibri_fonts.sh [/path/to/dir/with/calibri.ttf]
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
targets=("$root/fonts" "$root/mobile/assets/fonts")

find_font() {
  # $1 = real Calibri filename, $2 = Carlito fallback filename
  local override="${SRC_DIR:-}"
  if [[ -n "$override" && -f "$override/$1" ]]; then
    echo "$override/$1"; return 0
  fi
  local p
  for p in \
    "/mnt/c/Windows/Fonts/$1" \
    "/usr/share/fonts/truetype/msttcorefonts/$1" \
    "$HOME/Library/Fonts/$1" \
    "/Library/Fonts/$1"; do
    [[ -f "$p" ]] && { echo "$p"; return 0; }
  done
  for p in \
    "/usr/share/fonts/truetype/crosextra/$2" \
    "/usr/share/fonts/crosextra/$2" \
    "$HOME/Library/Fonts/$2"; do
    [[ -f "$p" ]] && { echo "$p"; return 0; }
  done
  return 1
}

SRC_DIR="${1:-}"
export SRC_DIR

status=0
for dir in "${targets[@]}"; do
  mkdir -p "$dir"
done

copy_one() {
  local real="$1" fallback="$2" dest="$3" src
  if ! src="$(find_font "$real" "$fallback")"; then
    echo "MISSING: no $real and no $fallback fallback." >&2
    echo "  Debian/Ubuntu: sudo apt-get install fonts-crosextra-carlito" >&2
    echo "  macOS:         brew install --cask font-carlito" >&2
    return 1
  fi
  for dir in "${targets[@]}"; do
    cp -f "$src" "$dir/$dest"
  done
  echo "$dest  <-  $src"
}

copy_one "calibri.ttf"  "Carlito-Regular.ttf" "Calibri.ttf"      || status=1
copy_one "calibrib.ttf" "Carlito-Bold.ttf"    "Calibri-Bold.ttf" || status=1

if [[ $status -eq 0 ]]; then
  echo "Fonts synced into: ${targets[*]}"
  echo "(These stay gitignored — see .gitignore.)"
fi
exit $status
