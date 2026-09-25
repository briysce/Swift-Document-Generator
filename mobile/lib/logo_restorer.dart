import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:image/image.dart' as img;
import 'package:path/path.dart' as p;

import 'gemini_client.dart';
import 'logo_image_process.dart';
import 'logo_realesrgan.dart';
import 'logo_vectorize.dart';
import 'restore_catalog.dart';

/// Print-ready customer logo restore.
///
/// Generative branch order (Windows, when Python tools exist) — every step
/// fail-opens (AI out-of-tokens never blocks local engines):
/// 1. **Vectorize** (`scripts/logo_vectorize.py`) — sectional Bezier with
///    DejaType-style per-element anchors, then VTracer (Inkscape in ensemble).
/// 2. **Raster polish + SR** (`logo_restorer.py`) — classical Gigapixel /
///    Upscayl / Remacri / UltraSharp-inspired polish, Real-ESRGAN family
///    weights, optional GFPGAN / Upscayl CLI, then Lanczos.
/// 3. **Cubic conservator** — faithful upscale; cannot invent lost detail.
///
/// Gemini (`restoreLogoPng`) is **demoted**: off by default. Set env
/// `LOGO_RESTORE_USE_GEMINI=1` to allow it only after earlier engines fail.
/// Redraws that fail the fidelity gate are discarded. Golden suite evidence
/// decides whether Gemini stays demoted.
///
/// Android: no bundled vectorize/ESRGAN — cubic, then optional Gemini if env
/// enables it. Capability limit: invent missing edge/fill detail from
/// degradation patterns — **not** a free-form brand redesign.
///
/// Every pass is scored into [lessonsFileName].
class LogoRestorer {
  LogoRestorer._();

  static const minDimension = 3000;
  static const pipelineVersion = 'print-ready-v15-vectorize';
  /// Below this source height, vectorize / Real-ESRGAN / optional Gemini may
  /// run. At or above it, only a gentle raster conservator runs.
  static const generativeMaxHeight = 1600;
  static const cacheFileName = 'logo_restore_cache.json';
  static const lessonsFileName = 'logo_restore_lessons.json';
  static final Map<String, Future<File>> _inFlight = {};
  static int _epoch = 0;

  static int get epoch => _epoch;

  /// Opt-in Gemini branch (default off). Checked at call time.
  static bool get useGeminiRestore {
    final v = (Platform.environment['LOGO_RESTORE_USE_GEMINI'] ?? '')
        .trim()
        .toLowerCase();
    return v == '1' || v == 'true' || v == 'yes' || v == 'on';
  }

  /// Abort in-flight restores. Completions after this are discarded.
  static void cancelAll() {
    _epoch++;
    _inFlight.clear();
  }

  static Future<File> ensureHighRes(
    File source, {
    required Directory logosDir,
    void Function(String)? onLog,
    bool skipGenerative = false,
  }) async {
    if (!await source.exists()) return source;

    final identity = await _sourceIdentity(source);
    final existing = _inFlight[identity];
    if (existing != null) return existing;

    final started = _epoch;
    final future = _ensureHighResUncapped(
      source,
      logosDir: logosDir,
      identity: identity,
      startedEpoch: started,
      onLog: onLog,
      skipGenerative: skipGenerative,
    );
    _inFlight[identity] = future;
    try {
      return await future;
    } finally {
      _inFlight.remove(identity);
    }
  }

