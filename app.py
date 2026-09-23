"""Web app: search box in, live audio out.

Endpoints
    GET  /                 the interface
    GET  /api/health       engine and configuration report
    POST /api/script       script only (JSON), for previewing or debugging
    GET  /api/audio        the podcast, streamed as live PCM or WAV

/api/audio is a GET on purpose so it can be used directly as an <audio> src.
"""
from __future__ import annotations

import asyncio
import hmac
import os
import json
import logging
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from urllib.parse import quote
from collections import defaultdict, deque
from typing import Optional, Union

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi import Response
from fastapi.responses import (
    HTMLResponse, JSONResponse, StreamingResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from anthropic_client import build_async_client, describe_http_version, http2_enabled
from cache import (MemoryScriptCache, SqliteScriptCache, build_cache, cache_key,
                   is_shareable, research_words)
import embeddings
import learned_rank
import taste_vectors
from demo_script import DemoGenerator
import credentials
import entitlements
import messages as messages_mod
import typing_indicator as typing_mod
import metering
import oauth
import quotas
import saved as saved_mod
import sharing
from config import (DEFAULT_MINUTES, DEFAULT_PIPELINE, describe_key,
                    key_source, settings)
import prefetch
import prefetch_sources
from episode_intelligence import report as ei_report
import live_sources
from gdelt import report as gdelt_report
import provenance as provenance_mod
import stories as stories_mod
import trending as trending_mod
from live_facts import report as live_facts_report
from research import NoEvidence, ResearchUnavailable, report as research_report
from pipeline import GenerationStats, NotCached, PodcastPipeline
from script_generator import ScriptGenerator, ScriptNotes, plan_episode
import attachments as attachments_mod
import categories as categories_mod
import topics as topics_mod
import accounts as accounts_mod
from paths import PROJECT_ROOT
import mixes as mixes_mod
import preferences as prefs_mod
import social as social_mod
import voice_store
from tts import (
    TTSUnavailable,
    build_engine,
    default_voice,
    engine_for_voice,
    engine_report,
    list_voices,
    warm_up,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("podcast")

# Prepare the shared voice store before anything asks it what it holds. On the
# first run of a new version this adopts voices an older project folder already
# downloaded; every run after, it is a no-op.
VOICE_STORE = voice_store.ensure_ready()
if VOICE_STORE["adopted"]:
    log.info(
        "reused %d voice(s) from a previous version of the app: %s",
        len(VOICE_STORE["adopted"]), ", ".join(VOICE_STORE["adopted"]),
    )
log.info("voices: %s", voice_store.describe())

#: Filled in at startup by _verify_credentials. "unchecked" until then.
CREDENTIALS = {"state": "unchecked", "detail": "", "key": "", "source": "", "secrets": {}}


async def _verify_credentials() -> None:
    """Ask Claude whether the key works, before anyone presses play.

    Every credential failure this project has had was discovered by a listener,
    mid-episode, as a 502 - because the app validated its *configuration* (is a
    key set?) and never the credential (does it work?). A key that is missing,
    expired, revoked, truncated on paste, or simply the wrong string all look
    identical until the first request, and by then someone is waiting for audio.

    `models.retrieve` is the cheapest possible question: it bills nothing, and
    it answers both "is this key accepted" and "can this account use this
    model" - which are the two ways this has actually failed.

    A rejection now has two things to try before it is reported, and the order
    matters. **Re-read the provider first**: the commonest reason a key that
    worked yesterday is refused today is that it was rotated, and the new one
    is already sitting in the secrets manager. **Then fail over**, if a pool was
    configured. Only when both are spent is this a rejection - which is what it
    always was, said at the same place, in the same words.
    """
    credentials.prime()
    CREDENTIALS["key"] = describe_key()
    CREDENTIALS["source"] = key_source()
    CREDENTIALS["secrets"] = credentials.report()
    if DEMO_MODE:
        CREDENTIALS.update(state="absent", detail="No API key: the canned sample script is standing in.")
        log.warning("NO API KEY - every episode will be the built-in sample script, "
                    "which does not answer what was asked.")
        _say_where_a_key_could_come_from()
        return
    rotated = False
    while True:
        try:
            client = build_async_client()
            await client.models.retrieve(settings.model)
        except Exception as exc:  # noqa: BLE001 - the report matters, not the type
            detail = friendly_error(exc)
            if not rotated and credentials.refresh("the key in force was rejected"):
                # The provider answered. Start again at the top of the pool: the
                # keys behind the rejected one may have been rotated as well.
                rotated = True
                credentials.reset("ANTHROPIC_API_KEY")
                CREDENTIALS["key"] = describe_key()
                CREDENTIALS["source"] = key_source()
                continue
            if credentials.demote("ANTHROPIC_API_KEY", detail):
                CREDENTIALS["key"] = describe_key()
                continue
            CREDENTIALS.update(state="rejected", detail=detail,
                               secrets=credentials.report())
            log.error("CREDENTIALS REJECTED - nothing will generate. %s", CREDENTIALS["detail"])
            log.error("  key in force: %s", CREDENTIALS["key"])
            log.error("  it came from: %s", CREDENTIALS["source"])
            log.error("  fix it and restart; the interface says the same thing on every tab.")
            return
        break
    CREDENTIALS.update(state="ok", detail=f"{settings.model} is reachable with this key.",
                       key=describe_key(), source=key_source(),
                       secrets=credentials.report())
    log.info("credentials OK - %s reachable (%s from %s)", settings.model,
             CREDENTIALS["key"], CREDENTIALS["source"])


def _say_where_a_key_could_come_from() -> None:
    """With no key, say the thing that stops this happening on the next machine.

    A fresh pod, a fresh container and a colleague's laptop all arrive here, and
    the answer that has been given four times is "paste it again". It is worth
    one line at the exact moment somebody is about to.
    """
    report = credentials.report()
    if report["state"] == "failed":
        log.error("  %s IS set and could not be read: %s",
                  credentials.PROVIDER_VAR, report["detail"])
        log.error("  That is why there is no key. Fix the provider, not the app.")
        return
    if report["configured"]:
        log.warning("  %s is set (%s) but supplied no ANTHROPIC_API_KEY.",
                    credentials.PROVIDER_VAR, ", ".join(report["provider"]))
        return
    log.warning("  On this machine:   python setup_key.py")
    log.warning("  On every machine:  set %s, e.g.", credentials.PROVIDER_VAR)
    log.warning("    %s='cmd:aws secretsmanager get-secret-value "
                "--secret-id fam --query SecretString --output text'",
                credentials.PROVIDER_VAR)
    log.warning("  See CREDENTIALS.md. A machine that has never run FAM needs "
                "that one line and nothing typed.")


def _announce_research() -> None:
    """Say at startup whether the configured research backend can actually run.

    `exa` is the default and needs a second credential. Without it a researched
    episode fails - it does not quietly search another way - and finding that
    out on a listener's first researched question is the shape of failure this
    project has paid for most. So it is said here, once, loudly, and again on
    every /api/health.

    Not fatal. Most questions are not researched, and an app that refuses to
    start because one path is unconfigured is worse than one that starts and
    says which path is unavailable.

    **What it says changed with §109**, and this is the kind of line that goes
    stale silently: it used to say researched episodes would FAIL rather than
    search another way, which was true when the configured backend was the
    only one. They now fall down a ladder - GDELT, since §135 the only rung
    below Exa - so the consequence is weaker research rather than no episode,
    and saying otherwise would send somebody looking for failures that are not
    happening.
    """
    report = research_report()
    if report.get("backend_replaced"):
        log.warning(
            "RESEARCH_BACKEND=%s is no longer a backend (PROBLEMS.md §135: the "
            "model never searches the web for FAM). Running as %s. Remove the "
            "variable, or set it to exa or gdelt.",
            report["backend_replaced"], report["backend"])
    if not report["unavailable"]:
        log.info("research: %s (%s)", report["backend"], report["exa_detail"])
        return
    log.warning(
        "RESEARCH UNAVAILABLE: RESEARCH_BACKEND=%s but %s.", report["backend"],
        report["exa_detail"])
    log.warning(
        "  Every researched episode will fall down the ladder to %s - weaker "
        "evidence, and a question that needs today's facts and finds none is "
        "refused.",
        " then ".join(report["ladder"][1:]) or "nothing else")
    log.warning(
        "  Set EXA_API_KEY, or set RESEARCH_BACKEND=gdelt with GDELT=1 to make "
        "the keyless index the configured path.")
    log.warning("  Every tab says the same thing; /api/health carries it too.")


async def _warm_stories() -> None:
    """One sweep at startup, so myFAM is full for the first listener.

    Never raises and never blocks boot. `stories.refresh` already swallows
    everything a provider can do wrong; this catches the rest, because an
    exception in a task nobody awaits is a warning in a log and an unexplained
    empty page.
    """
    try:
        await stories_mod.refresh()
    except Exception:  # noqa: BLE001 - a browse page is never worth a failed boot
        log.exception("stories: the warming sweep failed; myFAM will serve its "
                      "evergreen bank until the next refresh")


async def _refresh_stories_forever() -> None:
    """Keep the story pool current with nobody looking (§135).

    The pool used to refresh only when somebody drew myFAM and found it
    stale, so the first listener after a quiet hour saw an hour-old Trending
    row while the sweep they had just triggered ran behind it, and the
    API-Sports allowance the owner asked to be spent on fresh scores sat
    unused overnight. This ticks once a minute and sweeps whenever the pool
    is `STORIES_BACKGROUND_SECONDS` old - every fifteen minutes by default,
    one sweep for every listener, exactly as a page load would have paid.

    Never raises: a sweep that fails is logged by `_warm_stories` and the
    next tick tries again.
    """
    period = float(settings.stories_background_seconds)
    while True:
        await asyncio.sleep(min(60.0, period))
        if time.time() - stories_mod.pool().fetched_at >= period:
            await _warm_stories()


async def _grow_categories() -> None:
    """One growth cycle for the ranking vocabulary. Never raises.

    Beside `_warm_stories` and scheduled the same way, because it is the same
    kind of thing: one background pass whose result serves every listener.
    The tree it produces is read on every browse page and written nowhere
    near one - `categories.sweep` is the only thing in that module that can
    cost a model call, and nothing on a request path calls it.

    Sources, in the order they are trusted: the live story pool's own
    subjects, which have already been judged worth composing a tile about on
    evidence from four providers; and everything listeners have typed, which
    needs `categories.MIN_LISTENERS` different people behind it before it
    widens anybody else's vocabulary.
    """
    try:
        since = time.time() - settings.categories_window_days * 86400
        subjects = [s.subject for s in stories_mod.pool().held()]
        result = await categories_mod.sweep(
            topics_mod.category_tree(),
            EVENTS.subject_texts(since),
            always=subjects,
        )
        if result.get("minted") or result.get("pruned"):
            log.info("categories: %s", result)
    except Exception:  # noqa: BLE001 - a vocabulary is never worth a failed boot
        log.exception("categories: the growth sweep failed; ranking continues "
                      "on the vocabulary already in the tree")


def _seed_categories() -> None:
    """Put the starter vocabulary in the tree. Never raises.

    Separated from `_grow_categories` because the two answer different
    questions and only one of them can fail in an interesting way. Growth
    reads the event log and may call a model; this reads a Python dict. What
    they share is the rule that governs this whole layer: a vocabulary may
    add resolution and may never take the page away, so a seed that cannot be
    written leaves the deployment ranking exactly as it did before.

    Logged when it writes something and silent when it does not, because
    after the first boot it never writes anything and a line every restart is
    a line nobody reads.
    """
    try:
        added = categories_mod.apply_seed(topics_mod.category_tree())
        if added:
            log.info("categories: seeded %d starter nodes", added)
    except Exception:  # noqa: BLE001 - a vocabulary is never worth a failed boot
        log.exception("categories: could not apply the starter vocabulary; "
                      "ranking continues on the hand-written tags")


def _announce_storage() -> None:
    """Say at startup whether a redeploy will erase this deployment's listeners.

    `/api/health` has measured this since §107 and nobody reads a health page
    on the way past. The reported symptom - "the accounts and data are wiped
    every time a new Render deployment is made" - is what that measurement
    was built to answer, and it was answering it to an empty room.

    So it is said in the log, at boot, once, and only when it is a problem.
    The condition is deliberately narrow: a store is *ephemeral* (on the same
    filesystem as the code, so the image replaces it) **and** somebody set its
    environment variable to an absolute path. That pair is the whole
    diagnosis. It means this deployment asked for a mounted disk and did not
    get one - which is what a container with `/data` in its `ENV` and no disk
    attached looks like from inside, and is indistinguishable from a working
    deployment in every other way.

    A laptop trips neither half: nothing is configured and the project root is
    the code's own filesystem, which is correct and normal there. That is why
    this is not simply "warn when anything is ephemeral" - a warning every
    developer sees on every run is a warning nobody reads, which is how this
    one got missed in the first place.
    """
    at_risk = [entry for entry in _database_report()
               if entry.get("persistence") == "image" and entry.get("configured")]
    if not at_risk:
        return
    log.error(
        "STORAGE: %d database(s) are inside the container image and a "
        "redeploy will erase them: %s",
        len(at_risk), ", ".join(f"{e['name']} ({e['path']})" for e in at_risk))
    log.error(
        "  Their environment variables are set, so this deployment expected a "
        "mounted disk and has not got one. On Render: add a disk to the "
        "service with Mount Path /data (render.yaml declares it, but a "
        "service created outside the blueprint has none), then redeploy. "
        "Until then every push starts the listeners over. "
        "`python tools/storage_doctor.py` says the same thing on demand.")


def _announce_voice_control() -> None:
    """Say at startup how the voice will be found, and when it cannot be.

    Read from `voice_control.ladder()`, which is the same list the runtime
    walks and `/api/health` prints - so this line cannot describe an order the
    app does not use. A deployment whose only rung is an address somebody
    pasted is worth knowing about *before* RunPod moves the pod, which is the
    only warning that arrives in time to be useful.
    """
    if settings.voice_backend != "remote":
        return
    import voice_control

    warning = voice_control.startup_warning()
    if warning:
        log.warning("VOICE: %s", warning)
        return
    rungs = [rung for rung in voice_control.ladder() if rung.configured]
    log.info("voice ladder: %s", ", ".join(f"{r.name} ({r.detail})" for r in rungs))
    if [r.name for r in rungs] == ["pinned"]:
        log.warning(
            "  The only rung is REMOTE_VOICE_URL, so a pod that moves takes "
            "the voice with it until somebody edits that variable. Set "
            "VOICE_REGISTRY_TOKEN (and FAM_APP_URL on the pod) or RUNPOD_POD "
            "with RUNPOD_API_KEY - see REMOTE_VOICE.md.")


async def _supervise_voice() -> None:
    """Keep the address of the voice correct while nobody is listening.

    Scheduled, never awaited, and only when there is a remote voice to
    supervise. It is the browse-surface argument applied to infrastructure: the
    resolution that would otherwise happen in front of the first listener after
    a pod moved has already happened by the time they arrive.
    """
    try:
        import voice_control

        await voice_control.supervise_forever()
    except asyncio.CancelledError:  # pragma: no cover - shutdown
        raise
    except Exception:  # noqa: BLE001 - never worth a failed boot
        log.exception("the voice supervisor stopped; the request path will "
                      "still resolve an endpoint on demand")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Pay the voice model's load cost now rather than on the first listener.
    await warm_up()
    # Before a listener finds out the hard way.
    await _verify_credentials()
    _announce_research()
    # Before the first listener signs up into a database that is about to be
    # replaced by the next push.
    _announce_storage()
    # Expired scripts are already filtered out on read, so nothing ever deleted
    # them and the file grew for the life of the deployment. One DELETE at
    # startup is enough: entries expire on a timescale of days, not minutes.
    purged = getattr(SCRIPT_CACHE, "purge_expired", lambda: 0)()
    if purged:
        log.info("cache: dropped %d expired script(s)", purged)
    stale = ATTACHMENTS.purge_expired()
    if stale:
        log.info("attachments: dropped %d expired", stale)
    # Which surfaces this server can predict for. Installed whatever
    # PREFETCH says, because the *registry* is what /api/health reports and a
    # deploy with no sources installed looks identical to one with prefetch
    # switched off - two different problems with two different fixes.
    # Which live providers this deployment asked for. Installed before
    # prefetch for no reason other than reading order; nothing depends on it.
    # Problems are logged and reported rather than raised: a typo in
    # LIVE_SPORTS_PROVIDER must not stop the server, because every episode is
    # still answerable and the writer is told there is no live feed.
    live_sources.install()
    # The world-trending row's source. Same shape as the others: whatever
    # configuration asked for, with problems reported rather than raised, so a
    # typo empties one row instead of stopping the server.
    trending_mod.install()
    # The live sources behind myFAM's story pool, and one warming sweep before
    # the first listener arrives. Scheduled rather than awaited: a browse page
    # that waited on a news sweep to boot would be a server that fails to start
    # when somebody else's API is slow, and the pool is designed to be read
    # while it is still empty - the rails fall back to the evergreen bank and
    # say why. This just means the first listener usually does not see that.
    stories_mod.install()
    asyncio.create_task(_warm_stories())
    if settings.stories and settings.stories_background_seconds > 0:
        _BACKGROUND.add(asyncio.create_task(_refresh_stories_forever()))
    # The starter vocabulary, before the first growth sweep and before the
    # first listener. Awaited rather than scheduled, unlike the two beside it,
    # and the difference is the point: those two call the network and this one
    # is a pass over a dict into SQLite with no model call and no request in
    # it, so scheduling it would buy nothing and would leave a window in which
    # the first browse page ranked on a vocabulary this deployment is
    # supposed to have had before it started. It is idempotent, so every boot
    # after the first adds nothing.
    _seed_categories()
    asyncio.create_task(_grow_categories())
    # The ranker's tile vectors, embedded in a background thread before the
    # first listener arrives (§131). A no-op with no model installed, and
    # never awaited: ~10 ms a tile is a second on a cold process, which is a
    # second no browse page may spend.
    semantic = taste_vectors.describe()
    log.info("ranking: semantic taste %s",
             "on (" + str(semantic.get("model")) + ")" if semantic.get("enabled")
             else "off - " + str(semantic.get("reason")))
    taste_vectors.warm(list(topics_mod.TOPIC_BANK) + list(topics_mod.STARTUP_TOPICS))
    prefetch_sources.install(event_store=EVENTS, mix_store=MIXES,
                             social_store=SOCIAL)
    prefetch.prefetcher(
        generator=None if DEMO_MODE else ScriptGenerator(),
        cache=SCRIPT_CACHE,
    )
    # How the voice is found, and a loop that keeps that answer fresh. Both
    # are no-ops unless VOICE_BACKEND=remote: an in-process card is not
    # somewhere that can move.
    _announce_voice_control()
    if settings.voice_backend == "remote" and settings.voice_supervise_seconds > 0:
        _BACKGROUND.add(asyncio.create_task(_supervise_voice()))
    yield
    # Loops that live as long as the process end with it, rather than being
    # destroyed pending when the event loop closes under them.
    for task in list(_BACKGROUND):
        task.cancel()
    _BACKGROUND.clear()


app = FastAPI(title="Search to Podcast", version="1.0.0", lifespan=lifespan)

# A streamed WAV opens with a 44-byte header, which is not audio.
WAV_HEADER_BYTES = 44

# Hold this much audio before playing anything. Models stream in bursts, so
# starting on the very first sentence means a stall becomes an audible hole a
# second in. With a fast model this costs almost nothing: synthesis runs many
# times faster than speech, so a few seconds of audio arrives in a fraction of
# a second. It is the difference between "starts instantly" and "starts
# instantly and keeps going".
#
# A **quantity**, not a delay: the gate below counts bytes of audio, not
# elapsed time. At TARGET_WPM this is 3.75 words, so an ordinary opening
# sentence satisfies it on the first chunk and it costs nothing at all. It
# only forces a second synthesis when the opening is very short - which is the
# case the Phase 6 first-chunk rule deliberately allows, so the two interact.
#
# Configurable since the preroll sweep, so the value can be measured rather
# than argued about. The default is unchanged, and zero is refused in
# `config.Settings.__post_init__`.
PREROLL_SECONDS = settings.preroll_seconds

#: Generation allowance per listener: [tokens, when they were last topped up].
#: A bucket rather than a gate - see `_rate_limit`.
_gen_tokens: dict[str, list[float]] = {}
#: Recent cheap-read timestamps per client, for the burst-tolerant limiter.
_read_hits: dict[str, deque] = defaultdict(deque)
READ_WINDOW_SECONDS = 10.0
#: Forget a client the limiter has not heard from in this long. These are
#: process-lifetime dictionaries on a server that stays up for weeks, and one
#: entry per client was a slow leak with nothing to stop it.
LIMITER_IDLE_SECONDS = 900.0


def _ms(value: float | None) -> str:
    """A mark as milliseconds, or a dash when it never happened."""
    return "-" if value is None else f"{value * 1000:.0f}ms"


def friendly_error(exc: Exception) -> str:
    """Turn an SDK failure into something the person in the browser can act on."""
    import anthropic

    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)) or (
        isinstance(exc, TypeError) and "authentication method" in str(exc)
    ):
        return (
            "Claude rejected the credentials. Set ANTHROPIC_API_KEY in .env "
            "(or run `ant auth login`) and restart the server."
        )
    if isinstance(exc, anthropic.NotFoundError):
        return f"The model {settings.model!r} is not available to this account. Try MODEL=claude-sonnet-5."
    if isinstance(exc, anthropic.RateLimitError):
        return "Claude is rate limiting this key. Wait a moment and try again."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Could not reach the Claude API. Check the server's network access."
    if isinstance(exc, NoEvidence):
        # The sentence is composed in `research.NoEvidence` rather than here,
        # so the web app and the iOS client cannot word the same refusal two
        # ways - the rule `entitlements.service_label` already follows.
        return str(exc)
    if isinstance(exc, ResearchUnavailable):
        # This one already carries the remedy - "exa_py is not installed,
        # `pip install -r requirements-exa.txt`", or which key is missing.
        # Replacing that with "see the server log" throws away the one
        # sentence that would let the person fix it, which is the whole job
        # of this function. Every other component names what is wrong.
        return f"This question needed research and the backend could not run. {exc}"
    return f"Generation failed: {type(exc).__name__}. See the server log for details."

# One cache shared by every request this worker serves - and, with the SQLite
# backend, by every other worker on the machine too.
SCRIPT_CACHE = build_cache()
ATTACHMENTS = attachments_mod.AttachmentStore()

# With no credentials the app runs on a built-in sample script instead of
# refusing to start. Everything downstream of the model - streaming, pacing,
# duration matching, playback - is exercised for real; only the writer is
# canned. This is what makes the audio approach verifiable before anyone has
# an API key in place.
DEMO_MODE = not settings.anthropic_api_key


def _wake_remote_voice() -> None:
    """Fire-and-forget: start a remote GPU booting, if one is configured.

    Deliberately not awaited. The wake is worth several seconds when it lands
    and must be worth zero when it does not, so nothing here may raise, block,
    or keep a reference the request has to clean up. A no-op for every backend
    but a serverless one, where there is genuinely something asleep.
    """
    if settings.voice_backend != "remote":
        return
    try:
        import remote_voice

        task = asyncio.create_task(remote_voice.RemoteChatterboxEngine.wake())
        # Held so the loop cannot garbage-collect a running task, and dropped
        # the moment it finishes.
        _WAKES.add(task)
        task.add_done_callback(_WAKES.discard)
    except Exception as exc:  # pragma: no cover - a hint that cannot cost one
        log.debug("could not wake the remote voice: %s", exc)


#: Strong references to in-flight wake tasks. asyncio keeps only weak ones, so
#: without this a wake can be collected mid-flight and silently never sent.
_WAKES: set = set()

#: The same, for loops that live as long as the process - the voice supervisor
#: today. A collected supervisor is a deployment that stops noticing that its
#: GPU moved, which is exactly the failure it exists to catch.
_BACKGROUND: set = set()


