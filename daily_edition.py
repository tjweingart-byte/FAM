"""The DailyFAM edition: every mix's episodes, written before anybody taps.

What it is (§143, at the owner's direction)
-------------------------------------------
A DailyFAM mix holds subjects, never audio, and every play of one is that
day's edition of each subject (`mixes.daily_prompt`). Until this existed, a
play was an ordinary on-tap episode: the brief, the retrieval, the writer's
thinking and the voice all in front of the first word, which is the wait
CLAUDE.md says the browse surfaces must not pay. Prefetch warmed at most a
brief for a mix, and only when its owner happened to open myFAM.

So DailyFAM is an **edition** now, the way Trending is (`trending_bank.py`):

* **Built on a clock.** At `DAILY_EDITION_HOURS` on `DAILY_EDITION_TIMEZONE`'s
  wall clock (05:00 Eastern by default), one episode is written for every
  distinct subject across every mix on the deployment, into the shared script
  cache under the exact key a tap computes. A tap is an ordinary cache hit.
* **One clock for the date, and it is the server's.** A daily prompt carries
  the day it is about, and the day is part of the cache key. The interface
  used to fill it with the listener's own date and prefetch with the server's
  UTC one, so every warm made after about 8pm Eastern was for a day nobody
  asked about. The edition's day is `edition_day()` - the date of the most
  recent edition slot - and `/api/mixes` serves each item's prompt already
  dated with it, so the edition and the tap cannot disagree.
* **One length.** Minutes are in the key too, and a DailyFAM tap used to ask
  at the *search* length while prefetch warmed at myFAM's. The edition writes
  at `config.BROWSE_MINUTES` (two, §147), and `/api/mixes` says so.
* **Shared.** Two listeners following the Eagles get one episode a day, not
  two: subjects are deduplicated by the words a tap sends, and the most-
  followed are written first, so a ceiling that binds costs the rarest.
* **A subject added between editions is written at once**, in the
  background, when its mix is saved (`schedule_mix`). "All of DailyFAM" has
  to include the subject somebody added at lunchtime.
* **Only active mixes** (§359). A mix nobody has played, made or edited in
  `DAILY_EDITION_ACTIVE_HOURS` is dormant: skipped here, and written in the
  background when it is next opened (`schedule_open`). A subject an active
  mix follows is written whatever else follows it. No ceiling by default.
* **The rails edition shares this loop** (`run_slot`, `rails_edition.py`),
  with its own hours and slots, origin `"myfam"`.

**Episode intelligence runs on every one.** Each episode goes through
`ScriptGenerator.understand` before anything is retrieved - the same call a
search makes, or the brief prefetch warmed for it - and `stream_sentences`
then retrieves with the brief's query and recency window. A degraded brief
(no key, a timeout) is recorded per episode (`ei` in the edition's report and
on `/api/health`), because an EI that had quietly stopped would otherwise
look identical from outside to one that was working.

Where it departs from a rule, stated rather than buried
-------------------------------------------------------
* **A result-dependent subject is written ahead.** Prefetch never warms a
  script whose answer is a result (§88, §89) - a script written before the
  game is stale once it is played. A DailyFAM prompt asks for *the last 24
  hours as of a date*, and at 05:00 what happened yesterday has happened: the
  live lookup runs as on any tap, so a finished game is settled by the
  scoreboard rather than by an article. The one state still refused is a
  game **in progress** at write time - that episode is kept (every episode
  is, §143) but never current, so the tap writes a fresh one.
* **An edition episode is current until the next edition**, plus an hour,
  however short `ttl_for` would make it - the same bargain the Trending bank
  makes, and for the same reason: `CACHE_TTL_VOLATILE` would expire a 05:00
  briefing by 05:15. Its sourced time is stored beside it (§143), so what it
  is and when it was true are both on the record.

The rest is the house style: nothing on a request path builds anything, a
slot is *claimed* in the database before anything is spent so two workers
never build it twice, a build that fails leaves a row saying why, and a
missing writer (`DEMO_MODE`, no key) is reported rather than papered over.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3

import db as store_db
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import config
from paths import data_path

log = logging.getLogger(__name__)

BUILDING = "building"
READY = "ready"
FAILED = "failed"

#: A claim this old with nothing finished is a crashed builder.
STALE_CLAIM_SECONDS = 20 * 60
#: How long an edition episode stays current past the next edition, so a tap
#: in the minutes a new edition is being written still finds today's.
EPISODE_GRACE_SECONDS = 3600
#: Episodes written at once. An edition is hundreds of model calls; one at a
#: time would take hours, and dozens at once would compete with listeners.
CONCURRENCY = 3
#: What a batched edition reserves against `DAILY_EDITION_MAX_DOLLARS` for
#: each writer before the batch returns (§179): a 2-minute writer at batch
#: price, rounded up (~$0.016 at list, halved, plus room for a longer script).
BATCH_WRITER_RESERVE_USD = 0.02
#: What a build cut short by a shutdown writes, so the next boot retries.
INTERRUPTED = "interrupted: the server stopped during the build"
#: What an edition says on a server that cannot write (demo mode, no key).
NO_WRITER = ("no writer on this server (demo mode or no key); every DailyFAM "
             "episode is written on the tap")


def _settings():
    # Read at call time: the suite swaps `config.settings` per test.
    return config.settings


# --------------------------------------------------------------------------
# The clock
# --------------------------------------------------------------------------
#: Zones that could not be loaded, so the fallback is announced once per
#: process rather than on every `/api/mixes` item that asks for the day.
_BAD_ZONES: set = set()


def zone():
    """The zone editions are scheduled in. UTC, loudly, if it cannot be read."""
    name = _settings().daily_edition_timezone or "America/New_York"
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception as exc:  # noqa: BLE001 - a clock must never stop a boot
        if name not in _BAD_ZONES:
            _BAD_ZONES.add(name)
            log.error("daily edition: time zone %r is unavailable (%s); "
                      "scheduling in UTC instead. Install tzdata.", name, exc)
        return timezone.utc


def hours() -> list:
    """The build hours, sorted, deduplicated, and each a real hour."""
    out = set()
    for part in (_settings().daily_edition_hours or "").split(","):
        try:
            hour = int(part.strip())
        except ValueError:
            continue
        if 0 <= hour <= 23:
            out.add(hour)
    return sorted(out) or [5]


def _slots_around(now: float, hrs=None) -> list:
    tz = zone()
    local = datetime.fromtimestamp(now, tz)
    out = []
    for offset in (-1, 0, 1):
        day = (local + timedelta(days=offset)).date()
        for hour in (hrs or hours()):
            out.append(datetime(day.year, day.month, day.day, hour, tzinfo=tz))
    return sorted(out, key=lambda d: d.timestamp())


def last_slot(now: Optional[float] = None, hrs=None) -> datetime:
    """The most recent edition time at or before `now`. `hrs` is another
    edition's hours on the same zone (the rails edition's, §359)."""
    now = time.time() if now is None else now
    return [s for s in _slots_around(now, hrs) if s.timestamp() <= now][-1]


def next_slot(now: Optional[float] = None, hrs=None) -> datetime:
    """The next edition time after `now`."""
    now = time.time() if now is None else now
    return [s for s in _slots_around(now, hrs) if s.timestamp() > now][0]


def slot_id(slot: datetime) -> str:
    return slot.isoformat(timespec="minutes")


def edition_day(now: Optional[float] = None) -> date:
    """The day the current edition is about: the date of its slot.

    Before the first edition of a day it is still yesterday's - a listener at
    3am is offered the edition that exists, not one nobody has written.
    """
    return last_slot(now).date()


def minutes() -> int:
    """The one length DailyFAM episodes are written and played at: two
    minutes, like every episode searchFAM did not ask for (§147)."""
    import config

    return config.BROWSE_MINUTES


def prompt_for(item, now: Optional[float] = None) -> str:
    """What a tap on this mix item sends, dated for the current edition."""
    import mixes

    return mixes.prompt_for(item, edition_day(now))


# --------------------------------------------------------------------------
# What to write
# --------------------------------------------------------------------------
def active_hours() -> float:
    """How recently a mix must have been played (or made, or edited) for the
    edition to write it (§359). 0 writes every mix, as before."""
    return max(0.0, float(_settings().daily_edition_active_hours or 0.0))


def is_active(mix, now: Optional[float] = None) -> bool:
    """Whether the edition writes this mix ahead, or leaves it dormant."""
    now = time.time() if now is None else now
    check = getattr(mix, "is_active", None)
    return True if check is None else bool(check(now, active_hours()))


def subjects(mix_store, now: Optional[float] = None,
             include_dormant: bool = False) -> list:
    """Every distinct thing a DailyFAM tap could send today from an **active**
    mix, most-followed first. `[{"query", "mixes", "title"}]`.

    Deduplicated by the words, because the words are the key: two mixes
    following the same subject share one episode. A question that is nobody
    else's business (`cache.is_shareable`) is left out - the cache would not
    keep it for the tap either, so writing it ahead is money for nothing.

    **A dormant mix is skipped** (§359): one nobody has played, made or
    edited in `DAILY_EDITION_ACTIVE_HOURS` (48) is written when it is next
    opened (`schedule_open`), not every morning. Its subjects still get
    written when any active mix follows them - that is the dedup above -
    but it never pulls in the rest of itself. `include_dormant` counts
    every mix, for a reader that wants followers rather than what to write.
    """
    from cache import is_shareable

    now = time.time() if now is None else now
    counts: dict = {}
    titles: dict = {}
    for mix in mix_store.all_mixes():
        if not include_dormant and not is_active(mix, now):
            continue
        seen_here: set = set()
        for item in getattr(mix, "items", []) or []:
            query = (prompt_for(item, now) or "").strip()
            if not query or query in seen_here or not is_shareable(query):
                continue
            seen_here.add(query)
            counts[query] = counts.get(query, 0) + 1
            titles.setdefault(query, getattr(item, "title", "") or query)
    ordered = sorted(counts, key=lambda q: (-counts[q], q))
    return [{"query": q, "mixes": counts[q], "title": titles[q]} for q in ordered]


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------
class EditionStore:
    """Edition claims and reports, beside the mixes they were built from."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = data_path("MIXES_DB", "mixes.db", path)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS daily_editions (
                slot TEXT PRIMARY KEY, status TEXT NOT NULL,
                claimed_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                built_at REAL, detail TEXT, report TEXT)""")

    def _connect(self) -> sqlite3.Connection:
        db = store_db.connect(self.path, timeout=10, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def claim(self, slot: str, now: float, force: bool = False) -> bool:
        """Take the right to build `slot`. True for exactly one caller.

        A ready slot is re-claimed only by `force`; a failed one after
        `DAILY_EDITION_RETRY_SECONDS` or by `force`; a building one only once
        it has gone quiet - never by force, because a live builder is
        spending, and a second beside it would spend everything twice.
        """
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT status, claimed_at FROM daily_editions "
                                 "WHERE slot = ?", (slot,)).fetchone()
                if row is None:
                    db.execute("INSERT INTO daily_editions (slot, status, "
                               "claimed_at, attempts) VALUES (?, ?, ?, 1)",
                               (slot, BUILDING, now))
                    db.execute("COMMIT")
                    return True
                status, claimed_at = row
                quiet = now - float(claimed_at or 0.0)
                allowed = (
                    (status == BUILDING and quiet >= STALE_CLAIM_SECONDS)
                    or (status == FAILED and (
                        force or quiet >= _settings().daily_edition_retry_seconds))
                    or (status == READY and force))
                if not allowed:
                    db.execute("ROLLBACK")
                    return False
                db.execute("UPDATE daily_editions SET status = ?, claimed_at = ?, "
                           "attempts = attempts + 1 WHERE slot = ?",
                           (BUILDING, now, slot))
                db.execute("COMMIT")
                return True
            except Exception:
                db.execute("ROLLBACK")
                raise

    def touch(self, slot: str, now: float) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE daily_editions SET claimed_at = ? WHERE slot = ? "
                       "AND status = ?", (now, slot, BUILDING))

    def finish(self, slot: str, built_at: float, report: dict) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE daily_editions SET status = ?, built_at = ?, "
                       "detail = ?, report = ? WHERE slot = ?",
                       (READY, built_at, report.get("detail", ""),
                        json.dumps(report), slot))

    def fail(self, slot: str, detail: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE daily_editions SET status = ?, detail = ? "
                       "WHERE slot = ?", (FAILED, detail[:500], slot))

    def status(self, slot: str) -> Optional[dict]:
        with self._connect() as db:
            row = db.execute("SELECT status, claimed_at, attempts, built_at, "
                             "detail, report FROM daily_editions WHERE slot = ?",
                             (slot,)).fetchone()
        if row is None:
            return None
        out = dict(zip(("status", "claimed_at", "attempts", "built_at",
                        "detail"), row[:5]))
        try:
            out["report"] = json.loads(row[5]) if row[5] else None
        except ValueError:
            out["report"] = None
        return out

    def clear(self) -> int:
        with self._lock, self._connect() as db:
            n = db.execute("SELECT COUNT(*) FROM daily_editions").fetchone()[0]
            db.execute("DELETE FROM daily_editions")
        return int(n)


_STORE: Optional[EditionStore] = None
_BUILDING = False
#: Queries being written right now in this process, by anybody - the edition
#: or a freshly saved mix - so the same subject is never written twice at once.
_IN_FLIGHT: set = set()
#: Background tasks started by `schedule_mix`, held so none is collected
#: mid-flight.
_TASKS: set = set()
#: Episodes written outside an edition (`schedule_mix`), per slot, so the
#: edition's ceiling covers them too.
_EXTRA: dict = {}
#: Queries a saved mix has already asked for, per slot. A subject is tried
#: once per edition from a save - a failed or refused one is the tap's to
#: retry, not every later edit of the mix.
_TRIED: dict = {}
#: What the last attempt in this process said, for the health page.
_LAST_ATTEMPT: dict = {}
#: Keys an edition wrote about a game in progress, with when their window
#: ends (§359). Such an episode is current for `EDITION_IN_PROGRESS_SECONDS`
#: only, so nothing may stretch it to the edition's window: a second edition
#: writer finding it current (a forced re-run, an opened or saved mix, the
#: other edition) leaves it alone.
_IN_PROGRESS: dict = {}


def _short_lived(key: str, now: Optional[float] = None) -> bool:
    """Whether `key` is an edition's in-progress episode still in its window."""
    now = time.time() if now is None else now
    for old in [k for k, until in _IN_PROGRESS.items() if until <= now]:
        _IN_PROGRESS.pop(old, None)
    return key in _IN_PROGRESS


