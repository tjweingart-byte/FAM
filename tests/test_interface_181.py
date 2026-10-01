"""§181, the 9.30 interface packet: sign-up samples, myFAM search, Messages
on the tab bar, Explore as a myFAM rail, the 24-hour most-played rail, and
the player's (+) that adds an episode's topic to a DailyFAM mix."""
from __future__ import annotations

import os
import re
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import cache  # noqa: E402
import topics  # noqa: E402
from cache import SqliteScriptCache  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", SqliteScriptCache(str(tmp_path / "i.db")))
    return TestClient(appmod.app)


# --- the most-played rail --------------------------------------------------


def test_most_played_counts_the_last_twenty_four_hours():
    assert topics.MOST_PLAYED_WINDOW == 24 * 3600
    assert dict(topics.SECTIONS)["most_played"] == "Most played episodes today"
    assert "Most played episodes today" in HTML
    assert "can't stop listening" not in HTML


# --- the sign-up screen ----------------------------------------------------


def test_the_sign_up_screen_says_listen_to_anything():
    welcome = HTML[HTML.index('id="screen-welcome"'):HTML.index('id="screen-auth"')]
    assert "Listen to anything you want to know about" in welcome
    assert "Ask anything. Hear the answer." not in welcome
    assert 'id="welcomeSamples"' in welcome


def test_welcome_samples_are_empty_with_nothing_played(client):
    assert client.get("/api/welcome").json() == {"episodes": []}


def test_welcome_samples_are_the_top_three_with_kept_audio(client, monkeypatch):
    ranked = [topics.Topic(id=f"t{i}", title=f"Episode {i}", subtitle="", query=f"question {i}",
                           tags=("world",), icon="news")
              for i in range(6)]
    monkeypatch.setattr(topics, "rank_most_played", lambda *a, **k: ranked)
    # Episode 1 has no kept audio: a tap on it would wake the voice for
    # somebody with no account, so it is not offered.
    monkeypatch.setattr(appmod, "_audio_is_kept", lambda q, m: q != "question 1")
    got = client.get("/api/welcome").json()["episodes"]
    assert [e["title"] for e in got] == ["Episode 0", "Episode 2", "Episode 3"]
    assert all(e["minutes"] == appmod.BROWSE_MINUTES for e in got)
    assert all("user" not in key for e in got for key in e)


# --- search within myFAM ---------------------------------------------------


def test_rank_similar_orders_by_words_found_and_drops_the_unrelated():
    entries = [
        {"key": "a", "query": "how volcanoes erupt", "title": "Magma", "plays": 1},
        {"key": "b", "query": "why the eagles lost", "title": "Eagles", "plays": 9},
        {"key": "c", "query": "volcanoes in iceland erupt again", "title": "", "plays": 0},
    ]
    got = [e["key"] for e in cache.rank_similar("volcanoes erupt", entries)]
    assert got[:2] == ["a", "c"] or got[:2] == ["c", "a"]
    assert "b" not in got
    assert cache.rank_similar("the", entries) == []


def test_myfam_search_finds_other_peoples_episodes_only(client):
    me = client.get("/api/auth/me").json()["user_id"]
    appmod.SCRIPT_CACHE.put("mine", ["A sentence."], 600, "volcanoes I asked about",
                            "", 2, "", "", me, origin="search")
    appmod.SCRIPT_CACHE.put("theirs", ["A sentence."], 600, "how volcanoes erupt",
                            "", 2, "", "", "someone-else", origin="myfam")
    appmod.SCRIPT_CACHE.put("other", ["A sentence."], 600, "who won the world cup",
                            "", 2, "", "", "someone-else", origin="search")
    got = client.get("/api/myfam/search", params={"q": "volcanoes"}).json()["episodes"]
    assert [e["query"] for e in got] == ["how volcanoes erupt"]
    assert client.get("/api/myfam/search").json() == {"episodes": []}


# --- where things moved ----------------------------------------------------


def test_messages_took_explores_place_on_the_tab_bar():
    assert 'data-tab="explore"' not in HTML
    bars = re.findall(r'<div class="tabbar">(.*?)\n        </div>', HTML, re.S)
    assert len(bars) >= 6
    for bar in bars:
        assert 'data-tab="messages"' in bar
    messages = HTML[HTML.index('id="screen-messages"'):HTML.index('id="screen-thread"')]
    assert 'class="tabbar"' in messages
    assert "data-msg-badge" in messages


def test_myfams_header_searches_and_its_search_bar_is_gone():
    myfam = HTML[HTML.index('<section class="screen" id="screen-myfam">'):
                 HTML.index('id="screen-playfam"')]
    assert 'onclick="openMyFamSearch()"' in myfam
    assert 'onclick="openMessages()"' not in myfam.split('class="tabbar"')[0]
    assert "search-bar-lg" not in myfam
    assert 'id="screen-myfamsearch"' in HTML


def test_explore_is_a_rail_under_made_for_you():
    assert "What users are searching" in HTML
    assert "Start scrolling" in HTML
    assert '(sec.key === "from_history" ? searchingRailHTML() : "")' in HTML


# --- the player's (+) ------------------------------------------------------


def test_the_players_menu_adds_to_a_mix_and_sources_sit_by_the_title():
    """§190 moved the (+) into the three dots' menu ("Add to playlist") and
    the sources to the right of the title, where Spotify's green button is."""
    player = HTML[HTML.index('id="screen-player"'):HTML.index('id="screen-playall"')]
    assert 'onclick="openPlayerMenu()"' in player
    menu = HTML.split("function openPlayerMenu(){", 1)[1].split("\n  }\n", 1)[0]
    assert '"Add to playlist"), action: function(){ openAddToMix(); }' in menu
    row = player.split('<div class="p-titlerow">', 1)[1].split('<div class="progress-wrap">', 1)[0]
    assert row.index('id="p-title"') < row.index('id="srcPanel"')


@pytest.mark.parametrize("text,expected", [
    ("Eagles beat the Cowboys on Sunday", ("nfl", "Eagles")),
    ("How does Formula 1 aero work", ("formula1", "")),
    ("latest in AI agents", ("ai", "AI agents")),
    ("NFL week 5 recap", ("nfl", "")),
    ("why is the sky blue", ("", "")),
])
def test_episode_subject(text, expected):
    assert topics.episode_subject(text) == expected


def test_episode_topic_is_a_mix_entry(client):
    got = client.get("/api/episode/topic",
                     params={"q": "did the eagles win", "title": "Eagles hold on"}).json()
    assert got["id"] == "f:nfl~Eagles"
    assert got["label"] == "Eagles"
    # A topic the catalogue does not know is the episode's title, typed.
    typed = client.get("/api/episode/topic",
                       params={"q": "why is the sky blue", "title": "Rayleigh scattering"}).json()
    assert typed["id"] == "" and typed["query"] == "Rayleigh scattering"
