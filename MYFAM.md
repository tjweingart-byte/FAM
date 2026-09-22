# myFAM — the browse page, and what fills it

Five rails, two inventories, and one rule that decides the shape of all of it:
**a tile is a title and an angle; the script is written when somebody taps it.**

## The rails

| # | rail | question it answers | inventory | comes from |
|---|---|---|---|---|
| 1 | **Made for you** / **Start here** | what would *you* want today | live stories **and** the generic floor (see below), or the startup set on a cold start | `rank_from_history` / `rank_startup` |
| 2 | **Trending** | what is the world on | live stories only, `WORLD_FLOOR` reserved | `stories.pool()` |
| 3 | **What you missed last week** | what went past you | offered to you, played across FAM, or trending | `rank_missed` |
| 4 | **What FAM can't stop listening to** | what is everybody here playing | whatever has plays and **nothing else**, cached first | `rank_most_played` |
| 5 | **What your friends are listening to** | what is *your graph* playing | whatever they played, cached first | `rank_friends` |

**Rail 1 has two names, because on a cold start it is not personal** (§116).
A listener with no account, no chosen interests and nothing played scores zero
against every tile, so `RELEVANCE_FLOOR` emptied the one rail that matters
most — the whole page was one crowd row and four explanations. It is filled
from a third inventory now: `startup.STARTUP_TOPICS`, **one time-anchored
question per facet**, ordered by what FAM's listeners actually play. The
heading becomes **Start here**, because "Made for you" is a claim and the
ordering is real but is not this listener's; `taste_source` on `/api/myfam`
says which of the two drew it, and `startup_order` says whether even the
prior was measured or declared.

Three things make that affordable and honest, and they are the whole design:

* **The freshness comes from the tap, not from a provider.** Every startup
  query asks about now, and `SEARCH_MODE=always` means every episode is
  researched — so the tile costs nothing at page load, needs no live source
  configured, and `research.NoEvidence` refuses rather than writing a stale
  one from memory.
* **Nothing on it claims a fact about the world.** A tile is written before
  anything is retrieved, so it carries a question and never a result — §88
  and §102 applied one layer earlier. A test bans results, outcomes and even
  digits from the titles and hooks.
* **It takes itself down.** `cold` is `not profile`, derived rather than
  stored, so one play, one search or one chosen interest retires the set for
  that listener for good. And plays of it never feed `popular_facets`, or the
  prior would spend the deployment confirming its own opening guess — the
  same loop the impression rule already forbids, through a different door.

`startup.py` holds the editorial reasoning for why those eight questions.

A sixth ranking, `rank_might_like`, is computed and has no rail. It serves the
Explore New screen and still takes its turn in `FILL_ORDER`, so the tiles it
would show are held back from the crowd rows. `topics.UNSHELVED` is what marks
it — anything iterating `FILL_ORDER` and then looking the key up in the drawn
sections must skip it, or it is a `KeyError` rather than a finding.

**Trending gets its four before anything else chooses** (§114). It used to
be filled *last*, from what the four personal rails had not claimed — and
Made for you draws on the same live pool, so on a day the pool held five
stories and a listener's taste matched four of them, the world row got one.
`WORLD_FLOOR` tiles are set aside before the fill loop runs.

The trade, stated rather than buried: on a thin pool Made for you loses its
best live tile. That is the right way round **only** because Made for you
draws on both inventories and can never be empty — the bank is twenty-eight
topics — while Trending draws on the live pool alone and has nowhere else to
go. And the reservation happens only when it *buys* the floor: a pool of one
cannot fill the row however it is shared out, so holding that story back
would cost the personal rail its tile and still leave Trending short.

**Order matters and was chosen.** Personal first, because somebody opening
myFAM is more likely to want what was chosen for them than what is popular.
Trending second, because it is the one rail with a reason to be looked at
*today* and a row nobody scrolls to is a row nobody reads. "What you missed"
third — under Trending rather than over it, because it is a third rail about
what somebody already likes, a week older, and the argument that moved the
world row up applies against it too. Then the two crowd rows, FAM's own
popularity above the friends row — that row always has something in it, and
the friends row is empty until somebody follows anybody.

## What you missed last week

