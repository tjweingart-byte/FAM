"""The myFAM rails edition: every story the rails can offer, written before the tap.

What it is (§359, at the owner's direction)
-------------------------------------------
The rails (myFAM in code, "DailyFAM" on the listener's screen, §185) were the
one browse surface still written on the tap: Trending and Start here are
written by the trending bank, the crowd rails hold cached episodes only, but
a Made for you tile from the live story pool was a title and an angle whose
brief prefetch might have warmed and whose retrieval and writer ran while
the listener waited. Now the whole script is written ahead and the tap pays
only for the voice:

* **Twice a day, on the editions' clock.** At `RAILS_EDITION_HOURS` (05:00
  and 17:00) on `DAILY_EDITION_TIMEZONE`'s wall clock, one episode for every
  story the pool **holds** (`stories.Pool.held`) - what Made for you ranks
  (`topics.made_for_you_candidates`), not just the forty it offers - into the
  shared cache under the key a tap computes (the story's query at
  `BROWSE_MINUTES`). A tap is an ordinary cache hit.
* **Per story, never per listener.** The cache key has no listener in it, so
  "everyone's rails" is the pool, written once: the bill is bounded by the
  pool (`stories.POOL_STORE`), whatever the audience.
* **The same loop as the mixes' edition** (`daily_edition.run_slot`): the
  claim, EI on every episode, the batch at half price, the live lookup a tap
  would make, and the report. No ceiling by default (0); one can be set.
* **A sports story it wrote is held** until the next rails edition, plus the
  editions' grace hour (`stories.hold_until`): eight hours of shelf would
  otherwise end before the next edition with the episode still current.
* **A game in progress** is written all the same, keeps its title, hook and
  category on the tile, and is current for `EDITION_IN_PROGRESS_SECONDS`;
  a tap after that writes a new episode (§359, `daily_edition._finish`).

What it does not write: a heard story's "what's new" follow-up (its words
are the listener's own history), and a story that arrives between editions,
which is written on the tap until the next edition. Nothing on a request
path builds anything.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import config
import daily_edition

log = logging.getLogger(__name__)

#: Slots are kept beside the mixes' edition's, told apart by this prefix.
PREFIX = "rails@"
#: What a rails edition says on a server that cannot write.
NO_WRITER = ("no writer on this server (demo mode or no key); every rail "
             "episode is written on the tap")

_BUILDING = False
_LAST_ATTEMPT: dict = {}


def _settings():
    # Read at call time: the suite swaps `config.settings` per test.
    return config.settings


def hours() -> list:
    """The rails edition's hours, sorted, each a real hour; 05:00 and 17:00
    unless `RAILS_EDITION_HOURS` says otherwise."""
    out = set()
    for part in (_settings().rails_edition_hours or "").split(","):
        try:
            hour = int(part.strip())
        except ValueError:
            continue
        if 0 <= hour <= 23:
            out.add(hour)
    return sorted(out) or [5, 17]


def last_slot(now: Optional[float] = None):
    return daily_edition.last_slot(now, hours())


def next_slot(now: Optional[float] = None):
    return daily_edition.next_slot(now, hours())


def slot_id(slot) -> str:
    return PREFIX + daily_edition.slot_id(slot)


def current_until(now: float) -> float:
    """How long a rails episode is current: the next rails edition plus the
    editions' grace hour, so a tap while it is being written finds this one."""
    return daily_edition._current_until(now, hours())


def subjects(now: Optional[float] = None) -> list:
    """Every story the pool holds, loudest first, as `[{"query", "title",
    "story", "domain"}]`. Deduplicated by the words, because the words are
    the key; a question nobody else may share (`cache.is_shareable`) is left
    out, as the cache would not keep it."""
    import stories
    from cache import is_shareable

    now = time.time() if now is None else now
    try:
        held = stories.pool().held(now)
    except Exception as exc:  # noqa: BLE001 - an unreadable pool writes nothing
        log.warning("rails edition: the story pool could not be read: %s", exc)
        return []
    held = sorted(held, key=lambda s: (-s.push(now), s.id))
    out, seen = [], set()
    for story in held:
        query = (story.query or "").strip()
        if not query or query in seen or not is_shareable(query):
            continue
        seen.add(query)
        out.append({"query": query, "title": story.title, "story": story.id,
                    "domain": story.domain})
    return out


def _hold(until: float):
    """What `run_slot` calls for each story written: keep a sports story on
    the shelf as long as its episode is current."""
    import stories

    def held(subject: dict, _result: dict) -> None:
        stories.hold_until(subject.get("story", ""), until)

    return held


