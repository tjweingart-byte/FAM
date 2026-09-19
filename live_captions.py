"""The sentences being spoken right now, while they are being spoken.

`/api/transcript` reads the script cache, and the cache is written **once, at
the end**, when the whole episode is finished. That is the right place for a
replay to read from and the wrong place for a first listen to read from: a
listener who turns captions on during a fresh episode is asking what is being
said *now*, and the answer does not exist under that key until after the last
word has been spoken.

The interface papered over the gap by polling six times over twelve seconds and
then giving up. Twelve seconds is roughly how long a two-minute episode's
script takes to write and nothing like how long a researched ten-minute one
takes, so the panel settled on "no transcript for this one" - a sentence about
attachments - for every long episode anybody read along with. The bug was not
the poll count. Polling a place the answer is not yet in cannot be fixed by
polling it more.

So this is the other half, and it is deliberately tiny:

    speaking a sentence  ->  publish(key, sentence)   <- this module
    /api/transcript      ->  read(key) or the cache

**The sources panel had the same shape of bug and gets the same answer.** An
episode's provenance is known when retrieval returns, which on the research
path is *before the first sentence* - and it was stored only in the cache, so
"where is this coming from?" could not be answered until the episode had
finished. `publish_sources` / `read_sources` are the same two functions for the
same fact under the same key, which is why they live here rather than in a
registry of their own: two tables keyed identically and closed at the same two
moments are one idea maintained twice, and the copy is the one that stops
being closed.

**It never generates, and it cannot.** It holds text that has already been
paid for, on its way to the voice, and the one thing it does is let somebody
read it a few seconds before the cache does. The rule `script_for` is written
under is unchanged: a caption track that could trigger a write would be a
second full Claude call for every episode somebody chose to read along with.

## Why in-process, with no store behind it

A live track describes a generation happening *in this worker*, now. It is
worthless to another process - by the time a second worker could read it the
episode is finished and in the cache, which is the thing to read instead. So
there is no database, no serialisation and nothing to keep in step, and a
deployment that adds a second worker loses nothing: a listener whose caption
poll lands on the wrong worker sees "catching up with the script" until the
cache fills, which is exactly what they saw before this existed.

## What it is bounded by

Tracks are dropped oldest-first past `MAX_TRACKS`, and a finished one is
dropped after `KEEP_SECONDS`. Both are generous, because the thing being held
is characters: a three-minute episode is about 2,700 of them, so the whole
table at its ceiling is well under a megabyte. The bound exists so that a long
running server cannot accumulate one entry per episode ever generated, not
because the memory is close to mattering - the same reasoning `ScriptBuffer`
is sized by.

## The one thing to be careful about

The key is the **cache key**, so a live track and a cached script are the same
episode by construction and a caption panel cannot show one listener's episode
to another. An episode with no key - an attachment, which is deliberately
uncacheable - gets no live track either, which keeps "an attachment episode
has no captions" true rather than making it a special case here.
"""
from __future__ import annotations

import threading
import time
from typing import Iterable, Optional

#: How many episodes' sentences to hold at once. Generous: each is kilobytes.
MAX_TRACKS = 64

#: How long a finished track stays readable after its last sentence. Long
#: enough that a listener who turns captions on near the end of an episode
#: still finds it, short enough that nothing accumulates. The cache has it
#: after this anyway, so expiry costs a listener nothing.
KEEP_SECONDS = 900.0


class _Track:
    __slots__ = ("sentences", "sources", "done", "touched")

    def __init__(self) -> None:
        self.sentences: list[str] = []
        #: Provenance JSON for this episode, as soon as it is known.
        #:
        #: Here rather than in a module of its own because it is the same fact
        #: about the same thing: what this in-flight episode, under this key,
        #: knows about itself before the cache does. Two registries keyed the
        #: same way, opened and closed at the same two moments, would be one
        #: idea maintained twice - and the second copy is the one that stops
        #: being closed.
        #:
        #: It matters most on the retrieval path, where the evidence packet is
        #: built *before the first sentence*. Stored only in the cache, that
        #: was a panel which could not appear until the episode had finished -
        #: forty seconds after the question it answers ("where is this coming
        #: from?") stopped being interesting.
        self.sources: str = ""
        self.done = False
        self.touched = time.time()


