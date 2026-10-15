"""The writer's instructions cached before the writer asks for them (§270).

The writer's ~3,500 identical tokens of house rules and style examples are
marked cacheable (§179, `PROMPT_CACHE`), and the cache lives five minutes. A
writer call more than five minutes after the last one reads them in full
before it starts thinking - at FAM's traffic, most calls. That read sits in
front of the first word.

**Started at the tap, not on a timer.** When a searched episode starts
preparing and this writer's cache has gone cold, a `max_tokens: 0` request
(the API's pre-warm: it reads the prompt, writes the cache and returns
nothing) goes out beside the brief. The brief and the search take seconds;
by the time the writer asks, its instructions are cached. So:

* nothing is sent while nobody is listening ([sweep-on-demand]: ask when
  somebody is looking) - no request at 3am to keep a cache nobody reads;
* the cache write it pays is the one the writer's own call would have paid,
  and the writer then reads at a tenth of the price - so the extra cost is
  one cache read's worth, and it is metered on the episode it was for;
* only the search path: prefetch and the editions have nobody waiting.

It matches the writer's request exactly where the cache is keyed - model,
effort, the same `system` block - and is never awaited. It cannot warm the
brief's calls: `max_tokens: 0` is refused alongside a structured output.
"""
from __future__ import annotations

import asyncio
import logging
import time

from config import settings

log = logging.getLogger(__name__)

#: When each writer `(model, effort)` last read or wrote its cache.
_LAST: dict = {}
#: Outcomes since boot, for /api/health.
_COUNTS: dict = {"sent": 0, "warm": 0, "failed": 0}
_PENDING: set = set()


def fresh_for() -> float:
    """Seconds a cache stays readable after its last use, less a margin."""
    ttl = 3600.0 if str(settings.prompt_cache_ttl or "").strip() == "1h" else 300.0
    return ttl - 30.0


def note_used(model: str, effort: str, at: float | None = None) -> None:
    """A writer call read or wrote this cache just now."""
    _LAST[(model, effort)] = time.monotonic() if at is None else at


def is_cold(model: str, effort: str, now: float | None = None) -> bool:
    last = _LAST.get((model, effort))
    now = time.monotonic() if now is None else now
    return last is None or now - last > fresh_for()


async def _warm(client, model: str, effort: str, notes) -> None:
    import script_generator

    try:
        response = await client.messages.create(
            model=model, max_tokens=0,
            system=script_generator.writer_system(),
            output_config={"effort": effort},
            messages=[{"role": "user", "content": "warm"}],
        )
    except Exception as exc:  # noqa: BLE001 - the writer simply reads it cold
        _COUNTS["failed"] += 1
        _LAST.pop((model, effort), None)
        log.info("writer warm-up for %s failed: %s", model, exc)
        return
    if notes is not None:
        notes.usage.add_model_call(model, getattr(response, "usage", None))


def warm_if_cold(client, notes=None) -> bool:
    """Start the pre-warm for this episode's writer if its cache is cold.

    Returns whether one was started. Never awaited, never raises.
    """
    try:
        if not settings.prompt_cache or not settings.writer_warm:
            return False
        if notes is not None and (getattr(notes, "ahead", False)
                                  or getattr(notes, "prefetch", False)
                                  or getattr(notes, "edition", False)):
            return False
        import script_generator

        model, effort = script_generator.writer_for(notes)
        if not is_cold(model, effort):
            _COUNTS["warm"] += 1
            return False
        # Counted as used from now: two taps a second apart send one warm-up.
        note_used(model, effort)
        _COUNTS["sent"] += 1
        task = asyncio.ensure_future(_warm(client, model, effort, notes))
        _PENDING.add(task)
        task.add_done_callback(_PENDING.discard)
        return True
    except Exception as exc:  # noqa: BLE001
        log.info("writer warm-up not started: %s", exc)
        return False


def report() -> dict:
    return {"enabled": bool(settings.prompt_cache and settings.writer_warm),
            "fresh_for_seconds": fresh_for(), **_COUNTS}
