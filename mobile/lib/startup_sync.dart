import 'dart:async';

import 'package:flutter/foundation.dart';

import 'address_book_sync.dart';
import 'app_storage.dart';
import 'carrier_sync.dart';
import 'contact_sync.dart';
import 'document_history_sync.dart';
import 'preset_sync.dart';
import 'signature_sync.dart';

/// One shared "Supabase data is ready" signal for app startup.
///
/// Kicks off every launch-time Supabase-backed load exactly once — customer
/// presets, saved signatures, shared contacts, the delivery-address book,
/// shared carrier names, and expired generated-document history purge — and
/// exposes:
///  * [ready] — resolves once every load above has settled (success or
///    failure), capped by [_readyTimeout] so a slow/offline network can never
///    hang the caller forever. Individual errors are swallowed here; callers
///    that want to surface per-sync errors to the user should await the
///    dedicated `*Future` getters instead (same in-flight futures, no
///    duplicate network calls).
///  * [progress] — a 0..1 [ValueListenable] that ticks up as each load
///    settles, so a splash screen can show *real* progress instead of a fake
///    timer.
///
/// Both the Windows splash screen and the Android/Wear native launch-screen
/// hold (see `main.dart`) await the same [ready] future, and [HomeScreen]
/// awaits the same underlying per-sync futures — so Supabase is only ever
/// hit once per launch regardless of how many places gate on readiness.
class StartupSync {
  StartupSync(AppStorage storage)
      : presetSync = PresetSync(storage),
        signatureSync = SignatureSync(storage),
        contactSync = ContactSync(storage),
        addressBookSync = AddressBookSync(),
        carrierSync = CarrierSync(),
        documentHistorySync = DocumentHistorySync(storage) {
    presetFuture = presetSync.syncOnLaunch();
    signatureFuture = signatureSync.syncOnLaunch();
    contactFuture = contactSync.syncOnLaunch();
    addressBookFuture = addressBookSync.syncOnLaunch();
    carrierFuture = carrierSync.fetchNames();
    historyPurgeFuture = documentHistorySync.purgeExpired();
    _wireProgress();
  }

  /// Test/preview-only constructor — builds the same real sync objects (so
  /// their non-launch methods still work) but never fires the real Supabase
  /// calls. Callers may supply their own futures (e.g. a [Completer] they
  /// control) or leave them as already-resolved defaults. Used by widget
  /// tests so they never depend on network access or the real 25–45s HTTP
  /// timeouts inside each sync class.
  @visibleForTesting
  StartupSync.forTesting(
    AppStorage storage, {
    Future<void>? presetFuture,
    Future<void>? signatureFuture,
    Future<void>? contactFuture,
    Future<void>? addressBookFuture,
    Future<List<String>>? carrierFuture,
    Future<void>? historyPurgeFuture,
  })  : presetSync = PresetSync(storage),
        signatureSync = SignatureSync(storage),
        contactSync = ContactSync(storage),
        addressBookSync = AddressBookSync(),
        carrierSync = CarrierSync(),
        documentHistorySync = DocumentHistorySync(storage) {
    this.presetFuture = presetFuture ?? Future<void>.value();
    this.signatureFuture = signatureFuture ?? Future<void>.value();
    this.contactFuture = contactFuture ?? Future<void>.value();
    this.addressBookFuture = addressBookFuture ?? Future<void>.value();
    this.carrierFuture = carrierFuture ?? Future<List<String>>.value(const []);
    this.historyPurgeFuture = historyPurgeFuture ?? Future<void>.value();
    _wireProgress();
  }

  void _wireProgress() {
    for (final f in <Future<Object?>>[
      presetFuture,
      signatureFuture,
      contactFuture,
      addressBookFuture,
      carrierFuture,
      historyPurgeFuture,
    ]) {
      f.then((_) => _tick(), onError: (_) => _tick());
    }
  }

  final PresetSync presetSync;
  final SignatureSync signatureSync;
  final ContactSync contactSync;
  final AddressBookSync addressBookSync;
  final CarrierSync carrierSync;
  final DocumentHistorySync documentHistorySync;

  late final Future<void> presetFuture;
  late final Future<void> signatureFuture;
  late final Future<void> contactFuture;
  late final Future<void> addressBookFuture;
  late final Future<List<String>> carrierFuture;
  late final Future<void> historyPurgeFuture;

  static const _total = 6;
  static const _readyTimeout = Duration(seconds: 9);

  final ValueNotifier<double> progress = ValueNotifier<double>(0.0);
  int _done = 0;

  void _tick() {
    _done += 1;
    progress.value = (_done / _total).clamp(0.0, 1.0);
  }

  Future<void>? _ready;

  /// Resolves once every startup load has settled or [_readyTimeout] elapses
  /// — whichever comes first. Never throws.
  Future<void> get ready => _ready ??= _awaitReady();

  Future<void> _awaitReady() async {
    final settled = <Future<void>>[
      presetFuture.then((_) {}, onError: (_) {}),
      signatureFuture.then((_) {}, onError: (_) {}),
      contactFuture.then((_) {}, onError: (_) {}),
      addressBookFuture.then((_) {}, onError: (_) {}),
      carrierFuture.then((_) {}, onError: (_) {}),
      historyPurgeFuture.then((_) {}, onError: (_) {}),
    ];
    try {
      await Future.wait(settled).timeout(_readyTimeout);
    } on TimeoutException {
      // Offline / slow network — proceed with whatever local cache already
      // loaded synchronously in AppStorage.open(). Individual syncs keep
      // running in the background and still update the UI when they land.
    }
    // Make sure the progress bar always reaches 100% even on timeout.
    _done = _total;
    progress.value = 1.0;
  }
}
