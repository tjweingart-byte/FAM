"""A seam for facts an article index cannot be fresh enough to hold.

Exa retrieves *writing about* the world. For most questions that is the right
shape - an explainer, a cause, a trend. For two kinds it is structurally wrong,
and no amount of better querying fixes it:

* **Scores, fixtures and standings.** A game ends and the scoreboard knows
  instantly; the recap that says so is written, published and indexed later.
  Between those two moments a search returns the *preview*, and an episode
  built on it describes a match that has already been played as though it is
  still coming. That is the failure in the blueprint's temporal table, and it
  is not a writing bug - the evidence really did say that.
* **Prices, indexes and rates.** Same shape, shorter fuse. "Where is the stock
  now" is answered by a quote, not by an article about a quote.

So this is the route around the index: a small registry of sources that answer
a *fact* rather than return a document, consulted when the brief says the
question turns on one.

The shape, and why it is this shape
-----------------------------------
`LiveSource` is deliberately tiny - a domain, a diagnosis, and a fetch. That is
enough for the pipeline to route to it and enough for `/api/health` to say
whether it can serve, and it commits FAM to no particular vendor. Adding a real
provider is `register(MySource())` and nothing else changes: `research` already
asks this module, and `script_generator` already knows how to put the answer in
front of the writer.

**Nothing is configured by default, and that is not the same as nothing being
here.** Both domain sources below are declared and both report, precisely, what
they would need in order to serve. That is the difference this project keeps
paying for: a capability that is absent and says so can be fixed, and one that
is absent and quiet gets shipped. `report()` is on `/api/health` for exactly
that reason.

What a live fact outranks
-------------------------
Everything. A source here reports a state with a timestamp attached, which is
strictly better evidence than a sentence in an article about that state. So
`as_prompt_block` says so to the writer, and says the timestamp out loud, so a
result that arrived four minutes ago is not described as last night's.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from config import settings

log = logging.getLogger(__name__)

#: The question shapes an article index is structurally too slow for. Matches
#: `Brief.live_domain`, and is the whole routing vocabulary - a source declares
#: one of these and the brief names one of these.
LIVE_DOMAINS = ("sports", "markets", "elections")

#: What an event's state may be. **A closed vocabulary, because downstream
#: behaviour switches on it** - the cache TTL, the story shape and whether a
#: result may be spoken at all. A free string cannot be switched on safely: a
#: provider returning "In Progress", "live" or "1H" would each silently miss
#: every comparison and be treated as `unknown`, which is the quiet failure
#: this project keeps paying for.
#:
#: **Only evidence may set this.** EI decides whether the *request* wants a
#: result (`Brief.outcome_dependent`); it may never decide that an event is
#: under way or finished, because it has retrieved nothing and its own
#: knowledge is months old. See PROBLEMS.md §88 and §89.
SCHEDULED = "scheduled"
IN_PROGRESS = "in_progress"
FINAL = "final"
UNKNOWN = "unknown"
STATUSES = (SCHEDULED, IN_PROGRESS, FINAL, UNKNOWN)

#: A `LiveFacts.kind` that means "this is what people are betting will happen",
#: never "this is what happened". It exists as a constant because
#: `as_prompt_block` withdraws the authority paragraph for it: a forecast is
#: fresher than an article and less authoritative than one, which is the exact
#: combination every other live fact does not have.
PREDICTION_MARKET = "prediction-market"


def normalise_status(value: object) -> str:
    """Map whatever a provider said onto the closed vocabulary.

    Unrecognised means `unknown`, never a guess. `unknown` is not "probably
    fine" - it is the state in which a result may not be spoken, so a provider
    with a vocabulary nobody mapped degrades to silence rather than to a
    confident wrong tense.
    """
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return text if text in STATUSES else UNKNOWN


#: How old a live fact may be and still be described as the state *now*,
#: per domain, in seconds. Different clocks: a score changes every few seconds,
#: a quote every second the market is open but a minute-old one is still
#: honestly "around", and a vote count is published in batches an hour apart.
#: One universal threshold would be wrong for at least two of the three.
MAX_AGE_SECONDS = {
    "sports": 120.0,
    "markets": 300.0,
    "elections": 1800.0,
}


@dataclass(frozen=True)
class Entity:
    """The thing a question is about, named the way its provider names it.

    **This is the half that must never be guessed.** `Brief.subject` is free
    text a model produced - "the Kansas City Chiefs' Week 1 game against the
    Denver Broncos" - and a scores API wants a game id, a quotes API wants a
    ticker. Asking EI to supply one would be asking a layer with month-old
    knowledge to invent an identifier, and a wrong ticker does not fail: it
    returns somebody else's price, fresh, authoritative and completely wrong.
    That is worse than no answer, and it is the §88 failure wearing a badge.

    So resolution goes to the provider's own catalogue, which is definitionally
    right about what it holds. See `LiveSource.resolve`.
    """

    domain: str
    #: Which source this id belongs to. An id is only meaningful to its own
    #: provider, so the pair is the identity - that is also the cache key.
    provider: str
    #: The provider's own identifier, opaque to everything here.
    id: str
    #: What to call it in a log or a report. Never read aloud.
    label: str = ""
    #: When it is due to start, where the provider knows. Used to sanity-check
    #: a status, never to derive one.
    starts_at: Optional[datetime] = None

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.domain}:{self.id}"


@dataclass
class LiveFacts:
    """A state of the world, with the time it was true attached.

    `as_of` is not decoration. The entire reason this module exists is that the
    listener is told "last night" or "two days ago" and the difference has to
    come from a timestamp rather than a guess.
    """

    domain: str
    source: str
    #: When this was true **according to the provider** - the moment the state
    #: it describes was observed, not the moment FAM asked. Timezone-aware,
    #: always. A provider that only reports its own response time is reporting
    #: `delayed_seconds` as well, or it is lying about freshness for free.
    as_of: datetime
    #: Plain statements, already in the words a person would use. One per line
    #: in the prompt, so keep them short and each one self-contained, and never
    #: put a scoreline in a shape a voice cannot read.
    facts: list = field(default_factory=list)
    #: Whether the thing being asked about has finished, is running, or has not
    #: started. The single most valuable field here: it is what stops a future
    #: event being narrated in the past tense, and it is what decides whether
    #: the episode may be cached at all.
    #:
    #: One of `STATUSES`, enforced in `__post_init__` rather than trusted -
    #: a provider's own vocabulary is its own business and must be mapped at
    #: the boundary, not compared against downstream and silently missed.
    status: str = UNKNOWN
    #: Which entity this is about, when a provider resolved one.
    entity: Optional[Entity] = None
    #: How far behind real time this provider's data is *by design*, in
    #: seconds. Free market-data tiers are typically fifteen minutes; a
    #: scoreboard is usually zero. Nonzero means the prompt says "delayed",
    #: never "current" - representing delayed data as current is a lie with a
    #: timestamp attached, which is the most convincing kind.
    delayed_seconds: float = 0.0
    #: What sort of claim these facts are, where the domain has more than one.
    #: Elections especially: a prediction-market price, a poll, a reported
    #: count and a certified result are four different things and only the
    #: last two are results. Empty where the domain has only one kind.
    #:
    #: `PREDICTION_MARKET` is the one `as_prompt_block` switches on, so it is
    #: a named constant rather than a string each provider spells for itself.
    kind: str = ""
    url: str = ""

    def __post_init__(self) -> None:
        self.status = normalise_status(self.status)

    def age_seconds(self, now: Optional[datetime] = None) -> float:
        now = now or datetime.now(timezone.utc)
        return max(0.0, (now - self.as_of).total_seconds())

    def is_fresh(self, now: Optional[datetime] = None) -> bool:
        """Is this new enough to describe the state *now*?

        Deterministic, per domain, and checked before the writer ever sees it -
        a timestamp the model is asked to judge for itself is a timestamp that
        will be judged generously.
        """
        limit = MAX_AGE_SECONDS.get(self.domain)
        if limit is None:
            return True
        return self.age_seconds(now) <= limit

    def __bool__(self) -> bool:
        return bool(self.facts)

    def age_phrase(self, now: Optional[datetime] = None) -> str:
        """How old this is, in the words the episode would use."""
        now = now or datetime.now(timezone.utc)
        seconds = max(0.0, (now - self.as_of).total_seconds())
        if seconds < 120:
            return "just now"
        if seconds < 7200:
            return f"{int(seconds // 60)} minutes ago"
        if seconds < 172800:
            return f"{int(seconds // 3600)} hours ago"
        return f"{int(seconds // 86400)} days ago"

    #: What the writer is allowed to do with each status, said as the rule
    #: rather than left to be inferred from the word. `in_progress` is the one
    #: that produced §88, so it is the most explicit.
    _STATUS_RULE = {
        IN_PROGRESS: (
            "This event is UNDER WAY. It has no result, no winner and no final "
            "score, and nothing below and nothing you remember can supply one. "
            "Say where it stands and what is still open. Do not project how it "
            "ends and do not describe anyone's performance in it as settled."),
        FINAL: (
            "This event has FINISHED and this is the authoritative result. You "
            "may state it."),
        SCHEDULED: (
            "This event has NOT STARTED. It has no result. Write about it in "
            "the future tense."),
        UNKNOWN: (
            "The provider did not establish whether this has started or "
            "finished, so **you do not know that it has**. Do not state a "
            "result, and do not infer one from anything below - an article "
            "written before the event cannot report its outcome."),
    }

    def as_prompt_block(self, now: Optional[datetime] = None) -> str:
        """What the writer sees. Outranks the article packet, and says so."""
        if not self:
            return ""
        lines = "\n".join(f"- {fact}" for fact in self.facts)
        rule = self._STATUS_RULE.get(self.status, self._STATUS_RULE[UNKNOWN])

        # **"Current as of 10:32" and "last updated fifteen minutes ago" are
        # different sentences and the difference is the product.** A delayed
        # feed described as current is the one failure mode this whole module
        # exists to prevent, so the delay is stated in the same breath as the
        # timestamp rather than left in a field nobody reads.
        if self.delayed_seconds > 0:
            freshness = (
                f"This provider is DELAYED by about "
                f"{int(round(self.delayed_seconds / 60)) or 1} minute(s) by "
                f"design. The state below was true at "
                f"{self.as_of:%H:%M %Z} - {self.age_phrase(now)} - and may "
                f"already have moved. Say it is delayed if you give a figure; "
                f"never call it the current one.")
        else:
            freshness = (
                f"It was observed at {self.as_of:%A %d %B %Y at %H:%M %Z}, "
                f"{self.age_phrase(now)}.")

        kind = f"\nThese are {self.kind} figures." if self.kind else ""

        # **Freshness is not authority, and a forecast is the one live fact
        # where they come apart.** Every other source here reports a state
        # that was observed: a scoreboard outranks an article about the game
        # because the article is older than the state it describes. A
        # prediction market reports what people are *betting*, so it is
        # fresher than the articles and less authoritative than them - and
        # telling the writer it wins on disagreement is §88's side door
        # standing open: an article reporting the actual result would be
        # overruled by a price, and "trading at 94 percent" would be written
        # up as the outcome. So this one kind is told the opposite.
        if self.kind == PREDICTION_MARKET:
            standing = (
                "This is a FORECAST and it is not evidence of an outcome. It "
                "is the newest thing you have been given and the least "
                "authoritative: a price says what people expect, and the "
                "articles below say what has been reported. Where they "
                "disagree, THE ARTICLES WIN - a market that has not caught up "
                "with a reported result is a market that is wrong. Never "
                "settle the question with a number from here, never round one "
                "up into a certainty, and never describe a percentage as a "
                "lead, a win or a result.")
        else:
            standing = (
                "This is the most authoritative thing you have been given. "
                "Where it and the articles below disagree, this is what is "
                "true and the articles are older - say so in passing if it "
                "matters and carry on.")

        return f"""
