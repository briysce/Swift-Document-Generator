import 'bulk_label_models.dart';

/// Parses Swift Order Acknowledgement text (from pdfrx / pypdf) into bulk lines.
///
/// Identity field comes from OA line notes:
/// - `Order Line Notes: TAG# …` → valve sticker prints **TAG#**
/// - `Order Line Notes: PART# …` → non-valve sticker prints **PART#**
/// - Loose `part # 050211` / `TAG# …` on the line after CPO (common Propak layout)
/// - If still missing: use the first non-empty line under the CPO note as PART#
///
/// CPO detection accepts both legacy `CPO #4` and current `CPO LINE 1` /
/// `CPO LINE 8, 9` forms.
///
/// Lines with CPO but no usable identity are collected in
/// [OrderAckParseResult.incompleteLines] so the UI can prompt
/// Proceed / Skip / Cancel.
class OrderAckParser {
  const OrderAckParser();

  OrderAckParseResult parseText(
    String raw, {
    String sourceFileName = '',
  }) {
    final text = raw.replaceAll('\r\n', '\n').replaceAll('\r', '\n');
    final warnings = <String>[];

    final lines = <BulkLabelLine>[];
    final incomplete = <BulkIncompleteLine>[];
    // PO# seen beside individual CPO/Tag-Part-Item notes — a fallback (and
    // cross-check) for the header "PO Number" grid, which can misparse when
    // the Location value wraps across lines.
    final linePoSightings = <String>[];

    // Legacy: "Order Line Notes: CPO #4"
    // Current: "Order Line Notes: CPO LINE 1" / "CPO LINE 8, 9" (comma list)
    // Range: "Order Line Notes: CPO LINES 1-4" (hyphen range, plural LINES)
    final cpoRe = RegExp(
      r'Order\s+Line\s+Notes:\s*CPO\s*(?:LINES?\s*)?#?\s*'
      r'([0-9]+(?:\s*[-,]\s*[0-9]+)*)',
      caseSensitive: false,
    );
    // Classic identity note on its own Order Line Notes row.
    final idNoteRe = RegExp(
      r'Order\s+Line\s+Notes:\s*(TAG|PART|ITEM)\s*#\s*(.+)$',
      caseSensitive: false,
      multiLine: true,
    );
    // Loose "part # 050211" / "TAG# abc" / "ITEM# 033047" (often directly
    // under CPO LINE).
    final idLooseRe = RegExp(
      r'^\s*(TAG|PART|ITEM)\s*#\s*(.+?)\s*$',
      caseSensitive: false,
      multiLine: true,
    );
    // Per-line "PO# P613979" under a CPO note — cross-check / fallback for
    // the header PO Number when that grid is hard to parse.
    final linePoRe = RegExp(
      r'^\s*PO\s*#\s*(' '${_refToken.pattern}' r')\s*$',
      caseSensitive: false,
      multiLine: true,
    );
    // pypdf often mashes "…EA2.00…" (qty after EA, always with decimals).
    final qtyAfterEaRe = RegExp(
      r'EA\s*(\d+\.\d+)',
      caseSensitive: false,
    );
    // pdfrx layout often has "2.00 EA …" (qty before EA with a space).
    final qtyBeforeEaRe = RegExp(
      r'(\d+\.\d+)\s+EA\b',
      caseSensitive: false,
    );

    final cpoMatches = cpoRe.allMatches(text).toList();
    if (cpoMatches.isEmpty) {
      warnings.add(
        'No CPO line notes found (expected “Order Line Notes: CPO LINE …” '
        'or “Order Line Notes: CPO #…” ).',
      );
    }

    for (var i = 0; i < cpoMatches.length; i++) {
      final m = cpoMatches[i];
      final cpoSpec = _parseCpoSpec(m.group(1)!);
      if (cpoSpec.numbers.isEmpty) continue;

      final blockStart = m.end;
      final blockEnd =
          i + 1 < cpoMatches.length ? cpoMatches[i + 1].start : text.length;
      final after = text.substring(blockStart, blockEnd);

      // Quantity + description from text before this CPO note.
      final before = text.substring(0, m.start);
      final qtyCandidates = <(int end, double value)>[
        for (final qm in qtyAfterEaRe.allMatches(before))
          (qm.end, double.tryParse(qm.group(1)!) ?? 0),
        for (final qm in qtyBeforeEaRe.allMatches(before))
          (qm.end, double.tryParse(qm.group(1)!) ?? 0),
      ]..sort((a, b) => a.$1.compareTo(b.$1));
      var qty = 1;
      if (qtyCandidates.isNotEmpty) {
        final parsed = qtyCandidates.last.$2;
        if (parsed > 0) {
          qty = parsed.round();
          if (qty < 1) qty = 1;
        }
      } else {
        warnings.add(
          'Line CPO #${cpoSpec.display} has no quantity — defaulting to 1 label.',
        );
      }

      var description = '';
      if (qtyCandidates.isNotEmpty) {
        final qEnd = qtyCandidates.last.$1;
        final slice = before.substring(qEnd).trim();
        final linesSlice = slice
            .split('\n')
            .map((l) => l.trim())
            .where((l) => l.isNotEmpty && !RegExp(r'^\d+$').hasMatch(l))
            .take(3)
            .join(' ');
        description = linesSlice.length > 120
            ? '${linesSlice.substring(0, 117)}…'
            : linesSlice;
      }

      final identity = _resolveIdentity(
        after: after,
        lookBackStart: (m.start - 400).clamp(0, text.length),
        around: text.substring((m.start - 400).clamp(0, text.length), blockEnd),
        idNoteRe: idNoteRe,
        idLooseRe: idLooseRe,
      );

      final linePoMatch = linePoRe.firstMatch(after);
      if (linePoMatch != null) {
        linePoSightings.add(linePoMatch.group(1)!.trim());
      }

      final lineNo = cpoSpec.numbers.first;
      if (identity == null) {
        incomplete.add(
          BulkIncompleteLine(
            lineNo: lineNo,
            cpoDisplay: cpoSpec.display,
            cpoNumbers: cpoSpec.numbers,
            quantity: qty,
            description: description,
            reason: 'Missing TAG# / PART# / ITEM#',
          ),
        );
        continue;
      }

      final (idKind, idValue) = identity;
      if (idValue.isEmpty) {
        incomplete.add(
          BulkIncompleteLine(
            lineNo: lineNo,
            cpoDisplay: cpoSpec.display,
            cpoNumbers: cpoSpec.numbers,
            quantity: qty,
            description: description,
            reason: 'Empty ${idKind.fieldLabel}',
          ),
        );
        continue;
      }

      lines.add(
        BulkLabelLine(
          lineNo: lineNo,
          cpoDisplay: cpoSpec.display,
          cpoNumbers: cpoSpec.numbers,
          tagOrPart: idValue,
          idKind: idKind,
          quantity: qty,
          description: description,
        ),
      );
    }

    // Deduplicate by CPO display if page headers re-emit the same note.
    final deduped = <BulkLabelLine>[];
    final seen = <String>{};
    for (final line in lines) {
      final key = line.cpoDisplay;
      if (seen.contains(key)) {
        warnings.add('Duplicate CPO #${line.cpoDisplay} ignored.');
        continue;
      }
      seen.add(key);
      deduped.add(line);
    }

    final dedupedIncomplete = <BulkIncompleteLine>[];
    for (final inc in incomplete) {
      if (seen.contains(inc.cpoDisplay)) {
        warnings.add(
          'Duplicate incomplete CPO #${inc.cpoDisplay} ignored '
          '(already have a complete line).',
        );
        continue;
      }
      seen.add(inc.cpoDisplay);
      dedupedIncomplete.add(inc);
    }

    if (deduped.isEmpty && dedupedIncomplete.isEmpty) {
      warnings.add('No printable label lines were parsed from this document.');
    }

    var poNumber = _extractPo(text);
    if (poNumber == null || poNumber.isEmpty) {
      // Header grid didn't yield anything usable — fall back to whatever
      // PO# was printed beside the CPO/Tag-Part-Item notes themselves.
      if (linePoSightings.isNotEmpty) {
        poNumber = linePoSightings.first;
      } else {
        warnings.add('Could not find PO Number on the Order Acknowledgement.');
      }
    } else if (linePoSightings.isNotEmpty &&
        linePoSightings.any((p) => p.toUpperCase() != poNumber!.toUpperCase())) {
      warnings.add(
        'PO Number on the header ($poNumber) does not match the PO# noted '
        'beside the order lines (${linePoSightings.toSet().join(", ")}). '
        'Please double-check.',
      );
    }

    final orderNumber = _extractOrderNumber(text) ?? '';
    final header = OrderAckHeader.parse(text);

    return OrderAckParseResult(
      poNumber: poNumber ?? '',
      orderNumber: orderNumber,
      lines: deduped,
      warnings: warnings,
      incompleteLines: dedupedIncomplete,
      sourceFileName: sourceFileName,
      customerName: header.customerName,
      projectNumber: header.projectNumber.isNotEmpty
          ? header.projectNumber
          : (poNumber ?? ''),
      jobLocation: header.jobLocation,
      requisitioner: header.requisitioner,
      afeNumber: header.afeNumber,
      deliveryShipToName: header.deliveryShipToName,
      deliveryShipToAddress: header.deliveryShipToAddress,
      headerShipToName: header.headerShipToName,
      headerShipToAddress: header.headerShipToAddress,
      deliveryCarrier: header.deliveryCarrier,
      hasDeliveryShipTo: header.hasDeliveryShipTo,
      packingSlipNumber: header.packingSlipNumber,
      documentKind: header.documentKind,
    );
  }

