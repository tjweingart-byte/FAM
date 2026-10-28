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
| Editor in chief | Diana Russini | With the writer: extractive summary of both packets, outline, a journalist's open, flow and close |
| Script supervisor | Mclovin | Screens opinion and blogs from both packets; checks each sentence while the writer writes - quotes, figures, facts, dates, mixed stories - against the two packets only |
| Housekeeper | Consuela | MongoDB's only writer: files packets by node, dedupes, full packets 90 days then key items, keeps all sports and market data |
| IT guy | Keith Wong | Watches errors, usage against every plan, and the agents; fixes what it may silently with a report; plans for the rest; owns `/admin`'s content |

Names are internal: never shown to a listener.

## Decisions the owner must make before the affected work

Ask the owner these at the start, in one message; do the unaffected work
while waiting. Each one changes or touches a settled rule.

1. **C3P0 writes the episode title.** This amends `[title-from-content]`
   (the writer titles an episode by what it turned out to be about). Proposed:
   C3P0's title is the episode's title; Mclovin compares it with the
   finished script and a mismatch falls back to the writer's `<<TITLE:>>`.
   If confirmed, rewrite the rule in `docs/claude/product.md` and its
   `CLAUDE.md` line (the `tests/test_claude_md.py` budget applies).
2. **Consuela's "demographics, who is searching".** Rule 7 and
   `[private-search]` / `[account-gates-kept]` / `consent.py` forbid keeping
   who a listener is. Proposed: aggregate counts only (category, region,
   time), with a minimum group size, never guests or private searches;
   anything per-person waits for a Privacy Policy change and consent.
3. **Full packets in MongoDB for 90 days.** §307 sized the archive for key
   information only. Needs: the search vendors' terms on storing excerpts
   (Parallel, Perplexity), and the Atlas tier it implies.
4. **Keith's "authority to build" `/admin`.** Proposed: Keith edits a
   declarative panel layout over read-only data live; any new admin code is
   drafted as a change for a person to review. An agent never edits or
   deploys running code.
5. **What "Cloudflare" must stay in sync with MongoDB.** Today R2 holds
   episode audio and the episode rows live in the script cache
   (`scripts.db` / Postgres). Confirm which records must carry the same
   episode and node ids.
6. **Source screen edge cases**: official blogs announcing their own news;
   questions that ask for opinions; pieces labelled "analysis".
7. **Keith's notification channel** for urgent problems, and which fixes it
   may make first.

## The work, in order

Ship each phase as its own change, following the ship loop in `CLAUDE.md`
(`./dev.sh check`, rebuild and republish the preview, short summary).
Everything new is **off by default**, has a ceiling and a fallback, records
spend in `metering`, is scrubbed on staging by `spend_guard`, opens any new
store through `paths.data_path` and `db.connect`, registers what a wipe must
forget in `demo_data._forget_what_the_log_taught`, and copies every new
setting into `config.py`, `.env.example` and `/api/health` where it is
settable in two places. Log each phase in `PROBLEMS.md` and keep
`docs/FAM_AGENTS.md`, `docs/BACKEND.md` and `docs/DATA.md` true to the code.

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
- **Per-agent switches and ceilings** in `config.py` (names in each agent's
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
  (subject to decision 1). Code rejects any input or title with a result
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

### Phase 2 - MongoDB: Consuela and Sonic

- **Schema**: facts and packets carry **node ids** (and path), not only
  category words; indexes on `(node_id, published)` and on live records'
  `(subject, metric, as_of)`; a time-series layout for live sports and
  market records (every sweep's score and price kept with its time - scores
  through a game are a series, never duplicates).
- **Consuela's jobs** (background, batched, dry-run first): file each
  screened packet under Nando's node at intake; digest into key sentences,
  figures, quotes (sentence numbers only; figures parsed in code); itemise
  by node, date and kind; nightly dedupe with the never-duplicates table;
  **90-day consolidation** to key quotes, numbers, facts and sentences
  (subject to decision 3); re-file on tree changes; double-check with Nando;
  purge opinion and blog entries already stored; aggregate long-term signals
  (subject to decision 2). Reversible marks, 30 days, logged.
- **Sonic**: a **read-only Atlas user** and connection; the closed lookups
  (by node and period, result between dates, value on a day or series, by
  meaning, corroboration) built in code; node → parent/children → vector →
  none; a **date-ordered, grouped `ArchivePacket`** for Diana and the writer;
  the existing 0.5 s grace window; today's `history_archive.look` as the
  fallback.
- **Sync with Cloudflare** (subject to decision 5): a reconciliation job
  that every episode id and node id agree between MongoDB, the script cache
  and R2's objects; Keith reports drift.

### Phase 3 - Diana and Mclovin in the writing

- **Mclovin's source screen** inside `research.screen_results` (URL path,
  title label, a provider's page type, blogging hosts) on every rung and the
  second look, and the same screen on Sonic's packet; a small model on
  unsettled pages ahead of the tap only; drops logged and counted. A packet
  left thin gets the existing retry; an empty one is a missed search, never
  a refusal.
- **Diana**: an `Outline` (extractive summary by sentence number, beats,
  which packet carries each, opening and landing, `thin`) with numbers
  checked in code and the text shown to the writer from the sources; a
  **journalism playbook** file and `examples/` in her cached prompt; Sonnet
  batched on editions first, Haiku on search; measured with `write.py` and
  the quality checker (§309) against the owner's scores.
- **Mclovin while the writer writes**: a per-sentence hook in
  `_ScriptReader` beside the existing guards - quotes (span found in the
  packets, speaker matched), figures (`figure_guard`'s reader widened from
  markets to every number, matched on subject and moment - measure on audits
  first, it was kept narrow on purpose, §269), dates (`listener_clock` and
  the source's date), support from a **self-hosted grounding checker**
  (MiniCheck-class) on a machine that is **not** the voice pod, and the
  mixed-stories check (support spanning two `news_clusters` needs the link
  supported). A failing sentence goes back to the writer once; a second
  failure drops it. The whole-script writing check runs on a different model
  from the writer's (Sonnet), before caching ahead of the tap and right after
  on search. Only the two packets are evidence.
- **Latency**: measure the per-sentence cost on searchFAM with
  `tools/stall_probe.py` and `tools/compare_search.py` before switching it on
  there; the spec is about a second to the first word.

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
  notification channel per decision 7.
- **Agent conformance checks** written from each rule book (Sonic never
  wrote; Consuela's deletions are within grace; Mclovin's verdicts are among
  its named failures; C3P0's inputs carry no results; Nando's nodes exist),
  run on samples.
- **`/admin` as Keith's**: a declarative panel layout over read-only data
  sources Keith may arrange, add and retire; new code drafted for review
  (decision 4).

## Done looks like

- Each phase merged behind switches that are off, `./dev.sh check` green
  (CI on Python 3.12), the preview republished, and `PROBLEMS.md` updated.
- With every switch off, listeners hear exactly what they hear today.
- Each agent, when switched on, can be measured against the eval its tab
  in `docs/FAM_AGENTS.md` names before it is trusted.
- No rule book was changed; anything that could not honour one was raised
  with the owner.
