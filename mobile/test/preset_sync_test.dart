import 'package:flutter_test/flutter_test.dart';
import 'package:swift_shipping_label/app_storage.dart';
import 'package:swift_shipping_label/label_data.dart';
import 'package:swift_shipping_label/preset_sync.dart';

/// Regression coverage for the "K" BOL preset resurrection bug: a preset
/// deleted on one device kept reappearing because customer_presets had no
/// tombstone table, so any stale local cache (this device's own leftover
/// copy, or another device/install that never heard about the delete)
/// would see the name missing from remote and re-upload it on its next
/// syncOnLaunch(). These tests exercise the pure tombstone-filtering helpers
/// PresetSync.syncOnLaunch() relies on, without hitting the network.
void main() {
  CustomerPreset preset(String name, {LabelKind kind = LabelKind.bol}) =>
      CustomerPreset(name: name, kind: kind, fields: const {});

  group('PresetSync tombstones', () {
    test('a tombstoned preset is dropped from the local cache', () {
      final local = <String, CustomerPreset>{
        AppStorage.presetStorageKey(LabelKind.bol, 'K'): preset('K'),
        AppStorage.presetStorageKey(LabelKind.bol, 'ARJAE'): preset('ARJAE'),
      };
      final tombstones = <String, DateTime>{
        AppStorage.presetStorageKey(LabelKind.bol, 'K'): DateTime.utc(2026),
      };

      final next = PresetSync.withoutTombstoned(local, tombstones);

      expect(
        next.containsKey(AppStorage.presetStorageKey(LabelKind.bol, 'K')),
        isFalse,
        reason: 'deleted preset must not survive in the local cache — a '
            'surviving entry is exactly what re-uploads on the next sync',
      );
      expect(
        next.containsKey(AppStorage.presetStorageKey(LabelKind.bol, 'ARJAE')),
        isTrue,
        reason: 'un-deleted presets must be left alone',
      );
    });

    test('an untouched preset has no tombstone and stays pushable', () {
      expect(
        PresetSync.isTombstoned(LabelKind.bol, 'ARJAE', const {}),
        isFalse,
      );
    });

    test('a deleted preset is tombstoned and must not be re-pushed', () {
      final tombstones = <String, DateTime>{
        AppStorage.presetStorageKey(LabelKind.bol, 'K'): DateTime.utc(2026),
      };
      expect(PresetSync.isTombstoned(LabelKind.bol, 'K', tombstones), isTrue);
      // Same display name under a different kind is a different preset and
      // must not be caught by the same-name tombstone.
      expect(
        PresetSync.isTombstoned(LabelKind.shipping, 'K', tombstones),
        isFalse,
      );
    });

    test('tombstone presence is exact-key, not substring/case matching', () {
      final tombstones = <String, DateTime>{
        AppStorage.presetStorageKey(LabelKind.bol, 'K'): DateTime.utc(2026),
      };
      expect(PresetSync.isTombstoned(LabelKind.bol, 'k', tombstones), isFalse);
      expect(
        PresetSync.isTombstoned(LabelKind.bol, 'KK', tombstones),
        isFalse,
      );
    });
  });
}
