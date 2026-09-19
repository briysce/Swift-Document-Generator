import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:http/http.dart' as http;
import 'package:image/image.dart' as img;
import 'package:path/path.dart' as p;

import 'app_config.dart';
import 'logo_image_process.dart';

/// Shared Gemini multimodal client (logo validation + recreate assist).
///
/// Credentials resolve from (first hit wins):
/// 1. `--dart-define=GEMINI_API_KEY=...`
/// 2. Process environment `GEMINI_API_KEY` / `GOOGLE_API_KEY`
/// 3. Gitignored `.env` / `.env.local` next to the app / repo root
class GeminiClient {
  GeminiClient({http.Client? client}) : _client = client ?? http.Client();

  final http.Client _client;

  // gemini-2.0-flash was retired by Google (404s as of this writing) — keep
  // this current or every JSON-based call (validate/rank/critique/etc.)
  // silently returns null.
  static const _defaultModel = 'gemini-3.6-flash';
  static const _maxRetries = 3;

  static String? _cachedKey;
  static bool _envLoaded = false;

  static bool get isConfigured =>
      resolveApiKey().isNotEmpty && !isTemporarilyUnavailable;

  /// After repeated 429/5xx, skip Gemini for a cool-down so crawlers stay fast.
  static DateTime? _quotaCooldownUntil;
  static bool get isTemporarilyUnavailable {
    final until = _quotaCooldownUntil;
    if (until == null) return false;
    if (DateTime.now().isAfter(until)) {
      _quotaCooldownUntil = null;
      return false;
    }
    return true;
  }

  static void _tripQuotaCooldown([Duration duration = const Duration(minutes: 3)]) {
    _quotaCooldownUntil = DateTime.now().add(duration);
  }

  /// True when the provider refused the call for quota / token / billing.
  /// Callers must fall open to the local restore engine — never degrade.
  static bool _isQuotaOrTokenFailure(int status, [String body = '']) {
    if (status == 401 || status == 402 || status == 403) return true;
    final blob = body.toLowerCase();
    const markers = <String>[
      'resource_exhausted',
      'insufficient_quota',
      'quota exceeded',
      'quota_exceeded',
      'billing',
      'out of tokens',
      'token limit',
      'credit',
      'payment required',
    ];
    if (status == 429) {
      return markers.any(blob.contains);
    }
    return markers.any(blob.contains);
  }


  static String resolveApiKey() {
    if (_cachedKey != null && _cachedKey!.isNotEmpty) return _cachedKey!;
    _ensureEnvLoaded();
    final candidates = <String>[
      AppConfig.geminiApiKeyDefine,
      Platform.environment['GEMINI_API_KEY'] ?? '',
      Platform.environment['GOOGLE_API_KEY'] ?? '',
      _envOverlay['GEMINI_API_KEY'] ?? '',
      _envOverlay['GOOGLE_API_KEY'] ?? '',
    ];
    for (final c in candidates) {
      final v = c.trim();
      if (v.isNotEmpty) {
        _cachedKey = v;
        return v;
      }
    }
    _cachedKey = '';
    return '';
  }

  static String get model {
    _ensureEnvLoaded();
    final fromEnv = Platform.environment['GEMINI_MODEL']?.trim() ?? '';
    if (fromEnv.isNotEmpty) return fromEnv;
    final fromDotEnv = (_envOverlay['GEMINI_MODEL'] ?? '').trim();
    if (fromDotEnv.isNotEmpty) return fromDotEnv;
    return AppConfig.geminiModel.trim().isEmpty
        ? _defaultModel
        : AppConfig.geminiModel.trim();
  }

  /// Process env, then gitignored `.env` overlay.
  static String envValue(String key) {
    _ensureEnvLoaded();
    final fromProcess = (Platform.environment[key] ?? '').trim();
    if (fromProcess.isNotEmpty) return fromProcess;
    return (_envOverlay[key] ?? '').trim();
  }

