import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:path/path.dart' as p;

import 'app_config.dart';
import 'app_storage.dart';
import 'label_data.dart';

/// Field + logo snapshot saved beside a generated PDF for "Use template".
class HistoryFormSnapshot {
  const HistoryFormSnapshot({
    required this.fields,
    this.logoCount = 0,
  });

  final Map<String, String> fields;
  final int logoCount;

  bool get hasFields => fields.values.any((v) => v.trim().isNotEmpty);

  Map<String, dynamic> toJson() => {
        'fields': fields,
        'logo_count': logoCount,
      };

  factory HistoryFormSnapshot.fromJson(Map<String, dynamic> json) {
    final raw = json['fields'];
    final fields = <String, String>{};
    if (raw is Map) {
      for (final e in raw.entries) {
        fields['${e.key}'] = '${e.value}';
      }
    }
    final n = json['logo_count'];
    return HistoryFormSnapshot(
      fields: fields,
      logoCount: n is num ? n.toInt() : 0,
    );
  }
}

/// Metadata for a generated PDF stored in Supabase.
class GeneratedDocumentRecord {
  const GeneratedDocumentRecord({
    required this.id,
    required this.kind,
    required this.title,
    required this.customer,
    required this.salesOrder,
    required this.fileName,
    required this.storagePath,
    required this.byteSize,
    required this.createdAt,
    this.snapshotSaved = true,
  });

  final String id;
  final LabelKind kind;
  final String title;
  final String customer;
  final String salesOrder;
  final String fileName;
  final String storagePath;
  final int byteSize;
  final DateTime createdAt;
  /// False when PDF metadata uploaded but form.json / logo snapshot failed.
  final bool snapshotSaved;
}

/// Uploads / lists / downloads generated PDFs (cloud source of truth).
class DocumentHistorySync {
  DocumentHistorySync(this.storage);

  final AppStorage storage;

  static const bucket = 'generated-documents';
  static const _timeout = Duration(seconds: 45);

  Map<String, String> get _headers => {
        'apikey': AppConfig.supabaseAnonKey,
        'Authorization': 'Bearer ${AppConfig.supabaseAnonKey}',
        'Content-Type': 'application/json',
      };

  Future<GeneratedDocumentRecord> upload({
    required LabelKind kind,
    required String fileName,
    required Uint8List bytes,
    required String customer,
    required String salesOrder,
    String? title,
    Map<String, String>? fields,
    List<Uint8List>? logoBytes,
  }) async {
    final id = newId();
    final safeName = fileName.trim().isEmpty ? '$id.pdf' : fileName.trim();
    final storagePath = '${kind.name}/$id/${p.basename(safeName)}';
    await _uploadBytes(storagePath, bytes, contentType: 'application/pdf');
    var snapshotSaved = true;
    if (fields != null) {
      try {
        await _saveSnapshot(
          kind: kind,
          id: id,
          snapshot: HistoryFormSnapshot(
            fields: fields,
            logoCount: logoBytes?.length ?? 0,
          ),
          logoBytes: logoBytes ?? const [],
        );
      } catch (e) {
        snapshotSaved = false;
        // PDF + row still archive; Template button needs form.json.
        debugPrint('document_history: snapshot upload failed: $e');
      }
    }

    final created = DateTime.now().toUtc();
    final displayTitle = (title ?? safeName).trim();
    final uri = Uri.parse(
      '${AppConfig.supabaseUrl}/rest/v1/generated_documents?on_conflict=id',
    );
    final res = await http
        .post(
          uri,
          headers: {
            ..._headers,
            'Prefer': 'resolution=merge-duplicates,return=minimal',
          },
          body: jsonEncode({
            'id': id,
            'kind': kind.name,
            'title': displayTitle,
            'customer': customer.trim(),
            'sales_order': salesOrder.trim(),
            'file_name': safeName,
            'storage_path': storagePath,
            'byte_size': bytes.length,
            'created_at': created.toIso8601String(),
          }),
        )
        .timeout(_timeout);
    if (res.statusCode < 200 || res.statusCode >= 300) {
      throw DocumentHistorySyncException(
        'Could not save document metadata (${res.statusCode}).',
      );
    }
    return GeneratedDocumentRecord(
      id: id,
      kind: kind,
      title: displayTitle,
      customer: customer.trim(),
      salesOrder: salesOrder.trim(),
      fileName: safeName,
      storagePath: storagePath,
      byteSize: bytes.length,
      createdAt: created,
      snapshotSaved: snapshotSaved,
    );
  }

