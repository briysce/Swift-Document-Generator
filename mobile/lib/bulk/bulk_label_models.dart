/// Whether an OA line supplies a valve TAG#, a PART#, or an ITEM#.
enum BulkIdKind { tag, part, item }

extension BulkIdKindLabel on BulkIdKind {
  /// Printed field label on the Propak sticker.
  String get fieldLabel => switch (this) {
        BulkIdKind.tag => 'TAG#',
        BulkIdKind.part => 'PART#',
        BulkIdKind.item => 'ITEM#',
      };

  String get previewColumn => fieldLabel;

  /// Sensible default print mode when the user hasn't chosen one yet.
  /// TAG# lines are almost always individually-tagged valves (one tag per
  /// unit); PART#/ITEM# lines are more often fittings/flanges shipped
  /// together in one box or skid (one tag covers the whole quantity).
  BulkPrintMode get defaultPrintMode => switch (this) {
        BulkIdKind.tag => BulkPrintMode.perUnit,
        BulkIdKind.part => BulkPrintMode.single,
        BulkIdKind.item => BulkPrintMode.single,
      };
}

/// How many physical stickers one [BulkLabelLine] becomes.
enum BulkPrintMode {
  /// One sticker for the whole line, however large its quantity.
  single,

  /// One sticker per unit of quantity.
  perUnit,
}

/// User choice when OA lines are missing TAG# / PART# / ITEM#.
enum BulkMissingIdAction {
  /// Include incomplete lines with a blank identity (editable in Word).
  proceed,

  /// Omit incomplete lines; keep only fully tagged lines.
  skip,

  /// Discard the whole upload.
  cancel,
}

/// One order-ack line that will become one or more Avery stickers.
///
/// [cpoDisplay] is the CPO reference exactly as the PM wrote it in one
/// `Order Line Notes:` block — a single number ("5"), a comma list
/// ("8, 9"), or a hyphen range ("1-4"). [cpoNumbers] is that same reference
/// resolved to individual line numbers, used only when [printMode] is
/// [BulkPrintMode.perUnit] and the chosen quantity lines up 1:1 with them.
class BulkLabelLine {
  BulkLabelLine({
    required this.lineNo,
    required this.cpoDisplay,
    this.cpoNumbers = const [],
    required this.tagOrPart,
    required this.idKind,
    required this.quantity,
    this.description = '',
    this.missingIdentity = false,
    BulkPrintMode? printMode,
    this.aiNote,
  }) : printMode = printMode ?? idKind.defaultPrintMode;

  final int lineNo;
  final String cpoDisplay;
  final List<int> cpoNumbers;
  final String tagOrPart;
  final BulkIdKind idKind;
  final int quantity;
  final String description;

  /// True when Proceed kept a line that had no TAG#/PART#/ITEM# on the OA.
  final bool missingIdentity;

  /// One sticker for the whole line, or one per unit — user-editable in the
  /// bulk review screen before generating.
  final BulkPrintMode printMode;

  /// Claude's reasoning for this line (always populated when the API is
  /// configured — shown in the review screen, never silently substituted
  /// for the regex-parsed value).
  final String? aiNote;

  int get labelCount => quantity < 1 ? 0 : quantity;

  /// Actual sticker count once [printMode] is applied: 1 for the whole line
  /// in [BulkPrintMode.single], or [labelCount] (one per unit) otherwise.
  int get effectiveLabelCount =>
      printMode == BulkPrintMode.single ? (labelCount > 0 ? 1 : 0) : labelCount;

  BulkLabelLine copyWith({
    String? tagOrPart,
    BulkIdKind? idKind,
    bool? missingIdentity,
    BulkPrintMode? printMode,
    String? aiNote,
  }) =>
      BulkLabelLine(
        lineNo: lineNo,
        cpoDisplay: cpoDisplay,
        cpoNumbers: cpoNumbers,
        tagOrPart: tagOrPart ?? this.tagOrPart,
        idKind: idKind ?? this.idKind,
        quantity: quantity,
        description: description,
        missingIdentity: missingIdentity ?? this.missingIdentity,
        printMode: printMode ?? this.printMode,
        aiNote: aiNote ?? this.aiNote,
      );
}