This came from {self.source}, which reports the state directly rather than an
article about it. {freshness}{kind}

{rule}

{standing}

<live_facts>
{lines}
</live_facts>
"""

    def as_dict(self) -> dict:
        return {"domain": self.domain, "source": self.source,
                "as_of": self.as_of.isoformat(), "facts": list(self.facts),
                "status": self.status, "url": self.url,
                "delayed_seconds": self.delayed_seconds, "kind": self.kind,
                "entity": self.entity.key if self.entity else ""}


class LiveSource:
    """One provider of live facts for one domain.

    Subclass, implement `diagnose`, `resolve` and `fetch`, and `register` it.

    **Two steps, not one, and the split is load-bearing.** Resolution answers
    *which thing are they talking about*; fetching answers *what is true about
    it right now*. They are separated because they have completely different
    properties:

    * Resolution is stable - a game id does not change - so it can be cached
      for a long time, and it must come from the provider's own catalogue.
      Never from a model: an invented ticker returns somebody else's price,
      confidently, and nothing downstream can tell.
    * The fact is the opposite - it changes while you read it - so it may be
      cached for seconds at most, and never across a status change.

    Four rules, each of which this codebase has paid to learn:

    * `diagnose` says *why* it cannot serve, never just that it cannot. A check
      that answers no without a reason cannot be fixed from a log. It reports
      configuration; `verify` is what performs the real call.
    * `resolve` returns `None` when the catalogue has no such thing. That is a
      real answer - "no matching entity" - and is not the same as a failure.
    * `fetch` returns `None` when it has nothing, and raises when it is broken.
      Those are different states and collapsing them hides an outage as a quiet
      miss.
    * Neither ever falls back to another source. A deployment that asked for
      one provider and silently got another is measuring one thing and
      believing another - see `research.retrieve` for the same rule.
    """

    name = "unnamed"
    domain = ""
    #: What one `fetch` costs, in dollars. Metered at the moment of spend, per
    #: §73 - the provider only ever sees one account, so "which listener
    #: produced which call" is answerable then or never.
    cost_per_call = 0.0
    #: How far behind real time this provider is by design, in seconds.
    delayed_seconds = 0.0

    def diagnose(self) -> tuple[bool, str]:
        """Is this configured? Cheap, no network, called on every lookup."""
        raise NotImplementedError

    def available(self) -> bool:
        return self.diagnose()[0]

    async def verify(self) -> tuple[bool, str]:
        """Perform a real request and say whether it worked.

        §52's rule applied here: "a key is set" is not "the key works". This
        is for preflight and `tools/verify_live.py`, never for the request
        path - a network call at startup would make a provider outage into a
        server that will not boot.

        The default says it cannot be verified rather than claiming success,
        because a `verify` that returns True by inheriting it is worse than
        none at all.
        """
        return False, f"{self.name} does not implement verify()"

    async def resolve(self, brief) -> Optional[Entity]:
        """Which entity is this brief about, per this provider's catalogue?"""
        raise NotImplementedError

    async def fetch(self, entity: Entity) -> Optional[LiveFacts]:
        """What is true about that entity right now?"""
        raise NotImplementedError


