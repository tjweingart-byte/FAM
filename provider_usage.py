"""How often each outside data service is called, per day (§179).

The admin page shows it beside each service's limit, and in parentheses what
the next paid plan would raise that limit to - so the day a service is close
to its ceiling is seen on the page rather than discovered as an empty rail or
a missing score (docs/SCALING_TIMELINE.md says when each plan is worth buying).

Counted where the request goes out, not where it is asked for: a cache hit
never reaches here, and a request that failed is counted - it spent a slot
against the provider's limit all the same - with the failures kept beside it.

**Never on the critical path.** `record` adds one to a count in memory and
returns - it is called from the event loop, on the search path (live facts),
so it never touches the disk itself. The counts are written out by a
background thread at most every `FLUSH_SECONDS`, and before every report, so
a crash loses at most that many seconds of counts. It never raises, and a
store that cannot be opened costs a log line. Days are UTC because that is
when GNews resets its daily allowance.

The limits are copied here from each provider's published plans, looked up
2026-09-30, and from the settings that ration them - re-check before buying
anything. Where FAM rations a free allowance itself (`GNEWS_DAILY_REQUESTS`)
the setting in force is read, never a copy of it.
"""
from __future__ import annotations

import logging
import os
import sqlite3

import db as store_db
import threading
import time
from contextlib import closing
from typing import Optional

from paths import data_path

log = logging.getLogger("provider_usage")

DAY = 86400

#: The services the admin page reports, in the order it draws them. §470:
#: X, Claude, Google's image model and the R2 bucket were spent on and never
#: reported (X was counted and not shown; the other three were not counted).
PROVIDERS = ("balldontlie", "parallel", "perplexity", "gdelt", "polymarket",
             "gnews", "tiingo",
             "wikidata", "wikipedia", "wikimedia_enterprise",
             "nws", "open_meteo", "local_feeds",
             "x", "anthropic", "gemini", "r2")

LABELS = {
    "balldontlie": "BALLDONTLIE",
    "parallel": "Parallel",
    "perplexity": "Perplexity",
    "gdelt": "GDELT",
    "polymarket": "Polymarket",
    "gnews": "GNews",
    # §321: recent search for /admin/x, billed per post read.
    "x": "X",
    "tiingo": "Tiingo",
    # §278: the reference look.
    "wikidata": "Wikidata",
    "wikipedia": "Wikipedia",
    "wikimedia_enterprise": "Wikimedia Enterprise",
    # §194: weather and the local news collector. NWS and the feeds cost
    # nothing, and are counted all the same - an outage is a count too.
    "nws": "National Weather Service",
    "open_meteo": "Open-Meteo",
    "local_feeds": "Local news feeds",
    # §470: every Claude request, counted at the client (`anthropic_client`),
    # and what it cost from `metering` (the `spend` table below).
    "anthropic": "Claude (Anthropic)",
    # §470: the tile pictures' image model (`thumbnails._google_post`).
    "gemini": "Google image model",
    # §470: the audio and picture bucket (`audio_store.R2AudioStore`), by
    # Cloudflare's operation class.
    "r2": "Cloudflare R2",
}


#: A request counted against a part of a provider as well as the provider:
#: BALLDONTLIE's league (§275), so `balldontlie` also keeps `balldontlie/nfl`
#: and so on. Stored as its own provider-day row.
SUB = "/"