  static const historyKinds = [
    LabelKind.shipping,
    LabelKind.receiving,
    LabelKind.bol,
    LabelKind.bulk,
  ];

  Future<List<GeneratedDocumentRecord>> listForKind(
    LabelKind kind, {
    int limit = 60,
  }) async {
    // Retention purge runs on launch (and in the background). Never block the
    // History dialog on a full remote scan.
    return _fetchKind(
      kind,
      limit: limit,
      createdAtGte: _retentionCutoff(),
    );
  }

  /// Fetch the **entire** history for [kind] within the 90-day retention
  /// window — no artificial UI cap. Rows are tiny metadata (no PDF bytes),
  /// so paginating through a few thousand of them under the hood is trivial;
  /// this exists so the History dialog's Quick Search / pagination operate
  /// over the real dataset instead of a silently-truncated recent-N slice
  /// (the old `listForKind(limit: 60)` default is exactly why entries used
  /// to "cut off" once a kind passed ~60 documents).
  Future<List<GeneratedDocumentRecord>> listAllForKind(
    LabelKind kind, {
    int batchSize = 500,
  }) async {
    final cutoff = _retentionCutoff();
    final out = <GeneratedDocumentRecord>[];
    var offset = 0;
    // Sane hard stop so a server-side paging bug can't loop forever; far
    // beyond any realistic single-kind volume for this app.
    const hardCap = 20000;
    while (offset < hardCap) {
      final batch = await _fetchKind(
        kind,
        limit: batchSize,
        offset: offset,
        createdAtGte: cutoff,
      );
      out.addAll(batch);
      if (batch.length < batchSize) break;
      offset += batchSize;
    }
    return out;
  }

  /// Case-insensitive substring match against title/customer/sales order/
  /// file name — pure + testable, and the single source of truth used by
  /// both the real History dialog and its unit tests.
  static List<GeneratedDocumentRecord> filterDocs(
    List<GeneratedDocumentRecord> docs,
    String query,
  ) {
    final q = query.trim().toLowerCase();
    if (q.isEmpty) return docs;
    return docs.where((d) {
      final haystack = [
        d.title,
        d.customer,
        d.salesOrder,
        d.fileName,
      ].join(' ').toLowerCase();
      return haystack.contains(q);
    }).toList(growable: false);
  }

  /// Page count for [pageSize]-sized pages over [itemCount] items (always
  /// at least 1, even for zero items, so "Page 1 of 1" reads sensibly).
  static int pageCountFor(int itemCount, int pageSize) {
    if (itemCount <= 0) return 1;
    return (itemCount + pageSize - 1) ~/ pageSize;
  }

  /// The slice of [items] for a 0-based [page] at [pageSize] entries/page.
  static List<T> pageSlice<T>(List<T> items, int page, int pageSize) {
    final start = page * pageSize;
    if (start < 0 || start >= items.length) return const [];
    final end = (start + pageSize).clamp(0, items.length);
    return items.sublist(start, end);
  }

  /// Opt-in CLI only — do **not** call from the History UI.
  ///
  /// Removes rows with no local/cloud `form.json`. Missing snapshots used to
  /// be common (bucket MIME was PDF-only), so prune-on-open wiped all kinds.
  Future<int> pruneWithoutSnapshots() async {
    var removed = 0;
    for (final kind in historyKinds) {
      List<GeneratedDocumentRecord> docs;
      try {
        docs = await _fetchKind(kind, limit: 1000);
      } catch (_) {
        continue;
      }
      for (final doc in docs) {
        try {
          if (await hasFormSnapshot(doc)) continue;
          await deleteRecord(doc);
          removed++;
        } catch (_) {}
      }
    }
    return removed;
  }

  Future<bool> hasFormSnapshot(GeneratedDocumentRecord doc) async {
    final local = _localSnapshotFile(doc.id);
    if (await local.exists()) {
      try {
        final json = jsonDecode(await local.readAsString());
        if (json is Map) return true;
      } catch (_) {}
    }
    try {
      final path = '${_dirFor(doc)}/form.json';
      final encoded = path.split('/').map(Uri.encodeComponent).join('/');
      final uri = Uri.parse(
        '${AppConfig.supabaseUrl}/storage/v1/object/public/$bucket/$encoded',
      );
      final res = await http.get(uri).timeout(_timeout);
      if (isMissingStorageResponse(res.statusCode, res.body)) return false;
      if (res.statusCode < 200 || res.statusCode >= 300) {
        // Uncertain (auth/network) — keep the row.
        return true;
      }
      final json = jsonDecode(res.body);
      return json is Map;
    } catch (_) {
      return true;
    }
  }

