"""The myFAM page the personalisation packet asks for.

Four rails, in one order, each on a different signal:

    Made for you                     what you listen to, live and evergreen
    Trending                         what the world is on, live only
    What FAM can't stop listening to what everyone here is playing, cached
    What your friends are listening  what your graph is playing, cached

And five general rules, each of which this file pins because each is the sort
of thing that decays quietly:

* **variety** - no rail is allowed to become one subject;
* **push with judgement** - a story is loud, then quieter, then gone;
* **title and angle only** - opening the page writes no script;
* **cost** - one inventory for everybody, personalisation in the ordering;
* **no queue** - nothing on the page-load path waits on anything.
"""
from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stories  # noqa: E402
import topics as T  # noqa: E402


@pytest.fixture(autouse=True)
def clean_pool():
    stories.reset()
    yield
    stories.reset()


@pytest.fixture
def store():
    return T.EventStore(":memory:")


def play(store, user, topic_id, kind="play", tags=(), at=None):
    store.record(T.Event(user, kind, topic_id, "", tags,
                         at=at if at is not None else time.time()))


def story(subject, tags, domain=stories.ATTENTION, first_seen=None, strength=1.0):
    return stories.Story(
        subject=subject, title=subject.title(), angle=f"why {subject} is the story",
        query=f"what is actually driving {subject} right now",
        domain=domain, source="test feed", tags=tuple(tags), strength=strength,
        first_seen=first_seen if first_seen is not None else time.time(),
        shelf_life=stories.DOMAIN_SHELF_LIFE[domain])


# --------------------------------------------------------------------------
# the page itself
# --------------------------------------------------------------------------
def test_the_rails_are_in_the_order_the_packet_asks_for():
    """"What FAM can't stop listening to" above "What your friends are
    listening to" - the crowd row that always has something in it goes above
    the one that is empty until somebody follows anybody."""
    assert [k for k, _ in T.SECTIONS] == [
        "from_history", "world_trending", "most_played", "followers"]
    assert [t for _k, t in T.SECTIONS] == [
        "Made for you",
        "Trending",
        "What FAM can't stop listening to",
        "What your friends are listening to",
    ]


def test_opening_the_page_writes_no_script(store):
    """The packet's central cost rule: a tile is a title and an angle, and the
    script is written when somebody taps it. Read off the source rather than
    asserted about behaviour, because the failure would be a slow page in
    production and a passing test here."""
    import ast
    import inspect

    for builder in (T.build_feed, T.build_section):
        tree = ast.parse(inspect.getsource(builder))
        # Docstrings out: this file talks about generation constantly, and a
        # check that read the prose would pass or fail on the wording.
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                node.value = ast.Constant("")
        code = ast.unparse(tree)
        for forbidden in ("pipeline", "generate", "ScriptGenerator", "await ",
                          "refresh("):
            assert forbidden not in code, (
                f"{builder.__name__} reaches for {forbidden}")


def test_a_tile_carries_a_question_worth_an_episode_and_not_a_headline():
    """A tile whose query is a headline produces an episode that reads the
    headline back. The title may be a label; the query may not."""
    stories.seed([story("the port strike", ("world",))])
    tile = T.live_topics()[0]
    assert tile.query != tile.title
    assert len(tile.query.split()) >= 5
    assert tile.angle, "a live tile says what it is about"


# --------------------------------------------------------------------------
# Made for you: both inventories, heavily personal
# --------------------------------------------------------------------------
def test_made_for_you_mixes_live_stories_with_the_evergreen_bank(store):
    play(store, "me", "chip-supply", kind="complete", tags=("tech", "chips"))
    stories.seed([story("the chip export rules", ("tech", "chips"))])

    feed = T.build_feed(store, "me")
    made = [s for s in feed["sections"] if s["key"] == "from_history"][0]
    ids = [t["id"] for t in made["topics"]]
    assert any(i.startswith("st-") for i in ids), "no live story reached the rail"
    assert any(not i.startswith("st-") for i in ids), "the bank was crowded out"


def test_a_fresh_story_outranks_a_standing_explainer_on_the_same_taste(store):
    """What `FRESHNESS_BOOST` buys, and the reason it exists: a bank topic
    carries hand-written tags and matches a profile more tidily than a story
    whose tags came off a keyword sweep, so without a thumb on the scale the
    bank wins every time and the page never changes."""
    play(store, "me", "chip-supply", kind="complete", tags=("tech", "chips"))
    stories.seed([story("the chip export rules", ("tech", "chips"))])
    feed = T.build_feed(store, "me")
    made = [s for s in feed["sections"] if s["key"] == "from_history"][0]
    assert made["topics"][0]["id"].startswith("st-")


