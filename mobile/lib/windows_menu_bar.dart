import 'dart:io';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:url_launcher/url_launcher.dart';

import 'app_config.dart';
import 'app_snack.dart';
import 'app_storage.dart';
import 'feedback_forms.dart';
import 'label_data.dart';
import 'logo_finder.dart';
import 'pdf_render_options.dart';
import 'platform_io.dart';
import 'theme.dart';
import 'update_sheet.dart';
import 'windows_customize.dart';
import 'windows_window_snap.dart';

/// Callbacks + state for the Windows-only [MenuBar].
class WindowsMenuActions {
  const WindowsMenuActions({
    required this.storage,
    required this.settings,
    required this.kind,
    required this.busy,
    required this.bolStoreCopy,
    required this.bolDriverCopy,
    required this.bolCustomerCopy,
    required this.onNewDocument,
    required this.onGenerate,
    required this.onClearShipment,
    required this.onClearAll,
    required this.onSavePreset,
    required this.onDeletePreset,
    required this.onSelectKind,
    required this.onBolCopyChanged,
    required this.onSettingsChanged,
    required this.onFindLogo,
    required this.onBrowseLogo,
    required this.onAddFromStorage,
    required this.onLoadSample,
  });

  final AppStorage storage;
  final AppUiSettings settings;
  final LabelKind kind;
  final bool busy;
  final bool bolStoreCopy;
  final bool bolDriverCopy;
  final bool bolCustomerCopy;

  final void Function(LabelKind kind) onNewDocument;
  final VoidCallback onGenerate;
  final VoidCallback onClearShipment;
  final VoidCallback onClearAll;
  final VoidCallback onSavePreset;
  final VoidCallback onDeletePreset;
  final ValueChanged<LabelKind> onSelectKind;
  final void Function({bool? store, bool? driver, bool? customer})
      onBolCopyChanged;
  final ValueChanged<AppUiSettings> onSettingsChanged;
  final VoidCallback onFindLogo;
  final VoidCallback onBrowseLogo;
  final VoidCallback onAddFromStorage;
  final VoidCallback onLoadSample;
}

/// Professional Windows menu bar (not used on Android).
class WindowsAppMenuBar extends StatelessWidget {
  const WindowsAppMenuBar({super.key, required this.actions});

  final WindowsMenuActions actions;

  Future<void> _persist(AppUiSettings next) async {
    await actions.storage.saveUiSettings(next);
    actions.onSettingsChanged(next);
  }

  Future<void> _toggleDark(BuildContext context) async {
    final nextPref = actions.settings.isDark
        ? UiThemePreference.light
        : UiThemePreference.dark;
    await _persist(actions.settings.copyWith(themePreference: nextPref));
    if (context.mounted) {
      await _snack(
        context,
        nextPref == UiThemePreference.dark
            ? 'Dark mode on'
            : 'Light mode on',
      );
    }
  }

  Future<void> _customize(BuildContext context) async {
    final next = await showWindowsCustomizeDialog(
      context,
      settings: actions.settings,
    );
    if (next == null) return;
    await _persist(next);
    if (context.mounted) {
      await _snack(context, 'Customization applied.');
    }
  }

  Future<void> _snap(BuildContext context, WindowsSnapPreset preset) async {
    final msg = await WindowsWindowSnap.apply(preset);
    if (context.mounted) await _snack(context, msg);
  }

  Future<void> _feedback(BuildContext context) async {
    PackageInfo? info;
    try {
      info = await PackageInfo.fromPlatform();
    } catch (_) {}
    if (!context.mounted) return;
    await openFeedbackForm(
      context,
      installedVersion: info == null
          ? 'unknown'
          : '${info.version}+${info.buildNumber}',
    );
  }

  Future<void> _errorCapture(BuildContext context) async {
    PackageInfo? info;
    try {
      info = await PackageInfo.fromPlatform();
    } catch (_) {}
    if (!context.mounted) return;
    await openErrorCaptureForm(
      context,
      installedVersion: info == null
          ? 'unknown'
          : '${info.version}+${info.buildNumber}',
      title: 'Error capture (F2)',
    );
  }

  Future<void> _snack(BuildContext context, String message) async {
    showAppSnack(context, message);
  }

  Future<void> _openPath(BuildContext context, String path) async {
    final dir = Directory(path);
    if (!await dir.exists()) {
      try {
        await dir.create(recursive: true);
      } catch (e) {
        if (context.mounted) await _snack(context, 'Could not open: $e');
        return;
      }
    }
    await openFolder(path);
  }

