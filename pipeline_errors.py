"""Every way the pipeline went wrong, in one place, for `/admin` (§269).

The owner (10.14): "a section in the admin page that tracks errors happening
with any part of the pipeline (API-Sports refused, EI timed out, etc.) to help
us react faster to bugs". Each of those was already *said* somewhere - a log
line, a counter in `/api/health`, a row on the routing card - and none of them
was where somebody looks first. Five timeouts in eleven briefs (§268) sat in
`/api/health` until somebody thought to read it.

**What is recorded.** One row per thing that went wrong, by stage:

* ``brief`` - the brief degraded (timeout, out of room, failed, refused,
  unreadable), or one half of a split brief did not answer;
* ``live`` - a provider asked for a live state broke, timed out or answered
  too late (`provider_failed`, `timeout`, `stale`) - never `no_entity` or
  `no_facts`, which are answers, and never `not_configured`, which is a
  deployment's choice and is on the services page;
* ``research`` - a search rung raised, or every rung came back empty;
* ``writer`` - the writer call failed or refused, or a check removed a
  sentence before it was spoken (score, figure, opening);
* ``edition`` - an edition episode (Trending, DailyFAM) could not be written;
* ``episode`` - a listener's episode failed before any audio, came back with
  no speech, lost its voice, broke mid-stream, or the voice fell behind.

`severity` is ``error`` (somebody heard less than they should have, or
nothing) or ``notice`` (a guard did its job, or something degraded and the
episode still played). The page counts both and lists the newest.

**Never on the critical path**, exactly as `live_routing`: `record` appends to
a list in memory and returns; a background thread writes at most every
`FLUSH_SECONDS`, and before every report. It never raises. Rows older than
`KEEP_DAYS` go on write. No listener id: the stage, the kind, a sentence of
detail and the question it happened on.
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

log = logging.getLogger("pipeline_errors")

DAY = 86400
HOUR = 3600
KEEP_DAYS = 14
FLUSH_SECONDS = 10.0
MAX_PENDING = 2000
#: The newest rows the admin page lists.
RECENT = 60

STAGES = ("brief", "live", "research", "writer", "edition", "episode")
ERROR = "error"
NOTICE = "notice"


def row(stage: str, kind: str, detail: str = "", query: str = "",
        severity: str = ERROR, at: Optional[float] = None) -> dict:
    return {
        "at": time.time() if at is None else at,
        "stage": str(stage or "")[:20],
        "kind": str(kind or "")[:80],
        "detail": " ".join(str(detail or "").split())[:300],
        "query": " ".join(str(query or "").split())[:200],
        "severity": severity if severity in (ERROR, NOTICE) else ERROR,
    }


class ErrorStore:
    """`errors`, one row per thing that went wrong."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = data_path("PIPELINE_ERRORS_DB", "pipeline_errors.db", path)
        self._lock = threading.Lock()
        with closing(self._connect()) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS errors (
                              at       REAL NOT NULL,
                              stage    TEXT NOT NULL DEFAULT '',
                              kind     TEXT NOT NULL DEFAULT '',
                              detail   TEXT NOT NULL DEFAULT '',
                              query    TEXT NOT NULL DEFAULT '',
                              severity TEXT NOT NULL DEFAULT 'error')""")
            db.execute("CREATE INDEX IF NOT EXISTS errors_at ON errors (at)")
            # One row per measured step (§270): how long it took and how it
            # ended, whether or not anything went wrong.
            db.execute("""CREATE TABLE IF NOT EXISTS steps (
                              at      REAL NOT NULL,
                              step    TEXT NOT NULL DEFAULT '',
                              path    TEXT NOT NULL DEFAULT '',
                              outcome TEXT NOT NULL DEFAULT '',
                              seconds REAL)""")
            db.execute("CREATE INDEX IF NOT EXISTS steps_at ON steps (at)")

    def _connect(self) -> sqlite3.Connection:
        db = store_db.connect(self.path, timeout=5, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def add(self, rows: list, now: Optional[float] = None,
            steps: Optional[list] = None) -> None:
        steps = steps or []
        if not rows and not steps:
            return
        now = time.time() if now is None else now
        with self._lock:
            db = self._connect()
            try:
                with db:
                    if rows:
                        db.executemany(
                            "INSERT INTO errors (at, stage, kind, detail, query, "
                            "severity) VALUES (?, ?, ?, ?, ?, ?)",
                            [(r["at"], r["stage"], r["kind"], r["detail"],
                              r["query"], r["severity"]) for r in rows])
                        db.execute("DELETE FROM errors WHERE at < ?",
                                   (now - KEEP_DAYS * DAY,))
                    if steps:
                        db.executemany(
                            "INSERT INTO steps (at, step, path, outcome, seconds) "
                            "VALUES (?, ?, ?, ?, ?)",
                            [(r["at"], r["step"], r["path"], r["outcome"],
                              r["seconds"]) for r in steps])
                        db.execute("DELETE FROM steps WHERE at < ?",
                                   (now - KEEP_DAYS * DAY,))
            finally:
                db.close()

    def steps_since(self, at: float) -> list:
        with closing(self._connect()) as db:
            cursor = db.execute(
                "SELECT at, step, path, outcome, seconds FROM steps "
                "WHERE at >= ? ORDER BY at DESC", (at,))
            names = [c[0] for c in cursor.description]
            return [dict(zip(names, values)) for values in cursor]

    def since(self, at: float) -> list:
        with closing(self._connect()) as db:
            cursor = db.execute(
                "SELECT at, stage, kind, detail, query, severity FROM errors "
                "WHERE at >= ? ORDER BY at DESC", (at,))
            names = [c[0] for c in cursor.description]
            return [dict(zip(names, values)) for values in cursor]

    def clear(self) -> None:
        with self._lock, closing(self._connect()) as db:
            db.execute("DELETE FROM errors")
            db.execute("DELETE FROM steps")


_STORE: Optional[ErrorStore] = None
_STORE_LOCK = threading.Lock()
_PENDING: list = []
#: Measured steps not yet written (§270), held and flushed beside the errors.
_PENDING_STEPS: list = []
_PENDING_LOCK = threading.Lock()
_LAST_FLUSH = [0.0]
_FLUSHING = [False]
_FLUSH_LOCK = threading.Lock()


def store() -> ErrorStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = ErrorStore()
        return _STORE


def reset(error_store: Optional[ErrorStore] = None) -> None:
    """For tests: use this store (or open a fresh one on next use), and
    forget any rows not yet written."""
    global _STORE
    with _STORE_LOCK:
        _STORE = error_store
    with _PENDING_LOCK:
        _PENDING.clear()
        _PENDING_STEPS.clear()
        _LAST_FLUSH[0] = 0.0


def exists() -> bool:
    """Whether there is anything to report. Reading never creates the store."""
    if _STORE is not None:
        return True
    with _PENDING_LOCK:
        if _PENDING or _PENDING_STEPS:
            return True
    return store_db.has_tables(data_path("PIPELINE_ERRORS_DB", "pipeline_errors.db"))


def record(stage: str, kind: str, detail: str = "", query: str = "",
           severity: str = ERROR) -> None:
    """Note one thing that went wrong. Never raises, never waits."""
    try:
        entry = row(stage, kind, detail, query, severity)
        with _PENDING_LOCK:
            _PENDING.append(entry)
            del _PENDING[:-MAX_PENDING]
            due = (not _FLUSHING[0]
                   and time.monotonic() - _LAST_FLUSH[0] >= FLUSH_SECONDS)
            if due:
                _FLUSHING[0] = True
        if due:
            threading.Thread(target=_flush_in_background, daemon=True,
                             name="pipeline-errors-flush").start()
    except Exception as exc:  # noqa: BLE001 - a record never costs an episode
        log.warning("could not record a pipeline error: %s", exc)


#: The steps measured on every episode (§270), in the order a listener waits
#: through them, with the words the admin page shows. A step recorded under
#: a name not listed here is still reported, after these.
STEPS = (
    ("first audio", "Tap to first audio (the whole wait)"),
    ("brief", "Brief (episode intelligence)"),
    ("brief: understand", "Brief - understand part"),
    ("brief: search", "Brief - search part"),
    ("brief: outline", "Brief - plan of the piece"),
    ("writer warm-up", "Writer cache warm-up"),
    ("search", "Search (every rung)"),
    ("live lookup", "Live lookup"),
    ("evidence cut", "Evidence cut"),
    ("writer first word", "Writer thinking (request to first word)"),
    ("writer", "Writer (request to last word)"),
)
#: Outcomes that mean a step failed at its job - the page's failure rate.
#: Anything else is an ending the step was built to have (`ok`, a cut not
#: made, a provider with no such game).
FAILED = frozenset({"timeout", "failed", "refused", "out_of_room", "unreadable",
                    "late", "no_sources", "provider_failed", "stale"})


def step(name: str, path: str, outcome: str,
         seconds: Optional[float] = None) -> None:
    """Note how long one step took and how it ended (§270). Never raises,
    never waits. `path` is who was waiting: `search` (a listener, in front of
    the first word), `ahead` (prefetch, the editions) or `listener` (any tap
    that made an episode)."""
    try:
        entry = {"at": time.time(), "step": str(name or "")[:40],
                 "path": str(path or "")[:12], "outcome": str(outcome or "")[:40],
                 "seconds": None if seconds is None else round(float(seconds), 3)}
        with _PENDING_LOCK:
            _PENDING_STEPS.append(entry)
            del _PENDING_STEPS[:-MAX_PENDING]
            due = (not _FLUSHING[0]
                   and time.monotonic() - _LAST_FLUSH[0] >= FLUSH_SECONDS)
            if due:
                _FLUSHING[0] = True
        if due:
            threading.Thread(target=_flush_in_background, daemon=True,
                             name="pipeline-errors-flush").start()
    except Exception as exc:  # noqa: BLE001 - a measurement never costs an episode
        log.warning("could not record a pipeline step: %s", exc)


def _quantile(values: list, share: float):
    if not values:
        return None
    return values[min(len(values) - 1, int(share * len(values)))]


def steps_report(rows: list, now: float) -> list:
    """Per step and path: how many, how long, how often it failed."""
    groups: dict = {}
    for r in rows:
        groups.setdefault((r["step"], r["path"]), []).append(r)
    order = {name: i for i, (name, _) in enumerate(STEPS)}
    words = dict(STEPS)
    out = []
    for (name, path), items in groups.items():
        week = sorted(r["seconds"] for r in items if r["seconds"] is not None)
        day_items = [r for r in items if r["at"] >= now - DAY]
        day = [r["seconds"] for r in day_items if r["seconds"] is not None]
        outcomes: dict = {}
        for r in items:
            outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
        failed = sum(n for o, n in outcomes.items() if o in FAILED)
        out.append({
            "step": name, "label": words.get(name, name), "path": path,
            "hour": sum(1 for r in items if r["at"] >= now - HOUR),
            "day": len(day_items), "week": len(items),
            "mean_day": round(sum(day) / len(day), 3) if day else None,
            "mean": round(sum(week) / len(week), 3) if week else None,
            "median": _quantile(week, 0.5), "p90": _quantile(week, 0.9),
            "max": week[-1] if week else None,
            "failed": failed,
            "failure_rate": round(failed / len(items), 4) if items else 0.0,
            "outcomes": dict(sorted(outcomes.items(), key=lambda kv: -kv[1])),
        })
    out.sort(key=lambda x: (order.get(x["step"], len(order)), x["step"], x["path"]))
    return out


def mark_recorded(exc: BaseException) -> None:
    """Note on an exception that its stage already recorded it, so whatever
    catches it further up records a notice instead of a second error
    (review, §269: one writer failure counted twice on the badge)."""
    try:
        exc._fam_recorded = True  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - some exceptions take no attributes
        pass


def severity_for(exc: BaseException) -> str:
    """`NOTICE` for an exception a stage already recorded, else `ERROR`."""
    return NOTICE if getattr(exc, "_fam_recorded", False) else ERROR


def _flush_in_background() -> None:
    try:
        flush()
    finally:
        with _PENDING_LOCK:
            _FLUSHING[0] = False


def flush() -> None:
    """Write the rows held in memory. Never raises; a failed write keeps them."""
    with _FLUSH_LOCK:
        with _PENDING_LOCK:
            _LAST_FLUSH[0] = time.monotonic()
        try:
            target = store()
        except Exception as exc:  # noqa: BLE001
            log.warning("could not open pipeline errors: %s", exc)
            return
        with _PENDING_LOCK:
            taken = list(_PENDING)
            _PENDING.clear()
            steps = list(_PENDING_STEPS)
            _PENDING_STEPS.clear()
        if not taken and not steps:
            return
        try:
            target.add(taken, steps=steps)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not write pipeline errors: %s", exc)
            with _PENDING_LOCK:
                _PENDING[:0] = taken
                del _PENDING[:-MAX_PENDING]
                _PENDING_STEPS[:0] = steps
                del _PENDING_STEPS[:-MAX_PENDING]


def report(now: Optional[float] = None, days: int = 7) -> dict:
    """What the admin page draws. Never raises, never creates the store.

    Counts for the last hour, day and week; per stage and kind, how many in
    the last day and week, when it last happened and what it last said; and
    the newest rows."""
    now = time.time() if now is None else now
    rows: list = []
    steps: list = []
    try:
        if exists():
            flush()
            rows = store().since(now - days * DAY)
            steps = store().steps_since(now - days * DAY)
    except Exception as exc:  # noqa: BLE001 - a report is never load-bearing
        log.warning("could not read pipeline errors: %s", exc)

    def counted(since: float, severity: str) -> int:
        return sum(1 for r in rows if r["at"] >= since and r["severity"] == severity)

    kinds: dict = {}
    for r in rows:  # newest first, so the first seen is the latest
        key = (r["stage"], r["kind"])
        k = kinds.get(key)
        if k is None:
            k = kinds[key] = {"stage": r["stage"], "kind": r["kind"],
                              "severity": r["severity"], "day": 0, "week": 0,
                              "last_at": r["at"], "last_detail": r["detail"],
                              "last_query": r["query"]}
        k["week"] += 1
        if r["at"] >= now - DAY:
            k["day"] += 1
    by_stage = {s: {"day": 0, "week": 0} for s in STAGES}
    for k in kinds.values():
        s = by_stage.setdefault(k["stage"], {"day": 0, "week": 0})
        s["day"] += k["day"]
        s["week"] += k["week"]
    return {
        "days": days,
        "errors": {"hour": counted(now - HOUR, ERROR),
                   "day": counted(now - DAY, ERROR),
                   "week": counted(now - days * DAY, ERROR)},
        "notices": {"hour": counted(now - HOUR, NOTICE),
                    "day": counted(now - DAY, NOTICE),
                    "week": counted(now - days * DAY, NOTICE)},
        "by_stage": by_stage,
        "kinds": sorted(kinds.values(),
                        key=lambda k: (k["severity"] != ERROR, -k["day"],
                                       -k["week"], -k["last_at"]))[:40],
        "recent": rows[:RECENT],
        # How long each step takes and how often it fails (§270), from the
        # same store, so every worker's episodes are in it and a redeploy
        # keeps it.
        "steps": steps_report(steps, now),
    }
