"""Player and team reports from BALLDONTLIE, for every league it covers (§271).

What it answers, beside the scoreboard
--------------------------------------
A sports episode's scoreboard (`live_sources.ApiSportsSource`) knows a game:
its score, its period, a team's record. It knows nothing about the people in
it, so "how is Jokic playing" and "is Mahomes playing Sunday" were answered
from three short article excerpts. This module answers them from the league's
own data:

* **who** a player is - their team and position, from the league's roster;
* **season figures** to date, per sport (averages for basketball, totals for
  football, batting or pitching for baseball, goals and assists for hockey
  and soccer, a tennis player's year, a golfer's ranking, a driver's place in
  the championship, a fighter's record);
* **the injury report** - a player's listing, or a whole team's - in every
  league that publishes one.

`live_facts.players` asks it beside the scoreboard, never instead of it, and
the writer gets it as its own block (`live_facts.PLAYER_REPORT`).

The rules it keeps, each from `live_facts`
-----------------------------------------
* **Resolution comes from the provider's catalogue, never a model** - a
  player is the one row of a league's own player search whose whole name
  the question names; two such rows is an ambiguity, and an ambiguity is no
  answer rather than a guess.
* **Absence is said as absence from the report** - "not on the injury
  report", never "healthy" - and only when the report was actually read.
* **What the plan cannot reach is not asked** (`ENDPOINT_TIERS`,
  `BALLDONTLIE_TIER`), and an endpoint that refuses anyway is not asked
  again that day, the way a refused API-Sports catalogue is not (§191).
* **Every request is counted** on /admin (`provider_usage`, per league) and
  paced to the plan's per-minute limit, so a burst of listeners cannot spend
  the minute for everyone.

Where the shapes came from
--------------------------
BALLDONTLIE's own OpenAPI specifications (balldontlie-api/mcp, generated from
their source at commit 3b8fdeee, read 2026-10-15): paths, parameters, field
names, and the plan an endpoint states it needs. Where a spec states none,
injuries and season figures are taken to need ALL-STAR - their published
plans put stats and injuries above Free. **Nothing here has been run against
the live API from this repository** - `verify()` is the first real request.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import live_facts
import live_route
from config import settings
from live_facts import Entity, LiveFacts, LiveSource

log = logging.getLogger(__name__)

NAME = "BALLDONTLIE"


# --------------------------------------------------------------------------
# The leagues
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class League:
    key: str
    #: How the league is said aloud.
    label: str
    #: The API path every endpoint of this league starts with.
    prefix: str
    #: Phrases that name the league in a question, whole words.
    words: tuple
    #: The month its season starts (1-12). A season runs from there for a year.
    season_start: int
    #: Whether a season is named by two years ("2025-26").
    split_season: bool
    #: How its season figures are read - see `PlayerReports._season_facts`.
    stats: str
    #: Whether it publishes an injury report.
    injuries: bool
    #: How list parameters are spelled: "[]" (`player_ids[]=1`) or "".
    lists: str = "[]"
    #: The player-search parameter, and the catalogue path for players.
    search: str = "search"
    players: str = "players"
    #: A tournament run every four years from this year (the World Cup).
    every_four_from: int = 0


LEAGUES = {league.key: league for league in (
    League("nba", "NBA", "/nba/v1", ("nba", "basketball"), 10, True,
           "nba", True),
    League("wnba", "WNBA", "/wnba/v1", ("wnba",), 5, False,
           "basketball", True),
    League("ncaab", "men's college basketball", "/ncaab/v1",
           ("college basketball", "ncaab", "march madness",
            "men's college basketball"), 11, True, "basketball", False),
    League("ncaaw", "women's college basketball", "/ncaaw/v1",
           ("women's college basketball", "ncaaw"), 11, True,
           "basketball", False),
    League("nfl", "NFL", "/nfl/v1", ("nfl", "american football"), 9, False,
           "football", True),
    League("ncaaf", "college football", "/ncaaf/v1",
           ("college football", "ncaaf"), 8, False, "football", False),
    League("mlb", "MLB", "/mlb/v1", ("mlb", "baseball", "major league baseball"),
           3, False, "mlb", True),
    League("cbb", "college baseball", "/cbb/v1", ("college baseball",), 2,
           False, "cbb", False),
    League("nhl", "NHL", "/nhl/v1", ("nhl", "hockey"), 10, True, "nhl", True,
           search="name"),
    League("epl", "Premier League", "/epl/v2", ("premier league", "epl"), 8,
           True, "soccer", True, lists=""),
    League("laliga", "La Liga", "/laliga/v1", ("la liga", "laliga"), 8, True,
           "soccer", True, lists=""),
    League("seriea", "Serie A", "/seriea/v1", ("serie a",), 8, True,
           "soccer", True, lists=""),
    League("bundesliga", "Bundesliga", "/bundesliga/v1", ("bundesliga",), 8,
           True, "soccer", True, lists=""),
    League("ligue1", "Ligue 1", "/ligue1/v1", ("ligue 1", "ligue un"), 8, True,
           "soccer", True, lists=""),
    League("ucl", "Champions League", "/ucl/v1",
           ("champions league", "ucl"), 9, True, "soccer", True, lists=""),
    League("mls", "MLS", "/mls/v1", ("mls", "major league soccer"), 2, False,
           "soccer", True, lists=""),
    League("fifa", "World Cup", "/fifa/worldcup/v1", ("world cup",), 6, False,
           "soccer", True, every_four_from=2026),
    League("atp", "ATP", "/atp/v1", ("atp", "tennis", "men's tennis"), 1,
           False, "tennis", False),
    League("wta", "WTA", "/wta/v1", ("wta", "tennis", "women's tennis"), 1,
           False, "tennis", False),
    League("pga", "PGA Tour", "/pga/v1", ("pga", "golf"), 1, False, "pga",
           False),
    League("f1", "Formula 1", "/f1/v1", ("formula 1", "formula one", "f1"), 3,
           False, "f1", False, players="drivers"),
    League("mma", "UFC", "/mma/v1", ("ufc", "mma"), 1, False, "mma", False,
           players="fighters"),
)}

#: Which leagues an unplaced name is searched in, in order, when the question
#: names none. `BALLDONTLIE_LEAGUES` overrides it.
DEFAULT_ORDER = ("nba", "nfl", "mlb", "nhl", "epl", "wnba", "mls", "laliga",
                 "seriea", "bundesliga", "ligue1", "ucl", "ncaaf", "ncaab",
                 "ncaaw", "atp", "wta", "pga", "f1", "mma", "cbb", "fifa")

#: Leagues whose team catalogue is worth a request when the question names
#: the league: a team's injury report needs the team's id. Tennis, golf, F1
#: and MMA have no team injury report to read.
TEAM_LEAGUES = frozenset(k for k, lg in LEAGUES.items() if lg.injuries)

#: `live_route.ROSTER` leagues -> ours, so a named NFL team places the
#: question in the NFL without a request.
_ROSTER_LEAGUE = {"nfl": "nfl", "nba": "nba", "mlb": "mlb", "nhl": "nhl"}


# --------------------------------------------------------------------------
# Plans
# --------------------------------------------------------------------------
TIER_ORDER = ("free", "all-star", "goat")
#: Requests per minute on each plan, as published.
PER_MINUTE = {"free": 5, "all-star": 60, "goat": 600}

#: The plan each kind of request needs. `players` and `teams` are Free;
#: injuries and season figures need ALL-STAR unless a league's spec says
#: more. `(league, kind)` overrides `kind`.
ENDPOINT_TIERS = {
    "players": "free", "teams": "free",
    "injuries": "all-star", "season": "all-star",
    # Stated in BALLDONTLIE's own specs.
    ("epl", "injuries"): "goat", ("laliga", "injuries"): "goat",
    ("seriea", "injuries"): "goat", ("bundesliga", "injuries"): "goat",
    ("ligue1", "injuries"): "goat", ("ucl", "injuries"): "goat",
    ("mls", "injuries"): "goat", ("fifa", "injuries"): "goat",
    ("fifa", "players"): "goat", ("fifa", "teams"): "goat",
    ("fifa", "season"): "goat",
    ("pga", "season"): "goat", ("atp", "season"): "goat",
    ("wta", "season"): "goat",
    # A fighter's record is on the fighter's own row, and a golfer's world
    # ranking on theirs: both come with the catalogue, Free.
    ("mma", "season"): "free",
}


def tier_needed(league: str, kind: str) -> str:
    return ENDPOINT_TIERS.get((league, kind)) or ENDPOINT_TIERS.get(kind, "goat")


def plan_reaches(league: str, kind: str, tier: Optional[str] = None) -> bool:
    tier = tier or settings.balldontlie_tier
    have = TIER_ORDER.index(tier) if tier in TIER_ORDER else 0
    return have >= TIER_ORDER.index(tier_needed(league, kind))


class TierRefused(RuntimeError):
    """This request needs a plan above the one this deploy has."""


class AccountRefused(RuntimeError):
    """BALLDONTLIE refused the key itself: wrong, revoked or missing.

    `status` makes `live_facts._ask` log it as a sentence, not a crash."""

    status = 401


class RateLimited(RuntimeError):
    """The plan's requests for this minute are spent. Not sent."""

    status = 429


