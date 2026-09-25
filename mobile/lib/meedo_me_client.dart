import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import 'app_config.dart';

/// Meedo-Me — the project's local AI runtime.
///
/// Meedo-Me is our fork of Jan (Menlo Research, Apache-2.0) together with its
/// agent runtime, Tokamak. Jan exposes an OpenAI-compatible HTTP API, so this
/// client speaks that shape: `/v1/models`, `/v1/chat/completions`, and the same
/// `image_url` content parts for vision. Anything OpenAI-compatible can stand
/// in, which is deliberate — it keeps us off a single vendor rather than simply
/// swapping one for another.
///
/// Why it replaces the hosted clients
/// ----------------------------------
/// Gemini and Claude were doing two separable jobs here: reading text out of an
/// Order Acknowledgement, and judging logo candidates from an image. Both are
/// ordinary model work that a local runtime does perfectly well, and running
/// them locally removes per-call cost, quota cliffs, and sending customer
/// paperwork to a third party.
///
/// Where it runs
/// -------------
/// Meedo-Me is a desktop runtime listening on `localhost:1337` by default. That
/// is a real constraint worth stating plainly: a phone has no local server, so
/// on Android the base URL must point at a reachable host on the network
/// (`--dart-define=MEEDO_ME_BASE_URL=http://192.168.1.x:1337/v1`) or these
/// features simply stay unavailable. They are not required for any document to
/// generate.
///
/// Fail-open, always
/// -----------------
/// Every method returns null or an empty result when the runtime is unreachable,
/// the model is not loaded, or the reply does not parse. The local restore and
/// parsing engines are the product; the model is an assist. A model that is
/// down must never block a shipping label.
class MeedoMeClient {
  MeedoMeClient({http.Client? client}) : _client = client ?? http.Client();

  final http.Client _client;

  /// After repeated failures, stop dialling for a cool-down so batch work
  /// (crawlers, bulk parsing) does not stall on a runtime that is not running.
  static DateTime? _cooldownUntil;

  static bool get isTemporarilyUnavailable {
    final until = _cooldownUntil;
    return until != null && DateTime.now().isBefore(until);
  }

  static void _tripCooldown([Duration d = const Duration(minutes: 3)]) {
    _cooldownUntil = DateTime.now().add(d);
  }

  static void resetCooldown() => _cooldownUntil = null;

  /// Base URL of the Meedo-Me runtime, including the `/v1` suffix.
  static String resolveBaseUrl() {
    final define = AppConfig.meedoMeBaseUrl.trim();
    if (define.isNotEmpty) return _normalizeBase(define);
    final env = envValue('MEEDO_ME_BASE_URL');
    if (env.isNotEmpty) return _normalizeBase(env);
    return 'http://localhost:1337/v1';
  }

  static String _normalizeBase(String raw) {
    var v = raw.trim();
    while (v.endsWith('/')) {
      v = v.substring(0, v.length - 1);
    }
    return v.endsWith('/v1') ? v : '$v/v1';
  }

  /// Optional bearer token. Jan does not require one locally, but a shared
  /// Meedo-Me host on the network should.
  static String resolveApiKey() {
    final define = AppConfig.meedoMeApiKey.trim();
    if (define.isNotEmpty) return define;
    return envValue('MEEDO_ME_API_KEY');
  }

  static String get model {
    final define = AppConfig.meedoMeModel.trim();
    if (define.isNotEmpty) return define;
    final env = envValue('MEEDO_ME_MODEL');
    return env.isNotEmpty ? env : 'qwen3:8b';
  }

  /// Vision-capable model. Judging a logo candidate needs one; text extraction
  /// does not, and loading a vision model for text work is wasted memory.
  static String get visionModel {
    final define = AppConfig.meedoMeVisionModel.trim();
    if (define.isNotEmpty) return define;
    final env = envValue('MEEDO_ME_VISION_MODEL');
    return env.isNotEmpty ? env : 'qwen2.5-vl:7b';
  }

  // --------------------------------------------------------------------
  // env overlay (same resolution order the hosted clients used)
  // --------------------------------------------------------------------

  static final Map<String, String> _envOverlay = {};
  static bool _envLoaded = false;

  static String envValue(String key) {
    _ensureEnvLoaded();
    final fromEnv = Platform.environment[key];
    if (fromEnv != null && fromEnv.trim().isNotEmpty) return fromEnv.trim();
    return (_envOverlay[key] ?? '').trim();
  }

  static void _ensureEnvLoaded() {
    if (_envLoaded) return;
    _envLoaded = true;
    for (final path in _envCandidatePaths()) {
      try {
        final f = File(path);
        if (!f.existsSync()) continue;
        for (final line in f.readAsLinesSync()) {
          final t = line.trim();
          if (t.isEmpty || t.startsWith('#')) continue;
          final i = t.indexOf('=');
          if (i <= 0) continue;
          final k = t.substring(0, i).trim();
          var v = t.substring(i + 1).trim();
          if (v.length >= 2 &&
              ((v.startsWith('"') && v.endsWith('"')) ||
                  (v.startsWith("'") && v.endsWith("'")))) {
            v = v.substring(1, v.length - 1);
          }
          _envOverlay.putIfAbsent(k, () => v);
        }
      } catch (_) {
        // Unreadable .env is not an error — the defaults still apply.
      }
    }
  }

  static Iterable<String> _envCandidatePaths() sync* {
    try {
      final exeDir = File(Platform.resolvedExecutable).parent.path;
      yield '$exeDir${Platform.pathSeparator}.env';
      yield '$exeDir${Platform.pathSeparator}.env.local';
    } catch (_) {
      // resolvedExecutable is unavailable in some test hosts.
    }
    final cwd = Directory.current.path;
    yield '$cwd${Platform.pathSeparator}.env';
    yield '$cwd${Platform.pathSeparator}.env.local';
    yield '$cwd${Platform.pathSeparator}..${Platform.pathSeparator}.env';
  }

