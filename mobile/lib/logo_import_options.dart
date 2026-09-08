import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image/image.dart' as img;

import 'app_snack.dart';
import 'logo_crop_geometry.dart';
import 'theme.dart';

/// How to crop a logo before import.
enum LogoCropMode {
  auto,
  manual,
  none;

  String get label => switch (this) {
        LogoCropMode.auto => 'Auto-crop',
        LogoCropMode.manual => 'Manual crop',
        LogoCropMode.none => 'No crop',
      };
}

/// Post-pick editing choices for logo import.
class LogoImportOptions {
  const LogoImportOptions({
    required this.removeBackground,
    required this.cropMode,
    this.manualCropRect,
    this.perfectLogo = false,
  });

  factory LogoImportOptions.standard({
    bool removeBackground = true,
    LogoCropMode cropMode = LogoCropMode.auto,
    Rect? manualCropRect,
    bool perfectLogo = false,
  }) =>
      LogoImportOptions(
        removeBackground: removeBackground,
        cropMode: cropMode,
        manualCropRect: manualCropRect,
        perfectLogo: perfectLogo,
      );

  final bool removeBackground;
  final LogoCropMode cropMode;
  /// Runs the Gemini + Claude verified redraw-and-vectorize pipeline
  /// ([LogoPerfectRestore]) after import. Deliberate, paid, slow — never a
  /// silent default.
  final bool perfectLogo;

  /// Normalized crop rect (0–1) relative to image bounds; used when
  /// [cropMode] is [LogoCropMode.manual].
  final Rect? manualCropRect;
}

/// Post-pick logo edit prompt — crop + optional background removal.
  Future<LogoImportOptions?> showLogoImportEditDialog(
  BuildContext context, {
  required Uint8List previewBytes,
  bool initialPerfectLogo = false,
}) {
  return showDialog<LogoImportOptions>(
    context: context,
    builder: (ctx) => _LogoImportEditDialog(
      previewBytes: previewBytes,
      initialPerfectLogo: initialPerfectLogo,
    ),
  );
}

class _LogoImportEditDialog extends StatefulWidget {
  const _LogoImportEditDialog({
    required this.previewBytes,
    this.initialPerfectLogo = false,
  });

  final Uint8List previewBytes;
  final bool initialPerfectLogo;

  @override
  State<_LogoImportEditDialog> createState() => _LogoImportEditDialogState();
}

class _LogoImportEditDialogState extends State<_LogoImportEditDialog> {
  var _removeBg = true;
  var _cropMode = LogoCropMode.auto;
  late bool _perfectLogo;
  Rect? _manualCrop;

  @override
  void initState() {
    super.initState();
    _perfectLogo = widget.initialPerfectLogo;
  }

