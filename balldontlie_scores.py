"""The scoreboard, from BALLDONTLIE, for every league it covers (§277).

This is FAM's only sports provider (§277, §280, at the owner's direction):
the game a question is about, its state and score, both teams' records and
places in the standings, their last and next games, a Formula 1 weekend, a UFC
card - and the myFAM sweep that puts today's games on tiles with a live score
(`Signals`). Player figures and injury reports are `balldontlie.PlayerReports`
(§275); both share one account's client (`balldontlie.shared_client`).

The rules are the scoreboard's (§88, §259):

* **The status is the provider's, mapped at the boundary.** Every BALLDONTLIE
  game carries `status_state`, a normalised lifecycle (`scheduled`,
  `in_progress`, `final`, `postponed` ...) that maps onto FAM's closed
  vocabulary; anything else is `unknown`, and `unknown` says nothing.
* **A stuck feed is not believed** - "live" long past a game's length, or
  "not started" hours after its start, reads as `unknown` (`believable`).
* **The game is the provider's own row**, found from the teams the question
  names in the league's own catalogue; no row in the window is no game, never
  the nearest one.
* **The sentences are the scoreboard's shape**, word for word, because
  ScoreGuard reads them: "X lead Y 21 to 14.", "X beat Y 24 to 17.",
  "X and Y are level at 1 goals each."

Written from BALLDONTLIE's OpenAPI specs (see `balldontlie.py`); **never run
against the live API from this repository**.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import balldontlie
import live_facts
import live_route
import named_slots
from balldontlie import LEAGUES, League, RateLimited, TierRefused
from config import settings
from live_facts import Entity, LiveFacts, LiveSource

log = logging.getLogger(__name__)

NAME = "BALLDONTLIE scores"


# --------------------------------------------------------------------------
# How each league's games are shaped
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Shape:
    #: `games` or `matches`.
    path: str
    #: Query parameter names: the dates, the teams and the season.
    dates: str = "dates[]"
    teams: str = "team_ids[]"
    seasons: str = "seasons[]"
    #: Whether `seasons` takes a list or one value.
    season_list: bool = True
    #: What a score is called aloud.
    unit: str = "points"
    #: How long one game can plausibly run, start to finish, in hours.
    max_live_hours: float = 4.5
    #: How the period is said: quarter, half, inning, period or ''.
    period: str = "quarter"


_SOCCER = Shape("matches", dates="dates", teams="team_ids", seasons="season",
                season_list=False, unit="goals", max_live_hours=3.0, period="")

SHAPES = {
    "nba": Shape("games"),
    "wnba": Shape("games"),
    "ncaab": Shape("games", period="half"),
    "ncaaw": Shape("games"),
    "nfl": Shape("games", max_live_hours=5.0, period=""),
    "ncaaf": Shape("games", max_live_hours=5.0),
    "mlb": Shape("games", unit="runs", max_live_hours=6.0, period="inning"),
    "cbb": Shape("games", unit="runs", max_live_hours=6.0, period="inning"),
    "nhl": Shape("games", dates="dates", seasons="seasons", unit="goals",
                 period="period"),
    "epl": _SOCCER, "laliga": _SOCCER, "seriea": _SOCCER,
    "bundesliga": _SOCCER, "ligue1": _SOCCER, "ucl": _SOCCER, "mls": _SOCCER,
    "fifa": Shape("matches", dates="", teams="team_ids[]", seasons="seasons[]",
                  unit="goals", max_live_hours=3.5, period=""),
}

#: Leagues with a race weekend or a fight card rather than games.
EVENT_LEAGUES = ("f1", "mma")

#: How long a game may still say "not started" after its start (§259).
STUCK_SCHEDULED_HOURS = 4.0

#: The game the question means is in this window around now, in UTC days.
WINDOW_DAYS = (-1, 0, 1)

_STATE = {
    "scheduled": live_facts.SCHEDULED,
    "in_progress": live_facts.IN_PROGRESS,
    "final": live_facts.FINAL,
}
#: States that are not a game being played or finished, said as what they are.
_OFF = {"postponed": "postponed", "canceled": "cancelled",
        "cancelled": "cancelled", "suspended": "suspended",
        "abandoned": "abandoned", "delayed": "delayed"}


# --------------------------------------------------------------------------
# Reading a row
# --------------------------------------------------------------------------
def raw_state(row: dict) -> str:
    state = (row or {}).get("status_state")
    if isinstance(state, dict):
        state = state.get("state") or state.get("name") or state.get("value")
    return str(state or "").strip().lower()


def status_of(row: dict) -> str:
    """The game's status in FAM's closed vocabulary - only from the
    provider's own normalised state; anything else is `unknown`."""
    return _STATE.get(raw_state(row), live_facts.UNKNOWN)


def start_of(row: dict) -> Optional[datetime]:
    """When it starts, as an aware UTC datetime, or None when unreadable.
    A date with no time is not a start time."""
    for key in ("datetime", "start_time_utc", "date"):
        text = str((row or {}).get(key) or "").strip()
        if not text or "T" not in text:
            continue
        try:
            when = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            continue
        return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return None


def believable(status: str, row: dict, shape: Shape,
               now: Optional[datetime] = None) -> str:
    """A stuck scoreboard reads as `unknown` (§259)."""
    now = now or datetime.now(timezone.utc)
    start = start_of(row)
    if start is None:
        return status
    if status == live_facts.IN_PROGRESS and now - start > timedelta(hours=shape.max_live_hours):
        return live_facts.UNKNOWN
    if status == live_facts.SCHEDULED and now - start > timedelta(hours=STUCK_SCHEDULED_HOURS):
        return live_facts.UNKNOWN
    return status


def sides(row: dict, teams: Optional[dict] = None) -> tuple:
    """`(home, away)` team dicts, whatever the league calls them. Soccer
    rows carry ids only, so their names come from the league's catalogue."""
    row = row or {}
    home = row.get("home_team")
    away = row.get("visitor_team") or row.get("away_team")
    if not isinstance(home, dict) and row.get("home_team_id") is not None:
        home = (teams or {}).get(row["home_team_id"], {"id": row["home_team_id"]})
    if not isinstance(away, dict) and row.get("away_team_id") is not None:
        away = (teams or {}).get(row["away_team_id"], {"id": row["away_team_id"]})
    return (home if isinstance(home, dict) else {}), (away if isinstance(away, dict) else {})


