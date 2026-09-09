import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:path/path.dart' as p;

import 'address_book_sync.dart';
import 'android_shake.dart';
import 'app_config.dart';
import 'app_snack.dart';
import 'app_storage.dart';
import 'app_theme_scope.dart';
import 'address_suggest_field.dart';
import 'bol_document_number.dart';
import 'bol_dimensions.dart';
import 'bol_item_type.dart';
import 'brand_assets.dart';
import 'bulk/bulk_label_models.dart';
import 'bulk/order_ack_pdf_text.dart';
import 'bulk/order_ack_parser.dart';
import 'job_pdf_ai.dart';
import 'staging_log_promo.dart';
import 'circle_selector.dart';
import 'carrier_sync.dart';
import 'contact_sync.dart';
import 'document_history_sync.dart';
import 'employee_autocomplete_field.dart';
import 'feedback_forms.dart';
import 'form_scroll_text_field.dart';
import 'label_data.dart';
import 'logo_finder.dart';
import 'logo_import_options.dart';
import 'logo_perfect_restore.dart';
import 'pdf/bol_label_pdf.dart';
import 'pdf/bulk_label_docx.dart';
import 'pdf/bulk_label_pdf.dart';
import 'pdf/shipping_label_pdf.dart';
import 'multi_order_fields.dart';
import 'pdf_render_options.dart';
import 'platform_io.dart';
import 'preset_sync.dart';
import 'signature_pad.dart';
import 'signature_sync.dart';
import 'ship_to_suggest_field.dart';
import 'startup_sync.dart';
import 'theme.dart';
import 'update_sheet.dart';
import 'operations_apps_rail.dart';
import 'windows_menu_bar.dart';

String swiftUiFont(BuildContext context) {
  try {
    return AppThemeScope.of(context).value.uiFontFamily;
  } catch (_) {
    return Theme.of(context).textTheme.bodyMedium?.fontFamily ?? 'Helvetica';
  }
}


const _shippingGroups = <(String title, String hint, List<String> keys)>[
  (
    'Customer & job',
    'Who the shipment is for',
    [
      LabelFields.customer,
      LabelFields.salesOrder,
      LabelFields.poNum,
      LabelFields.project,
      LabelFields.attn,
      LabelFields.specialInstructions,
    ],
  ),
  (
    'Ship to',
    'Destination the warehouse reads first',
    [LabelFields.shipTo, LabelFields.location],
  ),
  (
    'Carrier & billing',
    'Courier and freight terms (same as BOL)',
    [
      LabelFields.carrier,
      BolFields.thirdPartyBilling,
    ],
  ),
  (
    'Swift references',
    'Internal tracking',
    [
      LabelFields.packingSlip,
      LabelFields.swiftContact,
    ],
  ),
];

const _receivingGroups = <(String title, String hint, List<String> keys)>[
  (
    'Customer & job',
    'Who the staged material is for',
    [
      LabelFields.customer,
      LabelFields.project,
      LabelFields.salesOrder,
      LabelFields.poNum,
      LabelFields.specialInstructions,
      LabelFields.swiftContact,
    ],
  ),
  (
    'Received',
    'Dock stamp — date and who signed',
    [LabelFields.dateReceived, LabelFields.receivedBy],
  ),
];

const _bolMaxLines = 10;

const _bolGroupsBeforeLines = <(String title, String hint, List<String> keys)>[
  (
    'Document',
    'Document number (SW-####) is assigned automatically from the shared company counter when you Generate',
    [
      BolFields.documentDate,
      BolFields.bookingRef,
      BolFields.probillNumber,
    ],
  ),
  (
    'Ship to (consignee)',
    'Delivery party',
    [
      BolFields.consigneeName,
      BolFields.consigneeAddress,
      BolFields.consigneeContactName,
      BolFields.consigneeContactNumber,
    ],
  ),
  (
    'Billing & freight',
    'Prepaid, collect, third party, or customer pick-up',
    [BolFields.thirdPartyBilling],
  ),
  (
    'Tracking & references',
    'PO, packing list, sales order, project',
    [
      LabelFields.salesOrder,
      LabelFields.poNum,
      BolFields.packingList,
      LabelFields.project,
      LabelFields.specialInstructions,
    ],
  ),
];

const _bolSignaturesGroup = (
  'Signatures',
  'Shipper / driver / consignee',
  [
    BolFields.shipperCertName,
    BolFields.shipperCertDate,
    BolFields.driverCompany,
    BolFields.driverPrint,
    BolFields.vehicleId,
    BolFields.driverDate,
    BolFields.consigneePrint,
    BolFields.consigneeDate,
  ],
);

List<String> _bolLineFieldKeys(int lineNum) => [
      BolFields.lineKey(lineNum, 'pieces'),
      BolFields.lineKey(lineNum, 'item_type'),
      BolFields.lineKey(lineNum, 'dimensions'),
      BolFields.lineKey(lineNum, 'description'),
      BolFields.lineKey(lineNum, 'weight'),
    ];

String _bolLineHint(int lineNum) => switch (lineNum) {
      1 => 'First goods row',
      2 => 'Second goods row',
      _ => 'Additional goods row (optional)',
    };

class HomeScreen extends StatefulWidget {
  const HomeScreen({
    super.key,
    required this.storage,
    required this.pdf,
    this.startupSync,
  });

  final AppStorage storage;
  final ShippingLabelPdf pdf;

