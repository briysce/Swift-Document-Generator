import 'dart:typed_data';

import 'claude_client.dart';
import 'restore_critic.dart';
import 'gemini_client.dart';
import 'logo_image_process.dart';
import 'logo_vectorize.dart';

/// "Perfect this logo": Gemini redraws with an explicit no-stylization
/// prompt, Gemini and Claude independently critique the redraw against the
/// original and it retries on a fail, then the accepted raster is vectorized
/// (Windows only) so the final asset is edge-perfect at any zoom.
///
/// This is a deliberate, user-triggered action (see `logo_import_options.dart`
/// "Perfect this logo") — it costs several paid API calls and 30–60+ seconds,
/// so it must never run silently on every logo import.
class LogoPerfectRestore {
  LogoPerfectRestore._();

  /// Bumped by [cancelAll] so an in-flight [run] stops before its next
  /// attempt/API call instead of racing a cancelled UI state.
  static int epoch = 0;
  static void cancelAll() => epoch++;

  static const _prompt = '''
Digitally recreate and clean up this logo in ultra-high resolution with
crisp, razor-sharp vector-style edges on a solid pure white background.
Preserve the exact original colors, flat styling, and font typography
faithfully — do not add any 3D bevels, gloss, reflections, chrome textures,
drop shadows, or stylized enhancements.
''';

  static Future<LogoPerfectRestoreResult> run(
    Uint8List source, {
    bool removeBackground = false,
    int maxAttempts = 3,
    void Function(String)? onLog,
  }) async {
    if (!GeminiClient.isConfigured) {
      throw StateError(
        'Perfect this logo needs internet and a Gemini API key.',
      );
    }

    final gemini = GeminiClient();
    final claudeConfigured = ClaudeClient.isConfigured;
    final claude = claudeConfigured ? ClaudeClient() : null;
    if (claude == null) {
      onLog?.call(
        'perfect_restore: no Claude key configured — Gemini self-critique only',
      );
    }
    // Meedo-Me is a third, local opinion. It only votes when it actually
    // answers: an unreachable runtime must not block a redraw that the hosted
    // critics already agree on, and a missing verdict is never a pass.
    final meedo = RestoreCritic();

    Uint8List? best;
    var bestScoreSum = -1;
    var verified = false;
    var geminiCritiqued = false;
    var claudeCritiqued = false;
    var meedoCritiqued = false;
    var feedback = '';
    var attemptsUsed = 0;
    final startedEpoch = epoch;

    for (var attempt = 1; attempt <= maxAttempts; attempt++) {
      if (startedEpoch != epoch) {
        throw StateError('Cancelled.');
      }
      attemptsUsed = attempt;
      final prompt = feedback.isEmpty
          ? _prompt
          : '$_prompt\n\nYour previous attempt failed review for these '
              'reasons — fix them this time:\n$feedback';
      onLog?.call('perfect_restore: attempt $attempt/$maxAttempts');

      final Uint8List candidate;
      try {
        candidate = await gemini.restoreLogoPng(source, promptOverride: prompt);
      } catch (e) {
        onLog?.call('perfect_restore: Gemini generate failed ($e)');
        if (best != null) break;
        continue;
      }

      final geminiVerdict = await gemini.critiqueRestoreMatch(source, candidate);
      final claudeVerdict = await claude?.critiqueRestoreMatch(source, candidate);
      final meedoVerdict = await meedo.critique(source, candidate);
      if (geminiVerdict != null) geminiCritiqued = true;
      if (claudeVerdict != null) claudeCritiqued = true;
      if (meedoVerdict != null) meedoCritiqued = true;

      final geminiOk = geminiVerdict?.pass ?? false;
      // Claude is optional: when no key is configured, Gemini alone can verify.
      // When Claude is configured, both must pass.
      final claudeOk = !claudeConfigured || (claudeVerdict?.pass ?? false);
      // Meedo-Me only gets a vote when it answered. Adding a local critic must
      // not make verification harder than it was before it existed.
      final meedoOk = meedoVerdict == null || meedoVerdict.pass;
      final scoreSum = (geminiVerdict?.score ?? -1) +
          (claudeVerdict?.score ?? 0) +
          (meedoVerdict?.score ?? 0);
      onLog?.call(
        'perfect_restore: gemini=${geminiVerdict?.score}/${geminiVerdict?.pass} '
        'claude=${claudeConfigured ? '${claudeVerdict?.score}/${claudeVerdict?.pass}' : 'skipped'} '
        'meedo=${meedoVerdict != null ? '${meedoVerdict.score}/${meedoVerdict.pass}' : 'unavailable'}',
      );

      if (scoreSum > bestScoreSum) {
        best = candidate;
        bestScoreSum = scoreSum;
      }

      if (geminiOk && claudeOk && meedoOk) {
        best = candidate;
        verified = true;
        break;
      }

      feedback = [
        ...?geminiVerdict?.issues,
        ...?claudeVerdict?.issues,
        ...?meedoVerdict?.issues,
      ].where((e) => e.trim().isNotEmpty).join('; ');
      if (feedback.isEmpty) {
        feedback = 'Restored version drifted from the original — match it '
            'more exactly.';
      }
    }

    if (best == null) {
      throw StateError('Gemini could not produce a usable restoration.');
    }

    // Vectorize the accepted/best raster — Windows-only local engine. Falls
    // back to the raster candidate untouched (still a real improvement) when
    // unavailable or when vtracer's own fidelity gate rejects the trace.
    Uint8List finalPng = best;
    String? svg;
    try {
      final vec = await LogoVectorize.restoreWithSvg(best, onLog: onLog);
      if (vec != null && vec.png.isNotEmpty) {
        finalPng = vec.png;
        svg = vec.svg;
      }
    } catch (e) {
      onLog?.call('perfect_restore: vectorize step failed ($e)');
    }

    if (removeBackground) {
      final stripped = LogoImageProcessor.normalizeToVisibleContent(finalPng);
      if (stripped.isNotEmpty) finalPng = stripped;
    }

    return LogoPerfectRestoreResult(
      png: finalPng,
      svg: svg,
      verified: verified,
      attempts: attemptsUsed,
      geminiCritiqued: geminiCritiqued,
      claudeCritiqued: claudeCritiqued,
      meedoCritiqued: meedoCritiqued,
      claudeConfigured: claudeConfigured,
    );
  }
}