class _UnconfiguredSource(LiveSource):
    """A domain FAM knows it needs and has no provider for yet.

    It exists so the gap is *named* on the health report rather than being an
    absence nobody can see. It never returns facts and never pretends to.

    **"Not configured" is not "there is no live information in the world."**
    Everything downstream keeps those apart, because collapsing them is how an
    unconfigured deployment starts telling listeners that a score nobody looked
    up does not exist.
    """

    def __init__(self, domain: str, needs: str):
        self.domain = domain
        self.name = f"{domain} (not configured)"
        self._needs = needs

    def diagnose(self) -> tuple[bool, str]:
        return False, (
            f"no {self.domain} source is configured, so {self.domain} questions "
            f"are answered from indexed articles and can lag the real state. "
            f"Needs: {self._needs}")

    async def verify(self) -> tuple[bool, str]:
        return False, self.diagnose()[1]

    async def resolve(self, brief) -> Optional[Entity]:
        return None

    async def fetch(self, entity: Entity) -> Optional[LiveFacts]:
        return None


#: What a lookup did. Every one of these is a different thing to say to the
#: writer and a different thing to do about it, which is why this is a word
#: rather than `Optional[LiveFacts]`.
FACTS = "facts"                      # a fresh, usable state
NOT_CONFIGURED = "not_configured"    # no provider for this domain
NO_ENTITY = "no_entity"              # provider has no such thing
NO_FACTS = "no_facts"                # provider knows it, has nothing to say
PROVIDER_FAILED = "provider_failed"  # it broke
TIMEOUT = "timeout"                  # it did not answer in time
STALE = "stale"                      # it answered, too late to be "now"
OUTCOMES = (FACTS, NOT_CONFIGURED, NO_ENTITY, NO_FACTS, PROVIDER_FAILED,
            TIMEOUT, STALE)


