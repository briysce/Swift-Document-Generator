import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

import 'label_data.dart';
import 'logo_dedupe.dart';
import 'logo_image_process.dart';
import 'logo_import_options.dart';
import 'pdf_render_options.dart';

/// Result of [AppStorage.importLogoBytes] / [AppStorage.importLogo].
class ImportLogoResult {
  const ImportLogoResult({
    required this.file,
    this.reused = false,
  });

  final File file;

  /// True when an existing stored file was the same mark — no new copy written.
  final bool reused;
}

class LogoLibraryDedupeReport {
  const LogoLibraryDedupeReport({
    required this.groups,
    required this.deleted,
    required this.kept,
    required this.removed,
  });

  final int groups;
  final int deleted;
  final List<String> kept;
  final List<String> removed;
}

/// App-private storage for presets, logos, and generated PDFs.
class AppStorage {
  AppStorage._(this.root);

  /// Test helper — does not load presets/signatures from disk.
  @visibleForTesting
  factory AppStorage.forTesting(Directory root) => AppStorage._(root);

  final Directory root;

  Directory get logosDir => Directory(p.join(root.path, 'customer_logos'));
  Directory get signaturesDir => Directory(p.join(root.path, 'signatures'));
  Directory get filledDir => Directory(p.join(root.path, 'filled'));
  File get presetsFile => File(p.join(root.path, 'presets.json'));
  File get signaturesFile => File(p.join(root.path, 'signatures.json'));
  File get updateScheduleFile => File(p.join(root.path, 'update_schedule.json'));
  File get settingsFile => File(p.join(root.path, 'settings.json'));
  /// Local cache of contact names used in autocomplete (most recent first).
  /// Synced across devices via [ContactSync] / `shared_contacts` — not SLST roster.
  File get rememberedContactsFile =>
      File(p.join(root.path, 'remembered_contacts.json'));

  Map<String, CustomerPreset> presets = {};
  List<SavedSignature> signatures = [];
  /// Most-recently-used contact names (local cache of shared_contacts).
  List<String> rememberedContacts = [];

  static const maxRememberedContacts = 60;

  /// Bump to force every install to wipe local user data once (presets, logos,
  /// signatures, remembered contacts, filled PDFs, settings). Prevents devices
  /// from re-uploading stale data after a cloud reset. Does not wipe shared
  /// cloud contacts (`shared_contacts`).
  static const localDataEpoch = 2;

  File get dataEpochFile => File(p.join(root.path, 'data_epoch.json'));

  /// Storage map key for a preset (`shipping::Name`, etc.).
  static String presetStorageKey(LabelKind kind, String displayName) =>
      '${kind.name}::$displayName';

  static LabelKind presetKindFromStorageKey(String storageKey) {
    final sep = storageKey.indexOf('::');
    if (sep <= 0) return LabelKind.shipping;
    final kindName = storageKey.substring(0, sep);
    return LabelKind.values.firstWhere(
      (k) => k.name == kindName,
      orElse: () => LabelKind.shipping,
    );
  }

  static String presetDisplayNameFromStorageKey(String storageKey) {
    final sep = storageKey.indexOf('::');
    if (sep < 0) return storageKey;
    return storageKey.substring(sep + 2);
  }

  List<String> presetDisplayNamesFor(LabelKind kind) {
    final prefix = '${kind.name}::';
    return presets.entries
        .where((e) => e.key.startsWith(prefix))
        .map((e) => e.value.name)
        .toList()
      ..sort();
  }

  CustomerPreset? presetFor(LabelKind kind, String displayName) =>
      presets[presetStorageKey(kind, displayName)];

  static Future<AppStorage> open() async {
    final docs = await getApplicationDocumentsDirectory();
    final root = Directory(p.join(docs.path, 'swift_document_generator'));
    final legacy = Directory(p.join(docs.path, 'swift_shipping_label'));
    // One-time migrate presets/logos from the old app folder name.
    if (!await root.exists() && await legacy.exists()) {
      try {
        await legacy.rename(root.path);
      } catch (_) {
        await root.create(recursive: true);
      }
    }
    final store = AppStorage._(root);
    await store.ensureDirs();
    await store.applyLocalDataEpochIfNeeded();
    await store.loadPresets();
    await store.loadSignatures();
    await store.loadRememberedContacts();
    return store;
  }

