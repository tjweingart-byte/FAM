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
import gzip
import base64
import dataclasses
import hmac
import html
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
    HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse)
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers, MutableHeaders
from pydantic import BaseModel, Field, field_validator

from anthropic_client import build_async_client, describe_http_version, http2_enabled
import audio_codec as audio_codec_mod
import db as db_mod
import audio_store as audio_store_mod
import cache as cache_mod
from cache import (MemoryScriptCache, SqliteScriptCache, build_cache, cache_key,
                   is_shareable, normalize_query, parse_episode_id, research_words)
import content_filter
import embeddings
import learned_rank
import taste_vectors
from demo_script import DemoGenerator
import credentials
import client_versions
import entitlements
import spend_guard
import messages as messages_mod
import typing_indicator as typing_mod
import metering
import oauth
import quotas
import feedback as feedback_mod
import consent as consent_mod
import image_check
import moderation as moderation_mod
import saved as saved_mod
import sharing
from config import (BROWSE_MINUTES, DEFAULT_MINUTES, DEFAULT_PIPELINE, describe_key,
                    key_source, settings)
import prefetch
import prefetch_sources
from episode_intelligence import report as ei_report
import live_captions
import live_sources
from gdelt import report as gdelt_report
import provenance as provenance_mod
import stories as stories_mod
import trending as trending_mod
import trending_bank
import daily_edition
from live_facts import report as live_facts_report
from research import NoEvidence, ResearchUnavailable, report as research_report
import local_news as local_news_mod
import weather as weather_mod
from pipeline import GenerationStats, NotCached, PodcastPipeline
from script_generator import ScriptGenerator, ScriptNotes, plan_episode
import attachments as attachments_mod
import autocorrect as autocorrect_mod
import categories as categories_mod
import topics as topics_mod
import thumbnails as thumbnails_mod
import accounts as accounts_mod
import viral_loops as viral_loops_mod
import waitlist as waitlist_mod
from paths import PROJECT_ROOT
import mixes as mixes_mod
import mail as mail_mod
import push as push_mod
import listener_clock
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
    production_engines,
    warm_up,
)
import pronunciation
import voice_bank

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


async def _verify_small_models() -> None:
    """Ask the same question of the models the small calls run on (§227).

    Until §227 every call ran on `settings.model`, so `_verify_credentials`
    covered them all. The brief, composer, placer and thumbnail calls now
    default to `config.SMALL_MODEL`, and an account that cannot use it would
    turn every brief into the raw-query fallback - each one logged, none of
    them said at boot. A model that fails here is reported in
    `/api/health` (`credentials.models`) and the log; nothing else changes,
    because every one of those calls already falls back on its own.
    """
    if CREDENTIALS.get("state") != "ok":
        return
    small = sorted({settings.ei_model, settings.stories_model,
                    settings.categories_model, settings.thumbnails_model}
                   - {settings.model})
    report: dict[str, str] = {}
    for model in small:
        try:
            await build_async_client().models.retrieve(model)
            report[model] = "ok"
        except Exception as exc:  # noqa: BLE001 - the report matters, not the type
            import anthropic

            # `friendly_error` names the writer's model on a 404; this is not it.
            report[model] = (f"The model {model!r} is not available to this account."
                             if isinstance(exc, anthropic.NotFoundError)
                             else friendly_error(exc))
            log.error("MODEL UNAVAILABLE - %s: %s. The calls on it fall back "
                      "(the brief searches the raw query, tiles are templated); "
                      "set EI_MODEL / STORIES_MODEL / CATEGORIES_MODEL / "
                      "THUMBNAILS_MODEL to a model this key can use.",
                      model, report[model])
    CREDENTIALS["models"] = report


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

    Since §191 a tick spends only on what is due: the news and Finnhub every
    two hours, and API-Sports only when somebody drew myFAM recently and a
    followed game is on (`ApiSportsSignals.idle`). A tick with nothing due
    asks nobody.

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
        tree = topics_mod.category_tree()
        # §235: what the writer filed two or more episodes under that the
        # tree could not place. Re-read by the exact reader only - a near
        # placement (`near`) is a stand-in until the tree holds the words.
        written = await asyncio.to_thread(
            categories_mod.writer_subjects, tree,
            lambda words: stories_mod.resolve_category(words, near=False))
        result = await categories_mod.sweep(
            tree,
            EVENTS.subject_texts(since),
            always=subjects,
            written=written,
        )
        if result.get("minted") or result.get("pruned"):
            log.info("categories: %s", result)
        # Pictures for whatever the vocabulary now holds that has none
        # (§160). After the sweep rather than beside it, so a node minted
        # this pass is painted this pass; inside the daily ceiling; and
        # awaited here because this whole function is already a background
        # task no request waits on.
        if settings.thumbnails and thumbnails_mod.configured()[0]:
            painted = await thumbnails_mod.backfill(settings.thumbnails_per_sweep)
            if painted.get("wanted") or painted.get("skipped") or painted.get("errors"):
                log.info("thumbnails: %s", {k: v for k, v in painted.items()
                                            if k != "nodes"})
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


def _daily_edition_report() -> dict:
    """`daily_edition.report()`, never able to break the health page."""
    try:
        return daily_edition.report()
    except Exception as exc:  # noqa: BLE001
        return {"enabled": bool(settings.daily_edition), "error": str(exc)}


#: The writer the DailyFAM edition and a freshly saved mix use. None in demo
#: mode, where nothing may be written into the shared cache (§51).
_EDITION_WRITER = None


def _write_mix_ahead(mix, before=None) -> None:
    """A mix was saved: write the subjects it gained that today's edition
    has not, in the background (§143). `before` is the mix as it was, so an
    edit that only removes or reorders spends nothing. Never awaited, never
    raises."""
    try:
        daily_edition.schedule_mix(mix, generator=_EDITION_WRITER,
                                   cache=SCRIPT_CACHE, before=before)
    except Exception:  # noqa: BLE001 - a save must not fail on a guess
        log.exception("daily edition: could not schedule a saved mix")


def _trending_bank_report() -> dict:
    """`trending_bank.report()`, never able to break the health page."""
    try:
        return trending_bank.report()
    except Exception as exc:  # noqa: BLE001 - health must always answer
        return {"enabled": bool(settings.trending_bank), "error": str(exc)}


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Whether zero spend's network guard can see this loop's connections
    # (§172) - uvloop goes round it, so staging runs UVICORN_LOOP=asyncio.
    spend_guard.check_loop(asyncio.get_running_loop())
    # Pay the voice model's load cost now rather than on the first listener.
    await warm_up()
    # The autocorrect word list, likewise (§142) - in a thread and not
    # awaited, so it costs startup nothing and the first word typed nothing.
    asyncio.get_running_loop().run_in_executor(None, autocorrect_mod.warm)
    # Before a listener finds out the hard way.
    await _verify_credentials()
    await _verify_small_models()
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
    # The local news collector (§194): polls only the outlets of places
    # somebody has asked about, so on a fresh deployment it does nothing
    # until the first local question. Never awaited.
    if settings.local_news:
        _BACKGROUND.add(asyncio.create_task(local_news_mod.run_forever()))
    # Weather's twice-daily sweep (§194): only places somebody asked about,
    # each at 05:00 and 17:00 in its own time. Nothing until the first ask.
    if settings.weather:
        _BACKGROUND.add(asyncio.create_task(weather_mod.run_forever()))
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
    # GDELT's export files (§211): the one job that downloads from GDELT,
    # once every fifteen minutes whatever the traffic. Everything else reads
    # its copy on disk. Started before the warming sweep, which reports the
    # GDELT source idle until the first file has landed.
    if settings.gdelt:
        import gdelt as gdelt_mod

        _BACKGROUND.add(asyncio.create_task(gdelt_mod.run_forever()))
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
    # Trending's edition: built at 05:00 and 17:00 Eastern from GNews and
    # nothing else, its ten episodes written into the shared cache before
    # anybody taps (§139). On boot it catches up - a slot with no edition is
    # built at once - so a new deployment does not wait for 5pm. Never
    # awaited; until the first edition lands the row is empty and says so.
    # The same loop writes the eight "Start here" questions at each slot, so
    # their cards name what they are about before anybody taps (9.29 packet).
    if settings.trending_bank or settings.startup_write_ahead:
        _BACKGROUND.add(asyncio.create_task(trending_bank.run_forever(
            generator=None if DEMO_MODE else ScriptGenerator(),
            cache=SCRIPT_CACHE,
            initial_delay=settings.boot_stagger_seconds)))
    # DailyFAM's edition (§143): every mix's episodes written before anybody
    # taps, at 05:00 Eastern, one per distinct subject, EI on every one. On
    # boot it catches up, so a new deployment writes today's at once. Never
    # awaited; until it lands a tap writes its own episode as it always did.
    global _EDITION_WRITER
    _EDITION_WRITER = None if DEMO_MODE else ScriptGenerator()
    if settings.daily_edition:
        _BACKGROUND.add(asyncio.create_task(daily_edition.run_forever(
            MIXES, generator=_EDITION_WRITER, cache=SCRIPT_CACHE,
            initial_delay=2 * settings.boot_stagger_seconds)))
    # "Your mix is ready" at each mix's listen time, once its edition is
    # written (`push.py`). Started only where it can deliver; a server
    # without keys logs why once and keeps every listen time for later.
    push_ready = push_mod.status()
    if push_ready["available"]:
        _BACKGROUND.add(asyncio.create_task(push_mod.run_forever(
            MIXES, PUSH, allowed=_may_be_notified)))
    else:
        log.info("mix notifications: off - %s", push_ready["reason"])
    # How the voice is found, and a loop that keeps that answer fresh. Both
    # are no-ops unless VOICE_BACKEND=remote: an in-process card is not
    # somewhere that can move.
    _announce_voice_control()
    if settings.voice_backend == "remote" and settings.voice_supervise_seconds > 0:
        _BACKGROUND.add(asyncio.create_task(_supervise_voice()))
    # The Viral Loops outbox (WAITLIST.md): whatever failed or waited for keys
    # is retried here. A no-op with no token, which is every staging deploy.
    if VIRAL_LOOPS.configured:
        _BACKGROUND.add(asyncio.create_task(_drain_viral_loops_forever()))
    # Kept audio's week (§237): keep what is saved, shared or vibed, delete
    # the rest, and say whether the bucket really answers - with a write, a
    # read and a delete, never by looking at the settings.
    if hasattr(SCRIPT_CACHE, "sweep_audio"):
        _BACKGROUND.add(asyncio.create_task(_sweep_audio_forever()))
    if audio_store_mod.get_store() is not None:
        # Not awaited: a bucket that is slow to answer must not hold up boot.
        _BACKGROUND.add(asyncio.create_task(_check_audio_store()))
    elif audio_store_mod.status().get("error"):
        log.error("audio store: %s", audio_store_mod.status()["error"])
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
        ("feedback", "FEEDBACK_DB", FEEDBACK.path),
        ("consent", "CONSENT_DB", CONSENT.path),
        ("moderation", "MODERATION_DB", MODERATION.path),
        # The grown ranking vocabulary. Reported like the rest rather than
        # lazily like the voice registry below: `category_tree()` opens it on
        # the first feed, every deployment has one, and a tree silently living
        # inside the image is a vocabulary that resets on every push - which
        # from outside looks exactly like one that had never grown.
        ("categories", "CATEGORIES_DB",
         getattr(topics_mod.category_tree(), "path", "")),
    ]
    # Trending's editions (§139). Holds the GNews request ledger as well as
    # the editions, so an image-local copy would reset the daily ceiling on
    # every push as well as the rail. Reported once it exists, like the voice
    # registry below: the first build creates it, and a health page must not
    # be what creates a database on a machine that never built an edition.
    if settings.trending_bank and trending_bank._exists():
        try:
            stores.append(("trending bank", "TRENDING_BANK_DB",
                           trending_bank.store().path))
        except Exception:  # pragma: no cover - a report is never load-bearing
            pass
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
    # The bank of voices (§147): the recordings and every listener's choice.
    # Opened by the first request that asks which voice to use, and reported
    # from then on, on the same terms as the registry above.
    held_bank = voice_bank.opened()
    if held_bank is not None:
        stores.append(("voice bank", "VOICE_BANK_DB", held_bank.path))
    # Tile pictures (§160). Reported once the first picture has been painted,
    # like the trending bank: a health page must not be what creates the
    # database on a machine that never made one.
    if thumbnails_mod._exists():
        try:
            stores.append(("thumbnails", "THUMBNAILS_DB",
                           thumbnails_mod.store().path))
        except Exception:  # pragma: no cover - a report is never load-bearing
            pass
    # Requests per outside service per day (§179). Created by the first
    # request that goes out, and reported once it exists, like the bank above.
    try:
        import provider_usage

        if provider_usage.exists():
            stores.append(("provider usage", "PROVIDER_USAGE_DB",
                           provider_usage.store().path))
    except Exception:  # pragma: no cover - a report is never load-bearing
        pass
    # Local news outlets, their stories and resolved places (§194). Created
    # by the collector's first sweep, and reported once it exists, like the
    # provider counts above.
    try:
        if local_news_mod.exists():
            stores.append(("local news", "LOCAL_NEWS_DB",
                           local_news_mod.store().path))
    except Exception:  # pragma: no cover - a report is never load-bearing
        pass
    # GDELT's export copy (§211). Created by the first download, and reported
    # once it exists, like the stores above.
    try:
        import gdelt as gdelt_mod

        if gdelt_mod.exists():
            stores.append(("GDELT export copy", "GDELT_EXPORT_DB",
                           gdelt_mod.store().path))
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
        if path and db_mod.enabled():
            # §243: the store is a schema in Postgres, which a redeploy does
            # not touch; the path only names the schema. Readable and
            # writable are what a real query just said (`db.probe`, at most
            # once every ten seconds), never assumed from the configuration.
            answer = db_mod.probe()
            entry["readable"] = bool(answer.get("reachable"))
            entry["writable"] = bool(answer.get("reachable") and answer.get("can_create"))
            if not answer.get("reachable"):
                entry["error"] = answer.get("error", "the database did not answer")
            entry["persistence"] = "database"
            entry["schema"] = db_mod.schema_for(path)
            report.append(entry)
            continue
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


