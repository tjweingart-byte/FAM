# Training Consuela and Sonic, next steps - the opening prompt for a branch

Paste the text below into a new session once the Phase 2 branch
(`claude/upbeat-allen-kv6633`) has been merged into Main. Delete this file
when C2, S3 and the two answer-key proposals are done.

**Alongside it (the owner), on production once the merge is deployed** - the
session's first piece needs none of this; its tuning pieces need it:

1. In Render, on the **fam** service's **Environment** page, set `AGENTS=1`
   and `HOUSEKEEPER=1`, and leave `HOUSEKEEPER_DRY_RUN` and
   `HOUSEKEEPER_DEDUPE_DRY_RUN` at `1`. Consuela then chooses, deduplicates
   and reports, but writes nothing (Haiku, about a cent per 8 articles).
2. After a few nights, in the service's **Shell** tab, run and save:
   `python tools/consuela_report.py --merges` and
   `python tools/consuela_dedupe_eval.py`.
3. When her choices look right, set `HOUSEKEEPER_DRY_RUN=0` so she files
   facts; after a few more nights, run and save
   `python tools/sonic_recall_eval.py`.

Paste those outputs into the session when you have them.

---

Continue training FAM's Consuela (the housekeeper) and Sonic (the retriever)
from `docs/AGENTS_TRAINING_PLAN.md`. C1 and S1 are built (PROBLEMS.md §407);
this branch does the **proposals for the two answer-key rules (C3, S4)**,
**the attribution case from §407**, and - once I paste production reports -
**C2** and **S3**. Don't build C3 or S4 themselves, C4-C6 or S2-S6 here.

Read first: `CLAUDE.md` (and `docs/claude/*.md` for any rule you touch),
`docs/AGENTS_TRAINING_PLAN.md`, `docs/FAM_AGENTS.md` (Housekeeper,
Retriever), the signed rule books in `docs/agent_rules/` (`every-agent.md`,
`consuela.md`, `sonic.md`, and `nando.md` - its rule 12 is the model for the
new rules), `docs/agent_rule_proposals/README.md` (how a proposal is signed),
and PROBLEMS.md §381-§387 and §396-§417 via `PROBLEMS_INDEX.md`. Check CI on
Main before starting.

The pieces, in order:

1. **The answer-key rules, as proposals.** Write
   `docs/agent_rule_proposals/consuela.md` and `sonic.md` - each the signed
   book plus one rule in the shape of `nando.md` rule 12: a stronger model I
   choose grades a sample of the agent's work in the background; its grades
   only teach and measure; they never change the archive or an episode. Add
   a test like `tests/test_consuela_proposal_398.py` that holds each
   proposal to exactly that difference and fails once signed until the
   proposal is deleted. `tests/test_consuela_proposal_398.py` (the Perplexity
   proposal, signed in §412) fails while any `consuela.md` proposal exists -
   it has done its job; retire it in the same change. Write the pending manifest for me to sign the way
   §412 did (the proposals put in place on a scratch copy, `--write`, the
   signed books restored), so I sign by downloading and uploading - no
   clone. Then wait for my signature.
2. **The attribution case (§407).** Haiku answers "unsure" to every grown
   case that adds ", according to reports", which is arguably right (a
   reported claim is not an asserted one). Ask me which I want: a neutral
   rewording that keeps the claim's certainty, or the label changed to
   "different"; build that.
3. **C2 - Consuela's thresholds, measured** - only once I paste
   `consuela_dedupe_eval.py` and `consuela_report.py --merges` from
   production. Whether `CANDIDATE_SIMILARITY` (0.80), `FIGURE_SIMILARITY`
   (0.90), `WINDOW_DAYS` (3) and the hedge list are right. Show me each
   proposed change as before and after - wrong merges, missed repeats,
   pairs sent to a model - and wait for my yes before changing a number.
   No wrong merge, ever: since §417 a figure is merged only word for word,
   and any way back to merging reworded figures must pass the swapped-score
   and rose/fell cases.
4. **S3 - Sonic's thresholds, measured** - only once I paste
   `sonic_recall_eval.py`. `ENOUGH` (2), `ROUTER_FLOOR` (0.55),
   `ARCHIVE_MIN_SIMILARITY` (0.45), how often the grace window is missed,
   how often the router agrees with C3P0. Same rule: before and after, my
   yes first; the two hard rules (nothing from inside the window, oldest
   first) stay at 0.

If I have not pasted reports by the time 1 and 2 are done, say so and stop
rather than tuning on made-up material.

How to work (my standing rules): plan first and ask before starting; ship
small pieces, each ending with the full `./dev.sh check`, a PROBLEMS.md
section and a short summary; ask before any new dependency; stop and ask
when docs and code disagree; never change a signed rule book's meaning and
never generate, hold or ask for its private key; everything stays off by
default, `AGENTS=0` changes nothing, and `tests/test_agents_off.py` keeps
passing and never skips; check other open branches for PROBLEMS section
numbers before numbering yours. The Anthropic key is `FAM_ANTHROPIC_KEY`:
pass it as `ANTHROPIC_API_KEY="$FAM_ANTHROPIC_KEY"` for one command. Leave
the preview to the other session. Develop and push only on the branch the
session assigns; no pull request unless I ask.
