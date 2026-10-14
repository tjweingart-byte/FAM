"""Which live provider each written episode was routed to, and what it said (§264).

Every researched episode is searched (Parallel, Perplexity, GDELT), and a live
provider - API-Sports, Finnhub, Polymarket, weather - is asked **beside** the
search only when the brief names a `live_domain`. So an episode that should
have had a scoreboard and did not has one of four causes, and from the outside
they all look the same:

* **not routed** - the brief left `live_domain` empty;
* **not routed, EI degraded** - there was no brief, so nothing could name one;
* **routed, no answer** - a provider was asked and returned `no_entity`,
  `timeout`, `not_configured` and so on (`live_facts.OUTCOMES`);
* **answered** - a provider's facts reached the writer.

Since §265 a domain may also come from code (`live_route`): `route` says
which - `brief`, `code: <why>`, or `local weather`.

`live_facts.lookup` already knew which, per provider, and kept it only on the
episode's notes and in a log line. This keeps one row per written episode so
`/admin` can say which cause is the common one before anyone changes the
routing.

A not-routed row is also checked, in code and without a request, for signs it
was a live question after all (`suspect`): a team named in a league catalogue
this server has already read, a named broadcast slot, or a market word. It is
a pointer for a person reading the page, never a routing decision.

**Never on the critical path.** `record` appends to a list in memory and
returns; a background thread writes at most every `FLUSH_SECONDS`, and before
every report. It never raises. Rows older than `KEEP_DAYS` are deleted on
write. No listener id is stored - the question, the brief's reading of it and
the providers' answers, nothing else.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from contextlib import closing
from typing import Optional

import db as store_db
from paths import data_path

log = logging.getLogger("live_routing")

DAY = 86400
#: How long a row is kept. Long enough to compare a week with the one before.
KEEP_DAYS = 14
#: How often, at most, rows held in memory are written out.
FLUSH_SECONDS = 10.0
#: Rows held in memory at most, if the store cannot be written; the oldest go.
MAX_PENDING = 2000
#: The newest rows the admin page lists.
RECENT = 40

ANSWERED = "answered"
NO_ANSWER = "routed, no answer"
NOT_ROUTED = "not routed"
DEGRADED = "not routed, EI degraded"
VERDICTS = (ANSWERED, NO_ANSWER, NOT_ROUTED, DEGRADED)

#: Words that say a question is about a price. Only ever a `suspect` flag on
#: a not-routed row for a person to look at - never a route.
_MARKET_WORDS = re.compile(
    r"\b(stock|stocks|shares|share price|ticker|nasdaq|dow jones|s&p 500|"
    r"market cap|earnings)\b", re.I)


def verdict(domain: str, outcome: str, degraded: bool) -> str:
    if domain:
        return ANSWERED if outcome == "facts" else NO_ANSWER
    return DEGRADED if degraded else NOT_ROUTED


def suspect(query: str, subject: str = "") -> str:
    """Why a question the brief did not route looks live anyway, or "".

    Spends nothing: only catalogues this server already holds are read."""
    text = " ".join(t for t in (query, subject) if t)
    if not text.strip():
        return ""
    try:
        import named_slots

        slot = named_slots.find(text)
        if slot is not None:
            return f"sports: names {slot.name}"
    except Exception:  # noqa: BLE001 - a hint is never load-bearing
        pass
    import live_route

    held = live_route.catalogue_team(text)
    if held:
        return f"sports: names {held}"
    found = _MARKET_WORDS.search(text)
    if found:
        return f"markets: says {found.group(0).lower()!r}"
    return ""


def row_for(query: str, brief, live) -> dict:
    """One row from an episode's brief and its `LiveLookup` (either may be None)."""
    domain = str(getattr(brief, "live_domain", "") or "") if brief else ""
    route = "brief" if domain else ""
    coded = str(getattr(brief, "live_route", "") or "") if brief else ""
    if not domain and coded:
        # Asked because code routed it (`live_route`, §265).
        domain = coded
        route = "code: " + str(getattr(brief, "live_route_why", "") or "")
    # The local ladder's weather is a live answer in the weather domain
    # (`ScriptGenerator.prepare`), whatever the brief named.
    if live is not None and not domain:
        domain = str(getattr(live, "domain", "") or "")
        route = "local weather" if domain else ""
    degraded = bool(getattr(brief, "degraded", False)) if brief else True
    outcome = str(getattr(live, "outcome", "") or "") if live is not None else ""
    if domain and not outcome:
        outcome = "not_asked"
    attempts = [list(a)[:3] for a in (getattr(live, "attempts", None) or [])]
    answered_by = ""
    if outcome == "facts":
        answered_by = next((str(a[0]) for a in attempts if a[1] == "facts"),
                           str(getattr(live, "detail", "") or ""))
    subject = str(getattr(brief, "subject", "") or "") if brief else ""
    why = ""
    if degraded and brief is not None:
        notes = list(getattr(brief, "notes", None) or [])
        why = str(notes[0]) if notes else ""
    return {
        "at": time.time(),
        "query": (query or "")[:300],
        "subject": subject[:300],
        "intent": str(getattr(brief, "intent", "") or "") if brief else "",
        "domain": domain,
        "route": route[:160],
        "degraded": degraded,
        "degraded_why": why[:200],
        "outcome": outcome,
        "answered_by": answered_by[:80],
        "attempts": [[str(x)[:200] for x in a] for a in attempts],
        "suspect": "" if domain else suspect(query, subject),
    }