The weekly recap's replacement, at the owner's direction, and deliberately a
different *kind* of thing. The recap was one episode **about** somebody's week,
written from their own log and fired as a popup on the first open on or after
Sunday — so a thin week produced an episode about having had a thin week, in
front of somebody who had opened the app to listen to something else. This is
a shelf of episodes they can still have.

**Membership widened, at the owner's direction (§114).** It was the
impression log and nothing else — tiles this app had put on a screen in front
of this person, minus everything they played. That was a defensible reading
of the heading and it made the rail a report on our own delivery: a listener
who did not open myFAM last week missed nothing, by construction, however
much happened. The instruction is that it should hold "the ABSOLUTE MOST
RELEVANT stories they didn't click on or listen to in the last week... it
could be stories that were popular throughout the app or trending that the
user never listened to."

So three things qualify, any one of them:

* **offered to them** — the impression log, as before;
* **played by other listeners** inside the window;
* **in the live story pool**, which is what the world has been on this week
  by definition.

All three genuinely went past this listener in the last seven days, which is
what keeps the heading true. **The standing bank is still not a source**: an
evergreen explainer nobody was offered and nobody played did not happen last
week, and putting one here to make the row look full is the padding this rail
was built against.

**And relevance is a floor, not only a sort.** "Only the ABSOLUTE MOST
RELEVANT" is the whole of the instruction, so a tile this listener has no
affinity for is not offered at all — short beats padded, and this is also
what stops the widened membership turning the rail into a second copy of
Trending for somebody who was shown nothing.

With **no taste profile at all** it falls back to exactly what it always was:
what was offered, newest first. Impressions deliberately never reach `taste`,
so a listener who has chosen nothing and played nothing scores 0.0 against
every tile, and a floor over nothing would empty the rail for precisely the
listener it is most use to. The claim shrinks to the one the evidence
supports.

**An impression still never becomes taste.** Being shown something says nothing
about whether you wanted it, and CLAUDE.md is emphatic that letting it into the
taste model is how a feed teaches itself its own preferences. The impression
decides *membership*, which is a fact about the feed; `_affinity` against the
same profile every other personal rail uses decides the *order*.

**It can only offer what it can still resolve.** A live story that expired and
fell out of the pool has no title, no angle and no question, and a tile invented
to stand in for one is exactly the failure this page is built against. So the
candidates are the bank plus `stories.pool().held()` — `held` rather than `live`,
so a story the variety cap is hiding is still resolvable — and a story that has
aged out is simply not in the rail.

It fills **first** in `FILL_ORDER`, which is the one ordering decision worth
writing down: its inventory is the narrowest on the page, so it cannot starve
anything, and letting Made for you choose ahead of it took the *best* of the
missed tiles and left the rail whose heading is about relevance holding the
leftovers.

**Trending is excluded from that first pass**, and this is what the widening
cost. A brand-new story nobody has been shown is not one this listener
*missed* in any useful sense — it is one Made for you exists to offer them —
and a tile already on the page is not one anybody missed either. So the rail
runs with `include_trending=False` inside the loop and is topped up from the
leftovers after every other rail has chosen, which makes the live pool the
**last** source for this row rather than the first.

`MISSED_SECTION_SIZE` is 8 and `MISSED_WINDOW` is a week.

## The three inventories

    TOPIC_BANK        ~28 evergreen topics, hand-written, true in any week
    stories.pool()    live candidates built from today's data, and expiring
    STARTUP_TOPICS    8 time-anchored questions, one per facet, for a cold start

The bank is what a browse page has when nothing has happened; the pool is what
it has when something has. The startup set is what it has when nothing is
known about *the listener*. None holds audio and none holds a script.

The third is the smallest and the narrowest: it *leads* one rail, for one
kind of listener, and `build_feed` stops reaching for it as a lead the moment
there is any taste at all. Since §125 it is also the generic floor behind
rail 1 for a listener who has an account — see below. It is deliberately **not** part of `TOPIC_BANK` — a question
written for a first impression has no business in the mix picker or in "What
FAM can't stop listening to", and a test asserts the two sets do not overlap.

## Which generic floor, and who gets which (§125)

`topics.browse_inventory` is the one definition, read by `build_feed`,
`build_section` and `rank_next_up`:

    no account   live story pool + the evergreen bank
    account      live story pool + the startup set

