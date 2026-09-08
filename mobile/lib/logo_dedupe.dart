import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:image/image.dart' as img;
import 'package:path/path.dart' as p;

import 'logo_image_process.dart';

/// Perceptual + name/size identity for customer logos.
///
/// Same mark (size, related name, visual scan) → reuse the stored file.
/// Different mark under the same stem → `stem(1).png`.
class LogoDedupe {
  LogoDedupe._();

  static const hashSize = 8;
  static const maxHamming = 10;
  static const maxAspectDelta = 0.22;
  static const sizeMatchRatio = 0.12;

  static final _copyNoise = RegExp(
    r'(\(\d+\)|_\d+_?|-copy(?:\s*\(\d+\))?|_restored(?:_hr)?|_copy)$',
    caseSensitive: false,
  );

  static String normalizeStem(String name) {
    var stem = p.basenameWithoutExtension(name).trim().toLowerCase();
    for (var i = 0; i < 6; i++) {
      final next = stem.replaceAll(_copyNoise, '').trim();
      if (next == stem) break;
      stem = next;
    }
    stem = stem.replaceAll(RegExp(r'[^a-z0-9]+'), '');
    return stem;
  }

  static bool stemsRelated(String a, String b) {
    final na = normalizeStem(a);
    final nb = normalizeStem(b);
    if (na.isEmpty || nb.isEmpty) return false;
    if (na == nb) return true;
    if (na.length >= 4 && nb.length >= 4) {
      if (na.contains(nb) || nb.contains(na)) return true;
    }
    return false;
  }

  static bool sizesMatch(int a, int b) {
    if (a <= 0 || b <= 0) return false;
    if (a == b) return true;
    final max = math.max(a, b);
    return (a - b).abs() / max <= sizeMatchRatio;
  }

  static LogoFingerprint? fingerprint(Uint8List bytes, {int? byteLength}) {
    final image = LogoImageProcessor.decodeToRgba(bytes);
    if (image == null) return null;
    return fingerprintImage(image, byteLength: byteLength ?? bytes.length);
  }

