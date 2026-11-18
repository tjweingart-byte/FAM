"""Finding friends (§656): your contacts on FAM, and "Suggested for you"."""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import accounts as accounts_mod
import app as appmod
import social as social_mod


@pytest.fixture
def graph(tmp_path):
    social = social_mod.SocialStore(str(tmp_path / "social.db"))
    for uid, name in (("me", "Me"), ("ian", "Ian"), ("a", "Ana"), ("b", "Ben"),
                      ("c", "Cy"), ("d", "Di")):
        social.set_person(uid, name, name.lower())
    return social


# --- suggestions in the store ---------------------------------------------

def test_after_a_follow_the_suggestions_are_who_that_person_follows(graph):
    graph.follow("ian", "a")
    graph.follow("ian", "b")
    graph.follow("ian", "nohandle")   # never set a handle: not findable
    graph.follow("me", "ian")
    found = graph.suggestions("me", around="ian")
    assert {p["handle"] for p in found} == {"ana", "ben"}
    assert all(p["reason"] for p in found)


def test_suggestions_rank_by_mutuals_and_never_repeat_who_you_follow(graph):
    graph.follow("ian", "a")
    graph.follow("ian", "b")
    graph.follow("ian", "c")
    graph.follow("me", "ian")
    graph.follow("me", "d")
    graph.follow("d", "b")          # one of mine follows Ben too
    graph.follow("me", "c")         # already followed: never suggested
    found = graph.suggestions("me", around="ian")
    assert [p["handle"] for p in found] == ["ben", "ana"]
    assert found[0]["mutuals"] == 2 and found[0]["reason"] == "2 mutuals"
    assert found[1]["reason"] == "1 mutual"


def test_discover_puts_who_follows_you_first(graph):
    graph.follow("me", "ian")
    graph.follow("ian", "a")
    graph.follow("b", "me")
    found = graph.suggestions("me")
    assert [p["handle"] for p in found] == ["ben", "ana"]
    assert found[0]["reason"] == "Follows you"


def test_a_new_listener_has_nobody_suggested_rather_than_strangers(graph):
    assert graph.suggestions("me") == []


def test_the_reason_is_worded_for_its_number():
    assert social_mod.suggestion_reason(0, False) == "Suggested for you"
    assert social_mod.suggestion_reason(1, False) == "1 mutual"
    assert social_mod.suggestion_reason(4, False) == "4 mutuals"
    assert social_mod.suggestion_reason(4, True) == "Follows you"


# --- contact matching in the account store --------------------------------

@pytest.fixture
def accounts(tmp_path):
    store = accounts_mod.AccountStore(str(tmp_path / "accounts.db"))
    store.sign_up("u1", "ana@example.com", "password12", phone="+14155550123")
    store.sign_up("u2", "ben@example.com", "password12")
    return store


def test_contacts_match_by_phone_however_it_was_saved(accounts):
    for saved in ("+1 (415) 555-0123", "(415) 555-0123", "1-415-555-0123", "0014155550123"):
        assert accounts.match_contacts([(saved, 7)], []) == {"u1": 7}, saved


def test_contacts_match_by_email_in_any_case(accounts):
    assert accounts.match_contacts([], [("Ben@Example.com", 2)]) == {"u2": 2}


def test_a_national_number_is_not_guessed_outside_plus_one(accounts):
    assert accounts.match_contacts([("(415) 555-0123", 0)], [], home_code="") == {}
    assert accounts.match_contacts([("+14155550123", 0)], [], home_code="") == {"u1": 0}


def test_junk_in_an_address_book_matches_nobody(accounts):
    assert accounts.match_contacts([("", 0), ("call me", 1), ("12", 2)],
                                   [("not an email", 3)]) == {}


# --- through the app ------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    with TestClient(appmod.app) as c:
        yield c


def signed_in(client, email, name, handle, phone=""):
    body = {"email": email, "password": "password12"}
    client.post("/api/auth/signup", json=body)
    client.post("/api/me", json={"name": name, "handle": handle})
    uid = client.get("/api/auth/me").json()["user_id"]
    if phone:
        appmod.ACCOUNTS._conn().execute(
            "UPDATE accounts SET phone = ? WHERE user_id = ?", (phone, uid))
    return uid


def test_contacts_on_fam_come_back_with_the_name_in_your_address_book(client):
    signed_in(client, "ana@b.com", "Ana", "ana", phone="+14155550199")
    client.post("/api/auth/logout")
    signed_in(client, "ben@b.com", "Ben", "ben")
    r = client.post("/api/friends/contacts", json={"contacts": [
        {"name": "Nobody", "tel": ["+15550000000"]},
        {"name": "Ana from work", "tel": ["(415) 555-0199"]},
        {"name": "Me", "email": ["ben@b.com"]},
    ]})
    assert r.status_code == 200
    people = r.json()["people"]
    assert [(p["handle"], p["contact_name"], p["following"]) for p in people] == [
        ("ana", "Ana from work", False)]


def test_contacts_need_an_account(client):
    assert client.post("/api/friends/contacts", json={"contacts": []}).status_code == 401


def test_the_row_under_follow_is_only_for_somebody_you_follow(client):
    signed_in(client, "cy@b.com", "Cy", "cy")
    client.post("/api/auth/logout")
    signed_in(client, "ana@b.com", "Ana", "ana")
    client.post("/api/friends/follow", json={"handle": "cy"})
    client.post("/api/auth/logout")
    signed_in(client, "ben@b.com", "Ben", "ben")

    before = client.get("/api/friends/suggested?handle=ana").json()
    assert before["people"] == [], "not followed yet: Ana's graph is not Ben's to read"
    client.post("/api/friends/follow", json={"handle": "ana"})
    after = client.get("/api/friends/suggested?handle=ana").json()
    assert after["around"] is True
    assert [p["handle"] for p in after["people"]] == ["cy"]
    assert after["people"][0]["following"] is False
