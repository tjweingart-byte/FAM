"""The myFAM row that says what the world is paying attention to.

Two rows, two questions, and keeping them apart is the point:

* **"What FAM can't stop playing"** - this app's own play counts over its own
  bank. Cheap, identical for everyone, and the row that already existed.
* **"Trending"** - what the world is talking about, from an outside feed.

And a third thing this is *not*: `live_facts`. That resolves one entity and
fetches its state, in seconds, with a status vocabulary. This has no entity, no
status, and minutes of freshness. One changes what an episode says; the other
changes what is offered.

The rule every empty-state test below is a form of, and the same one
PROBLEMS.md §89 settles for live facts: **an empty row is a fact about this
deployment, never a claim about the world.**
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import topics as T  # noqa: E402
import trending  # noqa: E402


@pytest.fixture(autouse=True)
def clean():
    """The feed cache and registry are global - that is the design - so a leak
    between tests would make one test's fetch satisfy another's assertion."""
    original = list(trending._SOURCES)
    trending.reset()
    yield
    trending._SOURCES[:] = original
    trending.reset()


class Boom(trending.TrendingSource):
    name = "exploding feed"

    def diagnose(self):
        return True, "ready"

    async def fetch(self, limit):
        raise RuntimeError("upstream 500")


class Slow(trending.TrendingSource):
    name = "slow feed"

    def diagnose(self):
        return True, "ready"

    async def fetch(self, limit):
        await asyncio.sleep(5)
        return []


class Empty(trending.TrendingSource):
    name = "quiet feed"

    def diagnose(self):
        return True, "ready"

    async def fetch(self, limit):
        return []


# --------------------------------------------------------------------------
# the two rows are two rows
# --------------------------------------------------------------------------
def test_myfam_has_both_a_world_row_and_a_fam_popularity_row():
    keys = [k for k, _ in T.SECTIONS]
    titles = dict(T.SECTIONS)
    assert "world_trending" in keys and "most_played" in keys
    assert titles["world_trending"] == "Trending"
    assert titles["most_played"] == "What FAM can't stop playing"


def test_the_fam_popularity_row_still_ranks_plays_over_the_bank():
    """Renaming it must not have changed what it does."""
    store = T.EventStore(":memory:")
    for listener in ("u1", "u2", "u3"):
        store.record(T.Event(listener, "play", T.TOPIC_BANK[2].id, "",
                             T.TOPIC_BANK[2].tags))
    ranked = T.rank_most_played(store)
    assert ranked[0].id == T.TOPIC_BANK[2].id
    assert all(t in T.TOPIC_BANK for t in ranked), "this row comes from the bank"


def test_the_world_row_does_not_come_from_the_bank():
    """It is the one row whose inventory is not FAM's own, which is why it
    takes no part in the fill loop's mutual exclusion."""
    assert "world_trending" not in T.FILL_ORDER
    trending.register(trending.FakeTrendingSource())
    asyncio.run(trending.refresh())
    tiles = T.topics_from_trending(trending.cached().items)
    assert tiles
    assert not any(t.id in {b.id for b in T.TOPIC_BANK} for t in tiles)


def test_the_world_row_never_steals_tiles_from_the_personal_rows():
    """The personal sections are the point of the page; a new row that could
    starve them would be a regression however good it is."""
    store = T.EventStore(":memory:")
    trending.register(trending.FakeTrendingSource())
    asyncio.run(trending.refresh())
    feed = T.build_feed(store, "u1")
    by_key = {s["key"]: s for s in feed["sections"]}
    world_ids = {t["id"] for t in by_key["world_trending"]["topics"]}
    for key in T.FILL_ORDER:
        assert not world_ids & {t["id"] for t in by_key[key]["topics"]}


# --------------------------------------------------------------------------
# an empty row is never a claim about the world
# --------------------------------------------------------------------------
def test_with_no_source_the_row_is_empty_and_says_why():
    store = T.EventStore(":memory:")
    feed = T.build_feed(store, "u1")
    row = [s for s in feed["sections"] if s["key"] == "world_trending"][0]
    assert row["topics"] == []
    assert row["empty_reason"]
    assert "isn't connected" in row["empty_reason"]


@pytest.mark.parametrize("outcome, must_not_say", [
    (trending.NOT_CONFIGURED, "nothing is trending"),
    (trending.SOURCE_FAILED, "nothing is trending"),
    (trending.TIMEOUT, "nothing is trending"),
    (trending.EMPTY, "nothing is trending"),
])
def test_no_empty_state_claims_the_world_is_quiet(outcome, must_not_say):
    reason = trending.TrendingFeed(outcome).empty_reason.lower()
    assert reason
    assert must_not_say not in reason
    assert "nothing is happening" not in reason


def test_every_way_of_coming_up_empty_is_a_different_sentence():
    """"We have no feed", "the feed broke" and "the feed had nothing" are three
    different things to say and three different things to fix."""
    seen = {trending.TrendingFeed(o).empty_reason
            for o in trending.OUTCOMES if o != trending.ITEMS}
    assert len(seen) == len(trending.OUTCOMES) - 1