  static Future<File> _ensureHighResUncapped(
    File source, {
    required Directory logosDir,
    required String identity,
    required int startedEpoch,
    void Function(String)? onLog,
    bool skipGenerative = false,
  }) async {
    bool cancelled() => startedEpoch != _epoch;

    await logosDir.create(recursive: true);
    final cache = await _loadCache(logosDir);
    final cachedPath = cache[identity];
    if (cachedPath != null) {
      final cached = File(cachedPath);
      if (await cached.exists() && await cached.length() > 0) {
        onLog?.call('logo_restorer: cache hit ${cached.path}');
        return cached;
      }
    }

    final sourceBytes = await source.readAsBytes();
    if (cancelled()) return source;
    if (_isPrintReadyRestored(source, sourceBytes)) {
      onLog?.call('logo_restorer: already print-ready ${source.path}');
      cache[identity] = source.absolute.path;
      await _saveCache(logosDir, cache);
      return source;
    }

    final sourceHeight = heightOfBytes(sourceBytes);
    final catalog = await _loadCatalog(logosDir);
    final addenda = catalog.winningAddenda();

    var usedGemini = false;
    var usedRealEsrgan = false;
    var usedVectorize = false;
    late Uint8List png;
    final referenceBytes = sourceBytes;
    late Uint8List workBytes;

    if (!usesGenerativeRestore(sourceHeight) || skipGenerative) {
      onLog?.call(
        'logo_restorer: high-res conservator (${sourceHeight}px, no SR)',
      );
      png = _conservatorRaster(sourceBytes);
      workBytes = LogoImageProcessor.prepareRasterForRestore(sourceBytes);
      if (workBytes.isEmpty) workBytes = sourceBytes;
    } else {
      final prepared = LogoImageProcessor.prepareRasterForRestore(sourceBytes);
      onLog?.call('logo_restorer: prepared transparent raster for enhance');
      workBytes = prepared.isNotEmpty ? prepared : sourceBytes;
      png = Uint8List(0);

      // 1) Vectorize — flat lockups (Swift-like clean fills when applicable).
      final vec = await LogoVectorize.restore(
        workBytes,
        minHeight: minDimension,
        onLog: onLog,
      );
      if (vec != null &&
          vec.isNotEmpty &&
          LogoImageProcessor.isAcceptableSuperResolution(sourceBytes, vec)) {
        png = vec;
        usedVectorize = true;
        onLog?.call('logo_restorer: vectorize accepted');
      }

      // 2) Real-ESRGAN — structure-aware SR when vectorize misses / unsuitable.
      if (!usedVectorize) {
        final sr = await LogoRealEsrgan.restore(
          workBytes,
          minHeight: minDimension,
          onLog: onLog,
        );
        if (sr != null &&
            sr.isNotEmpty &&
            LogoImageProcessor.isAcceptableSuperResolution(sourceBytes, sr)) {
          png = sr;
          usedRealEsrgan = true;
          onLog?.call('logo_restorer: Real-ESRGAN accepted');
        }
      }

      // 3) Gemini — opt-in only; reject redraws.
      final geminiAllowed = useGeminiRestore && GeminiClient.isConfigured;
      if (!usedVectorize && !usedRealEsrgan && geminiAllowed) {
        try {
          onLog?.call('logo_restorer: Gemini restoreLogoPng (opt-in)');
          final gem = await GeminiClient().restoreLogoPng(
            workBytes,
            addenda: addenda,
          );
          if (gem.isNotEmpty &&
              LogoImageProcessor.isFaithfulRestore(workBytes, gem) &&
              LogoImageProcessor.isFaithfulRestore(sourceBytes, gem) &&
              LogoImageProcessor.aspectDrift(sourceBytes, gem) <= 0.32) {
            png = gem;
            usedGemini = true;
          } else {
            onLog?.call(
              'logo_restorer: Gemini changed the artwork; skipped',
            );
          }
        } catch (e) {
          onLog?.call('logo_restorer: Gemini failed ($e)');
        }
      } else if (!usedVectorize &&
          !usedRealEsrgan &&
          GeminiClient.isConfigured &&
          !useGeminiRestore) {
        onLog?.call(
          'logo_restorer: Gemini demoted (set LOGO_RESTORE_USE_GEMINI=1)',
        );
      }

      // 4) Cubic conservator — faithful but cannot invent lost detail.
      if (!usedVectorize && !usedRealEsrgan && !usedGemini) {
        onLog?.call('logo_restorer: cubic conservator fallback');
        png = _conservatorRaster(sourceBytes);
      }
    }

    final fingerprint = _cheapFingerprint(workBytes);
    catalog.successCount[fingerprint] =
        (catalog.successCount[fingerprint] ?? 0) + 1;

    if (cancelled()) return source;

    final decoded = img.decodeImage(png);
    if (decoded != null) {
      // Connected-component strip is O(pixels). Run only at working size —
      // after a 3000px upscale this dominated restore latency (~80s smoke).
      final maxEdge = math.max(decoded.width, decoded.height);
      if (maxEdge <= 1600) {
        LogoImageProcessor.stripForeignMarks(decoded);
        png = Uint8List.fromList(img.encodePng(decoded));
      }
    }

    final enhanceOnly = !usedGemini && !usedRealEsrgan && !usedVectorize;
    final finalized = finalizeRestoredPng(
      png,
      sourceBytes: referenceBytes,
      enhanceOnly: enhanceOnly,
    );
    // Keep the ink-cropped canvas. Letterboxing back to the source plate
    // made contact sheets shrink the mark and failed knockout-relative IoU.

    final quality = RestoreQuality.measure(
      geminiOk: usedGemini,
      source: referenceBytes,
      restored: finalized,
      hadCornerMark: false,
    );
    catalog.record(
      sourceName: p.basename(source.path),
      grade: quality.grade,
      used: [
        if (usedVectorize) 'vectorize_primary',
        if (usedRealEsrgan) 'realesrgan_primary',
        if (usedGemini) 'gemini_opt_in',
        if (!usedVectorize && !usedRealEsrgan && !usedGemini)
          'raster_conservator',
        'plate_knockout',
        'no_watermark',
        'halo_strip',
        'solid_fills',
        'color_lock',
        'ink_crop',
        'studio_finish',
      ],
      notes: RestoreCatalog.lessonsFrom(quality),
    );
    await _saveCatalog(logosDir, catalog);
    if (cancelled()) return source;

    final dest = await _restoredDestFile(source, logosDir);
    await dest.writeAsBytes(finalized, flush: true);
    cache[identity] = dest.absolute.path;
    await _saveCache(logosDir, cache);
    onLog?.call('logo_restorer: wrote ${dest.path}');
    return dest;
  }

