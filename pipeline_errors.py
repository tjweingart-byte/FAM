"""Every way the pipeline went wrong, in one place, for `/admin` (§269).

The owner (10.14): "a section in the admin page that tracks errors happening
with any part of the pipeline (a sports provider refused, EI timed out, etc.) to help
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
  no speech, lost its voice, broke mid-stream, or the voice fell behind;
* ``voice`` - the supervisor found no worker that can speak, or a worker that
  used to register itself has stopped (§318), whether or not anybody asked
  for an episode while it was so.

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

import functools
import logging
import re
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
#: Most measured steps a report reads (§273): about a week at ~700 new
#: episodes a day, ten steps each. Past it the timings are over the newest
#: this many, and the page says so (`steps_capped`).
STEPS_READ = 50_000

STAGES = ("brief", "live", "research", "writer", "edition", "episode", "voice")
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
            # One row per declined model call (§273): Haiku 5.5 has no
            # server-side fallback, so each refusal is an episode that said
            # the fixed line instead of the story, or a brief or cut lost.
            db.execute("""CREATE TABLE IF NOT EXISTS refusals (
                              at          REAL NOT NULL,
                              call        TEXT NOT NULL DEFAULT '',
                              model       TEXT NOT NULL DEFAULT '',
                              path        TEXT NOT NULL DEFAULT '',
                              category    TEXT NOT NULL DEFAULT '',
                              explanation TEXT NOT NULL DEFAULT '',
                              query       TEXT NOT NULL DEFAULT '',
                              spoken      INTEGER NOT NULL DEFAULT 0)""")
            db.execute("CREATE INDEX IF NOT EXISTS refusals_at ON refusals (at)")

    def _connect(self) -> sqlite3.Connection:
        db = store_db.connect(self.path, timeout=5, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def add(self, rows: list, now: Optional[float] = None,
            steps: Optional[list] = None,
            refusals: Optional[list] = None) -> None:
        steps = steps or []
        refusals = refusals or []
        if not rows and not steps and not refusals:
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
                    if refusals:
                        db.executemany(
                            "INSERT INTO refusals (at, call, model, path, "
                            "category, explanation, query, spoken) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            [(r["at"], r["call"], r["model"], r["path"],
                              r["category"], r["explanation"], r["query"],
                              r["spoken"]) for r in refusals])
                        db.execute("DELETE FROM refusals WHERE at < ?",
                                   (now - KEEP_DAYS * DAY,))
            finally:
                db.close()

    def steps_since(self, at: float, limit: int = 0) -> list:
        """The newest steps since `at`, at most `limit` (0 is no limit) -
        bounded so a busy week never makes the admin page read it whole
        (review, §273)."""
        limit = int(limit or STEPS_READ)
        with closing(self._connect()) as db:
            cursor = db.execute(
                "SELECT at, step, path, outcome, seconds FROM steps "
                "WHERE at >= ? ORDER BY at DESC LIMIT ?", (at, limit))
            names = [c[0] for c in cursor.description]
            return [dict(zip(names, values)) for values in cursor]

    def since(self, at: float) -> list:
        with closing(self._connect()) as db:
            cursor = db.execute(
                "SELECT at, stage, kind, detail, query, severity FROM errors "
                "WHERE at >= ? ORDER BY at DESC", (at,))
            names = [c[0] for c in cursor.description]
            return [dict(zip(names, values)) for values in cursor]

    def refusals_since(self, at: float) -> list:
        with closing(self._connect()) as db:
            cursor = db.execute(
                "SELECT at, call, model, path, category, explanation, query, "
                "spoken FROM refusals WHERE at >= ? ORDER BY at DESC", (at,))
            names = [c[0] for c in cursor.description]
            return [dict(zip(names, values)) for values in cursor]

    def clear(self) -> None:
        with self._lock, closing(self._connect()) as db:
            db.execute("DELETE FROM errors")
            db.execute("DELETE FROM steps")
            db.execute("DELETE FROM refusals")


_STORE: Optional[ErrorStore] = None
_STORE_LOCK = threading.Lock()
_PENDING: list = []
#: Measured steps not yet written (§270), held and flushed beside the errors.
_PENDING_STEPS: list = []
#: Declined calls not yet written (§273).
_PENDING_REFUSALS: list = []
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
        _PENDING_REFUSALS.clear()
        _LAST_FLUSH[0] = 0.0
    _category_cached.cache_clear()


def exists() -> bool:
    """Whether there is anything to report. Reading never creates the store."""
    if _STORE is not None:
        return True
    with _PENDING_LOCK:
        if _PENDING or _PENDING_STEPS or _PENDING_REFUSALS:
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


def refusal(call: str, model: str, path: str, query: str, message=None,
            spoken: int = 0) -> None:
    """Note one declined model call, with what it was declined for (§273).

    `call` names which call (`writer`, `writer (batch)`, `brief: understand`,
    `evidence cut`...), `message` is the API's answer - its `stop_details`
    carry the classifier's category and explanation - and `spoken` how many
    sentences the listener had already heard when it stopped. Haiku 5.5 has
    no server-side fallback, so this is the record of what a retry on another
    model would have had to rescue. Never raises, never waits."""
    try:
        details = getattr(message, "stop_details", None)
        entry = {
            "at": time.time(), "call": str(call or "")[:40],
            "model": str(model or getattr(message, "model", "") or "")[:60],
            "path": str(path or "")[:12],
            "category": str(getattr(details, "category", None) or "")[:40],
            "explanation": " ".join(str(getattr(details, "explanation", None)
                                        or "").split())[:300],
            "query": " ".join(str(query or "").split())[:200],
            "spoken": int(spoken or 0),
        }
        with _PENDING_LOCK:
            _PENDING_REFUSALS.append(entry)
            del _PENDING_REFUSALS[:-MAX_PENDING]
            due = (not _FLUSHING[0]
                   and time.monotonic() - _LAST_FLUSH[0] >= FLUSH_SECONDS)
            if due:
                _FLUSHING[0] = True
        if due:
            threading.Thread(target=_flush_in_background, daemon=True,
                             name="pipeline-errors-flush").start()
    except Exception as exc:  # noqa: BLE001 - a record never costs an episode
        log.warning("could not record a refusal: %s", exc)


def refusals_report(rows: list, steps: list, now: float) -> dict:
    """How often calls were declined, for what, and on which questions."""
    def counted(since: float) -> int:
        return sum(1 for r in rows if r["at"] >= since)

    # The share of writer calls declined: each writer call is a `writer`
    # step whatever its ending, so the steps give the denominator.
    writes = [r for r in steps if r["step"] == "writer"]
    declined = sum(1 for r in writes if r["outcome"] == "refused")

    def tally(key: str) -> list:
        out: dict = {}
        for r in rows:
            name = r[key] or "(none given)"
            item = out.setdefault(name, {"name": name, "day": 0, "week": 0,
                                         "last_at": r["at"]})
            item["week"] += 1
            if r["at"] >= now - DAY:
                item["day"] += 1
        return sorted(out.values(), key=lambda x: (-x["week"], -x["last_at"]))

    return {
        "hour": counted(now - HOUR), "day": counted(now - DAY),
        "week": len(rows),
        "writer_calls": len(writes), "writer_refused": declined,
        "writer_rate": round(declined / len(writes), 4) if writes else None,
        "mid_episode": sum(1 for r in rows if r["spoken"]),
        "by_call": tally("call"), "by_category": tally("category"),
        "by_model": tally("model"),
        "recent": rows[:RECENT],
    }


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
            refusals = list(_PENDING_REFUSALS)
            _PENDING_REFUSALS.clear()
        if not taken and not steps and not refusals:
            return
        try:
            target.add(taken, steps=steps, refusals=refusals)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not write pipeline errors: %s", exc)
            with _PENDING_LOCK:
                _PENDING[:0] = taken
                del _PENDING[:-MAX_PENDING]
                _PENDING_STEPS[:0] = steps
                del _PENDING_STEPS[:-MAX_PENDING]
                _PENDING_REFUSALS[:0] = refusals
                del _PENDING_REFUSALS[:-MAX_PENDING]


# --------------------------------------------------------------------------
# One kind, opened (10.16 #1): what it means, which episodes, what time
# --------------------------------------------------------------------------
#: The newest rows an opened kind lists, and the most times it sends for the
#: page to draw by hour of day (in the reader's own clock, so drawn there).
KIND_EXAMPLES = 8
KIND_TIMES = 1000

_COUNTED = re.compile(r"\b\d+ sentences?\b")


def family(kind: str) -> str:
    """The kind with its count taken out, so "score check removed 1
    sentence" and "... 3 sentences" are one row, not one row per number."""
    return _COUNTED.sub("sentences", str(kind or ""))


