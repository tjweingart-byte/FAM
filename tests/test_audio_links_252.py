"""Replays straight from the bucket, the database's size, and the hot copy's
ceiling on Postgres (PROBLEMS.md §252).

What each must keep true, tested by what breaking it would cost:

* **A link is signed as S3 signs one**, or R2 refuses every replay and every
  one costs a fallback. Pinned against botocore's `generate_presigned_url`.
* **A link is spent like a stream**: the gate, the allowance, the play and
  the ledger happen before it is made, and nothing is voiced.
* **Only a replay gets one**: a first play, PCM, a cache with no bucket, and
  `AUDIO_LINKS=0` stream exactly as before.
* **The file decodes to what the stream would have sent**: the player turns
  the Ogg file into the same packets, so seek, speed and captions cannot
  drift between the two.
* **A failed link is counted**, never invisible.
* **The database says how big it is**, and the hot copy is small in Postgres
  unless somebody chose otherwise.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import random
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audio_codec  # noqa: E402
import audio_store  # noqa: E402
import cache as cache_mod  # noqa: E402
import db as db_mod  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

needs_opus = pytest.mark.skipif(not audio_codec.available()[0],
                                reason="PyAV is not installed")


# --- the signature -----------------------------------------------------------


def test_a_link_is_signed_exactly_as_botocore_signs_it():
    """botocore 1.x, `generate_presigned_url("get_object", ...)`, path-style,
    region "auto", ExpiresIn=300, at 2026-10-11T18:16:16Z - with a key that
    needs escaping, so the path's encoding is pinned too."""
    url = audio_store.presign(
        "GET", "acct123.r2.cloudflarestorage.com", "/fam-audio/kept/ab+c d.opus",
        "AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        dt.datetime(2026, 10, 11, 18, 16, 16), 300)
    assert url == (
        "https://acct123.r2.cloudflarestorage.com/fam-audio/kept/ab%2Bc%20d.opus"
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
        "&X-Amz-Credential=AKIDEXAMPLE%2F20261011%2Fauto%2Fs3%2Faws4_request"
        "&X-Amz-Date=20261011T181616Z&X-Amz-Expires=300&X-Amz-SignedHeaders=host"
        "&X-Amz-Signature=a219ab9b5a3b947d9340b26d525bf707ee84a0f2a7a590e678965451c7c8be17")


def test_the_r2_store_signs_without_asking_the_bucket():
    import httpx

    calls = []
    store = audio_store.R2AudioStore(
        "acct123", "fam-audio", "AK", "SK",
        transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(500)))
    url = store.link("recent/abc.opus", 300)
    assert url.startswith("https://acct123.r2.cloudflarestorage.com/fam-audio/recent/abc.opus?")
    assert "X-Amz-Expires=300" in url and "X-Amz-Signature=" in url
    assert calls == [], "a link is arithmetic; it must not cost a request"


# --- the endpoint -------------------------------------------------------------


def _set(monkeypatch, module, **values):
    monkeypatch.setattr(module, "settings", dataclasses.replace(module.settings, **values))


@pytest.fixture
def bucket():
    store = audio_store.MemoryAudioStore()
    audio_store.set_store(store)
    yield store
    audio_store.set_store(None)


def _client(tmp_path, monkeypatch, cache=None):
    import app as appmod
    import tts
    from cache import SqliteScriptCache
    from fastapi.testclient import TestClient
    from pipeline import PodcastPipeline
    from test_audio_cache import CountingGenerator, CountingVoice

    _set(monkeypatch, tts, sample_rate=24000)
    monkeypatch.setattr(appmod, "SCRIPT_CACHE",
                        cache or SqliteScriptCache(str(tmp_path / "s.db")))
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "DEMO_MODE", False)
    monkeypatch.setattr(appmod, "_AUDIO_LINKS", {"issued": 0, "fallbacks": 0})
    engine, gen = CountingVoice(), CountingGenerator()
    monkeypatch.setattr(
        appmod, "_make_pipeline",
        lambda voice=None, author="": PodcastPipeline(
            generator=gen, engine=engine, cache=appmod.SCRIPT_CACHE, voice=voice))
    return TestClient(appmod.app), engine, appmod


def _get(client, **extra):
    return client.get("/api/audio", params={
        "q": "why is the sky blue", "minutes": 1, "fmt": "opus", **extra})


@needs_opus
def test_a_replay_is_answered_with_a_link_and_nothing_is_voiced(tmp_path, monkeypatch, bucket):
    client, engine, appmod = _client(tmp_path, monkeypatch)
    first = _get(client, link=1)
    # A first play has no file yet: it streams as it is voiced.
    assert first.status_code == 200
    assert first.headers["x-fam-audio-format"] == "opus"
    assert "x-fam-audio-link" not in first.headers
    voiced = engine.calls
    assert bucket.objects, "the finished episode was not put in the bucket"

    reserved = []
    real = appmod._reserve
    monkeypatch.setattr(appmod, "_reserve",
                        lambda *a, **k: reserved.append(k) or real(*a, **k))
    again = _get(client, link=1)
    assert again.status_code == 200
    assert again.headers["x-fam-audio-link"] == "1"
    assert again.headers["x-fam-cache"] == "hit"
    assert again.headers["x-sample-rate"] == "24000"
    assert again.headers["x-fam-episode"] == first.headers["x-fam-episode"]
    body = again.json()
    assert body["format"] == "ogg-opus" and body["frames"] > 0
    assert body["sentences"] and len(body["starts"]) == len(body["sentences"])
    name = body["link"].split("memory://", 1)[1].split("?", 1)[0]
    assert name in bucket.objects
    assert engine.calls == voiced, "a linked replay reached the voice"
    assert len(reserved) == 1, "a link must be spent like a stream"
    assert appmod._AUDIO_LINKS["issued"] == 1


