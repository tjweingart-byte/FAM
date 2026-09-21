"""myFAM: the shared bank, the taste model, and the four sections.

The thing worth testing here is not that the endpoint returns JSON. It is
that the four sections run on four different signals - the failure mode of
any feed like this is four headings over one ranked list.
"""
from __future__ import annotations

import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import topics as T  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return T.EventStore(str(tmp_path / "myfam.db"))


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "EVENTS", T.EventStore(str(tmp_path / "api.db")))
    return TestClient(appmod.app)


def play(store, user, topic_id, kind="play", ago=0.0):
    store.record(T.Event(user, kind, topic_id, "",
                         T.BANK_BY_ID[topic_id].tags, time.time() - ago))


# --- the bank -------------------------------------------------------------


def test_the_bank_is_shared_so_scripts_can_be_shared():
    """The cost argument: one bank, one script per topic, reused by everyone."""
    queries = [t.query for t in T.TOPIC_BANK]
    assert len(set(queries)) == len(queries), "a duplicate query wastes a cache slot"
    ids = [t.id for t in T.TOPIC_BANK]
    assert len(set(ids)) == len(ids)
    for topic in T.TOPIC_BANK:
        assert topic.tags, f"{topic.id} has no tags, so it can never be ranked"
        assert set(topic.tags) <= set(T.TAG_WORDS), f"{topic.id} has an unknown tag"


def test_a_bank_query_reads_as_a_real_question():
    """Tiles generate from `query`, not `title`. A title is not a question."""
    for topic in T.TOPIC_BANK:
        assert len(topic.query.split()) >= 4, f"{topic.id}: query is too thin to brief"
        assert topic.query == topic.query.strip()


# --- taste ----------------------------------------------------------------


def test_taste_is_built_from_what_they_actually_finished():
    now = time.time()
    events = [
        T.Event("u", "complete", "", "", ("sports",), now),
        T.Event("u", "play", "", "", ("culture",), now),
    ]
    profile = T.taste(events, now)
    assert profile["sports"] > profile["culture"], "finishing beats merely starting"


def test_a_skip_counts_against_a_tag():
    """A skip is evidence, not a weak play - otherwise skipping recommends more."""
    now = time.time()
    profile = T.taste([T.Event("u", "skip", "", "", ("sports",), now)], now)
    assert profile["sports"] < 0


def test_old_interests_fade():
    now = time.time()
    events = [
        T.Event("u", "complete", "", "", ("sports",), now - 60 * T.HALF_LIFE),
        T.Event("u", "play", "", "", ("tech",), now),
    ]
    profile = T.taste(events, now)
    assert profile["tech"] > profile["sports"], "a feed should not be a museum"


def test_free_text_searches_are_tagged_so_history_counts():
    assert "money" in T.tags_for_text("what is the fed doing about inflation")
    assert "health" in T.tags_for_text("how do I fix my sleep")
    assert T.tags_for_text("zzzz nonsense") == ()


# --- the four sections ----------------------------------------------------


def test_trending_ignores_the_listener_entirely(store):
    """It is the same for everyone; that is what makes it the cheapest section."""
    for _ in range(3):
        play(store, "someone", "fed-next-move")
    play(store, "other", "golf-evolution")
    ranked = T.rank_most_played(store)
    assert ranked[0].id == "fed-next-move"


def test_trending_is_not_empty_on_a_cold_start(store):
    assert len(T.rank_most_played(store)) == T.SECTION_SIZE


def test_history_recommends_what_they_already_like(store):
    play(store, "u", "golf-evolution", kind="complete")
    profile = T.taste(store.for_user("u"))
    picks = T.rank_from_history(profile, exclude=set())
    assert any("sports" in p.tags for p in picks[:2])


def test_history_never_recommends_what_they_just_played(store):
    play(store, "u", "golf-evolution", kind="complete")
    events = store.for_user("u")
    picks = T.rank_from_history(T.taste(events), exclude=T._played_ids(events))
    assert all(p.id != "golf-evolution" for p in picks)


