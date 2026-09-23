"""The 22/09 packet (PROBLEMS.md §134): myFAM rails, Trending, caching,
DailyFAM playlists and Explore's counts.

Seven asks, and each test here names the behaviour a listener would notice
rather than the code that produces it:

1. Trending is the world's news - popularity, and the listener's country,
   and nothing else of theirs.
2. Exactly four tiles on every myFAM rail; the two crowd rails never invent
   one to get there.
3. Cached episodes are kept and shared so RunPod is called as little as
   possible.
4. "Different picks" is gone.
5. A DailyFAM playlist plays through in order and then stops, with a skip.
6. Explore replays other people's cached episodes only, with honest counts.
7. Trending never shows the bank or the startup set.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dataclasses  # noqa: E402

import app as appmod  # noqa: E402
import cache as cache_mod  # noqa: E402
import gdelt  # noqa: E402
import social as social_mod  # noqa: E402
import stories  # noqa: E402
import topics as T  # noqa: E402
from cache import MemoryScriptCache, SqliteScriptCache  # noqa: E402
from config import settings  # noqa: E402
from pipeline import PodcastPipeline  # noqa: E402
from script_generator import plan_episode  # noqa: E402

from test_audio_cache import CountingVoice, play  # noqa: E402
from test_pipeline import FakeGenerator  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
GENERIC = {t.id for t in T.TOPIC_BANK} | {t.id for t in T.STARTUP_TOPICS}


@pytest.fixture()
def store(tmp_path):
    stories.seed([])
    yield T.EventStore(str(tmp_path / "events.db"))
    stories.seed([])


def story(subject, tags, strength=1.0, countries=(), domain=stories.ATTENTION):
    return stories.Story(
        subject=subject, title=subject.title(),
        angle=f"why {subject} is the story",
        query=f"what is actually driving {subject} right now",
        domain=domain, source="test feed", tags=tuple(tags),
        strength=strength, first_seen=time.time(),
        shelf_life=stories.DOMAIN_SHELF_LIFE[domain],
        countries=tuple(countries))


def finished(store, user, topic_id, text, tags):
    store.record(T.Event(user, "complete", topic_id, text, tuple(tags),
                         at=time.time() - 100))


def rail(feed, key):
    return [s for s in feed["sections"] if s["key"] == key][0]


def ids(section):
    return [t["id"] for t in section["topics"]]


A_BUSY_DAY = [("the chip export rules", ("tech", "chips"), 1.0),
              ("the fed decision", ("money", "macro"), 0.95),
              ("the port strike", ("world",), 0.9),
              ("the playoff race", ("sports",), 0.85),
              ("the film festival", ("culture",), 0.8),
              ("the rover landing", ("science",), 0.75),
              ("the ai lawsuit", ("tech", "ai"), 0.7)]


def seed_busy_day(countries=None):
    countries = countries or {}
    stories.seed([story(s, tags, strength, countries.get(s, ()))
                  for s, tags, strength in A_BUSY_DAY])


# --------------------------------------------------------------------------
# 1 & 7. Trending is the world's, and never FAM's own inventory
# --------------------------------------------------------------------------
def test_trending_is_the_same_for_two_listeners_with_opposite_histories(store):
    """"A user's interests or past listens should not affect the content of
    the trending section." Two people who have played nothing in common, one
    of whom has even played a story's own tile, see one Trending row."""
    seed_busy_day()
    finished(store, "sporty", "golf-evolution", "golf", ("sports",))
    finished(store, "techy", "chip-supply", "chips", ("tech", "chips"))
    chip = stories.story_id("the chip export rules")
    store.record(T.Event("techy", "complete", chip, "chips", ("tech",),
                         at=time.time() - 50))

    a = ids(rail(T.build_feed(store, "sporty", interests=("sports",)),
                 "world_trending"))
    b = ids(rail(T.build_feed(store, "techy", interests=("tech",)),
                 "world_trending"))
    assert a == b, "Trending moved with the listener"
    assert chip in b, "a story they had played was taken off the world row"


