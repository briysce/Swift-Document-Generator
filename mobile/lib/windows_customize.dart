import 'package:flutter/material.dart';

import 'app_storage.dart';
import 'pdf_render_options.dart';
import 'theme.dart';

/// Windows Options → Customize appearance & PDF output.
Future<AppUiSettings?> showWindowsCustomizeDialog(
  BuildContext context, {
  required AppUiSettings settings,
}) {
  return showDialog<AppUiSettings>(
    context: context,
    builder: (ctx) => _CustomizeDialog(initial: settings),
  );
}

class _CustomizeDialog extends StatefulWidget {
  const _CustomizeDialog({required this.initial});

  final AppUiSettings initial;

  @override
  State<_CustomizeDialog> createState() => _CustomizeDialogState();
}

class _CustomizeDialogState extends State<_CustomizeDialog> {
  late AppUiSettings _draft;
  final _appearanceScroll = ScrollController();
  final _layoutScroll = ScrollController();
  final _pdfScroll = ScrollController();

  @override
  void initState() {
    super.initState();
    _draft = widget.initial;
  }

  @override
  void dispose() {
    _appearanceScroll.dispose();
    _layoutScroll.dispose();
    _pdfScroll.dispose();
    super.dispose();
  }

  void _set(AppUiSettings next) => setState(() => _draft = next);

