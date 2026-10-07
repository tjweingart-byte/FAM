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
when API-Sports and GNews reset their daily allowances.

The limits are copied here from each provider's published plans, looked up
2026-09-30, and from the settings that ration them - re-check before buying
anything. Where FAM rations a free allowance itself (`API_SPORTS_DAILY_REQUESTS`,
`GNEWS_DAILY_REQUESTS`) the setting in force is read, never a copy of it.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from contextlib import closing
from typing import Optional

from paths import data_path

log = logging.getLogger("provider_usage")

DAY = 86400

#: The services the admin page reports, in the order it draws them.
PROVIDERS = ("api_sports", "exa", "gdelt", "polymarket", "gnews", "finnhub",
             "nws", "open_meteo", "local_feeds")

LABELS = {
    "api_sports": "API-Sports",
    "exa": "Exa",
    "gdelt": "GDELT",
    "polymarket": "Polymarket",
    "gnews": "GNews",
    "finnhub": "Finnhub",
    # §194: weather and the local news collector. NWS and the feeds cost
    # nothing, and are counted all the same - an outage is a count too.
    "nws": "National Weather Service",
    "open_meteo": "Open-Meteo",
    "local_feeds": "Local news feeds",
}


#: A request counted against a part of a provider as well as the provider:
#: API-Sports bills per sport (§180), so `api_sports` also keeps
#: `api_sports/hockey` and so on. Stored as its own provider-day row.
SUB = "/"


def _plans() -> dict:
    """provider -> (the limit today, the next plan and what it allows).

    A function rather than a constant so the rationed allowances are read
    from the settings in force when the page is drawn.
    """
    from config import settings

    return {
        "api_sports": (
            "billed and limited per sport - see each sport below",
            "each sport's own next plan"),
        "exa": (
            "pay-as-you-go, ~10 requests/s per key",
            "no daily cap to buy; a second key adds rate"),
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
        "finnhub": (
            "60/min = 86,400/day, personal use only",
            "commercial plan, quote-based (~$11.99/mo quoted)"),
        "nws": (
            "free, keyless; rate limit unpublished and generous",
            "no paid plan"),
        "open_meteo": (
            "API Standard $29/mo: 1,000,000 calls/month",
            "API Professional $99/mo: 5,000,000 calls/month"),
        "local_feeds": (
            "free; one conditional request per feed per poll",
            "no paid plan"),
    }