def team_said(team: dict) -> str:
    for key in ("full_name", "display_name", "name"):
        value = str((team or {}).get(key) or "").strip()
        if value:
            return value
    return ""


def score_of(row: dict) -> tuple:
    row = row or {}
    pairs = (("home_team_score", "visitor_team_score"),
             ("home_score", "away_score"))
    for h, a in pairs:
        if row.get(h) is not None and row.get(a) is not None:
            return _int(row[h]), _int(row[a])
    home = (row.get("home_team_data") or {}).get("runs")
    away = (row.get("away_team_data") or {}).get("runs")
    if home is not None and away is not None:
        return _int(home), _int(away)
    return None, None


def _int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def where_said(row: dict, shape: Shape) -> str:
    """Where a game under way stands, from the provider's own fields only.
    "" when it does not say - the writer then names no period, which is what
    ScoreGuard requires without a stated one."""
    row = row or {}
    detail = str(row.get("period_detail") or row.get("status_detail")
                 or "").strip()
    if detail and not re.fullmatch(r"(?i)(in progress|live|in_progress)", detail):
        return detail
    period = _int(row.get("period"))
    if period and shape.period:
        if row.get("in_intermission"):
            return f"the intermission after the {_ordinal(period)} {shape.period}"
        clock = str(row.get("time_remaining") or row.get("time")
                    or row.get("display_clock") or "").strip()
        said = f"the {_ordinal(period)} {shape.period}"
        return f"{said}, {clock} left" if clock and shape.period != "inning" else said
    return ""


def game_facts(league: League, row: dict, status: str,
               teams: Optional[dict] = None) -> list:
    """One game as the scoreboard's sentences (the shapes ScoreGuard reads)."""
    shape = SHAPES[league.key]
    home_t, away_t = sides(row, teams)
    home = team_said(home_t) or "the home side"
    away = team_said(away_t) or "the away side"
    hs, as_ = score_of(row)
    said: list = []
    if hs is not None and as_ is not None and status in (
            live_facts.IN_PROGRESS, live_facts.FINAL):
        first, second, lead, trail = home, away, hs, as_
        if as_ > hs:
            first, second, lead, trail = away, home, as_, hs
        if lead == trail:
            said.append(f"{home} and {away} are level at {lead} {shape.unit} each.")
        elif status == live_facts.FINAL:
            said.append(f"{first} beat {second} {lead} to {trail}.")
        else:
            said.append(f"{first} lead {second} {lead} to {trail}.")
    if status == live_facts.IN_PROGRESS:
        where = where_said(row, shape)
        if where:
            said.append(f"They are in {where}.")
    elif status == live_facts.SCHEDULED:
        when = _when(start_of(row))
        said.append(f"{home} and {away} have not started yet."
                    + (f" It starts {when}." if when else ""))
    elif status == live_facts.UNKNOWN:
        off = _OFF.get(raw_state(row))
        if off:
            said.append(f"The {league.label} lists the game between {home} "
                        f"and {away} as {off}, so there is no result to give.")
        else:
            said.append(f"The scoreboard does not say whether {home} and "
                        f"{away} have started, are playing or have finished, "
                        "so there is no score or period to give.")
    return said


def live_line(league: League, row: dict, teams: Optional[dict] = None) -> tuple:
    """`(status, line)` for a tile, in the tiles' one format: "Live ·
    Chiefs 21–14 Bills · the 3rd quarter", "Final · ...", "Starts 20:15 UTC".
    Unknown gives no line."""
    shape = SHAPES[league.key]
    status = believable(status_of(row), row, shape)
    home_t, away_t = sides(row, teams)
    home, away = team_said(home_t), team_said(away_t)
    if status == live_facts.UNKNOWN or not (home and away):
        return live_facts.UNKNOWN, ""
    if status == live_facts.SCHEDULED:
        start = start_of(row)
        return status, (f"Starts {start:%H:%M} UTC" if start else "Today")
    hs, as_ = score_of(row)
    score = f"{home} {hs}–{as_} {away}" if hs is not None and as_ is not None \
        else f"{home} v {away}"
    if status == live_facts.FINAL:
        return status, f"Final · {score}"
    where = where_said(row, shape)
    return status, "Live · " + score + (f" · {where}" if where else "")


def _when(at: Optional[datetime]) -> str:
    """On the listener's clock ([listener-clock]), never the server's."""
    if at is None:
        return ""
    import listener_clock

    return listener_clock.say(at, "on %A %d %B at %H:%M %Z")


def _season_param(shape: Shape, season: int) -> dict:
    return {shape.seasons: [season] if shape.season_list else season}


# --------------------------------------------------------------------------
# Standings and schedules, read once and shared
# --------------------------------------------------------------------------
#: league -> (fetched at, {team id: team row}). A catalogue changes once a year.
TEAMS: dict = {}
TEAMS_SECONDS = 86400.0
#: league -> (fetched at, {team id: standings row})
STANDINGS: dict = {}
STANDINGS_SECONDS = 3 * 3600.0
#: (league, team id) -> (fetched at, rows)
SCHEDULES: dict = {}
SCHEDULE_SECONDS = 1800.0
#: league -> {UTC date: (fetched at, rows)}: the sweep's cards (`Signals`),
#: read by the lookup before it spends a request of its own.
CARDS: dict = {}
#: How old a swept card may be and still answer a lookup (§281): a row is
#: stamped with the sweep's time, and the freshness check withholds a score
#: older than `live_facts.MAX_AGE_SECONDS["sports"]`, so a card older than
#: that less a margin for the episode's own seconds is asked again instead.
CARD_REUSE_SECONDS = live_facts.MAX_AGE_SECONDS["sports"] - 20.0
#: How long one lookup's window is reused (§281): `resolve` reads it and
#: `fetch` reads it again a second later - one request, not two.
WINDOW_REUSE_SECONDS = 15.0
#: Pages of a schedule or a tournament read before stopping (§281): a
#: baseball team's season and a World Cup run past one page of a hundred.
PAGES = 3


async def _pages(client, league: League, kind: str, path: str,
                 params: dict) -> list:
    """Every row, following `next_cursor` up to `PAGES` (§281)."""
    rows: list = []
    cursor = None
    for _ in range(PAGES):
        asked = dict(params, cursor=cursor) if cursor else dict(params)
        data = await client.get(league, kind, path, asked)
        rows += [r for r in (data or {}).get("data") or [] if isinstance(r, dict)]
        cursor = ((data or {}).get("meta") or {}).get("next_cursor")
        if not cursor:
            break
    return rows


