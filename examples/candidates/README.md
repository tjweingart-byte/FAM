# Candidates, waiting on the owner (§619)

**Nothing in this folder is shown to the writer.** The loaders read
`examples/*.txt` and `examples/pairs/*.txt` and nothing beneath them
(`script_generator.load_style_examples`, `load_teaching_pairs`), and
`tests/test_example_candidates_619.py` fails if a candidate reaches the
writer's instructions. So the cached block is byte for byte what it was.

They are here, and not in `~/.fam/`, because they were written in a cloud
session whose machine is thrown away: a candidate kept only there would be
lost before anyone read it.

## What is here

- `3-virus-and-bacteria.txt` - a **three-minute** example. The folder had
  1, 2, 4 and 5 minutes and nothing at 3, the first length past the
  two-minute band (`DEPTH_BANDS`: "understanding"). It is also the first
  **comparison** (`docs/story_shapes.md`, shape 10, "none - a gap"): it
  opens on the difference that decides it, gives each side in its own
  terms, and never says which the listener should choose. Settled facts
  only, as every example is.
- `pairs/*.txt` - **before-and-after pairs** in `examples/pairs/`'s format.
  Each BEFORE is a real script the production writer (Haiku 5.5) wrote from
  a packet `tools/editor_eval.py` froze; each AFTER is that episode
  rewritten from the **same packet**, saying only what the packet holds.
  The rewrites were written by hand in the session, by the model running
  it, in place of `tools/teaching_pairs.py`'s Opus call, and then put
  through that tool's own gates: fewer flaws by the code's measures, no
  story or ear score lower, and the Opus grader's mean of six at least
  0.3 higher with no more unsupported claims (all six passed; the numbers
  are in PROBLEMS.md §619). The WHY line is built by the tool from the
  flaws the rewrite fixed; three also name a flaw found by reading that
  the code did not flag (a recap ending, a thing said twice).

  A pair's facts were true of its packet on the day it was frozen and are
  not kept current - the writer is told to learn what changed, never to
  borrow a pair's facts or topics.

## To adopt one

Read it aloud first; the house voice is the owner's ear, not a score.

- **The example:** move it to `examples/`, name its facet in
  `examples/facets.json` (`health`), and draft its worked plan with
  `python tools/editor_teaching.py --draft 3-virus-and-bacteria`. The
  README one level up says to prefer replacing a weak example to adding
  another; this one adds a length and a shape the folder does not have.
- **A pair:** move it to `examples/pairs/`. The writer is shown at most six
  (`MAX_TEACHING_PAIRS`), in file-name order, so choose which six.

Either move changes the writer's cached instructions, so it is a change to
what every episode is written from: run `tools/editor_eval.py` on the
frozen questions before and after if the difference should be measured
rather than heard.
