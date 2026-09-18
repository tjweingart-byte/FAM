"""The follow graph, and sending an episode to somebody inside the app."""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod
import messages as messages_mod
import social as social_mod


@pytest.fixture
def store(tmp_path):
    return messages_mod.MessageStore(str(tmp_path / "messages.db"))


@pytest.fixture
def graph(tmp_path):
    social = social_mod.SocialStore(str(tmp_path / "social.db"))
    social.set_person("a", "Ana", "ana")
    social.set_person("b", "Ben", "ben")
    social.set_person("c", "Cy", "cy")
    return social


# --- the follow graph -----------------------------------------------------

def test_following_is_one_sided_and_friendship_is_the_mutual_case(graph):
    """Asymmetric like the copy already says, and mutuals derived rather than
    stored - so there is no accept step to get wrong and no way for the two
    directions to disagree."""
    graph.follow("a", "b")
    assert graph.is_following("a", "b") and not graph.is_following("b", "a")
    assert graph.friends("a") == []
    graph.follow("b", "a")
    assert [p["handle"] for p in graph.friends("a")] == ["ben"]


def test_following_twice_changes_nothing(graph):
    assert graph.follow("a", "b") is True
    assert graph.follow("a", "b") is False


def test_nobody_can_follow_themselves(graph):
    """Not a moral position: friends is the intersection of following and
    followers, so a self-follow puts everybody in their own circle."""
    with pytest.raises(social_mod.SocialError):
        graph.follow("a", "a")


def test_counts_are_derived_rather_than_kept(graph):
    """A denormalised counter is a number that can be wrong, and this app has
    a rule about inventing numbers on a profile page."""
    graph.follow("a", "b")
    graph.follow("c", "b")
    assert graph.follow_counts("b") == {"following": 0, "followers": 2}


def test_people_can_be_found_by_handle_or_name(graph):
    assert [p["handle"] for p in graph.find_people("be")] == ["ben"]
    assert [p["handle"] for p in graph.find_people("An")] == ["ana"]


def test_a_one_letter_search_returns_nothing(graph):
    """It would return most of the listener table, which is a directory dump
    rather than a search."""
    assert graph.find_people("a") == []


def test_somebody_with_no_handle_is_not_in_the_directory(graph):
    graph.seen("ghost")
    assert graph.find_people("gh") == []


def test_deleting_a_listener_removes_them_from_both_directions(graph):
    """Otherwise they stay in somebody else's follower count, pointing at an id
    that no longer resolves to a person."""
    graph.follow("a", "b")
    graph.follow("b", "a")
    graph.forget("b")
    assert graph.follow_counts("a") == {"following": 0, "followers": 0}


# --- messages -------------------------------------------------------------

def test_a_thread_id_is_the_same_from_both_sides(store):
    """Derived rather than allocated, so two people opening the conversation at
    once cannot create two threads and split the history."""
    assert messages_mod.thread_id("a", "b") == messages_mod.thread_id("b", "a")


def test_you_cannot_message_yourself(store):
    with pytest.raises(messages_mod.MessageError):
        store.send("a", "a", text="hello")


def test_an_episode_share_carries_the_question_and_not_the_audio(store):
    """The whole cost design: a share is a row pointing at a query whose script
    already exists, so sending an episode to ten people costs ten rows and not
    ten episodes."""
    message = store.send("a", "b", kind="episode", query="why bonds move",
                         minutes=3, title="Bonds")
    assert message.query == "why bonds move" and message.minutes == 3
    assert message.text == ""


def test_an_episode_share_needs_a_question(store):
    with pytest.raises(messages_mod.MessageError):
        store.send("a", "b", kind="episode", query="")


def test_an_empty_text_message_is_refused(store):
    with pytest.raises(messages_mod.MessageError):
        store.send("a", "b", text="   ")


def test_a_thread_reads_oldest_first(store):
    store.send("a", "b", text="first", at=100)
    store.send("b", "a", text="second", at=200)
    assert [m.text for m in store.thread("a", "b")] == ["first", "second"]


def test_the_inbox_shows_the_last_message_per_conversation(store):
    store.send("a", "b", text="old", at=100)
    store.send("a", "b", text="new", at=200)
    store.send("a", "c", text="elsewhere", at=150)
    inbox = store.inbox("a")
    assert len(inbox) == 2
    assert inbox[0]["last"]["text"] == "new"


