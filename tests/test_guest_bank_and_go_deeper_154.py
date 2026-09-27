"""PROBLEMS.md §154: the evergreen bank is a guest's whole sample of FAM, and
costs nothing; Go Deeper is this week's unfinished episodes and this week's
Go Deeper prompts, and nothing adjacent."""
from __future__ import annotations

import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod
import prefetch
import saved as saved_mod
import topics as T

WEEK = 7 * 24 * 3600


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    appmod._read_hits.clear()
    with TestClient(appmod.app) as c:
        c.get("/api/auth/me")
        yield c


def signed_in(client, email):
    r = client.post("/api/auth/signup", json={"email": email, "password": "password12"})
    assert r.status_code == 200, r.text
    return client.get("/api/auth/me").json()["user_id"]


# --- the guest's myFAM is the bank, on every rail -------------------------

def test_a_guest_sees_the_bank_on_every_rail_and_nothing_twice(client):
    body = client.get("/api/myfam").json()
    assert body["sample"] is True and body["taste_source"] == "sample"
    shown = [t["id"] for s in body["sections"] for t in s["topics"]]
    assert [s["key"] for s in body["sections"]] == [k for k, _ in T.SECTIONS]
    for section in body["sections"]:
        assert len(section["topics"]) == T.SECTION_SIZE, section["key"]
    assert set(shown) <= set(T.BANK_BY_ID), "a guest was shown something off the bank"
    assert len(shown) == len(set(shown))
    assert all("playable" in t for s in body["sections"] for t in s["topics"])


def test_an_account_is_never_shown_the_sample(client):
    signed_in(client, "ranked@fam.test")
    body = client.get("/api/myfam").json()
    assert not body.get("sample")
    assert body["taste_source"] in ("startup", "taste")


def test_a_guest_page_schedules_nothing_that_costs(client, monkeypatch):
    calls: list = []
    monkeypatch.setattr(prefetch, "schedule_cycle",
                        lambda *a, **k: calls.append("prefetch") or True)
    monkeypatch.setattr(appmod.stories_mod, "is_stale", lambda *a, **k: True)
    monkeypatch.setattr(appmod, "_warm_stories",
                        lambda *a, **k: calls.append("stories"))
    monkeypatch.setattr(appmod.categories_mod, "is_stale", lambda *a, **k: True)
    monkeypatch.setattr(appmod, "_grow_categories",
                        lambda *a, **k: calls.append("categories"))
    assert client.get("/api/myfam").status_code == 200
    assert calls == []


def test_a_guest_view_more_is_the_bank(client):
    body = client.get("/api/myfam/section?key=followers").json()
    assert body["sample"] is True
    ids = [t["id"] for t in body["topics"]]
    assert set(ids) == set(T.BANK_BY_ID) and len(ids) == len(set(ids))
    rail = next(s for s in T.guest_feed()["sections"] if s["key"] == "followers")
    assert ids[:T.SECTION_SIZE] == [t["id"] for t in rail["topics"]]


# --- a guest's tap never writes an episode or wakes the voice -------------

def test_a_guest_bank_tap_with_nothing_kept_is_refused_before_any_cost(client, monkeypatch):
    woke: list = []
    monkeypatch.setattr(appmod, "_wake_remote_voice", lambda: woke.append(1))
    topic = T.BANK_BY_ID["fed-next-move"]
    r = client.get("/api/audio", params={"q": topic.query, "minutes": 2, "fmt": "pcm",
                                         "surface": "myfam", "topic_id": topic.id})
    assert r.status_code == 403
    assert r.json()["refused_by"] == "account"
    assert woke == [], "a guest's tap woke the GPU"
    # And nothing was written for it.
    if appmod.SCRIPT_CACHE is not None:
        key = appmod._episode_key(appmod._validated_plan(topic.query, 2))
        assert appmod.SCRIPT_CACHE.get(key) is None


def test_the_dailyfam_example_is_gated_the_same_way(client):
    r = client.get("/api/audio", params={"q": "anything at all", "minutes": 2,
                                         "fmt": "pcm", "surface": "dailyfam"})
    assert r.status_code == 403 and r.json()["refused_by"] == "account"


def test_the_gate_is_for_guests_on_the_sample_pages_only():
    class Req:
        def __init__(self, authed):
            class L:
                is_authenticated = authed
            self.state = type("S", (), {"listener": L()})()

    assert appmod._guest_play_gated(Req(False), "myfam", "")
    assert appmod._guest_play_gated(Req(False), "dailyfam", "")
    assert appmod._guest_play_gated(Req(False), "other", "fed-next-move")
    assert not appmod._guest_play_gated(Req(False), "search", "")
    assert not appmod._guest_play_gated(Req(True), "myfam", "fed-next-move")


