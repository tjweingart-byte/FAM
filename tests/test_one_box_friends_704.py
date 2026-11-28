"""§704: three notes from testers.

1. "Where are you" is one box, with the places it could be listed under it.
2. Find your friends is out of the initial set-up.
3. The Friends page is Instagram's order, and the suggestion cards are
   drawn there, as rows, and nowhere else.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as appmod
import places

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    start = INDEX.index("function " + name + "(")
    return INDEX[start:INDEX.index("\n  }\n", start)]


CATALOGUE = [
    {"name": "Huntington Beach", "admin1": "California", "country": "United States",
     "country_code": "US", "feature_code": "PPL", "population": 200000},
    {"name": "Huntington", "admin1": "West Virginia", "country": "United States",
     "country_code": "US", "feature_code": "PPLA2", "population": 46000},
    {"name": "Huntington", "admin1": "West Virginia", "country": "United States",
     "country_code": "US", "feature_code": "PPL", "population": 100},
    {"name": "Huntington", "admin1": "England", "country": "United Kingdom",
     "country_code": "GB", "feature_code": "PPL", "population": 12000},
    {"name": "Huntington Lake", "admin1": "California", "country": "United States",
     "country_code": "US", "feature_code": "LK"},
]


@pytest.fixture
def catalogue(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_NEWS_DB", str(tmp_path / "local.db"))
    places.reset_store()
    places._asked_today.clear()
    asked = []

    async def ask(name, count=10, timeout=4.0):
        asked.append(name)
        return list(CATALOGUE)

    monkeypatch.setattr(places, "geocoder_available", lambda: (True, "test"))
    monkeypatch.setattr(places, "_ask_catalogue", ask)
    yield asked
    places.reset_store()


def suggest(text, user="u"):
    return asyncio.run(places.suggest(text, user))


# --- 1. one box -------------------------------------------------------------

def test_typing_a_town_offers_it_with_its_region_and_country(catalogue):
    found = suggest("Hunt")
    assert found[0] == {"city": "Huntington Beach", "region": "California",
                        "country": "United States",
                        "label": "Huntington Beach, California, United States"}
    labels = [p["label"] for p in found]
    # A town once, however many rows the catalogue has for it; a lake never.
    assert labels.count("Huntington, West Virginia, United States") == 1
    assert not any("Lake" in label for label in labels)
    assert len(found) <= places.SUGGEST_MAX


def test_what_follows_a_comma_narrows_the_places(catalogue):
    assert [p["region"] for p in suggest("Huntington, Eng")] == ["England"]
    # "CA" is California, as `split_name` reads it.
    assert [p["city"] for p in suggest("Huntington Beach, ca")] == ["Huntington Beach"]


def test_the_same_letters_are_asked_of_the_catalogue_once(catalogue):
    suggest("Hunt")
    suggest("hunt ")
    suggest("Hunt, Cal")
    assert catalogue == ["Hunt"]


def test_one_letter_asks_nothing(catalogue):
    assert suggest("H") == []
    assert catalogue == []


def test_a_days_ceiling_stops_the_asking_not_the_box(catalogue, monkeypatch):
    monkeypatch.setattr(places, "SUGGEST_PER_DAY", 1)
    suggest("Hunt")
    assert suggest("Bost") == []        # nothing held, nothing asked
    assert catalogue == ["Hunt"]
    assert suggest("Hunt")              # what is held still answers


def test_no_catalogue_is_an_empty_list_never_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_NEWS_DB", str(tmp_path / "local.db"))
    places.reset_store()
    monkeypatch.setattr(places, "geocoder_available", lambda: (False, "off"))
    assert suggest("Huntington") == []
    # And a failed call is not remembered as "no such place".
    calls = []

    async def failing(name, count=10, timeout=4.0):
        calls.append(name)
        return None

    monkeypatch.setattr(places, "geocoder_available", lambda: (True, "test"))
    monkeypatch.setattr(places, "_ask_catalogue", failing)
    places._asked_today.clear()
    suggest("Huntington")
    suggest("Huntington")
    assert calls == ["Huntington", "Huntington"]
    places.reset_store()


def test_the_endpoint_needs_an_account_and_answers_one(catalogue):
    with TestClient(appmod.app) as client:
        assert client.get("/api/places/suggest?q=Hunt").status_code in (401, 403)
        client.post("/api/auth/signup",
                    json={"email": "place704@example.com", "password": "password12"})
        reply = client.get("/api/places/suggest?q=Hunt")
        assert reply.status_code == 200
        assert reply.json()["places"][0]["label"] == \
            "Huntington Beach, California, United States"
        # The iPhone's address for it.
        assert client.get("/api/v1/places/suggest?q=Hunt").json() == reply.json()


def test_the_privacy_policy_says_where_the_typed_letters_go():
    page = (ROOT / "pages" / "privacy.html").read_text(encoding="utf-8")
    assert 'letters you type into "Where you are"' in page


def test_the_web_asks_where_you_are_in_one_box():
    for gone in ("identityCity", "identityRegion", "identityCountry",
                 "locCity", "locRegion", "locCountry"):
        assert gone not in INDEX
    assert INDEX.count('oninput="placeTyped(') == 2
    assert "/api/places/suggest?q=" in _fn("placeTyped")
    # Saved as the three values the server has always kept.
    assert 'savePreferences(placeValue("identityPlace"))' in INDEX
    assert 'savePreferences(placeValue("locPlace"))' in INDEX
    # Nothing picked: what was typed, by its commas.
    assert 'text.split(",")' in _fn("placeValue")


# --- 2. sign-up is three steps ----------------------------------------------

def test_find_your_friends_is_not_a_sign_up_step():
    assert 'id="screen-findfriends"' not in INDEX
    assert "afterFindFriends" not in INDEX and "openFindFriendsStep" not in INDEX
    assert "of 3</div>" in INDEX and "of 4</div>" not in INDEX
    steps = _fn("firstRunStep")
    assert 'introMode === "first-run" ? 3 : 0' in steps
    assert "openInterestsStep()" in _fn("afterIdentity")
    # A profile link opened before the account opens when sign-up ends.
    assert "openLinkedPerson" in _fn("finishIntro")


# --- 3. the Friends page, and the cards nowhere else --------------------------

def test_the_friends_page_is_search_then_tabs_then_people_then_suggestions():
    screen = INDEX.split('id="screen-friends"', 1)[1].split("</section>", 1)[0]
    order = [screen.index(mark) for mark in
             ('class="fr-search"', 'id="friendsBody"', 'id="friendsFind"')]
    assert order == sorted(order)
    foot = _fn("paintFindFriends")
    parts = [foot.index(mark) for mark in
             ("suggestRowsHTML()", "contactsBlockHTML()", "profileLinkHTML()", "inviteFriends()")]
    assert parts == sorted(parts)


def test_suggestions_are_rows_and_only_on_the_friends_page():
    rows = _fn("suggestRowsHTML")
    assert "personRow(" in rows and "dismissSuggested(" in rows
    assert 'data-via="suggested"' in rows
    for gone in ("sg-card", '"sg-row"', "suggestCardsHTML", "hubDiscoverHTML",
                 "personSuggestHTML", "loadPersonSuggestions", "Discover people</"):
        assert gone not in INDEX, gone
    assert INDEX.count("suggestRowsHTML()") == 2          # defined, drawn once
    # dailyFAM's friends card keeps its count and its door, and lists nobody.
    card = _fn("friendsRailFindHTML")
    assert "personRow(" not in card and "openFriends()" in card
    assert not re.search(r"loadDiscoverPeople\(", _fn("paintFriendsRailFind"))