def test_might_like_is_not_the_same_list_as_history(store):
    """The whole reason for separate sections: distinct signals, not shuffles.

    might_like no longer has a section of its own, but it stays under test:
    it is the only signal that widens a taste rather than confirming it, and
    a silently rotting one is worse than none if it is ever put back.
    """
    play(store, "u", "golf-evolution", kind="complete")
    play(store, "u", "the-trade", kind="complete")
    profile = T.taste(store.for_user("u"))
    history = [t.id for t in T.rank_from_history(profile, set())]
    might = [t.id for t in T.rank_might_like(profile, set())]
    assert history[:3] != might[:3], "might_like is just history with a new heading"


def test_might_like_suppresses_their_strongest_tag(store):
    """Ranking on raw affinity builds a filter bubble by accident."""
    for _ in range(4):
        play(store, "u", "golf-evolution", kind="complete")
    profile = T.taste(store.for_user("u"))
    picks = T.rank_might_like(profile, set())
    assert picks, "a listener with taste should still get suggestions"
    assert not all("sports" in p.tags for p in picks)


def test_followers_uses_people_who_overlap_with_you(store):
    # Two listeners share golf; the neighbour also plays the space episode.
    play(store, "me", "golf-evolution")
    play(store, "neighbour", "golf-evolution")
    play(store, "neighbour", "space-race")
    play(store, "stranger", "restaurant-scene")
    mine = T._played_ids(store.for_user("me"))
    picks = T.rank_followers(store, "me", mine, exclude={"golf-evolution"})
    ids = [p.id for p in picks]
    assert "space-race" in ids
    assert "restaurant-scene" not in ids, "a stranger's taste is not a signal"


def test_followers_is_empty_rather_than_faked_for_a_new_listener(store):
    assert T.rank_followers(store, "nobody", set(), set()) == []


# --- the whole feed -------------------------------------------------------


def test_no_topic_appears_in_two_sections(store):
    play(store, "u", "golf-evolution", kind="complete")
    play(store, "friend", "golf-evolution")
    play(store, "friend", "space-race")
    feed = T.build_feed(store, "u")
    seen = [t["id"] for s in feed["sections"] for t in s["topics"]]
    assert len(seen) == len(set(seen)), "the page repeats itself"


def test_a_new_listener_gets_an_honest_page_not_a_fake_one(store):
    """Honest, and no longer empty where it does not have to be.

    `from_history` used to be in this list, and it was the right assertion
    against the right rule for as long as the only way to fill that rail was
    to invent a taste nobody had. It is filled from the **startup set** now -
    eight time-anchored questions, one per facet, ordered by what FAM's
    listeners actually play (`startup.py`) - which is a real signal that is
    simply not *this* listener's, so the rail is real and the heading stops
    saying "Made for you". `taste_source` is what says which happened.

    The rule the test was protecting is unchanged and is still pinned below:
    a rail that would have to make something up is empty and says why.
    """
    feed = T.build_feed(store, "brand-new")
    by_key = {s["key"]: s for s in feed["sections"]}
    assert by_key["most_played"]["topics"], "the crowd row works with no history at all"
    assert not feed["personalised"]
    assert feed["taste_source"] == "startup"
    # Filled, and openly not from this listener's taste.
    assert by_key["from_history"]["topics"]
    assert not by_key["from_history"]["empty_reason"]
    # And the rails that could only be filled by inventing something are not.
    for key in ("followers", "missed"):
        assert not by_key[key]["topics"], f"{key} invented something"
        assert by_key[key]["empty_reason"], f"{key} must say why it is empty"


def test_the_four_sections_are_always_present_and_in_order(store):
    feed = T.build_feed(store, "u")
    assert [s["key"] for s in feed["sections"]] == [k for k, _ in T.SECTIONS]


def test_explore_new_is_off_the_page_but_still_ranked(store):
    """It has now been taken off this page twice, and the second time was a
    decision rather than an accident - Trending took its slot.

    What that costs is worth keeping written down: `rank_might_like` is the
    only signal that offers anything *outside* an established taste, so
    without its rail myFAM is history, co-listeners and the crowd - three
    ways of being told what you already like.

    So the ranker stays filled and reachable. `UNSHELVED` is what says the
    absence is deliberate, and keeping it in `FILL_ORDER` is what keeps
    putting the rail back a one-line change.
    """
    keys = [k for k, _ in T.SECTIONS]
    assert "might_like" not in keys, "Explore New is back on myFAM"
    assert "might_like" in T.UNSHELVED
    assert "might_like" in T.FILL_ORDER, \
        "Explore New is not filled, so /api/explorenew would rank the leftovers"