def store() -> EditionStore:
    global _STORE
    if _STORE is None:
        _STORE = EditionStore()
    return _STORE


def reset(edition_store: Optional[EditionStore] = None) -> None:
    """Forget process state. For tests and after a wipe."""
    global _STORE, _BUILDING
    _STORE = edition_store
    _BUILDING = False
    _IN_FLIGHT.clear()
    _IN_PROGRESS.clear()
    _EXTRA.clear()
    _TRIED.clear()
    _LAST_ATTEMPT.clear()


# --------------------------------------------------------------------------
# Writing one episode
# --------------------------------------------------------------------------
def _ei_state(brief) -> str:
    if brief is None:
        return "off"
    return "degraded" if getattr(brief, "degraded", True) else "ok"


#: How many earlier editions of a subject a new one is told about. The cache
#: keeps every episode a week (§143), so six days back is everything it can
#: still name.
EARLIER_EDITIONS = 6


async def with_earlier_editions(plan, cache):
    """`plan` with the titles of its earlier daily editions in `covered`.

    For a dated DailyFAM prompt only: the same words with each earlier day
    in place of today's are exactly the earlier editions' cache keys
    (`mixes.earlier_prompts`), so this is a few local reads and no model
    call. Anything else comes back unchanged. Never raises - knowing what
    was said yesterday improves an episode and must never prevent one.

    The key is computed without the semantic canonicaliser: that is a model
    call per day looked up, on a tap's path. With `CACHE_SEMANTIC_KEY=1`
    (off by default) these lookups therefore find nothing, and the episode
    is written exactly as it was before this existed.
    """
    import dataclasses

    import mixes
    from pipeline import key_for

    if cache is None or getattr(plan, "covered", ()) or getattr(plan, "attachments", ()):
        return plan
    try:
        titles = []
        for earlier in mixes.earlier_prompts(plan.query, EARLIER_EDITIONS):
            key = await key_for(dataclasses.replace(plan, query=earlier), None)
            title = (getattr(cache, "title", lambda _k: "")(key) or "").strip() if key else ""
            if title and title.lower() not in (t.lower() for t in titles):
                titles.append(title)
    except Exception as exc:  # noqa: BLE001 - see the docstring
        log.warning("daily edition: could not read earlier editions of %r: %s",
                    plan.query, exc)
        return plan
    return dataclasses.replace(plan, covered=tuple(titles)) if titles else plan


