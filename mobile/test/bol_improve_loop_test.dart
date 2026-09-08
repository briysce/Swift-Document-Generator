import 'dart:convert';
import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
import 'package:pdfrx/pdfrx.dart';
import 'package:swift_shipping_label/label_data.dart';
import 'package:swift_shipping_label/logo_image_process.dart';
import 'package:swift_shipping_label/logo_import_options.dart';
import 'package:swift_shipping_label/pdf/bol_label_pdf.dart';
import 'package:swift_shipping_label/pdf/shipping_label_pdf.dart';
import 'package:swift_shipping_label/pdf_render_options.dart';

/// Synthetic BOL matrix for the improve/training loop.
///
/// Writes PDF/PNG + layout_debug under `qa_bol/synthetic/`.
/// North star: `baseline_sample` / filled/qa_bol_preview/bol_preview_latest.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('BOL improve-loop matrix', () async {
    final root = Directory.current.parent;
    final logosDir = Directory('${root.path}/customer_logos');
    final synDir = Directory('${root.path}/qa_bol/synthetic');
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

    final shipping = await ShippingLabelPdf.load();
    final bol = BolLabelPdf(shipping);

    Future<Uint8List> loadProcessed(String name) async {
      final f = File('${logosDir.path}/$name');
      expect(f.existsSync(), isTrue, reason: f.path);
      return LogoImageProcessor.processWithOptions(
        await f.readAsBytes(),
        LogoImportOptions.standard(removeBackground: true),
      );
    }

    Uint8List? sampleLogo;
    try {
      final data =
          await rootBundle.load('assets/images/sample_customer_logo.png');
      sampleLogo = data.buffer.asUint8List();
    } catch (_) {}

    final arc = await loadProcessed('Arc Resources LTD.png');
    final trialta = await loadProcessed('Trialta Projects.png');
    final arjae = await loadProcessed('ARJAE.png');
    final propak = await loadProcessed('Propak-Energy-Services-Logo.png');
    final wpw =
        await loadProcessed('WPW Pipeline and Facility Construction.png');
    final bfl = await loadProcessed('bfl fabricators.png');
    final murrays = await loadProcessed('murrays_trucking.png');
    Uint8List tallIsh;
    final tallFile = File('${logosDir.path}/Spartan Delta Corp.png');
    if (tallFile.existsSync()) {
      tallIsh = await loadProcessed('Spartan Delta Corp.png');
    } else {
      tallIsh = arjae;
    }

    ShippingLabelData baseBol() {
      final d = ShippingLabelData({
        ...ShippingLabelData.bolSample.values,
      });
      // Mirror app sync: ORDER # reads order_num with sales_order fallback.
      if (d.get(BolFields.orderNum).isEmpty) {
        d.set(BolFields.orderNum, d.get(LabelFields.salesOrder));
      }
      return d;
    }

    Future<Map<String, dynamic>> renderCase({
      required String caseId,
      required ShippingLabelData data,
      required List<Uint8List> logos,
      String notes = '',
    }) async {
      BolLabelPdf.debugLayout = null;

      final pdfBytes = await bol.build(
        data: data,
        customerLogoBytes: logos,
        copies: const ['STORE COPY'],
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
        'png': {'width': pngW, 'height': pngH, 'scale': 2.0},
        ...?(BolLabelPdf.debugLayout),
        'field_values': {
          'sales_order': data.get(LabelFields.salesOrder),
          'order_num': data.get(BolFields.orderNum),
          'po_num': data.get(LabelFields.poNum),
          'project': data.get(LabelFields.project),
          'packing_list': data.get(BolFields.packingList),
          'packing_slip': data.get(LabelFields.packingSlip),
          'consignee_name': data.get(BolFields.consigneeName),
          'consignee_address': data.get(BolFields.consigneeAddress),
        },
      };

      await File('${debugDir.path}/$caseId.json').writeAsBytes(
        utf8.encode(const JsonEncoder.withIndent('  ').convert(layout)),
      );

      expect(File(pdfPath).lengthSync(), greaterThan(2000));
      expect(File(pngPath).lengthSync(), greaterThan(2000));

      final track = layout['tracking_row'];
      // ignore: avoid_print
      print(
        'BOL_IMPROVE $caseId logos=${logos.length} '
        'track=${track is Map ? (track['cells'] as List?)?.length : '?'} '
        'swift=${layout['swift_rect']} probill=${layout['probill_rect']}',
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
      caseId: 'baseline_sample',
      data: baseBol(),
      logos: sampleLogo == null ? const <Uint8List>[] : [sampleLogo],
      notes: 'north_star bolSample + sample logo',
    ));

    cases.add(await renderCase(
      caseId: 'logo_square',
      data: () {
        final d = baseBol();
        d.set(LabelFields.customer, 'ARJAE');
        d.set(BolFields.consigneeName, 'ARJAE');
        return d;
      }(),
      logos: [arjae],
      notes: 'square-ish customer logo',
    ));

    cases.add(await renderCase(
      caseId: 'logo_rect',
      data: () {
        final d = baseBol();
        d.set(LabelFields.customer, 'PROPAK ENERGY SERVICES');
        d.set(BolFields.consigneeName, 'PROPAK ENERGY SERVICES');
        return d;
      }(),
      logos: [propak],
      notes: 'rectangular customer logo',
    ));

    cases.add(await renderCase(
      caseId: 'logo_dual_arc_trialta',
      data: () {
        final d = baseBol();
        d.set(LabelFields.customer, 'ARC RESOURCES C/O TRIALTA');
        d.set(BolFields.consigneeName, 'ARC RESOURCES C/O TRIALTA');
        return d;
      }(),
      logos: [arc, trialta],
      notes: 'dual rectangular logos',
    ));

    cases.add(await renderCase(
      caseId: 'logo_wide',
      data: () {
        final d = baseBol();
        d.set(LabelFields.customer, 'WPW PIPELINE');
        d.set(BolFields.consigneeName, 'WPW PIPELINE');
        return d;
      }(),
      logos: [wpw],
      notes: 'wide customer lockup',
    ));

    cases.add(await renderCase(
      caseId: 'logo_tall_ish',
      data: () {
        final d = baseBol();
        d.set(LabelFields.customer, 'SPARTAN DELTA');
        d.set(BolFields.consigneeName, 'SPARTAN DELTA');
        return d;
      }(),
      logos: [tallIsh],
      notes: 'taller / square-ish lockup',
    ));

    cases.add(await renderCase(
      caseId: 'logo_circle_bfl',
      data: () {
        final d = baseBol();
        d.set(LabelFields.customer, 'BFL FABRICATORS');
        d.set(BolFields.consigneeName, 'BFL FABRICATORS');
        return d;
      }(),
      logos: [bfl],
      notes: 'circular / badge single',
    ));

    cases.add(await renderCase(
      caseId: 'logo_ultra_wide_murrays',
      data: () {
        final d = baseBol();
        d.set(LabelFields.customer, "MURRAY'S TRUCKING");
        d.set(BolFields.consigneeName, "MURRAY'S TRUCKING");
        return d;
      }(),
      logos: [murrays],
      notes: 'ultra-wide wordmark single',
    ));

    cases.add(await renderCase(
      caseId: 'logo_mixed_square_rect',
      data: () {
        final d = baseBol();
        d.set(LabelFields.customer, 'ARJAE C/O PROPAK');
        d.set(BolFields.consigneeName, 'ARJAE C/O PROPAK');
        return d;
      }(),
      logos: [arjae, propak],
      notes: 'mixed square+rect dual',
    ));

    cases.add(await renderCase(
      caseId: 'text_long_so',
      data: () {
        final d = baseBol();
        const v = 'SO-10482-A / SO-10483-B / SO-10484-C / SO-10485-D';
        d.set(LabelFields.salesOrder, v);
        d.set(BolFields.orderNum, v);
        return d;
      }(),
      logos: sampleLogo == null ? [arc] : [sampleLogo],
      notes: 'extreme sales order / order #',
    ));

    cases.add(await renderCase(
      caseId: 'text_long_po',
      data: () {
        final d = baseBol();
        d.set(
          LabelFields.poNum,
          'PCE-112124-03690-EXTRA-LONG-CUSTOMER-PO-REF-2026-NORTH-PAD',
        );
        return d;
      }(),
      logos: sampleLogo == null ? [arc] : [sampleLogo],
      notes: 'extreme PO #',
    ));

    cases.add(await renderCase(
      caseId: 'text_long_project',
      data: () {
        final d = baseBol();
        d.set(
          LabelFields.project,
          'NISKU FAB NORTH PAD COMPRESSION UPGRADE PHASE 2B TRAIN A',
        );
        return d;
      }(),
      logos: sampleLogo == null ? [arc] : [sampleLogo],
      notes: 'extreme project string',
    ));

    cases.add(await renderCase(
      caseId: 'text_long_packing_list',
      data: () {
        final d = baseBol();
        d.set(
          BolFields.packingList,
          'PL-1224618-REV3-BUNDLE-A-THROUGH-F-COMPLETE',
        );
        return d;
      }(),
      logos: sampleLogo == null ? [arc] : [sampleLogo],
      notes: 'extreme packing list #',
    ));

    cases.add(await renderCase(
      caseId: 'text_long_all_refs',
      data: () {
        final d = baseBol();
        const so = 'SO-10482-A / SO-10483-B / SO-10484-C / SO-10485-D';
        d.set(LabelFields.salesOrder, so);
        d.set(BolFields.orderNum, so);
        d.set(
          LabelFields.poNum,
          'PCE-112124-03690-EXTRA-LONG-CUSTOMER-PO-REF-2026-NORTH-PAD',
        );
        d.set(
          LabelFields.project,
          'NISKU FAB NORTH PAD COMPRESSION UPGRADE PHASE 2B TRAIN A',
        );
        d.set(
          BolFields.packingList,
          'PL-1224618-REV3-BUNDLE-A-THROUGH-F-COMPLETE',
        );
        return d;
      }(),
      logos: [arc, trialta],
      notes: 'all four tracking fields extreme + dual logos',
    ));

    cases.add(await renderCase(
      caseId: 'text_long_ship_to',
      data: () {
        final d = baseBol();
        d.set(
          BolFields.consigneeName,
          'ARC RESOURCES LTD CARE OF TRIALTA PROJECTS SITE RECEIVING '
          'DEPARTMENT NORTH PAD STAGING YARD COMPLEX',
        );
        d.set(
          BolFields.consigneeAddress,
          'UNIT 12-1488 INDUSTRIAL PARKWAY NORTHWEST BUILDING B\n'
          'FORT ST. JOHN INDUSTRIAL SUBDIVISION PHASE THREE\n'
          'FORT ST. JOHN, BRITISH COLUMBIA V1J 8H6 CANADA',
        );
        return d;
      }(),
      logos: [arc, trialta],
      notes: 'extreme ship-to name + address',
    ));

    final manifest = {
      'generated_at': DateTime.now().toUtc().toIso8601String(),
      'north_star': 'baseline_sample',
      'n_cases': cases.length,
      'cases': cases,
    };
    await File('${synDir.path}/manifest.json').writeAsBytes(
      utf8.encode(const JsonEncoder.withIndent('  ').convert(manifest)),
    );

    // Soft gates on north-star layout dump.
    final baselineDebug = jsonDecode(
      await File('${debugDir.path}/baseline_sample.json').readAsString(),
    ) as Map<String, dynamic>;
    expect(baselineDebug['title_bar'], isNotNull);
    expect(baselineDebug['swift_rect'], isNotNull);
    expect(baselineDebug['probill_rect'], isNotNull);
    final track = baselineDebug['tracking_row'] as Map<String, dynamic>?;
    expect(track, isNotNull);
    final cells = track!['cells'] as List<dynamic>;
    expect(cells.length, 4);
    // ORDER # should resolve sales_order when order_num synced.
    final orderCell = cells.firstWhere(
      (c) => (c as Map)['key'] == BolFields.orderNum,
    ) as Map;
    expect(orderCell['value_non_empty'], isTrue);

    // ignore: avoid_print
    print('Wrote ${cases.length} cases -> ${synDir.path}/manifest.json');
  }, timeout: const Timeout(Duration(minutes: 10)));
}
