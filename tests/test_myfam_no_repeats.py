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


# --- Trending: never a heard story (§136) -----------------------------------

def story(i, last_seen, tags=("world",)):
    return T.Topic(id=f"st-{i}", title=f"Story number {i}", subtitle="",
                   query=f"what is going on with story {i}", tags=tags,
                   icon="", freshness=1.0 - i / 100, coverage=50 - i,
                   last_seen=last_seen)


FACETS = ("sports", "business", "tech", "politics", "world", "culture",
          "science", "health")


@pytest.fixture
def pool(monkeypatch):
    now = time.time()
    tiles = [story(i, now, tags=(FACETS[i % len(FACETS)],)) for i in range(8)]

    class Empty:
        def held(self, _now=None):
            return []

        def live(self, _now=None):
            return []

        def empty_reason(self, *_a, **_k):
            return ""

    monkeypatch.setattr(T, "live_topics", lambda now=None: list(tiles))
    monkeypatch.setattr(T.stories, "pool", lambda: Empty())
    return tiles


def trending(feed):
    return [t["id"] for t in rail(feed, "world_trending")]


def test_a_heard_trending_story_is_replaced_by_the_next_one(store, pool):
    first = trending(T.build_feed(store, "t1"))
    assert first[0] == "st-0"
    # Heard an hour ago: nothing new yet, so the next story takes the slot.
    store.record(T.Event("t1", "play", "st-0", pool[0].query, (),
                         time.time() - 3600))
    after = trending(T.build_feed(store, "t1"))
    assert "st-0" not in after and "st-0-new" not in after
    assert len(after) == T.SECTION_SIZE


def test_a_story_still_running_comes_back_as_whats_new(store, pool):
    heard_at = time.time() - 2 * DAY
    store.record(T.Event("t2", "play", "st-0", pool[0].query, (), heard_at))
    feed = T.build_feed(store, "t2")
    tile = rail(feed, "world_trending")[0]
    assert tile["id"] == "st-0-new"
    assert tile["query"] != pool[0].query and "since" in tile["query"]
    # And the original is nowhere on the page beside it.
    assert "st-0" not in on_page(feed)


def test_a_heard_follow_up_needs_newer_coverage_again(store, pool):
    now = time.time()
    store.record(T.Event("t3", "play", "st-0", pool[0].query, (), now - 2 * DAY))
    store.record(T.Event("t3", "play", "st-0-new", "what's new", (), now - 600))
    after = trending(T.build_feed(store, "t3"))
    assert "st-0" not in after and "st-0-new" not in after


def test_view_more_on_trending_applies_it_too(store, pool):
    store.record(T.Event("t4", "play", "st-1", pool[1].query, (),
                         time.time() - 60))
    section = T.build_section(store, "t4", "world_trending")
    assert "st-1" not in {t["id"] for t in section["topics"]}
    assert "st-1" not in {t["id"] for g in section["groups"] for t in g["topics"]}


def test_trending_order_is_still_popularity_not_taste(pool):
    h = T.heard_from([])
    assert [t.id for t in T.trending_for(pool, h, time.time())] == \
        [t.id for t in pool]


# --- View more on What you missed is its own ranking ------------------------

def test_view_more_on_missed_is_not_the_crowd_row(store, monkeypatch):
    calls = []
    real = T.rank_missed
    monkeypatch.setattr(T, "rank_missed",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    monkeypatch.setattr(T, "rank_most_played",
                        lambda *a, **k: pytest.fail("crowd row ranked"))
    T.build_section(store, "m1", "missed", interests=INTERESTS)
    assert calls


def test_a_second_follow_up_is_offered_once_the_story_moves_again(store, pool):
    now = time.time()
    store.record(T.Event("t5", "play", "st-0", pool[0].query, (), now - 3 * DAY))
    store.record(T.Event("t5", "play", "st-0-new", "what's new", (),
                         now - 2 * DAY))
    # Still reported now, two days after the first follow-up was heard.
    assert trending(T.build_feed(store, "t5"))[0] == "st-0-new"
    # And a personal rail may offer it too - the repeat check must not
    # block it for sharing an id with the follow-up they already heard.
    section = T.build_section(store, "t5", "from_history", interests=INTERESTS)
    assert "st-0-new" in {t["id"] for t in section["topics"]}


def test_made_for_you_never_offers_a_heard_story_as_itself(store, pool):
    """Trending is not the only rail that draws on the pool: a story heard
    yesterday must not come back on Made for you under its own title."""
    store.record(T.Event("t6", "play", "st-2", pool[2].query, pool[2].tags,
                         time.time() - 2 * DAY))
    for key in ("from_history", "missed"):
        section = T.build_section(store, "t6", key, interests=INTERESTS)
        assert "st-2" not in {t["id"] for t in section["topics"]}
    assert "st-2" not in on_page(T.build_feed(store, "t6", interests=INTERESTS))


def test_a_live_story_is_never_remade_under_its_own_title():
    now = time.time()
    tile = story(0, now)
    h = T.heard_from([(tile.id, tile.query, now - 5 * DAY)])
    assert T.is_repeat(tile, h, now, written_at=lambda q: None)