  Future<void> ensureDirs() async {
    await root.create(recursive: true);
    await logosDir.create(recursive: true);
    await signaturesDir.create(recursive: true);
    await filledDir.create(recursive: true);
  }

  Future<int> _readLocalDataEpoch() async {
    if (!await dataEpochFile.exists()) return 0;
    try {
      final raw = jsonDecode(await dataEpochFile.readAsString());
      if (raw is Map && raw['epoch'] is num) {
        return (raw['epoch'] as num).toInt();
      }
      if (raw is num) return raw.toInt();
    } catch (_) {}
    return 0;
  }

  Future<void> _writeLocalDataEpoch(int epoch) async {
    await dataEpochFile.writeAsString(
      const JsonEncoder.withIndent('  ').convert({'epoch': epoch}),
    );
  }

  /// One-shot wipe when [localDataEpoch] advances (fresh cloud / fresh install).
  Future<bool> applyLocalDataEpochIfNeeded() async {
    final current = await _readLocalDataEpoch();
    if (current >= localDataEpoch) return false;
    await wipeLocalUserData();
    await _writeLocalDataEpoch(localDataEpoch);
    return true;
  }

  /// Erase local presets, logos, signatures, PDFs, remembered contacts, settings.
  Future<void> wipeLocalUserData() async {
    presets = {};
    signatures = [];
    rememberedContacts = [];

    Future<void> clearDir(Directory dir) async {
      if (!await dir.exists()) {
        await dir.create(recursive: true);
        return;
      }
      await for (final entity in dir.list()) {
        try {
          await entity.delete(recursive: true);
        } catch (_) {}
      }
      await dir.create(recursive: true);
    }

    await clearDir(logosDir);
    await clearDir(signaturesDir);
    await clearDir(filledDir);

    for (final f in [
      presetsFile,
      signaturesFile,
      rememberedContactsFile,
      settingsFile,
      updateScheduleFile,
    ]) {
      try {
        if (await f.exists()) await f.delete();
      } catch (_) {}
    }

    await presetsFile.writeAsString('{}\n');
    await signaturesFile.writeAsString(
      const JsonEncoder.withIndent('  ').convert({'signatures': <Object>[]}),
    );
    await rememberedContactsFile.writeAsString('[]\n');
  }

  Future<void> loadPresets() async {
    presets = {};
    if (!await presetsFile.exists()) return;
    var migrated = false;
    try {
      final data = jsonDecode(await presetsFile.readAsString()) as Map<String, dynamic>;
      final customers = data['customers'];
      if (customers is Map) {
        for (final entry in customers.entries) {
          final rawKey = entry.key.toString();
          final v = entry.value;
          if (v is! Map) continue;
          final json = v is Map<String, dynamic>
              ? v
              : Map<String, dynamic>.from(v);

          final legacy = !rawKey.contains('::');
          final kind = legacy
              ? LabelKind.shipping
              : presetKindFromStorageKey(rawKey);
          final displayName = legacy
              ? rawKey
              : presetDisplayNameFromStorageKey(rawKey);
          final storageKey = presetStorageKey(kind, displayName);

          if (legacy) migrated = true;
          presets[storageKey] = CustomerPreset.fromJson(
            displayName,
            json,
            defaultKind: kind,
          );
        }
      }
      if (migrated) {
        await savePresets();
      }
    } catch (_) {
      presets = {};
    }
  }

  Future<void> savePresets() async {
    final customers = <String, dynamic>{};
    for (final e in presets.entries) {
      customers[e.key] = e.value.toJson();
    }
    await presetsFile.writeAsString(
      const JsonEncoder.withIndent('  ').convert({'customers': customers}),
    );
  }

  DateTime get signaturesIndexModified {
    try {
      return signaturesFile.statSync().modified;
    } catch (_) {
      return DateTime.fromMillisecondsSinceEpoch(0);
    }
  }

  Future<void> loadSignatures() async {
    signatures = [];
    if (!await signaturesFile.exists()) return;
    try {
      final data =
          jsonDecode(await signaturesFile.readAsString()) as Map<String, dynamic>;
      final raw = data['signatures'];
      if (raw is! List) return;
      for (final item in raw) {
        if (item is! Map) continue;
        final sig = SavedSignature.fromJson(Map<String, dynamic>.from(item));
        if (sig.id.isNotEmpty && sig.fileName.isNotEmpty) {
          signatures.add(sig);
        }
      }
      signatures.sort((a, b) => a.name.compareTo(b.name));
    } catch (_) {
      signatures = [];
    }
  }

