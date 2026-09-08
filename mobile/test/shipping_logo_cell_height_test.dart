import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:swift_shipping_label/label_data.dart';
import 'package:swift_shipping_label/logo_ink_fit.dart';
import 'package:swift_shipping_label/pdf/shipping_label_pdf.dart';

/// Rendered ink must hit red/green cell heights (not tiny in the box).
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('Propak single: rendered ink height ≈ green box (46pt)', () async {
    final root = Directory.current.parent;
    final propak = File(
      '${root.path}/customer_logos/Propak-Energy-Services-Logo.png',
    );
    expect(propak.existsSync(), isTrue);
    final bytes = propak.readAsBytesSync();
    final ink = LogoInkFit.prepare(bytes).ink;
    expect(ink.isSquareOrCircle, isFalse);

    final shipping = await ShippingLabelPdf.load();
    final pdf = await shipping.build(
      data: ShippingLabelData.sample.copy()
        ..set(LabelFields.customer, 'PROPAK ENERGY SERVICES'),
      customerLogoBytes: [bytes],
    );
    expect(pdf.length, greaterThan(1000));

    // Tight crop: canvas equals ink — drawn PDF height matches target.
    expect(ink.canvasH, ink.height);
    expect(ink.canvasW, ink.width);
    expect(ink.height, greaterThan(ShippingLabelPdf.rectLogoTargetH),
        reason: 'embed full-res PNG, not pt-sized downsample');

    final target = ShippingLabelPdf.rectLogoTargetH;
    expect(
      ink.height * ink.scaleForHeight(target),
      closeTo(target, 0.01),
    );
  });

  test('Propak + ARJAE dual: independent red/green heights in prepare', () {
    final root = Directory.current.parent;
    final propak = LogoInkFit.prepare(
      File('${root.path}/customer_logos/Propak-Energy-Services-Logo.png')
          .readAsBytesSync(),
    ).ink;
    final arjae = LogoInkFit.prepare(
      File('${root.path}/customer_logos/ARJAE.png').readAsBytesSync(),
    ).ink;
    final squareH = ShippingLabelPdf.squareLogoTargetH;
    final rectH = ShippingLabelPdf.rectLogoTargetH;

    expect(propak.targetHeight(squareH: squareH, rectH: rectH), rectH);
    expect(arjae.targetHeight(squareH: squareH, rectH: rectH), squareH);

  // After tight crop, bitmap draw height equals ink target.
    expect(propak.canvasH, propak.height);
    expect(arjae.canvasH, arjae.height);
  });
}
