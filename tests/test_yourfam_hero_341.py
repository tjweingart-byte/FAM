"""YourFAM's hero layout (§341): the owner's pick from the ten-layout deck -
option 4's centred header, option 2's three numbers, and Vibes / Mixes as a
grid of picture tiles."""
from __future__ import annotations

import pathlib
import re
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


def test_every_vibe_and_mix_on_the_profile_carries_a_picture_field(client):
    client.post("/api/auth/signup", json={"email": "ian@b.com", "password": "password12"})
    client.post("/api/me", json={"name": "Ian", "handle": "ian"})
    client.post("/api/vibe", json={"query": "why bonds move", "minutes": 3,
                                   "title": "Bonds"})
    client.post("/api/mixes", json={"name": "Morning", "topic_ids": ["fed-next-move"]})
    body = client.get("/api/profile").json()
    assert body["echoes"] and all("thumb" in e for e in body["echoes"])
    assert all(isinstance(e["thumb"], str) for e in body["echoes"])
    assert all("thumb" in m for m in body["mixes"])


def test_a_picture_that_fails_is_an_empty_string(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("no pictures")
    monkeypatch.setattr(appmod, "_player_picture", boom)
    assert appmod._picture_url("why bonds move") == ""


def test_the_hero_then_three_numbers_then_the_buttons():
    body = _fn("renderProfile")
    hero, stats, acts = (body.index('class="yf-hero"'), body.index('class="yf-stats"'),
                         body.index('yf-acts yf-acts-centre'))
    assert hero < stats < acts < body.index("interestLineHTML(")
    # Each number is a door: friends to the Friends page, the other two to
    # their shelf in the grid.
    assert body.count('class="yf-stat"') == 3
    assert 'onclick="openFriends()"' in body
    assert "showProfileShelf(\\'vibes\\', true)" in body
    assert "showProfileShelf(\\'mixes\\', true)" in body
    # Worded for the number held.
    assert 'plural(friends, "friend")' in body and 'plural(vibes, "vibe")' in body
    assert '(mixCount === 1 ? "mix" : "mixes")' in body


def test_the_hub_cards_are_gone():
    body = _fn("renderProfile")
    for gone in ("yf-msgcard", "yf-shelf-tile", "View Your FAM", "Find new friends"):
        assert gone not in body, gone


def test_vibes_and_mixes_are_tabs_over_one_grid_of_tiles():
    body = _fn("renderProfile")
    assert body.index('data-shelf="vibes"') < body.index('data-shelf="mixes"')
    assert 'id="yfGrid"' in body
    shelf = _fn("showProfileShelf")
    assert 'profileTileHTML("playProfileVibe(" + i + ")", v.thumb' in shelf
    # A mix's own cover wins over the borrowed picture.
    assert "m.cover || m.thumb" in shelf
    # Empty shelves say so rather than draw placeholders.
    assert shelf.count('class="pf-empty"') == 2
    rule = re.search(r"\.yf-grid\{[^}]*\}", INDEX).group(0)
    assert "repeat(3, 1fr)" in rule
