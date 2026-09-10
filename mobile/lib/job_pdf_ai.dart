import 'bulk/bulk_label_models.dart';
import 'claude_client.dart';
import 'gemini_client.dart';

/// Gemini overlay for Swift OA / packing-list field extraction and
/// leftover address-book merge decisions, plus a Claude "common sense" pass
/// over the individual bulk order lines.
class JobPdfAi {
  JobPdfAi({GeminiClient? client, ClaudeClient? claude})
      : _gemini = client ?? GeminiClient(),
        _claude = claude ?? ClaudeClient();

  final GeminiClient _gemini;
  final ClaudeClient _claude;

  Future<OrderAckParseResult> enrich(OrderAckParseResult parsed, String text) async {
    if (!GeminiClient.isConfigured) return parsed;
    final snippet = text.length > 9000 ? text.substring(0, 9000) : text;
    final data = await _gemini.generateJson(
      prompt: '''
You extract fields from a Swift Oilfield Supply Order Acknowledgement or Packing List.

Document text:
"""
$snippet
"""

Return JSON only:
{
  "document_kind": "order_ack" or "packing_list",
  "customer_name": "Bill To company name only (no account number)",
  "sales_order": "Swift order number",
  "packing_slip": "Swift packing slip/list number if this is a packing list, else empty",
  "po_number": "full customer PO exactly as printed (keep dots and hyphens)",
  "project": "full project number exactly as printed",
  "job_location": "Location column / site / LSD if present, else empty",
  "requisitioner": "Requisitioner name if present, else empty",
  "afe_number": "AFE # if present, else empty",
  "delivery_ship_to_name": "name from Delivery Instructions (c/o), else empty",
  "delivery_ship_to_address": "street/city from Delivery Instructions, else empty",
  "header_ship_to_name": "Ship To header name",
  "header_ship_to_address": "Ship To header address",
  "carrier": "carrier from delivery instructions if present"
}

Rules:
- packing_slip is only on packing lists, never invent one for an OA.
- Prefer Delivery Instructions over the Ship To header for the actual destination.
- Never truncate PO or project (e.g. use 4460.168-016 not 4460).
- Empty string when unknown.
''',
    );
    var out = parsed;
    if (data != null) {
      String g(String k) => '${data[k] ?? ''}'.trim();

      final kind = g('document_kind');
      final packing = g('packing_slip');
      final deliveryName = g('delivery_ship_to_name');
      final deliveryAddr = g('delivery_ship_to_address');
      out = parsed.copyWith(
        documentKind:
            kind == 'packing_list' || parsed.documentKind == 'packing_list'
                ? 'packing_list'
                : parsed.documentKind,
        customerName: preferField(parsed.customerName, g('customer_name')),
        orderNumber: preferField(parsed.orderNumber, g('sales_order')),
        packingSlipNumber: preferField(parsed.packingSlipNumber, packing),
        poNumber: preferField(parsed.poNumber, g('po_number')),
        projectNumber: preferField(parsed.projectNumber, g('project')),
        jobLocation: preferField(parsed.jobLocation, g('job_location')),
        requisitioner: preferField(parsed.requisitioner, g('requisitioner')),
        afeNumber: preferField(parsed.afeNumber, g('afe_number')),
        deliveryShipToName:
            preferField(parsed.deliveryShipToName, deliveryName),
        deliveryShipToAddress:
            preferField(parsed.deliveryShipToAddress, deliveryAddr),
        headerShipToName:
            preferField(parsed.headerShipToName, g('header_ship_to_name')),
        headerShipToAddress: preferField(
            parsed.headerShipToAddress, g('header_ship_to_address')),
        deliveryCarrier: preferField(parsed.deliveryCarrier, g('carrier')),
        hasDeliveryShipTo: parsed.hasDeliveryShipTo ||
            deliveryName.isNotEmpty ||
            deliveryAddr.isNotEmpty,
      );
    }
    return enrichHeaderWithClaude(out, text);
  }

  /// Claude "common sense" pass over the OA/packing-list header — always
  /// runs (not just as a fallback), same reconciliation rule as the Bulk
  /// line pass: only fills a field the regex+Gemini pass left completely
  /// empty; when a field already has a value and Claude reads it
  /// differently, the existing value is kept and Claude's read is noted in
  /// [OrderAckParseResult.warnings] instead of silently overriding it.
  Future<OrderAckParseResult> enrichHeaderWithClaude(
    OrderAckParseResult parsed,
    String text,
  ) async {
    if (!ClaudeClient.isConfigured) return parsed;
    final header = await _claude.extractOrderAckHeader(text);
    if (header == null) return parsed;
    return applyClaudeHeaderSuggestion(parsed, header);
  }

