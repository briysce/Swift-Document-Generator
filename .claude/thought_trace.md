# Thought trace — Metacognitive PM

Reasoning log for the Observe → Deduce → Experiment → Record loop. Structured,
queryable memory lives in Meedo-Me (`meedo_episodes.json`, `meedo_ledger.json`);
this file is the readable account of *why*.

---

## 2026-09-25 — Initial state

### Topology (observed)

- **Monorepo, Python + Flutter.** Tracked: 148 Dart, 88 Python, 11 SQL. Top dirs by
  file count: `mobile/` 237, `tools/` 51, `scripts/` 35, `qa_logos/` 32. No
  TypeScript, Rust or Tauri code is tracked here.
- **Logo engine:** `tools/logo_vectorizer/` (reconstruction `idealize.py`, primitives
  `shapes.py`, fonts `glyph_match.py`, reference-free quality `ideality.py`) and
  `scripts/logo_*.py` (shipping path, improve loop, golden-suite metrics).
- **Meedo-Me today:** memory and judgment in Python — `meedo_ledger.py` (runs,
  proposals, decisions), `meedo_review.py` (verdicts), `meedo_episodes.py` (method),
  `meedo_advisor.py` (analysis) — plus a Dart client in `mobile/lib/meedo_me_*.dart`
  that speaks OpenAI-compatible HTTP to a local server.
