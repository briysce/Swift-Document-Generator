import 'dart:async';

import 'package:flutter/material.dart';

import 'address_book_sync.dart';
import 'google_places_client.dart';
import 'osm_nominatim_client.dart';

class AddressSuggestion {
  const AddressSuggestion({
    required this.address,
    required this.caption,
    this.fromBook = false,
    this.placeName = '',
  });

  final String address;
  final String caption;
  final bool fromBook;
  /// Ship-to / company name from the address book; empty for OSM hits.
  final String placeName;
}

/// Full current field text for Nominatim — including an incomplete last word.
/// Do not drop the last token or wait for a trailing space.
String addressSearchQuery(String raw) => raw.trim();

bool shouldSearchRemoteAddress(String raw) =>
    addressSearchQuery(raw).length >= 3;

/// Subtitle for an address-book hit. Keeps ship-to / company text.
String savedAddressCaption(String shipToName) {
  final name = shipToName.trim();
  return name.isEmpty ? 'Saved' : 'Saved · $name';
}

/// Overlay rows: optional section headers, then suggestions.
class AddressSuggestOverlayItem {
  const AddressSuggestOverlayItem.header(this.header) : suggestion = null;

  const AddressSuggestOverlayItem.suggestion(this.suggestion) : header = null;

  final String? header;
  final AddressSuggestion? suggestion;

  bool get isHeader => header != null;
}

List<AddressSuggestOverlayItem> addressSuggestOverlayItems(
  List<AddressSuggestion> options,
) {
  final book = options.where((s) => s.fromBook).toList();
  final osm = options.where((s) => !s.fromBook).toList();
  final out = <AddressSuggestOverlayItem>[];
  if (book.isNotEmpty) {
    out.add(const AddressSuggestOverlayItem.header('Saved addresses'));
    for (final s in book) {
      out.add(AddressSuggestOverlayItem.suggestion(s));
    }
  }
  if (osm.isNotEmpty) {
    out.add(const AddressSuggestOverlayItem.header('Suggested addresses'));
    for (final s in osm) {
      out.add(AddressSuggestOverlayItem.suggestion(s));
    }
  }
  return out;
}

List<AddressSuggestion> mergeAddressSuggestions({
  required String raw,
  required List<DeliveryAddressEntry> entries,
  required List<NominatimHit> osmHits,
  int limit = 8,
}) {
  final q = raw.trim().toLowerCase();
  final out = <AddressSuggestion>[];
  final seen = <String>{};

  void add(AddressSuggestion s) {
    final k = s.address.trim().toLowerCase();
    if (k.isEmpty || !seen.add(k)) return;
    out.add(s);
  }

  for (final e in entries) {
    if (q.isEmpty) {
      add(
        AddressSuggestion(
          address: e.address,
          caption: savedAddressCaption(e.shipToName),
          fromBook: true,
          placeName: e.shipToName,
        ),
      );
      continue;
    }
    final blob = '${e.shipToName} ${e.address}'.toLowerCase();
    if (blob.contains(q)) {
      add(
        AddressSuggestion(
          address: e.address,
          caption: savedAddressCaption(e.shipToName),
          fromBook: true,
          placeName: e.shipToName,
        ),
      );
    }
  }

  // Nominatim ranking — keep hits even if they do not substring-match [raw].
  for (final p in osmHits) {
    final name = p.placeName.trim();
    add(
      AddressSuggestion(
        address: p.displayAddress,
        caption: name.isEmpty ? 'Suggested address' : name,
        placeName: name,
      ),
    );
  }

  return out.take(limit).toList();
}

/// Delivery address: address book first, then OpenStreetMap Nominatim.
class AddressSuggestField extends StatefulWidget {
  const AddressSuggestField({
    super.key,
    required this.controller,
    required this.entries,
    required this.shipToName,
    required this.customer,
    this.labelText = 'DELIVERY ADDRESS',
    this.onPickedBookEntry,
    this.onPicked,
  });

  final TextEditingController controller;
  final List<DeliveryAddressEntry> entries;
  final String Function() shipToName;
  final String Function() customer;
  final String labelText;
  final ValueChanged<DeliveryAddressEntry>? onPickedBookEntry;
  final ValueChanged<AddressSuggestion>? onPicked;

  @override
  State<AddressSuggestField> createState() => _AddressSuggestFieldState();
}

