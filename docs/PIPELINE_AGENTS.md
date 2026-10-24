# The pipeline with agents: options

Written 2026-10-24 for the pipeline the owner drew (PROBLEMS.md §310). No
agent is built yet. This document is for deciding how to build them: by price,
latency, how easy each is to implement, and how the agents talk to each other.

**Prices are Anthropic's list prices on 2026-10-24. The token counts and
latencies below are estimates; none has been measured. Measure before you
decide** (`write.py`, `/api/health`'s `seconds_by_path`, `metering`).

## 1. The pipeline as drawn, and what exists today

```
search
  └─ Episode Intelligence (agent)
        1. contextualise: category, subcategory, context of the search
        2. write the inputs for the plugins
        ├─ plugins ───────────────► packet one: what is happening now
        └─ history store (research agent, MongoDB) ► packet two: what came before
              └─ consolidate both packets into what the writer can digest (agent)
                    └─ script writer (Haiku, assisted by an agent)
```

| Box in the drawing | In the code today | Model, cost per episode |
|---|---|---|
| EI: contextualise | `episode_intelligence.understand`: three structured calls side by side (understand / search / outline). Category and subcategory are now `Brief.category_path`, placed against the category tree in code (§310) | Haiku 5.5, medium effort, 8 s ceiling |
| EI: plugin inputs | Brief fields each plugin reads: `search_query`/`angles`/`objective`/`recency_days` → search ladder; `live_domain`/`live_entity` → live facts and players; `outcome_dependent` → forecast; `place` → local ladder; `HistoryQuery` → history store (§310) | (part of the EI calls) |
| Plugins → packet one | `research.retrieve` (Parallel → Perplexity → GDELT), `live_facts`, `players`, `forecast`, `local_news`, `weather` | No model calls |
| Research agent → packet two | **New seam:** `history.lookup`, with the store plugged in through `history.register` (MongoDB branch) | None yet |
| Consolidate | Packet one: `evidence_digest` (an extractive cut; off by default). Both packets: `history.consolidate` (code) and the writer's prompt order: live state → evidence → background | Haiku 5.5, thinking off, when on |
| Script writer | `ScriptGenerator.stream_prepared`, streamed sentence by sentence into the voice | Haiku 5.5, low effort |
| "assisted by an agent" | The guards in code: `OpeningGuard`, `ScoreGuard`, `FigureGuard`, `MaterialGuard` | None |

## 2. The constraints that decide most of this

These are settled rules (CLAUDE.md). Each one rules some options out.

1. **The spec: about a second from tap to the first word** ([one-sentence-spec],
   amended for search only). Search already pays for EI and retrieval in front
   of the first word. Every model round trip added on the tap path adds
   seconds. Work done before the tap costs the listener nothing
   ([decouple-script-audio], [myfam-warms-briefs]).
2. **No tools on the speaking call** ([nothing-before-material]). The writer
   holds its material before its first token. An agent loop *inside* the
   writer (tools, the advisor tool, web search) breaks this rule. The writer's
   helper has to work before the writer (planning, consolidating) or after it
   (ahead of the tap only, because the search path streams).
3. **A layer that adds quality must not subtract availability**
   ([ei-before-search]). Every agent needs a ceiling and a fallback.
4. **Nothing the model says is evidence.** EI never asserts a fact. The
   evidence cut is extractive. A consolidating agent that *summarises* puts
   model prose where the writer expects sources.
5. **Staging spends nothing** ([zero-spend-staging]). A hosted agent or a
   hosted database cannot run on staging.
6. **Metering per listener at spend time** ([metering]). Each option has to
   report token usage call by call.

## 3. Five ways to build an agent

Prices per million tokens: **Haiku 5.5 $0.10 in / $0.50 out**, Sonnet 5.5
$2 / $10, Opus 5.5 $4 / $20. Cache reads cost about a tenth of input. The
Batches API is half price but answers within the hour.

The per-call estimate assumes about 8k tokens in and 1k out, thinking
included. That comes to about **$0.0013 per call on Haiku**, $0.026 on Sonnet
and $0.052 on Opus. At 10,000 episodes a day, one extra call per episode costs
about **$13 a day on Haiku**, $260 on Sonnet, or $520 on Opus.

| | A. Fixed calls, orchestrated in code (today) | B. Tool loop on the Messages API (Tool Runner or a manual loop) | C. Claude Agent SDK | D. Managed Agents | E. Batches |
|---|---|---|---|---|---|
| **What it is** | One structured-output call per role. `asyncio` decides the order | The model calls *our* tools (Mongo queries, lookups) for several turns | The Claude Code harness as a library: built-in tools, subagents, MCP | Anthropic runs the loop and a sandbox. Versioned agents, scheduled deployments, multi-agent workflows | Any of A or B, sent as a batch |
| **Price** | Lowest: one call per role | N calls, each re-sending the context. Prompt caching cuts the repeats | Higher. The harness's own system prompt and tool use add tokens | Tokens plus the session's container | Half of A or B |
| **Latency** | One round trip per role, and roles run side by side | N round trips **in series**. Too slow for the tap path unless capped at one tool turn | Process start-up plus a loop. Not for a request path | Session start-up plus a loop. Background only | Minutes to an hour |
| **Ease in FAM** | Exists: `episode_intelligence._ask` is the pattern. Metering, ceilings and `/admin` are already wired | Moderate. Same SDK and client. Each turn's `usage` fits `metering.Usage` | Easy to prototype. Hard to meter per listener and to bound in time | Config files plus `ant apply`. Runs outside our server, so metering comes from the session | Exists (`claude_batch.py`, `EDITION_BATCH`) |
| **How agents talk** | Typed hand-offs: `Brief` → `HistoryQuery` → `HistoryPacket` → `EpisodePlan`. Testable and loggable | Tool results inside one conversation. Agents pass results through the orchestrator | Subagents return summaries to a lead agent, so detail is lost on the way | Built-in multi-agent workflows, plus shared memory stores | The same as A or B |
| **Autonomy** | None: it can't decide to look again | It decides what to query next | High | High | The same as A or B |
| **Best FAM use** | Everything on the tap path | The research agent **ahead of the tap**, and one capped turn on the tap path | Developer and back-office tools: category audits, eval runs | Nightly and scheduled jobs: curating the history store, editions | Editions and write-ahead |

A sixth piece works with B, C and D: **an MCP server in front of the history
store.** Any agent framework, and Claude Code during development, can then use
the same tools: `episodes_about(entity, before)`, `events_between(subject,
from, to)`, `facts_for(category_path)`. The Messages API's MCP connector runs
those calls on Anthropic's side, which saves our round trips. It needs a
publicly reachable server, so staging cannot use it.

## 4. Recommendation by role

**EI (contextualise and write the plugin inputs): stay with A.** It already
runs three calls side by side under a ceiling, with a fallback brief.
Contextualising is now partly code (`category_path`). Two upgrades, cheapest
first:

1. **Make the plugin inputs one typed object.** Today each plugin reads the
   brief fields it needs. A `ResearchPlan` (one entry per plugin, with its
   inputs and a reason) would be the contract any future agent writes and
   reads. `HistoryQuery` is the first entry of it.
2. Warm more briefs before the tap ([myfam-warms-briefs]). Every warmed brief
   removes EI from the tap path entirely.

**Research agent over MongoDB: two halves.**

- **On the tap path: a query, not an agent.** `HistoryQuery` carries the
  entities, the category path, the subject and the window. An indexed Mongo
  query on those answers in tens of milliseconds. It runs beside a web search
  that takes seconds, so even a slow one is hidden. If a model has to choose
  among the results, cap it at one Haiku call with thinking off, the way the
  evidence cut works, and it still runs beside the search.
- **Before the tap: a real agent (B, or D for scheduled jobs).** The packet's
  quality comes from what is written into the store, as [match-at-write-time]
  says of the cache. A curator runs after each episode, or nightly over GDELT
  and the packets. It turns *dated source passages* into dated events and
  facts filed under category paths. Haiku in a tool loop, sent as a batch,
  costs fractions of a cent per episode. **It must write from the sources'
  sentences, never from FAM's scripts.** A script is model prose. Storing it
  as history and reading it back as background is how a recalled fact
  becomes "evidence".

**Consolidation: stay extractive (A).** `history.consolidate` is code. The
evidence cut picks sentence numbers, so the model's words never reach the
writer. The next step is one extractive cut across both packets: sentences
from packet one and records from packet two, numbered together, so the cut can
also drop background that repeats today's news. Never an abstractive summary
(constraint 4).

**The writer's assistant: before the writer on the tap path, after it only
ahead of the tap.**

- *Search (streamed):* the help has to come before the first token. That
  means the EI outline (already built), the consolidated packets, and a
  better `examples/` folder ([examples-lever]). Afterwards, only the code
  guards run.
- *Editions, Trending, write-ahead (nobody waiting):* Haiku writes, then a
  Sonnet 5.5 critic reads the script against the packets and the episode
  rules (no tease, last line most concrete, every sentence informs) and
  returns line-level fixes, and Haiku rewrites. That is roughly two calls
  more, about $0.03 per episode, half that as a batch. This is the most
  promising place for an agent to improve the writing, which is the product.
  A critic must not see anything the writer couldn't, or it will "fix"
  sentences towards its own memory.
- The advisor tool is a tool on the speaking call, so constraint 2 rules it
  out.

**Communication: typed hand-offs, plus a store keyed by subject.** Hand-offs
(dataclasses with JSON schemas) through the orchestrator are what FAM already
does, and they are what tests and `/admin` can see. When agents run at
different times (a curator at 05:00, a tap at 09:00), they meet in the store,
keyed by subject and category path. That is how the warmed brief already
works. Free-form agent-to-agent conversation (subagent summaries,
multi-agent chat) loses detail and is hard to bound. Keep it to back-office
jobs.

## 5. Other changes the pipeline needs

Built in this change (§310): the `HistoryQuery`/`HistoryPacket` seam, the
category path, the grace wait, consolidation, the `<background>` block,
provenance, `/api/health`, `write.py` output, and `FigureGuard` reading
packet two.

Still to do, roughly in order:

1. **The MongoDB branch registers itself:** `history.register(fetch,
   "mongo", remote=True)` at boot. Every entry needs a date. No URLs in the
   text.
2. **A write path into the store** (the curator above). Without it the store
   holds nothing worth reading. Decide whether it is written from packets
   (recommended) or from episodes (see the warning above).
3. **Indexes** on `entities`, `category_path` and the date. The query already
   carries them.
4. **Warm packet two before the tap.** `HistoryQuery` holds nothing personal,
   so prefetch can cache it beside the brief, by subject.
5. **`ScoreGuard` and history.** It only allows score pairs that the
   scoreboard or a player report states. A past result taken from packet two
   ("they beat them 41-17 last year") would be dropped on a sports episode.
   Deciding whether a dated past score may be spoken is a product call.
6. **One extractive cut across both packets** (section 4), measured with
   `write.py --compare`.
7. **`ResearchPlan`**: one typed object holding every plugin's inputs
   (section 4).
8. **Staging:** a local Mongo, or none, so the zero-spend guard holds.
   `remote=True` already stops a hosted store from being asked there.
9. **A wipe:** if the store is derived from episodes or packets, register it
   in `demo_data._forget_what_the_log_taught` ([wipe-derived]).
10. **Measure first.** `HISTORY_GRACE_SECONDS=0.5` is a guess. Read
    `/api/health.history.seconds_by_path` once the store is live.