# --------------------------------------------------------------------------
# Words and names
# --------------------------------------------------------------------------
def _fold(text: str) -> str:
    """Lower case, accents off, words joined by single spaces, padded."""
    plain = unicodedata.normalize("NFKD", str(text or ""))
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return " " + " ".join(re.findall(r"[a-z0-9]+", plain.lower())) + " "


def _names(text: str, phrase: str) -> bool:
    folded = _fold(phrase)
    return folded.strip() != "" and folded in _fold(text)


def leagues_named(text: str) -> list:
    """Every league `text` names, most specific first: "college basketball"
    is NCAAB, not the NBA its last word would also name."""
    folded = _fold(text)
    hits = []
    for key, league in LEAGUES.items():
        for word in league.words:
            if _fold(word) in folded:
                hits.append((len(word), key))
                break
    hits.sort(reverse=True)
    out: list = []
    for _, key in hits:
        if key not in out:
            out.append(key)
    # A specific league named means its generic sport word was part of it:
    # "college basketball" also contains "basketball", which is the NBA's.
    specific = {k for k in out if k in ("ncaab", "ncaaw", "ncaaf", "cbb")}
    if specific:
        generic = {"ncaab": "nba", "ncaaw": "nba", "ncaaf": "nfl", "cbb": "mlb"}
        out = [k for k in out if k not in {generic[s] for s in specific}
               or _named_alone(folded, LEAGUES[k])]
    return out


def _named_alone(folded: str, league: League) -> bool:
    """Whether a league's own short name is said, not only its sport word."""
    return any(_fold(w) in folded for w in league.words[:1])


def search_order() -> list:
    named = [k.strip().lower() for k in (settings.balldontlie_leagues or "").split(",")
             if k.strip()]
    known = [k for k in named if k in LEAGUES]
    for key in named:
        if key not in LEAGUES:
            log.error("BALLDONTLIE_LEAGUES names %r, which is not one of %s",
                      key, ", ".join(LEAGUES))
    return known or list(DEFAULT_ORDER)


def full_name(row: dict) -> str:
    """A catalogue row's whole name, whichever shape the league's is."""
    for key in ("full_name", "display_name", "name"):
        value = str((row or {}).get(key) or "").strip()
        if value and " " in value:
            return value
    first = str((row or {}).get("first_name") or "").strip()
    last = str((row or {}).get("last_name") or "").strip()
    joined = " ".join(p for p in (first, last) if p)
    return joined or str((row or {}).get("name") or "").strip()


