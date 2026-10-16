"""The 10.16 packet (PROBLEMS.md §285)."""
from __future__ import annotations

import os
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod
import cache as cache_mod
import mixes as mixes_mod
from pipeline import PodcastPipeline
from tts import DebugEngine

from test_pipeline import FakeGenerator  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def _signed_in(client, email, name, handle):
    client.post("/api/auth/signup", json={"email": email, "password": "password12"})
    client.post("/api/me", json={"name": name, "handle": handle})
    return client.get("/api/auth/me").json()["user_id"]


@pytest.fixture
def people(monkeypatch):
    """Ian, Beth and Cy, each with their own browser."""
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    ian, beth, cy = TestClient(appmod.app), TestClient(appmod.app), TestClient(appmod.app)
    with ian, beth, cy:
        ids = {"ian": _signed_in(ian, "ian@b.com", "Ian", "ian"),
               "beth": _signed_in(beth, "beth@b.com", "Beth", "beth"),
               "cy": _signed_in(cy, "cy@b.com", "Cy", "cy")}
        yield {"ian": ian, "beth": beth, "cy": cy}, ids


def _friends(a, b, ids, x, y):
    a.post("/api/friends/follow", json={"user_id": ids[y]})
    b.post("/api/friends/follow", json={"user_id": ids[x]})


# --- 1. a friend's friends -----------------------------------------------------

def test_a_friend_sees_their_friends_and_can_follow_one(people):
    c, ids = people
    _friends(c["ian"], c["beth"], ids, "ian", "beth")
    _friends(c["beth"], c["cy"], ids, "beth", "cy")

    body = c["ian"].get("/api/person?handle=beth").json()
    assert body["friend_count"] == 2
    # Cy, not Ian himself; no ids, as everywhere in this response.
    assert [f["handle"] for f in body["friends"]] == ["cy"]
    assert body["friends"][0]["following"] is False
    assert "user_id" not in body["friends"][0]

    # Found through Beth: opened and followed by handle.
    assert c["ian"].get("/api/person?handle=cy").status_code == 200
    assert c["ian"].post("/api/friends/follow", json={"handle": "cy"}).status_code == 200
    again = c["ian"].get("/api/person?handle=beth").json()
    assert again["friends"][0]["following"] is True


def test_only_a_friend_sees_the_list_but_anybody_sees_the_count(people):
    c, ids = people
    _friends(c["beth"], c["cy"], ids, "beth", "cy")
    # Ian follows Beth; she has not followed back.
    c["ian"].post("/api/friends/follow", json={"user_id": ids["beth"]})
    body = c["ian"].get("/api/person?handle=beth").json()
    assert body["friend_count"] == 1
    assert body["friends"] is None


def test_somebody_blocked_is_not_among_their_friends(people):
    c, ids = people
    _friends(c["ian"], c["beth"], ids, "ian", "beth")
    _friends(c["beth"], c["cy"], ids, "beth", "cy")
    appmod.MODERATION.block(ids["cy"], ids["ian"])
    body = c["ian"].get("/api/person?handle=beth").json()
    assert body["friends"] == [] and body["friend_count"] == 1


def test_the_profile_draws_the_shelf_and_the_friends():
    assert "’s Shelf</div>" in INDEX and "Public mixes</span>" in INDEX
    assert ".yf-shelf{" in INDEX
    # View more at the foot of each list, never beside its label.
    assert "function personMoreHTML(" in INDEX
    for which in ("mixes", "vibes", "friends"):
        assert f'personMoreHTML("{which}"' in INDEX
    assert "followHandle(" in INDEX and "openPersonFriend(" in INDEX
    assert 'plural(friendCount, "friend")' in INDEX


# --- 2. a vibe opens in the full player ----------------------------------------

def test_the_episode_on_a_story_opens_the_player():
    assert 'id="storyTitle" role="button" onclick="playStory()"' in INDEX
    assert "' role=\"button\" onclick=\"playStory()\"'" in INDEX
    assert ".story-canvas-wrap .sc-post{ pointer-events:auto;" in INDEX
    body = INDEX.split("function playStory(){", 1)[1].split("\n  }\n", 1)[0]
    assert 'navigate("player")' in body