def _memory_report() -> dict:
    """Resident memory now and at its peak, in MB, read from the kernel.

    `/proc/self/status` (Linux, which is every deployment) gives both; else
    `resource` gives the peak alone. `None` for what cannot be read - never a
    0 that looks like a measurement."""
    now = peak = None
    try:
        with open("/proc/self/status", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith(("VmRSS:", "VmHWM:")):
                    mb = round(int(line.split()[1]) / 1024, 1)
                    if line.startswith("VmRSS:"):
                        now = mb
                    else:
                        peak = mb
    except OSError:
        try:
            import resource

            peak = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
        except Exception:  # noqa: BLE001 - a health field never breaks health
            pass
    return {"rss_mb": now, "peak_mb": peak}


def _storage_summary(databases: list[dict]) -> dict:
    """One sentence's worth of "will a redeploy erase this".

    Named separately from the per-database list because the question is asked
    about the deployment, not about a file: somebody looking at this wants to
    know whether their listeners' accounts survive the next push, and counting
    twelve entries by hand to find out is how the answer gets skipped.
    """
    at_risk = [d["name"] for d in databases if d.get("persistence") == "image"]
    unknown = [d["name"] for d in databases if d.get("persistence") == "unknown"]
    if databases and all(d.get("persistence") == "database" for d in databases):
        return {"durable": [d["name"] for d in databases], "ephemeral": [],
                "unknown": [], "note": "Every store is in Postgres (DATABASE_URL, "
                "§243), which a redeploy does not touch."}
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


#: How often a limiter may look for clients that have gone away (§241).
LIMITER_PRUNE_EVERY = 60.0
_last_pruned: dict = {}


def _prune(store: dict, now: float, stamp_of) -> None:
    """Forget clients that have gone away, so these dicts stay bounded.

    At most once a minute per dict (§241). It used to scan every client
    whenever one came back after a quiet ten seconds - and past 512 listeners,
    all of them active, a scan found nothing to forget and ran again on the
    next request: 7% of the server's time at a thousand listeners, growing
    with the square of them. Anyone idle for LIMITER_IDLE_SECONDS is still
    forgotten, a minute later at most.
    """
    if len(store) < 512:
        return
    if now - _last_pruned.get(id(store), float("-inf")) < LIMITER_PRUNE_EVERY:
        return
    _last_pruned[id(store)] = now
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
    # An admin account is never refused by its own allowance (§207): the
    # people who test every surface every day would otherwise hit the free
    # ceiling by lunchtime the day quotas are switched on. Named by
    # `FAM_ADMIN_ACCOUNTS`, on the server - never by anything the client says.
    if _allowed_admin(listener):
        return "unlimited"
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


class _VoiceChoices:
    """The voice bank's per-listener half, opened only when erased from, so a
    bank that cannot open fails its own row rather than the whole erase."""

    @staticmethod
    def forget(user_id: str) -> int:
        return voice_bank.bank().forget(user_id)


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
    * **The shared script cache keeps every episode, and loses the name.**
      Other listeners' Explore feeds must not develop holes because somebody
      left, so no script is deleted - but `scripts.author` held this
      listener's id as provenance, and until §208 it outlived the account.
      It is cleared (`anonymise_author`), leaving the episodes unattributed.

    Returns a per-store count so the endpoint reports what it did. Each store
    is attempted independently: a failure in one must not leave the other six
    undeleted, which would be the worst outcome available here - a deletion
    that half happened and reported success.
    """
    removed: dict[str, int] = {}
    for name, store in (("events", EVENTS), ("mixes", MIXES), ("social", SOCIAL),
                        ("preferences", PREFS), ("attachments", ATTACHMENTS),
                        ("quotas", QUOTAS), ("messages", MESSAGES),
                        ("saved", SAVED), ("shares", SHARES),
                        ("voice_choice", _VoiceChoices()), ("push", PUSH),
                        ("consent", CONSENT), ("moderation", MODERATION)):
        try:
            removed[name] = store.forget(user_id)
        except Exception:
            log.exception("could not erase %s for %r", name, user_id)
            removed[name] = -1
    # Bug reports outlive the account that filed them, like the ledger: the
    # link to the person goes and the report stays (`feedback.py`).
    try:
        removed["feedback_anonymised"] = FEEDBACK.forget(user_id)
    except Exception:
        log.exception("could not anonymise feedback for %r", user_id)
        removed["feedback_anonymised"] = -1
    try:
        removed["usage_rows_anonymised"] = METER.anonymise(user_id)
    except Exception:
        log.exception("could not anonymise usage for %r", user_id)
        removed["usage_rows_anonymised"] = -1
    try:
        store = SCRIPT_CACHE if SCRIPT_CACHE is not None else build_cache()
        anonymise = getattr(store, "anonymise_author", None) if store else None
        removed["scripts_unattributed"] = anonymise(user_id) if anonymise else 0
    except Exception:
        log.exception("could not clear authorship for %r", user_id)
        removed["scripts_unattributed"] = -1
    try:
        removed["waitlist"] = WAITLIST.forget(user_id)
    except Exception:
        log.exception("could not erase waitlist rows for %r", user_id)
        removed["waitlist"] = -1
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
        return _cache_holds(key)
    except Exception:
        # A limiter must never be the thing that takes the app down, and
        # "assume it will generate" is the conservative answer.
        log.exception("cache probe failed; pacing this request as a generation")
        return False


def _cache_holds(key: str) -> bool:
    """Whether the cache would serve `key` to a new request - without reading
    it. `get` loads the whole script, scrubs every sentence and counts a hit;
    myFAM asked it that of every candidate tile on every draw, which made the
    page 38% of the server's time at a thousand listeners (§241). A cache
    without `holds` (a test's) is asked the old way."""
    holds = getattr(SCRIPT_CACHE, "holds", None)
    if holds is not None:
        return bool(holds(key))
    return SCRIPT_CACHE.get(key) is not None


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
        # Which deployment this is and whether it can spend (§172). The web
        # client draws the STAGING banner from this.
        "environment": spend_guard.report(),
        # The client releases this server still serves, and which versions
        # have called it since boot (`client_versions.py`).
        "clients": client_versions.report(),
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
        # Whether profile pictures and mix covers are being checked (§224).
        "image_check": image_check.report(),
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
        # §194: the local news collector and the weather providers.
        "local_news": local_news_mod.report(),
        "weather": weather_mod.report(),
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
        "trending_bank": _trending_bank_report(),
        # Tile pictures (§160): whether the sweep paints, whether it could,
        # and how many pictures are live, waiting for a person, or failed.
        "thumbnails": thumbnails_mod.health(),
        "daily_edition": _daily_edition_report(),
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
        # Whether every licensed provider in use may be used commercially
        # (§207): GNews' and Finnhub's free plans and Open-Meteo's keyless
        # endpoint may not. The question to ask before charging anybody.
        "licences": _licences_report(),
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
        # The two writer savings (§179), with where each came from: either
        # left at 0 in a dashboard would quietly undo it on every push.
        "writer_savings": {
            "prompt_cache": settings.prompt_cache,
            "prompt_cache_ttl": settings.prompt_cache_ttl,
            "prompt_cache_source": ("PROMPT_CACHE env var"
                                    if os.environ.get("PROMPT_CACHE", "").strip()
                                    else "config.py default"),
            "edition_batch": settings.edition_batch,
            "edition_batch_wait_seconds": settings.edition_batch_wait_seconds,
            "edition_batch_source": ("EDITION_BATCH env var"
                                     if os.environ.get("EDITION_BATCH", "").strip()
                                     else "config.py default"),
        },
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
        # §243: where the stores live - SQLite files, or Postgres when
        # DATABASE_URL is set (never the address).
        "database": db_mod.report(),
        # How much memory this process holds now and has ever held (§245):
        # Render restarts a service over its plan's limit, and without these
        # a restart cannot be told from a leak, a spike or a plan too small.
        "memory": _memory_report(),
        # Settable in the dashboard and in the environment, so said here
        # (WAITLIST.md): whether the app is closed to all but active accounts,
        # and whether the vendor is being told.
        "waitlist": {"gate": settings.waitlist,
                     "viral_loops": VIRAL_LOOPS.configured,
                     "outbox_pending": WAITLIST.outbox_summary()["pending"]},
        "voice_store": VOICE_STORE["dir"],
        # Where kept audio lives and how it is packed (§237): the bucket, the
        # boot check's real write/read/delete, the codec, and the last sweep.
        "audio": {**audio_store_mod.status(), **audio_codec_mod.describe(),
                  "check": dict(_AUDIO_STORE_CHECK),
                  "sweep": dict(_AUDIO_SWEEP)},
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
        # "Your mix is ready" (push.py): whether this server can deliver, and
        # if not, the sentence the mix page shows.
        "mix_notifications": {k: v for k, v in push_mod.status().items()
                              if k != "public_key"},
        # Email (mail.py, §244): whether password reset can send, how many
        # sends worked and failed, and the last failure's words.
        "mail": mail_mod.report(),
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
    #: The invite code a waitlist link carried (`?referralCode=`). Read on
    #: sign-up only, and only while the account is waitlisted.
    referral_code: str = Field("", max_length=64)
    #: The sign-up checkbox: "I agree to the Terms and the Privacy Policy"
    #: (clickwrap, §228). Required on sign-up from a client that draws it.
    accept_terms: bool = False
    #: Native clients only. See `_maybe_token` - a browser must never ask for
    #: this, because reading the token in script is precisely what the HttpOnly
    #: cookie exists to prevent.
    want_token: bool = False


class ProviderRequest(BaseModel):
    """A verified identity token from Google or Apple."""

    provider: str = Field(..., max_length=16)
    id_token: str = Field(..., max_length=8192)
    #: Ticked before a Google or Apple sign-in that may create an account
    #: (§228). An account made without it is asked on its first screen.
    accept_terms: bool = False
    #: The invite code a waitlist link carried; see CredentialsRequest.
    referral_code: str = Field("", max_length=64)
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
    #: `YYYY-MM-DD`, from the waitlist's profile (10.2 packet).
    birth_date: Optional[str] = Field(None, max_length=10)


class NewPasswordRequest(BaseModel):
    """Setting a first password, for an account created with Google or Apple.
    `PasswordChangeRequest` cannot serve this: there is no current password to
    prove, and asking for one would lock those accounts out of ever having
    one."""

    new: str = Field(..., max_length=accounts_mod.MAX_PASSWORD)


class PasswordChangeRequest(BaseModel):
    current: str = Field(..., max_length=accounts_mod.MAX_PASSWORD)
    new: str = Field(..., max_length=accounts_mod.MAX_PASSWORD)


class ResetStartRequest(BaseModel):
    """"Forgot password?": the address to send a code to (§244)."""

    email: str = Field("", max_length=accounts_mod.MAX_EMAIL)


class ResetFinishRequest(BaseModel):
    """The emailed code and the password to set with it (§244)."""

    email: str = Field("", max_length=accounts_mod.MAX_EMAIL)
    code: str = Field("", max_length=16)
    new: str = Field(..., max_length=accounts_mod.MAX_PASSWORD)
    #: Native clients only; see CredentialsRequest.
    want_token: bool = False


@app.get("/api/auth/me")
async def auth_me(request: Request) -> dict:
    """Who the server thinks is asking. Cheap, and the only way to find out -
    the id is not in the page's reach, which is the point of the cookie."""
    listener = getattr(request.state, "listener", None)
    if listener is None:
        return {"user_id": "", "email": "", "authenticated": False,
                "waitlist": settings.waitlist, "home": settings.app_home_url}
    return {**listener.as_dict(), "waitlist": settings.waitlist,
            "home": settings.app_home_url}


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


def _signup_listener(request: Request) -> tuple[str, bool]:
    """The identity a sign-up attaches credentials to, and whether it is new.

    Normally the one this browser already carries, so a guest's session is
    claimed rather than replaced. **But an account belongs to its credentials,
    never to the device** (PROBLEMS.md §190): a browser signed in to one
    account - or still holding the session of one - must be able to create
    another. Refusing with "this listener already has an account" tied the
    second account to the machine the first was made on, which is what the
    owner hit joining the waitlist from a computer with two fresh addresses.
    So when the session's id already has an account, the new one gets a
    freshly minted id; `_signup_session` moves this browser onto it only once
    the sign-up has succeeded, so a refused address logs nobody out.
    """
    user = _require_listener(request)
    if not ACCOUNTS.account(user):
        return user, False
    return accounts_mod.new_listener_id(), True


def _signup_session(request: Request, user_id: str, fresh: bool,
                    want_token: bool) -> dict:
    """The session half of a successful sign-up, as `_maybe_token` returns it.

    A fresh id moves the browser onto it as a login does: a new session, and
    the one it replaces (the other account's, on this device) ended. The
    other account is untouched and its credentials still log in. Otherwise the
    current session stays: nothing about signing up should log out the tab
    that did it, and a native client is handed a token it can store.
    """
    token = _session_token(request)
    if fresh:
        old = token
        token, _ = ACCOUNTS.new_session(user_id)
        if old:
            ACCOUNTS.end_session(old)
        request.state.set_session = token
    elif want_token and not token:
        token, _ = ACCOUNTS.new_session(user_id)
        request.state.set_session = token
    return _maybe_token(request, token, want_token)


@app.post("/api/auth/signup")
async def auth_signup(req: CredentialsRequest, request: Request) -> dict:
    """Attach an account to the identity this listener already has.

    Not "create a user": they exist already, with a history and possibly mixes
    and echoes. Signing up claims that identity rather than starting a second
    one, which is why nothing has to be migrated.
    """
    _rate_limit(request)
    kind = _signup_identity(req)
    _require_terms(request, req.accept_terms)
    user, fresh = _signup_listener(request)
    try:
        # In a thread: scrypt is ~50 ms of CPU, and on the event loop that is
        # 50 ms in which no other listener is served (§241).
        if kind == "phone":
            listener = await asyncio.to_thread(
                ACCOUNTS.sign_up_phone, user, req.phone, req.password)
        else:
            listener = await asyncio.to_thread(
                ACCOUNTS.sign_up, user, req.email, req.password,
                phone=req.phone or "")
    except accounts_mod.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    listener = _admit_admin(listener)
    if req.accept_terms:
        _record_terms(request, listener.user_id)
    _waitlist_after_signup(listener.user_id, req.referral_code or "")
    listener = ACCOUNTS.listener_of(listener.user_id)
    return {**listener.as_dict(), "admin": _allowed_admin(listener),
            **_signup_session(request, listener.user_id, fresh, req.want_token)}


@app.post("/api/auth/login")
async def auth_login(req: CredentialsRequest, request: Request) -> dict:
    """Verify credentials and move this browser onto that account's identity.

    A fresh session token is minted rather than the current one being
    repointed: reusing it would let a token captured before login keep working
    after it, which is the session-fixation bug.
    """
    _rate_limit(request)
    try:
        # scrypt off the event loop, as at sign-up (§241).
        if _one_identifier(req) == "email":
            listener = await asyncio.to_thread(ACCOUNTS.log_in, req.email, req.password)
        else:
            listener = await asyncio.to_thread(ACCOUNTS.log_in_phone, req.phone,
                                               req.password)
    except accounts_mod.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    listener = _admit_admin(listener)
    old = _session_token(request)
    token, _user_id = ACCOUNTS.new_session(listener.user_id)
    if old:
        # The anonymous session this client was carrying is finished with.
        ACCOUNTS.end_session(old)
    request.state.set_session = token
    return {**listener.as_dict(), "admin": _allowed_admin(listener),
            **_maybe_token(request, token, req.want_token)}


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
    if req.accept_terms:
        _record_terms(request, listener.user_id)
    if is_new:
        _waitlist_after_signup(listener.user_id, req.referral_code or "")
        listener = ACCOUNTS.listener_of(listener.user_id)

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
        await asyncio.to_thread(ACCOUNTS.change_password,
                                _require_listener(request), req.current, req.new)
    except accounts_mod.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    request.state.set_session = ""
    return {"ok": True}


#: What a reset request answers, whether or not the address has an account
#: (§244): anything else would tell a stranger which addresses are registered.
RESET_SENT = ("If that address has a FAM account, a 6-digit code is on its way. "
              "It works for 15 minutes.")


@app.get("/api/auth/reset")
async def auth_reset_status(request: Request) -> dict:
    """Whether this server can send a reset code, and if not, why. The log-in
    screen draws "Forgot password?" only when it can (§244)."""
    _read_limit(request)
    return mail_mod.status()


@app.post("/api/auth/reset/start")
async def auth_reset_start(req: ResetStartRequest, request: Request) -> dict:
    """Email a one-time code to the account at this address (§244).

    The security is in where the code goes: only to the address already on
    the account, so changing somebody's password needs their inbox, not just
    their address. The answer is identical for an address with no account,
    and the mail is sent on its own thread so the time taken is identical
    too. Paced like the other auth endpoints, and the store caps codes per
    account (`accounts.RESET_MAX_CODES`).
    """
    _rate_limit(request)
    ready = mail_mod.status()
    if not ready["available"]:
        raise HTTPException(status_code=503, detail=ready["reason"])
    minted = ACCOUNTS.start_reset(req.email)
    if minted:
        _user_id, address, code = minted
        mail_mod.send_later(
            address, f"{code} is your FAM code",
            f"Your FAM password reset code is {code}.\n\n"
            f"It works for {accounts_mod.RESET_CODE_SECONDS // 60} minutes. "
            "If you did not ask to reset your password, ignore this email - "
            "nothing changes unless the code is used.\n")
    return {"ok": True, "message": RESET_SENT}


@app.post("/api/auth/reset/finish")
async def auth_reset_finish(req: ResetFinishRequest, request: Request) -> dict:
    """Set a new password with the emailed code, and sign this device in.

    Every other session on the account ends (`finish_reset`), as a password
    change does, and the address is told it happened - so a reset the owner
    did not make is not a silent one.
    """
    _rate_limit(request)
    try:
        listener = ACCOUNTS.finish_reset(req.email, req.code, req.new)
    except accounts_mod.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if listener.email:
        mail_mod.send_later(
            listener.email, "Your FAM password was changed",
            "The password on your FAM account was just reset with a code sent "
            "to this address, and every device was signed out.\n\n"
            "If that was not you, reset it again from the log-in screen now.\n")
    listener = _admit_admin(listener)
    old = _session_token(request)
    token, _user_id = ACCOUNTS.new_session(listener.user_id)
    if old:
        ACCOUNTS.end_session(old)
    request.state.set_session = token
    return {**listener.as_dict(), "admin": _allowed_admin(listener),
            **_maybe_token(request, token, req.want_token)}


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
        await asyncio.to_thread(ACCOUNTS.set_password,
                                _require_account(request), req.new)
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
            email=req.email, phone=req.phone, birth_date=req.birth_date)
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
    #: Which shelf: "saved" (Save for Later, the default) or "vibe" (My Vibes).
    kind: str = Field("saved", max_length=8)


class MoveRequest(BaseModel):
    folder_id: str = Field("", max_length=64)


class ShareRequest(BaseModel):
    query: str = Field(..., max_length=sharing.MAX_QUERY)
    minutes: int = Field(DEFAULT_MINUTES, ge=0, le=60)
    title: str = Field("", max_length=sharing.MAX_TITLE)


# --- friends --------------------------------------------------------------

def _in_graph(me: str, other: str) -> bool:
    """Whether either of these two follows the other."""
    return bool(me and other and (SOCIAL.is_following(me, other)
                                  or SOCIAL.is_following(other, me)))


def _reachable(me: str, other: str) -> bool:
    """Whether `me` may find or open `other` (WAITLIST.md §4).

    A waitlisted account is out of discovery - search, handle lookup, follow by
    handle - until it is let in, enforced here rather than in the interface.
    The exception is a friendship that already exists: people already in each
    other's graph keep seeing each other whatever either one's status.
    """
    # A block, either way, and a suspension end reaching somebody whatever
    # the waitlist says (`moderation.py`).
    if MODERATION.is_apart(me, other) or MODERATION.is_suspended(other):
        return False
    if not settings.waitlist:
        # Launch: everybody is findable again, whatever their row still says.
        return True
    if other not in WAITLIST.waitlisted_among([other]):
        return True
    return _in_graph(me, other)


def _without_apart(me: str, people: list[dict], key: str = "user_id") -> list[dict]:
    """`people` without anybody `me` blocked, who blocked `me`, or who was
    suspended (`moderation.py`): to each of them the other is not there."""
    apart = MODERATION.apart(me)
    return [p for p in people if p.get(key) not in apart] if apart else people


SUSPENDED = ("Your account can't post right now because of a report we "
             "upheld. You can still listen.")


def _require_can_post(user: str) -> None:
    """A suspended account keeps listening and posts nothing that reaches
    anybody (`moderation.py`)."""
    if MODERATION.is_suspended(user):
        raise HTTPException(status_code=403, detail=SUSPENDED)


def _episode_target(query: str, minutes) -> str:
    """The moderation key for an episode, from the question as the cache
    matches it (`normalize_query`), so a report filed with what the listener
    typed and a cache row stored lower-cased name the same episode (§226)."""
    return moderation_mod.episode_target(normalize_query(query or ""), int(minutes or 0))


def _episode_removed(viewer: str, query: str, minutes) -> bool:
    """Whether a reviewer took this episode down, or this listener reported it."""
    target = _episode_target(query, minutes)
    return (target in MODERATION.hidden_episodes()
            or target in MODERATION.reported_by(viewer, "episode"))


def _episode_author(query: str, minutes: int) -> str:
    """Who searched this episode, from the shared cache's provenance, or ""."""
    store = SCRIPT_CACHE if SCRIPT_CACHE is not None else build_cache()
    if store is None:
        return ""
    norm = normalize_query(query)
    try:
        for entry in store.recent(TRENDING_SEARCHES_SCAN, origin="search"):
            if (normalize_query(entry.get("query") or "") == norm
                    and int(entry.get("minutes") or 0) == int(minutes)):
                return entry.get("author") or ""
    except Exception:  # noqa: BLE001 - a report is kept without an author
        log.exception("could not read an episode's author for a report")
    return ""


def _drop_removed(viewer: str, topics: list[dict], minutes: int) -> list[dict]:
    """A rail's tiles without any episode a reviewer took down or this
    listener reported (§226) - the crowd rails replay other people's
    searched episodes too, not only Explore. The two sets are read once per
    rail, not once per tile."""
    gone = MODERATION.hidden_episodes() | MODERATION.reported_by(viewer, "episode")
    if not gone:
        return topics
    return [t for t in topics
            if _episode_target(t.get("query") or "", t.get("minutes") or minutes) not in gone]


def _visible_episodes(viewer: str, entries: list[dict]) -> list[dict]:
    """Cached episodes as another listener's shelf shows them: none a reviewer
    took down, none this listener reported, and none searched by somebody
    blocked either way or suspended (`moderation.py`)."""
    hidden = MODERATION.hidden_episodes() | MODERATION.reported_by(viewer, "episode")
    apart = MODERATION.apart(viewer)
    if not hidden and not apart:
        return entries
    return [e for e in entries
            if (e.get("author") or "") not in apart
            and _episode_target(e.get("query") or "", e.get("minutes") or 0) not in hidden]


def _mark_waitlisted(people: list[dict]) -> list[dict]:
    """Label who is still on the waitlist, for "Still on the waitlist"."""
    waiting = (WAITLIST.waitlisted_among(p["user_id"] for p in people)
               if settings.waitlist else set())
    for person in people:
        person["waitlisted"] = person["user_id"] in waiting
    return people


@app.get("/api/friends")
async def friends_read(request: Request) -> dict:
    """Who this listener follows, who follows them, and who does both.

    Mutuals are derived rather than stored, so there is no request-and-accept
    state machine and no way for the two directions to disagree.
    """
    _read_limit(request)
    user = _require_account(request)
    return {
        "following": _mark_waitlisted(_without_apart(user, SOCIAL.following(user))),
        "followers": _mark_waitlisted(_without_apart(user, SOCIAL.followers(user))),
        "friends": _mark_waitlisted(_without_apart(user, SOCIAL.friends(user))),
        "counts": SOCIAL.follow_counts(user),
        # Who followed since this listener last looked. Read here rather than
        # from an endpoint of its own because the interface asks this question
        # at the same moment it asks the others, and a badge is not worth a
        # second round trip.
        "new_followers": _without_apart(user, SOCIAL.new_followers(user)),
        # The subset the popup may still raise: never shown to this listener
        # before (§142). A follower announced once is not announced again on
        # the next open of the app.
        "announce": _without_apart(user, SOCIAL.new_followers(user, unannounced=True)),
    }


class AnnouncedRequest(BaseModel):
    user_id: str = Field(..., max_length=64)


@app.post("/api/friends/announced")
async def friends_announced(req: AnnouncedRequest, request: Request) -> dict:
    """The "started following you" popup or banner for this person was shown.

    Once, and never again (§142): it used to be remembered in page memory, so
    reopening the app raised the latest follower's popup every time.
    """
    _read_limit(request)
    SOCIAL.mark_announced(_require_account(request), req.user_id)
    return {"ok": True}


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
    history. Four things come back, and each one is something the person
    actively decided to show:

    * **public mixes** - a new mix is public by default, and one its owner
      switched to private never appears here;
    * **vibes** - a vibe *is* the act of showing somebody an episode, so a
      list of them is a list of things they chose to publish;
    * **interests they have not hidden** - declared in the first run or in
      Settings, minus anything they turned off in Edit profile;
    * **the place they gave** - city, state, country as typed, under their
      name (10.7 packet); a profile with none shows none.

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
        target = _person_by_handle(handle)
        if target == me:
            target = ""
        if target and not _reachable(me, target):
            target = ""
    # Blocked either way, or suspended: to this listener they are not there.
    if target and (MODERATION.is_apart(me, target) or MODERATION.is_suspended(target)):
        target = ""
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
    added_mixes = MIXES.added_from(me) if me and _has_account(request) else set()
    return {
        # Deliberately no `user_id`: this response is drawn, not acted on, and
        # the follow buttons on that screen already have the id they need from
        # the graph. A listener id the client did not need is a listener id
        # that can be sent back.
        "name": person["name"],
        "handle": person["handle"],
        "avatar": person["avatar"],
        # The E beside a name that swears (§224): marked, never refused.
        "explicit": person.get("explicit", False),
        "joined": person["joined"],
        # With the (+) state each needs: whether this listener has already
        # added it to their own DailyFAM, and whether it is theirs.
        "mixes": [_public_mix(m, me, added_mixes)
                  for m in MIXES.public_for_user(target)],
        # Each vibe carries its subject, read off its own words - the same
        # label a shared episode's chat preview uses.
        # Every vibe, for good (the owner's direction after §161): the 24
        # hours are how long a vibe is a *story* on somebody's face, never
        # how long it stays on their profile.
        "vibes": [dict(e.as_dict(person["name"], person["handle"]),
                       topic=_topic_label(e.query, e.title))
                  for e in SOCIAL.echoes_by(target, limit=12, viewer=me)],
        "vibe_count": len(SOCIAL.echoes_by(target, limit=200, viewer=me)),
        "interests": interests,
        "interest_labels": [row["label"] for row in shown],
        # Where they said they are, under their name (10.7 packet, the
        # owner): a place they put on their profile is something they chose
        # to show, as typed - city, state, country, whichever they gave.
        "location": prefs.location.label,
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
    # Waitlisted accounts are not in the directory at all - friends included:
    # a friend is already on the Friends list, and search is discovery.
    found = SOCIAL.find_people(q, exclude_user=user, limit=40)
    waiting = (WAITLIST.waitlisted_among(p["user_id"] for p in found)
               if settings.waitlist else set())
    found = [p for p in _without_apart(user, found) if p["user_id"] not in waiting][:20]
    following = {p["user_id"] for p in SOCIAL.following(user)}
    for person in found:
        person["following"] = person["user_id"] in following
    return {"people": found}


@app.post("/api/friends/follow")
async def friends_follow(req: FollowRequest, request: Request) -> dict:
    _rate_limit(request)
    user = _require_account(request)
    _require_can_post(user)
    target = req.user_id
    if not target and req.handle:
        target = _person_by_handle(req.handle)
        if target == user:
            target = ""
    if target and not _reachable(user, target):
        target = ""
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
    # A blocked (or blocking, or suspended) person's conversation is gone
    # from the list; a group stays, without their messages (`moderation.py`).
    apart = MODERATION.apart(user)
    if apart:
        inbox = [row for row in inbox
                 if row.get("group") or row.get("with") not in apart]
    known = _decorate(SOCIAL.following(user) + SOCIAL.followers(user))
    for row in inbox:
        sender = row.pop("last_sender", "")
        if row.get("group"):
            # Nothing from somebody this listener cannot see: not counted,
            # and not the preview line (§226).
            if apart:
                row["unread"] = MESSAGES.unread_in(user, row["with"], exclude_senders=apart)
                if sender in apart:
                    row["last"] = {**(row.get("last") or {}), "text": "", "query": "",
                                   "title": "", "kind": "text"}
                    sender = ""
            # A group (10.6 packet #4): its name, or its people's first
            # names, and who said the last thing.
            view = _group_view(row["with"], user, known)
            row.update(name=view["name"], handle="", avatar="",
                       members=view["members"])
            last = row.get("last") or {}
            if sender and sender != user:
                who = known.get(sender) or SOCIAL.person(sender)
                last["from_name"] = _first_name(who)
            if last.get("kind") == "episode":
                last["topic"] = _topic_label(last.get("query") or "", last.get("title") or "")
            continue
        person = known.get(row["with"]) or SOCIAL.person(row["with"])
        row["name"] = person.get("name") or "Someone"
        row["handle"] = person.get("handle") or ""
        row["explicit"] = social_mod.explicit_name(row["name"], row["handle"])
        # Their picture, where they have set one - the list drew initials for
        # everybody, which made a conversation with a face look like one
        # with a stranger (§127). "" means initials, as before.
        row["avatar"] = person.get("avatar") or ""
        # "Shared an episode · Money & markets" - the subject, so a preview
        # says what was shared without printing a whole title into one line.
        last = row.get("last") or {}
        if last.get("kind") == "episode":
            last["topic"] = _topic_label(last.get("query") or "", last.get("title") or "")
    return {"threads": inbox, "unread": _unread_total(user)}


def _unread_total(user: str) -> int:
    """The Messages badge, never counting what this listener cannot open:
    messages from somebody blocked either way or suspended (§226)."""
    return MESSAGES.unread_total(user, exclude_senders=MODERATION.apart(user))


def _first_name(person: dict) -> str:
    name = str((person or {}).get("name") or (person or {}).get("handle") or "").strip()
    return name.split()[0] if name else "Someone"


def _group_view(gid: str, me: str, known: Optional[dict] = None) -> dict:
    """What a group chat draws: its name and its people (10.6 packet #4).

    Members carry a name, handle and picture and **no listener id**: the
    screen draws them and opens a profile by handle, which is all it needs.
    An unnamed group is called by its other people's first names, the way
    every phone names one.
    """
    group = MESSAGES.group(gid)
    known = known or {}
    members = []
    for uid in group.get("members") or []:
        person = known.get(uid) or SOCIAL.person(uid)
        members.append({"name": person.get("name") or "",
                        "handle": person.get("handle") or "",
                        "avatar": person.get("avatar") or "",
                        "me": uid == me})
    others = [m for m in members if not m["me"]]
    name = group.get("name") or ", ".join(
        _first_name(m) for m in others[:4]) + (" +%d" % (len(others) - 4)
                                               if len(others) > 4 else "")
    return {"user_id": gid, "group": True, "name": name or "Group",
            "named": bool(group.get("name")), "handle": "", "avatar": "",
            "members": members}


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
    group = messages_mod.is_group(with_)
    person = SOCIAL.person(with_) if not group else {}
    head = max([m.id for m in thread] + [since])
    # Nothing from somebody blocked either way or suspended, and nothing this
    # listener reported (`moderation.py`). The cursor still moves past them.
    apart = MODERATION.apart(user)
    if not group and with_ in apart:
        thread, person = [], {}
    hidden = MODERATION.reported_by(user, "message")
    if apart or hidden:
        thread = [m for m in thread
                  if m.sender not in apart and str(m.id) not in hidden]
    rows = [m.as_dict(user) for m in thread]
    if group:
        # Who said each thing - a group's bubbles are drawn under a name.
        known = _decorate(SOCIAL.following(user) + SOCIAL.followers(user))
        for m, r in zip(thread, rows):
            if not r["mine"]:
                who = known.get(m.sender) or SOCIAL.person(m.sender)
                r["from"] = {"name": who.get("name") or "",
                             "handle": who.get("handle") or "",
                             "avatar": who.get("avatar") or ""}
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
    if group:
        view = _group_view(with_, user)
        typing = any(typing_mod.is_typing(uid, with_)
                     for uid in MESSAGES.members(with_) if uid != user)
    else:
        view = {"user_id": with_, "name": person.get("name") or "Someone",
                "handle": person.get("handle") or "",
                "avatar": person.get("avatar") or "",
                "explicit": person.get("explicit", False)}
        typing = typing_mod.is_typing(with_, user)
    return {"with": view,
            # Whether they are typing to this listener right now (§127). Read
            # on the same two-second poll that tops the conversation up, so
            # the dots cost no request of their own. In a group, anybody in
            # it but this listener.
            "typing": typing,
            "messages": rows,
            # True for the ordinary open, False for a poll that is topping one
            # up. The client replaces the conversation on one and appends on
            # the other, and guessing from `since` in two places is how those
            # two get out of step.
            "partial": bool(since),
            "head": head}


class SpellRequest(BaseModel):
    #: The words just finished, in order. A handful at most: the box asks as
    #: each word ends, and a paste is corrected a word at a time the same way.
    words: list[str] = Field(..., max_length=40)
    #: Which of those words start a sentence, where a capital is the
    #: keyboard's rather than a name's.
    first: list[bool] = Field(default_factory=list, max_length=40)


@app.post("/api/spell")
async def spell(req: SpellRequest, request: Request) -> dict:
    """Autocorrect for a message or a search, a word at a time (§142).

    The second pass behind the phone's own keyboard, which the owner found
    misses things. Deliberately cautious - see `autocorrect.py` for why a
    general spell checker rewrites the subjects of half of FAM's searches -
    so the answer is `null` for every word it is not sure about. No account
    needed: search is the one box everybody types into.
    """
    _read_limit(request)
    words = [str(w or "")[:40] for w in req.words]
    firsts = [bool(req.first[i]) if i < len(req.first) else False
              for i in range(len(words))]
    # In the threadpool, never on the event loop: a checker lookup is pure
    # CPU, and a batch of unfamiliar words is tens of milliseconds in which
    # this worker would otherwise serve nobody else.
    out = await asyncio.to_thread(
        lambda: [autocorrect_mod.correct_word(w, first=f)
                 for w, f in zip(words, firsts)])
    return {"corrections": out, "available": autocorrect_mod.available()}


@app.delete("/api/messages/thread")
async def messages_delete_thread(request: Request,
                                 with_: str = Query(..., alias="with", max_length=64)) -> dict:
    """Delete a chat - for this listener only (§142).

    The owner's rule: it leaves *their* list, a new message to that person
    starts a fresh thread without the old history, and the other person is
    unaffected and keeps everything. So nothing is deleted from the table;
    `MessageStore.clear` moves where this listener's view begins.
    """
    _read_limit(request)
    user = _require_account(request)
    try:
        MESSAGES.clear(user, with_)
    except messages_mod.MessageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "unread": _unread_total(user)}


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
                "unread": _unread_total(user)}

    arrived = MESSAGES.arrived_for(user, after_id=since)
    apart = MODERATION.apart(user)
    head_seen = max([head] + [m.id for m in arrived])
    if apart:
        arrived = [m for m in arrived if m.sender not in apart]
    known = _decorate(SOCIAL.following(user) + SOCIAL.followers(user))
    out = []
    for message in arrived:
        person = known.get(message.sender) or SOCIAL.person(message.sender)
        row = message.as_dict(user)
        row["from"] = {"user_id": message.sender,
                       "name": person.get("name") or "Someone",
                       "handle": person.get("handle") or "",
                       "avatar": person.get("avatar") or ""}
        if messages_mod.is_group(message.thread):
            # A banner for a group opens the group, not the sender - who
            # may be a stranger, so their id stays here.
            row["from"].pop("user_id", None)
            view = _group_view(message.thread, user, known)
            row["group"] = {"user_id": message.thread, "name": view["name"],
                            "group": True}
        out.append(row)
    return {
        "messages": out,
        # The same list the follower popup draws, so the two cannot disagree
        # about who is new. Tapping one goes to Friends, which is also what
        # marks them seen - a badge cleared by something merely being drawn is
        # a count nobody got to read.
        # Only people never announced before (§142), so a banner raised on
        # one open of the app is not raised again on the next.
        "follows": _without_apart(user, SOCIAL.new_followers(user, unannounced=True)),
        "head": head_seen,
        "unread": _unread_total(user),
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
    # Dots in a group only from somebody in it (10.6 #4).
    if messages_mod.is_group(req.to) and not MESSAGES.is_member(req.to, user):
        raise HTTPException(status_code=404, detail="You are not in that group.")
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
    # Messaging opens with the app (WAITLIST.md §4): nobody on the waitlist
    # sends, and nothing is sent to somebody who could not open it.
    # Only while the waitlist runs: at launch (WAITLIST=0) everybody may
    # message, whatever an old row still says.
    if messages_mod.is_group(req.to) and not MESSAGES.is_member(req.to, user):
        raise HTTPException(status_code=400, detail="You are not in that group.")
    recipients = (MESSAGES.members(req.to) if messages_mod.is_group(req.to)
                  else [req.to])
    _require_can_post(user)
    # A block, either way, ends a one-to-one conversation. In a group the
    # message is sent and a blocked member simply never sees it.
    if not messages_mod.is_group(req.to) and (
            MODERATION.is_apart(user, req.to) or MODERATION.is_suspended(req.to)):
        raise HTTPException(status_code=403, detail="You can't message this person.")
    waiting = (WAITLIST.waitlisted_among([user] + recipients)
               if settings.waitlist else set())
    if waiting:
        raise HTTPException(status_code=403, detail=(
            "Messages open when you are let in off the waitlist."
            if user in waiting else
            "They are still on the waitlist. Messages open when they are in."))
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


class GroupRequest(BaseModel):
    #: The people to start it with - each somebody this listener follows or
    #: who follows them, the people the new-chat picker offers.
    user_ids: list[str] = Field(..., max_length=messages_mod.MAX_GROUP_MEMBERS)
    name: str = Field("", max_length=200)


class GroupNameRequest(BaseModel):
    name: str = Field("", max_length=200)


@app.post("/api/messages/groups")
async def messages_new_group(req: GroupRequest, request: Request) -> dict:
    """Start a group chat with the people picked (10.6 packet #4).

    Only people in this listener's graph, the ones the picker shows: a
    group is not a way to message a stranger the one-to-one chat would not.
    """
    _read_limit(request)
    user = _require_account(request)
    _require_can_post(user)
    graph = {p["user_id"] for p in _without_apart(
        user, SOCIAL.following(user) + SOCIAL.followers(user))}
    picked = [u for u in dict.fromkeys(req.user_ids) if u in graph]
    if len(picked) != len(set(req.user_ids) - {user}):
        raise HTTPException(status_code=400, detail=(
            "You can start a group with people you follow."))
    waiting = (WAITLIST.waitlisted_among([user] + picked)
               if settings.waitlist else set())
    if waiting:
        raise HTTPException(status_code=403, detail=(
            "Messages open when you are let in off the waitlist."
            if user in waiting else
            "Somebody you picked is still on the waitlist."))
    try:
        group = MESSAGES.create_group(user, picked, req.name)
    except messages_mod.MessageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "group": _group_view(group["id"], user)}


@app.patch("/api/messages/groups/{gid}")
async def messages_rename_group(gid: str, req: GroupNameRequest,
                                request: Request) -> dict:
    _read_limit(request)
    user = _require_account(request)
    _require_can_post(user)
    try:
        MESSAGES.rename_group(user, gid, req.name)
    except messages_mod.MessageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "group": _group_view(gid, user)}


@app.delete("/api/messages/groups/{gid}")
async def messages_leave_group(gid: str, request: Request) -> dict:
    """Leave a group. The others keep it, and are told in it."""
    _read_limit(request)
    user = _require_account(request)
    return {"ok": MESSAGES.leave_group(user, gid),
            "unread": _unread_total(user)}


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
                "folder": SAVED.create_folder(_require_account(request), req.name,
                                              kind=req.kind)}
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


def apple_app_site_association(team_id: str, bundle_id: str) -> Optional[dict]:
    """What iOS reads from this domain to trust the app (APP_STORE.md).

    `applinks`: a shared episode (`/s/<id>`) or mix (`/m/<id>`) opens in the
    app when it is installed, and in the browser - the landing page, which
    plays it - when it is not. `webcredentials`: the app may offer a FAM
    password saved in iCloud Keychain. None until both ids are configured.
    """
    if not (team_id and bundle_id):
        return None
    app_id = "%s.%s" % (team_id, bundle_id)
    return {
        "applinks": {"details": [{
            "appIDs": [app_id],
            "components": [{"/": "/s/*"}, {"/": "/m/*"}],
        }]},
        "webcredentials": {"apps": [app_id]},
    }


@app.get("/.well-known/apple-app-site-association", include_in_schema=False)
async def well_known_aasa() -> JSONResponse:
    """Served as JSON at the exact path Apple fetches, with no redirect and no
    gate: Apple's CDN asks for it without a session, waitlist or not."""
    body = apple_app_site_association(settings.apple_team_id,
                                      settings.ios_bundle_id)
    if body is None:
        raise HTTPException(status_code=404, detail="No iOS app is configured.")
    return JSONResponse(body)


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


def _licences_report() -> dict:
    """`provider_usage.licences`, never raising: health reports, it does not
    fail on a report.

    Only the verdict and which services: `/api/health` is open to anybody
    (the waitlist lets it through), so the plans, prices and what to buy stay
    on `/admin`'s outside-services table (§207 review)."""
    try:
        import provider_usage

        full = provider_usage.licences()
        return {"commercial_ready": full["commercial_ready"],
                "non_commercial_in_use": full["non_commercial_in_use"]}
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read licences: %s", exc)
        return {"error": str(exc)}


@app.get("/api/plans")
async def plans_read(request: Request) -> dict:
    """Every tier and every feature, for a pricing screen.

    Deliberately has no prices in it. A number here and a number in App Store
    Connect are two places for one fact, and the one that is wrong is always
    the one the listener is reading - so the price comes from the store's own
    product metadata, which is also the only place it can be right per country.
    """
    _read_limit(request)
    return {**entitlements.catalogue(), "current": _tier(request),
            # Whether a limit can refuse anybody on this deploy, and whether
            # anything sells a way past one (§207). The plans screen and the
            # limit card word themselves from these rather than guessing:
            # "everything is free" is false the day quotas are on, and a
            # "See plans" button with no checkout behind it is a dead end.
            "enforced": bool(settings.enforce_quotas),
            "checkout": False}


@app.get("/api/voices")
async def voices(request: Request) -> dict:
    """Voices this server can speak in, best first, and this listener's.

    `selected` is the voice their searches are spoken in (§147) - chosen on
    the search page or under Listening in Settings, and nowhere else. Every
    other surface is spoken in a voice drawn from the bank per episode, so
    this is the only voice a listener ever picks.
    """
    listed = [v.as_dict() for v in list_voices()]
    default = default_voice()
    chosen = _chosen_voice(_listener(request))
    selected = default
    if chosen:
        selected = next((v["id"] for v in listed
                         if voice_bank.slug_of(v["id"]) == chosen), default)
    return {
        "default": default,
        "selected": selected,
        "store": VOICE_STORE["dir"],
        "voices": listed,
    }


class VoiceChoice(BaseModel):
    voice: str = Field(..., max_length=80)


@app.post("/api/voices/choice")
async def choose_voice(req: VoiceChoice, request: Request) -> dict:
    """Keep the voice this listener's searches are spoken in (§147).

    Kept on the listener's server-minted id, account or not: it is a setting
    about how search sounds, not something the ranker learns from.
    """
    _read_limit(request)
    if not voice_bank.known(req.voice):
        raise HTTPException(status_code=400, detail="That voice is not in the bank.")
    slug = voice_bank.slug_of(req.voice)
    voice_bank.bank().choose(_listener(request),
                             "" if voice_bank.is_default(slug) else slug)
    return {"ok": True, "voice": req.voice}


class BankVoiceRequest(BaseModel):
    slug: str = Field(..., max_length=40)
    label: str = Field(..., max_length=40)
    description: str = Field("", max_length=160)
    #: The reference recording, a WAV, base64-encoded.
    audio: str = Field(..., max_length=12 * 1024 * 1024)
    #: consent, commercial_use and synthetic_voice_cleared, each "yes".
    rights: dict
    replace: bool = False


@app.get("/api/admin/voices")
async def admin_voices(request: Request) -> dict:
    """The voice bank as stored. Admin only, like `/api/usage`."""
    _require_admin(request)
    return {"voices": [v.as_dict() | {"sha256": v.sha256, "added": v.added}
                       for v in voice_bank.catalogue()]}


@app.post("/api/admin/voices")
async def admin_add_voice(req: BankVoiceRequest, request: Request) -> dict:
    """Add a voice to the bank (§147). `tools/voice_bank.py add` calls this."""
    _require_admin(request)
    try:
        audio = base64.b64decode(req.audio, validate=True)
        added = voice_bank.bank().add(req.slug, req.label, audio, req.rights,
                                      description=req.description,
                                      replace=req.replace)
    except (ValueError, voice_bank.VoiceBankError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "voice": added.as_dict()}


@app.delete("/api/admin/voices/{slug}")
async def admin_remove_voice(slug: str, request: Request) -> dict:
    """Take a voice out of the bank. Episodes already given it keep their
    kept audio; a play that needs it voiced again draws another (§147)."""
    _require_admin(request)
    return {"ok": voice_bank.bank().remove(slug)}


class PronunciationRequest(BaseModel):
    name: str = Field(..., max_length=60)
    say: str = Field(..., max_length=120)