  /// Resolve TAG#/PART# from notes after (or near) a CPO block.
  ///
  /// Fallback: first meaningful line under the CPO note becomes PART#.
  static (BulkIdKind, String)? _resolveIdentity({
    required String after,
    required int lookBackStart,
    required String around,
    required RegExp idNoteRe,
    required RegExp idLooseRe,
  }) {
    var idMatch = idNoteRe.firstMatch(after);
    if (idMatch == null) {
      final anyId = idNoteRe.allMatches(around).toList();
      if (anyId.isNotEmpty) idMatch = anyId.last;
    }
    if (idMatch != null) {
      final idKind = _idKindFromRaw(idMatch.group(1)!);
      final identity =
          idMatch.group(2)!.replaceAll(RegExp(r'\s+'), ' ').trim();
      return (idKind, identity);
    }

    final loose = idLooseRe.firstMatch(after);
    if (loose != null) {
      final idKind = _idKindFromRaw(loose.group(1)!);
      final identity = loose.group(2)!.replaceAll(RegExp(r'\s+'), ' ').trim();
      // Truncate if trailing OA mash leaked onto the same line.
      final cut = identity.split(RegExp(r'\s{2,}|\bEA\b')).first.trim();
      return (idKind, cut.isEmpty ? identity : cut);
    }

    // Fallback: the first non-empty line directly under the CPO note.
    // If that line is a catalog/price row, treat identity as missing (do not
    // scrape following description wrap lines).
    for (final rawLine in after.split('\n')) {
      final line = rawLine.trim();
      if (line.isEmpty) continue;
      if (RegExp(r'^Order\s+Line\s+Notes:', caseSensitive: false)
          .hasMatch(line)) {
        return null;
      }
      if (RegExp(r'^Rev\b|^Page\b|^Subtotal\b|^GST\b|^Total\b',
              caseSensitive: false)
          .hasMatch(line)) {
        return null;
      }
      if (RegExp(r'^\d{1,3}$').hasMatch(line)) {
        return null;
      }
      // Catalog/qty rows are not identities.
      if (RegExp(r'\d\s*EA\s*\d|\dEA\d|\bEA\b', caseSensitive: false)
          .hasMatch(line)) {
        return null;
      }
      final identity = line.replaceAll(RegExp(r'\s+'), ' ').trim();
      if (identity.isEmpty) return null;
      return (BulkIdKind.part, identity);
    }
    return null;
  }