@dataclass
class LiveLookup:
    """The result of asking for a live state, including all the ways it fails.

    **The failures are the point.** `Optional[LiveFacts]` collapsed six
    distinguishable situations into `None`, and the writer was told the same
    nothing by all of them - so "we have no scores provider" and "the provider
    says no such game exists" reached the prompt identically, and neither
    reached it at all. PROBLEMS.md §89.
    """

    domain: str
    outcome: str
    facts: Optional[LiveFacts] = None
    #: A sentence a person can act on, for the log and `/api/health`.
    detail: str = ""
    #: What each source did, in order. `(name, outcome, detail)`.
    attempts: list = field(default_factory=list)

    @property
    def status(self) -> str:
        """The event status *evidence* established. `unknown` unless it did.

        Read by the cache policy, so it must never report a status that a
        stale or failed lookup did not actually establish.
        """
        if self.outcome == FACTS and self.facts is not None:
            return self.facts.status
        return UNKNOWN

    def as_dict(self) -> dict:
        return {"domain": self.domain, "outcome": self.outcome,
                "detail": self.detail, "status": self.status,
                "facts": self.facts.as_dict() if self.facts else None,
                "attempts": list(self.attempts)}

    def as_prompt_block(self, now: Optional[datetime] = None) -> str:
        """What the writer is told about the live state, in every case.

        Never empty. An absence the writer is not told about is an absence the
        writer fills in, which is the whole of §88.
        """
        if self.outcome == FACTS and self.facts is not None:
            return self.facts.as_prompt_block(now)

        why = {
            NOT_CONFIGURED: (
                "FAM has no live feed for this domain at all. Nothing here has "
                "looked at the actual state."),
            NO_ENTITY: (
                "FAM has a live feed, and it does not recognise this as "
                "something it covers. That means it cannot tell you the state "
                "- not that there is nothing to tell."),
            NO_FACTS: (
                "FAM's live feed recognises this and is currently reporting "
                "nothing about it."),
            PROVIDER_FAILED: (
                "FAM's live feed FAILED when asked. That is a fault on our "
                "side and says nothing whatever about the world."),
            TIMEOUT: (
                "FAM's live feed did not answer in time. That is a fault on "
                "our side and says nothing whatever about the world."),
            STALE: (
                "FAM's live feed answered, but what it returned is too old to "
                "describe the state now, so it has been withheld rather than "
                "passed off as current."),
        }.get(self.outcome, "No live state was established.")

        return f"""
This question turns on a live {self.domain} state - a score, a standing, a
price, a count, something that changes while you write. {why}

So everything below is articles *about* the world rather than the world, and
articles are written after the fact and indexed after that. The newest thing
you have been given is older than the thing being asked about, and may have
been written before any of it happened.

**Not knowing is not the same as nothing having happened.** You do not know
whether this has started, is under way, or has finished. Do not state a
result, do not describe it in the past tense, and do not tell the listener
that nothing has been reported - what is missing here is our view of it, not
the event. Say what is genuinely established below, say plainly that the
current state is not something we have, and carry on.
"""