#: Two or three capitalised words in a row: the shape of a person's name in
#: a subject that is a sentence rather than a name.
_PROPER = re.compile(r"\b([A-Z][\w'.-]+(?:\s+[A-Z][\w'.-]+){1,2})\b")


def names_in(entity: str) -> list:
    """The names a resolution may search for: the bare entity when it is
    short enough to be a name, else the proper names inside it."""
    entity = " ".join((entity or "").split())
    if not entity:
        return []
    if len(entity.split()) <= 4 and not re.search(r"[?!]", entity):
        return [entity]
    return list(dict.fromkeys(m.group(1) for m in _PROPER.finditer(entity)))[:2]


def pick_player(rows: list, wanted: str) -> tuple:
    """`(row, why)`: the one row whose whole name `wanted` names.

    Every word of the row's name must be in `wanted`, whole - "James" alone
    names every James, so a one-word name matches only a row whose last name
    it is, and only when that row is the only one. Two rows that both match
    is an ambiguity and returns no row: a guessed player is somebody else's
    season, said with authority.
    """
    folded = _fold(wanted)
    exact, contained = [], []
    for row in rows or []:
        name = full_name(row)
        if not name:
            continue
        if _fold(name) == folded:
            exact.append(row)
        elif _fold(name) in folded:
            contained.append(row)
        elif len(folded.split()) == 1 and _fold(row.get("last_name") or "") == folded:
            contained.append(row)
    for group in (exact, contained):
        ids = {r.get("id") for r in group}
        if len(ids) == 1:
            return group[0], ""
        if len(ids) > 1:
            active = [r for r in group if r.get("active") is True]
            if len({r.get("id") for r in active}) == 1:
                return active[0], ""
            return None, f"{len(ids)} players named {wanted!r}"
    return None, f"no player named {wanted!r}"


def team_name(team: dict) -> str:
    for key in ("full_name", "display_name", "name"):
        value = str((team or {}).get(key) or "").strip()
        if value:
            return value
    return ""


def pick_team(teams: list, entity: str, context: str) -> Optional[dict]:
    """The team the question names: its full name anywhere in the wording,
    or its short name only when that is the whole entity ("Lakers") - a
    short name inside a sentence is how "Miami heat wave" became a team."""
    found = {}
    for team in teams or []:
        full = str(team.get("full_name") or team.get("display_name") or "").strip()
        short = str(team.get("name") or team.get("short_name") or "").strip()
        if full and _names(context, full):
            found[team.get("id")] = team
        elif short and _fold(short) == _fold(entity):
            found[team.get("id")] = team
    return next(iter(found.values())) if len(found) == 1 else None


# --------------------------------------------------------------------------
# Seasons
# --------------------------------------------------------------------------
def season_for(league: League, now: Optional[datetime] = None) -> int:
    """The season a league is in, or last played, on `now`."""
    now = now or datetime.now(timezone.utc)
    if league.every_four_from:
        year = now.year if now.month >= league.season_start else now.year - 1
        return year - ((year - league.every_four_from) % 4)
    return now.year if now.month >= league.season_start else now.year - 1


def season_label(league: League, season: int) -> str:
    if league.split_season:
        return f"{season}-{(season + 1) % 100:02d}"
    return str(season)


def _number(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _say(value, places: int = 1) -> str:
    """A figure as the facts say it: whole numbers whole, others to `places`."""
    number = _number(value)
    if number is None:
        return ""
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    return f"{number:.{places}f}"


def _percent(value) -> str:
    number = _number(value)
    if number is None:
        return ""
    return _say(number * 100 if number <= 1 else number)


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _date_said(value) -> str:
    """`2026-10-14...` as "October 14, 2026"; anything else as given."""
    text = str(value or "").strip()
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if not match:
        return text
    try:
        day = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return text
    return f"{day.strftime('%B')} {day.day}, {day.year}"


def _clip(text: str, limit: int = 220) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("; "))
    return cut[:end + 1] if end > limit // 2 else cut.rstrip() + "..."


