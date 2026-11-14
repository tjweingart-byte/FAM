"""Episodes being voiced right now, and which of them must be finished (§611).

Since §611 the voice stays only `VOICE_AHEAD_SECONDS` ahead of a listener,
and an episode the listener leaves part-way stops being written and voiced,
as it always did: nothing of it is kept, and nothing more is spent on it.

The exception, at the owner's direction: an episode somebody **saved, shared
or vibed** while it was playing is finished whether or not they stay - the
rest written and voiced in the background, behind every live listener
(`voice_schedule.NO_DEADLINE`), and its whole audio kept like any other, so
the save, the share or the vibe points at something that plays.

An episode saved later, from history, after its stream had already ended
part-way has no script to finish from; nothing here can bring it back.

Matched on the question and the minutes, which is what a save, a share and a
vibe carry. Every live stream of that question is held - two listeners
asking the same thing at the same moment is rare, and holding both costs one
extra finished episode.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(eq=False)
class LiveEpisode:
    query: str
    minutes: float
    held: bool = False


_lock = threading.Lock()
_live: set[LiveEpisode] = set()


def _norm(query: str) -> str:
    return " ".join(str(query or "").casefold().split())


def register(query: str, minutes: float) -> LiveEpisode:
    episode = LiveEpisode(_norm(query), float(minutes or 0))
    with _lock:
        _live.add(episode)
    return episode


def unregister(episode: LiveEpisode) -> None:
    with _lock:
        _live.discard(episode)


def hold(query: str, minutes: float = 0) -> int:
    """Mark every live stream of this question to be finished; how many."""
    wanted, mins = _norm(query), float(minutes or 0)
    if not wanted:
        return 0
    count = 0
    with _lock:
        for episode in _live:
            if episode.query != wanted:
                continue
            if mins and episode.minutes and abs(episode.minutes - mins) > 0.01:
                continue
            episode.held = True
            count += 1
    return count


def live_count() -> int:
    with _lock:
        return len(_live)
