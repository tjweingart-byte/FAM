"""The 20/09 packet: relevance, the interests row, and what a guest may reach.

Five separate complaints, and they share one shape - the app was answering a
question with whatever it had rather than with what was actually relevant:

* Trending showed one tile, because it was filled last from what four
  personal rails had left over;
* Made for you offered "a random small school college football matchup that
  I've never indicated ... I would be interested in";
* "What you missed last week" was everything put in front of them rather
  than the most relevant of it;
* the profile listed every interest anybody could infer, in a fixed order
  that a month of listening never changed.

Each test here names the behaviour, not the implementation, so a better
ranker that still answers these correctly does not have to edit this file.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import preferences as prefs_mod  # noqa: E402
import stories  # noqa: E402
import topics as T  # noqa: E402


# §127: every call here passes `floors={}` - these tests are about what a
# rail *chooses*, and since §127 each drawn rail but friends is topped up to
# a minimum afterwards. The floor is pinned in test_implementations_127.py.
@pytest.fixture()
def store(tmp_path):
    stories.seed([])
    yield T.EventStore(str(tmp_path / "events.db"))
    stories.seed([])


def story(subject, tags, strength=1.0, now=None):
    now = time.time() if now is None else now
    return stories.Story(
        subject=subject, title=subject.title(),
        angle=f"why {subject} is the story",
        query=f"what is actually driving {subject} right now",
        domain=stories.ATTENTION, source="test feed", tags=tuple(tags),
        strength=strength, first_seen=now,
        shelf_life=stories.DOMAIN_SHELF_LIFE[stories.ATTENTION])


def finished(store, user, topic_id, text, tags, at=None):
    store.record(T.Event(user, "complete", topic_id, text, tuple(tags),
                         at=at if at is not None else time.time() - 100))


def rail(feed, key):
    return [s for s in feed["sections"] if s["key"] == key][0]


# --------------------------------------------------------------------------
# Trending always has something in it
# --------------------------------------------------------------------------
def test_trending_keeps_four_tiles_when_the_pool_can_fill_it(store):
    """"There are times when there is only one trending episode being
    displayed. At all times I want there to be at least four."

    The cause was the fill order, not the sources: Trending was filled last,
    from what the personal rails had not claimed, and Made for you draws on
    the same live pool. A listener whose taste matches most of a thin pool
    took nearly all of it.
    """
    finished(store, "me", "chip-supply", "nvidia chip supply", ("tech", "chips"))
    finished(store, "me", "ai-agents", "ai agents", ("tech", "ai"))
    stories.seed([story("the chip export rules", ("tech", "chips"), 1.0),
                  story("the ai training lawsuit", ("tech", "ai"), 0.95),
                  story("the model weights leak", ("tech", "ai"), 0.9),
                  story("the fab subsidy fight", ("tech", "chips"), 0.85),
                  story("the fed decision", ("money", "macro"), 0.8),
                  story("the port strike", ("world",), 0.75)])

    world = rail(T.build_feed(store, "me", floors={}), "world_trending")["topics"]
    assert len(world) >= T.WORLD_FLOOR, (
        f"Trending showed {len(world)} tiles from a pool of six")


def test_a_pool_too_small_to_fill_trending_is_not_taken_from_the_listener(store):
    """The floor is reserved only when reserving buys it.

    A pool holding one story cannot fill this row however it is shared out,
    so holding that story back would take it off the personal rail and still
    leave Trending short - a cost with nothing bought.
    """
    finished(store, "me", "chip-supply", "nvidia chip supply", ("tech", "chips"))
    stories.seed([story("the chip export rules", ("tech", "chips"))])

    feed = T.build_feed(store, "me", floors={})
    assert rail(feed, "from_history")["topics"], "the one story went to nobody"
    assert rail(feed, "from_history")["topics"][0]["id"].startswith("st-")


def test_an_empty_trending_rail_still_never_blames_the_world(store):
    """Unchanged by the floor, and worth pinning: an empty row is a fact
    about this deployment, never a claim about the world (§89)."""
    stories.seed([])
    said = rail(T.build_feed(store, "me", floors={}), "world_trending")["empty_reason"]
    assert said
    assert "nothing is trending" not in said.lower()


# --------------------------------------------------------------------------
# Made for you stops offering things nobody asked about
# --------------------------------------------------------------------------
def test_a_broad_sports_match_on_an_unfamiliar_subject_is_damped(store):
    """The reported failure, in the smallest form that reproduces it.

    One NFL episode puts weight on `sports`. Every sports story in the world
    carries `sports`. Nothing in the tag vocabulary separates the NFL from a
    small-school college fixture - both are `sports` - so the only honest
    signal available is whether this listener has ever used any of the words
    on the tile.
    """
    finished(store, "me", "nfl-trade", "the nfl trade deadline",
             ("sports", "sports-drama"))
    stories.seed([
        story("wofford versus mercer", ("sports",), 1.0),
        story("the nfl trade deadline fallout", ("sports", "sports-drama"), 0.6),
    ])

    made = [t["title"] for t in rail(T.build_feed(store, "me", floors={}), "from_history")["topics"]]
    if made:
        assert "Wofford Versus Mercer" not in made[:1], (
            "a subject this listener has never been near led the rail")


def test_a_specific_match_outranks_a_broad_one(store):
    """`SUBTAG_WEIGHT`. Subtags have existed since §80 and the scorer was
    blind to them, so `sports` and `sports-drama` counted the same - the
    resolution added to the vocabulary was being thrown away by the
    ranking."""
    profile = {"sports": 1.0, "sports-drama": 1.0}
    broad = T.Topic(id="b", title="b", subtitle="", query="b", tags=("sports",),
                    icon="world")
    sharp = T.Topic(id="s", title="s", subtitle="", query="s",
                    tags=("sports-drama",), icon="world")
    assert T._affinity(sharp, profile) > T._affinity(broad, profile)


def test_a_tile_nobody_could_call_relevant_is_not_offered(store):
    """`RELEVANCE_FLOOR`. It used to be `> 0`, which every tile sharing one
    barely-touched facet clears - so the rail was the whole bank sorted,
    with a heading claiming it was chosen for this listener."""
    finished(store, "me", "chip-supply", "nvidia chip supply", ("tech", "chips"))
    picks = T.rank_from_history(
        T.taste(store.for_user("me")), exclude=set(), limit=40)
    assert picks, "the floor emptied the rail"
    assert all("tech" in t.tags or "chips" in t.tags or "ai" in t.tags
               or "platforms" in t.tags for t in picks), \
        "something with no tech affinity cleared the floor"


def test_a_listener_with_one_interest_still_gets_something(store):
    """The cap and the floor are both caps on what is available and never
    quotas on what is not. A listener whose entire history is one facet must
    not end up with an empty page."""
    finished(store, "me", "nfl-trade", "the nfl trade deadline",
             ("sports", "sports-drama"))
    feed = T.build_feed(store, "me", floors={})
    assert rail(feed, "from_history")["topics"], "one interest emptied the page"


# --------------------------------------------------------------------------
# Familiarity is read off the log and never invented
# --------------------------------------------------------------------------
def test_familiar_words_come_from_what_the_listener_actually_said(store):
    finished(store, "me", "nfl-trade", "what happened with the nfl trade deadline",
             ("sports",))
    words = T.familiar_words(store.for_user("me"))
    assert "nfl" in words
    assert "deadline" in words
    # Stopwords are not subjects. Without this every tile matches on "the".
    assert "the" not in words
    assert "what" not in words


def test_a_listener_with_no_history_has_no_familiar_words(store):
    assert T.familiar_words(store.for_user("me")) == frozenset()


# --------------------------------------------------------------------------
# What you missed last week
# --------------------------------------------------------------------------
def test_trending_is_the_last_source_for_what_you_missed(store):
    """A story nobody has been shown is not one this listener missed - it is
    one Made for you exists to offer them. So the rail's first pass excludes
    trending and it is topped up from the leftovers afterwards."""
    finished(store, "me", "chip-supply", "nvidia chip supply", ("tech", "chips"))
    stories.seed([story("the chip export rules", ("tech", "chips"))])

    feed = T.build_feed(store, "me", floors={})
    made = {t["id"] for t in rail(feed, "from_history")["topics"]}
    missed = {t["id"] for t in rail(feed, "missed")["topics"]}
    assert not (made & missed), "one tile on two rails"
    assert any(i.startswith("st-") for i in made), \
        "the missed rail took the live story ahead of Made for you"


def test_a_reserved_trending_tile_is_not_also_on_the_missed_rail(store):
    """The bug the floor introduced, and the reason the two rails take
    different exclusion sets.

    `missed` fills first, so the `used` set it was given was empty - which
    was the same thing as `seen` until `WORLD_FLOOR` reserved tiles before
    the loop ran. It is not any more. `include_trending=False` closes the
    live-pool route into this rail but not the impression route, so a story
    that Trending had already been promised, and that this listener happened
    to be shown last week, landed on both rails of one page.
    """
    now = time.time()
    finished(store, "me", "chip-supply", "nvidia chip supply", ("tech", "chips"),
             at=now - 200)
    stories.seed([story("the chip export rules", ("tech", "chips"), 1.0, now),
                  story("the ai training lawsuit", ("tech", "ai"), 0.95, now),
                  story("the model weights leak", ("tech", "ai"), 0.9, now),
                  story("the fab subsidy fight", ("tech", "chips"), 0.85, now),
                  story("the fed decision", ("money", "macro"), 0.8, now)])
    # Every live tile was put in front of them, which is what gives the
    # missed rail a claim on a story Trending has reserved.
    for tile in T.live_topics(now):
        store.record(T.Event("me", T.IMPRESSION, tile.id, "", tile.tags,
                             now - 3 * 86400, section="from_history",
                             algo=T.ALGO_VERSION))

    feed = T.build_feed(store, "me", now=now, floors={})
    shown = [t["id"] for sec in feed["sections"] for t in sec["topics"]]
    assert len(shown) == len(set(shown)), (
        "a tile appears on two rails: "
        + ", ".join(sorted({i for i in shown if shown.count(i) > 1})))
    assert len(rail(feed, "world_trending")["topics"]) >= T.WORLD_FLOOR


def test_an_impression_still_never_becomes_taste(store):
    """Membership may come from an impression; the profile may not. Letting
    it in is how a feed teaches itself its own preferences."""
    now = time.time()
    for topic in list(T.TOPIC_BANK)[:6]:
        store.record(T.Event("me", T.IMPRESSION, topic.id, "", topic.tags,
                             now - 86400, section="from_history",
                             algo=T.ALGO_VERSION))
    assert T.taste(store.for_user("me"), now) == {}


# --------------------------------------------------------------------------
# The profile's interests row
# --------------------------------------------------------------------------
def test_the_profile_shows_at_most_four_interests(store):
    for topic_id, text, tags in (
            ("a", "nvidia chips", ("tech", "chips")),
            ("b", "the fed", ("money", "macro")),
            ("c", "the nfl", ("sports", "sports-drama")),
            ("d", "sleep research", ("health", "sleep")),
            ("e", "the election", ("world", "elections")),
            ("f", "a new film", ("culture", "film-tv"))):
        finished(store, "me", topic_id, text, tags)
    ranked = T.ranked_interests(store, "me")
    assert len(ranked) > T.PROFILE_INTEREST_SLOTS, "not enough to cut"
    shown, source = T.profile_interests(ranked)
    assert len(shown) == T.PROFILE_INTEREST_SLOTS
    assert source == "top"


def test_the_top_interests_move_as_the_listener_listens(store):
    """"These top three or four will be continuously updating the more
    episodes they listen to." The old row was chosen facets first in their
    stored order, so day one outranked a month of listening forever."""
    now = time.time()
    finished(store, "me", "a", "the nfl", ("sports", "sports-drama"), at=now - 60)
    first = [row["id"] for row in T.ranked_interests(store, "me", chosen=("tech",))]
    for i in range(6):
        finished(store, "me", f"t{i}", "nvidia chips and ai models",
                 ("tech", "chips", "ai"), at=now - 30)
    later = [row["id"] for row in T.ranked_interests(store, "me", chosen=("tech",))]
    assert first[0] == "sports"
    assert later[0] == "tech", f"listening did not move the row: {later}"


def test_a_pinned_set_wins_and_says_so(store):
    finished(store, "me", "a", "nvidia chips", ("tech", "chips"))
    finished(store, "me", "b", "the fed", ("money", "macro"))
    ranked = T.ranked_interests(store, "me")
    shown, source = T.profile_interests(ranked, pinned=["money"])
    assert [row["id"] for row in shown] == ["money"]
    assert source == "pinned", (
        "a pinned row and an automatic one look identical on screen, and "
        "only one of them is 'kept up to date as you listen'")


def test_a_hidden_interest_stays_hidden_when_nothing_is_pinned(store):
    """Somebody who turned an interest off before the editor moved did not
    ask for it back."""
    finished(store, "me", "a", "nvidia chips", ("tech", "chips"))
    finished(store, "me", "b", "the fed", ("money", "macro"))
    ranked = T.ranked_interests(store, "me")
    shown, _ = T.profile_interests(ranked, hidden=["tech"])
    assert "tech" not in [row["id"] for row in shown]


def test_a_named_subject_can_be_pinned_beside_a_facet(store):
    """Two vocabularies, one row. The catalogue's subjects are what a
    listener recognises; the eight facets are what the ranker scores."""
    finished(store, "me", "a", "formula 1 racing", ("sports",))
    ranked = T.ranked_interests(store, "me", chosen_topics=("formula1",))
    assert "formula1" in [row["id"] for row in ranked]
    shown, _ = T.profile_interests(ranked, pinned=["formula1"])
    assert shown[0]["label"] == "Formula 1"


def test_the_store_refuses_a_fifth_pinned_interest(tmp_path):
    store = prefs_mod.PreferenceStore(str(tmp_path / "p.db"))
    saved = store.save("u", profile_interests=["tech", "money", "sports",
                                               "health", "culture"])
    assert len(saved.profile_interests) == prefs_mod.PROFILE_INTERESTS_MAX


def test_an_empty_pin_means_choose_for_me(tmp_path):
    """Not "show nothing". A stored empty tuple and a listener who has never
    touched this are the same state, deliberately."""
    store = prefs_mod.PreferenceStore(str(tmp_path / "p.db"))
    store.save("u", profile_interests=["tech"])
    assert store.save("u", profile_interests=[]).profile_interests == ()


def test_a_database_written_before_the_column_existed_still_opens(tmp_path):
    """The case a fresh test database never reaches.

    Every deployment that has ever had a listener has preference rows
    predating `profile_interests`, and `get` selects that column by position.
    A missing column raises inside a broad `except` that returns the
    defaults - so the failure would not be a crash, it would be every
    listener's stored interests and language quietly reading as unset.
    """
    import sqlite3

    path = str(tmp_path / "old.db")
    # The schema as it shipped before this column, with a row in it.
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("""CREATE TABLE preferences (
                        user_id      TEXT PRIMARY KEY,
                        interests    TEXT NOT NULL DEFAULT '',
                        language     TEXT NOT NULL DEFAULT 'en',
                        weekly_recap INTEGER NOT NULL DEFAULT 1,
                        recap_week   TEXT NOT NULL DEFAULT '',
                        intro_done   INTEGER NOT NULL DEFAULT 0,
                        updated      REAL NOT NULL,
                        hidden_interests TEXT NOT NULL DEFAULT '',
                        topics TEXT NOT NULL DEFAULT '')""")
    conn.execute("INSERT INTO preferences (user_id, interests, language,"
                 " intro_done, updated, hidden_interests, topics)"
                 " VALUES ('old', 'tech,sports', 'en', 1, 0, 'sports', 'Formula 1')")
    conn.close()

    store = prefs_mod.PreferenceStore(path)
    held = store.get("old")
    assert held.interests == ("tech", "sports"), "an existing row read as empty"
    assert held.hidden_interests == ("sports",)
    assert held.topics == ("Formula 1",)
    # Absent means "choose for me", which is what every pre-existing row means.
    assert held.profile_interests == ()
    # And it is writable afterwards, which is the other half of a migration.
    assert store.save("old", profile_interests=["tech"]).profile_interests == ("tech",)


def test_the_two_caps_agree(tmp_path):
    """`topics` cannot import `preferences` - it is the other way round - so
    the two constants are pinned together here rather than by an import."""
    assert T.PROFILE_INTEREST_SLOTS == prefs_mod.PROFILE_INTERESTS_MAX