def test_a_story_nobody_in_this_listener_cares_about_stays_off_their_rail(store):
    """"Filtered and influenced heavily by the individual's algorithm". A live
    story with no affinity is not "made for you" however hot it is."""
    play(store, "me", "chip-supply", kind="complete", tags=("tech", "chips"))
    stories.seed([story("the transfer window", ("sports",))])
    feed = T.build_feed(store, "me")
    made = [s for s in feed["sections"] if s["key"] == "from_history"][0]
    assert "sports" not in {tag for t in made["topics"] for tag in t["tags"]}


def test_a_story_that_has_expired_is_off_the_page_entirely(store):
    """The "for how long to push it" half of the judgement."""
    old = story("last week's story", ("tech",),
                first_seen=time.time() - 40 * 3600)
    stories.seed([old])
    assert T.live_topics() == []
    feed = T.build_feed(store, "me")
    trending_row = [s for s in feed["sections"] if s["key"] == "world_trending"][0]
    assert trending_row["topics"] == []


# --------------------------------------------------------------------------
# Trending: live only, never the play log
# --------------------------------------------------------------------------
def test_trending_never_ranks_what_fam_has_already_played(store):
    """"This does not use cached episodes." Answering "what is trending" with
    what FAM's listeners have played would make it a laggier copy of the row
    below it."""
    for listener in ("u1", "u2", "u3"):
        play(store, listener, "chip-supply", tags=("tech",))
    stories.seed([story("the port strike", ("world",))])

    feed = T.build_feed(store, "u4")
    row = [s for s in feed["sections"] if s["key"] == "world_trending"][0]
    assert [t["id"] for t in row["topics"]] == T.live_topics()[0].id.split()


def test_trending_is_drawn_from_one_inventory_for_everybody(store):
    """One fetch, one composition, every listener - the cost design the whole
    browse page rests on. A per-listener trending *inventory* would be the most
    expensive thing in the app rather than the cheapest.

    Two listeners can still be shown slightly different tiles out of it,
    because Made for you chooses first and a page must not show one person the
    same tile twice. That is personalisation in the ordering, which is what
    CLAUDE.md's cost rule actually permits - the scripts are still shared, so
    whoever taps a warmed tile takes the same one.
    """
    stories.seed([story(f"story {n}", ("world",)) for n in range(4)])
    play(store, "me", "chip-supply", kind="complete", tags=("tech",))
    pool_ids = {t.id for t in T.live_topics()}
    for listener in ("me", "somebody-else"):
        feed = T.build_feed(store, listener)
        shown = [t["id"] for t in
                 [s for s in feed["sections"]
                  if s["key"] == "world_trending"][0]["topics"]]
        assert shown, f"{listener} got an empty row"
        assert set(shown) <= pool_ids, "a second inventory appeared"


def test_no_tile_is_shown_twice_on_one_page(store):
    """Made for you and Trending draw on the same live pool now, so the two
    can collide - and a page showing one listener the same tile twice reads as
    a bug whatever the ranking meant by it."""
    play(store, "me", "chip-supply", kind="complete", tags=("tech", "chips"))
    stories.seed([story("the chip export rules", ("tech", "chips")),
                  story("the port strike", ("world",))])
    feed = T.build_feed(store, "me")
    shown = [t["id"] for s in feed["sections"] for t in s["topics"]]
    assert len(shown) == len(set(shown)), "a tile appears on two rails"


def test_the_loudest_story_leads_the_trending_row(store):
    stories.seed([
        story("quiet thing", ("world",), strength=0.2),
        story("loud thing", ("tech",), strength=1.0),
    ])
    feed = T.build_feed(store, "me")
    row = [s for s in feed["sections"] if s["key"] == "world_trending"][0]
    assert row["topics"][0]["title"] == "Loud Thing"


