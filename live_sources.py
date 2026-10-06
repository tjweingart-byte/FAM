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
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Optional

import live_facts
import named_slots
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


#: The provider a domain gets when nobody named one and its credential is
#: present (§135). The owner's rule is that an episode's information comes
#: from Exa, GDELT, API-Sports, Finnhub and Polymarket - so a deployment that
#: holds an API-Sports or Finnhub key and forgot the second line naming it was
#: quietly answering live questions from articles alone, which is the "a
#: capability configured and not used" shape §119 was paid for. The key is
#: the statement of intent; a separate switch saying so again is a second
#: place to forget. `none` still switches a domain off on purpose.
#:
#: Elections is not here: Polymarket is keyless, so there is no credential
#: whose presence says anything, and `render.yaml` names it explicitly.
DERIVED_FROM_KEY = {
    "sports": ("api-sports", "api_sports_key"),
    "markets": ("finnhub", "finnhub_key"),
}


def _provider(domain: str, named: str) -> str:
    named = (named or "").strip().lower()
    if named in ("none", "off", "0"):
        return ""
    if named:
        return named
    derived = DERIVED_FROM_KEY.get(domain)
    if derived and getattr(settings, derived[1], ""):
        return derived[0]
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
            # Shared by the story sweep and episode lookups (§135).
            "api_sports_budget": API_SPORTS_BUDGET.as_dict(),
            # Per sport since §180: each is its own plan and its own day.
            "api_sports_budgets": budgets_report(),
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


async def _json(url: str, headers: dict, params: dict, timeout: float) -> dict:
    import provider_usage

    # Whose allowance this request spends, for the admin page (§179). Every
    # live and story provider comes through here, so the count is taken once.
    provider = provider_usage.provider_for_host(httpx.URL(url).host)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.get(url, headers=headers, params=params)
        except Exception:
            provider_usage.record(provider, ok=False,
                                  detail=sport_of_url(url) if provider == "api_sports" else "")
            raise
        provider_usage.record(provider, ok=response.is_success,
                              detail=sport_of_url(url) if provider == "api_sports" else "")
        if provider == "api_sports":
            _note_quota(url, response.headers)
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
    #: The league whose teams' seasons are read off the provider's own
    #: schedule (9.30 #3): records, last and next games, computed in code.
    #: Empty for a sport this is not built for yet.
    team_league: str = ""
    #: Where the league's days are counted, and what that zone is called out
    #: loud. A Sunday night game kicks off on Monday in UTC, so a day said
    #: from UTC is the wrong day for every prime-time game.
    local_zone: str = ""
    zone_said: str = ""
    #: What one of its events is: `match` (two sides, home and away),
    #: `fight` (two fighters) or `race` (a field, read off rankings).
    kind: str = "match"
    #: What an event is called out loud.
    noun: str = "game"
    #: How this API names a season (§180): `start` is the year it starts
    #: (NFL, NHL, soccer), `span` is "2025-2026" (basketball), `calendar` is
    #: the year it is played in (MLB, F1, AFL, MMA).
    season_style: str = "start"
    #: The month a new season starts in, for `start` and `span`.
    season_month: int = 8
    #: What the listener would call it.
    label: str = ""


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
        },
        # API-Sports' NFL.
        team_league="1", local_zone="America/New_York", zone_said="Eastern",
        season_month=9),
    "football": Sport(
        key="football",
        host="https://v3.football.api-sports.io",
        path="fixtures", unit="goals",
        words=("soccer", "premier league", "la liga", "serie a", "bundesliga",
               "champions league", "world cup", "fifa", "ligue 1", "mls",
               "europa league", "fa cup"),
        statuses={
            "NS": live_facts.SCHEDULED, "TBD": live_facts.SCHEDULED,
            "1H": live_facts.IN_PROGRESS, "2H": live_facts.IN_PROGRESS,
            "HT": live_facts.IN_PROGRESS, "ET": live_facts.IN_PROGRESS,
            "P": live_facts.IN_PROGRESS, "LIVE": live_facts.IN_PROGRESS,
            "FT": live_facts.FINAL, "AET": live_facts.FINAL,
            "PEN": live_facts.FINAL,
        },
        label="Soccer"),
    "basketball": Sport(
        key="basketball",
        host="https://v1.basketball.api-sports.io",
        path="games", unit="points",
        words=("nba", "basketball", "wnba", "ncaab", "euroleague"),
        statuses={
            "NS": live_facts.SCHEDULED,
            "Q1": live_facts.IN_PROGRESS, "Q2": live_facts.IN_PROGRESS,
            "Q3": live_facts.IN_PROGRESS, "Q4": live_facts.IN_PROGRESS,
            "OT": live_facts.IN_PROGRESS, "HT": live_facts.IN_PROGRESS,
            "BT": live_facts.IN_PROGRESS,
            "FT": live_facts.FINAL, "AOT": live_facts.FINAL,
        },
        # API-Basketball's NBA; its seasons are named "2025-2026".
        team_league="12", local_zone="America/New_York", zone_said="Eastern",
        season_style="span", season_month=10, label="Basketball"),
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
        },
        # API-Baseball's MLB, played inside one calendar year.
        team_league="1", local_zone="America/New_York", zone_said="Eastern",
        season_style="calendar", label="Baseball"),
    # --- §180: the rest of API-Sports' products the owner asked for -------
    # Status codes are the providers' documented short codes as far as they
    # could be read from here (the hosts are blocked in the build container);
    # any code not listed becomes `unknown`, which says nothing - so a wrong
    # guess costs a silent game, never a wrong one.
    "hockey": Sport(
        key="hockey",
        host="https://v1.hockey.api-sports.io",
        path="games", unit="goals",
        words=("nhl", "hockey", "khl", "stanley cup", "ahl"),
        statuses={
            "NS": live_facts.SCHEDULED,
            "P1": live_facts.IN_PROGRESS, "P2": live_facts.IN_PROGRESS,
            "P3": live_facts.IN_PROGRESS, "OT": live_facts.IN_PROGRESS,
            "PT": live_facts.IN_PROGRESS, "BT": live_facts.IN_PROGRESS,
            "FT": live_facts.FINAL, "AOT": live_facts.FINAL,
            "AP": live_facts.FINAL,
        },
        # API-Hockey's NHL; its seasons are named by the year they start.
        team_league="57", local_zone="America/New_York", zone_said="Eastern",
        season_style="start", season_month=9, label="Hockey"),
    "rugby": Sport(
        key="rugby",
        host="https://v1.rugby.api-sports.io",
        path="games", unit="points",
        words=("rugby", "six nations", "top 14", "super rugby", "nrl",
               "premiership rugby", "all blacks", "springboks"),
        statuses={
            "NS": live_facts.SCHEDULED,
            "1H": live_facts.IN_PROGRESS, "2H": live_facts.IN_PROGRESS,
            "HT": live_facts.IN_PROGRESS, "ET": live_facts.IN_PROGRESS,
            "BT": live_facts.IN_PROGRESS, "PT": live_facts.IN_PROGRESS,
            "FT": live_facts.FINAL, "AET": live_facts.FINAL,
        },
        label="Rugby"),
    "volleyball": Sport(
        key="volleyball",
        host="https://v1.volleyball.api-sports.io",
        path="games", unit="sets",
        words=("volleyball", "superlega", "plusliga"),
        statuses={
            "NS": live_facts.SCHEDULED,
            "S1": live_facts.IN_PROGRESS, "S2": live_facts.IN_PROGRESS,
            "S3": live_facts.IN_PROGRESS, "S4": live_facts.IN_PROGRESS,
            "S5": live_facts.IN_PROGRESS,
            "FT": live_facts.FINAL,
        },
        label="Volleyball"),
    "afl": Sport(
        key="afl",
        host="https://v1.afl.api-sports.io",
        path="games", unit="points",
        words=("afl", "australian football", "aussie rules",
               "australian rules"),
        statuses={
            "NS": live_facts.SCHEDULED,
            "Q1": live_facts.IN_PROGRESS, "Q2": live_facts.IN_PROGRESS,
            "Q3": live_facts.IN_PROGRESS, "Q4": live_facts.IN_PROGRESS,
            "QT": live_facts.IN_PROGRESS, "HT": live_facts.IN_PROGRESS,
            "BT": live_facts.IN_PROGRESS,
            "FT": live_facts.FINAL,
        },
        season_style="calendar", label="AFL"),
    "formula-1": Sport(
        key="formula-1",
        host="https://v1.formula-1.api-sports.io",
        path="races", unit="",
        words=("formula 1", "formula one", "f1", "grand prix"),
        # Races carry a word, not a code.
        statuses={
            "SCHEDULED": live_facts.SCHEDULED,
            "LIVE": live_facts.IN_PROGRESS,
            "COMPLETED": live_facts.FINAL,
        },
        kind="race", noun="race", season_style="calendar",
        label="Formula 1"),
    "mma": Sport(
        key="mma",
        host="https://v1.mma.api-sports.io",
        path="fights", unit="",
        words=("mma", "ufc", "bellator", "pfl", "mixed martial arts"),
        statuses={
            "NS": live_facts.SCHEDULED,
            "IN": live_facts.IN_PROGRESS, "LIVE": live_facts.IN_PROGRESS,
            "EOR": live_facts.IN_PROGRESS,
            "FT": live_facts.FINAL,
        },
        kind="fight", noun="fight", season_style="calendar", label="MMA"),
}