The bank is a **first impression for somebody FAM knows nothing about and can
keep nothing for** — downloaded the app, has not signed up. Twenty-eight
standing explainers are the right answer to "show me what this is" and the
wrong answer to "what should I hear today", and an account is where the
second question becomes the one being asked.

**It is a swap and never a subtraction.** Taking the bank away on its own
would empty rail 1 on any deployment with no live provider — which is every
deployment today — and `WORLD_FLOOR` reserves its four Trending tiles on the
stated premise that rail 1 has somewhere else to go. The floor is replaced
with the fresher of the two: every startup query asks what changed recently,
and `SEARCH_MODE=always` researches it on the tap, so an account holder's
generic tile is current because it is *retrieved* rather than because
somebody edited a string.

This does **not** make the startup set warm inventory for a guest. A guest
who has played something keeps the bank; the set still leads rail 1 only for
a listener who has said and done nothing, which is what `startup.py` was
written for.

**Two surfaces are exempt, deliberately** — the rule is about what FAM offers
unprompted, not about what a listener can go and find. The DailyFAM mix
picker (`rank_bank`) keeps the whole bank, because it is a menu somebody
opened, because a mix holds topic ids rather than audio so a member is a
fresh episode every morning, and because a saved mix needs an account — so
gating it would empty the picker for exactly the listeners who can use it.
Explore New (`rank_might_like`) keeps it because it is off the page and is
reached only by somebody trying to widen a taste.

**Rail 4 fills from plays and from nothing else.** It used to top itself up
from the bank so it was never empty; the heading is a claim about this
deployment's listeners and twenty-eight unplayed tiles do not support it. An
unplayed row is empty and says so, and `tools/seed_demo.py` is what fills it
for a demo.

**All are shared.** Every listener sees the same inventory and a different
ordering of it. That is CLAUDE.md's settled rule for the browse surfaces and
it is the whole cost design: two people who tap the same tile share one script
through `cache.py`, so the second tap is free and instant. A per-listener
inventory would be a per-listener script for every tile — the same product at
many times the price.

## The story pool

`stories.py` holds it; `story_sources.py` holds the four providers.

    GDELT        what the world's press is writing about   (keyless)
    Finnhub      what moved in the market today            (free key)
    Polymarket   what people are betting on                (keyless)
    API-Sports   what is being played today                (free/cheap key)

Plus the `trending` registry, which is where `TRENDING_SOURCE` installs a feed;
it became one source among several rather than being deprecated, so anything
already configured keeps working and produces the same row it always did.

### One refresh, one small model call, everybody

A refresh window is:

1. **Sweep** every configured provider concurrently, each inside its own
   timeout. Each returns `Signal`s — a subject and a *measurement*.
2. **Compose** the ones that are new since the last window, in **one** model
   call, into a title, an angle and a question.
3. **Merge** with what is already in the pool, drop what has expired, cap for
   variety, and install.

Already-composed stories keep their title and angle, so a steady state composes
two or three tiles per window rather than twenty. At the shipped fifteen-minute
window that is under a hundred small calls a day for an entire deployment.

`/api/myfam` **schedules** a refresh when the pool is stale and renders from
whatever the pool holds. Nothing on the page-load path awaits anything, which
is what "zero queue" means here: not that the page is fast, but that there is
no path from opening it to generating anything. `tests/test_myfam_personalisation.py`
reads `build_feed`'s own source and fails if one appears.

### A signal is a measurement, never a result

This is the rule the subsystem exists to keep, and it is PROBLEMS.md §88 moved
onto the surface more people see. A tile is written *before* anything is
researched, so a result on one is a claim nobody checked.

* Sources put coverage volume, a traded price, a betting line or a fixture
  status into `Signal.observation` — never a score, a winner or a settled
  market. API-Sports *knows* the score and deliberately does not pass it on.
* The composer is told in its system prompt that it has researched nothing and
  must write a tile that is true whichever way the thing turns out.
* `_RESULT_WORDS` catches the case where the prompt did not hold, sends that
  one tile back to its template, and **logs it** — a guard that fires quietly
  is a prompt nobody fixes. It checks the **query** as well as the title and
  the angle, and the query is the one it could least afford to miss: a title is
  read, a query is what the pipeline researches *from*, so a result asserted
  there is one the episode inherits.
