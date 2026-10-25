# FAM agents — the guidelines for each one

| | |
|---|---|
| **Status** | Planning document. No agent here is built yet |
| **Started** | 2026-10-24 (PROBLEMS.md §307); the editor in chief and the script supervisor added 2026-10-25 (§310) |
| **Audience** | The owner, and any Claude session asked to design, build or change one of these agents |

FAM will have many agents. An *agent* here is a worker that uses a language
model for one job with a name and a section of its own: digesting, tidying,
editing, checking. Some run in the background (the Database inventory
specialist). Others sit on a listener's path, between the tap and the first
word (the editor in chief, the script supervisor on searchFAM).

**Latency is not ruled on yet** (the owner, 2026-10-25). The rule that
nobody waits on an agent was withdrawn: agents may sit on a listener's path,
and the latency they add will be minimised later. Until then each agent says
where it sits and what it adds in front of the first word, so the cost is
known when that work starts.

**How to use this file.** In a new session, say *"read `docs/FAM_AGENTS.md`
and work on the Database inventory specialist"* (or whichever agent). Each
agent has its own section: what it is for, what it must never do, and which
model is right-sized for it. Add an agent by copying the template at the end.
When an agent is built, change its **Status** line and log the build in
`PROBLEMS.md`.

---

## Rules every agent follows

These come from settled constraints in `CLAUDE.md`. An agent that breaks one is
wrong even if its output looks better. Rule 2 governs what an agent *stores
or hands on as evidence*; an agent that writes the episode (the editor in
chief, if it is given that job) writes as the writer does, under every
writer rule.

1. **An agent adds quality; it never costs an episode.** Every agent has a
   ceiling (time, tokens, dollars) and a fallback: when it fails, times out
   or answers nonsense, the pipeline carries on as it did before the agent
   existed, and says so ([ei-before-search], [failures-visible]).
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
between Claude models.** What differs is price, speed and how well each one handles the
task.

### Options outside Claude

Each agent's section also lists models from other companies, and
**specialised models** that do one part of the job (rank sentences, check a
claim against a source, pull a number out of text) rather than a general
model that does all of it. A specialised model is often faster and cheaper
for its part, and cannot invent text, because it does not write any.

**External prices below are bands, not quotes.** They were not checked for
this document, and these vendors change prices and model names often. Look
the price up on the vendor's own page and write it into `metering.PRICES`
before anything is switched on.

| Band | Roughly, per million tokens in / out | Examples (check current names) |
|---|---|---|
| $ | under $0.50 / $2 | Small "flash", "mini" and "nano" tiers (Google Gemini Flash / Flash-Lite, OpenAI's mini and nano models, Mistral Small); open-weight models (Llama, Qwen, Mistral, DeepSeek) on hosted inference |
| $$ | $1-3 / $5-15 | Mid tiers: Gemini Pro, OpenAI's main GPT model, Mistral Large, Claude Sonnet |
| $$$ | above that | The top reasoning tiers from each vendor |

The kinds of model that recur below:

| Kind | Examples | What it is good for here |
|---|---|---|
| **Fast hosted inference** for open-weight models | Groq, Cerebras, Together, Fireworks (Llama, Qwen, gpt-oss and similar) | Very high tokens per second, so the fastest option when a listener is waiting. Quality depends on the model chosen; usually below Claude on careful writing |
| **Rerankers** | Cohere Rerank, Voyage rerank, Jina Reranker; open cross-encoders such as `bge-reranker` | Scoring which sentences answer a question. Tens of milliseconds, priced per search or free when self-hosted, and they only ever choose. A natural fit for "extract, never invent" |
| **Grounding / claim checkers** | MiniCheck and Bespoke-MiniCheck, Vectara HHEM, Patronus Lynx (open weights); AlignScore; NLI cross-encoders (DeBERTa MNLI) | "Does this source sentence support this script sentence?" Small, fast, cheap, built for exactly that question |
| **Number and entity extraction** | spaCy, GLiNER, `quantulum3`, Duckling; Google Cloud / AWS Comprehend entities | Pulling figures, units, dates and names out of text in code-like, deterministic ways |
| **Embeddings** | The installed `all-MiniLM-L6-v2` (`embeddings.py`), Voyage, OpenAI and Cohere embeddings | Finding candidates (duplicates, related history) before any model judges them |
| **Other general models as a second judge** | Gemini, GPT | A checker from a different model family is less inclined to approve writing that sounds like its own |

**What an external model costs in FAM beyond its price.** Read this before
choosing one. Each of these is work a Claude model does not need:

- **Consent.** `consent.py` asks the listener before *their words* go to "the
  AI provider", and the wording names who receives them (App Store 5.1.2(i),
  [app-review-safety]). Sending a listener's question, attachment or Go
  Deeper text to another company is a new recipient: new wording, a new
  `VERSION`, everyone asked again, and the Privacy Policy updated. An agent
  that only sees FAM's own material (packets, scripts written from FAM's own
  questions) does not trigger this; one that sees a search's words does.
