import 'dart:convert';
import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
import 'package:pdfrx/pdfrx.dart';
import 'package:swift_shipping_label/label_data.dart';
import 'package:swift_shipping_label/logo_image_process.dart';
import 'package:swift_shipping_label/logo_import_options.dart';
import 'package:swift_shipping_label/pdf/shipping_label_pdf.dart';
import 'package:swift_shipping_label/pdf_render_options.dart';

/// Synthetic Shipping Label matrix for the improve/training loop.
///
/// Writes PDF/PNG + layout_debug under `qa_shipping/synthetic/`.
/// North star: approved SO/Contact geometry (see shipping-label-approved-layout).
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('shipping label improve-loop matrix', () async {
    final root = Directory.current.parent;
    final logosDir = Directory('${root.path}/customer_logos');
    final synDir = Directory('${root.path}/qa_shipping/synthetic');
    final outDir = Directory('${synDir.path}/renders');
    final debugDir = Directory('${synDir.path}/layout_debug');
    await outDir.create(recursive: true);
    await debugDir.create(recursive: true);

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

    Future<Uint8List> loadProcessed(String name) async {
      final f = File('${logosDir.path}/$name');
      expect(f.existsSync(), isTrue, reason: f.path);
      return LogoImageProcessor.processWithOptions(
        await f.readAsBytes(),
        LogoImportOptions.standard(removeBackground: true),
      );
    }

    Uint8List degradeLogo(Uint8List bytes, {int maxEdge = 48}) {
      final decoded = img.decodeImage(bytes);
      if (decoded == null) return bytes;
      final scale = maxEdge / (decoded.width > decoded.height
          ? decoded.width
          : decoded.height);
      final w = (decoded.width * scale).round().clamp(8, maxEdge);
      final h = (decoded.height * scale).round().clamp(8, maxEdge);
      final tiny = img.copyResize(
        decoded,
        width: w,
        height: h,
        interpolation: img.Interpolation.average,
      );
      // Mild JPEG crush to mimic phone / fax imports.
      final jpg = img.encodeJpg(tiny, quality: 35);
      return LogoImageProcessor.processWithOptions(
        Uint8List.fromList(jpg),
        LogoImportOptions.standard(removeBackground: true),
      );
    }

    final arc = await loadProcessed('Arc Resources LTD.png');
    final trialta = await loadProcessed('Trialta Projects.png');
    final arjae = await loadProcessed('ARJAE.png');
    final propak = await loadProcessed('Propak-Energy-Services-Logo.png');
    final bfl = await loadProcessed('bfl fabricators.png');
    final smjv = await loadProcessed('SMJV_Alpha.png');
    final murrays = await loadProcessed('murrays_trucking.png');
    // Prefer a naturally small lockup; fall back to downscaled ARJAE.
    Uint8List lowres;
    final smallCandidates = [
      'Spartan Delta Corp.png',
      'Trialta Projects.png',
      'WPW Pipeline and Facility Construction.png',
    ];
    File? smallFile;
    for (final name in smallCandidates) {
      final f = File('${logosDir.path}/$name');
      if (f.existsSync()) {
        smallFile = f;
        break;
      }
    }
    if (smallFile != null) {
      lowres = degradeLogo(await smallFile.readAsBytes());
    } else {
      lowres = degradeLogo(arjae);
    }

    ShippingLabelData baseData({
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

    Future<Map<String, dynamic>> renderCase({
      required String caseId,
      required ShippingLabelData data,
      required List<Uint8List> logos,
      String notes = '',
    }) async {
      ShippingLabelPdf.debugPillBottomY = null;
      ShippingLabelPdf.debugSoRuleY = null;
      ShippingLabelPdf.debugContactLabelY = null;
      ShippingLabelPdf.debugContactNameBaselineY = null;
      ShippingLabelPdf.debugPieceRuleY = null;
      ShippingLabelPdf.debugSoShowRule = null;

      final shipping = await ShippingLabelPdf.load();
      final pdfBytes = await shipping.build(
        data: data,
        customerLogoBytes: logos,
        piecePlan: const PieceCountPlan(palletCrates: 2, boxes: 1),
        options: PdfRenderOptions.defaults.copyWith(fontScale: 1.0),
      );

      final pdfPath = '${outDir.path}/$caseId.pdf';
      final pngPath = '${outDir.path}/$caseId.png';
      await File(pdfPath).writeAsBytes(pdfBytes);

      final doc = await PdfDocument.openData(pdfBytes);
      late final int pngW;
      late final int pngH;
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
        pngW = full.width;
        pngH = full.height;
        await File(pngPath).writeAsBytes(img.encodePng(full));
        rendered.dispose();
      } finally {
        await doc.dispose();
      }

      final layout = <String, dynamic>{
        'case_id': caseId,
        'page_format': {
          'width': ShippingLabelPdf.pageFormat.width,
          'height': ShippingLabelPdf.pageFormat.height,
        },
        'png': {'width': pngW, 'height': pngH, 'scale': 2.0},
        'pill_bottom_y': ShippingLabelPdf.debugPillBottomY,
        'so_rule_y': ShippingLabelPdf.debugSoRuleY,
        'so_show_rule': ShippingLabelPdf.debugSoShowRule,
        'contact_label_y': ShippingLabelPdf.debugContactLabelY,
        'contact_name_baseline_y': ShippingLabelPdf.debugContactNameBaselineY,
        'piece_rule_y': ShippingLabelPdf.debugPieceRuleY,
        'after_pill_gap_locked': 11.0,
        'mx': ShippingLabelPdf.mx,
        'my': ShippingLabelPdf.my,
        'gutter': ShippingLabelPdf.gutter,
        'col_w': ShippingLabelPdf.colW,
        'customer_logo_to_swift_gap': ShippingLabelPdf.customerLogoToSwiftGap,
      };
      if (ShippingLabelPdf.debugPillBottomY != null &&
          ShippingLabelPdf.debugContactLabelY != null) {
        layout['pill_to_contact_label_pt'] =
            ShippingLabelPdf.debugPillBottomY! -
                ShippingLabelPdf.debugContactLabelY!;
      }
      if (ShippingLabelPdf.debugContactNameBaselineY != null &&
          ShippingLabelPdf.debugPieceRuleY != null) {
        layout['name_clear_piece_pt'] =
            ShippingLabelPdf.debugContactNameBaselineY! -
                ShippingLabelPdf.debugPieceRuleY!;
      }

      await File('${debugDir.path}/$caseId.json').writeAsBytes(
        utf8.encode(const JsonEncoder.withIndent('  ').convert(layout)),
      );

      expect(File(pdfPath).lengthSync(), greaterThan(2000));
      expect(File(pngPath).lengthSync(), greaterThan(2000));

      // ignore: avoid_print
      print(
        'SHIP_IMPROVE $caseId '
        'pillToLabel=${layout['pill_to_contact_label_pt']} '
        'nameClear=${layout['name_clear_piece_pt']} '
        'showRule=${layout['so_show_rule']}',
      );

      return {
        'case_id': caseId,
        'notes': notes,
        'pdf': 'renders/$caseId.pdf',
        'png': 'renders/$caseId.png',
        'layout_debug': 'layout_debug/$caseId.json',
        'customer': data.get(LabelFields.customer),
        'logo_count': logos.length,
      };
    }

    final cases = <Map<String, dynamic>>[];

    cases.add(await renderCase(
      caseId: 'baseline_dual_arc_trialta',
      data: baseData(
        customer: 'ARC RESOURCES C/O TRIALTA',
        shipTo: 'ARC RESOURCES C/O TRIALTA',
      ),
      logos: [arc, trialta],
      notes: 'north_star dual rectangular',
    ));

    cases.add(await renderCase(
      caseId: 'square_arjae',
      data: baseData(customer: 'ARJAE', shipTo: 'ARJAE'),
      logos: [arjae],
      notes: 'square-ish logo height class',
    ));

    cases.add(await renderCase(
      caseId: 'rect_propak',
      data: baseData(
        customer: 'PROPAK ENERGY SERVICES',
        shipTo: 'PROPAK ENERGY SERVICES',
      ),
      logos: [propak],
      notes: 'rectangular logo height class',
    ));

    cases.add(await renderCase(
      caseId: 'mixed_arjae_propak',
      data: baseData(
        customer: 'ARJAE C/O PROPAK',
        shipTo: 'ARJAE C/O PROPAK',
      ),
      logos: [arjae, propak],
      notes: 'mixed square+rect dual',
    ));

    cases.add(await renderCase(
      caseId: 'shape_circle_bfl',
      data: baseData(customer: 'BFL FABRICATORS', shipTo: 'BFL FABRICATORS'),
      logos: [bfl],
      notes: 'circular / badge single',
    ));

    cases.add(await renderCase(
      caseId: 'shape_tall_smjv',
      data: baseData(customer: 'SMJV', shipTo: 'SMJV'),
      logos: [smjv],
      notes: 'tall / portrait single',
    ));

    cases.add(await renderCase(
      caseId: 'shape_ultra_wide_murrays',
      data: baseData(
        customer: "MURRAY'S TRUCKING",
        shipTo: "MURRAY'S TRUCKING",
      ),
      logos: [murrays],
      notes: 'ultra-wide wordmark single',
    ));

    cases.add(await renderCase(
      caseId: 'shape_dual_badge_rect',
      data: baseData(
        customer: 'BFL C/O PROPAK',
        shipTo: 'BFL C/O PROPAK',
      ),
      logos: [bfl, propak],
      notes: 'circular + rectangular dual',
    ));

    cases.add(await renderCase(
      caseId: 'logo_lowres',
      data: baseData(
        customer: 'LOWRES LOCKUP INC',
        shipTo: 'LOWRES LOCKUP INC',
      ),
      logos: [lowres],
      notes: 'degraded small customer logo',
    ));

    cases.add(await renderCase(
      caseId: 'text_long_customer',
      data: baseData(
        customer:
            'NORTHERN ALBERTA PIPELINE FABRICATION AND ENERGY SERVICES '
            'HOLDINGS LIMITED PARTNERSHIP C/O TRIALTA PROJECTS',
        shipTo: 'ARC RESOURCES',
      ),
      logos: [arc],
      notes: 'extreme customer name',
    ));

    cases.add(await renderCase(
      caseId: 'text_long_ship_to',
      data: baseData(
        customer: 'ARC RESOURCES',
        shipTo:
            'ARC RESOURCES LTD CARE OF TRIALTA PROJECTS SITE RECEIVING '
            'DEPARTMENT NORTH PAD STAGING YARD COMPLEX',
      ),
      logos: [arc, trialta],
      notes: 'extreme ship-to',
    ));

    cases.add(await renderCase(
      caseId: 'text_long_address',
      data: () {
        final d = baseData(
          customer: 'ARC RESOURCES C/O TRIALTA',
          shipTo: 'ARC RESOURCES C/O TRIALTA',
        );
        d.set(
          LabelFields.location,
          'UNIT 12-1488 INDUSTRIAL PARKWAY NORTHWEST BUILDING B\n'
          'FORT ST. JOHN INDUSTRIAL SUBDIVISION PHASE THREE\n'
          'FORT ST. JOHN, BRITISH COLUMBIA V1J 8H6 CANADA',
        );
        return d;
      }(),
      logos: [arc, trialta],
      notes: 'extreme multi-line address',
    ));

    cases.add(await renderCase(
      caseId: 'text_long_contact',
      data: () {
        final d = baseData(
          customer: 'ARC RESOURCES C/O TRIALTA',
          shipTo: 'ARC RESOURCES C/O TRIALTA',
        );
        d.set(
          LabelFields.swiftContact,
          'SEAN FITZPATRICK PROJECT COORDINATOR NORTH REGION',
        );
        return d;
      }(),
      logos: [arc, trialta],
      notes: 'extreme swift contact name',
    ));

    cases.add(await renderCase(
      caseId: 'text_long_project',
      data: () {
        final d = baseData(
          customer: 'ARC RESOURCES C/O TRIALTA',
          shipTo: 'ARC RESOURCES C/O TRIALTA',
        );
        d.set(
          LabelFields.project,
          'NISKU FAB NORTH PAD COMPRESSION UPGRADE PHASE 2B',
        );
        return d;
      }(),
      logos: [arc, trialta],
      notes: 'extreme project string',
    ));

    cases.add(await renderCase(
      caseId: 'text_long_so',
      data: () {
        final d = baseData(
          customer: 'ARC RESOURCES C/O TRIALTA',
          shipTo: 'ARC RESOURCES C/O TRIALTA',
        );
        d.set(LabelFields.salesOrder, 'SO-10482-A / SO-10483-B / SO-10484-C');
        return d;
      }(),
      logos: [arc, trialta],
      notes: 'extreme sales-order string',
    ));

    cases.add(await renderCase(
      caseId: 'text_long_instructions',
      data: () {
        final d = baseData(
          customer: 'ARC RESOURCES C/O TRIALTA',
          shipTo: 'ARC RESOURCES C/O TRIALTA',
        );
        d.set(
          LabelFields.specialInstructions,
          'CALL SITE SUPER BEFORE DELIVERY; FORKLIFT REQUIRED; '
          'DO NOT STACK; PHOTOGRAPH ALL CRATES; DELIVER TO NORTH GATE ONLY; '
          'REFERENCE PO-7781 ON EVERY PIECE.',
        );
        return d;
      }(),
      logos: [arc, trialta],
      notes: 'extreme special instructions',
    ));

    cases.add(await renderCase(
      caseId: 'freight_customer_pickup',
      data: () {
        final d = baseData(
          customer: 'ARC RESOURCES C/O TRIALTA',
          shipTo: 'ARC RESOURCES C/O TRIALTA',
        );
        d.set(BolFields.freightCharges, BolFields.freightCustomerPickup);
        d.set(LabelFields.carrier, 'CUSTOMER PICK-UP');
        return d;
      }(),
      logos: [arc, trialta],
      notes: 'customer pick-up freight term',
    ));

    final manifest = {
      'generated_at': DateTime.now().toUtc().toIso8601String(),
      'north_star': 'baseline_dual_arc_trialta',
      'approved_lock': {
        'after_pill_gap': 11.0,
        'so_show_rule': false,
        'contact_label_to_value': 3.0,
      },
      'n_cases': cases.length,
      'cases': cases,
    };
    await File('${synDir.path}/manifest.json').writeAsBytes(
      utf8.encode(const JsonEncoder.withIndent('  ').convert(manifest)),
    );

    // Soft gate on north-star layout dump (same bands as shipping_qa_preview).
    final baselineDebug = jsonDecode(
      await File('${debugDir.path}/baseline_dual_arc_trialta.json').readAsString(),
    ) as Map<String, dynamic>;
    expect(baselineDebug['so_show_rule'], isFalse);
    final pillToLabel = (baselineDebug['pill_to_contact_label_pt'] as num?)?.toDouble();
    expect(pillToLabel, isNotNull);
    expect(pillToLabel!, greaterThanOrEqualTo(9));
    expect(pillToLabel, lessThanOrEqualTo(13));
    final nameClear = (baselineDebug['name_clear_piece_pt'] as num?)?.toDouble();
    expect(nameClear, isNotNull);
    expect(nameClear!, greaterThanOrEqualTo(8));

    // ignore: avoid_print
    print('Wrote ${cases.length} cases -> ${synDir.path}/manifest.json');
  }, timeout: const Timeout(Duration(minutes: 8)));
}
