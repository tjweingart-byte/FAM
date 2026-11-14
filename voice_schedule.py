"""Who the voice serves next, when listeners share it (§608).

Since §578 the production endpoint keeps one always-on worker, and at most
`REMOTE_VOICE_ACTIVE_WORKERS` chunks are in flight across the whole server.
Listeners voiced at the same moment therefore take turns on one card. A
plain semaphore gives the turn to whoever asked first, so an episode with
thirty seconds of audio in hand would go ahead of one about to fall silent -
and /admin filled with "the voice fell behind".

`DeadlineGate` gives the turn to the chunk whose listener runs out of audio
soonest. It adds no capacity: it spends the capacity there is where silence
is nearest. A slow answer is a scheduling choice.

`VoiceTurn` is how the pipeline says when its listener runs out, and how it
learns how long the chunk waited for its turn - so a row can say "waited for
the worker" apart from "the worker was slow". Passed through a context
variable so no engine's `synth` signature changes; set and reset around one
`synth` call, never across a `yield`.
"""
from __future__ import annotations

import asyncio
import contextvars
import heapq
import itertools
import time
from dataclasses import dataclass


@dataclass
class VoiceTurn:
    """One synthesis call's place in the queue.

    `deadline` is `time.monotonic()` at which this chunk's listener runs out
    of audio (now, for an episode's first chunk: someone is waiting for the
    first word). `waited` is filled in by the gate."""

    deadline: float
    waited: float = 0.0


#: The turn of the `synth` call running in this context, or None (prefetch,
#: /admin's round trip, tools): those queue last, behind every listener.
CURRENT: contextvars.ContextVar[VoiceTurn | None] = contextvars.ContextVar(
    "voice_turn", default=None)

#: Where a call with no deadline queues: after any listener.
NO_DEADLINE = float("inf")


class DeadlineGate:
    """A semaphore that wakes the earliest deadline, not the first arrival.

    Same contract as `asyncio.Semaphore` for `async with`: `size` holders at
    once. A waiter cancelled before its turn is skipped; one cancelled after
    being handed the turn passes it on, so a slot is never lost.
    """

    def __init__(self, size: int) -> None:
        self.size = max(1, int(size))
        self._busy = 0
        self._waiters: list[tuple[float, int, asyncio.Future]] = []
        self._order = itertools.count()

    @property
    def waiting(self) -> int:
        return sum(1 for _, _, f in self._waiters if not f.done())

    async def acquire(self, deadline: float = NO_DEADLINE) -> float:
        """Wait for a turn; returns the seconds spent waiting."""
        if self._busy < self.size and not self.waiting:
            self._busy += 1
            return 0.0
        started = time.monotonic()
        future = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiters, (deadline, next(self._order), future))
        try:
            await future
        except asyncio.CancelledError:
            if future.done() and not future.cancelled():
                # Handed the turn, then cancelled before using it.
                self.release()
            else:
                future.cancel()
            raise
        return time.monotonic() - started

    def release(self) -> None:
        while self._waiters:
            _, _, future = heapq.heappop(self._waiters)
            if not future.done():
                future.set_result(None)  # the slot passes straight on
                return
        self._busy = max(0, self._busy - 1)

    async def __aenter__(self) -> "DeadlineGate":
        turn = CURRENT.get()
        waited = await self.acquire(turn.deadline if turn else NO_DEADLINE)
        if turn is not None:
            turn.waited += waited
        return self

    async def __aexit__(self, *exc) -> None:
        self.release()