class _Pending:
    """One episode between its brief and its words (§179).

    What `_start` hands `_finish`: everything a write needs that was decided
    before the writer ran, so a batched edition can prepare every episode,
    send the writers together, and finish each one from its answer.
    """

    def __init__(self, query: str, plan, key: str, notes,
                 origin: str = "dailyfam") -> None:
        self.query = query
        #: Which edition wrote it (§359): "dailyfam" for the mixes', "myfam"
        #: for the rails'. Stamped on the episode and on every audit row.
        self.origin = origin
        self.plan = plan
        self.key = key
        self.notes = notes
        self.ei = _ei_state(plan.brief)
        #: The plan `prepare` returned (brief and evidence in it), once a
        #: batched edition has prepared it; the writer is asked from this.
        self.prepared = None
        self.params: dict = {}
        #: The start of the edition being built, for the re-check on return.
        self.since = 0.0
        #: Set once `_write_batch` has finished it, so the clean-up after the
        #: batch does not discard a claim somebody else has taken since.
        self.done = False


async def _start(query: str, generator, cache, length: int,
                 current_until: float, since: float, origin: str = "dailyfam"):
    """Everything before the writer: a `_Pending`, or a finished result.

    Leaves `query` in `_IN_FLIGHT` when it returns a `_Pending`; the caller
    takes it out once the episode is finished either way.
    """
    from pipeline import key_for
    from script_generator import ScriptNotes, plan_episode

    if query in _IN_FLIGHT:
        return {"status": "in_flight", "key": ""}
    _IN_FLIGHT.add(query)
    try:
        plan = plan_episode(query, length)
        key = await key_for(plan, getattr(generator, "client", None))
        if not key:
            _IN_FLIGHT.discard(query)
            return {"status": "failed", "key": "", "detail": "no cache key"}
        if cache.get(key):
            sourced = getattr(cache, "sourced_at", lambda _k: None)(key) or 0.0
            if sourced >= since:
                # This edition's already - or a tap got there first, sourced
                # just as recently but current for only `ttl_for`'s fifteen
                # minutes. Give it the edition's window rather than pay for
                # the same episode twice - unless it is a game in progress,
                # whose window is its own (§359).
                extend = getattr(cache, "extend_current", None)
                if extend is not None and not _short_lived(key):
                    extend(key, current_until)
                _IN_FLIGHT.discard(query)
                return {"status": "cached", "key": key}
        notes = ScriptNotes()
        # Written at 05:00 for a listener who is asleep: the brief may take
        # its time (§268).
        notes.ahead = True
        # And its writer is the editions' own, when one is set (§269).
        notes.edition = True
        # What the earlier editions were, so today's is a different one.
        plan = await with_earlier_editions(plan, cache)
        # Episode intelligence first, exactly as a tap would run it: the
        # retrieval query, the recency window and the story shape all come
        # from this brief. A brief prefetch already warmed is taken instead.
        plan = await generator.understand(plan, notes)
        pending = _Pending(query, plan, key, notes, origin)
        pending.since = since
        return pending
    except BaseException:
        _IN_FLIGHT.discard(query)
        raise


