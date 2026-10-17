"""Tiingo's free plan for market prices - while developing (§290).

**Temporary, at the owner's direction (10.16)**, like `api_sports`: a free
plan to use the app on while FAM is built, deleted when FAM goes commercial.
Tiingo's free plan is 50 requests an hour, 1,000 a day and 500 different
tickers a month, for personal use only (`provider_usage.licences` flags it).

It is a markets provider in Finnhub's place, not beside it: `TiingoSource`
is `live_sources.FinnhubSource` with Tiingo's requests, so the sentences, the
freshness check, the closed-market rule (§267) and the sweep's quotes
(`live_sources.QUOTES`) are Finnhub's own. A quote is translated into
Finnhub's shape (`c`, `pc`, `dp`, `t`) at the boundary.

* **One request for the whole watchlist**: IEX's top of book takes a list of
  tickers, so the myFAM sweep (`story_sources.TiingoSignals`) is one request
  where Finnhub's is thirteen.
* **The price is IEX's last sale**, real time, from one exchange's trades;
  it is said as the price, never as a consolidated close.
* **The session is computed, never asked** - Tiingo has no market-status
  endpoint. Outside 9:30-16:00 Eastern on a weekday the market is closed;
  inside it, it is open only when IEX's last sale is minutes old. A holiday
  or an early close therefore reads as "unknown", never "trading at".
* **Ticker from Tiingo's own search, never a guess** (§88).
* **Rationed here** (`TIINGO_HOURLY_REQUESTS`, `TIINGO_DAILY_REQUESTS`) below
  the plan's own limits, so a busy hour costs answers, never the account.

Written from Tiingo's documented shapes; **never run against the live API
from this repository** (the host is blocked here).
"""
from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import live_route
import live_sources
from config import settings
from live_facts import Entity, LiveFacts

log = logging.getLogger(__name__)

NAME = "Tiingo"
BASE = "https://api.tiingo.com"

EASTERN = ZoneInfo("America/New_York")
#: A last sale older than this during the session is not "trading now".
LIVE_SALE_SECONDS = 15 * 60


class Rationed(RuntimeError):
    """FAM's own hourly or daily stop for Tiingo is reached."""

    #: Read by `live_facts._ask`: a refusal is a sentence, not a crash.
    status = 429


class Ration:
    """Requests this hour and today, against FAM's ceilings."""

    def __init__(self) -> None:
        self._hour: deque = deque()
        self._day: tuple = ("", 0)

    @staticmethod
    def _today(now: float) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(now))

    def spend(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        while self._hour and now - self._hour[0] >= 3600.0:
            self._hour.popleft()
        day, count = self._day
        count = count if day == self._today(now) else 0
        if len(self._hour) >= int(settings.tiingo_hourly_requests):
            raise Rationed(f"Tiingo: this hour's {settings.tiingo_hourly_requests} "
                           "requests are spent (TIINGO_HOURLY_REQUESTS)")
        if count >= int(settings.tiingo_daily_requests):
            raise Rationed(f"Tiingo: today's {settings.tiingo_daily_requests} "
                           "requests are spent (TIINGO_DAILY_REQUESTS)")
        self._hour.append(now)
        self._day = (self._today(now), count + 1)

    def report(self, now: Optional[float] = None) -> dict:
        now = time.time() if now is None else now
        day, count = self._day
        return {"hour": sum(1 for at in self._hour if now - at < 3600.0),
                "today": count if day == self._today(now) else 0,
                "hourly_limit": int(settings.tiingo_hourly_requests),
                "daily_limit": int(settings.tiingo_daily_requests)}


#: One ration for the account: the sweep and the lookups share it.
RATION = Ration()


async def get(path: str, params: dict, timeout: Optional[float] = None):
    """One Tiingo request. The key goes in a header, never the URL (§144)."""
    RATION.spend()
    return await live_sources._json(  # noqa: SLF001
        f"{BASE}{path}", {"Authorization": f"Token {settings.tiingo_api_key}"},
        params, float(timeout or settings.live_timeout_seconds))


def _stamp(value) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        at = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).timestamp()


def to_quote(row: dict) -> Optional[dict]:
    """IEX top of book -> Finnhub's quote shape, or None without a price."""
    row = row or {}
    price = row.get("last") or row.get("tngoLast")
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    if not price:
        return None
    quote: dict = {"c": price}
    stamp = _stamp(row.get("lastSaleTimestamp")) or _stamp(row.get("timestamp"))
    if stamp:
        quote["t"] = int(stamp)
    try:
        previous = float(row.get("prevClose"))
    except (TypeError, ValueError):
        previous = 0.0
    if previous:
        quote["pc"] = previous
        quote["dp"] = (price - previous) / previous * 100.0
    return quote


def session(now: Optional[float] = None,
            last_sale: Optional[float] = None) -> Optional[bool]:
    """Whether the US market is trading: False outside the weekday session,
    True inside it only with a sale minutes old, else None (a holiday or an
    early close is not asserted either way)."""
    now = time.time() if now is None else now
    local = datetime.fromtimestamp(now, tz=EASTERN)
    opens = local.replace(hour=9, minute=30, second=0, microsecond=0)
    closes = local.replace(hour=16, minute=0, second=0, microsecond=0)
    if local.weekday() >= 5 or not (opens <= local < closes):
        return False
    if last_sale and now - last_sale <= LIVE_SALE_SECONDS:
        return True
    return None


