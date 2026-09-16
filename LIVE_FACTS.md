# Live facts — rapidly changing information

Everything about the subsystem that answers questions an article index is
structurally too slow for: scores, prices, vote counts.

**The rule this whole subsystem exists to keep:**

> FAM must never confidently invent a current or recent fact when the system
> does not have authoritative, sufficiently fresh evidence for that fact.

And the corollary that is easier to get wrong, and that almost every mechanism
below is a form of:

> **FAM must never infer current-world facts from the absence of current-world
> evidence.** Not having a scores provider is not the game having no score. A
> search that missed next week's fixture is not the fixture being unannounced.
> A provider that timed out is not an event that did not happen.

## Why it exists

A game ends and the scoreboard knows instantly. The recap that says so is
written, published and indexed later. In between, a search returns the
*preview* — and that is not weak evidence of a result, it is evidence that
there is no result yet. FAM read one as the other and wrote a final score for
a game in its third quarter (PROBLEMS.md §88). Same shape, shorter fuse, for a
price; same shape, longer fuse, for a vote count.

## The shape

    episode_intelligence  →  Brief.live_domain, Brief.outcome_dependent
                             (what the REQUEST is; never what the world is)
    live_facts.lookup     →  LiveLookup: one of seven outcomes
      live_sources        →  resolve(brief) → Entity
                             fetch(entity)  → LiveFacts
    script_generator      →  LiveLookup.as_prompt_block(), always
    cache.ttl_for         →  how long the episode keeps, from the status

`live_facts.py` is the seam: routing, freshness, caching, failure semantics,
and no vendor. `live_sources.py` is where providers and configuration live.
Keeping them apart is deliberate — mixing them means the rules acquire a
vendor's exceptions, which is how a fallback gets buried somewhere nobody
looks (§51, §61).

## Status semantics

A closed vocabulary, in `live_facts.STATUSES`:

| status | means | a result may be spoken? | cacheable? |
|---|---|---|---|
| `scheduled` | has not started | no | briefly (30 min) |
| `in_progress` | under way | **no** | **never** |
| `final` | finished | yes | normally (24h) |
| `unknown` | nobody established it | **no** | volatile only |

Closed because downstream *switches* on it. A free string means a provider
returning `"In Progress"`, `"live"` or `"1H"` silently misses every comparison
and behaves as `unknown` without anyone noticing. `normalise_status()` maps at
the boundary; anything unrecognised becomes `unknown`, never a guess.

**`unknown` is not "probably fine".** It is the state in which no result may
be spoken. A provider whose vocabulary nobody mapped degrades to silence
rather than to a confident wrong tense.

### Who may set it

**Only evidence.** This is the line §88 and §89 both turn on:

* **EI may decide** that the *request* wants a result —
  `Brief.outcome_dependent`. That is a property of the question, decidable
  with nothing retrieved. "How is the Chiefs game going" is outcome-dependent
  whether the game finished an hour ago, is in its third quarter, or kicks off
  tonight.
* **EI may not decide** that an event is scheduled, in progress or final. It
  has retrieved nothing and its knowledge is months old. There is no `status`
  field in `BRIEF_SCHEMA` and a test asserts there never is — a field that
  does not exist cannot be filled in by a persuasive model.

## Freshness

`MAX_AGE_SECONDS` per domain: sports 120s, markets 300s, elections 1800s. One
universal threshold would be wrong for at least two of the three — a score is
stale in two minutes, a vote count published in hourly batches is not.

Checked in code before the writer sees anything. A timestamp the model is
asked to judge for itself is a timestamp that gets judged generously. Data
past the limit is **withheld** and the outcome becomes `stale` — stale live
data is more dangerous than none, because it arrives with a timestamp and
outranks the packet.

`delayed_seconds` is separate and is about design, not age: free market-data
tiers are typically fifteen minutes behind. Nonzero means the prompt says
**delayed**, never "current". "Current as of 10:32" and "last updated fifteen
minutes ago" are different sentences and the difference is the product.

## The seven outcomes

`LiveLookup.outcome` — each renders a different block to the writer:

| outcome | what happened |
|---|---|
| `facts` | a fresh, usable state |
| `not_configured` | no provider for this domain on this deployment |
| `no_entity` | the provider does not cover this thing |
| `no_facts` | it covers it and is reporting nothing |
| `provider_failed` | it broke |
| `timeout` | it did not answer in time |
| `stale` | it answered too late to describe *now* |

`Optional[LiveFacts]` collapsed six of these into `None`, so the writer was
told the same nothing by all of them — and in practice told nothing at all.
The failures are the point.

`lookup()` returns `None` for exactly one thing: the brief names no live
domain, so the question does not turn on a live state.

## Cache rules

