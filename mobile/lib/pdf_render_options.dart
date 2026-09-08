/// PDF generation preferences (Windows-customizable; defaults match current layout).
///
/// The Swift Supply logo is always drawn when asset bytes are available — there
/// is no toggle to hide it.
class PdfRenderOptions {
  const PdfRenderOptions({
    this.logoPlacement = PdfLogoPlacement.left,
    this.logoScale = 1.0,
    this.bodyFont = PdfBodyFont.brand,
    this.fontScale = 1.0,
    this.showCustomerLogos = true,
    this.isBoxSized = false,
  });

  final PdfLogoPlacement logoPlacement;
  final double logoScale;
  final PdfBodyFont bodyFont;
  final double fontScale;
  final bool showCustomerLogos;

  /// When true (Shipping/Receiving only): draw the full label at 50% scale in
  /// the top-left quadrant of landscape Letter (5.5" × 4.25").
  final bool isBoxSized;

  static const defaults = PdfRenderOptions();

  factory PdfRenderOptions.fromJson(Map<String, dynamic>? json) {
    if (json == null) return defaults;
    return PdfRenderOptions(
      logoPlacement: PdfLogoPlacement.tryParse('${json['logoPlacement']}') ??
          PdfLogoPlacement.left,
      logoScale: _clampDouble(json['logoScale'], 0.6, 1.5, 1.0),
      bodyFont:
          PdfBodyFont.tryParse('${json['bodyFont']}') ?? PdfBodyFont.brand,
      fontScale: _clampDouble(json['fontScale'], 0.8, 1.35, 1.0),
      // Legacy `showSwiftLogo` in saved settings is ignored — Swift is always on.
      showCustomerLogos: json['showCustomerLogos'] is bool
          ? json['showCustomerLogos'] as bool
          : true,
      // Session-only by default; still parse if present in saved settings.
      isBoxSized: json['isBoxSized'] is bool ? json['isBoxSized'] as bool : false,
    );
  }

  Map<String, dynamic> toJson() => {
        'logoPlacement': logoPlacement.name,
        'logoScale': logoScale,
        'bodyFont': bodyFont.name,
        'fontScale': fontScale,
        'showCustomerLogos': showCustomerLogos,
        'isBoxSized': isBoxSized,
      };

  PdfRenderOptions copyWith({
    PdfLogoPlacement? logoPlacement,
    double? logoScale,
    PdfBodyFont? bodyFont,
    double? fontScale,
    bool? showCustomerLogos,
    bool? isBoxSized,
  }) {
    return PdfRenderOptions(
      logoPlacement: logoPlacement ?? this.logoPlacement,
      logoScale: logoScale ?? this.logoScale,
      bodyFont: bodyFont ?? this.bodyFont,
      fontScale: fontScale ?? this.fontScale,
      showCustomerLogos: showCustomerLogos ?? this.showCustomerLogos,
      isBoxSized: isBoxSized ?? this.isBoxSized,
    );
  }
}

enum PdfLogoPlacement {
  left,
  right,
  belowSwift,
  hidden;

  String get label => switch (this) {
        PdfLogoPlacement.left => 'Left of Swift logo',
        PdfLogoPlacement.right => 'Right of Swift logo',
        PdfLogoPlacement.belowSwift => 'Below Swift logo',
        PdfLogoPlacement.hidden => 'Hide customer logos',
      };

  static PdfLogoPlacement? tryParse(String raw) {
    final t = raw.trim().toLowerCase();
    for (final v in values) {
      if (v.name == t) return v;
    }
    return null;
  }
}

enum PdfBodyFont {
  brand,
  calibri,
  oswald,
  /// PDF standard Helvetica for entry values (true Helvetica, not Arial).
  helvetica,
  /// Free geometric sans close to Proxima Nova (Google Fonts / OFL).
  montserrat;

  String get label => switch (this) {
        PdfBodyFont.brand => 'Brand (Oswald + Calibri)',
        PdfBodyFont.calibri => 'Calibri only',
        PdfBodyFont.oswald => 'Oswald only',
        PdfBodyFont.helvetica => 'Helvetica (entry fields)',
        PdfBodyFont.montserrat => 'Montserrat (Proxima-like)',
      };

  static PdfBodyFont? tryParse(String raw) {
    final t = raw.trim().toLowerCase();
    for (final v in values) {
      if (v.name == t) return v;
    }
    return null;
  }
}

enum UiLayoutPreset {
  classic,
  compact,
  widescreen,
  stacked;

  String get label => switch (this) {
        UiLayoutPreset.classic => 'Classic (default)',
        UiLayoutPreset.compact => 'Compact',
        UiLayoutPreset.widescreen => 'Widescreen workspace',
        UiLayoutPreset.stacked => 'Stacked workspace below',
      };

  static UiLayoutPreset? tryParse(String raw) {
    final t = raw.trim().toLowerCase();
    for (final v in values) {
      if (v.name == t) return v;
    }
    return null;
  }
}

enum UiThemePreference {
  light,
  dark;

  String get label => switch (this) {
        UiThemePreference.light => 'Light',
        UiThemePreference.dark => 'Dark',
      };

  static UiThemePreference? tryParse(String raw) {
    final t = raw.trim().toLowerCase();
    for (final v in values) {
      if (v.name == t) return v;
    }
    return null;
  }
}

double _clampDouble(Object? raw, double min, double max, double fallback) {
  final v = raw is num ? raw.toDouble() : double.tryParse('$raw');
  if (v == null) return fallback;
  return v.clamp(min, max);
}