def _plans() -> dict:
    """provider -> (the limit today, the next plan and what it allows).

    A function rather than a constant so the rationed allowances are read
    from the settings in force when the page is drawn.
    """
    from config import settings

    return {
        # §275, §277: scores, standings and player and team reports, one
        # account for every league.
        "balldontlie": (
            f"{settings.balldontlie_tier} plan: "
            f"{_bdl_per_minute(settings.balldontlie_tier)}"
            " requests/min, every league",
            "ALL-STAR for injuries and season figures; GOAT for soccer "
            "injuries and golf and tennis figures (see balldontlie.io pricing)"),
        # §258: the default search and its second look.
        "parallel": (
            "pay-as-you-go, $1/1k (fast); 600 requests/min",
            "Enterprise: volume price and zero data retention"),
        "perplexity": (
            "pay-as-you-go, $1/1k (fast); 50 requests/s",
            "no daily cap to buy; higher rate on request"),
        "gdelt": (
            "export files, no per-address limit: ~2 requests per 15 min "
            "(~200/day), whatever the traffic (§211)",
            "no paid plan; none needed"),
        "polymarket": (
            "keyless; no published daily cap",
            "no paid plan"),
        "gnews": (
            f"{int(settings.gnews_daily_requests):,}/day guard "
            "(free plan: 100/day, 10 articles)",
            "Essential €49.99/mo: 1,000/day, 25 articles"),
        "tiingo": (
            f"{_tiingo_plan()} plan: {_tiingo_stops()}",
            "redistribution licence, quote-based (sales@tiingo.com)"),
        # §278: the reference look. Keyless routes are free; Wikimedia asks
        # identified clients to stay near 200 requests a minute.
        "wikidata": (
            "free, keyless; ~200 requests/min identified (User-Agent with contact)",
            "no paid plan"),
        "wikipedia": (
            "free, keyless; ~200 requests/min identified (User-Agent with contact)",
            "Wikimedia Enterprise (below) for a contracted route"),
        "wikimedia_enterprise": (
            f"free account: 50,000 requests/month; FAM stops at "
            f"{int(settings.wikimedia_monthly_requests):,} (WIKIMEDIA_MONTHLY_REQUESTS)",
            "a paid Enterprise plan, quote-based (enterprise.wikimedia.com)"),
        "nws": (
            "free, keyless; rate limit unpublished and generous",
            "no paid plan"),
        "open_meteo": (
            "API Standard $29/mo: 1,000,000 calls/month",
            "API Professional $99/mo: 5,000,000 calls/month"),
        "local_feeds": (
            "free; one conditional request per feed per poll",
            "no paid plan"),
        # §470: billed per post read; FAM's own daily stop is counted in
        # posts by `x_threads`, so its row's allowance reads that store.
        "x": (
            f"pay per post read; FAM stops at "
            f"{int(settings.x_threads_daily_reads):,} posts/day (X_THREADS_DAILY_READS)",
            "a higher X_THREADS_DAILY_READS; X bills each post read"),
        # §470: the account's usage tier sets its rate and monthly spend
        # limits; which tier this deploy is on is not copied here yet.
        "anthropic": (
            "the usage tier's rate and monthly spend limits (tier not recorded here)",
            "the next usage tier (console.anthropic.com, Limits)"),
        "gemini": (
            f"pay per image, ${float(settings.thumbnails_image_price):.3f} each; "
            f"FAM stops at {int(settings.thumbnails_daily_images):,} images/day "
            "(THUMBNAILS_DAILY_IMAGES)",
            "a higher THUMBNAILS_DAILY_IMAGES; Google bills each image"),
        "r2": (
            "free each month: 1,000,000 Class A and 10,000,000 Class B "
            "operations, 10 GB stored",
            "pay as you go: $4.50 per million Class A, $0.36 per million "
            "Class B, $0.015 per GB-month (Standard)"),
    }


def _bdl_per_minute(tier: str) -> int:
    import balldontlie

    return balldontlie.PER_MINUTE.get(tier, 5)


def _tiingo_plan() -> str:
    import tiingo

    return tiingo.plan()


def _tiingo_stops() -> str:
    import tiingo

    hour, day = tiingo.hourly_limit(), tiingo.daily_limit()
    if not hour and not day:
        return "no FAM stop (paid plan)"
    return ("FAM stops at "
            f"{f'{hour:,}/hour' if hour else 'no hourly stop'}, "
            f"{f'{day:,}/day' if day else 'no daily stop'}")


#: The periods an allowance can run over. A quota over a day or a month is
#: measured from the counts here (UTC days, as the store keeps them); a rate
#: per second, minute or hour is a pace, which daily counts cannot measure -
#: those are reported as rates with no share, never as a share of nothing.
QUOTA_PERIODS = ("day", "month")
RATE_PERIODS = ("second", "minute", "hour")


def _allowance(count, period: str, unit: str = "requests") -> dict:
    return {"count": int(count), "period": period, "unit": unit,
            "kind": "quota" if period in QUOTA_PERIODS else "rate"}


