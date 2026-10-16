"""Live providers, and the contract a real one has to meet.

`live_facts.py` is the seam - routing, freshness, caching, failure semantics.
This is where actual providers live, and where configuration turns them on.

Why they are separate
---------------------
The seam has no vendor in it and must keep none. A provider is a credential, a
wire format and a set of quirks about what "in progress" is called this week;
the seam is the rule that a score too old to be current is withheld. Mixing
them means the rule acquires a vendor's exceptions, and this project has
already learned once what a fallback buried in a provider costs (§61, §51).

What a provider must supply
---------------------------
Two methods, and they answer different questions:

* **`resolve(brief) -> Entity | None`** - *which* thing is this about, per the
  provider's own catalogue. Never an identifier a model produced: a
  hallucinated game id or ticker does not fail, it returns somebody else's
  state, fresh and authoritative and wrong, and nothing downstream can catch
  it. A provider with a search endpoint uses it; one without needs a mapping
  it can defend.
* **`fetch(entity) -> LiveFacts | None`** - what is true about it *now*.

And it declares three things about itself: `cost_per_call`, so §73's rule that
spend is recorded when it is spent can be kept; `delayed_seconds`, because a
fifteen-minute-delayed feed described as current is the failure this module
exists to prevent; and a `verify()` that makes a real request, because §52 says
a readiness check that only confirms configuration is not a readiness check.

What a sports provider should return, where it has it
-----------------------------------------------------
Entity id, the teams, the scheduled start, the status, the score, the period,
the clock, the relevant current statistics, the final score when final, and the
timestamp the provider observed all that. **Fields it does not have are
omitted, never filled in.** `facts` is a list of short spoken sentences because
that is what reaches a voice - no scorelines in a shape nobody says aloud, no
abbreviations, no hostnames.

Nothing real is configured here
-------------------------------
`fake` is a deterministic stand-in for tests, `./demo.sh` and the eval suite.
It is selected explicitly or not at all - never a fallback - which is §51 and
§61 applied to facts: a stand-in that can be reached by accident is a stand-in
that reaches a listener. It says what it is in its own source name, so an
episode built on it is traceable to it from the log and `/api/health`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import live_facts
import live_route
from config import settings
from live_facts import Entity, LiveFacts, LiveSource

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# The fake, for tests and demos
# --------------------------------------------------------------------------
class FakeSportsSource(LiveSource):
    """A scoreboard with no wire behind it. Selected explicitly, never fallen to.

    Its whole job is to let the in-progress path be exercised end to end -
    by tests, by `tools/ei_eval.py --only in-progress`, and by `./demo.sh` -
    without anybody needing a credential. It reports a game that is under way,
    so the state that produced §88 is the state it is easiest to reproduce.

    **It is not production data and never becomes it.** The name it reports
    says so, and that name reaches the prompt, the log and `/api/health`.
    """

    name = "fake scoreboard (NOT REAL DATA)"
    domain = "sports"
    cost_per_call = 0.0
    delayed_seconds = 0.0

    #: What it pretends is happening. Settable so a test can walk one entity
    #: through scheduled -> in_progress -> final without three providers.
    def __init__(self, status: str = live_facts.IN_PROGRESS,
                 label: str = "Kansas City Chiefs at Denver Broncos") -> None:
        self.status = live_facts.normalise_status(status)
        self.label = label
        self.calls = 0

    def diagnose(self) -> tuple[bool, str]:
        return True, ("the fake scoreboard is serving; it is a stand-in and "
                      "reports invented state, never real scores")

    async def verify(self) -> tuple[bool, str]:
        return True, "the fake scoreboard needs nothing and proves nothing"

    async def resolve(self, brief) -> Optional[Entity]:
        subject = live_route.entity_of(brief).lower()
        if not any(word in subject for word in ("chief", "bronco", "game", "match")):
            return None
        return Entity(domain="sports", provider=self.name, id="fake-game-1",
                      label=self.label,
                      starts_at=datetime.now(timezone.utc) - timedelta(hours=1))

    async def fetch(self, entity: Entity) -> Optional[LiveFacts]:
        self.calls += 1
        now = datetime.now(timezone.utc)
        if self.status == live_facts.SCHEDULED:
            said = ["The Chiefs and the Broncos kick off tonight at seven thirty "
                    "Central.",
                    "Neither side has played a snap yet."]
        elif self.status == live_facts.FINAL:
            said = ["The Chiefs beat the Broncos twenty-seven to sixteen.",
                    "The game is over."]
        else:
            said = ["The Chiefs lead the Broncos twenty-one to seven.",
                    "There are about ten minutes left in the third quarter.",
                    "Patrick Mahomes has thrown for a hundred and eighty yards."]
        return LiveFacts(domain="sports", source=self.name, as_of=now,
                         facts=said, status=self.status, entity=entity)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
#: Provider name -> how to build it. A name not in here is a configuration
#: error and is reported as one rather than silently serving nothing.
BUILDERS = {
    "sports": {
        "fake": lambda: FakeSportsSource(status=settings.live_fake_sports_status),
    },
    "markets": {},
    "elections": {},
}


#: The provider a domain gets when nobody named one and its credential is
#: present (§135). The owner's rule is that an episode's information comes
#: from the web search, GDELT, BALLDONTLIE, Finnhub and Polymarket - so a deployment that
#: holds a BALLDONTLIE or Finnhub key and forgot the second line naming it was
#: quietly answering live questions from articles alone, which is the "a
#: capability configured and not used" shape §119 was paid for. The key is
#: the statement of intent; a separate switch saying so again is a second
#: place to forget. `none` still switches a domain off on purpose.
#:
#: Elections is not here: Polymarket is keyless, so there is no credential
#: whose presence says anything, and `render.yaml` names it explicitly.
DERIVED_FROM_KEY = {
    # BALLDONTLIE is FAM's only sports provider (§277, §280).
    "sports": (("balldontlie", "balldontlie_api_key"),),
    "markets": (("finnhub", "finnhub_key"),),
}


def _provider(domain: str, named: str) -> str:
    named = (named or "").strip().lower()
    if named in ("none", "off", "0"):
        return ""
    if named:
        return named
    for provider, key in DERIVED_FROM_KEY.get(domain, ()):
        if getattr(settings, key, ""):
            return provider
    return ""


def configured() -> dict:
    """Which provider each domain is set to. Empty string means none.

    A domain nobody named gets its provider from its credential - see
    `DERIVED_FROM_KEY`.
    """
    return {
        "sports": _provider("sports", settings.live_sports_provider),
        "markets": _provider("markets", settings.live_markets_provider),
        "elections": _provider("elections", settings.live_elections_provider),
    }


def install() -> dict:
    """Build and register every configured provider. Returns what happened.

    Re-runnable, like `prefetch_sources.install`: every test that opens a
    TestClient runs startup again, and a duplicate-registration guard that took
    the server down at boot would be a worse bug than the one it prevents. So
    it unregisters its own first and leaves everything else alone.

    A name nobody can build is an error that is *reported*, not raised. A
    deployment with a typo in `LIVE_SPORTS_PROVIDER` should start and say
    loudly that it has no scores, rather than fail to start - the episodes are
    still answerable, and the honest "no live feed" block is what the writer
    gets. The reverse - starting quietly with no provider and a config that
    says otherwise - is the failure this whole file exists to avoid.
    """
    installed: list = []
    problems: list = []

    for domain, name in configured().items():
        for source in list(live_facts.sources_for(domain)):
            if getattr(source, "_fam_installed", False):
                live_facts.unregister(source.name)
        if not name:
            continue
        builder = BUILDERS.get(domain, {}).get(name)
        if builder is None:
            problems.append(
                f"LIVE_{domain.upper()}_PROVIDER={name!r} is not a provider "
                f"this build knows. Known: "
                f"{', '.join(sorted(BUILDERS.get(domain, {}))) or 'none'}. "
                f"No live {domain} data will be available.")
            continue
        try:
            source = builder()
        except Exception as exc:  # noqa: BLE001 - a bad provider must not stop boot
            problems.append(f"{domain} provider {name!r} could not be built: {exc}")
            continue
        source._fam_installed = True  # noqa: SLF001 - our own marker, see above
        live_facts.register(source)
        installed.append(source.name)

    # Weather (§194) is not chosen by name: NWS is keyless and Open-Meteo is
    # decided by its key inside `weather`, so `WEATHER=1` is the whole choice.
    live_facts.unregister("weather")
    if settings.weather:
        import weather

        source = weather.WeatherSource()
        source._fam_installed = True  # noqa: SLF001 - our own marker
        live_facts.register(source)
        installed.append(source.name)

    # Player and team reports (§275): beside the scoreboard, never in its
    # slot, so registered apart from it. The key is the switch, as it is for
    # the scoreboard and Finnhub.
    import balldontlie

    live_facts.unregister_players(balldontlie.NAME)
    if settings.player_facts and settings.balldontlie_api_key:
        source = balldontlie.PlayerReports(client=balldontlie.shared_client())
        source._fam_installed = True  # noqa: SLF001 - our own marker
        live_facts.register_players(source)
        installed.append(f"{source.name} (player reports)")

    for problem in problems:
        log.error("live facts: %s", problem)
    if installed:
        log.info("live facts: installed %s", ", ".join(installed))
    else:
        log.info("live facts: no provider configured; live questions will be "
                 "answered from indexed articles and will say so")
    return {"installed": installed, "problems": problems,
            "configured": configured()}


def report() -> dict:
    """What configuration asked for, beside what is actually registered.

    Both, because they come apart: a name with a typo is configured and not
    registered, and that difference is invisible from a source list alone.
    """
    registered = {d: [s.name for s in live_facts.sources_for(d)
                      if getattr(s, "_fam_installed", False)]
                  for d in live_facts.LIVE_DOMAINS}
    named = {"sports": settings.live_sports_provider,
             "markets": settings.live_markets_provider,
             "elections": settings.live_elections_provider}
    return {"configured": configured(), "registered": registered,
            # Which of those came from a key rather than a name, because the
            # two look identical in `configured` and only one was typed.
            "derived_from_key": sorted(
                d for d, name in configured().items()
                if name and not (named[d] or "").strip()),
            "known": {d: sorted(v) for d, v in BUILDERS.items()}}


# --------------------------------------------------------------------------
# Real providers
# --------------------------------------------------------------------------
# **None of these has made a live request from this machine.** The build
# container's egress proxy blocks every one of the hosts below, so each is
# written from the vendor's documented shapes and exercised against recorded
# payloads in `tests/test_live_providers.py`. Run `python tools/verify_live.py`
# somewhere with network before trusting any of them - that is exactly the
# §52 gap `verify()` exists to close, and it is still open here.
import httpx  # noqa: E402

import log_redaction  # noqa: E402


class ProviderHTTPError(RuntimeError):
    """A provider answered with an HTTP error. Carries no URL (§144).

    `httpx.HTTPStatusError`'s message is the whole request URL, and Finnhub's
    key is a query parameter, so every 422 printed the key into Render's logs
    - once in the message and again in the traceback. This carries the status
    and the endpoint's path, which is everything a reader of the log needs.
    """

    def __init__(self, status: int, where: str, body: str = ""):
        self.status = status
        self.where = where
        detail = f" - {body}" if body else ""
        super().__init__(f"HTTP {status} from {where}{detail}")


def _usage_detail(provider: str, url: str) -> str:
    """The part of a provider a request is also counted against:
    BALLDONTLIE's league (§275)."""
    if provider == "balldontlie":
        path = httpx.URL(url).path.strip("/").split("/")
        return path[0] if path and path[0] else ""
    return ""