# --------------------------------------------------------------------------
# The source
# --------------------------------------------------------------------------
class PlayerReports(LiveSource):
    """A player's or a team's report, from BALLDONTLIE (§271)."""

    name = NAME
    domain = "sports"
    cost_per_call = 0.0  # a flat monthly plan; the bill is not per call
    delayed_seconds = 0.0
    #: Its own clock: a player is a search, then two reads at once (three
    #: for soccer's season, which sums the season's matches).
    timeout_seconds = 4.0

    def __init__(self) -> None:
        #: entity key -> (league key, "player"|"team", catalogue row)
        self._rows: dict = {}
        #: entity key -> (expires at, LiveFacts)
        self._reports: dict = {}
        #: league -> (fetched at, teams)
        self._teams: dict = {}
        #: (league, season) -> (fetched at, match ids)
        self._matches: dict = {}
        #: (league, kind) -> UTC day it was refused
        self.refused: dict = {}
        self._sent: deque = deque()

    # ---- configuration ------------------------------------------------------
    def diagnose(self) -> tuple[bool, str]:
        if not settings.player_facts:
            return False, "PLAYER_FACTS=0: player and team reports are off"
        if not settings.balldontlie_api_key:
            return False, "BALLDONTLIE_API_KEY is not set"
        tier = settings.balldontlie_tier
        reach = ("players and teams only - injuries and season figures need "
                 "ALL-STAR" if tier == "free" else
                 "players, teams, injuries and season figures (soccer injuries, "
                 "golf and tennis season figures need GOAT)" if tier == "all-star"
                 else "everything this module reads")
        return True, (f"BALLDONTLIE_API_KEY present, plan {tier}: {reach} "
                      "(not verified from this machine)")

    async def verify(self) -> tuple[bool, str]:
        """One Free request, the NBA's teams: proves the key, spends one."""
        try:
            data = await self._get(LEAGUES["nba"], "teams", "teams", {})
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"
        count = len((data or {}).get("data") or [])
        return (count > 0), f"BALLDONTLIE answered with {count} NBA teams"

    # ---- the wire -----------------------------------------------------------
    def _today(self, now: Optional[float] = None) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(time.time() if now is None else now))

    def _pace(self, now: Optional[float] = None) -> None:
        """Hold to the plan's per-minute limit. Raises rather than queueing:
        a listener waiting behind a full minute is a listener told late."""
        now = time.monotonic() if now is None else now
        while self._sent and now - self._sent[0] >= 60.0:
            self._sent.popleft()
        if len(self._sent) >= PER_MINUTE.get(settings.balldontlie_tier, 5):
            raise RateLimited(
                f"BALLDONTLIE's {settings.balldontlie_tier} plan allows "
                f"{PER_MINUTE.get(settings.balldontlie_tier, 5)} requests a "
                "minute and this minute's are spent")
        self._sent.append(now)

    async def _get(self, league: League, kind: str, path: str,
                   params: dict) -> dict:
        """One request. Raises `TierRefused`, `AccountRefused`,
        `RateLimited` or the HTTP error; never answers a refusal as data."""
        import live_sources

        if not plan_reaches(league.key, kind):
            raise TierRefused(
                f"{league.label} {kind} needs BALLDONTLIE's "
                f"{tier_needed(league.key, kind)} plan; this deploy has "
                f"{settings.balldontlie_tier}")
        if self.refused.get((league.key, kind)) == self._today():
            raise TierRefused(f"{league.label} {kind} was refused today")
        self._pace()
        url = f"{settings.balldontlie_base}{league.prefix}/{path}"
        try:
            return await live_sources._json(
                url, {"Authorization": settings.balldontlie_api_key},
                params, float(self.timeout_seconds))
        except live_sources.ProviderHTTPError as exc:
            if exc.status == 401 and kind in ("players", "teams"):
                # A Free endpoint refused: the key itself is the problem.
                raise AccountRefused(
                    f"BALLDONTLIE refused the key ({exc.where})") from None
            if exc.status in (401, 403):
                # A key that reads the catalogue and is refused this is a
                # plan that does not reach it: not asked again today.
                self.refused[(league.key, kind)] = self._today()
                log.warning("live facts: BALLDONTLIE refused %s %s on the %s "
                            "plan; not asked again today", league.key, kind,
                            settings.balldontlie_tier)
                raise TierRefused(f"{league.label} {kind} refused: {exc}") from None
            if exc.status == 429:
                raise RateLimited(f"BALLDONTLIE said 429 for {exc.where}") from None
            raise

    def _list(self, league: League, name: str) -> str:
        return f"{name}{league.lists}"

    # ---- catalogues ---------------------------------------------------------
    async def teams(self, league: League) -> list:
        """A league's teams, from its own catalogue; a day is generous."""
        held = self._teams.get(league.key)
        if held and time.time() - held[0] < 86400.0:
            return held[1]
        params: dict = {"per_page": 100}
        if league.stats == "soccer" and league.key != "fifa":
            params["season"] = season_for(league)
        data = await self._get(league, "teams", "teams", params)
        rows = [r for r in (data or {}).get("data") or [] if isinstance(r, dict)]
        self._teams[league.key] = (time.time(), rows)
        return rows

    async def search(self, league: League, name: str) -> list:
        """Catalogue rows for a name. Searched by its last word: a full name
        with a space is not what every league's search matches."""
        term = name.split()[-1] if name.split() else name
        params = {league.search: term, "per_page": 100}
        data = await self._get(league, "players", league.players, params)
        return [r for r in (data or {}).get("data") or [] if isinstance(r, dict)]

    # ---- resolution ---------------------------------------------------------
    def _candidate_leagues(self, context: str) -> tuple:
        """`(named, roster)`: leagues the wording names, then leagues a
        rostered team in it places it in."""
        named = leagues_named(context)
        roster = [_ROSTER_LEAGUE[lg] for lg, _ in live_route.teams_in(context)
                  if lg in _ROSTER_LEAGUE]
        return named, [k for k in dict.fromkeys(roster) if k not in named]

    async def resolve(self, brief) -> Optional[Entity]:
        entity = live_route.entity_of(brief)
        context = " ".join(filter(None, [live_route.context_of(brief),
                                         str(getattr(brief, "category", "") or "")]))
        named, roster = self._candidate_leagues(context)

        # A team first, where the wording places one: its injury report.
        for key in [k for k in named + roster if k in TEAM_LEAGUES]:
            league = LEAGUES[key]
            try:
                teams = await self.teams(league)
            except TierRefused:
                continue
            team = pick_team(teams, entity, context)
            if team is not None and not self._is_player_name(entity, team):
                return self._remember(league, "team", team)
        for key, (_, teams) in list(self._teams.items()):
            team = pick_team(teams, entity, context)
            if team is not None and key not in named + roster:
                return self._remember(LEAGUES[key], "team", team)

        # Then a player, in the leagues named, else the configured order.
        wanted = names_in(entity)
        if not wanted:
            return None
        leagues = named + roster
        if not leagues:
            leagues = search_order()[:max(1, int(settings.balldontlie_max_searches))]
        leagues = [k for k in leagues if plan_reaches(k, "players")]
        found: list = []
        for name in wanted:
            results = await asyncio.gather(
                *(self.search(LEAGUES[k], name) for k in leagues),
                return_exceptions=True)
            for key, rows in zip(leagues, results):
                if isinstance(rows, AccountRefused):
                    raise rows
                if isinstance(rows, BaseException):
                    log.info("live facts: BALLDONTLIE %s search for %r: %s",
                             key, name, rows)
                    continue
                row, why = pick_player(rows, name)
                if row is not None:
                    found.append((key, row))
                elif why.startswith(tuple("0123456789")):
                    log.info("live facts: BALLDONTLIE %s: %s", key, why)
            if found:
                break
        if len(found) == 1 or (found and named):
            # Several leagues' namesakes are an ambiguity unless the
            # question named the league, whose order then decides.
            key, row = found[0]
            return self._remember(LEAGUES[key], "player", row)
        if found:
            log.info("live facts: BALLDONTLIE found %r in %s; not guessing",
                     wanted, ", ".join(k for k, _ in found))
        return None

    @staticmethod
    def _is_player_name(entity: str, team: dict) -> bool:
        """"Jalen Brunson" is not the Knicks, even when the question also
        names them: a person's name as the entity is a player question."""
        return len(entity.split()) in (2, 3) and not _names(entity, team_name(team)) \
            and entity[:1].isupper()

    def _remember(self, league: League, kind: str, row: dict) -> Entity:
        label = team_name(row) if kind == "team" else full_name(row)
        entity = Entity(domain="sports", provider=self.name,
                        id=f"{league.key}:{kind}:{row.get('id')}", label=label)
        self._rows[entity.key] = (league.key, kind, row)
        return entity

    # ---- the report -----------------------------------------------------------
    async def fetch(self, entity: Entity) -> Optional[LiveFacts]:
        held = self._reports.get(entity.key)
        if held and held[0] > time.time():
            return held[1]
        row = self._rows.get(entity.key)
        if row is None:
            return None
        key, kind, data = row
        league = LEAGUES[key]
        now = datetime.now(timezone.utc)
        if kind == "team":
            facts = await self._team_facts(league, data, now)
        else:
            facts = await self._player_facts(league, data, now)
        if not facts:
            return None
        report = LiveFacts(domain="sports", source=self.name, as_of=now,
                           facts=facts, status=live_facts.UNKNOWN,
                           entity=entity, kind=live_facts.PLAYER_REPORT)
        self._reports[entity.key] = (
            time.time() + max(0, int(settings.balldontlie_cache_seconds)), report)
        return report

    async def _player_facts(self, league: League, row: dict,
                            now: datetime) -> list:
        name = full_name(row)
        parts = await asyncio.gather(
            self._player_injury(league, row, now),
            self._season_facts(league, row, now),
            return_exceptions=True)
        failures = [p for p in parts if isinstance(p, BaseException)
                    and not isinstance(p, TierRefused)]
        facts = [identity_line(league, row)]
        for part in parts:
            if isinstance(part, list):
                facts.extend(part)
        facts = [f for f in facts if f]
        if failures and len(facts) <= 1:
            # Nothing but the roster line, and a read that broke: an outage,
            # not a quiet player.
            raise failures[0]
        for failure in failures:
            log.warning("live facts: BALLDONTLIE %s for %s: %s", league.key,
                        name, failure)
        return facts

    # ---- injuries -------------------------------------------------------------
    async def _injury_rows(self, league: League, params: dict) -> list:
        params = dict(params, per_page=100)
        data = await self._get(league, "injuries", "player_injuries", params)
        return [r for r in (data or {}).get("data") or [] if isinstance(r, dict)]

    async def _player_injury(self, league: League, row: dict,
                             now: datetime) -> list:
        if not league.injuries:
            return []
        name = full_name(row)
        player_id = row.get("id")
        # The NHL's report takes no filter; every other league's takes the player.
        params = {} if league.key == "nhl" else {self._list(league, "player_ids"): [player_id]}
        rows = await self._injury_rows(league, params)
        mine = [r for r in rows if (r.get("player") or {}).get("id") == player_id]
        if not mine:
            return [f"{name} is not on the {league.label} injury report as "
                    f"BALLDONTLIE had it at {_clock(now)}."]
        return [injury_line(league, entry, name) for entry in mine[:2]]

    async def _team_facts(self, league: League, team: dict,
                          now: datetime) -> list:
        """A team's injury report: every listed player, up to eight."""
        name = team_name(team)
        if not league.injuries:
            return []
        team_id = team.get("id")
        params = {} if league.key == "nhl" else {self._list(league, "team_ids"): [team_id]}
        try:
            rows = await self._injury_rows(league, params)
        except TierRefused:
            return []
        mine = [r for r in rows if _team_of_injury(r) == team_id
                or (league.key == "nhl" and _nhl_team_named(r, name))
                or (league.key != "nhl" and _team_of_injury(r) is None)]
        if not mine:
            return [f"The {league.label} injury report lists nobody for the "
                    f"{name} as BALLDONTLIE had it at {_clock(now)}."]
        facts = [f"The {league.label} injury report lists {len(mine)} "
                 f"{'player' if len(mine) == 1 else 'players'} for the {name}."]
        for entry in mine[:8]:
            facts.append(injury_line(league, entry, full_name(entry.get("player") or {})))
        if len(mine) > 8:
            facts.append(f"{len(mine) - 8} more are listed and not named here.")
        return facts

    # ---- season figures -------------------------------------------------------
    async def _season_facts(self, league: League, row: dict,
                            now: datetime) -> list:
        """This season's figures, else last season's - labelled by season,
        so a finished season is never said as this one."""
        if league.stats in ("mma",):
            return mma_lines(row)
        if league.stats == "pga":
            facts = pga_rank_line(row)
            if plan_reaches(league.key, "season"):
                facts += await self._pga_season(league, row, now)
            return facts
        current = season_for(league, now)
        for season in (current, current - (4 if league.every_four_from else 1)):
            lines = await self._season(league, row, season, current)
            if lines:
                return lines
            if league.stats == "tennis":
                break  # career figures have no season to step back to
        return []

    async def _season(self, league: League, row: dict, season: int,
                      current: int) -> list:
        name = full_name(row)
        pid = row.get("id")
        said = season_label(league, season)
        so_far = season == current
        if league.stats == "nba":
            data = await self._get(league, "season", "season_averages",
                                   {"season": season, "player_id": pid})
            stats = _first((data or {}).get("data"))
            return basketball_lines(league, name, stats, said, so_far, averages=True)
        if league.stats == "basketball":
            data = await self._get(league, "season", "player_season_stats",
                                   {"season": season, "player_ids[]": [pid]})
            rows = [r for r in (data or {}).get("data") or [] if isinstance(r, dict)]
            stats = max(rows, key=lambda r: _number(r.get("games_played")) or 0,
                        default=None)
            return basketball_lines(league, name, stats, said, so_far)
        if league.stats == "football":
            path = "season_stats" if league.key == "nfl" else "player_season_stats"
            data = await self._get(league, "season", path,
                                   {"season": season, "player_ids[]": [pid]})
            stats = _regular((data or {}).get("data"))
            return football_lines(league, name, stats, said, so_far)
        if league.stats == "mlb":
            data = await self._get(league, "season", "season_stats",
                                   {"season": season, "player_ids[]": [pid]})
            stats = _regular((data or {}).get("data"))
            return baseball_lines(league, name, stats, said, so_far)
        if league.stats == "cbb":
            batting, pitching = await asyncio.gather(
                self._get(league, "season", "player_season_batting_stats",
                          {"season": season, "player_ids[]": [pid]}),
                self._get(league, "season", "player_season_pitching_stats",
                          {"season": season, "player_ids[]": [pid]}))
            return college_baseball_lines(
                league, name, _first((batting or {}).get("data")),
                _first((pitching or {}).get("data")), said, so_far)
        if league.stats == "nhl":
            data = await self._get(league, "season", f"players/{pid}/season_stats",
                                   {"season": season})
            return hockey_lines(league, name, (data or {}).get("data"), said, so_far)
        if league.stats == "soccer":
            return await self._soccer_season(league, row, season, said, so_far)
        if league.stats == "tennis":
            data = await self._get(league, "season", "player_career_stats",
                                   {"player_ids[]": [pid]})
            return tennis_lines(league, name, _first((data or {}).get("data")))
        if league.stats == "f1":
            data = await self._get(league, "season", "driver_standings",
                                   {"season": season, "driver_ids[]": [pid]})
            return f1_lines(league, name, _first((data or {}).get("data")),
                            season, so_far)
        return []

    async def _soccer_season(self, league: League, row: dict, season: int,
                             said: str, so_far: bool) -> list:
        """Summed from the player's match rows in the season's matches: the
        soccer APIs publish per-match figures and no season line."""
        matches = await self._season_matches(league, season)
        if not matches:
            return []
        pid = row.get("id")
        stats: list = []
        cursor = None
        for _ in range(3):
            params: dict = {self._list(league, "player_ids"): [pid], "per_page": 100}
            if cursor:
                params["cursor"] = cursor
            data = await self._get(league, "season", "player_match_stats", params)
            stats += [r for r in (data or {}).get("data") or []
                      if isinstance(r, dict) and r.get("match_id") in matches]
            cursor = ((data or {}).get("meta") or {}).get("next_cursor")
            if not cursor:
                break
        return soccer_lines(league, full_name(row), stats, said, so_far)

    async def _season_matches(self, league: League, season: int) -> set:
        held = self._matches.get((league.key, season))
        if held and time.time() - held[0] < 6 * 3600.0:
            return held[1]
        ids: set = set()
        cursor = None
        for _ in range(6):
            params: dict = {"per_page": 100}
            if league.key == "fifa":
                params["seasons[]"] = [season]
            else:
                params["season"] = season
            if cursor:
                params["cursor"] = cursor
            data = await self._get(league, "season", "matches", params)
            ids |= {r.get("id") for r in (data or {}).get("data") or []
                    if isinstance(r, dict) and r.get("id") is not None}
            cursor = ((data or {}).get("meta") or {}).get("next_cursor")
            if not cursor:
                break
        self._matches[(league.key, season)] = (time.time(), ids)
        return ids

    async def _pga_season(self, league: League, row: dict,
                          now: datetime) -> list:
        season = season_for(league, now)
        try:
            data = await self._get(league, "season", "player_season_stats",
                                   {"season": season, "player_ids[]": [row.get("id")]})
        except TierRefused:
            return []
        return pga_lines(full_name(row), (data or {}).get("data"), season)


