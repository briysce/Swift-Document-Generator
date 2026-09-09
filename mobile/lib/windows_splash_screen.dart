import 'dart:async';

import 'package:flutter/material.dart';

import 'app_storage.dart';
import 'brand_assets.dart';
import 'briysce_apps_logo.dart';
import 'home_screen.dart';
import 'pdf/shipping_label_pdf.dart';
import 'startup_sync.dart';

/// Windows-only load/splash screen — shown until [StartupSync.ready]
/// resolves (i.e. every startup Supabase load — presets, signatures,
/// contacts, delivery addresses, carriers, generated-document history purge —
/// has settled), then hands off to [HomeScreen].
///
/// Android/Wear do not get this widget: they keep their existing native
/// launch screen, just held open longer by `main.dart` awaiting the same
/// [StartupSync.ready] signal (see `deferFirstFrame`/`allowFirstFrame`).
class WindowsSplashScreen extends StatefulWidget {
  const WindowsSplashScreen({
    super.key,
    required this.storage,
    required this.pdf,
    required this.startupSync,
  });

  final AppStorage storage;
  final ShippingLabelPdf pdf;
  final StartupSync startupSync;

  @override
  State<WindowsSplashScreen> createState() => _WindowsSplashScreenState();
}

class _WindowsSplashScreenState extends State<WindowsSplashScreen> {
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
    return Scaffold(
      backgroundColor: const Color(0xFF14161A),
      body: SafeArea(
        child: Center(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 32, vertical: 24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const _SwiftMark(),
                const SizedBox(height: 22),
                const _AppIcon(),
                const SizedBox(height: 30),
                const Text(
                  'Swift Document Generator',
                  textAlign: TextAlign.center,
                  style: TextStyle(
                    fontFamily: 'Oswald',
                    fontWeight: FontWeight.w600,
                    fontSize: 22,
                    color: Colors.white,
                    letterSpacing: 0.4,
                  ),
                ),
                const SizedBox(height: 44),
                SizedBox(
                  width: 260,
                  child: ValueListenableBuilder<double>(
                    valueListenable: widget.startupSync.progress,
                    builder: (context, value, _) {
                      return ClipRRect(
                        borderRadius: BorderRadius.circular(3),
                        child: LinearProgressIndicator(
                          value: value <= 0 ? null : value,
                          minHeight: 4,
                          backgroundColor: Colors.white.withOpacity(0.12),
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
                    color: Colors.white.withOpacity(0.6),
                    letterSpacing: 0.3,
                  ),
                ),
                const SizedBox(height: 64),
                const BriysceAppsLockup(width: 112),
              ],
            ),
          ),
        ),
      ),
    );
  }
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
/// No genuine vector-path source exists for this mark to use instead: the
/// PDF pipeline's `_drawSwiftLogo` (`shipping_label_pdf.dart`,
/// `bol_label_pdf.dart`) draws from [SwiftBrandAssets.logoOrangeSvg], but
/// despite that asset's doc comment claiming "true vector paths", it is
/// actually a base64-embedded raster PNG wrapped in an `<svg><image>` tag
/// (confirmed by inspecting the file) — and it's the wrong (shadowed, non-
/// solid) variant besides. So this falls back to the solid-orange raster PNG
/// per brand guidance, same as [SwiftChromeLogo] does elsewhere in the app.
class _SwiftMark extends StatelessWidget {
  const _SwiftMark();

  @override
  Widget build(BuildContext context) {
    return Image.asset(
      SwiftBrandAssets.logoOrangeSolid,
      width: 96,
      fit: BoxFit.contain,
    );
  }
}

/// The Swift Document Generator app icon.
class _AppIcon extends StatelessWidget {
  const _AppIcon();

  static const _iconSize = 132.0;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: _iconSize,
      height: _iconSize,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(_iconSize * 0.222),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.45),
            blurRadius: 26,
            offset: const Offset(0, 12),
          ),
        ],
      ),
      child: Image.asset(
        'assets/images/app_icon.png',
        width: _iconSize,
        height: _iconSize,
        fit: BoxFit.contain,
      ),
    );
  }
}