* The templates cannot say a result either, so the rule survives a total
  outage.

The episode still finds out what happened. It researches the question from
scratch, from dated evidence, on the path built for exactly that — and
`live_facts` answers it when the question has an entity and a status.

### How hard to push, and for how long

The packet asks for judgement about this, so the judgement is written down
rather than left implicit in a sort order.

| | what decides it |
|---|---|
| **how hard** | `Story.push()` = `DOMAIN_WEIGHT` × the provider's own `strength`, halving every half-shelf-life |
| **how long** | `DOMAIN_SHELF_LIFE`: sport 8h, markets 18h, attention 20h, prediction markets 4 days |
| **and then stop** | expiry is hard, and `SUBJECT_COOLDOWN` (36h) keeps the subject out afterwards however hot its signal still reads |

`first_seen` is the clock, not `last_seen`: **a story that keeps being reported
does not get to be new again.** Without that, a subject the world talks about
all week is the top tile all week — the "shown the same thing forever" failure
arriving from the other direction from the one `TrendingItem.id` was hashed
from the subject to avoid.

A story whose provider stopped mentioning it is kept until it expires.
Providers are noisy; a tile that vanished between two page loads reads as a bug
rather than as an editorial decision.

### Variety, twice

* **In the pool**: `MAX_PER_FACET = 5` of the 24 it offers, so a busy Sunday in
  sport cannot crowd everything else out before a ranker sees it.
* **In each rail**: `topics.MAX_PER_FACET = 2` of 6, so no rail becomes one
  subject.

**The pool keeps more than it offers** (`POOL_STORE`), and that is not a detail.
The variety cap used to run when the pool was *written*, so a story it passed
over was discarded — and the next sweep saw that subject again, had no record
of it, and admitted it as brand new. Its `first_seen` reset, so it never aged,
never expired, never reached the cooldown, and was paid for again every window:
the "shown the same thing forever" failure, arriving through the one door the
push model was not watching. The cap runs on the way out now, so an
over-served story is **hidden rather than forgotten**.

And `_FIRST_SEEN` is the backstop under that: a ledger of when FAM first saw
each subject, kept independently of pool membership, so even a story that falls
out of the store entirely comes back with the clock it already had. Anything
not currently held goes through `_admissible`, which knows three states — never
seen, seen and still inside its shelf life, seen and past it — and only the
first is allowed to start a clock.

The rail cap is a **cap on what is available and never a quota on what is
not**. A listener whose entire history is sport has nothing else with any
affinity, so the cap has nothing to reach for, and `diversify` tops the rail up
rather than returning a short one — a two-tile rail reads as broken where a
samey six-tile one reads as a taste. `test_a_listener_with_one_interest_still_gets_a_full_rail`
pins that trade so it stays a decision.

## Made for you: one score over both inventories

The mixing happens inside one ranking rather than in two stitched together,
because two would need a rule for how many of each and there is no honest
answer to that — it depends entirely on whether anything happened today in the
corner of the world this listener cares about. One score answers it by
measuring.

`FRESHNESS_BOOST` is the only thumb on the scale, and it exists because a bank
topic carries four hand-written tags and matches a taste profile more tidily
than a story whose tags came off a keyword sweep. Without it the bank wins every
time and the page never changes.

**A story with no affinity stays off this rail however hot it is.** "Filtered
and influenced heavily by the individual's algorithm" is what the rail is for;
Trending is where the hot thing nobody here cares about belongs.

### Three changes that made "no affinity" mean something (§114)

The complaint was specific: "I don't need to be seeing recommended episodes
about a random small school college football matchup that I've never
indicated through my search behavior that I would be interested in."

**`SUBTAG_WEIGHT`.** `_affinity` summed every tag with the same weight, so
`sports` and `sports-drama` counted identically — the resolution §80 added to
the *vocabulary* was being thrown away by the *ranking*. A subtag match is
evidence about this episode; a facet match is evidence about a whole heading,
and they should not be worth the same.

