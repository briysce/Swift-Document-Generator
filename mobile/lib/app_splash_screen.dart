import 'dart:async';

import 'package:flutter/material.dart';

import 'app_storage.dart';
import 'brand_assets.dart';
import 'briysce_apps_logo.dart';
import 'home_screen.dart';
import 'pdf/shipping_label_pdf.dart';
import 'startup_sync.dart';

/// Branded load/splash screen — shown on both Windows and Android until
/// [StartupSync.ready] resolves (i.e. every startup Supabase load — presets,
/// signatures, contacts, delivery addresses, carriers, generated-document
/// history purge — has settled), then hands off to [HomeScreen].
///
/// Same design on both platforms; sizing scales down on narrow/phone-width
/// screens (see [_Scale]) rather than forking into a separate widget.
/// Android additionally holds its native launch_background open until
/// Flutter's first frame paints (see `main.dart`), so there's no flash of a
/// blank window before this widget appears.
class AppSplashScreen extends StatefulWidget {
  const AppSplashScreen({
    super.key,
    required this.storage,
    required this.pdf,
    required this.startupSync,
  });

  final AppStorage storage;
  final ShippingLabelPdf pdf;
  final StartupSync startupSync;

  @override
  State<AppSplashScreen> createState() => _AppSplashScreenState();
}

class _AppSplashScreenState extends State<AppSplashScreen> {
  bool _ready = false;
  Timer? _settleTimer;

  @override
  void initState() {
    super.initState();
    unawaited(_awaitReady());
  }

  @override
  void dispose() {
    _settleTimer?.cancel();
    super.dispose();
  }

  Future<void> _awaitReady() async {
    await widget.startupSync.ready;
    if (!mounted) return;
    // Let the progress bar visibly land on 100% instead of cutting away
    // mid-tick — purely cosmetic, not part of the readiness gate itself. A
    // cancellable Timer (not a bare Future.delayed) so disposing this widget
    // (e.g. in tests) cleanly cancels it instead of leaving it pending.
    final settled = Completer<void>();
    _settleTimer = Timer(const Duration(milliseconds: 220), () {
      if (!settled.isCompleted) settled.complete();
    });
    await settled.future;
    if (!mounted) return;
    setState(() => _ready = true);
  }

  @override
  Widget build(BuildContext context) {
    if (_ready) {
      return HomeScreen(
        storage: widget.storage,
        pdf: widget.pdf,
        startupSync: widget.startupSync,
      );
    }
    final width = MediaQuery.sizeOf(context).width;
    final scale = _Scale(width);
    return Scaffold(
      backgroundColor: const Color(0xFF14161A),
      body: SafeArea(
        child: Center(
          child: Padding(
            padding: EdgeInsets.symmetric(
              horizontal: 32,
              vertical: scale.compact ? 16 : 24,
            ),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                _SwiftMark(width: scale.swiftMark),
                SizedBox(height: scale.compact ? 16 : 22),
                _AppIcon(size: scale.appIcon),
                SizedBox(height: scale.compact ? 22 : 30),
                Text(
                  'Swift Document Generator',
                  textAlign: TextAlign.center,
                  style: TextStyle(
                    fontFamily: 'Oswald',
                    fontWeight: FontWeight.w600,
                    fontSize: scale.compact ? 19 : 22,
                    color: Colors.white,
                    letterSpacing: 0.4,
                  ),
                ),
                SizedBox(height: scale.compact ? 32 : 44),
                SizedBox(
                  width: scale.progressBar,
                  child: ValueListenableBuilder<double>(
                    valueListenable: widget.startupSync.progress,
                    builder: (context, value, _) {
                      return ClipRRect(
                        borderRadius: BorderRadius.circular(3),
                        child: LinearProgressIndicator(
                          value: value <= 0 ? null : value,
                          minHeight: 4,
                          backgroundColor:
                              Colors.white.withValues(alpha: 0.12),
                          color: const Color(0xFFCE4E30),
                        ),
                      );
                    },
                  ),
                ),
                const SizedBox(height: 14),
                Text(
                  'Loading your workspace…',
                  style: TextStyle(
                    fontFamily: 'Oswald',
                    fontSize: 12,
                    color: Colors.white.withValues(alpha: 0.6),
                    letterSpacing: 0.3,
                  ),
                ),
                SizedBox(height: scale.compact ? 40 : 64),
                BriysceAppsLockup(width: scale.compact ? 92 : 112),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// Proportions the splash down for phone-width screens instead of a
/// separate mobile layout — same design, smaller marks and tighter spacing
/// once the window is narrower than a small desktop window.
class _Scale {
  _Scale(double width) : compact = width < 480 {
    final f = compact ? (width / 480).clamp(0.72, 1.0) : 1.0;
    swiftMark = 96 * f;
    appIcon = 132 * f;
    progressBar = (260 * f).clamp(180, 260).toDouble();
  }

  final bool compact;
  late final double swiftMark;
  late final double appIcon;
  late final double progressBar;
}

/// Small standalone Swift Supply brand mark shown above the app icon.
///
/// Uses [SwiftBrandAssets.logoOrangeSolid] (the flat solid-orange lockup with
/// no shadow/box — the same asset [SwiftChromeLogo] uses for app chrome), not
/// [SwiftBrandAssets.logoOrange] (the drop-shadowed PDF/document variant).
/// Drawn as a plain [Image.asset] with no wrapping `Container`/decoration —
/// the PNG's own alpha channel is fully transparent outside the ink, so
/// nothing paints behind it except the splash background.
///
/// The PNG is rendered at 2987×910 from the rebuilt vector master
/// (`assets/brand/swift_supply_logo_orange_solid.svg`, exported by
/// `scripts/export_swift_app_logos.py`), so it stays sharp at splash sizes;
/// same asset as [SwiftChromeLogo] uses elsewhere in the app.
class _SwiftMark extends StatelessWidget {
  const _SwiftMark({required this.width});

  final double width;

  @override
  Widget build(BuildContext context) {
    return Image.asset(
      SwiftBrandAssets.logoOrangeSolid,
      width: width,
      fit: BoxFit.contain,
    );
  }
}

/// The Swift Document Generator app icon.
class _AppIcon extends StatelessWidget {
  const _AppIcon({required this.size});

  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(size * 0.222),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.45),
            blurRadius: 26,
            offset: const Offset(0, 12),
          ),
        ],
      ),
      child: Image.asset(
        'assets/images/app_icon.png',
        width: size,
        height: size,
        fit: BoxFit.contain,
      ),
    );
  }
}