  static BulkIdKind _idKindFromRaw(String kindRaw) {
    final k = kindRaw.toUpperCase();
    if (k.startsWith('PART')) return BulkIdKind.part;
    if (k.startsWith('ITEM')) return BulkIdKind.item;
    return BulkIdKind.tag;
  }

  /// Parses a raw CPO capture ("1-4", "8, 9", "5", or a mix like "1, 3-5")
  /// into a display string (source text, whitespace-normalized) and the
  /// resolved list of individual CPO numbers, expanding hyphen ranges.
  static ({String display, List<int> numbers}) _parseCpoSpec(String raw) {
    final display = raw.replaceAll(RegExp(r'\s+'), ' ').trim();
    final numbers = <int>[];
    for (final part in display.split(',')) {
      final p = part.trim();
      if (p.isEmpty) continue;
      final rangeMatch = RegExp(r'^(\d+)\s*-\s*(\d+)$').firstMatch(p);
      if (rangeMatch != null) {
        final a = int.parse(rangeMatch.group(1)!);
        final b = int.parse(rangeMatch.group(2)!);
        if (a <= b) {
          for (var n = a; n <= b; n++) {
            numbers.add(n);
          }
        } else {
          numbers.add(a);
        }
        continue;
      }
      final n = int.tryParse(p);
      if (n != null) numbers.add(n);
    }
    return (display: display, numbers: numbers);
  }

