"""The startup topics: what myFAM offers a listener it knows nothing about.

**The algorithm before the algorithm.** Every rail on myFAM is a query over an
event log, so a listener with an empty log scores zero against every tile and
`RELEVANCE_FLOOR` empties the rail that matters most. That is the correct
behaviour for a ranker - it has nothing to rank on and padding it with the
least bad thing in the bank is the failure `topics.rank_from_history` was
built against - and it is the wrong *product*: somebody who taps "Continue as
guest" and then "Skip for now" is shown an app with nothing in it, and that is
the only impression of FAM they will ever form for free.

So there is a third inventory beside the evergreen bank and the live story
pool, and it exists for exactly one listener: the one who has said nothing and
done nothing yet.

## What makes these different from the bank

The bank is **evergreen on purpose** - "what habit research actually shows"
is as true next March as it is today, which is what lets twenty-eight
hand-written tiles serve every listener forever. That is also what makes it
the wrong first impression: an app that opens on eight standing explainers
looks like an encyclopaedia, and the packet's requirement here is that these
episodes be *up to date*.

**These are time-anchored questions.** Every `query` below asks about now -
this week, right now, recently - and CLAUDE.md's settled constraint is that
`SEARCH_MODE=always` is production, so **every episode is researched on the
tap**. That is where the freshness comes from, and it is worth being precise
about why that is the right mechanism rather than a shortcut:

* It costs **nothing at page load**. A startup tile is a title, a hook and a
  question, exactly like a story-pool tile - the research and the writing
  happen when a finger lands. So this cannot put a model call on the browse
  path, which is the one thing `topics.build_feed` may never do.
* It needs **no live provider configured**. The story pool is the other way
  to be about today, and it is off until somebody sets `GDELT=1`; the
  research ladder is on in every deployment that can reach a search backend.
  A first impression that depends on optional configuration is one most
  deployments will not have.
* It **degrades honestly**. With no evidence at all `research.NoEvidence`
  refuses the episode rather than writing a stale one from memory, because
  these questions turn on something current by construction.

The trade, stated rather than buried: a startup tile's *title* cannot promise
what its episode will be about, the way a story-pool tile's composed angle
can, because nothing has been retrieved when the page is drawn. So the titles
below are standing frames and the hooks are standing promises, and **not one
of them asserts a fact about the world** - no result, no number, no claim that
a particular thing happened. That is `PROBLEMS.md` §88 and §102 applied one
layer earlier: a tile written before anything is researched may carry a
question, never an answer.

## Why one question per facet, and no more

`topics.TAG_LABELS` is the whole pickable vocabulary - eight facets - and a
cold start knows nothing about which of the eight this listener wants. One
question each is therefore the only *principled* size for this set: it is
complete coverage of what a listener could have told us, with no guess about
which part of it they meant. Anything more is a guess dressed as an editorial
decision, and anything less silently decides that some listeners get a worse
first page than others.

Which of the eight leads is a separate question, and it is not answered here:
`topics.startup_profile` orders them by what FAM's listeners actually play,
falling back to `topics.PICKER_DEFAULT_ORDER` on an empty log. That is the
same reasoning - and the same function - the first-run picker already uses
(§98): asked of somebody with no history, the only honest signal is
everybody else's.

## One tag each, deliberately

Every entry carries its facet and nothing finer, where a bank topic carries
subtags too. Two reasons, and they agree:

* It is **true**. "The world this week" is the whole facet, not a corner of
  it; giving it `geopolitics` would claim a specificity the question does not
  have.
* It makes the set **rank evenly**. The startup prior is over facets, so a
  subtag contributes nothing to `_affinity` while still counting in its
  `sqrt(len(tags))` denominator - a startup topic with two tags would score
  below an identical one with a single tag, for no reason a listener could
  ever benefit from.

And it is what makes the set hand over cleanly. A play on a startup tile logs
that facet, so the first episode somebody hears is also the first real thing
the ranker knows, and `topics.build_feed` stops using this set the moment
there is any taste at all. It is scaffolding that takes itself down.
"""
from __future__ import annotations

#: One startup topic: `(id, title, hook, query, facet, icon)`.
#:
#: Deliberately a plain tuple rather than a `topics.Topic`. `Topic` lives in
#: `topics.py`, which imports this module the way it imports `stories` and
#: `trending`, and importing it back would be a cycle. `topics.startup_topics`
#: does the conversion, which is the same seam `topics.topics_from_stories`
#: already is.
StartupSpec = tuple[str, str, str, str, str, str]

#: Every id starts with this. A startup topic is resolvable by id anywhere a
#: bank topic is (`topics.tags_for_id`, `topics.known_topics`), and the prefix
#: is what makes one recognisable in an event log written months ago.
ID_PREFIX = "su-"