  /// Shared launch-time Supabase sync bundle. When `main.dart` already
  /// created one (real app launches on every platform), pass it here so the
  /// sync calls below reuse its already-in-flight futures instead of hitting
  /// Supabase a second time. Tests that construct [HomeScreen] directly may
  /// omit it — a fresh [StartupSync] is created locally in that case.
  final StartupSync? startupSync;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen>
    with SingleTickerProviderStateMixin {
  late final Map<String, TextEditingController> _controllers;
  String? _presetName;
  /// Selected customer logo paths (primary first, optional C/O second).
  final List<String> _logoPaths = [];
  bool _busy = false;
  bool _findingLogo = false;
  int _restoreInFlight = 0;
  bool get _restoringLogo => _restoreInFlight > 0;
  LabelKind _kind = LabelKind.shipping;
  /// Which BOL copy pages to generate (all selected by default).
  bool _bolStoreCopy = true;
  bool _bolDriverCopy = true;
  bool _bolCustomerCopy = true;
  /// When true, BOL generate also appends Shipping Label pages.
  bool _bolAlsoShippingLabels = false;
  /// User dismissed SO↔PO/Project/Packing List pairing — Shipping Labels locked off.
  bool _soPairingDismissed = false;
  /// Visible BOL goods lines (1..7); start with line 1 only.
  int _bolLineCount = 1;
  late final StartupSync _startupSync;
  late final PresetSync _presetSync;
  late final SignatureSync _signatureSync;
  late final ContactSync _contactSync;
  late final AddressBookSync _addressBookSync;
  late final DocumentHistorySync _documentHistorySync;
  /// Customer text for which the template prompt was already resolved this session.
  String? _templatePromptResolvedForCustomer;
  final FocusNode _customerFocusNode = FocusNode();
  /// Portrait Android chrome: 1 = full header, 0 = collapsed strip (Chrome-like).
  late final AnimationController _mobileChromeCtrl;
  late final Animation<double> _mobileChromeT;
  Uint8List? _shipperSignatureBytes;
  SavedSignature? _selectedSavedSignature;
  AppUiSettings _uiSettings = AppUiSettings.defaults;
  /// Box-sized (1/4 page) shipping/receiving labels. Disabled for BOL.
  bool _boxSizedLabel = false;
  /// Parsed Order Acknowledgement for Bulk Labels mode.
  OrderAckParseResult? _bulkParse;
  String? _bulkSourcePath;
  bool _bulkParsing = false;
  /// OA PDF used to fill Shipping / Receiving / BOL fields.
  String? _oaFillSourceName;
  bool _oaFilling = false;
  bool _bolMultiPdf = false;
  List<String> _swiftContactNames = const [];
  bool _swiftContactsLoading = false;
  late final CarrierSync _carrierSync;
  List<String> _carrierNames = const [];
  bool _carrierNamesLoading = false;
  final FocusNode _carrierFocusNode = FocusNode();
  /// Focus nodes for employee-name autocomplete fields (one per key).
  final Map<String, FocusNode> _employeeFocusNodes = {
    LabelFields.swiftContact: FocusNode(),
    LabelFields.receivedBy: FocusNode(),
    BolFields.shipperCertName: FocusNode(),
  };
  /// Desktop form column — shared with [Scrollbar] so wheel works over fields.
  final ScrollController _desktopFormScroll = ScrollController();
  /// Desktop workspace pane list.
  final ScrollController _desktopWorkspaceScroll = ScrollController();
  StreamSubscription<void>? _androidShakeSub;
  bool _shakeMenuOpen = false;
  SoFieldMap _soFieldMap = SoFieldMap();
  bool _mappingDialogOpen = false;
  /// Prevents double-taps from stacking the same prompt (History, Address Book, …).
  final Set<String> _dialogLocks = {};
  String _installedVersionLabel = '';
  final Map<String, FocusNode> _multiFieldFocus = {};

  bool _isDialogLocked(String key) => _dialogLocks.contains(key);

  /// Opens at most one dialog per [key]. Second clicks are ignored until close.
  Future<T?> _runExclusiveDialog<T>(
    String key,
    Future<T?> Function() open,
  ) async {
    if (_dialogLocks.contains(key)) return null;
    setState(() => _dialogLocks.add(key));
    try {
      return await open();
    } finally {
      if (mounted) {
        setState(() => _dialogLocks.remove(key));
      } else {
        _dialogLocks.remove(key);
      }
    }
  }

  @override
  void initState() {
    super.initState();
    _startupSync = widget.startupSync ?? StartupSync(widget.storage);
    _presetSync = _startupSync.presetSync;
    _signatureSync = _startupSync.signatureSync;
    _contactSync = _startupSync.contactSync;
    _addressBookSync = _startupSync.addressBookSync;
    _documentHistorySync = _startupSync.documentHistorySync;
    _carrierSync = _startupSync.carrierSync;
    _customerFocusNode.addListener(_onCustomerFocusChanged);
    _mobileChromeCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 340),
      value: 1,
    );
    _mobileChromeT = CurvedAnimation(
      parent: _mobileChromeCtrl,
      curve: Curves.easeInOutCubic,
      reverseCurve: Curves.easeInOutCubic,
    );
    _controllers = {
      for (final def in LabelFields.formDefs)
        def.$1: TextEditingController(),
      BolFields.freightCharges: TextEditingController(
        text: BolFields.freightPrepaid,
      ),
    };
    _loadUiSettings();
    _syncPresetsOnLaunch();
    _refreshContactSuggestions();
    unawaited(_loadCarrierNames());
    unawaited(_loadInstalledVersionLabel());
    unawaited(_startupSync.historyPurgeFuture);
    if (Platform.isAndroid) {
      _androidShakeSub = AndroidShake.events.listen((_) {
        unawaited(_onAndroidShake());
      });
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _maybeShowLaunchPrompts();
    });
  }

  Future<void> _maybeShowLaunchPrompts() async {
    // Let first-frame layout settle so the dialog is not racing chrome paint.
    await Future<void>.delayed(const Duration(milliseconds: 450));
    if (!mounted) return;
    try {
      await maybeShowLaunchPrompts(context, widget.storage);
    } catch (_) {
      // Launch prompts are optional — never block the form on prompt errors.
    }
  }

  /// Autocomplete uses Document Generator shared contacts (synced Windows/Android).
  /// Not connected to the SLST `dropdown_roster` API.
  Future<void> _loadSwiftContacts({bool forceRefresh = false}) async {
    if (_swiftContactsLoading) return;
    setState(() => _swiftContactsLoading = true);
    try {
      if (forceRefresh) {
        await _contactSync.syncOnLaunch();
      }
      if (!mounted) return;
      setState(() {
        _swiftContactNames = widget.storage.contactSuggestions();
        _swiftContactsLoading = false;
      });
    } on ContactSyncException catch (e) {
      if (!mounted) return;
      setState(() {
        _swiftContactNames = widget.storage.contactSuggestions();
        _swiftContactsLoading = false;
      });
      showAppSnack(context, 'Contact sync: ${e.message}');
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _swiftContactNames = widget.storage.contactSuggestions();
        _swiftContactsLoading = false;
      });
    }
  }

  void _refreshContactSuggestions() {
    setState(() {
      _swiftContactNames = widget.storage.contactSuggestions();
    });
  }

  Future<void> _rememberContactNames(Iterable<String> rawNames) async {
    var changed = false;
    for (final raw in rawNames) {
      final name = raw.trim();
      if (name.isEmpty) continue;
      try {
        if (await _contactSync.remember(name)) changed = true;
      } on ContactSyncException {
        // Fall back to local-only remember if cloud is offline.
        if (await widget.storage.rememberContact(name)) changed = true;
      } catch (_) {
        if (await widget.storage.rememberContact(name)) changed = true;
      }
    }
    if (changed && mounted) _refreshContactSuggestions();
  }

  /// Autocomplete uses the same shared carrier directory as Staging &
  /// Shipping Log (Windows/Android + Wear) — not the SLST `dropdown_roster`.
  Future<void> _loadCarrierNames({bool forceRefresh = false}) async {
    if (_carrierNamesLoading && !forceRefresh) return;
    setState(() => _carrierNamesLoading = true);
    try {
      // Initial launch load reuses StartupSync's already-in-flight fetch;
      // an explicit refresh always hits Supabase fresh.
      final names = forceRefresh
          ? await _carrierSync.fetchNames()
          : await _startupSync.carrierFuture;
      if (!mounted) return;
      setState(() {
        _carrierNames = names;
        _carrierNamesLoading = false;
      });
    } on CarrierSyncException catch (e) {
      if (!mounted) return;
      setState(() => _carrierNamesLoading = false);
      showAppSnack(context, 'Carrier sync: ${e.message}');
    } catch (_) {
      if (!mounted) return;
      setState(() => _carrierNamesLoading = false);
    }
  }

  Future<void> _rememberCarrierName(String raw) async {
    final name = raw.trim();
    if (name.isEmpty) return;
    try {
      await _carrierSync.remember(name);
    } catch (_) {
      // Best-effort — the field value is already saved on the form either way.
      return;
    }
    if (!mounted) return;
    if (!_carrierNames.any((n) => n.toLowerCase() == name.toLowerCase())) {
      setState(() => _carrierNames = [name, ..._carrierNames]);
    }
  }

  Future<void> _loadUiSettings() async {
    try {
      final s = await widget.storage.loadUiSettings();
      if (!mounted) return;
      setState(() => _uiSettings = s);
      _syncAppTheme(s);
    } catch (_) {}
  }

  void _applyUiSettings(AppUiSettings s) {
    setState(() => _uiSettings = s);
    _syncAppTheme(s);
  }

  void _syncAppTheme(AppUiSettings s) {
    try {
      AppThemeScope.of(context).value = s;
    } catch (_) {}
  }

  Future<void> _loadInstalledVersionLabel() async {
    try {
      final info = await PackageInfo.fromPlatform();
      if (!mounted) return;
      setState(() {
        _installedVersionLabel = '${info.version}+${info.buildNumber}';
      });
    } catch (_) {}
  }

  Future<void> _openErrorCapture() async {
    await _runExclusiveDialog<void>('errorCapture', () async {
      if (!mounted) return;
      await openErrorCaptureForm(
        context,
        installedVersion:
            _installedVersionLabel.isEmpty ? 'unknown' : _installedVersionLabel,
        title: Platform.isAndroid ? 'Error Capture' : 'Error capture (F2)',
      );
    });
  }

  Future<void> _openFeedback() async {
    await _runExclusiveDialog<void>('feedback', () async {
      if (!mounted) return;
      await openFeedbackForm(
        context,
        installedVersion:
            _installedVersionLabel.isEmpty ? 'unknown' : _installedVersionLabel,
      );
    });
  }

  Future<void> _showAbout() async {
    await _runExclusiveDialog<void>('about', () async {
      if (!mounted) return;
      showAboutDialog(
        context: context,
        applicationName: 'Swift Document Generator',
        applicationVersion: _installedVersionLabel.isEmpty
            ? 'unknown'
            : _installedVersionLabel,
        applicationLegalese: 'Swift Oilfield Supply',
        children: [
          const SizedBox(height: 12),
          Text(
            'Shipping, Receiving, and Bill of Lading for Windows and Android.\n'
            'Releases: ${AppConfig.githubReleasesPage}',
          ),
        ],
      );
    });
  }

  Future<void> _onAndroidShake() async {
    if (!mounted || _shakeMenuOpen) return;
    _shakeMenuOpen = true;
    try {
      await showModalBottomSheet<void>(
        context: context,
        showDragHandle: true,
        builder: (ctx) {
          return SafeArea(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const ListTile(
                  title: Text('Help'),
                  subtitle: Text('Hard shake shortcut'),
                ),
                ListTile(
                  leading: const Icon(Icons.info_outline),
                  title: const Text('About'),
                  onTap: () {
                    Navigator.pop(ctx);
                    unawaited(_showAbout());
                  },
                ),
                ListTile(
                  leading: const Icon(Icons.feedback_outlined),
                  title: const Text('Send Feedback'),
                  onTap: () {
                    Navigator.pop(ctx);
                    unawaited(_openFeedback());
                  },
                ),
                ListTile(
                  leading: const Icon(Icons.bug_report_outlined),
                  title: const Text('Error Capture'),
                  onTap: () {
                    Navigator.pop(ctx);
                    unawaited(_openErrorCapture());
                  },
                ),
                const SizedBox(height: 8),
              ],
            ),
          );
        },
      );
    } finally {
      _shakeMenuOpen = false;
    }
  }

  Future<void> _toggleDarkMode() async {
    final next = _uiSettings.copyWith(
      themePreference: _uiSettings.isDark
          ? UiThemePreference.light
          : UiThemePreference.dark,
    );
    await widget.storage.saveUiSettings(next);
    _applyUiSettings(next);
  }

  Future<void> _syncPresetsOnLaunch() async {
    try {
      // Reuses the future StartupSync already kicked off in main() (or in
      // initState above, for callers that don't pass one in) — never a
      // second network round-trip for the same launch.
      await _startupSync.presetFuture;
      if (mounted) setState(() {});
    } on PresetSyncException catch (e) {
      if (mounted) {
        showAppSnack(context, 'Preset sync: ${e.message}');
      }
    } catch (_) {
      if (mounted) {
        showAppSnack(context, 'Preset sync failed — using local presets.');
      }
    }
    await _syncSignaturesQuietly();
    await _syncContactsQuietly();
    await _syncAddressBookQuietly();
  }

  Future<void> _syncAddressBookQuietly() async {
    try {
      await _startupSync.addressBookFuture;
      if (mounted) setState(() {});
    } on AddressBookSyncException catch (e) {
      if (mounted) {
        showAppSnack(context, 'Address book: ${e.message}');
      }
    } catch (_) {
      // Offline — address book still works after first successful sync.
    }
  }

  Future<void> _syncSignaturesQuietly() async {
    try {
      await _startupSync.signatureFuture;
      if (mounted) setState(() {});
    } on SignatureSyncException catch (e) {
      if (mounted) {
        showAppSnack(context, 'Signature sync: ${e.message}');
      }
    } catch (_) {
      // Signatures are optional — ignore offline failures.
    }
  }

  Future<void> _syncContactsQuietly() async {
    try {
      await _startupSync.contactFuture;
      if (!mounted) return;
      _refreshContactSuggestions();
    } on ContactSyncException catch (e) {
      if (mounted) {
        showAppSnack(context, 'Contact sync: ${e.message}');
      }
    } catch (_) {
      // Contacts still work from local cache when offline.
    }
  }

  Future<void> _pushPresetQuietly(LabelKind kind, String displayName) async {
    try {
      await _presetSync.pushPreset(kind, displayName);
    } on PresetSyncException catch (e) {
      if (mounted) {
        showAppSnack(context, 'Cloud sync: ${e.message}');
      }
    } catch (_) {
      if (mounted) {
        showAppSnack(context, 'Cloud sync failed — saved locally.');
      }
    }
  }

  Future<void> _deletePresetQuietly(LabelKind kind, String displayName) async {
    try {
      await _presetSync.deletePreset(kind, displayName);
    } on PresetSyncException catch (e) {
      if (mounted) {
        showAppSnack(context, 'Cloud sync: ${e.message}');
      }
    } catch (_) {
      if (mounted) {
        showAppSnack(context, 'Cloud sync failed — deleted locally only.');
      }
    }
  }

  @override
  void dispose() {
    _customerFocusNode.removeListener(_onCustomerFocusChanged);
    _customerFocusNode.dispose();
    _carrierFocusNode.dispose();
    _mobileChromeCtrl.dispose();
    for (final c in _controllers.values) {
      c.dispose();
    }
    for (final n in _employeeFocusNodes.values) {
      n.dispose();
    }
    for (final n in _multiFieldFocus.values) {
      n.dispose();
    }
    _androidShakeSub?.cancel();
    _desktopFormScroll.dispose();
    _desktopWorkspaceScroll.dispose();
    super.dispose();
  }

  ShippingLabelData _collect() {
    final data = ShippingLabelData();
    for (final e in _controllers.entries) {
      data.set(e.key, e.value.text);
    }
    data.set(LabelFields.soFieldMap, _soFieldMap.encode());
    return data;
  }

  FocusNode _multiFocus(String key) {
    return _multiFieldFocus.putIfAbsent(key, () {
      final n = FocusNode();
      n.addListener(() {
        if (!n.hasFocus) {
          unawaited(_onMultiFieldBlur(key));
        }
      });
      return n;
    });
  }

  bool _isSalesOrderKey(String key) =>
      key == LabelFields.salesOrder || key == BolFields.orderNum;

  bool _isNamedMultiKey(String key) =>
      key == LabelFields.poNum ||
      key == LabelFields.project ||
      key == LabelFields.packingSlip ||
      key == BolFields.packingList;

  void _applyFormattedField(String key, String formatted) {
    final c = _controllers[key];
    if (c == null) return;
    if (c.text == formatted) return;
    c.value = TextEditingValue(
      text: formatted,
      selection: TextSelection.collapsed(offset: formatted.length),
    );
    if (key == BolFields.packingList) {
      final slip = _controllers[LabelFields.packingSlip];
      if (slip != null && slip.text.trim().isEmpty) {
        slip.text = formatted;
      }
    }
    if (key == LabelFields.salesOrder) {
      _controllers[BolFields.orderNum]?.text = formatted;
    }
  }

  Future<void> _onMultiFieldBlur(String key) async {
    if (!mounted || _mappingDialogOpen) return;
    final raw = _controllers[key]?.text ?? '';
    if (_isSalesOrderKey(key)) {
      _applyFormattedField(key, formatNamedSegments(raw, finalize: true));
      return;
    }
    if (!_isNamedMultiKey(key)) return;
    _applyFormattedField(key, formatNamedSegments(raw, finalize: true));
    await _promptSoMappingIfNeeded(key);
  }

  Future<void> _promptSoMappingIfNeeded(String fieldKey) async {
    final sos = parseSalesOrders(_controllers[LabelFields.salesOrder]?.text ?? '');
    final extras = parseNamedSegments(
      _controllers[fieldKey]?.text ?? '',
      finalize: true,
    );
    if (pairableCount(extras.length, sos.length) == 0) return;
    var assigned = <int, String>{};
    final existing = _soFieldMap.forField(fieldKey);
    for (var i = 0; i < extras.length; i++) {
      for (final e in existing.entries) {
        if (e.value == extras[i]) assigned[i] = e.key;
      }
    }
    _mappingDialogOpen = true;
    try {
      while (mounted) {
        final step = nextMappingStep(
          extras: extras,
          salesOrders: sos,
          assigned: assigned,
        );
        if (step.done) {
          _soFieldMap.setField(
            fieldKey,
            mappingBySalesOrder(extras: extras, assigned: step.assigned),
          );
          if (_soPairingDismissed) {
            setState(() => _soPairingDismissed = false);
          }
          break;
        }
        final picked = await showDialog<String>(
          context: context,
          builder: (ctx) {
            return AlertDialog(
              title: const Text('Link to sales order'),
              content: SizedBox(
                width: 420,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(
                      'Which sales order does this belong to?\n${step.askValue}',
                    ),
                    const SizedBox(height: 12),
                    for (final so in step.choices)
                      ListTile(
                        title: Text(so),
                        onTap: () => Navigator.pop(ctx, so),
                      ),
                  ],
                ),
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.pop(ctx),
                  child: const Text('Proceed without pairing'),
                ),
              ],
            );
          },
        );
        if (picked == null || picked.isEmpty) {
          if (mounted) {
            setState(() {
              _soPairingDismissed = true;
              _bolAlsoShippingLabels = false;
            });
          }
          break;
        }
        assigned = {...step.assigned, step.askIndex!: picked};
      }
    } finally {
      _mappingDialogOpen = false;
    }
  }

  ShippingLabelData _shippingDataForSalesOrder(
    ShippingLabelData base,
    String salesOrder,
  ) {
    final d = base.copy();
    d.set(LabelFields.salesOrder, salesOrder);
    d.set(BolFields.orderNum, salesOrder);
    void apply(String field, String dest) {
      final v = _soFieldMap.valueFor(field, salesOrder);
      if (v.isNotEmpty) d.set(dest, v);
    }
    apply(LabelFields.poNum, LabelFields.poNum);
    apply(LabelFields.project, LabelFields.project);
    apply(BolFields.packingList, LabelFields.packingSlip);
    apply(LabelFields.packingSlip, LabelFields.packingSlip);
    apply(BolFields.packingList, BolFields.packingList);
    return d;
  }

  void _setField(String key, String value) {
    if (key == BolFields.freightCharges) {
      _controllers[key]?.text = _normalizeFreightCharges(value);
      return;
    }
    _controllers[key]?.text = value;
  }

  String _normalizeFreightCharges(String raw) {
    final f = raw.toLowerCase().trim();
    if (f == BolFields.freightCollect) return BolFields.freightCollect;
    if (f == BolFields.freightThirdParty ||
        f == '3rd party' ||
        f == 'third party') {
      return BolFields.freightThirdParty;
    }
    if (f == BolFields.freightCustomerPickup ||
        f == 'customer pick-up' ||
        f == 'customer pickup' ||
        f == 'cust. pick-up' ||
        f == 'cust pick-up' ||
        f == 'pick-up' ||
        f == 'pickup') {
      return BolFields.freightCustomerPickup;
    }
    if (f == BolFields.freightPrepaid) return BolFields.freightPrepaid;
    return '';
  }

  Set<String> _selectedFreightCharges() {
    final v = _normalizeFreightCharges(
      _controllers[BolFields.freightCharges]?.text ?? '',
    );
    return {v.isEmpty ? BolFields.freightPrepaid : v};
  }

  int _detectBolLineCount() {
    var maxLine = 1;
    for (var i = 1; i <= _bolMaxLines; i++) {
      final hasData = _bolLineFieldKeys(i).any(
        (key) => (_controllers[key]?.text.trim() ?? '').isNotEmpty,
      );
      if (hasData) maxLine = i;
    }
    return maxLine;
  }

  void _clearBolLineFields(int lineNum) {
    for (final key in _bolLineFieldKeys(lineNum)) {
      _controllers[key]?.clear();
    }
  }

  void _addBolLine() {
    if (_bolLineCount >= _bolMaxLines) return;
    setState(() => _bolLineCount++);
  }

  void _removeBolLine(int lineNum) {
    if (lineNum <= 1 || lineNum > _bolLineCount) return;
    for (var from = lineNum + 1; from <= _bolLineCount; from++) {
      final to = from - 1;
      for (final suffix
          in ['pieces', 'item_type', 'dimensions', 'description', 'weight']) {
        _setField(
          BolFields.lineKey(to, suffix),
          _controllers[BolFields.lineKey(from, suffix)]?.text ?? '',
        );
      }
    }
    _clearBolLineFields(_bolLineCount);
    setState(() => _bolLineCount--);
  }

  (String, String, bool) _meta(String key) {
    return LabelFields.formDefs.firstWhere(
      (d) => d.$1 == key,
      orElse: () => (key, key, false),
    );
  }

  String _presetStorageKey(String displayName) =>
      AppStorage.presetStorageKey(_kind, displayName);

  void _applyPreset(String displayName, {bool notify = true}) {
    final preset = widget.storage.presetFor(_kind, displayName);
    if (preset == null) return;
    for (final key in presetKeysFor(_kind)) {
      if (preset.fields.containsKey(key)) {
        _setField(key, preset.fields[key] ?? '');
      }
    }
    _logoPaths
      ..clear()
      ..addAll(
        preset.logoFileNames
            .map((n) => p.join(widget.storage.logosDir.path, n))
            .where((path) => File(path).existsSync())
            .take(maxCustomerLogos),
      );
    _presetName = displayName;
    _markTemplatePromptResolved();
    if (notify) setState(() {});
  }

  void _applyPresetLogos(CustomerPreset preset) {
    _logoPaths
      ..clear()
      ..addAll(
        preset.logoFileNames
            .map((n) => p.join(widget.storage.logosDir.path, n))
            .where((path) => File(path).existsSync())
            .take(maxCustomerLogos),
      );
  }

  void _applyPresetCore(CustomerPreset preset) {
    for (final key in corePresetKeysFor(_kind)) {
      if (preset.fields.containsKey(key)) {
        _setField(key, preset.fields[key] ?? '');
      }
    }
    _applyPresetLogos(preset);
    _presetName = preset.name;
    _markTemplatePromptResolved();
    setState(() {});
  }

  void _applyPresetLogosOnly(CustomerPreset preset) {
    for (final key in presetKeysFor(_kind)) {
      _setField(key, '');
    }
    _applyPresetLogos(preset);
    _presetName = preset.name;
    _markTemplatePromptResolved();
    setState(() {});
  }

  List<CustomerPreset> _matchingTemplatesForCustomer(String customer) {
    if (_kind != LabelKind.shipping && _kind != LabelKind.receiving) {
      return const [];
    }
    final out = <CustomerPreset>[];
    for (final name in widget.storage.presetDisplayNamesFor(_kind)) {
      final preset = widget.storage.presetFor(_kind, name);
      if (preset == null) continue;
      if (presetMatchesCustomer(preset, customer)) out.add(preset);
    }
    // Prefer longer/more specific names first when multiple fuzzy matches.
    out.sort((a, b) {
      final an = normalizeCustomerKey(a.name).length;
      final bn = normalizeCustomerKey(b.name).length;
      return bn.compareTo(an);
    });
    return out;
  }

  void _markTemplatePromptResolved() {
    _templatePromptResolvedForCustomer =
        _controllers[LabelFields.customer]?.text.trim();
  }

  void _onCustomerFocusChanged() {
    if (_customerFocusNode.hasFocus) return;
    // Debounce: fire after focus leaves Customer (tap another field / Done).
    Future<void>.delayed(const Duration(milliseconds: 80), () {
      if (!mounted) return;
      if (_customerFocusNode.hasFocus) return;
      _maybePromptCustomerTemplate();
    });
  }

  Future<void> _maybePromptCustomerTemplate() async {
    await _runExclusiveDialog<void>('customerTemplate', () async {
      if (!mounted) return;
      if (_kind != LabelKind.shipping && _kind != LabelKind.receiving) return;
      final customer = _controllers[LabelFields.customer]?.text.trim() ?? '';
      if (customer.isEmpty) return;
      // Already building from a chosen preset — do not ask again.
      if (_presetName != null) {
        _markTemplatePromptResolved();
        return;
      }
      final resolvedKey =
          normalizeCustomerKey(_templatePromptResolvedForCustomer ?? '');
      final customerKey = normalizeCustomerKey(customer);
      if (resolvedKey.isNotEmpty && resolvedKey == customerKey) {
        return;
      }

      final matches = _matchingTemplatesForCustomer(customer);
      if (matches.isEmpty) {
        _templatePromptResolvedForCustomer = customer;
        return;
      }

      final picked = await showDialog<CustomerPreset?>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('Use a saved template?'),
          content: SizedBox(
            width: 420,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  'Saved templates match “$customer”.',
                  style: TextStyle(
                    fontFamily: swiftUiFont(ctx),
                    fontSize: 14,
                  ),
                ),
                const SizedBox(height: 12),
                for (final m in matches)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text(m.name),
                    subtitle: Text(
                      (m.fields[LabelFields.customer] ?? '').trim().isEmpty
                          ? 'Template'
                          : m.fields[LabelFields.customer]!,
                    ),
                    trailing: const Text('USE'),
                    onTap: () => Navigator.pop(ctx, m),
                  ),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx, null),
              child: const Text('Proceed anyway'),
            ),
          ],
        ),
      );
      if (!mounted) return;
      _templatePromptResolvedForCustomer = customer;
      if (picked == null) return;

      final mode = await showDialog<String>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text('Apply “${picked.name}”'),
          content: const Text(
            'How much of this template should we load?',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx, 'full'),
              child: const Text('Full last save'),
            ),
            TextButton(
              onPressed: () => Navigator.pop(ctx, 'core'),
              child: const Text('Core only'),
            ),
            TextButton(
              onPressed: () => Navigator.pop(ctx, 'logos'),
              child: const Text('Logos only'),
            ),
            TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('Cancel'),
            ),
          ],
        ),
      );
      if (!mounted || mode == null) return;
      switch (mode) {
        case 'full':
          _applyPreset(picked.name);
        case 'core':
          _applyPresetCore(picked);
        case 'logos':
          _applyPresetLogosOnly(picked);
      }
    });
  }

  Future<void> _openAddressBook() async {
    await _runExclusiveDialog<void>('addressBook', () async {
      if (!mounted) return;
      final picked = await showDialog<DeliveryAddressEntry>(
        context: context,
        builder: (ctx) => _AddressBookEditor(
          sync: _addressBookSync,
        ),
      );
      if (picked == null || !mounted) return;
      _applyAddressBookEntry(picked);
    });
  }

  void _applyAddressBookEntry(DeliveryAddressEntry e) {
    if (_kind == LabelKind.shipping) {
      _setField(LabelFields.shipTo, e.shipToName);
      _setField(LabelFields.location, e.address);
      _setField(LabelFields.carrier, e.carrier);
      _setField(BolFields.thirdPartyBilling, e.accountNumbers);
    } else if (_kind == LabelKind.receiving) {
      _setField(LabelFields.customer, e.shipToName);
      _setField(LabelFields.location, e.address);
      _setField(LabelFields.carrier, e.carrier);
    } else if (_kind == LabelKind.bol) {
      _setField(BolFields.consigneeName, e.shipToName);
      _setField(BolFields.consigneeAddress, e.address);
      _setField(BolFields.driverCompany, e.carrier);
      _setField(BolFields.thirdPartyBilling, e.accountNumbers);
    }
    setState(() {});
  }

  Future<void> _rememberDeliveryAddress(ShippingLabelData data) async {
    // Applying a saved address works for all three kinds (_applyAddressBookEntry)
    // — remembering one back should too, or Receiving-only customers never
    // get learned.
    if (_kind != LabelKind.shipping &&
        _kind != LabelKind.bol &&
        _kind != LabelKind.receiving) {
      return;
    }
    final shipTo = switch (_kind) {
      LabelKind.receiving => data.get(LabelFields.customer),
      LabelKind.bol => data.get(BolFields.consigneeName),
      _ => data.get(LabelFields.shipTo),
    };
    final address = switch (_kind) {
      LabelKind.bol => data.get(BolFields.consigneeAddress),
      _ => data.get(LabelFields.location),
    };
    final carrier = switch (_kind) {
      LabelKind.bol => data.get(BolFields.driverCompany),
      _ => data.get(LabelFields.carrier),
    };
    final accounts = data.get(BolFields.thirdPartyBilling);
    if (address.trim().isEmpty) return;
    try {
      await _addressBookSync.remember(
        shipToName: shipTo,
        address: address,
        carrier: carrier,
        accountNumbers: accounts,
      );
      if (mounted) setState(() {});
    } on AddressBookSyncException catch (e) {
      if (mounted) {
        showAppSnack(context, 'Address book save: ${e.message}');
      }
    } catch (_) {
      // Non-fatal — PDF already generated.
    }
  }

  ShippingLabelData _shippingDataFromBol(ShippingLabelData bol) {
    final s = bol.copy();
    if (s.get(LabelFields.shipTo).isEmpty) {
      s.set(LabelFields.shipTo, bol.get(BolFields.consigneeName));
    }
    if (s.get(LabelFields.location).isEmpty) {
      s.set(LabelFields.location, bol.get(BolFields.consigneeAddress));
    }
    if (s.get(LabelFields.carrier).isEmpty) {
      s.set(LabelFields.carrier, bol.get(BolFields.driverCompany));
    }
    if (s.get(LabelFields.packingSlip).isEmpty) {
      s.set(LabelFields.packingSlip, bol.get(BolFields.packingList));
    }
    if (s.get(LabelFields.attn).isEmpty) {
      s.set(LabelFields.attn, bol.get(BolFields.consigneeContactName));
    }
    if (s.get(BolFields.freightCharges).isEmpty) {
      s.set(BolFields.freightCharges, BolFields.freightPrepaid);
    }
    return s;
  }

  Future<void> _openHistory() async {
    // Open immediately (like Address Book). Fetch the list inside the dialog.
    await _runExclusiveDialog<void>('history', () async {
      if (!mounted) return;
      await showDialog<void>(
        context: context,
        builder: (ctx) => _HistoryDialog(
          kind: _kind,
          sync: _documentHistorySync,
          onOpenPdf: (doc) async {
            Navigator.pop(ctx);
            await _openHistoryDocument(doc);
          },
          onTemplate: (doc) {
            Navigator.pop(ctx);
            unawaited(_applyHistoryTemplate(doc));
          },
        ),
      );
    });
  }

  Future<void> _applyHistoryTemplate(GeneratedDocumentRecord doc) async {
    final snap = await _documentHistorySync.downloadFormSnapshot(doc);
    if (!mounted) return;
    if (snap == null || !snap.hasFields) {
      showAppSnack(
        context,
        'No saved form data for this history item. '
        'Generate again once to store a template.',
      );
      return;
    }
    _selectKind(doc.kind);
    const skip = {
      LabelFields.palletNum,
      LabelFields.palletOf,
      LabelFields.boxNum,
      LabelFields.boxOf,
      BolFields.documentNumber,
    };
    for (final e in snap.fields.entries) {
      if (skip.contains(e.key)) continue;
      if (_controllers.containsKey(e.key)) {
        _setField(e.key, e.value);
      }
    }
    final mapRaw = snap.fields[LabelFields.soFieldMap] ?? '';
    _soFieldMap = SoFieldMap.decode(mapRaw);
    _logoPaths.clear();
    if (snap.logoCount > 0) {
      final logos = await _documentHistorySync.downloadSnapshotLogos(
        doc,
        snap.logoCount,
      );
      for (var i = 0; i < logos.length; i++) {
        try {
          final imported = await widget.storage.importLogoBytes(
            logos[i],
            preferredName: 'history_${doc.id}_$i.png',
            options: LogoImportOptions.standard(removeBackground: false),
          );
          _logoPaths.add(imported.file.path);
        } catch (_) {}
      }
    }
    if (doc.kind == LabelKind.bol) {
      _bolLineCount = _detectBolLineCount();
    }
    if (mounted) {
      setState(() {});
      showAppSnack(
        context,
        'Loaded template — edit fields, then Generate for new label counts.',
      );
    }
  }

  Future<void> _openHistoryDocument(GeneratedDocumentRecord doc) async {
    try {
      final file = await _documentHistorySync.downloadToCache(doc);
      await shareOrOpenFile(file: file);
    } on DocumentHistorySyncException catch (e) {
      if (mounted) showAppSnack(context, 'Open failed: ${e.message}');
    } catch (e) {
      if (mounted) showAppSnack(context, 'Open failed: $e');
    }
  }

  Future<void> _uploadGeneratedPdf({
    required LabelKind kind,
    required String fileName,
    required Uint8List bytes,
    required ShippingLabelData data,
  }) async {
    try {
      final record = await _documentHistorySync.upload(
        kind: kind,
        fileName: fileName,
        bytes: bytes,
        customer: data.get(LabelFields.customer).isNotEmpty
            ? data.get(LabelFields.customer)
            : data.get(BolFields.consigneeName),
        salesOrder: data.get(LabelFields.salesOrder).isNotEmpty
            ? data.get(LabelFields.salesOrder)
            : data.get(BolFields.orderNum),
        title: fileName,
        fields: Map<String, String>.from(data.values),
        logoBytes: await _loadSelectedLogoBytes(),
      );
      if (!record.snapshotSaved && mounted) {
        showAppSnack(
          context,
          'Cloud archive saved, but Template snapshot failed — '
          'Open PDF works; Generate again to store a template.',
        );
      }
    } on DocumentHistorySyncException catch (e) {
      if (mounted) {
        showAppSnack(context, 'Cloud archive: ${e.message}');
      }
    } catch (_) {
      if (mounted) {
        showAppSnack(context, 'Cloud archive failed — PDF saved locally.');
      }
    }
  }

  Future<void> _savePreset() async {
    final defaultName = _controllers[LabelFields.customer]!.text.trim().isEmpty
        ? (_presetName ?? 'New customer')
        : _controllers[LabelFields.customer]!.text.trim();
    final name = await _askString('Save preset', 'Preset name', defaultName);
    if (name == null || name.trim().isEmpty) return;

    final fields = <String, String>{};
    for (final key in presetKeysFor(_kind)) {
      fields[key] = _controllers[key]?.text.trim() ?? '';
    }

    final logoNames = <String>[];
    for (final logoPath in _logoPaths.take(maxCustomerLogos)) {
      final lp = File(logoPath);
      if (!await lp.exists()) continue;
      if (p.dirname(lp.path) == widget.storage.logosDir.path) {
        logoNames.add(p.basename(lp.path));
      } else {
        final imported = await widget.storage.importLogo(lp);
        logoNames.add(p.basename(imported.file.path));
      }
    }

    final displayName = name.trim();
    widget.storage.presets[_presetStorageKey(displayName)] = CustomerPreset(
      name: displayName,
      kind: _kind,
      fields: fields,
      logoFileNames: logoNames,
    );
    await widget.storage.savePresets();
    setState(() => _presetName = displayName);
    await _pushPresetQuietly(_kind, displayName);
    if (mounted) {
      showAppSnack(context, 'Saved preset “$displayName”.');
    }
  }

  Future<void> _deletePreset() async {
    final name = _presetName;
    if (name == null) return;
    await _runExclusiveDialog<void>('deletePreset', () async {
      final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('Delete preset'),
          content: Text('Delete preset “$name”?'),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('Delete'),
            ),
          ],
        ),
      );
      if (ok != true) return;
      widget.storage.presets.remove(_presetStorageKey(name));
      await widget.storage.savePresets();
      setState(() => _presetName = null);
      await _deletePresetQuietly(_kind, name);
    });
  }

  Future<String?> _askString(String title, String label, String initial) async {
    final ctrl = TextEditingController(text: initial);
    try {
      return await showDialog<String>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text(title),
          content: TextField(
            controller: ctrl,
            decoration: InputDecoration(labelText: label.toUpperCase()),
            autofocus: true,
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(ctx, ctrl.text),
              child: const Text('OK'),
            ),
          ],
        ),
      );
    } finally {
      ctrl.dispose();
    }
  }

  Future<List<String>> _pickImages({required bool multiple}) =>
      pickImagePaths(multiple: multiple);

  Future<void> _importBytesWithPrompt(
    Uint8List bytes, {
    required String preferredName,
    void Function(String path)? onImported,
  }) async {
    if (!mounted) return;
    final options = await showLogoImportEditDialog(
      context,
      previewBytes: bytes,
      // Always starts unchecked — perfecting a logo is a deliberate, paid,
      // slow action; it must never carry over as a silent default. Once a
      // customer's logo has been perfected the improved file is what's
      // reused going forward, so it should not need checking again.
      initialPerfectLogo: false,
    );
    if (options == null || !mounted) return;

    try {
      final result = await widget.storage.importLogoBytes(
        bytes,
        preferredName: preferredName,
        options: options,
        onLog: (line) => debugPrint('[logo] $line'),
      );
      if (!mounted) return;
      final file = result.file;
      if (onImported != null) {
        onImported(file.path);
      } else {
        setState(() {
          if (_logoPaths.length < maxCustomerLogos &&
              !_logoPaths.contains(file.path)) {
            _logoPaths.add(file.path);
          }
        });
      }
      if (options.perfectLogo) {
        unawaited(
          _perfectLogoInBackground(
            file.path,
            removeBackground: options.removeBackground,
          ),
        );
      }
    } catch (e) {
      if (mounted) showAppSnack(context, 'Logo import failed: $e');
    }
  }

  Future<void> _browseAndImportLogo() async {
    if (_logoPaths.length >= maxCustomerLogos) return;
    final paths = await _pickImages(multiple: false);
    if (paths.isEmpty || !mounted) return;
    final source = File(paths.first);
    final bytes = await source.readAsBytes();
    await _importBytesWithPrompt(
      bytes,
      preferredName: p.basename(source.path),
    );
  }

  Future<void> _addFromStorageAndImport() async {
    await _runExclusiveDialog<void>('logoStorage', () async {
    if (!mounted) return;
    var logos = widget.storage.listLogos();
    if (logos.isEmpty) {
      showAppSnack(context, 'No logos in storage yet — use Browse.');
      return;
    }

    final atLogoLimit = _logoPaths.length >= maxCustomerLogos;
    final picked = await showDialog<File>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDialogState) {
          logos = widget.storage.listLogos();
          return AlertDialog(
            title: const Text('Add from storage'),
            content: SizedBox(
              width: 420,
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxHeight: 360),
                child: logos.isEmpty
                    ? const Text('No logos in storage.')
                    : Column(
                        mainAxisSize: MainAxisSize.min,
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          if (atLogoLimit)
                            Padding(
                              padding: const EdgeInsets.only(bottom: 8),
                              child: Text(
                                'Logo slots full — delete below or remove a '
                                'selected logo to import another.',
                                style: TextStyle(
                                  fontSize: 12,
                                  color: SwiftColors.muted.withValues(alpha: 0.9),
                                ),
                              ),
                            ),
                          Flexible(
                            child: Scrollbar(
                              thumbVisibility: true,
                              interactive: true,
                              child: ListView(
                                shrinkWrap: true,
                                primary: true,
                                children: [
                                for (final f in logos)
                                  ListTile(
                                    leading: Image.file(
                                      f,
                                      width: 44,
                                      height: 44,
                                      fit: BoxFit.contain,
                                      errorBuilder: (_, _, _) =>
                                          const Icon(Icons.image_outlined),
                                    ),
                                    title: Text(p.basename(f.path)),
                                    subtitle: _logoPaths.contains(f.path)
                                        ? const Text(
                                            'Selected on this label',
                                            style: TextStyle(fontSize: 11),
                                          )
                                        : null,
                                    enabled: !atLogoLimit &&
                                        !_logoPaths.contains(f.path),
                                    onTap: !atLogoLimit &&
                                            !_logoPaths.contains(f.path)
                                        ? () => Navigator.pop(ctx, f)
                                        : null,
                                    trailing: IconButton(
                                      tooltip: 'Delete from storage',
                                      icon: const Icon(Icons.delete_outline),
                                      onPressed: () async {
                                        final name = p.basename(f.path);
                                        final ok = await showDialog<bool>(
                                          context: ctx,
                                          builder: (confirmCtx) => AlertDialog(
                                            title: const Text('Delete logo'),
                                            content: Text(
                                              'Delete “$name” from storage? '
                                              'Presets that used this logo will '
                                              'skip it on next load.',
                                            ),
                                            actions: [
                                              TextButton(
                                                onPressed: () =>
                                                    Navigator.pop(confirmCtx, false),
                                                child: const Text('Cancel'),
                                              ),
                                              FilledButton(
                                                onPressed: () =>
                                                    Navigator.pop(confirmCtx, true),
                                                child: const Text('Delete'),
                                              ),
                                            ],
                                          ),
                                        );
                                        if (ok != true || !ctx.mounted) return;
                                        final deleted =
                                            await widget.storage.deleteStoredLogo(f);
                                        if (!ctx.mounted) return;
                                        if (!deleted) {
                                          showAppSnack(ctx, 
                                                'Could not delete “$name”.',
                                              );
                                          return;
                                        }
                                        setState(() {
                                          _logoPaths.remove(f.path);
                                        });
                                        setDialogState(() {});
                                        if (!ctx.mounted) return;
                                        showAppSnack(ctx, 'Deleted “$name”.');
                                      },
                                    ),
                                  ),
                              ],
                              ),
                            ),
                          ),
                        ],
                      ),
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(ctx),
                child: const Text('Cancel'),
              ),
            ],
          );
        },
      ),
    );
    if (picked == null || !mounted) return;
    // Already in customer_logos — attach the existing file, never copy it.
    setState(() {
      if (_logoPaths.length < maxCustomerLogos &&
          !_logoPaths.contains(picked.path)) {
        _logoPaths.add(picked.path);
      }
    });
    });
  }

  Future<void> _showUploadManuallyMenu() async {
    if (_restoringLogo) return;
    await _runExclusiveDialog<void>('uploadManually', () async {
    if (!mounted) return;
    final action = await showDialog<String>(
      context: context,
      builder: (ctx) => SimpleDialog(
        title: const Text('Upload manually'),
        children: [
          SimpleDialogOption(
            onPressed: () => Navigator.pop(ctx, 'storage'),
            child: const ListTile(
              leading: Icon(Icons.folder_open),
              title: Text('Add from Storage'),
            ),
          ),
          SimpleDialogOption(
            onPressed: () => Navigator.pop(ctx, 'browse'),
            child: const ListTile(
              leading: Icon(Icons.folder_outlined),
              title: Text('Browse'),
            ),
          ),
        ],
      ),
    );
    if (action == 'storage') {
      await _addFromStorageAndImport();
    } else if (action == 'browse') {
      await _browseAndImportLogo();
    }
    });
  }

  Future<void> _findLogoOnWeb() async {
    if (_findingLogo || _isDialogLocked('findLogo')) return;
    if (_logoPaths.length >= maxCustomerLogos) {
      showAppSnack(context, 'Already have 2 logos. Remove one to find another.');
      return;
    }

    await _runExclusiveDialog<void>('findLogo', () async {
    final nameCtrl = TextEditingController(
      text: _controllers[LabelFields.customer]?.text.trim() ?? '',
    );
    final domainCtrl = TextEditingController();
    final retoolConfigured = LogoSearchEngine.retoolClearbitConfigured();
    final engineOptions =
        LogoSearchEngine.pickerOptions(retoolConfigured: retoolConfigured);
    // Prefer default immediately so the dialog is not blocked on prefs I/O.
    var selectedEngine = LogoSearchEngine.defaultEngine;
    if (!engineOptions.contains(selectedEngine)) {
      selectedEngine = engineOptions.first;
    }

    if (!mounted) return;
    // Wait for Tools MenuBar overlay to dismiss before presenting.
    await Future<void>.delayed(const Duration(milliseconds: 30));
    if (!mounted) return;
    await WidgetsBinding.instance.endOfFrame;
    if (!mounted) return;

    // MenuItemButton sets an inherited anchor near the menu (bottom-right).
    // Force the dialog anchor to the window center so DisplayFeatureSubScreen
    // cannot pin the card off-screen.
    final viewSize = MediaQuery.sizeOf(context);
    final confirmed = await showGeneralDialog<bool>(
      context: context,
      barrierDismissible: true,
      barrierLabel: 'Dismiss find logo',
      barrierColor: Colors.transparent,
      useRootNavigator: true,
      anchorPoint: Offset(viewSize.width / 2, viewSize.height / 2),
      transitionDuration: const Duration(milliseconds: 120),
      pageBuilder: (ctx, animation, secondaryAnimation) {
        // SizedBox.expand + Center (no MediaQuery math). A Positioned-only
        // Stack collapses to bottom-right on Windows; MediaQuery width can
        // also exceed the window and push the card off-screen.
        return SizedBox.expand(
          child: Stack(
            children: [
              Positioned.fill(
                child: GestureDetector(
                  behavior: HitTestBehavior.opaque,
                  onTap: () => Navigator.pop(ctx, false),
                  child: const ColoredBox(color: Color(0x8A000000)),
                ),
              ),
              Center(
                child: Material(
                  color: Theme.of(ctx).dialogTheme.backgroundColor ??
                      Theme.of(ctx).colorScheme.surface,
                  elevation: 8,
                  borderRadius: BorderRadius.circular(12),
                  clipBehavior: Clip.antiAlias,
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 400),
                    child: Padding(
                      padding: const EdgeInsets.fromLTRB(20, 18, 20, 14),
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Text(
                            'Find logo on the web',
                            style: Theme.of(ctx).textTheme.titleLarge,
                          ),
                          const SizedBox(height: 10),
                          const Text(
                            'Search Clearbit, Brandfetch, and Serper image results. '
                            'A website domain improves accuracy.',
                            style: TextStyle(
                              fontSize: 13,
                              color: SwiftColors.muted,
                            ),
                          ),
                          const SizedBox(height: 14),
                          TextField(
                            controller: nameCtrl,
                            decoration: const InputDecoration(
                              labelText: 'CUSTOMER / COMPANY',
                            ),
                            autofocus: true,
                            onSubmitted: (_) => Navigator.pop(ctx, true),
                          ),
                          const SizedBox(height: 10),
                          TextField(
                            controller: domainCtrl,
                            decoration: const InputDecoration(
                              labelText: 'WEBSITE DOMAIN (OPTIONAL)',
                              hintText: 'e.g. conocophillips.com',
                            ),
                            onSubmitted: (_) => Navigator.pop(ctx, true),
                          ),
                          const SizedBox(height: 8),
                          Text(
                            'Source: ${selectedEngine.label}  (Tools → Logo search engine…)',
                            style: const TextStyle(
                              fontSize: 12,
                              color: SwiftColors.muted,
                            ),
                          ),
                          const SizedBox(height: 16),
                          Row(
                            mainAxisAlignment: MainAxisAlignment.end,
                            children: [
                              TextButton(
                                onPressed: () => Navigator.pop(ctx, false),
                                child: const Text('Cancel'),
                              ),
                              const SizedBox(width: 8),
                              FilledButton(
                                onPressed: () => Navigator.pop(ctx, true),
                                child: const Text('Search'),
                              ),
                            ],
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
              ),
            ],
          ),
        );
      },
    );

    if (confirmed != true || !mounted) return;

    await widget.storage.saveLogoSearchEngine(selectedEngine.id);

    setState(() => _findingLogo = true);
    try {
      final finder = LogoFinder();
      final rawCandidates = await finder.findDownloadedCandidates(
        companyName: nameCtrl.text,
        domain: domainCtrl.text,
        engine: selectedEngine,
      );
      final candidates = LogoFinder.filterForPicker(rawCandidates);
      if (!mounted) return;
      if (candidates.isEmpty) {
        showAppSnack(context, 
              'No logo found from Clearbit, Brandfetch, or Serper. '
              'Try a website domain or upload manually.',
            );
        setState(() => _findingLogo = false);
        return;
      }

      final picked = await showDialog<LogoDownloadedCandidate>(
        context: context,
        builder: (ctx) {
          final mq = MediaQuery.of(ctx);
          final maxW = (mq.size.width - 32).clamp(280.0, 560.0);
          final maxH = (mq.size.height -
                  mq.padding.vertical -
                  mq.viewInsets.bottom -
                  96)
              .clamp(220.0, mq.size.height);
          // Leave room for title + actions inside the dialog.
          final gridH = (maxH * 0.72).clamp(160.0, 520.0);
          return AlertDialog(
            insetPadding:
                const EdgeInsets.symmetric(horizontal: 16, vertical: 24),
            title: const Text('Choose a logo'),
            content: SizedBox(
              width: maxW,
              height: gridH + 36,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(
                    'Top ${candidates.length} of up to ${LogoFinder.pickerMaxResults} '
                    'result${candidates.length == 1 ? '' : 's'} — tap a thumbnail',
                    style:
                        const TextStyle(fontSize: 13, color: SwiftColors.muted),
                  ),
                  const SizedBox(height: 12),
                  Expanded(
                    child: Scrollbar(
                      thumbVisibility: true,
                      interactive: true,
                      child: SingleChildScrollView(
                        primary: true,
                        child: Wrap(
                          spacing: 8,
                          runSpacing: 8,
                          children: [
                            for (final c in candidates)
                              InkWell(
                                onTap: () => Navigator.pop(ctx, c),
                                borderRadius: BorderRadius.circular(8),
                                child: Container(
                                  width: 96,
                                  padding: const EdgeInsets.all(6),
                                  decoration: BoxDecoration(
                                    border:
                                        Border.all(color: SwiftColors.muted),
                                    borderRadius: BorderRadius.circular(8),
                                  ),
                                  child: Column(
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      Image.memory(
                                        c.bytes,
                                        height: 56,
                                        fit: BoxFit.contain,
                                        errorBuilder: (_, _, _) =>
                                            const Icon(
                                          Icons.broken_image_outlined,
                                          size: 40,
                                        ),
                                      ),
                                      const SizedBox(height: 4),
                                      Text(
                                        c.source,
                                        style: const TextStyle(fontSize: 10),
                                        textAlign: TextAlign.center,
                                        maxLines: 2,
                                        overflow: TextOverflow.ellipsis,
                                      ),
                                    ],
                                  ),
                                ),
                              ),
                          ],
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(ctx),
                child: const Text('Cancel'),
              ),
            ],
          );
        },
      );
      if (picked == null || !mounted) return;
      final chosen = picked;

      final base = widget.storage.safeCustomerName(
        nameCtrl.text.trim().isEmpty ? 'logo' : nameCtrl.text.trim(),
      );
      final ext = LogoFinder.extensionForBytes(chosen.bytes);
      await _importBytesWithPrompt(
        chosen.bytes,
        preferredName: '$base$ext',
      );
      if (!mounted) return;
      final hint = chosen.hint.isEmpty ? '' : '\n${chosen.hint}';
      showAppSnack(context, 'Logo from ${chosen.source}.$hint');
    } catch (e) {
      if (mounted) {
        showAppSnack(context, 'Web find failed: $e. Try Upload manually.');
      }
    } finally {
      if (mounted) setState(() => _findingLogo = false);
    }
    });
  }

  void _removeLogoAt(int index) {
    if (index < 0 || index >= _logoPaths.length) return;
    setState(() => _logoPaths.removeAt(index));
  }

  Future<void> _replaceLogoAt(int index) async {
    final paths = await _pickImages(multiple: false);
    if (paths.isEmpty) return;
    final source = File(paths.first);
    final bytes = await source.readAsBytes();
    await _importBytesWithPrompt(
      bytes,
      preferredName: p.basename(source.path),
      onImported: (path) {
        setState(() {
          if (index >= 0 && index < _logoPaths.length) {
            _logoPaths[index] = path;
          }
        });
      },
    );
  }

  /// Runs [LogoPerfectRestore] on the just-imported logo.
  ///
  /// Only when critics **verify** the result do we overwrite the original
  /// path in place (+ sibling `.svg` when vectorization produced one). That
  /// overwrite is what "delete the original, redirect to the new one" means:
  /// presets and the next Supabase upsert keep the same path.
  ///
  /// Unverified / best-effort attempts write a sibling `*_perfect_review.png`
  /// and leave the original untouched so a failed Perfect cannot destroy the
  /// user's source logo.
  Future<void> _perfectLogoInBackground(
    String path, {
    required bool removeBackground,
  }) async {
    final source = File(path);
    if (!await source.exists()) return;
    final startedEpoch = LogoPerfectRestore.epoch;

    if (mounted) setState(() => _restoreInFlight++);
    try {
      final sourceBytes = await source.readAsBytes();
      final result = await LogoPerfectRestore.run(
        sourceBytes,
        removeBackground: removeBackground,
        onLog: (line) => debugPrint('[perfect_logo] $line'),
      );
      if (startedEpoch != LogoPerfectRestore.epoch || !mounted) return;

      if (result.verified) {
        await source.writeAsBytes(result.png, flush: true);
        if (result.svg != null) {
          await File(p.setExtension(path, '.svg'))
              .writeAsString(result.svg!, flush: true);
        }
        setState(() {}); // repaint the thumbnail with the new bytes
        if (mounted) {
          showAppSnack(
            context,
            'Logo perfected (verified by ${result.criticsLabel}).',
          );
        }
        return;
      }

      // Best-effort only — keep the original; save a review sibling.
      final reviewPath = p.join(
        p.dirname(path),
        '${p.basenameWithoutExtension(path)}_perfect_review.png',
      );
      await File(reviewPath).writeAsBytes(result.png, flush: true);
      if (mounted) {
        showAppSnack(
          context,
          'Perfect did not pass review after ${result.attempts} tries '
          '(${result.criticsLabel}). Original kept — review file saved as '
          '${p.basename(reviewPath)}.',
        );
      }
    } catch (e) {
      debugPrint('[perfect_logo] failed: $e');
      if (mounted) {
        showAppSnack(context, 'Perfect this logo failed: $e');
      }
    } finally {
      if (mounted) {
        setState(() => _restoreInFlight = (_restoreInFlight - 1).clamp(0, 99));
      }
    }
  }

  void _abortLogoRestore() {
    LogoPerfectRestore.cancelAll();
    if (mounted) setState(() => _restoreInFlight = 0);
  }

  Future<List<Uint8List>> _loadSelectedLogoBytes() async {
    final out = <Uint8List>[];
    final paths = _logoPaths.take(maxCustomerLogos).toList();
    for (final path in paths) {
      final file = File(path);
      if (await file.exists()) {
        out.add(await file.readAsBytes());
      }
    }
    return out;
  }

  Future<PieceCountPlan?> _askPieceCounts() async {
    return _runExclusiveDialog<PieceCountPlan>('pieceCounts', () async {
    final palletCtrl = TextEditingController(text: '1');
    final boxCtrl = TextEditingController(text: '0');
    var error = '';
    try {
    return await showDialog<PieceCountPlan>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setLocal) => AlertDialog(
          title: const Text('How many labels?'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text(
                'Enter how many Pallet/Crate and Box labels to print. '
                'Each unit gets its own page (1 of N, 2 of N, …).',
                style: TextStyle(fontSize: 13, color: SwiftColors.muted),
              ),
              const SizedBox(height: 14),
              TextField(
                controller: palletCtrl,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(
                  labelText: 'PALLET / CRATE LABELS',
                ),
                autofocus: true,
              ),
              const SizedBox(height: 10),
              TextField(
                controller: boxCtrl,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(
                  labelText: 'BOX LABELS',
                ),
              ),
              if (error.isNotEmpty) ...[
                const SizedBox(height: 10),
                Text(
                  error,
                  style: const TextStyle(color: SwiftColors.accent, fontSize: 13),
                ),
              ],
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () {
                int parseCount(String raw) {
                  final t = raw.trim();
                  if (t.isEmpty) return 0;
                  return int.tryParse(t) ?? -1;
                }

                final pallets = parseCount(palletCtrl.text);
                final boxes = parseCount(boxCtrl.text);
                if (pallets < 0 || boxes < 0) {
                  setLocal(() => error = 'Enter whole numbers only.');
                  return;
                }
                if (pallets + boxes <= 0) {
                  setLocal(
                    () => error = 'Enter at least one pallet/crate or box.',
                  );
                  return;
                }
                if (pallets + boxes > 200) {
                  setLocal(() => error = 'Keep total labels at 200 or fewer.');
                  return;
                }
                Navigator.pop(
                  ctx,
                  PieceCountPlan(palletCrates: pallets, boxes: boxes),
                );
              },
              child: const Text('Generate'),
            ),
          ],
        ),
      ),
    );
    } finally {
      palletCtrl.dispose();
      boxCtrl.dispose();
    }
    });
  }

  Future<Map<String, PieceCountPlan>?> _askPieceCountsPerSalesOrder({
    required List<String> salesOrders,
    required PieceCountPlan total,
  }) async {
    final palletCtrls = {
      for (final so in salesOrders)
        so: TextEditingController(
          text: so == salesOrders.first ? '${total.palletCrates}' : '0',
        ),
    };
    final boxCtrls = {
      for (final so in salesOrders)
        so: TextEditingController(
          text: so == salesOrders.first ? '${total.boxes}' : '0',
        ),
    };
    var error = '';
    final result = await showDialog<Map<String, PieceCountPlan>>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setLocal) => AlertDialog(
          title: const Text('Labels per sales order'),
          content: SizedBox(
            width: 520,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(
                    'Split ${total.palletCrates} skid/crate and ${total.boxes} box '
                    'labels across sales orders.',
                    style: const TextStyle(fontSize: 13, color: SwiftColors.muted),
                  ),
                  const SizedBox(height: 12),
                  for (final so in salesOrders) ...[
                    Text(so, style: const TextStyle(fontWeight: FontWeight.w600)),
                    Text(
                      [
                        if (_soFieldMap.valueFor(LabelFields.poNum, so).isNotEmpty)
                          'PO: ${_soFieldMap.valueFor(LabelFields.poNum, so)}',
                        if (_soFieldMap.valueFor(BolFields.packingList, so).isNotEmpty)
                          'PL: ${_soFieldMap.valueFor(BolFields.packingList, so)}'
                        else if (_soFieldMap
                            .valueFor(LabelFields.packingSlip, so)
                            .isNotEmpty)
                          'PL: ${_soFieldMap.valueFor(LabelFields.packingSlip, so)}',
                        if (_soFieldMap.valueFor(LabelFields.project, so).isNotEmpty)
                          'Project: ${_soFieldMap.valueFor(LabelFields.project, so)}',
                      ].join(' · '),
                      style: const TextStyle(
                        fontSize: 12,
                        color: SwiftColors.muted,
                      ),
                    ),
                    const SizedBox(height: 6),
                    LayoutBuilder(
                      builder: (context, constraints) {
                        final pallets = TextField(
                          controller: palletCtrls[so],
                          keyboardType: TextInputType.number,
                          decoration: const InputDecoration(
                            labelText: 'SKIDS / CRATES',
                          ),
                        );
                        final boxes = TextField(
                          controller: boxCtrls[so],
                          keyboardType: TextInputType.number,
                          decoration: const InputDecoration(
                            labelText: 'BOXES',
                          ),
                        );
                        // Side by side needs room for the longer label; below
                        // that, stack so it doesn't wrap into an unreadable mess.
                        if (constraints.maxWidth < 280) {
                          return Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              pallets,
                              const SizedBox(height: 10),
                              boxes,
                            ],
                          );
                        }
                        return Row(
                          children: [
                            Expanded(child: pallets),
                            const SizedBox(width: 10),
                            Expanded(child: boxes),
                          ],
                        );
                      },
                    ),
                    const SizedBox(height: 14),
                  ],
                  if (error.isNotEmpty)
                    Text(
                      error,
                      style: const TextStyle(
                        color: SwiftColors.accent,
                        fontSize: 13,
                      ),
                    ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () {
                int parseCount(String raw) {
                  final t = raw.trim();
                  if (t.isEmpty) return 0;
                  return int.tryParse(t) ?? -1;
                }

                var pallets = 0;
                var boxes = 0;
                final plans = <String, PieceCountPlan>{};
                for (final so in salesOrders) {
                  final p = parseCount(palletCtrls[so]!.text);
                  final b = parseCount(boxCtrls[so]!.text);
                  if (p < 0 || b < 0) {
                    setLocal(() => error = 'Enter whole numbers only.');
                    return;
                  }
                  pallets += p;
                  boxes += b;
                  plans[so] = PieceCountPlan(palletCrates: p, boxes: b);
                }
                if (pallets != total.palletCrates || boxes != total.boxes) {
                  setLocal(
                    () => error =
                        'Totals must equal ${total.palletCrates} skids and ${total.boxes} boxes.',
                  );
                  return;
                }
                Navigator.pop(ctx, plans);
              },
              child: const Text('Generate'),
            ),
          ],
        ),
      ),
    );
    for (final c in palletCtrls.values) {
      c.dispose();
    }
    for (final c in boxCtrls.values) {
      c.dispose();
    }
    return result;
  }

  void _loadSample() {
    if (_kind == LabelKind.bulk) {
      _loadBulkSample();
      return;
    }
    final s = switch (_kind) {
      LabelKind.receiving => ShippingLabelData.receivingSample,
      LabelKind.bol => ShippingLabelData.bolSample,
      LabelKind.shipping => ShippingLabelData.sample,
      LabelKind.bulk => ShippingLabelData.sample,
    };
    for (final e in s.values.entries) {
      _setField(e.key, e.value);
    }
    // Mirror ship-to into BOL consignee when loading BOL sample
    if (_kind == LabelKind.bol) {
      final name = _controllers[BolFields.consigneeName]?.text ?? '';
      if (name.isNotEmpty) _setField(LabelFields.shipTo, name);
      _bolLineCount = _detectBolLineCount();
    }
    setState(() {});
  }

  Future<void> _loadBulkSample() async {
    setState(() => _bulkParsing = true);
    try {
      final fixture = File(
        p.join(
          Directory.current.path,
          'test',
          'fixtures',
          'propak_order_ack_sample.pdf',
        ),
      );
      if (await fixture.exists()) {
        await _parseBulkPdf(fixture.path);
        return;
      }
      if (!mounted) return;
      setState(() => _bulkParsing = false);
      showAppSnack(context, 
            'Sample OA not found — upload an Order Acknowledgement PDF.',
          );
    } catch (e) {
      if (!mounted) return;
      setState(() => _bulkParsing = false);
      showAppSnack(context, 'Sample load failed: $e');
    }
  }

  Future<void> _pickBulkPdf() async {
    final path = await pickPdfPath();
    if (path == null) return;
    await _parseBulkPdf(path);
  }

  Future<void> _parseBulkPdf(String path) async {
    setState(() => _bulkParsing = true);
    try {
      final bytes = await File(path).readAsBytes();
      var parsed = await parseOrderAckPdf(
        Uint8List.fromList(bytes),
        sourceFileName: p.basename(path),
      );
      final text = await extractOrderAckPdfText(Uint8List.fromList(bytes));
      parsed = await JobPdfAi().enrich(parsed, text);
      // Claude "common sense" pass — always runs, always attaches its
      // reasoning to each line (shown in the review table below); never
      // silently overrides a value the regex parser already found.
      parsed = await JobPdfAi().enrichLines(parsed, text);
      if (!mounted) return;

      if (parsed.hasIncompleteLines) {
        setState(() => _bulkParsing = false);
        final action = await _promptBulkMissingIdentity(parsed);
        if (!mounted) return;
        if (action == null || action == BulkMissingIdAction.cancel) {
          setState(() {
            _bulkParse = null;
            _bulkSourcePath = null;
          });
          showAppSnack(context, 'Bulk label upload cancelled.');
          return;
        }
        parsed = parsed.applyingMissingIdAction(action);
      }

      setState(() {
        _bulkParse = parsed;
        _bulkSourcePath = path;
        _bulkParsing = false;
      });
      if (parsed.lines.isEmpty && mounted) {
        showAppSnack(context, 
              parsed.warnings.isEmpty
                  ? 'No label lines found in that PDF.'
                  : parsed.warnings.first,
            );
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _bulkParsing = false;
        _bulkParse = null;
      });
      showAppSnack(context, 'Could not read OA or packing list: $e');
    }
  }

  Future<void> _pickOrderAckForForm() async {
    final multi = _kind == LabelKind.bol && _bolMultiPdf;
    final paths = await pickPdfPaths(multiple: multi);
    if (paths.isEmpty) return;
    await _fillFormFromJobPdfs(paths);
  }

  Future<void> _fillFormFromJobPdfs(List<String> paths) async {
    setState(() => _oaFilling = true);
    try {
      final parsed = <OrderAckParseResult>[];
      for (final path in paths) {
        final bytes = await File(path).readAsBytes();
        final text = await extractOrderAckPdfText(Uint8List.fromList(bytes));
        var one = const OrderAckParser().parseText(
          text,
          sourceFileName: p.basename(path),
        );
        one = await JobPdfAi().enrich(one, text);
        parsed.add(one);
      }
      if (!mounted) return;
      final merged = _mergeJobPdfFills(parsed);
      await _applyOrderAckToForm(
        merged,
        parsed.map((e) => e.sourceFileName).where((s) => s.isNotEmpty).join(', '),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() => _oaFilling = false);
      showAppSnack(context, 'Could not read PDF: $e');
    }
  }

  OrderAckParseResult _mergeJobPdfFills(List<OrderAckParseResult> items) {
    if (items.length == 1) return items.first;
    String j(String Function(OrderAckParseResult e) read) =>
        joinJobPdfValues(items.map(read));
    final anyDelivery = items.any((e) => e.hasDeliveryShipTo);
    return OrderAckParseResult(
      poNumber: j((e) => e.poNumber),
      orderNumber: j((e) => e.orderNumber),
      lines: const [],
      warnings: [for (final e in items) ...e.warnings],
      sourceFileName: items.map((e) => e.sourceFileName).join(', '),
      customerName: j((e) => e.customerName),
      projectNumber: j((e) => e.projectNumber),
      jobLocation: j((e) => e.jobLocation),
      requisitioner: j((e) => e.requisitioner),
      afeNumber: j((e) => e.afeNumber),
      deliveryShipToName: j((e) => e.deliveryShipToName),
      deliveryShipToAddress: j((e) => e.deliveryShipToAddress),
      headerShipToName: j((e) => e.headerShipToName),
      headerShipToAddress: j((e) => e.headerShipToAddress),
      deliveryCarrier: j((e) => e.deliveryCarrier),
      hasDeliveryShipTo: anyDelivery,
      packingSlipNumber: j((e) => e.packingSlipNumber),
      documentKind: items.any((e) => e.documentKind == 'packing_list')
          ? 'packing_list'
          : 'order_ack',
    );
  }

  Future<void> _applyOrderAckToForm(
    OrderAckParseResult parsed,
    String sourceName,
  ) async {
    if (parsed.customerName.isNotEmpty) {
      _setField(LabelFields.customer, parsed.customerName);
    }
    if (parsed.orderNumber.isNotEmpty) {
      _setField(LabelFields.salesOrder, parsed.orderNumber);
      _setField(BolFields.orderNum, parsed.orderNumber);
    }
    if (parsed.projectNumber.isNotEmpty) {
      _setField(LabelFields.project, parsed.projectNumber);
    }
    if (parsed.poNumber.isNotEmpty) {
      _setField(LabelFields.poNum, parsed.poNumber);
    }
    if (parsed.requisitioner.isNotEmpty && _kind == LabelKind.shipping) {
      _setField(LabelFields.attn, parsed.requisitioner);
    }
    final special = parsed.specialInstructionsHint;
    if (special.isNotEmpty) {
      _setField(LabelFields.specialInstructions, special);
    }
    if (parsed.packingSlipNumber.isNotEmpty) {
      _setField(LabelFields.packingSlip, parsed.packingSlipNumber);
      _setField(BolFields.packingList, parsed.packingSlipNumber);
    }
    if (parsed.deliveryCarrier.isNotEmpty && _kind == LabelKind.shipping) {
      _setField(
        LabelFields.carrier,
        _carrierFromDeliveryLine(parsed.deliveryCarrier),
      );
      final lower = parsed.deliveryCarrier.toLowerCase();
      if (lower.contains('collect')) {
        _setField(BolFields.freightCharges, BolFields.freightCollect);
      } else if (lower.contains('prepaid')) {
        _setField(BolFields.freightCharges, BolFields.freightPrepaid);
      }
    }

    var shipName = parsed.deliveryShipToName;
    var shipAddr = parsed.deliveryShipToAddress;
    final wantsShipTo =
        _kind == LabelKind.shipping || _kind == LabelKind.bol;
    if (wantsShipTo && !parsed.hasDeliveryShipTo) {
      setState(() => _oaFilling = false);
      final choice = await _promptMissingDeliveryInstructions(parsed);
      if (!mounted) return;
      if (choice == _OaShipToChoice.useHeader) {
        shipName = parsed.headerShipToName;
        shipAddr = parsed.headerShipToAddress;
      } else {
        shipName = '';
        shipAddr = '';
      }
    }

    if (wantsShipTo && (shipName.isNotEmpty || shipAddr.isNotEmpty)) {
      if (_kind == LabelKind.shipping) {
        if (shipName.isNotEmpty) _setField(LabelFields.shipTo, shipName);
        if (shipAddr.isNotEmpty) _setField(LabelFields.location, shipAddr);
      } else if (_kind == LabelKind.bol) {
        if (shipName.isNotEmpty) {
          _setField(BolFields.consigneeName, shipName);
        }
        if (shipAddr.isNotEmpty) {
          _setField(BolFields.consigneeAddress, shipAddr);
        }
      }
    }

    setState(() {
      _oaFillSourceName = sourceName;
      _oaFilling = false;
    });
    if (!mounted) return;
    showAppSnack(
      context,
      parsed.documentKind == 'packing_list'
          ? 'Filled from packing list PDF.'
          : parsed.hasDeliveryShipTo
              ? 'Filled from Order Acknowledgement (Delivery Instructions).'
              : 'Filled from Order Acknowledgement.',
    );
  }

  String _carrierFromDeliveryLine(String raw) {
    var s = raw.replaceAll(RegExp(r'\s+'), ' ').trim();
    s = s.replaceAll(
      RegExp(r'^ship\s+via\s+', caseSensitive: false),
      '',
    );
    s = s.replaceAll(
      RegExp(r'\s*[-–]\s*(collect|prepaid)\b', caseSensitive: false),
      '',
    );
    s = s.replaceAll(
      RegExp(r'\b(collect|prepaid)\b', caseSensitive: false),
      '',
    );
    s = s.replaceAll(RegExp(r'\s+'), ' ').trim();
    return s.isEmpty ? raw.trim() : s;
  }

  Future<_OaShipToChoice?> _promptMissingDeliveryInstructions(
    OrderAckParseResult parsed,
  ) {
    final preview = [
      if (parsed.headerShipToName.isNotEmpty) parsed.headerShipToName,
      if (parsed.headerShipToAddress.isNotEmpty) parsed.headerShipToAddress,
    ].join('\n');
    return showDialog<_OaShipToChoice>(
      context: context,
      barrierDismissible: false,
      builder: (ctx) {
        return AlertDialog(
          title: const Text('No Delivery Instructions'),
          content: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 440),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'This Order Acknowledgement has no Delivery Instructions '
                  'block (the c/o / actual ship-to). The printed Ship To is '
                  'often the billing address without c/o.',
                ),
                if (preview.isNotEmpty) ...[
                  const SizedBox(height: 12),
                  Text(
                    'OA Ship To:\n$preview',
                    style: const TextStyle(height: 1.35),
                  ),
                ],
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () =>
                  Navigator.pop(ctx, _OaShipToChoice.manual),
              child: const Text('Enter manually'),
            ),
            FilledButton(
              onPressed: preview.trim().isEmpty
                  ? null
                  : () => Navigator.pop(ctx, _OaShipToChoice.useHeader),
              child: const Text('Use OA Ship To'),
            ),
          ],
        );
      },
    );
  }

  void _clearOrderAckFill() {
    setState(() => _oaFillSourceName = null);
  }

  Widget _buildOrderAckFillCard({bool dense = false}) {
    return _Card(
      title: 'Upload OA or Packing List',
      hint: dense
          ? 'Upload a Swift OA or packing list PDF'
          : 'Order Acknowledgement or packing list. Packing lists fill Swift Packing Slip No. '
              'Ship To comes from Delivery Instructions when present.',
      dense: dense,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (_kind == LabelKind.bol)
            CheckboxListTile(
              contentPadding: EdgeInsets.zero,
              dense: true,
              value: _bolMultiPdf,
              onChanged: _busy || _oaFilling
                  ? null
                  : (v) => setState(() => _bolMultiPdf = v ?? false),
              title: const Text('Upload multiple PDFs (same customer / BOL)'),
              subtitle: const Text(
                'Joins different values with  /  (e.g. two sales orders)',
              ),
            ),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              FilledButton.tonalIcon(
                onPressed: _busy || _oaFilling ? null : _pickOrderAckForForm,
                icon: _oaFilling
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.picture_as_pdf_outlined, size: 18),
                label: Text(
                  _oaFilling
                      ? 'Reading…'
                      : (_kind == LabelKind.bol && _bolMultiPdf)
                          ? 'Upload PDFs'
                          : 'Upload OA or Packing List',
                ),
              ),
              if (_oaFillSourceName != null)
                TextButton(
                  onPressed: _busy ? null : _clearOrderAckFill,
                  child: const Text('Clear'),
                ),
            ],
          ),
          if (_oaFillSourceName != null) ...[
            SizedBox(height: dense ? 6 : 8),
            Text(
              _oaFillSourceName!,
              maxLines: 3,
              overflow: TextOverflow.ellipsis,
            ),
          ],
        ],
      ),
    );
  }

  List<Widget> _presetOaLogoCards({
    bool dense = false,
    bool stackLogoActions = true,
  }) {
    if (_kind == LabelKind.bulk) return const [];
    return [
      _buildPresetCard(dense: dense),
      _buildOrderAckFillCard(dense: dense),
      _buildLogosCard(dense: dense, stackActions: stackLogoActions),
    ];
  }

  /// Ask what to do with OA lines that have CPO but no TAG# / PART# / ITEM#.
  Future<BulkMissingIdAction?> _promptBulkMissingIdentity(
    OrderAckParseResult parsed,
  ) async {
    final incomplete = parsed.incompleteLines;
    final cpoList = incomplete.map((l) => '#${l.cpoDisplay}').join(', ');
    final lineSummary = incomplete.length == 1
        ? 'Line CPO $cpoList is missing a TAG#, PART#, or ITEM# on the Order '
            'Acknowledgement.'
        : '${incomplete.length} lines are missing a TAG#, PART#, or ITEM# on '
            'the Order Acknowledgement (CPO $cpoList).';

    return showDialog<BulkMissingIdAction>(
      context: context,
      barrierDismissible: false,
      builder: (ctx) {
        final chrome = SwiftChromeColors.of(ctx);
        return AlertDialog(
          title: const Text('Missing TAG# / PART# / ITEM#'),
          content: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 440),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  lineSummary,
                  style: TextStyle(
                    fontFamily: swiftUiFont(ctx),
                    fontSize: 14,
                    height: 1.35,
                    color: chrome.ink,
                  ),
                ),
                const SizedBox(height: 12),
                Text(
                  'Please check with the PM before deciding. You can still '
                  'proceed with blank identity fields (edit them in Word), '
                  'skip these lines, or cancel the upload. Lines Claude '
                  'already filled appear italic with a * in the review table '
                  '— confirm those before printing.',
                  style: TextStyle(
                    fontFamily: swiftUiFont(ctx),
                    fontSize: 13,
                    height: 1.35,
                    color: chrome.muted,
                  ),
                ),
                if (incomplete.length <= 8) ...[
                  const SizedBox(height: 12),
                  for (final inc in incomplete)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 4),
                      child: Text(
                        '• CPO #${inc.cpoDisplay}  ·  qty ${inc.quantity}  ·  '
                        '${inc.reason}',
                        style: TextStyle(
                          fontFamily: swiftUiFont(ctx),
                          fontSize: 12,
                          color: SwiftColors.accent,
                        ),
                      ),
                    ),
                ],
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () =>
                  Navigator.of(ctx).pop(BulkMissingIdAction.cancel),
              child: const Text('Cancel'),
            ),
            TextButton(
              onPressed: () => Navigator.of(ctx).pop(BulkMissingIdAction.skip),
              child: const Text('Skip'),
            ),
            FilledButton(
              onPressed: () =>
                  Navigator.of(ctx).pop(BulkMissingIdAction.proceed),
              child: const Text('Proceed'),
            ),
          ],
        );
      },
    );
  }

  Widget _buildBulkPrintModeControl(BulkLabelLine line) {
    final chrome = SwiftChromeColors.of(context);
    return SizedBox(
      width: 170,
      child: DropdownButtonHideUnderline(
        child: DropdownButton<BulkPrintMode>(
          isDense: true,
          isExpanded: true,
          value: line.printMode,
          style: TextStyle(
            fontFamily: swiftUiFont(context),
            fontSize: 12,
            color: chrome.ink,
          ),
          items: [
            DropdownMenuItem(
              value: BulkPrintMode.single,
              child: Text('1 tag (qty ${line.quantity})'),
            ),
            DropdownMenuItem(
              value: BulkPrintMode.perUnit,
              child: Text('1 tag per unit (${line.labelCount})'),
            ),
          ],
          onChanged: (mode) {
            if (mode != null) _setBulkLinePrintMode(line, mode);
          },
        ),
      ),
    );
  }

  void _setBulkLinePrintMode(BulkLabelLine line, BulkPrintMode mode) {
    final parsed = _bulkParse;
    if (parsed == null || line.printMode == mode) return;
    setState(() {
      _bulkParse = parsed.replacingLine(line.copyWith(printMode: mode));
    });
  }

  void _showBulkAiNote(BulkLabelLine line) {
    showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('CPO #${line.cpoDisplay} — Claude’s reasoning'),
        content: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 420),
          child: Text(line.aiNote ?? ''),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Close'),
          ),
        ],
      ),
    );
  }

  /// Tap-to-edit the Kind/Identity cells — the only way to actually correct
  /// (or explicitly confirm) an AI-suggested TAG#/PART#/ITEM# before
  /// Generate, and also lets the user fix a regex misparse. Saving with a
  /// non-empty value always clears [BulkLabelLine.missingIdentity] since the
  /// user has now looked at it; clearing it back to blank keeps the line
  /// flagged (still needs a real value from the PM).
  Future<void> _editBulkLineIdentity(BulkLabelLine line) async {
    if (_bulkParse == null) return;
    final controller = TextEditingController(text: line.tagOrPart);
    var kind = line.idKind;
    final saved = await showDialog<bool>(
      context: context,
      builder: (ctx) {
        return StatefulBuilder(
          builder: (ctx, setLocalState) {
            final chrome = SwiftChromeColors.of(ctx);
            return AlertDialog(
              title: Text('CPO #${line.cpoDisplay} — confirm identity'),
              content: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 380),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if ((line.aiNote ?? '').trim().isNotEmpty) ...[
                      Text(
                        line.aiNote!,
                        style: TextStyle(fontSize: 12, color: chrome.muted),
                      ),
                      const SizedBox(height: 12),
                    ],
                    DropdownButtonHideUnderline(
                      child: DropdownButton<BulkIdKind>(
                        isExpanded: true,
                        value: kind,
                        items: [
                          for (final k in BulkIdKind.values)
                            DropdownMenuItem(
                              value: k,
                              child: Text(k.fieldLabel),
                            ),
                        ],
                        onChanged: (k) {
                          if (k != null) setLocalState(() => kind = k);
                        },
                      ),
                    ),
                    const SizedBox(height: 8),
                    TextField(
                      controller: controller,
                      autofocus: true,
                      decoration: const InputDecoration(
                        labelText: 'Identity value',
                      ),
                      onSubmitted: (_) => Navigator.of(ctx).pop(true),
                    ),
                  ],
                ),
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.of(ctx).pop(false),
                  child: const Text('Cancel'),
                ),
                FilledButton(
                  onPressed: () => Navigator.of(ctx).pop(true),
                  child: const Text('Save & confirm'),
                ),
              ],
            );
          },
        );
      },
    );
    if (saved != true || !mounted) return;
    final current = _bulkParse;
    if (current == null) return;
    final value = controller.text.trim();
    setState(() {
      _bulkParse = current.replacingLine(
        line.copyWith(
          tagOrPart: value,
          idKind: kind,
          // Non-empty = user explicitly confirmed or corrected it. Blank =
          // still needs a real value, so keep the "check PM" flag on.
          missingIdentity: value.isEmpty,
        ),
      );
    });
  }

  /// Last-chance gate before Generate: any line still flagged
  /// [BulkLabelLine.missingIdentity] (Claude-filled gap, or an explicitly
  /// blanked identity) prints with **no visual marker at all** on the actual
  /// Avery sticker/Word doc — the italic "*" only exists in this review
  /// table. This is the last chance to send the user back to confirm/edit
  /// before that distinction is lost.
  Future<bool> _confirmUnverifiedBulkIdentities(int count) async {
    final proceed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Unconfirmed identities'),
        content: Text(
          '$count line${count == 1 ? '' : 's'} still show an AI-suggested '
          'or blank TAG#/PART#/ITEM# (italic with * in the table above). '
          'The printed sticker will not carry that marker once generated. '
          'Tap a line’s Identity cell to confirm or fix it first, or '
          'generate anyway.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Review first'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Generate anyway'),
          ),
        ],
      ),
    );
    return proceed == true;
  }

  void _clearBulkParse() {
    setState(() {
      _bulkParse = null;
      _bulkSourcePath = null;
    });
  }

  void _clearShipment() {
    final clear = switch (_kind) {
      LabelKind.receiving => {
          LabelFields.poNum,
          LabelFields.project,
          LabelFields.salesOrder,
          LabelFields.swiftContact,
          LabelFields.dateReceived,
          LabelFields.receivedBy,
          LabelFields.specialInstructions,
        },
      LabelKind.bol => {
          for (final d in BolFields.formDefs) d.$1,
          BolFields.freightCharges,
          LabelFields.poNum,
          LabelFields.project,
          LabelFields.salesOrder,
          LabelFields.specialInstructions,
        },
      LabelKind.shipping => {
          LabelFields.poNum,
          LabelFields.project,
          LabelFields.packingSlip,
          LabelFields.salesOrder,
          LabelFields.palletNum,
          LabelFields.palletOf,
          LabelFields.boxNum,
          LabelFields.boxOf,
          LabelFields.specialInstructions,
          BolFields.freightCharges,
          BolFields.thirdPartyBilling,
        },
      LabelKind.bulk => <String>{},
    };
    for (final key in clear) {
      _controllers[key]?.clear();
    }
    if (_kind == LabelKind.bol) {
      _bolLineCount = 1;
      _setField(BolFields.freightCharges, BolFields.freightPrepaid);
    }
    if (_kind == LabelKind.shipping) {
      _setField(BolFields.freightCharges, BolFields.freightPrepaid);
    }
    if (_kind == LabelKind.bulk) {
      _bulkParse = null;
      _bulkSourcePath = null;
    }
    _soFieldMap = SoFieldMap();
    _soPairingDismissed = false;
    setState(() {});
  }

  void _clearAll() {
    for (final c in _controllers.values) {
      c.clear();
    }
    _logoPaths.clear();
    _presetName = null;
    _shipperSignatureBytes = null;
    _selectedSavedSignature = null;
    _bolLineCount = 1;
    _setField(BolFields.freightCharges, BolFields.freightPrepaid);
    _bulkParse = null;
    _bulkSourcePath = null;
    _templatePromptResolvedForCustomer = null;
    _soFieldMap = SoFieldMap();
    _soPairingDismissed = false;
    setState(() {});
  }

  Future<void> _generateAndShare() async {
    if (_busy) return;

    if (_kind == LabelKind.bulk) {
      await _generateBulkLabels();
      return;
    }

    PieceCountPlan? piecePlan;
    Map<String, PieceCountPlan>? perSoPlans;
    final alsoShipping = _kind == LabelKind.bol &&
        _bolAlsoShippingLabels &&
        !_soPairingDismissed;
    if (_kind == LabelKind.shipping || alsoShipping) {
      piecePlan = await _askPieceCounts();
      if (piecePlan == null || piecePlan.isEmpty) return;
      final sos = parseSalesOrders(_controllers[LabelFields.salesOrder]?.text ?? '');
      if (alsoShipping && sos.length > 1) {
        perSoPlans = await _askPieceCountsPerSalesOrder(
          salesOrders: sos,
          total: piecePlan,
        );
        if (perSoPlans == null) return;
      }
    }

    final bolCopies = <String>[];
    if (_kind == LabelKind.bol) {
      if (_bolStoreCopy) bolCopies.add('STORE COPY');
      if (_bolDriverCopy) bolCopies.add('DRIVER COPY');
      if (_bolCustomerCopy) bolCopies.add('CUSTOMER COPY');
      if (bolCopies.isEmpty) {
        if (!mounted) return;
        showAppSnack(context, 'Select at least one BOL copy to generate.');
        return;
      }
    }

    setState(() => _busy = true);
    try {
      final logoBytes = await _loadSelectedLogoBytes();
      final data = _collect();
      // Keep BOL consignee in sync with shared ship-to when empty
      if (_kind == LabelKind.bol) {
        if (data.get(BolFields.consigneeName).isEmpty) {
          data.set(BolFields.consigneeName, data.get(LabelFields.shipTo));
        }
        if (data.get(BolFields.consigneeAddress).isEmpty) {
          data.set(BolFields.consigneeAddress, data.get(LabelFields.location));
        }
        if (data.get(BolFields.orderNum).isEmpty) {
          data.set(BolFields.orderNum, data.get(LabelFields.salesOrder));
        }
        final soVal = data.get(LabelFields.salesOrder);
        if (soVal.isNotEmpty) {
          data.set(BolFields.orderNum, soVal);
        }
        if (data.get(BolFields.packingList).isEmpty) {
          data.set(BolFields.packingList, data.get(LabelFields.packingSlip));
        }
        if (data.get(BolFields.freightCharges).isEmpty) {
          data.set(BolFields.freightCharges, BolFields.freightPrepaid);
        }
        // Shared cloud serial — every Windows/Android generate bumps SW-####.
        final docNo = await BolDocumentNumber.allocate(
          source: 'swift_document_generator',
          note: data.get(BolFields.consigneeName).isEmpty
              ? data.get(LabelFields.customer)
              : data.get(BolFields.consigneeName),
        );
        data.set(BolFields.documentNumber, docNo);
        _setField(BolFields.documentNumber, docNo);
        if (data.get(BolFields.documentDate).isEmpty) {
          final today = BolDocumentNumber.todayStamp();
          data.set(BolFields.documentDate, today);
          _setField(BolFields.documentDate, today);
        }
      }

      if (_kind == LabelKind.shipping &&
          data.get(BolFields.freightCharges).isEmpty) {
        data.set(BolFields.freightCharges, BolFields.freightPrepaid);
      }

      data.set(
        LabelFields.salesOrder,
        formatSalesOrders(data.get(LabelFields.salesOrder), finalize: true),
      );
      data.set(
        LabelFields.poNum,
        formatNamedSegments(data.get(LabelFields.poNum), finalize: true),
      );
      data.set(
        LabelFields.project,
        formatNamedSegments(data.get(LabelFields.project), finalize: true),
      );
      data.set(
        BolFields.packingList,
        formatNamedSegments(data.get(BolFields.packingList), finalize: true),
      );
      data.set(BolFields.orderNum, data.get(LabelFields.salesOrder));

      Future<File> saveOne({
        required LabelKind kind,
        required Uint8List bytes,
        required ShippingLabelData forData,
        String? prefixOverride,
      }) async {
        final soName = forData.get(LabelFields.salesOrder).isNotEmpty
            ? forData.get(LabelFields.salesOrder)
            : forData.get(BolFields.orderNum);
        final name = widget.storage.labelPdfBaseName(
          kind: kind,
          customer: forData.get(LabelFields.customer).isNotEmpty
              ? forData.get(LabelFields.customer)
              : forData.get(BolFields.consigneeName),
          salesOrder: soName,
          prefixOverride: prefixOverride,
        );
        await _uploadGeneratedPdf(
          kind: kind,
          fileName: name,
          bytes: bytes,
          data: forData,
        );
        final file = await widget.storage.writePdf(
          name,
          bytes,
          outputDir: widget.storage.pdfOutputDir(_uiSettings),
        );
        final filled = widget.storage.filledDir;
        if (p.normalize(file.parent.path) != p.normalize(filled.path)) {
          await widget.storage.writePdf(name, bytes, outputDir: filled);
        }
        return file;
      }

      File? lastFile;
      // BOL + "also generate Shipping Labels" produces two separate PDFs —
      // both must be opened/reported, not just whichever saved last.
      File? bolFile;
      File? alsoShippingFile;
      var shippingPages = 0;
      switch (_kind) {
        case LabelKind.receiving:
          lastFile = await saveOne(
            kind: LabelKind.receiving,
            bytes: await widget.pdf.buildReceiving(
              data: data,
              customerLogoBytes: logoBytes,
              options: _pdfOptionsForGenerate,
            ),
            forData: data,
          );
        case LabelKind.bol:
          lastFile = bolFile = await saveOne(
            kind: LabelKind.bol,
            bytes: await BolLabelPdf(widget.pdf).build(
              data: data,
              customerLogoBytes: logoBytes,
              shipperSignatureBytes: _shipperSignatureBytes,
              copies: bolCopies,
              options: _pdfOptionsForGenerate,
            ),
            forData: data,
          );
          if (alsoShipping) {
            final shipBase = _shippingDataFromBol(data);
            final sos = parseSalesOrders(data.get(LabelFields.salesOrder));
            Uint8List shipBytes;
            ShippingLabelData shipMeta = shipBase;
            if (sos.length > 1 && perSoPlans != null) {
              shipBytes = await widget.pdf.buildForSalesOrders(
                jobs: [
                  for (final so in sos)
                    if (!(perSoPlans[so]?.isEmpty ?? true))
                      (
                        _shippingDataForSalesOrder(shipBase, so),
                        perSoPlans[so]!,
                      ),
                ],
                customerLogoBytes: logoBytes,
                options: _pdfOptionsForGenerate,
              );
              shippingPages = perSoPlans.values.fold<int>(
                0,
                (a, e) => a + e.totalPages,
              );
            } else {
              shipBytes = await widget.pdf.build(
                data: shipBase,
                customerLogoBytes: logoBytes,
                piecePlan: piecePlan!,
                options: _pdfOptionsForGenerate,
              );
              shippingPages = piecePlan.totalPages;
            }
            lastFile = alsoShippingFile = await saveOne(
              kind: LabelKind.shipping,
              bytes: shipBytes,
              forData: shipMeta,
            );
          }
        case LabelKind.shipping:
          lastFile = await saveOne(
            kind: LabelKind.shipping,
            bytes: await widget.pdf.build(
              data: data,
              customerLogoBytes: logoBytes,
              piecePlan: piecePlan!,
              options: _pdfOptionsForGenerate,
            ),
            forData: data,
          );
          shippingPages = piecePlan.totalPages;
        case LabelKind.bulk:
          throw StateError('Bulk labels use _generateBulkLabels');
      }

      await _rememberContactNames([
        data.get(LabelFields.swiftContact),
        data.get(LabelFields.receivedBy),
        data.get(BolFields.shipperCertName),
      ]);
      await _rememberDeliveryAddress(data);

      if (_uiSettings.autoOpenPdf) {
        if (bolFile != null) await shareOrOpenFile(file: bolFile);
        if (alsoShippingFile != null) {
          // The default PDF viewer (e.g. Edge) can still be claiming its
          // window/tab for the first file — opening the second one back to
          // back too quickly makes it reuse that same tab instead of
          // opening its own, so only the shipping label ends up visible.
          // A short gap lets the first file's viewer finish opening.
          if (bolFile != null) {
            await Future.delayed(const Duration(milliseconds: 1200));
          }
          await shareOrOpenFile(file: alsoShippingFile);
        }
        if (bolFile == null && alsoShippingFile == null) {
          await shareOrOpenFile(file: lastFile);
        }
      }

      if (mounted) {
        final pages = switch (_kind) {
          LabelKind.shipping => ' ($shippingPages pages)',
          LabelKind.bol => alsoShipping
              ? ' (${bolCopies.length} BOL + $shippingPages shipping · ${data.get(BolFields.documentNumber)})'
              : ' (${bolCopies.length} ${bolCopies.length == 1 ? 'copy' : 'copies'} · ${data.get(BolFields.documentNumber)})',
          LabelKind.receiving => '',
          LabelKind.bulk => '',
        };
        final openHint = _uiSettings.autoOpenPdf ? '' : ' (auto-open off)';
        final paths = alsoShippingFile != null
            ? '${bolFile!.path}\n${alsoShippingFile.path}'
            : lastFile.path;
        showAppSnack(
          context,
          'Saved$pages$openHint:\n$paths',
        );
      }
    } catch (e) {
      if (mounted) {
        showAppSnack(context, 'PDF failed: $e');
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _generateBulkLabels() async {
    final parsed = _bulkParse;
    if (parsed == null || parsed.totalLabels < 1) {
      if (!mounted) return;
      showAppSnack(context, 'Upload an Order Acknowledgement PDF first.');
      return;
    }
    if (parsed.poNumber.trim().isEmpty) {
      if (!mounted) return;
      showAppSnack(context, 'PO Number missing — check the uploaded OA PDF.');
      return;
    }
    final unconfirmed = parsed.lines.where((l) => l.missingIdentity).length;
    if (unconfirmed > 0) {
      final proceed = await _confirmUnverifiedBulkIdentities(unconfirmed);
      if (!mounted || !proceed) return;
    }

    setState(() => _busy = true);
    try {
      final labels = parsed.expand();
      final outDir = widget.storage.pdfOutputDir(_uiSettings);
      final name = widget.storage.labelPdfBaseName(
        kind: LabelKind.bulk,
        customer: 'Propak',
        salesOrder: parsed.poNumber,
      );

      final pdf = await BulkLabelPdf.load();
      final pdfBytes = await pdf.build(labels);
      final pdfFile = await widget.storage.writePdf(
        name,
        pdfBytes,
        outputDir: outDir,
      );

      final docxBytes = await BulkLabelDocx.build(labels);
      final docxFile = await widget.storage.writeDocx(
        name,
        docxBytes,
        outputDir: outDir,
      );

      // Same as other docs: Generate saves, then opens the primary file when
      // auto-open is on. For Bulk Labels the editable Word doc is primary.
      try {
        final record = await _documentHistorySync.upload(
          kind: LabelKind.bulk,
          fileName: '$name.pdf',
          bytes: pdfBytes,
          customer: 'Propak',
          salesOrder: parsed.poNumber,
          title: name,
          fields: {
            'po': parsed.poNumber,
            'labels': '${labels.length}',
            'sheets': '${parsed.sheetCount}',
            if (_bulkSourcePath != null) 'source': p.basename(_bulkSourcePath!),
          },
        );
        if (!record.snapshotSaved && mounted) {
          showAppSnack(
            context,
            'Bulk History archived, but snapshot incomplete.',
          );
        }
      } on DocumentHistorySyncException catch (e) {
        if (mounted) {
          showAppSnack(context, 'Bulk History: ${e.message}');
        }
      } catch (_) {}

      if (_uiSettings.autoOpenPdf) {
        await shareOrOpenFile(
          file: docxFile,
          mime:
              'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
          subject: 'Propak Bulk Labels',
        );
      }
      if (mounted) {
        final openHint = _uiSettings.autoOpenPdf ? '' : ' (auto-open off)';
        showAppSnack(
          context,
          'Saved ${labels.length} Propak labels on ${parsed.sheetCount} '
          'sheet${parsed.sheetCount == 1 ? '' : 's'}$openHint:\n'
          '${docxFile.path}\n(PDF also saved: ${p.basename(pdfFile.path)})',
        );
      }
    } catch (e) {
      if (mounted) {
        showAppSnack(context, 'Bulk label generate failed: $e');
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _pickDate(String key) async {
    final current = AppDates.parse(_controllers[key]!.text) ?? DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: current,
      firstDate: DateTime(2000),
      lastDate: DateTime(2100),
    );
    if (picked != null && mounted) {
      _setField(key, AppDates.format(picked));
      setState(() {});
    }
  }

  Widget _buildDateField(String key) {
    final m = _meta(key);
    final ctrl = _controllers[key]!;
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: TextField(
        controller: ctrl,
        readOnly: true,
        decoration: InputDecoration(
          labelText: m.$2.toUpperCase(),
          suffixIcon: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (ctrl.text.isNotEmpty)
                IconButton(
                  tooltip: 'Clear',
                  icon: const Icon(Icons.clear, size: 20),
                  onPressed: () {
                    _setField(key, '');
                    setState(() {});
                  },
                ),
              IconButton(
                tooltip: 'Pick date',
                icon: const Icon(Icons.calendar_today, size: 20),
                onPressed: () => _pickDate(key),
              ),
            ],
          ),
        ),
        onTap: () => _pickDate(key),
      ),
    );
  }

  Widget _buildItemTypeField(String key) {
    final raw = _controllers[key]?.text ?? '';
    final normalized = BolItemTypes.normalizeStored(raw);
    // Never mutate controllers during build — schedule a post-frame write.
    if (normalized != raw && normalized.isNotEmpty) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (!mounted) return;
        final ctrl = _controllers[key];
        if (ctrl != null && ctrl.text != normalized) {
          ctrl.text = normalized;
        }
      });
    }
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: DropdownButtonFormField<String>(
        value: BolItemTypes.options.contains(normalized) ? normalized : null,
        decoration: const InputDecoration(labelText: 'ITEM TYPE'),
        hint: const Text('Select type'),
        items: [
          for (final opt in BolItemTypes.options)
            DropdownMenuItem(value: opt, child: Text(opt)),
        ],
        onChanged: (v) {
          _setField(key, v ?? '');
          setState(() {});
        },
      ),
    );
  }

  Future<void> _captureShipperSignature() async {
    final padKey = GlobalKey<SignaturePadState>();
    Uint8List? captured;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDialogState) {
          final pad = padKey.currentState;
          final canUse = pad != null && !pad.isEmpty;

          return AlertDialog(
            title: const Text('Shipper signature'),
            content: SizedBox(
              width: 420,
              child: SignaturePad(
                key: padKey,
                onChanged: () => setDialogState(() {}),
              ),
            ),
            actions: [
              TextButton(
                onPressed: () {
                  padKey.currentState?.clear();
                  setDialogState(() {});
                },
                child: const Text('Clear'),
              ),
              TextButton(
                onPressed: () => Navigator.pop(ctx, false),
                child: const Text('Cancel'),
              ),
              FilledButton(
                onPressed: canUse
                    ? () async {
                        final state = padKey.currentState;
                        if (state == null || state.isEmpty) return;
                        captured = await state.exportPng();
                        if (ctx.mounted && captured != null) {
                          Navigator.pop(ctx, true);
                        }
                      }
                    : null,
                child: const Text('Use'),
              ),
            ],
          );
        },
      ),
    );
    if (ok != true || captured == null || !mounted) return;

    final png = captured!;

    setState(() {
      _shipperSignatureBytes = png;
      _selectedSavedSignature = null;
    });

    final save = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Save for future use?'),
        content: const Text(
          'Store this signature in the cloud so you can reuse it on later BOLs.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('No, this BOL only'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Yes, save'),
          ),
        ],
      ),
    );
    if (save != true || !mounted) return;

    final defaultName =
        _controllers[BolFields.shipperCertName]?.text.trim() ?? '';
    final name = await _askString(
      'Name signature',
      'Short name',
      defaultName.isEmpty ? 'My signature' : defaultName,
    );
    if (name == null || !mounted) return;

    try {
      final saved = await _signatureSync.saveSignature(
        name: name,
        pngBytes: png,
      );
      if (mounted) {
        setState(() => _selectedSavedSignature = saved);
        showAppSnack(context, 'Saved signature “${saved.name}”.');
      }
    } on SignatureSyncException catch (e) {
      if (mounted) {
        showAppSnack(context, 'Could not save signature: ${e.message}');
      }
    }
  }

  Future<void> _pickSavedSignature() async {
    await _runExclusiveDialog<void>('pickSignature', () async {
    final sigs = _signatureSync.localSignatures;
    if (sigs.isEmpty) {
      if (mounted) {
        showAppSnack(context, 'No saved signatures yet.');
      }
      return;
    }
    final picked = await showDialog<SavedSignature>(
      context: context,
      builder: (ctx) => SimpleDialog(
        title: const Text('Saved signatures'),
        children: [
          for (final sig in sigs)
            SimpleDialogOption(
              onPressed: () => Navigator.pop(ctx, sig),
              child: Text(sig.name),
            ),
        ],
      ),
    );
    if (picked == null || !mounted) return;
    final bytes = await _signatureSync.loadBytes(picked);
    if (!mounted) return;
    if (bytes == null) {
      showAppSnack(context, 'Could not load signature.');
      return;
    }
    setState(() {
      _shipperSignatureBytes = bytes;
      _selectedSavedSignature = picked;
    });
    });
  }

  void _clearShipperSignature() {
    setState(() {
      _shipperSignatureBytes = null;
      _selectedSavedSignature = null;
    });
  }

  Widget _buildShipperSignatureRow() {
    final label = _selectedSavedSignature?.name ??
        (_shipperSignatureBytes != null ? 'Drawn signature' : null);
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            'SHIPPER SIGNATURE (OPTIONAL)',
            style: TextStyle(
              fontFamily: swiftUiFont(context),
              fontSize: 11,
              fontWeight: FontWeight.w600,
              color: SwiftColors.muted,
              letterSpacing: 0.6,
            ),
          ),
          const SizedBox(height: 8),
          if (_shipperSignatureBytes != null) ...[
            Container(
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                border: Border.all(color: SwiftColors.border),
                borderRadius: BorderRadius.circular(8),
                color: Colors.white,
              ),
              child: Image.memory(
                _shipperSignatureBytes!,
                height: 56,
                fit: BoxFit.contain,
              ),
            ),
            if (label != null)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(label, style: const TextStyle(fontSize: 12)),
              ),
            const SizedBox(height: 8),
          ],
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              OutlinedButton.icon(
                onPressed: _captureShipperSignature,
                icon: const Icon(Icons.gesture, size: 18),
                label: Text(
                  _shipperSignatureBytes == null ? 'Add signature' : 'Redraw',
                ),
              ),
              OutlinedButton.icon(
                onPressed: _pickSavedSignature,
                icon: const Icon(Icons.bookmark_outline, size: 18),
                label: const Text('Pick saved'),
              ),
              if (_shipperSignatureBytes != null)
                TextButton(
                  onPressed: _clearShipperSignature,
                  child: const Text('Clear'),
                ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildFormField(String key) {
    if (key.endsWith('_item_type')) {
      return _buildItemTypeField(key);
    }
    if (key.endsWith('_dimensions')) {
      final itemTypeKey = '${key.substring(0, key.length - '_dimensions'.length)}_item_type';
      final itemType = _controllers[itemTypeKey]?.text ?? '';
      return BolDimensionsFields(
        controller: _controllers[key]!,
        unitDefaults: BolItemTypes.defaultDimensionUnits(itemType),
      );
    }
    if (appDateFieldKeys.contains(key)) {
      return _buildDateField(key);
    }
    if (key == LabelFields.swiftContact ||
        key == LabelFields.receivedBy ||
        key == BolFields.shipperCertName) {
      return _buildEmployeeNameField(key);
    }
    if (key == LabelFields.carrier) {
      return _buildCarrierField();
    }
    if (key == LabelFields.customer && _kind != LabelKind.bulk) {
      return _buildCustomerField();
    }
    if (key == LabelFields.shipTo || key == BolFields.consigneeName) {
      return _buildShipToNameField(key);
    }
    if (key == LabelFields.location || key == BolFields.consigneeAddress) {
      return _buildDeliveryAddressField(key);
    }
    final m = _meta(key);
    final isMulti = _isSalesOrderKey(key) || _isNamedMultiKey(key);
    final lines = !m.$3
        ? 1
        : key == LabelFields.specialInstructions
            ? 2
            : 3;
    void onLiveChanged(String v) {
      if (!isMulti) return;
      if (v.contains(',')) {
        _applyFormattedField(key, formatNamedSegments(v, finalize: false));
      }
    }

    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: lines <= 1
          ? TextField(
              controller: _controllers[key],
              focusNode: isMulti ? _multiFocus(key) : null,
              maxLines: 1,
              onChanged: isMulti ? onLiveChanged : null,
              decoration: InputDecoration(
                labelText: m.$2.toUpperCase(),
              ),
            )
          : FormScrollTextField(
              controller: _controllers[key]!,
              focusNode: isMulti ? _multiFocus(key) : null,
              minLines: 1,
              maxLines: lines,
              onChanged: isMulti ? onLiveChanged : null,
              decoration: InputDecoration(
                labelText: m.$2.toUpperCase(),
              ),
            ),
    );
  }

  Widget _buildShipToNameField(String key) {
    final meta = _meta(key);
    return ShipToSuggestField(
      controller: _controllers[key]!,
      entries: _addressBookSync.entries,
      labelText: meta.$2.toUpperCase(),
      hintText:
          'Type a name or pick a saved location (multi-store names listed per address)',
      onPicked: (e) {
        _applyAddressBookEntry(e);
      },
    );
  }

  Widget _buildCustomerField() {
    final m = _meta(LabelFields.customer);
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: TextField(
        controller: _controllers[LabelFields.customer],
        focusNode: _customerFocusNode,
        maxLines: 1,
        textInputAction: TextInputAction.next,
        onEditingComplete: () {
          _customerFocusNode.unfocus();
          _maybePromptCustomerTemplate();
        },
        onTapOutside: (_) {
          _customerFocusNode.unfocus();
          _maybePromptCustomerTemplate();
        },
        onSubmitted: (_) {
          _customerFocusNode.unfocus();
          _maybePromptCustomerTemplate();
        },
        onChanged: (v) {
          final resolved = _templatePromptResolvedForCustomer;
          if (resolved != null &&
              normalizeCustomerKey(resolved) != normalizeCustomerKey(v)) {
            _templatePromptResolvedForCustomer = null;
          }
        },
        decoration: InputDecoration(
          labelText: m.$2.toUpperCase(),
        ),
      ),
    );
  }

  Widget _buildDeliveryAddressField(String key) {
    final m = _meta(key);
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AddressSuggestField(
            controller: _controllers[key]!,
            entries: _addressBookSync.entries,
            shipToName: () => _kind == LabelKind.bol
                ? (_controllers[BolFields.consigneeName]?.text ?? '')
                : (_controllers[LabelFields.shipTo]?.text ?? ''),
            customer: () => _controllers[LabelFields.customer]?.text ?? '',
            labelText: m.$2.toUpperCase(),
            onPicked: (s) {
              final name = s.placeName.trim();
              if (name.isEmpty) return;
              if (_kind == LabelKind.shipping) {
                if ((_controllers[LabelFields.shipTo]?.text ?? '')
                    .trim()
                    .isEmpty) {
                  _setField(LabelFields.shipTo, name);
                }
              } else if (_kind == LabelKind.bol) {
                if ((_controllers[BolFields.consigneeName]?.text ?? '')
                    .trim()
                    .isEmpty) {
                  _setField(BolFields.consigneeName, name);
                }
              }
            },
            onPickedBookEntry: (e) {
              if (_kind == LabelKind.shipping) {
                if ((_controllers[LabelFields.shipTo]?.text ?? '').trim().isEmpty) {
                  _setField(LabelFields.shipTo, e.shipToName);
                }
                if (e.carrier.isNotEmpty &&
                    (_controllers[LabelFields.carrier]?.text ?? '').trim().isEmpty) {
                  _setField(LabelFields.carrier, e.carrier);
                }
              } else if (_kind == LabelKind.bol) {
                if ((_controllers[BolFields.consigneeName]?.text ?? '')
                    .trim()
                    .isEmpty) {
                  _setField(BolFields.consigneeName, e.shipToName);
                }
              }
            },
          ),
          const SizedBox(height: 4),
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton.icon(
              onPressed: _isDialogLocked('addressBook') ? null : _openAddressBook,
              icon: const Icon(Icons.menu_book_outlined, size: 18),
              label: const Text('Address book'),
            ),
          ),
        ],
      ),
    );
  }

  FocusNode _employeeFocusFor(String key) {
    return _employeeFocusNodes.putIfAbsent(key, FocusNode.new);
  }

  Widget _buildEmployeeNameField(String key) {
    final ctrl = _controllers[key]!;
    final meta = _meta(key);
    // Receiving form: Swift Contact field is the PM (duplicate PM field removed).
    final label = (key == LabelFields.swiftContact && _kind == LabelKind.receiving)
        ? 'PM'
        : meta.$2.toUpperCase();
    final hint = switch (key) {
      LabelFields.swiftContact when _kind == LabelKind.receiving =>
        'Type PM name or pick from shared memory',
      LabelFields.swiftContact => 'Type a name or pick from shared memory',
      LabelFields.receivedBy => 'Type who received or pick from shared memory',
      BolFields.shipperCertName =>
        'Type shipper name or pick from shared memory',
      _ => 'Type a name or pick from shared memory',
    };
    final remembered = {
      for (final n in widget.storage.rememberedContacts) n.toLowerCase(),
    };
    return EmployeeAutocompleteField(
      controller: ctrl,
      focusNode: _employeeFocusFor(key),
      names: _swiftContactNames,
      rememberedNames: remembered,
      labelText: label,
      hintText: hint,
      loading: _swiftContactsLoading,
      onRequestRefresh: () => _loadSwiftContacts(forceRefresh: true),
      onNameCommitted: (name) => _rememberContactNames([name]),
      onForgetRemembered: _forgetRememberedContact,
    );
  }

  Future<void> _forgetRememberedContact(String name) async {
    var removed = false;
    try {
      removed = await _contactSync.forget(name);
    } on ContactSyncException catch (e) {
      removed = await widget.storage.forgetContact(name);
      if (mounted) {
        showAppSnack(context, 'Removed locally; cloud sync failed: ${e.message}');
      }
      if (removed && mounted) _refreshContactSuggestions();
      return;
    } catch (_) {
      removed = await widget.storage.forgetContact(name);
    }
    if (!removed || !mounted) return;
    _refreshContactSuggestions();
    showAppSnack(context, 'Removed “$name” from shared memory.');
  }

  /// Reuses the generic shared-memory autocomplete widget (its name predates
  /// carrier support) for the Carrier field.
  Widget _buildCarrierField() {
    return EmployeeAutocompleteField(
      controller: _controllers[LabelFields.carrier]!,
      focusNode: _carrierFocusNode,
      names: _carrierNames,
      labelText: 'CARRIER',
      hintText: 'Type a carrier or pick from shared memory',
      loading: _carrierNamesLoading,
      onRequestRefresh: () => _loadCarrierNames(forceRefresh: true),
      onNameCommitted: _rememberCarrierName,
    );
  }

  /// On Windows, pair single-line fields into two columns for denser forms.
  Widget _buildFieldsBlock(List<String> keys, {bool dualColumn = false}) {
    if (!dualColumn) {
      return Column(
        children: [for (final key in keys) _buildFormField(key)],
      );
    }

    final out = <Widget>[];
    final pending = <String>[];

    void flushPending() {
      for (var i = 0; i < pending.length; i += 2) {
        if (i + 1 < pending.length) {
          out.add(
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(child: _buildFormField(pending[i])),
                const SizedBox(width: 12),
                Expanded(child: _buildFormField(pending[i + 1])),
              ],
            ),
          );
        } else {
          out.add(_buildFormField(pending[i]));
        }
      }
      pending.clear();
    }

    for (final key in keys) {
      final multiline = _meta(key).$3;
      if (multiline) {
        flushPending();
        out.add(_buildFormField(key));
      } else {
        pending.add(key);
      }
    }
    flushPending();
    return Column(children: out);
  }

  Widget _buildFreightChargesSelector() {
    final selected = _selectedFreightCharges().isEmpty
        ? BolFields.freightPrepaid
        : _selectedFreightCharges().first;
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            'FREIGHT CHARGES',
            style: TextStyle(
              fontFamily: swiftUiFont(context),
              fontSize: 11,
              fontWeight: FontWeight.w600,
              color: SwiftColors.muted,
              letterSpacing: 0.6,
            ),
          ),
          const SizedBox(height: 8),
          Wrap(
            spacing: 12,
            runSpacing: 4,
            children: [
              for (final o in BolFields.freightChargeOptions)
                SwiftCircleRadio<String>(
                  value: o.$1,
                  groupValue: selected,
                  label: o.$2,
                  dense: true,
                  onChanged: (v) {
                    if (v == null) return;
                    _setField(BolFields.freightCharges, v);
                    setState(() {});
                  },
                ),
            ],
          ),
        ],
      ),
    );
  }

  void _selectKind(LabelKind kind) {
    setState(() {
      _kind = kind;
      _presetName = null;
      _templatePromptResolvedForCustomer = null;
      if (_kind == LabelKind.bol) {
        _bolLineCount = _detectBolLineCount();
        _syncSignaturesQuietly();
        // Box-sized quarter-page labels are Shipping/Receiving only.
        _boxSizedLabel = false;
      } else if (_kind == LabelKind.bulk) {
        _boxSizedLabel = false;
        _bolLineCount = 1;
      } else {
        _bolLineCount = 1;
      }
    });
    // Bulk pages are short — keep chrome fully expanded (no hide-on-scroll).
    if (kind == LabelKind.bulk) {
      _setMobileChromeExpanded(true);
    }
  }

  /// Portrait Android only; disabled on Bulk. Smooth Chrome-like expand/collapse.
  bool get _mobileChromeCollapseEnabled {
    if (!mounted) return false;
    if (_kind == LabelKind.bulk) return false;
    return MediaQuery.orientationOf(context) == Orientation.portrait;
  }

  void _setMobileChromeExpanded(bool expanded) {
    if (!_mobileChromeCollapseEnabled) {
      if (_mobileChromeCtrl.value != 1) {
        _mobileChromeCtrl.animateTo(1, curve: Curves.easeOutCubic);
      }
      return;
    }
    if (expanded) {
      _mobileChromeCtrl.forward();
    } else {
      _mobileChromeCtrl.reverse();
    }
  }

  bool get _boxSizedAllowed =>
      _kind == LabelKind.shipping || _kind == LabelKind.receiving;

  PdfRenderOptions get _pdfOptionsForGenerate => _uiSettings.pdfOptions.copyWith(
        isBoxSized: _boxSizedAllowed && _boxSizedLabel,
      );

  /// Generate button + Box Label checkbox (desktop workspace / form / mobile).
  Widget _buildGenerateControls({
    required bool dense,
    bool stackVertically = false,
    double buttonHeight = 40,
  }) {
    final boxToggle = Tooltip(
      message: _boxSizedAllowed
          ? 'Print the label at 50% scale in the top-left quarter of a '
              'landscape Letter page (5.5″ × 4.25″).'
          : 'Box-sized labels are only available for Shipping and Receiving labels.',
      child: SwiftCircleCheckbox(
        value: _boxSizedAllowed && _boxSizedLabel,
        enabled: _boxSizedAllowed && !_busy,
        dense: true,
        label: 'Box Label (1/4 Page)',
        onChanged: !_boxSizedAllowed || _busy
            ? null
            : (v) => setState(() => _boxSizedLabel = v ?? false),
      ),
    );
    final generateLabel = _kind == LabelKind.bulk ? 'Generate' : 'Generate PDF';
    final generateIcon = _kind == LabelKind.bulk
        ? Icons.description_outlined
        : Icons.picture_as_pdf_outlined;
    final button = SizedBox(
      width: stackVertically ? double.infinity : null,
      height: buttonHeight,
      child: FilledButton.icon(
        onPressed: _busy ? null : _generateAndShare,
        icon: _busy
            ? const SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: Colors.white,
                ),
              )
            : Icon(generateIcon, size: 18),
        label: Text(_busy ? 'Generating…' : generateLabel),
      ),
    );
    if (stackVertically) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          boxToggle,
          SizedBox(height: dense ? 8 : 10),
          button,
        ],
      );
    }
    return Row(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        Expanded(child: boxToggle),
        const SizedBox(width: 12),
        button,
      ],
    );
  }

  void _newDocument(LabelKind kind) {
    _clearAll();
    _selectKind(kind);
  }

  void _onBolCopyChanged({bool? store, bool? driver, bool? customer}) {
    setState(() {
      if (store != null) _bolStoreCopy = store;
      if (driver != null) _bolDriverCopy = driver;
      if (customer != null) _bolCustomerCopy = customer;
    });
  }

  String get _kindTitle => switch (_kind) {
        LabelKind.shipping => 'Shipping Label',
        LabelKind.receiving => 'Receiving Label',
        LabelKind.bol => 'Bill of Lading',
        LabelKind.bulk => 'Bulk Labels',
      };

  String get _kindHint => switch (_kind) {
        LabelKind.receiving =>
          'Pre-fill the receiving / staging label → Generate PDF. Special Instructions stay two lines.',
        LabelKind.bol =>
          'Straight Bill of Lading. Choose which copies to print. Document number SW-#### is assigned from the shared company counter on Generate (needs network).',
        LabelKind.shipping =>
          'Pre-fill the label → Generate PDF. You’ll be asked how many pallet/crate and box labels to print.',
        LabelKind.bulk =>
          'Upload a Swift Order Acknowledgement PDF. Avery 5163 Propak stickers are built from PO#, CPO LINE #, and TAG# / PART# / ITEM# (or the note line under CPO when part# is missing). Choose Single vs Per-unit tags per line before Generate. Quantity = label count when Per-unit. Outputs Word + PDF.',
      };

  List<(String, String, List<String>)> get _activeGroups => switch (_kind) {
        LabelKind.receiving => _receivingGroups,
        LabelKind.bol => _bolGroupsBeforeLines,
        LabelKind.shipping => _shippingGroups,
        LabelKind.bulk => const [],
      };

  int get _kindRailIndex => switch (_kind) {
        LabelKind.shipping => 0,
        LabelKind.receiving => 1,
        LabelKind.bol => 2,
        LabelKind.bulk => 3,
      };

  Widget _buildMobileKindSelector() {
    final portrait =
        MediaQuery.orientationOf(context) == Orientation.portrait;
    // Portrait phones are too narrow for icon + check + label in each segment
    // (labels wrap mid-word). Use single-line labels only; landscape keeps icons.
    Text label(String text) => Text(
          text,
          maxLines: 1,
          softWrap: false,
          overflow: TextOverflow.fade,
        );
    return SegmentedButton<LabelKind>(
      showSelectedIcon: false,
      segments: [
        ButtonSegment(
          value: LabelKind.shipping,
          label: label('Ship'),
          icon: portrait
              ? null
              : const Icon(Icons.local_shipping_outlined, size: 18),
        ),
        ButtonSegment(
          value: LabelKind.receiving,
          label: label('Recv'),
          icon: portrait
              ? null
              : const Icon(Icons.inventory_2_outlined, size: 18),
        ),
        ButtonSegment(
          value: LabelKind.bol,
          label: label('BOL'),
          icon: portrait
              ? null
              : const Icon(Icons.description_outlined, size: 18),
        ),
        ButtonSegment(
          value: LabelKind.bulk,
          label: label('Bulk'),
          icon: portrait
              ? null
              : const Icon(Icons.grid_view_outlined, size: 18),
        ),
      ],
      selected: {_kind},
      onSelectionChanged: (s) => _selectKind(s.first),
      style: ButtonStyle(
        visualDensity:
            portrait ? VisualDensity.compact : VisualDensity.standard,
        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
        padding: WidgetStatePropertyAll(
          EdgeInsets.symmetric(
            horizontal: portrait ? 6 : 12,
            vertical: portrait ? 8 : 10,
          ),
        ),
        textStyle: WidgetStatePropertyAll(
          TextStyle(
            fontFamily: swiftUiFont(context),
            fontWeight: FontWeight.w600,
            fontSize: portrait ? 13 : 12,
            letterSpacing: 0.2,
            height: 1.0,
          ),
        ),
      ),
    );
  }

  Widget _buildBolCopiesCard({bool compact = false}) {
    return _Card(
      title: 'Copies to generate',
      hint: 'Only selected pages are included in the PDF',
      dense: compact,
      child: Column(
        children: [
          SwiftCircleCheckbox(
            dense: compact,
            label: 'Store Copy',
            value: _bolStoreCopy,
            onChanged: (v) => setState(() => _bolStoreCopy = v ?? false),
          ),
          SwiftCircleCheckbox(
            dense: compact,
            label: 'Driver Copy',
            value: _bolDriverCopy,
            onChanged: (v) => setState(() => _bolDriverCopy = v ?? false),
          ),
          SwiftCircleCheckbox(
            dense: compact,
            label: 'Customer Copy',
            value: _bolCustomerCopy,
            onChanged: (v) => setState(() => _bolCustomerCopy = v ?? false),
          ),
          SwiftCircleCheckbox(
            dense: compact,
            label: 'Shipping Labels',
            subtitle: _soPairingDismissed
                ? 'Unavailable — sales-order pairing was skipped'
                : null,
            value: _bolAlsoShippingLabels && !_soPairingDismissed,
            enabled: !_soPairingDismissed,
            onChanged: _soPairingDismissed
                ? null
                : (v) => setState(() => _bolAlsoShippingLabels = v ?? false),
          ),
        ],
      ),
    );
  }

  Widget _buildPresetCard({bool dense = false}) {
    final presetNames = widget.storage.presetDisplayNamesFor(_kind);
    return _Card(
      title: 'Customer preset',
      hint: dense
          ? 'Shared cloud presets — per document type'
          : 'Shared across all devices — per document type; shipment fields stay per job',
      dense: dense,
      child: Column(
        children: [
          DropdownButtonFormField<String?>(
            key: ValueKey('preset-$_kind-${presetNames.length}'),
            value: _presetName != null && presetNames.contains(_presetName)
                ? _presetName
                : null,
            hint: const Text('Select preset'),
            decoration: const InputDecoration(labelText: 'PRESET'),
            isExpanded: true,
            items: [
              for (final n in presetNames)
                DropdownMenuItem<String?>(value: n, child: Text(n)),
            ],
            onChanged: (v) {
              if (v != null) _applyPreset(v);
            },
          ),
          SizedBox(height: dense ? 8 : 10),
          Row(
            children: [
              Expanded(
                child: OutlinedButton(
                  onPressed: _savePreset,
                  child: const Text('Save'),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: OutlinedButton(
                  onPressed: _presetName == null ? null : _deletePreset,
                  child: const Text('Delete'),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildLogosCard({bool dense = false, bool stackActions = false}) {
    final logoButtons = stackActions
        ? Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              FilledButton.tonalIcon(
                onPressed: (_findingLogo ||
                        _restoringLogo ||
                        _isDialogLocked('findLogo') ||
                        _logoPaths.length >= maxCustomerLogos)
                    ? null
                    : _findLogoOnWeb,
                icon: _findingLogo
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.travel_explore, size: 18),
                label: Text(
                  _findingLogo ? 'Searching…' : 'Find logo on the web',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              const SizedBox(height: 8),
              OutlinedButton.icon(
                onPressed: _logoPaths.length >= maxCustomerLogos ||
                        _restoringLogo ||
                        _isDialogLocked('uploadManually')
                    ? null
                    : _showUploadManuallyMenu,
                icon: const Icon(Icons.upload_file, size: 18),
                label: const Text(
                  'Upload manually',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          )
        : Row(
            children: [
              Expanded(
                child: FilledButton.tonalIcon(
                  onPressed: (_findingLogo ||
                          _restoringLogo ||
                          _isDialogLocked('findLogo') ||
                          _logoPaths.length >= maxCustomerLogos)
                      ? null
                      : _findLogoOnWeb,
                  icon: _findingLogo
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.travel_explore, size: 18),
                  label: Text(
                    _findingLogo ? 'Searching…' : 'Find logo on the web',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: _logoPaths.length >= maxCustomerLogos ||
                          _restoringLogo ||
                          _isDialogLocked('uploadManually')
                      ? null
                      : _showUploadManuallyMenu,
                  icon: const Icon(Icons.upload_file, size: 18),
                  label: const Text(
                    'Upload manually',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ),
            ],
          );

    return _Card(
      title: 'Customer logos',
      hint: 'Up to $maxCustomerLogos per label — second logo is C/O',
      dense: dense,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (_logoPaths.isNotEmpty) ...[
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                for (var i = 0; i < _logoPaths.length; i++) ...[
                  if (i > 0) SizedBox(width: dense ? 6 : 8),
                  Expanded(
                    child: Container(
                      padding: EdgeInsets.all(dense ? 8 : 10),
                      decoration: BoxDecoration(
                        color: SwiftColors.bg,
                        borderRadius: BorderRadius.circular(dense ? 6 : 10),
                        border: Border.all(color: SwiftColors.border),
                      ),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  i == 0 ? 'CUSTOMER' : 'C/O',
                                  style: TextStyle(
                                    fontFamily: swiftUiFont(context),
                                    fontSize: 11,
                                    fontWeight: FontWeight.w600,
                                    color: SwiftColors.muted,
                                    letterSpacing: 0.6,
                                  ),
                                ),
                                const SizedBox(height: 6),
                                if (File(_logoPaths[i]).existsSync())
                                  Align(
                                    alignment: Alignment.centerLeft,
                                    child: Image.file(
                                      File(_logoPaths[i]),
                                      height: dense ? 40 : 48,
                                      filterQuality: FilterQuality.medium,
                                      gaplessPlayback: true,
                                      fit: BoxFit.contain,
                                    ),
                                  )
                                else
                                  Text(
                                    p.basename(_logoPaths[i]),
                                    style: const TextStyle(fontSize: 12),
                                  ),
                              ],
                            ),
                          ),
                          IconButton(
                            tooltip: 'Replace',
                            onPressed: () => _replaceLogoAt(i),
                            icon: const Icon(Icons.swap_horiz, size: 20),
                          ),
                          IconButton(
                            tooltip: 'Remove',
                            onPressed: () => _removeLogoAt(i),
                            icon: const Icon(Icons.close, size: 20),
                          ),
                        ],
                      ),
                    ),
                  ),
                ],
              ],
            ),
            SizedBox(height: dense ? 10 : 12),
          ],
          if (_restoringLogo) ...[
            const Text(
              'Perfecting logo…',
              style: TextStyle(fontSize: 12, color: SwiftColors.muted),
            ),
            SizedBox(height: dense ? 6 : 8),
          ],
          logoButtons,
        ],
      ),
    );
  }

  Widget _buildBulkLabelsPanel({bool dense = false}) {
    final parsed = _bulkParse;
    final chrome = SwiftChromeColors.of(context);
    return _Card(
      title: 'Upload OA or Packing List',
      hint:
          'Upload a Swift Order Acknowledgement or packing list PDF (Propak / Avery 5163). '
          'Lines come from the document.',
      dense: dense,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              FilledButton.tonalIcon(
                onPressed: _busy || _bulkParsing ? null : _pickBulkPdf,
                icon: _bulkParsing
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.upload_file_outlined, size: 18),
                label: Text(_bulkParsing ? 'Reading…' : 'Upload OA or Packing List'),
              ),
              if (parsed != null)
                OutlinedButton.icon(
                  onPressed: _busy ? null : _clearBulkParse,
                  icon: const Icon(Icons.clear, size: 18),
                  label: const Text('Clear'),
                ),
            ],
          ),
          if (_bulkSourcePath != null) ...[
            const SizedBox(height: 10),
            Text(
              _bulkSourcePath == 'sample'
                  ? 'Source: sample fixture'
                  : 'Source: ${p.basename(_bulkSourcePath!)}',
              style: TextStyle(
                fontFamily: swiftUiFont(context),
                fontSize: 12,
                color: chrome.muted,
              ),
            ),
          ],
          if (parsed != null) ...[
            const SizedBox(height: 12),
            Text(
              'PO# ${parsed.poNumber.isEmpty ? '—' : parsed.poNumber}'
              '${parsed.orderNumber.isEmpty ? '' : '  ·  Order ${parsed.orderNumber}'}'
              '\n${parsed.lines.length} lines → ${parsed.totalLabels} labels'
              ' on ${parsed.sheetCount} Avery 5163 sheet'
              '${parsed.sheetCount == 1 ? '' : 's'}',
              style: TextStyle(
                fontFamily: swiftUiFont(context),
                fontWeight: FontWeight.w600,
                fontSize: 13,
                color: chrome.ink,
              ),
            ),
            if (parsed.warnings.isNotEmpty) ...[
              const SizedBox(height: 8),
              ...parsed.warnings.take(5).map(
                    (w) => Padding(
                      padding: const EdgeInsets.only(bottom: 2),
                      child: Text(
                        '• $w',
                        style: TextStyle(
                          fontFamily: swiftUiFont(context),
                          fontSize: 12,
                          color: SwiftColors.accent,
                        ),
                      ),
                    ),
                  ),
            ],
            const SizedBox(height: 10),
            DecoratedBox(
              decoration: BoxDecoration(
                border: Border.all(color: chrome.border),
                borderRadius: BorderRadius.circular(8),
                color: chrome.surface,
              ),
              child: SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: DataTable(
                  headingRowHeight: 36,
                  dataRowMinHeight: 32,
                  dataRowMaxHeight: 44,
                  columns: const [
                    DataColumn(label: Text('Line')),
                    DataColumn(label: Text('CPO')),
                    DataColumn(label: Text('Kind')),
                    DataColumn(label: Text('Identity')),
                    DataColumn(label: Text('Qty'), numeric: true),
                    DataColumn(label: Text('Tags')),
                    DataColumn(label: Text('AI')),
                  ],
                  rows: [
                    for (final line in parsed.lines)
                      DataRow(
                        cells: [
                          DataCell(Text('${line.lineNo}')),
                          DataCell(Text(line.cpoDisplay)),
                          DataCell(
                            Text(() {
                              final blank = line.tagOrPart.trim().isEmpty;
                              if (blank) return 'TAG#*';
                              // AI-filled gaps stay marked with * so the
                              // user confirms before trusting the sticker.
                              return line.missingIdentity
                                  ? '${line.idKind.fieldLabel}*'
                                  : line.idKind.fieldLabel;
                            }()),
                            showEditIcon: true,
                            onTap: () => _editBulkLineIdentity(line),
                          ),
                          DataCell(
                            SizedBox(
                              width: 160,
                              child: Text(
                                () {
                                  final id = line.tagOrPart.trim();
                                  if (id.isEmpty) {
                                    return '(blank — check PM)';
                                  }
                                  // Show Claude/AI-suggested values — do not
                                  // hide them behind the blank placeholder.
                                  return id;
                                }(),
                                overflow: TextOverflow.ellipsis,
                                style: line.missingIdentity
                                    ? TextStyle(
                                        fontStyle: FontStyle.italic,
                                        color: SwiftColors.accent,
                                      )
                                    : null,
                              ),
                            ),
                            showEditIcon: true,
                            onTap: () => _editBulkLineIdentity(line),
                          ),
                          DataCell(Text('${line.quantity}')),
                          DataCell(_buildBulkPrintModeControl(line)),
                          DataCell(
                            (line.aiNote ?? '').trim().isEmpty
                                ? const SizedBox.shrink()
                                : IconButton(
                                    tooltip: 'Claude’s reasoning for this line',
                                    icon: const Icon(
                                      Icons.psychology_outlined,
                                      size: 18,
                                    ),
                                    onPressed: () => _showBulkAiNote(line),
                                  ),
                          ),
                        ],
                      ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 6),
            Text(
              'Tap Kind or Identity on any line to confirm or fix it — '
              'italic values with * are AI-suggested and not yet confirmed.',
              style: TextStyle(
                fontFamily: swiftUiFont(context),
                fontSize: 11,
                color: chrome.muted,
              ),
            ),
          ] else ...[
            const SizedBox(height: 12),
            Text(
              'Each sticker prints the Propak logo with PO#, CPO LINE #, and '
              'TAG# (valves), PART#, or ITEM# (fittings). Use the Tags column '
              'to choose 1 sticker for the whole line or 1 per unit. Press '
              'Generate to save Word (.docx) + PDF and open the editable Word '
              'labels (same as other documents).',
              style: TextStyle(
                fontFamily: swiftUiFont(context),
                fontSize: 13,
                color: chrome.muted,
                height: 1.35,
              ),
            ),
          ],
        ],
      ),
    );
  }

  List<Widget> _buildDocumentFormCards({required bool dualColumn}) {
    if (_kind == LabelKind.bulk) {
      return [_buildBulkLabelsPanel(dense: dualColumn)];
    }

    final cards = <Widget>[
      for (final group in _activeGroups)
        _Card(
          title: group.$1,
          hint: group.$2,
          dense: dualColumn,
          child: Column(
            children: [
              if (_kind == LabelKind.shipping &&
                  group.$1 == 'Carrier & billing') ...[
                _buildFormField(LabelFields.carrier),
                _buildFreightChargesSelector(),
                _buildFormField(BolFields.thirdPartyBilling),
              ] else ...[
                _buildFieldsBlock(group.$3, dualColumn: dualColumn),
                if (_kind == LabelKind.bol && group.$1 == 'Billing & freight')
                  _buildFreightChargesSelector(),
              ],
            ],
          ),
        ),
    ];

    if (_kind == LabelKind.bol) {
      for (var lineNum = 1; lineNum <= _bolLineCount; lineNum++) {
        cards.add(
          _Card(
            title: 'Line $lineNum',
            hint: _bolLineHint(lineNum),
            dense: dualColumn,
            trailing: lineNum > 1
                ? IconButton(
                    tooltip: 'Remove line',
                    onPressed: () => _removeBolLine(lineNum),
                    icon: const Icon(Icons.delete_outline, size: 20),
                  )
                : null,
            child: _buildFieldsBlock(
              _bolLineFieldKeys(lineNum),
              dualColumn: dualColumn,
            ),
          ),
        );
      }
      if (_bolLineCount < _bolMaxLines) {
        cards.add(
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Align(
              alignment: Alignment.centerLeft,
              child: dualColumn
                  ? OutlinedButton.icon(
                      onPressed: _addBolLine,
                      icon: const Icon(Icons.add, size: 18),
                      label: const Text('Add line'),
                    )
                  : IconButton.filledTonal(
                      tooltip: 'Add line',
                      onPressed: _addBolLine,
                      icon: const Icon(Icons.add),
                    ),
            ),
          ),
        );
      }
      cards.add(
        _Card(
          title: _bolSignaturesGroup.$1,
          hint: _bolSignaturesGroup.$2,
          dense: dualColumn,
          child: Column(
            children: [
              _buildShipperSignatureRow(),
              _buildFieldsBlock(
                _bolSignaturesGroup.$3,
                dualColumn: dualColumn,
              ),
            ],
          ),
        ),
      );
    }

    return cards;
  }

  Widget _buildUtilityActions({bool toolbarStyle = false}) {
    if (toolbarStyle) {
      return Wrap(
        spacing: 4,
        runSpacing: 4,
        children: [
          TextButton.icon(
              onPressed: _isDialogLocked('history') ? null : _openHistory,
              icon: const Icon(Icons.history, size: 16),
              label: const Text('History'),
            ),
          TextButton.icon(
            onPressed: _loadSample,
            icon: const Icon(Icons.science_outlined, size: 16),
            label: const Text('Load sample'),
          ),
          TextButton.icon(
            onPressed: _clearShipment,
            icon: const Icon(Icons.clear_all, size: 16),
            label: const Text('Clear shipment'),
          ),
          TextButton.icon(
            onPressed: _clearAll,
            icon: const Icon(Icons.delete_outline, size: 16),
            label: const Text('Clear all'),
          ),
        ],
      );
    }
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        TextButton(
            onPressed: _isDialogLocked('history') ? null : _openHistory,
            child: const Text('History'),
          ),
        TextButton(onPressed: _loadSample, child: const Text('Load sample')),
        TextButton(
          onPressed: _clearShipment,
          child: const Text('Clear shipment'),
        ),
        TextButton(onPressed: _clearAll, child: const Text('Clear all')),
      ],
    );
  }

  Widget _buildWindowsScaffold(BuildContext context) {
    final chrome = SwiftChromeColors.of(context);
    final width = MediaQuery.sizeOf(context).width;
    final preset = _uiSettings.layoutPreset;
    final wide = switch (preset) {
      UiLayoutPreset.widescreen => width >= 1000,
      UiLayoutPreset.compact => width >= 1400,
      UiLayoutPreset.stacked => false,
      UiLayoutPreset.classic => width >= 1180,
    };
    final showWorkspace = _uiSettings.showWorkspacePane &&
        (preset == UiLayoutPreset.stacked || wide);
    final stacked = preset == UiLayoutPreset.stacked && showWorkspace;
    final sideWorkspace = showWorkspace && !stacked;
    final railExtended = _uiSettings.preferExtendedRail
        ? width >= (preset == UiLayoutPreset.compact ? 1280 : 1100)
        : false;
    final dense = _uiSettings.denseForms || preset == UiLayoutPreset.compact;
    final dualColumn = _uiSettings.formColumns >= 2;

    final menuActions = WindowsMenuActions(
      storage: widget.storage,
      settings: _uiSettings,
      kind: _kind,
      busy: _busy,
      bolStoreCopy: _bolStoreCopy,
      bolDriverCopy: _bolDriverCopy,
      bolCustomerCopy: _bolCustomerCopy,
      onNewDocument: _newDocument,
      onGenerate: _generateAndShare,
      onClearShipment: _clearShipment,
      onClearAll: _clearAll,
      onSavePreset: _savePreset,
      onDeletePreset: _deletePreset,
      onSelectKind: _selectKind,
      onBolCopyChanged: _onBolCopyChanged,
      onSettingsChanged: _applyUiSettings,
      onFindLogo: _findLogoOnWeb,
      onBrowseLogo: _browseAndImportLogo,
      onAddFromStorage: _addFromStorageAndImport,
      onLoadSample: _loadSample,
    );

    final formList = ListView(
      controller: _desktopFormScroll,
      primary: false,
      padding: EdgeInsets.fromLTRB(dense ? 16 : 24, 8, dense ? 16 : 24, 28),
      children: [
        if (!sideWorkspace) ...[
          if (_kind == LabelKind.bol) ...[
            _buildBolCopiesCard(compact: true),
            const SizedBox(height: 4),
          ],
          if (_kind != LabelKind.bulk) ..._presetOaLogoCards(dense: true),
        ],
        ..._buildDocumentFormCards(dualColumn: dualColumn),
        const SizedBox(height: 4),
        _buildUtilityActions(toolbarStyle: true),
        // When the workspace pane is hidden (narrow window / user toggle),
        // keep Generate visible in the form column — menu/Ctrl+Enter still work.
        if (!showWorkspace) ...[
          const SizedBox(height: 12),
          _buildGenerateControls(dense: dense, stackVertically: true),
        ],
      ],
    );

    final workspacePane = Material(
      color: chrome.panel,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
            child: Text(
              'Workspace',
              style: TextStyle(
                fontFamily: swiftUiFont(context),
                fontWeight: FontWeight.w600,
                fontSize: 14,
                letterSpacing: 0.4,
                color: chrome.ink,
              ),
            ),
          ),
          Expanded(
            child: Scrollbar(
              controller: _desktopWorkspaceScroll,
              thumbVisibility: true,
              interactive: true,
              child: ListView(
                controller: _desktopWorkspaceScroll,
                primary: false,
                padding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
                children: [
                  if (_kind != LabelKind.bulk)
                    ..._presetOaLogoCards(dense: true),
                  if (_kind == LabelKind.bol) _buildBolCopiesCard(compact: true),
                  if (_kind == LabelKind.bulk)
                    Text(
                      'Avery 5163 · Propak template',
                      style: TextStyle(
                        fontFamily: swiftUiFont(context),
                        fontSize: 12,
                        color: chrome.muted,
                      ),
                    ),
                ],
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 16),
            child: _buildGenerateControls(dense: true),
          ),
        ],
      ),
    );

    return CallbackShortcuts(
      bindings: windowsShortcutMap(
        onGenerate: _generateAndShare,
        onShipping: () => _selectKind(LabelKind.shipping),
        onReceiving: () => _selectKind(LabelKind.receiving),
        onBol: () => _selectKind(LabelKind.bol),
        onBulk: () => _selectKind(LabelKind.bulk),
        onSavePreset: _savePreset,
        onClearShipment: _clearShipment,
        onNewShipping: () => _newDocument(LabelKind.shipping),
        onCheckUpdates: () => showUpdateFlow(context),
        onErrorCapture: _openErrorCapture,
        onToggleDark: _toggleDarkMode,
        onFindLogo: _findLogoOnWeb,
        overrides: _uiSettings.hotkeyOverrides,
      ),
      child: Focus(
        autofocus: true,
        child: Scaffold(
          backgroundColor: chrome.bg,
          body: Column(
            children: [
              Material(
                color: chrome.surface,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    WindowsAppMenuBar(actions: menuActions),
                    const Divider(height: 1),
                    RepaintBoundary(
                      child: _DesktopToolbar(
                        busy: _busy,
                        kindTitle: _kindTitle,
                        showUpdate: _uiSettings.showToolbarUpdate,
                        onUpdate: () => showUpdateFlow(context),
                        onToggleDark: _toggleDarkMode,
                        isDark: _uiSettings.isDark,
                      ),
                    ),
                  ],
                ),
              ),
              const Divider(height: 1),
              Expanded(
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    SizedBox(
                      width: railExtended ? 200 : 88,
                      child: ColoredBox(
                        color: chrome.panel,
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            Expanded(
                              child: NavigationRail(
                      extended: railExtended,
                      minExtendedWidth: 200,
                      backgroundColor: chrome.panel,
                      selectedIndex: _kindRailIndex,
                      labelType: railExtended
                          ? NavigationRailLabelType.none
                          : NavigationRailLabelType.all,
                      onDestinationSelected: (i) {
                        _selectKind(switch (i) {
                          1 => LabelKind.receiving,
                          2 => LabelKind.bol,
                          3 => LabelKind.bulk,
                          _ => LabelKind.shipping,
                        });
                      },
                      leading: Padding(
                        padding: EdgeInsets.only(
                          top: 10,
                          bottom: railExtended ? 16 : 10,
                        ),
                        child: Container(
                          width: railExtended ? 40 : 36,
                          height: 36,
                          decoration: BoxDecoration(
                            color: chrome.accentSoft,
                            borderRadius: BorderRadius.circular(8),
                            border: Border.all(
                              color: SwiftColors.accent.withValues(alpha: 0.22),
                            ),
                          ),
                          child: const Icon(
                            Icons.description_outlined,
                            color: SwiftColors.accent,
                            size: 20,
                          ),
                        ),
                      ),
                      destinations: const [
                        NavigationRailDestination(
                          icon: Icon(Icons.local_shipping_outlined),
                          selectedIcon: Icon(Icons.local_shipping),
                          label: Text('Shipping'),
                        ),
                        NavigationRailDestination(
                          icon: Icon(Icons.inventory_2_outlined),
                          selectedIcon: Icon(Icons.inventory_2),
                          label: Text('Receiving'),
                        ),
                        NavigationRailDestination(
                          icon: Icon(Icons.receipt_long_outlined),
                          selectedIcon: Icon(Icons.receipt_long),
                          label: Text('BOL'),
                        ),
                        NavigationRailDestination(
                          icon: Icon(Icons.grid_view_outlined),
                          selectedIcon: Icon(Icons.grid_view),
                          label: Text('Bulk'),
                        ),
                      ],
                    ),
                            ),
                            OperationsAppsRail(compact: !railExtended),
                          ],
                        ),
                      ),
                    ),
                    const VerticalDivider(width: 1),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Padding(
                            padding: EdgeInsets.fromLTRB(
                              dense ? 16 : 24,
                              16,
                              dense ? 16 : 24,
                              4,
                            ),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  _kindTitle,
                                  style: TextStyle(
                                    fontFamily: swiftUiFont(context),
                                    fontWeight: FontWeight.w600,
                                    fontSize: dense ? 20 : 22,
                                    color: chrome.ink,
                                    letterSpacing: 0.3,
                                  ),
                                ),
                                const SizedBox(height: 4),
                                Text(
                                  _kindHint,
                                  style: TextStyle(
                                    color: chrome.muted,
                                    fontSize: 13,
                                    height: 1.35,
                                  ),
                                ),
                              ],
                            ),
                          ),
                          Expanded(
                            child: stacked
                                ? Column(
                                    children: [
                                      Expanded(
                                        flex: 3,
                                        child: PrimaryScrollController(
                                          controller: _desktopFormScroll,
                                          child: Scrollbar(
                                            controller: _desktopFormScroll,
                                            thumbVisibility: true,
                                            interactive: true,
                                            child: formList,
                                          ),
                                        ),
                                      ),
                                      const Divider(height: 1),
                                      SizedBox(height: 280, child: workspacePane),
                                    ],
                                  )
                                : PrimaryScrollController(
                                    controller: _desktopFormScroll,
                                    child: Scrollbar(
                                      controller: _desktopFormScroll,
                                      thumbVisibility: true,
                                      interactive: true,
                                      child: formList,
                                    ),
                                  ),
                          ),
                        ],
                      ),
                    ),
                    if (sideWorkspace) ...[
                      const VerticalDivider(width: 1),
                      SizedBox(width: 340, child: workspacePane),
                    ],
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildMobileScaffold() {
    final portrait =
        MediaQuery.orientationOf(context) == Orientation.portrait;
    if (!portrait) {
      return _buildMobileLandscapeScaffold();
    }
    return _buildMobilePortraitScaffold();
  }

  /// Portrait: existing stacked form + Chrome-like collapsing header.
  Widget _buildMobilePortraitScaffold() {
    final chrome = SwiftChromeColors.of(context);

    final expandedChrome = Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        RepaintBoundary(
          child: _Header(
            busy: _busy,
            isDark: _uiSettings.isDark,
            kindTitle: _kindTitle,
            onToggleDark: _toggleDarkMode,
          ),
        ),
        Material(
          color: chrome.surface,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                _buildMobileKindSelector(),
                const SizedBox(height: 8),
                Text(
                  _kindHint,
                  maxLines: 4,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: chrome.muted,
                    fontSize: 13,
                    height: 1.35,
                  ),
                ),
              ],
            ),
          ),
        ),
        Divider(height: 1, color: chrome.border),
      ],
    );

    final collapsedChrome = _MobileChromeCollapsed(
      kindTitle: _kindTitle,
      isDark: _uiSettings.isDark,
      onExpand: () => _setMobileChromeExpanded(true),
    );

    return Scaffold(
      backgroundColor: chrome.bg,
      body: SafeArea(
        child: Column(
          children: [
            AnimatedBuilder(
              animation: _mobileChromeT,
              builder: (context, _) {
                final t = _mobileChromeCollapseEnabled
                    ? _mobileChromeT.value
                    : 1.0;
                return _MobileChromeTransition(
                  progress: t,
                  expanded: expandedChrome,
                  collapsed: collapsedChrome,
                );
              },
            ),
            Expanded(
              child: NotificationListener<ScrollNotification>(
                onNotification: _onMobileFormScroll,
                child: ListView(
                  keyboardDismissBehavior:
                      ScrollViewKeyboardDismissBehavior.onDrag,
                  padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
                  children: [
                    if (_kind == LabelKind.bol) ...[
                      _buildBolCopiesCard(),
                      const SizedBox(height: 10),
                    ],
                    if (_kind != LabelKind.bulk) ..._presetOaLogoCards(),
                    ..._buildDocumentFormCards(dualColumn: false),
                    _buildUtilityActions(),
                  ],
                ),
              ),
            ),
            RepaintBoundary(
              child: _BottomBar(
                busy: _busy,
                boxSizedLabel: _boxSizedAllowed && _boxSizedLabel,
                boxSizedEnabled: _boxSizedAllowed && !_busy,
                onBoxSizedChanged: (v) =>
                    setState(() => _boxSizedLabel = v ?? false),
                onGenerate: _generateAndShare,
                generateLabel:
                    _kind == LabelKind.bulk ? 'Generate' : 'Generate PDF',
                generateIcon: _kind == LabelKind.bulk
                    ? Icons.description_outlined
                    : Icons.picture_as_pdf_outlined,
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// Landscape phones: Windows-like rail + form + side workspace / Generate.
  Widget _buildMobileLandscapeScaffold() {
    final chrome = SwiftChromeColors.of(context);
    final width = MediaQuery.sizeOf(context).width;
    final height = MediaQuery.sizeOf(context).height;
    final short = height < 420;
    // Side workspace only when both wide enough and tall enough to not crush.
    final showWorkspace = width >= 780 && height >= 400;

    final formList = ListView(
      keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
      padding: EdgeInsets.fromLTRB(12, short ? 4 : 6, 12, 12),
      children: [
        if (!showWorkspace) ...[
          if (_kind == LabelKind.bol) ...[
            _buildBolCopiesCard(compact: true),
            const SizedBox(height: 6),
          ],
          if (_kind != LabelKind.bulk) ..._presetOaLogoCards(dense: true),
        ],
        ..._buildDocumentFormCards(dualColumn: width >= 980),
        _buildUtilityActions(toolbarStyle: true),
        if (!showWorkspace) ...[
          const SizedBox(height: 8),
          _buildGenerateControls(
            dense: true,
            stackVertically: short,
            buttonHeight: short ? 36 : 40,
          ),
        ],
      ],
    );

    final workspacePane = Material(
      color: chrome.panel,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(10, 8, 10, 4),
            child: Text(
              'Workspace',
              style: TextStyle(
                fontFamily: swiftUiFont(context),
                fontWeight: FontWeight.w600,
                fontSize: 12,
                letterSpacing: 0.4,
                color: chrome.ink,
              ),
            ),
          ),
          Expanded(
            child: ListView(
              padding: const EdgeInsets.fromLTRB(8, 0, 8, 6),
              children: [
                if (_kind != LabelKind.bulk)
                  ..._presetOaLogoCards(dense: true),
                if (_kind == LabelKind.bol) _buildBolCopiesCard(compact: true),
                if (_kind == LabelKind.bulk)
                  Text(
                    'Avery 5163 · Propak',
                    style: TextStyle(
                      fontFamily: swiftUiFont(context),
                      fontSize: 12,
                      color: chrome.muted,
                    ),
                  ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(8, 0, 8, 8),
            child: _buildGenerateControls(
              dense: true,
              stackVertically: true,
              buttonHeight: short ? 36 : 40,
            ),
          ),
        ],
      ),
    );

    return Scaffold(
      backgroundColor: chrome.bg,
      body: SafeArea(
        child: Column(
          children: [
            _MobileLandscapeToolbar(
              busy: _busy,
              isDark: _uiSettings.isDark,
              kindTitle: _kindTitle,
              onToggleDark: _toggleDarkMode,
              compact: short,
            ),
            Divider(height: 1, color: chrome.border),
            Expanded(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  SizedBox(
                    width: short ? 56 : 72,
                    child: ColoredBox(
                      color: chrome.panel,
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Expanded(
                            child: NavigationRail(
                    extended: false,
                    minWidth: short ? 56 : 64,
                    backgroundColor: chrome.panel,
                    selectedIndex: _kindRailIndex,
                    // Labels eat vertical space on short landscape phones.
                    labelType: short
                        ? NavigationRailLabelType.selected
                        : NavigationRailLabelType.all,
                    groupAlignment: short ? 0 : -0.85,
                    onDestinationSelected: (i) {
                      _selectKind(switch (i) {
                        1 => LabelKind.receiving,
                        2 => LabelKind.bol,
                        3 => LabelKind.bulk,
                        _ => LabelKind.shipping,
                      });
                    },
                    destinations: [
                      NavigationRailDestination(
                        icon: Icon(Icons.local_shipping_outlined,
                            size: short ? 18 : 20),
                        selectedIcon:
                            Icon(Icons.local_shipping, size: short ? 18 : 20),
                        label: const Text('Ship'),
                      ),
                      NavigationRailDestination(
                        icon: Icon(Icons.inventory_2_outlined,
                            size: short ? 18 : 20),
                        selectedIcon:
                            Icon(Icons.inventory_2, size: short ? 18 : 20),
                        label: const Text('Recv'),
                      ),
                      NavigationRailDestination(
                        icon: Icon(Icons.receipt_long_outlined,
                            size: short ? 18 : 20),
                        selectedIcon:
                            Icon(Icons.receipt_long, size: short ? 18 : 20),
                        label: const Text('BOL'),
                      ),
                      NavigationRailDestination(
                        icon: Icon(Icons.grid_view_outlined,
                            size: short ? 18 : 20),
                        selectedIcon:
                            Icon(Icons.grid_view, size: short ? 18 : 20),
                        label: const Text('Bulk'),
                      ),
                    ],
                  ),
                            ),
                            OperationsAppsRail(compact: true),
                          ],
                        ),
                      ),
                    ),
                  VerticalDivider(width: 1, color: chrome.border),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        if (!short)
                          Padding(
                            padding: const EdgeInsets.fromLTRB(12, 4, 12, 0),
                            child: Text(
                              _kindHint,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(
                                color: chrome.muted,
                                fontSize: 11,
                                height: 1.2,
                              ),
                            ),
                          ),
                        Expanded(child: formList),
                      ],
                    ),
                  ),
                  if (showWorkspace) ...[
                    VerticalDivider(width: 1, color: chrome.border),
                    SizedBox(
                      width: width >= 980 ? 260 : 220,
                      child: workspacePane,
                    ),
                  ],
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// Collapse portrait chrome when the user scrolls down the form.
  /// Scrolling back up near the top (or settling at the top) restores it.
  /// Disabled on Bulk (short page) and in landscape.
  bool _onMobileFormScroll(ScrollNotification notification) {
    if (Platform.isWindows) return false;
    if (!_mobileChromeCollapseEnabled) {
      if (_mobileChromeCtrl.value < 1) {
        _setMobileChromeExpanded(true);
      }
      return false;
    }
    if (notification.metrics.axis != Axis.vertical) return false;
    final pixels = notification.metrics.pixels;
    final expanded = _mobileChromeCtrl.value > 0.5;

    // Fling/settle at the absolute top should always restore chrome.
    if (!expanded &&
        notification is ScrollEndNotification &&
        pixels <= 24) {
      _setMobileChromeExpanded(true);
      return false;
    }

    if (notification is! ScrollUpdateNotification) return false;
    final delta = notification.scrollDelta ?? 0;
    if (delta > 4 && pixels > 20 && expanded) {
      _setMobileChromeExpanded(false);
    } else if (!expanded && delta < -2) {
      // Any upward scroll re-shows chrome (Chrome-like), not only near top.
      _setMobileChromeExpanded(true);
    }
    return false;
  }

  @override
  Widget build(BuildContext context) {
    final child = Platform.isWindows
        ? _buildWindowsScaffold(context)
        : _buildMobileScaffold();
    if (!_restoringLogo) return child;
    return Stack(
      children: [
        child,
        const ModalBarrier(dismissible: false, color: Color(0x66000000)),
        Center(
          child: Card(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(28, 24, 28, 20),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Align(
                    alignment: Alignment.centerRight,
                    child: IconButton(
                      tooltip: 'Cancel',
                      onPressed: _abortLogoRestore,
                      icon: const Icon(Icons.close),
                    ),
                  ),
                  const SizedBox(
                    width: 36,
                    height: 36,
                    child: CircularProgressIndicator(strokeWidth: 3),
                  ),
                  const SizedBox(height: 16),
                  Text(
                    'Perfecting logo…',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  const SizedBox(height: 6),
                  Text(
                    'Enhancing the raster for print. Press X to abort.',
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ],
              ),
            ),
          ),
        ),
      ],
    );
  }
}


/// Compact Windows title strip — brand left, document context, optional Update.
/// Generate PDF lives in Workspace / menu only (not duplicated here).
class _DesktopToolbar extends StatelessWidget {
  const _DesktopToolbar({
    required this.busy,
    required this.kindTitle,
    required this.showUpdate,
    required this.onUpdate,
    required this.onToggleDark,
    required this.isDark,
  });

  final bool busy;
  final String kindTitle;
  final bool showUpdate;
  final VoidCallback onUpdate;
  final VoidCallback onToggleDark;
  final bool isDark;

  @override
  Widget build(BuildContext context) {
    final chrome = SwiftChromeColors.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 7, 14, 7),
      child: Row(
        children: [
          Container(
            width: 3,
            height: 28,
            decoration: BoxDecoration(
              color: SwiftColors.accent,
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          const SizedBox(width: 10),
          SwiftChromeLogo(height: 26, isDark: isDark),
          const SizedBox(width: 12),
          Text(
            'Swift Document Generator',
            style: TextStyle(
              fontFamily: swiftUiFont(context),
              fontWeight: FontWeight.w600,
              fontSize: 15,
              letterSpacing: 0.3,
              color: chrome.ink,
            ),
          ),
          const SizedBox(width: 12),
          Container(
            width: 1,
            height: 18,
            color: chrome.border,
          ),
          const SizedBox(width: 12),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
            decoration: BoxDecoration(
              color: chrome.accentSoft,
              borderRadius: BorderRadius.circular(6),
              border: Border.all(
                color: SwiftColors.accent.withValues(alpha: 0.18),
              ),
            ),
            child: Text(
              kindTitle,
              style: TextStyle(
                fontFamily: Theme.of(context).textTheme.bodyMedium?.fontFamily,
                fontWeight: FontWeight.w600,
                fontSize: 12,
                letterSpacing: 0.3,
                color: SwiftColors.accent,
              ),
            ),
          ),
          const Spacer(),
          if (busy)
            const Padding(
              padding: EdgeInsets.only(right: 12),
              child: SizedBox(
                width: 18,
                height: 18,
                child: CircularProgressIndicator(strokeWidth: 2),
              ),
            ),
          IconButton(
            tooltip: isDark
                ? 'Light mode (Ctrl+Shift+D)'
                : 'Dark mode (Ctrl+Shift+D)',
            onPressed: busy ? null : onToggleDark,
            icon: Icon(
              isDark
                  ? Icons.light_mode_outlined
                  : Icons.dark_mode_outlined,
            ),
          ),
          if (showUpdate) ...[
            const SizedBox(width: 4),
            OutlinedButton.icon(
              onPressed: busy ? null : onUpdate,
              icon: const Icon(Icons.system_update_alt, size: 16),
              label: const Text('Update'),
            ),
          ],
        ],
      ),
    );
  }
}

/// Continuous Chrome-like morph between full header and collapsed strip.
/// [progress] 1 = fully expanded, 0 = fully collapsed.
class _MobileChromeTransition extends StatelessWidget {
  const _MobileChromeTransition({
    required this.progress,
    required this.expanded,
    required this.collapsed,
  });

  final double progress;
  final Widget expanded;
  final Widget collapsed;

  @override
  Widget build(BuildContext context) {
    final t = progress.clamp(0.0, 1.0);
    // Soft fade so layers blend during the slide (not a hard cut).
    final expandOpacity = Curves.easeOut.transform(t);
    final collapseOpacity = Curves.easeIn.transform(1 - t);

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        ClipRect(
          child: Align(
            alignment: Alignment.topCenter,
            heightFactor: t,
            child: Opacity(
              opacity: expandOpacity,
              child: Transform.translate(
                offset: Offset(0, -18 * (1 - t)),
                child: expanded,
              ),
            ),
          ),
        ),
        ClipRect(
          child: Align(
            alignment: Alignment.bottomCenter,
            heightFactor: 1 - t,
            child: Opacity(
              opacity: collapseOpacity,
              child: Transform.translate(
                offset: Offset(0, 10 * t),
                child: collapsed,
              ),
            ),
          ),
        ),
      ],
    );
  }
}

/// Compact single-row toolbar for Android landscape (Windows-like density).
class _MobileLandscapeToolbar extends StatelessWidget {
  const _MobileLandscapeToolbar({
    required this.busy,
    required this.isDark,
    required this.kindTitle,
    required this.onToggleDark,
    this.compact = false,
  });

  final bool busy;
  final bool isDark;
  final String kindTitle;
  final VoidCallback onToggleDark;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final chrome = SwiftChromeColors.of(context);
    return Material(
      color: chrome.surface,
      child: Padding(
        padding: EdgeInsets.fromLTRB(10, compact ? 2 : 4, 6, compact ? 2 : 4),
        child: Row(
          children: [
            Container(
              width: 3,
              height: compact ? 18 : 22,
              decoration: BoxDecoration(
                color: SwiftColors.accent,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            const SizedBox(width: 8),
            SwiftChromeLogo(height: compact ? 16 : 20, isDark: isDark),
            const SizedBox(width: 8),
            Flexible(
              child: Text(
                compact ? kindTitle : 'Swift Document Generator',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontFamily: swiftUiFont(context),
                  fontWeight: FontWeight.w600,
                  fontSize: compact ? 12 : 13,
                  letterSpacing: 0.2,
                  color: chrome.ink,
                ),
              ),
            ),
            if (!compact) ...[
              const SizedBox(width: 8),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                decoration: BoxDecoration(
                  color: chrome.accentSoft,
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(
                    color: SwiftColors.accent.withValues(alpha: 0.18),
                  ),
                ),
                child: Text(
                  kindTitle,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontFamily:
                        Theme.of(context).textTheme.bodyMedium?.fontFamily,
                    fontWeight: FontWeight.w600,
                    fontSize: 11,
                    color: SwiftColors.accent,
                  ),
                ),
              ),
            ],
            if (busy)
              const Padding(
                padding: EdgeInsets.symmetric(horizontal: 8),
                child: SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
              )
            else ...[
              IconButton(
                tooltip: isDark ? 'Light mode' : 'Dark mode',
                onPressed: onToggleDark,
                visualDensity: VisualDensity.compact,
                iconSize: compact ? 18 : 20,
                icon: Icon(
                  isDark
                      ? Icons.light_mode_outlined
                      : Icons.dark_mode_outlined,
                  color: chrome.ink,
                ),
              ),
              if (!compact)
                IconButton(
                  tooltip: 'Update',
                  onPressed: () => showUpdateFlow(context),
                  visualDensity: VisualDensity.compact,
                  iconSize: 20,
                  icon: Icon(Icons.system_update_alt, color: chrome.ink),
                ),
            ],
          ],
        ),
      ),
    );
  }
}