  /// Customer PO / project tokens: P612207, 4460.168-016, PCE-112124-03690.
  static final _refToken = RegExp(
    r'(?:P\d{4,}|(?:[A-Z]{1,6}-)?\d{3,}(?:[.\-/][A-Z0-9]+)*)',
    caseSensitive: false,
  );

  static String? _extractPo(String text) {
    final fromCols = OrderAckHeader.extractProjectLocationPo(text);
    if (fromCols.po.isNotEmpty) return fromCols.po;

    final token = _refToken.pattern;
    final labeledSame = RegExp(
      'PO\\s*Number\\s*[:#]?\\s*($token)',
      caseSensitive: false,
    ).firstMatch(text);
    if (labeledSame != null) return labeledSame.group(1)!.trim();

    final labeledNext = RegExp(
      'PO\\s*Number[^\\n]*\\n\\s*($token)',
      caseSensitive: false,
    ).firstMatch(text);
    if (labeledNext != null) return labeledNext.group(1)!.trim();

    // "ProjectLocationPO Number\nP613120" mashed headers.
    final mashed = RegExp(
      'PO\\s*Number[\\s\\S]{0,80}?\\b($token)\\b',
      caseSensitive: false,
    ).firstMatch(text);
    if (mashed != null) return mashed.group(1)!.trim();

    final loose = RegExp(r'\b(P\d{5,})\b').firstMatch(text);
    return loose?.group(1);
  }

  static String? _extractOrderNumber(String text) {
    final m = RegExp(
      r'ORDER\s+ACKNOWLEDGEMENT\s*\n\s*(\d{5,})',
      caseSensitive: false,
    ).firstMatch(text);
    if (m != null) return m.group(1);
    final m2 = RegExp(
      r'Order\s*Number\s*(\d{5,})',
      caseSensitive: false,
    ).firstMatch(text);
    if (m2 != null) return m2.group(1);
    final m3 = RegExp(
      r'Order\s*Number\s*\n(?:[^\n]*\n){0,16}?(\d{6,})',
      caseSensitive: false,
    ).firstMatch(text);
    return m3?.group(1);
  }
}

/// Bill To / Ship To / Delivery Instructions header from a Swift OA.
class OrderAckHeader {
  const OrderAckHeader({
    this.customerName = '',
    this.projectNumber = '',
    this.jobLocation = '',
    this.requisitioner = '',
    this.afeNumber = '',
    this.deliveryShipToName = '',
    this.deliveryShipToAddress = '',
    this.headerShipToName = '',
    this.headerShipToAddress = '',
    this.deliveryCarrier = '',
    this.hasDeliveryShipTo = false,
    this.packingSlipNumber = '',
    this.documentKind = 'order_ack',
  });

  final String customerName;
  final String projectNumber;

  /// Site / LSD from the Location column (e.g. 01-19-043-03W5M Riser Site).
  final String jobLocation;

  /// Requisitioner → Attn on shipping labels.
  final String requisitioner;

  /// AFE # when present on the OA header grid.
  final String afeNumber;

  final String deliveryShipToName;
  final String deliveryShipToAddress;
  final String headerShipToName;
  final String headerShipToAddress;
  final String deliveryCarrier;
  final bool hasDeliveryShipTo;
  final String packingSlipNumber;
  final String documentKind;

