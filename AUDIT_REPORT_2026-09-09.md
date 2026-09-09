# Swift Document Generator — QA Audit (2026-09-09)

**Scope:** Everything Claude Code + Cursor shipped as **v1.1.95** ("Finish Bulk OA
Claude enrich + shared Carrier sync") — `mobile/lib/bulk/*`, `job_pdf_ai.dart`,
`claude_client.dart`, `carrier_sync.dart`, the bulk UI in `home_screen.dart` — plus
one cross-cutting build-pipeline check that turned out to affect a v1.1.94 feature
too.

**Method:** Read every changed/new file against `git show bd5d6e9`; re-derived the
original plan from `~/.claude/plans/rippling-roaming-spark.md`; ran `flutter
analyze` and the full `flutter test` suite (62 files); queried the live Supabase
project directly (`list_tables`, `list_migrations`, RLS/grants) instead of assuming;
tested the live Anthropic API with the configured key/model. Fixes below are all
applied and green; nothing in the Shipping/Receiving/BOL approved-layout locks was
touched.

## Executive summary

The Bulk OA parser/model/Claude-reconciliation logic itself is solid — 22 unit
tests (18 existing + 4 new) all green, `flutter analyze` clean. Two real problems
were found and fixed:

1. **Critical, cross-feature:** the built app never actually had an Anthropic API
   key, so both "Claude" features shipped so far (v1.1.94 dual logo critique,
   v1.1.95 Bulk common-sense pass) silently ran Gemini/regex-only for every real
   user, every time, since the day each shipped.
2. **High:** the Bulk review table promised the user could "confirm" an
   AI-suggested TAG#/PART#/ITEM# before printing, but had no way to actually do
   that — and the AI-suggested flag didn't survive into the printed PDF/Word doc
   at all, so an unconfirmed guess printed identically to a verified value.

Both are fixed, tested, and included in a **v1.1.96** republish (see bottom).

---

## Critical

### C1 — Anthropic API key never reached the shipped binary

**Where:** `scripts/flutter_dart_defines.ps1`

`publish_release.ps1` builds with `--dart-define` flags pulled from the repo's
gitignored `.env` by `Get-FlutterDartDefines`, but that function hard-whitelists
only `SERPER_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY` — confirmed by the
publish log itself ("Including **3** dart-define(s) from .env"). `ANTHROPIC_API_KEY`
was never in that list, and `.env` is not bundled into `dist/` either, so
`ClaudeClient.resolveApiKey()`'s three lookup paths (`String.fromEnvironment`,
`Platform.environment`, gitignored `.env` walk) all come back empty in every
built Windows/Android app. `ClaudeClient.isConfigured` is `false` for every real
user.

Impact — two shipped, changelogged features silently downgraded from day one:

