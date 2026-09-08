import 'dart:convert';
import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
import 'package:path/path.dart' as p;
import 'package:swift_shipping_label/address_book_sync.dart';
import 'package:swift_shipping_label/app_storage.dart';
import 'package:swift_shipping_label/document_history_sync.dart';
import 'package:swift_shipping_label/label_data.dart';
import 'package:swift_shipping_label/logo_image_process.dart';
import 'package:swift_shipping_label/logo_import_options.dart';
import 'package:swift_shipping_label/logo_restorer.dart';
import 'package:swift_shipping_label/pdf/bol_label_pdf.dart';
import 'package:swift_shipping_label/pdf/shipping_label_pdf.dart';
import 'package:swift_shipping_label/pdf_render_options.dart';

/// App-level improve-loop harness.
///
/// Writes `qa_app/synthetic/harness_results.json` + artifacts for scoring.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('app improve-loop matrix', () async {
    final root = Directory.current.parent;
    final synDir = Directory(p.join(root.path, 'qa_app', 'synthetic'));
    final artDir = Directory(p.join(synDir.path, 'artifacts'));
    await artDir.create(recursive: true);

    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
      const MethodChannel('plugins.flutter.io/path_provider'),
      (call) async {
        if (call.method == 'getTemporaryDirectory' ||
            call.method == 'getApplicationDocumentsDirectory' ||
            call.method == 'getApplicationSupportDirectory') {
          return Directory.systemTemp.path;
        }
        return null;
      },
    );

    final cases = <Map<String, dynamic>>[];
    final notes = <String>[];

    Future<Uint8List?> loadCustomerLogo() async {
      final logosDir = Directory(p.join(root.path, 'customer_logos'));
      final candidates = [
        'Arc Resources LTD.png',
        'Trialta Projects.png',
        'ARJAE.png',
      ];
      for (final name in candidates) {
        final f = File(p.join(logosDir.path, name));
        if (await f.exists()) {
          return LogoImageProcessor.processWithOptions(
            await f.readAsBytes(),
            LogoImportOptions.standard(removeBackground: true),
          );
        }
      }
      try {
        final data =
            await rootBundle.load('assets/images/sample_customer_logo.png');
        return data.buffer.asUint8List();
      } catch (_) {
        return null;
      }
    }

    final logoBytes = await loadCustomerLogo();
    final logos = logoBytes == null ? <Uint8List>[] : [logoBytes];

    // --- cold_start_storage ---
    {
      final caseId = 'cold_start_storage';
      final t0 = DateTime.now();
      final tmp = await Directory.systemTemp.createTemp('app_improve_storage_');
      try {
        final store = AppStorage.forTesting(tmp);
        await store.ensureDirs();
        await store.loadPresets();
        await store.loadSignatures();
        await store.loadRememberedContacts();
        final ms = DateTime.now().difference(t0).inMilliseconds;
        final ok = await store.logosDir.exists() &&
            await store.filledDir.exists() &&
            await store.signaturesDir.exists();
        cases.add({
          'case_id': caseId,
          'ok': ok,
          'duration_ms': ms,
          'metrics_raw': {
            'dirs_ok': ok,
            'preset_count': store.presets.length,
          },
        });
      } finally {
        try {
          await tmp.delete(recursive: true);
        } catch (_) {}
      }
    }

    // Shared PDF builders
    final shippingPdf = await ShippingLabelPdf.load();
    final bolPdf = BolLabelPdf(shippingPdf);
    const opts = PdfRenderOptions.defaults;

    Future<Map<String, dynamic>> genCase({
      required String caseId,
      required Future<Uint8List> Function() build,
      required String outName,
      int minBytes = 8000,
    }) async {
      final t0 = DateTime.now();
      var ok = false;
      var bytesLen = 0;
      String? err;
      try {
        final bytes = await build();
        bytesLen = bytes.length;
        ok = bytesLen >= minBytes;
        final out = File(p.join(artDir.path, outName));
        await out.writeAsBytes(bytes);
      } catch (e) {
        err = '$e';
        ok = false;
      }
      final ms = DateTime.now().difference(t0).inMilliseconds;
      return {
        'case_id': caseId,
        'ok': ok,
        'duration_ms': ms,
        'bytes': bytesLen,
        'min_bytes': minBytes,
        'error': ?err,
      };
    }

    cases.add(
      await genCase(
        caseId: 'generate_shipping',
        outName: 'shipping_sample.pdf',
        minBytes: 10000,
        build: () => shippingPdf.build(
          data: ShippingLabelData.sample,
          customerLogoBytes: logos,
          piecePlan: const PieceCountPlan(palletCrates: 2, boxes: 1),
          options: opts,
        ),
      ),
    );

    cases.add(
      await genCase(
        caseId: 'generate_receiving',
        outName: 'receiving_sample.pdf',
        minBytes: 8000,
        build: () => shippingPdf.buildReceiving(
          data: ShippingLabelData.receivingSample,
          customerLogoBytes: logos,
          options: opts,
        ),
      ),
    );

    cases.add(
      await genCase(
        caseId: 'generate_bol',
        outName: 'bol_sample.pdf',
        minBytes: 20000,
        build: () => bolPdf.build(
          data: ShippingLabelData.bolSample,
          customerLogoBytes: logos,
          options: opts,
        ),
      ),
    );

    // --- logo_restore_cubic (skipGenerative) ---
    {
      const caseId = 'logo_restore_cubic';
      final tmp = await Directory.systemTemp.createTemp('app_improve_restore_');
      final logosTmp = Directory(p.join(tmp.path, 'customer_logos'));
      await logosTmp.create(recursive: true);
      try {
        // Tiny JPEG-crushed mark so restore has work; cubic path only.
        Uint8List srcBytes;
        if (logoBytes != null && logoBytes.isNotEmpty) {
          final decoded = img.decodeImage(logoBytes);
          if (decoded != null) {
            final tiny = img.copyResize(
              decoded,
              width: 64,
              height: (64 * decoded.height / decoded.width).round().clamp(16, 64),
              interpolation: img.Interpolation.average,
            );
            srcBytes = Uint8List.fromList(img.encodeJpg(tiny, quality: 40));
          } else {
            srcBytes = logoBytes;
          }
        } else {
          final blank = img.Image(width: 48, height: 32);
          img.fill(blank, color: img.ColorRgba8(220, 80, 40, 255));
          srcBytes = Uint8List.fromList(img.encodePng(blank));
        }
        final src = File(p.join(logosTmp.path, 'degraded_smoke.png'));
        await src.writeAsBytes(srcBytes);

        final t0 = DateTime.now();
        String? err;
        var ok = false;
        var outBytes = 0;
        var engine = 'cubic_skip_generative';
        try {
          final out = await LogoRestorer.ensureHighRes(
            src,
            logosDir: logosTmp,
            skipGenerative: true,
            onLog: (m) {
              if (m.contains('vectorize')) engine = 'vectorize';
              if (m.contains('Real-ESRGAN') || m.contains('esrgan')) {
                engine = 'esrgan';
              }
              if (m.contains('conservator') || m.contains('no SR')) {
                engine = 'cubic_conservator';
              }
              if (m.contains('cache hit')) engine = 'cache';
            },
          );
          outBytes = await out.length();
          ok = outBytes > 0 && await out.exists();
        } catch (e) {
          err = '$e';
        }
        final ms = DateTime.now().difference(t0).inMilliseconds;
        cases.add({
          'case_id': caseId,
          'ok': ok,
          'duration_ms': ms,
          'bytes': outBytes,
          'engine': engine,
          'skip_generative': true,
          'error': ?err,
        });
      } finally {
        try {
          await tmp.delete(recursive: true);
        } catch (_) {}
      }
    }

    // --- address_book_local ---
    {
      const caseId = 'address_book_local';
      final t0 = DateTime.now();
      final raw = <DeliveryAddressEntry>[];
      for (var i = 0; i < 120; i++) {
        raw.add(
          DeliveryAddressEntry(
            addressKey: 'k$i',
            shipToName: i.isEven ? 'Arc Resources $i' : 'Propak Site $i',
            address: '$i Industrial Rd, Nisku AB T9E 1E7',
            carrier: i % 3 == 0 ? 'Murrays' : 'Dunrite',
            accountNumbers: 'ACC-$i',
            lastUsedAt: DateTime.utc(2026, 1, 1).add(Duration(minutes: i)),
          ),
        );
      }
      // Duplicate pair for collapse
      raw.add(
        DeliveryAddressEntry(
          addressKey: 'dup-a',
          shipToName: 'GCM Valve',
          address: '3360 10 Street',
          carrier: 'Murrays',
          accountNumbers: '',
          lastUsedAt: DateTime.utc(2026, 1, 1),
        ),
      );
      raw.add(
        DeliveryAddressEntry(
          addressKey: 'dup-b',
          shipToName: 'GCM Valve',
          address: '3360 10th St, Nisku AB T9E 1E7',
          carrier: 'murrays',
          accountNumbers: '12345',
          lastUsedAt: DateTime.utc(2026, 1, 2),
        ),
      );

      final collapsed = AddressBookSync.collapseDuplicates(raw);
      final sorted = AddressBookSync.sortByShipToName(collapsed);
      final q = 'propak';
      final filtered = sorted
          .where(
            (e) =>
                e.shipToName.toLowerCase().contains(q) ||
                e.address.toLowerCase().contains(q),
          )
          .toList();
      final ms = DateTime.now().difference(t0).inMilliseconds;
      final ok = sorted.isNotEmpty &&
          filtered.isNotEmpty &&
          collapsed.length < raw.length;
      cases.add({
        'case_id': caseId,
        'ok': ok,
        'duration_ms': ms,
        'metrics_raw': {
          'raw_count': raw.length,
          'collapsed_count': collapsed.length,
          'sorted_count': sorted.length,
          'filter_hits': filtered.length,
          'network_skipped': true,
        },
      });
      notes.add('address_book_network: skipped (local-only smoke)');
    }

    // --- history_open_no_prune (source + API contract) ---
    {
      const caseId = 'history_open_no_prune';
      final home = File(p.join(root.path, 'mobile', 'lib', 'home_screen.dart'));
      final hist = File(
        p.join(root.path, 'mobile', 'lib', 'document_history_sync.dart'),
      );
      final homeSrc = await home.readAsString();
      final histSrc = await hist.readAsString();

      // History dialog load must use listForKind, never pruneWithoutSnapshots.
      final openBlock = _extractMethod(homeSrc, '_openHistory');
      final openCallsPrune = openBlock.contains('pruneWithoutSnapshots');
      // _load appears in several State classes — check History dialog region.
      final histDialogIdx = homeSrc.indexOf('class _HistoryDialogState');
      var histLoadCallsPrune = false;
      if (histDialogIdx >= 0) {
        final region = homeSrc.substring(histDialogIdx);
        final loadIdx = region.indexOf('Future<void> _load()');
        if (loadIdx >= 0) {
          final slice = region.substring(loadIdx, loadIdx + 800);
          histLoadCallsPrune = slice.contains('pruneWithoutSnapshots');
        }
      }
      final apiCommentOk = histSrc.contains('do **not** call from the History UI') ||
          histSrc.contains('do **not** call from the History UI'.replaceAll('**', '')) ||
          histSrc.contains('Opt-in CLI only');
      final listUsesRetention = histSrc.contains('createdAtGte: _retentionCutoff()');
      final ok = !openCallsPrune &&
          !histLoadCallsPrune &&
          apiCommentOk &&
          listUsesRetention &&
          DocumentHistorySync.historyKinds.contains(LabelKind.shipping);

      cases.add({
        'case_id': caseId,
        'ok': ok,
        'duration_ms': 0,
        'gates_raw': {
          'open_history_no_prune': !openCallsPrune,
          'history_dialog_load_no_prune': !histLoadCallsPrune,
          'prune_api_opt_in_documented': apiCommentOk,
          'list_uses_retention_cutoff': listUsesRetention,
        },
      });
    }

    // --- history_snapshot_heuristics ---
    {
      const caseId = 'history_snapshot_heuristics';
      final t0 = DateTime.now();
      final snap = HistoryFormSnapshot.fromJson({
        'fields': {'customer': 'Arc', 'sales_order': 'SO-1'},
        'logo_count': 1,
      });
      final empty = HistoryFormSnapshot.fromJson({'fields': {}});
      final idOk = DocumentHistorySync.isHistoryLocalFileForId(
            'abc123.form.json',
            'abc123',
          ) &&
          DocumentHistorySync.isHistoryLocalFileForId(
            'GCM_12345_abc123.pdf',
            'abc123',
          ) &&
          !DocumentHistorySync.isHistoryLocalFileForId(
            'GCM_12345.pdf',
            'abc123',
          );
      final missingOk = DocumentHistorySync.isMissingStorageResponse(
            400,
            '{"statusCode":"404","error":"not_found","code":"NoSuchKey"}',
          ) &&
          !DocumentHistorySync.isMissingStorageResponse(401, '{}');
      final ok = snap.hasFields && !empty.hasFields && idOk && missingOk;
      cases.add({
        'case_id': caseId,
        'ok': ok,
        'duration_ms': DateTime.now().difference(t0).inMilliseconds,
        'metrics_raw': {
          'snapshot_has_fields': snap.hasFields,
          'empty_snapshot_ok': !empty.hasFields,
          'local_file_id_match': idOk,
          'missing_storage_heuristic': missingOk,
          'retention_days': DocumentHistorySync.retention.inDays,
        },
      });
    }

    // --- exclusive_dialogs ---
    {
      const caseId = 'exclusive_dialogs';
      final home = File(p.join(root.path, 'mobile', 'lib', 'home_screen.dart'));
      final src = await home.readAsString();
      final hasHelper = src.contains('_runExclusiveDialog') &&
          src.contains('_isDialogLocked');
      final historyLocked =
          src.contains("_isDialogLocked('history')") &&
              src.contains("_runExclusiveDialog<void>('history'");
      final addressLocked =
          src.contains("_isDialogLocked('addressBook')") &&
              (src.contains("_runExclusiveDialog") &&
                  src.contains("'addressBook'"));
      final ok = hasHelper && historyLocked && addressLocked;
      cases.add({
        'case_id': caseId,
        'ok': ok,
        'duration_ms': 0,
        'gates_raw': {
          'exclusive_helper_present': hasHelper,
          'history_exclusive': historyLocked,
          'address_book_exclusive': addressLocked,
        },
      });
    }

    // --- rapid_generate_loop ---
    {
      const caseId = 'rapid_generate_loop';
      const n = 5;
      final t0 = DateTime.now();
      var successes = 0;
      final sizes = <int>[];
      String? err;
      try {
        for (var i = 0; i < n; i++) {
          final bytes = await shippingPdf.build(
            data: ShippingLabelData.sample,
            customerLogoBytes: logos,
            piecePlan: const PieceCountPlan(palletCrates: 1, boxes: 0),
            options: opts,
          );
          sizes.add(bytes.length);
          if (bytes.length >= 10000) {
            successes++;
            await File(p.join(artDir.path, 'rapid_$i.pdf')).writeAsBytes(bytes);
          }
        }
      } catch (e) {
        err = '$e';
      }
      final ms = DateTime.now().difference(t0).inMilliseconds;
      final ok = successes == n && err == null;
      cases.add({
        'case_id': caseId,
        'ok': ok,
        'duration_ms': ms,
        'metrics_raw': {
          'n': n,
          'successes': successes,
          'sizes': sizes,
        },
        'error': ?err,
      });
    }

    final manifest = {
      'generated_at': DateTime.now().toUtc().toIso8601String(),
      'n_cases': cases.length,
      'case_ids': cases.map((c) => c['case_id']).toList(),
      'notes': notes,
    };
    await File(p.join(synDir.path, 'manifest.json')).writeAsString(
      const JsonEncoder.withIndent('  ').convert(manifest),
    );

    final results = {
      'generated_at': DateTime.now().toUtc().toIso8601String(),
      'ok': cases.every((c) => c['ok'] == true),
      'cases': cases,
      'notes': notes,
    };
    await File(p.join(synDir.path, 'harness_results.json')).writeAsString(
      const JsonEncoder.withIndent('  ').convert(results),
    );

    // Soft expects — scoring owns pass/fail budgets; harness must not crash.
    expect(cases, isNotEmpty);
    expect(File(p.join(synDir.path, 'harness_results.json')).existsSync(), isTrue);
  }, timeout: const Timeout(Duration(minutes: 8)));
}

String _extractMethod(String src, String name) {
  final idx = src.indexOf(name);
  if (idx < 0) return '';
  return src.substring(idx, (idx + 1200).clamp(0, src.length));
}