- **Data terms.** Check retention and training-on-data terms; prefer a
  zero-retention agreement for anything carrying listener words.
- **A second SDK and client**, its own timeouts, retries, refusal handling
  and structured-output support (some vendors' JSON modes are looser than
  Claude's schema-constrained output). Metering needs its price in
  `metering.PRICES` or the cost shows as unpriced ([metering]).
- **Credentials and staging.** Its key comes through `FAM_SECRETS`
  ([credentials-fetched]) and must be added to `spend_guard.PAID_CREDENTIALS`
  so staging cannot use it ([zero-spend-staging]).
- **Self-hosting** an open-weight model means a GPU. FAM's only GPU is the
  voice pod; sharing it would put an agent in competition with speech.
- **Prompt caching and batch discounts** differ by vendor; the Claude
  numbers in each table assume both.

**The rule of thumb:** a specialised model is worth its integration cost
when it replaces a whole step with something faster and incapable of
inventing (a reranker for selection, a claim checker for support). A general
model from another vendor is worth it mainly as an independent second
judge, or when its speed on fast hosted inference buys back latency a
listener is waiting through.

---

## 1. Database inventory specialist

**Status:** planned, not built. Designed in conversation on 2026-10-24
(§307). **The archive it works on is built** (§308, `history_archive.py`,
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
  far (§307):
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
years of history on a small Atlas tier (§307's projections).

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
§307, which are estimates from `docs/FINANCIAL.md` §4 and not measured:
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

**Options outside Claude** (bands and caveats in the shared reference):

| Part | Option | Price | Latency | Effectiveness and fit |
|---|---|---|---|---|
| a) Extraction | Gemini Flash-Lite / Flash, OpenAI nano / mini, batched | $ | Batch | Comparable job. Haiku 5.5 at $0.10 / $0.50 is already in the cheapest band, so the saving is small and the integration cost (second SDK, `metering.PRICES`) is real |
| a) Extraction | Open-weight model (Qwen, Llama) self-hosted or on Together / Fireworks | $ | Batch | Cheapest at very high volume (100k listeners); weaker at choosing the sentence that explains *why*. Needs an eval before trust |
| a) Figures, help for code | `quantulum3`, spaCy, Duckling | Free, local | Milliseconds | Find numbers, units and dates for the code that normalises them. Deterministic, which suits rule 3 |
| b) Duplicate yes/no | An NLI cross-encoder (DeBERTa MNLI), entailment both ways = the same claim | Free, CPU | Milliseconds | Purpose-built for "do these say the same thing". A strong first look; uncertain pairs still go to Sonnet |
| b) Candidates | The installed sentence model (`all-MiniLM-L6-v2`), Voyage or Cohere embeddings | Free / $ | Milliseconds | Finding candidate pairs is already embeddings' job (`history_archive` embeds every fact) |

None of these sees a listener's words, so none needs a consent change.

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

## 2. Editor in chief

**Status:** planned, not built. Named by the owner on 2026-10-25 (§310). It
may later be combined with the script supervisor (3); that is the owner's
decision, and the end of section 3 says what combining would cost.

### Purpose

Turn everything gathered for an episode into the episode. FAM's writer today
reads the packets raw and plans the piece in its own head, in front of the
first word. The editor in chief reads both packets, decides what the episode
is, which material carries each part of it, and in what order, and then
either hands the writer that plan or writes the episode itself. The writing
is the product ([writing-is-the-problem]), and most of what was wrong with it
came from what the writer was given ([inputs-not-prompt]). This agent is the
place to fix the inputs.

### Where it sits