#: §290: BALLDONTLIE's own catalogue of a league API-Sports answers for, kept
#: apart so its ids never reach the scoreboard and API-Sports' never reach
#: the player reports. Read by nobody else.
SHADOW_TEAMS: dict = {}


async def league_teams(client, league: League) -> dict:
    import api_sports

    store = TEAMS
    if api_sports.serves(league.key) and not api_sports.answers_for(client):
        store = SHADOW_TEAMS
    held = store.get(league.key)
    if held and time.time() - held[0] < TEAMS_SECONDS:
        return held[1]
    params: dict = {"per_page": 100}
    if SHAPES.get(league.key) is _SOCCER:
        params["season"] = balldontlie.season_for(league)
    data = await client.get(league, "teams", "teams", params)
    rows = {r.get("id"): r for r in (data or {}).get("data") or []
            if isinstance(r, dict) and r.get("id") is not None}
    store[league.key] = (time.time(), rows)
    return rows


async def league_standings(client, league: League) -> dict:
    held = STANDINGS.get(league.key)
    if held and time.time() - held[0] < STANDINGS_SECONDS:
        return held[1]
    season = balldontlie.season_for(league)
    if league.key == "fifa":
        path, params = "group_standings", {"seasons[]": [season]}
    elif league.key == "ncaaf":
        # Its table needs a conference id the teams do not carry.
        return {}
    else:
        path, params = "standings", {"season": season}
    data = await client.get(league, "standings", path, params)
    rows = {}
    for row in (data or {}).get("data") or []:
        team = (row or {}).get("team") or {}
        if isinstance(team, dict) and team.get("id") is not None:
            rows[team["id"]] = row
    STANDINGS[league.key] = (time.time(), rows)
    return rows


async def team_schedule(client, league: League, team_id,
                        max_age: float = SCHEDULE_SECONDS) -> list:
    """A team's season, every page (§281). `max_age` is how old a held copy
    may be: a game's state read off it must be as fresh as a score is."""
    key = (league.key, team_id)
    held = SCHEDULES.get(key)
    if held and time.time() - held[0] < min(max_age, SCHEDULE_SECONDS):
        return held[1]
    shape = SHAPES[league.key]
    params = {shape.teams: [team_id], "per_page": 100}
    params.update(_season_param(shape, balldontlie.season_for(league)))
    rows = await _pages(client, league, "games", shape.path, params)
    rows.sort(key=lambda r: start_of(r) or datetime.max.replace(tzinfo=timezone.utc))
    SCHEDULES[key] = (time.time(), rows)
    return rows


def standing_facts(league: League, team: dict, row: Optional[dict]) -> list:
    """A team's record and place, as the league's own table has them."""
    if not row:
        return []
    name = team_said(team) or team_said(row.get("team") or {})
    if not name:
        return []
    wins = _int(row.get("wins", row.get("won")))
    losses = _int(row.get("losses", row.get("lost")))
    if wins is None or losses is None:
        return []
    extra = []
    for key, word in (("ties", "tied"), ("draws", "drawn"), ("drawn", "drawn"),
                      ("ot_losses", "lost in overtime")):
        value = _int(row.get(key))
        if value:
            extra.append(f"{value} {word}")
    said = [f"The {name} have won {wins} and lost {losses}"
            + (f", with {' and '.join(extra)}" if extra else "")
            + " this season, per the league's standings."]
    place = ""
    if row.get("rank") is not None:
        group = str(row.get("group_name") or league.label)
        place = f"{_ordinal(_int(row['rank']) or 0)} in the {group}"
        if row.get("points") is not None:
            place += f" with {_int(row['points'])} points"
    elif row.get("position") is not None and isinstance(row.get("group"), dict):
        place = (f"{_ordinal(_int(row['position']) or 0)} in "
                 f"{row['group'].get('name', 'their group')}"
                 + (f" with {_int(row['points'])} points"
                    if row.get("points") is not None else ""))
    elif row.get("division_rank") is not None and row.get("division_name"):
        place = f"{_ordinal(_int(row['division_rank']) or 0)} in the {row['division_name']}"
    elif row.get("conference_rank") is not None:
        conference = str((row.get("team") or {}).get("conference") or "").strip()
        conference = {"East": "Eastern", "West": "Western"}.get(conference, conference)
        if conference:
            place = (f"{_ordinal(_int(row['conference_rank']) or 0)} in the "
                     f"{conference} Conference")
    elif row.get("points") is not None and row.get("division_name"):
        place = f"on {_int(row['points'])} points in the {row['division_name']}"
    elif row.get("playoff_seed") is not None:
        place = f"seeded {_ordinal(_int(row['playoff_seed']) or 0)}"
    if place:
        said.append(f"The {name} are {place}.")
    return said


def schedule_facts(league: League, team: dict, rows: list, skip_id=None,
                   teams: Optional[dict] = None,
                   now: Optional[datetime] = None) -> list:
    """Its last finished game and its next one, off its own schedule."""
    now = now or datetime.now(timezone.utc)
    name = team_said(team)
    tid = team.get("id")
    if not name or tid is None:
        return []
    finals, upcoming = [], []
    for row in rows:
        if row.get("id") == skip_id:
            continue
        home, away = sides(row, teams)
        if tid not in (home.get("id"), away.get("id")):
            continue
        status = status_of(row)
        if status == live_facts.FINAL:
            finals.append(row)
        elif status == live_facts.SCHEDULED and (start_of(row) or now) >= now - timedelta(hours=6):
            upcoming.append(row)
    said = []
    if finals:
        row = finals[-1]
        home, away = sides(row, teams)
        mine_home = home.get("id") == tid
        other = team_said(away if mine_home else home)
        hs, as_ = score_of(row)
        when = _when(start_of(row))
        if other and hs is not None and as_ is not None:
            mine, theirs = (hs, as_) if mine_home else (as_, hs)
            if mine > theirs:
                said.append(f"Their last game: the {name} beat the {other} "
                            f"{mine} to {theirs}{(' ' + when) if when else ''}.")
            elif mine < theirs:
                said.append(f"Their last game: the {name} lost to the {other} "
                            f"{theirs} to {mine}{(' ' + when) if when else ''}.")
            else:
                said.append(f"Their last game: the {name} and the {other} tied "
                            f"{mine} each{(' ' + when) if when else ''}.")
    if upcoming:
        row = upcoming[0]
        home, away = sides(row, teams)
        other = team_said(away if home.get("id") == tid else home)
        when = _when(start_of(row))
        if other:
            said.append(f"Their next game has not been played yet: the {name} "
                        f"play the {other}{(' ' + when) if when else ''}.")
    return said


