import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:swift_shipping_label/app_storage.dart';
import 'package:swift_shipping_label/pdf/shipping_label_pdf.dart';
import 'package:swift_shipping_label/startup_sync.dart';
import 'package:swift_shipping_label/windows_splash_screen.dart';

Future<void> _loadFont(String family, String assetPath) async {
  // Sync dart:io read — testWidgets bodies run in a fake-async test zone
  // where real (async) OS-thread I/O never completes unless wrapped in
  // tester.runAsync(); sync reads sidestep that entirely.
  final data = File(assetPath).readAsBytesSync();
  final loader = FontLoader(family)
    ..addFont(Future.value(ByteData.view(data.buffer)));
  await loader.load();
}

/// Renders the Windows-only splash/loading screen and writes a PNG for
/// visual review — mirrors the generate_*_preview_test.dart pattern used for
/// Shipping/BOL PDF previews elsewhere in this repo.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('generate Windows splash screen preview', (tester) async {
    await _loadFont('Oswald', 'assets/fonts/Oswald-SemiBold.ttf');
    await _loadFont('Montserrat', 'assets/fonts/Montserrat-Bold.ttf');

    final tempDir = Directory.systemTemp.createTempSync('splash_preview');
    addTearDown(() => tempDir.deleteSync(recursive: true));
    final storage = AppStorage.forTesting(tempDir);
    final pdf = await ShippingLabelPdf.load();
    // Test-only StartupSync — no real network calls, so the preview never
    // depends on network access or the sync classes' 25–45s HTTP timeouts.
    final startupSync = StartupSync.forTesting(storage);

    final key = GlobalKey();
    await tester.pumpWidget(
      MaterialApp(
        home: RepaintBoundary(
          key: key,
          child: WindowsSplashScreen(
            storage: storage,
            pdf: pdf,
            startupSync: startupSync,
          ),
        ),
      ),
    );
    // Freeze progress mid-way for a representative preview frame. Real PNG
    // decode for Image.asset (app icon + Swift badge) needs pumps driven
    // inside runAsync — plain tester.pump() never lets it finish. Stay under
    // the widget's 220ms "settle" delay so we capture the *loading* frame,
    // not the post-ready HomeScreen handoff.
    startupSync.progress.value = 0.5;
    await tester.pump();
    await tester.runAsync(() async {
      for (var i = 0; i < 4; i++) {
        await Future<void>.delayed(const Duration(milliseconds: 40));
        await tester.pump();
      }
    });

    final outDir = Directory(
      '${Directory.current.parent.path}${Platform.pathSeparator}'
      'filled${Platform.pathSeparator}qa_windows_splash_preview',
    );
    outDir.createSync(recursive: true);
    final outFile = File('${outDir.path}/windows_splash_preview_latest.png');

    await tester.runAsync(() async {
      final boundary =
          key.currentContext!.findRenderObject() as RenderRepaintBoundary;
      final image = await boundary.toImage(pixelRatio: 2.0);
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      image.dispose();
      outFile.writeAsBytesSync(bytes!.buffer.asUint8List());
    });

    // ignore: avoid_print
    print('Wrote Windows splash preview:\n  ${outFile.path}');
    expect(outFile.existsSync(), isTrue);

    // Unmount so the widget's dispose() cancels its own settle Timer —
    // otherwise flutter_test's "no pending timers" leak check fails, and we
    // don't want to pump far enough to actually reach HomeScreen here (that
    // full screen isn't sized for this test's default viewport).
    await tester.pumpWidget(const SizedBox());
  });
}
