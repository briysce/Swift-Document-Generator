import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
import 'package:pdfrx/pdfrx.dart';
import 'package:swift_shipping_label/label_data.dart';
import 'package:swift_shipping_label/pdf/bol_label_pdf.dart';
import 'package:swift_shipping_label/pdf/shipping_label_pdf.dart';

/// Writes a sample BOL PDF + PNG for visual review.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('generate BOL sample PDF + PNG preview', () async {
    final shipping = await ShippingLabelPdf.load();
    final bol = BolLabelPdf(shipping);

    Uint8List? logo;
    try {
      final data =
          await rootBundle.load('assets/images/sample_customer_logo.png');
      logo = data.buffer.asUint8List();
    } catch (_) {}

    final data = ShippingLabelData({
      ...ShippingLabelData.bolSample.values,
      BolFields.freightCharges: BolFields.freightCustomerPickup,
    });

    final pdfBytes = await bol.build(
      data: data,
      customerLogoBytes: logo == null ? const [] : [logo],
    );

    final outDir = Directory(
      '${Directory.current.parent.path}${Platform.pathSeparator}'
      'filled${Platform.pathSeparator}qa_bol_preview',
    );
    await outDir.create(recursive: true);
    final stamp = DateTime.now()
        .toIso8601String()
        .replaceAll(':', '-')
        .split('.')
        .first;
    final pdfPath = '${outDir.path}/bol_preview_$stamp.pdf';
    final pngPath = '${outDir.path}/bol_preview_$stamp.png';
    final latestPdf = File('${outDir.path}/bol_preview_latest.pdf');
    final latestPng = File('${outDir.path}/bol_preview_latest.png');

    await File(pdfPath).writeAsBytes(pdfBytes);
    await latestPdf.writeAsBytes(pdfBytes);

    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
      const MethodChannel('plugins.flutter.io/path_provider'),
      (call) async {
        if (call.method == 'getTemporaryDirectory') {
          return Directory.systemTemp.path;
        }
        return null;
      },
    );

    await pdfrxFlutterInitialize();
    final doc = await PdfDocument.openData(pdfBytes);
    try {
      final page = doc.pages.first;
      final rendered = await page.render(
        fullWidth: page.width * 2,
        fullHeight: page.height * 2,
        backgroundColor: 0xFFFFFFFF,
      );
      expect(rendered, isNotNull);
      final pixels = rendered!.pixels;
      final full = img.Image.fromBytes(
        width: rendered.width,
        height: rendered.height,
        bytes: pixels.buffer,
        bytesOffset: pixels.offsetInBytes,
        numChannels: 4,
        order: img.ChannelOrder.bgra,
      );
      for (final p in full) {
        p.a = 255;
      }
      final png = img.encodePng(full);
      await File(pngPath).writeAsBytes(png);
      await latestPng.writeAsBytes(png);
      rendered.dispose();
    } finally {
      await doc.dispose();
    }

    // ignore: avoid_print
    print('Wrote BOL preview:\n  $pdfPath\n  $pngPath');
    expect(pdfBytes.length, greaterThan(1000));
    expect(await latestPng.exists(), isTrue);
  });
}
