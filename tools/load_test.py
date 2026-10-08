#!/usr/bin/env python3
"""Many listeners at once: what happens to latency when FAM is busy.

A Locust file. Each simulated user is one listener with its own server-minted
session (the cookie the first request is handed), doing what a person does:
open myFAM, swipe Explore, look at trending searches, play a kept episode,
open Go Deeper and the captions. The number that matters is not requests per
second, it is **time to the first byte of audio** - the one-sentence spec -
so a play is reported twice:

    audio: first byte   the tap to the first sample arriving
    audio: whole        the tap to the last sample (or LOAD_TEST_LISTEN_BYTES)

**Staging only, by default.** Staging spends nothing (`FAM_ENV=staging`,
spend_guard.py), so this measures the server - the event loop, SQLite, the
cache, the stream - and replays of kept episodes, never the model or the
voice. Production spends a model call and GPU time on every new episode and
refuses most of a crowd anyway (waitlist, quotas), so the test refuses to
start against any server whose `/api/health` does not report zero spend,
unless LOAD_TEST_ALLOW_SPEND=1 says somebody decided to pay for it.

Staging's cache is what `tools/replay_episodes.py --to` copied there. If
Explore comes back empty, plays are skipped and the run says so at the end;
replay some episodes first.

    pip install -r requirements-loadtest.txt
    locust -f tools/load_test.py --host https://fam-staging.onrender.com

then open http://localhost:8089, choose the number of listeners and the
spawn rate. Headless, for a fixed run with a CSV of every timing:

    locust -f tools/load_test.py --host https://fam-staging.onrender.com \\
        --headless -u 50 -r 5 -t 5m --csv load-50

Switches (environment variables):

    LOAD_TEST_SIGNUP=1        each listener signs up first, so the
                              account-only paths (logging, history) run too
    LOAD_TEST_FRESH=1         also type new searches (generation path); on
                              staging that is the zero-spend stand-in
    LOAD_TEST_LISTEN_BYTES=N  stop reading a play after N bytes (default: the
                              whole episode; 0 = first byte only)
    LOAD_TEST_ALLOW_SPEND=1   run against a server that is not zero-spend

The server paces each listener (`READ_LIMIT_PER_WINDOW`, 60 cheap reads per
10s) and every listener here has its own session, so a 429 in the results is
a real limiter firing, not the test sharing a bucket.
"""
from __future__ import annotations

import os
import random
import time
import uuid
from typing import Optional
from urllib.parse import urlencode

#: Every path this file requests, so a test can check each is still a route
#: (tests/test_load_test.py) - a load test that 404s measures nothing.
PATHS = (
    "/api/health",
    "/api/auth/me",
    "/api/auth/signup",
    "/api/myfam",
    "/api/explore",
    "/api/searches/trending",
    "/api/myfam/catalog",
    "/api/audio",
    "/api/next",
    "/api/transcript",
)

#: Typed searches for LOAD_TEST_FRESH. The three shapes the pipeline treats
#: differently: today's facts, evergreen, a live event.
FRESH_QUESTIONS = (
    "what happened with the fed this week",
    "how does a heat pump work",
    "how did the eagles do last night",
    "why is the ocean salty",
    "what is going on with housing prices",
)

#: Bytes per read while streaming. Small enough that the first chunk is the
#: first byte the server sent, not the first 64 KB of it.
CHUNK = 4096


def flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def listen_bytes() -> Optional[int]:
    """How much of a play to read: None for all of it."""
    raw = os.environ.get("LOAD_TEST_LISTEN_BYTES", "").strip()
    if not raw:
        return None
    return max(0, int(raw))


def spend_refusal(health: dict, allow_spend: bool) -> str:
    """Why this server must not be load-tested, or "" when it may be.

    Reads `/api/health`'s `environment` block (spend_guard.report()). A server
    too old to report it is treated as one that spends: unknown is not safe.
    """
    if allow_spend:
        return ""
    env = health.get("environment") or {}
    if env.get("zero_spend") is True:
        return ""
    return ("This server is not zero-spend (environment %r). A load test here "
            "pays for every new episode. Point --host at staging, or set "
            "LOAD_TEST_ALLOW_SPEND=1 if that is what you meant."
            % env.get("name", "unknown"))


def playable(payload: dict) -> list[tuple[str, int]]:
    """The (query, minutes) pairs an Explore response offers to replay."""
    pairs = []
    for card in (payload or {}).get("episodes") or []:
        query = (card.get("query") or "").strip()
        minutes = card.get("minutes")
        if query and isinstance(minutes, int):
            pairs.append((query, minutes))
    return pairs


def audio_path(query: str, minutes: int, *, cached_only: bool,
               surface: str) -> str:
    """The request the web player makes for one episode (`fam-audio.js`)."""
    params = {"q": query, "minutes": minutes, "fmt": "pcm", "surface": surface}
    if cached_only:
        params["cached_only"] = 1
    return "/api/audio?" + urlencode(params)


