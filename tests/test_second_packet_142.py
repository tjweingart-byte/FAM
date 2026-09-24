"""§142, the 9.23 second packet: messages, history, autocorrect, follows.

Each test names the complaint it answers, because every one of these was a
behaviour the owner hit on a phone rather than a failure anything reported.
"""
from __future__ import annotations

import os
import re
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod
import autocorrect
import messages as messages_mod
import saved as saved_mod
import social as social_mod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()


@pytest.fixture
def store(tmp_path):
    return messages_mod.MessageStore(str(tmp_path / "messages.db"))


@pytest.fixture
def shelf(tmp_path):
    return saved_mod.SavedStore(str(tmp_path / "saved.db"))


@pytest.fixture
def graph(tmp_path):
    g = social_mod.SocialStore(str(tmp_path / "social.db"))
    g.set_person("a", "Ana", "ana")
    g.set_person("b", "Ben", "ben")
    return g


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    with TestClient(appmod.app) as c:
        yield c


def signed_in(client, email, name, handle):
    client.post("/api/auth/signup", json={"email": email, "password": "password12"})
    client.post("/api/me", json={"name": name, "handle": handle})
    return client.get("/api/auth/me").json()["user_id"]


def js_function(name: str) -> str:
    start = INDEX.index(f"function {name}(")
    depth, i = 0, INDEX.index("{", start)
    while True:
        if INDEX[i] == "{":
            depth += 1
        elif INDEX[i] == "}":
            depth -= 1
            if depth == 0:
                return INDEX[start:i + 1]
        i += 1


# --- messages: return is a new line --------------------------------------

def test_a_message_keeps_its_line_breaks():
    """The return key now makes a new line, so the server must not flatten
    it back out - that would be the same dead key one step later."""
    assert messages_mod.clean_text("hey\nhow are you") == "hey\nhow are you"
    # Paragraphs survive; a wall of empty lines is cut to two.
    assert messages_mod.clean_text("  a   b  \n\n\n\n\nc  ") == "a b\n\n\nc"
    assert messages_mod.clean_text("\n\nhi\n\n") == "hi"


def test_the_composer_is_a_textarea_and_return_does_not_send():
    box = re.search(r'<(\w+)[^>]*id="threadInput"[^>]*>', INDEX)
    assert box and box.group(1) == "textarea", "the message box is not multi-line"
    assert "sendThreadMessage" not in box.group(0), "return still sends"


# --- messages: delete chat, for one side only ----------------------------

def test_deleting_a_chat_hides_it_for_one_side_only(store):
    store.send("a", "b", text="first")
    store.send("b", "a", text="second")
    store.clear("a", "b")
    assert store.inbox("a") == [], "the chat is still in the deleter's list"
    assert store.thread("a", "b") == []
    assert store.unread_total("a") == 0
    # The other person is unaffected and keeps everything.
    assert [m.text for m in store.thread("b", "a")] == ["first", "second"]
    assert len(store.inbox("b")) == 1


def test_a_message_after_deleting_starts_a_fresh_thread(store):
    store.send("a", "b", text="old")
    store.clear("a", "b")
    store.send("b", "a", text="new")
    assert [m.text for m in store.thread("a", "b")] == ["new"]
    assert store.inbox("a")[0]["last"]["text"] == "new"
    assert store.unread_total("a") == 1
    assert [m.text for m in store.thread("b", "a")] == ["old", "new"]


def test_delete_chat_is_on_the_menu_and_share_is_not():
    menu = js_function("openThreadMenu")
    assert "Delete chat" in menu
    assert "Share an episode" not in menu


def test_delete_chat_through_the_app(client):
    ana = signed_in(client, "ana@b.com", "Ana", "ana")
    client.post("/api/auth/logout")
    signed_in(client, "ben@b.com", "Ben", "ben")
    client.post("/api/messages", json={"to": ana, "text": "hello"})
    assert len(client.get("/api/messages").json()["threads"]) == 1
    assert client.delete(f"/api/messages/thread?with={ana}").status_code == 200
    assert client.get("/api/messages").json()["threads"] == []
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": "ana@b.com", "password": "password12"})
    assert len(client.get("/api/messages").json()["threads"]) == 1


def test_going_back_to_the_list_rereads_it():
    """A chat you had only sent to did not appear until Messages was left
    and reopened, because the list was drawn once on open."""
    assert "renderThreadList()" in js_function("goBack")


# --- the follower popup, once ---------------------------------------------