  @override
  Widget build(BuildContext context) {
    final pdf = _draft.pdfOptions;
    return AlertDialog(
      title: const Text('Customize view & PDF'),
      content: SizedBox(
        width: 560,
        height: 520,
        child: DefaultTabController(
          length: 3,
          child: Column(
            children: [
              const TabBar(
                tabs: [
                  Tab(text: 'Appearance'),
                  Tab(text: 'Layout'),
                  Tab(text: 'PDF output'),
                ],
              ),
              Expanded(
                child: TabBarView(
                  children: [
                    Scrollbar(
                      controller: _appearanceScroll,
                      thumbVisibility: true,
                      interactive: true,
                      child: ListView(
                        controller: _appearanceScroll,
                        primary: false,
                        padding: const EdgeInsets.fromLTRB(4, 12, 4, 8),
                        children: [
                        const Text(
                          'THEME',
                          style: TextStyle(
                            fontFamily: 'Oswald',
                            fontSize: 11,
                            color: SwiftColors.muted,
                            letterSpacing: 0.6,
                          ),
                        ),
                        const SizedBox(height: 6),
                        SegmentedButton<UiThemePreference>(
                          segments: [
                            for (final t in UiThemePreference.values)
                              ButtonSegment(value: t, label: Text(t.label)),
                          ],
                          selected: {_draft.themePreference},
                          onSelectionChanged: (s) => _set(
                            _draft.copyWith(themePreference: s.first),
                          ),
                        ),
                        const SizedBox(height: 16),
                        Text(
                          'UI TEXT SCALE (${(_draft.uiFontScale * 100).round()}%)',
                          style: const TextStyle(
                            fontFamily: 'Oswald',
                            fontSize: 11,
                            color: SwiftColors.muted,
                            letterSpacing: 0.6,
                          ),
                        ),
                        Slider(
                          value: _draft.uiFontScale,
                          min: 0.85,
                          max: 1.35,
                          divisions: 10,
                          label: '${(_draft.uiFontScale * 100).round()}%',
                          onChanged: (v) =>
                              _set(_draft.copyWith(uiFontScale: v)),
                        ),
                        CheckboxListTile(
                          contentPadding: EdgeInsets.zero,
                          value: _draft.useOswaldFont,
                          onChanged: (v) => _set(
                            _draft.copyWith(useOswaldFont: v ?? false),
                          ),
                          title: const Text('Use Oswald font'),
                          subtitle: const Text(
                            'Off = Helvetica (default). On = Oswald headings/UI.',
                          ),
                          controlAffinity: ListTileControlAffinity.leading,
                        ),
                        CheckboxListTile(
                          contentPadding: EdgeInsets.zero,
                          value: _draft.denseForms,
                          onChanged: (v) =>
                              _set(_draft.copyWith(denseForms: v ?? false)),
                          title: const Text('Dense forms'),
                          controlAffinity: ListTileControlAffinity.leading,
                        ),
                      ],
                      ),
                    ),
                    Scrollbar(
                      controller: _layoutScroll,
                      thumbVisibility: true,
                      interactive: true,
                      child: ListView(
                        controller: _layoutScroll,
                        primary: false,
                        padding: const EdgeInsets.fromLTRB(4, 12, 4, 8),
                        children: [
                        const Text(
                          'LAYOUT PRESET',
                          style: TextStyle(
                            fontFamily: 'Oswald',
                            fontSize: 11,
                            color: SwiftColors.muted,
                            letterSpacing: 0.6,
                          ),
                        ),
                        const SizedBox(height: 6),
                        for (final p in UiLayoutPreset.values)
                          RadioListTile<UiLayoutPreset>(
                            contentPadding: EdgeInsets.zero,
                            value: p,
                            // ignore: deprecated_member_use
                            groupValue: _draft.layoutPreset,
                            // ignore: deprecated_member_use
                            onChanged: (v) {
                              if (v == null) return;
                              _set(_draft.copyWith(
                                layoutPreset: v,
                                showWorkspacePane:
                                    v != UiLayoutPreset.compact,
                              ));
                            },
                            title: Text(p.label),
                          ),
                        const Divider(),
                        const Text(
                          'FORM GRID',
                          style: TextStyle(
                            fontFamily: 'Oswald',
                            fontSize: 11,
                            color: SwiftColors.muted,
                            letterSpacing: 0.6,
                          ),
                        ),
                        SegmentedButton<int>(
                          segments: const [
                            ButtonSegment(value: 1, label: Text('1 column')),
                            ButtonSegment(value: 2, label: Text('2 columns')),
                          ],
                          selected: {_draft.formColumns},
                          onSelectionChanged: (s) =>
                              _set(_draft.copyWith(formColumns: s.first)),
                        ),
                        CheckboxListTile(
                          contentPadding: EdgeInsets.zero,
                          value: _draft.showWorkspacePane,
                          onChanged: (v) => _set(
                            _draft.copyWith(showWorkspacePane: v ?? true),
                          ),
                          title: const Text('Show Workspace pane'),
                          controlAffinity: ListTileControlAffinity.leading,
                        ),
                        CheckboxListTile(
                          contentPadding: EdgeInsets.zero,
                          value: _draft.preferExtendedRail,
                          onChanged: (v) => _set(
                            _draft.copyWith(preferExtendedRail: v ?? true),
                          ),
                          title: const Text('Prefer extended navigation rail'),
                          controlAffinity: ListTileControlAffinity.leading,
                        ),
                      ],
                      ),
                    ),
                    Scrollbar(
                      controller: _pdfScroll,
                      thumbVisibility: true,
                      interactive: true,
                      child: ListView(
                        controller: _pdfScroll,
                        primary: false,
                        padding: const EdgeInsets.fromLTRB(4, 12, 4, 8),
                        children: [
                        const Text(
                          'Applies to Shipping, Receiving, and BOL PDFs.',
                          style: TextStyle(
                            color: SwiftColors.muted,
                            fontSize: 13,
                          ),
                        ),
                        const SizedBox(height: 12),
                        const Text(
                          'CUSTOMER LOGO PLACEMENT',
                          style: TextStyle(
                            fontFamily: 'Oswald',
                            fontSize: 11,
                            color: SwiftColors.muted,
                            letterSpacing: 0.6,
                          ),
                        ),
                        const SizedBox(height: 6),
                        DropdownButtonFormField<PdfLogoPlacement>(
                          // ignore: deprecated_member_use
                          value: pdf.logoPlacement,
                          items: [
                            for (final p in PdfLogoPlacement.values)
                              DropdownMenuItem(
                                value: p,
                                child: Text(p.label),
                              ),
                          ],
                          onChanged: (v) {
                            if (v == null) return;
                            _set(_draft.copyWith(
                              pdfOptions: pdf.copyWith(logoPlacement: v),
                            ));
                          },
                        ),
                        const SizedBox(height: 12),
                        Text(
                          'LOGO SCALE (${(pdf.logoScale * 100).round()}%)',
                          style: const TextStyle(
                            fontFamily: 'Oswald',
                            fontSize: 11,
                            color: SwiftColors.muted,
                            letterSpacing: 0.6,
                          ),
                        ),
                        Slider(
                          value: pdf.logoScale,
                          min: 0.6,
                          max: 1.5,
                          divisions: 18,
                          label: '${(pdf.logoScale * 100).round()}%',
                          onChanged: (v) => _set(_draft.copyWith(
                            pdfOptions: pdf.copyWith(logoScale: v),
                          )),
                        ),
                        Text(
                          'PDF FONT SCALE (${(pdf.fontScale * 100).round()}%)',
                          style: const TextStyle(
                            fontFamily: 'Oswald',
                            fontSize: 11,
                            color: SwiftColors.muted,
                            letterSpacing: 0.6,
                          ),
                        ),
                        Slider(
                          value: pdf.fontScale,
                          min: 0.8,
                          max: 1.35,
                          divisions: 11,
                          label: '${(pdf.fontScale * 100).round()}%',
                          onChanged: (v) => _set(_draft.copyWith(
                            pdfOptions: pdf.copyWith(fontScale: v),
                          )),
                        ),
                        const SizedBox(height: 8),
                        const Text(
                          'GENERATION FONT',
                          style: TextStyle(
                            fontFamily: 'Oswald',
                            fontSize: 11,
                            color: SwiftColors.muted,
                            letterSpacing: 0.6,
                          ),
                        ),
                        const SizedBox(height: 6),
                        DropdownButtonFormField<PdfBodyFont>(
                          // ignore: deprecated_member_use
                          value: pdf.bodyFont,
                          items: [
                            for (final f in PdfBodyFont.values)
                              DropdownMenuItem(
                                value: f,
                                child: Text(f.label),
                              ),
                          ],
                          onChanged: (v) {
                            if (v == null) return;
                            _set(_draft.copyWith(
                              pdfOptions: pdf.copyWith(bodyFont: v),
                            ));
                          },
                        ),
                        const SizedBox(height: 12),
                        const Text(
                          'PAGE ORIENTATION',
                          style: TextStyle(
                            fontFamily: 'Oswald',
                            fontSize: 11,
                            color: SwiftColors.muted,
                            letterSpacing: 0.6,
                          ),
                        ),
                        const SizedBox(height: 6),
                        Text(
                          'Fixed by document type: Shipping/Receiving are '
                          'landscape Letter; BOL is portrait Letter. '
                          'This cannot be changed without redesigning the layouts.',
                          style: TextStyle(
                            fontSize: 13,
                            color: SwiftColors.muted.withValues(alpha: 0.95),
                            height: 1.35,
                          ),
                        ),
                        CheckboxListTile(
                          contentPadding: EdgeInsets.zero,
                          value: pdf.showCustomerLogos,
                          onChanged: (v) => _set(_draft.copyWith(
                            pdfOptions:
                                pdf.copyWith(showCustomerLogos: v ?? true),
                          )),
                          title: const Text('Show customer logos'),
                          controlAffinity: ListTileControlAffinity.leading,
                        ),
                      ],
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        TextButton(
          onPressed: () => Navigator.pop(context, AppUiSettings.defaults),
          child: const Text('Reset defaults'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(context, _draft),
          child: const Text('Apply'),
        ),
      ],
    );
  }
}
