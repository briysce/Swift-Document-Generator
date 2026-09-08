import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:swift_shipping_label/label_data.dart';
import 'package:swift_shipping_label/logo_ink_fit.dart';
import 'package:swift_shipping_label/pdf/shipping_label_pdf.dart';

/// Dual customer logos must each hit their red/green cell height (not shared height).
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('Arc + square dual: independent red/green cell heights', () async {
    final root = Directory.current.parent;
    final arcFile = File('${root.path}/customer_logos/Arc Resources LTD.png');
    final squareFile = File('${root.path}/customer_logos/bfl fabricators.png');
    expect(arcFile.existsSync(), isTrue);
    expect(squareFile.existsSync(), isTrue);

    const squareH = ShippingLabelPdf.squareLogoTargetH;
    const rectH = ShippingLabelPdf.rectLogoTargetH;

    final arcInk = LogoInkFit.prepare(arcFile.readAsBytesSync()).ink;
    final squareInk = LogoInkFit.prepare(squareFile.readAsBytesSync()).ink;

    expect(arcInk.isSquareOrCircle, isFalse);
    expect(squareInk.isSquareOrCircle, isTrue);

    final arcTarget = arcInk.targetHeight(squareH: squareH, rectH: rectH);
    final squareTarget = squareInk.targetHeight(squareH: squareH, rectH: rectH);
    expect(arcTarget, rectH);
    expect(squareTarget, squareH);

    // Pink-limit scale uses ink width at class target height.
    final scales = LogoInkMetrics.rowScalesForPinkLimit(
      [arcInk, squareInk],
      squareH,
      rectH,
      ShippingLabelPdf.customerLogoGap,
      500,
    );

    final arcDrawH = arcInk.height * scales[0];
    final squareDrawH = squareInk.height * scales[1];
    expect(
      arcInk.height * arcInk.scaleForHeight(arcDrawH),
      closeTo(arcDrawH, 0.5),
      reason: 'Arc ink must fill green cell',
    );
    expect(
      squareInk.height * squareInk.scaleForHeight(squareDrawH),
      closeTo(squareDrawH, 0.5),
      reason: 'Square ink must fill red cell',
    );
    expect(arcDrawH, closeTo(rectH, 1.5), reason: 'Arc at green height');
    expect(squareDrawH, closeTo(squareH, 1.5), reason: 'Square at red height');
    expect(
      squareDrawH / arcDrawH,
      closeTo(squareH / rectH, 0.08),
      reason: 'red/green ratio preserved (not shared tiny height)',
    );

    final shipping = await ShippingLabelPdf.load();
    final bytes = await shipping.build(
      data: ShippingLabelData.sample.copy()
        ..set(LabelFields.customer, 'ARC C/O BFL'),
      customerLogoBytes: [
        arcFile.readAsBytesSync(),
        squareFile.readAsBytesSync(),
      ],
    );
    expect(bytes.length, greaterThan(2000));
  });
}
