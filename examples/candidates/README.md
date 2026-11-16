# Candidates: written, adopted, measured, taken back out (§621-§626)

**Nothing in this folder is shown to the writer.** The loaders read
`examples/*.txt` and `examples/pairs/*.txt` and nothing beneath them, and
`tests/test_example_candidates_626.py` fails if a candidate reaches the
writer's instructions.

## What is here

- `3-virus-and-bacteria.txt` - a three-minute example, the one comparison
  (`docs/story_shapes.md`, shape 10), with its worked plan in `plans/`.
- `pairs/*.txt` - six before-and-after pairs. Each BEFORE is a real script
  the writer (Haiku 5.5) wrote on 2026-11-16 from a packet
  `tools/editor_eval.py` froze; each AFTER was rewritten by hand in the
  session from the same packet and passed both of
  `tools/teaching_pairs.py`'s gates (§621 has the numbers).

## What happened to them

The owner adopted all seven (§622). Then they were measured (§625): 50
fixed questions, written with and without them, graded blind by Opus. On
the 44 questions no pair was made from:

- nothing improved past zero;
- **storytelling was marked lower with both in: -0.41 [-0.77, -0.05]**;
  the mean of six leaned down, 25 questions worse to 10 better;
- the example alone and the pairs alone each leaned slightly down.

So the owner took both back out (§626).

## Before moving anything from here into `examples/`

1. **Passing the gates is not a gain.** All six pairs passed the code's
   measures and the grader's mark, and the writer still wrote flatter with
   them in its instructions. The likely reason, not measured: the rewrites
   are short, plain recaps with one figure to a sentence - what the code
   rewards - and half of what a pair shows the writer is a draft going
   wrong.
2. **Measure first**, without moving anything, **in a new folder**:
   `python tools/editor_eval.py --freeze --dir ~/.fam/<new>`, then
   `--run --arms none,none_candidates --dir ...` writes each question with
   and without everything here, and `--compare none_candidates,none --dir
   ...` gives the paired table (§629). A folder from an earlier run holds
   scripts written from other instructions under the same names. Budget
   about six cents a script graded by Opus (§625), not the 2.5 cents an
   earlier run cost.
3. The example was not measured where it is meant to teach: at three
   minutes, and on searchFAM's low effort. That run has not been done.
4. A pair's facts were true of its packet on the day it was frozen.