def _make_pipeline(voice: Optional[str] = None,
                   author: str = "") -> PodcastPipeline:
    """`author` is the listener whose tap paid for this, from
    `_listener(request)` and never from a parameter. It is stamped on anything
    this pipeline writes to the shared cache so Explore can keep somebody's
    own episodes off their own feed - see `cache.recent`."""
    engine = engine_for_voice(voice)
    if DEMO_MODE:
        # Demo mode swaps the model, not the plumbing. It used to pass
        # cache=None, which quietly made Explore impossible without
        # credentials - and Explore is the one surface that needs no
        # credentials at all, since it only ever replays. Keeping the real
        # cache also means demo mode exercises the real hit/miss path.
        # Reads yes, writes never. The canned script does not answer the
        # question it was asked, so caching it puts a briefing about the audio
        # pipeline behind someone's search - for the whole TTL, and for every
        # other listener, including after a key is finally added.
        return PodcastPipeline(
            generator=DemoGenerator(), engine=engine, cache=SCRIPT_CACHE,
            voice=voice, cache_writes=False, author=author,
        )
    return PodcastPipeline(engine=engine, cache=SCRIPT_CACHE, voice=voice,
                           author=author)


def _cache_report() -> dict:
    if SCRIPT_CACHE is None:
        return {"enabled": False}
    report = {"enabled": True, "semantic_key": settings.cache_semantic_key}
    # Near matching is the one cache setting that can serve a *wrong* episode,
    # so the health report says whether it is on and, if it is, what kind of
    # embedding is behind it. "vector matching on" reads like semantics; with
    # no model installed it is lexical, and the difference decides how much to
    # trust a hit. Reporting one without the other would be the §52 mistake in
    # a new place.
    report["near_match"] = settings.cache_vector
    # §132: whether cached episodes replay from kept audio or go back to the
    # voice engine, and how much of the ceiling that audio is using - the
    # disk it lives on is shared with every other database.
    report["audio"] = settings.audio_cache
    if settings.audio_cache:
        try:
            held = SCRIPT_CACHE.stats()
        except Exception:
            held = {}
        report["audio_entries"] = held.get("audio_entries")
        report["audio_mb"] = (round(held["audio_bytes"] / 1048576, 1)
                              if isinstance(held.get("audio_bytes"), int) else None)
        report["audio_ceiling_mb"] = settings.audio_cache_max_mb
    if settings.cache_vector:
        report["embedding"] = embeddings.describe()
        report["threshold"] = settings.cache_vector_threshold
        report["overlap"] = settings.cache_vector_overlap
    if isinstance(SCRIPT_CACHE, (MemoryScriptCache, SqliteScriptCache)):
        report.update(SCRIPT_CACHE.stats())
    return report


def _database_report() -> list[dict]:
    """Where each database actually is, and whether it really opens.

    §52's rule applied to storage: `"status": "ok"` used to be reported while
    four of the five could be pointed anywhere or be unwritable, and no runtime
    surface named a single path. Configuration was being confirmed instead of
    readiness being verified.

    So each entry performs a real read - `SELECT count(*) FROM sqlite_master`,
    which forces the file header and schema to be parsed - and reports the path
    the store is genuinely holding rather than one re-derived here.

    It is deliberately *not* `SELECT 1`: that is a constant expression, answered
    without touching the file, so it returns happily for a path containing
    nothing but rubbish. This check was written that way first and a test caught
    it reporting a corrupt database as readable - the same mistake §52 is about,
    made inside the code meant to prevent it. `writable` is a permission check and is labelled as one; writing on
    every health poll would cost more than it tells anyone.

    **Each entry also says whether a redeploy would erase it** (§107). That
    question was answered by reading the Dockerfile and hoping, and reading the
    Dockerfile was wrong twice: four stores were never pinned to the mounted
    disk at all, so a deployment lost every conversation, saved episode and
    share link on each push while the accounts beside them survived. Nothing
    said so, because from outside a fresh database and a new install look
    identical.

    `persistence` is measured rather than configured, which is the same rule
    the rest of this function keeps. A mounted volume is a different
    filesystem, so `st_dev` answers it: a file on the same device as the
    application code is *inside the container image* and goes when the image
    is replaced. That is a real property of the running machine, not an echo
    of an environment variable - a store pointed at `/data` with no disk
    actually attached reports `image`, which is exactly the case somebody
    needs to be told about and the case a settings check cannot see.
    """
    stores = [
        ("scripts", "CACHE_PATH", getattr(SCRIPT_CACHE, "path", "")),
        ("events", "MYFAM_DB", EVENTS.path),
        ("social", "SOCIAL_DB", SOCIAL.path),
        ("mixes", "MIXES_DB", MIXES.path),
        ("attachments", "ATTACHMENTS_PATH", ATTACHMENTS.path),
        ("accounts", "ACCOUNTS_DB", ACCOUNTS.path),
        ("preferences", "PREFS_DB", PREFS.path),
        ("messages", "MESSAGES_DB", MESSAGES.path),
        ("saved", "SAVED_DB", SAVED.path),
        ("shares", "SHARES_DB", SHARES.path),
        ("quotas", "QUOTAS_DB", QUOTAS.path),
        ("metering", "METERING_DB", METER.path),
        # The grown ranking vocabulary. Reported like the rest rather than
        # lazily like the voice registry below: `category_tree()` opens it on
        # the first feed, every deployment has one, and a tree silently living
        # inside the image is a vocabulary that resets on every push - which
        # from outside looks exactly like one that had never grown.
        ("categories", "CATEGORIES_DB",
         getattr(topics_mod.category_tree(), "path", "")),
    ]
    # Opened lazily and only where workers register themselves, so it is
    # reported only when it exists: a store listed as missing on every machine
    # that never switched the feature on is a health page teaching people to
    # ignore it.
    try:
        import voice_registry

        held = voice_registry.opened()
        if held is not None:
            stores.append(("voice workers", "VOICE_REGISTRY_DB", held.path))
    except Exception:  # pragma: no cover - a report is never load-bearing
        pass
    try:
        code_device = os.stat(PROJECT_ROOT).st_dev
    except OSError:
        code_device = None
    report = []
    for name, env_var, path in stores:
        entry = {
            "name": name,
            "env_var": env_var,
            "path": path or "(in memory)",
            # Whether this machine was told where to put it, or worked it out.
            "configured": bool(os.environ.get(env_var, "").strip()),
        }
        if not path:
            entry["readable"] = True  # the memory backend has no file to open
            entry["writable"] = True
            # Nothing on disk at all. Not "ephemeral because the disk is
            # missing" - there is no file to lose - so it is named for what it
            # is rather than folded into either answer.
            entry["persistence"] = "memory"
            report.append(entry)
            continue
        try:
            sqlite3.connect(path, timeout=2.0).execute(
                "SELECT count(*) FROM sqlite_master"
            ).fetchone()
            entry["readable"] = True
        except Exception as exc:
            entry["readable"] = False
            entry["error"] = f"{type(exc).__name__}: {exc}"
        target = path if os.path.exists(path) else os.path.dirname(path) or "."
        entry["writable"] = os.access(target, os.W_OK)
        entry["bytes"] = os.path.getsize(path) if os.path.exists(path) else 0
        entry["persistence"] = _persistence_of(target, code_device)
        report.append(entry)
    return report


def _persistence_of(target: str, code_device) -> str:
    """"disk", "image" or "unknown" for one database's location.

    Three answers rather than a boolean, because "we could not tell" is a real
    state and reporting it as either of the others is the kind of confident
    wrong answer this project keeps paying for. `unknown` is what a machine
    with no readable device number gives, and it is never read as safe.

    A laptop reports `image` for the project root and that is correct there
    too: nothing is mounted, so nothing is separately durable. It matters on a
    container host, where `image` means the file is replaced with the image.
    """
    if code_device is None:
        return "unknown"
    try:
        return "image" if os.stat(target).st_dev == code_device else "disk"
    except OSError:
        return "unknown"


def _storage_summary(databases: list[dict]) -> dict:
    """One sentence's worth of "will a redeploy erase this".

    Named separately from the per-database list because the question is asked
    about the deployment, not about a file: somebody looking at this wants to
    know whether their listeners' accounts survive the next push, and counting
    twelve entries by hand to find out is how the answer gets skipped.
    """
    at_risk = [d["name"] for d in databases if d.get("persistence") == "image"]
    unknown = [d["name"] for d in databases if d.get("persistence") == "unknown"]
    if not at_risk and not unknown:
        note = "Every database is on a volume separate from the code, so a redeploy keeps them."
    elif at_risk:
        note = ("These are inside the application filesystem and a redeploy replaces them: "
                + ", ".join(at_risk)
                + ". On a container host that erases them - mount a disk and point their "
                  "environment variables at it. On a laptop this is normal and expected.")
    else:
        note = "Could not tell where some databases live: " + ", ".join(unknown)
    return {"durable": [d["name"] for d in databases if d.get("persistence") == "disk"],
            "ephemeral": at_risk, "unknown": unknown, "note": note}


def _limit_key(request: Request) -> str:
    """Who a limiter is pacing: the listener, not the address.

    Keying on `request.client.host` was correct on a laptop and wrong
    everywhere this actually runs. Behind the RunPod public proxy - and
    behind Render's router - every request arrives from the proxy, so the
    whole world shared one bucket: reproduced with two listeners through a
    non-loopback proxy, where the first got 200 and the second got 429
    while `X-Forwarded-For` carried the right addresses and was ignored
    (uvicorn trusts forwarded headers only from 127.0.0.1 by default). At
    RATE_LIMIT_SECONDS=3 that is one episode every three seconds for all
    listeners at once, which is not a limiter, it is an outage.

    Trusting `X-Forwarded-For` would fix the symptom and open a hole: the
    header is client-supplied, so anyone could forge a new one per request
    and never be paced at all - and this limiter guards model spend, which
    `metering.py` exists precisely because it is real.

    The session id is the right key and was already here. It is minted by
    the server, carried in an HttpOnly cookie, and cannot be set by the
    page - the same property that made it the right key for every store.
    So pacing follows the listener across proxies, across Render and
    RunPod, and across a phone changing networks mid-episode.

    The address stays as a fallback for the one case that has no session:
    the middleware mints identity for `/` and `/api/*` but skips
    `/api/health`, and minting can fail. An unpaced endpoint is worse than
    a coarsely paced one, so that case keeps the old behaviour rather than
    keeping no behaviour. The prefixes keep the two namespaces apart, so a
    session id can never collide with an address.
    """
    listener = _listener(request)
    if listener:
        return "listener:" + listener
    return "ip:" + (request.client.host if request.client else "anonymous")


def _prune(store: dict, now: float, stamp_of) -> None:
    """Forget clients that have gone away, so these dicts stay bounded."""
    if len(store) < 512:
        return
    for key in [k for k, v in store.items() if now - stamp_of(v) > LIMITER_IDLE_SECONDS]:
        del store[key]


def _too_fast(seconds: float) -> HTTPException:
    """The pacing refusal, said in a way a client can act on.

    `Retry-After` because without it a client's only strategy is to try again
    immediately, which turns a throttle into the storm it exists to prevent.
    `X-FAM-Refused-By` because this server has two different reasons to answer
    429 - the pace and the allowance - and in an access log they are the same
    three digits (PROBLEMS.md 70).
    """
    wait = max(1, int(seconds + 0.999))
    return HTTPException(
        status_code=429,
        detail="Slow down a moment, then try again.",
        headers={"Retry-After": str(wait), "X-FAM-Refused-By": "pace"},
    )


def _rate_limit(request: Request) -> None:
    """Pace the requests that can actually spend a model call.

    Each one holds a Claude stream and a TTS subprocess open for the whole
    episode, so an unthrottled endpoint is trivially expensive to abuse.

    It is a small **bucket**, not a gate: `RATE_LIMIT_BURST` starts may be spent
    at once and refill one per `RATE_LIMIT_SECONDS`, so the sustained rate is
    exactly what it always was while a listener who taps twice - a voice
    switch, a second tap on an episode still loading - is not answered with
    "Slow down a moment, then try again." A limiter that fires on correct use
    is not protecting anything; it is the failure.

    This belongs on the endpoints that generate, and nowhere else. It was once
    on all eighteen, including the cheap cache and JSON reads that a tab fires
    on open, and `/api/audio` now asks the cache before applying it at all.
    """
    if settings.rate_limit_seconds <= 0:
        return
    now = time.monotonic()
    capacity = max(1, settings.rate_limit_burst)
    client = _limit_key(request)
    bucket = _gen_tokens.get(client)
    if bucket is None:
        _prune(_gen_tokens, now, lambda v: v[1])
        _gen_tokens[client] = [capacity - 1.0, now]
        return
    refilled = (now - bucket[1]) / settings.rate_limit_seconds
    tokens = min(float(capacity), bucket[0] + refilled)
    if tokens < 1.0:
        log.info("pace refused %s", client)
        raise _too_fast((1.0 - tokens) * settings.rate_limit_seconds)
    bucket[0], bucket[1] = tokens - 1.0, now


def _read_limit(request: Request) -> None:
    """A ceiling for the cheap endpoints: JSON reads and cache lookups.

    These cost a SQLite query and no model call, and the interface fires a
    handful of them every time a tab opens, so the limit has to allow bursts.
    It exists to bound a script hammering the server, not to pace a listener.
    """
    if settings.read_limit_per_window <= 0:
        return
    # Same key, same reason. This one is worse when it is wrong: the
    # interface fires several cheap reads whenever a tab opens, so a
    # shared 60-per-10s ceiling is spent by a handful of listeners
    # navigating normally.
    client = _limit_key(request)
    now = time.monotonic()
    hits = _read_hits[client]
    cutoff = now - READ_WINDOW_SECONDS
    while hits and hits[0] < cutoff:
        hits.popleft()
    if len(hits) >= settings.read_limit_per_window:
        raise _too_fast(hits[0] + READ_WINDOW_SECONDS - now)
    if not hits:
        _prune(_read_hits, now, lambda v: v[-1] if v else 0.0)
    hits.append(now)


def _has_password(user_id: str) -> bool:
    """Whether this account can be logged into with a password.

    The settings screen needs it to decide between "change password" and "set
    one", and `unlink_identity` needs it to know whether dropping a provider
    would lock the account. Read through the store rather than exposing the
    hash anywhere near a response.
    """
    try:
        row = ACCOUNTS._conn().execute(  # noqa: SLF001 - one field, no public reader
            "SELECT password FROM accounts WHERE user_id = ?", (user_id,)).fetchone()
    except Exception:
        log.exception("could not check for a password")
        return False
    return bool(row and row[0])


def _quota_snapshot(user: str, tier_name: str) -> dict:
    """Where this listener stands against every countable resource.

    One shape, so the settings screen, the entitlements endpoint and a refusal
    all describe the allowance the same way. A status read never refuses and
    never raises: an interface unable to say what the limit is, is worse than
    one showing a limit that is briefly stale.
    """
    if not user:
        return {}
    return {resource: QUOTAS.status(user, tier_name, resource).as_dict()
            for resource in entitlements.RESOURCES}


def _tier(request: Request) -> str:
    """Which tier this request is entitled to.

    From the resolved session, never from a parameter - the same rule the
    listener id follows, and for the same reason: a plan is worth money, so a
    client-supplied one is a client-supplied upgrade.
    """
    listener = getattr(request.state, "listener", None)
    return entitlements.normalise(listener.tier if listener else "free")


def _reserve(request: Request, resource: str, episode_key: str = "",
             surface: str = ""):
    """Take one from this listener's allowance, or refuse with the reason.

    Returns the granted verdict, which the caller keeps so it can refund. A
    402 would be the pedantic status for "you have run out of allowance", but
    it means "payment required" in a way browsers and SDKs have never agreed
    on; 429 is what a client library already knows to back off from, and the
    `X-FAM-Quota` header carries the whole verdict - the limit, what is left,
    when it resets - so the interface can say what to do next rather than only
    that something was refused.
    """
    user = _listener(request)
    if not user:
        # No session to count against. Not an error - `carry_the_session` logs
        # why - and not a free pass either: `_rate_limit` still applies.
        return None
    try:
        return QUOTAS.reserve(user, _tier(request), resource,
                              episode_key=episode_key,
                              # What they were doing, in their words. The
                              # refusal is about searches, not about the word
                              # the ledger uses for them.
                              service=entitlements.service_label(resource, surface))
    except quotas.QuotaExceeded as exc:
        # Named in the log, because in an access log a quota refusal and a
        # pacing refusal are both "429" and nothing distinguishes them - which
        # is how a day was spent looking at the rate limiter for a refusal the
        # allowance was making (PROBLEMS.md 70).
        log.info("quota refused %s for %s: %s", resource, user, exc.verdict.message)
        raise HTTPException(status_code=429, detail=exc.verdict.message,
                            headers={"X-FAM-Quota": json.dumps(exc.verdict.as_dict()),
                                     "X-FAM-Refused-By": "quota"}
                            ) from exc


def _refund(verdict, user: str) -> None:
    """Give the allowance back unconditionally. For the paths where nothing
    could have been spent - the server has no voice, the request never
    started - so there is nothing to weigh.

    A verdict that took nothing gives nothing back. A repeat of an episode
    already charged for rides on the first reservation, and refunding it would
    hand back a unit that was never taken - allowance earned by failing.
    """
    if verdict is not None and user and getattr(verdict, "charged", True):
        QUOTAS.refund(user, verdict.resource, verdict.window,
                      episode_key=getattr(verdict, "episode_key", ""))


def _refund_if_unspent(verdict, user: str, usage: metering.Usage) -> None:
    """On a **failed** request, give the allowance back if nothing was billed.

    Only ever called from an error path. A request that succeeded keeps its
    reservation whatever it cost to serve - in particular **a cache hit is a
    full episode**: the listener heard one and the GPU produced it, and only
    the Claude call was saved. An earlier version refunded whenever no model
    call had been made, which silently made every cached episode free and
    would have made the allowance unenforceable exactly as the cache warmed up.

    Among failures the rule is *was money spent*:

    * A replay whose entry expired between listing and tapping, and a server
      with no voice installed, spent nothing - charging for those would shrink
      an allowance for reasons the listener cannot see.
    * A generation that called Claude and then failed did spend, and refunding
      it would make a broken key the cheapest thing on the server and the most
      expensive thing on the invoice - the same reasoning `_record_usage`
      already applies to the ledger.

    An episode that arrives empty spent nothing either, and it is refunded for
    the same reason - it used to be the one failure that was charged for, and
    on a server whose voice had gone away that was five silent 502s and then a
    429 for the rest of the day, to a listener who had heard nothing at all.
    Fixing the empty episode is still the real answer; charging for it was a
    second fault sitting on top of the first.
    """
    if verdict is None or not user or not getattr(verdict, "charged", True):
        return
    if usage.model_calls or usage.exa_searches:
        return
    QUOTAS.refund(user, verdict.resource, verdict.window,
                  episode_key=getattr(verdict, "episode_key", ""))


def erase_listener(user_id: str) -> dict:
    """Delete everything FAM holds about one listener, and say what went.

    Required by App Store guideline 5.1.1(v) for any app that lets someone
    create an account, and the shape of it is a decision rather than a loop:

    * **Every per-listener store is emptied** - events, mixes, echoes, the
      profile row, preferences, attachments, quota counters, credentials,
      identities and sessions.
    * **The cost ledger is anonymised, not emptied.** What the GPU and the
      model cost in a given month is a fact about the business; a ledger with
      holes cannot be reconciled against an invoice. The link to the person
      goes and the amount stays (`metering.anonymise`).
    * **The shared script cache is untouched, and needs no decision.** It holds
      no `user_id` at all - it never has - so a script written for this
      listener is already unattributed, and other listeners' Explore feeds do
      not develop holes because somebody left.

    Returns a per-store count so the endpoint reports what it did. Each store
    is attempted independently: a failure in one must not leave the other six
    undeleted, which would be the worst outcome available here - a deletion
    that half happened and reported success.
    """
    removed: dict[str, int] = {}
    for name, store in (("events", EVENTS), ("mixes", MIXES), ("social", SOCIAL),
                        ("preferences", PREFS), ("attachments", ATTACHMENTS),
                        ("quotas", QUOTAS), ("messages", MESSAGES),
                        ("saved", SAVED), ("shares", SHARES)):
        try:
            removed[name] = store.forget(user_id)
        except Exception:
            log.exception("could not erase %s for %r", name, user_id)
            removed[name] = -1
    try:
        removed["usage_rows_anonymised"] = METER.anonymise(user_id)
    except Exception:
        log.exception("could not anonymise usage for %r", user_id)
        removed["usage_rows_anonymised"] = -1
    credentials_gone = ACCOUNTS.delete_account(user_id)
    removed["identities"] = credentials_gone["identities"]
    removed["sessions"] = credentials_gone["sessions"]
    removed["account"] = 1 if credentials_gone["account"] else 0
    return removed


def _episode_key(plan) -> str:
    """Which episode this is, for anything that has to count episodes.

    The cache key: the same question, length, context and research setting are
    the same episode, whoever asks and in whatever voice - voice is deliberately
    not in it, which is what makes switching voice free.

    "" means "this episode has no shared identity, count it every time":
    an episode built on somebody's own attachment is never cached and never
    shared, and with `CACHE_SEMANTIC_KEY` on the key itself needs a model call,
    which is the one cost this product refuses to put in front of the first word.
    """
    if plan.attachments or settings.cache_semantic_key:
        return ""
    if not is_shareable(plan.query):
        return ""
    try:
        return cache_key(plan.query, plan.minutes, None, plan.context, plan.search)
    except Exception:
        log.exception("could not derive an episode key; counting this as a new one")
        return ""


def _already_written(plan) -> bool:
    """Is this episode's script already in the shared cache?

    A request that finds one spends no model call - `pipeline` replays the
    stored sentences - so it is as cheap as an Explore replay and must not be
    paced as a generation. Cheap on purpose: one local SQLite read, the same
    lookup the pipeline is about to do anyway.
    """
    key = _episode_key(plan)
    if not key or SCRIPT_CACHE is None:
        return False
    try:
        return SCRIPT_CACHE.get(key) is not None
    except Exception:
        # A limiter must never be the thing that takes the app down, and
        # "assume it will generate" is the conservative answer.
        log.exception("cache probe failed; pacing this request as a generation")
        return False


def _validated_plan(q: str, minutes: int, context: str = "", search: bool | None = None,
                    cached_only: bool = False, attachments: tuple = ()):
    q = (q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Ask a question first.")
    if len(q) > 500:
        raise HTTPException(status_code=400, detail="Query is too long (500 characters max).")
    if not settings.min_minutes <= minutes <= settings.max_minutes:
        raise HTTPException(
            status_code=400,
            detail=f"Length must be {settings.min_minutes}-{settings.max_minutes} minutes.",
        )
    return plan_episode(q, minutes, (context or "").strip()[:300], search, cached_only,
                        attachments)


class ScriptRequest(BaseModel):
    query: str = Field(..., max_length=500)
    minutes: int = Field(..., ge=1, le=10)
    #: Omitted means "let the question decide" - see /api/audio.
    search: bool | None = None


def _build_report() -> dict:
    """Which code this process is actually running.

    "Is my fix deployed?" was unanswerable from outside this server, so it was
    answered by reasoning about what *should* have happened - which is the
    shape PROBLEMS.md 52 is about. Render injects RENDER_GIT_COMMIT and
    RENDER_GIT_BRANCH into every build; FAM_COMMIT covers a host that does
    not, and a checkout that has its .git is asked directly. Unknown says
    unknown rather than guessing.
    """
    commit = (os.environ.get("RENDER_GIT_COMMIT")
              or os.environ.get("FAM_COMMIT") or "").strip()
    branch = (os.environ.get("RENDER_GIT_BRANCH")
              or os.environ.get("FAM_BRANCH") or "").strip()
    source = "environment"
    if not commit:
        try:
            import subprocess

            import pathlib

            root = pathlib.Path(__file__).resolve().parent
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                text=True, timeout=5, check=True).stdout.strip()
            source = "git"
        except Exception:
            source = "unknown"
    return {"commit": commit or "unknown", "short": (commit or "unknown")[:7],
            "branch": branch or "unknown", "source": source}