  /// Pure reconciliation used by [enrichHeaderWithClaude] (and unit tests).
  static OrderAckParseResult applyClaudeHeaderSuggestion(
    OrderAckParseResult parsed,
    ClaudeOrderAckHeader header,
  ) {
    bool differs(String current, String claude) {
      if (claude.isEmpty) return false;
      if (current.isEmpty) return false;
      return current.trim().toLowerCase() != claude.trim().toLowerCase();
    }

    final disagreements = <String>[];
    void check(String label, String current, String claude) {
      if (differs(current, claude)) {
        disagreements.add('$label: using "$current" — Claude read "$claude"');
      }
    }

    check('Customer', parsed.customerName, header.customerName);
    check('PO#', parsed.poNumber, header.poNumber);
    check('Sales order', parsed.orderNumber, header.orderNumber);
    check('Project', parsed.projectNumber, header.projectNumber);
    check('Requisitioner', parsed.requisitioner, header.requisitioner);
    check('AFE #', parsed.afeNumber, header.afeNumber);

    final merged = parsed.copyWith(
      customerName: preferField(parsed.customerName, header.customerName),
      poNumber: preferField(parsed.poNumber, header.poNumber),
      orderNumber: preferField(parsed.orderNumber, header.orderNumber),
      projectNumber: preferField(parsed.projectNumber, header.projectNumber),
      jobLocation: preferField(parsed.jobLocation, header.jobLocation),
      requisitioner: preferField(parsed.requisitioner, header.requisitioner),
      afeNumber: preferField(parsed.afeNumber, header.afeNumber),
      deliveryShipToName:
          preferField(parsed.deliveryShipToName, header.shipToName),
      deliveryShipToAddress:
          preferField(parsed.deliveryShipToAddress, header.shipToAddress),
      deliveryCarrier: preferField(parsed.deliveryCarrier, header.carrier),
      packingSlipNumber:
          preferField(parsed.packingSlipNumber, header.packingSlipNumber),
      hasDeliveryShipTo: parsed.hasDeliveryShipTo ||
          header.shipToName.isNotEmpty ||
          header.shipToAddress.isNotEmpty,
    );

    final notes = <String>[
      if (header.reasoning.isNotEmpty) 'Claude review: ${header.reasoning}',
      if (header.flags.isNotEmpty)
        'Claude is unsure about: ${header.flags.join(", ")} — please confirm.',
      ...disagreements.map((d) => 'Claude review — $d'),
    ];
    if (notes.isEmpty) return merged;
    return merged.copyWith(warnings: [...merged.warnings, ...notes]);
  }

  /// Claude "common sense" pass over every bulk order line — always runs
  /// (not just as a fallback), and always attaches its reasoning to
  /// [BulkLabelLine.aiNote] / [BulkIncompleteLine.aiNote] so the review
  /// screen can show it. Never silently overrides a regex-parsed identity:
  /// when the regex found nothing at all (an [OrderAckParseResult.incompleteLines]
  /// row), Claude's value fills the gap but stays flagged `missingIdentity`
  /// so the user must still confirm it; when the regex already found a
  /// value, Claude's read is attached as a note (agreement or an
  /// alternative) but never replaces it.
  Future<OrderAckParseResult> enrichLines(
    OrderAckParseResult parsed,
    String text,
  ) async {
    if (!ClaudeClient.isConfigured) return parsed;
    final claudeLines = await _claude.extractOrderAckLines(text);
    if (claudeLines == null || claudeLines.isEmpty) return parsed;
    return applyClaudeLineSuggestions(parsed, claudeLines);
  }

  /// Pure reconciliation used by [enrichLines] (and unit tests).
  static OrderAckParseResult applyClaudeLineSuggestions(
    OrderAckParseResult parsed,
    List<ClaudeOrderAckLine> claudeLines,
  ) {
    String norm(String s) => s.replaceAll(RegExp(r'\s+'), '').toUpperCase();
    final byCpo = <String, ClaudeOrderAckLine>{};
    for (final cl in claudeLines) {
      final key = norm(cl.cpoDisplay);
      if (key.isEmpty) continue;
      byCpo.putIfAbsent(key, () => cl);
    }
    if (byCpo.isEmpty) return parsed;

    final updatedLines = <BulkLabelLine>[];
    for (final line in parsed.lines) {
      final match = byCpo[norm(line.cpoDisplay)];
      String note;
      if (match == null) {
        note = 'Claude’s review did not separately match this CPO '
            'reference.';
      } else {
        final agrees = match.idKind == line.idKind &&
            match.idValue.trim().toLowerCase() ==
                line.tagOrPart.trim().toLowerCase();
        note = agrees
            ? 'Claude agrees: ${line.idKind.fieldLabel} ${line.tagOrPart}. '
                '${match.reasoning}'
            : 'Claude read this differently (${match.confidence} '
                'confidence): ${match.idKind?.fieldLabel ?? 'no identity'} '
                '${match.idValue}. ${match.reasoning}';
      }
      updatedLines.add(line.copyWith(aiNote: note.trim()));
    }

    final stillIncomplete = <BulkIncompleteLine>[];
    for (final inc in parsed.incompleteLines) {
      final match = byCpo[norm(inc.cpoDisplay)];
      if (match != null &&
          match.idKind != null &&
          match.idValue.trim().isNotEmpty) {
        // Regex found nothing for this line; Claude did. Fill the gap, but
        // keep it flagged as AI-suggested — never silently trusted.
        updatedLines.add(
          BulkLabelLine(
            lineNo: inc.lineNo,
            cpoDisplay: inc.cpoDisplay,
            cpoNumbers: inc.cpoNumbers,
            tagOrPart: match.idValue.trim(),
            idKind: match.idKind!,
            quantity: inc.quantity,
            description: inc.description,
            missingIdentity: true,
            aiNote: 'AI-suggested (${match.confidence} confidence) — '
                'please confirm: ${match.reasoning}',
          ),
        );
        continue;
      }
      stillIncomplete.add(
        BulkIncompleteLine(
          lineNo: inc.lineNo,
          cpoDisplay: inc.cpoDisplay,
          cpoNumbers: inc.cpoNumbers,
          quantity: inc.quantity,
          description: inc.description,
          reason: inc.reason,
          aiNote: match?.reasoning.trim().isNotEmpty == true
              ? match!.reasoning.trim()
              : 'Claude’s review also found no TAG#/PART#/ITEM# for '
                  'this line.',
        ),
      );
    }

    updatedLines.sort((a, b) => a.lineNo.compareTo(b.lineNo));
    return parsed.copyWith(
      lines: updatedLines,
      incompleteLines: stillIncomplete,
    );
  }

