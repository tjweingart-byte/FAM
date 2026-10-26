"""Recency filters, in code (§320).

The window was only a hint to the search vendor and nothing checked what came
back; the thin-packet retry searched with no window at all. A dated result
before the window is now dropped, and the retry widens the window instead.
"""
import types
from datetime import datetime, timedelta, timezone

import research


NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


def result(days_ago=None):
    date = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%d") \
        if days_ago is not None else ""
    return types.SimpleNamespace(url="https://reuters.com/x", title="t",
                                 published_date=date, highlights=["x"])


def test_a_dated_result_before_the_window_is_dropped_and_an_undated_one_kept():
    old, fresh, undated = result(400), result(1), result(None)
    kept = research.within_window([old, fresh, undated], 2, now=NOW)
    assert kept == [fresh, undated]


def test_a_day_of_grace_either_side_of_the_window():
    edge = result(3)
    assert research.within_window([edge], 2, now=NOW) == [edge]
    assert research.within_window([result(4)], 2, now=NOW) == []


def test_no_window_keeps_everything():
    old = result(4000)
    assert research.within_window([old], 0, now=NOW) == [old]


def test_the_retry_widens_the_window_and_never_drops_it():
    assert research.retry_window(0) == 0, "an evergreen question has no window"
    assert research.retry_window(1) == 7
    assert research.retry_window(2) >= 7
    assert research.retry_window(30) == 90
