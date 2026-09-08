import 'dart:math' as math;
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';

/// Signature capture for mouse, touch, and stylus (S Pen) without extra packages.
class SignaturePad extends StatefulWidget {
  const SignaturePad({super.key, this.height = 220, this.onChanged});

  final double height;
  final VoidCallback? onChanged;

  @override
  State<SignaturePad> createState() => SignaturePadState();
}

class SignaturePadState extends State<SignaturePad> {
  final GlobalKey _boardKey = GlobalKey();
  final List<List<Offset>> _strokes = [];
  List<Offset>? _current;
  int? _activePointer;
  int _revision = 0;

  bool get isEmpty =>
      _strokes.isEmpty && (_current == null || _current!.length < 2);

  void clear() {
    setState(() {
      _strokes.clear();
      _current = null;
      _activePointer = null;
      _revision++;
    });
    widget.onChanged?.call();
  }

  /// Exports strokes as PNG: uniform scale, cropped to ink bounds (+ padding).
  Future<Uint8List?> exportPng({int maxHeight = 220, double padding = 10}) async {
    if (isEmpty) return null;

    final box = _boardKey.currentContext?.findRenderObject() as RenderBox?;
    final padW = box?.size.width ?? 400.0;
    final padH = box?.size.height ?? widget.height;

    var minX = double.infinity;
    var minY = double.infinity;
    var maxX = double.negativeInfinity;
    var maxY = double.negativeInfinity;
    const strokePad = 2.0;
    for (final stroke in [..._strokes, ?_current]) {
      for (final p in stroke) {
        minX = math.min(minX, p.dx);
        minY = math.min(minY, p.dy);
        maxX = math.max(maxX, p.dx);
        maxY = math.max(maxY, p.dy);
      }
    }
    if (!minX.isFinite) return null;

    minX = (minX - strokePad - padding).clamp(0.0, padW);
    minY = (minY - strokePad - padding).clamp(0.0, padH);
    maxX = (maxX + strokePad + padding).clamp(0.0, padW);
    maxY = (maxY + strokePad + padding).clamp(0.0, padH);

    final cropW = (maxX - minX).clamp(1.0, padW);
    final cropH = (maxY - minY).clamp(1.0, padH);

    // Uniform scale into maxHeight; never independent sx/sy (that squashes strokes).
    final outH = maxHeight;
    final outW = (maxHeight * cropW / cropH).round().clamp(1, 4096);
    final scale = outH / cropH;
    Offset scalePt(Offset p) =>
        Offset((p.dx - minX) * scale, (p.dy - minY) * scale);

    final recorder = ui.PictureRecorder();
    final canvas = Canvas(recorder);
    canvas.drawRect(
      Rect.fromLTWH(0, 0, outW.toDouble(), outH.toDouble()),
      Paint()..color = const Color(0xFFFFFFFF),
    );
    final paint = Paint()
      ..color = const Color(0xFF1A1A1A)
      ..strokeWidth = 2.2 * scale
      ..strokeCap = StrokeCap.round
      ..style = PaintingStyle.stroke;
    for (final stroke in [..._strokes, ?_current]) {
      if (stroke.length < 2) continue;
      for (var i = 0; i < stroke.length - 1; i++) {
        canvas.drawLine(scalePt(stroke[i]), scalePt(stroke[i + 1]), paint);
      }
    }
    final picture = recorder.endRecording();
    final image = await picture.toImage(outW, outH);
    final data = await image.toByteData(format: ui.ImageByteFormat.png);
    return data?.buffer.asUint8List();
  }

  RenderBox? get _boardBox =>
      _boardKey.currentContext?.findRenderObject() as RenderBox?;

  Offset? _toBoardLocal(PointerEvent event) {
    final box = _boardBox;
    if (box == null || !box.hasSize) return null;
    return box.globalToLocal(event.position);
  }

  Offset _clampToBoard(Offset local) {
    final box = _boardBox!;
    return Offset(
      local.dx.clamp(0.0, box.size.width),
      local.dy.clamp(0.0, box.size.height),
    );
  }

  void _notifyChanged() => widget.onChanged?.call();

  void _start(PointerDownEvent event) {
    if (_activePointer != null) return;
    final local = _toBoardLocal(event);
    if (local == null) return;
    _activePointer = event.pointer;
    setState(() {
      _revision++;
      _current = [_clampToBoard(local)];
    });
    _notifyChanged();
  }

  void _move(PointerMoveEvent event) {
    if (event.pointer != _activePointer) return;
    final local = _toBoardLocal(event);
    if (local == null) return;
    setState(() {
      _revision++;
      _current = [...?_current, _clampToBoard(local)];
    });
    _notifyChanged();
  }

  void _end(int pointer) {
    if (pointer != _activePointer) return;
    setState(() {
      if (_current != null && _current!.length >= 2) {
        _strokes.add(List.of(_current!));
      }
      _current = null;
      _activePointer = null;
      _revision++;
    });
    _notifyChanged();
  }

  @override
  Widget build(BuildContext context) {
    final borderColor = Theme.of(context).dividerColor;

    return NotificationListener<ScrollNotification>(
      onNotification: (_) => true,
      child: RepaintBoundary(
        child: Container(
          key: _boardKey,
          width: double.infinity,
          height: widget.height,
          decoration: BoxDecoration(
            color: Colors.white,
            border: Border.all(color: borderColor),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Listener(
            behavior: HitTestBehavior.opaque,
            onPointerDown: _start,
            onPointerMove: _move,
            onPointerUp: (event) => _end(event.pointer),
            onPointerCancel: (event) => _end(event.pointer),
            child: CustomPaint(
              foregroundPainter: _SignaturePainter(
                strokes: _strokes,
                current: _current,
                revision: _revision,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _SignaturePainter extends CustomPainter {
  _SignaturePainter({
    required this.strokes,
    required this.current,
    required this.revision,
  });

  final List<List<Offset>> strokes;
  final List<Offset>? current;
  final int revision;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.save();
    canvas.clipRect(Offset.zero & size);

    final paint = Paint()
      ..color = const Color(0xFF1A1A1A)
      ..strokeWidth = 2.2
      ..strokeCap = StrokeCap.round
      ..style = PaintingStyle.stroke;
    for (final stroke in [...strokes, ?current]) {
      if (stroke.length < 2) continue;
      for (var i = 0; i < stroke.length - 1; i++) {
        canvas.drawLine(stroke[i], stroke[i + 1], paint);
      }
    }
    canvas.restore();
  }

  @override
  bool shouldRepaint(covariant _SignaturePainter old) =>
      old.revision != revision;
}