async def iex(symbols: list, timeout: Optional[float] = None) -> dict:
    """symbol -> Finnhub-shaped quote, every symbol in one request."""
    if not symbols:
        return {}
    data = await get("/iex/", {"tickers": ",".join(s.lower() for s in symbols)},
                     timeout)
    out = {}
    for row in data if isinstance(data, list) else []:
        quote = to_quote(row) if isinstance(row, dict) else None
        if quote is not None:
            out[str(row.get("ticker") or "").upper()] = quote
    return out


def pick_listing(rows: list, subject: str) -> Optional[dict]:
    """The search row a subject means: the ticker typed exactly, else the
    first active US stock, else the first active US fund."""
    wanted = (subject or "").strip().lstrip("$").upper()
    usable = [r for r in rows or [] if isinstance(r, dict) and r.get("ticker")
              and r.get("isActive", True) is not False
              and str(r.get("countryCode") or "US").upper() == "US"]
    for row in usable:
        if str(row["ticker"]).upper() == wanted:
            return row
    for kind in ("Stock", "ETF"):
        for row in usable:
            if str(row.get("assetType") or "") == kind:
                return row
    return None


class TiingoSource(live_sources.FinnhubSource):
    """Finnhub's markets source, answered from Tiingo (see the docstring)."""

    name = NAME
    cost_per_call = 0.0
    #: IEX's last sale, in real time.
    delayed_seconds = 0.0

    def diagnose(self) -> tuple[bool, str]:
        if not settings.tiingo_api_key:
            return False, "TIINGO_API_KEY is not set"
        used = RATION.report()
        return True, (f"TIINGO_API_KEY present (free plan, temporary, §290): "
                      f"{used['hour']}/{used['hourly_limit']} this hour, "
                      f"{used['today']}/{used['daily_limit']} today; not "
                      "verified from this machine")

    async def verify(self) -> tuple[bool, str]:
        try:
            quotes = await iex(["AAPL"])
        except Exception as exc:  # noqa: BLE001
            return False, f"Tiingo did not answer: {type(exc).__name__}: {exc}"
        if "AAPL" not in quotes:
            return False, "Tiingo answered without a price for AAPL"
        return True, f"Tiingo accepted the key (AAPL at {quotes['AAPL']['c']})"

    async def resolve(self, brief) -> Optional[Entity]:
        subject = live_route.entity_of(brief)
        if not subject:
            return None
        # "Oracle Corporation (NYSE: ORCL)" names its own listing (§289's
        # 10.16 #5, as Finnhub reads it): looked up, never trusted - only a
        # row with exactly that ticker is used. Tiingo spells BRK.B "BRK-B".
        named = live_sources.ticker_named(subject).replace(".", "-")
        if named:
            rows = await self._search(named)
            exact = [r for r in rows if str(r.get("ticker") or "").upper() == named]
            best = pick_listing(exact, named)
            if best is not None:
                return self._entity(best)
        if not live_sources.looks_like_a_listing(subject):
            return None
        best = pick_listing(await self._search(subject), subject)
        return self._entity(best) if best is not None else None

    async def _search(self, query: str) -> list:
        try:
            rows = await get("/tiingo/utilities/search", {"query": query})
        except live_sources.ProviderHTTPError as exc:
            if 400 <= exc.status < 500 and exc.status not in (401, 403, 429):
                log.info("tiingo: no listing for %r (%s)", query, exc)
                return []
            raise
        return rows if isinstance(rows, list) else []

    def _entity(self, row: dict) -> Entity:
        symbol = str(row["ticker"]).upper()
        return Entity(domain="markets", provider=self.name, id=symbol,
                      label=str(row.get("name") or symbol).title())

    async def market_open(self) -> Optional[bool]:
        return session()

    async def fetch(self, entity: Entity) -> Optional[LiveFacts]:
        # The sweep's quote first (§267), when the session it was taken in
        # is the session now - computed, so it costs no request.
        held = live_sources.swept_quote(entity.id)
        if live_sources.usable_sweep(held):
            now_open = session(last_sale=(held["data"] or {}).get("t"))
            candidate = self.to_facts(held["data"], entity, held["is_open"])
            if candidate is not None and candidate.is_fresh() \
                    and now_open == held["is_open"]:
                log.info("tiingo: %s from the story sweep's quote", entity.id)
                return candidate
        quote = (await iex([entity.id])).get(entity.id.upper())
        if quote is None:
            return None
        return self.to_facts(quote, entity, session(last_sale=quote.get("t")))


async def sweep_quotes(symbols: list, timeout: float) -> tuple:
    """The sweep's `([(symbol, quote or None)], session)` in one request."""
    try:
        quotes = await iex(symbols, timeout)
    except Exception as exc:  # noqa: BLE001 - said by the sweep
        log.info("stories/tiingo: quotes failed: %s", exc)
        raise
    latest = max((q.get("t") or 0 for q in quotes.values()), default=0) or None
    return [(s, quotes.get(s.upper())) for s in symbols], session(last_sale=latest)

