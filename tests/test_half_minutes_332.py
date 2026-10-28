"""§332: the standard episode is a minute and a half.

Minutes were whole numbers everywhere - the cache key, the saved/shared/social
rows, every endpoint - so a half minute has to survive each of them, and a
whole length has to stay exactly the key it always was.
"""
from __future__ import annotations

import os
import pathlib
import sys

from pydantic import TypeAdapter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import config  # noqa: E402
from cache import cache_key, key_bucket  # noqa: E402
from config import as_minutes  # noqa: E402
from script_generator import plan_episode  # noqa: E402

INDEX = (pathlib.Path(__file__).resolve().parent.parent
         / "static" / "index.html").read_text(encoding="utf-8")


def test_the_standard_length_is_a_minute_and_a_half():
    assert config.DEFAULT_MINUTES == config.BROWSE_MINUTES == 1.5
    assert "var DEFAULT_LENGTH_MINUTES = 1.5;" in INDEX
    assert "var BROWSE_MINUTES = 1.5;" in INDEX


def test_a_length_is_held_to_the_half_minute_and_a_whole_one_stays_an_int():
    assert as_minutes(1.5) == 1.5
    assert as_minutes("1.5") == 1.5
    assert as_minutes(1.4) == 1.5
    assert as_minutes(1) == 1 and isinstance(as_minutes(1), int)
    assert as_minutes(2.0) == 2 and isinstance(as_minutes(2.0), int)
    assert as_minutes(None) == 0 and as_minutes("") == 0 and as_minutes("x") == 0
    assert as_minutes(float("nan")) == 0 and as_minutes(float("inf")) == 0


def test_a_half_minute_is_its_own_key_and_a_whole_one_is_unchanged():
    """`int(1.5)` used to file a minute-and-a-half episode under one minute's
    key. And `1.0` from a float column is the same episode as `1`."""
    assert cache_key("why tides happen", 1.5) != cache_key("why tides happen", 1)
    assert cache_key("why tides happen", 1.0) == cache_key("why tides happen", 1)
    assert key_bucket(1.5) != key_bucket(1)
    assert key_bucket(2.0) == key_bucket(2)


def test_a_plan_keeps_the_half_minute():
    plan = plan_episode("why tides happen", 1.5)
    assert plan.minutes == 1.5
    assert plan.target_seconds == 90


def test_an_endpoint_accepts_a_half_minute():
    """`Minutes` replaced `int` on the wire: 1.5 was a 422."""
    adapter = TypeAdapter(appmod.Minutes)
    assert adapter.validate_python(1.5) == 1.5
    assert adapter.validate_python("1.5") == 1.5
    whole = adapter.validate_python("2")
    assert whole == 2 and isinstance(whole, int)
    assert appmod.ScriptRequest(query="q", minutes=1.5).minutes == 1.5


def test_search_and_go_deeper_offer_the_standard_length():
    assert "var SEARCH_LENGTHS = [1, 1.5, 2, 3, 4, 5];" in INDEX
    assert 'data-min="1.5"' in INDEX and "setGoDeeperMinutes(1.5)" in INDEX
    # Rounded to the half minute, never to the minute: Math.round(1.5) is 2.
    fn = INDEX[INDEX.index("function setGoDeeperMinutes("):]
    fn = fn[:fn.index("\n  }\n")]
    assert "offeredMinutes(minutes)" in fn and "Math.round(minutes)" not in fn