  /// Supabase public storage often returns HTTP 400 with JSON statusCode 404.
  static bool isMissingStorageResponse(int statusCode, String body) {
    if (statusCode == 404) return true;
    try {
      final json = jsonDecode(body);
      if (json is! Map) return false;
      final sc = '${json['statusCode'] ?? ''}';
      final code = '${json['code'] ?? ''}';
      final err = '${json['error'] ?? ''}';
      return sc == '404' || code == 'NoSuchKey' || err == 'not_found';
    } catch (_) {
      return false;
    }
  }

  /// Delete REST row, every storage object under `{kind}/{id}/`, and local
  /// history cache for this id. Ignores 404. Does not touch unrelated
  /// `filled/` Generate PDFs.
  Future<void> deleteRecord(GeneratedDocumentRecord doc) async {
    try {
      await _deleteAllStorageFor(doc);
    } catch (_) {}
    try {
      final del = Uri.parse(
        '${AppConfig.supabaseUrl}/rest/v1/generated_documents'
        '?id=eq.${Uri.encodeComponent(doc.id)}',
      );
      final res = await http
          .delete(del, headers: _headers)
          .timeout(const Duration(seconds: 20));
      if (res.statusCode != 404 &&
          (res.statusCode < 200 || res.statusCode >= 300)) {
        throw DocumentHistorySyncException(
          'Could not delete history row (${res.statusCode}).',
        );
      }
    } catch (e) {
      if (e is DocumentHistorySyncException) rethrow;
    }
    await _deleteLocalCacheForId(doc);
  }

  Future<List<GeneratedDocumentRecord>> _fetchKind(
    LabelKind kind, {
    int limit = 60,
    int offset = 0,
    DateTime? createdAtGte,
  }) async {
    final kindEnc = Uri.encodeComponent(kind.name);
    final cutoffQ = createdAtGte == null
        ? ''
        : '&created_at=gte.${Uri.encodeComponent(createdAtGte.toIso8601String())}';
    final offsetQ = offset > 0 ? '&offset=$offset' : '';
    final uri = Uri.parse(
      '${AppConfig.supabaseUrl}/rest/v1/generated_documents'
      '?kind=eq.$kindEnc'
      '$cutoffQ'
      '&select=id,kind,title,customer,sales_order,file_name,storage_path,byte_size,created_at'
      '&order=created_at.desc'
      '&limit=$limit'
      '$offsetQ',
    );
    final res = await http.get(uri, headers: _headers).timeout(_timeout);
    if (res.statusCode < 200 || res.statusCode >= 300) {
      throw DocumentHistorySyncException(
        'Could not list documents (${res.statusCode}).',
      );
    }
    final body = jsonDecode(res.body);
    if (body is! List) return [];
    final out = <GeneratedDocumentRecord>[];
    for (final row in body) {
      if (row is! Map) continue;
      final m = Map<String, dynamic>.from(row);
      final id = '${m['id'] ?? ''}'.trim();
      final kindName = '${m['kind'] ?? ''}'.trim();
      final path = '${m['storage_path'] ?? ''}'.trim();
      final fileName = '${m['file_name'] ?? ''}'.trim();
      if (id.isEmpty || path.isEmpty) continue;
      final k = LabelKind.values.firstWhere(
        (x) => x.name == kindName,
        orElse: () => LabelKind.shipping,
      );
      out.add(
        GeneratedDocumentRecord(
          id: id,
          kind: k,
          title: '${m['title'] ?? fileName}'.trim(),
          customer: '${m['customer'] ?? ''}'.trim(),
          salesOrder: '${m['sales_order'] ?? ''}'.trim(),
          fileName: fileName.isEmpty ? p.basename(path) : fileName,
          storagePath: path,
          byteSize: (m['byte_size'] is num) ? (m['byte_size'] as num).toInt() : 0,
          createdAt: DateTime.tryParse('${m['created_at']}')?.toUtc() ??
              DateTime.fromMillisecondsSinceEpoch(0, isUtc: true),
        ),
      );
    }
    return out;
  }