@app.post("/api/voice/register")
async def voice_register(request: Request) -> dict:
    """A voice worker saying where it is. The rung that survives a pod moving.

    This is the inbound half of PROBLEMS.md §112: the pod knows its own address
    and the app does not, so the pod says, on boot and on a heartbeat. A
    replaced pod is back in service within one beat and nobody edits a
    dashboard.

    Three things make it safe to have at all:

    * **It is off unless a secret is set.** No `VOICE_REGISTRY_TOKEN`, no
      registration - not an open endpoint with a warning. An endpoint that
      accepts "the voice is here" from anybody redirects every script FAM
      writes to a machine of their choosing, and it would look like the
      feature working.
    * **A registration is a claim, never a promotion.** Nothing is spoken to
      because it registered; `voice_control` still verifies it with a real
      call first, exactly as it does the configured address.
    * **It says nothing back.** The reply names no other worker and no
      setting: the caller is a GPU on somebody else's network, and it needs to
      know only whether it was heard.
    """
    import voice_control
    import voice_registry

    expected = (settings.voice_registry_token or "").strip()
    if not expected:
        # 404 rather than 403: a deployment that has not switched this on has
        # no such endpoint, and saying "wrong token" to an unauthenticated
        # caller tells them there is a token to find.
        raise HTTPException(status_code=404, detail="Not found.")
    sent = (request.headers.get("authorization") or "")
    if sent.lower().startswith("bearer "):
        sent = sent[len("bearer "):]
    if not hmac.compare_digest(sent.strip(), expected):
        # Said out loud here as well as to the caller. A refusal is the whole
        # diagnosis and it used to travel only in the 422/401 body, which goes
        # to a GPU on somebody else's network and nowhere a person looks: from
        # this side a worker heartbeating every minute and being turned away
        # every minute is indistinguishable from no worker at all, and the
        # only symptom is the supervisor's "no voice worker could be found"
        # (PROBLEMS.md §119). Never the token, on either side of the compare.
        log.warning("voice worker registration refused: the bearer token "
                    "presented does not match this app's VOICE_REGISTRY_TOKEN. "
                    "The same string has to be set on the app and on the pod.")
        raise HTTPException(status_code=401, detail="Bad registration token.")

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400,
                            detail=f"Body is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Body is not an object.")

    store = voice_registry.registry()
    try:
        if payload.get("leaving"):
            store.forget(str(payload.get("url") or ""))
            voice_control.demote(
                voice_control.Endpoint(transport="http",
                                       url=voice_registry.clean_url(
                                           str(payload.get("url") or "")),
                                       rung="registered", why="withdrawn"),
                "the worker said it was shutting down")
            return {"ok": True, "registered": False}
        row = store.register(payload)
    except voice_registry.RegistryError as exc:
        # 422 rather than 500: everything this refuses is something the pod's
        # own environment can fix, and the message says which part.
        #
        # Logged for the reason above. This is the refusal that actually
        # happens: since §117 a pod announces its direct TCP address, which is
        # plain HTTP, and an app left at `VOICE_ALLOW_PLAIN_HTTP=0` says no to
        # every heartbeat - correctly, and until now silently.
        log.warning("voice worker registration refused from %s: %s",
                    payload.get("url") or "an unnamed worker", exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    log.info("voice worker registered: %s (%s, contract %s, %s Hz)",
             row.url, row.mode, row.contract or "unknown",
             row.sample_rate or "unknown")
    return {"ok": True, "registered": True, "url": row.url,
            "contract_expected": voice_control.CONTRACT_VERSION,
            "ttl_seconds": settings.voice_registry_ttl}


@app.get("/api/health")
async def health(request: Request) -> dict:
    # Built once and read twice: the list and the summary over it have to
    # describe the same moment, and calling the report a second time would
    # stat every database again to say the same thing.
    _databases = _database_report()
    return {
        "status": "ok",
        # Which commit is serving this request. Without it, "the fix is
        # pushed" and "the fix is live" are the same sentence from outside.
        "build": _build_report(),
        "mode": "demo" if DEMO_MODE else "live",
        "model": settings.model,
        "web_search_default": settings.enable_web_search,
        "http": {"version": describe_http_version(), "http2_negotiated": http2_enabled()},
        "api_key_configured": bool(credentials.active("ANTHROPIC_API_KEY")
                                    or settings.anthropic_api_key),
        # Configured is not the same as working, and only one of them matters.
        # `credentials.secrets` says where the working one came from and whether
        # the next machine will find it without anybody typing it.
        "credentials": CREDENTIALS,
        "sample_rate": build_engine().sample_rate,
        "min_minutes": settings.min_minutes,
        "max_minutes": settings.max_minutes,
        "tts": engine_report(),
        # Who does the looking on a researched episode, and whether that
        # backend can actually run. `unavailable: true` means researched
        # episodes will fail rather than quietly search another way - worth
        # seeing on a tab rather than discovering in a log.
        "research": research_report(),
        # Whether the layer in front of retrieval is running, and on what. An
        # EI that has been switched off looks identical from outside to one
        # that is working - the episodes are merely less relevant - which is
        # the "quietly worse than intended" shape this project keeps paying
        # for. `enabled: false` means the raw query is going to Exa.
        "episode_intelligence": ei_report(),
        # Which questions this server can answer from a live state rather than
        # from an article about one. Everything unconfigured is *named* here
        # rather than silently absent, because a scoreboard question answered
        # from an index is wrong in a way nobody sees until a listener hears it.
        "live_facts": live_facts_report(),
        # What configuration asked for, beside what actually registered. A
        # provider name this build does not know is configured and absent, and
        # that difference does not show in a source list.
        "live_sources": live_sources.report(),
        # The other half of "live": what the world is paying attention to, as
        # opposed to what one entity's state is. Reported separately because
        # they fail separately and are fixed separately.
        "trending": trending_mod.report(),
        # What myFAM's two live rails are actually built from, and which
        # of the four sources this deployment can reach. A pool that had
        # quietly stopped refreshing would look identical from outside to
        # one that was working - `stories.report()` is what makes the
        # difference visible, down to how many tiles were templated
        # because the composer was unavailable.
        "stories": stories_mod.report(),
        # The ranking vocabulary, and how much of it this deployment grew
        # rather than inherited. Worth reporting for the reason every other
        # optional layer here is: a tree that had stopped growing, or one
        # whose placer had quietly stopped answering, would look identical
        # from outside to one that was working - the whole feed would simply
        # be ranked one level blunter than intended. `degraded` is the count
        # of nodes a model has never placed, which is the normal state of a
        # deployment with no key and a warning sign on one with a key.
        "categories": {**topics_mod.category_tree().report(),
                       # Declared against learned. The number worth watching
                       # is `learned`: a deployment whose vocabulary is still
                       # entirely the seed is one where either nothing is
                       # being searched for or the sweep has stopped, and a
                       # node count cannot tell those apart from a healthy
                       # tree that started full.
                       **categories_mod.seed_report(topics_mod.category_tree()),
                       "growing": settings.categories,
                       "placing": settings.categories_place,
                       "min_listeners": categories_mod.MIN_LISTENERS,
                       "min_wordings": categories_mod.MIN_TEXTS,
                       "stale": categories_mod.is_stale()},
        # The second retrieval index, and the Trending row's feed. Reported
        # separately from `research` because they fail separately: Exa can be
        # healthy while this is off, and vice versa.
        "gdelt": gdelt_report(),
        # Whether episodes are being written before anybody asks for them, on
        # what evidence, and whether the guesses are being taken. The hit rate
        # is the only thing that answers CLAUDE.md's open question about how
        # much to prefetch, and a prefetcher nobody checks is a standing bill.
        "prefetch": prefetch.report(),
        "prefetch_sources": prefetch_sources.report(),
        # Which streaming architecture this process is actually running, and
        # whether that was chosen or inherited. A deployment that has been
        # rolled back to `legacy` by hand looks identical to one that has not
        # from the outside, and that is exactly the thing worth being able to
        # ask a running server.
        "streaming_pipeline": settings.streaming_pipeline,
        "streaming_pipeline_default": settings.streaming_pipeline == DEFAULT_PIPELINE,
        # How the listener is told what is happening while they wait. There is
        # no filler any more, so the interface has to be honest instead.
        "search_mode": settings.search_mode,
        # Where that value came from. An env var beats the code default
        # silently and outlives any number of pushes, so "the default was
        # changed" and "this server researches" are different claims and this
        # is the one that settles them.
        "search_mode_source": ("SEARCH_MODE env var"
                               if os.environ.get("SEARCH_MODE", "").strip()
                               else "ENABLE_WEB_SEARCH env var"
                               if os.environ.get("ENABLE_WEB_SEARCH", "").strip()
                               else "config.py default"),
        # How much hidden thinking the writing call does before its first
        # word, and where that came from - same reason as the line above: an
        # EFFORT left in a dashboard beats the code default on every push,
        # and it is expected to be the largest wait on search (PROBLEMS.md §129).
        "writer_effort": settings.effort,
        "writer_effort_source": ("EFFORT env var"
                                 if os.environ.get("EFFORT", "").strip()
                                 else "config.py default"),
        "research_words": sorted(research_words()),
        "cache": _cache_report(),
        # What orders Made for you beyond the tags (§131): whether a semantic
        # model is reading meaning, and whether a fitted order is in force -
        # each with the reason when it is not, because "installed but not
        # loading" and "never installed" have different fixes.
        "ranking": {
            "algo": EVENTS.algo_stamp(),
            "semantic": taste_vectors.describe(),
            "learned": learned_rank.describe(EVENTS),
        },
        # Built, and switched on or not. A tier system that is not enforcing
        # looks exactly like one that is until somebody reaches a limit, and
        # "are limits live on this deploy?" is the question a beta asks most.
        "tiers": {
            "enforced": settings.enforce_quotas,
            "source": "env" if os.environ.get("ENFORCE_QUOTAS", "").strip() else "default",
            "tiers": list(entitlements.TIERS),
        },
        # Every database, its resolved path, and a real read against each.
        "databases": _databases,
        # And the question a deployment actually asks of that list: does a
        # redeploy keep the accounts people made? Measured from where the
        # files are, not from what was configured - see `_persistence_of`.
        "storage": _storage_summary(_databases),
        "voice_store": VOICE_STORE["dir"],
        # The public API surface, so a client can ask rather than assume.
        "api": {"version": API_VERSION, "prefix": API_PREFIX,
                "cors_origins": _ALLOWED_ORIGINS},
        # Whether Google and Apple sign-in can actually complete on this
        # machine, per provider and with the reason when they cannot.
        # "Configured" is not "works" (PROBLEMS.md §52): a missing PyJWT and an
        # empty audience both make the button fail, and both say so here rather
        # than at the moment somebody presses it.
        "oauth": oauth.report(),
        # Whether tier limits bite, and what they are. A server running with
        # them off looks identical from the outside to one running with them
        # on, and that is exactly the thing worth being able to ask.
        "quotas": {"enforced": quotas.settings_enforcing(),
                   "tiers": entitlements.catalogue()["tiers"]},
        # Whether a share can actually leave this machine, and what a
        # recipient finds when it does. Both are unset on a localhost run and
        # both fail in ways nobody sees from inside the app: a share link that
        # names no host is posted to LinkedIn as `/s/abc`, and a landing page
        # with no App Store link draws no way to get FAM at all. Configured is
        # not working, but for a URL it is the whole of it - there is nothing
        # to call.
        "sharing": {
            "public_base_url": bool(settings.public_base_url),
            # Where a share link's host actually comes from on this machine.
            # `env` is PUBLIC_BASE_URL; `request` is derived from the request
            # that asked, which is what makes sharing work on a deployment
            # nobody configured; `none` is a loopback host, where there is no
            # honest link to give and the app says so rather than inventing
            # `localhost`. Reported because the three are indistinguishable
            # from outside and only one of them used to exist.
            "link_host": ("env" if settings.public_base_url
                          else "request" if _public_base(request) else "none"),
            "link_base": _public_base(request),
            "app_store_url": bool(settings.app_store_url),
            # What a recipient can do besides listen. False is a deliberate
            # state, not a misconfiguration - see `sharing.landing_doors`.
            "landing_doors": sharing.landing_doors(settings.app_store_url),
            "targets": list(sharing.TARGET_KEYS),
        },
    }


class CredentialsRequest(BaseModel):
    """Email **or** phone, plus a password.

    Both optional at the schema level and exactly one required in the handler,
    because "you must send one of these two" is not a thing a field validator
    can say clearly, and a 422 from the framework is a worse message than a
    sentence written for the person reading it.
    """

    email: str = Field("", max_length=accounts_mod.MAX_EMAIL)
    phone: str = Field("", max_length=accounts_mod.MAX_PHONE * 2)
    password: str = Field(..., max_length=accounts_mod.MAX_PASSWORD)
    #: Native clients only. See `_maybe_token` - a browser must never ask for
    #: this, because reading the token in script is precisely what the HttpOnly
    #: cookie exists to prevent.
    want_token: bool = False


class ProviderRequest(BaseModel):
    """A verified identity token from Google or Apple."""

    provider: str = Field(..., max_length=16)
    id_token: str = Field(..., max_length=8192)
    #: The raw nonce the client generated for this sign-in, if it used one.
    #: Sending it is what stops a captured token being replayed; the server
    #: accepts both the raw value and its SHA-256, because Apple is sent the
    #: hash and Google echoes the original.
    nonce: str = Field("", max_length=256)
    want_token: bool = False


class ProfileRequest(BaseModel):
    """Account settings. Every field optional and `None` means "leave it" -
    an empty string means "remove it", which is a different request."""

    display_name: Optional[str] = Field(None, max_length=accounts_mod.MAX_DISPLAY_NAME)
    email: Optional[str] = Field(None, max_length=accounts_mod.MAX_EMAIL)
    phone: Optional[str] = Field(None, max_length=accounts_mod.MAX_PHONE * 2)


class NewPasswordRequest(BaseModel):
    """Setting a first password, for an account created with Google or Apple.
    `PasswordChangeRequest` cannot serve this: there is no current password to
    prove, and asking for one would lock those accounts out of ever having
    one."""

    new: str = Field(..., max_length=accounts_mod.MAX_PASSWORD)


class PasswordChangeRequest(BaseModel):
    current: str = Field(..., max_length=accounts_mod.MAX_PASSWORD)
    new: str = Field(..., max_length=accounts_mod.MAX_PASSWORD)


@app.get("/api/auth/me")
async def auth_me(request: Request) -> dict:
    """Who the server thinks is asking. Cheap, and the only way to find out -
    the id is not in the page's reach, which is the point of the cookie."""
    listener = getattr(request.state, "listener", None)
    if listener is None:
        return {"user_id": "", "email": "", "authenticated": False}
    return listener.as_dict()


def _one_identifier(req: CredentialsRequest) -> str:
    """Which of email or phone this *login* is using. Exactly one.

    Logging in is still a choice of one identifier - two would be two lookups
    with nothing to do when they disagree. Signing up is not: see
    `_signup_identity`.
    """
    if bool(req.email) == bool(req.phone):
        raise HTTPException(
            status_code=400,
            detail="Send either an email address or a phone number, not both.")
    return "email" if req.email else "phone"


def _signup_identity(req: CredentialsRequest) -> str:
    """Which identifiers a sign-up is carrying: "email", "phone" or "both".

    Sign-up asks for an address *and* a number and keeps both on one account,
    so "both" is the ordinary case rather than a contradiction. It was refused
    for as long as this endpoint shared `_one_identifier` with login, which
    meant filling in the number the form itself offered failed with a message
    about how the server stores things.
    """
    if not req.email and not req.phone:
        raise HTTPException(
            status_code=400,
            detail="Send an email address or a phone number.")
    if req.email and req.phone:
        return "both"
    return "email" if req.email else "phone"


def _maybe_token(request: Request, token: str, want_token: bool) -> dict:
    """The session token in the response body, but only if it was asked for.

    A native app has to be handed the token: it stores it in the Keychain and
    sends it as `Authorization: Bearer`, because iOS clears its cookie jar
    under conditions the app does not control.

    A browser must never ask. The cookie is set either way, and it is HttpOnly
    precisely so that page script cannot read it - a web client that requests
    the token has voluntarily undone that, and an XSS on that page can then
    take the session rather than merely borrow it. Which is why this is an
    explicit opt-in rather than something every response carries.
    """
    if not want_token:
        return {}
    return {"session_token": token, "expires_in": accounts_mod.SESSION_TTL}


@app.post("/api/auth/signup")
async def auth_signup(req: CredentialsRequest, request: Request) -> dict:
    """Attach an account to the identity this listener already has.

    Not "create a user": they exist already, with a history and possibly mixes
    and echoes. Signing up claims that identity rather than starting a second
    one, which is why nothing has to be migrated.
    """
    _rate_limit(request)
    user = _require_listener(request)
    try:
        kind = _signup_identity(req)
        if kind == "phone":
            listener = ACCOUNTS.sign_up_phone(user, req.phone, req.password)
        else:
            listener = ACCOUNTS.sign_up(user, req.email, req.password,
                                        phone=req.phone or "")
    except accounts_mod.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # A fresh token even though the id has not changed, so that a native
    # client is handed one it can store. The old session stays valid: nothing
    # about signing up should log out the browser tab that did it.
    token = _session_token(request)
    if req.want_token and not token:
        token, _ = ACCOUNTS.new_session(listener.user_id)
        request.state.set_session = token
    return {**listener.as_dict(), **_maybe_token(request, token, req.want_token)}


@app.post("/api/auth/login")
async def auth_login(req: CredentialsRequest, request: Request) -> dict:
    """Verify credentials and move this browser onto that account's identity.

    A fresh session token is minted rather than the current one being
    repointed: reusing it would let a token captured before login keep working
    after it, which is the session-fixation bug.
    """
    _rate_limit(request)
    try:
        if _one_identifier(req) == "email":
            listener = ACCOUNTS.log_in(req.email, req.password)
        else:
            listener = ACCOUNTS.log_in_phone(req.phone, req.password)
    except accounts_mod.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    old = _session_token(request)
    token, _user_id = ACCOUNTS.new_session(listener.user_id)
    if old:
        # The anonymous session this client was carrying is finished with.
        ACCOUNTS.end_session(old)
    request.state.set_session = token
    return {**listener.as_dict(), **_maybe_token(request, token, req.want_token)}


@app.post("/api/auth/provider")
async def auth_provider(req: ProviderRequest, request: Request) -> dict:
    """Sign in with Google or Sign in with Apple.

    The app gets an identity token from the platform SDK and posts it here;
    `oauth.verify` checks the signature, issuer, audience and nonce against the
    provider's published keys. There is no code exchange and no client secret,
    because a native app needs neither - which removes the most common way this
    is built wrong.

    The two failure modes are deliberately different status codes. 503 means
    *this server* cannot verify tokens for that provider - PyJWT is missing, or
    no audience is configured - and is an operator's problem with an operator's
    message. 401 means the token was checked and refused.
    """
    _rate_limit(request)
    provider = (req.provider or "").strip().lower()
    try:
        verified = oauth.verify(provider, req.id_token, req.nonce)
    except oauth.OAuthUnavailable as exc:
        log.error("provider sign-in unavailable: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except oauth.OAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    try:
        listener, is_new = ACCOUNTS.sign_in_with(
            verified.provider, verified.subject, email=verified.email,
            display_name=verified.name,
            current_user_id=_require_listener(request))
    except accounts_mod.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    old = _session_token(request)
    token, _user_id = ACCOUNTS.new_session(listener.user_id)
    if old:
        ACCOUNTS.end_session(old)
    request.state.set_session = token
    return {**listener.as_dict(), "is_new": is_new,
            # Said out loud rather than left for the client to work out from
            # the address: an Apple relay address forwards today and can be
            # switched off by its owner tomorrow, so nothing should promise to
            # reach somebody there.
            "private_relay": verified.is_private_relay,
            **_maybe_token(request, token, req.want_token)}


@app.post("/api/auth/logout")
async def auth_logout(request: Request) -> dict:
    """Drop the session. The next request mints a fresh anonymous one, so the
    app keeps working - as a different listener, with nothing of theirs."""
    _read_limit(request)
    ACCOUNTS.end_session(_session_token(request))
    request.state.set_session = ""
    return {"ok": True}


@app.post("/api/auth/password")
async def auth_password(req: PasswordChangeRequest, request: Request) -> dict:
    """Change it, and log every device out - including this one. A password
    change that leaves old sessions alive does not do what people believe."""
    _rate_limit(request)
    try:
        ACCOUNTS.change_password(_require_listener(request), req.current, req.new)
    except accounts_mod.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    request.state.set_session = ""
    return {"ok": True}


@app.post("/api/auth/password/set")
async def auth_password_set(req: NewPasswordRequest, request: Request) -> dict:
    """Add a first password to an account that signed up with Google or Apple.

    Its own endpoint rather than a branch inside the change-password one,
    because the two have different preconditions: that one proves the current
    password, and this one is reachable exactly when there is none to prove.
    Merging them would mean a request that omits `current` is sometimes a
    legitimate first set and sometimes an attempt to skip the check.
    """
    _rate_limit(request)
    try:
        ACCOUNTS.set_password(_require_account(request), req.new)
    except accounts_mod.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.get("/api/account")
async def account_read(request: Request) -> dict:
    """Everything the settings screen shows: who you are, how you get in,
    what tier you are on, and where else you are signed in."""
    _read_limit(request)
    user = _require_account(request)
    account = ACCOUNTS.account(user) or {}
    tier_name = entitlements.normalise(account.get("plan", "free"))
    return {
        "user_id": user,
        "email": account.get("email", ""),
        "phone": account.get("phone", ""),
        "display_name": account.get("display_name", ""),
        "created": account.get("created", 0),
        "identities": ACCOUNTS.identities_for(user),
        "has_password": bool(ACCOUNTS.account(user) and _has_password(user)),
        "sessions": ACCOUNTS.sessions_for(user, _session_token(request)),
        "entitlements": entitlements.describe(tier_name),
        # Said here as well as on /api/entitlements because a settings screen
        # that shows a plan without showing what is left of it invites the
        # question it cannot answer.
        "usage": _quota_snapshot(user, tier_name),
    }


@app.post("/api/account")
async def account_update(req: ProfileRequest, request: Request) -> dict:
    """Change the account's own details. Not preferences - those are
    `/api/preferences`, they are not credentials, and they work without an
    account at all."""
    _rate_limit(request)
    try:
        account = ACCOUNTS.update_profile(
            _require_account(request), display_name=req.display_name,
            email=req.email, phone=req.phone)
    except accounts_mod.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return account


@app.delete("/api/account")
async def account_delete(request: Request) -> dict:
    """Delete the account and everything FAM holds about this listener.

    Required of any app that offers account creation (App Store guideline
    5.1.1(v)), and it has to be reachable *in the app* rather than through a
    support address - which is why it is an endpoint and not a mailbox.

    `erase_listener` says exactly what is removed, what is anonymised and what
    is deliberately untouched. The session is dropped afterwards, so the next
    request mints a fresh anonymous listener and the app keeps working.
    """
    _rate_limit(request)
    user = _require_account(request)
    removed = erase_listener(user)
    request.state.set_session = ""
    log.info("erased listener %r: %s", user, removed)
    return {"ok": True, "removed": removed}


@app.post("/api/account/signout-everywhere")
async def account_signout_everywhere(request: Request) -> dict:
    """Drop every other session, keeping this one.

    The thing somebody reaches for when they think a device is lost, and it is
    the reason `sessions_for` reports a count without reporting tokens.
    """
    _rate_limit(request)
    ended = ACCOUNTS.end_other_sessions(_require_account(request),
                                        _session_token(request))
    return {"ok": True, "ended": ended}


@app.delete("/api/account/identity")
async def account_unlink(request: Request,
                         provider: str = Query(..., max_length=16)) -> dict:
    """Remove one sign-in route, unless it is the only way in."""
    _rate_limit(request)
    try:
        removed = ACCOUNTS.unlink_identity(_require_account(request),
                                           provider.strip().lower())
    except accounts_mod.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": bool(removed), "removed": removed}


class FollowRequest(BaseModel):
    """Who to follow. By handle from a search, or by the id a search returned -
    never by a listener id the client made up, which is why both come from
    somewhere the server produced."""

    handle: str = Field("", max_length=social_mod.MAX_HANDLE + 1)
    user_id: str = Field("", max_length=64)


class SendMessageRequest(BaseModel):
    to: str = Field(..., max_length=64)
    text: str = Field("", max_length=messages_mod.MAX_TEXT)
    #: An episode share carries the question and the length, which is the
    #: script cache's key - so the recipient's play is a cache hit and the
    #: share cost one row.
    query: str = Field("", max_length=messages_mod.MAX_QUERY)
    minutes: int = Field(0, ge=0, le=60)
    title: str = Field("", max_length=messages_mod.MAX_TITLE)


class SaveRequest(BaseModel):
    query: str = Field(..., max_length=saved_mod.MAX_QUERY)
    minutes: int = Field(DEFAULT_MINUTES, ge=0, le=60)
    title: str = Field("", max_length=saved_mod.MAX_TITLE)
    source: str = Field("", max_length=40)
    folder_id: str = Field("", max_length=64)


class FolderRequest(BaseModel):
    name: str = Field(..., max_length=saved_mod.MAX_NAME)


class MoveRequest(BaseModel):
    folder_id: str = Field("", max_length=64)


class ShareRequest(BaseModel):
    query: str = Field(..., max_length=sharing.MAX_QUERY)
    minutes: int = Field(DEFAULT_MINUTES, ge=0, le=60)
    title: str = Field("", max_length=sharing.MAX_TITLE)


# --- friends --------------------------------------------------------------

@app.get("/api/friends")
async def friends_read(request: Request) -> dict:
    """Who this listener follows, who follows them, and who does both.

    Mutuals are derived rather than stored, so there is no request-and-accept
    state machine and no way for the two directions to disagree.
    """
    _read_limit(request)
    user = _require_account(request)
    return {
        "following": SOCIAL.following(user),
        "followers": SOCIAL.followers(user),
        "friends": SOCIAL.friends(user),
        "counts": SOCIAL.follow_counts(user),
        # Who followed since this listener last looked. Read here rather than
        # from an endpoint of its own because the interface asks this question
        # at the same moment it asks the others, and a badge is not worth a
        # second round trip.
        "new_followers": SOCIAL.new_followers(user),
    }


@app.post("/api/friends/seen")
async def friends_seen(request: Request) -> dict:
    """They opened the Friends tab, so nobody is new any more.

    Deliberately *not* done when the follower popup is drawn: a badge that
    cleared itself the moment a popup appeared would be a count nobody ever
    got to read.
    """
    _read_limit(request)
    SOCIAL.mark_followers_seen(_require_account(request))
    return {"ok": True}


@app.get("/api/person")
async def person_profile(request: Request,
                         handle: str = Query("", max_length=social_mod.MAX_HANDLE + 1),
                         user_id: str = Query("", max_length=64)) -> dict:
    """Another listener's profile: **only what they have chosen to publish.**

    The rule this endpoint exists under, and the reason it did not exist
    before: what somebody has listened to is theirs. There is no play count
    here, no completion total, no subjects inferred from behaviour and no
    history. Three things come back, and each one is something the person
    actively decided to show:

    * **public mixes** - a mix is private by default and appears here only
      once its owner switched it to public;
    * **vibes** - a vibe *is* the act of showing somebody an episode, so a
      list of them is a list of things they chose to publish;
    * **interests they have not hidden** - declared in the first run or in
      Settings, minus anything they turned off in Edit profile.

    The standing between the two of you comes from the follow graph, which
    both sides can already see.
    """
    _read_limit(request)
    me = _listener(request)
    target = ""
    if user_id:
        # Only somebody already in this listener's graph, by id. An id is
        # guessable in a way a handle search is not, and the graph is the
        # boundary: you may look at people you or they have followed.
        known = {p["user_id"] for p in
                 SOCIAL.following(me) + SOCIAL.followers(me)} if me else set()
        target = user_id if user_id in known else ""
    if not target and handle:
        wanted = handle.strip().lstrip("@").lower()
        found = [p for p in SOCIAL.find_people(wanted, exclude_user=me, limit=5)
                 if p["handle"] == wanted]
        target = found[0]["user_id"] if found else ""
    if not target:
        raise HTTPException(status_code=404, detail="No listener by that handle.")

    person = SOCIAL.person(target)
    prefs = PREFS.get(target)
    # What they pinned, if they pinned anything, and otherwise what they
    # declared minus what they hid. Capped at the same four their own page
    # draws.
    #
    # **Deliberately not the ranked list their own profile now shows.** That
    # ranking is read off what they have listened to, and the rule this whole
    # endpoint exists under is that what somebody has listened to is theirs:
    # a pill row inferred from behaviour would publish exactly the thing the
    # docstring above promises is never here, in a form that reads as a
    # statement they made. A pin is a statement they made. A declared interest
    # is a statement they made. Neither of those is their history.
    #
    # So somebody's own page can be up to date with their listening while
    # their public one stays a matter of record, and the editor on the profile
    # is how the first becomes the second - which is what pinning is for.
    pinned = list(prefs.profile_interests)
    if pinned:
        shown = topics_mod.profile_interests(
            topics_mod.ranked_interests(EVENTS, target, chosen=prefs.interests,
                                        chosen_topics=prefs.topics),
            pinned=pinned, hidden=prefs.hidden_interests)[0]
    else:
        shown = [{"id": tag, "label": topics_mod.TAG_LABELS[tag], "kind": "facet"}
                 for tag in topics_mod.facets_only(prefs.public_interests)
                 ][:topics_mod.PROFILE_INTEREST_SLOTS]
    interests = [row["id"] for row in shown]
    return {
        # Deliberately no `user_id`: this response is drawn, not acted on, and
        # the follow buttons on that screen already have the id they need from
        # the graph. A listener id the client did not need is a listener id
        # that can be sent back.
        "name": person["name"],
        "handle": person["handle"],
        "avatar": person["avatar"],
        "joined": person["joined"],
        "mixes": [m.as_dict() for m in MIXES.public_for_user(target)],
        # Each vibe carries its subject, read off its own words - the same
        # label a shared episode's chat preview uses.
        "vibes": [dict(e.as_dict(person["name"], person["handle"]),
                       topic=_topic_label(e.query, e.title))
                  for e in SOCIAL.echoes_by(target, limit=12)],
        "vibe_count": len(SOCIAL.echoes_by(target, limit=200)),
        "interests": interests,
        "interest_labels": [row["label"] for row in shown],
        "follows": SOCIAL.follow_counts(target),
    }


@app.get("/api/people")
async def people_search(request: Request,
                        q: str = Query("", max_length=64)) -> dict:
    """Find somebody by handle or name, to follow or share with.

    Only people who have chosen a handle are findable. Someone who has never
    set one is not hidden from a directory - they are not in one, which is the
    difference between a private setting and a feature nobody enabled.
    """
    _read_limit(request)
    user = _require_account(request)
    found = SOCIAL.find_people(q, exclude_user=user)
    following = {p["user_id"] for p in SOCIAL.following(user)}
    for person in found:
        person["following"] = person["user_id"] in following
    return {"people": found}


@app.post("/api/friends/follow")
async def friends_follow(req: FollowRequest, request: Request) -> dict:
    _rate_limit(request)
    user = _require_account(request)
    target = req.user_id
    if not target and req.handle:
        found = SOCIAL.find_people(req.handle, exclude_user=user, limit=5)
        exact = [p for p in found
                 if p["handle"] == req.handle.strip().lstrip("@").lower()]
        target = exact[0]["user_id"] if exact else ""
    if not target:
        raise HTTPException(status_code=404, detail="No listener by that handle.")
    try:
        changed = SOCIAL.follow(user, target)
    except social_mod.SocialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "changed": changed,
            "counts": SOCIAL.follow_counts(user)}


@app.delete("/api/friends/follow")
async def friends_unfollow(request: Request,
                           user_id: str = Query(..., max_length=64)) -> dict:
    _rate_limit(request)
    user = _require_account(request)
    return {"ok": SOCIAL.unfollow(user, user_id),
            "counts": SOCIAL.follow_counts(user)}


# --- messages -------------------------------------------------------------

def _decorate(people: list[dict]) -> dict:
    """user_id -> what to draw. One lookup for a whole inbox rather than one
    per row."""
    out = {}
    for person in people:
        out[person["user_id"]] = {"name": person.get("name") or "",
                                  "handle": person.get("handle") or "",
                                  # Carried because a notification draws a
                                  # face, and the graph read already has it -
                                  # fetching it per banner would be one
                                  # round trip to avoid copying a string.
                                  "avatar": person.get("avatar") or ""}
    return out


@app.get("/api/messages")
async def messages_inbox(request: Request) -> dict:
    """Every conversation, most recent first, with an unread count each."""
    _read_limit(request)
    user = _require_account(request)
    inbox = MESSAGES.inbox(user)
    known = _decorate(SOCIAL.following(user) + SOCIAL.followers(user))
    for row in inbox:
        person = known.get(row["with"]) or SOCIAL.person(row["with"])
        row["name"] = person.get("name") or "Someone"
        row["handle"] = person.get("handle") or ""
        # Their picture, where they have set one - the list drew initials for
        # everybody, which made a conversation with a face look like one
        # with a stranger (§127). "" means initials, as before.
        row["avatar"] = person.get("avatar") or ""
        # "Shared an episode · Money & markets" - the subject, so a preview
        # says what was shared without printing a whole title into one line.
        last = row.get("last") or {}
        if last.get("kind") == "episode":
            last["topic"] = _topic_label(last.get("query") or "", last.get("title") or "")
    return {"threads": inbox, "unread": MESSAGES.unread_total(user)}


def _topic_label(query: str, title: str = "") -> str:
    """The one facet an episode is about, as a listener reads it, or "".

    Keyword tagging over the words already in hand - the same
    `tags_for_text` history is ranked on - so it costs no model call and says
    nothing when the words match nothing, rather than guessing a subject.
    """
    tags = topics_mod.facets_only(topics_mod.tags_for_text(f"{query} {title}"))
    return topics_mod.TAG_LABELS.get(tags[0], "") if tags else ""


@app.get("/api/messages/thread")
async def messages_thread(request: Request,
                          with_: str = Query(..., alias="with", max_length=64),
                          since: int = Query(0, ge=0)) -> dict:
    """One conversation, and reading it marks it read.

    Marking on read rather than on a separate call, because the two would drift
    the moment a client crashed between them - and a thread that stays unread
    after somebody has read it is the more annoying direction.

    **`since` is what makes an open conversation live** (§107). Messages used
    to appear only when the screen was opened, so two people talking had to
    leave the chat and come back to see each other - which is not a slow chat,
    it is a chat that does not work. A client holding the conversation polls
    with the highest id it has and gets back only what arrived after it, so
    staying current costs a primary-key comparison rather than the whole
    history every couple of seconds.

    `head` is the cursor to send next time, and it is returned whether or not
    anything came back: a client that derived it from the last message would
    have no cursor at all on an empty poll and would have to fall back to
    re-reading everything - which is the thing being removed.
    """
    _read_limit(request)
    user = _require_account(request)
    try:
        thread = MESSAGES.thread(user, with_, after_id=since)
    except messages_mod.MessageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    MESSAGES.mark_read(user, with_)
    person = SOCIAL.person(with_)
    head = max([m.id for m in thread] + [since])
    rows = [m.as_dict(user) for m in thread]
    # What the chat's episode card and its receipt draw. `finished` is read
    # off this listener's own completions, so "You finished it" is a fact the
    # event log holds rather than a guess from the conversation. Matched on
    # the question alone: a completion event carries no length, so finishing
    # the two-minute version counts for a shared five-minute one.
    if any(r["kind"] == "episode" for r in rows):
        done = {e.text for e in EVENTS.for_user(user, limit=1000)
                if e.kind == "complete" and e.text}
        for r in rows:
            if r["kind"] == "episode":
                r["topic"] = _topic_label(r["query"] or "", r["title"] or "")
                r["finished"] = (not r["mine"]) and (r["query"] in done)
    return {"with": {"user_id": with_, "name": person.get("name") or "Someone",
                     "handle": person.get("handle") or "",
                     "avatar": person.get("avatar") or ""},
            # Whether they are typing to this listener right now (§127). Read
            # on the same two-second poll that tops the conversation up, so
            # the dots cost no request of their own.
            "typing": typing_mod.is_typing(with_, user),
            "messages": rows,
            # True for the ordinary open, False for a poll that is topping one
            # up. The client replaces the conversation on one and appends on
            # the other, and guessing from `since` in two places is how those
            # two get out of step.
            "partial": bool(since),
            "head": head}


@app.get("/api/notifications")
async def notifications(request: Request,
                        since: int = Query(0, ge=0),
                        bootstrap: bool = Query(False)) -> dict:
    """What has happened to this listener since they last asked.

    One endpoint for both kinds of drop-down - a message arriving and somebody
    following you - because the interface asks both questions at the same
    moment, from the same timer, and two polls to answer one question is two
    things to keep in step for no benefit.

    **The cursor is the client's, and that is the whole design.** A message's
    *read* state is a fact about a conversation somebody opened; whether it
    has been *announced* is a fact about a banner this client already raised.
    Deriving the second from the first would mean opening one chat silenced
    the notifications for every other - so the client holds `since` and the
    server only answers what it was asked.

    `bootstrap` is the first call after the app loads: it returns the cursor
    and deliberately nothing else. Without it, opening the app would raise a
    banner for every message ever sent to this listener, which is the usual
    way this feature is got wrong.

    Follows have no id to page on - the graph stores timestamps - so they are
    answered by the same `new_followers` query the Friends badge already uses,
    which is cleared by opening the Friends tab and by nothing else.
    """
    _read_limit(request)
    user = _listener(request)
    # Anonymous listeners have no messages and no followers by construction:
    # both need an account, which is the boundary ACCOUNT_REQUIRED draws. An
    # empty answer rather than a 401, because this is polled on a timer and a
    # timer that logs an error every few seconds is a broken-looking app.
    listener = getattr(request.state, "listener", None)
    if not (listener is not None and listener.is_authenticated):
        return {"messages": [], "follows": [], "head": 0, "unread": 0}

    head = MESSAGES.latest_id(user)
    if bootstrap:
        return {"messages": [], "follows": [], "head": head,
                "unread": MESSAGES.unread_total(user)}

    arrived = MESSAGES.arrived_for(user, after_id=since)
    known = _decorate(SOCIAL.following(user) + SOCIAL.followers(user))
    out = []
    for message in arrived:
        person = known.get(message.sender) or SOCIAL.person(message.sender)
        row = message.as_dict(user)
        row["from"] = {"user_id": message.sender,
                       "name": person.get("name") or "Someone",
                       "handle": person.get("handle") or "",
                       "avatar": person.get("avatar") or ""}
        out.append(row)
    return {
        "messages": out,
        # The same list the follower popup draws, so the two cannot disagree
        # about who is new. Tapping one goes to Friends, which is also what
        # marks them seen - a badge cleared by something merely being drawn is
        # a count nobody got to read.
        "follows": SOCIAL.new_followers(user),
        "head": max([head] + [m.id for m in arrived]),
        "unread": MESSAGES.unread_total(user),
    }


class TypingRequest(BaseModel):
    to: str = Field(..., max_length=64)


@app.post("/api/messages/typing")
async def messages_typing(req: TypingRequest, request: Request) -> dict:
    """Say "I am typing to this person", for the three dots on their side.

    Held in memory for a few seconds and never written anywhere - see
    `typing_indicator.py` for why that is the whole design. Paced like the
    rest of messaging, and a client sends it at most every couple of seconds
    while keys are being pressed.
    """
    _read_limit(request)
    user = _require_account(request)
    typing_mod.note(user, req.to)
    return {"ok": True}


@app.post("/api/messages")
async def messages_send(req: SendMessageRequest, request: Request) -> dict:
    """Send a message, or share an episode into a conversation.

    Paced by `_read_limit` rather than `_rate_limit`: this provably makes no
    model call - it writes one row pointing at a question - and pacing it at
    one every three seconds would make a conversation unusable. The generation
    happens when the recipient taps, against their own allowance.
    """
    _read_limit(request)
    user = _require_account(request)
    kind = "episode" if req.query else "text"
    # Sending ends the typing, now rather than when the dots time out - a
    # message arriving under a still-bouncing indicator reads as a second one
    # on its way.
    typing_mod.clear(user, req.to)
    try:
        message = MESSAGES.send(user, req.to, kind=kind, text=req.text,
                                query=req.query, minutes=req.minutes,
                                title=req.title)
    except messages_mod.MessageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # A share is a signal about taste as much as an act of sending, and the
    # feed already learns from plays. Recorded for the sender only: what the
    # recipient thinks of it is not known until they press play.
    if kind == "episode":
        EVENTS.record(topics_mod.Event(
            user, "share", "", req.query, topics_mod.tags_for_text(req.query)))
    return {"ok": True, "message": message.as_dict(user)}


# --- save for later -------------------------------------------------------

@app.get("/api/saved")
async def saved_read(request: Request,
                     folder_id: Optional[str] = Query(None, max_length=64),
                     q: str = Query("", max_length=saved_mod.MAX_QUERY),
                     minutes: int = Query(0, ge=0, le=60)) -> dict:
    """The shelf, or - with `q` - whether one episode is on it.

    The second form is what draws the save control's state. It is the same
    shape as `/api/vibe`'s, and for the same reason: a control that lights up
    has to be able to ask whether it is lit without pulling the whole shelf
    down to find out.
    """
    _read_limit(request)
    user = _require_account(request)
    if q:
        return {"saved": SAVED.find(user, " ".join(q.split()), minutes) is not None}
    return {
        "folders": SAVED.folders(user),
        "items": [i.as_dict() for i in SAVED.items(user, folder_id)],
    }


@app.post("/api/saved")
async def saved_save(req: SaveRequest, request: Request) -> dict:
    """Save an episode for later. Idempotent, and the whole of the action.

    It used to answer with the offline shelf's capacity, because saving
    raised a popup asking whether to download the episode too. There is no
    download and no popup: pressing save saves, and the icon turns green.
    """
    _read_limit(request)
    user = _require_account(request)
    try:
        item = SAVED.save(user, req.query, req.minutes, title=req.title,
                          source=req.source, folder_id=req.folder_id)
    except saved_mod.SavedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # "Keep this for me" is taste, and the shelf was the only place in the app
    # holding it. Saving only: un-saving is not a negative signal, it is a
    # shelf being tidied, and reading it as a skip would punish the listeners
    # who use the feature most.
    EVENTS.record(topics_mod.Event(
        user, "save", "", req.query, topics_mod.tags_for_text(req.query)))
    return {"ok": True, "saved": True, "item": item.as_dict()}


@app.delete("/api/saved")
async def saved_unsave(request: Request,
                       q: str = Query(..., max_length=saved_mod.MAX_QUERY),
                       minutes: int = Query(0, ge=0, le=60)) -> dict:
    """Un-press the save control, which knows the episode and not a row id."""
    _read_limit(request)
    user = _require_account(request)
    return {"ok": SAVED.unsave(user, q, minutes), "saved": False}


@app.delete("/api/saved/{item_id}")
async def saved_remove(item_id: str, request: Request) -> dict:
    _read_limit(request)
    return {"ok": SAVED.remove(_require_account(request), item_id)}


@app.post("/api/saved/{item_id}/played")
async def saved_played(item_id: str, request: Request) -> dict:
    """Note that a saved episode was played, so the shelf can order itself by
    what somebody actually comes back to."""
    _read_limit(request)
    SAVED.played(_require_account(request), item_id)
    return {"ok": True}


@app.post("/api/saved/{item_id}/move")
async def saved_move(item_id: str, req: MoveRequest, request: Request) -> dict:
    _read_limit(request)
    try:
        item = SAVED.move(_require_account(request), item_id, req.folder_id)
    except saved_mod.SavedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="No such saved episode.")
    return {"ok": True, "item": item.as_dict()}


