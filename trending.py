"""What the world is paying attention to - the browse surface's live signal.

This is **not** `live_facts`, and keeping them apart is the whole design.

    live_facts   "what is the state of THIS entity right now"
                 resolve an entity, fetch its state, seconds of freshness,
                 a status vocabulary. It changes what an episode SAYS.

    trending     "what is the world paying attention to"
                 no entity to resolve, no status, minutes of freshness.
                 It changes what is OFFERED.

Forcing trending through `LiveSource.resolve/fetch` would mean inventing a
fake entity for "the world", and bending a rule built around scoreboards
around a thing that is not one. They compose instead, and cleanly: a trending
tile about a game becomes an ordinary FAM question when it is tapped, EI marks
it `live_domain=sports`, and `live_facts` answers it. **Trending feeds the
bank; live facts feed the evidence.**

The cost design, which is the reason this is worth having
--------------------------------------------------------
**One fetch serves every listener.** CLAUDE.md's rule for the browse surfaces
is one bank for everyone with personalisation in the ordering, and the crowd
row is the cheapest section precisely because it is identical for everybody.
A world-trending row keeps that: one upstream call per refresh window, one
warmed script per tile, every listener. That is a far better ratio than
`live_facts`, which is per entity and per episode.

So the cache here is **global and deliberately not per-listener**, and the
feed read is synchronous against it. `topics.build_feed` stays a pure function
of the log plus this cache, which is what keeps the ranker testable by calling
it - a ranker that fetched would be a ranker nothing could test offline.

What a source may and may not return
------------------------------------
An item is a **subject and a question**, never a headline. "Chiefs 21 Broncos
7" is a fact, has a shelf life of seconds, and belongs to `live_facts`; "why
the Chiefs' offensive line is suddenly the story of their season" is a
question, keeps for hours, and is what a tile is for.

And an item is never a claim. `why_now` is a one-line reason the subject is
being talked about, and it is rendered as a subtitle, not spoken. Nothing here
reaches the writer: a tapped tile runs the ordinary pipeline, which researches
the question from scratch. That is what stops a stale trending blurb becoming
a stale episode.

Nothing is configured by default
--------------------------------
Same doctrine as `live_facts` and for the same reason: an absent capability
that says so can be fixed, and one that is absent and quiet gets shipped. With
no source configured the row is honestly empty and says why, and **that is not
the same as saying nothing is trending in the world.**
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from config import settings

log = logging.getLogger(__name__)


#: What a source is telling us. Distinguished because they are different
#: things to show a listener and different things to fix.
ITEMS = "items"
NOT_CONFIGURED = "not_configured"
SOURCE_FAILED = "source_failed"
TIMEOUT = "timeout"
EMPTY = "empty"
OUTCOMES = (ITEMS, NOT_CONFIGURED, SOURCE_FAILED, TIMEOUT, EMPTY)

#: What sort of signal an item came from. A prediction-market price is what
#: people are *betting*, not what is true, and a tile built from one must not
#: read as a report. Empty where the source has only one kind.
ATTENTION = "attention"              # a news index: this is being written about
PREDICTION_MARKET = "prediction-market"


@dataclass(frozen=True)
class TrendingItem:
    """One thing the world is currently paying attention to.

    `query` is the load-bearing field: it is what FAM would generate, and it
    has to be a *question worth an episode* rather than a headline. A source
    that can only produce headlines needs a step in front of it that turns
    them into questions - see `TRENDING.md` - because a tile whose query is a
    headline produces an episode that restates the headline.
    """

    subject: str
    query: str
    #: One line on why it is being talked about. Shown as the tile's subtitle,
    #: never spoken, and never treated as evidence by anything downstream.
    why_now: str = ""
    source: str = ""
    as_of: Optional[datetime] = None
    #: Position in the source's own ordering, 1-based. Kept so the row can be
    #: shown in the world's order rather than ours.
    rank: int = 0
    kind: str = ATTENTION
    tags: tuple = ()
    url: str = ""

    @property
    def id(self) -> str:
        """A stable id for this subject.

        Hashed from the subject rather than the query so that a source
        rephrasing the question between refreshes does not produce a new tile
        - the id is what impressions, the cache key and the feed's
        already-seen set are all keyed on, and a tile whose id churned every
        fifteen minutes would be shown to the same listener forever.
        """
        digest = hashlib.sha256(" ".join(self.subject.lower().split()).encode()).hexdigest()
        return f"tr-{digest[:12]}"


@dataclass
class TrendingFeed:
    """What the last refresh produced, and what to say when it produced nothing."""

    outcome: str = NOT_CONFIGURED
    items: list = field(default_factory=list)
    detail: str = ""
    fetched_at: float = 0.0

    def __bool__(self) -> bool:
        return bool(self.items)

    @property
    def empty_reason(self) -> str:
        """What the row says when there is nothing in it.

        **Never "nothing is trending".** The row being empty is a fact about
        this deployment's configuration or about one failed fetch, and saying
        otherwise would be the §89 mistake on a browse surface: reading our own
        blindness as a statement about the world.
        """
        return {
            NOT_CONFIGURED: "FAM isn't connected to a world news feed yet.",
            SOURCE_FAILED: "Couldn't reach the news feed just now.",
            TIMEOUT: "The news feed didn't answer in time.",
            EMPTY: "The news feed had nothing new this time.",
        }.get(self.outcome, "Nothing to show here yet.")

    def as_dict(self) -> dict:
        return {"outcome": self.outcome, "count": len(self.items),
                "detail": self.detail, "fetched_at": self.fetched_at,
                "subjects": [i.subject for i in self.items[:10]]}


class TrendingSource:
    """One provider of world-trending subjects.

    Subclass, implement `diagnose` and `fetch`, and `register` it. The same
    three rules `live_facts.LiveSource` obeys, for the same reasons:

    * `diagnose` says *why* it cannot serve. Cheap, no network.
    * `fetch` raises when it is broken and returns `[]` when it genuinely has
      nothing. Collapsing those hides an outage as a quiet miss.
    * `verify` performs a real request - §52, "a key is set" is not "the key
      works" - and is never called on the request path.
    """

    name = "unnamed"
    #: What one refresh costs, in dollars. Metered per refresh rather than per
    #: listener, because one refresh is what every listener shares.
    cost_per_refresh = 0.0

    def diagnose(self) -> tuple[bool, str]:
        raise NotImplementedError

    def available(self) -> bool:
        return self.diagnose()[0]

    async def verify(self) -> tuple[bool, str]:
        return False, f"{self.name} does not implement verify()"

    async def fetch(self, limit: int) -> list:
        raise NotImplementedError


class _UnconfiguredSource(TrendingSource):
    """Named so the gap is visible on `/api/health` rather than being absent."""

    name = "world trending (not configured)"

    def diagnose(self) -> tuple[bool, str]:
        return False, (
            "no world-trending source is configured, so the Trending row is "
            "empty. Needs: a global news or prediction-market feed and its "
            "credential, plus a `fetch` that returns questions rather than "
            "headlines. See TRENDING.md")

    async def verify(self) -> tuple[bool, str]:
        return False, self.diagnose()[1]

    async def fetch(self, limit: int) -> list:
        return []


_SOURCES: list = [_UnconfiguredSource()]

#: The one shared feed. Global on purpose - see the cost design above.
_FEED = TrendingFeed()
_REFRESHING = False


def register(source: TrendingSource) -> None:
    _SOURCES.insert(0, source)
    log.info("trending: registered %s", source.name)


def unregister(name: str) -> bool:
    before = len(_SOURCES)
    _SOURCES[:] = [s for s in _SOURCES if s.name != name]
    return len(_SOURCES) != before


def sources() -> list:
    return list(_SOURCES)


def reset() -> None:
    """Drop the cached feed. For tests and for a forced refresh."""
    global _FEED, _REFRESHING
    _FEED = TrendingFeed()
    _REFRESHING = False


def cached() -> TrendingFeed:
    """What the last refresh produced. Synchronous, so the feed ranker stays
    a pure function that can be called in a test without a network."""
    return _FEED


def is_stale(now: Optional[float] = None) -> bool:
    now = now if now is not None else time.time()
    return (now - _FEED.fetched_at) >= float(settings.trending_ttl_seconds)


async def refresh(limit: int = 0) -> TrendingFeed:
    """Ask the first source that can serve. One call, for every listener.

    Never raises: a browse surface that failed to load because a news feed was
    down would be a worse product than one whose top row is honestly empty.

    Re-entrant by design - several listeners opening myFAM at once must not
    produce several upstream calls, which is the whole economics of this row.
    """
    global _FEED, _REFRESHING

    if not settings.trending:
        _FEED = TrendingFeed(NOT_CONFIGURED, [], "TRENDING=0", time.time())
        return _FEED
    if _REFRESHING:
        return _FEED

    limit = limit or int(settings.trending_max_items)
    _REFRESHING = True
    try:
        for source in _SOURCES:
            ok, why = source.diagnose()
            if not ok:
                log.debug("trending: %s cannot serve - %s", source.name, why)
                continue
            try:
                items = await asyncio.wait_for(
                    source.fetch(limit),
                    timeout=float(settings.trending_timeout_seconds))
            except asyncio.TimeoutError:
                log.warning("trending: %s timed out", source.name)
                _FEED = TrendingFeed(TIMEOUT, [], f"{source.name} timed out",
                                     time.time())
                return _FEED
            except Exception as exc:  # noqa: BLE001 - see docstring
                log.warning("trending: %s failed: %s", source.name, exc,
                            exc_info=True)
                _FEED = TrendingFeed(
                    SOURCE_FAILED, [],
                    f"{source.name} failed: {type(exc).__name__}: {exc}",
                    time.time())
                return _FEED

            items = [i for i in (items or []) if i.subject and i.query][:limit]
            if not items:
                _FEED = TrendingFeed(EMPTY, [],
                                     f"{source.name} returned nothing",
                                     time.time())
                return _FEED
            log.info("trending: %s returned %d subject(s)", source.name, len(items))
            _FEED = TrendingFeed(ITEMS, items, source.name, time.time())
            return _FEED

        _FEED = TrendingFeed(
            NOT_CONFIGURED, [],
            "no world-trending source is configured on this deployment",
            time.time())
        return _FEED
    finally:
        _REFRESHING = False


def report() -> dict:
    """What this server can do for the Trending row - for /api/health."""
    entries = []
    for source in _SOURCES:
        ok, why = source.diagnose()
        entries.append({"name": source.name, "ready": ok, "detail": why})
    ready = [e["name"] for e in entries if e["ready"]]
    return {
        "enabled": bool(settings.trending),
        "sources": entries,
        "ready": ready,
        "ttl_seconds": float(settings.trending_ttl_seconds),
        "max_items": int(settings.trending_max_items),
        "feed": _FEED.as_dict(),
        # Said in words: an empty row is a designed state here, not a fault,
        # and it must never be read as a claim that nothing is happening.
        "detail": ("no world-trending source is configured; the Trending row "
                   "is empty and says so, which is not a statement about the "
                   "world" if not ready else
                   f"world trending served by: {', '.join(ready)}"),
    }


# --------------------------------------------------------------------------
# The fake, for tests and demos
# --------------------------------------------------------------------------
class FakeTrendingSource(TrendingSource):
    """A deterministic stand-in. Selected explicitly, never fallen back to.

    Same rule as the fake scoreboard: a stand-in reachable by accident is a
    stand-in that reaches a listener, so this names itself and is only ever
    built when `TRENDING_SOURCE=fake` asks for it.
    """

    name = "fake trending feed (NOT REAL DATA)"

    #: Questions rather than headlines, because that is the contract a real
    #: source has to meet and a fake that met a lower one would hide the gap.
    SUBJECTS = (
        ("undersea cables",
         "why cutting one undersea cable can slow a whole country's internet",
         "two cables in the Red Sea were reported damaged this week"),
        ("central bank digital currency",
         "what a central bank digital currency would actually change for you",
         "three more central banks published pilot results"),
        ("stadium financing",
         "who really pays when a city builds a stadium",
         "a new public financing vote is in the news"),
        ("drought and shipping",
         "how a drought thousands of miles away raises what you pay for goods",
         "canal transit limits were tightened again"),
        ("antibiotic resistance",
         "why the antibiotics pipeline nearly stopped, and what refilled it",
         "a new class cleared a late-stage trial"),
        ("rare earth processing",
         "why refining rare earths is harder than digging them up",
         "a new processing plant was announced outside China"),
    )

    def diagnose(self) -> tuple[bool, str]:
        return True, ("the fake trending feed is serving; it returns invented "
                      "subjects and is never real world data")

    async def verify(self) -> tuple[bool, str]:
        return True, "the fake trending feed needs nothing and proves nothing"

    @classmethod
    def SUBJECTS_AS_ITEMS(cls, limit: int = 0) -> list:  # noqa: N802 - a constant-ish builder
        """The same items, without an event loop.

        The preview fixture needs them synchronously at build time. Shared
        with `fetch` rather than copied, so the preview cannot drift into
        showing a row the app would not build - which is the failure the
        fixture's own missing-key guard exists to catch.
        """
        now = datetime.now(timezone.utc)
        rows = cls.SUBJECTS[:limit] if limit else cls.SUBJECTS
        return [
            TrendingItem(subject=subject, query=query, why_now=why,
                         source=cls.name, as_of=now, rank=index + 1,
                         kind=ATTENTION)
            for index, (subject, query, why) in enumerate(rows)
        ]

    async def fetch(self, limit: int) -> list:
        return self.SUBJECTS_AS_ITEMS(limit)


def _gdelt_source():
    """Imported lazily: `gdelt` imports this module, so a top-level import
    here would be a cycle."""
    import gdelt

    return gdelt.GdeltTrendingSource()


#: Source name -> builder. A name not here is a configuration error, reported
#: rather than silently serving nothing.
BUILDERS = {"fake": FakeTrendingSource, "gdelt": _gdelt_source}


def install() -> dict:
    """Register whatever `TRENDING_SOURCE` asks for. Re-runnable."""
    for source in list(_SOURCES):
        if getattr(source, "_fam_installed", False):
            unregister(source.name)

    name = (settings.trending_source or "").strip()
    problems: list = []
    installed: list = []
    if name:
        builder = BUILDERS.get(name)
        if builder is None:
            problems.append(
                f"TRENDING_SOURCE={name!r} is not a source this build knows. "
                f"Known: {', '.join(sorted(BUILDERS)) or 'none'}. The Trending "
                f"row will be empty.")
        else:
            try:
                source = builder()
                source._fam_installed = True  # noqa: SLF001 - our own marker
                register(source)
                installed.append(source.name)
            except Exception as exc:  # noqa: BLE001 - must not stop boot
                problems.append(f"trending source {name!r} failed to build: {exc}")

    for problem in problems:
        log.error("trending: %s", problem)
    return {"installed": installed, "problems": problems, "configured": name}
