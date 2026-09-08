import 'dart:math' as math;
import 'dart:typed_data';

import 'package:pdf/pdf.dart';
import 'package:pdf/src/svg/painter.dart';
import 'package:pdf/src/svg/parser.dart';
import 'package:pdf/widgets.dart' as pw;
import 'package:vector_math/vector_math_64.dart';
import 'package:xml/xml.dart';

import '../bol_item_type.dart';
import '../label_data.dart';
import '../logo_image_process.dart';
import '../logo_ink_fit.dart';
import '../pdf_render_options.dart';
import 'shipping_label_pdf.dart';

class _BolInkLogo {
  _BolInkLogo(this.image, this.ink);
  final PdfImage image;
  final LogoInkMetrics ink;
}

/// Flat print Bill of Lading — geometry ported from `generate_swift_bol_pdf.py`.
/// Three pages: STORE / DRIVER / CUSTOMER copy (same field values).
class BolLabelPdf {
  BolLabelPdf(this.shipping);

  final ShippingLabelPdf shipping;

  /// Last-page layout dump for QA / improve-loop (PDF pt, bottom-left origin).
  /// Cleared at the start of each page paint; read after [build]/appendPages].
  static Map<String, dynamic>? debugLayout;

  static const swift = PdfColor.fromInt(0xFFCE4E30);
  static const swiftLight = PdfColor.fromInt(0xFFFDF4F1);
  static const black = PdfColor.fromInt(0xFF1A1A1A);
  static const secondary = PdfColor.fromInt(0xFF4A4A4A);
  static const muted = PdfColor.fromInt(0xFF767676);
  static const hint = PdfColor.fromInt(0xFF9A9A9A);
  static const rule = PdfColor.fromInt(0xFFC8C8C8);
  static const tableHead = PdfColor.fromInt(0xFFF0F0F0);
  static const zebra = PdfColor.fromInt(0xFFF7F7F7);
  static const fieldBg = PdfColor.fromInt(0xFFF4F7FA);
  static const white = PdfColor.fromInt(0xFFFFFFFF);

  static final pageFormat = PdfPageFormat.letter;
  static const inch = PdfPageFormat.inch;
  static final margin = 0.4 * inch;
  static final contentW = pageFormat.width - 2 * margin;
  static final footerH = 0.52 * inch;
  static final footerBase = margin + 2;
  static final footerBoxH = footerH - 2;
  static final contentBottom = footerBase + footerBoxH + 8;
  /// Outer page/frame left edge (page frame + disclaimer footer).
  static final frameX = margin - 3;
  /// Outer page/frame width (page frame + disclaimer footer).
  static final frameW = contentW + 6;
  static const gap = 7.0;
  static const pad = 6.0;
  static const bodyInset = 8.0;
  static const inset = 3.0;
  /// Distance from cell/band top down to micro-label baseline (pt).
  static const microFromTop = 8.0;
  /// Gap from micro-label baseline to value-box top — same rhythm as Shipping
  /// field labels (`_fieldRow` uses 3pt). Applied across BOL cells.
  static const microToValueGap = 3.0;
  /// Default height for uploaded customer/C/O logos on the BOL header (pt).
  /// Also the single-logo max and the dual-logo square/circular target —
  /// matches Swift's own presence on this row.
  static const customerLogoTargetH = 59.0;
  /// Dual customer logos only: square/circular target height (same as
  /// [customerLogoTargetH]) vs. rectangular/wide target height — mirrors the
  /// Shipping/Receiving 74/58 treatment so a wide mark doesn't read as more
  /// prominent than a square one just because it's wider.
  static const dualSquareLogoTargetH = customerLogoTargetH;
  static const dualRectLogoTargetH = 46.0;
  /// Safety gap between customer logo frame and Probill / Swift.
  static const customerToProbillGap = 12.0;
  /// Legacy alias — same clearance used toward Swift when Probill is absent.
  static const customerToSwiftGap = customerToProbillGap;
  /// Gap between dual customer logo cells.
  static const customerStackGap = 8.0;
  /// Keep uploaded logos inside the header band (above the orange title bar).
  static const logoBandInset = 1.0;
  /// Locked Probill cut-out width (pt).
  static final probillBoxW = 2.35 * inch;
  /// Gap between Swift wordmark and Probill cut-out.
  static const probillCutGap = 14.0;
  static const lineRows = 10;
  static List<String> get itemTypes => BolItemTypes.productTotalLabels;
  static const productTotalLeft = ['Pallets', 'Crates', 'Boxes'];
  static const productTotalRight = ['Pipes', 'Other'];
  /// Still summed for rollups; not drawn in the compact 2-col Product Total grid.
  static const productTotalHidden = ['Bundles'];
  static const copyTypes = ['STORE COPY', 'DRIVER COPY', 'CUSTOMER COPY'];
  static const shipperLines = [
    'Swift Oilfield Supply',
    'Unit 200, 920 - 36 Avenue',
    'Nisku, AB  T9E 1C6',
    '780-423-6979',
  ];

  static const legal =
      'RECEIVED AT THE POINT OF ORIGIN ON THE DATE SPECIFIED, FROM THE CONSIGNOR '
      'MENTIONED HEREIN, THE PROPERTY DESCRIBED IN APPARENT GOOD ORDER EXCEPT AS '
      'NOTED (CONTENTS AND CONDITION OF CONTENTS OF PACKAGES UNKNOWN), MARKED, '
      'CONSIGNED, AND DESTINED AS INDICATED BELOW. NOTICE OF CLAIM: (A) NO CARRIER '
      'IS LIABLE FOR LOSS, DAMAGE, OR DELAY TO ANY GOODS UNDER THIS BILL OF LADING '
      'UNLESS NOTICE THEREOF IS GIVEN IN WRITING TO THE ORIGINATING CARRIER OR THE '
      'DELIVERING CARRIER WITHIN FIVE (5) DAYS AFTER DELIVERY OF THE GOODS, OR IN '
      'THE CASE OF FAILURE TO MAKE DELIVERY, WITHIN NINE (9) MONTHS FROM THE DATE OF '
      'SHIPMENT. (B) THE FINAL STATEMENT OF THE CLAIM MUST BE FILED WITHIN NINE (9) '
      'MONTHS FROM THE DATE OF SHIPMENT, TOGETHER WITH A COPY OF THE PAID FREIGHT '
      'BILL. THE CONTRACT FOR CARRIAGE IS DEEMED TO CONTAIN AND BE SUBJECT TO ALL '
      'CONDITIONS NOT PROHIBITED BY LAW UNDER THE MOTOR TRANSPORT ACT (ALBERTA).';

  static const shipperCert =
      'This is to certify that the above-named materials are properly classified, '
      'described, packaged, marked and labeled, and are in proper condition for '
      'transportation according to the applicable regulations of the Department of '
      'Transportation.';

  Future<Uint8List> build({
    required ShippingLabelData data,
    List<Uint8List> customerLogoBytes = const [],
    Uint8List? shipperSignatureBytes,
    /// Which copy pages to include (subset of [copyTypes]). Defaults to all three.
    List<String>? copies,
    PdfRenderOptions options = PdfRenderOptions.defaults,
  }) async {
    final doc = pw.Document(
      title: 'Swift Oilfield Supply — Straight Bill of Lading',
      author: 'Swift Oilfield Supply',
    );
    await appendPages(
      doc,
      data: data,
      customerLogoBytes: customerLogoBytes,
      shipperSignatureBytes: shipperSignatureBytes,
      copies: copies,
      options: options,
    );
    return doc.save();
  }

