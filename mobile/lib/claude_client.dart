import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:http/http.dart' as http;
import 'package:path/path.dart' as p;

import 'bulk/bulk_label_models.dart';

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

  /// Reads every order line on a Swift Order Acknowledgement / packing list
  /// and returns Claude's own take on CPO reference, PO#, identity
  /// (TAG#/PART#/ITEM#), and quantity for each — with reasoning, always.
  /// This is a "common sense" second pass that runs on every parse, not just
  /// as a fallback when the regex parser comes up empty: it reads the whole
  /// document the way a person would, so it catches layouts the regex
  /// vocabulary doesn't know yet. Reconciliation against the regex result
  /// happens in `JobPdfAi.enrichLines` — this method never decides anything
  /// on its own, it only reports what it sees.
  ///
  /// Returns null on any failure (unconfigured, network, unparsable) —
  /// never thrown, never treated as a pass.
  Future<List<ClaudeOrderAckLine>?> extractOrderAckLines(String text) async {
    final key = resolveApiKey();
    if (key.isEmpty) return null;

    final snippet = text.length > 14000 ? text.substring(0, 14000) : text;
    final prompt = '''
You are reading a Swift Oilfield Supply Order Acknowledgement (or packing
list). Swift ships fittings/flanges/valves to customers, and prints one
Avery sticker per order line to identify it in the warehouse. Your job is to
read every order line and report, for each one:

- The CPO reference: the customer's own PO line number(s) for that row,
  usually noted as "CPO LINE 4", "CPO LINES 1-4" (a range), or
  "CPO LINE 8, 9" (a list) — copy it EXACTLY as printed, including the
  range/list punctuation. A range or list written together on one note is
  ONE reference, not several.
- The PO# for that row (usually the same PO# repeated on every line, but
  check — do not assume).
- The identity field: whichever of TAG#, PART#, or ITEM# is printed for that
  row (terminology varies by customer/salesperson), and its value.
- The quantity ordered for that row.
- Your reasoning: a short, specific note on how you determined each value,
  or what's ambiguous about the row and how you resolved it using common
  sense (e.g. "no Order Line Notes block at all for this row — nothing to
  tag" or "identity note wrapped across a page break, matched it back to
  this row by position").
- Your confidence: "high" if the row's CPO/PO/identity are printed plainly,
  "low" if you had to infer or guess.

If a row genuinely has no CPO note, no identity, or both, say so plainly in
reasoning and use empty strings for the missing field(s) — do not invent
data. Skip rows that are pure header/subtotal/freight noise, not real order
lines.

Document text:
"""
$snippet
"""

Respond with strict JSON only, no other text — an array, one object per
order line:
[
  {
    "cpo": "1-4",
    "po": "P613979",
    "id_kind": "TAG" | "PART" | "ITEM" | "",
    "id_value": "033047",
    "quantity": 7,
    "reasoning": "short explanation",
    "confidence": "high" | "low"
  }
]
''';

    try {
      final uri = Uri.parse('https://api.anthropic.com/v1/messages');
      final payload = {
        'model': model,
        'max_tokens': 4096,
        'messages': [
          {
            'role': 'user',
            'content': [
              {'type': 'text', 'text': prompt},
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
          .timeout(const Duration(seconds: 60));
      if (res.statusCode < 200 || res.statusCode >= 300) return null;

      final body = jsonDecode(res.body);
      if (body is! Map) return null;
      final content = body['content'];
      if (content is! List || content.isEmpty) return null;
      String? text0;
      for (final block in content) {
        if (block is Map && block['type'] == 'text') {
          text0 = '${block['text'] ?? ''}';
          break;
        }
      }
      if (text0 == null || text0.trim().isEmpty) return null;

      final jsonStart = text0.indexOf('[');
      final jsonEnd = text0.lastIndexOf(']');
      if (jsonStart < 0 || jsonEnd <= jsonStart) return null;
      final parsed = jsonDecode(text0.substring(jsonStart, jsonEnd + 1));
      if (parsed is! List) return null;

      final out = <ClaudeOrderAckLine>[];
      for (final item in parsed) {
        if (item is! Map) continue;
        String s(String k) => '${item[k] ?? ''}'.trim();
        final qtyRaw = item['quantity'];
        final quantity = qtyRaw is num
            ? qtyRaw.round()
            : int.tryParse('$qtyRaw'.trim()) ?? 0;
        out.add(
          ClaudeOrderAckLine(
            cpoDisplay: s('cpo'),
            poNumber: s('po'),
            idKind: _idKindFromClaude(s('id_kind')),
            idValue: s('id_value'),
            quantity: quantity,
            reasoning: s('reasoning'),
            confidence: s('confidence').toLowerCase() == 'high'
                ? 'high'
                : 'low',
          ),
        );
      }
      return out;
    } catch (_) {
      return null;
    }
  }

  /// Reads the header of a Swift Order Acknowledgement / packing list — the
  /// same "common sense" pass as [extractOrderAckLines], but for the
  /// document-level fields (customer, PO#, sales order, project, ship-to,
  /// requisitioner, AFE, carrier) that every document type (Shipping,
  /// Receiving, BOL, Bulk) pre-fills from. Runs on every upload, not just as
  /// a fallback — `JobPdfAi.enrich` reconciles it against the regex/Gemini
  /// result and never silently overrides a value they already found.
  ///
  /// Returns null on any failure — never thrown, never treated as a pass.
  Future<ClaudeOrderAckHeader?> extractOrderAckHeader(String text) async {
    final key = resolveApiKey();
    if (key.isEmpty) return null;

    final snippet = text.length > 9000 ? text.substring(0, 9000) : text;
    final prompt = '''
You are reading the header of a Swift Oilfield Supply Order Acknowledgement
(or packing list) — the customer/job info block above the line-item table,
not the line items themselves. Extract, using common sense about how these
documents are laid out (values can wrap across lines, be mashed together
with no spaces, or use a customer-specific term for the same thing):

- customer_name: the Bill To company name (no account number prefix)
- po_number: the customer's PO # exactly as printed (keep dots/hyphens,
  don't truncate)
- sales_order: Swift's own order/sales-order number
- project_number: the Project column value if separate from the PO
- job_location: Location / site / LSD column if present
- requisitioner: the requisitioner/approver name
- afe_number: AFE # if present
- ship_to_name / ship_to_address: the actual delivery destination — prefer
  Delivery Instructions over the Ship To header if both are present and
  differ
- carrier: freight carrier / "ship via" line if present
- packing_slip_number: only if this document is a packing list

Document text:
"""
$snippet
"""

Respond with strict JSON only, no other text:
{
  "customer_name": "", "po_number": "", "sales_order": "",
  "project_number": "", "job_location": "", "requisitioner": "",
  "afe_number": "", "ship_to_name": "", "ship_to_address": "",
  "carrier": "", "packing_slip_number": "",
  "reasoning": "short note on anything ambiguous or wrapped/mashed that you
    had to puzzle out, or empty string if the header was straightforward",
  "flags": ["field names you're not confident about, empty array if none"]
}
''';

    try {
      final uri = Uri.parse('https://api.anthropic.com/v1/messages');
      final payload = {
        'model': model,
        'max_tokens': 1024,
        'messages': [
          {
            'role': 'user',
            'content': [
              {'type': 'text', 'text': prompt},
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
      String? text0;
      for (final block in content) {
        if (block is Map && block['type'] == 'text') {
          text0 = '${block['text'] ?? ''}';
          break;
        }
      }
      if (text0 == null || text0.trim().isEmpty) return null;

      final jsonStart = text0.indexOf('{');
      final jsonEnd = text0.lastIndexOf('}');
      if (jsonStart < 0 || jsonEnd <= jsonStart) return null;
      final parsed = jsonDecode(text0.substring(jsonStart, jsonEnd + 1));
      if (parsed is! Map) return null;

      String s(String k) => '${parsed[k] ?? ''}'.trim();
      final flagsRaw = parsed['flags'];
      return ClaudeOrderAckHeader(
        customerName: s('customer_name'),
        poNumber: s('po_number'),
        orderNumber: s('sales_order'),
        projectNumber: s('project_number'),
        jobLocation: s('job_location'),
        requisitioner: s('requisitioner'),
        afeNumber: s('afe_number'),
        shipToName: s('ship_to_name'),
        shipToAddress: s('ship_to_address'),
        carrier: s('carrier'),
        packingSlipNumber: s('packing_slip_number'),
        reasoning: s('reasoning'),
        flags: flagsRaw is List
            ? flagsRaw.map((e) => '$e').where((e) => e.isNotEmpty).toList()
            : const [],
      );
    } catch (_) {
      return null;
    }
  }

  static BulkIdKind? _idKindFromClaude(String raw) {
    final k = raw.trim().toUpperCase();
    if (k.startsWith('TAG')) return BulkIdKind.tag;
    if (k.startsWith('PART')) return BulkIdKind.part;
    if (k.startsWith('ITEM')) return BulkIdKind.item;
    return null;
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

/// Claude's own read of one order line from [ClaudeClient.extractOrderAckLines].
/// Always carries [reasoning] — reconciliation against the regex parser's
/// result happens in `JobPdfAi.enrichLines`, never here.
class ClaudeOrderAckLine {
  const ClaudeOrderAckLine({
    required this.cpoDisplay,
    required this.poNumber,
    required this.idKind,
    required this.idValue,
    required this.quantity,
    required this.reasoning,
    this.confidence = 'low',
  });

  final String cpoDisplay;
  final String poNumber;

  /// Null when Claude couldn't identify TAG#/PART#/ITEM# for this row.
  final BulkIdKind? idKind;
  final String idValue;
  final int quantity;
  final String reasoning;

  /// "high" or "low".
  final String confidence;
}

/// Claude's own read of an OA/packing-list header from
/// [ClaudeClient.extractOrderAckHeader]. Fields are empty strings when
/// Claude didn't find them — never null, so callers can treat this like any
/// other header source in a `preferField`-style merge.
class ClaudeOrderAckHeader {
  const ClaudeOrderAckHeader({
    this.customerName = '',
    this.poNumber = '',
    this.orderNumber = '',
    this.projectNumber = '',
    this.jobLocation = '',
    this.requisitioner = '',
    this.afeNumber = '',
    this.shipToName = '',
    this.shipToAddress = '',
    this.carrier = '',
    this.packingSlipNumber = '',
    this.reasoning = '',
    this.flags = const [],
  });

  final String customerName;
  final String poNumber;
  final String orderNumber;
  final String projectNumber;
  final String jobLocation;
  final String requisitioner;
  final String afeNumber;
  final String shipToName;
  final String shipToAddress;
  final String carrier;
  final String packingSlipNumber;

  /// Short note on anything ambiguous/wrapped Claude had to puzzle out.
  final String reasoning;

  /// Field names (customer_name, po_number, …) Claude is unsure about.
  final List<String> flags;
}
