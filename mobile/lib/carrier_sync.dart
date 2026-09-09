import 'dart:convert';

import 'package:http/http.dart' as http;

import 'app_config.dart';

/// Cross-app shared carrier-name directory (`shared_carriers` /
/// `shared_carrier_tombstones`) — the same store behind Swift Staging &
/// Shipping Log's "Carrier" field (Windows/Android and Wear).
///
/// Unlike [ContactSync], this has no local cache: the Carrier field is a
/// lighter-weight remembered-value list (matching how Carrier already works
/// on Staging & Shipping Log), fetched live rather than persisted per device.
class CarrierSync {
  static const _timeout = Duration(seconds: 25);

  Map<String, String> get _headers => {
        'apikey': AppConfig.supabaseAnonKey,
        'Authorization': 'Bearer ${AppConfig.supabaseAnonKey}',
        'Content-Type': 'application/json',
      };

  static String nameKey(String raw) => raw.trim().toLowerCase();

  /// Remembered carrier names, most-recently-used first, honoring tombstones.
  Future<List<String>> fetchNames({int limit = 60}) async {
    final tombstones = await _fetchTombstoneKeys();
    final uri = Uri.parse(
      '${AppConfig.supabaseUrl}/rest/v1/shared_carriers'
      '?select=name_key,name,last_used_at'
      '&order=last_used_at.desc',
    );
    final res = await http.get(uri, headers: _headers).timeout(_timeout);
    if (res.statusCode < 200 || res.statusCode >= 300) {
      throw CarrierSyncException(
        'Could not fetch shared carriers (${res.statusCode}).',
      );
    }
    final body = jsonDecode(res.body);
    if (body is! List) return [];
    final seen = <String>{};
    final out = <String>[];
    for (final row in body) {
      if (row is! Map) continue;
      final m = Map<String, dynamic>.from(row);
      final key = '${m['name_key'] ?? ''}'.trim();
      final name = '${m['name'] ?? ''}'.trim();
      if (key.isEmpty || name.isEmpty) continue;
      if (tombstones.contains(key)) continue;
      if (!seen.add(key)) continue;
      out.add(name);
      if (out.length >= limit) break;
    }
    return out;
  }

  Future<Set<String>> _fetchTombstoneKeys() async {
    final uri = Uri.parse(
      '${AppConfig.supabaseUrl}/rest/v1/shared_carrier_tombstones'
      '?select=name_key',
    );
    final res = await http.get(uri, headers: _headers).timeout(_timeout);
    if (res.statusCode < 200 || res.statusCode >= 300) return {};
    final body = jsonDecode(res.body);
    if (body is! List) return {};
    final out = <String>{};
    for (final row in body) {
      if (row is! Map) continue;
      final key = '${Map<String, dynamic>.from(row)['name_key'] ?? ''}'.trim();
      if (key.isNotEmpty) out.add(key);
    }
    return out;
  }

  /// Remembers a carrier name into the shared directory (upsert; clears any
  /// prior tombstone so a re-typed name reappears everywhere it was forgotten).
  Future<void> remember(String raw) async {
    final name = raw.trim();
    if (name.isEmpty) return;
    final key = nameKey(name);
    await _clearTombstone(key);
    final uri = Uri.parse(
      '${AppConfig.supabaseUrl}/rest/v1/shared_carriers?on_conflict=name_key',
    );
    final res = await http
        .post(
          uri,
          headers: {
            ..._headers,
            'Prefer': 'resolution=merge-duplicates,return=minimal',
          },
          body: jsonEncode({
            'name_key': key,
            'name': name,
            'last_used_at': DateTime.now().toUtc().toIso8601String(),
          }),
        )
        .timeout(_timeout);
    if (res.statusCode < 200 || res.statusCode >= 300) {
      throw CarrierSyncException(
        'Could not save carrier “$name” (${res.statusCode}).',
      );
    }
  }

  Future<void> _clearTombstone(String key) async {
    final enc = Uri.encodeComponent(key);
    final uri = Uri.parse(
      '${AppConfig.supabaseUrl}/rest/v1/shared_carrier_tombstones'
      '?name_key=eq.$enc',
    );
    await http.delete(uri, headers: _headers).timeout(_timeout);
  }
}

class CarrierSyncException implements Exception {
  CarrierSyncException(this.message);
  final String message;

  @override
  String toString() => message;
}
