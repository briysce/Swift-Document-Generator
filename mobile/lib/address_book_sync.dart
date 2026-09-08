import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import 'address_match.dart';
import 'address_osm_enrich.dart';
import 'app_config.dart';
import 'gemini_client.dart';
import 'job_pdf_ai.dart';
import 'osm_nominatim_client.dart';

/// One Delivery Address book entry shared by Shipping + BOL.
class DeliveryAddressEntry {
  const DeliveryAddressEntry({
    required this.addressKey,
    required this.shipToName,
    required this.address,
    required this.carrier,
    required this.accountNumbers,
    required this.lastUsedAt,
  });

  final String addressKey;
  final String shipToName;
  final String address;
  final String carrier;
  final String accountNumbers;
  final DateTime lastUsedAt;

  DeliveryAddressEntry copyWith({
    String? addressKey,
    String? shipToName,
    String? address,
    String? carrier,
    String? accountNumbers,
    DateTime? lastUsedAt,
  }) {
    return DeliveryAddressEntry(
      addressKey: addressKey ?? this.addressKey,
      shipToName: shipToName ?? this.shipToName,
      address: address ?? this.address,
      carrier: carrier ?? this.carrier,
      accountNumbers: accountNumbers ?? this.accountNumbers,
      lastUsedAt: lastUsedAt ?? this.lastUsedAt,
    );
  }

  Map<String, dynamic> toJson() => {
        'address_key': addressKey,
        'ship_to_name': shipToName,
        'address': address,
        'carrier': carrier,
        'account_numbers': accountNumbers,
        'last_used_at': lastUsedAt.toUtc().toIso8601String(),
      };
}

/// Syncs Delivery Address book across Windows & Android (not contact memory).
class AddressBookSync {
  AddressBookSync({
    OsmNominatimClient? osm,
    this._osmMinInterval = const Duration(milliseconds: 1100),
  }) : _osm = osm ?? OsmNominatimClient();

  final OsmNominatimClient _osm;
  final Duration _osmMinInterval;
  Future<void>? _osmPass;
  DateTime? _lastOsmAt;

  static const _timeout = Duration(seconds: 25);
  static const maxEntries = 80;
  /// Fetch more than [maxEntries] so collapse can see every duplicate, not just
  /// the newest 80 truncated rows.
  static const fetchLimit = 2000;

  List<DeliveryAddressEntry> entries = const [];

  Map<String, String> get _headers => {
        'apikey': AppConfig.supabaseAnonKey,
        'Authorization': 'Bearer ${AppConfig.supabaseAnonKey}',
        'Content-Type': 'application/json',
      };

  /// Canonical street fingerprint (case, hyphens, St/Street, 8th → 8).
  static String addressKey(String address) => AddressMatch.addressKey(address);

  Future<void> syncOnLaunch() async {
    entries = await fetchAll();
    unawaited(ensureOsmEnrich());
  }

  /// One-shot OSM fill + courier-aware collapse. Launch and the address-book
  /// dialog start this in the background — do not await before showing UI.
  Future<void> ensureOsmEnrich() {
    return _osmPass ??= _runOsmEnrichPass();
  }

  Future<void> _runOsmEnrichPass() async {
    try {
      final raw = entries.isNotEmpty ? List<DeliveryAddressEntry>.from(entries) : await _fetchRaw();
      final filled = <DeliveryAddressEntry>[];
      for (final e in raw) {
        if (!AddressOsmEnrich.looksIncomplete(e.address)) {
          filled.add(e);
          continue;
        }
        await _paceOsm();
        List<NominatimHit> hits = const [];
        try {
          hits = await _osm.searchAddress(e.address, shipTo: e.shipToName);
        } catch (_) {}
        final next = AddressOsmEnrich.fillFromHits(e.address, hits);
        filled.add(next == e.address ? e : e.copyWith(address: next));
      }
      var collapsed = collapseDuplicates(filled);
      if (GeminiClient.isConfigured) {
        try {
          collapsed = await _aiMerge(collapsed);
        } catch (_) {}
      }
      await _persistCollapsed(raw, collapsed);
      try {
        collapsed = collapseDuplicates(await _fetchRaw());
      } catch (_) {}
      entries = _capAndSort(collapsed);
    } catch (_) {}
  }

  Future<void> _paceOsm() async {
    final last = _lastOsmAt;
    if (last != null) {
      final wait = _osmMinInterval - DateTime.now().difference(last);
      if (wait > Duration.zero) {
        await Future<void>.delayed(wait);
      }
    }
    _lastOsmAt = DateTime.now();
  }

