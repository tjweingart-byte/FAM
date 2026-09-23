"""Kept audio (PROBLEMS.md §131): a cached episode never goes back to the voice.

The voice runs on a rented GPU, and replaying cached episodes through it was
the largest line on the bill. So the first time an episode is spoken in a
production voice its PCM is kept beside its script, and every later play is
read out of the database. These tests count engine calls, because "the audio
came from the cache" and "the audio was synthesised again and happened to
match" sound identical.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import live_captions  # noqa: E402
from cache import MemoryScriptCache, SqliteScriptCache  # noqa: E402
from config import settings  # noqa: E402
from pipeline import GenerationStats, PodcastPipeline  # noqa: E402
from script_generator import plan_episode  # noqa: E402
from tts import DebugEngine  # noqa: E402

from test_pipeline import FakeGenerator  # noqa: E402


class CountingVoice(DebugEngine):
    """A production-shaped engine: keeps its audio, and counts every call."""

    name = "countingvoice"
    keeps_audio = True

    def __init__(self) -> None:
        self.calls = 0

    async def synth(self, text, wpm, voice=None):
        self.calls += 1
        return await super().synth(text, wpm, voice)


class CountingTone(CountingVoice):
    """The placeholder's shape: speaks, and must never be kept."""

    name = "countingtone"
    keeps_audio = False


class CountingGenerator(FakeGenerator):
    def __init__(self) -> None:
        super().__init__(ratio=1.0)
        self.calls = 0

    async def stream_sentences(self, plan, notes=None):
        self.calls += 1
        async for s in super().stream_sentences(plan, notes):
            yield s


def play(pipeline, plan):
    stats = GenerationStats()

    async def run():
        out = bytearray()
        async for chunk in pipeline.stream_pcm(plan, stats):
            out.extend(chunk)
        return bytes(out)

    return asyncio.run(run()), stats


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        return MemoryScriptCache()
    return SqliteScriptCache(str(tmp_path / "scripts.db"))


def test_a_second_play_never_calls_the_voice(store):
    engine, gen = CountingVoice(), CountingGenerator()
    pipe = PodcastPipeline(generator=gen, engine=engine, cache=store)
    plan = plan_episode("why the sky is blue", 1)

    first, s1 = play(pipe, plan)
    spoken = engine.calls
    assert spoken > 0 and s1.cache == "miss" and s1.audio == "kept"

    second, s2 = play(pipe, plan)
    assert engine.calls == spoken, "a cached episode went back to the voice engine"
    assert gen.calls == 1
    assert s2.cache == "hit" and s2.audio == "stored"
    assert second == first, "the replay is not the audio that was first streamed"
    assert s2.sentences == s1.sentences and s2.script == s1.script
    assert s2.audio_seconds == pytest.approx(s1.audio_seconds, abs=0.01)


def test_a_script_cached_before_audio_was_kept_is_voiced_once_more_then_kept(store):
    """Every script written before §131 has no audio: the first play of one
    pays for synthesis a last time, and nothing after it does."""
    engine = CountingVoice()
    plan = plan_episode("how tides work", 1)
    writer = PodcastPipeline(generator=FakeGenerator(), engine=CountingTone(), cache=store)
    play(writer, plan)  # a placeholder writes the script and keeps no audio

    pipe = PodcastPipeline(generator=CountingGenerator(), engine=engine, cache=store)
    _, s1 = play(pipe, plan)
    assert s1.cache == "hit" and s1.audio == "kept" and engine.calls > 0
    calls = engine.calls
    _, s2 = play(pipe, plan)
    assert s2.audio == "stored" and engine.calls == calls


def test_a_placeholder_tone_is_never_kept(store):
    """A tone written here would be served long after the voice came back."""
    engine = CountingTone()
    pipe = PodcastPipeline(generator=FakeGenerator(), engine=engine, cache=store)
    plan = plan_episode("what is a black hole", 1)
    _, s1 = play(pipe, plan)
    calls = engine.calls
    _, s2 = play(pipe, plan)
    assert s1.audio == "" and s2.audio == "" and engine.calls > calls
    assert store.stats()["audio_entries"] == 0