#: What an admin page and a log call each product.
for _key, _sport in list(SPORTS.items()):
    if not _sport.label:
        SPORTS[_key] = replace(_sport, label={
            "american-football": "American football"}.get(_key, _key.title()))
del _key, _sport


def season_of(sport: "Sport", now: Optional[datetime] = None) -> str:
    """The season this API is in today, named the way it names seasons."""
    now = now or datetime.now(timezone.utc)
    if sport.season_style == "calendar":
        return str(now.year)
    # A season that starts in the autumn belongs, until next summer, to the
    # year it started. From two months before its opening month the coming
    # season is the one schedules are published for, so it counts from then.
    start = now.year if now.month >= sport.season_month - 2 else now.year - 1
    if sport.season_style == "span":
        return f"{start}-{start + 1}"
    return str(start)


def sport_of_url(url: str) -> str:
    """Which API-Sports product a request goes to, from its host, or ""."""
    try:
        host = httpx.URL(url).host.lower()
    except Exception:  # noqa: BLE001
        return ""
    for key, sport in SPORTS.items():
        if httpx.URL(sport.host).host == host:
            return key
    return ""


def enabled_sports() -> list:
    """The products this deployment uses (`API_SPORTS_SPORTS`), in order."""
    raw = (settings.api_sports_sports or "").strip()
    if not raw:
        return list(SPORTS)
    return [k.strip() for k in raw.split(",") if k.strip() in SPORTS]


# --------------------------------------------------------------------------
# One daily allowance, shared by the sweep and the lookups (§135)
# --------------------------------------------------------------------------
class RequestBudget:
    """API-Sports' daily request allowance, counted as it is spent.

    The free tier is a hundred requests a *day*, and two things spend them:
    the story sweep that keeps myFAM's scores current, and the live lookup an
    episode about a game makes. The owner's direction is that the sweep uses
    the allowance to the full - roughly every fifteen minutes - rather than
    the two-hourly trickle it had. So the sweep is **paced**: whatever is
    left, spread evenly over what is left of the UTC day. A day where
    episodes spent a lot sweeps a little less often; a quiet one sweeps as
    often as the plan allows; neither runs out before midnight.

    Per process and reset at UTC midnight, which is when API-Sports resets.
    Several workers each keep their own count, which is why the setting says
    to divide the plan between them.
    """

    #: Never faster than this, however much is left late in the day - the
    #: pool refreshes on a fifteen-minute clock and a request that lands
    #: between two refreshes buys nothing.
    MIN_INTERVAL_SECONDS = 300.0

    def __init__(self, sport: str = "") -> None:
        #: The API-Sports product this counts, or "" for the shared fallback
        #: (a request to a host no product in `SPORTS` owns).
        self.sport = sport
        self.day = ""
        self.used = 0
        #: What API-Sports itself said on its last answer, from the
        #: `x-ratelimit-requests-*` headers: the plan's daily limit and what
        #: was left, and our count when it said so. The truth across every
        #: worker, where `used` is only this process's.
        self.reported_limit: Optional[int] = None
        self.reported_remaining: Optional[int] = None
        self.reported_used_at = 0
        self.reported_day = ""

    @staticmethod
    def _today(now: Optional[float] = None) -> str:
        at = datetime.fromtimestamp(time.time() if now is None else now,
                                    tz=timezone.utc)
        return at.date().isoformat()

    def _roll(self, now: Optional[float] = None) -> None:
        today = self._today(now)
        if today != self.day:
            self.day, self.used = today, 0
        if self.reported_day and self.reported_day != today:
            self.reported_remaining = None
            self.reported_day = ""

    def note_reported(self, limit: Optional[int], remaining: Optional[int],
                      now: Optional[float] = None) -> None:
        """Take API-Sports' own count, off the headers of an answer."""
        self._roll(now)
        if limit is not None:
            self.reported_limit = max(0, int(limit))
        if remaining is not None:
            self.reported_remaining = max(0, int(remaining))
            self.reported_used_at = self.used
            self.reported_day = self.day

    def spend(self, requests: int = 1, now: Optional[float] = None) -> None:
        self._roll(now)
        self.used += max(0, int(requests))

    def exhaust(self, now: Optional[float] = None) -> None:
        """The provider said the day's allowance is gone; believe it."""
        self._roll(now)
        self.used = max(self.used, self.daily)

    @property
    def daily(self) -> int:
        if self.sport:
            return daily_allowance(self.sport)
        return max(0, int(settings.api_sports_daily_requests) or TIERS["free"]["daily"])

    def remaining(self, now: Optional[float] = None) -> int:
        self._roll(now)
        own = max(0, self.daily - self.used)
        if self.reported_remaining is not None:
            # The provider's count is every worker's; ours is this process's.
            # Whichever says less is the one that holds.
            since = self.used - self.reported_used_at
            own = min(own, max(0, self.reported_remaining - since))
        return own

    def reserve(self) -> int:
        """Requests the sweep leaves for episodes' own lookups (§180)."""
        share = max(0.0, min(0.9, float(settings.api_sports_lookup_reserve)))
        return int(round(self.daily * share))

    def sweep_interval(self, per_sweep: int, now: Optional[float] = None) -> float:
        """How long to wait between sweeps that each cost `per_sweep`."""
        now = time.time() if now is None else now
        per_sweep = max(1, int(per_sweep))
        at = datetime.fromtimestamp(now, tz=timezone.utc)
        midnight = datetime(at.year, at.month, at.day, tzinfo=timezone.utc)
        left_today = max(1.0, 86400.0 - (at - midnight).total_seconds())
        sweeps_left = max(0, self.remaining(now) - self.reserve()) // per_sweep
        if sweeps_left <= 0:
            return left_today           # nothing left: next sweep is tomorrow
        return max(self.MIN_INTERVAL_SECONDS, left_today / sweeps_left)

    def as_dict(self) -> dict:
        out = {"daily": self.daily, "used_today": self.used if self.day ==
               self._today() else 0, "remaining": self.remaining()}
        if self.sport:
            out["tier"] = tier_of(self.sport)
        if self.reported_limit is not None:
            out["provider_limit"] = self.reported_limit
        if self.reported_remaining is not None:
            out["provider_remaining"] = self.reported_remaining
        return out


# --------------------------------------------------------------------------
# Plans per sport (§180)
# --------------------------------------------------------------------------
#: API-Sports' plans, per product: each sport is its own subscription on the
#: same key, so each is on its own plan. Copied from the published prices,
#: looked up 2026-09-30 - re-check before buying. `API_SPORTS_TIERS` picks one
#: per sport; everything is `free` until somebody buys something.
TIERS = {
    "free":  {"daily": 100,     "usd": 0,  "label": "Free"},
    "pro":   {"daily": 7_500,   "usd": 19, "label": "Pro"},
    "ultra": {"daily": 75_000,  "usd": 29, "label": "Ultra"},
    "mega":  {"daily": 150_000, "usd": 39, "label": "Mega"},
}
TIER_ORDER = ("free", "pro", "ultra", "mega")


