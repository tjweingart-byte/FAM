"""A friend's profile takes YourFAM's layout (§345): hero, three numbers,
centred buttons, interests, then Vibes / Mixes tabs over a grid of tiles."""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as appmod  # noqa: E402

INDEX = (ROOT / "static" / "index.html").read_text()


def _fn(name):
    start = INDEX.index(f"function {name}(")
    return INDEX[start:INDEX.index("\n  }\n", start)]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    with TestClient(appmod.app) as c:
        yield c


def test_a_friends_vibes_and_mixes_carry_a_picture_field(client):
    with TestClient(appmod.app) as beth:
        beth.post("/api/auth/signup", json={"email": "beth@b.com", "password": "password12"})
        beth.post("/api/me", json={"name": "Beth", "handle": "beth"})
        beth.post("/api/vibe", json={"query": "why bonds move", "minutes": 3, "title": "Bonds"})
        beth.post("/api/mixes", json={"name": "Morning", "topic_ids": ["fed-next-move"]})
    client.post("/api/auth/signup", json={"email": "ian@b.com", "password": "password12"})
    client.post("/api/me", json={"name": "Ian", "handle": "ian"})
    body = client.get("/api/person?handle=beth").json()
    assert body["vibes"] and all(isinstance(v["thumb"], str) for v in body["vibes"])
    assert body["mixes"] and all(isinstance(m["thumb"], str) for m in body["mixes"])
    assert "user_id" not in body


def test_the_same_order_as_your_own_page():
    body = _fn("renderFriendProfile")
    order = [body.index(mark) for mark in (
        'class="yf-hero"', 'class="yf-stats"', "yf-acts yf-acts-centre",
        "interestLineHTML(", 'class="yf-tabs"', 'id="personGrid"', 'id="personFriends"',
        "stays private")]
    assert order == sorted(order)
    for gone in ("yf-person-id", "’s Shelf", "yf-vrow\"", "yf-mix\""):
        assert gone not in body, gone


def test_the_friends_number_is_a_door_only_when_there_is_a_list():
    body = _fn("renderFriendProfile")
    assert "(theirFriends ? '<button class=\"yf-stat\" onclick=\"scrollToPersonFriends()\">'" in body
    assert "showPersonShelf(\\'vibes\\', true)" in body
    assert "showPersonShelf(\\'mixes\\', true)" in body


def test_the_grid_uses_yourfams_tiles_and_keeps_the_add_button():
    shelf = _fn("showPersonShelf")
    assert 'profileTileHTML("playPersonVibe(" + i + ")", v.thumb' in shelf
    assert "m.cover || m.thumb" in shelf
    assert 'mixAddButtonHTML(m, "person", i)' in shelf
    assert "#screen-person .scroll" in INDEX


def test_the_grid_says_nothing_before_the_profile_has_answered():
    shelf = _fn("showPersonShelf")
    assert shelf.index("if(!personPublished)") < shelf.index("'<div class=\"pf-empty\">Nothing vibed yet")
    # Each profile opens on Vibes, set before its first draw.
    opener = _fn("openPersonProfile")
    assert opener.index('personShelf = "vibes"') < opener.index("renderFriendProfile(")
