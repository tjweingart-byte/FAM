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
#: After the owner says an error kind is fixed, this long with none of it,
#: and Keith closes it - checked, not believed (keith.md "always" 11).
CLAIM_QUIET_SECONDS = 30 * 60
#: At most this many urgent emails in one round.
EMAILS_PER_ROUND = 5

#: Causes that are states, not events: closed as soon as a round no longer
#: finds them (a level back to ok, a store writing again).
STATE_SOURCES = ("usage", "tracking", "loop", "reports", "quota", "agents", "cost")


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
                              closed_why TEXT NOT NULL DEFAULT '',
                              fixed_claim_at REAL)""")

    def _connect(self):
        db = store_db.connect(self.path, timeout=5, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    _COLUMNS = ("cause, source, severity, title, opened_at, last_seen, closed_at, "
                "seen, reopened, detail, plan, emailed_at, closed_why, fixed_claim_at")

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
            row = db.execute("SELECT closed_at, seen, severity, plan, detail FROM "
                             "incidents WHERE cause = ?", (finding["cause"],)).fetchone()
            payload = (finding["source"], finding["severity"], finding["title"],
                       json.dumps(finding.get("detail") or {}),
                       json.dumps(finding.get("plan") or {}))
            if row is None:
                db.execute(f"INSERT INTO incidents ({self._COLUMNS}) VALUES "
                           "(?, ?, ?, ?, ?, ?, NULL, 1, 0, ?, ?, NULL, '', NULL)",
                           (finding["cause"], payload[0], payload[1], payload[2],
                            now, now, payload[3], payload[4]))
                return "opened"
            if row[0] is not None:
                db.execute("UPDATE incidents SET source = ?, severity = ?, title = ?, "
                           "detail = ?, plan = ?, opened_at = ?, last_seen = ?, "
                           "closed_at = NULL, seen = 1, reopened = reopened + 1, "
                           "emailed_at = NULL, closed_why = '', fixed_claim_at = NULL "
                           "WHERE cause = ?",
                           (*payload, now, now, finding["cause"]))
                return "reopened"
            # Never quieter than it was while it stays open: a worse round
            # raises it, a calmer one does not hide that it was worse.
            severity = max(row[2], finding["severity"], key=lambda s: RANK.get(s, 0))
            # What a fix recorded as tried (§490) outlives the round's rewrite.
            kept = json.loads(row[3] or "{}").get("tried", "")
            if kept.startswith("Keith "):
                plan = dict(finding.get("plan") or {})
                plan["tried"] = kept
                payload = payload[:4] + (json.dumps(plan),)
            # So does what was found after the owner said it was fixed (§495).
            note = json.loads(row[4] or "{}").get("after_your_fix", "")
            if note and not (finding.get("detail") or {}).get("after_your_fix"):
                payload = payload[:3] + (json.dumps({**(finding.get("detail") or {}),
                                                     "after_your_fix": note}),) + payload[4:]
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

    def claim_fixed(self, cause: str, at: Optional[float]) -> None:
        """When the owner said it was fixed (None clears it)."""
        with self._lock, closing(self._connect()) as db:
            db.execute("UPDATE incidents SET fixed_claim_at = ? WHERE cause = ? "
                       "AND closed_at IS NULL", (at, cause))

    def note(self, cause: str, key: str, text: str) -> None:
        """One line in an open incident's detail (§495)."""
        row = self.get(cause)
        if row is None or row["closed_at"] is not None:
            return
        detail = {**(row["detail"] or {}), key: text[:300]}
        with self._lock, closing(self._connect()) as db:
            db.execute("UPDATE incidents SET detail = ? WHERE cause = ?",
                       (json.dumps(detail), cause))

    def mark_emailed(self, cause: str, now: float) -> None:
        with self._lock, closing(self._connect()) as db:
            db.execute("UPDATE incidents SET emailed_at = ? WHERE cause = ?",
                       (now, cause))

    # -- the fix report (§490) ------------------------------------------
    def _fix_table(self, db) -> None:
        db.execute("""CREATE TABLE IF NOT EXISTS incident_fixes (
                          at        REAL NOT NULL,
                          cause     TEXT NOT NULL,
                          opened_at REAL NOT NULL,
                          fix       TEXT NOT NULL,
                          mode      TEXT NOT NULL,
                          outcome   TEXT NOT NULL,
                          how       TEXT NOT NULL DEFAULT '')""")

    def add_fix(self, cause: str, opened_at: float, fix: str, mode: str,
                outcome: str, how: str, at: float) -> None:
        with self._lock, closing(self._connect()) as db:
            self._fix_table(db)
            db.execute("INSERT INTO incident_fixes (at, cause, opened_at, fix, mode, "
                       "outcome, how) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (at, cause, opened_at, fix, mode, outcome, how[:500]))
            db.execute("DELETE FROM incident_fixes WHERE at < ?",
                       (at - KEEP_DAYS * 86400,))

    def fixes_since(self, at: float) -> list:
        with closing(self._connect()) as db:
            self._fix_table(db)
            cursor = db.execute("SELECT at, cause, opened_at, fix, mode, outcome, how "
                                "FROM incident_fixes WHERE at >= ? ORDER BY at DESC",
                                (at,))
            names = [c[0] for c in cursor.description]
            return [dict(zip(names, values)) for values in cursor]

    def tried(self, cause: str, opened_at: float, fix: str) -> list:
        """What was already recorded for this fix on this opening."""
        with closing(self._connect()) as db:
            self._fix_table(db)
            return [r[0] for r in db.execute(
                "SELECT outcome FROM incident_fixes WHERE cause = ? AND opened_at = ? "
                "AND fix = ?", (cause, opened_at, fix))]

    def set_tried(self, cause: str, text: str) -> None:
        """What the plan says was tried, after a fix."""
        row = self.get(cause)
        if row is None:
            return
        plan = dict(row["plan"] or {})
        plan["tried"] = text[:500]
        with self._lock, closing(self._connect()) as db:
            db.execute("UPDATE incidents SET plan = ? WHERE cause = ?",
                       (json.dumps(plan), cause))

    def clear(self) -> None:
        with self._lock, closing(self._connect()) as db:
            db.execute("DELETE FROM incidents")
            self._fix_table(db)
            db.execute("DELETE FROM incident_fixes")


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
        ERRORS_LAST.clear()
    COUNTS.update(at=None, open={})
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
    name = loop_name(task)

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


def loop_name(task) -> str:
    """`module.function` for a task's coroutine (three loops are each called
    `run_forever`), read while it still runs."""
    try:
        coro = task.get_coro()
        frame = getattr(coro, "cr_frame", None)
        module = frame.f_globals.get("__name__", "") if frame is not None else ""
        qualname = getattr(coro, "__qualname__", "") or task.get_name()
        return f"{module}.{qualname}" if module else qualname
    except Exception:  # noqa: BLE001
        return "a background task"


#: Loops a fix may start again once (§490): `module.function` -> the
#: zero-argument coroutine function. Registered by the app at startup.
RESTARTABLE: dict = {}


def restartable(*functions) -> None:
    for fn in functions:
        RESTARTABLE[f"{fn.__module__}.{fn.__qualname__}"] = fn


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


#: Each error kind's newest row in the last hour, from the round's read
#: (§495): an owner's "fixed" is checked against it even once the kind has
#: fallen below the floor that opens an incident.
ERRORS_LAST: dict = {}


def from_errors(now: float) -> list:
    """An error kind seen `IT_INCIDENT_ERRORS` times in the last hour."""
    import pipeline_errors

    if not pipeline_errors.exists():
        return []
    pipeline_errors.flush()
    rows = pipeline_errors.store().since(now - 3600)
    counts: dict = {}
    last: dict = {}
    for r in rows:
        if r["severity"] != pipeline_errors.ERROR:
            continue
        key = (r["stage"], pipeline_errors.family(r["kind"]))
        counts[key] = counts.get(key, 0) + 1
        last[key] = max(last.get(key, 0.0), float(r["at"]))
    with _LOOPS_LOCK:
        ERRORS_LAST.clear()
        ERRORS_LAST.update({f"errors:{stage}:{kind}": at for (stage, kind), at in last.items()})
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
                    "last_at": last[(stage, kind)], "hint": said.get("hint", "")},
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


