import 'dart:convert';

import 'bulk/bulk_label_models.dart';
import 'claude_client.dart';
import 'meedo_me_client.dart';

/// Model-assisted reading of a Swift Order Acknowledgement / packing list.
///
/// This was previously bound to Anthropic's HTTP API. The prompts were the
/// valuable part — they encode a lot of hard-won knowledge about how these
/// documents are actually laid out — so they are carried over verbatim and only
/// the transport is chosen at runtime.
///
/// Meedo-Me, our local runtime, is tried first: it keeps customer paperwork off
/// a third-party service and has no per-call cost or quota cliff. The hosted
/// Claude client is kept and used as a fallback, because a local runtime that
/// is not running is a real and ordinary situation — someone on a laptop
/// without it installed, or a phone with no reachable host — and falling back
/// beats losing the pass entirely. Local-first, hosted when it has to be.
///
/// Every method returns null on any failure. `JobPdfAi` reconciles whatever
/// comes back against the regex parser's result and never lets a model silently
/// override a value the deterministic pass already found. A model being
/// unavailable must never stop a label from printing.
class OrderAckAi {
  OrderAckAi({MeedoMeClient? client}) : _client = client ?? MeedoMeClient();

  final MeedoMeClient _client;

  /// Whether any backend could answer.
  ///
  /// A local runtime needs no API key, so unlike the hosted client there is no
  /// credential to check — we attempt, and the client's cooldown stops repeated
  /// dialling when nothing is listening. A refused local connection is fast.
  /// Hosted Claude counts too, since it is the fallback.
  static bool get isConfigured =>
      !MeedoMeClient.isTemporarilyUnavailable || ClaudeClient.isConfigured;

  /// Which backend answered last. Surfaced in the reconciliation notes so an
  /// operator can tell whether a reading came from the local runtime or the
  /// hosted model.
  String lastBackend = '';