async def build(generator=None, cache=None, now: Optional[float] = None,
                force: bool = False) -> Optional[dict]:
    """Write the current rails slot, if this caller wins the claim.

    Returns the edition's report, or None when another builder has it or it
    is already built. Never raises.
    """
    global _BUILDING
    s = _settings()
    now = time.time() if now is None else now
    if not s.rails_edition:
        return None
    slot = last_slot(now)
    sid = slot_id(slot)
    if generator is None or cache is None:
        return {"slot": sid, "minutes": config.BROWSE_MINUTES,
                "subjects": len(subjects(now)), "episodes": {},
                "skipped_for_ceiling": 0, "detail": NO_WRITER}
    until = current_until(now)
    _BUILDING = True
    try:
        report = await daily_edition.run_slot(
            sid, slot, now, lambda: subjects(now), generator, cache,
            until=until, length=config.BROWSE_MINUTES, origin="myfam",
            max_episodes=s.rails_edition_max_episodes,
            max_dollars=s.rails_edition_max_dollars, force=force,
            label="rails edition", on_written=_hold(until),
            failures=_LAST_ATTEMPT)
        if report is not None:
            _LAST_ATTEMPT.clear()
            _save_pool(now)
        return report
    finally:
        _BUILDING = False


def _save_pool(now: float) -> None:
    """Keep the longer shelf lives on the disk. Never raises."""
    try:
        import stories

        stories.save(now)
    except Exception:  # noqa: BLE001
        log.exception("rails edition: could not save the story pool")


def due(now: Optional[float] = None) -> bool:
    """Whether the current rails slot still wants building."""
    if not _settings().rails_edition:
        return False
    now = time.time() if now is None else now
    row = daily_edition.store().status(slot_id(last_slot(now)))
    if row is None:
        return True
    if row["status"] == daily_edition.READY:
        return False
    if row["status"] == daily_edition.FAILED:
        return (str(row.get("detail") or "").startswith(daily_edition.INTERRUPTED)
                or now - float(row["claimed_at"] or 0)
                >= _settings().daily_edition_retry_seconds)
    return now - float(row["claimed_at"] or 0) >= daily_edition.STALE_CLAIM_SECONDS


async def run_forever(generator=None, cache=None,
                      initial_delay: float = 0.0) -> None:
    """Build on the clock, catching up on boot. Runs for the server's life.

    The catch-up waits for `initial_delay` and then for the story pool's
    first sweep, so a boot writes the pool it is about to hold rather than
    an empty one.
    """
    if not _settings().rails_edition:
        log.info("rails edition: off (RAILS_EDITION=0)")
        return
    if generator is None or cache is None:
        _LAST_ATTEMPT.update(slot=slot_id(last_slot()), detail=NO_WRITER,
                             at=time.time())
        log.warning("rails edition: %s", NO_WRITER)
        return
    log.info("rails edition: at %s %s; next at %s",
             ", ".join(f"{h:02d}:00" for h in hours()),
             _settings().daily_edition_timezone, slot_id(next_slot()))
    if initial_delay > 0:
        await asyncio.sleep(initial_delay)
    while True:
        try:
            import stories

            if due() and stories.pool().stories:
                row = daily_edition.store().status(slot_id(last_slot()))
                force = bool(row and str(row.get("detail") or "")
                             .startswith(daily_edition.INTERRUPTED))
                await build(generator=generator, cache=cache, force=force)
        except Exception:  # noqa: BLE001
            log.exception("rails edition: the scheduler tick failed")
        await asyncio.sleep(max(5.0, min(60.0, next_slot().timestamp() - time.time())))


def report(now: Optional[float] = None) -> dict:
    """For `/api/health`: the schedule and the current rails edition."""
    now = time.time() if now is None else now
    s = _settings()
    out = {
        "enabled": bool(s.rails_edition),
        "schedule": {"timezone": s.daily_edition_timezone, "hours": hours(),
                     "next": slot_id(next_slot(now)),
                     "current_slot": slot_id(last_slot(now))},
        "minutes": config.BROWSE_MINUTES,
        "ceilings": {"episodes": s.rails_edition_max_episodes,
                     "dollars": s.rails_edition_max_dollars},
        "building": _BUILDING,
        "last_failure": dict(_LAST_ATTEMPT) or None,
    }
    try:
        row = daily_edition.store().status(slot_id(last_slot(now)))
    except Exception as exc:  # noqa: BLE001 - never break the health page
        row = {"error": str(exc)}
    if row and isinstance(row.get("report"), dict):
        row = dict(row)
        row["report"] = {k: v for k, v in row["report"].items()
                         if k != "episodes"}
    out["current"] = row
    return out


def reset() -> None:
    """Forget process state. For tests."""
    global _BUILDING
    _BUILDING = False
    _LAST_ATTEMPT.clear()