async def _json(url: str, headers: dict, params: dict, timeout: float) -> dict:
    import provider_usage

    # Whose allowance this request spends, for the admin page (§179). Every
    # live and story provider comes through here, so the count is taken once.
    provider = provider_usage.provider_for_host(httpx.URL(url).host)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.get(url, headers=headers, params=params)
        except Exception as exc:
            provider_usage.record(provider, ok=False, detail=_usage_detail(provider, url),
                                  why=type(exc).__name__)
            raise
        # The reason names the path and status, never the query: Finnhub's
        # key is a query parameter (§144, §220).
        provider_usage.record(provider, ok=response.is_success,
                              detail=_usage_detail(provider, url),
                              why=f"HTTP {response.status_code} {response.url.path}")
        if not response.is_success:
            where = f"{response.url.host}{response.url.path}"
            body = log_redaction.redact(response.text[:160].strip())
            # `from None`: a chained HTTPStatusError would print the URL in
            # the traceback, which is the leak this exists to close.
            raise ProviderHTTPError(response.status_code, where, body) from None
        return response.json()


#: Words that make a subject a topic rather than a company or index name.
#: Finnhub's `/search` is a ticker lookup: it answers "Apple" or "NVDA", and a
#: DailyFAM subject like "Business and finance news of the last 24 hours" is
#: refused with a 422 (§144).
_TOPIC_WORDS = frozenset("""
    news industry industries sector sectors market markets economy economic
    economics latest today yesterday week weekly update updates trends
    startup startups
    """.split())
