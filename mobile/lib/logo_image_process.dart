import 'dart:math' as math;
import 'dart:typed_data';
import 'dart:ui';

import 'package:image/image.dart' as img;

import 'logo_import_options.dart';

/// Smart crop + solid-background removal for customer logos.
///
/// Crop only removes empty outer margin (transparent / solid-bg padding).
/// Logo artwork is never trimmed — a small safe pad is kept after crop.
class LogoImageProcessor {
  LogoImageProcessor._();

  static const alphaThreshold = 12;
  static const colorTolerance = 26;
  static const paddingFraction = 0.03;
  static const minPadding = 2;
  static const maxPadding = 14;
  /// Extra pixels kept inside the crop so anti-aliased edges are not clipped.
  static const safeInset = 1;

  /// Decode PNG/JPEG bytes to a true RGBA bitmap.
  ///
  /// Palette/indexed sources (mode P) sometimes decode with missing alpha so
  /// edge floods eat every pixel — Apex Valves knockout collapsed to 1×1.
  static img.Image? decodeToRgba(Uint8List input) {
    final decoded = img.decodeImage(input);
    if (decoded == null) return null;

    img.Image image;
    if (decoded.numChannels == 4) {
      image = decoded.clone();
    } else {
      image = decoded.convert(numChannels: 4, format: img.Format.uint8);
    }

    var opaque = 0;
    for (var y = 0; y < image.height; y += math.max(1, image.height ~/ 12)) {
      for (var x = 0; x < image.width; x += math.max(1, image.width ~/ 12)) {
        if (image.getPixel(x, y).a.toInt() > 200) opaque++;
      }
    }
    if (opaque == 0) {
      for (var y = 0; y < image.height; y++) {
        for (var x = 0; x < image.width; x++) {
          final p = image.getPixel(x, y);
          final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
          if (r >= 250 && g >= 250 && b >= 250) continue;
          if (r + g + b < 24) continue;
          image.setPixelRgba(x, y, r, g, b, 255);
        }
      }
    }
    return image;
  }

  /// Aspect drift between two rasters (0 = identical, 1 = unrelated).
  ///
  /// Compares the **ink bounding box**, not the outer canvas. A wide wordmark
  /// on a square plate vs the same mark tightly cropped is not a warp.
  static double aspectDrift(Uint8List a, Uint8List b) {
    final sa = decodeToRgba(a);
    final sb = decodeToRgba(b);
    if (sa == null || sb == null || sa.height <= 0 || sb.height <= 0) {
      return 1.0;
    }
    final ba = _opaqueInkBounds(sa);
    final bb = _opaqueInkBounds(sb);
    final aspectA = ba == null
        ? sa.width / sa.height
        : (ba.$3 - ba.$1 + 1) / math.max(1, ba.$4 - ba.$2 + 1);
    final aspectB = bb == null
        ? sb.width / sb.height
        : (bb.$3 - bb.$1 + 1) / math.max(1, bb.$4 - bb.$2 + 1);
    return (aspectA - aspectB).abs() / math.max(aspectA, 0.01);
  }

  /// Pad [output] with transparency so its outer canvas aspect matches [source].
  ///
  /// This never stretches or redraws logo ink; it only adds transparent margin.
  static Uint8List padToSourceAspect(
    Uint8List output,
    Uint8List source, {
    double maxDrift = 0.30,
  }) {
    final out = decodeToRgba(output);
    final src = decodeToRgba(source);
    if (out == null || src == null || out.height <= 0 || src.height <= 0) {
      return output;
    }
    final srcAspect = src.width / src.height;
    final outAspect = out.width / out.height;
    final drift = (srcAspect - outAspect).abs() / math.max(srcAspect, 0.01);
    if (drift <= maxDrift) return output;

    var targetW = out.width;
    var targetH = out.height;
    if (outAspect < srcAspect) {
      targetW = math.max(out.width, (out.height * srcAspect).round());
    } else {
      targetH = math.max(out.height, (out.width / srcAspect).round());
    }
    if (targetW == out.width && targetH == out.height) return output;

    final canvas = img.Image(width: targetW, height: targetH, numChannels: 4);
    img.fill(canvas, color: img.ColorRgba8(0, 0, 0, 0));
    final dx = ((targetW - out.width) / 2).round();
    final dy = ((targetH - out.height) / 2).round();
    img.compositeImage(canvas, out, dstX: dx, dstY: dy);
    return Uint8List.fromList(img.encodePng(canvas));
  }

  /// Strip solid plate + crop for restore: raw raster first, enhance second.
  ///
  /// Same knockout/crop as `processWithOptions(... cropMode: auto)` so the
  /// conservator path cannot clip taglines the knockout kept.
  ///
  /// Swift-quality lesson: when the source already has true alpha and looks
  /// studio-ready (clean outer ring, solid fills), skip edge-flood knockout /
  /// JPEG-rim punches that open white seams between chromatic fills and dark
  /// strokes. Only crop + safe-pad those assets.
  static Uint8List prepareRasterForRestore(Uint8List input) {
    if (input.isEmpty) return input;
    var image = decodeToRgba(input);
    if (image == null) return input;

    final alreadyAlpha = _hasMeaningfulTransparency(image);
    final studioReady = alreadyAlpha && _looksStudioReadyAlpha(image);

    if (!alreadyAlpha) {
      _knockOutOuterPlate(image);
      recoverHueConsistentChroma(image);
    } else if (!studioReady) {
      // Transparent but dirty plate leftovers — gentle outer cleanup only.
      _removeCheckerboardMatte(image);
    }

    if (studioReady) {
      // Swift-quality path: do not peel AA / stroke rims. Crop loosely + pad.
      image = _cropToContentBBox(image, null, minAlpha: 32);
      image = addSafePad(image, fraction: 0.02);
    } else {
      image = _cropToContentBBox(image, null, minAlpha: 48);
      image = _trimLowCoverageMargins(image);
      image = addSafePad(image);
    }
    return Uint8List.fromList(img.encodePng(image));
  }