@app.post("/api/saved/folders")
async def saved_folder_create(req: FolderRequest, request: Request) -> dict:
    _read_limit(request)
    try:
        return {"ok": True,
                "folder": SAVED.create_folder(_require_account(request), req.name)}
    except saved_mod.SavedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/saved/folders/{folder_id}")
async def saved_folder_rename(folder_id: str, req: FolderRequest,
                              request: Request) -> dict:
    _read_limit(request)
    try:
        return {"ok": True, "folder": SAVED.rename_folder(
            _require_account(request), folder_id, req.name)}
    except saved_mod.SavedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/saved/folders/{folder_id}")
async def saved_folder_delete(folder_id: str, request: Request) -> dict:
    """Remove a folder. Its episodes are unfiled, never deleted - see
    `SavedStore.delete_folder` for why that is the only safe direction."""
    _read_limit(request)
    return {"ok": True,
            "unfiled": SAVED.delete_folder(_require_account(request), folder_id)}


# --- sharing outside FAM --------------------------------------------------

#: Hosts a share link must never be built from, because nobody else can reach
#: them. A link to `localhost` is worse than a relative one: it looks like a
#: URL, so it gets posted, and it resolves on the recipient's own machine to
#: whatever they happen to be running.
_PRIVATE_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1")

#: What a host is allowed to look like: a hostname or an IP, optionally with a
#: port, or a bracketed IPv6 literal.
#:
#: The `Host` header is client-supplied, and although a link built from it is
#: only ever handed back to the caller that sent it, one of the places it lands
#: is the `og:url` of `/s/<id>` - a server-rendered page served with
#: `Cache-Control: public`. `html.escape` already stops that becoming markup;
#: this stops it becoming a *different URL*. `evil.com/path?` and
#: `good.com@evil.com` are both legal header values and neither is a host.
#:
#: Refusing rather than sanitising, because a host this server does not
#: recognise is one it should not be naming in a link at all - the same answer
#: `_PRIVATE_HOSTS` gives, for the same reason.
_HOST_SHAPE = re.compile(r"^(?:[A-Za-z0-9._-]+|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?$")


def _public_base(request: Request | None = None) -> str:
    """Where this server is reachable from the internet, or "".

    **`PUBLIC_BASE_URL` first, and the request second.** This used to be the
    variable and nothing else, and that is why sharing did not work: on a
    deployment where nobody had set it - which is every deployment, since
    nothing prompts for it - `/api/share` handed back `/s/abc123`. That is a
    correct relative URL and a useless thing to send somebody. Pasted into a
    message it is not a link at all; pasted into LinkedIn it resolves against
    linkedin.com. The share feature was complete apart from the one part that
    leaves the machine.

    Deriving it from the request is `/api/health`'s own rule applied here:
    measure the thing rather than reading a setting that describes it. A
    request arrived, so this server has an address that at least one client
    outside it could reach, and that address is in the request. Behind
    Render's router the scheme is in `X-Forwarded-Proto` - the connection
    itself is plain HTTP - so a link built from `request.url` alone would be
    `http://` on an HTTPS deployment and get upgraded or blocked.

    The variable still wins when it is set, and it is still worth setting:
    it is the only way to name a host this server is *not* reached at, which
    is what a custom domain in front of a Render URL is. What changes is that
    not setting it is no longer a silently broken share.

    **The client-supplied `Host` header is used deliberately and narrowly.**
    It decides nothing but the text of a link handed back to the same caller
    that sent it: there is no trust decision here, no email, no redirect, and
    no cached response keyed on it. What it cannot be allowed to do is name a
    host that is nobody's - so a loopback or wildcard address is refused and
    reported as not public, which is exactly what a laptop is.
    """
    if settings.public_base_url:
        return settings.public_base_url
    if request is None:
        return ""
    headers = request.headers
    # First value only: a forwarded header accumulates one entry per hop, and
    # the client's own is the first.
    proto = (headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
             or request.url.scheme or "https")
    host = (headers.get("x-forwarded-host", "").split(",")[0].strip()
            or headers.get("host", "").strip()
            or request.url.netloc)
    if proto not in ("http", "https") or not host:
        return ""
    if not _HOST_SHAPE.match(host):
        return ""
    bare = host.rsplit(":", 1)[0].lower() if not host.startswith("[") else host
    bare = bare.split("]")[0].lstrip("[") if bare.startswith("[") else bare
    if bare in _PRIVATE_HOSTS or bare.endswith(".local"):
        return ""
    return f"{proto}://{host}"