# --------------------------------------------------------------------------
# The live source
# --------------------------------------------------------------------------
class ScoreBoard(LiveSource):
    """The game a sports question is about, from BALLDONTLIE (§277)."""

    name = NAME
    domain = "sports"
    cost_per_call = 0.0
    delayed_seconds = 0.0
    #: A game is found, then fetched with its standings and schedules beside
    #: it: several requests, each paced, so its own clock.
    timeout_seconds = 3.5

    def __init__(self, client=None) -> None:
        self.client = client or balldontlie.Client()
        #: entity key -> (league key, kind, payload)
        self._rows: dict = {}
        #: (league, days, team ids) -> (read at, rows): one lookup's window,
        #: read by `resolve` and again by `fetch` (§281).
        self._windows: dict = {}

    def diagnose(self) -> tuple[bool, str]:
        import api_sports

        if not settings.balldontlie_api_key and not api_sports.served():
            return False, "BALLDONTLIE_API_KEY is not set"
        problem = getattr(self.client, "problem", lambda: "")()
        said = (f"BALLDONTLIE_API_KEY present, plan "
                f"{settings.balldontlie_tier}: games for "
                f"{len(SHAPES)} leagues, Formula 1 and UFC (not verified "
                "from this machine)") if settings.balldontlie_api_key else \
            "BALLDONTLIE_API_KEY is not set: no Formula 1 or UFC"
        # §290: API-Sports' free plans answer some leagues while developing.
        extra = api_sports.describe()
        return True, (said + (f"; {extra}" if extra else "")
                      + (f"; ACCOUNT REFUSED: {problem}" if problem else ""))

    def _source(self, league_key: str) -> str:
        """Who answered for this league (§290): provenance names the
        provider read, never the scoreboard's name alone."""
        named = getattr(self.client, "source_for", lambda k: "")(league_key)
        return named or self.name

    async def verify(self) -> tuple[bool, str]:
        try:
            data = await self.client.get(LEAGUES["nba"], "teams", "teams", {})
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"
        count = len((data or {}).get("data") or [])
        who = self._source("nba").replace(" scores", "")
        return count > 0, f"{who} answered with {count} NBA teams"

    # ---- finding the game ---------------------------------------------------
    async def resolve(self, brief) -> Optional[Entity]:
        entity = live_route.entity_of(brief)
        context = " ".join(filter(None, [live_route.context_of(brief),
                                         str(getattr(brief, "category", "") or "")]))
        slot = named_slots.by_key(getattr(brief, "named_slot", "") or "")
        named = balldontlie.leagues_named(context)
        roster = [lg for lg, _ in live_route.teams_in(context)]
        if slot is not None:
            named = [slot.league.lower()] + [k for k in named if k != slot.league.lower()]
        if named and named[0] in EVENT_LEAGUES:
            return await (self._resolve_race() if named[0] == "f1"
                          else self._resolve_card())
        leagues = [k for k in dict.fromkeys(named + roster) if k in SHAPES]
        if not leagues:
            # Nothing named: the leagues an unplaced name is searched in.
            leagues = [k for k in balldontlie.search_order() if k in SHAPES][
                :max(1, int(settings.balldontlie_max_searches))]
        for key in leagues:
            league = LEAGUES[key]
            try:
                catalogue = await league_teams(self.client, league)
            except TierRefused:
                continue
            found = teams_named(list(catalogue.values()), context, entity,
                                nicknames=key in named or key in roster)
            if not found and slot is None:
                continue
            row = await self._find_game(league, found, slot, catalogue)
            if row is not None:
                home, away = sides(row, catalogue)
                entity_ = Entity(domain="sports", provider=self.name,
                                 id=f"{key}:game:{row.get('id')}",
                                 label=f"{team_said(away)} at {team_said(home)}",
                                 starts_at=start_of(row))
                self._rows[entity_.key] = (key, "game", {
                    "row": row, "teams": [t.get("id") for t in found]
                    or [home.get("id"), away.get("id")]})
                return entity_
        return None

    async def _window(self, league: League, team_ids: list) -> list:
        """The games in `WINDOW_DAYS`, the sweep's card first."""
        return (await self._window_read(league, team_ids))[0]

    async def _window_read(self, league: League, team_ids: list) -> tuple:
        """`(rows, read at)`: a row off the sweep's card is as old as the
        sweep (§135), so the freshness check that withholds a stale score
        reads the sweep's time, never this moment."""
        shape = SHAPES[league.key]
        today = datetime.now(timezone.utc).date()
        days = [(today + timedelta(days=d)).isoformat() for d in WINDOW_DAYS]
        held = CARDS.get(league.key, {})
        # §281: only a card a score off it would still be fresh from - it
        # was ten minutes, and from two to ten every game on it was withheld.
        cards = [(at, rows) for d in days for at, rows in [held.get(d, (0, []))]
                 if time.time() - at < CARD_REUSE_SECONDS]
        swept = [r for _, rows in cards for r in rows]
        if team_ids and swept and any(_has_team(r, team_ids) for r in swept):
            return swept, min(at for at, rows in cards if rows)
        key = (league.key, tuple(days), tuple(sorted(team_ids or [])))
        for asked in (key, key[:2] + ((),)):
            # This lookup's own window, or an unfiltered one, read seconds ago.
            at, rows = self._windows.get(asked, (0.0, []))
            if time.time() - at < WINDOW_REUSE_SECONDS:
                return rows, at
        params: dict = {"per_page": 100}
        if shape.dates:
            params[shape.dates] = days
        else:
            params.update(_season_param(shape, balldontlie.season_for(league)))
        if team_ids:
            params[shape.teams] = team_ids
        if shape.dates:
            data = await self.client.get(league, "games", shape.path, params)
            rows = [r for r in (data or {}).get("data") or [] if isinstance(r, dict)]
        else:
            # A tournament has no date filter: its season, every page (§281).
            rows = await _pages(self.client, league, "games", shape.path, params)
            now = datetime.now(timezone.utc)
            rows = [r for r in rows if start_of(r) and abs(
                (start_of(r) - now).total_seconds()) < 2 * 86400]
        at = time.time()
        self._windows = {k: v for k, v in self._windows.items()
                         if at - v[0] < WINDOW_REUSE_SECONDS}
        self._windows[key] = (at, rows)
        return rows, at

    async def _find_game(self, league: League, teams: list, slot,
                         catalogue: dict) -> Optional[dict]:
        ids = [t.get("id") for t in teams]
        rows = await self._window(league, ids)
        if ids:
            both = [r for r in rows if all(_has_team(r, [i], catalogue) for i in ids)]
            rows = both or [r for r in rows if _has_team(r, ids, catalogue)]
        if slot is not None:
            # A named slot names a game by when it starts; none in the slot
            # is none (§259), never the nearest.
            rows = [r for r in rows if named_slots.in_slot(slot, start_of(r))]
        if not rows and len(ids) == 1:
            schedule = await team_schedule(self.client, league, ids[0])
            now = datetime.now(timezone.utc)
            near = [r for r in schedule if start_of(r)
                    and abs((start_of(r) - now).total_seconds()) < 7 * 86400]
            if slot is not None:
                # §281: the week's schedule is held to the slot too - the
                # afternoon game is not Sunday Night Football.
                near = [r for r in near if named_slots.in_slot(slot, start_of(r))]
            rows = near
        return pick_game(rows)

    async def _resolve_race(self) -> Optional[Entity]:
        league = LEAGUES["f1"]
        data = await self.client.get(league, "games", "events",
                                     {"season": balldontlie.season_for(league),
                                      "per_page": 100})
        events = [r for r in (data or {}).get("data") or [] if isinstance(r, dict)]
        event = pick_event(events, "start_date", "end_date")
        if event is None:
            return None
        entity = Entity(domain="sports", provider=self.name,
                        id=f"f1:event:{event.get('id')}", label=str(event.get("name") or ""))
        self._rows[entity.key] = ("f1", "race", {"row": event})
        return entity

    async def _resolve_card(self) -> Optional[Entity]:
        league = LEAGUES["mma"]
        data = await self.client.get(league, "games", "events",
                                     {"year": datetime.now(timezone.utc).year,
                                      "per_page": 100})
        events = [r for r in (data or {}).get("data") or [] if isinstance(r, dict)
                  and "ufc" in str((r.get("league") or {}).get("abbreviation")
                                   or (r.get("league") or {}).get("name")
                                   or r.get("name") or "").lower()]
        event = pick_event(events, "date", "date")
        if event is None:
            return None
        entity = Entity(domain="sports", provider=self.name,
                        id=f"mma:event:{event.get('id')}", label=str(event.get("name") or ""))
        self._rows[entity.key] = ("mma", "card", {"row": event})
        return entity

    # ---- what is true about it ---------------------------------------------
    async def fetch(self, entity: Entity) -> Optional[LiveFacts]:
        held = self._rows.get(entity.key)
        if held is None:
            return None
        key, kind, payload = held
        league = LEAGUES[key]
        if kind == "race":
            return await self._race_facts(league, payload["row"], entity)
        if kind == "card":
            return await self._card_facts(league, payload["row"], entity)
        catalogue = (TEAMS.get(key) or (0, {}))[1]
        game_id = payload["row"].get("id")
        rows, read_at = await self._window_read(
            league, [i for i in payload["teams"] if i is not None])
        row = next((r for r in rows if r.get("id") == game_id), None)
        if row is None:
            # Out of the window now: its own team's schedule has it.
            for team_id in payload["teams"]:
                if team_id is None:
                    continue
                # §281: as fresh as a score must be, and stamped with when
                # it was actually read - a held schedule is up to half an
                # hour old, and "now" would let a stale state pass as current.
                schedule = await team_schedule(self.client, league, team_id,
                                               max_age=CARD_REUSE_SECONDS)
                row = next((r for r in schedule if r.get("id") == game_id), None)
                if row is not None:
                    read_at = (SCHEDULES.get((key, team_id)) or (time.time(),))[0]
                    break
        if row is None:
            return None
        status = believable(status_of(row), row, SHAPES[key])
        said = game_facts(league, row, status, catalogue)
        said += await self._seasons(league, row, catalogue)
        if not said:
            return None
        return LiveFacts(domain="sports", source=self._source(key),
                         as_of=datetime.fromtimestamp(read_at, tz=timezone.utc),
                         facts=said, status=status, entity=entity)

    async def _seasons(self, league: League, row: dict, catalogue: dict) -> list:
        """Both teams' records, places, and last and next games beside the
        game. Never costs the game its facts: what cannot be read in time is
        left out, and the log says so."""
        home, away = sides(row, catalogue)
        teams = [t for t in (home, away) if t.get("id") is not None]

        async def one(team):
            table, schedule = await asyncio.gather(
                league_standings(self.client, league),
                team_schedule(self.client, league, team["id"]),
                return_exceptions=True)
            out = []
            if isinstance(table, dict):
                out += standing_facts(league, team, table.get(team["id"]))
            if isinstance(schedule, list):
                out += schedule_facts(league, team, schedule, skip_id=row.get("id"),
                                      teams=catalogue)
            return out
        try:
            parts = await asyncio.wait_for(
                asyncio.gather(*(one(t) for t in teams)), timeout=2.0)
        except Exception as exc:  # noqa: BLE001 - the game's facts stand
            log.info("live facts: no season facts for %s: %s", league.key, exc)
            return []
        return list(dict.fromkeys(s for part in parts for s in part))

    async def _race_facts(self, league: League, event: dict,
                          entity: Entity) -> Optional[LiveFacts]:
        data = await self.client.get(league, "games", "events",
                                     {"event_ids[]": [event.get("id")]})
        event = next((r for r in (data or {}).get("data") or []
                      if r.get("id") == event.get("id")), event)
        status = status_of(event)
        name = str(event.get("name") or "the Grand Prix")
        circuit = (event.get("circuit") or {}).get("name") or event.get("location") or ""
        said = []
        if status == live_facts.SCHEDULED:
            said.append(f"The {name} weekend has not started yet"
                        + (f"; it runs from {_date_only(event.get('start_date'))} "
                           f"to {_date_only(event.get('end_date'))}" if event.get("start_date") else "")
                        + (f" at {circuit}" if circuit else "") + ".")
        elif status == live_facts.IN_PROGRESS:
            said.append(f"The {name} weekend is under way" +
                        (f" at {circuit}" if circuit else "") + ".")
        elif status == live_facts.FINAL:
            said.append(f"The {name} has finished.")
            podium = await self._podium(league, event)
            if podium:
                said.append("The podium: " + ", ".join(
                    f"{_ordinal(i + 1)} {d}" for i, d in enumerate(podium)) + ".")
        else:
            said.append(f"The scoreboard does not say where the {name} weekend stands.")
        return LiveFacts(domain="sports", source=self.name,
                         as_of=datetime.now(timezone.utc), facts=said,
                         status=status, entity=entity)

    async def _podium(self, league: League, event: dict) -> list:
        try:
            data = await self.client.get(league, "sessions", "sessions",
                                         {"event_ids[]": [event.get("id")], "type": "Race"})
            session = next((s for s in (data or {}).get("data") or []
                            if str(s.get("type") or "").lower() == "race"), None)
            if session is None:
                return []
            data = await self.client.get(league, "sessions", "session_results",
                                         {"session_ids[]": [session.get("id")]})
        except TierRefused:
            return []
        rows = sorted((r for r in (data or {}).get("data") or []
                       if _int(r.get("position"))), key=lambda r: _int(r["position"]))
        return [balldontlie.full_name(r.get("driver") or {}) for r in rows[:3]]

    async def _card_facts(self, league: League, event: dict,
                          entity: Entity) -> Optional[LiveFacts]:
        data = await self.client.get(league, "games", "fights",
                                     {"event_ids": [event.get("id")], "per_page": 100})
        fights = [f for f in (data or {}).get("data") or [] if isinstance(f, dict)]
        status = status_of(event)
        name = str(event.get("name") or "the card")
        said = []
        if status == live_facts.SCHEDULED:
            when = _when(start_of({"datetime": event.get("main_card_start_time")}))
            said.append(f"{name} has not started yet."
                        + (f" The main card starts {when}." if when else ""))
        elif status == live_facts.IN_PROGRESS:
            said.append(f"{name} is under way.")
        elif status == live_facts.FINAL:
            said.append(f"{name} has finished.")
        else:
            said.append(f"The scoreboard does not say where {name} stands.")
        main = next((f for f in fights if f.get("is_main_event")), None)
        if main:
            f1, f2 = main.get("fighter1") or {}, main.get("fighter2") or {}
            weight = (main.get("weight_class") or {}).get("name") or ""
            said.append(f"The main event is {f1.get('name')} against {f2.get('name')}"
                        + (f" at {weight}" if weight else "") + ".")
            winner = main.get("winner") or {}
            if status_of(main) == live_facts.FINAL and winner.get("id"):
                loser = f2 if winner.get("id") == f1.get("id") else f1
                how = str(main.get("result_method") or "").strip()
                rnd = _int(main.get("result_round"))
                said.append(f"{winner.get('name')} beat {loser.get('name')}"
                            + (f" by {how.lower()}" if how else "")
                            + (f" in round {rnd}" if rnd else "") + ".")
        return LiveFacts(domain="sports", source=self.name,
                         as_of=datetime.now(timezone.utc), facts=said,
                         status=status, entity=entity)


