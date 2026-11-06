"""Keith's incidents: one per cause, from what FAM already records (§488).

keith.md "always" 1, 5, 9: watch every error, report and problem; beyond
what he may fix, tell the owner with a solution plan; show every open alert
until it is resolved. FAM_AGENTS.md, Keith's tab: "A check that fails and
recovers every minute: one incident with a count, not sixty alerts."

**The signals, all already recorded** (nothing new is asked of anyone):

* `pipeline_errors` - a kind of error (stage and kind, counts folded as the
  admin page folds them) seen `IT_INCIDENT_ERRORS` times in the last hour,
  with the explanation FAM already wrote for it (`explain()`);
* the error and usage records themselves falling behind (§480, §482);
* Keith's usage monitors (§483-§487): a figure at `warn` or `urgent`, and a
  monitor that has stopped reading;
* a background loop that stopped with an error (`watch_task`), which before
  this nothing noticed;
* listener reports left open past `REPORT_HOURS` (App Store 1.2 asks for a
  day).

The voice supervisor records its failures in `pipeline_errors` (stage
`voice`, §318), so it is covered there; `/api/health`'s parts are each
covered by the signal behind them.

**An incident** is keyed by its cause, so a cause that keeps happening is
one incident whose count and `last_seen` grow. It closes when its cause has
been quiet for `CLOSE_AFTER_SECONDS` (or, for a state such as a level, as
soon as a round no longer finds it); it reopens - said, with how many times -
if the cause comes back. Every incident carries a **solution plan** in
code: what is broken, what a listener notices, what was tried, the fix
recommended, its cost and risk, and what is needed from the owner. A model
writing these up (`IT_MODEL`) is not used yet.

**Observe only.** Nothing here fixes, changes, holds or deletes anything
(keith.md "never" 1-3). Runs as Netenyahu's `it:watch`, every
`IT_WATCH_SECONDS`, only while Keith may run. Never raises.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import closing
from typing import Optional

import db as store_db
from paths import data_path

log = logging.getLogger("incidents")

NOTICE, WARN, URGENT = "notice", "warn", "urgent"
RANK = {NOTICE: 0, WARN: 1, URGENT: 2}
#: An error kind this many times in an hour opens an incident, and this many
#: makes it urgent.
ERRORS_URGENT = 30
#: Quiet this long, and an incident about events closes.
CLOSE_AFTER_SECONDS = 2 * 3600
#: A listener report left open this long is an incident.
REPORT_HOURS = 20
#: How long closed incidents are kept.
KEEP_DAYS = 90

#: Causes that are states, not events: closed as soon as a round no longer
#: finds them (a level back to ok, a store writing again).
STATE_SOURCES = ("usage", "tracking", "loop", "reports")


def _settings():
    from config import settings

    return settings


# ---------------------------------------------------------------------- store
class IncidentStore:
    """`incidents`, one row per cause, beside the error rows they come from
    (the same database as `pipeline_errors`)."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = data_path("PIPELINE_ERRORS_DB", "pipeline_errors.db", path)
        self._lock = threading.Lock()
        with closing(self._connect()) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS incidents (
                              cause      TEXT PRIMARY KEY,
                              source     TEXT NOT NULL DEFAULT '',
                              severity   TEXT NOT NULL DEFAULT 'warn',
                              title      TEXT NOT NULL DEFAULT '',
                              opened_at  REAL NOT NULL,
                              last_seen  REAL NOT NULL,
                              closed_at  REAL,
                              seen       INTEGER NOT NULL DEFAULT 1,
                              reopened   INTEGER NOT NULL DEFAULT 0,
                              detail     TEXT NOT NULL DEFAULT '{}',
                              plan       TEXT NOT NULL DEFAULT '{}',
                              emailed_at REAL,
                              closed_why TEXT NOT NULL DEFAULT '')""")

    def _connect(self):
        db = store_db.connect(self.path, timeout=5, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    _COLUMNS = ("cause, source, severity, title, opened_at, last_seen, closed_at, "
                "seen, reopened, detail, plan, emailed_at, closed_why")

    def _rows(self, where: str, args: tuple) -> list:
        with closing(self._connect()) as db:
            cursor = db.execute(f"SELECT {self._COLUMNS} FROM incidents {where}", args)
            names = [c[0] for c in cursor.description]
            out = []
            for values in cursor:
                row = dict(zip(names, values))
                row["detail"] = json.loads(row["detail"] or "{}")
                row["plan"] = json.loads(row["plan"] or "{}")
                out.append(row)
            return out

    def get(self, cause: str) -> Optional[dict]:
        rows = self._rows("WHERE cause = ?", (cause,))
        return rows[0] if rows else None

    def open_incidents(self) -> list:
        return self._rows("WHERE closed_at IS NULL", ())

    def closed_since(self, at: float) -> list:
        return self._rows("WHERE closed_at IS NOT NULL AND closed_at >= ?", (at,))

    def seen(self, finding: dict, now: float) -> str:
        """Record a finding: `opened`, `reopened` or `seen` (already open)."""
        with self._lock, closing(self._connect()) as db:
            row = db.execute("SELECT closed_at, seen, severity FROM incidents "
                             "WHERE cause = ?", (finding["cause"],)).fetchone()
            payload = (finding["source"], finding["severity"], finding["title"],
                       json.dumps(finding.get("detail") or {}),
                       json.dumps(finding.get("plan") or {}))
            if row is None:
                db.execute(f"INSERT INTO incidents ({self._COLUMNS}) VALUES "
                           "(?, ?, ?, ?, ?, ?, NULL, 1, 0, ?, ?, NULL, '')",
                           (finding["cause"], payload[0], payload[1], payload[2],
                            now, now, payload[3], payload[4]))
                return "opened"
            if row[0] is not None:
                db.execute("UPDATE incidents SET source = ?, severity = ?, title = ?, "
                           "detail = ?, plan = ?, opened_at = ?, last_seen = ?, "
                           "closed_at = NULL, seen = 1, reopened = reopened + 1, "
                           "emailed_at = NULL, closed_why = '' WHERE cause = ?",
                           (*payload, now, now, finding["cause"]))
                return "reopened"
            # Never quieter than it was while it stays open: a worse round
            # raises it, a calmer one does not hide that it was worse.
            severity = max(row[2], finding["severity"], key=lambda s: RANK.get(s, 0))
            db.execute("UPDATE incidents SET severity = ?, title = ?, detail = ?, "
                       "plan = ?, last_seen = ?, seen = seen + 1 WHERE cause = ?",
                       (severity, payload[2], payload[3], payload[4], now,
                        finding["cause"]))
            return "seen"

    def close(self, cause: str, why: str, now: float) -> None:
        with self._lock, closing(self._connect()) as db:
            db.execute("UPDATE incidents SET closed_at = ?, closed_why = ? "
                       "WHERE cause = ? AND closed_at IS NULL", (now, why[:200], cause))
            db.execute("DELETE FROM incidents WHERE closed_at IS NOT NULL "
                       "AND closed_at < ?", (now - KEEP_DAYS * 86400,))

    def mark_emailed(self, cause: str, now: float) -> None:
        with self._lock, closing(self._connect()) as db:
            db.execute("UPDATE incidents SET emailed_at = ? WHERE cause = ?",
                       (now, cause))

    def clear(self) -> None:
        with self._lock, closing(self._connect()) as db:
            db.execute("DELETE FROM incidents")


_STORE: list = [None]
_STORE_LOCK = threading.Lock()


def store() -> IncidentStore:
    with _STORE_LOCK:
        if _STORE[0] is None:
            _STORE[0] = IncidentStore()
        return _STORE[0]


def reset(incident_store: Optional[IncidentStore] = None) -> None:
    """For tests: this store (or a fresh one on next use), and no loops seen."""
    with _STORE_LOCK:
        _STORE[0] = incident_store
    with _LOOPS_LOCK:
        LOOPS.clear()
    FEEDBACK[0] = None


def exists() -> bool:
    if _STORE[0] is not None:
        return True
    return store_db.has_tables(data_path("PIPELINE_ERRORS_DB", "pipeline_errors.db"))


# ------------------------------------------------------------------ the loops
#: Background loops that stopped with an error, in this process (§488):
#: name -> {"at", "error"}. Before this a loop that died was gone in silence.
LOOPS: dict = {}
_LOOPS_LOCK = threading.Lock()


def watch_task(task) -> None:
    """Note it if this background task ever stops with an error. A task that
    returns (a feature switched off says so and returns at boot) or is
    cancelled at shutdown is not an incident. Never raises."""
    try:
        name = getattr(task.get_coro(), "__qualname__", "") or task.get_name()
    except Exception:  # noqa: BLE001
        name = "a background task"

    def done(finished) -> None:
        try:
            if finished.cancelled():
                return
            exc = finished.exception()
            if exc is not None:
                with _LOOPS_LOCK:
                    LOOPS[name] = {"at": time.time(), "error": type(exc).__name__}
                log.error("background loop %s stopped: %s", name, type(exc).__name__)
        except Exception:  # noqa: BLE001
            pass

    try:
        task.add_done_callback(done)
    except Exception:  # noqa: BLE001
        pass


#: The listener reports, set at startup (`register`): the app's store.
FEEDBACK: list = [None]


# ------------------------------------------------------------------- findings
def finding(cause: str, source: str, severity: str, title: str, *,
            detail: Optional[dict] = None, problem: str = "", heard: str = "",
            tried: str = "Nothing: Keith observes only until a fix is switched on.",
            recommended: str = "", cost_risk: str = "", needs: str = "") -> dict:
    return {"cause": cause, "source": source, "severity": severity, "title": title,
            "detail": detail or {},
            "plan": {"problem": problem, "listener_notices": heard, "tried": tried,
                     "recommended": recommended, "cost_and_risk": cost_risk,
                     "needs_from_owner": needs}}


def from_errors(now: float) -> list:
    """An error kind seen `IT_INCIDENT_ERRORS` times in the last hour."""
    import pipeline_errors

    if not pipeline_errors.exists():
        return []
    pipeline_errors.flush()
    rows = pipeline_errors.store().since(now - 3600)
    counts: dict = {}
    for r in rows:
        if r["severity"] != pipeline_errors.ERROR:
            continue
        key = (r["stage"], pipeline_errors.family(r["kind"]))
        counts[key] = counts.get(key, 0) + 1
    floor = max(1, int(getattr(_settings(), "it_incident_errors", 3) or 3))
    out = []
    for (stage, kind), n in counts.items():
        if n < floor:
            continue
        said = pipeline_errors.explain(stage, kind)
        out.append(finding(
            f"errors:{stage}:{kind}", "errors", URGENT if n >= ERRORS_URGENT else WARN,
            f"{said.get('title') or kind}: {n} in the last hour",
            detail={"stage": stage, "kind": kind, "last_hour": n,
                    "hint": said.get("hint", "")},
            problem=said.get("what", ""), heard=said.get("heard", ""),
            recommended=said.get("act", ""),
            cost_risk=f"Likely causes: {said.get('causes', '') or 'not written yet'}",
            needs="Your eye on /admin's errors for this kind; nothing else yet."))
    return out


def from_tracking() -> list:
    import pipeline_errors
    import provider_usage

    out = []
    for name, status, page in (("provider_usage", provider_usage.status(),
                                "the outside-services table"),
                               ("pipeline_errors", pipeline_errors.status(),
                                "the errors page")):
        if status.get("behind"):
            out.append(finding(
                f"tracking:{name}", "tracking", URGENT,
                f"FAM's {name} records are not being written",
                detail={k: status.get(k) for k in ("waiting", "failures",
                                                   "last_failure", "dropped_total")},
                problem=f"{status.get('last_failure') or 'writes are failing'}; "
                        f"{status.get('waiting', 0)} waiting in memory.",
                heard="Nothing directly; /admin is short of what it could not keep.",
                recommended="Check storage and the database in /api/health; the "
                            "rows are written as soon as the database answers.",
                cost_risk="Rows past 2,000 waiting are dropped (counted).",
                needs="A look at the database if it does not recover within the hour."))
    return out


def from_usage() -> list:
    import usage_monitors

    out = []
    for latest in usage_monitors.report().get("services", []):
        service = latest.get("service", "?")
        if latest.get("never_read"):
            continue
        if not latest.get("ok"):
            why = str(latest.get("why", ""))
            if why.startswith("not configured") or "not on Atlas" in why:
                continue                      # a deployment's choice, not a fault
            out.append(finding(
                f"usage:{service}:unread", "usage", NOTICE,
                f"Keith cannot read {service}'s usage",
                detail={"why": why}, problem=why,
                heard="Nothing; the usage warnings for it are blind meanwhile.",
                recommended="Check the key and the address in FAM_SECRETS.",
                needs="A key that answers, if it was rotated or revoked."))
            continue
        for f in latest.get("figures", []):
            if f.get("level") not in (WARN, URGENT):
                continue
            share = f.get("share")
            out.append(finding(
                f"usage:{service}:{f['name']}", "usage", f["level"],
                f"{service}: {f['name']} at "
                + (f"{share:.0%}" if share is not None else "its limit")
                + (f" of {f['limit']} {f['unit']}" if f.get("limit") else ""),
                detail={"value": f.get("value"), "limit": f.get("limit"),
                        "unit": f.get("unit"), "projected": f.get("projected"),
                        "limit_source": f.get("limit_source")},
                problem=f"{f['name']} is {f.get('value')} {f.get('unit')} against "
                        f"{f.get('limit')} ({f.get('limit_source')})"
                        + (f"; on pace for {f['projected']} this month"
                           if f.get("projected") else ""),
                heard="Nothing yet; at the limit the service refuses, slows or bills more.",
                recommended=f"Move up before the limit: {f.get('next') or 'see the plan page'}.",
                cost_risk=f.get("next") or "",
                needs="Your decision on the plan; Keith never changes one."))
    return out


def from_loops() -> list:
    with _LOOPS_LOCK:
        stopped = dict(LOOPS)
    return [finding(
        f"loop:{name}", "loop", URGENT, f"Background loop {name} stopped",
        detail=info, problem=f"It raised {info['error']} and is no longer running.",
        heard="Whatever it keeps fresh goes stale (editions, sweeps, the voice's "
              "address) until the server restarts.",
        recommended="Read the server log for the traceback; a restart brings it "
                    "back, which is a person's action (keith.md never 2).",
        cost_risk="A restart is seconds of downtime; leaving it is staleness.",
        needs="The fix for the error in the log, and a restart.")
        for name, info in stopped.items()]


def from_reports(now: float) -> list:
    feedback = FEEDBACK[0]
    if feedback is None:
        return []
    try:
        old = [r for r in feedback.list("open", limit=200)
               if float(r.get("created") or now) < now - REPORT_HOURS * 3600]
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read the listener reports: %s", exc)
        return []
    if not old:
        return []
    return [finding(
        "reports:open", "reports", WARN,
        f"{len(old)} listener report(s) open over {REPORT_HOURS} hours",
        detail={"open_over": len(old)},
        problem="Reports listeners sent are still unanswered.",
        heard="The listener who reported it has heard nothing back.",
        recommended="Resolve them in /admin's feedback inbox (App Store 1.2 "
                    "asks for action within a day).",
        needs="Your review of each report.")]


def findings(now: float) -> tuple[list, set]:
    """Every finding this round, and the sources that were read (a source that
    could not be read closes nothing)."""
    out, read = [], set()
    for source, get in (("errors", lambda: from_errors(now)),
                        ("tracking", from_tracking), ("usage", from_usage),
                        ("loop", from_loops), ("reports", lambda: from_reports(now))):
        try:
            out.extend(get())
            read.add(source)
        except Exception as exc:  # noqa: BLE001 - one source never stops the rest
            log.warning("incidents: could not read %s: %s", source, exc)
    return out, read


# ------------------------------------------------------------------- the round
def watch(now: Optional[float] = None) -> dict:
    """One round: open, update, reopen and close incidents. Returns what it
    did. Never raises."""
    now = time.time() if now is None else now
    done = {"opened": [], "reopened": [], "seen": 0, "closed": []}
    try:
        found, read = findings(now)
        target = store()
        causes = set()
        for f in found:
            causes.add(f["cause"])
            what = target.seen(f, now)
            if what == "seen":
                done["seen"] += 1
            else:
                done[what].append(f["cause"])
        for row in target.open_incidents():
            if row["cause"] in causes or row["source"] not in read:
                continue
            if row["source"] in STATE_SOURCES:
                target.close(row["cause"], "no longer found", now)
                done["closed"].append(row["cause"])
            elif now - row["last_seen"] >= CLOSE_AFTER_SECONDS:
                target.close(row["cause"], "quiet for two hours", now)
                done["closed"].append(row["cause"])
    except Exception as exc:  # noqa: BLE001 - the round never raises
        log.warning("incidents: the round failed: %s", exc)
        done["error"] = type(exc).__name__
    return done


async def handler(_task: str) -> None:
    """Netenyahu's `it:watch`: one round, observe only."""
    import asyncio

    from agents import roster

    if not roster.status("it")["may_run"]:
        log.info("incidents not watched: %s", roster.status("it")["why_not"])
        return
    await asyncio.to_thread(watch)


