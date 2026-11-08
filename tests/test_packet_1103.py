"""The 11.3 packet (PROBLEMS.md §498): exploreFAM out of the app, "Not
interested" in a tile's three dots, the day an episode was made in the
catalogue, a vibe deleted from your profile, and a layout that holds when the
phone's text is made larger. (The categories redesign is a deck for the owner
to choose from; nothing of it is built yet.)"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import mixes as M  # noqa: E402
import social as S  # noqa: E402
import topics as T  # noqa: E402
from cache import SqliteScriptCache  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text()


def _fn(name):
    return INDEX.split("function " + name + "(", 1)[1].split("\n  }\n", 1)[0]


@pytest.fixture
def store(tmp_path):
    return T.EventStore(str(tmp_path / "events.db"))


# --- 1. exploreFAM is gone -------------------------------------------------

def test_no_way_into_explorefam_is_left():
    for gone in ('id="screen-explore"', "openExplore(", "primeExplore(",
                 "exploreFamPillHTML", "openCategoryReel(", "reelCurrent",
                 "Search slides away to show Explore"):
        assert gone not in INDEX, gone


def test_the_server_still_answers_an_installed_clients_explore():
    """`old-clients`: a phone that has not updated still calls it."""
    paths = {r.path for r in appmod.app.routes}
    assert "/api/explore" in paths


# --- 2. Not interested -----------------------------------------------------

def test_not_interested_is_a_row_in_a_tiles_three_dots():
    menu = _fn("openTileMenu")
    assert 'menuLabel("notint", "Not interested")' in menu
    assert "tileNotInterested(topicId)" in menu
    act = _fn("tileNotInterested")
    assert 'recordFamEvent("not_interested", topicId' in act
    assert ".remove()" in act
    # Not a button on the card (§171 still holds there).
    assert "Not interested" not in _fn("seedActsHtml")


def test_not_interested_is_its_own_kind_and_weighs_against_the_subject():
    assert T.NOT_INTERESTED in T.EVENT_KINDS
    assert T.EVENT_WEIGHT[T.NOT_INTERESTED] < T.EVENT_WEIGHT["skip"] < 0
    # The retired `hide` stays retired: its old rows are not revived.
    assert T.HIDE not in T.EVENT_KINDS


def _shown(store, user):
    feed = T.build_feed(store, user)
    return [t["id"] for s in feed["sections"] for t in s["topics"]]


def test_a_tile_waved_off_leaves_every_rail_for_good(store):
    store.record(T.Event("u", "complete", T.TOPIC_BANK[0].id, "",
                         T.TOPIC_BANK[0].tags, time.time()))
    shown = _shown(store, "u")
    assert shown
    target = shown[0]
    topic = T.BANK_BY_ID.get(target) or T.known_topics(time.time()).get(target)
    store.record(T.Event("u", T.NOT_INTERESTED, target,
                         getattr(topic, "query", ""), T.tags_for_id(target, "")))
    assert target not in _shown(store, "u")
    # Only for them: somebody else still gets it.
    store.record(T.Event("v", "complete", T.TOPIC_BANK[0].id, "",
                         T.TOPIC_BANK[0].tags, time.time()))
    assert store.not_interested("v") == []


def test_not_wanted_matches_by_id_or_by_question():
    bank = list(T.TOPIC_BANK)[:3]
    assert T.not_wanted(bank, [(bank[0].id, "")]) == {bank[0].id}
    assert T.not_wanted(bank, [("", bank[1].query.upper())]) == {bank[1].id}
    assert T.not_wanted(bank, []) == set()


def test_a_waved_off_subject_is_not_a_familiar_word():
    events = [T.Event("u", T.NOT_INTERESTED, "x", "curling championships", ())]
    assert "curling" not in T.familiar_words(events)


def test_the_api_takes_the_kind(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "EVENTS", T.EventStore(str(tmp_path / "api.db")))
    client = TestClient(appmod.app)
    client.post("/api/auth/signup", json={"email": "ni@fam.test",
                                          "password": "a-long-enough-password"})
    target = T.TOPIC_BANK[0]
    r = client.post("/api/event", json={"kind": "not_interested",
                                        "topic_id": target.id, "text": target.query})
    assert r.status_code == 200 and r.json()["remembered"] is True
    user = client.get("/api/auth/me").json()["user_id"]
    assert appmod.EVENTS.not_interested(user) == [(target.id, target.query)]


# --- 3. The day an episode was made, in the catalogue ----------------------

def test_a_catalogue_row_says_when_it_was_made():
    row = _fn("myFamSearchRow")
    assert "episodeDayLabel(ep.sourced_age_seconds)" in row


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_day_reads_today_yesterday_days_ago_then_the_date():
    js = _fn("episodeDayLabel")
    script = ("var MONTHS_SHORT = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug',"
              "'Sep','Oct','Nov','Dec'];\nfunction episodeDayLabel(" + js + "\n}\n"
              # Noon on 8 November 2026, local time.
              "var now = new Date(2026, 10, 8, 12, 0, 0).getTime();\n"
              "var H = 3600;\n"
              "console.log(JSON.stringify(["
              "episodeDayLabel(60, now), episodeDayLabel(11 * H, now),"
              "episodeDayLabel(13 * H, now), episodeDayLabel(2 * 24 * H, now),"
              "episodeDayLabel(6 * 24 * H, now), episodeDayLabel(7 * 24 * H, now),"
              "episodeDayLabel(320 * 24 * H, now)]));")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                         check=True).stdout.strip()
    assert out == ('["Today","Today","Yesterday","2 days ago","6 days ago",'
                   '"Nov 1","Dec 23, 2025"]')


def test_the_catalogue_sends_each_rows_age(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    cache = SqliteScriptCache(str(tmp_path / "c.db"))
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", cache)
    cache.put("k", ["A sentence."], 600, "why tides turn", "", 2,
              origin="search", author="somebody-else", title="Why Tides Turn")
    rows = TestClient(appmod.app).get("/api/myfam/catalog").json()["episodes"]
    assert rows and all(isinstance(r["sourced_age_seconds"], float) for r in rows)


# --- 4. A vibe deleted from your profile -----------------------------------

def test_holding_a_vibe_on_your_grid_offers_to_remove_it():
    shelf = _fn("showProfileShelf")
    assert 'profileTileHTML("playProfileVibe(" + i + ")", v.thumb, v.title || v.query, i)' in shelf
    assert "armVibeHolds(grid)" in shelf
    hold = _fn("armVibeHolds")
    assert "VIBE_HOLD_MS" in hold and "contextmenu" in hold
    assert "stopImmediatePropagation" in hold      # lifting does not also play it
    menu = _fn("openVibeTileMenu")
    assert '"Remove vibe from profile"' in menu and "deleteMyVibe(v)" in menu
    assert 'method: "DELETE"' in _fn("deleteMyVibe")
    # A friend's grid draws no hold.
    assert "data-hold" not in _fn("showPersonShelf")


def test_deleting_a_vibe_takes_it_off_the_profile_and_the_story(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "SOCIAL", S.SocialStore(str(tmp_path / "s.db")))
    monkeypatch.setattr(appmod, "MIXES", M.MixStore(str(tmp_path / "m.db")))
    cache = SqliteScriptCache(str(tmp_path / "c.db"))
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", cache)
    cache.put("v", ["A sentence."], 600, "why tides turn", "", 3, origin="search")
    client = TestClient(appmod.app)
    client.post("/api/me", json={"name": "Ada", "handle": "ada"})
    client.post("/api/vibe", json={"query": "why tides turn",
                                   "title": "Why tides turn", "minutes": 3})
    assert client.get("/api/profile").json()["echo_count"] == 1
    gone = client.delete("/api/vibe?q=why+tides+turn&minutes=3").json()
    assert gone["ok"] is True
    profile = client.get("/api/profile").json()
    assert profile["echo_count"] == 0 and profile["echoes"] == []


def test_the_story_menu_deletes_rather_than_hides():
    assert "deleteVibeFromStory(v)" in _fn("openStoryMenu")
    assert "deleteMyVibe(v," in _fn("deleteVibeFromStory")


# --- 5. Larger text on the phone -------------------------------------------

def test_the_pick_up_cards_grow_with_their_words():
    assert ".gd-card{ min-height:80px; height:auto; }" in INDEX
    assert ".gd-card:has(.gd-card-sum){ min-height:104px; height:auto; }" in INDEX


def test_the_centred_wordmark_never_breaks_over_two_lines():
    assert ".myfam-header-fixed .wordmark, .yf-head .wordmark{ white-space:nowrap; }" in INDEX


def test_made_for_you_has_the_whole_width():
    # The exploreFAM pill beside it squeezed "Made for you, Saturday
    # afternoon" to a word a line at a larger text size.
    assert "feed-head-row" not in _fn("renderMyFamFeed")


def test_a_second_category_tap_replaces_what_the_first_queued():
    listed = _fn("playCategoryList")
    assert "if(QUEUE[i].fromCategory) QUEUE.splice(i, 1);" in listed
    assert listed.index("QUEUE.splice") < listed.index("QUEUE.push")
    assert "fromCategory: true" in _fn("categoryQueueEntry")


def test_trending_and_its_view_more_drop_a_waved_off_story_too():
    """Trending ranks only on the world, but like a heard story (§136) a
    story the listener waved off is not offered back to them there."""
    import inspect
    for fn in (T.build_feed, T.build_section):
        src = inspect.getsource(fn)
        assert "waved_off = (not_wanted(universe, store.not_interested(user_id))" in src
        assert "world_live = [t for t in world_live if t.id not in waved_off]" in src
        assert "world_held = [t for t in world_held if t.id not in waved_off]" in src
