# FAM agents — the guidelines for each one

| | |
|---|---|
| **Status** | Planning document. No agent here is built yet |
| **Started** | 2026-10-24 (PROBLEMS.md §305) |
| **Audience** | The owner, and any Claude session asked to design, build or change one of these agents |

FAM will have many agents. An *agent* here is a background worker that uses
a language model to do a job nobody is waiting on (digesting, tidying,
checking), as opposed to the calls on a listener's path (the brief, the
writer), which are governed by `CLAUDE.md`.

**How to use this file.** In a new session, say *"read `docs/FAM_AGENTS.md`
and work on the Database inventory specialist"* (or whichever agent). Each
agent has its own section: what it is for, what it must never do, and which
model is right-sized for it. Add an agent by copying the template at the end.
When an agent is built, change its **Status** line and log the build in
`PROBLEMS.md`.

---

## Rules every agent follows

These come from settled constraints in `CLAUDE.md`. An agent that breaks one is
wrong even if its output looks better.

1. **Nobody waits on an agent.** Agents run in the background: on a timer, or
   after the episode has been written. Nothing on a listener's path awaits
   one ([nothing-before-material], [latency-start-earlier]).
2. **Extract, never invent.** When an agent keeps text, it keeps the
   publisher's own sentences, and code assembles them. A model's own wording
   never becomes stored evidence. This is the guard rail of `evidence_digest.py`:
   the model answers with sentence numbers, code does the rest
   ([started-not-finished], [live-facts]).
3. **Figures are handled by code.** Numbers are parsed, normalised and compared
   in code, never judged by a model. A figure a model "tidied" is a figure
   nobody can trust ([score-guard]).
4. **Every dollar has a ceiling.** Each agent has a daily dollar and item cap in
   `config.py`, records its spend in `metering`, and reports `None` rather than
   `0` when it has not run ([metering], [prefetch-same-key]).
5. **Failures are visible.** Each run reports what it did and what failed on
   `/admin` ([failures-visible]).
6. **Staging spends nothing.** On staging an agent runs its code-only parts,
   or nothing, and says so ([zero-spend-staging]).
7. **Nothing personal.** An agent never stores who asked. Attached documents
   and private searches are never fed to an agent ([op-attachments],
   [private-search]).
8. **Destructive work is reversible.** Anything that removes data marks it
   first, keeps it for a grace period, logs why, and can be undone. Hard
   deletion is a separate, later step.
9. **Provenance is kept.** What an agent keeps stays traceable to the article it
   came from ([provenance-all-paths]).

---

## Choosing a model: the shared reference

Anthropic list prices, per million tokens (cached 2026-10-06; check
<https://platform.claude.com/docs/en/about-claude/pricing> before committing
money).

| Model | ID | Input | Output | Best at |
|---|---|---|---|---|
| Claude Haiku 5.5 | `claude-haiku-5-5` | $0.10 | $0.50 | High-volume extraction, classification, picking from a list. Already FAM's brief and writer model |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | $2.00 | $10.00 | Judgment calls on messy text: "do these two sentences claim the same thing?" |
| Claude Opus 5.5 | `claude-opus-5-5` | $4.00 | $20.00 | Hard reasoning, long multi-step work, designing rules |
| Claude Fable 5.1 | `claude-fable-5-1` | $10.00 | $50.00 | The most demanding reasoning; rarely right for a background job |

Levers that apply to every model:

- **Batch API: half price.** Results arrive asynchronously, typically within
  an hour and at most 24 hours. Most agent work can wait that long.
  FAM already batches its editions (`claude_batch.py`, [writer-savings]).
- **Prompt caching.** An agent's fixed instructions cost about a tenth of
  the input price once cached (`PROMPT_CACHE`).
- **Effort.** Haiku 5.5 and Opus 5.5 default to `medium`. Extraction and
  classification usually hold their quality at `low`, which is cheaper and
  faster. Measure before lowering.