def register(feedback_store=None) -> None:
    """At startup, with `AGENTS=1`: the listener reports, and Keith's task."""
    from agents import netenyahu

    FEEDBACK[0] = feedback_store
    netenyahu.HANDLERS[("it", "watch")] = handler


def report(now: Optional[float] = None, days: int = 7) -> dict:
    """For /admin: open incidents, worst first, and those closed in `days`.
    Never raises, never creates the store."""
    now = time.time() if now is None else now
    try:
        if not exists():
            return {"open": [], "closed": [], "counts": {}}
        open_ = sorted(store().open_incidents(),
                       key=lambda r: (-RANK.get(r["severity"], 0), -r["last_seen"]))
        closed = sorted(store().closed_since(now - days * 86400),
                        key=lambda r: -r["closed_at"])[:50]
    except Exception as exc:  # noqa: BLE001 - a report is never load-bearing
        log.warning("could not read the incidents: %s", exc)
        return {"open": [], "closed": [], "counts": {}, "error": type(exc).__name__}
    counts: dict = {}
    for r in open_:
        counts[r["severity"]] = counts.get(r["severity"], 0) + 1
    return {"open": open_, "closed": closed, "counts": counts}


def health() -> dict:
    """`/api/health`'s `incidents`: open counts by severity, and the loops
    that stopped in this process. Never raises."""
    try:
        counts = report()["counts"] if exists() else {}
    except Exception:  # noqa: BLE001
        counts = {}
    with _LOOPS_LOCK:
        loops = sorted(LOOPS)
    return {"open": counts, "loops_stopped": loops}
