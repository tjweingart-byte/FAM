"""Totals about groups of listeners (§415): groups of 100 or more, never a person.

The rules pinned: who is counted (an account let in, a date of birth, a yes
to counting - never a guest, never a Private search); only cells; no cell
under 100; a breakdown kept whole or not at all, so no small group can be
worked out by subtraction; recomputed, so a deleted account or a no stops
counting; registered with the wipe, the schedule and the data disk.
"""
from __future__ import annotations

import dataclasses
import datetime
import os
import sqlite3
import sys
from contextlib import closing
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import demographic_totals as dt  # noqa: E402

WEEK = datetime.date(2026, 10, 26)          # a Monday
AT = datetime.datetime(2026, 10, 28, 12, tzinfo=datetime.timezone.utc).timestamp()


class _World:
    """Stand-ins for the four stores the totals read."""

    def __init__(self):
        self.people, self.counted, self.places, self.log = [], set(), {}, []

    def add(self, n, birth="1990-01-01", gender="man", region="Pennsylvania",
            counted=True, story="sports", kind="play", prefix="u"):
        for i in range(n):
            uid = f"{prefix}{len(self.people)}"
            self.people.append((uid, birth, gender))
            if counted:
                self.counted.add(uid)
            self.places[uid] = SimpleNamespace(region=region, country="US")
            self.log.append((uid, kind, (story,), AT))

    # the store interfaces
    def demographic_rows(self):
        return list(self.people)

    def allowed_users(self, scope):
        return set(self.counted)

    def get(self, uid):
        return SimpleNamespace(location=self.places.get(uid, SimpleNamespace(region="", country="")))

    def between(self, start, end, kinds):
        return [e for e in self.log if start <= e[3] < end and e[1] in kinds]


def _compute(world, min_group=dt.MIN_GROUP):
    return dt.compute(WEEK, world, world, world, world, min_group=min_group)


def _cells(result):
    return {(c["story"], c["breakdown"], c["group"]): c for c in result["cells"]}


# --- the pieces ---------------------------------------------------------------

def test_the_owner_s_age_bands():
    on = datetime.date(2026, 11, 1)
    assert dt.band_of("2010-01-01", on) == "13-17"
    assert dt.band_of("2008-01-01", on) == "18-24"
    assert dt.band_of("1990-01-01", on) == "35-44"
    assert dt.band_of("1950-01-01", on) == "65+"
    assert dt.band_of("2015-01-01", on) == ""          # under 13: never counted


def test_a_region_never_a_city():
    assert dt.region_of(SimpleNamespace(city="Exton", region="Pennsylvania",
                                        country="US")) == "Pennsylvania, US"
    assert dt.region_of(SimpleNamespace(city="Exton", region="", country="US")) == "not given"


# --- the count -------------------------------------------------------------------

def test_a_group_of_100_is_kept_with_its_breakdowns():
    world = _World()
    world.add(120)
    cells = _cells(_compute(world))
    assert cells[("sports", "none", "")]["listeners"] == 120
    assert cells[("sports", "age", "35-44")]["plays"] == 120
    assert cells[("sports", "gender", "man")]["listeners"] == 120
    assert cells[("sports", "region", "Pennsylvania, US")]["listeners"] == 120
    assert cells[("all", "none", "")]["listeners"] == 120


def test_no_group_under_100_and_never_by_subtraction():
    world = _World()
    world.add(120)                                   # 35-44
    world.add(20, birth="2004-01-01")                # 18-24: too few
    result = _compute(world)
    cells = _cells(result)
    # The story's total is kept (140) ...
    assert cells[("sports", "none", "")]["listeners"] == 140
    # ... but none of its age breakdown: keeping 35-44's 120 would give away
    # the 20 by subtraction.
    assert not [k for k in cells if k[:2] == ("sports", "age")]
    assert result["withheld"] > 0
    # The gender and region breakdowns, every group 100+, stay.
    assert cells[("sports", "gender", "man")]["listeners"] == 140


def test_only_counted_accounts_and_no_one_under_13():
    world = _World()
    world.add(100)
    world.add(50, counted=False, prefix="no")        # turned counting off
    world.add(30, birth="2016-01-01", prefix="kid")  # never counted
    cells = _cells(_compute(world))
    assert cells[("sports", "none", "")]["listeners"] == 100


def test_a_search_and_a_play_are_counted_apart():
    world = _World()
    world.add(100, kind="search")
    for uid, *_rest in world.people:
        world.log.append((uid, "play", ("sports",), AT))
    cell = _cells(_compute(world))[("sports", "none", "")]
    assert (cell["listeners"], cell["searches"], cell["plays"]) == (100, 100, 100)