  Future<List<DeliveryAddressEntry>> fetchAll() async {
    final raw = await _fetchRaw();
    var collapsed = collapseDuplicates(raw);
    if (GeminiClient.isConfigured) {
      try {
        collapsed = await _aiMerge(collapsed);
      } catch (_) {}
    }
    await _persistCollapsed(raw, collapsed);
    try {
      collapsed = collapseDuplicates(await _fetchRaw());
    } catch (_) {}
    entries = _capAndSort(collapsed);
    return entries;
  }

  static List<DeliveryAddressEntry> _capAndSort(
    List<DeliveryAddressEntry> collapsed,
  ) {
    var out = collapsed;
    if (out.length > maxEntries) {
      out = List<DeliveryAddressEntry>.from(out)
        ..sort((a, b) => b.lastUsedAt.compareTo(a.lastUsedAt));
      out = out.take(maxEntries).toList();
    }
    return sortByShipToName(out);
  }

  /// Drop extras and rewrite keepers to [rowKey] so courier variants persist.
  Future<void> _persistCollapsed(
    List<DeliveryAddressEntry> raw,
    List<DeliveryAddressEntry> collapsed,
  ) async {
    final keepKeys = <String>{};
    for (final e in collapsed) {
      final destKey = rowKey(
        address: e.address,
        carrier: e.carrier,
        accountNumbers: e.accountNumbers,
      );
      final key = destKey.isEmpty ? e.addressKey : destKey;
      keepKeys.add(key);
      try {
        await _upsertRow(e.copyWith(addressKey: key));
      } catch (_) {}
    }
    for (final e in raw) {
      if (keepKeys.contains(e.addressKey)) continue;
      try {
        await _deleteRow(e.addressKey);
      } catch (_) {}
    }
  }

  /// Keep one row per ship-to + place + courier (distinct accounts stay split).
  @visibleForTesting
  static List<DeliveryAddressEntry> collapseDuplicates(
    List<DeliveryAddressEntry> input,
  ) {
    final byCourier = <String, List<DeliveryAddressEntry>>{};
    for (final e in input) {
      final s = AddressMatch.shipToKey(e.shipToName);
      final a = AddressMatch.addressKey(e.address);
      if (a.isEmpty) continue;
      final id = '${s.isEmpty ? '_' : s}|$a|${_normCarrier(e.carrier)}';
      byCourier.putIfAbsent(id, () => []).add(e);
    }
    final out = <DeliveryAddressEntry>[];
    for (final group in byCourier.values) {
      out.addAll(_splitByAccount(group));
    }
    return sortByShipToName(out);
  }

  static List<DeliveryAddressEntry> _splitByAccount(
    List<DeliveryAddressEntry> group,
  ) {
    if (group.length == 1) return group;
    final byAcc = <String, DeliveryAddressEntry>{};
    final empties = <DeliveryAddressEntry>[];
    for (final e in group) {
      final acc = _normAccount(e.accountNumbers);
      if (acc.isEmpty) {
        empties.add(e);
        continue;
      }
      final prev = byAcc[acc];
      byAcc[acc] = prev == null ? e : mergeKeepers(e, prev);
    }
    if (byAcc.isEmpty) {
      return [group.reduce(mergeKeepers)];
    }
    if (empties.isNotEmpty) {
      final emptyMerged = empties.reduce(mergeKeepers);
      DeliveryAddressEntry newest = byAcc.values.first;
      for (final e in byAcc.values) {
        if (e.lastUsedAt.isAfter(newest.lastUsedAt)) newest = e;
      }
      final acc = _normAccount(newest.accountNumbers);
      byAcc[acc] = mergeKeepers(newest, emptyMerged);
    }
    return byAcc.values.toList();
  }

  /// A–Z by Ship To Name (case-insensitive).
  static List<DeliveryAddressEntry> sortByShipToName(
    List<DeliveryAddressEntry> input,
  ) {
    final out = List<DeliveryAddressEntry>.from(input);
    out.sort((a, b) {
      final an = a.shipToName.trim();
      final bn = b.shipToName.trim();
      if (an.isEmpty != bn.isEmpty) return an.isEmpty ? 1 : -1;
      final byName = an.toLowerCase().compareTo(bn.toLowerCase());
      if (byName != 0) return byName;
      return b.lastUsedAt.compareTo(a.lastUsedAt);
    });
    return out;
  }

  @visibleForTesting
  static DeliveryAddressEntry mergeKeepers(
    DeliveryAddressEntry a,
    DeliveryAddressEntry b,
  ) {
    final newer = a.lastUsedAt.isAfter(b.lastUsedAt) ? a : b;
    final older = identical(newer, a) ? b : a;
    final richer = newer.address.trim().length >= older.address.trim().length
        ? newer.address
        : older.address;
    final carrier =
        newer.carrier.trim().isNotEmpty ? newer.carrier : older.carrier;
    final accounts = _mergeAccounts(a.accountNumbers, b.accountNumbers);
    return DeliveryAddressEntry(
      addressKey: rowKey(
        address: richer,
        carrier: carrier,
        accountNumbers: accounts,
      ),
      shipToName: newer.shipToName.trim().isNotEmpty
          ? newer.shipToName
          : older.shipToName,
      address: richer,
      carrier: carrier,
      accountNumbers: accounts,
      lastUsedAt: newer.lastUsedAt,
    );
  }

