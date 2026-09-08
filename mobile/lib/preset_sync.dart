import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;
import 'package:path/path.dart' as p;

import 'app_config.dart';
import 'app_storage.dart';
import 'label_data.dart';

/// Syncs customer presets + logos with Supabase (shared across all installs).
class PresetSync {
  PresetSync(this.storage);

  final AppStorage storage;

  static const _bucket = 'customer-logos';
  static const _timeout = Duration(seconds: 25);

  Map<String, String> get _headers => {
        'apikey': AppConfig.supabaseAnonKey,
        'Authorization': 'Bearer ${AppConfig.supabaseAnonKey}',
        'Content-Type': 'application/json',
      };

  /// Pull remote presets, merge into local cache, download missing logos.
  /// If remote is empty, uploads all local presets first.
  Future<void> syncOnLaunch() async {
    final remoteRows = await _fetchRemotePresets();
    if (remoteRows.isEmpty) {
      await _pushAllLocal();
      return;
    }
    await _mergeRemoteIntoLocal(remoteRows);
    await _pushLocalOnlyPresets(remoteRows);
    await _pushNewerLocalPresets(remoteRows);
  }

  /// Upsert one preset (+ logos) after local save.
  Future<void> pushPreset(LabelKind kind, String displayName) async {
    final key = AppStorage.presetStorageKey(kind, displayName);
    final preset = storage.presets[key];
    if (preset == null) return;

    final logoRefs = <String>[];
    for (final fileName in preset.logoFileNames) {
      final ref = await _ensureLogoUploaded(kind, displayName, fileName);
      if (ref != null) logoRefs.add(ref);
    }

    await _upsertRemote(
      kind: kind,
      name: displayName,
      fields: preset.fields,
      logoRefs: logoRefs,
    );
  }

  /// Remove preset from Supabase after local delete.
  Future<void> deletePreset(LabelKind kind, String displayName) async {
    final kindEnc = Uri.encodeComponent(kind.name);
    final nameEnc = Uri.encodeComponent(displayName);
    final uri = Uri.parse(
      '${AppConfig.supabaseUrl}/rest/v1/customer_presets'
      '?kind=eq.$kindEnc&name=eq.$nameEnc',
    );
    final res = await http.delete(uri, headers: _headers).timeout(_timeout);
    if (res.statusCode >= 200 && res.statusCode < 300) return;
    throw PresetSyncException(
      'Could not delete remote preset (${res.statusCode}).',
    );
  }

  Future<List<_RemotePreset>> _fetchRemotePresets() async {
    final uri = Uri.parse(
      '${AppConfig.supabaseUrl}/rest/v1/customer_presets'
      '?select=kind,name,fields,logo_refs,updated_at',
    );
    final res = await http.get(uri, headers: _headers).timeout(_timeout);
    if (res.statusCode < 200 || res.statusCode >= 300) {
      throw PresetSyncException(
        'Could not fetch presets (${res.statusCode}).',
      );
    }
    final body = jsonDecode(res.body);
    if (body is! List) return [];
    final out = <_RemotePreset>[];
    for (final row in body) {
      if (row is! Map) continue;
      final m = Map<String, dynamic>.from(row);
      final kindName = '${m['kind'] ?? ''}'.trim();
      final name = '${m['name'] ?? ''}'.trim();
      if (kindName.isEmpty || name.isEmpty) continue;
      final kind = LabelKind.values.firstWhere(
        (k) => k.name == kindName,
        orElse: () => LabelKind.shipping,
      );
      final fieldsRaw = m['fields'];
      final fields = <String, String>{};
      if (fieldsRaw is Map) {
        for (final e in fieldsRaw.entries) {
          fields['${e.key}'] = '${e.value}';
        }
      }
      final refs = <String>[];
      final rawRefs = m['logo_refs'];
      if (rawRefs is List) {
        for (final r in rawRefs) {
          final s = '$r'.trim();
          if (s.isNotEmpty) refs.add(s);
        }
      }
      final updatedAt = _parseTime(m['updated_at']);
      out.add(
        _RemotePreset(
          kind: kind,
          name: name,
          fields: fields,
          logoRefs: refs,
          updatedAt: updatedAt,
        ),
      );
    }
    return out;
  }