def test_a_follower_is_announced_once(graph):
    graph.follow("b", "a")
    assert [p["user_id"] for p in graph.new_followers("a", unannounced=True)] == ["b"]
    graph.mark_announced("a", "b")
    assert graph.new_followers("a", unannounced=True) == []
    # The Friends badge still counts them until Friends is opened.
    assert [p["user_id"] for p in graph.new_followers("a")] == ["b"]


def test_the_popup_reads_the_unannounced_list(client):
    ana = signed_in(client, "ana@b.com", "Ana", "ana")
    client.post("/api/auth/logout")
    signed_in(client, "ben@b.com", "Ben", "ben")
    client.post("/api/friends/follow", json={"handle": "ana"})
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": "ana@b.com", "password": "password12"})
    first = client.get("/api/friends").json()
    assert len(first["announce"]) == 1
    ben = first["announce"][0]["user_id"]
    client.post("/api/friends/announced", json={"user_id": ben})
    # The next open of the app: nothing to announce, on the popup or the banner.
    assert client.get("/api/friends").json()["announce"] == []
    assert client.get("/api/notifications?since=0").json()["follows"] == []
    assert ana


# --- listening history ----------------------------------------------------

def test_history_is_newest_first_and_filtered_by_surface(shelf):
    shelf.note_listen("u", "bonds", 2, "myfam", at=100)
    shelf.note_listen("u", "fed", 2, "search", at=200)
    shelf.note_listen("u", "nfl today", 2, "dailyfam", at=300)
    now = 400
    assert [h["query"] for h in shelf.history("u", now=now)] == ["nfl today", "fed", "bonds"]
    assert [h["query"] for h in shelf.history("u", "search", now=now)] == ["fed"]


def test_explore_is_never_history(shelf):
    assert shelf.note_listen("u", "bonds", 2, "explore") is False
    assert shelf.history("u") == []


def test_history_keeps_two_weeks(shelf):
    day = 86400
    shelf.note_listen("u", "old", 2, "search", at=1000)
    shelf.note_listen("u", "new", 2, "search", at=1000 + 15 * day)
    assert [h["query"] for h in shelf.history("u", now=1000 + 15 * day)] == ["new"]


def test_hearing_it_again_moves_it_up_rather_than_listing_it_twice(shelf):
    shelf.note_listen("u", "bonds", 2, "myfam", title="Bonds", at=100)
    shelf.note_listen("u", "fed", 2, "myfam", at=200)
    shelf.note_listen("u", "bonds", 2, "myfam", at=300)
    rows = shelf.history("u", now=400)
    assert [h["query"] for h in rows] == ["bonds", "fed"]
    assert rows[0]["title"] == "Bonds", "a later empty title wiped the real one"


def test_history_needs_an_account_and_a_guest_is_not_recorded(client):
    assert client.get("/api/history").status_code == 401
    ok = client.post("/api/history", json={"query": "bonds", "minutes": 2,
                                           "surface": "search"})
    assert ok.json()["remembered"] is False
    signed_in(client, "ana@b.com", "Ana", "ana")
    client.post("/api/history", json={"query": "bonds", "minutes": 2,
                                      "surface": "myfam", "title": "Bonds"})
    client.post("/api/history", json={"query": "bonds", "minutes": 2,
                                      "surface": "myfam", "title": "Why Bonds Move",
                                      "retitle": True})
    items = client.get("/api/history?surface=myfam").json()["items"]
    assert [(i["query"], i["title"]) for i in items] == [("bonds", "Why Bonds Move")]


def test_settings_offers_listening_history():
    assert 'openHistory()' in js_function("renderSettings")
    for tab in ("All", "myFAM", "dailyFAM", "searchFAM"):
        assert f">{tab}</button>" in INDEX


# --- autocorrect ----------------------------------------------------------

needs_speller = pytest.mark.skipif(not autocorrect.available(),
                                   reason="pyspellchecker not installed")


@needs_speller
@pytest.mark.parametrize("typed,fixed", [
    ("teh", "the"), ("recieve", "receive"), ("definately", "definitely"),
    ("tomorow", "tomorrow"), ("leage", "league"), ("goverment", "government"),
    ("im", "I'm"), ("thats", "that's"),
])
def test_ordinary_typos_are_corrected(typed, fixed):
    assert autocorrect.correct_word(typed) == fixed


@needs_speller
@pytest.mark.parametrize("name", [
    "bitcoin", "nvidia", "mahomes", "messi", "ohtani", "siri", "nba", "lol",
    "Messi", "NFL", "COVID19",
])
def test_names_and_shorthand_are_left_alone(name):
    """A general speller turned bitcoin into "bitching" and nvidia into
    "vida". A correction that rewrites the subject of a search is far worse
    than a missed typo."""
    assert autocorrect.correct_word(name) is None