def _finish(pending: "_Pending", sentences: list, cache, length: int,
            current_until: float) -> dict:
    """Put one written episode into the shared cache, and say what happened."""
    import prefetch
    from cache import ttl_for
    from pipeline import bucket_for

    notes, query, key, ei = pending.notes, pending.query, pending.key, pending.ei
    dollars = prefetch._dollars(notes)
    if not sentences:
        return {"status": "failed", "key": key, "dollars": dollars, "ei": ei,
                "detail": "the writer returned nothing"}
    now = time.time()
    if (notes.live_status or "") == "in_progress":
        # Kept, like every episode (§143), with its title, hook and category
        # held on the tile; current only for `EDITION_IN_PROGRESS_SECONDS`
        # after it was written (§359, at the owner's direction - 0 restores
        # "never current"). A tap after that window writes a new episode.
        ttl = max(0, int(_settings().edition_in_progress_seconds or 0))
        if ttl:
            _IN_PROGRESS[key] = now + ttl
    else:
        ttl = max(ttl_for(query, live_status=notes.live_status,
                          outcome_dependent=notes.outcome_dependent,
                          recency_days=notes.recency_days),
                  int(current_until - now), 60)
    sources = notes.provenance.to_json() if notes.provenance is not None else ""
    extra = {"summary": notes.summary} if notes.summary else {}
    if notes.sourced_at:
        extra["sourced_at"] = notes.sourced_at
    if getattr(notes, "category", ""):
        extra["category"] = notes.category   # §189
    # No author: an edition episode was nobody's tap, so it belongs to
    # everybody - the rule prefetch and the Trending bank keep.
    # Written in a voice drawn from the bank (§147): nobody chose one, and
    # the tap plays the episode in the voice kept here.
    import voice_bank

    origin = getattr(pending, "origin", "") or "dailyfam"
    extra["origin"] = origin
    extra["voice"] = voice_bank.random_slug()
    cache.put(key, sentences, ttl, query, notes.thread, length,
              bucket_for(pending.plan), sources, "", notes.title, **extra)
    # What each categoriser said about it (§209), for the audit.
    import category_audit

    category_audit.note(query, getattr(notes, "category", ""), origin)
    # Its articles, for the history archive (§308). Never awaited.
    import history_archive

    history_archive.after_episode(notes, key, notes.sourced_at, origin)
    # And how well it was written (§309), kept beside it.
    import episode_quality

    episode_quality.note(key, notes.sourced_at or now, query, length, origin,
                         sentences, notes)
    in_progress = (notes.live_status or "") == "in_progress"
    return {"status": "volatile" if (in_progress or not ttl) else "written",
            "key": key,
            "dollars": dollars, "ei": ei, "title": notes.title,
            "sourced_at": notes.sourced_at or now}