  /// Download the unique cloud object for this history row.
  ///
  /// Cache is keyed by document [id], never by display [fileName] — regenerating
  /// the same customer+SO overwrites `filled/{fileName}` locally, which must not
  /// be reused for an older history entry.
  Future<File> downloadToCache(GeneratedDocumentRecord doc) async {
    final stem = p.basenameWithoutExtension(
      doc.fileName.isEmpty ? 'document' : doc.fileName,
    );
    final cache = File(
      p.join(storage.filledDir.path, '${stem}_${doc.id}.pdf'),
    );
    if (await cache.exists()) {
      final len = await cache.length();
      if (len > 0 && (doc.byteSize <= 0 || len == doc.byteSize)) {
        return cache;
      }
    }

    final encodedPath = doc.storagePath
        .split('/')
        .map(Uri.encodeComponent)
        .join('/');
    final uri = Uri.parse(
      '${AppConfig.supabaseUrl}/storage/v1/object/public/$bucket/$encodedPath',
    );
    final res = await http.get(uri).timeout(_timeout);
    if (res.statusCode < 200 || res.statusCode >= 300) {
      throw DocumentHistorySyncException(
        'Could not download PDF (${res.statusCode}).',
      );
    }
    await storage.filledDir.create(recursive: true);
    await cache.writeAsBytes(res.bodyBytes, flush: true);
    return cache;
  }

  /// Drop history older than 90 days from Supabase (rows + PDFs) and local cache.
  Future<void> purgeExpired() async {
    final cutoff = _retentionCutoff();
    try {
      await _purgeExpiredRemote(cutoff);
    } catch (_) {}
    try {
      await _purgeExpiredLocalCache(cutoff);
    } catch (_) {}
  }

  static DateTime _retentionCutoff() =>
      DateTime.now().toUtc().subtract(retention);

  static const retention = Duration(days: 90);

  Future<void> _purgeExpiredRemote(DateTime cutoff) async {
    final cutoffEnc = Uri.encodeComponent(cutoff.toIso8601String());
    final uri = Uri.parse(
      '${AppConfig.supabaseUrl}/rest/v1/generated_documents'
      '?created_at=lt.$cutoffEnc'
      '&select=id,storage_path,file_name',
    );
    final res = await http.get(uri, headers: _headers).timeout(_timeout);
    if (res.statusCode < 200 || res.statusCode >= 300) return;
    final body = jsonDecode(res.body);
    if (body is! List) return;
    for (final row in body) {
      if (row is! Map) continue;
      final path = '${row['storage_path'] ?? ''}'.trim();
      final id = '${row['id'] ?? ''}'.trim();
      if (path.isNotEmpty) {
        await _deleteStorageObject(path);
      }
      if (id.isEmpty) continue;
      final del = Uri.parse(
        '${AppConfig.supabaseUrl}/rest/v1/generated_documents?id=eq.${Uri.encodeComponent(id)}',
      );
      await http
          .delete(del, headers: _headers)
          .timeout(const Duration(seconds: 20));
    }
  }

  Map<String, String> get _storageHeaders => {
        'apikey': AppConfig.supabaseAnonKey,
        'Authorization': 'Bearer ${AppConfig.supabaseAnonKey}',
      };

  Future<void> _deleteAllStorageFor(GeneratedDocumentRecord doc) async {
    final dir = _dirFor(doc);
    final listed = await _listStoragePrefix(dir);
    final paths = <String>{
      if (doc.storagePath.trim().isNotEmpty) doc.storagePath.trim(),
      if (doc.fileName.trim().isNotEmpty) '$dir/${p.basename(doc.fileName)}',
      ...listed,
      '$dir/form.json',
      for (var i = 0; i < maxCustomerLogos; i++) '$dir/logo_$i.png',
    };
    for (final path in paths) {
      await _deleteStorageObject(path);
    }
    await _deleteStoragePrefixes({dir, ...paths});
  }

  /// Same auth as upload (anon). Encoded path first, then raw.
  Future<void> _deleteStorageObject(String storagePath) async {
    final trimmed = storagePath.trim();
    if (trimmed.isEmpty) return;
    final encoded = trimmed.split('/').map(Uri.encodeComponent).join('/');
    final candidates = [
      Uri.parse('${AppConfig.supabaseUrl}/storage/v1/object/$bucket/$encoded'),
      Uri.parse('${AppConfig.supabaseUrl}/storage/v1/object/$bucket/$trimmed'),
    ];
    for (final uri in candidates) {
      final res = await http
          .delete(uri, headers: _storageHeaders)
          .timeout(const Duration(seconds: 20));
      if (res.statusCode == 404 ||
          (res.statusCode >= 200 && res.statusCode < 300) ||
          isMissingStorageResponse(res.statusCode, res.body)) {
        return;
      }
    }
  }

