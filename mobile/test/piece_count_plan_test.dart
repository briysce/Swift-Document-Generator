import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pdfrx/pdfrx.dart';
import 'package:swift_shipping_label/label_data.dart';
import 'package:swift_shipping_label/pdf/shipping_label_pdf.dart';

/// "Don't know the count yet" — the checkbox in the "How many labels?"
/// dialog that greys out both count fields and prints one blank-count label
/// instead of blocking on "enter at least one pallet/crate or box".
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('PieceCountPlan.isUndetermined', () {
    test('never counts as empty, even with zero counts', () {
      const plan = PieceCountPlan(isUndetermined: true);
      expect(plan.isEmpty, isFalse);
      expect(plan.totalPages, 0);
    });

    test('a plain zero/zero plan is still empty', () {
      const plan = PieceCountPlan();
      expect(plan.isEmpty, isTrue);
    });
  });

  test('undetermined plan renders exactly one page with blank pallet/box fields', () async {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
      const MethodChannel('plugins.flutter.io/path_provider'),
      (call) async {
        if (call.method == 'getTemporaryDirectory') {
          return Directory.systemTemp.path;
        }
        return null;
      },
    );
    await pdfrxFlutterInitialize();

    final shipping = await ShippingLabelPdf.load();
    // A plain ShippingLabelData(), not `.sample` — matches the real
    // generate flow, where palletNum/palletOf/boxNum/boxOf are never
    // user-typed form fields and start out blank like every other field.
    final data = ShippingLabelData({
      LabelFields.customer: 'ACME LTD.',
      LabelFields.shipTo: 'ACME WAREHOUSE',
    });
    final bytes = await shipping.build(
      data: data,
      piecePlan: const PieceCountPlan(isUndetermined: true),
    );

    final doc = await PdfDocument.openData(bytes);
    try {
      // Zero pallets + zero boxes would normally mean nothing to print;
      // `isUndetermined` is what keeps this at exactly one blank-count page
      // instead of the dialog's "enter at least one" validation blocking it.
      expect(doc.pages.length, 1);
    } finally {
      await doc.dispose();
    }
  });
}