def _ex(title: str, what: str, heard: str, causes: str, act: str) -> dict:
    return {"title": title, "what": what, "heard": heard, "causes": causes,
            "act": act}


#: What each kind means, in words, by stage and the kind's pattern. The
#: first match wins; the stage's own entry (pattern "") is the fallback, so a
#: kind added later still says which part of making an episode it is.
EXPLAIN = (
    ("brief", r"^search half",
     _ex("Half the brief did not answer",
         "The brief is asked in parts at once. The part that plans the searches "
         "did not answer (timed out, failed or was declined).",
         "Nothing missing: the episode was searched with what the other part "
         "planned, or the question as typed.",
         "The brief model was slow or busy; 'timeout' is the commonest.",
         "Act only if it is most episodes: look at Brief - search part in the timing table.")),
    ("brief", r"^timeout",
     _ex("The brief took too long",
         "Episode intelligence (the brief that decides what to search for) did "
         "not answer before its deadline, so the episode went ahead without it.",
         "A plainer episode: it was searched with the question as typed, with no "
         "plan of the piece and no pronunciations.",
         "The brief model was slow (a busy hour at Anthropic), or the question "
         "needed a long answer. '(search)' means a listener was waiting; "
         "'(ahead)' means prefetch or an edition.",
         "If it is more than a few a day, raise EI_TIMEOUT_SECONDS or check "
         "Anthropic's status page.")),
    ("brief", r"^out_of_room",
     _ex("The brief ran out of room",
         "The brief's answer was cut off at its token limit before it finished.",
         "A plainer episode: searched with the question as typed.",
         "A question with many parts, or the limit set too low.",
         "Raise the brief's max tokens if this repeats.")),
    ("brief", r"^refused",
     _ex("The brief was declined",
         "A safety classifier declined the brief call (see Refusals below).",
         "A plainer episode: searched with the question as typed.",
         "Usually a news question about violence, crime or a sensitive person.",
         "Read the question; nothing to fix unless it is an ordinary question.")),
    ("brief", r"^unreadable",
     _ex("The brief came back unreadable",
         "The brief answered, but not in the shape the code reads.",
         "A plainer episode: searched with the question as typed.",
         "The model broke the answer's format.",
         "One or two are noise; a run of them means the brief's prompt changed.")),
    ("brief", r"",
     _ex("The brief failed",
         "Episode intelligence (the brief that decides what to search for) "
         "raised an error.",
         "A plainer episode: searched with the question as typed.",
         "An API error, a network drop or a bad key.",
         "Read the detail; repeated failures mean the Anthropic key or network.")),
    ("live", r":\s*provider_failed",
     _ex("A live data provider broke",
         "A provider asked for a live fact (a score, a price, the weather) "
         "answered with an error.",
         "The episode was written from the web search alone - no live score or "
         "price in it.",
         "Most often the plan's limit: BALLDONTLIE's free plan allows 5 "
         "requests a minute, and Finnhub's free plan refuses indices, futures "
         "and foreign listings with HTTP 403.",
         "See Outside service requests for the plan and what the next one raises.")),
    ("live", r":\s*timeout",
     _ex("A live data provider was too slow",
         "A live provider did not answer before the episode had to start.",
         "The episode was written from the web search alone.",
         "The provider was slow, or several lookups queued behind one minute's limit.",
         "Act if it is one provider most of the time.")),
    ("live", r":\s*stale",
     _ex("A live answer was too old to use",
         "The provider answered, but with data older than the episode may say "
         "is current, so it was withheld.",
         "Nothing wrong was said; the figure was left out.",
         "A delayed feed, or a sweep that has not run recently.",
         "Usually nothing to do; a run of them means the sweep stopped.")),
    ("research", r"^no sources",
     _ex("No sources found",
         "Every search rung (Perplexity, then Parallel, then GDELT) came back "
         "empty for this question.",
         "The episode was written without evidence, from what is settled, "
         "with nothing current in it.",
         "A very new or very local question, a misspelled name, or the search "
         "services failing at once.",
         "Search the question yourself; if it has obvious results, a search "
         "service is down.")),
    ("research", r"unavailable$",
     _ex("A search service was not available",
         "A search rung could not be asked (no key, or switched off).",
         "Nothing, if a later rung answered.",
         "A missing key or a service switched off on this server.",
         "Check the key in the environment.")),
    ("research", r"failed$",
     _ex("A search service failed",
         "A search rung raised an error; the next rung was asked instead.",
         "Nothing, if a later rung answered.",
         "An outage, a timeout or a spent plan at that service.",
         "Read the detail; repeated failures mean the service's key or plan.")),
    ("research", r"^evidence cut",
     _ex("The evidence cut did not work",
         "The step that trims the evidence packet before the writer failed, "
         "so the writer read the whole packet.",
         "Nothing: a slightly slower, longer-read episode.",
         "The trimming model timed out or was declined.",
         "Nothing to do unless it is most episodes.")),
    ("writer", r"^opening check",
     _ex("The opening check removed a sentence",
         "The writer opened by orienting ('In this episode...') or another "
         "opening the rules forbid; the guard dropped it before it was spoken.",
         "Nothing wrong; the episode started one sentence later.",
         "The writer breaking an opening rule.",
         "A good example in examples/ teaches it better than another rule.")),
    ("writer", r"^score check",
     _ex("The score check removed a sentence",
         "The writer said a score that is not on the scoreboard; the guard "
         "removed it before it was spoken.",
         "Nothing wrong was said; that sentence was missing.",
         "No live score reached the writer (often BALLDONTLIE's rate limit) "
         "and it reached for one from memory, or the game was not on the "
         "scoreboard yet.",
         "Look at live errors at the same time; fixing those fixes this.")),
    ("writer", r"^figure check",
     _ex("The figure check removed a sentence",
         "The writer said a market figure that no source gave; the guard "
         "removed it before it was spoken.",
         "Nothing wrong was said; that sentence was missing.",
         "No live price reached the writer (often a Finnhub refusal), so it "
         "estimated one.",
         "Look at live errors at the same time.")),
    ("writer", r"^ran out of room",
     _ex("The writer ran out of room",
         "The script was cut off at the writer's token limit, which thinking "
         "and script share.",
         "The episode ended early, and its title, category and follow-up were lost.",
         "A long episode with a lot of thinking, or MAX_OUTPUT_TOKENS set too low.",
         "Raise MAX_OUTPUT_TOKENS if this repeats.")),
    ("writer", r"^refused",
     _ex("The writer was declined",
         "A safety classifier declined the script (see Refusals below).",
         "The listener heard 'I can't put together a briefing on that' instead "
         "of the episode.",
         "A sensitive news subject.",
         "Read the question; an ordinary question declined is worth reporting.")),
    ("writer", r"",
     _ex("The writer failed",
         "The call that writes the script raised an error.",
         "The listener heard nothing, or the episode stopped.",
         "An API error, overload, a network drop or a bad key.",
         "Read the detail; a run of them means Anthropic is down or the key is wrong.")),
    ("edition", r"",
     _ex("An edition episode was not written",
         "One episode of the Trending or DailyFAM edition (written ahead, "
         "nobody waiting) could not be written.",
         "That tile is missing or is written live on its first tap.",
         "A failed brief, search or writer while the edition ran.",
         "Look at the other stages at the same time of day (05:00 / 17:00 ET).")),
    ("episode", r"^no voice",
     _ex("No voice",
         "The server could not reach any voice, so nothing was made.",
         "The listener heard nothing.",
         "The GPU voice worker is down or unreachable.",
         "Check the voice worker (RunPod) and /api/health.")),
    ("episode", r"^failed before any audio",
     _ex("Failed before any audio",
         "The episode failed before its first word was spoken.",
         "The listener heard nothing and saw an error.",
         "Whatever the detail says - usually a writer or voice failure.",
         "Read the detail.")),
    ("episode", r"^no speech",
     _ex("No speech",
         "The episode finished with no audio in it.",
         "The listener heard nothing.",
         "An empty script or a voice that returned silence.",
         "Rare; worth a look every time.")),
    ("episode", r"^stream broke",
     _ex("The stream broke",
         "The audio stopped part-way through.",
         "The listener heard part of the episode.",
         "The voice worker or the network dropped mid-episode.",
         "Check the voice worker if it repeats.")),
    ("voice", r"^no worker can speak",
     _ex("No voice worker can speak",
         "The once-a-minute check found no voice worker it could reach. "
         "Reported once an hour while it lasts.",
         "Every new episode fails with the apology until it is fixed; kept "
         "audio still plays.",
         "The pod moved and nothing told the app its new address, the pod "
         "or its worker stopped, or the address in REMOTE_VOICE_URL is stale.",
         "Read the detail: it names every address tried and why each failed. "
         "Then check the pod on RunPod (Connect tab for its direct address) "
         "and /api/health.")),
    ("voice", r"^registration lapsed",
     _ex("The voice worker stopped registering",
         "The worker used to tell the app where it is every minute and has "
         "stopped. Reported once an hour while it lasts.",
         "Nothing yet, if another address still works. The day the pod moves "
         "or restarts, nobody can find it and every episode fails.",
         "FAM_APP_URL or VOICE_REGISTRY_TOKEN wrong on the pod, the app "
         "unreachable from it, or a worker image old enough to hide the "
         "reason (before §306).",
         "On the pod: check those two settings, search its log for "
         "'registration', and rebuild it from Dockerfile.voice if its "
         "/health has no 'registering' field.")),
    ("episode", r"^voice fell behind",
     _ex("The voice fell behind",
         "The voice made audio slower than it was played, so the listener "
         "heard silence part-way through.",
         "A gap of silence in the middle of the episode.",
         "The GPU was slow or shared, or the network to it was.",
         "A run of these means the voice worker needs a faster GPU.")),
)
_EXPLAIN = [(stage, re.compile(pattern, re.I), words)
            for stage, pattern, words in EXPLAIN]