/// OA line that has CPO (+ qty) but no TAG# / PART# / ITEM# yet.
class BulkIncompleteLine {
  const BulkIncompleteLine({
    required this.lineNo,
    required this.cpoDisplay,
    this.cpoNumbers = const [],
    required this.quantity,
    this.description = '',
    this.reason = 'Missing TAG# / PART# / ITEM#',
    this.aiNote,
  });

  final int lineNo;
  final String cpoDisplay;
  final List<int> cpoNumbers;
  final int quantity;
  final String description;
  final String reason;
  final String? aiNote;

  /// Placeholder sticker rows if the user chooses Proceed.
  BulkLabelLine asProceedLine() => BulkLabelLine(
        lineNo: lineNo,
        cpoDisplay: cpoDisplay,
        cpoNumbers: cpoNumbers,
        tagOrPart: '',
        idKind: BulkIdKind.tag,
        quantity: quantity,
        description: description,
        missingIdentity: true,
        aiNote: aiNote,
      );
}

/// One physical sticker after quantity expansion.
class BulkLabelInstance {
  const BulkLabelInstance({
    required this.poNumber,
    required this.cpo,
    required this.tagOrPart,
    required this.idKind,
    required this.sourceLineNo,
  });

  final String poNumber;
  final String cpo;
  final String tagOrPart;
  final BulkIdKind idKind;
  final int sourceLineNo;

  String get idFieldLabel => idKind.fieldLabel;

  Map<String, dynamic> toJson() => {
        'po': poNumber,
        'cpo': cpo,
        'id': tagOrPart,
        'idKind': idKind.name,
        'line': sourceLineNo,
      };
}