def _day_of(row: dict) -> str:
    """The UTC day a row is played on, `YYYY-MM-DD`."""
    start = start_of(row)
    if start is not None:
        return start.astimezone(timezone.utc).date().isoformat()
    return str((row or {}).get("date") or (row or {}).get("game_date") or "")[:10]


def _date_only(value) -> str:
    return balldontlie._date_said(str(value or "")[:10])  # noqa: SLF001


def _has_team(row: dict, ids: list, catalogue: Optional[dict] = None) -> bool:
    home, away = sides(row, catalogue)
    return home.get("id") in ids or away.get("id") in ids


def teams_named(teams: list, context: str, entity: str,
                nicknames: bool = False) -> list:
    """The teams the wording names: a full name anywhere; a short name
    ("Chiefs") only when the league is already placed - by its own name or a
    rostered team - or it is the whole entity. Two at most."""
    found = []
    folded = balldontlie._fold(context)  # noqa: SLF001
    for team in teams:
        full = str(team.get("full_name") or team.get("display_name") or "").strip()
        short = str(team.get("name") or team.get("short_name") or "").strip()
        if full and balldontlie._fold(full) in folded:  # noqa: SLF001
            found.append(team)
        elif short and (balldontlie._fold(short) == balldontlie._fold(entity)  # noqa: SLF001
                        or (nicknames and len(short) > 3
                            and balldontlie._fold(short) in folded)):  # noqa: SLF001
            found.append(team)
    unique = list({t.get("id"): t for t in found}.values())
    return unique[:2]