_TIERS_PARSED: dict = {}


def configured_tiers() -> dict:
    """sport -> tier, from `API_SPORTS_TIERS` ("hockey=pro,football=ultra")
    over `API_SPORTS_TIER` (every sport's default). A tier this build does not
    know is logged - once per setting, not on every count - and read as the
    default, never guessed upward."""
    raw = (settings.api_sports_tier, settings.api_sports_tiers,
           int(settings.api_sports_daily_requests or 0))
    held = _TIERS_PARSED.get(raw)
    if held is None:
        held = _TIERS_PARSED[raw] = _parse_tiers()
    return held


def _parse_tiers() -> dict:
    default = (settings.api_sports_tier or "free").strip().lower()
    if default not in TIERS:
        log.error("API_SPORTS_TIER=%r is not one of %s; using free",
                  default, ", ".join(TIER_ORDER))
        default = "free"
    out = {key: default for key in SPORTS}
    for part in (settings.api_sports_tiers or "").split(","):
        if "=" not in part:
            continue
        sport, _, tier = (x.strip().lower() for x in part.partition("="))
        if sport not in SPORTS:
            log.error("API_SPORTS_TIERS names %r, which is not one of %s",
                      sport, ", ".join(SPORTS))
            continue
        if tier not in TIERS:
            log.error("API_SPORTS_TIERS gives %s the tier %r, which is not one "
                      "of %s; using %s", sport, tier, ", ".join(TIER_ORDER), default)
            continue
        out[sport] = tier
    override = int(settings.api_sports_daily_requests or 0)
    for sport, tier in out.items():
        if override > TIERS[tier]["daily"]:
            # The pre-§180 guidance was "bought Pro: set this to 7,500". It is
            # a ceiling now, so say why the allowance did not follow it.
            log.error("API_SPORTS_DAILY_REQUESTS=%d is above %s's %s plan "
                      "(%d/day) and only caps it now; if %s is on a bigger "
                      "plan, say so in API_SPORTS_TIERS", override, sport,
                      tier, TIERS[tier]["daily"], sport)
    return out


def tier_of(sport: str) -> str:
    return configured_tiers().get(sport, "free")


def next_tier(sport: str) -> str:
    """The plan above this sport's, or "" when it is on the top one."""
    at = TIER_ORDER.index(tier_of(sport))
    return TIER_ORDER[at + 1] if at + 1 < len(TIER_ORDER) else ""


def daily_allowance(sport: str) -> int:
    """Requests per UTC day this process may spend on `sport`.

    The sport's plan, unless `API_SPORTS_DAILY_REQUESTS` sets a ceiling of its
    own - for several workers sharing one plan, each takes a share."""
    override = int(settings.api_sports_daily_requests or 0)
    allowance = TIERS[tier_of(sport)]["daily"]
    return max(0, min(allowance, override) if override > 0 else allowance)


API_SPORTS_BUDGET = RequestBudget()
#: One budget per product, because API-Sports counts each separately.
BUDGETS: dict = {}


def budget_for(sport: str) -> RequestBudget:
    """This sport's budget; the shared fallback for a host no sport owns."""
    if not sport or sport not in SPORTS:
        return API_SPORTS_BUDGET
    held = BUDGETS.get(sport)
    if held is None:
        held = BUDGETS[sport] = RequestBudget(sport)
    return held


def _header_int(headers, name: str) -> Optional[int]:
    try:
        value = headers.get(name)
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError, AttributeError):
        return None


def _note_quota(url: str, headers) -> None:
    """Read API-Sports' own daily count off an answer. Never raises."""
    try:
        limit = _header_int(headers, "x-ratelimit-requests-limit")
        left = _header_int(headers, "x-ratelimit-requests-remaining")
        if limit is not None or left is not None:
            budget_for(sport_of_url(url)).note_reported(limit, left)
    except Exception:  # noqa: BLE001 - a header is never worth a request
        log.debug("could not read API-Sports' quota headers", exc_info=True)


def budgets_report() -> dict:
    """Per enabled sport: plan, allowance, today's spend, and what API-Sports
    itself reported - for /api/health and the admin page."""
    out = {}
    # Every sport in use, and any other one spending today (a sport swept
    # through STORIES_SPORTS but not in API_SPORTS_SPORTS is still billed).
    keys = list(enabled_sports()) + [k for k in BUDGETS if k not in enabled_sports()]
    for key in keys:
        budget = budget_for(key)
        row = budget.as_dict()
        row["label"] = SPORTS[key].label
        row["mismatch"] = bool(budget.reported_limit is not None
                               and budget.reported_limit != TIERS[tier_of(key)]["daily"])
        out[key] = row
    return out


class BudgetSpent(RuntimeError):
    """The day's API-Sports allowance is gone. Said, not guessed around."""


def _limit_reached(data: dict) -> bool:
    """Whether a reply is API-Sports saying the day's allowance is spent.

    It answers 200 with the refusal in `errors`, so a status check would read
    it as a quiet day with no games - which is §89's failure exactly.
    """
    errors = (data or {}).get("errors")
    text = str(errors or "").lower()
    return bool(errors) and ("request" in text and "limit" in text)


async def api_sports_json(url: str, params: dict, timeout: float) -> dict:
    """One API-Sports request, counted against its sport's daily allowance."""
    sport = sport_of_url(url)
    budget = budget_for(sport)
    if budget.remaining() <= 0:
        raise BudgetSpent(
            f"API-Sports' {budget.daily} {SPORTS[sport].label if sport else ''} "
            "requests for today are spent; they come back at 00:00 UTC")
    budget.spend(1)
    data = await _json(url, {"x-apisports-key": settings.api_sports_key},
                       params, timeout)
    if _limit_reached(data):
        budget.exhaust()
        raise BudgetSpent(f"API-Sports refused: {(data or {}).get('errors')}")
    errors = (data or {}).get("errors")
    if errors:
        # A bad key, a bad parameter, a suspended account: all answered 200
        # with the reason in `errors` and an empty `response`. Read as data,
        # that is a day with no games and nothing to say it is wrong.
        raise RuntimeError(f"API-Sports refused: {errors}")
    return data


#: Today's card, as the last story sweep read it: sport key -> (when, rows).
#: The live lookup reads it before spending a request of its own, because on
#: a plan of a hundred a day a request saved is fourteen minutes of fresher
#: scores on myFAM.
CARD: dict = {}


def remember_card(sport_key: str, rows: list, now: Optional[float] = None) -> None:
    CARD[sport_key] = (time.time() if now is None else now, list(rows or []))


def card_rows(sport_key: str, max_age: float,
              now: Optional[float] = None) -> Optional[list]:
    """The swept card for a sport, if it is younger than `max_age` seconds."""
    held = CARD.get(sport_key)
    if not held:
        return None
    at, rows = held
    now = time.time() if now is None else now
    return rows if now - at <= max_age else None


#: Leagues whose games lead the card, as (league, country) with "" meaning
#: any country. A date request returns every fixture in the world - a
#: Tuesday's football card is hundreds of rows - and without this a
#: third-division match outranks the game half the listeners are watching.
#: Pairs, because "Premier League" is a name in a dozen countries.
MAJOR_LEAGUES = frozenset({
    ("nfl", ""), ("ncaa", ""), ("nba", ""), ("wnba", ""), ("mlb", ""),
    ("premier league", "england"), ("la liga", "spain"),
    ("serie a", "italy"), ("bundesliga", "germany"), ("ligue 1", "france"),
    ("eredivisie", "netherlands"), ("primeira liga", "portugal"),
    ("major league soccer", "usa"), ("liga mx", "mexico"),
    ("uefa champions league", ""), ("uefa europa league", ""),
    ("uefa nations league", ""), ("world cup", ""), ("fifa world cup", ""),
    ("copa libertadores", ""), ("euroleague", ""),
    # §180's sports.
    ("nhl", ""), ("afl", ""), ("six nations", ""), ("rugby world cup", ""),
    ("super rugby", ""), ("nrl", ""),
})


