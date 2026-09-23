"""Where a listener says they are, and the two places it is allowed to matter.

Location is the first thing FAM has ever stored about somebody that is not a
statement about *listening*, which makes the boundaries worth pinning
precisely:

* It changes **which question is offered** and never **how an episode is
  written**. A listener's city on `EpisodePlan` would land in
  `pipeline.key_for`, every listener would get a private script cache, and
  nothing would fail - the app would keep working and quietly cost several
  times more. That is the one test here that is about cost rather than
  behaviour, and it is the most important one.
* It **boosts and never filters**. A location filter empties a rail for
  somebody in a small town, which is the failure `BROAD_MATCH_PENALTY`
  already avoids by damping.
* **Country does not rank.** Boosting every story about the United States for
  every listener in the United States is a different global sort order, not
  personalisation.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import pipeline as pipeline_mod  # noqa: E402
import preferences as P  # noqa: E402
import social as S  # noqa: E402
import startup as startup_mod  # noqa: E402
import topics as T  # noqa: E402


@pytest.fixture
def prefs(tmp_path):
    return P.PreferenceStore(str(tmp_path / "prefs.db"))


@pytest.fixture
def store(tmp_path):
    return T.EventStore(str(tmp_path / "myfam.db"))


def _story(topic_id: str, title: str, query: str, tags, freshness=0.8) -> T.Topic:
    """A live story tile. `freshness` is what makes one a story rather than a
    bank topic, here as everywhere else in the ranker."""
    return T.Topic(topic_id, title, "", query, tags, "world",
                   angle="", source="test", freshness=freshness)


# --- storage ---------------------------------------------------------------


def test_a_location_round_trips(prefs):
    prefs.save("u1", city="Cincinnati", region="Ohio", country="United States")
    assert prefs.get("u1").location.label == "Cincinnati, Ohio, United States"


def test_one_part_can_be_corrected_without_resending_the_rest(prefs):
    """The Edit profile screen sends a changed city and nothing else."""
    prefs.save("u1", city="Cincinnati", region="Ohio", country="United States")
    prefs.save("u1", city="Columbus")
    place = prefs.get("u1").location
    assert (place.city, place.region) == ("Columbus", "Ohio")


def test_a_location_does_not_disturb_the_other_settings(prefs):
    prefs.save("u1", interests=["tech"], topics=["rocketry, but the engines"])
    prefs.save("u1", city="Berlin")
    stored = prefs.get("u1")
    assert stored.interests == ("tech",)
    assert stored.topics == ("rocketry, but the engines",)


def test_no_location_is_a_perfectly_good_answer(prefs):
    prefs.save("u1", interests=["tech"])
    assert not prefs.get("u1").location
    assert prefs.get("u1").location.words == frozenset()


def test_a_place_name_is_not_validated_against_a_list(prefs):
    """There is no catalogue of the world's towns that is both complete and
    short enough to ship, and a field that refuses somebody's home town is
    worse than one that accepts a typo."""
    prefs.save("u1", city="Llanfairpwllgwyngyll", region="Ynys Môn",
               country="Cymru")
    assert prefs.get("u1").location.city == "Llanfairpwllgwyngyll"


def test_a_place_is_bounded_even_though_it_is_free_text(prefs):
    prefs.save("u1", city="x" * 400)
    assert len(prefs.get("u1").location.city) == P.MAX_PLACE


# --- what the ranker is allowed to read ------------------------------------


def test_the_country_is_stored_and_never_ranked():
    """Boosting every story about the United States for every listener in the
    United States is a different global sort order wearing personalisation's
    name."""
    place = P.Location("", "", "United States")
    assert place.country == "United States"
    assert place.words == frozenset()


def test_a_stopword_in_a_city_name_is_not_a_match_word():
    """A listener whose city made every tile with "new" in it look local
    would have a worse feed than one with no location at all."""
    assert P.Location("New York", "New York").words == frozenset({"york"})


# --- the boost -------------------------------------------------------------


def test_a_local_story_outranks_a_comparable_one_elsewhere():
    profile = {"sports": 1.0}
    local = _story("st-1", "Cincinnati stadium deal", "the Cincinnati stadium deal",
                   ("sports",))
    away = _story("st-2", "Glasgow stadium deal", "the Glasgow stadium deal",
                  ("sports",))
    ranked = T.rank_from_history(profile, set(), candidates=[away, local],
                                 local=frozenset({"cincinnati"}))
    assert [t.id for t in ranked] == ["st-1", "st-2"]


def test_the_boost_never_becomes_a_filter():
    """A listener in a small town must still get a full rail."""
    away = [_story(f"st-{i}", f"Story {i}", f"story {i} about markets", ("money",))
            for i in range(5)]
    ranked = T.rank_from_history({"money": 1.0}, set(), candidates=away,
                                 local=frozenset({"cincinnati"}))
    # A full rail is `SECTION_SIZE` (four since §133), from five candidates.
    assert len(ranked) == T.SECTION_SIZE


def test_a_location_cannot_rescue_a_tile_from_the_floor():
    """"It is local" is a tie-breaker, not a subject somebody asked for."""
    tile = _story("st-1", "Cincinnati zoning", "Cincinnati zoning rules",
                  ("culture",))
    ranked = T.rank_from_history({"sports": 1.0}, set(), candidates=[tile],
                                 local=frozenset({"cincinnati"}))
    assert ranked == []


def test_the_bank_is_never_local():
    """"How Food Gets to a City" is not local news to somebody in Kansas
    City, and boosting it for them would be the keyword sweep making a claim
    the tile does not support."""
    bank = T.BANK_BY_ID["food-supply"]
    assert bank.freshness == 0
    assert not T._is_local(bank, frozenset({"city", "kansas"}))


def test_living_somewhere_answers_the_broad_match_question(store):
    """The free half, and the one that costs nothing: a story about their own
    town stops being damped for being a subject they have never typed into a
    search box, because `build_feed` folds their place into `familiar`."""
    tile = _story("st-1", "Cincinnati transit vote", "the Cincinnati transit vote",
                  ("world",))
    assert T._is_broad_match(tile, {"world": 1.0}), "only a facet matches"
    assert not T._subject_is_familiar(tile, frozenset({"golf", "nvidia"}))
    assert T._subject_is_familiar(tile, frozenset({"cincinnati"}))


# --- the cold start --------------------------------------------------------


def test_a_cold_listener_with_a_location_is_offered_their_own_town(store):
    feed = T.build_feed(store, "newbie", place=["cincinnati", "ohio"],
                        place_name="Cincinnati, Ohio")
    made_for_you = feed["sections"][0]
    assert feed["taste_source"] == "startup"
    ids = [t["id"] for t in made_for_you["topics"]]
    assert startup_mod.LOCAL_ID in ids
    local = [t for t in made_for_you["topics"] if t["id"] == startup_mod.LOCAL_ID][0]
    assert "Cincinnati, Ohio" in local["title"]
    assert "Cincinnati, Ohio" in local["query"]


def test_a_cold_listener_with_no_location_gets_the_eight(store):
    feed = T.build_feed(store, "newbie")
    ids = [t["id"] for t in feed["sections"][0]["topics"]]
    assert startup_mod.LOCAL_ID not in ids, (
        '"What Changed in " is the kind of half-rendered string that ships'
    )


def test_the_local_tile_resolves_its_tags_months_later():
    """A play on a startup tile is the first real thing the ranker learns.
    An id it cannot resolve falls through to a keyword sweep of the question,
    which matches nothing and teaches it nothing."""
    assert T.tags_for_id(startup_mod.LOCAL_ID) == ("world",)


def test_the_local_tile_is_damped_like_every_other(store):
    """A tile that leads unconditionally leads forever, and somebody shown
    local news six times without tapping it has told us something."""
    local = T.local_startup_topic("Cincinnati, Ohio")
    prior, _src = T.startup_profile(store)
    hot = T.rank_startup(prior, set(), limit=6, local_topic=local)
    cold = T.rank_startup(prior, set(), limit=6, local_topic=local,
                          damp={startup_mod.LOCAL_ID: T.FATIGUE_FLOOR})
    assert hot[0].id == startup_mod.LOCAL_ID
    assert cold[0].id != startup_mod.LOCAL_ID


# --- the line that must not be crossed -------------------------------------


def test_a_location_is_nowhere_near_the_cache_key():
    """The one that matters. A listener's location in `key_for` gives every
    listener a private script cache, and *nothing fails* - the app keeps
    working and quietly costs several times more.

    Reads the function's own source, the same guard `scripts.author` has.
    """
    import inspect
    source = inspect.getsource(pipeline_mod.key_for)
    source += inspect.getsource(pipeline_mod.bucket_for)
    for word in ("city", "region", "country", "location", "place"):
        assert word not in source.lower(), (
            f"{word!r} reached the cache key; the shared-cost design the whole "
            "app rests on is gone and no test but this one would have said so"
        )


def test_an_episode_plan_holds_no_location():
    """The layer above the key. A field here is one refactor from the key."""
    import script_generator
    fields = set(script_generator.EpisodePlan.__dataclass_fields__)
    assert not fields & {"city", "region", "country", "location", "place"}


# --- the API ---------------------------------------------------------------


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "EVENTS", T.EventStore(str(tmp_path / "e.db")))
    monkeypatch.setattr(appmod, "SOCIAL", S.SocialStore(str(tmp_path / "s.db")))
    monkeypatch.setattr(appmod, "PREFS", P.PreferenceStore(str(tmp_path / "p.db")))
    return TestClient(appmod.app)


def test_a_location_is_saved_and_read_back(client, monkeypatch):
    monkeypatch.setattr(appmod, "_require_account", lambda request: "acct-1")
    ok = client.post("/api/preferences",
                     json={"city": "Cincinnati", "region": "Ohio",
                           "country": "United States"})
    assert ok.status_code == 200
    assert ok.json()["location"]["label"] == "Cincinnati, Ohio, United States"


def test_an_oversized_place_is_refused_before_the_database(client, monkeypatch):
    monkeypatch.setattr(appmod, "_require_account", lambda request: "acct-1")
    r = client.post("/api/preferences", json={"city": "x" * (P.MAX_PLACE + 50)})
    assert r.status_code == 422


def test_a_location_is_never_taken_from_the_query_string():
    """Unlike an interest, which is eight known words from a closed
    vocabulary used for one response and thrown away. Free text off the
    request would let a caller say "rank this feed as though I were in
    Cincinnati", which is a request no interface in this app makes.

    Checked on the signature rather than by grepping the body, because the
    body is allowed to *explain* the difference and a grep for "query" finds
    the explanation.
    """
    import inspect
    params = inspect.signature(appmod._place_for).parameters
    assert list(params) == ["request"]
    assert list(inspect.signature(appmod._interests_for).parameters) == [
        "request", "given"], (
        "the comparison this test is making - if _interests_for stops taking "
        "a hint, the distinction being drawn here no longer exists"
    )