- **The Jan fork (= Meedo-Me's client) does not exist as a fork.** It is an unmodified
  clone of `menloresearch/jan` @ `af0ddc3` in an ephemeral scratchpad (504 MB). Nothing
  built there survives this container. License: Apache-2.0 — forking and renaming are
  permitted; keep LICENSE and the Menlo Research notice, and mark changes.
- **Prior agent instructions:** `.cursor/rules/*.mdc`. The logo rule requires the loop
  to continue unprompted and promote only score-proven techniques; north star is
  `swift_orange` / `swift_orange_solid`.

### Directive interfaces mapped to what exists

| Directive names | Exists? | Real equivalent |
|---|---|---|
| `/benchmarks/vector_scores.json` | no | `meedo_ledger.json` (per-run observations, committed); `improve_log.jsonl` (per-row, gitignored); run snapshots in `training_lessons.json`. Not duplicated: one registry, not two that drift. |
| `npm run test:vectorizer` | no | Fast: `pytest tools/logo_vectorizer/tests` (54 tests, ~1 s). Corpus: `scripts/logo_restore_improve_loop.py --engines vectorize --min-height 1200` (~40 min; add `LOGO_IDEALIZE=1` for the reconstruction path). |
| memory vault | yes | `meedo_episodes.json` — problem, first read, evidence, cause, fix, verification, method. |
| `.claude/thought_trace.md` | now | this file. |

### Baselines (verified, not assumed)

- Default path, `--min-height 1200`: Swift vectorize anchors **0.9269** composite /
  0.9356 v2; baseline engine 0.9270. Reproduced to four decimals and byte-identical
  after every change that targets only the reconstruction path.
- Reconstruction path (`LOGO_IDEALIZE=1`): corpus 0.7567 / 0.7584 against 0.7433 /
  0.7455 for the trace; Swift anchors 0.9285 / 0.9389; 7 of 18 pairs improved, 0
  regressed. Oracle ceiling 0.7688.

### Where the directive conflicts with measured fact

1. **Stage 4 accepts on score alone** (≥ 0.927 / 0.9269 → commit). On this corpus
   `composite` ranked outputs that had *deleted GCM's red monogram* as the best results;
   the loop reported them as its largest wins. Score-only acceptance would have
   committed them. Amendment in force: success = guardrails hold **and** Meedo-Me's
   reviewer passes **and** the output has been looked at. The two baselines stay as
   non-regression guardrails — the role they already play.
2. **The legacy composite cannot express the goal.** An exact copy of the reference
   scores 0.93–0.95, not 1.0, and fidelity to a degraded raster rewards doing nothing.
   `composite_v2` (identity = 1.0) and `ideality` (reference-free craftsmanship) are
   the scales that can say "better vector"; `composite` is the continuity guard.
3. **"Eliminate path clustering on 90° corners"** is measurable without a reference:
   `ideality` reports anchors per 1k, straightness and staircase. Adopted as a target.
4. **Two of the three Jan features already exist upstream** — native MCP
   (`src-tauri/src/core/`), an agent loop with lifecycle hooks (`core/agent/loop.rs`),
   and a collapsible reasoning UI (`reasoning-timeline.tsx`, `chain-of-thought`, with
   tests). Rebuilding them is duplication. The new piece is Project Manager Mode:
   Meedo-Me's memory in the model's context.
5. **"True open source only"** is read as priority, not purge: the Claude and Gemini
   critics stay (an explicit earlier decision); Meedo-Me's own model runs locally.

### Deduction — architecture

Meedo-Me's memory is in Python here; its face is the Jan fork; OpenClaw, Ollama and
Claude Code all want the same memory. Building a context-stitching payload engine
inside Jan would tie the memory to one client. **An MCP server exposing Meedo-Me's
memory** — standup, recall, episodes, review, ledger report — is consumed natively by
Jan, by Claude Code, and by any MCP-capable agent, with Ollama as the local model.
One memory, several faces. Project Manager Mode in Jan becomes: connect to the
Meedo-Me MCP server.

### Engine anomalies, ranked (next experiments)

1. **Letterforms collapse to blobs on heavy degradation** (TRIALTA "ALTA"), where the
   sketch still shows the letters. Check with font matching on before diagnosing.
2. **Small same-colour elements are dropped** — GCM "Modification" lost its i-dots.
   The reviewer is colour-based and cannot see it; it needs an element-count check.
3. **Outlines lumpy, corners wrong** — GCM icon arcs notched, bar ends chamfered where
   the original is rounded. Target for ideality-driven primitive fitting.
4. **The trace shatters PROPAK** into colour blobs; the reconstruction is far better
   but not chosen when the trace produced a vector.
5. **Half the oracle headroom is unreachable** without a signal that ranks
   candidates within a pair where the trace produced a vector.

### Blocking decision (the user's)

The fork needs a durable home before any Jan code is written. Recommended: fork
`menloresearch/jan` to `briysce/meedo-me` on GitHub. Creating a repository under the
user's account is theirs to approve.


---

## 2026-09-25 — Cursor pickup after Claude Code

Session left off at `a333184` (Meedo-Me MCP) on `claude/relaxed-babbage-igbk0v`
([Claude session](https://claude.ai/code/session_01D7eJEVF3QVi48DE5eKDtgr)).
No local Claude Code transcript on this VM; branch tip + `.claude/thought_trace.md`
+ commit messages were the handoff.

### Done this cycle

1. Meedo standup: accepted `80ac95cb` (trialta__import_combo stuck at 0.5045).
2. **Anomaly #2 shipped:** `meedo_review._check_element_count` — connected-component
   count catches same-colour piece drops (i-dots) that brand-colour share misses.
   Tests: drop blocked, fringe-merge still passes. MCP relative-path test no longer
   depends on gitignored `clean/gcm.png` (synthesises under `qa_logos/`).
3. Seeded local `qa_logos/synthetic/clean/` from golden/customer for diagnostics
   (gitignored; not committed).
4. Trialta clean has 18 ink components; font corpus present (349 faces) — anomaly #1
   (letterform→blob under heavy degrade) still open for a font-match experiment.

### Still blocked on the user

Fork `menloresearch/jan` → `briysce/meedo-me` before any Jan app code. MCP server
is ready for that client.

### Next experiments (unchanged rank, #2 done)

1. Letterforms → blobs on heavy degrade (TRIALTA) — font-match first.
3. Outlines lumpy / wrong corners (GCM) — ideality-driven primitives.
4. Trace wins over better reconstruction on PROPAK — selection signal.
5. Oracle headroom — within-pair ranking when trace already produced a vector.


---

## 2026-09-25 — Anomaly #1 (Trialta letterforms)

### Observation
`match_glyphs` returned {} on clean Trialta despite Oswald matching TRI and ALTA
perfectly in isolation. One baseline run mixed gray TRI + green ALTA + icon scrap;
mean 0.807 < 0.86 floor.

### Deduction
Colour is the designer's partition of a wordmark. Runs must be colour-family
segregated before font agreement. Contiguous subsequences recover a correct
triple when an icon shares the baseline.

### Experiment
- `_colour_run_families` + hairline filter + `_best_run_match` subsequences.
- Clean Trialta now names **TRIALTA** (7 glyphs). Idealize SVG emits `data-glyph`.
- `LOGO_IDEALIZE=1` on `trialta__plate_halo`: composite **0.536 → 0.630**, palette
  **0.370 → 0.906**. import_combo still keeps the trace (ideal agreement 0.229
  below collapse floor; traced has a vector). downscale: ideal agrees more
  (0.849 vs 0.829) but `_prefer_reconstruction` correctly keeps the vectorized
  trace per the corpus-derived rule — within-pair ranking remains anomaly #5.

### Element-count refinement
Satellite check now ignores fragmented sketches (>4 compact crumbs) so JPEG
noise cannot block a reconstruction Meedo-Me should prefer.