def pick_game(rows: list, now: Optional[datetime] = None) -> Optional[dict]:
    """Under way first, then today's, then the latest finished, then the
    next to start. One row, or None."""
    now = now or datetime.now(timezone.utc)
    if not rows:
        return None
    live = [r for r in rows if status_of(r) == live_facts.IN_PROGRESS]
    if live:
        return live[0]
    finals = [r for r in rows if status_of(r) == live_facts.FINAL and start_of(r)]
    upcoming = [r for r in rows if status_of(r) == live_facts.SCHEDULED and start_of(r)
                and start_of(r) >= now - timedelta(hours=STUCK_SCHEDULED_HOURS)]
    today = [r for r in finals + upcoming if start_of(r).date() == now.date()]
    if today:
        return sorted(today, key=start_of)[-1]
    if finals:
        return max(finals, key=start_of)
    if upcoming:
        return min(upcoming, key=start_of)
    return rows[0] if len(rows) == 1 else None


def pick_event(events: list, start_key: str, end_key: str,
               now: Optional[datetime] = None) -> Optional[dict]:
    """An event under way, else the next within ten days, else the last
    within three."""
    now = now or datetime.now(timezone.utc)
    live = [e for e in events if status_of(e) == live_facts.IN_PROGRESS]
    if live:
        return live[0]

    def day(e, key):
        try:
            return datetime.fromisoformat(str(e.get(key) or "")[:10]).replace(
                tzinfo=timezone.utc)
        except ValueError:
            return None
    upcoming = [e for e in events if status_of(e) == live_facts.SCHEDULED and day(e, start_key)
                and now - timedelta(days=1) <= day(e, start_key) <= now + timedelta(days=10)]
    if upcoming:
        return min(upcoming, key=lambda e: day(e, start_key))
    done = [e for e in events if status_of(e) == live_facts.FINAL and day(e, end_key)
            and day(e, end_key) >= now - timedelta(days=3)]
    if done:
        return max(done, key=lambda e: day(e, end_key))
    return None