def _clock(now: datetime) -> str:
    import listener_clock

    return listener_clock.say(now, "%H:%M %Z on %A")


def _first(rows) -> Optional[dict]:
    for row in rows or []:
        if isinstance(row, dict):
            return row
    return None


def _regular(rows) -> Optional[dict]:
    """The regular-season row where a provider sends several."""
    rows = [r for r in rows or [] if isinstance(r, dict)]
    regular = [r for r in rows if not r.get("postseason")
               and str(r.get("season_type") or "").lower() not in ("postseason", "3")]
    return (regular or rows or [None])[0]


def _team_of_injury(entry: dict) -> Optional[int]:
    team = entry.get("team") or (entry.get("player") or {}).get("team") or {}
    return team.get("id") if isinstance(team, dict) else None


def _nhl_team_named(entry: dict, name: str) -> bool:
    teams = (entry.get("player") or {}).get("teams") or []
    latest = max((t for t in teams if isinstance(t, dict)),
                 key=lambda t: t.get("season") or 0, default={})
    return _fold(latest.get("full_name") or "") == _fold(name)


# --------------------------------------------------------------------------
# Sentences - one fact each, in words a voice can read
# --------------------------------------------------------------------------
def identity_line(league: League, row: dict) -> str:
    name = full_name(row)
    team = row.get("team") if isinstance(row.get("team"), dict) else None
    if team is None and isinstance(row.get("teams"), list) and row["teams"]:
        team = max((t for t in row["teams"] if isinstance(t, dict)),
                   key=lambda t: t.get("season") or 0, default=None)
    position = str(row.get("position") or "").strip()
    position = position if len(position) > 3 else ""
    where = team_name(team) if team else ""
    if league.stats == "mma":
        weight = (row.get("weight_class") or {}).get("name") or ""
        return f"{name} fights at {weight} in the UFC's listings." if weight else ""
    if where and position:
        return f"{name} is a {position.lower()} for the {where}, per the {league.label}'s roster."
    if where:
        return f"{name} plays for the {where}, per the {league.label}'s roster."
    return ""