#: The set. One question per facet, in `topics.PICKER_DEFAULT_ORDER` so the
#: declared fallback order reads down the file.
#:
#: Each `query` is written to be worth an episode rather than a headline -
#: `TRENDING.md`'s rule, and for the same reason: "what is the score" produces
#: an episode that restates a score, where "what is at stake" produces one
#: worth three minutes. Each asks for the *why* alongside the *what*, because
#: the brief is what `episode_intelligence` resolves against and a question
#: with no angle in it gets a shapeless one.
STARTUP_TOPICS: tuple[StartupSpec, ...] = (
    ("su-world", "The Week the World Just Had",
     "One story from this week, from the beginning.",
     "the most consequential world news story of the past week, explained from "
     "the beginning and why it matters now",
     "world", "business"),
    ("su-tech", "What Actually Shipped in AI",
     "Past the announcements, to what changed.",
     "the most significant recent developments in artificial intelligence and "
     "what they actually change",
     "tech", "tech"),
    ("su-sports", "The Argument in Sport Right Now",
     "Walk in already knowing the argument.",
     "the biggest storylines in professional sport right now and what is "
     "actually at stake in them",
     "sports", "sports"),
    ("su-business", "The Bet a Big Company Just Made",
     "Big bets are public. The thinking rarely is.",
     "a major strategic move a large company has made recently and the "
     "thinking behind it",
     "business", "business"),
    ("su-money", "What the Market Is Actually Worried About",
     "Not the number. The thing behind it.",
     "what is moving markets right now and what investors are watching next",
     "money", "business"),
    ("su-science", "What the Lab Actually Found",
     "One recent result, and what it doesn't prove.",
     "a significant recent scientific finding and what it actually establishes",
     "science", "rocket"),
    ("su-health", "The New Health Advice, Weighed",
     "The headline claimed more than the study.",
     "recent health research that changed what experts recommend, and what the "
     "evidence actually shows",
     "health", "leaf"),
    ("su-culture", "Why Everyone Is Watching This",
     "Why this, why now, why all at once.",
     "what is capturing cultural attention right now and why it caught on",
     "culture", "camera"),
)

#: The ninth question, which only exists for a listener who said where they
#: are. `{place}` is filled with their own city and region - "Cincinnati,
#: Ohio" - at feed time.
#:
#: **This is the single best use of a location in the whole app**, and the
#: reason is the same one this module exists for: a cold start knows nothing
#: about somebody, and a location is the one thing they may have told us
#: before playing anything. The eight questions above are a complete cover of
#: the pickable vocabulary with no guess about which part of it was meant;
#: this one is not a guess at all.
#:
#: It leads the rail when it exists, ahead of the facet questions, because
#: "where you live" outranks "what most people play" as an answer to *what
#: should this person hear*. It is still one tile of six, so a listener who
#: does not want local news is one scroll from the rest.
#:
#: **It carries `world` and nothing finer**, for both reasons the set above
#: gives: local news is the whole facet rather than a corner of it, and the
#: startup prior is over facets, so a second tag would only inflate the
#: `sqrt(len(tags))` denominator and push this tile below the others.
#:
#: The query asks for what *changed* rather than for "news in {place}", which
#: would retrieve a local paper's front page and produce an episode that
#: reads one out. Same rule as every other entry: worth an episode, not a
#: headline.
LOCAL_ID = "su-local"

LOCAL_TOPIC: StartupSpec = (
    LOCAL_ID, "What Changed in {place}",
    "The week where you actually live.",
    "what has changed recently in and around {place} and why it matters to "
    "people who live there",
    "world", "business",
)


def local_spec(place: str) -> StartupSpec | None:
    """`LOCAL_TOPIC` filled in for one listener, or None with no location.

    None rather than a tile with an empty place in it: "What Changed in " is
    the kind of half-rendered string that ships, and a missing location is a
    perfectly good reason to have eight questions instead of nine.
    """
    place = " ".join(str(place or "").split())
    if not place:
        return None
    spec_id, title, hook, query, facet, icon = LOCAL_TOPIC
    return (spec_id, title.format(place=place), hook,
            query.format(place=place), facet, icon)


#: The longest a hook may be, in characters.
#:
#: `.seed-why` on a browse card is two clamped lines of 10px text in a 150px
#: rail card, and a hook over this length is silently cut mid-word - which is
#: the one failure a clamp can hide. Enforced by a test rather than hoped for,
#: because the alternative is a browser measurement and PROBLEMS.md §115 is
#: what happens when a layout assertion depends on which fonts the machine
#: running it happens to have.
MAX_HOOK = 58