def from_quotas(now: float) -> list:
    """A data provider's daily or monthly quota (§480's `allowance`) at the
    warning share (§490): a rationed provider stops itself at its ceiling,
    so this says so before a rail or a score goes missing."""
    import provider_usage

    s = _settings()
    warn = float(getattr(s, "it_warn_share", 0.8) or 0.8)
    urgent = float(getattr(s, "it_urgent_share", 0.95) or 0.95)
    out = []
    for row in provider_usage.report(now):
        parts = [row] + list(row.get("breakdown") or [])
        for part in parts:
            allowance = part.get("allowance") or {}
            share = allowance.get("share")
            if allowance.get("kind") != "quota" or share is None or share < warn:
                continue
            label = part.get("label") if part is not row else row["label"]
            name = row["label"] if part is row else f"{row['label']} ({label})"
            out.append(finding(
                f"quota:{row['provider']}:{part.get('league', '')}", "quota",
                URGENT if share >= urgent else WARN,
                f"{name}: {share:.0%} of its {allowance['period']}'s "
                f"{allowance['count']:,} {allowance['unit']}",
                detail={"used": allowance.get("used"), "count": allowance["count"],
                        "period": allowance["period"]},
                problem=f"{allowance.get('used'):,} of {allowance['count']:,} "
                        f"{allowance['unit']} used this {allowance['period']}.",
                heard="At the ceiling FAM stops asking it until the "
                      f"{allowance['period']} resets: what it feeds goes stale.",
                recommended=f"Next plan: {row.get('next') or 'see the services page'}.",
                cost_risk=row.get("next") or "",
                needs="Your decision on the plan; Keith never changes one."))
    return out


