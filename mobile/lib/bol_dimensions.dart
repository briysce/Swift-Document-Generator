import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'theme.dart';

/// Length / width / height with a unit per axis, stored as one BOL line string.
class BolDimensionsValue {
  const BolDimensionsValue({
    this.length = '',
    this.width = '',
    this.height = '',
    this.lengthUnit = 'in',
    this.widthUnit = 'in',
    this.heightUnit = 'in',
  });

  final String length;
  final String width;
  final String height;
  final String lengthUnit;
  final String widthUnit;
  final String heightUnit;

  static const units = <String>['in', 'ft', 'cm', 'm', 'mm'];

  static const unitLabels = <String, String>{
    'in': 'Inches',
    'ft': 'Feet',
    'cm': 'Centimeters',
    'm': 'Meters',
    'mm': 'Millimeters',
  };

  bool get isEmpty =>
      length.trim().isEmpty && width.trim().isEmpty && height.trim().isEmpty;

  /// `6 in × 6 in × 21 ft` — empty when no measurements were entered.
  String format() {
    if (isEmpty) return '';
    final l = length.trim().isEmpty ? '—' : length.trim();
    final w = width.trim().isEmpty ? '—' : width.trim();
    final h = height.trim().isEmpty ? '—' : height.trim();
    return '$l ${normalizeUnit(lengthUnit)} × '
        '$w ${normalizeUnit(widthUnit)} × '
        '$h ${normalizeUnit(heightUnit)}';
  }

  static const _unitAlt =
      r'mm|cm|m|in|ft|inches?|feet|foot|meters?|'
      r'centimet(?:er|re)s?|millimet(?:er|re)s?';

  /// Legacy: `48 × 40 × 48 in` (one shared unit at the end).
  static final _legacyTriple = RegExp(
    r'^\s*([0-9]*\.?[0-9]+)\s*[x×]\s*([0-9]*\.?[0-9]+)\s*[x×]\s*([0-9]*\.?[0-9]+)'
    r'(?:\s*('
    '$_unitAlt'
    r'))\s*$',
    caseSensitive: false,
  );

  /// Per-axis: `6 in × 6 in × 21 ft` (unit optional on each number).
  static final _perAxisTriple = RegExp(
    r'^\s*([0-9]*\.?[0-9]+)\s*('
    '$_unitAlt'
    r')?\s*[x×]\s*'
    r'([0-9]*\.?[0-9]+)\s*('
    '$_unitAlt'
    r')?\s*[x×]\s*'
    r'([0-9]*\.?[0-9]+)\s*('
    '$_unitAlt'
    r')?\s*$',
    caseSensitive: false,
  );

  static String normalizeUnit(String raw) {
    switch (raw.trim().toLowerCase()) {
      case 'inch':
      case 'inches':
      case 'in':
        return 'in';
      case 'foot':
      case 'feet':
      case 'ft':
        return 'ft';
      case 'centimeter':
      case 'centimeters':
      case 'centimetre':
      case 'centimetres':
      case 'cm':
        return 'cm';
      case 'meter':
      case 'meters':
      case 'metre':
      case 'metres':
      case 'm':
        return 'm';
      case 'millimeter':
      case 'millimeters':
      case 'millimetre':
      case 'millimetres':
      case 'mm':
        return 'mm';
      default:
        return 'in';
    }
  }

