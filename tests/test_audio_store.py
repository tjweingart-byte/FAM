"""Kept audio in a bucket, packed as Opus (PROBLEMS.md §235).

Three promises, each tested by what it would cost if it broke:

* **Opus changes nothing a listener can measure but size**: the decoded PCM
  is exactly as long as what was voiced (or a caption moves), and production
  rates are Opus while a rate Opus cannot carry stays zlib.
* **The database names the object; the bucket holds it**: a row is written
  only after its upload, a play with no hot copy reads the bucket, and an
  object the bucket lost costs one re-voicing, never a failed play.
* **The week (owner's direction)**: every episode's audio is kept a week;
  then saved, shared or vibed audio moves to `kept/` in Infrequent Access
  and everything else is deleted - and every way a row is dropped deletes
  its object, so nothing is paid for that nothing points at.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import os
import sys
import time
import wave
import zlib

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audio_codec  # noqa: E402
import audio_store  # noqa: E402
import cache as cache_mod  # noqa: E402
from cache import SqliteScriptCache, cache_key  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEEK = 7 * 86400

needs_opus = pytest.mark.skipif(not audio_codec.available()[0],
                                reason="PyAV is not installed")


def speech(seconds: float, rate: int = 24000) -> bytes:
    """The reference recording at `rate`, looped to `seconds`."""
    with wave.open(os.path.join(ROOT, "reference_3.wav")) as w:
        raw = w.readframes(w.getnframes())
        source_rate, channels = w.getframerate(), w.getnchannels()
    import numpy as np

    samples = np.frombuffer(raw, dtype="<i2")[::channels]
    step = max(1, source_rate // rate)
    samples = samples[::step]
    want = int(seconds * rate)
    return np.tile(samples, want // len(samples) + 1)[:want].astype("<i2").tobytes()


@pytest.fixture
def bucket():
    store = audio_store.MemoryAudioStore()
    audio_store.set_store(store)
    yield store
    audio_store.set_store(None)


@pytest.fixture
def db(tmp_path):
    return SqliteScriptCache(str(tmp_path / "scripts.db"))


def keep(db, key, pcm=None, voice="v"):
    db.put(key, ["One."], ttl=WEEK, query=key, minutes=2)
    assert db.put_audio(key, voice, 24000, pcm or speech(2), ["One."], [0.0])


def age(db, key, seconds):
    db._conn().execute("UPDATE episode_audio SET created = created - ? WHERE key = ?",
                       (seconds, key))


def row(db, key, voice="v"):
    return db._conn().execute(
        "SELECT object_key, tier, length(pcm), codec FROM episode_audio"
        " WHERE key = ? AND voice = ?", (key, voice)).fetchone()


# -- the codec ----------------------------------------------------------------


@needs_opus
def test_opus_is_a_twelfth_of_zlib_and_exactly_as_long():
    pcm = speech(60)
    codec, blob = audio_codec.encode(pcm, 24000)
    assert codec == audio_codec.OPUS
    zipped = zlib.compress(pcm, audio_codec.ZLIB_LEVEL)
    assert len(blob) * 9 < len(zipped), (len(blob), len(zipped))
    # 24 kbps is 3 KB a second, plus the container.
    assert len(blob) < 60 * 3000 * 1.1
    back = audio_codec.decode(codec, blob, 24000, len(pcm) // 2)
    assert len(back) == len(pcm), "the decoded episode is not the voiced length"


@needs_opus
def test_decoding_is_a_slice_at_a_time():
    pcm = speech(10)
    codec, blob = audio_codec.encode(pcm, 24000)
    pieces = list(audio_codec.slices(codec, blob, 24000, len(pcm) // 2))
    assert len(pieces) >= 9, "decoded whole: the first word would wait for all of it"
    assert sum(map(len, pieces)) == len(pcm)


def test_a_rate_opus_cannot_carry_stays_zlib():
    assert audio_codec.encode(b"\x01\x00" * 500, 22050)[0] == audio_codec.ZLIB


def test_zlib_is_a_setting(monkeypatch):
    monkeypatch.setattr(audio_codec, "settings", dataclasses.replace(
        audio_codec.settings, audio_codec="zlib"))
    codec, blob = audio_codec.encode(b"\x01\x00" * 500, 24000)
    assert codec == audio_codec.ZLIB
    assert audio_codec.decode(codec, blob, 24000) == b"\x01\x00" * 500


@needs_opus
def test_rows_from_before_opus_still_play(db):
    """A zlib row written by §132 has no codec column value of its own."""
    db.put("old", ["One."], ttl=WEEK, query="old")
    pcm = b"\x02\x00" * 480
    db._conn().execute(
        "INSERT INTO episode_audio (key, voice, sample_rate, created, played,"
        " bytes, sentences, starts, pcm) VALUES ('old', 'v', 24000, ?, ?, 1,"
        " '[\"One.\"]', '[0.0]', ?)", (time.time(), time.time(), zlib.compress(pcm)))
    assert db.get_audio("old", "v", 24000).pcm == pcm


# -- the request signature ----------------------------------------------------


def test_requests_are_signed_as_botocore_signs_them():
    """Computed by botocore's S3SigV4Auth for these exact requests; pinned so
    a change to the hand-written signer fails here, not against R2."""
    at = dt.datetime(2026, 10, 9, 17, 15, 22, tzinfo=dt.timezone.utc)
    key = ("AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY")
    get = audio_store.sign("GET", "acct.r2.cloudflarestorage.com",
                           "/fam-audio/kept/x y.opus", {}, b"", *key, at)
    assert get["authorization"].endswith(
        "Signature=12da25f43fd48f6327b4a70243f9bb5d1d7ea6a67475bf35876e421f0b94abaf")
    copy = audio_store.sign(
        "PUT", "acct.r2.cloudflarestorage.com", "/fam-audio/kept/a.opus",
        {"x-amz-copy-source": "/fam-audio/recent/a.opus",
         "x-amz-storage-class": "STANDARD_IA"}, b"", *key, at)
    assert copy["authorization"].endswith(
        "Signature=21cc85bf52d8fdc7594560379185ce531eb46cad232d47b99aff0a9ec1c0d938")


def test_a_half_configured_bucket_is_reported_not_used(monkeypatch):
    import config

    monkeypatch.setattr(audio_store, "settings", dataclasses.replace(
        config.settings, audio_store="r2", audio_bucket="fam-audio"))
    audio_store.set_store(None)
    assert audio_store.get_store() is None
    assert "R2_ACCOUNT_ID" in audio_store.status()["error"]
    audio_store.set_store(None)


def test_verify_writes_reads_and_deletes(bucket):
    assert audio_store.verify()["ok"]
    assert [c[0] for c in bucket.calls] == ["put", "get", "delete"]
    assert not bucket.objects


# -- the database names it, the bucket holds it -------------------------------


def test_new_audio_goes_to_recent_and_the_row_names_it(db, bucket):
    keep(db, "a")
    name, tier, hot, codec = row(db, "a")
    assert name.startswith(audio_store.RECENT) and name in bucket.objects
    assert tier == "" and hot > 0
    if audio_codec.available()[0]:
        assert codec == audio_codec.OPUS


def test_a_failed_upload_keeps_the_audio_here_alone(db, bucket, monkeypatch):
    def refuse(*_a, **_k):
        raise audio_store.AudioStoreError("down")

    real = bucket.put
    monkeypatch.setattr(bucket, "put", refuse)
    keep(db, "a")
    assert row(db, "a")[0] == "", "a row named an object that was never written"
    assert db.get_audio("a", "v", 24000) is not None
    # The next sweep puts it in the bucket.
    monkeypatch.setattr(bucket, "put", real)
    assert db.sweep_audio(set())["uploaded"] == 1
    assert row(db, "a")[0] in bucket.objects


def test_audio_from_before_the_bucket_is_backfilled_then_kept(db):
    keep(db, "old")  # no bucket yet
    age(db, "old", WEEK + 60)
    store = audio_store.MemoryAudioStore()
    audio_store.set_store(store)
    try:
        counts = db.sweep_audio({"old"})
    finally:
        audio_store.set_store(None)
    assert counts["uploaded"] == 1 and counts["kept"] == 1
    name = row(db, "old")[0]
    assert name.startswith(audio_store.KEPT)
    assert store.objects[name][1] == audio_store.INFREQUENT


def test_an_evicted_hot_copy_is_read_back_from_the_bucket(db, bucket, monkeypatch):
    monkeypatch.setattr(cache_mod, "audio_ceiling_bytes", lambda: 1)
    keep(db, "a")
    assert row(db, "a")[2] == 0, "the hot copy outlived the ceiling"
    assert db.has_audio("a", "v", 24000), "eviction lost audio the bucket holds"
    assert db.get_audio("a", "v", 24000) is not None
    assert ("get", row(db, "a")[0]) in bucket.calls


def test_an_object_the_bucket_lost_costs_a_revoicing_not_a_failure(db, bucket, monkeypatch):
    monkeypatch.setattr(cache_mod, "audio_ceiling_bytes", lambda: 1)
    keep(db, "a")
    bucket.objects.clear()
    assert db.get_audio("a", "v", 24000) is None
    assert not db.has_audio("a", "v", 24000)


# -- the week -----------------------------------------------------------------


def test_a_week_old_held_episode_moves_to_kept_in_infrequent_access(db, bucket):
    keep(db, "saved")
    first = row(db, "saved")[0]
    age(db, "saved", WEEK + 60)
    counts = db.sweep_audio({"saved"})
    assert counts["kept"] == 1
    name, tier, hot, _ = row(db, "saved")
    assert tier == "kept" and name.startswith(audio_store.KEPT)
    assert bucket.objects[name][1] == audio_store.INFREQUENT
    assert hot == 0, "a cold episode kept its hot copy"
    db.drain_deletes()
    assert first not in bucket.objects, "the recent copy was paid for twice"
    assert db.get_audio("saved", "v", 24000) is not None


def test_a_week_old_episode_nobody_holds_is_deleted(db, bucket):
    keep(db, "heard")
    name = row(db, "heard")[0]
    age(db, "heard", WEEK + 60)
    assert db.sweep_audio(set())["deleted"] == 1
    assert row(db, "heard") is None
    db.drain_deletes()
    assert name not in bucket.objects
    # The script is untouched: the week governs audio, not episodes.
    assert db.get("heard", current=False) == ["One."]


def test_nothing_younger_than_a_week_is_touched(db, bucket):
    keep(db, "new")
    age(db, "new", WEEK - 3600)
    assert db.sweep_audio(set()) == {"kept": 0, "deleted": 0, "released": 0,
                                     "failed": 0, "uploaded": 0}
    assert row(db, "new")[1] == ""


def test_kept_audio_nobody_holds_any_more_is_released(db, bucket):
    keep(db, "a")
    age(db, "a", WEEK + 60)
    db.sweep_audio({"a"})
    name = row(db, "a")[0]
    assert db.sweep_audio(set(), check_kept=False)["released"] == 0
    assert db.sweep_audio(set())["released"] == 1
    db.drain_deletes()
    assert name not in bucket.objects


def test_a_failed_copy_is_tried_again_and_deletes_nothing(db, bucket, monkeypatch):
    keep(db, "a")
    age(db, "a", WEEK + 60)
    real = bucket.copy

    def refuse(*_a, **_k):
        raise audio_store.AudioStoreError("down")

    monkeypatch.setattr(bucket, "copy", refuse)
    assert db.sweep_audio({"a"})["failed"] == 1
    assert row(db, "a")[1] == "" and row(db, "a")[0] in bucket.objects
    monkeypatch.setattr(bucket, "copy", real)
    assert db.sweep_audio({"a"})["kept"] == 1


def test_kept_audio_pins_its_script_and_survives_the_purge(db, bucket):
    keep(db, "a")
    age(db, "a", WEEK + 60)
    db.sweep_audio({"a"})
    expires = db._conn().execute("SELECT expires FROM scripts WHERE key='a'").fetchone()[0]
    assert expires >= time.time() + WEEK
    db._conn().execute("UPDATE scripts SET expires = 1 WHERE key = 'a'")
    db.purge_expired()
    assert row(db, "a") is not None, "a saved episode's audio was purged with its clock"


def test_an_archived_episode_is_not_kept_for_its_old_save(db, bucket):
    """A save points at the question; once its key holds new words, the old
    words' audio is history, not something anybody saved."""
    keep(db, "a")
    db.put("a", ["Two."], ttl=WEEK, query="a", minutes=2)  # re-written
    archived = db._conn().execute(
        "SELECT key FROM episode_audio").fetchone()[0]
    assert archived != "a"
    age(db, archived, WEEK + 60)
    assert db.sweep_audio({"a", archived})["deleted"] == 1


