"""YourFAM: the tab that replaced Profile (the handoff's tab spec).

The interface draws; these pin what the server hands it, because every screen
in the tab is built on data the server already had - the follow graph, the
inbox, the vibes - and a hub that invented any of it would be the social
surface this project has a rule against.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

import app as appmod
import preferences as prefs_mod
import topics as topics_mod


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    with TestClient(appmod.app) as c:
        yield c


def signed_in(client, email, name, handle):
    client.post("/api/auth/signup", json={"email": email, "password": "password12"})
    client.post("/api/me", json={"name": name, "handle": handle})
    return client.get("/api/auth/me").json()["user_id"]


# --- the interest cap -----------------------------------------------------

def test_a_profile_shows_up_to_five_interests():
    """Five, at the owner's direction in the handoff, and both halves agree."""
    assert prefs_mod.PROFILE_INTERESTS_MAX == 5
    assert topics_mod.PROFILE_INTEREST_SLOTS == 5


# --- the hub's avatar row -------------------------------------------------

def test_the_circle_is_friends_first_and_lit_only_by_what_is_stored(client):
    me = signed_in(client, "ian@b.com", "Ian", "ian")
    beth = TestClient(appmod.app)
    with beth:
        her = signed_in(beth, "beth@b.com", "Beth", "beth")
        beth.post("/api/friends/follow", json={"user_id": me})
        # A vibe is something she chose to show, so it may light her face.
        beth.post("/api/vibe", json={"query": "why bonds move", "minutes": 3,
                                     "title": "Bonds"})
    mike = TestClient(appmod.app)
    with mike:
        him = signed_in(mike, "mike@b.com", "Mike", "mike")
    client.post("/api/friends/follow", json={"user_id": him})
    client.post("/api/friends/follow", json={"user_id": her})

    circle = client.get("/api/profile").json()["circle"]
    assert [p["handle"] for p in circle] == ["beth", "mike"], \
        "a friend did not come before somebody merely followed"
    by = {p["handle"]: p for p in circle}
    assert by["beth"]["friend"] is True and by["mike"]["friend"] is False
    assert by["beth"]["vibed"] and by["beth"]["fresh"]
    assert not by["mike"]["vibed"] and not by["mike"]["fresh"], \
        "somebody who did nothing was drawn with a story ring"


def test_an_unread_message_lights_the_senders_ring(client):
    me = signed_in(client, "ian@b.com", "Ian", "ian")
    tj = TestClient(appmod.app)
    with tj:
        his = signed_in(tj, "tj@b.com", "TJ Weingart", "tj")
        tj.post("/api/messages", json={"to": me, "text": "listen to this"})
    client.post("/api/friends/follow", json={"user_id": his})
    row = client.get("/api/profile").json()["circle"][0]
    assert row["fresh"] is True and row["vibed"] is False


def test_nobody_in_the_graph_is_an_empty_circle_not_strangers(client):
    signed_in(client, "ian@b.com", "Ian", "ian")
    assert client.get("/api/profile").json()["circle"] == []


# --- messages say what was shared ------------------------------------------

def test_a_shared_episode_is_previewed_by_its_subject(client):
    me = signed_in(client, "ian@b.com", "Ian", "ian")
    tj = TestClient(appmod.app)
    with tj:
        signed_in(tj, "tj@b.com", "TJ", "tj")
        tj.post("/api/messages", json={
            "to": me, "query": "what the fed rate decision means for the stock market",
            "minutes": 7, "title": "The rate decision"})
    last = client.get("/api/messages").json()["threads"][0]["last"]
    assert last["kind"] == "episode"
    assert last["topic"] == topics_mod.TAG_LABELS["money"]


def test_you_finished_it_is_read_off_your_own_completions(client):
    me = signed_in(client, "ian@b.com", "Ian", "ian")
    tj = TestClient(appmod.app)
    with tj:
        his = signed_in(tj, "tj@b.com", "TJ", "tj")
        tj.post("/api/messages", json={"to": me, "query": "why bonds move",
                                       "minutes": 3, "title": "Bonds"})
    first = client.get(f"/api/messages/thread?with={his}").json()["messages"][0]
    assert first["finished"] is False, "a receipt was drawn for nothing heard"
    client.post("/api/event", json={"kind": "complete", "text": "why bonds move"})
    again = client.get(f"/api/messages/thread?with={his}").json()["messages"][0]
    assert again["finished"] is True


# --- the Topic screen -----------------------------------------------------