- **Reads:** the brief (intent, subject, `category_path`, cautions, the
  outline), **packet one** (the search packet, live facts, players report,
  forecast, local news, weather) and **packet two** (the archive's recall,
  `history_archive.py`, §308, and the `history.py` seam, §309).
- **Runs:** after both packets are in, before the writer's first token.
  - On **searchFAM** a listener is waiting, so everything it adds is in
    front of the first word.
  - On **myFAM, DailyFAM, Trending and the write-ahead**, it runs before
    anyone taps and costs the listener nothing ([spec-amended-for-search]).
- **What it replaces or absorbs:** the evidence cut (`evidence_digest.py`,
  off by default), the code consolidation of the two packets
  (`history.consolidate`), and the planning half of the EI outline. Each of
  these stays as its fallback.
- **Writes:** an **editorial plan** for the writer. If the owner gives it the
  writing too, it writes **the script**.

### Responsibilities

**1. Digest both packets into one plan.**
- Decide the answer: what the listener asked, and what the material says
  ([satisfy-first], [never-withhold]).
- Decide the beats, in order, within what the length buys
  ([depth-not-words], [duration-ceiling]). A beat with nothing behind it is
  dropped, never filled.
- For each beat, name the **numbered source sentences** that carry it:
  "beat 2, the turning point: [4], [7], [R2]". The plan points at material;
  it does not restate it.
- Fill the opening slots (who, what, when) with sentence numbers
  ([situate], [opening-last]), and name the concrete thing the last line
  lands on ([speak-from-inside], [endings-do-not-tease]).

**2. Keep the order of trust.**
- The live state outranks the fresh packet, and the fresh packet outranks
  the archive ([live-facts], [two-packets]).
- Where sources disagree, the better grade wins, then the more recent
  ([recency-credibility]). That choice is made **by code from the grades and
  dates**, never by the model's preference. The plan says which sentence won.
- An archive record says what was true when it was dated. It is history,
  never the current state, and never the answer to "where does X stand now".

**3. Keep status honest.**
- A preview, an expectation or "is expected to" is never an outcome
  ([started-not-finished]).
- On an outcome-dependent question, the plan either points at a sentence
  that reports the outcome as an outcome, or plans the episode about
  something that has not finished. It never chooses between those by
  guessing.

**4. Say what is thin, privately.**
- What the brief asked to establish and nobody found is named to the writer,
  with the rule that goes with it ([missed-not-absent]): a volatile thing is
  never supplied from memory, a settled thing may be, and the gap is never
  announced to the listener.

**5. If it writes the episode, write as the writer does.**
- Every writer rule applies, and every guard still runs on what it writes
  (`OpeningGuard`, `ScoreGuard`, `FigureGuard`, `MaterialGuard`).
- **No tools on the call that writes what is spoken**
  ([nothing-before-material]). Any looking-up, ranking or checking is done
  before that call starts.
- It streams sentence by sentence into the voice, exactly as the writer's
  output does today (`_ScriptReader`).

### What it must never do

- Put a fact into the plan in its own words. The plan names sentences by
  number; code shows the writer those sentences ([started-not-finished],
  rule 2 above).
- Choose between conflicting sources by its own judgment, or supply a figure
  from memory ([score-guard], [live-facts]).
- Put a hostname, URL or "the sources say" in front of the voice
  ([recency-credibility]).
- Tell the listener what was not found ([missed-not-absent]).
- Plan a teasing ending, a rhetorical question, or a forecast as the last
  line ([endings-do-not-tease]).
- See anything personal it does not need: attached documents stay with the
  writer path they already use, and a private search keeps every
  private-search rule ([op-attachments], [private-search]).
- Change the cache key. Its plan is derived from the request, like the
  evidence cut, and is **never in `key_for`** ([prefetch-same-key]).
- Cost an episode. On any failure the writer reads the packets as it does
  today (rule 1).

### Model options for this agent

**Volume assumed:** per episode written, priced per 10k / 100k / 1M episodes
a month. Episodes per listener are not measured yet; multiply by your own
estimate.

**Size of one call:** about 10k tokens in (the instructions, cached, plus
both packets) and about 1k out (the plan, plus thinking). If it also writes
the script, the writer's call is replaced rather than added to, so the
episode costs roughly the same as one editor call.

