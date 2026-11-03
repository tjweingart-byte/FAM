"""Phase 2, piece 7: one category tag, everywhere, for an episode's life (§404).

Nando's node on the script row, the audio's R2 metadata and the archive's
records, and `category_tag.retag` moving all of them together - against a
memory bucket, a mock R2 and the SQLite archive (no network).
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
from contextlib import closing
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as store_db  # noqa: E402  (a store's file, or its schema in Postgres, §243)
import audio_store  # noqa: E402
import category_tag  # noqa: E402
import history_archive as ha  # noqa: E402
from cache import SqliteScriptCache, episode_id  # noqa: E402

WEEK = 7 * 86400
PCM = b"\x00\x01" * 24000


@pytest.fixture
def bucket():
    store = audio_store.MemoryAudioStore()
    audio_store.set_store(store)
    yield store
    audio_store.set_store(None)


@pytest.fixture
def scripts(tmp_path):
    return SqliteScriptCache(str(tmp_path / "scripts.db"))


@pytest.fixture
def archive(monkeypatch, tmp_path):
    ha.reset()
    changed = dataclasses.replace(ha.settings, archive=True, archive_backend="sqlite")
    monkeypatch.setattr(ha, "settings", changed)
    store = ha.SQLiteBackend(str(tmp_path / "archive.db"))
    ha.use(store)
    category_tag.reset()
    yield store
    ha.reset()


def _write(scripts, key, node="eagles"):
    scripts.put(key, ["One."], ttl=WEEK, query=key, minutes=2, category="nfl",
                category_node=node)
    assert scripts.put_audio(key, "v", 24000, PCM, ["One."], [0.0])


# --- the bucket ------------------------------------------------------------------

def test_audio_carries_nandos_node_and_its_episode_from_the_upload(bucket, scripts):
    _write(scripts, "k1")
    name, = scripts.audio_objects("k1")
    assert bucket.metadata[name] == {"category-node": "eagles",
                                     "episode": episode_id("k1", scripts.sourced_at("k1"))}


def test_without_a_node_the_upload_is_exactly_as_it_was(bucket, scripts):
    _write(scripts, "k1", node="")
    name, = scripts.audio_objects("k1")
    assert bucket.metadata[name] == {}


def test_r2_retags_by_copying_in_place_and_keeps_the_storage_class():
    import httpx

    seen, metadata = [], {}

    def answer(request):
        seen.append(request)
        name = request.url.path.split("/fam-audio/", 1)[1]
        if request.method == "HEAD":
            return httpx.Response(200, headers=metadata.get(name, {}))
        metadata[name] = {k: v for k, v in request.headers.items()
                          if k.startswith("x-amz-meta-")}
        return httpx.Response(200, content=b"<CopyObjectResult/>")

    store = audio_store.R2AudioStore("acct", "fam-audio", "AK", "SK",
                                     transport=httpx.MockTransport(answer))
    store.set_metadata("kept/a.opus", {"category-node": "new york giants",
                                       "episode": "k.1"})
    headers = seen[-1].headers
    assert headers["x-amz-copy-source"] == "/fam-audio/kept/a.opus"
    assert headers["x-amz-metadata-directive"] == "REPLACE"
    assert headers["x-amz-storage-class"] == "STANDARD_IA"
    assert headers["content-type"] == "audio/ogg"
    assert headers["x-amz-meta-category-node"] == "new york giants"
    assert "x-amz-meta-category-node" in headers["authorization"]   # signed
    assert store.head("kept/a.opus") == {"category-node": "new york giants",
                                         "episode": "k.1"}
    store.set_metadata("recent/b.opus", {"category-node": "café"})
    assert "x-amz-storage-class" not in seen[-1].headers
    assert seen[-1].headers["x-amz-meta-category-node"] == "caf%C3%A9"


# --- the archive -------------------------------------------------------------------

def _episode(archive, key, scripts, node="eagles"):
    episode = episode_id(key, scripts.sourced_at(key))
    finding = ha.Finding(url="https://x.com/a", title="T", passages=("One.",),
                         provider="perplexity")
    archive.intake([finding], "Eagles", "nfl", 1.0, node=node)
    archive.link_episode(episode, key, scripts.sourced_at(key), [finding.as_link()], 1.0,
                         node=node)
    return episode


def test_an_episode_s_record_carries_its_node(archive, scripts):
    _write(scripts, "k1")
    episode = _episode(archive, "k1", scripts)
    assert archive.episode_node(episode) == "eagles"
    archive.link_episode("other.1", "other", 1.0, [], 1.0)
    assert archive.episode_node("other.1") == ""
    assert archive.episode_node("missing.1") is None


# --- retag: everything at once ---------------------------------------------------------

def test_retag_moves_the_script_row_the_bucket_and_the_archive_together(bucket, scripts,
                                                                       archive):
    _write(scripts, "k1")
    episode = _episode(archive, "k1", scripts)
    out = category_tag.retag(scripts, "k1", "nfl", reason="test")
    assert (out["old"], out["new"], out["script"], out["objects"], out["failed"]) == (
        "eagles", "nfl", True, 1, [])
    assert scripts.category_node("k1") == "nfl"
    name, = scripts.audio_objects("k1")
    assert bucket.metadata[name]["category-node"] == "nfl"
    assert archive.episode_node(episode) == "nfl"
    with closing(store_db.connect(archive.path)) as db:
        assert json.loads(db.execute("SELECT nodes FROM intake").fetchone()[0]) == ["nfl"]
    assert category_tag.report()["retagged"] == 1


def test_a_dry_run_retag_changes_nothing(bucket, scripts, archive):
    _write(scripts, "k1")
    out = category_tag.retag(scripts, "k1", "nfl", dry_run=True)
    assert out["would"] and scripts.category_node("k1") == "eagles"
    name, = scripts.audio_objects("k1")
    assert bucket.metadata[name]["category-node"] == "eagles"


def test_an_episode_nando_never_filed_is_left_to_the_writer_s_words(bucket, scripts, archive):
    _write(scripts, "k1", node="")
    out = category_tag.retag(scripts, "k1", "nfl")
    assert "never filed" in out["skipped"] and scripts.category_node("k1") == ""


def test_a_bucket_failure_is_recorded_and_the_rest_still_moves(bucket, scripts, archive,
                                                               monkeypatch):
    import pipeline_errors

    _write(scripts, "k1")
    recorded = []
    monkeypatch.setattr(pipeline_errors, "record", lambda *a, **k: recorded.append(a))

    def refuse(name, metadata):
        raise audio_store.AudioStoreError("HTTP 503")

    monkeypatch.setattr(bucket, "set_metadata", refuse)
    out = category_tag.retag(scripts, "k1", "nfl")
    assert out["failed"] and out["failed"][0].startswith("r2 ")
    assert scripts.category_node("k1") == "nfl"
    assert recorded and "sync check" in recorded[0][2]


def test_retag_never_raises(scripts):
    broken = SimpleNamespace(category_node=lambda k: 1 / 0)
    assert category_tag.retag(broken, "k", "nfl")["failed"]


# --- a tree change moves the episodes too ------------------------------------------------

def test_a_pruned_node_moves_its_episodes_with_its_facts(bucket, scripts, archive,
                                                        monkeypatch):
    import topics
    from agents import housekeeper as hk, housekeeping as hj

    parents = {"sports": "", "nfl": "sports"}   # "eagles" pruned
    tree = SimpleNamespace(get=lambda n: object() if n in parents else None,
                           ancestors=lambda n: ["sports"] if n == "nfl" else [])
    monkeypatch.setattr(topics, "category_tree", lambda: tree)
    monkeypatch.setattr(ha, "consuela_runs", lambda: True)
    monkeypatch.setattr(ha, "_embed", lambda t: ([1.0] + [0.0] * (ha.DIMS - 1), "onnx:384"))
    changed = dataclasses.replace(hj.settings, housekeeper_dry_run=False)
    monkeypatch.setattr(hj, "settings", changed)
    hk.reset(hk.ReportStore(str(archive.path) + ".hk"))
    hj.SCRIPT_CACHE[0] = scripts
    try:
        _write(scripts, "k1")
        ha.add_fact(ha.Fact(url="https://x.com/a", text="The Eagles won.",
                            published="2025-01-01", node_id="eagles",
                            node_path=["sports", "nfl", "eagles"]))
        summary = hj.refile("eagles")
        assert summary["episodes"] == 1
        assert scripts.category_node("k1") == "nfl"
    finally:
        hj.SCRIPT_CACHE[0] = None
        hk.reset()


def test_startup_hands_consuela_the_script_cache(scripts):
    from agents import housekeeper as hk, housekeeping as hj, netenyahu

    try:
        hk.register(scripts)
        assert hj.SCRIPT_CACHE[0] is scripts
    finally:
        hj.SCRIPT_CACHE[0] = None
        for kind in ("consolidate", "dedupe", "refile", "second", "comb"):
            netenyahu.HANDLERS.pop(("housekeeper", kind), None)
