"""The second packet: what came before, from FAM's own store (§310).

The pipeline the owner drew (2026-10-24) has two packets in front of the
writer, not one:

    search -> EI (contextualise; write the inputs for every source)
                 -> the plugins (search ladder, live facts, forecast, local)
                        -> packet one: what is happening now
                 -> the history store (a research agent, later)
                        -> packet two: what came before
              -> consolidate both into what the writer can digest
              -> the writer

Packet one is `EpisodePlan.evidence` and the live blocks, and has been for a
long time. This module is packet two's **seam**: what EI hands the store
(`HistoryQuery`), what the store hands back (`HistoryEntry`), the one
function the pipeline calls (`lookup`), and the consolidation that decides
which of it the writer reads (`consolidate`). The store itself - MongoDB,
being built on another branch - plugs in with `register` and nothing else
here changes.

The rules it keeps, each a settled constraint:

* **Never on the path to an episode's existence** ([ei-before-search]): a
  store that is missing, slow, failing or empty leaves the episode exactly as
  it was before this existed. `lookup` never raises; every way it ends is
  counted (`report`, /api/health) and a failure is noticed on /admin.
* **It never holds the first word** ([latency-start-earlier]): it starts with
  the search and the live lookup, and once the evidence is in it is waited
  for only `HISTORY_GRACE_SECONDS` more - as the forecast is. Nobody waiting
  (prefetch, the editions), it gets its whole `HISTORY_AHEAD_TIMEOUT_SECONDS`.
* **Recency filters** ([recency-credibility]): every entry carries a date and
  the writer is told how old it is, computed here, never inferred from prose.
  An undated entry cannot be placed in time and is dropped. When the search
  found evidence, an entry dated inside the brief's window is dropped too:
  the fresh packet speaks for that stretch, and two accounts of last night
  are an argument the writer would have to referee.
* **History is background, never the current state** ([missed-not-absent],
  [live-facts]): the writer is told a stored record says what was true *when
  it was written*. Anything that changes comes from packet one, which wins.
* **No hostname, no listener** ([recency-credibility], [prefetch-same-key]):
  an entry is shown by its kind and date, never where it came from; and
  `HistoryQuery` has no listener field, so a lookup can never be personal and
  a prefetched brief can never warm somebody's own history.
* **Staging spends nothing** ([zero-spend-staging]): a source registered as
  `remote` is not asked while the spend guard is on.
* **Never in the cache key**: derived from the request, like the evidence.
"""
from __future__ import annotations

import asyncio
import collections
import dataclasses
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from config import settings

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# What goes in and what comes out
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class HistoryQuery:
    """What EI hands the history store: the request, contextualised.

    Everything here is the brief's, worked out before anything was looked
    up. **No listener**: the store is shared, and a lookup that knew who was
    asking could not be prefetched or cached for anybody else.
    """

    #: What was typed.
    query: str
    #: The resolved subject ("the US Federal Reserve").
    subject: str = ""
    #: The bare names a catalogue lists them under ("Philadelphia Eagles").
    entities: tuple = ()
    #: The category tree's path for this request, broadest first - facet,
    #: then each subcategory - as `Brief.category_path` (§310).
    category_path: tuple = ()
    #: One of `episode_intelligence.INTENTS`.
    intent: str = ""
    #: The town, when the question is about one (§194).
    place: str = ""
    #: The brief's window, in days; 0 is evergreen. What the fresh packet
    #: covers - the store is most useful for what came *before* it.
    recency_days: int = 0
    #: What the episode must establish, for a store that ranks by it.
    must_establish: tuple = ()
    #: How many entries the consolidation will keep at most.
    limit: int = 0

    @property
    def category(self) -> str:
        """The broadest node: the facet ("sport")."""
        return self.category_path[0] if self.category_path else ""

    @property
    def subcategory(self) -> str:
        """The most specific node ("philadelphia eagles"), or ""."""
        return self.category_path[-1] if len(self.category_path) > 1 else ""

    def as_dict(self) -> dict:
        out = dataclasses.asdict(self)
        out["category"], out["subcategory"] = self.category, self.subcategory
        return out


#: What a stored record is, in the words the writer reads. A source may
#: return any of these; anything else is shown as a record.
KINDS = {
    "episode": "an earlier FAM episode on this subject",
    "event": "a dated event",
    "fact": "a settled fact",
    "record": "a stored record",
}


@dataclass
class HistoryEntry:
    """One thing the store knows about the subject's past."""

    #: What it is about, in a few words.
    title: str
    #: The material itself: sentences the writer may use. Never a URL.
    text: str
    #: When it was true - the event's date, or when the record was written.
    #: Naive datetimes are read as UTC. None is dropped (see the module).
    when: Optional[datetime] = None
    #: A key of `KINDS`.
    kind: str = "record"
    #: The store's own id, for the record and for de-duplication. Never shown.
    ref: str = ""