  static final _itemRow = RegExp(
    r'\d[\d,]*\.\d{2}\s*EA|\bEA\s*\d|\bOrder\s+Line\s+Notes:|\bItem\s+Description\b|\bRev\s+20|\bSubtotal:',
    caseSensitive: false,
  );
  static final _freightLine = RegExp(
    r'^(ship\s+via\b)|(\bcollect\b)|(\bprepaid\b)|'
    r'^(rosenau|dunrite|murray|murrays|mel martins|highway|cole)\b',
    caseSensitive: false,
  );
  static final _phone = RegExp(r'^\d{3}[-.\s]\d{3}[-.\s]\d{4}');
  static final _country = RegExp(r'^(CA|US|USA|CANADA)$', caseSensitive: false);
  static final _accountOnly = RegExp(r'^\d{4,}$');

  static OrderAckHeader parse(String raw) {
    final text = raw.replaceAll('\r\n', '\n').replaceAll('\r', '\n');
    final parties = _parseBillAndShip(text);
    final cols = extractProjectLocationPo(text);
    final project = cols.project.isNotEmpty ? cols.project : _extractProject(text);
    final meta = _extractRequisitionerAfe(text);
    final delivery = _parseDeliveryInstructions(text);
    return OrderAckHeader(
      customerName: _stripAccountPrefix(parties.billName),
      projectNumber: project,
      jobLocation: cols.location,
      requisitioner: meta.requisitioner,
      afeNumber: meta.afe,
      deliveryShipToName: delivery.name,
      deliveryShipToAddress: delivery.address,
      headerShipToName: _stripAccountPrefix(parties.shipName),
      headerShipToAddress: parties.shipAddress,
      deliveryCarrier: delivery.carrier,
      hasDeliveryShipTo:
          delivery.name.isNotEmpty || delivery.address.isNotEmpty,
      packingSlipNumber: _extractPackingSlip(text),
      documentKind: _documentKind(text),
    );
  }

  static String _stripAccountPrefix(String name) {
    final t = name.trim();
    if (t.isEmpty) return t;
    return t.replaceFirst(RegExp(r'^\d{4,6}\s+'), '').trim();
  }

  /// Parse Project/Location/PO or PO/Location/Project value row.
  static ({String project, String location, String po})
      extractProjectLocationPo(String text) {
    ({String project, String location, String po}) tryRow(
      Match headerMatch, {
      required bool poLast,
    }) {
      final rest = text.substring(headerMatch.end);
      final restLines = rest.split('\n');
      if (restLines.isEmpty) return (project: '', location: '', po: '');

      // Fast path: PO/Location/Project already sit on one line (most OAs).
      final direct = _splitProjectLocationPoRow(restLines.first, poLast: poLast);
      if (direct.po.isNotEmpty || direct.project.isNotEmpty) return direct;

      // Slow path: a wrapped Location value (e.g. a street line then a
      // postal-code line) pushed the PO value onto a later line. Keep
      // collecting lines as "location" until one, by itself, is exactly a
      // PO-shaped token — that one is the PO value, not part of the address.
      final collected = <String>[];
      String? poLine;
      for (final raw in restLines.take(6)) {
        final line = raw.trim();
        if (line.isEmpty) continue;
        if (RegExp('^(${OrderAckParser._refToken.pattern})\$',
                caseSensitive: false)
            .hasMatch(line)) {
          poLine = line;
          break;
        }
        collected.add(line);
      }
      if (poLine == null) return (project: '', location: '', po: '');
      // This layout has no separate Project value here — Project falls back
      // to PO elsewhere in the parser, matching the single-value convention.
      return (project: '', location: collected.join(' '), po: poLine);
    }

    // Propak: Project Location PO Number
    final projectFirst = RegExp(
      r'Project\s*Location\s*PO\s*Number\s*\n',
      caseSensitive: false,
    ).firstMatch(text);
    if (projectFirst != null) {
      final result = tryRow(projectFirst, poLast: true);
      if (result.po.isNotEmpty || result.project.isNotEmpty) return result;
    }

    // Spartan / many OAs: PO Number Location Project
    final poFirst = RegExp(
      r'PO\s*Number\s*Location\s*Project\s*\n',
      caseSensitive: false,
    ).firstMatch(text);
    if (poFirst != null) {
      final result = tryRow(poFirst, poLast: false);
      if (result.po.isNotEmpty || result.project.isNotEmpty) return result;
    }

    // Tab-separated header on one line.
    final tabbed = RegExp(
      r'(Project|PO\s*Number)[ \t]+Location[ \t]+(PO\s*Number|Project)\s*\n',
      caseSensitive: false,
    ).firstMatch(text);
    if (tabbed != null) {
      final firstIsProject =
          tabbed.group(1)!.toLowerCase().startsWith('project');
      final result = tryRow(tabbed, poLast: firstIsProject);
      if (result.po.isNotEmpty || result.project.isNotEmpty) return result;
    }

    return (project: '', location: '', po: '');
  }