class LogoPerfectRestoreResult {
  const LogoPerfectRestoreResult({
    required this.png,
    required this.svg,
    required this.verified,
    required this.attempts,
    this.geminiCritiqued = false,
    this.claudeCritiqued = false,
    this.claudeConfigured = false,
    this.meedoCritiqued = false,
  });

  /// Final raster — vectorized when possible, the accepted Gemini redraw
  /// otherwise. This is what the document generator embeds.
  final Uint8List png;

  /// Vector markup to store alongside the logo, or null when vectorization
  /// wasn't available (non-Windows) or its own fidelity gate rejected the
  /// trace.
  final String? svg;

  /// True when configured critics passed the redraw before [attempts] ran
  /// out. Callers must not overwrite the user's original file when false.
  final bool verified;

  final int attempts;

  /// True if Gemini returned at least one critique response this run.
  final bool geminiCritiqued;

  /// True if Claude returned at least one critique response this run.
  final bool claudeCritiqued;

  /// Whether a Claude API key was available when the run started.
  final bool claudeConfigured;

  /// True if Meedo-Me, the local critic, returned at least one verdict this
  /// run. False simply means no runtime was reachable — it is not a failure,
  /// and the hosted critics carry the decision on their own.
  final bool meedoCritiqued;

  /// Short snack / UI label for which critics participated.
  String get criticsLabel {
    if (claudeCritiqued && geminiCritiqued && meedoCritiqued) {
      return 'Gemini + Claude + Meedo-Me';
    }
    if (claudeCritiqued && geminiCritiqued) return 'Gemini + Claude';
    if (geminiCritiqued && meedoCritiqued) return 'Gemini + Meedo-Me';
    if (geminiCritiqued && !claudeConfigured) return 'Gemini';
    if (geminiCritiqued) return 'Gemini (Claude unavailable)';
    return 'no critic response';
  }
}