@needs_speller
def test_a_capital_only_counts_as_the_keyboards_at_a_sentence_start():
    assert autocorrect.correct_word("Teh", first=True) == "The"
    assert autocorrect.correct_word("Teh", first=False) is None


def test_the_spell_endpoint_answers_per_word(client):
    r = client.post("/api/spell", json={"words": ["teh", "bitcoin"],
                                        "first": [False, False]}).json()
    assert len(r["corrections"]) == 2
    assert r["corrections"][1] is None


def test_both_boxes_have_autocorrect_on():
    for box in ("threadInput", "searchInput"):
        tag = re.search(r'<\w+[^>]*id="' + box + r'"[^>]*>', INDEX).group(0)
        assert 'autocorrect="on"' in tag and 'spellcheck="true"' in tag, box
        assert "data-autocorrect" in tag, box


# --- sharing: select, then confirm ----------------------------------------

def test_tapping_a_person_selects_rather_than_sends():
    tap = js_function("sendToContact")
    assert "fetch(" not in tap, "tapping a face sends straight away again"
    assert "Share with " in js_function("drawShareChoice")
    assert 'id="shareConfirm"' in INDEX


# --- interests: the list, no wheel ----------------------------------------

def test_the_interests_wheel_is_gone():
    for gone in ("orbitRing", "renderInterestWheel", "syncWheelPhase",
                 'id="screen-catalog"'):
        assert gone not in INDEX, gone
    intro = INDEX[INDEX.index('id="screen-intro"'):INDEX.index('id="screen-identity"')]
    assert 'id="catalogBody"' in intro, "the list is not the interests page"


# --- the player's X minimises ---------------------------------------------

def test_the_players_x_minimises_rather_than_stopping():
    top = INDEX[INDEX.index('id="screen-player"'):]
    top = top[:top.index("</div>")]
    assert 'onclick="minimizePlayer()"' in top
    body = js_function("minimizePlayer")
    assert "stopSpeech" not in body and "FamAudio.stop" not in body


def test_navigating_keeps_a_playing_episode():
    assert "if(!nowBarState) stopSpeech()" in js_function("goBack")
    assert "if(!nowBarState || leavingExplore) stopSpeech()" in js_function("setTab")
    assert "placeNowBar(next)" in js_function("showScreen")


# --- titles ---------------------------------------------------------------

def test_the_title_rule_asks_for_the_subject_by_name():
    """Titles were too vague to tap (§142): the rule now asks for the actual
    subject by name first and the angle second, in all three places a title
    is written - the writer's system prompt, its user prompt, and the brief."""
    import episode_intelligence
    import script_generator
    # The system prompt has a size budget (test_the_prompt_stays_lean), so
    # it carries the example; the per-episode prompt carries the rule.
    assert "A Costly Pause" in script_generator.system_prompt()
    source = open(script_generator.__file__, encoding="utf-8").read()
    assert "Clear first, curious second" in source
    assert "Clear first" in episode_intelligence.build_ei_prompt("the fed", 2)


# --- found in review ------------------------------------------------------

def test_the_inbox_never_shows_a_message_from_before_a_delete(store):
    """Two rows can share a timestamp; the inbox joined on it and could show
    the pre-delete message as the thread's last line."""
    old = store.send("a", "b", text="old secret", at=100.0)
    store.clear("a", "b")
    store.send("b", "a", text="new", at=100.0)
    assert old.id
    assert store.inbox("a")[0]["last"]["text"] == "new"


def test_a_deleted_chat_raises_no_banner(store):
    before = store.latest_id("a")
    store.send("b", "a", text="hi")
    store.clear("a", "b")
    assert store.arrived_for("a", after_id=before) == []


def test_two_follow_ups_in_the_same_words_stay_two_episodes(shelf):
    """`context` is in the episode's cache key, so it is in history's."""
    shelf.note_listen("u", "and the costs", 2, "other", title="A", context="topic one", at=100)
    shelf.note_listen("u", "and the costs", 2, "other", title="B", context="topic two", at=200)
    rows = shelf.history("u", now=300)
    assert [(r["context"], r["title"]) for r in rows] == [("topic two", "B"), ("topic one", "A")]
    shelf.retitle("u", "and the costs", 2, "Renamed", context="topic one")
    assert [r["title"] for r in shelf.history("u", now=300)] == ["B", "Renamed"]


def test_a_cut_message_does_not_end_on_a_line_break():
    text = "a" * (messages_mod.MAX_TEXT - 1) + "\nmore"
    assert not messages_mod.clean_text(text).endswith("\n")


def test_spell_runs_off_the_event_loop():
    import inspect
    assert "asyncio.to_thread" in inspect.getsource(appmod.spell)
