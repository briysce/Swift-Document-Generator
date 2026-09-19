# Swift Document Generator

Swift Oilfield Supply warehouse documents for **Windows** and **Android**:

1. **Shipping Label** — Swiss print PDF; multi-page piece counts  
2. **Receiving Label** — staging skid label  
3. **Bill of Lading** — straight BOL (3 copies), ported from `generate_swift_bol_pdf.py`

## Run (Windows)

| | |
|--|--|
| Portable | `dist\Swift Document Generator\swift_shipping_label.exe` |
| Installer | [SwiftDocumentGenerator-Setup.exe](https://github.com/briysce/Swift-Document-Generator/releases/latest/download/SwiftDocumentGenerator-Setup.exe) (per-user, no admin; Start Menu, uninstaller) |
| Launch | **Launch Swift Document Generator.vbs** (no console) |

```powershell
.\scripts\build_windows.ps1
.\Launch Swift Document Generator.vbs
```

Build the installer after a Windows release build:

```powershell
.\scripts\build_windows_installer.ps1
```

## Update

In-app **Update** downloads from GitHub Releases:

- Windows: `SwiftDocumentGenerator-Setup.exe` (launches the installer; portable zip is still published for manual use)
- Android: `SwiftDocumentGenerator-android.apk`

## Publish

```powershell
.\scripts\publish_release.ps1
```

Android builds use `%LOCALAPPDATA%\swift-document-generator-mobile` (synced from `mobile/`), falling back to the repo `mobile/` tree directly if that folder isn't set up.

## BOL source

Python reference generator (also in this repo):

`C:\Users\Brice\OneDrive\Documents\generate_swift_bol_pdf.py` → copied as `generate_swift_bol_pdf.py`

## Data

Presets, logos, and filled PDFs live under the app documents folder  
`swift_document_generator\` (migrated automatically from `swift_shipping_label\` when present).

Filenames: `SL-` / `RL-` / `BOL-` + customer + sales order, with `(1)`, `(2)` on collision.
