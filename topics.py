"""myFAM: two shared inventories, plus per-user ranking over them.

The cost argument decides the shape of this module. Generating an episode
costs a model call; ranking a list costs nothing. So **every user sees the
same inventory and a different ordering of it**. Two people who tap the same
tile share one script through `cache.py`, and the second tap is free and
instant. A per-user *inventory* would mean a per-user script for every tile,
which is the same product at many times the price.

There are three of them, and they answer different halves of "what should I
hear":

    TOPIC_BANK        ~28 evergreen topics, written by hand, always true
    stories.pool()    live candidates built from today's data, and expiring
    STARTUP_TOPICS    one time-anchored question per facet, for a cold start

The bank is what a browse page has when nothing has happened; the pool is what
it has when something has. Neither is a script - a tile is a title, an angle
and a question, and the writing happens on the tap. See `stories.py`.

The third is the smallest and is there for one listener: the one who has just
tapped "Continue as guest" and then "Skip for now", whose log is empty, and
who therefore scores zero against both of the other two. It is used by
`rank_startup` and by nothing else, and `build_feed` stops reaching for it the
moment there is any taste at all - so it is scaffolding that takes itself
down. See `startup.py` for why those eight questions and not others.

Four rails, and the point is that each runs on a *different* signal - four
shuffles of one score would be one rail wearing four hats:

    from_history      closest to what you played, live and evergreen mixed
    world_trending    what the world is paying attention to    (global, live)
    most_played       what FAM's listeners are playing         (global, cached)
    followers         what the people you follow are playing   (your graph)

A fifth, `might_like`, is ranked and reachable and has no rail: it is the only
signal that offers anything *outside* an established taste, it serves the
Explore New surface, and it still takes its turn in `FILL_ORDER` so the tiles
it would show are held back from the generic rows.

Everything is derived from an append-only event log, so there is no profile to
keep in sync - a taste profile is a query, not a stored object.

The log holds two different kinds of thing, and the distinction is the reason
the ranking still works. A **behavioural** event (search, play, complete, skip)
is something the listener did, and it is what taste is computed from. An
**impression** is something the app did - one tile, on one shelf, from one
ranking version - and it exists so "why did we show this?" has an answer.
Impressions arrive ~18 at a time on every feed load, so they are excluded from
`for_user`, the capped read that feeds `taste`; letting them in would train the
feed on its own output. See `record_impressions` and `for_user`.

**"What your friends are listening to" now is.** It used to be a label over
data this app did not have: there was no follow graph, so the rail ranked
co-listener overlap - people who played what you played also played this - and
said "people you follow" about strangers. The graph has existed since
SHARING.md and `rank_friends` reads it, so the heading is a promise the code
keeps. The overlap signal was not deleted: it is real and standard, and it
still tops up the post-episode popup, where nothing claims those people are
anybody's friends.
"""
from __future__ import annotations

import logging
import math
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Iterable, Optional

import startup
import categories
import learned_rank
import taste_vectors
import stories
import trending
from paths import data_path

log = logging.getLogger(__name__)

#: How long an event keeps influencing ranking, in seconds. A taste that never
#: fades makes the feed a museum of what someone cared about last month.
HALF_LIFE = 14 * 86400
#: What "now" means for trending.
TRENDING_WINDOW = 3 * 86400
#: Never show the same tile in two sections; the feed should look wider than
#: the bank actually is.
#:
#: **Exactly four on the page, and "View more" holds the rest** (§134, at the
#: owner's direction: "make sure there are exactly 4 tiles in each rail at all
#: times"). It was six, with per-rail floors under it, and a rail that could
#: show anything from one to eight tiles read as broken. The two rails that
#: report rather than choose - what FAM plays and what friends play - may show
#: fewer, because filling them would be inventing plays; the moment they have
#: four they show exactly four like everything else. See `RAIL_MINIMUM`.
SECTION_SIZE = 4

#: How far back a friend's listening still counts. Longer than `TRENDING_WINDOW`
#: on purpose: the crowd row is asking "what is being played *now*", which is a
#: question about a large number of people, and the friends row is asking what
#: a handful of named people have been into - a question three days of data
#: cannot answer for somebody with four friends.
FRIENDS_WINDOW = 14 * 86400

#: How many candidates a ranker hands to the variety pass. Three times what a
#: rail shows, so `diversify` has something to choose between - trimming six
#: to six is not a variety rule, it is a sort.
CANDIDATE_FACTOR = 3

#: At most this many tiles from one facet in one rail. Two, because a rail of
#: six with three sports tiles reads as a sports rail, and "make sure there is
#: good variety" is the first line of the packet this page was built from.
#:
#: It is a *cap and not a quota*: a listener whose entire history is sport
#: still gets sport at the top of Made for you, they just do not get six of it
#: when the bank has something else to say. The cost of the cap is that the
#: third-best sports tile loses to the best tech one; that is the trade, and
#: it is the right way round for a browse page somebody scrolls.
MAX_PER_FACET = 2

#: How far back "What you missed last week" looks, and how many it offers.
#:
#: A week because that is what the rail says, and eight because the packet
#: asks for six to eight - the top of that range, so the rail fills when there
#: is enough and is honestly short when there is not. Padding it out to eight
#: from things the listener was never offered would make the heading a lie.
MISSED_WINDOW = 7 * 86400
#: Four, like every rail since §134. It was eight; the rest of what went past
#: somebody is one tap away behind "View more".
MISSED_SECTION_SIZE = SECTION_SIZE

#: The fewest tiles the Trending rail may show while the pool can fill it.
#:
#: The reported failure: "there are times when there is only one trending
#: episode being displayed on trending. At all times I want there to be at
#: least four." The cause was the fill order rather than the sources. Trending
#: was filled **last**, from whatever the four personal rails had not already
#: claimed, and Made for you draws on the same live pool - so on a day the
#: pool held five stories and a listener's taste matched four of them, the
#: world row got one.
#:
#: So this many are set aside for it before anything else chooses. The trade,
#: stated rather than buried: on a thin pool, Made for you loses its best
#: live tile to the world row. That is the right way round *only* because
#: Made for you draws on both inventories and can never be empty - the
#: evergreen bank is twenty-eight topics - while Trending draws on the live
#: pool alone and has nowhere else to go. An empty rail is a worse answer
#: than a rail that had to give up its first pick.
#:
#: **Superseded by §134**: Trending now chooses first and alone
#: (`rank_world`), so it is never starved by the personal rails and there is
#: nothing left to reserve. Kept as the number the row aims for.
WORLD_FLOOR = SECTION_SIZE

#: The fewest tiles each drawn rail shows, whatever its own ranking found
#: (§127, at the owner's direction). "Every rail should always display at
#: least four episodes, especially Trending and Made for you, which should
#: always display at least five or six" - and "What your friends are listening
#: to" is the stated exception, because it is a statement about named people
#: and a friend's row filled with strangers is §102's bug again.
#:
#: This reverses three "honestly empty" decisions - §125's crowd row that
#: holds plays and nothing else, "What you missed"'s relevance floor, and
#: Trending drawing on the live pool alone - and it does so by **topping up
#: after** each rail has chosen, never by weakening the ranking: a rail's own
#: picks still lead, in its own order, and only the gap under the minimum is
#: filled. See `_rail_fallback` for what each rail is filled from and why.
#:
#: **Narrowed by §134, at the owner's direction.** Every rail shows exactly
#: `SECTION_SIZE`, and only the rails that *choose* are topped up to it:
#:
#: * **What FAM can't stop listening to** is out of this table. Topping it up
#:   from tiles nobody has played is making up plays under a heading that
#:   claims them ("it should not make up episodes"); it holds what was played
#:   and shows four the moment four have been.
#: * **Trending** is out of it too, for a stronger reason: its fallback was
#:   the startup set and then the bank - the "dummy data" the owner has now
#:   ruled off that row entirely. It holds live stories and nothing else.
#: * **What your friends are listening to** was never in it.
RAIL_MINIMUM = {"from_history": SECTION_SIZE, "missed": SECTION_SIZE}

#: How much a story running in the listener's own country is lifted on the
#: Trending rail, per unit of that country's share of the coverage (§134).
#:
#: The owner's rule for that row: popularity, and "the only factor that should
#: have input in it other than popularity is the country the user is in (and
#: how stories are trending in that country)". So a story whose coverage is
#: entirely from their country counts double, one that is half from it counts
#: one-and-a-half times, and one that nobody there is writing about keeps its
#: global weight exactly - a boost, never a filter, because a genuinely
#: global story is trending *for* them whether or not their press has it.
COUNTRY_WEIGHT = 1.0

#: How many tiles one facet may take on the Trending rail. One, which on a
#: four-tile row is the most variety the row can have ("there should be more
#: variety in the recommended episodes"). A cap, never a quota: a pool that is
#: all one subject still fills the row, from the tiles the cap passed over.
WORLD_MAX_PER_FACET = 1

#: How much the press's coverage lifts a story on the Trending rail (§135).
#: "The trending section should carry stories based off of their popularity
#: on trending news" - so a story is ranked on how many outlets are running
#: it, log-scaled against the most-covered story in the pool, on top of its
#: own push. A game or a price nobody is writing about still has a place; a
#: story forty newsrooms are running outranks it.
COVERAGE_WEIGHT = 3.0

#: How many of the rail's four places go to the listener's own part of the
#: world before the rest are filled worldwide (§135: "trending stories from
#: over the world and regionally"). At most half, so the row is never only
#: local; zero when the listener's country is unknown or nothing is trending
#: there, in which case the row is ranked worldwide exactly as before.
WORLD_LOCAL_SLOTS = 2

#: What a live story is worth next to an evergreen one of the same affinity.
#:
#: Made for you draws from both inventories, and without this the bank wins
#: every time: a bank topic carries four hand-written tags and matches a taste
#: profile more tidily than a story whose tags came off a keyword sweep. The
#: multiplier is applied to `Story.push()`, which is already 0..1 and already
#: decaying, so a fresh story about something they listen to comfortably beats
#: a standing explainer and a three-quarters-expired one does not.
FRESHNESS_BOOST = 1.6

#: What a *subtag* match is worth next to a facet match, in `_affinity`.
#:
#: The reported failure: "recommended episodes about a random small school
#: college football matchup that I've never indicated through my search
#: behaviour that I would be interested in". One finished episode about the
#: NFL puts weight on `sports`, every sports story in the world carries
#: `sports`, and until this every one of them scored exactly as well as an
#: episode about the thing they actually played.
#:
#: Subtags have existed since §80 and the scorer was blind to them: `sports`
#: and `sports-drama` counted the same, so the resolution that was added to
#: the *vocabulary* was being thrown away by the *ranking*. This is the other
#: half of that change. A tile matching what somebody plays specifically now
#: beats one matching only the heading it lives under, which is the whole
#: difference between "you like sport" and "you like this".
#:
#: Why a weight rather than a rule: a facet match is real evidence, just
#: weaker, and a listener with one broad interest and no subtag history must
#: still get a full rail. 1.75 is enough that one specific match outranks one
#: broad one and not so much that two broad matches are worthless.
SUBTAG_WEIGHT = 1.75

#: What each extra level of the *grown* vocabulary is worth, compounding.
#:
#: `SUBTAG_WEIGHT` is this at one level, and that is the point: a subtag is a
#: hand-written depth-1 category, and once the tree can be four levels deep
#: the same argument has to keep working all the way down. Matching
#: `cincinnati bengals` is a sharper claim about an episode than matching
#: `nfl`, which is sharper than `american football`, which is sharper than
#: `sports` - so the weight is `CATEGORY_DEPTH_WEIGHT ** depth` and the
#: reported complaint finally has a vocabulary that can express it.
#:
#: A category node minted with no parent scores as though it were at depth
#: one. A phrase enough listeners searched for is at least as specific a
#: thing as a hand-written subtag; what an orphan lacks is a *home*, not
#: specificity, and scoring it as a facet would make the keyless path worse
#: than the vocabulary it is extending.
#:
#: Same value as `SUBTAG_WEIGHT` so the two cannot drift apart. If a reason
#: ever appears to make them differ, it has to be written down here first.
CATEGORY_DEPTH_WEIGHT = SUBTAG_WEIGHT

#: How far a live story's score is cut when its only claim on this listener is
#: a whole facet and they have never been near its subject.
#:
#: `SUBTAG_WEIGHT` sharpens every comparison; this one answers the case it
#: cannot, because the vocabulary has no word for it. There is no `nfl` tag
#: and no `college-football` tag - both are `sports` - so no amount of tag
#: weighting distinguishes them and nothing should pretend it does. What can
#: be measured without inventing a vocabulary is whether this listener has
#: ever *said* any of the words in the tile: `familiar_words` reads their own
#: searches and plays out of the event log, which is a fact about them rather
#: than a guess about the subject.
#:
#: Applied to live stories only. The evergreen bank is twenty-eight standing
#: subjects chosen to be broad, so damping a bank topic for being broad would
#: damp the whole bank; a story is one specific thing that happened, and one
#: specific thing nobody has shown any interest in is exactly the complaint.
#:
#: **It damps, it never excludes.** A listener whose history is one episode
#: long has almost no familiar words, and a rule would empty their rail in
#: the name of relevance.
BROAD_MATCH_PENALTY = 0.3

#: What a live story about where this listener says they are is worth.
#:
#: Location does two things in this ranker and they are deliberately separate
#: mechanisms rather than one weighted score, which is the same shape as
#: "recency filters; credibility sorts". The *negative* one is free: a
#: listener's city and region join `familiar_words`, so a story about their
#: own town stops being damped by `BROAD_MATCH_PENALTY` for being a subject
#: they have never typed - they live there, which answers the question that
#: penalty is asking. This is the *positive* one.
#:
#: **Live stories only**, for the same reason the penalty is. The evergreen
#: bank is twenty-eight standing subjects with no place in them, and the one
#: that mentions a city mentions it generically - "How Food Gets to a City" is
#: not local news to somebody in Kansas City, and boosting it for them would
#: be the keyword sweep making a claim the tile does not support.
#:
#: **And country is not part of it** - see `preferences.Location.words`.
#: Boosting every story about the United States for every listener in the
#: United States is not personalisation, it is a different global sort order,
#: and the page already has two rails for what everybody is playing.
#:
#: 1.5 rather than something larger: a local story should beat a comparable
#: one about somewhere else and must not beat a strong match on what this
#: listener actually listens to. Somebody in Cincinnati who has never played a
#: sports episode does not want the Bengals; they want the thing they came for,
#: and the fact that it is local is a tie-breaker rather than a subject.
LOCAL_BOOST = 1.5

#: Below this, a tile is not a recommendation - it is the least bad thing left
#: in the inventory, and a rail is better short than padded with one.
#:
#: Scores are comparable across listeners because `taste` normalises to a peak
#: of 1.0, so this is a real threshold rather than a tuning knob per user. A
#: single facet match on something they barely touch lands near 0.1; one solid
#: match on something they play lands well above 0.3.
RELEVANCE_FLOOR = 0.12


@dataclass(frozen=True)
class Topic:
    """One tile. `query` is what gets generated; `title` is only a label.

    The last three fields are empty for every entry in the evergreen bank and
    filled for a tile that came out of the live story pool. They are additive
    on purpose: a bank topic is exactly the tile it always was, and nothing
    downstream has to know which kind it is holding.
    """

    id: str
    title: str
    #: The one line under the title on a browse card: **a hook, not a
    #: description.** It used to be a standing summary ("NIL money,
    #: facilities, and the new power brokers") and it was shown nowhere a
    #: listener chooses from - the card showed the *rail's reason* instead,
    #: so the question "is this episode worth three minutes" was answered
    #: with "because of what you have played". A reason is about the feed; a
    #: hook is about the episode, and only one of the two is what somebody
    #: deciding needs. See `seedWhy` in `static/index.html`.
    #:
    #: It stays **evergreen** for a bank topic, which is what separates it
    #: from `angle` below: a hook may be pointed, and it may not claim
    #: anything about today, because this string is written by hand once and
    #: read for as long as the tile exists.
    subtitle: str
    query: str
    tags: tuple[str, ...]
    icon: str
    #: The one line that says what *this* episode is about, written by the
    #: story composer. Distinct from `subtitle`, which the bank uses as a
    #: standing description: an angle is a claim about today and a subtitle is
    #: not, so a tile that has one shows it and a tile that does not is
    #: unchanged rather than being given a stale one.
    angle: str = ""
    #: Which live source put this here. Shown nowhere; it is what makes a
    #: per-source hit rate answerable, the same way prefetch counts warmed
    #: against taken. A guess nobody can score is a guess nobody can improve.
    source: str = ""
    #: `Story.push()` at the moment the feed was built: how hard this is being
    #: pushed right now, between 0 and 1. Zero for the bank, which is not
    #: pushed at all - it is simply always there.
    freshness: float = 0.0
    #: Where a live story's coverage is coming from - `Story.countries`. Read
    #: by `rank_world` only, and deliberately not serialised: it is an input
    #: to one rail's order, not something a card says.
    countries: tuple = ()
    #: How many distinct outlets are running it - `Story.coverage`, the
    #: popularity Trending ranks on (§135). Zero for the bank.
    coverage: int = 0
    #: Where it is trending - `Story.geo_scope`/`geo_key`/`geo`. The label is
    #: what a Trending card shows; the scope and key are what the rail's
    #: geography mix and the "View more" grouping read.
    geo_scope: str = ""
    geo_key: str = ""
    geo: str = ""
    #: A game's score line and status, refreshed every sweep, and when
    #: (`Story.live_line`). Drawn beside the title, never inside it.
    live_line: str = ""
    live_status: str = ""
    live_as_of: float = 0.0
    #: The last sweep that saw this story still being reported
    #: (`Story.last_seen`). Read by `trending_for` only: coverage continuing
    #: after somebody heard a story is the evidence there is something new
    #: to say about it. Not serialised.
    last_seen: float = 0.0
    #: On a follow-up tile, the id of the story it follows (§136). Empty on
    #: everything else. Not serialised.
    follows: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "subtitle": self.subtitle,
            "query": self.query,
            "tags": list(self.tags),
            "icon": self.icon,
            "angle": self.angle,
            "source": self.source,
            "freshness": round(self.freshness, 3),
            "coverage": self.coverage,
            "geo": self.geo,
            "geo_scope": self.geo_scope,
            "geo_key": self.geo_key,
            "live_line": self.live_line,
            "live_status": self.live_status,
            "live_as_of": round(self.live_as_of, 1),
        }


# Keywords that map a free-text search onto the same facets the bank uses.
# Deliberately dumb: a wrong tag costs one mediocre recommendation, and a
# model call to classify every search would cost more than the episode it is
# recommending. See PROBLEMS for why this is the right trade at this size.
TAG_WORDS: dict[str, tuple[str, ...]] = {
    "sports": ("nfl", "nba", "football", "basketball", "golf", "soccer", "tennis",
               "olympics", "coach", "playoff", "draft", "league", "match"),
    "business": ("startup", "founder", "company", "ceo", "ipo", "merger", "layoff",
                 "strategy", "brand", "hiring", "venture"),
    "money": ("fed", "inflation", "rates", "market", "stocks", "economy", "tariff",
              "recession", "housing", "oil", "currency", "bond"),
    "tech": ("ai", "software", "chip", "robot", "app", "model", "data", "code",
             "computing", "algorithm", "internet"),
    "science": ("physics", "space", "rocket", "climate", "biology", "brain",
                "research", "study", "energy", "quantum", "genome"),
    "health": ("sleep", "diet", "exercise", "habit", "stress", "anxiety", "mindset",
               "motivation", "longevity", "cortisol", "mental", "therapy"),
    "culture": ("film", "movie", "music", "album", "book", "art", "hollywood",
                "song", "show", "artist", "fashion"),
    "world": ("election", "war", "treaty", "border", "sanctions", "summit",
              "government", "protest", "strait", "diplomacy", "policy"),
}

#: The same eight facets, with a word a listener would recognise on a button.
#: The intro's interest picker is built from this, so a facet added to
#: TAG_WORDS without a label here would rank episodes it could never be chosen
#: for - tests/test_preferences.py fails on the mismatch rather than letting
#: the two drift.
#:
#: **These eight are the whole picker, and that is deliberate.** The ranking
#: vocabulary below is much larger; the *pickable* one is not, because a
#: twenty-seven button intro is a worse question than an eight button one and
#: would buy a signal the listener's behaviour supplies within a day anyway.
TAG_LABELS: dict[str, str] = {
    "sports": "Sport",
    "business": "Business",
    "money": "Money & markets",
    "tech": "Technology",
    "science": "Science",
    "health": "Health & mind",
    "culture": "Culture",
    "world": "World",
}

#: The eight facets, as a set, for the checks that care about the difference
#: between "a tag" and "a tag somebody can choose".
FACETS: frozenset[str] = frozenset(TAG_LABELS)

#: The same eight, short enough to sit inside a circle.
#:
#: The first run draws them on a wheel and "Money & markets" does not fit in a
#: 78px disc at a readable size. This is a *shortening*, never a second name:
#: every entry is a prefix or the whole of its `TAG_LABELS` value, so nothing
#: in the app calls one facet two things. Settings, the wheels and the
#: catalogue all still read the full label.
TAG_SHORT: dict[str, str] = {
    "sports": "Sport",
    "business": "Business",
    "money": "Money",
    "tech": "Tech",
    "science": "Science",
    "health": "Health",
    "culture": "Culture",
    "world": "World",
}

#: How many of them the first-run picker actually shows.
#:
#: The eight are still the whole pickable vocabulary; this is how many are put
#: in front of somebody at once. Eight was every facet there is, in dictionary
#: order, which is not an answer to "what are people listening to" - it is the
#: order the file happens to be written in. Six is the designs' grid, and the
#: two that do not make it are reachable through "View more", which carries
#: every facet on its interests. So this narrows the *screen* and not the
#: vocabulary, which is the line CLAUDE.md draws.
PICKER_SIZE = 6

#: The order to offer them in when nothing has been played yet.
#:
#: A fresh deployment has an empty log, which is the normal state on the run
#: this screen exists for - so there has to be a declared answer rather than
#: whichever six `dict` iteration puts first. Written down, in one place, so
#: it is a decision somebody made and can be argued with.
PICKER_DEFAULT_ORDER: tuple[str, ...] = (
    "world", "tech", "sports", "business", "money", "science",
    "health", "culture",
)


def popular_facets(
    store: "EventStore", limit: int = PICKER_SIZE, now: Optional[float] = None
) -> tuple[list[str], str]:
    """The facets to put in the picker, most played across FAM first.

    Global, like `rank_most_played` and for the same reason: this is asked on
    the first run, when the listener has no history of their own and the only
    honest signal is everybody else's. One count serves every listener.

    Returns the ids *and where they came from* - `"played"` or `"default"`.
    The two look identical on screen and the difference matters, because a
    deployment whose log is empty is showing a declared order rather than a
    measurement, and saying which is the difference between a fact and a
    claim about what people like.
    """
    now = time.time() if now is None else now
    by_id = {t.id: t for t in TOPIC_BANK}
    counts: dict[str, int] = {}
    for _user, topic_id in store.plays_since(now - TRENDING_WINDOW):
        topic = by_id.get(topic_id)
        if topic is None:
            continue
        for facet in facets_only(topic.tags):
            counts[facet] = counts.get(facet, 0) + 1

    if not counts:
        return list(PICKER_DEFAULT_ORDER[:limit]), "default"
    # Ties break on the declared order rather than on `dict` insertion, so two
    # equally played facets do not swap places between requests.
    rank = {tag: i for i, tag in enumerate(PICKER_DEFAULT_ORDER)}
    ordered = sorted(TAG_LABELS, key=lambda t: (-counts.get(t, 0), rank.get(t, 99)))
    return ordered[:limit], "played"


def my_facets(
    store: "EventStore", user_id: str, chosen: Iterable[str] = (),
    limit: int = PICKER_SIZE,
) -> tuple[list[str], str]:
    """The facets *this* listener has actually listened to, most first.

    The sibling of `popular_facets` and deliberately a different question.
    That one is asked on the first run, of somebody with no history, so the
    only honest signal is everybody else's. This one is asked from Settings by
    somebody who has been using the app, where their own listening is a better
    answer than the crowd's - and it keeps answering differently as they use
    it, which is the point.

    Three sources, in order, and the order is the whole design:

    1. **what they played**, which is the live half;
    2. **what they chose**, which is what they said before they had played
       anything, kept in their stored order;
    3. **`PICKER_DEFAULT_ORDER`**, as filler.

    The filler is not decoration. A wheel is six discs or it is a broken
    wheel, and a listener who has played two facets and chosen none would
    otherwise get two - so the shape stays and `source` says which of the
    three actually decided it. A declared order and a measurement look
    identical on a screen full of circles.
    """
    counts: dict[str, int] = {}
    for event in store.for_user(user_id):
        if event.kind not in ("play", "complete"):
            continue
        for facet in facets_only(event.tags):
            counts[facet] = counts.get(facet, 0) + 1

    rank = {tag: i for i, tag in enumerate(PICKER_DEFAULT_ORDER)}
    out: list[str] = sorted(
        (t for t in counts if t in TAG_LABELS),
        key=lambda t: (-counts[t], rank.get(t, 99)),
    )
    source = "listened" if out else ""

    for tag in chosen or ():
        if tag in TAG_LABELS and tag not in out:
            out.append(tag)
            source = source or "chosen"
    for tag in PICKER_DEFAULT_ORDER:
        if len(out) >= limit:
            break
        if tag not in out:
            out.append(tag)
            source = source or "default"
    return out[:limit], source or "default"


