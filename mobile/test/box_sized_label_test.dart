import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:swift_shipping_label/label_data.dart';
import 'package:swift_shipping_label/pdf/shipping_label_pdf.dart';
import 'package:swift_shipping_label/pdf_render_options.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('box-sized shipping/receiving PDFs are landscape Letter', () async {
    final shipping = await ShippingLabelPdf.load();
    final root = Directory.current.parent.path;
    final sep = Platform.pathSeparator;

    Uint8List? logo;
    try {
      final data =
          await rootBundle.load('assets/images/sample_customer_logo.png');
      logo = data.buffer.asUint8List();
    } catch (_) {}
    final logos = logo == null ? <Uint8List>[] : [logo];
    const box = PdfRenderOptions(isBoxSized: true);

    final shipBytes = await shipping.build(
      data: ShippingLabelData.sample,
      customerLogoBytes: logos,
      piecePlan: const PieceCountPlan(palletCrates: 1, boxes: 0),
      options: box,
    );
    final recvBytes = await shipping.buildReceiving(
      data: ShippingLabelData.receivingSample,
      customerLogoBytes: logos,
      options: box,
    );

    final shipOut = File(
      '$root${sep}qa_logs${sep}box_sized_shipping_app.pdf',
    );
    final recvOut = File(
      '$root${sep}qa_logs${sep}box_sized_receiving_app.pdf',
    );
    await shipOut.parent.create(recursive: true);
    await shipOut.writeAsBytes(shipBytes);
    await recvOut.writeAsBytes(recvBytes);

    expect(shipBytes.length, greaterThan(1000));
    expect(recvBytes.length, greaterThan(1000));
    expect(_hasLandscapeLetterMediaBox(shipBytes), isTrue);
    expect(_hasLandscapeLetterMediaBox(recvBytes), isTrue);
  });
}

bool _hasLandscapeLetterMediaBox(Uint8List bytes) {
  final text = String.fromCharCodes(bytes);
  // Landscape Letter: 792 × 612 pt (11" × 8.5").
  return text.contains('/MediaBox [0 0 792 612]') ||
      text.contains('/MediaBox[0 0 792 612]');
}
