# Phase 4 - the opening prompt (Keith Wong and `/admin`)

Phases 0-3 are done: the framework, C3P0 and Nando, Consuela and Sonic,
Shakespeare and Mclovin in the writing (PROBLEMS.md §434-§461; the last
pull request is [tjweingart-byte/FAM#224](https://github.com/tjweingart-byte/FAM/pull/224)).
Paste the text below the line into a new session once #224 is merged into
Main. Delete this file when Phase 4 is finished.

**Before you start (the owner).** The session plans first and asks before
building; these are what it will ask you for, so have them to hand:

1. **Read-only keys** for Keith's usage monitors, put where FAM reads secrets
   (`FAM_SECRETS`, never typed into a chat): the Render API, the MongoDB
   Atlas Admin API, and Cloudflare's analytics for R2. RunPod's key is
   already used by the voice supervisor.
2. **Your current plan on each service** - Render, Atlas, Cloudflare R2,
   RunPod, Anthropic and the data providers - so the limits the 80% and 95%
   warnings measure against are yours, not guesses.
3. **Urgent email**: `IT_ALERT_EMAIL=Admin@familiarize.net` on the `fam`
   service, and working sender credentials for `mail.py`.
4. **The cost per episode Keith compares against** (the Cost table in
   `docs/FAM_AGENTS.md` says ~$45 per 10,000 episodes plus ~$30 a month of
   nightly jobs) and **what "well above" means** before he emails you.
5. Later, as he proposes them: which of his reversible fixes he may make,
   and your yes or no on each admin change (a before-and-after) or new page
   (a slideshow).

---

Start Phase 4 of FAM's agents: **Keith Wong and `/admin`**
(`docs/AGENTS_BUILD_PROMPT.md`, "Phase 4 - Keith Wong and `/admin`"), starting
from Main, which now holds Phase 3 ([tjweingart-byte/FAM#224](https://github.com/tjweingart-byte/FAM/pull/224), merged).
If #224 is not on Main yet, tell me and stop.

**Read first:** `CLAUDE.md` (and `docs/claude/*.md` for any rule you touch);
`docs/AGENTS_BUILD_PROMPT.md` (Phase 4, "The work, in order", and decided
items 4, 7, 9, 10, 21, 23, 27, 32 and **42-46**, the Phase 3 decisions);
`docs/FAM_AGENTS.md` (Keith's tab, Shared rules, and the Cost section); the
signed rule books `docs/agent_rules/every-agent.md` and `keith.md`; and
PROBLEMS.md §351 (the Agents panel on `/admin`), §405 (Keith's tag check),
§289 and §310 (the admin page as it is) and §450-§461 via
`PROBLEMS_INDEX.md`. **Check CI on Main first.**

**My standing rules:**

* Plan first and ask me before starting.
* Ship small pieces, each ending with the full `./dev.sh check`, a PROBLEMS
  section and a short summary. Never edit files while `./dev.sh check` is
  running: a test reads source by line and fails if you do (§442).
* Ask before any new dependency or machine.
* Never change a signed rule book. Write proposals under
  `docs/agent_rule_proposals/` with a pending manifest, and walk me through
  signing.
* Everything stays off by default, and `tests/test_agents_off.py` must keep
  passing.
* Leave `preview/` to the other session.
* Check other open branches for PROBLEMS numbers before numbering yours (and
  again before the pull request), and merge Main into your branch before
  telling me it is ready.
* No pull request unless I ask. When I do, it goes straight to Main, not
  through staging.
* API keys are for what I ask, not tests at will (decided item 46): spend
  only on a measurement I have agreed to, and say what it costs first. Use
  the Anthropic key as `ANTHROPIC_API_KEY="$FAM_ANTHROPIC_KEY"`.
* Postgres 16 is installed; test any new store against it too (`service
  postgresql start`, then
  `DATABASE_URL=postgresql://postgres:fam@localhost:5432/fam_test`; if the
  password is refused after a container restart, set it over the local
  socket).
* In this container, `pip install playwright==1.63.0` - a newer one cannot
  find the installed Chromium and every smoke run fails (§450).

**Settled in Phase 3 - do not reopen** (decided items 42-46):

* **Shakespeare** is Haiku 5.5 at high effort ahead of the tap, and works
  **beside the writer, never in its place**; his rule book does not change.
  **He plans in tandem on every episode a listener waits on** - typed
  searches, tiles scripted on the tap, Go Deeper - at low effort
  (`EDITOR_TANDEM`, §456), never in front of the first word. **Do not
  measure the tandem on real searches.** His two weeks with and without him
  run behind `EDITOR_TRIAL` (§454), read with `python tools/editor_trial.py`.
* **Support** ("does the source say this?") is **Mclovin's job, never an
  outside checker's**; **no grounding checker is installed and `support`
  stays off until I revisit it.** Do not build, install, measure or propose
  a checker in this phase. Figures stay count only.
* Mclovin's per-sentence limits are 2 s and 500 tokens.
* `MONGODB_URI` is the archive's read-write user (FAM and Consuela write
  with it); `MONGODB_READ_URI` is Sonic's separate read-only user
  (`ARCHIVE.md` step 7). Both matter only once `ARCHIVE_BACKEND=atlas`.

**The work, in the build prompt's order (I may reorder):**

1. **Keith's first fixes are in the error and usage tracking** (decided item
   7, keith.md "always" 12): `pipeline_errors`, `provider_usage`, their
   flushes and stores, missing limits, spend in `metering` not counted as
   usage. Audit first and bring me the list before changing anything.
2. **Usage monitors** beside `provider_usage` and `metering`: the Render
   API, the RunPod API (already used by `voice_control`), the MongoDB Atlas
   Admin API and Cloudflare's GraphQL analytics for R2 - read-only keys
   through `FAM_SECRETS`; my plan limits copied beside them; a warning at
   80% and urgent at 95% or a projected overrun, with the next plan's price
   (`docs/SCALING_TIMELINE.md`). Tell me exactly which keys and plan figures
   you need before building against them.
3. **Incidents**: one per cause, from `pipeline_errors` (`explain()`),
   `/api/health`, the background loops' last runs, the voice supervisor and
   listener reports; a closed list of reversible fixes (re-verify the voice
   ladder, re-run a failed job once, hold a background sweep until a limit
   resets, ask Netenyahu to hold an agent), each verified by a real call
   and **observe-only until I switch each on**; a fix report answerable at
   any moment; escalations with a solution plan; **the pop-up on every
   admin page** with all open alerts (decided item 7); **urgent alerts
   emailed** through `mail.py` to `IT_ALERT_EMAIL` (items 10 and 23).
4. **Agent conformance checks** written from each signed rule book and run
   on samples: Sonic never wrote; Consuela's deletions are within grace;
   Mclovin's verdicts are among his named failures; C3P0's inputs carry no
   results; Nando's nodes exist. Plus what Phase 3 left Keith to watch:
   * **Mclovin's unchecked sentences** (a check out of time plays its
     sentence, decided item 32): counted, and an alert when the count
     rises (keith.md "always" 7).
   * **Shakespeare's tandem outcomes** (`ScriptNotes.editor_tandem`:
     handed_over, late, no_plan, second_failed, over_budget, short; counted
     in his health block as `tandem_*`): an alert when "late" or
     "second_failed" rises. Watching only - never a measurement that spends.
   * **The two weeks' progress** (`episode_quality.editor_trial`): episodes
     and listens per arm, so I know when there is enough to read.
5. **Cost per episode on `/admin`** (decided items 21 and 27): what the
   agents cost per episode from `metering`, against the plan I give you;
   an email when it runs well above it; no cap ever stops an agent.
6. **`/admin` as Keith's** (decided items 4 and 9): an improvement to an
   existing admin page is shown to me as a before-and-after; a new page or
   feature is a slideshow first; **nothing ships without my yes**, then
   through FAM's normal checks, reversible and logged. Likely candidates to
   propose: the trial's table, the tandem outcomes, Mclovin's unchecked
   count and the cost-per-episode figure - each a proposal, not a build.
7. **Turning the agents on in production** stays my action in the Render
   dashboard. Keep the list of variables per agent, what each turns on and
   what it costs (PROBLEMS.md §461 has the current one) true to the code as
   Phase 4 changes it, and change nothing in `render.yaml` without my yes.

When all of this is done, tell me Phase 4 is done, summarise what is left
for me to switch on or decide, and what comes after Phase 4.
