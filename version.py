"""Embedded app version for Windows Update checks (semver)."""

__version__ = "1.1.97"
APP_NAME = "Swift Document Generator"
GITHUB_OWNER = "StagingLogShippingTracker"
GITHUB_REPO = "swift-shipping-label"
GITHUB_RELEASES_API = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)
GITHUB_RELEASES_PAGE = (
    f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases"
)

# Release asset names (must match scripts/publish_release.ps1)
WINDOWS_SETUP_ASSET = "SwiftDocumentGenerator-Setup.exe"
WINDOWS_ZIP_ASSET = "SwiftDocumentGenerator-windows.zip"
ANDROID_APK_ASSET = "SwiftDocumentGenerator-android.apk"
