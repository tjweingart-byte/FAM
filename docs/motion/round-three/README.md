# Motion, round three: the owner's picks (2026-11-16)

Every animation in the app was reviewed against the `charles-ux-animation`
skill (`.claude/skills/charles-ux-animation/`), page by page, with three
options each drawn as a working phone. The owner picked one per animation in
the picker page (https://claude.ai/artifact/NzbBYrjj6hktWttEdG6h51).

One file per page: `dailyfam.json` (rails, `screen-myfam`), `myfam.json`
(mixes, `screen-playfam`), `search.json` (`screen-home`), `messages.json`,
`yourfam.json` (`screen-profile`) and `shared.json` (the `Motion` module and
everything every page shares). Each animation carries `picked` - the chosen
option's key - and every option's exact numbers (`spec`), recipe, trade-off
and reduced-motion behaviour, plus the review's findings and bugs.

88 picks. FAM's motion rules still hold for every one of them
(`docs/claude/constraints.md`, `rule:motion`): nothing waits on motion, every
movement is 450 ms or less, and search's five loading steps are unchanged.