# --------------------------------------------------------------------------
# variety, on every rail
# --------------------------------------------------------------------------
def test_no_rail_becomes_one_subject(store):
    """The packet's first line. A rail of six with four sports tiles reads as
    a sports rail, whatever the ranking thought it was doing."""
    # A day with something in it, which is what the pool's own facet cap
    # guarantees the rails will be handed: `stories.seed` bypasses that cap,
    # so an inventory built here has to look like one a refresh would produce.
    stories.seed([story(f"game {n}", ("sports", "sports-drama")) for n in range(3)]
                 + [story(f"chip story {n}", ("tech", "chips")) for n in range(3)]
                 + [story(f"rates story {n}", ("money", "macro")) for n in range(3)]
                 + [story(f"world story {n}", ("world",)) for n in range(3)])
    for tag, topic_id in (("sports", "golf-evolution"), ("tech", "chip-supply"),
                          ("money", "fed-next-move"), ("science", "space-race")):
        play(store, "me", topic_id, kind="complete", tags=(tag,))

    feed = T.build_feed(store, "me")
    for section in feed["sections"]:
        facets = [T.facet_of(t["tags"][0]) for t in section["topics"] if t["tags"]]
        if len(facets) < T.SECTION_SIZE:
            continue
        # Three of six, not "at most two of each". The cap is what does the
        # work, but it tops a short rail up rather than leaving a hole (see
        # the next test), so the assertion that survives both behaviours is
        # the one a listener would actually make: this row is about more than
        # one thing.
        assert len(set(facets)) >= 3, (
            f"{section['key']} is really one subject: {facets}")
        assert max(facets.count(f) for f in set(facets)) <= T.SECTION_SIZE - 2, (
            f"{section['key']} is dominated by one subject: {facets}")


def test_a_listener_with_one_interest_still_gets_a_full_rail(store):
    """The cost of the cap, stated so it is a known trade rather than a
    surprise. Somebody whose entire history is sport has nothing else with any
    affinity, so the cap has nothing to reach for - and a two-tile rail would
    read as broken where a samey six-tile one reads as a taste. The variety
    rule is a cap on what is available, never a quota on what is not.
    """
    stories.seed([story(f"game {n}", ("sports", "sports-drama"))
                  for n in range(8)])
    for _ in range(4):
        play(store, "me", "golf-evolution", kind="complete", tags=("sports",))
    feed = T.build_feed(store, "me")
    made = [s for s in feed["sections"] if s["key"] == "from_history"][0]
    assert len(made["topics"]) == T.SECTION_SIZE


def test_the_variety_cap_holds_while_there_is_something_to_reach_for():
    tiles = ([T.Topic(f"s{n}", f"S{n}", "", "q", ("sports",), "sports")
              for n in range(5)]
             + [T.Topic(f"t{n}", f"T{n}", "", "q", ("tech",), "tech")
                for n in range(3)])
    picked = T.diversify(tiles, 4)
    facets = [t.tags[0] for t in picked]
    assert facets.count("sports") == T.MAX_PER_FACET
    assert picked[0].id == "s0", "the best tile is still first"


def test_the_variety_cap_tops_up_rather_than_returning_a_short_rail():
    """A half-empty rail reads as broken; a slightly samey one does not."""
    tiles = [T.Topic(f"t{n}", f"T{n}", "", "q", ("sports",), "sports")
             for n in range(6)]
    assert len(T.diversify(tiles, 6)) == 6
    assert T.diversify(tiles, 6)[0].id == "t0", "the best tile is still first"


# --------------------------------------------------------------------------
# the two cached rows
# --------------------------------------------------------------------------
def test_what_fam_cant_stop_listening_to_ranks_total_listens(store):
    for listener in ("u1", "u2", "u3"):
        play(store, listener, "space-race", tags=("science",))
    play(store, "u1", "golf-evolution", tags=("sports",))
    picks = T.rank_most_played(store)
    assert picks[0].id == "space-race"


def test_a_live_story_can_be_what_fam_cant_stop_listening_to(store):
    """It is a ranking of what this app is playing, not of the bank. A story
    tapped by fifty people belongs here as much as any evergreen topic."""
    hot = story("the port strike", ("world",))
    stories.seed([hot])
    for listener in ("u1", "u2", "u3", "u4"):
        play(store, listener, hot.id, tags=("world",))
    assert T.rank_most_played(store)[0].id == hot.id


def test_the_cached_rows_lead_with_what_is_already_written(store):
    """"Stories FAM users are listening to (cached)". A written tile starts
    instantly and costs nothing to serve, which is a real difference between
    two tiles a listener is choosing between."""
    for listener in ("u1", "u2", "u3"):
        play(store, listener, "golf-evolution", tags=("sports",))
    for listener in ("u1", "u2"):
        play(store, listener, "space-race", tags=("science",))

    written = {T.BANK_BY_ID["space-race"].query}
    picks = T.rank_most_played(store, written=lambda q: q in written)
    assert picks[0].id == "space-race", "the cached one should lead"

    # And without a cache to ask, the play counts decide on their own.
    assert T.rank_most_played(store)[0].id == "golf-evolution"


