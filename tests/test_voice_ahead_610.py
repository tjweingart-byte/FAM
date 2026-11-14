"""§610: an always-on worker with nobody to voice voices the episodes the
editions already wrote, so a tap on one is a replay of kept audio - and a
listener always comes first."""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import voice_ahead  # noqa: E402
import voice_schedule  # noqa: E402
from cache import MemoryScriptCache, SqliteScriptCache  # noqa: E402
from pipeline import PodcastPipeline, key_for  # noqa: E402
from script_generator import plan_episode  # noqa: E402

from test_audio_cache import (CountingGenerator, CountingTone,  # noqa: E402
                              CountingVoice, play)
from test_pipeline import FakeGenerator  # noqa: E402


def configure(monkeypatch, **changes):
    """Settings are frozen: each module that reads them gets a copy."""
    import pipeline
    import remote_voice

    for module in (pipeline, voice_ahead, remote_voice):
        monkeypatch.setattr(module, "settings", dataclasses.replace(
            module.settings, **changes))


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    import audio_codec

    monkeypatch.setattr(audio_codec, "settings", dataclasses.replace(
        audio_codec.settings, audio_codec="zlib"))
    configure(monkeypatch, voice_ahead_quiet_seconds=0.0)
    voice_schedule.reset()
    voice_ahead.reset()
    yield
    voice_schedule.reset()
    voice_ahead.reset()


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        return MemoryScriptCache()
    return SqliteScriptCache(str(tmp_path / "scripts.db"))


def written_ahead(store, query, origin="myfam"):
    """An edition's episode: the script in the cache, no audio anywhere."""
    writer = PodcastPipeline(generator=FakeGenerator(), engine=CountingTone(),
                             cache=store)
    writer.origin = origin
    plan = plan_episode(query, 1)
    play(writer, plan)
    return plan, asyncio.run(key_for(plan))


def run_once(store, engine):
    return asyncio.run(voice_ahead.run_once(
        store, lambda _key: None,
        lambda voice: PodcastPipeline(generator=CountingGenerator(), engine=engine,
                                      cache=store, voice=voice)))


def test_a_written_ahead_episode_is_voiced_and_its_tap_is_a_replay(store):
    plan, key = written_ahead(store, "why the eagles traded for a safety")
    engine = CountingVoice()
    assert run_once(store, engine) == "voiced"
    voiced = engine.calls
    assert voiced > 0
    assert store.has_any_audio(key)

    gen = CountingGenerator()
    tap = PodcastPipeline(generator=gen, engine=engine, cache=store)
    _, stats = play(tap, plan)
    assert stats.audio == "stored", "the tap voiced it again"
    assert engine.calls == voiced and gen.calls == 0


def test_voicing_ahead_is_not_a_play(store):
    _, key = written_ahead(store, "how the fed sets rates")
    before = store.plays(key)
    assert run_once(store, CountingVoice()) == "voiced"
    assert store.plays(key) == before, "voicing ahead counted as a play"


def test_nothing_left_to_voice_says_so(store):
    written_ahead(store, "how tides work")
    engine = CountingVoice()
    assert run_once(store, engine) == "voiced"
    calls = engine.calls
    assert run_once(store, engine) == "nothing"
    assert engine.calls == calls


def test_a_search_is_not_voiced_ahead(store):
    """Only what the editions wrote ahead: a search was voiced when it was
    asked, and a searched script with no audio is not a likely tap."""
    written_ahead(store, "who won the election in peru", origin="search")
    assert run_once(store, CountingVoice()) == "nothing"


def test_every_edition_is_voiced_ahead(store):
    for origin in ("dailyfam", "trending", "startup", "push"):
        written_ahead(store, f"a {origin} story about rivers", origin=origin)
    engine = CountingVoice()
    outcomes = [run_once(store, engine) for _ in range(5)]
    assert outcomes == ["voiced"] * 4 + ["nothing"]


def test_a_placeholder_voice_voices_nothing_ahead(store):
    written_ahead(store, "how tides work")
    assert run_once(store, CountingTone()) == "off"


def test_no_chunk_starts_while_a_listener_is_in_progress(store):
    written_ahead(store, "how volcanoes form")
    engine = CountingVoice()

    async def main():
        token = voice_schedule.listener_started()
        task = asyncio.create_task(voice_ahead.run_once(
            store, lambda _k: None,
            lambda v: PodcastPipeline(generator=CountingGenerator(),
                                      engine=engine, cache=store, voice=v)))
        await asyncio.sleep(1.2)
        held = engine.calls
        voice_schedule.listener_finished(token)
        outcome = await task
        return held, outcome

    held, outcome = asyncio.run(main())
    assert held == 0, "voiced ahead while a listener's episode was in progress"
    assert outcome == "voiced" and engine.calls > 0