  Future<void> _choosePdfOutput(BuildContext context) async {
    final selected = await getDirectoryPath(
      confirmButtonText: 'Use folder',
    );
    if (selected == null || selected.isEmpty) return;
    final next = actions.settings.copyWith(pdfOutputDir: selected);
    await _persist(next);
    if (context.mounted) {
      await _snack(context, 'PDF output folder:\n$selected');
    }
  }

  Future<void> _resetPdfOutput(BuildContext context) async {
    final next = actions.settings.copyWith(clearPdfOutputDir: true);
    await _persist(next);
    if (context.mounted) {
      await _snack(
        context,
        'PDF output reset to:\n${actions.storage.filledDir.path}',
      );
    }
  }

  Future<void> _pickLogoEngine(BuildContext context) async {
    final retool = AppConfig.retoolClearbitLogoUrl.trim().isNotEmpty;
    final options = LogoSearchEngine.pickerOptions(retoolConfigured: retool);
    final current = LogoSearchEngine.tryParse(actions.settings.logoSearchEngine);
    final picked = await showDialog<LogoSearchEngine>(
      context: context,
      builder: (ctx) => SimpleDialog(
        title: const Text('Logo search engine'),
        children: [
          for (final e in options)
            SimpleDialogOption(
              onPressed: () => Navigator.of(ctx).pop(e),
              child: Row(
                children: [
                  Icon(
                    (current ?? LogoSearchEngine.defaultEngine) == e
                        ? Icons.radio_button_checked
                        : Icons.radio_button_off,
                    size: 18,
                    color: SwiftColors.accent,
                  ),
                  const SizedBox(width: 10),
                  Text(e.label),
                ],
              ),
            ),
        ],
      ),
    );
    if (picked == null) return;
    final next = actions.settings.copyWith(logoSearchEngine: picked.name);
    await _persist(next);
    await actions.storage.saveLogoSearchEngine(picked.name);
    if (context.mounted) {
      await _snack(context, 'Logo search: ${picked.label}');
    }
  }