def injury_line(league: League, entry: dict, name: str) -> str:
    status = str(entry.get("status") or "").strip()
    what = " ".join(str(entry.get(k) or "").strip()
                    for k in ("side", "type", "detail") if entry.get(k)) \
        or str(entry.get("injury_type") or "").strip()
    note = (entry.get("description") or entry.get("comment")
            or entry.get("short_comment") or "")
    said = f"{name} is listed as {status.lower() or 'injured'} on the {league.label} injury report"
    if what:
        said += f", with {what.lower()}"
    said += "."
    if note:
        said += f" The report says: {_clip(note)}"
        said += "" if said.endswith((".", "...")) else "."
    returning = entry.get("return_date")
    if returning:
        said += f" It gives an expected return of {_date_said(returning)}, which is the team's estimate."
    updated = entry.get("updated_at") or entry.get("date")
    if updated:
        said += f" The listing is dated {_date_said(updated)}."
    return said


def _season_phrase(league: League, said: str, so_far: bool) -> str:
    return (f"So far in the {said} {league.label} season" if so_far
            else f"In the {said} {league.label} season")


def basketball_lines(league: League, name: str, stats: Optional[dict],
                     said: str, so_far: bool, averages: bool = False) -> list:
    if not stats:
        return []
    games = _number(stats.get("games_played")) or 0
    if games <= 0:
        return []
    minutes = stats.get("min")
    per_game = 1.0
    if not averages:
        # Averages or totals is not said by every league's spec: minutes
        # tell them apart, because nobody averages more than a game's length.
        total_minutes = _number(minutes)
        if total_minutes is not None and total_minutes > 60:
            per_game = games
    def avg(key):
        value = _number(stats.get(key))
        return None if value is None else value / per_game
    pts, reb, ast = avg("pts"), avg("reb"), avg("ast")
    if pts is None:
        return []
    line = (f"{_season_phrase(league, said, so_far)}, {name} "
            f"{'has averaged' if so_far else 'averaged'} {_say(pts)} points")
    extra = [f"{_say(reb)} rebounds" if reb is not None else "",
             f"{_say(ast)} assists" if ast is not None else ""]
    extra = [e for e in extra if e]
    if extra:
        line += ", " + " and ".join(extra)
    line += f" a game over {_say(games)} games"
    pct = stats.get("fg_pct")
    if _number(pct):
        line += f", shooting {_percent(pct)} percent from the field"
    return [line + "."]