**`RELEVANCE_FLOOR`.** The rail kept anything scoring `> 0`, which every tile
sharing one barely-touched facet clears. One finished NFL episode put weight
on `sports`, and every sports story in the world carries `sports`.

**`BROAD_MATCH_PENALTY`**, which answers the case the vocabulary *cannot*
express and is the interesting one. **There is no tag for the NFL and none
for college football.** Both are `sports`. No weighting distinguishes them and
nothing should pretend it does. What is knowable without inventing a
vocabulary is whether this listener has ever *said* any of the words on the
tile — `topics.familiar_words` reads their own searches and plays out of the
event log, which is a fact about them rather than a guess about the subject.
A live story matching only a whole facet, on a subject they have never been
near, is cut.

It **damps and never excludes**, and it applies to **live stories only**. A
listener one episode into the app has almost no familiar words, and a rule
would empty their rail in the name of relevance; the bank's twenty-eight
subjects are broad by construction, so penalising breadth there would
penalise the whole evergreen inventory.

## The two cached rows

Both lead with tiles whose script is **already written**: those start instantly
and cost nothing to serve, which is a real difference between two tiles
somebody is choosing between. `/api/myfam` passes a memoised `query -> bool`
into the ranker and marks every tile with the answer.

**It is a sort and never a filter.** A deployment whose cache has just expired
would otherwise show an empty row, which is a fact about the cache told as a
fact about what people are playing.

`minutes` is on the request for one reason: whether a script exists depends on
the length it would be written at, so asking at the wrong length marks ready
tiles unready and sorts the rails wrong.

## What the page warms before a tap

Drawing myFAM schedules a **prefetch cycle** - `prefetch.schedule_cycle(user,
minutes)`, called after the feed is built and never awaited, exactly like the
story sweep above it. What it warms is the **brief**: the one model call
episode intelligence makes between a tap and its retrieval, which is the
several seconds a browse tap would otherwise pay in front of its first word.

**Not the script.** A brief costs a fraction of a cent, so a wrong guess is
nearly free; an episode costs an episode. `PREFETCH_LEVEL=script` warms the
whole thing and stays opt-in until the hit rate says it pays - and on a page
whose top two rails are today's news, writing episodes hours before they are
tapped is also how they arrive stale.

Four things make the warming worth anything at all, and each was a way for it
to be quietly worthless (PROBLEMS.md §105):

* **The length is the one this page is showing.** A brief is keyed by
  `(query, minutes, context)`, and the header's length control is separate
  from the search player's, so a warm at the interface default is a warm
  nobody looks up.
* **One cycle per listener per `PREFETCH_CYCLE_SECONDS`.** A browse page is
  drawn far more often than it is acted on.
* **The story pool has a source of its own.** It fills the top two rails and
  had none, which made it the one inventory where every tap paid full price -
  and it is where a brief buys most, because a tile is a title and an angle.
* **A brief already held is not bought again.** They keep for an hour and a
  cycle can come round every five minutes.

`python tools/prefetch_report.py` prints what this deployment would warm
without spending anything; `--live` reads the hit rate, per source, off a
running server.

## The friends rail reads the graph

It used to be co-listener overlap — *people who played what you played also
played this* — under a heading that said "your circle" and on a card that said
"People you follow played this". A real signal wearing somebody else's name.

`social.circle_of` is friends (mutual follows) first, then everyone else they
follow. Both, rather than friends only, because following here is asymmetric by
design: somebody who has just gone and followed six people has no friends yet
and would get an empty rail on the day it should have filled.

**An empty circle returns nothing, deliberately.** A rail called "what your
friends are listening to" that quietly showed strangers would be the original
problem again with better wording. The empty state names the thing to do about
it, because the listener can fix it in two taps.

The overlap ranking was not deleted: `rank_followers` still tops up the
post-episode popup, where the question is "what next" and nothing claims those
people are anybody's friends.

## Empty states

**An empty rail is a fact about this deployment and never a claim about the
world** — the browse-surface form of PROBLEMS.md §89. `stories.Pool.empty_reason`
knows *which* way it came up empty and says so; a test asserts that none of the
five outcomes ever says "nothing is trending" or "nothing is happening".