def is_major_event(sport: "Sport", row: dict) -> bool:
    """A race or a fight has no league row: every Formula 1 race is major,
    and a fight is when it is on a UFC card (§180)."""
    if sport.kind == "race":
        return True
    if sport.kind == "fight":
        text = " ".join(str((row or {}).get(k) or "") for k in ("slug", "event", "name"))
        return "ufc" in text.lower()
    return False


def league_of(row: dict) -> tuple:
    """`(league name, country)` for a row, whichever sport's shape it is."""
    league = (row or {}).get("league") or {}
    name = str(league.get("name") or "").strip()
    country = league.get("country")
    if isinstance(country, dict):
        country = country.get("name")
    if not country:
        block = (row or {}).get("country") or {}
        country = block.get("name") if isinstance(block, dict) else block
    return name, str(country or "").strip()


def is_major(name: str, country: str) -> bool:
    name, country = name.lower(), country.lower()
    return (name, "") in MAJOR_LEAGUES or (name, country) in MAJOR_LEAGUES


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
    padded = " " + " ".join(re.findall(r"[a-z0-9]+", text)) + " "
    enabled = enabled_sports()

    def said(word: str) -> bool:
        # Whole words, so "f1" is not inside "f150" and "afl" not "waffle".
        return " " + " ".join(re.findall(r"[a-z0-9]+", word)) + " " in padded
    matched = [SPORTS[k] for k in enabled if any(said(w) for w in SPORTS[k].words)]
    # Several sports share event names ("world cup", "champions league"); the
    # one whose own name is said wins - "Rugby World Cup" is rugby (§180).
    for sport in matched:
        own = {sport.key.replace("-", " "), sport.label.lower(), sport.words[0]}
        if any(said(word) for word in own):
            return sport
    if matched:
        return matched[0]
    # No sport named: a team this deployment has already read a league's
    # catalogue for ("Maple Leafs game") names its sport (§180). Only cached
    # catalogues are asked - routing never spends a request.
    for key in enabled:
        held = TEAMS.get(key)
        if held and teams_named(held[1], subject):
            return SPORTS[key]
    default = settings.api_sports_sport if settings.api_sports_sport in enabled \
        else (enabled[0] if enabled else "american-football")
    return SPORTS.get(default, SPORTS["american-football"])


# --------------------------------------------------------------------------
# Team seasons (9.30 #3): the numbers an episode gets wrong
# --------------------------------------------------------------------------
#
# The live lookup used to know one thing about a game: its score and whether
# it had started. Everything else an episode said about the teams - their
# record, who they beat last week, when they play next - came from articles,
# and articles disagree: a preview written before last week's game, a recap
# of the wrong week, a Madden sim league's "result" for a game not yet
# played (the 9.30 packet's worst episode). So the numbers now come from the
# provider's own season schedule, counted in code: a record is the finals in
# the regular season, never a figure a model read or remembered.

#: The league's teams, per sport: (fetched at, rows). A roster changes once a
#: year; a day is generous.
TEAMS: dict = {}
TEAMS_SECONDS = 86400.0
#: sport -> (UTC day, why) for a catalogue the provider refused (§191): it
#: is not asked again until its day turns over.
TEAMS_REFUSED: dict = {}
#: Each team's season schedule: (sport, team id) -> (fetched at, rows).
SCHEDULES: dict = {}
#: How long a schedule serves before it is asked again - unless it holds a
#: game whose state may have moved (`_unsettled`), which is asked every time.
SCHEDULE_SECONDS = 600.0
#: Which stage counts toward a record. Pre-season games never do.
#: Matched as a phrase: the NFL's stage is "Regular Season", other APIs
#: prefix it ("NBA - Regular Season").
REGULAR_SEASON = "regular season"
#: The share of a resolve's time the team path may take, leaving the rest
#: for today's live games if it finds nothing.
TEAM_PATH_SHARE = 0.6


def _row_block(row: dict, name: str) -> dict:
    for holder in ("game", "fixture"):
        block = (row or {}).get(holder) or {}
        if block.get(name) is not None:
            return {name: block.get(name)}
    return {name: (row or {}).get(name)}


async def league_teams(sport: Sport, now: Optional[float] = None) -> list:
    """Every team in the sport's `team_league` this season, from the
    provider's own catalogue - never a list a model wrote."""
    now = time.time() if now is None else now
    held = TEAMS.get(sport.key)
    if held and now - held[0] < TEAMS_SECONDS:
        return held[1]
    day = RequestBudget._today(now)
    refused = TEAMS_REFUSED.get(sport.key)
    if refused and refused[0] == day:
        # Asked once today and refused (§191): the answer will not change
        # before the provider's day does, and asking again on every sweep
        # and every lookup is how hockey spent 172 requests in a day.
        raise RuntimeError(f"the {sport.label} catalogue was refused today: "
                           f"{refused[1]}")
    try:
        data = await api_sports_json(f"{sport.host}/teams",
                                     {"league": sport.team_league,
                                      "season": season_of(sport)},
                                     settings.live_timeout_seconds)
    except BudgetSpent:
        raise          # the allowance, not the catalogue: tomorrow says
    except ProviderHTTPError as exc:
        # A 4xx is the provider saying no; a 5xx is the provider unwell,
        # which the next sweep may find better.
        if 400 <= exc.status < 500:
            TEAMS_REFUSED[sport.key] = (day, str(exc))
        raise
    except RuntimeError as exc:
        # Answered 200 with the refusal in `errors` (`api_sports_json`) -
        # the free plan's season limit is one.
        TEAMS_REFUSED[sport.key] = (day, str(exc))
        raise
    rows = [r for r in ((data or {}).get("response") or [])
            if isinstance(r, dict) and r.get("id") is not None and r.get("name")]
    TEAMS[sport.key] = (now, rows)
    return rows


_TEAM_WORD = re.compile(r"[a-z0-9]+")


def teams_named(teams: list, text: str) -> list:
    """The teams `text` names, in the order it names them.

    By full name or nickname ("Commanders", "49ers"), and by city only where
    one team has it - "New York" is two teams and names neither."""
    words = _TEAM_WORD.findall((text or "").lower())
    joined = " " + " ".join(words) + " "
    # The words as typed, to tell "Houston game" (the Texans) from "Houston
    # Rockets" (somebody else's team): a city followed by a capitalised
    # name names that name's team, not this league's.
    typed = re.findall(r"[A-Za-z0-9]+", text or "")
    cities: dict = {}
    for team in teams:
        city = " ".join(_TEAM_WORD.findall(str(team.get("city") or "").lower()))
        if city:
            cities[city] = cities.get(city, 0) + 1
    found = []
    for team in teams:
        name = _TEAM_WORD.findall(str(team.get("name") or "").lower())
        # One-word "teams" are conference all-star sides (AFC, NFC), which
        # no question about a team means.
        if len(name) < 2:
            continue
        city = " ".join(_TEAM_WORD.findall(str(team.get("city") or "").lower()))
        keys = [" ".join(name), name[-1]]
        at = [joined.find(" " + k + " ") for k in keys if k]
        if city and cities.get(city) == 1 and _city_alone(typed, city):
            at.append(joined.find(" " + city + " "))
        at = [a for a in at if a >= 0]
        if at:
            found.append((min(at), team))
    return [team for _, team in sorted(found, key=lambda x: x[0])]


def _city_alone(typed: list, city: str) -> bool:
    """Whether `city` appears in the typed words without a capitalised name
    straight after it ("Houston game", not "Houston Rockets")."""
    parts = city.split()
    lowered = [w.lower() for w in typed]
    for i in range(len(lowered) - len(parts) + 1):
        if lowered[i:i + len(parts)] == parts:
            after = typed[i + len(parts)] if i + len(parts) < len(typed) else ""
            if not (after[:1].isupper() and after.lower() not in _NOT_NAMES):
                return True
    return False


#: Capitalised words that may follow a city without being a team's name.
_NOT_NAMES = frozenset({"game", "games", "football", "nfl", "score", "vs",
                        "v", "at", "and", "this", "last", "next", "week",
                        "tonight", "today", "sunday", "monday", "thursday",
                        "saturday", "record", "season"})