def from_cost(now: float) -> list:
    """What an episode costs against the owner's plan (§492, `cost_watch`)."""
    import cost_watch

    return cost_watch.findings(now)


def from_agents(now: float, fresh: bool = False) -> list:
    """Each agent against its rule book, and what Phase 3 left Keith to watch
    (§491, `agent_checks`): at most hourly; between, the last findings stand."""
    import agent_checks

    return agent_checks.findings(now, force=fresh)


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


def findings(now: float, fresh: bool = False) -> tuple[list, set]:
    """Every finding this round, and the sources that were read (a source that
    could not be read closes nothing). `fresh` (the owner's "I fixed it",
    §495) runs the agents' checks now rather than standing on the hour's."""
    out, read = [], set()
    for source, get in (("errors", lambda: from_errors(now)),
                        ("tracking", from_tracking), ("usage", from_usage),
                        ("loop", from_loops), ("reports", lambda: from_reports(now)),
                        ("quota", lambda: from_quotas(now)),
                        ("agents", lambda: from_agents(now, fresh)),
                        ("cost", lambda: from_cost(now))):
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
            row = target.get(f["cause"])
            claim = (row or {}).get("fixed_claim_at") if row and row["closed_at"] is None else None
            if claim and f["source"] == "errors":
                last_at = float(f["detail"].get("last_at") or 0)
                if last_at <= claim:
                    # Nothing new since the owner said fixed: close once quiet
                    # long enough, and until then leave it as it is.
                    if now - claim >= CLAIM_QUIET_SECONDS:
                        target.close(f["cause"], "you said fixed; Keith saw none "
                                     f"for {CLAIM_QUIET_SECONDS // 60} minutes after", now)
                        done["closed"].append(f["cause"])
                    continue
                f["detail"]["after_your_fix"] = (
                    "still happening after you said it was fixed, at "
                    + time.strftime("%H:%M UTC", time.gmtime(claim)))
                target.claim_fixed(f["cause"], None)
            what = target.seen(f, now)
            if what == "seen":
                done["seen"] += 1
            else:
                done[what].append(f["cause"])
        for row in target.open_incidents():
            if row["cause"] in causes or row["source"] not in read:
                continue
            claim = row["fixed_claim_at"]
            if claim and row["source"] == "errors":
                # Below the floor now, but checked, not believed (§495): any of
                # it since the claim keeps it open; none closes it once quiet.
                with _LOOPS_LOCK:
                    last_at = ERRORS_LAST.get(row["cause"], 0.0)
                if last_at > claim:
                    target.note(row["cause"], "after_your_fix",
                                "still happening after you said it was fixed, at "
                                + time.strftime("%H:%M UTC", time.gmtime(claim)))
                    target.claim_fixed(row["cause"], None)
                elif now - claim >= CLAIM_QUIET_SECONDS:
                    target.close(row["cause"], "you said fixed; Keith saw none "
                                 f"for {CLAIM_QUIET_SECONDS // 60} minutes after", now)
                    done["closed"].append(row["cause"])
                continue
            if row["source"] in STATE_SOURCES:
                target.close(row["cause"], "no longer found", now)
                done["closed"].append(row["cause"])
            elif now - row["last_seen"] >= CLOSE_AFTER_SECONDS:
                target.close(row["cause"], "quiet for two hours", now)
                done["closed"].append(row["cause"])
        done["emailed"] = email_urgent(now)
        _count(target.open_incidents(), now)
    except Exception as exc:  # noqa: BLE001 - the round never raises
        log.warning("incidents: the round failed: %s", exc)
        done["error"] = type(exc).__name__
    return done


