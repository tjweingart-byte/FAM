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

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import live_facts
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
        subject = (getattr(brief, "subject", "") or getattr(brief, "query", "")).lower()
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


def configured() -> dict:
    """Which provider each domain is set to. Empty string means none."""
    return {
        "sports": settings.live_sports_provider,
        "markets": settings.live_markets_provider,
        "elections": settings.live_elections_provider,
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
    return {"configured": configured(), "registered": registered,
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


async def _json(url: str, headers: dict, params: dict, timeout: float) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()


class ApiSportsSource(LiveSource):
    """API-Sports (api-football / api-american-football and siblings).

    Self-serve and transparently priced, which is why it is the default sports
    adapter: free 100 req/day to build against, then $19/mo for 7,500/day.

    `resolve` uses the provider's own fixtures endpoint rather than any
    identifier a model produced - a hallucinated game id does not fail, it
    returns somebody else's game, and nothing downstream can tell.
    """

    name = "API-Sports"
    domain = "sports"
    cost_per_call = 0.0  # flat-rate plan; the bill is not per call
    delayed_seconds = 0.0
    BASE = "https://v3.football.api-sports.io"

    def diagnose(self) -> tuple[bool, str]:
        if not settings.api_sports_key:
            return False, "API_SPORTS_KEY is not set"
        return True, "API_SPORTS_KEY present (not verified from this machine)"

    def _headers(self) -> dict:
        return {"x-apisports-key": settings.api_sports_key}

    async def verify(self) -> tuple[bool, str]:
        try:
            data = await _json(f"{self.BASE}/status", self._headers(), {},
                               settings.live_timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            return False, f"API-Sports did not answer: {type(exc).__name__}: {exc}"
        account = (data or {}).get("response", {})
        if not account:
            return False, "API-Sports answered but the response was unreadable"
        return True, f"API-Sports accepted the key: {account}"

    async def resolve(self, brief) -> Optional[Entity]:
        subject = (getattr(brief, "subject", "") or getattr(brief, "query", "")).strip()
        if not subject:
            return None
        data = await _json(f"{self.BASE}/fixtures", self._headers(),
                           {"live": "all"}, settings.live_timeout_seconds)
        wanted = {w for w in subject.lower().split() if len(w) > 3}
        for row in (data or {}).get("response", []) or []:
            teams = (row.get("teams") or {})
            names = " ".join(
                str((teams.get(side) or {}).get("name", "")).lower()
                for side in ("home", "away"))
            if wanted and any(word in names for word in wanted):
                fixture = row.get("fixture") or {}
                return Entity(
                    domain="sports", provider=self.name,
                    id=str(fixture.get("id", "")),
                    label=" v ".join(
                        str((teams.get(s) or {}).get("name", "")) for s in ("home", "away")))
        return None

    #: API-Sports' own status short codes, mapped at the boundary. Anything
    #: unlisted becomes `unknown`, which is the state in which no result may
    #: be spoken - never a guess.
    STATUS = {
        "NS": live_facts.SCHEDULED, "TBD": live_facts.SCHEDULED,
        "1H": live_facts.IN_PROGRESS, "2H": live_facts.IN_PROGRESS,
        "HT": live_facts.IN_PROGRESS, "ET": live_facts.IN_PROGRESS,
        "P": live_facts.IN_PROGRESS, "LIVE": live_facts.IN_PROGRESS,
        "FT": live_facts.FINAL, "AET": live_facts.FINAL, "PEN": live_facts.FINAL,
    }

    async def fetch(self, entity: Entity) -> Optional[LiveFacts]:
        data = await _json(f"{self.BASE}/fixtures", self._headers(),
                           {"id": entity.id}, settings.live_timeout_seconds)
        rows = (data or {}).get("response", []) or []
        if not rows:
            return None
        return self.to_facts(rows[0], entity)

    def to_facts(self, row: dict, entity: Entity) -> Optional[LiveFacts]:
        """One fixture row -> LiveFacts. Split out so tests can drive it."""
        fixture = row.get("fixture") or {}
        teams = row.get("teams") or {}
        goals = row.get("goals") or {}
        short = str(((fixture.get("status") or {}).get("short") or "")).upper()
        status = self.STATUS.get(short, live_facts.UNKNOWN)

        home = str((teams.get("home") or {}).get("name", "")) or "the home side"
        away = str((teams.get("away") or {}).get("name", "")) or "the away side"
        said: list = []
        # Spoken sentences, never a scoreline in a shape a voice cannot read.
        if goals.get("home") is not None and goals.get("away") is not None:
            verb = "beat" if status == live_facts.FINAL else "lead"
            first, second = (home, away)
            hs, as_ = goals["home"], goals["away"]
            if as_ > hs:
                first, second, hs, as_ = away, home, as_, hs
            if hs == as_:
                said.append(f"{home} and {away} are level at {hs} apiece.")
            else:
                said.append(f"{first} {verb} {second} {hs} to {as_}.")
        elapsed = (fixture.get("status") or {}).get("elapsed")
        if status == live_facts.IN_PROGRESS and elapsed:
            said.append(f"About {elapsed} minutes have been played.")
        if status == live_facts.SCHEDULED:
            said.append(f"{home} and {away} have not kicked off yet.")
        if not said:
            return None
        return LiveFacts(domain="sports", source=self.name,
                         as_of=datetime.now(timezone.utc), facts=said,
                         status=status, entity=entity)


class SportsDataIOSource(ApiSportsSource):
    """SportsDataIO. Deeper US coverage, including player-level statistics.

    The reason to reach for this over API-Sports is the motivating failure:
    *"Mahomes had a great game"* needs player data, which API-Sports' football
    endpoints do not carry for the NFL. Pricing is sales-gated above a
    ~$99-149/mo "Discovery Lab" tier, so this is the upgrade rather than
    the start.

    Subclasses the API-Sports adapter only for the spoken-sentence shaping in
    `to_facts`; the endpoints and the status vocabulary are its own.
    """

    name = "SportsDataIO"
    BASE = "https://api.sportsdata.io/v3/nfl/scores/json"
    STATUS = {
        "Scheduled": live_facts.SCHEDULED, "InProgress": live_facts.IN_PROGRESS,
        "Final": live_facts.FINAL, "F/OT": live_facts.FINAL,
    }

    def diagnose(self) -> tuple[bool, str]:
        if not settings.sportsdataio_key:
            return False, "SPORTSDATAIO_KEY is not set"
        return True, "SPORTSDATAIO_KEY present (not verified from this machine)"

    def _headers(self) -> dict:
        return {"Ocp-Apim-Subscription-Key": settings.sportsdataio_key}

    async def verify(self) -> tuple[bool, str]:
        try:
            await _json(f"{self.BASE}/AreAnyGamesInProgress", self._headers(),
                        {}, settings.live_timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            return False, f"SportsDataIO did not answer: {type(exc).__name__}: {exc}"
        return True, "SportsDataIO accepted the key"


class FinnhubSource(LiveSource):
    """Finnhub quotes. The most generous free tier in market data.

    60 requests a minute free, and **delayed by about twenty minutes** on that
    tier - which is fine here and not fine elsewhere: `delayed_seconds` makes
    the prompt say "delayed by twenty minutes" rather than "current". A
    provider that is honestly late is usable; one that is quietly late is the
    failure this whole subsystem exists to prevent.
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
            data = await _json(f"{self.BASE}/quote",
                               {}, {"symbol": "AAPL", "token": settings.finnhub_key},
                               settings.live_timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            return False, f"Finnhub did not answer: {type(exc).__name__}: {exc}"
        if not data or data.get("c") in (None, 0):
            return False, "Finnhub answered but returned no price"
        return True, f"Finnhub accepted the key (AAPL at {data.get('c')})"

    async def resolve(self, brief) -> Optional[Entity]:
        """Symbol lookup through Finnhub's own search - never a guessed ticker.

        A hallucinated symbol returns somebody else's price, fresh and
        confident and wrong, which nothing downstream can catch.
        """
        subject = (getattr(brief, "subject", "") or getattr(brief, "query", "")).strip()
        if not subject:
            return None
        data = await _json(f"{self.BASE}/search", {},
                           {"q": subject, "token": settings.finnhub_key},
                           settings.live_timeout_seconds)
        for row in (data or {}).get("result", []) or []:
            symbol = str(row.get("symbol") or "").strip()
            if symbol and "." not in symbol:
                return Entity(domain="markets", provider=self.name, id=symbol,
                              label=str(row.get("description") or symbol))
        return None

    async def fetch(self, entity: Entity) -> Optional[LiveFacts]:
        data = await _json(f"{self.BASE}/quote", {},
                           {"symbol": entity.id, "token": settings.finnhub_key},
                           settings.live_timeout_seconds)
        return self.to_facts(data, entity)

    def to_facts(self, data: dict, entity: Entity) -> Optional[LiveFacts]:
        price = (data or {}).get("c")
        if price in (None, 0):
            return None
        change = (data or {}).get("dp")
        said = [f"{entity.label} is trading at {price:.2f}."]
        if change is not None:
            way = "up" if change >= 0 else "down"
            said.append(f"That is {way} about {abs(change):.1f} percent on the day.")
        stamp = (data or {}).get("t")
        when = (datetime.fromtimestamp(stamp, tz=timezone.utc)
                if isinstance(stamp, (int, float)) and stamp
                else datetime.now(timezone.utc))
        # A quote is a price, not an event: `unknown` is the honest status,
        # and it is what stops a market question being answered as a result.
        return LiveFacts(domain="markets", source=self.name, as_of=when,
                         facts=said, status=live_facts.UNKNOWN, entity=entity,
                         delayed_seconds=self.delayed_seconds)


class AlphaVantageSource(FinnhubSource):
    """Alpha Vantage. The alternative when Finnhub's terms do not fit.

    Free tier is 25 requests a *day* against Finnhub's 60 a minute, and paid
    starts at $49.99/mo against Finnhub's $11.99 - so this is the second
    choice on both axes. It is here because Finnhub's free tier is
    personal/non-commercial, and that is a licensing question rather than a
    technical one.
    """

    name = "Alpha Vantage"
    cost_per_call = 0.0
    delayed_seconds = 900.0
    BASE = "https://www.alphavantage.co"

    def diagnose(self) -> tuple[bool, str]:
        if not settings.alpha_vantage_key:
            return False, "ALPHA_VANTAGE_KEY is not set"
        return True, "ALPHA_VANTAGE_KEY present (not verified from this machine)"

    async def verify(self) -> tuple[bool, str]:
        try:
            data = await _json(f"{self.BASE}/query", {},
                               {"function": "GLOBAL_QUOTE", "symbol": "AAPL",
                                "apikey": settings.alpha_vantage_key},
                               settings.live_timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            return False, f"Alpha Vantage did not answer: {type(exc).__name__}: {exc}"
        if "Global Quote" not in (data or {}):
            return False, f"Alpha Vantage answered without a quote: {str(data)[:120]}"
        return True, "Alpha Vantage accepted the key"

    async def resolve(self, brief) -> Optional[Entity]:
        subject = (getattr(brief, "subject", "") or getattr(brief, "query", "")).strip()
        if not subject:
            return None
        data = await _json(f"{self.BASE}/query", {},
                           {"function": "SYMBOL_SEARCH", "keywords": subject,
                            "apikey": settings.alpha_vantage_key},
                           settings.live_timeout_seconds)
        for row in (data or {}).get("bestMatches", []) or []:
            symbol = str(row.get("1. symbol") or "").strip()
            if symbol:
                return Entity(domain="markets", provider=self.name, id=symbol,
                              label=str(row.get("2. name") or symbol))
        return None

    async def fetch(self, entity: Entity) -> Optional[LiveFacts]:
        data = await _json(f"{self.BASE}/query", {},
                           {"function": "GLOBAL_QUOTE", "symbol": entity.id,
                            "apikey": settings.alpha_vantage_key},
                           settings.live_timeout_seconds)
        quote = (data or {}).get("Global Quote") or {}
        try:
            price = float(quote.get("05. price"))
        except (TypeError, ValueError):
            return None
        said = [f"{entity.label} is trading at {price:.2f}."]
        percent = str(quote.get("10. change percent") or "").strip().rstrip("%")
        try:
            change = float(percent)
            way = "up" if change >= 0 else "down"
            said.append(f"That is {way} about {abs(change):.1f} percent on the day.")
        except (TypeError, ValueError):
            pass
        return LiveFacts(domain="markets", source=self.name,
                         as_of=datetime.now(timezone.utc), facts=said,
                         status=live_facts.UNKNOWN, entity=entity,
                         delayed_seconds=self.delayed_seconds)


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
    """

    name = "Polymarket"
    domain = "elections"
    cost_per_call = 0.0
    delayed_seconds = 0.0

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
        subject = (getattr(brief, "subject", "") or getattr(brief, "query", "")).strip()
        if not subject:
            return None
        data = await _json(f"{settings.polymarket_base}/markets", {},
                           {"limit": 20, "closed": "false", "order": "volume",
                            "ascending": "false"},
                           settings.live_timeout_seconds)
        wanted = {w for w in subject.lower().split() if len(w) > 3}
        for row in (data if isinstance(data, list) else []):
            question = str(row.get("question") or "").lower()
            if wanted and any(word in question for word in wanted):
                return Entity(domain=self.domain, provider=self.name,
                              id=str(row.get("id") or row.get("conditionId") or ""),
                              label=str(row.get("question") or "")[:120])
        return None

    async def fetch(self, entity: Entity) -> Optional[LiveFacts]:
        data = await _json(f"{settings.polymarket_base}/markets/{entity.id}",
                           {}, {}, settings.live_timeout_seconds)
        return self.to_facts(data, entity)

    def to_facts(self, row: dict, entity: Entity) -> Optional[LiveFacts]:
        price = None
        for field_name in ("bestBid", "lastTradePrice", "outcomePrices"):
            value = (row or {}).get(field_name)
            if isinstance(value, list) and value:
                value = value[0]
            try:
                price = float(value)
                break
            except (TypeError, ValueError):
                continue
        if price is None:
            return None
        percent = max(0, min(100, round(price * 100)))
        said = [
            f"On prediction markets, {entity.label} is trading around "
            f"{percent} percent.",
            "That is what people are betting, not a reported result.",
        ]
        volume = (row or {}).get("volume")
        if volume:
            said.append(f"There is real money behind it - about {volume} traded.")
        return LiveFacts(
            domain=self.domain, source=self.name,
            as_of=datetime.now(timezone.utc), facts=said,
            # Always unknown. A market never establishes that anything
            # happened, and `unknown` is what structurally forbids a result.
            status=live_facts.UNKNOWN, entity=entity,
            kind="prediction-market")


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


BUILDERS["sports"]["api-sports"] = ApiSportsSource
BUILDERS["sports"]["sportsdataio"] = SportsDataIOSource
BUILDERS["markets"]["finnhub"] = FinnhubSource
BUILDERS["markets"]["alpha-vantage"] = AlphaVantageSource
BUILDERS["elections"]["polymarket"] = PolymarketSource
BUILDERS["elections"]["ap"] = lambda: _QuoteOnlyElections(
    "AP Elections", "a contract with the Associated Press and their API guide")
BUILDERS["elections"]["ddhq"] = lambda: _QuoteOnlyElections(
    "Decision Desk HQ", "a subscription agreement with DDHQ and an API key")