class LocalOutletRequest(BaseModel):
    name: str = Field("", max_length=120)
    homepage: str = Field(..., max_length=500)
    town: str = Field("", max_length=120)
    county: str = Field("", max_length=120)
    region: str = Field("", max_length=120)
    country: str = Field("US", max_length=2)
    scope: str = Field("town", max_length=10)
    feed_url: str = Field("", max_length=500)


class LocalExcludeRequest(BaseModel):
    host: str = Field(..., max_length=255)
    reason: str = Field("the publisher asked", max_length=200)


@app.get("/api/admin/local-news")
async def admin_local_news(request: Request) -> dict:
    """The local news collector (§194): every outlet, its state and why,
    troubled ones first, and the totals. Admin only."""
    _require_admin(request)
    return {"report": local_news_mod.report(),
            "outlets": local_news_mod.admin_rows()}


@app.post("/api/admin/local-news/outlets")
async def admin_add_local_outlet(req: LocalOutletRequest,
                                 request: Request) -> dict:
    """File an outlet for a town or county. Its feed is found on the next
    poll; nothing else needs saying. Admin only."""
    _require_admin(request)
    outlet_id = local_news_mod.store().add_outlet(
        req.name, req.homepage, town=req.town, county=req.county,
        region=req.region, country=req.country, scope=req.scope,
        source="admin", feed_url=req.feed_url)
    if outlet_id is None:
        raise HTTPException(status_code=400, detail=(
            "That homepage was refused: it must start with http:// or "
            "https://, and the publisher must not be on the do-not-use list."))
    return {"ok": True, "id": outlet_id}


@app.post("/api/admin/local-news/exclude")
async def admin_exclude_local_outlet(req: LocalExcludeRequest,
                                     request: Request) -> dict:
    """Never read this publisher again - for a publisher who objects.
    Admin only."""
    _require_admin(request)
    local_news_mod.store().exclude(req.host, req.reason)
    return {"ok": True}


@app.get("/api/admin/pronunciations")
async def admin_pronunciations(request: Request) -> dict:
    """How the voice says hard names (§165): every respelling on file, and
    who said it - the brief, the writer, or an admin. Admin only."""
    _require_admin(request)
    return {"pronunciations": voice_bank.bank().pronunciations()}


@app.post("/api/admin/pronunciations")
async def admin_set_pronunciation(req: PronunciationRequest,
                                  request: Request) -> dict:
    """Fix how the voice says one name. An admin's respelling is never
    replaced by a model's, and applies from the next sentence spoken."""
    _require_admin(request)
    try:
        name, say = pronunciation.lexicon().set(req.name, req.say)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "name": name, "say": say}


@app.delete("/api/admin/pronunciations/{name}")
async def admin_remove_pronunciation(name: str, request: Request) -> dict:
    """Forget one respelling; the voice reads that name as spelled again."""
    _require_admin(request)
    return {"ok": pronunciation.lexicon().remove(name)}


# ---------------------------------------------------------------- feedback
#
# Instant feedback (`feedback.py`): the button under the phone on the demo
# page files a bug report here, and `/admin` is the inbox that resolves them.
# Anybody may file one - a demo is mostly people without accounts - and only
# an admin may read them.


class FeedbackEpisode(BaseModel):
    """The episode on the player when a report was typed there: the words it
    was asked for, keyed the way `/api/next` keys them.

    Nothing here can refuse the report it rides on: over-long words are cut
    (a key that no longer matches finds nothing, and the report is kept), and
    minutes are whatever the page said, checked when the key is built.
    `title` is accepted from older pages and ignored - what the inbox shows is
    read from the cache, never taken from the page. `thumb` is the picture
    the player was showing, kept only when it is one of FAM's own
    thumbnails (`feedback.thumb_path`); without it the server picks the
    player's picture itself (`_player_picture`)."""
    q: str = ""
    minutes: int = 0
    context: str = ""
    title: str = ""
    thumb: str = ""

    @field_validator("q", "context", "title", "thumb", mode="before")
    @classmethod
    def _cut(cls, value):
        return str(value or "")[:500]

    @field_validator("minutes", mode="before")
    @classmethod
    def _whole(cls, value):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0


class FeedbackRequest(BaseModel):
    text: str = Field("", max_length=feedback_mod.MAX_TEXT + 500)
    screen: str = Field("", max_length=500)
    build: str = Field("", max_length=500)
    page: str = Field("", max_length=2000)
    viewport: str = Field("", max_length=500)
    episode: Optional[FeedbackEpisode] = None


async def _feedback_episode(ep: Optional[FeedbackEpisode]) -> Optional[dict]:
    """Title, sources and transcript of the episode a report was filed on
    (9.30 #6), read from the cache and the live track under the episode's own
    key - the same lookups as `/api/next`, `/api/sources` and
    `/api/transcript`, so the inbox shows what the listener was hearing.

    **Never generates** and never raises: a report is kept whether or not its
    episode can be found. The page names only the question; everything kept
    is read here, so a report cannot plant a transcript of its own.
    """
    if ep is None or not ep.q.strip():
        return None
    minutes = ep.minutes or DEFAULT_MINUTES
    title, sources, sentences = "", {}, []
    try:
        plan = _validated_plan(ep.q, minutes, ep.context)
        pipeline = _make_pipeline()
        meta = await pipeline.episode_meta(plan)
        title = meta.get("title") or title
        sources = provenance_mod.Provenance.from_json(
            await pipeline.sources_for(plan)).as_dict()
        sentences, _live, _done = await pipeline.captions_for(plan)
    except HTTPException:
        pass
    except Exception:  # noqa: BLE001 - the report matters more than its attachment
        log.exception("could not read the episode for a feedback report")
    # The picture the episode was playing over (10.10 #3): the page's, when
    # it is one of ours, else the one the player would have drawn.
    thumb = feedback_mod.thumb_path(ep.thumb)
    if not thumb:
        try:
            thumb = (_player_picture(ep.q, title, minutes, "", ep.context)
                     .get("url", "") or "")
        except Exception:  # noqa: BLE001 - a picture is never worth a report
            log.exception("could not pick a picture for a feedback report")
    return feedback_mod.episode_snapshot(ep.q, minutes, title, sources, sentences,
                                         thumb=thumb)


@app.post("/api/feedback")
async def file_feedback(req: FeedbackRequest, request: Request) -> dict:
    """Keep one bug report. The listener comes from the session, never the
    body, and is recorded only for an account; a guest is paced by session
    and kept anonymous."""
    listener = _listener(request)
    episode = None
    if req.episode is not None and req.text.strip():
        # The same pace as the lookups it repeats (`/api/next` and friends) -
        # but a listener past it loses the episode's details, never the
        # report they typed.
        try:
            _read_limit(request)
            episode = await _feedback_episode(req.episode)
        except HTTPException:
            log.info("feedback: over the read pace; kept without its episode")
    try:
        report = FEEDBACK.add(
            req.text, user_id=listener if _has_account(request) else "",
            screen=req.screen,
            # The page says which client it is; the server adds which code
            # answered, so a report can be matched to the deploy it was on.
            build=" @ ".join(x for x in (req.build.strip(),
                                         _build_report()["short"]) if x),
            page=req.page,
            viewport=req.viewport,
            agent=request.headers.get("user-agent", ""),
            throttle_key=listener, episode=episode)
    except feedback_mod.FeedbackError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "id": report["id"]}


class FeedbackResolve(BaseModel):
    resolved: bool = True
    note: Optional[str] = Field(None, max_length=feedback_mod.MAX_TEXT)


@app.get("/api/admin/feedback")
async def admin_feedback(request: Request,
                         state: str = Query("open"),
                         limit: int = Query(200, ge=1, le=1000)) -> dict:
    """The inbox: reports newest first, open by default, with both counts."""
    _require_admin(request)
    try:
        reports = FEEDBACK.list(state, limit)
    except feedback_mod.FeedbackError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"reports": reports, "counts": FEEDBACK.counts()}


@app.post("/api/admin/feedback/{report_id}/resolve")
async def admin_resolve_feedback(report_id: str, req: FeedbackResolve,
                                 request: Request) -> dict:
    """Mark a report resolved (or `resolved: false` to open it again). The
    report is kept either way, with the date it was resolved."""
    _require_admin(request)
    report = FEEDBACK.resolve(report_id, req.resolved, note=req.note)
    if report is None:
        raise HTTPException(status_code=404, detail="No report with that id.")
    return {"ok": True, "report": report, "counts": FEEDBACK.counts()}


# ---------------- Reporting and blocking (App Store 1.2) ----------------
# `moderation.py` says what each record means. Reporting is open to anybody
# who can see the content - Explore and comments are read without an
# account - and blocking is kept on an account, like everything else kept.

def _person_by_handle(handle: str) -> str:
    """The listener a handle names exactly, or ""."""
    wanted = str(handle or "").strip().lstrip("@").lower()
    if len(wanted) < 2:
        return ""
    return SOCIAL.user_by_handle(wanted)


#: The three pages App Store review asks for (APP_STORE.md): the terms every
#: listener agrees to, the privacy policy and the support page. Templates in
#: `pages/`, outside `static/` so a placeholder is never served raw.
LEGAL_PAGES = ("terms", "privacy", "support")
_LEGAL_TEMPLATES = {name: (PROJECT_ROOT / "pages" / f"{name}.html").read_text(encoding="utf-8")
                    for name in LEGAL_PAGES}
#: The privacy policy's date. Change it whenever pages/privacy.html changes.
PRIVACY_UPDATED = "8 October 2026"


def legal_page(name: str, support_email: str) -> str:
    """One of `LEGAL_PAGES` with the published contact (1.2) filled in."""
    if support_email:
        safe = html.escape(support_email)
        link = '<a href="mailto:%s">%s</a>' % (safe, safe)
        contact = ('<p>Email %s. A person reads every message, and we answer '
                   'within %d hours.</p>' % (link, moderation_mod.REVIEW_HOURS))
    else:
        link = "us through the app"
        contact = ('<p class="note">Report anything from its menu in the app; a person '
                   'reviews every report.</p>')
    return (_LEGAL_TEMPLATES[name].replace("{{CONTACT}}", contact)
            .replace("{{EMAIL_LINK}}", link)
            .replace("{{UPDATED}}", PRIVACY_UPDATED)
            .replace("{{REVIEW_HOURS}}", str(moderation_mod.REVIEW_HOURS)))


def terms_page(support_email: str) -> str:
    return legal_page("terms", support_email)


@app.get("/terms", include_in_schema=False)
async def terms() -> HTMLResponse:
    """Not behind the waitlist: everybody agrees to these before signing up."""
    return HTMLResponse(legal_page("terms", settings.support_email))


@app.get("/privacy", include_in_schema=False)
async def privacy() -> HTMLResponse:
    """The privacy policy App Store Connect links to. Open to everybody."""
    return HTMLResponse(legal_page("privacy", settings.support_email))


@app.get("/support", include_in_schema=False)
async def support() -> HTMLResponse:
    """The support URL App Store Connect links to. Open to everybody."""
    return HTMLResponse(legal_page("support", settings.support_email))


@app.get("/api/report")
async def report_options(request: Request) -> dict:
    """The reasons a report offers and what happens next, in the server's
    words so every client lists the same reasons."""
    _read_limit(request)
    return {"reasons": [{"id": i, "label": label}
                        for i, label in moderation_mod.REASONS],
            "kinds": list(moderation_mod.KINDS),
            "review_hours": moderation_mod.REVIEW_HOURS,
            "contact": settings.support_email}


class ReportRequest(BaseModel):
    kind: str = Field(..., max_length=16)
    #: A comment, message or vibe id, a handle, or a group id. An episode is
    #: named by `query` and `minutes` instead.
    target: str = Field("", max_length=120)
    query: str = Field("", max_length=300)
    minutes: int = Field(0, ge=0, le=10)
    reason: str = Field(..., max_length=24)
    note: str = Field("", max_length=moderation_mod.MAX_NOTE)


REPORT_THANKS = ("Thanks for telling us. We review every report within "
                 "%d hours." % moderation_mod.REVIEW_HOURS)


def _report_subject(kind: str, req: ReportRequest, me: str) -> tuple[str, str, dict]:
    """(target, owner, snapshot) for what is being reported, read from the
    stores rather than the request, or a 404 if this listener cannot see it."""
    missing = HTTPException(status_code=404, detail="That isn't here any more.")
    if kind == "episode":
        query = " ".join(req.query.split())
        if not query or not req.minutes:
            raise missing
        # The searcher is who posted it, so a reviewer can suspend them (1.2).
        return (_episode_target(query, req.minutes), _episode_author(query, req.minutes),
                {"query": query, "minutes": req.minutes})
    target = req.target.strip()
    # Row ids: digits, and short enough to be an SQLite integer (§226).
    if kind in ("comment", "message", "vibe") and not (target.isdigit() and len(target) <= 18):
        raise missing
    if kind == "comment":
        row = SOCIAL.comment(int(target))
        if not row:
            raise missing
        return (target, row["user_id"],
                {"text": row["text"], "query": row["query"], "minutes": row["minutes"]})
    if kind == "message":
        msg = MESSAGES.message(int(target))
        if msg is None:
            raise missing
        group = messages_mod.is_group(msg.thread)
        if (group and not MESSAGES.is_member(msg.thread, me)) or (
                not group and me not in (msg.sender, msg.recipient)):
            raise missing
        return (target, msg.sender, {"text": msg.text, "query": msg.query,
                                     "title": msg.title, "thread": msg.thread})
    if kind == "vibe":
        row = SOCIAL.echo_row(int(target))
        if not row:
            raise missing
        return (target, row["user_id"], {"caption": row["caption"], "title": row["title"],
                                         "query": row["query"], "minutes": row["minutes"]})
    if kind == "person":
        owner = _person_by_handle(target)
        if not owner:
            raise missing
        person = SOCIAL.person(owner)
        return (person.get("handle") or target, owner,
                {"name": person.get("name") or "", "handle": person.get("handle") or ""})
    if kind == "group":
        group = MESSAGES.group(target) if messages_mod.is_group(target) else {}
        if not group or not MESSAGES.is_member(target, me):
            raise missing
        return (target, "", {"name": group.get("name") or "",
                             "members": len(group.get("members") or [])})
    raise HTTPException(status_code=400, detail="That can't be reported.")


@app.post("/api/report")
async def report_content(req: ReportRequest, request: Request) -> dict:
    """Report something offensive. It is hidden from this listener at once
    and a person reviews it (`moderation.py`)."""
    _read_limit(request)
    me = _require_listener(request)
    kind = req.kind.strip().lower()
    target, owner, snapshot = _report_subject(kind, req, me)
    try:
        report = MODERATION.report(me, kind, target, req.reason, owner=owner,
                                   note=req.note, snapshot=snapshot)
    except moderation_mod.ModerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "id": report["id"], "message": REPORT_THANKS,
            # What the client should stop showing now: the thing itself.
            "hidden": kind in ("comment", "message", "vibe", "episode")}


class BlockRequest(BaseModel):
    handle: str = Field(..., max_length=64)


@app.post("/api/block")
async def block_person(req: BlockRequest, request: Request) -> dict:
    """Block somebody: neither of you sees or reaches the other, and any
    follow between you ends. They are not told."""
    _read_limit(request)
    me = _require_account(request)
    target = _person_by_handle(req.handle)
    if not target:
        raise HTTPException(status_code=404, detail="No listener by that handle.")
    try:
        MODERATION.block(me, target)
    except moderation_mod.ModerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    SOCIAL.sever(me, target)
    return {"ok": True, "blocked": True}


@app.delete("/api/block")
async def unblock_person(request: Request,
                         handle: str = Query(..., max_length=64)) -> dict:
    """Unblock. Nothing that the block ended - a follow - comes back."""
    _read_limit(request)
    me = _require_account(request)
    target = _person_by_handle(handle)
    return {"ok": bool(target) and MODERATION.unblock(me, target), "blocked": False}


@app.get("/api/blocks")
async def blocked_people(request: Request) -> dict:
    """The people this listener blocked, newest first, for Settings."""
    _read_limit(request)
    me = _require_account(request)
    out = []
    for uid in MODERATION.blocked_by(me):
        person = SOCIAL.person(uid)
        if person.get("handle"):
            out.append({"name": person.get("name") or "",
                        "handle": person["handle"],
                        "avatar": person.get("avatar") or ""})
    return {"people": out}


@app.get("/api/admin/reports")
async def admin_reports(request: Request, state: str = Query("open"),
                        limit: int = Query(200, ge=1, le=1000)) -> dict:
    """Content reports, oldest open first: the 24-hour promise is kept from
    the top of this list."""
    _require_admin(request)
    reports = MODERATION.reports("open" if state == "open" else "resolved", limit)
    for report in reports:
        owner = report.pop("owner", "")
        person = SOCIAL.person(owner) if owner else {}
        report["posted_by"] = ({"name": person.get("name") or "",
                                "handle": person.get("handle") or "",
                                "suspended": MODERATION.is_suspended(owner)}
                               if owner else None)
        # The reporter is never shown, even here: a reviewer needs the
        # content, not who objected to it.
        report.pop("reporter", None)
    return {"reports": reports, "summary": MODERATION.report_summary()}


class ReportDecision(BaseModel):
    action: str = Field(..., max_length=16)
    note: str = Field("", max_length=moderation_mod.MAX_NOTE)


def _remove_reported(report: dict, owner: str) -> bool:
    """Take the reported thing down for everybody. False for a person or a
    group, which are not content - suspend the person instead."""
    kind, target = report["kind"], report["target"]
    if kind == "comment":
        SOCIAL.delete_comment(owner, int(target), moderator=True)
    elif kind == "message":
        MESSAGES.remove(int(target))
    elif kind == "vibe":
        row = SOCIAL.echo_row(int(target))
        if row:
            SOCIAL.unecho(row["user_id"], row["query"], row["minutes"])
    elif kind == "episode":
        MODERATION.hide_episode(target, report["id"])
    else:
        return False
    return True


@app.post("/api/admin/reports/{report_id}/resolve")
async def admin_resolve_report(report_id: str, req: ReportDecision,
                               request: Request) -> dict:
    """Dismiss, remove the content for everybody, or remove it and suspend
    whoever posted it. Every open report about the same thing is answered."""
    _require_admin(request)
    report = MODERATION.get(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="No report with that id.")
    action = req.action.strip().lower()
    owner = report.get("owner") or ""
    if action == "removed" and not _remove_reported(report, owner):
        raise HTTPException(status_code=400, detail=(
            "A person or a group isn't content to remove. Suspend the person instead."))
    if action == "suspended":
        if not owner:
            raise HTTPException(status_code=400, detail="Nobody posted this to suspend.")
        _remove_reported(report, owner)
        MODERATION.suspend(owner, report_id)
    try:
        report = MODERATION.resolve(report_id, action, req.note)
    except moderation_mod.ModerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "report": report, "summary": MODERATION.report_summary()}


@app.post("/api/admin/reports/reinstate")
async def admin_reinstate(request: Request, handle: str = Query(..., max_length=64)) -> dict:
    """Lift a suspension."""
    _require_admin(request)
    target = _person_by_handle(handle)
    return {"ok": bool(target) and MODERATION.reinstate(target)}


@app.post("/api/script")
async def script(req: ScriptRequest, request: Request) -> dict:
    _require_ai_consent(request, _listener(request))
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
        # PATCH is how a mix is edited and X-FAM-TZ is every request's clock
        # (§186); both were missing, which only a cross-origin client would
        # have found (§208).
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-FAM-Client", "X-FAM-TZ"],
        # So a browser client can read the quota verdict on a 429 rather than
        # only the status code.
        expose_headers=["X-FAM-Quota", "X-Sample-Rate", "X-Requested-Seconds",
                    "X-FAM-Cache", "X-FAM-Keepable", "X-FAM-Client-Status",
                    "X-FAM-Episode", "X-FAM-Consent",
                    # §242: without these a cross-origin player cannot tell
                    # Opus from PCM, and would play the frames as noise.
                    "X-FAM-Audio-Format", "X-FAM-Opus-Preskip"],
    )
    log.info("CORS enabled for %s", ", ".join(_ALLOWED_ORIGINS))


EVENTS = topics_mod.EventStore()
MIXES = mixes_mod.MixStore()
#: Where each listener's phone can be reached for "your mix is ready".
PUSH = push_mod.PushStore()
SOCIAL = social_mod.SocialStore()
ACCOUNTS = accounts_mod.AccountStore()
#: The pre-launch waitlist (WAITLIST.md): columns on the accounts row, read and
#: written through the accounts store's own connection.
WAITLIST = waitlist_mod.Waitlist(ACCOUNTS)
if settings.waitlist:
    # Every route that creates an account (email, phone, Google, Apple) starts
    # it on the list - one switch in the INSERT, not a step each route has to
    # remember.
    ACCOUNTS.new_account_status = waitlist_mod.WAITLISTED
VIRAL_LOOPS = viral_loops_mod.ViralLoops(settings.viral_loops_api_token,
                                         settings.viral_loops_campaign_id)
PREFS = prefs_mod.PreferenceStore()
METER = metering.MeterStore()
QUOTAS = quotas.QuotaStore()
MESSAGES = messages_mod.MessageStore()
SAVED = saved_mod.SavedStore()
SHARES = sharing.ShareStore()
FEEDBACK = feedback_mod.FeedbackStore()
CONSENT = consent_mod.ConsentStore()
MODERATION = moderation_mod.ModerationStore()


#: Page types worth compressing: text. Never audio (raw PCM barely
#: compresses, and buffering it would cost the first word its latency) and
#: never pictures, which are compressed already.
COMPRESSIBLE_TYPES = frozenset({
    "text/html", "text/css", "text/javascript", "application/javascript",
    "application/json", "application/manifest+json", "image/svg+xml",
    "text/plain",
})
#: A page with no ETag is compressed on each request only up to this size;
#: one with an ETag is compressed once per version and kept.
COMPRESS_UNTAGGED_MAX = 256 * 1024


def compress_pages(app):
    """gzip the app's pages and scripts, once per version of each (§241).

    The app shell (`index.html`) is about a megabyte, and it went out
    uncompressed on every open: at a thousand listeners the page was the
    slowest thing the load test measured (p95 9.6 s), and the largest thing
    most listeners fetch except audio. Compressed it is about a quarter
    (1,069 KB to 283 KB).

    Compressing a megabyte costs ~15 ms of CPU, and this server is CPU-bound
    under load, so the compressed copy is **kept, keyed by the file's ETag**:
    each version of each file is compressed once per process. A page without
    an ETag (built per request) is compressed only when small.

    Only for GET outside `/api/`: the API's JSON is small, and `/api/audio`
    is a stream whose first byte must not wait for a compressor. Inside the
    session and waitlist steps, so those still run for every page.
    """
    kept: dict = {}

    async def asgi(scope, receive, send):
        if (scope["type"] != "http" or scope.get("method") != "GET"
                or scope.get("path", "").startswith("/api/")
                or "gzip" not in Headers(scope=scope).get("accept-encoding", "")):
            await app(scope, receive, send)
            return
        # through: False = collecting, True = passing through untouched,
        # None = already answered from `kept`.
        state = {"start": None, "through": False, "chunks": []}

        async def capture(message):
            if state["through"] is True:
                await send(message)
                return
            if message["type"] == "http.response.start":
                headers = Headers(raw=message.get("headers") or [])
                kind = headers.get("content-type", "").split(";")[0].strip().lower()
                if (message.get("status") != 200 or kind not in COMPRESSIBLE_TYPES
                        or "content-encoding" in headers):
                    state["through"] = True
                    await send(message)
                    return
                state["start"] = message
                # The version is known from the headers: if it was compressed
                # already, send that now rather than waiting for the file to be
                # read in 64 KB pieces through a busy thread pool - which was
                # most of the page's time under load - and let the rest go.
                etag = headers.get("etag")
                packed = kept.get((scope.get("path", ""), etag)) if etag else None
                if packed is not None:
                    state["through"] = None  # sent; swallow what follows
                    await _send_packed(send, message, packed)
                return
            if state["through"] is None:
                return
            if message["type"] != "http.response.body":
                await send(message)
                return
            state["chunks"].append(message.get("body", b""))
            if message.get("more_body"):
                return
            body = b"".join(state["chunks"])
            start = state["start"]
            etag = Headers(raw=start.get("headers") or []).get("etag")
            key = (scope.get("path", ""), etag) if etag else None
            packed = kept.get(key) if key else None
            if packed is None and (key or len(body) <= COMPRESS_UNTAGGED_MAX):
                packed = gzip.compress(body, compresslevel=6, mtime=0)
                if key and len(packed) < len(body):
                    if len(kept) >= 256:
                        kept.clear()
                    kept[key] = packed
            if packed is None or len(packed) >= len(body):
                await send(start)
                await send({"type": "http.response.body", "body": body})
                return
            await _send_packed(send, start, packed)

        await app(scope, receive, capture)
    return asgi


async def _send_packed(send, start: dict, packed: bytes) -> None:
    """A response start and its body, as the gzip of what it was."""
    headers = MutableHeaders(raw=list(start.get("headers") or []))
    headers["content-encoding"] = "gzip"
    headers["content-length"] = str(len(packed))
    headers.add_vary_header("Accept-Encoding")
    await send({**start, "headers": headers.raw})
    await send({"type": "http.response.body", "body": packed})


# Innermost of the per-request steps: added first.
app.add_middleware(compress_pages)


def _on_response_start(send, edit):
    """`send`, with `edit(headers)` applied to the response's start message.

    How a plain ASGI middleware changes a response's headers: at the moment
    they go out, which is after the handler has run - so a cookie the handler
    asked for (`request.state.set_session`) is already decided (§241).
    """
    async def wrapped(message):
        if message["type"] == "http.response.start":
            message = {**message, "headers": list(message.get("headers") or [])}
            edit(MutableHeaders(raw=message["headers"]))
        await send(message)
    return wrapped