def owner_fixed(cause: str, now: Optional[float] = None) -> dict:
    """The owner says this is fixed: Keith checks rather than believes
    (keith.md "always" 11). A state is re-read now and closed only if it is
    gone (read fresh: a usage service is polled again and the agents'
    checks run now, §495); an error kind is watched, and closed once
    `CLAIM_QUIET_SECONDS` pass with none of it. Never raises."""
    now = time.time() if now is None else now
    try:
        row = store().get(cause)
        if row is None or row["closed_at"] is not None:
            return {"state": "closed", "message": "Already closed."}
        if row["source"] == "errors":
            store().claim_fixed(cause, now)
            return {"state": "watching",
                    "message": f"Keith closes it once {CLAIM_QUIET_SECONDS // 60} "
                               "minutes pass with none of it, and says so if it "
                               "happens again."}
        if row["source"] == "usage":
            # Re-read the service now, not the hour's reading (§495).
            import asyncio
            import usage_monitors

            service = cause.split(":")[1] if cause.count(":") >= 2 else ""
            if service in usage_monitors.MONITORS:
                asyncio.run(usage_monitors.poll(only={service}))
        found, read = findings(now, fresh=True)
        if row["source"] not in read:
            return {"state": "unknown",
                    "message": "Keith could not re-check it just now; it stays open."}
        again = next((f for f in found if f["cause"] == cause), None)
        if again is not None:
            again["detail"]["after_your_fix"] = (
                "still found when Keith re-checked after you said it was fixed, at "
                + time.strftime("%H:%M UTC", time.gmtime(now)))
            store().seen(again, now)
            return {"state": "still_open",
                    "message": "Keith re-checked: it is still there. " + again["title"]}
        store().close(cause, "you said fixed; Keith's re-check no longer finds it", now)
        return {"state": "closed", "message": "Keith re-checked: it is gone. Closed."}
    except Exception as exc:  # noqa: BLE001
        log.warning("incidents: could not check %r: %s", cause, exc)
        return {"state": "unknown", "message": f"The check failed: {type(exc).__name__}"}


def email_ready() -> dict:
    """Whether an urgent incident can be emailed, and if not, why."""
    import mail

    to = (getattr(_settings(), "it_alert_email", "") or "").strip()
    if not to:
        return {"ready": False, "to": "", "why": "IT_ALERT_EMAIL is not set"}
    status = mail.status()
    if not status.get("available"):
        return {"ready": False, "to": to, "why": status.get("reason", "")}
    return {"ready": True, "to": to, "why": ""}


def email_urgent(now: float) -> list:
    """Email each open urgent incident not emailed yet - once, and again only
    if it reopens (decided items 10 and 23). A failed send is tried next
    round; the pop-up shows it either way. Returns the causes sent."""
    ready = email_ready()
    if not ready["ready"]:
        return []
    import mail

    sent = []
    for row in store().open_incidents():
        if len(sent) >= EMAILS_PER_ROUND:
            break
        if row["severity"] != URGENT or row["emailed_at"]:
            continue
        plan = row["plan"] or {}
        body = "\n".join([
            row["title"], "",
            f"Problem: {plan.get('problem', '')}",
            f"What a listener notices: {plan.get('listener_notices', '')}",
            f"Tried: {plan.get('tried', '')}",
            f"Recommended: {plan.get('recommended', '')}",
            f"Cost and risk: {plan.get('cost_and_risk', '')}",
            f"Needed from you: {plan.get('needs_from_owner', '')}", "",
            "Every open alert is on any /admin page until it is resolved.",
            "- Keith (FAM)"])
        if mail.send(ready["to"], f"[FAM urgent] {row['title']}"[:200], body):
            store().mark_emailed(row["cause"], now)
            sent.append(row["cause"])
    return sent


async def handler(_task: str) -> None:
    """Netenyahu's `it:watch`: one round, observe only."""
    import asyncio

    from agents import roster

    if not roster.status("it")["may_run"]:
        log.info("incidents not watched: %s", roster.status("it")["why_not"])
        return
    await asyncio.to_thread(watch)
    # Then his closed list of fixes: observe only until each is switched on
    # (§490). On the event loop: a fix may start a task or call the voice.
    import keith_fixes

    await keith_fixes.act()


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
    counts = _count(open_, now)
    return {"open": open_, "closed": closed, "counts": counts}


#: Open incidents by severity as last read (a round or /admin), for
#: `/api/health` (§495): health reads memory, never the store.
COUNTS: dict = {"at": None, "open": {}}


def _count(open_rows: list, now: float) -> dict:
    counts: dict = {}
    for r in open_rows:
        counts[r["severity"]] = counts.get(r["severity"], 0) + 1
    COUNTS.update(at=now, open=counts)
    return counts


def health() -> dict:
    """`/api/health`'s `incidents`: open counts by severity as last read, when,
    and the loops that stopped in this process. Memory only (§495): health
    runs on the event loop and never opens or creates a store."""
    with _LOOPS_LOCK:
        loops = sorted(LOOPS)
    return {"open": dict(COUNTS["open"]), "counted_at": COUNTS["at"],
            "loops_stopped": loops}
