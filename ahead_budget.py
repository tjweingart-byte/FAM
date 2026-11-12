"""What is written ahead each day, and the one budget all of it shares (§568).

Three writers write episodes before anybody taps, each with ceilings of its
own (kept exactly as they were):

* ``dailyfam`` - the mixes' edition at 05:00 (`daily_edition.py`), and a
  saved or opened mix's subjects between editions. **The listener sees this
  as "myFAM"** (names-swapped, §185): "DailyFAM" in code is the mixes.
  `DAILY_EDITION_MAX_EPISODES` / `DAILY_EDITION_MAX_DOLLARS`, per edition.
* ``rails`` - the rails edition at 05:00 and 17:00 (`rails_edition.py`): the
  news the listener sees on **"DailyFAM"**. `RAILS_EDITION_MAX_EPISODES` /
  `RAILS_EDITION_MAX_DOLLARS`, per edition.
* ``prefetch`` - the cycle a drawn page schedules (`prefetch.py`): DailyFAM's
  other cards, briefs or whole scripts (`PREFETCH_LEVEL`).
  `PREFETCH_DAILY_EPISODES` / `_BRIEFS` / `_DOLLARS`, a rolling day.

On top of those, at the owner's direction: **one ceiling on the day's spend
for all three** (`AHEAD_DAILY_DOLLARS`, 0 = none, so nothing changes until it
is set). Each writer stops at its own ceiling *or* this one, whichever comes
first, and says which (`stopped`, on /admin and `/api/health`).
`AHEAD_DAILYFAM_SHARE` (0-1, 0 = none) holds a fraction of it for DailyFAM
alone - the rails edition and prefetch - so the 05:00 mixes' edition cannot
spend all of it before the day's news is written: the mixes' edition counts
DailyFAM's spend as at least the reserve.

**The day is the edition day** (`daily_edition.edition_day`): from the 05:00
mixes' edition on `DAILY_EDITION_TIMEZONE`'s clock to the next. Never a
listener's clock, and both of the rails' editions fall inside one day.

**The dollars are metering's** (`metering.price_of`, through each writer's
own count - `prefetch._dollars`, an edition's `spent`), the same figures each
writer's own dollar ceiling already reads. Kept in a small table beside the
day's model spend (`PROVIDER_USAGE_DB`), so a redeploy mid-day does not hand
the day's budget back, and every worker reads one figure. Like metering, it
is a record of spend and is never wiped.

Like each writer's own dollar ceiling, the dollars are known after an episode
is written, so the shared ceiling can be passed by at most the episodes
already in flight (`EDITION_CONCURRENCY`). A ceiling that cannot be read is
treated as reached - it is a spending cap - and says so.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from contextlib import closing
from typing import Optional

import db as store_db
from paths import data_path

log = logging.getLogger(__name__)

DAY = 86400.0

#: The writers, in the order the admin page draws them.
WRITERS = ("dailyfam", "rails", "prefetch")

#: What the listener calls each one (names-swapped, §185): the mixes are
#: "myFAM" on the screen and DailyFAM in code; the rails the reverse.
LABELS = {
    "dailyfam": "myFAM mixes (05:00 edition)",
    "rails": "DailyFAM news (05:00 and 17:00 editions)",
    "prefetch": "DailyFAM cards (written after a page is drawn)",
}

#: The writers the reserve is held for: what the listener sees as DailyFAM.
DAILYFAM_WRITERS = ("rails", "prefetch")

#: Each writer's name in the day's model spend (`metering.attribute_task`).
SPEND_SOURCE = {"dailyfam": "edition:dailyfam", "rails": "edition:rails",
                "prefetch": "prefetch"}

#: Why a writer stopped, in a sentence the owner reads.
WHY = {
    "shared": "the shared daily budget (AHEAD_DAILY_DOLLARS) was reached",
    "reserve": ("the rest of the shared budget is held for DailyFAM "
                "(AHEAD_DAILYFAM_SHARE)"),
    "own_episodes": "its own episode ceiling was reached",
    "own_dollars": "its own dollar ceiling was reached",
    "own_briefs": "its own brief ceiling was reached",
    "unreadable": ("the shared budget could not be read, so it was treated "
                   "as reached"),
}


def _settings():
    import config

    return config.settings


def day(now: Optional[float] = None) -> str:
    """The edition day `now` falls in, as `YYYY-MM-DD`."""
    import daily_edition

    return daily_edition.edition_day(now).isoformat()


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------
class AheadStore:
    """`ahead_spend(day, writer, episodes, briefs, usd, stopped, stopped_at)`."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = data_path("PROVIDER_USAGE_DB", "provider_usage.db", path)
        self._lock = threading.Lock()
        with closing(self._connect()) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS ahead_spend (
                                day        TEXT NOT NULL,
                                writer     TEXT NOT NULL,
                                episodes   INTEGER NOT NULL DEFAULT 0,
                                briefs     INTEGER NOT NULL DEFAULT 0,
                                usd        REAL NOT NULL DEFAULT 0,
                                stopped    TEXT NOT NULL DEFAULT '',
                                stopped_at REAL NOT NULL DEFAULT 0,
                                PRIMARY KEY (day, writer))""")

    def _connect(self) -> sqlite3.Connection:
        conn = store_db.connect(self.path, timeout=5, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def add(self, on: str, writer: str, episodes: int = 0, briefs: int = 0,
            usd: float = 0.0) -> None:
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO ahead_spend (day, writer, episodes, briefs, usd) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(day, writer) DO UPDATE SET "
                "episodes = ahead_spend.episodes + excluded.episodes, "
                "briefs = ahead_spend.briefs + excluded.briefs, "
                "usd = ahead_spend.usd + excluded.usd",
                (on, writer, int(episodes), int(briefs), float(usd)))

    def stop(self, on: str, writer: str, why: str, at: float) -> None:
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO ahead_spend (day, writer, stopped, stopped_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(day, writer) DO UPDATE SET "
                "stopped = excluded.stopped, stopped_at = excluded.stopped_at",
                (on, writer, why, float(at)))

    def rows_since(self, on: str) -> list:
        """Every row from edition day `on` on, as dicts."""
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "SELECT day, writer, episodes, briefs, usd, stopped, stopped_at "
                "FROM ahead_spend WHERE day >= ?", (on,))
            names = [c[0] for c in cursor.description]
            return [dict(zip(names, values)) for values in cursor]

    def clear(self) -> None:
        with self._lock, closing(self._connect()) as conn:
            conn.execute("DELETE FROM ahead_spend")


_STORE: Optional[AheadStore] = None
_STORE_LOCK = threading.Lock()


def store() -> AheadStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = AheadStore()
        return _STORE


def reset(ahead_store: Optional[AheadStore] = None) -> None:
    """For tests: use this store, or open the deployment's on next use."""
    global _STORE
    with _STORE_LOCK:
        _STORE = ahead_store