  Future<void> _showHotkeys(BuildContext context) async {
    final overrides = Map<String, String>.from(actions.settings.hotkeyOverrides);
    const defaults = <String, String>{
      'generate': 'Ctrl+Enter',
      'shipping': 'Ctrl+1',
      'receiving': 'Ctrl+2',
      'bol': 'Ctrl+3',
      'savePreset': 'Ctrl+S',
      'clearShipment': 'Ctrl+Shift+K',
      'newShipping': 'Ctrl+N',
      'checkUpdates': 'Ctrl+Shift+U',
    };
    await showDialog<void>(
      context: context,
      builder: (ctx) {
        return StatefulBuilder(
          builder: (ctx, setLocal) {
            return AlertDialog(
              title: const Text('Hotkeys'),
              content: SizedBox(
                width: 420,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    for (final e in defaults.entries)
                      ListTile(
                        dense: true,
                        title: Text(_hotkeyLabel(e.key)),
                        subtitle: Text(overrides[e.key] ?? e.value),
                        trailing: TextButton(
                          onPressed: () async {
                            final custom = await _askHotkey(
                              ctx,
                              _hotkeyLabel(e.key),
                              overrides[e.key] ?? e.value,
                            );
                            if (custom == null) return;
                            setLocal(() {
                              if (custom.trim().isEmpty) {
                                overrides.remove(e.key);
                              } else {
                                overrides[e.key] = custom.trim();
                              }
                            });
                          },
                          child: const Text('Assign'),
                        ),
                      ),
                  ],
                ),
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.of(ctx).pop(),
                  child: const Text('Cancel'),
                ),
                FilledButton(
                  onPressed: () async {
                    final next =
                        actions.settings.copyWith(hotkeyOverrides: overrides);
                    await _persist(next);
                    if (ctx.mounted) Navigator.of(ctx).pop();
                  },
                  child: const Text('Save'),
                ),
              ],
            );
          },
        );
      },
    );
  }

  String _hotkeyLabel(String key) => switch (key) {
        'generate' => 'Generate PDF',
        'shipping' => 'Shipping document',
        'receiving' => 'Receiving document',
        'bol' => 'Bill of Lading',
        'savePreset' => 'Save preset',
        'clearShipment' => 'Clear shipment',
        'newShipping' => 'New Shipping',
        'checkUpdates' => 'Check for Updates',
        _ => key,
      };

  Future<String?> _askHotkey(
    BuildContext context,
    String title,
    String initial,
  ) async {
    final ctrl = TextEditingController(text: initial);
    try {
      return await showDialog<String>(
        context: context,
        builder: (ctx) => StatefulBuilder(
          builder: (ctx, setLocal) {
            final text = ctrl.text.trim();
            final valid = text.isEmpty || parseHotkeyString(text) != null;
            return AlertDialog(
              title: Text('Assign: $title'),
              content: TextField(
                controller: ctrl,
                autofocus: true,
                onChanged: (_) => setLocal(() {}),
                decoration: InputDecoration(
                  hintText: 'e.g. Ctrl+Shift+G',
                  helperText: 'Ctrl/Shift/Alt + a letter, digit, or key name '
                      '(Enter, F2, Space…), joined with +. Takes effect on Save.',
                  errorText:
                      valid ? null : 'Not recognized — try e.g. Ctrl+Shift+G',
                ),
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.of(ctx).pop(),
                  child: const Text('Cancel'),
                ),
                TextButton(
                  onPressed: () => Navigator.of(ctx).pop(''),
                  child: const Text('Reset'),
                ),
                FilledButton(
                  onPressed:
                      valid ? () => Navigator.of(ctx).pop(ctrl.text) : null,
                  child: const Text('OK'),
                ),
              ],
            );
          },
        ),
      );
    } finally {
      ctrl.dispose();
    }
  }

  Future<void> _showAbout(BuildContext context) async {
    final info = await PackageInfo.fromPlatform();
    if (!context.mounted) return;
    showAboutDialog(
      context: context,
      applicationName: 'Swift Document Generator',
      applicationVersion: '${info.version}+${info.buildNumber}',
      applicationLegalese: 'Swift Oilfield Supply',
      children: [
        const SizedBox(height: 12),
        Text(
          'Shipping, Receiving, and Bill of Lading for Windows and Android.\n'
          'Releases: ${AppConfig.githubReleasesPage}',
        ),
      ],
    );
  }

  Future<void> _showDiagnostics(BuildContext context) async {
    final info = await PackageInfo.fromPlatform();
    final s = actions.settings;
    final report = StringBuffer()
      ..writeln('Swift Document Generator ${info.version}+${info.buildNumber}')
      ..writeln('Platform: ${Platform.operatingSystem}')
      ..writeln('Data root: ${actions.storage.root.path}')
      ..writeln('Logos: ${actions.storage.logosDir.path}')
      ..writeln(
        'PDF out: ${actions.storage.pdfOutputDir(s).path}',
      )
      ..writeln('Presets: ${actions.storage.presetsFile.path}')
      ..writeln()
      ..writeln(
        'Logo restore: vectorize → Real-ESRGAN → cubic; '
        'Gemini opt-in (LOGO_RESTORE_USE_GEMINI=1)',
      )
      ..writeln()
      ..writeln('Workspace pane: ${s.showWorkspacePane}')
      ..writeln('Extended rail: ${s.preferExtendedRail}')
      ..writeln('Dense forms: ${s.denseForms}')
      ..writeln('Toolbar Update: ${s.showToolbarUpdate}')
      ..writeln('Auto-open PDF: ${s.autoOpenPdf}')
      ..writeln('Auto-update: ${s.autoUpdateEnabled}');
    if (!context.mounted) return;
    await showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Diagnostics'),
        content: SizedBox(
          width: 480,
          child: SelectableText(report.toString()),
        ),
        actions: [
          TextButton(
            onPressed: () async {
              await Clipboard.setData(ClipboardData(text: report.toString()));
              if (ctx.mounted) Navigator.of(ctx).pop();
            },
            child: const Text('Copy'),
          ),
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Close'),
          ),
        ],
      ),
    );
  }

  Future<void> _resetSettings(BuildContext context) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Reset settings?'),
        content: const Text(
          'Restores default desktop preferences (PDF folder, view options, '
          'hotkey labels). Presets and logos are not deleted.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Reset'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    await _persist(AppUiSettings.defaults);
    if (context.mounted) await _snack(context, 'Settings reset to defaults.');
  }

  @override
  Widget build(BuildContext context) {
    final s = actions.settings;
    final pdfDir = actions.storage.pdfOutputDir(s);
    final chrome = SwiftChromeColors.of(context);
    final dark = Theme.of(context).brightness == Brightness.dark;

    final barStyle = MenuStyle(
      backgroundColor: WidgetStatePropertyAll(chrome.surface),
      elevation: const WidgetStatePropertyAll(0),
      padding:
          const WidgetStatePropertyAll(EdgeInsets.symmetric(horizontal: 4)),
      surfaceTintColor: const WidgetStatePropertyAll(Colors.transparent),
      shadowColor: const WidgetStatePropertyAll(Colors.transparent),
    );
    final panelStyle = MenuStyle(
      backgroundColor: WidgetStatePropertyAll(chrome.surface),
      surfaceTintColor: const WidgetStatePropertyAll(Colors.transparent),
      shadowColor: WidgetStatePropertyAll(
        Colors.black.withValues(alpha: dark ? 0.55 : 0.22),
      ),
      elevation: const WidgetStatePropertyAll(8),
      padding: const WidgetStatePropertyAll(EdgeInsets.symmetric(vertical: 6)),
      shape: WidgetStatePropertyAll(
        RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(6),
          side: BorderSide(color: chrome.border),
        ),
      ),
    );
    final itemStyle = ButtonStyle(
      foregroundColor: WidgetStateProperty.resolveWith((states) {
        if (states.contains(WidgetState.disabled)) {
          return chrome.muted.withValues(alpha: 0.55);
        }
        return chrome.ink;
      }),
      iconColor: WidgetStateProperty.resolveWith((states) {
        if (states.contains(WidgetState.disabled)) {
          return chrome.muted.withValues(alpha: 0.55);
        }
        return chrome.ink;
      }),
      backgroundColor: WidgetStateProperty.resolveWith((states) {
        if (states.contains(WidgetState.hovered) ||
            states.contains(WidgetState.focused) ||
            states.contains(WidgetState.selected)) {
          return chrome.accentSoft;
        }
        return chrome.surface;
      }),
      overlayColor: const WidgetStatePropertyAll(Colors.transparent),
      textStyle: WidgetStatePropertyAll(
        TextStyle(
          fontFamily: 'Calibri',
          fontSize: 13,
          fontWeight: FontWeight.w600,
          color: chrome.ink,
        ),
      ),
    );
    final scheme = Theme.of(context).colorScheme.copyWith(
          surface: chrome.surface,
          onSurface: chrome.ink,
          onSurfaceVariant: chrome.muted,
          outline: chrome.border,
          surfaceContainerLowest: chrome.bg,
          surfaceContainerLow: chrome.surface,
          surfaceContainer: chrome.surface,
          surfaceContainerHigh: chrome.elevated,
          surfaceContainerHighest: chrome.elevated,
        );

    return Theme(
      data: Theme.of(context).copyWith(
        colorScheme: scheme,
        canvasColor: chrome.surface,
        menuTheme: MenuThemeData(style: panelStyle),
        menuBarTheme: MenuBarThemeData(style: barStyle),
        menuButtonTheme: MenuButtonThemeData(style: itemStyle),
        dividerTheme: DividerThemeData(
          color: chrome.border,
          space: 1,
          thickness: 1,
        ),
      ),
      child: DefaultTextStyle(
        style: TextStyle(
          fontFamily: 'Calibri',
          fontSize: 13,
          fontWeight: FontWeight.w600,
          color: chrome.ink,
        ),
        child: IconTheme(
          data: IconThemeData(color: chrome.ink, size: 18),
          child: MenuBar(
            style: barStyle,
            children: _darkSafeMenuChildren(
              panelStyle: panelStyle,
              itemStyle: itemStyle,
              children: [
        SubmenuButton(
          menuChildren: [
            MenuItemButton(
              onPressed: () => actions.onNewDocument(LabelKind.shipping),
              shortcut: const SingleActivator(LogicalKeyboardKey.keyN, control: true),
              child: const Text('New Shipping'),
            ),
            MenuItemButton(
              onPressed: () => actions.onNewDocument(LabelKind.receiving),
              child: const Text('New Receiving'),
            ),
            MenuItemButton(
              onPressed: () => actions.onNewDocument(LabelKind.bol),
              child: const Text('New Bill of Lading'),
            ),
            MenuItemButton(
              onPressed: () => actions.onNewDocument(LabelKind.bulk),
              child: const Text('New Bulk Labels'),
            ),
            const Divider(),
            MenuItemButton(
              onPressed: actions.busy ? null : actions.onGenerate,
              shortcut:
                  const SingleActivator(LogicalKeyboardKey.enter, control: true),
              child: Text(
                actions.kind == LabelKind.bulk ? 'Generate' : 'Generate PDF',
              ),
            ),
            const Divider(),
            MenuItemButton(
              onPressed: () => _openPath(context, pdfDir.path),
              child: const Text('Open PDF output folder'),
            ),
            MenuItemButton(
              onPressed: () => _openPath(context, actions.storage.logosDir.path),
              child: const Text('Open logos folder'),
            ),
            MenuItemButton(
              onPressed: () async {
                final f = actions.storage.presetsFile;
                if (!await f.exists()) await actions.storage.savePresets();
                await launchUrl(Uri.file(f.path));
              },
              child: const Text('Open presets file'),
            ),
            const Divider(),
            MenuItemButton(
              onPressed: () => _choosePdfOutput(context),
              child: const Text('Choose PDF output folder…'),
            ),
            MenuItemButton(
              onPressed: () => _resetPdfOutput(context),
              child: const Text('Reset output folder'),
            ),
            const Divider(),
            MenuItemButton(
              onPressed: () => showUpdateFlow(context),
              shortcut: const SingleActivator(
                LogicalKeyboardKey.keyU,
                control: true,
                shift: true,
              ),
              child: const Text('Check for Updates'),
            ),
            MenuItemButton(
              onPressed: () => exit(0),
              child: const Text('Exit'),
            ),
          ],
          child: const Text('File'),
        ),
        SubmenuButton(
          menuChildren: [
            MenuItemButton(
              onPressed: actions.onClearShipment,
              shortcut: const SingleActivator(
                LogicalKeyboardKey.keyK,
                control: true,
                shift: true,
              ),
              child: const Text('Clear shipment'),
            ),
            MenuItemButton(
              onPressed: actions.onClearAll,
              child: const Text('Clear all'),
            ),
            const Divider(),
            MenuItemButton(
              onPressed: actions.onSavePreset,
              shortcut:
                  const SingleActivator(LogicalKeyboardKey.keyS, control: true),
              child: const Text('Save preset'),
            ),
            MenuItemButton(
              onPressed: actions.onDeletePreset,
              child: const Text('Delete preset'),
            ),
            const Divider(),
            MenuItemButton(
              onPressed: actions.onLoadSample,
              child: const Text('Load sample'),
            ),
          ],
          child: const Text('Edit'),
        ),
        SubmenuButton(
          menuChildren: [
            CheckboxMenuButton(
              value: s.isDark,
              onChanged: (_) => _toggleDark(context),
              child: const Text('Dark mode'),
            ),
            CheckboxMenuButton(
              value: s.useOswaldFont,
              onChanged: (v) => _persist(
                s.copyWith(useOswaldFont: v ?? false),
              ),
              child: const Text('Use Oswald font (default: Helvetica)'),
            ),
            MenuItemButton(
              onPressed: () => _customize(context),
              child: const Text('Customize appearance & PDF…'),
            ),
            const Divider(),
            SubmenuButton(
              menuChildren: [
                for (final p in UiLayoutPreset.values)
                  MenuItemButton(
                    onPressed: () => _persist(
                      s.copyWith(
                        layoutPreset: p,
                        showWorkspacePane: p != UiLayoutPreset.compact,
                        preferExtendedRail: p == UiLayoutPreset.widescreen,
                        denseForms: p == UiLayoutPreset.compact,
                        formColumns: p == UiLayoutPreset.compact ? 1 : 2,
                      ),
                    ),
                    child: Text(
                      s.layoutPreset == p ? '● ${p.label}' : p.label,
                    ),
                  ),
              ],
              child: const Text('Layout presets'),
            ),
            SubmenuButton(
              menuChildren: [
                for (final snap in WindowsSnapPreset.values)
                  MenuItemButton(
                    onPressed: () => _snap(context, snap),
                    child: Text(snap.label),
                  ),
              ],
              child: const Text('Window snap'),
            ),
            const Divider(),
            CheckboxMenuButton(
              value: s.showWorkspacePane,
              onChanged: (v) => _persist(
                s.copyWith(showWorkspacePane: v ?? true),
              ),
              child: const Text('Workspace pane'),
            ),
            CheckboxMenuButton(
              value: s.preferExtendedRail,
              onChanged: (v) => _persist(
                s.copyWith(preferExtendedRail: v ?? true),
              ),
              child: const Text('Extended navigation rail'),
            ),
            CheckboxMenuButton(
              value: s.denseForms,
              onChanged: (v) => _persist(
                s.copyWith(denseForms: v ?? false),
              ),
              child: const Text('Dense forms'),
            ),
            CheckboxMenuButton(
              value: s.showToolbarUpdate,
              onChanged: (v) => _persist(
                s.copyWith(showToolbarUpdate: v ?? true),
              ),
              child: const Text('Toolbar Update button'),
            ),
            const Divider(),
            MenuItemButton(
              onPressed: () => _persist(s.copyWith(formColumns: 1)),
              child: Text(
                s.formColumns == 1 ? '● Single-column forms' : 'Single-column forms',
              ),
            ),
            MenuItemButton(
              onPressed: () => _persist(s.copyWith(formColumns: 2)),
              child: Text(
                s.formColumns == 2 ? '● Two-column forms' : 'Two-column forms',
              ),
            ),
          ],
          child: const Text('View'),
        ),        SubmenuButton(
          menuChildren: [
            MenuItemButton(
              onPressed: () => actions.onSelectKind(LabelKind.shipping),
              shortcut:
                  const SingleActivator(LogicalKeyboardKey.digit1, control: true),
              child: Text(
                actions.kind == LabelKind.shipping
                    ? '● Shipping Label'
                    : 'Shipping Label',
              ),
            ),
            MenuItemButton(
              onPressed: () => actions.onSelectKind(LabelKind.receiving),
              shortcut:
                  const SingleActivator(LogicalKeyboardKey.digit2, control: true),
              child: Text(
                actions.kind == LabelKind.receiving
                    ? '● Receiving Label'
                    : 'Receiving Label',
              ),
            ),
            MenuItemButton(
              onPressed: () => actions.onSelectKind(LabelKind.bol),
              shortcut:
                  const SingleActivator(LogicalKeyboardKey.digit3, control: true),
              child: Text(
                actions.kind == LabelKind.bol
                    ? '● Bill of Lading'
                    : 'Bill of Lading',
              ),
            ),
            MenuItemButton(
              onPressed: () => actions.onSelectKind(LabelKind.bulk),
              shortcut:
                  const SingleActivator(LogicalKeyboardKey.digit4, control: true),
              child: Text(
                actions.kind == LabelKind.bulk
                    ? '● Bulk Labels'
                    : 'Bulk Labels',
              ),
            ),
            const Divider(),
            CheckboxMenuButton(
              value: actions.bolStoreCopy,
              onChanged: actions.kind == LabelKind.bol
                  ? (v) => actions.onBolCopyChanged(store: v ?? false)
                  : null,
              child: const Text('BOL Store copy'),
            ),
            CheckboxMenuButton(
              value: actions.bolDriverCopy,
              onChanged: actions.kind == LabelKind.bol
                  ? (v) => actions.onBolCopyChanged(driver: v ?? false)
                  : null,
              child: const Text('BOL Driver copy'),
            ),
            CheckboxMenuButton(
              value: actions.bolCustomerCopy,
              onChanged: actions.kind == LabelKind.bol
                  ? (v) => actions.onBolCopyChanged(customer: v ?? false)
                  : null,
              child: const Text('BOL Customer copy'),
            ),
          ],
          child: const Text('Document'),
        ),
        SubmenuButton(
          menuChildren: [
            MenuItemButton(
              onPressed: () => _pickLogoEngine(context),
              child: const Text('Logo search engine…'),
            ),
            MenuItemButton(
              onPressed: actions.onFindLogo,
              shortcut: const SingleActivator(
                LogicalKeyboardKey.keyF,
                control: true,
                shift: true,
              ),
              child: const Text('Find logo on web…'),
            ),
            MenuItemButton(
              onPressed: actions.onBrowseLogo,
              child: const Text('Upload / browse logo…'),
            ),
            MenuItemButton(
              onPressed: actions.onAddFromStorage,
              child: const Text('Add logo from storage…'),
            ),
          ],
          child: const Text('Tools'),
        ),
        SubmenuButton(
          menuChildren: [
            MenuItemButton(
              onPressed: () => _choosePdfOutput(context),
              child: const Text('PDF output path…'),
            ),
            CheckboxMenuButton(
              value: s.autoOpenPdf,
              onChanged: (v) => _persist(
                s.copyWith(autoOpenPdf: v ?? true),
              ),
              child: const Text('Auto-open PDF after generate'),
            ),
            MenuItemButton(
              onPressed: () => _customize(context),
              child: const Text('Customize appearance & PDF…'),
            ),
            const Divider(),
            MenuItemButton(
              onPressed: () => _showHotkeys(context),
              child: const Text('Hotkeys…'),
            ),
            CheckboxMenuButton(
              value: s.autoUpdateEnabled,
              onChanged: (v) => _persist(
                s.copyWith(autoUpdateEnabled: v ?? true),
              ),
              child: const Text('Daily auto-update checks'),
            ),
            const Divider(),
            MenuItemButton(
              onPressed: () => _resetSettings(context),
              child: const Text('Reset settings…'),
            ),
          ],
          child: const Text('Options'),
        ),
        SubmenuButton(
          menuChildren: [
            MenuItemButton(
              onPressed: () => _showAbout(context),
              child: const Text('About'),
            ),
            MenuItemButton(
              onPressed: () => launchUrl(
                Uri.parse(AppConfig.githubReleasesPage),
                mode: LaunchMode.externalApplication,
              ),
              child: const Text('Releases'),
            ),
            MenuItemButton(
              onPressed: () => _showDiagnostics(context),
              child: const Text('Diagnostics report…'),
            ),
            const Divider(),
            MenuItemButton(
              onPressed: () => _feedback(context),
              child: const Text('Send feedback…'),
            ),
            MenuItemButton(
              onPressed: () => _errorCapture(context),
              shortcut: const SingleActivator(LogicalKeyboardKey.f2),
              child: const Text('Error capture (F2)…'),
            ),
          ],
          child: const Text('Help'),
        ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// Force dark/light chrome onto MenuBar overlays (panels render in [Overlay]).
List<Widget> _darkSafeMenuChildren({
  required MenuStyle panelStyle,
  required ButtonStyle itemStyle,
  required List<Widget> children,
}) {
  return [
    for (final child in children)
      _darkSafeMenuChild(
        panelStyle: panelStyle,
        itemStyle: itemStyle,
        child: child,
      ),
  ];
}

Widget _darkSafeMenuChild({
  required MenuStyle panelStyle,
  required ButtonStyle itemStyle,
  required Widget child,
}) {
  if (child is SubmenuButton) {
    return SubmenuButton(
      style: itemStyle,
      menuStyle: panelStyle,
      menuChildren: _darkSafeMenuChildren(
        panelStyle: panelStyle,
        itemStyle: itemStyle,
        children: child.menuChildren,
      ),
      child: child.child,
    );
  }
  if (child is MenuItemButton) {
    return MenuItemButton(
      style: itemStyle,
      onPressed: child.onPressed,
      shortcut: child.shortcut,
      child: child.child,
    );
  }
  if (child is CheckboxMenuButton) {
    return CheckboxMenuButton(
      style: itemStyle,
      value: child.value,
      onChanged: child.onChanged,
      child: child.child,
    );
  }
  return child;
}

/// Parses a user-typed hotkey string like `"Ctrl+Shift+G"` into an activator.
/// Returns null when the text isn't recognized — callers should keep the
/// previous binding rather than silently dropping it.
SingleActivator? parseHotkeyString(String raw) {
  final tokens = raw
      .split('+')
      .map((p) => p.trim())
      .where((p) => p.isNotEmpty)
      .toList();
  if (tokens.isEmpty) return null;
  var control = false, shift = false, alt = false, meta = false;
  LogicalKeyboardKey? key;
  for (final token in tokens) {
    switch (token.toLowerCase()) {
      case 'ctrl':
      case 'control':
        control = true;
      case 'shift':
        shift = true;
      case 'alt':
      case 'option':
        alt = true;
      case 'cmd':
      case 'meta':
      case 'win':
      case 'windows':
        meta = true;
      case final t:
        final found = _hotkeyToken(t);
        if (found == null) return null;
        key = found;
    }
  }
  if (key == null) return null;
  return SingleActivator(key, control: control, shift: shift, alt: alt, meta: meta);
}

const _hotkeyLetterKeys = {
  'a': LogicalKeyboardKey.keyA, 'b': LogicalKeyboardKey.keyB,
  'c': LogicalKeyboardKey.keyC, 'd': LogicalKeyboardKey.keyD,
  'e': LogicalKeyboardKey.keyE, 'f': LogicalKeyboardKey.keyF,
  'g': LogicalKeyboardKey.keyG, 'h': LogicalKeyboardKey.keyH,
  'i': LogicalKeyboardKey.keyI, 'j': LogicalKeyboardKey.keyJ,
  'k': LogicalKeyboardKey.keyK, 'l': LogicalKeyboardKey.keyL,
  'm': LogicalKeyboardKey.keyM, 'n': LogicalKeyboardKey.keyN,
  'o': LogicalKeyboardKey.keyO, 'p': LogicalKeyboardKey.keyP,
  'q': LogicalKeyboardKey.keyQ, 'r': LogicalKeyboardKey.keyR,
  's': LogicalKeyboardKey.keyS, 't': LogicalKeyboardKey.keyT,
  'u': LogicalKeyboardKey.keyU, 'v': LogicalKeyboardKey.keyV,
  'w': LogicalKeyboardKey.keyW, 'x': LogicalKeyboardKey.keyX,
  'y': LogicalKeyboardKey.keyY, 'z': LogicalKeyboardKey.keyZ,
};

const _hotkeyDigitKeys = {
  '0': LogicalKeyboardKey.digit0, '1': LogicalKeyboardKey.digit1,
  '2': LogicalKeyboardKey.digit2, '3': LogicalKeyboardKey.digit3,
  '4': LogicalKeyboardKey.digit4, '5': LogicalKeyboardKey.digit5,
  '6': LogicalKeyboardKey.digit6, '7': LogicalKeyboardKey.digit7,
  '8': LogicalKeyboardKey.digit8, '9': LogicalKeyboardKey.digit9,
};

const _hotkeyNamedKeys = {
  'enter': LogicalKeyboardKey.enter,
  'return': LogicalKeyboardKey.enter,
  'space': LogicalKeyboardKey.space,
  'tab': LogicalKeyboardKey.tab,
  'esc': LogicalKeyboardKey.escape,
  'escape': LogicalKeyboardKey.escape,
  'delete': LogicalKeyboardKey.delete,
  'del': LogicalKeyboardKey.delete,
  'backspace': LogicalKeyboardKey.backspace,
  'up': LogicalKeyboardKey.arrowUp,
  'down': LogicalKeyboardKey.arrowDown,
  'left': LogicalKeyboardKey.arrowLeft,
  'right': LogicalKeyboardKey.arrowRight,
  'f1': LogicalKeyboardKey.f1, 'f2': LogicalKeyboardKey.f2,
  'f3': LogicalKeyboardKey.f3, 'f4': LogicalKeyboardKey.f4,
  'f5': LogicalKeyboardKey.f5, 'f6': LogicalKeyboardKey.f6,
  'f7': LogicalKeyboardKey.f7, 'f8': LogicalKeyboardKey.f8,
  'f9': LogicalKeyboardKey.f9, 'f10': LogicalKeyboardKey.f10,
  'f11': LogicalKeyboardKey.f11, 'f12': LogicalKeyboardKey.f12,
};

LogicalKeyboardKey? _hotkeyToken(String token) {
  if (token.length == 1) return _hotkeyLetterKeys[token] ?? _hotkeyDigitKeys[token];
  return _hotkeyNamedKeys[token];
}

/// Built-in Windows CallbackShortcuts (MenuBar shortcuts alone are not enough).
///
/// [overrides] (from Options → Hotkeys → Assign) replace the default combo
/// for the keys it covers ('generate', 'shipping', 'receiving', 'bol',
/// 'savePreset', 'clearShipment', 'newShipping', 'checkUpdates') when the
/// saved text parses; unparsed or missing entries keep the default.
Map<ShortcutActivator, VoidCallback> windowsShortcutMap({
  required VoidCallback onGenerate,
  required VoidCallback onShipping,
  required VoidCallback onReceiving,
  required VoidCallback onBol,
  VoidCallback? onBulk,
  required VoidCallback onSavePreset,
  required VoidCallback onClearShipment,
  required VoidCallback onNewShipping,
  required VoidCallback onCheckUpdates,
  required VoidCallback onErrorCapture,
  VoidCallback? onToggleDark,
  VoidCallback? onFindLogo,
  Map<String, String> overrides = const {},
}) {
  ShortcutActivator activatorFor(String key, SingleActivator fallback) {
    final custom = overrides[key];
    if (custom == null || custom.trim().isEmpty) return fallback;
    return parseHotkeyString(custom) ?? fallback;
  }

  return {
    activatorFor(
      'generate',
      const SingleActivator(LogicalKeyboardKey.enter, control: true),
    ): onGenerate,
    activatorFor(
      'shipping',
      const SingleActivator(LogicalKeyboardKey.digit1, control: true),
    ): onShipping,
    activatorFor(
      'receiving',
      const SingleActivator(LogicalKeyboardKey.digit2, control: true),
    ): onReceiving,
    activatorFor(
      'bol',
      const SingleActivator(LogicalKeyboardKey.digit3, control: true),
    ): onBol,
    const SingleActivator(LogicalKeyboardKey.digit4, control: true): ?onBulk,
    activatorFor(
      'savePreset',
      const SingleActivator(LogicalKeyboardKey.keyS, control: true),
    ): onSavePreset,
    activatorFor(
      'clearShipment',
      const SingleActivator(LogicalKeyboardKey.keyK, control: true, shift: true),
    ): onClearShipment,
    activatorFor(
      'newShipping',
      const SingleActivator(LogicalKeyboardKey.keyN, control: true),
    ): onNewShipping,
    activatorFor(
      'checkUpdates',
      const SingleActivator(LogicalKeyboardKey.keyU, control: true, shift: true),
    ): onCheckUpdates,
    const SingleActivator(LogicalKeyboardKey.f2): onErrorCapture,
    const SingleActivator(LogicalKeyboardKey.keyD, control: true, shift: true):
        ?onToggleDark,
    const SingleActivator(
      LogicalKeyboardKey.keyF,
      control: true,
      shift: true,
    ): ?onFindLogo,
  };
}