  static BolDimensionsValue parse(String raw) {
    final s = raw.trim();
    if (s.isEmpty) return const BolDimensionsValue();

    final perAxis = _perAxisTriple.firstMatch(s);
    if (perAxis != null) {
      final sharedFallback = 'in';
      final lu = perAxis.group(2);
      final wu = perAxis.group(4);
      final hu = perAxis.group(6);
      // If only one unit appears overall, apply it to blank axes (legacy-ish).
      final anyUnit = lu ?? wu ?? hu;
      final fallback =
          anyUnit != null ? normalizeUnit(anyUnit) : sharedFallback;
      return BolDimensionsValue(
        length: perAxis.group(1)!,
        width: perAxis.group(3)!,
        height: perAxis.group(5)!,
        lengthUnit: lu != null ? normalizeUnit(lu) : fallback,
        widthUnit: wu != null ? normalizeUnit(wu) : fallback,
        heightUnit: hu != null ? normalizeUnit(hu) : fallback,
      );
    }

    final legacy = _legacyTriple.firstMatch(s);
    if (legacy != null) {
      final u = normalizeUnit(legacy.group(4) ?? 'in');
      return BolDimensionsValue(
        length: legacy.group(1)!,
        width: legacy.group(2)!,
        height: legacy.group(3)!,
        lengthUnit: u,
        widthUnit: u,
        heightUnit: u,
      );
    }

    // Bare `48x40x48` — inches on all axes.
    final bare = RegExp(
      r'^\s*([0-9]*\.?[0-9]+)\s*[x×]\s*([0-9]*\.?[0-9]+)\s*[x×]\s*([0-9]*\.?[0-9]+)\s*$',
    ).firstMatch(s);
    if (bare != null) {
      return BolDimensionsValue(
        length: bare.group(1)!,
        width: bare.group(2)!,
        height: bare.group(3)!,
      );
    }

    return BolDimensionsValue(length: s);
  }
}

/// L / W / H boxes each with their own unit, bound to the dimensions key.
class BolDimensionsFields extends StatefulWidget {
  const BolDimensionsFields({super.key, required this.controller, this.unitDefaults});

  final TextEditingController controller;

  /// (length, width, height) units to switch to whenever this changes —
  /// driven by the paired Item Type field via
  /// [BolItemTypes.defaultDimensionUnits]. Null leaves units exactly as the
  /// user set them (e.g. Crate/Other, or no item type chosen yet).
  final (String, String, String)? unitDefaults;

  @override
  State<BolDimensionsFields> createState() => _BolDimensionsFieldsState();
}

class _BolDimensionsFieldsState extends State<BolDimensionsFields> {
  late final TextEditingController _length;
  late final TextEditingController _width;
  late final TextEditingController _height;
  late String _lengthUnit;
  late String _widthUnit;
  late String _heightUnit;
  var _writing = false;

  @override
  void initState() {
    super.initState();
    final parsed = BolDimensionsValue.parse(widget.controller.text);
    _length = TextEditingController(text: parsed.length);
    _width = TextEditingController(text: parsed.width);
    _height = TextEditingController(text: parsed.height);
    // Prefer units already stored on the line (preset / prior edit). Only
    // apply item-type defaults when the dimensions field is still empty —
    // otherwise loading a Pallet line with `48 in × …` would silently rewrite
    // to ft on every mount.
    final hint = widget.unitDefaults;
    final useHint = parsed.isEmpty && hint != null;
    _lengthUnit = useHint ? hint.$1 : _safeUnit(parsed.lengthUnit);
    _widthUnit = useHint ? hint.$2 : _safeUnit(parsed.widthUnit);
    _heightUnit = useHint ? hint.$3 : _safeUnit(parsed.heightUnit);
    widget.controller.addListener(_onParent);
    _length.addListener(_push);
    _width.addListener(_push);
    _height.addListener(_push);
  }

  String _safeUnit(String u) =>
      BolDimensionsValue.units.contains(u) ? u : 'in';