# Everything above runs without Locust installed, so the tests can import it.
# Importing Locust patches the standard library (gevent), which a test run
# must never do, so the users are only defined when Locust is the caller.
try:
    from locust import HttpUser, between, events, task
    from locust.exception import StopUser
except ImportError:  # pragma: no cover - only under `locust -f`
    HttpUser = None

if HttpUser is not None:  # pragma: no cover - exercised by Locust, not pytest
    _SEEN_EMPTY = {"explore": 0}

    @events.test_start.add_listener
    def _refuse_spending_servers(environment, **_kw):
        import requests

        host = (environment.host or "").rstrip("/")
        if not host:
            return
        try:
            health = requests.get(host + "/api/health", timeout=30).json()
            reason = spend_refusal(health, flag("LOAD_TEST_ALLOW_SPEND"))
        except Exception as exc:  # noqa: BLE001 - say why and stop
            reason = "Could not read %s/api/health: %s" % (host, exc)
        if reason:
            print("load_test: " + reason)
            environment.process_exit_code = 1
            if environment.runner is not None:
                environment.runner.quit()

    @events.test_stop.add_listener
    def _say_what_was_skipped(environment, **_kw):
        if _SEEN_EMPTY["explore"]:
            print("load_test: Explore was empty %d times, so those plays were "
                  "skipped. Copy episodes to staging with "
                  "tools/replay_episodes.py --to first." % _SEEN_EMPTY["explore"])

    class Listener(HttpUser):
        """One person using FAM: browse, then listen for a while."""

        # A person reads a screen before tapping; seconds, not milliseconds.
        wait_time = between(3, 10)

        def on_start(self) -> None:
            # The first /api request mints this listener's session; the client
            # keeps the cookie, so every later request is the same listener.
            # Not /api/health: that stats every database and would put its
            # own cost into the numbers.
            self.client.get("/api/auth/me", name="/api/auth/me")
            self.episodes: list[tuple[str, int]] = []
            if flag("LOAD_TEST_SIGNUP"):
                self.client.post("/api/auth/signup", name="/api/auth/signup", json={
                    "email": "loadtest+%s@example.invalid" % uuid.uuid4().hex[:12],
                    "password": uuid.uuid4().hex,
                })
            self.browse_explore()

        @task(4)
        def open_myfam(self) -> None:
            self.client.get("/api/myfam", name="/api/myfam")

        @task(3)
        def browse_explore(self) -> None:
            with self.client.get("/api/explore?limit=30", name="/api/explore",
                                 catch_response=True) as resp:
                if resp.status_code != 200:
                    resp.failure("HTTP %s" % resp.status_code)
                    return
                found = playable(resp.json())
                if found:
                    self.episodes = found
                else:
                    _SEEN_EMPTY["explore"] += 1

        @task(2)
        def trending(self) -> None:
            self.client.get("/api/searches/trending", name="/api/searches/trending")

        @task(1)
        def catalog(self) -> None:
            self.client.get("/api/myfam/catalog", name="/api/myfam/catalog")

        @task(5)
        def play_kept(self) -> None:
            if not self.episodes:
                return
            query, minutes = random.choice(self.episodes)
            self._play(audio_path(query, minutes, cached_only=True,
                                  surface="explore"), "kept")
            # What the player asks for beside the audio.
            params = urlencode({"q": query, "minutes": minutes})
            self.client.get("/api/next?" + params, name="/api/next")
            self.client.get("/api/transcript?" + params, name="/api/transcript")

        @task(2)
        def search_fresh(self) -> None:
            if not flag("LOAD_TEST_FRESH"):
                return
            # A per-listener suffix so each is a new key, not a cache hit.
            query = "%s %s" % (random.choice(FRESH_QUESTIONS), uuid.uuid4().hex[:4])
            self._play(audio_path(query, 2, cached_only=False, surface="search"),
                       "search")

        def _play(self, path: str, label: str) -> None:
            limit = listen_bytes()
            started = time.perf_counter()
            first = None
            received = 0
            error = None
            try:
                with self.client.get(path, name="/api/audio [%s]" % label,
                                     stream=True, catch_response=True) as resp:
                    if resp.status_code != 200:
                        resp.failure("HTTP %s" % resp.status_code)
                        return
                    for chunk in resp.iter_content(CHUNK):
                        if not chunk:
                            continue
                        if first is None:
                            first = time.perf_counter() - started
                        received += len(chunk)
                        if limit is not None and received >= limit:
                            break
                    resp.success()
            except StopUser:
                raise
            except Exception as exc:  # noqa: BLE001 - recorded, not raised
                error = exc
            whole = time.perf_counter() - started
            fire = self.environment.events.request.fire
            fire(request_type="AUDIO", name="audio: first byte [%s]" % label,
                 response_time=(first or whole) * 1000, response_length=0,
                 exception=error or (None if first is not None
                                     else RuntimeError("no audio arrived")),
                 context={})
            fire(request_type="AUDIO", name="audio: whole [%s]" % label,
                 response_time=whole * 1000, response_length=received,
                 exception=error, context={})
