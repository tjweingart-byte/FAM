# Finishing Phase 1 - the opening prompt for the next branch

The owner moved four Phase 1 items to their own branch (PROBLEMS.md §390).
Paste the text below into a new session once the Phase 2 branch
(`claude/upbeat-allen-kv6633`) has been merged into Main. Delete this file
when Phase 1 is finished.

**Before you start (the owner):** the public-figures item needs the cloud
environment to reach `en.wikipedia.org`, `www.wikidata.org` and
`query.wikidata.org` - in the environment's network settings, allow those
three hosts (the Phase 2 session's container was refused them). Without it
the session can still build and test on fakes, and will say what it could
not measure.

---

Finish Phase 1 of FAM's agents ("C3P0 and Nando at the request"). The Phase 1
branch built C3P0's research plan and Nando's filing and training
(PROBLEMS.md §369-§392); Phase 2 then built Consuela, Sonic, the category tag
everywhere and the activation step (§393-§416). Four Phase 1 items remain,
listed in §390 and in `docs/AGENTS_BUILD_PROMPT.md` under "Phase 1 - C3P0 and
Nando at the request".

Read first: `CLAUDE.md` (and `docs/claude/*.md` for any rule you touch),
`docs/AGENTS_BUILD_PROMPT.md` (Phase 1, HOW TO WORK, the decided items),
`docs/FAM_AGENTS.md`, the signed rule books in `docs/agent_rules/`
(`every-agent.md`, `c3p0.md`, `nando.md`), and PROBLEMS.md §369-§392 and
§403-§404 via `PROBLEMS_INDEX.md`. Check CI on Main before starting.

The four items, one piece each, in this order (I may reorder):

1. **Facts, never advice** (decided item 17). C3P0 already sets
   `advice_request` (`agents/contextualizer.py`, `agents/handoffs.py`), but
   nothing acts on it. Make the writer plan the facts around an advice
   request, never a recommendation (every-agent.md, never 10).
2. **Ask the listener when a name is ambiguous** (decided item 15). An
   ambiguous brief returns the readings before any plugin - Sonic included
   (§403) - is called: `/api` first, then the search and voice-search
   screens in `static/index.html`. My answer: the transport is
   `/api/progress` plus a `reading=` parameter. The chosen reading resumes
   the request; old clients get the likeliest reading from context (keep
   every installed client working, [old-clients]). Tiles and editions use
   context, else stay unwritten. This also settles §377's open question:
   Nando's own call (`agents/categorizer.ask_tie`) picks one reading of an
   undecided name, and the build prompt says "never a guess".
3. **Public figures only** (decided item 16, my answer 4a). A named person
   unknown to Wikipedia, Wikidata or a provider's catalogue gets no episode
   and a plain explanation, logged. C3P0 already sets `private_person`;
   nothing acts on it. If the three Wikipedia/Wikidata hosts are refused
   from this container, say so and ask me - don't work around it.
4. **Re-file old episodes** (decided item 20; nando.md always 10). A dry run
   listing every category change Nando would make to episodes written before
   him, for my review; applied only after I say yes - and applied through
   `category_tag.retag` (§404), the one function that moves an episode's
   node on its script row, its R2 audio metadata and the archive together.

Also: the before-and-after measurement of the two problems Nando exists to
fix (pictures that do not match titles; category pages with unrelated
episodes; `python tools/categories_report.py --audit`) needs real episodes -
ask me how to run it on staging.

How to work (my standing rules): plan first and ask before starting; ship
small pieces, each ending with the full `./dev.sh check`, a PROBLEMS.md
section and a short summary; ask before any new dependency; stop and ask
when docs and code disagree; never change a signed rule book's meaning and
never generate, hold or ask for its private key - if a rule must change,
write the proposed file under `docs/agent_rule_proposals/` and wait for me
to sign it; everything stays off by default (`AGENTS`, `CONTEXTUALIZER`,
`CATEGORIZER`), `AGENTS=0` changes nothing, and the byte-identical episode
test in `tests/test_agents_off.py` must keep passing and never skip; check
other open branches for PROBLEMS section numbers before numbering yours.
Measure with a key where you can: the Anthropic key is `FAM_ANTHROPIC_KEY`,
so pass it as `ANTHROPIC_API_KEY="$FAM_ANTHROPIC_KEY"` for one command, and
any tool making several model calls keeps one event loop (§388). Leave the
preview to the other session. Develop and push only on the branch the
session assigns; no pull request unless I ask.
