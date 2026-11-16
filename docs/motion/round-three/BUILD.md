# Building round three

One phase per file, in this order: `shared.json`, then `dailyfam.json`,
`myfam.json`, `search.json`, `messages.json`, `yourfam.json`. For each
animation, build the option named by `picked` - exactly its `spec`, `recipe`
and `reduced` - and fix the review's `findings` and `bugs` that the picked
option depends on. The skill (`.claude/skills/charles-ux-animation/`) decides
anything the spec leaves open.

Rules for every phase:

- Read `CLAUDE.md`, then `rule:motion` in `docs/claude/constraints.md` and
  PROBLEMS.md §292 and §298 before touching `Motion` (`static/index.html`).
- Motion never holds anything; every movement is 450 ms or less; search's five
  loading steps keep their timing; the change is made first and the movement
  drawn over it (inert copies leave).
- Curves and durations come from the named tokens the `shared` phase adds
  (`tokens: A`); no bare numbers or `ease-out` keywords in new motion.
- Where a pick changes a written rule (e.g. reduced motion all off, the tab
  nudge, the press), update the rule's text in `docs/claude/constraints.md`
  with a `> **Current:**` note, as the project does, and its one-line summary
  in CLAUDE.md if that summary becomes wrong.
- Ship loop (`docs/claude/workflow.md`): `./dev.sh check` passes in full; a
  PROBLEMS.md section for the phase (then `python tools/problems_index.py`);
  `python preview/build_live_preview.py`; commit to the session branch.
  The preview republish is done by the lead.
- Add or extend smoke checks in `tools/smoke_preview.py` for what you build
  where the existing motion checks show the pattern (they assert nothing is
  held and nothing is left behind).