def test_an_empty_cache_does_not_empty_the_row(store):
    """Preferring cached is a sort and never a filter: a deployment whose
    cache has just expired would otherwise show an empty row, which is a fact
    about the cache told as a fact about what people are playing."""
    for listener in ("u1", "u2"):
        play(store, listener, "golf-evolution", tags=("sports",))
    assert T.rank_most_played(store, written=lambda q: False)


# --------------------------------------------------------------------------
# friends: the real graph
# --------------------------------------------------------------------------
def test_the_friends_rail_reads_the_circle_it_is_given(store):
    play(store, "friend", "space-race", tags=("science",))
    play(store, "stranger", "restaurant-scene", tags=("culture",))
    picks = T.rank_friends(store, ["friend"], exclude=set())
    ids = [p.id for p in picks]
    assert "space-race" in ids
    assert "restaurant-scene" not in ids, "a stranger is not a friend"


def test_the_friends_rail_is_empty_rather_than_filled_with_strangers(store):
    """The rename is a promise. A rail called "what your friends are listening
    to" that quietly showed co-listeners would be the original problem again
    with better wording."""
    play(store, "stranger", "space-race", tags=("science",))
    assert T.rank_friends(store, [], exclude=set()) == []

    feed = T.build_feed(store, "me")
    row = [s for s in feed["sections"] if s["key"] == "followers"][0]
    assert row["topics"] == []
    assert "Follow some people" in row["empty_reason"]


def test_the_empty_friends_rail_names_what_to_do_about_it():
    """It is empty for exactly one reason, and the listener can fix it in two
    taps. Saying "nobody has listened yet" would blame the app instead."""
    said = T._empty_reason("followers").lower()
    assert "follow" in said
    assert "nobody" not in said


# --------------------------------------------------------------------------
# what a tap logs
# --------------------------------------------------------------------------
def test_tapping_a_live_story_teaches_the_taste_model_something(store):
    """A story's tags live in the pool and nowhere else. An event logged
    without them records the tap and learns nothing from it."""
    hot = story("the chip export rules", ("tech", "chips"))
    stories.seed([hot])
    assert T.tags_for_id(hot.id) == ("tech", "chips")
    # And after it has expired, the words of the question are the fallback.
    stories.reset()
    assert "tech" in T.tags_for_id(hot.id, "what the new chip rules change")


def test_an_impression_on_a_live_story_is_recorded_with_its_tags(store):
    hot = story("the chip export rules", ("tech", "chips"))
    stories.seed([hot])
    store.record_impressions("me", [("world_trending", hot.id)])
    shown = store.impressions_for("me")
    assert shown and shown[0].tags == ("tech", "chips")


def test_an_empty_trending_rail_never_blames_the_sources_for_our_own_ordering(store):
    """Made for you chooses first, so it can empty this rail on a day the pool
    served perfectly well. Saying "the live sources had nothing" there would be
    our own page's arrangement reported as a fact about the world - §89's
    mistake with a new way in."""
    play(store, "me", "chip-supply", kind="complete", tags=("tech", "chips"))
    # One story, and it is one this listener's history claims.
    stories.seed([story("the chip export rules", ("tech", "chips"))])

    feed = T.build_feed(store, "me")
    by_key = {s["key"]: s for s in feed["sections"]}
    assert by_key["from_history"]["topics"], "the rail above did not claim it"
    row = by_key["world_trending"]
    assert row["topics"] == []
    said = row["empty_reason"].lower()
    assert "made for you" in said, f"the rail does not say where it went: {said}"
    for blame in ("didn't answer", "had nothing", "isn't connected",
                  "couldn't reach"):
        assert blame not in said, (
            f"an empty rail blamed the sources for our own ordering: {said}")


def test_an_empty_trending_rail_with_an_empty_pool_still_names_the_gap(store):
    """The other half. With nothing in the pool the honest sentence is about
    this deployment, and it is the pool's own - it knows which way it came up
    empty."""
    feed = T.build_feed(store, "me")
    row = [s for s in feed["sections"] if s["key"] == "world_trending"][0]
    assert row["empty_reason"] == stories.pool().empty_reason
    assert "isn't connected" in row["empty_reason"]
