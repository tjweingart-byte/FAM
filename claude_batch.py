"""Claude's Message Batches API, for writing nobody is waiting on (§179).

The Batches API bills every token at half price, cache reads and writes
included, in exchange for answering asynchronously - most batches within the
hour, none later than a day. That trade is right for the two surfaces that
write before anybody taps: the Trending edition and the DailyFAM edition. It is
wrong for anything a listener is waiting on, and nothing on a tap's path calls
this module.

**A saving must not subtract availability** (the rule EI keeps). So `run`
never raises and never loses a request: a batch that cannot be created, that
errors, or that has not ended by `wait_seconds` gives back `None` for every
request it did not answer, and the caller writes those the ordinary way. What
that can cost is paid once and said in the log - a batch cancelled at the
deadline may still have finished some requests, and those are billed at the
batch rate even though the caller wrote them again.

One batch per edition, not one per episode: the deadline is a property of the
edition, and ten batches of one would wait ten times in a row.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional, Union

log = logging.getLogger("claude_batch")

#: Custom ids the API accepts: 1-64 of letters, digits, `_` and `-`.
_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")

#: How long `run` keeps asking after a cancel, for the requests that finished
#: before it. A cancel is not instant: the batch goes `canceling`, then `ended`.
CANCEL_GRACE_SECONDS = 60.0

Alive = Optional[Callable[[], Union[None, Awaitable[None]]]]


def valid_id(custom_id: str) -> bool:
    return 0 < len(custom_id) <= 64 and set(custom_id) <= _ID_CHARS


async def run(client, requests: dict, *, wait_seconds: float,
              poll_seconds: float = 30.0, alive: Alive = None,
              sleep=asyncio.sleep, clock=time.monotonic) -> dict:
    """Send `requests` (custom id -> Messages params) as one batch.

    Returns custom id -> the `Message` it produced, or `None` for every
    request the batch did not answer successfully. Never raises.

    `alive` is called on every poll, so an edition holding a claim while it
    waits keeps it (`STALE_CLAIM_SECONDS`). `sleep` and `clock` are for tests.
    """
    answers: dict = {custom_id: None for custom_id in requests}
    if not requests:
        return answers
    bad = [custom_id for custom_id in requests if not valid_id(custom_id)]
    if bad:
        log.error("claude batch: not sent - invalid custom id(s) %s", bad[:3])
        return answers
    try:
        batch = await client.messages.batches.create(requests=[
            {"custom_id": custom_id, "params": params}
            for custom_id, params in requests.items()])
    except Exception as exc:  # noqa: BLE001 - the caller writes them live
        log.warning("claude batch: could not create a batch of %d (%s: %s); "
                    "they will be written live", len(requests),
                    type(exc).__name__, exc)
        return answers

    batch_id = getattr(batch, "id", "")
    log.info("claude batch %s: %d request(s) sent", batch_id, len(requests))
    deadline = clock() + max(0.0, float(wait_seconds))
    status = getattr(batch, "processing_status", "")
    cancelled = False
    while status != "ended":
        if clock() >= deadline and not cancelled:
            cancelled = True
            log.warning("claude batch %s: not ended after %.0fs; cancelling, "
                        "and whatever it has not answered is written live",
                        batch_id, wait_seconds)
            try:
                await client.messages.batches.cancel(batch_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("claude batch %s: cancel failed (%s)", batch_id, exc)
                return answers
            deadline = clock() + CANCEL_GRACE_SECONDS
        elif cancelled and clock() >= deadline:
            log.warning("claude batch %s: still not ended after cancelling; "
                        "giving up on its answers", batch_id)
            return answers
        await _call(alive)
        await sleep(max(1.0, float(poll_seconds)))
        try:
            batch = await client.messages.batches.retrieve(batch_id)
            status = getattr(batch, "processing_status", "")
        except Exception as exc:  # noqa: BLE001 - a failed poll is not an answer
            log.warning("claude batch %s: poll failed (%s: %s)", batch_id,
                        type(exc).__name__, exc)

    try:
        results = await client.messages.batches.results(batch_id)
        async for entry in results:
            custom_id = getattr(entry, "custom_id", "")
            result = getattr(entry, "result", None)
            if custom_id not in answers or result is None:
                continue
            kind = getattr(result, "type", "")
            if kind == "succeeded":
                answers[custom_id] = getattr(result, "message", None)
            else:
                log.warning("claude batch %s: %s %s", batch_id, custom_id,
                            _why(result))
    except Exception as exc:  # noqa: BLE001
        log.warning("claude batch %s: could not read results (%s: %s)",
                    batch_id, type(exc).__name__, exc)
    answered = sum(1 for message in answers.values() if message is not None)
    log.info("claude batch %s: %d of %d answered", batch_id, answered,
             len(requests))
    return answers


def _why(result) -> str:
    kind = getattr(result, "type", "") or "unknown"
    error = getattr(result, "error", None)
    inner = getattr(error, "error", error)
    detail = getattr(inner, "message", "") or getattr(inner, "type", "")
    return f"{kind}: {detail}" if detail else kind


async def _call(alive: Alive) -> None:
    if alive is None:
        return
    try:
        outcome = alive()
        if asyncio.iscoroutine(outcome):
            await outcome
    except Exception as exc:  # noqa: BLE001 - a heartbeat never stops a batch
        log.warning("claude batch: heartbeat failed (%s)", exc)