  @override
  void didUpdateWidget(covariant BolDimensionsFields oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.controller != widget.controller) {
      oldWidget.controller.removeListener(_onParent);
      widget.controller.addListener(_onParent);
      _pullFromParent();
    }
    final hint = widget.unitDefaults;
    if (hint != null && hint != oldWidget.unitDefaults) {
      setState(() {
        _lengthUnit = hint.$1;
        _widthUnit = hint.$2;
        _heightUnit = hint.$3;
      });
      _push();
    }
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onParent);
    _length
      ..removeListener(_push)
      ..dispose();
    _width
      ..removeListener(_push)
      ..dispose();
    _height
      ..removeListener(_push)
      ..dispose();
    super.dispose();
  }

  void _onParent() {
    if (_writing) return;
    _pullFromParent();
  }

  void _pullFromParent() {
    final parsed = BolDimensionsValue.parse(widget.controller.text);
    void sync(TextEditingController c, String v) {
      if (c.text == v) return;
      c.value = TextEditingValue(
        text: v,
        selection: TextSelection.collapsed(offset: v.length),
      );
    }

    sync(_length, parsed.length);
    sync(_width, parsed.width);
    sync(_height, parsed.height);
    final lu = _safeUnit(parsed.lengthUnit);
    final wu = _safeUnit(parsed.widthUnit);
    final hu = _safeUnit(parsed.heightUnit);
    if ((lu != _lengthUnit || wu != _widthUnit || hu != _heightUnit) &&
        mounted) {
      setState(() {
        _lengthUnit = lu;
        _widthUnit = wu;
        _heightUnit = hu;
      });
    }
  }

  void _push() {
    final next = BolDimensionsValue(
      length: _length.text,
      width: _width.text,
      height: _height.text,
      lengthUnit: _lengthUnit,
      widthUnit: _widthUnit,
      heightUnit: _heightUnit,
    ).format();
    if (widget.controller.text == next) return;
    _writing = true;
    widget.controller.value = TextEditingValue(
      text: next,
      selection: TextSelection.collapsed(offset: next.length),
    );
    _writing = false;
  }

  InputDecoration _box(String label) {
    return InputDecoration(
      labelText: label,
      isDense: true,
      contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 10),
    );
  }

  /// TextField + unit dropdown pair for one axis. Callers decide how to lay
  /// multiple of these out (side by side vs. stacked) — this just needs a
  /// width, so it works either as a `Row` child wrapped in [Expanded] or
  /// directly inside a stretch-aligned `Column`.
  Widget _axis({
    required String label,
    required TextEditingController controller,
    required String unit,
    required ValueChanged<String> onUnit,
  }) {
    return Row(
      children: [
        Expanded(
          flex: 3,
          child: TextField(
            controller: controller,
            keyboardType: const TextInputType.numberWithOptions(
              decimal: true,
            ),
            inputFormatters: [
              FilteringTextInputFormatter.allow(RegExp(r'[0-9.]')),
            ],
            decoration: _box(label),
          ),
        ),
        const SizedBox(width: 4),
        Expanded(
          flex: 2,
          child: DropdownButtonFormField<String>(
            value: unit,
            isExpanded: true,
            decoration: const InputDecoration(
              labelText: 'UNIT',
              isDense: true,
              contentPadding: EdgeInsets.symmetric(
                horizontal: 8,
                vertical: 10,
              ),
            ),
            items: [
              for (final u in BolDimensionsValue.units)
                DropdownMenuItem(
                  value: u,
                  child: Text(u, overflow: TextOverflow.ellipsis),
                ),
            ],
            onChanged: (v) {
              if (v == null) return;
              onUnit(v);
            },
          ),
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            'DIMENSIONS',
            style: TextStyle(
              fontFamily: 'Oswald',
              fontSize: 11,
              fontWeight: FontWeight.w600,
              color: SwiftColors.muted,
              letterSpacing: 0.6,
            ),
          ),
          const SizedBox(height: 6),
          LayoutBuilder(
            builder: (context, constraints) {
              final axes = [
                _axis(
                  label: 'L',
                  controller: _length,
                  unit: _lengthUnit,
                  onUnit: (v) {
                    setState(() => _lengthUnit = v);
                    _push();
                  },
                ),
                _axis(
                  label: 'W',
                  controller: _width,
                  unit: _widthUnit,
                  onUnit: (v) {
                    setState(() => _widthUnit = v);
                    _push();
                  },
                ),
                _axis(
                  label: 'H',
                  controller: _height,
                  unit: _heightUnit,
                  onUnit: (v) {
                    setState(() => _heightUnit = v);
                    _push();
                  },
                ),
              ];
              // Three axes side by side need room for a number field + unit
              // dropdown each; below that they'd squeeze into unreadable
              // slivers, so stack them instead.
              if (constraints.maxWidth < 360) {
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    for (var i = 0; i < axes.length; i++) ...[
                      if (i > 0) const SizedBox(height: 8),
                      axes[i],
                    ],
                  ],
                );
              }
              return Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  for (var i = 0; i < axes.length; i++) ...[
                    if (i > 0) const SizedBox(width: 6),
                    Expanded(child: axes[i]),
                  ],
                ],
              );
            },
          ),
        ],
      ),
    );
  }
}
