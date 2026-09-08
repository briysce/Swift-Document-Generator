import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
import 'package:path/path.dart' as p;
import 'package:swift_shipping_label/logo_image_process.dart';
import 'package:swift_shipping_label/logo_import_options.dart';
import 'package:swift_shipping_label/logo_restorer.dart';

void main() {
  test('finalizeRestoredPng scales height to 3000', () {
    final src = img.Image(width: 80, height: 80, numChannels: 4);
    img.fill(src, color: img.ColorRgba8(20, 120, 40, 255));
    final out = LogoRestorer.finalizeRestoredPng(
      Uint8List.fromList(img.encodePng(src)),
    );
    final decoded = img.decodeImage(out)!;
    expect(decoded.height, LogoRestorer.minDimension);
    expect(decoded.width, greaterThan(1000));
  });

  test('flattenSolidBrandFills collapses mottled interiors of any hues to solids',
      () {
    // Teal + magenta — not orange/green — so the pass is color-agnostic.
    const teal = (18, 168, 164);
    const magenta = (188, 28, 156);
    final src = img.Image(width: 120, height: 80, numChannels: 4);
    img.fill(src, color: img.ColorRgba8(0, 0, 0, 0));
    for (var y = 8; y < 36; y++) {
      for (var x = 8; x < 112; x++) {
        final n = ((x * 13 + y * 7) % 11) - 5;
        src.setPixelRgba(x, y, teal.$1 + n, teal.$2 + n, teal.$3 + n ~/ 2, 255);
      }
    }
    for (var y = 44; y < 72; y++) {
      for (var x = 8; x < 112; x++) {
        final n = ((x * 5 + y * 11) % 9) - 4;
        src.setPixelRgba(
          x,
          y,
          magenta.$1 + n,
          magenta.$2 + n,
          magenta.$3 + n ~/ 2,
          255,
        );
      }
    }

    final out = LogoImageProcessor.flattenSolidBrandFills(src);
    final a = out.getPixel(60, 20);
    final a2 = out.getPixel(40, 22);
    expect(a.r.toInt(), a2.r.toInt());
    expect(a.g.toInt(), a2.g.toInt());
    expect(a.b.toInt(), a2.b.toInt());

    final b = out.getPixel(60, 56);
    final b2 = out.getPixel(40, 58);
    expect(b.r.toInt(), b2.r.toInt());
    expect(b.g.toInt(), b2.g.toInt());
    expect(b.b.toInt(), b2.b.toInt());
    expect((a.r.toInt() - b.r.toInt()).abs(), greaterThan(40));
    expect((a.g.toInt() - b.g.toInt()).abs(), greaterThan(40));
  });

  test('rebuildPredictedEdges paints solid fills with smooth masks', () {
    final src = img.Image(width: 40, height: 40, numChannels: 4);
    img.fill(src, color: img.ColorRgba8(255, 255, 255, 255));
    img.fillRect(
      src,
      x1: 8,
      y1: 8,
      x2: 31,
      y2: 31,
      color: img.ColorRgba8(200, 30, 30, 255),
    );
    src.setPixelRgba(8, 12, 80, 80, 80, 255);
    src.setPixelRgba(31, 20, 40, 40, 40, 255);
    final out = LogoImageProcessor.rebuildPredictedEdges(src, targetHeight: 80);
    expect(out.height, 80);
    final mid = out.getPixel(out.width ~/ 2, out.height ~/ 2);
    expect(mid.r.toInt(), greaterThan(120));
    expect(mid.a.toInt(), greaterThan(200));
  });

  test('ensureLetterOutline restores a dropped black text stroke', () {
    final source = img.Image(width: 40, height: 40, numChannels: 4);
    img.fill(source, color: img.ColorRgba8(255, 255, 255, 255));
    img.fillRect(
      source,
      x1: 8,
      y1: 8,
      x2: 31,
      y2: 31,
      color: img.ColorRgba8(0, 0, 0, 255),
    );
    img.fillRect(
      source,
      x1: 12,
      y1: 12,
      x2: 27,
      y2: 27,
      color: img.ColorRgba8(200, 40, 40, 255),
    );

    final restored = img.Image(width: 40, height: 40, numChannels: 4);
    img.fill(restored, color: img.ColorRgba8(255, 255, 255, 255));
    img.fillRect(
      restored,
      x1: 12,
      y1: 12,
      x2: 27,
      y2: 27,
      color: img.ColorRgba8(200, 40, 40, 255),
    );

    expect(LogoImageProcessor.detectLetterOutline(source), isNotNull);
    expect(LogoImageProcessor.detectLetterOutline(source)!.r, 0);
    expect(LogoImageProcessor.detectLetterOutline(source)!.g, 0);
    expect(LogoImageProcessor.detectLetterOutline(source)!.b, 0);
    final out = LogoImageProcessor.ensureLetterOutline(source, restored);
    final border = out.getPixel(10, 20);
    expect(border.r.toInt() + border.g.toInt() + border.b.toInt(), lessThan(80));
    expect(border.a.toInt(), greaterThan(200));
  });

  test('finalize falls back when restore drops the wordmark and keeps an accent',
      () {
    // Source: green wordmark + orange accent on black (bird-style).
    final source = img.Image(width: 200, height: 80, numChannels: 4);
    img.fill(source, color: img.ColorRgba8(0, 0, 0, 255));
    img.fillRect(
      source,
      x1: 10,
      y1: 25,
      x2: 160,
      y2: 70,
      color: img.ColorRgba8(0, 110, 55, 255),
    );
    img.fillRect(
      source,
      x1: 90,
      y1: 8,
      x2: 150,
      y2: 28,
      color: img.ColorRgba8(230, 100, 30, 255),
    );
    final sourceBytes = Uint8List.fromList(img.encodePng(source));

    // Bad restore: only the orange accent survives.
    final bad = img.Image(width: 80, height: 40, numChannels: 4);
    img.fill(bad, color: img.ColorRgba8(0, 0, 0, 0));
    img.fillRect(
      bad,
      x1: 5,
      y1: 5,
      x2: 74,
      y2: 34,
      color: img.ColorRgba8(230, 100, 30, 255),
    );
    final badBytes = Uint8List.fromList(img.encodePng(bad));

    expect(
      LogoImageProcessor.retainsBrandColors(sourceBytes, badBytes),
      isFalse,
    );

    final out = LogoRestorer.finalizeRestoredPng(
      badBytes,
      sourceBytes: sourceBytes,
    );
    final decoded = img.decodeImage(out)!;
    expect(decoded.height, LogoRestorer.minDimension);

    // Green wordmark must be present after fallback.
    var green = 0;
    var orange = 0;
    for (var y = 0; y < decoded.height; y += 8) {
      for (var x = 0; x < decoded.width; x += 8) {
        final p = decoded.getPixel(x, y);
        if (p.a.toInt() < 96) continue;
        final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
        if (g > r + 20 && g > b + 20) green++;
        if (r > 180 && g < 140 && b < 90) orange++;
      }
    }
    expect(green, greaterThan(20));
    expect(orange, greaterThan(0));
  });

  test('dark green wordmark on black plate is not stripped as background', () {
    final src = img.Image(width: 180, height: 70, numChannels: 4);
    img.fill(src, color: img.ColorRgba8(0, 0, 0, 255));
    img.fillRect(
      src,
      x1: 8,
      y1: 18,
      x2: 170,
      y2: 60,
      color: img.ColorRgba8(0, 111, 55, 255),
    );
    img.fillRect(
      src,
      x1: 100,
      y1: 6,
      x2: 150,
      y2: 22,
      color: img.ColorRgba8(230, 100, 30, 255),
    );
    final out = LogoImageProcessor.normalizeToVisibleContent(
      Uint8List.fromList(img.encodePng(src)),
    );
    final decoded = img.decodeImage(out)!;
    var green = 0;
    for (var y = 0; y < decoded.height; y++) {
      for (var x = 0; x < decoded.width; x++) {
        final p = decoded.getPixel(x, y);
        if (p.a.toInt() < 96) continue;
        final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
        if (g > r + 20 && g > b + 20) green++;
      }
    }
    expect(green, greaterThan(100));
    expect(decoded.width / decoded.height, greaterThan(2));
  });

  test('enclosed black letter counters become transparent', () {
    // Green "o": ring of brand ink, black hole, black outer plate.
    final src = img.Image(width: 80, height: 80, numChannels: 4);
    img.fill(src, color: img.ColorRgba8(0, 0, 0, 255));
    img.fillCircle(
      src,
      x: 40,
      y: 40,
      radius: 28,
      color: img.ColorRgba8(0, 111, 55, 255),
    );
    img.fillCircle(
      src,
      x: 40,
      y: 40,
      radius: 12,
      color: img.ColorRgba8(0, 0, 0, 255),
    );
    final out = LogoImageProcessor.normalizeToVisibleContent(
      Uint8List.fromList(img.encodePng(src)),
    );
    final decoded = img.decodeImage(out)!;
    final hole = decoded.getPixel(decoded.width ~/ 2, decoded.height ~/ 2);
    expect(hole.a.toInt(), lessThan(40),
        reason: 'counter inside the letter must be transparent');
    var green = 0;
    for (var y = 0; y < decoded.height; y++) {
      for (var x = 0; x < decoded.width; x++) {
        final p = decoded.getPixel(x, y);
        if (p.a.toInt() < 96) continue;
        if (p.g.toInt() > p.r.toInt() + 20) green++;
      }
    }
    expect(green, greaterThan(80));
    final corner = decoded.getPixel(0, 0);
    expect(corner.a.toInt(), lessThan(40));
  });

  test('enclosed white letter counters become transparent', () {
    final src = img.Image(width: 80, height: 80, numChannels: 4);
    img.fill(src, color: img.ColorRgba8(255, 255, 255, 255));
    img.fillCircle(
      src,
      x: 40,
      y: 40,
      radius: 28,
      color: img.ColorRgba8(20, 40, 160, 255),
    );
    img.fillCircle(
      src,
      x: 40,
      y: 40,
      radius: 12,
      color: img.ColorRgba8(255, 255, 255, 255),
    );
    final out = LogoImageProcessor.normalizeToVisibleContent(
      Uint8List.fromList(img.encodePng(src)),
    );
    final decoded = img.decodeImage(out)!;
    final hole = decoded.getPixel(decoded.width ~/ 2, decoded.height ~/ 2);
    expect(hole.a.toInt(), lessThan(40),
        reason: 'white counter must punch to transparent');
    var blue = 0;
    for (var y = 0; y < decoded.height; y++) {
      for (var x = 0; x < decoded.width; x++) {
        final p = decoded.getPixel(x, y);
        if (p.a.toInt() < 96) continue;
        if (p.b.toInt() > p.r.toInt() + 40) blue++;
      }
    }
    expect(blue, greaterThan(80));
  });

  test('black outline against transparent canvas is kept', () {
    final src = img.Image(width: 60, height: 60, numChannels: 4);
    img.fill(src, color: img.ColorRgba8(0, 0, 0, 0));
    img.fillCircle(
      src,
      x: 30,
      y: 30,
      radius: 18,
      color: img.ColorRgba8(0, 0, 0, 255),
    );
    img.fillCircle(
      src,
      x: 30,
      y: 30,
      radius: 12,
      color: img.ColorRgba8(200, 70, 40, 255),
    );
    final out = LogoImageProcessor.normalizeToVisibleContent(
      Uint8List.fromList(img.encodePng(src)),
    );
    final decoded = img.decodeImage(out)!;
    // Outer ring should still have black stroke pixels.
    var black = 0;
    var orange = 0;
    for (var y = 0; y < decoded.height; y++) {
      for (var x = 0; x < decoded.width; x++) {
        final p = decoded.getPixel(x, y);
        if (p.a.toInt() < 96) continue;
        final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
        if (r < 40 && g < 40 && b < 40) black++;
        if (r > 140 && g < 120 && b < 80) orange++;
      }
    }
    expect(orange, greaterThan(40));
    expect(black, greaterThan(20));
  });

  test('prepareRasterForRestore strips plate before restore', () {
    final src = img.Image(width: 100, height: 60, numChannels: 4);
    img.fill(src, color: img.ColorRgba8(255, 255, 255, 255));
    img.fillRect(
      src,
      x1: 20,
      y1: 15,
      x2: 80,
      y2: 45,
      color: img.ColorRgba8(0, 111, 55, 255),
    );
    final prepared = LogoImageProcessor.prepareRasterForRestore(
      Uint8List.fromList(img.encodePng(src)),
    );
    final decoded = img.decodeImage(prepared)!;
    final corner = decoded.getPixel(0, 0);
    expect(corner.a.toInt(), lessThan(20));
    final mid = decoded.getPixel(decoded.width ~/ 2, decoded.height ~/ 2);
    expect(mid.g.toInt(), greaterThan(80));
    expect(mid.a.toInt(), greaterThan(200));
  });

  test('snapToSourceBrandColors corrects hue drift from a bad restore', () {
    final source = img.Image(width: 80, height: 40, numChannels: 4);
    img.fill(source, color: img.ColorRgba8(0, 0, 0, 0));
    img.fillRect(
      source,
      x1: 4,
      y1: 8,
      x2: 75,
      y2: 35,
      color: img.ColorRgba8(0, 111, 55, 255),
    );
    final sourceBytes = Uint8List.fromList(img.encodePng(source));

    // Gemini-style drift: brownish green + invented neon yellow plate.
    final bad = img.Image(width: 80, height: 40, numChannels: 4);
    img.fill(bad, color: img.ColorRgba8(0, 0, 0, 0));
    img.fillRect(
      bad,
      x1: 4,
      y1: 8,
      x2: 75,
      y2: 35,
      color: img.ColorRgba8(18, 128, 62, 255),
    );
    final snapped = LogoImageProcessor.snapToSourceBrandColors(bad, sourceBytes);
    final mid = snapped.getPixel(40, 20);
    expect(mid.g.toInt(), greaterThan(mid.r.toInt() + 20));
    expect(mid.r.toInt(), lessThan(40));
  });

  test('finalize snaps drifted colors instead of leaving brown wordmark', () {
    final source = img.Image(width: 120, height: 50, numChannels: 4);
    img.fill(source, color: img.ColorRgba8(0, 0, 0, 0));
    img.fillRect(
      source,
      x1: 6,
      y1: 12,
      x2: 110,
      y2: 42,
      color: img.ColorRgba8(0, 111, 55, 255),
    );
    img.fillRect(
      source,
      x1: 70,
      y1: 4,
      x2: 105,
      y2: 16,
      color: img.ColorRgba8(230, 100, 30, 255),
    );
    final sourceBytes = Uint8List.fromList(img.encodePng(source));

    final drifted = img.Image(width: 120, height: 50, numChannels: 4);
    img.fill(drifted, color: img.ColorRgba8(0, 0, 0, 0));
    img.fillRect(
      drifted,
      x1: 6,
      y1: 12,
      x2: 110,
      y2: 42,
      color: img.ColorRgba8(140, 95, 45, 255),
    );
    img.fillRect(
      drifted,
      x1: 70,
      y1: 4,
      x2: 105,
      y2: 16,
      color: img.ColorRgba8(255, 160, 60, 255),
    );

    final out = LogoRestorer.finalizeRestoredPng(
      Uint8List.fromList(img.encodePng(drifted)),
      sourceBytes: sourceBytes,
    );
    final decoded = img.decodeImage(out)!;
    var green = 0;
    for (var y = 0; y < decoded.height; y += 16) {
      for (var x = 0; x < decoded.width; x += 16) {
        final p = decoded.getPixel(x, y);
        if (p.a.toInt() < 96) continue;
        final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
        if (g > r + 20 && g > b + 20 && r < 40) green++;
      }
    }
    expect(green, greaterThan(10));
  });

  test('thin enclosed white highlight is not punched as a letter hole', () {
    final src = img.Image(width: 120, height: 80, numChannels: 4);
    img.fill(src, color: img.ColorRgba8(255, 255, 255, 255));
    img.fillRect(
      src,
      x1: 10,
      y1: 10,
      x2: 109,
      y2: 69,
      color: img.ColorRgba8(40, 150, 70, 255),
    );
    img.fillRect(
      src,
      x1: 20,
      y1: 36,
      x2: 99,
      y2: 39,
      color: img.ColorRgba8(255, 255, 255, 255),
    );
    final out = LogoImageProcessor.normalizeToVisibleContent(
      Uint8List.fromList(img.encodePng(src)),
    );
    final decoded = img.decodeImage(out)!;
    var white = 0;
    var green = 0;
    for (var y = 0; y < decoded.height; y++) {
      for (var x = 0; x < decoded.width; x++) {
        final p = decoded.getPixel(x, y);
        if (p.a.toInt() < 96) continue;
        final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
        if (r >= 240 && g >= 240 && b >= 240) white++;
        if (g > r + 20 && g > b + 20) green++;
      }
    }
    expect(green, greaterThan(80));
    expect(white, greaterThan(20));
  });

  test('cubic enhance matches source geometry after downscale', () {
    final src = img.Image(width: 40, height: 20, numChannels: 4);
    img.fill(src, color: img.ColorRgba8(0, 0, 0, 0));
    img.fillRect(
      src,
      x1: 4,
      y1: 4,
      x2: 35,
      y2: 15,
      color: img.ColorRgba8(20, 40, 160, 255),
    );
    final bytes = Uint8List.fromList(img.encodePng(src));
    final up = LogoImageProcessor.upscaleForPrint(bytes, minHeight: 80);
    expect(LogoImageProcessor.matchesSourceGeometry(bytes, up), isTrue);

    final warped = img.Image(width: 80, height: 40, numChannels: 4);
    img.fill(warped, color: img.ColorRgba8(0, 0, 0, 0));
    img.fillCircle(
      warped,
      x: 40,
      y: 20,
      radius: 16,
      color: img.ColorRgba8(20, 40, 160, 255),
    );
    expect(
      LogoImageProcessor.matchesSourceGeometry(
        bytes,
        Uint8List.fromList(img.encodePng(warped)),
      ),
      isFalse,
    );
  });

  test('isFaithfulRestore rejects a different lockup and a checker matte', () {
    final src = img.Image(width: 80, height: 40, numChannels: 4);
    img.fill(src, color: img.ColorRgba8(0, 0, 0, 0));
    img.fillRect(
      src,
      x1: 6,
      y1: 8,
      x2: 74,
      y2: 32,
      color: img.ColorRgba8(0, 90, 180, 255),
    );
    final srcPng = Uint8List.fromList(img.encodePng(src));

    final other = img.Image(width: 80, height: 40, numChannels: 4);
    img.fill(other, color: img.ColorRgba8(0, 0, 0, 0));
    img.fillCircle(
      other,
      x: 40,
      y: 20,
      radius: 14,
      color: img.ColorRgba8(0, 90, 180, 255),
    );
    expect(
      LogoImageProcessor.isFaithfulRestore(
        srcPng,
        Uint8List.fromList(img.encodePng(other)),
      ),
      isFalse,
    );

    final check = img.Image(width: 64, height: 64, numChannels: 4);
    for (var y = 0; y < 64; y++) {
      for (var x = 0; x < 64; x++) {
        final on = ((x ~/ 8) + (y ~/ 8)).isEven;
        check.setPixelRgba(x, y, on ? 230 : 40, on ? 230 : 40, on ? 230 : 40, 255);
      }
    }
    expect(
      LogoImageProcessor.looksLikeCheckerboardMatte(
        Uint8List.fromList(img.encodePng(check)),
      ),
      isTrue,
    );
  });

  test('grey metallic letters on a black plate survive knockout', () {
    final src = img.Image(width: 220, height: 90, numChannels: 4);
    img.fill(src, color: img.ColorRgba8(0, 0, 0, 255));
    img.fillRect(
      src,
      x1: 12,
      y1: 18,
      x2: 100,
      y2: 48,
      color: img.ColorRgba8(96, 96, 96, 255),
    );
    img.fillRect(
      src,
      x1: 108,
      y1: 18,
      x2: 208,
      y2: 48,
      color: img.ColorRgba8(0, 190, 70, 255),
    );
    for (var y = 56; y <= 76; y++) {
      final lum = 70 + ((y - 56) * 5);
      for (var x = 30; x <= 190; x++) {
        src.setPixelRgba(x, y, lum, lum, lum, 255);
      }
    }
    final out = LogoImageProcessor.processWithOptions(
      Uint8List.fromList(img.encodePng(src)),
      LogoImportOptions.standard(perfectLogo: false),
    );
    final decoded = img.decodeImage(out)!;
    var grey = 0, green = 0, gradient = 0;
    for (var y = 0; y < decoded.height; y++) {
      for (var x = 0; x < decoded.width; x++) {
        final p = decoded.getPixel(x, y);
        if (p.a.toInt() < 96) continue;
        final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
        final sat = [r, g, b].reduce((a, c) => a > c ? a : c) -
            [r, g, b].reduce((a, c) => a < c ? a : c);
        final lum = (r + g + b) / 3.0;
        if (g > r + 20 && g > b + 20) {
          green++;
        } else if (sat < 28 && lum >= 85) {
          gradient++;
        } else if (sat < 28 && lum >= 48) {
          grey++;
        }
      }
    }
    expect(decoded.getPixel(0, 0).a.toInt(), lessThan(40));
    expect(green, greaterThan(80));
    expect(grey, greaterThan(40));
    expect(gradient, greaterThan(40));
  });

  test('black letter bodies on a white plate survive knockout', () {
    final src = img.Image(width: 160, height: 80, numChannels: 4);
    img.fill(src, color: img.ColorRgba8(255, 255, 255, 255));
    img.fillRect(
      src,
      x1: 16,
      y1: 16,
      x2: 70,
      y2: 64,
      color: img.ColorRgba8(10, 10, 10, 255),
    );
    img.fillRect(
      src,
      x1: 30,
      y1: 28,
      x2: 48,
      y2: 46,
      color: img.ColorRgba8(255, 255, 255, 255),
    );
    img.fillRect(
      src,
      x1: 86,
      y1: 20,
      x2: 140,
      y2: 58,
      color: img.ColorRgba8(12, 12, 12, 255),
    );
    final out = LogoImageProcessor.processWithOptions(
      Uint8List.fromList(img.encodePng(src)),
      LogoImportOptions.standard(),
    );
    final decoded = img.decodeImage(out)!;
    var black = 0;
    for (var y = 0; y < decoded.height; y++) {
      for (var x = 0; x < decoded.width; x++) {
        final p = decoded.getPixel(x, y);
        if (p.a.toInt() < 96) continue;
        if (p.r.toInt() < 40 && p.g.toInt() < 40 && p.b.toInt() < 40) black++;
      }
    }
    expect(decoded.getPixel(0, 0).a.toInt(), lessThan(40));
    expect(black, greaterThan(200));
    var punched = 0;
    for (var y = 0; y < decoded.height; y++) {
      for (var x = 0; x < decoded.width; x++) {
        if (decoded.getPixel(x, y).a.toInt() < 40) punched++;
      }
    }
    expect(punched, greaterThan(20));
  });

  test('snap does not recolor grey fills to a chromatic accent', () {
    final source = img.Image(width: 120, height: 40, numChannels: 4);
    img.fill(source, color: img.ColorRgba8(0, 0, 0, 0));
    img.fillRect(
      source,
      x1: 4,
      y1: 8,
      x2: 58,
      y2: 32,
      color: img.ColorRgba8(110, 110, 110, 255),
    );
    img.fillRect(
      source,
      x1: 64,
      y1: 8,
      x2: 114,
      y2: 32,
      color: img.ColorRgba8(0, 180, 70, 255),
    );
    final sourceBytes = Uint8List.fromList(img.encodePng(source));

    final mixed = img.decodeImage(sourceBytes)!;
    final snapped = LogoImageProcessor.snapToSourceBrandColors(
      mixed,
      sourceBytes,
    );
    final grey = snapped.getPixel(20, 20);
    final green = snapped.getPixel(90, 20);
    expect(grey.g.toInt() - grey.r.toInt(), lessThan(20));
    expect(green.g.toInt(), greaterThan(green.r.toInt() + 40));
  });

  test('redrawn lockup is not a faithful restore', () {
    final source = img.Image(width: 80, height: 40, numChannels: 4);
    img.fill(source, color: img.ColorRgba8(0, 0, 0, 0));
    img.fillRect(
      source,
      x1: 4,
      y1: 8,
      x2: 38,
      y2: 32,
      color: img.ColorRgba8(100, 100, 100, 255),
    );
    img.fillRect(
      source,
      x1: 42,
      y1: 8,
      x2: 76,
      y2: 32,
      color: img.ColorRgba8(0, 180, 70, 255),
    );
    final srcBytes = Uint8List.fromList(img.encodePng(source));

    final redraw = img.Image(width: 80, height: 40, numChannels: 4);
    img.fill(redraw, color: img.ColorRgba8(0, 0, 0, 0));
    img.fillRect(
      redraw,
      x1: 4,
      y1: 6,
      x2: 76,
      y2: 34,
      color: img.ColorRgba8(0, 200, 80, 255),
    );
    final redBytes = Uint8List.fromList(img.encodePng(redraw));
    expect(LogoImageProcessor.isFaithfulRestore(srcBytes, redBytes), isFalse);
    expect(LogoImageProcessor.retainsBrandColors(srcBytes, redBytes), isFalse);
  });

  test('generative restore is skipped for already high-res sources', () {
    expect(LogoRestorer.usesGenerativeRestore(200), isTrue);
    expect(LogoRestorer.usesGenerativeRestore(1599), isTrue);
    expect(LogoRestorer.usesGenerativeRestore(1600), isFalse);
    expect(LogoRestorer.usesGenerativeRestore(6000), isFalse);
  });

  test('palette PNG knockout keeps ink (Apex Valves crawl cache)', () {
    final repo = Directory.current.path.endsWith('mobile')
        ? Directory.current.parent
        : Directory.current;
    final apex = File(
      p.join(
        repo.path,
        'qa_logs',
        'logo_restore_marathon',
        'crawl_cache',
        'apex_valves.png',
      ),
    );
    if (!apex.existsSync()) return;
    final bytes = apex.readAsBytesSync();
    final out = LogoImageProcessor.processWithOptions(
      bytes,
      LogoImportOptions.standard(perfectLogo: false),
    );
    final decoded = img.decodeImage(out)!;
    expect(decoded.width, greaterThan(200));
    expect(decoded.height, greaterThan(80));
    var ink = 0;
    for (var y = 0; y < decoded.height; y++) {
      for (var x = 0; x < decoded.width; x++) {
        final p = decoded.getPixel(x, y);
        if (p.a.toInt() < 96) continue;
        final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
        if (r >= 250 && g >= 250 && b >= 250) continue;
        ink++;
      }
    }
    expect(ink, greaterThan(5000));
  });

  test('aspectDrift rejects a square Gemini hallucination on a wide lockup', () {
    final wide = img.Image(width: 80, height: 20, numChannels: 4);
    img.fill(wide, color: img.ColorRgba8(0, 0, 0, 0));
    img.fillRect(
      wide,
      x1: 4,
      y1: 4,
      x2: 76,
      y2: 15,
      color: img.ColorRgba8(0, 90, 180, 255),
    );
    final widePng = Uint8List.fromList(img.encodePng(wide));

    final square = img.Image(width: 40, height: 40, numChannels: 4);
    img.fill(square, color: img.ColorRgba8(0, 0, 0, 0));
    img.fillCircle(
      square,
      x: 20,
      y: 20,
      radius: 16,
      color: img.ColorRgba8(80, 80, 80, 255),
    );
    final squarePng = Uint8List.fromList(img.encodePng(square));

    expect(LogoImageProcessor.aspectDrift(widePng, squarePng), greaterThan(0.32));
    expect(
      LogoImageProcessor.isFaithfulRestore(widePng, squarePng),
      isFalse,
    );
  });

  test('conservator finalize does not hollow grey letter fills', () {
    final src = img.Image(width: 120, height: 40, numChannels: 4);
    img.fill(src, color: img.ColorRgba8(0, 0, 0, 0));
    img.fillRect(
      src,
      x1: 8,
      y1: 10,
      x2: 112,
      y2: 30,
      color: img.ColorRgba8(120, 120, 120, 255),
    );
    final bytes = Uint8List.fromList(img.encodePng(src));
    final up = LogoImageProcessor.upscaleForPrint(bytes, minHeight: 120);
    final out = LogoRestorer.finalizeRestoredPng(
      up,
      sourceBytes: bytes,
      enhanceOnly: true,
    );
    final decoded = img.decodeImage(out)!;
    var grey = 0;
    for (var y = 0; y < decoded.height; y++) {
      for (var x = 0; x < decoded.width; x++) {
        final p = decoded.getPixel(x, y);
        if (p.a.toInt() < 180) continue;
        final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
        final sat = [r, g, b].reduce((a, c) => a > c ? a : c) -
            [r, g, b].reduce((a, c) => a < c ? a : c);
        if (sat < 28 && (r + g + b) / 3.0 >= 70) grey++;
      }
    }
    expect(grey, greaterThan(800));
    expect(
      LogoImageProcessor.isFaithfulRestore(bytes, out),
      isTrue,
    );
  });

  test('premultiplied upscale keeps thin black type and does not halo', () {
    final src = img.Image(width: 80, height: 28, numChannels: 4);
    img.fill(src, color: img.ColorRgba8(0, 0, 0, 0));
    img.fillRect(
      src,
      x1: 6,
      y1: 18,
      x2: 74,
      y2: 21,
      color: img.ColorRgba8(12, 12, 12, 255),
    );
    img.fillRect(
      src,
      x1: 8,
      y1: 4,
      x2: 40,
      y2: 14,
      color: img.ColorRgba8(0, 90, 180, 255),
    );
    final bytes = Uint8List.fromList(img.encodePng(src));
    final up = LogoImageProcessor.upscaleForPrint(bytes, minHeight: 140);
    final decoded = img.decodeImage(up)!;
    var black = 0;
    var whiteHalo = 0;
    for (var y = 0; y < decoded.height; y++) {
      for (var x = 0; x < decoded.width; x++) {
        final p = decoded.getPixel(x, y);
        final a = p.a.toInt();
        if (a < 40) continue;
        final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
        final lum = (r + g + b) / 3.0;
        if (a < 200 && lum >= 210) whiteHalo++;
        if (a >= 180 && r < 40 && g < 40 && b < 40) black++;
      }
    }
    expect(black, greaterThan(80));
    expect(whiteHalo, lessThan(30));
  });

  test('grey tagline wider than the chromatic wordmark is not cropped', () {
    final src = img.Image(width: 220, height: 120, numChannels: 4);
    img.fill(src, color: img.ColorRgba8(255, 255, 255, 255));
    img.fillRect(
      src,
      x1: 70,
      y1: 20,
      x2: 150,
      y2: 70,
      color: img.ColorRgba8(200, 30, 30, 255),
    );
    img.fillRect(
      src,
      x1: 10,
      y1: 92,
      x2: 209,
      y2: 104,
      color: img.ColorRgba8(90, 90, 90, 255),
    );
    final out = LogoImageProcessor.processWithOptions(
      Uint8List.fromList(img.encodePng(src)),
      LogoImportOptions.standard(perfectLogo: false),
    );
    final decoded = img.decodeImage(out)!;
    var grey = 0;
    var red = 0;
    for (var y = 0; y < decoded.height; y++) {
      for (var x = 0; x < decoded.width; x++) {
        final p = decoded.getPixel(x, y);
        if (p.a.toInt() < 96) continue;
        final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
        if (r > 150 && g < 80 && b < 80) red++;
        if ((r - g).abs() < 20 && (r - b).abs() < 20 && r >= 60 && r <= 140) {
          grey++;
        }
      }
    }
    expect(red, greaterThan(80));
    expect(grey, greaterThan(80));
    expect(decoded.width / decoded.height, greaterThan(1.4));
  });

  test('letterboxed restore still matches knockout geometry', () {
    final mark = img.Image(width: 80, height: 24, numChannels: 4);
    img.fill(mark, color: img.ColorRgba8(0, 0, 0, 0));
    img.fillRect(
      mark,
      x1: 4,
      y1: 4,
      x2: 75,
      y2: 19,
      color: img.ColorRgba8(0, 90, 180, 255),
    );
    final tight = Uint8List.fromList(img.encodePng(mark));

    final plate = img.Image(width: 80, height: 70, numChannels: 4);
    img.fill(plate, color: img.ColorRgba8(0, 0, 0, 0));
    img.compositeImage(plate, mark, dstX: 0, dstY: 23);
    final padded = Uint8List.fromList(img.encodePng(plate));

    expect(LogoImageProcessor.matchesSourceGeometry(tight, padded), isTrue);
    expect(LogoImageProcessor.aspectDrift(tight, padded), lessThan(0.12));
    expect(LogoImageProcessor.isFaithfulRestore(tight, padded), isTrue);
  });

  test('black script keeps brand color after knockout', () {
    final src = img.Image(width: 160, height: 50, numChannels: 4);
    img.fill(src, color: img.ColorRgba8(255, 255, 255, 255));
    img.fillRect(
      src,
      x1: 12,
      y1: 14,
      x2: 148,
      y2: 36,
      color: img.ColorRgba8(8, 8, 8, 255),
    );
    final srcBytes = Uint8List.fromList(img.encodePng(src));
    final knockout = LogoImageProcessor.processWithOptions(
      srcBytes,
      LogoImportOptions.standard(perfectLogo: false),
    );
    expect(LogoImageProcessor.retainsBrandColors(srcBytes, knockout), isTrue);
    expect(LogoImageProcessor.retainsBrandColors(knockout, knockout), isTrue);
  });
}