# --------------------------------------------------------------------------
# The myFAM sweep: today's games on tiles, with the score
# --------------------------------------------------------------------------
#: The leagues the sweep follows (§191's list, in BALLDONTLIE's leagues).
SWEPT = ("nfl", "ncaaf", "nba", "ncaab", "wnba", "mlb", "nhl", "ucl", "epl",
         "laliga", "bundesliga", "fifa")
#: When each league was last swept, and its last failure.
SWEPT_AT: dict = {}
FAILURES: dict = {}
#: Requests the sweep leaves in the plan's minute for listeners (§281): it
#: stops before a question would no longer fit, never after.
SWEEP_RESERVE = 10


def sweeps_on_plan(tier: Optional[str] = None) -> bool:
    """Whether the plan can carry the myFAM sweep at all (§281). Free is five
    requests a minute - one listener's question - so on Free games are looked
    up on demand only, and the sweep never spends a listener's minute."""
    tier = tier or settings.balldontlie_tier
    return balldontlie.PER_MINUTE.get(tier, 5) > SWEEP_RESERVE


def _api_sports(key: str) -> bool:
    import api_sports

    return api_sports.serves(key)


def sweepable(key: str) -> bool:
    """Whether the sweep may follow this league at all: API-Sports' leagues
    are paced by their own day (§290), BALLDONTLIE's by its plan's minute."""
    return _api_sports(key) or sweeps_on_plan()


def watching(rows: list, now: float) -> bool:
    """A held card with a game under way, about to start, or started in the
    last few hours and not yet final (§191)."""
    for row in rows or ():
        status = status_of(row)
        if status == live_facts.IN_PROGRESS:
            return True
        start = start_of(row)
        if status == live_facts.SCHEDULED and start is not None:
            at = start.timestamp()
            if at - 900.0 <= now and at >= now - 6 * 3600:
                return True
    return False


def _keep_finals(league: League, rows: list, catalogue: dict, source: str) -> None:
    """The finished games in one league's sweep, to the history archive
    (§315). Only a **final**: a score mid-game is a moment, not history.
    Never raises."""
    try:
        import history_archive

        kept = []
        for row in rows:
            if status_of(row) != live_facts.FINAL:
                continue
            lines = game_facts(league, row, live_facts.FINAL, catalogue)
            home, away = sides(row, catalogue)
            if not lines or row.get("id") is None:
                continue
            day = _day_of(row) or datetime.now(timezone.utc).date().isoformat()
            kept.append(history_archive.swept_game(
                league.key, league.label, row.get("id"), day, lines, source,
                start_of(row) or datetime.now(timezone.utc),
                team_said(home) or "the home side",
                team_said(away) or "the away side"))
        history_archive.keep_swept(kept)
    except Exception as exc:  # noqa: BLE001 - the archive never costs a sweep
        log.warning("sweep finals were not archived: %s", exc)


def _keep_readings(league: League, rows: list, catalogue: dict, source: str,
                   now: datetime) -> None:
    """Every game under way or finished in one league's sweep, as readings
    of Consuela's series (§397): scores through a game are a series, each
    kept with the sweep's time; a final is one reading, kept at the first
    sweep that saw it. `history_archive.keep_readings` decides whether
    Consuela runs and the provider is approved. Never raises."""
    try:
        import history_archive

        records = []
        for row in rows:
            status = status_of(row)
            if status not in (live_facts.IN_PROGRESS, live_facts.FINAL):
                continue
            lines = game_facts(league, row, status, catalogue)
            home, away = sides(row, catalogue)
            if not lines or row.get("id") is None:
                continue
            records.append({
                "domain": "sports", "status": status, "source": source,
                "subject": f"{team_said(home) or 'the home side'} vs "
                           f"{team_said(away) or 'the away side'}",
                "as_of": now.isoformat(),
                "lines": [f"{league.label}: " + " ".join(lines)],
                # One final per game, whenever it is first seen.
                "reading_id": (history_archive.url_id(
                    f"final:{source}:{league.key}:{row.get('id')}")
                    if status == live_facts.FINAL else "")})
        history_archive.keep_readings(
            [r for r in map(history_archive.reading_from, records) if r is not None])
    except Exception as exc:  # noqa: BLE001 - the archive never costs a sweep
        log.warning("sweep scores were not kept as readings: %s", exc)


