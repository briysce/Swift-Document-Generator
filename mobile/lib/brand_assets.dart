import 'package:flutter/material.dart';

/// Brand image paths used in app chrome (not PDF).
class SwiftBrandAssets {
  SwiftBrandAssets._();

  /// Document / PDF Swift lockup: orange SWIFT + bars, black SUPPLY + shadow.
  static const logoOrange = 'assets/images/swift_supply_logo_orange.png';

  /// True vector master [logoOrange] was rasterized from (same 2987×910
  /// viewBox/coordinate space) — PDF embeds this directly as vector paths so
  /// the Swift lockup never pixelates at any zoom, print size, or DPI.
  static const logoOrangeSvg = 'assets/images/swift_supply_logo_orange.svg';

  /// Older transparent-plate export (seams between fill and outline). Unused
  /// by generated PDFs — kept so existing chrome/tests can still load it.
  static const logoDocument = 'assets/images/swift_supply_logo_document.png';

  /// App chrome / side-menu logo: flat solid-orange lockup (no shadow).
  static const logoOrangeSolid =
      'assets/images/swift_supply_logo_orange_solid.png';

  /// White wordmark for legacy orange headers (light-only accents).
  static const headerWhite = 'assets/images/swift_supply_header_white.png';

  /// Chrome logo — flat solid orange for light and dark UI.
  static String chromeLogo({required bool dark}) => logoOrangeSolid;
}

/// DPI-aware Swift Supply mark for toolbars / headers / side chrome.
///
/// Always uses [SwiftBrandAssets.logoOrangeSolid] (flat solid orange). Generated
/// documents use [SwiftBrandAssets.logoOrange] via the PDF pipeline instead.
class SwiftChromeLogo extends StatelessWidget {
  const SwiftChromeLogo({
    super.key,
    required this.height,
    this.isDark = false,
  });

  final double height;

  /// Reserved for future light/dark variants; chrome is solid orange either way.
  final bool isDark;

  @override
  Widget build(BuildContext context) {
    final dpr = MediaQuery.devicePixelRatioOf(context);
    final width = MediaQuery.sizeOf(context).width;
    var h = height;
    if (width >= 1800) {
      h = height + 6;
    } else if (width >= 1440) {
      h = height + 3;
    }
    // Decode above display pixels so large/high-DPI monitors stay sharp.
    final cacheH = (h * dpr * 2).round().clamp(64, 910);
    return Image.asset(
      SwiftBrandAssets.logoOrangeSolid,
      height: h,
      fit: BoxFit.contain,
      filterQuality: FilterQuality.high,
      isAntiAlias: true,
      cacheHeight: cacheH,
    );
  }
}