def allowances() -> dict:
    """provider (or provider/part) -> its limit as a number, or None (§470).

    The sentences in `_plans` say what each plan allows; nothing could be
    measured against them, so no warning could say "80% of the limit". These
    are the same limits as numbers, read from the settings in force where FAM
    rations a service itself, and None - said, never zero - where a service
    has no limit to measure. Never raises.
    """
    from config import settings

    out: dict = {p: None for p in PROVIDERS}
    try:
        out["balldontlie"] = _allowance(_bdl_per_minute(settings.balldontlie_tier),
                                        "minute")
        out["parallel"] = _allowance(600, "minute")
        out["perplexity"] = _allowance(50, "second")
        if int(settings.gnews_daily_requests or 0) > 0:
            out["gnews"] = _allowance(settings.gnews_daily_requests, "day")
        import tiingo

        if tiingo.daily_limit():
            out["tiingo"] = _allowance(tiingo.daily_limit(), "day")
        elif tiingo.hourly_limit():
            out["tiingo"] = _allowance(tiingo.hourly_limit(), "hour")
        out["wikidata"] = _allowance(200, "minute")
        out["wikipedia"] = _allowance(200, "minute")
        if int(settings.wikimedia_monthly_requests or 0) > 0:
            out["wikimedia_enterprise"] = _allowance(
                settings.wikimedia_monthly_requests, "month")
        out["open_meteo"] = _allowance(1_000_000, "month")
        if int(settings.x_threads_daily_reads or 0) > 0:
            out["x"] = _allowance(settings.x_threads_daily_reads, "day", "posts")
        if int(settings.thumbnails_daily_images or 0) > 0:
            out["gemini" + SUB + "images"] = _allowance(
                settings.thumbnails_daily_images, "day", "images")
        out["r2" + SUB + "class A"] = _allowance(1_000_000, "month", "operations")
        out["r2" + SUB + "class B"] = _allowance(10_000_000, "month", "operations")
    except Exception as exc:  # noqa: BLE001 - a report is never load-bearing
        log.warning("could not read the allowances: %s", exc)
    return out


def _used(allowance: Optional[dict], per_day: dict, now: float,
          provider: str) -> Optional[dict]:
    """The allowance with what was used of it and the share, for a quota."""
    if not allowance:
        return None
    out = dict(allowance)
    if out["kind"] != "quota":
        return out
    today, month = _day(now), _day(now)[:7]
    if provider == "x":
        used = _x_posts_today(now)
    elif out["period"] == "day":
        used = per_day.get(today, (0, 0))[0]
    else:
        used = sum(r for day, (r, _) in per_day.items() if day.startswith(month))
    out["used"] = used
    out["share"] = (round(used / out["count"], 4)
                    if used is not None and out["count"] else None)
    return out


def _x_posts_today(now: float) -> Optional[int]:
    """Posts read from X today, from `x_threads`' own count (X bills per post,
    and its requests carry many). None when nothing has been read."""
    try:
        import x_threads

        if not x_threads._exists():
            return None
        return int(x_threads.store().reads_since(x_threads._day_start(now)))
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read X's posts today: %s", exc)
        return None


def licences() -> dict:
    """provider -> whether this deploy may use it commercially (§207).

    Only those whose plan may forbid it: GNews (development use on free),
    Tiingo (every plan but a redistribution licence is internal use only,
    and speaking a price to listeners is redistribution) and Open-Meteo's
    keyless endpoint (non-commercial). The plan is what the deploy says it
    bought (`GNEWS_PLAN`, `TIINGO_PLAN`) - neither provider's answer carries it -
    and Open-Meteo's is the key itself. `in_use` is whether the service is
    configured at all: an unused free plan is not a licence problem.

    `commercial_ready` is the one question before charging anybody money.
    """
    from config import settings
    import credentials

    gnews_paid = settings.gnews_plan not in ("", "free")
    meteo_key = bool(credentials.active("OPEN_METEO_API_KEY"))
    rows = {
        "gnews": {
            "in_use": bool((settings.gnews_key or "").strip()),
            "plan": settings.gnews_plan or "free",
            "commercial": gnews_paid,
            "buy": "Essential, EUR 49.99/mo (gnews.io); then GNEWS_PLAN=essential",
        },
        # §418: Power and Commercial are internal use; prices said to
        # listeners need Tiingo's redistribution licence.
        "tiingo": {
            "in_use": bool((settings.tiingo_api_key or "").strip()),
            "plan": settings.tiingo_plan or "free",
            "commercial": settings.tiingo_plan == "redistribution",
            "buy": ("a redistribution licence (email sales@tiingo.com); "
                    "then TIINGO_PLAN=redistribution"),
        },
        "open_meteo": {
            "in_use": meteo_key or bool(settings.open_meteo_keyless),
            "plan": "paid key" if meteo_key else (
                "keyless" if settings.open_meteo_keyless else "none"),
            "commercial": meteo_key,
            "buy": "API Standard, $29/mo (open-meteo.com); then OPEN_METEO_API_KEY",
        },
    }
    if gnews_paid and int(settings.gnews_daily_requests) <= 100:
        rows["gnews"]["warning"] = (
            f"GNEWS_DAILY_REQUESTS is still {settings.gnews_daily_requests}: "
            "raise it to the plan (950 on Essential)")
    blocking = [p for p, r in rows.items() if r["in_use"] and not r["commercial"]]
    return {"providers": rows, "non_commercial_in_use": blocking,
            "commercial_ready": not blocking}


