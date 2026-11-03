"""Totals about groups of listeners - groups of 100 or more, never a person (§415).

Decided item 2, consuela.md never 4, and the Privacy Policy the owner
approved on 2026-11-03 (§410): FAM keeps totals of what is searched and
heard - by kind of story (the eight facets), region and week - and how those
totals differ by age group, gender and region. Consuela's nightly task
(`housekeeper:totals`); it lives beside the accounts data, never in the
archive.

**Who is counted.** An account let into the app, with a date of birth, and a
yes to the `demographics` consent's current wording - counted by default
once the activation step showed the notice; Settings turns it off (§411,
§414). Never a guest and never a Private search: neither is ever written to
the event log (`app._remembers`), so neither can be read here.

**What is kept.** Only cells: for a week, a kind of story ("all" included)
and one breakdown - none, age group (13-17 ... 65+), gender, or region
(state or province and country; never a city) - the number of listeners, and
their searches and plays. Computed in memory from the accounts, the consent
and the event log, read in place; **no row per person is ever written**.

**Groups of 100 or more, and no subtraction.** A cell with fewer than
`MIN_GROUP` (100) listeners is never kept. And a breakdown is kept whole or
not at all: if any group in, say, one story kind's age breakdown is under
100, none of that breakdown is kept - otherwise the missing group could be
worked out by subtracting the others from the kind's total.

**Recomputed, not added to.** Each night the current week and the last are
computed again from scratch and replace what was there, so an account
deleted, or one that turned counting off, simply stops being counted;
totals of earlier weeks stay, as the policy says (they describe groups, not
anyone). Kept indefinitely, reviewed at two years.

With few listeners almost every group is under 100 and almost nothing is
kept. That is the rule working, not a failure.
"""
from __future__ import annotations

import datetime
import logging
import sqlite3
import threading
import time
from contextlib import closing
from typing import Optional

import db as store_db
from paths import data_path

log = logging.getLogger("fam.totals")

#: The owner's number (decided item 2): no total for a smaller group.
MIN_GROUP = 100
#: The owner's age bands (decided item 11).
AGE_BANDS = ((13, 17), (18, 24), (25, 34), (35, 44), (45, 54), (55, 64), (65, 200))
#: What is counted: searches and plays. A finish is not a second play.
KINDS = ("search", "play")
BREAKDOWNS = ("none", "age", "gender", "region")
NOT_GIVEN = "not given"


def band_of(birth_date: str, on: datetime.date) -> str:
    import accounts as accounts_mod

    age = accounts_mod.age_on(birth_date, on)
    for low, high in AGE_BANDS:
        if age is not None and low <= age <= high:
            return f"{low}+" if high >= 200 else f"{low}-{high}"
    return ""      # under 13: never counted (and never an account, §411)


def region_of(location) -> str:
    region = str(getattr(location, "region", "") or "").strip()
    country = str(getattr(location, "country", "") or "").strip()
    if not region:
        return NOT_GIVEN
    return f"{region}, {country}" if country else region


def week_start(day: datetime.date) -> datetime.date:
    return day - datetime.timedelta(days=day.weekday())


