"""Every sent episode is cached, for as long as its message is (§610).

The owner: "I just clicked on an episode sent to me and it generated a full
new one. This is not acceptable." Each way a sent episode used to be
written again is pinned here.
"""
from __future__ import annotations

import os
import pathlib
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import cache as cache_mod  # noqa: E402
from cache import MemoryScriptCache, cache_key, episode_id  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def _signed_in(client, email, name, handle):
    client.post("/api/auth/signup", json={"email": email, "password": "password12"})
    client.post("/api/me", json={"name": name, "handle": handle})
    return client.get("/api/auth/me").json()["user_id"]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    with TestClient(appmod.app) as c:
        yield c


def test_the_share_sheet_sends_which_episode_it_is():
    """The player's Share -> a friend sent only the question."""
    body = INDEX.split("function sendEpisodeToContact(person, done){", 1)[1].split("\n  }", 1)[0]
    assert 'episode: ep.episode || ""' in body


@pytest.mark.usefixtures("everyone_friends")
def test_a_go_deeper_or_a_reading_is_sent_as_itself(client):
    """Its key carries what it follows, so the plain key never matched."""
    query = "what did the fed say about cuts 610"
    key = cache_key(query, 3, None, "the fed meeting", True)
    appmod.SCRIPT_CACHE.put(key, ["The Fed held."], 3600, query, "", 3,
                            bucket="", title="Fed", origin="search")
    heard = episode_id(key, appmod.SCRIPT_CACHE.sourced_at(key))
    reading = "mercury 610 (the planet)"
    rkey = cache_key(reading, 3, None, "", True)
    appmod.SCRIPT_CACHE.put(rkey, ["Mercury is small."], 3600, reading, "", 3,
                            bucket="", title="Mercury", origin="search")
    rheard = episode_id(rkey, appmod.SCRIPT_CACHE.sourced_at(rkey))
    other = "a" * 63 + "6"
    appmod.SCRIPT_CACHE.put(other, ["Other."], 3600, "something else 610", "", 3,
                            bucket="", title="Other", origin="search")

    ana = _signed_in(client, "ana610@b.com", "Ana", "ana610")
    client.post("/api/auth/logout")
    _signed_in(client, "ben610@b.com", "Ben", "ben610")
    client.post("/api/friends/follow", json={"handle": "ana610"})

    sent = client.post("/api/messages", json={"to": ana, "query": query,
                                              "minutes": 3, "episode": heard})
    assert sent.json()["message"]["episode"] == heard
    sent = client.post("/api/messages", json={"to": ana, "query": "mercury 610",
                                              "minutes": 3, "episode": rheard})
    assert sent.json()["message"]["episode"] == rheard
    # Still never another question's episode.
    sent = client.post("/api/messages", json={
        "to": ana, "query": query, "minutes": 3,
        "episode": episode_id(other, appmod.SCRIPT_CACHE.sourced_at(other))})
    assert sent.json()["message"]["episode"] == ""


def test_an_episode_still_being_written_is_sent_by_the_senders_history(monkeypatch):
    """Shared mid-play, before the row exists: the sender's history is proof."""
    ep = "b" * 64 + ".1700000610"
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", MemoryScriptCache())
    monkeypatch.setattr(appmod.SAVED, "history", lambda user, limit=50: (
        [{"episode": ep, "query": "Why is the sky blue 610"}] if user == "ben" else []))
    assert appmod._shared_episode("why is the sky blue 610", 3, ep, "ben") == ep
    assert appmod._shared_episode("why is the sky blue 610", 3, ep, "eve") == ""
    assert appmod._shared_episode("another question", 3, ep, "ben") == ""