#: Hosts `live_sources._json` sends to, and whose request each one is.
_HOSTS = (
    ("polymarket.com", "polymarket"),
    ("balldontlie.io", "balldontlie"),
    ("tiingo.com", "tiingo"),
    # §278: the reference look (`wikimedia._send`).
    ("wikidata.org", "wikidata"),
    ("wikipedia.org", "wikipedia"),
    ("enterprise.wikimedia.com", "wikimedia_enterprise"),
)


def provider_for_host(host: str) -> str:
    """The provider a request to `host` counts against, or "" for none here."""
    host = (host or "").lower()
    for suffix, provider in _HOSTS:
        if host == suffix or host.endswith("." + suffix):
            return provider
    return ""


class UsageStore:
    """`calls(day, provider, requests, failures)`, one row per provider-day;
    `spend(day, source, model, ...)`, what the model calls cost per day (§470).
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = data_path("PROVIDER_USAGE_DB", "provider_usage.db", path)
        self._lock = threading.Lock()
        with closing(self._connect()) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS calls (
                              day      TEXT NOT NULL,
                              provider TEXT NOT NULL,
                              requests INTEGER NOT NULL DEFAULT 0,
                              failures INTEGER NOT NULL DEFAULT 0,
                              PRIMARY KEY (day, provider))""")
            # §470: every model call's tokens and dollars, per UTC day, by
            # what spent it (`source`) and on which model - the listener's
            # episodes, the editions, prefetch, the composer, the agents.
            # `metering`'s ledger is per listener episode; this is the whole
            # bill, the figure a provider's limit is measured against. No
            # listener id. `unpriced` counts calls with no rate in
            # `metering.PRICES`, whose dollars are not in `usd`.
            db.execute("""CREATE TABLE IF NOT EXISTS spend (
                              day                TEXT NOT NULL,
                              source             TEXT NOT NULL,
                              model              TEXT NOT NULL,
                              calls              INTEGER NOT NULL DEFAULT 0,
                              input_tokens       INTEGER NOT NULL DEFAULT 0,
                              output_tokens      INTEGER NOT NULL DEFAULT 0,
                              cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
                              cache_write_tokens INTEGER NOT NULL DEFAULT 0,
                              usd                REAL NOT NULL DEFAULT 0,
                              unpriced           INTEGER NOT NULL DEFAULT 0,
                              PRIMARY KEY (day, source, model))""")
            # §473: each usage monitor's reading of a service's own figures
            # (RunPod's spend, Atlas's storage...), one row per reading, kept
            # `READINGS_DAYS`. `payload` is the reading as JSON.
            db.execute("""CREATE TABLE IF NOT EXISTS monitor_readings (
                              at      REAL NOT NULL,
                              service TEXT NOT NULL,
                              level   TEXT NOT NULL DEFAULT '',
                              payload TEXT NOT NULL DEFAULT '{}')""")
            db.execute("CREATE INDEX IF NOT EXISTS monitor_readings_at "
                       "ON monitor_readings (service, at)")

    def add_reading(self, service: str, level: str, payload: str,
                    at: float) -> None:
        """Keep one monitor reading, and drop those past `READINGS_DAYS`."""
        with self._lock:
            db = self._connect()
            try:
                with db:
                    db.execute("INSERT INTO monitor_readings (at, service, level, "
                               "payload) VALUES (?, ?, ?, ?)",
                               (at, service, level, payload))
                    db.execute("DELETE FROM monitor_readings WHERE at < ?",
                               (at - READINGS_DAYS * DAY,))
            finally:
                db.close()

    def readings_since(self, at: float) -> list:
        """`[(at, service, level, payload)]` since `at`, newest first."""
        with closing(self._connect()) as db:
            return [tuple(r) for r in db.execute(
                "SELECT at, service, level, payload FROM monitor_readings "
                "WHERE at >= ? ORDER BY at DESC", (at,))]

    def _connect(self) -> sqlite3.Connection:
        db = store_db.connect(self.path, timeout=5, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def record(self, provider: str, ok: bool = True,
               now: Optional[float] = None) -> None:
        day = _day(time.time() if now is None else now)
        self.add({(day, provider): (1, 0 if ok else 1)})

    def add(self, counts: dict, spent: Optional[dict] = None) -> None:
        """Add `{(day, provider): (requests, failures)}` and
        `{(day, source, model): SPEND_FIELDS values}` in one transaction."""
        if not counts and not spent:
            return
        with self._lock:
            db = self._connect()
            try:
                with db:
                    if counts:
                        db.executemany(
                            "INSERT INTO calls (day, provider, requests, failures) "
                            "VALUES (?, ?, ?, ?) ON CONFLICT(day, provider) DO UPDATE "
                            "SET requests = calls.requests + excluded.requests, "
                            "failures = calls.failures + excluded.failures",
                            [(day, provider, requests, failures)
                             for (day, provider), (requests, failures)
                             in counts.items()])
                    if spent:
                        names = ", ".join(SPEND_FIELDS)
                        sums = ", ".join(f"{f} = spend.{f} + excluded.{f}"
                                         for f in SPEND_FIELDS)
                        db.executemany(
                            f"INSERT INTO spend (day, source, model, {names}) "
                            f"VALUES (?, ?, ?, {', '.join('?' for _ in SPEND_FIELDS)}) "
                            f"ON CONFLICT(day, source, model) DO UPDATE SET {sums}",
                            [(*key, *values) for key, values in spent.items()])
            finally:
                db.close()

    def days(self, count: int, now: Optional[float] = None) -> dict:
        """provider -> {day: (requests, failures)} for the last `count` days."""
        now = time.time() if now is None else now
        since = _day(now - (count - 1) * DAY)
        out: dict = {}
        with closing(self._connect()) as db:
            for day, provider, requests, failures in db.execute(
                    "SELECT day, provider, requests, failures FROM calls "
                    "WHERE day >= ?", (since,)):
                out.setdefault(provider, {})[day] = (int(requests), int(failures))
        return out

    def spend_since(self, day: str) -> list:
        """Every spend row from `day` (a UTC `YYYY-MM-DD`) on, as dicts."""
        with closing(self._connect()) as db:
            cursor = db.execute(
                f"SELECT day, source, model, {', '.join(SPEND_FIELDS)} FROM spend "
                "WHERE day >= ?", (day,))
            names = [c[0] for c in cursor.description]
            return [dict(zip(names, values)) for values in cursor]

    def clear(self) -> None:
        with self._lock, closing(self._connect()) as db:
            db.execute("DELETE FROM calls")
            db.execute("DELETE FROM spend")
            db.execute("DELETE FROM monitor_readings")


#: How long a monitor's readings are kept (§473).
READINGS_DAYS = 90

#: The spend table's summed columns, in order.
SPEND_FIELDS = ("calls", "input_tokens", "output_tokens", "cache_read_tokens",
                "cache_write_tokens", "usd", "unpriced")


def _day(at: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(at))


_STORE: Optional[UsageStore] = None
_STORE_LOCK = threading.Lock()


def store() -> UsageStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = UsageStore()
        return _STORE


def reset(usage_store: Optional[UsageStore] = None) -> None:
    """For tests: use this store (or open a fresh one on next use), and
    forget any counts not yet written."""
    global _STORE
    with _STORE_LOCK:
        _STORE = usage_store
    with _PENDING_LOCK:
        _PENDING.clear()
        _PENDING_SPEND.clear()
        _WHY.clear()
        _LAST_FLUSH[0] = 0.0
        _WRITES.update(_WRITES_EMPTY)


def exists() -> bool:
    """Whether there is anything to report. Reading never creates the store.

    §470: asks `db.has_tables`, which knows Postgres. It asked for the SQLite
    file, which a Postgres deployment never has, so after every deploy the
    admin page read no usage at all until the first request was counted.
    """
    if _STORE is not None:
        return True
    with _PENDING_LOCK:
        if _PENDING or _PENDING_SPEND:
            return True
    return store_db.has_tables(data_path("PROVIDER_USAGE_DB", "provider_usage.db"))


#: How often, at most, the counts held in memory are written out.
FLUSH_SECONDS = 10.0

_PENDING: dict = {}
#: Model spend not yet written (§470): (day, source, model) -> SPEND_FIELDS.
_PENDING_SPEND: dict = {}
_PENDING_LOCK = threading.Lock()
_LAST_FLUSH = [0.0]
_FLUSHING = [False]

#: How writing the counts has gone since this server started (§470). A write
#: that failed used to be a log line and nothing else; the counts waited in
#: memory and nothing on /admin or /api/health said they were behind.
_WRITES_EMPTY = {"last_written_at": None, "failures": 0,
                 "last_failure_at": None, "last_failure": "",
                 "waiting_since": None}
_WRITES: dict = dict(_WRITES_EMPTY)


#: Why requests failed, per provider, since this server started (§220): the
#: admin page's "(101 failed)" said how many and never why. In memory only -
#: a reason is a diagnosis for whoever is looking now, not a record to keep -
#: and never a URL (a key can be a query parameter, §144).
_WHY: dict = {}
#: Most distinct reasons kept per provider; the rest are "other".
WHY_KINDS = 12


def _held_since(at: float) -> None:
    """Under `_PENDING_LOCK`: note when the oldest unwritten count arrived."""
    if _WRITES["waiting_since"] is None:
        _WRITES["waiting_since"] = at


def _due() -> bool:
    """Under `_PENDING_LOCK`: whether a background write should start now."""
    due = (not _FLUSHING[0]
           and time.monotonic() - _LAST_FLUSH[0] >= FLUSH_SECONDS)
    if due:
        _FLUSHING[0] = True
    return due


def record(provider: str, ok: bool = True, detail: str = "",
           why: str = "") -> None:
    """Count one request that went out to `provider`. Never raises, never waits.

    `detail` is the part of the provider it was asked of (a BALLDONTLIE
    league), counted as well as the provider's own total. `why` says, for a
    failure, what went wrong ("HTTP 404", "ReadTimeout") - shown beside the
    count on the admin page. Adds to a count in memory; a background thread
    writes it out.
    """
    if provider not in LABELS:
        return
    try:
        now = time.time()
        day = _day(now)
        keys = [(day, provider)]
        if detail:
            keys.append((day, provider + SUB + detail))
        with _PENDING_LOCK:
            for key in keys:
                requests, failures = _PENDING.get(key, (0, 0))
                _PENDING[key] = (requests + 1, failures + (0 if ok else 1))
            _held_since(now)
            if not ok:
                kinds = _WHY.setdefault(provider, {})
                reason = (why or "no reason given").strip()[:80]
                if reason not in kinds and len(kinds) >= WHY_KINDS:
                    reason = "other"
                kinds[reason] = kinds.get(reason, 0) + 1
            due = _due()
        if due:
            threading.Thread(target=_flush_in_background, daemon=True,
                             name="provider-usage-flush").start()
    except Exception as exc:  # noqa: BLE001 - counting never costs a request
        log.warning("could not count a %s request: %s", provider, exc)


def spend(source: str, model: str, *, input_tokens: int = 0,
          output_tokens: int = 0, cache_read_tokens: int = 0,
          cache_write_tokens: int = 0, usd: Optional[float] = None,
          calls: int = 1) -> None:
    """Count what one model call cost (§470). Never raises, never waits.

    Called by `metering.Usage.add_model_call` for every Claude answer and by
    the few calls that price themselves (the image model). `usd` None means
    the model has no rate: the call is counted as unpriced, never as $0.
    """
    try:
        now = time.time()
        key = (_day(now), str(source or "other")[:40], str(model or "?")[:80])
        added = (int(calls), int(input_tokens or 0), int(output_tokens or 0),
                 int(cache_read_tokens or 0), int(cache_write_tokens or 0),
                 float(usd or 0.0), 0 if usd is not None else int(calls))
        with _PENDING_LOCK:
            held = _PENDING_SPEND.get(key)
            _PENDING_SPEND[key] = (added if held is None else
                                   tuple(a + b for a, b in zip(held, added)))
            _held_since(now)
            due = _due()
        if due:
            threading.Thread(target=_flush_in_background, daemon=True,
                             name="provider-usage-flush").start()
    except Exception as exc:  # noqa: BLE001 - counting never costs a call
        log.warning("could not count a %s call's spend: %s", model, exc)


def _flush_in_background() -> None:
    try:
        flush()
    finally:
        with _PENDING_LOCK:
            _FLUSHING[0] = False


#: One flush at a time, so a report that flushes waits for a background write
#: already holding the counts rather than reading the store before it lands.
_FLUSH_LOCK = threading.Lock()


def flush() -> None:
    """Write the counts held in memory to the store. Never raises.

    A write that fails puts its counts back, so they are tried again rather
    than lost, and is remembered (`status`) so the page can say so.
    """
    with _FLUSH_LOCK:
        _flush_locked()


def _failed(what: str, exc: BaseException) -> None:
    log.warning("could not %s provider usage: %s", what, exc)
    with _PENDING_LOCK:
        _WRITES["failures"] += 1
        _WRITES["last_failure_at"] = time.time()
        _WRITES["last_failure"] = f"could not {what} the store: {type(exc).__name__}: {exc}"[:300]


def _flush_locked() -> None:
    with _PENDING_LOCK:
        if not _PENDING and not _PENDING_SPEND:
            _LAST_FLUSH[0] = time.monotonic()
            return
    try:
        # The store first, then the counts: a `reset` between the two leaves
        # nothing to take, so counts never land in a store they were not
        # recorded against.
        target = store()
    except Exception as exc:  # noqa: BLE001
        with _PENDING_LOCK:
            _LAST_FLUSH[0] = time.monotonic()
        _failed("open", exc)
        return
    with _PENDING_LOCK:
        taken = dict(_PENDING)
        _PENDING.clear()
        spent = dict(_PENDING_SPEND)
        _PENDING_SPEND.clear()
        waiting_since = _WRITES["waiting_since"]
        _WRITES["waiting_since"] = None
        _LAST_FLUSH[0] = time.monotonic()
    if not taken and not spent:
        return
    try:
        # The spend only when there is some: a store's `add` took the
        # counts alone before §471, and a stand-in for one still may.
        if spent:
            target.add(taken, spent)
        else:
            target.add(taken)
    except Exception as exc:  # noqa: BLE001
        with _PENDING_LOCK:
            for key, (requests, failures) in taken.items():
                held = _PENDING.get(key, (0, 0))
                _PENDING[key] = (held[0] + requests, held[1] + failures)
            for key, values in spent.items():
                held = _PENDING_SPEND.get(key)
                _PENDING_SPEND[key] = (values if held is None else
                                       tuple(a + b for a, b in zip(held, values)))
            if waiting_since is not None and (
                    _WRITES["waiting_since"] is None
                    or waiting_since < _WRITES["waiting_since"]):
                _WRITES["waiting_since"] = waiting_since
        _failed("write", exc)
        return
    with _PENDING_LOCK:
        _WRITES["last_written_at"] = time.time()


def status() -> dict:
    """How the counts are being kept (§470), for /api/health and /admin.

    `behind` is true while counts are waiting in memory after a failed write
    or open - the store is behind, and the page's numbers are short by
    `waiting`. Never raises."""
    with _PENDING_LOCK:
        out = dict(_WRITES)
        out["waiting"] = sum(r for (_, p), (r, _) in _PENDING.items() if SUB not in p)
        out["waiting_spend_calls"] = sum(v[0] for v in _PENDING_SPEND.values())
    last_fail, last_ok = out["last_failure_at"], out["last_written_at"]
    out["behind"] = bool(last_fail and (not last_ok or last_fail > last_ok)
                         and (out["waiting"] or out["waiting_spend_calls"]))
    return out


def _behind_alert(row_provider: str, state: dict) -> str:
    """The words a row's `alert` carries while its counts are not written."""
    with _PENDING_LOCK:
        waiting = sum(r for (_, p), (r, _) in _PENDING.items() if p == row_provider)
    if row_provider == "anthropic":
        with _PENDING_LOCK:
            waiting += sum(v[0] for v in _PENDING_SPEND.values())
    if not waiting:
        return ""
    since = state.get("waiting_since")
    when = time.strftime("%H:%M UTC", time.gmtime(since)) if since else "a while"
    return (f"{waiting:,} count(s) since {when} are not written yet - "
            f"{state.get('last_failure') or 'the store is unwritable'}; "
            "the numbers here are short by them")


def failure_reasons(provider: str) -> list:
    """`[{"why", "count"}]`, commonest first, since this server started."""
    with _PENDING_LOCK:
        kinds = dict(_WHY.get(provider, {}))
    return [{"why": why, "count": count}
            for why, count in sorted(kinds.items(), key=lambda kv: -kv[1])]


#: Days read for a report: a month's quota needs the whole UTC month.
MONTH_DAYS = 31


def report(now: Optional[float] = None, days: int = 7) -> list[dict]:
    """One row per provider for the admin page. Never raises, never creates.

    `today` and `yesterday` are UTC days; `average` is over the last `days`
    days including today, so a service that has only just been switched on
    reads low rather than high. `allowance` (§470) is the limit as a number,
    with what was used of it and the share for a daily or monthly quota.
    """
    now = time.time() if now is None else now
    try:
        if exists():
            flush()
        seen = store().days(max(days, MONTH_DAYS), now) if exists() else {}
    except Exception as exc:  # noqa: BLE001 - a report is never load-bearing
        log.warning("could not read provider usage: %s", exc)
        seen = {}
    plans = _plans()
    limits = allowances()
    state = status()
    try:
        licensed = licences()["providers"]
    except Exception as exc:  # noqa: BLE001 - a report is never load-bearing
        log.warning("could not read licences: %s", exc)
        licensed = {}
    today, yesterday = _day(now), _day(now - DAY)
    first = _day(now - (days - 1) * DAY)
    spent = spend_report(now) if exists() else None
    rows = []
    for provider in PROVIDERS:
        per_day = seen.get(provider, {})
        total = sum(requests for day, (requests, _) in per_day.items()
                    if day >= first)
        limit, upgrade = plans[provider]
        if provider == "anthropic" and spent:
            limit = (f"{limit}; spent today ${spent['today']:,.2f}, this month "
                     f"${spent['month']:,.2f} (priced at metering.PRICES)")
        row = {
            "provider": provider,
            "label": LABELS[provider],
            "today": per_day.get(today, (0, 0))[0],
            "failed_today": per_day.get(today, (0, 0))[1],
            "yesterday": per_day.get(yesterday, (0, 0))[0],
            "average": round(total / max(1, days), 1),
            "limit": limit,
            "next": upgrade,
            "allowance": _used(limits.get(provider), per_day, now, provider),
        }
        if provider == "anthropic":
            row["spend"] = spent
        if provider in licensed:
            row["licence"] = licensed[provider]
        why = failure_reasons(provider)
        if why:
            row["failure_reasons"] = why
        if state["behind"]:
            alert = _behind_alert(provider, state)
            if alert:
                row["alert"] = alert
        if provider in BROKEN_DOWN:
            breakdown = _league_breakdown(seen, today, yesterday, days, provider,
                                          limits, now)
            if breakdown:
                row["breakdown"] = breakdown
        rows.append(row)
    return rows


def spend_report(now: Optional[float] = None) -> Optional[dict]:
    """What the model calls cost (§470): today, yesterday and this UTC month,
    in dollars at `metering.PRICES`, by source and by model, with the calls
    no rate covers. None when nothing was ever counted. Never raises."""
    now = time.time() if now is None else now
    try:
        if not exists():
            return None
        flush()
        month = _day(now)[:7]
        start = min(month + "-01", _day(now - DAY))
        rows = store().spend_since(start)
    except Exception as exc:  # noqa: BLE001 - a report is never load-bearing
        log.warning("could not read model spend: %s", exc)
        return None
    today, yesterday = _day(now), _day(now - DAY)

    def total(pick) -> float:
        return round(sum(float(r["usd"]) for r in rows if pick(r)), 6)

    def grouped(field: str) -> list:
        out: dict = {}
        for r in rows:
            g = out.setdefault(r[field], {field: r[field], "today": 0.0,
                                          "month": 0.0, "calls_today": 0,
                                          "unpriced_month": 0})
            if r["day"] == today:
                g["today"] += float(r["usd"])
                g["calls_today"] += int(r["calls"])
            if r["day"].startswith(month):
                g["month"] += float(r["usd"])
                g["unpriced_month"] += int(r["unpriced"])
        for g in out.values():
            g["today"], g["month"] = round(g["today"], 6), round(g["month"], 6)
        return sorted(out.values(), key=lambda g: (-g["month"], g[field]))

    return {
        "today": total(lambda r: r["day"] == today),
        "yesterday": total(lambda r: r["day"] == yesterday),
        "month": total(lambda r: r["day"].startswith(month)),
        "calls_today": sum(int(r["calls"]) for r in rows if r["day"] == today),
        "unpriced_today": sum(int(r["unpriced"]) for r in rows if r["day"] == today),
        "unpriced_month": sum(int(r["unpriced"]) for r in rows
                              if r["day"].startswith(month)),
        "by_source": grouped("source"),
        "by_model": grouped("model"),
    }


#: Providers whose requests are also counted by part (`record(detail=...)`),
#: each part a row under the provider: BALLDONTLIE by league (§275), GDELT
#: by which file it fetched (10.16 #4), so "why so many" is on the page;
#: the image model by what was asked and R2 by operation class (§470).
BROKEN_DOWN = {
    "balldontlie": ("the account's plan", "shared by every league"),
    "gdelt": ("part of the ~200/day", "what the requests were for"),
    "gemini": ("part of the image model's requests", "what they were for"),
    "anthropic": ("part of Claude's requests", "messages, or the batch API"),
    "r2": ("Cloudflare's operation class",
           "Class A writes and lists, Class B reads; deletes are free"),
}


def _league_breakdown(seen: dict, today: str, yesterday: str,
                      days: int, provider: str = "balldontlie",
                      limits: Optional[dict] = None,
                      now: Optional[float] = None) -> list[dict]:
    """One row per part of `provider` asked in the window - a BALLDONTLIE
    league, a kind of GDELT file: the rows say where the requests went, not
    what each costs."""
    out = []
    limit, upgrade = BROKEN_DOWN.get(provider, BROKEN_DOWN["balldontlie"])
    prefix = provider + SUB
    first = _day((now or time.time()) - (days - 1) * DAY)
    for key in sorted(k for k in seen if k.startswith(prefix)):
        per_day = seen[key]
        total = sum(requests for day, (requests, _) in per_day.items()
                    if day >= first)
        league = key[len(prefix):]
        if not any(day >= first for day in per_day) and key not in (limits or {}):
            continue
        out.append({
            "league": league,
            "label": league.upper() if provider == "balldontlie" else league,
            "today": per_day.get(today, (0, 0))[0],
            "failed_today": per_day.get(today, (0, 0))[1],
            "yesterday": per_day.get(yesterday, (0, 0))[0],
            "average": round(total / max(1, days), 1),
            "limit": limit,
            "next": upgrade,
            "allowance": _used((limits or {}).get(key), per_day,
                               now or time.time(), key),
        })
    return out
