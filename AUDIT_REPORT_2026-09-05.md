# Swift Document Generator — QA Audit + Fix Pass (2026-09-05)

**Scope:** `mobile/` Flutter app (Windows + Android), `main` @ **v1.1.93+120**
**Method:** `flutter analyze`, manual code review targeted by (a) unresolved items from `AUDIT_REPORT_STRUCTURE.md` (2026-08-05, app was v1.1.47/48 then), and (b) a full read of the 30k-line chat transcript covering v1.1.78→v1.1.93, `git log` verification of specific claims, plus two full `flutter test` runs (before and after fixes).
**Baseline:** 59 commits landed between the last audit and this one (v1.1.48 → v1.1.93), almost entirely customer-logo processing/placement and BOL/Shipping/Receiving layout work.
**This pass:** audited, then fixed everything that was safe to fix without touching hand-tuned, golden-suite-validated visual heuristics. `flutter analyze`: **54 → 3 issues** (the 3 remaining are a deliberate skip, see §4). `flutter test`: **9 pre-existing failures → 6**, all 6 remaining diagnosed with root cause (see §3).

---

## 1. Fixed

### Real bugs (behavior change)

| # | File | Problem | Fix |
|---|------|---------|-----|
| 1 | `mobile/lib/preset_sync.dart` | `_localUpdatedAt(storageKey)` **ignored its own parameter** and returned `presets.json`'s whole-file mtime instead. Editing *any one* preset made `_pushNewerLocalPresets` treat **every** local preset as newer than remote and force-push all of them — able to silently clobber a concurrent edit made on another device/install to an unrelated preset. Flagged as M1 in the 2026-08-05 audit; still present after 59 commits. | Added a real per-preset `updatedAt` (`mobile/lib/label_data.dart`'s `CustomerPreset`), persisted as `updated_at`, stamped to "now" on local saves/edits, mirrored from the remote row on merge-from-remote (not "now" — see code comment), and defaulted to epoch-0 for presets saved before this field existed (so upgrading doesn't make every existing preset look freshly-edited and mass-push on first sync). `_pushNewerLocalPresets` now compares each preset's own timestamp instead of one file-wide stamp. |
| 2 | `mobile/lib/logo_dedupe.dart` (`planCleanup`) | The name-similarity signal (`related`) was computed but **never actually drove clustering** — the loop only unioned two files when `isVisualMatch()` was true, making the `related` check dead weight. Two files with obviously related names (`stem.png`, `stem (2).png`) whose perceptual hash had drifted (e.g. after re-export/recompression) were never grouped for cleanup, contradicting the class's own doc comment ("Same mark (size, **related name**, visual scan) → reuse"). Likely a contributor to the `customer_logos/` duplicate-file buildup the last audit flagged. | Changed the union condition to `related || isVisualMatch(...)`. The existing `_splitVersions` step still separates genuinely different marks that happen to share a name stem, so this doesn't risk over-merging distinct logos. |
| 3 | `mobile/lib/windows_menu_bar.dart` (Options → Hotkeys → **Assign**) | Fully cosmetic — `hotkeyOverrides` round-tripped through storage and was only ever read back to redisplay the same dialog; the app's real `CallbackShortcuts` map never consulted it. A user could "reassign" Ctrl+1, see it saved, and the app kept responding only to the hardcoded default. The dialog's own helper text admitted this ("does not rebind keyboard shortcuts"). Flagged as m7 in the 2026-08-05 audit; still open. | Wired it for real: added a `parseHotkeyString` parser (Ctrl/Shift/Alt/Meta + letter/digit/named key), `windowsShortcutMap` now takes the saved `overrides` map and uses a parsed override in place of each of the 8 assignable defaults when present and valid, and the Assign dialog now validates live (disables OK, shows an error) instead of accepting anything. Takes effect immediately since saving triggers the same settings-changed rebuild that already existed. |
| 4 | `mobile/lib/home_screen.dart` (`_rememberDeliveryAddress`) | Asymmetric address-book wiring: applying a saved address worked for all three document kinds (Shipping/Receiving/BOL via `_applyAddressBookEntry`), but writing a completed address *back* to the address book only happened for Shipping and BOL. A Receiving-only customer's address was never learned from normal use. | Added a `receiving` branch (`customer`/`location`/`carrier` fields) so completing a Receiving label now remembers the address too, same as the other two kinds. |
| 5 | `mobile/test/shipping_qa_preview_test.dart` | The ARJAE "should be square/circle" assertion checked a **stale variable** (`arcInk`, left over from the Arc Resources check earlier in the same test) instead of the freshly-computed `arjaeInk`, and asserted `isFalse` — the opposite of what the `reason:` string says. This is the exact file `.cursor/rules/shipping-label-approved-layout.mdc` names as the **QA guard** for the locked logo-sizing constants; it was silently not testing ARJAE's classification at all. | Fixed to `expect(arjaeInk.isSquareOrCircle, isTrue, ...)`. |
| 6 | `mobile/test/staging_log_promo_test.dart` | Hardcoded `expect(AppChangelog.campaignId, 'whats_new_1_1_91')` while `changelog.dart` had moved on to `'whats_new_1_1_93'`. **This test was failing on `main`.** | Updated the literal to `'whats_new_1_1_93'`. |
| 7 | `mobile/test/document_history_prune_test.dart` | Asserted `historyKinds` covers only Shipping/Receiving/BOL and explicitly **not** Bulk — but Bulk Labels were deliberately folded into the History/prune system later (matching a punch-list item the transcript shows was fixed: "Bulk Labels has no History"). **This test was failing on `main`.** Verified `LabelKind.bulk` inclusion is intentional and safe: `DocumentHistorySync.upload()` saves the same form.json snapshot for Bulk as every other kind, so the opt-in prune sweep (which only deletes rows *without* a snapshot) treats Bulk on equal footing, not as a special wipe risk. | Updated the test to assert Bulk **is** included. |
| 8 | `mobile/lib/home_screen.dart` (saved-signature loader) | `if (bytes == null || !mounted) { showAppSnack(context, ...); return; }` called `showAppSnack` with `context` even when `!mounted` was the reason for the branch — an unsafe `BuildContext`-after-async-gap use if the widget was disposed while awaiting the signature bytes. | Split into `if (!mounted) return;` followed by the `bytes == null` snackbar check, so `context` is never touched after an unmount. |

### Dead code / redundancy (no behavior change)

- Removed two unreferenced logo-drawing helpers flagged unused by the analyzer: `_drawImageInBox` (`bol_label_pdf.dart`) and `_drawImageFit` (`shipping_label_pdf.dart`) — leftovers from earlier logo-placement iterations.
- Removed `targetH_squareCircle` / `targetH_rectangular` in `shipping_label_pdf.dart` — plain aliases of `squareLogoTargetH` / `rectLogoTargetH` with non-camelCase names; call sites now use the canonical constants directly.
- Removed the fully-unused `pageOrientation` field/`PdfPageOrientation` enum from `pdf_render_options.dart` — it was a no-op for every PDF builder (Shipping/Receiving always landscape, BOL always portrait) and its Windows UI control had already been removed; confirmed zero remaining consumers before deleting.
- Deleted 3 unreferenced, undeclared image assets: `swift_supply_logo.png`, `slst_mark.png`, `swift_staging_log_icon.png` (not in `pubspec.yaml`'s asset list, not referenced anywhere in `lib/`).
- Removed dead `_brandInkCount` helper in `customer_logo_knockout_regression_test.dart` and dead `loadBlock` variable in `app_improve_loop_test.dart`.
- `logo_image_process.dart`: removed the unused `nextBg` local in `detectLetterOutline` — **see §3 for why the behavior itself was left alone.**

### `flutter analyze` cleanup (54 → 3 issues)

Removed unused/unnecessary imports (`dart:typed_data` superseded by `flutter/services.dart` or `flutter/foundation.dart`, unused `dart:math`, unused `package:image/image.dart`) across `home_screen.dart`, `app_storage.dart`, `document_history_sync.dart`, `shipping_label_pdf.dart`, and ~12 test files; fixed an initializing-formal in `address_book_sync.dart`; removed an unnecessary string-interpolation brace in `gemini_client.dart`; collapsed `(_, __, ___)` → `(_, _, _)` callback params in `home_screen.dart`/`logo_finder.dart`; converted `if (x != null) k: x` map/list entries to null-aware `?x` / `k: ?x` syntax in `signature_pad.dart`, `windows_menu_bar.dart`, and two test files; added a documented `// ignore` for the one legitimate `@visibleForTesting` use in the `tool/prune_history_without_snapshots.dart` maintenance CLI; and removed 4 genuinely-dead null-checks in `home_screen.dart`'s generate flow that the analyzer proved were unreachable (`piecePlan!` after promotion, `lastFile != null` after definite assignment, `lastFile?.path ?? ''`).

---

## 2. Deliberately left alone

| Where | Why |
|---|---|
| `DropdownButtonFormField.value` deprecation (`bol_dimensions.dart:307`, `home_screen.dart:3201,4031`) — 3 spots | Flutter's suggested replacement, `initialValue`, is **not** a drop-in rename: `value` keeps the field synced on every rebuild (needed here — e.g. loading a preset changes the backing field and the dropdown must follow), while `initialValue` only seeds the first build. Migrating would risk dropdowns silently freezing on stale selections after a preset load. Left as-is; not worth the regression risk for a lint warning. |

---

## 3. Pre-existing test failures — diagnosed, not blind-fixed

The original full suite had **9** failing tests before this pass (2 were the stale-literal bugs fixed in §1 as #5/#6, 1 was the Bulk-history test fixed as #7). The remaining **6** are pre-existing on `main` (confirmed unrelated to anything touched today) and fall into three categories:

**Environment/fixture-dependent (not code bugs) — 3:**
- `generate_bol_wide_logo_preview_test.dart` — requires a local file `qa_logs/mastec_purnell_wide.png` that doesn't exist in this checkout; it's a manually-saved fixture from an earlier live logo-search session, not part of the repo.
- `logo_finder_mastec_purnell_live_test.dart`, `logo_finder_strike_guard_test.dart` — both explicitly "live" tests that scrape real search results over the network; inherently non-deterministic, not something to "fix" in code.

**Delicate, already-tuned image heuristics — 2 (one investigated hands-on):**
- `customer_logo_knockout_regression_test.dart` (`bird_source.png: prepare left opaque black plate corners`) — traced to `LogoInkFit.prepare`'s guard at `logo_ink_fit.dart:172`: it skips background-stripping entirely whenever `hasMeaningfulTransparency(input)` is true, on the assumption the image was "already knocked out at import." `bird_source.png` apparently has some pre-existing transparency *and* a large solid black plate that still needs removing, so the guard fires and skips it. Not fixed — the guard exists specifically to avoid re-stripping already-clean logos (comment cites a real prior regression, "GCM Modification"), and I don't have a way to validate a change against all 24 golden customer logos plus the wider golden suite in this pass.
- `restore_catalog_test.dart` (`stripHaloFringe punches a white rim, keeps the brand fill`) — traced to `logo_image_process.dart`'s `lum > 230` exclusion in `stripHaloFringe`, which was added (per its own comment) to stop a *different* real logo's genuine near-white letter outline from being wrongly punched as JPEG halo. This test's synthetic rim color (240,240,240) falls inside that same "protected" luminance range, so it no longer gets stripped. Same story as above: two real, comment-justified fixes are in tension, and resolving it safely needs golden-suite validation, not a guess.

  **I did test one hypothesis empirically before writing this**: for the `detectLetterOutline` `nextBg` dead-variable (§1), I tried requiring the ring pixel to touch background *and* fill (the plausible reading of the unused variable's intent), reran the two regression suites, and it broke a currently-passing test (`ensureLetterOutline restores a dropped black text stroke`) plus the same `bird_source.png` case — hard evidence that the *original* code (nextFill-only) is the one that's actually correct, and `nextBg` really was just dead cruft, not a bug. Reverted that change; kept only the dead-variable removal. This is why I stopped short of guessing on the other two.

**Known, actively-worked quality ceiling — 1:**
- `logo_golden_suite_test.dart` (`golden prepare path: self-consistency + quality floors`) — `swift_orange` IoU scored 0.7397 against a 0.75 floor. This matches the transcript's own account almost exactly: the team's 0.90 composite-quality target was never reached across a month of iteration (v1.1.78–v1.1.93), plateauing around 0.77–0.81. Not a regression to chase in this pass — it's the same open research problem the project has already spent significant effort on.

---

## 4. Structural concerns (trend, not fixed — out of scope for a safe same-day change)

| Observation |
|---|
| `home_screen.dart` has grown from ~2,896 lines (Aug 5 audit) to **6,310 lines** today — more than doubled in a month. Still one file mixing form state, logo workspace, sync UX, both Windows/Android scaffolds, dialogs, and the address-book editor. Splitting was already recommended last audit and deferred; growth has made the case stronger, not weaker. |
| `logo_image_process.dart` is now **2,448 lines** — effectively a second god file, grown out of the month-long logo-restore/knockout work. Not urgent to split (it's cohesive image-processing code), but worth watching. |

---

## 5. Verified as resolved since the 2026-08-05 audit (no action needed, confirmed before this pass)

- **B3** Generate button hidden on narrow Windows layout — fixed; kept visible in the form column.
- **M3** `_buildItemTypeField` mutating a controller during build — fixed via `addPostFrameCallback`.
- **M5** `fontScale` — now actually applied.
- **M6** Auto-update "mark day done" — now happens *after* the update dialog.
- **M10** Android release signing — conditionally uses a real keystore when `key.properties` exists.
- **m5** Signature delete — now deletes the Supabase storage object too, not just the DB row.
- **History prune-on-open wipe** — `pruneWithoutSnapshots()` explicitly opt-in/CLI-only now.
- **Gemini logo restore** — confirmed demoted to opt-in only via `LOGO_RESTORE_USE_GEMINI=1`.
- **BOL PDF layout** — confirmed untouched since v1.1.89 through all later Shipping/Receiving rewrites.
- **`customerLogoToSwiftGap` (12.0pt pink-line gap)** — survived the v1.1.92/93 sizing rewrite intact.

---

## Summary

**Fixed:** 4 real bugs (multi-device preset sync clobbering, logo-dedupe name matching, non-functional Hotkeys UI, Receiving address-book gap), 3 currently-failing tests (2 stale literals, 1 stale expectation), 1 unsafe-context bug, plus a full dead-code/lint sweep (54 → 3 analyzer issues).
**Deliberately not touched:** 3 `DropdownButtonFormField.value` deprecations (real regression risk for a cosmetic warning).
**Diagnosed but not blind-fixed:** 6 pre-existing test failures — 3 are environment/network-dependent, 2 are delicate image heuristics already in tension with other real fixes (one hypothesis tested and reverted after breaking a passing test), 1 is a known, actively-worked quality ceiling documented in the project's own history.
