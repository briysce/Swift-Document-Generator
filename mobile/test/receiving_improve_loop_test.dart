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

/// Synthetic Receiving Label matrix for the improve/training loop.
///
/// Writes PDF/PNG + layout_debug under `qa_receiving/synthetic/`.
/// Receiving intentionally uses SO under-pill `showRule: true` (SO → PM).
/// Do **not** apply Shipping SO/Contact lock (`showRule: false`).
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('receiving label improve-loop matrix', () async {
    final root = Directory.current.parent;
    final logosDir = Directory('${root.path}/customer_logos');
    final synDir = Directory('${root.path}/qa_receiving/synthetic');
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
      final scale = maxEdge /
          (decoded.width > decoded.height ? decoded.width : decoded.height);
      final w = (decoded.width * scale).round().clamp(8, maxEdge);
      final h = (decoded.height * scale).round().clamp(8, maxEdge);
      final tiny = img.copyResize(
        decoded,
        width: w,
        height: h,
        interpolation: img.Interpolation.average,
      );
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
      String? project,
      String? po,
      String? instructions,
      String? so,
      String? pm,
    }) {
      return ShippingLabelData.receivingSample.copy()
        ..set(LabelFields.customer, customer)
        ..set(
          LabelFields.project,
          project ?? ShippingLabelData.receivingSample.get(LabelFields.project),
        )
        ..set(
          LabelFields.poNum,
          po ?? ShippingLabelData.receivingSample.get(LabelFields.poNum),
        )
        ..set(
          LabelFields.specialInstructions,
          instructions ??
              ShippingLabelData.receivingSample
                  .get(LabelFields.specialInstructions),
        )
        ..set(
          LabelFields.salesOrder,
          so ?? ShippingLabelData.receivingSample.get(LabelFields.salesOrder),
        )
        ..set(
          LabelFields.swiftContact,
          pm ??
              ShippingLabelData.receivingSample.get(LabelFields.swiftContact),
        );
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
      final pdfBytes = await shipping.buildReceiving(
        data: data,
        customerLogoBytes: logos,
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
        'kind': 'receiving',
        'page_format': {
          'width': ShippingLabelPdf.pageFormat.width,
          'height': ShippingLabelPdf.pageFormat.height,
        },
        'png': {'width': pngW, 'height': pngH, 'scale': 2.0},
        'pill_bottom_y': ShippingLabelPdf.debugPillBottomY,
        'so_rule_y': ShippingLabelPdf.debugSoRuleY,
        'so_show_rule': ShippingLabelPdf.debugSoShowRule,
        'after_pill_gap_locked': 11.0,
        'receiving_expects_so_rule': true,
        'mx': ShippingLabelPdf.mx,
        'my': ShippingLabelPdf.my,
        'content_w': ShippingLabelPdf.contentW,
        'customer_logo_to_swift_gap': ShippingLabelPdf.customerLogoToSwiftGap,
        'has_instructions':
            data.get(LabelFields.specialInstructions).trim().isNotEmpty,
        'customer': data.get(LabelFields.customer),
        'sales_order': data.get(LabelFields.salesOrder),
        'pm': data.get(LabelFields.swiftContact),
      };
      if (ShippingLabelPdf.debugPillBottomY != null &&
          ShippingLabelPdf.debugSoRuleY != null) {
        layout['pill_to_rule_pt'] =
            ShippingLabelPdf.debugPillBottomY! -
                ShippingLabelPdf.debugSoRuleY!;
      }

      await File('${debugDir.path}/$caseId.json').writeAsBytes(
        utf8.encode(const JsonEncoder.withIndent('  ').convert(layout)),
      );

      expect(File(pdfPath).lengthSync(), greaterThan(2000));
      expect(File(pngPath).lengthSync(), greaterThan(2000));

      // ignore: avoid_print
      print(
        'RECV_IMPROVE $caseId '
        'showRule=${layout['so_show_rule']} '
        'pill=${layout['pill_bottom_y']} '
        'rule=${layout['so_rule_y']}',
      );

      return {
        'case_id': caseId,
        'notes': notes,
        'pdf': 'renders/$caseId.pdf',
        'png': 'renders/$caseId.png',
        'layout_debug': 'layout_debug/$caseId.json',
        'customer': data.get(LabelFields.customer),
        'logo_count': logos.length,
        'has_instructions': layout['has_instructions'],
      };
    }

    final cases = <Map<String, dynamic>>[];

    cases.add(await renderCase(
      caseId: 'baseline_receiving_sample',
      data: ShippingLabelData.receivingSample.copy(),
      logos: [arc],
      notes: 'north_star receivingSample',
    ));

    cases.add(await renderCase(
      caseId: 'baseline_dual_arc_trialta',
      data: baseData(customer: 'ARC RESOURCES C/O TRIALTA'),
      logos: [arc, trialta],
      notes: 'dual rectangular logos',
    ));

    cases.add(await renderCase(
      caseId: 'square_arjae',
      data: baseData(customer: 'ARJAE'),
      logos: [arjae],
      notes: 'square-ish logo height class',
    ));

    cases.add(await renderCase(
      caseId: 'rect_propak',
      data: baseData(customer: 'PROPAK ENERGY SERVICES'),
      logos: [propak],
      notes: 'rectangular logo height class',
    ));

    cases.add(await renderCase(
      caseId: 'mixed_arjae_propak',
      data: baseData(customer: 'ARJAE C/O PROPAK'),
      logos: [arjae, propak],
      notes: 'mixed square+rect dual',
    ));

    cases.add(await renderCase(
      caseId: 'shape_circle_bfl',
      data: baseData(customer: 'BFL FABRICATORS'),
      logos: [bfl],
      notes: 'circular / badge single',
    ));

    cases.add(await renderCase(
      caseId: 'shape_tall_smjv',
      data: baseData(customer: 'SMJV'),
      logos: [smjv],
      notes: 'tall / portrait single',
    ));

    cases.add(await renderCase(
      caseId: 'shape_ultra_wide_murrays',
      data: baseData(customer: "MURRAY'S TRUCKING"),
      logos: [murrays],
      notes: 'ultra-wide wordmark single',
    ));

    cases.add(await renderCase(
      caseId: 'shape_dual_badge_rect',
      data: baseData(customer: 'BFL C/O PROPAK'),
      logos: [bfl, propak],
      notes: 'circular + rectangular dual',
    ));

    cases.add(await renderCase(
      caseId: 'logo_lowres',
      data: baseData(customer: 'LOWRES LOCKUP INC'),
      logos: [lowres],
      notes: 'degraded small customer logo',
    ));

    cases.add(await renderCase(
      caseId: 'text_long_customer',
      data: baseData(
        customer:
            'NORTHERN ALBERTA PIPELINE FABRICATION AND ENERGY SERVICES '
            'HOLDINGS LIMITED PARTNERSHIP C/O TRIALTA PROJECTS',
      ),
      logos: [arc],
      notes: 'extreme customer name',
    ));

    cases.add(await renderCase(
      caseId: 'text_long_project',
      data: baseData(
        customer: 'CONOCOPHILLIPS CANADA (BRC) PARTNERSHIP',
        project:
            'GATEWAY PIPELINES AND CBR PAD 107 LATERAL FEL3 NORTH REGION '
            'COMPRESSION UPGRADE PHASE 2B WITH EXTENDED STAGING NOTES',
      ),
      logos: [arc],
      notes: 'extreme project',
    ));

    cases.add(await renderCase(
      caseId: 'text_long_po',
      data: baseData(
        customer: 'CONOCOPHILLIPS CANADA (BRC) PARTNERSHIP',
        po: '278-07-31-0009 / RELEASE 4 / HOLD DOCK / CONFIRM BEFORE STAGE',
      ),
      logos: [arc],
      notes: 'extreme PO',
    ));

    cases.add(await renderCase(
      caseId: 'text_long_so',
      data: baseData(
        customer: 'CONOCOPHILLIPS CANADA (BRC) PARTNERSHIP',
        so: '1380380-A / 1380381-B / 1380382-C',
      ),
      logos: [arc],
      notes: 'extreme sales order',
    ));

    cases.add(await renderCase(
      caseId: 'text_long_pm',
      data: baseData(
        customer: 'CONOCOPHILLIPS CANADA (BRC) PARTNERSHIP',
        pm: 'CHRIS ACORN PROJECT COORDINATOR NORTH REGION STAGING',
      ),
      logos: [arc],
      notes: 'extreme PM name',
    ));

    cases.add(await renderCase(
      caseId: 'text_long_instructions',
      data: baseData(
        customer: 'CONOCOPHILLIPS CANADA (BRC) PARTNERSHIP',
        instructions:
            'HOLD ON DOCK UNTIL SHIP CONFIRM; DO NOT BREAK SKID; '
            'FORKLIFT REQUIRED; PHOTOGRAPH ALL PIECES; STAGE AT NORTH BAY ONLY; '
            'CALL SITE SUPER BEFORE MOVING.',
      ),
      logos: [arc],
      notes: 'extreme special instructions (alert bg)',
    ));

    cases.add(await renderCase(
      caseId: 'instructions_empty',
      data: baseData(
        customer: 'CONOCOPHILLIPS CANADA (BRC) PARTNERSHIP',
        instructions: '',
      ),
      logos: [arc],
      notes: 'empty instructions — no red alert fill',
    ));

    final manifest = {
      'generated_at': DateTime.now().toUtc().toIso8601String(),
      'north_star': 'baseline_receiving_sample',
      'receiving_layout': {
        'after_pill_gap': 11.0,
        'so_show_rule': true,
        'note':
            'Receiving uses SO→PM hairline (showRule true). '
            'Do not apply Shipping SO/Contact lock (showRule false).',
      },
      'n_cases': cases.length,
      'cases': cases,
    };
    await File('${synDir.path}/manifest.json').writeAsBytes(
      utf8.encode(const JsonEncoder.withIndent('  ').convert(manifest)),
    );

    final baselineDebug = jsonDecode(
      await File('${debugDir.path}/baseline_receiving_sample.json')
          .readAsString(),
    ) as Map<String, dynamic>;
    expect(baselineDebug['so_show_rule'], isTrue);
    expect(baselineDebug['so_rule_y'], isNotNull);
    expect(baselineDebug['pill_bottom_y'], isNotNull);

    // ignore: avoid_print
    print('Wrote ${cases.length} cases -> ${synDir.path}/manifest.json');
  }, timeout: const Timeout(Duration(minutes: 8)));
}