# --------------------------------------------------------------------------
# Spending
# --------------------------------------------------------------------------
def record(writer: str, *, episodes: int = 0, briefs: int = 0,
           usd: float = 0.0, now: Optional[float] = None) -> None:
    """Add to a writer's day. Negative figures correct an earlier estimate
    (a batched edition swaps its reserve for the real price). Never raises."""
    if not (episodes or briefs or usd):
        return
    try:
        store().add(day(now), writer, episodes, briefs, usd)
    except Exception as exc:  # noqa: BLE001 - counting never stops a writer
        log.warning("could not count %s's spend written ahead: %s", writer, exc)


def note_stopped(writer: str, why: str, now: Optional[float] = None) -> None:
    """Record that `writer` stopped today, and why (one of `WHY`). Logged once
    per writer, reason and day; stored every time. Never raises."""
    now = time.time() if now is None else now
    try:
        on = day(now)
    except Exception:  # noqa: BLE001
        on = time.strftime("%Y-%m-%d", time.gmtime(now))
    if (on, writer, why) not in _ANNOUNCED:
        _ANNOUNCED.add((on, writer, why))
        log.warning("written ahead: %s stopped for today: %s", LABELS.get(
            writer, writer), WHY.get(why, why))
    try:
        store().stop(on, writer, why, now)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not record that %s stopped: %s", writer, exc)


#: (day, writer, why) already logged in this process.
_ANNOUNCED: set = set()


def ceiling() -> float:
    return max(0.0, float(_settings().ahead_daily_dollars or 0.0))


def reserve_share() -> float:
    return min(1.0, max(0.0, float(_settings().ahead_dailyfam_share or 0.0)))


