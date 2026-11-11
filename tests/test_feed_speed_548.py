"""§548: myFAM drawn in a handful of statements, off the event loop.

What a draw used to cost, and what each fix must not change:

* a cache probe per candidate tile (~1,000 a draw) is now a few batched
  reads (`ScriptCache.rows_many`), and each answer is the one the
  single-key read gives (`holds`, `written_at`, `title`, `sources`);
* the crowd's listens were read four times a draw for every listener and
  are now one shared snapshot - which must answer exactly what a fresh read
  answers, including a play recorded a moment ago;
* prefetch's feed source drew the page a second time, and now reads the
  page that was drawn;
* the draw and /api/health ran on the event loop and now run in a thread;
* /api/health keeps its counting reports for a few seconds, never its real
  checks.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import cache as cache_mod  # noqa: E402
import topics as T  # noqa: E402
from config import BROWSE_MINUTES  # noqa: E402

MINUTES = BROWSE_MINUTES
DAY = 86400.0


# --- the batch probe is the single-key probe -----------------------------

def _put(cache, query, ttl, *, sourced=None, title="", sources=""):
    key = appmod._episode_key(appmod._validated_plan(query, MINUTES))
    cache.put(key, ["One sentence."], ttl, query, "", MINUTES, title=title,
              sources=sources, sourced_at=sourced)
    return key


def test_rows_many_answers_what_each_single_key_read_answers(tmp_path):
    cache = cache_mod.SqliteScriptCache(str(tmp_path / "scripts.db"))
    now = time.time()
    keys = [
        _put(cache, "Who won the league?", 3600, title="A Title"),          # current
        _put(cache, "Why is the sky blue?", 0, sourced=now - 60),             # kept, not current
        _put(cache, "What is a black hole?", 86400, sourced=now - 10 * DAY),  # gone
        _put(cache, "How do tides work?", 600, title="Tides",
             sources='{"items": []}'),
    ]
    # One written before `fresh_until` existed: current as long as it is kept.
    conn = cache._conn()
    conn.execute("UPDATE scripts SET fresh_until = 0 WHERE key = ?", (keys[3],))
    missing = "0" * 64
    rows = cache.rows_many(keys + [missing])
    assert missing not in rows
    stamp = time.time()
    for key in keys:
        row = rows.get(key)
        for current in (True, False):
            assert (bool(row) and row.holds(stamp, current)) == cache.holds(key, current)
        assert (row.written_at(stamp) if row else None) == cache.written_at(key)
        assert (row.title_text(stamp) if row else "") == cache.title(key)
        assert (row.sources_text(stamp) if row else "") == cache.sources(key)
    assert cache.holds_many(keys + [missing]) == {k for k in keys if cache.holds(k)}
    assert cache.holds_many(keys, current=False) == {
        k for k in keys if cache.holds(k, current=False)}


def test_rows_many_reads_in_chunks(tmp_path, monkeypatch):
    cache = cache_mod.SqliteScriptCache(str(tmp_path / "scripts.db"))
    keys = [_put(cache, f"Question number {i}?", 3600) for i in range(7)]
    monkeypatch.setattr(cache_mod, "ROWS_MANY_CHUNK", 3)
    seen: list = []
    cache._conn().set_trace_callback(seen.append)
    assert set(cache.rows_many(keys)) == set(keys)
    assert sum("WHERE key IN" in s for s in seen) == 3


def test_the_memory_cache_batch_probe_matches_holds():
    cache = cache_mod.MemoryScriptCache()
    keys = [_put(cache, "Who won the league?", 3600),
            _put(cache, "Why is the sky blue?", 0, sourced=time.time() - 60)]
    assert cache.holds_many(keys + ["nope"]) == {k for k in keys if cache.holds(k)}


# --- a draw, on a fixture ------------------------------------------------

LISTENER = "me"
FRIENDS = [f"friend-{i}" for i in range(6)]
CROWD = [f"crowd-{i}" for i in range(30)]


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A cache with a few hundred episodes, a crowd that heard them, and a
    listener with friends - enough candidates that a probe per tile shows."""
    cache = cache_mod.SqliteScriptCache(str(tmp_path / "scripts.db"))
    store = T.EventStore(str(tmp_path / "events.db"))
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", cache)
    now = time.time()
    questions = [f"What happened with subject {i} this week?" for i in range(240)]
    for i, q in enumerate(questions):
        if i % 3 == 0:  # most of what people heard is not cached at this length
            _put(cache, q, 6 * 3600, title=f"Subject {i}")
    for n, user in enumerate(CROWD + FRIENDS):
        for j in range(25):
            q = questions[(n * 7 + j * 11) % len(questions)]
            at = now - ((n + j) % 13) * DAY / 2 - 120
            store.record(T.Event(user, "play", "", q, (), at))
            store.mark_heard(user, q, at + 40)
    for q in questions[:5]:
        store.record(T.Event(LISTENER, "play", "", q, (), now - DAY))
    return cache, store, now


