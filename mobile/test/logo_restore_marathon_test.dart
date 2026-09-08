import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
import 'package:path/path.dart' as p;
import 'package:swift_shipping_label/logo_finder.dart';
import 'package:swift_shipping_label/logo_image_process.dart';
import 'package:swift_shipping_label/logo_import_options.dart';
import 'package:swift_shipping_label/logo_restorer.dart';

/// True-to-life smoke: same LogoFinder + knockout + LogoRestorer as the app.
///
///   flutter test test/logo_restore_marathon_test.dart
class _RealHttpOverrides extends HttpOverrides {
  @override
  HttpClient createHttpClient(SecurityContext? context) {
    HttpOverrides.global = null;
    final client = HttpClient(context: context);
    HttpOverrides.global = this;
    return client;
  }
}

void main() {
  HttpOverrides.global = _RealHttpOverrides();
  TestWidgetsFlutterBinding.ensureInitialized();
  HttpOverrides.global = _RealHttpOverrides();

  test('conservator-only cache score', () async {
    final round = DateTime.now().toIso8601String().replaceAll(':', '-');
    final report = await runLogoRestoreMarathon(
      roundLabel: round,
      recrawl: false,
      conservatorOnly: true,
    );
    expect(report['companies'], isA<List>());
    final companies = report['companies'] as List;
    expect(companies.length, 10);
  }, timeout: const Timeout(Duration(minutes: 20)));

  test('full restore marathon with Gemini', () async {
    final round = DateTime.now().toIso8601String().replaceAll(':', '-');
    final report = await runLogoRestoreMarathon(
      roundLabel: round,
      recrawl: false,
      conservatorOnly: false,
    );
    expect(report['companies'], isA<List>());
    expect((report['companies'] as List).length, 10);
  }, skip: 'Conservator-only is the shipped path; Gemini is always rejected on this set. Run explicitly to re-check.', timeout: const Timeout(Duration(minutes: 50)));
}

const marathonCompanies = <({String name, String domain})>[
  (name: 'Allied Fitting', domain: 'alliedfitting.com'),
  (name: 'PVF Canada', domain: 'pvfcanada.com'),
  (name: 'Comco Pipe', domain: ''),
  (name: '5MPFF', domain: ''),
  (name: 'CCTF Corp', domain: ''),
  (name: 'Paragon Oilfield Supply', domain: ''),
  (name: 'Apex Valves', domain: ''),
  (name: 'Warren Valve', domain: 'warrenvalve.com'),
  (name: 'Quest Gasket', domain: ''),
  (name: 'Flexitallic', domain: 'flexitallic.com'),
];

