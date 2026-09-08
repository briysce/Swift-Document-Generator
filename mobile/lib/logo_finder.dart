import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import 'app_config.dart';
import 'gemini_client.dart';

/// Which web source(s) to query when finding a customer logo.
enum LogoSearchEngine {
  all,
  serper,
  /// Deprecated alias for [serper] (saved settings / old tests).
  google,
  /// Deprecated alias for [serper] (saved settings / old tests).
  bing,
  clearbit,
  brandsOfTheWorld,
  logoDev,
  brandfetch,
  tineye,
  wikipediaDuckDuckGo,
  retoolClearbit;

  static const defaultEngine = LogoSearchEngine.all;

  String get id => name;

  String get label => switch (this) {
        LogoSearchEngine.all => 'All sources',
        LogoSearchEngine.serper => 'Web search (Serper)',
        LogoSearchEngine.google => 'Web search (Serper)',
        LogoSearchEngine.bing => 'Web search (Serper)',
        LogoSearchEngine.clearbit => 'Clearbit',
        LogoSearchEngine.brandsOfTheWorld => 'Brands of the World',
        LogoSearchEngine.logoDev => 'Logo.dev',
        LogoSearchEngine.brandfetch => 'Brandfetch',
        LogoSearchEngine.tineye => 'TinEye',
        LogoSearchEngine.wikipediaDuckDuckGo => 'Wikipedia / DuckDuckGo',
        LogoSearchEngine.retoolClearbit => 'Clearbit via Retool',
      };

  static LogoSearchEngine? tryParse(String? raw) {
    if (raw == null || raw.isEmpty) return null;
    if (raw == 'google' || raw == 'bing') return LogoSearchEngine.serper;
    for (final e in LogoSearchEngine.values) {
      if (e.id == raw) return e;
    }
    return null;
  }

  /// UI order: All + Serper + domain APIs; Retool last when configured.
  static List<LogoSearchEngine> pickerOptions({required bool retoolConfigured}) {
    final out = <LogoSearchEngine>[
      LogoSearchEngine.all,
      LogoSearchEngine.serper,
      LogoSearchEngine.clearbit,
      LogoSearchEngine.brandfetch,
      LogoSearchEngine.logoDev,
    ];
    if (retoolConfigured) out.add(LogoSearchEngine.retoolClearbit);
    return out;
  }

  static bool retoolClearbitConfigured() =>
      LogoFinder._retoolClearbitUrlTemplate().isNotEmpty;
}

/// A logo image URL discovered from a web source, ranked for download.
class LogoCandidate {
  const LogoCandidate({
    required this.url,
    required this.source,
    required this.score,
  });

  final String url;
  final String source;
  final int score;
}

/// A successfully downloaded logo candidate ready for the picker UI.
class LogoDownloadedCandidate {
  const LogoDownloadedCandidate({
    required this.bytes,
    required this.source,
    required this.url,
    required this.score,
    this.hint = '',
  });

  final Uint8List bytes;
  final String source;
  final String url;
  final int score;
  final String hint;
}

/// Result of attempting to download a customer logo from the web.
class LogoFindResult {
  const LogoFindResult.ok(this.bytes, {required this.source, this.hint = ''})
      : error = null;

  const LogoFindResult.fail(this.error)
      : bytes = null,
        source = '',
        hint = '';

  final Uint8List? bytes;
  final String source;
  final String hint;
  final String? error;

  bool get ok => bytes != null && bytes!.isNotEmpty && error == null;
}

class _RelevanceContext {
  const _RelevanceContext({
    required this.companyName,
    required this.domains,
    required this.tokens,
    this.extraQueries = const [],
    this.alternateNames = const [],
  });

  final String companyName;
  final List<String> domains;
  final List<String> tokens;
  /// Gemini (or other) extra image-search queries.
  final List<String> extraQueries;
  /// Alternate company names suggested by Gemini.
  final List<String> alternateNames;

  factory _RelevanceContext.from(
    String companyName,
    List<String> domains, {
    List<String> extraQueries = const [],
    List<String> alternateNames = const [],
  }) {
    return _RelevanceContext(
      companyName: companyName.trim(),
      domains: domains,
      tokens: LogoFinder._companyTokens(companyName),
      extraQueries: extraQueries,
      alternateNames: alternateNames,
    );
  }

  _RelevanceContext withExtras({
    List<String>? extraQueries,
    List<String>? alternateNames,
    List<String>? domains,
  }) {
    return _RelevanceContext(
      companyName: companyName,
      domains: domains ?? this.domains,
      tokens: tokens,
      extraQueries: extraQueries ?? this.extraQueries,
      alternateNames: alternateNames ?? this.alternateNames,
    );
  }
}

/// Multi-source logo lookup: Clearbit / Brandfetch domain APIs plus Serper.dev
/// Google Images search (no HTML scraping).
class LogoFinder {
  LogoFinder({http.Client? client}) : _client = client ?? http.Client();

  final http.Client _client;
  static const maxBytes = 5 * 1024 * 1024;
  /// Max thumbnails shown in the post-search logo picker grid.
  static const pickerMaxResults = 30;
  /// Try enough ranked URLs that we can still fill [pickerMaxResults] after
  /// download/filter losses (timeouts, junk, duplicates).
  static const _maxCandidatesToDownload = 120;
  static const _minUrlCandidateScore = 20;
  static const _minDownloadedScore = 18;
  static const _pickerMinScore = 18;
  /// Per-request timeout so one blocked engine cannot stall All Sources.
  static const _requestTimeout = Duration(seconds: 5);
  /// Stop downloading once the picker can fill with strong candidates.
  static const _strongPickerScore = 36;