def test_trending_takes_the_loudest_stories_with_one_per_subject(store):
    seed_busy_day()
    world = rail(T.build_feed(store, "me"), "world_trending")["topics"]
    assert len(world) == T.SECTION_SIZE
    facets = [T.facet_of(t["tags"][0]) for t in world]
    assert len(set(facets)) == len(facets), f"two tiles on one subject: {facets}"
    # The loudest on each subject, in order - the ai lawsuit is the quietest
    # tech story, so the cap passes it over for something else entirely.
    assert world[0]["id"] == stories.story_id("the chip export rules")
    assert stories.story_id("the ai lawsuit") not in [t["id"] for t in world]


def test_the_variety_cap_never_empties_a_one_subject_day():
    tiles = T.topics_from_stories(
        [story(f"match {n}", ("sports",), 1.0 - n / 10) for n in range(6)])
    assert len(T.rank_world(tiles)) == T.SECTION_SIZE


def test_the_listeners_country_lifts_a_story_running_there(store):
    """The one listener input the row may take, and it reorders - it never
    removes a story that is trending everywhere else."""
    seed_busy_day({"the rover landing": (("united kingdom", 0.9),)})
    plain = ids(rail(T.build_feed(store, "me"), "world_trending"))
    uk = ids(rail(T.build_feed(store, "me", country="UK"), "world_trending"))
    rover = stories.story_id("the rover landing")
    assert rover not in plain, "the quietest story made the row with no boost"
    assert rover in uk, "a story running in their country did not move up"
    # And a country with no coverage data changes nothing at all.
    assert ids(rail(T.build_feed(store, "me", country="Peru"),
                    "world_trending")) == plain


def test_trending_never_shows_the_bank_or_the_startup_set(store):
    """The owner's word for them is "dummy data". With no live source the
    row is empty and says why; with a thin pool it is short, never padded."""
    for has_account in (False, True):
        feed = T.build_feed(store, "me", has_account=has_account)
        row = rail(feed, "world_trending")
        assert row["topics"] == []
        assert row["empty_reason"], "an empty Trending row gave no reason"
        section = T.build_section(store, "me", "world_trending",
                                  has_account=has_account)
        assert section["topics"] == []

    stories.seed([story("the port strike", ("world",))])
    feed = T.build_feed(store, "me")
    assert ids(rail(feed, "world_trending")) == [stories.story_id("the port strike")]
    assert not GENERIC & set(ids(rail(feed, "world_trending")))


def test_view_more_on_trending_is_the_same_ranking_at_length(store):
    seed_busy_day()
    row = ids(rail(T.build_feed(store, "me"), "world_trending"))
    full = ids(T.build_section(store, "me", "world_trending"))
    assert full[:len(row)] == row
    assert len(full) == len(A_BUSY_DAY)
    assert not GENERIC & set(full)


def test_the_rail_fallback_for_trending_holds_live_stories_only():
    live = T.topics_from_stories([story("the port strike", ("world",))])
    assert T._rail_fallback("world_trending", {"sports": 1.0}, live, [], []) == live


# --------------------------------------------------------------------------
# 2. Exactly four on every rail
# --------------------------------------------------------------------------
def test_every_rail_shows_at_most_four_and_the_choosing_ones_exactly_four(store):
    seed_busy_day()
    finished(store, "me", "chip-supply", "chips", ("tech", "chips"))
    for has_account in (False, True):
        feed = T.build_feed(store, "me", has_account=has_account)
        for section in feed["sections"]:
            assert len(section["topics"]) <= 4, section["key"]
        for key in ("from_history", "world_trending", "missed"):
            assert len(rail(feed, key)["topics"]) == 4, key


def test_a_brand_new_listener_still_gets_four_on_the_rails_that_choose(store):
    feed = T.build_feed(store, "")
    assert len(rail(feed, "from_history")["topics"]) == 4
    assert len(rail(feed, "missed")["topics"]) == 4


def test_the_crowd_rows_never_invent_a_play_and_show_four_once_they_can(store):
    """"If there is not enough information in either of those categories, it
    should not make up episodes, but as soon as there are at least four tiles
    in those categories they should also have exactly four.\""""
    feed = T.build_feed(store, "me")
    assert rail(feed, "most_played")["topics"] == []
    assert rail(feed, "followers")["topics"] == []

    for n, topic in enumerate(T.TOPIC_BANK[:2]):
        store.record(T.Event(f"other{n}", "play", topic.id, topic.query,
                             topic.tags, at=time.time() - 60))
    shown = ids(rail(T.build_feed(store, "me"), "most_played"))
    assert len(shown) == 2, "the row was padded with tiles nobody played"

    for n, topic in enumerate(T.TOPIC_BANK[2:9]):
        store.record(T.Event(f"more{n}", "play", topic.id, topic.query,
                             topic.tags, at=time.time() - 60))
    assert len(ids(rail(T.build_feed(store, "me"), "most_played"))) == 4


