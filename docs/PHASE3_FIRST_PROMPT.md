# Phase 3 - the opening prompt (Shakespeare and Mclovin in the writing)

Phase 0 (the framework), Phase 1 (C3P0 and Nando at the request, finished in
PROBLEMS.md §423-§429) and Phase 2 (Consuela and Sonic, §393-§417) are done,
and the provider change that had to wait for them (API-Sports and Finnhub
removed, §418) is merged. Paste the text below into a new session once the
Phase 1 branch (`claude/pensive-brown-x9nsp8`) is merged into Main. Delete
this file when Phase 3 is finished.

**Before you start (the owner):** two decisions are still yours
(`docs/AGENTS_BUILD_PROMPT.md`, items 34 and 37). The session plans around
them and asks; it builds nothing that depends on either until you answer.
Phase 3's checks are measured with the Anthropic key, and the grounding
checker may need a machine of its own - the session will say what it needs
before asking for it.

---

Start Phase 3 of FAM's agents: **Shakespeare and Mclovin in the writing**
(`docs/AGENTS_BUILD_PROMPT.md`, "Phase 3 - Shakespeare and Mclovin in the
writing").

Read first: `CLAUDE.md` (and `docs/claude/*.md` for any rule you touch -
the writing rules in `product.md` above all), `docs/AGENTS_BUILD_PROMPT.md`
(Phase 3, HOW TO WORK, the decided items - 6, 12, 13, 14, 17, 26, 28, 29, 30,
32, 34 and 37 are Phase 3's), `docs/FAM_AGENTS.md` (Shakespeare's and
Mclovin's tabs, Models decided), the signed rule books in `docs/agent_rules/`
(`every-agent.md`, `shakespeare.md`, `mclovin.md`), and PROBLEMS.md §309
(episode quality), §346 (the models), §369-§392, §393-§417 and §423-§429 via
`PROBLEMS_INDEX.md`. Check CI on Main before starting.

The work, in the build prompt's order (I may reorder):

1. **Mclovin's source screen** (decided items 6 and 13): opinion pieces and
   blogs dropped from both packets inside `research.screen_results` - URL
   path, title label, a provider's page type, blogging hosts - on every rung,
   the second look and Sonic's packet; standing aside only for C3P0's
   `opinion_requested`, and only for that person's pieces; official blogs and
   "analysis" kept; drops logged and counted; a thin packet gets the existing
   retry, an empty one is a missed search, never a refusal. A small model on
   unsettled pages ahead of the tap only. This is pure code first - start
   here.
2. **Shakespeare** (decided items 28, 29, 30, 37): an `Outline` (extractive
   summary by sentence number, beats, which packet carries each, opening and
   landing, `thin`), numbers checked in code, the text shown to the writer
   from the sources; `docs/journalism_playbook.md` (`EDITOR_PLAYBOOK`) and
   `examples/` in his cached prompt; on searchFAM in tandem with the writer,
   never a step after it. Ahead of the tap his model is **my decision after
   the comparison** in item 37 - build the eval (about 50 frozen edition
   questions, no editor / Haiku 5.5 high / Sonnet 5.5, scored on
   `episode_quality`'s six code measures, Mclovin's error counts once he
   exists, a blind Opus grader, cost and time), run it with the key, and
   bring me the numbers. Sonnet is the default until I decide.
3. **Mclovin while the writer writes** (decided items 12, 14, 17, 26, 32):
   a per-sentence hook in `_ScriptReader` beside the existing guards -
   quotes (span found in the packets, speaker matched; this is also the
   quote-length guardrail §427 left open), figures (`figure_guard` widened
   carefully, measured on audits first, §269), dates, mixed stories across
   `news_clusters`, advice (an advising sentence fails - §423's
   `advice_audit` counts today and is the starting point), and support from
   a grounding checker. A failing sentence goes back to the writer once; a
   second failure drops it; a check past `SUPERVISOR_TIMEOUT_SECONDS` lets
   the sentence play unchecked, recorded. No whole-script model.
4. **The grounding checker - ask me before building it** (item 34): analyse,
   on its own, whether it can run inside the current pipeline without a new
   machine (Vectara's HHEM-2.1-Open on a CPU first; never on the voice pod),
   and how a rejected sentence avoids delaying the writer. Measure the first
   chunk's added wait on searchFAM with `tools/stall_probe.py` and
   `tools/compare_search.py` (the spec is about a second to the first word)
   and bring me the analysis and the numbers before any new dependency or
   machine.

How to work (my standing rules): plan first and ask before starting; ship
small pieces, each ending with the full `./dev.sh check`, a PROBLEMS.md
section and a short summary; ask before any new dependency or machine; stop
and ask when docs and code disagree; never change a signed rule book's
meaning and never generate, hold or ask for its private key - if a rule must
change, write the proposed file under `docs/agent_rule_proposals/` and wait
for me to sign it; everything stays off by default (`AGENTS`,
`EDITOR_AGENT`, `SUPERVISOR`), `AGENTS=0` changes nothing, and the
byte-identical episode test in `tests/test_agents_off.py` must keep passing
and never skip; the writer's prompt stays under its length guard
(`test_the_prompt_stays_lean`) - dedup before adding, and prefer an example
over a rule; check other open branches for PROBLEMS section numbers before
numbering yours, and merge Main before the pull request (Main took this
branch's numbers once already, §418). Measure with a key where you can: the
Anthropic key is `FAM_ANTHROPIC_KEY`, so pass it as
`ANTHROPIC_API_KEY="$FAM_ANTHROPIC_KEY"` for one command, and any tool making
several model calls keeps one event loop (§388). Leave the preview to the
other session. Develop and push only on the branch the session assigns; no
pull request unless I ask. If `pip install` upgrades Playwright past the
preinstalled browser (`/opt/pw-browsers`), the smoke runs cannot launch it:
install the Playwright version whose `browsers.json` names that browser's
revision (the Phase 1 session lost a check run to this).