def carry_the_session(app):
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

    Plain ASGI rather than `@app.middleware("http")` (§241): that wrapper
    runs every request in an extra task and copies every response - every
    chunk of every episode - through an in-memory channel. Measured
    in-process, that bookkeeping was three quarters of the app's own time
    on a request (0.60 ms to 0.15 ms without it). The steps are the same,
    in the same order.
    """
    async def asgi(scope, receive, send):
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        request = Request(scope, receive)
        path = request.url.path
        wants_identity = path == "/" or (
            path.startswith("/api/") and path not in MACHINE_PATHS
        )
        # The listener's clock, from their device (10.1): what "today", "tonight"
        # and "yesterday" mean in anything written for this request.
        listener_clock.set_for_request(request.headers.get(listener_clock.HEADER, ""))
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

        closed = _waitlist_refusal(request, listener)
        if closed is not None:
            await closed(scope, receive, send)
            return

        def cookies(headers):
            new_token = getattr(request.state, "set_session", None)
            if new_token is None and not minted:
                return
            jar = Response()
            if new_token is not None:
                if new_token:
                    _set_session_cookie(jar, request, new_token)
                else:
                    jar.delete_cookie(accounts_mod.COOKIE_NAME, path="/")
            else:
                _set_session_cookie(jar, request, minted)
            for name, value in jar.raw_headers:
                if name == b"set-cookie":
                    headers.append("set-cookie", value.decode("latin-1"))

        await app(scope, receive, _on_response_start(send, cookies))
    return asgi


app.add_middleware(carry_the_session)


#: While `WAITLIST` is on, the API a listener who is not active may still reach:
#: signing up and in, the waitlist's own endpoints, and the profile fields the
#: status page edits (`/api/me`: name, handle, photo; `/api/preferences`:
#: topics). Everything else - listening included - answers 403. The admin
#: endpoints check their own credential and are passed through for it.
#: `/api/thumb/` is a category's picture - nobody's data - which the landing
#: page's samples draw (§190).
WAITLIST_OPEN_PREFIXES = ("/api/auth/", "/api/waitlist/", "/api/admin/",
                          "/api/thumb/")
WAITLIST_OPEN_PATHS = frozenset({
    "/api/health", "/api/voice/register", "/api/client-status",
    "/api/me", "/api/preferences",
    # The sign-up samples the landing page rotates (§190); their audio is
    # `_welcome_sample_request`.
    "/api/welcome",
    # Their account: read it, rename it, and above all delete it. Deleting
    # an account has to be reachable by everybody who has one (App Store
    # 5.1.1(v)), the waitlisted included.
    "/api/account",
})
#: Pages that are the app, sent to the waitlist instead while it is on. A mix
#: link is a list of episodes to browse, which is the app. A shared *episode*
#: (`/s/<id>`) is not here: anyone may listen to one, waitlist or not (the
#: owner, 01/10) - see `_shared_episode_request` for the audio it needs.
WAITLIST_CLOSED_PAGES = ("/", "/index.html")
WAITLIST_CLOSED_PAGE_PREFIXES = ("/v/", "/m/")


def _shared_episode_request(request: Request) -> bool:
    """Whether this API call is a shared episode's landing page at work.

    Two calls make that page go: counting the open, and the audio itself. The
    audio endpoint is the whole app's, so it is let through only for exactly
    what somebody shared - that question at that length, from the share
    surface, with nothing attached - and never for anything else a stranger
    might type into the same URL.
    """
    path = request.url.path
    if path.startswith("/api/share/") and path.endswith("/open"):
        return True
    if path != "/api/audio":
        return False
    params = request.query_params
    if params.get("surface") != "share":
        return False
    if any(params.get(k) for k in ("attach", "context", "episode", "topic_id")):
        return False
    return SHARES.is_shared(params.get("q", ""), params.get("minutes", ""))


def _welcome_sample_request(request: Request) -> bool:
    """Whether this is the audio of one of the sign-up samples (§190).

    The waitlist's landing page rotates the same three episodes the app's
    sign-up screen does, for somebody who cannot get into the app yet. As
    with a shared episode, `/api/audio` is let through only for exactly
    those: a replay (`cached_only`, so nothing is written and no voice
    wakes), at the length the samples are kept at, nothing attached, and a
    question that is one of today's samples - never anything else typed into
    the same URL.
    """
    if request.url.path != "/api/audio":
        return False
    params = request.query_params
    if params.get("cached_only") != "true":
        return False
    if any(params.get(k) for k in ("attach", "context", "episode", "topic_id",
                                   "voice")):
        return False
    if params.get("minutes", "") != str(BROWSE_MINUTES):
        return False
    q = params.get("q", "")
    if not q or not any(ep.get("query") == q for ep in _welcome_episodes()):
        return False
    # Read again, not from the minute-old memo: audio evicted since then
    # would have this replay wake the voice for somebody with no account.
    return _audio_is_kept(q, BROWSE_MINUTES)


def _waitlist_page_for(listener) -> str:
    if listener is not None and listener.status == waitlist_mod.WAITLISTED:
        return "/waitlist/me"
    return "/waitlist"


def _waitlist_refusal(request: Request, listener):
    """The response that keeps the app closed, or None to let it through.

    Enforced here, on the server, for every request, because a closed app that
    is closed only in the interface is open to anybody with curl. Only an
    'active' account passes; a guest or a waitlisted account is sent to the
    waitlist (a page) or told why (the API, 403 with `X-FAM-Waitlist` naming
    where to go, which the app's own pages follow).
    """
    if not settings.waitlist:
        return None
    if listener is not None and listener.status == waitlist_mod.ACTIVE:
        return None
    if _allowed_admin(listener):
        return None
    path = request.url.path
    target = _waitlist_page_for(listener)
    if path.startswith("/api/"):
        if path in WAITLIST_OPEN_PATHS or path.startswith(WAITLIST_OPEN_PREFIXES):
            return None
        if _admin_request(request):
            return None
        if _shared_episode_request(request) or _welcome_sample_request(request):
            return None
        return JSONResponse(
            {"detail": "FAM is open to members only while the waitlist is"
                       " running. Your place is saved.",
             "waitlist": listener.status if listener is not None and
                         listener.status else "guest",
             "redirect": target},
            status_code=403, headers={"X-FAM-Waitlist": target})
    # Normalised, because the static mount serves the shell for `/index.html/`
    # and `//` too, and a gate that matches only the spellings it thought of
    # is a gate with a side door.
    page = "/" + path.strip("/")
    # A guest is never moved off the front door (§217, restored at the
    # owner's direction in §231, reversing the 10.7 packet): typing the
    # address opens the app on its own sign-in and sign-up. Sign Up goes to
    # the waitlist; Sign In signs a member into the app and sends anyone
    # still in line to their status page. The app stays closed all the same -
    # every API call above still refuses a guest - and only a referral link,
    # which is an invitation to join, still goes to the waitlist.
    if target == "/waitlist" and not request.query_params.get(
            waitlist_mod.REFERRAL_PARAM):
        return None
    if page in WAITLIST_CLOSED_PAGES or page.startswith(WAITLIST_CLOSED_PAGE_PREFIXES):
        query = request.url.query
        return RedirectResponse(target + ("?" + query if query else ""),
                                status_code=302)
    return None


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


def version_prefix(app):
    """Serve `/api/v1/x` from the same handler as `/api/x`.

    A rewrite rather than a second set of routes: two registrations of one
    endpoint is two places for a decorator to drift, and the failure would be
    a native client quietly getting different behaviour from the web one.

    Declared after `carry_the_session` so it wraps it and therefore runs
    *first* - the session middleware decides what to do from the path, and it
    has to see the real one.
    """
    async def asgi(scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path.startswith(API_PREFIX + "/") or path == API_PREFIX:
                scope["path"] = "/api" + path[len(API_PREFIX):]
        await app(scope, receive, send)
    return asgi


app.add_middleware(version_prefix)


def client_version(app):
    """Keep the promise made to every installed client (§172, client_versions.py).

    A client names itself in `X-FAM-Client`. A release the registry marks
    `retired` gets a 426 with a sentence and the store link on everything but
    the two paths that let it say so; a `deprecated` one is served normally
    with `X-FAM-Client-Status: deprecated` on the response, so the app can
    suggest an update without being stopped. Anything unknown - no header, a
    TestFlight build, a simulator - is served exactly as before.
    """
    async def asgi(scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if not path.startswith("/api/"):
            await app(scope, receive, send)
            return
        header = Headers(scope=scope).get(client_versions.HEADER, "")
        client_versions.record(header)
        verdict = client_versions.status_for(header) if header else None
        plain = ("/api" + path[len(API_PREFIX):]) if path.startswith(API_PREFIX + "/") else path
        if verdict and verdict["update_required"] and plain not in client_versions.RETIRED_MAY_REACH:
            refusal = JSONResponse(
                {"error": verdict["message"], "client": verdict,
                 "update_url": os.environ.get("APP_STORE_URL", "").strip() or None},
                status_code=426,
                headers={client_versions.STATUS_HEADER: verdict["status"]})
            await refusal(scope, receive, send)
            return
        if not (verdict and verdict["known"]):
            await app(scope, receive, send)
            return

        def status(headers):
            headers[client_versions.STATUS_HEADER] = verdict["status"]

        await app(scope, receive, _on_response_start(send, status))
    return asgi


app.add_middleware(client_version)


@app.get("/api/client-status")
async def client_status(request: Request) -> dict:
    """What the server makes of the calling client: supported, deprecated
    (works, suggest an update) or retired (update required). A native app asks
    this at launch; the answer comes from `releases/registry.json`."""
    return client_versions.status_for(request.headers.get(client_versions.HEADER, ""))


_ARCHIVE_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                  ".css": "text/css; charset=utf-8", ".json": "application/json"}


@app.get("/v/{version}")
async def web_release_root(version: str):
    return RedirectResponse(f"/v/{quote(version)}/", status_code=307)


@app.get("/v/{version}/{name:path}")
async def web_release(version: str, name: str = ""):
    """A kept web release, served whole against this server (§172).

    Only files the release's manifest lists, and nothing from a retired
    release: a retired version is one whose API calls are refused, so serving
    its page would only show a screen that cannot work.
    """
    release = client_versions.find("web", version)
    if release is None or client_versions.archive_dir(version) is None:
        raise HTTPException(status_code=404, detail=f"No kept web release {version!r}.")
    if release.status == "retired":
        return HTMLResponse(
            f"<!doctype html><meta charset=utf-8><title>FAM {version}</title>"
            f"<p>FAM web {version} is retired: its API calls are refused by this "
            "server. It is kept in the repository, not served.</p>", status_code=410)
    name = name or "index.html"
    files = client_versions.manifest(version).get("files", {})
    if name not in files:
        raise HTTPException(status_code=404, detail=f"{name} is not part of web {version}.")
    path = client_versions.archive_dir(version) / name
    suffix = path.suffix.lower()
    return Response(path.read_bytes(),
                    media_type=_ARCHIVE_TYPES.get(suffix, "application/octet-stream"),
                    # An archive never changes, and a stale shell must not be
                    # mistaken for it either: revalidate, then trust.
                    headers={"Cache-Control": "no-cache"})


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


#: Where a tap may say it came from (§147). The interface sends it, because
#: `_surface` cannot tell a DailyFAM play or a shared link from a search by
#: the parameters alone - and Explore is searched episodes only.
PLAY_SURFACES = ("search", "myfam", "dailyfam", "share", "other")

#: The surfaces that offer no length and no voice: two minutes, and a voice
#: drawn from the bank per episode (§147).
BROWSE_SURFACES = ("myfam", "dailyfam")


def _play_surface(surface: str, cached_only: bool, topic_id: str,
                  context: str) -> str:
    """Which surface this play is, for the rules §147 hangs on it."""
    if cached_only:
        return "explore"
    named = (surface or "").strip().lower()
    if named in PLAY_SURFACES:
        return named
    derived = _surface(cached_only, topic_id, context)
    return "other" if derived == "godeeper" else derived


def _voice_engine() -> str:
    """The production engine's name, or "" when nothing but a tone speaks."""
    for cls in production_engines():
        try:
            if cls.available():
                return cls.name
        except Exception:  # noqa: BLE001 - a name, never a reason to fail
            continue
    return ""


def _chosen_voice(user: str) -> str:
    """The bank voice this listener's searches are spoken in, or "" for the
    default. A choice whose voice has left the bank is the default again."""
    try:
        chosen = voice_bank.bank().choice(user)
    except Exception:  # noqa: BLE001 - the default voice still speaks
        log.exception("could not read a listener's voice choice")
        return ""
    return chosen if chosen and voice_bank.known(chosen) else ""


def _episode_voice(user: str, where: str, plan, asked: str) -> Optional[str]:
    """The voice this play is spoken in (§147). None is the default voice.

    * **searchFAM** - the one surface with a voice picker: the voice the
      request names, else the listener's kept choice.
    * **Everywhere else** - the voice kept beside the episode's script, and
      for an episode with none, one drawn at random from the bank and kept,
      so the next play is the same voice and replays its kept audio. Explore
      and a shared link never draw: they play the voice the episode was made
      in, or the default.

    On a machine with no production voice this is what the request named,
    exactly as before - there is no bank to draw from, only a tone.
    """
    engine = _voice_engine()
    if not engine:
        return asked or None
    slug = ""
    if where == "search":
        if asked and voice_bank.known(asked):
            slug = voice_bank.slug_of(asked)
        else:
            slug = _chosen_voice(user)
    else:
        key = _episode_key(plan)
        store = SCRIPT_CACHE
        try:
            slug = store.voice_of(key) if (key and store is not None) else ""
        except Exception:  # noqa: BLE001
            slug = ""
        if slug and not voice_bank.known(slug):
            slug = ""
        if not slug and where not in ("explore", "share"):
            slug = voice_bank.random_slug()
            if key and store is not None and hasattr(store, "set_voice"):
                # First one wins: two listeners tapping one tile at once
                # hear the same voice.
                slug = store.set_voice(key, slug) or slug
    if not slug or voice_bank.is_default(slug):
        return None
    return f"{engine}:{slug}"


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
    #: No `max_length` here on purpose: pydantic would refuse an oversized
    #: photo with a 422 the interface cannot read, where mixes.clean_cover
    #: refuses it with a sentence the listener can act on.
    cover: Optional[str] = None
    #: When the listener means to hear the mix, "HH:MM"; "" removes it. It is
    #: when their phone is told the mix is ready (`push.py`), never when its
    #: episodes are written.
    listen_at: Optional[str] = Field(None, max_length=8)
    #: Their IANA zone for `listen_at` (the browser's own); unreadable reads
    #: as the edition's zone.
    listen_tz: Optional[str] = Field(None, max_length=64)


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
    people: dict = {}
    return {
        # A mix added from somebody else's says whose it was.
        "mixes": [dict(m.as_dict(), **_source_label(m, people))
                  for m in MIXES.list_for_user(user)],
        "starters": [
            {"name": name, "topic_ids": list(ids)}
            for name, ids in mixes_mod.STARTER_MIXES
        ],
    }


@app.get("/api/mixes/sample")
async def sample_mix(request: Request):
    """An example DailyFAM playlist for a listener with no account.

    Built from the evergreen bank (`topics.GUEST_SAMPLE_MIX`), so a guest sees
    what a mix *is* - a name, a handful of briefings and a play button -
    instead of only a wall. Nothing is stored and nothing is written: each
    item asks for the bank tile's own question at the browse length, the
    same episode myFAM's tile is, and a guest's tap on it plays only kept
    audio (see `_guest_play_gated`). Open to everybody; the interface asks for
    it only when `/api/mixes` answered 401.
    """
    _read_limit(request)
    name, ids = topics_mod.GUEST_SAMPLE_MIX
    items = []
    for topic_id in ids:
        topic = topics_mod.BANK_BY_ID.get(topic_id)
        if topic is None:
            continue
        item = mixes_mod.MixItem(topic.id, topic.title, topic.query, False,
                                 topic.subtitle, topic.icon).as_dict()
        # The bank tile's own words, never a dated edition: this is the same
        # episode as the myFAM tile, so one kept recording serves both.
        item.update(daily_prompt="", prompt="", minutes=BROWSE_MINUTES)
        _mark_guest_tiles([item], BROWSE_MINUTES)
        items.append(item)
    return {"mix": {"id": "sample", "name": name, "sample": True,
                    "items": items, "topics": items, "topic_ids": list(ids),
                    "custom_count": 0, "public": False, "cover": "",
                    "source_id": "", "created_at": 0, "updated_at": 0},
            "note": ("An example playlist. Create an account to make your "
                     "own - named for when you listen, fresh every morning.")}


@app.post("/api/mixes")
async def create_mix(req: MixRequest, request: Request):
    _read_limit(request)
    try:
        # Public unless the request says otherwise: a new mix is public by
        # default, at the owner's direction.
        account = _require_account(request)
        _require_can_post(account)
        # The cheap checks first, so a refused mix never pays for a photo check.
        mixes_mod.clean_name(req.name or "")
        if req.cover:
            mixes_mod.clean_cover(req.cover)
        await _check_photo(request, account, req.cover)
        mix = MIXES.create(account, req.name or "", req.topic_ids or [],
                           req.cover or "", public=req.public is not False)
    except mixes_mod.MixError as exc:
        # Phrased for the listener: these are things they did, not faults.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _record_mix_adds(mix)
    _write_mix_ahead(mix)
    return mix.as_dict()


@app.patch("/api/mixes/{mix_id}")
async def update_mix(mix_id: str, req: MixRequest, request: Request):
    _read_limit(request)
    account = _require_account(request)
    kept = MIXES.get(account, mix_id)
    before = kept if req.topic_ids is not None else None
    if req.name is not None or req.cover or req.public:
        _require_can_post(account)
    if req.cover:
        try:
            if req.name is not None:
                mixes_mod.clean_name(req.name)
            mixes_mod.clean_cover(req.cover)
        except mixes_mod.MixError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await _check_photo(request, account, req.cover,
                           before=(kept.cover if kept else "") or "")
    try:
        mix = MIXES.update(account, mix_id, req.name, req.topic_ids, req.public,
                           req.cover, req.listen_at, req.listen_tz)
    except mixes_mod.MixError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if req.topic_ids is not None:
        _record_mix_adds(mix, before=before)
        _write_mix_ahead(mix, before=before)
    return mix.as_dict()


def _record_mix_adds(mix, before=None) -> None:
    """Log a `mix_add` for every item this write put into a mix (§202).

    Only what is new - an item already in the mix before this write was
    counted when it went in, and renaming a mix or reordering it adds
    nothing. A mix needs an account, so this listener is always remembered;
    a failure here costs one signal and never the save.
    """
    had = {item.id for item in (before.items if before else ())}
    for item in mix.items:
        if item.id in had:
            continue
        try:
            EVENTS.record(topics_mod.Event(
                mix.user_id, topics_mod.MIX_ADD, item.id, item.query,
                mixes_mod.taste_tags(item)))
        except Exception:  # noqa: BLE001 - a taste signal never fails a save
            log.exception("could not record a mix add for %r", item.id)


@app.delete("/api/mixes/{mix_id}")
async def delete_mix(mix_id: str, request: Request):
    _read_limit(request)
    if not MIXES.delete(_require_account(request), mix_id):
        raise HTTPException(status_code=404, detail="That mix no longer exists.")
    return {"ok": True}


def _may_be_notified(user_id: str) -> bool:
    """Whether "your mix is ready" may reach this account: always, unless the
    waitlist is running and they are not let in yet (WAITLIST.md)."""
    if not settings.waitlist:
        return True
    try:
        return WAITLIST.status_of(user_id) == waitlist_mod.ACTIVE
    except Exception:  # noqa: BLE001 - unsure is no
        log.exception("push: could not read the waitlist status of %r", user_id)
        return False


# ---------------- "Your mix is ready" (the 10.1 packet, third set) ----------------
#
# A mix's listen time is when this server tells the listener's phone the mix
# is ready; it never moves when the episodes are written. `push.py` has the
# rules. These three let a page (or, later, the native app) say whether this
# server can deliver at all, and hand over or take back where to deliver.

class PushSubscription(BaseModel):
    #: The browser's PushSubscription.toJSON(): {endpoint, keys: {p256dh, auth}}.
    subscription: dict = Field(default_factory=dict)


@app.get("/api/push")
async def push_status(request: Request):
    """Whether this server sends notifications, its public key if so, and
    whether this listener has anywhere to receive one."""
    _read_limit(request)
    body = push_mod.status()
    user = _listener(request)
    body["subscribed"] = bool(user) and _has_account(request) and bool(PUSH.subscriptions(user))
    return body


@app.post("/api/push/subscribe")
async def push_subscribe(req: PushSubscription, request: Request):
    _read_limit(request)
    account = _require_account(request)
    if not push_mod.status()["available"]:
        raise HTTPException(status_code=409, detail=push_mod.status()["reason"])
    try:
        PUSH.subscribe(account, req.subscription)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.delete("/api/push/subscribe")
async def push_unsubscribe(request: Request, endpoint: str = Query("", max_length=2048)):
    _read_limit(request)
    return {"ok": True, "removed": PUSH.unsubscribe(_require_account(request), endpoint)}


# ---------------- Other listeners' public mixes (DailyFAM search) ----------------
#
# DailyFAM is also where somebody finds other people's daily playlists. A mix
# made public is findable by its name, by any topic in it, or by whose it is,
# and anybody can add a public mix to their own DailyFAM - which makes a copy
# that is theirs from then on (`MixStore.add_copy` says why a copy and not a
# link). Only what the owner chose to publish is ever returned, and like
# `/api/person` the response carries no listener id.


def _public_mix(mix: "mixes_mod.Mix", viewer: str, added: set,
                people: dict | None = None) -> dict:
    """A mix as somebody other than its owner sees it."""
    people = people if people is not None else {}
    if mix.user_id not in people:
        people[mix.user_id] = SOCIAL.person(mix.user_id)
    owner = people[mix.user_id] or {}
    body = mix.public_dict()
    who = mixes_mod.owner_label(owner.get("name") or "", owner.get("handle") or "")
    for item in body["items"]:
        # "Added by you" is the owner's own view of a typed topic.
        if item.get("custom"):
            item["subtitle"] = f"Typed in by {who}"
    body.pop("topics", None)
    body.update(
        # No avatar: it is an inline data URL, it would ride along once per
        # mix, and the owner's profile - one tap away - carries it anyway.
        owner={"name": owner.get("name") or "", "handle": owner.get("handle") or ""},
        mine=bool(viewer) and mix.user_id == viewer,
        added=mix.id in added,
    )
    return body


def _source_label(mix: "mixes_mod.Mix", people: dict) -> dict:
    """Whose mix a copy came from, for the owner's own list."""
    if not mix.source_user:
        return {}
    if mix.source_user not in people:
        people[mix.source_user] = SOCIAL.person(mix.source_user)
    owner = people[mix.source_user] or {}
    if not (owner.get("name") or owner.get("handle")):
        # Nothing to name them by - an owner who never set one, or whose
        # account has gone. "From another listener" would tell nobody anything.
        return {}
    return {"from": {"name": owner.get("name") or "", "handle": owner.get("handle") or ""}}


@app.get("/api/mixes/public")
async def public_mixes(request: Request, q: str = Query("", max_length=80)) -> dict:
    """Other listeners' public mixes, for the search bar at the top of DailyFAM.

    Empty `q` is every public mix, most recently changed first - what the bar
    shows the moment it is tapped. Otherwise each word must be found in the
    mix's name, a topic in it, or its owner's name or handle
    (`mixes.match_score`), so "gym", "AI updates" and "@sam" all work.

    Not account-gated: finding and hearing a mix needs no account, the same
    as every other listening surface. Adding one does.
    """
    _read_limit(request)
    viewer = _listener(request)
    added = MIXES.added_from(viewer) if viewer and _has_account(request) else set()
    people: dict = {}
    scored = []
    # With nothing typed only the newest are shown, so only a few more than
    # that are read (an empty mix is skipped); a search reads the lot.
    scan = mixes_mod.PUBLIC_SCAN if q.strip() else mixes_mod.MAX_PUBLIC_RESULTS * 3
    for position, mix in enumerate(MIXES.all_public(exclude_user=viewer, limit=scan)):
        if not mix.items:
            continue
        if q.strip():
            if mix.user_id not in people:
                people[mix.user_id] = SOCIAL.person(mix.user_id)
            owner = people[mix.user_id] or {}
            score = mixes_mod.match_score(mix, q, owner.get("name") or "",
                                          owner.get("handle") or "")
            if not score:
                continue
        else:
            score = 1
        scored.append((-score, position, mix))
    scored.sort(key=lambda row: (row[0], row[1]))
    return {
        "query": q.strip(),
        "mixes": [_public_mix(m, viewer, added, people)
                  for _, _, m in scored[:mixes_mod.MAX_PUBLIC_RESULTS]],
    }


@app.get("/api/mixes/public/{mix_id}")
async def public_mix(mix_id: str, request: Request) -> dict:
    """One public mix, for a search result opened or a shared link followed.
    A private mix answers exactly like one that does not exist."""
    _read_limit(request)
    viewer = _listener(request)
    mix = MIXES.get_public(mix_id, viewer=viewer)
    if not mix:
        raise HTTPException(status_code=404,
                            detail="That mix is private or no longer exists.")
    added = MIXES.added_from(viewer) if viewer and _has_account(request) else set()
    return _public_mix(mix, viewer, added)


@app.post("/api/mixes/{mix_id}/add")
async def add_public_mix(mix_id: str, request: Request) -> dict:
    """The (+) on somebody else's mix: put it in this listener's DailyFAM."""
    _read_limit(request)
    user = _require_account(request)
    source = MIXES.get_public(mix_id)
    if not source:
        raise HTTPException(status_code=404,
                            detail="That mix is private or no longer exists.")
    owner = SOCIAL.person(source.user_id) or {}
    try:
        mix = MIXES.add_copy(user, source, mixes_mod.owner_label(
            owner.get("name") or "", owner.get("handle") or ""))
    except mixes_mod.MixError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _record_mix_adds(mix)
    _write_mix_ahead(mix)
    return dict(mix.as_dict(), **_source_label(mix, {}))


@app.delete("/api/mixes/{mix_id}/add")
async def remove_public_mix(mix_id: str, request: Request) -> dict:
    """The same (+) tapped again: take that mix back out of this listener's
    DailyFAM. `mix_id` is the original's, as on the add."""
    _read_limit(request)
    if not MIXES.remove_copy(_require_account(request), mix_id):
        raise HTTPException(status_code=404, detail="That mix is not in your myFAM.")
    return {"ok": True}


def _mix_url(mix_id: str, request: Request) -> tuple[str, bool]:
    base = _public_base(request)
    return (f"{base}/m/{mix_id}" if base else f"/m/{mix_id}"), bool(base)


@app.post("/api/mixes/{mix_id}/share")
async def share_mix(mix_id: str, request: Request) -> dict:
    """Share a whole mix: the link and the words, per destination.

    The same destinations and the same hand-off rules as sharing an episode
    (`sharing.render_mix`), and the same promise - FAM posts nothing and holds
    no token. **Only a public mix can be shared**, because the link opens it
    for whoever follows it; the interface asks before making a private one
    public rather than doing it quietly.
    """
    _read_limit(request)
    user = _require_account(request)
    mix = MIXES.get(user, mix_id)
    if not mix:
        raise HTTPException(status_code=404, detail="That mix no longer exists.")
    if not mix.public:
        raise HTTPException(status_code=409,
                            detail="Only a public mix can be shared. Make it public first.")
    url, public = _mix_url(mix.id, request)
    titles = [i.title for i in mix.items]
    base = _public_base(request)
    card = f"/api/mixes/{quote(mix.id)}/card"
    return {
        "url": url,
        "public": public,
        "card": (base + card) if base else card,
        "targets": {t.key: sharing.render_mix(t.key, name=mix.name, topics=titles, url=url)
                    for t in sharing.TARGETS},
    }


@app.get("/api/mixes/{mix_id}/card")
async def mix_card(mix_id: str, request: Request) -> Response:
    """The story image for a shared mix, as SVG - see `share_card`."""
    _read_limit(request)
    mix = MIXES.get_public(mix_id)
    if not mix:
        raise HTTPException(status_code=404, detail="That mix is private or no longer exists.")
    owner = SOCIAL.person(mix.user_id) or {}
    svg = sharing.story_card(mix.name, sharing.mix_topics_line([i.title for i in mix.items], 3),
                             0, owner.get("handle") or "", pill="Daily mix")
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=300"})


@app.get("/m/{mix_id}")
async def open_shared_mix(mix_id: str):
    """Where a shared mix link lands: the app, opened on that mix, with the
    (+) that adds it. Unlike an episode link (`/s/`) there is no stand-alone
    page, because what a mix link offers - adding it to your own DailyFAM -
    only exists inside the app."""
    return RedirectResponse(url=f"/?mix={quote(mix_id[:64], safe='')}", status_code=302)


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
    #: §190: whether the player may name them as an episode's searcher.
    searches_public: Optional[bool] = None


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
        # Both kinds of choice, since §202: the facets and the named subjects
        # from the interests page. `taste` holds every one of them constant.
        prefs = PREFS.get(listener.user_id)
        return tuple(prefs.interests) + tuple(prefs.topics)
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


# ---------------- Consent: a listener's words and the AI (5.1.2(i)) --------
# `consent.py` says why. The answer is asked once by the client, kept here,
# and held to on the generation path.

def _client_asks_consent(request: Request) -> bool:
    """Whether this client knows to ask (`consent.py`): the web page this
    server serves, and every iOS build. A kept older web release cannot ask,
    and is not broken by a question it never learned (`old-clients`); a tool
    or a test with no client header is not an app at all."""
    parsed = client_versions.parse(request.headers.get(client_versions.HEADER))
    if not parsed:
        return False
    platform, version = parsed[0], parsed[1]
    return platform == "ios" or (platform, version) == client_versions.LIVE


def _sends_listener_words(where: str, context: str, attach: str,
                          topic_id: str = "", own: bool = False) -> bool:
    """Whether a generation carries something of this listener to the
    writer: a search they typed or spoke, an attachment, or the "what changed
    where you live" tile, whose question names the place they set
    (`startup.LOCAL_ID`, §223). Every other myFAM, DailyFAM or Trending tile
    is FAM's own question.

    A follow-up (`context`) is not counted on its own (§226): the one the
    post-episode grid starts is FAM's own `<<NEXT>>` prediction, and refusing
    it put the consent question over a myFAM listener's next episode. A
    *typed* Go Deeper question is the listener's words: the client says so
    with `own=1`, and both clients ask before sending one (`confirmGoDeeper`,
    `ConsentModel.ensure`)."""
    return (where == "search" or bool(attach) or own
            or topic_id == startup.LOCAL_ID)


CONSENT_REQUIRED = ("FAM needs your OK before it sends what you ask to "
                    "Anthropic to write the episode.")

TERMS_REQUIRED = ("Tick the box to agree to the Terms and the Privacy Policy "
                  "before creating an account.")


def _client_draws_terms(request: Request) -> bool:
    """Whether this client draws the sign-up checkbox (§228): the app's page,
    the waitlist page and every iOS build. A kept older release never had
    one and is not refused for it (`old-clients`); a tool with no client
    header is not a person agreeing to anything."""
    parsed = client_versions.parse(request.headers.get(client_versions.HEADER))
    if not parsed:
        return False
    platform, version = parsed[0], parsed[1]
    return platform == "ios" or (platform == "web" and version in ("live", "waitlist"))


def _require_terms(request: Request, accepted: bool) -> None:
    """Before an account is created: the box was ticked, where there is one."""
    if not accepted and _client_draws_terms(request):
        raise HTTPException(status_code=400, detail=TERMS_REQUIRED)


def _record_terms(request: Request, user_id: str) -> None:
    """Keep the acceptance - which version, when, from which client - as the
    record that this person agreed. Never fails a sign-up."""
    try:
        CONSENT.record(user_id, True, scope=consent_mod.TERMS,
                       version=consent_mod.TERMS_VERSION,
                       client=request.headers.get(client_versions.HEADER, ""))
    except Exception:  # noqa: BLE001 - logged; the account still exists
        log.exception("could not record a terms acceptance")


def _require_ai_consent(request: Request, user: str) -> None:
    """403 with `X-FAM-Consent: ai` when a client that asks has no yes on
    record. The client shows the notice and sends the request again."""
    if not _client_asks_consent(request) or CONSENT.given(user):
        return
    raise HTTPException(status_code=403, detail=CONSENT_REQUIRED,
                        headers={"X-FAM-Consent": consent_mod.AI})


@app.get("/api/consent")
async def consent_read(request: Request) -> dict:
    """What this listener was asked and what they said, with the words every
    client shows, so the app and the web say the same thing."""
    _read_limit(request)
    user = _listener(request)
    return {"ai": consent_mod.describe(CONSENT.get(user)),
            "terms": consent_mod.describe_terms(CONSENT.get(user, consent_mod.TERMS))}


class ConsentRequest(BaseModel):
    scope: str = Field(consent_mod.AI, max_length=16)
    allow: bool
    #: The notice version the listener was shown. A yes to an older wording
    #: is kept as what it was and does not count as a yes to this one.
    #: Left out, it is the current version of `scope` (as before §228).
    version: Optional[int] = Field(None, ge=1, le=1000)


@app.post("/api/consent")
async def consent_write(req: ConsentRequest, request: Request) -> dict:
    """Record a yes or a no. Withdrawing is the same call with `allow: false`."""
    user = _require_listener(request)
    if req.scope == consent_mod.TERMS and not req.allow:
        # Agreeing to the terms is what an account is; leaving is deleting it.
        raise HTTPException(status_code=400, detail=(
            "To stop agreeing to the Terms, delete your account in Settings."))
    try:
        CONSENT.record(
            user, req.allow, scope=req.scope,
            version=min(req.version or consent_mod.current_version(req.scope),
                        consent_mod.current_version(req.scope)),
            client=request.headers.get(client_versions.HEADER, ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ai": consent_mod.describe(CONSENT.get(user)),
            "terms": consent_mod.describe_terms(CONSENT.get(user, consent_mod.TERMS))}


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
                           weekly_recap=req.weekly_recap, intro_done=req.intro_done,
                           searches_public=req.searches_public)
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
    # A written live story or startup question is offered under its
    # episode's own title and category here too (§189), as on myFAM.
    tiles = [t.as_dict() for t in picks]
    written = _written_probe(BROWSE_MINUTES)
    for tile in tiles:
        tile["cached"] = written(tile.get("query", ""))
    _name_written_tiles(tiles, BROWSE_MINUTES)
    return {"topics": tiles, "algo": EVENTS.algo_stamp()}


#: What a guest is told when they tap an episode that has not been made yet.
#: Making it would cost a script and a GPU, and a guest's sample page is
#: promised to cost nothing (at the owner's direction).
GUEST_GATE_MESSAGE = ("Create a free account to hear this one. Episodes are "
                      "made fresh for listeners with an account.")


def _mark_guest_tiles(tiles: list[dict], minutes: int) -> None:
    """Say on each guest tile whether its script is written, and whether it
    would play for a guest at all.

    `playable` is the cheap half of `_guest_play_gated`: written script and
    kept audio, read from SQLite. Never a model call, never the voice."""
    written = _written_probe(minutes)
    for tile in tiles:
        tile["cached"] = written(tile.get("query", ""))
        tile["playable"] = bool(tile["cached"]
                                and _audio_is_kept(tile.get("query", ""), minutes))


import startup


def _name_written_tiles(tiles: list[dict], minutes: int) -> None:
    """A written tile is called what its episode turned out to be about.

    **Startup tiles** (the 27/09 packet): a question asked before anything
    is retrieved - "this week's biggest storylines in sports" - names no
    subject, so once a tap has written it the card takes the model's
    `<<TITLE:>>` and `<<SUMMARY:>>`.

    **And live stories** (§189, at the owner's direction): a Trending or
    pool tile is titled by the composer from headlines alone, before anything
    was researched, and the episode the writer then made can be about
    something more specific or different. The first listener may hear it
    under the composer's guess; once it is cached, every listener after them
    sees the writer's title and summary, and the tile's picture, facet word
    and tags come from the writer's `<<CATEGORY:>>` rather than from the
    composer's. Never a bank tile: its title is its own (§104).

    Only what the cache says is *current* (`cached`), so a stale episode's
    title never sits on a tile whose next tap writes a new one. A few local
    reads per written tile, never a model call.
    """
    for tile in tiles:
        tid = str(tile.get("id", ""))
        renamed = (tid.startswith(startup.ID_PREFIX)
                   or (bool(tile.get("source"))
                       and tid not in topics_mod.BANK_BY_ID))
        if not tile.get("cached") or not renamed:
            continue
        try:
            key = _episode_key(_validated_plan(tile.get("query", ""), minutes))
            title = SCRIPT_CACHE.title(key) if key and SCRIPT_CACHE else ""
            summary = (getattr(SCRIPT_CACHE, "summary", lambda _k: "")(key)
                       if title else "")
            category = (getattr(SCRIPT_CACHE, "category", lambda _k: "")(key)
                        if key and SCRIPT_CACHE else "")
        except Exception:  # noqa: BLE001 - a card's name is never worth a 500
            log.exception("could not name a written tile")
            continue
        if title:
            tile["title"] = title
            if summary:
                tile["angle"] = summary
        if category:
            _categorise_written_tile(tile, category)


def _categorise_written_tile(tile: dict, words: str) -> None:
    """Redraw a written tile's picture, facet word and tags from the writer's
    category (§189). Unresolvable words change nothing - the tile keeps the
    composer's category rather than losing one."""
    import stories as stories_mod
    import thumbnails

    node = stories_mod.resolve_category(words, near=True)
    if not node:
        return
    text = " ".join(str(tile.get(k, "")) for k in ("title", "angle", "query"))
    tags = stories_mod.refine_tags(tile.get("tags") or (), node, text)
    tile["tags"] = list(tags)
    found = thumbnails.pick_for_tile(tile.get("query", ""), tags, category=node)
    tile["thumb"] = found["url"] if found else ""
    tile["thumb_borrowed"] = bool(found and found.get("fallback"))
    tile["thumb_facet"] = (found["facet"] if found
                           else stories_mod.facet_for(node))


def _episode_category(key: str) -> str:
    """The category-tree node the cached episode under `key` says it is
    about (§189), or "" when there is none or the tree cannot place it.

    **The one reader of a written episode's category** (§209): the tile, the
    player's picture, the ranking and the logged tags all come through here,
    so one episode is filed under one node everywhere. The words are stored
    as the writer put them and resolved against the tree as it is now, so a
    tree that has grown since still places an older episode. One local read;
    never a model call."""
    if not key or SCRIPT_CACHE is None:
        return ""
    try:
        words = getattr(SCRIPT_CACHE, "category", lambda _k: "")(key)
    except Exception:  # noqa: BLE001 - a category is never worth a failure
        return ""
    if not words:
        return ""
    import stories as stories_mod

    # The nearest node by meaning when no phrase matches (§235).
    return stories_mod.resolve_category(words, near=True)


def _heard_key(query: str, minutes: int, episode: str = "",
               context: str = "") -> str:
    """The cache key of the episode a request is about, or "".

    The heard episode's id (`X-FAM-Episode`, §173) when the client sent one:
    it names exactly what played, whatever its length, context or voice.
    Else the question at `minutes` (§209 - this used to assume the browse
    length, so a search at any other length never found its own category).
    Never raises."""
    if SCRIPT_CACHE is None:
        return ""
    try:
        if episode and parse_episode_id(episode) is not None:
            key = SCRIPT_CACHE.resolve_episode(episode)
            if key:
                return key
        if not query or not minutes:
            return ""
        return _episode_key(_validated_plan(query, minutes, context)) or ""
    except Exception:  # noqa: BLE001 - a lookup, never a failure
        return ""


def _written_category(query: str, minutes: int, episode: str = "") -> str:
    """The category-tree node the cached episode for `query` (or the heard
    `episode`) says it is about (§189), or "" when it is not written, has
    none, or cannot be placed. One local read; never a model call."""
    return _episode_category(_heard_key(query, minutes, episode))


def _playing_category(query: str, minutes: int, episode: str = "",
                      context: str = "") -> str:
    """The node the episode on the player is about, as early as anything
    knows it (10.10 #2): the written episode's own category (§209) once it
    is cached, else the one on its live track - the writer's when the script
    has finished, the brief's from before the first word
    (`Brief.category`). "" when neither can be placed. Local reads only."""
    key = _heard_key(query, minutes, episode, context)
    if not key:
        return ""
    node = _episode_category(key)
    if node:
        return node
    words = live_captions.read_category(key)
    if not words:
        return ""
    import stories as stories_mod

    return stories_mod.resolve_category(words, near=True)


#: How long one live story's written category is remembered (§209). The
#: ranker asks for every story in the pool on every browse page, and an
#: episode that has just been written can wait this long to be re-filed.
WRITTEN_CATEGORY_SECONDS = 60.0
#: The memo's ceiling: the pool and the edition hold about fifty stories.
MAX_WRITTEN_CATEGORY_MEMO = 2000
_WRITTEN_CATEGORY_MEMO: dict[str, tuple[str, float]] = {}


def _written_category_probe(query: str) -> str:
    """`topics.set_written_category`'s source: the node a live story's
    written episode (at the browse length, every live tile's) says it is
    about, or "". Local reads, memoised briefly; never a model call."""
    now = time.monotonic()
    hit = _WRITTEN_CATEGORY_MEMO.get(query)
    if hit is not None and now - hit[1] < WRITTEN_CATEGORY_SECONDS:
        return hit[0]
    node = _written_category(query, BROWSE_MINUTES)
    if len(_WRITTEN_CATEGORY_MEMO) >= MAX_WRITTEN_CATEGORY_MEMO:
        _WRITTEN_CATEGORY_MEMO.clear()
    _WRITTEN_CATEGORY_MEMO[query] = (node, now)
    return node


topics_mod.set_written_category(_written_category_probe)


def _event_tags(topic_id: str, text: str, minutes: int,
                episode: str = "") -> tuple:
    """The tags an interaction is logged with: `topics.tags_for_id`, refined
    by what the written episode said it was about (§189).

    A bank or catalogue tile declares its own tags and keeps them. A startup
    question keeps its facet and gains the episode's category beside it. A
    live story, or anything else, is corrected by it (`stories.refine_tags`):
    the category is added and tags of another facet - matched off headline
    words before anything was researched - are dropped, while its own
    facet's tags (a team, a league) stay. So the taste model learns "boxing"
    from a play of a boxing episode, not "science" from a headline that said
    "study"."""
    tags = topics_mod.tags_for_id(topic_id, text)
    if (topic_id in topics_mod.BANK_BY_ID
            or topic_id in topics_mod.CATALOGUE_BY_ID):
        return tags
    node = _written_category(text, minutes, episode)
    if not node:
        return tags
    import stories as stories_mod

    if topic_id in topics_mod.STARTUP_BY_ID:
        return tuple(sorted(set(tags)
                            | set(stories_mod.category_tags(node, text))))
    return stories_mod.refine_tags(tags, node, text)


def _audio_is_kept(query: str, minutes: int) -> bool:
    """Whether any voice's audio for this episode is kept beside its script.
    A read, and only a read - see `ScriptCache.has_any_audio`."""
    store = SCRIPT_CACHE
    if store is None or not hasattr(store, "has_any_audio"):
        return False
    try:
        key = _episode_key(_validated_plan(query, minutes))
        return bool(key) and store.has_any_audio(key)
    except Exception:  # noqa: BLE001 - a tile's marker is never worth a 500
        return False


def _guest_play_gated(request: Request, where: str, topic_id: str) -> bool:
    """Whether this play is a guest's tap on the sample pages - myFAM, the
    DailyFAM example playlist, or any bank tile - which may never write a
    script or wake the voice."""
    if _has_account(request):
        return False
    return where in BROWSE_SURFACES or topic_id in topics_mod.BANK_BY_ID


def _seen_ids(seen: str) -> set:
    """The comma-separated tile ids a "View more" screen has already shown."""
    return {s.strip() for s in (seen or "").split(",") if s.strip()}


@app.get("/api/myfam/section")
async def myfam_section(request: Request,
                        key: str = Query(..., max_length=32),
                        minutes: int = Query(DEFAULT_MINUTES, ge=1, le=10),
                        interests: str = Query("", max_length=200),
                        page_size: int = Query(0, ge=0, le=40),
                        seen: str = Query("", max_length=6000)):
    """One myFAM rail, at full length, for the screen behind its "View more".

    With `page_size` (the app sends `topics.VIEW_MORE_PAGE`, eight) it is one
    page of that length: the first tiles of the ranking not in `seen`, which
    is how the screen's refresh deals eight new ones (§165). Without it, the
    whole list, as before.

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
    minutes = BROWSE_MINUTES   # §147: only searchFAM offers a length
    user = _listener(request)
    if not _has_account(request):
        # A guest's page is the evergreen bank on every rail, and so is the
        # screen behind each rail's "View more". See `topics.guest_feed`.
        try:
            body = topics_mod.guest_section(key)
        except KeyError as exc:
            raise HTTPException(status_code=404,
                                detail="No such section.") from exc
        _mark_guest_tiles(body["topics"], minutes)
        body["ready"] = sum(1 for t in body["topics"] if t["cached"])
        body["minutes"] = minutes
        body["algo"] = EVENTS.algo_stamp()
        return topics_mod.page_section(body, _seen_ids(seen), page_size)
    written = _written_probe(minutes)
    try:
        place = _place_for(request)
        circle = SOCIAL.circle_of(user)
        body = topics_mod.build_section(
            EVENTS, user, key, interests=_interests_for(request, interests),
            circle=circle, written=written,
            written_at=_written_at_probe(minutes),
            episode_info=_episode_info_probe(minutes),
            authored=_authored_by_circle(circle),
            place=place.words, place_name=place.label,
            has_account=_has_account(request),
            country=_country_for(request, place))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail="No such section.") from exc

    body["topics"] = _drop_removed(user, body["topics"], minutes)
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
    _name_written_tiles(body["topics"], minutes)
    for group in body.get("groups", ()):
        _name_written_tiles(group["topics"], minutes)
    body["topics"].sort(key=lambda t: not t["cached"])
    body["ready"] = sum(1 for t in body["topics"] if t["cached"])
    body["minutes"] = minutes
    # One page of it, when the screen asks for one - and only that page is an
    # impression below: a tile nobody was shown was not passed over.
    topics_mod.page_section(body, _seen_ids(seen), page_size)
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


def _episode_info_probe(minutes: int):
    """A memoised `query -> topics.CachedEpisode | None` for one request.

    What the three crowd rails read - they show cached episodes only, and
    "What you missed last week" also refuses one written from a live feed.
    None when the script is not in the cache at this length, which is also
    the answer for anything unaskable. Two local reads per episode (the
    sentences, then the title and provenance of a hit), never a model call.
    """
    answers: dict[str, Optional[topics_mod.CachedEpisode]] = {}

    def probe(query: str) -> Optional[topics_mod.CachedEpisode]:
        if query not in answers:
            answers[query] = _cached_episode(query, minutes)
        return answers[query]

    return probe


def _cached_episode(query: str, minutes: int):
    if not query or SCRIPT_CACHE is None:
        return None
    try:
        key = _episode_key(_validated_plan(query, minutes))
    except HTTPException:
        return None
    if not key:
        return None
    try:
        if not _cache_holds(key):
            return None
        title = SCRIPT_CACHE.title(key) or ""
        found = provenance_mod.Provenance.from_json(SCRIPT_CACHE.sources(key))
    except Exception:  # noqa: BLE001 - a browse row is never worth a 500
        log.exception("cache probe failed; treating %r as unwritten", query)
        return None
    feeds = tuple(item.label for item in found.items
                  if getattr(item, "kind", "") == provenance_mod.LIVE)
    return topics_mod.CachedEpisode(title=title, live_feeds=feeds)


def _authored_by_circle(circle) -> list:
    """The live cache rows the listener's circle first wrote, for the
    "created" half of "What your friends are listening to"."""
    reader = getattr(SCRIPT_CACHE, "authored_by", None)
    if reader is None or not circle:
        return []
    try:
        return reader(circle, since=time.time() - topics_mod.FRIENDS_WINDOW)
    except Exception:  # noqa: BLE001 - a browse row is never worth a 500
        log.exception("could not read the circle's episodes")
        return []


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


@app.get("/api/episode/card")
async def episode_card(request: Request,
                       q: str = Query("", max_length=300),
                       title: str = Query("", max_length=300),
                       minutes: int = Query(0, ge=0, le=10),
                       episode: str = Query("", max_length=80),
                       context: str = Query("", max_length=300)) -> dict:
    """What the player draws around an episode (§190): its picture, and who
    searched it.

    * `thumb` - the picture for the episode's words, drawn behind the title
      (`thumbnails.pick_for_player`): the tile's own, else one borrowed from
      its branch, its facet or a stable choice, so a searched episode never
      plays over an empty screen. "" only when nothing is approved.
    * `fallback` - True when `thumb` was borrowed; the client asks again
      once the writer's title lands.
    * `searcher` - the handle of the listener whose search wrote it, **only
      when they have turned on `searches_public`** and it is not the asker:
      what somebody searched is theirs unless they say otherwise
      (`op-friend-profile`). Authorship is provenance (`scripts.author`,
      never in the key); this is the one place it is shown, by the author's
      choice, and the response carries no id.

    `minutes`, `episode` and `context` (all optional) say which episode
    this is, so its own category picks the picture, as it does on the tile
    that opened it (§209) - while it is still being written, the category
    on its live track (`_playing_category`). `placed` says the picture came
    from that category rather than from the words, and the player lets it
    replace one matched off the words.

    Reads the cache and the profile store; no model call.
    """
    _read_limit(request)
    asked = (q or "").strip()
    words = (title or "").strip()
    thumb = ""
    borrowed = False
    placed = False
    try:
        found = _player_picture(asked, words, minutes, episode, context)
        thumb = found.get("url", "") or ""
        borrowed = bool(found.get("fallback"))
        placed = bool(thumb and found.get("placed"))
    except Exception:  # noqa: BLE001 - a picture is never worth a 500
        log.exception("could not pick a picture for the player")
    searcher = ""
    store = SCRIPT_CACHE if SCRIPT_CACHE is not None else build_cache()
    norm = normalize_query(asked)
    if store is not None and norm:
        listener = _listener(request)
        apart = MODERATION.apart(listener)
        for entry in store.recent(TRENDING_SEARCHES_SCAN, origin="search"):
            if normalize_query(entry.get("query") or "") != norm:
                continue
            author = entry.get("author") or ""
            if author and author in apart:
                break
            if author and author != listener and PREFS.get(author).searches_public:
                handle = SOCIAL.person(author).get("handle") or ""
                searcher = "@" + handle if handle else ""
            break
    return {"thumb": thumb, "fallback": borrowed, "placed": placed,
            "searcher": searcher}


def _player_picture(asked: str, title: str = "", minutes: int = 0,
                    episode: str = "", context: str = "") -> dict:
    """The player's picture for an episode, as `/api/episode/card` draws it
    and an Instant feedback report keeps it: `thumbnails.pick_for_player`
    over the words, with the episode's own category when one is known
    (`placed`). {} when nothing is approved. Never a model call."""
    import thumbnails

    node = _playing_category(asked, minutes, episode, context)
    found = thumbnails.pick_for_player(
        f"{asked} {title}".strip(), key=normalize_query(asked),
        category=node) or {}
    return dict(found, placed=bool(node)) if found else {}


@app.get("/api/episode/topic")
async def episode_topic(request: Request,
                        q: str = Query("", max_length=300),
                        title: str = Query("", max_length=300)) -> dict:
    """What TOPIC an episode falls into, for the player's (+) (§181).

    The (+) adds the episode's *topic* to a DailyFAM mix - a mix follows
    subjects, never episodes (§137) - so the answer is a mix entry exactly as
    `PATCH /api/mixes/{id}` takes it: a followed subject (`f:nfl`,
    `f:nfl~Eagles`, from `topics.episode_subject`), or, when the episode names
    no subject the catalogue knows, the episode's title as a typed topic.
    `label` is what the sheet says: "Add [label] to a DailyFAM mix".
    Words in, words out: no model call, no cache read.
    """
    _read_limit(request)
    words = (title or "").strip()
    asked = (q or "").strip()
    subject, focus = topics_mod.episode_subject(f"{asked} {words}")
    if subject:
        ident = "f:" + subject + ("~" + quote(focus, safe="!'()*")
                                  if focus else "")
        try:
            item = mixes_mod.followed_item(ident)
        except mixes_mod.MixError:
            item = None
        if item is not None:
            return {"id": item.id, "label": focus or item.topic_label,
                    "title": item.title}
    typed = (words or asked)[:mixes_mod.MAX_QUERY]
    if not typed:
        return {"id": "", "label": "", "title": ""}
    return {"id": "", "query": typed, "label": typed, "title": typed}


@app.get("/api/myfam/search")
async def myfam_search(request: Request,
                       q: str = Query("", max_length=200),
                       limit: int = Query(12, ge=1, le=30)) -> dict:
    """myFAM's search button (§181): the cached episodes, made by other
    listeners, most like what was typed.

    A read of the shared cache and nothing else - like Explore it cannot
    cause anything to be written, and a result is played replay-only. Every
    surface's episodes are searched (a myFAM tile somebody else tapped is as
    much "made by others" as a search), the listener's own are left out, as
    Explore leaves them out, and archived rows never appear (`recent`).
    Ranked by `cache.rank_similar`.
    """
    _read_limit(request)
    words = (q or "").strip()
    store = SCRIPT_CACHE if SCRIPT_CACHE is not None else build_cache()
    if not words or store is None:
        return {"episodes": []}
    listener = _listener(request)
    now = time.time()
    try:
        entries = _visible_episodes(listener, store.recent(400, exclude_author=listener))
    except Exception:  # noqa: BLE001 - a search box is never worth a 500
        log.exception("could not read the cache for a myFAM search")
        return {"episodes": []}
    # Off the event loop: with the semantic embedder the first search embeds
    # every entry it has not seen, which is CPU work no other request should
    # wait behind.
    ranked = await asyncio.to_thread(cache_mod.rank_similar, words, entries, limit)
    episodes = []
    for entry in ranked:
        episodes.append({
            "query": entry["query"],
            "title": entry.get("title")
                     or (entry["query"][:1].upper() + entry["query"][1:]),
            "minutes": entry["minutes"],
            "plays": entry.get("plays", 0),
            "sourced_age_seconds": max(
                0.0, now - (entry.get("sourced_at") or entry.get("created") or now)),
            "explicit": bool(entry.get("explicit")),
        })
    return {"episodes": episodes}


#: How far back the A to Z catalogue reads (10.2 feedback): every row kept
#: a week fits on a deployment this size; past it the newest win.
MYFAM_CATALOG_SCAN = 1000


def _catalog_sort_key(title: str) -> tuple[str, str]:
    """A to Z the way a person reads a list: case and leading punctuation
    ignored, and anything not starting with a letter filed under "#" at the
    end, the way a phone's contacts are."""
    folded = re.sub(r"^[^0-9a-z]+", "", (title or "").casefold())
    first = folded[:1]
    letter = first.upper() if "a" <= first <= "z" else "#"
    return ("~" if letter == "#" else letter, folded)