def test_view_more_holds_what_the_rail_does_not_show(store):
    seed_busy_day()
    finished(store, "me", "chip-supply", "chips", ("tech", "chips"))
    section = T.build_section(store, "me", "from_history")
    assert len(section["topics"]) > 4


def test_the_minimum_table_is_only_the_rails_that_choose():
    assert set(T.RAIL_MINIMUM) == {"from_history", "missed"}
    assert all(v == T.SECTION_SIZE == 4 for v in T.RAIL_MINIMUM.values())


def test_explore_new_is_a_screen_and_keeps_its_own_size():
    assert T.EXPLORE_NEW_SIZE == 6
    assert len(T.rank_might_like({}, set())) == 6


# --------------------------------------------------------------------------
# Where a story's coverage comes from
# --------------------------------------------------------------------------
def test_country_shares_are_a_measurement_and_never_an_even_guess():
    assert stories.country_shares(["", ""]) == ()
    shares = dict(stories.country_shares(
        ["United States", "United States", "United Kingdom", "US"]))
    assert shares == {"united states": 0.75, "united kingdom": 0.25}


def test_the_ways_of_writing_a_country_are_one_country():
    for name in ("US", "usa", "United States", "the United States", "America"):
        assert stories.normalise_country(name) == "united states"
    assert stories.normalise_country("") == ""


def test_gdelt_articles_carry_the_publishers_country():
    rows = gdelt.parse_articles({"articles": [
        {"url": "https://a.example/x", "title": "T", "seendate": "20260922T000000Z",
         "sourcecountry": "United Kingdom"},
        {"url": "https://b.example/y", "title": "U"}]})
    assert [r.country for r in rows] == ["United Kingdom", ""]


def test_a_signals_countries_reach_the_story_and_survive_a_resighting():
    signal = stories.Signal(subject="the port strike", observation="busy",
                            countries=(("germany", 1.0),))
    made = stories.template(signal)
    assert made.countries == (("germany", 1.0),)
    assert made.share_in("Germany") == 1.0 and made.share_in("France") == 0.0


# --------------------------------------------------------------------------
# 3. Keeping cached episodes, and their audio, for everybody
# --------------------------------------------------------------------------
def test_an_evergreen_episode_that_keeps_being_played_keeps_its_life(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "settings", dataclasses.replace(
        settings, cache_ttl_seconds=1000, cache_max_age_seconds=5000))
    cache = SqliteScriptCache(str(tmp_path / "s.db"))
    cache.put("k", ["One."], 1000, "why the sky is blue", minutes=2, slide=True)
    db = sqlite3.connect(cache.path)
    db.execute("UPDATE scripts SET expires = ?", (time.time() + 10,))
    db.commit()
    cache.record_play("k")
    expires = db.execute("SELECT expires FROM scripts").fetchone()[0]
    assert expires > time.time() + 900, "a play did not keep an evergreen entry"
    # ...but never past the ceiling from when it was written.
    db.execute("UPDATE scripts SET created = ?, expires = ?",
               (time.time() - 4990, time.time() + 5))
    db.commit()
    cache.record_play("k")
    expires = db.execute("SELECT expires, created FROM scripts").fetchone()
    assert expires[0] <= expires[1] + 5000 + 1


def test_a_volatile_episode_never_slides(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "settings", dataclasses.replace(
        settings, cache_ttl_seconds=1000))
    cache = SqliteScriptCache(str(tmp_path / "s.db"))
    cache.put("k", ["One."], 900 - 800, "latest score tonight", minutes=2, slide=True)
    before = sqlite3.connect(cache.path).execute(
        "SELECT expires FROM scripts").fetchone()[0]
    cache.record_play("k")
    after = sqlite3.connect(cache.path).execute(
        "SELECT expires FROM scripts").fetchone()[0]
    assert after == before