# --- 3. the player's side buttons on a dark picture ----------------------------

def test_the_side_buttons_carry_their_own_contrast():
    rule = INDEX.split(".p-side .pt-icon{", 1)[1].split("}", 1)[0]
    assert "color:#F4EFE4" in rule
    cap = INDEX.split(".p-side .pt-icon .pt-cap{", 1)[1].split("}", 1)[0]
    assert "text-shadow" in cap and "color:#F4EFE4" in cap


# --- 4. Continue with nothing typed --------------------------------------------

def test_continue_with_nothing_typed_plays_the_suggestion():
    body = INDEX.split("function confirmGoDeeper(){", 1)[1].split("\n  }\n", 1)[0]
    assert "if(!val) val = (goDeeperSuggestionText() || \"\").trim();" in body
    assert body.index("goDeeperSuggestionText()") < body.index(".focus()")


# --- 5. private search, left on ------------------------------------------------

@pytest.fixture
def searcher(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "DEMO_MODE", False)
    monkeypatch.setattr(appmod, "SCRIPT_CACHE",
                        cache_mod.SqliteScriptCache(str(tmp_path / "e.db")))
    monkeypatch.setattr(
        appmod, "_make_pipeline",
        lambda voice=None, author="": PodcastPipeline(
            generator=FakeGenerator(ratio=1.0), engine=DebugEngine(),
            cache=appmod.SCRIPT_CACHE, voice=voice, author=author))
    with TestClient(appmod.app) as c:
        c.post("/api/auth/signup", json={"email": "p@b.com", "password": "password12"})
        yield c


def _plays(query):
    return appmod.EVENTS._conn().execute(
        "SELECT COUNT(*) FROM events WHERE kind = 'play' AND text = ?",
        (query,)).fetchone()[0]


def test_a_go_deeper_may_be_private(searcher):
    q = "why the tides lag the moon"
    res = searcher.get(f"/api/audio?q={q}&minutes=2&fmt=pcm&surface=other"
                       "&context=Tides&private=1")
    assert res.status_code == 200
    assert _plays(q) == 0
    assert all(e["query"] != q for e in appmod.SCRIPT_CACHE.recent())


def test_private_search_left_on_is_honoured_without_the_flag(searcher):
    saved = searcher.post("/api/preferences", json={"private_search": True}).json()
    assert saved["private_search"] is True
    assert searcher.get("/api/preferences").json()["private_search"] is True
    q = "how glaciers carve valleys"
    searcher.get(f"/api/audio?q={q}&minutes=1&fmt=pcm&surface=search")
    assert _plays(q) == 0, "a search with private search on was recorded"
    # A bank tile stays everybody's, whatever the switch says.
    tile = "the history of the paperclip"
    searcher.get(f"/api/audio?q={tile}&minutes=2&fmt=pcm&surface=myfam")
    assert _plays(tile) == 1
    # Off again: searches are recorded as ever.
    searcher.post("/api/preferences", json={"private_search": False})
    other = "how fjords are made"
    searcher.get(f"/api/audio?q={other}&minutes=1&fmt=pcm&surface=search")
    assert _plays(other) == 1


def test_private_search_is_one_switch_in_settings_and_on_search():
    assert 'settingsRow("Private search"' in INDEX
    assert "savePreferences({ private_search: privateSearch })" in INDEX
    assert "paintPrivateSearch(d.saved ? !!d.private_search" in INDEX
    # It no longer turns itself off after one search.
    send = INDEX.split("function sendSearch(", 1)[1].split("\n  }\n", 1)[0]
    assert "setPrivateSearch(false)" not in send
    follow = INDEX.split("function startFollowUp(", 1)[1].split("\n  }\n", 1)[0]
    assert "private: hush" in follow and "var hush = privateSearch;" in follow


# --- 6. a mix row titled by its specific -----------------------------------------

def test_a_followed_specific_is_titled_by_itself():
    item = mixes_mod.followed_item("f:startups~OpenAI")
    assert item.title == "OpenAI"
    assert item.subtitle == "Startups · new briefing every day"
    plain = mixes_mod.followed_item("f:startups")
    assert plain.title == "Startups"
