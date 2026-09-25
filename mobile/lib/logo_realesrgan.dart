import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:path/path.dart' as p;

/// Runs repo/bundled [logo_restorer.py] for structure-aware SR.
///
/// Pipeline inside the Python engine (fail-open at every step):
/// classical Gigapixel / Upscayl / Remacri / UltraSharp-inspired polish →
/// Real-ESRGAN family weights (x4plus, Remacri, UltraSharp, … when cached) →
/// optional GFPGAN / Upscayl CLI → Lanczos conservator.
///
/// Capability limit (be upfront): this invents missing edge/fill detail from
/// degradation patterns. It is **not** a free-form brand redesign. Windows
/// only (needs local Python + torch/realesrgan). Android stays on Dart cubic
/// (+ optional Gemini) — do not claim Real-ESRGAN there.
class LogoRealEsrgan {
  LogoRealEsrgan._();

  static const _timeout = Duration(minutes: 4);

  /// Returns restored PNG bytes, or null when unavailable / failed.
  static Future<Uint8List?> restore(
    Uint8List sourceBytes, {
    int minHeight = 3000,
    void Function(String)? onLog,
  }) async {
    if (!Platform.isWindows) {
      onLog?.call('realesrgan: skipped (Windows-only local engine)');
      return null;
    }
    if (sourceBytes.isEmpty) return null;

    final script = await _findScript();
    if (script == null) {
      onLog?.call('realesrgan: logo_restorer.py not found next to exe/repo');
      return null;
    }

    final py = await _findPython();
    if (py == null) {
      onLog?.call('realesrgan: Python not found (py/python)');
      return null;
    }

    final tmp = await Directory.systemTemp.createTemp('swift_logo_sr_');
    try {
      final input = File(p.join(tmp.path, 'in.png'));
      final output = File(p.join(tmp.path, 'out.png'));
      await input.writeAsBytes(sourceBytes, flush: true);

      onLog?.call('realesrgan: ${py.exe} ${script.path}');
      final proc = await Process.run(
        py.exe,
        [
          ...py.prefixArgs,
          script.path,
          input.path,
          output.path,
          '--min-dimension',
          '$minHeight',
        ],
        workingDirectory: script.parent.path,
        runInShell: false,
      ).timeout(_timeout);

      if (proc.exitCode != 0) {
        final err = '${proc.stderr}'.trim();
        onLog?.call(
          'realesrgan: exit ${proc.exitCode}'
          '${err.isEmpty ? '' : ' — ${err.split('\n').last}'}',
        );
        return null;
      }
      if (!await output.exists()) {
        onLog?.call('realesrgan: no output file');
        return null;
      }
      final out = await output.readAsBytes();
      if (out.isEmpty) {
        onLog?.call('realesrgan: empty output');
        return null;
      }
      onLog?.call('realesrgan: ok (${out.length} bytes)');
      return out;
    } on TimeoutException {
      onLog?.call('realesrgan: timed out');
      return null;
    } catch (e) {
      onLog?.call('realesrgan: $e');
      return null;
    } finally {
      try {
        await tmp.delete(recursive: true);
      } catch (_) {}
    }
  }

  static Future<File?> _findScript() async {
    final candidates = <String>[];
    try {
      candidates.add(
        p.join(p.dirname(Platform.resolvedExecutable), 'logo_restorer.py'),
      );
    } catch (_) {}
    // Dev / repo checkout: mobile/lib → ../../logo_restorer.py
    candidates.add(
      p.normalize(
        p.join(
          p.dirname(Platform.script.toFilePath()),
          '..',
          '..',
          '..',
          'logo_restorer.py',
        ),
      ),
    );
    // Relative to cwd when launched from repo root or dist folder.
    candidates.add(p.join(Directory.current.path, 'logo_restorer.py'));
    candidates.add(
      p.join(Directory.current.path, '..', 'logo_restorer.py'),
    );

    for (final path in candidates) {
      final f = File(path);
      if (await f.exists()) return f;
    }
    return null;
  }

  static Future<_PyCmd?> _findPython() async {
    for (final attempt in [
      (exe: 'py', prefix: <String>['-3']),
      (exe: 'python', prefix: <String>[]),
      (exe: 'python3', prefix: <String>[]),
    ]) {
      try {
        final check = await Process.run(
          attempt.exe,
          [...attempt.prefix, '-c', 'import sys; print(sys.version)'],
          runInShell: false,
        ).timeout(const Duration(seconds: 8));
        if (check.exitCode == 0) {
          return _PyCmd(attempt.exe, attempt.prefix);
        }
      } catch (_) {}
    }
    return null;
  }
}

class _PyCmd {
  const _PyCmd(this.exe, this.prefixArgs);
  final String exe;
  final List<String> prefixArgs;
}
