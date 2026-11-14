"""Who the voice serves next, when listeners share it (§609).

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
    #: Voiced ahead of any tap (§610), not for a listener.
    ahead: bool = False
    #: A listener's first chunk: someone is waiting for the first word.
    first: bool = False


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
        #: Slots held by chunks voiced ahead (§610) right now.
        self.ahead_holding = 0

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
        behind_ahead = self.ahead_holding > 0
        waited = await self.acquire(turn.deadline if turn else NO_DEADLINE)
        if turn is not None:
            turn.waited += waited
            if turn.ahead:
                self.ahead_holding += 1
            elif behind_ahead and waited >= 0.05 and turn.deadline != NO_DEADLINE:
                note_listener_wait(waited, turn.first)
        return self

    async def __aexit__(self, *exc) -> None:
        turn = CURRENT.get()
        if turn is not None and turn.ahead:
            self.ahead_holding = max(0, self.ahead_holding - 1)
        self.release()


# ---- voice ahead (§610) ---------------------------------------------------
#
# An always-on worker with nobody to voice is paid for and idle. `voice_ahead`
# spends that time voicing the episodes the editions already wrote. A
# listener always comes first, and "first" means more than the gate's order:
# a search spends seconds on its brief and research before its first chunk
# reaches the gate, and a background chunk taken in that gap would be in
# front of it. So background work starts a chunk only while no listener's
# episode is in progress at all, and not for `quiet` seconds after one ends.

#: Listeners' episodes in progress: token -> when it began. A dict rather
#: than a count, so an episode whose stream was dropped without being closed
#: cannot hold the worker from background work forever: one older than
#: `LISTENER_HORIZON` is forgotten.
_LISTENERS: dict = {}
_TOKENS = itertools.count(1)
_LAST_LISTENER = 0.0
#: Longer than any episode is voiced for (ten minutes at most, written at
#: well over realtime).
LISTENER_HORIZON = 1800.0


def listener_started() -> int:
    """A listener's episode began (it may be voiced). Cheap: request path.
    Returns the token `listener_finished` takes."""
    global _LAST_LISTENER
    token = next(_TOKENS)
    _LAST_LISTENER = time.monotonic()
    _LISTENERS[token] = _LAST_LISTENER
    return token


def listener_finished(token: int) -> None:
    """That episode needs the voice no longer (ended, or plays kept audio)."""
    global _LAST_LISTENER
    if _LISTENERS.pop(token, None) is not None:
        _LAST_LISTENER = time.monotonic()


def listeners(now: float | None = None) -> int:
    now = time.monotonic() if now is None else now
    for token, began in list(_LISTENERS.items()):
        if now - began > LISTENER_HORIZON:
            _LISTENERS.pop(token, None)
    return len(_LISTENERS)


def quiet(seconds: float, now: float | None = None) -> bool:
    """No listener's episode in progress, none for `seconds`."""
    now = time.monotonic() if now is None else now
    return listeners(now) == 0 and now - _LAST_LISTENER >= seconds


async def wait_for_quiet(seconds: float, poll: float = 0.5) -> float:
    """Wait until `quiet(seconds)`; returns how long that took."""
    started = time.monotonic()
    while not quiet(seconds):
        await asyncio.sleep(poll)
    return time.monotonic() - started


#: Listeners' chunks that waited for the worker while a chunk voiced ahead
#: held it (§610): what voicing ahead cost listeners. `first` counts the
#: waits in front of an episode's first word, the ones a search feels.
_WAITS: dict = {}


def _no_waits() -> dict:
    return {"chunks": 0, "seconds": 0.0, "longest": 0.0,
            "first_chunks": 0, "first_seconds": 0.0, "first_longest": 0.0,
            "last_at": None}


_WAITS.update(_no_waits())


def note_listener_wait(seconds: float, first: bool) -> None:
    _WAITS["chunks"] += 1
    _WAITS["seconds"] = round(_WAITS["seconds"] + seconds, 2)
    _WAITS["longest"] = round(max(_WAITS["longest"], seconds), 2)
    if first:
        _WAITS["first_chunks"] += 1
        _WAITS["first_seconds"] = round(_WAITS["first_seconds"] + seconds, 2)
        _WAITS["first_longest"] = round(max(_WAITS["first_longest"], seconds), 2)
    _WAITS["last_at"] = time.time()


def listener_waits() -> dict:
    """For /api/health: since boot."""
    return dict(_WAITS)


def reset() -> None:
    """For tests."""
    global _LAST_LISTENER
    _LISTENERS.clear()
    _LAST_LISTENER = 0.0
    _WAITS.update(_no_waits())
