# Finishing Phase 1 - the opening prompt for the last item

The owner moved four Phase 1 items to their own branch (PROBLEMS.md §390).
Three are done on `claude/pensive-brown-x9nsp8` (§418-§423): facts never
advice, asking which reading of a shared name, and the re-file of episodes
written before Nando. One is left. Paste the text below into a new session
once that branch has been merged into Main. Delete this file when it is done.

**Before you start (the owner):** this item needs the cloud environment to
reach `en.wikipedia.org`, `www.wikidata.org` and `query.wikidata.org`
(allowed in the environment's network settings on 2026-11-03; a session
started before that cannot reach them). Without them the session can build
and test on fakes, and will say what it could not measure.

---

Finish Phase 1 of FAM's agents: **public figures only** (decided item 16, my
answer 4a; c3p0.md never 6; every-agent.md never 9). Everything else in
Phase 1 is done (PROBLEMS.md §418-§423).

Read first: `CLAUDE.md` (and `docs/claude/*.md` for any rule you touch),
`docs/AGENTS_BUILD_PROMPT.md` (Phase 1, HOW TO WORK, the decided items),
`docs/FAM_AGENTS.md` (C3P0's tab), the signed rule books in
`docs/agent_rules/` (`every-agent.md`, `c3p0.md`), and PROBLEMS.md §369-§392
and §418-§423 via `PROBLEMS_INDEX.md`. Check CI on Main before starting.

The item. C3P0 already sets `private_person` (`agents/contextualizer.py`,
`agents/handoffs.py`); nothing acts on it. A request **about** a named person
is researched only when that person is public: known to Wikipedia or
Wikidata, to a provider's catalogue, **or named in reporting in a public
role** (an official, a coach, a business owner quoted in the news - my rule,
2026-11-03). A name none of these know gets no episode and a plain
explanation, logged and counted. It stops before any plugin or Sonic, as the
ambiguous-name stop does (§419: `_settle_reading` in `ScriptGenerator.prepare`
is the pattern), and nothing is charged. **Only the request's subject is
gated:** a name that merely appears inside an article on a public topic is
said as the reporting says it (the writer's rule since §422: private people
named only as the reporting names them; a child accused of a crime, never).
A lookup that fails or times out establishes nothing about the person
([missed-not-absent]; every-agent.md always 1): the episode goes ahead and
the failure is recorded. With the agents off nothing changes. If the three
hosts are refused from the container, say so and ask me - don't work around
it.

How to work (my standing rules): plan first and ask before starting; ship
small pieces, each ending with the full `./dev.sh check`, a PROBLEMS.md
section and a short summary; ask before any new dependency; stop and ask
when docs and code disagree; never change a signed rule book's meaning and
never generate, hold or ask for its private key - if a rule must change,
write the proposed file under `docs/agent_rule_proposals/` and wait for me
to sign it; everything stays off by default (`AGENTS`, `CONTEXTUALIZER`),
`AGENTS=0` changes nothing, and the byte-identical episode test in
`tests/test_agents_off.py` must keep passing and never skip; check other
open branches for PROBLEMS section numbers before numbering yours. Measure
with a key where you can: the Anthropic key is `FAM_ANTHROPIC_KEY`, so pass
it as `ANTHROPIC_API_KEY="$FAM_ANTHROPIC_KEY"` for one command. Leave the
preview to the other session. Develop and push only on the branch the
session assigns; no pull request unless I ask.
