# Prompt for a new session: make FAM's agents buildable

Paste everything below the line into a new Claude Code session on the
`tjweingart-byte/FAM` repository.

---

You are picking up FAM, an app that turns what people care about into short
generated audio episodes. Over several sessions the owner designed eight
**agents**, each with a name, a job and a rule book. None is built. Your job
in this session is the **backend groundwork that makes them buildable**, done
in the order below, behind switches that are off by default, without
changing what a listener hears until the owner turns something on.

## Read first, in this order

1. `CLAUDE.md` (the core rules) and `docs/claude/workflow.md` (how to ship).
   Every rule there still holds; several items below touch settled rules and
   say so.
2. `docs/FAM_AGENTS.md` - the agents guide, one `##` per agent: what each
   does, its guardrails, how to prompt and test it, its plugin choice. Also
   readable as a page: `python docs/build_agents_html.py`.
3. `docs/agent_rules/` - each agent's rule book in plain English. These are
   **drafts**; the owner keeps the governing copies outside Claude. Never
   edit the meaning of a rule book; if code cannot honour a rule, stop and
   say so.
4. `PROBLEMS.md` §307-§318 (use `PROBLEMS_INDEX.md` for line numbers): the
   history archive, its agents, and the owner's directions.
5. The code the agents sit on: `pipeline.py`, `episode_intelligence.py`
   (the brief, `category_path`, `title`), `research.py`
   (`screen_results`, `retrieve`, `finish_history_look`),
   `script_generator.py` (`stream_prepared`, `_ScriptReader` and its guards),
   `figure_guard.py`, `score_guard.py`, `evidence_digest.py`,
   `history_archive.py` (intake, facts, recall, Atlas and SQLite backends),
   `categories.py`, `category_near.py`, `category_audit.py`,
   `thumbnails.py`, `stories.py` (`resolve_category`, `writer_vocabulary`),
   `app.py` (`_episode_category`, `/admin`), `provider_usage.py`,
   `pipeline_errors.py`, `metering.py`, `spend_guard.py`, `audio_store.py`
   (Cloudflare R2), `voice_control.py` (RunPod), `claude_batch.py`.

## The agents, in one line each

| Agent | Name | Job |
|---|---|---|
| Overlord | Netenyahu | Directs every agent: when to work, what to do; knows their limits |
| Contextualizer | C3P0 | With EI: tells same-name things apart, picks plugins and their inputs, tells Sonic what to fetch, writes the title |
| Categorizer | Nando | With EI and C3P0: pins each request to one tree node; confirms after writing; tells Consuela and Sonic |
| Retriever | Sonic | Reads MongoDB only; returns one date-ordered history packet |
| Editor in chief | Shakespeare | With the writer: extractive summary of both packets, outline, a journalist's open, flow and close |
| Script supervisor | Mclovin | Screens opinion and blogs from both packets; checks each sentence while the writer writes - quotes, figures, facts, dates, mixed stories - against the two packets only |
| Housekeeper | Consuela | MongoDB's only writer: consolidates each packet as it enters, files it by node, dedupes, consolidates again at 90 days, keeps all sports and market data, keeps aggregate demographic signals |
| IT guy | Keith Wong | Watches errors, usage against every plan, and the agents; fixes what it may silently with a report; plans for the rest; owns `/admin`'s content |

Names are internal: never shown to a listener.

## Already decided by the owner (2026-10-28)

1. **C3P0 writes the episode title.** Its title is the episode's title.
   Mclovin compares it with the finished script; on a mismatch C3P0 retitles
   from the finished script. When this is built, rewrite the
   `[title-from-content]` rule in `docs/claude/product.md` and its
   `CLAUDE.md` line (the `tests/test_claude_md.py` budget applies), keeping
   what still holds: a listener's or bank tile's own title
   (`titleOverridden`) is never replaced; tiles claim no results.