class RoutingStore:
    """`episodes`, one row per written episode."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = data_path("LIVE_ROUTING_DB", "live_routing.db", path)
        self._lock = threading.Lock()
        with closing(self._connect()) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS episodes (
                              at           REAL NOT NULL,
                              query        TEXT NOT NULL DEFAULT '',
                              subject      TEXT NOT NULL DEFAULT '',
                              intent       TEXT NOT NULL DEFAULT '',
                              domain       TEXT NOT NULL DEFAULT '',
                              route        TEXT NOT NULL DEFAULT '',
                              degraded     INTEGER NOT NULL DEFAULT 0,
                              degraded_why TEXT NOT NULL DEFAULT '',
                              outcome      TEXT NOT NULL DEFAULT '',
                              answered_by  TEXT NOT NULL DEFAULT '',
                              attempts     TEXT NOT NULL DEFAULT '[]',
                              suspect      TEXT NOT NULL DEFAULT '',
                              verdict      TEXT NOT NULL DEFAULT '')""")
            db.execute("CREATE INDEX IF NOT EXISTS episodes_at ON episodes (at)")

    def _connect(self) -> sqlite3.Connection:
        db = store_db.connect(self.path, timeout=5, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def add(self, rows: list, now: Optional[float] = None) -> None:
        if not rows:
            return
        now = time.time() if now is None else now
        with self._lock:
            db = self._connect()
            try:
                with db:
                    db.executemany(
                        "INSERT INTO episodes (at, query, subject, intent, "
                        "domain, route, degraded, degraded_why, outcome, "
                        "answered_by, attempts, suspect, verdict) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [(r["at"], r["query"], r["subject"], r["intent"],
                          r["domain"], r.get("route", ""), int(r["degraded"]),
                          r["degraded_why"],
                          r["outcome"], r["answered_by"],
                          json.dumps(r["attempts"]), r["suspect"],
                          verdict(r["domain"], r["outcome"], r["degraded"]))
                         for r in rows])
                    db.execute("DELETE FROM episodes WHERE at < ?",
                               (now - KEEP_DAYS * DAY,))
            finally:
                db.close()

    def since(self, at: float) -> list:
        with closing(self._connect()) as db:
            cursor = db.execute(
                "SELECT at, query, subject, intent, domain, route, degraded, "
                "degraded_why, outcome, answered_by, attempts, suspect, verdict "
                "FROM episodes WHERE at >= ? ORDER BY at DESC", (at,))
            names = [c[0] for c in cursor.description]
            out = []
            for values in cursor:
                row = dict(zip(names, values))
                row["degraded"] = bool(row["degraded"])
                try:
                    row["attempts"] = json.loads(row["attempts"] or "[]")
                except ValueError:
                    row["attempts"] = []
                out.append(row)
            return out

    def clear(self) -> None:
        with self._lock, closing(self._connect()) as db:
            db.execute("DELETE FROM episodes")


_STORE: Optional[RoutingStore] = None
_STORE_LOCK = threading.Lock()
_PENDING: list = []
_PENDING_LOCK = threading.Lock()
_LAST_FLUSH = [0.0]
_FLUSHING = [False]
_FLUSH_LOCK = threading.Lock()


