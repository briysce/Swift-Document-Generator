import 'dart:typed_data';

/// Which print template the generator is targeting.
enum LabelKind { shipping, receiving, bol, bulk }

/// Max customer logos per label (primary + care-of / C/O).
const int maxCustomerLogos = 2;

/// Bill of Lading field keys (flat print PDF).
class BolFields {
  static const documentNumber = 'document_number';
  static const documentDate = 'document_date';
  static const bookingRef = 'booking_ref';
  static const probillNumber = 'probill_number';
  static const consigneeName = 'consignee_name';
  static const consigneeAddress = 'consignee_address';
  static const consigneeContactName = 'consignee_contact_name';
  static const consigneeContactNumber = 'consignee_contact_number';
  static const thirdPartyBilling = 'third_party_billing';
  static const freightCharges = 'freight_charges';

  /// Stored values for [freightCharges] (PDF radio group).
  static const freightPrepaid = 'prepaid';
  static const freightCollect = 'collect';
  static const freightThirdParty = 'third_party';
  static const freightCustomerPickup = 'customer_pickup';

  static const freightChargeOptions = <(String value, String label)>[
    (freightPrepaid, 'Prepaid'),
    (freightCollect, 'Collect'),
    (freightThirdParty, 'Third Party'),
    (freightCustomerPickup, 'Customer Pick-Up'),
  ];

  /// Compact labels for the BOL freight radio row (four options).
  static const freightChargePdfLabels = <String, String>{
    freightPrepaid: 'Prepaid',
    freightCollect: 'Collect',
    freightThirdParty: '3rd Party',
    freightCustomerPickup: 'Customer Pick-Up',
  };

  /// Human-readable freight term for Shipping/BOL PDFs.
  static String freightChargesDisplay(String raw) {
    final v = raw.trim();
    for (final o in freightChargeOptions) {
      if (o.$1 == v) return o.$2;
    }
    return v;
  }
  static const packingList = 'packing_list';
  static const orderNum = 'order_num';
  static const totalPieces = 'total_pieces';
  static const totalWeight = 'total_weight';
  static const shipperCertName = 'shipper_cert_name';
  static const shipperCertSign = 'shipper_cert_sign';
  static const shipperCertDate = 'shipper_cert_date';
  static const driverCompany = 'driver_company';
  static const driverPrint = 'driver_print';
  static const driverSign = 'driver_sign';
  static const driverDate = 'driver_date';
  static const vehicleId = 'vehicle_id';
  /// Kept for older fills; no longer drawn on the BOL.
  static const arrivalTime = 'arrival_time';
  static const consigneeSign = 'consignee_sign';
  static const consigneePrint = 'consignee_print';
  static const consigneeDate = 'consignee_date';

  static String lineKey(int row, String suffix) => 'line_${row}_$suffix';

  /// BOL-only keys (PO / Project reuse [LabelFields]).
  static final formDefs = <(String key, String label, bool multiline)>[
    (documentNumber, 'Document Number', false),
    (documentDate, 'Date', false),
    (bookingRef, 'Booking Ref', false),
    (probillNumber, 'Probill #', false),
    (consigneeName, 'Ship To Name', false),
    (consigneeAddress, 'Delivery Address', true),
    (consigneeContactName, 'Contact Name', false),
    (consigneeContactNumber, 'Contact Number', false),
    (thirdPartyBilling, '3rd Party Billing', true),
    (packingList, 'Packing List #', false),
    (shipperCertName, 'Shipper Cert Name', false),
    (shipperCertDate, 'Shipper Cert Date', false),
    (driverCompany, 'Carrier Company', false),
    (driverPrint, 'Driver Name', false),
    (vehicleId, 'Vehicle ID', false),
    (driverDate, 'Departure Date', false),
    (consigneePrint, 'Consignee Print Name', false),
    (consigneeDate, 'Consignee Date', false),
    for (var i = 1; i <= 10; i++) ...[
      (lineKey(i, 'pieces'), 'Line $i Qty', false),
      (lineKey(i, 'item_type'), 'Line $i Item Type', false),
      (lineKey(i, 'dimensions'), 'Line $i Dimensions', false),
      (lineKey(i, 'description'), 'Line $i Description', false),
      (lineKey(i, 'weight'), 'Line $i Weight', false),
    ],
  ];
}

