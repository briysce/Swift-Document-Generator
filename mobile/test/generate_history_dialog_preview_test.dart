import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:swift_shipping_label/app_storage.dart';
import 'package:swift_shipping_label/document_history_sync.dart';
import 'package:swift_shipping_label/home_screen.dart';
import 'package:swift_shipping_label/label_data.dart';

Future<void> _loadFont(String family, String assetPath) async {
  // Sync dart:io read — testWidgets bodies run in a fake-async test zone
  // where real (async) OS-thread I/O never completes unless wrapped in
  // tester.runAsync(); sync reads sidestep that entirely. Without this the
  // dialog renders with Flutter's blocky test-font placeholder glyphs
  // instead of real, human-reviewable text.
  final data = File(assetPath).readAsBytesSync();
  final loader = FontLoader(family)
    ..addFont(Future.value(ByteData.view(data.buffer)));
  await loader.load();
}

/// Renders the revamped History dialog (Quick Search + 20/page pagination +
/// per-entry delete) and writes a PNG for visual review — mirrors the
/// generate_windows_splash_preview_test.dart pattern used elsewhere.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('generate History dialog preview (search + pagination + delete)',
      (tester) async {
    // App theme default is 'Helvetica' (a system font, not bundled — real
    // devices resolve it from the OS). Load a bundled TTF instead so this
    // preview shows real glyphs rather than the test-font placeholder.
    await _loadFont('Calibri', 'assets/fonts/Calibri.ttf');
    // Real Icons.search/close/chevron_* glyphs (not blank test-font boxes)
    // — the SDK ships this font in the bundled Flutter's own cache.
    await _loadFont(
      'MaterialIcons',
      '../.tools/flutter/bin/cache/artifacts/material_fonts/MaterialIcons-Regular.otf',
    );

    final tempDir = Directory.systemTemp.createTempSync('history_preview');
    addTearDown(() => tempDir.deleteSync(recursive: true));
    final storage = AppStorage.forTesting(tempDir);
    final sync = DocumentHistorySync(storage);

    // 45 fake entries → 3 pages of 20/20/5, proving the ~50-entry cutoff is
    // gone (the old default cap was 60, but real production data already
    // has a "shipping" kind past that — see the live count checked before
    // this fix) and the pagination math lands exactly right.
    final customers = [
      'PROPAK SYSTEMS LTD.',
      'Arc Resources LTD',
      'Spartan Delta Corp.',
      'Trialta Projects',
      'Murray\'s Trucking',
    ];
    final docs = [
      for (var i = 0; i < 45; i++)
        GeneratedDocumentRecord(
          id: 'preview_$i',
          kind: LabelKind.shipping,
          title: '${customers[i % customers.length]}_SO${10000 + i}',
          customer: customers[i % customers.length],
          salesOrder: 'SO${10000 + i}',
          fileName: 'label_$i.pdf',
          storagePath: 'shipping/preview_$i/label_$i.pdf',
          byteSize: 42000 + i,
          createdAt: DateTime.utc(2026, 9, 9).subtract(Duration(hours: i)),
        ),
    ];

    final key = GlobalKey();
    await tester.pumpWidget(
      MaterialApp(
        theme: ThemeData(fontFamily: 'Calibri'),
        home: Scaffold(
          backgroundColor: Colors.black12,
          body: Center(
            child: RepaintBoundary(
              key: key,
              child: HistoryDialog(
                kind: LabelKind.shipping,
                sync: sync,
                onOpenPdf: (_) async {},
                onTemplate: (_) {},
                debugInitialDocs: docs,
              ),
            ),
          ),
        ),
      ),
    );
    // Let the debug-docs microtask + resulting setState settle.
    await tester.pump();
    await tester.pump();

    final outDir = Directory(
      '${Directory.current.parent.path}${Platform.pathSeparator}'
      'filled${Platform.pathSeparator}qa_history_dialog_preview',
    );
    outDir.createSync(recursive: true);
    final outFile = File('${outDir.path}/history_dialog_preview_latest.png');

    await tester.runAsync(() async {
      final boundary =
          key.currentContext!.findRenderObject() as RenderRepaintBoundary;
      final image = await boundary.toImage(pixelRatio: 2.0);
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      image.dispose();
      outFile.writeAsBytesSync(bytes!.buffer.asUint8List());
    });

    // ignore: avoid_print
    print('Wrote History dialog preview:\n  ${outFile.path}');
    expect(outFile.existsSync(), isTrue);

    // Sanity-check the pagination math the screenshot is showing: 45 docs
    // at 20/page is page 1 of 3, with the first 20 (most-recent-first)
    // visible.
    final state = tester.state<HistoryDialogState>(find.byType(HistoryDialog));
    expect(state.debugPageCount, 3);
    expect(state.debugPageItems.length, 20);
    expect(find.text('Page 1 of 3'), findsOneWidget);
    expect(find.byIcon(Icons.close), findsWidgets);
    expect(find.byIcon(Icons.search), findsOneWidget);
  });
}