  /// True when outer ring is already clean transparent (Swift-style lockup).
  static bool _looksStudioReadyAlpha(img.Image image) {
    final w = image.width;
    final h = image.height;
    if (w < 8 || h < 8) return false;
    final band = math.max(2, math.min(w, h) ~/ 25);
    var edge = 0;
    var opaquePlate = 0;
    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        final onEdge =
            x < band || y < band || x >= w - band || y >= h - band;
        if (!onEdge) continue;
        edge++;
        final p = image.getPixel(x, y);
        final a = p.a.toInt();
        if (a < 40) continue;
        final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
        final lum = (r + g + b) / 3.0;
        final sat =
            math.max(r, math.max(g, b)) - math.min(r, math.min(g, b));
        if ((lum > 245 && sat < 12) || (lum < 18 && sat < 12)) {
          opaquePlate++;
        }
      }
    }
    if (edge == 0) return false;
    return opaquePlate / edge < 0.02;
  }

  /// Remap restored opaque pixels onto the nearest source brand fill so Gemini
  /// cannot shift hues (green→brown, Shell yellow→neon, etc.).
  static img.Image snapToSourceBrandColors(
    img.Image restored,
    Uint8List sourcePng,
  ) {
    final brands = dominantBrandColors(sourcePng, minFraction: 0.03);
    if (brands.isEmpty) return restored;
    final out = restored.numChannels == 4
        ? restored.clone()
        : restored.convert(numChannels: 4);
    final palette = [
      for (final b in brands) (b.r, b.g, b.b),
    ];
    for (var y = 0; y < out.height; y++) {
      for (var x = 0; x < out.width; x++) {
        final p = out.getPixel(x, y);
        final a = p.a.toInt();
        if (a < 40) continue;
        final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
        if (_isNearBlackCanvas(r, g, b)) {
          // Keep black strokes; enclosed plate holes are punched below.
          out.setPixelRgba(x, y, 0, 0, 0, a);
          continue;
        }
        if (r >= 245 && g >= 245 && b >= 245) {
          // Keep white brand marks (snow lines, wordmark fills). Letter
          // counters are punched as enclosed plate holes below.
          continue;
        }
        var best = palette.first;
        var bestD = 1 << 30;
        final pixelGrey = _isGreyFill(r, g, b);
        for (final c in palette) {
          if (pixelGrey != _isGreyFill(c.$1, c.$2, c.$3)) continue;
          final d = _colorDistance(r, g, b, c.$1, c.$2, c.$3);
          if (d < bestD) {
            bestD = d;
            best = c;
          }
        }
        // Only correct nearby hue drift. Never map grey/black/silver ink
        // onto the nearest chromatic accent.
        if (bestD > 48) continue;
        if (a >= 180) {
          out.setPixelRgba(x, y, best.$1, best.$2, best.$3, a);
        } else {
          final t = a / 255.0;
          out.setPixelRgba(
            x,
            y,
            (r + (best.$1 - r) * t).round().clamp(0, 255),
            (g + (best.$2 - g) * t).round().clamp(0, 255),
            (b + (best.$3 - b) * t).round().clamp(0, 255),
            a,
          );
        }
      }
    }
    return out;
  }

  /// Hex list for Gemini: exact brand fills already measured on the cleaned raster.
  static String brandColorPromptNote(Uint8List png) {
    final brands = dominantBrandColors(png, minFraction: 0.03);
    if (brands.isEmpty) return '';
    final hexes = brands
        .map(
          (c) =>
              '#${c.r.toRadixString(16).padLeft(2, '0')}'
              '${c.g.toRadixString(16).padLeft(2, '0')}'
              '${c.b.toRadixString(16).padLeft(2, '0')}',
        )
        .join(', ');
    return '\nBRAND FILLS (exact — use these hex values only, do not shift hue '
        'or invent new colors): $hexes.\n';
  }

  /// True when the bitmap looks like a UI transparency checkerboard (Gemini
  /// sometimes redraws the matte as yellow/gray tiles).
  static bool looksLikeCheckerboardMatte(Uint8List png) {
    final image = img.decodeImage(png);
    if (image == null || image.width < 16 || image.height < 16) return false;
    final w = image.width;
    final h = image.height;
    var tileHits = 0;
    var samples = 0;
    const step = 8;
    for (var y = 0; y < h - step; y += step) {
      for (var x = 0; x < w - step; x += step) {
        final a = image.getPixel(x, y);
        final b = image.getPixel(x + step, y);
        final c = image.getPixel(x, y + step);
        final d = image.getPixel(x + step, y + step);
        samples++;
        final contrastAb = _colorDistance(
          a.r.toInt(), a.g.toInt(), a.b.toInt(),
          b.r.toInt(), b.g.toInt(), b.b.toInt(),
        );
        final contrastAc = _colorDistance(
          a.r.toInt(), a.g.toInt(), a.b.toInt(),
          c.r.toInt(), c.g.toInt(), c.b.toInt(),
        );
        final matchAd = _colorDistance(
          a.r.toInt(), a.g.toInt(), a.b.toInt(),
          d.r.toInt(), d.g.toInt(), d.b.toInt(),
        );
        if (contrastAb > 40 && contrastAc > 40 && matchAd < 28) {
          tileHits++;
        }
      }
    }
    return samples > 20 && tileHits / samples > 0.08;
  }

  /// Cubic upscale of the existing raster — recovers pixelation without
  /// redrawing or warping letterforms.
  ///
  /// Uses premultiplied alpha so transparent-neighbor interpolation cannot
  /// pull JPEG-white AA into a halo or hollow thin grey type.
  static Uint8List upscaleForPrint(Uint8List png, {required int minHeight}) {
    if (png.isEmpty) return png;
    final decoded = decodeToRgba(png);
    if (decoded == null || decoded.height <= 0) return png;
    if (decoded.height >= minHeight) {
      return Uint8List.fromList(img.encodePng(decoded));
    }
    final scale = minHeight / decoded.height;
    final w = math.max(1, (decoded.width * scale).round());
    final out = resizePremultipliedCubic(decoded, width: w, height: minHeight);
    return Uint8List.fromList(img.encodePng(out));
  }

  /// Premultiplied resize with smooth interpolation (never nearest-neighbor).
  ///
  /// Cubic for upscale / modest resize; average when downscaling to preserve
  /// anti-aliased edges without blocky stair-steps.
  static img.Image resizePremultipliedCubic(
    img.Image src, {
    required int width,
    required int height,
  }) {
    final work = src.numChannels == 4 ? src.clone() : src.convert(numChannels: 4);
    _premultiplyInPlace(work);
    final scaleX = width / math.max(1, work.width);
    final scaleY = height / math.max(1, work.height);
    final scale = math.max(scaleX, scaleY);
    final interp = scale < 1.0
        ? img.Interpolation.average
        : img.Interpolation.cubic;
    final scaled = img.copyResize(
      work,
      width: width,
      height: height,
      interpolation: interp,
    );
    _unpremultiplyInPlace(scaled);
    return scaled;
  }

  static void _premultiplyInPlace(img.Image image) {
    if (image.numChannels < 4) return;
    final buf = image.toUint8List();
    final nc = image.numChannels;
    for (var i = 0; i + 3 < buf.length; i += nc) {
      final a = buf[i + 3];
      if (a == 0) {
        buf[i] = 0;
        buf[i + 1] = 0;
        buf[i + 2] = 0;
        continue;
      }
      if (a == 255) continue;
      buf[i] = (buf[i] * a / 255.0).round().clamp(0, 255);
      buf[i + 1] = (buf[i + 1] * a / 255.0).round().clamp(0, 255);
      buf[i + 2] = (buf[i + 2] * a / 255.0).round().clamp(0, 255);
    }
  }

  static void _unpremultiplyInPlace(img.Image image) {
    if (image.numChannels < 4) return;
    final buf = image.toUint8List();
    final nc = image.numChannels;
    for (var i = 0; i + 3 < buf.length; i += nc) {
      final a = buf[i + 3];
      if (a == 0 || a >= 254) continue;
      final fa = a / 255.0;
      buf[i] = (buf[i] / fa).round().clamp(0, 255);
      buf[i + 1] = (buf[i + 1] / fa).round().clamp(0, 255);
      buf[i + 2] = (buf[i + 2] / fa).round().clamp(0, 255);
    }
  }

  /// True when [result] is a super-resolution of [source], not a redraw.
  ///
  /// Crops both rasters to opaque ink first so transparent letterboxing /
  /// source-plate padding cannot fail IoU against a tight knockout crop.
  static bool matchesSourceGeometry(
    Uint8List source,
    Uint8List result, {
    double maxMeanAbs = 28,
    double minAlphaIou = 0.90,
  }) {
    final srcRaw = decodeToRgba(source);
    final dstRaw = decodeToRgba(result);
    if (srcRaw == null || dstRaw == null) return false;
    final src = _cropToOpaqueInk(srcRaw) ?? srcRaw;
    final dst = _cropToOpaqueInk(dstRaw) ?? dstRaw;
    if (src.width < 4 || src.height < 4) return true;
    final scaled = img.copyResize(
      dst,
      width: src.width,
      height: src.height,
      interpolation: img.Interpolation.average,
    );
    var abs = 0;
    var n = 0;
    var inter = 0;
    var union = 0;
    for (var y = 0; y < src.height; y++) {
      for (var x = 0; x < src.width; x++) {
        final a = src.getPixel(x, y);
        final b = scaled.getPixel(x, y);
        final aa = a.a.toInt();
        final ba = b.a.toInt();
        // JPEG/cubic AA (alpha 40–95) is not the lockup. Scoring it as ink
        // made cubic conservators fail against native knockouts.
        const core = 96;
        final aOn = aa >= core;
        final bOn = ba >= core;
        if (aOn && bOn) inter++;
        if (aOn || bOn) union++;
        if (!aOn || !bOn) continue;
        n++;
        abs += (a.r.toInt() - b.r.toInt()).abs();
        abs += (a.g.toInt() - b.g.toInt()).abs();
        abs += (a.b.toInt() - b.b.toInt()).abs();
      }
    }
    if (n == 0 || union == 0) return false;
    if (inter / union < minAlphaIou) return false;
    return abs / (n * 4) <= maxMeanAbs;
  }

  /// True when [result] still has the source lockup — geometry, fills, no matte.
  static bool isFaithfulRestore(Uint8List source, Uint8List result) {
    if (result.isEmpty || source.isEmpty) return false;
    if (looksLikeCheckerboardMatte(result)) return false;
    if (aspectDrift(source, result) > 0.32) return false;
    if (!matchesSourceGeometry(source, result, maxMeanAbs: 22, minAlphaIou: 0.90)) {
      return false;
    }
    return retainsBrandColors(source, result);
  }

  /// Looser accept gate for Real-ESRGAN (allowed to invent edge/fill detail).
  ///
  /// Still rejects matte plates, extreme aspect warps, and total redraws.
  static bool isAcceptableSuperResolution(Uint8List source, Uint8List result) {
    if (result.isEmpty || source.isEmpty) return false;
    if (looksLikeCheckerboardMatte(result)) return false;
    if (aspectDrift(source, result) > 0.40) return false;
    // Palette collapse (Arc red→mauve, etc.) must not beat cubic fallback.
    if (!retainsBrandColors(source, result)) return false;
    final srcH = decodeToRgba(source)?.height ?? 0;
    final dstH = decodeToRgba(result)?.height ?? 0;
    final grew = srcH > 0 && dstH >= (srcH * 1.8).round();
    if (matchesSourceGeometry(
      source,
      result,
      maxMeanAbs: 42,
      minAlphaIou: 0.72,
    )) {
      return true;
    }
    // Strong scale-up with brand colors retained is enough for SR accept.
    return grew;
  }

  static void _removeCheckerboardMatte(img.Image image) {
    final w = image.width;
    final h = image.height;
    if (w < 8 || h < 8) return;
    // Sample corner 2x2 blocks; classic light/dark alternating tiles.
    var checker = 0;
    for (final (cx, cy) in [(0, 0), (w - 2, 0), (0, h - 2), (w - 2, h - 2)]) {
      final p00 = image.getPixel(cx, cy);
      final p10 = image.getPixel(cx + 1, cy);
      final d = _colorDistance(
        p00.r.toInt(), p00.g.toInt(), p00.b.toInt(),
        p10.r.toInt(), p10.g.toInt(), p10.b.toInt(),
      );
      if (d > 50) checker++;
    }
    if (checker < 2) return;

    bool isMatteTile(int r, int g, int b, int a) {
      if (a < 8) return true;
      final sat = math.max(r, math.max(g, b)) - math.min(r, math.min(g, b));
      final lum = (r + g + b) / 3.0;
      // Classic PNG preview: near-white + light-gray tiles (low chroma).
      if (lum >= 205 && sat <= 45) return true;
      if (lum >= 145 && lum <= 205 && sat <= 40) return true;
      return false;
    }

    final visited = List<bool>.filled(w * h, false);
    final queue = <int>[];
    void tryEnqueue(int x, int y) {
      if (x < 0 || y < 0 || x >= w || y >= h) return;
      final i = y * w + x;
      if (visited[i]) return;
      final p = image.getPixel(x, y);
      if (!isMatteTile(p.r.toInt(), p.g.toInt(), p.b.toInt(), p.a.toInt())) {
        return;
      }
      visited[i] = true;
      queue.add(i);
    }

    for (var x = 0; x < w; x++) {
      tryEnqueue(x, 0);
      tryEnqueue(x, h - 1);
    }
    for (var y = 0; y < h; y++) {
      tryEnqueue(0, y);
      tryEnqueue(w - 1, y);
    }

    while (queue.isNotEmpty) {
      final i = queue.removeLast();
      final x = i % w;
      final y = i ~/ w;
      image.setPixelRgba(x, y, 0, 0, 0, 0);
      tryEnqueue(x - 1, y);
      tryEnqueue(x + 1, y);
      tryEnqueue(x, y - 1);
      tryEnqueue(x, y + 1);
    }
  }

  /// Decode, trim empty margins, remove solid backgrounds, re-encode PNG.
  static Uint8List process(Uint8List input) =>
      processWithOptions(input, LogoImportOptions.standard());

  /// Crop to opaque / non-background content bounds so PDF height matching
  /// uses the visible artwork, not transparent (or flat) padding.
  ///
  /// Edge flood removes white/black/transparent canvas first, then a true
  /// pixel bounding-box (not whole-row peeling) so a square icon on a large
  /// plate is sized by the mark, not the plate.
  ///
  /// Adds a small transparent safe-pad so flush-cropped wordmarks are not hard
  /// against the PDF header rules (looks “cut off” even when scaled correctly).
  static Uint8List normalizeToVisibleContent(Uint8List input) {
    if (input.isEmpty) return input;
    var image = decodeToRgba(input);
    if (image == null) return input;

    final plateForHoles = _hasMeaningfulTransparency(image)
        ? (255, 255, 255)
        : (_estimateBackgroundColor(image) ?? (255, 255, 255));
    _knockOutOuterPlate(image);
    recoverHueConsistentChroma(image);
    stripForeignMarks(image);
    stripHaloFringe(image, protectStrokeFills: true);

    final beforeW = image.width;
    final beforeH = image.height;
    image = _cropToContentBBox(image, null, minAlpha: 48);
    image = _trimLowCoverageMargins(image);

    if (image.width == beforeW && image.height == beforeH) {
      image = _trimEmptyMargins(image, null);
    }

    image = addSafePad(image);
    // Halo / crop can isolate counters the first punch saw as border-connected.
    _punchEnclosedPlateHoles(image, plateForHoles);

    // Always re-encode the flooded/cropped bitmap — never keep the padded original.
    return Uint8List.fromList(img.encodePng(image));
  }

  /// Punch JPEG / Gemini white-gray halo that frays ink against the plate.
  ///
  /// Light, low-chroma pixels sitting between brand ink and empty canvas are
  /// compression fringe — not part of the lockup. Enclosed white fills (letter
  /// counters, snow marks) do not touch empty canvas and are kept.
  ///
  /// When [protectStrokeFills] is true (Swift-style orange fill + black stroke),
  /// skip punching pixels that sit between chromatic fill and near-black ink —
  /// those AA seams must stay opaque or re-encoding opens white gaps.
  /// Foreground / AABB density — Arc-class thin strokes sit below 0.18.
  static bool isThinStrokeMark(img.Image image) {
    final w = image.width;
    final h = image.height;
    if (w < 8 || h < 8) return false;
    var minX = w, minY = h, maxX = -1, maxY = -1, n = 0;
    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        final p = image.getPixel(x, y);
        if (p.a.toInt() < 40) continue;
        final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
        final lum = (r + g + b) / 3.0;
        final sat = math.max(r, math.max(g, b)) - math.min(r, math.min(g, b));
        if (lum >= 230 && sat <= 22) continue;
        n++;
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
    }
    if (n < 80 || maxX < minX) return false;
    final area = (maxX - minX + 1) * (maxY - minY + 1);
    return n / area < 0.18;
  }

  /// Morphological skeleton of the raw contrast mask (thin strokes only).
  static Uint8List? centerlineProtectMask(img.Image image) {
    if (!isThinStrokeMark(image)) return null;
    final w = image.width;
    final h = image.height;
    var ink = Uint8List(w * h);
    var n = 0;
    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        final p = image.getPixel(x, y);
        if (p.a.toInt() < 40) continue;
        final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
        final lum = (r + g + b) / 3.0;
        final sat = math.max(r, math.max(g, b)) - math.min(r, math.min(g, b));
        if (lum >= 230 && sat <= 22) continue;
        ink[y * w + x] = 1;
        n++;
      }
    }
    if (n < 20) return null;
    // 1px close, then Lantuéjoul skeleton (erode leftover).
    ink = _dilateMask(ink, w, h);
    ink = _erodeMask(ink, w, h);
    final skel = Uint8List(w * h);
    var imgMask = Uint8List.fromList(ink);
    while (true) {
      final eroded = _erodeMask(imgMask, w, h);
      final opened = _dilateMask(eroded, w, h);
      var remain = 0;
      for (var i = 0; i < imgMask.length; i++) {
        if (imgMask[i] != 0 && opened[i] == 0) skel[i] = 1;
        if (eroded[i] != 0) remain++;
      }
      imgMask = eroded;
      if (remain == 0) break;
    }
    for (final v in skel) {
      if (v != 0) return skel;
    }
    return null;
  }

  static Uint8List _erodeMask(Uint8List src, int w, int h) {
    final out = Uint8List(w * h);
    for (var y = 1; y < h - 1; y++) {
      for (var x = 1; x < w - 1; x++) {
        if (src[y * w + x] == 0) continue;
        if (src[y * w + (x - 1)] != 0 &&
            src[y * w + (x + 1)] != 0 &&
            src[(y - 1) * w + x] != 0 &&
            src[(y + 1) * w + x] != 0) {
          out[y * w + x] = 1;
        }
      }
    }
    return out;
  }

  /// HSV hue-angle recover for JPEG-bleached thin-stroke edges.
  ///
  /// Propagates the observed chromatic anchor only — never invents a hue.
  static void recoverHueConsistentChroma(img.Image image) {
    if (!isThinStrokeMark(image)) return;
    final w = image.width;
    final h = image.height;
    var hSumSin = 0.0, hSumCos = 0.0, nHigh = 0;
    var sSum = 0.0, vSum = 0.0;
    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        final p = image.getPixel(x, y);
        if (p.a.toInt() < 200) continue;
        final hsv = _rgbToHsv(p.r.toInt(), p.g.toInt(), p.b.toInt());
        if (hsv.$2 < 0.35) continue;
        final lum = (p.r.toInt() + p.g.toInt() + p.b.toInt()) / 3.0;
        if (lum < 35 || lum > 200) continue;
        final rad = hsv.$1 * math.pi / 180.0;
        hSumSin += math.sin(rad);
        hSumCos += math.cos(rad);
        sSum += hsv.$2;
        vSum += hsv.$3;
        nHigh++;
      }
    }
    if (nHigh < 12) return;
    final hueAnchor =
        (math.atan2(hSumSin / nHigh, hSumCos / nHigh) * 180.0 / math.pi +
            360.0) %
        360.0;
    final sMed = sSum / nHigh;
    final vMed = vSum / nHigh;
    if (sMed < 0.35) return;

    bool nearEmpty(int x, int y) {
      for (var dy = -2; dy <= 2; dy++) {
        for (var dx = -2; dx <= 2; dx++) {
          final nx = x + dx, ny = y + dy;
          if (nx < 0 || ny < 0 || nx >= w || ny >= h) return true;
          if (image.getPixel(nx, ny).a.toInt() < 40) return true;
        }
      }
      return false;
    }

    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        final p = image.getPixel(x, y);
        if (p.a.toInt() < 48) continue;
        if (!nearEmpty(x, y)) continue;
        final hsv = _rgbToHsv(p.r.toInt(), p.g.toInt(), p.b.toInt());
        if (hsv.$2 < 0.08 || hsv.$2 >= 0.35) continue;
        var d = (hsv.$1 - hueAnchor).abs() % 360.0;
        if (d > 180) d = 360 - d;
        if (d > 25) continue;
        final rgb = _hsvToRgb(hueAnchor, sMed, vMed);
        image.setPixelRgba(x, y, rgb.$1, rgb.$2, rgb.$3, p.a.toInt());
      }
    }
  }

  static (double h, double s, double v) _rgbToHsv(int r, int g, int b) {
    final rf = r / 255.0, gf = g / 255.0, bf = b / 255.0;
    final mx = math.max(rf, math.max(gf, bf));
    final mn = math.min(rf, math.min(gf, bf));
    final df = mx - mn;
    var h = 0.0;
    if (df > 1e-8) {
      if (mx == rf) {
        h = (60.0 * ((gf - bf) / df) + 360.0) % 360.0;
      } else if (mx == gf) {
        h = 60.0 * ((bf - rf) / df) + 120.0;
      } else {
        h = 60.0 * ((rf - gf) / df) + 240.0;
      }
    }
    final s = mx > 1e-8 ? df / mx : 0.0;
    return (h, s, mx);
  }

  static (int r, int g, int b) _hsvToRgb(double h, double s, double v) {
    final c = v * s;
    final hp = (h % 360.0) / 60.0;
    final x = c * (1.0 - ((hp % 2.0) - 1.0).abs());
    final m = v - c;
    late final double rp, gp, bp;
    if (hp < 1) {
      rp = c;
      gp = x;
      bp = 0;
    } else if (hp < 2) {
      rp = x;
      gp = c;
      bp = 0;
    } else if (hp < 3) {
      rp = 0;
      gp = c;
      bp = x;
    } else if (hp < 4) {
      rp = 0;
      gp = x;
      bp = c;
    } else if (hp < 5) {
      rp = x;
      gp = 0;
      bp = c;
    } else {
      rp = c;
      gp = 0;
      bp = x;
    }
    return (
      ((rp + m) * 255).round().clamp(0, 255),
      ((gp + m) * 255).round().clamp(0, 255),
      ((bp + m) * 255).round().clamp(0, 255),
    );
  }

  static void stripHaloFringe(
    img.Image image, {
    bool protectStrokeFills = false,
  }) {
    final w = image.width;
    final h = image.height;
    if (w < 4 || h < 4) return;

    bool nearBlack(int r, int g, int b) {
      final sat = math.max(r, math.max(g, b)) - math.min(r, math.min(g, b));
      return (r + g + b) / 3.0 <= 40 && sat <= 16;
    }

    bool chromaticInk(img.Pixel p) {
      if (p.a.toInt() < 80) return false;
      final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
      if (nearBlack(r, g, b)) return false;
      final sat = math.max(r, math.max(g, b)) - math.min(r, math.min(g, b));
      return sat > 45;
    }

    final centerline = centerlineProtectMask(image);
    final punch = Uint8List(w * h);
    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        if (centerline != null && centerline[y * w + x] != 0) continue;
        final p = image.getPixel(x, y);
        final a = p.a.toInt();
        if (a < 40) continue;
        final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
        final sat = math.max(r, math.max(g, b)) - math.min(r, math.min(g, b));
        final lum = (r + g + b) / 3.0;
        // Grey/silver letter bodies are ink, not JPEG halo.
        if (sat < 30 && lum >= 48 && lum <= 215) continue;
        final fringe = (sat < 42 && lum > 88) || (sat < 28 && lum > 70);
        if (!fringe) continue;
        var opaqueN = 0;
        var hasInk = false;
        var hasEmpty = false;
        var hasChromatic = false;
        var hasDarkStroke = false;
        for (var dy = -1; dy <= 1; dy++) {
          for (var dx = -1; dx <= 1; dx++) {
            if (dx == 0 && dy == 0) continue;
            final nx = x + dx;
            final ny = y + dy;
            if (nx < 0 || ny < 0 || nx >= w || ny >= h) {
              hasEmpty = true;
              continue;
            }
            final n = image.getPixel(nx, ny);
            if (n.a.toInt() < 40) {
              hasEmpty = true;
            } else {
              opaqueN++;
              if (chromaticInk(n)) {
                hasInk = true;
                hasChromatic = true;
              }
              final nr = n.r.toInt(), ng = n.g.toInt(), nb = n.b.toInt();
              if (nearBlack(nr, ng, nb)) hasDarkStroke = true;
            }
          }
        }
        // Fills (grey/silver letter bodies) have many opaque neighbors.
        // JPEG halo is a 1px rim between ink and empty canvas.
        if (opaqueN >= 6) continue;
        if (protectStrokeFills && hasChromatic && hasDarkStroke) {
          // Swift lesson: do not punch AA between fill and stroke.
          continue;
        }
        if (hasInk && hasEmpty) punch[y * w + x] = 1;
      }
    }
    for (var i = 0; i < punch.length; i++) {
      if (punch[i] == 0) continue;
      image.setPixelRgba(i % w, i ~/ w, 0, 0, 0, 0);
    }
  }

  /// Clear Gemini/Google watermarks and extra wordmarks that are not the logo.
  ///
  /// The largest ink component is the brand. Small disconnected blobs in the
  /// corners or along the bottom edge (Spark / "Gemini" marks) are punched
  /// to transparent. Brand ® marks that sit next to the lockup are kept.
  static void stripForeignMarks(img.Image image) {
    final w = image.width;
    final h = image.height;
    if (w < 16 || h < 16) return;

    const minA = 80;
    final seen = Uint8List(w * h);
    final comps = <_InkBlob>[];
    const dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)];

    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        final start = y * w + x;
        if (seen[start] != 0) continue;
        if (image.getPixel(x, y).a.toInt() < minA) continue;

        var minX = x, minY = y, maxX = x, maxY = y, area = 0;
        final queue = <int>[start];
        seen[start] = 1;
        while (queue.isNotEmpty) {
          final i = queue.removeLast();
          area++;
          final cx = i % w;
          final cy = i ~/ w;
          if (cx < minX) minX = cx;
          if (cy < minY) minY = cy;
          if (cx > maxX) maxX = cx;
          if (cy > maxY) maxY = cy;
          for (final d in dirs) {
            final nx = cx + d.$1;
            final ny = cy + d.$2;
            if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
            final ni = ny * w + nx;
            if (seen[ni] != 0) continue;
            if (image.getPixel(nx, ny).a.toInt() < minA) continue;
            seen[ni] = 1;
            queue.add(ni);
          }
        }
        comps.add(
          _InkBlob(
            minX: minX,
            minY: minY,
            maxX: maxX,
            maxY: maxY,
            area: area,
          ),
        );
      }
    }
    if (comps.length < 2) return;
    comps.sort((a, b) => b.area.compareTo(a.area));
    final main = comps.first;
    final mainArea = math.max(1, main.area);
    final padX = math.max(8, (w * 0.08).round());
    final padY = math.max(8, (h * 0.08).round());

    bool nearMain(_InkBlob b) {
      return b.maxX >= main.minX - padX &&
          b.minX <= main.maxX + padX &&
          b.maxY >= main.minY - padY &&
          b.minY <= main.maxY + padY;
    }

    for (final b in comps.skip(1)) {
      if (b.area > mainArea * 0.12) continue;
      if (nearMain(b)) continue;
      final cx = (b.minX + b.maxX) / 2;
      final cy = (b.minY + b.maxY) / 2;
      final inCorner = (cx < w * 0.22 || cx > w * 0.78) &&
          (cy < h * 0.22 || cy > h * 0.78);
      if (!inCorner) continue;

      for (var y = b.minY; y <= b.maxY; y++) {
        for (var x = b.minX; x <= b.maxX; x++) {
          if (image.getPixel(x, y).a.toInt() < minA) continue;
          image.setPixelRgba(x, y, 0, 0, 0, 0);
        }
      }
    }
  }

  /// Collapse JPEG / upscale mottling and weak interior gradients to a single
  /// flat fill per connected region, for every hue. Anti-aliased edges are
  /// left alone so geometry stays sharp.
  static img.Image flattenSolidBrandFills(img.Image source) {
    final image = source.numChannels == 4
        ? source.clone()
        : source.convert(numChannels: 4);
    final w = image.width;
    final h = image.height;
    if (w <= 2 || h <= 2) return image;

    const minAlpha = 200;
    const joinTol = 28;
    const edgeTol = 36;
    final visited = List<bool>.filled(w * h, false);
    final region = <int>[];
    final queue = <int>[];

    bool opaqueAt(int i) {
      final p = image.getPixel(i % w, i ~/ w);
      return p.a.toInt() >= minAlpha;
    }

    int dist(int i, int j) {
      final a = image.getPixel(i % w, i ~/ w);
      final b = image.getPixel(j % w, j ~/ w);
      return _colorDistance(
        a.r.toInt(),
        a.g.toInt(),
        a.b.toInt(),
        b.r.toInt(),
        b.g.toInt(),
        b.b.toInt(),
      );
    }

    bool isEdgePixel(int i) {
      final x = i % w;
      final y = i ~/ w;
      for (var dy = -1; dy <= 1; dy++) {
        for (var dx = -1; dx <= 1; dx++) {
          if (dx == 0 && dy == 0) continue;
          final nx = x + dx;
          final ny = y + dy;
          if (nx < 0 || ny < 0 || nx >= w || ny >= h) return true;
          final n = image.getPixel(nx, ny);
          if (n.a.toInt() < minAlpha) return true;
          if (dist(i, ny * w + nx) > edgeTol) return true;
        }
      }
      return false;
    }

    for (var i = 0; i < w * h; i++) {
      if (visited[i] || !opaqueAt(i)) continue;
      region.clear();
      queue
        ..clear()
        ..add(i);
      visited[i] = true;
      var qi = 0;
      while (qi < queue.length) {
        final cur = queue[qi++];
        region.add(cur);
        final x = cur % w;
        final y = cur ~/ w;
        for (final n in [cur - 1, cur + 1, cur - w, cur + w]) {
          if (n < 0 || n >= w * h) continue;
          final nx = n % w;
          final ny = n ~/ w;
          if ((nx - x).abs() + (ny - y).abs() != 1) continue;
          if (visited[n] || !opaqueAt(n)) continue;
          if (dist(cur, n) > joinTol) continue;
          visited[n] = true;
          queue.add(n);
        }
      }
      if (region.length < 24) continue;

      final interiors = <int>[];
      for (final idx in region) {
        if (!isEdgePixel(idx)) interiors.add(idx);
      }
      if (interiors.length < 12) continue;

      final rs = interiors.map((idx) => image.getPixel(idx % w, idx ~/ w).r.toInt()).toList()
        ..sort();
      final gs = interiors.map((idx) => image.getPixel(idx % w, idx ~/ w).g.toInt()).toList()
        ..sort();
      final bs = interiors.map((idx) => image.getPixel(idx % w, idx ~/ w).b.toInt()).toList()
        ..sort();
      final mid = interiors.length ~/ 2;
      final fr = rs[mid];
      final fg = gs[mid];
      final fb = bs[mid];
      for (final idx in interiors) {
        final p = image.getPixel(idx % w, idx ~/ w);
        image.setPixelRgba(idx % w, idx ~/ w, fr, fg, fb, p.a.toInt());
      }
      // Mid-alpha edge mush only — opaque silver rims stay.
      for (final idx in region) {
        if (!isEdgePixel(idx)) continue;
        final p = image.getPixel(idx % w, idx ~/ w);
        final a = p.a.toInt();
        if (a < 96 || a >= 240) continue;
        final d = _colorDistance(
          p.r.toInt(),
          p.g.toInt(),
          p.b.toInt(),
          fr,
          fg,
          fb,
        );
        if (d > 40) continue;
        image.setPixelRgba(idx % w, idx ~/ w, fr, fg, fb, a < 220 ? 255 : a);
      }
    }
    return image;
  }

  /// Collapse soft AA / JPEG fringe toward hard neighbor fills (universal).
  ///
  /// Only touches mid-alpha boundary fringe. Solid grey/silver letter fills
  /// (opaque interiors) are left alone.
  static img.Image hardenFlatEdges(img.Image source) {
    final image = source.numChannels == 4
        ? source.clone()
        : source.convert(numChannels: 4);
    final w = image.width;
    final h = image.height;
    if (w < 4 || h < 4) return image;

    final out = image.clone();
    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        final p = image.getPixel(x, y);
        final a = p.a.toInt();
        if (a < 40 || a >= 240) continue;

        var sr = 0, sg = 0, sb = 0, n = 0;
        var nearEmpty = false;
        for (var dy = -1; dy <= 1; dy++) {
          for (var dx = -1; dx <= 1; dx++) {
            if (dx == 0 && dy == 0) continue;
            final nx = x + dx, ny = y + dy;
            if (nx < 0 || ny < 0 || nx >= w || ny >= h) {
              nearEmpty = true;
              continue;
            }
            final q = image.getPixel(nx, ny);
            if (q.a.toInt() < 80) {
              nearEmpty = true;
              continue;
            }
            if (q.a.toInt() < 200) continue;
            final qr = q.r.toInt(), qg = q.g.toInt(), qb = q.b.toInt();
            final qsat =
                math.max(qr, math.max(qg, qb)) - math.min(qr, math.min(qg, qb));
            final qlum = (qr + qg + qb) / 3.0;
            if (qsat < 24 && qlum > 40 && qlum < 230) continue;
            sr += qr;
            sg += qg;
            sb += qb;
            n++;
          }
        }
        if (n == 0) continue;
        if (!nearEmpty && a >= 200) continue;
        out.setPixelRgba(x, y, sr ~/ n, sg ~/ n, sb ~/ n, a < 220 ? 255 : a);
      }
    }
    return out;
  }

  /// True when [source] has a dark stroke hugging chromatic fills (not a plate).
  static ({int r, int g, int b, double widthFrac})? detectLetterOutline(
    img.Image source,
  ) {
    final image = source.numChannels == 4 ? source : source.convert(numChannels: 4);
    final w = image.width;
    final h = image.height;
    if (w < 8 || h < 8) return null;
    var fillN = 0;
    var ringN = 0;
    var sr = 0, sg = 0, sb = 0;
    bool isBg(img.Pixel p) {
      if (p.a.toInt() < 40) return true;
      final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
      return r + g + b >= 720 && (r - g).abs() < 36 && (r - b).abs() < 36;
    }

    bool isFill(img.Pixel p) {
      if (p.a.toInt() < 40 || isBg(p)) return false;
      final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
      final lum = (r + g + b) / 3.0;
      final sat = math.max(r, math.max(g, b)) - math.min(r, math.min(g, b));
      return sat > 40 && lum > 55;
    }

    bool isDark(img.Pixel p) {
      if (p.a.toInt() < 40 || isBg(p)) return false;
      final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
      final lum = (r + g + b) / 3.0;
      final sat = math.max(r, math.max(g, b)) - math.min(r, math.min(g, b));
      return lum < 70 && sat < 55;
    }

    for (var y = 1; y < h - 1; y++) {
      for (var x = 1; x < w - 1; x++) {
        final p = image.getPixel(x, y);
        if (isFill(p)) fillN++;
        if (!isDark(p)) continue;
        var nextFill = false;
        for (var dy = -1; dy <= 1; dy++) {
          for (var dx = -1; dx <= 1; dx++) {
            if (dx == 0 && dy == 0) continue;
            if (isFill(image.getPixel(x + dx, y + dy))) nextFill = true;
          }
        }
        if (nextFill) {
          ringN++;
          sr += p.r.toInt();
          sg += p.g.toInt();
          sb += p.b.toInt();
        }
      }
    }
    if (ringN < 16 || fillN < 30) return null;
    if (ringN < fillN * 0.03) return null;
    var r = (sr / ringN).round();
    var g = (sg / ringN).round();
    var b = (sb / ringN).round();
    // JPEG black outlines read as navy/brown; lockup strokes are ink black.
    if ((r + g + b) / 3 < 90) {
      r = 0;
      g = 0;
      b = 0;
    }
    return (
      r: r.clamp(0, 255),
      g: g.clamp(0, 255),
      b: b.clamp(0, 255),
      widthFrac: (2.4 / math.min(w, h)).clamp(0.006, 0.03),
    );
  }

  /// Paint a clean even stroke when the source lockup had one and the restore
  /// dropped it (Gemini often treats black borders as background).
  static img.Image ensureLetterOutline(img.Image source, img.Image restored) {
    final hint = detectLetterOutline(source);
    if (hint == null) return restored;
    if (detectLetterOutline(restored) != null) return restored;

    final image = restored.numChannels == 4
        ? restored.clone()
        : restored.convert(numChannels: 4);
    final w = image.width;
    final h = image.height;
    final radius = math.max(2, (math.min(w, h) * hint.widthFrac).round());
    final fill = List<bool>.filled(w * h, false);
    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        final p = image.getPixel(x, y);
        if (p.a.toInt() < 40) continue;
        final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
        final lum = (r + g + b) / 3.0;
        final sat = math.max(r, math.max(g, b)) - math.min(r, math.min(g, b));
        if (sat > 40 && lum > 55) fill[y * w + x] = true;
      }
    }
    var dilate = List<bool>.from(fill);
    for (var pass = 0; pass < radius; pass++) {
      final next = List<bool>.from(dilate);
      for (var y = 1; y < h - 1; y++) {
        for (var x = 1; x < w - 1; x++) {
          final i = y * w + x;
          if (dilate[i]) continue;
          if (dilate[i - 1] ||
              dilate[i + 1] ||
              dilate[i - w] ||
              dilate[i + w]) {
            next[i] = true;
          }
        }
      }
      dilate = next;
    }
    for (var i = 0; i < w * h; i++) {
      if (!dilate[i] || fill[i]) continue;
      image.setPixelRgba(i % w, i ~/ w, hint.r, hint.g, hint.b, 255);
    }
    return image;
  }

  /// Rebuild lettering / shapes with predicted smooth edges.
  ///
  /// Classifies ink into a few solid colors, upsamples each mask with a
  /// blur-threshold so stair-stepped JPEG edges become curves, then paints
  /// dark outline first and fills after.
  static img.Image rebuildPredictedEdges(img.Image source, {int? targetHeight}) {
    var src = source.numChannels == 4
        ? source.clone()
        : source.convert(numChannels: 4);
    const maxWork = 400;
    if (math.max(src.width, src.height) > maxWork) {
      final scale = maxWork / math.max(src.width, src.height);
      src = img.copyResize(
        src,
        width: math.max(1, (src.width * scale).round()),
        height: math.max(1, (src.height * scale).round()),
        interpolation: img.Interpolation.average,
      );
    }
    final sw = src.width;
    final sh = src.height;
    if (sw < 4 || sh < 4) return src;

    final samples = <List<int>>[];
    for (var y = 0; y < sh; y++) {
      for (var x = 0; x < sw; x++) {
        final p = src.getPixel(x, y);
        if (p.a.toInt() < 40) continue;
        samples.add([p.r.toInt(), p.g.toInt(), p.b.toInt(), x, y]);
      }
    }
    if (samples.length < 40) return src;

    var whiteN = 0, blackN = 0;
    for (final s in samples) {
      final sat = math.max(s[0], math.max(s[1], s[2])) -
          math.min(s[0], math.min(s[1], s[2]));
      final lum = (s[0] + s[1] + s[2]) / 3.0;
      if (lum >= 232 && sat <= 36) whiteN++;
      if (lum <= 40 && sat <= 16) blackN++;
    }
    final skipWhitePlate = whiteN / samples.length > 0.35;
    final skipBlackPlate = blackN / samples.length > 0.35;
    if (skipWhitePlate || skipBlackPlate) {
      samples.removeWhere((s) {
        final sat = math.max(s[0], math.max(s[1], s[2])) -
            math.min(s[0], math.min(s[1], s[2]));
        final lum = (s[0] + s[1] + s[2]) / 3.0;
        if (skipWhitePlate && lum >= 232 && sat <= 36) return true;
        if (skipBlackPlate && lum <= 40 && sat <= 16) return true;
        return false;
      });
    }
    if (samples.length < 40) return src;

    final k = math.min(4, math.max(2, samples.length ~/ 80));
    final centers = <List<int>>[];
    for (var i = 0; i < k; i++) {
      final s = samples[(i * samples.length) ~/ k];
      centers.add([s[0], s[1], s[2]]);
    }
    for (var iter = 0; iter < 8; iter++) {
      final sums = List.generate(k, (_) => [0, 0, 0, 0]);
      for (final s in samples) {
        var best = 0;
        var bestD = 1 << 30;
        for (var c = 0; c < k; c++) {
          final d = _colorDistance(s[0], s[1], s[2], centers[c][0], centers[c][1], centers[c][2]);
          if (d < bestD) {
            bestD = d;
            best = c;
          }
        }
        sums[best][0] += s[0];
        sums[best][1] += s[1];
        sums[best][2] += s[2];
        sums[best][3] += 1;
      }
      for (var c = 0; c < k; c++) {
        if (sums[c][3] == 0) continue;
        centers[c][0] = (sums[c][0] / sums[c][3]).round();
        centers[c][1] = (sums[c][1] / sums[c][3]).round();
        centers[c][2] = (sums[c][2] / sums[c][3]).round();
      }
    }

    final order = List<int>.generate(k, (i) => i)
      ..sort((a, b) =>
          (centers[a][0] + centers[a][1] + centers[a][2])
              .compareTo(centers[b][0] + centers[b][1] + centers[b][2]));

    final th = targetHeight ?? math.max(sh, 3000);
    final tw = math.max(1, (sw * th / sh).round());
    final out = img.Image(width: tw, height: th, numChannels: 4);
    img.fill(out, color: img.ColorRgba8(0, 0, 0, 0));

    for (final ci in order) {
      final mask = img.Image(width: sw, height: sh, numChannels: 4);
      img.fill(mask, color: img.ColorRgba8(0, 0, 0, 0));
      var count = 0;
      for (final s in samples) {
        var best = 0;
        var bestD = 1 << 30;
        for (var c = 0; c < k; c++) {
          final d = _colorDistance(s[0], s[1], s[2], centers[c][0], centers[c][1], centers[c][2]);
          if (d < bestD) {
            bestD = d;
            best = c;
          }
        }
        if (best != ci) continue;
        mask.setPixelRgba(s[3], s[4], 255, 255, 255, 255);
        count++;
      }
      if (count < 20) continue;
      var scaled = img.copyResize(
        mask,
        width: tw,
        height: th,
        interpolation: img.Interpolation.cubic,
      );
      final cr = centers[ci][0];
      final cg = centers[ci][1];
      final cb = centers[ci][2];
      for (var y = 0; y < th; y++) {
        for (var x = 0; x < tw; x++) {
          final cov = scaled.getPixel(x, y).r.toInt();
          if (cov <= out.getPixel(x, y).a.toInt()) continue;
          out.setPixelRgba(x, y, cr, cg, cb, cov);
        }
      }
    }
    return out;
  }

  /// Apply [options]: optional manual crop, bg removal, auto margin trim.
  static Uint8List processWithOptions(
    Uint8List input,
    LogoImportOptions options,
  ) {
    // True leave-as-is: no crop and no background strip.
    if (options.cropMode == LogoCropMode.none && !options.removeBackground) {
      return input;
    }

    var image = decodeToRgba(input);
    if (image == null) return input;

    if (options.cropMode == LogoCropMode.manual &&
        options.manualCropRect != null) {
      image = _applyManualCrop(image, options.manualCropRect!);
    }

    final removingBackground =
        options.removeBackground && !_hasMeaningfulTransparency(image);
    (int, int, int)? plateForHoles;
    if (removingBackground) {
      plateForHoles = _estimateBackgroundColor(image);
      _knockOutOuterPlate(image);
      recoverHueConsistentChroma(image);
      stripForeignMarks(image);
      stripHaloFringe(image, protectStrokeFills: true);
    }

    if (options.cropMode == LogoCropMode.auto) {
      image = _cropToContentBBox(image, null, minAlpha: 48);
      image = _trimLowCoverageMargins(image);
      image = addSafePad(image);
      if (removingBackground) {
        // Cropping/padding can newly enclose plate remnants that were still
        // edge-connected (and so skipped) before the bounding box tightened
        // — re-punch once more on the final bitmap, same as
        // normalizeToVisibleContent, so corners don't come out with an
        // opaque plate smudge.
        _punchEnclosedPlateHoles(image, plateForHoles ?? (255, 255, 255));
      }
      return Uint8List.fromList(img.encodePng(image));
    }

    return Uint8List.fromList(img.encodePng(image));
  }

  /// Prepare a saved/drawn signature for BOL embed: knock out white/light
  /// paper background to transparent ink, then crop to the stroke bounds.
  ///
  /// Without this, a large white PNG canvas is fit into a tiny signature cell
  /// and the ink looks microscopic while the white plate covers other fields.
  static Uint8List prepareSignatureInk(Uint8List input) {
    if (input.isEmpty) return input;
    final decoded = img.decodeImage(input);
    if (decoded == null) return input;

    var image = decoded.numChannels == 4
        ? decoded.clone()
        : decoded.convert(numChannels: 4);

    final w = image.width;
    final h = image.height;
    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        final p = image.getPixel(x, y);
        final r = p.r.toInt();
        final g = p.g.toInt();
        final b = p.b.toInt();
        final a = p.a.toInt();
        if (a < alphaThreshold) {
          image.setPixelRgba(x, y, 0, 0, 0, 0);
          continue;
        }
        // Darkness of the stroke (white paper → 0, black ink → 255).
        final ink = (255 - ((r + g + b) / 3.0)).round().clamp(0, 255);
        if (ink < 18) {
          image.setPixelRgba(x, y, 0, 0, 0, 0);
        } else {
          // Solid black ink with alpha from darkness (anti-aliased edges).
          final outA = (ink * a / 255.0).round().clamp(0, 255);
          image.setPixelRgba(x, y, 0, 0, 0, outA);
        }
      }
    }

    image = _cropToContentBBox(image, null, minAlpha: 24);
    image = _addPadding(image, fraction: 0.04, minPad: 2, maxPad: 8);
    return Uint8List.fromList(img.encodePng(image));
  }

  static img.Image _applyManualCrop(img.Image image, Rect normalized) {
    final left = (normalized.left * image.width).round().clamp(0, image.width - 1);
    final top = (normalized.top * image.height).round().clamp(0, image.height - 1);
    final right =
        (normalized.right * image.width).round().clamp(left + 1, image.width);
    final bottom =
        (normalized.bottom * image.height).round().clamp(top + 1, image.height);
    return img.copyCrop(
      image,
      x: left,
      y: top,
      width: right - left,
      height: bottom - top,
    );
  }

  /// True when border pixels are already mostly transparent (good PNG).
  static bool hasMeaningfulTransparency(Uint8List png) {
    final image = decodeToRgba(png);
    if (image == null) return false;
    return _hasMeaningfulTransparency(image);
  }

  /// True when border pixels are already mostly transparent (good PNG).
  static bool _hasMeaningfulTransparency(img.Image image) {
    final w = image.width;
    final h = image.height;
    if (w < 2 || h < 2) return false;

    var transparent = 0;
    var total = 0;

    void sample(int x, int y) {
      final a = image.getPixel(x, y).a.toInt();
      total++;
      if (a < alphaThreshold) transparent++;
    }

    for (var x = 0; x < w; x++) {
      sample(x, 0);
      sample(x, h - 1);
    }
    for (var y = 1; y < h - 1; y++) {
      sample(0, y);
      sample(w - 1, y);
    }

    return total > 0 && transparent / total >= 0.35;
  }

  /// Edge-flood the outer canvas only. Letter fills — including dark/grey
  /// ink on a dark plate and black bodies on a light plate — stay opaque.
  ///
  /// Paint-like for phone photos / speckled gray plates: wider plate match,
  /// then a second pass that punches remaining low-chroma plate islands.
  static void _knockOutOuterPlate(img.Image image) {
    _removeCheckerboardMatte(image);
    final plate = _estimateBackgroundColor(image);
    final protect = _protectedInkMask(image, plate);
    if (plate != null) {
      final dark = _isNearBlackCanvas(plate.$1, plate.$2, plate.$3);
      _removeSolidBackground(
        image,
        plate,
        eightWay: !dark,
        protect: protect,
        floodTolerance: _plateFloodTolerance(plate),
      );
    } else {
      _removeSolidBackground(
        image,
        (255, 255, 255),
        eightWay: true,
        protect: protect,
        floodTolerance: 40,
      );
    }
    // When corners disagree (thin dark frame, checker matte) still punch
    // enclosed near-white counters (O/B/D/P). Large white fields stay —
    // they exceed the 22% ink cap.
    _punchEnclosedPlateHoles(image, plate ?? (255, 255, 255));
    _stripJpegWhiteRim(image);
    _stripTexturedPlateRemnants(image, plate);
  }

  /// Speckled / JPEG gray plates need a wider flood than flat studio white.
  static int _plateFloodTolerance((int r, int g, int b) plate) {
    final sat =
        math.max(plate.$1, math.max(plate.$2, plate.$3)) -
        math.min(plate.$1, math.min(plate.$2, plate.$3));
    final lum = (plate.$1 + plate.$2 + plate.$3) / 3.0;
    if (_isNearBlackCanvas(plate.$1, plate.$2, plate.$3)) return 14;
    // Near-white studio plates.
    if (lum >= 230 && sat <= 20) return 38;
    // Mid-gray photo / desk / paper texture (Inked Energy style).
    if (sat <= 35 && lum >= 140 && lum <= 230) return 48;
    return colorTolerance + 10;
  }

  /// Punch leftover JPEG-white rims that the plate flood left attached to ink.
  /// Light-grey / silver letter bodies (lum 80–200) are not JPEG white.
  static void _stripJpegWhiteRim(img.Image image) {
    final w = image.width;
    final h = image.height;
    if (w < 4 || h < 4) return;
    final punch = Uint8List(w * h);
    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        final p = image.getPixel(x, y);
        final a = p.a.toInt();
        if (a < 40) continue;
        final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
        final sat = math.max(r, math.max(g, b)) - math.min(r, math.min(g, b));
        final lum = (r + g + b) / 3.0;
        // Pure white letter outlines (GCM etc.) stay; only off-white JPEG rims.
        if (lum < 220 || lum >= 248 || sat > 20) continue;
        var emptyN = 0;
        var inkN = 0;
        for (var dy = -1; dy <= 1; dy++) {
          for (var dx = -1; dx <= 1; dx++) {
            if (dx == 0 && dy == 0) continue;
            final nx = x + dx;
            final ny = y + dy;
            if (nx < 0 || ny < 0 || nx >= w || ny >= h) {
              emptyN++;
              continue;
            }
            final n = image.getPixel(nx, ny);
            if (n.a.toInt() < 40) {
              emptyN++;
            } else {
              inkN++;
            }
          }
        }
        if (emptyN > 0 && inkN <= 6) punch[y * w + x] = 1;
      }
    }
    for (var i = 0; i < punch.length; i++) {
      if (punch[i] == 0) continue;
      image.setPixelRgba(i % w, i ~/ w, 0, 0, 0, 0);
    }
  }

  /// Pixels that differ from the outer plate, dilated one pixel so
  /// anti-aliased rims are not eaten by the edge flood.
  ///
  /// On light / textured plates, keep protect tight so speckled gray is not
  /// treated as ink (that blocked Paint-style knockout for Inked Energy).
  static Uint8List _protectedInkMask(
    img.Image image,
    (int r, int g, int b)? plate,
  ) {
    final w = image.width;
    final h = image.height;
    final raw = Uint8List(w * h);
    final plateTol = plate == null ? colorTolerance : _plateFloodTolerance(plate);
    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        final p = image.getPixel(x, y);
        if (p.a.toInt() < alphaThreshold) continue;
        final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
        if (plate != null) {
          final d = _colorDistance(r, g, b, plate.$1, plate.$2, plate.$3);
          if (d <= plateTol) continue;
          final sat =
              math.max(r, math.max(g, b)) - math.min(r, math.min(g, b));
          final lum = (r + g + b) / 3.0;
          // Speckled plate mush near plate luma — not protectable ink.
          if (sat <= 28 && lum >= 130 && (lum - (plate.$1 + plate.$2 + plate.$3) / 3.0).abs() <= 40) {
            continue;
          }
        }
        raw[y * w + x] = 1;
      }
    }
    var dilated = _dilateMask(raw, w, h);
    // Dilation must not paint protect onto plate-fill. That 1px shell around
    // letters stayed after the flood and 8-way-joined counters to any
    // border-touching leftover, so `_punchEnclosedPlateHoles` skipped O/B/D/P.
    if (plate != null) {
      for (var y = 0; y < h; y++) {
        for (var x = 0; x < w; x++) {
          final i = y * w + x;
          if (dilated[i] == 0) continue;
          if (_isPlateFill(image.getPixel(x, y), plate)) dilated[i] = 0;
        }
      }
    }
    final centerline = centerlineProtectMask(image);
    if (centerline == null) return dilated;
    for (var i = 0; i < dilated.length; i++) {
      if (centerline[i] != 0) dilated[i] = 1;
    }
    return dilated;
  }

  static Uint8List _dilateMask(Uint8List src, int w, int h) {
    final out = Uint8List.fromList(src);
    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        if (src[y * w + x] == 0) continue;
        for (var dy = -1; dy <= 1; dy++) {
          for (var dx = -1; dx <= 1; dx++) {
            final nx = x + dx;
            final ny = y + dy;
            if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
            out[ny * w + nx] = 1;
          }
        }
      }
    }
    return out;
  }

  static void _removeSolidBackground(
    img.Image image,
    (int r, int g, int b) bg, {
    bool eightWay = false,
    Uint8List? protect,
    int? floodTolerance,
  }) {
    final w = image.width;
    final h = image.height;
    final visited = Uint8List(w * h);
    final queue = <(int, int)>[];
    final tol = floodTolerance ?? colorTolerance;

    void trySeed(int x, int y) {
      if (x < 0 || y < 0 || x >= w || y >= h) return;
      final idx = y * w + x;
      if (visited[idx] != 0) return;
      if (protect != null && protect[idx] != 0) return;
      if (!_isEmptyPixel(image.getPixel(x, y), bg, tolerance: tol)) return;
      visited[idx] = 1;
      queue.add((x, y));
    }

    for (var x = 0; x < w; x++) {
      trySeed(x, 0);
      trySeed(x, h - 1);
    }
    for (var y = 0; y < h; y++) {
      trySeed(0, y);
      trySeed(w - 1, y);
    }

    while (queue.isNotEmpty) {
      final (x, y) = queue.removeLast();
      image.setPixelRgba(x, y, 0, 0, 0, 0);

      trySeed(x + 1, y);
      trySeed(x - 1, y);
      trySeed(x, y + 1);
      trySeed(x, y - 1);
      if (eightWay) {
        trySeed(x + 1, y + 1);
        trySeed(x + 1, y - 1);
        trySeed(x - 1, y + 1);
        trySeed(x - 1, y - 1);
      }
    }
  }

  /// Second pass after edge flood: punch remaining low-chroma plate islands
  /// (textured desk/photo backgrounds Paint removes in one click).
  static void _stripTexturedPlateRemnants(
    img.Image image,
    (int r, int g, int b)? plate,
  ) {
    final w = image.width;
    final h = image.height;
    if (w < 8 || h < 8) return;

    final pr = plate?.$1 ?? 245;
    final pg = plate?.$2 ?? 245;
    final pb = plate?.$3 ?? 245;
    final plateLum = (pr + pg + pb) / 3.0;
    final plateSat =
        math.max(pr, math.max(pg, pb)) - math.min(pr, math.min(pg, pb));
    // Only run for light / mid-gray plates — not black canvases.
    if (plateLum < 120) return;

    final tol = _plateFloodTolerance((pr, pg, pb));
    final punch = Uint8List(w * h);

    bool looksLikePlate(int r, int g, int b) {
      final sat = math.max(r, math.max(g, b)) - math.min(r, math.min(g, b));
      final lum = (r + g + b) / 3.0;
      if (sat > 40) return false; // keep chromatic brand ink
      if (lum < 55) return false; // keep dark marks
      final d = _colorDistance(r, g, b, pr, pg, pb);
      if (d <= tol) return true;
      // Speckled mid-gray near the plate luma.
      if (plateSat <= 40 && sat <= 28 && (lum - plateLum).abs() <= 36) {
        return true;
      }
      if (lum >= 225 && sat <= 24) return true;
      return false;
    }

    bool nearStrongInk(int x, int y) {
      for (var dy = -2; dy <= 2; dy++) {
        for (var dx = -2; dx <= 2; dx++) {
          final nx = x + dx;
          final ny = y + dy;
          if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
          final p = image.getPixel(nx, ny);
          if (p.a.toInt() < 80) continue;
          final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
          final sat =
              math.max(r, math.max(g, b)) - math.min(r, math.min(g, b));
          final lum = (r + g + b) / 3.0;
          if (sat >= 36) return true;
          if (lum <= 70 && sat <= 40) return true;
        }
      }
      return false;
    }

    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        final p = image.getPixel(x, y);
        if (p.a.toInt() < 40) continue;
        final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
        if (!looksLikePlate(r, g, b)) continue;
        // Preserve AA rims hugging real ink.
        if (nearStrongInk(x, y)) continue;
        punch[y * w + x] = 1;
      }
    }

    // Only keep punches that touch transparent / border (outer leftovers).
    final keep = Uint8List(w * h);
    final q = <int>[];
    for (var x = 0; x < w; x++) {
      for (final y in [0, h - 1]) {
        final i = y * w + x;
        if (punch[i] != 0) {
          keep[i] = 1;
          q.add(i);
        }
      }
    }
    for (var y = 0; y < h; y++) {
      for (final x in [0, w - 1]) {
        final i = y * w + x;
        if (punch[i] != 0 && keep[i] == 0) {
          keep[i] = 1;
          q.add(i);
        }
      }
    }
    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        final i = y * w + x;
        if (punch[i] == 0 || keep[i] != 0) continue;
        var touchEmpty = false;
        for (final (dx, dy) in [(1, 0), (-1, 0), (0, 1), (0, -1)]) {
          final nx = x + dx, ny = y + dy;
          if (nx < 0 || ny < 0 || nx >= w || ny >= h) {
            touchEmpty = true;
            break;
          }
          if (image.getPixel(nx, ny).a.toInt() < 40) {
            touchEmpty = true;
            break;
          }
        }
        if (touchEmpty) {
          keep[i] = 1;
          q.add(i);
        }
      }
    }
    while (q.isNotEmpty) {
      final i = q.removeLast();
      final x = i % w;
      final y = i ~/ w;
      for (final (dx, dy) in [(1, 0), (-1, 0), (0, 1), (0, -1)]) {
        final nx = x + dx, ny = y + dy;
        if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
        final ni = ny * w + nx;
        if (punch[ni] == 0 || keep[ni] != 0) continue;
        keep[ni] = 1;
        q.add(ni);
      }
    }

    for (var i = 0; i < keep.length; i++) {
      if (keep[i] == 0) continue;
      image.setPixelRgba(i % w, i ~/ w, 0, 0, 0, 0);
    }
  }

  /// Knock out letter counters / gaps that stayed filled with the canvas color
  /// after the outer plate flood (holes in b/d, white counters, gray plates).
  /// Does not strip brand strokes that sit against already-transparent canvas.
  static void _punchEnclosedPlateHoles(
    img.Image image,
    (int r, int g, int b)? plate,
  ) {
    final w = image.width;
    final h = image.height;
    if (w < 8 || h < 8) return;

    final seen = Uint8List(w * h);
    var inkCount = 0;
    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        final p = image.getPixel(x, y);
        if (p.a.toInt() < 80) continue;
        if (_isPlateFill(p, plate)) continue;
        inkCount++;
      }
    }
    if (inkCount < 40) return;

    const dirs = [
      (1, 0),
      (-1, 0),
      (0, 1),
      (0, -1),
      (1, 1),
      (1, -1),
      (-1, 1),
      (-1, -1),
    ];

    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        final start = y * w + x;
        if (seen[start] != 0) continue;
        final seed = image.getPixel(x, y);
        if (seed.a.toInt() < alphaThreshold) continue;
        if (!_isPlateFill(seed, plate)) continue;

        final comp = <int>[];
        final queue = <int>[start];
        seen[start] = 1;
        var touchesBorder = false;
        var minX = w, minY = h, maxX = 0, maxY = 0;
        while (queue.isNotEmpty) {
          final i = queue.removeLast();
          comp.add(i);
          final cx = i % w;
          final cy = i ~/ w;
          if (cx < minX) minX = cx;
          if (cy < minY) minY = cy;
          if (cx > maxX) maxX = cx;
          if (cy > maxY) maxY = cy;
          if (cx == 0 || cy == 0 || cx == w - 1 || cy == h - 1) {
            touchesBorder = true;
          }
          for (final d in dirs) {
            final nx = cx + d.$1;
            final ny = cy + d.$2;
            if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
            final ni = ny * w + nx;
            if (seen[ni] != 0) continue;
            final np = image.getPixel(nx, ny);
            if (np.a.toInt() < alphaThreshold) continue;
            if (!_isPlateFill(np, plate)) continue;
            seen[ni] = 1;
            queue.add(ni);
          }
        }
        if (touchesBorder) continue;
        // Letter counters, not 1px JPEG crumbs. Matches Python punch.
        if (comp.length < 8) continue;
        // Counters are small vs the wordmark; skip large brand fills.
        if (comp.length > inkCount * 0.22) continue;
        final bw = maxX - minX + 1;
        final bh = maxY - minY + 1;
        if (bw <= 0 || bh <= 0) continue;
        // Thin highlight strokes (mountain snow, underlines) are elongated.
        if (bw > bh * 3.5 || bh > bw * 3.5) continue;
        final compactness = comp.length / (bw * bh);
        if (compactness < 0.32) continue;

        var inkN = 0;
        var clearN = 0;
        for (final i in comp) {
          final cx = i % w;
          final cy = i ~/ w;
          for (final d in dirs) {
            final nx = cx + d.$1;
            final ny = cy + d.$2;
            if (nx < 0 || ny < 0 || nx >= w || ny >= h) {
              clearN++;
              continue;
            }
            final p = image.getPixel(nx, ny);
            if (p.a.toInt() < 80) {
              clearN++;
              continue;
            }
            if (_isPlateFill(p, plate)) continue;
            inkN++;
          }
        }
        final boundary = inkN + clearN;
        if (boundary == 0) continue;
        if (inkN < boundary * 0.7 || inkN <= clearN) continue;

        for (final i in comp) {
          image.setPixelRgba(i % w, i ~/ w, 0, 0, 0, 0);
        }
      }
    }
  }

  /// Plate / canvas fill: only the estimated outer plate hue.
  /// Do not treat every near-black or near-white pixel as plate — that
  /// strips brand snow lines, black wordmarks, and interior highlights.
  static bool _isPlateFill(img.Pixel pixel, (int r, int g, int b)? plate) {
    if (plate == null) return false;
    final r = pixel.r.toInt();
    final g = pixel.g.toInt();
    final b = pixel.b.toInt();
    final d = _colorDistance(r, g, b, plate.$1, plate.$2, plate.$3);
    if (_isNearBlackCanvas(plate.$1, plate.$2, plate.$3)) {
      return _isNearBlackCanvas(r, g, b) && d <= 14;
    }
    return d <= colorTolerance;
  }

  /// Sample corner regions; return a shared bg color when corners agree.
  static (int r, int g, int b)? _estimateBackgroundColor(img.Image image) {
    final w = image.width;
    final h = image.height;
    final samples = <img.Pixel>[];

    void corner(int cx, int cy) {
      final radius = math.min(3, math.min(w, h));
      for (var dy = 0; dy < radius; dy++) {
        for (var dx = 0; dx < radius; dx++) {
          final x = (cx + dx).clamp(0, w - 1);
          final y = (cy + dy).clamp(0, h - 1);
          final p = image.getPixel(x, y);
          if (p.a.toInt() < alphaThreshold) continue;
          samples.add(p);
        }
      }
    }

    corner(0, 0);
    corner(w - 3, 0);
    corner(0, h - 3);
    corner(w - 3, h - 3);

    if (samples.isEmpty) return null;

    final rs = samples.map((p) => p.r.toInt()).toList()..sort();
    final gs = samples.map((p) => p.g.toInt()).toList()..sort();
    final bs = samples.map((p) => p.b.toInt()).toList()..sort();
    final mid = samples.length ~/ 2;
    final mr = rs[mid];
    final mg = gs[mid];
    final mb = bs[mid];

    var agree = 0;
    for (final p in samples) {
      if (_colorDistance(p.r.toInt(), p.g.toInt(), p.b.toInt(), mr, mg, mb) <=
          colorTolerance) {
        agree++;
      }
    }
    if (agree / samples.length < 0.75) return null;

    return (mr, mg, mb);
  }

  /// Empty = transparent, matches known flat background, or near-white padding.
  ///
  /// Black canvases only match **achromatic** near-black pixels. Dark brand
  /// greens/blues (high saturation) must never be treated as the plate — that
  /// used to leave only a bright accent (e.g. orange swoosh) which then scaled
  /// up to Swift height and looked huge / cut-off on the PDF.
  static bool _isEmptyPixel(
    img.Pixel pixel,
    (int r, int g, int b)? bg, {
    int? tolerance,
  }) {
    if (pixel.a.toInt() < alphaThreshold) return true;
    final r = pixel.r.toInt();
    final g = pixel.g.toInt();
    final b = pixel.b.toInt();
    final tol = tolerance ?? colorTolerance;
    if (bg == null) {
      return r >= 240 && g >= 240 && b >= 240;
    }
    final bgBlack = _isNearBlackCanvas(bg.$1, bg.$2, bg.$3);
    if (bgBlack) {
      // Dark letter fills and metallic rims are ink, not canvas.
      if (!_isNearBlackCanvas(r, g, b)) return false;
      return _colorDistance(r, g, b, bg.$1, bg.$2, bg.$3) <= 14;
    }
    final sat = math.max(r, math.max(g, b)) - math.min(r, math.min(g, b));
    final lum = (r + g + b) / 3.0;
    // JPEG ringing / textured light plate — Paint-style near-white knockout.
    if (lum >= 200 && sat <= 28) return true;
    if (r >= 248 && g >= 248 && b >= 248) return true;
    return _colorDistance(r, g, b, bg.$1, bg.$2, bg.$3) <= tol;
  }

  /// Near-black canvas (not a dark brand fill): low luminance and low saturation.
  static bool _isGreyFill(int r, int g, int b) {
    final sat = math.max(r, math.max(g, b)) - math.min(r, math.min(g, b));
    return sat < 28;
  }

  static bool _isNearBlackCanvas(int r, int g, int b) {
    final sat = math.max(r, math.max(g, b)) - math.min(r, math.min(g, b));
    final lum = (r + g + b) / 3.0;
    return lum <= 40 && sat <= 16;
  }

  static int _colorDistance(int r1, int g1, int b1, int r2, int g2, int b2) {
    return math.max(
      (r1 - r2).abs(),
      math.max((g1 - g2).abs(), (b1 - b2).abs()),
    );
  }

  /// Dominant brand fills (chromatic and grey/silver). Plate pixels omitted.
  static List<({int r, int g, int b, double fraction})> dominantBrandColors(
    Uint8List png, {
    double minFraction = 0.06,
  }) {
    final image = img.decodeImage(png);
    if (image == null) return const [];
    // After knockout the corners are transparent pad sitting on ink, not a
    // new plate. Estimating a plate from those ink corners would skip black
    // script (Flexitallic) as "background".
    final plate = _hasMeaningfulTransparency(image)
        ? null
        : _estimateBackgroundColor(image);
    final plateLight = plate != null &&
        plate.$1 >= 230 &&
        plate.$2 >= 230 &&
        plate.$3 >= 230;
    var ink = 0;
    final buckets = <int, int>{};
    for (var y = 0; y < image.height; y++) {
      for (var x = 0; x < image.width; x++) {
        final p = image.getPixel(x, y);
        if (p.a.toInt() < 96) continue;
        final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
        if (plate != null &&
            _colorDistance(r, g, b, plate.$1, plate.$2, plate.$3) <= 18) {
          continue;
        }
        if (plateLight && r >= 240 && g >= 240 && b >= 240) continue;
        if (plate == null && r >= 240 && g >= 240 && b >= 240) continue;
        final sat = math.max(r, math.max(g, b)) - math.min(r, math.min(g, b));
        final lum = (r + g + b) / 3.0;
        final chromatic = sat >= 22;
        final greyFill = !chromatic && lum >= 48 && lum <= 230;
        // Black script on a knocked-out / light plate is brand ink. Do not
        // require the source canvas to still be an opaque white JPEG.
        final blackMark = _isNearBlackCanvas(r, g, b) &&
            (plate == null ||
                plateLight ||
                !_isNearBlackCanvas(plate.$1, plate.$2, plate.$3));
        if (!chromatic && !greyFill && !blackMark) continue;
        ink++;
        final key = ((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4);
        buckets[key] = (buckets[key] ?? 0) + 1;
      }
    }
    if (ink < 40 || buckets.isEmpty) return const [];
    final ranked = buckets.entries.toList()
      ..sort((a, b) => b.value.compareTo(a.value));
    final out = <({int r, int g, int b, double fraction})>[];
    for (final e in ranked.take(6)) {
      final fraction = e.value / ink;
      if (fraction < minFraction) break;
      final key = e.key;
      out.add((
        r: ((key >> 8) & 0xf) * 16 + 8,
        g: ((key >> 4) & 0xf) * 16 + 8,
        b: (key & 0xf) * 16 + 8,
        fraction: fraction,
      ));
    }
    return out;
  }

  /// True when [result] still carries the significant brand fills from [source].
  static bool retainsBrandColors(Uint8List source, Uint8List result) {
    final needed = dominantBrandColors(source);
    if (needed.isEmpty) return true;
    final present = dominantBrandColors(result, minFraction: 0.02);
    if (present.isEmpty) return false;
    var hits = 0;
    for (final n in needed) {
      final ok = present.any(
        (p) => _colorDistance(n.r, n.g, n.b, p.r, p.g, p.b) <= 56,
      );
      if (ok) hits++;
    }
    // Keep every major fill when the source has 2+ (wordmark + accent).
    if (needed.length >= 2) return hits >= needed.length;
    return hits >= 1;
  }

  /// Transparent border so flush-cropped marks are not hard against PDF rules.
  static img.Image addSafePad(img.Image image, {double fraction = 0.04}) {
    if (image.width < 4 || image.height < 4) return image;
    final pad = math
        .max(
          2,
          (math.min(image.width, image.height) * fraction).round(),
        )
        .clamp(2, 48);
    final out = img.Image(
      width: image.width + pad * 2,
      height: image.height + pad * 2,
      numChannels: 4,
    );
    img.fill(out, color: img.ColorRgba8(0, 0, 0, 0));
    img.compositeImage(out, image, dstX: pad, dstY: pad);
    return out;
  }

  /// Opaque ink AABB, or null when the bitmap is empty.
  static (int minX, int minY, int maxX, int maxY)? _opaqueInkBounds(
    img.Image image, {
    int minAlpha = 40,
  }) {
    var minX = image.width;
    var minY = image.height;
    var maxX = -1;
    var maxY = -1;
    for (var y = 0; y < image.height; y++) {
      for (var x = 0; x < image.width; x++) {
        final p = image.getPixel(x, y);
        if (p.a.toInt() < minAlpha) continue;
        final r = p.r.toInt(), g = p.g.toInt(), b = p.b.toInt();
        if (r >= 250 && g >= 250 && b >= 250) continue;
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
    }
    if (maxX < minX || maxY < minY) return null;
    return (minX, minY, maxX, maxY);
  }

  static img.Image? _cropToOpaqueInk(img.Image image) {
    final b = _opaqueInkBounds(image);
    if (b == null) return null;
    return img.copyCrop(
      image,
      x: b.$1,
      y: b.$2,
      width: b.$3 - b.$1 + 1,
      height: b.$4 - b.$2 + 1,
    );
  }

  /// Pixel AABB of "solid" content (ignores faint fringe below [minAlpha]).
  static img.Image _cropToContentBBox(
    img.Image image,
    (int r, int g, int b)? bg, {
    int minAlpha = 40,
  }) {
    var minX = image.width;
    var minY = image.height;
    var maxX = -1;
    var maxY = -1;

    for (var y = 0; y < image.height; y++) {
      for (var x = 0; x < image.width; x++) {
        final p = image.getPixel(x, y);
        if (p.a.toInt() < minAlpha) continue;
        final r = p.r.toInt();
        final g = p.g.toInt();
        final b = p.b.toInt();
        // Near-white outlines/counters are part of the mark (keep in bbox).
        // Only skip them when they match an estimated light plate.
        if (bg != null &&
            _colorDistance(r, g, b, bg.$1, bg.$2, bg.$3) <= colorTolerance) {
          continue;
        }
        if (bg != null &&
            _isNearBlackCanvas(bg.$1, bg.$2, bg.$3) &&
            _isNearBlackCanvas(r, g, b)) {
          continue;
        }
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
    }

    if (maxX < minX || maxY < minY) return image;

    minX = math.max(0, minX - safeInset);
    minY = math.max(0, minY - safeInset);
    maxX = math.min(image.width - 1, maxX + safeInset);
    maxY = math.min(image.height - 1, maxY + safeInset);

    return img.copyCrop(
      image,
      x: minX,
      y: minY,
      width: maxX - minX + 1,
      height: maxY - minY + 1,
    );
  }

  /// Peel only fully empty outer rows/columns — never shrink into artwork.
  static img.Image _trimEmptyMargins(
    img.Image image,
    (int r, int g, int b)? bg,
  ) {
    var top = 0;
    var left = 0;
    var bottom = image.height - 1;
    var right = image.width - 1;

    bool rowEmpty(int y) {
      for (var x = left; x <= right; x++) {
        if (!_isEmptyPixel(image.getPixel(x, y), bg)) return false;
      }
      return true;
    }

    bool colEmpty(int x) {
      for (var y = top; y <= bottom; y++) {
        if (!_isEmptyPixel(image.getPixel(x, y), bg)) return false;
      }
      return true;
    }

    while (top < bottom && rowEmpty(top)) {
      top++;
    }
    while (bottom > top && rowEmpty(bottom)) {
      bottom--;
    }
    while (left < right && colEmpty(left)) {
      left++;
    }
    while (right > left && colEmpty(right)) {
      right--;
    }

    if (top == 0 &&
        left == 0 &&
        bottom == image.height - 1 &&
        right == image.width - 1) {
      return image;
    }

    // Keep a tiny safe inset so anti-aliased logo edges are not clipped.
    top = math.max(0, top - safeInset);
    left = math.max(0, left - safeInset);
    bottom = math.min(image.height - 1, bottom + safeInset);
    right = math.min(image.width - 1, right + safeInset);

    return img.copyCrop(
      image,
      x: left,
      y: top,
      width: right - left + 1,
      height: bottom - top + 1,
    );
  }

  /// Drop outer rows/columns that are almost empty (anti-aliased halo) so a
  /// square mark is not sized by a 1-pixel fringe around a large canvas.
  static img.Image _trimLowCoverageMargins(img.Image image) {
    const minAlpha = 96;
    bool isInk(int x, int y) {
      final p = image.getPixel(x, y);
      if (p.a.toInt() < minAlpha) return false;
      // Near-white outlines count as ink so wordmarks are not edge-peeled.
      return true;
    }

    // Any letter ink — chromatic, grey, silver, or black. Peeling only
    // "non-chromatic" columns clips grey taglines that sit wider than the
    // main wordmark (Quest "SEALING SOLUTIONS" → "LING SOLUT").
    bool rowHasLetterInk(int y) {
      for (var x = 0; x < image.width; x++) {
        if (isInk(x, y)) return true;
      }
      return false;
    }

    bool colHasLetterInk(int x) {
      for (var y = 0; y < image.height; y++) {
        if (isInk(x, y)) return true;
      }
      return false;
    }

    var top = 0;
    var left = 0;
    var bottom = image.height - 1;
    var right = image.width - 1;

    bool rowWeak(int y) {
      if (rowHasLetterInk(y)) return false;
      var n = 0;
      final span = right - left + 1;
      if (span <= 0) return true;
      for (var x = left; x <= right; x++) {
        if (isInk(x, y)) n++;
      }
      return n / span < 0.015;
    }

    bool colWeak(int x) {
      if (colHasLetterInk(x)) return false;
      var n = 0;
      final span = bottom - top + 1;
      if (span <= 0) return true;
      for (var y = top; y <= bottom; y++) {
        if (isInk(x, y)) n++;
      }
      return n / span < 0.015;
    }

    while (top < bottom && rowWeak(top)) {
      top++;
    }
    while (bottom > top && rowWeak(bottom)) {
      bottom--;
    }
    while (left < right && colWeak(left)) {
      left++;
    }
    while (right > left && colWeak(right)) {
      right--;
    }

    if (top == 0 &&
        left == 0 &&
        bottom == image.height - 1 &&
        right == image.width - 1) {
      return image;
    }

    return img.copyCrop(
      image,
      x: left,
      y: top,
      width: right - left + 1,
      height: bottom - top + 1,
    );
  }

  static img.Image _addPadding(
    img.Image image, {
    double fraction = paddingFraction,
    int minPad = minPadding,
    int maxPad = maxPadding,
  }) {
    final side = math.max(image.width, image.height);
    final pad =
        (side * fraction).round().clamp(minPad, maxPad).toInt();

    final out = img.Image(
      width: image.width + pad * 2,
      height: image.height + pad * 2,
      numChannels: 4,
    );

    img.compositeImage(out, image, dstX: pad, dstY: pad);
    return out;
  }
}

class _InkBlob {
  const _InkBlob({
    required this.minX,
    required this.minY,
    required this.maxX,
    required this.maxY,
    required this.area,
  });

  final int minX;
  final int minY;
  final int maxX;
  final int maxY;
  final int area;
}
