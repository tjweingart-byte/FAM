"""§695: what a listener chose reaches Made for you, whatever they chose.

The report (a first-week listener, relayed by the owner): "He's a diehard
Boston sports fan and hasn't gotten a single Boston sports episode
recommended for him." "Boston sports" reached the ranker as no tags at all,
so the rail was the "Start here" set - and so did 94 of 208 plausible typed
interests. Pinned here:

* a city's sports are its teams, the one the tree leaves out included;
* a tile that says a chosen subject by name is on subject and scored;
* every chosen subject has a tile of its own, so the first open is theirs;
* four teams playing tonight are four tiles - the variety cap holds
  everything else and never a subject somebody chose; and
* a whole heading ("Sports") names no subject.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories as C  # noqa: E402
import category_seed  # noqa: E402
import stories  # noqa: E402
import topics as T  # noqa: E402


@pytest.fixture()
def log(tmp_path, monkeypatch):
    store = C.CategoryStore(str(tmp_path / "categories.db"))
    monkeypatch.setattr(T, "_CATEGORIES", store)
    C.apply_seed(store)
    T.reset_topic_tags()
    monkeypatch.setattr(T, "world_inventory", lambda *a, **k: ([], []))
    stories.seed([])
    yield T.EventStore(str(tmp_path / "events.db"))
    stories.seed([])
    T.reset_topic_tags()


def story(subject, tags, domain=stories.ATTENTION):
    scope, key, label = stories._geography(())
    return stories.Story(
        subject=subject, title=subject, angle=f"why {subject}",
        query=f"{subject} tonight", domain=domain, source="test",
        tags=tuple(tags), strength=0.8, first_seen=time.time(),
        shelf_life=stories.DOMAIN_SHELF_LIFE[domain], countries=(),
        geo_scope=scope, geo_key=key, geo=label)


def game(home, away):
    return story(f"{home} vs {away}", ("sports",), domain=stories.SPORTS)


BOSTON = [game("Boston Celtics", "New York Knicks"),
          game("Boston Bruins", "Toronto Maple Leafs"),
          game("Boston Red Sox", "New York Yankees"),
          game("New England Patriots", "Buffalo Bills")]
OTHERS = [game("Los Angeles Lakers", "Denver Nuggets"),
          game("Dallas Cowboys", "Philadelphia Eagles"),
          game("Seattle Kraken", "Vegas Golden Knights"),
          story("Fed holds rates", ("money",)),
          story("Taylor Swift announces a tour", ("culture",))]


def rail(log, interests, key="from_history"):
    feed = T.build_feed(log, "me", has_account=True, country="US",
                        interests=interests)
    return feed, [t["title"] for s in feed["sections"] if s["key"] == key
                  for t in s["topics"]]


# --- a city's sports are its teams ----------------------------------------

def test_boston_sports_is_the_four_teams(log):
    teams = category_seed.teams_of("boston")
    assert set(teams) == {"boston celtics", "boston bruins", "boston red sox",
                          "new england patriots"}
    shares = T.interest_shares(["Boston sports"])
    for node in ("boston celtics", "boston bruins", "boston red sox"):
        assert shares[node] == 1.0
    assert shares["sports"] > 0


def test_a_team_seeded_by_nickname_is_found_by_its_city(log):
    assert category_seed.teams_of("cincinnati")["cincinnati bengals"] == "bengals"
    assert "bengals" in T.interest_shares(["Cincinnati teams"])


def test_a_city_alone_is_not_its_teams(log):
    assert T._teams_named("Boston") == {}
    assert T._teams_named("york sports") == {}


def test_a_team_the_tree_leaves_out_is_followed_by_name(log):
    assert "new england patriots" in category_seed.LEFT_TO_GROW
    assert T.followed_names(["New England Patriots"]) == ("new england patriots",)
    assert T.category_tree().get("new england patriots") is None


# --- the reported case ----------------------------------------------------

def test_every_boston_game_is_on_the_rail(log):
    stories.seed(BOSTON + OTHERS)
    feed, titles = rail(log, ("Boston sports",))
    assert feed["taste_source"] == "taste"
    assert set(titles) == {s.title for s in BOSTON}


def test_with_no_game_on_the_first_open_is_still_theirs(log):
    feed, titles = rail(log, ("Boston sports",))
    assert feed["taste_source"] == "taste"
    assert titles == [f"{team}: What's New This Week" for team in
                      ("Boston Celtics", "Boston Red Sox", "Boston Bruins",
                       "New England Patriots")]


# --- every topic, not only that one ---------------------------------------

@pytest.mark.parametrize("typed", ["Taylor Swift", "Tesla", "Bitcoin",
                                   "Arsenal", "China", "running"])
def test_an_interest_the_vocabulary_cannot_place_still_counts(log, typed):
    feed, titles = rail(log, (typed,))
    assert feed["taste_source"] == "taste"
    assert titles[0].lower() == f"{typed.lower()}: what's new this week"


def test_a_story_naming_a_chosen_subject_is_offered_in_its_place(log):
    stories.seed(OTHERS)
    _feed, titles = rail(log, ("Taylor Swift",))
    assert titles[0] == "Taylor Swift announces a tour"
    assert "Taylor Swift: What's New This Week" not in titles


def test_a_name_matches_whole_words_in_order(log):
    familiar = T.FamiliarWords().naming(("boston celtics",))
    hit = T.topics_from_stories([game("Boston Celtics", "New York Knicks")])[0]
    miss = T.topics_from_stories([game("Celtic", "Rangers")])[0]
    assert T.names_followed(hit, familiar)
    assert not T.names_followed(miss, familiar)


def test_a_whole_heading_names_no_subject(log):
    assert T.chosen_subjects(["sports", "tech"]) == []
    assert T.interest_topics(["sports"]) == []
    assert T.followed_names(["music"]) == ()


def test_interests_are_dealt_in_turn(log):
    tiles = T.interest_topics(["Boston sports", "Taylor Swift"])
    assert [t.title.split(":")[0] for t in tiles[:3]] == [
        "Boston Celtics", "Taylor Swift", "Boston Red Sox"]


def test_a_composed_tile_claims_nothing(log):
    for tile in T.interest_topics(["Boston sports", "Taylor Swift", "ai"]):
        assert tile.id.startswith(T.INTEREST_TILE_PREFIX)
        assert not any(ch.isdigit() for ch in tile.title + tile.subtitle)
        assert T.answer_moves(tile)


# --- variety still holds for what nobody chose ----------------------------

def test_one_followed_name_is_capped(log):
    stories.seed([story(f"NFL story number {w}", ("sports",))
                  for w in ("one", "two", "three", "four")])
    _feed, titles = rail(log, ("nfl",))
    assert sum("NFL story" in t for t in titles) <= T.MAX_PER_FACET


def test_once_they_listen_half_the_rail_is_the_rankings(log):
    now = time.time()
    tags = T.tags_for_text("how are chips made")
    log.record(T.Event("me", "complete", "", "how are chips made", tags,
                       at=now - 500))
    _feed, titles = rail(log, ("Boston sports",))
    assert sum("What's New This Week" in t for t in titles[:T.INTEREST_LEAD]) \
        == T.INTEREST_LEAD
    assert len(titles) == T.SECTION_SIZE


def test_view_more_leads_with_every_chosen_subject(log):
    section = T.build_section(log, "me", "from_history", has_account=True,
                              country="US", interests=("Boston sports",))
    titles = [t["title"] for t in section["topics"]]
    assert all("What's New This Week" in t for t in titles[:4])