@dataclass(frozen=True)
class Interest:
    """One named thing a listener can say they are interested in."""

    id: str
    label: str
    #: A key into the interface's own small icon set. Named rather than drawn
    #: here: this module knows nothing about SVG, and several interests share
    #: one glyph on purpose (every music genre is a note, as the design has
    #: it).
    icon: str
    #: What choosing it means to the ranker. The facet first, then any subtag
    #: the interest genuinely carries.
    tags: tuple[str, ...]

    def as_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "icon": self.icon,
                "tags": list(self.tags)}


#: The catalogue behind "View more" on the first run.
#:
#: **These are interests, not tags, and that distinction is the whole reason
#: this can exist.** CLAUDE.md is emphatic that the eight facets are the only
#: *pickable tag* vocabulary, and they still are - the chips above this
#: catalogue are `TAG_LABELS` and nothing else. What somebody picks here is a
#: named subject; the tags come along with it, and a listener never reads one.
#:
#: That is what lets the list be this long and this specific. "Formula 1" is
#: not a tag anybody could have been offered - `sports` is - but it is a
#: perfectly good thing to say about yourself, and it lands on the ranker as
#: `sports` plus whatever subtag it really carries. Eight buttons cannot
#: express it; seventy-three named subjects can, without adding one word to
#: the vocabulary the ranker reasons in.
#:
#: The list and its order come from the reference designs. One entry is worth
#: flagging rather than quietly keeping: `Iran Conflict` is a live news event
#: rather than a durable interest, so unlike everything else here it will go
#: stale. It is in the designs, so it is in the list.
INTEREST_CATALOGUE: tuple[Interest, ...] = (
    # id, label, icon, tags (facet first, then any subtag it carries)
    Interest("soccer",        "Soccer",                  "ball",      ("sports",)),
    Interest("stocks",        "Stocks & Economy",        "chart",     ("money", "macro")),
    Interest("politics",      "Politics",                "ballot",    ("world",)),
    Interest("iran",          "Iran Conflict",           "news",      ("world", "geopolitics")),
    Interest("sports",        "Sports",                  "tennis",    ("sports",)),
    Interest("business",      "Business & Finance",      "briefcase", ("business",)),
    Interest("science",       "Science",                 "atom",      ("science",)),
    Interest("technology",    "Technology",              "chip",      ("tech",)),
    Interest("art",           "Art",                     "palette",   ("culture",)),
    Interest("movies-tv",     "Movies & TV",             "film",      ("culture", "film-tv")),
    Interest("ai",            "Artificial Intelligence", "sparkle",   ("tech", "ai")),
    Interest("gaming",        "Gaming",                  "gamepad",   ("culture", "internet-culture")),
    Interest("crypto",        "Cryptocurrency",          "coin",      ("money",)),
    Interest("nfl",           "NFL",                     "shield",    ("sports",)),
    Interest("anime",         "Anime",                   "anime",     ("culture", "film-tv")),
    Interest("travel",        "Travel",                  "plane",     ("culture",)),
    Interest("food-drink",    "Food & Drink",            "chef",      ("culture", "food")),
    Interest("baseball",      "Baseball",                "baseball",  ("sports",)),
    Interest("basketball",    "Basketball",              "basketball",("sports",)),
    Interest("beauty",        "Beauty",                  "beauty",    ("culture",)),
    Interest("boxing",        "Boxing",                  "glove",     ("sports",)),
    Interest("career",        "Career",                  "cap",       ("business",)),
    Interest("cars",          "Cars",                    "car",       ("tech",)),
    Interest("pets",          "Pets",                    "paw",       ("culture",)),
    Interest("celebs",        "Celebs",                  "star",      ("culture",)),
    Interest("music",         "Music",                   "note",      ("culture", "music")),
    Interest("country-music", "Country Music",           "note",      ("culture", "music")),
    Interest("news",          "News",                    "news",      ("world",)),
    Interest("dance",         "Dance",                   "disco",     ("culture",)),
    Interest("dating",        "Dating & Relationships",  "hearts",    ("health", "mind")),
    Interest("design",        "Design",                  "design",    ("culture",)),
    Interest("education",     "Education",               "cap",       ("science",)),
    Interest("electronic",    "Electronic Music",        "note",      ("culture", "music")),
    Interest("startups",      "Startups",                "briefcase", ("business", "founders")),
    Interest("esports",       "Esports",                 "gamepad",   ("culture", "internet-culture")),
    Interest("family",        "Marriage & Family",       "people",    ("health", "mind")),
    Interest("fashion",       "Fashion",                 "shirt",     ("culture",)),
    Interest("pop",           "Pop",                     "note",      ("culture", "music")),
    Interest("golf",          "Golf",                    "golf",      ("sports",)),
    Interest("kpop",          "K-pop",                   "note",      ("culture", "music")),
    Interest("memes",         "Memes",                   "meme",      ("culture", "internet-culture")),
    Interest("health",        "Health & Fitness",        "pulse",     ("health", "fitness")),
    Interest("mma",           "MMA & Wrestling",         "glove",     ("sports",)),
    Interest("motorsport",    "Racing & Motorsports",    "car",       ("sports",)),
    Interest("motorcycles",   "Motorcycles",             "bike",      ("sports",)),
    Interest("nature",        "Nature & Outdoors",       "tree",      ("science",)),
    Interest("hockey",        "Ice Hockey",              "hockey",    ("sports",)),
    Interest("olympics",      "Olympics",                "tennis",    ("sports",)),
    Interest("personal-fin",  "Personal Finance",        "doc",       ("money",)),
    Interest("photography",   "Photography",             "camera",    ("culture",)),
    Interest("podcasts",      "Podcasts",                "mic",       ("culture", "media-business")),
    Interest("real-estate",   "Real Estate",             "thumb",     ("money", "housing")),
    Interest("robotics",      "Robotics",                "chip",      ("tech",)),
    Interest("rock",          "Rock",                    "note",      ("culture", "music")),
    Interest("rugby",         "Rugby",                   "rugby",     ("sports",)),
    Interest("shopping",      "Shopping",                "bag",       ("money", "consumer-prices")),
    Interest("snow-sports",   "Snow Sports",             "snow",      ("sports",)),
    Interest("software",      "Software Development",    "laptop",    ("tech", "platforms")),
    Interest("space",         "Space",                   "rocket",    ("science", "space")),
    Interest("tennis",        "Tennis",                  "tennis",    ("sports",)),
    Interest("home-garden",   "Home & Garden",           "home",      ("money", "housing")),
    Interest("cricket",       "Cricket",                 "cricket",   ("sports",)),
    Interest("formula1",      "Formula 1",               "car",       ("sports",)),
    Interest("cycling",       "Cycling",                 "bike",      ("sports", "fitness")),
    Interest("jpop",          "J-pop",                   "note",      ("culture", "music")),
    Interest("concerts",      "Concerts",                "note",      ("culture", "music")),
    Interest("hiphop",        "Hip Hop",                 "note",      ("culture", "music")),
    Interest("jazz",          "Jazz",                    "note",      ("culture", "music")),
    Interest("crime",         "Crime",                   "news",      ("world",)),
    Interest("elections",     "Elections",               "ballot",    ("world", "elections")),
    Interest("biotech",       "Biotech",                 "atom",      ("science", "body-science")),
    Interest("mental-health", "Mental Health",           "pulse",     ("health", "mind")),
    Interest("digital-art",   "Digital Art",             "palette",   ("culture",)),
)

CATALOGUE_BY_ID: dict[str, Interest] = {i.id: i for i in INTEREST_CATALOGUE}

#: Every subtag, and the facet it lives under.
#:
#: **Why this exists.** Eight tags over a twenty-eight topic bank cannot
#: express a preference finely enough to rank one. Measured before this was
#: added: a listener whose entire history was `tech` had exactly four topics
#: with any positive affinity and three of them scored *identically*
#: (0.7071), so the grid they were shown was one real recommendation followed
#: by `topic.id` in alphabetical order. That is not a tuning problem -
#: no weight or half-life fixes a scoring function whose inputs only take
#: twenty distinct values - it is a vocabulary problem, and this is the
#: vocabulary.
#:
#: A subtag never replaces its facet, it refines it: a topic carries both, and
#: `tags_for_text` adds the parent to every subtag it matches. So a listener
#: who chose "Technology" in the intro still matches every tech episode, and
#: one who has only ever finished episodes about chips now outranks them
#: against each other. Nothing that worked on the eight stops working.
TAG_PARENT: dict[str, str] = {
    # sport
    "sports-business": "sports", "sports-performance": "sports",
    "sports-drama": "sports",
    # business
    "founders": "business", "media-business": "business",
    "strategy": "business", "supply-chain": "business",
    # money
    "macro": "money", "housing": "money", "commodities": "money",
    "consumer-prices": "money",
    # tech
    "ai": "tech", "chips": "tech", "platforms": "tech",
    # science
    "space": "science", "body-science": "science", "energy": "science",
    # health
    "sleep": "health", "mind": "health", "habits": "health",
    "longevity": "health", "fitness": "health",
    # culture
    "film-tv": "culture", "music": "culture", "food": "culture",
    "internet-culture": "culture",
    # world
    "geopolitics": "world", "elections": "world", "cities": "world",
}

#: Keywords for the subtags. Same dumb set-intersection as the facets above
#: and the same trade: a wrong tag costs one mediocre recommendation. A miss
#: here is cheaper still than a miss on a facet, because the facet's own
#: keywords are unchanged and still catch the episode - a subtag only ever
#: adds resolution, it never takes the topic out of its facet.
SUBTAG_WORDS: dict[str, tuple[str, ...]] = {
    "sports-business": ("nil", "salary", "cap", "transfer", "stadium",
                        "contract", "sponsorship", "franchise", "wages"),
    "sports-performance": ("training", "fitness", "conditioning", "technique",
                           "swing", "endurance", "recovery"),
    "sports-drama": ("rivalry", "upset", "comeback", "trade", "scandal"),
    "founders": ("founder", "startup", "ceo", "entrepreneur", "cofounder"),
    "media-business": ("streaming", "studio", "subscriber", "catalogue",
                       "broadcast", "licensing"),
    "strategy": ("pricing", "margin", "moat", "positioning", "competition"),
    "supply-chain": ("supply", "logistics", "shipping", "factory",
                     "warehouse", "distribution", "freight"),
    "macro": ("fed", "inflation", "rates", "recession", "gdp",
              "unemployment", "monetary"),
    "housing": ("housing", "mortgage", "rent", "property", "homes"),
    "commodities": ("oil", "gas", "copper", "wheat", "barrel", "commodity"),
    "consumer-prices": ("price", "prices", "cost", "checkout", "discount"),
    "ai": ("ai", "model", "agent", "llm", "training", "inference",
           "neural", "chatbot"),
    "chips": ("chip", "chips", "semiconductor", "fab", "foundry",
              "wafer", "lithography"),
    "platforms": ("platform", "feed", "algorithm", "app", "social",
                  "viral", "engagement"),
    "space": ("space", "rocket", "orbit", "satellite", "launch", "mars",
              "moon"),
    "body-science": ("brain", "genome", "cell", "biology", "neuroscience",
                     "metabolism", "immune"),
    "energy": ("grid", "solar", "wind", "nuclear", "battery", "renewable",
               "electricity"),
    "sleep": ("sleep", "insomnia", "circadian", "nap", "rest"),
    "mind": ("anxiety", "stress", "mindset", "motivation", "focus",
             "therapy", "mental", "worry"),
    "habits": ("habit", "habits", "routine", "discipline", "willpower",
               "morning"),
    "longevity": ("longevity", "ageing", "aging", "lifespan", "supplement"),
    "fitness": ("exercise", "workout", "strength", "cardio", "athlete"),
    "film-tv": ("film", "movie", "hollywood", "series", "actor", "director",
                "box"),
    "music": ("song", "album", "artist", "band", "chart", "tour"),
    "food": ("restaurant", "chef", "menu", "kitchen", "dining", "cuisine"),
    "internet-culture": ("meme", "creator", "influencer", "trend",
                         "attention", "scroll"),
    "geopolitics": ("sanctions", "treaty", "strait", "border", "alliance",
                    "diplomacy", "tariff"),
    "elections": ("election", "ballot", "vote", "voter", "poll", "campaign"),
    "cities": ("city", "transit", "infrastructure", "urban", "housing"),
}

# One vocabulary from here down: facets and subtags in a single table, so
# every lookup is one dict and a tag is a tag. TAG_PARENT is what says which
# of them a listener could have chosen in the intro.
TAG_WORDS = {**TAG_WORDS, **SUBTAG_WORDS}

#: What one declared interest is worth next to real behaviour, in `taste`.
#: Equal to a play and well under a completion (EVENT_WEIGHT), and it does not
#: decay - so it carries a new listener's first feed and is quietly outvoted
#: once they have actually listened to anything. Higher and the intro would
#: pin the feed for weeks; lower and choosing six things would change nothing,
#: which is worse than not asking.
INTEREST_WEIGHT = 1.0

_WORD = re.compile(r"[a-z0-9]+")

#: The grown vocabulary, opened once per process.
#:
#: Lazy rather than module-level, for the reason every store in this app is
#: lazy: `data_path` reads the environment at call time, so a test that sets
#: `CATEGORIES_DB` and imports this module has to get the database it asked
#: for. Cached afterwards because `match()` is called for every tile on a
#: browse page and opening a connection per call would be the cost this
#: module is arranged to avoid.
_CATEGORIES = None


def category_tree() -> "categories.CategoryStore":
    """The tree, opened on first use. Never raises.

    A failure here returns an empty tree rather than propagating, which means
    the feed falls back to the hand-written vocabulary - the one every
    deployment ran on before this existed. The rule the whole module keeps: a
    vocabulary that grows itself may add resolution and may never take the
    page away.
    """
    global _CATEGORIES
    if _CATEGORIES is None:
        try:
            _CATEGORIES = categories.CategoryStore()
        except Exception:
            log.exception("could not open the category tree; "
                          "ranking on the hand-written vocabulary alone")
            _CATEGORIES = categories.EmptyTree()
    return _CATEGORIES


def reset_category_tree() -> None:
    """Drop the cached tree. For tests, and after a sweep in another process.

    Takes the tile-tag memo with it. That memo keys itself on the tree's
    `_loaded_at`, so in the ordinary case it would notice on its own - but
    "on its own" there means *two floats from the clock differ*, and a
    sentinel the clock could reproduce is exactly what §113 was. The memo is
    derived from the tree; dropping one drops the other, by construction.
    """
    global _CATEGORIES
    _CATEGORIES = None
    reset_topic_tags()


def tags_for_text(text: str) -> tuple[str, ...]:
    """Best-effort facets and subtags for a free search, so history can rank.

    A matched subtag brings its facet with it. That is what keeps this change
    additive: "how are chips actually made" now carries `chips`, but it still
    carries `tech`, so it counts for a listener who only ever said Technology
    exactly as much as it did before subtags existed. The reverse is not true
    and should not be - matching the facet says nothing about which part of it
    was meant.

    **And the grown vocabulary, which is where the ceiling came off.**
    `TAG_WORDS` is thirty-seven hand-written keyword lists two levels deep;
    `categories.py` is a tree with no depth limit, minted from what listeners
    actually searched for, and a match there brings its whole ancestry with
    it for exactly the reason a subtag brings its facet. So "the cincinnati
    bengals game" carries `sports` from the keyword map and, once enough
    people have asked about them, `cincinnati bengals`, `nfl` and
    `american football` from the tree.

    The two vocabularies cannot collide: `categories.mint` refuses to mint a
    phrase that is already a facet, so no key in the returned tuple means two
    different things.

    Returned sorted for a stable order. These tuples are written into the
    event log and compared in tests, and dict iteration order is a poor thing
    to have quietly load-bearing underneath either.
    """
    words = set(_WORD.findall(text.lower()))
    found = {tag for tag, keys in TAG_WORDS.items() if words & set(keys)}
    found |= {TAG_PARENT[tag] for tag in found if tag in TAG_PARENT}
    found |= set(category_tree().match(text))
    return tuple(sorted(found))


#: Words that say nothing about a subject. Kept short on purpose: this list
#: only has to stop `familiar_words` matching a tile on "the" and "what", and
#: a long one starts quietly deciding that real subjects are noise.
FAMILIAR_STOPWORDS = frozenset("""
a an and are as at be been but by can did do does for from had has have how
in into is it its just like made make more most new not now of off on one or
our out over should so than that the their them then there these they this
to too until up was way we were what when where which who why will with would
you your about after again all any because before being between both down
each few here him his i if me my no nor only other own same she some such
their theirs through under very
""".split())

#: How long a word has to be before it counts as a subject word.
FAMILIAR_MIN_WORD = 3


def familiar_words(events: Iterable[Event]) -> frozenset[str]:
    """Every subject word this listener has actually said or played.

    Read off the event log's own `text` - the question they typed, the tile
    they tapped - which is the only record in FAM of *what* somebody was
    interested in rather than which of eight headings it lived under.

    This exists because the tag vocabulary cannot answer the complaint it is
    used for. There is no tag for the NFL and none for college football; both
    are `sports`, and no weighting distinguishes them because nothing in the
    vocabulary knows they are different. What is knowable without inventing a
    vocabulary is whether the words on a tile have ever appeared in anything
    this listener did, and that is a measurement rather than a guess.

    Deliberately crude, in the same way `tags_for_text` is deliberately crude.
    A set intersection over lower-cased words, no stemming, no embedding, no
    model call. A miss costs one tile being damped that need not have been,
    which is a damping and not a filter - see `BROAD_MATCH_PENALTY`.
    """
    out: set[str] = set()
    for event in events:
        if not event.text:
            continue
        for word in _WORD.findall(event.text.lower()):
            if len(word) >= FAMILIAR_MIN_WORD and word not in FAMILIAR_STOPWORDS:
                out.add(word)
    return frozenset(out)


def _is_specific(tag: str) -> bool:
    """Whether this tag says something narrower than one of the eight headings.

    Two vocabularies can say it and they are both counted here: a hand-written
    subtag (`TAG_PARENT` holds exactly the tags nobody could have been
    *offered* and everybody's behaviour still reveals) and a grown category
    node, which is the same claim at any depth. `categories.mint` refuses to
    mint a facet or a subtag slug, so the two sets cannot overlap and a facet
    can never arrive here as a category.

    Split out so `tag_weight` and `_is_broad_match` cannot drift about what
    "specific" means. They are the two places that ask, they answer different
    questions with it - how much is this worth, and is this tile broad - and
    the first already counted a category and the second did not.
    """
    if tag in TAG_LABELS:
        return False
    return tag in TAG_PARENT or category_tree().get(tag) is not None


def _is_broad_match(topic: Topic, profile: dict[str, float]) -> bool:
    """True when nothing specific about this tile matches this listener.

    **It reads `topic_tags` and counts a grown category as specific**, and
    that is the half of the vocabulary join that `_affinity` alone did not
    finish. This penalty is documented as answering "the case the vocabulary
    **cannot express**: there is no tag for the NFL and none for college
    football, both are `sports`" - and since §126 the vocabulary *can* express
    it. Left reading the declared tuple, this function damped a live story
    about college football by `BROAD_MATCH_PENALTY` for a listener whose
    profile literally contains `college football`, because the story's only
    hand-written tag is `sports`. The one thing that rescued such a tile was
    `_subject_is_familiar`, which reads raw search words precisely because the
    tags could not say it - so the workaround was carrying a case the
    vocabulary now covers properly.

    A tile the tree says nothing about is unchanged: `topic_tags` returns the
    declared tuple, and the only tags that can be specific in it are subtags,
    which is exactly what this asked before.
    """
    return not any(profile.get(tag, 0.0) > 0 for tag in topic_tags(topic)
                   if _is_specific(tag))


def _is_local(topic: Topic, local: frozenset[str]) -> bool:
    """Whether this tile is about where the listener says they are.

    Reads the same three strings `_subject_is_familiar` does and answers a
    different question, so the two stay separate functions: one asks whether
    they have been near this subject, this one asks whether they live in it.

    Live stories only. `freshness` is what distinguishes a story from a bank
    topic here, exactly as it does in `rank_from_history`, and it is set by
    `topics_from_stories` and by nothing else.
    """
    if not local or topic.freshness <= 0:
        return False
    words = {w for w in _WORD.findall(
        f"{topic.title} {topic.query} {topic.angle}".lower())
        if len(w) >= FAMILIAR_MIN_WORD and w not in FAMILIAR_STOPWORDS}
    return bool(words & local)


def _subject_is_familiar(topic: Topic, familiar: frozenset[str]) -> bool:
    """Whether this listener has ever been near the words on this tile."""
    if not familiar:
        return False
    words = {w for w in _WORD.findall(f"{topic.title} {topic.query}".lower())
             if len(w) >= FAMILIAR_MIN_WORD and w not in FAMILIAR_STOPWORDS}
    return bool(words & familiar)


def facet_of(tag: str) -> str:
    """The pickable facet a tag belongs to; a facet is its own facet."""
    return TAG_PARENT.get(tag, tag)


def facets_only(tags: Iterable[str]) -> list[str]:
    """Fold a mix of facets and subtags up to facets, keeping first-seen order.

    Anything a *listener* reads - a rail's subjects, a mix's icon - has to
    speak in the eight words they were actually offered. `sleep` is a better
    ranking signal than `health` and a worse sentence: nobody chose it, and
    TAG_LABELS has no word for it on purpose. So the resolution stays inside
    the ranker and the vocabulary that reaches a screen is unchanged.
    """
    out: list[str] = []
    for tag in tags:
        facet = facet_of(tag)
        if facet in TAG_LABELS and facet not in out:
            out.append(facet)
    return out


TOPIC_BANK: tuple[Topic, ...] = (
    Topic("nil-arms-race", "The New College Football Arms Race",
          "Somebody is paying. It isn't who you think.",
          "how NIL money changed college football recruiting", ("sports", "money", "sports-business"), "sports"),
    Topic("operator-ceos", "Why Founders Are Taking Back Control",
          "The grown-ups were meant to fix it. They didn't.",
          "why boards are keeping founders as CEO", ("business", "founders"), "business"),
    Topic("ai-agents", "Why Everyone Is Talking About AI Agents",
          "Everyone says it. Almost nobody defines it.",
          "what AI agents are and why they matter now", ("tech", "ai"), "tech"),
    Topic("hollywood-comebacks", "Inside the Best Hollywood Comebacks",
          "Nobody comes back by accident. It's engineered.",
          "how Hollywood comeback stories actually happen", ("culture", "film-tv"), "camera"),
    Topic("golf-evolution", "The Quiet Evolution of Golf",
          "The money changed the game. Then the game changed.",
          "how professional golf formats are changing", ("sports", "sports-performance"), "golf"),
    Topic("habits-research", "The Habits That Actually Change Your Life",
          "Twenty-one days is a myth. Here's what isn't.",
          "what habit research actually shows about lasting change", ("health", "habits"), "leaf"),
    Topic("fed-next-move", "The Fed's Next Move, Explained",
          "Twelve people, one decision, everybody's mortgage.",
          "what the Federal Reserve is likely to do about interest rates",
          ("money", "macro"), "business"),
    Topic("space-race", "Inside the New Space Race",
          "Getting to orbit stopped being the hard part.",
          "how reusable rockets changed the economics of spaceflight",
          ("science", "business", "space"), "rocket"),
    Topic("song-breaks-internet", "How One Song Breaks the Internet",
          "Hits aren't found any more. They're triggered.",
          "how a song becomes a hit through playlists and short video",
          ("culture", "tech", "music", "platforms"), "music"),
    Topic("restaurant-scene", "The Restaurants Everyone's Talking About",
          "The food is rarely why the line is that long.",
          "why some restaurants become impossible to book", ("culture", "food"), "food"),
    Topic("the-trade", "The Trade That Changed Everything",
          "One phone call, and a decade went differently.",
          "how a single trade reshapes a sports franchise", ("sports", "sports-drama"), "sports"),
    Topic("sleep-science", "What We Actually Know About Sleep",
          "Eight hours is advice, not a finding.",
          "what sleep research actually establishes", ("health", "science", "sleep", "body-science"), "leaf"),
    Topic("chip-supply", "Who Actually Makes the World's Chips",
          "One island makes them. Everyone else waits.",
          "why semiconductor manufacturing is concentrated in so few places",
          ("tech", "world", "chips", "geopolitics"), "tech"),
    Topic("hormuz", "The Two-Mile Lane That Moves the Oil Price",
          "One narrow lane, and the oil market flinches.",
          "why the Strait of Hormuz moves the oil price", ("world", "money", "geopolitics", "commodities"), "business"),
    Topic("morning-mindset", "What to Do With the First Ten Minutes",
          "What you do first is not a small decision.",
          "a good mindset for when I wake up in the morning", ("health", "habits", "mind"), "leaf"),
    Topic("founder-motivation", "Where Motivation Actually Comes From",
          "It arrives after you start, not before.",
          "finding the motivation for my startup", ("health", "business", "mind", "founders"), "leaf"),
    Topic("housing-market", "Why Houses Cost What They Cost",
          "Four people blame four different things.",
          "what actually drives house prices", ("money", "housing"), "business"),
    Topic("longevity-claims", "Sorting the Longevity Claims",
          "Some of it works. Most of it is for sale.",
          "which longevity interventions have real evidence", ("health", "science", "longevity", "body-science"), "leaf"),
    Topic("streaming-economics", "Why Streaming Keeps Getting Worse",
          "It was never going to stay cheap. Here's why.",
          "why streaming services keep raising prices and losing shows",
          ("culture", "business", "film-tv", "media-business"), "camera"),
    Topic("election-mechanics", "How a Close Election Is Actually Called",
          "Nobody counts every vote before it's called.",
          "how news organisations decide to call an election", ("world", "elections"), "business"),
    Topic("energy-grid", "What the Grid Does When the Wind Drops",
          "Somebody balances the grid every single second.",
          "how electricity grids handle intermittent renewable power",
          ("science", "money", "energy", "commodities"), "rocket"),
    Topic("attention-economy", "The Fight for Fifteen Seconds",
          "Fifteen seconds rewrote TV, music and the news.",
          "how short-form video changed the media business", ("tech", "culture", "platforms", "internet-culture"), "music"),
    Topic("transfer-window", "How a Transfer Window Actually Works",
          "The deal was done weeks before you heard it.",
          "how football transfer deals actually get done", ("sports", "money", "sports-business"), "sports"),
    Topic("stadium-money", "Who Really Pays for a Stadium",
          "The team owns it. You probably paid for it.",
          "who actually pays for new sports stadiums", ("sports", "money", "world", "sports-business", "cities"), "business"),
    Topic("anxiety-loop", "Why Worry Feels Productive",
          "Worrying feels like working. It is not.",
          "why worrying feels useful when it is not", ("health", "mind"), "leaf"),
    Topic("pricing-psychology", "Why Everything Ends in Ninety-Nine",
          "One penny, and it reads as a different number.",
          "what the evidence says about psychological pricing", ("business", "money", "strategy", "consumer-prices"), "business"),
    Topic("food-supply", "How Food Gets to a City",
          "A city holds days of food, not weeks.",
          "how a city's food supply chain actually works", ("world", "business", "supply-chain", "cities"), "food"),
    Topic("training-load", "How Athletes Are Actually Trained Now",
          "The hard part is deciding when to stop.",
          "how modern athletic training load is managed", ("sports", "health", "sports-performance", "fitness"), "sports"),
)