| Model | Per episode | 10k / 100k / 1M episodes a month | Latency on search | Effectiveness |
|---|---|---|---|---|
| **Claude Haiku 5.5** | ~$0.0015 | ~$15 / $150 / $1,500 | One more Haiku call in front of the first word (not measured; the brief, a similar call, has an 8 s ceiling) | Good at selection and ordering; the writer is already Haiku, so this mostly moves the planning out of the writer's thinking |
| **Claude Sonnet 5.5** | ~$0.03 | ~$300 / $3,000 / $30,000 | Slower than Haiku | Better editorial judgment: which beat matters, what to cut. **The likeliest model to improve the writing** |
| Claude Opus 5.5 | ~$0.06 | ~$600 / $6,000 / $60,000 | Slower again | Strongest planning; worth testing on the editions first, where nobody waits |
| Claude Fable 5.1 | ~$0.15 | ~$1,500 / $15,000 / $150,000 | Slowest | Not right-sized for one episode's plan |
| Batch (any Claude model), editions only | half the above | | Within the hour | The Trending and DailyFAM editions already batch their writers ([writer-savings]) |

**Options outside Claude:**

| Option | Price | Latency | Effectiveness and fit |
|---|---|---|---|
| A **reranker** (Cohere Rerank, Voyage, Jina; or a self-hosted `bge-reranker`) for the selection half | $ or free | Tens of ms | Scores every sentence of both packets against the question and each beat. Cannot invent, so it is the safest way to do responsibility 1's "which sentences". It does not plan; pair it with a small model or code |
| Gemini Flash / OpenAI mini on the planning half | $ | Fast | Comparable to Haiku; little price gain. Needs the consent change if it sees the listener's question (it does on searchFAM) |
| An open-weight model on **fast hosted inference** (Groq, Cerebras) | $ | Fastest tokens per second | The way to buy latency back on searchFAM if Sonnet-level planning proves too slow. Quality needs an eval. Sees the listener's question, so needs the consent change |
| Gemini Pro / OpenAI's main GPT model | $$ | Like Sonnet | An alternative to Sonnet for the editions; no clear reason to prefer it without an eval |

**Recommendation:**
1. Build the plan format first: beats, numbered sentences, opening slots,
   landing line. It is the same whichever model fills it.
2. Start on the **editions and the write-ahead** with **Sonnet 5.5, batched**:
   nobody waits, and that is where editorial judgment is cheapest to try.