@app.get("/api/myfam/catalog")
async def myfam_catalog(request: Request) -> dict:
    """Search DailyFAM before anything is typed (10.2 feedback): every cached
    episode other listeners made, A to Z by title, each with the `letter` it
    is filed under, so the screen is a catalogue to scroll rather than an
    empty box.

    The same rows and the same rules as `/api/myfam/search` - a read of the
    shared cache, the listener's own left out, archived rows never, played
    replay-only - in a different order. One row per title: two keys that
    turned out to be the same episode (`title-from-content`) are one entry,
    the most played.
    """
    _read_limit(request)
    store = SCRIPT_CACHE if SCRIPT_CACHE is not None else build_cache()
    if store is None:
        return {"episodes": []}
    listener = _listener(request)
    now = time.time()
    try:
        entries = _visible_episodes(
            listener, store.recent(MYFAM_CATALOG_SCAN, exclude_author=listener))
    except Exception:  # noqa: BLE001 - a catalogue is never worth a 500
        log.exception("could not read the cache for the myFAM catalogue")
        return {"episodes": []}
    best: dict[str, dict] = {}
    for entry in entries:
        query = entry.get("query") or ""
        title = (entry.get("title") or (query[:1].upper() + query[1:])).strip()
        if not title:
            continue
        held = best.get(title.casefold())
        if held is not None and held["plays"] >= entry.get("plays", 0):
            continue
        best[title.casefold()] = {
            "query": query,
            "title": title,
            "minutes": entry["minutes"],
            "plays": entry.get("plays", 0),
            "sourced_age_seconds": max(
                0.0, now - (entry.get("sourced_at") or entry.get("created") or now)),
            "explicit": bool(entry.get("explicit")),
        }
    ordered = sorted(best.values(), key=lambda e: _catalog_sort_key(e["title"]))
    for episode in ordered:
        letter = _catalog_sort_key(episode["title"])[0]
        episode["letter"] = "#" if letter == "~" else letter
    return {"episodes": ordered}


# --------------------------------------------------------------------------
# Your categories (§229)
# --------------------------------------------------------------------------
#: How far back a category page reads the cache: every row written today on
#: a deployment this size; past it the newest win.
CATEGORY_SCAN = 1000
#: The orders a category page offers, the first the default.
CATEGORY_SORTS = ("popular", "az", "recent")
#: How long one filing of today's cached episodes serves every category page
#: and every "Your categories" count. A filing is one category read per row.
CATEGORY_MEMO_SECONDS = 30.0
_CATEGORY_MEMO: dict = {"at": 0.0}


def _category_label(node: str) -> str:
    """What a category is called on screen: a facet's label, a tree node's,
    or the id itself with a capital."""
    if node in topics_mod.TAG_LABELS:
        return topics_mod.TAG_LABELS[node]
    try:
        found = topics_mod.category_tree().get(node)
    except Exception:  # noqa: BLE001 - a label is never worth a failure
        found = None
    label = (getattr(found, "label", "") or node or "").strip()
    return " ".join(_label_word(w, i) for i, w in enumerate(label.split()))


#: League and body names said as letters, which a tree node keeps lowercase
#: ("nfl") and a page should not print as "Nfl".
_LETTER_NAMES = frozenset({"nfl", "nba", "wnba", "mlb", "nhl", "ufc", "mls",
                           "ai", "nascar", "fc", "us", "uk", "eu", "un"})
_SMALL_WORDS = frozenset({"and", "of", "the", "in", "on", "for", "to", "a"})


def _label_word(word: str, at: int) -> str:
    """One word of a tree node's label, as a heading prints it."""
    if word.lower() in _LETTER_NAMES:
        return word.upper()
    if word != word.lower():
        return word  # the tree already cased it
    if at and word in _SMALL_WORDS:
        return word
    return word[:1].upper() + word[1:]


def _category_known(node: str) -> bool:
    if node in topics_mod.TAG_LABELS:
        return True
    try:
        return topics_mod.category_tree().get(node) is not None
    except Exception:  # noqa: BLE001
        return False


def _category_children(node: str) -> list[str]:
    """The tree's nodes one level below `node`, A to Z by label."""
    try:
        nodes = topics_mod.category_tree().nodes()
    except Exception:  # noqa: BLE001
        return []
    kids = [n for n, row in nodes.items() if row.parent_id == node]
    return sorted(kids, key=lambda n: _category_label(n).casefold())


def _filed_under(entry: dict) -> tuple[str, frozenset]:
    """(the node one cached episode is filed under, that node and everything
    above it).

    The writer's category through `_episode_category`, the one reader
    (§209). An episode written before the writer named one is filed by its
    words (`topics.tags_for_text`, the keyword map and the tree) - the same
    fallback every other reader of an uncategorised episode uses - and its
    deepest match is its node. ("", empty) when nothing places it."""
    node = _episode_category(entry.get("key", ""))
    tree = topics_mod.category_tree()
    if node:
        try:
            above = tree.ancestors(node)
        except Exception:  # noqa: BLE001
            above = []
        return node, frozenset([node, *above])
    text = f"{entry.get('title', '')} {entry.get('query', '')}"
    try:
        tags = set(topics_mod.tags_for_text(text))
    except Exception:  # noqa: BLE001 - a filing is never worth a failure
        return "", frozenset()
    placed = [t for t in tags if t in topics_mod.TAG_LABELS or tree.get(t)]
    if not placed:
        return "", frozenset()

    def depth(t: str) -> int:
        if t in topics_mod.TAG_LABELS:
            return 0
        try:
            return tree.depth_of(t)
        except Exception:  # noqa: BLE001
            return 0
    placed.sort(key=lambda t: (-depth(t), t))
    return placed[0], frozenset(placed)


def _mix_counts() -> dict:
    """How many mixes follow each of today's DailyFAM questions, by the
    words - the words are an edition episode's cache key (§143)."""
    try:
        return {s["query"]: s["mixes"] for s in daily_edition.subjects(MIXES)}
    except Exception:  # noqa: BLE001 - a count is never worth a failure
        log.exception("could not count mixes for the category pages")
        return {}


def _day_start() -> float:
    """When the listener's day began, by their clock (`listener_clock`)."""
    return listener_clock.now().replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()


def _todays_filed() -> list[dict]:
    """Every episode cached since the start of the listener's day, filed.

    **A read of the shared cache and nothing else** (§229): nothing here
    writes, prefetches or asks a model. What is in it is what is already
    made - everything listeners searched and played, and the edition and
    warmed episodes nobody has tapped yet, which sit in the same cache under
    the key the tap will ask for. Shared by every listener, so it is not yet
    screened or one per title - `_todays_for` does both for one viewer.
    Memoised briefly per day and per store."""
    day_start = _day_start()
    now_mono = time.monotonic()
    stores = (id(SCRIPT_CACHE), id(MIXES), day_start)
    if (_CATEGORY_MEMO.get("stores") == stores
            and now_mono - _CATEGORY_MEMO["at"] < CATEGORY_MEMO_SECONDS):
        return _CATEGORY_MEMO["rows"]
    store = SCRIPT_CACHE if SCRIPT_CACHE is not None else build_cache()
    if store is None:
        return []
    entries = store.recent(CATEGORY_SCAN)
    mixed = _mix_counts()
    rows = []
    for entry in entries:
        made = float(entry.get("created") or entry.get("sourced_at") or 0.0)
        if made < day_start:
            continue
        query = entry.get("query") or ""
        title = (entry.get("title") or (query[:1].upper() + query[1:])).strip()
        if not title:
            continue
        node, chain = _filed_under(entry)
        if not node:
            continue
        plays = int(entry.get("plays") or 0)
        in_mixes = int(mixed.get(query, 0))
        rows.append({
            "query": query,
            "title": title,
            "minutes": entry["minutes"],
            "plays": plays,
            "mixes": in_mixes,
            "popularity": plays + in_mixes,
            "created": made,
            "sourced_at": float(entry.get("sourced_at") or made),
            "explicit": bool(entry.get("explicit")),
            "node": node,
            "chain": chain,
            # For `_visible_episodes` only; never sent (`_category_row`).
            "author": entry.get("author") or "",
        })
    _CATEGORY_MEMO.update(at=now_mono, stores=stores, rows=rows)
    return rows


def _todays_for(viewer: str) -> list[dict]:
    """Today's filed episodes as `viewer` may see them: screened as every
    shelf of other people's episodes is (`_visible_episodes`: nothing taken
    down, reported by them, or from somebody blocked or suspended), then one
    row per title, the most popular, as the A to Z catalogue does."""
    best: dict[str, dict] = {}
    for row in _visible_episodes(viewer, _todays_filed()):
        held = best.get(row["title"].casefold())
        if held is None or (row["popularity"], row["created"]) > (
                held["popularity"], held["created"]):
            best[row["title"].casefold()] = row
    return list(best.values())


def _category_row(row: dict, now: float) -> dict:
    """One episode as a category page sends it: no author, no filing."""
    return {
        "query": row["query"], "title": row["title"],
        "minutes": row["minutes"], "plays": row["plays"],
        "mixes": row["mixes"], "popularity": row["popularity"],
        "made_age_seconds": max(0.0, now - row["created"]),
        "sourced_age_seconds": max(0.0, now - row["sourced_at"]),
        "explicit": row["explicit"], "node": row["node"],
        "node_label": _category_label(row["node"]),
    }


def _category_words(text: str) -> list[str]:
    return re.findall(r"[0-9a-z]+", (text or "").casefold())