class _AddressSuggestFieldState extends State<AddressSuggestField> {
  final _focus = FocusNode();
  final _layerLink = LayerLink();
  final _fieldKey = GlobalKey();
  final _osm = OsmNominatimClient();
  final _places = GooglePlacesClient();
  OverlayEntry? _overlay;
  Timer? _debounce;
  Timer? _hideTimer;
  List<NominatimHit> _osmHits = const [];
  bool _thinking = false;
  int _req = 0;
  String _lastText = '';
  String? _hideAfterPick;

  @override
  void initState() {
    super.initState();
    _focus.addListener(_onFocus);
    _lastText = widget.controller.text;
    widget.controller.addListener(_onText);
  }

  @override
  void didUpdateWidget(covariant AddressSuggestField oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.controller != widget.controller) {
      oldWidget.controller.removeListener(_onText);
      widget.controller.addListener(_onText);
    }
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _hideTimer?.cancel();
    widget.controller.removeListener(_onText);
    _focus.removeListener(_onFocus);
    _removeOverlay();
    _focus.dispose();
    super.dispose();
  }

  void _onFocus() {
    if (_focus.hasFocus) {
      _hideTimer?.cancel();
      _scheduleSuggest(widget.controller.text);
      _syncOverlay();
    } else {
      _tryAttachBusinessName();
      _hideTimer?.cancel();
      _hideTimer = Timer(const Duration(milliseconds: 150), () {
        if (!_focus.hasFocus) _removeOverlay();
      });
    }
  }

  void _onText() {
    final t = widget.controller.text;
    if (t != _lastText) {
      _lastText = t;
      if (_hideAfterPick != null && t != _hideAfterPick) {
        _hideAfterPick = null;
      }
      _scheduleSuggest(t);
    }
    _syncOverlay();
  }

  void _scheduleSuggest(String query) {
    _debounce?.cancel();
    if (!shouldSearchRemoteAddress(query)) {
      if (_osmHits.isNotEmpty) {
        setState(() => _osmHits = const []);
        _syncOverlay();
      }
      return;
    }
    // Nominatim: 1 req/s. Short debounce + stale-response ignore (not Space).
    _debounce = Timer(const Duration(milliseconds: 350), () {
      unawaited(_fetch(addressSearchQuery(query)));
    });
  }

  Future<void> _fetch(String query) async {
    final id = ++_req;
    if (mounted) setState(() => _thinking = true);
    try {
      // Google Places has far better real-world coverage for civic/commercial
      // addresses than free OSM data — try it first when configured. It
      // returns nothing for LSD/lease-road/wellsite queries (not on any
      // commercial map), so those still fall through to Nominatim/Photon
      // exactly as before.
      var hits = GooglePlacesClient.isConfigured
          ? await _places.autocomplete(query)
          : const <NominatimHit>[];
      if (hits.isEmpty) {
        hits = await _osm.search(query);
      }
      if (!mounted || id != _req) return;
      setState(() {
        _osmHits = hits;
        _thinking = false;
      });
      _syncOverlay();
    } catch (_) {
      if (!mounted || id != _req) return;
      setState(() => _thinking = false);
    }
  }

  List<AddressSuggestion> _options(String raw) {
    return mergeAddressSuggestions(
      raw: raw,
      entries: widget.entries,
      osmHits: _osmHits,
    );
  }

  void _pick(AddressSuggestion s) {
    final addr = s.address;
    _hideAfterPick = addr;
    widget.controller.value = TextEditingValue(
      text: addr,
      selection: TextSelection.collapsed(offset: addr.length),
    );
    _removeOverlay();
    widget.onPicked?.call(s);
    if (s.fromBook && widget.onPickedBookEntry != null) {
      for (final e in widget.entries) {
        if (e.address.trim().toLowerCase() == s.address.trim().toLowerCase()) {
          widget.onPickedBookEntry!(e);
          break;
        }
      }
    }
  }

  void _syncOverlay() {
    if (!mounted || !_focus.hasFocus) return;
    if (_hideAfterPick != null &&
        widget.controller.text == _hideAfterPick) {
      _removeOverlay();
      return;
    }
    final options = _options(widget.controller.text);
    if (options.isEmpty) {
      _removeOverlay();
      return;
    }
    if (_overlay == null) {
      _overlay = OverlayEntry(builder: _buildOverlay);
      Overlay.of(context, rootOverlay: true).insert(_overlay!);
    } else {
      _overlay!.markNeedsBuild();
    }
  }

  void _removeOverlay() {
    _overlay?.remove();
    _overlay = null;
  }

  void _tryAttachBusinessName() {
    final addr = widget.controller.text.trim();
    if (addr.isEmpty) return;
    final lower = addr.toLowerCase();
    for (final s in _options(addr)) {
      if (s.placeName.trim().isEmpty) continue;
      final hit = s.address.trim().toLowerCase();
      if (hit == lower ||
          lower.contains(hit.split('\n').first) ||
          hit.contains(lower.split('\n').first)) {
        widget.onPicked?.call(s);
        return;
      }
    }
  }

  Widget _buildOverlay(BuildContext overlayContext) {
    final options = _options(widget.controller.text);
    if (options.isEmpty) return const SizedBox.shrink();
    final rows = addressSuggestOverlayItems(options);
    final theme = Theme.of(context);
    final chromeHint = theme.hintColor;
    final box = _fieldKey.currentContext?.findRenderObject() as RenderBox?;
    final width = box?.size.width ?? 520;
    return Positioned(
      left: 0,
      top: 0,
      child: CompositedTransformFollower(
        link: _layerLink,
        showWhenUnlinked: false,
        targetAnchor: Alignment.bottomLeft,
        followerAnchor: Alignment.topLeft,
        child: SizedBox(
          width: width,
          child: Material(
            elevation: 4,
            borderRadius: BorderRadius.circular(8),
            clipBehavior: Clip.antiAlias,
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxHeight: 360),
              child: ListView.builder(
                      padding: EdgeInsets.zero,
                      shrinkWrap: true,
                      itemCount: rows.length,
                      itemBuilder: (context, i) {
                        final row = rows[i];
                        if (row.isHeader) {
                          return Padding(
                            padding: EdgeInsets.fromLTRB(
                              16,
                              i == 0 ? 8 : 10,
                              16,
                              4,
                            ),
                            child: Text(
                              row.header!,
                              textAlign: TextAlign.left,
                              style: TextStyle(
                                fontSize: 11,
                                fontWeight: FontWeight.w600,
                                letterSpacing: 0.35,
                                color: theme.colorScheme.primary,
                              ),
                            ),
                          );
                        }
                        final s = row.suggestion!;
                        final portrait = MediaQuery.orientationOf(context) ==
                            Orientation.portrait;
                        final android =
                            Theme.of(context).platform == TargetPlatform.android;
                        final addrSize = android && portrait ? 16.0 : 14.0;
                        return Column(
                          mainAxisSize: MainAxisSize.min,
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            InkWell(
                              onTap: () => _pick(s),
                              child: Padding(
                                padding: const EdgeInsets.fromLTRB(
                                  16,
                                  10,
                                  16,
                                  10,
                                ),
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                      s.address,
                                      maxLines: 4,
                                      softWrap: true,
                                      textAlign: TextAlign.left,
                                      overflow: TextOverflow.ellipsis,
                                      style: TextStyle(
                                        fontSize: addrSize,
                                        height: 1.35,
                                      ),
                                    ),
                                    const SizedBox(height: 3),
                                    Text(
                                      s.caption,
                                      maxLines: 2,
                                      softWrap: true,
                                      textAlign: TextAlign.left,
                                      overflow: TextOverflow.ellipsis,
                                      style: TextStyle(
                                        color: chromeHint,
                                        fontSize: addrSize - 1.5,
                                        height: 1.3,
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                            ),
                            if (i < rows.length - 1 && !rows[i + 1].isHeader)
                              const Divider(height: 1),
                          ],
                        );
                      },
                    ),
                  ),
                ),
              ),
            ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final portrait =
        MediaQuery.orientationOf(context) == Orientation.portrait;
    final android = Theme.of(context).platform == TargetPlatform.android;
    final addrSize = android && portrait ? 16.0 : 15.0;
    return CompositedTransformTarget(
      link: _layerLink,
      child: TextField(
        key: _fieldKey,
        controller: widget.controller,
        focusNode: _focus,
        minLines: portrait ? 3 : 2,
        maxLines: 4,
        style: TextStyle(fontSize: addrSize, height: 1.35),
        textCapitalization: TextCapitalization.characters,
        decoration: InputDecoration(
          labelText: widget.labelText,
          hintText:
              'Type a street or pick a suggestion — or enter manually',
          suffixIcon: _thinking
              ? const Padding(
                  padding: EdgeInsets.all(10),
                  child: SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                )
              : null,
        ),
      ),
    );
  }
}