def test_a_look_is_not_a_play_and_keeps_nothing_alive(tmp_path, monkeypatch):
    """The pacing probe, a prefetch check and the GPU-wake hint all read the
    cache; none of them is anybody listening, so none may slide an entry."""
    monkeypatch.setattr(cache_mod, "settings", dataclasses.replace(
        settings, cache_ttl_seconds=1000, cache_max_age_seconds=5000))
    cache = SqliteScriptCache(str(tmp_path / "s.db"))
    cache.put("k", ["One."], 1000, "why the sky is blue", minutes=2, slide=True)
    db = sqlite3.connect(cache.path)
    db.execute("UPDATE scripts SET expires = ?", (time.time() + 10,))
    db.commit()
    for _ in range(3):
        assert cache.get("k")
    assert db.execute("SELECT expires FROM scripts").fetchone()[0] < time.time() + 11


def test_an_entry_not_marked_free_to_slide_never_slides(tmp_path, monkeypatch):
    """Prefetch, tools and anything written about a window of time pass no
    `slide`, and a play must not keep "this week" alive for a month."""
    monkeypatch.setattr(cache_mod, "settings", dataclasses.replace(
        settings, cache_ttl_seconds=1000, cache_max_age_seconds=5000))
    cache = SqliteScriptCache(str(tmp_path / "s.db"))
    cache.put("k", ["One."], 1000, "what changed this week", minutes=2)
    db = sqlite3.connect(cache.path)
    db.execute("UPDATE scripts SET expires = ?", (time.time() + 10,))
    db.commit()
    cache.record_play("k")
    assert db.execute("SELECT expires FROM scripts").fetchone()[0] < time.time() + 11


@pytest.mark.parametrize("recency, outcome, status, slides", [
    (0, False, "", True), (3, False, "", False), (0, True, "", False),
    (0, False, "scheduled", False), (0, False, "final", True)])
def test_the_pipeline_marks_only_timeless_episodes_free_to_slide(
        recency, outcome, status, slides):
    seen = {}

    class Spy(MemoryScriptCache):
        def put(self, *a, **kw):
            seen.update(kw)
            return super().put(*a, **kw)

    class Gen(FakeGenerator):
        async def stream_sentences(self, plan, notes=None):
            if notes is not None:
                notes.recency_days, notes.outcome_dependent = recency, outcome
                notes.live_status = status
            async for s in super().stream_sentences(plan, notes):
                yield s

    play(PodcastPipeline(generator=Gen(), engine=CountingVoice(), cache=Spy()),
         plan_episode("why the sky is blue", 1))
    assert bool(seen.get("slide")) is slides


def test_sliding_off_is_the_old_fixed_lifetime(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "settings", dataclasses.replace(
        settings, cache_max_age_seconds=0))
    cache = SqliteScriptCache(str(tmp_path / "s.db"))
    cache.put("k", ["One."], settings.cache_ttl_seconds, "why", minutes=2, slide=True)
    db = sqlite3.connect(cache.path)
    db.execute("UPDATE scripts SET expires = ?", (time.time() + 10,))
    db.commit()
    cache.record_play("k")
    assert db.execute("SELECT expires FROM scripts").fetchone()[0] < time.time() + 11


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_a_rewrite_with_the_same_words_keeps_the_audio(kind, tmp_path):
    cache = (MemoryScriptCache() if kind == "memory"
             else SqliteScriptCache(str(tmp_path / "s.db")))
    pipe = PodcastPipeline(generator=FakeGenerator(), engine=CountingVoice(),
                           cache=cache)
    plan = plan_episode("how rainbows form", 1)
    _audio, stats = play(pipe, plan)
    assert cache.stats()["audio_entries"] == 1
    key = stats.audio_key or stats.caption_key
    cache.put(key, list(stats.script), 600, "how rainbows form", minutes=1)
    assert cache.stats()["audio_entries"] == 1, "identical words dropped the audio"