  Future<void> saveSignatures() async {
    await signaturesFile.writeAsString(
      const JsonEncoder.withIndent('  ').convert({
        'signatures': signatures.map((s) => s.toJson()).toList(),
      }),
    );
  }

  List<File> listLogos() {
    if (!logosDir.existsSync()) return [];
    const exts = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'};
    final files = logosDir
        .listSync()
        .whereType<File>()
        .where((f) => exts.contains(p.extension(f.path).toLowerCase()))
        .toList()
      ..sort((a, b) => p.basename(a.path).compareTo(p.basename(b.path)));
    return files;
  }

  /// Delete a stored customer logo raster and optional SVG sidecar.
  ///
  /// Also removes [fileName] from any presets that reference it.
  /// Returns true when the raster file was deleted.
  Future<bool> deleteStoredLogo(File file) async {
    final logosRoot = p.normalize(logosDir.path);
    final target = p.normalize(file.path);
    if (!p.isWithin(logosRoot, target)) return false;
    final fileName = p.basename(file.path);
    final svg = File(p.setExtension(file.path, '.svg'));
    try {
      if (await file.exists()) await file.delete();
      if (await svg.exists()) await svg.delete();
    } catch (_) {
      return false;
    }
    await _removeLogoFromPresets(fileName);
    return true;
  }

  Future<void> _removeLogoFromPresets(String fileName) async {
    var changed = false;
    for (final entry in presets.entries.toList()) {
      final preset = entry.value;
      if (!preset.logoFileNames.contains(fileName)) continue;
      presets[entry.key] = CustomerPreset(
        name: preset.name,
        kind: preset.kind,
        fields: preset.fields,
        logoFileNames:
            preset.logoFileNames.where((n) => n != fileName).toList(),
        // Logo list cleanup must not bump updatedAt — that would make every
        // touched preset look "newer" than Supabase and force-push clobber.
        updatedAt: preset.updatedAt,
      );
      changed = true;
    }
    if (changed) await savePresets();
  }

  Future<ImportLogoResult> importLogo(
    File source, {
    String? preferredName,
    LogoImportOptions? options,
    void Function(String)? onLog,
  }) async {
    final raw = await source.readAsBytes();
    return importLogoBytes(
      raw,
      preferredName: preferredName ?? p.basename(source.path),
      options: options,
      onLog: onLog,
    );
  }

  /// Save logo bytes into [logosDir], reusing an existing file when the mark
  /// already exists (name + size + visual scan).
  ///
  /// A colliding filename only gets `stem(1).png` when the incoming image is
  /// actually a different mark. Optional high-quality cleanup is the
  /// user-triggered "Perfect this logo" path ([LogoPerfectRestore]), not an
  /// automatic import-time restore.
  Future<ImportLogoResult> importLogoBytes(
    List<int> bytes, {
    required String preferredName,
    LogoImportOptions? options,
    void Function(String)? onLog,
  }) async {
    await logosDir.create(recursive: true);

    final importOptions = options ?? LogoImportOptions.standard();
    var working = Uint8List.fromList(bytes);

    if (importOptions.cropMode == LogoCropMode.manual &&
        importOptions.manualCropRect != null) {
      working = LogoImageProcessor.processWithOptions(
        working,
        LogoImportOptions.standard(
          removeBackground: false,
          cropMode: LogoCropMode.manual,
          manualCropRect: importOptions.manualCropRect,
        ),
      );
    }

    final finalBytes = LogoImageProcessor.processWithOptions(
      working,
      importOptions.cropMode == LogoCropMode.manual
          ? LogoImportOptions.standard(
              removeBackground: importOptions.removeBackground,
              cropMode: LogoCropMode.auto,
            )
          : importOptions,
    );

    final reused = LogoDedupe.findReusable(
      processedBytes: finalBytes,
      preferredName: preferredName,
      stored: listLogos(),
    );
    if (reused != null) {
      onLog?.call('reuse ${p.basename(reused.path)} (same mark already stored)');
      return ImportLogoResult(file: reused, reused: true);
    }

    var stem = p.basenameWithoutExtension(preferredName.trim());
    if (stem.isEmpty) stem = 'logo';
    stem = stem.replaceAll(RegExp(r'[^\w\- .]+'), '_');
    var dest = File(p.join(logosDir.path, '$stem.png'));
    if (await dest.exists()) {
      dest = LogoDedupe.nextUniqueDest(logosDir.path, stem);
      onLog?.call('new mark; wrote ${p.basename(dest.path)}');
    }

    await dest.writeAsBytes(finalBytes, flush: true);
    return ImportLogoResult(file: dest);
  }