def test_an_event_outside_the_week_is_not_counted():
    world = _World()
    world.add(100)
    world.log = [(u, k, t, at + 8 * 86400) for u, k, t, at in world.log]
    assert _compute(world)["cells"] == []


# --- what is kept ---------------------------------------------------------------------

def test_only_cells_are_ever_written_never_a_person(tmp_path):
    world = _World()
    world.add(120)
    store = dt.TotalsStore(str(tmp_path / "t.db"))
    result = _compute(world)
    store.replace_week(result["week"], result["cells"], AT)
    with closing(sqlite3.connect(store.path)) as db:
        columns = [r[1] for r in db.execute("PRAGMA table_info(totals)")]
        rows = db.execute("SELECT * FROM totals").fetchall()
    assert columns == ["week", "story", "breakdown", "grp", "listeners", "searches",
                       "plays", "computed_at"]
    assert not any(str(v).startswith("u") and str(v)[1:].isdigit()
                   for row in rows for v in row)


def test_recomputed_so_a_deleted_or_unwilling_account_stops_counting(tmp_path):
    world = _World()
    world.add(100)
    dt.reset(dt.TotalsStore(str(tmp_path / "t.db")))
    try:
        sources = {"accounts": world, "consent": world, "prefs": world, "events": world}
        now = AT + 86400
        dt.run(now=now, sources=sources)
        assert dt.store().week(WEEK.isoformat())
        world.counted.discard("u0")                  # a no, or a deleted account
        dt.run(now=now, sources=sources)
        assert dt.store().week(WEEK.isoformat()) == []   # 99: under 100
    finally:
        dt.reset()


def test_the_wipe_forgets_them(tmp_path, monkeypatch):
    import demo_data

    world = _World()
    world.add(100)
    dt.reset(dt.TotalsStore(str(tmp_path / "t.db")))
    monkeypatch.setattr(dt, "exists", lambda: True)
    try:
        result = _compute(world)
        dt.store().replace_week(result["week"], result["cells"], AT)
        out = demo_data._forget_what_the_log_taught()
        assert out["totals_dropped"] > 0 and dt.store().weeks() == []
    finally:
        dt.reset()


# --- the real stores' reads --------------------------------------------------------------

def test_the_real_stores_give_only_what_the_rules_allow(tmp_path):
    import accounts
    import consent
    import topics

    acc = accounts.AccountStore(str(tmp_path / "a.db"))
    acc.sign_up("in", "in@fam.test", "correct horse battery")
    acc.update_profile("in", birth_date="1990-01-01", gender="woman")
    acc.sign_up("nodate", "nd@fam.test", "correct horse battery")
    acc.new_account_status = "waitlisted"
    acc.sign_up("waiting", "w@fam.test", "correct horse battery")
    acc.update_profile("waiting", birth_date="1990-01-01")
    assert acc.demographic_rows() == [("in", "1990-01-01", "woman")]
    con = consent.ConsentStore(str(tmp_path / "c.db"))
    con.record("in", True, scope=consent.DEMOGRAPHICS, version=1)
    con.record("nodate", False, scope=consent.DEMOGRAPHICS, version=1)
    assert con.allowed_users(consent.DEMOGRAPHICS) == {"in"}
    events = topics.EventStore(str(tmp_path / "e.db"))
    events.record(topics.Event("in", "play", "t", "", ("sports",), at=AT))
    events.record(topics.Event("in", "skip", "t", "", ("sports",), at=AT))
    assert events.between(AT - 1, AT + 1, ("search", "play")) == [
        ("in", "play", ("sports",), AT)]


# --- registered where everything else is ----------------------------------------------

def test_it_is_consuela_s_nightly_task_and_shown_to_admins_only(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import app as appmod
    from agents import netenyahu

    assert ("housekeeper", "totals", 0, "housekeeper") in netenyahu.SCHEDULE
    try:
        dt.register("a", "c", "p", "e")
        assert netenyahu.HANDLERS[("housekeeper", "totals")] is dt.handler
    finally:
        netenyahu.HANDLERS.pop(("housekeeper", "totals"), None)
        dt.SOURCES.clear()
    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "admin-secret")
    assert TestClient(appmod.app).get("/api/admin/totals").status_code == 404
    body = TestClient(appmod.app, headers={"X-Admin-Token": "admin-secret"}).get(
        "/api/admin/totals").json()
    assert body["min_group"] == 100