def allows(writer: str, now: Optional[float] = None) -> tuple:
    """`(True, "")` when the shared budget has room for one more of
    `writer`'s episodes, else `(False, why)`. No ceiling set is always room,
    without reading anything."""
    cap = ceiling()
    if cap <= 0:
        return True, ""
    try:
        today = day(now)
        rows = store().rows_since(today)
    except Exception as exc:  # noqa: BLE001 - a spending cap fails closed
        log.warning("could not read the shared budget written ahead: %s", exc)
        return False, "unreadable"
    rows = [r for r in rows if r["day"] == today]
    total = sum(float(r["usd"] or 0.0) for r in rows)
    if total >= cap:
        return False, "shared"
    share = reserve_share()
    if share > 0 and writer not in DAILYFAM_WRITERS:
        dailyfam = sum(float(r["usd"] or 0.0) for r in rows
                       if r["writer"] in DAILYFAM_WRITERS)
        # The reserve counts as spent by DailyFAM until DailyFAM spends it.
        if (total - dailyfam) + max(cap * share, dailyfam) >= cap:
            return False, "reserve"
    return True, ""


class Tally:
    """Keeps a running writer's figures in the day's table as they change.

    An edition keeps its own `spent = {"n", "usd"}` - an episode reserved
    before it is written and given back if it is not, a batch's reserve
    swapped for the real price later - and `sync` writes whatever moved since
    the last call, so the table ends where the edition's own figures do.
    """

    def __init__(self, writer: str, spent: dict) -> None:
        self.writer, self.spent = writer, spent
        self.n, self.usd = 0, 0.0

    def sync(self) -> None:
        n, usd = int(self.spent.get("n", 0)), float(self.spent.get("usd", 0.0))
        dn, dusd = n - self.n, usd - self.usd
        if dn or abs(dusd) > 1e-12:
            self.n, self.usd = n, usd
            record(self.writer, episodes=dn, usd=dusd)


# --------------------------------------------------------------------------
# What a person needs to see
# --------------------------------------------------------------------------
def _own_ceilings(writer: str) -> dict:
    """A writer's own ceilings, how often they apply, and their settings."""
    s = _settings()
    if writer == "dailyfam":
        return {"per": "edition", "editions_per_day": 1,
                "episodes": int(s.daily_edition_max_episodes or 0),
                "dollars": float(s.daily_edition_max_dollars or 0.0),
                "env": {"episodes": "DAILY_EDITION_MAX_EPISODES",
                        "dollars": "DAILY_EDITION_MAX_DOLLARS"}}
    if writer == "rails":
        import rails_edition

        return {"per": "edition", "editions_per_day": len(rails_edition.hours()),
                "episodes": int(s.rails_edition_max_episodes or 0),
                "dollars": float(s.rails_edition_max_dollars or 0.0),
                "env": {"episodes": "RAILS_EDITION_MAX_EPISODES",
                        "dollars": "RAILS_EDITION_MAX_DOLLARS"}}
    return {"per": "day", "editions_per_day": None,
            "episodes": int(s.prefetch_daily_episodes or 0),
            "briefs": int(s.prefetch_daily_briefs or 0),
            "dollars": float(s.prefetch_daily_dollars or 0.0),
            "level": s.prefetch_level,
            "env": {"episodes": "PREFETCH_DAILY_EPISODES",
                    "briefs": "PREFETCH_DAILY_BRIEFS",
                    "dollars": "PREFETCH_DAILY_DOLLARS",
                    "level": "PREFETCH_LEVEL"}}


def _per_day(own: dict, field: str) -> Optional[float]:
    """The most a writer's own ceiling lets it spend in a day; None is none."""
    value = own.get(field) or 0
    if not value:
        return None
    return value * (own.get("editions_per_day") or 1)


def _search_cost(since: float) -> Optional[dict]:
    """searchFAM's written episodes since `since`, from metering's ledger:
    the writing cost (everything but the voice), as an ahead episode has."""
    try:
        import cost_watch

        ledger = cost_watch._ledger()
        if ledger is None:
            return {"episodes": 0, "usd": 0.0, "per_episode": None}
        row = ledger._conn().execute(
            "SELECT COUNT(*), COALESCE(SUM(cost_usd - gpu_usd), 0) FROM usage "
            "WHERE surface = 'search' AND cache_hit = 0 AND at >= ?",
            (since,)).fetchone()
    except Exception as exc:  # noqa: BLE001 - a report is never load-bearing
        log.warning("could not read searchFAM's cost per episode: %s", exc)
        return None
    n, usd = int(row[0] or 0), float(row[1] or 0.0)
    return {"episodes": n, "usd": round(usd, 4),
            "per_episode": round(usd / n, 5) if n else None}