def test_trending_took_the_slot(store):
    """Second, not last. Last is where a row nobody scrolls to goes, and this
    is the one rail on the page with a reason to be looked at today."""
    keys = [k for k, _ in T.SECTIONS]
    assert keys[1] == "world_trending", keys
    assert keys.index("from_history") < keys.index("world_trending") \
        < keys.index("most_played")


def test_explore_new_offers_something_outside_an_established_taste(store):
    """The actual point of it, rather than its position.

    A listener who only plays one tag must not get a fourth rail of that tag -
    that is the filter bubble arrived at by accident, and it is what
    `rank_might_like` mutes the strongest tag to avoid.
    """
    for _ in range(4):
        store.record(T.Event("u", "complete", "chip-supply", "", ("tech",)))
    # Through its own surface, since it is not a section on the page.
    picks = T.build_explore_new(store, "u")["topics"]
    assert picks, "Explore New came back empty for a listener with a clear taste"
    assert not all("tech" in (t.get("tags") or []) for t in picks), \
        "Explore New returned only the tag this listener already plays"


def test_the_personal_section_comes_before_the_popular_one(store):
    """Someone opening myFAM should not scroll past the crowd to reach it."""
    keys = [k for k, _ in T.SECTIONS]
    assert keys.index("from_history") < keys.index("most_played")
    # Fill order is the opposite on purpose: the constrained sections pick first.
    assert T.FILL_ORDER.index("from_history") < T.FILL_ORDER.index("most_played")


# --- the API --------------------------------------------------------------


def test_the_feed_endpoint_works_without_a_user(client):
    body = client.get("/api/myfam").json()
    assert [s["key"] for s in body["sections"]] == [k for k, _ in T.SECTIONS]


def test_recording_an_event_changes_the_feed(client):
    before = client.get("/api/myfam?user=u1").json()
    for _ in range(3):
        client.post("/api/event", json={"user": "u1", "kind": "complete",
                                        "topic_id": "sleep-science"})
    after = client.get("/api/myfam?user=u1").json()
    assert after["personalised"] and not before["personalised"]
    history = [s for s in after["sections"] if s["key"] == "from_history"][0]
    assert history["topics"], "three completions should produce recommendations"


def test_an_unknown_event_kind_is_ignored_not_fatal(client):
    assert client.post("/api/event", json={"user": "u", "kind": "nonsense"}).status_code == 200


def test_a_broken_event_store_never_breaks_the_feed(client, monkeypatch):
    """Playback and browsing must survive the recommender falling over."""
    class Broken(T.EventStore):
        def __init__(self):
            pass
        def _conn(self):
            raise RuntimeError("disk gone")
    monkeypatch.setattr(appmod, "EVENTS", Broken())
    body = client.get("/api/myfam?user=u1").json()
    most_played = [s for s in body["sections"] if s["key"] == "most_played"][0]
    assert most_played["topics"], "the crowd row should still fall back to the bank"


def test_the_personal_sections_are_not_starved_by_the_generic_ones(store):
    """The bug this ordering exists to prevent.

    Filled in display order, the crowd rows claim the whole bank first - they
    can fall back to anything - and the two rails the listener actually asked
    for arrive empty. The personal rails must choose first.
    """
    play(store, "me", "golf-evolution", kind="complete")
    play(store, "me", "the-trade", kind="complete")
    play(store, "friend", "golf-evolution")
    play(store, "friend", "sleep-science")

    feed = T.build_feed(store, "me", circle=["friend"])
    by_key = {s["key"]: s for s in feed["sections"]}
    assert by_key["from_history"]["topics"], "history section was starved"
    assert by_key["followers"]["topics"], "the friends rail was starved"
    assert "sleep-science" in [t["id"] for t in by_key["followers"]["topics"]]
    # And the generic section still fills, because the bank is big enough.
    assert by_key["most_played"]["topics"]