# ------------------------------------------------------------------ the store
class TotalsStore:
    """`totals`: one row per kept cell. Nothing about anyone."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = data_path("DEMOGRAPHICS_DB", "demographics.db", path)
        self._lock = threading.Lock()
        with closing(self._connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS totals ("
                       " week TEXT NOT NULL, story TEXT NOT NULL,"
                       " breakdown TEXT NOT NULL, grp TEXT NOT NULL,"
                       " listeners INTEGER NOT NULL, searches INTEGER NOT NULL,"
                       " plays INTEGER NOT NULL, computed_at REAL NOT NULL,"
                       " PRIMARY KEY (week, story, breakdown, grp))")

    def _connect(self) -> sqlite3.Connection:
        return store_db.connect(self.path, timeout=10)

    def replace_week(self, week: str, cells: list, now: float) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute("DELETE FROM totals WHERE week = ?", (week,))
            db.executemany(
                "INSERT INTO totals (week, story, breakdown, grp, listeners, searches,"
                " plays, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(week, c["story"], c["breakdown"], c["group"], c["listeners"],
                  c["searches"], c["plays"], now) for c in cells])

    def week(self, week: str) -> list:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT story, breakdown, grp, listeners, searches, plays"
                              " FROM totals WHERE week = ? ORDER BY story, breakdown, grp",
                              (week,)).fetchall()
        return [{"story": r[0], "breakdown": r[1], "group": r[2], "listeners": r[3],
                 "searches": r[4], "plays": r[5]} for r in rows]

    def weeks(self) -> list:
        with closing(self._connect()) as db:
            return [r[0] for r in db.execute(
                "SELECT DISTINCT week FROM totals ORDER BY week DESC").fetchall()]

    def forget(self) -> int:
        """A wipe ([wipe-derived]): the totals came from the event log."""
        with self._lock, closing(self._connect()) as db, db:
            return db.execute("DELETE FROM totals").rowcount or 0


_STORE: list = [None]
_STORE_LOCK = threading.Lock()
#: The accounts, consent, preferences and event stores, handed in at
#: startup (`register`), read in place.
SOURCES: dict = {}


def store() -> TotalsStore:
    with _STORE_LOCK:
        if _STORE[0] is None:
            _STORE[0] = TotalsStore()
        return _STORE[0]


def reset(totals_store: Optional[TotalsStore] = None) -> None:
    with _STORE_LOCK:
        _STORE[0] = totals_store


def exists() -> bool:
    return store_db.has_tables(data_path("DEMOGRAPHICS_DB", "demographics.db"))


# ------------------------------------------------------------------ the count
def compute(week: datetime.date, accounts, consent, prefs, events,
            min_group: int = MIN_GROUP) -> dict:
    """The kept cells for the week starting `week`, and how many were
    withheld. Pure apart from reading the stores; writes nothing."""
    import consent as consent_mod

    start = datetime.datetime.combine(week, datetime.time(), datetime.timezone.utc)
    end = start + datetime.timedelta(days=7)
    counted = consent.allowed_users(consent_mod.DEMOGRAPHICS)
    people = {}
    for user_id, birth_date, gender in accounts.demographic_rows():
        if user_id not in counted:
            continue
        band = band_of(birth_date, (end - datetime.timedelta(days=1)).date())
        if not band:
            continue
        people[user_id] = {"age": band, "gender": gender or NOT_GIVEN,
                           "region": region_of(prefs.get(user_id).location)}
    import topics

    # (story, breakdown, group) -> {listeners: set, searches, plays}; in memory only.
    cells: dict = {}
    for user_id, kind, tags, _at in events.between(start.timestamp(), end.timestamp(),
                                                   KINDS):
        who = people.get(user_id)
        if who is None:
            continue
        stories = {t for t in tags if t in topics.FACETS} or {"other"}
        for story in stories | {"all"}:
            for breakdown in BREAKDOWNS:
                group = "" if breakdown == "none" else who[breakdown]
                cell = cells.setdefault((story, breakdown, group),
                                        {"listeners": set(), "searches": 0, "plays": 0})
                cell["listeners"].add(user_id)
                cell["searches" if kind == "search" else "plays"] += 1
    kept, withheld = [], 0
    # A breakdown is kept whole or not at all, so no group under 100 can be
    # worked out by subtraction.
    by_breakdown: dict = {}
    for (story, breakdown, group), cell in cells.items():
        by_breakdown.setdefault((story, breakdown), []).append((group, cell))
    for (story, breakdown), groups in sorted(by_breakdown.items()):
        if any(len(c["listeners"]) < min_group for _g, c in groups):
            withheld += len(groups)
            continue
        kept += [{"story": story, "breakdown": breakdown, "group": group,
                  "listeners": len(c["listeners"]), "searches": c["searches"],
                  "plays": c["plays"]} for group, c in sorted(groups)]
    return {"week": week.isoformat(), "people": len(people), "cells": kept,
            "withheld": withheld}


def run(now: Optional[float] = None, sources: Optional[dict] = None) -> dict:
    """This week's and last week's totals, computed again and kept. Blocking;
    never raises."""
    sources = sources or SOURCES
    now = now or time.time()
    out = {"weeks": []}
    try:
        need = ("accounts", "consent", "prefs", "events")
        if any(sources.get(k) is None for k in need):
            return {**out, "why": "the stores were not handed in"}
        today = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).date()
        for week in (week_start(today) - datetime.timedelta(days=7), week_start(today)):
            result = compute(week, sources["accounts"], sources["consent"],
                             sources["prefs"], sources["events"])
            store().replace_week(result["week"], result["cells"], now)
            out["weeks"].append({k: result[k] for k in ("week", "people", "withheld")}
                                | {"kept": len(result["cells"])})
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("the totals could not be computed")
        return {**out, "why": f"{type(exc).__name__}: {exc}"}


# ------------------------------------------------------------------ Netenyahu's task
async def handler(_task: str) -> None:
    import asyncio

    import history_archive as ha

    if not ha.consuela_runs():
        return
    summary = await asyncio.to_thread(run)
    log.info("the totals: %s", summary)


def register(accounts, consent, prefs, events) -> None:
    """At startup, with `AGENTS=1`: the stores, read in place, and the task."""
    from agents import netenyahu

    SOURCES.update(accounts=accounts, consent=consent, prefs=prefs, events=events)
    netenyahu.HANDLERS[("housekeeper", "totals")] = handler


def report() -> dict:
    out = {"min_group": MIN_GROUP}
    try:
        if exists():
            weeks = store().weeks()
            out["weeks"] = len(weeks)
            if weeks:
                out["latest"] = weeks[0]
                out["latest_cells"] = len(store().week(weeks[0]))
    except Exception:  # noqa: BLE001
        pass
    return out
