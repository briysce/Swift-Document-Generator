import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:path/path.dart' as p;

import 'app_storage.dart';
import 'theme.dart';

/// In-app "What's new" prompt for the first [maxShows] launches of a campaign.
///
/// Bump [campaignId] when shipping a release that should re-show promo + changelog.
class AppChangelog {
  AppChangelog._();

  static const campaignId = 'whats_new_1_1_98';
  static const maxShows = 3;
  static const title = "What's new (v1.1.68 – v1.1.98)";

  /// Ordered newest-first sections shown in the dialog.
  static const sections = <ChangelogSection>[
    ChangelogSection(
      version: 'v1.1.98',
      bullets: [
        'Fixed: deleted presets (like a stray sample BOL preset) could resurrect from another device\'s stale cache — deletes now stick permanently everywhere',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.97',
      bullets: [
        'Windows: branded startup/loading screen — app icon, Swift Supply mark, and a real progress bar tied to your data actually syncing (presets, contacts, addresses, carriers, signatures, history), not a fake timer',
        'Android: startup no longer flashes to a blank/incomplete screen — the launch screen now stays up until that same data sync finishes',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.96',
      bullets: [
        'Fixed: Claude API key now ships in built Windows/Android apps — "Perfect this logo" dual critique and Bulk\'s common-sense pass actually run (were silently Gemini/regex-only since v1.1.94/v1.1.95)',
        'Bulk: tap Kind or Identity in the review table to confirm or fix an AI-suggested TAG#/PART#/ITEM# before Generate',
        'Bulk: Generate now warns if any unconfirmed AI-suggested identity remains',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.95',
      bullets: [
        'Bulk Avery OA: CPO LINES ranges, ITEM#, fixed qty for comma/range groups; Single vs Per-unit tags per line',
        'Bulk: Claude common-sense pass always attaches reasoning; AI-suggested IDs stay visible for confirm',
        'Carrier field: shared remembered names with Staging & Shipping Log',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.94',
      bullets: [
        'Perfect this logo: Gemini redraw + dual critique (Gemini/Claude); overwrite only when verified; review sidecar when not',
        'Swift logo: true vector SVG on Shipping, Receiving, and BOL (crisp at any zoom)',
        'Shipping / Receiving logos: square/circular 74pt, rectangular 58pt; band grows upward only',
        'BOL: Pallet/Crate default to ft; Box to in; Pipe/Bundle to in×in×ft',
        'Android portrait dialogs: less cramped wrapping; preset sync uses per-preset timestamps',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.93',
      bullets: [
        'Shipping / Receiving: full-resolution logo embed in PDF — no 46px pre-downsample; cubic/average interpolation in restore pipeline for smooth print edges',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.92',
      bullets: [
        'Shipping / Receiving: logos pre-rasterized to red/green cell height — shape slots (0.8–1.3 aspect = red 62pt, else green 46pt); pink line is the only width shrink; vertically centered in header band',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.91',
      bullets: [
        'Shipping / Receiving: logos always fill the red (square/circular) or green (rectangular) cell height — tight ink crop, bottom-aligned row; pink line is the only width shrink',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.90',
      bullets: [
        'Shipping / Receiving: each logo conformally fits its red (square/circular) or green (rectangular) cell — dual logos keep independent heights on a shared baseline (fixes Arc + Inked mismatch)',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.89',
      bullets: [
        'BOL: customer logos scale inside the fixed left header frame — single fills the box, dual shrinks side-by-side without pushing the title bar',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.88',
      bullets: [
        'BOL: customer logo placement restored to the prior left-aligned layout (reverts v1.1.87 stack/center experiment)',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.87',
      bullets: [
        'Logos: reuse the stored file when name, size, and a visual scan match — no more image(1) copies of the same mark',
        'Add from Storage attaches the existing file instead of importing a duplicate',
        'Shipping / Receiving / BOL: extra shape cases (badge, tall, ultra-wide, mixed dual) in the logo display loop',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.86',
      bullets: [
        'BOL: proceed without SO pairing; Shipping Labels copy greys out when pairing is skipped',
        'Ship To Name: address-book memory suggestions; multi-store names listed per location',
        'Logo knockout: stronger textured/photo plate removal (Inked-style) without eating brand ink',
        'Shipping logos: fill red/green target heights; plate leftovers no longer shrink marks',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.85',
      bullets: [
        'BOL: ORDER# fallback when Sales Order is empty; tracking refs shrink/wrap so long values stay visible',
        'BOL: wide customer logos clamp harder left of Probill / Swift',
        'Logo restore: faster on large upscales (strip at working size; linear when scale > 8)',
        'Receiving Label: improve-loop scoring harness (keeps SO→PM hairline; separate from Shipping lock)',
        'Training loops: shared improve-loop curriculum for Shipping, Receiving, BOL, app, and logo restore',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.84',
      bullets: [
        'Shipping Label: approved SO/Contact spacing; aspect-based logo heights (square/circle vs rectangular)',
        'Shipping Label: Customer Pick-Up freight option; improve-loop scoring harness',
        'BOL dimensions: each of L / W / H has its own unit (e.g. 6 in × 6 in × 21 ft)',
        'BOL: standardize micro-label→value gaps; Carrier Vehicle ID + Departure Date row',
        'Freight Charges: add Customer Pick-Up (Shipping Label + BOL radios)',
        'Logo restore: vectorize flat logos → Real-ESRGAN → cubic; Gemini off unless opted in',
        'Logo restore: keep fill↔stroke seams (Swift-quality) — skip destructive re-knockout on clean alpha',
        'Golden suite under qa_logos/golden for restore score regression',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.83',
      bullets: [
        'History: opens instantly, never wipes cloud rows on open; Bulk History archives too',
        'History Template: warns if form snapshot upload fails (PDF still archives)',
        'Logo restore: Real-ESRGAN is primary on Windows (invents lost detail); Gemini stays gated',
        'Address Book works on Receiving; PDF font scale applies; page orientation is fixed by doc type',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.82',
      bullets: [
        'Logo restore: keep grey taglines and black script; crop to the mark instead of the source plate',
        'Logo restore: drop milky JPEG/cubic halos without hollowing silver type',
        'Find logo: Gemini may sharpen low-res rasters; redraws are discarded and the photo is enhanced instead',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.81',
      bullets: [
        'BOL: wide customer logos shrink to stay left of Probill instead of clipping behind the sticker box',
        'Shipping / Receiving: dual C/O logos share a bounded frame so wide marks do not overlap',
        'Logo restore: Gemini enhances the existing pixels, then a studio finish; later restores reuse what worked',
        'Restore runs from Edit logo (with Cancel), not automatically on Generate',
        'BOL line dimensions: length × width × height with a unit',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.80',
      bullets: [
        'Customer logos: strip the plate first, then restore — brand colors stay true and letter holes punch to transparent',
        'PDFs use swift_supply_logo_orange.png (no white seams in SWIFT)',
        'Order Acknowledgement fill keeps full dotted PO / project values and pulls Attn, site, and AFE',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.79',
      bullets: [
        'Delivery Address book opens from the saved list immediately; extra taps no longer stack another window',
        'Android portrait: address lines wrap at a readable size in the book and live suggestions',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.78',
      bullets: [
        'First-launch intro for Swift Staging & Shipping Log (staff release): what it is, warehouse floor / staging / shipping log, then Try it today',
        'Intro shares the same 3-launch window as this What’s new summary; Dismiss only hides it until the next launch',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.77',
      bullets: [
        'Side rail title is MORE APPS (not Operations apps), pinned under Shipping / Receiving / BOL / Bulk',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.76',
      bullets: [
        'MORE APPS on the side rail: open Swift Staging & Shipping Log if installed, or download and install it',
        'Delivery Address book fills missing city/province/postal from OpenStreetMap; same ship-to keeps separate courier rows',
        'History prune removes snapshot-less rows and their PDFs from Supabase Storage',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.75',
      bullets: [
        'Shipping, Receiving, and BOL: upload an Order Acknowledgement under Customer preset to fill sales order, PO, project, customer, and ship-to',
        'Ship-to prefers Delivery Instructions; if missing, choose OA Ship To or enter manually',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.74',
      bullets: [
        'Delivery Address book lists Z–A by Ship To Name; duplicate places merge in the cloud',
        'History opens the PDF (and logo) for that version, not the first generate for the same SO',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.73',
      bullets: [
        'Logo search uses Serper.dev (plus Clearbit/Brandfetch) instead of scraping Google/Bing',
        'Checkbox: convert a low-resolution customer photo to high-resolution',
        'Shipping/Receiving: template prompt only after entering the customer name, not on Generate',
        'Generated document history auto-deletes after 90 days (app + Supabase)',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.72',
      bullets: [
        'Customer logos: optional “Convert low-resolution photo to high-resolution” (RealESRGAN)',
        'Removed Recreate / vectorizer (no SVG tracing). Check the box to upscale low-res logos',
        'Logo restore spinner while Gemini redraws the image',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.71',
      bullets: [
        'Bulk OA: detect “CPO LINE …” notes and loose “part # …” identities',
        'Bulk OA: if PART#/TAG# missing, use the note line under CPO',
        'Shipping/Receiving: template prompt when leaving the Customer field',
        'Looser customer-name matching for templates (Arc / Rite-Way variants)',
        'Android: denser landscape layout; Edit logo crop options no longer wrap vertically',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.70',
      bullets: [
        'Android portrait: smoother Chrome-like header hide/show while scrolling',
        'Android: header auto-hide disabled on Bulk (short page)',
        'Android landscape: Windows-style rail + form + workspace layout',
        'Portrait Android layout unchanged aside from the smoother chrome transition',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.69',
      bullets: [
        'Customer templates: prompt to apply Full, Core only, or Logos only',
        'Shared Delivery Address book for Shipping and BOL',
        'BOL → optional Shipping Labels in the same PDF',
        'Shipping freight / 3rd-party billing + Carrier ↔ ATTN PDF layout',
        'Blank piece counts treated as 0',
        'Per-kind History with cloud PDF storage and local cache',
        'Shared contact memory (Document Generator only — not Swift Staging & Shipping Log roster)',
        'Solid orange chrome logo in the app UI',
        'Android dark mode: Update sheet contrast fix',
      ],
    ),
    ChangelogSection(
      version: 'v1.1.68',
      bullets: [
        'Receiving Label: centered values, Sales Order pill at 60 pt (centered)',
        'Receiving Label: no break line under PM',
        'Shipping Label drawing path unchanged',
      ],
    ),
  ];
}

class ChangelogSection {
  const ChangelogSection({required this.version, required this.bullets});

  final String version;
  final List<String> bullets;
}

class ChangelogPromptState {
  const ChangelogPromptState({
    required this.campaignId,
    required this.timesShown,
  });

  final String campaignId;
  final int timesShown;

  factory ChangelogPromptState.fromJson(Map<String, dynamic> json) {
    return ChangelogPromptState(
      campaignId: '${json['campaignId'] ?? ''}'.trim(),
      timesShown: (json['timesShown'] is num)
          ? (json['timesShown'] as num).toInt()
          : int.tryParse('${json['timesShown']}') ?? 0,
    );
  }

  Map<String, dynamic> toJson() => {
        'campaignId': campaignId,
        'timesShown': timesShown,
      };
}

extension ChangelogPromptStorage on AppStorage {
  File get changelogPromptFile =>
      File(p.join(root.path, 'changelog_prompt.json'));

  Future<ChangelogPromptState> loadChangelogPromptState() async {
    try {
      final f = changelogPromptFile;
      if (!await f.exists()) {
        return const ChangelogPromptState(campaignId: '', timesShown: 0);
      }
      final raw = jsonDecode(await f.readAsString());
      if (raw is! Map) {
        return const ChangelogPromptState(campaignId: '', timesShown: 0);
      }
      return ChangelogPromptState.fromJson(Map<String, dynamic>.from(raw));
    } catch (_) {
      return const ChangelogPromptState(campaignId: '', timesShown: 0);
    }
  }

  Future<void> saveChangelogPromptState(ChangelogPromptState state) async {
    await root.create(recursive: true);
    await changelogPromptFile.writeAsString(
      const JsonEncoder.withIndent('  ').convert(state.toJson()),
      flush: true,
    );
  }
}

/// What's New dialog only (counter already advanced by [maybeShowLaunchPrompts]).
Future<void> showChangelogDialog(
  BuildContext context, {
  required int timesShown,
}) async {
  if (!context.mounted) return;

  String versionLabel = '';
  try {
    final info = await PackageInfo.fromPlatform();
    versionLabel = '${info.version}+${info.buildNumber}';
  } catch (_) {}

  if (!context.mounted) return;

  final remaining = AppChangelog.maxShows - timesShown;
  final footer = remaining <= 0
      ? 'This is the last time this summary will appear on this device.'
      : remaining == 1
          ? 'This summary will appear 1 more time on this device.'
          : 'This summary will appear $remaining more times on this device.';

  await showDialog<void>(
    context: context,
    barrierDismissible: true,
    builder: (ctx) {
      final chrome = SwiftChromeColors.of(ctx);
      return AlertDialog(
        title: Text(AppChangelog.title),
        content: SizedBox(
          width: 480,
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              mainAxisSize: MainAxisSize.min,
              children: [
                if (versionLabel.isNotEmpty) ...[
                  Text(
                    'Installed: $versionLabel',
                    style: TextStyle(
                      fontSize: 12,
                      color: chrome.muted,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const SizedBox(height: 12),
                ],
                for (final section in AppChangelog.sections) ...[
                  Text(
                    section.version,
                    style: TextStyle(
                      fontFamily: 'Oswald',
                      fontWeight: FontWeight.w600,
                      fontSize: 15,
                      color: SwiftColors.accent,
                      letterSpacing: 0.3,
                    ),
                  ),
                  const SizedBox(height: 6),
                  for (final bullet in section.bullets)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 4),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            '•  ',
                            style: TextStyle(color: chrome.ink, height: 1.35),
                          ),
                          Expanded(
                            child: Text(
                              bullet,
                              style: TextStyle(
                                color: chrome.ink,
                                fontSize: 13,
                                height: 1.35,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  const SizedBox(height: 12),
                ],
                Text(
                  footer,
                  style: TextStyle(
                    fontSize: 12,
                    color: chrome.muted,
                    height: 1.35,
                  ),
                ),
              ],
            ),
          ),
        ),
        actions: [
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Got it'),
          ),
        ],
      );
    },
  );
}