def make_signals():
    """`Signals` is built lazily: `stories` imports `story_sources`, which
    names this class in its registry."""
    import stories
    import story_sources

    class Signals(stories.StorySource):
        """Today's games in the followed leagues, with the score (§277,
        §280: the only sports sweep; §191's pacing:
        swept only when somebody drew myFAM recently, and only while a
        followed game is on or about to start)."""

        name = "BALLDONTLIE"
        domain = stories.SPORTS
        cost_per_refresh = 0.0
        max_signals = 24
        STRENGTH = {live_facts.IN_PROGRESS: 1.0, live_facts.SCHEDULED: 0.8,
                    live_facts.FINAL: 0.55}

        def __init__(self, client=None) -> None:
            import api_sports

            # §290: a `Router` while API-Sports answers any league.
            self.client = client or api_sports.scoreboard_client()

        def diagnose(self) -> tuple[bool, str]:
            import api_sports
            import live_sources

            if not settings.balldontlie_api_key and not api_sports.served():
                return False, "BALLDONTLIE_API_KEY is not set, so sports stories are off"
            if live_sources.configured().get("sports") != "balldontlie":
                return False, ("sports are on another provider "
                               f"({live_sources.configured().get('sports') or 'none'})")
            if not any(sweepable(k) for k in SWEPT):
                return False, (f"BALLDONTLIE's {settings.balldontlie_tier} plan "
                               f"allows {balldontlie.PER_MINUTE.get(settings.balldontlie_tier, 5)}"
                               " requests a minute, one listener's question: the "
                               "myFAM sweep is off and games are looked up on "
                               "demand only")
            failing = "; ".join(f"{k} FAILING ({w})" for k, w in FAILURES.items())
            problem = getattr(self.client, "problem", lambda: "")()
            beyond = [k for k in SWEPT if not balldontlie.plan_reaches(k, "games")]
            return True, ("BALLDONTLIE_API_KEY present, following "
                          f"{', '.join(k for k in SWEPT if k not in beyond)} "
                          "while a game is on; not verified from this machine"
                          + (f"; not swept on the {settings.balldontlie_tier} "
                             f"plan: {', '.join(beyond)}" if beyond else "")
                          + (f"; {failing}" if failing else "")
                          + (f"; ACCOUNT REFUSED: {problem}" if problem else ""))

        async def verify(self) -> tuple[bool, str]:
            return await ScoreBoard(self.client).verify()

        def due(self, key: str, now: float) -> list:
            today = datetime.fromtimestamp(now, tz=timezone.utc).date()
            days = [today.isoformat(), (today - timedelta(days=1)).isoformat()]
            held = CARDS.get(key, {})
            if today.isoformat() in held and not any(
                    watching(held.get(d, (0, []))[1], now) for d in days):
                return []
            last = SWEPT_AT.get(key)
            if last is not None and now - last < 0.9 * float(
                    settings.stories_sports_interval_seconds):
                return []
            return days

        def idle(self, now: float) -> str:
            if not any(sweepable(k) for k in SWEPT):
                return (f"BALLDONTLIE's {settings.balldontlie_tier} plan cannot "
                        "carry the sweep; sports are looked up on demand")
            if not stories.recent_demand(now):
                return ("nobody has drawn myFAM in the last "
                        f"{settings.stories_demand_seconds / 60:.0f} min; "
                        "sports are swept on demand")
            if not any(self.due(k, now) for k in SWEPT):
                return "nothing to watch: no followed league has a game on"
            return ""

        async def collect(self, limit: int) -> list:
            if not any(sweepable(k) for k in SWEPT):
                return []
            clock = time.time()
            # §281: a league the plan cannot reach (the World Cup below GOAT)
            # is never asked, and is not a failure.
            due = {k: self.due(k, clock) for k in SWEPT if sweepable(k)
                   and (_api_sports(k) or balldontlie.plan_reaches(k, "games"))}
            due = {k: d for k, d in due.items() if d}

            async def card(key, days):
                league = LEAGUES[key]
                shape = SHAPES[key]
                params: dict = {"per_page": 100}
                if shape.dates:
                    params[shape.dates] = days
                    data = await self.client.get(league, "games", shape.path, params)
                    rows = [r for r in (data or {}).get("data") or []
                            if isinstance(r, dict)]
                else:
                    params.update(_season_param(shape, balldontlie.season_for(league)))
                    rows = await _pages(self.client, league, "games", shape.path,
                                        params)
                if not shape.dates:
                    # A tournament has no date filter: its days, from the rows.
                    rows = [r for r in rows if start_of(r) and abs(
                        (start_of(r) - datetime.now(timezone.utc)).total_seconds())
                        < 36 * 3600]
                held = CARDS.setdefault(key, {})
                for day in days:
                    held[day] = (clock, [r for r in rows if _day_of(r) == day])
                catalogue = {}
                if shape is _SOCCER or not shape.dates:
                    catalogue = await league_teams(self.client, league)
                return league, rows, catalogue

            out, failures = [], []
            # §281: one league at a time, never all at once - twelve requests
            # in one burst spent a plan's minute and every listener's lookup
            # in it. The sweep stops while a question still fits in the
            # minute; a league not reached is due again next time.
            spare = getattr(self.client, "spare", None)
            # §290: per league, where a `Router` decides whose allowance it is.
            may = getattr(self.client, "sweep_may", None)
            stopped: Optional[RateLimited] = None
            for key, days in due.items():
                if (not may(key)) if may is not None else (
                        spare is not None and spare() <= SWEEP_RESERVE):
                    stopped = RateLimited(
                        f"sweep stopped before {key}: the minute's last "
                        f"{SWEEP_RESERVE} requests are kept for listeners")
                    log.info("stories: BALLDONTLIE %s", stopped)
                    break
                SWEPT_AT[key] = clock
                try:
                    result = await card(key, days)
                except RateLimited as exc:
                    log.info("stories: BALLDONTLIE sweep stopped at %s: %s", key, exc)
                    SWEPT_AT.pop(key, None)
                    stopped = exc
                    break
                except TierRefused as exc:
                    # The plan's answer, not a fault (§281).
                    log.info("stories: BALLDONTLIE %s not swept: %s", key, exc)
                    FAILURES.pop(key, None)
                    continue
                except Exception as exc:  # noqa: BLE001 - said below
                    failures.append((key, exc))
                    FAILURES[key] = f"{type(exc).__name__}: {exc}"
                    continue
                FAILURES.pop(key, None)
                league, rows, catalogue = result
                now = datetime.now(timezone.utc)
                for row in rows:
                    signal = self._signal(league, row, catalogue, now)
                    if signal is not None:
                        out.append(signal)
                # Every final the sweep saw, for the history archive (§315):
                # no request of its own, never awaited. Named by the provider
                # that answered for the league (§290), so the archive can
                # tell BALLDONTLIE's from API-Sports' (§397).
                source = (getattr(self.client, "source_for", lambda k: "")(key)
                          or self.name)
                _keep_finals(league, rows, catalogue, source)
                # Every score the sweep saw, as Consuela's series (§397).
                _keep_readings(league, rows, catalogue, source, now)
            for key, exc in failures:
                log.warning("stories: BALLDONTLIE %s failed: %s", key, exc)
            if failures and not out:
                raise failures[0][1]
            if stopped is not None and not out:
                # A sweep the minute stopped is an outage, never an empty
                # card (§89): a day with no games is not what was read.
                raise stopped
            out.sort(key=lambda s: (-s.strength, s.subject))
            return out[:limit]

        def _signal(self, league, row, catalogue, now):
            status, line = live_line(league, row, catalogue)
            if status == live_facts.UNKNOWN or not line:
                return None
            home, away = sides(row, catalogue)
            home, away = team_said(home), team_said(away)
            stamp = now.strftime("%H:%M UTC")
            said = {
                live_facts.IN_PROGRESS: f"is under way right now: {line} (as of {stamp})",
                live_facts.SCHEDULED: f"is on today's card and has not started ({line})",
                live_facts.FINAL: f"has finished: {line}",
            }[status]
            return stories.Signal(
                subject=f"{home} vs {away}",
                observation=(f"this {league.label} game {said}. The score is "
                             "shown to the listener beside the tile and "
                             "updates on its own; keep it out of the title, "
                             "the angle and the query."),
                domain=self.domain, source=self.name,
                tags=story_sources._in_field(  # noqa: SLF001
                    story_sources._tags(f"{home} {away} {league.label}", "sports"),  # noqa: SLF001
                    "sports"),
                strength=round(self.STRENGTH.get(status, 0.5), 3),
                as_of=now, outcome_pending=status != live_facts.FINAL,
                live_line=line, live_status=status, minor_league=False)

    return Signals
