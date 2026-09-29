"""§173: the same question is the same episode, and history replays what was heard.

The owner searched "upcoming dodgers game" twice, twenty-eight minutes apart,
and paid for two different episodes; then tapped the first row of Recent
listening history and got a third. Two causes, each pinned here:

* an "upcoming game" episode was current for fifteen minutes (thirty for a
  scheduled game), so the second search wrote a new one; and
* a history row was a *question*, replayed by asking it again - which once
  its answer stopped being current wrote a new episode, and a re-write of the
  key overwrote the one that had been heard.
"""
from __future__ import annotations

import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import saved as saved_mod  # noqa: E402
from cache import (ARCHIVE_ORIGIN, MemoryScriptCache, SqliteScriptCache,  # noqa: E402
                   archive_key, episode_id, parse_episode_id, ttl_for)
from config import settings  # noqa: E402
from pipeline import PodcastPipeline  # noqa: E402
from tts import DebugEngine  # noqa: E402

from test_pipeline import FakeGenerator  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
AUDIO_JS = open(os.path.join(ROOT, "static", "fam-audio.js"), encoding="utf-8").read()


# --- the same question, asked again the same afternoon ---------------------

def test_an_upcoming_game_stays_current_for_the_afternoon():
    """Twenty-eight minutes is the gap the owner hit; it must be one episode."""
    assert settings.cache_ttl_volatile >= 2 * 3600
    for ttl in (ttl_for("upcoming dodgers game", live_status="scheduled"),
                ttl_for("upcoming dodgers game", outcome_dependent=True),
                ttl_for("dodgers latest", recency_days=1)):
        assert ttl >= 28 * 60


def test_a_game_in_progress_is_still_never_current():
    assert ttl_for("dodgers game", live_status="in_progress") == 0


# --- the archive: a re-written key keeps the episode it replaces -----------

@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        return MemoryScriptCache()
    return SqliteScriptCache(str(tmp_path / "s.db"))


def _write(store, key, words, sourced):
    store.put(key, words, 60, "upcoming dodgers game", "", 2, bucket="b",
              title=words[0], origin="search", sourced_at=sourced)


def test_a_rewrite_moves_the_heard_episode_aside(store):
    key = "a" * 64
    first = time.time() - 3600
    _write(store, key, ["First episode."], first)
    heard = episode_id(key, store.sourced_at(key))
    store.put_audio(key, "v", 24000, b"\x01\x00" * 50, ["First episode."], [0.0])

    _write(store, key, ["Second episode."], time.time())

    old = store.resolve_episode(heard)
    assert old == archive_key(key, first)
    assert store.get(old, current=False) == ["First episode."]
    assert store.get_audio(old, "v", 24000) is not None, "the heard audio was lost"
    assert store.get(key) == ["Second episode."]
    assert not store.has_any_audio(key)
    # The new one resolves to the key itself.
    assert store.resolve_episode(episode_id(key, store.sourced_at(key))) == key


def test_an_archived_episode_is_never_served_to_a_new_request(store):
    key = "b" * 64
    _write(store, key, ["First."], time.time() - 60)
    heard = episode_id(key, store.sourced_at(key))
    _write(store, key, ["Second."], time.time())
    old = store.resolve_episode(heard)
    assert store.get(old) is None, "an archive copy was current"
    assert all(r["key"] != old for r in store.recent())
    assert all(r["origin"] != ARCHIVE_ORIGIN for r in store.recent())
    near = store.nearest("b", "upcoming dodgers game")
    assert near is None or near[0] != old


def test_a_failed_archive_never_costs_the_new_episode(tmp_path, monkeypatch):
    store = SqliteScriptCache(str(tmp_path / "f.db"))
    key = "9" * 64
    _write(store, key, ["Old."], time.time() - 60)

    def broken(*_a, **_k):
        raise RuntimeError("disk said no")
    monkeypatch.setattr(store, "_archive", broken)
    _write(store, key, ["New."], time.time())
    assert store.get(key) == ["New."]


def test_the_same_words_again_archive_nothing(store):
    key = "c" * 64
    _write(store, key, ["Same."], time.time() - 60)
    _write(store, key, ["Same."], time.time())
    assert store.stats()["entries"] == 1


def test_a_heard_episode_is_kept_as_long_as_the_history_shows_it(store):
    key = "d" * 64
    _write(store, key, ["Heard."], time.time())
    until = time.time() + saved_mod.HISTORY_SECONDS
    assert store.keep_until(key, until)
    # Kept past the week, and no more current than it was.
    assert store.get(key, current=False) == ["Heard."]


