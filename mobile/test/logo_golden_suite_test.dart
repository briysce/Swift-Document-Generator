import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
import 'package:swift_shipping_label/logo_image_process.dart';
import 'package:swift_shipping_label/logo_import_options.dart';
import 'package:swift_shipping_label/logo_ink_fit.dart';

/// Non-regression floors for qa_logos/golden prepare / knockout path.
///
/// Skips gracefully when the golden folder is absent (e.g. shallow CI checkout).
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  final root = _repoRoot();
  final casesDir = Directory('${root.path}/qa_logos/golden/cases');

  test('golden prepare path: self-consistency + quality floors', () {
    if (!casesDir.existsSync()) {
      // ignore: avoid_print
      print('skip: golden cases missing at ${casesDir.path}');
      return;
    }

    final dirs = casesDir
        .listSync()
        .whereType<Directory>()
        .where((d) => File('${d.path}/original.png').existsSync())
        .toList()
      ..sort((a, b) => a.path.compareTo(b.path));

    expect(dirs, isNotEmpty, reason: 'expected seeded golden cases');

    var scored = 0;
    for (final dir in dirs) {
      final slug = dir.uri.pathSegments
          .where((s) => s.isNotEmpty)
          .last;
      final bytes = File('${dir.path}/original.png').readAsBytesSync();
      expect(bytes, isNotEmpty, reason: slug);

      final prepared = LogoImageProcessor.prepareRasterForRestore(bytes);
      final processed = LogoImageProcessor.processWithOptions(
        bytes,
        LogoImportOptions.standard(
          removeBackground: true,
          cropMode: LogoCropMode.auto,
        ),
      );
      final work = prepared.isNotEmpty ? prepared : processed;
      expect(work, isNotEmpty, reason: slug);

      // Idempotent prepare — second pass should not chew the mark.
      final again = LogoImageProcessor.prepareRasterForRestore(work);
      final selfIou = _inkIouCropped(work, again);
      expect(selfIou, greaterThan(0.90), reason: '$slug self_iou=$selfIou');

      final drift = LogoImageProcessor.aspectDrift(bytes, work);
      expect(
        drift,
        lessThan(0.45),
        reason: '$slug aspect_drift=$drift',
      );

      final clean = _alphaClean(work);
      expect(clean, greaterThan(0.70), reason: '$slug alpha_clean=$clean');

      final ink = LogoInkFit.prepare(work);
      expect(ink.ink.isValid, isTrue, reason: slug);
      expect(ink.ink.width, greaterThan(2), reason: slug);
      expect(ink.ink.height, greaterThan(2), reason: slug);

      // Pinnacle anchors: both Swift orange variants (document + solid chrome)
      // — multi-iteration north star for flat brand recomposition.
      if (slug == 'swift_orange' || slug == 'swift_orange_solid') {
        final iou = _inkIouCropped(bytes, work);
        expect(drift, lessThan(0.12), reason: '$slug aspect');
        expect(iou, greaterThan(0.75), reason: '$slug iou=$iou');
        expect(clean, greaterThan(0.95), reason: '$slug alpha');
        expect(selfIou, greaterThan(0.97), reason: '$slug self');
        expect(
          LogoImageProcessor.hasMeaningfulTransparency(work),
          isTrue,
          reason: '$slug keeps true alpha',
        );
      }
      scored++;
    }
    expect(scored, greaterThanOrEqualTo(12));
  });
}

Directory _repoRoot() {
  var dir = Directory.current;
  for (var i = 0; i < 6; i++) {
    if (Directory('${dir.path}/qa_logos/golden/cases').existsSync() ||
        Directory('${dir.path}/customer_logos').existsSync()) {
      return dir;
    }
    final parent = dir.parent;
    if (parent.path == dir.path) break;
    dir = parent;
  }
  // flutter test cwd is usually mobile/
  return Directory.current.parent;
}

double _inkIou(Uint8List aBytes, Uint8List bBytes, {int core = 96}) {
  final a = img.decodeImage(aBytes);
  final b = img.decodeImage(bBytes);
  if (a == null || b == null) return 0;
  final scaled = img.copyResize(
    b,
    width: a.width,
    height: a.height,
    interpolation: img.Interpolation.average,
  );
  var inter = 0;
  var union = 0;
  for (var y = 0; y < a.height; y++) {
    for (var x = 0; x < a.width; x++) {
      final ao = a.getPixel(x, y).a.toInt() >= core;
      final bo = scaled.getPixel(x, y).a.toInt() >= core;
      if (ao && bo) inter++;
      if (ao || bo) union++;
    }
  }
  if (union == 0) return 0;
  return inter / union;
}

/// Crop each raster to opaque ink AABB, then IoU at shared size.
double _inkIouCropped(Uint8List aBytes, Uint8List bBytes, {int core = 96}) {
  img.Image? cropInk(img.Image src) {
    var minX = src.width, minY = src.height, maxX = -1, maxY = -1;
    for (var y = 0; y < src.height; y++) {
      for (var x = 0; x < src.width; x++) {
        if (src.getPixel(x, y).a.toInt() < core) continue;
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
    }
    if (maxX < minX) return null;
    return img.copyCrop(
      src,
      x: minX,
      y: minY,
      width: maxX - minX + 1,
      height: maxY - minY + 1,
    );
  }

  final a0 = img.decodeImage(aBytes);
  final b0 = img.decodeImage(bBytes);
  if (a0 == null || b0 == null) return 0;
  final a = cropInk(a0) ?? a0;
  final b = cropInk(b0) ?? b0;
  return _inkIou(
    Uint8List.fromList(img.encodePng(a)),
    Uint8List.fromList(img.encodePng(b)),
    core: core,
  );
}

double _alphaClean(Uint8List png) {
  final image = img.decodeImage(png);
  if (image == null) return 0;
  final w = image.width;
  final h = image.height;
  final band = math.max(2, math.min(w, h) ~/ 20);
  var edge = 0;
  var leftover = 0;
  for (var y = 0; y < h; y++) {
    for (var x = 0; x < w; x++) {
      final onEdge =
          x < band || y < band || x >= w - band || y >= h - band;
      if (!onEdge) continue;
      edge++;
      final p = image.getPixel(x, y);
      final a = p.a.toInt();
      if (a < 200) continue;
      final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
      final lum = (r + g + b) / 3.0;
      final sat = math.max(r, math.max(g, b)) - math.min(r, math.min(g, b));
      final plate = (lum > 245 && sat < 12) || (lum < 18 && sat < 12);
      if (plate) leftover++;
    }
  }
  if (edge == 0) return 1;
  return 1.0 - leftover / edge;
}
