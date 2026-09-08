/// BOL line item type dropdown values (stored singular) and PDF pluralization.
class BolItemTypes {
  BolItemTypes._();

  static const options = [
    'Pallet',
    'Crate',
    'Box',
    'Pipe',
    'Bundle',
    'Other',
  ];

  /// Product-total row labels on the BOL PDF (always plural except Other).
  static const productTotalLabels = [
    'Pallets',
    'Crates',
    'Boxes',
    'Pipes',
    'Bundles',
    'Other',
  ];

  static String pluralize(String singular) {
    switch (singular) {
      case 'Pallet':
        return 'Pallets';
      case 'Crate':
        return 'Crates';
      case 'Box':
        return 'Boxes';
      case 'Pipe':
        return 'Pipes';
      case 'Bundle':
        return 'Bundles';
      default:
        return singular;
    }
  }

  /// Normalize legacy plural or mixed-case values to a dropdown option.
  static String normalizeStored(String raw) {
    final s = raw.trim();
    if (s.isEmpty) return '';
    final lower = s.toLowerCase();
    for (final opt in options) {
      if (lower == opt.toLowerCase() || lower == pluralize(opt).toLowerCase()) {
        return opt;
      }
    }
    return s;
  }

  /// Text drawn on the BOL line row — plural when qty > 1.
  static String displayForm(String stored, num qty) {
    final singular = normalizeStored(stored);
    if (singular.isEmpty) return '';
    if (qty <= 1) return singular;
    return pluralize(singular);
  }

  /// Maps a stored value to the product-total category label.
  static String totalCategory(String stored) {
    final singular = normalizeStored(stored);
    if (singular.isEmpty) return '';
    return pluralize(singular);
  }

  /// (length, width, height) unit defaults for the Dimensions row, keyed by
  /// container type — Pallet/Crate ship in feet, Box in inches, and
  /// Pipe/Bundle use inches for the cross-section with feet for the run
  /// length. Null for Other/unset: leave whatever units are already there
  /// alone.
  static (String, String, String)? defaultDimensionUnits(String stored) {
    switch (normalizeStored(stored)) {
      case 'Pallet':
      case 'Crate':
        return ('ft', 'ft', 'ft');
      case 'Box':
        return ('in', 'in', 'in');
      case 'Pipe':
      case 'Bundle':
        return ('in', 'in', 'ft');
      default:
        return null;
    }
  }
}
