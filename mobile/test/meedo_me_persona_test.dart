import 'package:flutter_test/flutter_test.dart';
import 'package:swift_shipping_label/meedo_me_persona.dart';

void main() {
  test('persona sets a terse voice', () {
    final p = meedoMePersona.toLowerCase();
    expect(p, contains('be short'));
    expect(p, contains('meedo-me'));
  });

  test('accuracy rules outrank the voice', () {
    final p = meedoMePersona.toLowerCase();
    // A persona must never license invented shipping data.
    expect(p, contains('never invent'));
    expect(p, contains('not sure'));
    // Brevity must not become an excuse to withhold detail that was asked for.
    expect(p, contains('if asked for detail'));
  });

  test('never impersonates a real person', () {
    final p = meedoMePersona.toLowerCase();
    expect(p, contains('never claim to be a real person'));
    // The friend the tone is named for is not named in the shipped prompt.
    expect(p, isNot(contains('attia')));
  });

  test('withPersona prefixes the task prompt without losing it', () {
    const task = 'Extract the ship-to address.';
    final out = withPersona(task);
    expect(out, startsWith(meedoMePersona));
    expect(out, endsWith(task));
  });
}