# --------------------------------------------------------------------------
# it cannot break the page
# --------------------------------------------------------------------------
def test_a_source_that_fails_leaves_the_rest_of_myfam_intact():
    trending.register(Boom())
    feed = asyncio.run(trending.refresh())
    assert feed.outcome == trending.SOURCE_FAILED
    assert "upstream 500" in feed.detail

    page = T.build_feed(T.EventStore(":memory:"), "u1")
    by_key = {s["key"]: s for s in page["sections"]}
    assert by_key["most_played"]["topics"], "one bad feed emptied the whole page"
    assert by_key["world_trending"]["topics"] == []


def test_a_source_that_hangs_is_bounded(monkeypatch):
    monkeypatch.setattr(trending, "settings", dataclasses.replace(
        config.settings, trending_timeout_seconds=0.05))
    trending.register(Slow())
    assert asyncio.run(trending.refresh()).outcome == trending.TIMEOUT


def test_a_source_with_nothing_to_say_is_not_a_failure():
    trending.register(Empty())
    assert asyncio.run(trending.refresh()).outcome == trending.EMPTY


def test_the_switch_turns_it_off_completely(monkeypatch):
    monkeypatch.setattr(trending, "settings", dataclasses.replace(
        config.settings, trending=False))
    trending.register(trending.FakeTrendingSource())
    assert asyncio.run(trending.refresh()).outcome == trending.NOT_CONFIGURED


# --------------------------------------------------------------------------
# the cost design: one fetch, every listener
# --------------------------------------------------------------------------
def test_one_refresh_serves_every_listener():
    """This row's whole economics, and why it is the cheapest place in FAM to
    add live data. A per-listener fetch would make it the most expensive."""
    source = trending.FakeTrendingSource()
    calls = {"n": 0}
    original = source.fetch

    async def counted(limit):
        calls["n"] += 1
        return await original(limit)

    source.fetch = counted
    trending.register(source)
    asyncio.run(trending.refresh())

    store = T.EventStore(":memory:")
    for listener in ("u1", "u2", "u3"):
        page = T.build_feed(store, listener)
        row = [s for s in page["sections"] if s["key"] == "world_trending"][0]
        assert row["topics"], f"{listener} got an empty row"
    assert calls["n"] == 1, "the feed was fetched per listener"


def test_the_feed_read_needs_no_network_and_no_await():
    """`build_feed` stays a pure function of the log plus the cache. A ranker
    that fetched would be a ranker no test could call offline."""
    import inspect
    import re

    source = inspect.getsource(T.build_feed)
    code = "\n".join(re.sub(r"#.*", "", line) for line in source.split("\n"))
    assert not inspect.iscoroutinefunction(T.build_feed)
    assert "await" not in code
    assert "trending.refresh" not in code, "the ranker is fetching"
    assert "trending.cached()" in code


def test_a_subject_keeps_its_id_when_the_question_is_rephrased():
    """The id is what impressions, fatigue and the already-seen set key on. If
    it churned every refresh, the same tile would be shown to one listener
    forever and fatigue could never damp it."""
    first = trending.TrendingItem(subject="undersea cables",
                                  query="why cutting one cable slows a country")
    second = trending.TrendingItem(subject="Undersea  Cables",
                                   query="a completely different question")
    assert first.id == second.id


# --------------------------------------------------------------------------
# what a tile is, and is not
# --------------------------------------------------------------------------
def test_a_tile_carries_a_question_rather_than_a_headline():
    """A tile whose query is a headline produces an episode that restates the
    headline. The contract is a question worth an episode."""
    trending.register(trending.FakeTrendingSource())
    asyncio.run(trending.refresh())
    for item in trending.cached().items:
        assert len(item.query.split()) >= 5, f"{item.query!r} reads as a headline"


def test_the_why_now_line_is_a_subtitle_and_never_evidence():
    """Tapping a tile runs the ordinary pipeline, which researches the question
    from scratch. That is what stops a stale blurb becoming a stale episode -
    so the blurb must reach the tile and nothing else."""
    import inspect

    import script_generator as sg

    assert "trending" not in inspect.getsource(sg.build_prompt)

    trending.register(trending.FakeTrendingSource())
    asyncio.run(trending.refresh())
    tiles = T.topics_from_trending(trending.cached().items)
    assert tiles[0].subtitle == trending.cached().items[0].why_now


def test_a_prediction_market_signal_is_labelled_as_one():
    """A market price is what people are betting, not what is true. The kind
    travels with the item so a tile built from odds can never be presented as
    a report."""
    item = trending.TrendingItem(
        subject="a contested election", query="why this race is close",
        kind=trending.PREDICTION_MARKET)
    assert item.kind == "prediction-market"
    assert trending.ATTENTION != trending.PREDICTION_MARKET


def test_health_names_the_gap_rather_than_hiding_it():
    report = trending.report()
    assert report["ready"] == []
    assert "not a statement about the world" in report["detail"]
    assert any("Needs:" in s["detail"] for s in report["sources"])


def test_an_unknown_source_name_is_reported_and_does_not_stop_boot(monkeypatch):
    monkeypatch.setattr(trending, "settings", dataclasses.replace(
        config.settings, trending_source="nope"))
    result = trending.install()
    assert result["installed"] == []
    assert result["problems"] and "nope" in result["problems"][0]