  /// Collapse visually-identical copies in [logosDir] and retarget presets.
  Future<LogoLibraryDedupeReport> dedupeStoredLogos() async {
    final groups = LogoDedupe.planCleanup(listLogos());
    var deleted = 0;
    final kept = <String>[];
    final removed = <String>[];
    for (final group in groups) {
      final keepName = p.basename(group.keep.path);
      kept.add(keepName);
      await _remapLogoInPresets(
        group.delete.map((f) => p.basename(f.path)).toList(),
        keepName,
      );
      for (final file in group.delete) {
        final name = p.basename(file.path);
        try {
          if (await file.exists()) await file.delete();
          final svg = File(p.setExtension(file.path, '.svg'));
          if (await svg.exists()) await svg.delete();
          deleted++;
          removed.add(name);
        } catch (_) {}
      }
    }
    return LogoLibraryDedupeReport(
      groups: groups.length,
      deleted: deleted,
      kept: kept,
      removed: removed,
    );
  }

  Future<void> _remapLogoInPresets(
    List<String> fromNames,
    String toName,
  ) async {
    if (fromNames.isEmpty) return;
    final drop = fromNames.toSet();
    var changed = false;
    for (final entry in presets.entries.toList()) {
      final preset = entry.value;
      if (!preset.logoFileNames.any(drop.contains)) continue;
      final next = <String>[];
      for (final name in preset.logoFileNames) {
        final mapped = drop.contains(name) ? toName : name;
        if (!next.contains(mapped)) next.add(mapped);
      }
      presets[entry.key] = CustomerPreset(
        name: preset.name,
        kind: preset.kind,
        fields: preset.fields,
        logoFileNames: next,
        // Remap must not bump updatedAt (same reason as logo delete).
        updatedAt: preset.updatedAt,
      );
      changed = true;
    }
    if (changed) await savePresets();
  }

  /// Directory used for generated PDFs — custom override when set.

  Directory pdfOutputDir(AppUiSettings settings) {
    final custom = settings.pdfOutputDir?.trim();
    if (custom != null && custom.isNotEmpty) {
      return Directory(custom);
    }
    return filledDir;
  }

  /// Write PDF under the effective output directory, uniquifying with `(1)`,
  /// `(2)`, … if needed (no space before the parentheses).
  Future<File> writePdf(
    String fileName,
    List<int> bytes, {
    Directory? outputDir,
  }) async {
    return writeOutputFile(
      fileName,
      bytes,
      extension: '.pdf',
      outputDir: outputDir,
    );
  }

  /// Write a Word `.docx` (Bulk Labels) beside PDFs in the output folder.
  Future<File> writeDocx(
    String fileName,
    List<int> bytes, {
    Directory? outputDir,
  }) async {
    return writeOutputFile(
      fileName,
      bytes,
      extension: '.docx',
      outputDir: outputDir,
    );
  }

  /// Write bytes with [extension], uniquifying with `(1)`, `(2)`, … if needed.
  Future<File> writeOutputFile(
    String fileName,
    List<int> bytes, {
    required String extension,
    Directory? outputDir,
  }) async {
    final ext = extension.startsWith('.') ? extension : '.$extension';
    final dir = outputDir ?? filledDir;
    await dir.create(recursive: true);
    var base = p.basename(fileName.trim());
    if (!base.toLowerCase().endsWith(ext.toLowerCase())) {
      base = '${p.basenameWithoutExtension(base)}$ext';
    }
    // Allow letters, digits, dash, underscore, parentheses; strip other junk.
    base = base.replaceAll(RegExp(r'[^\w.\-()]+'), '');
    if (base.isEmpty || base == ext) {
      base = 'label$ext';
    }

    final stem = p.basenameWithoutExtension(base);
    var out = File(p.join(dir.path, '$stem$ext'));
    var n = 1;
    while (await out.exists()) {
      out = File(p.join(dir.path, '$stem($n)$ext'));
      n++;
    }
    await out.writeAsBytes(bytes, flush: true);
    return out;
  }