/// Thin portrait strip shown after the Android header collapses on scroll.
class _MobileChromeCollapsed extends StatelessWidget {
  const _MobileChromeCollapsed({
    required this.kindTitle,
    required this.isDark,
    required this.onExpand,
  });

  final String kindTitle;
  final bool isDark;
  final VoidCallback onExpand;

  @override
  Widget build(BuildContext context) {
    final chrome = SwiftChromeColors.of(context);
    return Material(
      color: chrome.surface,
      elevation: 0,
      child: InkWell(
        onTap: onExpand,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 6, 4, 6),
              child: Row(
                children: [
                  Container(
                    width: 3,
                    height: 22,
                    decoration: BoxDecoration(
                      color: SwiftColors.accent,
                      borderRadius: BorderRadius.circular(2),
                    ),
                  ),
                  const SizedBox(width: 8),
                  SwiftChromeLogo(height: 18, isDark: isDark),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      kindTitle,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontFamily: swiftUiFont(context),
                        fontWeight: FontWeight.w600,
                        fontSize: 13,
                        letterSpacing: 0.3,
                        color: chrome.ink,
                      ),
                    ),
                  ),
                  IconButton(
                    tooltip: 'Show header',
                    onPressed: onExpand,
                    icon: Icon(
                      Icons.expand_more,
                      color: chrome.ink,
                    ),
                  ),
                ],
              ),
            ),
            Divider(height: 1, color: chrome.border),
          ],
        ),
      ),
    );
  }
}

