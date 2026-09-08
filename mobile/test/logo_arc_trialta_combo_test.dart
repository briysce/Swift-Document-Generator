import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:swift_shipping_label/label_data.dart';
import 'package:swift_shipping_label/logo_ink_fit.dart';
import 'package:swift_shipping_label/pdf/shipping_label_pdf.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('Arc + Trialta dual logo layout metrics and PDF', () async {
    final root = Directory.current.parent;
    final arcFile = File('${root.path}/customer_logos/Arc Resources LTD.png');
    expect(arcFile.existsSync(), isTrue);

    // Prefer repo customer_logos; fall back to user attachment paths.
    File? trialtaFile;
    for (final name in ['Trialta Projects.png', 'TRIALTA.png', 'Trialta.png']) {
      final f = File('${root.path}/customer_logos/$name');
      if (f.existsSync()) {
        trialtaFile = f;
        break;
      }
    }
    trialtaFile ??= File(
      r'C:\Users\Brice\.cursor\projects\c-Users-Brice-Projects-swift-document-generator\assets\c__Users_Brice_AppData_Roaming_Cursor_User_workspaceStorage_4bff5e36c39e59914d9e1ad0a9394a2a_images_trialta-cc2b883c-b9f7-4c63-a3df-1f9a433fbfb1.png',
    );
    expect(trialtaFile.existsSync(), isTrue);

    const squareH = ShippingLabelPdf.squareLogoTargetH;
    const rectH = ShippingLabelPdf.rectLogoTargetH;
    for (final f in [arcFile, trialtaFile]) {
      final prep = LogoInkFit.prepare(f.readAsBytesSync());
      final ink = prep.ink;
      final targetH = ink.targetHeight(squareH: squareH, rectH: rectH);
      final drawW = ink.drawWidth(targetH);
      final drawH = ink.drawHeight(targetH);
      // Visible ink maps to red/green target; bitmap may be slightly taller.
      expect(
        ink.height * ink.scaleForHeight(targetH),
        closeTo(targetH, 0.001),
      );
      expect(
        drawH,
        lessThanOrEqualTo(targetH * 1.06),
        reason: '${f.path} drawH=$drawH canvas=${ink.canvasW}x${ink.canvasH} '
            'ink=${ink.width}x${ink.height} target=$targetH',
      );
      final canvasAspect = ink.canvasW / ink.canvasH;
      final drawAspect = drawW / drawH;
      expect(drawAspect, closeTo(canvasAspect, 0.05));
    }

    final shipping = await ShippingLabelPdf.load();
    final bytes = await shipping.build(
      data: ShippingLabelData.sample.copy()
        ..set(LabelFields.customer, 'Arc Resources LTD'),
      customerLogoBytes: [
        arcFile.readAsBytesSync(),
        trialtaFile.readAsBytesSync(),
      ],
    );

    final outDir = Directory('${root.path}/filled/logo_combo');
    await outDir.create(recursive: true);
    final out = File('${outDir.path}/arc_trialta_shipping.pdf');
    await out.writeAsBytes(bytes);
    expect(bytes.length, greaterThan(1000));
  });
}