@pytest.mark.parametrize("drop", ["clear", "forget_author", "rewrite", "purge"])
def test_every_way_a_row_is_dropped_deletes_its_object(db, bucket, drop):
    db.put("a", ["One."], ttl=WEEK, query="a", author="seed")
    db.put_audio("a", "v", 24000, speech(1), ["One."], [0.0])
    name = row(db, "a")[0]
    if drop == "clear":
        db.clear()
    elif drop == "forget_author":
        db.forget_author("seed")
    elif drop == "rewrite":
        # A key re-written after its row expired has nothing to archive.
        db._conn().execute("UPDATE scripts SET expires = 1 WHERE key = 'a'")
        db.put("a", ["Two."], ttl=WEEK, query="a")
    else:
        db._conn().execute("UPDATE scripts SET expires = 1 WHERE key = 'a'")
        db.purge_expired()
    db.drain_deletes()
    assert name not in bucket.objects, f"{drop} left an object nothing points at"


def test_an_export_carries_bytes_never_a_bucket_name(db, bucket, tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "audio_ceiling_bytes", lambda: 1)
    keep(db, "a")
    data = db.export_episode("a")
    assert data["audio"] and data["audio"][0]["object_key"] == ""
    audio_store.set_store(None)
    other = SqliteScriptCache(str(tmp_path / "staging.db"))
    other.import_episode(data, keep_until=time.time() + WEEK)
    assert other.get_audio("a", "v", 24000) is not None