  /// Append BOL copy pages onto an existing document.
  Future<void> appendPages(
    pw.Document doc, {
    required ShippingLabelData data,
    List<Uint8List> customerLogoBytes = const [],
    Uint8List? shipperSignatureBytes,
    List<String>? copies,
    PdfRenderOptions options = PdfRenderOptions.defaults,
  }) async {
    final selected = (copies == null || copies.isEmpty)
        ? List<String>.from(copyTypes)
        : [
            for (final c in copyTypes)
              if (copies.contains(c)) c,
          ];
    if (selected.isEmpty) {
      throw ArgumentError('At least one BOL copy must be selected.');
    }

    // BOL layout is fixed to portrait Letter.
    final format = pageFormat;
    shipping.activeFontScale = options.fontScale.clamp(0.8, 1.35);

    for (final copy in selected) {
      doc.addPage(
        pw.Page(
          pageFormat: format,
          margin: pw.EdgeInsets.zero,
          build: (context) {
            final fonts = _Fonts(
              regular: pw.Font.helvetica().getFont(context),
              bold: pw.Font.helveticaBold().getFont(context),
            );
            PdfImage? swiftLogo;
            if (shipping.swiftLogoBytes != null) {
              swiftLogo = PdfImage.file(
                context.document,
                bytes: shipping.swiftLogoBytes!,
              );
            }
            final customerLogos = <_BolInkLogo>[];
            if (options.showCustomerLogos &&
                options.logoPlacement != PdfLogoPlacement.hidden) {
              for (final b in customerLogoBytes.take(maxCustomerLogos)) {
                if (b.isNotEmpty) {
                  final prepared = LogoInkFit.prepare(b);
                  customerLogos.add(
                    _BolInkLogo(
                      PdfImage.file(context.document, bytes: prepared.png),
                      prepared.ink,
                    ),
                  );
                }
              }
            }
            return pw.CustomPaint(
              size: PdfPoint(format.width, format.height),
              painter: (c, size) {
                PdfImage? shipperSig;
                if (shipperSignatureBytes != null &&
                    shipperSignatureBytes.isNotEmpty) {
                  final ink = LogoImageProcessor.prepareSignatureInk(
                    shipperSignatureBytes,
                  );
                  shipperSig = PdfImage.file(
                    context.document,
                    bytes: ink.isNotEmpty ? ink : shipperSignatureBytes,
                  );
                }
                return _paint(
                  c,
                  fonts,
                  data,
                  copy,
                  swiftLogo,
                  customerLogos,
                  shipperSignature: shipperSig,
                );
              },
            );
          },
        ),
      );
    }
  }

  /// BOL pages first, then shipping label pages in one PDF.
  Future<Uint8List> buildWithShippingLabels({
    required ShippingLabelData bolData,
    required ShippingLabelData shippingData,
    required PieceCountPlan piecePlan,
    List<Uint8List> customerLogoBytes = const [],
    Uint8List? shipperSignatureBytes,
    List<String>? copies,
    PdfRenderOptions options = PdfRenderOptions.defaults,
  }) async {
    final doc = pw.Document(
      title: 'Swift Oilfield Supply — BOL + Shipping Labels',
      author: 'Swift Oilfield Supply',
    );
    await appendPages(
      doc,
      data: bolData,
      customerLogoBytes: customerLogoBytes,
      shipperSignatureBytes: shipperSignatureBytes,
      copies: copies,
      options: options,
    );
    await shipping.appendPages(
      doc,
      data: shippingData,
      customerLogoBytes: customerLogoBytes,
      piecePlan: piecePlan,
      options: options,
    );
    return doc.save();
  }