def test_a_listeners_episode_holds_the_voice_until_it_ends(store):
    engine = CountingVoice()
    pipe = PodcastPipeline(generator=CountingGenerator(), engine=engine, cache=store)
    plan = plan_episode("why the sky is blue", 1)
    seen = []

    async def main():
        async for _ in pipe.stream_pcm(plan):
            seen.append(voice_schedule.listeners())

    asyncio.run(main())
    assert seen and all(n == 1 for n in seen)
    assert voice_schedule.listeners() == 0

    seen.clear()
    asyncio.run(main())  # kept audio now: a replay needs no voice
    assert seen and all(n == 0 for n in seen[1:])
    assert voice_schedule.listeners() == 0


def test_quiet_waits_after_the_last_listener():
    token = voice_schedule.listener_started()
    assert not voice_schedule.quiet(0.0)
    voice_schedule.listener_finished(token)
    assert voice_schedule.quiet(0.0)
    assert not voice_schedule.quiet(60.0)


def test_a_dropped_stream_cannot_hold_the_worker_forever():
    voice_schedule.listener_started()
    later = time.monotonic() + voice_schedule.LISTENER_HORIZON + 1
    assert voice_schedule.listeners(later) == 0


def test_a_background_chunk_queues_behind_every_listener():
    """The gate's order (§609) as voicing ahead uses it."""
    async def main():
        gate = voice_schedule.DeadlineGate(1)
        order = []
        await gate.acquire()

        async def call(name, deadline):
            await gate.acquire(deadline)
            order.append(name)
            gate.release()

        tasks = [asyncio.create_task(call("ahead", voice_schedule.NO_DEADLINE))]
        await asyncio.sleep(0)
        tasks.append(asyncio.create_task(call("search", time.monotonic())))
        await asyncio.sleep(0)
        gate.release()
        await asyncio.gather(*tasks)
        return order

    assert asyncio.run(main()) == ["search", "ahead"]


def test_a_failed_episode_is_not_retried_at_once(store, monkeypatch):
    written_ahead(store, "how glaciers move")

    class Failing(CountingVoice):
        async def synth(self, text, wpm, voice=None):
            raise RuntimeError("worker down")

    with pytest.raises(RuntimeError):
        run_once(store, Failing())
    assert run_once(store, CountingVoice()) == "nothing"


def test_voicing_ahead_never_reports_the_voice_fell_behind(store, monkeypatch):
    import pipeline_errors

    recorded = []
    monkeypatch.setattr(pipeline_errors, "record",
                        lambda *a, **k: recorded.append(a))
    written_ahead(store, "how bridges hold up")

    class Slow(CountingVoice):
        async def synth(self, text, wpm, voice=None):
            await asyncio.sleep(0.05)
            return await super().synth(text, wpm, voice)

    assert run_once(store, Slow()) == "voiced"
    assert not [r for r in recorded if "fell behind" in str(r)]


# ---- only where an idle second is already paid for -----------------------

def _remote(monkeypatch, transport, active):
    import remote_voice

    configure(monkeypatch, remote_voice_transport=transport,
              remote_voice_active_workers=active)
    engine = CountingVoice()
    engine.name = remote_voice.RemoteChatterboxEngine.name
    return engine


def test_a_pay_per_second_endpoint_is_never_voiced_ahead(monkeypatch):
    ok, why = voice_ahead.always_on(_remote(monkeypatch, "runpod", 0))
    assert not ok and "billed per second" in why


def test_an_always_on_worker_is_voiced_ahead(monkeypatch):
    assert voice_ahead.always_on(_remote(monkeypatch, "runpod", 1))[0]


def test_a_pod_runs_continuously(monkeypatch):
    assert voice_ahead.always_on(_remote(monkeypatch, "http", 0))[0]


def test_the_loop_voices_nothing_without_an_always_on_worker(store, monkeypatch):
    written_ahead(store, "how tides work")
    engine = _remote(monkeypatch, "runpod", 0)

    class Stop(Exception):
        pass

    async def sleep(_s):
        raise Stop

    with pytest.raises(Stop):
        asyncio.run(voice_ahead.run_forever(
            store, lambda _k: None,
            lambda v: PodcastPipeline(engine=engine, cache=store, voice=v),
            lambda: engine, sleep=sleep))
    assert engine.calls == 0
    assert "billed per second" in voice_ahead.report()["reason"]


def test_off_is_off(store, monkeypatch):
    configure(monkeypatch, voice_ahead=False)
    asyncio.run(voice_ahead.run_forever(store, None, None, None))
    assert voice_ahead.report()["reason"].startswith("off")
