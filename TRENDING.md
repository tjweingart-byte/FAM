# Trending — what the world is paying attention to

The myFAM row backed by an outside feed, and why it is a separate subsystem
from live facts.

> **Since PROBLEMS.md §139 the row is an *edition*.** Built at 05:00 and
> 17:00 Eastern from GNews (`gnews.py`, `trending_bank.py`), ten stories,
> with their ten episodes written into the shared cache before anybody taps.
> GNews is the only source - there is no fallback. **The live pool never
> reaches this row**: it is Made for you's, and with no edition Trending is
> empty and says why. See **The trending bank** at the end of this file.

> **Since PROBLEMS.md §102 this registry is one source among several rather
> than the row's whole supply.** myFAM's two outward-facing rails are now fed
> by the story pool (`stories.py`, `MYFAM.md`), which sweeps four live sources
> and composes a title and an angle for each; `story_sources.TrendingRegistrySignals`
> reads whatever `TRENDING_SOURCE` installed and hands it over as attention
> signals. Everything below still holds - the contract a source keeps, the
> item shape, the empty states, the cost design - and a configured feed still
> produces the row it always did, because its own `query` and `why_now` ride
> through as `suggested_query` and `suggested_angle`. What changed is that the
> row is no longer *only* this.

## Two rows, two questions

| row | key | answers | comes from |
|---|---|---|---|
| **Trending** | `world_trending` | what is the world talking about | `trending.py` → an outside feed |
| **What FAM can't stop listening to** | `most_played` | what are *FAM's* listeners playing | `topics.rank_most_played` → the event log |

The second already existed under the key `trending`, which is why it was
renamed: it was always FAM's own popularity, never the world's.

A listener reads these differently and they can disagree — the world can be
consumed by something nobody on FAM has played, and FAM can have a runaway hit
the world has never heard of. Blending them into one ranking would lose both
signals, so they are two rows.

## This is not `live_facts`, deliberately

    live_facts   "what is the state of THIS entity right now"
                 resolve an entity → fetch its state
                 seconds of freshness, a status vocabulary
                 changes what an episode SAYS

    trending     "what is the world paying attention to"
                 no entity, no status
                 minutes of freshness
                 changes what is OFFERED

Forcing trending through `LiveSource.resolve/fetch` would mean inventing a fake
entity for "the world" and bending a contract built around scoreboards around
something that is not one.

**They compose without coupling.** A trending tile about a game becomes an
ordinary FAM question when tapped; EI marks it `live_domain=sports`;
`live_facts` answers it. Trending feeds the *bank*; live facts feed the
*evidence*.

## The cost design, which is the reason this is worth having

**One fetch serves every listener.** One upstream call per refresh window, one
warmed script per tile, everybody. That is a far better ratio than `live_facts`,
which is per entity and per episode, and it is what makes this the cheapest
place in FAM to add live data.

It follows CLAUDE.md's settled rule for the browse surfaces — one bank for
everyone, personalisation in the ordering — and preserves what already made the
crowd row cheapest to serve.

Consequences that fall out of that:

* The cache is **global, not per-listener**.
* `build_feed` reads it **synchronously**. The ranker stays a pure function of
  the log plus the cache, which is what keeps it callable in a test with no
  network. A ranker that fetched could not be tested offline.
* `/api/myfam` **schedules** a refresh when stale and renders from the cache.
  The browse surfaces are the one place the wait must be zero, and a news feed
  is not worth spending it on. A cold first load shows the row honestly empty
  and the next load has it.

## What a source must return

`TrendingItem(subject, query, why_now, kind, tags, rank, as_of, source)`.

**`query` is the load-bearing field: a question worth an episode, never a
headline.** "Chiefs 21 Broncos 7" is a fact with a shelf life of seconds and
belongs to `live_facts`. "Why the Chiefs' offensive line is suddenly the story
of their season" is a question, keeps for hours, and is what a tile is for. A
tile whose query is a headline produces an episode that restates the headline.

A source that can only produce headlines needs a step in front of it that turns
them into questions. That step is one model call per refresh window for
everybody — affordable, and it fits the cost design rather than fighting it.

**`why_now` is a subtitle and never evidence.** It is shown, never spoken, and
nothing downstream treats it as a source: tapping a tile runs the ordinary
pipeline, which researches the question from scratch. That is what stops a
stale blurb becoming a stale episode, and a test asserts `build_prompt` never
mentions trending at all.