  void _paint(
    PdfGraphics c,
    _Fonts fonts,
    ShippingLabelData d,
    String copyLabel,
    PdfImage? swiftLogo,
    List<_BolInkLogo> customerLogos, {
    PdfImage? shipperSignature,
  }) {
    final pageW = pageFormat.width;
    final pageH = pageFormat.height;
    var y = pageH - margin - 2;
    debugLayout = {
      'copy_label': copyLabel,
      'page_format': {'width': pageW, 'height': pageH},
      'margin': margin,
      'content_w': contentW,
      'customer_to_probill_gap': customerToProbillGap,
      'micro_to_value_gap': microToValueGap,
    };

    // Header row: Swift + Probill are absolutely locked first. Customer logos
    // may only occupy the remaining left frame and never move static elements.
    const swiftBandH = customerLogoTargetH;
    var swiftH = swiftBandH;
    var swiftW = 0.0;
    var swiftX = margin;
    var swiftY = y - swiftH;

    if (swiftLogo != null) {
      final ink = shipping.swiftInk;
      if (ink != null && ink.isValid) {
        var targetH = swiftBandH;
        swiftW = ink.drawWidth(targetH);
        final maxW = contentW * 0.42;
        if (swiftW > maxW && ink.canvasW > 0) {
          targetH = maxW * ink.height / ink.canvasW;
          swiftW = ink.drawWidth(targetH);
        }
        swiftH = targetH;
        swiftX = pageW - margin - swiftW;
        if (swiftX < margin) swiftX = margin;
        swiftY = ink.bitmapBottomY(y - swiftH, targetH);
        _drawSwiftLogo(
          c,
          swiftLogo,
          swiftX,
          swiftY,
          ink.drawWidth(targetH),
          ink.drawHeight(targetH),
        );
      } else {
        final iw = swiftLogo.width.toDouble();
        final ih = swiftLogo.height.toDouble();
        if (iw > 0 && ih > 0) {
          swiftW = swiftH * iw / ih;
          final maxW = contentW * 0.42;
          if (swiftW > maxW) {
            swiftW = maxW;
            swiftH = swiftW * ih / iw;
          }
          swiftX = pageW - margin - swiftW;
          if (swiftX < margin) swiftX = margin;
          swiftY = y - swiftH;
          _drawSwiftLogo(c, swiftLogo, swiftX, swiftY, swiftW, swiftH);
        }
      }
    }
    debugLayout!['swift_rect'] = {
      'x': swiftX,
      'y': swiftY,
      'w': swiftW,
      'h': swiftH,
    };

    // Probill locks immediately left of Swift (hardcoded relative to Swift).
    // Customer logos never feed these coordinates.
    final probill = _probillBox(swiftX, swiftW, y, swiftH);
    debugLayout!['probill_rect'] = {
      'x': probill.x,
      'y': probill.y,
      'w': probill.w,
      'h': probill.h,
    };
    final staticLeft = swiftW > 0
        ? math.min(swiftX, probill.x)
        : probill.x;
    _drawCustomerLogosLeft(
      c,
      customerLogos,
      logoTop: y,
      bandH: math.max(swiftH, customerLogoTargetH),
      frameRightLimit: staticLeft - customerToProbillGap,
      dualCenterRightX: staticLeft,
    );
    _paintProbillCutout(c, fonts, d, probill);
    y -= math.max(swiftH, customerLogoTargetH) + 6;

    // Title band
    const bandH = 22.0;
    debugLayout!['title_bar'] = {
      'x': margin,
      'y': y - bandH,
      'w': contentW,
      'h': bandH,
    };
    _orangeBar(c, margin, y - bandH, contentW, bandH, r: 3);
    _drawCentered(
      c,
      fonts.bold,
      8.5,
      'SWIFT OILFIELD SUPPLY',
      pageW / 2,
      y - 11,
      color: white,
    );
    _drawCentered(
      c,
      fonts.regular,
      6.5,
      'STRAIGHT BILL OF LADING  ·  NOT NEGOTIABLE',
      pageW / 2,
      y - 18,
      color: white,
    );
    c
      ..setFillColor(white)
      ..setFont(fonts.bold, 7)
      ..drawString(
        fonts.bold,
        7,
        copyLabel,
        margin + contentW - pad - _sw(fonts.bold, 7, copyLabel),
        y - bandH + 6,
      );
    y -= bandH + 10;

    y = _metaStrip(c, fonts, d, y);

    final colW = (contentW - gap) / 2;
    final lx = margin;
    final rx = margin + colW + gap;
    const hdrH = 15.0;
    // Equal-height panels sized for a 3-line Delivery Address + compact contacts.
    const nameH = 20.0;
    const addrH = 46.0;
    const contactH = 20.0;
    const panelH = nameH + addrH + contactH;

    _sectionTitle(c, fonts, lx, y, colW, 'SHIPPER (CONSIGNOR)', hdrH);
    _sectionTitle(c, fonts, rx, y, colW, 'SHIP TO (CONSIGNEE)', hdrH);
    final top = y - hdrH;
    final bot = top - panelH;
    _rect(c, lx, bot, colW, panelH, lw: 0.75);
    // Vertically center shipper lines — no open blank strip under the address.
    _drawShipperBlockCentered(c, fonts, lx + pad, top, bot);

    final row1 = top - nameH;
    final row2 = top - nameH - addrH;
    final midX = rx + colW / 2;
    _cellValue(
      c,
      fonts,
      rx,
      row1,
      colW,
      top - row1,
      'Ship To Name',
      d.get(BolFields.consigneeName),
    );
    _cellValue(
      c,
      fonts,
      rx,
      row2,
      colW,
      row1 - row2,
      'Delivery Address',
      d.get(BolFields.consigneeAddress),
      maxLines: 3,
    );
    _cellValue(
      c,
      fonts,
      rx,
      bot,
      midX - rx,
      row2 - bot,
      'Contact Name',
      d.get(BolFields.consigneeContactName),
      compact: true,
    );
    _cellValue(
      c,
      fonts,
      midX,
      bot,
      rx + colW - midX,
      row2 - bot,
      'Contact Number',
      d.get(BolFields.consigneeContactNumber),
      compact: true,
    );
    y = bot - gap;

    final billH = 0.42 * inch;
    _sectionTitle(c, fonts, lx, y, colW, '3RD PARTY BILLING (COLLECT)', hdrH);
    final billBodyBot = y - hdrH - billH;
    _rect(c, lx, billBodyBot, colW, billH, lw: 0.75);
    _drawValue(
      c,
      fonts,
      d.get(BolFields.thirdPartyBilling),
      lx + pad,
      billBodyBot + pad,
      colW - 2 * pad,
      billH - 2 * pad,
      7,
      maxLines: 3,
    );

    final freightH = hdrH + billH;
    _rect(c, rx, y - freightH, colW, freightH, lw: 0.75);
    _sectionTitle(c, fonts, rx, y, colW, 'FREIGHT CHARGES', hdrH);
    final freight = d.get(BolFields.freightCharges).toLowerCase().trim();
    // Classic thin-ring radios (match standard PDF/browser controls).
    const radioSize = 8.5;
    const labelSize = 6.5;
    const radioLabelGap = 3.0;
    final freightBodyBot = y - freightH;
    final freightBodyTop = y - hdrH;
    final rowCenter = (freightBodyBot + freightBodyTop) / 2;
    final ry = rowCenter - radioSize / 2;
    // Vertically center label with the radio circle.
    final labelY = rowCenter - labelSize * 0.32;
    final options = [
      for (final o in BolFields.freightChargeOptions)
        (
          o.$1,
          BolFields.freightChargePdfLabels[o.$1] ?? o.$2,
        ),
    ];
    final innerLeft = rx + pad + 2;
    final innerRight = rx + colW - pad - 2;
    final widths = [
      for (final o in options)
        radioSize + radioLabelGap + _sw(fonts.regular, labelSize, o.$2),
    ];
    final totalW = widths.fold<double>(0, (a, b) => a + b);
    final free = (innerRight - innerLeft - totalW).clamp(0.0, double.infinity);
    final radioGap =
        options.length > 1 ? free / (options.length - 1) : 0.0;
    var ox = innerLeft;
    for (var i = 0; i < options.length; i++) {
      final key = options[i].$1;
      final label = options[i].$2;
      final on = freight == key ||
          freight == label.toLowerCase() ||
          freight == BolFields.freightChargesDisplay(key).toLowerCase() ||
          (key == BolFields.freightThirdParty &&
              (freight == '3rd party' || freight == 'third party')) ||
          (key == BolFields.freightCustomerPickup &&
              (freight == 'customer pick-up' ||
                  freight == 'customer pickup' ||
                  freight == 'cust. pick-up' ||
                  freight == 'pick-up' ||
                  freight == 'pickup'));
      _radio(c, ox, ry, radioSize, on);
      c
        ..setFillColor(black)
        ..setFont(fonts.regular, labelSize)
        ..drawString(
          fonts.regular,
          labelSize,
          label,
          ox + radioSize + radioLabelGap,
          labelY,
        );
      ox += widths[i] + radioGap;
    }
    y -= freightH + gap;

    // Tracking
    const trackHdr = 13.0;
    _sectionTitle(
      c,
      fonts,
      margin,
      y,
      contentW,
      'TRACKING & REFERENCE NUMBERS',
      trackHdr,
    );
    y -= trackHdr;
    final trackCols = [
      (BolFields.orderNum, 'ORDER #', 1.15),
      (LabelFields.poNum, 'PO #', 1.15),
      (BolFields.packingList, 'PACKING LIST #', 1.45),
      (LabelFields.project, 'PROJECT', 1.35),
    ];
    const th = 12.0, rh = 14.0;
    final tSum = trackCols.fold<double>(0, (a, e) => a + e.$3);
    final tWidths = trackCols.map((e) => contentW * e.$3 / tSum).toList();
    var cx = margin;
    for (var i = 0; i < trackCols.length; i++) {
      c
        ..setFillColor(tableHead)
        ..drawRect(cx, y - th, tWidths[i], th)
        ..fillPath();
      _rect(c, cx, y - th, tWidths[i], th);
      c
        ..setFillColor(secondary)
        ..setFont(fonts.bold, 5)
        ..drawString(fonts.bold, 5, trackCols[i].$2, cx + 3, y - th + 3.5);
      cx += tWidths[i];
    }
    y -= th;
    cx = margin;
    final trackCells = <Map<String, dynamic>>[];
    // Prefer 2-line wrap + shrink so extreme refs stay visible (never vanish).
    const trackMaxLines = 2;
    const trackValueSize = 7.0;
    final trackValueH = math.max(
      rh - 2 * inset,
      trackMaxLines * (trackValueSize * 0.72 + 1) - 1,
    );
    final trackRowH = math.max(rh, trackValueH + 2 * inset);
    // If we grew the row, shift the value band down from the header bottom.
    final valueTop = y;
    final valueBot = y - trackRowH;
    for (var i = 0; i < trackCols.length; i++) {
      final key = trackCols[i].$1;
      final value = _trackingValue(d, key);
      final cellX = cx;
      final cellY = valueBot;
      final cellW = tWidths[i];
      final cellH = trackRowH;
      _rect(c, cellX, cellY, cellW, cellH);
      _drawValue(
        c,
        fonts,
        value,
        cellX + inset,
        cellY + inset,
        cellW - 2 * inset,
        cellH - 2 * inset,
        trackValueSize,
        maxLines: trackMaxLines,
        shrinkToFit: true,
        minSize: 4.0,
      );
      trackCells.add({
        'key': key,
        'label': trackCols[i].$2,
        'value': value,
        'value_non_empty': value.trim().isNotEmpty,
        'x': cellX,
        'y': cellY,
        'w': cellW,
        'h': cellH,
        'value_box': {
          'x': cellX + inset,
          'y': cellY + inset,
          'w': cellW - 2 * inset,
          'h': cellH - 2 * inset,
        },
      });
      cx += tWidths[i];
    }
    debugLayout!['tracking_row'] = {
      'header_y': valueTop + th,
      'value_y': valueBot,
      'row_h': trackRowH,
      'cells': trackCells,
    };
    y = valueBot - gap;

    // Line items
    final lineCols = [
      ('pieces', 'QTY', 0.55),
      ('item_type', 'ITEM TYPE', 1.05),
      ('dimensions', 'DIMENSIONS', 0.95),
      ('description', 'DESCRIPTION OF GOODS', 2.2),
      ('weight', 'WEIGHT (LBS)', 0.8),
    ];
    const lh = 14.0, lr = 14.0;
    final lSum = lineCols.fold<double>(0, (a, e) => a + e.$3);
    final lWidths = lineCols.map((e) => contentW * e.$3 / lSum).toList();
    cx = margin;
    for (var i = 0; i < lineCols.length; i++) {
      _orangeBar(c, cx, y - lh, lWidths[i], lh, r: 0);
      _rect(c, cx, y - lh, lWidths[i], lh, lw: 0.4);
      c
        ..setFillColor(white)
        ..setFont(fonts.bold, 5)
        ..drawString(fonts.bold, 5, lineCols[i].$2, cx + 3, y - lh + 4);
      cx += lWidths[i];
    }
    y -= lh;

    var totalPieces = 0.0;
    var totalWeight = 0.0;
    final typeTotals = {for (final t in itemTypes) t: 0.0};

    for (var row = 0; row < lineRows; row++) {
      cx = margin;
      final n = row + 1;
      final pieces = d.get(BolFields.lineKey(n, 'pieces'));
      final itypeRaw = d.get(BolFields.lineKey(n, 'item_type'));
      final dims = d.get(BolFields.lineKey(n, 'dimensions'));
      final desc = d.get(BolFields.lineKey(n, 'description'));
      final weight = d.get(BolFields.lineKey(n, 'weight'));
      final pq = double.tryParse(pieces.replaceAll(',', '')) ?? 0;
      final itype = BolItemTypes.displayForm(itypeRaw, pq);
      final vals = [pieces, itype, dims, desc, weight];
      final wq = double.tryParse(weight.replaceAll(',', '')) ?? 0;
      totalPieces += pq;
      totalWeight += wq;
      final cat = BolItemTypes.totalCategory(itypeRaw);
      if (cat.isNotEmpty) {
        for (final t in itemTypes) {
          if (cat.toLowerCase() == t.toLowerCase()) {
            typeTotals[t] = (typeTotals[t] ?? 0) + pq;
          }
        }
      }
      for (var i = 0; i < lineCols.length; i++) {
        if (row.isOdd) {
          c
            ..setFillColor(zebra)
            ..drawRect(cx, y - lr, lWidths[i], lr)
            ..fillPath();
        }
        _rect(c, cx, y - lr, lWidths[i], lr);
        _drawValue(
          c,
          fonts,
          vals[i],
          cx + inset,
          y - lr + inset,
          lWidths[i] - 2 * inset,
          lr - 2 * inset,
          7,
        );
        cx += lWidths[i];
      }
      y -= lr;
    }
    y -= gap;

    // Product total (2-col) | Special instructions | Totals — compact band
    final blockH = 0.92 * inch;
    const bHdr = 14.0;
    _alignedBottomRow(
      c,
      fonts,
      d,
      y,
      blockH,
      bHdr,
      typeTotals,
      totalPieces,
      totalWeight,
    );
    y -= blockH + gap;

    // Signature columns — content-sized height; never hang into the disclaimer.
    final sw = (contentW - 2 * gap) / 3;
    const shdr = 14.0;
    var sx = margin;
    final bodyTop = y - shdr;
    const aboveDisclaimerGap = 8.0;
    final minBodyBot = footerBase + footerBoxH + aboveDisclaimerGap;
    final maxSh = bodyTop - minBodyBot;
    // Prefer compact frames; only shrink further if the page is tight.
    final sh = math.min(1.28 * inch, maxSh);
    final bodyBot = bodyTop - sh;

    // Shipper's Certification
    _sectionTitle(c, fonts, sx, y, sw, "SHIPPER'S CERTIFICATION", shdr);
    _rect(c, sx, bodyBot, sw, sh, lw: 0.75);
    final certBot = bodyBot + sh * 0.55;
    _wrapText(
      c,
      fonts,
      shipperCert,
      sx + pad,
      bodyTop - bodyInset,
      sw - 2 * pad,
      size: 4.4,
      leading: 5.2,
      color: secondary,
      minY: certBot + 3,
    );
    c
      ..setStrokeColor(rule)
      ..setLineWidth(0.5)
      ..drawLine(sx, certBot, sx + sw, certBot)
      ..strokePath();
    _sigFields(
      c,
      fonts,
      d,
      sx,
      certBot,
      bodyBot,
      sw,
      [
        [(BolFields.shipperCertName, 'Name', 1.0)],
        [
          (BolFields.shipperCertSign, 'Signature', 0.62),
          (BolFields.shipperCertDate, 'Date', 0.38),
        ],
      ],
      topInset: 7,
      signatureImages: shipperSignature == null
          ? null
          : {BolFields.shipperCertSign: shipperSignature},
    );
    sx += sw + gap;

    // Carrier / Driver Acceptance — 4 rows (was 5): Vehicle ID shares the
    // bottom row with Departure Date (calendar) so micro-labels can breathe.
    _sectionTitle(c, fonts, sx, y, sw, 'CARRIER / DRIVER ACCEPTANCE', shdr);
    _rect(c, sx, bodyBot, sw, sh, lw: 0.75);
    _sigFields(
      c,
      fonts,
      d,
      sx,
      bodyTop,
      bodyBot,
      sw,
      [
        [(BolFields.driverCompany, 'Company', 1.0)],
        [(BolFields.driverPrint, 'Driver Print Name', 1.0)],
        [(BolFields.driverSign, 'Signature', 1.0)],
        [
          (BolFields.vehicleId, 'Vehicle ID', 0.62),
          (BolFields.driverDate, 'Departure Date', 0.38),
        ],
      ],
      topInset: bodyInset,
      bottomInset: 5.0,
    );
    sx += sw + gap;

    // Consignee Delivery Receipt
    _sectionTitle(c, fonts, sx, y, sw, 'CONSIGNEE DELIVERY RECEIPT', shdr);
    _rect(c, sx, bodyBot, sw, sh, lw: 0.75);
    _sigFields(
      c,
      fonts,
      d,
      sx,
      bodyTop,
      bodyBot,
      sw,
      [
        [(BolFields.consigneeSign, 'Consignee Signature', 1.0)],
        [(BolFields.consigneePrint, 'Print Name', 1.0)],
        [(BolFields.consigneeDate, 'Date', 1.0)],
      ],
      topInset: bodyInset,
    );

    _drawPageFrame(
      c,
      pageH,
      // Frame ends with the signature boxes — disclaimer sits cleanly below.
      contentBot: bodyBot - 4,
    );
    _drawFooter(c, fonts);
  }