  /// Reads every order line: CPO reference, PO#, identity field, quantity.
  ///
  /// Returns null on any failure, never throws.
  Future<List<AiOrderAckLine>?> extractOrderAckLines(String text) async {
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

Respond with strict JSON only, no other text — an object with one key
"lines" holding an array, one object per order line:
{
  "lines": [
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
}
''';

    // Wrapped in an object rather than a bare array: JSON mode on an
    // OpenAI-compatible runtime returns an object, and a local model asked for
    // a top-level array will often wrap it anyway. Both shapes are accepted
    // below so neither habit costs us the parse.
    final raw = await _client.complete(
      prompt: prompt,
      maxTokens: 4096,
      json: true,
      timeout: const Duration(seconds: 180),
    );
    final items = raw == null ? null : _extractLineArray(raw);
    if (items == null) {
      // Local runtime absent or unusable — use the hosted model rather than
      // dropping the pass. Its own prompt is equivalent.
      final viaClaude = await _claudeLines(text);
      if (viaClaude != null) lastBackend = 'Claude';
      return viaClaude;
    }
    lastBackend = 'Meedo-Me';

    final out = <AiOrderAckLine>[];
    for (final item in items) {
      if (item is! Map) continue;
      String s(String k) => '${item[k] ?? ''}'.trim();
      final qtyRaw = item['quantity'];
      final quantity =
          qtyRaw is num ? qtyRaw.round() : int.tryParse('$qtyRaw'.trim()) ?? 0;
      out.add(
        AiOrderAckLine(
          cpoDisplay: s('cpo'),
          poNumber: s('po'),
          idKind: idKindFromLabel(s('id_kind')),
          idValue: s('id_value'),
          quantity: quantity,
          reasoning: s('reasoning'),
          confidence: s('confidence').toLowerCase() == 'high' ? 'high' : 'low',
        ),
      );
    }
    return out;
  }

  /// Reads the document-level header every document type pre-fills from.
  ///
  /// Runs on every upload, not just as a fallback; `JobPdfAi.enrich`
  /// reconciles it against the regex/vision result. Null on any failure.
  Future<AiOrderAckHeader?> extractOrderAckHeader(String text) async {
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

    final parsed = await _client.completeJson(
      prompt: prompt,
      maxTokens: 1024,
      timeout: const Duration(seconds: 120),
    );
    if (parsed == null) {
      final viaClaude = await _claudeHeader(text);
      if (viaClaude != null) lastBackend = 'Claude';
      return viaClaude;
    }
    lastBackend = 'Meedo-Me';

    String s(String k) => '${parsed[k] ?? ''}'.trim();
    final flagsRaw = parsed['flags'];
    return AiOrderAckHeader(
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
  }

  Future<List<AiOrderAckLine>?> _claudeLines(String text) async {
    if (!ClaudeClient.isConfigured) return null;
    final claude = ClaudeClient();
    try {
      final rows = await claude.extractOrderAckLines(text);
      if (rows == null) return null;
      return rows
          .map(
            (r) => AiOrderAckLine(
              cpoDisplay: r.cpoDisplay,
              poNumber: r.poNumber,
              idKind: r.idKind,
              idValue: r.idValue,
              quantity: r.quantity,
              reasoning: r.reasoning,
              confidence: r.confidence,
            ),
          )
          .toList();
    } catch (_) {
      return null;
    }
  }

  Future<AiOrderAckHeader?> _claudeHeader(String text) async {
    if (!ClaudeClient.isConfigured) return null;
    final claude = ClaudeClient();
    try {
      final h = await claude.extractOrderAckHeader(text);
      if (h == null) return null;
      return AiOrderAckHeader(
        customerName: h.customerName,
        poNumber: h.poNumber,
        orderNumber: h.orderNumber,
        projectNumber: h.projectNumber,
        jobLocation: h.jobLocation,
        requisitioner: h.requisitioner,
        afeNumber: h.afeNumber,
        shipToName: h.shipToName,
        shipToAddress: h.shipToAddress,
        carrier: h.carrier,
        packingSlipNumber: h.packingSlipNumber,
        reasoning: h.reasoning,
        flags: h.flags,
      );
    } catch (_) {
      return null;
    }
  }

  /// Pulls the line array out of whichever shape the model produced.
  ///
  /// Accepts a bare `[...]`, an object wrapping it under a plausible key, or a
  /// fenced reply. Local models are less consistent about this than a hosted
  /// endpoint, and losing a good extraction to formatting would be a poor
  /// trade for the independence gained.
  static List<dynamic>? _extractLineArray(String raw) {
    final obj = MeedoMeClient.extractJsonObject(raw);
    if (obj != null) {
      for (final key in const ['lines', 'order_lines', 'items', 'rows', 'data']) {
        final v = obj[key];
        if (v is List) return v;
      }
      // Single object that is itself a line.
      if (obj.containsKey('cpo') || obj.containsKey('id_value')) return [obj];
    }
    final start = raw.indexOf('[');
    final end = raw.lastIndexOf(']');
    if (start < 0 || end <= start) return null;
    try {
      final v = jsonDecode(raw.substring(start, end + 1));
      return v is List ? v : null;
    } catch (_) {
      return null;
    }
  }

  /// TAG / PART / ITEM, or null when the row carried no identity field.
  static BulkIdKind? idKindFromLabel(String raw) {
    final k = raw.trim().toUpperCase();
    if (k.startsWith('TAG')) return BulkIdKind.tag;
    if (k.startsWith('PART')) return BulkIdKind.part;
    if (k.startsWith('ITEM')) return BulkIdKind.item;
    return null;
  }

  void close() => _client.close();
}

/// One order line as the model read it.
///
/// Always carries [reasoning]; reconciliation against the regex parser happens
/// in `JobPdfAi.enrichLines`, never here.
class AiOrderAckLine {
  const AiOrderAckLine({
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

  /// Null when no TAG#/PART#/ITEM# could be identified for this row.
  final BulkIdKind? idKind;
  final String idValue;
  final int quantity;
  final String reasoning;
  final String confidence;
}

/// Document-level header fields every document type pre-fills from.
class AiOrderAckHeader {
  const AiOrderAckHeader({
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
  final String reasoning;
  final List<String> flags;
}