3. On **searchFAM**, start with **Haiku 5.5**, plus a **reranker** for the
   sentence selection if the eval shows Haiku missing material. Move search
   to Sonnet only when the latency work (the owner's, later) makes room.
4. Measure with `write.py` (the brief and the packets print above the
   script, [brief-above-script]) on a fixed set of questions: with and
   without the editor, scored by the script supervisor's checks.

### Settings it will need (proposed names)

| Setting | What it is |
|---|---|
| `EDITOR` | `0` / `1`, off by default until the eval says it helps |
| `EDITOR_MODEL`, `EDITOR_SEARCH_MODEL` | The model for the editions and the write-ahead, and the one for searchFAM |
| `EDITOR_WRITES` | `0` (it plans and the writer writes) or `1` (it writes) |
| `EDITOR_TIMEOUT_SECONDS`, `EDITOR_AHEAD_TIMEOUT_SECONDS` | Its ceilings; past them, the writer reads the packets as today |
| `EDITOR_RERANKER` | Empty, or the reranker used for selection |

### Open questions for the owner

- Does it plan only, or plan and write (`EDITOR_WRITES`)?
- Is it on searchFAM from the start, or on the editions first?
- Combine it with the script supervisor (end of section 3)?

---

## 3. Script supervisor

**Status:** planned, not built. Named by the owner on 2026-10-25 (§310).

### Purpose

Check the finished script before (or, on searchFAM, while) a listener hears
it: is every claim supported by the material, is the status of every event
honest, and does the episode keep the rules of what an episode is? It is the
quality check FAM does not yet have. Today only code guards check scripts,
and only for scores, market figures, openings and material-free sentences.

### Where it sits

- **Reads:** the script, the plan if the editor wrote one, and **exactly the
  material the writer had** (both packets, the live blocks, the brief). It
  never reads anything the writer could not see. A checker with more
  information marks correct writing as wrong.
- **Runs after the writer, in one of three places:**
  - **Ahead of the tap** (editions, the write-ahead, prefetched scripts):
    the whole script is checked before it is cached or offered. A failed
    check can hold the episode for **one** rewrite, then the better of the
    two is kept.
  - **On searchFAM, while streaming:** the script is spoken sentence by
    sentence as it is written, so a whole-script check would come after the
    listener has heard it. Here it can only check each sentence before it is
    voiced, as the code guards do, which adds time per sentence; or check
    after the episode.
  - **After the episode (an audit):** score the script, log it on `/admin`,
    and when it fails hard, retire the cached copy so the next listener gets
    a rewrite instead of the same mistake ([heard-is-kept] keeps the heard
    one replayable from history).
- **Writes:** a **verdict per check**, with the script sentence numbers, the
  supporting source sentence numbers where there are any, and a severity. It
  does not rewrite. Fixing is the writer's or the editor's job, which keeps
  the checker independent of the thing it checks.

### Responsibilities

**1. Support: every factual sentence traces to the material.**
- Each claim is matched to the source sentences that support it.
- *Unsupported is not false* ([missed-not-absent]): something settled may be
  said from general knowledge, so an unsupported settled fact is noted, not
  failed. An unsupported **volatile** fact (a result, a score, a price, who
  holds a job today) is a hard failure ([live-facts]).
- Figures are checked **by code** against the material, as `ScoreGuard` and
  `FigureGuard` already do; the supervisor adds the sentences around them
  (rule 3).

**2. Status and time.**
- No preview, expectation or "is expected to" told as an outcome
  ([started-not-finished]).
- Relative time ("last night", "this week") agrees with the source's date as
  computed in code ([recency-credibility]).
- History from packet two is told as history, never as the current state.

**3. What an episode is.** Each is a named check with a pass or fail:
- The first two sentences situate who, what and when, and answer what was
  typed ([situate], [opening-last]).
- No orienting, no justifying, no "the sources say", no announced gap
  ([speak-from-inside], [missed-not-absent]).
- Every sentence carries information; no filler or atmosphere
  ([every-sentence-informs], [no-filler]).
- The ending lands on something concrete: no tease, no rhetorical question,
  no forecast as the last line, no summary ([endings-do-not-tease]).
- It closes the question it was asked ([never-withhold]).
- It fits the length; it does not pad ([duration-ceiling]).
- The title and category match what the episode turned out to be about
  ([title-from-content], [one-category]).

**4. Mechanical checks, in code first.** Hostnames, URLs or "according to
[outlet]" in the spoken text; a question mark on the last line; slurs (only
slurs, [slurs-only]); names the voice will mispronounce that have no
respelling ([op-voice]). Code catches these for free. A model is asked only
what code cannot decide.

**5. Report, and feed the writing work.**
- Every verdict is counted on `/admin` by check, by surface and by category,
  so the weakest kind of episode is visible ([failures-visible]).
- Failing scripts are the raw material for `examples/`, the strongest lever
  on the writing ([examples-lever]), and for the editor's eval.

### What it must never do

- Rewrite a sentence itself, or add a fact. It judges; it does not write.
- Judge with information the writer did not have, or with its own memory of
  a volatile fact ([live-facts]).
- Fail an episode on taste alone. Hard failures are an unsupported volatile
  fact, an outcome invented from a preview, a hostname in the speech and a
  figure the material does not hold. Everything else is reported, not
  blocked.
- Loop. At most **one** rewrite per episode; then the better of the two
  versions is kept, and both verdicts are logged.
- Hold a listener on searchFAM without a ceiling; on a timeout the sentence
  or episode goes ahead as it would have without the supervisor (rule 1).
- See a listener's words through an external model without the consent
  change (the shared reference).

### Model options for this agent

**Size of one whole-script check:** about 10k tokens in (the rules, cached,
plus the material and a one-minute script of about 250 tokens) and about 500
out (verdicts).