  /// Draws the Swift lockup as true vector paths from [shipping]'s loaded
  /// SVG when available — mathematically lossless at any zoom/print DPI,
  /// unlike a raster embed. Falls back to the raster [swiftLogo] PNG
  /// (identical box) if the SVG is missing or fails to render for any
  /// reason. Same approach as `shipping_label_pdf.dart`'s `_drawSwiftLogo`
  /// — see its comment for why this drives [SvgPainter] directly instead of
  /// `pw.Widget.draw`/`SvgImage`.
  void _drawSwiftLogo(
    PdfGraphics c,
    PdfImage swiftLogo,
    double x,
    double y,
    double w,
    double h,
  ) {
    final svg = shipping.swiftLogoSvg;
    if (svg != null) {
      var pushed = false;
      try {
        final parser = SvgParser(xml: XmlDocument.parse(svg));
        final vb = parser.viewBox;
        if (vb.width > 0 && vb.height > 0) {
          final scale = w / vb.width;
          c.saveContext();
          pushed = true;
          c.setTransform(
            Matrix4.identity()
              ..translateByDouble(x, y + h, 0, 1)
              ..scaleByDouble(scale, -scale, 1, 1),
          );
          SvgPainter(
            parser,
            c,
            swiftLogo.pdfDocument,
            PdfRect(0, 0, pageFormat.width, pageFormat.height),
          ).paint();
          c.restoreContext();
          return;
        }
      } catch (_) {
        if (pushed) {
          try {
            c.restoreContext();
          } catch (_) {}
        }
      }
    }
    c.drawImage(swiftLogo, x, y, w, h);
  }

  void _alignedBottomRow(
    PdfGraphics c,
    _Fonts fonts,
    ShippingLabelData d,
    double y,
    double blockH,
    double hdrH,
    Map<String, double> typeTotals,
    double totalPieces,
    double totalWeight,
  ) {
    final blockBot = y - blockH;
    // Wider Product Total for dual columns; SI narrows; Totals slightly tighter.
    final pw = 2.05 * inch;
    final tw = 1.12 * inch;
    final iw = contentW - pw - tw - 2 * gap;
    final ix = margin + pw + gap;
    final tx = ix + iw + gap;
    final bodyH = blockH - hdrH;
    final bodyTop = y - hdrH;

    _sectionTitle(c, fonts, margin, y, pw, 'PRODUCT TOTAL', hdrH);
    _rect(c, margin, blockBot, pw, bodyH, lw: 0.75);

    _sectionTitle(c, fonts, ix, y, iw, 'SPECIAL INSTRUCTIONS', hdrH);
    _rect(c, ix, blockBot, iw, bodyH, lw: 0.75);

    _sectionTitle(c, fonts, tx, y, tw, 'TOTALS', hdrH);
    c
      ..setFillColor(swiftLight)
      ..drawRect(tx, blockBot, tw, bodyH)
      ..fillPath();
    _rect(c, tx, blockBot, tw, bodyH, lw: 0.75);

    // Product totals: 2-column grid (left stack + right stack).
    const leftCats = productTotalLeft;
    const rightCats = productTotalRight;
    final nRows = leftCats.length > rightCats.length
        ? leftCats.length
        : rightCats.length;
    const topInset = 5.0;
    final avail = bodyH - topInset - 3;
    final rowH = avail / nRows;
    const colGap = 5.0;
    final innerW = pw - 2 * pad;
    final colW = (innerW - colGap) / 2;

    void productTotalCol(List<String> cats, double colX) {
      for (var i = 0; i < cats.length; i++) {
        final label = cats[i];
        final rowTop = bodyTop - topInset - i * rowH;
        final labelY = rowTop - microFromTop + 1.5;
        _micro(c, fonts, colX, labelY, label);
        // Value band under the micro-label, resting on the underline.
        final fieldTop = labelY - microToValueGap;
        final fieldBot = rowTop - rowH + 2;
        final fieldH = (fieldTop - fieldBot).clamp(9.0, rowH);
        final lineW = colW - 1;
        final v = typeTotals[label] ?? 0;
        final text = v > 0
            ? v.toStringAsFixed(v.truncateToDouble() == v ? 0 : 1)
            : '';
        _drawCenteredInRect(
          c,
          fonts,
          text,
          colX,
          fieldBot,
          lineW,
          fieldH,
          size: 8,
        );
        _hline(c, colX, fieldBot, lineW);
      }
    }

    productTotalCol(leftCats, margin + pad);
    productTotalCol(rightCats, margin + pad + colW + colGap);

    final siBot = blockBot + 3;
    final siH = bodyH - topInset - 2;
    _drawValue(
      c,
      fonts,
      d.get(LabelFields.specialInstructions),
      ix + pad,
      siBot,
      iw - 2 * pad,
      siH < 18 ? 18 : siH,
      7,
      maxLines: 5,
    );

    final midTot = blockBot + bodyH / 2;
    c
      ..setStrokeColor(rule)
      ..setLineWidth(0.45)
      ..drawLine(tx, midTot, tx + tw, midTot)
      ..strokePath();

    // Totals: reserve label strip via shared micro→value gap; center numerals.
    const lbsReserve = 10.0;
    const totNumSize = 10.0;
    const totValueNudgeUp = 2.0;
    final totLabelY = bodyTop - microFromTop + 2;
    _micro(c, fonts, tx + pad, totLabelY, 'Total Piece Count');
    final piecesText = totalPieces > 0
        ? totalPieces.toStringAsFixed(0)
        : d.get(BolFields.totalPieces);
    _drawCenteredInRect(
      c,
      fonts,
      piecesText,
      tx + pad,
      midTot + 2,
      tw - 2 * pad,
      (totLabelY - microToValueGap) - (midTot + 2),
      size: totNumSize,
      shrinkToFit: false,
      nudgeUp: totValueNudgeUp,
    );

    final weightLabelY = midTot - microFromTop + 2;
    _micro(c, fonts, tx + pad, weightLabelY, 'Total Weight');
    final weightText = totalWeight > 0
        ? totalWeight.toStringAsFixed(0)
        : d.get(BolFields.totalWeight);
    _drawCenteredInRect(
      c,
      fonts,
      weightText,
      tx + pad,
      blockBot + lbsReserve,
      tw - 2 * pad,
      (weightLabelY - microToValueGap) - (blockBot + lbsReserve),
      size: totNumSize,
      shrinkToFit: false,
      nudgeUp: totValueNudgeUp,
    );
    c
      ..setFillColor(hint)
      ..setFont(fonts.regular, 5);
    final lbs = 'LBS';
    c.drawString(
      fonts.regular,
      5,
      lbs,
      tx + (tw - _sw(fonts.regular, 5, lbs)) / 2,
      blockBot + 3,
    );
  }