#: What the detail line itself says, in words, beside the explanation.
HINTS = (
    (r"RateLimited|\b429\b|minute's are spent",
     "Rate limited: the service's per-minute allowance was spent."),
    (r"\b403\b|don't have access|not have access",
     "Refused (403): the plan does not include this - for Finnhub, an index, "
     "future or foreign listing."),
    (r"\b401\b|refused the key",
     "The key was refused (401): wrong key, or a plan the account does not have."),
    (r"Timeout|timed out",
     "It ran out of time."),
    (r"ConnectError|ConnectTimeout|RemoteProtocolError|ReadError",
     "The connection failed or dropped."),
    (r"\b5\d\d\b|Overloaded|overloaded",
     "The service itself had an error or was overloaded."),
)
_HINTS = [(re.compile(pattern), words) for pattern, words in HINTS]


def explain(stage: str, kind: str, detail: str = "") -> dict:
    """What one kind of error means, in words: what happened, what the
    listener noticed, the likely causes and what to do. Never raises."""
    words = None
    for at, pattern, said in _EXPLAIN:
        if at == stage and pattern.search(kind or ""):
            words = said
            break
    out = dict(words or _ex(kind or "Unknown", "Recorded at the " + (stage or "?")
                            + " stage; no explanation is written for it yet.",
                            "", "", "Read the detail and the question."))
    hint = next((said for pattern, said in _HINTS if pattern.search(detail or "")), "")
    if hint:
        out["hint"] = hint
    return out