def test_episode_ids_parse_and_refuse():
    key = "e" * 64
    assert parse_episode_id(episode_id(key, 1700000000.9)) == (key, 1700000000)
    assert parse_episode_id(key) == (key, 0)
    for bad in ("", "nope", key + ".x", "E" * 64, key + "." + "9" * 13):
        assert parse_episode_id(bad) is None


# --- end to end: search, history, re-write, replay -------------------------

class Counting(FakeGenerator):
    def __init__(self):
        super().__init__(ratio=1.0)
        self.calls = 0

    async def stream_sentences(self, plan, notes=None):
        self.calls += 1
        async for s in super().stream_sentences(plan, notes):
            yield s


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "DEMO_MODE", False)
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", SqliteScriptCache(str(tmp_path / "e.db")))
    gen = Counting()
    monkeypatch.setattr(
        appmod, "_make_pipeline",
        lambda voice=None, author="": PodcastPipeline(
            generator=gen, engine=DebugEngine(), cache=appmod.SCRIPT_CACHE, voice=voice))
    with TestClient(appmod.app) as c:
        c.gen = gen
        yield c


def _sign_in(client):
    client.post("/api/auth/signup", json={"email": "t@b.com", "password": "password12"})


def test_history_replays_the_episode_that_was_heard(client):
    _sign_in(client)
    q = "upcoming dodgers game"
    first = client.get(f"/api/audio?q={q}&minutes=1&fmt=pcm&surface=search")
    assert first.status_code == 200
    heard = first.headers["X-FAM-Episode"]
    assert parse_episode_id(heard), "the audio did not say which episode it was"
    assert client.gen.calls == 1

    # Asked again at once: the same episode, no new writing.
    again = client.get(f"/api/audio?q={q}&minutes=1&fmt=pcm&surface=search")
    assert again.headers["X-FAM-Cache"] == "hit"
    assert again.headers["X-FAM-Episode"] == heard
    assert client.gen.calls == 1

    client.post("/api/history", json={"query": q, "minutes": 1, "surface": "search",
                                      "title": "Dodgers", "episode": heard})
    item = client.get("/api/history").json()["items"][0]
    assert item["episode"] == heard

    # Later, the question is written again with different words.
    key, _ = parse_episode_id(heard)
    appmod.SCRIPT_CACHE.put(key, ["A different, later episode."], 60, q, "", 1,
                            origin="search", sourced_at=time.time() + 5)

    replay = client.get(f"/api/audio?q={q}&minutes=1&fmt=pcm&surface=search"
                        f"&episode={item['episode']}")
    assert replay.status_code == 200
    assert replay.headers["X-FAM-Cache"] == "hit"
    assert replay.headers["X-FAM-Episode"] == heard
    assert client.gen.calls == 1, "a history replay wrote a new episode"


def test_a_history_replay_never_writes(client):
    gone = episode_id("f" * 64, 1700000000)
    res = client.get(f"/api/audio?q=anything&minutes=1&fmt=pcm&episode={gone}")
    assert res.status_code == 409
    assert client.gen.calls == 0
    assert client.get("/api/audio?q=a&minutes=1&fmt=pcm&episode=junk").status_code == 400


def test_history_pins_what_it_names(client):
    _sign_in(client)
    res = client.get("/api/audio?q=how+tides+work&minutes=1&fmt=pcm&surface=search")
    heard = res.headers["X-FAM-Episode"]
    client.post("/api/history", json={"query": "how tides work", "minutes": 1,
                                      "surface": "search", "episode": heard})
    key, _ = parse_episode_id(heard)
    import sqlite3
    expires = sqlite3.connect(appmod.SCRIPT_CACHE.path).execute(
        "SELECT expires FROM scripts WHERE key = ?", (key,)).fetchone()[0]
    assert expires >= time.time() + saved_mod.HISTORY_SECONDS - 60


def test_an_old_history_row_replays_what_is_kept(client):
    """A row written before §173 carries no id; while its question's key
    still holds an episode it replays that instead of writing."""
    _sign_in(client)
    client.get("/api/audio?q=how+tides+work&minutes=1&fmt=pcm&surface=search")
    client.post("/api/history", json={"query": "how tides work", "minutes": 1,
                                      "surface": "search"})
    item = client.get("/api/history").json()["items"][0]
    assert parse_episode_id(item["episode"])


# --- the interface carries the id both ways --------------------------------

def test_the_interface_keeps_and_replays_the_episode_id():
    assert 'res.headers.get("X-FAM-Episode")' in AUDIO_JS
    assert '"&episode="' in AUDIO_JS
    play = INDEX[INDEX.index("function playHistory("):]
    assert "episode: item.episode" in play[:1200]
    note = INDEX[INDEX.index("function noteListen("):]
    assert "episode:" in note[:1400]
    assert "FamAudio.episode()" in INDEX
