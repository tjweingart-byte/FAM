"""§611: voiced just in time on a shared worker, and an episode saved,
shared or vibed while playing is finished and kept after its listener leaves.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import live_keep  # noqa: E402
import pipeline as pipeline_mod  # noqa: E402
import voice_schedule  # noqa: E402
from cache import MemoryScriptCache  # noqa: E402
from pipeline import GenerationStats, PodcastPipeline  # noqa: E402
from script_generator import plan_episode  # noqa: E402

from test_audio_cache import CountingVoice, lossless_packing  # noqa: E402,F401
from test_pipeline import FakeGenerator  # noqa: E402


# --- which live episodes are held ------------------------------------------

def test_a_save_holds_the_live_episode_of_that_question():
    a = live_keep.register("Why is the sky blue?", 3)
    b = live_keep.register("how tides work", 3)
    try:
        assert live_keep.hold("  why is the SKY blue? ", 3) == 1
        assert a.held and not b.held
        assert live_keep.hold("how tides work", 5) == 0, "other minutes"
        assert live_keep.hold("how tides work") == 1, "no minutes holds any"
        assert live_keep.hold("") == 0
    finally:
        live_keep.unregister(a)
        live_keep.unregister(b)


# --- pacing ------------------------------------------------------------------

class _Pace:
    def __init__(self, elapsed):
        self.elapsed = elapsed


class SharedVoice(CountingVoice):
    shares_workers = True


def _pipe(engine, monkeypatch, ahead):
    monkeypatch.setattr(pipeline_mod, "settings", dataclasses.replace(
        pipeline_mod.settings, voice_ahead_seconds=ahead))
    return PodcastPipeline(generator=FakeGenerator(), engine=engine,
                           cache=MemoryScriptCache())


def _stats(played: float) -> GenerationStats:
    stats = GenerationStats()
    stats.started_at = time.perf_counter() - played - 1.0
    stats.first_audio_at = 1.0  # first audio one second after the tap
    return stats


def test_a_listener_far_enough_ahead_waits(monkeypatch):
    pipe = _pipe(SharedVoice(), monkeypatch, ahead=1.0)
    stats = _stats(played=0.0)

    async def main():
        started = time.monotonic()
        await pipe._keep_pace(stats, _Pace(elapsed=1.3))
        return time.monotonic() - started

    assert 0.2 <= asyncio.run(main()) < 1.0


def test_nothing_waits_on_a_voice_on_this_machine(monkeypatch):
    pipe = _pipe(CountingVoice(), monkeypatch, ahead=1.0)

    async def main():
        await asyncio.wait_for(
            pipe._keep_pace(_stats(0.0), _Pace(elapsed=60.0)), 0.1)

    asyncio.run(main())


def test_nothing_waits_when_switched_off_or_detached(monkeypatch):
    pipe = _pipe(SharedVoice(), monkeypatch, ahead=0.0)
    asyncio.run(asyncio.wait_for(
        pipe._keep_pace(_stats(0.0), _Pace(elapsed=60.0)), 0.1))
    pipe = _pipe(SharedVoice(), monkeypatch, ahead=1.0)
    stats = _stats(0.0)
    stats.detached = True
    asyncio.run(asyncio.wait_for(pipe._keep_pace(stats, _Pace(elapsed=60.0)), 0.1))


def test_a_detached_episode_queues_behind_every_listener(monkeypatch):
    seen = []

    class Recording(CountingVoice):
        async def synth(self, text, wpm, voice=None):
            seen.append(voice_schedule.CURRENT.get().deadline)
            return await super().synth(text, wpm, voice)

    pipe = _pipe(Recording(), monkeypatch, ahead=0.0)
    stats = GenerationStats()
    stats.detached = True
    asyncio.run(pipe._synth("hello there.", 150, stats, _Pace(0.0)))
    assert seen == [voice_schedule.NO_DEADLINE]


# --- leaving part-way ---------------------------------------------------------

def _leave_after_first_chunk(pipe, plan, stats, hold):
    async def main():
        stream = pipe.stream_pcm(plan, stats)
        async for _ in stream:
            if hold:
                assert live_keep.hold(plan.query, plan.minutes) == 1
            break
        await stream.aclose()
        # Let a background finish run to its end.
        for _ in range(200):
            if not pipeline_mod._FINISHING:
                break
            await asyncio.sleep(0.01)
    asyncio.run(main())


def test_an_episode_left_part_way_is_stopped_and_not_kept():
    store = MemoryScriptCache()
    pipe = PodcastPipeline(generator=FakeGenerator(), engine=CountingVoice(),
                           cache=store)
    plan = plan_episode("the history of tea", 2)
    stats = GenerationStats()
    _leave_after_first_chunk(pipe, plan, stats, hold=False)
    assert not stats.detached
    assert store.stats()["audio_entries"] == 0
    assert live_keep.live_count() == 0


def test_a_saved_episode_left_part_way_is_finished_and_kept():
    store = MemoryScriptCache()
    engine = CountingVoice()
    pipe = PodcastPipeline(generator=FakeGenerator(), engine=engine, cache=store)
    plan = plan_episode("the history of coffee", 2)
    stats = GenerationStats()
    ledger = []
    stats.after_background.append(lambda: ledger.append(stats.voiced_seconds))
    _leave_after_first_chunk(pipe, plan, stats, hold=True)
    assert stats.detached
    assert store.stats()["audio_entries"] == 1, "the whole episode was kept"
    assert ledger and ledger[0] > 0, "its ledger row waited for the finish"
    assert live_keep.live_count() == 0

    # And it replays from the bucket, never the voice.
    calls = engine.calls
    replay = GenerationStats()

    async def again():
        async for _ in pipe.stream_pcm(plan, replay):
            pass
    asyncio.run(again())
    assert replay.audio == "stored" and engine.calls == calls


def test_an_episode_that_fails_still_raises_to_its_listener():
    class Broken(CountingVoice):
        async def synth(self, text, wpm, voice=None):
            raise RuntimeError("the voice went away")

    pipe = PodcastPipeline(generator=FakeGenerator(), engine=Broken(),
                           cache=MemoryScriptCache())

    async def main():
        async for _ in pipe.stream_pcm(plan_episode("why cats purr", 1),
                                       GenerationStats()):
            pass

    with pytest.raises(RuntimeError, match="went away"):
        asyncio.run(main())