  String safeCustomerName(String text) {
    final t = text.trim().isEmpty ? 'customer' : text.trim();
    final cleaned = t.replaceAll(RegExp(r'[^\w\- ]+'), '').trim();
    return cleaned.isEmpty ? 'customer' : cleaned;
  }

  /// Compact token for `SL-` / `RL-` filenames (alphanumeric only).
  String compactFileToken(String text) {
    final cleaned = text.replaceAll(RegExp(r'[^A-Za-z0-9]+'), '');
    return cleaned;
  }

  static const logoSearchEngineKey = 'logoSearchEngine';

  Future<Map<String, dynamic>> _readSettingsMap() async {
    if (!await settingsFile.exists()) return {};
    try {
      final raw = jsonDecode(await settingsFile.readAsString());
      if (raw is Map<String, dynamic>) return Map<String, dynamic>.from(raw);
      if (raw is Map) return Map<String, dynamic>.from(raw);
    } catch (_) {}
    return {};
  }

  Future<void> _writeSettingsMap(Map<String, dynamic> data) async {
    await settingsFile.writeAsString(
      const JsonEncoder.withIndent('  ').convert(data),
    );
  }

  Future<void> loadRememberedContacts() async {
    rememberedContacts = [];
    if (!await rememberedContactsFile.exists()) return;
    try {
      final raw = jsonDecode(await rememberedContactsFile.readAsString());
      if (raw is! List) return;
      final seen = <String>{};
      final out = <String>[];
      for (final item in raw) {
        final name = '$item'.trim();
        if (name.isEmpty) continue;
        final key = name.toLowerCase();
        if (seen.contains(key)) continue;
        seen.add(key);
        out.add(name);
        if (out.length >= maxRememberedContacts) break;
      }
      rememberedContacts = out;
    } catch (_) {
      rememberedContacts = [];
    }
  }

  Future<void> saveRememberedContacts() async {
    await rememberedContactsFile.writeAsString(
      const JsonEncoder.withIndent('  ').convert(rememberedContacts),
    );
  }

  /// Remember a typed / selected contact name (most-recent first, local cache).
  /// Returns true when the in-memory list changed.
  Future<bool> rememberContact(String raw) async {
    final name = raw.trim();
    if (name.isEmpty) return false;
    final key = name.toLowerCase();
    final existingIdx =
        rememberedContacts.indexWhere((n) => n.toLowerCase() == key);
    if (existingIdx == 0) return false;
    final next = <String>[name];
    for (final n in rememberedContacts) {
      if (n.toLowerCase() == key) continue;
      next.add(n);
      if (next.length >= maxRememberedContacts) break;
    }
    rememberedContacts = next;
    await saveRememberedContacts();
    return true;
  }

  /// Wipe a single remembered contact name from the local cache.
  /// Returns true when the list changed.
  Future<bool> forgetContact(String raw) async {
    final name = raw.trim();
    if (name.isEmpty) return false;
    final key = name.toLowerCase();
    final next =
        rememberedContacts.where((n) => n.toLowerCase() != key).toList();
    if (next.length == rememberedContacts.length) return false;
    rememberedContacts = next;
    await saveRememberedContacts();
    return true;
  }

  /// Wipe local remembered contact cache (does not touch shared cloud store).
  Future<void> clearRememberedContacts() async {
    rememberedContacts = [];
    await saveRememberedContacts();
  }

  /// Merge local memory (recent first) with a roster list (deduped).
  List<String> contactSuggestions({List<String> roster = const []}) {
    final seen = <String>{};
    final out = <String>[];
    void addAll(Iterable<String> names) {
      for (final n in names) {
        final t = n.trim();
        if (t.isEmpty) continue;
        final k = t.toLowerCase();
        if (seen.contains(k)) continue;
        seen.add(k);
        out.add(t);
      }
    }

    addAll(rememberedContacts);
    addAll(roster);
    return out;
  }

  Future<AppUiSettings> loadUiSettings() async {
    final data = await _readSettingsMap();
    return AppUiSettings.fromJson(data);
  }