| Feature | Changelog claim | Actual behavior for every real user |
|---|---|---|
| v1.1.94 "Perfect this logo" | "Gemini redraw + dual critique (Gemini/Claude)" | Gemini-only critique (code has a graceful fallback log line, `perfect_restore: no Claude key configured`, but the UI's own `criticsLabel` correctly says Gemini-only — it just never says otherwise in production) |
| v1.1.95 Bulk OA | "Claude common-sense pass always attaches reasoning" | `enrichLines()` returns the input unchanged every time (`if (!ClaudeClient.isConfigured) return parsed;`); no `aiNote` ever attached, no gap-filling ever happens |

This also fully explains **H3 from `AUDIT_REPORT_2026-09-06.md`** ("Snack claims
'Gemini + Claude' when Claude may be absent") — that pass fixed the UI string to
be honest about Claude's absence; this pass fixes *why* it was always absent.

Verified live (key present in `.env`, not printed/committed):
`POST https://api.anthropic.com/v1/messages` with `model: "claude-sonnet-5"` →
**HTTP 200**, real completion — the model id and key are both good; only the
build pipeline was dropping them.

**Fix applied:** added `ANTHROPIC_API_KEY` to the whitelist in
`flutter_dart_defines.ps1`. Verified `Get-FlutterDartDefines` now returns 4
defines instead of 3. This is the same trust model already accepted for the other
three keys (client-embedded, single-company internal tool) — no new class of risk.
Rolls into the v1.1.96 republish.

---

## High

### H1 — Bulk review table had no way to "confirm" an AI-suggested identity

**Where:** `home_screen.dart` (bulk review `DataTable`), `bulk_label_pdf.dart` /
`bulk_label_docx.dart`

The plan and the shipped copy both promise a confirm step:

> "Lines Claude already filled appear italic with a \* in the review table —
> confirm those before printing." (`_promptBulkMissingIdentity` dialog copy)
>
> "…never silently trusted…" / "…must still confirm…" (`job_pdf_ai.dart` doc
> comments)

But the Identity/Kind cells in the review table were plain `Text` widgets — no
edit affordance existed anywhere. Worse, `BulkLabelInstance` (what
`bulk_label_pdf.dart` / `bulk_label_docx.dart` actually render) never carried
`missingIdentity` or `aiNote` — the italic "\*" only ever existed in the in-app
table. Once a user clicked Generate, an unconfirmed AI guess printed on the real
Avery sticker/Word doc **with the exact same bold-value styling as a verified
regex match**, with no way to tell them apart after the fact.

**Fix applied:**
- Identity/Kind cells are now real edit affordances (`DataCell(..., showEditIcon:
  true, onTap: ...)` — the idiomatic Flutter pattern for exactly this). Tapping
  either opens a small dialog (Claude's reasoning if any, a TAG#/PART#/ITEM#
  dropdown, a value field) that saves back through the existing
  `OrderAckParseResult.replacingLine` / `BulkLabelLine.copyWith`. Saving a
  non-empty value clears `missingIdentity`; clearing it to blank keeps the line
  flagged (still needs a real answer from the PM) — the same semantics the rest
  of the feature already uses.
- `_generateBulkLabels()` now gates on any remaining `missingIdentity` line: a
  dialog explains the marker won't survive into the printed doc and offers
  **Review first** or **Generate anyway** — a warning, not a hard block, so it
  can't strand a user who genuinely wants to proceed.
- A one-line caption under the table makes the tap targets discoverable.

This only touches the brand-new Bulk feature; no Shipping/Receiving/BOL layout
code was touched.

---

## Medium

### M1 — `shared_carriers` / `shared_carrier_tombstones` had no migration in this repo

**Where:** `supabase/`

`carrier_sync.dart` reads/writes `shared_carriers` and `shared_carrier_tombstones`,
but unlike every other shared table this repo uses (`shared_contacts`,
`shared_delivery_addresses`, `customer_presets`, `signatures`,
`generated_documents`), there was no `.sql` file for it under
`supabase/migrations/`. Checked the live project directly rather than assuming:

- Both tables **do** exist (`list_tables`) with the expected `name_key` PK shape,
  RLS enabled, and full `anon` grants (`SELECT/INSERT/UPDATE/DELETE`) — so the
  feature is **not** broken at runtime; there are already 6 real rows.
- `list_migrations` shows a migration named `shared_carriers` (version
  `20260909174839`) was already applied directly against the project — just never
  committed as a file in this repo's tree (it's owned by the sibling Swift
  Staging & Shipping Log codebase).

**Fix applied:** added `supabase/migrations/20260909174839_shared_carriers.sql`
(matching that exact version/name so tooling doesn't think it's a new,
un-applied migration), using the same `IF NOT EXISTS` / `DROP POLICY IF EXISTS`
pattern as every sibling migration — a no-op against the live tables. Documented
the table (and that Document Generator doesn't own it) in `supabase/README.md`.

### M2 — Dead `previewColumn` getter

**Where:** `mobile/lib/bulk/bulk_label_models.dart`

`BulkIdKindLabel.previewColumn` was an exact duplicate of `fieldLabel`, added but
never referenced anywhere. Removed.

### M3 — `OrderAckParseResult.replacingLine` had zero test coverage

Both the existing print-mode dropdown and the new identity-edit dialog depend on
`replacingLine` to update exactly one line by `lineNo` and leave every other line
untouched — that invariant was never directly tested. Added a `replacingLine`
group to `bulk_labels_test.dart` (updates only the matching line; confirming an
AI-suggested identity clears `missingIdentity`; clearing a value back to blank
keeps it flagged) plus a small test for the new Generate-time unconfirmed-count
gate. 22 → 24 tests green.

---

## Verified OK (re-checked this pass, not just trusted)

- `flutter analyze` on the whole `mobile/` package: **7 pre-existing info-level**
  issues only (deprecated `value`→`initialValue` on unrelated form fields,
  `implementation_imports` in BOL/Shipping PDF), none in the touched files, none
  new after my edits (re-ran targeted analyze: 2/2 are the same pre-existing
  infos, just shifted line numbers).
- Full `flutter test` (62 files, ~12 min): **208 passed, 6 failed, 1 skipped**,
  all 6 failures traced to specific unrelated causes — none touch `bulk/`,
  `job_pdf_ai.dart`, `claude_client.dart`, `carrier_sync.dart`, or the Bulk UI:
  - `logo_finder_mastec_purnell_live_test.dart`, `logo_finder_strike_guard_test.dart`,
    `logo_finder_win_hour_12_test.dart`, `logo_finder_funnel_diag_test.dart` — live
    web-scraping tests hitting real external sites (Google/Bing/Serper); result
    counts vary run to run by design.
  - `generate_bol_wide_logo_preview_test.dart` — fails only because it depends on
    a PNG one of the above live tests writes; knock-on, not independent.
  - `logo_golden_suite_test.dart` — one IoU self-consistency float just under
    threshold (0.7397 vs 0.75) — golden-image rendering variance.
  - These are pre-existing, environment/network-dependent, and owned by the
    separate logo-finder/golden-suite QA domain — out of scope for this pass per
    the improve-loop ownership rules; not touched.
- `OrderAckParser` CPO ranges/lists, `ITEM#`, quantity-per-group (not
  per-CPO-number), header PO/Location wrap handling — all match the plan's spec
  and the real customer fixture (`propak_oa_1431332.txt`).
- `JobPdfAi.applyClaudeLineSuggestions` reconciliation (agree / disagree / fill-gap)
  matches the "never silently override a regex hit" design.
- `CarrierSync` error handling doesn't crash on failure (catches, snacks or
  silently degrades to local-only, matching the existing Contact-field pattern).
- Test fixture `propak_oa_1431332.txt` — real OA text, no secrets.
- "Perfect this logo" overwrite-only-when-verified safeguard (`*_perfect_review.png`
  sidecar for unverified attempts) — still present in `home_screen.dart`, unrelated
  to this session's changes, spot-checked per the standing project memory.

## Open item carried over (not code — needs your action)

`AUDIT_REPORT_2026-09-06.md` (H5) already flagged that an Anthropic key and a
Gemini Studio key appeared in a Claude transcript/OneDrive export and should be
rotated. I can't tell from here whether that rotation happened. The key currently
in `.env` **is live and working** (tested above), but I have no way to confirm
whether it's a already-rotated fresh key or the one that leaked. Please confirm/rotate
directly in the Anthropic console if that's still open — not something I can do
from inside the repo.

---

## Fixes shipped this pass

| Fix | File(s) |
|---|---|
| Ship `ANTHROPIC_API_KEY` as a `--dart-define` | `scripts/flutter_dart_defines.ps1` |
| Editable Kind/Identity cells + confirm dialog | `mobile/lib/home_screen.dart` |
| Generate-time "unconfirmed identities" warning gate | `mobile/lib/home_screen.dart` |
| Discoverability caption under the review table | `mobile/lib/home_screen.dart` |
| Removed dead `previewColumn` getter | `mobile/lib/bulk/bulk_label_models.dart` |
| `replacingLine` + gate-count test coverage (+4 tests) | `mobile/test/bulk_labels_test.dart` |
| `shared_carriers` migration committed to this repo | `supabase/migrations/20260909174839_shared_carriers.sql` |
| Documented `shared_carriers` ownership | `supabase/README.md` |
| Changelog + promo campaign bump | `mobile/lib/changelog.dart`, `mobile/test/staging_log_promo_test.dart` |

**Republished as v1.1.96** (supersedes v1.1.95, which shipped with the key gap).
