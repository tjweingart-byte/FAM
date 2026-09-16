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
import logging
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Sport:
    """One API-Sports product. They are separate APIs wearing one brand.

    Each sport has **its own host and its own response shape** - soccer returns
    `goals: {home, away}` from `/fixtures`, American football returns
    `scores: {home: {total}, away: {total}}` from `/games` - and each has its
    own status vocabulary. Writing one adapter against `v3.football` and
    calling it "sports" is how the Chiefs end up being looked up on a soccer
    endpoint, which is the bug this table exists to prevent.
    """

    key: str
    host: str
    #: `fixtures` for soccer, `games` for everything else.
    path: str
    #: What a score is called out loud in this sport.
    unit: str
    #: Words in a subject that select this sport outright.
    words: tuple
    #: Provider short code -> FAM status. Anything unlisted becomes `unknown`.
    statuses: dict


#: The four with the clearest demand. Adding one is a row here plus its status
#: codes - no new class, because the shape differences are data, not behaviour.
SPORTS = {
    "american-football": Sport(
        key="american-football",
        host="https://v1.american-football.api-sports.io",
        path="games", unit="points",
        words=("nfl", "american football", "touchdown", "quarterback",
               "super bowl", "college football", "ncaaf"),
        statuses={
            "NS": live_facts.SCHEDULED,
            "Q1": live_facts.IN_PROGRESS, "Q2": live_facts.IN_PROGRESS,
            "Q3": live_facts.IN_PROGRESS, "Q4": live_facts.IN_PROGRESS,
            "OT": live_facts.IN_PROGRESS, "HT": live_facts.IN_PROGRESS,
            "FT": live_facts.FINAL, "AOT": live_facts.FINAL,
        }),
    "football": Sport(
        key="football",
        host="https://v3.football.api-sports.io",
        path="fixtures", unit="goals",
        words=("soccer", "premier league", "la liga", "serie a", "bundesliga",
               "champions league", "world cup", "fifa"),
        statuses={
            "NS": live_facts.SCHEDULED, "TBD": live_facts.SCHEDULED,
            "1H": live_facts.IN_PROGRESS, "2H": live_facts.IN_PROGRESS,
            "HT": live_facts.IN_PROGRESS, "ET": live_facts.IN_PROGRESS,
            "P": live_facts.IN_PROGRESS, "LIVE": live_facts.IN_PROGRESS,
            "FT": live_facts.FINAL, "AET": live_facts.FINAL,
            "PEN": live_facts.FINAL,
        }),
    "basketball": Sport(
        key="basketball",
        host="https://v1.basketball.api-sports.io",
        path="games", unit="points",
        words=("nba", "basketball", "wnba", "ncaab"),
        statuses={
            "NS": live_facts.SCHEDULED,
            "Q1": live_facts.IN_PROGRESS, "Q2": live_facts.IN_PROGRESS,
            "Q3": live_facts.IN_PROGRESS, "Q4": live_facts.IN_PROGRESS,
            "OT": live_facts.IN_PROGRESS, "HT": live_facts.IN_PROGRESS,
            "BT": live_facts.IN_PROGRESS,
            "FT": live_facts.FINAL, "AOT": live_facts.FINAL,
        }),
    "baseball": Sport(
        key="baseball",
        host="https://v1.baseball.api-sports.io",
        path="games", unit="runs",
        words=("mlb", "baseball", "world series", "innings"),
        statuses={
            "NS": live_facts.SCHEDULED,
            "IN1": live_facts.IN_PROGRESS, "IN2": live_facts.IN_PROGRESS,
            "IN3": live_facts.IN_PROGRESS, "IN4": live_facts.IN_PROGRESS,
            "IN5": live_facts.IN_PROGRESS, "IN6": live_facts.IN_PROGRESS,
            "IN7": live_facts.IN_PROGRESS, "IN8": live_facts.IN_PROGRESS,
            "IN9": live_facts.IN_PROGRESS, "LIVE": live_facts.IN_PROGRESS,
            "FT": live_facts.FINAL,
        }),
}


