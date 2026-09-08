import 'package:flutter_test/flutter_test.dart';
import 'package:swift_shipping_label/label_data.dart';

void main() {
  group('presetMatchesCustomer', () {
    CustomerPreset preset(String name, [String? customerField]) =>
        CustomerPreset(
          name: name,
          fields: {
            LabelFields.customer: ?customerField,
          },
          logoFileNames: const [],
        );

    test('matches Arc prefixes loosely', () {
      final p = preset('Arc Resources Inc', 'Arc Resources Inc');
      expect(presetMatchesCustomer(p, 'Arc'), isTrue);
      expect(presetMatchesCustomer(p, 'Arc Resources'), isTrue);
      expect(presetMatchesCustomer(p, 'arc resources inc'), isTrue);
      expect(presetMatchesCustomer(p, 'Shell'), isFalse);
    });

    test('matches hyphenated vs concatenated names', () {
      final p = preset('Rite-Way Machining', 'Rite-Way Machining Inc.');
      expect(presetMatchesCustomer(p, 'Riteway'), isTrue);
      expect(presetMatchesCustomer(p, 'Rite-Way'), isTrue);
      expect(presetMatchesCustomer(p, 'Rite Way'), isTrue);
      expect(presetMatchesCustomer(p, 'Rite-Way Machining'), isTrue);
    });
  });
}
