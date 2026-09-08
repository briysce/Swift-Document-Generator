import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:http/http.dart' as http;
import 'package:path/path.dart' as p;

/// Second-opinion logo-restore critique via the Claude API — runs alongside
/// [GeminiClient.critiqueRestoreMatch] so one model's blind spot doesn't
/// silently pass a drifted redraw. Same shape of verdict, independent model.
///
/// Credentials resolve the same way as Gemini's client (first hit wins):
/// 1. `--dart-define=ANTHROPIC_API_KEY=...`
/// 2. Process environment `ANTHROPIC_API_KEY`
/// 3. Gitignored `.env` / `.env.local` next to the app / repo root
class ClaudeClient {
  ClaudeClient({http.Client? client}) : _client = client ?? http.Client();

  final http.Client _client;

  static const _defaultModel = 'claude-sonnet-5';
  static const _apiVersion = '2023-06-01';

  static String? _cachedKey;
  static bool _envLoaded = false;
  static final _envOverlay = <String, String>{};

  static bool get isConfigured => resolveApiKey().isNotEmpty;

  static String resolveApiKey() {
    if (_cachedKey != null && _cachedKey!.isNotEmpty) return _cachedKey!;
    _ensureEnvLoaded();
    final candidates = <String>[
      const String.fromEnvironment('ANTHROPIC_API_KEY'),
      Platform.environment['ANTHROPIC_API_KEY'] ?? '',
      _envOverlay['ANTHROPIC_API_KEY'] ?? '',
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
    final fromEnv = Platform.environment['ANTHROPIC_MODEL']?.trim() ?? '';
    if (fromEnv.isNotEmpty) return fromEnv;
    final fromDotEnv = (_envOverlay['ANTHROPIC_MODEL'] ?? '').trim();
    if (fromDotEnv.isNotEmpty) return fromDotEnv;
    return _defaultModel;
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
          if ((Platform.environment[key] ?? '').trim().isEmpty) {
            _envOverlay.putIfAbsent(key, () => value);
          }
        }
      }
    } catch (_) {}
  }

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
  }

  /// Same rubric/response shape as [GeminiClient.critiqueRestoreMatch] so
  /// callers can combine both verdicts uniformly. Null on any request/parse
  /// failure — "not verified," never treated as a pass.
  Future<ClaudeMatchVerdict?> critiqueRestoreMatch(
    Uint8List original,
    Uint8List candidate,
  ) async {
    final key = resolveApiKey();
    if (key.isEmpty) return null;

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

Respond with strict JSON only, no other text:
{"score": <0-100 integer>, "pass": <bool, true only if score >= 90>,
 "issues": [<short strings, empty array if none>]}
''';

    try {
      final uri = Uri.parse('https://api.anthropic.com/v1/messages');
      final payload = {
        'model': model,
        'max_tokens': 512,
        'messages': [
          {
            'role': 'user',
            'content': [
              {
                'type': 'image',
                'source': {
                  'type': 'base64',
                  'media_type': _guessMime(original),
                  'data': base64Encode(original),
                },
              },
              {'type': 'text', 'text': 'IMAGE_A (original) above.'},
              {
                'type': 'image',
                'source': {
                  'type': 'base64',
                  'media_type': _guessMime(candidate),
                  'data': base64Encode(candidate),
                },
              },
              {'type': 'text', 'text': 'IMAGE_B (candidate) above.\n$prompt'},
            ],
          },
        ],
      };

      final res = await _client
          .post(
            uri,
            headers: {
              'Content-Type': 'application/json',
              'x-api-key': key,
              'anthropic-version': _apiVersion,
            },
            body: jsonEncode(payload),
          )
          .timeout(const Duration(seconds: 45));
      if (res.statusCode < 200 || res.statusCode >= 300) return null;

      final body = jsonDecode(res.body);
      if (body is! Map) return null;
      final content = body['content'];
      if (content is! List || content.isEmpty) return null;
      String? text;
      for (final block in content) {
        if (block is Map && block['type'] == 'text') {
          text = '${block['text'] ?? ''}';
          break;
        }
      }
      if (text == null || text.trim().isEmpty) return null;

      final jsonStart = text.indexOf('{');
      final jsonEnd = text.lastIndexOf('}');
      if (jsonStart < 0 || jsonEnd <= jsonStart) return null;
      final parsed = jsonDecode(text.substring(jsonStart, jsonEnd + 1));
      if (parsed is! Map) return null;

      final score = parsed['score'];
      final pass = parsed['pass'];
      final issuesRaw = parsed['issues'];
      return ClaudeMatchVerdict(
        score: score is num ? score.toInt() : -1,
        pass: pass == true,
        issues: issuesRaw is List
            ? issuesRaw.map((e) => '$e').where((e) => e.isNotEmpty).toList()
            : const [],
      );
    } catch (_) {
      return null;
    }
  }

  static String _guessMime(Uint8List bytes) {
    if (bytes.length >= 8 &&
        bytes[0] == 0x89 &&
        bytes[1] == 0x50 &&
        bytes[2] == 0x4E &&
        bytes[3] == 0x47) {
      return 'image/png';
    }
    if (bytes.length >= 3 &&
        bytes[0] == 0xFF &&
        bytes[1] == 0xD8 &&
        bytes[2] == 0xFF) {
      return 'image/jpeg';
    }
    return 'image/png';
  }
}

/// Verdict from [ClaudeClient.critiqueRestoreMatch].
class ClaudeMatchVerdict {
  const ClaudeMatchVerdict({
    required this.score,
    required this.pass,
    this.issues = const [],
  });

  /// 0-100, or -1 if the model didn't return a usable number.
  final int score;
  final bool pass;
  final List<String> issues;
}
