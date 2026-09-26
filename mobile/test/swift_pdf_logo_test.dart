import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
// ignore: implementation_imports
import 'package:pdf/src/svg/parser.dart';
import 'package:swift_shipping_label/brand_assets.dart';
import 'package:swift_shipping_label/label_data.dart';
import 'package:swift_shipping_label/pdf/bol_label_pdf.dart';
import 'package:swift_shipping_label/pdf/shipping_label_pdf.dart';
import 'package:xml/xml.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('Swift lockup SVG is vector paths in the PNG fallback\'s own box',
      () async {
    // The PDF painter scales by viewBox width and ignores its origin, and
    // falls back to the PNG in the same box — so the two must share it.
    final svg = await rootBundle.loadString(SwiftBrandAssets.logoOrangeSvg);
    expect(svg.contains('<image'), isFalse,
        reason: 'a raster in an SVG wrapper is not a vector');
    expect(svg.contains('<path'), isTrue);
    final vb = SvgParser(xml: XmlDocument.parse(svg)).viewBox;
    final data = await rootBundle.load(SwiftBrandAssets.logoOrange);
    final png = img.decodeImage(data.buffer.asUint8List())!;
    expect(vb.x, 0);
    expect(vb.y, 0);
    expect(vb.width, png.width.toDouble());
    expect(vb.height, png.height.toDouble());
  });

  test('PDF Swift lockup is swift_supply_logo_orange.png, not document/solid',
      () async {
    Future<Uint8List> loadAsset(String path) async {
      final data = await rootBundle.load(path);
      return data.buffer.asUint8List(data.offsetInBytes, data.lengthInBytes);
    }

    final orange = await loadAsset(SwiftBrandAssets.logoOrange);
    final document = await loadAsset(SwiftBrandAssets.logoDocument);
    final solid = await loadAsset(SwiftBrandAssets.logoOrangeSolid);
    expect(orange, isNot(equals(solid)));
    expect(orange, isNot(equals(document)));

    final pdf = await ShippingLabelPdf.load();
    expect(pdf.swiftLogoBytes, isNotNull);
    expect(pdf.swiftLogoBytes, equals(orange));
    expect(pdf.swiftLogoBytes, isNot(equals(solid)));
    expect(pdf.swiftLogoBytes, isNot(equals(document)));

    final decoded = img.decodeImage(pdf.swiftLogoBytes!);
    expect(decoded, isNotNull);
    final image = decoded!;

    var orangeUpper = 0;
    var blackLower = 0;
    var orangeLower = 0;
    var seamHoles = 0;
    final midY = image.height * 2 ~/ 5;
    final lowY = image.height * 55 ~/ 100;
    bool isOrange(img.Pixel p) {
      final a = p.a.toInt();
      final r = p.r.toInt();
      final g = p.g.toInt();
      final b = p.b.toInt();
      return a > 180 && r > 140 && g < 120 && b < 80;
    }

    bool isBlack(img.Pixel p) {
      final a = p.a.toInt();
      final r = p.r.toInt();
      final g = p.g.toInt();
      final b = p.b.toInt();
      return a > 180 && r < 40 && g < 40 && b < 40;
    }

    bool isHole(img.Pixel p) => p.a.toInt() < 80;

    for (var y = 1; y < image.height - 1; y += 2) {
      for (var x = 1; x < image.width - 1; x += 2) {
        final p = image.getPixel(x, y);
        if (p.a.toInt() >= 40) {
          final r = p.r.toInt();
          final g = p.g.toInt();
          final b = p.b.toInt();
          final orangePx = r > 140 && g < 120 && b < 80;
          final blackPx = r < 50 && g < 50 && b < 50;
          if (y < midY && orangePx) orangeUpper++;
          if (y >= lowY && blackPx) blackLower++;
          if (y >= lowY && orangePx) orangeLower++;
        }
        if (!isHole(p)) continue;
        final n = [
          image.getPixel(x - 1, y),
          image.getPixel(x + 1, y),
          image.getPixel(x, y - 1),
          image.getPixel(x, y + 1),
        ];
        if (n.any(isOrange) && n.any(isBlack)) seamHoles++;
      }
    }
    expect(orangeUpper, greaterThan(200),
        reason: 'SWIFT / bars must remain orange');
    expect(blackLower, greaterThan(80),
        reason: 'SUPPLY must stay opaque black, not knocked out');
    expect(orangeLower, greaterThan(10),
        reason: 'bottom bar must remain orange');
    expect(seamHoles, 0,
        reason: 'no transparent cracks between orange fill and black outline');

    final ship = await pdf.build(data: ShippingLabelData.sample);
    final recv = await pdf.buildReceiving(
      data: ShippingLabelData.receivingSample,
    );
    final bol = await BolLabelPdf(pdf).build(data: ShippingLabelData.bolSample);
    expect(ship.length, greaterThan(1000));
    expect(recv.length, greaterThan(1000));
    expect(bol.length, greaterThan(1000));
  });
}