def _failure(query: str, exc: BaseException, pending=None) -> dict:
    """What a write that raised is recorded as. One episode is not the edition."""
    import prefetch
    import research

    if isinstance(exc, research.NoEvidence):
        # Not a fault: FAM declined to write this from memory, which is the
        # answer a tap would get too. The tap will try again with its own.
        return {"status": "no_evidence", "key": "", "detail": str(exc)[:200]}
    log.warning("daily edition: could not write %r: %s", query, exc)
    import pipeline_errors

    pipeline_errors.record("edition", "DailyFAM episode failed",
                           f"{type(exc).__name__}: {exc}", query,
                           pipeline_errors.severity_for(exc))
    result = {"status": "failed", "key": "",
              "detail": f"{type(exc).__name__}: {exc}"[:200]}
    if pending is not None:
        result["dollars"] = prefetch._dollars(pending.notes)
    return result


async def write_episode(query: str, generator, cache, length: int,
                        current_until: float, since: float = 0.0,
                        origin: str = "dailyfam") -> dict:
    """Write one subject's episode into the shared cache. Never raises.

    `since` is the start of the edition being built: a current episode
    sourced after it is this edition's already (a restart, or a mix saved an
    hour ago) and is left alone; one sourced before it is the last edition's
    and is written again - which matters only when an edition runs twice in
    one day and the words, dated by day, are the same.
    """
    try:
        pending = await _start(query, generator, cache, length,
                               current_until, since, origin)
    except Exception as exc:  # noqa: BLE001 - one episode is not the edition
        return _failure(query, exc)
    if isinstance(pending, dict):
        return pending
    try:
        sentences = [s async for s in generator.stream_sentences(
            pending.plan, pending.notes)]
        return _finish(pending, sentences, cache, length, current_until)
    except Exception as exc:  # noqa: BLE001 - one episode is not the edition
        return _failure(query, exc)
    finally:
        _IN_FLIGHT.discard(query)


def _dollars_so_far(pending: "_Pending") -> float:
    import prefetch

    return float(prefetch._dollars(pending.notes) or 0.0)


async def _prepare_for_batch(query: str, generator, cache, length: int,
                             current_until: float, since: float,
                             origin: str = "dailyfam"):
    """`_start`, then everything the writer needs: a `_Pending` or a result."""
    try:
        pending = await _start(query, generator, cache, length,
                               current_until, since, origin)
    except Exception as exc:  # noqa: BLE001 - one episode is not the edition
        return _failure(query, exc)
    if isinstance(pending, dict):
        return pending
    try:
        pending.prepared, pending.params = await generator.batch_request(
            pending.plan, pending.notes)
        return pending
    except Exception as exc:  # noqa: BLE001
        _IN_FLIGHT.discard(query)
        return _failure(query, exc, pending)


async def _write_batch(pending: list, generator, cache, length: int,
                       current_until: float, report: dict, spent: dict,
                       alive) -> None:
    """Send every prepared writer as one batch; finish each from its answer.

    A writer the batch did not answer - it failed, errored, or was cancelled
    at `EDITION_BATCH_WAIT_SECONDS` - is written live from the brief and
    evidence it already has, `CONCURRENCY` at a time, so a saving never costs
    an episode.
    """
    import claude_batch

    s = _settings()
    ids = {f"e{i}": item for i, item in enumerate(pending)}
    try:
        answers = await claude_batch.run(
            generator.client, {cid: item.params for cid, item in ids.items()},
            wait_seconds=s.edition_batch_wait_seconds,
            poll_seconds=s.edition_batch_poll_seconds, alive=alive)
        gate = asyncio.Semaphore(concurrency())

        async def finish(cid: str, item: "_Pending") -> None:
            before = _dollars_so_far(item)
            message = answers.get(cid)
            try:
                if _tapped_meanwhile(item, cache, current_until):
                    # A listener tapped this subject while the batch was out
                    # and the tap wrote it, audio and all. Keep theirs: the
                    # answer arriving now would archive it and drop its audio.
                    result = {"status": "cached", "key": item.key,
                              "dollars": _dollars_so_far(item),
                              "detail": "a tap wrote it while the batch was out"}
                elif message is not None:
                    sentences = generator.sentences_from_message(
                        item.prepared, item.notes, message)
                    result = _finish(item, sentences, cache, length,
                                     current_until)
                else:
                    async with gate:
                        alive()
                        sentences = [x async for x in generator.stream_prepared(
                            item.prepared, item.notes)]
                    result = _finish(item, sentences, cache, length,
                                     current_until)
            except Exception as exc:  # noqa: BLE001
                result = _failure(item.query, exc, item)
            finally:
                _IN_FLIGHT.discard(item.query)
                item.done = True
            result["batched"] = message is not None and result.get("status") != "cached"
            if result.get("status") not in ("written", "volatile"):
                spent["n"] -= 1
            spent["usd"] += (float(result.get("dollars") or 0.0) - before
                             - BATCH_WRITER_RESERVE_USD)
            result["mixes"] = item.mixes
            report["episodes"][item.query] = result

        await asyncio.gather(*(finish(cid, item) for cid, item in ids.items()))
    finally:
        # Only what was never finished (a cancelled build): a finished one
        # let go of its claim already, and it may have been taken since.
        for item in pending:
            if not item.done:
                _IN_FLIGHT.discard(item.query)