def football_lines(league: League, name: str, stats: Optional[dict],
                   said: str, so_far: bool) -> list:
    if not stats:
        return []
    n = lambda key: _number(stats.get(key)) or 0  # noqa: E731
    groups = []
    if n("passing_attempts") > 0:
        groups.append(f"{_say(n('passing_yards'))} passing yards, "
                      f"{_say(n('passing_touchdowns'))} touchdown passes and "
                      f"{_say(n('passing_interceptions'))} interceptions")
    if n("rushing_attempts") >= 10 or (n("rushing_yards") > 0 and not groups):
        groups.append(f"{_say(n('rushing_yards'))} rushing yards and "
                      f"{_say(n('rushing_touchdowns'))} rushing touchdowns")
    if n("receptions") > 0:
        groups.append(f"{_say(n('receptions'))} catches for "
                      f"{_say(n('receiving_yards'))} yards and "
                      f"{_say(n('receiving_touchdowns'))} touchdowns")
    if n("total_tackles") > 0 or n("sacks") > 0:
        groups.append(f"{_say(n('total_tackles'))} tackles, "
                      f"{_say(n('sacks'))} sacks and "
                      f"{_say(n('interceptions'))} interceptions")
    if not groups:
        return []
    games = n("games_played")
    tail = f" in {_say(games)} games" if games else ""
    return [f"{_season_phrase(league, said, so_far)}, {name} "
            f"{'has' if so_far else 'had'} {'; '.join(groups[:2])}{tail}."]


def baseball_lines(league: League, name: str, stats: Optional[dict],
                   said: str, so_far: bool) -> list:
    if not stats:
        return []
    n = lambda key: _number(stats.get(key))  # noqa: E731
    lines = []
    lead = f"{_season_phrase(league, said, so_far)}, {name}"
    if (n("batting_ab") or 0) > 0:
        avg = n("batting_avg")
        lines.append(
            f"{lead} {'is batting' if so_far else 'batted'} "
            f"{f'{avg:.3f}'.lstrip('0') if avg is not None else 'without an average'}"
            f" with {_say(n('batting_hr') or 0)} home runs and "
            f"{_say(n('batting_rbi') or 0)} runs batted in over "
            f"{_say(n('batting_gp') or 0)} games.")
    if (n("pitching_ip") or 0) > 0:
        era = n("pitching_era")
        lines.append(
            f"{lead} {'has' if so_far else 'had'} {_say(n('pitching_w') or 0)} wins "
            f"and {_say(n('pitching_l') or 0)} losses as a pitcher, with "
            f"{f'a {era:.2f} earned run average, ' if era is not None else ''}"
            f"{_say(n('pitching_k') or 0)} strikeouts in "
            f"{_say(n('pitching_ip'))} innings.")
    return lines