def _share_url(share_id: str, request: Request | None = None) -> tuple[str, bool]:
    """The link, and whether it names a host anybody else can reach.

    Both, because a relative link is still useful inside the app and is a
    broken promise on LinkedIn. The caller decides what to do with that; what
    it must not do is invent `localhost`.
    """
    base = _public_base(request)
    return (f"{base}/s/{share_id}" if base else f"/s/{share_id}"), bool(base)


@app.get("/api/share/targets")
async def share_targets(request: Request) -> dict:
    """Where an episode can be sent, and what each destination can carry.

    `needs_image` is the one that changes what the client does: a story is a
    picture with a link attached, not a sentence with a URL in it, so those
    destinations take the card from `/api/share/card` instead of the text.
    """
    _read_limit(request)
    return {"targets": [
        {"key": t.key, "label": t.label, "kind": t.kind,
         "needs_image": t.needs_image, "max_chars": t.max_chars,
         # Whether this destination has a URL to open at all. The rendered
         # one, with the wording in it, comes back from `/api/share`; this
         # only says which kind of hand-off a client should be ready for.
         "has_destination": bool(t.destination)}
        for t in sharing.TARGETS]}


@app.post("/api/share")
async def share_create(req: ShareRequest, request: Request) -> dict:
    """Make a share link and the words to send with it, per destination.

    Deliberately reachable without an account: a share link is the cheapest
    route FAM has to a listener who does not have it yet, and putting a sign-up
    in front of the act of recommending it would be a strange way to grow.
    Everything *kept* still needs an account - which is the settled boundary,
    and a share is an outbound act rather than a shelf.

    Nothing is posted anywhere. FAM holds no token for any of these platforms
    and asks for none; the phone's share sheet and the platforms' own apps do
    the posting, with the person looking at it.
    """
    _read_limit(request)
    user = _listener(request)
    try:
        share = SHARES.create(user, req.query, req.minutes, req.title)
    except sharing.ShareError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    url, public = _share_url(share["id"], request)
    rendered = {t.key: sharing.render(
        t.key, title=share["title"], question=share["query"],
        minutes=share["minutes"], url=url) for t in sharing.TARGETS}
    return {
        "share": share, "url": url,
        # Said plainly rather than left to be discovered: without
        # PUBLIC_BASE_URL this link works inside the app and nowhere else.
        "public": public,
        # Absolute where this server knows its own address, because a native
        # client is not on this origin and a relative path means nothing to
        # it. The web app fetches either happily.
        "card": _card_url(share["id"], request)
                or f"/api/share/card?share={quote(share['id'])}",
        "targets": rendered,
    }


@app.get("/api/share/card")
async def share_card(request: Request,
                     share: str = Query(..., max_length=64)) -> Response:
    """The story image, as SVG.

    Instagram and Snapchat stories cannot carry a link as text - they are
    pictures with a sticker on them - so without this the listener shares a
    screenshot of a player UI, which is not an invitation to anything.
    """
    _read_limit(request)
    record = SHARES.get(share)
    if not record:
        raise HTTPException(status_code=404, detail="No such share.")
    person = SOCIAL.person(record["user_id"]) if record["user_id"] else {}
    svg = sharing.story_card(record["title"], record["query"],
                             record["minutes"], person.get("handle") or "")
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/share/{share_id}")
async def share_read(share_id: str, request: Request) -> dict:
    """One share, for anybody holding the link.

    The API behind the landing page, and it exists separately from the page
    for IOS_APP.md's first rule: every feature is an API before it is a
    screen. A universal link opening the app has to resolve the same share the
    web page resolves, and neither client should be reading the other's HTML
    to do it.

    Unauthenticated on purpose - a share link is public by construction, which
    is the whole point of sending one. What it returns is therefore exactly
    `sharing.landing_payload`, which carries no `user_id`: authorship is
    provenance and never identity (PROBLEMS.md 95), and this is the one
    response where the listener id sits right next to the data being handed
    to a stranger.
    """
    _read_limit(request)
    record = SHARES.get(share_id)
    if not record:
        raise HTTPException(status_code=404, detail="No such share.")
    return sharing.landing_payload(
        record,
        url=_share_url(share_id, request)[0],
        card_url=_card_url(share_id, request),
        app_store=settings.app_store_url,
    )


@app.post("/api/share/{share_id}/open")
async def share_opened(share_id: str, request: Request) -> dict:
    """Somebody actually looked at a shared episode.

    Separate from serving the page, and that is the whole design. Facebook and
    LinkedIn *fetch* a shared link to build their preview card, so counting
    the HTML serve would produce a number made mostly of crawlers - and the
    open count is the only number sharing produces, so a wrong one is worse
    than none. Crawlers do not run the page's script; this is what the page
    calls once it is running in front of a person.

    The alternative - a list of crawler user agents - is the shape PROBLEMS.md
    76 settled against: it can always be widened by one more entry, and the
    next one it misses is already written.
    """
    _read_limit(request)
    record = SHARES.get(share_id)
    if not record:
        raise HTTPException(status_code=404, detail="No such share.")
    SHARES.opened(share_id)
    return {"ok": True}


def _card_url(share_id: str, request: Request | None = None) -> str:
    """The story card, absolute where there is a host to make it absolute.

    Open Graph images are fetched by a crawler on somebody else's server, so a
    relative one is no image at all. Returning "" rather than a relative path
    is what stops `landing_head` advertising a picture that never loads - the
    same refusal `destination_for` already makes about the link itself.
    """
    base = _public_base(request)
    return f"{base}/api/share/card?share={quote(share_id)}" if base else ""


#: Read once. The landing page is one small file and re-reading it per request
#: would be a disk hit in front of a stranger's first second of FAM - which is
#: the second this product cares about most.
_LANDING_TEMPLATE: str | None = None


def _landing_template() -> str:
    global _LANDING_TEMPLATE
    if _LANDING_TEMPLATE is None:
        _LANDING_TEMPLATE = (PROJECT_ROOT / "static" / "listen.html").read_text(
            encoding="utf-8")
    return _LANDING_TEMPLATE


@app.get("/s/{share_id}")
async def share_open(share_id: str, request: Request):
    """Where a shared link lands: one episode, and no way into the rest.

    **This used to redirect into the web app**, which handed a stranger the
    whole product - search, myFAM, Explore, an account - when what they were
    sent was one episode. The landing page is the opposite: the only control
    that works is play, and everything else is a door to the App Store.

    The episode is resolved with no new concept at all. A share row holds the
    question and the length, `pipeline.key_for` builds the cache key from
    exactly those, so the page asking `/api/audio` for them gets the sharer's
    own script back out of the shared cache. There is no episode id in this
    product and this did not add one; see the note in `sharing.py`.

    A dead link still lands here rather than redirecting, because a page that
    says the link has expired is a better answer than the front door of an app
    the person did not ask for - and because a crawler following a stale
    preview should get markup, not a bounce.

    The open is not counted here. See `share_opened`.
    """
    record = SHARES.get(share_id)
    payload = sharing.landing_payload(
        record or {}, url=_share_url(share_id, request)[0],
        card_url=_card_url(share_id, request) if record else "",
        app_store=settings.app_store_url,
    )
    return HTMLResponse(
        content=sharing.render_landing(_landing_template(), payload),
        status_code=200 if record else 404,
        # A share is one episode's worth of fixed text. Cacheable, but briefly:
        # the title can be re-written when an episode is regenerated.
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/api/entitlements")
async def entitlements_read(request: Request) -> dict:
    """What this listener may do, and how much of it is left.

    Works without an account, because an anonymous listener is on a tier too
    and needs to be told what it allows - a limit nobody can see coming is
    indistinguishable from a bug when it arrives.
    """
    _read_limit(request)
    user = _listener(request)
    tier_name = _tier(request)
    return {
        "enforced": quotas.settings_enforcing(),
        **entitlements.describe(tier_name),
        "usage": _quota_snapshot(user, tier_name),
    }


@app.get("/api/plans")
async def plans_read(request: Request) -> dict:
    """Every tier and every feature, for a pricing screen.

    Deliberately has no prices in it. A number here and a number in App Store
    Connect are two places for one fact, and the one that is wrong is always
    the one the listener is reading - so the price comes from the store's own
    product metadata, which is also the only place it can be right per country.
    """
    _read_limit(request)
    return {**entitlements.catalogue(), "current": _tier(request)}


@app.get("/api/voices")
async def voices() -> dict:
    """Voices this server can speak in, best first."""
    return {
        "default": default_voice(),
        "store": VOICE_STORE["dir"],
        "voices": [v.as_dict() for v in list_voices()],
    }


@app.post("/api/script")
async def script(req: ScriptRequest, request: Request) -> dict:
    _rate_limit(request)
    # A script is a Claude call, which is the expensive half of an episode.
    # Counted against the same allowance rather than a second one: from the
    # allowance's point of view this *is* an episode, minus the audio.
    # **There is one failure path here now** (§109): FAM refuses a question
    # that turns on current facts when every retriever came back empty, so
    # this endpoint refunds on that exactly as `/api/audio` does. The comment
    # this replaces said there was no failure path between the reservation
    # and the response, which was true when it was written and silently
    # stopped being true.
    reserved = _reserve(request, "episode", surface="script")
    plan = _validated_plan(req.query, req.minutes, "", req.search)
    generator = DemoGenerator() if DEMO_MODE else ScriptGenerator()
    notes = ScriptNotes()
    try:
        text = " ".join([s async for s in generator.stream_sentences(plan, notes)])
    except NoEvidence as exc:
        log.warning("refused a script for lack of evidence: %s", exc)
        _record_usage(_listener(request), notes.usage, surface="script",
                      minutes=plan.minutes)
        _refund(reserved, _listener(request))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    # No audio, but a full script call - the same money as an episode, minus
    # the synthesis. Left out, a tool or a probe hammering this endpoint would
    # be the one kind of spend the ledger could not see.
    _record_usage(_listener(request), notes.usage, surface="script",
                  minutes=plan.minutes)
    return {
        "query": plan.query,
        "minutes": plan.minutes,
        "word_budget": plan.word_budget,
        "words": len(text.split()),
        "script": text,
        "thread": notes.thread,
    }


# Each store resolves its own path (env var, else the project root), so the
# mapping from variable to file lives in one place per store rather than
# being restated here.
# Browser origins allowed to call this server. Off unless configured, and
# deliberately not a wildcard: these requests carry the session cookie, and a
# browser refuses `*` together with credentials - so a wildcard here would look
# permissive, not work, and hide the real fix behind a setting that appeared to
# be already correct.
#
# A native app is not a browser. It sends no Origin header and is not subject
# to the same-origin policy at all, so the iOS client needs nothing here; this
# exists only for a web client served from somewhere other than this server.
_ALLOWED_ORIGINS = [o.strip() for o in settings.api_origins.split(",") if o.strip()]
if _ALLOWED_ORIGINS:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        # So a browser client can read the quota verdict on a 429 rather than
        # only the status code.
        expose_headers=["X-FAM-Quota", "X-Sample-Rate", "X-Requested-Seconds"],
    )
    log.info("CORS enabled for %s", ", ".join(_ALLOWED_ORIGINS))


EVENTS = topics_mod.EventStore()
MIXES = mixes_mod.MixStore()
SOCIAL = social_mod.SocialStore()
ACCOUNTS = accounts_mod.AccountStore()
PREFS = prefs_mod.PreferenceStore()
METER = metering.MeterStore()
QUOTAS = quotas.QuotaStore()
MESSAGES = messages_mod.MessageStore()
SAVED = saved_mod.SavedStore()
SHARES = sharing.ShareStore()


@app.middleware("http")
async def carry_the_session(request: Request, call_next):
    """Resolve who is asking, from a cookie the client cannot forge.

    This is where the old hole is closed. The listener id used to arrive as
    `?user=` - chosen by the browser, checked by nobody - so anyone who guessed
    one could act as that listener. It is now read from an HttpOnly session
    cookie and, when there is no cookie, minted here with `secrets`.

    Minting rather than demanding a login is the point: an anonymous listener
    still gets a real, unforgeable identity, so search, myFAM and Go Deeper
    work exactly as before for someone who has never signed up. Signing up
    later attaches an email to the id they already have, which is why no data
    has to move.

    Only paths that need identity mint one, so a monitoring poll on
    /api/health does not accumulate a session row per request. A voice worker's
    heartbeat is the same case and was nearly the worse version of it: it
    carries an `Authorization: Bearer` that is a *registration* token rather
    than a session, so without this it would mint a listener every sixty
    seconds, for ever, and each one would look like a person in the accounts
    store.
    """
    path = request.url.path
    wants_identity = path == "/" or (
        path.startswith("/api/") and path not in MACHINE_PATHS
    )
    token = _session_token(request)
    listener = ACCOUNTS.listener_for(token) if token else None
    minted = ""
    if listener is None and wants_identity:
        try:
            minted, user_id = ACCOUNTS.new_session()
            listener = accounts_mod.Listener(user_id)
        except Exception:
            # Never fail a request because a session could not be written; the
            # listener is simply anonymous-and-unrecorded for this one.
            log.exception("could not mint a session; continuing without one")
    request.state.listener = listener

    response = await call_next(request)

    # An endpoint that changes who you are (log in, log out, sign up) says so
    # here rather than building its own response.
    new_token = getattr(request.state, "set_session", None)
    if new_token is not None:
        if new_token:
            _set_session_cookie(response, request, new_token)
        else:
            response.delete_cookie(accounts_mod.COOKIE_NAME, path="/")
    elif minted:
        _set_session_cookie(response, request, minted)
    return response


#: Endpoints answered for a machine rather than for a listener, so no session
#: is minted for them. `/api/health` is a monitor's poll; `/api/voice/register`
#: is a GPU saying where it is. Both are called on a timer for ever, and a
#: session row per call is a store full of listeners who are not people.
MACHINE_PATHS = ("/api/health", "/api/voice/register")


#: The public API's version. Every endpoint is reachable at `/api/v1/...` as
#: well as at `/api/...`, and the prefixed form is the one a shipped app must
#: use. The reason is the whole reason a version exists: an app on somebody's
#: phone cannot be redeployed with the server, so the day an endpoint has to
#: change shape, `/api/v2` can carry the new one while `/api/v1` keeps the
#: promise made to every phone already out there. Without a prefix that day
#: forces a choice between breaking installed apps and never changing the API.
API_VERSION = "v1"
API_PREFIX = f"/api/{API_VERSION}"


@app.middleware("http")
async def version_prefix(request: Request, call_next):
    """Serve `/api/v1/x` from the same handler as `/api/x`.

    A rewrite rather than a second set of routes: two registrations of one
    endpoint is two places for a decorator to drift, and the failure would be
    a native client quietly getting different behaviour from the web one.

    Declared after `carry_the_session` so it wraps it and therefore runs
    *first* - the session middleware decides what to do from the path, and it
    has to see the real one.
    """
    path = request.scope.get("path", "")
    if path.startswith(API_PREFIX + "/") or path == API_PREFIX:
        request.scope["path"] = "/api" + path[len(API_PREFIX):]
    return await call_next(request)


def _session_token(request: Request) -> str:
    """The session token, from the cookie or from an Authorization header.

    Two carriers, one session model. The web client uses the HttpOnly cookie
    and cannot read it, which is what makes an XSS unable to walk off with
    somebody's identity. A native app has no cookie jar worth relying on -
    iOS clears `HTTPCookieStorage` under conditions the app does not control,
    and "the listener silently became a different listener" is the worst
    failure available to a product whose personalisation is an append-only log
    keyed on that id - so it holds the same token in the Keychain and sends it
    as `Authorization: Bearer`.

    The settled rule is untouched: **the id still never comes from the
    client.** A bearer token is the same server-minted, high-entropy,
    revocable, never-stored-in-the-clear string the cookie carries. What
    changes is the envelope, not the trust.

    The cookie wins when both are present. A browser attaches its cookie
    automatically, so a header alongside it is either a mistake or somebody
    testing whether one overrides the other; the answer is no.
    """
    cookie = request.cookies.get(accounts_mod.COOKIE_NAME, "")
    if cookie:
        return cookie
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return ""


def _set_session_cookie(response, request: Request, token: str) -> None:
    """HttpOnly so page scripts cannot read it, which is what makes an XSS
    unable to walk off with someone's identity. Secure only over https, or the
    cookie would be dropped on the http://<lan-ip> address a phone uses."""
    response.set_cookie(
        accounts_mod.COOKIE_NAME,
        token,
        max_age=accounts_mod.SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )


def _surface(cached_only: bool, topic_id: str, context: str) -> str:
    """Which part of the app spent this money.

    Derived rather than passed, because the interface already says it in the
    parameters it sends. It matters for pricing: Explore replays and never
    writes a script, so an Explore-heavy listener costs a fraction of a
    search-heavy one, and a single blended per-listener number hides that.
    """
    if cached_only:
        return "explore"
    if topic_id:
        return "myfam"
    if context:
        return "godeeper"
    return "search"


def _record_usage(user: str, usage: metering.Usage, *, surface: str,
                  minutes: int = 0, audio_seconds: float = 0.0,
                  cache_hit: bool = False) -> None:
    """Append this episode to the ledger. Never fails the request.

    Called after the listener already has their audio. A metering failure that
    became a failed episode would trade a gap in the billing record - which is
    recoverable, and which `MeterStore.record` logs loudly - for a broken
    product, which is not.
    """
    usage.audio_seconds = audio_seconds
    usage.cache_hit = cache_hit
    try:
        METER.record(user, usage, plan=ACCOUNTS.plan_for(user),
                     surface=surface, minutes=minutes)
    except Exception:  # noqa: BLE001 - the episode already played
        log.exception("could not meter an episode for %r", user)


def _listener(request: Request) -> str:
    """The id every store keys on, or "" when there is no session."""
    listener = getattr(request.state, "listener", None)
    return listener.user_id if listener else ""


def _require_listener(request: Request) -> str:
    user = _listener(request)
    if not user:
        raise HTTPException(status_code=503, detail="Could not start a session.")
    return user


#: What "Skip for now" costs, in one place. Playback is never gated: search,
#: myFAM, DailyFAM's episodes, Explore and Go Deeper all work with no account,
#: because a login in front of the first word breaks the one-sentence spec and
#: that mistake has already been avoided once here (see accounts.py).
#:
#: What *is* gated is everything the server keeps for you long-term - saved
#: mixes, chosen interests and language, Save for Later - on the product
#: decision that durable per-listener storage is what an account is for.
#:
#: **The interaction log is in that set now too** (PROBLEMS.md §127, at the
#: owner's direction, reversing what this comment used to say). It was left
#: outside on the argument that gating it would mean an anonymous feed could
#: never be ranked - true, and the owner's answer is that it should not be:
#: everything the algorithm learns about somebody belongs to their *account*,
#: and a guest session is a device, not a person. So a guest is never written
#: into the log at all (`_remembers`), a guest's feed is the startup set every
#: time, and signing up starts the account's history rather than adopting
#: whatever a borrowed phone had been doing. Listening itself is untouched.
ACCOUNT_REQUIRED = ("You need an account for this. Listening never needs one; "
                    "an account is what FAM remembers you by.")


def _remembers(request: Request) -> bool:
    """Whether anything this listener does may be written into the event log.

    One predicate for every write site, so "only accounts are remembered" is
    one rule rather than eleven `if` statements that drift. Plays, impressions,
    events from the client and the categories grown from them all read the
    log, so gating the writes is gating all of it. `_has_account` is the
    definition of an account, and this is only its name at the write sites.
    """
    return _has_account(request)


def _require_account(request: Request) -> str:
    """The listener id, but only if credentials are attached to it.

    401 rather than 403: the listener genuinely has an identity, it just has
    nothing proving it is theirs on another device. The interface reads the
    status and offers signup rather than printing a failure.
    """
    listener = getattr(request.state, "listener", None)
    if listener is None or not listener.user_id:
        raise HTTPException(status_code=503, detail="Could not start a session.")
    if not listener.is_authenticated:
        raise HTTPException(status_code=401, detail=ACCOUNT_REQUIRED)
    return listener.user_id


class MixRequest(BaseModel):
    # No `user` field: identity comes from the session cookie, never the body.
    name: Optional[str] = Field(None, max_length=mixes_mod.MAX_NAME)
    #: Bank ids as strings, or {"query": "..."} for a topic the listener typed.
    #: Validated in mixes.clean_items rather than here, so one place owns the
    #: rules and the message the listener sees.
    topic_ids: Optional[list[Union[str, dict]]] = None
    #: Public mixes appear on the listener's profile.
    public: Optional[bool] = None
    #: The mix's cover photo as a data URL; "" removes it, omitted keeps it.
    #: Checked in mixes.clean_cover, which says why it is refused.
    cover: Optional[str] = Field(None, max_length=mixes_mod.MAX_COVER_CHARS)


def _attachments_for(user: str, ids: str) -> tuple:
    """Resolve `attach=` into stored attachments, or say which one is gone.

    Silently dropping an expired attachment would produce an episode about a
    document the listener believes was read and was not - the exact failure
    this project treats as worse than an error.
    """
    wanted = [i for i in (ids or "").split(",") if i.strip()]
    if not wanted:
        return ()
    found = ATTACHMENTS.resolve(user, wanted)
    if len(found) != len(wanted):
        raise HTTPException(
            status_code=410,
            detail="An attachment has expired. Add it again and re-run the search.",
        )
    return tuple(found)


class AttachRequest(BaseModel):
    kind: str = Field(..., pattern="^(document|image|link)$")
    name: str = Field("", max_length=300)
    data: str = Field("", description="Base64 file contents; documents and photos")
    url: str = Field("", max_length=2000)


@app.post("/api/attach")
async def attach(req: AttachRequest, request: Request) -> dict:
    """Extract a document, photo or link once, when it is added.

    Deliberately not on the generation path: reading a PDF or fetching a page
    is a round-trip, and the one thing this product will not spend is seconds
    in front of the first word. Doing it here puts the cost while someone is
    still typing.

    **Only a link is paced as a spend.** A document or a photo is read locally
    and costs no model call and no outbound request, so it takes the reader's
    limit - the rule this file already applies to `/api/audio`, which asks the
    cache before pacing at all. Pacing them as generations meant attaching two
    files in a row - which the interface invites, since a search can carry
    several - answered the second one with "Slow down a moment", from a button
    that had just asked for it. A limiter that fires on correct use is not
    protecting anything.

    A link is different and stays paced: it is an outbound fetch of whatever
    address was typed, which is the one thing here somebody else pays for.
    """
    if req.kind == "link":
        _rate_limit(request)
    else:
        _read_limit(request)
    try:
        item = attachments_mod.build(req.kind, req.name, req.data, req.url)
    except attachments_mod.AttachmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ATTACHMENTS.put(_listener(request), item)
    return item.as_dict()


@app.delete("/api/attach")
async def detach(request: Request, id: str = Query(..., max_length=64)) -> dict:
    _read_limit(request)
    return {"ok": ATTACHMENTS.delete(_listener(request), id)}


@app.get("/api/topics")
async def bank(request: Request, ranked: bool = Query(False),
               interests: str = Query("", max_length=200)):
    """The whole shared bank, for the mix topic picker.

    `ranked` answers the question the picker's own heading was already
    claiming: it said "Suggested topics" over the bank in `topic.id` order,
    which is alphabetical by slug and suggests nothing. Ranked, it is the same
    ranker every personal rail on myFAM uses - `rank_from_history` over
    `taste`, plus the intro's chosen interests for somebody with no history.

    **A sort, never a filter.** The whole bank comes back either way, in a
    different order. A picker that hid what it did not rank would be a picker
    somebody could not find a topic in, and unlike a rail there is no
    "somewhere else to look" - this *is* the list. It also does not exclude
    what they have played, which a rail does: wanting a mix of subjects you
    already like is the entire point of a mix.
    """
    _read_limit(request)
    if not ranked:
        return {"topics": [t.as_dict() for t in topics_mod.TOPIC_BANK]}
    user = _listener(request)
    chosen = _interests_for(request, interests)
    profile = topics_mod.taste(EVENTS.for_user(user) if user else [],
                               interests=chosen)
    order = topics_mod.rank_bank(profile)
    return {"topics": [t.as_dict() for t in order],
            "personalised": bool(profile)}