2. **Demographics are aggregate only, in groups of at least 100.**
   Consuela keeps what is searched and heard by category, region and time,
   and how it correlates with age (in bands), gender and location - only as
   totals and correlations for groups of **100 or more**, never names,
   account ids or a row per person, never guests or private searches.
   **Date of birth, location and gender are asked when a listener is let off
   the waitlist and into the app** (an activation step; accounts already
   store `birth_date` and a place, not gender). The Privacy Policy and
   consent (`consent.py`) must cover them first; App Store guideline 5.1.1
   limits requiring data an app does not need - see the open questions.
3. **No full packets in MongoDB.** Consuela consolidates every packet **the
   moment it enters**: key sentences, figures, quotes and facts, chosen
   (never rewritten), each with its article link, date and grade. **After
   90 days** it consolidates again to important figures, quotes, trends
   (kept as the dated series) and article links. Sports and market records
   are never consolidated away.
4. **Keith and `/admin`.** Keith may **improve any existing admin page**; a
   **new page or feature is presented to the owner as a slideshow first**
   (what it looks like, what it does for them) and built only on a yes.
5. **One category tag, everywhere, for the episode's whole life.** Nando's
   node id travels with the episode: its script row, its audio's metadata in
   Cloudflare R2, every MongoDB record from it, every app surface (tile,
   player, category pages and reels, Explore, share page, saved, history)
   and its logged tags. A re-file or a tree change updates all of them
   together; Keith checks for drift daily.
6. **Opinion pieces are screened out unless the request explicitly asks
   for a person's opinion or point of view**; then C3P0 marks
   `opinion_requested` with whose, and only that person's pieces are kept,
   labelled as opinion.
7. **Keith's alerts appear as a pop-up whenever the owner opens any admin
   page**, listing every open alert until resolved. **Keith's first fixes
   are in the error and usage tracking** (`pipeline_errors`,
   `provider_usage`, their flushes and stores, missing limits, spend in
   `metering` not counted as usage).
8. **Provider terms.** The owner's use is an internal chronological history
   of what FAM searched with its own keys, never resold. The findings so
   far are in the Housekeeper tab of `docs/FAM_AGENTS.md`: GDELT is open
   (with a citation); Parallel's and Perplexity's terms are unclear for an
   archive reused across listeners; GNews's free plan is non-commercial;
   Tiingo and BALLDONTLIE are approved (the owner has agreed redistribution
   with both); Finnhub and API-Sports are being removed; Polymarket is
   unconfirmed. **Consuela stores a
   provider's material only once the owner marks that provider approved**
   (a per-provider setting, all off except GDELT and FAM's own records
   until then).

## Decided in the owner's third round (2026-10-28)

9. **Keith's admin changes wait for the owner's yes** - an improvement to an
   existing page is shown before and after; a new page or feature is a
   slideshow; nothing ships without approval.
10. **Urgent alerts are also emailed** (`mail.py`), beside the pop-up.
11. **Activation details**: date of birth **required**, **13 and over only**
    (an under-13 date of birth cannot enter); location and gender
    **optional**; gender is man, woman, non-binary, self-describe, prefer
    not to say; location keeps the city, aggregates use the region;
    listeners already active are asked the next time they open the app;
    age bands 13-17, 18-24, 25-34, 35-44, 45-54, 55-64, 65+.
12. **Settled facts the packets do not support** may stay only if they have
    no name, number or date in them; otherwise Mclovin fails them.