def college_baseball_lines(league: League, name: str, batting: Optional[dict],
                           pitching: Optional[dict], said: str,
                           so_far: bool) -> list:
    lines = []
    lead = f"{_season_phrase(league, said, so_far)}, {name}"
    if batting and (_number(batting.get("at_bats")) or 0) > 0:
        avg = _number(batting.get("avg"))
        lines.append(
            f"{lead} {'is batting' if so_far else 'batted'} "
            f"{f'{avg:.3f}'.lstrip('0') if avg is not None else 'without an average'}"
            f" with {_say(batting.get('home_runs') or 0)} home runs and "
            f"{_say(batting.get('rbi') or 0)} runs batted in.")
    if pitching and (_number(pitching.get("innings_pitched")) or 0) > 0:
        era = _number(pitching.get("era"))
        lines.append(
            f"{lead} {'has' if so_far else 'had'} "
            f"{f'a {era:.2f} earned run average and ' if era is not None else ''}"
            f"{_say(pitching.get('strikeouts') or 0)} strikeouts in "
            f"{_say(pitching.get('innings_pitched'))} innings pitched.")
    return lines


#: NHL season figures arrive as name/value pairs; these are said, in this
#: order, matched on the name with case and punctuation ignored.
_HOCKEY = (
    (("gamesplayed", "gp"), "games"),
    (("goals", "g"), "goals"),
    (("assists", "a"), "assists"),
    (("points", "pts"), "points"),
    (("plusminus",), "plus-minus"),
    (("wins", "w"), "wins as a goalie"),
    (("savepct", "savepercentage", "savepctg"), "save percentage"),
    (("goalsagainstaverage", "gaa"), "goals-against average"),
    (("shutouts", "so"), "shutouts"),
)


def hockey_lines(league: League, name: str, pairs, said: str,
                 so_far: bool) -> list:
    values = {}
    for pair in pairs or []:
        if isinstance(pair, dict) and pair.get("value") is not None:
            values[re.sub(r"[^a-z]", "", str(pair.get("name") or "").lower())] = pair["value"]
    said_parts = []
    for keys, label in _HOCKEY:
        for key in keys:
            if key in values:
                value = values[key]
                if label == "save percentage":
                    said_parts.append(f"a {_say(value, 3)} {label}")
                elif label == "goals-against average":
                    said_parts.append(f"a {_say(value, 2)} {label}")
                else:
                    said_parts.append(f"{_say(value)} {label}")
                break
    if not said_parts:
        return []
    return [f"{_season_phrase(league, said, so_far)}, {name}'s figures are "
            f"{', '.join(said_parts)}."]


def soccer_lines(league: League, name: str, rows: list, said: str,
                 so_far: bool) -> list:
    played = [r for r in rows or [] if (_number(r.get("minutes_played")) or 0) > 0
              or (_number(r.get("appearances")) or 0) > 0]
    if not played:
        return []
    total = lambda key: sum(_number(r.get(key)) or 0 for r in played)  # noqa: E731
    line = (f"{_season_phrase(league, said, so_far)}, {name} "
            f"{'has' if so_far else 'had'} {_say(total('goals'))} goals and "
            f"{_say(total('assists'))} assists in {len(played)} "
            f"{'appearance' if len(played) == 1 else 'appearances'}, "
            f"{_say(total('minutes_played'))} minutes in all")
    cards = []
    if total("yellow_cards"):
        cards.append(f"{_say(total('yellow_cards'))} yellow cards")
    if total("red_cards"):
        cards.append(f"{_say(total('red_cards'))} red cards")
    if cards:
        line += ", with " + " and ".join(cards)
    lines = [line + "."]
    saves = total("goalkeeper_saves") or total("saves")
    if saves and any(r.get("lineup_position") == "G" for r in played):
        lines.append(f"As a goalkeeper, {name} has made {_say(saves)} saves "
                     f"in those matches.")
    return lines


def tennis_lines(league: League, name: str, stats: Optional[dict]) -> list:
    if not stats:
        return []
    n = lambda key: _number(stats.get(key))  # noqa: E731
    lines = []
    if n("ytd_wins") is not None and n("ytd_losses") is not None:
        line = (f"This year on the {league.label} tour, {name} has won "
                f"{_say(n('ytd_wins'))} matches and lost {_say(n('ytd_losses'))}")
        if n("ytd_titles"):
            line += f", with {_say(n('ytd_titles'))} titles"
        lines.append(line + ".")
    if n("career_titles") is not None:
        lines.append(f"{name} has {_say(n('career_titles'))} career singles titles.")
    return lines


def pga_rank_line(row: dict) -> list:
    rank = _number(row.get("owgr"))
    if not rank:
        return []
    return [f"{full_name(row)} is ranked {_ordinal(int(rank))} in the Official "
            f"World Golf Ranking."]


#: Golf's season figures are one row per statistic; these are said.
_GOLF = ("scoring average", "fedexcup", "wins", "top 10", "official money")


def pga_lines(name: str, rows, season: int) -> list:
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        stat = str(row.get("stat_name") or "")
        if not any(word in stat.lower() for word in _GOLF):
            continue
        value = next((v.get("statValue") for v in row.get("stat_value") or []
                      if isinstance(v, dict) and v.get("statValue")), None)
        if value is None:
            continue
        rank = row.get("rank")
        out.append(f"In {season}, {name}'s {stat} is {value}"
                   + (f", {_ordinal(int(rank))} on tour" if isinstance(rank, int) else "")
                   + ".")
        if len(out) >= 4:
            break
    return out


def f1_lines(league: League, name: str, row: Optional[dict], season: int,
             so_far: bool) -> list:
    if not row or row.get("position") is None:
        return []
    verb = "is" if so_far else "finished"
    return [f"In the {season} Formula 1 drivers' championship, {name} {verb} "
            f"{_ordinal(int(row['position']))} with {_say(row.get('points') or 0)} "
            f"points."]


def mma_lines(row: dict) -> list:
    wins, losses = row.get("record_wins"), row.get("record_losses")
    if wins is None or losses is None:
        return []
    draws = row.get("record_draws") or 0
    line = (f"{full_name(row) or row.get('name')}'s professional record is "
            f"{_say(wins)} wins, {_say(losses)} losses and {_say(draws)} draws")
    if row.get("record_no_contests"):
        line += f", with {_say(row['record_no_contests'])} no contests"
    return [line + "."]
