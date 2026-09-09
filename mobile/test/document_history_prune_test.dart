import 'package:flutter_test/flutter_test.dart';
import 'package:swift_shipping_label/document_history_sync.dart';
import 'package:swift_shipping_label/label_data.dart';

void main() {
  group('isHistoryLocalFileForId', () {
    test('matches cache PDF and form snapshot', () {
      expect(
        DocumentHistorySync.isHistoryLocalFileForId(
          'GCM_12345_abc123.pdf',
          'abc123',
        ),
        isTrue,
      );
      expect(
        DocumentHistorySync.isHistoryLocalFileForId('abc123.form.json', 'abc123'),
        isTrue,
      );
    });

    test('does not match Generate outputs without the id', () {
      expect(
        DocumentHistorySync.isHistoryLocalFileForId('GCM_12345.pdf', 'abc123'),
        isFalse,
      );
      expect(
        DocumentHistorySync.isHistoryLocalFileForId(
          'otherid.form.json',
          'abc123',
        ),
        isFalse,
      );
    });
  });

  test('treats nested Supabase 404 JSON as missing object', () {
    expect(
      DocumentHistorySync.isMissingStorageResponse(
        400,
        '{"statusCode":"404","error":"not_found","code":"NoSuchKey"}',
      ),
      isTrue,
    );
    expect(DocumentHistorySync.isMissingStorageResponse(404, ''), isTrue);
    expect(DocumentHistorySync.isMissingStorageResponse(401, '{}'), isFalse);
  });

  test('historyKinds covers shipping, receiving, BOL, and bulk', () {
    expect(
      DocumentHistorySync.historyKinds,
      [
        LabelKind.shipping,
        LabelKind.receiving,
        LabelKind.bol,
        LabelKind.bulk,
      ],
    );
  });

  group('newId', () {
    test('is not derived from / does not collide across a shared SO', () {
      // Two "different entries for the same SO/PO" must never collide on
      // id, regardless of how fast they're generated back-to-back.
      final ids = {for (var i = 0; i < 500; i++) DocumentHistorySync.newId()};
      expect(ids.length, 500, reason: 'every generated id must be unique');
    });

    test('is base36 (timestamp + random suffix), never SO/PO text', () {
      final id = DocumentHistorySync.newId();
      expect(RegExp(r'^[0-9a-z]+$').hasMatch(id), isTrue);
      expect(id, isNot(contains('P613979')));
    });
  });

  GeneratedDocumentRecord doc({
    required String id,
    String title = '',
    String customer = '',
    String salesOrder = '',
    String fileName = '',
  }) =>
      GeneratedDocumentRecord(
        id: id,
        kind: LabelKind.shipping,
        title: title,
        customer: customer,
        salesOrder: salesOrder,
        fileName: fileName,
        storagePath: 'shipping/$id/$fileName',
        byteSize: 100,
        createdAt: DateTime.utc(2026, 9, 1),
      );

  group('filterDocs (History Quick Search)', () {
    final docs = [
      doc(id: '1', title: 'Propak_P612207', customer: 'PROPAK SYSTEMS LTD.', salesOrder: 'P612207'),
      doc(id: '2', title: 'Arc_1425965', customer: 'Arc Resources LTD', salesOrder: '1425965'),
      doc(id: '3', title: '', customer: 'Spartan Delta Corp.', salesOrder: '4460.168-016', fileName: 'spartan_report.pdf'),
    ];

    test('empty query returns everything, unfiltered', () {
      expect(DocumentHistorySync.filterDocs(docs, ''), docs);
      expect(DocumentHistorySync.filterDocs(docs, '   '), docs);
    });

    test('matches case-insensitively across title/customer/SO/file name', () {
      expect(
        DocumentHistorySync.filterDocs(docs, 'propak').map((d) => d.id),
        ['1'],
      );
      expect(
        DocumentHistorySync.filterDocs(docs, 'ARC RESOURCES').map((d) => d.id),
        ['2'],
      );
      expect(
        DocumentHistorySync.filterDocs(docs, '4460.168').map((d) => d.id),
        ['3'],
      );
      expect(
        DocumentHistorySync.filterDocs(docs, 'spartan_report').map((d) => d.id),
        ['3'],
      );
    });

    test('no match returns an empty list', () {
      expect(DocumentHistorySync.filterDocs(docs, 'nonexistent-xyz'), isEmpty);
    });
  });

  group('pageCountFor / pageSlice (History pagination, 20/page)', () {
    test('page count rounds up and is always at least 1', () {
      expect(DocumentHistorySync.pageCountFor(0, 20), 1);
      expect(DocumentHistorySync.pageCountFor(1, 20), 1);
      expect(DocumentHistorySync.pageCountFor(20, 20), 1);
      expect(DocumentHistorySync.pageCountFor(21, 20), 2);
      expect(DocumentHistorySync.pageCountFor(45, 20), 3);
    });

    test('slices 45 items into pages of 20/20/5, in order', () {
      final items = [for (var i = 0; i < 45; i++) i];
      expect(DocumentHistorySync.pageSlice(items, 0, 20), List.generate(20, (i) => i));
      expect(
        DocumentHistorySync.pageSlice(items, 1, 20),
        List.generate(20, (i) => i + 20),
      );
      expect(DocumentHistorySync.pageSlice(items, 2, 20), [40, 41, 42, 43, 44]);
    });

    test('out-of-range page returns empty, never throws', () {
      final items = [1, 2, 3];
      expect(DocumentHistorySync.pageSlice(items, 5, 20), isEmpty);
      expect(DocumentHistorySync.pageSlice(items, -1, 20), isEmpty);
    });
  });
}