#: More words than this is a sentence, not a name. "Taiwan Semiconductor
#: Manufacturing Company" is four.
_MAX_LISTING_WORDS = 5


def looks_like_a_listing(subject: str) -> bool:
    """Could `subject` be a company, fund or index Finnhub can look up?

    Deliberately generous in one direction only: a false "yes" costs one
    request that returns nothing, a false "no" costs a price for an episode
    about a company - so it refuses only what is plainly a topic.
    """
    text = (subject or "").strip()
    if not text or any(ch in text for ch in "()?:;"):
        return False
    words = re.findall(r"[A-Za-z0-9&.'-]+", text)
    if not words or len(words) > _MAX_LISTING_WORDS:
        return False
    if re.search(r"\b(19|20)\d\d\b", text):
        return False
    return not any(w.lower() in _TOPIC_WORDS for w in words)



#: "(NYSE: ORCL)", "NASDAQ: AAPL", "ticker ORCL" - a listing named by its
#: exchange and ticker (10.16 #5).
_TICKER = re.compile(r"\b(?:NYSE(?:\s+American)?|NASDAQ|Nasdaq|AMEX|NYSEARCA|"
                     r"NYSE\s+Arca|ticker)\s*:?\s*\$?([A-Z]{1,5}(?:\.[A-Z])?)\b")