#: The registry. Ordered, first match wins within a domain - so a real source
#: registered at startup takes precedence over the placeholder without either
#: of them needing to know about the other.
_SOURCES: list = [
    _UnconfiguredSource(
        "sports",
        "a scores/fixtures API and its credential, plus a `resolve` that maps "
        "the brief's subject to a game or team in the provider's catalogue"),
    _UnconfiguredSource(
        "markets",
        "a quotes API and its credential, plus a `resolve` that maps the "
        "brief's subject to a ticker or index in the provider's catalogue"),
    _UnconfiguredSource(
        "elections",
        "a results or prediction-market API and its credential, plus a "
        "`resolve` that maps the brief's subject to a race - and a `kind` on "
        "every fact, because a market price is not a result"),
]


def register(source: LiveSource) -> None:
    """Add a source. Registered first is tried first within its domain."""
    if source.domain not in LIVE_DOMAINS:
        raise ValueError(
            f"{source.name} declares domain {source.domain!r}, which is not one "
            f"of {', '.join(LIVE_DOMAINS)}. Refusing rather than registering it "
            "somewhere it will never be consulted.")
    _SOURCES.insert(0, source)
    log.info("live facts: registered %s for %s", source.name, source.domain)


def unregister(name: str) -> bool:
    """Drop a source by name. Returns whether one went.

    Startup must be re-runnable - every test that opens a TestClient runs it
    again - so installing sources clears its own first. Same reasoning as
    `prefetch_sources.install`.
    """
    before = len(_SOURCES)
    _SOURCES[:] = [s for s in _SOURCES if s.name != name]
    return len(_SOURCES) != before