- **Haiku 5.5 above 100K tokens** in one prompt bills $0.50 / $2.50. Keep agent
  prompts small.

All four models use the same SDK and the same `anthropic_client.py` path.
Switching models is one setting, so **ease of implementation does not differ
by model.** What differs is price, speed and how well each one handles the
task.

---

## 1. Database inventory specialist

**Status:** planned, not built. Designed in conversation on 2026-10-24
(§305). **The archive it works on is built** (§306, `history_archive.py`,
`ARCHIVE.md`), so this agent is next.

**What it reads and writes, as built:**
- It reads `intake` records with `digested: false`: one per article, with
  passages, grade, date, and the subjects and categories of the episodes
  that used it.
- It writes `facts` through `history_archive.add_fact(Fact(...))`, which
  embeds each fact with the installed sentence model.
- It marks each intake record `digested` once done.
- Superseding a fact is `active: false` plus `superseded_by`; the recall
  already ignores both.
- `episode_sources` is filled by the archive itself; the agent does not
  touch it.

### Purpose

Keep FAM's **history archive** small, trustworthy and free of repetition, so
the writer can get a second packet of historical context beside the fresh
web search, and a listener's episode screen can show the articles it came
from.

### Where it sits

- **The archive** is the store this agent fills and tidies. The decisions so
  far (§305):
  - It will live on **MongoDB Atlas**: start on Flex in AWS Oregon
    (us-west-2, beside Render's default region), move to M10 at about 1,000
    monthly listeners.
  - A **local SQLite backend** sits behind the same interface for tests,
    staging and a fresh clone, so staging still spends nothing.
  - The goal is **years of history**, kept affordable by storing only key
    information (below), never whole articles.
- **Retrieval** (finding archive items relevant to a new question and
  appending them to the packet as dated background) is a separate piece of
  code, not this agent. This agent decides *what is in* the archive. The
  retrieval step decides *what the writer sees*.

### Responsibilities

**1. Digest every packet that comes in.**
After an episode is written, its packet's sources are queued for this agent.
The queue is fed by searchFAM, myFAM live tiles, Trending, DailyFAM and the
story pool. Private searches and attached documents are never queued. The
agent runs in batches, never on the listener's path.

**2. Store the key information: sentences and figures.**
For each article it keeps:

- **Key sentences**: the publisher's own sentences that carry who, what,
  when and why. They are chosen by number, assembled by code, and never
  paraphrased.
- **Important numbers and figures** as structured records, each with:
  - the subject (team, company, person, place);
  - the metric (score, closing price, revenue, vote share, casualties,
    transfer fee...);
  - the value and unit, normalised in code ("$1.2bn" = 1,200,000,000 USD);
  - the period or as-of date (the game, the trading day, Q3 2026);
  - whether it is a **reported outcome** or a **forecast or expectation**;
  - the sentence it came from, so it can be checked.
- **Dates are absolute.** "Yesterday" is resolved in code against the
  article's publish date before anything is stored ([recency-credibility]).
- **The article's grade** (primary / established / unverified), its publish
  date and its category ([one-category]).

**3. Keep the article links, for the episode screen.**
Every episode keeps the list of articles it was written from: URL, title,
outlet, date. This list feeds a planned "sources" view on the episode screen.
It is provenance, kept per episode, and separate from the deduplicated
facts. An article whose facts were all duplicates still appears in the
episode's source list. The hostname never enters the writer's packet; that
rule is unchanged.

**4. Prefer the most trusted source, and never keep duplicate information.**
When a new article states something the archive already holds:

- The version from the **higher-graded source** is the one kept: primary over
  established over unverified.
- On equal grades, the **earliest published** is kept (the first report); on
  equal dates, the **more specific** (the one with the figure).
- The duplicate's text is **not stored**. If the new article is better
  sourced, its sentence replaces the old one. The old one is marked
  superseded, not deleted (see 5).
- Information the new article adds (a new figure, a new development) is kept
  even when the rest of the article is a duplicate.
- **A repeating article is kept as a corroborating link** (the owner's
  decision, 2026-10-24): its URL, outlet, grade and date are attached to the
  fact it repeats, and the fact's corroboration count goes up. None of its
  text is stored. Corroboration tells the writer how widely a fact was
  reported, and that is never "duplicate information". Each link is
  counted once per fact, even if the article is seen again.

**5. Scrape the existing archive and remove duplicates, very carefully.**
A periodic pass (nightly, batched) looks for duplicates that slipped in.

**Two things are duplicates only when all of these match:**

- same subject;
- same claim or metric;
- same as-of date or period;
- same value after normalisation;
- same status (an outcome matches only an outcome; a forecast only a
  forecast).

**These are never duplicates**, however similar the wording:

| Looks alike | Why both stay |
|---|---|
| A stock's closing price on Monday and on Tuesday | Different as-of dates. A price series is history, not repetition |
| Q2 revenue and Q3 revenue; quarterly and annual figures | Different periods |
| "Expected to win 4-2" and "won 4-2" | A forecast and an outcome ([started-not-finished]) |
| An early figure and a revised one ("12 dead", later "15 dead") | A revision. Keep both, the later one marked as the update |
| Two sources giving different values for the same thing on the same date | A **conflict**, not a duplicate. Keep both, flag it, and never pick a winner by model |
| Two teams' scores in one game; two candidates' vote shares | Different subjects |
| The same quote attributed to two different people | Different speakers. Flag it; don't merge |
| The same fact in two languages or units | A duplicate only after code normalises both. Never on a model's say-so |

**How the pass decides, in order:**

1. **Code finds candidates.** Records with the same subject and metric (for
   figures), or close vectors plus overlapping names (for sentences).
2. **Figures are decided by code alone.** Exact match on subject, metric,
   period, value, unit and status, or not a duplicate. No model is asked.
3. **Sentences go to a model only as a yes/no question**: "do these two
   sentences state the same fact about the same moment?" The model answers
   with a verdict and nothing else. Any uncertainty means **keep both**.
4. **Nothing is hard-deleted by this pass.** A duplicate is marked
   `superseded_by` with the reason, kept for 30 days, and can be restored.
   Every merge is logged with both texts, and the run's counts appear on
   `/admin`.
5. **A dry-run mode** reports what it *would* merge. The first runs on real
   data are dry runs, and a sample is checked by hand before the pass is
   allowed to mark anything.

**6. Roll up old history (later).** After about 90 days, a subject's
records for a month can be consolidated into one timeline record of dated
sentences and figures. These are chosen, never rewritten. This is what keeps
years of history on a small Atlas tier (§305's projections).

### What it must never do

- Write its own summary of an article and store it as evidence.
- Merge or drop a figure on a model's judgment.
- Treat a forecast, preview or "is expected to" as an outcome.
- Store who searched for something, or anything from a private search or
  attachment.
- Run on staging with a paid key, or put a hostname into a writer's packet.
- Delete on the first pass.

### Model options for this agent

The agent does three kinds of work, and they need different amounts of
intelligence.

**Volume assumed:** unique new articles per month at the projections in
§305, which are estimates from `docs/FINANCIAL.md` §4 and not measured:
~8k (100 listeners), ~47k (1,000), ~285k (10,000), ~2.1M (100,000).

**a) Extraction: picking key sentences and figure-bearing sentences.**
About 1,200 tokens in (cached instructions plus the article's passages) and
about 150 out (sentence numbers and figure fields) per article.

| Model | Per article (batch) | 1k listeners/mo | 10k listeners/mo | 100k listeners/mo | Latency | Effectiveness |
|---|---|---|---|---|---|---|
| **Haiku 5.5, low effort** | ~$0.0001 | **~$5** | **~$28** | **~$210** | Seconds per call; batch within ~1 h | Good: the same job `evidence_digest.py` already gives it. Misses some subtle "why" sentences |
| Sonnet 5.5 | ~$0.002 | ~$94 | ~$570 | ~$4,200 | A few seconds | Better at choosing the sentence that explains, not just reports |
| Opus 5.5 | ~$0.004 | ~$183 | ~$1,100 | ~$8,200 | Slower | Marginal gain over Sonnet for this task |
| Fable 5.1 | ~$0.01 | ~$470 | ~$2,850 | ~$21,000 | Slowest | No gain worth the price for extraction |

**b) Duplicate judgment on sentences (the yes/no question).** About 300
tokens in and a few out per candidate pair. Only pairs that code could not
settle reach a model.

| Model | Per pair (batch) | Role |
|---|---|---|
| **Haiku 5.5** | ~$0.00002 | First look at every candidate pair |
| **Sonnet 5.5** | ~$0.0003 | Second opinion on the pairs Haiku was unsure of (expected to be a few percent). Never decides alone to drop anything |
| Opus 5.5 / Fable 5.1 | ~$0.0006 / ~$0.0015 | Not needed for a yes/no on two sentences |

**c) Figures:** code only. **$0.**

