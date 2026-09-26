# Claude Code ↔ Cursor — working together

The user runs both agents at once and asked them to cooperate, with either one
taking over when the other runs out of credits. Claude Code runs in a cloud
container; Cursor runs on the user's PC. We share nothing but this repository,
so the repository is how we talk.

## Protocol

1. **Once per clone:** `sh scripts/setup_collab.sh` — registers the merge
   driver that unions Meedo-Me's memory (episodes, ledger) instead of
   conflicting on every concurrent append.
2. **Branches.** Claude Code pushes to `claude/relaxed-babbage-igbk0v`. Cursor
   pushes to `cursor/<topic>` branches. At the start of every work unit, merge
   the other's latest branch into yours (merge, never rebase or force-push the
   other's work).
3. **Claim before you start.** Put your name and `in progress` on a task in
   the board below, commit and push that change alone, then begin. Never start
   a task someone else holds.
4. **Hand off as you go.** Push at the end of every unit. Update the board and
   the CURRENT STATE section of `.claude/thought_trace.md`. The other agent
   sees only what is committed.
5. **Taking over.** A claim with no push from its holder for 45 minutes may be
   taken over: note `taken over from <agent>` on the task, and start from the
   holder's last pushed state.
6. **Talk** in GitHub issue [#6](https://github.com/briysce/Swift-Document-Generator/issues/6)
   (Claude Code ↔ Cursor coordination). Questions, findings, "I'm about to
   touch file X", credit pause/resume.
7. **House rules** (CLAUDE.md): Meedo-Me standup first; tests pass before
   push; measure per logo against the previous engine on identical inputs;
   look at the images; record the method as an episode when a problem is solved.
8. **Meedo-Me logs every unit (both agents).** Start with
   `python -m tools.logo_vectorizer.meedo_cycle` (ledger + journal + snapshot).
   Decide every open proposal. During/after each unit: `meedo_journal log
   --agent <claude|cursor> --task N --kind claim|finding|handoff|done|blocked
   --summary "…"` with evidence (`--commit`, `--tests`, `--images-looked-at`,
   `--measured-vs-previous` when an engine claim). When a problem is understood:
   `meedo_episodes` / `meedo_record_episode`. Do not keep a private side log —
   Meedo-Me is the shared memory (journal + episodes + ledger), merged via
   `merge=meedo`.
9. **Refine Meedo-Me continuously (both agents).** Whenever the journal,
   standup, recall, reviewer, advisor, MCP face, or desktop/OpenClaw wiring is
   wrong, thin, silent, or awkward — **fix it in the same session**. Do not
   wait for a dedicated Meedo ticket. Prefer deepening integration (one memory
   via MCP across SDG, briyszier, staging-tracker, Meedo-Me app) over parallel
   brains. Log Meedo product fixes under board **#3** / workstream `meedo-me`
   with an episode when the method changes.

### Agent identities (2026-09-26)
- **Claude Code** — cloud container; branch `claude/relaxed-babbage-igbk0v`.
- **Cursor Cloud Agent** — also cloud (not the Desktop app on the PC); branch
  `cursor/logo-engine-collab-d4c9`. Can remote the PC when a task truly needs
  Flutter UI / Meedo-Me desktop / OpenClaw; otherwise works the engine here.

## Task board

Status: `open` · `in progress` · `done` · `blocked (why)`.
Owner: `claude`, `cursor`, or empty.

| # | Task | Owner | Status | Notes |
|---|------|-------|--------|-------|
| 1 | Put the rebuilt Swift logo into the app (orange SVG + PNGs) | claude | in progress | from `assets/brand/swift_supply_logo_rebuilt.svg`; approved by the user 2026-09-26 |
| 2 | Look at the new Swift logo in the running Flutter app (screens, PDFs, dark/light) and report anything off | cursor | open | needs the PC: Claude cannot run the app's UI |
| 3 | Meedo-Me earns trust + continuous product refine (both agents) | claude+cursor | in progress | standing duty: autonomy/hit-rate, journal, MCP, recall/review quality, cross-project MCP integration; either agent fixes Meedo friction when seen; Cursor landed `meedo_journal` + `meedo_cycle` |
| 4 | Selection rule: rank candidates by craftsmanship once identity holds; design_fit as an engine candidate | claude | open | Swift solid downscale/import_combo ship the trace; old rule's reconstructions scored 0.9364/0.9465 |
| 5 | Build and run the Meedo-Me desktop app (briysce/meedo-me, branch `claude/relaxed-babbage-igbk0v`) with local Ollama; connect the MCP server (`python -m tools.meedo_me.connect app`); check Project Manager Mode | cursor | open | needs the PC; Jan's logos still need replacing (needs a Meedo-Me logo from the user) |
| 6 | OpenClaw with a larger local model (e.g. `ollama pull qwen3:8b`): does it reach Meedo-Me's tools in its own agent? | cursor | open | 0.6B did not; config via `python -m tools.meedo_me.connect openclaw --model ollama/qwen3:8b` |
| 7 | PROPAK's "Services" i-dot: survives despeckle, lost later in `prepare_for_engine` | cursor | done | prepare-path closed 2026-09-26: clean PROPAK (47px) + GCM golden keep i-dot islands through `prepare_for_engine`/`prune_ink_speckles` (E0022/0673cf2). Board note "lost later" is stale for current recipes — synthetic degrade already merges the separate tittle in RAW. Residual degraded recovery → #4 (design_fit/font). Regression: `test_prepare_idot_retention.py` |
| 8 | Arc: the reconstruction drops tagline letters; the trace draws them red | | open | the reviewer's element check now blocks the reconstruction |
| 9 | Centre-line tracing for thin strokes (PROPAK's red rule, one-pixel borders) | cursor | done | 2026-09-26: `elongated_thin_components` + protect/stamp for rule-like islands inside dense lockups (Propak density gate stays closed). `fit_centerline_stroke` names 1–3px rules as stroked centre-lines; 6px Propak bar still `rect`. Tests: `test_centerline_thin_rules.py`. Do not reopen whole-logo density≤0.30 |
| 10 | Swift rebuild details: the bars' faint grey under-edge; trim anchors on small serif runs | | open | `scripts/rebuild_swift_logo.py` |