  static void _ensureEnvLoaded() {
    if (_envLoaded) return;
    _envLoaded = true;
    try {
      for (final path in _envCandidatePaths()) {
        final f = File(path);
        if (!f.existsSync()) continue;
        for (final raw in f.readAsLinesSync()) {
          final line = raw.trim();
          if (line.isEmpty || line.startsWith('#')) continue;
          final i = line.indexOf('=');
          if (i <= 0) continue;
          final key = line.substring(0, i).trim();
          var value = line.substring(i + 1).trim();
          if ((value.startsWith('"') && value.endsWith('"')) ||
              (value.startsWith("'") && value.endsWith("'"))) {
            value = value.substring(1, value.length - 1);
          }
          if (key.isEmpty) continue;
          // Do not override an already-set process env var.
          if ((Platform.environment[key] ?? '').trim().isEmpty) {
            _envOverlay.putIfAbsent(key, () => value);
          }
        }
      }
    } catch (_) {}
  }

  static final Map<String, String> _envOverlay = {};

  static Iterable<String> _envCandidatePaths() sync* {
    try {
      var dir = Directory.current.path;
      for (var i = 0; i < 6; i++) {
        yield p.join(dir, '.env');
        yield p.join(dir, '.env.local');
        final parent = p.dirname(dir);
        if (parent == dir) break;
        dir = parent;
      }
    } catch (_) {}
    try {
      final exeDir = File(Platform.resolvedExecutable).parent.path;
      yield p.join(exeDir, '.env');
      yield p.join(exeDir, 'data', '.env');
    } catch (_) {}
  }

  /// Vision JSON for crawler filtering.
  Future<GeminiLogoValidation?> validateLogoCandidate(
    Uint8List bytes, {
    String? mimeType,
    String companyHint = '',
  }) async {
    final key = resolveApiKey();
    if (key.isEmpty || bytes.isEmpty || isTemporarilyUnavailable) return null;

    final prompt = '''
You are filtering scraped web images for a corporate logo picker${companyHint.isEmpty ? '' : ' for "$companyHint"'}.

Decide if this image is a clean usable company logo / brand mark suitable for a
shipping label (wordmark, icon, or lockup). Reject photos of people, trucks,
equipment, warehouses, screenshots of articles, memes, or low-quality artifacts.

Return JSON only:
{
  "is_valid_logo": true,
  "has_transparent_or_solid_background": true,
  "confidence_score": 0.92,
  "reason": "Clean wordmark on transparent background"
}
''';

    final data = await _generateJson(
      key: key,
      prompt: prompt,
      imageBytes: bytes,
      mimeType: mimeType ?? _guessMime(bytes),
    );
    if (data == null) return null;
    return GeminiLogoValidation.fromJson(data);
  }

  /// Use Gemini + Google Search grounding to recover logo image URLs when the
  /// HTML Google Images scraper is blocked (JS-only shells).
  ///
  /// Fail-open: returns [] when unconfigured, rate-limited, or malformed.
  Future<List<String>> suggestGoogleLogoImageUrls({
    required String companyName,
    List<String> knownDomains = const [],
  }) async {
    final key = resolveApiKey();
    final name = companyName.trim();
    if (key.isEmpty || name.isEmpty || isTemporarilyUnavailable) {
      return const [];
    }

    final known =
        knownDomains.where((d) => d.trim().isNotEmpty).take(6).join(', ');
    final prompt = '''
Find direct public image URLs for the official company logo of "$name".
Prefer PNG/SVG/JPEG/WebP links to clean brand marks (wordmark or icon).
Known domains (may help): ${known.isEmpty ? '(none)' : known}

Return JSON only:
{
  "logo_urls": ["https://..."],
  "notes": "brief"
}

Rules:
- Only include URLs you believe are real, publicly reachable logo assets.
- Prefer official company sites and reputable brand CDN / press assets.
- Prefer Google Images-style logo results a human would pick in ~2 seconds.
- Omit social profile photos, screenshots, and unrelated stock images.
''';

    final uri = Uri.parse(
      'https://generativelanguage.googleapis.com/v1beta/models/'
      '$model:generateContent?key=$key',
    );
    final payload = {
      'contents': [
        {
          'parts': [
            {'text': prompt},
          ],
        },
      ],
      'tools': [
        {'google_search': <String, dynamic>{}},
      ],
      'generationConfig': {
        'temperature': 0.2,
      },
    };

    try {
      final res = await _client
          .post(
            uri,
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode(payload),
          )
          .timeout(const Duration(seconds: 10));
      if (res.statusCode == 429) {
        _tripQuotaCooldown();
        return const [];
      }
      if (_isQuotaOrTokenFailure(res.statusCode, res.body)) {
        _tripQuotaCooldown(const Duration(minutes: 10));
        return const [];
      }
      if (res.statusCode < 200 || res.statusCode >= 300) return const [];
      final body = jsonDecode(res.body);
      if (body is! Map) return const [];

      final out = <String>{};

      // Model text JSON (when present).
      final text = _extractText(body);
      if (text != null && text.isNotEmpty) {
        final parsed = _parseJsonObject(text);
        final list = parsed?['logo_urls'];
        if (list is List) {
          for (final item in list) {
            final u = '$item'.trim();
            if (u.startsWith('http')) out.add(u);
          }
        }
        // Also harvest any raw http URLs from the text blob.
        for (final m in RegExp(r'https?://[^\s\"<>\]]+').allMatches(text)) {
          final u = m.group(0)!;
          if (_looksLikeImageUrl(u)) out.add(u);
        }
      }

      // Grounding chunks often carry the Google Search result URIs.
      final cands = body['candidates'];
      if (cands is List && cands.isNotEmpty && cands.first is Map) {
        final gm = (cands.first as Map)['groundingMetadata'];
        if (gm is Map) {
          final chunks = gm['groundingChunks'];
          if (chunks is List) {
            for (final ch in chunks) {
              if (ch is! Map) continue;
              final web = ch['web'];
              if (web is! Map) continue;
              final u = '${web['uri'] ?? ''}'.trim();
              if (u.startsWith('http') && _looksLikeImageUrl(u)) out.add(u);
            }
          }
        }
      }

      return out.take(12).toList();
    } catch (_) {
      return const [];
    }
  }