  static String _mergeAccounts(String a, String b) {
    final parts = <String>{};
    for (final raw in [a, b]) {
      for (final p in raw.split(RegExp(r'[,;/]+'))) {
        final t = p.trim();
        if (t.isNotEmpty) parts.add(t);
      }
    }
    if (parts.isEmpty) {
      return a.trim().isNotEmpty ? a.trim() : b.trim();
    }
    return parts.join(', ');
  }

  /// Stable PK: street fingerprint, plus courier/account when set.
  @visibleForTesting
  static String rowKey({
    required String address,
    required String carrier,
    required String accountNumbers,
  }) {
    final street = AddressMatch.addressKey(address);
    if (street.isEmpty) return '';
    final c = _normCarrier(carrier);
    final acc = _normAccount(accountNumbers);
    if (c.isEmpty && acc.isEmpty) return street;
    return '$street::$c::$acc';
  }

  static String _normCarrier(String raw) =>
      raw.trim().toLowerCase().replaceAll(RegExp(r'\s+'), ' ');

  static String _normAccount(String raw) =>
      raw.trim().toLowerCase().replaceAll(RegExp(r'\s+'), '');

  @visibleForTesting
  static bool sameCourier(DeliveryAddressEntry a, DeliveryAddressEntry b) {
    if (_normCarrier(a.carrier) != _normCarrier(b.carrier)) return false;
    final aa = _normAccount(a.accountNumbers);
    final ab = _normAccount(b.accountNumbers);
    if (aa.isEmpty || ab.isEmpty) return true;
    return aa == ab;
  }

  Future<List<DeliveryAddressEntry>> _aiMerge(
    List<DeliveryAddressEntry> input,
  ) async {
    if (input.length < 2) return input;
    final ai = JobPdfAi();
    final kept = <DeliveryAddressEntry>[];
    for (final e in input) {
      var merged = false;
      for (var i = 0; i < kept.length; i++) {
        final prev = kept[i];
        if (!sameCourier(e, prev)) continue;
        if (AddressMatch.samePlace(
          shipToA: e.shipToName,
          addressA: e.address,
          shipToB: prev.shipToName,
          addressB: prev.address,
        )) {
          kept[i] = mergeKeepers(e, prev);
          merged = true;
          break;
        }
        final sameName = AddressMatch.shipToKey(e.shipToName) ==
            AddressMatch.shipToKey(prev.shipToName);
        if (!sameName) continue;
        final same = await ai.samePlace(
          shipToA: e.shipToName,
          addressA: e.address,
          shipToB: prev.shipToName,
          addressB: prev.address,
        );
        if (!same) continue;
        kept[i] = mergeKeepers(e, prev);
        merged = true;
        break;
      }
      if (!merged) kept.add(e);
    }
    return sortByShipToName(kept);
  }

  Future<void> remember({
    required String shipToName,
    required String address,
    required String carrier,
    required String accountNumbers,
  }) async {
    final addr = address.trim();
    if (addr.isEmpty) return;
    List<DeliveryAddressEntry> existing;
    try {
      existing = await _fetchRaw();
    } catch (_) {
      existing = entries;
    }

    final incoming = DeliveryAddressEntry(
      addressKey: '',
      shipToName: shipToName.trim(),
      address: addr,
      carrier: carrier.trim(),
      accountNumbers: accountNumbers.trim(),
      lastUsedAt: DateTime.now().toUtc(),
    );

    final matches = existing
        .where(
          (e) =>
              AddressMatch.samePlace(
                shipToA: shipToName,
                addressA: addr,
                shipToB: e.shipToName,
                addressB: e.address,
              ) &&
              sameCourier(incoming, e),
        )
        .toList();

    final key = rowKey(
      address: addr,
      carrier: incoming.carrier,
      accountNumbers: incoming.accountNumbers,
    );
    if (key.isEmpty) return;

    await _clearTombstone(key);
    for (final extra in matches) {
      if (extra.addressKey != key) {
        await _deleteRow(extra.addressKey);
      }
    }

    await _upsertRow(incoming.copyWith(addressKey: key));
    await fetchAll();
  }