BANK_BY_ID = {t.id: t for t in TOPIC_BANK}

#: The cold-start inventory, as tiles. See `startup.py` for what these are and
#: why there are eight of them.
#:
#: `freshness` is left at zero, which is worth saying out loud because these
#: are the freshest tiles on the page. That field means one specific thing -
#: `Story.push()`, how hard the live pool is pushing this story right now -
#: and `rank_from_history` reads `freshness > 0` as "this is a live story" in
#: order to apply `BROAD_MATCH_PENALTY`. Setting it here to express "this is
#: about today" would therefore both lie about the field and damp the tiles it
#: was meant to lift. A startup topic leads because `rank_startup` puts it
#: first, not because a number was borrowed from another subsystem.
STARTUP_TOPICS: tuple[Topic, ...] = tuple(
    Topic(spec_id, title, hook, query, (facet,), icon)
    for spec_id, title, hook, query, facet, icon in startup.STARTUP_TOPICS
)

STARTUP_BY_ID = {t.id: t for t in STARTUP_TOPICS}

#: The local startup question with its place left unfilled.
#:
#: **Resolvable for tags, and never offerable.** Those are two different
#: questions and this template answers only the first:
#:
#: * `tags_for_id` has to answer for `su-local` months after the tap, when
#:   nobody knows which city it was about. A play on a startup tile is the
#:   *first real thing the ranker learns*, and an id it cannot resolve falls
#:   through to a keyword sweep of "what has changed recently in and around
#:   Cincinnati, Ohio", which matches nothing in `TAG_WORDS` and teaches it
#:   precisely nothing.
#: * `known_topics` and `STARTUP_BY_ID` must **not** hold it, because both
#:   are read to put a tile on a screen and this one's title still says
#:   "What Changed in {place}". A rail that drew it would print the braces.
#:
#: It was in `STARTUP_BY_ID` for one revision and the startup suite caught it
#: immediately - "View more carries the whole set" stopped being true the
#: moment the set contained something the screen could not draw.
def _startup_topic(spec) -> Topic:
    """One `startup.StartupSpec` as a `Topic`. The seam between the two
    modules, in one place so the template and a filled copy cannot be built
    differently."""
    spec_id, title, hook, query, facet, icon = spec
    return Topic(spec_id, title, hook, query, (facet,), icon)


LOCAL_STARTUP = _startup_topic(startup.LOCAL_TOPIC)


def local_startup_topic(place: str) -> Optional[Topic]:
    """The cold-start tile about where this listener says they are, or None.

    The one place in the ranker where a location changes *what is offered*
    rather than how it is ordered - and it is still only an offer: the tile
    carries a question, the question is researched on the tap like every
    other, and the episode it produces is cached under that question for
    everybody else in the same town. A listener's location never reaches
    `EpisodePlan`, so it never reaches `pipeline.key_for`, so the shared cache
    stays shared. See `preferences.Location`.
    """
    spec = startup.local_spec(place)
    return _startup_topic(spec) if spec else None

#: How much less each facet is worth than the one above it in the startup
#: prior. Eight facets, so the last is worth 1 - 7 * 0.08 = 0.44 of the first.
#:
#: It has to decay at all: a flat prior scores all eight startup topics
#: identically and the rail falls back to sorting on `topic.id`, which is
#: alphabetical by slug and is exactly the fake ordering §98 took out of the
#: first-run picker. It has to decay *gently* because the ordering it is
#: expressing is a weak signal - what other people play - and a steep one
#: would make the eighth facet effectively unofferable to a listener who may
#: well have wanted it. Every weight stays well clear of RELEVANCE_FLOOR.
STARTUP_PRIOR_STEP = 0.08


def browse_inventory(live: Iterable[Topic], has_account: bool) -> list[Topic]:
    """What a browse rail is allowed to *offer* this listener, as one list.

    Three inventories exist and they are not interchangeable:

    * the **live story pool** - today, composed once for everybody, and empty
      until a deployment sets `GDELT=1`;
    * the **startup set** - eight time-anchored questions, one per facet,
      researched on the tap by `SEARCH_MODE=always` and therefore current
      whenever it is played;
    * the **evergreen bank** - twenty-eight standing explainers, as true in
      March as today, which is what makes them cheap to share and what makes
      them generic.

    **The live pool is offered to everybody. Which generic floor sits behind
    it depends on whether there is an account**, at the owner's direction:

        no account   live + the evergreen bank
        account      live + the startup set

    It is a **swap and not a subtraction**, and that is the part worth
    holding on to. Taking the bank away on its own would have left an account
    holder on a deployment with no live provider - which is every deployment
    today - looking at a page with nothing on it, and `WORLD_FLOOR` reserves
    its four tiles on the stated premise that the personal rail has somewhere
    else to go. So the floor is replaced rather than removed, and it is
    replaced with the fresher of the two.

    The reasoning for each side:

    * The bank is a **first impression for somebody FAM knows nothing about
      and can keep nothing for** - downloaded the app, has not signed up.
      Twenty-eight hand-written subjects are the right answer to "show me
      what this is" and the wrong answer to "what should I hear today", and
      an account is the point where the second question becomes the one
      being asked.
    * The startup set is the same size of promise made about **now**. Every
      query in it asks what changed recently, so an account holder's generic
      tile is researched fresh on the tap rather than replayed from a script
      about nothing in particular. That is the freshness half of the same
      instruction, answered with a mechanism that already exists rather than
      by rewriting twenty-eight topics into something they were deliberately
      not.

    **This does not make the startup set warm inventory for a guest.**
    `startup.py` is explicit that the set exists for a listener who has said
    and done nothing, and `rank_startup` still leads with it on exactly that
    listener. A guest who has played something keeps the bank, which is the
    behaviour that shipped; what changed is only what replaces the bank once
    there is an account. Confining it that way is what keeps "the set is for
    somebody who said nothing, and only them" true where it was written.

    Ordering here is candidate order and never display order - every caller
    ranks what comes back. Live first, so a tie inside a ranker breaks
    towards the fresher inventory.

    One function because §119: a rule with four call sites and one of them
    reading something else is a rule that is not built. `rank_bank` (the
    DailyFAM mix picker) and `rank_might_like` (Explore New) deliberately do
    **not** read it - see their docstrings for why a menu somebody opened is
    not the app offering them something.

    **And the two crowd rows do not read it either, which is the sharper
    version of the same boundary: this gates what FAM *offers*, never what it
    *reports*.** `rank_most_played` and `rank_friends` are measurements over
    the play log - what everybody played, what your friends played - and if
    listeners really did play a bank topic then saying so is simply true. An
    account holder can therefore see a bank tile in "What FAM can't stop
    listening to", and should: hiding the most-played episode in the app
    because of who is looking would be §125's own over-claim in reverse, a row
    whose heading is a claim about this deployment quietly filtered per
    listener. The rails that *choose for you* - Made for you, What you missed,
    and the post-episode popup's own passes - are the ones where offering a
    standing explainer to somebody with an account is the thing this rule is
    about.
    """
    return list(live) + list(STARTUP_TOPICS if has_account else TOPIC_BANK)


#: Sections are FILLED in this order and DISPLAYED in SECTIONS order. The most
#: constrained sections choose first.
#:
#: `most_played` is last, and since §125 for a different reason than it used
#: to be. It was last because it could fall back to the whole bank and so
#: could not be starved; it no longer falls back to anything - it holds what
#: listeners actually played and is empty otherwise - so it is last because
#: it is the rail that loses least by choosing late. A tile it wanted and a
#: personal rail took is still a tile somebody is being offered, which is not
#: true of a rail whose heading claims relevance.
#:
#: `missed` is first because it is still the narrowest inventory on the page:
#: what this listener was shown in the last week and did not take, plus what
#: the rest of FAM played. It cannot starve anything, and letting
#: `from_history` choose ahead of it would take the *best* of the missed
#: tiles and leave the rail whose heading is about relevance holding the
#: leftovers.
#:
#: **Trending is deliberately not part of that first pass.** `rank_missed`'s
#: membership widened to the live pool as well (see its docstring), and a
#: story that has never been put in front of anybody is not one this listener
#: missed in any useful sense - it is one Made for you exists to offer them.
#: So `build_feed` calls this rail with `include_trending=False` here and
#: tops it up from what is left over afterwards, which makes trending the
#: last source for this row rather than the first.
#: `world_trending` is filled outside this loop and before it - see
#: `rank_world`, which since §134 takes its tiles before any of these choose
#: and takes nothing of the listener's into account but their country.
FILL_ORDER = ("missed", "from_history", "followers", "might_like", "most_played")
#: `might_like` stays in the fill order even though it is no longer displayed.
#: That is deliberate: it claims its picks before the generic sections do, so
#: the topics it would have shown are still held back from them - which keeps
#: `/api/explorenew` showing something other than the rest of the page, and
#: keeps putting the rail back a one-line change.

#: Display order: personal first, global last. Someone opening myFAM is more
#: likely to want what was chosen for them than what is popular, and the page
#: should not make them scroll past the crowd to reach it. (Fill order is
#: separate - see FILL_ORDER - because the constrained sections must still
#: choose their topics first.)
SECTIONS = (
    # Live stories the listener's own history argues for, mixed with the
    # generic floor. The one rail that is allowed both inventories, because
    # it is the one whose question is "what would *you* want", and the answer
    # to that is sometimes today's news and sometimes a standing explainer.
    # Which floor depends on whether there is an account - see
    # `browse_inventory`, which is where that decision lives.
    ("from_history", "Made for you"),
    # What the *world* is paying attention to. A different question from what
    # this app's listeners are playing, and from a different place: the live
    # story pool, refreshed once for everybody. It is second at the owner's
    # direction - it was last, where a row nobody scrolls to is a row nobody
    # reads, and it is the one rail on this page with a reason to be looked at
    # today rather than eventually.
    ("world_trending", "Trending"),
    # What this listener was offered in the last week and did not take. The
    # weekly recap's replacement, and deliberately a *shelf* rather than the
    # popup it replaces: the recap was one episode about somebody's week,
    # which meant a thin week produced an episode about having had a thin
    # week. This is episodes they can still have.
    #
    # Third, under Trending rather than over it. §102 moved the world row up
    # here on the argument that the one rail about *today* should not sit
    # under two rails about what somebody already likes, and this is a third
    # rail about what they already like - a week older.
    ("missed", "What you missed last week"),
    # The two crowd rows, in this order at the owner's direction. FAM's own
    # popularity is a real measurement over every listener; the friends row is
    # a real measurement over the handful somebody follows, and is empty until
    # they follow anybody - so the row that always has something in it goes
    # above the row that does not.
    ("most_played", "What FAM can't stop listening to"),
    # Renamed from "Your circle is on this", and the rename is a promise this
    # rail now keeps: it reads the follow graph rather than co-listener
    # overlap. The old heading, and the card copy under it, already said
    # "people you follow" about strangers who happened to play the same tile.
    ("followers", "What your friends are listening to"),
)

#: Ranked, reachable by API, and **not on myFAM** - removed from the page at
#: the owner's direction, with Trending taking its slot.
#:
#: This is the second time this rail has come off, so it is worth writing
#: down what that costs rather than just doing it: `rank_might_like` is the
#: only ranking in FAM that offers anything *outside* an established taste,
#: and without a shelf the page is three ways of being told what you already
#: like. The ranker, `build_explore_new` and `/api/explorenew` are all kept
#: and tested, so putting it back is a one-line change to SECTIONS rather
#: than a rebuild.
UNSHELVED = ("might_like",)

#: How much each kind of interaction says about taste. Finishing an episode is
#: the strongest signal there is; a skip is real evidence in the other
#: direction and must not be treated as a weak play.
#:
#: `pick` is somebody saying "this one" from the intro's topic catalogue,
#: before they have played anything. It is weighted *between* a search and a
#: completion: stronger than a search, because they chose it off a list rather
#: than typing a passing thought, and weaker than finishing an episode,
#: because they have not actually heard one yet. It is the only signal a
#: listener can give on their first run that has a whole topic behind it - and
#: therefore the topic's subtags, which the eight pickable facets cannot
#: express. It costs nothing and generates nothing: a row in the log.
#:
#: **The three endorsements** - `share`, `vibe` and `save` - are what somebody
#: does about an episode *after* hearing it, and until now not one of them
#: reached this table. `share` was the worst of the three, because the code
#: already believed it did: `app.py` has recorded a `share` event since the
#: messages feature shipped, `EVENT_KINDS` never accepted the kind, and every
#: one of those rows was dropped with a log line nobody was reading. A vibe
#: and a save were never written at all.
#:
#: They are weighted above a play and below a completion, and the reasoning is
#: the same one `pick` uses. Sending an episode to somebody is a statement
#: with a person's name on it - a stronger claim about taste than pressing
#: play, which is a claim about curiosity - and it is still weaker than
#: sitting through the whole thing, which is the only signal here that is
#: evidence the episode was any good.
#:
#: **`share` and `vibe` are worth the same**, deliberately. One is sent to a
#: person and the other posted to followers, and a rule that made either count
#: for more would need a number nobody has any way to tune - the same call
#: `social.circle_of` makes about a mutual follow. `save` is a little lower:
#: it is a statement about wanting more of this, made to nobody, and it is the
#: one of the three that can be pressed before the episode has said anything.
EVENT_WEIGHT = {"search": 1.0, "play": 1.0, "complete": 2.5, "skip": -1.5,
                "pick": 1.6, "share": 2.0, "vibe": 2.0, "save": 1.5}

#: The three above, as a set. Nothing in the ranker branches on it; it is here
#: so a report can ask "how many endorsements does this listener give" without
#: hard-coding the list a second time, and so a fourth one added later has one
#: obvious place to be added to.
ENDORSEMENTS = frozenset(("share", "vibe", "save"))

#: An **impression** is one tile put in front of one listener by one version of
#: the ranking. It is recorded so "why did we show this?" has an answer, and it
#: deliberately has no entry in EVENT_WEIGHT: being *shown* something says
#: nothing about whether you liked it, and giving it a weight would let the
#: feed teach itself its own taste.
IMPRESSION = "impression"

#: Kinds the log will accept. Taste weights live in EVENT_WEIGHT; this is the
#: wider set, because an impression is a real event that carries no weight.
EVENT_KINDS = frozenset(EVENT_WEIGHT) | {IMPRESSION}

#: Bump this whenever the ranking changes in a way that would make two feeds
#: incomparable - a new signal, a different weight, a changed section. Stamped
#: on every impression, so a later comparison of algorithms is a GROUP BY
#: rather than an archaeology project. Date-and-counter rather than a plain
#: integer, because the useful question is nearly always "what were we running
#: in September" and not "what was the sixth version".
ALGO_VERSION = "2026-09-23.3"

#: **Fatigue**: how a tile that keeps being shown and never played stops being
#: offered quite so hard. This is the one thing impressions are allowed to do
#: to the ranking, and the boundary is exact and load-bearing:
#:
#: * An impression must never become **taste**. "We showed you tech" is not
#:   "you like tech"; giving IMPRESSION a row in EVENT_WEIGHT would let the
#:   feed teach itself its own preferences and call the echo a signal. It has
#:   no weight there, and it must not acquire one.
#: * An impression may become **fatigue**. Shown on six separate occasions and
#:   never once played is real evidence about *that tile*, and it is the only
#:   negative the log holds for a topic nobody ever taps - a skip needs a play
#:   first, so without this a tile can be ignored forever and never learn it.
#:
#: The difference that makes it safe: fatigue is per-topic, never per-tag, and
#: it can only push a tile *down*. It cannot reinforce itself, because nothing
#: it does raises anything. The bubble the EVENT_WEIGHT note guards against
#: needs a positive feedback loop, and this is strictly negative.
#:
#: Applied to the personalised rankings only. `rank_most_played` is deliberately
#: identical for everyone - that is what makes it the cheapest section to
#: serve - and per-listener damping would quietly end that.
FATIGUE_WEIGHT = 0.35
#: Impressions inside one bucket count once. A feed load writes ~18 rows and a
#: listener who opens myFAM four times before breakfast has not rejected
#: anything four times; without this, refreshing the page would look like
#: disinterest and the ranking would punish the most engaged listeners hardest.
FATIGUE_BUCKET = 3600.0
#: Occasions before fatigue starts counting. A rail scrolls, so being *sent* a
#: tile twice is not evidence it was ever looked at.
FATIGUE_GRACE = 2
#: Fatigue never reaches zero. A tile buried early by a burst of impressions
#: must still be able to come back when the listener's taste moves toward it.
FATIGUE_FLOOR = 0.15

#: **Engagement**: how often a tile actually gets tapped when it is offered.
#:
#: The second of the two questions a feed has to answer. `_affinity` answers
#: *will this listener like it*; nothing has ever answered *will they tap it*,
#: except `fatigue`, which can only say no. The data to answer it has been
#: written since impressions shipped - every impression carries the listener,
#: the tile and the rail, every play carries the listener and the tile - and
#: until this constant existed nothing read it.
#:
#: **It is global, not per listener, and that is a decision rather than a
#: shortcut.** Per (listener, tile) there is almost nothing to measure: a
#: listener sees a given tile a handful of times ever. Per (listener, facet)
#: there is plenty, and it would be a second, noisier copy of `taste`, which
#: already reads their plays. What is left is a property of the *tile* - does
#: this title and this hook get tapped when people see them - and that is
#: dense, meaningful, and one computation for the whole deployment, which is
#: the same economics `rank_most_played` and the story pool run on.
#:
#: The danger, named so it stays a decision: **an engagement term optimised
#: alone converges on whatever is most clickable for everybody**, which is a
#: row this page already has and deliberately keeps separate. Four things
#: hold that line, and all four are load-bearing:
#:
#: * it is **bounded** (`ENGAGEMENT_FLOOR`..`ENGAGEMENT_CEILING`), so it can
#:   reorder comparable tiles and can never outvote affinity;
#: * it is **never applied to `rank_most_played`**, which is what everybody
#:   plays and must stay identical for everyone;
#: * a tile with no data scores **exactly 1.0**, so a new tile is not buried
#:   for being new - that is `ENGAGEMENT_PRIOR`'s whole job; and
#: * `ALGO_VERSION` moves when this changes, so the impression log can tell
#:   the two regimes apart and `tools/ctr_report.py --by algo` can say
#:   whether it helped. A ranking change that cannot be measured afterwards
#:   is a ranking change nobody can defend.
#:
#: It does not double-count against `fatigue`. Fatigue is "*you* were shown
#: this and passed"; this is "*nobody* taps this". Different evidence, and
#: the bounds stop them compounding into a tile that can never recover.
#:
#: **Which rails get it, and why the rest do not.** `rank_from_history` and
#: `rank_startup` - the two forms of "what should I hear", which is the
#: question this term sharpens. Not the others, and each for its own reason:
#:
#: * `rank_most_played` **must not have it**. That row is what everybody
#:   plays; adding a term for what everybody taps would make it that twice.
#: * `rank_missed` would be counting one event from two angles. Every tile on
#:   that rail is one this listener was already shown and did not take, so a
#:   global "people pass this over" term is the same passing-over measured
#:   again more widely.
#: * `rank_friends` ranks by what the people you follow played, not by
#:   affinity. A global tap rate has no business reordering a fact about
#:   named people.
#: * `rank_might_like` is off the page (`UNSHELVED`) and scores in tiers
#:   rather than on one number. Worth revisiting if it is ever shelved again;
#:   not worth the complexity to reorder a rail nobody is shown.
ENGAGEMENT_WEIGHT = 1.0

#: How many occasions a tile needs before its own rate outweighs the average.
#: Below it the estimate is pulled toward what the whole feed converts at, so
#: three shows and one tap is not a 33% tile. The same line
#: `tools/ctr_report.MIN_SHOWN` draws for a printed number, drawn here for a
#: score - except that here it shrinks rather than refuses, because a rank
#: has to produce something for every tile.
ENGAGEMENT_PRIOR = 20.0

#: The bounds. A tile that converts at twice the average is worth offering
#: sooner and is not worth offering *instead* of something this listener
#: actually wants; a tile nobody taps is worth offering later and is not
#: worth burying, because the reason it is not tapped may be that it has only
#: ever been shown at the bottom of a rail.
ENGAGEMENT_FLOOR = 0.6
ENGAGEMENT_CEILING = 1.5

#: How long the global engagement table is held in process before it is
#: recomputed. It is the same answer for every listener, so this is one query
#: per five minutes for the whole deployment rather than two per page load -
#: the same reasoning that makes `trending`'s cache global.
ENGAGEMENT_CACHE_SECONDS = 300.0

#: How long an impression is kept. Behavioural events are low-volume and worth
#: keeping indefinitely; impressions arrive ~18 at a time on every feed load,
#: so left alone they would be the whole table inside a week. A month is long
#: enough to compare two ALGO_VERSIONs against each other and short enough
#: that the file stays a file.
IMPRESSION_TTL = 30 * 86400


def _decay(age_seconds: float) -> float:
    return 0.5 ** (age_seconds / HALF_LIFE)


@dataclass
class Event:
    user_id: str
    kind: str
    topic_id: str = ""
    text: str = ""
    tags: tuple[str, ...] = ()
    at: float = field(default_factory=time.time)
    #: The follow-up predicted for this episode, carried on the event that finished
    #: it. Recorded here rather than joined back to the cache at read time,
    #: because the cache key depends on settings that may have moved on and a
    #: thread the listener was actually offered should not disappear.
    thread: str = ""
    #: Impressions only. Which shelf the tile appeared on, and the ranking
    #: version that put it there - the two halves of "why did we show this".
    #: Empty on every behavioural event, the way `thread` is empty on
    #: everything but a completion.
    section: str = ""
    algo: str = ""