def _hit_rate() -> dict:
    """Taps that found a script prefetch had written, by source - the
    measurement dec-prefetch asked for. In this process since it started."""
    try:
        import prefetch

        r = prefetch.report()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    keys = ("warmed", "taken", "hit_rate", "briefs_warmed", "briefs_taken",
            "brief_hit_rate", "by_source")
    return {k: r.get(k) for k in keys}


def report(now: Optional[float] = None, history: bool = True) -> dict:
    """Each writer's day, the shared budget, and (with `history`) the last
    seven days' cost per episode written ahead against a searchFAM one, and
    the taps that found a ready script. Never raises."""
    now = time.time() if now is None else now
    s = _settings()
    try:
        today = day(now)
    except Exception:  # noqa: BLE001
        today = time.strftime("%Y-%m-%d", time.gmtime(now))
    days = 7
    try:
        first = day(now - (days - 1) * DAY)
    except Exception:  # noqa: BLE001
        first = time.strftime("%Y-%m-%d", time.gmtime(now - (days - 1) * DAY))
    error = ""
    try:
        rows = store().rows_since(min(first, today))
    except Exception as exc:  # noqa: BLE001
        rows, error = [], str(exc)
    mine = {r["writer"]: r for r in rows if r["day"] == today}

    writers = []
    for writer in WRITERS:
        row = mine.get(writer) or {}
        own = _own_ceilings(writer)
        why = str(row.get("stopped") or "")
        writers.append({
            "writer": writer, "label": LABELS[writer],
            "spend_source": SPEND_SOURCE[writer],
            "episodes": int(row.get("episodes") or 0),
            "briefs": int(row.get("briefs") or 0),
            "usd": round(float(row.get("usd") or 0.0), 4),
            "own": own,
            "max_episodes_per_day": _per_day(own, "episodes"),
            "max_dollars_per_day": _per_day(own, "dollars"),
            "stopped": ({"why": why, "sentence": WHY.get(why, why),
                         "at": float(row.get("stopped_at") or 0.0)}
                        if why else None),
        })

    cap, share = ceiling(), reserve_share()
    used = round(sum(w["usd"] for w in writers), 4)
    out = {
        "day": today,
        "day_note": (f"an edition day: from the 05:00 mixes' edition on "
                     f"{s.daily_edition_timezone}'s clock to the next"),
        "writers": writers,
        "shared": {
            "ceiling": cap or None, "used": used,
            "left": round(max(0.0, cap - used), 4) if cap else None,
            "share": round(used / cap, 4) if cap else None,
            "reserve_share": share or None,
            "reserve_usd": round(cap * share, 4) if cap and share else None,
            "env": {"ceiling": "AHEAD_DAILY_DOLLARS",
                    "reserve": "AHEAD_DAILYFAM_SHARE"},
        },
        "prefetch_level": {"value": s.prefetch_level, "env": "PREFETCH_LEVEL"},
    }
    if error:
        out["error"] = error
    if not history:
        return out

    since = now - days * DAY
    past = [r for r in rows if r["day"] >= first]
    by_writer = {}
    for writer in WRITERS:
        group = [r for r in past if r["writer"] == writer]
        n = sum(int(r["episodes"] or 0) for r in group)
        usd = sum(float(r["usd"] or 0.0) for r in group)
        by_writer[writer] = {"episodes": n, "usd": round(usd, 4),
                             "per_episode": round(usd / n, 5) if n else None}
    # Prefetch's dollars include its briefs, which are not episodes: the
    # all-writers figure is the editions' alone unless prefetch wrote scripts.
    n = sum(v["episodes"] for v in by_writer.values())
    usd = sum(v["usd"] for k, v in by_writer.items()
              if k != "prefetch" or v["episodes"])
    out["cost_per_episode"] = {
        "days": days,
        "ahead": {"episodes": n, "usd": round(usd, 4),
                  "per_episode": round(usd / n, 5) if n else None},
        "by_writer": by_writer,
        "search": _search_cost(since),
        "note": ("written ahead: metering's price of the brief, research and "
                 "writer, from this table; searchFAM: metering's ledger, "
                 "searches that wrote an episode, less the voice"),
    }
    out["hit_rate"] = _hit_rate()
    return out