#: How long after its kick-off a game that is not final may still be under
#: way. Past it, a game that never went final (postponed, cancelled) is not a
#: reason to read the schedule again on every lookup.
UNSETTLED_HOURS = 6.0


def _unsettled(rows: list, sport: Sport, now: float, skip: str = "") -> bool:
    """Whether a schedule holds a game whose state may have moved since it
    was read: one under way, or one kicked off in the last few hours and not
    yet final. `skip` is a game the caller already holds fresh."""
    for row in rows:
        if skip and ApiSportsSource._game_id(row) == skip:
            continue
        status = sport.statuses.get(
            str(ApiSportsSource._status_block(row).get("short") or "").upper(),
            live_facts.UNKNOWN)
        if status == live_facts.IN_PROGRESS:
            return True
        kick = ApiSportsSource._kickoff(row)
        if (status != live_facts.FINAL and kick is not None
                and now - UNSETTLED_HOURS * 3600 <= kick.timestamp() <= now):
            return True
    return False


async def team_schedule(sport: Sport, team_id, now: Optional[float] = None,
                        fresh: Optional[dict] = None) -> list:
    """One team's games this season, oldest first.

    `fresh` is a game row the caller has just fetched: it replaces that game
    in the schedule, and does not by itself make the schedule worth asking
    for again - so a live game costs one request per lookup, not three."""
    now = time.time() if now is None else now
    key = (sport.key, str(team_id))
    held = SCHEDULES.get(key)
    skip = ApiSportsSource._game_id(fresh) if fresh else ""
    if held and now - held[0] < SCHEDULE_SECONDS and not _unsettled(
            held[1], sport, now, skip):
        return _with_fresh(held[1], fresh, skip)
    if str(team_id) == "season":
        # A race calendar: every race this season, not one team's (§180).
        params = {"season": season_of(sport), "type": "Race"}
    else:
        params = {"team": str(team_id), "season": season_of(sport)}
        if sport.team_league:
            params["league"] = sport.team_league
    data = await api_sports_json(f"{sport.host}/{sport.path}", params,
                                 settings.live_timeout_seconds)
    rows = [r for r in ((data or {}).get("response") or []) if isinstance(r, dict)]
    rows.sort(key=lambda r: (ApiSportsSource._kickoff(r)
                             or datetime.max.replace(tzinfo=timezone.utc)))
    SCHEDULES[key] = (now, rows)
    return _with_fresh(rows, fresh, skip)


def _with_fresh(rows: list, fresh: Optional[dict], game_id: str) -> list:
    if not fresh or not game_id:
        return rows
    return [fresh if ApiSportsSource._game_id(r) == game_id else r for r in rows]


def _side(row: dict, team_id) -> Optional[str]:
    teams = (row or {}).get("teams") or {}
    for side in ("home", "away"):
        if str((teams.get(side) or {}).get("id")) == str(team_id):
            return side
    return None


def _local(at: datetime, sport: Optional[Sport]) -> tuple:
    """`at` in the league's own zone, and that zone's spoken name."""
    if sport is not None and sport.local_zone:
        try:
            from zoneinfo import ZoneInfo

            return at.astimezone(ZoneInfo(sport.local_zone)), sport.zone_said
        except Exception:  # noqa: BLE001 - no tz database: say UTC, never guess
            pass
    return at.astimezone(timezone.utc), "UTC"


def _when_said(at: Optional[datetime], sport: Optional[Sport] = None) -> str:
    """The day a game is on, counted where its league counts days."""
    if at is None:
        return ""
    local, _ = _local(at, sport)
    return f"{local:%A} {local.day} {local:%B}"


def _kickoff_said(at: Optional[datetime], sport: Optional[Sport] = None) -> str:
    """'Sunday 4 October at 1:00 pm Eastern', or '' with no time."""
    if at is None:
        return ""
    local, zone = _local(at, sport)
    if zone == "UTC":
        clock = f"{local:%H:%M}"
    else:
        clock = f"{local.hour % 12 or 12}:{local:%M} {'am' if local.hour < 12 else 'pm'}"
    return f"{_when_said(at, sport)} at {clock} {zone}"