  Future<List<DeliveryAddressEntry>> _fetchRaw() async {
    final uri = Uri.parse(
      '${AppConfig.supabaseUrl}/rest/v1/shared_delivery_addresses'
      '?select=address_key,ship_to_name,address,carrier,account_numbers,last_used_at'
      '&order=last_used_at.desc'
      '&limit=$fetchLimit',
    );
    final res = await http.get(uri, headers: _headers).timeout(_timeout);
    if (res.statusCode < 200 || res.statusCode >= 300) {
      throw AddressBookSyncException(
        'Could not fetch addresses (${res.statusCode}).',
      );
    }
    final body = jsonDecode(res.body);
    if (body is! List) return [];
    final out = <DeliveryAddressEntry>[];
    for (final row in body) {
      if (row is! Map) continue;
      final m = Map<String, dynamic>.from(row);
      final key = '${m['address_key'] ?? ''}'.trim();
      final address = '${m['address'] ?? ''}'.trim();
      if (key.isEmpty || address.isEmpty) continue;
      out.add(
        DeliveryAddressEntry(
          addressKey: key,
          shipToName: '${m['ship_to_name'] ?? ''}'.trim(),
          address: address,
          carrier: '${m['carrier'] ?? ''}'.trim(),
          accountNumbers: '${m['account_numbers'] ?? ''}'.trim(),
          lastUsedAt: _parseTime(m['last_used_at']) ??
              DateTime.fromMillisecondsSinceEpoch(0, isUtc: true),
        ),
      );
    }
    return out;
  }

  Future<void> forget(String addressOrKey) async {
    final trimmed = addressOrKey.trim();
    if (trimmed.isEmpty) return;
    List<DeliveryAddressEntry> existing;
    try {
      existing = await _fetchRaw();
    } catch (_) {
      existing = entries;
    }
    final toDrop = <String>{};
    for (final e in existing) {
      if (e.addressKey == trimmed) toDrop.add(e.addressKey);
    }
    if (toDrop.isEmpty) {
      final needleKey = AddressMatch.addressKey(trimmed);
      final raw = trimmed.toLowerCase();
      for (final e in existing) {
        if (e.addressKey == needleKey ||
            e.address.trim().toLowerCase() == raw) {
          toDrop.add(e.addressKey);
        }
      }
      if (toDrop.isEmpty && needleKey.isNotEmpty) toDrop.add(needleKey);
    }
    for (final key in toDrop) {
      await _deleteRow(key);
      await _tombstone(key);
    }
    await fetchAll();
  }

  Future<void> _upsertRow(DeliveryAddressEntry e) async {
    final uri = Uri.parse(
      '${AppConfig.supabaseUrl}/rest/v1/shared_delivery_addresses'
      '?on_conflict=address_key',
    );
    final res = await http
        .post(
          uri,
          headers: {
            ..._headers,
            'Prefer': 'resolution=merge-duplicates,return=minimal',
          },
          body: jsonEncode({
            'address_key': e.addressKey,
            'ship_to_name': e.shipToName,
            'address': e.address,
            'carrier': e.carrier,
            'account_numbers': e.accountNumbers,
            'last_used_at': e.lastUsedAt.toUtc().toIso8601String(),
          }),
        )
        .timeout(_timeout);
    if (res.statusCode < 200 || res.statusCode >= 300) {
      throw AddressBookSyncException(
        'Could not save address (${res.statusCode}).',
      );
    }
  }

  Future<void> _deleteRow(String key) async {
    if (key.isEmpty) return;
    final enc = Uri.encodeComponent(key);
    final del = Uri.parse(
      '${AppConfig.supabaseUrl}/rest/v1/shared_delivery_addresses'
      '?address_key=eq.$enc',
    );
    await http.delete(del, headers: _headers).timeout(_timeout);
  }

  Future<void> _tombstone(String key) async {
    final tomb = Uri.parse(
      '${AppConfig.supabaseUrl}/rest/v1/shared_delivery_address_tombstones'
      '?on_conflict=address_key',
    );
    await http
        .post(
          tomb,
          headers: {
            ..._headers,
            'Prefer': 'resolution=merge-duplicates,return=minimal',
          },
          body: jsonEncode({
            'address_key': key,
            'deleted_at': DateTime.now().toUtc().toIso8601String(),
          }),
        )
        .timeout(_timeout);
  }

  Future<void> _clearTombstone(String key) async {
    final enc = Uri.encodeComponent(key);
    final uri = Uri.parse(
      '${AppConfig.supabaseUrl}/rest/v1/shared_delivery_address_tombstones'
      '?address_key=eq.$enc',
    );
    await http.delete(uri, headers: _headers).timeout(_timeout);
  }

  static DateTime? _parseTime(Object? raw) {
    if (raw == null) return null;
    return DateTime.tryParse('$raw')?.toUtc();
  }
}

class AddressBookSyncException implements Exception {
  AddressBookSyncException(this.message);
  final String message;

  @override
  String toString() => message;
}
