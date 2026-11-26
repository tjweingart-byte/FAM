"""§694, the owner's 11.25 packet, on the server and the web.

What's next is shown after every episode again and leaves only when the
listener leaves it; an Explore card plays that one story, by the id of the
kept episode; Explore's shelf is today's episodes and the still-current ones
of the days before, dealt league by league; a Go Deeper's episode is never a
card; the card says the day it was made and its play is a round play button;
the chip row is the main categories and never changes; a playlist's header
has no line under it, and an empty playlist's Add topics is under its
picture and name."""
from __future__ import annotations

import os
import pathlib
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import cache as cache_mod  # noqa: E402
import categories as C  # noqa: E402
import mixes as M  # noqa: E402
import preferences as P  # noqa: E402
import topics as T  # noqa: E402
from cache import SqliteScriptCache  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
DAY = 86400.0


def _fn(name: str) -> str:
    return HTML.split(f"function {name}(", 1)[1].split("\n  }\n", 1)[0]


@pytest.fixture
def client(monkeypatch, tmp_path):
    C.apply_seed(T.category_tree())
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", SqliteScriptCache(str(tmp_path / "c.db")))
    monkeypatch.setattr(appmod, "MIXES", M.MixStore(str(tmp_path / "m.db")))
    monkeypatch.setattr(appmod, "PREFS", P.PreferenceStore(str(tmp_path / "p.db")))
    # The listener's day began an hour ago.
    monkeypatch.setattr(appmod, "_day_start", lambda: time.time() - 3600)
    monkeypatch.setattr(appmod, "_trending_keys", lambda now: {})
    appmod._CATEGORY_MEMO.update(at=0.0, stores=None)
    return TestClient(appmod.app)


def _put(key, title, category, plays=0, days_ago=0.0, current=True, bucket=""):
    """One kept episode. `days_ago`: made that long before now; `current`:
    whether a new request would still be served it."""
    cache = appmod.SCRIPT_CACHE
    cache.put(key, ["A sentence."], 7 * 86400, "q " + key, "", 2, bucket, "", "someone",
              title=title, category=category, origin="search")
    made = time.time() - days_ago * DAY
    with cache._conn() as conn:
        conn.execute("UPDATE scripts SET plays = ?, created = ?, sourced_at = ?,"
                     " fresh_until = ? WHERE key = ?",
                     (plays, made, made, time.time() + (3600 if current else -60), key))


def _titles(client, node, **params):
    res = client.get("/api/categories/episodes", params={"id": node, **params})
    assert res.status_code == 200, res.text
    return [e["title"] for e in res.json()["episodes"]]


# --- Explore's shelf --------------------------------------------------------

def test_the_shelf_is_today_and_what_is_still_current_from_before(client):
    _put("a", "Tonight's game", "nba")
    _put("b", "The trade that built a dynasty", "nba", days_ago=3)
    _put("c", "Monday's final score", "nba", days_ago=2, current=False)
    _put("d", "Older than the shelf", "nba", days_ago=appmod.EXPLORE_PAST_DAYS + 2)
    assert set(_titles(client, "nba", sort="trending")) == {
        "Tonight's game", "The trade that built a dynasty"}
    # The orders installed clients ask for are today's only, as they were.
    assert _titles(client, "nba") == ["Tonight's game"]
    assert _titles(client, "nba", sort="recent") == ["Tonight's game"]


def test_a_go_deepers_episode_is_never_a_card(client):
    """It is kept under a key that carries the episode before it, so the
    card's own words never found it: "That episode is no longer in the
    cache"."""
    cold = cache_mod.key_bucket(2, "", True)
    follow = cache_mod.key_bucket(2, "Why the West looks stronger", True)
    _put("a", "Why the West looks stronger", "nba", bucket=cold)
    _put("b", "What's the bigger story behind Why the West looks stronger?", "nba",
         bucket=follow)
    assert _titles(client, "all", sort="trending") == ["Why the West looks stronger"]
    assert _titles(client, "nba") == ["Why the West looks stronger"]


