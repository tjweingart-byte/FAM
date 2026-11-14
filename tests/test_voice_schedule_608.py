"""§608: the shared voice worker serves the nearest silence first, and a
"voice fell behind" row says whether the episode waited for the worker."""
import asyncio
import time

import pytest

import voice_schedule
from voice_schedule import DeadlineGate, VoiceTurn


def _run(coro):
    return asyncio.run(coro)


def test_the_earliest_deadline_goes_next_not_the_first_arrival():
    async def main():
        gate = DeadlineGate(1)
        order = []
        await gate.acquire()  # someone is being voiced

        async def chunk(name, deadline):
            await gate.acquire(deadline)
            order.append(name)
            gate.release()

        relaxed = asyncio.create_task(chunk("thirty seconds in hand", 30.0))
        await asyncio.sleep(0)
        urgent = asyncio.create_task(chunk("about to fall silent", 1.0))
        await asyncio.sleep(0)
        gate.release()
        await asyncio.gather(relaxed, urgent)
        return order

    assert _run(main()) == ["about to fall silent", "thirty seconds in hand"]


def test_a_call_with_no_listener_queues_behind_every_listener():
    async def main():
        gate = DeadlineGate(1)
        order = []
        await gate.acquire()

        async def call(name, turn):
            token = voice_schedule.CURRENT.set(turn)
            try:
                async with gate:
                    order.append(name)
            finally:
                voice_schedule.CURRENT.reset(token)

        tasks = [asyncio.create_task(call("prefetch", None))]
        await asyncio.sleep(0)
        tasks.append(asyncio.create_task(
            call("listener", VoiceTurn(deadline=time.monotonic() + 100))))
        await asyncio.sleep(0)
        gate.release()
        await asyncio.gather(*tasks)
        return order

    assert _run(main()) == ["listener", "prefetch"]


def test_the_wait_is_recorded_on_the_turn():
    async def main():
        gate = DeadlineGate(1)
        await gate.acquire()
        turn = VoiceTurn(deadline=time.monotonic())

        async def call():
            token = voice_schedule.CURRENT.set(turn)
            try:
                async with gate:
                    pass
            finally:
                voice_schedule.CURRENT.reset(token)

        task = asyncio.create_task(call())
        await asyncio.sleep(0.05)
        gate.release()
        await task
        return turn.waited

    assert _run(main()) >= 0.04


def test_a_cancelled_waiter_never_loses_the_slot():
    async def main():
        gate = DeadlineGate(1)
        await gate.acquire()
        gone = asyncio.create_task(gate.acquire(1.0))
        await asyncio.sleep(0)
        gone.cancel()
        with pytest.raises(asyncio.CancelledError):
            await gone
        gate.release()
        # The slot is free again: a new caller gets it at once.
        assert await asyncio.wait_for(gate.acquire(5.0), 0.5) == 0.0
        gate.release()

        # Handed the turn, then cancelled before running: passed on.
        await gate.acquire()
        handed = asyncio.create_task(gate.acquire(1.0))
        await asyncio.sleep(0)
        gate.release()
        handed.cancel()
        with pytest.raises(asyncio.CancelledError):
            await handed
        assert await asyncio.wait_for(gate.acquire(5.0), 0.5) == 0.0

    _run(main())


def test_the_size_is_honoured():
    async def main():
        gate = DeadlineGate(2)
        assert await gate.acquire() == 0.0
        assert await gate.acquire() == 0.0
        third = asyncio.create_task(gate.acquire())
        await asyncio.sleep(0.01)
        assert not third.done()
        gate.release()
        await asyncio.wait_for(third, 0.5)

    _run(main())


def test_the_remote_voice_gate_is_deadline_ordered():
    import remote_voice

    remote_voice.RemoteChatterboxEngine._gate = None
    try:
        gate = remote_voice.RemoteChatterboxEngine._semaphore(1)
        assert isinstance(gate, DeadlineGate)
    finally:
        remote_voice.RemoteChatterboxEngine._gate = None


def test_a_row_says_how_long_the_episode_waited_for_the_worker(monkeypatch):
    import pipeline
    import pipeline_errors

    rows = []
    monkeypatch.setattr(pipeline_errors, "record",
                        lambda *a, **k: rows.append(a))
    stats = pipeline.GenerationStats(query="q")
    stats.first_audio_at = time.perf_counter() - stats.started_at - 20.0
    stats.synth_seconds, stats.voice_wait = 12.0, 7.5
    stats.note_headroom(10.0)
    assert "7.5s of 12.0s voicing was waiting for the worker" in rows[0][2]

    rows.clear()
    stats = pipeline.GenerationStats(query="q")
    stats.first_audio_at = time.perf_counter() - stats.started_at - 20.0
    stats.synth_seconds = 12.0
    stats.note_headroom(9.0)
    assert "0.8x realtime, never waiting" in rows[0][2]