/// Android / mobile header — Windows-like surface chrome, compact layout.
class _Header extends StatelessWidget {
  const _Header({
    required this.busy,
    required this.isDark,
    required this.kindTitle,
    required this.onToggleDark,
  });

  final bool busy;
  final bool isDark;
  final String kindTitle;
  final VoidCallback onToggleDark;

  @override
  Widget build(BuildContext context) {
    final chrome = SwiftChromeColors.of(context);
    final narrow = MediaQuery.sizeOf(context).width < 420;
    return Material(
      color: chrome.surface,
      elevation: 0,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: EdgeInsets.fromLTRB(14, 10, narrow ? 6 : 10, 10),
            child: Row(
              children: [
                Container(
                  width: 3,
                  height: 36,
                  decoration: BoxDecoration(
                    color: SwiftColors.accent,
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
                const SizedBox(width: 10),
                SwiftChromeLogo(height: 30, isDark: isDark),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        narrow
                            ? 'Swift Document Gen'
                            : 'Swift Document Generator',
                        maxLines: 1,
                        softWrap: false,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          fontFamily: swiftUiFont(context),
                          fontWeight: FontWeight.w600,
                          fontSize: 15,
                          letterSpacing: 0.3,
                          color: chrome.ink,
                        ),
                      ),
                      const SizedBox(height: 3),
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 7,
                          vertical: 2,
                        ),
                        decoration: BoxDecoration(
                          color: chrome.accentSoft,
                          borderRadius: BorderRadius.circular(6),
                          border: Border.all(
                            color: SwiftColors.accent.withValues(alpha: 0.18),
                          ),
                        ),
                        child: Text(
                          kindTitle,
                          maxLines: 1,
                          softWrap: false,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            fontFamily:
                                Theme.of(context).textTheme.bodyMedium?.fontFamily,
                            fontWeight: FontWeight.w600,
                            fontSize: 11,
                            letterSpacing: 0.3,
                            height: 1.1,
                            color: SwiftColors.accent,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
                if (busy)
                  const Padding(
                    padding: EdgeInsets.only(right: 4),
                    child: SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    ),
                  )
                else ...[
                  IconButton(
                    tooltip: isDark ? 'Light mode' : 'Dark mode',
                    onPressed: onToggleDark,
                    visualDensity: VisualDensity.compact,
                    icon: Icon(
                      isDark
                          ? Icons.light_mode_outlined
                          : Icons.dark_mode_outlined,
                      color: chrome.ink,
                    ),
                  ),
                  if (narrow)
                    IconButton(
                      tooltip: 'Update',
                      onPressed: () => showUpdateFlow(context),
                      visualDensity: VisualDensity.compact,
                      icon: Icon(
                        Icons.system_update_alt,
                        color: chrome.ink,
                        size: 20,
                      ),
                    )
                  else
                    OutlinedButton.icon(
                      onPressed: () => showUpdateFlow(context),
                      icon: const Icon(Icons.system_update_alt, size: 16),
                      label: const Text('Update'),
                      style: OutlinedButton.styleFrom(
                        visualDensity: VisualDensity.compact,
                        padding: const EdgeInsets.symmetric(
                          horizontal: 10,
                          vertical: 8,
                        ),
                      ),
                    ),
                ],
              ],
            ),
          ),
          Divider(height: 1, color: chrome.border),
        ],
      ),
    );
  }
}

