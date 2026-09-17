"""Write the episode before anybody asks for it.

CLAUDE.md has said from the beginning that latency is answered by *starting
earlier*, never by filling the gap - and that the browse surfaces are where
that is actually possible, because myFAM and DailyFAM know what somebody might
tap before they tap it. This is the machinery for doing it.

The whole trick, in one line
----------------------------
**Prefetch writes into the same script cache, under the same key, as a live
episode.** Nothing on the tap path changes, nothing new has to be looked up,
and there is no second code path that can disagree with the first. A tap on a
warmed tile is an ordinary cache hit - the pipeline has served those since
before this module existed. That is why `pipeline.key_for` is a module-level
function rather than a method: the two sides compute the key with the *same*
code, because two implementations that agree today drift the first time one
gains a field, and the failure would be silent and total - every speculative
script paid for and never read, with the feed looking exactly as it did.

Why this is worth more now than it used to be
---------------------------------------------
Episode intelligence - contextual relevance - put a model call in front of the
first word on search (PROBLEMS.md §82). That was a deliberate trade there, and
on the browse surfaces it does not have to be a trade at all: the brief is the
expensive half and the tap is predictable, so the brief can be built before the
finger lands. Two warm levels follow from that, and they are the honest answer
to CLAUDE.md's open question "how much to prefetch?":

* **brief** - run contextual relevance only, and keep the result. Cheap (one
  small model call), and it removes the seconds EI costs. A tap still pays
  retrieval and writing.
* **script** - run the whole thing and cache the script. The tap pays nothing
  at all. Costs a full episode, and is wasted outright if nobody taps.

Where candidates come from
--------------------------
A `CandidateSource` says what somebody is likely to want and *why* - the reason
is not decoration, it is the contextual-relevance claim being made, and it is
what a report has to print for anyone to judge whether prefetching is paying.
Sources are registered, so myFAM and DailyFAM plug in when they are ready
without this module learning anything about them.

Three are built in because they can be answered from what exists today:
trending (the same list for everyone, so one script serves every listener -
by far the best value per dollar), mixes (the strongest prediction in the
app: somebody said "play this every morning"), and a listener's own feed.

What this must never do
-----------------------
* **Never compete with a live listener.** A speculative episode that delays a
  real one has inverted the entire point. One at a time, and paused while the
  server is generating for somebody who is waiting.
* **Never spend without a ceiling.** Every speculative script costs money and
  some fraction of them are wasted by construction. The budget is a hard cap in
  dollars and in episodes, not a guideline.
* **Never prefetch something personal.** `is_shareable` and the attachment rule
  decide, the same as for a live episode.
* **Never pretend it is paying.** Warmed and *consumed* are counted separately,
  because "how much to prefetch" cannot be answered by anything except the hit
  rate, and a prefetcher that is never checked is a standing bill.

It ships off (`PREFETCH=0`), like the tier system and for the same reason: the
mechanism is worth having ready and the policy is worth deciding with numbers.
`/api/health` reports which state a deploy is in, because a prefetcher that is
off looks exactly like one that is on and missing.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

from config import PREFETCH_LEVELS, settings

log = logging.getLogger(__name__)

#: Warm levels, cheapest first. `off` is not a level - it is `PREFETCH=0`.
#: Re-exported from config so there is one definition and the validator, this
#: module and the tests cannot drift into disagreeing about what a level is.
LEVELS = PREFETCH_LEVELS


# --------------------------------------------------------------------------
# What might be wanted
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Candidate:
    """One episode somebody might ask for, and the claim that they might.

    `reason` is the contextual-relevance statement in words - "third in
    trending", "in their 'At the gym' mix", "the follow-up this episode
    predicted". It is carried all the way to the report, because the only way
    to judge a prefetcher is to see which *kinds* of guess get taken and which
    are paid for and dropped.
    """

    query: str
    minutes: int
    #: Which `CandidateSource` produced this.
    source: str
    reason: str
    #: The bank topic behind it, when there is one. Empty for a typed question.
    topic_id: str = ""
    #: Higher is warmed first. Comparable only within one source's output -
    #: `Prefetcher.plan` interleaves sources rather than ranking across them.
    weight: float = 0.0
    #: Whose tap this is predicted for. Empty for a candidate that is the same
    #: for everyone, which is the cheapest kind: one script, every listener.
    listener: str = ""

    def as_dict(self) -> dict:
        return {"query": self.query, "minutes": self.minutes,
                "source": self.source, "reason": self.reason,
                "topic_id": self.topic_id, "listener": self.listener}


class CandidateSource(Protocol):
    """Something that can say what a listener is likely to want next.

    Implement `candidates` and `register` it. It is called off the request
    path, so it may read a store, but it must not call a model or the network -
    a source that costs money to *ask* turns a speculative saving into a
    certain spend.
    """

    name: str

    def candidates(self, listener: str, limit: int) -> list: ...


_SOURCES: list = []


def register(source) -> None:
    """Add a candidate source. Registered first is asked first."""
    name = getattr(source, "name", "")
    if not name:
        raise ValueError("a candidate source must have a name; the report "
                         "prints it and an unnamed source is unattributable")
    if any(getattr(s, "name", "") == name for s in _SOURCES):
        raise ValueError(f"a candidate source named {name!r} is already "
                         "registered; two would double-count its hit rate")
    _SOURCES.append(source)
    log.info("prefetch: registered candidate source %s", name)


def unregister(name: str) -> bool:
    """Remove a source by name. Returns whether there was one.

    Exists so `install` can be idempotent: startup is the statement "these are
    this deployment's sources", and a statement has to be re-makeable. Without
    it a second startup in one process - which is every test that opens a
    TestClient, and any in-process reload - hits the duplicate guard and takes
    the server down at boot.
    """
    before = len(_SOURCES)
    _SOURCES[:] = [s for s in _SOURCES if getattr(s, "name", "") != name]
    return len(_SOURCES) != before


def sources() -> list:
    return list(_SOURCES)


def reset_sources() -> None:
    """Empty the registry. For tests, and for a process that rebuilds it."""
    _SOURCES.clear()


# --------------------------------------------------------------------------
# What it is allowed to spend
# --------------------------------------------------------------------------
@dataclass
class Budget:
    """A hard ceiling, in two currencies, that resets on a rolling day.

    Two because they fail differently. The episode count is what stops a
    runaway loop; the dollar figure is what stops a *correct* loop being
    expensive - a 10-minute researched episode costs several times a 1-minute
    one, so counting episodes alone does not bound the bill.

    Deliberately not a token bucket. A speculative spend has no user waiting on
    it, so there is nothing to smooth: when the budget is gone the right
    behaviour is to stop until tomorrow, not to trickle.
    """

    max_episodes: int = 0
    max_dollars: float = 0.0
    window_seconds: float = 86400.0

    episodes: int = 0
    dollars: float = 0.0
    started: float = field(default_factory=time.time)

    def _roll(self, now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        if now - self.started >= self.window_seconds:
            self.episodes, self.dollars, self.started = 0, 0.0, now

    def remaining(self, now: Optional[float] = None) -> tuple[int, float]:
        self._roll(now)
        return (max(0, self.max_episodes - self.episodes),
                max(0.0, self.max_dollars - self.dollars))

    def allows(self, now: Optional[float] = None) -> bool:
        episodes, dollars = self.remaining(now)
        return episodes > 0 and dollars > 0

    def spend(self, dollars: float, now: Optional[float] = None) -> None:
        self._roll(now)
        self.episodes += 1
        self.dollars += max(0.0, float(dollars or 0.0))

    def as_dict(self) -> dict:
        episodes, dollars = self.remaining()
        return {
            "max_episodes": self.max_episodes,
            "max_dollars": round(self.max_dollars, 4),
            "episodes_used": self.episodes,
            "dollars_used": round(self.dollars, 4),
            "episodes_left": episodes,
            "dollars_left": round(dollars, 4),
            "window_seconds": self.window_seconds,
        }


# --------------------------------------------------------------------------
# Whether it is paying
# --------------------------------------------------------------------------
@dataclass
class Ledger:
    """What was warmed, and what was actually taken.

    The single most important thing in this module. "Every speculative script
    costs money; every one not fetched costs a wait" is CLAUDE.md's open
    question, and it cannot be answered by anything but the hit rate - so
    warming and consuming are counted separately, per source, and a prefetcher
    nobody checks is just a standing bill.

    Keys rather than counts, because a key is what proves it: `consumed` is
    only ever called from the cache-hit path with the key that actually hit.
    """

    #: key -> the candidate that warmed it, for attributing a later hit.
    warmed: dict = field(default_factory=dict)
    #: keys that were warmed and then served to somebody.
    taken: set = field(default_factory=set)
    #: source name -> {"warmed": n, "taken": n, "dollars": f}
    by_source: dict = field(default_factory=dict)
    skipped_already_cached: int = 0
    #: Warms that stopped at the brief because the answer is a result. Counted
    #: separately from a failure: nothing went wrong, the guess was simply not
    #: the kind of episode that keeps. A prefetcher whose refusals look like
    #: faults cannot be tuned.
    skipped_volatile: int = 0
    failures: int = 0

    def _row(self, source: str) -> dict:
        return self.by_source.setdefault(
            source, {"warmed": 0, "taken": 0, "dollars": 0.0})

    def note_warmed(self, key: str, candidate: Candidate, dollars: float) -> None:
        self.warmed[key] = candidate
        row = self._row(candidate.source)
        row["warmed"] += 1
        row["dollars"] = round(row["dollars"] + max(0.0, dollars), 4)

    def note_consumed(self, key: str) -> bool:
        """Record that a cache hit was on something prefetch put there.

        Returns whether it was - so the caller can log the interesting case and
        stay silent about the ordinary one. Counted once per key: the second
        listener to take a warmed script is a *cache* win, which the cache
        already counts, not a second prediction coming true.
        """
        candidate = self.warmed.get(key)
        if candidate is None or key in self.taken:
            return False
        self.taken.add(key)
        self._row(candidate.source)["taken"] += 1
        log.info("prefetch hit: %r was warmed by %s (%s)",
                 candidate.query, candidate.source, candidate.reason)
        return True

    def as_dict(self) -> dict:
        warmed = len(self.warmed)
        taken = len(self.taken)
        return {
            "warmed": warmed,
            "taken": taken,
            # The number the open question turns on. None rather than 0 when
            # nothing has been warmed: a rate of zero out of zero reads as a
            # failing prefetcher and is actually no data at all.
            "hit_rate": round(taken / warmed, 3) if warmed else None,
            "skipped_already_cached": self.skipped_already_cached,
            "skipped_volatile": self.skipped_volatile,
            "failures": self.failures,
            "by_source": {k: dict(v) for k, v in sorted(self.by_source.items())},
        }


# --------------------------------------------------------------------------
# Briefs warmed ahead of the tap
# --------------------------------------------------------------------------
@dataclass
class _WarmBrief:
    brief: object
    expires: float


class BriefStore:
    """Briefs built before anybody asked, held until they go stale.

    The cheap half of prefetching: contextual relevance costs one small model
    call and several seconds, and on a predictable tap both can be paid in
    advance. A tap then skips straight to retrieval.

    **In-process, and that is a stated limit rather than an oversight.** It
    does not survive a restart and is not shared between workers, so a
    multi-worker deploy warms per worker. Scripts are the expensive half and
    they go in the real shared cache; this exists so the seam is here, and
    moving it to a shared store is a change to this class alone.

    Time-bounded because a brief is a claim about *now*: a why-now hypothesis
    and a recency window built this morning are wrong by this evening, and a
    stale brief is worse than no brief - it would make the episode confidently
    about the wrong day.
    """

    def __init__(self, ttl_seconds: Optional[float] = None) -> None:
        self._ttl = ttl_seconds
        self._items: dict = {}

    @property
    def ttl(self) -> float:
        return (self._ttl if self._ttl is not None
                else float(settings.prefetch_brief_ttl_seconds))

    @staticmethod
    def key(query: str, minutes: int, context: str = "") -> str:
        return f"{int(minutes)}|{context.strip().lower()}|{' '.join(query.lower().split())}"

    def put(self, query: str, minutes: int, brief, context: str = "",
            now: Optional[float] = None) -> None:
        # A degraded brief is the raw query with nothing worked out. Keeping one
        # would mean a tap *skips* contextual relevance and gets the pre-EI
        # behaviour, having paid for a call that failed - worse than not
        # warming at all, and invisible.
        if brief is None or getattr(brief, "degraded", True):
            return
        now = now if now is not None else time.time()
        self._items[self.key(query, minutes, context)] = _WarmBrief(
            brief, now + self.ttl)

    def get(self, query: str, minutes: int, context: str = "",
            now: Optional[float] = None):
        now = now if now is not None else time.time()
        item = self._items.get(self.key(query, minutes, context))
        if item is None:
            return None
        if item.expires <= now:
            self._items.pop(self.key(query, minutes, context), None)
            return None
        return item.brief

    def purge(self, now: Optional[float] = None) -> int:
        now = now if now is not None else time.time()
        stale = [k for k, v in self._items.items() if v.expires <= now]
        for key in stale:
            self._items.pop(key, None)
        return len(stale)

    def __len__(self) -> int:
        return len(self._items)


# --------------------------------------------------------------------------
# The prefetcher
# --------------------------------------------------------------------------
class Prefetcher:
    """Turns candidates into cache entries, within a budget, out of the way.

    Holds no pipeline and synthesises no audio - that is the point of the whole
    design. The script is the expensive, cacheable part; audio is nearly free
    and is made on the tap. So this drives `ScriptGenerator` directly and never
    touches a voice.
    """

    def __init__(self, generator=None, cache=None,
                 briefs: Optional[BriefStore] = None,
                 budget: Optional[Budget] = None) -> None:
        self.generator = generator
        self.cache = cache
        self.briefs = briefs if briefs is not None else BriefStore()
        self.budget = budget if budget is not None else Budget(
            max_episodes=settings.prefetch_daily_episodes,
            max_dollars=settings.prefetch_daily_dollars,
        )
        self.ledger = Ledger()
        #: When a listener was last generating. Speculative work stands aside
        #: for real work; see `quiet_enough`.
        self._last_live = 0.0
        #: One at a time. Concurrency here would be spending the headroom a
        #: waiting listener needs, which is the failure this exists to avoid.
        self._lock = asyncio.Lock()
        self._running = False

    # --- staying out of the way ------------------------------------------
    def note_live_generation(self) -> None:
        """Called when a real listener starts an episode. Cheap on purpose:
        it runs on the request path, so it does one clock read and no more."""
        self._last_live = time.monotonic()

    def quiet_enough(self, now: Optional[float] = None) -> bool:
        """Whether the server is idle enough to spend on a guess.

        A speculative episode that delays a real one has inverted the entire
        point of prefetching, so this is checked before every warm rather than
        once per cycle.
        """
        now = now if now is not None else time.monotonic()
        if not self._last_live:
            return True
        return (now - self._last_live) >= settings.prefetch_quiet_seconds

    # --- what to warm ------------------------------------------------------
    def plan(self, listener: str = "", limit: int = 0) -> list:
        """Candidates to warm, best first, deduped and already-cached removed.

        Sources are **interleaved**, not concatenated, so one source cannot
        take the whole budget. A prefetcher that spends everything on trending
        is a prefetcher with no evidence about whether personalised guesses pay
        - and the hit rate per source is the number the open question needs.
        """
        limit = limit or settings.prefetch_per_cycle
        per_source = [list(s.candidates(listener, limit) or []) for s in _SOURCES]

        interleaved: list = []
        for rank in range(limit):
            for group in per_source:
                if rank < len(group):
                    interleaved.append(group[rank])

        from cache import is_shareable

        seen: set = set()
        chosen: list = []
        for candidate in interleaved:
            query = (candidate.query or "").strip()
            if not query:
                continue
            # The same rule a live episode obeys. A question that is nobody
            # else's business must not be written speculatively either - it
            # would be a script nobody can be served, paid for in advance.
            if not is_shareable(query):
                continue
            fingerprint = (query.lower(), candidate.minutes, candidate.listener)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            chosen.append(candidate)
            if len(chosen) >= limit:
                break
        return chosen

    # --- warming -----------------------------------------------------------
    async def warm(self, candidate: Candidate, level: str = "") -> str:
        """Build this episode ahead of the tap. Returns what it did.

        One of: `"script"`, `"brief"`, `"cached"` (somebody already has it),
        `"budget"`, `"busy"`, `"off"`, `"failed"`. A word rather than a bool
        because every one of those is a different thing to do about it, and a
        prefetcher that reports only success and failure cannot be tuned.

        Never raises. A speculative failure must not reach a caller that has a
        listener waiting on something else.
        """
        level = level or settings.prefetch_level
        if not settings.prefetch or self.generator is None:
            return "off"
        if not self.quiet_enough():
            return "busy"
        if not self.budget.allows():
            return "budget"

        async with self._lock:
            try:
                return await self._warm(candidate, level)
            except Exception as exc:  # noqa: BLE001 - see docstring
                self.ledger.failures += 1
                log.warning("prefetch failed for %r (%s): %s",
                            candidate.query, candidate.source, exc)
                return "failed"

    async def _warm(self, candidate: Candidate, level: str) -> str:
        import metering
        from script_generator import ScriptNotes, plan_episode

        plan = plan_episode(candidate.query, candidate.minutes)
        notes = ScriptNotes()

        # Already there? Then the prediction was right and somebody else's
        # spend already paid for it. Counted, because "we did not need to" is
        # a different outcome from "we did it" and a prefetcher whose skips are
        # invisible looks like it is doing more than it is.
        key = await self._key(plan)
        if key and self.cache is not None and self.cache.get(key):
            self.ledger.skipped_already_cached += 1
            return "cached"

        # Contextual relevance, ahead of the tap. This is the half that costs
        # the listener seconds on search and can cost them nothing here.
        #
        # **No live lookup here, deliberately.** A warmed live fact is stale
        # by the time it is tapped - that is what "live" means - so warming one
        # would spend a provider call speculatively in order to bake a score
        # into a script served hours later. The live state is fetched on the
        # tap path or not at all. PROBLEMS.md §89.
        plan = await self.generator.understand(plan, notes)
        if plan.brief is not None:
            self.briefs.put(candidate.query, candidate.minutes, plan.brief)

        if level == "brief":
            self.budget.spend(_dollars(notes))
            log.info("prefetch warmed a brief for %r (%s: %s)",
                     candidate.query, candidate.source, candidate.reason)
            return "brief"

        # **A script may not be warmed for a question whose answer is a
        # result.** The brief above is a claim about what is being asked and
        # keeps; a script about a game is a claim about its state and does not.
        # Warming one buys a stale episode at full price and then serves it as
        # current, which is §88's failure with a cache in front of it. The
        # brief is still kept, so the tap keeps the latency saving.
        if getattr(plan.brief, "outcome_dependent", False):
            self.budget.spend(_dollars(notes))
            self.ledger.skipped_volatile += 1
            log.info("prefetch kept only the brief for %r (%s): the answer is a "
                     "result, so a warmed script would be stale on arrival",
                     candidate.query, candidate.source)
            return "volatile"

        sentences = [s async for s in self.generator.stream_sentences(plan, notes)]
        if not sentences:
            self.ledger.failures += 1
            return "failed"

        spent = _dollars(notes)
        self.budget.spend(spent)

        if key and self.cache is not None:
            from cache import ttl_for

            # The same one function the serving path uses, with the same
            # inputs. Two implementations of "how long does this keep" drift,
            # and a prefetcher that caches for longer than a tap would is a
            # prefetcher that publishes staleness.
            ttl = ttl_for(candidate.query, live_status=notes.live_status,
                          outcome_dependent=notes.outcome_dependent,
                          recency_days=notes.recency_days)
            if ttl <= 0:
                self.ledger.skipped_volatile += 1
                log.info("prefetch wrote nothing for %r: it does not keep",
                         candidate.query)
                return "volatile"
            # No author, deliberately. A warmed script was nobody's tap, so
            # it belongs to everybody: stamping the listener it was guessed
            # for would hide it from the one Explore feed most likely to want
            # it. See `cache.recent`.
            self.cache.put(key, sentences, ttl,
                           candidate.query, notes.thread, candidate.minutes,
                           self._bucket(plan))
            self.ledger.note_warmed(key, candidate, spent)
            log.info("prefetch warmed %d sentences for %r (%s: %s, $%.4f)",
                     len(sentences), candidate.query, candidate.source,
                     candidate.reason, spent)
        return "script"

    async def _key(self, plan) -> str:
        from pipeline import key_for

        return await key_for(plan, getattr(self.generator, "client", None))

    @staticmethod
    def _bucket(plan) -> str:
        from pipeline import bucket_for

        return bucket_for(plan)

    # --- a cycle -----------------------------------------------------------
    async def run_once(self, listener: str = "") -> dict:
        """Warm one cycle's worth. Returns what happened to each candidate.

        Stops early on `budget` or `busy` - both mean every later candidate
        would get the same answer, and continuing would just be a loop that
        logs the same refusal `prefetch_per_cycle` times.
        """
        if not settings.prefetch:
            return {"ran": False, "reason": "PREFETCH=0", "outcomes": {}}
        if self._running:
            return {"ran": False, "reason": "already running", "outcomes": {}}

        self._running = True
        outcomes: dict = {}
        try:
            for candidate in self.plan(listener):
                result = await self.warm(candidate)
                outcomes[result] = outcomes.get(result, 0) + 1
                if result in ("budget", "busy", "off"):
                    break
        finally:
            self._running = False
        return {"ran": True, "reason": "", "outcomes": outcomes}

    # --- what a person needs to see ---------------------------------------
    def report(self) -> dict:
        return {
            "enabled": bool(settings.prefetch),
            "level": settings.prefetch_level,
            "per_cycle": settings.prefetch_per_cycle,
            "quiet_seconds": settings.prefetch_quiet_seconds,
            "sources": [getattr(s, "name", "?") for s in _SOURCES],
            "budget": self.budget.as_dict(),
            "briefs_held": len(self.briefs),
            **self.ledger.as_dict(),
        }


def _dollars(notes) -> float:
    """What one warmed episode cost, at `metering`'s published rates.

    Reads `metering.price_of` rather than repeating its prices, so a rate
    change moves in one place and a prefetch budget is denominated in the same
    dollars the usage report prints.

    An unpriced model yields a Cost with zeroes for the Claude half, which
    would let a budget stop capping anything without looking broken. So that
    case is *named*: the model is logged, and the episode still consumes its
    slot in `max_episodes`, which is the count that keeps a runaway bounded
    when the dollar figure cannot.
    """
    usage = getattr(notes, "usage", None)
    if usage is None:
        return 0.0
    try:
        import metering

        cost = metering.price_of(usage)
    except Exception as exc:  # noqa: BLE001 - never let accounting stop a warm
        log.warning("prefetch could not price a warmed episode: %s", exc)
        return float(getattr(usage, "exa_cost", 0.0) or 0.0)
    if not cost.priced:
        log.warning("prefetch warmed an episode on %r, which has no price in "
                    "metering.PRICES; the dollar budget cannot see it and only "
                    "the episode count is bounding this", usage.model)
    return float(cost.total)


# --------------------------------------------------------------------------
# The process-wide one
# --------------------------------------------------------------------------
_PREFETCHER: Optional[Prefetcher] = None


def prefetcher(generator=None, cache=None) -> Prefetcher:
    """The one this process uses, built on first ask.

    A single instance because the budget and the ledger are process-wide facts:
    two prefetchers would each believe they had the whole day's allowance, and
    the hit rate would be reported twice at half its value.
    """
    global _PREFETCHER
    if _PREFETCHER is None:
        _PREFETCHER = Prefetcher(generator=generator, cache=cache)
    else:
        if generator is not None and _PREFETCHER.generator is None:
            _PREFETCHER.generator = generator
        if cache is not None and _PREFETCHER.cache is None:
            _PREFETCHER.cache = cache
    return _PREFETCHER


def reset() -> None:
    """Drop the process-wide prefetcher. For tests."""
    global _PREFETCHER
    _PREFETCHER = None


def warm_brief(query: str, minutes: int, context: str = ""):
    """A brief built before the tap, if there is one. Used by the writing path.

    Module-level so `script_generator` can ask without importing a prefetcher
    or knowing whether one exists - prefetching is an optimisation, and the
    generation path must not grow a dependency on it being switched on.
    """
    if _PREFETCHER is None or not settings.prefetch:
        return None
    return _PREFETCHER.briefs.get(query, minutes, context)


def note_consumed(key: str) -> bool:
    """Tell the ledger a cache hit was on something prefetch put there."""
    if _PREFETCHER is None or not key:
        return False
    return _PREFETCHER.ledger.note_consumed(key)


def note_live_generation() -> None:
    if _PREFETCHER is not None:
        _PREFETCHER.note_live_generation()


def report() -> dict:
    """For /api/health. Describes the configuration even with no prefetcher
    built yet, because "off" and "never asked" look identical otherwise."""
    if _PREFETCHER is None:
        return {
            "enabled": bool(settings.prefetch),
            "level": settings.prefetch_level,
            "per_cycle": settings.prefetch_per_cycle,
            "quiet_seconds": settings.prefetch_quiet_seconds,
            "sources": [getattr(s, "name", "?") for s in _SOURCES],
            "built": False,
        }
    return {**_PREFETCHER.report(), "built": True}
