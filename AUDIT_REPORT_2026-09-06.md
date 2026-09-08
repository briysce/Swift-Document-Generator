# Swift Document Generator — QA Audit (2026-09-06)

**Scope:** Uncommitted working tree on top of **v1.1.93+120** (`a70ec3d`), including Claude Code’s 2026-09-05/06 session.  
**Method:** Transcript digest (`swift_document_generator_chat_transcript.txt`), `flutter analyze`, focused regression tests, code review of Perfect-logo / SVG Swift / logo sizing / sync / BOL units.  
**Policy this pass:** **No commit / no publish** (per user).

---

## Executive summary

The tree is **buildable** and core Shipping layout guards still pass. Claude’s session delivered real value (Perfect pipeline, vector Swift, 74/58 logo heights, BOL unit defaults, audit fixes) but left **product gaps**, **data-risk bugs**, and a large **unreleased** change set still sitting dirty.

| Signal | Result |
|---|---|
| `flutter analyze` | **7 infos** (3× `DropdownButtonFormField.value` deprecations; 4× private `pdf/src/svg` imports) — no errors |
| Focused tests (shipping QA, dual logos, staging promo, history prune, logo dedupe, bol dims) | **All passed** |
| Shipping SO/Contact locks | **Intact** (`afterPillGap=11`, Shipping `showRule=false`, contact gap 3.0) |
| Logo targets | **74 / 58** in code + rule doc |
| Version / changelog | Still **1.1.93** — none of today’s product work is versioned or released |

---

## Critical

### C1 — Perfect overwrites the original even when critics reject
**Where:** `home_screen.dart` `_perfectLogoInBackground` (~1958)  
**Status (2026-09-06 fix):** **Fixed** — overwrite only when `verified`; otherwise write `*_perfect_review.png` and keep the original.  
**What was wrong:** After `LogoPerfectRestore.run`, `source.writeAsBytes(result.png)` always ran. `verified: false` only changed the snack text.  

### C2 — Untracked Swift SVG asset (release footgun)
**Where:** `mobile/assets/images/swift_supply_logo_orange.svg` is **untracked** (`??` in git status) while `pubspec.yaml` already lists it and PDF code embeds it.  
**Status:** Still untracked until the next commit (user forbade commit/publish this pass). File is present locally and in AppData MIR sync.

---

## High

### H1 — `CustomerPreset.updatedAt` stomped on logo remap/delete
**Where:** `app_storage.dart` ~332–338, ~468–473  
**Status (2026-09-06 fix):** **Fixed** — both rebuild sites now pass `updatedAt: preset.updatedAt`.  

### H2 — Private `package:pdf/src/svg/*` imports
**Where:** `shipping_label_pdf.dart`, `bol_label_pdf.dart`  
**What:** Analyzer flags `implementation_imports`. Transcript already recorded a blank-page corruption when the public `SvgImage` path was used wrong.  
**Risk:** `pdf` package upgrades can break vector Swift silently.  
**Fix:** Isolate painter helper; pin `pdf` version; keep a raster-fallback + header-not-blank regression test. *(Deferred — not in this fix pass.)*

### H3 — Snack claims “Gemini + Claude” when Claude may be absent
**Where:** `logo_perfect_restore.dart` / `home_screen.dart`  
**Status (2026-09-06 fix):** **Fixed** — `criticsLabel` reports Gemini / Gemini+Claude / Claude unavailable accurately.

### H4 — BOL unit defaults may override saved units on mount
**Where:** `bol_dimensions.dart` initState  
**Status (2026-09-06 fix):** **Fixed** — item-type defaults apply only when the dimensions line is empty; changing item type still updates units via `didUpdateWidget`.

### H5 — Secrets exposed in transcript (ops, not code)
Anthropic API key and a Gemini Studio key appear in the Claude transcript / OneDrive export.  
**Action:** Rotate both; confirm `.env` stays gitignored (keys not found hardcoded in new Dart files).

---

## Medium

### M1 — Remove-background “perfection” was **not** done
User asked for remove-bg improved to perfection. Diff on `logo_image_process.dart` is essentially dead-code cleanup (`nextBg`). Perfect optionally reuses existing `normalizeToVisibleContent`.  
**Status:** Still owed.