/// Field keys shared by the form UI and PDF generators.
class LabelFields {
  static const customer = 'customer';
  static const poNum = 'po_num';
  static const project = 'project';
  static const specialInstructions = 'special_instructions';
  static const shipTo = 'ship_to';
  static const location = 'location';
  static const attn = 'attn';
  static const carrier = 'carrier';
  static const packingSlip = 'packing_slip';
  static const salesOrder = 'sales_order';
  /// JSON map of extra field values → sales order (see [SoFieldMap]).
  static const soFieldMap = 'so_field_map';
  static const swiftContact = 'swift_contact';
  static const palletNum = 'pallet_num';
  static const palletOf = 'pallet_of';
  static const boxNum = 'box_num';
  static const boxOf = 'box_of';
  static const pm = 'pm';
  static const dateReceived = 'date_received';
  static const receivedBy = 'received_by';

  static final formDefs = <(String key, String label, bool multiline)>[
    (customer, 'Customer', false),
    (salesOrder, 'Swift Sales Order No.', true),
    (poNum, 'PO No.', true),
    (project, 'Project', true),
    (specialInstructions, 'Special Instructions', true),
    (shipTo, 'Ship To Name', false),
    (location, 'Delivery Address', true),
    (attn, 'Attn', false),
    (carrier, 'Carrier', false),
    (packingSlip, 'Swift Packing Slip No.', false),
    (swiftContact, 'Swift Contact', false),
    (palletNum, 'Pallet / Crate #', false),
    (palletOf, 'Pallet / Crate of', false),
    (boxNum, 'Box #', false),
    (boxOf, 'Box of', false),
    (pm, 'PM', false),
    (dateReceived, 'Date Received', false),
    (receivedBy, 'Received By', false),
    ...BolFields.formDefs,
  ];

  /// Legacy shipping preset keys (prefer [presetKeysFor]).
  static const presetKeys = <String>[
    customer,
    shipTo,
    location,
    attn,
    carrier,
    swiftContact,
    specialInstructions,
    pm,
  ];
}

/// Date fields that use a calendar picker in the form UI.
const appDateFieldKeys = <String>{
  LabelFields.dateReceived,
  BolFields.documentDate,
  BolFields.shipperCertDate,
  BolFields.driverDate,
  BolFields.consigneeDate,
};

/// Display/parse dates as "Aug 1, 2026" (matches BOL today stamp).
class AppDates {
  AppDates._();

  static const _months = [
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec',
  ];

  static String format(DateTime d) =>
      '${_months[d.month - 1]} ${d.day}, ${d.year}';

  static DateTime? parse(String raw) {
    final s = raw.trim();
    if (s.isEmpty) return null;
    final direct = DateTime.tryParse(s);
    if (direct != null) return direct;
    final m = RegExp(
      r'^([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$',
    ).firstMatch(s);
    if (m == null) return null;
    final mon = m.group(1)!.toLowerCase();
    final month = _months.indexWhere((name) => name.toLowerCase() == mon) + 1;
    if (month < 1) return null;
    final day = int.tryParse(m.group(2)!);
    final year = int.tryParse(m.group(3)!);
    if (day == null || year == null) return null;
    return DateTime(year, month, day);
  }
}

/// Form field keys persisted on a customer preset for [kind].
List<String> presetKeysFor(LabelKind kind) {
  switch (kind) {
    case LabelKind.shipping:
      return const [
        LabelFields.customer,
        LabelFields.shipTo,
        LabelFields.location,
        LabelFields.attn,
        LabelFields.carrier,
        BolFields.freightCharges,
        BolFields.thirdPartyBilling,
        LabelFields.swiftContact,
        LabelFields.specialInstructions,
      ];
    case LabelKind.receiving:
      return const [
        LabelFields.customer,
        LabelFields.project,
        LabelFields.poNum,
        LabelFields.specialInstructions,
        LabelFields.swiftContact,
      ];
    case LabelKind.bol:
      return const [
        LabelFields.customer,
        LabelFields.specialInstructions,
        LabelFields.salesOrder,
        LabelFields.poNum,
        LabelFields.project,
        BolFields.consigneeName,
        BolFields.consigneeAddress,
        BolFields.consigneeContactName,
        BolFields.consigneeContactNumber,
        BolFields.freightCharges,
        BolFields.thirdPartyBilling,
        BolFields.packingList,
        BolFields.driverCompany,
        BolFields.shipperCertName,
      ];
    case LabelKind.bulk:
      return const [];
  }
}