@app.get("/api/categories")
async def list_categories(request: Request) -> dict:
    """Every category a listener can follow on myFAM, and the ones they do.

    The eight facets, each with the tree's first level under it, and `all`:
    every node with its path, for the picker's search ("NFL" is three levels
    down). `mine` is what this listener follows, each with how many episodes
    were made in it today. Following is kept for an account (`saved`), like
    a mix; anybody may browse."""
    _read_limit(request)
    listener = getattr(request.state, "listener", None)
    authed = bool(listener is not None and listener.is_authenticated)
    chosen = PREFS.get(listener.user_id).categories if authed else ()
    tree = topics_mod.category_tree()
    try:
        nodes = tree.nodes()
    except Exception:  # noqa: BLE001
        nodes = {}
    facets = [{"id": facet, "label": label,
               "children": [{"id": c, "label": _category_label(c)}
                            for c in _category_children(facet)]}
              for facet, label in topics_mod.TAG_LABELS.items()]
    everything = []
    for node in nodes:
        try:
            above = [a for a in reversed(tree.ancestors(node))]
        except Exception:  # noqa: BLE001
            above = []
        everything.append({"id": node, "label": _category_label(node),
                           "facet": topics_mod._root_facet(node),
                           "path": [_category_label(a) for a in above]})
    everything.sort(key=lambda n: n["label"].casefold())
    try:
        filed = await asyncio.to_thread(_todays_for, _listener(request))
    except Exception:  # noqa: BLE001 - counts are never worth a 500
        log.exception("could not file today's episodes")
        filed = []
    mine = []
    for node in chosen:
        if not _category_known(node):
            continue  # pruned from the tree since; kept, not shown
        mine.append({
            "id": node, "label": _category_label(node),
            "facet": topics_mod._root_facet(node),
            "children": [_category_label(c) for c in _category_children(node)][:4],
            "today": sum(1 for row in filed if node in row["chain"]),
        })
    return {"facets": facets, "all": everything, "mine": mine,
            "saved": authed}


class CategoryFollowRequest(BaseModel):
    """The whole followed list, in order: add, remove and reorder are one
    write, like a mix's items."""

    categories: list[str] = Field(..., max_length=prefs_mod.MAX_CATEGORIES)


@app.post("/api/categories/mine")
async def follow_categories(req: CategoryFollowRequest, request: Request) -> dict:
    """Keep the categories this listener follows. Account only - what is
    kept is what an account is for. Writes no event: following a category
    is a list to browse, never a taste signal (§229)."""
    _read_limit(request)
    user = _require_account(request)
    try:
        prefs = PREFS.save(user, categories=req.categories)
    except prefs_mod.PreferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"categories": list(prefs.categories)}


@app.get("/api/categories/episodes")
async def category_episodes(
    request: Request,
    id: str = Query(..., min_length=1, max_length=120),
    sort: str = Query("popular", max_length=12),
    q: str = Query("", max_length=200),
) -> dict:
    """Today's episodes in one category or anything under it (§229).

    Cached only - this never causes an episode to be written; a tap plays
    what is there (`cached_only`). `sort` is `popular` (plays plus the mixes
    that follow it), `az` (by title, the catalogue's order) or `recent`
    (newest made first). `q` keeps the titles holding every word typed,
    whole or begun. Screened for the viewer (`_todays_for`).
    `subcategories` are the categories one level down that have something
    today, with how many."""
    _read_limit(request)
    node = id.strip().lower()
    if not _category_known(node):
        raise HTTPException(status_code=404, detail="There is no such category.")
    order = sort if sort in CATEGORY_SORTS else CATEGORY_SORTS[0]
    try:
        filed = await asyncio.to_thread(_todays_for, _listener(request))
    except Exception:  # noqa: BLE001 - a category page is never worth a 500
        log.exception("could not file today's episodes")
        raise HTTPException(status_code=503,
                            detail="Could not read today's episodes.")
    inside = [row for row in filed if node in row["chain"]]
    subs = []
    for child in _category_children(node):
        count = sum(1 for row in inside if child in row["chain"])
        if count:
            subs.append({"id": child, "label": _category_label(child),
                         "count": count})
    wanted = _category_words(q)
    if wanted:
        # Inside a word as well as a whole one, so a half-typed "lio"
        # already finds the Lions.
        inside = [row for row in inside
                  if all(w in " ".join(_category_words(row["title"]))
                         for w in wanted)]
    if order == "az":
        inside.sort(key=lambda r: _catalog_sort_key(r["title"]))
    elif order == "recent":
        inside.sort(key=lambda r: -r["created"])
    else:
        inside.sort(key=lambda r: (-r["popularity"], -r["created"]))
    now = time.time()
    episodes = [_category_row(row, now) for row in inside]
    return {"id": node, "label": _category_label(node),
            "facet": topics_mod._root_facet(node), "sort": order,
            "q": q.strip(), "subcategories": subs, "episodes": episodes}


#: How many of today's most-played episodes the sign-up screen rotates
#: through (§181, the 9.30 interface packet).
WELCOME_SAMPLES = 3

#: How far down the most-played ranking the samples look, as a multiple of
#: `WELCOME_SAMPLES`: an episode is skipped without kept audio or its own
#: picture, so the top three by plays are often not the three shown.
WELCOME_SCAN = 10


#: How long one ranking of the samples serves every request (§190): the
#: waitlist gate asks for it on each sample's audio, and the samples turn
#: over by the day, not by the second.
WELCOME_MEMO_SECONDS = 60.0
_WELCOME_MEMO: dict = {"at": 0.0, "episodes": []}


def _welcome_episodes() -> list[dict]:
    """The samples, ranked: see `welcome_samples`. Memoised briefly."""
    now = time.monotonic()
    # Keyed on the stores too, so a swapped log (a wipe, a test) is never
    # answered from the old one.
    stores = (id(EVENTS), id(SCRIPT_CACHE))
    if (_WELCOME_MEMO["at"] and _WELCOME_MEMO.get("stores") == stores
            and now - _WELCOME_MEMO["at"] < WELCOME_MEMO_SECONDS):
        return _WELCOME_MEMO["episodes"]
    minutes = BROWSE_MINUTES
    try:
        ranked = topics_mod.rank_most_played(
            EVENTS, episode_info=_episode_info_probe(minutes),
            limit=WELCOME_SAMPLES * WELCOME_SCAN)
    except Exception:  # noqa: BLE001 - a sign-up page is never worth a 500
        log.exception("could not rank the welcome samples")
        ranked = []
    samples = []
    for topic in ranked:
        if not _audio_is_kept(topic.query, minutes):
            continue
        # The whole tile, as the rail draws it - title, hook and picture -
        # so the sign-up screen shows exactly what myFAM shows (10.1 packet).
        tile = topic.as_dict()
        # Only an episode with its own picture (the owner, 07/10): the
        # waitlist's front page is the product's shop window, and a line
        # drawing or a picture borrowed from its branch is not a good one.
        if not tile.get("thumb") or tile.get("thumb_borrowed"):
            continue
        samples.append({**tile, "minutes": minutes})
        if len(samples) >= WELCOME_SAMPLES:
            break
    _WELCOME_MEMO.update(at=now, episodes=samples, stores=stores)
    return samples


@app.get("/api/welcome")
async def welcome_samples(request: Request) -> dict:
    """The sign-up screen's samples: the top of "Most played episodes today".

    The same ranking as the rail (`topics.rank_most_played`, total listens in
    the last twenty-four hours), cut to `WELCOME_SAMPLES`, and only episodes
    whose **audio is kept**: the person reading this screen has no account
    yet, and a tap here must play at once and wake nothing - the guest rule
    (`_guest_play_gated`) applied before the tap rather than after it. Fewer
    than three is honest; none means the screen shows no samples at all.
    No model call, no voice, no listener id in the response. The waitlist's
    landing page shows the same three (§190), so this is open past the gate.
    """
    _read_limit(request)
    # A sample a reviewer took down is not offered on the front door (§226).
    return {"episodes": [e for e in _welcome_episodes()
                         if not _episode_removed("", e.get("query") or "",
                                                 e.get("minutes") or BROWSE_MINUTES)]}


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
    #: The episode's length and its id (`X-FAM-Episode`, §173), so the event
    #: is filed under the category of the episode that was actually heard
    #: (§209). Both optional: an older client sends neither, and its events
    #: are looked up at the browse length as they always were.
    minutes: int = Field(0, ge=0, le=10)
    episode: str = Field("", max_length=80)


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

    `minutes` is accepted and ignored: every myFAM episode is
    `BROWSE_MINUTES` long (§147), and only searchFAM offers a length. It
    matters because whether a tile's script is already written depends on the
    length it would be written at.
    """
    # A cheap read: ranking a fixed inventory costs no model call, so it takes
    # the reader's limit rather than the generation one.
    _read_limit(request)
    user = _listener(request)
    minutes = BROWSE_MINUTES

    # **A guest is shown the evergreen bank on every rail, and nothing on this
    # path may cost anything** (at the owner's direction). So none of what
    # follows runs for one: no story sweep, no vocabulary growth, no prefetch
    # cycle - the last of those spends a model call per warmed brief - and no
    # impressions, which a guest does not keep anyway. A tap on one of these
    # tiles plays kept audio or asks for an account; see `_guest_play_gated`.
    if not _has_account(request):
        SOCIAL.seen(user)
        feed = topics_mod.guest_feed()
        for section in feed["sections"]:
            _mark_guest_tiles(section["topics"], minutes)
        feed["minutes"] = minutes
        feed["algo"] = EVENTS.algo_stamp()
        return feed

    # One refresh serves every listener, so this is scheduled rather than
    # awaited: myFAM renders from whatever the shared pool holds and stays
    # instant. The browse surfaces are the one place CLAUDE.md says the wait
    # must be zero, and a news sweep is not worth spending it on - the rails
    # fall back to the bank on a cold first load and are full on the next.
    # The vocabulary keeps growing, on the same shape as the story sweep
    # beside it: scheduled, never awaited, at most once every two hours for
    # the whole deployment (§157). Without this the tree would be whatever it
    # was at boot, and a process that has been up for a week would be
    # ranking on a week-old vocabulary while the log filled with subjects it
    # cannot name.
    if categories_mod.is_stale():
        asyncio.create_task(_grow_categories())
    # Somebody is looking (§191): the sports sweep spends only on demand,
    # and the first look after a quiet spell starts a sweep now rather than
    # waiting for the next tick.
    woke = stories_mod.note_demand()
    if stories_mod.is_stale() or woke:
        # Through the same wrapper the boot sweep uses. A bare `create_task`
        # drops its exception into a log line nobody reads, and this one runs
        # on every page load - so a provider that raises would stop the pool
        # refreshing for the life of the process with the page still looking
        # normal.
        asyncio.create_task(_warm_stories())

    written = _written_probe(minutes)
    place = _place_for(request)
    circle = SOCIAL.circle_of(user)
    feed = topics_mod.build_feed(
        EVENTS, user, interests=_interests_for(request, interests),
        circle=circle, written=written,
        written_at=_written_at_probe(minutes),
        episode_info=_episode_info_probe(minutes),
        authored=_authored_by_circle(circle),
        place=place.words, place_name=place.label,
        has_account=_has_account(request),
        country=_country_for(request, place))
    # Every tile says whether it would replay or generate, the same way the
    # "view more" screen already did. A listener browsing is choosing between
    # things to hear, and "this one starts instantly" is a real difference
    # between two of them - and it is free to say, because the ranking asked
    # the same question a moment ago and this is the memoised answer.
    for section in feed["sections"]:
        section["topics"] = _drop_removed(user, section["topics"], minutes)
        for topic in section["topics"]:
            topic["cached"] = written(topic.get("query", ""))
        _name_written_tiles(section["topics"], minutes)
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
    tags = _event_tags(req.topic_id, req.text, req.minutes or BROWSE_MINUTES,
                       req.episode)
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
    #: What the person vibing it says about it (10.5 packet #9), shown with
    #: the episode when it plays as a story. Optional; cut to
    #: `social.MAX_CAPTION` rather than refused, since a client may not know
    #: the limit.
    caption: str = Field("", max_length=1000)
    #: The story's layout (10.6 packet #2): picture frame and size, the
    #: caption's face, size and place, stickers. Clamped in
    #: `social.clean_style`; omitted for a plain vibe.
    #: Omitted (None) by an installed client that predates the editor, which
    #: keeps the row's layout, tags and audience as they are.
    style: Optional[dict] = None
    #: People tagged with @, by handle, and where each tag sits.
    tags: Optional[list[dict]] = Field(None, max_length=social_mod.MAX_TAGS)
    #: "" for everybody who follows, "close" for close friends only.
    audience: Optional[str] = Field(None, max_length=10)


async def _check_photo(request: Request, user: str, data_url: Optional[str],
                       before: str = "") -> None:
    """Refuse a picture strangers would see if the photo check says no
    (image_check.py, §224). Nothing to check when there is no picture or it
    is the one already kept; a check that cannot run lets it through."""
    if not data_url or data_url == before:
        return
    if image_check.split_data_url(data_url) is None:
        raise HTTPException(status_code=400, detail=image_check.UNREADABLE)
    # A paid model call: paced like one (`limit-episodes`) - and only when
    # one will actually be made.
    if image_check.will_call():
        _rate_limit(request)
    usage = metering.Usage()
    verdict = await image_check.check(data_url, usage=usage)
    # Whatever the verdict, a call that was made was paid for (`metering`).
    if usage.model_calls and user:
        _record_usage(user, usage, surface="photo_check")
    if not verdict.allowed:
        raise HTTPException(status_code=400, detail=image_check.REFUSED)


@app.post("/api/me")
async def set_me(req: PersonRequest, request: Request):
    """Name, handle and picture for this device. Not an account - see
    /api/profile.

    `avatar` is omitted to leave the current one alone and sent as "" to take
    it off, which are different requests: a client that simply never sends the
    field must not silently delete a picture somebody chose.
    """
    _read_limit(request)
    user = _listener(request)
    # Suspended: nothing new that strangers read, a name or a face included.
    _require_can_post(user)
    # The cheap checks first, so a bad handle never pays for a photo check.
    try:
        handle = social_mod.clean_handle(req.handle)
        if SOCIAL.user_by_handle(handle) not in ("", user):
            raise social_mod.SocialError(f"@{handle} is taken.")
        if not " ".join(str(req.name or "").split()):
            raise social_mod.SocialError("Give yourself a name.")
        if req.avatar:
            social_mod.clean_avatar(req.avatar)
    except social_mod.SocialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _check_photo(request, user, req.avatar,
                       before=SOCIAL.person(user).get("avatar") or "")
    try:
        return SOCIAL.set_person(user, req.name, req.handle,
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
    _require_can_post(user)
    audience = None
    if req.audience is not None:
        audience = req.audience if req.audience in social_mod.AUDIENCES else ""
    if audience == "close" and not _has_account(request):
        raise HTTPException(status_code=401, detail="Close friends need an account.")
    tags = (_resolve_tags(user, req.tags, audience or "")
            if req.tags is not None else None)
    # Only somebody newly tagged hears about it: posting the same story
    # again is not a second invitation.
    told = SOCIAL.tagged_in(user, req.query, req.minutes) if tags else set()
    try:
        echo = SOCIAL.echo(user, req.query, req.title, req.minutes, req.thread,
                           caption=req.caption, style=req.style, tags=tags,
                           audience=audience)
    except social_mod.SocialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Somebody tagged hears about it the way a share arrives: one message,
    # the episode in it (10.6 packet #2). Only accounts message.
    fresh = [t for t in (tags or []) if t["user_id"] not in told]
    if fresh and _has_account(request):
        _tell_tagged(user, fresh, req)
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


def _resolve_tags(user: str, tags: list[dict], audience: str) -> list[dict]:
    """Handles typed after @, as people this listener may tag.

    Only somebody in their graph - followed or following - since a tag sends
    them a message; and on a close-friends story only a close friend, or the
    tag would tell somebody about a story they cannot see.
    """
    if not user or not tags:
        return []
    graph = {p["handle"]: p["user_id"]
             for p in SOCIAL.following(user) + SOCIAL.followers(user)
             if p.get("handle")}
    close = set(SOCIAL.close_friends(user)) if audience == "close" else None
    # Nobody still on the waitlist: a tag draws a handle on other people's
    # screens, and waitlisted accounts are kept out of discovery.
    waiting = (WAITLIST.waitlisted_among(graph.values())
               if settings.waitlist else set())
    out = []
    for tag in tags[:social_mod.MAX_TAGS]:
        handle = str((tag or {}).get("handle") or "").strip().lstrip("@").lower()
        uid = graph.get(handle)
        if not uid or uid in waiting or (close is not None and uid not in close):
            continue
        out.append({"user_id": uid, "x": tag.get("x"), "y": tag.get("y")})
    return out


def _tell_tagged(user: str, tags: list[dict], req: "EchoRequest") -> None:
    waiting = (WAITLIST.waitlisted_among([user] + [t["user_id"] for t in tags])
               if settings.waitlist else set())
    if user in waiting:
        return
    for tag in tags:
        if tag["user_id"] in waiting:
            continue
        try:
            MESSAGES.send(user, tag["user_id"], kind="episode",
                          text="Tagged you in their VIBE!", query=req.query,
                          minutes=req.minutes, title=req.title)
        except messages_mod.MessageError:
            log.exception("could not tell somebody they were tagged")


@app.delete("/api/vibe/story/{echo_id}")
async def remove_from_story(echo_id: int, request: Request) -> dict:
    """Take one of your own vibes off your story (10.6 packet #1).

    Only the poster's: matched on the id *and* this listener. The vibe stays
    on their profile - it is the story that comes down.
    """
    _read_limit(request)
    return {"ok": SOCIAL.unstory(_listener(request), echo_id)}


class CloseFriendRequest(BaseModel):
    user_id: str = Field(..., max_length=64)
    on: bool = True


@app.get("/api/close-friends")
async def close_friends(request: Request) -> dict:
    """The people a "Close Friends" story can go to, and who is on the list.

    Everybody in this listener's graph, close friends first. Settings draws
    it; the list is theirs and is never shown to anybody on it.
    """
    _read_limit(request)
    user = _require_account(request)
    chosen = SOCIAL.close_friends(user)
    people, seen = [], set()
    for person in SOCIAL.friends(user) + SOCIAL.following(user) + SOCIAL.followers(user):
        uid = person.get("user_id") or ""
        if uid and uid not in seen:
            seen.add(uid)
            people.append({"user_id": uid, "name": person.get("name") or "",
                           "handle": person.get("handle") or "",
                           "avatar": person.get("avatar") or "",
                           "close": uid in chosen})
    people.sort(key=lambda p: (not p["close"], (p["name"] or p["handle"]).lower()))
    return {"people": people, "count": len(chosen)}


@app.post("/api/close-friends")
async def set_close_friend(req: CloseFriendRequest, request: Request) -> dict:
    _read_limit(request)
    user = _require_account(request)
    graph = {p["user_id"] for p in SOCIAL.following(user) + SOCIAL.followers(user)}
    if req.on and req.user_id not in graph:
        raise HTTPException(status_code=404, detail="Pick somebody you follow.")
    try:
        SOCIAL.set_close_friend(user, req.user_id, req.on)
    except social_mod.SocialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "close": req.on, "count": len(SOCIAL.close_friends(user))}


@app.delete("/api/echo")
async def delete_echo(request: Request, q: str = Query("", max_length=300),
                      minutes: int = Query(DEFAULT_MINUTES, ge=1, le=10)):
    _read_limit(request)
    user = _listener(request)
    ok = SOCIAL.unecho(user, q, minutes)
    # A vibe taken back leaves its folder too, or the folder counts a ghost.
    if ok:
        try:
            SAVED.file_vibe(user, q, minutes, "")
        except Exception:  # noqa: BLE001 - the vibe is gone either way
            log.exception("could not unfile a vibe taken back")
    return {"ok": ok}


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


# --- comments (10.5 packet #8) ---------------------------------------------
#
# Explore's comments sheet. A comment is about an episode - `(query,
# minutes)`, the same pair a vibe and a thumb are keyed on - so everybody who
# hears that episode reads the one thread. Reading is open to anyone who can
# hear the episode; writing is kept, so it takes an account
# (`account-gates-kept`), and the 401 is what opens the sign-up screen.
class CommentRequest(BaseModel):
    query: str = Field(..., max_length=300)
    minutes: int = Field(DEFAULT_MINUTES, ge=1, le=10)
    #: Cut to `social.MAX_COMMENT` rather than refused.
    text: str = Field(..., max_length=2000)
    #: The comment this answers, or 0 for a new one.
    parent_id: int = Field(0, ge=0)


class CommentLikeRequest(BaseModel):
    on: bool = True


@app.get("/api/comments")
async def episode_comments(request: Request, q: str = Query("", max_length=300),
                           minutes: int = Query(DEFAULT_MINUTES, ge=1, le=10)) -> dict:
    """An episode's comments, most liked first, each with its replies."""
    _read_limit(request)
    viewer = _listener(request)
    # Nobody blocked either way or suspended, and nothing this listener
    # reported (`moderation.py`); a hidden comment takes its replies with it.
    hidden = {int(c) for c in MODERATION.reported_by(viewer, "comment") if c.isdigit()}
    rows = SOCIAL.comments(q, minutes, viewer=viewer,
                           exclude_users=MODERATION.apart(viewer),
                           exclude_ids=hidden) if q.strip() else []
    return {"comments": rows,
            "count": sum(1 + len(c.get("replies") or []) for c in rows)}


@app.post("/api/comments")
async def post_comment(req: CommentRequest, request: Request) -> dict:
    _read_limit(request)
    user = _require_account(request)
    _require_can_post(user)
    try:
        return SOCIAL.add_comment(user, req.query, req.minutes, req.text,
                                  parent_id=req.parent_id)
    except social_mod.SocialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/comments/{comment_id}/like")
async def like_comment(comment_id: int, req: CommentLikeRequest,
                       request: Request) -> dict:
    _read_limit(request)
    user = _require_account(request)
    try:
        return SOCIAL.like_comment(user, comment_id, on=req.on)
    except social_mod.SocialError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/comments/{comment_id}")
async def delete_comment(comment_id: int, request: Request) -> dict:
    _read_limit(request)
    return {"ok": SOCIAL.delete_comment(_listener(request), comment_id)}


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
    # Their own folders, and which one each vibe is filed in (after §161).
    # A guest has neither: folders are kept on an account.
    filed = SAVED.vibe_files(user) if _has_account(request) else {}
    return {"vibes": [dict(v.as_dict(person["name"], person["handle"]),
                           folder_id=filed.get((v.query, int(v.minutes)), ""))
                      for v in vibes],
            "folders": SAVED.folders(user, "vibe") if _has_account(request) else [],
            "count": len(SOCIAL.echoes_by(user, limit=200))}


class VibeFileRequest(BaseModel):
    query: str = Field(..., max_length=300)
    minutes: int = Field(DEFAULT_MINUTES, ge=0, le=60)
    #: A vibe folder's id, or "" to take the vibe out of any folder.
    folder_id: str = Field("", max_length=64)


@app.post("/api/vibes/file")
async def file_vibe(req: VibeFileRequest, request: Request) -> dict:
    """Put one of this listener's vibes in one of their vibe folders."""
    _read_limit(request)
    user = _require_account(request)
    try:
        folder = SAVED.file_vibe(user, req.query, req.minutes, req.folder_id)
    except saved_mod.SavedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "folder_id": folder}


@app.get("/api/profile")
async def profile(request: Request):
    """Counts and subjects from this listener's own event log. No model call."""
    _read_limit(request)
    user = _listener(request)
    body = topics_mod.summary(EVENTS, user, interests=_interests_for(request))
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
    body["mixes"] = [m.public_dict() for m in MIXES.public_for_user(user)]
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
    # Shown under their name (10.7 packet): the place as they wrote it.
    body["location"] = prefs.location.label
    body["circle"] = _circle_row(user)
    # Their own story (10.6 packet #1): their vibes of the last 24 hours, the
    # ones their friends' faces play, so their own picture can carry the
    # ring and play them too.
    body["stories"] = SOCIAL.stories_among(
        [user], time.time() - CIRCLE_VIBE_WINDOW, viewer=user).get(user, [])
    return body


#: How long a vibe is a story. A friend's avatar on YourFAM carries the VIBE
#: badge, and tapping it plays their vibes like stories, for 24 hours after
#: each one and not a minute longer (the 27/09 packet, at the owner's
#: direction - it was a week, and a badge that outlives its story is a badge
#: that opens nothing).
CIRCLE_VIBE_WINDOW = 24 * 3600
#: The gold ring - "something new from this person" - is the same day: a vibe
#: still up as a story, or a message you have not read yet.
CIRCLE_FRESH_WINDOW = CIRCLE_VIBE_WINDOW
CIRCLE_MAX = 12
#: How many friends and follows are looked at for vibes before the row is cut
#: to `CIRCLE_MAX`, so a vibe sorts to the front from anywhere in the list.
CIRCLE_CANDIDATES = 200


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
    for person in _without_apart(user, SOCIAL.friends(user) + SOCIAL.following(user)):
        uid = person.get("user_id") or ""
        if uid and uid not in seen:
            seen.add(uid)
            people.append(person)
    people = people[:CIRCLE_CANDIDATES]
    if not people:
        return []
    now = time.time()
    stories = SOCIAL.stories_among([p["user_id"] for p in people],
                                   now - CIRCLE_VIBE_WINDOW, viewer=user)
    # Friends with a vibe up come first, left to right, newest vibe first
    # (the 10.1 packet, third set), so who has vibed is seen at a glance and
    # the stories run on from one to the next in the order drawn. Everybody
    # else keeps friends-then-follows. Sorted before the cap, so a vibe from
    # the thirteenth person followed is not cut off the row.
    def _newest(uid: str) -> float:
        row = stories.get(uid) or []
        return max((float(v.get("at") or 0) for v in row), default=0.0)
    people.sort(key=lambda p: (0, -_newest(p["user_id"])) if stories.get(p["user_id"])
                else (1, 0.0))
    people = people[:CIRCLE_MAX]
    latest = SOCIAL.latest_echo_at([p["user_id"] for p in people], viewer=user)
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
            # Their vibes of the last 24 hours, oldest first: what tapping
            # the face plays, story by story.
            "stories": stories.get(uid, []),
        })
    return out


@app.get("/api/circle")
async def circle(request: Request) -> dict:
    """The YourFAM avatar row on its own, for the top of the rails screen.

    The 10.2 packet #1: the faces on YourFAM sit at the top of the screen
    that shows "DailyFAM" (code: myFAM, §185), so what friends have vibed is
    seen first thing and scrolled sideways. The same rows as `/api/profile`'s
    `circle` (`_circle_row`), asked separately so the rails never wait on it.
    A guest has no follow graph and gets an empty row - never strangers.
    """
    _read_limit(request)
    if not _has_account(request):
        return {"circle": []}
    return {"circle": _circle_row(_listener(request))}


class ProgressRequest(BaseModel):
    query: str = Field(..., max_length=saved_mod.MAX_QUERY)
    minutes: int = Field(..., ge=1, le=10)
    seconds: float = Field(..., ge=0, le=3600)
    title: str = Field("", max_length=saved_mod.MAX_TITLE)
    #: The topic a follow-up was asked from. Part of the episode's cache key,
    #: so without it a resumed follow-up would be a different episode.
    context: str = Field("", max_length=300)
    #: The episode's real length in seconds, once the player holds all of it;
    #: 0 when it does not know yet. Optional, so older clients still send.
    duration: float = Field(0, ge=0, le=3600)


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
                               req.seconds, title=req.title, context=req.context,
                               duration=req.duration)
    return {"ok": True, "remembered": True, "resumable": kept}


class HistoryRequest(BaseModel):
    query: str = Field(..., max_length=saved_mod.MAX_QUERY)
    minutes: int = Field(..., ge=1, le=10)
    surface: str = Field(..., max_length=16)
    title: str = Field("", max_length=saved_mod.MAX_TITLE)
    context: str = Field("", max_length=300)
    #: True when this only carries the writer's title for an episode already
    #: in the history, so it must not move the row to the top.
    retitle: bool = False
    #: Which episode was heard - the `X-FAM-Episode` its audio arrived with
    #: (§173). '' from an older client, whose row replays by its question.
    episode: str = Field("", max_length=80)


def _kept_under_question(item: dict) -> str:
    """A bare-key episode id for a history row from before §173, when its
    question's key still holds a kept episode - so the row replays that
    rather than writing a new one. '' when nothing is kept (the row then
    plays as it always did)."""
    if SCRIPT_CACHE is None:
        return ""
    try:
        key = _episode_key(_validated_plan(item["query"], int(item["minutes"]),
                                           item.get("context") or ""))
        # Pinned as a new row would be, so it stays kept while it is shown.
        return _pin_heard(key) if key else ""
    except Exception:  # noqa: BLE001 - a hint on an old row, never a failure
        return ""


def _pin_heard(episode: str) -> str:
    """Keep a heard episode for as long as the listening history shows it
    (§173). Returns the id when it names a kept episode, else ''.

    The cache keeps a row a week and history is two weeks, so without this
    the second week of history could only be written again - a different
    episode under the same title.

    **A well-formed id is kept even before its row exists** (§245). History
    is written at first audio and a written episode's row only at the end of
    its stream, so the id would otherwise be dropped - and for an episode
    kept to its listener alone (`ScriptNotes.limited`) the shared question
    is exactly what must not stand in for it. The end of the stream pins it
    (`/api/audio`); a row that never arrives reads as no id (`history_read`)."""
    if not episode or SCRIPT_CACHE is None or parse_episode_id(episode) is None:
        return ""
    try:
        key = SCRIPT_CACHE.resolve_episode(episode)
        if key:
            SCRIPT_CACHE.keep_until(key, time.time() + saved_mod.HISTORY_SECONDS)
        return episode
    except Exception:
        log.exception("could not keep a heard episode; continuing")
        return ""


@app.post("/api/history")
async def history_write(req: HistoryRequest, request: Request) -> dict:
    """An episode started playing; put it in Recent listening history (§142).

    Sent by the client at first audio, because only the client knows which
    surface the tap came from - `/api/audio` sees myFAM and DailyFAM as the
    same request. Explore is refused by the store, not trusted to the client.
    A guest's is accepted and dropped, like `/api/progress`: history is kept
    on an account, and a playback timer must never surface an error.
    """
    _read_limit(request)
    if not _remembers(request):
        return {"ok": True, "remembered": False}
    user = _listener(request)
    if req.retitle:
        SAVED.retitle(user, req.query, req.minutes, req.title, context=req.context)
        return {"ok": True, "remembered": True}
    kept = SAVED.note_listen(user, req.query, req.minutes, req.surface,
                             title=req.title, context=req.context,
                             episode=_pin_heard(req.episode))
    return {"ok": True, "remembered": kept}