  static bool usesGenerativeRestore(int sourceHeight) =>
      sourceHeight > 0 && sourceHeight < generativeMaxHeight;

  static Uint8List _conservatorRaster(Uint8List sourceBytes) {
    final prepared = LogoImageProcessor.prepareRasterForRestore(sourceBytes);
    final bytes = prepared.isNotEmpty ? prepared : sourceBytes;
    // Keep native / prepared size here. [_finalizeConservatorOnly] does the
    // single premultiplied cubic upscale to [minDimension] — avoid double
    // 3000px encode (was ~tens of seconds on Windows smoke).
    return bytes;
  }

  static bool _isPrintReadyRestored(File source, Uint8List bytes) {
    final name = p.basename(source.path).toLowerCase();
    if (!name.contains('restored')) return false;
    return heightOfBytes(bytes) >= 2000;
  }

  static int heightOfBytes(Uint8List bytes) {
    final decoded = img.decodeImage(bytes);
    if (decoded == null) return 0;
    return decoded.height;
  }

  static String _cheapFingerprint(Uint8List bytes) {
    var h = bytes.length;
    final step = math.max(1, bytes.length ~/ 512);
    for (var i = 0; i < bytes.length; i += step) {
      h = 0x1fffffff & (h * 31 + bytes[i]);
    }
    return '$h:${bytes.length}:$pipelineVersion';
  }

  /// Studio finish after generative restore (or cubic fallback): plate/halo
  /// out, lock brand colors, even blotchy interiors, PNG at [minDimension].
  ///
  /// Swift-quality lesson: do not re-trace over a good lockup; preserve dark
  /// strokes against chromatic fills; avoid letterboxing to the source plate.
  static Uint8List finalizeRestoredPng(
    Uint8List bytes, {
    Uint8List? sourceBytes,
    bool enhanceOnly = false,
  }) {
    if (bytes.isEmpty) return bytes;

    if (enhanceOnly) {
      return _finalizeConservatorOnly(bytes, sourceBytes: sourceBytes);
    }

    final cropped = LogoImageProcessor.normalizeToVisibleContent(bytes);
    var image = img.decodeImage(cropped.isNotEmpty ? cropped : bytes);
    if (image == null || image.height <= 0) {
      return LogoImageProcessor.upscaleForPrint(
        bytes,
        minHeight: minDimension,
      );
    }
    // Stroke-aware halo strip: Swift orange↔black seams must not open.
    LogoImageProcessor.stripHaloFringe(image, protectStrokeFills: true);

    if (!enhanceOnly && sourceBytes != null && sourceBytes.isNotEmpty) {
      image = LogoImageProcessor.snapToSourceBrandColors(image, sourceBytes);
      final srcImg = img.decodeImage(sourceBytes);
      if (srcImg != null) {
        image = LogoImageProcessor.ensureLetterOutline(srcImg, image);
      }
      final locked = Uint8List.fromList(img.encodePng(image));
      if (!LogoImageProcessor.retainsBrandColors(sourceBytes, locked)) {
        return _finalizeFromSourceFallback(sourceBytes);
      }
    }

    if (!enhanceOnly) {
      // Flatten at working size — k-means/flood at 3000px hangs and over-edges.
      if (image.height > 1600) {
        final s = 1600 / image.height;
        image = img.copyResize(
          image,
          width: math.max(1, (image.width * s).round()),
          height: 1600,
          interpolation: img.Interpolation.average,
        );
      }
      image = LogoImageProcessor.flattenSolidBrandFills(image);
      image = LogoImageProcessor.hardenFlatEdges(image);
    }

    if (image.height < minDimension) {
      final scale = minDimension / image.height;
      final w = math.max(1, (image.width * scale).round());
      image = LogoImageProcessor.resizePremultipliedCubic(
        image,
        width: w,
        height: minDimension,
      );
    }
    return Uint8List.fromList(img.encodePng(image));
  }