  // --------------------------------------------------------------------
  // core call
  // --------------------------------------------------------------------

  Map<String, String> get _headers {
    final key = resolveApiKey();
    return {
      'Content-Type': 'application/json',
      if (key.isNotEmpty) 'Authorization': 'Bearer $key',
    };
  }

  /// True when a Meedo-Me runtime answers and has at least one model loaded.
  Future<bool> isReachable({Duration timeout = const Duration(seconds: 4)}) async {
    if (isTemporarilyUnavailable) return false;
    try {
      final r = await _client
          .get(Uri.parse('${resolveBaseUrl()}/models'), headers: _headers)
          .timeout(timeout);
      return r.statusCode >= 200 && r.statusCode < 300;
    } catch (_) {
      return false;
    }
  }

  /// One chat completion. Returns the assistant text, or null on any failure.
  ///
  /// [images] are attached as OpenAI-style base64 `image_url` parts, which is
  /// what Jan expects for a vision model.
  Future<String?> complete({
    required String prompt,
    String? system,
    List<Uint8List> images = const [],
    double temperature = 0.0,
    int maxTokens = 2048,
    bool json = false,
    Duration timeout = const Duration(seconds: 120),
  }) async {
    if (isTemporarilyUnavailable) return null;

    final content = <Map<String, dynamic>>[
      {'type': 'text', 'text': prompt},
      for (final bytes in images)
        {
          'type': 'image_url',
          'image_url': {
            'url': 'data:${_guessMime(bytes)};base64,${base64Encode(bytes)}',
          },
        },
    ];

    final body = <String, dynamic>{
      'model': images.isEmpty ? model : visionModel,
      'messages': [
        if (system != null && system.trim().isNotEmpty)
          {'role': 'system', 'content': system},
        {'role': 'user', 'content': content},
      ],
      'temperature': temperature,
      'max_tokens': maxTokens,
      'stream': false,
      if (json) 'response_format': {'type': 'json_object'},
    };

    http.Response r;
    try {
      r = await _client
          .post(
            Uri.parse('${resolveBaseUrl()}/chat/completions'),
            headers: _headers,
            body: jsonEncode(body),
          )
          .timeout(timeout);
    } catch (_) {
      // Runtime not running is the common case, not an exception worth raising.
      _tripCooldown();
      return null;
    }

    if (r.statusCode < 200 || r.statusCode >= 300) {
      // 404 usually means the named model is not pulled; both are the operator's
      // problem to fix, and neither should stall the caller meanwhile.
      _tripCooldown();
      return null;
    }

    try {
      final decoded = jsonDecode(r.body);
      if (decoded is! Map) return null;
      final choices = decoded['choices'];
      if (choices is! List || choices.isEmpty) return null;
      final msg = (choices.first as Map)['message'];
      if (msg is! Map) return null;
      final text = msg['content'];
      return text is String && text.trim().isNotEmpty ? text : null;
    } catch (_) {
      return null;
    }
  }

  /// Chat completion parsed as a JSON object, or null.
  ///
  /// Local models wrap JSON in prose or fences more often than hosted ones do,
  /// so the first balanced object in the reply is extracted rather than
  /// requiring the whole response to parse.
  Future<Map<String, dynamic>?> completeJson({
    required String prompt,
    String? system,
    List<Uint8List> images = const [],
    int maxTokens = 2048,
    Duration timeout = const Duration(seconds: 120),
  }) async {
    final text = await complete(
      prompt: prompt,
      system: system,
      images: images,
      maxTokens: maxTokens,
      json: true,
      timeout: timeout,
    );
    if (text == null) return null;
    final obj = extractJsonObject(text);
    return obj;
  }

  /// First balanced `{...}` in [raw], decoded. Null when there is none.
  static Map<String, dynamic>? extractJsonObject(String raw) {
    var s = raw.trim();
    if (s.startsWith('```')) {
      final nl = s.indexOf('\n');
      if (nl > 0) s = s.substring(nl + 1);
      final fence = s.lastIndexOf('```');
      if (fence > 0) s = s.substring(0, fence);
      s = s.trim();
    }
    final start = s.indexOf('{');
    if (start < 0) return null;
    var depth = 0;
    var inStr = false;
    var esc = false;
    for (var i = start; i < s.length; i++) {
      final c = s[i];
      if (inStr) {
        if (esc) {
          esc = false;
        } else if (c == r'\') {
          esc = true;
        } else if (c == '"') {
          inStr = false;
        }
        continue;
      }
      if (c == '"') {
        inStr = true;
      } else if (c == '{') {
        depth++;
      } else if (c == '}') {
        depth--;
        if (depth == 0) {
          try {
            final v = jsonDecode(s.substring(start, i + 1));
            return v is Map<String, dynamic> ? v : null;
          } catch (_) {
            return null;
          }
        }
      }
    }
    return null;
  }

  static String _guessMime(Uint8List b) {
    if (b.length >= 8 &&
        b[0] == 0x89 &&
        b[1] == 0x50 &&
        b[2] == 0x4E &&
        b[3] == 0x47) {
      return 'image/png';
    }
    if (b.length >= 3 && b[0] == 0xFF && b[1] == 0xD8 && b[2] == 0xFF) {
      return 'image/jpeg';
    }
    if (b.length >= 12 &&
        b[0] == 0x52 &&
        b[1] == 0x49 &&
        b[2] == 0x46 &&
        b[3] == 0x46) {
      return 'image/webp';
    }
    return 'image/png';
  }

  void close() => _client.close();
}
