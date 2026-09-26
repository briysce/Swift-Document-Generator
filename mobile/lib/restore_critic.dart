import 'dart:typed_data';

import 'meedo_me_client.dart';

/// Independent fidelity check on a restored logo, run on Meedo-Me.
///
/// A redraw is only allowed through if a model, looking at the original and the
/// candidate side by side, agrees they are the same mark. The rubric is carried
/// over unchanged from the hosted critics it replaces: this is a fidelity test,
/// not a taste test, and a prettier logo that changed the design is a failure.
///
/// Why two opinions
/// ----------------
/// The previous design asked two different vendors to critique the same redraw,
/// on the reasoning that one model's blind spot should not quietly pass drifted
/// artwork. That property is worth keeping, so a second critic model can be
/// named via `MEEDO_ME_CRITIC_MODEL`; when it is, both must pass.
///
/// When it is not set there is only one opinion, and [CriticPanel.independent]
/// reports false so callers can say so rather than implying a cross-check that
/// did not happen. A single local critic is still a real check — it is just not
/// two.
///
/// Null is never a pass. A model that could not be reached, or whose reply did
/// not parse, means "not verified".
class RestoreCritic {
  RestoreCritic({MeedoMeClient? client}) : _client = client ?? MeedoMeClient();

  final MeedoMeClient _client;

  static const String _prompt = '''
Compare IMAGE_A (the original source) and IMAGE_B (a cleaned-up candidate
meant to be the same logo, just higher resolution and crisper). You are
checking fidelity, not aesthetics — a beautiful redraw that changed the
design is a FAIL.

Score 0-100 on how closely IMAGE_B preserves IMAGE_A's exact: letterforms and
their proportions, icon shape and geometry, layout/spacing between elements,
and colors (hue, not just "similar family"). Deduct heavily for anything
IMAGE_B added that is not in IMAGE_A (extra shading, bevels, glow, drop
shadows, chrome/gloss, changed background) or removed/altered from IMAGE_A.

Respond with strict JSON only, no other text:
{"score": <0-100 integer>, "pass": <bool, true only if score >= 90>,
 "issues": [<short strings, empty array if none>]}
''';

  /// Name of the optional second critic model, or empty when unset.
  static String get secondOpinionModel =>
      MeedoMeClient.envValue('MEEDO_ME_CRITIC_MODEL');

  static bool get hasSecondOpinion => secondOpinionModel.isNotEmpty;

  /// One critic's verdict. Null on any failure — never a pass.
  Future<MatchVerdict?> critique(
    Uint8List source,
    Uint8List candidate, {
    String? model,
  }) async {
    final parsed = await _client.completeJson(
      prompt: 'IMAGE_A (source) then IMAGE_B (candidate) above.\n$_prompt',
      images: [source, candidate],
      maxTokens: 512,
      timeout: const Duration(seconds: 120),
    );
    if (parsed == null) return null;

    final score = parsed['score'];
    final pass = parsed['pass'];
    final issuesRaw = parsed['issues'];
    return MatchVerdict(
      score: score is num ? score.toInt() : -1,
      pass: pass == true,
      issues: issuesRaw is List
          ? issuesRaw.map((e) => '$e').where((e) => e.isNotEmpty).toList()
          : const [],
    );
  }

  /// Run every configured critic. All present verdicts must pass.
  Future<CriticPanel> critiquePanel(
    Uint8List source,
    Uint8List candidate,
  ) async {
    final primary = await critique(source, candidate);
    MatchVerdict? second;
    if (hasSecondOpinion) {
      second = await critique(source, candidate, model: secondOpinionModel);
    }
    return CriticPanel(primary: primary, second: second);
  }

  void close() => _client.close();
}

/// One critic's read of a redraw.
class MatchVerdict {
  const MatchVerdict({
    required this.score,
    required this.pass,
    this.issues = const [],
  });

  /// 0-100, or -1 when the model did not return a usable number.
  final int score;
  final bool pass;
  final List<String> issues;
}

/// The combined result of every critic that ran.
class CriticPanel {
  const CriticPanel({this.primary, this.second});

  final MatchVerdict? primary;
  final MatchVerdict? second;

  /// True only when at least one critic ran and every critic that ran passed.
  ///
  /// A missing verdict is "not verified", so it cannot carry the decision.
  bool get pass {
    if (primary == null) return false;
    if (!primary!.pass) return false;
    if (second != null && !second!.pass) return false;
    return true;
  }

  /// Whether two different models actually weighed in.
  bool get independent => primary != null && second != null;

  int get bestScore {
    final a = primary?.score ?? -1;
    final b = second?.score ?? -1;
    return a > b ? a : b;
  }

  List<String> get issues => [
        ...?primary?.issues,
        ...?second?.issues,
      ];

  /// Short description for logs and the restore report.
  String get describe {
    if (primary == null) return 'not verified';
    if (independent) return 'Meedo-Me x2 (independent critics)';
    return 'Meedo-Me (single critic)';
  }
}