### M2 — Customer `.svg` sidecars written but never used in PDFs
Perfect writes sibling `.svg`; Shipping/Receiving/BOL still raster customer logos via `LogoInkFit` + PNG. Sidecars are future/cache-only today — fine if documented, otherwise waste + sync noise.

### M3 — Approved-layout rule aspect text ≠ code classifier
Rule table still says square aspect **≤ 1.4**; code uses `isSquareOrCircle` ∈ **[0.8, 1.3]**. Heights 74/58 match. Sync the rule wording.

### M4 — Dual restore engines
UI path is Perfect-only; `LogoRestorer` remains for tests/tools; `app_storage` comment still says restore-on-import via LogoRestorer. Confusing for maintainers.

### M5 — Perfect prompt override drops brand/outline notes
`gemini_client` `promptOverride` replaces full `restorePrompt(...)` (no brand/outline append). Can hurt faithfulness vs the carefully tuned restore path.

### M6 — Duplicate `_drawSwiftLogo` (Shipping vs BOL)
Near-identical SVG paint blocks — drift risk.

### M7 — Changelog / version lag
Large product surface (Perfect, vector Swift, 74/58, BOL units, dialog polish) still labeled **1.1.93** with no new changelog section — correct only because user forbade publish; expect a bump before release.

### M8 — Android AppData sync (resolved in Cursor, not by Claude)
Claude was stuck on `robocopy /MIR`. A later Cursor pass **did** mirror-sync both AppData trees, deleted stale PNGs, and rebuilt a debug APK with SVG inside. Still **no release APK/publish**.

---

## Low / intentional leave-alones

| Item | Notes |
|---|---|
| `DropdownButtonFormField.value` ×3 | Deprecation; `initialValue` is not a safe drop-in for live preset sync |
| Stale settings keys (`restoreLowResLogos`, `pageOrientation`) | Harmless if parsers ignore |
| Broader logo-dedupe (`related \|\| visual`) | Intentional fix; watch over-merge |
| Deleted unused brand PNGs | OK if unused (confirmed not in pubspec asset list) |
| `home_screen.dart` size (~6k+ lines) | Structural debt, not a same-day fix |

---

## Verified OK (re-checked this pass)

- Shipping `afterPillGap=11`, `showRule=false`, contact spacing / piece clearance (QA preview PDF layout numbers match lock).
- Receiving still uses under-pill `showRule: true`.
- `squareLogoTargetH=74`, `rectLogoTargetH=58`, `customerLogoToSwiftGap=12`.
- Swift SVG listed in `pubspec.yaml`; Shipping **and** BOL draw SVG with PNG fallback.
- Gemini default model moved off retired `gemini-2.0-flash` → `gemini-3.6-flash`.
- Perfect option defaults off; workspace Restore toggle removed (import dialog only).
- Prior audit fixes still present: logo-dedupe union, receiving address remember, hotkey assign wiring, ARJAE classification test polarity, campaignId / Bulk history test literals.

---

## Transcript claims vs reality

| Claimed done | Reality |
|---|---|
| QA + Fix it all | Mostly yes; 6 env/heuristic test failures from prior audit still expected outside this focused set |
| Logo sizes 74/58 | Yes, and SO/Contact locks preserved |
| Vector Swift on Shipping + BOL | Yes in this tree |
| Perfect pipeline | Present; destructive overwrite + dual-critic honesty gaps remain |
| Remove-bg perfected | **No** |
| Delete original everywhere | In-place overwrite only; Supabase multi-object cleanup not proven |
| Android Swift fixed | Debug path fixed after `/MIR`; no published release |
| 0.99 always | Still an honesty ceiling for customer rasters; Swift vector ≠ generalized restore |

---

## Recommended fix order (when you want work — still no publish)

1. **C1** — don’t overwrite originals until verified / confirmed  
2. **H1** — preserve `updatedAt` on preset logo remap/delete  
3. **C2** — track the SVG asset in git before any release  
4. **H3 / H4** — snack accuracy + BOL unit-default mount behavior  
5. **M1** — dedicated remove-background quality pass (golden suite)  
6. Then version bump + changelog + release when you ask  

---

## Security reminder

Rotate the Anthropic key pasted into the Claude session transcript (and the Gemini Studio key if that alternate was real). Treat the OneDrive transcript export as sensitive.