/// Preset keys applied for “Core only” template mode (logos applied separately).
List<String> corePresetKeysFor(LabelKind kind) {
  switch (kind) {
    case LabelKind.shipping:
      return const [
        LabelFields.shipTo,
        LabelFields.location,
        LabelFields.carrier,
        BolFields.freightCharges,
        BolFields.thirdPartyBilling,
      ];
    case LabelKind.receiving:
      // Receiving has no ship-to / carrier block — logos only for core.
      return const [];
    case LabelKind.bol:
      return const [
        BolFields.consigneeName,
        BolFields.consigneeAddress,
        BolFields.consigneeContactName,
        BolFields.consigneeContactNumber,
        BolFields.driverCompany,
        BolFields.freightCharges,
        BolFields.thirdPartyBilling,
      ];
    case LabelKind.bulk:
      return const [];
  }
}

/// Normalize customer names for loose template matching.
///
/// Strips punctuation/hyphens/spaces so `Rite-Way`, `Rite Way`, and `Riteway`
/// compare equal, and lowercases for case-insensitive matching.
String normalizeCustomerKey(String value) {
  final lower = value.trim().toLowerCase();
  if (lower.isEmpty) return '';
  return lower.replaceAll(RegExp(r'[^a-z0-9]+'), '');
}

/// True when [customer] loosely matches the preset display name or customer field.
///
/// Accepts prefixes / contained tokens so typing `Arc` or `Arc Resources`
/// still matches a saved `Arc Resources Inc` template (and hyphen variants).
bool presetMatchesCustomer(CustomerPreset preset, String customer) {
  final needle = normalizeCustomerKey(customer);
  if (needle.isEmpty) return false;
  final name = normalizeCustomerKey(preset.name);
  final field =
      normalizeCustomerKey(preset.fields[LabelFields.customer] ?? '');
  return _looseCustomerKeyMatch(needle, name) ||
      _looseCustomerKeyMatch(needle, field);
}

bool _looseCustomerKeyMatch(String a, String b) {
  if (a.isEmpty || b.isEmpty) return false;
  if (a == b) return true;
  // Require a meaningful stem so 1–2 letter typos do not match everything.
  if (a.length < 3 && b.length < 3) return false;
  final short = a.length <= b.length ? a : b;
  final long = a.length <= b.length ? b : a;
  if (short.length < 3) return false;
  return long.startsWith(short);
}

class ShippingLabelData {
  ShippingLabelData([Map<String, String>? values])
      : values = {
          for (final def in LabelFields.formDefs) def.$1: '',
          ...?values,
        };

  final Map<String, String> values;

  String get(String key) => (values[key] ?? '').trim();

  void set(String key, String value) => values[key] = value;

  ShippingLabelData copy() => ShippingLabelData(Map.of(values));

  static final sample = ShippingLabelData({
    LabelFields.customer: 'PACIFIC CANBRIAM',
    LabelFields.shipTo: 'STRAIT PROJECTS',
    LabelFields.poNum:
        'PCE-112124-03690 / RELEASE 2 / FORT ST JOHN DELIVERY WINDOW / RUSH / CONFIRM DOCK',
    LabelFields.location: '12341 271 RD, FORT ST. JOHN, BC',
    LabelFields.project:
        'B35 PIPE AND FITTINGS - NORTH PAD STAGING AND HOOKUP MATERIALS FOR WELLSITE PACKAGE',
    LabelFields.carrier: 'WILLYS',
    BolFields.freightCharges: BolFields.freightPrepaid,
    BolFields.thirdPartyBilling: 'Acct 44521',
    LabelFields.specialInstructions: 'Call before delivery. Staging bay 3.',
    LabelFields.attn: 'RICK SHUMAN / JEREMY PLATZ',
    LabelFields.palletNum: '1',
    LabelFields.palletOf: '2',
    LabelFields.boxNum: '',
    LabelFields.boxOf: '',
    LabelFields.packingSlip: '1224618',
    LabelFields.salesOrder: 'SO-88421',
    LabelFields.swiftContact: 'J. SMITH',
  });