def test_the_default_voice_is_one_audio_key_however_it_is_asked_for(monkeypatch):
    """The app sends the default voice's id; a shared link sends none. Same
    engine, same weights, same recording - it must be one row, or one episode
    is voiced on RunPod twice."""
    engine = CountingVoice()
    monkeypatch.setattr(CountingVoice, "default_voice_id",
                        classmethod(lambda cls: "countingvoice:reference_3"))
    named = PodcastPipeline(generator=FakeGenerator(), engine=engine,
                            cache=MemoryScriptCache(),
                            voice="countingvoice:reference_3")
    unnamed = PodcastPipeline(generator=FakeGenerator(), engine=engine,
                              cache=MemoryScriptCache())
    assert unnamed._audio_voice() == named._audio_voice()
    # A default that belongs to another engine is not what this one speaks.
    monkeypatch.setattr(CountingVoice, "default_voice_id",
                        classmethod(lambda cls: "other:reference_3"))
    assert unnamed._audio_voice() == "countingvoice:default"


def test_a_replay_through_a_link_plays_the_audio_the_app_kept(monkeypatch):
    engine = CountingVoice()
    monkeypatch.setattr(CountingVoice, "default_voice_id",
                        classmethod(lambda cls: "countingvoice:reference_3"))
    cache = MemoryScriptCache()
    plan = plan_episode("why the sky is blue", 1)
    play(PodcastPipeline(generator=FakeGenerator(), engine=engine, cache=cache,
                         voice="countingvoice:reference_3"), plan)
    spoken = engine.calls
    _audio, stats = play(PodcastPipeline(generator=FakeGenerator(), engine=engine,
                                         cache=cache), plan)
    assert engine.calls == spoken and stats.audio == "stored"


# --------------------------------------------------------------------------
# 6. Explore: plays, vibes, likes and dislikes
# --------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_plays_count_real_plays_and_not_every_look(kind, tmp_path):
    cache = (MemoryScriptCache() if kind == "memory"
             else SqliteScriptCache(str(tmp_path / "s.db")))
    pipe = PodcastPipeline(generator=FakeGenerator(), engine=CountingVoice(),
                           cache=cache)
    plan = plan_episode("why the sky is blue", 1)
    _a, stats = play(pipe, plan)
    key = stats.caption_key
    assert cache.plays(key) == 1, "the listen that wrote it is its first play"
    for _ in range(5):
        cache.get(key)           # probes, prefetch checks, pacing
    assert cache.plays(key) == 1, "a look was counted as a play"
    play(pipe, plan)
    assert cache.plays(key) == 2
    assert cache.recent(5)[0]["plays"] == 2


def test_ratings_are_one_thumb_per_listener_per_episode(tmp_path):
    s = social_mod.SocialStore(str(tmp_path / "social.db"))
    s.rate("a", "why the sky is blue", 2, 1)
    s.rate("a", "why the sky is blue", 2, 1)
    s.rate("b", "why the sky is blue", 2, -1)
    s.echo("c", "why the sky is blue", "Sky", 2)
    counts = s.episode_counts("why the sky is blue", 2, "a")
    assert (counts["likes"], counts["dislikes"], counts["vibes"],
            counts["rating"]) == (1, 1, 1, 1)
    s.rate("a", "why the sky is blue", 2, -1)       # a dislike replaces a like
    counts = s.episode_counts("why the sky is blue", 2, "a")
    assert (counts["likes"], counts["dislikes"], counts["rating"]) == (0, 2, -1)
    s.rate("a", "why the sky is blue", 2, 0)        # and a thumb comes back
    assert s.episode_counts("why the sky is blue", 2, "a")["rating"] == 0
    s.forget("b")
    assert s.episode_counts("why the sky is blue", 2)["dislikes"] == 0


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "SCRIPT_CACHE",
                        SqliteScriptCache(str(tmp_path / "e.db")))
    monkeypatch.setattr(appmod, "SOCIAL",
                        social_mod.SocialStore(str(tmp_path / "s.db")))
    return TestClient(appmod.app)


def test_an_explore_card_carries_its_counts_and_its_key(client):
    appmod.SCRIPT_CACHE.put("k1", ["A sentence."], 600, "why volcanoes erupt",
                            "", 3)
    appmod.SCRIPT_CACHE.record_play("k1")
    appmod.SOCIAL.echo("someone", "why volcanoes erupt", "Volcanoes", 3)
    appmod.SOCIAL.rate("someone", "why volcanoes erupt", 3, 1)
    card = client.get("/api/explore").json()["episodes"][0]
    assert card["key"] == "k1"
    assert (card["plays"], card["vibes"], card["likes"], card["dislikes"]) == (1, 1, 1, 0)
    assert card["rating"] == 0 and card["my_vibe"] is False
    assert "author" not in card


