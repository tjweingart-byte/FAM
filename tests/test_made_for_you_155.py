"""§155: Made for you offers subjects, not fields.

"If I have listened to an episode about football in the past, I should not
be seeing a random division II college football game on my made for you
page." The 20/09 packet reported the same thing, and its fix only damped
such a story - `test_relevance_and_profile_interests.py` asserts it no longer
*leads* the rail, and it went on being offered second. Three leaks, each of
which alone let a fixture through:

* a live story off this listener's subject was cut to 0.3 and still cleared
  the floor, then the floor's top-up could put it back;
* the seed's `football` node sits straight under `sports`, so the word made
  every game "specific" to anybody who had typed it;
* "football" in their own history made every football tile "familiar".
"""
from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories as C  # noqa: E402
import stories  # noqa: E402
import topics as T  # noqa: E402

EAGLES = "who won the eagles vs cowboys football game"


@pytest.fixture()
def tree(tmp_path, monkeypatch):
    store = C.CategoryStore(str(tmp_path / "categories.db"))
    monkeypatch.setattr(T, "_CATEGORIES", store)
    C.apply_seed(store)
    T.reset_topic_tags()
    yield store
    T.reset_topic_tags()


@pytest.fixture()
def log(tmp_path, tree, monkeypatch):
    # Trending chooses first and alone (§134), so on a pool this small it
    # would take every story before Made for you saw one. These tests are
    # about what Made for you does with a story it is offered.
    monkeypatch.setattr(T, "world_inventory", lambda *a, **k: ([], []))
    stories.seed([])
    yield T.EventStore(str(tmp_path / "events.db"))
    stories.seed([])


def story(subject, query, tags=("sports",), strength=0.8):
    return stories.Story(
        subject=subject, title=subject.title(), angle=f"why {subject}",
        query=query, domain=stories.ATTENTION, source="api-sports",
        tags=tuple(tags), strength=strength, first_seen=time.time(),
        shelf_life=stories.DOMAIN_SHELF_LIFE[stories.ATTENTION])


def listened(log, text):
    now = time.time()
    tags = T.tags_for_text(text)
    log.record(T.Event("me", "search", "", text, tags, at=now - 500))
    log.record(T.Event("me", "complete", "", text, tags, at=now - 400))


def made_for_you(log):
    feed = T.build_feed(log, "me", has_account=True)   # the real floors
    return [t["title"] for s in feed["sections"] if s["key"] == "from_history"
            for t in s["topics"]]


def test_one_eagles_game_does_not_buy_every_football_fixture(log):
    """The reported case, end to end, with the minimum rail on."""
    listened(log, EAGLES)
    stories.seed([
        story("slippery rock vs shepherd",
              "slippery rock vs shepherd division ii college football game"),
        story("wofford vs mercer", "wofford vs mercer football game"),
        story("the eagles injury report", "eagles injury report this week"),
    ])
    made = made_for_you(log)
    assert "Slippery Rock Vs Shepherd" not in made
    assert "Wofford Vs Mercer" not in made
    assert made[0] == "The Eagles Injury Report", made
    assert len(made) == T.SECTION_SIZE, "the floor still fills the rail"


def test_the_top_up_never_puts_back_a_story_the_ranking_turned_down(log):
    listened(log, EAGLES)
    stories.seed([story("wofford vs mercer", "wofford vs mercer football game")])
    made = made_for_you(log)
    assert "Wofford Vs Mercer" not in made
    assert made, "the evergreen inventory fills it instead"


def test_a_follower_of_the_subject_still_gets_the_story(log):
    """Not a rule against sport: somebody who follows college football is
    offered a college football story."""
    listened(log, "what happened in college football this weekend")
    listened(log, "college football playoff rankings explained")
    stories.seed([story("ohio state vs michigan",
                        "ohio state vs michigan college football game")])
    assert "Ohio State Vs Michigan" in made_for_you(log)


def test_a_field_word_is_not_familiarity(tree):
    tile = T.Topic("st-x", "Wofford vs Mercer", "", "wofford vs mercer football game",
                   ("sports",), "sports", freshness=0.5)
    assert not T._subject_is_familiar(tile, frozenset({"football", "game"}))
    assert T._subject_is_familiar(tile, frozenset({"wofford"}))


def test_a_sport_is_a_field_and_a_league_is_a_subject(tree):
    assert tree.depth_of("football") == 1
    assert not T._names_a_subject("football")
    assert not T._names_a_subject("american football")
    assert T._names_a_subject("college football")
    assert not T._names_a_subject("sports")
    # And `tag_weight` still counts the sport as narrower than the heading:
    # how much a match is worth and whether it names a subject are two
    # different questions.
    assert T.tag_weight("football") > T.tag_weight("sports")


def test_a_place_still_answers_it(tree):
    """§121's free half survives: living somewhere is having been near it."""
    tile = T.Topic("st-y", "Cincinnati council vote", "",
                   "what the cincinnati council voted on", ("world",), "world",
                   freshness=0.5)
    assert not T._off_subject(tile, {"world": 1.0}, frozenset({"cincinnati"}), {})
    assert T._off_subject(tile, {"world": 1.0}, frozenset(), {})


def test_only_a_near_paraphrase_counts_as_semantic_nearness(tree):
    tile = T.Topic("st-z", "Wofford vs Mercer", "", "wofford vs mercer football game",
                   ("sports",), "sports", freshness=0.5)
    loose = T.taste_vectors.SEMANTIC_WEIGHT * 0.2   # cosine ~0.44: same sport
    assert T._off_subject(tile, {"sports": 1.0}, frozenset(), {"st-z": loose})
    assert not T._off_subject(tile, {"sports": 1.0}, frozenset(),
                              {"st-z": T._semantic_near()})


def test_the_bank_is_never_off_subject(tree):
    """Broad on purpose; the rule is for live stories only."""
    for topic in T.TOPIC_BANK:
        assert not T._off_subject(topic, {"sports": 1.0}, frozenset(), {})