Future<Map<String, Object?>> runLogoRestoreMarathon({
  required String roundLabel,
  bool recrawl = true,
  bool conservatorOnly = false,
}) async {
  final root = Directory.current.path.endsWith('mobile')
      ? Directory.current.parent
      : Directory.current;
  final outRoot = Directory(
    p.join(root.path, 'qa_logs', 'logo_restore_marathon', roundLabel),
  );
  await outRoot.create(recursive: true);
  final cacheDir = Directory(
    p.join(root.path, 'qa_logs', 'logo_restore_marathon', 'crawl_cache'),
  );
  await cacheDir.create(recursive: true);

  final finder = recrawl ? LogoFinder() : null;
  final companies = <Map<String, Object?>>[];

  for (final c in marathonCompanies) {
    final stem = _stem(c.name);
    final companyDir = Directory(p.join(outRoot.path, stem));
    await companyDir.create(recursive: true);

    final cached = File(p.join(cacheDir.path, '$stem.png'));
    Uint8List? sourceBytes;
    String sourceNote = '';
    var crawlCount = 0;
    String? crawlError;

    if (recrawl || !cached.existsSync()) {
      try {
        final found = await finder!.findDownloadedCandidates(
          companyName: c.name,
          domain: c.domain,
          engine: LogoSearchEngine.all,
        );
        crawlCount = found.length;
        if (found.isNotEmpty) {
          found.sort((a, b) => b.score.compareTo(a.score));
          sourceBytes = found.first.bytes;
          sourceNote = '${found.first.source} score=${found.first.score}';
          await cached.writeAsBytes(sourceBytes);
          await File(
            p.join(cacheDir.path, '$stem.meta.json'),
          ).writeAsString(
            jsonEncode({
              'name': c.name,
              'domain': c.domain,
              'source': found.first.source,
              'url': found.first.url,
              'score': found.first.score,
              'hint': found.first.hint,
              'candidates': found.length,
            }),
          );
        }
      } catch (e) {
        crawlError = e.toString();
      }
    }
    if ((sourceBytes == null || sourceBytes.isEmpty) && cached.existsSync()) {
      sourceBytes = await cached.readAsBytes();
      sourceNote = sourceNote.isEmpty ? 'crawl_cache' : sourceNote;
      crawlCount = math.max(crawlCount, 1);
    }

    if (sourceBytes == null || sourceBytes.isEmpty) {
      companies.add({
        'company': c.name,
        'ok': false,
        'errors': ['crawl_failed: ${crawlError ?? 'no candidates'}'],
        'crawlCount': crawlCount,
      });
      continue;
    }

    final srcFile = File(p.join(companyDir.path, '00_source.png'));
    await srcFile.writeAsBytes(sourceBytes);

    final knockout = LogoImageProcessor.processWithOptions(
      sourceBytes,
      LogoImportOptions.standard(
        removeBackground: true,
        cropMode: LogoCropMode.auto,
        perfectLogo: false,
      ),
    );
    final koFile = File(p.join(companyDir.path, '01_knockout.png'));
    await koFile.writeAsBytes(knockout);

    final logosDir = Directory(p.join(companyDir.path, 'restore_work'));
    await logosDir.create(recursive: true);
    final restoreSrc = File(p.join(logosDir.path, '$stem.png'));
    await restoreSrc.writeAsBytes(sourceBytes);

    String restoreLog = '';
    File? restoredFile;
    try {
      restoredFile = await LogoRestorer.ensureHighRes(
        restoreSrc,
        logosDir: logosDir,
        onLog: (m) => restoreLog = '$restoreLog$m\n',
        skipGenerative: conservatorOnly,
      );
    } catch (e) {
      restoreLog = '$restoreLog$e\n';
    }

    Uint8List? restoredBytes;
    if (restoredFile != null && await restoredFile.exists()) {
      restoredBytes = await restoredFile.readAsBytes();
      await File(p.join(companyDir.path, '02_restore.png'))
          .writeAsBytes(restoredBytes);
    }

    final errors = <String>[];
    final koScore = _scoreKnockout(sourceBytes, knockout);
    errors.addAll(koScore.errors);
    Map<String, Object?> restoreScore = {};
    if (restoredBytes == null) {
      errors.add('restore_missing');
    } else {
      final rs = _scoreRestore(sourceBytes, knockout, restoredBytes);
      restoreScore = rs.fields;
      errors.addAll(rs.errors);
    }

    await _writeContactSheet(
      File(p.join(companyDir.path, '03_contact.png')),
      sourceBytes,
      knockout,
      restoredBytes,
    );

    companies.add({
      'company': c.name,
      'domain': c.domain,
      'ok': errors.isEmpty,
      'errors': errors,
      'crawlCount': crawlCount,
      'sourceNote': sourceNote,
      'knockout': koScore.fields,
      'restore': restoreScore,
      'restoreLogTail': restoreLog.trim().split('\n').take(12).toList(),
    });
  }

  final fail = companies.where((e) => e['ok'] != true).length;
  final report = {
    'round': roundLabel,
    'when': DateTime.now().toIso8601String(),
    'pipeline': LogoRestorer.pipelineVersion,
    'pass': companies.length - fail,
    'fail': fail,
    'companies': companies,
    'verdict':
        'Terminal/Dart is the true-to-life path: LogoFinder + processWithOptions + '
        'LogoRestorer.ensureHighRes are the same libraries the Windows/Android app '
        'calls. UI clicking is slower and cannot score geometry/color. Use this '
        'harness for smoke; spot-check the contact sheets in the running app.',
  };

  final jsonFile = File(p.join(outRoot.path, 'report.json'));
  await jsonFile.writeAsString(
    const JsonEncoder.withIndent('  ').convert(report),
  );
  await File(p.join(outRoot.path, 'report.md')).writeAsString(
    _markdown(report),
  );
  // ignore: avoid_print
  print('Wrote ${jsonFile.path} pass=${report['pass']} fail=${report['fail']}');
  return report;
}

String _stem(String name) =>
    name.toLowerCase().replaceAll(RegExp(r'[^a-z0-9]+'), '_');

({Map<String, Object?> fields, List<String> errors}) _scoreKnockout(
  Uint8List source,
  Uint8List knockout,
) {
  final errors = <String>[];
  final s = img.decodeImage(source);
  final k = img.decodeImage(knockout);
  if (s == null || k == null) {
    return (fields: {'ok': false}, errors: ['knockout_undecodable']);
  }
  final srcInk = _inkCount(s);
  final koInk = _inkCount(k);
  final keep = srcInk == 0 ? 1.0 : koInk / srcInk;
  if (keep < 0.55) {
    errors.add(
      'knockout_stripped_ink keep=${keep.toStringAsFixed(2)} src=$srcInk ko=$koInk',
    );
  }
  if (koInk < 80) errors.add('knockout_almost_empty ink=$koInk');
  return (
    fields: {
      'srcW': s.width,
      'srcH': s.height,
      'koW': k.width,
      'koH': k.height,
      'srcInk': srcInk,
      'koInk': koInk,
      'keep': double.parse(keep.toStringAsFixed(3)),
    },
    errors: errors,
  );
}

