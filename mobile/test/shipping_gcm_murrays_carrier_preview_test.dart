import 'dart:io';
import 'dart:math' as math;

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
import 'package:pdfrx/pdfrx.dart';
import 'package:swift_shipping_label/label_data.dart';
import 'package:swift_shipping_label/pdf/shipping_label_pdf.dart';

/// Isolated preview: GCM ship-to + Murray's logo in the Carrier value box.
/// Not a product feature; do not wire into the Windows form.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('GCM shipping label with Murray\'s carrier logo preview', () async {
    final root = Directory.current.parent;
    final attached = File(
      r'C:\Users\Brice\.cursor\projects\c-Users-Brice-OneDrive-Documents-swift-document-generator\assets\c__Users_Brice_AppData_Roaming_Cursor_User_workspaceStorage_074cb3d740d8b2c861dd5d216d9abd09_images_Murrays-d818414d-80b4-4bea-823a-86f02accd541.png',
    );
    expect(attached.existsSync(), isTrue, reason: attached.path);

    final gcmLogo = File('${root.path}/customer_logos/GCM logo2.png');
    expect(gcmLogo.existsSync(), isTrue, reason: gcmLogo.path);

    final data = ShippingLabelData.sample.copy()
      ..set(LabelFields.customer, 'GCM VALVE MODIFICATIONS')
      ..set(LabelFields.shipTo, 'GCM VALVE MODIFICATIONS')
      ..set(
        LabelFields.location,
        '3360 10TH STREET\nNISKU, AB T9E 1E7',
      )
      ..set(LabelFields.attn, 'KYNDRA STUBER')
      ..set(LabelFields.carrier, "Murray's Pre-Paid")
      ..set(BolFields.freightCharges, BolFields.freightPrepaid)
      ..set(BolFields.thirdPartyBilling, '')
      ..set(LabelFields.swiftContact, 'SEAN FITZPATRICK')
      ..set(LabelFields.specialInstructions, '')
      ..set(LabelFields.poNum, '')
      ..set(LabelFields.project, '');

    final shipping = await ShippingLabelPdf.load();
    final pdfBytes = await shipping.build(
      data: data,
      customerLogoBytes: [await gcmLogo.readAsBytes()],
      carrierLogoBytes: _flattenMurraysOnWhite(await attached.readAsBytes()),
    );

    final outDir = Directory('${root.path}/filled/logo_resize_examples');
    await outDir.create(recursive: true);
    final pdfPath =
        '${outDir.path}/shipping_gcm_murrays_carrier_preview.pdf';
    final pngPath =
        '${outDir.path}/shipping_gcm_murrays_carrier_preview.png';
    await File(pdfPath).writeAsBytes(pdfBytes);

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
      await File(pngPath).writeAsBytes(img.encodePng(full));
      rendered.dispose();
    } finally {
      await doc.dispose();
    }

    // ignore: avoid_print
    print(
      'Wrote preview PDF/PNG.\n'
      'GCM: 3360 10TH STREET, NISKU, AB T9E 1E7 (attn KYNDRA STUBER)\n'
      '  $pdfPath\n  $pngPath',
    );
    expect(File(pdfPath).lengthSync(), greaterThan(1000));
    expect(File(pngPath).lengthSync(), greaterThan(1000));
  }, timeout: const Timeout(Duration(minutes: 3)));
}

/// Punch baked checkerboard, keep maroon fill + navy outlines, flatten on white.
Uint8List _flattenMurraysOnWhite(Uint8List input) {
  final decoded = img.decodeImage(input);
  if (decoded == null) return input;
  final src = decoded.convert(numChannels: 4);
  final out = img.Image(width: src.width, height: src.height, numChannels: 4);
  img.fill(out, color: img.ColorRgba8(255, 255, 255, 255));
  for (var y = 0; y < src.height; y++) {
    for (var x = 0; x < src.width; x++) {
      final p = src.getPixel(x, y);
      final r = p.r.toInt();
      final g = p.g.toInt();
      final b = p.b.toInt();
      final maxc = math.max(r, math.max(g, b));
      final minc = math.min(r, math.min(g, b));
      final chroma = maxc - minc;
      final luma = 0.299 * r + 0.587 * g + 0.114 * b;
      final checker = chroma < 32 && luma > 140;
      if (checker) continue;
      out.setPixelRgba(x, y, r, g, b, 255);
    }
  }
  return Uint8List.fromList(img.encodePng(out));
}