  Future<void> _mergeRemoteIntoLocal(List<_RemotePreset> remoteRows) async {
    var changed = false;
    for (final remote in remoteRows) {
      final key = AppStorage.presetStorageKey(remote.kind, remote.name);
      final local = storage.presets[key];
      final localUpdated = local != null ? _localUpdatedAt(key) : null;
      if (local != null &&
          localUpdated != null &&
          localUpdated.isAfter(remote.updatedAt)) {
        continue;
      }

      final logoNames = <String>[];
      for (final ref in remote.logoRefs.take(maxCustomerLogos)) {
        final name = await _downloadLogoIfMissing(ref);
        if (name != null) logoNames.add(name);
      }

      storage.presets[key] = CustomerPreset(
        name: remote.name,
        kind: remote.kind,
        fields: {
          for (final k in presetKeysFor(remote.kind))
            k: remote.fields[k] ?? local?.fields[k] ?? '',
        },
        logoFileNames: logoNames,
        // Mirrors remote's own timestamp — this write isn't a local edit.
        updatedAt: remote.updatedAt,
      );
      changed = true;
    }
    if (changed) await storage.savePresets();
  }

  Future<void> _pushLocalOnlyPresets(List<_RemotePreset> remoteRows) async {
    final remoteKeys = {
      for (final r in remoteRows)
        AppStorage.presetStorageKey(r.kind, r.name): r,
    };
    for (final entry in storage.presets.entries) {
      if (remoteKeys.containsKey(entry.key)) continue;
      final preset = entry.value;
      await pushPreset(preset.kind, preset.name);
    }
  }

  Future<void> _pushNewerLocalPresets(List<_RemotePreset> remoteRows) async {
    final remoteByKey = {
      for (final r in remoteRows)
        AppStorage.presetStorageKey(r.kind, r.name): r,
    };
    for (final entry in storage.presets.entries) {
      final remote = remoteByKey[entry.key];
      if (remote == null) continue;
      if (entry.value.updatedAt.isAfter(remote.updatedAt)) {
        await pushPreset(entry.value.kind, entry.value.name);
      }
    }
  }

  Future<void> _pushAllLocal() async {
    for (final preset in storage.presets.values) {
      await pushPreset(preset.kind, preset.name);
    }
  }

  Future<void> _upsertRemote({
    required LabelKind kind,
    required String name,
    required Map<String, String> fields,
    required List<String> logoRefs,
  }) async {
    final uri = Uri.parse(
      '${AppConfig.supabaseUrl}/rest/v1/customer_presets?on_conflict=kind,name',
    );
    final res = await http
        .post(
          uri,
          headers: {
            ..._headers,
            'Prefer': 'resolution=merge-duplicates,return=minimal',
          },
          body: jsonEncode({
            'kind': kind.name,
            'name': name,
            'fields': fields,
            'logo_refs': logoRefs,
          }),
        )
        .timeout(_timeout);
    if (res.statusCode < 200 || res.statusCode >= 300) {
      throw PresetSyncException(
        'Could not save preset to cloud (${res.statusCode}).',
      );
    }

    for (final ref in logoRefs) {
      await _upsertLogoMetadata(ref);
    }
  }