@needs_opus
def test_only_a_replay_that_asks_for_opus_gets_a_link(tmp_path, monkeypatch, bucket):
    client, engine, appmod = _client(tmp_path, monkeypatch)
    _get(client)  # voiced and kept
    plain = _get(client)
    assert "x-fam-audio-link" not in plain.headers, "a client that did not ask"
    assert plain.headers["x-fam-audio-format"] == "opus"
    pcm = _get(client, fmt="pcm", link=1)
    assert "x-fam-audio-link" not in pcm.headers
    assert pcm.headers["content-type"].startswith("audio/L16")
    _set(monkeypatch, appmod, audio_links=False)
    off = _get(client, link=1)
    assert "x-fam-audio-link" not in off.headers, "AUDIO_LINKS=0 must stream"


@needs_opus
def test_no_bucket_means_a_stream(tmp_path, monkeypatch):
    audio_store.set_store(None)
    client, _, _ = _client(tmp_path, monkeypatch)
    _get(client, link=1)
    again = _get(client, link=1)
    assert again.status_code == 200
    assert "x-fam-audio-link" not in again.headers
    assert again.headers["x-fam-audio-format"] == "opus"


@needs_opus
def test_a_link_that_would_not_play_is_counted_and_streamed(tmp_path, monkeypatch, bucket):
    client, _, appmod = _client(tmp_path, monkeypatch)
    _get(client, link=1)
    plays = []
    monkeypatch.setattr(appmod, "_remembers", lambda request: True)
    monkeypatch.setattr(appmod, "_listener", lambda request: "listener-1")
    monkeypatch.setattr(appmod.EVENTS, "record", lambda event: plays.append(event))
    linked = _get(client, link=1)
    assert linked.headers["x-fam-audio-link"] == "1"
    fallback = _get(client, link_failed=1)
    assert len(plays) == 1, "one tap is one play, link and fallback together"
    assert fallback.status_code == 200
    assert fallback.headers["x-fam-audio-format"] == "opus"
    assert appmod._AUDIO_LINKS["fallbacks"] == 1
    links = client.get("/api/health").json()["audio"]["links"]
    assert links["fallbacks"] == 1 and links["enabled"] is True


def test_a_cross_origin_player_can_see_a_link():
    source = open(os.path.join(ROOT, "app.py")).read()
    exposed = source[source.index("expose_headers="):]
    assert '"X-FAM-Audio-Link"' in exposed[:exposed.index("]")]


# --- the player ---------------------------------------------------------------


def _player() -> str:
    return open(os.path.join(ROOT, "static", "fam-audio.js")).read()


def test_the_player_asks_for_a_link_only_with_opus_and_falls_back():
    js = _player()
    assert '"&fmt=opus&link=1"' in js, "a link only where Opus decodes"
    assert '"&fmt=pcm"' in js
    assert "&link_failed=1" in js, "a fallback the server can count"
    assert '"X-FAM-Audio-Link"' in js


@needs_opus
@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_file_becomes_exactly_the_packets_the_stream_sends():
    """The Ogg file read by `oggOpus`, in arbitrary pieces, is byte for byte
    what `/api/audio` passes through for the same kept episode."""
    from test_audio_store import speech

    pcm = speech(6.0)
    frames = len(pcm) // 2
    codec, blob = audio_codec.encode(pcm, 24000)
    assert codec == audio_codec.OPUS
    preskip, chunks = audio_codec.stored_packets(blob, frames, 24000)
    wire = b"".join(chunks)

    js = _player()
    start = js.index("  function oggOpus(")
    source = js[start:js.index("  /* Play from samples", start)]
    rng = random.Random(251)
    cuts, at = [], 0
    while at < len(blob):
        step = rng.choice([1, 7, 27, 300, 4096])
        cuts.append(list(blob[at:at + step]))
        at += step
    script = source + """
const pieces = %s;
let i = 0;
const source = { read: () => Promise.resolve(i < pieces.length
  ? { done: false, value: Uint8Array.from(pieces[i++]) } : { done: true }) };
oggOpus(source, %d).then(async ({ preskip, reader }) => {
  const out = [];
  for (;;) { const r = await reader.read(); if (r.done) break; out.push(...r.value); }
  process.stdout.write(JSON.stringify({ preskip, bytes: Buffer.from(out).toString("hex") }));
}).catch(e => { console.error(e); process.exit(1); });
""" % (json.dumps(cuts), frames)
    out = subprocess.run([shutil.which("node"), "-e", script], capture_output=True,
                         text=True, check=True).stdout
    got = json.loads(out)
    # The stream's pre-skip header is in 48 kHz samples, as OpusHead's is.
    assert got["preskip"] == preskip
    assert bytes.fromhex(got["bytes"]) == wire


