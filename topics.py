"""myFAM: two shared inventories, plus per-user ranking over them.

The cost argument decides the shape of this module. Generating an episode
costs a model call; ranking a list costs nothing. So **every user sees the
same inventory and a different ordering of it**. Two people who tap the same
tile share one script through `cache.py`, and the second tap is free and
instant. A per-user *inventory* would mean a per-user script for every tile,
which is the same product at many times the price.

There are two of them, and they answer different halves of "what should I
hear":

    TOPIC_BANK        ~28 evergreen topics, written by hand, always true
    stories.pool()    live candidates built from today's data, and expiring

The bank is what a browse page has when nothing has happened; the pool is what
it has when something has. Neither is a script - a tile is a title, an angle
and a question, and the writing happens on the tap. See `stories.py`.

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
from dataclasses import dataclass, field
from typing import Iterable, Optional

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
SECTION_SIZE = 6

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
MISSED_SECTION_SIZE = 8

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
WORLD_FLOOR = 4

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


def tags_for_text(text: str) -> tuple[str, ...]:
    """Best-effort facets and subtags for a free search, so history can rank.

    A matched subtag brings its facet with it. That is what keeps this change
    additive: "how are chips actually made" now carries `chips`, but it still
    carries `tech`, so it counts for a listener who only ever said Technology
    exactly as much as it did before subtags existed. The reverse is not true
    and should not be - matching the facet says nothing about which part of it
    was meant.

    Returned sorted for a stable order. These tuples are written into the
    event log and compared in tests, and dict iteration order is a poor thing
    to have quietly load-bearing underneath either.
    """
    words = set(_WORD.findall(text.lower()))
    found = {tag for tag, keys in TAG_WORDS.items() if words & set(keys)}
    found |= {TAG_PARENT[tag] for tag in found if tag in TAG_PARENT}
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


def _is_broad_match(topic: Topic, profile: dict[str, float]) -> bool:
    """True when nothing specific about this tile matches this listener.

    "Specific" means a subtag: the whole point of `TAG_PARENT` is that it
    holds the tags nobody could have been *offered* and everybody's behaviour
    still reveals. A tile whose only positive tags are facets is matching the
    heading and not the thing.
    """
    return not any(profile.get(tag, 0.0) > 0 for tag in topic.tags
                   if tag in TAG_PARENT)


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
          "NIL money, facilities, and the new power brokers.",
          "how NIL money changed college football recruiting", ("sports", "money", "sports-business"), "sports"),
    Topic("operator-ceos", "Why Founders Are Taking Back Control",
          "Leadership, product, and the rise of operator CEOs.",
          "why boards are keeping founders as CEO", ("business", "founders"), "business"),
    Topic("ai-agents", "Why Everyone Is Talking About AI Agents",
          "What they are, how they work, why now.",
          "what AI agents are and why they matter now", ("tech", "ai"), "tech"),
    Topic("hollywood-comebacks", "Inside the Best Hollywood Comebacks",
          "The stories, the risks, the second acts.",
          "how Hollywood comeback stories actually happen", ("culture", "film-tv"), "camera"),
    Topic("golf-evolution", "The Quiet Evolution of Golf",
          "New players. New formats. Same obsession.",
          "how professional golf formats are changing", ("sports", "sports-performance"), "golf"),
    Topic("habits-research", "The Habits That Actually Change Your Life",
          "What the research says, and what people ignore.",
          "what habit research actually shows about lasting change", ("health", "habits"), "leaf"),
    Topic("fed-next-move", "The Fed's Next Move, Explained",
          "Rates, inflation data, and what markets expect.",
          "what the Federal Reserve is likely to do about interest rates",
          ("money", "macro"), "business"),
    Topic("space-race", "Inside the New Space Race",
          "Reusable rockets, private missions, who's winning.",
          "how reusable rockets changed the economics of spaceflight",
          ("science", "business", "space"), "rocket"),
    Topic("song-breaks-internet", "How One Song Breaks the Internet",
          "Playlists, algorithms, and the new path to a hit.",
          "how a song becomes a hit through playlists and short video",
          ("culture", "tech", "music", "platforms"), "music"),
    Topic("restaurant-scene", "The Restaurants Everyone's Talking About",
          "Openings, closings, and where the lines form.",
          "why some restaurants become impossible to book", ("culture", "food"), "food"),
    Topic("the-trade", "The Trade That Changed Everything",
          "Front offices, cap space, and deals nobody saw.",
          "how a single trade reshapes a sports franchise", ("sports", "sports-drama"), "sports"),
    Topic("sleep-science", "What We Actually Know About Sleep",
          "The research, the myths, what isn't settled.",
          "what sleep research actually establishes", ("health", "science", "sleep", "body-science"), "leaf"),
    Topic("chip-supply", "Who Actually Makes the World's Chips",
          "Fabs, bottlenecks, and why it is so concentrated.",
          "why semiconductor manufacturing is concentrated in so few places",
          ("tech", "world", "chips", "geopolitics"), "tech"),
    Topic("hormuz", "The Two-Mile Lane That Moves the Oil Price",
          "Chokepoints, insurance, and why geography decides.",
          "why the Strait of Hormuz moves the oil price", ("world", "money", "geopolitics", "commodities"), "business"),
    Topic("morning-mindset", "What to Do With the First Ten Minutes",
          "Why waking up feels the way it does.",
          "a good mindset for when I wake up in the morning", ("health", "habits", "mind"), "leaf"),
    Topic("founder-motivation", "Where Motivation Actually Comes From",
          "Progress, evidence, and the founder's problem.",
          "finding the motivation for my startup", ("health", "business", "mind", "founders"), "leaf"),
    Topic("housing-market", "Why Houses Cost What They Cost",
          "Supply, rates, and the arguments that repeat.",
          "what actually drives house prices", ("money", "housing"), "business"),
    Topic("longevity-claims", "Sorting the Longevity Claims",
          "What holds up, what is marketing.",
          "which longevity interventions have real evidence", ("health", "science", "longevity", "body-science"), "leaf"),
    Topic("streaming-economics", "Why Streaming Keeps Getting Worse",
          "Licensing, churn, and the maths underneath.",
          "why streaming services keep raising prices and losing shows",
          ("culture", "business", "film-tv", "media-business"), "camera"),
    Topic("election-mechanics", "How a Close Election Is Actually Called",
          "Counting, models, and why it takes days.",
          "how news organisations decide to call an election", ("world", "elections"), "business"),
    Topic("energy-grid", "What the Grid Does When the Wind Drops",
          "Storage, baseload, and the balancing act.",
          "how electricity grids handle intermittent renewable power",
          ("science", "money", "energy", "commodities"), "rocket"),
    Topic("attention-economy", "The Fight for Fifteen Seconds",
          "How short video rewired everything downstream.",
          "how short-form video changed the media business", ("tech", "culture", "platforms", "internet-culture"), "music"),
    Topic("transfer-window", "How a Transfer Window Actually Works",
          "Agents, deadlines, and the money underneath.",
          "how football transfer deals actually get done", ("sports", "money", "sports-business"), "sports"),
    Topic("stadium-money", "Who Really Pays for a Stadium",
          "Public money, private returns, and the argument.",
          "who actually pays for new sports stadiums", ("sports", "money", "world", "sports-business", "cities"), "business"),
    Topic("anxiety-loop", "Why Worry Feels Productive",
          "The loop, and what actually interrupts it.",
          "why worrying feels useful when it is not", ("health", "mind"), "leaf"),
    Topic("pricing-psychology", "Why Everything Ends in Ninety-Nine",
          "What the pricing research does and does not show.",
          "what the evidence says about psychological pricing", ("business", "money", "strategy", "consumer-prices"), "business"),
    Topic("food-supply", "How Food Gets to a City",
          "Logistics, margins, and the fragile bits.",
          "how a city's food supply chain actually works", ("world", "business", "supply-chain", "cities"), "food"),
    Topic("training-load", "How Athletes Are Actually Trained Now",
          "Load, recovery, and the data behind it.",
          "how modern athletic training load is managed", ("sports", "health", "sports-performance", "fitness"), "sports"),
)

BANK_BY_ID = {t.id: t for t in TOPIC_BANK}

#: Sections are FILLED in this order and DISPLAYED in SECTIONS order. The most
#: constrained sections choose first; trending can fall back to the whole bank
#: and therefore chooses last.
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
#: `world_trending` is filled outside this loop - see `build_feed`, which
#: reserves `WORLD_FLOOR` tiles for it before any of these choose.
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
    # evergreen bank. The one rail that is allowed both inventories, because
    # it is the one whose question is "what would *you* want", and the answer
    # to that is sometimes today's news and sometimes a standing explainer.
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
EVENT_WEIGHT = {"search": 1.0, "play": 1.0, "complete": 2.5, "skip": -1.5,
                "pick": 1.6}

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
ALGO_VERSION = "2026-09-14.1"

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
        self._pruned_at = 0.0

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
        algo: str = ALGO_VERSION,
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
            return cur.rowcount or 0
        except Exception:
            log.exception("could not clear the event log")
            return 0

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
    for event in events:
        weight = EVENT_WEIGHT.get(event.kind, 0.0) * _decay(max(0.0, now - event.at))
        tags = event.tags or (tags_for_text(event.text) if event.text else ())
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
    """
    if not topic.tags:
        return 0.0
    total = sum(profile.get(tag, 0.0) * (SUBTAG_WEIGHT if tag in TAG_PARENT else 1.0)
                for tag in topic.tags)
    return total / math.sqrt(len(topic.tags))