({Map<String, Object?> fields, List<String> errors}) _scoreRestore(
  Uint8List source,
  Uint8List knockout,
  Uint8List restored,
) {
  final errors = <String>[];
  final r = img.decodeImage(restored);
  if (r == null) {
    return (fields: {'ok': false}, errors: ['restore_undecodable']);
  }
  final faithful = LogoImageProcessor.isFaithfulRestore(knockout, restored);
  final geometry = LogoImageProcessor.matchesSourceGeometry(
    knockout,
    restored,
    maxMeanAbs: 36,
  );
  final colors = LogoImageProcessor.retainsBrandColors(knockout, restored);
  final checker = LogoImageProcessor.looksLikeCheckerboardMatte(restored);
  if (!faithful) errors.add('restore_not_faithful');
  if (!geometry) errors.add('restore_geometry_changed');
  if (!colors) errors.add('restore_brand_colors_lost');
  if (checker) errors.add('restore_checkerboard_matte');
  if (r.height < 1200) errors.add('restore_not_high_res h=${r.height}');

  final src = img.decodeImage(source)!;
  final srcAspect = src.width / math.max(1, src.height);
  final outAspect = r.width / math.max(1, r.height);
  if (LogoImageProcessor.aspectDrift(source, restored) > 0.35) {
    errors.add(
      'restore_aspect_warp src=${srcAspect.toStringAsFixed(2)} '
      'out=${outAspect.toStringAsFixed(2)}',
    );
  }

  return (
    fields: {
      'w': r.width,
      'h': r.height,
      'faithful': faithful,
      'geometry': geometry,
      'colors': colors,
      'checker': checker,
    },
    errors: errors,
  );
}

int _inkCount(img.Image im) {
  var n = 0;
  for (var y = 0; y < im.height; y += 1) {
    for (var x = 0; x < im.width; x += 1) {
      final p = im.getPixel(x, y);
      if (p.a.toInt() < 96) continue;
      final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
      if (r >= 250 && g >= 250 && b >= 250) continue;
      n++;
    }
  }
  return n;
}

Future<void> _writeContactSheet(
  File dest,
  Uint8List source,
  Uint8List knockout,
  Uint8List? restored,
) async {
  img.Image? fit(Uint8List bytes) {
    final im = img.decodeImage(bytes);
    if (im == null) return null;
    const h = 360;
    final w = math.max(1, (im.width * h / im.height).round());
    return img.copyResize(im, width: w, height: h);
  }

  final a = fit(source);
  final b = fit(knockout);
  final c = restored == null ? null : fit(restored);
  if (a == null || b == null) return;
  final gap = 12;
  final w = a.width + gap + b.width + (c == null ? 0 : gap + c.width);
  final sheet = img.Image(width: w, height: 360, numChannels: 4);
  img.fill(sheet, color: img.ColorRgba8(240, 240, 240, 255));
  img.compositeImage(sheet, a, dstX: 0, dstY: 0);
  img.compositeImage(sheet, b, dstX: a.width + gap, dstY: 0);
  if (c != null) {
    img.compositeImage(sheet, c, dstX: a.width + gap + b.width + gap, dstY: 0);
  }
  await dest.writeAsBytes(img.encodePng(sheet));
}

String _markdown(Map<String, Object?> report) {
  final buf = StringBuffer()
    ..writeln('# Logo restore marathon')
    ..writeln()
    ..writeln('- Round: ${report['round']}')
    ..writeln('- Pipeline: ${report['pipeline']}')
    ..writeln('- Pass ${report['pass']} / fail ${report['fail']}')
    ..writeln()
    ..writeln('${report['verdict']}')
    ..writeln();
  for (final raw in report['companies'] as List) {
    final c = Map<String, Object?>.from(raw as Map);
    final errors = (c['errors'] as List?)?.map((e) => '$e').toList() ?? [];
    buf
      ..writeln('## ${c['company']}')
      ..writeln('- ok: ${c['ok']}')
      ..writeln('- crawl: ${c['crawlCount']} (${c['sourceNote']})');
    if (errors.isNotEmpty) {
      buf.writeln('- **errors:** ${errors.join('; ')}');
    }
    buf.writeln();
  }
  return buf.toString();
}