| outcome | what it means |
|---|---|
| `not_configured` | this deployment has not connected that source |
| `source_failed` | it was asked and it broke |
| `timeout` | it was asked and did not answer in time |
| `empty` | it was asked and genuinely had nothing |
| `skipped` | working, and deliberately not asked this window (see below) |

"What you missed last week" has an empty sentence of its own, and the thing to
notice about it is what it does *not* say. A listener who was not here last
week was offered nothing; one who played everything missed nothing. The rail
cannot tell those apart from where it stands, so its sentence has to be true of
both and claims neither: *"Nothing went past you this week."*

**Made for you has no empty sentence on a cold start any more, and that is a
narrowing of this section rather than an exception to it** (§116). It had one —
*"Your first episode starts this one off."* — which was true and was the wrong
kind of true: nothing about that listener made the rail unfillable, only the
absence of a signal we had never asked them for. The rails that genuinely
cannot be filled without inventing something still are empty and still say
why: "What your friends are listening to" needs a graph, "What you missed last
week" needs a week of impressions, and Trending needs a live source. Those
three sentences are unchanged, and a test asserts those rails stay empty for a
brand-new listener rather than quietly borrowing the startup set.

There is a sixth pool sentence, and it belongs to the **rail** rather than the pool.
Made for you chooses first, so on a thin day it can take everything and leave
Trending empty while the pool served perfectly well. Saying "the live sources
had nothing" there would be our own page's arrangement reported as a fact about
the world, so `_world_empty_reason` says where the stories went instead.

`skipped` is the one that is not an error. API-Sports allows a hundred requests
a *day*, so it sweeps at most every two hours; a quota spent on a browse page
nobody opened is a quota that is not there when somebody does. Its stories stay
in the pool until they expire, so a skipped sweep is invisible to a listener
and honest on `/api/health`. Reporting it as `empty` would have made a working
source look broken.

## Configuration

    STORIES=1                       # the registry; leave on
    STORIES_SOURCES=                # empty = every source this build knows
    STORIES_TTL_SECONDS=900.0       # one sweep + one composition serves this long
    STORIES_TIMEOUT_SECONDS=12.0    # per source; never in front of the first word
    STORIES_COMPOSE=1               # 0 = templated tiles, which is a real product
    STORIES_MODEL=                  # defaults to MODEL
    STORIES_MARKET_MOVE_PERCENT=3.0
    STORIES_SPORTS=                 # empty follows API_SPORTS_SPORT
    STORIES_POLYMARKET=0            # keyless, so it needs a switch of its own
    FINNHUB_WATCHLIST=              # empty = story_sources.WATCHLIST

    PREFETCH=1                      # warm what a tap would pay for; 0 = every tap pays
    PREFETCH_LEVEL=brief            # script = write whole episodes in advance
    PREFETCH_CYCLE_SECONDS=300.0    # at most one cycle per listener this often
    PREFETCH_PER_CYCLE=6            # candidates per cycle, shared across sources
    PREFETCH_DAILY_BRIEFS=400       # counted apart from episodes; they cost far less
    PREFETCH_DAILY_DOLLARS=2.0      # the ceiling both kinds of warm share

**Nothing live is configured by default**, and that is deliberately not the
same as nothing being here — the same doctrine `live_facts` and `trending`
ship under. Every source is registered, every one reports exactly what it
needs, and `/api/health` distinguishes *ready* from *not configured*.

**The one line that makes it live is `GDELT=1`.** GDELT needs no credential, so
a deployment with network gets the attention half of the pool for nothing. The
other three want a key or a switch; `PROVIDER_ROLLOUT.md` has the order.

## Verifying it

    python tools/stories_report.py --dry     # sweep only, no model call
    python tools/stories_report.py           # sweep, compose, print the page

`--dry` is the cheap look: which sources answered, what they said, and what the
rails would hold. Without it, one real model call composes the tiles — the
whole cost of a refresh window for an entire deployment.

Read the output rather than the exit code. A pool of six tiles from one source
is a browse page about one corner of the world, and that is a configuration
finding rather than a bug.

**Nothing here has made a real request from the build container** — its egress
proxy blocks all four hosts, so every shape in `story_sources.py` is written
from the documented API and tested against recorded payloads. Do not say FAM's
browse page is live until `tools/stories_report.py` has printed real tiles on a
real machine.
