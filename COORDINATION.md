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
   `python -m tools.logo_vectorizer.meedo_cycle` (ledger + journal + snapshot)
   and decide every open proposal. Log each unit —
   `python -m tools.logo_vectorizer.meedo_journal log --agent <claude|cursor>
   --task N --kind claim|finding|handoff|done|blocked|pause|resume --summary "…"`
   (or `meedo_journal_log` over MCP). A `done` answers each house rule in its
   evidence: `--commit`, `--tests`, `--looked` (or `--images-looked-at`),
   `--review`, `--measured` (or `--measured-vs-previous`), `--episode`; `n/a:
   <why>` when one does not apply. When a problem is understood:
   `meedo_record_episode`. Every Gemini/Claude call goes through
   `meedo_consult` (judged later), and `python -m tools.meedo_me.trace import`
   keeps the step-by-step procedure. No private side logs — Meedo-Me is the
   shared memory, merged via `merge=meedo`. The board says what is planned;
   the journal says what happened.
9. **Refine Meedo-Me continuously (both agents).** Whenever the journal,
   standup, recall, reviewer, advisor, MCP face, or desktop/OpenClaw wiring is
   wrong, thin, silent, or awkward — **fix it in the same session**, with a
   test and an episode, and say so in the issue so the other agent knows. Do
   not wait for a dedicated Meedo ticket. Prefer deepening integration (one
   memory via MCP across SDG, briyszier, staging-tracker, Meedo-Me app) over
   parallel brains: two of us built the same journal, digest and AI-learning
   layer on the same morning (2026-09-26) — claim before building, even for
   Meedo-Me work. Log Meedo product fixes under board **#3** / workstream
   `meedo-me`.
10. **Meedo succession (advisor → GM).** User doctrine 2026-09-26: Meedo is the
    overqualified advisor who will eventually take the GM chair. Embed it in
    **every** build across our projects — learning from logo engines, Gemini,
    Claude, Serper, OpenClaw — so project-specific skill compounds until live
    APIs and specialist paths can be phased down. Do not treat Meedo as a
    secondary logger. Do not pretend it is expert yet; promote ownership only
    when offline confidence is earned. Standing rule:
    `.cursor/rules/meedo-succession.mdc` + skill `meedo-me`.

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
| 3 | Meedo-Me earns trust + continuous product refine (both agents) | claude+cursor | in progress | **Succession doctrine:** advisor→GM (`.cursor/rules/meedo-succession.mdc`). Standing duty: embed Meedo in every change; learn from Gemini/Claude/logo engines; recall-first; phase tutors when offline confidence earned. Unified observe + MCP study/procedures; all improve loops hooked |
| 4 | Selection rule: rank candidates by craftsmanship once identity holds; design_fit as an engine candidate | claude | in progress | back from Cursor 22:40 UTC (Cursor held it while Claude was out of credits); measuring minds ranking per New Bot's spec on #6: all 18 pairs, both minds, pairs_made_worse empty + lift, before LOGO_MINDS_RANK is enabled |
| 5 | Build and run the Meedo-Me desktop app (briysce/meedo-me, branch `claude/relaxed-babbage-igbk0v`) with local Ollama; connect the MCP server (`python -m tools.meedo_me.connect app`); check Project Manager Mode | cursor | open | needs the PC; Jan's logos still need replacing (needs a Meedo-Me logo from the user) |
| 6 | OpenClaw WhatsApp hourly Claude Code progress + larger Ollama tool model | cursor | in progress | Cloud: OpenClaw + WhatsApp plugin + hourly automation registered; **blocked on Linked-Devices QR**. Watch script is print-only (no double-send). MCP `meedo_claude_progress`. |
| 7 | PROPAK's "Services" i-dot: survives despeckle, lost later in `prepare_for_engine` | cursor | done | prepare-path closed 2026-09-26: clean PROPAK (47px) + GCM golden keep i-dot islands through `prepare_for_engine`/`prune_ink_speckles` (E0022/0673cf2). Board note "lost later" is stale for current recipes — synthetic degrade already merges the separate tittle in RAW. Residual degraded recovery → #4 (design_fit/font). Regression: `test_prepare_idot_retention.py` |
| 8 | Arc: the reconstruction drops tagline letters; the trace draws them red | cursor | done | 2026-09-26: teal kept via quantize/snap/identity-first (`f25d7b0`/`7ba92d7`); E0121; import/downscale Meedo-pass; clean Arc traced ~0.816. Residual: idealize glyph craftsmanship → #4 |
| 9 | Centre-line tracing for thin strokes (PROPAK's red rule, one-pixel borders) | cursor | done | 2026-09-26: `elongated_thin_components` + protect/stamp for rule-like islands inside dense lockups (Propak density gate stays closed). `fit_centerline_stroke` names 1–3px rules as stroked centre-lines; 6px Propak bar still `rect`. Tests: `test_centerline_thin_rules.py`. Do not reopen whole-logo density≤0.30 |
| 10 | Swift rebuild details: ~~the bars' faint grey under-edge~~ (not a design element: row profiles of the source show it is the 1.4 px dark border anti-aliasing into orange and white, symmetric above and below each bar — the rebuild already draws that border); trim anchors on small serif runs | | open | `scripts/rebuild_swift_logo.py`; after any change rerun `scripts/export_swift_app_logos.py` |
| 11 | Gemini+Claude `collab_mind`: escalate-when-stuck (critique/guide/takeover) | cursor | done | 2026-09-26: logo escalate path + **app-wide** `tools/ai_collab` (Meedo cycle, logo/shipping/app improve hooks, MCP `meedo_ai_advise`/`meedo_ai_lessons`, persist → recall offline). Keys gitignored `.env` only — **rotate keys exposed in chat** |
| 12 | Engine minds, part 2: take-over drawing (Claude SVG; Gemini image-model redraw → engine trace), each mind reviewing the other's drawing, one revision round — builds on #11 `collab_mind`; nothing ships without `meedo_review` | claude | in progress | taken over from Claude (usage down); minds useful as critics/judges more than drawers (E0056); next = minds_rank measurement |
| 13 | Meedo-Me consultation memory: every Gemini/Claude answer kept and judged; per-mind track record, disagreements, helped → episode, dataset export, shadow readiness for a local model; `ingest` for the app | claude | done | `meedo_consult`; MCP `meedo_minds`; complements `tools/ai_collab/learn.py` lessons (recall-first) |
| 14 | App (Flutter): audit every AI touchpoint (`claude_client.dart`, `gemini_client.dart` and callers), keys via the env overlay on the PC, current models, append consultations as JSONL for `meedo_consult ingest` | cursor | open | the Python-side improve-loop hooks landed in #11 |
| 15 | Flutter tests that time out here: `pdfrxFlutterInitialize` (pdfium) never finishes in the cloud container; history-dialog preview needs `../.tools/flutter` fonts | | open | fail with the old logo assets too; check on the PC |