#: key -> _Track. Guarded by `_LOCK`: sentences are published from whichever
#: task is speaking and read from a request handler, which on this server are
#: the same event loop but not the same call stack, and a plain dict mutated
#: while another coroutine iterates it is a bug waiting for a slow day.
_TRACKS: dict = {}
_LOCK = threading.Lock()


def open_track(key: str) -> None:
    """Start (or restart) the live track for one episode.

    Restarting matters: the same key is generated again when its cache entry
    expires, and a second generation must not append to the first one's
    sentences - that would show a listener the episode twice, once stale.
    """
    if not key:
        return
    with _LOCK:
        _TRACKS[key] = _Track()
        _evict()


def publish(key: str, sentences: Iterable[str]) -> None:
    """Add sentences that have just been handed to the voice.

    Called from the speaking path, so it must never raise and never block for
    long: an exception here would take down an episode to fix a caption.
    """
    if not key:
        return
    lines = [s for s in (str(x).strip() for x in sentences) if s]
    if not lines:
        return
    with _LOCK:
        track = _TRACKS.get(key)
        if track is None:
            track = _TRACKS[key] = _Track()
            _evict()
        track.sentences.extend(lines)
        track.touched = time.time()


def close(key: str) -> None:
    """Mark an episode finished, so the client can stop asking.

    Without this the interface cannot tell "still being written" from "that is
    the whole thing", and the only honest rendering of the difference is the
    one the panel already draws - so it has to be told.
    """
    if not key:
        return
    with _LOCK:
        track = _TRACKS.get(key)
        if track is not None:
            track.done = True
            track.touched = time.time()


def read(key: str) -> Optional[tuple[list, bool]]:
    """`(sentences, done)` for a live episode, or None if there is no track.

    None rather than `([], False)`: "nothing is being spoken under this key"
    and "an episode is under way and has said nothing yet" are different, and
    the caller falls back to the cache for only one of them.
    """
    if not key:
        return None
    with _LOCK:
        _expire()
        track = _TRACKS.get(key)
        if track is None:
            return None
        return list(track.sentences), track.done


def forget(key: str) -> None:
    """Drop one track. Used by tests and by nothing else."""
    with _LOCK:
        _TRACKS.pop(key, None)


def reset() -> None:
    """Empty the table. Tests only - there is no runtime reason to."""
    with _LOCK:
        _TRACKS.clear()


def _expire(now: float = 0.0) -> None:
    """Drop finished tracks that nobody came back for. Caller holds the lock."""
    now = now or time.time()
    for key in [k for k, t in _TRACKS.items()
                if t.done and now - t.touched > KEEP_SECONDS]:
        _TRACKS.pop(key, None)


def _evict() -> None:
    """Hold the table to `MAX_TRACKS`, oldest touch first. Caller holds the lock."""
    if len(_TRACKS) <= MAX_TRACKS:
        return
    for key, _t in sorted(_TRACKS.items(), key=lambda kv: kv[1].touched)[
            : len(_TRACKS) - MAX_TRACKS]:
        _TRACKS.pop(key, None)


def publish_sources(key: str, raw: str) -> None:
    """Record this episode's provenance while it is still being spoken.

    `raw` is `Provenance.to_json()` - already the shareable half, because that
    is what `to_json` returns and what the cache stores. Passing the object
    would make this module import `provenance`, and it has no business knowing
    what a source is: it holds a string under a key until the cache has one.
    """
    if not key or not raw:
        return
    with _LOCK:
        track = _TRACKS.get(key)
        if track is None:
            track = _TRACKS[key] = _Track()
            _evict()
        track.sources = raw
        track.touched = time.time()


def read_sources(key: str) -> str:
    """Provenance JSON for an episode in flight, or "" if there is none yet.

    "" rather than None: unlike the sentences, the caller's fallback is the
    same either way - read the cache - so there is no distinction to carry.
    """
    if not key:
        return ""
    with _LOCK:
        _expire()
        track = _TRACKS.get(key)
        return track.sources if track is not None else ""