def test_a_sent_episode_outlives_two_weeks_and_its_question_being_written_again(monkeypatch):
    cache = MemoryScriptCache()
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", cache)
    query = "upcoming dodgers game 610"
    key = cache_key(query, 3, None, "", True)
    cache.put(key, ["First."], 7200, query, "", 3, bucket="", title="Dodgers",
              origin="search", sourced_at=time.time() - 60)
    sent = episode_id(key, cache.sourced_at(key))
    monkeypatch.setattr(appmod.MESSAGES, "episodes", lambda: [(query, 3, sent)])

    # Written again: the sent one is the archive copy now.
    cache.put(key, ["Second."], 7200, query, "", 3, bucket="", title="Dodgers",
              origin="search", sourced_at=time.time())
    archived = cache.resolve_episode(sent)
    assert archived and archived != key
    assert archived in appmod._held_episode_keys()

    # Its clock runs out (and the two-week pin with it) ...
    entry = cache._data[archived]
    cache._data[archived] = (time.time() - 1,) + tuple(entry[1:])
    assert cache.resolve_episode(sent) == ""
    # ... and the sweep holds it again, a week and a day ahead.
    assert appmod._hold_sent_episodes() == 1
    assert cache.resolve_episode(sent) == archived
    assert cache._data[archived][0] > time.time() + cache_mod.recent_seconds()
    # Held is never current.
    assert cache.get(archived, current=True) is None


def test_an_archived_sent_episode_keeps_its_audio():
    """The sweep deleted an archive copy's audio even when a message named it."""
    cache = MemoryScriptCache()
    key = "c" * 64
    cache._audio[(key, "v")] = {"tier": "recent", "created": 0.0}
    cache._origins[key] = cache_mod.ARCHIVE_ORIGIN
    # Held by its question alone (a save), the old words go ...
    assert cache.sweep_audio({key})["deleted"] == 1
    cache._audio[(key, "v")] = {"tier": "recent", "created": 0.0}
    # ... named by a message's id, they are kept.
    counts = cache.sweep_audio({key}, named={key})
    assert counts["kept"] == 1 and (key, "v") in cache._audio
    other = "d" * 64
    cache._audio[(other, "v")] = {"tier": "recent", "created": 0.0}
    cache._origins[other] = cache_mod.ARCHIVE_ORIGIN
    assert cache.sweep_audio(set())["deleted"] >= 1
    assert (other, "v") not in cache._audio


def test_the_sqlite_cache_holds_a_row_past_its_clock(tmp_path):
    cache = cache_mod.SqliteScriptCache(str(tmp_path / "s.db"))
    key = "e" * 64
    cache.put(key, ["Kept."], 60, "q 610", "", 3, bucket="", title="Q",
              origin="search", sourced_at=time.time() - 30 * 86400)
    sent = episode_id(key, cache.sourced_at(key))
    assert cache.resolve_episode(sent) == ""  # its week ran out
    assert cache.query_of(key) == "q 610"
    assert cache.hold_episode(sent, time.time() + 86400) == key
    assert cache.resolve_episode(sent) == key
    assert cache.get(key, current=True) is None


# --- share links name their episode too ------------------------------------

def test_a_share_link_names_its_episode_and_replays_it(tmp_path):
    import sharing
    store = sharing.ShareStore(str(tmp_path / "shares.db"))
    ep = "f" * 64 + ".1700000610"
    share = store.create("u", "why is the sky blue", 1.5, "Sky", episode=ep)
    assert share["episode"] == ep
    assert sharing.landing_payload(share, url="")["episode"] == ep
    # Shared again: the same link, naming the episode heard this time.
    newer = "f" * 64 + ".1700000999"
    again = store.create("u", "why is the sky blue", 1.5, "Sky", episode=newer)
    assert again["id"] == share["id"] and again["episode"] == newer
    assert store.is_shared_episode("why is the sky blue", 1.5, newer)
    assert not store.is_shared_episode("why is the sky blue", 1.5, "a" * 64 + ".1")
    assert not store.is_shared_episode("something else", 1.5, newer)
    assert store.episode_ids() == [newer]


def test_the_share_clients_send_and_replay_the_episode():
    listen = (ROOT / "static" / "listen.html").read_text(encoding="utf-8")
    assert 'episode: sharedEpisode' in listen and "err.status === 409" in listen
    make = INDEX.split('fetch("/api/share", {', 1)[1][:400]
    assert 'episode: ep.episode || ""' in make