def test_the_bank_can_fill_every_section_without_repeating(store):
    """Every section must fill from the shared bank without reusing a topic."""
    # `world_trending` is not filled from the bank - it comes from
    # `trending.py` - so the bank only has to cover the other four.
    from_bank = [k for k, _ in T.SECTIONS if k != "world_trending"]
    assert len(T.TOPIC_BANK) >= len(from_bank) * T.SECTION_SIZE


def test_no_section_offers_back_something_already_played(store):
    """The feed hands them the next episode, not the one they finished."""
    for topic_id in ("golf-evolution", "the-trade", "sleep-science"):
        play(store, "me", topic_id, kind="complete")
    play(store, "neighbour", "golf-evolution")
    play(store, "neighbour", "space-race")

    feed = T.build_feed(store, "me")
    shown = {t["id"] for s in feed["sections"] for t in s["topics"]}
    assert not (shown & {"golf-evolution", "the-trade", "sleep-science"})


# --- Go Deeper: the threads episodes left open ----------------------------


def test_a_finished_episode_leaves_its_thread_behind(store):
    store.record(T.Event("u", "complete", "ai-agents", "", ("tech",),
                         thread="why chip supply is so concentrated"))
    threads = store.open_threads("u")
    assert len(threads) == 1
    assert threads[0]["thread"] == "why chip supply is so concentrated"
    assert threads[0]["from_title"] == "Why Everyone Is Talking About AI Agents"


def test_a_thread_they_have_already_asked_about_is_closed(store):
    """It is only a thread while it is still open."""
    store.record(T.Event("u", "complete", "ai-agents", "", ("tech",),
                         thread="why chip supply is so concentrated"))
    store.record(T.Event("u", "search", "", "why chip supply is so concentrated", ()))
    assert store.open_threads("u") == []


def test_only_finished_episodes_leave_threads(store):
    """Starting an episode is not hearing the ending that opened the thread."""
    store.record(T.Event("u", "play", "ai-agents", "", ("tech",), thread="a thread"))
    assert store.open_threads("u") == []


def test_the_same_thread_twice_appears_once(store):
    for _ in range(2):
        store.record(T.Event("u", "complete", "ai-agents", "", ("tech",), thread="one thread"))
    assert len(store.open_threads("u")) == 1


def test_threads_survive_a_restart(tmp_path):
    path = str(tmp_path / "e.db")
    T.EventStore(path).record(
        T.Event("u", "complete", "ai-agents", "", ("tech",), thread="a lasting thread"))
    assert T.EventStore(path).open_threads("u")[0]["thread"] == "a lasting thread"


def test_the_go_deeper_endpoint_serves_them(client):
    client.post("/api/event", json={"user": "u1", "kind": "complete",
                                    "topic_id": "sleep-science",
                                    "thread": "why sleep debt cannot be repaid"})
    body = client.get("/api/godeeper?user=u1").json()["threads"]
    assert [t["thread"] for t in body] == ["why sleep debt cannot be repaid"]


def test_go_deeper_is_empty_not_broken_for_a_new_listener(client):
    assert client.get("/api/godeeper?user=nobody").json()["threads"] == []


# --- the profile summary --------------------------------------------------


def test_the_profile_counts_only_what_actually_happened(store):
    play(store, "u", "ai-agents")
    play(store, "u", "sleep-science", kind="complete")
    store.record(T.Event("u", "search", "", "how heat pumps work", ()))
    body = T.summary(store, "u")
    assert body["played"] == 1 and body["finished"] == 1 and body["searched"] == 1
    assert body["listener"] == "u"


def test_a_new_listener_has_an_empty_profile_not_a_fake_one(store):
    body = T.summary(store, "nobody")
    assert body["played"] == 0 and body["finished"] == 0
    assert body["subjects"] == []
    assert body["since"] == 0.0


def test_a_skipped_subject_is_not_listed_as_something_they_like(store):
    play(store, "u", "golf-evolution", kind="complete")
    play(store, "u", "sleep-science", kind="skip")
    subjects = T.summary(store, "u")["subjects"]
    assert "sports" in subjects
    assert "health" not in subjects, "a skip is evidence against, not for"


