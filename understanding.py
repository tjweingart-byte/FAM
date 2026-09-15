"""The fork in the pipeline, as a piece of code.

    RESEARCH PACKET / EPISODE INTELLIGENCE
                |
         EPISODE UNDERSTANDING
            /            \\
    EPISODE WRITER    VISUAL DIRECTOR

FAM already works out what an episode is about before it writes it: EI resolves
the subject and decides what to search for, and `research` returns a dated,
graded packet. That work is the most expensive thinking in the whole request,
and until now exactly one thing read it.

This is how a second consumer reads it without paying for it again, and without
being in front of anything.

**Why a bus rather than a return value.** The writer's understanding is
produced deep inside `ScriptGenerator.prepare`, on the task that is streaming
sentences into the listener's ears. Threading it out through the pipeline's
return types would put the illustration in the audio path's call graph, which
is precisely the coupling this feature must not have. Publishing it is
one-directional: the writer announces what it worked out and carries on; if
something is listening, it hears; if nothing is, nothing happens and nothing is
slower.

**Why polling rather than an Event.** A waiter and a publisher have to share an
event loop for an `asyncio.Event` to work, and this project runs tests, a
preview build and a server that each make their own. A 100 ms poll over a wait
measured in seconds costs nothing measurable and cannot be wrong about which
loop it is on.

Entries expire. An understanding is a claim about one request in flight, and a
dictionary that only ever grows is a slow leak with a long fuse.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

#: How long a published understanding stays available to a late listener. Long
#: enough that a slow image provider still picks it up, short enough that this
#: is a mailbox and not a store.
TTL_SECONDS = 180.0
#: How often a waiter looks. See the module docstring.
POLL_SECONDS = 0.1


@dataclass
class Understanding:
    """What the episode is about, as both halves of the fork see it."""

    query: str = ""
    context: str = ""
    minutes: int = 3
    #: `episode_intelligence.Brief`, or None when EI did not run.
    brief: object = None
    #: The research packet's context block, when there was one.
    evidence: str = ""
    published: float = field(default_factory=time.time)


_ENTRIES: dict = {}


def publish(key: str, value: Understanding) -> None:
    """Announce what this episode turned out to be about.

    Never raises and never blocks. The writer calls this on the audio path, so
    the one thing it must never do is have an opinion about whether anybody is
    listening.
    """
    if not key:
        return
    _expire()
    _ENTRIES[key] = value
    log.debug("understanding published for %s (%r)", key[:8], value.query)


def publish_episode(query: str, context: str = "", *, brief=None,
                    evidence: str = "", minutes: int = 3) -> str:
    """Publish under the key the visual side will look for.

    The key comes from `visuals.key_for`, imported here rather than at the top
    of the file so that the writing path does not drag the whole drawing stack
    - numpy, the processor, the provider - into its import graph, and so the
    two modules cannot become a cycle.
    """
    try:
        import visuals

        key = visuals.key_for(query, context)
    except Exception:  # noqa: BLE001 - never break writing over a picture
        log.debug("could not compute a visual key", exc_info=True)
        return ""
    if key:
        publish(key, Understanding(query=query, context=context,
                                   minutes=minutes, brief=brief,
                                   evidence=evidence))
    return key


def peek(key: str):
    _expire()
    return _ENTRIES.get(key)


async def wait(key: str, timeout: float = 8.0):
    """Wait for the understanding, or give up and say so with `None`.

    `None` is an ordinary answer, not an error: a browse-surface warm runs when
    no episode is being written at all, and the director is built to work from
    the query alone. The timeout exists so that a picture is never waiting on
    an episode that died.
    """
    if not key:
        return None
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        found = peek(key)
        if found is not None:
            return found
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(POLL_SECONDS)


def clear(key: str = "") -> None:
    if key:
        _ENTRIES.pop(key, None)
    else:
        _ENTRIES.clear()


def _expire(now: float | None = None) -> None:
    now = now if now is not None else time.time()
    for key, value in list(_ENTRIES.items()):
        if now - value.published > TTL_SECONDS:
            _ENTRIES.pop(key, None)


def report() -> dict:
    _expire()
    return {"waiting": len(_ENTRIES), "ttl_seconds": TTL_SECONDS}