  static bool _looksLikeImageUrl(String url) {
    final lower = url.toLowerCase();
    if (lower.contains('.png') ||
        lower.contains('.jpg') ||
        lower.contains('.jpeg') ||
        lower.contains('.webp') ||
        lower.contains('.svg') ||
        lower.contains('/logo') ||
        lower.contains('logo_')) {
      return true;
    }
    return false;
  }

  /// Text-only search plan to close gaps when scrapers return sparse/junk results.
  ///
  /// Fail-open: returns null when unconfigured, rate-limited, or malformed.
  Future<GeminiLogoSearchPlan?> suggestLogoSearchPlan({
    required String companyName,
    List<String> knownDomains = const [],
  }) async {
    final key = resolveApiKey();
    final name = companyName.trim();
    if (key.isEmpty || name.isEmpty || isTemporarilyUnavailable) return null;

    final known = knownDomains.where((d) => d.trim().isNotEmpty).take(6).join(', ');
    final prompt = '''
You help a warehouse shipping-label app find the official company logo online.

Company: "$name"
Known domains (may be empty or wrong): ${known.isEmpty ? '(none)' : known}

Return JSON only:
{
  "alternate_names": ["short or legal name variants people search"],
  "search_queries": [
    "high-signal Google/Bing image queries that a human would try",
    "include logo png / brand / transparent / official website variants"
  ],
  "official_domains": ["example.com"],
  "logo_url_hints": [
    "https://only-if-you-are-highly-confident-this-is-a-real-public-logo-URL"
  ],
  "notes": "brief"
}

Rules:
- Prefer real corporate domains (not wikipedia, linkedin, facebook, stock sites).
- search_queries: 4-10 concrete strings, English, tuned for image search.
- logo_url_hints: ZERO or few URLs; never invent plausible-looking fake CDN paths.
- If unsure about a direct logo URL, omit it and rely on search_queries/domains.
''';

    final data = await _generateJson(
      key: key,
      prompt: prompt,
      timeout: const Duration(seconds: 12),
    );
    if (data == null) return null;
    return GeminiLogoSearchPlan.fromJson(data);
  }

