"""§607: every brief request that was sent is counted, the hedged loser too.

§603's second call left the slower one cancelled and its usage unread, so a
hedged brief's spend under-counted by a whole Haiku call. Now each request
folds its own answer where it answers; the one not taken is left to answer
into the same notes, and one cancelled before answering is recorded."""
from __future__ import annotations

import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import episode_intelligence as ei  # noqa: E402
import metering  # noqa: E402
from tests.test_ei_split_268 import _reply, _with, model  # noqa: E402,F401


def _usage(n):
    return types.SimpleNamespace(input_tokens=n, output_tokens=n,
                                 cache_read_input_tokens=0,
                                 cache_creation_input_tokens=0)


def _notes():
    return types.SimpleNamespace(usage=metering.Usage())


def _client(delays):
    """A stub whose nth request waits `delays[n]` and reports n+1 tokens."""
    sent = []

    class _Messages:
        async def create(self, **kwargs):
            n = len(sent)
            sent.append(n)
            await asyncio.sleep(delays[n])
            reply = _reply({"search_query": "q"})
            reply.usage = _usage(100 * (n + 1))
            return reply

    return types.SimpleNamespace(messages=_Messages()), sent


def test_the_hedged_loser_is_counted_in_the_same_notes(monkeypatch):
    _with(monkeypatch, ei_hedge_seconds=0.05, ei_frame_apart=False)
    client, sent = _client([0.3, 0.01])
    notes = _notes()

    async def run():
        kind, _, _ = await ei._ask(client, "search", "q", 2, "", "now", (), 0,
                                   notes, path="search")
        assert kind == "ok"
        # The winner (the second request) is counted as it is taken.
        assert notes.usage.model_calls == 1 and notes.usage.input_tokens == 200
        await asyncio.sleep(0.4)        # the loser answers in its own time

    asyncio.run(run())
    assert len(sent) == 2
    assert notes.usage.model_calls == 2, "the request not taken was billed too"
    assert notes.usage.input_tokens == 300


def test_one_call_is_counted_once(monkeypatch):
    _with(monkeypatch, ei_hedge_seconds=0.5)
    client, sent = _client([0.01])
    notes = _notes()
    asyncio.run(ei._ask(client, "search", "q", 2, "", "now", (), 0, notes,
                        path="search"))
    assert len(sent) == 1 and notes.usage.model_calls == 1


def test_a_request_cancelled_before_answering_is_recorded():
    ei._UNANSWERED.clear()

    async def make():
        await asyncio.sleep(5)

    async def run():
        task = asyncio.ensure_future(ei._hedged(make, 0.05, "understand"))
        await asyncio.sleep(0.1)        # both requests sent
        task.cancel()                   # the listener left
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0)

    asyncio.run(run())
    assert ei._UNANSWERED["understand"] == {"cancelled": 2}
    assert "unanswered" in ei.report()