def sources_for(domain: str) -> list:
    return [s for s in _SOURCES if s.domain == domain]


# --------------------------------------------------------------------------
# Caching, by entity and never by question
# --------------------------------------------------------------------------
class _EntityCache:
    """Resolutions, keyed by provider and subject. Long-lived on purpose.

    A game id does not change, so paying a catalogue lookup per listener is
    pure waste. This is also what makes three differently-worded questions
    about the same game share one fetch.
    """

    def __init__(self, ttl: float = 3600.0) -> None:
        self.ttl = ttl
        self._items: dict = {}

    @staticmethod
    def key(provider: str, subject: str) -> str:
        return f"{provider}|{' '.join((subject or '').lower().split())}"

    def get(self, provider: str, subject: str, now: Optional[float] = None):
        now = now if now is not None else time.time()
        item = self._items.get(self.key(provider, subject))
        if item is None or item[0] <= now:
            return None
        return item[1]

    def put(self, provider: str, subject: str, entity, now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        self._items[self.key(provider, subject)] = (now + self.ttl, entity)

    def clear(self) -> None:
        self._items.clear()


class _FactCache:
    """Live facts, keyed by **entity** and never by the listener's wording.

    Three people asking "how are the Chiefs doing", "what's happening in the
    Chiefs game" and "Chiefs score" resolve to one entity and share one
    provider call. That turns cost per *listener* into cost per *game*, which
    is the same economics CLAUDE.md applies to the topic bank.

    The TTL comes from the status, because that is what says how fast the
    thing moves - and it is short even for `in_progress`, where the point is
    only to collapse a burst of simultaneous listeners rather than to keep
    anything.
    """

    def __init__(self) -> None:
        self._items: dict = {}

    def ttl_for_status(self, status: str) -> float:
        return {
            IN_PROGRESS: float(settings.live_cache_in_progress_seconds),
            SCHEDULED: float(settings.live_cache_scheduled_seconds),
            FINAL: float(settings.live_cache_final_seconds),
        }.get(status, 0.0)  # unknown is never cached

    def get(self, entity: Entity, now: Optional[float] = None):
        now = now if now is not None else time.time()
        item = self._items.get(entity.key)
        if item is None or item[0] <= now:
            self._items.pop(entity.key, None)
            return None
        return item[1]

    def put(self, entity: Entity, facts: LiveFacts,
            now: Optional[float] = None) -> None:
        ttl = self.ttl_for_status(facts.status)
        if ttl <= 0:
            return
        now = now if now is not None else time.time()
        self._items[entity.key] = (now + ttl, facts)

    def clear(self) -> None:
        self._items.clear()


ENTITIES = _EntityCache()
FACT_CACHE = _FactCache()


def clear_caches() -> None:
    ENTITIES.clear()
    FACT_CACHE.clear()


# --------------------------------------------------------------------------
# The lookup
# --------------------------------------------------------------------------
async def _ask(source: LiveSource, brief, notes, deadline: float) -> LiveLookup:
    """One source, resolved then fetched, on the clock. Never raises."""
    domain = source.domain
    subject = (getattr(brief, "subject", "") or getattr(brief, "query", "") or "")
    per_call = float(settings.live_timeout_seconds)

    def left() -> float:
        return min(per_call, max(0.0, deadline - time.monotonic()))

    try:
        entity = ENTITIES.get(source.name, subject)
        if entity is None:
            budget = left()
            if budget <= 0:
                return LiveLookup(domain, TIMEOUT,
                                  detail=f"{source.name}: no time left to resolve")
            entity = await asyncio.wait_for(source.resolve(brief), timeout=budget)
            if entity is None:
                return LiveLookup(
                    domain, NO_ENTITY,
                    detail=f"{source.name} has nothing matching {subject!r}")
            ENTITIES.put(source.name, subject, entity)

        cached = FACT_CACHE.get(entity)
        if cached is not None:
            # A cache hit is not a provider request and must never be metered
            # as one - §73's rule, and the reason the count lives here rather
            # than inside a provider that cannot see the cache.
            log.info("live facts: %s served %s from the entity cache",
                     source.name, entity.key)
            return LiveLookup(domain, FACTS, facts=cached,
                              detail=f"{source.name} (cached {entity.key})")

        budget = left()
        if budget <= 0:
            return LiveLookup(domain, TIMEOUT,
                              detail=f"{source.name}: no time left to fetch")
        facts = await asyncio.wait_for(source.fetch(entity), timeout=budget)
    except asyncio.TimeoutError:
        log.warning("live facts: %s timed out for %r", source.name, subject)
        return LiveLookup(domain, TIMEOUT, detail=f"{source.name} timed out")
    except Exception as exc:  # noqa: BLE001 - a provider must not end an episode
        # A traceback only for the unexpected. A provider answering with an
        # HTTP status is a sentence, not a crash, and its traceback was what
        # made a Finnhub 422 read as an outage in Render's logs (§144).
        log.warning("live facts: %s failed for %r: %s", source.name, subject, exc,
                    exc_info=not hasattr(exc, "status"))
        return LiveLookup(domain, PROVIDER_FAILED,
                          detail=f"{source.name} failed: {type(exc).__name__}: {exc}")

    if notes is not None and source.cost_per_call:
        notes.usage.add_live_call(1, source.cost_per_call)
    elif notes is not None:
        notes.usage.add_live_call(1, 0.0)

    if not facts or not facts.facts:
        return LiveLookup(domain, NO_FACTS,
                          detail=f"{source.name} is reporting nothing for {entity.key}")

    facts.entity = facts.entity or entity
    if not facts.delayed_seconds and source.delayed_seconds:
        facts.delayed_seconds = float(source.delayed_seconds)

    if not facts.is_fresh():
        # Withheld rather than passed on. Stale live data is more dangerous
        # than none: it arrives with a timestamp and outranks the packet.
        log.warning("live facts: %s returned %s data %.0fs old for %s; "
                    "withholding", source.name, domain, facts.age_seconds(),
                    entity.key)
        return LiveLookup(
            domain, STALE, detail=(
                f"{source.name} returned data {int(facts.age_seconds())}s old, "
                f"past the {int(MAX_AGE_SECONDS.get(domain, 0))}s limit for "
                f"{domain}"))

    FACT_CACHE.put(entity, facts)
    log.info("live facts: %s answered %s with %d fact(s), status=%s, as of %s",
             source.name, entity.key, len(facts.facts), facts.status,
             facts.as_of.isoformat())
    return LiveLookup(domain, FACTS, facts=facts, detail=source.name)


async def lookup(brief, notes=None) -> Optional[LiveLookup]:
    """Ask the registered sources for this brief's domain, in order.

    Returns `None` only when the brief names no live domain - meaning the
    question does not turn on a live state and there is nothing to say about
    one. Every other case returns a `LiveLookup` whose `outcome` says exactly
    what happened, because the writer needs to be told the difference between
    "no provider", "provider broke" and "no such game".

    Bounded twice: each source gets `LIVE_TIMEOUT_SECONDS` and the whole thing
    gets `LIVE_TOTAL_TIMEOUT_SECONDS`, so a dead provider costs a known slice
    of time-to-first-audio and never the episode.
    """
    domain = getattr(brief, "live_domain", "") or ""
    if domain not in LIVE_DOMAINS:
        return None
    if not settings.live_facts:
        return LiveLookup(domain, NOT_CONFIGURED,
                          detail="LIVE_FACTS=0: live lookups are switched off")

    deadline = time.monotonic() + float(settings.live_total_timeout_seconds)
    attempts: list = []
    fallback: Optional[LiveLookup] = None

    for source in sources_for(domain):
        ok, why = source.diagnose()
        if not ok:
            log.info("live facts: %s cannot serve - %s", source.name, why)
            attempts.append((source.name, NOT_CONFIGURED, why))
            continue

        result = await _ask(source, brief, notes, deadline)
        attempts.append((source.name, result.outcome, result.detail))
        if result.outcome == FACTS:
            result.attempts = attempts
            return result
        # Keep the most informative failure to report if nothing succeeds. A
        # provider that broke is a better thing to tell the writer about than
        # a provider that was never configured.
        if fallback is None or fallback.outcome == NOT_CONFIGURED:
            fallback = result
        if time.monotonic() >= deadline:
            log.warning("live facts: out of time for %s after %d source(s)",
                        domain, len(attempts))
            break

    if fallback is None:
        fallback = LiveLookup(
            domain, NOT_CONFIGURED,
            detail=f"no {domain} provider is configured on this deployment")
    fallback.attempts = attempts
    return fallback


def report() -> dict:
    """What live-fact coverage this server has - for /api/health.

    Three states, never two, because "not configured" and "configured and
    broken" need different fixes and look identical from a boolean: `ready`
    lists the domains something can serve, `sources` says per provider whether
    it is operational and why not if it is not, and `live_sources.report()`
    beside it says what configuration *asked* for - a name with a typo is
    configured and not registered, and nothing else would show that.
    """
    per_domain: dict = {}
    for domain in LIVE_DOMAINS:
        entries = []
        for source in sources_for(domain):
            ok, why = source.diagnose()
            entries.append({"name": source.name, "ready": ok, "detail": why})
        per_domain[domain] = entries
    ready = [d for d, entries in per_domain.items()
             if any(e["ready"] for e in entries)]
    return {
        "enabled": bool(settings.live_facts),
        "domains": list(LIVE_DOMAINS),
        "statuses": list(STATUSES),
        "max_age_seconds": dict(MAX_AGE_SECONDS),
        "timeout_seconds": float(settings.live_timeout_seconds),
        "total_timeout_seconds": float(settings.live_total_timeout_seconds),
        "sources": per_domain,
        "ready": ready,
        # Said in words, because "ready: []" reads as a failure and is
        # actually the honest, designed state of a deployment with no
        # provider - and because a listener never hears the difference.
        "detail": ("no live provider is configured; live questions are "
                   "answered from indexed articles and the writer is told so"
                   if not ready else
                   f"live data available for: {', '.join(ready)}"),
    }