def _played_ids(events: Iterable[Event]) -> set[str]:
    return {e.topic_id for e in events if e.topic_id and e.kind in ("play", "complete")}


def known_topics(now: Optional[float] = None) -> dict[str, Topic]:
    """Every tile this server could name right now: the bank, then the pool.

    The bank wins a collision, which cannot happen - a story id starts `st-`
    and a bank id is a hand-written word - but saying which wins is cheaper
    than finding out the day somebody adds a bank entry called `st-...`.
    """
    known = dict(BANK_BY_ID)
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
    # A cold bank has no plays yet. A stable slice beats an empty section, and
    # beats a random one - random means the tile a listener saw this morning is
    # gone this afternoon, and it defeats the shared script cache.
    filler = [t for t in TOPIC_BANK if t.id not in counts and t.id not in exclude]
    return (ranked + filler)[:limit]


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


def rank_from_history(profile: dict[str, float], exclude: set[str],
                      damp: Optional[dict[str, float]] = None,
                      limit: int = SECTION_SIZE,
                      candidates: Optional[Iterable[Topic]] = None,
                      familiar: frozenset = frozenset(),
                      floor: float = RELEVANCE_FLOOR) -> list[Topic]:
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
    """
    damp = damp or {}
    pool = list(candidates) if candidates is not None else list(TOPIC_BANK)
    scored = []
    for topic in pool:
        if topic.id in exclude:
            continue
        score = (_affinity(topic, profile) * damp.get(topic.id, 1.0)
                 * (1.0 + FRESHNESS_BOOST * topic.freshness))
        # A live story is one specific thing that happened; `freshness` is
        # exactly what distinguishes one from a bank topic here, and it is
        # set by `topics_from_stories` and by nothing else.
        if (score > 0 and topic.freshness > 0
                and _is_broad_match(topic, profile)
                and not _subject_is_familiar(topic, familiar)):
            score *= BROAD_MATCH_PENALTY
        if score > floor:
            scored.append((score, topic))
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [t for _s, t in scored[:limit]]


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


def rank_might_like(profile: dict[str, float], exclude: set[str],
                    damp: Optional[dict[str, float]] = None,
                    limit: int = SECTION_SIZE) -> list[Topic]:
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
               written=None) -> dict:
    """The whole myFAM page for one listener.

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
    """
    now = time.time() if now is None else now
    events = store.for_user(user_id) if user_id else []
    profile = taste(events, now, interests)
    mine = _played_ids(events)
    # One read for the whole page. Every personalised section damps the same
    # way, so computing this per section would be the same answer four times.
    damp = fatigue(store.impression_occasions(user_id), mine) if user_id else {}
    used: set[str] = set()
    picked: dict[str, list[Topic]] = {}

    # The live inventory, read once for the page. Synchronous against a cache
    # somebody else refreshed, so `build_feed` stays a pure function of the log
    # plus that cache - which is what keeps it callable in a test with no
    # network, and what makes the page instant. See `stories.py`.
    live = live_topics(now)
    # Everything the pool holds, cap included. "What you missed" has to be able
    # to resolve a tile that was offered a few days ago and has since been
    # pushed under the variety cap - to that listener it was on the page, and
    # a rail that quietly dropped it would be answering a different question.
    live_held = topics_from_stories(stories.pool().held(now), now=now)
    # Which tiles were put in front of them this week. Read once, like the
    # fatigue table, and for a different purpose - see `rank_missed` on why
    # membership may come from an impression and order may not.
    shown = store.impressions_since(user_id, now - MISSED_WINDOW) if user_id else {}
    # What this listener has actually said, for `rank_from_history`'s broad
    # match check. Read once for the page, like the fatigue table, and off
    # the events already in hand.
    familiar = familiar_words(events)
    # What the rest of FAM played this week, for "What you missed". One read,
    # like everything else on this page, and it is a fact about the crowd
    # rather than about this listener - it decides membership and never
    # taste, which is the rule `rank_missed` is built on.
    played_elsewhere = {topic_id for user, topic_id
                        in store.plays_since(now - MISSED_WINDOW)
                        if user != user_id} if user_id else set()
    wide = SECTION_SIZE * CANDIDATE_FACTOR

    # What the world row keeps whatever the personal rails want.
    #
    # Reserved before the fill loop rather than taken after it, because
    # "after" is what produced a Trending rail with one tile on it: the row
    # was filled last, from what four personal rails had left, and Made for
    # you draws on the same pool. See `WORLD_FLOOR` for the trade this makes.
    # **Only when reserving actually buys the floor.** A pool holding fewer
    # than four stories cannot fill this row however it is shared out, so
    # holding its one story back would take it off the personal rail and
    # still leave Trending short - a cost with nothing bought. With enough in
    # the pool, the personal rails still have the rest plus the whole bank.
    world_available = [t for t in live if t.id not in mine]
    world_first = (world_available[:WORLD_FLOOR]
                   if len(world_available) >= WORLD_FLOOR else [])
    reserved = {t.id for t in world_first}

    # Filled most-constrained first, displayed in the order the product asks
    # for. Filling in display order starves the two personal sections: the
    # generic ones can fall back to the whole bank, so they claim the very
    # topics the personal ones needed and those arrive empty - which is
    # exactly backwards, since the personal sections are the point.
    for key in FILL_ORDER:
        # Nothing they have already played, in any section. The feed's job is
        # to hand them the next episode; the crowd rows stay globally *ranked*,
        # they just stop offering back the one they finished this morning.
        seen = used | mine | reserved
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
                                candidates=live_held + list(TOPIC_BANK),
                                limit=MISSED_SECTION_SIZE, now=now,
                                popular=played_elsewhere, familiar=familiar,
                                include_trending=False)
        elif key == "from_history":
            # The one rail that draws on both inventories - today's stories
            # and the standing bank - which is what "a mix of new and cached"
            # asks for.
            picks = rank_from_history(profile, seen, damp, limit=wide,
                                      candidates=live + list(TOPIC_BANK),
                                      familiar=familiar)
        elif key == "followers":
            picks = rank_friends(store, circle, seen, damp, limit=wide, now=now,
                                 written=written)
        elif key == "might_like":
            picks = rank_might_like(profile, seen, damp, limit=wide)
        else:
            # Not damped, deliberately: this row is the same list for
            # everyone, which is what makes it the cheapest section to serve.
            picks = rank_most_played(store, now, seen, limit=wide, written=written)
        picks = diversify(picks, MISSED_SECTION_SIZE if key == "missed"
                          else SECTION_SIZE)
        picked[key] = picks
        used |= {t.id for t in picks}

    # The world row. Filled last and from what is left, which is a change
    # worth explaining: it used to take no part in the mutual exclusion above,
    # because its inventory was not FAM's and the two could not collide. They
    # can now - Made for you draws on the same live pool - and a page showing
    # one listener the same tile twice reads as a bug whatever the ranking
    # meant by it.
    #
    # So the personal rail chooses first and this takes the next hottest.
    # Two listeners therefore see slightly different Trending rows, and that
    # is ordering rather than inventory: the pool is fetched and composed once
    # for everybody, and any warmed script is still taken by whoever taps it.
    # The alternative - Trending claiming the hottest story before the rail
    # the page exists for - would have put the one story this listener
    # actually wants in the row about everybody else.
    #
    # It also never reads the play log. The packet is explicit that Trending
    # "does not use cached episodes": what is trending is a question about
    # today, and answering it with what FAM's listeners have already played
    # would make it a second, laggier copy of the row below it.
    # Its reserved four first, then whatever the personal rails did not take,
    # then - only if it is still short - the stories the pool's own variety
    # cap is holding back. That last rung is free inventory: `held` is already
    # fetched and already composed, and it is offered here rather than
    # anywhere else because this is the row that has nowhere else to go.
    world = world_first + [t for t in live
                           if t.id not in used and t.id not in mine
                           and t.id not in reserved]
    if len(world) < WORLD_FLOOR:
        # `used` as well as `mine`: a story a personal rail already claimed is
        # not spare inventory, and offering it here too would put one tile on
        # two rails - which reads as a bug whatever the ranking meant by it.
        have = {t.id for t in world} | mine | used
        world += [t for t in live_held if t.id not in have]
    # `diversify` caps a facet at two, and on a thin pool that cap is what
    # would take the rail back under four - so it is applied and then topped
    # up from what it dropped, which is the same "a cap on what is available,
    # never a quota on what is not" rule the pool itself keeps.
    picked["world_trending"] = _at_least(
        diversify(world, SECTION_SIZE), world, WORLD_FLOOR)

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
    return {"sections": sections, "personalised": bool(profile),
            "live_stories": len(live)}


#: How many tiles a full-screen section shows. The bank is ~28 topics, so
#: this is "all of it, in this section's order" rather than a page size -
#: there is no second page to fetch and nothing new to generate to fill one.
FULL_SECTION_SIZE = 40


def build_section(store: EventStore, user_id: str, key: str,
                  now: Optional[float] = None,
                  interests: Iterable[str] = (), circle: Iterable[str] = (),
                  written=None) -> dict:
    """One myFAM section, at full length, in the same order the rail used.

    The rail shows six and the screen behind it shows the rest **of the same
    ranking**. One ranker, two views - the rule Explore New already follows,
    for the same reason: a rail and the surface it opens must not give a
    listener two different answers to one question.

    Nothing here generates anything. It reorders a fixed bank, exactly as
    `build_feed` does, which is what makes "view more" free.
    """
    if key not in dict(SECTIONS):
        raise KeyError(key)
    now = time.time() if now is None else now
    events = store.for_user(user_id) if user_id else []
    profile = taste(events, now, interests)
    mine = _played_ids(events)
    damp = fatigue(store.impression_occasions(user_id), mine) if user_id else {}
    limit = FULL_SECTION_SIZE
    live = live_topics(now)
    # `exclude` is what they have already played, and *not* the other
    # sections' picks. On the page the sections take turns so no tile appears
    # twice; here there is only one section, and hiding its best tiles because
    # a different rail happened to claim them would make "view more" show
    # less.
    if key == "from_history":
        picks = rank_from_history(profile, mine, damp, limit=limit,
                                  candidates=live + list(TOPIC_BANK))
    elif key == "might_like":
        picks = rank_might_like(profile, mine, damp, limit=limit)
    elif key == "followers":
        picks = rank_friends(store, circle, mine, damp, limit=limit, now=now,
                             written=written)
    elif key == "world_trending":
        picks = [t for t in live if t.id not in mine][:limit]
    else:
        picks = rank_most_played(store, now, mine, limit=limit, written=written)
    # The same variety rule the rail uses, at the same ratio. A screen showing
    # forty tiles can carry more of one subject than a row showing six, and a
    # cap that did not scale would make "view more" a different ranking from
    # the rail it opened - which is the one thing this screen must not be.
    picks = diversify(picks, limit,
                      max_per_facet=MAX_PER_FACET * (limit // SECTION_SIZE or 1))
    return {
        "key": key,
        "title": dict(SECTIONS)[key],
        "topics": [t.as_dict() for t in picks],
        "empty_reason": _empty_reason(key) if not picks else "",
        "personalised": bool(profile),
    }


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
        ))
    return tiles[:limit] if limit else tiles


def live_topics(now: Optional[float] = None) -> list:
    """The story pool as tiles. Synchronous, and that is the whole design.

    `build_feed` stays a pure function of the event log plus two caches
    somebody else refreshed, which is what keeps the ranker callable in a test
    with no network - and what keeps the browse page instant, because nothing
    on this path can wait on an upstream. See `stories.py` for why the pool is
    global and refreshed in the background.
    """
    return topics_from_stories(stories.pool().live(now), now=now)


def _at_least(picked: list, pool: list, floor: int) -> list:
    """Top a capped list back up to `floor` from what the cap dropped.

    The variety cap is a cap on what is available and never a quota on what
    is not - the rule `stories.py` already keeps about its own pool. Applied
    to a thin Trending row the cap alone can take four tiles down to two,
    which turns "make sure there is variety" into "show less", and the row
    the owner asked to never be under four is exactly the row with no second
    inventory to fall back on.
    """
    if len(picked) >= floor:
        return picked
    have = {t.id for t in picked}
    for topic in pool:
        if len(picked) >= floor:
            break
        if topic.id not in have:
            picked.append(topic)
            have.add(topic.id)
    return picked


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

    Three places to look, in order: the evergreen bank, the live story pool,
    and - failing both - the words of whatever was asked. The middle one is
    why this function exists: a story tile's tags live in the pool and nowhere
    else, and an event logged without them teaches the taste model nothing
    about the tap it just recorded.

    A story that has expired since the tap is the case `text` covers. It is a
    keyword sweep over the question rather than the story's own tags, which is
    a worse answer and still much better than none.
    """
    if topic_id in BANK_BY_ID:
        return BANK_BY_ID[topic_id].tags
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
PROFILE_INTEREST_SLOTS = 4


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
    if pool_had_stories:
        return "Everything the world is on today is already in Made for you."
    return stories.pool().empty_reason