  static LogoFingerprint? fingerprintImage(
    img.Image image, {
    required int byteLength,
  }) {
    var minX = image.width;
    var minY = image.height;
    var maxX = -1;
    var maxY = -1;
    var ink = 0;
    for (var y = 0; y < image.height; y++) {
      for (var x = 0; x < image.width; x++) {
        final px = image.getPixel(x, y);
        if (px.a.toInt() < 80) continue;
        final r = px.r.toInt();
        final g = px.g.toInt();
        final b = px.b.toInt();
        final sat = math.max(r, math.max(g, b)) - math.min(r, math.min(g, b));
        final lum = (r + g + b) / 3.0;
        if (lum >= 232 && sat <= 18) continue;
        if (lum <= 18 && sat <= 12) continue;
        ink++;
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
    }
    if (ink < 12 || maxX < minX) {
      minX = 0;
      minY = 0;
      maxX = image.width - 1;
      maxY = image.height - 1;
      ink = math.max(ink, 1);
    }
    final cropW = maxX - minX + 1;
    final cropH = maxY - minY + 1;
    final cropped = img.copyCrop(
      image,
      x: minX,
      y: minY,
      width: cropW,
      height: cropH,
    );
    final small = img.copyResize(
      cropped,
      width: hashSize,
      height: hashSize,
      interpolation: img.Interpolation.average,
    );
    final lums = <double>[];
    for (var y = 0; y < hashSize; y++) {
      for (var x = 0; x < hashSize; x++) {
        final px = small.getPixel(x, y);
        lums.add((px.r.toInt() + px.g.toInt() + px.b.toInt()) / 3.0);
      }
    }
    final mean = lums.reduce((a, b) => a + b) / lums.length;
    var hash = 0;
    for (var i = 0; i < lums.length; i++) {
      if (lums[i] >= mean) hash |= 1 << i;
    }
    return LogoFingerprint(
      byteLength: byteLength,
      hash: hash,
      aspect: cropW / math.max(1, cropH),
      inkPixels: ink,
      width: image.width,
      height: image.height,
    );
  }

  static int hamming(int a, int b) {
    var x = a ^ b;
    var n = 0;
    while (x != 0) {
      x &= x - 1;
      n++;
    }
    return n;
  }

  static bool isVisualMatch(LogoFingerprint a, LogoFingerprint b) {
    if (hamming(a.hash, b.hash) > maxHamming) return false;
    final aspectDelta =
        (a.aspect - b.aspect).abs() / math.max(a.aspect, b.aspect);
    if (aspectDelta > maxAspectDelta) return false;
    final inkMax = math.max(a.inkPixels, b.inkPixels);
    if (inkMax > 0 && (a.inkPixels - b.inkPixels).abs() / inkMax > 0.55) {
      return false;
    }
    return true;
  }

  /// First stored file that is the same mark as [processedBytes].
  static File? findReusable({
    required Uint8List processedBytes,
    required String preferredName,
    required List<File> stored,
  }) {
    final incoming = fingerprint(processedBytes);
    if (incoming == null || stored.isEmpty) return null;

    File? visualHit;
    var visualHamming = 64;
    for (final file in stored) {
      if (!file.existsSync()) continue;
      final existingBytes = file.readAsBytesSync();
      final existing = fingerprint(
        Uint8List.fromList(existingBytes),
        byteLength: existingBytes.length,
      );
      if (existing == null) continue;
      final related = stemsRelated(preferredName, p.basename(file.path));
      final sizeOk = sizesMatch(processedBytes.length, existingBytes.length);
      final visual = isVisualMatch(incoming, existing);
      if (!visual) continue;
      if (related && sizeOk) return file;
      final d = hamming(incoming.hash, existing.hash);
      if (visualHit == null || d < visualHamming || (d == visualHamming && sizeOk)) {
        visualHit = file;
        visualHamming = d;
      }
    }
    return visualHit;
  }

  /// Next free `stem(1).png`, `stem(2).png`, … when the mark is actually new.
  static File nextUniqueDest(String logosDir, String stem) {
    var n = 1;
    var dest = File(p.join(logosDir, '$stem($n).png'));
    while (dest.existsSync()) {
      n++;
      dest = File(p.join(logosDir, '$stem($n).png'));
    }
    return dest;
  }

  static bool isClutterName(String name) {
    final base = p.basename(name);
    return RegExp(
      r'(\(\d+\)|_\d+_|- copy|history_|_restored)',
      caseSensitive: false,
    ).hasMatch(base);
  }

  static const preferredNames = {
    'Arc Resources LTD.png',
    'Trialta Projects.png',
    'ARJAE.png',
    'Propak-Energy-Services-Logo.png',
    'GCM logo2.png',
    'bird_source.png',
    'bfl fabricators.png',
    'bfl_google_source.png',
    'murrays_trucking.png',
    'SMJV_Alpha.png',
    'WPW Pipeline and Facility Construction.png',
    'Spartan Delta Corp.png',
    'Worley logo.png',
    'Worley Cord LP.png',
  };

  static double keepScore(File file, LogoFingerprint fp) {
    final name = p.basename(file.path);
    var score = math.log(math.max(16, fp.inkPixels)) * 10;
    score += math.log(math.max(16, fp.width * fp.height)) * 4;
    if (preferredNames.contains(name)) score += 80;
    if (!isClutterName(name)) score += 24;
    if (RegExp(r'[A-Z]').hasMatch(name) && name.contains(' ')) score += 6;
    if (name.toLowerCase().contains('history_')) score -= 40;
    if (name.toLowerCase().contains('copy')) score -= 18;
    if (RegExp(r'\(\d+\)|_\d+_').hasMatch(name)) score -= 12;
    // Giant restored dumps of the same mark are clutter.
    if (name.toLowerCase().contains('restored') && fp.width * fp.height > 2000000) {
      score -= 30;
    }
    return score;
  }

  /// Cluster visually-matching files and pick one keeper (or two if
  /// aspect/hash says they are distinct versions, e.g. Arc vs Arc Resources).
  static List<LogoCleanupGroup> planCleanup(List<File> files) {
    final items = <_Item>[];
    for (final file in files) {
      if (!file.existsSync()) continue;
      final bytes = file.readAsBytesSync();
      final fp = fingerprint(Uint8List.fromList(bytes), byteLength: bytes.length);
      if (fp == null) continue;
      items.add(_Item(file: file, fp: fp));
    }
    final n = items.length;
    final parent = List<int>.generate(n, (i) => i);
    int find(int i) {
      while (parent[i] != i) {
        parent[i] = parent[parent[i]];
        i = parent[i];
      }
      return i;
    }

    void union(int a, int b) {
      final ra = find(a);
      final rb = find(b);
      if (ra != rb) parent[rb] = ra;
    }

    for (var i = 0; i < n; i++) {
      for (var j = i + 1; j < n; j++) {
        final related = stemsRelated(
          p.basename(items[i].file.path),
          p.basename(items[j].file.path),
        );
        if (related || isVisualMatch(items[i].fp, items[j].fp)) {
          union(i, j);
        }
      }
    }

    final buckets = <int, List<_Item>>{};
    for (var i = 0; i < n; i++) {
      buckets.putIfAbsent(find(i), () => []).add(items[i]);
    }

    final groups = <LogoCleanupGroup>[];
    for (final bucket in buckets.values) {
      if (bucket.length == 1) continue;
      final versions = _splitVersions(bucket);
      for (final version in versions) {
        version.sort(
          (a, b) => keepScore(b.file, b.fp).compareTo(keepScore(a.file, a.fp)),
        );
        final keep = version.first;
        final drop = version.skip(1).map((e) => e.file).toList();
        if (drop.isEmpty) continue;
        groups.add(
          LogoCleanupGroup(
            keep: keep.file,
            delete: drop,
            reason:
                'same visual mark (${version.length} files); kept ${p.basename(keep.file.path)}',
          ),
        );
      }
    }
    return groups;
  }

  static List<List<_Item>> _splitVersions(List<_Item> bucket) {
    if (bucket.length < 2) return [bucket];
    // Distinct lockups (short wordmark vs long name) stay as two keepers.
    final seeds = <_Item>[];
    for (final item in bucket) {
      final isNew = seeds.every((s) {
        final aspectDelta =
            (item.fp.aspect - s.fp.aspect).abs() / math.max(item.fp.aspect, s.fp.aspect);
        final canvasA = item.fp.width / math.max(1, item.fp.height);
        final canvasB = s.fp.width / math.max(1, s.fp.height);
        final canvasDelta =
            (canvasA - canvasB).abs() / math.max(canvasA, canvasB);
        final inkMax = math.max(item.fp.inkPixels, s.fp.inkPixels);
        final inkDelta =
            inkMax == 0 ? 0.0 : (item.fp.inkPixels - s.fp.inkPixels).abs() / inkMax;
        return hamming(item.fp.hash, s.fp.hash) > maxHamming ||
            aspectDelta > 0.28 ||
            canvasDelta > 0.18 ||
            inkDelta > 0.28;
      });
      if (isNew) seeds.add(item);
    }
    if (seeds.length <= 1) return [bucket];
    final groups = [for (final _ in seeds) <_Item>[]];
    for (final item in bucket) {
      var best = 0;
      var bestD = 64;
      for (var i = 0; i < seeds.length; i++) {
        final d = hamming(item.fp.hash, seeds[i].fp.hash);
        if (d < bestD) {
          bestD = d;
          best = i;
        }
      }
      groups[best].add(item);
    }
    return groups.where((g) => g.isNotEmpty).toList();
  }
}

class LogoFingerprint {
  const LogoFingerprint({
    required this.byteLength,
    required this.hash,
    required this.aspect,
    required this.inkPixels,
    required this.width,
    required this.height,
  });

  final int byteLength;
  final int hash;
  final double aspect;
  final int inkPixels;
  final int width;
  final int height;
}

class LogoCleanupGroup {
  const LogoCleanupGroup({
    required this.keep,
    required this.delete,
    required this.reason,
  });

  final File keep;
  final List<File> delete;
  final String reason;
}

class _Item {
  const _Item({required this.file, required this.fp});
  final File file;
  final LogoFingerprint fp;
}