| Model | Per episode | 10k / 100k / 1M episodes a month | Latency | Effectiveness |
|---|---|---|---|---|
| Code checks (responsibility 4, figures) | $0 | $0 | Milliseconds | Exact, for what code can decide. Always first |
| **Claude Haiku 5.5** | ~$0.0013 | ~$13 / $125 / $1,250 | Fast | Good at mechanical and rule checks; weaker as a judge of writing it would itself have written |
| **Claude Sonnet 5.5** | ~$0.025 | ~$250 / $2,500 / $25,000 | Slower | **The right size for judging the writing** (ending, opening, story); a different model from the Haiku writer, so less inclined to approve its own style |
| Claude Opus 5.5 | ~$0.05 | ~$500 / $5,000 / $50,000 | Slower again | For building the eval and auditing samples, not every episode |
| Batch (any Claude model), audits and editions | half the above | | Within the hour | Audits wait by design |

**Options outside Claude:**

| Option | Price | Latency | Effectiveness and fit |
|---|---|---|---|
| A **grounding checker** (MiniCheck / Bespoke-MiniCheck, Vectara HHEM, Patronus Lynx; AlignScore) for responsibility 1 | Free self-hosted, or $ hosted | Milliseconds to well under a second per sentence | Purpose-built for "is this sentence supported by that source", and small enough to run **per sentence while streaming**. The best fit for support checks on searchFAM. Needs a CPU or GPU of its own (not the voice pod) or a hosted endpoint |
| An NLI cross-encoder (DeBERTa MNLI) | Free, CPU | Milliseconds | A cheaper, blunter version of the above. Good for "does this contradict the source" |
| **Gemini or GPT as a second judge** of the writing (responsibility 3) | $ to $$ | Like Sonnet | A judge from a different family than the writer reduces self-preference. Worth comparing with Sonnet in the eval. Sees only FAM's material and the script, except on searchFAM, where the script answers the listener's question; check whether that needs the consent change before using it there |
| Hosted evaluation platforms (Patronus, Galileo and similar) | Subscription | Varies | Ready-made hallucination and quality evaluators with dashboards. Useful for the eval set; another vendor in the data path for live use |

**Recommendation:**
1. **Code first** for everything in responsibility 4 and every figure.
2. A **self-hosted grounding checker** (MiniCheck-class) for support,
   per sentence. It is fast enough for searchFAM and cannot invent.
3. **Sonnet 5.5** for the writing checks (responsibility 3) on the editions
   and the write-ahead, batched, before anything is cached. Use it as an
   audit on searchFAM until the latency work decides whether it can run
   before speech.
4. Build the eval first: about 100 real scripts, hand-marked against every
   check. Each check earns its place only by catching what the hand marks
   catch.

### Combining it with the editor in chief

One model could plan, write and check. That would be cheaper (one call) and
faster (no second pass). It loses the independence that makes a check worth
having: a model grading its own writing passes what it meant to write. If
they are combined, keep the code checks and the grounding checker separate
from it, since neither shares the writer's blind spots.

### Settings it will need (proposed names)

| Setting | What it is |
|---|---|
| `SUPERVISOR` | `0` / `1`, off by default |
| `SUPERVISOR_MODE` | `ahead` (check before caching), `audit` (after the episode), `stream` (per sentence on searchFAM) |
| `SUPERVISOR_MODEL`, `SUPERVISOR_GROUNDING` | The judge model, and the grounding checker's endpoint or local model |
| `SUPERVISOR_REWRITES` | `1`, the most rewrites per episode |
| `SUPERVISOR_TIMEOUT_SECONDS` | Its ceiling; past it the episode goes ahead unchecked and the miss is counted |

### Open questions for the owner

- On searchFAM: per-sentence checks before speech (slower), or an audit after?
- Which failures may hold an episode for a rewrite, and which are only reported?
- Combine it with the editor in chief?

---

## Template for the next agent

```markdown
## N. <Agent name>

**Status:** planned / building / live (§ in PROBLEMS.md)

### Purpose
One paragraph: the job, and who benefits.

### Where it sits
What it reads, what it writes, what runs before and after it. Whether a
listener is waiting on it, and what it adds in front of the first word.

### Responsibilities
Numbered, each with its rules.

### What it must never do
The lines it does not cross, citing the CLAUDE.md rule ids.

### Model options for this agent
The kinds of work it does; for each, a table of Claude models with cost at
the §307 volumes, latency and effectiveness; a table of options outside
Claude (general models from other vendors and specialised models for a part
of the job, with the costs in the shared reference); then the
recommendation and the eval that would change it.

### Settings it will need
### Open questions for the owner
```