def test_an_interest_page_generates_nothing_and_ends_on_the_bank(client):
    """Every card is something that already exists; the bank is the tail."""
    body = client.get("/api/interest?id=tech&limit=24").json()
    assert body["label"] == topics_mod.TAG_LABELS["tech"]
    assert body["episodes"], "the tech page was empty on a bank full of tech"
    bank = {t.query for t in topics_mod.TOPIC_BANK if "tech" in topics_mod.topic_tags(t)}
    assert {c["query"] for c in body["episodes"] if c["source"] == "bank"} <= bank
    assert all(c["source"] in ("story", "cache", "bank") for c in body["episodes"])


def test_an_interest_page_is_paged(client):
    first = client.get("/api/interest?id=tech&limit=2").json()
    assert len(first["episodes"]) == 2 and first["more"] is True
    second = client.get("/api/interest?id=tech&limit=2&offset=2").json()
    assert not ({c["query"] for c in first["episodes"]}
                & {c["query"] for c in second["episodes"]})


def test_a_typed_interest_is_matched_on_its_words(client):
    """Something the vocabulary cannot see still gets a page, not an error."""
    body = client.get("/api/interest?id=Surfing&label=Surfing").json()
    assert body["label"] == "Surfing"
    assert body["episodes"] == [] and body["reason"]


def test_friends_vibed_says_why_it_is_empty_without_a_graph(client):
    signed_in(client, "ian@b.com", "Ian", "ian")
    body = client.get("/api/interest?id=money&filter=friends").json()
    assert body["episodes"] == []
    assert "Follow" in body["reason"]


def test_friends_vibed_lists_what_the_circle_vibed_on_the_subject(client):
    me = signed_in(client, "ian@b.com", "Ian", "ian")
    beth = TestClient(appmod.app)
    with beth:
        her = signed_in(beth, "beth@b.com", "Beth", "beth")
        beth.post("/api/vibe", json={
            "query": "what the fed rate decision means for the stock market",
            "minutes": 3, "title": "The rate decision"})
        beth.post("/api/vibe", json={"query": "how sourdough starters work",
                                     "minutes": 3, "title": "Sourdough"})
    client.post("/api/friends/follow", json={"user_id": her})
    body = client.get("/api/interest?id=money&filter=friends").json()
    assert [c["title"] for c in body["episodes"]] == ["The rate decision"]
    assert body["episodes"][0]["vibed_by"]["handle"] == "beth"


# --- the interface keeps the spec's shape ----------------------------------

def _interface() -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "static", "index.html"), encoding="utf-8") as fh:
        return fh.read()


def test_the_last_tab_is_yourfam_on_every_tab_bar():
    page = _interface()
    assert page.count('<div class="lbl">YourFAM</div>') == 5
    assert '<div class="lbl">Profile</div>' not in page


def test_what_came_off_the_hub_stays_off_it():
    """The spec's "removed on purpose, so don't bring these back"."""
    page = _interface()
    start = page.index("  function renderProfile(p){")
    end = page.index("  function loadHubMessages(){")
    body = page[start:end]
    assert "This month" not in body
    assert "thread-row" not in body
    assert "From your FAM" not in body


def test_the_chat_composer_has_no_vibe_button():
    """Sending is just sending: VIBE! in the composer mixed a share with a vibe."""
    page = _interface()
    start = page.index('<section class="screen" id="screen-thread">')
    end = page.index("</section>", start)
    assert "VIBE" not in page[start:end].upper().replace("VIBES", "")


def test_two_friends_vibing_the_same_episode_both_get_their_badge(client):
    """One row per episode would keep only the later of the two."""
    me = signed_in(client, "ian@b.com", "Ian", "ian")
    ids = []
    for email, name, handle in (("beth@b.com", "Beth", "beth"),
                                ("mike@b.com", "Mike", "mike")):
        other = TestClient(appmod.app)
        with other:
            ids.append(signed_in(other, email, name, handle))
            other.post("/api/vibe", json={"query": "why bonds move",
                                          "minutes": 3, "title": "Bonds"})
    for uid in ids:
        client.post("/api/friends/follow", json={"user_id": uid})
    circle = client.get("/api/profile").json()["circle"]
    assert all(p["vibed"] for p in circle), circle


def test_edit_profile_never_replaces_topics_with_a_list_it_never_loaded():
    """`topics` is replaced wholesale, so appending to an unloaded list would
    wipe every topic somebody chose."""
    page = _interface()
    start = page.index("  function saveIdentityInterests(){")
    body = page[start:page.index("\n  }\n", start)]
    assert "Array.isArray(PREF_CHOICES.topics)" in body
    assert "PREF_CHOICES.topics) || []" not in body