def store() -> RoutingStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = RoutingStore()
        return _STORE


def reset(routing_store: Optional[RoutingStore] = None) -> None:
    """For tests: use this store (or open a fresh one on next use), and
    forget any rows not yet written."""
    global _STORE
    with _STORE_LOCK:
        _STORE = routing_store
    with _PENDING_LOCK:
        _PENDING.clear()
        _LAST_FLUSH[0] = 0.0


def exists() -> bool:
    """Whether there is anything to report. Reading never creates the store."""
    if _STORE is not None:
        return True
    with _PENDING_LOCK:
        if _PENDING:
            return True
    # Its file, or its schema under Postgres (`db.has_tables`).
    return store_db.has_tables(data_path("LIVE_ROUTING_DB", "live_routing.db"))


def record(query: str, brief, live) -> None:
    """Note how one written episode was routed. Never raises, never waits."""
    try:
        row = row_for(query, brief, live)
        with _PENDING_LOCK:
            _PENDING.append(row)
            del _PENDING[:-MAX_PENDING]
            due = (not _FLUSHING[0]
                   and time.monotonic() - _LAST_FLUSH[0] >= FLUSH_SECONDS)
            if due:
                _FLUSHING[0] = True
        if due:
            threading.Thread(target=_flush_in_background, daemon=True,
                             name="live-routing-flush").start()
    except Exception as exc:  # noqa: BLE001 - a record never costs an episode
        log.warning("could not record live routing: %s", exc)


def _flush_in_background() -> None:
    try:
        flush()
    finally:
        with _PENDING_LOCK:
            _FLUSHING[0] = False


def flush() -> None:
    """Write the rows held in memory. Never raises; a failed write keeps them."""
    with _FLUSH_LOCK:
        # Stamped first, so a store that cannot be opened is retried at the
        # flush interval rather than by a new thread on every `record`.
        with _PENDING_LOCK:
            _LAST_FLUSH[0] = time.monotonic()
        try:
            target = store()
        except Exception as exc:  # noqa: BLE001
            log.warning("could not open live routing: %s", exc)
            return
        with _PENDING_LOCK:
            taken = list(_PENDING)
            _PENDING.clear()
        if not taken:
            return
        try:
            target.add(taken)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not write live routing: %s", exc)
            with _PENDING_LOCK:
                _PENDING[:0] = taken
                del _PENDING[:-MAX_PENDING]


def _reason(row: dict) -> str:
    """For a routed row with no answer: the outcome and the first provider's
    sentence, which is what someone fixing it needs."""
    for attempt in row.get("attempts") or []:
        name, outcome, detail = (list(attempt) + ["", "", ""])[:3]
        if outcome != "facts":
            return f"{outcome}: {detail or name}"
    return row.get("outcome") or "not asked"


def report(now: Optional[float] = None, days: int = 7) -> dict:
    """What the admin page draws. Never raises, never creates the store."""
    now = time.time() if now is None else now
    rows: list = []
    try:
        if exists():
            flush()
            rows = store().since(now - days * DAY)
    except Exception as exc:  # noqa: BLE001 - a report is never load-bearing
        log.warning("could not read live routing: %s", exc)
    totals = {v: 0 for v in VERDICTS}
    domains: dict = {}
    by_code = 0
    reasons: dict = {}
    for row in rows:
        v = row.get("verdict") or verdict(row["domain"], row["outcome"],
                                          row["degraded"])
        row["verdict"] = v
        totals[v] = totals.get(v, 0) + 1
        if (row.get("route") or "").startswith("code"):
            by_code += 1
        if row["domain"]:
            d = domains.setdefault(row["domain"], {"routed": 0, "answered": 0})
            d["routed"] += 1
            d["answered"] += int(v == ANSWERED)
        if v == NO_ANSWER:
            why = f"{row['domain']} - {_reason(row)}"[:160]
            reasons[why] = reasons.get(why, 0) + 1
    suspects = [r for r in rows if r.get("suspect")]
    return {
        "days": days,
        "episodes": len(rows),
        "totals": totals,
        "domains": domains,
        "no_answer_reasons": [{"why": k, "count": n} for k, n in
                              sorted(reasons.items(), key=lambda kv: -kv[1])][:12],
        "suspects": len(suspects),
        "routed_by_code": by_code,
        "recent": rows[:RECENT],
        "recent_suspects": suspects[:RECENT],
    }