def licences() -> dict:
    """provider -> whether this deploy may use it commercially (§207).

    Only the three whose free plan forbids it: GNews (development use),
    Finnhub (personal, non-commercial) and Open-Meteo's keyless endpoint
    (non-commercial). The plan is what the deploy says it bought
    (`GNEWS_PLAN`, `FINNHUB_PLAN`) - neither provider's answer carries it -
    and Open-Meteo's is the key itself. `in_use` is whether the service is
    configured at all: an unused free plan is not a licence problem.

    `commercial_ready` is the one question before charging anybody money.
    """
    from config import settings
    import credentials

    gnews_paid = settings.gnews_plan not in ("", "free")
    finnhub_paid = settings.finnhub_plan not in ("", "free")
    meteo_key = bool(credentials.active("OPEN_METEO_API_KEY"))
    rows = {
        "gnews": {
            "in_use": bool((settings.gnews_key or "").strip()),
            "plan": settings.gnews_plan or "free",
            "commercial": gnews_paid,
            "buy": "Essential, EUR 49.99/mo (gnews.io); then GNEWS_PLAN=essential",
        },
        "finnhub": {
            "in_use": bool((settings.finnhub_key or "").strip()),
            "plan": settings.finnhub_plan or "free",
            "commercial": finnhub_paid,
            "buy": "a commercial plan, quote-based (finnhub.io); then FINNHUB_PLAN=commercial",
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
    ("api-sports.io", "api_sports"),
    ("finnhub.io", "finnhub"),
    ("polymarket.com", "polymarket"),
)


def provider_for_host(host: str) -> str:
    """The provider a request to `host` counts against, or "" for none here."""
    host = (host or "").lower()
    for suffix, provider in _HOSTS:
        if host == suffix or host.endswith("." + suffix):
            return provider
    return ""


class UsageStore:
    """`calls(day, provider, requests, failures)`, one row per provider-day."""

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

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def record(self, provider: str, ok: bool = True,
               now: Optional[float] = None) -> None:
        day = _day(time.time() if now is None else now)
        self.add({(day, provider): (1, 0 if ok else 1)})

    def add(self, counts: dict) -> None:
        """Add `{(day, provider): (requests, failures)}` in one transaction."""
        if not counts:
            return
        with self._lock:
            db = self._connect()
            try:
                with db:
                    db.executemany(
                        "INSERT INTO calls (day, provider, requests, failures) "
                        "VALUES (?, ?, ?, ?) ON CONFLICT(day, provider) DO UPDATE "
                        "SET requests = requests + excluded.requests, "
                        "failures = failures + excluded.failures",
                        [(day, provider, requests, failures)
                         for (day, provider), (requests, failures)
                         in counts.items()])
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

    def clear(self) -> None:
        with self._lock, closing(self._connect()) as db:
            db.execute("DELETE FROM calls")


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
        _WHY.clear()
        _LAST_FLUSH[0] = 0.0


def exists() -> bool:
    """Whether there is anything to report. Reading never creates the store."""
    if _STORE is not None:
        return True
    with _PENDING_LOCK:
        if _PENDING:
            return True
    return os.path.exists(data_path("PROVIDER_USAGE_DB", "provider_usage.db"))


#: How often, at most, the counts held in memory are written out.
FLUSH_SECONDS = 10.0

_PENDING: dict = {}
_PENDING_LOCK = threading.Lock()
_LAST_FLUSH = [0.0]
_FLUSHING = [False]


#: Why requests failed, per provider, since this server started (§218): the
#: admin page's "(101 failed)" said how many and never why. In memory only -
#: a reason is a diagnosis for whoever is looking now, not a record to keep -
#: and never a URL (Finnhub's key is a query parameter, §144).
_WHY: dict = {}
#: Most distinct reasons kept per provider; the rest are "other".
WHY_KINDS = 12


def record(provider: str, ok: bool = True, detail: str = "",
           why: str = "") -> None:
    """Count one request that went out to `provider`. Never raises, never waits.

    `detail` is the part of the provider it was billed to (an API-Sports
    sport), counted as well as the provider's own total. `why` says, for a
    failure, what went wrong ("HTTP 404", "ReadTimeout") - shown beside the
    count on the admin page. Adds to a count in memory; a background thread
    writes it out.
    """
    if provider not in LABELS:
        return
    try:
        day = _day(time.time())
        keys = [(day, provider)]
        if detail:
            keys.append((day, provider + SUB + detail))
        with _PENDING_LOCK:
            for key in keys:
                requests, failures = _PENDING.get(key, (0, 0))
                _PENDING[key] = (requests + 1, failures + (0 if ok else 1))
            if not ok:
                kinds = _WHY.setdefault(provider, {})
                reason = (why or "no reason given").strip()[:80]
                if reason not in kinds and len(kinds) >= WHY_KINDS:
                    reason = "other"
                kinds[reason] = kinds.get(reason, 0) + 1
            due = (not _FLUSHING[0]
                   and time.monotonic() - _LAST_FLUSH[0] >= FLUSH_SECONDS)
            if due:
                _FLUSHING[0] = True
        if due:
            threading.Thread(target=_flush_in_background, daemon=True,
                             name="provider-usage-flush").start()
    except Exception as exc:  # noqa: BLE001 - counting never costs a request
        log.warning("could not count a %s request: %s", provider, exc)


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
    than lost.
    """
    with _FLUSH_LOCK:
        _flush_locked()


def _flush_locked() -> None:
    try:
        # The store first, then the counts: a `reset` between the two leaves
        # nothing to take, so counts never land in a store they were not
        # recorded against.
        target = store()
    except Exception as exc:  # noqa: BLE001
        log.warning("could not open provider usage: %s", exc)
        return
    with _PENDING_LOCK:
        taken = dict(_PENDING)
        _PENDING.clear()
        _LAST_FLUSH[0] = time.monotonic()
    if not taken:
        return
    try:
        target.add(taken)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not write provider usage: %s", exc)
        with _PENDING_LOCK:
            for key, (requests, failures) in taken.items():
                held = _PENDING.get(key, (0, 0))
                _PENDING[key] = (held[0] + requests, held[1] + failures)


def failure_reasons(provider: str) -> list:
    """`[{"why", "count"}]`, commonest first, since this server started."""
    with _PENDING_LOCK:
        kinds = dict(_WHY.get(provider, {}))
    return [{"why": why, "count": count}
            for why, count in sorted(kinds.items(), key=lambda kv: -kv[1])]


def report(now: Optional[float] = None, days: int = 7) -> list[dict]:
    """One row per provider for the admin page. Never raises, never creates.

    `today` and `yesterday` are UTC days; `average` is over the last `days`
    days including today, so a service that has only just been switched on
    reads low rather than high.
    """
    now = time.time() if now is None else now
    try:
        if exists():
            flush()
        seen = store().days(days, now) if exists() else {}
    except Exception as exc:  # noqa: BLE001 - a report is never load-bearing
        log.warning("could not read provider usage: %s", exc)
        seen = {}
    plans = _plans()
    try:
        licensed = licences()["providers"]
    except Exception as exc:  # noqa: BLE001 - a report is never load-bearing
        log.warning("could not read licences: %s", exc)
        licensed = {}
    today, yesterday = _day(now), _day(now - DAY)
    rows = []
    for provider in PROVIDERS:
        per_day = seen.get(provider, {})
        total = sum(requests for requests, _ in per_day.values())
        limit, upgrade = plans[provider]
        row = {
            "provider": provider,
            "label": LABELS[provider],
            "today": per_day.get(today, (0, 0))[0],
            "failed_today": per_day.get(today, (0, 0))[1],
            "yesterday": per_day.get(yesterday, (0, 0))[0],
            "average": round(total / max(1, days), 1),
            "limit": limit,
            "next": upgrade,
        }
        if provider in licensed:
            row["licence"] = licensed[provider]
        why = failure_reasons(provider)
        if why:
            row["failure_reasons"] = why
        if provider == "api_sports":
            row["breakdown"] = _api_sports_breakdown(seen, today, yesterday, days)
        rows.append(row)
    return rows


def _api_sports_breakdown(seen: dict, today: str, yesterday: str,
                          days: int) -> list[dict]:
    """One row per API-Sports sport this deployment uses (§180): its calls,
    its plan and what the next plan costs, and what API-Sports itself last
    said about its limit - because each sport is its own bill."""
    try:
        import live_sources

        budgets = live_sources.budgets_report()
    except Exception as exc:  # noqa: BLE001 - a report is never load-bearing
        log.warning("could not read API-Sports' budgets: %s", exc)
        return []
    out = []
    for sport, budget in budgets.items():
        per_day = seen.get("api_sports" + SUB + sport, {})
        total = sum(requests for requests, _ in per_day.values())
        tier = budget.get("tier", "free")
        plan = live_sources.TIERS[tier]
        upgrade = live_sources.next_tier(sport)
        up = live_sources.TIERS.get(upgrade)
        out.append({
            "sport": sport,
            "label": budget.get("label", sport),
            "today": per_day.get(today, (0, 0))[0],
            "failed_today": per_day.get(today, (0, 0))[1],
            "yesterday": per_day.get(yesterday, (0, 0))[0],
            "average": round(total / max(1, days), 1),
            "tier": tier,
            "limit": (f"{plan['label']}: {budget.get('daily', plan['daily']):,}/day"
                      + (f" (${plan['usd']}/mo)" if plan["usd"] else "")),
            "next": (f"{up['label']} ${up['usd']}/mo: {up['daily']:,}/day"
                     if up else "top plan"),
            "remaining": budget.get("remaining"),
            "provider_limit": budget.get("provider_limit"),
            "provider_remaining": budget.get("provider_remaining"),
            "mismatch": bool(budget.get("mismatch")),
        })
    return out
