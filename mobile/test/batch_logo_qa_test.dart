import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:swift_shipping_label/label_data.dart';
import 'package:swift_shipping_label/logo_ink_fit.dart';
import 'package:swift_shipping_label/pdf/bol_label_pdf.dart';
import 'package:swift_shipping_label/pdf/shipping_label_pdf.dart';

/// Batch-generate Shipping / Receiving / BOL PDFs for every logo (solo + combo)
/// and assert ink-fit sizing rules (target height, aspect, no runaway width).
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('batch logo QA — solo and combo documents', () async {
    final root = Directory.current.parent;
    final logosDir = Directory('${root.path}/customer_logos');
    expect(logosDir.existsSync(), isTrue, reason: logosDir.path);

    final logos = _collectLogos(logosDir);
    expect(logos.length, greaterThan(5), reason: 'Need customer logos in ${logosDir.path}');

    final outRoot = Directory('${root.path}/filled/logo_qa');
    await outRoot.create(recursive: true);

    final shipping = await ShippingLabelPdf.load();
    final report = StringBuffer('batch_logo_qa ${DateTime.now().toIso8601String()}\n');
    var soloOk = 0;
    var comboOk = 0;
    var fail = 0;

    for (final f in logos) {
      final stem = _stem(f);
      try {
        final bytes = f.readAsBytesSync();
        _assertInkMetrics(bytes, f.path, ShippingLabelPdf.customerLogoTargetH);
        final soloDir = Directory('${outRoot.path}/solo/$stem');
        await soloDir.create(recursive: true);

        await _writePdf(
          '${soloDir.path}/shipping.pdf',
          await shipping.build(
            data: ShippingLabelData.sample.copy()
              ..set(LabelFields.customer, stem),
            customerLogoBytes: [bytes],
          ),
        );
        await _writePdf(
          '${soloDir.path}/receiving.pdf',
          await shipping.buildReceiving(
            data: ShippingLabelData.receivingSample.copy()
              ..set(LabelFields.customer, stem),
            customerLogoBytes: [bytes],
          ),
        );
        await _writePdf(
          '${soloDir.path}/bol.pdf',
          await BolLabelPdf(shipping).build(
            data: ShippingLabelData.bolSample.copy()
              ..set(LabelFields.customer, stem),
            customerLogoBytes: [bytes],
          ),
        );
        soloOk++;
        report.writeln('OK solo  $stem');
      } catch (e, st) {
        fail++;
        report.writeln('FAIL solo $stem: $e\n$st');
      }
    }

    for (var i = 0; i < logos.length; i++) {
      final a = logos[i];
      final b = logos[(i + 1) % logos.length];
      final stem = '${_stem(a)}__co__${_stem(b)}';
      try {
        final ba = a.readAsBytesSync();
        final bb = b.readAsBytesSync();
        _assertDualInk(ba, bb, a.path, b.path, ShippingLabelPdf.customerLogoTargetH);

        final comboDir = Directory('${outRoot.path}/combo/$stem');
        await comboDir.create(recursive: true);

        await _writePdf(
          '${comboDir.path}/shipping.pdf',
          await shipping.build(
            data: ShippingLabelData.sample.copy()
              ..set(LabelFields.customer, '${_stem(a)} c/o ${_stem(b)}'),
            customerLogoBytes: [ba, bb],
          ),
        );
        await _writePdf(
          '${comboDir.path}/receiving.pdf',
          await shipping.buildReceiving(
            data: ShippingLabelData.receivingSample.copy()
              ..set(LabelFields.customer, '${_stem(a)} c/o ${_stem(b)}'),
            customerLogoBytes: [ba, bb],
          ),
        );
        await _writePdf(
          '${comboDir.path}/bol.pdf',
          await BolLabelPdf(shipping).build(
            data: ShippingLabelData.bolSample.copy()
              ..set(LabelFields.customer, '${_stem(a)} c/o ${_stem(b)}'),
            customerLogoBytes: [ba, bb],
          ),
        );
        comboOk++;
        report.writeln('OK combo $stem');
      } catch (e, st) {
        fail++;
        report.writeln('FAIL combo $stem: $e\n$st');
      }
    }

    report.writeln('\nSummary: ${logos.length} logos, $soloOk solo OK, $comboOk combo OK, $fail failures');
    final reportFile = File('${root.path}/qa_logs/batch_logo_qa_report.txt');
    await reportFile.parent.create(recursive: true);
    await reportFile.writeAsString(report.toString());
    // ignore: avoid_print
    print(report.toString());

    expect(fail, 0, reason: 'See ${reportFile.path}');
  }, timeout: const Timeout(Duration(minutes: 45)));
}

List<File> _collectLogos(Directory dir) {
  const skipSuffixes = ['_restored', '_restored_flat', '_processed'];
  const skipNames = {'logo_restore_cache.json', 'murrays_trucking_restored.json'};
  final out = <File>[];
  for (final ent in dir.listSync().whereType<File>()) {
    final name = ent.uri.pathSegments.last;
    if (skipNames.contains(name)) continue;
    final lower = name.toLowerCase();
    if (!lower.endsWith('.png') && !lower.endsWith('.jpg') && !lower.endsWith('.jpeg')) {
      continue;
    }
    if (skipSuffixes.any((s) => lower.contains(s))) continue;
    out.add(ent);
  }
  out.sort((a, b) => a.path.compareTo(b.path));
  return out;
}

String _stem(File f) =>
    f.uri.pathSegments.last.replaceAll(RegExp(r'\.(png|jpe?g)$', caseSensitive: false), '');

void _assertInkMetrics(Uint8List bytes, String path, double targetH) {
  final prep = LogoInkFit.prepare(bytes);
  final ink = prep.ink;
  expect(ink.isValid, isTrue, reason: '$path invalid ink');
  expect(
    ink.height * ink.scaleForHeight(targetH),
    closeTo(targetH, 0.001),
    reason: '$path ink height',
  );
  final drawH = ink.drawHeight(targetH);
  expect(drawH, lessThanOrEqualTo(targetH * 1.06), reason: '$path drawH=$drawH');
  final drawW = ink.drawWidth(targetH);
  expect(drawW, greaterThan(0));
  expect(drawW, lessThan(600), reason: '$path runaway width $drawW');
}

void _assertDualInk(
  Uint8List a,
  Uint8List b,
  String pathA,
  String pathB,
  double targetH,
) {
  final inkA = LogoInkFit.prepare(a).ink;
  final inkB = LogoInkFit.prepare(b).ink;
  const gap = 10.0;
  const frameW = 400.0; // conservative content band
  final cellW = (frameW - gap) / 2;
  final shared = LogoInkMetrics.sharedHeightForCells([inkA, inkB], targetH, cellW);
  expect(shared, greaterThan(0));
  expect(shared, lessThanOrEqualTo(targetH));
  for (final pair in [(inkA, pathA), (inkB, pathB)]) {
    final ink = pair.$1;
    final path = pair.$2;
    expect(ink.drawWidth(shared), lessThanOrEqualTo(cellW * 1.02), reason: '$path cell overflow');
  }
}

Future<void> _writePdf(String path, Uint8List bytes) async {
  expect(bytes.length, greaterThan(800), reason: 'PDF too small: $path');
  await File(path).writeAsBytes(bytes);
}
