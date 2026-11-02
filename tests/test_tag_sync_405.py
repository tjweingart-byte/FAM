"""Phase 2, piece 8: the check that an episode's tag agrees everywhere (§405).

Script row, R2 metadata and the archive's record, against a memory bucket
and the SQLite archive (no network): every kind of drift named, nothing
repaired on its own, the owner's repair pushing the row's node out, and the
check as Keith's observe-only task.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audio_store  # noqa: E402
import history_archive as ha  # noqa: E402
import tag_sync  # noqa: E402
from cache import SqliteScriptCache, episode_id  # noqa: E402

WEEK = 7 * 86400
PCM = b"\x00\x01" * 24000
ALIVE = {"sports", "nfl", "eagles"}


@pytest.fixture
def world(monkeypatch, tmp_path):
    import topics

    bucket = audio_store.MemoryAudioStore()
    audio_store.set_store(bucket)
    ha.reset()
    changed = dataclasses.replace(ha.settings, archive=True, archive_backend="sqlite")
    monkeypatch.setattr(ha, "settings", changed)
    archive = ha.SQLiteBackend(str(tmp_path / "archive.db"))
    ha.use(archive)
    monkeypatch.setattr(topics, "category_tree",
                        lambda: SimpleNamespace(get=lambda n: object() if n in ALIVE else None))
    scripts = SqliteScriptCache(str(tmp_path / "scripts.db"))
    tag_sync._CURSOR[0] = ""
    yield SimpleNamespace(bucket=bucket, archive=archive, scripts=scripts)
    audio_store.set_store(None)
    ha.reset()


def _episode(world, key, node="eagles", record_node=None):
    world.scripts.put(key, ["One."], ttl=WEEK, query=key, minutes=2, category_node=node)
    assert world.scripts.put_audio(key, "v", 24000, PCM, ["One."], [0.0])
    episode = episode_id(key, world.scripts.sourced_at(key))
    world.archive.link_episode(episode, key, 1.0, [], 1.0,
                               node=node if record_node is None else record_node)
    return episode


def _kinds(summary):
    return {k: n for k, n in summary["drift"].items() if n}


def test_an_episode_that_agrees_everywhere_is_not_drift(world):
    _episode(world, "k1")
    summary = tag_sync.run(world.scripts, record=False)
    assert summary["checked"] == 1 and _kinds(summary) == {}


def test_every_kind_of_drift_is_named(world):
    _episode(world, "a-r2-node")
    name, = world.scripts.audio_objects("a-r2-node")
    world.bucket.metadata[name]["category-node"] = "giants"
    _episode(world, "b-r2-untagged")
    name, = world.scripts.audio_objects("b-r2-untagged")
    world.bucket.metadata[name] = {}
    _episode(world, "c-missing")
    name, = world.scripts.audio_objects("c-missing")
    world.bucket.objects.pop(name)
    _episode(world, "d-archive-drift", record_node="nfl")
    _episode(world, "e-episode-drift")
    name, = world.scripts.audio_objects("e-episode-drift")
    world.bucket.metadata[name]["episode"] = "someone else.1"
    _episode(world, "f-dead", node="bears")
    summary = tag_sync.run(world.scripts, record=False)
    by_key = {(f["key"], f["kind"]) for f in summary["findings"]}
    assert ("a-r2-node", "r2 node drift") in by_key
    assert ("b-r2-untagged", "r2 untagged") in by_key
    assert ("c-missing", "object missing") in by_key
    assert ("d-archive-drift", "archive node drift") in by_key
    assert ("e-episode-drift", "r2 episode drift") in by_key
    assert ("f-dead", "dead node") in by_key


def test_an_archive_record_written_before_404_is_untagged(world):
    import sqlite3
    from contextlib import closing

    episode = _episode(world, "k1")
    # A record written before §404 has no node (a later write without one
    # never forgets it, so it is made here as the old code left it).
    with closing(sqlite3.connect(world.archive.path)) as db, db:
        db.execute("UPDATE episode_sources SET node = '' WHERE episode = ?", (episode,))
    assert _kinds(tag_sync.run(world.scripts, record=False)) == {"archive untagged": 1}


def test_an_episode_without_an_archive_record_is_not_drift(world):
    world.scripts.put("k1", ["One."], ttl=WEEK, query="k1", minutes=2, category_node="eagles")
    assert _kinds(tag_sync.run(world.scripts, record=False)) == {}


def test_the_check_repairs_nothing_and_reports_on_admin(world, monkeypatch):
    import pipeline_errors

    recorded = []
    monkeypatch.setattr(pipeline_errors, "record", lambda *a, **k: recorded.append((a, k)))
    _episode(world, "k1", record_node="nfl")
    summary = tag_sync.run(world.scripts)
    assert _kinds(summary) == {"archive node drift": 1}
    episode = episode_id("k1", world.scripts.sourced_at("k1"))
    assert world.archive.episode_node(episode) == "nfl"           # nothing moved
    (stage, kind, detail), kw = recorded[0]
    assert (stage, kind) == ("episode", "tag drift") and "--repair" in detail
    assert kw["severity"] == pipeline_errors.NOTICE
    assert tag_sync.report()["drift"]["archive node drift"] == 1


def test_the_owner_s_repair_pushes_the_row_s_node_out(world):
    _episode(world, "k1", record_node="nfl")
    name, = world.scripts.audio_objects("k1")
    world.bucket.metadata[name] = {}
    _episode(world, "dead", node="bears")
    summary = tag_sync.run(world.scripts, record=False)
    dry = tag_sync.repair(world.scripts, summary["findings"], dry_run=True)
    assert [r["key"] for r in dry] == ["k1"] and dry[0]["would"]   # a dead node: never here
    results = tag_sync.repair(world.scripts, summary["findings"])
    assert results[0]["failed"] == [] and results[0]["objects"] == 1
    assert _kinds(tag_sync.run(world.scripts, record=False)) == {"dead node": 1}


def test_pages_follow_a_cursor_and_come_round_again(world):
    for key in ("a", "b", "c"):
        _episode(world, key)
    first = tag_sync.run(world.scripts, page=2, record=False)
    second = tag_sync.run(world.scripts, page=2, record=False)
    assert (first["checked"], first["finished"]) == (2, False)
    assert (second["checked"], second["finished"]) == (1, True)
    assert tag_sync.run(world.scripts, page=2, record=False)["checked"] == 2


def test_without_a_bucket_the_rest_is_still_checked(world):
    _episode(world, "k1", record_node="nfl")
    audio_store.set_store(None)
    assert _kinds(tag_sync.run(world.scripts, record=False)) == {"archive node drift": 1}


def test_it_is_keith_s_observe_only_task(world, monkeypatch):
    from agents import netenyahu, roster

    assert ("it", "tag_sync", 5, "it_agent") in netenyahu.SCHEDULE
    try:
        tag_sync.register(world.scripts)
        assert netenyahu.HANDLERS[("it", "tag_sync")] is tag_sync.handler
    finally:
        netenyahu.HANDLERS.pop(("it", "tag_sync"), None)
        tag_sync.SCRIPT_CACHE[0] = None
    ran = []
    monkeypatch.setattr(tag_sync, "run", lambda *a, **k: ran.append(1) or
                        {"checked": 0, "drift": {}})
    monkeypatch.setattr(roster, "status", lambda role: {"may_run": False, "why_not": "IT_AGENT=0"})
    asyncio.run(tag_sync.handler("it:tag_sync:day"))
    assert ran == []
    monkeypatch.setattr(roster, "status", lambda role: {"may_run": True, "why_not": ""})
    asyncio.run(tag_sync.handler("it:tag_sync:day"))
    assert ran == [1]