  void _sigFields(
    PdfGraphics c,
    _Fonts fonts,
    ShippingLabelData d,
    double x,
    double yTop,
    double yBottom,
    double w,
    List<List<(String, String, double)>> rows, {
    double? topInset,
    double? bottomInset,
    Map<String, PdfImage>? signatureImages,
  }) {
    final innerX = x + pad;
    final innerW = w - 2 * pad;
    final n = rows.length;
    // Prefer shared micro→value rhythm; only tighten outer insets when ≥5 rows.
    final dense = n >= 5;
    final insetTop = topInset ?? (dense ? 3.0 : bodyInset);
    final insetBot = bottomInset ?? (dense ? 3.0 : 5.0);
    final contentTop = yTop - insetTop;
    final contentBot = yBottom + insetBot;
    final availH = contentTop - contentBot;
    if (availH < 20 || rows.isEmpty) return;

    final valueSize = dense ? 7.0 : 8.0;
    final ruleAboveBandBot = dense ? 2.0 : 3.5;
    const colGap = 10.0;
    final rowH = availH / n;

    for (var i = 0; i < n; i++) {
      final bandTop = contentTop - i * rowH;
      final bandBot = bandTop - rowH;
      final yRule = bandBot + ruleAboveBandBot;
      final labelY = bandTop - microFromTop + 2.5;
      // Same gap as `_cellValue` / Shipping field labels.
      final valueTop = labelY - microToValueGap;
      final valueBot = yRule + 1.0;
      final valueBandH = (valueTop - valueBot).clamp(valueSize, rowH);

      final row = rows[i];
      final total = row.fold<double>(0, (a, e) => a + e.$3);
      var cx = innerX;
      final avail = innerW - colGap * (row.length - 1);
      for (final cell in row) {
        final fw = avail * (cell.$3 / total);
        _micro(c, fonts, cx, labelY, cell.$2);
        final sigImg = signatureImages?[cell.$1];
        if (sigImg != null) {
          final maxW = fw - 2;
          // Previous fit used a short band under the label; allow ~3× that
          // height (capped by the full row) so saved signatures read clearly.
          final legacyMaxH = (labelY - yRule - 2.0).clamp(8.0, rowH - 8.0);
          final maxH = math
              .min(bandTop - yRule - 1.0, legacyMaxH * 3.0)
              .clamp(12.0, rowH - 2.0);
          final iw = sigImg.width.toDouble();
          final ih = sigImg.height.toDouble();
          if (iw > 0 && ih > 0 && maxH > 4 && maxW > 4) {
            final fitScale = math.min(maxW / iw, maxH / ih);
            final imgW = iw * fitScale;
            final imgH = ih * fitScale;
            final imgX = cx + 1 + (maxW - imgW) / 2;
            final imgY = yRule + 1 + ((bandTop - yRule - 1) - imgH) / 2;
            c.drawImage(sigImg, imgX, imgY, imgW, imgH);
          }
        } else {
          final value = _latin1(d.get(cell.$1)).trim();
          if (value.isNotEmpty) {
            final valueBaseline =
                valueBot + (valueBandH - valueSize) / 2;
            c
              ..setFillColor(black)
              ..setFont(fonts.bold, valueSize)
              ..drawString(fonts.bold, valueSize, value, cx, valueBaseline);
          }
        }
        _hline(c, cx, yRule, fw);
        cx += fw + colGap;
      }
    }
  }

  /// ORDER # prefers [BolFields.orderNum], then sales-order aliases used by the form.
  String _trackingValue(ShippingLabelData d, String key) {
    if (key == BolFields.orderNum) {
      final order = d.get(BolFields.orderNum).trim();
      if (order.isNotEmpty) return order;
      return d.get(LabelFields.salesOrder).trim();
    }
    if (key == BolFields.packingList) {
      final pack = d.get(BolFields.packingList).trim();
      if (pack.isNotEmpty) return pack;
      return d.get(LabelFields.packingSlip).trim();
    }
    return d.get(key).trim();
  }

  /// Locked Probill geometry from Swift only (customer logos never feed this).
  /// Prefers the slot immediately left of Swift so the left header stays free
  /// for customer logos; falls back to the right only if left cannot fit.
  ({double x, double y, double w, double h}) _probillBox(
    double logoX,
    double logoW,
    double logoTop,
    double logoH,
  ) {
    final boxW = probillBoxW;
    final boxH = logoH < 0.85 * inch ? logoH : 0.85 * inch;
    final leftX = logoX - probillCutGap - boxW;
    final rightX = logoX + logoW + probillCutGap;
    // Prefer left-of-Swift so customer logos have a stable left frame.
    double boxX;
    if (leftX >= margin) {
      boxX = leftX;
    } else if (rightX + boxW <= margin + contentW - 2) {
      boxX = rightX;
    } else {
      boxX = margin.clamp(margin, margin + contentW - boxW);
    }
    final boxY = logoTop - logoH + (logoH - boxH) / 2;
    return (x: boxX, y: boxY, w: boxW, h: boxH);
  }

  void _paintProbillCutout(
    PdfGraphics c,
    _Fonts fonts,
    ShippingLabelData d,
    ({double x, double y, double w, double h}) box,
  ) {
    final boxX = box.x;
    final boxY = box.y;
    final boxW = box.w;
    final boxH = box.h;

    c
      ..setFillColor(const PdfColor.fromInt(0xFFFAFAFA))
      ..drawRect(boxX, boxY, boxW, boxH)
      ..fillPath()
      ..setStrokeColor(swift)
      ..setLineWidth(1)
      ..setLineDashPattern(const [3, 2])
      ..drawRect(boxX, boxY, boxW, boxH)
      ..strokePath()
      ..setLineDashPattern(const []);

    _drawCentered(
      c,
      fonts.bold,
      6,
      'PROBILL',
      boxX + boxW / 2,
      boxY + boxH - 11,
      color: swift,
    );
    _drawCentered(
      c,
      fonts.regular,
      5,
      'AFFIX STICKER HERE',
      boxX + boxW / 2,
      boxY + boxH - 20,
      color: muted,
    );
    _drawValue(
      c,
      fonts,
      d.get(BolFields.probillNumber),
      boxX + 6,
      boxY + 6,
      boxW - 12,
      12,
      8,
    );
    _hline(c, boxX + 6, boxY + 6, boxW - 12);
  }