  @override
  Widget build(BuildContext context) {
    final maxW = MediaQuery.sizeOf(context).width;
    return AlertDialog(
      title: Row(
        children: [
          const Expanded(child: Text('Edit logo')),
          IconButton(
            tooltip: 'Close',
            onPressed: () => Navigator.pop(context),
            icon: const Icon(Icons.close),
          ),
        ],
      ),
      content: SizedBox(
        width: maxW < 520 ? maxW - 48 : 480,
        child: Scrollbar(
          thumbVisibility: true,
          interactive: true,
          child: SingleChildScrollView(
            primary: true,
            child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: ColoredBox(
                  color: const Color(0xFFE8E8E8),
                  child: Padding(
                    padding: const EdgeInsets.all(8),
                    child: _cropMode == LogoCropMode.manual
                        ? _ManualCropEditor(
                            bytes: widget.previewBytes,
                            initialRect: _manualCrop,
                            onChanged: (r) => setState(() => _manualCrop = r),
                          )
                        : Image.memory(
                            widget.previewBytes,
                            height: 120,
                            fit: BoxFit.contain,
                          ),
                  ),
                ),
              ),
              const SizedBox(height: 14),
              Text(
                'CROP',
                style: const TextStyle(
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
                  final narrow = constraints.maxWidth < 360;
                  if (narrow) {
                    // Portrait phone dialogs: SegmentedButton wraps letter-by-letter.
                    return Column(
                      children: [
                        for (final mode in LogoCropMode.values)
                          Padding(
                            padding: const EdgeInsets.only(bottom: 6),
                            child: Material(
                              color: _cropMode == mode
                                  ? SwiftColors.accentSoft
                                  : SwiftColors.panel,
                              borderRadius: BorderRadius.circular(8),
                              child: InkWell(
                                borderRadius: BorderRadius.circular(8),
                                onTap: () {
                                  setState(() {
                                    _cropMode = mode;
                                    if (mode == LogoCropMode.none) {
                                      _removeBg = false;
                                    }
                                  });
                                },
                                child: Container(
                                  width: double.infinity,
                                  padding: const EdgeInsets.symmetric(
                                    horizontal: 12,
                                    vertical: 12,
                                  ),
                                  decoration: BoxDecoration(
                                    borderRadius: BorderRadius.circular(8),
                                    border: Border.all(
                                      color: _cropMode == mode
                                          ? SwiftColors.accent
                                          : SwiftColors.border,
                                    ),
                                  ),
                                  child: Row(
                                    children: [
                                      Icon(
                                        _cropMode == mode
                                            ? Icons.check_circle
                                            : Icons.circle_outlined,
                                        size: 18,
                                        color: _cropMode == mode
                                            ? SwiftColors.accent
                                            : SwiftColors.muted,
                                      ),
                                      const SizedBox(width: 10),
                                      Text(
                                        mode.label,
                                        style: TextStyle(
                                          fontWeight: _cropMode == mode
                                              ? FontWeight.w600
                                              : FontWeight.w500,
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                              ),
                            ),
                          ),
                      ],
                    );
                  }
                  return SegmentedButton<LogoCropMode>(
                    showSelectedIcon: false,
                    segments: [
                      for (final mode in LogoCropMode.values)
                        ButtonSegment(
                          value: mode,
                          label: Text(
                            mode.label,
                            softWrap: false,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                    ],
                    selected: {_cropMode},
                    onSelectionChanged: (s) {
                      final mode = s.first;
                      setState(() {
                        _cropMode = mode;
                        if (mode == LogoCropMode.none) {
                          _removeBg = false;
                        }
                      });
                    },
                  );
                },
              ),
              if (_cropMode == LogoCropMode.none)
                const Padding(
                  padding: EdgeInsets.only(top: 8),
                  child: Text(
                    'Imports the image without cropping or background removal.',
                    style: TextStyle(fontSize: 12, color: SwiftColors.muted),
                  ),
                ),
              if (_cropMode != LogoCropMode.none) ...[
                const SizedBox(height: 14),
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  value: _removeBg,
                  onChanged: (v) => setState(() => _removeBg = v ?? false),
                  title: const Text('Remove background'),
                  subtitle: const Text(
                    'Strip a solid outer background to transparent',
                    style: TextStyle(fontSize: 12, color: SwiftColors.muted),
                  ),
                  controlAffinity: ListTileControlAffinity.leading,
                ),
              ],
              const SizedBox(height: 6),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                value: _perfectLogo,
                onChanged: (v) => setState(() => _perfectLogo = v ?? false),
                title: const Text('Perfect this logo'),
                subtitle: const Text(
                  'Gemini redraws it crisp on a clean white background, Gemini '
                  'and Claude independently check the redraw against the '
                  'original and it retries if either rejects it, then it\'s '
                  'vectorized (Windows) for a print-ready file. Several paid '
                  'API calls, 30–60+ seconds — use once per customer logo, '
                  'not on every import.',
                  style: TextStyle(fontSize: 12, color: SwiftColors.muted),
                ),
                controlAffinity: ListTileControlAffinity.leading,
              ),
            ],
            ),
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () {
            if (_cropMode == LogoCropMode.manual &&
                (_manualCrop == null || _manualCrop!.width < 0.05)) {
              showAppSnack(context, 'Drag the crop box or choose Auto-crop.');
              return;
            }
            Navigator.pop(
              context,
              LogoImportOptions.standard(
                removeBackground:
                    _cropMode == LogoCropMode.none ? false : _removeBg,
                cropMode: _cropMode,
                manualCropRect: _manualCrop,
                perfectLogo: _perfectLogo,
              ),
            );
          },
          child: const Text('Import'),
        ),
      ],
    );
  }
}

enum _CropDragMode {
  move,
  left,
  right,
  top,
  bottom,
  topLeft,
  topRight,
  bottomLeft,
  bottomRight,
}

/// Interactive normalized crop rectangle over [bytes].
class _ManualCropEditor extends StatefulWidget {
  const _ManualCropEditor({
    required this.bytes,
    required this.onChanged,
    this.initialRect,
  });

  final Uint8List bytes;
  final Rect? initialRect;
  final ValueChanged<Rect> onChanged;

  @override
  State<_ManualCropEditor> createState() => _ManualCropEditorState();
}

class _ManualCropEditorState extends State<_ManualCropEditor> {
  static const _minCrop = 0.05;
  static const _handleRadius = 8.0;
  static const _edgeHit = 12.0;

  late Size _imageSize;
  late Rect _rect;
  _CropDragMode? _dragMode;
  Rect? _dragStartRect;
  Offset? _panStartNormalized;

  @override
  void initState() {
    super.initState();
    final decoded = img.decodeImage(widget.bytes);
    _imageSize = decoded != null
        ? Size(decoded.width.toDouble(), decoded.height.toDouble())
        : const Size(1, 1);
    _rect = LogoCropGeometry.clampNormalized(
      widget.initialRect ?? const Rect.fromLTWH(0.05, 0.05, 0.9, 0.9),
      minSize: _minCrop,
    );
    WidgetsBinding.instance.addPostFrameCallback((_) => widget.onChanged(_rect));
  }

  void _update(Rect r) {
    final next = LogoCropGeometry.clampNormalized(r, minSize: _minCrop);
    setState(() => _rect = next);
    widget.onChanged(next);
  }

  _CropDragMode? _hitTest(Offset local, Size containerSize) {
    final displayRect =
        LogoCropGeometry.normalizedToDisplay(_rect, containerSize, _imageSize);
    final corners = <(_CropDragMode, Offset)>[
      (_CropDragMode.topLeft, displayRect.topLeft),
      (_CropDragMode.topRight, displayRect.topRight),
      (_CropDragMode.bottomLeft, displayRect.bottomLeft),
      (_CropDragMode.bottomRight, displayRect.bottomRight),
    ];
    for (final (mode, point) in corners) {
      if ((local - point).distance <= _handleRadius + 4) return mode;
    }

    if ((local.dx - displayRect.left).abs() <= _edgeHit &&
        local.dy >= displayRect.top - _edgeHit &&
        local.dy <= displayRect.bottom + _edgeHit) {
      return _CropDragMode.left;
    }
    if ((local.dx - displayRect.right).abs() <= _edgeHit &&
        local.dy >= displayRect.top - _edgeHit &&
        local.dy <= displayRect.bottom + _edgeHit) {
      return _CropDragMode.right;
    }
    if ((local.dy - displayRect.top).abs() <= _edgeHit &&
        local.dx >= displayRect.left - _edgeHit &&
        local.dx <= displayRect.right + _edgeHit) {
      return _CropDragMode.top;
    }
    if ((local.dy - displayRect.bottom).abs() <= _edgeHit &&
        local.dx >= displayRect.left - _edgeHit &&
        local.dx <= displayRect.right + _edgeHit) {
      return _CropDragMode.bottom;
    }
    if (displayRect.contains(local)) return _CropDragMode.move;
    return null;
  }

  Rect _applyDrag(Rect start, Offset normalizedDelta, _CropDragMode mode) {
    var left = start.left;
    var top = start.top;
    var right = start.right;
    var bottom = start.bottom;

    switch (mode) {
      case _CropDragMode.move:
        left += normalizedDelta.dx;
        top += normalizedDelta.dy;
        right += normalizedDelta.dx;
        bottom += normalizedDelta.dy;
      case _CropDragMode.left:
        left += normalizedDelta.dx;
      case _CropDragMode.right:
        right += normalizedDelta.dx;
      case _CropDragMode.top:
        top += normalizedDelta.dy;
      case _CropDragMode.bottom:
        bottom += normalizedDelta.dy;
      case _CropDragMode.topLeft:
        left += normalizedDelta.dx;
        top += normalizedDelta.dy;
      case _CropDragMode.topRight:
        right += normalizedDelta.dx;
        top += normalizedDelta.dy;
      case _CropDragMode.bottomLeft:
        left += normalizedDelta.dx;
        bottom += normalizedDelta.dy;
      case _CropDragMode.bottomRight:
        right += normalizedDelta.dx;
        bottom += normalizedDelta.dy;
    }

    if (right - left < _minCrop) {
      if (mode == _CropDragMode.left ||
          mode == _CropDragMode.topLeft ||
          mode == _CropDragMode.bottomLeft) {
        left = right - _minCrop;
      } else {
        right = left + _minCrop;
      }
    }
    if (bottom - top < _minCrop) {
      if (mode == _CropDragMode.top ||
          mode == _CropDragMode.topLeft ||
          mode == _CropDragMode.topRight) {
        top = bottom - _minCrop;
      } else {
        bottom = top + _minCrop;
      }
    }

    return Rect.fromLTRB(left, top, right, bottom);
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        const maxH = 220.0;
        return SizedBox(
          height: maxH,
          child: GestureDetector(
            behavior: HitTestBehavior.opaque,
            onPanStart: (d) {
              final box = context.findRenderObject() as RenderBox?;
              if (box == null) return;
              final mode = _hitTest(d.localPosition, box.size);
              if (mode == null) return;
              _dragMode = mode;
              _dragStartRect = _rect;
              _panStartNormalized = LogoCropGeometry.displayToNormalized(
                d.localPosition,
                box.size,
                _imageSize,
              );
            },
            onPanUpdate: (d) {
              final box = context.findRenderObject() as RenderBox?;
              if (box == null ||
                  _dragMode == null ||
                  _dragStartRect == null ||
                  _panStartNormalized == null) {
                return;
              }
              final current = LogoCropGeometry.displayToNormalized(
                d.localPosition,
                box.size,
                _imageSize,
              );
              final totalDelta = current - _panStartNormalized!;
              _update(_applyDrag(_dragStartRect!, totalDelta, _dragMode!));
            },
            onPanEnd: (_) {
              _dragMode = null;
              _dragStartRect = null;
              _panStartNormalized = null;
            },
            onPanCancel: () {
              _dragMode = null;
              _dragStartRect = null;
              _panStartNormalized = null;
            },
            child: Stack(
              fit: StackFit.expand,
              children: [
                Image.memory(widget.bytes, fit: BoxFit.contain),
                CustomPaint(
                  painter: _CropOverlayPainter(
                    normalizedRect: _rect,
                    imageSize: _imageSize,
                  ),
                ),
                Positioned(
                  right: 4,
                  bottom: 4,
                  child: IconButton.filledTonal(
                    tooltip: 'Reset crop',
                    onPressed: () =>
                        _update(const Rect.fromLTWH(0.05, 0.05, 0.9, 0.9)),
                    icon: const Icon(Icons.crop_free, size: 18),
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _CropOverlayPainter extends CustomPainter {
  _CropOverlayPainter({
    required this.normalizedRect,
    required this.imageSize,
  });

  final Rect normalizedRect;
  final Size imageSize;

  @override
  void paint(Canvas canvas, Size size) {
    final imageRect = LogoCropGeometry.containImageRect(size, imageSize);
    final cropRect = LogoCropGeometry.normalizedToDisplay(
      normalizedRect,
      size,
      imageSize,
    );
    final shade = Paint()..color = const Color(0xAA000000);

    // Dim letterbox margins outside the image.
    canvas.drawPath(
      Path.combine(
        PathOperation.difference,
        Path()..addRect(Offset.zero & size),
        Path()..addRect(imageRect),
      ),
      shade,
    );

    // Dim area outside crop window but inside the image.
    canvas.save();
    canvas.clipRect(imageRect);
    canvas.drawPath(
      Path.combine(
        PathOperation.difference,
        Path()..addRect(imageRect),
        Path()..addRect(cropRect),
      ),
      shade,
    );
    canvas.restore();

    final border = Paint()
      ..color = SwiftColors.accent
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2;
    canvas.drawRect(cropRect, border);

    // Rule-of-thirds guides inside crop window.
    final guide = Paint()
      ..color = Colors.white.withValues(alpha: 0.35)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1;
    for (var i = 1; i <= 2; i++) {
      final x = cropRect.left + cropRect.width * i / 3;
      final y = cropRect.top + cropRect.height * i / 3;
      canvas.drawLine(Offset(x, cropRect.top), Offset(x, cropRect.bottom), guide);
      canvas.drawLine(Offset(cropRect.left, y), Offset(cropRect.right, y), guide);
    }

    final handleFill = Paint()..color = SwiftColors.accent;
    final handleStroke = Paint()
      ..color = Colors.white
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.5;
    for (final point in [
      cropRect.topLeft,
      cropRect.topRight,
      cropRect.bottomLeft,
      cropRect.bottomRight,
    ]) {
      canvas.drawCircle(point, 7, handleFill);
      canvas.drawCircle(point, 7, handleStroke);
    }
  }

  @override
  bool shouldRepaint(covariant _CropOverlayPainter oldDelegate) =>
      oldDelegate.normalizedRect != normalizedRect ||
      oldDelegate.imageSize != imageSize;
}