#: Finnhub's free plan quotes US stocks and funds. An index, a future, a
#: currency or a foreign listing answers 403 (10.16 #5), so a search row
#: that is one of those is never chosen.
_UNQUOTABLE_TYPES = ("index", "crypto", "forex", "future", "commodity", "bond")


def ticker_named(subject: str) -> str:
    """The ticker a subject names with its exchange, or ""."""
    found = _TICKER.search(subject or "")
    return found.group(1).upper() if found else ""


def quotable(row: dict) -> bool:
    """Whether a Finnhub search row is a listing the plan can quote."""
    symbol = str(row.get("symbol") or "")
    kind = str(row.get("type") or "").lower()
    if not symbol or any(ch in symbol for ch in "^:=/"):
        return False
    if refused_today(symbol):
        return False
    return not any(word in kind for word in _UNQUOTABLE_TYPES)


#: symbol -> the UTC day Finnhub refused to quote it (10.16 #5).
_REFUSED: dict = {}


def _utc_day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def note_refused(symbol: str) -> None:
    _REFUSED[str(symbol or "").upper()] = _utc_day()


def refused_today(symbol: str) -> bool:
    return _REFUSED.get(str(symbol or "").upper()) == _utc_day()


#: symbol -> {"data": Finnhub's quote, "is_open": the session then, "at": when
#: swept}. Written by the story sweep (`story_sources.FinnhubSignals`) for
#: the watchlist only - never the whole market - and read by
#: `FinnhubSource.fetch` (§267), the same seam `CARD` is for scores.
QUOTES: dict = {}


def remember_quote(symbol: str, data: dict, is_open: Optional[bool],
                   now: Optional[float] = None) -> None:
    if not symbol or not data or data.get("c") in (None, 0):
        return
    QUOTES[symbol.upper()] = {"data": dict(data), "is_open": is_open,
                              "at": time.time() if now is None else now}


def swept_quote(symbol: str) -> Optional[dict]:
    """The sweep's quote for `symbol`, if it took one. Freshness is the
    caller's check (`LiveFacts.is_fresh`), on the quote's own time."""
    return QUOTES.get((symbol or "").upper())


def usable_sweep(held: Optional[dict], now: Optional[float] = None) -> bool:
    """Whether a swept quote may stand in for a live one at all (§267).

    It needs its own trade time - without one `to_facts` would date it now
    and it would never age - a session the sweep could read, and a sweep no
    older than two of its own intervals: a Friday close kept through a
    weekend of failed sweeps is not what "the session has not changed"
    vouches for on Monday evening.
    """
    if not held or held.get("is_open") is None:
        return False
    if not (held.get("data") or {}).get("t"):
        return False
    now = time.time() if now is None else now
    limit = 2 * float(settings.stories_markets_interval_seconds)
    return now - float(held.get("at") or 0.0) <= limit