  static final receivingSample = ShippingLabelData({
    LabelFields.customer: 'CONOCOPHILLIPS CANADA (BRC) PARTNERSHIP',
    LabelFields.project: 'Gateway Pipelines & CBR Pad 107 Lateral FEL3',
    LabelFields.poNum: '278-07-31 - 0009',
    LabelFields.salesOrder: '1380380',
    LabelFields.swiftContact: 'CHRIS ACORN',
    LabelFields.dateReceived: 'May 1st, 2026',
    LabelFields.receivedBy: 'Keith Blackman',
    LabelFields.specialInstructions:
        'Hold on dock until ship confirm. Do not break skid.',
  });

  static final bolSample = ShippingLabelData({
    LabelFields.customer: 'PACIFIC CANBRIAM',
    LabelFields.specialInstructions: 'Call before delivery. Staging bay 3.',
    LabelFields.poNum: 'PCE-112124-03690',
    LabelFields.project: 'B35 PIPE AND FITTINGS',
    LabelFields.salesOrder: 'SO-88421',
    BolFields.documentNumber: 'SW-0024',
    BolFields.documentDate: 'Aug 1, 2026',
    BolFields.bookingRef: 'BK-4412',
    BolFields.consigneeName: 'STRAIT PROJECTS',
    BolFields.consigneeAddress: '12341 271 RD, FORT ST. JOHN, BC',
    BolFields.consigneeContactName: 'RICK SHUMAN',
    BolFields.consigneeContactNumber: '250-555-0199',
    BolFields.freightCharges: 'prepaid',
    BolFields.packingList: '1224618',
    BolFields.lineKey(1, 'pieces'): '2',
    BolFields.lineKey(1, 'item_type'): 'Pallet',
    BolFields.lineKey(1, 'dimensions'): '48 in × 40 in × 48 in',
    BolFields.lineKey(1, 'description'): 'Pipe fittings - north pad staging',
    BolFields.lineKey(1, 'weight'): '1800',
    BolFields.lineKey(2, 'pieces'): '1',
    BolFields.lineKey(2, 'item_type'): 'Box',
    BolFields.lineKey(2, 'description'): 'Gasket kit',
    BolFields.lineKey(2, 'weight'): '40',
    BolFields.shipperCertName: 'J. SMITH',
    BolFields.shipperCertDate: 'Aug 1, 2026',
  });
}

/// Saved shipper signature (local PNG + Supabase sync).
class SavedSignature {
  SavedSignature({
    required this.id,
    required this.name,
    required this.fileName,
    required this.updatedAt,
  });

  final String id;
  final String name;
  final String fileName;
  final DateTime updatedAt;

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'file_name': fileName,
        'updated_at': updatedAt.toUtc().toIso8601String(),
      };

  factory SavedSignature.fromJson(Map<String, dynamic> json) {
    DateTime updated = DateTime.fromMillisecondsSinceEpoch(0);
    try {
      updated = DateTime.parse('${json['updated_at']}').toUtc();
    } catch (_) {}
    return SavedSignature(
      id: '${json['id'] ?? ''}'.trim(),
      name: '${json['name'] ?? ''}'.trim(),
      fileName: '${json['file_name'] ?? ''}'.trim(),
      updatedAt: updated,
    );
  }
}

class CustomerPreset {
  CustomerPreset({
    required this.name,
    required this.fields,
    this.kind = LabelKind.shipping,
    this.logoFileNames = const [],
    DateTime? updatedAt,
  }) : updatedAt = updatedAt ?? DateTime.now().toUtc();

