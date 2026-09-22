"""PROBLEMS.md §127: the eleven changes in "9.21.26 II Implementations".

Only the server half is here - the interface half is in the smoke checks
(`tools/smoke_preview.py`), because a fixed header, a cancel button and a
cross-fading caption are things a browser has to be asked about.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod
import cache as cache_mod
import episode_intelligence as ei
import live_captions
import pipeline as pipeline_mod
import saved as saved_mod
import script_generator as sg
import topics
import typing_indicator


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    with TestClient(appmod.app) as c:
        yield c


def signed_in(client, email, name="", handle=""):
    client.post("/api/auth/signup", json={"email": email, "password": "password12"})
    if name:
        client.post("/api/me", json={"name": name, "handle": handle})
    return client.get("/api/auth/me").json()["user_id"]


# --- 1. what the algorithm learns belongs to an account ---------------------

def test_a_guest_event_is_accepted_and_not_remembered(client):
    """A timer firing events must not read a guest as a broken server, and
    nothing a guest does may reach the log."""
    user = client.get("/api/auth/me").json()["user_id"]
    before = len(appmod.EVENTS.for_user(user))
    r = client.post("/api/event", json={"kind": "search", "text": "why bonds move"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "remembered": False}
    assert len(appmod.EVENTS.for_user(user)) == before


def test_an_account_event_is_remembered(client):
    user = signed_in(client, "remember@b.com")
    r = client.post("/api/event", json={"kind": "search", "text": "why bonds move"})
    assert r.json()["remembered"] is True
    assert any(e.text == "why bonds move" for e in appmod.EVENTS.for_user(user))


def test_a_guest_page_view_records_no_impressions(client):
    user = client.get("/api/auth/me").json()["user_id"]
    client.get("/api/myfam")
    assert appmod.EVENTS.impression_occasions(user) == {}


def test_the_account_copy_no_longer_promises_a_guest_history():
    """Signing up used to "keep the listening you have already done". Nothing
    a guest does is kept now, so saying so would be a promise the server
    cannot keep."""
    assert "already done" not in appmod.ACCOUNT_REQUIRED


# --- 2. pick up where you left off -----------------------------------------

def test_progress_keeps_the_middle_and_drops_both_ends(tmp_path):
    store = saved_mod.SavedStore(str(tmp_path / "saved.db"))
    assert store.note_progress("u", "why bonds move", 3, 5) is False
    assert store.progress("u") == []
    assert store.note_progress("u", "why bonds move", 3, 90, title="Bonds") is True
    [row] = store.progress("u")
    assert row["seconds"] == 90 and row["title"] == "Bonds"
    # Finished: the position goes, so a done episode is not offered as undone.
    assert store.note_progress("u", "why bonds move", 3, 175) is False
    assert store.progress("u") == []


def test_progress_is_erased_with_the_account(tmp_path):
    store = saved_mod.SavedStore(str(tmp_path / "saved.db"))
    store.note_progress("u", "why bonds move", 3, 90)
    store.forget("u")
    assert store.progress("u") == []


def test_go_deeper_is_empty_for_a_guest_and_before_any_listening(client):
    assert client.get("/api/godeeper").json() == {
        "threads": [], "resume": [], "similar": []}
    signed_in(client, "fresh@b.com")
    body = client.get("/api/godeeper").json()
    assert body["resume"] == [] and body["threads"] == [] and body["similar"] == []


def test_a_guest_position_is_dropped_and_an_account_one_comes_back(client):
    r = client.post("/api/progress", json={
        "query": "why bonds move", "minutes": 3, "seconds": 90})
    assert r.json()["remembered"] is False
    signed_in(client, "resume@b.com")
    r = client.post("/api/progress", json={
        "query": "why bonds move", "minutes": 3, "seconds": 90, "title": "Bonds"})
    assert r.json() == {"ok": True, "remembered": True, "resumable": True}
    body = client.get("/api/godeeper").json()
    assert [row["query"] for row in body["resume"]] == ["why bonds move"]
    assert "summary" in body["resume"][0]
    # Something was heard, so there is something to be similar to.
    assert len(body["similar"]) <= 4


def test_progress_does_not_follow_a_log_out(client):
    """The reported symptom: part-heard episodes belonged to the phone."""
    signed_in(client, "first@b.com")
    client.post("/api/progress", json={
        "query": "why bonds move", "minutes": 3, "seconds": 90})
    client.post("/api/auth/logout")
    signed_in(client, "second@b.com")
    assert client.get("/api/godeeper").json()["resume"] == []


def test_the_summary_marker_is_parsed_and_never_spoken():
    text = ("The Fed held rates. <<TITLE: The Fed Holds>>\n"
            "<<SUMMARY: Why the Fed held rates and what it is waiting for>>\n"
            "<<NEXT: when the next cut might come>>")
    assert sg.extract_summary(text) == "Why the Fed held rates and what it is waiting for."
    spoken = sg.clean_for_speech(text)
    assert "SUMMARY" not in spoken and "waiting for" not in spoken


def test_both_prompts_ask_for_a_summary():
    assert "<<SUMMARY:" in sg.SYSTEM_PROMPT
    assert "<<SUMMARY:" in sg.build_prompt(sg.plan_episode("why bonds move", 3))


@pytest.mark.parametrize("make", [
    lambda tmp: cache_mod.MemoryScriptCache(),
    lambda tmp: cache_mod.SqliteScriptCache(str(tmp / "scripts.db")),
])
def test_both_caches_keep_a_summary_and_a_rewrite_keeps_it(tmp_path, make):
    store = make(tmp_path)
    store.put("k", ["One."], 60, "q", minutes=3, title="T", summary="What it is.")
    assert store.summary("k") == "What it is."
    store.put("k", ["One."], 60, "q", minutes=3)
    assert store.summary("k") == "What it is."


# --- 3. titles that are not the question -----------------------------------

def test_the_brief_is_asked_for_a_title():
    assert "title" in ei.BRIEF_SCHEMA["required"]
    assert "**title**" in ei.build_ei_prompt("what happened with the fed", 3)


def test_a_title_that_is_the_question_handed_back_is_refused():
    assert ei.clean_title("What Happened With The Fed?", "what happened with the fed") == ""
    assert ei.clean_title("The Fed's Rate Decision", "what happened with the fed") \
        == "The Fed's Rate Decision"


def test_a_provisional_title_never_replaces_the_writers():
    live_captions.reset()
    live_captions.open_track("k")
    live_captions.publish_title("k", "The Brief's Guess")
    assert live_captions.read_title("k") == ("The Brief's Guess", False)
    live_captions.publish_title("k", "The Writer's Own", final=True)
    live_captions.publish_title("k", "A Late Guess")
    assert live_captions.read_title("k") == ("The Writer's Own", True)
    live_captions.reset()


# --- 5. every rail but the friends one has a floor -------------------------

def test_a_blank_slate_still_fills_every_rail_but_friends(tmp_path, monkeypatch):
    store = topics.EventStore(str(tmp_path / "events.db"))
    monkeypatch.setattr(topics, "live_topics", lambda now=None: [])
    for has_account in (False, True):
        feed = topics.build_feed(store, "nobody", has_account=has_account)
        rails = {s["key"]: s["topics"] for s in feed["sections"]}
        for key, floor in topics.RAIL_MINIMUM.items():
            assert len(rails[key]) >= floor, (key, has_account, len(rails[key]))
        assert rails["followers"] == [], "a friends row is never filled with strangers"
        ids = [t["id"] for s in feed["sections"] for t in s["topics"]]
        assert len(ids) == len(set(ids)), "a tile appeared on two rails"


def test_view_more_never_shows_fewer_than_its_rail(tmp_path, monkeypatch):
    store = topics.EventStore(str(tmp_path / "events.db"))
    monkeypatch.setattr(topics, "live_topics", lambda now=None: [])
    for key, floor in topics.RAIL_MINIMUM.items():
        body = topics.build_section(store, "nobody", key)
        assert len(body["topics"]) >= floor, key


# --- 7 and 8. faces, and the three dots ------------------------------------

def test_the_inbox_and_a_thread_carry_the_other_persons_picture(client):
    picture = "data:image/png;base64,iVBORw0KGgo="
    ana = signed_in(client, "ana127@b.com", "Ana", "ana127")
    client.post("/api/me", json={"name": "Ana", "handle": "ana127", "avatar": picture})
    client.post("/api/auth/logout")
    signed_in(client, "ben127@b.com", "Ben", "ben127")
    client.post("/api/messages", json={"to": ana, "text": "hello"})
    [row] = client.get("/api/messages").json()["threads"]
    assert row["avatar"] == picture
    assert client.get(f"/api/messages/thread?with={ana}").json()["with"]["avatar"] == picture


def test_typing_is_directed_and_expires():
    typing_indicator.reset()
    typing_indicator.note("a", "b", now=100.0)
    assert typing_indicator.is_typing("a", "b", now=101.0)
    assert not typing_indicator.is_typing("a", "c", now=101.0), "told a third person"
    assert not typing_indicator.is_typing("b", "a", now=101.0), "reversed the direction"
    assert not typing_indicator.is_typing(
        "a", "b", now=100.0 + typing_indicator.TYPING_SECONDS + 1)
    typing_indicator.clear("a", "b")
    assert not typing_indicator.is_typing("a", "b", now=101.0)


def test_the_thread_poll_says_when_they_are_typing_and_sending_stops_it(client):
    typing_indicator.reset()
    ana = signed_in(client, "ana128@b.com", "Ana", "ana128")
    client.post("/api/auth/logout")
    ben = signed_in(client, "ben128@b.com", "Ben", "ben128")
    assert client.post("/api/messages/typing", json={"to": ana}).json() == {"ok": True}
    assert typing_indicator.is_typing(ben, ana)
    client.post("/api/messages", json={"to": ana, "text": "done"})
    assert not typing_indicator.is_typing(ben, ana)
    typing_indicator.note(ana, ben)
    assert client.get(f"/api/messages/thread?with={ana}").json()["typing"] is True
    typing_indicator.reset()


def test_typing_needs_an_account(client):
    assert client.post("/api/messages/typing", json={"to": "x"}).status_code == 401


# --- 10. captions follow the voice -----------------------------------------

def test_sentence_starts_are_measured_from_the_audio():
    starts = pipeline_mod._sentence_starts(["Short.", "A longer sentence here."],
                                           10.0, 3.0)
    assert starts[0] == 10.0
    assert 10.0 < starts[1] < 13.0


def test_a_live_track_carries_starts_until_one_sentence_has_none():
    live_captions.reset()
    live_captions.open_track("k")
    live_captions.publish("k", ["One.", "Two."], [0.0, 1.5])
    assert live_captions.read_starts("k") == [0.0, 1.5]
    live_captions.publish("k", ["Three."])
    assert live_captions.read_starts("k") == [], \
        "a partial timing table would put every later caption on the wrong line"
    live_captions.reset()