# --- the hot copy's ceiling and the database's size ---------------------------


def test_the_hot_copy_is_small_in_postgres_unless_chosen(monkeypatch):
    _set(monkeypatch, cache_mod, audio_cache_max_mb=-1)
    monkeypatch.setattr(cache_mod.store_db, "enabled", lambda: False)
    assert cache_mod.audio_ceiling_mb() == 512
    monkeypatch.setattr(cache_mod.store_db, "enabled", lambda: True)
    assert cache_mod.audio_ceiling_mb() == 128
    assert cache_mod.audio_ceiling_bytes() == 128 * 1024 * 1024
    _set(monkeypatch, cache_mod, audio_cache_max_mb=2048)
    assert cache_mod.audio_ceiling_mb() == 2048, "a value set must win"
    _set(monkeypatch, cache_mod, audio_cache_max_mb=0)
    assert cache_mod.audio_ceiling_bytes() == 0, "0 is still no ceiling"


def test_unset_is_the_default(monkeypatch):
    import importlib

    import config
    monkeypatch.delenv("AUDIO_CACHE_MAX_MB", raising=False)
    monkeypatch.setenv("FAM_IGNORE_DOTENV", "1")
    try:
        importlib.reload(config)
        assert config.settings.audio_cache_max_mb == -1
    finally:
        importlib.reload(config)


def test_health_says_how_big_the_database_is(tmp_path, monkeypatch):
    client, _, _ = _client(tmp_path, monkeypatch)
    database = client.get("/api/health").json()["database"]
    assert isinstance(database["size_mb"], (int, float))
    assert isinstance(database["largest"], list) and len(database["largest"]) <= 3
    for entry in database["largest"]:
        assert set(entry) == {"name", "mb"}


@pytest.mark.skipif(not db_mod.enabled(), reason="needs DATABASE_URL (Postgres)")
def test_postgres_measures_itself():
    db_mod._SIZED[:] = [0.0, None]
    measured = db_mod.sizes()
    assert "error" not in measured, measured
    assert measured["total_bytes"] > 0
    assert all(isinstance(v, int) for v in measured["schemas"].values())


# --- review fixes: a linked replay is a replay in every count ------------------


def _plays(appmod, key):
    return appmod.SCRIPT_CACHE._conn().execute(
        "SELECT plays FROM scripts WHERE key = ?", (key,)).fetchone()[0]


@needs_opus
def test_a_linked_replay_counts_as_a_play_and_publishes_its_title(tmp_path, monkeypatch, bucket):
    """The play count slides an evergreen entry forward (§134); the title is
    what the player swaps in from /api/next."""
    import live_captions

    client, _, appmod = _client(tmp_path, monkeypatch)
    _get(client, link=1)
    key = appmod.SCRIPT_CACHE._conn().execute("SELECT key FROM scripts").fetchone()[0]
    before = _plays(appmod, key)
    appmod.SCRIPT_CACHE._conn().execute(
        "UPDATE scripts SET title = ? WHERE key = ?", ("Why The Sky Is Blue", key))
    linked = _get(client, link=1)
    assert linked.headers["x-fam-audio-link"] == "1"
    assert _plays(appmod, key) == before + 1, "a linked replay was not counted"
    assert live_captions.read_title(key) == ("Why The Sky Is Blue", True), (
        "a linked replay must publish its final title")


@needs_opus
def test_opus_switched_off_means_no_opus_file_either(tmp_path, monkeypatch, bucket):
    client, _, appmod = _client(tmp_path, monkeypatch)
    _get(client, link=1)
    _set(monkeypatch, audio_codec, audio_stream_opus=False)
    res = _get(client, link=1)
    assert "x-fam-audio-link" not in res.headers, "AUDIO_STREAM_OPUS=0 is always PCM"
    assert res.headers["content-type"].startswith("audio/L16")


@needs_opus
def test_a_fallback_writes_no_second_ledger_row(tmp_path, monkeypatch, bucket):
    client, _, appmod = _client(tmp_path, monkeypatch)
    _get(client, link=1)
    rows = []
    monkeypatch.setattr(appmod, "_record_usage", lambda *a, **k: rows.append(k))
    assert _get(client, link=1).headers["x-fam-audio-link"] == "1"
    _get(client, link_failed=1)
    assert len(rows) == 1, "one tap, one ledger row"


@needs_opus
def test_a_bare_episode_key_gets_the_id_the_stream_would_send(tmp_path, monkeypatch, bucket):
    client, _, appmod = _client(tmp_path, monkeypatch)
    first = _get(client)
    full = first.headers["x-fam-episode"]
    bare = full.split(".", 1)[0]
    linked = _get(client, link=1, episode=bare)
    assert linked.headers["x-fam-audio-link"] == "1"
    assert linked.headers["x-fam-episode"] == full
