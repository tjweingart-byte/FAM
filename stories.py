"""The myFAM story pool: live data turned into episodes worth offering.

What this is for
----------------
myFAM used to offer a fixed bank of twenty-eight evergreen topics, ranked four
ways. That bank is still here and still useful - "How a Close Election Is
Actually Called" is worth hearing in any week of any year - but it cannot
answer the thing a browse page is actually asked: *what should I hear about
today*. A bank that never changes says the same thing on the morning a war
starts as it did the morning before.

So there is now a second inventory beside it. A **story** is a subject, a
title and an angle, derived from something that actually moved in the world
in the last few hours, and it is a candidate for an episode rather than an
episode. Nothing here writes a script.

The three rules this module exists to keep
------------------------------------------
**1. A story is a title and an angle, never a script.** The expensive half of
an episode is the writing (~$0.03 and several seconds); the cheap half is
deciding it is worth writing. myFAM decides, at a cost of one small model call
per refresh window shared by every listener, and the script is written on the
tap - or replayed from the shared cache when somebody else already tapped it.
Offering forty tiles therefore costs one call and not forty.

**2. One refresh serves every listener.** The same cost design `trending.py`
was built on, and CLAUDE.md's settled rule for the browse surfaces: one bank
for everyone, personalisation in the *ordering*. The pool is global. What
differs per listener is which stories rank where, which is free.

**3. Nothing here may assert a fact.** A story is composed before anything is
retrieved - the whole point is that it is cheap - so it holds a *measurement*
(coverage volume, a traded price, a fixture on today's card) and never a
result. PROBLEMS.md §88 was paid for in a wrong final score, and a tile that
says "how they pulled it off in the fourth" about a game still in its second
is the same failure moved onto the browse surface, where it is seen by more
people. The composer is told so explicitly, `_RESULT_WORDS` catches it when
the prompt does not hold and sends that one tile back to its template, and the
rule survives a total outage because a template cannot say a result either.

And the corollary the rest of the app already lives by: **never infer a
current-world fact from the absence of current-world evidence.** A provider
that is not configured produces no stories and says which provider and why. It
never produces "nothing is happening".

How hard to push, and for how long
----------------------------------
The packet asks for judgement about "how strong to push certain stories and
for how long to push them for", so that judgement is written down here rather
than left implicit in a sort order:

* **How strong** is `Story.push()`: the source's standing weight
  (`DOMAIN_WEIGHT`) times the provider's own measure of how big this is
  (`Signal.strength`, normalised inside each provider), decayed by half every
  half-shelf-life. A story is at its loudest the hour it appears.
* **How long** is `DOMAIN_SHELF_LIFE`, and it is a hard expiry rather than a
  decay to nothing: a game is worth pushing for hours, a market move for a
  day, a wave of coverage for most of one, an election market for days.
* **And then it stops.** An expired story is retired and may not come back for
  `SUBJECT_COOLDOWN`, however hot its signal still reads. Without that, a
  subject the world talks about all week is the same tile all week - which is
  the "shown the same thing forever" failure `TrendingItem.id` was hashed from
  the subject to avoid, arriving from the other direction.

Variety is enforced in the pool as well as in the rails (`MAX_PER_FACET`), so
a busy day in sport cannot crowd out everything else before the ranker even
sees it.

What it costs
-------------
Per refresh window, for every listener: one fan-out across the configured
providers (keyless or flat-rate; see `story_sources.py`), and **one model call
for the stories that are new since the last window** - already-composed
stories keep their title and angle, so a steady state composes two or three
at a time rather than twenty. At the shipped fifteen-minute window that is
under a hundred small calls a day for the whole deployment.

Everything degrades
-------------------
No key, a timeout, a refusal, unreadable JSON: the story is templated from its
signal instead, `degraded` says so, and the row still fills. Same rule as
episode intelligence - **a layer that adds quality must not be able to
subtract availability** - and the same reason: an outage that looked like
working code is the failure this project has lost the most time to.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Iterable, Optional

import credentials
from anthropic_client import build_async_client
from config import settings

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# What kind of thing a signal is
# --------------------------------------------------------------------------
#: A news index: this is being *written about*. Volume, not truth.
ATTENTION = "attention"
#: A price moved. A measurement of a market, never a verdict on a company.
MARKETS = "markets"
#: What people are *betting*. A forecast, and the one domain where reading a
#: number as a result is most tempting and most wrong - see `live_sources`.
PREDICTION = "prediction-market"
#: Something is being played. Scheduled, under way or just finished, and this
#: module is never the thing that decides which.
SPORTS = "sports"

DOMAINS = (ATTENTION, MARKETS, PREDICTION, SPORTS)

#: How hard a domain gets pushed, before its own strength is taken into
#: account. A judgement, not a measurement, and written here so it can be
#: argued with: a wave of coverage is the broadest signal FAM has and leads;
#: a game today is nearly as good and much more specific; a market move is
#: real but narrower; a betting line is a forecast and ranks last, because a
#: tile built on one has to spend half its angle saying so.
DOMAIN_WEIGHT = {
    ATTENTION: 1.0,
    SPORTS: 0.95,
    MARKETS: 0.8,
    PREDICTION: 0.65,
}

#: How long a story stays worth offering, from the moment FAM first saw it.
#: Hard expiry, not a fade - see the module docstring. These are the "for how
#: long to push them" half of the judgement the packet asks for.
DOMAIN_SHELF_LIFE = {
    SPORTS: 8 * 3600.0,          # a fixture is today's story and not tomorrow's
    MARKETS: 18 * 3600.0,        # a move is a day's story; by the next open it is context
    ATTENTION: 20 * 3600.0,      # a wave of coverage outlives a news cycle, not a day
    PREDICTION: 4 * 86400.0,     # an election market moves over weeks, not hours
}

#: After a story expires, how long before the same subject may return. Stops a
#: subject the world talks about all week from being the same tile all week.
SUBJECT_COOLDOWN = 36 * 3600.0

#: At most this many stories in the pool at once, and at most this many
#: sharing one facet. The second is the variety rule applied before the
#: rankers ever see the inventory: a busy Sunday in sport must not be able to
#: crowd everything else out of the bank.
POOL_SIZE = 24
MAX_PER_FACET = 5

#: How many signals one source may contribute to a single refresh. Without it
#: the provider with the chattiest endpoint decides what FAM is about.
MAX_PER_SOURCE = 8


# --------------------------------------------------------------------------
# What a source reports
# --------------------------------------------------------------------------
#: The same closed vocabulary `trending.py` uses, for the same reason: "not
#: configured", "broke" and "had nothing" are three different sentences, and
#: an empty list said all three.
SIGNALS = "signals"
NOT_CONFIGURED = "not_configured"
SOURCE_FAILED = "source_failed"
TIMEOUT = "timeout"
EMPTY = "empty"
#: Working, and deliberately not asked this time round. A source with a free
#: tier measured in requests per *day* cannot be swept every fifteen minutes,
#: and a quota spent on a browse page nobody opened is a quota that is not
#: there when somebody does. Its stories stay in the pool until they expire,
#: so a skipped sweep is invisible to a listener and honest on `/api/health` -
#: which is exactly what `EMPTY` would have made it look like, wrongly.
SKIPPED = "skipped"
OUTCOMES = (SIGNALS, NOT_CONFIGURED, SOURCE_FAILED, TIMEOUT, EMPTY, SKIPPED)


@dataclass(frozen=True)
class Signal:
    """One thing a live provider noticed. Raw material, not a tile.

    `observation` is the load-bearing field and the one with a rule on it: it
    is what the provider *measured* - "coverage is running about three times
    its weekly average", "the contract is trading around 62 percent", "they
    kick off at 8pm tonight" - and it may never be a result. Everything
    downstream is allowed to lean on it, so a provider that put a score in
    here would put a score on a tile, and from there into an episode.
    """

    subject: str
    observation: str
    domain: str = ATTENTION
    source: str = ""
    tags: tuple = ()
    #: 0..1, normalised *inside* the provider. Comparing raw coverage volume
    #: with traded dollars would be comparing units, so each source says how
    #: big this is on its own scale and `DOMAIN_WEIGHT` reconciles them.
    strength: float = 0.5
    as_of: Optional[datetime] = None
    #: Whether the thing behind this signal has an outcome that is not yet in.
    #: Set by the provider from what it can see of the world (a fixture that
    #: has not kicked off, a market that has not resolved) and never inferred
    #: from the words. It is what stops prefetch warming a script that will be
    #: wrong within the hour, and what tells the composer to keep the angle
    #: off the result.
    outcome_pending: bool = False
    #: A URL for provenance, when the provider has one. Never shown as
    #: evidence and never read out; a tile is not a citation.
    url: str = ""
    #: What this provider would call the tile if nobody composed one. Optional,
    #: and only the *templated* path reads it - the composer is given the
    #: observation and writes its own.
    #:
    #: It exists for one case and is worth keeping for it: the trending
    #: registry already produced a question and a one-line reason, and before
    #: the pool existed those two strings *were* the Trending row. A source
    #: that has something better than a template must be able to say so, or
    #: adding the pool in front of it would quietly make a configured
    #: deployment's row worse than it was.
    suggested_query: str = ""
    suggested_angle: str = ""

    @property
    def id(self) -> str:
        return story_id(self.subject)


def story_id(subject: str) -> str:
    """A stable id for a subject.

    Hashed from the subject rather than from any wording around it, so a
    provider that rephrases its observation between refreshes does not mint a
    new tile. The id is what impressions, fatigue, the cooldown and the
    already-seen set are keyed on, and an id that churned every fifteen
    minutes would show one listener one subject forever while fatigue watched
    a different id each time.
    """
    digest = hashlib.sha256(" ".join((subject or "").lower().split()).encode())
    return f"st-{digest.hexdigest()[:12]}"


@dataclass(frozen=True)
class Story:
    """One candidate episode: a title, an angle, and the question behind it.

    `query` is what FAM would actually generate from, and it has to be a
    *question worth an episode* rather than a headline - the rule
    `TRENDING.md` already states, restated here because this is now where
    tiles come from. A tile whose query is a headline produces an episode that
    reads the headline back.
    """

    subject: str
    title: str
    angle: str
    query: str
    domain: str = ATTENTION
    source: str = ""
    tags: tuple = ()
    strength: float = 0.5
    #: When FAM first saw this subject. The clock the push model runs on, and
    #: deliberately *not* the last time a provider mentioned it: a story that
    #: keeps being reported does not get to be new again.
    first_seen: float = 0.0
    #: When the provider last saw it. Kept for the report, never for ranking.
    last_seen: float = 0.0
    shelf_life: float = 24 * 3600.0
    outcome_pending: bool = False
    #: True when the title and angle were templated rather than written,
    #: because the model was unavailable. Visible on `/api/health`: an outage
    #: here looks exactly like working code from outside, which is the whole
    #: reason this field exists.
    degraded: bool = False
    url: str = ""

    @property
    def id(self) -> str:
        return story_id(self.subject)

    def age(self, now: Optional[float] = None) -> float:
        return max(0.0, (time.time() if now is None else now) - self.first_seen)

    def expired(self, now: Optional[float] = None) -> bool:
        return self.age(now) >= self.shelf_life

    def push(self, now: Optional[float] = None) -> float:
        """How hard to push this story, right now. Zero once it has expired.

        Halves every half-shelf-life, so a story is loudest in the hour it
        appears and has faded to a quarter by the time it is retired. That is
        the shape the packet asks for: strong while it is the thing, gone
        before it is yesterday's.
        """
        if self.expired(now):
            return 0.0
        half = max(60.0, self.shelf_life / 2.0)
        return (DOMAIN_WEIGHT.get(self.domain, 0.6) * max(0.0, self.strength)
                * (0.5 ** (self.age(now) / half)))

    def as_dict(self) -> dict:
        return {"id": self.id, "subject": self.subject, "title": self.title,
                "angle": self.angle, "query": self.query, "domain": self.domain,
                "source": self.source, "tags": list(self.tags),
                "first_seen": self.first_seen, "shelf_life": self.shelf_life,
                "outcome_pending": self.outcome_pending,
                "degraded": self.degraded}


@dataclass
class SourceReport:
    """What one provider did on the last refresh, in words that name the fix."""

    name: str
    domain: str
    outcome: str = NOT_CONFIGURED
    count: int = 0
    detail: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "domain": self.domain,
                "outcome": self.outcome, "count": self.count,
                "detail": self.detail}


@dataclass
class Pool:
    """The shared inventory. Global on purpose - see the cost design above."""

    stories: list = field(default_factory=list)
    fetched_at: float = 0.0
    sources: list = field(default_factory=list)
    #: How many of the last refresh's new stories had to be templated.
    degraded: int = 0
    composed: int = 0

    def __bool__(self) -> bool:
        return bool(self.stories)

    def live(self, now: Optional[float] = None) -> list:
        """The unexpired stories, loudest first. What every rail reads."""
        now = time.time() if now is None else now
        rows = [s for s in self.stories if not s.expired(now)]
        rows.sort(key=lambda s: (-s.push(now), s.id))
        return rows

    @property
    def empty_reason(self) -> str:
        """What a rail says when the pool is empty.

        Never "nothing is happening". An empty pool is a fact about which
        providers this deployment has configured, or about one failed sweep -
        the browse-surface form of PROBLEMS.md §89, and the same sentence
        discipline `trending.TrendingFeed.empty_reason` keeps.
        """
        if not self.sources:
            return "FAM isn't connected to a live news source yet."
        if any(s.outcome == TIMEOUT for s in self.sources):
            return "The live sources didn't answer in time."
        if any(s.outcome == SOURCE_FAILED for s in self.sources):
            return "Couldn't reach the live sources just now."
        if all(s.outcome in (NOT_CONFIGURED, SKIPPED) for s in self.sources):
            return "FAM isn't connected to a live news source yet."
        return "The live sources had nothing new this time."

    def as_dict(self) -> dict:
        now = time.time()
        live = self.live(now)
        return {
            "count": len(live),
            "fetched_at": self.fetched_at,
            "degraded": self.degraded,
            "composed": self.composed,
            "sources": [s.as_dict() for s in self.sources],
            "subjects": [{"subject": s.subject, "domain": s.domain,
                          "push": round(s.push(now), 3),
                          "age_hours": round(s.age(now) / 3600.0, 1)}
                         for s in live[:12]],
        }


class StorySource:
    """One provider of signals.

    The same three obligations `live_facts.LiveSource` and
    `trending.TrendingSource` carry, for the same reasons:

    * `diagnose` says *why* it cannot serve, cheaply and without a network.
    * `collect` raises when it is broken and returns `[]` when it genuinely
      saw nothing. Collapsing those hides an outage as a quiet miss.
    * `verify` performs a real request - §52, "a key is set" is not "the key
      works" - and is never called on a request path.
    """

    name = "unnamed"
    domain = ATTENTION
    #: What one sweep of this source costs, in dollars. Per refresh rather
    #: than per listener, because one refresh is what every listener shares.
    cost_per_refresh = 0.0
    #: The shortest gap between two sweeps of *this* source. Zero means "every
    #: time the pool refreshes". It exists because the providers' free tiers
    #: are not measured in the same units - API-Sports allows a hundred
    #: requests a *day* - and a pool clock that suited the cheapest source
    #: would exhaust the dearest one before lunch.
    min_interval_seconds = 0.0

    def diagnose(self) -> tuple[bool, str]:
        raise NotImplementedError

    def available(self) -> bool:
        return self.diagnose()[0]

    async def verify(self) -> tuple[bool, str]:
        return False, f"{self.name} does not implement verify()"

    async def collect(self, limit: int) -> list:
        raise NotImplementedError


_SOURCES: list = []
_POOL = Pool()
_REFRESHING = False
#: subject id -> when it was retired. The cooldown that stops a long-running
#: subject being the same tile for a week.
_RETIRED: dict = {}
#: source name -> when it was last asked. See `StorySource.min_interval_seconds`.
_LAST_SWEPT: dict = {}


def register(source: StorySource) -> None:
    _SOURCES.append(source)
    log.info("stories: registered %s (%s)", source.name, source.domain)


def unregister(name: str) -> bool:
    before = len(_SOURCES)
    _SOURCES[:] = [s for s in _SOURCES if s.name != name]
    return len(_SOURCES) != before


def sources() -> list:
    return list(_SOURCES)


def pool() -> Pool:
    """The current inventory. Synchronous, so `topics.build_feed` stays a pure
    function of the event log plus this - a ranker that fetched could not be
    tested offline, and the browse surfaces are where the wait must be zero."""
    return _POOL


def reset() -> None:
    """Drop everything. For tests, and for a forced refresh."""
    global _POOL, _REFRESHING
    _POOL = Pool()
    _REFRESHING = False
    _RETIRED.clear()
    _LAST_SWEPT.clear()


def seed(stories: Iterable[Story], sources_report: Iterable[SourceReport] = ()) -> Pool:
    """Install a pool directly. For tests, the preview fixture and demos.

    Named rather than reached through the module global, so a test that wants
    an inventory says so and nothing acquires one by accident.
    """
    global _POOL
    _POOL = Pool(stories=list(stories), fetched_at=time.time(),
                 sources=list(sources_report))
    return _POOL


def is_stale(now: Optional[float] = None) -> bool:
    now = now if now is not None else time.time()
    return (now - _POOL.fetched_at) >= float(settings.stories_ttl_seconds)


# --------------------------------------------------------------------------
# Collecting
# --------------------------------------------------------------------------
async def collect(limit: int = MAX_PER_SOURCE,
                  now: Optional[float] = None) -> tuple[list, list]:
    """Sweep every configured provider concurrently. Never raises.

    Concurrent because these are independent upstreams and doing them in
    series would put the sweep past any sensible ceiling; nobody is waiting on
    it, but a sweep that takes a minute is a sweep that overlaps the next one.

    Returns the signals and one `SourceReport` per provider, because "GDELT
    served and Finnhub is not configured" is the sentence `/api/health` has to
    be able to say.
    """
    # The caller's clock, not the wall clock, so a test that moves time
    # forward moves the per-source floors with it.
    now = time.time() if now is None else now
    reports: list = []
    ready: list = []
    for source in _SOURCES:
        ok, why = source.diagnose()
        if not ok:
            reports.append(SourceReport(source.name, source.domain,
                                        NOT_CONFIGURED, 0, why))
            continue
        floor = float(getattr(source, "min_interval_seconds", 0.0) or 0.0)
        since = now - _LAST_SWEPT.get(source.name, 0.0)
        if floor and since < floor:
            reports.append(SourceReport(
                source.name, source.domain, SKIPPED, 0,
                f"swept {since / 60:.0f} min ago; this source sweeps at most "
                f"every {floor / 60:.0f} min to stay inside its free tier"))
            continue
        ready.append(source)

    async def sweep(source: StorySource):
        try:
            rows = await asyncio.wait_for(
                source.collect(limit),
                timeout=float(settings.stories_timeout_seconds))
        except asyncio.TimeoutError:
            return source, TIMEOUT, [], f"{source.name} timed out"
        except Exception as exc:  # noqa: BLE001 - one provider must not sink the sweep
            log.warning("stories: %s failed: %s", source.name, exc, exc_info=True)
            return source, SOURCE_FAILED, [], f"{type(exc).__name__}: {exc}"
        rows = [r for r in (rows or []) if r.subject and r.observation][:limit]
        if not rows:
            return source, EMPTY, [], f"{source.name} returned nothing"
        return source, SIGNALS, rows, f"{source.name} returned {len(rows)} signal(s)"

    signals: list = []
    if ready:
        for source, outcome, rows, detail in await asyncio.gather(
                *(sweep(s) for s in ready)):
            # Stamped whatever happened, including a failure: a provider that
            # is down must not be retried every fifteen seconds by a page
            # somebody is refreshing, which is how a small outage upstream
            # becomes a large one here.
            _LAST_SWEPT[source.name] = now
            reports.append(SourceReport(source.name, source.domain, outcome,
                                        len(rows), detail))
            signals.extend(rows)
    return signals, reports


# --------------------------------------------------------------------------
# Composing: signals -> titles and angles
# --------------------------------------------------------------------------
COMPOSER_SYSTEM = (
    "You choose what a listening app offers on its browse page. You are given "
    "measurements of what is happening in the world right now, and you turn "
    "each one into a tile: a title, a one-line angle, and the question the "
    "episode will answer.\n\n"
    "You have researched nothing. Each measurement is a fact about attention, "
    "a price, or a schedule - it is not a report of what happened. So you "
    "never state or imply an outcome: no scores, no winners, no results, no "
    "decisions, no numbers you were not handed. Write the tile so that it is "
    "true whichever way the thing turns out.\n\n"
    "A question worth an episode, never a headline. A headline restates what "
    "somebody already saw; a question is the thing they would want explained "
    "after seeing it."
)

STORY_SCHEMA = {
    "type": "object",
    "properties": {
        "stories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer",
                          "description": "the number of the signal this is for"},
                    "title": {"type": "string",
                              "description": "3-7 words. The tile's name. No "
                                             "colon-subtitle, no clickbait, no "
                                             "question mark."},
                    "angle": {"type": "string",
                              "description": "Up to twelve words: what this "
                                             "episode is actually about. Never "
                                             "a claim about an outcome."},
                    "query": {"type": "string",
                              "description": "6-16 words. The question FAM will "
                                             "research and answer."},
                },
                "required": ["n", "title", "angle", "query"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["stories"],
    "additionalProperties": False,
}

#: Words that would turn a tile into a claim about an outcome. Checked on the
#: composed title and angle, and a hit falls that one story back to its
#: template rather than dropping it - the row still fills, and the thing it
#: fills with cannot be the §88 failure.
#:
#: Deliberately small and deliberately not the whole defence. The prompt is
#: what is supposed to hold; this is the check that says it did not, the same
#: way `OpeningGuard` backs the opening rules. Every catch is logged.
_RESULT_WORDS = re.compile(
    r"\b(beat|beats|defeated|won|wins|winner|lost|loses|final score|"
    r"clinched|sealed|upset|thrashed|edged|survived|crushed|"
    r"soared|plunged|crashed|surged past|hit an all-time)\b",
    re.I)


def _safe(text: str) -> bool:
    return not _RESULT_WORDS.search(text or "")


def template(signal: Signal, now: Optional[float] = None) -> Story:
    """The story a signal becomes when the composer is unavailable.

    Not a placeholder: this is what the whole row looks like on a deployment
    with no API key, and it has to be honest and playable on its own. Each
    template is written to be true before anything is known - it asks what is
    driving a thing, never what a thing turned out to be - which is also why
    the degraded path cannot commit the failure `_RESULT_WORDS` guards.
    """
    now = time.time() if now is None else now
    subject = signal.subject.strip()
    label = subject[:1].upper() + subject[1:]
    if signal.domain == SPORTS:
        title, angle, query = (
            label,
            "What actually decides it",
            f"what to watch for in {subject} and what would decide it")
    elif signal.domain == MARKETS:
        title, angle, query = (
            label,
            "What is moving it, and what it signals",
            f"what is driving the move in {subject} and what it signals")
    elif signal.domain == PREDICTION:
        title, angle, query = (
            label,
            "What the betting says - and what it cannot",
            f"what people are betting about {subject}, and what would settle it")
    else:
        title, angle, query = (
            label,
            "Why it is suddenly everywhere",
            f"what is actually driving the news about {subject} right now")
    return _story_from(signal, title,
                       signal.suggested_angle or angle,
                       signal.suggested_query or query,
                       degraded=True, now=now)


def _story_from(signal: Signal, title: str, angle: str, query: str,
                degraded: bool, now: float) -> Story:
    return Story(
        subject=signal.subject,
        title=title.strip()[:80],
        angle=angle.strip()[:120],
        query=query.strip()[:200],
        domain=signal.domain,
        source=signal.source,
        tags=tuple(signal.tags),
        strength=float(signal.strength),
        first_seen=now,
        last_seen=now,
        shelf_life=DOMAIN_SHELF_LIFE.get(signal.domain, 24 * 3600.0),
        outcome_pending=bool(signal.outcome_pending),
        degraded=degraded,
        url=signal.url,
    )


def build_composer_prompt(signals: list, when: str) -> str:
    """What the composer is shown. One block per signal, numbered.

    The numbering is what lets the reply be matched back: a model asked to
    return things "in the same order" eventually returns them in a different
    one, and an off-by-one here would put a golf angle on a bond story with
    nothing failing.
    """
    lines = [f"It is {when}.", "",
             "Here is what the live sources are measuring right now."]
    for index, signal in enumerate(signals, start=1):
        lines.append("")
        lines.append(f"[{index}] subject: {signal.subject}")
        lines.append(f"    what was measured: {signal.observation}")
        lines.append(f"    kind of measurement: {_DOMAIN_NOTE[signal.domain]}")
        if signal.outcome_pending:
            lines.append("    NOTE: this has not finished. Nothing about how it "
                         "turns out is known to anyone yet, including you. The "
                         "tile must still be true tomorrow.")
    lines += [
        "",
        f"Write one tile for each of the {len(signals)} signals above, and "
        "give each one the number it came from.",
        "",
        "Vary them. If several signals are about the same corner of the world, "
        "make each tile about a different question rather than three "
        "phrasings of one.",
    ]
    return "\n".join(lines)


_DOMAIN_NOTE = {
    ATTENTION: ("how much the world's press is writing about this, compared "
                "with normal. It says the subject is busy; it does not say "
                "what happened."),
    MARKETS: ("a traded price and how far it moved. It is a measurement of a "
              "market, not a verdict on a company."),
    PREDICTION: ("what people are betting will happen. A forecast, and often "
                 "a wrong one - never a report, and never a result."),
    SPORTS: ("something on today's schedule. Whether it has finished, and how, "
             "is not known here."),
}


async def compose(signals: list, now: Optional[float] = None) -> list:
    """Turn signals into stories. One model call for the batch; never raises.

    Every failure path templates instead, which is the behaviour a deployment
    with no key gets permanently, so a composer outage costs quality and never
    availability. `Story.degraded` and `/api/health` say which happened -
    silently templated stories would look identical to written ones from
    outside, and this project has lost whole sessions to exactly that.
    """
    now = time.time() if now is None else now
    signals = list(signals)
    if not signals:
        return []
    if not settings.stories_compose:
        return [template(s, now) for s in signals]

    when = datetime.now(timezone.utc).astimezone().strftime(
        "%A %d %B %Y at %H:%M %Z").replace(" 0", " ")
    try:
        client = build_async_client(credentials.active("ANTHROPIC_API_KEY"))
        response = await asyncio.wait_for(
            client.messages.create(
                model=settings.stories_model,
                max_tokens=settings.stories_max_tokens,
                system=COMPOSER_SYSTEM,
                output_config={
                    "effort": settings.stories_effort,
                    "format": {"type": "json_schema", "schema": STORY_SCHEMA},
                },
                messages=[{"role": "user",
                           "content": build_composer_prompt(signals, when)}],
            ),
            timeout=float(settings.stories_compose_timeout_seconds),
        )
    except asyncio.TimeoutError:
        log.warning("stories: the composer did not answer in %ss; templating %d",
                    settings.stories_compose_timeout_seconds, len(signals))
        return [template(s, now) for s in signals]
    except Exception as exc:  # noqa: BLE001 - availability outranks diagnosis
        log.warning("stories: the composer failed (%s); templating %d", exc,
                    len(signals))
        return [template(s, now) for s in signals]

    if getattr(response, "stop_reason", "") == "refusal":
        log.warning("stories: the composer declined; templating %d", len(signals))
        return [template(s, now) for s in signals]
    try:
        text = next(b.text for b in response.content if b.type == "text")
        rows = (json.loads(text) or {}).get("stories") or []
    except (StopIteration, AttributeError, ValueError, TypeError) as exc:
        log.warning("stories: the composer returned nothing readable (%s)", exc)
        return [template(s, now) for s in signals]

    written: dict = {}
    for row in rows:
        try:
            index = int(row.get("n", 0)) - 1
        except (TypeError, ValueError):
            continue
        if not 0 <= index < len(signals):
            continue
        title = str(row.get("title") or "").strip()
        angle = str(row.get("angle") or "").strip()
        query = str(row.get("query") or "").strip()
        if not (title and query):
            continue
        if not (_safe(title) and _safe(angle)):
            # The prompt is what is supposed to hold. It did not, so this one
            # falls back - and it is logged, because a guard that fires
            # quietly is a prompt nobody fixes.
            log.warning("stories: composed tile asserts an outcome, templating "
                        "instead: %r / %r", title, angle)
            continue
        written[index] = (title, angle, query)

    out = []
    for index, signal in enumerate(signals):
        if index in written:
            title, angle, query = written[index]
            out.append(_story_from(signal, title, angle, query,
                                   degraded=False, now=now))
        else:
            out.append(template(signal, now))
    return out


# --------------------------------------------------------------------------
# Refreshing
# --------------------------------------------------------------------------
def _diversified(stories: list, now: float) -> list:
    """The pool's own variety rule, applied before any ranker sees it.

    Facet-capped rather than source-capped, because the thing a listener
    notices is four tiles about sport and not four tiles from API-Sports.
    Ordered by push, so what gets dropped is the weakest of an over-served
    facet rather than an arbitrary one.
    """
    stories = sorted(stories, key=lambda s: (-s.push(now), s.id))
    per_facet: dict = {}
    kept: list = []
    for story in stories:
        facets = {_facet(tag) for tag in story.tags} or {story.domain}
        if any(per_facet.get(f, 0) >= MAX_PER_FACET for f in facets):
            continue
        for facet in facets:
            per_facet[facet] = per_facet.get(facet, 0) + 1
        kept.append(story)
        if len(kept) >= POOL_SIZE:
            break
    return kept


def _worth_composing(signals: list, already: int) -> list:
    """Which of the new signals to spend the composition on.

    The cheapest saving in the whole subsystem, because composing a tile that
    the variety cap is about to drop is money spent on something nobody will
    ever read. So the same cap is applied *before* the call rather than after:
    strongest first, capped per facet, and only as many as the pool has room
    for.

    It is deliberately the same ordering `_diversified` uses. Two different
    ideas of "best" would mean composing one set and showing another, which is
    the expensive half of both mistakes.
    """
    room = max(0, POOL_SIZE - already)
    if room <= 0 or not signals:
        return []
    ordered = sorted(
        signals,
        key=lambda s: (-(DOMAIN_WEIGHT.get(s.domain, 0.6) * max(0.0, s.strength)),
                       s.subject))
    per_facet: dict = {}
    kept: list = []
    for signal in ordered:
        facets = {_facet(tag) for tag in signal.tags} or {signal.domain}
        if any(per_facet.get(f, 0) >= MAX_PER_FACET for f in facets):
            continue
        for facet in facets:
            per_facet[facet] = per_facet.get(facet, 0) + 1
        kept.append(signal)
        if len(kept) >= room:
            break
    return kept


def _facet(tag: str) -> str:
    """Fold a subtag up to its facet without importing `topics`.

    `topics` imports this module, so the dependency only runs one way. The
    table is `topics.TAG_PARENT` and is read lazily rather than copied, so the
    two cannot drift into disagreeing about what `chips` belongs to.
    """
    import topics

    return topics.TAG_PARENT.get(tag, tag)


async def refresh(now: Optional[float] = None) -> Pool:
    """Sweep the providers, compose what is new, and install the pool.

    Never raises: a browse surface that failed to load because a news feed was
    down would be a worse product than one whose rows are honestly thinner.

    Re-entrant by design. Several listeners opening myFAM at once must not
    produce several sweeps and several model calls, which is the whole
    economics of this - one refresh serves every listener.
    """
    global _POOL, _REFRESHING
    now = time.time() if now is None else now

    if not settings.stories:
        _POOL = Pool([], now, [], 0, 0)
        return _POOL
    if _REFRESHING:
        return _POOL

    _REFRESHING = True
    try:
        signals, reports = await collect(now=now)

        # Anything already in the pool keeps its title, its angle and - the
        # part that matters - its `first_seen`. A story that keeps being
        # reported does not get to be new again, which is what makes "how long
        # to push it for" a real ceiling rather than a rolling one.
        existing = {s.id: s for s in _POOL.stories}
        _retire(existing, now)

        kept: list = []
        fresh: list = []
        seen_ids: set = set()
        for signal in signals:
            key = signal.id
            if key in seen_ids:
                continue          # two providers noticed the same subject
            seen_ids.add(key)
            previous = existing.get(key)
            if previous is not None and not previous.expired(now):
                kept.append(replace(previous, strength=float(signal.strength),
                                    last_seen=now))
                continue
            retired_at = _RETIRED.get(key)
            if retired_at is not None and now - retired_at < SUBJECT_COOLDOWN:
                continue          # said its piece; not again this soon
            fresh.append(signal)

        # A story whose provider stopped mentioning it is kept until it
        # expires. Providers are noisy - a theme drops out of GDELT's top
        # fifteen for one sweep and returns - and a tile that vanished between
        # two page loads would read as a bug rather than as an editorial
        # decision.
        #
        # Counted before composing rather than after, because it takes up room
        # in the pool: composing against the space the *mentioned* stories
        # leave would buy tiles that the cap then drops.
        still = [s for s in _POOL.stories
                 if s.id not in seen_ids and not s.expired(now)]

        # Composed in one call, and only for what is actually new *and could
        # actually be shown*. In a steady state that is two or three tiles per
        # window; on a cold pool with every source configured it would
        # otherwise be forty, two thirds of which `_diversified` would throw
        # away a moment later - paid for, and never read.
        composed = await compose(_worth_composing(fresh, len(kept) + len(still)),
                                 now)

        _POOL = Pool(
            stories=_diversified(kept + composed + still, now),
            fetched_at=now,
            sources=reports,
            degraded=sum(1 for s in composed if s.degraded),
            composed=len(composed),
        )
        log.info("stories: pool holds %d (%d new, %d templated) from %s",
                 len(_POOL.stories), len(composed), _POOL.degraded,
                 ", ".join(f"{r.name}:{r.outcome}" for r in reports) or "no sources")
        return _POOL
    finally:
        _REFRESHING = False


def _retire(existing: dict, now: float) -> None:
    """Record what has just expired, so the cooldown can hold it out."""
    for key, story in existing.items():
        if story.expired(now):
            _RETIRED.setdefault(key, story.first_seen + story.shelf_life)
    for key in [k for k, at in _RETIRED.items() if now - at > SUBJECT_COOLDOWN * 2]:
        _RETIRED.pop(key, None)


# --------------------------------------------------------------------------
# What this server can do - for /api/health
# --------------------------------------------------------------------------
def report() -> dict:
    entries = []
    for source in _SOURCES:
        ok, why = source.diagnose()
        entries.append({"name": source.name, "domain": source.domain,
                        "ready": ok, "detail": why})
    ready = [e["name"] for e in entries if e["ready"]]
    return {
        "enabled": bool(settings.stories),
        "composing": bool(settings.stories_compose),
        "model": settings.stories_model if settings.stories_compose else "",
        "ttl_seconds": float(settings.stories_ttl_seconds),
        "sources": entries,
        "ready": ready,
        "pool": _POOL.as_dict(),
        # Said in words, because an empty pool is a designed state and must
        # never be read as a claim that nothing is happening in the world.
        "detail": (
            "no live story source is configured, so myFAM offers its evergreen "
            "bank only and says why - which is not a statement about the world"
            if not ready else
            f"myFAM's story pool is fed by: {', '.join(ready)}"),
    }


def install() -> dict:
    """Register the configured providers. Re-runnable, and never raises.

    Lives in `story_sources` rather than here so this module stays about the
    pool; imported lazily because the providers import this one for its types.
    """
    for source in list(_SOURCES):
        if getattr(source, "_fam_installed", False):
            unregister(source.name)
    try:
        import story_sources

        return story_sources.install()
    except Exception as exc:  # noqa: BLE001 - a bad provider must not stop boot
        log.error("stories: could not install sources: %s", exc, exc_info=True)
        return {"installed": [], "problems": [str(exc)]}