def test_audio_is_per_voice(store):
    """A voice changes the audio and not the words: the script is shared, the
    audio is not."""
    engine = CountingVoice()
    plan = plan_episode("how vaccines work", 1)
    play(PodcastPipeline(generator=FakeGenerator(), engine=engine, cache=store,
                         voice="countingvoice:a"), plan)
    calls = engine.calls
    _, other = play(PodcastPipeline(generator=CountingGenerator(), engine=engine,
                                    cache=store, voice="countingvoice:b"), plan)
    assert other.cache == "hit" and other.audio == "kept" and engine.calls > calls


def test_switching_it_off_restores_resynthesis(store, monkeypatch):
    import dataclasses

    import pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "settings",
                        dataclasses.replace(settings, audio_cache=False))
    engine = CountingVoice()
    pipe = PodcastPipeline(generator=FakeGenerator(), engine=engine, cache=store)
    plan = plan_episode("why leaves change colour", 1)
    play(pipe, plan)
    calls = engine.calls
    _, s2 = play(pipe, plan)
    assert s2.cache == "hit" and s2.audio == "" and engine.calls > calls


def test_demo_mode_keeps_no_audio(store):
    """`cache_writes=False` writes nothing, audio included."""
    plan = plan_episode("how bees communicate", 1)
    play(PodcastPipeline(generator=FakeGenerator(), engine=CountingTone(),
                         cache=store), plan)  # a script, no audio
    reader = PodcastPipeline(generator=FakeGenerator(), engine=CountingVoice(),
                             cache=store, cache_writes=False)
    _, s = play(reader, plan)
    assert s.cache == "hit" and s.audio == "" and store.stats()["audio_entries"] == 0


def test_an_abandoned_stream_keeps_nothing(store):
    """Only a whole episode is kept - half of one would be replayed as all."""
    engine = CountingVoice()
    pipe = PodcastPipeline(generator=FakeGenerator(), engine=engine, cache=store)
    plan = plan_episode("the history of tea", 2)

    async def partial():
        stream = pipe.stream_pcm(plan, GenerationStats())
        async for _ in stream:
            break
        await stream.aclose()

    asyncio.run(partial())
    assert store.stats()["audio_entries"] == 0


def test_a_rewritten_script_drops_the_audio_it_no_longer_matches(store):
    engine = CountingVoice()
    pipe = PodcastPipeline(generator=FakeGenerator(), engine=engine, cache=store)
    plan = plan_episode("how rainbows form", 1)
    play(pipe, plan)
    assert store.stats()["audio_entries"] == 1
    key = next(iter(store._data)) if isinstance(store, MemoryScriptCache) else \
        sqlite3.connect(store.path).execute("SELECT key FROM scripts").fetchone()[0]
    store.put(key, ["Different words entirely."], ttl=60, query="how rainbows form")
    assert store.stats()["audio_entries"] == 0


def test_audio_is_only_readable_while_its_script_is(tmp_path):
    store = SqliteScriptCache(str(tmp_path / "s.db"))
    store.put("k", ["One."], ttl=60, query="q")
    assert store.put_audio("k", "v", 24000, b"\x01\x00" * 100, ["One."], [0.0])
    assert store.get_audio("k", "v", 24000).pcm == b"\x01\x00" * 100
    # Wrong rate: the header is already written, so this would play at the
    # wrong pitch rather than fail.
    assert store.get_audio("k", "v", 48000) is None
    assert not store.put_audio("orphan", "v", 24000, b"\x00\x00", [], [])
    store.put("k", ["One."], ttl=-1, query="q")  # expire the script
    assert store.get_audio("k", "v", 24000) is None
    assert store.purge_expired() == 1
    assert store.stats()["audio_entries"] == 0


def test_clear_and_forget_author_take_the_audio_too(tmp_path):
    store = SqliteScriptCache(str(tmp_path / "s.db"))
    store.put("a", ["One."], ttl=60, query="q", author="seed")
    store.put("b", ["Two."], ttl=60, query="r")
    store.put_audio("a", "v", 24000, b"\x00\x00" * 10, ["One."], [0.0])
    store.put_audio("b", "v", 24000, b"\x00\x00" * 10, ["Two."], [0.0])
    store.forget_author("seed")
    assert store.stats()["audio_entries"] == 1
    store.clear()
    assert store.stats()["audio_entries"] == 0


