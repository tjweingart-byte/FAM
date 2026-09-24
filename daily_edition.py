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


def _slots_around(now: float) -> list:
    tz = zone()
    local = datetime.fromtimestamp(now, tz)
    out = []
    for offset in (-1, 0, 1):
        day = (local + timedelta(days=offset)).date()
        for hour in hours():
            out.append(datetime(day.year, day.month, day.day, hour, tzinfo=tz))
    return sorted(out, key=lambda d: d.timestamp())


def last_slot(now: Optional[float] = None) -> datetime:
    """The most recent edition time at or before `now`."""
    now = time.time() if now is None else now
    return [s for s in _slots_around(now) if s.timestamp() <= now][-1]


def next_slot(now: Optional[float] = None) -> datetime:
    """The next edition time after `now`."""
    now = time.time() if now is None else now
    return [s for s in _slots_around(now) if s.timestamp() > now][0]


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
def subjects(mix_store, now: Optional[float] = None) -> list:
    """Every distinct thing a DailyFAM tap could send today, most-followed
    first. `[{"query", "mixes", "title"}]`.

    Deduplicated by the words, because the words are the key: two mixes
    following the same subject share one episode. A question that is nobody
    else's business (`cache.is_shareable`) is left out - the cache would not
    keep it for the tap either, so writing it ahead is money for nothing.
    """
    from cache import is_shareable

    counts: dict = {}
    titles: dict = {}
    for mix in mix_store.all_mixes():
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
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
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


async def write_episode(query: str, generator, cache, length: int,
                        current_until: float, since: float = 0.0) -> dict:
    """Write one subject's episode into the shared cache. Never raises.

    `since` is the start of the edition being built: a current episode
    sourced after it is this edition's already (a restart, or a mix saved an
    hour ago) and is left alone; one sourced before it is the last edition's
    and is written again - which matters only when an edition runs twice in
    one day and the words, dated by day, are the same.
    """
    import prefetch
    import research
    from cache import ttl_for
    from pipeline import bucket_for, key_for
    from script_generator import ScriptNotes, plan_episode

    if query in _IN_FLIGHT:
        return {"status": "in_flight", "key": ""}
    _IN_FLIGHT.add(query)
    try:
        plan = plan_episode(query, length)
        key = await key_for(plan, getattr(generator, "client", None))
        if not key:
            return {"status": "failed", "key": "", "detail": "no cache key"}
        if cache.get(key):
            sourced = getattr(cache, "sourced_at", lambda _k: None)(key) or 0.0
            if sourced >= since:
                # This edition's already - or a tap got there first, sourced
                # just as recently but current for only `ttl_for`'s fifteen
                # minutes. Give it the edition's window rather than pay for
                # the same episode twice.
                extend = getattr(cache, "extend_current", None)
                if extend is not None:
                    extend(key, current_until)
                return {"status": "cached", "key": key}
        notes = ScriptNotes()
        # Episode intelligence first, exactly as a tap would run it: the
        # retrieval query, the recency window and the story shape all come
        # from this brief. A brief prefetch already warmed is taken instead.
        plan = await generator.understand(plan, notes)
        ei = _ei_state(plan.brief)
        sentences = [s async for s in generator.stream_sentences(plan, notes)]
        dollars = prefetch._dollars(notes)
        if not sentences:
            return {"status": "failed", "key": key, "dollars": dollars, "ei": ei,
                    "detail": "the writer returned nothing"}
        now = time.time()
        if (notes.live_status or "") == "in_progress":
            # Kept, like every episode (§143), and never current: a score
            # taken mid-game is not what a tap later should be handed.
            ttl = 0
        else:
            ttl = max(ttl_for(query, live_status=notes.live_status,
                              outcome_dependent=notes.outcome_dependent,
                              recency_days=notes.recency_days),
                      int(current_until - now), 60)
        sources = notes.provenance.to_json() if notes.provenance is not None else ""
        extra = {"summary": notes.summary} if notes.summary else {}
        if notes.sourced_at:
            extra["sourced_at"] = notes.sourced_at
        # No author: an edition episode was nobody's tap, so it belongs to
        # everybody - the rule prefetch and the Trending bank keep.
        # Written in a voice drawn from the bank (§147): nobody chose one,
        # and the tap plays the episode in the voice kept here.
        import voice_bank

        extra["origin"] = "dailyfam"
        extra["voice"] = voice_bank.random_slug()
        cache.put(key, sentences, ttl, query, notes.thread, length,
                  bucket_for(plan), sources, "", notes.title, **extra)
        return {"status": "written" if ttl else "volatile", "key": key,
                "dollars": dollars, "ei": ei, "title": notes.title,
                "sourced_at": notes.sourced_at or now}
    except research.NoEvidence as exc:
        # Not a fault: FAM declined to write this from memory, which is the
        # answer a tap would get too. The tap will try again with its own.
        return {"status": "no_evidence", "key": "", "detail": str(exc)[:200]}
    except Exception as exc:  # noqa: BLE001 - one episode is not the edition
        log.warning("daily edition: could not write %r: %s", query, exc)
        return {"status": "failed", "key": "",
                "detail": f"{type(exc).__name__}: {exc}"[:200]}
    finally:
        _IN_FLIGHT.discard(query)


def _current_until(now: float) -> float:
    return next_slot(now).timestamp() + EPISODE_GRACE_SECONDS


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
    try:
        if not store().claim(sid, now, force=force):
            return None
    except Exception as exc:  # noqa: BLE001
        log.error("daily edition: could not claim %s: %s", sid, exc)
        return None

    _BUILDING = True
    started = time.monotonic()
    report: dict = {"slot": sid, "day": edition_day(now).isoformat(),
                    "minutes": minutes(), "subjects": 0, "episodes": {},
                    "skipped_for_ceiling": 0, "detail": ""}
    try:
        wanted = subjects(mix_store, now)
        report["subjects"] = len(wanted)

        ceiling_n = max(0, int(s.daily_edition_max_episodes or 0))
        ceiling_usd = max(0.0, float(s.daily_edition_max_dollars or 0.0))
        # Episodes are reserved before each write; dollars are only known
        # after one, so the dollar ceiling can be passed by at most the
        # episodes already in flight (`CONCURRENCY`).
        spent = {"n": 0, "usd": 0.0}
        until = _current_until(now)
        gate = asyncio.Semaphore(CONCURRENCY)

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
                store().touch(sid, now + (time.monotonic() - started))
                result = await write_episode(query, generator, cache, minutes(),
                                             until, since=slot.timestamp())
                if result.get("status") not in ("written", "volatile"):
                    spent["n"] -= 1
                spent["usd"] += float(result.get("dollars") or 0.0)
                result["mixes"] = subject["mixes"]
                report["episodes"][query] = result

        await asyncio.gather(*(one(subject) for subject in wanted))
        report.update(_summarise(report["episodes"]))
        report["dollars"] = round(spent["usd"], 4)
        store().finish(sid, now, report)
        _LAST_ATTEMPT.clear()
        log.info("daily edition %s: %d subjects, %d written, %d already cached, "
                 "%d past the ceiling, EI ok on %d, $%.4f", sid, len(wanted),
                 report["written"], report["cached"],
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
        log.exception("daily edition: the build for %s failed", sid)
        try:
            store().fail(sid, f"{type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001
            pass
        _LAST_ATTEMPT.update(slot=sid, detail=str(exc), at=now)
        return None
    finally:
        _BUILDING = False


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
    log.info("daily edition: at %s %s, %d min an episode; next at %s",
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