def _empty_reason(key: str) -> str:
    return {
        "most_played": "Nothing has been played yet today.",
        # Deliberately not "nothing is trending". An empty row here is a fact
        # about this deployment, never a claim about the world - the browse
        # surface's version of PROBLEMS.md §89. The live text comes from
        # `stories.Pool.empty_reason`, which knows *which* way it came up
        # empty; this is the fallback when nothing has been asked yet.
        "world_trending": stories.pool().empty_reason,
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

    add(rank_from_history(profile, taken, damp,
                          candidates=live_topics(now) + list(TOPIC_BANK)))
    if len(picks) < size:
        add(rank_followers(store, user_id, mine, taken, damp))
    if len(picks) < size:
        add(rank_most_played(store, now, taken))
    # A listener who has played most of the bank would otherwise get a short
    # grid. Four tiles is the layout, so the last resort drops the "not already
    # played" rule rather than the shape - re-hearing something is a far
    # smaller disappointment than two empty squares. `taken` is rebuilt from
    # what is actually on the grid, because it still carries the played-ids
    # exclusion at this point and reusing it would filter out the very topics
    # this fallback exists to reach.
    if len(picks) < size:
        taken = {t.id for t in picks} | ({after_id} if after_id else set())
        add(list(TOPIC_BANK))
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
