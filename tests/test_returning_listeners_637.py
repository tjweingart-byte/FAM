"""Listeners who came back, on /admin (§637).

The owner's plan is stated in "consistent weekly listeners" and nothing
counted them. `heard_listens` keeps only the latest time a question was
heard, so a listener who hears the same mix every morning left no trace of
last week; `heard_days` keeps one row a listener a day, and /admin counts
who listened in the last seven days and also in the seven before.
"""
from __future__ import annotations

import pathlib

import pytest

import admin_tracker
import topics

ROOT = pathlib.Path(__file__).resolve().parent.parent
DAY = 86400
NOW = 20_000 * DAY + 12 * 3600          # midday on some UTC day


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MYFAM_DB", str(tmp_path / "myfam.db"))
    events = topics.EventStore(str(tmp_path / "myfam.db"))
    yield events


def _count(store):
    stores = {s.alias: s for s in admin_tracker.discover_stores()}
    assert pathlib.Path(stores["myfam"].path) == pathlib.Path(store.path)
    return admin_tracker.returning_listeners(stores, NOW)


def test_a_listener_is_returning_only_with_a_day_in_both_weeks(store):
    store.mark_heard("both", "what happened in the nfl", NOW - 9 * DAY)
    store.mark_heard("both", "what happened in the nba", NOW - 1 * DAY)
    store.mark_heard("new", "what happened in the nfl", NOW - 2 * DAY)
    store.mark_heard("gone", "what happened in the nfl", NOW - 10 * DAY)
    r = _count(store)
    assert (r["this_week"], r["last_week"], r["returning"]) == (2, 2, 1)
    assert r["share"] == 0.5                       # of last week's two, one is back
    assert r["weeks"][0] == {"ends": r["weeks"][0]["ends"], "listeners": 2,
                             "returning": 1, "share": 0.5}
    assert len(r["weeks"]) == admin_tracker.RETURNING_WEEKS


def test_hearing_the_same_question_again_does_not_erase_last_week(store):
    # The reason for the table: one row a question in `heard_listens`, so the
    # same morning mix heard again overwrote the week before.
    for days_ago in (9, 8, 2, 1, 0):
        store.mark_heard("daily", "what is new with the eagles", NOW - days_ago * DAY)
    rows = store._conn().execute(
        "SELECT COUNT(*) FROM heard_listens WHERE user_id = 'daily'").fetchone()[0]
    assert rows == 1
    assert _count(store)["returning"] == 1


def test_a_week_is_the_seven_days_ending_today(store):
    store.mark_heard("edge", "q one", NOW - 6 * DAY)      # still this week
    store.mark_heard("edge", "q two", NOW - 7 * DAY)      # the first day of last week
    store.mark_heard("old", "q one", NOW - 13 * DAY)      # the last day of last week
    store.mark_heard("old", "q two", NOW - 14 * DAY)      # the week before that
    r = _count(store)
    assert (r["this_week"], r["last_week"], r["returning"]) == (1, 2, 1)
    assert r["weeks"][1]["returning"] == 1                # "old" was there the week before


def test_nothing_counted_is_not_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("MYFAM_DB", str(tmp_path / "absent.db"))
    stores = {s.alias: s for s in admin_tracker.discover_stores()}
    r = admin_tracker.returning_listeners(stores, NOW)
    assert r["returning"] is None and r["error"]


def test_no_listeners_is_a_counted_zero(store):
    r = _count(store)
    assert (r["this_week"], r["last_week"], r["returning"], r["share"]) == (0, 0, 0, None)


def test_the_days_are_filled_once_from_what_was_already_heard(tmp_path, monkeypatch):
    path = str(tmp_path / "myfam.db")
    monkeypatch.setenv("MYFAM_DB", path)
    first = topics.EventStore(path)
    first.mark_heard("early", "a question", NOW - 3 * DAY)
    first._conn().execute("DELETE FROM heard_days")       # as a store from before §637
    again = topics.EventStore(path)
    days = again._conn().execute("SELECT user_id, day FROM heard_days").fetchall()
    assert [tuple(d) for d in days] == [("early", topics.heard_day(NOW - 3 * DAY))]


def test_a_wipe_and_a_deleted_account_take_their_days(store):
    store.mark_heard("a", "q", NOW)
    store.mark_heard("b", "q", NOW)
    store.forget("a")
    left = [r[0] for r in store._conn().execute("SELECT user_id FROM heard_days")]
    assert left == ["b"]
    store.clear()
    assert store._conn().execute("SELECT COUNT(*) FROM heard_days").fetchone()[0] == 0


def test_the_page_draws_it_and_says_what_counts():
    page = (ROOT / "admin_ui" / "tracker.html").read_text(encoding="utf-8")
    assert 'id="returning"' in page and "drawReturning(s.returning)" in page
    assert "30 seconds, or 30% of a short one" in page
    assert topics.HEARD_SECONDS == 30 and topics.HEARD_SHARE == 0.3