  /// Rank already-downloaded logo candidates for [companyHint] (best first).
  /// Returns preferred indices into [images], or null on failure (fail-open).
  Future<List<int>?> rankLogoCandidates(
    List<Uint8List> images, {
    String companyHint = '',
  }) async {
    final key = resolveApiKey();
    if (key.isEmpty || images.isEmpty || isTemporarilyUnavailable) return null;
    final capped = images.take(6).toList();
    if (capped.length == 1) return const [0];

    final prompt = '''
You are ranking logo candidates for a shipping-label picker${companyHint.isEmpty ? '' : ' for "$companyHint"'}.

Images are labeled Candidate 0 .. Candidate ${capped.length - 1} in order.
Prefer clean official wordmarks/icons with transparent or solid backgrounds.
Penalize photos, screenshots, watermarks, unrelated brands, and tiny favicons.

Return JSON only:
{
  "ranked_indices": [best_index, ..., worst_index],
  "best_reason": "short"
}
''';

    final parts = <Map<String, dynamic>>[];
    for (var i = 0; i < capped.length; i++) {
      parts.add({'text': 'Candidate $i:'});
      parts.add({
        'inline_data': {
          'mime_type': _guessMime(capped[i]),
          'data': base64Encode(_maybeDownscale(capped[i])),
        },
      });
    }
    parts.add({'text': prompt});

    final data = await _generateJson(
      key: key,
      prompt: '', // prompt already in parts
      partsOverride: parts,
      timeout: const Duration(seconds: 20),
    );
    if (data == null) return null;
    final raw = data['ranked_indices'];
    if (raw is! List || raw.isEmpty) return null;
    final out = <int>[];
    final seen = <int>{};
    for (final item in raw) {
      final idx = item is num ? item.toInt() : int.tryParse('$item');
      if (idx == null || idx < 0 || idx >= capped.length) continue;
      if (seen.add(idx)) out.add(idx);
    }
    // Append any missing indices so ranking stays a full permutation prefix.
    for (var i = 0; i < capped.length; i++) {
      if (seen.add(i)) out.add(i);
    }
    return out.isEmpty ? null : out;
  }

  /// Pre-recreate structural hints (brand, fonts, colors, layout).
  Future<GeminiRecreateHints?> analyzeForRecreate(Uint8List bytes) async {
    final key = resolveApiKey();
    if (key.isEmpty || bytes.isEmpty) return null;

    const prompt = '''
Analyze this logo PNG for high-quality vector tracing and cleanup.

Return JSON only:
{
  "brand_name": "ACME Energy",
  "font_family_guess": "geometric sans-serif",
  "dominant_colors_hex": ["#CE4E30", "#111111"],
  "layout_summary": "wordmark left of icon; horizontal lockup",
  "has_holes": true,
  "recommended_max_colors": 4,
  "notes": "strip studio backdrop; preserve counters"
}
''';

    final data = await _generateJson(
      key: key,
      prompt: prompt,
      imageBytes: bytes,
      mimeType: _guessMime(bytes),
    );
    if (data == null) return null;
    return GeminiRecreateHints.fromJson(data);
  }

  /// Recreate a low-res logo as a sharp high-resolution PNG (online Gemini).
  ///
  /// Throws if offline, unconfigured, or the model returns no image.
  Future<Uint8List> restoreLogoPng(
    Uint8List bytes, {
    List<String> addenda = const [],
    String? promptOverride,
  }) async {
    final key = resolveApiKey();
    if (key.isEmpty) {
      throw StateError('Logo restore needs internet and a Gemini API key.');
    }
    if (isTemporarilyUnavailable) {
      throw StateError('Gemini is busy — try logo restore again in a minute.');
    }
    if (bytes.isEmpty) {
      throw StateError('Logo file is empty.');
    }

    final models = <String>[
      imageModel,
      'gemini-3.1-flash-image',
      'gemini-2.5-flash-image',
      'gemini-3-pro-image',
      'gemini-2.5-flash-image-preview',
    ];
    final seen = <String>{};
    Object? lastError;
    for (final modelId in models) {
      final id = modelId.trim();
      if (id.isEmpty || !seen.add(id)) continue;
      try {
        final png = await _restoreLogoPngWithModel(
          key: key,
          modelId: id,
          bytes: bytes,
          addenda: addenda,
          promptOverride: promptOverride,
        );
        if (png != null && png.isNotEmpty) return png;
      } catch (e) {
        lastError = e;
      }
    }
    throw StateError(
      'Gemini could not restore this logo. Check your internet connection.'
      '${lastError == null ? '' : ' ($lastError)'}',
    );
  }