  double _metaStrip(
    PdfGraphics c,
    _Fonts fonts,
    ShippingLabelData d,
    double y,
  ) {
    const stripH = 28.0;
    final bot = y - stripH;
    c
      ..setFillColor(fieldBg)
      ..drawRect(margin, bot, contentW, stripH)
      ..fillPath();
    _rect(c, margin, bot, contentW, stripH, lw: 0.75);
    final colW = contentW / 3;
    final specs = [
      (BolFields.documentNumber, 'Document Number'),
      (BolFields.documentDate, 'Date'),
      (BolFields.bookingRef, 'Booking Ref'),
    ];
    for (var i = 0; i < specs.length; i++) {
      final cx = margin + i * colW;
      if (i > 0) {
        c
          ..setStrokeColor(rule)
          ..setLineWidth(0.5)
          ..drawLine(cx, bot, cx, y)
          ..strokePath();
      }
      _micro(c, fonts, cx + pad, y - microFromTop, specs[i].$2);
      final valueTop = y - microFromTop - microToValueGap;
      final valueH = (valueTop - (bot + 3)).clamp(8.0, 14.0);
      _drawValue(
        c,
        fonts,
        d.get(specs[i].$1),
        cx + 4,
        bot + 3,
        colW - 8,
        valueH,
        8,
      );
      _hline(c, cx + 4, bot + 3, colW - 8);
    }
    return bot - gap;
  }

  void _drawPageFrame(PdfGraphics c, double pageH, {required double contentBot}) {
    final top = pageH - margin + 4;
    final bot = contentBot - 2;
    c
      ..setStrokeColor(rule)
      ..setLineWidth(0.75)
      ..drawRect(frameX, bot, frameW, top - bot)
      ..strokePath()
      ..setFillColor(swift)
      ..drawRect(frameX, pageH - margin + 2, frameW, 3)
      ..fillPath();
  }

  void _drawFooter(PdfGraphics c, _Fonts fonts) {
    final boxY = footerBase;
    final boxH = footerBoxH;
    final boxTop = boxY + boxH;
    // Full frame width — not inset — so the disclaimer does not look floating.
    c
      ..setStrokeColor(rule)
      ..setLineWidth(0.5)
      ..drawRect(frameX, boxY, frameW, boxH)
      ..strokePath()
      ..setFillColor(swift)
      ..drawRect(frameX, boxTop - 2, frameW, 2)
      ..fillPath();

    const size = 5.2;
    const leading = 6.2;
    final textW = frameW - 2 * pad;
    final textH = _measureWrapped(fonts, legal, textW, size: size, leading: leading);
    final bodyTop = boxTop - 3;
    final bodyBot = boxY + 2;
    final bodyH = bodyTop - bodyBot;
    final topPad = ((bodyH - textH) / 2).clamp(1.0, bodyH);
    final legalTop = bodyTop - topPad - size + 0.5;
    _wrapText(
      c,
      fonts,
      legal,
      frameX + pad,
      legalTop,
      textW,
      size: size,
      leading: leading,
      color: muted,
      minY: bodyBot + 1,
      bold: false,
    );
  }

  void _orangeBar(
    PdfGraphics c,
    double x,
    double y,
    double w,
    double h, {
    double r = 3,
  }) {
    c.setFillColor(swift);
    if (r > 0) {
      c.drawRRect(x, y, w, h, r, r);
    } else {
      c.drawRect(x, y, w, h);
    }
    c.fillPath();
  }

  void _rect(
    PdfGraphics c,
    double x,
    double y,
    double w,
    double h, {
    double lw = 0.5,
  }) {
    c
      ..setStrokeColor(rule)
      ..setLineWidth(lw)
      ..drawRect(x, y, w, h)
      ..strokePath();
  }

  void _hline(PdfGraphics c, double x, double y, double w) {
    c
      ..setStrokeColor(rule)
      ..setLineWidth(0.4)
      ..drawLine(x, y, x + w, y)
      ..strokePath();
  }

  void _sectionTitle(
    PdfGraphics c,
    _Fonts fonts,
    double x,
    double y,
    double w,
    String text,
    double h,
  ) {
    // Sharp corners so panel grids join cleanly.
    c
      ..setFillColor(swift)
      ..drawRect(x, y - h, w, h)
      ..fillPath();
    _rect(c, x, y - h, w, h, lw: 0.5);
    const fontSize = 6.5;
    c
      ..setFillColor(white)
      ..setFont(fonts.bold, fontSize)
      ..drawString(
        fonts.bold,
        fontSize,
        text,
        x + pad,
        y - h + (h - fontSize) / 2 + 0.5,
      );
  }

  void _micro(PdfGraphics c, _Fonts fonts, double x, double y, String text) {
    c
      ..setFillColor(muted)
      ..setFont(fonts.bold, 5.5)
      ..drawString(fonts.bold, 5.5, text.toUpperCase(), x, y);
  }

  /// Scale so visible ink is [targetH] tall. Width follows aspect.
  /// Clip is a safety net; logos are scaled to fit the left frame first.
  ({double w, double h, double y}) _inkDraw(
    LogoInkMetrics ink,
    double inkBottomY,
    double targetH,
  ) {
    return (
      w: ink.drawWidth(targetH),
      h: ink.drawHeight(targetH),
      y: ink.bitmapBottomY(inkBottomY, targetH),
    );
  }

  void _drawLogoInCell(
    PdfGraphics c,
    _BolInkLogo logo,
    double cellX,
    double cellY,
    double cellW,
    double cellH,
    double targetH,
  ) {
    if (!logo.ink.isValid) return;
    // Fit ink width first, then shrink so the full bitmap (incl. transparent
    // pad) stays inside the cell — clip is a backstop, not the primary clamp.
    var h = math.min(
      LogoInkMetrics.fitHeightToWidth(logo.ink, targetH, cellW),
      cellH,
    );
    if (h <= 0) return;
    final drawH0 = logo.ink.drawHeight(h);
    if (drawH0 > cellH && drawH0 > 0) {
      h *= cellH / drawH0;
    }
    h = math.min(h, LogoInkMetrics.fitHeightToWidth(logo.ink, h, cellW));
    if (h < 1) return;

    var draw = _inkDraw(logo.ink, cellY, h);
    // Keep bitmap fully inside the cell (shift up if pad would hang below).
    if (draw.y < cellY) {
      draw = (w: draw.w, h: draw.h, y: cellY);
    }
    if (draw.y + draw.h > cellY + cellH) {
      final overflow = (draw.y + draw.h) - (cellY + cellH);
      draw = (w: draw.w, h: draw.h, y: draw.y - overflow);
      if (draw.y < cellY) {
        // Still too tall — uniform shrink to cell.
        final scale = cellH / draw.h;
        h *= scale;
        draw = _inkDraw(logo.ink, cellY, h);
        if (draw.y < cellY) {
          draw = (w: draw.w, h: math.min(draw.h, cellH), y: cellY);
        }
      }
    }
    final drawW = math.min(draw.w, cellW);
    final drawH = math.min(draw.h, cellH);
    final cellDrawX = cellX + math.max(0.0, (cellW - drawW) / 2);
    c.drawImage(logo.image, cellDrawX, draw.y, drawW, drawH);
    final boxes = debugLayout?['customer_logo_boxes'];
    if (boxes is List) {
      boxes.add({
        'x': cellX,
        'y': draw.y,
        'w': drawW,
        'h': drawH,
        'cell': {'x': cellX, 'y': cellY, 'w': cellW, 'h': cellH},
      });
    }
  }

  /// Max ink height that fits inside a fixed [boxW]×[boxH] frame (may upscale
  /// up to [boxH] when width allows — never exceeds the box).
  static double _maxInkHeightInBox(
    LogoInkMetrics ink,
    double boxW,
    double boxH,
  ) =>
      LogoInkMetrics.fitHeightToWidth(ink, boxH, boxW);