def _draw(store, now):
    shared = appmod._FeedProbe(MINUTES)
    return T.build_feed(
        store, LISTENER, now=now, circle=FRIENDS,
        written=appmod._written_probe(MINUTES, shared),
        written_at=appmod._written_at_probe(MINUTES, shared),
        episode_info=appmod._episode_info_probe(MINUTES, shared),
        has_account=True)


class _OneKeyAtATime:
    """The same cache without the batch read: the probes fall back to one
    statement per tile, which is what every draw did before §548."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        if name in ("rows_many", "holds_many"):
            raise AttributeError(name)
        return getattr(self._inner, name)


def test_a_draw_reads_the_cache_a_handful_of_times(world):
    cache, store, now = world
    seen: list = []
    cache._conn().set_trace_callback(seen.append)
    feed = _draw(store, now)
    cache._conn().set_trace_callback(None)
    probes = [s for s in seen if "FROM scripts" in s]
    assert any(t for sec in feed["sections"] for t in sec["topics"])
    # A probe per candidate was hundreds of statements here.
    assert len(probes) <= 12, probes
    assert not [s for s in probes if "WHERE key = " in s]


def test_the_batched_draw_is_the_one_key_at_a_time_draw(world, monkeypatch):
    cache, store, now = world
    batched = _draw(store, now)
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", _OneKeyAtATime(cache))
    assert _draw(store, now) == batched


def test_the_shared_crowd_snapshot_answers_what_a_fresh_read_answers(world, monkeypatch):
    _cache, store, now = world
    T.reset_crowd()
    shared = _draw(store, now)
    monkeypatch.setattr(T.EventStore, "_crowd", lambda self, since: None)
    assert _draw(store, now) == shared


def test_a_play_recorded_here_is_on_the_next_draw(tmp_path):
    """Immediacy in this process: a write throws the snapshot away."""
    store = T.EventStore(str(tmp_path / "events.db"))
    now = time.time()
    store.record(T.Event("a", "play", "", "Who won the league?", (), now - 60))
    assert len(store.listens_between(now - DAY, now + 1)) == 1
    store.record(T.Event("b", "play", "", "Who won the league?", (), now - 30))
    store.mark_heard("b", "Who won the league?", now - 5)
    assert len(store.listens_between(now - DAY, now + 1)) == 2
    assert ("b", "who won the league") in store.heard_since(now - DAY)
    store.forget("b")
    assert len(store.listens_between(now - DAY, now + 1)) == 1


def test_another_workers_play_is_seen(tmp_path, monkeypatch):
    """Another process writes straight to the database: the newest play's
    time is what tells this one its snapshot is behind."""
    path = str(tmp_path / "events.db")
    store = T.EventStore(path)
    now = time.time()
    store.record(T.Event("a", "play", "", "Who won the league?", (), now - 60))
    assert len(store.listens_between(now - DAY, now + 1)) == 1
    monkeypatch.setattr(T, "CROWD_CHECK_SECONDS", 0.0)
    other = sqlite3.connect(path)
    other.execute("INSERT INTO events (user_id, kind, topic_id, text, tags, at)"
                  " VALUES ('b', 'play', '', 'Who won the league?', '', ?)", (now - 10,))
    other.commit()
    other.close()
    assert len(store.listens_between(now - DAY, now + 1)) == 2


def test_a_read_older_than_the_snapshot_goes_to_the_database(tmp_path):
    store = T.EventStore(str(tmp_path / "events.db"))
    old = time.time() - T.CROWD_SPAN - 30 * DAY
    store.record(T.Event("a", "play", "", "Who won the league?", (), old))
    assert len(store.listens_between(old - 1, old + 1)) == 1


# --- prefetch reads the page it was given ---------------------------------

def test_the_feed_source_reads_the_drawn_page(tmp_path, monkeypatch):
    import prefetch_sources

    store = T.EventStore(str(tmp_path / "events.db"))
    monkeypatch.setattr(T, "build_feed", lambda *a, **k: pytest.fail(
        "the page was drawn a second time"))
    drawn = {"sections": [
        {"key": "from_history", "title": "Made for you",
         "topics": [{"id": "t1", "query": "Who won the league?"}]},
        {"key": "followers", "title": "Friends",
         "topics": [{"id": "t2", "query": "Why is the sky blue?"}]}]}
    found = prefetch_sources.FeedSource(store).candidates("me", feed=drawn)
    assert [c.query for c in found] == ["Who won the league?", "Why is the sky blue?"]


def test_a_cycle_scheduled_by_a_draw_is_handed_the_page(monkeypatch):
    import prefetch

    got: list = []

    class Recording(prefetch.Prefetcher):
        async def run_once(self, listener="", minutes=0, feed=None):
            got.append(feed)
            return {"ran": True, "reason": "", "outcomes": {}}

    import dataclasses

    import config

    prefetch.reset()
    monkeypatch.setattr(prefetch, "settings",
                        dataclasses.replace(config.settings, prefetch=True))

    async def scenario():
        monkeypatch.setattr(prefetch, "_PREFETCHER", Recording(generator=None))
        feed = {"sections": [{"key": "from_history", "title": "Made for you",
                              "topics": [{"id": "t1", "query": "q", "title": "x"}]}]}
        assert prefetch.schedule_cycle("listener-1", 2, feed) is True
        for _ in range(3):
            await asyncio.sleep(0)

    asyncio.run(scenario())
    assert got == [{"sections": [{"key": "from_history", "title": "Made for you",
                                  "topics": [{"id": "t1", "query": "q"}]}]}]


# --- off the event loop ---------------------------------------------------

def test_the_page_is_drawn_off_the_event_loop(monkeypatch):
    """No event loop is running where the page is ranked: it is a worker
    thread, so one listener's draw never holds up another's request."""
    from fastapi.testclient import TestClient

    where: list = []
    real = T.build_feed

    def spy(*a, **k):
        try:
            asyncio.get_running_loop()
            where.append("loop")
        except RuntimeError:
            where.append(threading.current_thread().name)
        return real(*a, **k)

    monkeypatch.setattr(appmod.topics_mod, "build_feed", spy)
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_has_account", lambda request: True)
    monkeypatch.setattr(appmod.prefetch, "schedule_cycle", lambda *a, **k: False)
    assert TestClient(appmod.app).get("/api/myfam").status_code == 200
    assert where and "loop" not in where


# --- /api/health ----------------------------------------------------------

def test_health_keeps_the_cache_totals_but_never_the_real_checks(monkeypatch):
    calls = {"stats": 0, "databases": 0}
    real_stats = appmod._cache_report_now
    real_dbs = appmod._database_report

    def stats():
        calls["stats"] += 1
        return real_stats()

    def dbs():
        calls["databases"] += 1
        return real_dbs()

    monkeypatch.setattr(appmod, "_cache_report_now", stats)
    monkeypatch.setattr(appmod, "_database_report", dbs)
    monkeypatch.setattr(appmod, "_HEALTH_MEMO", {})
    from fastapi.testclient import TestClient

    client = TestClient(appmod.app)
    first = client.get("/api/health").json()
    second = client.get("/api/health").json()
    assert calls == {"stats": 1, "databases": 2}
    assert first["cache"] == second["cache"]
    assert first["build"] == second["build"]
    # Past the window it is counted again.
    monkeypatch.setattr(appmod, "HEALTH_REPORT_SECONDS", 0.0)
    client.get("/api/health")
    assert calls["stats"] == 2


def test_health_builds_its_http_client_once(monkeypatch):
    import anthropic_client

    built: list = []
    real = anthropic_client.build_http_client
    monkeypatch.setattr(anthropic_client, "_PROBE_CLIENTS", {})
    monkeypatch.setattr(anthropic_client, "build_http_client",
                        lambda: built.append(1) or real())
    for _ in range(3):
        anthropic_client.http2_enabled()
    assert len(built) == 1