`cache.ttl_for(query, *, live_status, outcome_dependent, recency_days)`.
**Zero means do not cache.** Precedence, most authoritative first:

1. **Live status.** `in_progress` → 0. `scheduled` → 30 min. `final` → normal.
2. **`outcome_dependent`** → volatile (15 min). *This is the half that works
   with no provider configured, and it is what actually fixes the reported
   bug today.*
3. **`recency_days <= 1`** → volatile.
4. **The keyword list**, unchanged, as the floor for paths with none of the
   above (`EPISODE_INTELLIGENCE=0`, offline `write.py`, `seed_demo`).

**Do not fix a cache-staleness bug by adding keywords.** §76 settled that: a
keyword list can always be widened by one more word, and the next query it
misses is already written. `"Chiefs game"` has no volatile word in it and was
cached for 24 hours; `"Chiefs"`, `"game"` and `"score"` would all have missed
`"how is the match going"`.

`in_progress` is uncacheable rather than briefly cached because `cache.recent()`
is the Explore feed — a cached in-progress episode is not merely re-served, it
is *published* as a finished episode.

The volatility facts reach the write site on `ScriptNotes`, not on the plan:
the caller holds the **unprepared** plan (`stream_sentences` rebinds it via
`prepare`, and `_answer_first` derives two more), so `plan.brief` and
`plan.live` are always `None` there.

## Entity-level caching

Live facts are cached by **entity**, never by the listener's wording.
"How are the Chiefs doing", "what's happening in the Chiefs game" and "Chiefs
score" resolve to one entity and share one provider call — cost per *event*,
not per listener. TTLs: in-progress 10s (enough only to collapse a burst of
simultaneous listeners), scheduled 300s, final 900s, `unknown` never.

Resolutions cache separately and for an hour: a game id does not change.

## Failure semantics

* A provider **never** prevents an episode. Everything is caught.
* Per-provider timeout `LIVE_TIMEOUT_SECONDS` (1.5s), whole-lookup
  `LIVE_TOTAL_TIMEOUT_SECONDS` (2.5s). This sits in front of the first word;
  the one-sentence spec was amended once for EI and not again.
* One provider failing does not stop the next being tried.
* A failure outranks a missing provider in what gets reported — it is the more
  informative thing to tell the writer and the log.
* The live lookup runs **concurrently** with research (`prepare` gathers them).
  They both read `plan.brief` and neither reads the other's output.

## Metering

`Usage.add_live_call(calls, cost)` → `live_calls`, `live_cost`, priced into
`Cost.live`. A third category beside model calls and research, because a
blended figure cannot answer "what would turning on live scores cost".

**A cache hit is never counted.** The count happens in `live_facts._ask` at
the fetch, not at the ask — which is why it lives in the seam rather than in a
provider that cannot see the cache.

Existing databases get the columns by additive `ALTER TABLE`, guarded on
`PRAGMA table_info`, same shape as the migrations in `cache.py`.

## Prefetch restrictions

Two rules, both in `prefetch.Prefetcher._warm`:

1. **Prefetch never calls `live_lookup`.** A warmed live fact is stale by the
   time it is tapped — that is what "live" means — so warming one would spend a
   provider call speculatively in order to bake a score into a script served
   hours later.
2. **A script is never warmed for an outcome-dependent question.** The *brief*
   is kept (it is a claim about what is being asked, and it keeps), the script
   is not. The ledger counts these as `skipped_volatile`, separately from
   failures: nothing went wrong, the guess was simply not the kind of episode
   that keeps.

## Configuration

    LIVE_FACTS=1                      # the registry; leave on
    LIVE_SPORTS_PROVIDER=             # empty = none, and none is shipped
    LIVE_MARKETS_PROVIDER=
    LIVE_ELECTIONS_PROVIDER=
    LIVE_FAKE_SPORTS_STATUS=in_progress
    LIVE_TIMEOUT_SECONDS=1.5
    LIVE_TOTAL_TIMEOUT_SECONDS=2.5
    LIVE_CACHE_IN_PROGRESS_SECONDS=10.0
    LIVE_CACHE_SCHEDULED_SECONDS=300.0
    LIVE_CACHE_FINAL_SECONDS=900.0

`live_sources.install()` runs from the app lifespan and is re-runnable. A
provider name this build does not know is **reported, not raised**: the server
starts, `/api/health` says the domain is unserved, and episodes are still
answerable. Failing to boot would be the worse bug.

`/api/health` reports three states, never two:

* **configured and operational** — `live_facts.ready` names the domain
* **configured but unavailable** — the source is registered and `diagnose()`
  says why it cannot serve
* **not configured** — `live_sources.configured` is empty for that domain

"Not configured" is never rendered as "there is no live information in the
world", in the health report or in the prompt.