def test_a_kept_episode_plays_for_a_guest(client, monkeypatch):
    """The one thing a guest may play from the sample pages: audio already
    kept. Stood in for here by the pipeline's own answer."""
    from pipeline import PodcastPipeline

    async def kept(self, plan):
        return True

    monkeypatch.setattr(PodcastPipeline, "has_stored_audio", kept)
    monkeypatch.setattr(appmod, "_wake_remote_voice", lambda: None)
    topic = T.BANK_BY_ID["sleep-science"]
    r = client.get("/api/audio", params={"q": topic.query, "minutes": 2, "fmt": "pcm",
                                         "surface": "myfam", "topic_id": topic.id})
    assert r.status_code != 403


def test_a_guest_sample_episode_does_not_count_down_into_the_gate():
    """The What's next popup starts its first tile by itself. For a guest on
    the sample pages every tile would be refused into the sign-up sheet, so
    the popup is not offered there at all."""
    with open(os.path.join(os.path.dirname(__file__), "..", "static", "index.html"),
              encoding="utf-8") as f:
        html = f.read()
    offer = html.split("function maybeOfferNextUp(", 1)[1].split("\n  }\n", 1)[0]
    assert "AUTH.authenticated" in offer and '"myfam"' in offer and '"dailyfam"' in offer


# --- DailyFAM's example playlist ------------------------------------------

def test_the_sample_mix_is_bank_topics_at_the_browse_length(client):
    body = client.get("/api/mixes/sample").json()
    mix = body["mix"]
    assert mix["sample"] is True and mix["items"]
    for item in mix["items"]:
        assert item["id"] in T.BANK_BY_ID
        assert item["query"] == T.BANK_BY_ID[item["id"]].query
        assert item["prompt"] == "" and item["daily_prompt"] == ""
        assert item["minutes"] == appmod.BROWSE_MINUTES
        assert "playable" in item
    # And a guest's own mix list is still the wall it was.
    assert client.get("/api/mixes").status_code == 401


# --- Go Deeper: this week, and only two kinds of thing --------------------

def test_progress_can_be_limited_to_a_window(tmp_path):
    store = saved_mod.SavedStore(str(tmp_path / "saved.db"))
    now = time.time()
    store.note_progress("u", "old one", 3, 90, at=now - WEEK - 60)
    store.note_progress("u", "new one", 3, 90, at=now - 60)
    assert [r["query"] for r in store.progress("u", since=now - WEEK)] == ["new one"]
    assert len(store.progress("u")) == 2


def test_threads_can_be_limited_to_a_window(tmp_path):
    store = T.EventStore(str(tmp_path / "e.db"))
    now = time.time()
    store.record(T.Event("u", "complete", "ai-agents", "", ("tech",),
                         at=now - WEEK - 60, thread="an old thread"))
    store.record(T.Event("u", "complete", "sleep-science", "", ("health",),
                         thread="a new thread"))
    assert [t["thread"] for t in store.open_threads("u", since=now - WEEK)] == [
        "a new thread"]


def test_a_thread_they_have_since_listened_to_is_closed(tmp_path):
    store = T.EventStore(str(tmp_path / "e.db"))
    store.record(T.Event("u", "complete", "ai-agents", "", ("tech",),
                         thread="why chip supply is so concentrated"))
    store.record(T.Event("u", "play", "", "why chip supply is so concentrated", ()))
    assert store.open_threads("u") == []


def test_go_deeper_offers_nothing_similar(client):
    signed_in(client, "deeper@fam.test")
    client.post("/api/progress", json={"query": "why bonds move", "minutes": 3,
                                       "seconds": 90})
    client.post("/api/event", json={"kind": "complete", "topic_id": "sleep-science",
                                    "thread": "why sleep debt cannot be repaid"})
    body = client.get("/api/godeeper").json()
    assert body["similar"] == []
    assert [r["query"] for r in body["resume"]] == ["why bonds move"]
    assert [t["thread"] for t in body["threads"]] == ["why sleep debt cannot be repaid"]


def test_go_deeper_ignores_what_is_older_than_a_week(client):
    user = signed_in(client, "lastweek@fam.test")
    old = time.time() - WEEK - 3600
    appmod.SAVED.note_progress(user, "a month-old half listen", 3, 90, at=old)
    appmod.EVENTS.record(T.Event(user, "complete", "sleep-science", "", ("health",),
                                 at=old, thread="an old follow-up"))
    body = client.get("/api/godeeper").json()
    assert body["resume"] == [] and body["threads"] == []


def test_a_dismissed_tile_is_replaced_only_by_one_that_qualifies(client):
    user = signed_in(client, "replace@fam.test")
    for q in ("first half listen", "second half listen"):
        client.post("/api/progress", json={"query": q, "minutes": 3, "seconds": 90})
    client.post("/api/godeeper/dismiss", json={"query": "second half listen"})
    body = client.get("/api/godeeper").json()
    assert [r["query"] for r in body["resume"]] == ["first half listen"]
    assert body["threads"] == [] and body["similar"] == []
