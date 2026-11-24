"""§688: the screen that shows "myFAM" is Explore | Playlists, drawn from
the owner's 11.24 mock. Explore is the categories tab as cards, the Trending
edition's stories first; a playlist is a row; a playlist's page is today's
stories - heard, now playing, up next - with a heart that saves for later."""
from __future__ import annotations

import os
import pathlib
import sys
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import categories as C  # noqa: E402
import mixes as M  # noqa: E402
import preferences as P  # noqa: E402
import topics as T  # noqa: E402
from cache import SqliteScriptCache  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


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
    monkeypatch.setattr(appmod, "_day_start", lambda: time.time() - 86400)
    monkeypatch.setattr(appmod, "_trending_keys", lambda now: {})
    return TestClient(appmod.app)


def _put(key, query, title, category="", plays=0, summary=""):
    cache = appmod.SCRIPT_CACHE
    cache.put(key, ["A sentence."], 600, query, "", 2, "", "", "someone",
              title=title, summary=summary, category=category, origin="search")
    with cache._conn() as conn:
        conn.execute("UPDATE scripts SET plays = ? WHERE key = ?", (plays, key))


def _episodes(client, node, **params):
    res = client.get("/api/categories/episodes", params={"id": node, **params})
    assert res.status_code == 200, res.text
    return res.json()


# --- the server -------------------------------------------------------------


def test_all_is_every_category_at_once(client):
    _put("k1", "detroit lions win", "Lions roll on", category="detroit lions", plays=1)
    _put("k2", "fed rates", "The Fed holds", category="federal reserve", plays=5)
    got = _episodes(client, "all", sort="trending")
    assert got["id"] == "all" and got["label"] == "All"
    assert got["subcategories"] == []
    assert [e["title"] for e in got["episodes"]] == ["The Fed holds", "Lions roll on"]


def test_the_trending_editions_stories_come_first_in_its_order(client, monkeypatch):
    _put("k1", "detroit lions win", "Lions roll on", category="detroit lions", plays=9)
    _put("k2", "fed rates", "The Fed holds", category="federal reserve", plays=5)
    _put("k3", "nfl trade deadline", "The trade deadline", category="nfl", plays=0)
    monkeypatch.setattr(appmod, "_trending_keys", lambda now: {"k3": 0, "k2": 1})
    got = _episodes(client, "all", sort="trending")
    assert [e["title"] for e in got["episodes"]] == [
        "The trade deadline", "The Fed holds", "Lions roll on"]
    assert [e["trending"] for e in got["episodes"]] == [True, True, False]
    # Inside a category too, and the old order is untouched (`old-clients`).
    inside = _episodes(client, "nfl", sort="trending")
    assert [e["title"] for e in inside["episodes"]] == ["The trade deadline", "Lions roll on"]
    popular = _episodes(client, "nfl")
    assert popular["sort"] == "popular"
    assert [e["title"] for e in popular["episodes"]] == ["Lions roll on", "The trade deadline"]


def test_a_row_carries_its_one_line(client):
    _put("k1", "fed rates", "The Fed holds", category="federal reserve",
         summary="Twelve people, one decision.")
    _put("k2", "nfl trade deadline", "The trade deadline", category="nfl")
    rows = {e["title"]: e for e in _episodes(client, "all")["episodes"]}
    assert rows["The Fed holds"]["hook"] == "Twelve people, one decision."
    assert rows["The trade deadline"]["hook"] == ""


def test_all_is_capped(client, monkeypatch):
    monkeypatch.setattr(appmod, "EXPLORE_ALL_LIMIT", 2)
    for n in range(4):
        _put(f"k{n}", f"fed rates {n}", f"The Fed holds {n}", category="federal reserve")
    assert len(_episodes(client, "all")["episodes"]) == 2
    # A category's own page is not.
    assert len(_episodes(client, "money")["episodes"]) == 4


def test_trending_keys_read_the_edition_in_memory_and_nothing_else(monkeypatch):
    tiles = [SimpleNamespace(query="first story"), SimpleNamespace(query="second story"),
             SimpleNamespace(query="first story")]
    monkeypatch.setattr(appmod.trending_bank, "stories_in_memory", lambda now: ["s"])
    monkeypatch.setattr(appmod.topics_mod, "topics_from_stories", lambda rows, now=None: tiles)
    keys = appmod._trending_keys(time.time())
    assert sorted(keys.values()) == [0, 1]
    assert len(keys) == 2

    def boom(now):
        raise RuntimeError("no edition")
    monkeypatch.setattr(appmod.trending_bank, "stories_in_memory", boom)
    assert appmod._trending_keys(time.time()) == {}


def test_a_playlists_row_carries_todays_one_line(monkeypatch):
    monkeypatch.setattr(appmod, "_today_title", lambda prompt, minutes: "Today's title")
    monkeypatch.setattr(appmod, "_today_hook", lambda prompt, minutes: "Today's line.")
    body = {"items": [{"prompt": "p", "minutes": 2}]}
    appmod._mix_episode_titles(body)
    assert body["items"][0]["episode_hook"] == "Today's line."
    # No episode today, no line: the row shows the topic.
    monkeypatch.setattr(appmod, "_today_title", lambda prompt, minutes: "")
    body = {"items": [{"prompt": "p", "minutes": 2}]}
    appmod._mix_episode_titles(body)
    assert "episode_hook" not in body["items"][0]


# --- the screen -------------------------------------------------------------