def test_a_shared_link_episode_is_held_like_a_sent_one(monkeypatch):
    cache = MemoryScriptCache()
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", cache)
    key = cache_key("shared 610", 1.5, None, "", True)
    cache.put(key, ["Kept."], 3600, "shared 610", "", 1.5, bucket="", title="S",
              origin="search")
    ep = episode_id(key, cache.sourced_at(key))
    monkeypatch.setattr(appmod.MESSAGES, "episodes", lambda: [])
    monkeypatch.setattr(appmod.SHARES, "episode_ids", lambda: [ep])
    assert appmod._hold_sent_episodes() == 1
    assert key in appmod._sent_episode_keys()


# --- a minute and a half is not one minute -----------------------------------

def test_history_and_resume_keep_a_minute_and_a_half(tmp_path):
    """`int()` read 1.5 back as 1: a different key, so a new episode."""
    import saved
    store = saved.SavedStore(str(tmp_path / "s.db"))
    store.note_listen("u", "why is the sky blue", 1.5, "search", "t")
    assert store.history("u")[0]["minutes"] == 1.5
    src = (ROOT / "saved.py").read_text(encoding="utf-8")
    assert '"minutes": int(r[1])' not in src and "_length(int(" not in src


def test_the_sweep_holds_a_minute_and_a_half_episode():
    """Saved, shared and vibed 1.5-minute episodes lost their audio at a week."""
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    body = src.split("def _held_episode_keys() -> set:", 1)[1].split("\ndef ", 1)[0]
    assert "int(m or 0)" not in body
    msgs = (ROOT / "messages.py").read_text(encoding="utf-8")
    assert "(q, int(m or 0), e" not in msgs


# --- saved and vibed episodes replay by id too ------------------------------

def test_a_saved_row_names_its_episode(tmp_path):
    import saved
    store = saved.SavedStore(str(tmp_path / "s.db"))
    ep = "1" * 64 + ".1700000610"
    item = store.save("u", "why is the sky blue", 1.5, title="Sky", episode=ep)
    assert item.episode == ep and item.as_dict()["episode"] == ep
    newer = "1" * 64 + ".1700000999"
    again = store.save("u", "why is the sky blue", 1.5, episode=newer)
    assert again.id == item.id and again.episode == newer
    assert store.save("u", "why is the sky blue", 1.5).episode == newer, \
        "an older client's save keeps the id"
    assert [i.episode for i in store.items("u")] == [newer]
    assert store.episode_ids() == [newer]


def test_a_vibe_names_its_episode_on_every_surface(tmp_path):
    import social
    store = social.SocialStore(str(tmp_path / "social.db"))
    ep = "2" * 64 + ".1700000610"
    echo = store.echo("u", "why is the sky blue", "Sky", 1.5, episode=ep)
    assert echo.episode == ep and echo.as_dict()["episode"] == ep
    assert store.echoes_by("u")[0].episode == ep
    assert store.echo("u", "why is the sky blue", "Sky", 1.5).episode == ep
    assert store.vibed_episode_ids() == [ep]
    posts = store.vibe_posts("u", viewer="u") if hasattr(store, "vibe_posts") else None
    if posts is not None:
        assert posts[0]["episode"] == ep


def test_saved_and_vibed_ids_are_held(monkeypatch):
    monkeypatch.setattr(appmod.MESSAGES, "episodes", lambda: [])
    monkeypatch.setattr(appmod.SHARES, "episode_ids", lambda: [])
    monkeypatch.setattr(appmod.SAVED, "episode_ids", lambda: ["3" * 64 + ".1"])
    monkeypatch.setattr(appmod.SOCIAL, "vibed_episode_ids", lambda: ["4" * 64 + ".2"])
    assert appmod._sent_episode_ids() == {"3" * 64 + ".1", "4" * 64 + ".2"}


def test_saved_and_vibes_send_and_replay_their_episode():
    for fn in ("function playSaved(id){", "function playVibePost(i, at){",
               "function playStory(){", "function playOwnVibe(v, i){"):
        body = INDEX.split(fn, 1)[1].split("\n  }", 1)[0]
        assert "askIfGone: !!" in body and "episode: " in body, fn
    for fn in ("function saveStoryEpisode(ep){", "function saveVibePost(i){",
               "function postVibeStory(){"):
        body = INDEX.split(fn, 1)[1].split("\n  }", 1)[0]
        assert "episode: " in body, fn