  Future<void> _deleteStoragePrefixes(Set<String> prefixes) async {
    final paths = prefixes.where((p) => p.trim().isNotEmpty).toList();
    if (paths.isEmpty) return;
    final uri = Uri.parse(
      '${AppConfig.supabaseUrl}/storage/v1/object/$bucket',
    );
    await http
        .delete(
          uri,
          headers: _headers,
          body: jsonEncode({'prefixes': paths}),
        )
        .timeout(const Duration(seconds: 20));
  }

  Future<List<String>> _listStoragePrefix(String prefix) async {
    final dir = prefix.replaceAll(RegExp(r'/+$'), '');
    if (dir.isEmpty) return [];
    final out = <String>[];
    await _collectStoragePrefix(dir, out);
    return out;
  }

  Future<void> _collectStoragePrefix(String prefix, List<String> out) async {
    final uri = Uri.parse(
      '${AppConfig.supabaseUrl}/storage/v1/object/list/$bucket',
    );
    final res = await http
        .post(
          uri,
          headers: _headers,
          body: jsonEncode({
            'prefix': prefix,
            'limit': 100,
            'offset': 0,
          }),
        )
        .timeout(const Duration(seconds: 20));
    if (res.statusCode == 404 || res.statusCode < 200 || res.statusCode >= 300) {
      return;
    }
    final body = jsonDecode(res.body);
    if (body is! List) return;
    for (final item in body) {
      if (item is! Map) continue;
      final name = '${item['name'] ?? ''}'.trim();
      if (name.isEmpty) continue;
      final full = '$prefix/$name';
      if (item['id'] == null && item['metadata'] == null) {
        await _collectStoragePrefix(full, out);
        continue;
      }
      out.add(full);
    }
  }

  /// History download cache `{stem}_{id}.pdf` or leftover `{id}.form.json`.
  /// Not a plain Generate output like `Customer_SO.pdf`.
  static bool isHistoryLocalFileForId(String basename, String id) {
    if (id.isEmpty || basename.isEmpty) return false;
    if (basename == '$id.form.json') return true;
    if (basename.endsWith('_$id.pdf')) return true;
    return false;
  }

  Future<void> _deleteLocalCacheForId(GeneratedDocumentRecord doc) async {
    final id = doc.id;
    if (id.isEmpty) return;
    final dir = storage.filledDir;
    if (await dir.exists()) {
      await for (final entity in dir.list(followLinks: false)) {
        if (entity is! File) continue;
        final name = p.basename(entity.path);
        if (!isHistoryLocalFileForId(name, id)) continue;
        try {
          await entity.delete();
        } catch (_) {}
      }
    }
    final logos = storage.logosDir;
    if (await logos.exists()) {
      await for (final entity in logos.list(followLinks: false)) {
        if (entity is! File) continue;
        final name = p.basename(entity.path);
        if (!name.startsWith('history_${id}_')) continue;
        try {
          await entity.delete();
        } catch (_) {}
      }
    }
  }

  Future<void> _purgeExpiredLocalCache(DateTime cutoff) async {
    final dir = storage.filledDir;
    if (!await dir.exists()) return;
    await for (final entity in dir.list(followLinks: false)) {
      if (entity is! File) continue;
      if (p.extension(entity.path).toLowerCase() != '.pdf') continue;
      try {
        final stat = await entity.stat();
        if (stat.modified.toUtc().isBefore(cutoff)) {
          await entity.delete();
        }
      } catch (_) {}
    }
  }

  String _dirFor(GeneratedDocumentRecord doc) {
    final parts = doc.storagePath.split('/');
    if (parts.length >= 2) return '${parts[0]}/${parts[1]}';
    return '${doc.kind.name}/${doc.id}';
  }