  static ({String project, String location, String po})
      _splitProjectLocationPoRow(String raw, {required bool poLast}) {
    var line = raw.trim();
    if (line.isEmpty || RegExp(r'^AFE\b', caseSensitive: false).hasMatch(line)) {
      return (project: '', location: '', po: '');
    }

    final cols = line
        .split(RegExp(r'\t+|\s{2,}'))
        .map((s) => s.trim())
        .where((s) => s.isNotEmpty)
        .where((s) => !RegExp(r'^AFE\b', caseSensitive: false).hasMatch(s))
        .toList();

    if (cols.length >= 3) {
      if (poLast) {
        return (
          project: cols.first,
          location: cols.sublist(1, cols.length - 1).join(' '),
          po: cols.last,
        );
      }
      return (
        po: cols.first,
        location: cols.sublist(1, cols.length - 1).join(' '),
        project: cols.last,
      );
    }

    // Single spaced line: "4460.168-016 01-19-043-03W5M Riser Site 4460.168"
    final tokens = OrderAckParser._refToken.allMatches(line).toList();
    if (tokens.isEmpty) {
      return (
        project: cols.isNotEmpty ? cols.first : '',
        location: '',
        po: '',
      );
    }
    if (tokens.length == 1) {
      // Only trust a lone match when the WHOLE line is that value (e.g. a
      // bare "P613979" on its own line). A single incidental digit-run
      // inside prose — a street number in a wrapped address line, say — is
      // not a PO/project value.
      final only = tokens.first.group(0)!;
      if (only == line) {
        return (project: only, location: '', po: only);
      }
      return (project: '', location: '', po: '');
    }

    final first = tokens.first.group(0)!;
    final last = tokens.last.group(0)!;
    final locStart = poLast ? tokens.first.end : tokens.first.end;
    final locEnd = poLast ? tokens.last.start : tokens.last.start;
    var location = '';
    if (locEnd > locStart) {
      location = line.substring(locStart, locEnd).trim();
    }
    // Drop leading LSD fragment that was captured as middle of PO/project.
    if (poLast) {
      return (project: first, location: location, po: last);
    }
    return (po: first, location: location, project: last);
  }

  static ({String requisitioner, String afe}) _extractRequisitionerAfe(
    String text,
  ) {
    var requisitioner = '';
    var afe = '';

    final row = RegExp(
      r'Requisitioner\s+Approver\s+AFE\s*#[^\n]*\n',
      caseSensitive: false,
    ).firstMatch(text);
    if (row != null) {
      final restLines = text.substring(row.end).split('\n');
      var rowText = restLines.isNotEmpty ? restLines.first.trim() : '';
      // A bare given name with no tab/AFE columns on its line ("MARLENE")
      // often wrapped a surname onto the next line ("DUNAND").
      final bareWord = RegExp(r"^[A-Za-z][A-Za-z.'\-]*$");
      if (bareWord.hasMatch(rowText) && restLines.length > 1) {
        final next = restLines[1].trim();
        if (bareWord.hasMatch(next)) {
          rowText = '$rowText $next';
        }
      }
      final cols = rowText
          .split(RegExp(r'\t+|\s{2,}'))
          .map((s) => s.trim())
          .where((s) => s.isNotEmpty)
          .toList();
      if (cols.isNotEmpty &&
          !RegExp(r'^(AFE|Cost|Work|GL)\b', caseSensitive: false)
              .hasMatch(cols.first)) {
        // Name-like first cell (not a code-only AFE).
        if (RegExp(r'[A-Za-z]{2,}').hasMatch(cols.first) &&
            !RegExp(r'^\d').hasMatch(cols.first)) {
          requisitioner = cols.first;
        }
      }
      for (final c in cols) {
        if (RegExp(r'^[A-Z0-9]{2,}\d[A-Z0-9\-]*$', caseSensitive: false)
            .hasMatch(c)) {
          afe = c;
          break;
        }
      }
    }

    if (afe.isEmpty) {
      // Require a digit somewhere in the value (real AFE codes always have
      // one) so a bare column header word ("Cost", from the following
      // "Cost Center #" column when the row underneath is blank) can't be
      // mistaken for an AFE number.
      final labeled = RegExp(
        r'AFE\s*#\s*((?=[A-Z0-9\-]*\d)[A-Z0-9][A-Z0-9\-]{2,})',
        caseSensitive: false,
      ).firstMatch(text);
      if (labeled != null) afe = labeled.group(1)!.trim();
    }

    // Fallback: first person-like line under Approver mash (Propak SHAWN EPP).
    if (requisitioner.isEmpty) {
      final mash = RegExp(
        r"AFE\s*#\s*GL\s*Code[^\n]*\n\s*([A-Z][A-Za-z .'\-]{2,})",
        caseSensitive: false,
      ).firstMatch(text);
      if (mash != null) {
        requisitioner = mash.group(1)!.trim();
      }
    }

    return (requisitioner: requisitioner, afe: afe);
  }

