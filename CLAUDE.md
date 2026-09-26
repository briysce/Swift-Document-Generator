# Swift Document Generator — working rules

## Start every work cycle with Meedo-Me

Meedo-Me is this project's **manager and assistant**: its memory, its reviewer,
its advisor, and the place experiments are judged. Keep it heavily involved so
it can learn, grow, adapt, adjust, and experiment with us — not as a sidecar
that is only consulted after the fact.

```
python -m tools.logo_vectorizer.meedo_cycle          # ledger + journal + snapshot
python -m tools.logo_vectorizer.meedo_ledger decide --by <you> <id> accept|reject "<reason>"
python -m tools.logo_vectorizer.meedo_episodes recall "<problem>"
python -m tools.logo_vectorizer.meedo_journal log --agent <claude|cursor> --task N \
  --kind claim|finding|handoff|done|blocked --summary "…" --images-looked-at
# …do the work, with meedo_review on logo outputs…
python -m tools.logo_vectorizer.meedo_ledger observe   # usually automatic from the improve loop
python -m tools.logo_vectorizer.meedo_ledger propose
python -m tools.logo_vectorizer.meedo_ledger report
```

Decide every proposal in the standup before starting new work. Accept only what
you will act on now — accepted advice is judged by later runs, so accepting and
then ignoring it scores Meedo-Me's advice as a failure it did not earn. Reject
with a real reason; Meedo-Me learns from the pattern of what is rejected.

Log **every** work unit into the journal (Claude Code and Cursor). A `done`
without tests / images-looked-at / measured-vs-previous (for engine tasks) is
flagged on the next journal standup. Episodes still hold the durable *method*;
the journal holds who did what, when, and with what evidence.

**Refine Meedo-Me as you go.** Claude Code and Cursor both own Meedo-Me's
product quality: when standup/recall/review/journal/MCP/desktop wiring fails or
feels dumb, fix it in-session (board #3 / workstream `meedo-me`). The goal is a
smarter utility deeply integrated across our projects via one MCP memory — not a
sidecar that only gets attention on a dedicated ticket.

The same memory is served over MCP (`.mcp.json` registers it for Claude Code):
`meedo_standup`, `meedo_journal_standup`, `meedo_journal_recent`,
`meedo_recall`, `meedo_playbook`, `meedo_report`, `meedo_review`, and the writers
`meedo_decide`, `meedo_record_episode`, `meedo_journal_log`. Recall before
diagnosing — past episodes say what the first guess got wrong. Record the method
when a problem is solved. Any agent that reads untrusted input (email, chat, the
web) gets the server with `--read-only`, never the writers.

Its advice once sat unread for a whole session. Twenty-eight proposals, all
ignored, were scored as failures and its hit rate read 0% — while its most
repeated one was a correct diagnosis of the next problem to fix. That must not
happen again: **no open proposals left undecided at the end of a cycle.**

## The logo engine's goal

A raster logo is a sketch, never the target. The output is the vector a designer
would draw over it: straight lines straight, circles round, every element the
sketch shows present, the page and the blur not drawn. "Closer to the raster" is
not "better" — a blurry upscale is maximally faithful to a blurry source.

## Look at the images

`composite` has scored a logo with a deleted brand colour as the best result in
the corpus. Never report an improvement without looking at the output. Meedo-Me's
reviewer (`tools/logo_vectorizer/meedo_review.py`) blocks outputs that dropped an
element; a blocked output's score is not a result.

## Measuring

- Compare improve-loop runs only when `min_height` and `engines` match; both are
  recorded on every row. The historical baseline is at `--min-height 1200`.
- The default path must stay byte-identical when a change targets only the
  reconstruction path (`LOGO_IDEALIZE`); check it.
- An optimisation that should change nothing is verified by showing it changed
  nothing, not by argument.

## Never

- Commit the WhatsApp transcript or its photos (third-party private material) —
  only the derived style profile.
- Track Calibri (`fonts/Calibri*.ttf`, `mobile/assets/fonts/Calibri*.ttf`); it is
  proprietary. `scripts/sync_calibri_fonts.sh` falls back to Carlito.
- Track `tools/logo_vectorizer/.cache/` (font corpus, glyph store, memory store).
- Enable memory recall by default or loosen `MIN_SIMILARITY` (0.995) without
  re-measuring cross-brand false recalls. Emitting another company's logo is
  unrecoverable.
