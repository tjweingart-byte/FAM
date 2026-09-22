"""Who is typing to whom, for the three dots in a conversation (§127).

**In memory, for a few seconds, and nowhere else.** A typing indicator is a
fact about the last couple of seconds of somebody's keyboard: it is worthless
the moment it is old, and writing it to a database would put a row on disk
for every keystroke burst in the app to record something nobody will ever
read again. So it is a dictionary with a clock, the same bargain
`live_captions` makes for an episode in flight.

The cost of that, stated: on a deployment with more than one worker the
sender's note and the recipient's poll can land on different processes, and
then the dots simply do not show. That is the right way for it to fail - a
missing indicator is a smaller wrong than a message that never arrives, and
nothing else about messaging depends on this.

**It is directed.** "A is typing" is only ever answered to B, the person A is
typing *to*: a global "is typing" flag would tell everybody A talks to that A
is composing a message to somebody.
"""
from __future__ import annotations

import threading
import time

#: How long one note keeps the dots up. A client sends one at most every
#: couple of seconds while keys are going down, so this is long enough to
#: bridge the gaps between notes and short enough that the dots stop soon
#: after somebody stops - or walks away mid-sentence.
TYPING_SECONDS = 5.0

#: A bound on the table, for the same reason `live_captions` has one: a
#: long-running server must not accumulate an entry per pair ever seen.
MAX_PAIRS = 5000

_TYPING: dict = {}
_LOCK = threading.Lock()


def note(sender: str, recipient: str, now: float = 0.0) -> None:
    """`sender` is typing to `recipient`, as of now."""
    if not sender or not recipient or sender == recipient:
        return
    now = now or time.time()
    with _LOCK:
        _TYPING[(sender, recipient)] = now
        if len(_TYPING) > MAX_PAIRS:
            cutoff = now - TYPING_SECONDS
            for pair in [p for p, at in _TYPING.items() if at < cutoff]:
                _TYPING.pop(pair, None)


def clear(sender: str, recipient: str) -> None:
    """They sent the message, so they are not typing it any more."""
    with _LOCK:
        _TYPING.pop((sender, recipient), None)


def is_typing(sender: str, recipient: str, now: float = 0.0) -> bool:
    """Whether `sender` has typed to `recipient` in the last few seconds."""
    if not sender or not recipient:
        return False
    now = now or time.time()
    with _LOCK:
        at = _TYPING.get((sender, recipient))
    return at is not None and now - at <= TYPING_SECONDS


def reset() -> None:
    """Empty the table. Tests only."""
    with _LOCK:
        _TYPING.clear()
