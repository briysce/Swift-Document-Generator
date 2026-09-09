import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:swift_shipping_label/bulk/bulk_label_models.dart';
import 'package:swift_shipping_label/bulk/order_ack_parser.dart';
import 'package:swift_shipping_label/claude_client.dart';
import 'package:swift_shipping_label/job_pdf_ai.dart';
import 'package:swift_shipping_label/pdf/bulk_label_pdf.dart';

void main() {
  group('OrderAckParser', () {
    test('parses clean OA text fixture', () {
      final text =
          File('test/fixtures/propak_order_ack_sample.txt').readAsStringSync();
      final result = const OrderAckParser().parseText(text);
      expect(result.poNumber, 'P612207');
      expect(result.orderNumber, '1423442');
      expect(result.lines.length, greaterThanOrEqualTo(40));

      final line4 = result.lines.firstWhere((l) => l.cpoDisplay == '4');
      expect(line4.tagOrPart, '2"GL-A-03AR');
      expect(line4.idKind, BulkIdKind.tag);
      expect(line4.idKind.fieldLabel, 'TAG#');
      expect(line4.quantity, 2);
      expect(line4.labelCount, 2);

      final line8 = result.lines.firstWhere((l) => l.cpoDisplay == '8');
      expect(line8.quantity, 12);
      expect(line8.idKind, BulkIdKind.tag);

      final expanded = result.expand();
      expect(expanded.length, result.totalLabels);
      expect(
        expanded
            .where((e) => e.cpo == '4' && e.tagOrPart == '2"GL-A-03AR')
            .length,
        2,
      );
      expect(result.sheetCount, (result.totalLabels + 9) ~/ 10);
      // All stickers share the same PO.
      expect(expanded.every((e) => e.poNumber == 'P612207'), isTrue);
    });

    test('parses pdfrx-style qty-before-EA layout', () {
      const text = '''
ORDER ACKNOWLEDGEMENT
1423442
PO Number
P612207
2.00 EA 2" 300# RF WARREN
4
Order Line Notes: CPO #4
Order Line Notes: TAG# 2"GL-A-03AR
12.00 EA 1/2" BALL
8
Order Line Notes: CPO #8
Order Line Notes: TAG# 1/2"BA-A-20AT
''';
      final result = const OrderAckParser().parseText(text);
      expect(result.poNumber, 'P612207');
      expect(result.lines.firstWhere((l) => l.cpoDisplay == '4').quantity, 2);
      expect(result.lines.firstWhere((l) => l.cpoDisplay == '8').quantity, 12);
    });

    test('parses CPO LINE + loose part # and under-CPO fallback (1425965)', () {
      final text =
          File('test/fixtures/propak_oa_1425965.txt').readAsStringSync();
      final result = const OrderAckParser().parseText(text);
      expect(result.poNumber, 'P613120');
      expect(result.orderNumber, '1425965');
      expect(result.lines, isNotEmpty);

      // CPO LINE 1 has no part # — use the "Used by …" line under CPO.
      final line1 = result.lines.firstWhere((l) => l.cpoDisplay == '1');
      expect(line1.idKind, BulkIdKind.part);
      expect(line1.tagOrPart.toLowerCase(), contains('used by'));
      expect(line1.quantity, 1);

      // Explicit loose part # under CPO LINE.
      final line5 = result.lines.firstWhere((l) => l.cpoDisplay == '5');
      expect(line5.tagOrPart, '050211');
      expect(line5.idKind, BulkIdKind.part);

      // "CPO LINE 8, 9" written together on one note is ONE line, not two —
      // the PM wrote them as a single group, and the row's quantity (2)
      // already accounts for both CPO numbers. It must not double the label
      // count by also splitting into separate per-number lines.
      final line89 = result.lines.firstWhere((l) => l.cpoDisplay == '8, 9');
      expect(result.lines.any((l) => l.cpoDisplay == '8'), isFalse);
      expect(result.lines.any((l) => l.cpoDisplay == '9'), isFalse);
      expect(line89.cpoNumbers, [8, 9]);
      expect(line89.tagOrPart, '055329');
      expect(line89.idKind, BulkIdKind.part);
      expect(line89.quantity, 2);
      expect(line89.labelCount, 2);
    });

    test('uses PART# when OA has no TAG#', () {
      const text = '''
ORDER ACKNOWLEDGEMENT
1423442
PO Number
P612207
1.00 EA STRAINER
12
Order Line Notes: CPO #12
Order Line Notes: PART# 4"Y-STRAINER-300
2.00 EA VALVE
13
Order Line Notes: CPO #13
Order Line Notes: TAG# 2"GL-A-03AR
''';
      final result = const OrderAckParser().parseText(text);
      final partLine = result.lines.firstWhere((l) => l.cpoDisplay == '12');
      expect(partLine.idKind, BulkIdKind.part);
      expect(partLine.idKind.fieldLabel, 'PART#');
      expect(partLine.tagOrPart, '4"Y-STRAINER-300');
      expect(partLine.quantity, 1);

      final tagLine = result.lines.firstWhere((l) => l.cpoDisplay == '13');
      expect(tagLine.idKind, BulkIdKind.tag);
      expect(tagLine.idKind.fieldLabel, 'TAG#');

      final expanded = result.expand();
      expect(expanded.where((e) => e.idKind == BulkIdKind.part).length, 1);
      expect(expanded.where((e) => e.idKind == BulkIdKind.tag).length, 2);
    });

    test('collects incomplete lines missing TAG#/PART# for dialog', () {
      final text =
          File('test/fixtures/propak_order_ack_sample.txt').readAsStringSync();
      final result = const OrderAckParser().parseText(text);
      expect(result.hasIncompleteLines, isTrue);
      expect(
        result.incompleteLines.any((l) => l.cpoDisplay == '28'),
        isTrue,
      );
      // Incomplete lines are not in the printable set until Proceed/Skip.
      expect(result.lines.any((l) => l.cpoDisplay == '28'), isFalse);

      final skipped = result.applyingMissingIdAction(BulkMissingIdAction.skip);
      expect(skipped.hasIncompleteLines, isFalse);
      expect(skipped.lines.any((l) => l.cpoDisplay == '28'), isFalse);
      expect(
        skipped.warnings.any((w) => w.contains('CPO #28') && w.contains('PM')),
        isTrue,
      );

      final proceeded =
          result.applyingMissingIdAction(BulkMissingIdAction.proceed);
      expect(proceeded.hasIncompleteLines, isFalse);
      final line28 = proceeded.lines.firstWhere((l) => l.cpoDisplay == '28');
      expect(line28.missingIdentity, isTrue);
      expect(line28.tagOrPart, isEmpty);
      expect(
        proceeded.warnings.any((w) => w.contains('CPO #28') && w.contains('PM')),
        isTrue,
      );
    });

    test('header: Bill To customer + Delivery Instructions ship-to (1425965)', () {
      final text =
          File('test/fixtures/propak_oa_1425965.txt').readAsStringSync();
      final result = const OrderAckParser().parseText(text);
      expect(result.customerName, 'PROPAK SYSTEMS LTD.');
      expect(result.orderNumber, '1425965');
      expect(result.projectNumber, 'P613120');
      expect(result.poNumber, 'P613120');
      expect(result.hasDeliveryShipTo, isTrue);
      expect(result.deliveryShipToName.toLowerCase(), contains('propak'));
      expect(result.deliveryShipToAddress.toLowerCase(), contains('veterans'));
      expect(result.deliveryCarrier.toUpperCase(), contains('ROSENAU'));
      expect(result.headerShipToName, 'PROPAK SYSTEMS LTD.');
    });

    test('header: Delivery Instructions name when freight is on the last line', () {
      final text =
          File('test/fixtures/propak_order_ack_sample.txt').readAsStringSync();
      final result = const OrderAckParser().parseText(text);
      expect(result.customerName, 'PROPAK SYSTEMS LTD.');
      expect(result.hasDeliveryShipTo, isTrue);
      expect(result.deliveryShipToName.toLowerCase(), contains('propak'));
      expect(result.deliveryShipToAddress.toLowerCase(), contains('east lake'));
      expect(result.deliveryCarrier.toLowerCase(), contains('rosenau'));
    });

    test('header: missing Delivery Instructions is flagged', () {
      const text = '''
ORDER ACKNOWLEDGEMENT
1420001
Bill To: 11693 Ship To:
ACME LTD.
1 MAIN ST
NISKU, AB T9E 1C6
CA
780-000-0000
ACME LTD.
1 MAIN ST
NISKU, AB T9E 1C6
CA
Ordered By: JANE
ProjectLocationPO Number
P111111
AFE # GL Code
Item DescriptionQuantityNo. UOM Unit Price Extended Price
1.00EA1.00 1.00WIDGET
1
Order Line Notes: CPO LINE 1
part # 1
''';
      final result = const OrderAckParser().parseText(text);
      expect(result.hasDeliveryShipTo, isFalse);
      expect(result.customerName, 'ACME LTD.');
      expect(result.headerShipToName, 'ACME LTD.');
      expect(result.headerShipToAddress.toLowerCase(), contains('main'));
    });

    test('packing list fills Swift packing slip number', () {
      const text = '''
PACKING LIST
PS-88991
Order Date
Order Number
Swift Oilfield Supply Inc.
1425965
Bill To: 11693 Ship To:
PROPAK SYSTEMS LTD.
440 EAST. LAKE ROAD
AIRDRIE, AB T4A 2J8
CA
403-912-7000
PROPAK SYSTEMS LTD.
440 EAST. LAKE ROAD
AIRDRIE, AB T4A 2J8
CA
Ordered By: RONDA MOORE
ProjectLocationPO Number
P613120
Packing Slip No. PS-88991
''';
      final result = const OrderAckParser().parseText(text);
      expect(result.documentKind, 'packing_list');
      expect(result.packingSlipNumber, 'PS-88991');
      expect(result.orderNumber, '1425965');
      expect(result.customerName, 'PROPAK SYSTEMS LTD.');
    });

    test('Spartan-style PO Location Project keeps full dotted refs', () {
      const text = '''
ORDER ACKNOWLEDGEMENT
1423246
Order Date
Order Number
Swift Oilfield Supply Inc.
08/04/2026
Sales Rep CHRIS.ACORN
Bill To: 11797 Ship To:
SPARTAN DELTA CORP.
350 - 7 AVENUE SW
CALGARY, AB T2P 3N9
CA
403-265-8011
SPARTAN DELTA CORP.
350 - 7 AVENUE SW
CALGARY, AB T2P 3N9
CA
Ordered By: Kyle Johnson
PO Number Location Project
4460.168-016	01-19-043-03W5M Riser Site	4460.168
Requisitioner Approver AFE # Cost Center # Work Order # GL Code
Kyle Johnson		26GAT609-O	351.04
Item DescriptionQuantityNo. UOM Unit Price Extended Price
9.00EA42.02 PRESSURE INDICATOR
1
''';
      final result = const OrderAckParser().parseText(text);
      expect(result.orderNumber, '1423246');
      expect(result.customerName, 'SPARTAN DELTA CORP.');
      expect(result.poNumber, '4460.168-016');
      expect(result.projectNumber, '4460.168');
      expect(result.jobLocation, contains('01-19-043-03W5M'));
      expect(result.jobLocation.toLowerCase(), contains('riser'));
      expect(result.requisitioner, 'Kyle Johnson');
      expect(result.afeNumber, '26GAT609-O');
      expect(result.specialInstructionsHint, contains('Riser'));
      expect(result.specialInstructionsHint, contains('AFE'));
      expect(result.headerShipToName, 'SPARTAN DELTA CORP.');
    });

    test('CPO LINES range + ITEM# + wrapped header PO (1431332)', () {
      final text =
          File('test/fixtures/propak_oa_1431332.txt').readAsStringSync();
      final result = const OrderAckParser().parseText(text);

      expect(result.orderNumber, '1431332');
      // The header "PO Number Location Project" row wraps the Location
      // across 3 physical lines before the PO value — must not surface the
      // street number or the raw location text as the PO#.
      expect(result.poNumber, 'P613979');
      expect(result.customerName, 'PROPAK SYSTEMS LTD.');
      expect(result.jobLocation, contains('404 East Lake Road'));
      expect(result.jobLocation, contains('T4A 2J8'));
      expect(result.requisitioner, 'MARLENE DUNAND');
      expect(
        result.warnings.any((w) => w.toLowerCase().contains('does not match')),
        isFalse,
      );

      // Every parsed line's PO# cross-check (per-line "PO# P613979" notes)
      // agrees with the header value — no mismatch warning.
      final expectedItems = <String, (String cpo, String item, int qty)>{
        '033047': ('1-4', '033047', 7),
        '033049': ('5-7', '033049', 5),
        '045428': ('8-13', '045428', 8),
        '045603': ('14-22', '045603', 9),
        '045624': ('23-27', '045624', 8),
        '045755': ('28-31', '045755', 6),
      };
      expect(result.lines.length, expectedItems.length);
      for (final line in result.lines) {
        expect(line.idKind, BulkIdKind.item);
        expect(line.idKind.fieldLabel, 'ITEM#');
        final expected = expectedItems[line.tagOrPart];
        expect(expected, isNotNull, reason: 'unexpected ITEM# ${line.tagOrPart}');
        expect(line.cpoDisplay, expected!.$1);
        expect(line.quantity, expected.$3);
      }
      // Range CPOs expand to the individual line numbers.
      final line1to4 = result.lines.firstWhere((l) => l.cpoDisplay == '1-4');
      expect(line1to4.cpoNumbers, [1, 2, 3, 4]);

      // The "No." 8 row (7.00 EA, same FLANGE description) has no Order Line
      // Notes at all in this OA — it must not appear as a line or an
      // incomplete line; there is nothing to tag it with.
      expect(result.incompleteLines, isEmpty);

      // ITEM#/PART# lines default to a single tag per line, not one per unit.
      expect(result.expand().length, expectedItems.length);
    });

    test('spaced PO Location Project row still splits dotted tokens', () {
      const text = '''
ORDER ACKNOWLEDGEMENT
1423246
PO Number Location Project
4460.168-016 01-19-043-03W5M Riser Site 4460.168
Bill To: 11797 Ship To:
SPARTAN DELTA CORP.
1 MAIN ST
CALGARY, AB T2P 3N9
CA
403-265-8011
SPARTAN DELTA CORP.
1 MAIN ST
CALGARY, AB T2P 3N9
CA
''';
      final result = const OrderAckParser().parseText(text);
      expect(result.poNumber, '4460.168-016');
      expect(result.projectNumber, '4460.168');
      expect(result.jobLocation, contains('Riser'));
    });
  });

  group('Avery 5163 tiling', () {
    test('sheetCount math', () {
      expect(BulkLabelPdf.perSheet, 10);
      expect((1 + 9) ~/ 10, 1);
      expect((10 + 9) ~/ 10, 1);
      expect((11 + 9) ~/ 10, 2);
    });
  });

  group('JobPdfAi.applyClaudeLineSuggestions', () {
    test('fills incomplete lines from Claude but keeps missingIdentity', () {
      final parsed = OrderAckParseResult(
        poNumber: 'P613979',
        orderNumber: '1431332',
        lines: const [],
        warnings: const [],
        incompleteLines: const [
          BulkIncompleteLine(
            lineNo: 1,
            cpoDisplay: '1-4',
            cpoNumbers: [1, 2, 3, 4],
            quantity: 7,
            description: 'FLANGE',
          ),
        ],
      );
      final out = JobPdfAi.applyClaudeLineSuggestions(parsed, const [
        ClaudeOrderAckLine(
          cpoDisplay: '1-4',
          poNumber: 'P613979',
          idKind: BulkIdKind.item,
          idValue: '033047',
          quantity: 7,
          reasoning: 'ITEM# on the note line',
          confidence: 'high',
        ),
      ]);
      expect(out.incompleteLines, isEmpty);
      expect(out.lines, hasLength(1));
      expect(out.lines.first.tagOrPart, '033047');
      expect(out.lines.first.idKind, BulkIdKind.item);
      expect(out.lines.first.missingIdentity, isTrue);
      expect(out.lines.first.aiNote, contains('AI-suggested'));
      expect(out.lines.first.aiNote, contains('confirm'));
    });

    test('keeps regex identity when Claude disagrees, attaches note', () {
      final parsed = OrderAckParseResult(
        poNumber: 'P1',
        orderNumber: '1',
        lines: [
          BulkLabelLine(
            lineNo: 2,
            cpoDisplay: '8, 9',
            cpoNumbers: const [8, 9],
            tagOrPart: 'ABC-1',
            idKind: BulkIdKind.tag,
            quantity: 2,
          ),
        ],
        warnings: const [],
      );
      final out = JobPdfAi.applyClaudeLineSuggestions(parsed, const [
        ClaudeOrderAckLine(
          cpoDisplay: '8, 9',
          poNumber: 'P1',
          idKind: BulkIdKind.part,
          idValue: 'OTHER',
          quantity: 2,
          reasoning: 'read as PART#',
          confidence: 'low',
        ),
      ]);
      expect(out.lines, hasLength(1));
      expect(out.lines.first.tagOrPart, 'ABC-1');
      expect(out.lines.first.idKind, BulkIdKind.tag);
      expect(out.lines.first.missingIdentity, isFalse);
      expect(out.lines.first.aiNote, contains('differently'));
      expect(out.lines.first.aiNote, contains('OTHER'));
    });

    test('attaches agree note when Claude matches regex', () {
      final parsed = OrderAckParseResult(
        poNumber: 'P1',
        orderNumber: '1',
        lines: [
          BulkLabelLine(
            lineNo: 3,
            cpoDisplay: '5',
            cpoNumbers: const [5],
            tagOrPart: '033047',
            idKind: BulkIdKind.item,
            quantity: 1,
          ),
        ],
        warnings: const [],
      );
      final out = JobPdfAi.applyClaudeLineSuggestions(parsed, const [
        ClaudeOrderAckLine(
          cpoDisplay: '5',
          poNumber: 'P1',
          idKind: BulkIdKind.item,
          idValue: '033047',
          quantity: 1,
          reasoning: 'plain ITEM#',
          confidence: 'high',
        ),
      ]);
      expect(out.lines.first.aiNote, contains('agrees'));
      expect(out.lines.first.tagOrPart, '033047');
    });
  });
}