def test_rating_an_episode_returns_the_cards_fresh_counts(client):
    appmod.SCRIPT_CACHE.put("k1", ["A sentence."], 600, "why volcanoes erupt",
                            "", 3)
    body = client.post("/api/rate", json={"query": "why volcanoes erupt",
                                          "minutes": 3, "value": 1}).json()
    assert body["likes"] == 1 and body["rating"] == 1
    body = client.post("/api/rate", json={"query": "why volcanoes erupt",
                                          "minutes": 3, "value": 0}).json()
    assert body["likes"] == 0 and body["rating"] == 0
    assert client.post("/api/rate", json={"query": "x", "minutes": 3,
                                          "value": 5}).status_code == 422


def test_episode_stats_include_the_play_that_just_started(client):
    appmod.SCRIPT_CACHE.put("k1", ["A sentence."], 600, "why volcanoes erupt",
                            "", 3)
    appmod.SCRIPT_CACHE.record_play("k1")
    appmod.SCRIPT_CACHE.record_play("k1")
    stats = client.get("/api/episode/stats",
                       params={"q": "why volcanoes erupt", "minutes": 3,
                               "key": "k1"}).json()
    assert stats["plays"] == 2
    # No key, no guess.
    assert client.get("/api/episode/stats",
                      params={"q": "why volcanoes erupt", "minutes": 3}
                      ).json()["plays"] == 0


# The rule that Explore leaves out a listener's own episodes is pinned where
# it was built: tests/test_explore.py,
# test_an_episode_is_dropped_from_its_own_authors_feed.


# --------------------------------------------------------------------------
# 4, 5 and 6 in the interface
# --------------------------------------------------------------------------
def test_different_picks_is_gone():
    assert "<span>Different picks</span>" not in INDEX
    assert "refreshSeedsFeed" not in INDEX
    assert "seeds-refresh" not in INDEX


def test_play_all_starts_a_queue_rather_than_one_episode():
    body = re.search(r"function playMix\(\)\{(.*?)\n  \}", INDEX, re.S).group(1)
    assert "startMixQueue(m, 0)" in body
    body = re.search(r"function playMixFrom\(itemId\)\{(.*?)\n  \}", INDEX, re.S).group(1)
    assert "startMixQueue(m, i)" in body


def test_a_playlist_entry_ends_by_playing_the_next_and_never_whats_next():
    body = re.search(r"function populatePlayer\(key, albumContext\)\{(.*?)\n  \}",
                     INDEX, re.S).group(1)
    assert "playOpts.nextUp = false" in body
    assert "advanceMix(key)" in body
    assert "speakText(query, t.caption, mixOnEnd" in body


def test_the_end_of_a_playlist_names_it_and_plays_nothing():
    body = re.search(r"function finishMix\(\)\{(.*?)\n  \}", INDEX, re.S).group(1)
    assert "You have finished listening to your " in body
    assert "playlist for today" in body
    assert "generate(" not in body and "maybeOfferNextUp" not in body
    assert 'id="mixDoneOverlay"' in INDEX


def test_skip_to_next_is_on_the_player_and_only_drawn_for_a_playlist():
    button = re.search(r'<div class="mini-ctrl-btn mix-skip"[^>]*>', INDEX).group(0)
    assert 'id="mixSkipBtn"' in button and "hidden" in button
    assert 'onclick="skipMixItem()"' in button
    assert "drawMixSkip(!!mixOnEnd)" in INDEX


def test_explore_shows_the_episodes_plays_and_not_the_sessions_swipes():
    assert '(reelHistory.length) + " played"' not in INDEX
    body = re.search(r"function drawReelStats\(\)\{(.*?)\n  \}", INDEX, re.S).group(1)
    assert "ep.plays" in body
    for element in ('id="reelLike"', 'id="reelDislike"', 'id="reelVibeN"',
                    'id="reelLikeN"', 'id="reelDislikeN"'):
        assert element in INDEX