**`kind`** distinguishes what sort of signal it is — `attention` (a news index:
this is being written about) from `prediction-market` (what people are
*betting*, which is a forecast and not a report).

## Item ids

Hashed from the **subject**, not the query, so a source rephrasing its question
between refreshes does not mint a new tile. The id is what impressions, fatigue
and the already-seen set are keyed on; an id that churned every fifteen minutes
would show the same listener the same tile forever and fatigue could never damp
it.

## Empty states

**An empty row is a fact about this deployment, never a claim about the world.**
The browse-surface form of PROBLEMS.md §89. Four outcomes, four sentences:

| outcome | row says |
|---|---|
| `not_configured` | FAM isn't connected to a world news feed yet. |
| `source_failed` | Couldn't reach the news feed just now. |
| `timeout` | The news feed didn't answer in time. |
| `empty` | The news feed had nothing new this time. |

None of them says "nothing is trending", and a test enforces that.

## Configuration

    TRENDING=1                      # the registry; leave on
    TRENDING_SOURCE=                # empty = none, and none is shipped
    TRENDING_TTL_SECONDS=900.0      # one refresh serves this long
    TRENDING_TIMEOUT_SECONDS=8.0    # generous; never in front of the first word
    TRENDING_MAX_ITEMS=6

`trending.install()` runs from the app lifespan and is re-runnable. An unknown
source name is reported, not raised: it empties one row rather than stopping
the server.

## Adding a source

1. Subclass `trending.TrendingSource`.
2. Implement `diagnose()` (cheap, no network), `fetch(limit)` and `verify()`.
3. `fetch` raises when broken and returns `[]` when it genuinely has nothing —
   those are different states and collapsing them hides an outage as a quiet
   miss.
4. Return **questions**, not headlines. Set `kind`.
5. Register it in `BUILDERS`; add the env var to `config.py`, `.env.example`
   and `tests/conftest.py`'s `FAM_ENVIRONMENT`.

## Candidate feeds

Not connected, and not chosen — this is a starting point, and the market moves.

**GDELT** is the strongest fit: free, global, 100+ languages, refreshes roughly
every fifteen minutes, and it emits **structured events and themes** rather
than article text — which is what generating a topic needs. The cost is
engineering; it is research-grade with a steep learning curve.

**NewsAPI** is ruled out: its free tier is localhost-only and forbids
commercial use.

**Event Registry** clusters articles into events (the right shape) at around
$600/month.

**Prediction markets** (Polymarket, Kalshi) are the broadest single source for
*outcome* questions and are cheap to read — but a price is a forecast, so
anything from them carries `kind="prediction-market"` and must never read as a
report.

## The open decision

Connecting a source means **the bank stops being entirely hand-written.**
CLAUDE.md treats "one bank for everyone" as settled, and the 28 hand-written
topics have taste in them that a generated row will not. Generated tiles also
have to obey the same `is_shareable` rule as everything else.

That is a change to a settled constraint and should be decided deliberately
rather than arrived at. The seam is built so the decision can be made with a
real row in front of you: turn on `TRENDING_SOURCE=fake`, look at the page, and
judge whether generated tiles belong beside the hand-written ones.

## Status

**No real source is connected.** The row is empty and says so. That is not a
statement about the world.

---

# GDELT

Added in §91. `TRENDING_SOURCE=gdelt`, and `GDELT=1`.

## What it does here

Sweeps a fixed list of GKG themes (`gdelt.THEMES`), measures each one's
coverage volume via `mode=timelinevolraw`, ranks by volume, and turns the top
few into tiles. Concurrent, on the shared clock, so nobody waits on it.

## Stories, not themes (§135)

The limitation below is what the Trending *row* no longer has. The myFAM
story pool's GDELT source (`story_sources.GdeltSignals`) measures the themes
only to choose where to look, then reads the recent articles under the
hottest ones and under **each region's own press** (`gdelt.discover`,
`geography.GDELT_SOURCES`), groups the headlines into the actual stories
(`news_clusters`), and counts the distinct outlets running each - the
popularity the row is ranked on. Every story knows where it is trending
(`geography.scope_for`): worldwide, a region, or a country. The rail keeps
two of its four places for the listener's own part of the world and "View
more" groups everything by place (`topics.rank_world`, `trending_groups`).
`GdeltTrendingSource` below is the older registry feed and still templates
themes; it is what `TRENDING_SOURCE=gdelt` installs, and the story pool no
longer depends on it.

## The honest limitation