@dataclass
class HistoryPacket:
    """What the store answered, and what the writer will read of it."""

    #: found | empty | off | no_source | staging | timeout | failed | late
    outcome: str
    entries: list = field(default_factory=list)
    #: Which source answered, for provenance and /api/health ("mongo").
    source: str = ""
    seconds: float = 0.0
    detail: str = ""
    #: Set by `consolidate`: how many the store returned, and why each one
    #: the writer does not see was left out.
    returned: int = 0
    dropped: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.entries)

    def as_dict(self) -> dict:
        return {"outcome": self.outcome, "source": self.source,
                "seconds": round(self.seconds, 3), "detail": self.detail,
                "returned": self.returned, "kept": len(self.entries),
                "dropped": dict(self.dropped)}


#: A source: given the query, the entries it knows, newest first or not.
Source = Callable[[HistoryQuery], Awaitable[list]]


@dataclass
class _Registered:
    name: str
    fetch: Source
    #: Leaves the machine (a hosted database). Not asked on staging.
    remote: bool


_SOURCE: list = []


def register(fetch: Source, name: str, remote: bool = True) -> None:
    """Plug the history store in. One source; registering again replaces it.

    `fetch(query)` is awaited with `HISTORY_TIMEOUT_SECONDS` around it and
    may raise anything - `lookup` turns every failure into an outcome.
    """
    _SOURCE[:] = [_Registered(str(name or "history"), fetch, bool(remote))]


def unregister() -> None:
    _SOURCE.clear()


def registered() -> str:
    """The registered source's name, or ""."""
    return _SOURCE[0].name if _SOURCE else ""


# --------------------------------------------------------------------------
# Asking
# --------------------------------------------------------------------------
def limit_for(minutes: int) -> int:
    """How many entries an episode of this length reads: more time buys more
    of what came before ([depth-not-words]), never more of the same."""
    per = max(1, int(settings.history_entries_per_minute))
    return min(int(settings.history_max_entries), per * max(1, int(minutes or 1)))


def query_for(plan) -> Optional[HistoryQuery]:
    """The store's inputs, from the brief. None when there is no brief: a
    store asked with the raw words alone is asked to do EI's job."""
    brief = getattr(plan, "brief", None)
    if brief is None or getattr(brief, "degraded", False):
        return None
    entities = tuple(e.strip() for e in str(
        getattr(brief, "live_entity", "") or "").split(",") if e.strip())
    return HistoryQuery(
        query=str(getattr(plan, "query", "") or ""),
        subject=str(getattr(brief, "subject", "") or ""),
        entities=entities,
        category_path=tuple(getattr(brief, "category_path", ()) or ()),
        intent=str(getattr(brief, "intent", "") or ""),
        place=str(getattr(brief, "place", "") or ""),
        recency_days=int(getattr(brief, "recency_days", 0) or 0),
        must_establish=tuple(str(m) for m in (
            getattr(brief, "must_establish", None) or [])),
        limit=limit_for(getattr(plan, "minutes", 1)))


_BY_PATH: dict = {"search": collections.Counter(), "ahead": collections.Counter()}
_SECONDS: dict = {"search": collections.deque(maxlen=200),
                  "ahead": collections.deque(maxlen=200)}
FAILURES = ("timeout", "failed")


def note(packet: HistoryPacket, path: str, query: str = "") -> HistoryPacket:
    """Count how one lookup ended. Public for `late`, which the caller knows."""
    _BY_PATH.setdefault(path, collections.Counter())[packet.outcome] += 1
    if packet.outcome not in ("off", "no_source", "staging"):
        _SECONDS.setdefault(path, collections.deque(maxlen=200)).append(
            round(packet.seconds, 3))
        import pipeline_errors

        pipeline_errors.step("history", path, packet.outcome, packet.seconds)
        if packet.outcome in FAILURES:
            # A notice, not an error: the episode is written from packet one,
            # exactly as it was before the store existed.
            pipeline_errors.record("research", f"history {packet.outcome}",
                                   packet.detail, query, pipeline_errors.NOTICE)
    return packet


async def lookup(plan, ahead: bool = False) -> HistoryPacket:
    """Ask the store about the subject's past. Never raises."""
    started = time.monotonic()
    path = "ahead" if ahead else "search"
    query = str(getattr(plan, "query", "") or "")

    def done(outcome: str, entries=(), source: str = "", detail: str = ""):
        return note(HistoryPacket(outcome, list(entries), source,
                                  time.monotonic() - started, detail,
                                  returned=len(entries)), path, query)

    if not settings.history_packet:
        return done("off")
    if not _SOURCE:
        return done("no_source")
    source = _SOURCE[0]
    if source.remote:
        import spend_guard

        if spend_guard.enabled():
            return done("staging", source=source.name,
                        detail="a remote store is not asked on staging")
    asked = query_for(plan)
    if asked is None:
        return done("off", source=source.name, detail="no brief to ask with")
    timeout = float(settings.history_ahead_timeout_seconds if ahead
                    else settings.history_timeout_seconds)
    try:
        found = await asyncio.wait_for(source.fetch(asked), timeout)
    except asyncio.TimeoutError:
        return done("timeout", source=source.name,
                    detail=f"no answer within {timeout:g}s")
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - packet one is the fallback
        return done("failed", source=source.name,
                    detail=f"{type(exc).__name__}: {exc}")
    entries = [e for e in (found or []) if isinstance(e, HistoryEntry)]
    return done("found" if entries else "empty", entries, source.name)


