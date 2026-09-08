import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:swift_shipping_label/bol_dimensions.dart';
import 'package:swift_shipping_label/bol_item_type.dart';

/// Lets a test swap [BolDimensionsFields.unitDefaults] after the initial
/// pump, so [State.didUpdateWidget] actually fires (a fresh [pumpWidget]
/// with a new hint would just re-run initState on a new element).
class _Harness extends StatefulWidget {
  const _Harness({
    super.key,
    required this.controller,
    required this.unitDefaults,
  });

  final TextEditingController controller;
  final (String, String, String)? unitDefaults;

  @override
  State<_Harness> createState() => _HarnessState();
}

class _HarnessState extends State<_Harness> {
  late (String, String, String)? _hint = widget.unitDefaults;

  void setHint((String, String, String)? hint) =>
      setState(() => _hint = hint);

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      home: Scaffold(
        body: BolDimensionsFields(
          controller: widget.controller,
          unitDefaults: _hint,
        ),
      ),
    );
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets(
    'existing saved units survive mount even with a conflicting item-type hint',
    (tester) async {
      final controller =
          TextEditingController(text: '48 in × 40 in × 48 in');
      await tester.pumpWidget(
        _Harness(controller: controller, unitDefaults: ('ft', 'ft', 'ft')),
      );
      await tester.pump();

      // Nudge the length field to force a re-format from live widget state,
      // proving the *unit* actually held in state is still 'in', not just
      // that the original string was left alone untouched.
      await tester.enterText(find.byType(TextField).first, '48');
      await tester.pump();

      expect(controller.text, '48 in × 40 in × 48 in');
    },
  );

  testWidgets(
    'a truly empty line still adopts the item-type default units',
    (tester) async {
      final controller = TextEditingController();
      await tester.pumpWidget(
        _Harness(controller: controller, unitDefaults: ('ft', 'ft', 'ft')),
      );
      await tester.pump();

      await tester.enterText(find.byType(TextField).first, '10');
      await tester.pump();

      expect(controller.text, startsWith('10 ft'));
    },
  );

  testWidgets(
    'actively switching item type updates units even with existing dimensions',
    (tester) async {
      final controller = TextEditingController(text: '10 ft × 5 ft × 3 ft');
      final key = GlobalKey<_HarnessState>();
      await tester.pumpWidget(
        _Harness(
          key: key,
          controller: controller,
          unitDefaults: ('ft', 'ft', 'ft'),
        ),
      );
      await tester.pump();

      // Simulate the user changing the Item Type dropdown from Pallet to Box.
      key.currentState!.setHint(('in', 'in', 'in'));
      await tester.pump();

      expect(controller.text, '10 in × 5 in × 3 in');
    },
  );
  test('parses bare 48x40x48 as inches on each axis', () {
    final d = BolDimensionsValue.parse('48x40x48');
    expect(d.length, '48');
    expect(d.width, '40');
    expect(d.height, '48');
    expect(d.lengthUnit, 'in');
    expect(d.widthUnit, 'in');
    expect(d.heightUnit, 'in');
    expect(d.format(), '48 in × 40 in × 48 in');
  });

  test('parses legacy shared unit suffix', () {
    final d = BolDimensionsValue.parse('12 × 8 × 10 cm');
    expect(d.lengthUnit, 'cm');
    expect(d.widthUnit, 'cm');
    expect(d.heightUnit, 'cm');
    expect(d.format(), '12 cm × 8 cm × 10 cm');
  });

  test('parses mixed per-axis units (pipe)', () {
    final d = BolDimensionsValue.parse('6 in × 6 in × 21 ft');
    expect(d.length, '6');
    expect(d.width, '6');
    expect(d.height, '21');
    expect(d.lengthUnit, 'in');
    expect(d.widthUnit, 'in');
    expect(d.heightUnit, 'ft');
    expect(d.format(), '6 in × 6 in × 21 ft');
  });

  test('empty stays empty', () {
    expect(BolDimensionsValue.parse('').isEmpty, isTrue);
    expect(const BolDimensionsValue().format(), '');
  });

  test('item-type unit defaults: Pallet/Crate ft, Box in, Pipe in×in×ft', () {
    expect(BolItemTypes.defaultDimensionUnits('Pallet'), ('ft', 'ft', 'ft'));
    expect(BolItemTypes.defaultDimensionUnits('Crate'), ('ft', 'ft', 'ft'));
    expect(BolItemTypes.defaultDimensionUnits('Box'), ('in', 'in', 'in'));
    expect(BolItemTypes.defaultDimensionUnits('Pipe'), ('in', 'in', 'ft'));
    expect(BolItemTypes.defaultDimensionUnits('Bundle'), ('in', 'in', 'ft'));
    expect(BolItemTypes.defaultDimensionUnits('Other'), isNull);
  });
}