def test_a_row_names_the_kept_episode_it_plays(client):
    key = cache_mod.cache_key("tonight's game", 2)   # a real key's shape
    _put(key, "Tonight's game", "nba")
    row = client.get("/api/categories/episodes",
                     params={"id": "all", "sort": "trending"}).json()["episodes"][0]
    sourced = appmod.SCRIPT_CACHE.sourced_at(key)
    assert row["episode"] == cache_mod.episode_id(key, sourced)
    # And that id is one the replay route resolves to this very row.
    assert appmod.SCRIPT_CACHE.resolve_episode(row["episode"]) == key
    assert "made_age_seconds" in row


def test_sport_is_dealt_league_by_league_the_major_leagues_first(client):
    # The NBA has been played most; sorted by plays alone it was the page.
    for n in range(4):
        _put(f"nba{n}", f"NBA story {n}", "nba", plays=50 - n)
    _put("nfl0", "NFL story", "nfl", plays=3)
    _put("mlb0", "MLB story", "mlb", plays=1)
    _put("golf0", "Golf story", "golf", plays=9)
    _put("cric", "Cricket story", "cricket", plays=20)
    got = _titles(client, "sports", sort="trending")
    # One from each in turn: the leagues in `EXPLORE_LEADING`'s order, then
    # the page's other categories, the busiest first; then round again.
    assert got[:5] == ["NFL story", "NBA story 0", "MLB story", "Golf story", "Cricket story"]
    assert got[5:] == ["NBA story 1", "NBA story 2", "NBA story 3"]
    # `popular` is untouched for installed clients.
    assert _titles(client, "sports")[0] == "NBA story 0"


def test_todays_stories_are_woven_with_the_ones_from_before(client):
    for n in range(5):
        _put(f"t{n}", f"Today {n}", "nba", plays=10 - n)
    _put("p0", "From before 0", "nba", plays=99, days_ago=2)
    _put("p1", "From before 1", "nba", plays=1, days_ago=4)
    got = _titles(client, "nba", sort="trending")
    run = appmod.EXPLORE_FRESH_RUN
    assert got[:run] == [f"Today {n}" for n in range(run)]
    assert got[run] == "From before 0"
    assert got[-1] == "From before 1" and len(got) == 7


def test_the_trending_editions_stories_still_lead(client, monkeypatch):
    _put("a", "NBA story", "nba", plays=50)
    _put("b", "The Fed holds", "federal reserve")
    monkeypatch.setattr(appmod, "_trending_keys", lambda now: {"b": 0})
    assert _titles(client, "all", sort="trending") == ["The Fed holds", "NBA story"]


def test_the_order_is_pure_and_loses_nothing():
    C.apply_seed(T.category_tree())
    strands = ("nfl", "nba", "golf", "cricket", "sports")
    rows = [{"key": str(n), "node": strands[n % 5],
             "chain": frozenset({"sports", strands[n % 5]}),
             "popularity": n % 4, "created": float(n), "today": n % 3 != 0}
            for n in range(23)]
    for trending in ({}, {"7": 0, "0": 1}):
        out = appmod._explore_order(list(rows), "sports", trending)
        assert sorted(r["key"] for r in out) == sorted(r["key"] for r in rows)
        assert len({r["key"] for r in out}) == len(rows)
    # Place 0 in the edition is a place, not "none".
    assert [r["key"] for r in out[:2]] == ["7", "0"]


def test_a_change_of_settings_does_not_empty_the_page(client):
    """A bucket holds the model and pace as they were at the write. Rows from
    before a change match neither of today's cold buckets, but they share
    one with each other - which a Go Deeper's never does (review, §694)."""
    # What `key_bucket` gave under the earlier settings: any other hashes.
    old_cold, old_follow = "bucket-of-the-earlier-model", "that-bucket-plus-an-episode"
    assert old_cold != cache_mod.key_bucket(2, "", True)
    for n in range(appmod.COLD_BUCKET_ROWS):
        _put(f"o{n}", f"Before the change {n}", "nba", bucket=old_cold)
    _put("f", "A follow-up from before", "nba", bucket=old_follow)
    got = _titles(client, "nba")
    assert len(got) == appmod.COLD_BUCKET_ROWS and "A follow-up from before" not in got


def test_a_follow_up_with_no_bucket_kept_is_still_a_card_and_plays_by_id(client):
    key = cache_mod.cache_key("why", 2, None, "Some episode", True)
    _put(key, "A follow-up", "nba")           # CACHE_VECTOR=0: no bucket
    row = client.get("/api/categories/episodes", params={"id": "nba"}).json()["episodes"][0]
    assert appmod.SCRIPT_CACHE.resolve_episode(row["episode"]) == key


