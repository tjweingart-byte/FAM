#!/usr/bin/env python3
"""Many listeners at once: what happens to latency when FAM is busy.

A Locust file. Each simulated user is one listener with its own server-minted
session (the cookie the first request is handed), doing what a person does:
open the app (the page shell), keep it open (the notification poll every
open app runs, every three seconds), open myFAM, swipe Explore, look at
trending searches, play a kept episode, open Go Deeper and the captions. The
number that matters is not requests per second, it is **time to the first
byte of audio** - the one-sentence spec - so a play is reported twice:

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
    LOAD_TEST_SHAPE=name      a staged run (above); without it, -u and -r
    LOAD_TEST_USERS=N         the full load for a shape (default 1000)
    LOAD_TEST_HOLD=M          minutes the soak holds full load (default 60)
    LOAD_TEST_STEP=S          seconds per ramp/breakpoint step (180 / 120)
    LOAD_TEST_BASELINE=path   a quiet run's _stats.csv to compare against
    LOAD_TEST_REPORT=path     write the verdict and step table as Markdown
    LOAD_TEST_LISTEN=X        how long a listener stays on an episode, as a
                              share of its length (default 1; 0 = taps the
                              next thing at once, a stress run)
    LOAD_TEST_NO_POLL=1       leave out the notification poll (to see its
                              share of the load by difference)

**A run ends in a verdict** (LOAD_TESTING.md). Every run is judged against
the budgets in `BUDGETS` - p95 and p99 per kind of request, an error rate -
and exits 1 when one is missed, so a CI job or a person reads one line.
With LOAD_TEST_BASELINE pointing at a quiet run's `_stats.csv`, it also fails
when anything is more than `SLOWDOWN` times slower than it was with ten
listeners: "no slower with a thousand" is a comparison, not a feeling.

    LOAD_TEST_SHAPE=smoke     10 listeners, 2 minutes: the baseline
    LOAD_TEST_SHAPE=ramp      10% → 25% → 50% → 75% → 100% of LOAD_TEST_USERS
                              (default 1000), 3 minutes a step
    LOAD_TEST_SHAPE=spike     10, then all of them inside ~20s, then back to 10
    LOAD_TEST_SHAPE=soak      ramp up, then hold LOAD_TEST_HOLD minutes (60)
    LOAD_TEST_SHAPE=breakpoint  +10% every 2 minutes until it breaks (or 2x)

With a shape, the statistics are reset at each step, the verdict is taken on
the step at full load, and a table of every step is printed (and written to
LOAD_TEST_REPORT, if set) so the curve of latency against listeners is
visible. A thousand listeners is more than one Locust process should drive:
add `--processes -1` (one per core) or run workers on other machines.

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
    "/api/notifications",
    "/api/saved",
    "/api/history",
    "/api/friends",
    "/api/mixes",
)

#: What opening the app downloads from this server before any /api call:
#: the page shell and the audio player. Static files, not routes; a test
#: checks each is still in static/. `/` is index.html - about a megabyte, and
#: the largest thing most listeners ever fetch except audio.
ASSETS = ("/", "/fam-audio.js")

#: How often an open app asks `/api/notifications` (NOTIF_POLL_MS in
#: static/index.html, pinned by a test). At a thousand open apps this one
#: timer is ~330 requests a second on its own - more than everything else a
#: listener does - so a load test without it measures a quieter server than
#: the one people use.
NOTIF_POLL_SECONDS = 3.0

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


def listening_seconds(minutes: int, scale: float, draw: float) -> float:
    """How long a listener stays on an episode before doing anything else.

    A person who taps a two-minute episode listens to it - all of it, or
    something between a third and all of it (`draw`, 0..1) - with the app
    open and polling, and taps nothing meanwhile. Without this pause each
    simulated listener plays an episode every few seconds, and a thousand of
    them are a much busier crowd than a thousand people. LOAD_TEST_LISTEN
    scales it; 0 removes it for a stress run.
    """
    if scale <= 0 or minutes <= 0:
        return 0.0
    return minutes * 60.0 * scale * (0.3 + 0.7 * max(0.0, min(1.0, draw)))


def listen_scale() -> float:
    raw = os.environ.get("LOAD_TEST_LISTEN", "").strip()
    return 1.0 if not raw else max(0.0, float(raw))


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


# ------------------------------------------------------------ shapes
#
# A shape is a list of steps: (seconds the step lasts, listeners, spawn rate
# per second, whether the verdict is taken on it). Pure, so a test can check
# that "ramp" reaches the full load and that the verdict lands on a full step.

def _rate(users: int, seconds: float) -> float:
    """A spawn rate that reaches `users` in about `seconds`."""
    return max(1.0, round(users / max(1.0, seconds), 1))


def shape_steps(shape: str, users: int = 1000, hold_minutes: float = 60,
                step_seconds: Optional[float] = None) -> list[tuple[float, int, float, bool]]:
    users = max(1, int(users))
    shape = (shape or "").strip().lower()
    quiet = min(10, users)
    if shape == "smoke":
        return [(120, quiet, quiet, True)]
    if shape == "ramp":
        steps = []
        for share in (0.10, 0.25, 0.50, 0.75, 1.00):
            n = max(1, int(round(users * share)))
            steps.append((step_seconds or 180, n, _rate(n, 30), share == 1.00))
        return steps
    if shape == "spike":
        return [(120, quiet, quiet, False),
                (300, users, _rate(users, 20), True),
                (180, quiet, _rate(users, 20), False)]
    if shape == "soak":
        return [(300, users, _rate(users, 300), False),
                (hold_minutes * 60, users, _rate(users, 300), True)]
    if shape == "breakpoint":
        steps = []
        for i in range(1, 21):
            n = max(1, int(round(users * 0.1 * i)))
            steps.append((step_seconds or 120, n, _rate(n, 30), n >= users))
        return steps
    raise ValueError("unknown LOAD_TEST_SHAPE %r: smoke, ramp, spike, soak "
                     "or breakpoint" % shape)


#: When the breakpoint search stops: the step it broke on is the answer.
BREAK_FAIL_RATIO = 0.05
BREAK_P95_MS = 5000


# ------------------------------------------------------------ the verdict
#
# The budgets a run is judged on. Prefix-matched against Locust's names, first
# match wins; p95 and p99 in milliseconds. Set from what a listener notices,
# not from what this server happens to do today:
#
#   audio first byte   the spec - "within about a second audio starts". A
#                      kept replay has no model or voice in front of it, so
#                      the whole second is the server's to spend.
#   page shell         the app opening. Two seconds at p95 on a phone.
#   notifications      the poll every open app runs; it must stay trivial or
#                      it becomes the load.
#   everything else    a screen's JSON: half a second at p95.
#
# A 429 is a failure here: each listener has its own session, so one means
# the limiter refused a person using the app normally.
BUDGETS = (
    ("audio: first byte", 1000, 2000),
    ("audio: whole", None, None),          # bounded by the network, reported only
    ("/api/audio", None, None),            # its timing is the two lines above
    ("page: ", 2000, 4000),
    ("/api/notifications", 300, 1000),
    ("/api/auth/signup", 1500, 3000),
    ("/api/", 500, 1500),
)
MAX_ERROR_RATE = 0.01
#: With a baseline: how much slower than ten listeners is still "no slower".
#: Twice the p95, plus SLACK_MS so a 4 ms request becoming 9 ms is not news.
SLOWDOWN = 2.0
SLACK_MS = 50


def budget_for(name: str) -> tuple[Optional[float], Optional[float]]:
    for prefix, p95, p99 in BUDGETS:
        if name.startswith(prefix):
            return p95, p99
    return None, None


def verdict(rows: dict, baseline: Optional[dict] = None,
            max_error_rate: float = MAX_ERROR_RATE) -> list[str]:
    """Every budget a run missed, in words; [] means it passed.

    `rows` maps a request name to {"requests", "failures", "p95", "p99"}
    (Locust's own statistics or `rows_from_csv`). A run that measured no
    audio has not passed - it has measured nothing that matters - and says so.
    """
    problems = []
    total = sum(r["requests"] for r in rows.values())
    failed = sum(r["failures"] for r in rows.values())
    if not total:
        return ["no requests were made"]
    if failed / total > max_error_rate:
        problems.append("error rate %.2f%% (budget %.2f%%): %d of %d requests failed"
                        % (100.0 * failed / total, 100.0 * max_error_rate, failed, total))
    if not any(n.startswith("audio: first byte") and r["requests"]
               for n, r in rows.items()):
        problems.append("no audio was played, so time to first audio was not "
                        "measured (is Explore empty? replay episodes to staging)")
    for name, row in sorted(rows.items()):
        if not row["requests"]:
            continue
        p95_budget, p99_budget = budget_for(name)
        if p95_budget is not None and row["p95"] > p95_budget:
            problems.append("%s: p95 %d ms (budget %d ms)" % (name, row["p95"], p95_budget))
        if p99_budget is not None and row["p99"] > p99_budget:
            problems.append("%s: p99 %d ms (budget %d ms)" % (name, row["p99"], p99_budget))
        before = (baseline or {}).get(name)
        if before and before["requests"] and p95_budget is not None:
            allowed = before["p95"] * SLOWDOWN + SLACK_MS
            if row["p95"] > allowed:
                problems.append("%s: p95 %d ms under load vs %d ms quiet - more "
                                "than %.1fx slower" % (name, row["p95"], before["p95"], SLOWDOWN))
    return problems


def rows_from_csv(path: str) -> dict:
    """A Locust `<prefix>_stats.csv` as `verdict` rows (Aggregated left out)."""
    import csv

    rows = {}
    with open(path, newline="") as fh:
        for rec in csv.DictReader(fh):
            name = rec.get("Name") or ""
            if not name or name == "Aggregated":
                continue
            rows[name] = {"requests": int(rec["Request Count"]),
                          "failures": int(rec["Failure Count"]),
                          "p95": float(rec["95%"] or 0),
                          "p99": float(rec["99%"] or 0)}
    return rows


def step_table(steps: list[dict]) -> str:
    """The latency-against-listeners curve, one Markdown row per step."""
    lines = ["| listeners | req/s | errors | p95 all | p95 first audio | p95 page | p95 notifications |",
             "|---:|---:|---:|---:|---:|---:|---:|"]
    for s in steps:
        lines.append("| %d | %.0f | %.2f%% | %s | %s | %s | %s |" % (
            s["users"], s["rps"], 100.0 * s["fail_ratio"],
            _ms(s.get("p95_all")), _ms(s.get("p95_audio")),
            _ms(s.get("p95_page")), _ms(s.get("p95_notifications"))))
    return "\n".join(lines)


def _ms(value) -> str:
    return "-" if value is None else "%d ms" % value


# Everything above runs without Locust installed, so the tests can import it.
# Importing Locust patches the standard library (gevent), which a test run
# must never do, so the users are only defined when Locust is the caller.
try:
    from locust import HttpUser, LoadTestShape, between, events, task
    from locust.exception import StopUser
    from locust.runners import MasterRunner, WorkerRunner
except ImportError:  # pragma: no cover - only under `locust -f`
    HttpUser = None

if HttpUser is not None:  # pragma: no cover - exercised by Locust, not pytest
    import gevent

    _SEEN_EMPTY = {"explore": 0}
    _UNCOMPRESSED = {"shell": 0}
    #: One entry per finished step of a shape: what `step_table` draws.
    _STEPS: list[dict] = []
    #: The statistics the verdict is taken on, when a shape chose a step.
    _GATED: dict = {}

    def _rows(stats) -> dict:
        return {e.name: {"requests": e.num_requests, "failures": e.num_failures,
                         "p95": e.get_response_time_percentile(0.95) or 0,
                         "p99": e.get_response_time_percentile(0.99) or 0}
                for e in stats.entries.values()}

    def _p95(stats, prefix: str):
        hits = [e for e in stats.entries.values()
                if e.name.startswith(prefix) and e.num_requests]
        if not hits:
            return None
        return max(e.get_response_time_percentile(0.95) for e in hits)

    def _snapshot(stats, users: int) -> dict:
        total = stats.total
        return {"users": users, "rps": total.total_rps, "fail_ratio": total.fail_ratio,
                "p95_all": total.get_response_time_percentile(0.95) if total.num_requests else None,
                "p95_audio": _p95(stats, "audio: first byte"),
                "p95_page": _p95(stats, "page: "),
                "p95_notifications": _p95(stats, "/api/notifications")}

    @events.test_start.add_listener
    def _refuse_spending_servers(environment, **_kw):
        if isinstance(environment.runner, WorkerRunner):
            return  # the master asked already
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

    @events.quitting.add_listener
    def _judge(environment, **_kw):
        """The verdict: budgets, the baseline, the step table. Exit 1 on a miss."""
        if isinstance(environment.runner, WorkerRunner) or environment.process_exit_code:
            return
        if _SEEN_EMPTY["explore"]:
            print("load_test: Explore was empty %d times, so those plays were "
                  "skipped. Copy episodes to staging with "
                  "tools/replay_episodes.py --to first." % _SEEN_EMPTY["explore"])
        rows = _GATED.get("rows") or _rows(environment.stats)
        baseline_path = os.environ.get("LOAD_TEST_BASELINE", "").strip()
        baseline = rows_from_csv(baseline_path) if baseline_path else None
        problems = verdict(rows, baseline)
        lines = []
        if _STEPS:
            lines += ["", "Latency against listeners (each step measured on its own):", "",
                      step_table(_STEPS)]
            if _GATED.get("users"):
                lines += ["", "Verdict taken at %d listeners." % _GATED["users"]]
            if _GATED.get("broke_at"):
                lines += ["", "Broke at %d listeners: %s" % (_GATED["broke_at"], _GATED["why"])]
        lines += [""] + (["FAIL - %d budget(s) missed:" % len(problems)]
                         + ["  - " + p for p in problems] if problems
                         else ["PASS - every budget met%s." % (
                             " and nothing more than %.1fx slower than the baseline" % SLOWDOWN
                             if baseline else "")])
        report = "\n".join(lines)
        print("load_test:" + report)
        out = os.environ.get("LOAD_TEST_REPORT", "").strip()
        if out:
            with open(out, "w") as fh:
                fh.write("# Load test verdict\n" + report + "\n")
        if problems:
            environment.process_exit_code = 1

    if os.environ.get("LOAD_TEST_SHAPE", "").strip():
        class Steps(LoadTestShape):
            """LOAD_TEST_SHAPE as Locust steps, each measured on its own."""

            def __init__(self):
                super().__init__()
                self.steps = shape_steps(os.environ["LOAD_TEST_SHAPE"],
                                         int(os.environ.get("LOAD_TEST_USERS") or 1000),
                                         float(os.environ.get("LOAD_TEST_HOLD") or 60),
                                         float(os.environ.get("LOAD_TEST_STEP") or 0) or None)
                self.index = 0
                self.ends = 0.0

            def tick(self):
                now = self.get_run_time()
                if self.index == 0 and self.ends == 0.0:
                    self.ends = self.steps[0][0]
                if now >= self.ends:
                    if not self._close_step():
                        return None
                seconds, users, rate, _gate = self.steps[self.index]
                return users, rate

            def _close_step(self) -> bool:
                """Record the step just finished; False when the run is over."""
                stats = self.runner.stats
                seconds, users, rate, gate = self.steps[self.index]
                step = _snapshot(stats, users)
                _STEPS.append(step)
                if gate:
                    _GATED.update(rows=_rows(stats), users=users)
                if os.environ["LOAD_TEST_SHAPE"].strip().lower() == "breakpoint" and (
                        step["fail_ratio"] > BREAK_FAIL_RATIO
                        or (step["p95_all"] or 0) > BREAK_P95_MS):
                    _GATED.update(broke_at=users, why="%.1f%% errors, p95 %s" % (
                        100 * step["fail_ratio"], _ms(step["p95_all"])))
                    if not _GATED.get("rows"):
                        _GATED.update(rows=_rows(stats), users=users)
                    return False
                self.index += 1
                if self.index >= len(self.steps):
                    return False
                # Each step on its own numbers: the previous step's quiet
                # seconds would otherwise flatter the next one's percentiles.
                stats.reset_all()
                self.ends += self.steps[self.index][0]
                return True

    class Listener(HttpUser):
        """One person using FAM: open the app, browse, listen, keep it open."""

        # A person reads a screen before tapping; seconds, not milliseconds.
        wait_time = between(3, 10)

        def on_start(self) -> None:
            # The first /api request mints this listener's session; the client
            # keeps the cookie, so every later request is the same listener.
            # Not /api/health: that stats every database and would put its
            # own cost into the numbers.
            self.client.get("/api/auth/me", name="/api/auth/me")
            self.episodes: list[tuple[str, int]] = []
            self.account = False
            self.open_app()
            if flag("LOAD_TEST_SIGNUP"):
                resp = self.client.post("/api/auth/signup", name="/api/auth/signup", json={
                    "email": "loadtest+%s@example.invalid" % uuid.uuid4().hex[:12],
                    "password": uuid.uuid4().hex,
                    "accept_terms": True,
                })
                self.account = resp.status_code == 200
            self.poller = None
            if not flag("LOAD_TEST_NO_POLL"):
                self.poller = gevent.spawn(self._poll_notifications)
            self.browse_explore()

        def on_stop(self) -> None:
            if getattr(self, "poller", None) is not None:
                self.poller.kill(block=False)

        def open_app(self) -> None:
            """The page shell and the player, as a phone opening FAM fetches
            them. Content-Encoding is read so a run says whether the megabyte
            travelled compressed."""
            for path in ASSETS:
                name = "page: " + ("app shell" if path == "/" else path.lstrip("/"))
                with self.client.get(path, name=name, catch_response=True,
                                     headers={"Accept-Encoding": "gzip, br"}) as resp:
                    if resp.status_code != 200:
                        resp.failure("HTTP %s" % resp.status_code)
                    elif path == "/" and not resp.headers.get("Content-Encoding"):
                        _UNCOMPRESSED["shell"] += 1

        def _poll_notifications(self) -> None:
            """The timer every open app runs (startNotificationPolling)."""
            cursor = 0
            resp = self.client.get("/api/notifications?bootstrap=1",
                                   name="/api/notifications")
            try:
                cursor = int((resp.json() or {}).get("head") or 0)
            except Exception:  # noqa: BLE001 - a failed poll is already recorded
                pass
            while True:
                gevent.sleep(NOTIF_POLL_SECONDS)
                resp = self.client.get("/api/notifications?since=%d" % cursor,
                                       name="/api/notifications")
                try:
                    cursor = int((resp.json() or {}).get("head") or cursor)
                except Exception:  # noqa: BLE001
                    pass

        @task(1)
        def reopen_app(self) -> None:
            # Coming back to the app later: the shell again (the browser's
            # cache may answer this; a phone that was purged will not).
            self.open_app()

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

        @task(2)
        def account_screens(self) -> None:
            # The screens only an account has: saved, history, friends, mixes.
            if not self.account:
                return
            for path in ("/api/saved", "/api/history", "/api/friends", "/api/mixes"):
                self.client.get(path, name=path)

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
            # Then they listen, with the app open (the poll carries on).
            gevent.sleep(listening_seconds(minutes, listen_scale(), random.random()))

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

    @events.test_stop.add_listener
    def _say_the_shell_was_uncompressed(environment, **_kw):
        if _UNCOMPRESSED["shell"]:
            print("load_test: the app shell arrived uncompressed %d times - "
                  "about a megabyte per open, where gzip makes it a fraction "
                  "of that. See LOAD_TESTING.md." % _UNCOMPRESSED["shell"])