  /// Cubic conservator: optional native flatten for mottled JPEG fills, then
  /// premultiplied upscale. Matches Swift's flat-fill look without redrawing.
  static Uint8List _finalizeConservatorOnly(
    Uint8List bytes, {
    Uint8List? sourceBytes,
  }) {
    var work = bytes;
    final decoded = img.decodeImage(bytes);
    if (decoded != null && decoded.height > 0 && decoded.height < 900) {
      // Light interior flatten at native size (Swift-like solid fills).
      var flat = LogoImageProcessor.flattenSolidBrandFills(decoded);
      flat = LogoImageProcessor.hardenFlatEdges(flat);
      if (sourceBytes != null && sourceBytes.isNotEmpty) {
        flat = LogoImageProcessor.snapToSourceBrandColors(flat, sourceBytes);
        flat = LogoImageProcessor.hardenFlatEdges(flat);
      }
      work = Uint8List.fromList(img.encodePng(flat));
    }
    return LogoImageProcessor.upscaleForPrint(work, minHeight: minDimension);
  }

  static Uint8List _finalizeFromSourceFallback(Uint8List sourceBytes) {
    final cropped = LogoImageProcessor.normalizeToVisibleContent(sourceBytes);
    var image = img.decodeImage(cropped.isNotEmpty ? cropped : sourceBytes);
    if (image == null || image.height <= 0) return sourceBytes;
    if (image.height < minDimension) {
      final scale = minDimension / image.height;
      final w = math.max(1, (image.width * scale).round());
      image = LogoImageProcessor.resizePremultipliedCubic(
        image,
        width: w,
        height: minDimension,
      );
    }
    return Uint8List.fromList(img.encodePng(image));
  }

  static Future<String> _sourceIdentity(File source) async {
    final stat = await source.stat();
    return '${p.normalize(source.absolute.path)}|'
        '${stat.size}|${stat.modified.millisecondsSinceEpoch}|'
        '$pipelineVersion';
  }

  static Future<File> _restoredDestFile(File source, Directory logosDir) async {
    var stem = p.basenameWithoutExtension(source.path);
    if (stem.toLowerCase().endsWith('_restored')) {
      stem = stem.substring(0, stem.length - '_restored'.length);
    }
    stem = stem.replaceAll(RegExp(r'[^\w\- .]+'), '_');
    if (stem.isEmpty) stem = 'logo';
    var dest = File(p.join(logosDir.path, '${stem}_restored.png'));
    if (p.equals(dest.path, source.absolute.path)) {
      dest = File(p.join(logosDir.path, '${stem}_restored_hr.png'));
    }
    return dest;
  }

  static File _cacheFile(Directory logosDir) =>
      File(p.join(logosDir.path, cacheFileName));

  static File _lessonsFile(Directory logosDir) =>
      File(p.join(logosDir.path, lessonsFileName));

  static Future<Map<String, String>> _loadCache(Directory logosDir) async {
    final file = _cacheFile(logosDir);
    if (!await file.exists()) return {};
    try {
      final decoded = jsonDecode(await file.readAsString());
      if (decoded is! Map) return {};
      return {
        for (final e in decoded.entries)
          if (e.key is String && e.value is String)
            e.key as String: e.value as String,
      };
    } catch (_) {
      return {};
    }
  }

  static Future<void> _saveCache(
    Directory logosDir,
    Map<String, String> cache,
  ) async {
    final file = _cacheFile(logosDir);
    await file.writeAsBytes(
      utf8.encode(const JsonEncoder.withIndent('  ').convert(cache)),
    );
  }

  static Future<RestoreCatalog> _loadCatalog(Directory logosDir) async {
    final file = _lessonsFile(logosDir);
    if (!await file.exists()) return RestoreCatalog();
    try {
      final decoded = jsonDecode(await file.readAsString());
      if (decoded is! Map) return RestoreCatalog();
      return RestoreCatalog.fromJson(Map<String, dynamic>.from(decoded));
    } catch (_) {
      return RestoreCatalog();
    }
  }

  static Future<void> _saveCatalog(
    Directory logosDir,
    RestoreCatalog catalog,
  ) async {
    final file = _lessonsFile(logosDir);
    await file.writeAsBytes(
      utf8.encode(prettyJson(catalog.toJson())),
    );
  }
}