def test_unread_counts_only_what_arrived_for_you(store):
    store.send("a", "b", text="hello")
    assert store.unread_total("b") == 1
    assert store.unread_total("a") == 0


def test_opening_a_thread_marks_it_read(store):
    store.send("a", "b", text="hello", at=100)
    store.mark_read("b", "a", at=200)
    assert store.unread_total("b") == 0


def test_a_message_arriving_after_a_read_is_unread_again(store):
    store.send("a", "b", text="hello", at=100)
    store.mark_read("b", "a", at=200)
    store.send("a", "b", text="again", at=300)
    assert store.unread_total("b") == 1


def test_mine_is_reported_rather_than_the_senders_id(store):
    """The client needs to know which side to draw the bubble on, and does not
    need somebody else's listener id to do it."""
    message = store.send("a", "b", text="hello")
    assert message.as_dict("a")["mine"] is True
    assert "sender" not in message.as_dict("b")


def test_deleting_a_listener_removes_their_side_of_a_conversation(store):
    """The other person's messages stay: those are their words, not this
    listener's data."""
    store.send("a", "b", text="mine")
    store.send("b", "a", text="theirs")
    store.forget("a")
    assert [m.text for m in store.thread("a", "b")] == ["theirs"]


# --- through the app ------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    with TestClient(appmod.app) as c:
        yield c


def signed_in(client, email, name, handle):
    client.post("/api/auth/signup", json={"email": email, "password": "password12"})
    client.post("/api/me", json={"name": name, "handle": handle})
    return client.get("/api/auth/me").json()["user_id"]


def test_two_listeners_can_find_follow_and_message_each_other(client):
    ana = signed_in(client, "ana@b.com", "Ana", "ana")
    client.post("/api/auth/logout")
    signed_in(client, "ben@b.com", "Ben", "ben")

    found = client.get("/api/people?q=ana").json()["people"]
    assert [p["handle"] for p in found] == ["ana"]
    assert found[0]["following"] is False

    assert client.post("/api/friends/follow",
                       json={"handle": "ana"}).json()["counts"]["following"] == 1
    sent = client.post("/api/messages", json={
        "to": ana, "query": "why bonds move", "minutes": 3, "title": "Bonds"})
    assert sent.status_code == 200, sent.text
    assert sent.json()["message"]["kind"] == "episode"

    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": "ana@b.com", "password": "password12"})
    inbox = client.get("/api/messages").json()
    assert inbox["unread"] == 1
    assert inbox["threads"][0]["name"] == "Ben"


def test_a_search_never_returns_yourself(client):
    signed_in(client, "ana@b.com", "Ana", "ana")
    assert client.get("/api/people?q=ana").json()["people"] == []


def test_following_somebody_who_does_not_exist_is_a_404(client):
    signed_in(client, "ana@b.com", "Ana", "ana")
    assert client.post("/api/friends/follow",
                       json={"handle": "nobody"}).status_code == 404


def test_friends_need_an_account(client):
    assert client.get("/api/friends").status_code == 401
    assert client.get("/api/messages").status_code == 401


# --- new followers, and what clears them ----------------------------------
#
# There is no push and will not be until the app exists, so "___ started
# following you" is answered the next time this listener's own app asks. The
# question is a query over the follow graph's own timestamps against one column
# saying when they last looked - not a second table with its own read state.


def test_a_new_follower_is_reported_until_the_tab_is_opened(client):
    me = signed_in(client, "ian@b.com", "Ian", "ian")
    nadia = TestClient(appmod.app)
    with nadia:
        signed_in(nadia, "nadia@b.com", "Nadia", "nadia")
        nadia.post("/api/friends/follow", json={"user_id": me})

    fresh = client.get("/api/friends").json()["new_followers"]
    assert [p["handle"] for p in fresh] == ["nadia"]
    assert fresh[0]["follows_back"] is False, \
        "Follow back was not offered to somebody who is not followed"

    # Asking again does not clear it: a badge that cleared itself the moment
    # something drew it would be a count nobody got to read.
    assert len(client.get("/api/friends").json()["new_followers"]) == 1

    client.post("/api/friends/seen")
    assert client.get("/api/friends").json()["new_followers"] == []