  final String name;
  final Map<String, String> fields;
  final LabelKind kind;
  /// Up to [maxCustomerLogos] filenames under app logo storage.
  final List<String> logoFileNames;

  /// Last local edit, used to decide sync direction per-preset (not the
  /// whole presets file's mtime, which would treat every preset as "newer"
  /// whenever any one of them is saved).
  final DateTime updatedAt;

  /// First logo (legacy helpers / single-logo callers).
  String get logoFileName =>
      logoFileNames.isEmpty ? '' : logoFileNames.first;

  Map<String, dynamic> toJson() => {
        'kind': kind.name,
        ...fields,
        'logos': logoFileNames,
        // Keep legacy key for older app versions
        if (logoFileNames.isNotEmpty) 'logo': logoFileNames.first,
        'updated_at': updatedAt.toIso8601String(),
      };

  factory CustomerPreset.fromJson(
    String name,
    Map<String, dynamic> json, {
    LabelKind defaultKind = LabelKind.shipping,
  }) {
    var kind = defaultKind;
    final rawKind = json['kind'];
    if (rawKind is String) {
      kind = LabelKind.values.firstWhere(
        (k) => k.name == rawKind,
        orElse: () => defaultKind,
      );
    }
    final fields = <String, String>{};
    for (final key in presetKeysFor(kind)) {
      final v = json[key];
      if (v != null) fields[key] = '$v';
    }
    // Receiving used a separate `pm` key; form+PDF now use swift_contact as PM.
    if (kind == LabelKind.receiving &&
        (fields[LabelFields.swiftContact] ?? '').trim().isEmpty) {
      final legacyPm = json[LabelFields.pm];
      if (legacyPm != null && '$legacyPm'.trim().isNotEmpty) {
        fields[LabelFields.swiftContact] = '$legacyPm'.trim();
      }
    }
    if (kind == LabelKind.bol &&
        (fields[LabelFields.salesOrder] ?? '').trim().isEmpty) {
      final legacy = json[BolFields.orderNum];
      if (legacy != null && '$legacy'.trim().isNotEmpty) {
        fields[LabelFields.salesOrder] = '$legacy';
      }
    }
    final logos = <String>[];
    final rawList = json['logos'];
    if (rawList is List) {
      for (final item in rawList) {
        final s = '$item'.trim();
        if (s.isNotEmpty) logos.add(s);
      }
    }
    if (logos.isEmpty) {
      final legacy = '${json['logo'] ?? ''}'.trim();
      if (legacy.isNotEmpty) logos.add(legacy);
    }
    // Missing on presets saved before per-entity timestamps existed — treat
    // as "old" (not "now") so a fresh install doesn't look newer than every
    // remote preset and mass-push on first sync.
    final updatedAt =
        DateTime.tryParse('${json['updated_at'] ?? ''}')?.toUtc() ??
            DateTime.fromMillisecondsSinceEpoch(0, isUtc: true);
    return CustomerPreset(
      name: name,
      fields: fields,
      kind: kind,
      logoFileNames: logos.take(maxCustomerLogos).toList(),
      updatedAt: updatedAt,
    );
  }
}

/// Counts entered before generating a multi-page shipping PDF.
class PieceCountPlan {
  const PieceCountPlan({
    this.palletCrates = 0,
    this.boxes = 0,
    this.isUndetermined = false,
  });

  final int palletCrates;
  final int boxes;

  /// User doesn't know the count or container type yet — print a single
  /// label with the Pallet/Crate and Box "# of #" fields left blank to fill
  /// in by hand, then run off as many physical copies as needed.
  final bool isUndetermined;

  int get totalPages => palletCrates + boxes;

  bool get isEmpty => !isUndetermined && totalPages <= 0;
}

class LabelFonts {
  LabelFonts({
    required this.oswald,
    required this.oswaldMedium,
    required this.oswaldSemiBold,
    required this.oswaldBold,
    required this.calibri,
    required this.calibriBold,
  });

  final ByteData oswald;
  final ByteData oswaldMedium;
  final ByteData oswaldSemiBold;
  final ByteData oswaldBold;
  final ByteData calibri;
  final ByteData calibriBold;
}