  Future<void> saveUiSettings(AppUiSettings settings) async {
    final data = await _readSettingsMap();
    data.addAll(settings.toJson());
    if (data['pdfOutputDir'] == null) data.remove('pdfOutputDir');
    await _writeSettingsMap(data);
  }

  Future<String?> loadLogoSearchEngine() async {
    final settings = await loadUiSettings();
    final value = settings.logoSearchEngine;
    return value != null && value.isNotEmpty ? value : null;
  }

  Future<void> saveLogoSearchEngine(String engineId) async {
    final settings = await loadUiSettings();
    await saveUiSettings(settings.copyWith(logoSearchEngine: engineId));
  }

  /// e.g. `SL-StrikeSO1223344.pdf`, `RL-…`, `BOL-…`, or `BOL-SL-…`
  String labelPdfBaseName({
    required LabelKind kind,
    required String customer,
    required String salesOrder,
    String? prefixOverride,
  }) {
    final prefix = prefixOverride ??
        switch (kind) {
          LabelKind.receiving => 'RL-',
          LabelKind.bol => 'BOL-',
          LabelKind.bulk => 'BL-',
          LabelKind.shipping => 'SL-',
        };
    final cust = compactFileToken(customer);
    final so = compactFileToken(salesOrder);
    final body = '$cust$so';
    if (body.isEmpty) return '${prefix}label.pdf';
    return '$prefix$body.pdf';
  }
}

/// Persisted UI / desktop preferences in [AppStorage.settingsFile].
class AppUiSettings {
  const AppUiSettings({
    this.logoSearchEngine,
    this.pdfOutputDir,
    this.autoOpenPdf = true,
    this.showWorkspacePane = true,
    this.preferExtendedRail = true,
    this.denseForms = false,
    this.showToolbarUpdate = true,
    this.autoUpdateEnabled = true,
    this.hotkeyOverrides = const {},
    this.themePreference = UiThemePreference.light,
    this.layoutPreset = UiLayoutPreset.classic,
    this.formColumns = 2,
    this.uiFontScale = 1.0,
    this.useOswaldFont = false,
    this.pdfOptions = PdfRenderOptions.defaults,
  });

  final String? logoSearchEngine;
  final String? pdfOutputDir;
  final bool autoOpenPdf;
  final bool showWorkspacePane;
  final bool preferExtendedRail;
  final bool denseForms;
  final bool showToolbarUpdate;
  final bool autoUpdateEnabled;
  final Map<String, String> hotkeyOverrides;
  final UiThemePreference themePreference;
  final UiLayoutPreset layoutPreset;
  final int formColumns;
  final double uiFontScale;
  /// When false (default), UI uses Helvetica; when true, Oswald.
  final bool useOswaldFont;
  final PdfRenderOptions pdfOptions;

  /// Active UI typeface for ThemeData / chrome widgets.
  String get uiFontFamily => useOswaldFont ? 'Oswald' : 'Helvetica';

  static const defaults = AppUiSettings();

  bool get isDark => themePreference == UiThemePreference.dark;