def _tapped_meanwhile(item: "_Pending", cache, current_until: float) -> bool:
    """Whether this episode was written for this edition since it was prepared.

    The same test `_start` makes, made again when a batched answer returns -
    up to `EDITION_BATCH_WAIT_SECONDS` later - and with the same consequence:
    the edition's window is given to what is there instead of writing over it.
    """
    try:
        if not cache.get(item.key):
            return False
        sourced = getattr(cache, "sourced_at", lambda _k: None)(item.key) or 0.0
        if sourced < item.since:
            return False
        extend = getattr(cache, "extend_current", None)
        if extend is not None and not _short_lived(item.key):
            extend(item.key, current_until)
        return True
    except Exception:  # noqa: BLE001 - a failed check writes, as before
        return False


def _batches(generator) -> bool:
    """Whether this edition's writers go out as one batch (§179)."""
    return bool(_settings().edition_batch
                and getattr(generator, "client", None) is not None
                and hasattr(generator, "batch_request")
                and hasattr(generator, "sentences_from_message")
                and hasattr(generator, "stream_prepared"))


def _current_until(now: float, hrs=None) -> float:
    return next_slot(now, hrs).timestamp() + EPISODE_GRACE_SECONDS


# --------------------------------------------------------------------------
# Building an edition
# --------------------------------------------------------------------------
async def build(mix_store, generator=None, cache=None,
                now: Optional[float] = None, force: bool = False) -> Optional[dict]:
    """Write the current slot's edition, if this caller wins the claim.

    Returns the edition's report, or None when another builder has it or it
    is already built. Never raises.
    """
    global _BUILDING
    s = _settings()
    now = time.time() if now is None else now
    if not s.daily_edition:
        return None
    slot = last_slot(now)
    sid = slot_id(slot)
    if generator is None or cache is None:
        # Nothing is claimed or stored: a slot marked built with nothing
        # written would stop today's edition being written once a key
        # arrives and the server restarts.
        return {"slot": sid, "day": edition_day(now).isoformat(),
                "minutes": minutes(), "subjects": len(subjects(mix_store, now)),
                "episodes": {}, "skipped_for_ceiling": 0,
                "detail": NO_WRITER}
    def claimed() -> None:
        global _BUILDING
        _BUILDING = True

    try:
        def wanted() -> list:
            return subjects(mix_store, now)

        def extra(report: dict) -> None:
            report["day"] = edition_day(now).isoformat()
            report["dormant_mixes"] = dormant_count(mix_store, now)

        report = await run_slot(
            sid, slot, now, wanted, generator, cache,
            until=_current_until(now), length=minutes(), origin="dailyfam",
            max_episodes=s.daily_edition_max_episodes,
            max_dollars=s.daily_edition_max_dollars, force=force,
            label="daily edition", extra=extra, on_claimed=claimed)
        if report is not None:
            _LAST_ATTEMPT.clear()
        return report
    finally:
        _BUILDING = False


def dormant_count(mix_store, now: Optional[float] = None) -> int:
    """How many mixes the edition left dormant (§359), for its report."""
    now = time.time() if now is None else now
    try:
        return sum(1 for m in mix_store.all_mixes() if not is_active(m, now))
    except Exception:  # noqa: BLE001 - a count never fails an edition
        return 0


