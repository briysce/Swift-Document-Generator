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
8. **Log every unit in Meedo-Me's journal** — claim, finding, done (with its
   evidence), handoff, blocked, pause/resume:
   `python -m tools.logo_vectorizer.meedo_journal log --agent cursor --task 7 --kind claim "…"`
   (or `meedo_log` over MCP). The journal merges by union like the rest of
   Meedo-Me's memory, and the standup reads it: who holds what, what went
   quiet, which done units skipped a rule. The board says what is planned; the
   journal says what happened.
9. **Fix Meedo-Me when it is wrong.** A false alarm, misleading advice, a gap
   in what it records: fix it in the unit that hit it, with a test and an
   episode, whoever you are. Note it in the issue so the other agent knows.

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
| 1 | Put the rebuilt Swift logo into the app (orange SVG + PNGs) | claude | done | `scripts/export_swift_app_logos.py` from the rebuilt master; PDFs draw it as vector paths (E0027). Chrome/splash still use the PNG rendered from it — switching them to `SvgPicture` is optional |
| 2 | Look at the new Swift logo in the running Flutter app (screens, PDFs, dark/light) and report anything off | cursor | open | needs the PC: Claude cannot run the app's UI |
| 3 | Meedo-Me earns trust: track record, autonomy levels, self-run experiments | claude | in progress | user-approved plan: autonomy grows with a proven hit rate. Step 1 done: the work journal (`meedo_journal`, MCP `meedo_log`/`meedo_journal`) |
| 4 | Selection rule: rank candidates by craftsmanship once identity holds; design_fit as an engine candidate | claude | open | Swift solid downscale/import_combo ship the trace; old rule's reconstructions scored 0.9364/0.9465 |
| 5 | Build and run the Meedo-Me desktop app (briysce/meedo-me, branch `claude/relaxed-babbage-igbk0v`) with local Ollama; connect the MCP server (`python -m tools.meedo_me.connect app`); check Project Manager Mode | cursor | open | needs the PC; Jan's logos still need replacing (needs a Meedo-Me logo from the user) |
| 6 | OpenClaw with a larger local model (e.g. `ollama pull qwen3:8b`): does it reach Meedo-Me's tools in its own agent? | cursor | open | 0.6B did not; config via `python -m tools.meedo_me.connect openclaw --model ollama/qwen3:8b` |
| 7 | PROPAK's "Services" i-dot: survives despeckle, lost later in `prepare_for_engine` | cursor | in progress | claim 2026-09-26 by Cursor Cloud; stage-trace E0022; branch `cursor/logo-engine-collab-d4c9` |
| 8 | Arc: the reconstruction drops tagline letters; the trace draws them red | | open | the reviewer's element check now blocks the reconstruction |
| 9 | Centre-line tracing for thin strokes (PROPAK's red rule, one-pixel borders) | | open | user recommendation |
| 10 | Swift rebuild details: the bars' faint grey under-edge; trim anchors on small serif runs | | open | `scripts/rebuild_swift_logo.py`; after any change rerun `scripts/export_swift_app_logos.py` |
| 12 | Engine minds: Gemini + Claude critique, guide and take over (SVG; clean redraw to trace) when the engine is stuck; each reviews the other; nothing ships without `meedo_review` | claude | in progress | user request 2026-09-26; keys are local only (`.env.local`, gitignored) |
| 13 | Meedo-Me learns from every consultation: log, per-mind track record, lessons → episodes, dataset export, shadow mode for a local model | claude | in progress | the path to handing these jobs to Meedo-Me |
| 14 | App: audit every AI touchpoint (claude_client.dart, gemini_client.dart and callers), keys via the env overlay on the PC, current models, write consultations in Meedo-Me's format | cursor | open | format + `ingest` command from #13 first |
| 11 | Flutter tests that time out here: `pdfrxFlutterInitialize` (pdfium) never finishes in the cloud container; history-dialog preview needs `../.tools/flutter` fonts | | open | check on the PC whether they pass there |
