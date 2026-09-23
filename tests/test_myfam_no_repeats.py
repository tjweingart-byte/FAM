"""myFAM's order of operations: taste, then cached first, then no repeats.

The owner's path for choosing a listener's tiles:

1. Decide what they are into from what is known about them (`taste`).
2. Prefer tiles whose episode is already written - instant, already paid for.
3. Cross-check against everything they have heard. A heard tile is either
   remade with new information or replaced by the next-best topic.

Step 3 is the one with teeth, and it is tested through `build_feed` and
`build_section` rather than through the helper, because a rule one surface
reads and the other does not is §119 again.
"""
from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cache  # noqa: E402
import topics as T  # noqa: E402

DAY = 86400.0
INTERESTS = tuple(T.TAG_LABELS)


@pytest.fixture
def store(tmp_path):
    return T.EventStore(str(tmp_path / "events.db"))


def on_page(feed) -> set:
    return {t["id"] for s in feed["sections"] for t in s["topics"]}


def rail(feed, key) -> list:
    return next(s for s in feed["sections"] if s["key"] == key)["topics"]


def a_bank_tile_on(store, user) -> T.Topic:
    feed = T.build_feed(store, user, interests=INTERESTS, floors={})
    first = rail(feed, "from_history")[0]["id"]
    return T.BANK_BY_ID[first]


# --- 3. no repeats ---------------------------------------------------------

def test_a_played_tile_leaves_every_rail(store):
    tile = a_bank_tile_on(store, "u1")
    store.record(T.Event("u1", "play", tile.id, tile.query, tile.tags,
                         time.time() - 60))
    feed = T.build_feed(store, "u1", interests=INTERESTS)
    assert tile.id not in on_page(feed)
    # ... and the next-best topic took its place rather than the rail
    # shrinking.
    assert len(rail(feed, "from_history")) == T.SECTION_SIZE


def test_hearing_the_question_anywhere_counts(store):
    """Typed into search, or replayed on Explore: no tile id, same episode."""
    tile = a_bank_tile_on(store, "u2")
    store.record(T.Event("u2", "play", "", "  " + tile.query.upper() + "?",
                         (), time.time() - 60))
    assert tile.id not in on_page(T.build_feed(store, "u2", interests=INTERESTS))


def test_a_play_older_than_the_taste_window_is_still_heard(store):
    """`for_user` stops at 400 events; what somebody has heard does not."""
    tile = a_bank_tile_on(store, "u3")
    now = time.time()
    store.record(T.Event("u3", "play", tile.id, tile.query, tile.tags,
                         now - 20 * DAY))
    for i in range(450):
        store.record(T.Event("u3", "search", "", f"question {i}", (),
                             now - 10 * DAY + i))
    assert tile.id not in {e.topic_id for e in store.for_user("u3")}
    assert tile.id not in on_page(T.build_feed(store, "u3", interests=INTERESTS))


def test_view_more_applies_the_same_check(store):
    tile = a_bank_tile_on(store, "u4")
    store.record(T.Event("u4", "play", "", tile.query, (), time.time() - 60))
    section = T.build_section(store, "u4", "from_history", interests=INTERESTS)
    assert tile.id not in {t["id"] for t in section["topics"]}


# --- remade with new information --------------------------------------------

def heard(topic, ago):
    now = 1_000_000_000.0
    return T.heard_from([(topic.id, topic.query, now - ago)]), now


STARTUP = T.STARTUP_TOPICS[0]
BANK = T.TOPIC_BANK[0]


def test_an_evergreen_episode_is_never_remade():
    h, now = heard(BANK, 60 * DAY)
    assert T.is_repeat(BANK, h, now, written_at=lambda q: None)
    assert T.is_repeat(BANK, h, now, written_at=lambda q: now)


def test_a_moving_answer_comes_back_only_as_a_different_episode():
    h, now = heard(STARTUP, 3 * DAY)
    at = now - 3 * DAY
    # No live script: the tap researches and writes a new one.
    assert not T.is_repeat(STARTUP, h, now, written_at=lambda q: None)
    # Rewritten since they heard it, by somebody else's tap: new and cached.
    assert not T.is_repeat(STARTUP, h, now, written_at=lambda q: at + DAY)
    # The script they heard, still live: a repeat.
    assert T.is_repeat(STARTUP, h, now, written_at=lambda q: at - 60)
    # Written by their own tap, landing just after the play was recorded.
    assert T.is_repeat(STARTUP, h, now, written_at=lambda q: at + 90)


def test_a_moving_answer_is_not_remade_the_same_day():
    h, now = heard(STARTUP, 2 * 3600)
    assert T.is_repeat(STARTUP, h, now, written_at=lambda q: None)


def test_an_unknown_is_never_read_as_fresh():
    h, now = heard(STARTUP, 3 * DAY)
    assert T.is_repeat(STARTUP, h, now, written_at=None)

    def broken(_q):
        raise RuntimeError("database is locked")
    assert T.is_repeat(STARTUP, h, now, written_at=broken)


def test_repeats_lets_a_remade_tile_back_and_only_that_one():
    now = time.time()
    h = T.heard_from([(STARTUP.id, STARTUP.query, now - 3 * DAY)])
    blocked = T.repeats([STARTUP, BANK], h, now, written_at=lambda q: None)
    assert STARTUP.id not in blocked
    blocked = T.repeats([STARTUP, BANK], h, now, written_at=None)
    assert STARTUP.id in blocked


# --- 2. cached first ---------------------------------------------------------

def test_a_written_tile_leads_the_rail_it_qualified_for(store):
    ranked = T.rank_from_history(T.taste([], time.time(), INTERESTS), set(),
                                 limit=T.READY_REACH)
    assert len(ranked) > T.SECTION_SIZE * 2, "needs a ranking deeper than a rail"
    plain = [t["id"] for t in rail(
        T.build_feed(store, "u5", interests=INTERESTS, floors={}),
        "from_history")]
    deep = next(t for t in ranked[T.SECTION_SIZE * 2:] if t.id not in plain)
    feed = T.build_feed(store, "u5", interests=INTERESTS, floors={},
                        written=lambda q: q == deep.query)
    assert rail(feed, "from_history")[0]["id"] == deep.id


def test_cached_first_never_filters():
    tiles = list(T.TOPIC_BANK[:5])
    ready = {tiles[3].query}
    out = T.ready_first(tiles, written=lambda q: q in ready)
    assert [t.id for t in out] == [tiles[3].id] + [t.id for t in tiles
                                                   if t is not tiles[3]]
    assert T.ready_first(tiles, written=None) == tiles


# --- the cache half -----------------------------------------------------------

@pytest.mark.parametrize("make", [
    lambda tmp: cache.MemoryScriptCache(),
    lambda tmp: cache.SqliteScriptCache(str(tmp / "scripts.db")),
])
def test_the_cache_says_when_a_script_was_written(tmp_path, make):
    c = make(tmp_path)
    assert c.written_at("k") is None
    before = time.time()
    c.put("k", ["One sentence."], 3600, "a question", minutes=2)
    assert before - 1 <= c.written_at("k") <= time.time() + 1
    c.put("gone", ["One sentence."], -10, "old", minutes=2)
    assert c.written_at("gone") is None


def test_the_app_hands_the_ranker_a_working_probe():
    """The endpoints pass `_written_at_probe`; without it every heard tile
    stays excluded and nothing is ever remade."""
    import app as appmod
    probe = appmod._written_at_probe(2)
    assert probe is not None
    made = probe(T.TOPIC_BANK[0].query)
    assert made is None or isinstance(made, float)