/// Result of parsing a Swift Order Acknowledgement.
class OrderAckParseResult {
  const OrderAckParseResult({
    required this.poNumber,
    required this.orderNumber,
    required this.lines,
    required this.warnings,
    this.incompleteLines = const [],
    this.sourceFileName = '',
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

  final String poNumber;
  final String orderNumber;
  final List<BulkLabelLine> lines;
  final List<String> warnings;

  /// Lines with CPO but no TAG#/PART#/ITEM# — awaiting Proceed / Skip / Cancel.
  final List<BulkIncompleteLine> incompleteLines;
  final String sourceFileName;

  /// Bill To company name (not the Ship To header).
  final String customerName;

  /// Project column (often a P-number). May match [poNumber] when the OA
  /// only prints one value under Project / Location / PO Number.
  final String projectNumber;

  /// Location column (LSD / site) when present.
  final String jobLocation;

  /// Requisitioner name → Attn.
  final String requisitioner;

  /// AFE # from the OA header grid.
  final String afeNumber;

  final String deliveryShipToName;
  final String deliveryShipToAddress;
  final String headerShipToName;
  final String headerShipToAddress;

  /// Freight line from Delivery Instructions (e.g. ROSENAU COLLECT).
  final String deliveryCarrier;

  /// True when Delivery Instructions include a usable name and/or street.
  final bool hasDeliveryShipTo;

  /// Swift packing slip / packing list number when the PDF is a packing list.
  final String packingSlipNumber;

  /// `order_ack` or `packing_list`.
  final String documentKind;

  /// Special-instructions blob from location + AFE.
  String get specialInstructionsHint {
    final parts = <String>[
      if (jobLocation.trim().isNotEmpty) jobLocation.trim(),
      if (afeNumber.trim().isNotEmpty) 'AFE ${afeNumber.trim()}',
    ];
    return parts.join(' · ');
  }

  bool get hasIncompleteLines => incompleteLines.isNotEmpty;

  int get totalLabels =>
      lines.fold<int>(0, (sum, line) => sum + line.effectiveLabelCount);

  int get sheetCount {
    final n = totalLabels;
    if (n <= 0) return 0;
    return (n + 9) ~/ 10; // Avery 5163 = 10 / sheet
  }

  OrderAckParseResult copyWith({
    String? poNumber,
    String? orderNumber,
    List<BulkLabelLine>? lines,
    List<String>? warnings,
    List<BulkIncompleteLine>? incompleteLines,
    String? sourceFileName,
    String? customerName,
    String? projectNumber,
    String? jobLocation,
    String? requisitioner,
    String? afeNumber,
    String? deliveryShipToName,
    String? deliveryShipToAddress,
    String? headerShipToName,
    String? headerShipToAddress,
    String? deliveryCarrier,
    bool? hasDeliveryShipTo,
    String? packingSlipNumber,
    String? documentKind,
  }) {
    return OrderAckParseResult(
      poNumber: poNumber ?? this.poNumber,
      orderNumber: orderNumber ?? this.orderNumber,
      lines: lines ?? this.lines,
      warnings: warnings ?? this.warnings,
      incompleteLines: incompleteLines ?? this.incompleteLines,
      sourceFileName: sourceFileName ?? this.sourceFileName,
      customerName: customerName ?? this.customerName,
      projectNumber: projectNumber ?? this.projectNumber,
      jobLocation: jobLocation ?? this.jobLocation,
      requisitioner: requisitioner ?? this.requisitioner,
      afeNumber: afeNumber ?? this.afeNumber,
      deliveryShipToName: deliveryShipToName ?? this.deliveryShipToName,
      deliveryShipToAddress:
          deliveryShipToAddress ?? this.deliveryShipToAddress,
      headerShipToName: headerShipToName ?? this.headerShipToName,
      headerShipToAddress: headerShipToAddress ?? this.headerShipToAddress,
      deliveryCarrier: deliveryCarrier ?? this.deliveryCarrier,
      hasDeliveryShipTo: hasDeliveryShipTo ?? this.hasDeliveryShipTo,
      packingSlipNumber: packingSlipNumber ?? this.packingSlipNumber,
      documentKind: documentKind ?? this.documentKind,
    );
  }

  /// Apply the user's dialog choice for incomplete lines.
  OrderAckParseResult applyingMissingIdAction(BulkMissingIdAction action) {
    switch (action) {
      case BulkMissingIdAction.cancel:
        return this;
      case BulkMissingIdAction.skip:
        final notes = [
          for (final inc in incompleteLines)
            'Line CPO #${inc.cpoDisplay} is missing TAG# / PART# / ITEM# — '
                'skipped. Please check with the PM.',
        ];
        return copyWith(
          warnings: [...warnings, ...notes],
          incompleteLines: const [],
        );
      case BulkMissingIdAction.proceed:
        final merged = [
          ...lines,
          for (final inc in incompleteLines) inc.asProceedLine(),
        ]..sort((a, b) => a.lineNo.compareTo(b.lineNo));
        final notes = [
          for (final inc in incompleteLines)
            'Line CPO #${inc.cpoDisplay} is missing TAG# / PART# / ITEM# — '
                'included blank for editing. Please check with the PM.',
        ];
        return copyWith(
          lines: merged,
          warnings: [...warnings, ...notes],
          incompleteLines: const [],
        );
    }
  }

  /// Replace one line (matched by [lineNo]) — used by the bulk review screen
  /// when the user edits the identity text or the single/per-unit toggle.
  OrderAckParseResult replacingLine(BulkLabelLine updated) {
    return copyWith(
      lines: [
        for (final l in lines) if (l.lineNo == updated.lineNo) updated else l,
      ],
    );
  }

  List<BulkLabelInstance> expand() {
    final out = <BulkLabelInstance>[];
    for (final line in lines) {
      final n = line.labelCount;
      if (n <= 0) continue;
      if (line.printMode == BulkPrintMode.single) {
        out.add(
          BulkLabelInstance(
            poNumber: poNumber,
            cpo: line.cpoDisplay,
            tagOrPart: line.tagOrPart,
            idKind: line.idKind,
            sourceLineNo: line.lineNo,
          ),
        );
        continue;
      }
      // Per-unit: distribute individual CPO numbers 1:1 when the count
      // lines up with the chosen quantity; otherwise every sticker repeats
      // the full combined reference (still editable in the Word doc).
      final oneToOne = line.cpoNumbers.length == n;
      for (var i = 0; i < n; i++) {
        out.add(
          BulkLabelInstance(
            poNumber: poNumber,
            cpo: oneToOne ? '${line.cpoNumbers[i]}' : line.cpoDisplay,
            tagOrPart: line.tagOrPart,
            idKind: line.idKind,
            sourceLineNo: line.lineNo,
          ),
        );
      }
    }
    return out;
  }
}