  static String get imageModel {
    _ensureEnvLoaded();
    final fromEnv = Platform.environment['GEMINI_IMAGE_MODEL']?.trim() ?? '';
    if (fromEnv.isNotEmpty) return fromEnv;
    final defined = AppConfig.geminiImageModel.trim();
    return defined.isEmpty ? 'gemini-3.1-flash-image' : defined;
  }

  /// Prompt used by [restoreLogoPng]. Kept in one place so lessons can append.
  static String restorePrompt({
    String brandNote = '',
    String outlineNote = '',
    List<String> addenda = const [],
  }) {
    final extra = addenda.isEmpty
        ? ''
        : '\n${addenda.map((e) => e.trim()).where((e) => e.isNotEmpty).join('\n')}\n';
    return '''
Restore this company logo the way a conservator restores a photograph: enhance
what is already there into a clean, high-resolution PNG. Do not invent a new
design.

Enhance the existing pixels — super-resolve and de-blur. Keep every letterform,
icon, curve, thickness, and proportion identical to the source.

Repair edges: remove JPEG stair-steps, pixelation, frayed rims, and white/gray
compression halo. Do not over-sharpen, cartoon, outline, or thicken strokes.

Repair blotchy patches: where a region is meant to be one solid brand color,
even it out. Do not posterize real gradients, photos, or intentional texture.

True alpha — never bake a black, white, gray, or checkerboard plate.
Keep every existing fill: grey, silver, black, white, and chromatic. Do not
recolor a lockup to its brightest accent.
If letters sit on a dark plate, they are ink — do not erase them.
COLORS: copy the source hues exactly — do not warm, cool, neon-shift, or invent
fills.$brandNote
If the source letters have a dark/black border or outline, keep that stroke.$outlineNote
No extra padding, mockups, drop shadows, or new text.
Never add Gemini, Google, Spark, Imagen, or any AI watermark, badge, sparkle,
or wordmark. The logo is unbranded — do not stamp a generator mark.
Do not invent a company name, tagline, or second lockup that is not in the source.
Output a high-definition PNG, 3000 pixels tall. Image only.
$extra''';
  }

  Future<Uint8List?> _restoreLogoPngWithModel({
    required String key,
    required String modelId,
    required Uint8List bytes,
    List<String> addenda = const [],
    String? promptOverride,
  }) async {
    final uri = Uri.parse(
      'https://generativelanguage.googleapis.com/v1beta/models/'
      '$modelId:generateContent?key=$key',
    );
    final src = img.decodeImage(bytes);
    final outline = src == null ? null : LogoImageProcessor.detectLetterOutline(src);
    final outlineNote = outline == null
        ? ''
        : '\nKeep the dark RGB(${outline.r},${outline.g},${outline.b}) '
            'border around the letters. It is not background.\n';
    final brandNote = LogoImageProcessor.brandColorPromptNote(bytes);
    final prompt = promptOverride ??
        restorePrompt(
          brandNote: brandNote,
          outlineNote: outlineNote,
          addenda: addenda,
        );
    final aspect = (src != null && src.height > 0)
        ? src.width / src.height
        : 1.0;
    final aspectToken = aspect >= 1.6
        ? '16:9'
        : aspect >= 1.25
            ? '4:3'
            : aspect <= 0.8
                ? '3:4'
                : '1:1';
    final payload = {
      'contents': [
        {
          'parts': [
            {
              'inline_data': {
                'mime_type': _guessMime(bytes),
                'data': base64Encode(bytes),
              },
            },
            {'text': prompt},
          ],
        },
      ],
      'generationConfig': {
        'responseModalities': ['TEXT', 'IMAGE'],
        'temperature': 0.1,
        'imageConfig': {
          'aspectRatio': aspectToken,
        },
      },
    };

    Future<http.Response> postPayload(Map<String, dynamic> body) {
      return _client
          .post(
            uri,
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode(body),
          )
          .timeout(const Duration(seconds: 120));
    }

    var res = await postPayload(payload);
    if (res.statusCode >= 400 &&
        (payload['generationConfig'] as Map).containsKey('imageConfig')) {
      (payload['generationConfig'] as Map).remove('imageConfig');
      res = await postPayload(payload);
    }
    if (res.statusCode == 429) {
      _tripQuotaCooldown();
      throw StateError('Gemini rate limit — try again shortly.');
    }
    if (_isQuotaOrTokenFailure(res.statusCode, res.body)) {
      _tripQuotaCooldown(const Duration(minutes: 10));
      throw StateError('Gemini quota/tokens exhausted — using local engine.');
    }
    if (res.statusCode < 200 || res.statusCode >= 300) {
      throw StateError('Gemini HTTP ${res.statusCode}');
    }
    final body = jsonDecode(res.body);
    if (body is! Map) return null;
    return _extractInlineImage(body);
  }

