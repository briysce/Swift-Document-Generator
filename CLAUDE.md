# Swift Document Generator — working rules

## Start every work cycle with Meedo-Me

Meedo-Me is this project's manager: its memory, its reviewer, and its advisor.
Its advice is only worth anything if someone reads it and decides.

```
python -m tools.logo_vectorizer.meedo_ledger standup
python -m tools.logo_vectorizer.meedo_ledger decide --by <you> <id> accept|reject "<reason>"
```

Decide every proposal in the standup before starting new work. Accept only what
you will act on now — accepted advice is judged by later runs, so accepting and
then ignoring it scores Meedo-Me's advice as a failure it did not earn. Reject
with a real reason; Meedo-Me learns from the pattern of what is rejected.

Its advice once sat unread for a whole session. Twenty-eight proposals, all
ignored, were scored as failures and its hit rate read 0% — while its most
repeated one was a correct diagnosis of the next problem to fix.

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
