import 'dart:io';
import 'dart:math' as math;

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
import 'package:pdfrx/pdfrx.dart';
import 'package:swift_shipping_label/label_data.dart';
import 'package:swift_shipping_label/logo_ink_fit.dart';
import 'package:swift_shipping_label/pdf/shipping_label_pdf.dart';

/// Rasterized header ink must span ≈ green (46pt) target.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  Future<int> measureBlueInkHeightPx(Uint8List pdfBytes) async {
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
      final im = img.Image.fromBytes(
        width: rendered!.width,
        height: rendered.height,
        bytes: rendered.pixels.buffer,
        bytesOffset: rendered.pixels.offsetInBytes,
        numChannels: 4,
        order: img.ChannelOrder.bgra,
      );
      rendered.dispose();
      final topBand = (im.height * 0.2).floor();
      var minY = im.height;
      var maxY = -1;
      for (var y = 0; y < topBand; y++) {
        var blue = 0;
        for (var x = 0; x < (im.width * 0.5).floor(); x++) {
          final p = im.getPixel(x, y);
          if (p.a < 128) continue;
          final r = p.r.toInt();
          final g = p.g.toInt();
          final b = p.b.toInt();
          if (b > r + 25 && b > g + 8 && b > 70) blue++;
        }
        if (blue > 6) {
          minY = math.min(minY, y);
          maxY = math.max(maxY, y);
        }
      }
      expect(maxY, greaterThan(minY), reason: 'blue ink not found in header');
      return maxY - minY + 1;
    } finally {
      await doc.dispose();
    }
  }

  test('Propak PDF ink height ≈ 46pt (2× px)', () async {
    final root = Directory.current.parent;
    final bytes = File(
      '${root.path}/customer_logos/Propak-Energy-Services-Logo.png',
    ).readAsBytesSync();
    final shipping = await ShippingLabelPdf.load();
    final pdf = await shipping.build(
      data: ShippingLabelData.sample.copy()
        ..set(LabelFields.customer, 'PROPAK ENERGY SERVICES'),
      customerLogoBytes: [bytes],
    );
    final inkPx = await measureBlueInkHeightPx(pdf);
    final targetPx = ShippingLabelPdf.rectLogoTargetH * 2;
    // ignore: avoid_print
    print('Propak rendered ink px=$inkPx target~$targetPx');
    expect(inkPx / targetPx, greaterThan(0.82));
  });

  test('scaleToInkHeight makes canvas match ink box', () {
    final root = Directory.current.parent;
    final prep = LogoInkFit.prepare(
      File('${root.path}/customer_logos/Propak-Energy-Services-Logo.png')
          .readAsBytesSync(),
    );
    final h = ShippingLabelPdf.rectLogoTargetH;
    final scaled = LogoInkFit.scaleToInkHeight(prep.png, prep.ink, h);
    expect(scaled.ink.canvasH, scaled.ink.height);
    expect(scaled.ink.canvasW, scaled.ink.width);
    expect(scaled.ink.height, h.round());
  });
}