13. **Source screen**: official blogs (a company's newsroom, a government
    agency's announcements) are primary sources and kept; all other blogs
    dropped; "analysis" pieces kept as reporting.
14. **Mclovin checks before speech on searchFAM too**, as the script is
    written; only the first chunk waits on it. Measure it; if it costs more
    than expected, tell the owner rather than switching it off.
15. **C3P0 asks the listener when a name is ambiguous** ("United Airlines
    or Manchester United?") before anything is searched - a new step in
    search (an API first, then the screen; old clients that cannot show it
    get the most likely reading from context). Where no listener can be
    asked (tiles, editions), use the context, else leave it unwritten.
16. **Public figures only** - a firm rule: FAM never researches a private
    person; such a request gets no episode and a plain explanation.
17. **Medical, legal and money questions get facts, never advice.**
18. **Consolidated records are kept indefinitely**, reviewed at two years;
    Consuela also combs the whole archive for duplicates from time to time.
19. **The archive may answer an empty search alone - past or settled things
    only** (closes §316's open question).
20. **Episodes made before Nando are re-filed once**, as a trial run the
    owner reviews first.
21. **No budget cap decides whether an agent runs.** Cost is designed low
    (the Cost section of the Shared rules tab in `docs/FAM_AGENTS.md`): code
    first, folded calls, batch, caching, Haiku by default, self-hosted
    checkers, bounded calls with one retry. Loops are stopped as bugs; Keith
    emails when cost per episode runs well above the plan. The owner wants
    to look further into pricing options - keep a running cost-per-episode
    figure on `/admin`.
22. **Shakespeare's journalism playbook**: Claude drafts it; the owner edits
    it (see item 29).

## Decided in the owner's fourth round (2026-10-28)

23. **Urgent alerts go to Admin@familiarize.net** (`IT_ALERT_EMAIL`, set in
    the environment, never in code).
24. **A name the reference look and the providers do not know is treated as
    a private person** - no episode about them.
25. **Finnhub and API-Sports are removed from FAM** - their data cannot be
    published. **Tiingo** (prices) and **BALLDONTLIE** (scores) replace
    them; the owner has agreed redistribution with both, so they are
    approved for speaking and for Consuela's storage.
26. **Mclovin has no whole-script model check.** It checks the named things
    every time - the source screen, quotes, figures, dates, facts and
    claims, mixed stories, advice - in code and with the self-hosted
    grounding checker; the episode rules in code (§309's measures); Haiku
    only on sentences the checker scores unsure, with the source sentences
    beside them (a proposed model - item 28). Target: ~$5 per 10,000
    episodes plus the checker's machine.
27. **Cost target**: all eight agents ~0.8 cents an episode (estimated;
    the Cost section of `docs/FAM_AGENTS.md`), ~80% of it Shakespeare on Sonnet
    ahead of the tap, kept as the quality lever. Keith shows the real
    figure from `metering` on `/admin`. The models in this estimate are
    proposals (item 28).

## Decided in the owner's fifth round (2026-10-28)

28. **Which model or plugin each agent runs is not decided here.** The
    owner: "I will not be making decisions on what to run each agent here,
    that will happen later in another branch with a more complete
    decision-making method." Every model and plugin named in
    `docs/FAM_AGENTS.md` and below is a proposal. **Build every agent's
    model as a setting** (`<AGENT>_MODEL`, priced in `metering.PRICES`) with
    the proposal as its default, so the later decision is a change of
    setting, not of code; no agent may hard-code a model or vendor.
29. **The journalism playbook combines A, B and C** - the public editorial
    guides' principles, audio craft, and the best reporting per category -
    drafted by Claude in `docs/journalism_playbook.md` (`EDITOR_PLAYBOOK`
    points at it). Option D (learning from episodes the owner scores) is
    dropped.
30. **Quality checks are not hands-on.** No step waits on the owner scoring
    episodes. Shakespeare and the playbook are judged automatically: the
    quality checker's code measures (§309), Mclovin's verdicts, and
    listening (who leaves in the first twenty seconds, who hears 85%). Where
    an eval needs labelled examples, build them in code (e.g. inject a wrong
    figure, a swapped quote, a preview stated as a result into a good script
    and check Mclovin catches it) rather than asking the owner to label.
31. **The editor in chief is named Shakespeare** (first called Diana
    Russini, then Shakespear; renamed by the owner). Rule book `docs/agent_rules/shakespeare.md`.

## Decisions still to ask the owner

Check `PROBLEMS.md` §318 (the last entries) for answers first.

- Whether the outlets Claude picked per category in
  `docs/journalism_playbook.md` (Part 4; sports, money and tech are already the owner's)
  are the owner's - the owner may
  edit them; do not wait on this to build.
- Parallel, Perplexity, GNews and Polymarket: whether their material may be
  stored (allow their domains in the session's network settings to read
  their terms), and who asks them.

## The work, in order

Ship each phase as its own change, following the ship loop in `CLAUDE.md`
(`./dev.sh check`, rebuild and republish the preview, short summary).
Everything new is **off by default**, has a time limit, a bounded call size
and a fallback (no budget cap - decided item 21), records
spend in `metering`, is scrubbed on staging by `spend_guard`, opens any new
store through `paths.data_path` and `db.connect`, registers what a wipe must
forget in `demo_data._forget_what_the_log_taught`, and copies every new
setting into `config.py`, `.env.example` and `/api/health` where it is
settable in two places. Log each phase in `PROBLEMS.md` and keep
`docs/FAM_AGENTS.md`, `docs/BACKEND.md` and `docs/DATA.md` true to the code.

### Before the phases - remove Finnhub and API-Sports (decided item 25)

Take Finnhub (the story pool's market moves, `story_sources.FinnhubSignals`,
`WATCHLIST`) and API-Sports (`api_sports.py`, its free-plan scores) out of
FAM, with Tiingo and BALLDONTLIE covering what they did; remove their keys
from `config.py`, `.env.example`, `spend_guard.PAID_CREDENTIALS`,
`provider_usage` and `/api/health`; rewrite the `CLAUDE.md` rules that name
them (`[sports-balldontlie]`, `[sweep-on-demand]`) and their topic-file text;
keep old clients working ([old-clients]). This is its own change, shipped
first.

### Phase 0 - the agent framework (no agent yet)

- An `agents/` package: a **roster** (each agent's purpose, tasks, surfaces,
  ceilings, fallback, rule-book id), **typed hand-offs** as dataclasses with
  JSON schemas (`Brief` → `ResearchPlan` → `PluginPacket` / `ArchivePacket`
  → `Outline` → script sentences → `Verdict`s), a **task table** with
  per-task claims (like `trending_bank`'s per-slot claim) so background work
  is idempotent across two servers.
- **Rule books loaded read-only** from a location the owner controls
  (`AGENT_RULES_DIR` or a fetched URL), each checked against a fingerprint
  the owner sets, the way `VOICE_REFERENCE_FINGERPRINT` refuses a changed
  voice. An agent with a missing or changed rule book does not run. Prompts
  include the rule book text; no code path writes to it.
- **Per-agent switches, time limits and call sizes** in `config.py` (names in each agent's
  Settings sub-tab), `metering` prices, `spend_guard.PAID_CREDENTIALS` for any
  new key, and an **Agents** panel on `/admin` (on/off, last run, spend,
  fallbacks taken, holds).
- **Netenyahu** as code: the per-episode orchestration inside the pipeline
  (asyncio, no model on the tap) and a background loop for Consuela's jobs.

### Phase 1 - C3P0 and Nando at the request

- **`ResearchPlan`**, filled first from today's brief and `live_route` with
  no model; then extend EI's existing call (no new call in series) with:
  the resolved **entity and its kind** and `ambiguous` (United airline/club,
  Eagles bird/team), using provider catalogues and tree candidates as
  evidence - ambiguous means both readings are asked, never a guess; plugin
  choices from the closed list with inputs and dates from `listener_clock`;
  the **archive request** for Sonic (node, what, period); the **title**
  (decided item 1). Code rejects any input or title with a result
  pattern.
- **Nando**: pin the request to one node at the request (EI already returns
  `category_path`; validate against the tree, `category_near` beneath it);
  **store the node id with the episode** (a column beside the category words
  in the script cache) and make `app._episode_category` read it; drive
  `thumbnails.pick*` from it; **confirm after the writer** against the
  writer's `<<CATEGORY:>>` and re-file on disagreement; extend
  `category_audit`. Measure the two problems it exists to fix - thumbnails
  that do not match titles, and myFAM category pages with unrelated episodes
  - before and after (`tools/categories_report.py --audit`).
- **Tree-change events**: when `categories` mints, moves or merges a node,
  write a task for Consuela.
- **Ask the listener when a name is ambiguous** (decided item 15): an
  `ambiguous` brief returns the readings to the client before any plugin is
  called (`/api` first, then the search and voice-search screens); the
  chosen reading resumes the request; old clients get the likeliest reading
  from context. On tiles and editions: context, else unwritten.
- **Public figures only** (decided item 16): a named person is researched
  only when the reference look, a provider's catalogue or reporting shows a
  public role; otherwise no episode and a plain explanation, logged.
- **Facts, never advice** (decided item 17): an advice request is planned as
  the facts around it.
- **Re-file old episodes** (decided item 20): a dry run listing every change
  for the owner's review, applied only after a yes.

### Phase 2 - MongoDB: Consuela and Sonic

- **Schema**: facts and packets carry **node ids** (and path), not only
  category words; indexes on `(node_id, published)` and on live records'
  `(subject, metric, as_of)`; a time-series layout for live sports and
  market records (every sweep's score and price kept with its time - scores
  through a game are a series, never duplicates).
- **Consuela's jobs** (background, dry-run first) - decided item 3:
  **consolidate at intake**: each screened packet, filed under Nando's node,
  is reduced at once to key sentences, figures, quotes and facts (sentence
  numbers only from the model; figures parsed in code), each with article
  link, date and grade. Today `history_archive` keeps intake records with
  passages until digested (§308); change that so passages wait only in a
  short-lived queue until consolidation runs (minutes) and are never kept,
  and consolidate and clear the intake already stored. Then itemise by
  node, date and kind; nightly dedupe with the never-duplicates table; a
  **second consolidation at 90 days** to important figures, quotes, trends
  (the dated series themselves) and article links; re-file on tree changes;
  double-check with Nando; purge opinion and blog entries already stored;
  keep consolidated records indefinitely (review at two years) and run a
  periodic full-archive duplicate comb (decided item 18).
  Reversible marks, 30 days, logged.
- **Activation step** (decided items 2 and 11): when an account is let off
  the waitlist (`status` flips to `active`), and for active accounts the
  next time they open the app, the app asks for date of birth (required;
  under 13 cannot enter), location (optional; the city kept) and gender
  (optional; man, woman, non-binary, self-describe, prefer not to say) -
  an API before a screen ([api-before-screen]), a contract for old clients
  ([old-clients]), the Privacy Policy and consent updated first, account
  deletion removing them ([ios-pressures]).
- **Aggregate demographic signals** (decided item 2): a background job that
  joins accounts' demographic fields with what was searched and heard, in
  place, and writes **only** totals and correlations by category, region,
  time and band, for groups of **100 or more**, never a per-person row,
  never guests or private searches; it lives with the accounts data
  (Postgres), not in MongoDB, and is registered with account deletion and
  wipes.
- **Per-provider storage switch** (decided items 8 and 25): Consuela keeps a
  provider's material only when that provider is marked approved - GDELT
  (with its citation), Tiingo and BALLDONTLIE so far.
- **Sonic**: a **read-only Atlas user** and connection; the closed lookups
  (by node and period, result between dates, value on a day or series, by
  meaning, corroboration) built in code; node → parent/children → vector →
  none; a **date-ordered, grouped `ArchivePacket`** for Shakespeare and the writer;
  the existing 0.5 s grace window; today's `history_archive.look` as the
  fallback.
- **The category tag everywhere** (decided item 5): write Nando's node id
  on the script row, as R2 object metadata (a copy-in-place update when it
  changes), and on every MongoDB record; one function updates all of them
  together on a re-file or tree change, and app caches are invalidated.
- **Sync with Cloudflare** (decided item 5): a reconciliation job
  that every episode id and node id agree between MongoDB, the script cache
  and R2's objects; Keith reports drift.

### Phase 3 - Shakespeare and Mclovin in the writing

- **Mclovin's source screen** (decided item 6: stands aside only for
  `opinion_requested`, only for the named person's pieces) inside
  `research.screen_results` (URL path,
  title label, a provider's page type, blogging hosts) on every rung and the
  second look, and the same screen on Sonic's packet; a small model on
  unsettled pages ahead of the tap only; drops logged and counted. A packet
  left thin gets the existing retry; an empty one is a missed search, never
  a refusal.
- **Shakespeare**: an `Outline` (extractive summary by sentence number, beats,
  which packet carries each, opening and landing, `thin`) with numbers
  checked in code and the text shown to the writer from the sources; a
  `docs/journalism_playbook.md` (`EDITOR_PLAYBOOK`) and `examples/` in its
  cached prompt; the model a setting (item 28; proposed: Sonnet batched on
  editions first, Haiku on search); measured automatically with `write.py`,
  the quality checker's code (§309) and Mclovin's verdicts, with and
  without Shakespeare - never by hand-scoring (item 30).
- **Mclovin while the writer writes**: a per-sentence hook in
  `_ScriptReader` beside the existing guards - quotes (span found in the
  packets, speaker matched), figures (`figure_guard`'s reader widened from
  markets to every number, matched on subject and moment - measure on audits
  first, it was kept narrow on purpose, §269), dates (`listener_clock` and
  the source's date), support from a **self-hosted grounding checker**
  (MiniCheck-class) on a machine that is **not** the voice pod, and the
  mixed-stories check (support spanning two `news_clusters` needs the link
  supported). A failing sentence goes back to the writer once; a second
  failure drops it. Only the two packets are evidence.
- **Before speech on searchFAM** (decided item 14): the per-sentence check
  runs on search too; measure the first chunk's added wait with
  `tools/stall_probe.py` and `tools/compare_search.py` (the spec is about a
  second to the first word) and report it to the owner if it is more than
  expected.
- **No whole-script model** (decided item 26): the episode rules in code
  (`episode_quality.measure`); the grounding checker returns a score per
  sentence, and only the unsure band goes to a small model (proposed:
  Haiku; item 28) with the source
  sentences beside it. Vectara's HHEM-2.1-Open is built to run on a CPU -
  try it first on a Render instance, a GPU only if searchFAM's first chunk
  is too slow.
- **Settled facts and advice** (decided items 12 and 17): an unsupported
  settled fact fails if it has a name, number or date; an advising sentence
  fails. **Official blogs and "analysis"** kept (decided item 13).

### Phase 4 - Keith Wong and `/admin`

- **Usage monitors** beside `provider_usage` and `metering`: Render API,
  RunPod API (already used by `voice_control`), MongoDB Atlas Admin API,
  Cloudflare GraphQL analytics for R2 - read-only keys via `FAM_SECRETS`;
  plan limits copied beside them; warnings at 80% and urgent at 95% or a
  projected overrun, with the next plan's price (`docs/SCALING_TIMELINE.md`).
- **Incidents**: one per cause, from `pipeline_errors` (`explain()`),
  `/api/health`, background loops' last runs, the voice supervisor and
  listener reports; a closed list of reversible fixes (re-verify the voice
  ladder, re-run a failed job once, hold a background sweep until a limit
  resets, ask Netenyahu to hold an agent), each verified by a real call,
  **observe-only until the owner switches each on**; a **fix report**
  answerable at any moment; escalations with a solution plan; the
  **pop-up on every admin page** with all open alerts (decided item 7);
  **first fixes in error and usage tracking**.
- **Agent conformance checks** written from each rule book (Sonic never
  wrote; Consuela's deletions are within grace; Mclovin's verdicts are among
  its named failures; C3P0's inputs carry no results; Nando's nodes exist),
  run on samples.
- **`/admin` as Keith's** (decided items 4 and 9): Keith proposes
  improvements to existing admin pages as a before-and-after and new pages
  or features as a slideshow; **nothing ships without the owner's yes**,
  then through FAM's normal checks, staging first, reversible, logged.
- **Email for urgent alerts** (decided item 10) via `mail.py`, beside the
  pop-up.
- **Cost per episode on `/admin`** (decided item 21): what the agents cost
  per episode against the plan, with an email when it runs well above it;
  no cap stops an agent.

## Done looks like

- Each phase merged behind switches that are off, `./dev.sh check` green
  (CI on Python 3.12), the preview republished, and `PROBLEMS.md` updated.
- With every switch off, listeners hear exactly what they hear today.
- Each agent, when switched on, can be measured against the eval its tab
  in `docs/FAM_AGENTS.md` names before it is trusted.
- No rule book was changed; anything that could not honour one was raised
  with the owner.
