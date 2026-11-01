# Finishing Phase 1 - the opening prompt for the next branch

The owner moved four Phase 1 items to the next branch (PROBLEMS.md §390).
Paste the text below into a new session once `claude/modest-babbage-iu0z8h`
has been merged into Main. Delete this file when Phase 1 is finished.

---

Finish Phase 1 of FAM's agents ("C3P0 and Nando at the request"). The
previous branch built C3P0's research plan, Nando's one-category filing and
Nando's training (PROBLEMS.md §369-§389). Four Phase 1 items remain; they
are listed in PROBLEMS.md §390 and in `docs/AGENTS_BUILD_PROMPT.md` under
"Phase 1 - C3P0 and Nando at the request".

Read first: `CLAUDE.md` (and `docs/claude/*.md` for any rule you touch),
`docs/AGENTS_BUILD_PROMPT.md` (Phase 1, HOW TO WORK, the decided items),
`docs/FAM_AGENTS.md`, the signed rule books in `docs/agent_rules/`
(`every-agent.md`, `c3p0.md`, `nando.md`), and PROBLEMS.md §369-§390 via
`PROBLEMS_INDEX.md`.

The four items, one piece each, in this order (the owner may reorder):

1. **Facts, never advice** (decided item 17). C3P0 already sets
   `advice_request` (`agents/contextualizer.py`, `agents/handoffs.py`), but
   nothing acts on it. Make the writer plan the facts around an advice
   request, never a recommendation (every-agent.md, never 10).
2. **Ask the listener when a name is ambiguous** (decided item 15). An
   ambiguous brief returns the readings before any plugin is called: `/api`
   first, then the search and voice-search screens in `static/index.html`.
   The owner's answer: the transport is `/api/progress` plus a `reading=`
   parameter. The chosen reading resumes the request; old clients get the
   likeliest reading from context (keep every installed client working,
   [old-clients]). Tiles and editions use context, else stay unwritten. This
   also settles §377's open question: Nando's own call
   (`agents/categorizer.ask_tie`) picks one reading of an undecided name,
   and the build prompt says "never a guess".
3. **Public figures only** (decided item 16, the owner's answer 4a). A named
   person unknown to Wikipedia, Wikidata or a provider's catalogue gets no
   episode and a plain explanation, logged. C3P0 already sets
   `private_person`; nothing acts on it. Needs `en.wikipedia.org`,
   `www.wikidata.org` and `query.wikidata.org` allowed in the environment's
   network settings; if they are refused, say so and ask the owner.
4. **Re-file old episodes** (decided item 20; nando.md always 10). A dry run
   listing every category change Nando would make to episodes written before
   him, for the owner's review; applied only after the owner says yes.

Also: the before-and-after measurement of the two problems Nando exists to
fix (pictures that do not match titles; category pages with unrelated
episodes; `python tools/categories_report.py --audit`) needs real episodes -
ask the owner how to run it on staging.

How to work (the owner's standing rules): plan first and ask before
starting; ship small pieces, each ending with the full `./dev.sh check`, a
PROBLEMS.md section and a short summary ([ship-loop]); ask before any new
dependency; stop and ask when docs and code disagree; never change a signed
rule book's meaning and never generate, hold or ask for its private key - if
a rule must change, write the proposed file under
`docs/agent_rule_proposals/` and wait for the owner to sign it; everything
stays off by default (`AGENTS`, `CONTEXTUALIZER`, `CATEGORIZER`),
`AGENTS=0` changes nothing, and the byte-identical episode test in
`tests/test_agents_off.py` must keep passing and never skip; check other
open branches for PROBLEMS section numbers before numbering yours (§386).
Measure with a key where you can: the environment's Anthropic key is named
`FAM_ANTHROPIC_KEY`, so pass it as `ANTHROPIC_API_KEY="$FAM_ANTHROPIC_KEY"`
for one command, and any tool making several model calls keeps one event
loop (§388). Develop on the branch the session assigns; no pull request
unless the owner asks.