  /// Uploaded logos only — left of the locked Probill / Swift static zone.
  ///
  /// Logos are clipped to a fixed frame between the left margin and
  /// [frameRightLimit]. The header band height is fixed by the caller
  /// ([bandH]); this method never grows layout below the orange title bar.
  /// Single marks scale up to fill the frame; dual marks share equal-width
  /// cells and shrink uniformly so both stay inside the same frame.
  void _drawCustomerLogosLeft(
    PdfGraphics c,
    List<_BolInkLogo> customerLogos, {
    required double logoTop,
    required double bandH,
    required double frameRightLimit,
    required double dualCenterRightX,
  }) {
    final logos = <_BolInkLogo>[];
    for (final l in customerLogos.take(maxCustomerLogos)) {
      if (l.image.width > 0 && l.image.height > 0 && l.ink.isValid) {
        logos.add(l);
      }
    }
    if (logos.isEmpty) {
      debugLayout?['customer_logo_frame'] = null;
      debugLayout?['customer_logo_boxes'] = <Map<String, dynamic>>[];
      return;
    }

    final frameLeft = margin;
    final frameRight = math.min(
      frameRightLimit,
      margin + contentW,
    );
    final frameW = frameRight - frameLeft;
    if (frameW < 8) {
      debugLayout?['customer_logo_frame'] = {
        'x': frameLeft,
        'y': logoTop - bandH,
        'w': frameW,
        'h': bandH,
        'skipped': true,
      };
      debugLayout?['customer_logo_boxes'] = <Map<String, dynamic>>[];
      return;
    }

    final frameTop = logoTop - logoBandInset;
    final frameBot = logoTop - bandH + logoBandInset;
    final frameH = (frameTop - frameBot).clamp(1.0, 10000.0);
    if (frameH <= 0) return;

    debugLayout?['customer_logo_frame'] = {
      'x': frameLeft,
      'y': frameBot,
      'w': frameW,
      'h': frameH,
      'right_limit': frameRightLimit,
    };
    debugLayout?['customer_logo_boxes'] = <Map<String, dynamic>>[];

    // Clip region is the union of the single-logo frame and the wider
    // wall-to-Probill span the dual-logo row centers within below — a
    // safety backstop only, never what either path sizes itself to.
    final clipLeft = math.min(frameLeft, frameX);
    final clipRight = math.max(frameRight, dualCenterRightX);
    c.saveContext();
    c
      ..drawRect(clipLeft, frameBot, clipRight - clipLeft, frameH)
      ..clipPath();

    if (logos.length == 1) {
      // Sizing keeps the existing Probill safety-gapped frame width (never
      // grows bigger than before) — but centers within the true wall-to-
      // Probill span (same as the dual-logo case) instead of the narrower
      // sizing frame, so a wide mark that fills that whole frame doesn't
      // end up flush against the left wall with all the slack on the right.
      final targetH = _maxInkHeightInBox(logos.first.ink, frameW, frameH);
      final centerLeft = frameX;
      final centerSpanW = math.max(0.0, dualCenterRightX - centerLeft);
      _drawLogoInCell(
        c,
        logos.first,
        centerLeft,
        frameBot,
        centerSpanW,
        frameH,
        targetH,
      );
      c.restoreContext();
      return;
    }

    // Dual logos: square/circular reads taller than rectangular/wide (same
    // rule as Shipping/Receiving), each keeps its own aspect-driven width
    // (not a fixed 50/50 split), and both share one vertical midline so a
    // taller square mark and a shorter wide mark still read as aligned.
    //
    // Sizing keeps the existing Probill safety-gapped frame width (so logos
    // never grow larger than the single-logo case would allow) — but the
    // resulting row is then centered between the true page-frame wall and
    // the Probill cutout's own left edge, not the narrower sizing frame.
    // That guarantees equal, visible clearance on both sides instead of the
    // old zero-gap-left / full-gap-right split, even when two wide logos
    // together need most of the sizing frame's width.
    if (frameW < 8) {
      c.restoreContext();
      return;
    }
    final inks = logos.map((l) => l.ink).toList();
    final centerLeft = frameX;
    final centerSpanW = math.max(0.0, dualCenterRightX - centerLeft);

    // Two wide/rectangular logos side by side would each get squeezed to a
    // sliver (both fighting for half the width AND sharing one height cap)
    // — stacking instead lets each keep the full available width and only
    // splits the vertical budget, which reads far more legibly for wide
    // marks. Square/circular logos (alone or paired with a wide mark) keep
    // the side-by-side treatment below, unaffected.
    if (inks.every((ink) => !ink.isSquareOrCircle)) {
      final rowH = (frameH - customerStackGap) / 2;
      final heights = <double>[];
      final widths = <double>[];
      for (final ink in inks) {
        final h = _maxInkHeightInBox(
          ink,
          frameW,
          math.min(dualRectLogoTargetH, rowH),
        );
        heights.add(h);
        widths.add(ink.inkDrawWidth(h));
      }
      final stackTotalH = heights[0] + customerStackGap + heights[1];
      final bandCenter = (frameTop + frameBot) / 2;
      var rowTopY = bandCenter + stackTotalH / 2;
      final stackBoxes = debugLayout?['customer_logo_boxes'];
      for (var i = 0; i < logos.length; i++) {
        final h = heights[i];
        if (h < 1) continue;
        final w = widths[i];
        final rowY = rowTopY - h;
        final rowX = centerLeft + math.max(0.0, (centerSpanW - w) / 2);
        c.drawImage(logos[i].image, rowX, rowY, w, h);
        if (stackBoxes is List) {
          stackBoxes.add({'x': rowX, 'y': rowY, 'w': w, 'h': h});
        }
        rowTopY = rowY - customerStackGap;
      }
      c.restoreContext();
      return;
    }

    final scales = LogoInkMetrics.rowScalesForPinkLimit(
      inks,
      math.min(dualSquareLogoTargetH, frameH),
      math.min(dualRectLogoTargetH, frameH),
      customerStackGap,
      frameW,
    );
    final widths = [
      for (var i = 0; i < inks.length; i++) inks[i].width * scales[i],
    ];
    final rowTotalW =
        widths.fold(0.0, (a, b) => a + b) + customerStackGap * (logos.length - 1);
    final bandCenter = (frameTop + frameBot) / 2;
    var x = centerLeft + math.max(0.0, (centerSpanW - rowTotalW) / 2);
    debugLayout?['customer_logo_row'] = {
      'center_left': centerLeft,
      'center_right': dualCenterRightX,
      'center_span_w': centerSpanW,
      'row_total_w': rowTotalW,
      'start_x': x,
    };
    final boxes = debugLayout?['customer_logo_boxes'];
    for (var i = 0; i < logos.length; i++) {
      final h = inks[i].height * scales[i];
      if (h < 1) continue;
      final w = widths[i];
      final inkBottomY = bandCenter - h / 2;
      c.drawImage(logos[i].image, x, inkBottomY, w, h);
      if (boxes is List) {
        boxes.add({'x': x, 'y': inkBottomY, 'w': w, 'h': h});
      }
      x += w + customerStackGap;
    }
    c.restoreContext();
  }

  /// Classic radio: thin dark-gray ring; selected = centered dark-gray dot.
  /// [PdfGraphics.drawEllipse] takes center + radii (not a bounding box).
  void _radio(PdfGraphics c, double x, double y, double size, bool on) {
    final cx = x + size / 2;
    final cy = y + size / 2;
    const ringColor = secondary;
    final stroke = (size * 0.09).clamp(0.75, 1.05);
    final outerR = size / 2;
    final holeR = (outerR - stroke).clamp(1.0, outerR);
    final dotR = (size * 0.20).clamp(1.6, holeR - 0.6);

    void fillCircle(double r, PdfColor color) {
      c
        ..setFillColor(color)
        ..drawEllipse(cx, cy, r, r)
        ..fillPath();
    }

    fillCircle(outerR, ringColor);
    fillCircle(holeR, white);
    if (on) {
      fillCircle(dotR, ringColor);
    }
  }

  void _cellValue(
    PdfGraphics c,
    _Fonts fonts,
    double x,
    double y,
    double w,
    double h,
    String label,
    String value, {
    int? maxLines,
    bool compact = false,
  }) {
    _rect(c, x, y, w, h, lw: 0.6);
    // Same micro→value gap whether compact or full — only value line count differs.
    final labelY = y + h - microFromTop;
    _micro(c, fonts, x + pad, labelY, label);
    final topReserve = microFromTop + microToValueGap;
    final botPad = compact ? 2.5 : 3.5;
    final lines = maxLines ?? (h > 36 ? 3 : (h > 28 ? 2 : 1));
    _drawValue(
      c,
      fonts,
      value,
      x + pad,
      y + botPad,
      w - 2 * pad,
      (h - topReserve - botPad).clamp(8.0, h),
      7.5,
      maxLines: lines,
    );
  }

