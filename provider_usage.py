"""How often each outside data service is called, per day (§179).

The admin page shows it beside each service's limit, and in parentheses what
the next paid plan would raise that limit to - so the day a service is close
to its ceiling is seen on the page rather than discovered as an empty rail or
a missing score (docs/SCALING_TIMELINE.md says when each plan is worth buying).

Counted where the request goes out, not where it is asked for: a cache hit
never reaches here, and a request that failed is counted - it spent a slot
against the provider's limit all the same - with the failures kept beside it.

**Never on the critical path for real.** `record` is one small SQLite upsert,
it never raises, and a store that cannot be opened costs a log line. Days are
UTC because that is when API-Sports and GNews reset their daily allowances.

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
from typing import Optional

from paths import data_path

log = logging.getLogger("provider_usage")

DAY = 86400

#: The services the admin page reports, in the order it draws them.
PROVIDERS = ("api_sports", "exa", "gdelt", "polymarket", "gnews", "finnhub")

LABELS = {
    "api_sports": "API-Sports",
    "exa": "Exa",
    "gdelt": "GDELT",
    "polymarket": "Polymarket",
    "gnews": "GNews",
    "finnhub": "Finnhub",
}


def _plans() -> dict:
    """provider -> (the limit today, the next plan and what it allows).

    A function rather than a constant so the rationed allowances are read
    from the settings in force when the page is drawn.
    """
    from config import settings

    return {
        "api_sports": (
            f"{int(settings.api_sports_daily_requests):,}/day "
            "(free plan: 100/day, per process)",
            "Pro $19/mo: 7,500/day"),
        "exa": (
            "pay-as-you-go, ~10 requests/s per key",
            "no daily cap to buy; a second key adds rate"),
        "gdelt": (
            "1 request per 5 s per IP = 17,280/day at most",
            "no paid plan; a dedicated egress IP"),
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
    }


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
        with self._connect() as db:
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
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO calls (day, provider, requests, failures) "
                "VALUES (?, ?, 1, ?) ON CONFLICT(day, provider) DO UPDATE SET "
                "requests = requests + 1, failures = failures + excluded.failures",
                (day, provider, 0 if ok else 1))

    def days(self, count: int, now: Optional[float] = None) -> dict:
        """provider -> {day: (requests, failures)} for the last `count` days."""
        now = time.time() if now is None else now
        since = _day(now - (count - 1) * DAY)
        out: dict = {}
        with self._connect() as db:
            for day, provider, requests, failures in db.execute(
                    "SELECT day, provider, requests, failures FROM calls "
                    "WHERE day >= ?", (since,)):
                out.setdefault(provider, {})[day] = (int(requests), int(failures))
        return out

    def clear(self) -> None:
        with self._lock, self._connect() as db:
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
    """For tests: use this store (or open a fresh one on next use)."""
    global _STORE
    with _STORE_LOCK:
        _STORE = usage_store


def exists() -> bool:
    """Whether the store has been created. Reading never creates it."""
    if _STORE is not None:
        return True
    return os.path.exists(data_path("PROVIDER_USAGE_DB", "provider_usage.db"))


def record(provider: str, ok: bool = True) -> None:
    """Count one request that went out to `provider`. Never raises."""
    if provider not in LABELS:
        return
    try:
        store().record(provider, ok)
    except Exception as exc:  # noqa: BLE001 - counting never costs a request
        log.warning("could not count a %s request: %s", provider, exc)


def report(now: Optional[float] = None, days: int = 7) -> list[dict]:
    """One row per provider for the admin page. Never raises, never creates.

    `today` and `yesterday` are UTC days; `average` is over the last `days`
    days including today, so a service that has only just been switched on
    reads low rather than high.
    """
    now = time.time() if now is None else now
    try:
        seen = store().days(days, now) if exists() else {}
    except Exception as exc:  # noqa: BLE001 - a report is never load-bearing
        log.warning("could not read provider usage: %s", exc)
        seen = {}
    plans = _plans()
    today, yesterday = _day(now), _day(now - DAY)
    rows = []
    for provider in PROVIDERS:
        per_day = seen.get(provider, {})
        total = sum(requests for requests, _ in per_day.values())
        limit, upgrade = plans[provider]
        rows.append({
            "provider": provider,
            "label": LABELS[provider],
            "today": per_day.get(today, (0, 0))[0],
            "failed_today": per_day.get(today, (0, 0))[1],
            "yesterday": per_day.get(yesterday, (0, 0))[0],
            "average": round(total / max(1, days), 1),
            "limit": limit,
            "next": upgrade,
        })
    return rows