  static String _documentKind(String text) {
    final head = text.length > 1200 ? text.substring(0, 1200) : text;
    final packing = RegExp(
      r'\bPACKING\s+(LIST|SLIP)\b',
      caseSensitive: false,
    ).hasMatch(head);
    final oa = RegExp(
      r'\bORDER\s+ACKNOWLEDGEMENT\b',
      caseSensitive: false,
    ).hasMatch(head);
    if (packing && !oa) return 'packing_list';
    if (packing && oa) return 'packing_list';
    return 'order_ack';
  }

  static String _extractPackingSlip(String text) {
    if (_documentKind(text) != 'packing_list') return '';
    final labeled = RegExp(
      r'Packing\s*(?:List|Slip)\s*(?:No\.?|Number|#)?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-]{2,})',
      caseSensitive: false,
    ).firstMatch(text);
    if (labeled != null) {
      final v = labeled.group(1)!.trim();
      if (!RegExp(r'^(LIST|SLIP|NO|NUMBER)$', caseSensitive: false).hasMatch(v)) {
        return v;
      }
    }
    final titled = RegExp(
      r'PACKING\s+(?:LIST|SLIP)\s*\n\s*([A-Z0-9][A-Z0-9\-]{3,})',
      caseSensitive: false,
    ).firstMatch(text);
    return titled?.group(1)?.trim() ?? '';
  }

  static String _extractProject(String text) {
    final cols = extractProjectLocationPo(text);
    if (cols.project.isNotEmpty) return cols.project;

    final mashed = RegExp(
      r'Project\s*Location\s*PO\s*Number\s*\n\s*([^\n]+)',
      caseSensitive: false,
    ).firstMatch(text);
    if (mashed != null) {
      final line = mashed.group(1)!.trim();
      if (RegExp(r'^AFE\b', caseSensitive: false).hasMatch(line)) {
        return '';
      }
      final pNum = RegExp(r'\bP\d{5,}\b', caseSensitive: false).firstMatch(line);
      if (pNum != null) return pNum.group(0)!.toUpperCase();
      final parts = line.split(RegExp(r'\s{2,}|\t')).map((s) => s.trim()).where(
            (s) => s.isNotEmpty && !RegExp(r'^AFE\b', caseSensitive: false).hasMatch(s),
          );
      if (parts.isNotEmpty) return parts.first;
    }
    final tabbed = RegExp(
      r'Project[ \t]+Location[ \t]+PO\s*Number\s*\n\s*([^\n]+)',
      caseSensitive: false,
    ).firstMatch(text);
    if (tabbed != null) {
      final cols2 = tabbed.group(1)!.split(RegExp(r'\t+|\s{2,}'));
      if (cols2.isNotEmpty && cols2.first.trim().isNotEmpty) {
        return cols2.first.trim();
      }
    }
    return '';
  }