  /// Self-critique step: asks Gemini to grade [candidate] against [original]
  /// so a restore loop can retry instead of accepting a drifted redraw
  /// on the first try. Returns null on any request/parse failure — callers
  /// should treat that as "not verified," not "passed."
  Future<LogoMatchVerdict?> critiqueRestoreMatch(
    Uint8List original,
    Uint8List candidate,
  ) async {
    final key = resolveApiKey();
    if (key.isEmpty || isTemporarilyUnavailable) return null;
    const prompt = '''
Compare IMAGE_A (the original source) and IMAGE_B (a cleaned-up candidate
meant to be the same logo, just higher resolution and crisper). You are
checking fidelity, not aesthetics — a beautiful redraw that changed the
design is a FAIL.

Score 0-100 on how closely IMAGE_B preserves IMAGE_A's exact: letterforms and
their proportions, icon shape and geometry, layout/spacing between elements,
and colors (hue, not just "similar family"). Deduct heavily for anything
IMAGE_B added that is not in IMAGE_A (extra shading, bevels, glow, drop
shadows, chrome/gloss, changed background) or removed/altered from IMAGE_A.

Respond with strict JSON only:
{"score": <0-100 integer>, "pass": <bool, true only if score >= 90>,
 "issues": [<short strings, empty array if none>]}
''';
    final result = await _generateJson(
      key: key,
      prompt: prompt,
      partsOverride: [
        {
          'inline_data': {
            'mime_type': _guessMime(original),
            'data': base64Encode(_maybeDownscale(original)),
          },
        },
        {'text': 'IMAGE_A (original) above.'},
        {
          'inline_data': {
            'mime_type': _guessMime(candidate),
            'data': base64Encode(_maybeDownscale(candidate)),
          },
        },
        {'text': 'IMAGE_B (candidate) above.\n$prompt'},
      ],
    );
    if (result == null) return null;
    final score = result['score'];
    final pass = result['pass'];
    final issuesRaw = result['issues'];
    return LogoMatchVerdict(
      score: score is num ? score.toInt() : -1,
      pass: pass == true,
      issues: issuesRaw is List
          ? issuesRaw.map((e) => '$e').where((e) => e.isNotEmpty).toList()
          : const [],
    );
  }

  static Uint8List? _extractInlineImage(Map body) {
    try {
      final candidates = body['candidates'];
      if (candidates is! List || candidates.isEmpty) return null;
      final content = candidates.first['content'];
      if (content is! Map) return null;
      final parts = content['parts'];
      if (parts is! List) return null;
      for (final part in parts) {
        if (part is! Map) continue;
        final inline = part['inlineData'] ?? part['inline_data'];
        if (inline is! Map) continue;
        final mime = '${inline['mimeType'] ?? inline['mime_type'] ?? ''}'.toLowerCase();
        final data = '${inline['data'] ?? ''}';
        if (data.isEmpty) continue;
        if (mime.isNotEmpty &&
            !mime.contains('image') &&
            !mime.contains('png') &&
            !mime.contains('jpeg') &&
            !mime.contains('webp')) {
          continue;
        }
        return Uint8List.fromList(base64Decode(data));
      }
    } catch (_) {}
    return null;
  }

  Future<Map<String, dynamic>?> generateJson({
    required String prompt,
    Duration timeout = const Duration(seconds: 25),
  }) async {
    final key = resolveApiKey();
    if (key.isEmpty || isTemporarilyUnavailable) return null;
    return _generateJson(key: key, prompt: prompt, timeout: timeout);
  }