def test_the_profile_endpoint_serves_it(client):
    client.post("/api/event", json={"user": "p1", "kind": "complete",
                                    "topic_id": "ai-agents"})
    body = client.get("/api/profile?user=p1").json()
    assert body["finished"] == 1 and "tech" in body["subjects"]


# --- the tag vocabulary ---------------------------------------------------


def test_a_subtag_always_brings_its_facet():
    """Subtags refine a facet; they never take an episode out of one.

    This is what makes the two-level vocabulary additive. A listener who chose
    only "Technology" in the intro must keep matching every tech episode, or
    adding resolution would have silently narrowed their feed.
    """
    tags = T.tags_for_text("who actually makes the chips")
    assert "chips" in tags, "the subtag should be matched"
    assert "tech" in tags, "and must carry its facet with it"
    for topic in T.TOPIC_BANK:
        facets = set(topic.tags) & set(T.TAG_LABELS)
        assert facets, f"{topic.id} has no facet and is unreachable from the intro"
        for tag in topic.tags:
            assert T.facet_of(tag) in facets, (
                f"{topic.id} carries {tag} without its parent facet")


def test_the_bank_is_no_longer_mostly_ties():
    """The vocabulary exists to make one topic rankable against another.

    Before subtags, 28 topics shared 20 distinct tag signatures and a listener
    whose history was one facet got three identical scores broken by topic id
    - alphabetical order wearing a recommender's hat.
    """
    signatures = {t.tags for t in T.TOPIC_BANK}
    assert len(signatures) >= 27, (
        f"only {len(signatures)} distinct signatures for {len(T.TOPIC_BANK)} topics")


def test_a_listener_with_real_history_gets_a_ranking_not_an_alphabet():
    profile = {"tech": 1.0, "chips": 0.8, "ai": 0.6}
    scores = [T._affinity(t, profile) for t in T.TOPIC_BANK]
    positive = [s for s in scores if s > 0]
    assert len(set(round(s, 6) for s in positive)) >= 3, (
        "the ranking still collapses into ties for a listener with history")


def test_listener_facing_tags_stay_in_the_eight_words_they_were_offered():
    """The extra resolution is for the ranker; a screen still says "Health"."""
    folded = T.facets_only(("sleep", "body-science", "health", "ai"))
    assert folded == ["health", "science", "tech"], (
        "body-science parents to science, not to health")
    assert all(f in T.TAG_LABELS for f in folded)


# --- fatigue --------------------------------------------------------------


def test_a_tile_shown_and_never_played_stops_being_pushed(store):
    """The one thing impressions are allowed to do to the ranking."""
    now = time.time()
    for i in range(8):
        store.record_impressions(
            "u", [("from_history", "ai-agents")], at=now - i * 7200)
    store.record(T.Event("u", "complete", "song-breaks-internet", "",
                         T.BANK_BY_ID["song-breaks-internet"].tags, now - 86400))
    profile = T.taste(store.for_user("u"), now)
    damp = T.fatigue(store.impression_occasions("u"),
                     T._played_ids(store.for_user("u")))
    assert damp["ai-agents"] < 1.0

    plain = [t.id for t in T.rank_from_history(profile, set())]
    damped = [t.id for t in T.rank_from_history(profile, set(), damp)]
    assert "ai-agents" in plain, "precondition: it ranks without fatigue"
    # Off the shelf entirely counts as losing ground; SECTION_SIZE is a
    # window, so a tile can be damped past the end of it rather than down it.
    fell = ("ai-agents" not in damped
            or damped.index("ai-agents") > plain.index("ai-agents"))
    assert fell, "a tile ignored eight times should lose ground"


def test_refreshing_the_page_is_not_a_rejection(store):
    """A feed load writes a row per tile; opening myFAM twice is not evidence."""
    now = time.time()
    for i in range(12):
        store.record_impressions("u", [("most_played", "ai-agents")], at=now + i)
    assert store.impression_occasions("u")["ai-agents"] == 1, (
        "impressions inside one bucket must collapse to a single occasion")
    assert T.fatigue(store.impression_occasions("u")) == {}


def test_fatigue_never_buries_a_tile_for_good(store):
    now = time.time()
    for i in range(400):
        store.record_impressions("u", [("most_played", "ai-agents")], at=now - i * 7200)
    damp = T.fatigue(store.impression_occasions("u"))
    assert damp["ai-agents"] >= T.FATIGUE_FLOOR > 0