@app.get("/api/mixes")
async def list_mixes(request: Request):
    """This listener's DailyFAM mixes, each with its topics resolved.

    Account-gated along with the rest of /api/mixes: a mix is a thing the
    listener made and expects to find again, which is the definition this app
    uses for "needs an account". See ACCOUNT_REQUIRED.
    """
    _read_limit(request)
    user = _require_account(request)
    return {
        "mixes": [m.as_dict() for m in MIXES.list_for_user(user)],
        "starters": [
            {"name": name, "topic_ids": list(ids)}
            for name, ids in mixes_mod.STARTER_MIXES
        ],
    }


@app.post("/api/mixes")
async def create_mix(req: MixRequest, request: Request):
    _read_limit(request)
    try:
        mix = MIXES.create(_require_account(request), req.name or "", req.topic_ids or [],
                           req.cover or "")
    except mixes_mod.MixError as exc:
        # Phrased for the listener: these are things they did, not faults.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return mix.as_dict()


@app.patch("/api/mixes/{mix_id}")
async def update_mix(mix_id: str, req: MixRequest, request: Request):
    _read_limit(request)
    account = _require_account(request)
    try:
        mix = MIXES.update(account, mix_id, req.name, req.topic_ids, req.public,
                           req.cover)
    except mixes_mod.MixError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return mix.as_dict()


@app.delete("/api/mixes/{mix_id}")
async def delete_mix(mix_id: str, request: Request):
    _read_limit(request)
    if not MIXES.delete(_require_account(request), mix_id):
        raise HTTPException(status_code=404, detail="That mix no longer exists.")
    return {"ok": True}


class PreferenceRequest(BaseModel):
    """Every field optional: the intro saves one page at a time, and a
    settings row writes one flag from a screen that knows nothing about the
    rest."""

    # No `user` field, for the same reason MixRequest has none.
    interests: Optional[list[str]] = None
    language: Optional[str] = Field(None, max_length=8)
    #: Which of their interests they have chosen *not* to show on their
    #: profile. The hidden set rather than the shared one - see
    #: `preferences.Preferences.hidden_interests` for why that direction.
    hidden_interests: Optional[list[str]] = None
    #: The named subjects chosen from the catalogue, plus anything typed there
    #: that was not on it. Capped at the model boundary as well as in
    #: `preferences.clean_topics`, because an oversized body should be refused
    #: before a database round trip rather than after one.
    topics: Optional[list[str]] = Field(None, max_length=prefs_mod.MAX_TOPICS)
    #: Which interests to draw on their profile, at most four. An empty list
    #: is a real answer and means "choose for me" - see
    #: `preferences.clean_profile_interests` - so this is `None` when the
    #: caller is not writing it and `[]` when they are clearing it.
    profile_interests: Optional[list[str]] = Field(
        None, max_length=prefs_mod.PROFILE_INTERESTS_MAX)
    #: Where they say they are. Three fields rather than one string, because
    #: the ranker reads city and region and deliberately ignores country -
    #: see `preferences.Location.words` - and because a single "Cincinnati,
    #: Ohio, United States" would have to be split back apart on a comma by
    #: whoever needed the parts.
    #:
    #: Free text and validated against nothing, which is deliberate: there is
    #: no list of the world's towns that is both complete and short enough to
    #: ship, and a field that refuses somebody's home town is worse than one
    #: that accepts a typo. Length is capped here as well as in
    #: `preferences.clean_place`, so an oversized body is refused before a
    #: database round trip rather than after one.
    city: Optional[str] = Field(None, max_length=prefs_mod.MAX_PLACE)
    region: Optional[str] = Field(None, max_length=prefs_mod.MAX_PLACE)
    country: Optional[str] = Field(None, max_length=prefs_mod.MAX_PLACE)
    #: Written by nothing in the interface any more. The weekly recap popup is
    #: gone, replaced by myFAM's "What you missed last week" rail, and the
    #: column stays for the same reason `language` does: dropping it is a
    #: migration with no benefit, and it is what a scheduled digest would read
    #: on the day one exists.
    weekly_recap: Optional[bool] = None
    intro_done: Optional[bool] = None


def _interests_for(request: Request, given: str = "") -> tuple[str, ...]:
    """Chosen facets for ranking: stored ones for an account, the query string
    for anyone else.

    Taking these off the request for an anonymous listener is not what "a
    listener id is never accepted from the client" forbids. An id is an
    identity and grants access to somebody's data; this is a ranking hint,
    validated against a fixed eight-word vocabulary, used for one response and
    never written down. An anonymous listener's intro answers live in their own
    browser and nowhere else, so this is the only route by which the ranker can
    honour them at all - and honouring them is the entire reason the intro asks.
    """
    listener = getattr(request.state, "listener", None)
    if listener is not None and listener.is_authenticated:
        return PREFS.get(listener.user_id).interests
    try:
        return prefs_mod.clean_interests(given.split(","))
    except prefs_mod.PreferenceError:
        # A malformed hint costs one less-personal feed. It must never be what
        # stops the page loading.
        return ()


def _place_for(request: Request) -> "prefs_mod.Location":
    """Where this listener says they are, for ranking.

    Account only, and unlike `_interests_for` there is **no query-string
    fallback**. The two look like the same kind of hint and are not: an
    interest is one of eight words from a closed vocabulary, used for one
    response and thrown away, where a location is free text that would let a
    caller ask "rank this feed as though I were in Cincinnati" - which is a
    request nobody's interface makes and a thing worth not accepting. An
    anonymous listener simply gets the feed they got before this existed.
    """
    listener = getattr(request.state, "listener", None)
    if listener is not None and listener.is_authenticated:
        return PREFS.get(listener.user_id).location
    return prefs_mod.Location()


def _country_for(request: Request, place: "prefs_mod.Location") -> str:
    """The country Trending ranks for (§134) - the one listener input it takes.

    The country somebody stored, when they have. Otherwise the region of the
    browser's own language setting - `en-US`, `en-GB` - which every browser
    sends and which is the only honest hint there is about a guest. It is not
    the location `_place_for` refuses to take from a caller: it moves a story
    up the world row by the share of its coverage that comes from there and
    touches nothing else, so the worst a wrong one can do is reorder four
    stories that were all trending anyway.
    """
    if place.country:
        return place.country
    return _region_of(request.headers.get("accept-language", ""))


def _region_of(header: str) -> str:
    """The region in the first language tag of an Accept-Language header.

    BCP 47: language, then an optional four-letter script, then a region of
    two letters or three digits, then variants and extensions. Only a
    two-letter region names a country (`es-419` is Latin America, which is
    not one), so that is the only thing taken: `zh-Hant-TW` is "TW",
    `en-US-u-ca-gregory` is "US", `en_US` is "US", and `en` or `*` is "".
    """
    first = (header or "").split(",", 1)[0].split(";", 1)[0].strip()
    parts = first.replace("_", "-").split("-")[1:]
    for part in parts:
        if len(part) == 1:          # an extension singleton: the region is past
            break
        if len(part) == 2 and part.isalpha():
            return part.upper()
    return ""


def _has_account(request: Request) -> bool:
    """Whether credentials are attached to this listener.

    The one thing the browse rankers need from `accounts`, reduced to a bool
    at the request boundary so `topics.py` stays a pure query over the event
    log - the same arrangement `circle` and `place` already have.

    It is `is_authenticated` rather than "signed in": a listener with an
    account is one whichever route they took and whether or not the cookie
    came back on this request, which is exactly the question
    `topics.browse_inventory` is asking. Guests and brand-new sessions are
    False, and False is what puts the evergreen bank on their page.
    """
    listener = getattr(request.state, "listener", None)
    return bool(listener is not None and listener.is_authenticated)


@app.get("/api/preferences")
async def read_preferences(request: Request):
    """What is on offer, and what this listener chose.

    The *choices* are public - the intro is shown before anyone has an account,
    and a picker that cannot list its own options is no picker. What was chosen
    comes back only for an account, and `saved` says which of the two the
    caller is looking at, so the interface can tell the listener the truth
    about whether their answers are being kept.
    """
    _read_limit(request)
    listener = getattr(request.state, "listener", None)
    authed = bool(listener is not None and listener.is_authenticated)
    stored = (PREFS.get(listener.user_id) if authed
              else prefs_mod.Preferences(_listener(request)))
    # The six the picker shows, most played across FAM first. `interests_all`
    # is still every facet, because the picker narrowing is a screen decision
    # and the eight remain the whole pickable vocabulary - anything that
    # *reads* a stored interest (Settings, the catalogue) needs every label.
    picker, picker_source = topics_mod.popular_facets(EVENTS)
    # And the six the *Settings* wheel shows, which is a different question
    # asked by a different person. The first run asks somebody with no history
    # what they like, so the honest answer is what everybody plays. Settings is
    # opened by somebody who has been using the app, where their own listening
    # is the better answer - and it keeps changing as they listen, which is
    # what makes that wheel worth opening twice.
    mine, mine_source = topics_mod.my_facets(
        EVENTS, _listener(request), stored.interests)
    body = {
        # `short` is what fits inside the first run's circles; `label` is what
        # everything that *reads* an interest back shows. A shortening, never a
        # second name - see `topics.TAG_SHORT`.
        "interests_available": [{"id": tag,
                                 "label": topics_mod.TAG_LABELS[tag],
                                 "short": topics_mod.TAG_SHORT[tag]}
                                for tag in picker],
        "interests_all": [{"id": tag, "label": label}
                          for tag, label in topics_mod.TAG_LABELS.items()],
        # "played" or "default". A deployment with an empty log is showing a
        # declared order rather than a measurement, and the two look identical
        # on screen - so it says which, here and on /api/health, rather than
        # letting anybody read a default as a popularity ranking.
        "interests_source": picker_source,
        "interests_yours": [{"id": tag,
                             "label": topics_mod.TAG_LABELS[tag],
                             "short": topics_mod.TAG_SHORT[tag]}
                            for tag in mine],
        # "listened", "chosen" or "default" - which of the three sources
        # actually decided the wheel. A listener with two plays still gets six
        # discs, because a wheel is six or it is a broken wheel, and this is
        # what stops the filler being read as a measurement.
        "interests_yours_source": mine_source,
        # The long list behind "View more". Named subjects rather than tags -
        # see `topics.INTEREST_CATALOGUE` for why that distinction is what
        # lets it be seventy-odd entries without widening the vocabulary the
        # ranker reasons in by a single word.
        "catalogue": [i.as_dict() for i in topics_mod.INTEREST_CATALOGUE],
        # The vocabulary a DailyFAM mix ranks catalogue subjects in: which
        # facet each subtag lives under, and what each facet is called. The
        # picker filters and orders subjects by the listener's interests and
        # a mix's recommendations by what it already follows, and both need
        # "ai" to count as Technology (§137).
        "tag_parent": dict(topics_mod.TAG_PARENT),
        "tag_labels": dict(topics_mod.TAG_LABELS),
        "languages": [dict(lang) for lang in prefs_mod.LANGUAGES],
        # False until per-language generation exists. Printed under the picker
        # rather than left implicit: a setting that silently changes nothing is
        # the failure mode this project has paid for most often.
        "language_active": prefs_mod.LANGUAGE_ACTIVE,
        # What they chose from the catalogue, resolved for display: a stored
        # entry is either a catalogue id or a word somebody typed, and a screen
        # needs the label either way. Resolved here rather than in the client
        # so the iOS app does not have to carry a copy of the catalogue to
        # render a pill - the same reason `interests_all` is served.
        "topics_chosen": [
            {"id": topic,
             "label": (topics_mod.CATALOGUE_BY_ID[topic].label
                       if topic in topics_mod.CATALOGUE_BY_ID else topic),
             "icon": (topics_mod.CATALOGUE_BY_ID[topic].icon
                      if topic in topics_mod.CATALOGUE_BY_ID else "news"),
             # True for something they typed rather than picked off the list.
             # Shown identically; carried because "this is not one of ours" is
             # worth being able to see when reading the data back.
             "typed": topic not in topics_mod.CATALOGUE_BY_ID}
            for topic in stored.topics
        ],
        "account": authed,
        "saved": authed,
        "account_required": ACCOUNT_REQUIRED,
    }
    body.update(stored.as_dict())
    return body


@app.post("/api/preferences")
async def write_preferences(req: PreferenceRequest, request: Request):
    """Store the intro's answers. Account only - see ACCOUNT_REQUIRED."""
    _read_limit(request)
    user = _require_account(request)
    try:
        prefs = PREFS.save(user, interests=req.interests, language=req.language,
                           hidden_interests=req.hidden_interests,
                           topics=req.topics,
                           profile_interests=req.profile_interests,
                           city=req.city, region=req.region,
                           country=req.country,
                           weekly_recap=req.weekly_recap, intro_done=req.intro_done)
    except prefs_mod.PreferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Saved topics are also *picks*, and the log is what the ranker reads.
    #
    # Both, rather than one: the stored list is what a screen draws - what to
    # show, what to remove, what the Settings wheel is a reflection of - and
    # the event is what `taste` scores. The list is a statement, the log is
    # behaviour, and `topics.py` is deliberately a pure query over behaviour.
    # Recording only the list would leave the ranker exactly as ignorant as it
    # was before anybody chose anything.
    if req.topics is not None:
        for topic in prefs.topics:
            EVENTS.record(topics_mod.Event(
                user, "pick", topic, "",
                topics_mod.tags_for_id(topic, topic)))
    return prefs.as_dict()


@app.get("/api/nextup")
async def next_up(
    request: Request,
    topic_id: str = Query("", max_length=64),
    q: str = Query("", max_length=300, description="What the finished episode asked"),
    interests: str = Query("", max_length=200),
):
    """The four tiles the post-episode popup offers.

    Costs no model call - it ranks the same inventory myFAM does, seeded with
    what just finished. See topics.rank_next_up for why this is the feed's
    ranker rather than a second one, and `topics.browse_inventory` for why
    "the same inventory" is a per-listener answer rather than a fixed bank.
    """
    _read_limit(request)
    user = _listener(request)
    picks = topics_mod.rank_next_up(
        EVENTS, user, after_id=topic_id, after_text=q,
        interests=_interests_for(request, interests),
        has_account=_has_account(request),
    )
    # Recorded on the same terms as a shelf: one tile, one listener, one
    # ranking version. Without it the popup would be the one surface whose
    # picks nobody could account for afterwards.
    if user and _remembers(request):
        EVENTS.record_impressions(user, [("next_up", t.id) for t in picks])
    return {"topics": [t.as_dict() for t in picks], "algo": EVENTS.algo_stamp()}


@app.get("/api/myfam/section")
async def myfam_section(request: Request,
                        key: str = Query(..., max_length=32),
                        minutes: int = Query(DEFAULT_MINUTES, ge=1, le=10),
                        interests: str = Query("", max_length=200)):
    """One myFAM rail, at full length, for the screen behind its "View more".

    Costs no model call and cannot cause one: this reorders the same fixed
    bank `build_feed` does. Which is the answer to "how do we fill a whole
    screen without making episodes nobody asked for" - the tiles were always
    there, the rail just showed six of them.

    Each tile also says whether its script is **already written**. That is the
    other half of the same answer: a cached tile costs a listener nothing but
    the audio, so the screen leads with those and says so. It is one local
    SQLite read per tile - the same probe the pacing path already does - and
    never a model call, so marking them is as free as ranking them.
    """
    _read_limit(request)
    user = _listener(request)
    written = _written_probe(minutes)
    try:
        place = _place_for(request)
        body = topics_mod.build_section(
            EVENTS, user, key, interests=_interests_for(request, interests),
            circle=SOCIAL.circle_of(user), written=written,
            written_at=_written_at_probe(minutes),
            place=place.words, place_name=place.label,
            has_account=_has_account(request),
            country=_country_for(request, place))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail="No such section.") from exc

    for topic in body["topics"]:
        topic["cached"] = written(topic.get("query", ""))
    # Trending's geography groups carry the same tiles; they are marked the
    # same way, and deliberately *not* re-sorted ready-first - within a place
    # the order is popularity, which is what the row is about (§135).
    for group in body.get("groups", ()):
        for topic in group["topics"]:
            topic["cached"] = written(topic.get("query", ""))
    # Ready ones first, each rail's own order preserved inside those two
    # groups. A listener on this screen is browsing, and an episode that
    # starts instantly is a better thing to put in front of them than one
    # three places higher that has to be written first.
    body["topics"].sort(key=lambda t: not t["cached"])
    body["ready"] = sum(1 for t in body["topics"] if t["cached"])
    body["minutes"] = minutes
    if user and _remembers(request):
        EVENTS.record_impressions(
            user, [(f"section:{key}", t["id"]) for t in body["topics"]])
    body["algo"] = EVENTS.algo_stamp()
    return body


def _written_probe(minutes: int):
    """A memoised `query -> already in the cache?` for one request.

    The rankers ask about every candidate they consider and the endpoint then
    asks again about the ones that survived, so the same handful of queries
    comes up several times on one page load. Each answer is a local SQLite
    read - cheap, and cheaper still done once. Memoised per request rather
    than globally, because "is this cached" is exactly the sort of answer that
    must not be allowed to go stale.
    """
    answers: dict[str, bool] = {}

    def probe(query: str) -> bool:
        key = query or ""
        if key not in answers:
            answers[key] = _topic_is_written(key, minutes)
        return answers[key]

    return probe


def _written_at_probe(minutes: int):
    """A memoised `query -> when its live script was written, or None`, or
    None when this cache cannot say.

    What myFAM's no-repeats check reads (`topics.is_repeat`) to tell the
    episode a listener heard from one written since. None rather than a probe
    on a backend without `written_at`, because a probe answering None means
    "no script, a tap writes a new one" - and an unknown must not look fresh.
    A probe that cannot ask raises for the same reason, and the ranker counts
    that tile as heard.
    """
    reader = getattr(SCRIPT_CACHE, "written_at", None)
    if reader is None:
        return None
    answers: dict[str, Optional[float]] = {}

    def probe(query: str) -> Optional[float]:
        if query not in answers:
            if not query:
                raise ValueError("a tile with no question cannot be dated")
            key = _episode_key(_validated_plan(query, minutes))
            if not key:
                raise ValueError(f"no episode key for {query!r}")
            answers[query] = reader(key)
        return answers[query]

    return probe


def _topic_is_written(query: str, minutes: int) -> bool:
    """Whether a tile would replay rather than generate.

    Wrapped rather than inlined because a malformed bank entry must not turn a
    browse screen into a 400: the honest answer for anything unaskable is
    "not ready", which is what an unwritten tile already says.
    """
    if not query:
        return False
    try:
        return _already_written(_validated_plan(query, minutes))
    except HTTPException:
        return False


@app.get("/api/explorenew")
async def explore_new(request: Request, interests: str = Query("", max_length=200)):
    """Explore New: episodes adjacent to a taste rather than inside it.

    This is `rank_might_like`, which has been written and tested since myFAM
    was built and shown nowhere since its shelf was removed - the only signal
    in the app that offers anything outside an established taste. Giving it a
    surface of its own is what makes it worth keeping.
    """
    _read_limit(request)
    user = _listener(request)
    body = topics_mod.build_explore_new(
        EVENTS, user, interests=_interests_for(request, interests)
    )
    if user and _remembers(request):
        EVENTS.record_impressions(user, [("explore_new", t["id"]) for t in body["topics"]])
    body["algo"] = EVENTS.algo_stamp()
    return body


class EventRequest(BaseModel):
    kind: str = Field(..., max_length=16)
    topic_id: str = Field("", max_length=64)
    text: str = Field("", max_length=300)
    #: The follow-up predicted for the finished episode, so Go Deeper can offer it
    #: back later without a second lookup.
    thread: str = Field("", max_length=200)


@app.get("/api/myfam")
async def myfam(request: Request, interests: str = Query("", max_length=200),
                minutes: int = Query(DEFAULT_MINUTES, ge=1, le=10)):
    """The four myFAM rails, ranked for this listener.

    **Costs no model call and cannot cause one.** Both inventories are already
    built - the evergreen bank is fixed, and the live story pool was composed
    in the background by whichever request found it stale - so this reads,
    ranks and returns. That is what "zero queue" means here: not that the page
    is fast, but that there is no path from opening it to generating anything.

    A listener with no history gets the **startup set** on the first rail -
    one time-anchored question per facet, ordered by what FAM's listeners
    actually play - with the rails that would have to invent something
    (friends, what went past you) honestly empty rather than filled with
    fakes. `taste_source` says which of the two ordered the first rail. See
    `startup.py`.

    `minutes` is the browse length this listener has chosen. It is here for one
    reason: whether a tile's script is already written depends on the length it
    would be written at, so asking the cache at the wrong length would mark
    ready tiles as unready and sort the rails wrong.
    """
    # A cheap read: ranking a fixed inventory costs no model call, so it takes
    # the reader's limit rather than the generation one.
    _read_limit(request)
    user = _listener(request)

    # One refresh serves every listener, so this is scheduled rather than
    # awaited: myFAM renders from whatever the shared pool holds and stays
    # instant. The browse surfaces are the one place CLAUDE.md says the wait
    # must be zero, and a news sweep is not worth spending it on - the rails
    # fall back to the bank on a cold first load and are full on the next.
    # The vocabulary keeps growing, on the same shape as the story sweep
    # beside it: scheduled, never awaited, at most once an hour for the whole
    # deployment. Without this the tree would be whatever it was at boot, and
    # a process that has been up for a week would be ranking on a week-old
    # vocabulary while the log filled with subjects it cannot name.
    if categories_mod.is_stale():
        asyncio.create_task(_grow_categories())
    if stories_mod.is_stale():
        # Through the same wrapper the boot sweep uses. A bare `create_task`
        # drops its exception into a log line nobody reads, and this one runs
        # on every page load - so a provider that raises would stop the pool
        # refreshing for the life of the process with the page still looking
        # normal.
        asyncio.create_task(_warm_stories())

    written = _written_probe(minutes)
    place = _place_for(request)
    feed = topics_mod.build_feed(
        EVENTS, user, interests=_interests_for(request, interests),
        circle=SOCIAL.circle_of(user), written=written,
        written_at=_written_at_probe(minutes),
        place=place.words, place_name=place.label,
        has_account=_has_account(request),
        country=_country_for(request, place))
    # Every tile says whether it would replay or generate, the same way the
    # "view more" screen already did. A listener browsing is choosing between
    # things to hear, and "this one starts instantly" is a real difference
    # between two of them - and it is free to say, because the ranking asked
    # the same question a moment ago and this is the memoised answer.
    for section in feed["sections"]:
        for topic in section["topics"]:
            topic["cached"] = written(topic.get("query", ""))
    feed["minutes"] = minutes
    # Logged here rather than inside build_feed, which stays a pure function of
    # the log - the whole ranking design is "computed on read, never stored",
    # and a ranker that writes cannot be tested by calling it. The impression
    # is a fact about this *request*, so it belongs at the request boundary.
    SOCIAL.seen(user)
    if _remembers(request):
        EVENTS.record_impressions(
            user,
            [(section["key"], topic["id"])
             for section in feed["sections"] for topic in section["topics"]],
        )
    feed["algo"] = EVENTS.algo_stamp()

    # Guess what this listener might tap, and pay for the *understanding* of it
    # now rather than when they are waiting (PROBLEMS.md §105).
    #
    # Scheduled, never awaited, for the same reason as the story sweep above:
    # this page is the one place CLAUDE.md says the wait must be zero, and a
    # page that waited for speculation would have spent the latency the
    # speculation was buying. It refuses itself cheaply - off, no prefetcher,
    # or this listener warmed recently - so calling it on every draw is a
    # dictionary lookup in the ordinary case.
    #
    # `minutes` is passed because it is part of the key. The browse length is
    # this header's own control, so warming at the interface default would
    # produce briefs nobody ever looks up for any listener who changed it.
    prefetch.schedule_cycle(user, minutes)
    return feed