  Future<String?> _ensureLogoUploaded(
    LabelKind kind,
    String presetName,
    String fileName,
  ) async {
    final local = File(p.join(storage.logosDir.path, fileName));
    if (!await local.exists()) return null;

    final storagePath = _logoStoragePath(kind, presetName, fileName);
    final bytes = await local.readAsBytes();
    final contentType = _contentTypeFor(fileName);

    final uri = Uri.parse(
      '${AppConfig.supabaseUrl}/storage/v1/object/$_bucket/$storagePath',
    );
    final res = await http
        .post(
          uri,
          headers: {
            'apikey': AppConfig.supabaseAnonKey,
            'Authorization': 'Bearer ${AppConfig.supabaseAnonKey}',
            'Content-Type': contentType,
            'x-upsert': 'true',
          },
          body: bytes,
        )
        .timeout(_timeout);

    if (res.statusCode < 200 || res.statusCode >= 300) {
      throw PresetSyncException(
        'Could not upload logo (${res.statusCode}).',
      );
    }
    return storagePath;
  }

  Future<void> _upsertLogoMetadata(String storagePath) async {
    final fileName = p.basename(storagePath);
    final uri = Uri.parse(
      '${AppConfig.supabaseUrl}/rest/v1/customer_logos?on_conflict=storage_path',
    );
    await http
        .post(
          uri,
          headers: {
            ..._headers,
            'Prefer': 'resolution=merge-duplicates,return=minimal',
          },
          body: jsonEncode({
            'storage_path': storagePath,
            'file_name': fileName,
            'content_type': _contentTypeFor(fileName),
          }),
        )
        .timeout(_timeout);
  }

  Future<String?> _downloadLogoIfMissing(String storagePath) async {
    final fileName = p.basename(storagePath);
    var dest = File(p.join(storage.logosDir.path, fileName));
    if (await dest.exists()) return fileName;

    final uri = Uri.parse(
      '${AppConfig.supabaseUrl}/storage/v1/object/public/$_bucket/$storagePath',
    );
    final res = await http.get(uri).timeout(_timeout);
    if (res.statusCode < 200 || res.statusCode >= 300) return null;

    // Write exact remote basename — do not reimport (avoids "stem (2).png").
    await dest.parent.create(recursive: true);
    await dest.writeAsBytes(res.bodyBytes, flush: true);
    return fileName;
  }

  static String _logoStoragePath(
    LabelKind kind,
    String presetName,
    String fileName,
  ) {
    final safePreset = presetName
        .replaceAll(RegExp(r'[^\w\- ]+'), '')
        .trim()
        .replaceAll(' ', '_');
    final safeFile = fileName.replaceAll(RegExp(r'[^\w.\-() ]+'), '_');
    return '${kind.name}/$safePreset/$safeFile';
  }

  static String _contentTypeFor(String fileName) {
    switch (p.extension(fileName).toLowerCase()) {
      case '.jpg':
      case '.jpeg':
        return 'image/jpeg';
      case '.gif':
        return 'image/gif';
      case '.webp':
        return 'image/webp';
      case '.bmp':
        return 'image/bmp';
      default:
        return 'image/png';
    }
  }

  /// Per-preset local edit time (not the whole `presets.json` file's mtime —
  /// that would make editing any one preset look like every preset changed).
  DateTime _localUpdatedAt(String storageKey) =>
      storage.presets[storageKey]?.updatedAt ??
      DateTime.fromMillisecondsSinceEpoch(0);

  static DateTime _parseTime(Object? raw) {
    if (raw == null) return DateTime.fromMillisecondsSinceEpoch(0);
    try {
      return DateTime.parse('$raw').toUtc();
    } catch (_) {
      return DateTime.fromMillisecondsSinceEpoch(0);
    }
  }
}

class _RemotePreset {
  const _RemotePreset({
    required this.kind,
    required this.name,
    required this.fields,
    required this.logoRefs,
    required this.updatedAt,
  });

  final LabelKind kind;
  final String name;
  final Map<String, String> fields;
  final List<String> logoRefs;
  final DateTime updatedAt;
}

class PresetSyncException implements Exception {
  const PresetSyncException(this.message);
  final String message;

  @override
  String toString() => message;
}