def test_playing_something_clears_its_fatigue(store):
    """The impressions before a play are the opposite of disinterest."""
    now = time.time()
    for i in range(9):
        store.record_impressions("u", [("from_history", "ai-agents")],
                                 at=now - i * 7200)
    assert "ai-agents" in T.fatigue(store.impression_occasions("u"))
    assert "ai-agents" not in T.fatigue(store.impression_occasions("u"),
                                        played={"ai-agents"})


def test_impressions_still_say_nothing_about_taste(store):
    """Fatigue is per-topic and negative. It must never become a tag score."""
    now = time.time()
    for i in range(20):
        store.record_impressions("u", [("most_played", "ai-agents")], at=now - i * 7200)
    assert T.taste(store.for_user("u"), now) == {}, (
        "being shown a tile taught the feed a preference")
    assert T.IMPRESSION not in T.EVENT_WEIGHT


# --- "View more": one rail, in full ---------------------------------------
#
# The rail shows six of a bank that holds ~28. The screen behind it shows the
# rest of the same ranking, and generates nothing to do it - which is the
# whole answer to "fill a screen without making episodes nobody asked for".


def test_a_section_opens_at_full_length_in_the_rails_own_order(client):
    rail = [t["id"] for s in client.get("/api/myfam").json()["sections"]
            if s["key"] == "most_played" for t in s["topics"]]
    full = client.get("/api/myfam/section?key=most_played").json()
    assert len(full["topics"]) > len(rail), "view more showed no more"
    # Everything the rail offered is still on the screen behind it. Order is
    # not asserted: the screen leads with what is already written.
    assert set(rail) <= {t["id"] for t in full["topics"]}


def test_an_unknown_section_is_a_404_not_an_empty_screen(client):
    assert client.get("/api/myfam/section?key=nonsense").status_code == 404


def test_the_ready_ones_come_first_and_are_counted(client):
    import app as appmod
    import topics as topics_mod

    # Write the script for one bank topic, as another listener, at the length
    # the screen is asking for.
    topic = topics_mod.TOPIC_BANK[3]
    plan = appmod._validated_plan(topic.query, 3)
    appmod.SCRIPT_CACHE.put(appmod._episode_key(plan), ["A sentence."], 600,
                            topic.query, "", 3, "", "", "someone-else")

    body = client.get("/api/myfam/section?key=most_played&minutes=3").json()
    ready = [t for t in body["topics"] if t["cached"]]
    assert [t["id"] for t in ready] == [topic.id]
    assert body["ready"] == 1
    assert body["topics"][0]["id"] == topic.id, "a ready tile was not put first"
    # And at a length nothing was written for, nothing claims to be ready.
    other = client.get("/api/myfam/section?key=most_played&minutes=7").json()
    assert other["ready"] == 0


def test_opening_a_section_costs_no_model_call(client, monkeypatch):
    """It reorders a fixed bank. If this ever needs a generator, the "view
    more" screen has stopped being free and the rail should say so."""
    import app as appmod

    def explode(*args, **kwargs):
        raise AssertionError("a browse screen tried to build a pipeline")

    monkeypatch.setattr(appmod, "_make_pipeline", explode)
    assert client.get("/api/myfam/section?key=from_history").status_code == 200


# --- the page's shape -----------------------------------------------------


def test_trending_is_second_and_explore_new_is_not_a_rail(client):
    """Trending was last, where a row nobody scrolls to is a row nobody
    reads. It is second now, at the owner's direction, in the slot Explore
    New held."""
    import topics as topics_mod

    keys = [k for k, _ in topics_mod.SECTIONS]
    assert keys[1] == "world_trending", keys
    assert "might_like" not in keys, "Explore New is back on myFAM"

    body = client.get("/api/myfam").json()
    assert [s["key"] for s in body["sections"]] == keys