@app.get("/api/history")
async def history_read(request: Request,
                       surface: str = Query("", max_length=16)) -> dict:
    """Two weeks of listening, newest first, optionally one surface only."""
    _read_limit(request)
    user = _require_account(request)
    items = SAVED.history(user, surface=surface)
    for item in items:
        # An id whose episode was never stored (the listener left before the
        # end, §245) is no id: the kept answer to the question stands in.
        if item.get("episode") and SCRIPT_CACHE is not None \
                and not SCRIPT_CACHE.resolve_episode(item["episode"]):
            item["episode"] = ""
        if not item.get("episode"):
            item["episode"] = _kept_under_question(item)
    return {"items": items,
            "surfaces": list(saved_mod.HISTORY_SURFACES),
            "days": saved_mod.HISTORY_SECONDS // 86400}


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


#: How far back "Pick up where you left off" looks. Both of its sources -
#: episodes started and not finished, and the follow-up the player's Go Deeper
#: button would offer on an episode heard - must come from listening inside
#: this window, at the owner's direction. A day since the 9.29 packet (it was
#: a week): a tile is on the section for at most 24 hours after it was last
#: listened to, and the next one that qualifies takes its place.
GO_DEEPER_WINDOW_SECONDS = 24 * 3600
#: How many of each source are read. The section shows four, and the rest wait
#: behind them so a tile closed with its X is replaced - but only ever by
#: another tile that qualifies.
GO_DEEPER_DEPTH = 8


@app.get("/api/godeeper")
async def go_deeper(request: Request, interests: str = Query("", max_length=200)):
    """"Pick up where you left off": two things, and nothing else.

    1. **Episodes started in the last day and under 60% heard** (`resume`).
    2. **The Go Deeper prompt of an episode finished in the last day**
       (`threads`) - exactly the follow-up the player's Go Deeper button
       would have offered on it, so the tile is that episode.

    It used to top itself up with "similar" episodes from the feed's next-up
    ranking, seeded with the last thing heard. Those were adjacent rather than
    deeper - a different subject in the same facet - so they are gone, at the
    owner's direction. **Fewer than four is the honest answer** when fewer
    than four qualify, and a tile closed with its X is replaced only by
    another that qualifies. `similar` stays in the response, always empty, so
    a client that still reads it draws nothing rather than failing.

    **Only for an account**, on the same rule as the event log: what somebody
    was halfway through is part of what FAM remembers about them, and a guest
    session is a device rather than a person.

    Costs nothing: every line here is read, never written. `interests` is
    accepted and unused, for clients that still send it.
    """
    _read_limit(request)
    user = _listener(request)
    if not user or not _remembers(request):
        return {"threads": [], "resume": [], "similar": []}

    since = time.time() - GO_DEEPER_WINDOW_SECONDS
    # Tiles closed with their X are never offered again, so a few more of
    # each are read and the closed ones filtered out - the next one along
    # takes the place of one that was dismissed.
    hidden = SAVED.dismissed(user)
    resume = [r for r in SAVED.progress(user, limit=GO_DEEPER_DEPTH + len(hidden),
                                        since=since)
              if not SAVED.is_dismissed(hidden, r["query"])][:GO_DEEPER_DEPTH]
    try:
        pipeline = _make_pipeline() if resume else None
    except TTSUnavailable:
        pipeline = None
    for row in resume:
        title, summary = await _episode_blurb(pipeline, row["query"], row["minutes"],
                                              row.get("context", ""))
        row["title"] = title or row.get("title") or ""
        row["summary"] = summary

    resuming = {" ".join(r["query"].lower().split()) for r in resume}
    threads = [t for t in EVENTS.open_threads(
                   user, limit=GO_DEEPER_DEPTH + len(hidden), since=since)
               if not SAVED.is_dismissed(hidden, t.get("thread", ""))
               # Already one of the part-heard tiles: offered once, as that.
               and " ".join(t.get("thread", "").lower().split()) not in resuming
               ][:GO_DEEPER_DEPTH]
    for row in threads:
        # The follow-up itself has usually not been made, so there is no
        # summary of *it* to read - the line says where it comes from instead,
        # which is the one true thing known about it.
        row["summary"] = (f"Follows on from {row['from_title']}."
                          if row.get("from_title") else "")
    return {"threads": threads, "resume": resume, "similar": []}


class DismissRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=300)


@app.post("/api/godeeper/dismiss")
async def dismiss_go_deeper(req: DismissRequest, request: Request):
    """The X on a "Pick up where you left off" tile: never offer that
    question there again. Account only, like the section itself."""
    _read_limit(request)
    SAVED.dismiss(_require_account(request), req.query)
    return {"ok": True}


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


def _capitalised(text: str) -> str:
    return text[:1].upper() + text[1:]


#: How many trending searches the search page offers (10.1 #3): five to
#: ten, the owner's range.
TRENDING_SEARCHES_MAX = 8
#: The window they are counted over (§190, the owner's): the most searched
#: questions of the last two hours, in order.
TRENDING_SEARCHES_WINDOW = 2 * 3600
#: How far back the cache is read to find them.
TRENDING_SEARCHES_SCAN = 200


@app.get("/api/searches/trending")
async def trending_searches(request: Request) -> dict:
    """The most searched questions of the last two hours, in order (§190).

    Drawn under the search box, so a tap is a search that lands on an
    episode already written. **Ranked by how many listeners searched it in
    `TRENDING_SEARCHES_WINDOW`** (the owner's, §190; it was most played,
    §182) - distinct listeners, so one person searching the same thing ten
    times is one search - with plays breaking ties. **Only what is current**
    (`ttl_for`): a search whose episode a new request would not be served is
    never offered, which is what keeps a score or a game update off this list
    - an episode built on a sports game that is not over is never current
    (`cache.ttl_for`'s sports rule). Searched episodes only, like Explore; no
    attachment is ever cached, a swearing episode is left off, and an empty
    list is a fact about the deployment, never filled with anything else.

    Reads the cache and the event log and nothing else: it costs nothing and
    generates nothing.
    """
    _read_limit(request)
    store = SCRIPT_CACHE if SCRIPT_CACHE is not None else build_cache()
    if store is None:
        return {"searches": []}
    searchers: dict[str, set] = defaultdict(set)
    for user, text in EVENTS.searches_since(time.time() - TRENDING_SEARCHES_WINDOW):
        norm = normalize_query(text or "")
        if norm:
            searchers[norm].add(user)
    if not searchers:
        return {"searches": []}
    entries = _visible_episodes(_listener(request),
                                store.recent(TRENDING_SEARCHES_SCAN, origin="search"))
    seen: set[str] = set()
    picks = []
    ranked = sorted(entries, key=lambda e: (
        -len(searchers.get(normalize_query(e.get("query") or ""), ())),
        -int(e.get("plays") or 0)))
    for entry in ranked:
        query = (entry.get("query") or "").strip()
        norm = normalize_query(query)
        if not query or norm not in searchers:
            continue
        if not entry.get("current") or entry.get("explicit"):
            continue
        if content_filter.scrub(query) != query:
            continue
        # Only what a bare search for these words would land on: a Go
        # Deeper follow-up is stored under its parent's context, so its words
        # asked cold are a different key - a miss that writes a new episode,
        # the opposite of what a trending search is for. With the semantic
        # key on (`CACHE_SEMANTIC_KEY`, off by default) the key needs a model
        # call to compute, and the row is offered on trust.
        minutes = int(entry.get("minutes") or 0)
        if not settings.cache_semantic_key and entry.get("key") not in (
                cache_key(query, minutes, None, "", True),
                cache_key(query, minutes, None, "", False)):
            continue
        if norm in seen:
            continue
        seen.add(norm)
        picks.append({
            "query": query,
            # The episode's own title, spelled by the writer - never the
            # question, which may be misspelled (10.1 #4).
            "title": entry.get("title") or "",
            "minutes": minutes,
            "searches": len(searchers[norm]),
        })
        if len(picks) >= TRENDING_SEARCHES_MAX:
            break
    # The question as the speller would have sent it, for a chip with no
    # title to show (10.1 #4); `query` is still what is asked. Off the event
    # loop, like `/api/spell`, and only for the untitled.
    spelled = await asyncio.to_thread(
        lambda: [autocorrect_mod.correct_text(p["query"]) if not p["title"]
                 else p["query"] for p in picks])
    for pick, text in zip(picks, spelled):
        pick["spelled"] = text
    return {"searches": picks}


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

    **And searched episodes only** (§147). Every surface writes into the one
    shared cache, and Explore is what other listeners *searched* - never a
    myFAM tile, a DailyFAM edition, a Trending episode or a prefetch guess.
    `scripts.origin` says which surface wrote an entry; one written before
    that column existed says nothing and is left off.
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
    vibes = SOCIAL.echoes_among(list(friends), viewer=listener) if friends else {}

    # And who else vibed what. Still read, and still only for the *order*: a
    # vibe is somebody choosing to send an episode, which is a real reason for
    # a card to lead. It no longer puts a stranger's name on one - naming
    # people the listener has never heard of under a heading about their
    # friends is the mistake §102 took off myFAM.
    anyone = SOCIAL.recent_echoes(exclude_user=listener)

    episodes = []
    # Searched episodes only (§147, at the owner's direction): a myFAM tile,
    # a DailyFAM edition, a Trending episode or a warmed guess is cached too,
    # and none of them is on Explore.
    entries = store.recent(limit, exclude_author=listener, origin="search")
    entries = _visible_episodes(listener, entries)
    # An untitled card is titled from its question, through the speller so a
    # misspelling never becomes a title (10.1 #4) - in a thread, like
    # `/api/spell`, and only for the cards that need it.
    untitled = [e["query"] for e in entries if not e.get("title")]
    spelled = dict(zip(untitled, await asyncio.to_thread(
        lambda: [autocorrect_mod.correct_text(q) for q in untitled])))
    all_counts = SOCIAL.episode_counts_many(
        [(e["query"], e["minutes"]) for e in entries], listener)
    # The number under the comment button (10.5 packet #8).
    comment_counts = SOCIAL.comment_counts_many(
        [(e["query"], e["minutes"]) for e in entries])
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
            # Through the speller, so a misspelled question is never a
            # card's title (10.1 #4).
            "title": entry.get("title")
                     or _capitalised(spelled.get(entry["query"], entry["query"])),
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
            # When the information in it was sourced (§143), which is what
            # the card says: an episode is kept a week, and a listener
            # judging whether it is still true needs its age, not its row's.
            "sourced_age_seconds": max(
                0.0, now - (entry.get("sourced_at") or entry["created"])),
            "current": bool(entry.get("current", True)),
            # It swears (§171): the card draws the E beside its title.
            "explicit": bool(entry.get("explicit")),
            "vibed": bool(pair in anyone or by),
        }
        # The counts on the card's buttons: vibes, likes, dislikes, and this
        # listener's own thumb and vibe so the buttons open in the right state.
        counts = all_counts[(entry["query"], entry["minutes"])]
        card.update({"vibes": counts["vibes"], "likes": counts["likes"],
                     "dislikes": counts["dislikes"], "rating": counts["rating"],
                     "my_vibe": counts["vibed"],
                     "comments": comment_counts[(entry["query"], entry["minutes"])]})
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
        apart = MODERATION.apart(listener)
        circle = [u for u in circle if u not in apart]
        vibes = SOCIAL.echoes_among(circle, viewer=listener) if circle else {}
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
                   "Follow friends to see what they vibe on this."))
    else:
        for tile in topics_mod.live_topics(now):
            if (tags & set(tile.tags)) or _on_interest(tile.query, set(), words):
                add(tile.query, tile.title, 0, "story", 0.0, angle=tile.subtitle)
        store = SCRIPT_CACHE if SCRIPT_CACHE is not None else build_cache()
        if store is not None:
            for entry in _visible_episodes(listener, store.recent(120)):
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


@app.get("/api/progress")
async def progress(
    request: Request,
    q: str = Query(..., description="What the listener asked"),
    minutes: int = Query(DEFAULT_MINUTES, ge=1, le=10),
    context: str = Query("", description="Topic the listener just heard"),
    # Same reason as /api/audio: the answer is keyed on the episode, and the
    # key has to be built the way the audio request built it.
    search: bool | None = Query(None),
    # And for the same reason, the two that decide a browse tap's length
    # (§147): the audio request holds myFAM and DailyFAM to BROWSE_MINUTES,
    # so this has to as well or it asks about a different key.
    surface: str = Query("", max_length=16),
    topic_id: str = Query("", max_length=64),
):
    """Which of the loading screen's steps the episode being made has finished.

    The audio response cannot carry this: its status line is only sent once
    the first audio exists, which is after every step. So the loading screen
    asks here while it waits, the same side channel `/api/transcript` and
    `/api/next` already use, keyed the same way.

    `steps` is four booleans in waiting order - the brief, the retrieval, the
    writer's planning, the first sentence written - each set from the mark
    the pipeline records when that step really finishes, never from a
    timer. `cached` means the episode is a replay with nothing to write.
    `known` False means this worker is not making it; the interface then
    walks the list when the audio arrives, since audio means all of it
    happened.

    **It never generates** - it reads the live track and nothing else.
    """
    _read_limit(request)
    if _play_surface(surface, False, topic_id, context) in BROWSE_SURFACES:
        minutes = BROWSE_MINUTES
    plan = _validated_plan(q, minutes, context, search)
    try:
        pipeline = _make_pipeline()
    except TTSUnavailable:
        return live_captions.read_progress("")
    return await pipeline.progress_for(plan)