  static ({String billName, String shipName, String shipAddress})
      _parseBillAndShip(String text) {
    final start = RegExp(r'Bill\s*To:', caseSensitive: false).firstMatch(text);
    if (start == null) {
      return (billName: '', shipName: '', shipAddress: '');
    }
    final rest = text.substring(start.end);
    final end = RegExp(
      r'Ordered\s+By:|Project\s*Location|Project\t',
      caseSensitive: false,
    ).firstMatch(rest);
    final block = (end == null ? rest : rest.substring(0, end.start));
    final lines = [
      for (final raw in block.split('\n'))
        raw.replaceAll('\t', ' ').trim(),
    ].where((l) => l.isNotEmpty && l.toLowerCase() != 'ship to:').toList();

    // Drop "11693 Ship To:" leftovers on the Bill To line.
    final cleaned = <String>[];
    for (var l in lines) {
      l = l.replaceAll(RegExp(r'^Ship\s*To:\s*', caseSensitive: false), '');
      l = l.replaceAll(RegExp(r'\s+Ship\s*To:\s*$', caseSensitive: false), '');
      if (l.isEmpty || _accountOnly.hasMatch(l)) continue;
      cleaned.add(l);
    }

    String takeAddress(List<String> src, int from) {
      final parts = <String>[];
      for (var i = from; i < src.length; i++) {
        final l = src[i];
        if (_country.hasMatch(l) || _phone.hasMatch(l)) break;
        if (i > from && _looksLikeCompany(l) && parts.isNotEmpty) break;
        parts.add(l);
      }
      return parts.join('\n').trim();
    }

    if (cleaned.isEmpty) {
      return (billName: '', shipName: '', shipAddress: '');
    }
    final billName = cleaned.first;
    var i = 1;
    while (i < cleaned.length &&
        !_country.hasMatch(cleaned[i]) &&
        !_phone.hasMatch(cleaned[i])) {
      i++;
    }
    while (i < cleaned.length &&
        (_country.hasMatch(cleaned[i]) || _phone.hasMatch(cleaned[i]))) {
      i++;
    }
    if (i >= cleaned.length) {
      return (billName: billName, shipName: '', shipAddress: '');
    }
    final shipName = cleaned[i];
    final shipAddress = takeAddress(cleaned, i + 1);
    return (billName: billName, shipName: shipName, shipAddress: shipAddress);
  }

  static bool _looksLikeCompany(String line) {
    return RegExp(
      r'\b(ltd|inc|corp|llc|llp|lp|co\.|systems|energy|services)\b',
      caseSensitive: false,
    ).hasMatch(line);
  }

  static ({String name, String address, String carrier})
      _parseDeliveryInstructions(String text) {
    final m = RegExp(
      r'Delivery\s+Instructions\s*:\s*',
      caseSensitive: false,
    ).firstMatch(text);
    if (m == null) {
      return (name: '', address: '', carrier: '');
    }
    final rest = text.substring(m.end);
    final lines = <String>[];
    for (final raw in rest.split('\n')) {
      final line = raw.trim();
      if (line.isEmpty) {
        if (lines.isNotEmpty) break;
        continue;
      }
      if (_itemRow.hasMatch(line)) break;
      if (RegExp(r'^AFE\b', caseSensitive: false).hasMatch(line)) continue;
      lines.add(line);
      if (lines.length >= 8) break;
    }
    if (lines.isEmpty) {
      return (name: '', address: '', carrier: '');
    }

    var carrier = '';
    final body = <String>[];
    for (final line in lines) {
      if (body.isEmpty && _freightLine.hasMatch(line)) {
        carrier = line.replaceAll(RegExp(r'\s+'), ' ').trim();
        continue;
      }
      body.add(line);
    }
    // Trailing "Ship via …" after the address.
    if (body.isNotEmpty && _freightLine.hasMatch(body.last)) {
      if (carrier.isEmpty) {
        carrier = body.last.replaceAll(RegExp(r'\s+'), ' ').trim();
      }
      body.removeLast();
    }
    if (body.isEmpty) {
      return (name: '', address: '', carrier: carrier);
    }
    final name = body.first.replaceAll(RegExp(r'\s+'), ' ').trim();
    final address = body.skip(1).join('\n').trim();
    return (name: name, address: address, carrier: carrier);
  }
}