def test_explore_new_is_still_ranked_and_still_reachable(client):
    """Off the page is not gone. `rank_might_like` is the only ranking in FAM
    that offers anything *outside* an established taste, so the ranker, the
    endpoint and the screen all stay - which is what makes putting the rail
    back a one-line change to SECTIONS rather than a rebuild."""
    import topics as topics_mod

    assert "might_like" in topics_mod.FILL_ORDER
    assert "might_like" in topics_mod.UNSHELVED
    body = client.get("/api/explorenew").json()
    assert body["topics"], "Explore New ranks nothing"
    assert body["reason"].strip()
    # And it can still be asked for by name as a full section.
    full = client.get("/api/myfam/section?key=might_like")
    assert full.status_code == 404, \
        "a section that is not on the page should not be openable as one"


# --- the first run's interest catalogue -----------------------------------


def test_the_catalogue_is_offered_and_every_tag_in_it_is_real(client):
    """Interests, not tags. The eight facets are still the only *pickable
    tag* vocabulary - these are named subjects, and the tags ride along."""
    import topics as topics_mod

    body = client.get("/api/preferences").json()
    catalogue = body["catalogue"]
    assert len(catalogue) > 50, f"only {len(catalogue)} interests offered"
    assert len({i["id"] for i in catalogue}) == len(catalogue), "duplicate ids"

    known = set(topics_mod.TAG_LABELS) | set(topics_mod.TAG_PARENT)
    for item in catalogue:
        assert item["label"] and item["icon"], item
        assert item["tags"], f"{item['id']} means nothing to the ranker"
        for tag in item["tags"]:
            assert tag in known, f"{item['id']} carries unknown tag {tag!r}"
        # Every entry reaches a facet, so it can never rank nothing.
        assert any(topics_mod.facet_of(t) in topics_mod.TAG_LABELS
                   for t in item["tags"]), item


def test_the_chips_above_it_are_still_only_the_eight_facets(client):
    """The catalogue being seventy-odd entries must not widen the vocabulary
    the ranker reasons in. If this ever fails, read CLAUDE.md before fixing.

    The picker now *shows* six of the eight, which narrows the screen and not
    the vocabulary - so the thing to assert is that everything it offers is a
    facet, and that every facet is still reachable.
    """
    import topics as topics_mod

    body = client.get("/api/preferences").json()
    shown = [i["id"] for i in body["interests_available"]]
    assert len(shown) == topics_mod.PICKER_SIZE
    assert set(shown) <= set(topics_mod.TAG_LABELS), "the picker invented a tag"
    assert [i["id"] for i in body["interests_all"]] == list(topics_mod.TAG_LABELS)


def test_the_picker_shows_the_six_most_played(client):
    """Not the first six of a dict. `popular_facets` counts what FAM's
    listeners actually play, globally - the only honest signal on a run where
    this listener has no history at all."""
    import topics as topics_mod

    store = topics_mod.EventStore(":memory:")
    culture = [t for t in topics_mod.TOPIC_BANK
               if "culture" in topics_mod.facets_only(t.tags)][0]
    for listener in ("a", "b", "c", "d", "e"):
        store.record(topics_mod.Event(listener, "play", culture.id, "", culture.tags))

    shown, source = topics_mod.popular_facets(store)
    assert source == "played"
    assert shown[0] == "culture", shown
    assert len(shown) == topics_mod.PICKER_SIZE


def test_the_settings_wheel_is_this_listener_rather_than_the_crowd(client):
    """Two wheels, two questions (§100). The first run asks somebody with no
    history, so the honest answer is what everybody plays. Settings is opened
    by somebody who has been using the app."""
    import topics as topics_mod

    store = topics_mod.EventStore(":memory:")
    culture = [t for t in topics_mod.TOPIC_BANK
               if "culture" in topics_mod.facets_only(t.tags)][0]
    for _ in range(4):
        store.record(topics_mod.Event("me", "complete", culture.id, "", culture.tags))
    # Somebody else plays something else, loudly. It must not reach my wheel.
    health = [t for t in topics_mod.TOPIC_BANK
              if "health" in topics_mod.facets_only(t.tags)][0]
    for _ in range(40):
        store.record(topics_mod.Event("them", "play", health.id, "", health.tags))

    mine, source = topics_mod.my_facets(store, "me")
    assert source == "listened"
    assert mine[0] == "culture", mine
    crowd, _ = topics_mod.popular_facets(store)
    assert crowd[0] == "health", "the crowd wheel stopped being the crowd"


