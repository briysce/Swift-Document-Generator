import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
import 'package:pdfrx/pdfrx.dart';
import 'package:swift_shipping_label/label_data.dart';
import 'package:swift_shipping_label/pdf/bol_label_pdf.dart';
import 'package:swift_shipping_label/pdf/shipping_label_pdf.dart';

/// Ad-hoc preview generator: dual customer logos (square/circular + wide
/// rectangular) on Shipping, Receiving, and BOL. Not a regression test.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('dual customer logo previews: Shipping, Receiving, BOL', () async {
    final root = Directory.current.parent;
    final squareLogo =
        File('${root.path}/customer_logos/ARJAE.png').readAsBytesSync();
    final rectLogo = File(
      '${root.path}/customer_logos/Propak-Energy-Services-Logo.png',
    ).readAsBytesSync();
    final dualLogos = [squareLogo, rectLogo];

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

    final outDir = Directory('${root.path}/filled/dual_logo_previews');
    await outDir.create(recursive: true);

    Future<void> renderToPng(Uint8List pdfBytes, String name) async {
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
        final pngPath = '${outDir.path}/$name.png';
        await File(pngPath).writeAsBytes(img.encodePng(full));
        // ignore: avoid_print
        print('Wrote $pngPath');
        rendered.dispose();
      } finally {
        await doc.dispose();
      }
    }

    final shipping = await ShippingLabelPdf.load();

    final shippingData = ShippingLabelData.sample.copy()
      ..set(LabelFields.customer, 'ARJAE C/O PROPAK ENERGY SERVICES');
    final shippingBytes = await shipping.build(
      data: shippingData,
      customerLogoBytes: dualLogos,
    );
    await File('${outDir.path}/shipping_dual_logo.pdf')
        .writeAsBytes(shippingBytes);
    await renderToPng(shippingBytes, 'shipping_dual_logo');

    final receivingData = ShippingLabelData.sample.copy()
      ..set(LabelFields.customer, 'ARJAE C/O PROPAK ENERGY SERVICES');
    final receivingBytes = await shipping.buildReceiving(
      data: receivingData,
      customerLogoBytes: dualLogos,
    );
    await File('${outDir.path}/receiving_dual_logo.pdf')
        .writeAsBytes(receivingBytes);
    await renderToPng(receivingBytes, 'receiving_dual_logo');

    final bol = BolLabelPdf(shipping);
    final bolData = ShippingLabelData({
      ...ShippingLabelData.bolSample.values,
      BolFields.freightCharges: BolFields.freightCustomerPickup,
      LabelFields.customer: 'ARJAE C/O PROPAK ENERGY SERVICES',
    });
    final bolBytes = await bol.build(
      data: bolData,
      customerLogoBytes: dualLogos,
    );
    await File('${outDir.path}/bol_dual_logo.pdf').writeAsBytes(bolBytes);
    await renderToPng(bolBytes, 'bol_dual_logo');

    expect(shippingBytes.length, greaterThan(1000));
    expect(receivingBytes.length, greaterThan(1000));
    expect(bolBytes.length, greaterThan(1000));
  }, timeout: const Timeout(Duration(minutes: 3)));

  test('BOL preview: two same-shape (rectangular) customer logos', () async {
    final root = Directory.current.parent;
    final rectLogo = File(
      '${root.path}/customer_logos/Propak-Energy-Services-Logo.png',
    ).readAsBytesSync();

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

    final outDir = Directory('${root.path}/filled/dual_logo_previews');
    await outDir.create(recursive: true);

    final shipping = await ShippingLabelPdf.load();
    final bol = BolLabelPdf(shipping);
    final bolData = ShippingLabelData({
      ...ShippingLabelData.bolSample.values,
      BolFields.freightCharges: BolFields.freightCustomerPickup,
      LabelFields.customer: 'PROPAK C/O PROPAK ENERGY SERVICES',
    });
    final bolBytes = await bol.build(
      data: bolData,
      customerLogoBytes: [rectLogo, rectLogo],
    );
    await File('${outDir.path}/bol_dual_propak_logo.pdf')
        .writeAsBytes(bolBytes);

    final doc = await PdfDocument.openData(bolBytes);
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
      final pngPath = '${outDir.path}/bol_dual_propak_logo.png';
      await File(pngPath).writeAsBytes(img.encodePng(full));
      // ignore: avoid_print
      print('Wrote $pngPath');
      rendered.dispose();
    } finally {
      await doc.dispose();
    }

    expect(bolBytes.length, greaterThan(1000));
  }, timeout: const Timeout(Duration(minutes: 3)));

  test('BOL preview: two different rectangular logos (Propak + Arc)', () async {
    final root = Directory.current.parent;
    final propak = File(
      '${root.path}/customer_logos/Propak-Energy-Services-Logo.png',
    ).readAsBytesSync();
    final arc =
        File('${root.path}/customer_logos/Arc Resources LTD.png')
            .readAsBytesSync();

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

    final outDir = Directory('${root.path}/filled/dual_logo_previews');
    await outDir.create(recursive: true);

    final shipping = await ShippingLabelPdf.load();
    final bol = BolLabelPdf(shipping);
    final bolData = ShippingLabelData({
      ...ShippingLabelData.bolSample.values,
      BolFields.freightCharges: BolFields.freightCustomerPickup,
      LabelFields.customer: 'ARC RESOURCES C/O PROPAK ENERGY SERVICES',
    });
    final bolBytes = await bol.build(
      data: bolData,
      customerLogoBytes: [propak, arc],
    );
    await File('${outDir.path}/bol_dual_propak_arc_logo.pdf')
        .writeAsBytes(bolBytes);

    final doc = await PdfDocument.openData(bolBytes);
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
      final pngPath = '${outDir.path}/bol_dual_propak_arc_logo.png';
      await File(pngPath).writeAsBytes(img.encodePng(full));
      // ignore: avoid_print
      print('Wrote $pngPath');
      rendered.dispose();
    } finally {
      await doc.dispose();
    }

    expect(bolBytes.length, greaterThan(1000));
  }, timeout: const Timeout(Duration(minutes: 3)));

  test('BOL preview: two square/circular customer logos (ARJAE + bfl)', () async {
    final root = Directory.current.parent;
    final arjae =
        File('${root.path}/customer_logos/ARJAE.png').readAsBytesSync();
    final bfl = File('${root.path}/customer_logos/bfl fabricators.png')
        .readAsBytesSync();

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

    final outDir = Directory('${root.path}/filled/dual_logo_previews');
    await outDir.create(recursive: true);

    final shipping = await ShippingLabelPdf.load();
    final bol = BolLabelPdf(shipping);
    final bolData = ShippingLabelData({
      ...ShippingLabelData.bolSample.values,
      BolFields.freightCharges: BolFields.freightCustomerPickup,
      LabelFields.customer: 'ARJAE C/O BFL FABRICATORS',
    });
    final bolBytes = await bol.build(
      data: bolData,
      customerLogoBytes: [arjae, bfl],
    );
    await File('${outDir.path}/bol_dual_square_logo.pdf').writeAsBytes(bolBytes);

    final doc = await PdfDocument.openData(bolBytes);
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
      final pngPath = '${outDir.path}/bol_dual_square_logo.png';
      await File(pngPath).writeAsBytes(img.encodePng(full));
      // ignore: avoid_print
      print('Wrote $pngPath');
      rendered.dispose();
    } finally {
      await doc.dispose();
    }

    expect(bolBytes.length, greaterThan(1000));
  }, timeout: const Timeout(Duration(minutes: 3)));

  Future<void> renderBolSingleLogo({
    required String logoFile,
    required String customerName,
    required String outName,
  }) async {
    final root = Directory.current.parent;
    final logo = File('${root.path}/customer_logos/$logoFile').readAsBytesSync();

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

    final outDir = Directory('${root.path}/filled/dual_logo_previews');
    await outDir.create(recursive: true);

    final shipping = await ShippingLabelPdf.load();
    final bol = BolLabelPdf(shipping);
    final bolData = ShippingLabelData({
      ...ShippingLabelData.bolSample.values,
      BolFields.freightCharges: BolFields.freightCustomerPickup,
      LabelFields.customer: customerName,
    });
    final bolBytes = await bol.build(data: bolData, customerLogoBytes: [logo]);
    await File('${outDir.path}/$outName.pdf').writeAsBytes(bolBytes);

    final doc = await PdfDocument.openData(bolBytes);
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
      final pngPath = '${outDir.path}/$outName.png';
      await File(pngPath).writeAsBytes(img.encodePng(full));
      // ignore: avoid_print
      print('Wrote $pngPath');
      rendered.dispose();
    } finally {
      await doc.dispose();
    }

    expect(bolBytes.length, greaterThan(1000));
  }

  test('BOL preview: single square/circular customer logo (ARJAE)', () async {
    await renderBolSingleLogo(
      logoFile: 'ARJAE.png',
      customerName: 'ARJAE DESIGN SOLUTIONS LTD.',
      outName: 'bol_single_square_logo',
    );
  }, timeout: const Timeout(Duration(minutes: 3)));

  test('BOL preview: single rectangular/wide customer logo (Propak)', () async {
    await renderBolSingleLogo(
      logoFile: 'Propak-Energy-Services-Logo.png',
      customerName: 'PROPAK ENERGY SERVICES',
      outName: 'bol_single_rect_logo',
    );
  }, timeout: const Timeout(Duration(minutes: 3)));
}
