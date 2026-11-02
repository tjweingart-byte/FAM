# Training Consuela and Sonic, next steps - the opening prompt for a branch

Paste the text below into a new Claude Code session once Consuela has run on
a deployment long enough to have material (the walkthrough in PROBLEMS.md
§408 says how), and paste the outputs it asks for with it. Delete this file
when C2, S3 and the two answer-key proposals are done.

---

Continue training FAM's Consuela (the housekeeper) and Sonic (the retriever)
from `docs/AGENTS_TRAINING_PLAN.md`. C1 and S1 are built (PROBLEMS.md §407);
this branch does **C2**, **S3**, and the **proposals for C3 and S4's
answer-key rules**. Don't build C3/S4 themselves, C4-C6 or S2-S6 here.

Read first: `CLAUDE.md` (and `docs/claude/*.md` for any rule you touch),
`docs/AGENTS_TRAINING_PLAN.md`, `docs/FAM_AGENTS.md` (Housekeeper,
Retriever), the signed rule books in `docs/agent_rules/` (`every-agent.md`,
`consuela.md`, `sonic.md`, `nando.md` - rule 12 is the model for the new
rules), and PROBLEMS.md §381-§387 and §396-§408 via `PROBLEMS_INDEX.md`.

What I am pasting below this prompt, from the deployment (Render shell):
- `python tools/consuela_dedupe_eval.py` - her dedupe eval, core and grown;
- `python tools/consuela_report.py --merges` - her dry-run merges,
  conflicts and disputed pairs;
- `python tools/sonic_recall_eval.py` - Sonic on the filed facts (only if
  `HOUSEKEEPER_DRY_RUN=0` has run long enough to file some);
- and my answers to the decisions listed at the end of §408.

The pieces, in order:

1. **C2 - Consuela's thresholds, measured.** From the eval's details and the
   merges report: whether the dedupe's cut-offs (`CANDIDATE_SIMILARITY`
   0.80, `FIGURE_SIMILARITY` 0.90, `WINDOW_DAYS` 3) and the hedge list are
   right; and the open label question from §407 - ", according to reports"
   is answered "unsure" by Haiku every time: change that grown case to a
   neutral rewording, or change its label. Show me each proposed change as
   before and after (wrong merges, missed repeats, pairs sent to a model) and
   wait for my yes before changing a number.
2. **S3 - Sonic's thresholds, measured.** From the recall eval: `ENOUGH`
   (2), `ROUTER_FLOOR` (0.55), `ARCHIVE_MIN_SIMILARITY` (0.45), how often the
   grace window is missed, and how often the router agrees with C3P0. Same
   rule: before and after, my yes first. The two hard rules (no leaks, oldest
   first) must stay at 0.
3. **The answer-key rules, as proposals only.** Write
   `docs/agent_rule_proposals/consuela.md` and `sonic.md` - each the signed
   book plus one rule in the shape of `nando.md` rule 12 (a stronger model
   chosen by me grades a sample of the agent's work in the background; its
   grades only teach and measure; they never change the archive or an
   episode). If `consuela.md` still has the Perplexity proposal waiting,
   build on it rather than replacing it. Add a test like
   `tests/test_teaching_label_384.py`'s that holds each proposal to exactly
   that difference. Then stop: I sign them.

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