  Future<Map<String, dynamic>?> _generateJson({
    required String key,
    required String prompt,
    Uint8List? imageBytes,
    String? mimeType,
    List<Map<String, dynamic>>? partsOverride,
    Duration timeout = const Duration(seconds: 45),
  }) async {
    final uri = Uri.parse(
      'https://generativelanguage.googleapis.com/v1beta/models/'
      '$model:generateContent?key=$key',
    );

    final parts = partsOverride ??
        <Map<String, dynamic>>[
          if (imageBytes != null && imageBytes.isNotEmpty)
            {
              'inline_data': {
                'mime_type': mimeType ?? _guessMime(imageBytes),
                'data': base64Encode(_maybeDownscale(imageBytes)),
              },
            },
          if (prompt.isNotEmpty) {'text': prompt},
        ];

    final payload = {
      'contents': [
        {'parts': parts},
      ],
      'generationConfig': {
        'responseMimeType': 'application/json',
        'temperature': 0.2,
      },
    };

    Object? lastError;
    for (var attempt = 0; attempt < _maxRetries; attempt++) {
      try {
        final res = await _client
            .post(
              uri,
              headers: {'Content-Type': 'application/json'},
              body: jsonEncode(payload),
            )
            .timeout(timeout);
        if (res.statusCode == 429 || res.statusCode >= 500) {
          lastError = 'HTTP ${res.statusCode}';
          if (res.statusCode == 429) {
            // Don't burn retries across every logo candidate — cool down.
            _tripQuotaCooldown();
            break;
          }
          await Future<void>.delayed(
            Duration(milliseconds: (800 * math.pow(2, attempt)).toInt()),
          );
          continue;
        }
        if (_isQuotaOrTokenFailure(res.statusCode, res.body)) {
          lastError = 'quota/token HTTP ${res.statusCode}';
          _tripQuotaCooldown(const Duration(minutes: 10));
          break;
        }
        if (res.statusCode < 200 || res.statusCode >= 300) {
          lastError = 'HTTP ${res.statusCode}';
          return null;
        }
        final body = jsonDecode(res.body);
        if (body is! Map) return null;
        final text = _extractText(body);
        if (text == null || text.isEmpty) return null;
        return _parseJsonObject(text);
      } catch (e) {
        lastError = e;
        await Future<void>.delayed(
          Duration(milliseconds: (800 * math.pow(2, attempt)).toInt()),
        );
      }
    }
    assert(() {
      // ignore: avoid_print
      print('GeminiClient failed after retries: $lastError');
      return true;
    }());
    return null;
  }

  static String? _extractText(Map body) {
    try {
      final candidates = body['candidates'];
      if (candidates is! List || candidates.isEmpty) return null;
      final content = candidates.first['content'];
      if (content is! Map) return null;
      final parts = content['parts'];
      if (parts is! List || parts.isEmpty) return null;
      return '${parts.first['text'] ?? ''}';
    } catch (_) {
      return null;
    }
  }

  static Map<String, dynamic>? _parseJsonObject(String text) {
    var t = text.trim();
    final fence = RegExp(r'```(?:json)?\s*([\s\S]*?)```').firstMatch(t);
    if (fence != null) t = fence.group(1)!.trim();
    try {
      final decoded = jsonDecode(t);
      if (decoded is Map<String, dynamic>) return decoded;
      if (decoded is Map) return Map<String, dynamic>.from(decoded);
    } catch (_) {
      final start = t.indexOf('{');
      final end = t.lastIndexOf('}');
      if (start >= 0 && end > start) {
        try {
          final decoded = jsonDecode(t.substring(start, end + 1));
          if (decoded is Map) return Map<String, dynamic>.from(decoded);
        } catch (_) {}
      }
    }
    return null;
  }

  static String _guessMime(Uint8List b) {
    if (b.length >= 3 && b[0] == 0xFF && b[1] == 0xD8 && b[2] == 0xFF) {
      return 'image/jpeg';
    }
    if (b.length >= 8 &&
        b[0] == 0x89 &&
        b[1] == 0x50 &&
        b[2] == 0x4E &&
        b[3] == 0x47) {
      return 'image/png';
    }
    if (b.length >= 4 &&
        b[0] == 0x52 &&
        b[1] == 0x49 &&
        b[2] == 0x46 &&
        b[3] == 0x46) {
      return 'image/webp';
    }
    return 'image/png';
  }