def test_a_listener_with_no_plays_falls_back_to_what_they_chose(client):
    import topics as topics_mod

    store = topics_mod.EventStore(":memory:")
    mine, source = topics_mod.my_facets(store, "new", chosen=["science", "money"])
    assert source == "chosen"
    assert mine[:2] == ["science", "money"]


def test_the_settings_wheel_is_always_six_discs(client):
    """A wheel is six or it is a broken wheel. Somebody who has played one
    thing and chosen nothing still gets six, and `source` is what says the
    other five are filler rather than a measurement."""
    import topics as topics_mod

    store = topics_mod.EventStore(":memory:")
    topic = topics_mod.TOPIC_BANK[0]
    store.record(topics_mod.Event("me", "play", topic.id, "", topic.tags))
    mine, source = topics_mod.my_facets(store, "me")
    assert len(mine) == topics_mod.PICKER_SIZE
    assert len(set(mine)) == topics_mod.PICKER_SIZE, "a facet was drawn twice"
    assert source == "listened"


def test_both_wheels_reach_the_interface(client):
    body = client.get("/api/preferences").json()
    for key in ("interests_available", "interests_yours"):
        assert len(body[key]) == 6, key
        assert all(i["short"] and i["label"].startswith(i["short"]) for i in body[key])
    assert body["interests_yours_source"] in ("listened", "chosen", "default")


def test_an_empty_log_says_it_is_showing_a_default_rather_than_a_ranking(client):
    """A declared order and a measurement look identical on screen. On a fresh
    deployment - which is exactly when this screen is shown - it is the former,
    and calling that "most popular" would be inventing a number."""
    import topics as topics_mod

    shown, source = topics_mod.popular_facets(topics_mod.EventStore(":memory:"))
    assert source == "default"
    assert shown == list(topics_mod.PICKER_DEFAULT_ORDER[:topics_mod.PICKER_SIZE])


def test_picking_an_interest_teaches_the_ranker_its_tags(client):
    """The whole point of the catalogue: "Formula 1" is not something the
    eight facets can say, and this is how it reaches the taste model."""
    import topics as topics_mod

    assert client.post("/api/event",
                       json={"kind": "pick", "topic_id": "formula1"}).status_code == 200
    me = client.get("/api/auth/me").json()["user_id"]
    events = topics_mod.EventStore().for_user(me) if False else None
    import app as appmod
    logged = [e for e in appmod.EVENTS.for_user(me) if e.kind == "pick"]
    assert logged, "the pick was not recorded"
    assert set(logged[0].tags) >= set(topics_mod.CATALOGUE_BY_ID["formula1"].tags)


# --- warming what a tap would pay for (PROBLEMS.md §105) ------------------
def test_the_page_schedules_a_warm_and_never_waits_for_one(client, monkeypatch):
    """myFAM is where CLAUDE.md says the wait must be zero, so the page draws
    from what already exists and *schedules* the guessing. A page that waited
    for speculation would have spent the latency the speculation was buying."""
    import prefetch

    scheduled: list = []
    monkeypatch.setattr(prefetch, "schedule_cycle",
                        lambda listener="", minutes=0: scheduled.append(
                            (listener, minutes)) or True)

    assert client.get("/api/myfam?minutes=7").status_code == 200
    assert scheduled, "the browse page warmed nothing at all"
    listener, minutes = scheduled[0]
    assert listener, "a warm has to be attributed to the listener it is for"
    assert minutes == 7, (
        "warmed at the wrong length: a brief is keyed by (query, minutes, "
        "context), so this one is a brief nobody ever looks up"
    )


def test_the_page_is_drawn_from_what_exists_even_if_warming_is_broken(client,
                                                                      monkeypatch):
    """A guess that falls over must never reach a listener who asked for a
    browse page. The same rule the story sweep beside it keeps."""
    import prefetch

    class Broken:
        def due(self, listener="", now=None):
            raise RuntimeError("the prefetcher fell over")

    monkeypatch.setattr(prefetch, "_PREFETCHER", Broken())
    body = client.get("/api/myfam")
    assert body.status_code == 200
    assert body.json()["sections"], "a broken guess emptied the page"