# -- what is held -------------------------------------------------------------


def test_held_keys_are_the_keys_a_replay_plays():
    import app

    app.SAVED.save("u1", "why the sky is blue", 2)
    app.SHARES.create("u2", "how tides work", 3)
    app.SOCIAL.echo("u3", "what is a black hole", "", 1, "")
    held = app._held_episode_keys()
    for query, minutes in (("why the sky is blue", 2), ("how tides work", 3),
                           ("what is a black hole", 1)):
        assert cache_key(query, minutes, None, "", True) in held
    assert cache_key("why the sky is blue", 3, None, "", True) not in held


def test_staging_never_reaches_the_bucket():
    import spend_guard

    assert spend_guard.FORCED["AUDIO_STORE"] == ""
    assert {"R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"} <= set(spend_guard.PAID_CREDENTIALS)


def test_the_bucket_rules_agree_with_the_settings():
    """A setting is settled only where it is copied: the bucket deletes
    `recent/` one day after FAM stops reading it, and never deletes `kept/`."""
    import json

    import config

    with open(os.path.join(ROOT, "deploy", "r2-lifecycle.json")) as f:
        rules = {r["conditions"]["prefix"]: r for r in json.load(f)["rules"]}
    days = config.Settings().audio_recent_days
    assert rules[audio_store.RECENT]["deleteObjectsTransition"]["condition"]["maxAge"] \
        == (days + 1) * 86400
    assert "deleteObjectsTransition" not in rules[audio_store.KEPT]