def test_explore_still_plays_cached_only():
    body = re.search(r"function playReel\(\)\{(.*?)\n  \}", INDEX, re.S).group(1)
    assert "cachedOnly: true" in body


def test_the_deployment_turns_the_trending_source_on():
    render = open(os.path.join(ROOT, "render.yaml"), encoding="utf-8").read()
    assert re.search(r"- key: GDELT\n\s+value: \"1\"", render)


# --------------------------------------------------------------------------
# Found in review (§134)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("header, region", [
    ("en-US,en;q=0.9", "US"), ("zh-Hant-TW", "TW"), ("en-US-u-ca-gregory", "US"),
    ("es-419", ""), ("en_GB", "GB"), ("en", ""), ("*", ""), ("", ""),
    ("de-CH;q=0.8", "CH")])
def test_the_browser_region_is_read_as_bcp47_says(header, region):
    assert appmod._region_of(header) == region


def test_every_two_letter_region_resolves_to_a_country_name():
    for code in ("SE", "CH", "TW", "PT", "BE", "GB", "US", "NG", "BR", "KR"):
        name = stories.normalise_country(code)
        assert len(name) > 2, f"{code} resolved to {name!r}"
    assert stories.normalise_country("Myanmar") == stories.normalise_country("MM")


def test_a_held_story_never_outranks_a_live_one_on_trending():
    live = T.topics_from_stories([story(f"live {n}", (facet,), 0.3)
                                  for n, facet in enumerate(
                                      ("world", "money", "culture", "science"))])
    held = T.topics_from_stories([story("held loud", ("sports",), 1.0,
                                        (("united kingdom", 1.0),))])
    row = T.rank_world(live, "UK", held)
    assert {t.id for t in row} == {t.id for t in live}
    assert [t.id for t in T.rank_world(live[:2], "UK", held)][-1] == held[0].id


def test_a_page_of_counts_matches_one_episode_at_a_time(tmp_path):
    s = social_mod.SocialStore(str(tmp_path / "social.db"))
    for who, q, m in (("a", "why tides turn", 2), ("b", "why tides turn", 2),
                      ("a", "why volcanoes erupt", 3)):
        s.echo(who, q, q.title(), m)
    s.rate("a", "why tides turn", 2, 1)
    s.rate("c", "why tides turn", 2, -1)
    s.rate("a", "why tides turn", 3, 1)          # another length, another episode
    pairs = [("why tides turn", 2), ("why volcanoes erupt", 3), ("nobody", 2)]
    many = s.episode_counts_many(pairs, "a")
    for q, m in pairs:
        assert many[(q, m)] == s.episode_counts(q, m, "a"), (q, m)


def test_a_thumb_does_not_zero_the_play_count(client):
    appmod.SCRIPT_CACHE.put("k1", ["A sentence."], 600, "why volcanoes erupt",
                            "", 3)
    appmod.SCRIPT_CACHE.record_play("k1")
    body = client.post("/api/rate", json={"query": "why volcanoes erupt",
                                          "minutes": 3, "value": 1,
                                          "key": "k1"}).json()
    assert body["plays"] == 1
    assert re.search(r"value: next,\s*key: ep\.key", INDEX)


def test_a_friends_mix_does_not_take_over_a_running_playlist():
    body = re.search(r"function playPersonMix\(i\)\{(.*?)\n  \}", INDEX, re.S).group(1)
    assert "endMixQueue()" in body and "playMixItem(items[0], false)" in body
    body = re.search(r"function playMixItem\(item, inQueue\)\{(.*?)\n  \}",
                     INDEX, re.S).group(1)
    assert "inQueue === false" in body


def test_finishing_a_playlist_clears_a_loading_screen():
    body = re.search(r"function finishMix\(\)\{(.*?)\n  \}", INDEX, re.S).group(1)
    assert "clearGenOverlay()" in body


def test_vibes_are_counted_through_an_index_on_the_episode(tmp_path):
    s = social_mod.SocialStore(str(tmp_path / "social.db"))
    plan = " ".join(r[3] for r in sqlite3.connect(s.path).execute(
        "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM echoes WHERE query = ? AND minutes = ?",
        ("q", 2)))
    assert "echoes_episode" in plan, plan