def sport_for(subject: str) -> Sport:
    """Which API-Sports product answers this question.

    A named sport wins; otherwise the deployment's configured default.

    **The honest limitation**, and it is the motivating case: "Chiefs game"
    contains no sport word. Team-name routing would need a maintained roster
    of every team in every league, and a stale one sends an NFL question to a
    soccer endpoint - worse than the default. So a deployment says what it
    mostly serves (`API_SPORTS_SPORT`) and explicit words override it.

    Resolving the sport from the team is the obvious next step and wants the
    provider's own team search across sports, which is N requests rather than
    one. Deliberately not guessed at here.
    """
    text = " ".join((subject or "").lower().split())
    for sport in SPORTS.values():
        if any(word in text for word in sport.words):
            return sport
    return SPORTS.get(settings.api_sports_sport, SPORTS["american-football"])


class ApiSportsSource(LiveSource):
    """API-Sports. Self-serve, transparently priced, one product per sport.

    Free 100 req/day to build against; $19/mo for 7,500/day, $29 for 75,000,
    $39 for 150,000. All endpoints on every tier, history limited on free.

    `resolve` uses the provider's own fixtures list rather than any identifier
    a model produced - a hallucinated game id does not fail, it returns
    somebody else's game, and nothing downstream can tell.
    """

    name = "API-Sports"
    domain = "sports"
    cost_per_call = 0.0  # flat-rate plan; the bill is not per call
    delayed_seconds = 0.0

    def diagnose(self) -> tuple[bool, str]:
        if not settings.api_sports_key:
            return False, "API_SPORTS_KEY is not set"
        if settings.api_sports_sport not in SPORTS:
            return False, (f"API_SPORTS_SPORT={settings.api_sports_sport!r} is not "
                           f"one of {', '.join(sorted(SPORTS))}")
        return True, (f"API_SPORTS_KEY present, default sport "
                      f"{settings.api_sports_sport} (not verified from this machine)")

    def _headers(self) -> dict:
        return {"x-apisports-key": settings.api_sports_key}

    async def verify(self) -> tuple[bool, str]:
        sport = SPORTS[settings.api_sports_sport]
        try:
            data = await _json(f"{sport.host}/status", self._headers(), {},
                               settings.live_timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            return False, f"API-Sports did not answer: {type(exc).__name__}: {exc}"
        account = (data or {}).get("response") or {}
        if not account:
            errors = (data or {}).get("errors")
            return False, f"API-Sports rejected the request: {errors or 'unreadable'}"
        requests = (account.get("requests") or {})
        return True, (f"API-Sports accepted the key on {sport.key}: "
                      f"{requests.get('current', '?')}/{requests.get('limit_day', '?')} "
                      f"requests used today")

    async def resolve(self, brief) -> Optional[Entity]:
        subject = (getattr(brief, "subject", "") or getattr(brief, "query", "")).strip()
        if not subject:
            return None
        sport = sport_for(subject)
        data = await _json(f"{sport.host}/{sport.path}", self._headers(),
                           {"live": "all"}, settings.live_timeout_seconds)

        wanted = {w for w in subject.lower().split() if len(w) > 3}
        for row in (data or {}).get("response", []) or []:
            names = " ".join(self._team_names(row)).lower()
            if wanted and any(word in names for word in wanted):
                home, away = self._team_names(row)
                return Entity(
                    domain="sports", provider=self.name,
                    # The sport rides in the id, because a bare game id is
                    # meaningless without knowing which API issued it - and
                    # `fetch` gets only the entity.
                    id=f"{sport.key}:{self._game_id(row)}",
                    label=f"{home} v {away}")
        return None

    async def fetch(self, entity: Entity) -> Optional[LiveFacts]:
        sport_key, _, game_id = entity.id.partition(":")
        sport = SPORTS.get(sport_key)
        if sport is None or not game_id:
            return None
        data = await _json(f"{sport.host}/{sport.path}", self._headers(),
                           {"id": game_id}, settings.live_timeout_seconds)
        rows = (data or {}).get("response", []) or []
        if not rows:
            return None
        return self.to_facts(rows[0], entity, sport)

    # --- shape readers ----------------------------------------------------
    # Small and separate because this is where the sports genuinely differ,
    # and a single branching `to_facts` is where that difference gets lost.
    @staticmethod
    def _team_names(row: dict) -> tuple:
        teams = (row or {}).get("teams") or {}
        return (str((teams.get("home") or {}).get("name", "")),
                str((teams.get("away") or {}).get("name", "")))

    @staticmethod
    def _game_id(row: dict) -> str:
        for holder in ("game", "fixture"):
            block = (row or {}).get(holder) or {}
            if block.get("id") is not None:
                return str(block["id"])
        return str((row or {}).get("id", ""))

    @staticmethod
    def _status_block(row: dict) -> dict:
        for holder in ("game", "fixture"):
            block = (row or {}).get(holder) or {}
            if block.get("status"):
                return block["status"] or {}
        return (row or {}).get("status") or {}

    @staticmethod
    def _score(row: dict) -> tuple:
        """Points for each side, whichever shape this sport uses."""
        goals = (row or {}).get("goals")
        if isinstance(goals, dict) and goals.get("home") is not None:
            return goals.get("home"), goals.get("away")
        scores = (row or {}).get("scores") or {}
        home, away = scores.get("home"), scores.get("away")
        if isinstance(home, dict):
            home, away = home.get("total"), (away or {}).get("total")
        return home, away

    def to_facts(self, row: dict, entity: Entity,
                 sport: Optional[Sport] = None) -> Optional[LiveFacts]:
        """One game row -> LiveFacts. Split out so tests can drive it."""
        sport = sport or SPORTS[settings.api_sports_sport]
        status_block = self._status_block(row)
        short = str(status_block.get("short") or "").upper()
        # Mapped at the boundary. Unrecognised becomes `unknown`, never a
        # guess - so a provider that changes its codes degrades to silence
        # rather than to a confident wrong tense.
        status = sport.statuses.get(short, live_facts.UNKNOWN)

        home, away = self._team_names(row)
        home = home or "the home side"
        away = away or "the away side"
        hs, as_ = self._score(row)

        said: list = []
        if hs is not None and as_ is not None:
            first, second, lead, trail = home, away, hs, as_
            if as_ > hs:
                first, second, lead, trail = away, home, as_, hs
            if lead == trail:
                said.append(f"{home} and {away} are level at {lead} {sport.unit} each.")
            elif status == live_facts.FINAL:
                said.append(f"{first} beat {second} {lead} to {trail}.")
            else:
                said.append(f"{first} lead {second} {lead} to {trail}.")

        if status == live_facts.IN_PROGRESS:
            where = (status_block.get("long") or status_block.get("timer")
                     or status_block.get("elapsed"))
            if where:
                said.append(f"They are in {where}." if isinstance(where, str)
                            else f"About {where} minutes have been played.")
        elif status == live_facts.SCHEDULED:
            said.append(f"{home} and {away} have not started yet.")

        if not said:
            return None
        return LiveFacts(domain="sports", source=self.name,
                         as_of=datetime.now(timezone.utc), facts=said,
                         status=status, entity=entity)


class SportsDataIOSource(LiveSource):
    """SportsDataIO. Deeper US coverage, including player-level statistics.

    The reason to reach for this over API-Sports is the motivating failure:
    *"Mahomes had a great game"* needs player data, and API-Sports does not
    carry it for the NFL. Pricing is sales-gated above roughly a $99-149/mo
    "Discovery Lab" tier, so this is the upgrade rather than the start.

    **Its free key returns deliberately scrambled data**, which is a trap worth
    naming: a free key looks like it works. `verify()` cannot tell the
    difference, so a deployment on a trial key will produce confident nonsense.
    Do not run this in production without a paid key.
    """

    name = "SportsDataIO"
    domain = "sports"
    cost_per_call = 0.0
    delayed_seconds = 0.0
    BASE = "https://api.sportsdata.io/v3/nfl/scores/json"
    STATUS = {
        "Scheduled": live_facts.SCHEDULED, "InProgress": live_facts.IN_PROGRESS,
        "Final": live_facts.FINAL, "F/OT": live_facts.FINAL,
        "Suspended": live_facts.IN_PROGRESS, "Halftime": live_facts.IN_PROGRESS,
    }

    def diagnose(self) -> tuple[bool, str]:
        if not settings.sportsdataio_key:
            return False, "SPORTSDATAIO_KEY is not set"
        return True, ("SPORTSDATAIO_KEY present (not verified from this machine; "
                      "note a free trial key returns scrambled data)")

    def _headers(self) -> dict:
        return {"Ocp-Apim-Subscription-Key": settings.sportsdataio_key}

    async def verify(self) -> tuple[bool, str]:
        try:
            await _json(f"{self.BASE}/AreAnyGamesInProgress", self._headers(),
                        {}, settings.live_timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            return False, f"SportsDataIO did not answer: {type(exc).__name__}: {exc}"
        return True, ("SportsDataIO accepted the key - but this cannot tell a "
                      "paid key from a trial key returning scrambled data")

    async def resolve(self, brief) -> Optional[Entity]:
        subject = (getattr(brief, "subject", "") or getattr(brief, "query", "")).strip()
        if not subject:
            return None
        data = await _json(f"{self.BASE}/ScoresByWeek/{_nfl_season()}/current",
                           self._headers(), {}, settings.live_timeout_seconds)
        wanted = {w for w in subject.lower().split() if len(w) > 3}
        for row in (data if isinstance(data, list) else []):
            names = f"{row.get('HomeTeam', '')} {row.get('AwayTeam', '')}".lower()
            if wanted and any(word in names for word in wanted):
                return Entity(domain="sports", provider=self.name,
                              id=str(row.get("GameKey") or row.get("ScoreID") or ""),
                              label=f"{row.get('AwayTeam')} at {row.get('HomeTeam')}")
        return None

    async def fetch(self, entity: Entity) -> Optional[LiveFacts]:
        data = await _json(f"{self.BASE}/ScoresByWeek/{_nfl_season()}/current",
                           self._headers(), {}, settings.live_timeout_seconds)
        for row in (data if isinstance(data, list) else []):
            if str(row.get("GameKey") or row.get("ScoreID") or "") == entity.id:
                return self.to_facts(row, entity)
        return None

    def to_facts(self, row: dict, entity: Entity) -> Optional[LiveFacts]:
        status = self.STATUS.get(str(row.get("Status") or ""), live_facts.UNKNOWN)
        home, away = str(row.get("HomeTeam") or ""), str(row.get("AwayTeam") or "")
        hs, as_ = row.get("HomeScore"), row.get("AwayScore")
        said: list = []
        if hs is not None and as_ is not None:
            first, second, lead, trail = home, away, hs, as_
            if as_ > hs:
                first, second, lead, trail = away, home, as_, hs
            if lead == trail:
                said.append(f"{home} and {away} are level at {lead} points each.")
            elif status == live_facts.FINAL:
                said.append(f"{first} beat {second} {lead} to {trail}.")
            else:
                said.append(f"{first} lead {second} {lead} to {trail}.")
        quarter, clock = row.get("Quarter"), row.get("TimeRemaining")
        if status == live_facts.IN_PROGRESS and quarter:
            said.append(f"It is the {quarter} quarter"
                        + (f", {clock} left." if clock else "."))
        if not said:
            return None
        return LiveFacts(domain="sports", source=self.name,
                         as_of=datetime.now(timezone.utc), facts=said,
                         status=status, entity=entity)


def _nfl_season() -> str:
    """The NFL season a date belongs to. A January game is last season's."""
    now = datetime.now(timezone.utc)
    return str(now.year if now.month >= 3 else now.year - 1)


class FinnhubSource(LiveSource):
    """Finnhub quotes. The most generous free tier in market data.

    60 requests a minute free against Alpha Vantage's 25 a *day*, and paid
    starts at $11.99/mo against $49.99. The one catch is licensing rather than
    engineering: the free tier is personal/non-commercial, so a monetised app
    needs a paid plan - which is the only real argument for Alpha Vantage.

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
        subject = (getattr(brief, "subject", "") or getattr(brief, "query", "")).strip()
        if not subject:
            return None
        data = await _json(f"{self.BASE}/search", {},
                           {"q": subject, "token": settings.finnhub_key},
                           settings.live_timeout_seconds)
        rows = (data or {}).get("result", []) or []
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
        quote, is_open = await asyncio.gather(
            _json(f"{self.BASE}/quote", {},
                  {"symbol": entity.id, "token": settings.finnhub_key},
                  settings.live_timeout_seconds),
            self.market_open())
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
                         delayed_seconds=self.delayed_seconds)


class AlphaVantageSource(LiveSource):
    """Alpha Vantage. The alternative when Finnhub's terms do not fit.

    Free tier is 25 requests a *day* against Finnhub's 60 a minute, and paid
    starts at $49.99/mo against Finnhub's $11.99 - so this is the second
    choice on both axes. It is here because Finnhub's free tier is
    personal/non-commercial, and that is a licensing question rather than a
    technical one.
    """

    name = "Alpha Vantage"
    domain = "markets"
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
            kind=live_facts.PREDICTION_MARKET)


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