def _category_of(query: str) -> str:
    """The category an error's question is about, read from its words
    against the category tree (`stories.resolve_category`, no model, no
    embedding), with the facet first: "Sport · nfl". Cached by the hour, so
    the tree's growth reaches the page and a passing failure is never kept
    (review). Never raises."""
    try:
        return _category_cached(query, int(time.time() // 3600))
    except Exception:  # noqa: BLE001 - a report is never load-bearing
        return "Uncategorised"


@functools.lru_cache(maxsize=4096)
def _category_cached(query: str, _hour: int) -> str:
    import stories
    import topics

    node = stories.resolve_category(query) if query else ""
    facet = stories.facet_for(node) if node else ""
    if not facet and query:
        facets = topics.facets_only(topics.tags_for_text(query))
        facet = facets[0] if facets else ""
    label = topics.TAG_LABELS.get(facet, facet)
    if node and node != facet:
        return f"{label} · {node}" if label else node
    return label or "Uncategorised"


def report(now: Optional[float] = None, days: int = 7) -> dict:
    """What the admin page draws. Never raises, never creates the store.

    Counts for the last hour, day and week; per stage and kind, how many in
    the last day and week, when it last happened and what it last said; and
    the newest rows. Each kind also carries what opening it on the page
    shows (10.16 #1): its explanation, the categories of the questions it
    happened on, its times (drawn by hour in the reader's clock) and its
    newest rows. Kinds that differ only by a count are one kind."""
    now = time.time() if now is None else now
    rows: list = []
    steps: list = []
    refused: list = []
    try:
        if exists():
            flush()
            rows = store().since(now - days * DAY)
            steps = store().steps_since(now - days * DAY)
            refused = store().refusals_since(now - days * DAY)
    except Exception as exc:  # noqa: BLE001 - a report is never load-bearing
        log.warning("could not read pipeline errors: %s", exc)

    def counted(since: float, severity: str) -> int:
        return sum(1 for r in rows if r["at"] >= since and r["severity"] == severity)

    kinds: dict = {}
    categories: dict = {}
    for r in rows:  # newest first, so the first seen is the latest
        key = (r["stage"], family(r["kind"]))
        k = kinds.get(key)
        if k is None:
            k = kinds[key] = {"stage": r["stage"], "kind": key[1],
                              "severity": r["severity"], "day": 0, "week": 0,
                              "last_at": r["at"], "last_detail": r["detail"],
                              "last_query": r["query"],
                              "explain": explain(r["stage"], key[1], r["detail"]),
                              "categories": {}, "times": [], "examples": []}
        k["week"] += 1
        if r["at"] >= now - DAY:
            k["day"] += 1
        if r["severity"] == ERROR:
            k["severity"] = ERROR
        query = r["query"] or ""
        if query not in categories:
            categories[query] = _category_of(query) if query else "No question recorded"
        name = categories[query]
        k["categories"][name] = k["categories"].get(name, 0) + 1
        if len(k["times"]) < KIND_TIMES:
            k["times"].append(round(r["at"]))
        if len(k["examples"]) < KIND_EXAMPLES:
            k["examples"].append({"at": r["at"], "kind": r["kind"],
                                  "severity": r["severity"],
                                  "detail": r["detail"], "query": r["query"],
                                  "category": name})
    for k in kinds.values():
        k["categories"] = [{"name": n, "count": c} for n, c in
                           sorted(k["categories"].items(), key=lambda kv: (-kv[1], kv[0]))]
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
        "steps_capped": len(steps) >= STEPS_READ,
        # Every declined model call (§273): how often, for what, and where.
        "refusals": refusals_report(refused, steps, now),
    }