def test_the_tabs_are_explore_then_playlists():
    tabs = HTML.split('<div class="pf-tabs cats"', 1)[1].split("</div>", 1)[0]
    assert tabs.index(">Explore<") < tabs.index(">Playlists<")
    assert 'id="pfTabCats" role="tab" aria-selected="true"' in tabs
    assert ">Mixes<" not in tabs and ">Categories<" not in tabs


def test_an_arrival_opens_on_explore_unless_a_playlist_is_new():
    body = _fn("openPlayFAM")
    assert 'tab === "mixes" || fresh ? "mixes" : "categories"' in body
    assert "data-mix-dot" in body
    # Back from a playlist, or one just made, returns to the playlists.
    assert 'openPlayFAM("mixes")' in _fn("closeMixPage")


def test_explore_asks_for_all_trending_first():
    body = _fn("loadCategoryEpisodes")
    assert 'var asked = node || "all"' in body
    assert "&sort=trending" in body


def test_all_is_the_chip_that_is_on():
    body = _fn("paintCategoryChips")
    assert 'data-chip="all"' in body and ">All</button>" in body


def test_a_card_is_picture_category_title_line_and_two_buttons():
    body = _fn("exploreCardHTML")
    for cls in ("xp-thumb", "xp-kick", "xp-title", "xp-hook", "xp-play", "xp-add"):
        assert cls in body, cls
    assert "Add to playlist" in body
    # FAM plays what is cached; Add to playlist is the player's own sheet.
    assert "playCategoryEpisode(" in body
    assert "openAddToMix(ep.query" in _fn("addExploreToPlaylist")
    assert "cachedOnly: true" in _fn("playCategoryList")


def test_a_playlist_card_is_a_row_with_its_count_topics_and_play():
    body = _fn("playlistCardHTML")
    for cls in ("pl-art", "pl-name", "pl-meta", "pl-topics", "pl-play"):
        assert cls in body, cls
    assert '" briefings"' in body and "daily" in body
    assert '" and " + more + " more"' in _fn("playlistTopicsLine")
    assert "startMixQueue(m, 0)" in _fn("playMixCard")
    assert "mix-a-grid" not in _fn("renderMixList")


def test_new_playlist_is_a_dashed_row_hidden_without_an_account():
    button = HTML.split('id="newMixBtn"', 1)[1].split("</button>", 1)[0]
    assert "hidden" in button and button.rstrip().endswith("New playlist")
    assert HTML.index('<div id="mixList">') < HTML.index('id="newMixBtn"')
    assert "Create new mix" not in HTML.split("<body", 1)[1].split("<script", 1)[0]


def test_the_playlists_page_says_what_plays():
    head = HTML.split('id="screen-mixdetail"', 1)[1].split('id="mixBody"', 1)[0]
    assert 'aria-label="myFAM Playlist"' in head
    assert 'id="mixTitle"' in head and 'id="mixHeadMeta"' in head
    assert 'onclick="openMixMenu()"' in head
    body = _fn("renderMixBody")
    assert '" of " + mixPlay.order.length' in body.replace("' of '", '" of "')
    assert "playlistRowHTML(m, t, state)" in body
    row = _fn("playlistRowHTML")
    assert '"Now playing"' in row and '"Up next"' in row and '"Heard"' in row


def test_dragging_is_part_of_edit_topics():
    body = _fn("renderMixBody")
    rows, editing = body.split("// Edit topics: the owner's order", 1)
    assert "startMixDrag" not in rows
    assert "startMixDrag" in editing and "removeMixTopic" in editing


def test_the_heart_is_saved_for_later_and_nothing_else():
    body = _fn("toggleMixRowSave")
    assert '"/api/saved"' in body
    assert "/api/vibe" not in body and "/api/echo" not in body and "rating" not in body
    assert "mixItemPrompt(t)" in body
    # What a tap plays is what the heart saves.
    assert "mixItemPrompt(item)" in _fn("playMixItem")


def test_the_mini_player_in_a_playlist_keeps_its_x():
    bar = HTML.split('id="nowBar"', 1)[1].split('<div class="tabbar">', 1)[0]
    assert 'id="nowHeart" data-save' in bar and "saveForLater()" in bar
    assert 'id="nowNext"' in bar and "skipMixItem()" in bar
    assert "dismissNowBar()" in bar
    assert "data-echo" not in bar
    assert '" of " + mixPlay.order.length' in _fn("nowBarMixLine")
    # And it is on a playlist's own page: the page has the tabs.
    page = HTML.split('id="screen-mixdetail"', 1)[1].split("</section>", 1)[0]
    assert '<div class="tabbar">' in page


def test_a_playlist_can_still_be_given_its_own_cover():
    """The owner's follow-up: the redesign must not cost a playlist its own
    cover photo. Three ways in - the naming screen, the picture on the
    playlist's page, the sliders' menu - and the photo is what the card
    and the add sheet draw, ahead of today's lead picture."""
    assert "coverTapped('new')" in HTML.split('id="screen-newmix"', 1)[1].split("</section>", 1)[0]
    body = _fn("renderMixBody")
    assert "coverTapped(" in body and "nm-cam" in body
    assert "if(!editing) html += details;" in body
    menu = _fn("openMixMenu")
    assert '"Change cover" : "Add a cover photo"' in menu and "coverTapped(m.id)" in menu
    assert "{ cover: dataUrl }" in _fn("applyCover")
    assert "m.cover || (m.thumbs || [])[0]" in _fn("coverOf")
    assert "coverOf(m)" in _fn("playlistCardHTML")