class FinnhubSource(LiveSource):
    """Finnhub quotes. The most generous free tier in market data.

    60 requests a minute free, and paid starts at $11.99/mo. The one catch is
    licensing rather than engineering: the free tier is personal/non-commercial,
    so a monetised app needs the paid plan (`FINNHUB_PLAN`).

    **Delayed by about twenty minutes on the free tier**, which is fine here
    and not fine elsewhere: `delayed_seconds` makes the prompt say "delayed by
    twenty minutes" rather than "current". A provider that is honestly late is
    usable; one that is quietly late is the failure this subsystem exists to
    prevent.

    **And it knows whether the market is open**, which matters more than the
    delay. A quote pulled at 3am is not what something "is trading at" - it is
    where it closed. Saying the first when you mean the second is a small lie
    that a listener catches immediately, and it is the kind this project keeps
    paying for. One extra call per lookup, cached with the facts.
    """

    name = "Finnhub"
    domain = "markets"
    cost_per_call = 0.0008  # $0.80 per 1,000 on the metered plan
    delayed_seconds = 1200.0
    BASE = "https://finnhub.io/api/v1"

    def diagnose(self) -> tuple[bool, str]:
        if not settings.finnhub_key:
            return False, "FINNHUB_KEY is not set"
        return True, "FINNHUB_KEY present (not verified from this machine)"

    async def verify(self) -> tuple[bool, str]:
        try:
            data = await _json(f"{self.BASE}/quote", {},
                               {"symbol": "AAPL", "token": settings.finnhub_key},
                               settings.live_timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            return False, f"Finnhub did not answer: {type(exc).__name__}: {exc}"
        if not data or data.get("c") in (None, 0):
            return False, f"Finnhub answered without a price: {str(data)[:120]}"
        return True, f"Finnhub accepted the key (AAPL at {data.get('c')})"

    async def resolve(self, brief) -> Optional[Entity]:
        """Symbol lookup through Finnhub's own search - never a guessed ticker.

        A hallucinated symbol does not fail. It returns somebody else's price,
        fresh and confident and wrong, and nothing downstream can catch it.

        Ordinary common stock is preferred over the warrants, units and foreign
        listings that share a prefix, because "Apple" should find AAPL and not
        AAPL.SW.
        """
        subject = live_route.entity_of(brief)
        if not subject:
            return None
        # "Oracle Corporation (NYSE: ORCL)" names its own listing (10.16 #5):
        # it used to be refused as a topic for its brackets. The ticker is
        # still looked up, never trusted - only an exact match is used.
        named = ticker_named(subject)
        if named:
            return await self._resolve_ticker(named)
        if not looks_like_a_listing(subject):
            # "Startup and venture capital industry news" is a topic, not a
            # company. Finnhub refuses it with a 422 - and if it did not, the
            # first row it matched would be somebody else's price (§144).
            log.info("finnhub: %r is a topic, not a listing; not looked up",
                     subject)
            return None
        try:
            data = await _json(f"{self.BASE}/search", {},
                               {"q": subject, "token": settings.finnhub_key},
                               settings.live_timeout_seconds)
        except ProviderHTTPError as exc:
            # A 4xx on a *lookup* is Finnhub saying it cannot search on this
            # text, which is "nothing matching", not a broken provider. A bad
            # key, a spent plan or a rate limit is still a failure.
            if 400 <= exc.status < 500 and exc.status not in (401, 403, 429):
                log.info("finnhub: no listing for %r (%s)", subject, exc)
                return None
            raise
        rows = [row for row in (data or {}).get("result", []) or []
                if quotable(row)]
        best = None
        for row in rows:
            symbol = str(row.get("symbol") or "").strip()
            if not symbol or "." in symbol:
                continue
            kind = str(row.get("type") or "")
            if kind == "Common Stock" and best is None:
                best = row
            elif best is None and not kind:
                best = row
        best = best or (rows[0] if rows else None)
        if not best:
            return None
        symbol = str(best.get("symbol") or "").strip()
        if not symbol:
            return None
        return Entity(domain="markets", provider=self.name, id=symbol,
                      label=str(best.get("description") or symbol).title())

    async def _resolve_ticker(self, ticker: str) -> Optional[Entity]:
        """The listing a subject named by ticker, if Finnhub has exactly it."""
        if refused_today(ticker):
            return None
        try:
            data = await _json(f"{self.BASE}/search", {},
                               {"q": ticker, "token": settings.finnhub_key},
                               settings.live_timeout_seconds)
        except ProviderHTTPError as exc:
            if 400 <= exc.status < 500 and exc.status not in (401, 403, 429):
                return None
            raise
        for row in (data or {}).get("result", []) or []:
            if str(row.get("symbol") or "").strip().upper() == ticker and quotable(row):
                return Entity(domain="markets", provider=self.name, id=ticker,
                              label=str(row.get("description") or ticker).title())
        return None

    async def market_open(self) -> Optional[bool]:
        """Is the US market trading right now? `None` when it cannot be told.

        `None` rather than a guess: an unknown session is reported as "most
        recently" rather than asserted either way.
        """
        try:
            data = await _json(f"{self.BASE}/stock/market-status", {},
                               {"exchange": "US", "token": settings.finnhub_key},
                               settings.live_timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - a missing session is not fatal
            log.info("finnhub: market status unavailable (%s)", exc)
            return None
        value = (data or {}).get("isOpen")
        return bool(value) if isinstance(value, bool) else None

    async def fetch(self, entity: Entity) -> Optional[LiveFacts]:
        # A watchlist symbol the story sweep already quoted (§267): its quote
        # is used when the session it was taken in is still the session now
        # and it passes the same freshness check a live quote must. The
        # session itself is always asked live - one cheap call, run beside
        # research, so it costs the first word nothing - because a quote
        # swept at 9:25 is a close, and at 9:35 it is not.
        #
        # Judged first in the session it was swept in: a quote that would be
        # stale even there is not worth a round trip, so the lookup goes
        # straight to the two requests in parallel, exactly as before - it
        # never puts a second request behind the first under the live
        # timeout. Only a quote that could be used costs the session check,
        # and only a session that changed since costs the quote after it.
        held = swept_quote(entity.id)
        candidate = (self.to_facts(held["data"], entity, held["is_open"])
                     if usable_sweep(held) else None)
        if candidate is not None and candidate.is_fresh():
            is_open = await self.market_open()
            if is_open == held["is_open"]:
                log.info("finnhub: %s from the story sweep's quote", entity.id)
                return candidate
            quote = await _json(f"{self.BASE}/quote", {},
                                {"symbol": entity.id, "token": settings.finnhub_key},
                                settings.live_timeout_seconds)
            return self.to_facts(quote, entity, is_open)
        try:
            quote, is_open = await asyncio.gather(
                _json(f"{self.BASE}/quote", {},
                      {"symbol": entity.id, "token": settings.finnhub_key},
                      settings.live_timeout_seconds),
                self.market_open())
        except ProviderHTTPError as exc:
            if exc.status == 403:
                # The plan does not reach this listing (10.16 #5): not asked
                # again today, so one refusal is one error, not one a question.
                note_refused(entity.id)
            raise
        return self.to_facts(quote, entity, is_open)

    def to_facts(self, data: dict, entity: Entity,
                 is_open: Optional[bool] = None) -> Optional[LiveFacts]:
        price = (data or {}).get("c")
        if price in (None, 0):
            return None

        # The sentence changes with the session, because the fact does. A
        # closing price described as "is trading at" is a small lie a listener
        # catches instantly.
        if is_open is True:
            said = [f"{entity.label} is trading at {price:.2f}."]
        elif is_open is False:
            said = [f"{entity.label} closed at {price:.2f}."]
        else:
            said = [f"{entity.label} was most recently at {price:.2f}."]

        change = (data or {}).get("dp")
        if change is not None:
            way = "up" if change >= 0 else "down"
            said.append(f"That is {way} about {abs(change):.1f} percent on the day.")

        stamp = (data or {}).get("t")
        when = (datetime.fromtimestamp(stamp, tz=timezone.utc)
                if isinstance(stamp, (int, float)) and stamp
                else datetime.now(timezone.utc))
        # A quote is a price, not an event. `unknown` is the honest status and
        # is what stops a market question being answered as a result.
        return LiveFacts(domain="markets", source=self.name, as_of=when,
                         facts=said, status=live_facts.UNKNOWN, entity=entity,
                         delayed_seconds=self.delayed_seconds,
                         # Only Finnhub's own "closed" (§267); an unknown
                         # session keeps the open-market hour.
                         market_closed=is_open is False)


class PolymarketSource(LiveSource):
    """Polymarket. Forecasts - and **never** results.

    The broadest single live integration available: one keyless endpoint
    spanning elections, sport, economics and geopolitics. What it returns is
    what people are *betting*, which is why every fact carries
    `kind="prediction-market"` and why `status` is always `unknown`.

    **The rule that keeps it safe**, and the one somebody will be tempted to
    break: a market price may never close an outcome-dependent question. A
    live in-game win-probability line moves with the score, so it reads like
    the score - *"Chiefs at 94%, so they must be winning"* - and that
    inference is exactly PROBLEMS.md §88 coming back through a side door.
    `unknown` is what forbids it, structurally, rather than by asking nicely.

    **Asked for every question that turns on an outcome** (§211), not only
    elections: `live_facts.forecast` asks it beside the live lookup whenever
    EI says the answer is `outcome_dependent` - a Fed decision, a final, a
    war, a ruling. And it **searches** for the question's subject
    (`/public-search`) rather than scanning the twenty most-traded markets for
    a shared word, which is why it used to match almost nothing.
    """

    name = "Polymarket"
    domain = "elections"
    cost_per_call = 0.0
    delayed_seconds = 0.0
    #: `live_facts.forecast` asks the sources that say this (§211).
    forecasts = True
    #: Markets read out per event: the likeliest few, never the whole board.
    MAX_MARKETS = 3

    def diagnose(self) -> tuple[bool, str]:
        if not settings.polymarket_base:
            return False, "POLYMARKET_BASE is empty"
        return True, "Polymarket public API, keyless (not verified from this machine)"

    async def verify(self) -> tuple[bool, str]:
        try:
            data = await _json(f"{settings.polymarket_base}/markets", {},
                               {"limit": 1, "closed": "false"},
                               settings.live_timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            return False, f"Polymarket did not answer: {type(exc).__name__}: {exc}"
        if not data:
            return False, "Polymarket answered but returned nothing"
        return True, "Polymarket answered"

    async def resolve(self, brief) -> Optional[Entity]:
        subject = live_route.entity_of(brief)
        wanted = subject_words(subject)
        if not wanted:
            return None
        try:
            data = await _json(f"{settings.polymarket_base}/public-search", {},
                               {"q": " ".join(wanted[:6]), "limit_per_type": 10,
                                "events_status": "active",
                                "keep_closed_markets": 0},
                               settings.live_timeout_seconds)
            events = data.get("events") if isinstance(data, dict) else None
        except ProviderHTTPError as exc:
            # Search is the better half; a deployment whose host has no search
            # still gets the old scan rather than nothing. Logged, so a
            # search that quietly stopped working is seen.
            log.info("polymarket: search refused (%s); scanning the busiest "
                     "markets instead", exc.status)
            events = None
        if events is not None:
            best = _best_match(
                ((e, " ".join([str(e.get("title") or "")]
                              + [str(m.get("question") or "")
                                 for m in (e.get("markets") or [])
                                 if isinstance(m, dict)]),
                  _as_float(e.get("volume")) or 0.0)
                 for e in events if isinstance(e, dict)
                 and not e.get("closed") and _open_markets(e)),
                wanted)
            if best is None or not best.get("id"):
                return None
            return Entity(domain=self.domain, provider=self.name,
                          id=f"event:{best['id']}",
                          label=str(best.get("title") or "")[:120])
        data = await _json(f"{settings.polymarket_base}/markets", {},
                           {"limit": 100, "closed": "false", "order": "volume24hr",
                            "ascending": "false"},
                           settings.live_timeout_seconds)
        best = _best_match(
            ((m, str(m.get("question") or ""), _as_float(m.get("volume")) or 0.0)
             for m in (data if isinstance(data, list) else [])
             if isinstance(m, dict)),
            wanted)
        if best is None:
            return None
        return Entity(domain=self.domain, provider=self.name,
                      id=str(best.get("id") or best.get("conditionId") or ""),
                      label=str(best.get("question") or "")[:120])

    async def fetch(self, entity: Entity) -> Optional[LiveFacts]:
        if entity.id.startswith("event:"):
            data = await _json(
                f"{settings.polymarket_base}/events/{entity.id[len('event:'):]}",
                {}, {}, settings.live_timeout_seconds)
        else:
            data = await _json(f"{settings.polymarket_base}/markets/{entity.id}",
                               {}, {}, settings.live_timeout_seconds)
        return self.to_facts(data, entity)

    def to_facts(self, row: dict, entity: Entity) -> Optional[LiveFacts]:
        row = row or {}
        if isinstance(row.get("markets"), list):
            said = self._event_lines(row, entity)
        else:
            said = self._market_lines(row, entity)
        if not said:
            return None
        said.append("That is what people are betting, not a reported result.")
        volume = row.get("volume")
        if volume:
            amount = _as_float(volume)
            said.append("There is real money behind it - about "
                        + (f"{amount:,.0f} dollars" if amount else str(volume))
                        + " traded.")
        return LiveFacts(
            domain=self.domain, source=self.name,
            as_of=datetime.now(timezone.utc), facts=said,
            # Always unknown. A market never establishes that anything
            # happened, and `unknown` is what structurally forbids a result.
            status=live_facts.UNKNOWN, entity=entity,
            kind=live_facts.PREDICTION_MARKET)

    def _market_lines(self, row: dict, entity: Entity) -> list:
        price = market_price(row)
        if price is None:
            return []
        return [f"On prediction markets, {entity.label} is trading around "
                f"{_percent(price)} percent."]

    def _event_lines(self, event: dict, entity: Entity) -> list:
        """An event is several markets - "Fed decision in December" is one
        market per outcome. The likeliest few, each with what it asks."""
        priced = []
        for market in _open_markets(event):
            outcomes = _listed(market.get("outcomes"))
            prices = _listed(market.get("outcomePrices"))
            label = str(market.get("groupItemTitle") or market.get("question") or "").strip()
            if (len(outcomes) == 2 and len(prices) == 2
                    and [o.lower() for o in outcomes] != ["yes", "no"]):
                # A head-to-head market names its two sides.
                try:
                    split = [(str(o), float(p)) for o, p in zip(outcomes, prices)]
                except (TypeError, ValueError):
                    continue
                split.sort(key=lambda pair: -pair[1])
                priced.append((split[0][1], f"{label}: {split[0][0]} around "
                               f"{_percent(split[0][1])} percent, {split[1][0]} "
                               f"around {_percent(split[1][1])} percent"))
                continue
            price = market_price(market)
            if price is not None and label:
                priced.append((price, f"{label}: around {_percent(price)} percent"))
        if not priced:
            return []
        priced.sort(key=lambda pair: -pair[0])
        title = str(event.get("title") or entity.label or "").strip()
        return [f"On Polymarket, \"{title}\" - " + "; ".join(
            text for _p, text in priced[:self.MAX_MARKETS]) + "."]


#: Words that say nothing about which market a question is about.
_MARKET_STOPWORDS = frozenset("""
will what when where which while about after again against before being does
doing from have having into more most much other over same some than that their
them then there these they this those through under until very were what with
would could should next last latest news today week year happen happening
going expected chance odds market markets prediction predictions
""".split())


def subject_words(subject: str) -> list:
    """A brief's subject as the words a market would be named by."""
    out: list = []
    for word in re.findall(r"[^\W_]+", (subject or "").lower()):
        if len(word) > 3 and word not in _MARKET_STOPWORDS and word not in out:
            out.append(word)
    return out


def _best_match(candidates, wanted: list):
    """The candidate whose text shares the most of `wanted`, then the most
    traded - and only one that shares at least two words when the subject has
    two. One shared word is a namesake: "Eagles" alone matches every Eagles
    market and the wrong Eagles."""
    need = min(2, len(wanted))
    best = None
    best_key = None
    for item, text, volume in candidates:
        lowered = text.lower()
        hits = sum(1 for w in wanted if re.search(rf"\b{re.escape(w)}", lowered))
        if hits < need:
            continue
        key = (hits, volume)
        if best_key is None or key > best_key:
            best, best_key = item, key
    return best


def _listed(value) -> list:
    """Gamma sends `outcomes` and `outcomePrices` as JSON *strings*."""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _open_markets(event: dict) -> list:
    return [m for m in (event.get("markets") or [])
            if isinstance(m, dict) and not m.get("closed")
            and m.get("active", True) is not False]


def market_price(row: dict) -> Optional[float]:
    """The YES price of one market, 0-1, or None."""
    outcomes = [str(o).lower() for o in _listed(row.get("outcomes"))]
    prices = _listed(row.get("outcomePrices"))
    if prices:
        index = outcomes.index("yes") if "yes" in outcomes else 0
        try:
            return float(prices[index])
        except (TypeError, ValueError, IndexError):
            pass
    for field_name in ("bestBid", "lastTradePrice", "outcomePrices"):
        value = (row or {}).get(field_name)
        if isinstance(value, list) and value:
            value = value[0]
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _percent(price: float) -> int:
    return max(0, min(100, round(price * 100)))


def _as_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class _QuoteOnlyElections(LiveSource):
    """AP Elections and Decision Desk HQ: named, and priced by quote only.

    Neither publishes API pricing - both are sales-gated - so there is no
    endpoint here to write against and guessing one would be worse than
    nothing. What this does is make the gap *visible*, with the thing a person
    would have to do to close it, rather than leaving elections looking like a
    domain nobody thought about.
    """

    domain = "elections"

    def __init__(self, name: str, needs: str) -> None:
        self.name = name
        self._needs = needs

    def diagnose(self) -> tuple[bool, str]:
        return False, (f"{self.name} is not implemented: it is sales-gated with "
                       f"no public pricing or open endpoint. Needs: {self._needs}")

    async def verify(self) -> tuple[bool, str]:
        return False, self.diagnose()[1]

    async def resolve(self, brief) -> Optional[Entity]:
        return None

    async def fetch(self, entity: Entity) -> Optional[LiveFacts]:
        return None


def _balldontlie_scores():
    import balldontlie
    import balldontlie_scores

    return balldontlie_scores.ScoreBoard(client=balldontlie.shared_client())


BUILDERS["sports"]["balldontlie"] = _balldontlie_scores
BUILDERS["markets"]["finnhub"] = FinnhubSource
BUILDERS["elections"]["polymarket"] = PolymarketSource
BUILDERS["elections"]["ap"] = lambda: _QuoteOnlyElections(
    "AP Elections", "a contract with the Associated Press and their API guide")
BUILDERS["elections"]["ddhq"] = lambda: _QuoteOnlyElections(
    "Decision Desk HQ", "a subscription agreement with DDHQ and an API key")