@app.post("/api/event")
async def record_event(req: EventRequest, request: Request):
    """Log one interaction. Playback never depends on this succeeding."""
    _read_limit(request)
    # The bank, the first-run catalogue, the live story pool, and failing all
    # three the words of the question. The catalogue is the case worth naming:
    # an interest carries its own tags, which is the whole point of it -
    # "Formula 1" is not something the eight pickable facets can say, and this
    # is how it reaches the ranker without anybody being shown a tag name.
    #
    # A guest's is accepted and dropped - `ok` with `remembered: false`, never
    # an error, because a client firing events on a timer must not read a
    # guest session as a broken server (§127).
    if not _remembers(request):
        return {"ok": True, "remembered": False}
    tags = topics_mod.tags_for_id(req.topic_id, req.text)
    EVENTS.record(
        topics_mod.Event(_listener(request), req.kind, req.topic_id, req.text, tags,
                         thread=req.thread)
    )
    SOCIAL.seen(_listener(request))
    return {"ok": True, "remembered": True}


class PersonRequest(BaseModel):
    name: str = Field("", max_length=social_mod.MAX_NAME)
    handle: str = Field("", max_length=social_mod.MAX_HANDLE + 1)
    #: A `data:image/...` URL the client already downscaled, or "" to remove
    #: the picture. `None` means "leave it as it is" - see `set_me`. The cap
    #: is the one in `social.MAX_AVATAR`, repeated here so an oversized body
    #: is refused at the edge rather than after a database round trip.
    avatar: Optional[str] = Field(None, max_length=social_mod.MAX_AVATAR)


class EchoRequest(BaseModel):
    query: str = Field(..., max_length=300)
    title: str = Field("", max_length=200)
    minutes: int = Field(DEFAULT_MINUTES, ge=1, le=10)
    thread: str = Field("", max_length=200)


@app.post("/api/me")
async def set_me(req: PersonRequest, request: Request):
    """Name, handle and picture for this device. Not an account - see
    /api/profile.

    `avatar` is omitted to leave the current one alone and sent as "" to take
    it off, which are different requests: a client that simply never sends the
    field must not silently delete a picture somebody chose.
    """
    _read_limit(request)
    try:
        return SOCIAL.set_person(_listener(request), req.name, req.handle,
                                 avatar=req.avatar)
    except social_mod.SocialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/echo")
async def post_echo(req: EchoRequest, request: Request):
    """Push a finished episode to the people who follow this listener.

    Costs nothing to generate: an echo is a row pointing at a query whose
    script already exists, which is exactly why the social layer is cheap.
    """
    _read_limit(request)
    user = _listener(request)
    try:
        echo = SOCIAL.echo(user, req.query, req.title, req.minutes, req.thread)
    except social_mod.SocialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Showing somebody an episode is a statement about taste, and until this
    # line the ranker never heard about it. Recorded after the row is written,
    # so a failed vibe does not teach the feed anything happened - and, like
    # every other write to this log, it can be lost without costing the action
    # the listener actually took. An account's only, like every other write
    # to it (§127): a guest's vibe is still posted, and still not remembered.
    if _remembers(request):
        EVENTS.record(topics_mod.Event(
            user, "vibe", "", req.query, topics_mod.tags_for_text(req.query)))
    return echo.as_dict()


@app.delete("/api/echo")
async def delete_echo(request: Request, q: str = Query("", max_length=300),
                      minutes: int = Query(DEFAULT_MINUTES, ge=1, le=10)):
    _read_limit(request)
    return {"ok": SOCIAL.unecho(_listener(request), q, minutes)}


# --- vibe -----------------------------------------------------------------
#
# "Vibe" is the product name for what this codebase calls an echo. The rename
# is a rename in the interface and an *alias* on the server: `/api/echo` and
# `/api/vibe` are the same handler under two paths, and `social.py` still says
# echo throughout.
#
# Two paths rather than one because a client in the field - a phone that has
# not updated - is still calling the old one, and a rename that breaks it
# turns a copy change into an outage. Two names for one row is a cost of
# exactly zero; a second table would not be.
@app.post("/api/vibe")
async def post_vibe(req: EchoRequest, request: Request):
    """Vibe an episode. The same act as `/api/echo`, under its product name."""
    return await post_echo(req, request)


@app.delete("/api/vibe")
async def delete_vibe(request: Request, q: str = Query("", max_length=300),
                      minutes: int = Query(DEFAULT_MINUTES, ge=1, le=10)):
    """Take a vibe back. The same act as `DELETE /api/echo`."""
    return await delete_echo(request, q, minutes)


@app.get("/api/vibes")
async def my_vibes(request: Request, limit: int = Query(40, ge=1, le=200)):
    """Everything this listener has vibed, newest first.

    The profile's "My Vibe" shelf. `/api/profile` already returns the first
    twelve beside everything else it knows; this is the same list on its own,
    so a shelf that wants all of them does not have to fetch a whole profile
    to get them.
    """
    _read_limit(request)
    user = _listener(request)
    person = SOCIAL.person(user)
    vibes = SOCIAL.echoes_by(user, limit=limit)
    return {"vibes": [v.as_dict(person["name"], person["handle"]) for v in vibes],
            "count": len(SOCIAL.echoes_by(user, limit=200))}


@app.get("/api/profile")
async def profile(request: Request):
    """Counts and subjects from this listener's own event log. No model call."""
    _read_limit(request)
    user = _listener(request)
    body = topics_mod.summary(EVENTS, user)
    SOCIAL.seen(user)
    person = SOCIAL.person(user)
    body["name"] = person["name"]
    body["handle"] = person["handle"]
    body["joined"] = person["joined"]
    # Observed, not invented: when the server first saw this listener and when
    # it last did. `known` separates "never been here" from "here, unnamed",
    # which the page could not tell apart while a row meant "chose a name".
    body["last_seen"] = person["last_seen"]
    body["known"] = person["known"]
    body["mixes"] = [m.as_dict() for m in MIXES.public_for_user(user)]
    body["echoes"] = [e.as_dict(person["name"], person["handle"])
                      for e in SOCIAL.echoes_by(user, limit=12)]
    body["echo_count"] = len(SOCIAL.echoes_by(user, limit=200))
    # The picture, and the follow graph the page has been describing without
    # having. Counts rather than the lists: a profile draws two numbers and
    # opens a screen for the rest, and shipping five hundred people to draw
    # two numbers is the wrong shape of request.
    body["avatar"] = person["avatar"]
    body["follows"] = SOCIAL.follow_counts(user)
    # The interests row, decided here rather than in the interface.
    #
    # It used to be assembled on the page - chosen facets, then chosen
    # subjects, then whatever the log had inferred, twelve of them - and that
    # had two problems the owner named. It was a fixed order, so an answer
    # given in thirty seconds on the first run outranked a month of listening
    # forever; and there was no cap that meant anything, so the row grew with
    # every episode until it was a list of everything somebody had touched.
    #
    # `ranked_interests` is the ranking (by the same taste profile myFAM uses,
    # so it moves as they listen) and `profile_interests` is the cut - four,
    # pinned or top. Both come back: the pills are `interests_shown` and the
    # editor offers `interests_ranked`, so the client draws and never decides.
    #
    # This is **their own** profile, which is why it may be read off their
    # listening at all. `/api/person` deliberately does not do the same - see
    # the note there - because the rule that what somebody has listened to is
    # theirs does not stop applying because the inference is flattering.
    prefs = PREFS.get(user)
    ranked = topics_mod.ranked_interests(
        EVENTS, user, chosen=prefs.interests, chosen_topics=prefs.topics)
    shown, source = topics_mod.profile_interests(
        ranked, pinned=prefs.profile_interests, hidden=prefs.hidden_interests)
    body["interests_shown"] = shown
    body["interests_ranked"] = ranked
    body["interests_pinned"] = list(prefs.profile_interests)
    body["interests_source"] = source
    body["interests_max"] = topics_mod.PROFILE_INTEREST_SLOTS
    body["circle"] = _circle_row(user)
    return body


#: How recent a friend's vibe has to be for their avatar to carry the VIBE
#: badge on YourFAM. A week, the same window "What you missed last week" uses:
#: a badge that stayed up for a vibe from March would stop meaning anything.
CIRCLE_VIBE_WINDOW = 7 * 24 * 3600
#: And the gold ring - "something new from this person" - is narrower: a vibe
#: in the last two days, or a message you have not read yet.
CIRCLE_FRESH_WINDOW = 2 * 24 * 3600
CIRCLE_MAX = 12


def _circle_row(user: str) -> list[dict]:
    """The story-style avatar row on YourFAM: friends first, then follows.

    Every flag is read off something stored - the echoes table and the
    unread counts - so a ring or a badge on somebody's face is a claim the
    app can back. Nothing here is a play or a completion: what a friend has
    listened to is theirs, and only what they chose to show (a vibe) or sent
    (a message) may light up their picture.
    """
    if not user:
        return []
    people: list[dict] = []
    seen: set[str] = set()
    for person in SOCIAL.friends(user) + SOCIAL.following(user):
        uid = person.get("user_id") or ""
        if uid and uid not in seen:
            seen.add(uid)
            people.append(person)
    people = people[:CIRCLE_MAX]
    if not people:
        return []
    now = time.time()
    latest = SOCIAL.latest_echo_at([p["user_id"] for p in people])
    unread = {t["with"] for t in MESSAGES.inbox(user) if t.get("unread")}
    friends = {p["user_id"] for p in SOCIAL.friends(user)}
    out = []
    for person in people:
        uid = person["user_id"]
        vibed_at = latest.get(uid, 0.0)
        out.append({
            "user_id": uid,
            "name": person.get("name") or "",
            "handle": person.get("handle") or "",
            "avatar": person.get("avatar") or "",
            "friend": uid in friends,
            "vibed": bool(vibed_at) and now - vibed_at <= CIRCLE_VIBE_WINDOW,
            "fresh": (uid in unread
                      or (bool(vibed_at) and now - vibed_at <= CIRCLE_FRESH_WINDOW)),
        })
    return out


class ProgressRequest(BaseModel):
    query: str = Field(..., max_length=saved_mod.MAX_QUERY)
    minutes: int = Field(..., ge=1, le=10)
    seconds: float = Field(..., ge=0, le=3600)
    title: str = Field("", max_length=saved_mod.MAX_TITLE)
    #: The topic a follow-up was asked from. Part of the episode's cache key,
    #: so without it a resumed follow-up would be a different episode.
    context: str = Field("", max_length=300)


@app.post("/api/progress")
async def progress_write(req: ProgressRequest, request: Request) -> dict:
    """How far through an episode this listener got, kept on their account.

    It was kept in the browser, so it belonged to the phone rather than the
    person: it survived a log-out, and the next person on that device was
    offered somebody else's half-heard episodes (§127). A guest's is accepted
    and dropped rather than refused, like `/api/event`, because this is sent
    from a playback timer and a timer must never surface an error.
    """
    _read_limit(request)
    if not _remembers(request):
        return {"ok": True, "remembered": False}
    kept = SAVED.note_progress(_listener(request), req.query, req.minutes,
                               req.seconds, title=req.title, context=req.context)
    return {"ok": True, "remembered": True, "resumable": kept}


async def _episode_blurb(pipeline, query: str, minutes: int,
                         context: str = "") -> tuple[str, str]:
    """`(title, summary)` for an episode the cache holds, or `("", "")`.

    What a Go Deeper card draws, read from the same cache the player reads, so
    a card cannot name an episode differently from the player that opens it.
    Never generates. `pipeline` is built once per request by the caller - it
    carries a voice engine, and four cards are not worth four of those.
    """
    if pipeline is None:
        return "", ""
    try:
        plan = _validated_plan(query, minutes, context)
    except HTTPException:
        return "", ""
    try:
        meta = await pipeline.episode_meta(plan)
        return meta["title"], meta["summary"]
    except Exception:  # noqa: BLE001 - a card line must not fail the section
        log.exception("could not read a Go Deeper card's title")
        return "", ""


@app.get("/api/godeeper")
async def go_deeper(request: Request, interests: str = Query("", max_length=200)):
    """"Pick up where you left off": part-heard episodes, the follow-ups the
    finished ones predicted, and episodes like the last one heard.

    **Empty until the listener has done something** (§127). It used to top
    itself up from the bank so it was never empty, which meant a brand-new
    listener was told they had left something off before they had played
    anything. Now nothing is offered until there is something to pick up.

    **Only for an account**, on the same rule as the event log: what somebody
    was halfway through is part of what FAM remembers about them, and a guest
    session is a device rather than a person.

    Each card carries a one-sentence `summary` when the cache has one, which is
    what makes the section read like the rest of myFAM rather than a bare list
    of titles. Costs nothing: every line here is read, never written.
    """
    _read_limit(request)
    user = _listener(request)
    if not user or not _remembers(request):
        return {"threads": [], "resume": [], "similar": []}

    resume = SAVED.progress(user, limit=4)
    try:
        pipeline = _make_pipeline() if resume else None
    except TTSUnavailable:
        pipeline = None
    for row in resume:
        title, summary = await _episode_blurb(pipeline, row["query"], row["minutes"],
                                              row.get("context", ""))
        row["title"] = title or row.get("title") or ""
        row["summary"] = summary

    threads = EVENTS.open_threads(user)
    for row in threads[:4]:
        # The follow-up itself has usually not been made, so there is no
        # summary of *it* to read - the line says where it comes from instead,
        # which is the one true thing known about it.
        row["summary"] = (f"Follows on from {row['from_title']}."
                          if row.get("from_title") else "")

    # "Similar": the feed's own next-up ranking, seeded with the last thing
    # they heard - the same ranker as the post-episode popup, so the two
    # cannot disagree. Only once there is something to be similar *to*.
    similar: list[dict] = []
    last = (resume[0]["query"] if resume
            else next((e.text for e in EVENTS.for_user(user, limit=20)
                       if e.kind in ("play", "complete") and e.text), ""))
    if last:
        picks = topics_mod.rank_next_up(
            EVENTS, user, after_text=last,
            interests=_interests_for(request, interests), has_account=True)
        for topic in picks[:4]:
            tile = topic.as_dict()
            similar.append({"topic_id": tile.get("id", ""),
                            "query": tile.get("query", ""),
                            "title": tile.get("title", ""),
                            "summary": tile.get("angle") or tile.get("subtitle")
                                       or ""})
    return {"threads": threads, "resume": resume, "similar": similar}


class RateRequest(BaseModel):
    query: str = Field(..., max_length=300)
    minutes: int = Field(DEFAULT_MINUTES, ge=1, le=10)
    #: 1 like, -1 dislike, 0 take it back.
    value: int = Field(..., ge=-1, le=1)
    #: The card's cache key, so the counts that come back include its play
    #: count rather than a 0 the card would draw over the real one.
    key: str = Field("", max_length=128)


def _episode_stats(listener: str, query: str, minutes: int, key: str = "") -> dict:
    """Everything Explore's card counts, for one episode (§134)."""
    counts = SOCIAL.episode_counts(query, minutes, listener)
    store = SCRIPT_CACHE if SCRIPT_CACHE is not None else build_cache()
    plays = 0
    if store is not None and key and hasattr(store, "plays"):
        plays = store.plays(key)
    return {"plays": plays, "vibes": counts["vibes"], "likes": counts["likes"],
            "dislikes": counts["dislikes"], "rating": counts["rating"],
            "my_vibe": counts["vibed"]}


@app.post("/api/rate")
async def rate_episode(req: RateRequest, request: Request):
    """Like or dislike an episode, or take the thumb back (§134).

    Explore's thumbs. Costs nothing and generates nothing - a row beside the
    vibe it sits next to on the card, keyed the same way. Returns the card's
    fresh counts so the buttons redraw from the server's numbers rather than
    from a guess about everybody else's.
    """
    _read_limit(request)
    user = _listener(request)
    try:
        SOCIAL.rate(user, req.query, req.minutes, req.value)
    except social_mod.SocialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _episode_stats(user, req.query, req.minutes, req.key)


@app.get("/api/episode/stats")
async def episode_stats(request: Request,
                        q: str = Query(..., max_length=300),
                        minutes: int = Query(DEFAULT_MINUTES, ge=1, le=10),
                        key: str = Query("", max_length=128)):
    """Plays, vibes, likes and dislikes for one episode, right now (§134).

    What an Explore card re-reads once its episode has started, so the play
    count in its corner includes the play that is happening instead of being
    a number from whenever the feed was fetched. `key` is the card's own cache
    key; without one the play count is 0 rather than a guess.
    """
    _read_limit(request)
    return _episode_stats(_listener(request), q, minutes, key)


@app.get("/api/explore")
async def explore(request: Request, limit: int = Query(30, ge=1, le=60)):
    """Episodes other listeners have already generated, newest first.

    This endpoint costs nothing and, by design, can cause nothing to be
    generated: it reads finished scripts out of the cache. Everything in that
    cache passed the personal-query filter before it was written, so it is
    already safe to show someone else.

    **Other people's, and only other people's.** An episode this listener
    generated is dropped from their own feed, because Explore's entire premise
    is that these are somebody else's questions - and their own coming back at
    them reads as the app having nothing to show rather than as a feature.
    It is a display filter: the entry stays in the shared cache, still replays
    instantly for them anywhere else, and still appears on everyone else's
    feed. Entries written before authorship was recorded have no author and
    are shown to everybody, which is what they were already doing.
    """
    _read_limit(request)
    store = SCRIPT_CACHE if SCRIPT_CACHE is not None else build_cache()
    if store is None:
        return {"episodes": [], "reason": "The shared cache is switched off."}
    now = time.time()
    listener = _listener(request)

    # **A friend vibed this.** Two conditions, and the card claims a
    # friendship so both have to hold: this listener's *friend* generated the
    # episode, and that same friend vibed it. Either alone is a weaker claim -
    # a stranger's vibe is not addressed to you, and a friend who generated
    # something without vibing it did not recommend it.
    #
    # `friends` is the mutual case, derived and never stored (see SHARING.md),
    # which is what makes "friend" a word the tag is allowed to use.
    friends = {p["user_id"]: p for p in SOCIAL.friends(listener)} if listener else {}
    vibes = SOCIAL.echoes_among(list(friends)) if friends else {}

    # And who else vibed what. Still read, and still only for the *order*: a
    # vibe is somebody choosing to send an episode, which is a real reason for
    # a card to lead. It no longer puts a stranger's name on one - naming
    # people the listener has never heard of under a heading about their
    # friends is the mistake §102 took off myFAM.
    anyone = SOCIAL.recent_echoes(exclude_user=listener)

    episodes = []
    entries = store.recent(limit, exclude_author=listener)
    all_counts = SOCIAL.episode_counts_many(
        [(e["query"], e["minutes"]) for e in entries], listener)
    for entry in entries:
        pair = (entry["query"], entry["minutes"])
        by = vibes.get(pair)
        friend = friends.get(entry.get("author") or "")
        card = {
            "query": entry["query"],
            # The episode's own title when the model wrote one, else the
            # question with a capital letter - which is what every card
            # showed before titles existed, and is still right for an entry
            # written before this column did.
            "title": entry.get("title")
                     or (entry["query"][:1].upper() + entry["query"][1:]),
            "minutes": entry["minutes"],
            # How many times it has actually been played - `scripts.plays`,
            # counted where an episode starts and nowhere that only looks
            # (§134). The top-right number on the card.
            "plays": entry["plays"],
            # The cache key, so the card can ask for its own fresh numbers
            # after a play (`/api/episode/stats`). A hash of the question and
            # the length - it names an episode and nobody.
            "key": entry["key"],
            "thread": entry["thread"],
            "age_seconds": max(0.0, now - entry["created"]),
            "vibed": bool(pair in anyone or by),
        }
        # The counts on the card's buttons: vibes, likes, dislikes, and this
        # listener's own thumb and vibe so the buttons open in the right state.
        counts = all_counts[(entry["query"], entry["minutes"])]
        card.update({"vibes": counts["vibes"], "likes": counts["likes"],
                     "dislikes": counts["dislikes"], "rating": counts["rating"],
                     "my_vibe": counts["vibed"]})
        # Note what is *not* on the card: `author`. It is read here for one
        # display decision and resolved to a name and a picture; a listener id
        # in this response would be an id the client could send back, which is
        # the rule `_listener` exists to keep.
        if by and friend and by.get("user_id") == friend["user_id"]:
            card["vibed_by"] = {
                "name": by["name"] or friend.get("name") or "A friend",
                "handle": by["handle"] or friend.get("handle") or "",
                "avatar": by["avatar"] or friend.get("avatar") or "",
            }
        episodes.append(card)

    # A vibed episode leads, because someone chose to send it, and a friend's
    # leads over a stranger's.
    episodes.sort(key=lambda e: (not e.get("vibed_by"), not e["vibed"],
                                 e["age_seconds"]))
    return {"episodes": episodes}


#: How many cards the Topic screen asks for at a time. Six fills the
#: two-column grid above its "View more"; the rest come in pages.
INTEREST_PAGE = 6


def _interest_scope(interest_id: str, label: str) -> tuple[str, set, list]:
    """(label, tags, words) that decide whether an episode is "on" an interest.

    Three kinds of interest reach this screen, and they resolve differently:
    a facet (`money`), a catalogue subject (`formula1`), or whatever somebody
    typed into Edit profile ("Surfing"). The most **specific** tags win - a
    catalogue subject carries its facet too, and matching on that would make
    "Formula 1" a page about every sport. A typed topic the vocabulary cannot
    see at all is matched on its own words, which is a worse answer and much
    better than an empty page for something the listener said they like.
    """
    iid = (interest_id or "").strip()
    if iid in topics_mod.TAG_LABELS:
        return topics_mod.TAG_LABELS[iid], {iid}, []
    item = topics_mod.CATALOGUE_BY_ID.get(iid)
    if item is not None:
        name, tags = item.label, set(item.tags)
    else:
        name = (label or iid).strip()
        tags = set(topics_mod.tags_for_text(name))
    specific = {t for t in tags if t not in topics_mod.TAG_LABELS}
    words = [w for w in name.lower().replace("&", " ").split() if len(w) >= 3]
    return name, (specific or tags), words


def _on_interest(text: str, tags: set, words: list) -> bool:
    if tags and tags & set(topics_mod.tags_for_text(text)):
        return True
    low = text.lower()
    return bool(words) and all(w in low for w in words)


@app.get("/api/interest")
async def interest_episodes(request: Request,
                            id: str = Query("", max_length=120),
                            label: str = Query("", max_length=120),
                            filter: str = Query("latest", pattern="^(latest|friends)$"),
                            offset: int = Query(0, ge=0, le=200),
                            limit: int = Query(INTEREST_PAGE, ge=1, le=24)) -> dict:
    """Episodes on one interest, freshest first. **Generates nothing.**

    The Topic screen behind every interest chip on YourFAM, a friend's
    profile and a chat header. Three inventories, in freshness order: the
    live story pool, then finished episodes in the shared cache (anybody's -
    this is a place a listener went looking, like Explore New), then the
    evergreen bank as the tail, because a standing explainer is on-topic but
    is never the freshest thing. `filter=friends` is what their circle
    vibed, and nothing else - the one row that can be empty for a reason
    about people rather than about content, and it says so.

    Every card is a question and its original length. The screen plays it at
    the length its own pill is set to, which is the spec's point: the pill
    sets how long a generated episode is, it does not filter by duration.
    """
    _read_limit(request)
    listener = _listener(request)
    name, tags, words = _interest_scope(id, label)
    if not tags and not words:
        return {"label": name, "episodes": [], "more": False,
                "reason": "FAM cannot tell what that interest covers yet."}
    now = time.time()
    cards: list[dict] = []
    seen: set[str] = set()

    def add(query: str, title: str, minutes: int, source: str, at: float,
            **extra) -> None:
        q = (query or "").strip()
        if not q or q.lower() in seen:
            return
        seen.add(q.lower())
        card = {"query": q, "title": title or (q[:1].upper() + q[1:]),
                "minutes": int(minutes or 0), "source": source,
                "age_seconds": max(0.0, now - at) if at else None}
        card.update(extra)
        cards.append(card)

    if filter == "friends":
        circle = SOCIAL.circle_of(listener) if listener else []
        vibes = SOCIAL.echoes_among(circle) if circle else {}
        for (query, minutes), who in sorted(vibes.items(),
                                            key=lambda kv: -(kv[1].get("at") or 0)):
            if _on_interest(f"{query} {who.get('title', '')}", tags, words):
                add(query, who.get("title", ""), minutes, "vibe",
                    who.get("at") or 0.0,
                    vibed_by={"name": who.get("name") or "",
                              "handle": who.get("handle") or "",
                              "avatar": who.get("avatar") or ""})
        reason = ("" if cards else
                  ("Nobody you follow has vibed anything on this yet."
                   if circle else
                   "Follow some people and what they vibe on this shows up here."))
    else:
        for tile in topics_mod.live_topics(now):
            if (tags & set(tile.tags)) or _on_interest(tile.query, set(), words):
                add(tile.query, tile.title, 0, "story", 0.0, angle=tile.subtitle)
        store = SCRIPT_CACHE if SCRIPT_CACHE is not None else build_cache()
        if store is not None:
            for entry in store.recent(120):
                text = f"{entry['query']} {entry.get('title') or ''}"
                if _on_interest(text, tags, words):
                    add(entry["query"], entry.get("title") or "",
                        entry["minutes"], "cache", entry["created"])
        for tile in topics_mod.TOPIC_BANK:
            if tags & set(topics_mod.topic_tags(tile)):
                add(tile.query, tile.title, 0, "bank", 0.0, angle=tile.subtitle)
        reason = "" if cards else "Nothing on this yet. Search it, and yours is the first."
    page = cards[offset:offset + limit]
    return {"label": name, "episodes": page,
            "more": len(cards) > offset + limit, "reason": reason}