**Ease of implementation** is the same for every model: same SDK, same
`anthropic_client.py`, same batch path (`claude_batch.py`). The model is a
setting (`INVENTORY_MODEL`, proposed). What costs build time is the code
around the model: parsing figures, normalising units, the candidate search
and the reversible marks. That work is the same whichever model runs.

**Recommendation:**

1. **Haiku 5.5 at `low` effort, batched,** for extraction and the first look at
   duplicates.
2. **Sonnet 5.5** as the second opinion on uncertain pairs only.
3. **Code** for every figure.

Expected cost: about $5 a month at 1,000 listeners, $30 at 10,000, about $250
at 100,000.

Before turning it on, build a small eval: about 200 real packets, extracted
and hand-checked for missed figures, wrong merges and kept duplicates. Switch
a step to Sonnet only where the eval shows Haiku falling short.

### Settings it will need (proposed names)

| Setting | What it is |
|---|---|
| `ARCHIVE_BACKEND` | `sqlite` (default, tests and staging) or `atlas` |
| `MONGODB_URI` | The Atlas connection string, delivered through `FAM_SECRETS`, never typed into a file ([credentials-fetched]) |
| `INVENTORY_MODEL` / `INVENTORY_REVIEW_MODEL` | `claude-haiku-5-5` / `claude-sonnet-5-5` |
| `INVENTORY_DAILY_DOLLARS`, `INVENTORY_DAILY_ARTICLES` | The ceilings |
| `INVENTORY_DEDUPE_DRY_RUN` | `1` until a hand-checked sample says the pass is safe |
| `INVENTORY_SUPERSEDED_DAYS` | How long a merged record is kept before it is really deleted (30) |

### Open questions for the owner

- ~~Should a repeating article count as a corroborating source?~~ **Answered
  2026-10-24: yes, as a link and a count, no text** (responsibility 4).
- How long before rollups (responsibility 6) start: 90 days, or longer?
- The search vendors' terms on storing excerpts (Parallel, Perplexity):
  check them before the archive stores anything from them.

---

## Template for the next agent

```markdown
## N. <Agent name>

**Status:** planned / building / live (§ in PROBLEMS.md)

### Purpose
One paragraph: the job, and who benefits.

### Where it sits
What it reads, what it writes, what runs before and after it.

### Responsibilities
Numbered, each with its rules.

### What it must never do
The lines it does not cross, citing the CLAUDE.md rule ids.

### Model options for this agent
The kinds of work it does; for each, a table of models with cost at the
§305 volumes, latency and effectiveness; then the recommendation and the
eval that would change it.

### Settings it will need
### Open questions for the owner
```