## Adding a provider

1. Subclass `live_facts.LiveSource` in `live_sources.py`.
2. Set `name`, `domain`, `cost_per_call`, `delayed_seconds`.
3. Implement `diagnose()` — cheap, no network, runs on every lookup.
4. Implement `resolve(brief) -> Entity | None` using **the provider's own
   catalogue search**. Never an identifier a model produced: a hallucinated
   game id or ticker does not fail, it returns somebody else's state, fresh
   and authoritative and completely wrong, and nothing downstream can catch it.
5. Implement `fetch(entity) -> LiveFacts | None`. Map the provider's status
   vocabulary onto `STATUSES` here. Set `as_of` to when the provider
   *observed* the state, not when you asked. Write `facts` as short spoken
   sentences — no scorelines in a shape nobody says aloud, no abbreviations,
   no hostnames.
6. Implement `verify()` — a real request.
7. Add it to `BUILDERS`, add the env var to `config.py`, `.env.example` and
   `tests/conftest.py`'s `FAM_ENVIRONMENT` (a hermetic test fails otherwise,
   in both directions).
8. **Omit fields you do not have. Never fill them in.**

## Verifying a provider

    python tools/verify_live.py
    python tools/verify_live.py --domain sports --query "Chiefs game"

Performs a real resolve and fetch, prints the status, the age against the
domain limit, the delay, the cost and the facts, and warns on the two things
most likely to be silently wrong in a new provider: an unmapped status, and a
naive or future `as_of`.

§52's rule: a check that answers a cheaper question than the one being asked
and then reports OK is worse than no check. `diagnose()` is the cheap question
and must stay cheap; `verify()` is the real one and is **not** run at startup —
a network call at boot turns a provider outage into a server that will not
start.

A pass means credentials work, the network is reachable, the response parsed,
and the status and timestamp are in a shape this build understands. It does
**not** mean the data is correct.

## Testing in-progress events

    python -m pytest tests/test_in_progress.py -q

Seven cases, matching the seven ways this goes wrong: in progress, pregame,
final, no provider, stale data, provider failure, and a settled fact one
search missed.

End to end with a script, which needs a key:

    LIVE_SPORTS_PROVIDER=fake python tools/ei_eval.py --only in-progress --script
    LIVE_FAKE_SPORTS_STATUS=final LIVE_SPORTS_PROVIDER=fake python tools/ei_eval.py --only in-progress --script
    python tools/ei_eval.py --only in-progress --script    # no provider at all

The fake scoreboard is selected explicitly or not at all — never a fallback,
which is §51 and §61 applied to facts. It names itself `NOT REAL DATA`, and
that name reaches the prompt, the log and the health report.

## Status of this subsystem

**No real provider is connected.** The architecture is ready for one; live
world data is not available. `/api/health` says so, `tools/verify_live.py`
says so, and the writer is told so on every live question.

Do not describe FAM as supporting live scores until a real provider is
configured and returning them.

---

# Configured providers

Added in PROBLEMS.md §91. **None has been verified against its live service
from this build** — the container's egress proxy blocks every host below, so
each adapter is written from the vendor's documented shapes and pinned against
recorded payloads in `tests/test_live_providers.py`. Run
`python tools/verify_live.py` somewhere with network before trusting any.

| domain | name | provider | credential | notes |
|---|---|---|---|---|
| sports | `api-sports` | API-Sports | `API_SPORTS_KEY` + `API_SPORTS_SPORT` | self-serve; **one API per sport** |
| sports | `sportsdataio` | SportsDataIO | `SPORTSDATAIO_KEY` | player-level stats; sales-gated |
| markets | `finnhub` | Finnhub | `FINNHUB_KEY` | ~20 min delayed; knows if the market is open |
| markets | `alpha-vantage` | Alpha Vantage | `ALPHA_VANTAGE_KEY` | ~15 min delayed |
| elections | `polymarket` | Polymarket | none | **forecast, never a result** |
| elections | `ap` | AP Elections | — | declared, unimplemented: quote-only |
| elections | `ddhq` | Decision Desk HQ | — | declared, unimplemented: quote-only |

## The rule that keeps Polymarket safe

A prediction market returns what people are **betting**, not what is true. So
every fact it produces carries `kind="prediction-market"` and
`status=UNKNOWN`, always — and `unknown` is the status in which no result may
be spoken.

That is structural rather than a request, and it matters because of a specific
temptation: a live in-game win-probability line moves with the score, so it
*reads* like the score. *"Chiefs at 94%, so they must be winning"* is
PROBLEMS.md §88 returning through a side door. A market may colour an episode;
it may never close one.

