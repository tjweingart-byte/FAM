# Trending — what the world is paying attention to

The myFAM row backed by an outside feed, and why it is a separate subsystem
from live facts.

## Two rows, two questions

| row | key | answers | comes from |
|---|---|---|---|
| **Trending** | `world_trending` | what is the world talking about | `trending.py` → an outside feed |
| **What FAM can't stop playing** | `most_played` | what are *FAM's* listeners playing | `topics.rank_most_played` → the event log |

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
