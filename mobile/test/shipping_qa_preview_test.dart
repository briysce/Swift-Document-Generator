import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
import 'package:pdfrx/pdfrx.dart';
import 'package:swift_shipping_label/label_data.dart';
import 'package:swift_shipping_label/logo_image_process.dart';
import 'package:swift_shipping_label/logo_import_options.dart';
import 'package:swift_shipping_label/logo_ink_fit.dart';
import 'package:swift_shipping_label/pdf/shipping_label_pdf.dart';
import 'package:swift_shipping_label/pdf_render_options.dart';

/// QA preview: sample shipping label PDF + PNG for visual review.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  Future<void> writePreview({
    required String stem,
    required ShippingLabelData data,
    required List<Uint8List> logos,
    required Directory outDir,
  }) async {
    final shipping = await ShippingLabelPdf.load();
    final pdfBytes = await shipping.build(
      data: data,
      customerLogoBytes: logos,
      piecePlan: const PieceCountPlan(palletCrates: 2, boxes: 1),
      options: PdfRenderOptions.defaults.copyWith(fontScale: 1.0),
    );

    final stamp = DateTime.now()
        .toIso8601String()
        .replaceAll(':', '-')
        .split('.')
        .first;
    final pdfPath = '${outDir.path}/${stem}_$stamp.pdf';
    final pngPath = '${outDir.path}/${stem}_$stamp.png';
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

    await File('${outDir.path}/${stem}_latest.pdf').writeAsBytes(pdfBytes);
    await File(pngPath).copy('${outDir.path}/${stem}_latest.png');

    // ignore: avoid_print
    print('Wrote $stem:\n  $pdfPath\n  $pngPath');
    expect(File(pdfPath).lengthSync(), greaterThan(2000));
    expect(File(pngPath).lengthSync(), greaterThan(2000));
  }

  ShippingLabelData sampleData({
    required String customer,
    required String shipTo,
  }) {
    return ShippingLabelData.sample.copy()
      ..set(LabelFields.customer, customer)
      ..set(LabelFields.salesOrder, 'SO-10482')
      ..set(LabelFields.poNum, 'PO-7781')
      ..set(LabelFields.project, 'NISKU FAB')
      ..set(LabelFields.shipTo, shipTo)
      ..set(
        LabelFields.location,
        '12341 271 RD\nFORT ST. JOHN, BC V1J 8H6',
      )
      ..set(LabelFields.attn, 'RECEIVING')
      ..set(LabelFields.carrier, "Murray's Trucking")
      ..set(BolFields.freightCharges, BolFields.freightPrepaid)
      ..set(BolFields.thirdPartyBilling, '')
      ..set(LabelFields.packingSlip, 'PS-2201')
      ..set(LabelFields.swiftContact, 'SEAN FITZPATRICK')
      ..set(LabelFields.specialInstructions, 'CALL BEFORE DELIVERY');
  }

  test('shipping label PDF preview for QA', () async {
    final root = Directory.current.parent;
    final logosDir = Directory('${root.path}/customer_logos');
    final outDir = Directory('${root.path}/filled/qa_shipping_preview');
    await outDir.create(recursive: true);

    Future<Uint8List> loadProcessed(String name) async {
      final f = File('${logosDir.path}/$name');
      expect(f.existsSync(), isTrue, reason: f.path);
      final raw = await f.readAsBytes();
      return LogoImageProcessor.processWithOptions(
        raw,
        LogoImportOptions.standard(removeBackground: true),
      );
    }

    final arcBytes = await loadProcessed('Arc Resources LTD.png');
    File? trialtaFile;
    for (final name in [
      'Trialta Projects.png',
      'TRIALTA.png',
      'Trialta.png',
    ]) {
      final f = File('${logosDir.path}/$name');
      if (f.existsSync()) {
        trialtaFile = f;
        break;
      }
    }
    trialtaFile ??= File(
      r'C:\Users\Brice\.cursor\projects\c-Users-Brice-Projects-swift-document-generator\assets\c__Users_Brice_AppData_Roaming_Cursor_User_workspaceStorage_4bff5e36c39e59914d9e1ad0a9394a2a_images_trialta-cc2b883c-b9f7-4c63-a3df-1f9a433fbfb1.png',
    );
    expect(trialtaFile.existsSync(), isTrue, reason: trialtaFile.path);
    final trialtaBytes = LogoImageProcessor.processWithOptions(
      await trialtaFile.readAsBytes(),
      LogoImportOptions.standard(removeBackground: true),
    );

    // Red/green targets: Arc source lockup is wide (aspect ≫ 1.4) → green;
    // Trialta is also rectangular → green. ARJAE (below) covers red/square.
    final arcInk = LogoInkFit.prepare(arcBytes).ink;
    final trialtaInk = LogoInkFit.prepare(trialtaBytes).ink;
    expect(
      arcInk.isSquareOrCircle,
      isFalse,
      reason: 'Arc Resources LTD.png is a wide lockup, not square/circle',
    );
    expect(trialtaInk.isSquareOrCircle, isFalse, reason: 'Trialta should be rect');
    expect(
      arcInk.targetHeight(
        squareH: ShippingLabelPdf.squareLogoTargetH,
        rectH: ShippingLabelPdf.rectLogoTargetH,
      ),
      ShippingLabelPdf.rectLogoTargetH,
    );
    expect(
      trialtaInk.targetHeight(
        squareH: ShippingLabelPdf.squareLogoTargetH,
        rectH: ShippingLabelPdf.rectLogoTargetH,
      ),
      ShippingLabelPdf.rectLogoTargetH,
    );

    // Dual rectangular (Arc + Trialta) + SO/contact clearance check.
    await writePreview(
      stem: 'shipping_preview',
      data: sampleData(
        customer: 'ARC RESOURCES C/O TRIALTA',
        shipTo: 'ARC RESOURCES C/O TRIALTA',
      ),
      logos: [arcBytes, trialtaBytes],
      outDir: outDir,
    );

    // Objective clearance: contiguous SO peach bottom must sit above contact name.
    final previewPng = File('${outDir.path}/shipping_preview_latest.png');
    final decoded = img.decodePng(await previewPng.readAsBytes());
    expect(decoded, isNotNull);
    final full = decoded!;
    final pw = full.width;
    final ph = full.height;
    bool isPeach(img.Pixel p) =>
        (p.r - 248).abs() < 12 &&
        (p.g - 235).abs() < 12 &&
        (p.b - 231).abs() < 12;
    // Longest contiguous peach run in the right column = SO pill.
    var bestStart = -1;
    var bestEnd = -1;
    var runStart = -1;
    for (var y = (ph * 0.35).floor(); y < (ph * 0.85).floor(); y++) {
      var peach = 0;
      for (var x = (pw * 0.58).floor(); x < (pw * 0.92).floor(); x++) {
        if (isPeach(full.getPixel(x, y))) peach++;
      }
      if (peach > 400) {
        runStart = runStart < 0 ? y : runStart;
      } else if (runStart >= 0) {
        final end = y - 1;
        if (bestStart < 0 || end - runStart > bestEnd - bestStart) {
          bestStart = runStart;
          bestEnd = end;
        }
        runStart = -1;
      }
    }
    if (runStart >= 0) {
      final end = (ph * 0.85).floor() - 1;
      if (bestStart < 0 || end - runStart > bestEnd - bestStart) {
        bestStart = runStart;
        bestEnd = end;
      }
    }
    expect(bestEnd, greaterThan(0), reason: 'SO peach pill not found');
    var nameTop = -1;
    for (var y = bestEnd + 1; y < (ph * 0.92).floor(); y++) {
      var black = 0;
      for (var x = (pw * 0.58).floor(); x < (pw * 0.92).floor(); x++) {
        final p = full.getPixel(x, y);
        if (p.r < 40 && p.g < 40 && p.b < 40) black++;
      }
      if (black > 40) {
        nameTop = y;
        break;
      }
    }
    expect(nameTop, greaterThan(0), reason: 'Contact name ink not found');
    final peachToName = nameTop - bestEnd;
    // ignore: avoid_print
    print(
      'SHIP_LAYOUT PDF: pillBottom=${ShippingLabelPdf.debugPillBottomY} '
      'soRuleY=${ShippingLabelPdf.debugSoRuleY} '
      'soShowRule=${ShippingLabelPdf.debugSoShowRule} '
      'contactLabelY=${ShippingLabelPdf.debugContactLabelY} '
      'contactNameY=${ShippingLabelPdf.debugContactNameBaselineY} '
      'pieceRuleY=${ShippingLabelPdf.debugPieceRuleY}',
    );
    // ignore: avoid_print
    print(
      'SHIP_LAYOUT PNG: peachEnd=$bestEnd nameTop=$nameTop '
      'peachToNamePx=$peachToName (~${(peachToName / 2).toStringAsFixed(1)}pt)',
    );
    expect(
      ShippingLabelPdf.debugSoShowRule,
      isFalse,
      reason: 'Shipping must not draw under-pill SO rule',
    );
    expect(
      ShippingLabelPdf.debugSoRuleY,
      isNull,
      reason: 'Under-pill SO ruleY must be unset on shipping',
    );
    final pillBottom = ShippingLabelPdf.debugPillBottomY!;
    final contactLabel = ShippingLabelPdf.debugContactLabelY!;
    final nameBase = ShippingLabelPdf.debugContactNameBaselineY!;
    final pieceRule = ShippingLabelPdf.debugPieceRuleY!;
    final pillToLabel = pillBottom - contactLabel;
    // APPROVED LOCK (2026-08-20): afterPillGap=11 under SO pill before CONTACT label.
    // Do not loosen/tighten without explicit user format change.
    expect(
      pillToLabel,
      greaterThanOrEqualTo(9),
      reason: 'CONTACT label too tight under pill ($pillToLabel)',
    );
    expect(
      pillToLabel,
      lessThanOrEqualTo(13),
      reason: 'CONTACT label too far under pill ($pillToLabel)',
    );
    expect(
      nameBase - pieceRule,
      greaterThanOrEqualTo(8),
      reason: 'Contact name must clear piece-band rule by ≥8pt',
    );
    // Pixel guard: no full-width C8-ish hairline between peach bottom and name.
    var hairlineRows = 0;
    for (var y = bestEnd + 1; y < nameTop; y++) {
      var rulePx = 0;
      for (var x = (pw * 0.58).floor(); x < (pw * 0.92).floor(); x++) {
        final p = full.getPixel(x, y);
        final r = p.r.toInt();
        final g = p.g.toInt();
        final b = p.b.toInt();
        if ((r - g).abs() < 10 &&
            (g - b).abs() < 10 &&
            r >= 185 &&
            r <= 215) {
          rulePx++;
        }
      }
      if (rulePx > 150) hairlineRows++;
    }
    expect(
      hairlineRows,
      equals(0),
      reason:
          'Hairline between SO pill and contact name (culprit drawLine still on)',
    );
    // SO→name air at 2×: afterPillGap(11) + label→value(3) + centered pad.
    expect(
      peachToName,
      greaterThanOrEqualTo(24),
      reason:
          'SO peach (y=$bestEnd) too close to contact name (y=$nameTop)',
    );
    expect(
      peachToName,
      lessThanOrEqualTo(56),
      reason:
          'SO peach (y=$bestEnd) too far from contact name (y=$nameTop) — tighten afterPillGap',
    );

    // Square-ish (ARJAE) vs rectangular (Propak) single-logo cases.
    final arjae = await loadProcessed('ARJAE.png');
    final propak = await loadProcessed('Propak-Energy-Services-Logo.png');
    final arjaeInk = LogoInkFit.prepare(arjae).ink;
    final propakInk = LogoInkFit.prepare(propak).ink;
    expect(
      arjaeInk.isSquareOrCircle,
      isTrue,
      reason: 'ARJAE should be square/circle',
    );
    expect(
      propakInk.isSquareOrCircle,
      isFalse,
      reason: 'Propak should be rectangular',
    );

    await writePreview(
      stem: 'shipping_square_arjae',
      data: sampleData(customer: 'ARJAE', shipTo: 'ARJAE'),
      logos: [arjae],
      outDir: outDir,
    );
    await writePreview(
      stem: 'shipping_rect_propak',
      data: sampleData(
        customer: 'PROPAK ENERGY SERVICES',
        shipTo: 'PROPAK ENERGY SERVICES',
      ),
      logos: [propak],
      outDir: outDir,
    );

    // Mixed: square + rectangular dual (each height class + pink clamp).
    await writePreview(
      stem: 'shipping_mixed_arjae_propak',
      data: sampleData(
        customer: 'ARJAE C/O PROPAK',
        shipTo: 'ARJAE C/O PROPAK',
      ),
      logos: [arjae, propak],
      outDir: outDir,
    );
  }, timeout: const Timeout(Duration(minutes: 5)));
}