  /// Keep the better of regex vs Gemini: prefer non-empty, and prefer a value
  /// that extends a truncated prefix (4460 → 4460.168-016).
  static String preferField(String current, String incoming) {
    final a = current.trim();
    final b = incoming.trim();
    if (b.isEmpty) return a;
    if (a.isEmpty) return b;
    final al = a.toLowerCase();
    final bl = b.toLowerCase();
    if (bl.startsWith(al) && b.length > a.length) return b;
    if (al.startsWith(bl) && a.length > b.length) return a;
    return a;
  }

  /// When two rows share a ship-to name but rules did not merge, ask Gemini.
  Future<bool> samePlace({
    required String shipToA,
    required String addressA,
    required String shipToB,
    required String addressB,
  }) async {
    if (!GeminiClient.isConfigured) return false;
    final data = await _gemini.generateJson(
      timeout: const Duration(seconds: 12),
      prompt: '''
Are these the same physical delivery location for a warehouse address book?
Name A: "$shipToA"
Address A: "$addressA"
Name B: "$shipToB"
Address B: "$addressB"

Treat extra city, province, or postal code as the same place.
Different civic numbers are different places.
Different companies / c/o names are different places.

Return JSON only: { "same_place": true }
''',
    );
    if (data == null) return false;
    final v = data['same_place'];
    return v == true || v == 'true';
  }

  /// Predictive delivery-address lines. Empty if unconfigured or query too short.
  Future<List<String>> suggestAddresses({
    required String query,
    String shipToName = '',
    String customer = '',
  }) async {
    if (!GeminiClient.isConfigured) return const [];
    final q = query.trim();
    if (q.length < 3) return const [];
    final data = await _gemini.generateJson(
      timeout: const Duration(seconds: 12),
      prompt: '''
Suggest up to 5 Canadian oilfield / industrial delivery addresses for a shipping label.

User is typing: "$q"
Ship To Name: "${shipToName.trim().isEmpty ? '(none)' : shipToName.trim()}"
Customer: "${customer.trim().isEmpty ? '(none)' : customer.trim()}"

Return JSON only:
{ "suggestions": ["3360 10 Street\\nNisku, AB T9E 1E7"] }

Rules:
- Western Canada (AB/BC/SK) industrial, shop, and wellsite addresses are in scope.
- Remote locations, LSD, lease roads, and site numbers that are NOT on Google Maps are valid. If the user typed a site or LSD, suggest completing that site — do not invent a city street instead.
- Do not invent a civic number you are not reasonably sure about.
- Prefer 1–3 line addresses (street or site, city/province, postal if known).
- If nothing is plausible, return { "suggestions": [] }.
''',
    );
    if (data == null) return const [];
    final list = data['suggestions'];
    if (list is! List) return const [];
    final out = <String>[];
    final seen = <String>{};
    for (final item in list) {
      final s = '$item'.replaceAll('\r\n', '\n').trim();
      if (s.isEmpty) continue;
      final k = s.toLowerCase();
      if (seen.add(k)) out.add(s);
      if (out.length >= 5) break;
    }
    return out;
  }
}

String joinJobPdfValues(Iterable<String> values) {
  final seen = <String>{};
  final out = <String>[];
  for (final raw in values) {
    final t = raw.trim();
    if (t.isEmpty) continue;
    final k = t.toLowerCase();
    if (seen.add(k)) out.add(t);
  }
  return out.join(' / ');
}
