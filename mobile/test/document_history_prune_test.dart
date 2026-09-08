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
}