class EventStore:
    """Append-only interaction log. SQLite for the same reasons as the cache:
    no new dependency, survives restarts, shared by every worker."""

    def __init__(self, path: str | None = None) -> None:
        # None means "the app's own database", resolved from the project
        # root rather than the cwd. See paths.py for why that matters.
        self.path = data_path("MYFAM_DB", "myfam.db", path)
        self._local = threading.local()
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS events (
                       id       INTEGER PRIMARY KEY AUTOINCREMENT,
                       user_id  TEXT NOT NULL,
                       kind     TEXT NOT NULL,
                       topic_id TEXT NOT NULL DEFAULT '',
                       text     TEXT NOT NULL DEFAULT '',
                       tags     TEXT NOT NULL DEFAULT '',
                       at       REAL NOT NULL
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS events_user ON events(user_id, at)")
            conn.execute("CREATE INDEX IF NOT EXISTS events_topic ON events(topic_id, at)")
            # Added after the table shipped, so an existing log is widened
            # rather than recreated - the same trade cache.py makes, except
            # that here the data is not regenerable and must not be dropped.
            for ddl in (
                "ALTER TABLE events ADD COLUMN thread TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE events ADD COLUMN section TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE events ADD COLUMN algo TEXT NOT NULL DEFAULT ''",
            ):
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass  # already there
            # The fitted ranking order (`learned_rank`, §131). One row, in
            # this database rather than a file of its own, because it is
            # *derived from* this log: it lives where the log lives, is on
            # the same disk, and goes when the log is cleared.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS learned_rank (
                       id    INTEGER PRIMARY KEY CHECK (id = 1),
                       model TEXT NOT NULL,
                       at    REAL NOT NULL
                   )"""
            )
        self._pruned_at = 0.0

    def learned_model(self) -> str:
        """The stored ranking model as JSON, or "". See `learned_rank`."""
        try:
            row = self._conn().execute(
                "SELECT model FROM learned_rank WHERE id = 1").fetchone()
        except Exception:
            log.exception("could not read the ranking model")
            return ""
        return row[0] if row else ""

    def save_learned_model(self, model_json: str, at: Optional[float] = None) -> None:
        """Replace the stored ranking model. Only `tools/learn_rank.py` calls
        this; nothing on a request path writes a model."""
        self._conn().execute(
            "INSERT INTO learned_rank (id, model, at) VALUES (1, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET model = excluded.model, at = excluded.at",
            (model_json, time.time() if at is None else at))
        learned_rank.reset()

    def clear_learned_model(self) -> None:
        """Remove the stored ranking model; the hand-tuned order resumes."""
        self._conn().execute("DELETE FROM learned_rank")
        learned_rank.reset()

    def algo_stamp(self) -> str:
        """`ALGO_VERSION`, plus which of the conditional §131 terms were in
        force: `+sem` when a semantic model is ranking, `+lr<trained_at>`
        when a fitted order is. Both switch on with no code change - an
        install, a training run - so the version constant alone could not
        tell the regimes apart in the impression log, which is what it is
        for."""
        stamp = ALGO_VERSION
        try:
            if taste_vectors.enabled():
                stamp += "+sem"
            model = learned_rank.active(self)
            if model is not None:
                stamp += "+" + model.stamp()
        except Exception:  # noqa: BLE001 - a stamp is never worth a page
            log.exception("could not compute the algorithm stamp")
        return stamp

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def record(self, event: Event) -> None:
        if event.kind not in EVENT_KINDS:
            log.warning("ignoring unknown event kind %r", event.kind)
            return
        try:
            self._conn().execute(
                "INSERT INTO events"
                " (user_id, kind, topic_id, text, tags, at, thread, section, algo)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event.user_id[:64], event.kind, event.topic_id[:64], event.text[:300],
                 ",".join(event.tags), event.at, event.thread[:200],
                 event.section[:40], event.algo[:40]),
            )
        except Exception:
            # A feed is a nicety. Losing an event must never break playback.
            log.exception("could not record interaction; continuing")

    def record_impressions(
        self,
        user_id: str,
        shown: Iterable[tuple[str, str]],
        algo: Optional[str] = None,
        at: Optional[float] = None,
    ) -> int:
        """Log the tiles one feed actually put in front of one listener.

        `shown` is (section_key, topic_id) in display order. Written in a
        single statement because a feed is ~18 rows and eighteen round trips
        on a page load is how a read surface acquires a write bottleneck.

        Separate from `record` on purpose: this is the only kind that arrives
        in bulk, needs pruning, and must stay out of the ranking read path.
        """
        if not user_id:
            return 0
        now = time.time() if at is None else at
        algo = self.algo_stamp() if algo is None else algo
        rows = [
            (user_id[:64], IMPRESSION, topic_id[:64], "",
             ",".join(tags_for_id(topic_id)),
             now, "", str(section)[:40], str(algo)[:40])
            for section, topic_id in shown
        ]
        if not rows:
            return 0
        try:
            self._conn().executemany(
                "INSERT INTO events"
                " (user_id, kind, topic_id, text, tags, at, thread, section, algo)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        except Exception:
            # Same rule as every other write here: the feed is already built
            # and on its way to the listener. Losing the audit trail for it is
            # not a reason to fail the page.
            log.exception("could not record impressions; continuing")
            return 0
        self._maybe_prune(now)
        return len(rows)

    def _maybe_prune(self, now: float) -> None:
        """Drop impressions past IMPRESSION_TTL, at most hourly.

        Checked here rather than on a timer because this is the only writer
        that grows the table quickly, so it is the only one that has to care.
        """
        if now - self._pruned_at < 3600:
            return
        self._pruned_at = now
        try:
            self._conn().execute(
                "DELETE FROM events WHERE kind = ? AND at < ?",
                (IMPRESSION, now - IMPRESSION_TTL),
            )
        except Exception:
            log.exception("could not prune impressions; continuing")

    def impressions_for(self, user_id: str, limit: int = 200) -> list[Event]:
        """What this listener was shown, newest first. Audit, never ranking."""
        try:
            rows = self._conn().execute(
                "SELECT topic_id, tags, at, section, algo FROM events"
                " WHERE user_id = ? AND kind = ? ORDER BY at DESC LIMIT ?",
                (user_id, IMPRESSION, int(limit)),
            ).fetchall()
        except Exception:
            log.exception("could not read impressions")
            return []
        return [
            Event(user_id, IMPRESSION, r[0], "",
                  tuple(t for t in r[1].split(",") if t), r[2],
                  section=r[3], algo=r[4])
            for r in rows
        ]

    def impressions_since(self, user_id: str, since: float) -> dict[str, float]:
        """Which tiles were put in front of this listener since `since`, and
        when they last were.

        The read behind "What you missed last week". Deliberately *not* a
        ranking input the way `impression_occasions` is - this answers "was
        this offered", which is a fact about the feed, and the rail then
        removes everything they played. An impression still never becomes
        taste: the ordering below it is `_affinity`, the same profile every
        other personal rail scores against.
        """
        if not user_id:
            return {}
        try:
            rows = self._conn().execute(
                "SELECT topic_id, MAX(at) FROM events"
                " WHERE user_id = ? AND kind = ? AND topic_id != '' AND at >= ?"
                " GROUP BY topic_id",
                (user_id, IMPRESSION, float(since)),
            ).fetchall()
        except Exception:
            # One rail short is a far better outcome than no feed, which is
            # the rule every other read in this class keeps.
            log.exception("could not read recent impressions")
            return {}
        return {r[0]: float(r[1]) for r in rows}

    def impression_occasions(self, user_id: str) -> dict[str, int]:
        """How many separate occasions each tile was put in front of them.

        Distinct `FATIGUE_BUCKET` buckets rather than rows, because one feed
        load writes a row per tile and a refresh writes them all again. The
        question worth answering is "how many times did this come up and get
        passed over", and that is a count of occasions, not of renders.

        Unlike `impressions_for`, this one *is* read by the ranking - see the
        FATIGUE_WEIGHT note for exactly how far that is allowed to go.
        """
        if not user_id:
            return {}
        try:
            rows = self._conn().execute(
                "SELECT topic_id, COUNT(DISTINCT CAST(at / ? AS INTEGER))"
                " FROM events WHERE user_id = ? AND kind = ? AND topic_id != ''"
                " GROUP BY topic_id",
                (FATIGUE_BUCKET, user_id, IMPRESSION),
            ).fetchall()
        except Exception:
            # Same rule as every other read here: a feed with no fatigue
            # applied is the feed that shipped before this existed, which is
            # a far better outcome than no feed at all.
            log.exception("could not read impression occasions")
            return {}
        return {r[0]: int(r[1]) for r in rows}

    def for_user(self, user_id: str, limit: int = 400) -> list[Event]:
        """The behavioural log for one listener. **Impressions are excluded.**

        Not a filter for tidiness. This is the read that feeds `taste`, and it
        is capped at `limit` rows: a feed load writes ~18 impressions, so
        letting them in here means roughly twenty visits to myFAM push every
        play, completion and search out of the window - the taste model would
        be trained on what the feed showed rather than on what the listener
        did, and would then rank on it. Impressions are read by
        `impressions_for`, which nothing in the ranking path calls.
        """
        try:
            rows = self._conn().execute(
                "SELECT kind, topic_id, text, tags, at, thread FROM events"
                " WHERE user_id = ? AND kind != ? ORDER BY at DESC LIMIT ?",
                (user_id, IMPRESSION, limit),
            ).fetchall()
        except Exception:
            log.exception("could not read interactions")
            return []
        return [
            Event(user_id, r[0], r[1], r[2], tuple(t for t in r[3].split(",") if t),
                  r[4], r[5] or "")
            for r in rows
        ]

    def open_threads(self, user_id: str, limit: int = 8) -> list[dict]:
        """Threads from episodes this listener finished, newest first.

        Each finished episode carries the model's guess at what its listener
        would most likely ask next. This is where those land: an episode they
        have already heard, and the obvious question after it, one tap from
        being answered.

        A thread they have since asked about is dropped - it is no longer
        open - which is why this reads the log rather than a stored list.
        """
        events = self.for_user(user_id, limit=200)
        asked = " ".join(e.text.lower() for e in events if e.kind == "search")
        seen: set[str] = set()
        out: list[dict] = []
        for event in events:
            thread = (event.thread or "").strip()
            if not thread or event.kind != "complete":
                continue
            key = thread.lower()
            if key in seen or key in asked:
                continue
            seen.add(key)
            out.append({
                "thread": thread,
                "title": thread[:1].upper() + thread[1:],
                "from_title": (BANK_BY_ID[event.topic_id].title
                               if event.topic_id in BANK_BY_ID else event.text),
                "at": event.at,
            })
            if len(out) >= limit:
                break
        return out

    def heard(self, user_id: str, limit: int = 5000) -> list[tuple[str, str, float]]:
        """(topic_id, question, at) for everything this listener has played.

        Separate from `for_user` on purpose: that read is capped at the last
        400 behavioural events because it feeds `taste`, and a play that has
        scrolled out of a taste window is still an episode they have heard.
        "No repeats" is a statement about their whole history, so it gets a
        read of its own. Every surface counts - a search, an Explore replay,
        a shared link - which is why the question is returned beside the id.
        """
        try:
            rows = self._conn().execute(
                "SELECT topic_id, text, at FROM events"
                " WHERE user_id = ? AND kind IN ('play', 'complete')"
                " ORDER BY at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        except Exception:
            log.exception("could not read listening history")
            return []
        return [(r[0] or "", r[1] or "", float(r[2])) for r in rows]

    def plays_since(self, since: float) -> list[tuple[str, str]]:
        """(user_id, topic_id) for every bank topic played in the window."""
        try:
            rows = self._conn().execute(
                "SELECT user_id, topic_id FROM events"
                " WHERE at >= ? AND topic_id != '' AND kind IN ('play', 'complete')",
                (since,),
            ).fetchall()
        except Exception:
            log.exception("could not read plays")
            return []
        return [(r[0], r[1]) for r in rows]

    def impression_outcomes(self, since: float = 0.0,
                            now: Optional[float] = None) -> list[dict]:
        """Every tile put in front of somebody in the window, and whether they
        took it. The impression-to-play join.

        **This data has been written since impressions shipped and nothing has
        ever read it this way.** The ranking asks "will they like it"
        (`_affinity`) and, negatively, "have they ignored it" (`fatigue`);
        neither is a measurement of whether a tile actually gets tapped. This
        is that measurement, and it is a plain query over rows that already
        exist rather than anything new to collect.

        One row per **occasion** - a distinct `FATIGUE_BUCKET` hour in which
        the tile was in front of them - never one per impression row. A rail
        scrolls and a page gets refreshed, and counting renders would report
        the most-reloaded feed as the least effective one. This is the same
        unit `impression_occasions` counts, deliberately: the ranking's
        negative signal and its positive one should not disagree about what
        "shown" means.

        And it is *not* one row per (listener, tile) either. A tile offered on
        nine separate days and taken on the tenth is nine offers that were
        passed over and one that was not - collapsing them would score it as a
        tile that works, when what it actually did was wear somebody down.

        Attribution is **last touch before the play**. A tile can be on two
        rails of one page and in several days of feeds, so the credit goes to
        the impression that was most recently in front of them when they
        pressed it. Without a play, the last impression in the window carries
        the miss. Sections and algorithm versions therefore partition the
        rows, which is what makes "which rail converts" answerable rather than
        a sum that double-counts.

        A play *before* any impression is not counted at all: they found the
        episode somewhere else, and crediting a rail for it would flatter
        whichever rail happened to show it afterwards.
        """
        now = time.time() if now is None else now
        try:
            shown = self._conn().execute(
                "SELECT user_id, topic_id, section, algo, at FROM events"
                " WHERE kind = ? AND topic_id != '' AND at >= ?"
                " ORDER BY at",
                (IMPRESSION, float(since)),
            ).fetchall()
            played = self._conn().execute(
                "SELECT user_id, topic_id, MIN(at) FROM events"
                " WHERE kind IN ('play', 'complete') AND topic_id != ''"
                " AND at >= ? GROUP BY user_id, topic_id",
                (float(since),),
            ).fetchall()
        except Exception:
            # Same rule as every other read in this class. A report that
            # cannot run is a report; a feed that cannot load is an outage.
            log.exception("could not read impression outcomes")
            return []

        first_play = {(r[0], r[1]): float(r[2]) for r in played}
        # (user, topic, hour) -> that occasion. Rows arrive in time order, so
        # a second render inside the same hour simply overwrites the first,
        # which is what makes this a count of occasions.
        occasions: dict[tuple[str, str, int], dict] = {}
        for user_id, topic_id, section, algo, at in shown:
            at = float(at)
            play_at = first_play.get((user_id, topic_id))
            if play_at is not None and at > play_at:
                # Shown again after they had already played it. That is the
                # feed repeating itself, not an offer they took.
                continue
            occasions[(user_id, topic_id, int(at / FATIGUE_BUCKET))] = {
                "user_id": user_id,
                "topic_id": topic_id,
                "section": section,
                "algo": algo,
                "at": at,
                "taken": False,
                "lag": None,
            }

        # Last touch. Of the occasions before the play, exactly one gets the
        # credit: the most recent. Crediting all of them would make a tile
        # that had to be offered nine times look nine times as effective as
        # one taken the first time it appeared.
        best: dict[tuple[str, str], dict] = {}
        for row in occasions.values():
            pair = (row["user_id"], row["topic_id"])
            if pair not in first_play:
                continue
            if pair not in best or row["at"] > best[pair]["at"]:
                best[pair] = row
        for pair, row in best.items():
            row["taken"] = True
            row["lag"] = first_play[pair] - row["at"]

        return sorted(occasions.values(), key=lambda r: r["at"])

    def engagement_totals(self, since: float = 0.0) -> tuple[dict, int, int]:
        """`{topic_id: (offered, taken)}`, plus the same two totalled.

        The *aggregate* of `impression_outcomes`, done in SQL rather than by
        loading the rows and counting them in Python. `impression_outcomes`
        is a report and may read a month of impressions into memory; this one
        is on the feed's read path and may not.

        An **occasion** is one (listener, tile, `FATIGUE_BUCKET` hour), the
        same unit fatigue counts, so the positive and negative signals cannot
        disagree about what "shown" means. A tile is **taken** by a listener
        who played it having been shown it first - the `EXISTS` is what makes
        that "first", and without it an episode somebody found by searching
        would be scored as a tile that converts.

        Global, and that is the point: one answer serves every listener, so
        this is a query per cache window for the whole deployment rather than
        one per page load. See `ENGAGEMENT_WEIGHT` for why the per-listener
        version is not worth having.
        """
        try:
            offers = self._conn().execute(
                "SELECT topic_id,"
                " COUNT(DISTINCT user_id || ':' || CAST(at / ? AS INTEGER))"
                " FROM events WHERE kind = ? AND topic_id != '' AND at >= ?"
                " GROUP BY topic_id",
                (FATIGUE_BUCKET, IMPRESSION, float(since)),
            ).fetchall()
            taken = self._conn().execute(
                "SELECT p.topic_id, COUNT(DISTINCT p.user_id) FROM events p"
                " WHERE p.kind IN ('play', 'complete') AND p.topic_id != ''"
                " AND p.at >= ? AND EXISTS ("
                "   SELECT 1 FROM events i WHERE i.kind = ?"
                "   AND i.user_id = p.user_id AND i.topic_id = p.topic_id"
                "   AND i.at <= p.at AND i.at >= ?)"
                " GROUP BY p.topic_id",
                (float(since), IMPRESSION, float(since)),
            ).fetchall()
        except Exception:
            # The rule every read in this class keeps. A feed with no
            # engagement term is the feed that shipped before this existed,
            # which is a far better outcome than no feed.
            log.exception("could not read engagement totals")
            return {}, 0, 0

        taken_by = {r[0]: int(r[1]) for r in taken}
        out = {r[0]: (int(r[1]), min(int(r[1]), taken_by.get(r[0], 0)))
               for r in offers}
        return (out,
                sum(o for o, _t in out.values()),
                sum(t for _o, t in out.values()))

    def subject_texts(self, since: float = 0.0,
                      limit: int = 20000) -> list[tuple[str, str]]:
        """`(listener, text)` for everything anybody said in the window.

        What the category sweep reads. Behavioural kinds only - an impression
        carries no text and would contribute nothing, and including it would
        let *the feed's own tiles* mint the vocabulary the feed is ranked in,
        which is the impression rule arriving through a different door.

        Bounded, because this runs in a background sweep on a table that grows
        forever and a query with no ceiling is one that is fine for a year.
        """
        try:
            rows = self._conn().execute(
                "SELECT user_id, text FROM events"
                " WHERE kind != ? AND text != '' AND at >= ?"
                " ORDER BY at DESC LIMIT ?",
                (IMPRESSION, float(since), int(limit)),
            ).fetchall()
        except Exception:
            log.exception("could not read subject texts")
            return []
        return [(r[0], r[1]) for r in rows]

    def users_who_played(self, topic_ids: Iterable[str]) -> dict[str, set[str]]:
        """topic_id -> the users who played it. The co-listener join."""
        ids = list(topic_ids)
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        try:
            rows = self._conn().execute(
                f"SELECT topic_id, user_id FROM events WHERE topic_id IN ({marks})"
                " AND kind IN ('play', 'complete')",
                ids,
            ).fetchall()
        except Exception:
            log.exception("could not read co-listeners")
            return {}
        out: dict[str, set[str]] = {}
        for topic_id, user_id in rows:
            out.setdefault(topic_id, set()).add(user_id)
        return out

    def count(self) -> int:
        """How many events the log holds. For a report, never for ranking."""
        try:
            return self._conn().execute(
                "SELECT COUNT(*) FROM events").fetchone()[0]
        except Exception:
            log.exception("could not count events")
            return 0

    def clear(self) -> int:
        """Empty the log. Every listener, every kind.

        Not part of account deletion and not reachable from any listener path
        - `forget` is the per-listener one and is what deletion uses. This is
        the blank slate for measuring the recommender: with seeded plays in
        the log, "is myFAM recommending the right things" has an unknown
        share of its answer coming from listeners who do not exist. See
        `tools/wipe_demo_data.py`.

        Everything here is behaviour rather than anything a listener made, so
        nothing they could point at is lost - but the taste model does start
        from nothing, and that is a real cost rather than a formality.
        """
        try:
            cur = self._conn().execute("DELETE FROM events")
            removed = cur.rowcount or 0
        except Exception:
            log.exception("could not clear the event log")
            return 0
        # The fitted order goes with the log it was fitted to (§124's rule:
        # a wipe takes what is derived from what it empties).
        try:
            self.clear_learned_model()
        except Exception:
            log.exception("could not clear the ranking model")
        return removed

    def forget(self, user_id: str) -> int:
        """Erase everything this store holds for one listener.

        Part of account deletion, which App Store guideline 5.1.1(v) requires
        of any app that creates accounts. Each store implements its own rather
        than a central deleter reaching into six databases by table name: that
        deleter silently stops covering the seventh, and the failure is
        invisible until somebody audits it.

        Returns rows removed, so the endpoint can report what it did rather
        than that it tried.
        """
        removed = 0
        for table in ('events',):
            try:
                cur = self._conn().execute(
                    f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
                removed += cur.rowcount or 0
            except Exception:
                log.exception("could not erase %s for %r", table, user_id)
        return removed


def taste(events: Iterable[Event], now: Optional[float] = None,
          interests: Iterable[str] = ()) -> dict[str, float]:
    """Tag affinity for one listener: recency-weighted, signed, normalised.

    Computed on read rather than stored. A stored profile is a cache that can
    disagree with the log it came from; this cannot.

    `interests` are the facets they picked in the intro. They enter as a flat
    INTEREST_WEIGHT before normalisation - a starting position, not a rule -
    so a listener who has never played anything still gets a ranked feed, and
    one who has gets ranked mostly on what they did. A skip against a chosen
    interest can take it negative, which is correct: choosing "Sport" in an
    intro is a weaker statement than abandoning three sports episodes.
    """
    now = time.time() if now is None else now
    scores: dict[str, float] = {tag: INTEREST_WEIGHT for tag in interests
                                if tag in TAG_LABELS}
    tree = category_tree()
    for event in events:
        weight = EVENT_WEIGHT.get(event.kind, 0.0) * _decay(max(0.0, now - event.at))
        tags = set(event.tags)
        if event.text:
            # **The stored tags are kept and the text is re-read.** A node
            # minted last week did not exist when this row was written, so its
            # `tags` column cannot mention it - and a vocabulary that only
            # applied to events written after it appeared would take a month
            # to be worth anything to anybody who was already here.
            #
            # Re-matching rather than rewriting: the log stays append-only and
            # the stored tags stay exactly what the ranker believed at the
            # time, which is what makes an old feed reproducible. What is
            # re-read is the listener's own words, which have not changed.
            tags |= set(tree.match(event.text) if tags
                        else tags_for_text(event.text))
        for tag in tags:
            scores[tag] = scores.get(tag, 0.0) + weight
    peak = max((abs(v) for v in scores.values()), default=0.0)
    return {k: v / peak for k, v in scores.items()} if peak else {}


def fatigue(occasions: dict[str, int], played: Iterable[str] = ()) -> dict[str, float]:
    """Per-topic multiplier in (FATIGUE_FLOOR, 1.0]. 1.0 means "no evidence".

    A topic they actually played is dropped rather than damped: it is excluded
    from every ranking anyway, and the impressions that led up to the play are
    the opposite of disinterest.
    """
    played = set(played)
    out: dict[str, float] = {}
    for topic_id, seen in occasions.items():
        if topic_id in played:
            continue
        ignored = seen - FATIGUE_GRACE
        if ignored <= 0:
            continue
        out[topic_id] = max(FATIGUE_FLOOR, 1.0 / (1.0 + FATIGUE_WEIGHT * ignored))
    return out


#: The global engagement table, held per database path.
#:
#: Keyed on the path rather than kept as one value, because a test builds a
#: store per temporary directory and a single module-level number would let
#: one test's feed be ranked by another's log. The same reason
#: `stories.reset()` exists.
_ENGAGEMENT_CACHE: dict[str, tuple[float, dict[str, float]]] = {}


def reset_engagement() -> None:
    """Forget the cached table. For tests and for a store that was rebuilt."""
    _ENGAGEMENT_CACHE.clear()


def engagement(totals: dict, offered: int, taken: int) -> dict[str, float]:
    """`{topic_id: multiplier}` in [ENGAGEMENT_FLOOR, ENGAGEMENT_CEILING].

    A tile's own conversion rate, shrunk toward what the whole feed converts
    at, divided by that same overall rate. Three properties fall out of that
    shape and each of them is the reason for it:

    * **No data is exactly 1.0, and thin data is nearly so.** A tile nobody
      has been shown is absent from `totals`, and one with a handful of
      occasions is pulled most of the way back to the average by
      `ENGAGEMENT_PRIOR` - two shows and no tap costs about a tenth, where a
      raw rate would have said zero and buried it. It takes roughly
      `ENGAGEMENT_PRIOR` occasions with nothing taken to reach the floor,
      which is a real measurement rather than a slow start.
    * **It is a ratio, so it is comparable across deployments.** A feed where
      everything converts at 4% and one where everything converts at 30% both
      produce multipliers around 1.0; what moves a tile is being better or
      worse than its own feed, not an absolute number somebody has to tune.
    * **It is bounded**, so it reorders comparable tiles and can never outvote
      what the listener actually wants. See `ENGAGEMENT_WEIGHT`.

    Returns `{}` when there is nothing to measure - not a table of 1.0s -
    because an empty dict is what every caller already treats as "no
    evidence", and a full one would hide the difference between a feed with
    no impressions and a feed where everything is average.
    """
    if not totals or offered <= 0 or taken <= 0:
        return {}
    overall = taken / offered
    out: dict[str, float] = {}
    for topic_id, (shown, took) in totals.items():
        rate = ((took + ENGAGEMENT_PRIOR * overall)
                / (shown + ENGAGEMENT_PRIOR))
        lift = 1.0 + ENGAGEMENT_WEIGHT * (rate / overall - 1.0)
        out[topic_id] = max(ENGAGEMENT_FLOOR, min(ENGAGEMENT_CEILING, lift))
    return out


def engagement_for(store: EventStore, now: Optional[float] = None
                   ) -> dict[str, float]:
    """The engagement table for this database, cached for the whole
    deployment.

    One answer serves every listener - that is what makes this affordable on
    a browse path - so it is computed at most once per
    `ENGAGEMENT_CACHE_SECONDS` rather than once per page. The same shape the
    story pool and the trending cache use, and for the same reason.
    """
    now = time.time() if now is None else now
    # Every failure here is an empty table, which is the feed that shipped
    # before this term existed. `store.path` is the one that caught this: a
    # store too broken to have opened a file is exactly the case
    # `test_a_broken_event_store_never_breaks_the_feed` exists for, and
    # reading an attribute off it is a way for a ranking nicety to take the
    # browse page down.
    try:
        key = store.path
    except Exception:
        return {}
    cached = _ENGAGEMENT_CACHE.get(key)
    if cached and now - cached[0] < ENGAGEMENT_CACHE_SECONDS:
        return cached[1]
    try:
        table = engagement(*store.engagement_totals(now - IMPRESSION_TTL))
    except Exception:
        log.exception("could not build the engagement table")
        table = {}
    _ENGAGEMENT_CACHE[key] = (now, table)
    return table


def tag_weight(tag: str) -> float:
    """How specific a claim about an episode this one tag is.

    Three vocabularies meet here and they are ordered by how much they
    actually say:

    * a **facet** (`sports`) is 1.0 - it names a heading;
    * a **subtag** (`sports-drama`) is `SUBTAG_WEIGHT` - a hand-written
      corner of a heading; and
    * a **category** is `CATEGORY_DEPTH_WEIGHT ** depth`, which keeps going:
      `cincinnati bengals` under `nfl` under `american football` under
      `sports` is four levels, and the leaf is worth several times the root
      because it is several times more specific.

    The hand-written half is checked first and costs a dict lookup, so the
    tree is only consulted for a tag that is not a facet or a subtag - which
    keeps the common case exactly as fast as it was before the tree existed.
    """
    if tag in TAG_PARENT:
        return SUBTAG_WEIGHT
    if tag in TAG_LABELS:
        return 1.0
    # Past here the tag is a category or nothing at all, which is exactly the
    # case `_is_specific` answers True for - the two stay in step because the
    # order of these checks is the same in both.
    depth = category_tree().depth_of(tag)
    if depth <= 0 and category_tree().get(tag) is None:
        # Not in any vocabulary. A tag written into the log months ago by a
        # node that has since been pruned, most likely. Counted as a facet
        # rather than dropped: it was a real thing somebody was interested in
        # and the weight is the only thing that is uncertain.
        return 1.0
    return CATEGORY_DEPTH_WEIGHT ** max(1, depth)


#: What the tree recognises in a tile's question, keyed on that question.
#:
#: Bounded by the number of distinct tile queries a process sees - the bank
#: and the startup set are fixed, and the live pool is capped - and dropped
#: wholesale whenever the tree is rebuilt, so a node minted by the sweep is
#: visible on the next page rather than at the next restart.
#:
#: It exists because of §122 rather than out of caution: a word-set
#: intersection is cheap and `_affinity` is called per tile per rail per
#: page, and the last thing this file did on that path without measuring it
#: cost 134ms. `MAX_TAG_MEMO` is the blast radius if a caller ever starts
#: handing this unique strings.
_TAG_MEMO: dict[str, tuple[str, ...]] = {}
_TAG_MEMO_GEN = -1.0
MAX_TAG_MEMO = 2000


def topic_tags(topic: Topic) -> tuple[str, ...]:
    """The tile's declared tags, plus whatever the grown vocabulary recognises
    in its question.

    **This is the join that was missing, and without it the tree could not
    reach a browse page at all.** `categories.py` reads what listeners search
    for and builds a vocabulary deep enough to tell college football from the
    NFL; `taste` puts those words into a listener's profile. But a tile's
    `tags` are a hand-written tuple compiled into `TOPIC_BANK`, so the scorer
    was comparing a profile that could say `college football` against tiles
    that could only say `sports` - and a listener whose whole history was
    college football scored the bank's college-football tile *below* its golf
    tile, because neither could say anything the other could not.

    So a tile is scored as though somebody had hand-written onto it every tag
    the tree finds in its own question. That is deliberately the *same*
    treatment a subtag already gets rather than a new mechanism beside it:
    `sports-business` is in the NIL tile's tuple and in its denominator, and
    a category node behaves identically. A tile the tree says nothing about
    is returned exactly as it was, so a deployment with an empty tree ranks
    precisely as it did before any of this existed.

    It reads the *query* and never the title or the hook. The query is the
    thing that gets generated and is the only field that is reliably a
    statement of subject - a title is a label, and `<<TITLE:>>` means it may
    not even be the one the episode ends up with.
    """
    global _TAG_MEMO, _TAG_MEMO_GEN
    if not topic.query:
        return topic.tags
    tree = category_tree()
    # Read the generation once. The sweep rebuilds the tree on another
    # thread, and taking it again below would risk filing an answer computed
    # against one tree under the key of another.
    gen = getattr(tree, "_loaded_at", 0.0)
    if gen != _TAG_MEMO_GEN:
        _TAG_MEMO = {}
        _TAG_MEMO_GEN = gen
    # **What is memoised is the tree's half only, and that is not an
    # optimisation detail.** `tree.match(query)` is a pure function of the
    # query and the tree; the combined answer is not - it also depends on
    # this tile's declared tuple. Caching the combined answer under the query
    # alone would mean two tiles that happen to share a question get each
    # other's declared tags, which is silent, wrong, and exactly the kind of
    # thing that would survive a long time. Nothing in the bank shares a
    # query (a test says so), but a live story and a bank topic are minted by
    # different code and nothing makes that true across inventories.
    found = _TAG_MEMO.get(topic.query)
    if found is None:
        try:
            found = tree.match(topic.query)
        except Exception:  # noqa: BLE001 - a vocabulary never takes the page away
            log.exception("could not read the category tree for %r", topic.id)
            return topic.tags
        if len(_TAG_MEMO) < MAX_TAG_MEMO:
            _TAG_MEMO[topic.query] = found
    extra = set(found) - set(topic.tags)
    # Returned unchanged when the tree has nothing to add, rather than sorted
    # into the same set. The guarantee worth being able to state is the
    # strong one - a deployment with no tree gets back the identical tuple -
    # and a caller that ever cares about declaration order is then not
    # quietly broken by a vocabulary it has nothing to do with.
    return tuple(sorted(set(topic.tags) | extra)) if extra else topic.tags


def reset_topic_tags() -> None:
    """Drop the memo. For tests, and after a tree is cleared under us."""
    global _TAG_MEMO, _TAG_MEMO_GEN
    _TAG_MEMO = {}
    _TAG_MEMO_GEN = -1.0


def _affinity(topic: Topic, profile: dict[str, float]) -> float:
    """How well one tile matches one listener, with specificity counted.

    A weighted sum over the tile's tags, damped by how many it carries so a
    tile that covers more ground does not outrank a sharper one by breadth
    alone. `SUBTAG_WEIGHT` is what makes it *specific*: matching `chips`
    counts for more than matching `tech`, because the first is evidence about
    this episode and the second is evidence about a whole heading.

    The denominator stays `sqrt(len(tags))` rather than the sum of weights.
    Dividing by the weights would cancel the boost exactly - a tile made
    entirely of subtags would score the same as one made entirely of facets -
    which is the opposite of the point.

    **The tags are `topic_tags(topic)` and not `topic.tags`** - the declared
    tuple plus whatever the grown vocabulary recognises in the question. See
    that function for why the two were not the same thing and what it cost.
    Nothing else here changes: a tag the tree contributed is weighted,
    counted and divided by exactly as if it had been typed into the tile.
    """
    tags = topic_tags(topic)
    if not tags:
        return 0.0
    total = sum(profile.get(tag, 0.0) * tag_weight(tag) for tag in tags)
    return total / math.sqrt(len(tags))


def _played_ids(events: Iterable[Event]) -> set[str]:
    return {e.topic_id for e in events if e.topic_id and e.kind in ("play", "complete")}


#: The soonest a heard episode whose answer moves may be offered again. A
#: day, because "what changed in the NFL this week" re-asked the same
#: afternoon is the same episode with a new timestamp.
REMAKE_AFTER = 86400.0

#: A script written this soon after the listener's own play is the one they
#: heard. Their tap is what wrote it, and the write lands at the end of the
#: generation - after the play was recorded - so "written after they heard
#: it" needs a margin or every episode anybody generated would look remade.
REMAKE_MARGIN = 3600.0


def _norm_question(text: str) -> str:
    return " ".join(_WORD.findall((text or "").lower()))


@dataclass(frozen=True)
class Heard:
    """When this listener last heard each episode, by tile and by question.

    Both, because an episode is a question and a length rather than a tile:
    somebody who typed the bank's own question into search, or replayed it on
    Explore, has heard the tile's episode without ever tapping the tile.
    """
    by_id: dict
    by_question: dict

    def last(self, topic: "Topic") -> float:
        return max(self.by_id.get(topic.id, 0.0),
                   self.by_question.get(_norm_question(topic.query), 0.0))


def heard_from(rows: Iterable[tuple[str, str, float]]) -> Heard:
    by_id: dict[str, float] = {}
    by_question: dict[str, float] = {}
    for topic_id, text, at in rows:
        if topic_id:
            by_id[topic_id] = max(by_id.get(topic_id, 0.0), at)
        question = _norm_question(text)
        if question:
            by_question[question] = max(by_question.get(question, 0.0), at)
    return Heard(by_id, by_question)


def answer_moves(topic: "Topic") -> bool:
    """Whether writing this *same tile* again would say something new.

    A startup question is about now by construction - "what changed in the
    NFL this week" - and is researched on the tap against a recency window,
    so a fresh script of it is fresh information under the same title. A bank
    topic is evergreen: its second script says what its first one did in
    different words, which is a repeat.

    **A live story is not on this list, and that is deliberate.** Its title
    names one thing that happened, so the same tile offered again reads as a
    repeat whatever the script says. A heard story is handled by
    `trending_for` instead: a follow-up with a question and a card of its
    own, or nothing.
    """
    return topic.id in STARTUP_BY_ID or topic.id == LOCAL_STARTUP.id


def is_repeat(topic: "Topic", heard: Heard, now: float,
              written_at=None) -> bool:
    """Would offering this tile hand the listener an episode they have heard?

    Not heard: no. Heard, and the answer is evergreen: yes, always - the
    recommendation changes rather than the episode being made again. Heard,
    and the answer moves: it may come back **only as a different episode** -
    at least `REMAKE_AFTER` later, and either with no live script (the tap
    researches and writes a new one) or with a script written since they
    heard it (somebody else's tap already remade it, so it is new *and*
    cached).

    `written_at` is `query -> Optional[float]`, when the tile's live script
    was written or None if there is none. Without it - a test, the preview,
    a cache that cannot say - nothing heard comes back, which is the rule
    this replaced. A probe that raises is treated the same way: an unknown
    must never be read as "fresh".
    """
    at = heard.last(topic)
    if not at:
        return False
    if not answer_moves(topic) or written_at is None:
        return True
    if now - at < REMAKE_AFTER:
        return True
    try:
        made = written_at(topic.query)
    except Exception:  # noqa: BLE001 - a browse row is never worth a 500
        log.exception("could not date the script for %r; treating as heard",
                      topic.id)
        return True
    return made is not None and made <= at + REMAKE_MARGIN


def repeats(topics: Iterable["Topic"], heard: Heard, now: float,
            written_at=None) -> set[str]:
    """Every tile id that would be a repeat for this listener.

    Starts from every id they have played - so a tile no inventory holds any
    more stays excluded - and then answers `is_repeat` for each tile the page
    could actually offer, which is what lets a remade one back in.
    """
    blocked = set(heard.by_id)
    for topic in topics:
        if topic.follows:
            # A follow-up was already judged by `trending_for`, which is the
            # only thing that knows a heard one may come back once the story
            # has moved on again. Its id is the same each time, so the rule
            # below would call every second follow-up a repeat.
            blocked.discard(topic.id)
            continue
        if is_repeat(topic, heard, now, written_at):
            blocked.add(topic.id)
        else:
            blocked.discard(topic.id)
    return blocked


def known_topics(now: Optional[float] = None) -> dict[str, Topic]:
    """Every tile this server could name right now: the bank, the startup
    set, then the pool.

    The bank wins a collision, which cannot happen - a story id starts `st-`
    and a bank id is a hand-written word - but saying which wins is cheaper
    than finding out the day somebody adds a bank entry called `st-...`.
    """
    known = dict(BANK_BY_ID)
    known.update(STARTUP_BY_ID)
    # `LOCAL_STARTUP` is deliberately absent: this map is read to *draw* a
    # tile, and the template's title still contains its placeholder. A
    # listener who played the local question is one whose friends' rail
    # cannot offer it back, which is correct - it was about their town, not
    # about anybody else's.
    for topic in live_topics(now):
        known.setdefault(topic.id, topic)
    return known


def rank_most_played(
    store: EventStore, now: Optional[float] = None, exclude: Optional[set[str]] = None,
    limit: int = SECTION_SIZE, written=None,
) -> list[Topic]:
    """Total listens across FAM, most first. Deliberately identical for
    everyone, which is what makes it the cheapest section to serve: one
    script, every listener.

    Renamed from `rank_trending`: this was always FAM's own popularity, and
    "trending" now means the world - a separate row on a separate source. The
    two answer different questions and a listener reads them differently, so
    they are two rows rather than one blended ranking.

    **Live stories count here too.** A story somebody tapped this morning is
    exactly as much "what FAM can't stop listening to" as a bank topic, and
    excluding it would have made this row a ranking of the evergreen bank
    wearing a heading about the whole app.

    `written` is an optional `query -> bool`: whether that episode's script is
    already in the shared cache. The packet asks for this row to be the cached
    one, and a written tile is one a listener hears instantly and FAM pays
    nothing for - so written tiles sort first *within* the ranking. Not a
    filter: a deployment whose cache has just expired would show an empty row,
    which is a fact about the cache being told as a fact about what people are
    playing.

    **It fills from plays and from nothing else, and an unplayed row is
    empty.** It used to top itself up from the evergreen bank on the argument
    that "a stable slice beats an empty section, and beats a random one" -
    true about the *content*, and beside the point, because the heading is a
    claim. "What FAM can't stop listening to" over twenty-eight tiles nobody
    has ever played says something about this deployment that is not so, and
    a row that over-claims is worse than a row that is short: §89's rule
    about empty rows is that the app may report a fact about itself and never
    invent one about the world, and what its own listeners are playing is the
    most checkable fact on the page. Reversed at the owner's direction, which
    is what CLAUDE.md §124 said it would take - it recorded the filler as a
    documented decision precisely so undoing it had to be one too.

    The cost, stated rather than discovered: a fresh deployment shows this
    row empty until somebody plays something, and `tools/seed_demo.py` is
    what fills it for a demo. That is the same bargain Explore already makes
    and for the same reason - it replays what listeners did, so on a database
    where nobody has listened there is honestly nothing to replay.
    """
    now = time.time() if now is None else now
    exclude = exclude or set()
    known = known_topics(now)
    counts: dict[str, int] = {}
    for _user, topic_id in store.plays_since(now - TRENDING_WINDOW):
        if topic_id in known:
            counts[topic_id] = counts.get(topic_id, 0) + 1
    # Only the tiles that could appear here are asked about. Probing the whole
    # inventory would be a cache read per topic on every feed load to answer a
    # question about topics nobody has played - cheap each, and the sort of
    # cheap that a browse surface does thirty times a page.
    candidates = [t for t in known.values()
                  if t.id in counts and t.id not in exclude]
    ready = _ready_set(candidates, written)
    ranked = sorted(
        candidates, key=lambda t: (t.id not in ready, -counts[t.id], t.id))
    return ranked[:limit]


def _ready_set(topics: Iterable[Topic], written=None) -> set[str]:
    """Which of these already have a script. Empty when nobody asked.

    One local SQLite read per tile and never a model call, so marking a whole
    feed is as cheap as ranking it. `None` means the caller does not have a
    cache to ask - a test, the preview, `write.py` - and the honest answer
    then is "no information", which sorts nothing rather than sorting
    everything as unwritten.
    """
    if written is None:
        return set()
    ready = set()
    for topic in topics:
        try:
            if written(topic.query):
                ready.add(topic.id)
        except Exception:  # noqa: BLE001 - a browse row is never worth a 500
            log.exception("could not check the cache for %r; treating as unwritten",
                          topic.id)
    return ready


#: How deep into a personal rail's ranking a cached episode is looked for.
#: Twice the usual candidate width: every tile here has already cleared the
#: relevance floor, so reaching further for one that plays instantly and costs
#: nothing trades a little rank for a lot of latency and money.
READY_REACH = SECTION_SIZE * CANDIDATE_FACTOR * 2


def ready_first(topics: list, written=None) -> list:
    """The same tiles, those with a script already written first.

    A stable sort, so each group keeps the ranking's own order - the best
    cached tile leads, then the next best, and the best unwritten tile follows
    the last cached one. Never a filter: an unwritten tile is still offered,
    it just stops outranking one that is ready. `written=None` changes nothing.
    """
    ready = _ready_set(topics, written)
    if not ready:
        return list(topics)
    return ([t for t in topics if t.id in ready]
            + [t for t in topics if t.id not in ready])


def rank_from_history(profile: dict[str, float], exclude: set[str],
                      damp: Optional[dict[str, float]] = None,
                      limit: int = SECTION_SIZE,
                      candidates: Optional[Iterable[Topic]] = None,
                      familiar: frozenset = frozenset(),
                      local: frozenset = frozenset(),
                      engage: Optional[dict[str, float]] = None,
                      floor: float = RELEVANCE_FLOOR,
                      semantic: Optional[dict[str, float]] = None,
                      learned=None) -> list[Topic]:
    """Closest match to what they already play. Exploitation.

    `damp` is the fatigue multiplier: a tile offered here again and again and
    never taken loses ground to one that has not been asked yet.

    `candidates` is how the live story pool gets in. Made for you is the one
    rail drawing on both inventories - the packet asks for "a mix of new and
    cached" - and the mixing happens here rather than in two rankings stitched
    together afterwards, because two rankings would need a rule for how many
    of each, and there is no honest answer to that: it depends entirely on
    whether anything happened today in the corner of the world this listener
    cares about. One score over both inventories answers it by measuring,
    and `FRESHNESS_BOOST` is the only thumb on the scale.

    **Two more thumbs now, both answering the same complaint** - that this
    rail offered episodes about things the listener had never given any sign
    of caring about. `_affinity` counts a subtag match for more than a facet
    match (`SUBTAG_WEIGHT`), and a *live story* whose only claim is a facet,
    and whose words this listener has never used, is cut by
    `BROAD_MATCH_PENALTY`. The second is deliberately not applied to the
    bank: those twenty-eight subjects are broad by construction, so
    penalising breadth there would penalise the whole evergreen inventory.

    `floor` is the third and the bluntest. Under it a tile is not a
    recommendation, it is the least bad thing left in the inventory, and this
    rail is better short than padded - its heading claims these were chosen
    for this listener. It used to be `> 0`, which every tile sharing one
    barely-touched facet clears.

    `local` is the listener's own city and region, from
    `preferences.Location.words`. It is the fourth thumb and the only positive
    one: a live story about where they actually are is worth more than a
    comparable one about somewhere else. It cannot rescue a tile from the
    floor on its own - a boost applied to a score near zero is still near
    zero - which is deliberate, because "it is local" is a tie-breaker and
    never a subject somebody asked for.

    `engage` is the second question - *will they tap it* - where everything
    above answers *will they like it*. It is applied **before** the floor on
    purpose: a tile nobody taps should be able to fall out of a rail whose
    heading claims these were chosen for this listener, and one that converts
    well should be able to clear it. See `ENGAGEMENT_WEIGHT` for the four
    things that stop it turning this rail into a second copy of what
    everybody plays.

    `semantic` is the meaning of what they have asked for against each tile
    (`taste_vectors.for_listener`), and it is **added** to `_affinity` rather
    than multiplied into it, because its job is to find what the tags missed
    and a multiplier on a zero is a zero. A tile with a real semantic match is
    also exempt from `BROAD_MATCH_PENALTY`: that penalty exists for a subject
    this listener has never been near, and a close match to something they
    asked for is exactly having been near it. `{}` - no model installed - is
    the ranking that shipped before it existed.

    `learned` is a `learned_rank.Model` or None. It **re-orders what cleared
    the floor and never decides what clears it** - see `learned_rank` for why
    that split is the whole safety argument. The two thumbs the log cannot
    record, freshness and a listener's own place, stay hand-applied on top of
    the model's probability.
    """
    damp = damp or {}
    semantic = semantic or {}
    pool = list(candidates) if candidates is not None else list(TOPIC_BANK)
    scored = []
    for topic in pool:
        if topic.id in exclude:
            continue
        score = ((_affinity(topic, profile) + semantic.get(topic.id, 0.0))
                 * damp.get(topic.id, 1.0)
                 * (1.0 + FRESHNESS_BOOST * topic.freshness))
        # A live story is one specific thing that happened; `freshness` is
        # exactly what distinguishes one from a bank topic here, and it is
        # set by `topics_from_stories` and by nothing else.
        if (score > 0 and topic.freshness > 0
                and _is_broad_match(topic, profile)
                and not _subject_is_familiar(topic, familiar)
                and not semantic.get(topic.id)):
            score *= BROAD_MATCH_PENALTY
        # After the penalty rather than before it, so the two multiply in a
        # stated order rather than one silently cancelling the other. In
        # practice a local story rarely meets the penalty at all: the
        # listener's own place words are folded into `familiar` by
        # `build_feed`, which is the free half of this - living somewhere is
        # a perfectly good answer to "have you ever been near this subject".
        if _is_local(topic, local):
            score *= LOCAL_BOOST
        score *= (engage or {}).get(topic.id, 1.0)
        if score > floor:
            scored.append((score, topic))
    if learned is not None and scored:
        rows = {t.id: learned_rank.features(t, profile, semantic, damp,
                                            engage or {}, familiar)
                for _s, t in scored}
        thumbs = {t.id: (1.0 + FRESHNESS_BOOST * t.freshness)
                  * (LOCAL_BOOST if _is_local(t, local) else 1.0)
                  for _s, t in scored}
        scored = learned_rank.rerank(scored, learned, rows, thumbs)
    else:
        scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [t for _s, t in scored[:limit]]


def startup_profile(
    store: "EventStore", now: Optional[float] = None
) -> tuple[dict[str, float], str]:
    """The prior to rank a listener we know nothing about. "The algorithm
    before the algorithm."

    Returns the profile *and where its order came from* - `"played"` or
    `"default"` - because those look identical on a screen and only one of
    them is a measurement. Same contract as `popular_facets`, whose answer
    this is, and for the same reason: this is asked of somebody with no
    history, so the only honest signal is everybody else's, and one count
    serves every listener.

    It is a `taste`-shaped dictionary rather than a list of `interests`
    deliberately. Interests enter `taste` at a flat `INTEREST_WEIGHT`, so
    routing the prior through that channel would throw away the one thing it
    knows - which facet leads - and hand the ranking back to the `topic.id`
    tiebreak. It is also not a *claim* that this listener chose anything:
    nothing here is written to their preferences, and one play replaces the
    whole of it.

    Every facet is present, not just the six the picker shows. The picker is
    narrowing a screen to a grid; this is ranking an inventory, and leaving
    two facets at zero would make two of the eight startup topics unofferable
    to the listener who wanted exactly those.

    **`popular_facets` counts plays of the bank and not of the startup set,
    and that is load-bearing rather than an omission.** This prior decides
    what every cold-start listener is offered, and a cold-start listener's
    first play is by definition a play of what it offered them - so counting
    those plays here would close the loop and the prior would spend the rest
    of the deployment's life confirming its own opening guess. It is the same
    failure the impression rule already names ("an impression must never
    become taste - that is a feedback loop where the feed teaches itself its
    own preferences"), arriving through a different door. Fed only by the
    bank, this stays a measurement of something the prior does not choose.

    A play on a startup tile is not wasted by that: it goes into *that
    listener's* own `taste` in full, which is what retires the prior for them
    on the very next draw. It is only barred from voting on what the next
    stranger sees.
    """
    order, source = popular_facets(store, limit=len(TAG_LABELS), now=now)
    # `popular_facets` is capped at the facets it can see. Anything it left
    # out keeps the declared order behind what it returned, so the prior
    # always covers all eight.
    for facet in PICKER_DEFAULT_ORDER:
        if facet not in order:
            order.append(facet)
    return ({facet: 1.0 - rank * STARTUP_PRIOR_STEP
             for rank, facet in enumerate(order)}, source)


def rank_startup(profile: dict[str, float], exclude: set[str],
                 limit: int = SECTION_SIZE,
                 candidates: Optional[Iterable[Topic]] = None,
                 damp: Optional[dict[str, float]] = None,
                 familiar: frozenset = frozenset(),
                 local: frozenset = frozenset(),
                 local_topic: Optional[Topic] = None,
                 engage: Optional[dict[str, float]] = None) -> list[Topic]:
    """The first rail a listener with no history sees: the startup set first,
    then the ordinary ranking behind it.

    **Leading with the set is the whole point, and it is a deliberate
    departure from how every other rail works.** Elsewhere a ranking chooses
    from one pool and the best tile wins; here the eight startup questions go
    in front of the twenty-eight bank topics *by construction*, because the
    thing that makes them right for this listener is not that they score
    better - against a prior nobody measured they score much the same - it is
    that they are about today and the bank is about always. A score cannot
    express that difference without borrowing `freshness` from the live pool,
    which would be a lie about that field (see `STARTUP_TOPICS`).

    Ordered among themselves by `_affinity` against the startup prior, so the
    facet FAM's listeners play most leads. Topped up from `rank_from_history`
    over whatever was passed as `candidates` - the bank, and the live story
    pool when this deployment has one - so a rail is never shorter than a
    rail, and so a deployment that *does* have live stories still shows them
    here.

    `damp` is the fatigue multiplier and applies to the lead as well as the
    top-up: eight tiles into a rail of six means the two at the bottom are
    unreachable otherwise, however many times somebody declines the top six.

    `BROAD_MATCH_PENALTY` is not applied to the startup set and cannot be: it
    damps a live story matching only a facet whose words this listener has
    never used, and a cold-start listener has used no words at all, so the
    check has nothing to measure and would damp every tile equally. It still
    applies to the top-up, which goes through `rank_from_history` unchanged.
    """
    damp = damp or {}
    lead = [t for t in STARTUP_TOPICS if t.id not in exclude]
    # Damped, like every other personalised rail. There are eight of these and
    # a rail shows six, so without it a listener who keeps opening myFAM and
    # never tapping sees the same six in the same order forever and the other
    # two are unreachable. It can only reorder, never shorten: no floor is
    # applied to the lead, `FATIGUE_FLOOR` keeps the multiplier positive, and
    # `FATIGUE_GRACE` means being sent a tile twice is not yet evidence of
    # anything - which is the whole reason this is safe on an inventory this
    # small.
    # The local question joins the sort rather than jumping it. Nine tiles,
    # one ordering, `LOCAL_BOOST` on the one that is about where they live.
    #
    # Prepending it would have been the obvious move and is the wrong one,
    # for the reason the note above already gives: a tile that leads
    # unconditionally leads forever, and somebody shown local news six times
    # without tapping it has told us something. Going through the same sort
    # means fatigue reaches it like everything else - and it keeps
    # `LOCAL_BOOST` honest, because that constant says a location is a
    # tie-breaker rather than a subject, and a tile placed first by fiat
    # would be a subject.
    if local_topic is not None and local_topic.id not in exclude:
        lead = [t for t in lead if t.id != local_topic.id] + [local_topic]
    # `engage` matters more here than on any other rail. The prior orders
    # these eight by what FAM's listeners *play*, which is the honest signal
    # for somebody with no history - and "which of these eight questions
    # actually gets tapped when it is shown" is a sharper form of the same
    # signal, measured on this exact set rather than on the facets behind it.
    engage = engage or {}
    lead.sort(key=lambda t: (
        -_affinity(t, profile) * damp.get(t.id, 1.0)
        * engage.get(t.id, 1.0)
        * (LOCAL_BOOST if local_topic is not None and t.id == local_topic.id
           else 1.0),
        t.id))
    lead = lead[:limit]
    if len(lead) >= limit:
        return lead
    # The floor is dropped for the top-up, not for the lead. Against a prior
    # rather than a measured taste, `RELEVANCE_FLOOR`'s reasoning does not
    # hold - it exists to keep a rail whose heading claims relevance from
    # offering the least bad thing in the bank, and this rail's heading claims
    # to be a starting point. Padding it with a real topic beats ending it
    # early, which is the opposite of the call `rank_from_history` makes and
    # is the right one here for exactly that reason.
    seen = exclude | {t.id for t in lead}
    rest = rank_from_history(profile, seen, damp, limit=limit - len(lead),
                             candidates=candidates, familiar=familiar,
                             local=local, engage=engage, floor=0.0)
    return lead + rest


def rank_bank(profile: dict[str, float]) -> list[Topic]:
    """The whole bank, best match first. What the mix picker offers.

    **A sort and never a filter**, which is the difference between this and
    every rail on myFAM. A rail is one of five and a listener who does not
    like its picks can scroll to the next one; the picker *is* the list, so
    one that hid what it could not rank would be a picker somebody could not
    find a topic in.

    It also does not exclude what they have played, which every rail does:
    wanting a mix of subjects you already like is the entire point of a mix.

    With no profile at all the order is the bank's own, which is honest - a
    listener with no history has expressed no preference, and inventing one
    from `topic.id` is what the picker was doing when its heading already
    said "Suggested topics".

    **This keeps the whole bank whatever `browse_inventory` says**, and the
    exemption is the point rather than an oversight. That rule is about what
    FAM *offers* somebody unprompted - a tile on a shelf, under a heading
    making a claim. This is a menu somebody opened in order to choose
    subjects, and a mix holds topic ids rather than audio, so a bank member
    in a mix is a fresh episode every morning and never a standing one
    replayed. Applying the rule here would also empty the picker for exactly
    the listeners who can use it, since a saved mix needs an account - which
    is §123's failure, one screen over: a fact about the account gate
    reported as a broken topic list.
    """
    if not profile:
        return list(TOPIC_BANK)
    scored = [(_affinity(topic, profile), topic) for topic in TOPIC_BANK]
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [topic for _score, topic in scored]


def rank_missed(profile: dict[str, float], shown: dict[str, float],
                played: set[str], exclude: set[str],
                candidates: Iterable[Topic],
                limit: int = MISSED_SECTION_SIZE,
                now: Optional[float] = None,
                popular: Optional[set[str]] = None,
                familiar: frozenset = frozenset(),
                include_trending: bool = True,
                floor: float = RELEVANCE_FLOOR) -> list[Topic]:
    """The week's best episodes this listener did not take.

    The replacement for the weekly recap, and a different kind of thing from
    it: the recap was an *episode about their week*, written from their own
    log, which meant a listener who had a thin week got a thin episode about
    having a thin week. This is a shelf of episodes they can still have.

    **What counts as "missed" widened, at the owner's direction.** It used to
    be the impression log and nothing else - only tiles this app had put on a
    screen in front of this person. That was a defensible reading of the
    heading and it made the rail a report on our own delivery: a listener who
    did not open myFAM last week missed nothing, by construction, however
    much happened. The direction is that it should hold "the ABSOLUTELY MOST
    RELEVANT stories they didn't click on or listen to in the last week...
    it could be stories that were popular throughout the app or trending that
    the user never listened to".

    So membership is now three things, any of which qualifies:

    * **offered to them** - the impression log, as before;
    * **popular across FAM** - played by other listeners inside the window;
    * **trending** - anything in the live story pool, which is what the world
      has been on this week by definition.

    What keeps the heading honest is that all three are things that genuinely
    went past this listener in the last seven days, and none of them is
    invented. There is still no top-up from the standing bank: an evergreen
    explainer nobody was offered and nobody played did not happen last week,
    and putting one here to make the row look full is the padding this rail
    was built to avoid.

    Two rules survive the widening unchanged, and one is added.

    **An impression still never becomes taste.** Being shown something says
    nothing about whether you wanted it, and CLAUDE.md is emphatic that
    letting it into the taste model is how a feed teaches itself its own
    preferences. It decides *membership* - a fact about the feed - and
    `_affinity` decides the order. Widening membership does not change that;
    it adds two more facts about the feed and the crowd.

    **It can only offer what it can still resolve.** A live story that expired
    and fell out of the pool has no title, no angle and no question, and a
    tile invented to stand in for one would be exactly the failure this whole
    subsystem is built against. So `candidates` is the bank plus what the pool
    still holds, and a story that has aged out is simply not in the rail.

    **And now: relevance is a floor, not just a sort.** "Most relevant" is
    the whole of the instruction, so a tile this listener has no affinity for
    is not offered at all - the rail is honestly short rather than padded
    with the least bad thing that went past. That is also what stops the
    widened membership turning this into a second copy of Trending for
    somebody who was shown nothing.
    """
    now = time.time() if now is None else now
    popular = popular or set()
    by_id = {t.id: t for t in candidates}

    # **No profile, no relevance, and so no widening and no floor.**
    #
    # `taste` is empty for a listener who has chosen nothing and played
    # nothing - impressions deliberately never reach it - so every affinity
    # is 0.0, every tile fails the floor, and a rail that should have been
    # "here is what you did not get to" would be empty for exactly the
    # listener it is most use to. Widening membership would be worse still:
    # eight trending tiles under a heading saying *you* missed them, chosen
    # by nothing, for somebody the app knows nothing about.
    #
    # So with nothing to rank on, this is what it always was: what was put in
    # front of them, newest first. The claim shrinks back to the one the
    # evidence supports.
    if not profile:
        offered = [(at, by_id[tid]) for tid, at in shown.items()
                   if tid in by_id and tid not in played and tid not in exclude
                   and now - at <= MISSED_WINDOW]
        offered.sort(key=lambda row: (-row[0], row[1].id))
        return [topic for _at, topic in offered[:limit]]

    rows = []
    for topic_id, topic in by_id.items():
        if topic_id in played or topic_id in exclude:
            continue
        offered_at = shown.get(topic_id, 0.0)
        was_offered = bool(offered_at) and now - offered_at <= MISSED_WINDOW
        # A live tile is in the pool, so it is current by construction - the
        # pool's own shelf life is what decides that, not a second clock here.
        # `include_trending` is how `build_feed` keeps this rail from
        # claiming a brand-new story ahead of Made for you - see FILL_ORDER.
        is_trending = topic.freshness > 0 and include_trending
        if not (was_offered or is_trending or topic_id in popular):
            continue
        score = _affinity(topic, profile)
        if (score > 0 and is_trending and _is_broad_match(topic, profile)
                and not _subject_is_familiar(topic, familiar)):
            # The same rule Made for you keeps, for the same reason: a story
            # nobody has shown any interest in is not a thing they *missed*.
            score *= BROAD_MATCH_PENALTY
        if score <= floor:
            continue
        rows.append((score, 1 if was_offered else 0, offered_at, topic))
    # Relevance first, then whether it was actually put in front of them -
    # which is the strongest reading of "missed" and so wins a tie - then
    # most recently offered.
    rows.sort(key=lambda row: (-row[0], -row[1], -row[2], row[3].id))
    return [row[3] for row in rows[:limit]]


def rank_friends(
    store: EventStore, circle: Iterable[str], exclude: set[str],
    damp: Optional[dict[str, float]] = None, limit: int = SECTION_SIZE,
    now: Optional[float] = None, written=None,
) -> list[Topic]:
    """What the people this listener actually follows have been listening to.

    **This used to be co-listener overlap** - "people who played what you
    played also played this" - under a heading that said "people you follow",
    on a card that said "People you follow played this". That was a real
    signal wearing somebody else's name, and it was honest only in a comment
    in this file. The follow graph has existed since SHARING.md; the rail now
    reads it.

    `circle` is passed in rather than looked up, so this module keeps knowing
    nothing about `social.py` and stays a pure function of the event log. The
    caller decides who counts as the circle - today that is friends (mutual
    follows) first and then anyone they follow, which is `social.circle_of`.

    An empty circle returns nothing, deliberately. A rail called "what your
    friends are listening to" that quietly showed strangers would be the
    original problem again with better wording, and the empty state names the
    thing to do about it.
    """
    circle = {u for u in circle if u}
    if not circle:
        return []
    now = time.time() if now is None else now
    known = known_topics(now)
    damp = damp or {}
    counts: dict[str, float] = {}
    for user_id, topic_id in store.plays_since(now - FRIENDS_WINDOW):
        if user_id in circle and topic_id in known and topic_id not in exclude:
            counts[topic_id] = counts.get(topic_id, 0.0) + damp.get(topic_id, 1.0)
    if not counts:
        return []
    ready = _ready_set((known[i] for i in counts), written)
    ordered = sorted(counts, key=lambda i: (i not in ready, -counts[i], i))
    return [known[i] for i in ordered[:limit]]


#: How many Explore New shows. Six, and deliberately not `SECTION_SIZE`: that
#: became four in §134 as a rule about *rails on myFAM*, and Explore New is a
#: screen somebody opened on purpose rather than a rail.
EXPLORE_NEW_SIZE = 6


def rank_might_like(profile: dict[str, float], exclude: set[str],
                    damp: Optional[dict[str, float]] = None,
                    limit: int = EXPLORE_NEW_SIZE) -> list[Topic]:
    """Adjacent, not identical. Exploration.

    Serves two surfaces from one ranking: the Explore New rail on myFAM and
    the Explore New screen behind it. One ranking rather than two, so the rail
    and the page it opens cannot give a listener different answers to the same
    question - the same reason the What's next popup reuses the feed's ranking.

    Their strongest tag is deliberately suppressed. Ranking purely on affinity
    gives four sections of the same thing and a listener who only ever hears
    back what they already told you - the filter bubble, arrived at by
    accident. This section is the one that widens the bank.

    Built in tiers that top each other up rather than replace each other. A
    listener whose entire history is one tag has an empty profile the moment
    it is muted, and they are exactly who this section exists for; the earlier
    version returned nothing for them, which the tests caught.

    **It keeps the bank whatever `browse_inventory` says**, on the same
    distinction `rank_bank` draws. Explore New is off the page (`UNSHELVED`)
    and is reached only by a listener who went looking for something outside
    their taste; widening a taste is what it is for, and doing that over the
    eight startup questions - one per facet, and the facets are what this
    ranker mutes - would leave it with almost nothing to widen *into*. A
    surface somebody opened on purpose is not the app offering them filler.
    """
    damp = damp or {}
    if not profile:
        return [t for t in TOPIC_BANK if t.id not in exclude][:limit]

    # The strongest tag's whole *family* is muted, not just the tag. With two
    # levels, muting `sports-performance` on its own leaves `sports` at full
    # strength and the section quietly becomes history with a new heading -
    # the exact bubble this exists to break. Muting the facet alone has the
    # mirror problem, so it is both, together.
    top_tag = max(profile, key=lambda k: profile[k])
    top_facet = facet_of(top_tag)
    top_family = {k for k in profile if facet_of(k) == top_facet}
    muted = {k: v for k, v in profile.items() if k not in top_family}
    picks: list[Topic] = []
    taken = set(exclude)

    def add(candidates: list[tuple[float, Topic]]) -> None:
        candidates.sort(key=lambda pair: (-pair[0], pair[1].id))
        for _score, topic in candidates:
            if len(picks) >= limit:
                return
            if topic.id not in taken:
                picks.append(topic)
                taken.add(topic.id)

    # 1. Their other interests, with a nudge toward anything that also brings
    #    a tag they have never touched.
    tier = []
    for topic in TOPIC_BANK:
        if topic.id in taken:
            continue
        score = _affinity(topic, muted)
        if score <= 0:
            continue
        if any(tag not in profile for tag in topic.tags):
            score *= 1.4
        tier.append((score * damp.get(topic.id, 1.0), topic))
    add(tier)

    # 2. Bridges out of the tag they already have: keep the familiar tag, but
    #    only where it is paired with something new, so it leads somewhere.
    if len(picks) < limit:
        add([
            (float(sum(1 for tag in t.tags if tag not in profile)), t)
            for t in TOPIC_BANK
            if t.id not in taken and top_family & set(t.tags)
            and any(tag not in profile for tag in t.tags)
        ])

    # 3. Anything genuinely unseen. An empty shelf helps nobody, and a narrow
    #    listener is the one who most needs a way out of the bubble.
    if len(picks) < limit:
        add([
            (float(sum(1 for tag in t.tags if tag not in profile)), t)
            for t in TOPIC_BANK if t.id not in taken
        ])

    return picks


def rank_followers(
    store: EventStore, user_id: str, mine: set[str], exclude: set[str],
    damp: Optional[dict[str, float]] = None, limit: int = SECTION_SIZE
) -> list[Topic]:
    """Co-listener overlap: people who played what you played also played this.

    **No longer the "friends" rail**, and that is the point of the split.
    This used to fill a shelf headed "people you follow", which it was not;
    `rank_friends` reads the real graph and has that heading now. What is left
    here is the signal itself, which is a good and standard one - it is what
    tops up the post-episode popup when history alone cannot fill four tiles,
    where the question is "what next" and nothing claims these people are
    anybody's friends.
    """
    if not mine:
        return []
    by_topic = store.users_who_played(BANK_BY_ID.keys())
    neighbours: set[str] = set()
    for topic_id in mine:
        neighbours |= by_topic.get(topic_id, set())
    neighbours.discard(user_id)
    if not neighbours:
        return []
    damp = damp or {}
    scored = []
    for topic in TOPIC_BANK:
        if topic.id in exclude:
            continue
        overlap = len(by_topic.get(topic.id, set()) & neighbours)
        if overlap:
            scored.append((overlap * damp.get(topic.id, 1.0), topic))
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [t for _s, t in scored[:limit]]


def build_feed(store: EventStore, user_id: str, now: Optional[float] = None,
               interests: Iterable[str] = (), circle: Iterable[str] = (),
               written=None, place: Iterable[str] = (),
               place_name: str = "", has_account: bool = False,
               floors: Optional[dict] = None, country: str = "",
               written_at=None) -> dict:
    """The whole myFAM page for one listener.

    **The order of operations** (the owner's, stated as a path): decide what
    this listener is into (`taste`, or the startup prior with nothing to go
    on); rank each personal rail on it, looking `READY_REACH` deep and putting
    the tiles whose script is already written first (`ready_first`) so the
    page holds as many instant, already-paid-for episodes as it can; and
    cross-check every tile against everything they have ever heard
    (`repeats`). A heard tile is dropped and the next-best topic takes its
    place, unless its answer moves and it has since been remade - see
    `is_repeat`. `written_at` is what makes that last case knowable.

    Sections are filled in order and never repeat a topic, so the page looks
    as wide as possible from two deliberately small inventories.

    **Nothing here generates anything, and nothing here waits on anything.**
    That is the packet's "zero queue" requirement and it is structural rather
    than fast: this function reads the event log and two caches somebody else
    refreshed in the background, ranks what it finds, and returns. There is no
    path from a page load to a model call, so there is nothing that could
    queue.

    `interests` come from the intro. They matter most on the first open, when
    "Made for you" would otherwise be empty and the honest empty-state is the
    only thing a new listener sees.

    `circle` is who counts as this listener's friends - see `rank_friends`.
    `written` is an optional `query -> bool` that says whether a tile's script
    is already in the shared cache; the two crowd rows lead with the ones that
    are, and every tile carries the answer so the interface can say so.

    `place` is the listener's own city and region as words - what
    `preferences.Location.words` returns. It reaches exactly two rails, both
    of which answer "what should I hear", and it deliberately does not reach
    Trending: that row is the world's and a per-listener thumb on it would
    give two listeners different answers to a question that claims to have
    one. It is words rather than a `Location` so this module keeps knowing
    nothing about `preferences` - the same arrangement `circle` has with
    `social`.

    `place_name` is the same location as a readable label - "Cincinnati,
    Ohio" - and it is used for one thing: the cold-start rail's local
    question, which puts the place into the *query* it offers. Separate from
    `place` because the two are genuinely different values and neither derives
    from the other: a set of match words has lost the order and the
    capitalisation a question needs, and a label is the wrong thing to match a
    headline against.

    `floors` is the fewest tiles each rail shows - `RAIL_MINIMUM` unless a
    caller says otherwise (§127). `{}` turns the top-up off, which is how a
    test asks what a rail *chose* rather than what it was filled to.

    `country` is the one input Trending takes from the listener (§134) - see
    `rank_world`. Nothing else on the page reads it.

    `has_account` is whether credentials are attached to this listener, and it
    decides one thing only: whether the evergreen bank is offered. See
    `browse_inventory` for the rule and the reasoning. It arrives as a bare
    bool from the request boundary for the same reason `circle` and `place`
    do - this module stays a pure query over the event log and knows nothing
    about `accounts`.

    **It defaults to False, which is the generous answer**, and that is
    deliberate: a caller that has not been taught about accounts - a test,
    `write.py`, the fixture preview - is a caller that does not know, and the
    honest reading of "we do not know" here is the cold-start one. The
    failure it avoids is the one worth avoiding: defaulting to True would
    silently take the bank away from every surface whose caller was never
    updated, and an emptier page is exactly the failure that looks like a
    design decision rather than a bug.
    """
    now = time.time() if now is None else now
    events = store.for_user(user_id) if user_id else []
    profile = taste(events, now, interests)
    # **The cold start, decided by measurement rather than by a flag.**
    #
    # An empty `taste` is exactly the listener `startup.py` exists for: no
    # account, no chosen interests, nothing played. Deriving it here rather
    # than reading a "they skipped the intro" preference is the same call this
    # whole module already makes - a taste profile is a query, not a stored
    # object - and it is strictly better than a flag, because a flag cannot
    # say whether behaviour has since arrived. One play, one search or one
    # chosen interest and this is False again for good.
    cold = not profile
    startup_source = ""
    prior: dict[str, float] = {}
    if cold:
        prior, startup_source = startup_profile(store, now)
    played = _played_ids(events)
    # One read for the whole page. Every personalised section damps the same
    # way, so computing this per section would be the same answer four times.
    damp = fatigue(store.impression_occasions(user_id), played) if user_id else {}
    used: set[str] = set()
    picked: dict[str, list[Topic]] = {}

    # The live inventory, read once for the page. Synchronous against a cache
    # somebody else refreshed, so `build_feed` stays a pure function of the log
    # plus that cache - which is what keeps it callable in a test with no
    # network, and what makes the page instant. See `stories.py`.
    live = live_topics(now)
    # **No repeats, part one: a heard live story is never offered as itself**
    # (§136). Anywhere on the page it becomes a "what's new since you
    # listened" follow-up if it has kept being reported, or it is gone and
    # the next story takes its place - `trending_for`. Done here, before any
    # rail sees the pool, so Trending and the personal rails cannot disagree.
    heard = heard_from(store.heard(user_id)) if user_id else None
    live = trending_for(live, heard, now)
    # What every rail below is allowed to *offer*, decided once for the page.
    # One list rather than four `live + list(TOPIC_BANK)` expressions, because
    # a rule spelled out at each call site is a rule one of them will spell
    # differently - which is §119 exactly. See `browse_inventory`.
    inventory = browse_inventory(live, has_account)
    # Everything the pool holds, cap included. "What you missed" has to be able
    # to resolve a tile that was offered a few days ago and has since been
    # pushed under the variety cap - to that listener it was on the page, and
    # a rail that quietly dropped it would be answering a different question.
    live_held = trending_for(
        topics_from_stories(stories.pool().held(now), now=now), heard, now)
    # **No repeats, part two.** Everything this listener has ever heard, from
    # every surface, checked against every tile this page could offer. What
    # comes back is excluded from every rail below; a startup question that
    # has been remade since they heard it is not in it. Named `mine` because
    # it replaced the played-ids set every rail already excluded.
    local_topic = local_startup_topic(place_name)
    universe = (list(known_topics(now).values()) + list(inventory)
                + list(live_held) + ([local_topic] if local_topic else []))
    mine = repeats(universe, heard, now, written_at) if heard else set()
    # Which tiles were put in front of them this week. Read once, like the
    # fatigue table, and for a different purpose - see `rank_missed` on why
    # membership may come from an impression and order may not.
    shown = store.impressions_since(user_id, now - MISSED_WINDOW) if user_id else {}
    # What this listener has actually said, for `rank_from_history`'s broad
    # match check. Read once for the page, like the fatigue table, and off
    # the events already in hand.
    familiar = familiar_words(events)
    # Where they say they are, folded straight into the familiar set. This is
    # the free half of the location change and the better half: living
    # somewhere is a complete answer to "has this listener ever been near this
    # subject", so a story about their own town stops being damped for being
    # a place they have never typed into a search box. `LOCAL_BOOST` is the
    # other half and is the only part that is a thumb on the scale.
    place = frozenset(w for w in place if w)
    familiar = familiar | place
    # The second question, read once for the page and shared by every
    # listener on this deployment. See `ENGAGEMENT_WEIGHT` for which rails
    # are allowed to use it.
    engage = engagement_for(store, now)
    # Meaning and a fitted order, for Made for you only (§131). Both are `{}`
    # / None on a deployment with no model and no trained ranking, which is
    # the page that shipped before either existed. Never on a cold start:
    # there is no history to mean anything, and the prior is not a taste.
    semantic = ({} if cold
                else taste_vectors.for_listener(events, inventory, now))
    learned = learned_rank.active(store, now)
    # What the rest of FAM played this week, for "What you missed". One read,
    # like everything else on this page, and it is a fact about the crowd
    # rather than about this listener - it decides membership and never
    # taste, which is the rule `rank_missed` is built on.
    played_elsewhere = {topic_id for user, topic_id
                        in store.plays_since(now - MISSED_WINDOW)
                        if user != user_id} if user_id else set()
    wide = SECTION_SIZE * CANDIDATE_FACTOR
    # How deep the personal rails look for a written tile. Only deeper when
    # there is a cache to ask, so a caller with none ranks exactly as before.
    reach = READY_REACH if written is not None else wide

    # **Trending chooses first, and chooses alone** (§134, at the owner's
    # direction). "The trending section should be trending news from around
    # the world, not based on the user's algorithm ... a user's interests or
    # past listens should not affect the content of the trending section."
    #
    # It used to be filled *after* the personal rails, from whatever they had
    # left - so what Made for you took, and what this listener had played,
    # decided what the world row showed. Both were this listener's history
    # reaching a row that claims to be about everybody. Now it is ranked from
    # the live pool alone, before anything else is chosen, with nothing of
    # the listener in it but their country; the personal rails then avoid its
    # tiles, so the page still never shows one tile twice. The trade, stated:
    # on a thin pool Made for you loses live tiles to this row. It has the
    # rest of the inventory to fall back on and Trending has nothing else.
    #
    # And never the startup set or the bank - see `rank_world`. A deployment
    # with no live source has an empty Trending row that says why.
    #
    # **And never a story this listener has heard** (§136, at the owner's
    # direction): it comes back as a follow-up if the story has moved on, or
    # the next trending story takes its place. See `trending_for`.
    world_live, world_held = world_inventory(live, live_held, heard, now)
    world_first = rank_world(world_live, country, world_held)
    # A follow-up's story is reserved as well, so no personal rail offers the
    # original beside the "what's new" episode about it.
    reserved = ({t.id for t in world_first}
                | {t.follows for t in world_first if t.follows})

    # Filled most-constrained first, displayed in the order the product asks
    # for. Filling in display order starves the two personal sections: the
    # generic ones draw on the whole inventory, so they claim the very topics
    # the personal ones needed and those arrive empty - which is exactly
    # backwards, since the personal sections are the point. (`most_played`
    # stopped being one of those since §125 - it now holds only what has been
    # played - but `might_like` still is, and the ordering is the same rule.)
    # What an UNSHELVED rail claimed, kept apart from `used`. See the note on
    # `most_played` below for why the difference matters.
    unshelved_held: set[str] = set()
    for key in FILL_ORDER:
        # Nothing they have already played, in any section. The feed's job is
        # to hand them the next episode; the crowd rows stay globally *ranked*,
        # they just stop offering back the one they finished this morning.
        seen = used | mine | reserved | unshelved_held
        if key == "missed":
            # The bank plus whatever the pool still holds - `held` rather than
            # `live`, so a story the variety cap is hiding is still resolvable.
            # What it cannot resolve, it does not offer: see `rank_missed`.
            # `seen` rather than `used`, which were the same thing until
            # `reserved` existed. They are not any more, and the difference is
            # a tile on two rails of one page: this rail fills first, so
            # `used` is empty, and a live story that *was* put in front of
            # this listener last week qualifies here on its impression - the
            # one route into this rail that `include_trending=False` does not
            # close. Trending has already been promised that tile.
            picks = rank_missed(profile, shown, mine, seen,
                                candidates=browse_inventory(live_held,
                                                            has_account),
                                limit=(reach if written is not None
                                       else MISSED_SECTION_SIZE), now=now,
                                popular=played_elsewhere, familiar=familiar,
                                include_trending=False)
        elif key == "from_history":
            # The one rail that draws on both inventories - today's stories
            # and the standing bank - which is what "a mix of new and cached"
            # asks for. Three of them on a cold start: the startup questions
            # lead and the other two top up behind them.
            #
            # **Only this rail gets the prior.** Each of the others would be
            # claiming something a prior cannot support - that these went past
            # you this week, that your friends played them - where this one's
            # question is "what should I hear", which is answerable for
            # somebody we know nothing about and is the whole reason the
            # startup set exists. The rest stay honestly empty.
            if cold:
                # The cold start is where a location is worth the most. It is
                # the one fact we hold about somebody we otherwise know
                # nothing about, and `startup.py` leads this rail with a
                # question about their own town when they have given one.
                picks = rank_startup(prior, seen, limit=reach,
                                     candidates=inventory,
                                     damp=damp, familiar=familiar, local=place,
                                     local_topic=local_topic,
                                     engage=engage)
            else:
                picks = rank_from_history(profile, seen, damp, limit=reach,
                                          candidates=inventory,
                                          familiar=familiar, local=place,
                                          engage=engage, semantic=semantic,
                                          learned=learned)
        elif key == "followers":
            picks = rank_friends(store, circle, seen, damp, limit=wide, now=now,
                                 written=written)
        elif key == "might_like":
            picks = rank_might_like(profile, seen, damp, limit=wide)
        else:
            # Not damped, deliberately: this row is the same list for
            # everyone, which is what makes it the cheapest section to serve.
            #
            # **And it ignores what the unshelved rail reserved**, which is
            # the one exception to the mutual exclusion above. `might_like`
            # is not drawn on this page; it claims its picks early so the
            # *drawn* rails do not show what Explore New would show, and that
            # is a sensible rule for a rail that chooses. This row does not
            # choose - it reports what listeners actually played - so a tile
            # held back by a ranking nobody is looking at is a genuinely
            # most-played episode missing from a row whose whole job since
            # §125 is to say what was played. It still avoids `mine` and the
            # drawn rails, so no tile appears twice on the page.
            #
            # Latent until §125: this row used to top itself up from the
            # bank, so being starved here was invisible. Removing the filler
            # is what made an old coupling show.
            picks = rank_most_played(store, now, used | mine | reserved,
                                     limit=wide, written=written)
        if key in ("missed", "from_history"):
            # Cached first, inside what the ranking chose. The two rails that
            # choose for this listener; the two crowd rows already lead with
            # written tiles in their own rankers.
            picks = ready_first(picks, written)
        picks = diversify(picks, MISSED_SECTION_SIZE if key == "missed"
                          else SECTION_SIZE)
        picked[key] = picks
        if key in UNSHELVED:
            unshelved_held |= {t.id for t in picks}
        else:
            used |= {t.id for t in picks}

    # The world row, as `rank_world` chose it before anything else did.
    picked["world_trending"] = world_first

    # Trending is the *last* source for "What you missed", not the first.
    #
    # A story nobody has been shown is not one this listener missed - it is
    # one Made for you exists to offer them, which is why the rail's first
    # pass runs with `include_trending=False`. What is left over after every
    # other rail has chosen is a different thing: it went past them this week,
    # it is still current, and nothing else on the page is going to show it.
    # Relevance decides, with the same floor, so a short rail stays short.
    if user_id and len(picked["missed"]) < MISSED_SECTION_SIZE and profile:
        taken = used | mine | {t.id for t in picked["world_trending"]}
        spare = rank_missed(
            profile, {}, mine, taken, candidates=live,
            limit=MISSED_SECTION_SIZE - len(picked["missed"]), now=now,
            familiar=familiar)
        picked["missed"] = picked["missed"] + spare
        used |= {t.id for t in spare}
    # Why it is empty, when it is. The pool's own sentence is right only when
    # the pool is empty; a pool that served perfectly well and was claimed by
    # the rail above would otherwise make this row say the live sources had
    # nothing - our own page's arrangement reported as a fact about the world,
    # which is the §89 mistake with a new way in.
    # The minimums, last, so every rail's own choice is already made and
    # nothing here can take a tile a ranking wanted. Most-constrained first:
    # the two rails the owner named first, then the rest.
    floors = RAIL_MINIMUM if floors is None else floors
    for key in ("from_history", "missed"):
        if not floors.get(key):
            continue
        on_page = {t.id for rail, tiles in picked.items()
                   if rail not in UNSHELVED for t in tiles}
        fallback = _rail_fallback(key, profile, live, live_held, inventory)
        # A top-up is chosen for nobody in particular, so a written tile is
        # the better filler by every measure. Only the head is asked about:
        # the fallback is the whole inventory, and one cache read per tile of
        # it on every short rail is a cost nothing on the page would repay.
        fallback = (ready_first(fallback[:READY_REACH], written)
                    + fallback[READY_REACH:])
        extra = _fill_to_minimum(picked[key], floors[key], fallback,
                                 on_page | mine)
        picked[key] = picked[key] + extra
        used |= {t.id for t in extra}
    # **Exactly `SECTION_SIZE` on the page, never more** (§134). Every ranker
    # is asked for this many already; this is the line that makes it a fact
    # about the page rather than a habit of each ranker, so a rail added
    # later cannot show five. "View more" is where the rest lives.
    for key in picked:
        picked[key] = picked[key][:SECTION_SIZE]
    world_reason = ("" if picked["world_trending"]
                    else _world_empty_reason(bool(live)))

    # An empty section is honest, not broken: a new listener genuinely has no
    # history and no friends, and a deployment with no live source genuinely
    # has no stories. The interface says which, rather than padding the row
    # with picks that pretend to be personal or current.
    sections = [
        {
            "key": key,
            "title": title,
            "topics": [t.as_dict() for t in picked[key]],
            "empty_reason": (world_reason if key == "world_trending"
                             else _empty_reason(key) if not picked[key] else ""),
        }
        for key, title in SECTIONS
    ]
    # What ordered the first rail, said out loud. `"taste"` means this
    # listener's own behaviour and interests; `"startup"` means the prior, and
    # `startup_order` then says whether even that was a measurement of what
    # FAM plays or the declared fallback. The interface reads the first to
    # keep a generic rail from being headed "Made for you", and the second
    # distinction exists for the reason `popular_facets` already draws it: a
    # declared order and a measured one look identical on a screen.
    return {"sections": sections, "personalised": bool(profile),
            "live_stories": len(live),
            "taste_source": "startup" if cold else "taste",
            "startup_order": startup_source}


#: How many tiles a full-screen section shows. The bank is ~28 topics, so
#: this is "all of it, in this section's order" rather than a page size -
#: there is no second page to fetch and nothing new to generate to fill one.
FULL_SECTION_SIZE = 40


def build_section(store: EventStore, user_id: str, key: str,
                  now: Optional[float] = None,
                  interests: Iterable[str] = (), circle: Iterable[str] = (),
                  written=None, place: Iterable[str] = (),
                  place_name: str = "", has_account: bool = False,
                  floors: Optional[dict] = None, country: str = "",
                  written_at=None) -> dict:
    """One myFAM section, at full length, in the same order the rail used.

    The rail shows six and the screen behind it shows the rest **of the same
    ranking**. One ranker, two views - the rule Explore New already follows,
    for the same reason: a rail and the surface it opens must not give a
    listener two different answers to one question.

    Nothing here generates anything. It reorders a fixed bank, exactly as
    `build_feed` does, which is what makes "view more" free.

    **Every thumb the rail applies has to be applied here too**, and one of
    them was not: `familiar_words` never reached this function, so a live
    story damped by `BROAD_MATCH_PENALTY` on the rail was undamped on the
    screen behind it. Silent, because both orderings look plausible - which
    is exactly why the one rule above is worth enforcing by construction
    rather than by care. `place` and `place_name` are here from the start for
    the same reason.

    `has_account` is on that list too, and it is the one with teeth: this
    screen and the rail it opens must draw from the same inventory, or "View
    more" would hand a listener the twenty-eight standing explainers the rail
    had just decided they should not be shown. See `browse_inventory`.
    """
    if key not in dict(SECTIONS):
        raise KeyError(key)
    now = time.time() if now is None else now
    events = store.for_user(user_id) if user_id else []
    profile = taste(events, now, interests)
    # **The cold start, decided by measurement rather than by a flag.**
    #
    # An empty `taste` is exactly the listener `startup.py` exists for: no
    # account, no chosen interests, nothing played. Deriving it here rather
    # than reading a "they skipped the intro" preference is the same call this
    # whole module already makes - a taste profile is a query, not a stored
    # object - and it is strictly better than a flag, because a flag cannot
    # say whether behaviour has since arrived. One play, one search or one
    # chosen interest and this is False again for good.
    cold = not profile
    damp = (fatigue(store.impression_occasions(user_id), _played_ids(events))
            if user_id else {})
    # The same two reads the rail does, for the same two thumbs. See the note
    # in the docstring about why their absence here was invisible.
    familiar = familiar_words(events) | frozenset(w for w in place if w)
    place = frozenset(w for w in place if w)
    engage = engagement_for(store, now)
    limit = FULL_SECTION_SIZE
    live = live_topics(now)
    # The same no-repeats pass as the rail, in the same place, or "View more"
    # would offer back the story the rail had just replaced (§136).
    heard = heard_from(store.heard(user_id)) if user_id else None
    live = trending_for(live, heard, now)
    # The rail's inventory, on the rail's rule. See the docstring.
    inventory = browse_inventory(live, has_account)
    # The same no-repeats check as the rail, or "View more" would offer back
    # the episode the rail had just dropped for being heard.
    live_held = trending_for(
        topics_from_stories(stories.pool().held(now), now=now), heard, now)
    local_topic = local_startup_topic(place_name)
    universe = (list(known_topics(now).values()) + list(inventory)
                + list(live_held) + ([local_topic] if local_topic else []))
    mine = repeats(universe, heard, now, written_at) if heard else set()
    # `exclude` is what they have already played, and *not* the other
    # sections' picks. On the page the sections take turns so no tile appears
    # twice; here there is only one section, and hiding its best tiles because
    # a different rail happened to claim them would make "view more" show
    # less.
    if key == "from_history":
        # The cold start is honoured here too, and it has to be: this screen's
        # one rule is that it is the rail's own ranking at full length, so a
        # startup rail whose "View more" ran the ordinary ranker would open on
        # the empty list the rail was built to avoid - two different answers to
        # one question, which is the thing this function exists not to do.
        if cold:
            prior, _order = startup_profile(store, now)
            picks = rank_startup(prior, mine, limit=limit,
                                 candidates=inventory, damp=damp,
                                 familiar=familiar, local=place,
                                 local_topic=local_topic,
                                 engage=engage)
        else:
            # The same two §131 terms the rail reads, or this screen would
            # be a different ranking from the rail that opened it.
            picks = rank_from_history(
                profile, mine, damp, limit=limit, candidates=inventory,
                familiar=familiar, local=place, engage=engage,
                semantic=taste_vectors.for_listener(events, inventory, now),
                learned=learned_rank.active(store, now))
    elif key == "might_like":
        picks = rank_might_like(profile, mine, damp, limit=limit)
    elif key == "missed":
        # Its own ranking. It used to fall through to `rank_most_played`, so
        # "View more" on What you missed opened the crowd row (§136). The
        # same membership the rail uses - offered, played by others, or in
        # the pool - with trending included from the start, because on this
        # screen there is no Made for you for it to leave new stories to.
        shown = (store.impressions_since(user_id, now - MISSED_WINDOW)
                 if user_id else {})
        popular = ({u_topic for u, u_topic
                    in store.plays_since(now - MISSED_WINDOW) if u != user_id}
                   if user_id else set())
        picks = rank_missed(profile, shown, mine, set(),
                            candidates=browse_inventory(live_held, has_account),
                            limit=limit, now=now, popular=popular,
                            familiar=familiar)
    elif key == "followers":
        picks = rank_friends(store, circle, mine, damp, limit=limit, now=now,
                             written=written)
    elif key == "world_trending":
        # The rail's own ranker at full length, and like the rail it takes
        # nothing of the listener but their country - not even what they have
        # played (§134). Grouped by where each story is trending (§135): the
        # screen is the whole of Trending, worldwide and region by region.
        world_live, world_held = world_inventory(live, live_held, heard, now)
        groups = trending_groups(world_live, country, world_held)
        picks = [t for g in groups for t in g["topics"]][:limit]
    else:
        picks = rank_most_played(store, now, mine, limit=limit, written=written)
    # The same variety rule the rail uses, at the same ratio. A screen showing
    # forty tiles can carry more of one subject than a row showing six, and a
    # cap that did not scale would make "view more" a different ranking from
    # the rail it opened - which is the one thing this screen must not be.
    if key != "world_trending":
        picks = diversify(picks, limit,
                          max_per_facet=MAX_PER_FACET * (limit // SECTION_SIZE or 1))
    # "View more" never shows fewer than the rail it opened (§127).
    floors = RAIL_MINIMUM if floors is None else floors
    if floors.get(key):
        picks = picks + _fill_to_minimum(
            picks, floors[key],
            _rail_fallback(key, profile, live, live_held, inventory), mine)
    section = {
        "key": key,
        "title": dict(SECTIONS)[key],
        "topics": [t.as_dict() for t in picks],
        "empty_reason": _empty_reason(key) if not picks else "",
        "personalised": bool(profile),
        "taste_source": "startup" if cold else "taste",
    }
    if key == "world_trending":
        # The same tiles as `topics`, in the same order, under the geography
        # they are trending in. A client that ignores this still gets the
        # whole list; one that reads it draws a heading per place.
        shown = {t.id for t in picks}
        section["groups"] = [
            {"key": g["key"], "label": g["label"], "yours": g["yours"],
             "topics": [t.as_dict() for t in g["topics"] if t.id in shown]}
            for g in groups if any(t.id in shown for t in g["topics"])]
    return section


def topics_from_stories(rows, limit: int = 0, now: Optional[float] = None) -> list:
    """Turn live stories into tiles, loudest first.

    The one conversion between the two inventories, so everything downstream -
    fatigue, impressions, `facets_only`, the "view more" screen, the
    post-episode popup - works on stories without knowing they exist. A second
    tile type would have meant a second branch in each of those, which is
    six places to forget.

    Tags come from the story, which got them from the same keyword map the
    bank uses. `freshness` is `Story.push()` frozen at the moment the feed was
    built: the rankers need it as a number and must not each re-derive it from
    a clock, or two rails on one page could disagree about how hot something
    is.
    """
    now = time.time() if now is None else now
    tiles = []
    for story in rows:
        tags = tuple(story.tags) or tags_for_text(f"{story.subject} {story.query}")
        tiles.append(Topic(
            id=story.id,
            title=story.title,
            # A story's standing description *is* its angle - it has no other -
            # so both carry it. `subtitle` is what every existing surface
            # already reads; `angle` is what says this one is about today.
            subtitle=story.angle,
            query=story.query,
            tags=tags,
            icon=_icon_for_tags(tags),
            angle=story.angle,
            source=story.source,
            freshness=story.push(now),
            countries=tuple(getattr(story, "countries", ()) or ()),
            coverage=int(getattr(story, "coverage", 0) or 0),
            geo_scope=getattr(story, "geo_scope", "") or "",
            geo_key=getattr(story, "geo_key", "") or "",
            geo=getattr(story, "geo", "") or "",
            live_line=getattr(story, "live_line", "") or "",
            live_status=getattr(story, "live_status", "") or "",
            live_as_of=float(getattr(story, "live_as_of", 0.0) or 0.0),
            last_seen=float(getattr(story, "last_seen", 0.0) or 0.0),
        ))
    return tiles[:limit] if limit else tiles


def world_inventory(live: list, live_held: list, heard: Optional["Heard"],
                    now: float) -> tuple:
    """What Trending ranks: the trending bank's edition, else the story pool.

    §137, at the owner's direction: Trending is an edition built twice a day
    from GNews (`trending_bank`), and when there is one it is the whole of
    the row - rail and "View more" alike, through this one function so the
    two cannot disagree. With no edition (no key and no crutch, the first
    build still running, or `TRENDING_BANK=0`) the row reads the live pool
    exactly as it did before, so a deployment without the bank loses nothing.

    `live` and `live_held` are the pool's, already through `trending_for`;
    the bank's tiles go through it here, so a heard story is a follow-up or
    gone on this row as on every other.
    """
    import trending_bank

    bank = trending_bank.stories_now(now)
    if not bank:
        return live, live_held
    return trending_for(topics_from_stories(bank, now=now), heard, now), []


def live_topics(now: Optional[float] = None) -> list:
    """The story pool as tiles. Synchronous, and that is the whole design.

    `build_feed` stays a pure function of the event log plus two caches
    somebody else refreshed, which is what keeps the ranker callable in a test
    with no network - and what keeps the browse page instant, because nothing
    on this path can wait on an upstream. See `stories.py` for why the pool is
    global and refreshed in the background.
    """
    return topics_from_stories(stories.pool().live(now), now=now)


def trending_score(tile, country: str = "", peak_coverage: int = 0) -> float:
    """How trending one tile is, for the listener's country. The Trending order.

    Three things and nothing else (§134, extended by §135):

    * **Push** - `Topic.freshness`, the story's own measured strength
      weighted by domain and decayed by age.
    * **Coverage** - how many outlets are running it, log-scaled against the
      most-covered story on offer (`COVERAGE_WEIGHT`). This is "popularity
      on trending news", and it is measured across every source:
      `stories.corroborate` lends a game or a market the coverage of the
      news story it matches.
    * **Country** - the share of that coverage from the listener's own
      country (`COUNTRY_WEIGHT`). A boost, never a filter.
    """
    import math

    want = stories.normalise_country(country)
    share = next((float(v) for name, v in getattr(tile, "countries", ())
                  if name == want), 0.0) if want else 0.0
    covered = int(getattr(tile, "coverage", 0) or 0)
    heat = (math.log1p(covered) / math.log1p(peak_coverage)
            if covered and peak_coverage else 0.0)
    return (float(tile.freshness) * (1.0 + COVERAGE_WEIGHT * heat)
            * (1.0 + COUNTRY_WEIGHT * share))


def _ranked_trending(tiles: list, country: str, peak: int) -> list:
    # Pool order breaks ties - the pool's own loudest-first order - so a tie
    # never falls through to an id sort, an order nobody chose.
    return [t for _s, _i, t in sorted(
        ((-trending_score(t, country, peak), i, t) for i, t in enumerate(tiles)),
        key=lambda row: row[:2])]


#: How long a story has to have kept being reported after a listener heard
#: it before a follow-up is offered. Six hours: less and "what's new" is the
#: same wire copy re-filed; a story still running half a day later has moved.
FOLLOWUP_AFTER = 6 * 3600.0


def followup_for(tile: Topic, since: float) -> Topic:
    """The same story, asked again for what has happened since `since`.

    A different question, so a different episode and a different cache key -
    and researched on the tap like every live tile, so the new information is
    retrieved rather than assumed. The date is in the question because it is
    what the writer needs to know to skip what the listener already heard; it
    also means everybody who heard the story on the same day shares one
    follow-up script. Every other field is the story's, so it ranks and is
    placed exactly where the story would have been.
    """
    when = time.strftime("%B %d", time.gmtime(since)).replace(" 0", " ")
    subject = tile.title.rstrip(" ?.!")
    return replace(
        tile,
        id=f"{tile.id}-new"[:64],
        query=f"What's new with {subject} since {when}?",
        angle="What has changed since you last listened",
        subtitle="What has changed since you last listened",
        follows=tile.id,
    )


def trending_for(tiles: Iterable[Topic], heard: Optional[Heard],
                 now: float) -> list:
    """The trending inventory with this listener's heard stories dealt with.

    Every tile comes back as one of three things, in its own position:

    * **itself**, if they have not heard it;
    * **a follow-up** (`followup_for`) if they have, and the story has kept
      being reported for `FOLLOWUP_AFTER` since - directly related, with new
      information;
    * **nothing**, otherwise - so the next trending story moves up into its
      place in `rank_world`.

    A heard follow-up counts as hearing the story, so the next follow-up is
    dated from it and needs fresh coverage after *that*. This is the one
    input Trending takes from a listener's history, and it decides only
    whether an episode would be a repeat: the order is still popularity and
    country alone (§134).
    """
    tiles = list(tiles)
    if heard is None:
        return tiles
    out = []
    for tile in tiles:
        last = max(heard.last(tile), heard.by_id.get(f"{tile.id}-new"[:64], 0.0))
        if not last:
            out.append(tile)
        elif (tile.freshness > 0 and tile.last_seen - last >= FOLLOWUP_AFTER):
            out.append(followup_for(tile, last))
    return out


def rank_world(live: list, country: str = "", held: Iterable = (),
               limit: int = SECTION_SIZE) -> list:
    """The Trending rail: the world's loudest stories, and nothing about you.

    §134, at the owner's direction, and the rule is narrow on purpose:
    **popularity, and the listener's country, and nothing else.** Not their
    taste, not their interests, not what they have played, not fatigue, not
    engagement, not the learned order - every one of those is "the user's
    algorithm", and this row is the one on the page that is not theirs.

    * **Popularity** is `trending_score` - push, times how widely the press
      is running it (§135), times the share of that from their country.
    * **Geography** (§135): up to `WORLD_LOCAL_SLOTS` places go to what is
      trending in the listener's own part of the world
      (`geography.matches`), and the rest to the most popular stories
      anywhere. Then the row is shown in popularity order, each card saying
      where it is trending.
    * **Variety** is one tile per facet (`WORLD_MAX_PER_FACET`), topped back
      up from what the cap passed over when the pool is all one subject.

    `held` is what the pool's own variety cap is hiding - live, composed and
    free - and is used only when `live` cannot fill the row.

    **Never the startup set or the evergreen bank.** "The trending section
    of myFAM should never show the dummy data episodes" - both are
    inventories FAM wrote, not stories the world is reading, so a pool that
    cannot fill four leaves the row short, and an empty pool leaves it empty
    with a sentence saying why (`_world_empty_reason`).
    """
    import geography

    live = list(live)
    held = [t for t in held if t.id not in {x.id for x in live}]
    peak = max((int(getattr(t, "coverage", 0) or 0) for t in live + held),
               default=0)
    order = _ranked_trending(live, country, peak)

    local_slots = min(WORLD_LOCAL_SLOTS, limit // 2) if country else 0
    mine = [t for t in order
            if geography.matches(getattr(t, "geo_scope", ""),
                                 getattr(t, "geo_key", ""), country)]
    rows = diversify(mine, min(local_slots, len(mine)),
                     max_per_facet=WORLD_MAX_PER_FACET) if local_slots and mine else []
    taken = {t.id for t in rows}
    rows += diversify([t for t in order if t.id not in taken],
                      limit - len(rows), max_per_facet=WORLD_MAX_PER_FACET)
    # Shown most popular first, wherever it is trending - the local places
    # are a reservation, not a position at the front.
    position = {t.id: i for i, t in enumerate(order)}
    rows.sort(key=lambda t: position[t.id])
    # **Live first, and held only to fill** - what the docstring promises.
    # The pool's variety cap is what keeps a story out of `live`, so letting a
    # held one outrank a live one would undo that cap on the one row that
    # reads the pool most directly.
    if len(rows) < limit:
        have = {t.id for t in rows}
        rows += [t for t in _ranked_trending(held, country, peak)
                 if t.id not in have][:limit - len(rows)]
    return rows


def trending_groups(live: list, country: str = "", held: Iterable = (),
                    limit: int = 0) -> list:
    """Every trending story, by where it is trending. "View more" (§135).

    `[{"key", "label", "topics"}]`: **Worldwide** first, then the listener's
    own region, then every other region with something trending, busiest
    first. Within a group, `trending_score` order. A country-scoped story is
    grouped under its region and keeps its own label on the card, so a
    listener in Ohio sees "United States" stories under North America.
    """
    import geography

    live = list(live)
    everything = live + [t for t in held if t.id not in {x.id for x in live}]
    peak = max((int(getattr(t, "coverage", 0) or 0) for t in everything),
               default=0)
    groups: dict = {}
    for tile in _ranked_trending(everything, country, peak):
        scope = getattr(tile, "geo_scope", "") or geography.WORLD
        key = getattr(tile, "geo_key", "") or geography.WORLD
        if scope == geography.WORLD:
            group = geography.WORLD
        elif scope == "country":
            group = geography.REGION_OF.get(key, geography.WORLD)
        else:
            group = key
        groups.setdefault(group, []).append(tile)

    mine = geography.region_of(country) if country else ""

    def order(key: str) -> tuple:
        if key == geography.WORLD:
            return (0, 0.0, key)
        if key == mine:
            return (1, 0.0, key)
        busy = sum(trending_score(t, country, peak) for t in groups[key])
        return (2, -busy, key)

    out = []
    for key in sorted(groups, key=order):
        tiles = groups[key][:limit] if limit else groups[key]
        out.append({"key": key, "label": geography.label_for(key),
                    "yours": key == mine and key != geography.WORLD,
                    "topics": tiles})
    return out


def _rail_fallback(key: str, profile: dict, live: list, live_held: list,
                   inventory: list) -> list:
    """What a rail is topped up from when its own ranking came up short.

    Ordered, best source first, and every source is a real tile that plays a
    real episode - never a placeholder.

    * **Trending**: the live pool, then what the pool's variety cap is
      holding, and nothing else. It used to fall through to the startup set
      and the bank; the owner has ruled both off that row (§134).
    * **Made for you** and **What you missed**: this listener's own
      inventory in affinity order, then the rest of the tiles FAM has.
    * **What FAM can't stop listening to** is no longer topped up at all
      (§134) - a tile nobody played under a heading that says it was played
      is making one up.

    The bank is always last, and for an account it is the one place it can
    now appear on a rail that chooses (`browse_inventory`): the owner's
    minimum outranks §125's rule on the day the other inventories run out.
    """
    def by_affinity(topics: list) -> list:
        if not profile:
            return list(topics)
        scored = [(_affinity(t, profile), i, t) for i, t in enumerate(topics)]
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [t for _s, _i, t in scored]

    if key == "world_trending":
        # Live stories only, and never the startup set or the bank (§134):
        # "the trending section of myFAM should never show the dummy data
        # episodes". Nothing calls this for Trending today - it is not in
        # `RAIL_MINIMUM` - and this answer is what makes that safe to change.
        return list(live) + list(live_held)
    return (by_affinity(list(inventory))
            + by_affinity(list(STARTUP_TOPICS) + list(TOPIC_BANK)))


def _fill_to_minimum(picked: list, minimum: int, candidates: list,
                     exclude: set) -> list:
    """The tiles to append so `picked` reaches `minimum`, and no more.

    Never a tile already on the rail, already on another rail (`exclude`
    carries those) or already played. Returns only the additions, so the
    caller keeps the rail's own picks exactly where the ranking put them.
    """
    need = minimum - len(picked)
    if need <= 0:
        return []
    have = {t.id for t in picked} | set(exclude)
    out: list = []
    for topic in candidates:
        if topic.id in have:
            continue
        have.add(topic.id)
        out.append(topic)
        if len(out) >= need:
            break
    return out


def diversify(topics: list, limit: int = SECTION_SIZE,
              max_per_facet: int = MAX_PER_FACET) -> list:
    """Cap how much of one rail one subject may have. The variety rule.

    Facets rather than tags, because the thing a listener notices is four
    tiles about sport and not four tiles that happen to share
    `sports-business`. Order is otherwise preserved exactly, so this is a
    filter on a ranking and never a re-ranking: the best tile is always still
    first.

    It tops up rather than returning short. If capping leaves fewer than
    `limit`, the tiles it passed over come back in their original order -
    a half-empty rail is a worse outcome than a slightly samey one, and the
    listener reads the first one as broken.
    """
    kept: list = []
    spare: list = []
    counts: dict[str, int] = {}
    for topic in topics:
        # **`topic.tags` and deliberately not `topic_tags`.** This caps how
        # many tiles of one *heading* a rail shows, and `facet_of` returns an
        # unknown tag unchanged - so a grown category would come through as a
        # facet of its own, every tile would land in a bucket nobody else is
        # in, and the cap would silently stop binding. The join belongs where
        # a tile is scored against a listener; this is a rule about the shape
        # of the row and the eight headings are the right vocabulary for it.
        facets = {facet_of(tag) for tag in topic.tags} or {"other"}
        if any(counts.get(f, 0) >= max_per_facet for f in facets):
            spare.append(topic)
            continue
        for facet in facets:
            counts[facet] = counts.get(facet, 0) + 1
        kept.append(topic)
        if len(kept) >= limit:
            return kept
    return (kept + spare)[:limit]


def topics_from_trending(items, limit: int = SECTION_SIZE) -> list:
    """Turn world-trending subjects into tiles.

    Tags are derived from the question with the same keyword map the bank
    uses, so a trending tile is legible to the rest of the feed's machinery -
    fatigue, impressions, `facets_only` - without needing a second vocabulary.

    The subtitle is the source's `why_now`. It is shown and never spoken, and
    nothing downstream treats it as evidence: tapping the tile runs the
    ordinary pipeline, which researches the question from scratch. That is
    what stops a stale blurb becoming a stale episode.
    """
    tiles = []
    for item in items:
        tags = tuple(item.tags) or tags_for_text(f"{item.subject} {item.query}")
        tiles.append(Topic(
            id=item.id,
            title=item.subject[:1].upper() + item.subject[1:],
            subtitle=item.why_now,
            query=item.query,
            tags=tags,
            icon=_icon_for_tags(tags),
        ))
    return tiles[:limit]


def tags_for_id(topic_id: str, text: str = "") -> tuple[str, ...]:
    """The tags to log against an interaction with `topic_id`.

    Four places to look, in order: the evergreen bank, the cold-start
    startup set, the first-run catalogue, the live story pool, and - failing
    all of them - the words of whatever was asked. The middle one is
    why this function exists: a story tile's tags live in the pool and nowhere
    else, and an event logged without them teaches the taste model nothing
    about the tap it just recorded.

    A story that has expired since the tap is the case `text` covers. It is a
    keyword sweep over the question rather than the story's own tags, which is
    a worse answer and still much better than none.
    """
    if topic_id in BANK_BY_ID:
        return BANK_BY_ID[topic_id].tags
    # The cold-start set. It matters more here than its size suggests: these
    # eight are the first thing a new listener is offered, so a play on one is
    # the *first real thing the ranker ever learns* - and their queries are
    # written for a research backend rather than for `tags_for_text`. "The
    # most consequential world news story of the past week" contains not one
    # of the keywords `world` is matched on, so falling through to the text
    # would log the opening episode of somebody's history with no tags at all,
    # and the set that exists to bootstrap a taste model would teach it
    # nothing.
    if topic_id in STARTUP_BY_ID:
        return STARTUP_BY_ID[topic_id].tags
    # The local question, whose id is fixed and whose text is not. Resolved
    # from the template rather than from the words, which name a city and
    # match nothing in `TAG_WORDS`. See `LOCAL_STARTUP`.
    if topic_id == LOCAL_STARTUP.id:
        return LOCAL_STARTUP.tags
    if topic_id in CATALOGUE_BY_ID:
        return CATALOGUE_BY_ID[topic_id].tags
    for story in stories.pool().live():
        if story.id == topic_id:
            return tuple(story.tags)
    return tags_for_text(text) if text else ()


def _icon_for_tags(tags) -> str:
    """Reuse the bank's icon vocabulary rather than inventing one."""
    for tag in tags:
        for topic in TOPIC_BANK:
            if tag in topic.tags:
                return topic.icon
    return "world"


def summary(store: EventStore, user_id: str, now: Optional[float] = None) -> dict:
    """What this app actually knows about a listener.

    Deliberately only what the event log really holds. A profile page is the
    easiest place in an app to invent numbers - followers, streaks, hours
    saved - and every invented one is a promise the product has to keep later.
    """
    events = store.for_user(user_id, limit=1000)
    profile = taste(events, now)
    top = sorted(profile.items(), key=lambda kv: -kv[1])
    return {
        "listener": user_id,
        "played": sum(1 for e in events if e.kind == "play"),
        "finished": sum(1 for e in events if e.kind == "complete"),
        "searched": sum(1 for e in events if e.kind == "search"),
        "open_threads": len(store.open_threads(user_id)),
        # Only tags they are actually positive about; a skip pushes a tag
        # negative and it has no business on a list of what someone likes.
        "subjects": facets_only(tag for tag, weight in top if weight > 0)[:5],
        "since": min((e.at for e in events), default=0.0),
    }


#: How many interests a profile draws. Mirrors `preferences.PROFILE_INTERESTS_MAX`
#: and is not imported from it, because `topics` is the module `preferences`
#: imports and not the other way round. The two are pinned together by a test
#: rather than by an import, which is the same trade `pipeline.key_for` makes.
PROFILE_INTEREST_SLOTS = 5


def _interest_score(tags: Iterable[str], profile: dict[str, float]) -> float:
    """How much this listener is into one interest, from their taste profile.

    The same shape as `_affinity` and for the same reason - a sum over tags,
    damped by how many there are, so an interest carrying five tags does not
    outrank a sharper one simply by covering more ground.
    """
    tags = tuple(tags)
    if not tags:
        return 0.0
    return sum(profile.get(tag, 0.0) for tag in tags) / math.sqrt(len(tags))


def ranked_interests(
    store: "EventStore", user_id: str, chosen: Iterable[str] = (),
    chosen_topics: Iterable[str] = (), now: Optional[float] = None,
) -> list[dict]:
    """Everything this listener could put on their profile, best match first.

    **The ordering is the feature.** A profile used to draw what somebody
    chose in the first run, then whatever the log had inferred, in that fixed
    order - so an interest declared in thirty seconds on day one outranked a
    month of listening forever. This is ranked by `taste`, which is the same
    profile every rail on myFAM is built from, so it moves as they listen and
    the top of it is the most current thing this app knows about them.

    Declared interests are not thrown away by that: `taste` already folds them
    in at `INTEREST_WEIGHT` before it normalises, which is exactly a starting
    position that real behaviour then outvotes. So a new listener's profile
    shows what they chose, and the same listener's profile a month later shows
    what they listen to, with no switch between the two.

    Two vocabularies come back in one list, because they are one list on the
    screen: the eight facets, and the named subjects from the catalogue or
    typed into its search. `kind` says which, for anything that needs to tell
    them apart; nothing a listener reads does.

    A facet with no positive weight is left out - it is not an interest, it is
    a word this listener has never been near - but anything they *chose* stays
    in whatever it scores, because that is a statement they made and it is not
    this function's business to overrule it.
    """
    now = time.time() if now is None else now
    events = store.for_user(user_id) if user_id else []
    chosen = [t for t in (chosen or ()) if t in TAG_LABELS]
    profile = taste(events, now, chosen)
    rows: list[tuple[float, int, str, dict]] = []
    seen: set[str] = set()

    declared = set(chosen)
    for tag, label in TAG_LABELS.items():
        score = _interest_score(_facet_and_children(tag), profile)
        if score <= 0 and tag not in declared:
            continue
        seen.add(tag)
        rows.append((score, 0, label.lower(),
                     {"id": tag, "label": label, "kind": "facet"}))

    for topic_id in chosen_topics or ():
        if topic_id in seen:
            continue
        seen.add(topic_id)
        entry = CATALOGUE_BY_ID.get(topic_id)
        label = entry.label if entry else topic_id
        score = _interest_score(tags_for_id(topic_id, label), profile)
        # A named subject is a sharper statement than the facet above it -
        # "Formula 1" says more than "Sport" - so on a tie it goes first.
        # Without this a profile with one chosen subject and eight facets
        # around it would draw four facets and never the subject.
        rows.append((score, -1, label.lower(),
                     {"id": topic_id, "label": label, "kind": "topic"}))

    rows.sort(key=lambda row: (-row[0], row[1], row[2]))
    return [row[3] for row in rows]


def _facet_and_children(facet: str) -> tuple[str, ...]:
    """A facet, with every subtag that lives under it.

    A listener whose entire history is chips has weight on `chips` and on
    `tech`, and one whose history is spread thinly across four tech subjects
    has it spread across four subtags. Scoring the facet alone would call the
    second listener less interested in technology than the first, which is
    backwards. Scoring the family answers "how much of what they play lands
    under this heading", which is the question a profile pill is asking.
    """
    return (facet,) + tuple(t for t, parent in TAG_PARENT.items() if parent == facet)


def profile_interests(
    ranked: list[dict], pinned: Iterable[str] = (), hidden: Iterable[str] = (),
    limit: int = PROFILE_INTEREST_SLOTS,
) -> tuple[list[dict], str]:
    """The three or four pills a profile actually draws, and who decided them.

    `pinned` wins outright when there is one: a listener who opened the editor
    said what they wanted there, and a list that kept re-sorting itself
    underneath them would make the editor look broken. Anything pinned that
    is no longer in `ranked` is still drawn - they chose it, and dropping it
    because the ranker has since lost interest would be the app editing
    somebody's profile.

    With nothing pinned this is the top of `ranked`, minus anything hidden.
    `hidden` is the older statement of the same kind - "not this one, on my
    profile" - and it is still honoured, because a listener who turned an
    interest off before this screen existed did not ask for it back.

    `source` is `"pinned"` or `"top"`, and it is returned rather than inferred
    because those two look identical on screen and a line of copy that says
    "your top interests, kept up to date" is a lie on the first one.
    """
    pinned = [p for p in (pinned or ()) if p]
    by_id = {row["id"]: row for row in ranked}
    if pinned:
        return ([by_id.get(pid) or {"id": pid,
                                    "label": (CATALOGUE_BY_ID[pid].label
                                              if pid in CATALOGUE_BY_ID
                                              else TAG_LABELS.get(pid, pid)),
                                    "kind": "topic"}
                 for pid in pinned][:limit], "pinned")
    hidden = set(hidden or ())
    return ([row for row in ranked if row["id"] not in hidden][:limit], "top")


def _world_empty_reason(pool_had_stories: bool) -> str:
    """What the Trending rail says when it has nothing in it.

    Two different facts, and they must not share a sentence. With an empty
    pool this is about the deployment - which source is missing, or which one
    failed - and `stories.Pool.empty_reason` knows which. With a full pool it
    is about this page: the rail above got there first, and saying the feed
    had nothing would be describing our own ordering as the world's silence.
    """
    # Since §134 the first clause cannot happen - Trending chooses before any
    # personal rail, so a pool with stories in it always puts them here. It
    # stays because the sentence is still the right one if that order ever
    # changes, and a wrong one is §89.
    if pool_had_stories:
        return "Everything the world is on today is already in Made for you."
    return _trending_empty_reason()


def _trending_empty_reason() -> str:
    """The bank's sentence when it is on, else the pool's (§137)."""
    import trending_bank
    from config import settings

    if settings.trending_bank:
        return trending_bank.empty_reason()
    return stories.pool().empty_reason


def _empty_reason(key: str) -> str:
    return {
        # Reachable now that this row has no filler behind it, and worded for
        # the only state it means: nobody has played anything in FAM's
        # trending window. Not "today" - the window is three days - and not
        # "nothing is popular", which would be a claim about listeners this
        # deployment has not got.
        "most_played": "Nothing has been played here yet. This fills up as "
                       "people listen.",
        # Deliberately not "nothing is trending". An empty row here is a fact
        # about this deployment, never a claim about the world - the browse
        # surface's version of PROBLEMS.md §89. The live text comes from
        # `stories.Pool.empty_reason`, which knows *which* way it came up
        # empty; this is the fallback when nothing has been asked yet.
        "world_trending": _trending_empty_reason(),
        # Names the thing to do about it. The rail is empty for exactly one
        # reason - they follow nobody - and a row that said "nobody has
        # listened yet" would be blaming the app for a state the listener can
        # fix in two taps.
        "followers": "Follow some people and this fills up with what they play.",
        "from_history": "Your first episode starts this one off.",
        # Three different nothings now that membership is wider, and the
        # rail can tell none of them apart from here: nothing went past this
        # listener, or everything that did was played, or nothing that did
        # was relevant enough to offer. The sentence has to be true of all
        # three, so it claims none of them.
        "missed": "Nothing went past you this week.",
        # Never actually empty in practice - with no profile at all this falls
        # back to the whole bank - but a reason has to exist for the day the
        # bank is smaller than the sections that draw from it.
        "might_like": "Listen to a few episodes and this fills in.",
    }.get(key, "")


#: How many tiles the post-episode popup shows. Four, because the design is a
#: 2x2 grid and the first one starts itself.
NEXT_UP_SIZE = 4

#: What the episode they *just heard* is worth when choosing the next one.
#: Above a completion, and deliberately so: "what should follow this" is a
#: question about this episode first and their history second. It is a seed on
#: the same profile the shelves are ranked from, not a separate scorer - the
#: popup is meant to be the feed's opinion, arrived at one tap earlier.
JUST_HEARD_WEIGHT = 3.0


def _seeded(profile: dict[str, float], tags: Iterable[str],
            weight: float) -> dict[str, float]:
    """`profile` with `tags` pushed up, renormalised the way `taste` leaves it."""
    scores = dict(profile)
    for tag in tags:
        if tag in TAG_LABELS:
            scores[tag] = scores.get(tag, 0.0) + weight
    peak = max((abs(v) for v in scores.values()), default=0.0)
    return {k: v / peak for k, v in scores.items()} if peak else {}


def tags_for_episode(topic_id: str = "", text: str = "") -> tuple[str, ...]:
    """Facets for an episode, from the bank if it is a tile and the words if not.

    Search episodes have no topic id at all, which is most of what gets played
    on the search surface - so falling back to the keyword map is not an edge
    case here, it is the common path.
    """
    if topic_id and topic_id in BANK_BY_ID:
        return BANK_BY_ID[topic_id].tags
    return tags_for_text(text) if text else ()


def rank_next_up(
    store: EventStore,
    user_id: str,
    now: Optional[float] = None,
    after_id: str = "",
    after_text: str = "",
    interests: Iterable[str] = (),
    size: int = NEXT_UP_SIZE,
    has_account: bool = False,
) -> list[Topic]:
    """The four episodes to offer when one finishes.

    Not a new recommender. It is `build_feed`'s three signals, in the same
    order of preference, over a profile seeded with what they just heard:
    closest-to-your-taste first, then what co-listeners went on to play, then
    the crowd. A second, disconnected implementation of "what next" would drift
    from the shelves within a release and give a listener two different answers
    to the same question on two screens.

    Everything they have already played is excluded, along with the episode
    that just ended. Offering back the thing they are still listening to the
    end of is the one recommendation guaranteed to be wrong.

    **It draws on both inventories, like Made for you.** It used to be the
    bank alone, which made the popup a quietly worse recommender than the rail
    it is meant to be the feed's opinion of: somebody who had just heard an
    episode about today's news was offered four standing explainers, because
    the one place today's stories live was not in its candidate list. Same
    call to `live_topics`, same `FRESHNESS_BOOST`, same single score over both.

    **"Both inventories" now means whichever two this listener is allowed**,
    on `browse_inventory`'s rule - which this reads rather than restating,
    including on the last-resort pass below. A popup is a browse surface with
    a smaller grid, and a rule the shelves keep and the popup does not is a
    back door into exactly the surface a listener looks at hardest: the one
    that starts playing by itself in fifteen seconds.
    """
    now = time.time() if now is None else now
    events = store.for_user(user_id) if user_id else []
    profile = _seeded(
        taste(events, now, interests),
        tags_for_episode(after_id, after_text),
        JUST_HEARD_WEIGHT,
    )
    mine = _played_ids(events)
    damp = fatigue(store.impression_occasions(user_id), mine) if user_id else {}
    exclude = set(mine) | ({after_id} if after_id else set())

    picks: list[Topic] = []
    taken = set(exclude)

    def add(candidates: list[Topic]) -> None:
        for topic in candidates:
            if len(picks) >= size:
                return
            if topic.id not in taken:
                picks.append(topic)
                taken.add(topic.id)

    inventory = browse_inventory(live_topics(now), has_account)
    # The episode that just ended leads the semantic history, the way it is
    # seeded into the tag profile above at `JUST_HEARD_WEIGHT`.
    heard = ([Event(user_id, "complete", after_id, after_text, at=now)]
             if (after_id or after_text) else [])
    add(rank_from_history(
        profile, taken, damp, candidates=inventory,
        semantic=taste_vectors.for_listener(heard + events, inventory, now),
        learned=learned_rank.active(store, now)))
    if len(picks) < size:
        add(rank_followers(store, user_id, mine, taken, damp))
    if len(picks) < size:
        add(rank_most_played(store, now, taken))
    # A listener who has played most of the inventory would otherwise get a
    # short grid. Four tiles is the layout, so the last resort drops the "not
    # already played" rule rather than the shape - re-hearing something is a
    # far smaller disappointment than two empty squares. `taken` is rebuilt
    # from what is actually on the grid, because it still carries the
    # played-ids exclusion at this point and reusing it would filter out the
    # very topics this fallback exists to reach.
    #
    # **It drops the played rule and not the inventory rule.** Reaching for
    # the bank here would fill an account holder's grid with the standing
    # explainers every other surface has stopped offering them, and it would
    # do it on the one surface that plays its first tile without being asked.
    # A grid of three is the honest shortfall; a fourth tile from an
    # inventory this listener is not shown is not.
    if len(picks) < size:
        taken = {t.id for t in picks} | ({after_id} if after_id else set())
        add(inventory)
    return picks[:size]


def build_explore_new(store: EventStore, user_id: str, now: Optional[float] = None,
                      interests: Iterable[str] = ()) -> dict:
    """The Explore New surface: adjacent to a taste, deliberately not inside it.

    `rank_might_like` has existed and been tested since myFAM was built, and
    has been shown nowhere since its shelf came off that page. It is the one
    ranking here that widens a taste rather than confirming it, so what it
    needed was a home rather than a rewrite.

    Returned with `reason` rather than bare tiles, because a shelf of things
    you have not asked for is only a good idea if it says why it is there.
    """
    events = store.for_user(user_id) if user_id else []
    profile = taste(events, now, interests)
    mine = _played_ids(events)
    damp = fatigue(store.impression_occasions(user_id), mine) if user_id else {}
    picks = rank_might_like(profile, mine, damp)
    return {
        "topics": [t.as_dict() for t in picks],
        "personalised": bool(profile),
        "reason": ("Next to what you already listen to, rather than more of it."
                   if profile else
                   "A spread across the whole bank, until there is something to "
                   "be next to."),
    }