async def run_slot(sid: str, slot: datetime, now: float, wanted, generator,
                   cache, until: float, length, origin: str,
                   max_episodes: int = 0, max_dollars: float = 0.0,
                   force: bool = False, label: str = "edition",
                   extra=None, on_written=None, on_claimed=None,
                   failures: Optional[dict] = None) -> Optional[dict]:
    """Claim `sid` and write every subject `wanted()` returns into the cache.

    The one edition loop, shared by the mixes' edition and the rails' (§359):
    the claim, the ceilings (0 means none), the batch, the report. `wanted`
    is called after the claim so a losing caller reads nothing. `on_written`
    is called with each subject whose episode was written or is current;
    `on_claimed` once the claim is won, before anything is spent.
    Returns the report, or None when another builder has the slot (or the
    build failed - `failures`, when given, then holds why). Never raises.
    """
    failures = _LAST_ATTEMPT if failures is None else failures
    s = _settings()
    try:
        if not store().claim(sid, now, force=force):
            return None
    except Exception as exc:  # noqa: BLE001
        log.error("%s: could not claim %s: %s", label, sid, exc)
        return None
    if on_claimed is not None:
        on_claimed()

    started = time.monotonic()
    report: dict = {"slot": sid, "minutes": length, "subjects": 0,
                    "episodes": {}, "skipped_for_ceiling": 0, "detail": ""}
    try:
        subjects_now = list(wanted())
        report["subjects"] = len(subjects_now)
        if extra is not None:
            extra(report)

        ceiling_n = max(0, int(max_episodes or 0))
        ceiling_usd = max(0.0, float(max_dollars or 0.0))
        # Episodes are reserved before each write; dollars are only known
        # after one, so the dollar ceiling can be passed by at most the
        # episodes already in flight (`concurrency()`). Batched (§179), the
        # writer is not paid until the batch returns, so each prepared
        # episode reserves `BATCH_WRITER_RESERVE_USD` for it up front and the
        # reservation is swapped for the real figure when its answer lands.
        spent = {"n": 0, "usd": 0.0}
        gate = asyncio.Semaphore(concurrency())
        # Batched (§179): each episode is prepared here as it always was,
        # under the same gate and ceilings, and its writer waits in
        # `pending` to go out with the rest as one batch.
        batching = _batches(generator)
        pending: list = []
        by_query = {subject["query"]: subject for subject in subjects_now}

        def alive() -> None:
            store().touch(sid, now + (time.monotonic() - started))

        async def one(subject: dict) -> None:
            query = subject["query"]
            async with gate:
                if ((ceiling_n and spent["n"] >= ceiling_n)
                        or (ceiling_usd and spent["usd"] >= ceiling_usd)):
                    report["skipped_for_ceiling"] += 1
                    report["episodes"][query] = {"status": "ceiling"}
                    return
                # Reserved before the write, not counted after it: with
                # several writing at once, counting afterwards lets every one
                # of them pass a ceiling of one.
                spent["n"] += 1
                alive()
                if batching:
                    result = await _prepare_for_batch(
                        query, generator, cache, length, until,
                        since=slot.timestamp(), origin=origin)
                    if isinstance(result, _Pending):
                        # The brief and the evidence are spent already; the
                        # writer is added when its answer comes back.
                        result.mixes = subject.get("mixes", 0)
                        spent["usd"] += (_dollars_so_far(result)
                                         + BATCH_WRITER_RESERVE_USD)
                        pending.append(result)
                        return
                else:
                    result = await write_episode(query, generator, cache,
                                                 length, until,
                                                 since=slot.timestamp(),
                                                 origin=origin)
                if result.get("status") not in ("written", "volatile"):
                    spent["n"] -= 1
                spent["usd"] += float(result.get("dollars") or 0.0)
                result["mixes"] = subject.get("mixes", 0)
                report["episodes"][query] = result

        await asyncio.gather(*(one(subject) for subject in subjects_now))
        if pending:
            await _write_batch(pending, generator, cache, length, until,
                               report, spent, alive)
        if on_written is not None:
            for query, result in report["episodes"].items():
                # `in_flight`: the other edition is writing the same words.
                if result.get("status") in ("written", "volatile", "cached",
                                            "in_flight") \
                        and query in by_query:
                    try:
                        on_written(by_query[query], result)
                    except Exception:  # noqa: BLE001 - a hook never fails a build
                        log.exception("%s: after-write hook failed for %r",
                                      label, query)
        report.update(_summarise(report["episodes"]))
        report["dollars"] = round(spent["usd"], 4)
        store().finish(sid, now, report)
        log.info("%s %s: %d subjects, %d written, %d already cached, "
                 "%d past the ceiling, EI ok on %d, $%.4f", label, sid,
                 len(subjects_now), report["written"], report["cached"],
                 report["skipped_for_ceiling"], report["ei_ok"],
                 report["dollars"])
        return report
    except asyncio.CancelledError:
        try:
            store().fail(sid, INTERRUPTED)
        except Exception:  # noqa: BLE001
            pass
        raise
    except Exception as exc:  # noqa: BLE001 - a build never takes the server down
        log.exception("%s: the build for %s failed", label, sid)
        try:
            store().fail(sid, f"{type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001
            pass
        failures.update(slot=sid, detail=str(exc), at=now)
        return None


def concurrency() -> int:
    """Episodes written at once by an edition: `EDITION_CONCURRENCY` (§359).

    With the ceilings lifted an edition can be thousands of episodes, and
    three at a time would still be writing the 05:00 edition at noon."""
    return max(1, int(_settings().edition_concurrency or CONCURRENCY))


def _summarise(episodes: dict) -> dict:
    statuses = [e.get("status") for e in episodes.values()]
    eis = [e.get("ei") for e in episodes.values() if e.get("ei")]
    return {
        "written": statuses.count("written"),
        "volatile": statuses.count("volatile"),
        "cached": statuses.count("cached"),
        "no_evidence": statuses.count("no_evidence"),
        "failed": statuses.count("failed"),
        # How many of the episodes written were built on a real brief. A
        # number below `written` is EI falling back, and this is where it
        # shows.
        "ei_ok": eis.count("ok"),
        "ei_degraded": eis.count("degraded"),
        "ei_off": eis.count("off"),
        # Written from a batched answer at half price (§179). Below `written`
        # on a batching edition means the batch fell back to writing live.
        "batched": sum(1 for e in episodes.values() if e.get("batched")),
    }


def due(now: Optional[float] = None) -> bool:
    """Whether the current slot still wants building."""
    if not _settings().daily_edition:
        return False
    now = time.time() if now is None else now
    row = store().status(slot_id(last_slot(now)))
    if row is None:
        return True
    if row["status"] == READY:
        return False
    if row["status"] == FAILED:
        return (str(row.get("detail") or "").startswith(INTERRUPTED)
                or now - float(row["claimed_at"] or 0)
                >= _settings().daily_edition_retry_seconds)
    return now - float(row["claimed_at"] or 0) >= STALE_CLAIM_SECONDS


async def run_forever(mix_store, generator=None, cache=None,
                      initial_delay: float = 0.0) -> None:
    """Build on the clock, catching up on boot. Runs for the server's life.

    `initial_delay` holds the boot catch-up back (§144), so the edition's
    model calls and searches do not land in the same second as the story
    sweep and the trending bank - which is what timed EI out on 24/09.
    """
    if not _settings().daily_edition:
        log.info("daily edition: off (DAILY_EDITION=0)")
        return
    if generator is None or cache is None:
        # Said once, at boot, rather than every minute: nothing here changes
        # until the server restarts with a key.
        _LAST_ATTEMPT.update(slot=slot_id(last_slot()), detail=NO_WRITER,
                             at=time.time())
        log.warning("daily edition: %s", NO_WRITER)
        return
    log.info("daily edition: at %s %s, %s min an episode; next at %s",
             ", ".join(f"{h:02d}:00" for h in hours()),
             _settings().daily_edition_timezone, minutes(),
             slot_id(next_slot()))
    if initial_delay > 0:
        log.info("daily edition: boot catch-up held %.0fs so it does not start "
                 "in the same second as the story sweep", initial_delay)
        await asyncio.sleep(initial_delay)
    while True:
        try:
            if due():
                row = store().status(slot_id(last_slot()))
                force = bool(row and str(row.get("detail") or "")
                             .startswith(INTERRUPTED))
                await build(mix_store, generator=generator, cache=cache,
                            force=force)
        except Exception:  # noqa: BLE001
            log.exception("daily edition: the scheduler tick failed")
        await asyncio.sleep(max(5.0, min(60.0, next_slot().timestamp() - time.time())))


# --------------------------------------------------------------------------
# A mix saved between editions
# --------------------------------------------------------------------------
def schedule_mix(mix, generator=None, cache=None,
                 now: Optional[float] = None, before=None) -> bool:
    """Write this mix's episodes for the current edition, in the background.

    Called when a mix is created, edited or copied. Never awaited and never
    raises: the listener gets their saved mix back at once, and by the time
    they press play the subjects they just added are being written. Anything
    already current (the edition wrote it, or another mix follows the same
    subject) is skipped by `write_episode` for the price of a lookup.

    Only what is **new**: `before` is the mix as it was, and a subject it
    already held is the edition's, not this save's - so removing or
    reordering items spends nothing. And each subject is tried from a save
    at most once per edition (`_TRIED`), so a failed or refused one is not
    retried on every later edit.
    """
    s = _settings()
    if not s.daily_edition or generator is None or cache is None or mix is None:
        return False
    now = time.time() if now is None else now
    from cache import is_shareable

    sid = slot_id(last_slot(now))
    had = {(prompt_for(item, now) or "").strip()
           for item in (getattr(before, "items", []) or [])}
    tried = _TRIED.setdefault(sid, set())
    queries = []
    for item in getattr(mix, "items", []) or []:
        query = (prompt_for(item, now) or "").strip()
        if (query and query not in queries and query not in had
                and query not in tried and is_shareable(query)):
            queries.append(query)
    if not queries:
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    tried.update(queries)
    # Only this slot's: yesterday's set is of no use and would grow forever.
    for old in [k for k in _TRIED if k != sid]:
        _TRIED.pop(old, None)
    since = last_slot(now).timestamp()

    async def _write() -> None:
        until = _current_until(time.time())
        for query in queries:
            ceiling = max(0, int(s.daily_edition_max_episodes or 0))
            row = store().status(sid) or {}
            built = int(((row.get("report") or {}).get("written")) or 0)
            if ceiling and built + _EXTRA.get(sid, 0) >= ceiling:
                log.info("daily edition: ceiling reached; %r is written on the "
                         "tap", query)
                continue
            result = await write_episode(query, generator, cache, minutes(),
                                         until, since=since)
            if result.get("status") in ("written", "volatile"):
                _EXTRA[sid] = _EXTRA.get(sid, 0) + 1
            log.info("daily edition: %s for newly saved subject %r (EI %s)",
                     result.get("status"), query, result.get("ei", "-"))

    async def _guarded() -> None:
        # The edition's clock, whoever's save started this (`listener_clock`).
        import listener_clock

        listener_clock.set_for_request("")
        try:
            await _write()
        except asyncio.CancelledError:  # pragma: no cover - shutdown
            raise
        except Exception:  # noqa: BLE001
            log.exception("daily edition: writing a saved mix failed")

    task = loop.create_task(_guarded())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return True


def schedule_open(mix, generator=None, cache=None,
                  now: Optional[float] = None) -> bool:
    """A dormant mix was opened (§359): write all of it for the current
    edition, in the background. Never awaited and never raises.

    The same path a saved mix takes (`schedule_mix`) with nothing "before",
    so every subject is asked for once per edition and anything already
    current - another mix follows it, or a tap wrote it - costs a lookup."""
    return schedule_mix(mix, generator=generator, cache=cache, now=now)


# --------------------------------------------------------------------------
# What a person needs to see
# --------------------------------------------------------------------------
def report(now: Optional[float] = None) -> dict:
    """For `/api/health`: the schedule, the current edition, and EI's share."""
    now = time.time() if now is None else now
    s = _settings()
    out = {
        "enabled": bool(s.daily_edition),
        "schedule": {"timezone": s.daily_edition_timezone, "hours": hours(),
                     "next": slot_id(next_slot(now)),
                     "current_slot": slot_id(last_slot(now)),
                     "edition_day": edition_day(now).isoformat()},
        "minutes": minutes(),
        "ceilings": {"episodes": s.daily_edition_max_episodes,
                     "dollars": s.daily_edition_max_dollars},
        "active_hours": active_hours(),
        "concurrency": concurrency(),
        "in_progress_seconds": s.edition_in_progress_seconds,
        "building": _BUILDING,
        "written_since_edition": _EXTRA.get(slot_id(last_slot(now)), 0),
        "last_failure": dict(_LAST_ATTEMPT) or None,
    }
    try:
        row = store().status(slot_id(last_slot(now)))
    except Exception as exc:  # noqa: BLE001 - never break the health page
        row = {"error": str(exc)}
    if row and isinstance(row.get("report"), dict):
        # The per-subject detail is long; the health page wants the totals.
        row = dict(row)
        row["report"] = {k: v for k, v in row["report"].items()
                         if k != "episodes"}
    out["current"] = row
    return out
