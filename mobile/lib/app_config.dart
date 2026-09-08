/// Public GitHub Releases feed for in-app Update (Windows/Android parity).
class AppConfig {
  static const githubOwner = 'StagingLogShippingTracker';
  static const githubRepo = 'swift-shipping-label';

  static const githubLatestReleaseApi =
      'https://api.github.com/repos/$githubOwner/$githubRepo/releases/latest';

  static const githubReleasesPage =
      'https://github.com/$githubOwner/$githubRepo/releases';

  /// Preferred Windows in-app update asset (Inno Setup installer).
  static const windowsSetupAsset = 'SwiftDocumentGenerator-Setup.exe';

  /// Legacy portable zip — kept for classification only; in-app Update prefers Setup.
  static const windowsZipAsset = 'SwiftDocumentGenerator-windows.zip';
  static const androidApkAsset = 'SwiftDocumentGenerator-android.apk';

  /// Swift Staging & Shipping Log (sibling Operations app).
  static const stagingTrackerGithubOwner = githubOwner;
  static const stagingTrackerGithubRepo = 'staging-tracker';
  static const stagingTrackerLatestReleaseApi =
      'https://api.github.com/repos/$stagingTrackerGithubOwner/$stagingTrackerGithubRepo/releases/latest';
  static const stagingTrackerReleasesPage =
      'https://github.com/$stagingTrackerGithubOwner/$stagingTrackerGithubRepo/releases';
  static const stagingTrackerWindowsSetupAsset = 'SwiftStagingLog-Setup-User.exe';
  static const stagingTrackerWindowsSetupAssetLegacy = 'SLST-Setup-User.exe';
  static const stagingTrackerAndroidApkAsset = 'SwiftStagingLog-Android.apk';
  static const stagingTrackerAndroidApkAssetLegacy = 'SLST-Android.apk';
  static const stagingTrackerAndroidPackage = 'ca.swiftsupply.slst';
  static const stagingTrackerWindowsExe = 'SwiftStagingLog.exe';
  static const stagingTrackerWindowsInstallFolder = 'Swift Staging Shipping Log';

  /// Shared BOL serial counter (StagingLogShippingTracker Supabase).
  /// Anon key only — RPC is SECURITY DEFINER `next_bol_serial`.
  static const supabaseUrl = 'https://gdrpdiwykmnybmkadlrv.supabase.co';
  static const supabaseAnonKey =
      'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdkcnBkaXd5a21ueWJta2FkbHJ2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1MjMyMTIsImV4cCI6MjA5NjA5OTIxMn0.Z7ih_vQic1GtzCyZmTEV-RWJnmuaNZQDfOV2_Fvan5g';

  /// Optional Retool REST/workflow proxy for Clearbit logo lookups.
  /// Set at build time via `--dart-define=RETOOL_CLEARBIT_LOGO_URL=...` or
  /// the `RETOOL_CLEARBIT_LOGO_URL` environment variable. Use `{domain}` in
  /// the template, e.g. `https://your-org.retool.com/api/.../clearbit?domain={domain}`.
  /// When empty, only the public `https://logo.clearbit.com/{domain}` endpoint is used.
  static const retoolClearbitLogoUrl = String.fromEnvironment(
    'RETOOL_CLEARBIT_LOGO_URL',
    defaultValue: '',
  );

  /// Serper.dev Images API (`POST https://google.serper.dev/images`).
  /// Prefer runtime env / gitignored `.env` (`SERPER_API_KEY`).
  static const serperApiKeyDefine = String.fromEnvironment(
    'SERPER_API_KEY',
    defaultValue: '',
  );

  /// Google Gemini API key for logo validation + recreate assist.
  /// Prefer runtime env / gitignored `.env` — do not commit secrets.
  /// Build-time: `--dart-define=GEMINI_API_KEY=...`
  static const geminiApiKeyDefine = String.fromEnvironment(
    'GEMINI_API_KEY',
    defaultValue: '',
  );

  /// GCP project number associated with the Gemini key (metadata).
  static const geminiProjectNumber = String.fromEnvironment(
    'GEMINI_PROJECT_NUMBER',
    defaultValue: '308655478522',
  );

  /// Multimodal model id (default flash). gemini-2.0-flash was retired by
  /// Google — keep this current or JSON-based Gemini calls 404 silently.
  static const geminiModel = String.fromEnvironment(
    'GEMINI_MODEL',
    defaultValue: 'gemini-3.6-flash',
  );

  /// Image-generation model for logo restore.
  static const geminiImageModel = String.fromEnvironment(
    'GEMINI_IMAGE_MODEL',
    defaultValue: 'gemini-3.1-flash-image',
  );

  /// Unused by live address suggest (OpenStreetMap Nominatim). Kept so
  /// existing `--dart-define` / `.env` values do not break builds.
  static const googlePlacesApiKeyDefine = String.fromEnvironment(
    'GOOGLE_PLACES_API_KEY',
    defaultValue: '',
  );

  static const googleMapsApiKeyDefine = String.fromEnvironment(
    'GOOGLE_MAPS_API_KEY',
    defaultValue: '',
  );

  /// Resolved Gemini API key for local recreate process env injection.
  /// Prefer [GeminiClient.resolveApiKey] at call sites.
  static String get geminiApiKey => geminiApiKeyDefine.trim();
}