class _AddressBookEditor extends StatefulWidget {
  const _AddressBookEditor({required this.sync});

  final AddressBookSync sync;

  @override
  State<_AddressBookEditor> createState() => _AddressBookEditorState();
}

class _AddressBookEditorState extends State<_AddressBookEditor> {
  bool _adding = false;
  bool _busy = false;
  final _name = TextEditingController();
  final _address = TextEditingController();
  final _carrier = TextEditingController();
  final _accounts = TextEditingController();

  @override
  void initState() {
    super.initState();
    unawaited(_refreshInBackground());
  }

  /// Show cached rows immediately; sync/OSM must not block the dialog.
  Future<void> _refreshInBackground() async {
    try {
      await widget.sync.fetchAll();
      if (mounted) setState(() {});
    } catch (_) {}
    try {
      await widget.sync.ensureOsmEnrich();
      if (mounted) setState(() {});
    } catch (_) {}
  }

  @override
  void dispose() {
    _name.dispose();
    _address.dispose();
    _carrier.dispose();
    _accounts.dispose();
    super.dispose();
  }

  List<DeliveryAddressEntry> get _entries =>
      AddressBookSync.sortByShipToName(widget.sync.entries);

  Future<void> _add() async {
    final addr = _address.text.trim();
    if (addr.isEmpty) return;
    setState(() => _busy = true);
    try {
      await widget.sync.remember(
        shipToName: _name.text.trim(),
        address: addr,
        carrier: _carrier.text.trim(),
        accountNumbers: _accounts.text.trim(),
      );
      _name.clear();
      _address.clear();
      _carrier.clear();
      _accounts.clear();
      _adding = false;
    } catch (e) {
      if (mounted) {
        showAppSnack(context, 'Could not add address: $e');
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _delete(DeliveryAddressEntry e) async {
    setState(() => _busy = true);
    try {
      await widget.sync.forget(e.addressKey);
    } catch (e) {
      if (mounted) {
        showAppSnack(context, 'Could not delete: $e');
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final entries = _entries;
    return AlertDialog(
      title: const Text('Delivery Address book'),
      content: SizedBox(
        width: 480,
        height: 440,
        child: Column(
          children: [
            Align(
              alignment: Alignment.centerLeft,
              child: TextButton.icon(
                onPressed: _busy
                    ? null
                    : () => setState(() => _adding = !_adding),
                icon: Icon(_adding ? Icons.close : Icons.add),
                label: Text(_adding ? 'Cancel add' : 'Add address'),
              ),
            ),
            if (_adding) ...[
              TextField(
                controller: _name,
                decoration: const InputDecoration(labelText: 'SHIP TO NAME'),
              ),
              AddressSuggestField(
                controller: _address,
                entries: widget.sync.entries,
                shipToName: () => _name.text,
                customer: () => '',
                labelText: 'ADDRESS',
                onPicked: (s) {
                  if (_name.text.trim().isNotEmpty) return;
                  final n = s.placeName.trim();
                  if (n.isEmpty) return;
                  _name.text = n;
                  setState(() {});
                },
                onPickedBookEntry: (e) {
                  if (_name.text.trim().isEmpty &&
                      e.shipToName.trim().isNotEmpty) {
                    _name.text = e.shipToName;
                  }
                  if (_carrier.text.trim().isEmpty && e.carrier.isNotEmpty) {
                    _carrier.text = e.carrier;
                  }
                  if (_accounts.text.trim().isEmpty &&
                      e.accountNumbers.isNotEmpty) {
                    _accounts.text = e.accountNumbers;
                  }
                  setState(() {});
                },
              ),
              TextField(
                controller: _carrier,
                decoration: const InputDecoration(labelText: 'CARRIER'),
              ),
              TextField(
                controller: _accounts,
                decoration: const InputDecoration(labelText: 'ACCOUNT #'),
              ),
              Align(
                alignment: Alignment.centerRight,
                child: FilledButton(
                  onPressed: _busy ? null : _add,
                  child: const Text('Save'),
                ),
              ),
              const Divider(),
            ],
            Expanded(
              child: entries.isEmpty
                  ? const Center(
                      child: Text(
                        'No saved addresses yet.\nAdd one here or they are remembered when you Generate.',
                        textAlign: TextAlign.center,
                      ),
                    )
                  : ListView.separated(
                      itemCount: entries.length,
                      separatorBuilder: (_, _) => const Divider(height: 1),
                      itemBuilder: (context, i) {
                        final e = entries[i];
                        final portrait = MediaQuery.orientationOf(context) ==
                            Orientation.portrait;
                        final android =
                            Theme.of(context).platform == TargetPlatform.android;
                        final addrSize = android && portrait ? 16.0 : 14.5;
                        return InkWell(
                          onTap: () => Navigator.pop(context, e),
                          child: Padding(
                            padding: const EdgeInsets.fromLTRB(8, 10, 4, 10),
                            child: Row(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Expanded(
                                  child: Column(
                                    crossAxisAlignment:
                                        CrossAxisAlignment.start,
                                    children: [
                                      Text(
                                        e.shipToName.isEmpty
                                            ? '(No ship-to name)'
                                            : e.shipToName,
                                        maxLines: 2,
                                        softWrap: true,
                                        overflow: TextOverflow.ellipsis,
                                        style: TextStyle(
                                          fontWeight: FontWeight.w600,
                                          fontSize: addrSize,
                                          height: 1.3,
                                        ),
                                      ),
                                      const SizedBox(height: 4),
                                      Text(
                                        e.address,
                                        maxLines: 4,
                                        softWrap: true,
                                        overflow: TextOverflow.ellipsis,
                                        style: TextStyle(
                                          fontSize: addrSize,
                                          height: 1.35,
                                        ),
                                      ),
                                      if (e.carrier.isNotEmpty) ...[
                                        const SizedBox(height: 4),
                                        Text(
                                          'Carrier: ${e.carrier}',
                                          maxLines: 2,
                                          softWrap: true,
                                          overflow: TextOverflow.ellipsis,
                                          style: TextStyle(
                                            fontSize: addrSize - 1,
                                            height: 1.3,
                                            color: Theme.of(context).hintColor,
                                          ),
                                        ),
                                      ],
                                      if (e.accountNumbers.isNotEmpty) ...[
                                        const SizedBox(height: 2),
                                        Text(
                                          'Acct: ${e.accountNumbers}',
                                          maxLines: 2,
                                          softWrap: true,
                                          overflow: TextOverflow.ellipsis,
                                          style: TextStyle(
                                            fontSize: addrSize - 1,
                                            height: 1.3,
                                            color: Theme.of(context).hintColor,
                                          ),
                                        ),
                                      ],
                                    ],
                                  ),
                                ),
                                IconButton(
                                  tooltip: 'Delete',
                                  onPressed: _busy ? null : () => _delete(e),
                                  icon: const Icon(Icons.delete_outline),
                                ),
                                TextButton(
                                  onPressed: () => Navigator.pop(context, e),
                                  child: const Text('USE'),
                                ),
                              ],
                            ),
                          ),
                        );
                      },
                    ),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Close'),
        ),
      ],
    );
  }
}

class _BottomBar extends StatelessWidget {
  const _BottomBar({
    required this.busy,
    required this.boxSizedLabel,
    required this.boxSizedEnabled,
    required this.onBoxSizedChanged,
    required this.onGenerate,
    this.generateLabel = 'Generate PDF',
    this.generateIcon = Icons.picture_as_pdf_outlined,
  });

  final bool busy;
  final bool boxSizedLabel;
  final bool boxSizedEnabled;
  final ValueChanged<bool?> onBoxSizedChanged;
  final VoidCallback onGenerate;
  final String generateLabel;
  final IconData generateIcon;

  @override
  Widget build(BuildContext context) {
    final chrome = SwiftChromeColors.of(context);
    return Material(
      color: chrome.surface,
      elevation: 0,
      child: DecoratedBox(
        decoration: BoxDecoration(
          border: Border(
            top: BorderSide(color: chrome.border),
          ),
          boxShadow: [
            BoxShadow(
              color: Color(isDarkChrome(context) ? 0x66000000 : 0x14000000),
              blurRadius: 10,
              offset: const Offset(0, -2),
            ),
          ],
        ),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Tooltip(
                message: boxSizedEnabled
                    ? 'Print the label at 50% scale in the top-left quarter of a '
                        'landscape Letter page (5.5″ × 4.25″).'
                    : 'Box-sized labels are only available for Shipping and Receiving labels.',
                child: SwiftCircleCheckbox(
                  value: boxSizedLabel,
                  enabled: boxSizedEnabled,
                  dense: true,
                  label: 'Box Label (1/4 Page)',
                  onChanged: boxSizedEnabled ? onBoxSizedChanged : null,
                ),
              ),
              const SizedBox(height: 10),
              SizedBox(
                width: double.infinity,
                height: 48,
                child: FilledButton.icon(
                  onPressed: busy ? null : onGenerate,
                  icon: busy
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: Colors.white,
                          ),
                        )
                      : Icon(generateIcon, size: 20),
                  label: Text(busy ? 'Generating…' : generateLabel),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

bool isDarkChrome(BuildContext context) =>
    Theme.of(context).brightness == Brightness.dark;

class _Card extends StatelessWidget {
  const _Card({
    required this.title,
    required this.hint,
    required this.child,
    this.trailing,
    this.dense = false,
  });

  final String title;
  final String hint;
  final Widget child;
  final Widget? trailing;
  final bool dense;

  @override
  Widget build(BuildContext context) {
    final chrome = SwiftChromeColors.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Card(
        child: Padding(
          padding: EdgeInsets.fromLTRB(
            dense ? 12 : 14,
            dense ? 10 : 12,
            dense ? 12 : 14,
            dense ? 10 : 12,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Text(
                          title,
                          style: TextStyle(
                            fontFamily: swiftUiFont(context),
                            fontWeight: FontWeight.w600,
                            fontSize: dense ? 14 : 15,
                            color: chrome.ink,
                            letterSpacing: 0.3,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          hint,
                          style: TextStyle(
                            fontSize: dense ? 11 : 12,
                            color: chrome.muted,
                            height: 1.3,
                          ),
                        ),
                      ],
                    ),
                  ),
                  ?trailing,
                ],
              ),
              SizedBox(height: dense ? 8 : 10),
              child,
            ],
          ),
        ),
      ),
    );
  }
}

/// History list: open immediately, fetch cloud rows in the background.
class _HistoryDialog extends StatefulWidget {
  const _HistoryDialog({
    required this.kind,
    required this.sync,
    required this.onOpenPdf,
    required this.onTemplate,
  });

  final LabelKind kind;
  final DocumentHistorySync sync;
  final Future<void> Function(GeneratedDocumentRecord doc) onOpenPdf;
  final void Function(GeneratedDocumentRecord doc) onTemplate;

  @override
  State<_HistoryDialog> createState() => _HistoryDialogState();
}

class _HistoryDialogState extends State<_HistoryDialog> {
  bool _loading = true;
  String? _error;
  List<GeneratedDocumentRecord> _docs = const [];

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  Future<void> _load() async {
    try {
      final docs = await widget.sync.listForKind(widget.kind);
      if (!mounted) return;
      setState(() {
        _docs = docs;
        _loading = false;
        _error = null;
      });
    } on DocumentHistorySyncException catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e.message;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = '$e';
      });
    }
  }

  String get _title => switch (widget.kind) {
        LabelKind.shipping => 'Shipping history',
        LabelKind.receiving => 'Receiving history',
        LabelKind.bol => 'BOL history',
        LabelKind.bulk => 'History',
      };

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text(_title),
      content: SizedBox(
        width: 520,
        height: 440,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? Center(
                    child: Padding(
                      padding: const EdgeInsets.all(16),
                      child: Text(
                        _error!,
                        textAlign: TextAlign.center,
                      ),
                    ),
                  )
                : _docs.isEmpty
                    ? const Center(child: Text('No generated documents yet.'))
                    : ListView.separated(
                        itemCount: _docs.length,
                        separatorBuilder: (_, _) => const Divider(height: 1),
                        itemBuilder: (context, i) {
                          final d = _docs[i];
                          final when =
                              d.createdAt.toLocal().toString().split('.').first;
                          return ListTile(
                            title: Text(
                              d.title.isEmpty ? d.fileName : d.title,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                            subtitle: Text(
                              [
                                if (d.customer.isNotEmpty) d.customer,
                                if (d.salesOrder.isNotEmpty) d.salesOrder,
                                when,
                              ].join(' · '),
                            ),
                            trailing: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                TextButton(
                                  onPressed: () => widget.onTemplate(d),
                                  child: const Text('Template'),
                                ),
                                IconButton(
                                  tooltip: 'Open PDF',
                                  icon: const Icon(Icons.open_in_new, size: 18),
                                  onPressed: () =>
                                      unawaited(widget.onOpenPdf(d)),
                                ),
                              ],
                            ),
                            onTap: () => unawaited(widget.onOpenPdf(d)),
                          );
                        },
                      ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Close'),
        ),
      ],
    );
  }
}

enum _OaShipToChoice { useHeader, manual }