  factory AppUiSettings.fromJson(Map<String, dynamic> json) {
    final hotkeysRaw = json['hotkeyOverrides'];
    final hotkeys = <String, String>{};
    if (hotkeysRaw is Map) {
      for (final e in hotkeysRaw.entries) {
        final k = '${e.key}'.trim();
        final v = '${e.value}'.trim();
        if (k.isNotEmpty && v.isNotEmpty) hotkeys[k] = v;
      }
    }
    final cols = json['formColumns'];
    final formColumns = cols is int
        ? cols.clamp(1, 2)
        : (int.tryParse('$cols') ?? 2).clamp(1, 2);
    final fontScaleRaw = json['uiFontScale'];
    final uiFontScale = fontScaleRaw is num
        ? fontScaleRaw.toDouble().clamp(0.85, 1.35)
        : (double.tryParse('$fontScaleRaw') ?? 1.0).clamp(0.85, 1.35);
    return AppUiSettings(
      logoSearchEngine: json[AppStorage.logoSearchEngineKey] is String
          ? json[AppStorage.logoSearchEngineKey] as String
          : null,
      pdfOutputDir: json['pdfOutputDir'] is String
          ? (json['pdfOutputDir'] as String).trim()
          : null,
      autoOpenPdf: json['autoOpenPdf'] is bool
          ? json['autoOpenPdf'] as bool
          : true,
      showWorkspacePane: json['showWorkspacePane'] is bool
          ? json['showWorkspacePane'] as bool
          : true,
      preferExtendedRail: json['preferExtendedRail'] is bool
          ? json['preferExtendedRail'] as bool
          : true,
      denseForms:
          json['denseForms'] is bool ? json['denseForms'] as bool : false,
      showToolbarUpdate: json['showToolbarUpdate'] is bool
          ? json['showToolbarUpdate'] as bool
          : true,
      autoUpdateEnabled: json['autoUpdateEnabled'] is bool
          ? json['autoUpdateEnabled'] as bool
          : true,
      hotkeyOverrides: hotkeys,
      themePreference:
          UiThemePreference.tryParse('${json['themePreference']}') ??
              UiThemePreference.light,
      layoutPreset: UiLayoutPreset.tryParse('${json['layoutPreset']}') ??
          UiLayoutPreset.classic,
      formColumns: formColumns,
      uiFontScale: uiFontScale,
      useOswaldFont: json['useOswaldFont'] is bool
          ? json['useOswaldFont'] as bool
          : false,
      pdfOptions: PdfRenderOptions.fromJson(
        json['pdfOptions'] is Map<String, dynamic>
            ? json['pdfOptions'] as Map<String, dynamic>
            : (json['pdfOptions'] is Map
                ? Map<String, dynamic>.from(json['pdfOptions'] as Map)
                : null),
      ),
    );
  }

  Map<String, dynamic> toJson() => {
        AppStorage.logoSearchEngineKey: logoSearchEngine,
        'pdfOutputDir':
            (pdfOutputDir != null && pdfOutputDir!.trim().isNotEmpty)
                ? pdfOutputDir!.trim()
                : null,
        'autoOpenPdf': autoOpenPdf,
        'showWorkspacePane': showWorkspacePane,
        'preferExtendedRail': preferExtendedRail,
        'denseForms': denseForms,
        'showToolbarUpdate': showToolbarUpdate,
        'autoUpdateEnabled': autoUpdateEnabled,
        'hotkeyOverrides': hotkeyOverrides,
        'themePreference': themePreference.name,
        'layoutPreset': layoutPreset.name,
        'formColumns': formColumns,
        'uiFontScale': uiFontScale,
        'useOswaldFont': useOswaldFont,
        'pdfOptions': pdfOptions.toJson(),
      };

  AppUiSettings copyWith({
    String? logoSearchEngine,
    String? pdfOutputDir,
    bool clearPdfOutputDir = false,
    bool? autoOpenPdf,
    bool? showWorkspacePane,
    bool? preferExtendedRail,
    bool? denseForms,
    bool? showToolbarUpdate,
    bool? autoUpdateEnabled,
    Map<String, String>? hotkeyOverrides,
    UiThemePreference? themePreference,
    UiLayoutPreset? layoutPreset,
    int? formColumns,
    double? uiFontScale,
    bool? useOswaldFont,
    PdfRenderOptions? pdfOptions,
  }) {
    return AppUiSettings(
      logoSearchEngine: logoSearchEngine ?? this.logoSearchEngine,
      pdfOutputDir:
          clearPdfOutputDir ? null : (pdfOutputDir ?? this.pdfOutputDir),
      autoOpenPdf: autoOpenPdf ?? this.autoOpenPdf,
      showWorkspacePane: showWorkspacePane ?? this.showWorkspacePane,
      preferExtendedRail: preferExtendedRail ?? this.preferExtendedRail,
      denseForms: denseForms ?? this.denseForms,
      showToolbarUpdate: showToolbarUpdate ?? this.showToolbarUpdate,
      autoUpdateEnabled: autoUpdateEnabled ?? this.autoUpdateEnabled,
      hotkeyOverrides: hotkeyOverrides ?? this.hotkeyOverrides,
      themePreference: themePreference ?? this.themePreference,
      layoutPreset: layoutPreset ?? this.layoutPreset,
      formColumns: formColumns ?? this.formColumns,
      uiFontScale: uiFontScale ?? this.uiFontScale,
      useOswaldFont: useOswaldFont ?? this.useOswaldFont,
      pdfOptions: pdfOptions ?? this.pdfOptions,
    );
  }
}