  Future<HistoryFormSnapshot?> downloadFormSnapshot(
    GeneratedDocumentRecord doc,
  ) async {
    final local = _localSnapshotFile(doc.id);
    if (await local.exists()) {
      try {
        final json = jsonDecode(await local.readAsString());
        if (json is Map<String, dynamic> || json is Map) {
          return HistoryFormSnapshot.fromJson(Map<String, dynamic>.from(json as Map));
        }
      } catch (_) {}
    }
    try {
      final path = '${_dirFor(doc)}/form.json';
      final encoded = path.split('/').map(Uri.encodeComponent).join('/');
      final uri = Uri.parse(
        '${AppConfig.supabaseUrl}/storage/v1/object/public/$bucket/$encoded',
      );
      final res = await http.get(uri).timeout(_timeout);
      if (res.statusCode < 200 || res.statusCode >= 300) return null;
      final json = jsonDecode(res.body);
      if (json is! Map) return null;
      final snap = HistoryFormSnapshot.fromJson(Map<String, dynamic>.from(json));
      try {
        await local.parent.create(recursive: true);
        await local.writeAsString(jsonEncode(snap.toJson()));
      } catch (_) {}
      return snap;
    } catch (_) {
      return null;
    }
  }

  Future<List<Uint8List>> downloadSnapshotLogos(
    GeneratedDocumentRecord doc,
    int logoCount,
  ) async {
    final out = <Uint8List>[];
    for (var i = 0; i < logoCount && i < maxCustomerLogos; i++) {
      try {
        final path = '${_dirFor(doc)}/logo_$i.png';
        final encoded = path.split('/').map(Uri.encodeComponent).join('/');
        final uri = Uri.parse(
          '${AppConfig.supabaseUrl}/storage/v1/object/public/$bucket/$encoded',
        );
        final res = await http.get(uri).timeout(_timeout);
        if (res.statusCode >= 200 && res.statusCode < 300 && res.bodyBytes.isNotEmpty) {
          out.add(res.bodyBytes);
        }
      } catch (_) {}
    }
    return out;
  }

  Future<void> _saveSnapshot({
    required LabelKind kind,
    required String id,
    required HistoryFormSnapshot snapshot,
    required List<Uint8List> logoBytes,
  }) async {
    final jsonBytes = Uint8List.fromList(
      utf8.encode(jsonEncode(snapshot.toJson())),
    );
    final dir = '${kind.name}/$id';
    await _uploadBytes(
      '$dir/form.json',
      jsonBytes,
      contentType: 'application/json',
    );
    try {
      final local = _localSnapshotFile(id);
      await local.parent.create(recursive: true);
      await local.writeAsString(jsonEncode(snapshot.toJson()));
    } catch (_) {
      // Local cache is best-effort; cloud form.json is required for Template.
    }
    for (var i = 0; i < logoBytes.length && i < maxCustomerLogos; i++) {
      await _uploadBytes(
        '$dir/logo_$i.png',
        logoBytes[i],
        contentType: 'image/png',
      );
    }
  }

  File _localSnapshotFile(String id) =>
      File(p.join(storage.filledDir.path, '$id.form.json'));

  Future<void> _uploadBytes(
    String storagePath,
    Uint8List bytes, {
    String contentType = 'application/pdf',
  }) async {
    final uri = Uri.parse(
      '${AppConfig.supabaseUrl}/storage/v1/object/$bucket/$storagePath',
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
      throw DocumentHistorySyncException(
        'Could not upload ($contentType, ${res.statusCode}).',
      );
    }
  }

  /// A fresh id for one `generated_documents` row: microsecond timestamp +
  /// a per-process random salt (differs across app launches/devices) +
  /// a monotonic in-process counter. The counter alone *provably* rules out
  /// same-process collisions no matter how tight the calling loop is or how
  /// coarse the platform clock's real resolution turns out to be (measured:
  /// a plain timestamp+random-suffix scheme still collided under a tight
  /// loop in testing). Never derived from `sales_order`/PO — two entries
  /// sharing an SO/PO must never collide on id, so each can be found,
  /// individually deleted, and independently purged after 90 days on its own.
  @visibleForTesting
  static String newId() {
    final micros = DateTime.now().toUtc().microsecondsSinceEpoch;
    final seq = _idCounter++;
    return '${micros.toRadixString(36)}$_idSessionSalt${seq.toRadixString(36)}';
  }

  static final _idSessionSalt =
      Random().nextInt(1296).toRadixString(36).padLeft(2, '0');
  static int _idCounter = 0;
}

class DocumentHistorySyncException implements Exception {
  DocumentHistorySyncException(this.message);
  final String message;

  @override
  String toString() => message;
}