def test_somebody_already_followed_is_not_offered_a_follow_back(client):
    """Offering it there is a control that cannot do anything."""
    me = signed_in(client, "ian@b.com", "Ian", "ian")
    beth = TestClient(appmod.app)
    with beth:
        her = signed_in(beth, "beth@b.com", "Beth", "beth")
        client.post("/api/friends/follow", json={"user_id": her})
        beth.post("/api/friends/follow", json={"user_id": me})
    fresh = client.get("/api/friends").json()["new_followers"]
    assert [p["follows_back"] for p in fresh] == [True]


def test_a_follower_arriving_after_a_look_is_new_again(client):
    me = signed_in(client, "ian@b.com", "Ian", "ian")
    first = TestClient(appmod.app)
    with first:
        signed_in(first, "beth@b.com", "Beth", "beth")
        first.post("/api/friends/follow", json={"user_id": me})
    client.post("/api/friends/seen")
    assert client.get("/api/friends").json()["new_followers"] == []

    later = TestClient(appmod.app)
    with later:
        signed_in(later, "nadia@b.com", "Nadia", "nadia")
        later.post("/api/friends/follow", json={"user_id": me})
    assert [p["handle"] for p in
            client.get("/api/friends").json()["new_followers"]] == ["nadia"]


# --- another listener's profile -------------------------------------------
#
# Only what they chose to publish. What somebody has listened to is theirs,
# and this endpoint exists precisely because that line needed drawing in code
# rather than by having no endpoint at all.


def test_a_person_profile_carries_only_what_they_published(client):
    signed_in(client, "ian@b.com", "Ian", "ian")
    beth = TestClient(appmod.app)
    with beth:
        her = signed_in(beth, "beth@b.com", "Beth", "beth")
        beth.post("/api/preferences", json={"interests": ["tech", "sports"]})
        mix = beth.post("/api/mixes", json={"name": "Morning",
                                            "topic_ids": ["ai-agents"]}).json()
        beth.patch(f"/api/mixes/{mix['id']}", json={"public": True})
        beth.post("/api/mixes", json={"name": "Private one",
                                      "topic_ids": ["fed-next-move"]})
        beth.post("/api/vibe", json={"query": "why bonds move", "title": "Bonds",
                                     "minutes": 2})
        # Behaviour, which must not reach anybody else.
        for _ in range(4):
            beth.post("/api/event", json={"kind": "complete",
                                          "topic_id": "ai-agents"})
        client.post("/api/friends/follow", json={"user_id": her})

    body = client.get("/api/person?handle=beth").json()
    assert body["name"] == "Beth" and body["handle"] == "beth"
    assert [m["name"] for m in body["mixes"]] == ["Morning"], \
        "a private mix reached somebody else's screen"
    assert [v["title"] for v in body["vibes"]] == ["Bonds"]
    assert body["vibe_count"] == 1
    assert set(body["interests"]) == {"tech", "sports"}

    # The line this endpoint exists to keep.
    for leaked in ("played", "finished", "searched", "open_threads",
                   "subjects", "user_id", "listener"):
        assert leaked not in body, f"{leaked!r} reached another listener"


def test_a_hidden_interest_does_not_reach_another_listener(client):
    """"Which topics they choose to publicly share", from the edit screen. The
    hidden set is stored rather than the shared one, so an existing row means
    "all of them" - the alternative defaults every profile in the app to an
    empty pill row that reads as broken."""
    signed_in(client, "ian@b.com", "Ian", "ian")
    beth = TestClient(appmod.app)
    with beth:
        her = signed_in(beth, "beth@b.com", "Beth", "beth")
        beth.post("/api/preferences", json={"interests": ["tech", "sports", "money"]})
        beth.post("/api/preferences", json={"hidden_interests": ["sports"]})
        client.post("/api/friends/follow", json={"user_id": her})
        # Their own screen still shows all of them: hiding is about the
        # profile, not about the ranker or the editor.
        theirs = beth.get("/api/preferences").json()
        assert set(theirs["interests"]) == {"tech", "sports", "money"}
        assert theirs["hidden_interests"] == ["sports"]

    body = client.get("/api/person?handle=beth").json()
    assert set(body["interests"]) == {"tech", "money"}
    assert "sports" not in body["interests"]


def test_a_person_nobody_can_find_is_a_404(client):
    signed_in(client, "ian@b.com", "Ian", "ian")
    assert client.get("/api/person?handle=nobody").status_code == 404
    # And an id alone is not a way in: the graph is the boundary.
    assert client.get("/api/person?user_id=anon_made_up").status_code == 404