**And `unknown` is only half of it** (§93). Every other live fact ends its
block with "this is the most authoritative thing you have been given; where it
and the articles disagree, this is true" — earned, because a scoreboard was
*observed* and the article about it was written later. A market was observed
too, but what it observed is what people **expect**, so it is the newest thing
in the prompt and the least authoritative thing in it. That combination is
unique to forecasts, and handing one the standard paragraph would let a price
overrule an article reporting the actual result. `PREDICTION_MARKET` is a named
constant precisely because `as_prompt_block` switches on it, and that one kind
is told the opposite: **where it and the articles disagree, the articles win.**
`status` forbids *speaking* a result; this decides who wins a disagreement, and
they are different questions.

## The routing vocabulary lives in exactly one place

`LIVE_DOMAINS` is it. A source declares one of those, `Brief.live_domain` names
one of those, and `BRIEF_SCHEMA` builds its enum by *reading the tuple* rather
than repeating it.

That is not tidiness. It was a copy, it drifted, and the result was a provider
that could be configured, registered, diagnosed, verified and reported healthy
while being structurally unable to receive a single question — `elections` was
in the tuple and not in the enum, and the enum is enforced strictly. Nothing
failed; it silently did not happen. PROBLEMS.md §93 has the whole of it.

Adding a domain is therefore two edits and no more: the tuple, and a sentence
in the EI prompt saying when to choose it. A test walks `LIVE_DOMAINS` and
fails if a domain is routable but never explained to the model, because an enum
value with no instruction behind it is one that never gets picked.

## Why the election providers are empty

Neither AP Elections nor Decision Desk HQ publishes API pricing, and neither
has an open endpoint — both are sales-gated. There is nothing to write an
adapter against, and guessing an endpoint would be worse than nothing. They
are declared so the gap is visible with the thing a person would have to do to
close it, rather than elections looking like a domain nobody considered.

Polymarket covers election *interest* at zero cost in the meantime. It does not
cover election *results*, and the `kind` field is what keeps those apart.

## Status mapping is done at the boundary

Each adapter translates its vendor's vocabulary into `live_facts.STATUSES` in
its own `fetch`. Anything unrecognised becomes `unknown` — never a guess — so a
provider that changes its codes degrades to silence rather than to a confident
wrong tense.

    API-Sports     NS/TBD -> scheduled   1H/2H/HT/ET/P/LIVE -> in_progress
                   FT/AET/PEN -> final   anything else -> unknown
    SportsDataIO   Scheduled -> scheduled  InProgress -> in_progress
                   Final/F-OT -> final     anything else -> unknown
    Finnhub / AV   always unknown — a price is not an event
    Polymarket     always unknown — a forecast is not an event


## API-Sports is four APIs, not one

Different **host**, **response shape** and **status vocabulary** per sport:

| sport | host | path | score field | unit |
|---|---|---|---|---|
| `american-football` | `v1.american-football.…` | `games` | `scores.home.total` | points |
| `football` (soccer) | `v3.football.…` | `fixtures` | `goals.home` | goals |
| `basketball` | `v1.basketball.…` | `games` | `scores.home.total` | points |
| `baseball` | `v1.baseball.…` | `games` | `scores.home.total` | runs |

Writing one adapter against `v3.football` and calling it "sports" is how the
Chiefs get looked up on a soccer endpoint. `live_sources.SPORTS` is that table
and a test asserts no two sports share a host, that both score shapes are read,
and that a status code borrowed from another sport does **not** map.

`sport_for()` routes on explicit sport words, falling back to
`API_SPORTS_SPORT`. **"Chiefs game" names no sport**, so it falls back — team
name routing would need a maintained roster of every league, and a stale one is
worse than a default someone chose. Resolving sport from team wants the
provider's own cross-sport team search; that is the next step, not guessed at.

The entity id carries its sport (`american-football:9`) because a bare game id
is meaningless without knowing which API issued it, and `fetch` gets only the
entity.

## Finnhub knows whether the market is open

One extra call per lookup, and it changes the sentence because it changes the
fact:

| session | the episode says |
|---|---|
| open | "is trading at" |
| closed | "closed at" |
| unknown | "was most recently at" |

A closing price described as "is trading at" is a small lie a listener catches
immediately — and `None` for an unknown session is reported as "most recently"
rather than asserted either way.

Its symbol resolution prefers ordinary common stock over the warrants, units
and foreign listings that share a prefix, so "Apple" finds `AAPL` and not
`AAPL.SW`. Never a guessed ticker: a hallucinated symbol returns somebody
else's price, fresh and confident and wrong.

## SportsDataIO's free key returns scrambled data

Worth naming because it is a trap: a trial key *looks* like it works, and
`verify()` cannot tell the difference. `diagnose()` says so whenever the key is
set. Do not run it in production on a trial key.