@app.get("/api/sources")
async def episode_sources(
    request: Request,
    q: str = Query(..., description="What the listener asked"),
    minutes: int = Query(DEFAULT_MINUTES, ge=1, le=10),
    context: str = Query("", description="Topic the listener just heard"),
    search: bool = Query(True),
):
    """Who this episode's facts came from.

    Read from the cache under the same key the script is stored under, which
    is why it works on a replay: a cached episode has no `notes` to rebuild
    provenance from, so it is stored beside the sentences like `thread` is.

    **Nothing here has ever been in a prompt.** The evidence packet carries
    grades and never hostnames, deliberately - a domain in the packet is a
    domain the voice can read out. This is the display channel, and it must
    stay separate: showing sources in the app is not a reason to let the model
    cite them aloud. See `provenance.py`.
    """
    _read_limit(request)
    plan = _validated_plan(q, minutes, context, search)
    try:
        pipeline = _make_pipeline()
    except TTSUnavailable:
        return {"items": [], "retrievers": [], "count": 0, "known": False}
    raw = await pipeline.sources_for(plan)
    found = provenance_mod.Provenance.from_json(raw)
    body = found.as_dict()
    # An episode with no stored provenance is not an episode with no sources -
    # it may simply predate this, or have been answered from knowledge. Said
    # in words so the interface can tell the difference rather than rendering
    # an empty list as "no sources".
    body["known"] = bool(raw)
    return body


@app.get("/api/transcript")
async def episode_transcript(
    request: Request,
    q: str = Query(..., description="What the listener asked"),
    minutes: int = Query(DEFAULT_MINUTES, ge=1, le=10),
    context: str = Query("", description="Topic the listener just heard"),
    search: bool = Query(True),
):
    """The sentences this episode is made of, for live captions.

    Read from the cache under the same key the script is stored under, like
    `/api/sources` and `/api/next` - and for the same reason as both: what an
    episode *says* is only settled once the script has been written, which is
    after the audio response headers have gone out.

    **It never generates.** Captions that could trigger a write would be a
    second full Claude call for every episode somebody chose to read along
    with - the expensive half of an episode, paid twice for one listen. So the
    honest states are "here are the sentences" and "not written down yet", and
    `known` is which. An attachment episode is deliberately never cached, so
    it never has captions; that is a fact about privacy, not a failure.

    **It reads the live track first** (§107). The cache is written once, at the
    end, so on a first listen the sentences do not exist under this key until
    after the last word has been spoken - which is the one moment captions are
    no use. `live_captions` holds what has been handed to the voice so far, so
    the transcript builds up as it is read rather than arriving whole or not at
    all. Same key, so the two cannot describe different episodes, and the
    fallback order is the only one that can be right: a live track is *this*
    generation and the cache may hold an older one under the same key while a
    re-write is in flight.

    `done` is what stops the client asking. Without it "still being written"
    and "that was the whole episode" are the same answer, and the interface
    guessed at the difference with a poll count - six tries over twelve
    seconds, which is roughly a two-minute episode's script and nothing like a
    researched ten-minute one's, so every long episode read back as having no
    transcript at all.
    """
    _read_limit(request)
    plan = _validated_plan(q, minutes, context, search)
    try:
        pipeline = _make_pipeline()
    except TTSUnavailable:
        return {"sentences": [], "known": False, "live": False, "done": True,
                "starts": []}
    sentences, live, done = await pipeline.captions_for(plan)
    # Where each sentence starts in the audio, measured on the speaking path
    # (§127). Empty when unmeasured, and the interface then estimates as it
    # always did - so a missing timing is a less precise caption, never a
    # missing one.
    starts = await pipeline.caption_starts(plan) if live else []
    return {"sentences": sentences, "known": bool(sentences),
            "live": live, "done": done,
            "starts": starts if len(starts) == len(sentences) else []}


@app.get("/api/next")
async def next_thread(
    request: Request,
    q: str = Query(..., description="What the listener asked"),
    minutes: int = Query(DEFAULT_MINUTES, ge=1, le=10),
    context: str = Query("", description="Topic the listener just heard"),
    # Same reason as /api/audio: this looks up a cache entry, and the entry it
    # looks for has to be keyed the same way the audio request keyed it.
    search: bool | None = Query(None),
):
    """The follow-up this listener is most likely to want, and the episode's
    own title.

    Both read from the script cache, so both cost no tokens and no time. Both
    are written by the model on trailing marker lines that are stripped before
    anything is spoken, and both are only known once the script is finished -
    which is after the audio response headers have gone out. Hence one lookup
    rather than a header on `/api/audio`.

    The thread is offered as a one-tap suggestion in Go Deeper: an episode that
    ends pointed at something specific is only half the job if acting on it
    still means composing a question into an empty box.

    The title replaces the typed question. Somebody who asked "what happened
    with the fed yesterday" was shown an episode called *What Happened With The
    Fed Yesterday* - their own words handed back with capital letters. The
    player opens on a provisional title derived from the question and swaps
    this in when it lands.

    An empty answer for either is normal - the script may not be cached, or the
    model may not have written that line - and the interface falls back to what
    it had.
    """
    _read_limit(request)
    plan = _validated_plan(q, minutes, context, search)
    try:
        pipeline = _make_pipeline()
    except TTSUnavailable:
        return {"thread": "", "title": "", "title_final": False, "summary": ""}
    # `title_final` is what lets the player ask early: the brief's title is on
    # the live track before the first word (§127), and the interface keeps
    # asking until the writer's own has replaced it.
    return await pipeline.episode_meta(plan)


@app.get("/api/audio")
async def audio(
    request: Request,
    q: str = Query(..., description="What the listener asked"),
    minutes: int = Query(DEFAULT_MINUTES, ge=1, le=10),
    fmt: str = Query("wav", pattern="^(wav|pcm)$"),
    context: str = Query("", description="Topic the listener just heard, for a follow-up"),
    voice: str = Query("", description="Voice id from /api/voices"),
    # `None`, not False. An omitted parameter has to stay omitted all the way
    # to plan_episode: `bool = Query(False)` turns "the listener said nothing"
    # into "the listener said no", which is a different thing and beats
    # SEARCH_MODE=auto. The browser never sends this parameter, so with a
    # False default the freshness heuristic was consulted exactly never.
    # search=1 / search=0 still win, which is what opt-in means.
    search: bool | None = Query(None, description="Force research on (1) or off (0); "
                                                  "omit to let the question decide"),
    cached_only: bool = Query(False, description="Replay only; never generate. Used by Explore"),
    topic_id: str = Query("", max_length=64, description="Bank topic id, when played from myFAM"),
    attach: str = Query("", max_length=400, description="Attachment ids from /api/attach"),
):
    """Stream the episode.

    `fmt=wav` prefixes a live-stream WAV header so a plain <audio> tag works.
    `fmt=pcm` sends bare samples for the Web Audio player, which schedules
    chunks itself and therefore starts sooner and seeks better.
    """
    # Every request answers to the cheap ceiling. The pace on top of it is for
    # requests that can actually spend a model call.
    _read_limit(request)
    user = _listener(request)
    minutes = min(minutes, entitlements.max_minutes(_tier(request),
                                                    settings.max_minutes))
    plan = _validated_plan(q, minutes, context, search, cached_only,
                           _attachments_for(user, attach))

    # A replay-only request - Explore, and any card played from it - provably
    # cannot spend a model call, so pacing it only stops someone swiping a feed
    # at a normal speed, which is exactly what the feed is for. Neither can a
    # request whose script is already written: the pipeline replays the stored
    # sentences. That second case is the ordinary one the old code got wrong -
    # tapping the episode you are listening to, or switching voice, which
    # reuses the script *by design* (PROBLEMS.md 70).
    if not (cached_only or _already_written(plan)):
        _rate_limit(request)

    # After validation, so a malformed request never costs an allowance, and
    # before anything expensive starts. An Explore replay counts against a
    # different, looser allowance because it provably cannot write a script -
    # the pipeline refuses - so it costs GPU seconds and nothing else.
    #
    # `episode_key` is *which* episode, so the allowance counts episodes and
    # not requests. Five taps on one question used to spend a free listener's
    # whole day and then answer 429 to everything - for one episode, which they
    # had already paid for on the first tap.
    reserved = _reserve(request, "explore" if cached_only else "episode",
                        episode_key=_episode_key(plan),
                        surface=_surface(cached_only, topic_id, context))

    try:
        pipeline = _make_pipeline(voice or None, author=user)
    except TTSUnavailable as exc:
        # The server cannot speak at all. Nothing was generated and nothing was
        # billed, so the allowance goes back - this is the machine being
        # broken, not the listener spending.
        _refund(reserved, user)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # Ask for a GPU now, before Claude has written a word.
    #
    # A serverless worker that has scaled to zero pays container boot plus a
    # ~10s model load on its first job. Firing that here means it happens
    # *alongside* script generation instead of in front of the first chunk -
    # which is CLAUDE.md's rule that latency is answered by starting earlier
    # rather than by filling the gap, applied to the one wait this split adds.
    # It is a hint: `wake()` never raises and never blocks, and a miss costs
    # only the cold start it was trying to hide.
    #
    # Not for an episode whose audio is already kept (§132): it will be read
    # out of the database, and a GPU booted for it is a bill for nothing.
    stored = getattr(pipeline, "has_stored_audio", None)
    if stored is None or not await stored(plan):
        _wake_remote_voice()

    stats = GenerationStats()
    started = time.monotonic()
    # The player must be told the engine's real rate, not the configured one.
    sample_rate = pipeline.engine.sample_rate

    source = pipeline.stream_wav(plan, stats) if fmt == "wav" else pipeline.stream_pcm(plan, stats)

    # Pull chunks until real audio exists BEFORE returning a response. Once the
    # first byte is sent the status code is fixed, so a failure after that point
    # can only be logged - which is how a broken API key used to arrive at the
    # browser as a successful, silent, empty episode. Priming here means such a
    # failure becomes a proper error the interface can show.
    primed: list[bytes] = []
    preroll_bytes = int(PREROLL_SECONDS * sample_rate * 2)
    # Instrumentation only - nothing below changes what is served. These are
    # the marks that turn the interval between "audio exists" and "the client
    # has a byte" from an invisible cost into a measured one.
    first_pcm_at: float | None = None
    preroll_at: float | None = None
    first_byte_at: float | None = None
    chunks_primed = 0
    try:
        async for chunk in source:
            primed.append(chunk)
            chunks_primed += 1
            if first_pcm_at is None and len(chunk) > WAV_HEADER_BYTES:
                first_pcm_at = time.monotonic() - started
            if sum(len(c) for c in primed) - WAV_HEADER_BYTES >= preroll_bytes:
                preroll_at = time.monotonic() - started
                break
    except NotCached as exc:
        # Expected, not a fault: the entry expired between listing and tapping.
        # The interface drops the card and moves on.
        _refund_if_unspent(reserved, user, stats.usage)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NoEvidence as exc:
        # FAM refused rather than guessing: every retriever came back empty on
        # a question that turns on current facts (§109). The listener heard
        # nothing, so they are not charged for an episode - and this one is
        # refunded *whatever was billed*, which is the exception to the rule
        # below. Research and the brief did spend money, but the spend was a
        # decision FAM made and then declined to deliver on; charging a
        # listener for a retrieval outage would let one bad afternoon at a
        # search vendor eat a free tier's whole day.
        log.warning("refused an episode for lack of evidence: %s", exc)
        _record_usage(user, stats.usage,
                      surface=_surface(cached_only, topic_id, context),
                      minutes=plan.minutes, audio_seconds=0.0,
                      cache_hit=False)
        _refund(reserved, user)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("generation failed before any audio was produced")
        # A failure is not a refund. Research may already have been billed, and
        # a retry loop against a broken key would otherwise be the cheapest
        # thing in the ledger while being the most expensive thing on the
        # invoice.
        _record_usage(user, stats.usage,
                      surface=_surface(cached_only, topic_id, context),
                      minutes=plan.minutes, audio_seconds=stats.voiced_seconds,
                      cache_hit=stats.cache == "hit")
        # Same rule as the ledger above: refunded only if nothing was billed.
        _refund_if_unspent(reserved, user, stats.usage)
        raise HTTPException(status_code=502, detail=friendly_error(exc)) from exc

    # `stats.sentences` is the honest test: silence is bytes, but it is not an
    # episode. A script that came back empty must not be served as one.
    if stats.sentences == 0 or sum(len(c) for c in primed) <= WAV_HEADER_BYTES:
        log.error("generation produced no audio for %r", plan.query)
        # The listener heard nothing, so they are not charged for an episode.
        # This was the one failure path with no refund on it at all, which on a
        # server whose voice had gone away cost a free listener their whole day
        # in silent 502s and then answered 429 until midnight UTC.
        _refund_if_unspent(reserved, user, stats.usage)
        raise HTTPException(
            status_code=502,
            detail="The episode came back with no speech in it. Check the server "
            "log, and that ANTHROPIC_API_KEY is set and a speech engine is installed.",
        )

    # Where the wait in front of the first word went, one step at a time, at
    # the moment it is known - this line is written when the first audio
    # exists, not when the episode ends minutes later, so a slow episode can
    # be read off the deploy's log while it is still playing.
    if stats.cache != "hit":
        log.info("%s", stats.marks.stage_report(plan.query))
    primed_bytes = max(0, sum(len(c) for c in primed) - WAV_HEADER_BYTES)
    primed_seconds = primed_bytes / (sample_rate * 2)

    async def body():
        nonlocal first_byte_at
        try:
            for chunk in primed:
                if first_byte_at is None:
                    first_byte_at = time.monotonic() - started
                yield chunk
            async for chunk in source:
                if await request.is_disconnected():
                    log.info("client disconnected; abandoning generation")
                    break
                yield chunk
        except Exception:
            # Past the first byte the status code is already sent, so this can
            # only be logged. The player detects the short stream and says so.
            log.exception("audio stream failed mid-flight")
        finally:
            # The two times that only exist once the episode is over, in the
            # same plain form as the per-step list written at first audio.
            if stats.cache != "hit":
                wrote = stats.marks.span("claude_start", "claude_complete")
                log.info(
                    "episode finished q=%r\n  writer finished writing      %s\n"
                    "  whole request, start to end  %.2fs",
                    plan.query,
                    "did not finish" if wrote is None else f"{wrote:.2f}s",
                    time.monotonic() - started)
            log.info(
                "episode q=%r %s wall=%.1fs preroll=%.2fs chunks_primed=%d "
                "audio_primed=%.2fs first_pcm=%s preroll_satisfied=%s "
                "first_byte=%s marks=%s",
                plan.query, stats.as_dict(), time.monotonic() - started,
                PREROLL_SECONDS, chunks_primed, primed_seconds,
                _ms(first_pcm_at), _ms(preroll_at), _ms(first_byte_at),
                json.dumps(stats.marks.to_dict(), default=str),
            )
            # The ledger row, written last, when the numbers are final.
            #
            # Here and not at the model call because this is the only place
            # that knows *whose* episode it was - and the id comes from
            # `_listener(request)`, the session cookie, never a parameter,
            # which is the settled rule for anything per-listener.
            #
            # A disconnect mid-stream still records: the money was spent
            # whether or not it was listened to, and a ledger that only counts
            # completed plays under-reports exactly the abusive pattern of
            # starting many episodes and finishing none.
            _record_usage(
                user, stats.usage,
                surface=_surface(cached_only, topic_id, context),
                minutes=plan.minutes, audio_seconds=stats.voiced_seconds,
                cache_hit=stats.cache == "hit",
            )

    # Recorded here rather than client-side: audio is being served, so the
    # play is a fact. A dropped event costs one weak signal, never the episode.
    # Both writes sit after the plan and the pipeline are ready and before the
    # response object is built, so neither is in front of the first word.
    # A guest's play is served and not remembered (§127): nothing the
    # algorithm learns is kept anywhere but an account.
    if user and _remembers(request):
        SOCIAL.seen(user)
        EVENTS.record(
            topics_mod.Event(
                user, "play", topic_id, plan.query,
                # Through `tags_for_id`, which is the one definition of where
                # a tile's tags live. This used to read the bank directly and
                # fall through to the words of the question, which quietly
                # skipped the other three inventories - the startup set is the
                # case that makes it matter, because a cold start's *first*
                # play is the one event that decides whether the ranker ever
                # learns anything, and those queries carry none of the
                # keywords their facet is matched on.
                topics_mod.tags_for_id(topic_id, plan.query),
            )
        )

    media_type = "audio/wav" if fmt == "wav" else "audio/L16"
    return StreamingResponse(
        body(),
        media_type=media_type,
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",  # tell nginx not to buffer the stream
            "X-Sample-Rate": str(sample_rate),
            "X-Requested-Seconds": str(plan.target_seconds),
            # Measurement headers. Additive: the player reads none of them,
            # and `tools/preroll_sweep.py` reads all of them.
            "X-Preroll-Seconds": f"{PREROLL_SECONDS:g}",
            "X-Chunks-Primed": str(chunks_primed),
            "X-Audio-Primed-Seconds": f"{primed_seconds:.3f}",
            "X-First-PCM-Seconds": f"{first_pcm_at:.4f}" if first_pcm_at is not None else "",
            "X-Preroll-Satisfied-Seconds": f"{preroll_at:.4f}" if preroll_at is not None else "",
            # The same breakdown the `stages` log line prints, for a client
            # that can read headers - `tools/pod_episode.py` does. Complete by
            # now: every stage ends at or before the first synthesis, which is
            # what the preroll above waited for.
            "X-Stage-Seconds": json.dumps(
                {k: round(v, 3) for k, v in stats.marks.stages().items()},
                separators=(",", ":")),
            # The episode's own marks, so a client-side probe can read the
            # server's view of the same request rather than inferring it.
            "X-Episode-Marks": json.dumps(stats.marks.summary(), default=str),
        },
    )


#: The credential that gates the usage report. Absent by default, and absent
#: means the endpoint does not exist rather than that it is open: this data is
#: every listener's spending history, and an endpoint that is protected only
#: when somebody remembers to protect it is not protected.
ADMIN_TOKEN = os.environ.get("FAM_ADMIN_TOKEN", "").strip()


def _require_admin(request: Request) -> None:
    """Constant-time check of the admin credential, or a 404.

    404 rather than 401: an unconfigured deployment should not advertise that
    it has a billing endpoint at all, and a wrong token should not tell the
    person holding it that they got the path right.
    """
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=404, detail="Not found")
    sent = (request.headers.get("x-admin-token")
            or request.headers.get("authorization", "").removeprefix("Bearer ").strip())
    if not sent or not hmac.compare_digest(sent, ADMIN_TOKEN):
        raise HTTPException(status_code=404, detail="Not found")


@app.get("/api/usage")
async def usage(
    request: Request,
    days: float = Query(30.0, gt=0, le=3650, description="Window, ending now"),
    top: int = Query(10, ge=1, le=100, description="How many top listeners"),
    flagged: bool = Query(False, description="Also run the abuse thresholds"),
) -> dict:
    """The billing and usage report, on demand.

    Everything `tools/usage_report.py` prints, as JSON, so the same numbers are
    available to a dashboard, a finance spreadsheet and a person at a terminal
    without three implementations disagreeing about what a month is.

    Not paced by `_rate_limit`: it makes no model call, and an operator pulling
    a report should not be competing with listeners for the generation budget.
    """
    _require_admin(request)
    now = time.time()
    report = METER.report(since=now - days * 86400, until=now, top=top)
    if flagged:
        report["flagged"] = metering.suspects(METER)
    return report


class WipeRequest(BaseModel):
    """What to remove. Nothing is removed unless `dry_run` is explicitly false."""

    scope: str = Field("seed", pattern="^(seed|all)$")
    #: Defaults to a dry run, on purpose and in both directions. This is not
    #: reversible, and a request body that forgot a field must not be the one
    #: that empties the event log.
    dry_run: bool = True


@app.post("/api/admin/wipe")
async def admin_wipe(req: WipeRequest, request: Request) -> dict:
    """Take the demonstration data out of a running deployment.

    Behind `FAM_ADMIN_TOKEN`, 404 without it, like `/api/usage` and for the
    stronger version of the same reason: that one hands over every listener's
    spending, this one deletes things.

    **It exists because the place it is needed has no shell.** A container
    host is exactly where seeded data ends up stranded - somebody ran
    `seed_demo.py` so the browse surfaces had something to show, and the
    measurement they now want is impossible while three invented listeners
    are voting in the taste model. The alternative to this endpoint is not
    "do it more carefully", it is redeploying with a wiped disk, which takes
    the real listeners with it.

    The work is `demo_data.wipe`, which `tools/wipe_demo_data.py` also calls,
    so a command line and an HTTP call cannot come to mean two different
    things. See that module for what each scope removes and what neither
    touches (accounts, credentials, the metering ledger).
    """
    _require_admin(request)
    import demo_data

    report = demo_data.wipe(cache=SCRIPT_CACHE, events=EVENTS,
                            erase_listener=erase_listener,
                            scope=req.scope, dry_run=req.dry_run)
    if not req.dry_run:
        # Loud, and in the server's own log, because this is the one
        # destructive operation the app offers and "why is the feed empty"
        # is a question somebody will ask later.
        log.warning("ADMIN: wiped %s - %s", req.scope, report)
    return report


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    # Headers are forwarded, not dropped. A 429 from the quota carries the
    # whole verdict in `X-FAM-Quota` - what the limit was, what is left, when
    # it resets - and a handler that kept only the sentence would leave the
    # interface able to say "no" and nothing else.
    headers = getattr(exc, "headers", None) or {}
    body = {"error": exc.detail}
    # The same verdict in the body as well as the header, because a header is
    # the one part of a response a client routinely cannot reach: `fetch`
    # hides it cross-origin without `expose_headers`, and every wrapper that
    # turns a failed response into an exception keeps the body and drops the
    # rest. The limit screen needs the numbers, so they travel where they
    # cannot be lost.
    if headers.get("X-FAM-Quota"):
        try:
            body["quota"] = json.loads(headers["X-FAM-Quota"])
        except Exception:  # noqa: BLE001 - a malformed header must not mask the error
            log.exception("could not attach the quota verdict to a refusal")
    if headers.get("X-FAM-Refused-By"):
        body["refused_by"] = headers["X-FAM-Refused-By"]
    return JSONResponse(body, status_code=exc.status_code, headers=headers or None)


# Also resolved from the project root, and for the same reason as the
# databases: a relative directory follows the working directory. This one
# at least fails loudly - starting the server from anywhere else raised
# "Directory 'static' does not exist" - but it made the app impossible to
# launch from outside its own folder, which is how the quiet database
# version of this bug stayed hidden behind it.
app.mount("/", StaticFiles(directory=str(PROJECT_ROOT / "static"), html=True),
          name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host=settings.host, port=settings.port, reload=False)
