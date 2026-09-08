import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:swift_shipping_label/label_data.dart';
import 'package:swift_shipping_label/pdf/bol_label_pdf.dart';
import 'package:swift_shipping_label/pdf/shipping_label_pdf.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('generate Shipping, Receiving, and BOL sample PDFs', () async {
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

    final shipBytes = await shipping.build(
      data: ShippingLabelData.sample,
      customerLogoBytes: logos,
      piecePlan: const PieceCountPlan(palletCrates: 2, boxes: 0),
    );
    final shipOut = File(
      '$root${sep}Swift Supply Shipping Label - Sample (app).pdf',
    );
    await shipOut.writeAsBytes(shipBytes);

    final recvBytes = await shipping.buildReceiving(
      data: ShippingLabelData.receivingSample,
      customerLogoBytes: logos,
    );
    final recvOut = File(
      '$root${sep}Swift Supply Receiving Label - Sample (app).pdf',
    );
    await recvOut.writeAsBytes(recvBytes);

    final bolBytes = await BolLabelPdf(shipping).build(
      data: ShippingLabelData.bolSample,
      customerLogoBytes: logos,
    );
    final bolOut = File(
      '$root${sep}Swift Supply Bill of Lading - Sample (app).pdf',
    );
    await bolOut.writeAsBytes(bolBytes);

    // ignore: avoid_print
    print('Wrote:\n  ${shipOut.path}\n  ${recvOut.path}\n  ${bolOut.path}');
    expect(shipBytes.length, greaterThan(1000));
    expect(recvBytes.length, greaterThan(1000));
    expect(bolBytes.length, greaterThan(1000));
  });
}