def season_facts(sport: Sport, team: dict, rows: list,
                 now: Optional[datetime] = None) -> list:
    """What is settled about one team's season, as spoken sentences.

    Its record over the finished regular-season games, its last result and
    its next game - each read off the provider's rows and counted here, so a
    number in these sentences is the scoreboard's and nobody's recollection.
    """
    now = now or datetime.now(timezone.utc)
    name = str(team.get("name") or "").strip()
    team_id = team.get("id")
    if not name or team_id is None:
        return []
    wins = losses = ties = 0
    finals, upcoming = [], []
    for row in rows:
        side = _side(row, team_id)
        if side is None:
            continue
        status = sport.statuses.get(
            str(ApiSportsSource._status_block(row).get("short") or "").upper(),
            live_facts.UNKNOWN)
        if status == live_facts.FINAL:
            finals.append((row, side))
            home, away = ApiSportsSource._score(row)
            if home is None or away is None:
                continue
            stage = str(_row_block(row, "stage")["stage"] or "").strip().lower()
            if stage and REGULAR_SEASON not in stage:
                continue
            mine, theirs = (home, away) if side == "home" else (away, home)
            if mine > theirs:
                wins += 1
            elif mine < theirs:
                losses += 1
            else:
                ties += 1
        elif status == live_facts.SCHEDULED:
            kick = ApiSportsSource._kickoff(row)
            if kick is None or kick >= now - timedelta(hours=6):
                upcoming.append((row, side))
    said = []
    if wins or losses or ties:
        record = (f"The {name} have won {wins} and lost {losses}"
                  + (f", with {ties} tied" if ties else "")
                  + " in the regular season so far.")
        said.append(record)
    elif upcoming and not finals and any(
            REGULAR_SEASON in str(_row_block(r, "stage")["stage"] or "").lower()
            for r, _ in upcoming):
        # Only when the schedule shows the regular season still ahead of
        # them. An empty or unreadable schedule says nothing: absence of
        # results is never a record of none.
        said.append(f"The {name} have not finished a regular-season game "
                    "yet this season.")
    if finals:
        row, side = finals[-1]
        home_name, away_name = ApiSportsSource._team_names(row)
        other = away_name if side == "home" else home_name
        hs, as_ = ApiSportsSource._score(row)
        if hs is not None and as_ is not None and other:
            mine, theirs = (hs, as_) if side == "home" else (as_, hs)
            when = _when_said(ApiSportsSource._kickoff(row), sport)
            on = f" on {when}" if when else ""
            if mine > theirs:
                said.append(f"Their last game: the {name} beat the {other} "
                            f"{mine} to {theirs}{on}.")
            elif mine < theirs:
                said.append(f"Their last game: the {name} lost to the {other} "
                            f"{theirs} to {mine}{on}.")
            else:
                said.append(f"Their last game: the {name} and the {other} "
                            f"tied {mine} each{on}.")
    if upcoming:
        row, side = upcoming[0]
        home_name, away_name = ApiSportsSource._team_names(row)
        other = away_name if side == "home" else home_name
        kick = ApiSportsSource._kickoff(row)
        venue = (_row_block(row, "venue")["venue"] or {})
        where = ""
        if isinstance(venue, dict):
            place = ", ".join(x for x in (str(venue.get("name") or "").strip(),
                                          str(venue.get("city") or "").strip()) if x)
            where = f" at {place}" if place else ""
        if other:
            when = _kickoff_said(kick, sport)
            said.append(
                f"Their next game has not been played yet: the {name} play the "
                f"{other}" + (f" on {when}" if when else "") + f"{where}.")
    return said


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
        """Ask every enabled sport's `/status` (§180): each is its own
        subscription, so each says its own plan and today's count - and a
        plan that is not the one `API_SPORTS_TIERS` says is named."""
        said, ok_all = [], True
        for key in enabled_sports():
            sport = SPORTS[key]
            try:
                data = await _json(f"{sport.host}/status", self._headers(), {},
                                   settings.live_timeout_seconds)
            except Exception as exc:  # noqa: BLE001
                ok_all = False
                said.append(f"{key}: did not answer ({type(exc).__name__}: {exc})")
                continue
            account = (data or {}).get("response") or {}
            if not isinstance(account, dict) or not account:
                ok_all = False
                errors = (data or {}).get("errors")
                said.append(f"{key}: rejected ({errors or 'unreadable'})")
                continue
            requests = account.get("requests") or {}
            plan = str((account.get("subscription") or {}).get("plan") or "?")
            expected = tier_of(key)
            note = "" if plan.lower() == expected or plan == "?" else \
                f" - API_SPORTS_TIERS says {expected}; set it to {plan.lower()}"
            said.append(f"{key}: {plan} plan, {requests.get('current', '?')}/"
                        f"{requests.get('limit_day', '?')} used today{note}")
        return ok_all, "API-Sports per sport: " + "; ".join(said)

    async def resolve(self, brief) -> Optional[Entity]:
        subject = (getattr(brief, "subject", "") or getattr(brief, "query", "")).strip()
        if not subject:
            return None
        # A named slot ("Sunday Night Football") names one game by when it
        # kicks off, and its words match no team - so without this the row
        # picked was whichever one happened to contain "football", or none.
        slot = named_slots.by_key(getattr(brief, "named_slot", "") or "")
        if slot is not None and slot.sport in SPORTS:
            return await self._resolve_slot(brief, slot)

        sport = sport_for(subject)
        # A race or a fight is not a game between two teams (§180).
        if sport.kind == "race":
            return await self._resolve_race(sport, subject)
        if sport.kind == "fight":
            return await self._resolve_fight(sport, subject)
        # Today's card from the last sweep first: finding *which* game costs
        # no request when the sweep already listed it. Only a game the sweep
        # did not see costs one. The state itself is always fetched fresh -
        # see `fetch`.
        rows = card_rows(sport.key, max_age=6 * 3600.0)
        wanted = {w for w in subject.lower().split() if len(w) > 3}
        on_card = rows is not None and any(
            wanted and any(w in " ".join(self._team_names(r)).lower()
                           for w in wanted) for r in rows)
        if not on_card and sport.team_league:
            # The teams' own schedules first: they hold a game under way as
            # well as last week's and next week's, so `live=all` would be a
            # request spent before the one that answers - and the whole
            # resolve has LIVE_TIMEOUT_SECONDS.
            try:
                found = await asyncio.wait_for(
                    self._resolve_by_team(brief, sport, subject),
                    timeout=settings.live_timeout_seconds * TEAM_PATH_SHARE)
            except Exception as exc:  # noqa: BLE001 - the card path still runs
                log.info("live facts: team schedules did not resolve %r (%s); "
                         "trying today's live games", subject,
                         type(exc).__name__)
                found = None
            if found is not None:
                return found
        if rows is None or not any(
                wanted and any(w in " ".join(self._team_names(r)).lower()
                               for w in wanted) for r in rows):
            data = await api_sports_json(f"{sport.host}/{sport.path}",
                                         {"live": "all"},
                                         settings.live_timeout_seconds)
            rows = (data or {}).get("response", []) or []

        for row in rows:
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

    async def _resolve_race(self, sport: Sport, subject: str) -> Optional[Entity]:
        """The race a question is about: the season's calendar (one request,
        cached like a team's schedule), then the Grand Prix it names by its
        name or place, else the one under way, just run, or next."""
        rows = await team_schedule(sport, "season")
        rows = [r for r in rows if str(r.get("type") or "race").lower() == "race"]
        words = {w for w in re.findall(r"[a-z]+", subject.lower()) if len(w) > 3}
        words -= {"grand", "prix", "formula", "race", "result", "results",
                  "winner", "won", "who", "what", "when", "this", "next", "last",
                  "will", "today", "season", "weekend", "qualifying", "sprint"}

        def names(row: dict) -> str:
            comp = row.get("competition") or {}
            where = comp.get("location") or {} if isinstance(comp, dict) else {}
            return " ".join(str(x) for x in (
                comp.get("name", "") if isinstance(comp, dict) else "",
                where.get("country", "") if isinstance(where, dict) else "",
                where.get("city", "") if isinstance(where, dict) else "",
                (row.get("circuit") or {}).get("name", "")
                if isinstance(row.get("circuit"), dict) else "")).lower()
        named = [r for r in rows
                 if words & set(re.findall(r"[a-z]+", names(r)))]
        row = self._pick_game(named or rows, sport, two_teams=bool(named))
        if row is None:
            return None
        return Entity(domain="sports", provider=self.name,
                      id=f"{sport.key}:{self._game_id(row)}",
                      label=self._team_names(row)[0] or "a race")

    async def _resolve_fight(self, sport: Sport, subject: str) -> Optional[Entity]:
        """The fight a question names by a fighter, on today's card (the
        sweep's, else one request). A fight card is a day's; a fighter no
        card today names is not guessed at."""
        rows = card_rows(sport.key, max_age=6 * 3600.0)
        if rows is None:
            data = await api_sports_json(
                f"{sport.host}/{sport.path}",
                {"date": datetime.now(timezone.utc).strftime("%Y-%m-%d")},
                settings.live_timeout_seconds)
            rows = (data or {}).get("response", []) or []
        words = {w for w in re.findall(r"[a-z]+", subject.lower()) if len(w) > 3}

        def match(rows: list) -> Optional[Entity]:
            for row in rows:
                first, second = self._team_names(row)
                # Whole names: "will" is not inside "Williams".
                names = set(re.findall(r"[a-z]+", f"{first} {second}".lower()))
                if words & names:
                    return Entity(domain="sports", provider=self.name,
                                  id=f"{sport.key}:{self._game_id(row)}",
                                  label=f"{first} v {second}")
            return None
        found = match(rows)
        if found is None and words:
            # "Who won the Jones fight" is asked the morning after: a US card
            # ends after midnight UTC, so yesterday's card is the other place.
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
            data = await api_sports_json(f"{sport.host}/{sport.path}",
                                         {"date": yesterday},
                                         settings.live_timeout_seconds)
            found = match((data or {}).get("response", []) or [])
        return found

    async def _resolve_by_team(self, brief, sport: Sport,
                               subject: str) -> Optional[Entity]:
        """The game a question about one or two teams is about (9.30 #3):
        a game later this week, last week's, or a team rather than a game
        ("how are the Commanders doing"), from the provider's catalogue.

        Two teams: their meeting this season - the one under way, else the
        next one, else the last. One team: its game under way, else one
        finished in the last day and a half, else its next, else its last.
        """
        teams = await league_teams(sport)
        text = " ".join((subject, getattr(brief, "query", "") or ""))
        named = teams_named(teams, text)
        if not named:
            return None
        rows = await team_schedule(sport, named[0]["id"])
        if len(named) > 1:
            other = named[1]["id"]
            rows = [r for r in rows if _side(r, other) is not None]
        row = self._pick_game(rows, sport, two_teams=len(named) > 1)
        if row is None:
            return None
        home, away = self._team_names(row)
        return Entity(domain="sports", provider=self.name,
                      id=f"{sport.key}:{self._game_id(row)}",
                      label=f"{home} v {away}")

    @classmethod
    def _pick_game(cls, rows: list, sport: Sport, *, two_teams: bool,
                   now: Optional[datetime] = None) -> Optional[dict]:
        now = now or datetime.now(timezone.utc)
        live, finals, upcoming = [], [], []
        for row in rows:
            status = sport.statuses.get(
                str(cls._status_block(row).get("short") or "").upper(),
                live_facts.UNKNOWN)
            kick = cls._kickoff(row)
            if status == live_facts.IN_PROGRESS:
                live.append(row)
            elif status == live_facts.FINAL:
                finals.append(row)
            elif kick is not None and kick >= now - timedelta(hours=6):
                upcoming.append(row)
        if live:
            return live[0]
        epoch = datetime.min.replace(tzinfo=timezone.utc)
        finals.sort(key=lambda r: cls._kickoff(r) or epoch)
        upcoming.sort(key=lambda r: cls._kickoff(r) or epoch)
        recent = finals[-1] if finals else None
        if (not two_teams and recent is not None
                and (cls._kickoff(recent) or epoch) >= now - timedelta(hours=36)):
            return recent
        if upcoming:
            return upcoming[0]
        return recent

    async def _resolve_slot(self, brief, slot) -> Optional[Entity]:
        """The one game a named slot names, picked by its kick-off time.

        No game in the window is no game - never the nearest one, which is
        how an afternoon kick-off became a "Sunday Night Football" recap.
        """
        sport = SPORTS[slot.sport]
        rows = card_rows(sport.key, max_age=6 * 3600.0) or []
        if not any(named_slots.in_slot(slot, self._kickoff(r)) for r in rows):
            # By date rather than `live=all`: a recap is asked after the game,
            # when it is no longer live. The prime-time slots all kick off
            # after midnight UTC, so today's UTC card holds last night's game.
            data = await api_sports_json(
                f"{sport.host}/{sport.path}",
                {"date": datetime.now(timezone.utc).strftime("%Y-%m-%d")},
                settings.live_timeout_seconds)
            rows = (data or {}).get("response", []) or []
        timed = [r for r in rows if named_slots.in_slot(slot, self._kickoff(r))]
        typed = " ".join((getattr(brief, "query", "") or "").lower().split())
        slot_words = set(slot.name.lower().split()) | {slot.key, "recap", "score"}
        team_words = {w for w in typed.split() if len(w) > 3 and w not in slot_words}
        if team_words:
            timed = [r for r in timed
                     if any(w in " ".join(self._team_names(r)).lower()
                            for w in team_words)] or timed
        if not timed:
            return None
        # The latest kick-off in the window: a Monday doubleheader's second
        # game is the one still being talked about.
        row = max(timed, key=self._kickoff)
        home, away = self._team_names(row)
        return Entity(domain="sports", provider=self.name,
                      id=f"{sport.key}:{self._game_id(row)}",
                      label=f"{home} v {away}")

    async def fetch(self, entity: Entity) -> Optional[LiveFacts]:
        sport_key, _, game_id = entity.id.partition(":")
        sport = SPORTS.get(sport_key)
        if sport is None or not game_id:
            return None
        # A sweep inside half the freshness limit is as good as a request, and
        # free. Anything older is fetched: a score is withheld past
        # `MAX_AGE_SECONDS`, and a fact about to be withheld is not worth
        # having saved a request on.
        started = time.monotonic()
        fresh = card_rows(sport.key, max_age=live_facts.MAX_AGE_SECONDS["sports"] / 2)
        rows = [r for r in (fresh or []) if self._game_id(r) == game_id]
        swept_at = CARD[sport.key][0] if rows else None
        if not rows:
            data = await api_sports_json(f"{sport.host}/{sport.path}",
                                         {"id": game_id},
                                         settings.live_timeout_seconds)
            rows = (data or {}).get("response", []) or []
        if not rows:
            return None
        row = rows[0]
        if sport.kind == "race" and sport.statuses.get(
                str(self._status_block(row).get("short") or "").upper()) \
                == live_facts.FINAL:
            row = dict(row, podium=await self._podium(sport, game_id))
        facts = self.to_facts(row, entity, sport)
        league = (row.get("league") or {}) if isinstance(row.get("league"), dict) else {}
        own_league = str(league.get("id", sport.team_league)) == sport.team_league
        if facts is not None and sport.team_league and own_league:
            # Only for the league the records are counted in: a EuroLeague or
            # KHL game would spend two requests on an empty NBA/NHL schedule.
            # Inside what is left of this fetch's own time, so a slow schedule
            # can never cost the game the facts already in hand.
            left = settings.live_timeout_seconds * 0.9 - (time.monotonic() - started)
            facts = await self._with_seasons(facts, rows[0], sport, left)
        if facts is not None and swept_at is not None:
            # Read off the sweep, so it is as old as the sweep - never stamped
            # with this moment, which would make the freshness check that
            # withholds a stale score pass a score it should have questioned.
            facts = replace(facts, as_of=datetime.fromtimestamp(
                swept_at, tz=timezone.utc))
        return facts

    #: A finished race's podium does not change: read once (§180).
    _PODIUMS: dict = {}

    async def _podium(self, sport: Sport, race_id: str) -> list:
        """A finished race's first three, `[(driver, team), ...]`, from its
        rankings - or [] when they cannot be read, and the race is then only
        said to have finished (§180)."""
        if race_id in self._PODIUMS:
            return self._PODIUMS[race_id]
        try:
            data = await api_sports_json(f"{sport.host}/rankings/races",
                                         {"race": race_id},
                                         settings.live_timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - the race's own facts stand
            log.info("live facts: no podium for race %s (%s)", race_id, exc)
            return []
        rows = [r for r in ((data or {}).get("response") or []) if isinstance(r, dict)]

        def position(r: dict) -> int:
            try:
                return int(r.get("position"))
            except (TypeError, ValueError):
                return 10_000
        rows = sorted((r for r in rows if position(r) < 10_000), key=position)
        podium = [(str((r.get("driver") or {}).get("name", "")),
                   str((r.get("team") or {}).get("name", ""))) for r in rows[:3]]
        if podium:
            self._PODIUMS[race_id] = podium
        return podium

    async def _with_seasons(self, facts: LiveFacts, row: dict,
                            sport: Sport, budget: float = 1.0) -> LiveFacts:
        """Both teams' records, last results and next games beside the game
        (9.30 #3). Never costs the game its facts: a schedule that cannot be
        read leaves the game's own sentences as they were, and says so in
        the log."""
        teams = (row or {}).get("teams") or {}
        sides = [teams.get(side) or {} for side in ("home", "away")]
        sides = [t for t in sides if t.get("id") is not None and t.get("name")]
        if budget <= 0:
            log.info("live facts: no time left for the teams' seasons")
            return facts
        try:
            schedules = await asyncio.wait_for(asyncio.gather(
                *(team_schedule(sport, t["id"], fresh=row) for t in sides)),
                timeout=budget)
        except Exception as exc:  # noqa: BLE001 - the game's facts stand
            log.warning("live facts: could not read the teams' seasons: %s", exc)
            return facts
        extra = []
        for team, schedule in zip(sides, schedules):
            extra.extend(season_facts(sport, team, schedule))
        # Their meeting is in both schedules; say its sentence once.
        extra = list(dict.fromkeys(extra))
        if not extra:
            return facts
        return replace(facts, facts=list(facts.facts) + extra)

    # --- shape readers ----------------------------------------------------
    # Small and separate because this is where the sports genuinely differ,
    # and a single branching `to_facts` is where that difference gets lost.
    @staticmethod
    def _team_names(row: dict) -> tuple:
        """The two sides: teams, or a fight's two fighters. A race has a
        field, not sides: its name and "" (§180)."""
        teams = (row or {}).get("teams") or {}
        if teams:
            return (str((teams.get("home") or {}).get("name", "")),
                    str((teams.get("away") or {}).get("name", "")))
        fighters = (row or {}).get("fighters") or {}
        if fighters:
            return (str((fighters.get("first") or {}).get("name", "")),
                    str((fighters.get("second") or {}).get("name", "")))
        competition = (row or {}).get("competition") or {}
        return (str(competition.get("name", "") if isinstance(competition, dict)
                    else competition or ""), "")

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
        status = (row or {}).get("status") or {}
        if isinstance(status, str):
            # A race's status is a word ("Completed"), not a block.
            return {"short": status.upper(), "long": status}
        return status

    @staticmethod
    def _score(row: dict) -> tuple:
        """Points for each side, whichever shape this sport uses."""
        goals = (row or {}).get("goals")
        if isinstance(goals, dict) and goals.get("home") is not None:
            return goals.get("home"), goals.get("away")
        scores = (row or {}).get("scores") or {}
        home, away = scores.get("home"), scores.get("away")
        if isinstance(home, dict):
            # `total` for most sports; AFL keeps `score` beside goals and
            # behinds (§180).
            def pick(block):
                block = block or {}
                for name in ("total", "score", "points"):
                    if block.get(name) is not None:
                        return block.get(name)
                return None
            home, away = pick(home), pick(away)
        return home, away

    @classmethod
    def live_line(cls, row: dict, sport: Sport) -> tuple:
        """`(status, line)` for a card: the score and where the game is.

        Written in code from the provider's own numbers, never by a model -
        the one place on a browse tile a result may appear (§135), because it
        is read off the scoreboard on every sweep rather than written once
        into a sentence. `unknown` status gives no line at all.
        """
        status_block = cls._status_block(row)
        status = sport.statuses.get(str(status_block.get("short") or "").upper(),
                                    live_facts.UNKNOWN)
        home, away = cls._team_names(row)
        if sport.kind == "race":
            # A race has a field, not two sides: its name and where it is.
            # Practice and qualifying are sessions of the weekend, not the
            # race: one "completed" on Friday is not the race finished.
            if (status == live_facts.UNKNOWN or not home
                    or str((row or {}).get("type") or "race").lower() != "race"):
                return live_facts.UNKNOWN, ""
            if status == live_facts.SCHEDULED:
                when = cls._start_time(row)
                return status, (f"Starts {when}" if when else "Today")
            return status, ("Finished" if status == live_facts.FINAL
                            else "Live \u00b7 under way")
        if status == live_facts.UNKNOWN or not (home and away):
            return live_facts.UNKNOWN, ""
        if sport.kind == "fight":
            if status == live_facts.SCHEDULED:
                when = cls._start_time(row)
                return status, (f"Starts {when}" if when else "Today")
            if status == live_facts.FINAL:
                winner, loser = cls._fight_result(row)
                return status, (f"Final \u00b7 {winner} beat {loser}" if winner
                                else f"Final \u00b7 {home} v {away}")
            return status, f"Live \u00b7 {home} v {away}"
        hs, as_ = cls._score(row)
        scored = hs is not None and as_ is not None
        if status == live_facts.SCHEDULED:
            when = cls._start_time(row)
            return status, (f"Starts {when}" if when else "Today")
        score = f"{home} {hs}\u2013{as_} {away}" if scored else f"{home} v {away}"
        if status == live_facts.FINAL:
            return status, f"Final \u00b7 {score}"
        where = status_block.get("long") or ""
        elapsed = status_block.get("elapsed") or status_block.get("timer")
        if not where and elapsed:
            where = f"{elapsed}'"
        return status, "Live \u00b7 " + score + (f" \u00b7 {where}" if where else "")

    @staticmethod
    def _fight_result(row: dict) -> tuple:
        """`(winner, loser)` off the fighters' own `winner` flags, or
        ("", "") when neither is marked - never inferred from anything else."""
        fighters = (row or {}).get("fighters") or {}
        first, second = fighters.get("first") or {}, fighters.get("second") or {}
        if first.get("winner") is True and second.get("winner") is not True:
            return str(first.get("name", "")), str(second.get("name", ""))
        if second.get("winner") is True and first.get("winner") is not True:
            return str(second.get("name", "")), str(first.get("name", ""))
        return "", ""

    def _event_facts(self, row: dict, entity: Entity,
                     sport: Sport) -> Optional[LiveFacts]:
        """A fight or a race -> LiveFacts (§180). The result is only what the
        provider marks: a fight's `winner` flag, a race's podium from its
        rankings (`podium`, filled in by `fetch`)."""
        status = sport.statuses.get(
            str(self._status_block(row).get("short") or "").upper(),
            live_facts.UNKNOWN)
        first, second = self._team_names(row)
        kick = _kickoff_said(self._kickoff(row), sport)
        said: list = []
        if sport.kind == "fight":
            if not (first and second):
                return None
            if status == live_facts.SCHEDULED:
                said.append(f"{first} and {second} have not fought yet."
                            + (f" The fight is set for {kick}." if kick else ""))
            elif status == live_facts.IN_PROGRESS:
                said.append(f"{first} and {second} are fighting now; there is "
                            "no result yet.")
            elif status == live_facts.FINAL:
                winner, loser = self._fight_result(row)
                if winner:
                    said.append(f"{winner} beat {loser}.")
                else:
                    said.append(f"{first} and {second} have fought; the "
                                "provider names no winner.")
            category = str((row or {}).get("category") or "").strip()
            if said and category:
                said.append(f"It is a {category.lower()} fight.")
        else:
            name = first or "The race"
            circuit = ((row or {}).get("circuit") or {})
            where = str(circuit.get("name", "")) if isinstance(circuit, dict) else ""
            at = f" at {where}" if where else ""
            if status == live_facts.SCHEDULED:
                said.append(f"The {name}{at} has not started yet."
                            + (f" It starts {kick}." if kick else ""))
            elif status == live_facts.IN_PROGRESS:
                said.append(f"The {name}{at} is under way; there is no result yet.")
            elif status == live_facts.FINAL:
                podium = list((row or {}).get("podium") or [])
                if podium:
                    first_place = podium[0]
                    line = f"{first_place[0]} won the {name}{at}"
                    line += f" for {first_place[1]}" if first_place[1] else ""
                    rest = [p[0] for p in podium[1:3] if p[0]]
                    said.append(line + (f", ahead of {' and '.join(rest)}." if rest else "."))
                else:
                    said.append(f"The {name}{at} has finished.")
        if not said:
            return None
        return LiveFacts(domain="sports", source=self.name,
                         as_of=datetime.now(timezone.utc), facts=said,
                         status=status, entity=entity)

    @classmethod
    def _start_time(cls, row: dict) -> str:
        """Kick-off as `HH:MM UTC`, from whichever field this sport uses."""
        at = cls._kickoff(row)
        return at.strftime("%H:%M UTC") if at else ""

    @staticmethod
    def _kickoff(row: dict) -> Optional[datetime]:
        """Kick-off as an aware UTC datetime, or None when unreadable."""
        stamp = None
        for holder in ("game", "fixture"):
            block = (row or {}).get(holder) or {}
            date = block.get("date")
            if isinstance(date, dict):
                stamp = date.get("timestamp") or stamp
            elif block.get("timestamp"):
                stamp = block.get("timestamp")
        stamp = stamp or (row or {}).get("timestamp")
        try:
            return datetime.fromtimestamp(int(stamp), tz=timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            pass
        # A race carries an ISO date with its offset (§180).
        date = (row or {}).get("date")
        if isinstance(date, str) and "T" in date:
            try:
                at = datetime.fromisoformat(date.replace("Z", "+00:00"))
                return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
            except ValueError:
                return None
        return None

    def to_facts(self, row: dict, entity: Entity,
                 sport: Optional[Sport] = None) -> Optional[LiveFacts]:
        """One game row -> LiveFacts. Split out so tests can drive it."""
        sport = sport or SPORTS[settings.api_sports_sport]
        if sport.kind in ("race", "fight"):
            return self._event_facts(row, entity, sport)
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
        # A game that has not started has no score, whatever the row holds: a
        # pre-game tracker's nought-nought is not "level" (9.30 #5).
        if hs is not None and as_ is not None and status != live_facts.SCHEDULED:
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
            kick = self._kickoff(row)
            when = _kickoff_said(kick, sport)
            said.append(f"{home} and {away} have not started yet."
                        + (f" Kick-off is {when}." if when else ""))

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
        subject = (getattr(brief, "subject", "") or getattr(brief, "query", "")).strip()
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
            if best is None:
                return None
            return Entity(domain=self.domain, provider=self.name,
                          id=f"event:{best.get('id') or best.get('slug') or ''}",
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


BUILDERS["sports"]["api-sports"] = ApiSportsSource
BUILDERS["sports"]["sportsdataio"] = SportsDataIOSource
BUILDERS["markets"]["finnhub"] = FinnhubSource
BUILDERS["markets"]["alpha-vantage"] = AlphaVantageSource
BUILDERS["elections"]["polymarket"] = PolymarketSource
BUILDERS["elections"]["ap"] = lambda: _QuoteOnlyElections(
    "AP Elections", "a contract with the Associated Press and their API guide")
BUILDERS["elections"]["ddhq"] = lambda: _QuoteOnlyElections(
    "Decision Desk HQ", "a subscription agreement with DDHQ and an API key")