**GDELT's DOC API is query-driven.** It tells you how much coverage *a query
you name* is getting; it does not hand back a ranked list of everything hot
right now. So this is real measurement over a **fixed vocabulary**, not
open-ended discovery.

Open-ended discovery needs the bulk GKG exports — a heavier path, deliberately
not taken. If the fixed vocabulary proves too narrow, that is the next step,
not a bug in this one.

## What is still templated

The tile's question is currently a template:

    what is actually driving the news about {subject} right now

That is only just on the right side of `TRENDING.md`'s rule that a tile carries
a question rather than a headline. The better version is **one model call per
refresh window** turning the top themes and their leading headlines into real
questions — one call for everybody, which the shared clock makes affordable.

Left as the next step rather than guessed at, because it is a writing-quality
decision and nothing in this build can test writing quality.

## Not verified

`api.gdeltproject.org` is blocked from the build container. Every shape in
`gdelt.py` is written from the documented API and tested against recorded
payloads. Run `python tools/gdelt_probe.py` somewhere with network first — it
checks both modes FAM uses and warns on the two things most likely to be
silently wrong (dates not parsing, URLs not arriving).


## The trending bank (§139)

**Why.** From Render, the GDELT story sweep timed out on every run: about
thirty-two requests to a free service that asks for one every five seconds,
fired six at a time inside a forty-five second ceiling. The pool stayed
empty and the row said "The live sources didn't answer in time" to everybody.

**What it is.** An edition, built on a clock rather than on a page load:

| | |
|---|---|
| When | 05:00 and 17:00, `America/New_York` (`TRENDING_BANK_HOURS`, `TRENDING_BANK_TIMEZONE`). A named zone, so 5am survives daylight saving. |
| From | GNews `top-headlines`: one call per category worldwide, one per country (`GNEWS_CATEGORIES`, `GNEWS_COUNTRIES`), then one `search` per leading candidate to count how widely it runs (`GNEWS_CORROBORATE`). About 26 requests an edition. |
| Ranked on | how many articles run it (`totalArticles`, log-scaled), how many top-story feeds lead with it, and how high. At most three from one section. |
| Holds | `TRENDING_BANK_SIZE` stories (10), composed into tiles by `stories.compose` exactly as the pool's are. |
| Writes | one episode per story, at `TRENDING_BANK_MINUTES` (2, the myFAM default), into the shared script cache under `pipeline.key_for` - the key a tap computes - kept until the next edition plus an hour. |
| Shown | by the Trending rail and its "View more", through `topics.world_inventory` - and nothing else: with no edition the row is empty and says why, never filled from the live pool. |
| Kept apart | GNews is called only by `trending_bank`; the live pool (GDELT, API-Sports, Finnhub, Polymarket, every 15 minutes) never spends a GNews request, and a test scans the modules to keep it so. |
| Order | Heard stories still become follow-ups (`trending_for`); order is still popularity and the listener's country (`rank_world`). |
| Stored | `TRENDING_BANK_DB` on the mounted disk, with the GNews request ledger (`GNEWS_DAILY_REQUESTS`). |

**No fallback, at the owner's direction.** GNews is the only source; if it
cannot answer, the fix is the plan, not a second source. A failed build
keeps the last edition on the row for up to `TRENDING_BANK_MAX_AGE_HOURS`
(36), is retried after `TRENDING_BANK_RETRY_SECONDS` (30 minutes), and is on
`/api/health` as `current_slot_status`. With no key nothing is attempted and
the row says "FAM isn't connected to a live news source yet"; a slot that
failed only for want of a key is built the moment one is set.

**The one rule it bends, at the owner's direction.** `cache.ttl_for` gives a
news episode fifteen minutes (`CACHE_TTL_VOLATILE`), which would expire a
5am edition before anybody woke. A bank episode keeps until the next edition.
It still never keeps a score in progress, and never writes ahead a question
whose answer is a result (`outcome_dependent`) - that tile is offered and
the tap writes it.

**The two inventories do not cross.** Made for you and What you missed read
the live pool (GDELT, API-Sports, Finnhub, Polymarket, every fifteen
minutes) and never the bank; Trending reads the bank and never the pool; and
GNews is spent by the bank alone.

**Operating it.** `python tools/trending_bank.py` prints the schedule and the
edition; `--verify` makes one real GNews request; `--dry` collects and ranks
without writing; `--build` rebuilds the current slot now; `--url` reads a
running server. Nothing here has made a real GNews request - gnews.io is
blocked from the build container, so every shape is from the v4 docs.
