# Swift Supply — Shipping Label (Android)

Flutter app that pre-fills a shipping label and generates a **print-ready PDF**
on-device (no Python server). Layout matches the Windows app: PO/Project wrap
up to 2 lines and Special Instructions shrinks into the remaining band.

## Sideload APK

Debug APK (ready to install):

`mobile\dist\SwiftShippingLabel-debug.apk`

Also produced by Flutter at:

`mobile\build\app\outputs\flutter-apk\app-debug.apk`

Install via USB:

```powershell
adb install -r mobile\dist\SwiftShippingLabel-debug.apk
```

Or copy the APK to the phone and open it (allow install from unknown sources).

## Build again

Flutter on this machine:

`C:\Users\Brice\Downloads\swift-staging-tracker\.tools\flutter`

```powershell
$env:PATH = "$env:USERPROFILE\Downloads\swift-staging-tracker\.tools\flutter\bin;$env:PATH"
$env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
cd mobile
flutter pub get
flutter build apk --debug
Copy-Item build\app\outputs\flutter-apk\app-debug.apk dist\SwiftShippingLabel-debug.apk -Force
```

Note: `mobile\build` may be a junction to `%LOCALAPPDATA%\swift-document-generator-build` to avoid OneDrive locking Gradle caches.

## Features vs Windows app

| Feature | Android | Windows |
|---------|---------|---------|
| Pre-fill + flat print PDF | Yes | Yes |
| PO/Project wrap (max 2 lines) | Yes | Yes |
| Special Instructions auto-shrink | Yes | Yes |
| Oswald labels + Calibri values | Yes (bundled) | Yes |
| Customer presets | Yes (app-private) | Yes |
| Multi logo import | Yes | Yes |
| Share / save PDF | Android share sheet | Save & open |
| Desktop shortcut | N/A | Yes |
| AcroForm fillable PDF | No (print flat only) | Optional, not recommended |

Data lives under app documents (`customer_logos/`, `presets.json`, `filled/`) — no root/admin.
