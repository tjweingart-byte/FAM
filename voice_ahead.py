"""Voice ahead: an always-on worker that has nobody to voice voices the
episodes the editions already wrote (§610, at the owner's direction).

Why
---
myFAM's rails, Trending, Start here and DailyFAM's mixes are written whole
before anybody taps (§139, §143, §359); until now a tap still voiced the
script on the GPU while the listener waited, and only then kept the audio
(§132). Since §578 the production endpoint keeps an always-on worker, paid
for by the hour whether it voices anything or not. Voicing those scripts in
its idle time costs nothing extra, and turns a tap into a replay of kept
audio: no wait for the first chunk, no "voice fell behind", no GPU call.

This narrows `decouple-script-audio`'s "prefetch warms text, never audio"
exactly where the reason for it - a wrong guess should cost only text - no
longer holds: with an always-on worker an idle second is already paid for.
On a pay-per-second endpoint it would be a bill, so nothing runs there
(`always_on`).

A listener always comes first
-----------------------------
Every chunk this voices waits until no listener's episode is in progress,
and for `VOICE_AHEAD_QUIET_SECONDS` after the last one ended
(`voice_schedule.quiet`), then queues behind any listener's chunk at the
worker (`NO_DEADLINE`). A search starts its brief and research seconds
before its first chunk reaches the worker, so the most it can meet is one
background chunk already in flight, finishing during that research. An
episode interrupted that way is resumed, not restarted.

What it voices
--------------
Current scripts the editions wrote (`VOICE_AHEAD_ORIGINS`: the cache row's
origin), newest first, with no audio kept in the voice a tap would play.
The voice is the one a tap would draw (`voice_for`, given by the app: the
voice kept beside the script, else one drawn from the bank and kept, as a
tap does), so the tap finds this audio. One episode at a time.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

from config import settings

log = logging.getLogger("fam.voice_ahead")

#: How long to sleep when nothing is left to voice, or nothing may be.
IDLE_SECONDS = 60.0
#: After a failure, so a worker that is down is not asked once a second.
FAILURE_SECONDS = 120.0
#: An episode that was not kept (the voice did not confirm itself, the
#: bucket refused it, the worker failed) is not tried again for this long:
#: the next pass moves on to the next one rather than retrying it at once.
RETRY_SECONDS = 3600.0

#: key -> when it was last tried without being kept.
_TRIED: dict = {}

_STATE: dict = {
    "running": False,
    "reason": "",
    "voiced": 0,
    "failed": 0,
    "skipped": 0,
    "last": None,
    "last_error": "",
    "current": "",
    "worker": "",
}


def origins() -> tuple:
    return tuple(o.strip() for o in (settings.voice_ahead_origins or "").split(",")
                 if o.strip())


def always_on(engine) -> tuple[bool, str]:
    """Whether `engine`'s worker is paid for whether it voices or not, and why.

    An in-process card is always on. A remote voice is on RunPod either as a
    pay-per-second endpoint (never: the same work would be a bill), an
    endpoint with active workers (§578: yes), or a pod over HTTP, which runs
    continuously (§118: yes).
    """
    if engine is None or not getattr(engine, "keeps_audio", False):
        return False, "no production voice keeps audio here"
    name = getattr(engine, "name", "")
    import remote_voice

    if name == getattr(remote_voice.RemoteChatterboxEngine, "name", "remote"):
        transport = (settings.remote_voice_transport or "").strip().lower()
        if transport == "runpod":
            if remote_voice.active_workers() > 0:
                return True, "the RunPod endpoint keeps an always-on worker"
            return False, ("the RunPod endpoint has no always-on worker "
                           "(REMOTE_VOICE_ACTIVE_WORKERS=0): voicing ahead "
                           "would be billed per second")
        return True, "a pod, which runs continuously"
    return True, "an in-process card"


# ---- only the always-on worker, confirmed by RunPod (§610) ----------------
#
# `REMOTE_VOICE_ACTIVE_WORKERS` is this app's belief about the console, and
# a belief is not a guard: if the console's Active workers were 0 while the
# setting said 1, every chunk voiced ahead would be billed by the second, or
# would keep a flex worker alive past its idle timeout. So before every
# background chunk RunPod itself is asked, and anything it cannot confirm is
# a no: the endpoint keeps an always-on worker (`workersMin`), nothing is
# queued or in progress there, and no worker is alive beyond the always-on
# ones - so the job can only land on an always-on worker that is idle. Both
# reads are free (no job, no worker woken).

#: How long RunPod's answer about the endpoint's settings is believed.
ENDPOINT_SECONDS = 60.0
#: How often a refused check is asked again while an episode waits.
CHECK_SECONDS = 15.0

_ENDPOINT: dict = {"at": 0.0, "min": None, "why": ""}


async def _ask(method: str, url: str, key: str, json: Optional[dict] = None):
    """One free read from RunPod, or None (logged)."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=settings.voice_probe_timeout) as client:
            response = await client.request(
                method, url, json=json,
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"})
        if response.status_code >= 400:
            log.info("voice ahead: %s %s returned HTTP %s", method, url,
                     response.status_code)
            return None
        return response.json()
    except Exception as exc:  # noqa: BLE001 - None is a refusal
        log.info("voice ahead: %s %s failed (%s)", method, url, type(exc).__name__)
        return None


def _workers_min(body, endpoint_id: str):
    """`workersMin` for this endpoint out of whichever shape RunPod sent."""
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        found = ((body["data"].get("myself") or {}).get("endpoints") or [])
        body = next((e for e in found if isinstance(e, dict)
                     and e.get("id") == endpoint_id), None)
    if isinstance(body, dict) and isinstance(body.get("endpoint"), dict):
        body = body["endpoint"]
    if not isinstance(body, dict):
        return None
    value = body.get("workersMin")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def endpoint_workers_min(config) -> tuple:
    """The endpoint's always-on workers as RunPod states them, or None."""
    now = time.monotonic()
    if _ENDPOINT["min"] is not None and now - _ENDPOINT["at"] < ENDPOINT_SECONDS:
        return _ENDPOINT["min"], _ENDPOINT["why"]
    eid = (settings.runpod_endpoint_id or "").strip()
    asks = (
        ("GET", f"{settings.runpod_api_url.rstrip('/')}/endpoints/{eid}", None),
        ("GET", f"{settings.runpod_rest_url.rstrip('/')}/endpoints/{eid}", None),
        ("POST", settings.runpod_graphql_url,
         {"query": "query { myself { endpoints { id workersMin } } }"}),
    )
    for method, url, body in asks:
        found = _workers_min(await _ask(method, url, config.api_key, body), eid)
        if found is not None:
            _ENDPOINT.update(at=now, min=found, why=f"RunPod says workersMin={found}")
            return found, _ENDPOINT["why"]
    _ENDPOINT.update(at=0.0, min=None, why="")
    return None, "RunPod did not say whether the endpoint keeps an always-on worker"


async def active_worker_free(engine) -> tuple:
    """Whether a chunk sent now can only be voiced by an idle always-on
    worker, so it costs nothing extra; and why. Fails closed."""
    ok, why = always_on(engine)
    if not ok:
        return False, why
    import remote_voice

    if (getattr(engine, "name", "") != remote_voice.RemoteChatterboxEngine.name
            or (settings.remote_voice_transport or "").strip().lower() != "runpod"):
        return True, why  # a pod or a card: billed by the hour regardless
    config = remote_voice.RemoteConfig.from_settings()
    if not config.url or not config.api_key:
        return False, "no RunPod endpoint or key to confirm the worker with"
    active, why = await endpoint_workers_min(config)
    if not active:
        return False, (why if active is None else
                       "RunPod says the endpoint keeps no always-on worker "
                       f"(workersMin=0) although REMOTE_VOICE_ACTIVE_WORKERS="
                       f"{remote_voice.active_workers()}: voicing ahead would "
                       "be billed")
    url = f"{config.url}/health"
    health = await _ask("GET", url, config.api_key)
    if not isinstance(health, dict):
        return False, "RunPod's health for the endpoint could not be read"
    jobs, workers = health.get("jobs") or {}, health.get("workers") or {}
    try:
        queued = int(jobs.get("inQueue", 0)) + int(jobs.get("inProgress", 0))
        busy = int(workers.get("running", 0)) + int(workers.get("initializing", 0))
        idle = int(workers["idle"])
    except (KeyError, TypeError, ValueError):
        return False, "RunPod's health for the endpoint did not say what is idle"
    if queued or busy:
        return False, "the always-on worker is busy"
    if idle < 1:
        return False, "no idle worker"
    if idle > active:
        return False, (f"{idle} workers are alive and only {active} are always-on: "
                       "a flex worker could take the job")
    return True, f"an always-on worker is idle ({why})"


async def wait_for_the_active_worker(engine) -> None:
    """Until no listener is in progress and RunPod confirms an idle
    always-on worker. Called before every chunk voiced ahead."""
    import voice_schedule

    quiet = settings.voice_ahead_quiet_seconds
    while True:
        await voice_schedule.wait_for_quiet(quiet)
        ok, why = await active_worker_free(engine)
        _STATE["worker"] = why
        if ok and voice_schedule.quiet(quiet):
            return
        await asyncio.sleep(CHECK_SECONDS)


def candidates(cache, limit: Optional[int] = None) -> list:
    """Written-ahead episodes still current, newest first, as cache rows.
    Local reads only."""
    limit = int(limit or settings.voice_ahead_scan)
    rows: list = []
    for origin in origins():
        try:
            rows.extend(r for r in cache.recent(limit, origin=origin)
                        if r.get("current"))
        except Exception:  # noqa: BLE001 - a list, never a reason to stop
            log.exception("voice ahead: could not list %s episodes", origin)
    rows.sort(key=lambda r: -float(r.get("created") or 0.0))
    return rows[:limit]


async def run_once(cache, voice_for: Callable[[str], Optional[str]],
                   make_pipeline: Callable[[Optional[str]], object]) -> str:
    """Voice the newest written-ahead episode with no audio a tap would play.

    Returns the outcome ("voiced", "nothing", ...). `voice_for(key)` is the
    voice a tap would play, `make_pipeline(voice)` the pipeline it would play
    it on - both the app's own, so the audio row is the one a tap reads.
    """
    now = time.time()
    for old in [k for k, at in _TRIED.items() if now - at >= RETRY_SECONDS]:
        _TRIED.pop(old, None)
    for row in candidates(cache):
        key = row["key"]
        if key in _TRIED:
            continue
        voice = voice_for(key)
        pipeline = make_pipeline(voice)
        if not pipeline._keeps_audio():
            return "off"
        if cache.has_audio(key, pipeline._audio_voice(), pipeline.engine.sample_rate):
            continue
        _STATE["current"] = row.get("title") or row.get("query") or key
        started = time.monotonic()
        try:
            outcome = await pipeline.voice_ahead(
                key, row["query"], row["minutes"],
                wait=lambda p=pipeline: wait_for_the_active_worker(p.engine))
        except Exception:
            _TRIED[key] = time.time()
            raise
        finally:
            _STATE["current"] = ""
        if outcome not in ("voiced", "changed"):
            _TRIED[key] = time.time()
        _STATE["last"] = {"key": key[:12], "title": row.get("title") or "",
                          "origin": row.get("origin") or "", "voice": voice or "",
                          "outcome": outcome, "at": time.time(),
                          "seconds": round(time.monotonic() - started, 1)}
        if outcome == "voiced":
            _STATE["voiced"] += 1
        else:
            _STATE["skipped"] += 1
        log.info("voice ahead: %s %r (%s)", outcome,
                 (row.get("title") or row["query"])[:60], row.get("origin"))
        return outcome
    return "nothing"


async def run_forever(cache, voice_for, make_pipeline, engine_for,
                      sleep: Callable[[float], Awaitable] = asyncio.sleep) -> None:
    """Voice ahead for the server's life. `engine_for()` is the production
    engine, asked each pass so a worker that comes or goes is followed."""
    if not settings.voice_ahead:
        _STATE["reason"] = "off (VOICE_AHEAD=0)"
        log.info("voice ahead: %s", _STATE["reason"])
        return
    _STATE["running"] = True
    while True:
        wait = IDLE_SECONDS
        try:
            engine = engine_for()
            ok, why = always_on(engine)
            _STATE["reason"] = why
            if ok:
                await wait_for_the_active_worker(engine)
                outcome = await run_once(cache, voice_for, make_pipeline)
                if outcome not in ("nothing", "off"):
                    wait = 0.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the next pass tries again
            _STATE["failed"] += 1
            _STATE["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
            log.warning("voice ahead: %s", _STATE["last_error"])
            wait = FAILURE_SECONDS
        await sleep(wait)


def report() -> dict:
    """For /api/health."""
    return {"enabled": bool(settings.voice_ahead), "origins": list(origins()),
            "quiet_seconds": settings.voice_ahead_quiet_seconds, **_STATE}


def reset() -> None:
    """For tests."""
    _TRIED.clear()
    _STATE.update(running=False, reason="", voiced=0, failed=0, skipped=0,
                  last=None, last_error="", current="",
                  worker="")
    _ENDPOINT.update(at=0.0, min=None, why="")