@app.get("/api/next")
async def next_thread(
    request: Request,
    q: str = Query(..., description="What the listener asked"),
    minutes: int = Query(DEFAULT_MINUTES, ge=1, le=10),
    context: str = Query("", description="Topic the listener just heard"),
    # Same reason as /api/audio: this looks up a cache entry, and the entry it
    # looks for has to be keyed the same way the audio request keyed it.
    search: bool | None = Query(None),
    # A replay (Explore) plays whatever is kept, so it may read a kept row's
    # details; anything else plays a current one, and past its window the
    # kept row is the previous episode under this key (§143).
    cached_only: bool = Query(False),
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
        return {"thread": "", "title": "", "title_final": False, "summary": "",
                "explicit": False}
    # `title_final` is what lets the player ask early: the brief's title is on
    # the live track before the first word (§127), and the interface keeps
    # asking until the writer's own has replaced it.
    return await pipeline.episode_meta(plan, current_only=not cached_only)


@app.get("/api/audio")
async def audio(
    request: Request,
    q: str = Query(..., description="What the listener asked"),
    minutes: int = Query(DEFAULT_MINUTES, ge=1, le=10),
    fmt: str = Query("wav", pattern="^(wav|pcm|opus)$"),
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
    surface: str = Query("", max_length=16,
                         description="Where the tap came from: search, myfam, "
                                     "dailyfam, explore, share or other"),
    episode: str = Query("", max_length=80,
                         description="A heard episode's id (X-FAM-Episode) to "
                                     "replay exactly; never generates (§173)"),
    own: bool = Query(False, description="The question is the listener's own "
                                         "words (a typed Go Deeper), held to "
                                         "their AI answer like a search"),
):
    """Stream the episode.

    `fmt=wav` prefixes a live-stream WAV header so a plain <audio> tag works.
    `fmt=pcm` sends bare samples for the Web Audio player, which schedules
    chunks itself and therefore starts sooner and seeks better.
    `fmt=opus` (§242) sends the same audio as "fam-opus v1" - Opus packets,
    length-prefixed, then an end marker with the true length
    (`audio_codec`) - when this server can; otherwise PCM, without the
    `X-FAM-Audio-Format: opus` header, which is how the player knows.
    """
    # Every request answers to the cheap ceiling. The pace on top of it is for
    # requests that can actually spend a model call.
    _read_limit(request)
    user = _listener(request)
    where = _play_surface(surface, cached_only, topic_id, context)
    # §147: only searchFAM offers a length. Every myFAM and DailyFAM episode
    # is two minutes, whatever an older client sends, because minutes are in
    # the cache key and the background editions are written at two.
    if where in BROWSE_SURFACES:
        minutes = BROWSE_MINUTES
    minutes = min(minutes, entitlements.max_minutes(_tier(request),
                                                    settings.max_minutes))
    plan = _validated_plan(q, minutes, context, search, cached_only,
                           _attachments_for(user, attach))
    # A reviewer took this episode down (moderation.py, §226): it plays for
    # nobody - not from a share, a rail, a replay, or written again.
    if _episode_target(q, minutes) in MODERATION.hidden_episodes():
        raise HTTPException(status_code=410, detail="This episode was removed.")
    if episode:
        # The listening history replaying what was heard (§173): that
        # episode, current or not, or a 409 - never a new one.
        if parse_episode_id(episode) is None or attach:
            raise HTTPException(status_code=400, detail="Unknown episode.")
        plan = dataclasses.replace(plan, episode=episode)

    # A replay-only request - Explore, and any card played from it - provably
    # cannot spend a model call, so pacing it only stops someone swiping a feed
    # at a normal speed, which is exactly what the feed is for. Neither can a
    # request whose script is already written: the pipeline replays the stored
    # sentences. That second case is the ordinary one the old code got wrong -
    # tapping the episode you are listening to, or switching voice, which
    # reuses the script *by design* (PROBLEMS.md 70).
    if not (cached_only or episode or _already_written(plan)):
        # Before anything is sent anywhere: a listener's own words reach the
        # AI provider only with their yes (5.1.2(i), `consent.py`). A replay
        # or a written script sends nothing, so it never asks.
        if _sends_listener_words(where, context, attach, topic_id, own):
            _require_ai_consent(request, user)
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
        pipeline = _make_pipeline(_episode_voice(user, where, plan, voice),
                                  author=user)
    except TTSUnavailable as exc:
        # The server cannot speak at all. Nothing was generated and nothing was
        # billed, so the allowance goes back - this is the machine being
        # broken, not the listener spending.
        _refund(reserved, user)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    # Stamped on anything this writes to the shared cache, so Explore can be
    # searched episodes and nothing else (§147).
    pipeline.origin = where

    # **A guest's tap on the sample pages costs nothing** (at the owner's
    # direction). myFAM and the DailyFAM example playlist are the evergreen
    # bank for a guest, and one of those tiles plays only when its script is
    # current and its audio is already kept - which is read out of SQLite and
    # never reaches Claude or RunPod. Anything else is refused *here*, before
    # the GPU is woken and before a word is written, with a reason the
    # interface turns into the sign-up screen. Refunded, because nothing was
    # spent.
    if _guest_play_gated(request, where, topic_id):
        stored = getattr(pipeline, "has_stored_audio", None)
        if stored is None or not await stored(plan):
            _refund(reserved, user)
            raise HTTPException(status_code=403, detail=GUEST_GATE_MESSAGE,
                                headers={"X-FAM-Refused-By": "account"})
        # Kept, so it plays only what is kept (§237): a bucket that does
        # not answer stops the episode rather than waking the GPU.
        pipeline.stored_only = True

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
    # §242: Opus only where it can be carried; anything else is PCM, as before.
    stats.opus = fmt == "opus" and audio_codec_mod.can_stream(sample_rate)

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
            if first_pcm_at is None and audio_codec_mod.pcm_len(chunk) > WAV_HEADER_BYTES:
                first_pcm_at = time.monotonic() - started
            if sum(audio_codec_mod.pcm_len(c) for c in primed) - WAV_HEADER_BYTES >= preroll_bytes:
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
    if stats.sentences == 0 or sum(audio_codec_mod.pcm_len(c) for c in primed) <= WAV_HEADER_BYTES:
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
    primed_bytes = max(0, sum(audio_codec_mod.pcm_len(c) for c in primed) - WAV_HEADER_BYTES)
    primed_seconds = primed_bytes / (sample_rate * 2)
    # §242: a kept Opus episode arrives already framed; anything else is
    # encoded here, at the edge, 20 ms at a time as it streams.
    # An encoder that will not open (a PyAV built without libopus) answers in
    # PCM, which the header then says: the episode is already reserved and
    # primed, and a 500 here would spend it on nothing.
    encoder = None
    if stats.opus and not stats.opus_passthrough:
        try:
            encoder = audio_codec_mod.OpusStream(sample_rate)
        except Exception:  # noqa: BLE001
            log.exception("Opus encoder would not open; answering in PCM")
            stats.opus = False

    async def wire(chunk: bytes) -> bytes:
        # Encoding is ~5 ms of CPU per second of audio: off the event loop,
        # like the decode beside it, so one replay never stalls every request.
        if encoder is None:
            return chunk
        return await asyncio.to_thread(encoder.feed, chunk)

    async def body():
        nonlocal first_byte_at
        finished = False
        try:
            for chunk in primed:
                if first_byte_at is None:
                    first_byte_at = time.monotonic() - started
                yield await wire(chunk)
            async for chunk in source:
                if await request.is_disconnected():
                    log.info("client disconnected; abandoning generation")
                    break
                yield await wire(chunk)
            else:
                finished = True
            if encoder is not None and finished:
                # The tail and the true length: only for a whole episode, so
                # a stream cut short never claims to be complete.
                yield await asyncio.to_thread(encoder.close)
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
            refile_play()
            # Kept as long as the history row written at first audio shows
            # it (§173): that row could not pin a row that did not exist yet.
            if play_row and write_pending and stats.episode:
                _pin_heard(stats.episode)
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
    # Recorded now, as it always was - the play is a fact the moment audio
    # is served, and myFAM drawn while it plays must already know it was
    # heard. **An episode being written is then re-filed once it has been**
    # (§209): its category is the writer's last line, so the row logged here
    # can only carry the question's keyword tags; the stream's `finally`
    # corrects that same row (`EventStore.retag`) once the episode it played
    # is stored, so the first play of an episode is filed like every later
    # one. Only when that exact episode was stored - a listener who left
    # before it was keeps the keyword tags rather than borrowing the
    # category of an older episode under the same key.
    play_row = None
    write_pending = stats.cache != "hit"
    if user and _remembers(request):
        SOCIAL.seen(user)
        play_row = EVENTS.record(
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
                _event_tags(topic_id, plan.query, plan.minutes, stats.episode),
            )
        )

    def refile_play() -> None:
        if not (play_row and write_pending and stats.episode
                and SCRIPT_CACHE is not None):
            return
        try:
            if not SCRIPT_CACHE.resolve_episode(stats.episode):
                return
            EVENTS.retag(play_row, _event_tags(topic_id, plan.query,
                                               plan.minutes, stats.episode))
        except Exception:  # noqa: BLE001 - a label, never the episode
            log.exception("could not re-file a play under its category")

    media_type = ("audio/wav" if fmt == "wav"
                  else "audio/x-fam-opus" if stats.opus else "audio/L16")
    opus_headers = ({"X-FAM-Audio-Format": "opus",
                     "X-FAM-Opus-Preskip": str(stats.opus_preskip if stats.opus_passthrough
                                               else encoder.preskip)}
                    if stats.opus else {})
    return StreamingResponse(
        body(),
        media_type=media_type,
        headers={
            **opus_headers,
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",  # tell nginx not to buffer the stream
            "X-Sample-Rate": str(sample_rate),
            "X-Requested-Seconds": str(plan.target_seconds),
            # Whether this was a replay. The loading screen walks its steps
            # only for an episode that was actually written (§148) - a replay
            # has no steps, and holding one for ten seconds to check off work
            # nobody did would be the filler this app deletes.
            "X-FAM-Cache": stats.cache or "",
            # Which episode this is (§173): the listening history keeps it,
            # so a row replays this episode rather than re-asking.
            "X-FAM-Episode": stats.episode,
            # Whether a device may keep this audio for offline listening
            # (§161): the same test the server's own audio cache uses - a
            # production voice, a real script, not an attachment - so a
            # placeholder tone from an outage is never kept on a phone.
            "X-FAM-Keepable": "1" if (getattr(pipeline.engine, "keeps_audio", False)
                                      and not DEMO_MODE and not attach) else "0",
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


def _admin_accounts() -> set[str]:
    """Who is an admin by *account*: `FAM_ADMIN_ACCOUNTS`, comma separated.

    Each entry is an email address, a phone number or a listener id, matched
    against the signed-in account - so an admin opens `/admin` signed in to
    the app like anybody else, with no token to paste. Read at call time, so
    adding somebody is an environment edit and a restart, never a code change.
    Nothing is an admin by default: an unset variable is an empty set.
    """
    raw = os.environ.get("FAM_ADMIN_ACCOUNTS", "")
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def _admin_configured() -> bool:
    return bool(ADMIN_TOKEN or _admin_accounts())


#: The admin page's own session cookie. Signing in to the app is not signing
#: in to `/admin`: the dashboard asks for an admin's email and password every
#: time, and what that mints is a session held here and nowhere else - so a
#: phone left signed in to FAM is not a phone that can read every store.
#:
#: **And it never keeps you signed in** (27/09 packet, at the owner's
#: direction). Loading `/admin` ends whatever admin session the browser was
#: holding, so the page opens on the form every time; the cookie has no
#: max-age, so it dies with the browser too. It lives only as long as the one
#: open page that signed in - the page's own refreshes ride on it.
ADMIN_COOKIE = "fam_admin"


def _admin_listener(request: Request):
    """Whoever the admin cookie belongs to, or None. Never the app session."""
    token = request.cookies.get(ADMIN_COOKIE, "")
    if not token:
        return None
    return ACCOUNTS.listener_for(token)


def _allowed_admin(listener) -> bool:
    if listener is None or not listener.is_authenticated:
        return False
    allowed = _admin_accounts()
    if not allowed:
        return False
    mine = {listener.user_id.lower(), (listener.email or "").lower(),
            (listener.phone or "").lower()} - {""}
    return bool(mine & allowed)


def _is_admin_account(request: Request) -> bool:
    """Signed in to `/admin` with an admin's email and password."""
    return _allowed_admin(_admin_listener(request))


def _admin_request(request: Request) -> bool:
    """Whether this request carries an admin credential - either one."""
    if _is_admin_account(request):
        return True
    if not ADMIN_TOKEN:
        return False
    sent = (request.headers.get("x-admin-token")
            or request.headers.get("authorization", "").removeprefix("Bearer ").strip())
    return bool(sent) and hmac.compare_digest(sent, ADMIN_TOKEN)


def _require_admin(request: Request) -> None:
    """The admin credential or an admin account, or a 404.

    404 rather than 401: an unconfigured deployment should not advertise that
    it has a billing endpoint at all, and a wrong token should not tell the
    person holding it that they got the path right.

    Two ways in, one rule. `FAM_ADMIN_TOKEN` is for a machine or a terminal;
    `FAM_ADMIN_ACCOUNTS` names the accounts that are admins, checked against
    the admin session `/api/admin/login` minted from that account's email and
    password - never against the app's own session, and never against
    anything the client says about itself.
    """
    if _is_admin_account(request):
        return
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


@app.get("/api/admin/financials.xlsx", include_in_schema=False)
def admin_financials(request: Request) -> Response:
    """The finance workbook (`financials.py`), built from the stores now.

    Built on request rather than on a timer, so a copy downloaded today has
    today's spend in it and nothing has to remember to run. Reads only - no
    model, no network - so it is as safe on staging as `/api/usage`.
    """
    _require_admin(request)
    import financials

    now = time.time()
    return Response(
        content=financials.build(now),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f'attachment; filename="{financials.filename(now)}"',
                 "Cache-Control": "no-store"})


@app.get("/api/admin/financials/daily.json", include_in_schema=False)
def admin_financials_daily(request: Request) -> JSONResponse:
    """The finance workbook's Daily Spend rows, for the Google Sheet.

    `tools/financials_apps_script.gs`, pasted into the sheet, fetches this
    each morning with the admin token and replaces its Daily Spend tab, so
    the sheet's Costs and Projections follow production without a download.
    """
    _require_admin(request)
    import financials

    now = time.time()
    return JSONResponse(
        {"as_of": time.strftime("%Y-%m-%d", time.gmtime(now)),
         "columns": ["date", "episodes", "claude_usd", "exa_usd", "gpu_usd",
                     "pictures_usd"],
         "rows": financials.daily_rows(now)},
        headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- replay
#
# Staging spends nothing, so it cannot write a real episode (§172). It can
# replay one: `tools/replay_episodes.py` reads kept episodes out of one
# deployment and writes them into a zero-spend one, script and audio together,
# without `author`. Admin-only at both ends, like every endpoint that reads
# the stores whole.


def _replay_cache():
    if not hasattr(SCRIPT_CACHE, "export_episode"):
        raise HTTPException(status_code=501, detail=(
            "This deployment's script cache is not the SQLite one, so it has no "
            "kept episodes to move."))
    return SCRIPT_CACHE


@app.get("/api/admin/episodes")
async def admin_episodes(request: Request,
                         limit: int = Query(50, ge=1, le=500)) -> dict:
    """The most-played kept episodes, for choosing what to replay."""
    _require_admin(request)
    return {"episodes": _replay_cache().export_keys(limit)}


@app.get("/api/admin/episodes/{key}")
async def admin_episode_export(key: str, request: Request) -> dict:
    """One kept episode, whole: its script row and every voiced copy."""
    _require_admin(request)
    data = _replay_cache().export_episode(key)
    if data is None:
        raise HTTPException(status_code=404, detail="No kept episode with that key.")
    return data


@app.post("/api/admin/episodes")
async def admin_episode_import(request: Request) -> dict:
    """Write an exported episode into this deployment's cache.

    **Only on a zero-spend deployment.** Production's cache is what listeners
    are served, and an imported episode skips everything that decides what
    belongs there; staging's cache exists to be filled for testing.
    """
    _require_admin(request)
    if not spend_guard.enabled():
        raise HTTPException(status_code=409, detail=(
            "Episodes are imported only into a zero-spend deployment (staging). "
            "This one can spend, so its cache is written by listening, not by import."))
    data = await request.json()
    if not isinstance(data, dict) or not isinstance(data.get("script"), dict) \
            or not data["script"].get("key"):
        raise HTTPException(status_code=400, detail="Expected an exported episode.")
    key = _replay_cache().import_episode(
        data, keep_until=time.time() + settings.cache_life_seconds)
    return {"imported": key, "audio": len(data.get("audio") or [])}


# ---------------------------------------------------------------- tracker
#
# The live admin tracker (`admin_tracker.py`): every store, read on each
# request, and a question box over them. Behind `_require_admin` like the
# usage report, and for the same reason - it is every listener's data.

import admin_tracker


@app.get("/admin", include_in_schema=False)
async def admin_page(request: Request):
    """The tracker's page. A shell with no data in it; every number on it is
    fetched from the endpoints below, which are what check who is asking.
    Not served at all on a deployment with no admin configured."""
    if not _admin_configured():
        raise HTTPException(status_code=404, detail="Not found")
    page = PROJECT_ROOT / "admin_ui" / "tracker.html"
    response = HTMLResponse(page.read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store",
                                     "X-Robots-Tag": "noindex"})
    # Every load asks again: an admin session the browser brought with it is
    # ended here, not merely ignored, so it cannot be replayed either.
    old = request.cookies.get(ADMIN_COOKIE, "")
    if old:
        ACCOUNTS.end_session(old)
        response.delete_cookie(ADMIN_COOKIE, path="/")
    return response


class AdminLogin(BaseModel):
    email: str = ""
    password: str = ""


@app.post("/api/admin/login")
async def admin_login(req: AdminLogin, request: Request) -> JSONResponse:
    """The dashboard's sign-in: an admin account's email and password.

    One refusal for every failure - no such account, wrong password, a right
    password on an account that is not an admin - so the form cannot be used
    to learn which addresses are admins. 404 on a deployment with no admin
    accounts configured, like every other admin path.
    """
    if not _admin_accounts():
        raise HTTPException(status_code=404, detail="Not found")
    _rate_limit(request)
    refused = HTTPException(status_code=401,
                            detail="That email and password are not an admin's.")
    try:
        listener = await asyncio.to_thread(ACCOUNTS.log_in, req.email, req.password)
    except accounts_mod.AuthError:
        raise refused from None
    if not _allowed_admin(listener):
        log.warning("admin sign-in refused for a non-admin account")
        raise refused
    old = request.cookies.get(ADMIN_COOKIE, "")
    if old:
        ACCOUNTS.end_session(old)
    token, _ = ACCOUNTS.new_session(listener.user_id)
    response = JSONResponse({"ok": True, "email": listener.email})
    # No max_age: a browser-session cookie, never a remembered sign-in.
    response.set_cookie(ADMIN_COOKIE, token,
                        httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", path="/")
    return response


@app.post("/api/admin/logout")
async def admin_logout(request: Request) -> JSONResponse:
    token = request.cookies.get(ADMIN_COOKIE, "")
    if token:
        ACCOUNTS.end_session(token)
    response = JSONResponse({"ok": True})
    response.delete_cookie(ADMIN_COOKIE, path="/")
    return response


@app.get("/api/admin/tracker")
async def admin_tracker_snapshot(request: Request) -> dict:
    """Every headline number, read live from the stores."""
    _require_admin(request)
    snap = await asyncio.to_thread(admin_tracker.snapshot)
    snap["suggestions"] = admin_tracker.suggestions()
    snap["via"] = "account" if _is_admin_account(request) else "token"
    return snap


@app.get("/api/admin/schema")
async def admin_tracker_schema(request: Request) -> dict:
    """Every store, table, column and live row count."""
    _require_admin(request)
    return {"stores": await asyncio.to_thread(admin_tracker.schema)}


class AdminAsk(BaseModel):
    question: str = Field("", max_length=500)


class AdminQuery(BaseModel):
    sql: str = Field("", max_length=8000)


@app.post("/api/admin/ask")
async def admin_tracker_ask(req: AdminAsk, request: Request) -> dict:
    """A question in words. A built-in recipe when one fits, the model
    otherwise; the SQL that produced the answer always comes back with it."""
    _require_admin(request)
    try:
        return await admin_tracker.ask(req.question)
    except admin_tracker.QueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/admin/query")
async def admin_tracker_query(req: AdminQuery, request: Request) -> dict:
    """One read-only SELECT, run in the tracker's sandbox."""
    _require_admin(request)
    try:
        return await asyncio.to_thread(
            admin_tracker.run_query, req.sql, {"now": time.time()})
    except admin_tracker.QueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


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


# ---------------------------------------------------------------- thumbnails
#
# One picture per branch of the category tree (`thumbnails.py`, §160). The
# public endpoint serves approved pictures only and does nothing else: no
# model call, no generation, one indexed read. Everything that paints or
# judges a picture is behind `_require_admin`.


@app.get("/api/thumb/{node_id:path}", include_in_schema=False)
async def thumb_image(node_id: str) -> Response:
    """An approved tile picture. 404 for anything else, including a picture
    waiting for review - a tile only ever asks for one `pick` said was live.

    Cached for a year: the URL carries the picture's version (`?v=`), so a
    repainted picture is a different URL rather than a stale cache entry."""
    if not thumbnails_mod._exists():
        raise HTTPException(status_code=404, detail="Not found")
    found = await asyncio.to_thread(thumbnails_mod.store().image, node_id)
    if not found:
        raise HTTPException(status_code=404, detail="Not found")
    data, mime = found
    return Response(content=data, media_type=mime, headers={
        "Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/admin/thumbnails", include_in_schema=False)
async def admin_thumbnails_page(request: Request):
    """The review page. A shell, like `/admin`; every row on it is fetched
    from the endpoints below, which are what check who is asking."""
    if not _admin_configured():
        raise HTTPException(status_code=404, detail="Not found")
    page = PROJECT_ROOT / "admin_ui" / "thumbnails.html"
    return HTMLResponse(page.read_text(encoding="utf-8"),
                        headers={"Cache-Control": "no-store",
                                 "X-Robots-Tag": "noindex"})


@app.get("/api/admin/thumbnails")
async def admin_thumbnails(request: Request, status: str = "") -> dict:
    """Every picture's row (without the bytes), the tree nodes still without
    one, what painting them should cost, and what has been spent."""
    _require_admin(request)
    tree = topics_mod.category_tree()
    have = []
    if thumbnails_mod._exists():
        have = [t.as_dict() for t in
                await asyncio.to_thread(thumbnails_mod.store().all, status)]
    missing_count = 0
    if thumbnails_mod._exists():
        missing_count = len(thumbnails_mod.wanted(tree, thumbnails_mod.store()))
    else:
        missing_count = len(thumbnails_mod._facets()) + len(tree.nodes())
    return {"health": thumbnails_mod.health(), "thumbnails": have,
            "missing": missing_count,
            "estimate": thumbnails_mod.estimate(missing_count)}


@app.get("/api/admin/thumbnails/{node_id:path}/image", include_in_schema=False)
async def admin_thumbnail_image(node_id: str, request: Request,
                                pending: bool = False) -> Response:
    """Any picture, whatever its status, for the review page - or, with
    `?pending=1`, a repaint held beside the live one."""
    _require_admin(request)
    if not thumbnails_mod._exists():
        raise HTTPException(status_code=404, detail="Not found")
    found = await asyncio.to_thread(thumbnails_mod.store().image, node_id,
                                    any_status=True, pending=pending)
    if not found:
        raise HTTPException(status_code=404, detail="Not found")
    return Response(content=found[0], media_type=found[1],
                    headers={"Cache-Control": "no-store"})


class ThumbnailDecision(BaseModel):
    node: str = Field(..., min_length=1, max_length=200)
    action: str = Field(..., pattern="^(approve|reject)$")


@app.post("/api/admin/thumbnails/decide")
async def admin_thumbnail_decide(req: ThumbnailDecision, request: Request) -> dict:
    """A person's verdict on one picture. Approving is what puts a held
    picture on tiles; rejecting takes a live one off them at once."""
    _require_admin(request)
    if not thumbnails_mod._exists():
        raise HTTPException(status_code=404, detail="No pictures yet")
    held = thumbnails_mod.store()
    row = held.get(req.node)
    if row is None:
        raise HTTPException(status_code=404, detail="No picture for that node")
    if row.pending:
        # A repaint held beside a live picture: approving puts it live,
        # rejecting drops it and the live one stays exactly as it was.
        if req.action == "approve":
            held.promote_pending(req.node)
            return {"node": req.node, "status": thumbnails_mod.STATUS_APPROVED}
        held.drop_pending(req.node)
        return {"node": req.node, "status": row.status, "kept_live": True}
    if req.action == "approve" and held.image(req.node, any_status=True) is None:
        raise HTTPException(status_code=409,
                            detail="That node has no picture to approve; paint it again.")
    status = (thumbnails_mod.STATUS_APPROVED if req.action == "approve"
              else thumbnails_mod.STATUS_REJECTED)
    held.set_status(req.node, status,
                    "" if req.action == "approve" else "rejected by a person")
    return {"node": req.node, "status": status}


class ThumbnailRun(BaseModel):
    limit: int = Field(10, ge=1, le=500)
    nodes: list[str] = Field(default_factory=list, max_length=500)
    regenerate: bool = False
    retry_failed: bool = False


@app.post("/api/admin/thumbnails/run")
async def admin_thumbnail_run(req: ThumbnailRun, request: Request) -> dict:
    """Paint pictures now, in the background, inside the daily ceiling.
    Returns at once; the review page polls the list to watch them arrive."""
    _require_admin(request)
    ok, why = thumbnails_mod.configured()
    if not ok:
        raise HTTPException(status_code=409, detail=f"Cannot paint: {why}.")
    held = thumbnails_mod.store()
    todo = thumbnails_mod.wanted(topics_mod.category_tree(), held,
                                 regenerate=req.regenerate,
                                 retry_failed=req.retry_failed,
                                 only=req.nodes)
    if not todo:
        # Said rather than "started": a Repaint of a node the tree has since
        # pruned, or a Paint with nothing left, would otherwise report work
        # that never happens.
        raise HTTPException(status_code=409, detail=(
            "Nothing to paint: that node is no longer in the category tree."
            if req.nodes else "Nothing to paint: every node has a picture."))
    room = thumbnails_mod.daily_room(held)
    if room <= 0:
        raise HTTPException(status_code=409, detail=(
            "Today's image ceiling (THUMBNAILS_DAILY_IMAGES) is used up; "
            "try again tomorrow or raise it."))

    async def _run() -> None:
        result = await thumbnails_mod.backfill(
            req.limit, only=req.nodes, regenerate=req.regenerate,
            retry_failed=req.retry_failed)
        log.info("thumbnails (admin run): %s",
                 {k: v for k, v in result.items() if k != "nodes"})

    _BACKGROUND.add(asyncio.create_task(_run()))
    return {"started": True, "limit": req.limit, "wanted": len(todo),
            "room_today": room}


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


# --- the waitlist (WAITLIST.md) -------------------------------------------
#
# One database, one account: joining creates the real FAM account, waitlisted,
# and granting access flips its status. Viral Loops is told through the outbox
# and never waited on. Place in line is counted here (`waitlist.ordered`) and
# used everywhere - the status page, the admin table and "grant the top N".

def _initials(name: str, handle: str) -> str:
    words = [w for w in (name or "").split() if w[:1].isalnum()]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    if words:
        return words[0][:2].upper()
    return (handle or "?")[:2].upper()


def _waitlist_after_signup(user_id: str, referral_code: str = "") -> None:
    """The waitlist half of a new account, if it was created on the list.

    Sets who invited them (once), makes inviter and invitee friends straight
    away - a mutual follow is what a friend *is* here, so "Your FAM" on the
    waitlist and Your Friends in the app are the same rows - and queues their
    registration with Viral Loops. Never raises: the account already exists,
    and nothing about the waitlist may turn a successful sign-up into an error.
    """
    try:
        if WAITLIST.status_of(user_id) != waitlist_mod.WAITLISTED:
            return
        joined = WAITLIST.join(user_id, referral_code,
                               referrals_per_hour=settings.waitlist_referrals_per_hour)
        if joined.get("capped"):
            log.warning("waitlist: invite code %r passed its hourly cap; %r joined"
                        " without crediting it", referral_code, user_id)
        if joined["referrer"]:
            for a, b in ((user_id, joined["referrer"]), (joined["referrer"], user_id)):
                try:
                    SOCIAL.follow(a, b)
                except social_mod.SocialError:
                    log.exception("could not make waitlist friends %r -> %r", a, b)
        if not WAITLIST.has_action(user_id, "register"):
            WAITLIST.enqueue(user_id, "register")
            _kick_viral_loops()
    except Exception:  # noqa: BLE001 - the sign-up already succeeded
        log.exception("waitlist bookkeeping failed for %r", user_id)


def _admit_admin(listener):
    """An admin is never on the waitlist: the listener, let in if it is one.

    `FAM_ADMIN_ACCOUNTS` names who runs FAM, and the person checking what the
    waitlist looks like must not end up in its line - signing up at
    `/waitlist`, or signing in to an account the line already holds, leaves an
    admin's account `active`. Viral Loops is told only if it was told about
    them in the first place (a `register` already queued), so a fresh admin
    never appears on its leaderboard. Anyone else is returned untouched.
    """
    if listener is None or listener.status != waitlist_mod.WAITLISTED:
        return listener
    if not _allowed_admin(listener):
        return listener
    user = listener.user_id
    WAITLIST.grant([user], flag=WAITLIST.has_action(user, "register"))
    log.info("waitlist: %r is an admin account; kept off the line", user)
    return ACCOUNTS.listener_of(user)


def _waitlist_preview(place_of_next: int, cutoff: int) -> dict:
    """The numbers an admin's status page draws: where the next person to join
    would land, so the page shows exactly what a new member sees."""
    return {"place": place_of_next, "total": place_of_next, "cutoff": cutoff,
            "places_until": max(0, place_of_next - cutoff),
            "in_next_batch": bool(cutoff and place_of_next <= cutoff)}


def _kick_viral_loops() -> None:
    """Deliver the outbox now, in the background. Never awaited by a request."""
    if not VIRAL_LOOPS.configured:
        return
    try:
        task = asyncio.get_running_loop().create_task(
            viral_loops_mod.drain(WAITLIST, VIRAL_LOOPS))
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)
    except RuntimeError:
        pass  # no running loop (a synchronous caller); the timer delivers it


#: Refusals caused by a request shape FAM has since corrected; their calls are
#: sent again once at boot (PROBLEMS.md §205).
VIRAL_LOOPS_FIXED_REFUSALS = (("flag", "'participants' is required"),)


def _held_episode_keys() -> set:
    """The cache keys of every episode somebody saved, shared or vibed (§237).

    Derived from the three stores on every sweep rather than counted up and
    down as people press things: a tally kept beside the stores drifts the
    first time one path forgets to decrement it, and the failure is invisible
    - audio kept forever, or a saved episode's audio deleted. The key is
    `cache_key` of the question and the length, exactly as a shared link's
    replay builds it (§106), once researched and once not, because a row
    does not record which.

    Cost: one hash per distinct episode held - about two seconds per million.
    Past ten million or so, record the key on the row at save time instead.
    """
    pairs = set()
    for read in (SAVED.episodes, SHARES.episodes, SOCIAL.vibed_episodes):
        try:
            pairs.update((q, int(m or 0)) for q, m in read())
        except Exception:
            log.exception("audio sweep: could not read what is held from %s",
                          getattr(read, "__qualname__", read))
            raise
    keys = set()
    for query, minutes in pairs:
        if not query:
            continue
        for searched in (True, False):
            keys.add(cache_key(query, minutes, None, "", searched))
    return keys


#: Whether the last audio sweep ran, and what it did - `/api/health` says.
_AUDIO_SWEEP: dict = {}
#: The boot check's answer: did the bucket take a write, a read and a delete.
_AUDIO_STORE_CHECK: dict = {}


async def _check_audio_store() -> None:
    _AUDIO_STORE_CHECK.update(await asyncio.to_thread(audio_store_mod.verify))
    if _AUDIO_STORE_CHECK.get("ok"):
        log.info("audio store: R2 bucket %s answered in %sms",
                 settings.audio_bucket, _AUDIO_STORE_CHECK.get("ms"))
    else:
        # Failing uploads fall back to scripts.db by themselves; this says so
        # once, loudly, rather than once per episode.
        log.error("audio store: the R2 bucket did not answer (%s); new audio"
                  " stays in scripts.db until it does",
                  _AUDIO_STORE_CHECK.get("why"))


async def _sweep_audio_forever(every: float = 3600.0) -> None:
    """§237: once an hour, a week-old episode's audio is kept (saved, shared
    or vibed) or deleted, and queued bucket deletes are sent. Once a day it
    also releases kept audio nobody holds any more."""
    last_full = 0.0
    while True:
        try:
            held = await asyncio.to_thread(_held_episode_keys)
            full = time.time() - last_full >= 86400
            counts = await asyncio.to_thread(
                SCRIPT_CACHE.sweep_audio, held, None, full)
            if full:
                last_full = time.time()
            drain = getattr(SCRIPT_CACHE, "drain_deletes", None)
            deleted = await asyncio.to_thread(drain) if drain else 0
            _AUDIO_SWEEP.update({"at": time.time(), "held": len(held),
                                 "objects_deleted": deleted, **counts})
            if any(counts.values()) or deleted:
                log.info("audio sweep: %s, %d object(s) deleted", counts, deleted)
        except Exception:  # noqa: BLE001 - a loop that dies stops keeping
            # A sweep that could not read what is held must not delete
            # anything, and does not: `_held_episode_keys` raised first.
            log.exception("audio sweep failed; trying again next hour")
            _AUDIO_SWEEP.update({"at": time.time(), "error": "sweep failed"})
        await asyncio.sleep(every)


async def _drain_viral_loops_forever(every: float = 300.0) -> None:
    for action, error in VIRAL_LOOPS_FIXED_REFUSALS:
        try:
            if n := WAITLIST.reopen_refused(action, error):
                log.info("viral loops: re-sending %d %s call(s) refused for %r",
                         n, action, error)
        except Exception:  # noqa: BLE001 - never stop the drain over this
            log.exception("could not reopen refused viral loops calls")
    while True:
        try:
            result = await viral_loops_mod.drain(WAITLIST, VIRAL_LOOPS)
            if result["sent"] or result["failed"] or result["refused"]:
                log.info("viral loops outbox: %s", result)
        except Exception:  # noqa: BLE001 - a loop that dies stops retrying
            log.exception("viral loops outbox drain failed")
        await asyncio.sleep(every)


def _referral_link(request: Request, code: str) -> str:
    # Never relative: this link is pasted into other apps, where `/waitlist`
    # is not a link at all. A server with no public host of its own (a
    # laptop, a preview) hands out the app's home address instead.
    base = _public_base(request) or settings.app_home_url
    return f"{base}/waitlist?{waitlist_mod.REFERRAL_PARAM}={code}"


def _profile_state(user_id: str) -> dict:
    person = SOCIAL.person(user_id)
    prefs = PREFS.get(user_id)
    topics = list(prefs.interests)
    return {"name": person.get("name") or "", "handle": person.get("handle") or "",
            "avatar": person.get("avatar") or "", "topics": topics,
            # A photo is asked for and not required: a profile with a name, a
            # handle and something to be interested in is one a friend can
            # recognise and the feed can use.
            "complete": bool(person.get("name") and person.get("handle") and topics)}


def _waitlist_page() -> HTMLResponse:
    page = PROJECT_ROOT / "static" / "waitlist.html"
    return HTMLResponse(page.read_text(encoding="utf-8"),
                        headers={"Cache-Control": "no-store"})


@app.get("/waitlist", include_in_schema=False)
async def waitlist_landing(request: Request):
    """The waitlist's front door. A signed-in waitlisted account goes straight
    to its status page; an active one to the app."""
    listener = getattr(request.state, "listener", None)
    if listener is not None and listener.status == waitlist_mod.WAITLISTED:
        return RedirectResponse("/waitlist/me", status_code=302)
    return _waitlist_page()


@app.get("/waitlist/me", include_in_schema=False)
async def waitlist_status_page(request: Request):
    listener = getattr(request.state, "listener", None)
    if listener is None or not listener.is_authenticated:
        return RedirectResponse("/waitlist", status_code=302)
    return _waitlist_page()


class WaitlistJoinRequest(BaseModel):
    email: str = Field(..., max_length=accounts_mod.MAX_EMAIL)
    password: str = Field(..., max_length=accounts_mod.MAX_PASSWORD)
    referral_code: str = Field("", max_length=64)
    want_token: bool = False
    #: The join form's checkbox (§228).
    accept_terms: bool = False


def _admin_previewing_join(req, request: Request):
    """An admin who already has an account, typing it into the join form.

    The owner (09/10, PROBLEMS.md §233): admins put their own email into
    `/waitlist` to see the page a member sees, and on production they all
    have accounts already, so the join was refused "That email is already
    registered". Now an admin email whose password is right is signed in, as
    `/api/auth/login` would, and sent to the status page's admin preview
    (§216) - never put in line. Anybody else, or a wrong password, still
    gets the refusal, so the form says nothing new about who is an admin.
    """
    try:
        email = accounts_mod.clean_email(req.email)
    except accounts_mod.AuthError:
        return None
    if email not in _admin_accounts():
        return None
    try:
        listener = ACCOUNTS.log_in(email, req.password)
    except accounts_mod.AuthError:
        return None
    if not _allowed_admin(listener):
        return None
    listener = _admit_admin(listener)
    old = _session_token(request)
    token, _user_id = ACCOUNTS.new_session(listener.user_id)
    if old:
        ACCOUNTS.end_session(old)
    request.state.set_session = token
    return {**listener.as_dict(), "admin": True, "redirect": "/waitlist/me",
            **_maybe_token(request, token, req.want_token)}


@app.post("/api/waitlist/join")
async def waitlist_join(req: WaitlistJoinRequest, request: Request) -> dict:
    """Join: the app's own email-and-password sign-up, then the waitlist half.

    The same account the app will open with later - `ACCOUNTS.sign_up` on the
    session this browser already has - so granting access changes one column
    and moves nothing. **Joining the waitlist always puts the account on it**
    (10.2 packet), gate on or off: it used to follow `WAITLIST`, so on a
    server where the switch was not set a join made an active account,
    skipped the line and dropped the person into the app's own sign-up -
    which is what the owner hit. The gate still decides only whether the app
    is closed to them.
    """
    _rate_limit(request)
    _require_terms(request, req.accept_terms)
    admin_seen = _admin_previewing_join(req, request)
    if admin_seen is not None:
        return admin_seen
    user, fresh = _signup_listener(request)
    try:
        listener = await asyncio.to_thread(ACCOUNTS.sign_up, user, req.email,
                                           req.password, waitlisted=True)
    except accounts_mod.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    listener = _admit_admin(listener)
    if req.accept_terms:
        _record_terms(request, listener.user_id)
    _waitlist_after_signup(listener.user_id, req.referral_code)
    listener = ACCOUNTS.listener_of(listener.user_id)
    admin = _allowed_admin(listener)
    return {**listener.as_dict(), "admin": admin,
            # An admin is sent to the status page too, to see it as a member
            # would (`/api/waitlist/me`'s preview), never put in the line.
            "redirect": "/waitlist/me" if admin or listener.status == waitlist_mod.WAITLISTED
            else "/",
            **_signup_session(request, listener.user_id, fresh, req.want_token)}


@app.get("/api/waitlist/me")
async def waitlist_me(request: Request) -> dict:
    """Everything the status page draws, for the signed-in account.

    Place and "places until full access" come from `waitlist.ordered` and the
    admin's cutoff. Your FAM is this account's friends - mutual follows, the
    same list the app's Your Friends shows - and invites are the accounts that
    joined with this one's code.
    """
    _read_limit(request)
    user = _require_account(request)
    status = WAITLIST.status_of(user)
    code = WAITLIST.ensure_code(user)
    place = WAITLIST.place_of(user)
    cutoff = WAITLIST.cutoff()
    invites = WAITLIST.invites_of(user)
    friends = SOCIAL.friends(user)
    waiting = WAITLIST.waitlisted_among(p["user_id"] for p in friends)
    # Invitees first (they are what the page is counting), then any other
    # friend - somebody already in the app who followed back is FAM too.
    order = {uid: n for n, uid in enumerate(invites)}
    friends.sort(key=lambda p: order.get(p["user_id"], len(order)))
    account = ACCOUNTS.account(user) or {}
    profile = _profile_state(user)
    listener = getattr(request.state, "listener", None)
    admin = bool(listener is not None and listener.user_id == user
                 and _allowed_admin(listener))
    preview = (_waitlist_preview(WAITLIST.waitlisted_count() + 1, cutoff)
               if admin and place is None else None)
    return {
        "status": status,
        "waitlist": settings.waitlist,
        # An admin is never in line; `preview` is what a member joining now
        # would see, and the page draws it under an "Admin preview" note.
        "admin": admin,
        "preview": preview,
        "name": profile["name"] or account.get("display_name") or "",
        "place": place,
        "total": WAITLIST.waitlisted_count() if place is not None else 0,
        "cutoff": cutoff,
        "places_until": (max(0, place - cutoff) if place is not None else None),
        "in_next_batch": bool(place is not None and cutoff and place <= cutoff),
        "referral_code": code,
        "referral_link": _referral_link(request, code),
        "invites": len(invites),
        "unlock": waitlist_mod.next_unlock(
            len(invites), waitlist_mod.parse_unlocks(settings.waitlist_unlocks)),
        "fam": [{"name": p["name"], "handle": p["handle"], "avatar": p["avatar"],
                 "initials": _initials(p["name"], p["handle"]),
                 "invited": p["user_id"] in order,
                 "waitlisted": p["user_id"] in waiting} for p in friends],
        "profile": profile,
        # "Personalize your experience" (10.2 packet): what the status page's
        # profile editor fills its fields from. Their own, to them only.
        "details": {
            "email": account.get("email") or "",
            "phone": account.get("phone") or "",
            "birth_date": account.get("birth_date") or "",
            "location": PREFS.get(user).location.as_dict(),
        },
    }


# --- admin -----------------------------------------------------------------

@app.get("/admin/waitlist", include_in_schema=False)
async def admin_waitlist_page(request: Request):
    """The waitlist's admin page. A shell like /admin: every number on it is
    fetched from the endpoints below, which are what check who is asking, and
    it signs in through the same `/api/admin/login` every load."""
    if not _admin_configured():
        raise HTTPException(status_code=404, detail="Not found")
    page = PROJECT_ROOT / "admin_ui" / "waitlist.html"
    response = HTMLResponse(page.read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store",
                                     "X-Robots-Tag": "noindex"})
    old = request.cookies.get(ADMIN_COOKIE, "")
    if old:
        ACCOUNTS.end_session(old)
        response.delete_cookie(ADMIN_COOKIE, path="/")
    return response


@app.get("/api/admin/waitlist")
async def admin_waitlist(request: Request) -> dict:
    """The whole line, with the summary numbers. Admin only (404 otherwise)."""
    _require_admin(request)
    ordered = WAITLIST.ordered()
    rows = []
    complete = 0
    for row in ordered:
        profile = _profile_state(row["user_id"])
        complete += profile["complete"]
        rows.append({"user_id": row["user_id"], "place": row["place"],
                     "name": profile["name"] or row["display_name"],
                     "handle": profile["handle"], "email": row["email"],
                     "invites": row["invites"],
                     "profile_complete": profile["complete"],
                     "joined": row["joined"]})
    counts = WAITLIST.counts()
    top = sorted((r for r in rows if r["invites"]), key=lambda r: -r["invites"])[:5]
    return {
        "summary": {**counts,
                    "profile_complete_pct": (round(100 * complete / len(rows))
                                             if rows else 0),
                    "top_inviters": [{"name": r["name"], "handle": r["handle"],
                                      "email": r["email"], "invites": r["invites"]}
                                     for r in top]},
        "cutoff": WAITLIST.cutoff(),
        "gate": settings.waitlist,
        "viral_loops": {"configured": VIRAL_LOOPS.configured,
                        **WAITLIST.outbox_summary()},
        "rows": rows,
    }


class AdminGrantRequest(BaseModel):
    user_ids: list[str] = Field(default_factory=list, max_length=5000)
    top: int = Field(0, ge=0, le=100000)


@app.post("/api/admin/waitlist/grant")
async def admin_waitlist_grant(req: AdminGrantRequest, request: Request) -> dict:
    """Let one person, several, or the top N in. Each is flagged in Viral
    Loops through the outbox, which takes them off its leaderboard."""
    _require_admin(request)
    try:
        if req.top:
            granted = WAITLIST.grant_top(req.top)
        elif req.user_ids:
            granted = WAITLIST.grant(req.user_ids)
        else:
            raise waitlist_mod.WaitlistError("Name somebody to let in, or a number.")
    except waitlist_mod.WaitlistError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _kick_viral_loops()
    return {"granted": len(granted), "user_ids": granted}


class AdminPlanRequest(BaseModel):
    who: str = Field(..., min_length=1, max_length=200)
    plan: str = Field(..., min_length=1, max_length=40)


@app.post("/api/admin/plan")
async def admin_set_plan(req: AdminPlanRequest, request: Request) -> dict:
    """Move one account between plans (§207).

    There is no checkout, so this is the only way an account leaves `free`:
    a tester, a friend of the product, anybody the owner wants past the daily
    ceiling while quotas are enforced. `who` is the listener id, email or
    phone number on the account. Takes effect on their next request - the
    plan is read with the session.
    """
    _require_admin(request)
    user_id = ACCOUNTS.user_id_for(req.who)
    if not user_id:
        raise HTTPException(status_code=404, detail=f"No account matches {req.who!r}.")
    try:
        plan = ACCOUNTS.set_plan(user_id, req.plan.strip().lower())
    except accounts_mod.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("admin set plan %s for %s", plan, user_id)
    return {"user_id": user_id, "plan": plan}


class AdminCutoffRequest(BaseModel):
    cutoff: int = Field(..., ge=0, le=10_000_000)


@app.post("/api/admin/waitlist/cutoff")
async def admin_waitlist_cutoff(req: AdminCutoffRequest, request: Request) -> dict:
    _require_admin(request)
    return {"cutoff": WAITLIST.set_cutoff(req.cutoff)}


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
