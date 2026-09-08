import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:swift_shipping_label/logo_perfect_restore.dart';

void main() {
  final empty = Uint8List(0);

  test('criticsLabel reflects which critics actually ran', () {
    final both = LogoPerfectRestoreResult(
      png: empty,
      svg: null,
      verified: true,
      attempts: 1,
      geminiCritiqued: true,
      claudeCritiqued: true,
      claudeConfigured: true,
    );
    expect(both.criticsLabel, 'Gemini + Claude');

    final geminiOnly = LogoPerfectRestoreResult(
      png: empty,
      svg: null,
      verified: true,
      attempts: 1,
      geminiCritiqued: true,
      claudeCritiqued: false,
      claudeConfigured: false,
    );
    expect(geminiOnly.criticsLabel, 'Gemini');

    final claudeMissing = LogoPerfectRestoreResult(
      png: empty,
      svg: null,
      verified: false,
      attempts: 2,
      geminiCritiqued: true,
      claudeCritiqued: false,
      claudeConfigured: true,
    );
    expect(claudeMissing.criticsLabel, 'Gemini (Claude unavailable)');
  });
}