# --------------------------------------------------------------------------
# Consolidating the two packets
# --------------------------------------------------------------------------
def _utc(when: Optional[datetime]) -> Optional[datetime]:
    if when is None:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def consolidate(packet: Optional[HistoryPacket], plan,
                now: Optional[datetime] = None) -> Optional[HistoryPacket]:
    """Which of packet two the writer reads, beside packet one.

    Newest first; undated, empty and repeated entries out; entries inside
    the brief's window out when the search found evidence for it; at most
    `limit_for(minutes)` entries and `HISTORY_MAX_CHARS` characters. None
    when nothing is left - the writer's prompt is then exactly what it was
    before the store existed.
    """
    if packet is None or not packet.entries:
        return None
    now = now or datetime.now(timezone.utc)
    brief = getattr(plan, "brief", None)
    window = int(getattr(brief, "recency_days", 0) or 0)
    fresh = bool(str(getattr(plan, "evidence", "") or "").strip())
    cutoff = now - timedelta(days=window) if (window and fresh) else None
    dropped: collections.Counter = collections.Counter()
    seen: set = set()
    kept: list = []
    for entry in sorted(packet.entries,
                        key=lambda e: _utc(e.when) or datetime.min.replace(
                            tzinfo=timezone.utc), reverse=True):
        when = _utc(entry.when)
        text = " ".join(str(entry.text or "").split())
        same = (entry.ref or "") or text.lower()
        if not text:
            dropped["empty"] += 1
        elif when is None:
            dropped["undated"] += 1
        elif when > now + timedelta(days=1):
            dropped["future"] += 1
        elif cutoff is not None and when >= cutoff:
            dropped["inside_window"] += 1
        elif same in seen:
            dropped["repeat"] += 1
        else:
            seen.add(same)
            kept.append(dataclasses.replace(entry, text=text, when=when))
    limit = limit_for(getattr(plan, "minutes", 1))
    if len(kept) > limit:
        dropped["over_limit"] += len(kept) - limit
        kept = kept[:limit]
    budget, total, fits = int(settings.history_max_chars), 0, []
    for entry in kept:
        total += len(entry.text) + len(entry.title)
        if fits and total > budget:
            dropped["over_budget"] += 1
            continue
        fits.append(entry)
    return dataclasses.replace(packet, entries=fits, dropped=dict(dropped),
                               returned=len(packet.entries)) if fits else None


def as_prompt_block(packet: Optional[HistoryPacket],
                    now: Optional[datetime] = None) -> str:
    """Packet two as the writer reads it, or "". Dates computed here."""
    if not packet:
        return ""
    import research

    records = []
    for number, entry in enumerate(packet.entries, 1):
        when = _utc(entry.when)
        dated = (f"{when:%d %B %Y} ({research.age_phrase(when, now)})"
                 if when else "date not stated")
        records.append(
            f"RECORD {number}\nAbout: {entry.title.strip()}\nDated: {dated}\n"
            f"What it is: {KINDS.get(entry.kind, KINDS['record'])}\n"
            f"{entry.text}")
    return """
FAM also keeps a record of this subject's past, and the entries below come
from it. They are background: what came before, so the episode can say how
things got here. Each one was true when it was dated, and only then.

- Anything that changes - a result, a standing, a price, who holds a job - is
  taken from the passages above and the live state, never from these. Where
  the two differ, the newer one wins and the older is history, if it matters
  at all.
- Use them for what led up to now, what was said before and how it turned
  out, a pattern the subject has repeated, a settled fact. Leave out anything
  the story does not need: they are offered, never owed.
- They carry their dates for your reasoning: say "last spring" or "in 2019",
  never the record, and never a word about where they came from.

<background>
""" + "\n\n".join(records) + "\n</background>\n"


def attribution(packet: Optional[HistoryPacket]):
    """The store, credited on the sources panel when the writer read it."""
    if not packet:
        return None
    import provenance

    newest = max((_utc(e.when) for e in packet.entries if e.when), default=None)
    return provenance.Attribution(
        label=packet.source or "FAM archive", kind=provenance.ARCHIVE,
        tier="FAM's own record - background, not today's news",
        at=newest.date().isoformat() if newest else "")


def report() -> dict:
    """For /api/health: on or off, which source, how each lookup ended."""
    def spread(values) -> dict:
        values = sorted(values)
        if not values:
            return {"n": 0}
        return {"n": len(values), "median": values[len(values) // 2],
                "slowest": values[-1]}

    return {
        "enabled": bool(settings.history_packet),
        "source": registered() or None,
        "timeout_seconds": settings.history_timeout_seconds,
        "grace_seconds": settings.history_grace_seconds,
        "since_boot": {p: dict(c) for p, c in _BY_PATH.items()},
        "seconds_by_path": {p: spread(v) for p, v in _SECONDS.items()},
    }