def test_a_stale_episode_id_falls_back_to_the_question_once():
    assert "askIfGone: !!first.episode" in _fn("playCategoryList")
    assert "askIfGone: !!entry.episode" in _fn("playQueueNext")
    failed = _fn("onGenerationFailed")
    assert "gone.askIfGone && gone.episode" in failed and 'gone.episode = "";' in failed


def test_a_follower_waits_once_for_whats_next():
    follower = _fn("showFollowerPopup")
    assert "if(followerWaiting[person.user_id]) return;" in follower


# --- the web: Explore -------------------------------------------------------

def test_a_card_plays_that_story_alone_by_its_episode_id():
    assert "playCategoryList([eps[i]])" in _fn("playCategoryEpisode")
    listed = _fn("playCategoryList")
    assert 'episode: first.episode || ""' in listed and "cachedOnly: true" in listed
    assert 'episode: ep.episode || ""' in _fn("categoryQueueEntry")
    assert 'episode: entry.episode || ""' in _fn("playQueueNext")
    # Play all still queues the category from the top.
    assert "playCategoryList(catPage.episodes)" in _fn("playCategoryAll")


def test_the_card_says_its_day_and_its_play_is_only_a_play_button():
    card = _fn("exploreCardHTML")
    assert 'class="xp-when"' in card and "episodeDayLabel(ep.made_age_seconds)" in card
    assert "FAM_GLYPHS" not in HTML and "xp-fam" not in HTML
    play = HTML.split("  .xp-play{", 1)[1].split("}", 1)[0]
    assert "width:44px" in play and "height:44px" in play and "border-radius:50%" in play
    assert 'aria-label="Play \' + escapeHtml(name)' in card


def test_the_chip_row_is_all_and_the_main_categories_whatever_is_picked():
    draw = _fn("paintCategoryChips")
    row = draw.split("var followed", 1)[1]
    assert 'data-chip="all"' in row and "catData.facets.map(" in row
    for gone in ("catPage.subs", "catchip-x", "catchip-more", "openCategoryFind", ".mine.map("):
        assert gone not in row, gone
    # The same call draws it with or without a category: only `on` differs.
    assert "on === f.id" in row and "(on ? '' : ' on')" in row


# --- the web: What's next ---------------------------------------------------

def test_whats_next_is_offered_with_one_tile_and_only_none_shows_nothing():
    offer = _fn("maybeOfferNextUp")
    assert "if(!tiles.length || !nextUpAllowedHere() || goDeeperIsOpen() || navigator.onLine === false) return;" in offer
    assert "tiles.length < 2" not in HTML


def test_nothing_but_the_listener_closes_whats_next():
    # Every caller of `closeNextUp` is a tap: a tile, the search, Back, the
    # dim around the card - or the "just joined" card, which never goes up
    # over another overlay in the first place.
    assert 'id="nextUpOverlay" onclick="if(event.target === this) closeNextUp()"' in HTML
    follower = _fn("showFollowerPopup")
    assert "closeNextUp()" not in follower
    assert 'getElementById("nextUpOverlay").classList.contains("active")' in follower
    joined = _fn("showNextJoined")
    assert joined.index('document.querySelector(".modal-overlay.active")') < joined.index("closeNextUp()")
    # And still nothing starts by itself (§686).
    shown = _fn("showNextUp")
    assert "setTimeout" not in shown and "setInterval" not in shown


# --- the web: a playlist's page ---------------------------------------------

def test_the_playlists_header_has_no_line_under_it():
    head = HTML.split('id="screen-mixdetail"', 1)[1].split('id="mixBody"', 1)[0]
    assert 'aria-label="myFAM Playlist"' in head
    for gone in ("pl-head-sub", 'id="mixTitle"', 'id="mixHeadMeta"'):
        assert gone not in head, gone
    assert "toLocaleDateString" not in _fn("paintMixHead")


def test_an_empty_playlists_add_topics_is_under_its_picture_and_name():
    body = _fn("renderMixBody")
    assert "if(!items.length && !editing){\n      html = details + html + addTopics;" in body
    # With topics it is as it was: the stories, Add topics, the details.
    rest = body.split("html = details + html + addTopics;", 1)[1]
    assert rest.index("html += addTopics;") < rest.index("if(!editing) html += details;")