  /// Keep payloads small for Gemini inline_data limits.
  static Uint8List _maybeDownscale(Uint8List bytes) {
    if (bytes.length <= 900 * 1024) return bytes;
    // Already compressed enough for most logos; truncate is unsafe — return as-is
    // and let the API reject if huge. Callers download picker-sized images.
    return bytes;
  }
}

/// Verdict from [GeminiClient.critiqueRestoreMatch].
class LogoMatchVerdict {
  const LogoMatchVerdict({
    required this.score,
    required this.pass,
    this.issues = const [],
  });

  /// 0-100, or -1 if the model didn't return a usable number.
  final int score;
  final bool pass;
  final List<String> issues;
}

class GeminiLogoValidation {
  const GeminiLogoValidation({
    required this.isValidLogo,
    required this.hasTransparentOrSolidBackground,
    required this.confidenceScore,
    this.reason = '',
  });

  final bool isValidLogo;
  final bool hasTransparentOrSolidBackground;
  final double confidenceScore;
  final String reason;

  factory GeminiLogoValidation.fromJson(Map<String, dynamic> json) {
    return GeminiLogoValidation(
      isValidLogo: json['is_valid_logo'] == true,
      hasTransparentOrSolidBackground:
          json['has_transparent_or_solid_background'] == true,
      confidenceScore: (json['confidence_score'] is num)
          ? (json['confidence_score'] as num).toDouble()
          : 0.0,
      reason: '${json['reason'] ?? ''}',
    );
  }

  bool get shouldKeep => isValidLogo && confidenceScore >= 0.45;
}

/// Gemini-assisted search enrichment for the logo crawler (fail-open).
class GeminiLogoSearchPlan {
  const GeminiLogoSearchPlan({
    this.alternateNames = const [],
    this.searchQueries = const [],
    this.officialDomains = const [],
    this.logoUrlHints = const [],
    this.notes = '',
  });

  final List<String> alternateNames;
  final List<String> searchQueries;
  final List<String> officialDomains;
  final List<String> logoUrlHints;
  final String notes;

  factory GeminiLogoSearchPlan.fromJson(Map<String, dynamic> json) {
    List<String> strList(dynamic raw) {
      if (raw is! List) return const [];
      final out = <String>[];
      for (final item in raw) {
        final s = '$item'.trim();
        if (s.isNotEmpty) out.add(s);
      }
      return out;
    }

    return GeminiLogoSearchPlan(
      alternateNames: strList(json['alternate_names']),
      searchQueries: strList(json['search_queries']),
      officialDomains: strList(json['official_domains']),
      logoUrlHints: strList(json['logo_url_hints'])
          .where((u) => u.startsWith('http'))
          .toList(),
      notes: '${json['notes'] ?? ''}',
    );
  }

  bool get isEmpty =>
      alternateNames.isEmpty &&
      searchQueries.isEmpty &&
      officialDomains.isEmpty &&
      logoUrlHints.isEmpty;
}

class GeminiRecreateHints {
  const GeminiRecreateHints({
    this.brandName = '',
    this.fontFamilyGuess = '',
    this.dominantColorsHex = const [],
    this.layoutSummary = '',
    this.hasHoles = true,
    this.recommendedMaxColors,
    this.notes = '',
  });

  final String brandName;
  final String fontFamilyGuess;
  final List<String> dominantColorsHex;
  final String layoutSummary;
  final bool hasHoles;
  final int? recommendedMaxColors;
  final String notes;

  factory GeminiRecreateHints.fromJson(Map<String, dynamic> json) {
    final colors = <String>[];
    final raw = json['dominant_colors_hex'] ?? json['brand_colors_hex'];
    if (raw is List) {
      for (final c in raw) {
        final s = '$c'.trim();
        if (s.isNotEmpty) colors.add(s);
      }
    }
    int? maxColors;
    final mc = json['recommended_max_colors'];
    if (mc is num) maxColors = mc.toInt();
    return GeminiRecreateHints(
      brandName: '${json['brand_name'] ?? ''}',
      fontFamilyGuess:
          '${json['font_family_guess'] ?? json['font_family'] ?? ''}',
      dominantColorsHex: colors,
      layoutSummary: '${json['layout_summary'] ?? ''}',
      hasHoles: json['has_holes'] != false,
      recommendedMaxColors: maxColors,
      notes: '${json['notes'] ?? ''}',
    );
  }
}