def test_the_ceiling_evicts_least_recently_played_audio_first(tmp_path, monkeypatch):
    import cache as cache_mod

    store = SqliteScriptCache(str(tmp_path / "s.db"))
    # Incompressible bytes so the ceiling is exercised on real sizes.
    blob = os.urandom(400_000)
    monkeypatch.setattr(cache_mod, "audio_ceiling_bytes", lambda: 1_000_000)
    for key in ("old", "mid", "new"):
        store.put(key, ["x."], ttl=60, query=key)
    store.put_audio("old", "v", 24000, blob, ["x."], [0.0])
    store.put_audio("mid", "v", 24000, blob, ["x."], [0.0])
    assert store.get_audio("old", "v", 24000) is not None  # played: now newest
    store.put_audio("new", "v", 24000, blob, ["x."], [0.0])
    assert store.has_audio("old", "v", 24000)
    assert not store.has_audio("mid", "v", 24000)
    assert store.has_audio("new", "v", 24000)
    # The script survives eviction: the next play re-voices it once.
    assert store.get("mid") == ["x."]


def test_a_stored_replay_publishes_its_captions(store):
    engine = CountingVoice()
    pipe = PodcastPipeline(generator=FakeGenerator(), engine=engine, cache=store)
    plan = plan_episode("why cats purr", 1)
    _, first = play(pipe, plan)
    _, second = play(pipe, plan)
    assert second.audio == "stored"
    lines, done = live_captions.read(second.caption_key)
    assert done and lines == first.script
    assert live_captions.read_starts(second.caption_key) == pytest.approx(
        first.starts, abs=0.01)


def test_the_wake_is_skipped_for_an_episode_with_kept_audio(store):
    engine = CountingVoice()
    pipe = PodcastPipeline(generator=FakeGenerator(), engine=engine, cache=store)
    plan = plan_episode("how glaciers move", 1)
    assert asyncio.run(pipe.has_stored_audio(plan)) is False
    play(pipe, plan)
    assert asyncio.run(pipe.has_stored_audio(plan)) is True


def test_only_the_production_voices_keep_audio():
    from remote_voice import RemoteChatterboxEngine
    from tts import PLACEHOLDER_ENGINE, ChatterboxEngine, DEV_ENGINES

    assert ChatterboxEngine.keeps_audio and RemoteChatterboxEngine.keeps_audio
    assert not PLACEHOLDER_ENGINE.keeps_audio
    assert not any(cls.keeps_audio for cls in DEV_ENGINES.values())


def test_a_stored_replay_is_not_metered_as_gpu_time(store):
    """Metering allocates GPU cost from the seconds the voice made. A replay
    out of kept audio made none, and billing it would put the saving back in
    the ledger as a cost."""
    engine = CountingVoice()
    pipe = PodcastPipeline(generator=FakeGenerator(), engine=engine, cache=store)
    plan = plan_episode("how comets form", 1)
    _, first = play(pipe, plan)
    _, second = play(pipe, plan)
    assert first.voiced_seconds == first.audio_seconds > 0
    assert second.audio == "stored" and second.voiced_seconds == 0.0
    assert second.audio_seconds > 0


def test_the_audio_endpoint_meters_voiced_seconds_not_played_ones():
    import pathlib

    source = (pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text()
    assert "audio_seconds=stats.audio_seconds" not in source
    assert source.count("audio_seconds=stats.voiced_seconds") == 2


def test_an_episode_larger_than_the_ceiling_is_not_reported_kept(tmp_path, monkeypatch):
    import cache as cache_mod

    store = SqliteScriptCache(str(tmp_path / "s.db"))
    monkeypatch.setattr(cache_mod, "audio_ceiling_bytes", lambda: 1000)
    store.put("k", ["x."], ttl=60, query="k")
    assert not store.put_audio("k", "v", 24000, os.urandom(5000), ["x."], [0.0])
    assert store.stats()["audio_entries"] == 0
