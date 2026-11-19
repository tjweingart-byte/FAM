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


# --- mutuals on a profile (§657) ------------------------------------------

def test_a_profile_says_who_you_follow_follows_them_whether_or_not_you_do(client):
    signed_in(client, "ian@b.com", "Ian", "ian")
    client.post("/api/auth/logout")
    for email, name, handle in (("ana@b.com", "Ana", "ana"), ("cy@b.com", "Cy", "cy")):
        signed_in(client, email, name, handle)
        client.post("/api/friends/follow", json={"handle": "ian"})
        client.post("/api/auth/logout")
    signed_in(client, "ben@b.com", "Ben", "ben")
    client.post("/api/friends/follow", json={"handle": "ana"})
    client.post("/api/friends/follow", json={"handle": "cy"})

    page = client.get("/api/person?handle=ian").json()
    assert page["mutuals"]["count"] == 2, "Ben does not follow Ian, and still sees them"
    assert {p["handle"] for p in page["mutuals"]["people"]} == {"ana", "cy"}
    assert all("user_id" not in p for p in page["mutuals"]["people"])

    client.post("/api/friends/follow", json={"handle": "ian"})
    assert client.get("/api/person?handle=ian").json()["mutuals"]["count"] == 2


def test_nobody_you_do_not_follow_is_named_a_mutual(client):
    signed_in(client, "ian@b.com", "Ian", "ian")
    client.post("/api/auth/logout")
    signed_in(client, "ana@b.com", "Ana", "ana")
    client.post("/api/friends/follow", json={"handle": "ian"})
    client.post("/api/auth/logout")
    signed_in(client, "ben@b.com", "Ben", "ben")
    assert client.get("/api/person?handle=ian").json()["mutuals"] == {"count": 0, "people": []}


# --- §660: where follows come from, Follow all, "just joined", /@handle ---

def test_a_follow_remembers_where_it_was_tapped(graph):
    graph.follow("me", "a", via="contacts")
    graph.follow("me", "b", via="signup_suggested")
    graph.follow("me", "c", via="somewhere made up")
    graph.follow("me", "d")
    assert graph.follow_sources() == {"contacts": 1, "signup_suggested": 1, "other": 2}


def test_only_a_named_door_is_kept():
    assert social_mod.follow_via("signup_contacts_all") == "signup_contacts_all"
    assert social_mod.follow_via("signup_") == ""
    assert social_mod.follow_via("signup_nonsense") == ""
    assert social_mod.follow_via(" Contacts ") == "contacts"


def test_the_admin_can_ask_where_follows_came_from():
    import admin_tracker
    recipe = admin_tracker.match_recipe("Where did follows come from this week?")
    assert recipe is not None and recipe.key == "follow_sources"
    sql, _, label = recipe.build("where did follows come from this week?", 0)
    assert "GROUP BY" in sql and "at >= :now" in sql and "7 days" in label


def test_the_app_sends_where_and_the_store_keeps_it(client):
    signed_in(client, "ana@b.com", "Ana", "ana")
    client.post("/api/auth/logout")
    signed_in(client, "ben@b.com", "Ben", "ben")
    assert client.post("/api/friends/follow",
                       json={"handle": "ana", "via": "profile_link"}).status_code == 200
    assert appmod.SOCIAL.follow_sources().get("profile_link", 0) >= 1


def test_follow_all_is_one_request_and_skips_who_it_cannot(client):
    for email, name, handle in (("a1@b.com", "A1", "aone"), ("a2@b.com", "A2", "atwo"),
                                ("a3@b.com", "A3", "athree"), ("a4@b.com", "A4", "afour")):
        signed_in(client, email, name, handle)
        client.post("/api/auth/logout")
    me = signed_in(client, "ben@b.com", "Ben", "ben")
    r = client.post("/api/friends/follow-all", json={
        "handles": ["aone", "atwo", "athree", "afour", "nobody", "ben"], "via": "group"})
    assert r.status_code == 200
    assert r.json()["followed"] == 4 and r.json()["skipped"] == 2
    assert appmod.SOCIAL.follow_counts(me)["following"] == 4


def test_somebody_new_who_has_you_in_their_contacts_is_announced_once(client):
    signed_in(client, "ana@b.com", "Ana", "ana", phone="+14155550111")
    client.post("/api/auth/logout")
    signed_in(client, "ben@b.com", "Ben", "ben")
    client.post("/api/friends/contacts", json={"contacts": [{"name": "Ana", "tel": ["4155550111"]}]})
    client.post("/api/friends/contacts", json={"contacts": [{"name": "Ana", "tel": ["4155550111"]}]})
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": "ana@b.com", "password": "password12"})
    first = client.get("/api/friends/joined").json()["people"]
    assert [p["handle"] for p in first] == ["ben"], "Ana hears that Ben joined"
    again = client.get("/api/friends/joined").json()["people"]
    assert [p["handle"] for p in again] == ["ben"], "kept until the card has been shown"
    client.post("/api/friends/joined/seen", json={"user_id": first[0]["user_id"]})
    assert client.get("/api/friends/joined").json()["people"] == [], "and only once"


def test_an_old_account_checking_its_contacts_announces_nothing(client):
    signed_in(client, "ana@b.com", "Ana", "ana", phone="+14155550112")
    client.post("/api/auth/logout")
    ben = signed_in(client, "ben@b.com", "Ben", "ben")
    appmod.ACCOUNTS._conn().execute("UPDATE accounts SET created = 0 WHERE user_id = ?", (ben,))
    client.post("/api/friends/contacts", json={"contacts": [{"name": "Ana", "tel": ["4155550112"]}]})
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": "ana@b.com", "password": "password12"})
    assert client.get("/api/friends/joined").json()["people"] == []


def test_a_notice_is_never_about_somebody_you_follow_and_goes_with_an_account(graph):
    graph.add_joined_notice("me", "a")
    graph.add_joined_notice("me", "b")
    graph.forget("b")
    assert graph.joined_notices("me") == ["a"]
    assert graph.drop_joined_notice("me", "a")
    assert graph.joined_notices("me") == []


def test_a_profile_link_opens_the_app_on_that_profile(client):
    r = client.get("/@Ana.B", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/?person=ana.b"
    bad = client.get("/@x", follow_redirects=False)
    assert bad.status_code == 302 and bad.headers["location"] == "/"


def test_group_members_say_whether_you_follow_them(client):
    for email, name, handle in (("ana@b.com", "Ana", "ana"), ("cy@b.com", "Cy", "cy")):
        signed_in(client, email, name, handle)
        client.post("/api/auth/logout")
    me = signed_in(client, "ben@b.com", "Ben", "ben")
    ana, cy = appmod._person_by_handle("ana"), appmod._person_by_handle("cy")
    appmod.SOCIAL.follow(me, ana)
    gid = appmod.MESSAGES.create_group(me, [ana, cy])["id"]
    view = appmod._group_view(gid, me)
    assert {m["handle"]: m["following"] for m in view["members"] if not m["me"]} == {
        "ana": True, "cy": False}