  /// Rotating desktop Chrome profiles (User-Agent + matching Sec-Ch-Ua).
  static const _chromeProfiles = <({String ua, String secChUa, String version})>[
    (
      version: '131',
      ua:
          'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
      secChUa:
          '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    ),
    (
      version: '132',
      ua:
          'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36',
      secChUa:
          '"Not A(Brand";v="8", "Chromium";v="132", "Google Chrome";v="132"',
    ),
    (
      version: '133',
      ua:
          'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
      secChUa:
          '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
    ),
    (
      version: '134',
      ua:
          'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
      secChUa:
          '"Chromium";v="134", "Google Chrome";v="134", "Not:A-Brand";v="24"',
    ),
    (
      version: '135',
      ua:
          'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
      secChUa:
          '"Google Chrome";v="135", "Not-A.Brand";v="8", "Chromium";v="135"',
    ),
  ];

  static int _uaRotate = 0;

  static ({String ua, String secChUa, String version}) _nextChromeProfile() {
    final profile = _chromeProfiles[_uaRotate % _chromeProfiles.length];
    _uaRotate++;
    return profile;
  }

  static Map<String, String> _imageDownloadHeaders(Uri uri) {
    final p = _nextChromeProfile();
    return {
      'User-Agent': p.ua,
      // Prefer formats we can magic-byte validate (avoid opaque AVIF from CDNs).
      'Accept': 'image/webp,image/png,image/jpeg,image/*,*/*;q=0.8',
      'Accept-Language': 'en-US,en;q=0.9',
      'Sec-Ch-Ua': p.secChUa,
      'Sec-Ch-Ua-Mobile': '?0',
      'Sec-Ch-Ua-Platform': p.ua.contains('Macintosh') ? '"macOS"' : '"Windows"',
      'Sec-Fetch-Dest': 'image',
      'Sec-Fetch-Mode': 'no-cors',
      'Sec-Fetch-Site': 'cross-site',
      'Referer': '${uri.scheme}://${uri.host}/',
    };
  }


  /// Ranked list for the picker UI — top [pickerMaxResults] by score, with light
  /// junk filtering. Callers must always show the grid when this is non-empty.
  static List<LogoDownloadedCandidate> filterForPicker(
    List<LogoDownloadedCandidate> candidates,
  ) {
    if (candidates.isEmpty) return candidates;
    final sorted = [...candidates]..sort((a, b) => b.score.compareTo(a.score));
    final usable = sorted.where((c) => c.score >= _pickerMinScore).toList();
    final pool = usable.isNotEmpty ? usable : sorted;

    // Collapse exact URL duplicates only — same-host distinct assets must remain
    // so the picker can approach [pickerMaxResults] (was host|bytes, too aggressive).
    final out = <LogoDownloadedCandidate>[];
    final seen = <String>{};
    for (final c in pool) {
      final key = _urlKey(c.url);
      if (key.isNotEmpty && !seen.add(key)) {
        continue;
      }
      out.add(c);
      if (out.length >= pickerMaxResults) break;
    }
    return out;
  }

  static List<String> debugPrimaryQueries(String companyName, {String domain = ''}) {
    final domains = domain.trim().isEmpty ? <String>[] : [domain.trim()];
    return _primaryLogoQueries(_RelevanceContext.from(companyName, domains));
  }

  static List<String> debugExpandedQueries(String companyName, {String domain = ''}) {
    final domains = domain.trim().isEmpty ? <String>[] : [domain.trim()];
    return _expandedLogoQueries(_RelevanceContext.from(companyName, domains));
  }

  static List<String> _primaryLogoQueries(_RelevanceContext ctx) {
    final name = ctx.companyName.trim();
    if (name.isEmpty) {
      if (ctx.domains.isEmpty) return const ['logo png'];
      final d = ctx.domains.first;
      return ['$d logo png', '${d.split('.').first} logo png', d];
    }
    // Bare company name first — Google/Bing Images find brand marks that
    // "{name} logo png" often misses (e.g. MasTec Purnell maple-leaf MP).
    return <String>[
      name,
      '$name logo',
      '$name logo png',
      if (ctx.domains.isNotEmpty) '${ctx.domains.first} logo',
    ];
  }

  /// Smart synonyms used when the primary `{name} logo png` search is empty.
  static List<String> _expandedLogoQueries(_RelevanceContext ctx) {
    final name = ctx.companyName.trim();
    if (name.isEmpty) {
      if (ctx.domains.isEmpty) return const ['brand vector logo'];
      final d = ctx.domains.first;
      final stem = d.split('.').first;
      return [
        '$stem brand vector logo',
        '$stem corporate logo transparent background',
        '$stem official website logo',
        'site:$d logo',
      ];
    }
    final out = <String>{
      '$name brand vector logo',
      '$name corporate logo transparent background',
      '$name official website logo',
      '$name company logo',
      '$name brand logo',
      '$name official logo',
      '"$name" logo png',
      '"$name" logo',
      '$name Canada logo',
      '$name Alberta logo',
    };
    for (final d in ctx.domains.take(4)) {
      out.add('site:$d logo');
      out.add('$name logo site:$d');
      out.add('${d.split('.').first} logo');
    }
    return out.toList();
  }

  static int debugScoreUrl(
    String url,
    String source,
    String companyName, {
    String domain = '',
  }) {
    final domains = domain.trim().isEmpty ? <String>[] : [domain.trim()];
    final ctx = _RelevanceContext.from(companyName, domains);
    return _scoreUrl(url, source, ctx);
  }

  /// Returns ranked, successfully downloaded logo candidates from selected sources.
  Future<List<LogoDownloadedCandidate>> findDownloadedCandidates({
    required String companyName,
    String domain = '',
    LogoSearchEngine engine = LogoSearchEngine.all,
  }) async {
    final name = companyName.trim();
    final dom = _normalizeDomain(domain);
    if (name.isEmpty && dom.isEmpty) return const [];

    final gemini = GeminiClient.isConfigured ? GeminiClient(client: _client) : null;

    // Resolve domains + Gemini plan in parallel (plan is fail-open, ~4s budget).
    final domainFuture = _resolveDomains(name, dom);
    final planFuture = (gemini != null && name.isNotEmpty)
        ? gemini
            .suggestLogoSearchPlan(
              companyName: name,
              knownDomains: dom.isEmpty ? const [] : [dom],
            )
            .timeout(const Duration(seconds: 4), onTimeout: () => null)
            .catchError((Object _, StackTrace _) => null)
        : Future<GeminiLogoSearchPlan?>.value(null);

    var domainList = await domainFuture;
    final plan = await planFuture;

    if (plan != null) {
      domainList = _mergeGeminiDomains(domainList, plan.officialDomains, name);
    }

    final extraQueries = <String>[
      if (plan != null) ...plan.searchQueries,
      if (plan != null)
        for (final alt in plan.alternateNames.take(4)) ...[
          '$alt logo png',
          '$alt logo',
          '$alt brand vector logo',
        ],
    ];

    final ctx = _RelevanceContext.from(
      name,
      domainList,
      extraQueries: extraQueries,
      alternateNames: plan?.alternateNames ?? const [],
    );
    final resolved = switch (engine) {
      LogoSearchEngine.google || LogoSearchEngine.bing => LogoSearchEngine.serper,
      _ => engine,
    };
    final useAll = resolved == LogoSearchEngine.all;
    bool use(LogoSearchEngine e) => useAll || resolved == e;

    // Domain APIs + Serper in parallel. Each source is isolated — one failure
    // must not zero out All Sources.
    final futures = <Future<List<LogoCandidate>>>[];
    if (use(LogoSearchEngine.clearbit)) {
      futures.add(
        _safeSource(() async => _clearbitUrlCandidates(domainList, ctx)),
      );
    }
    if (use(LogoSearchEngine.retoolClearbit)) {
      futures.add(
        _safeSource(() => _retoolClearbitCandidates(domainList, ctx)),
      );
    }
    if (use(LogoSearchEngine.logoDev)) {
      futures.add(
        _safeSource(() async => _logoDevCandidates(domainList, ctx)),
      );
    }
    if (use(LogoSearchEngine.brandfetch)) {
      futures.add(
        _safeSource(() async => _brandfetchCandidates(domainList, ctx)),
      );
    }
    if (use(LogoSearchEngine.serper) && name.isNotEmpty) {
      futures.add(
        _safeSource(
          () => _serperImageSearch(ctx),
          timeout: const Duration(seconds: 16),
        ),
      );
    }

    // Gemini-suggested direct logo URLs (only when the model is highly confident).
    if (plan != null && plan.logoUrlHints.isNotEmpty) {
      final lockedPlan = plan;
      futures.add(
        _safeSource(() async => _geminiUrlHintCandidates(lockedPlan, ctx)),
      );
    }

    // Fail-safe aggregation: never let a single rejected future cancel the rest.
    final urlLists = await Future.wait(
      futures.map(
        (f) => f.catchError((Object _, StackTrace _) => <LogoCandidate>[]),
      ),
    );

    final merged = <LogoCandidate>[];
    for (final list in urlLists) {
      merged.addAll(list);
    }

    // Always include Google Favicon as a last-resort URL candidate when we have domains.
    if (useAll || use(LogoSearchEngine.clearbit)) {
      for (final d in domainList.take(3)) {
        merged.add(
          LogoCandidate(
            url: 'https://www.google.com/s2/favicons?sz=256&domain_url=$d',
            source: 'Google Favicon ($d)',
            score: 28 + _domainMatchBonus(d, ctx),
          ),
        );
      }
    }

    var ranked = _rankAndDedupe(merged, ctx);

    // Download candidates in parallel batches so "All sources" merges as
    // well as any single engine (top [pickerMaxResults] after byte bonus).
    // Skip Gemini vision during bulk download — it was serializing ~30 API
    // calls and pushing wall-clock into 40–60s. Rerank once at the end.
    var toTry = ranked.take(_maxCandidatesToDownload).toList();
    var downloaded = await _downloadCandidatesParallel(
      toTry,
      ctx,
      gemini: null,
      stopAtStrongCount: pickerMaxResults,
    );

    // Sparse rescue: extra Serper queries only when well under the picker target.
    if (downloaded.length < (pickerMaxResults * 2 ~/ 3) &&
        use(LogoSearchEngine.serper) &&
        name.isNotEmpty) {
      try {
        final moreUrls = await _safeSource(
          () => _serperImageSearch(ctx, extraQueries: [
            if (plan != null) ...plan.searchQueries.take(3),
            '$name official logo png',
          ]),
          timeout: const Duration(seconds: 16),
        );
        ranked = _rankAndDedupe([...merged, ...moreUrls], ctx);
        toTry = ranked.take(_maxCandidatesToDownload).toList();
        final more = await _downloadCandidatesParallel(
          toTry,
          ctx,
          gemini: null,
          stopAtStrongCount: pickerMaxResults,
        );
        final seen = {for (final c in downloaded) _urlKey(c.url)};
        for (final c in more) {
          final key = _urlKey(c.url);
          if (key.isEmpty || seen.add(key)) downloaded.add(c);
        }
      } catch (_) {
        // Fail open — keep first-pass downloads.
      }
    }

    if (downloaded.isNotEmpty) {
      // One Gemini rerank pass on the top few — much cheaper than per-image vision.
      downloaded = await _maybeGeminiRerank(downloaded, gemini, name);
      downloaded.sort((a, b) => b.score.compareTo(a.score));
      return filterForPicker(downloaded);
    }

    // Favicon byte download fallback (may be tiny — keep as last ditch).
    for (final d in domainList.take(3)) {
      final fav = await _downloadImage(
        Uri.parse('https://www.google.com/s2/favicons?sz=256&domain_url=$d'),
        source: 'Favicon ($d)',
        minBytes: 400,
      );
      if (fav.ok) {
        return [
          LogoDownloadedCandidate(
            bytes: fav.bytes!,
            source: fav.source,
            url: '',
            score: 12,
            hint: 'Low-resolution favicon — upload a better logo if needed.',
          ),
        ];
      }
    }

    return const [];
  }

  static List<String> _mergeGeminiDomains(
    List<String> existing,
    List<String> suggested,
    String companyName,
  ) {
    final out = [...existing];
    final seen = out.toSet();
    for (final raw in suggested) {
      final d = _normalizeDomain(raw);
      if (d.isEmpty || !seen.add(d)) continue;
      if (!_domainLooksCorporate(d, companyName)) continue;
      // Prefer Gemini domains near the front (after an explicit user domain).
      final insertAt = existing.isNotEmpty && out.isNotEmpty ? 1 : 0;
      out.insert(insertAt > out.length ? out.length : insertAt, d);
    }
    // Deduplicate while preserving order.
    final dedup = <String>{};
    return [for (final d in out) if (dedup.add(d)) d];
  }

  List<LogoCandidate> _geminiUrlHintCandidates(
    GeminiLogoSearchPlan plan,
    _RelevanceContext ctx,
  ) {
    final out = <LogoCandidate>[];
    for (final url in plan.logoUrlHints.take(6)) {
      if (!_isAcceptableLogoUrl(url)) continue;
      out.add(
        LogoCandidate(
          url: url,
          source: 'Gemini URL hint',
          score: 70 + _scoreUrl(url, 'Gemini URL hint', ctx),
        ),
      );
    }
    return out;
  }

  Future<List<LogoDownloadedCandidate>> _maybeGeminiRerank(
    List<LogoDownloadedCandidate> downloaded,
    GeminiClient? gemini,
    String companyName,
  ) async {
    if (gemini == null || downloaded.length < 3) return downloaded;
    // Already have a full strong picker — skip the extra latency.
    final strong =
        downloaded.where((c) => c.score >= _strongPickerScore).length;
    if (strong >= pickerMaxResults) return downloaded;
    try {
      final top = downloaded.take(6).toList();
      final order = await gemini
          .rankLogoCandidates(
            [for (final c in top) c.bytes],
            companyHint: companyName,
          )
          .timeout(const Duration(seconds: 8));
      if (order == null || order.isEmpty) return downloaded;

      final rerankedTop = <LogoDownloadedCandidate>[];
      for (var i = 0; i < order.length; i++) {
        final idx = order[i];
        if (idx < 0 || idx >= top.length) continue;
        final c = top[idx];
        // Boost earlier ranks so picker sort favors Gemini's preference.
        rerankedTop.add(
          LogoDownloadedCandidate(
            bytes: c.bytes,
            source: c.source.contains('Gemini') ? c.source : '${c.source} · Gemini rank',
            url: c.url,
            score: c.score + (18 - i * 3).clamp(0, 18),
            hint: c.hint,
          ),
        );
      }
      final rest = downloaded.skip(top.length);
      return [...rerankedTop, ...rest];
    } catch (_) {
      return downloaded;
    }
  }

  /// Isolate one engine/source so errors never propagate to All Sources.
  Future<List<LogoCandidate>> _safeSource(
    Future<List<LogoCandidate>> Function() run, {
    Duration? timeout,
  }) async {
    try {
      return await run().timeout(
        timeout ?? _requestTimeout,
        onTimeout: () => <LogoCandidate>[],
      );
    } catch (_) {
      return const [];
    }
  }

  Future<List<LogoDownloadedCandidate>> _downloadCandidatesParallel(
    List<LogoCandidate> candidates,
    _RelevanceContext ctx, {
    GeminiClient? gemini,
    int stopAtStrongCount = pickerMaxResults,
  }) async {
    if (candidates.isEmpty) return const [];

    const batchSize = 12;
    final out = <LogoDownloadedCandidate>[];
    final vision = gemini;

    for (var start = 0; start < candidates.length; start += batchSize) {
      final strongCount =
          out.where((c) => c.score >= _strongPickerScore).length;
      if (out.length >= pickerMaxResults &&
          strongCount >= stopAtStrongCount) {
        break;
      }
      if (out.length >= pickerMaxResults * 2) break;
      final batch = candidates.skip(start).take(batchSize).toList();
      final results = await Future.wait(
        batch.map((c) async {
          try {
            final uri = Uri.tryParse(c.url);
            if (uri == null || !uri.hasScheme) return null;
            final isFavicon = c.source.toLowerCase().contains('favicon');
            final dl = await _downloadImage(
              uri,
              source: c.source,
              minBytes: isFavicon ? 400 : 1200,
            );
            if (!dl.ok) return null;

            // Drop scraped micro-icons that aren't useful on labels.
            if (!isFavicon &&
                c.source.startsWith('Site scrape') &&
                dl.bytes!.length < 1800 &&
                !c.url.toLowerCase().contains('logo')) {
              return null;
            }

            // Optional Gemini vision (usually disabled during bulk download).
            if (vision != null && !isFavicon) {
              try {
                final verdict = await vision.validateLogoCandidate(
                  dl.bytes!,
                  companyHint: ctx.companyName,
                );
                if (verdict != null && verdict.shouldKeep &&
                    verdict.confidenceScore >= 0.7) {
                  final finalScore =
                      c.score + _bytesBonus(dl.bytes!) + 6;
                  if (finalScore < _minDownloadedScore) return null;
                  return LogoDownloadedCandidate(
                    bytes: dl.bytes!,
                    source: '${c.source} · Gemini',
                    url: c.url,
                    score: finalScore,
                  );
                }
                if (verdict != null && !verdict.shouldKeep) {
                  final demoted =
                      c.score + _bytesBonus(dl.bytes!) - 16;
                  if (demoted < _minDownloadedScore) {
                    return LogoDownloadedCandidate(
                      bytes: dl.bytes!,
                      source: c.source,
                      url: c.url,
                      score: _minDownloadedScore,
                      hint: 'AI flagged — review carefully before using.',
                    );
                  }
                  return LogoDownloadedCandidate(
                    bytes: dl.bytes!,
                    source: c.source,
                    url: c.url,
                    score: demoted,
                    hint: 'AI flagged — review carefully before using.',
                  );
                }
              } catch (_) {
                // Fail open.
              }
            }

            final finalScore = c.score + _bytesBonus(dl.bytes!);
            if (!isFavicon && finalScore < _minDownloadedScore) return null;
            return LogoDownloadedCandidate(
              bytes: dl.bytes!,
              source: c.source,
              url: c.url,
              score: finalScore,
              hint: isFavicon
                  ? 'Low-resolution favicon — upload a better logo if needed.'
                  : '',
            );
          } catch (_) {
            return null;
          }
        }).map(
          (f) => f.catchError((Object _, StackTrace _) => null),
        ),
      );
      for (final item in results) {
        if (item != null) out.add(item);
      }
    }
    return out;
  }

  Future<LogoFindResult> find({
    required String companyName,
    String domain = '',
    LogoSearchEngine engine = LogoSearchEngine.all,
  }) async {
    final name = companyName.trim();
    final dom = _normalizeDomain(domain);
    if (name.isEmpty && dom.isEmpty) {
      return const LogoFindResult.fail(
        'Enter a customer name or website domain to search.',
      );
    }

    final candidates = filterForPicker(
      await findDownloadedCandidates(
        companyName: name,
        domain: dom,
        engine: engine,
      ),
    );
    if (candidates.isEmpty) {
      return LogoFindResult.fail(
        name.isEmpty
            ? 'No logo found for that domain. Try another domain or upload manually.'
            : 'No usable logo found for “$name”. Searched Clearbit, Brandfetch, and Serper — try a website domain or upload manually.',
      );
    }

    return const LogoFindResult.fail(
      'Logo candidates found — show the picker and let the user choose.',
    );
  }

  List<LogoCandidate> _clearbitUrlCandidates(
    List<String> domains,
    _RelevanceContext ctx,
  ) {
    return [
      for (final d in domains.take(8))
        LogoCandidate(
          url: 'https://logo.clearbit.com/$d',
          source: 'Clearbit ($d)',
          score: _scoreDomainApi(d, 'Clearbit', ctx),
        ),
      for (final d in domains.take(8))
        LogoCandidate(
          url: 'https://logo.clearbit.com/$d?size=512',
          source: 'Clearbit ($d)',
          score: _scoreDomainApi(d, 'Clearbit', ctx) - 1,
        ),
    ];
  }

  /// Keyless CDN / icon endpoints that often still resolve when scrapers stall.
  Future<List<LogoCandidate>> _retoolClearbitCandidates(
    List<String> domains,
    _RelevanceContext ctx,
  ) async {
    final template = _retoolClearbitUrlTemplate();
    if (template.isEmpty) return const [];

    final out = <LogoCandidate>[];
    for (final d in domains.take(4)) {
      final url = template.contains('{domain}')
          ? template.replaceAll('{domain}', d)
          : '$template${template.contains('?') ? '&' : '?'}domain=${Uri.encodeQueryComponent(d)}';
      out.add(
        LogoCandidate(
          url: url,
          source: 'Clearbit via Retool ($d)',
          score: _scoreDomainApi(d, 'Clearbit via Retool', ctx) + 2,
        ),
      );
    }
    return out;
  }

  static String _retoolClearbitUrlTemplate() {
    final env = _optionalEnv('RETOOL_CLEARBIT_LOGO_URL');
    if (env.isNotEmpty) return env;
    return AppConfig.retoolClearbitLogoUrl;
  }

  List<LogoCandidate> _logoDevCandidates(
    List<String> domains,
    _RelevanceContext ctx,
  ) {
    final token = _optionalEnv('LOGO_DEV_TOKEN');
    if (token.isEmpty) return const [];
    return [
      for (final d in domains.take(4))
        LogoCandidate(
          url: 'https://img.logo.dev/$d?token=$token&size=512&format=png',
          source: 'Logo.dev ($d)',
          score: _scoreDomainApi(d, 'Logo.dev', ctx),
        ),
    ];
  }

  List<LogoCandidate> _brandfetchCandidates(
    List<String> domains,
    _RelevanceContext ctx,
  ) {
    final clientId = _optionalEnv('BRANDFETCH_CLIENT_ID');
    if (clientId.isEmpty) return const [];
    return [
      for (final d in domains.take(4))
        LogoCandidate(
          url: 'https://cdn.brandfetch.io/$d/w/512/h/512/logo?c=$clientId',
          source: 'Brandfetch ($d)',
          score: _scoreDomainApi(d, 'Brandfetch', ctx),
        ),
    ];
  }

  static String _optionalEnv(String key) {
    final fromGemini = GeminiClient.envValue(key);
    if (fromGemini.isNotEmpty) return fromGemini;
    if (key == 'SERPER_API_KEY') {
      return AppConfig.serperApiKeyDefine.trim();
    }
    if (key == 'RETOOL_CLEARBIT_LOGO_URL') {
      return AppConfig.retoolClearbitLogoUrl.trim();
    }
                return '';
  }

  static List<String> debugSerperQueries(String companyName) {
    return _serperQueryList(companyName);
  }

  static List<String> _serperQueryList(String companyName) {
    final name = companyName.trim();
    if (name.isEmpty) return const [];
    return [
      '$name logo high resolution transparent',
      '$name official brand logo vector',
      '$name company logo png',
    ];
  }

  Future<List<LogoCandidate>> _serperImageSearch(
    _RelevanceContext ctx, {
    List<String> extraQueries = const [],
  }) async {
    final key = _optionalEnv('SERPER_API_KEY');
    if (key.isEmpty) return const [];
    final name = ctx.companyName.trim();
    if (name.isEmpty) return const [];

    final queries = <String>[
      ..._serperQueryList(name),
      for (final q in extraQueries)
        if (q.trim().isNotEmpty) q.trim(),
    ];

    final seen = <String>{};
    final urls = <String>[];

    Future<List<String>> oneQuery(String q) async {
      try {
          final res = await _client
            .post(
              Uri.parse('https://google.serper.dev/images'),
              headers: {
                'X-API-KEY': key,
                'Content-Type': 'application/json',
              },
              body: jsonEncode({
            'q': q,
                'gl': 'us',
                'hl': 'en',
              }),
            )
              .timeout(_requestTimeout);
        if (res.statusCode < 200 || res.statusCode >= 300) {
          return const <String>[];
        }
        final decoded = jsonDecode(res.body);
        if (decoded is! Map) return const <String>[];
        final images = decoded['images'];
        if (images is! List) return const <String>[];
        final out = <String>[];
        for (final item in images) {
              if (item is! Map) continue;
          final imageUrl = (item['imageUrl'] ?? '').toString().trim();
          final thumb = (item['thumbnailUrl'] ?? '').toString().trim();
          for (final u in [imageUrl, thumb]) {
            if (u.startsWith('http') && seen.add(_urlKey(u))) {
              out.add(u);
            }
        }
      }
      return out;
    } catch (_) {
        return const <String>[];
      }
    }

    final stopOnFirstHit = extraQueries.isEmpty;
        for (final q in queries) {
      urls.addAll(await oneQuery(q));
      if (!stopOnFirstHit) continue;
      final filtered = _urlCandidatesFromSet(
        urls,
        source: 'Serper',
        ctx: ctx,
        sourceBonus: 12,
      );
      if (filtered.isNotEmpty) break;
      }

      return _urlCandidatesFromSet(
        urls,
      source: 'Serper',
        ctx: ctx,
      sourceBonus: 12,
    );
  }

  List<LogoCandidate> _urlCandidatesFromSet(
    Iterable<String> urls, {
    required String source,
    required _RelevanceContext ctx,
    required int sourceBonus,
    int minScore = _minUrlCandidateScore,
  }) {
    final out = <LogoCandidate>[];
    for (final url in urls) {
      if (!_isAcceptableLogoUrl(url)) continue;
      final score = _scoreUrl(url, source, ctx) + sourceBonus;
      if (score < minScore) continue;
      out.add(LogoCandidate(url: url, source: source, score: score));
    }
    return out;
  }

  static List<LogoCandidate> _rankAndDedupe(
    List<LogoCandidate> input,
    _RelevanceContext ctx,
  ) {
    final best = <String, LogoCandidate>{};
    for (final c in input) {
      final key = _urlKey(c.url);
      if (key.isEmpty) continue;
      if (c.score < _minUrlCandidateScore) continue;
      final prev = best[key];
      if (prev == null || c.score > prev.score) {
        best[key] = c;
      }
    }
    final out = best.values.toList()
      ..sort((a, b) => b.score.compareTo(a.score));
    return out;
  }

  static String _urlKey(String url) {
    try {
      final u = Uri.parse(url);
      return '${u.scheme}://${u.host}${u.path}'.toLowerCase();
    } catch (_) {
      return url.toLowerCase();
    }
  }

  static int _scoreDomainApi(
    String domain,
    String source,
    _RelevanceContext ctx,
  ) {
    var score = 72;
    score += _domainMatchBonus(domain, ctx);
    switch (source) {
      case 'Clearbit':
        score += 8;
      case 'Clearbit via Retool':
        score += 9;
      case 'Logo.dev':
        score += 6;
      case 'Brandfetch':
        score += 5;
    }
    return score;
  }

  static int _domainMatchBonus(String domain, _RelevanceContext ctx) {
    var bonus = 0;
    final stem = domain.split('.').first.toLowerCase();
    for (final token in ctx.tokens) {
      if (token.length < 3) continue;
      if (stem.contains(token) || token.contains(stem)) bonus += 18;
    }
    if (ctx.domains.isNotEmpty && ctx.domains.first == domain) bonus += 12;
    return bonus;
  }

  static int _scoreUrl(
    String url,
    String source,
    _RelevanceContext ctx,
  ) {
    var score = 0;
    final lower = url.toLowerCase();
    final host = _hostOf(url);
    final pathAndQuery = lower.replaceFirst(host, '');

    score += _textRelevance('$host $pathAndQuery', ctx);

    if (lower.contains('.png')) score += 12;
    if (lower.contains('.webp')) score += 8;
    if (lower.contains('.jpg') || lower.contains('.jpeg')) score += 5;
    if (lower.contains('.gif')) score += 1;
    if (lower.contains('logo')) score += 10;
    if (lower.contains('brand')) score += 4;
    if (lower.contains('vector') || lower.contains('svg')) score += 3;

    for (final d in ctx.domains) {
      if (host == d || host.endsWith('.$d') || host.contains(d)) {
        score += 28;
        break;
      }
      final stem = d.split('.').first;
      if (stem.length >= 4 && host.contains(stem)) score += 16;
    }

    if (lower.contains('thumb') ||
        lower.contains('thumbnail') ||
        lower.contains('icon') ||
        lower.contains('favicon') ||
        lower.contains('sprite') ||
        lower.contains('avatar') ||
        lower.contains('profile')) {
      score -= 22;
    }
    if (lower.contains('gstatic.com') ||
        lower.contains('googleusercontent.com/imgres') ||
        lower.contains('bing.com/th') ||
        lower.contains('=s16') ||
        lower.contains('=s32') ||
        lower.contains('w=16') ||
        lower.contains('h=16')) {
      score -= 18;
    }

    score -= _junkPenalty(host, lower);

    if (lower.contains('cloudfront.net') &&
        (lower.contains('logo') || source == 'Brands of the World')) {
      score += 8;
    }

    switch (source) {
      case 'Brands of the World':
        score += 6;
      case 'Logo.dev':
      case 'Brandfetch':
      case 'Clearbit':
      case 'Clearbit via Retool':
        break;
      case 'Serper':
        score += 14;
        if (lower.contains('logo') || lower.contains('wordmark')) {
          score += 6;
        }
      case 'Bing Images':
        score += 14;
        if (lower.contains('logo') || lower.contains('wordmark')) {
          score += 6;
        }
      case 'Google Images':
        score += 10;
        if (lower.contains('logo') || lower.contains('wordmark')) {
          score += 4;
        }
      case 'TinEye':
        if (score < 32) score -= 6;
      case 'Wikipedia':
        score -= 8; // Prefer site scrapes / image search over wiki photos.
      case 'Site scrape':
        score += 14;
      case 'DuckDuckGo':
        score += 2;
      case 'DuckDuckGo Images':
        score += 5;
    }
    return score;
  }

  static int _textRelevance(String text, _RelevanceContext ctx) {
    if (ctx.tokens.isEmpty) return 0;
    final normalized = text.toLowerCase().replaceAll(RegExp(r'[^a-z0-9]'), '');
    if (normalized.isEmpty) return 0;

    var matched = 0;
    var score = 0;
    for (final token in ctx.tokens) {
      if (token.length < 3) continue;
      if (normalized.contains(token)) {
        matched++;
        score += token.length >= 6 ? 16 : 12;
      }
    }
    if (matched >= 2) score += 10;
    // Mild demotion only — image-search CDN URLs often omit the company
    // token in the path (hash filenames) but are still query-relevant.
    if (matched == 0 && ctx.companyName.isNotEmpty) score -= 6;
    return score;
  }

  static int _junkPenalty(String host, String lower) {
    var penalty = 0;
    const junkHosts = [
      'shutterstock',
      'gettyimages',
      'istockphoto',
      'alamy',
      'dreamstime',
      'depositphotos',
      '123rf',
      'stock.adobe',
      'adobestock',
      'freepik',
      'pngtree',
      'pngwing',
      'cleanpng',
      'pinimg',
      'pinterest',
      'facebook',
      'twitter',
      'instagram',
      'tiktok',
      'reddit',
      'youtube',
      'linkedin',
      'cnn.com',
      'bbc.',
      'forbes',
      'people.com',
      'celebrity',
      'news.',
      'blog.',
      'wordpress.com',
      'medium.com',
      'ebay.',
      'amazon.',
      'walmart.',
    ];
    for (final junk in junkHosts) {
      if (host.contains(junk) || lower.contains(junk)) {
        penalty += 35;
        break;
      }
    }

    const junkPaths = [
      'stock-photo',
      'stockphoto',
      'headshot',
      'portrait',
      'news/',
      '/article/',
      '/articles/',
      'press-release',
      'thumbnail',
      'apple-touch-icon',
      '/favicon/',
      'favicon.ico',
    ];
    for (final junk in junkPaths) {
      if (lower.contains(junk)) {
        penalty += junk.contains('favicon') || junk.contains('apple-touch')
            ? 80
            : 20;
        break;
      }
    }
    // Wrong-country / homonym traps for warehouse customers.
    if (host.contains('atco.co.uk') || host.endsWith('atco.co.uk')) {
      penalty += 90;
    }
    if (host == 'bfl.ca' || host.endsWith('.bfl.ca')) {
      // Domain-for-sale parking page — not BFL CANADA.
      penalty += 90;
    }
    if (host == 'cde.com' || host.endsWith('.cde.com')) {
      // Irish mining equipment — not CDE Engineering LTD (cdeeng.com).
      penalty += 70;
    }
    return penalty;
  }

  static String _hostOf(String url) {
    try {
      return Uri.parse(url).host.toLowerCase();
    } catch (_) {
      return '';
    }
  }

  static List<String> _companyTokens(String companyName) {
    final cleaned = companyName
        .toLowerCase()
        .replaceAll(
          RegExp(
            r'\b(inc|incorporated|ltd|limited|llc|corp|corporation|co|company|plc|lp|partnership|the|and|of|for)\b',
          ),
          ' ',
        )
        .replaceAll(RegExp(r'[^a-z0-9\s]'), ' ')
        .replaceAll(RegExp(r'\s+'), ' ')
        .trim();
    if (cleaned.isEmpty) return const [];

    final tokens = <String>{};
    for (final word in cleaned.split(' ')) {
      if (word.length >= 3) tokens.add(word);
    }
    tokens.add(cleaned.replaceAll(' ', ''));
    if (cleaned.contains(' ')) {
      tokens.add(cleaned.replaceAll(' ', '-'));
    }
    return tokens.toList();
  }

  static int _bytesBonus(Uint8List bytes) {
    final len = bytes.length;
    if (len >= 8000 && len <= 900_000) return 6;
    if (len < 2500) return -8;
    return 0;
  }

  static bool _isAcceptableLogoUrl(String url) {
    if (!url.startsWith('http')) return false;
    final lower = url.toLowerCase();
    if (lower.endsWith('.svg') || lower.contains('.svg?')) return false;
    if (lower.contains('data:image')) return false;
    if (lower.contains('facebook.com/tr') ||
        lower.contains('doubleclick') ||
        lower.contains('pixel.') ||
        lower.contains('/ads/')) {
      return false;
    }
    return lower.contains('.png') ||
        lower.contains('.jpg') ||
        lower.contains('.jpeg') ||
        lower.contains('.webp') ||
        lower.contains('.gif') ||
        lower.contains('logo') ||
        lower.contains('brand') ||
        lower.contains('cloudfront.net') ||
        lower.contains('logo.clearbit.com') ||
        lower.contains('img.logo.dev') ||
        lower.contains('cdn.brandfetch.io') ||
        lower.contains('google.com/s2/favicons') ||
        lower.contains('lookaside.fbsbx.com');
  }

  Future<LogoFindResult> _downloadImage(
    Uri uri, {
    required String source,
    int minBytes = 1200,
  }) async {
    try {
      final res = await _client
          .get(
            uri,
            headers: _imageDownloadHeaders(uri),
          )
          .timeout(_requestTimeout);
      if (res.statusCode < 200 || res.statusCode >= 300) {
        return LogoFindResult.fail('HTTP ${res.statusCode} from $source');
      }
      final bytes = res.bodyBytes;
      if (bytes.length > maxBytes) {
        return const LogoFindResult.fail('Image too large (over 5 MB).');
      }
      if (bytes.length < minBytes) {
        return LogoFindResult.fail('Image from $source too small.');
      }
      if (!_looksLikeImage(bytes)) {
        return LogoFindResult.fail('Response from $source was not an image.');
      }
      return LogoFindResult.ok(Uint8List.fromList(bytes), source: source);
    } catch (e) {
      return LogoFindResult.fail('Network error ($source): $e');
    }
  }

  static bool _looksLikeImage(List<int> b) {
    if (b.length < 8) return false;
    if (b[0] == 0x89 && b[1] == 0x50 && b[2] == 0x4E && b[3] == 0x47) {
      return true;
    }
    if (b[0] == 0xFF && b[1] == 0xD8) return true;
    if (b[0] == 0x47 && b[1] == 0x49 && b[2] == 0x46) return true;
    if (b.length > 12 &&
        b[0] == 0x52 &&
        b[1] == 0x49 &&
        b[2] == 0x46 &&
        b[3] == 0x46 &&
        b[8] == 0x57 &&
        b[9] == 0x45 &&
        b[10] == 0x42 &&
        b[11] == 0x50) {
      return true;
    }
    // AVIF / HEIF (ftyp....avif / heic)
    if (b.length > 12 && b[4] == 0x66 && b[5] == 0x74 && b[6] == 0x79 && b[7] == 0x70) {
      return true;
    }
    if (b[0] == 0x00 && b[1] == 0x00 && b[2] == 0x01 && b[3] == 0x00) {
      return true;
    }
    return false;
  }

  static String _normalizeDomain(String raw) {
    var d = raw.trim().toLowerCase();
    if (d.isEmpty) return '';
    d = d.replaceAll(RegExp(r'^https?://'), '');
    d = d.replaceAll(RegExp(r'^www\.'), '');
    d = d.split('/').first.split('?').first.trim();
    d = d.replaceAll(RegExp(r'[^a-z0-9.\-]'), '');
    if (!d.contains('.') || d.startsWith('.') || d.endsWith('.')) return '';
    return d;
  }

  /// Prefer an explicit domain, then guessed TLDs, then DuckDuckGo Instant Answer.
  Future<List<String>> _resolveDomains(String name, String dom) async {
    final guessed = _guessDomains(name);
    final domains = <String>[
      if (dom.isNotEmpty) dom,
      ...guessed,
    ];

    if (name.isNotEmpty) {
      try {
        final resolved = await _resolveDomainFromDuckDuckGo(name);
        if (resolved != null &&
            resolved.isNotEmpty &&
            _domainLooksCorporate(resolved, name)) {
          // Insert after an explicit user domain, ahead of pure guesses.
          domains.insert(dom.isNotEmpty ? 1 : 0, resolved);
        }
      } catch (_) {}
    }

    final seen = <String>{};
    return [
      for (final d in domains)
        if (d.isNotEmpty && seen.add(d)) d,
    ];
  }

  Future<String?> _resolveDomainFromDuckDuckGo(String companyName) async {
    try {
      final uri = Uri.https('api.duckduckgo.com', '/', {
        'q': '$companyName company',
        'format': 'json',
        'no_redirect': '1',
        'no_html': '1',
      });
      final res = await _client
          .get(uri, headers: {
            'User-Agent': _nextChromeProfile().ua,
            'Accept': 'application/json',
          })
          .timeout(_requestTimeout);
      if (res.statusCode != 200) return null;
      final body = jsonDecode(res.body);
      if (body is! Map) return null;

      for (final key in ['AbstractURL', 'Redirect', 'OfficialWebsite']) {
        final domain = _normalizeDomain('${body[key] ?? ''}');
        if (domain.isNotEmpty && _domainLooksCorporate(domain, companyName)) {
          return domain;
        }
      }

      final related = body['RelatedTopics'];
      if (related is List) {
        for (final item in related.take(8)) {
          if (item is! Map) continue;
          final firstUrl = '${item['FirstURL'] ?? ''}';
          final domain = _normalizeDomain(firstUrl);
          if (domain.isEmpty) continue;
          if (_domainLooksCorporate(domain, companyName)) return domain;
        }
      }
    } catch (_) {}
    return null;
  }

  static bool _domainLooksCorporate(String domain, String companyName) {
    final host = domain.toLowerCase();
    const blocked = [
      'wikipedia.org',
      'wikidata.org',
      'wikimedia.org',
      'facebook.com',
      'linkedin.com',
      'twitter.com',
      'x.com',
      'instagram.com',
      'youtube.com',
      'bloomberg.com',
      'reuters.com',
      'crunchbase.com',
      'glassdoor.',
      'indeed.com',
    ];
    for (final b in blocked) {
      if (host.contains(b)) return false;
    }
    final tokens = _companyTokens(companyName);
    final stem = host.split('.').first;
    for (final t in tokens) {
      if (t.length >= 4 && (stem.contains(t) || t.contains(stem))) return true;
    }
    // Allow unknown corporate hosts only when no tokens to compare.
    return tokens.isEmpty;
  }

  static List<String> _guessDomains(String companyName) {
    final lower = companyName.toLowerCase().trim();
    final known = _knownDomainHints(lower);
    // Hand-tuned domains win exclusively — TLD spray poisons ATCO/Comco/etc.
    if (known.isNotEmpty) return List<String>.from(known);

    final cleaned = companyName
        .toLowerCase()
        .replaceAll(
          RegExp(
            r'\b(inc|incorporated|ltd|limited|llc|corp|corporation|co|company|plc|lp|partnership|the)\b',
          ),
          '',
        )
        .replaceAll(RegExp(r'[^a-z0-9\s]'), ' ')
        .replaceAll(RegExp(r'\s+'), ' ')
        .trim();
    if (cleaned.isEmpty) return const [];
    final compact = cleaned.replaceAll(' ', '');
    final dashed = cleaned.replaceAll(' ', '-');
    const tlds = [
      '.com',
      '.ca',
      '.com.au',
      '.co.uk',
      '.net',
      '.org',
      '.co',
      '.io',
      '.energy',
      '.oil',
    ];
    final out = <String>{};
    if (cleaned.isNotEmpty) {
      for (final tld in tlds) {
        out.add('$compact$tld');
        if (dashed != compact) out.add('$dashed$tld');
      }
      final words = cleaned.split(' ');
      if (words.length >= 2) {
        out.add('${words.first}${words[1]}.com');
        out.add('${words.first}.com');
        out.add('${words.first}${words[1]}.ca');
        out.add('${words.first}.ca');
      } else if (words.length == 1 && words.first.length >= 3) {
        out.add('${words.first}.com');
        out.add('${words.first}.ca');
        out.add('${words.first}.net');
      }
    }
    return out.toList();
  }

  /// Hand-tuned domains for customers that generic guessing mis-resolves.
  static List<String> _knownDomainHints(String lowerName) {
    final n = lowerName.replaceAll(RegExp(r'\s+'), ' ').trim();
    if (n.contains('strike group') || n == 'strike') {
      return const ['strikegroup.ca', 'strike.ca', 'strikegroup.com'];
    }
    if (n.contains('5blue') || n.contains('5 blue')) {
      return const ['5blue.com', '5blue.ca', 'fiveblue.com'];
    }
    if (n.contains('flint energy') || n == 'flint') {
      return const ['flintcorp.com', 'flintenergy.com', 'flinteng.com'];
    }
    if (n.contains('mastec') && n.contains('purnell')) {
      // Canadian merger entity — not US parent mastec.com (wrong logos).
      return const [
        'mastecpurnell.com',
        'masteccanada.com',
        'mastec.com',
      ];
    }
    if (n.contains('mastec') || n.contains('purnell')) {
      return const ['mastec.com', 'purnell.com'];
    }
    if (n == 'shell' || n.startsWith('shell ')) {
      return const ['shell.com', 'shell.ca'];
    }
    if (n == 'atco' || n.startsWith('atco ')) {
      // Prefer Canadian ATCO Ltd — not UK lawn-mower atco.co.uk.
      return const ['atco.com', 'atco.ca', 'atcoltd.com'];
    }
    if (n.contains('arc resources') || n == 'arc') {
      return const ['arcresources.com'];
    }
    if (n.contains('cde engineering') ||
        n == 'cde' ||
        (n.startsWith('cde ') && n.contains('eng'))) {
      return const ['cdeeng.com', 'cdeengineering.com'];
    }
    if (n == 'epcor' || n.startsWith('epcor ')) {
      return const ['epcor.com'];
    }
    if (n == 'dnow' || n.contains('distributionnow') || n.contains('distribution now')) {
      return const ['dnow.com'];
    }
    if (n == 'comco' || n.contains('comco pipe')) {
      return const ['comcopipe.com', 'russelmetals.com'];
    }
    // Bare "Apex" in Swift Oilfield flow = Apex Distribution (Russell Metals).
    if (n == 'apex' ||
        n.contains('apex valve') ||
        n == 'apex valves' ||
        n.contains('apex distribution')) {
      return const ['russelmetals.com', 'apexdistribution.com'];
    }
    // User typo "Sureus" -> Surerus Murphy Joint Venture.
    if (n.contains('sureus') || n.contains('surerus')) {
      return const ['surerus-murphy.com'];
    }
    if (n == 'bfl' || n.contains('bfl canada') || n.startsWith('bfl ')) {
      // bfl.ca is a domain-for-sale junk page — prefer bflcanada.ca only.
      return const ['bflcanada.ca'];
    }
    if (n.contains('whitecap')) {
      return const ['wcap.ca', 'whitecapresources.com'];
    }
    if (n.contains('arjae')) {
      return const ['arjae.com'];
    }
    if (n.contains('paramount')) {
      return const ['paramountres.com'];
    }
    if (n == 'suncor' || n.startsWith('suncor ')) {
      return const ['suncor.com'];
    }
    if (n.contains('warren valve')) {
      return const ['warrenvalve.com'];
    }
    return const [];
  }

  /// Verified public logo URLs for hard-to-scrape / ambiguous customers.
  static String extensionForBytes(Uint8List bytes) {
    if (_looksLikeImage(bytes)) {
      if (bytes[0] == 0x89) return '.png';
      if (bytes[0] == 0xFF) return '.jpg';
      if (bytes[0] == 0x47) return '.gif';
      if (bytes[0] == 0x52) return '.webp';
      if (bytes[0] == 0x00) return '.ico';
    }
    return '.png';
  }
}