  void _drawShipperBlockCentered(
    PdfGraphics c,
    _Fonts fonts,
    double x,
    double top,
    double bot, {
    double size = 8.0,
  }) {
    const leadingExtra = 4.0;
    final blockH = shipperLines.isEmpty
        ? 0.0
        : shipperLines.length * size + (shipperLines.length - 1) * leadingExtra;
    final panelH = top - bot;
    const inset = 8.0;
    final avail = math.max(panelH - 2 * inset, blockH);
    var sy = top - inset - (avail - blockH) / 2;
    for (final line in shipperLines) {
      c
        ..setFillColor(black)
        ..setFont(fonts.bold, size)
        ..drawString(fonts.bold, size, line, x, sy);
      sy -= size + leadingExtra;
    }
  }

  String _latin1(String text) => text
      .replaceAll('\u2014', '-')
      .replaceAll('\u2013', '-')
      .replaceAll('\u2018', "'")
      .replaceAll('\u2019', "'")
      .replaceAll('\u201c', '"')
      .replaceAll('\u201d', '"')
      .replaceAll('\u2026', '...')
      .replaceAll('\u00b7', '·')
      .replaceAllMapped(RegExp(r'[^\x00-\xff]'), (_) => '?');

  double _sw(PdfFont font, double size, String text) =>
      font.stringMetrics(_latin1(text)).width * size;

  void _drawCentered(
    PdfGraphics c,
    PdfFont font,
    double size,
    String text,
    double cx,
    double y, {
    required PdfColor color,
  }) {
    text = _latin1(text);
    c
      ..setFillColor(color)
      ..setFont(font, size)
      ..drawString(font, size, text, cx - _sw(font, size, text) / 2, y);
  }

  /// Center [text] horizontally and vertically inside [x,y,w,h] (PDF bottom-left).
  void _drawCenteredInRect(
    PdfGraphics c,
    _Fonts fonts,
    String text,
    double x,
    double y,
    double w,
    double h, {
    double size = 9,
    bool shrinkToFit = true,
    double nudgeUp = 0,
  }) {
    text = _latin1(text).trim();
    if (text.isEmpty || w <= 2 || h <= 2) return;
    final useSize = shrinkToFit ? math.min(size, h) : size;
    final tw = _sw(fonts.bold, useSize, text);
    final tx = x + (w - tw) / 2;
    // Optical vertical center (baseline sits slightly below mid-glyph).
    var ty = y + (h - useSize) / 2 + useSize * 0.08 + nudgeUp;
    if (!shrinkToFit && useSize > h) {
      // Keep oversized numerals inside the band, biased toward the top so they
      // do not collide with captions under the cell (e.g. LBS).
      ty = y + h - useSize * 0.88 + nudgeUp;
    }
    c
      ..setFillColor(black)
      ..setFont(fonts.bold, useSize)
      ..drawString(fonts.bold, useSize, text, tx, ty);
  }

  void _drawValue(
    PdfGraphics c,
    _Fonts fonts,
    String text,
    double x,
    double y,
    double w,
    double h,
    double size, {
    int maxLines = 1,
    bool alignTop = false,
    bool alignCenter = false,
    bool shrinkToFit = false,
    double minSize = 4.5,
  }) {
    text = _latin1(text).trim();
    if (text.isEmpty || w <= 2 || h <= 2) return;

    var useSize = size;
    var lines = _wrapValueLines(fonts, text, w, useSize, maxLines);
    if (shrinkToFit) {
      while (useSize > minSize) {
        lines = _wrapValueLines(fonts, text, w, useSize, maxLines);
        final covered = lines.join(' ').replaceAll(RegExp(r'\s+'), ' ').trim();
        final target = text.replaceAll(RegExp(r'\s+'), ' ').trim();
        final widthOk = lines.every((l) => _sw(fonts.bold, useSize, l) <= w + 0.5);
        final heightOk =
            lines.length * (useSize + 1) - 1 <= h + 0.5 || lines.length <= 1;
        // Prefer full coverage; allow soft hyphenation of unbroken tokens via
        // character wrap when needed.
        if (widthOk && heightOk && (covered == target || covered.length >= target.length * 0.92)) {
          break;
        }
        useSize -= 0.25;
      }
      lines = _wrapValueLines(
        fonts,
        text,
        w,
        useSize,
        maxLines,
        allowCharBreak: true,
      );
    }

    if (lines.isEmpty) return;
    final leading = useSize + 1;

    c
      ..setFillColor(black)
      ..setFont(fonts.bold, useSize);

    final blockH = lines.length * leading - (leading - useSize);

    if (alignTop) {
      var yy = y + h - useSize;
      for (final l in lines) {
        if (yy < y) break;
        final tx = alignCenter ? x + (w - _sw(fonts.bold, useSize, l)) / 2 : x;
        c.drawString(fonts.bold, useSize, l, tx, yy);
        yy -= leading;
      }
      return;
    }

    // Default: vertically middle-align the text block in the cell with a little
    // breathing room from the top/bottom borders.
    var yy = y + (h - blockH) / 2 + (lines.length - 1) * leading;
    if (yy > y + h - useSize) yy = y + h - useSize;
    if (yy < y) yy = y;
    for (final l in lines) {
      final tx = alignCenter ? x + (w - _sw(fonts.bold, useSize, l)) / 2 : x;
      c.drawString(fonts.bold, useSize, l, tx, yy);
      yy -= leading;
    }
  }

  /// Word-wrap (and optional character-break) for value cells.
  List<String> _wrapValueLines(
    _Fonts fonts,
    String text,
    double w,
    double size,
    int maxLines, {
    bool allowCharBreak = false,
  }) {
    final words = text.split(RegExp(r'\s+')).where((e) => e.isNotEmpty).toList();
    final lines = <String>[];
    var line = '';

    void pushLine(String s) {
      if (s.isEmpty) return;
      lines.add(s);
    }

    for (final word in words) {
      if (lines.length >= maxLines) break;
      final trial = line.isEmpty ? word : '$line $word';
      if (_sw(fonts.bold, size, trial) <= w) {
        line = trial;
        continue;
      }
      if (line.isNotEmpty) {
        pushLine(line);
        line = '';
        if (lines.length >= maxLines) break;
      }
      if (_sw(fonts.bold, size, word) <= w) {
        line = word;
        continue;
      }
      if (!allowCharBreak) {
        // Keep the long token; caller may shrink font.
        line = word;
        continue;
      }
      // Character-break an unbroken token that still overflows.
      var chunk = '';
      for (final ch in word.split('')) {
        final next = '$chunk$ch';
        if (chunk.isNotEmpty && _sw(fonts.bold, size, next) > w) {
          pushLine(chunk);
          chunk = ch;
          if (lines.length >= maxLines) {
            chunk = '';
            break;
          }
        } else {
          chunk = next;
        }
      }
      line = chunk;
    }
    if (line.isNotEmpty && lines.length < maxLines) pushLine(line);
    return lines;
  }

  double _measureWrapped(
    _Fonts fonts,
    String text,
    double maxW, {
    required double size,
    required double leading,
  }) {
    final words = text.split(RegExp(r'\s+'));
    var lines = 0;
    var current = <String>[];
    for (final word in words) {
      final trial = [...current, word].join(' ');
      if (_sw(fonts.regular, size, trial) <= maxW) {
        current.add(word);
      } else {
        if (current.isNotEmpty) lines++;
        current = [word];
      }
    }
    if (current.isNotEmpty) lines++;
    if (lines <= 0) return 0;
    return size + (lines - 1) * leading;
  }

  void _wrapText(
    PdfGraphics c,
    _Fonts fonts,
    String text,
    double x,
    double y,
    double maxW, {
    required double size,
    required double leading,
    required PdfColor color,
    double? minY,
    bool bold = false,
  }) {
    final font = bold ? fonts.bold : fonts.regular;
    c
      ..setFillColor(color)
      ..setFont(font, size);
    final words = text.split(RegExp(r'\s+'));
    var curY = y;
    var current = <String>[];
    for (final word in words) {
      final trial = [...current, word].join(' ');
      if (_sw(font, size, trial) <= maxW) {
        current.add(word);
      } else {
        if (current.isNotEmpty) {
          if (minY != null && curY < minY) return;
          c.drawString(font, size, current.join(' '), x, curY);
          curY -= leading;
        }
        current = [word];
      }
    }
    if (current.isNotEmpty) {
      if (minY == null || curY >= minY) {
        c.drawString(font, size, current.join(' '), x, curY);
      }
    }
  }
}

class _Fonts {
  _Fonts({
    required this.regular,
    required this.bold,
  });
  final PdfFont regular;
  final PdfFont bold;
}
