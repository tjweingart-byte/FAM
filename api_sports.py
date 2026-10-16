"""API-Sports' free plans under BALLDONTLIE's scoreboard - while developing (§289).

**Temporary, at the owner's direction (10.16).** BALLDONTLIE is priced per
sport, and its free plan is five requests a minute with no standings, which
is not enough to use the app while it is being built. API-Sports' free plans
(100 requests a day per sport, one key for every sport) cover that until FAM
goes commercial, when this file is deleted and BALLDONTLIE's paid plans take
over. Nothing else in the scoreboard changes for it.

**How it fits.** `balldontlie_scores.ScoreBoard` and its myFAM sweep ask a
client for `get(league, kind, path, params)` and read BALLDONTLIE-shaped rows.
`Client` here answers the same call from API-Sports and translates each row
into that shape - `status_state`, `home_team` / `visitor_team`, the scores, the
start time - so the status mapping, the stuck-feed check, ScoreGuard's
sentences and the tiles are the scoreboard's own, unchanged. `Router` sends
each league to one provider or the other, never both, because the two
providers' team and game ids mean different things.

**Which leagues.** With `API_SPORTS_KEY` set, every league in `LEAGUES` goes to
API-Sports while BALLDONTLIE is absent or on its free plan
(`BALLDONTLIE_TIER=free`); `API_SPORTS_LEAGUES` names them instead. Formula 1,
UFC, player figures and injury reports stay BALLDONTLIE's.

**The rules it keeps:**

* **Status from the provider's own short code**, mapped at the boundary;
  anything not listed is `unknown`, which says nothing.
* **Its ids never meet BALLDONTLIE's** (`aps:`-prefixed): a team id from one
  provider asked of the other returns somebody else's team, fresh and wrong.
* **A refusal is a refusal, never a quiet day.** API-Sports answers 200 with the
  reason in `errors`: a spent day is `RateLimited`, the account is
  `balldontlie.AccountRefused`, and the free plan's season limit is
  `balldontlie.TierRefused`, remembered for the day and said on /admin.
* **The day is counted per sport** (`API_SPORTS_DAILY_REQUESTS`), and the
  myFAM sweep stops while `API_SPORTS_LOOKUP_RESERVE` requests are left, so a
  listener's question still gets an answer at the end of a busy day.

Written from API-Sports' documented shapes and the adapter §280 removed;
**never run against the live API from this repository** (the hosts are
blocked here) - `python tools/verify_live.py` on a machine with network is
what says it answers.
"""
from __future__ import annotations

import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import balldontlie
import live_facts
import live_route
from balldontlie import AccountRefused, League, RateLimited, TierRefused
from config import settings

log = logging.getLogger(__name__)

NAME = "API-Sports"

#: Prefix on every id this adapter hands out (see the module docstring).
ID_PREFIX = "aps:"


# --------------------------------------------------------------------------
# The products and the leagues
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Sport:
    key: str
    host: str
    #: `fixtures` for soccer, `games` for everything else.
    path: str
    #: Short status code -> BALLDONTLIE's `status_state` word. Anything not
    #: here becomes "", which the scoreboard reads as `unknown`.
    states: dict
    #: Short code -> the period number it names, for "the 3rd quarter".
    periods: dict


def _codes(prefix: str, count: int) -> dict:
    return {f"{prefix}{n}": n for n in range(1, count + 1)}


_OFF = {"POST": "postponed", "PST": "postponed", "CANC": "canceled",
        "SUSP": "suspended", "ABD": "abandoned", "INTR": "suspended",
        "AWD": "", "WO": "", "DELAYED": "delayed"}

SPORTS = {
    "american-football": Sport(
        "american-football", "https://v1.american-football.api-sports.io",
        "games",
        dict({"NS": "scheduled", "TBD": "scheduled",
              "Q1": "in_progress", "Q2": "in_progress", "Q3": "in_progress",
              "Q4": "in_progress", "OT": "in_progress", "HT": "in_progress",
              "FT": "final", "AOT": "final"}, **_OFF),
        _codes("Q", 4)),
    "basketball": Sport(
        "basketball", "https://v1.basketball.api-sports.io", "games",
        dict({"NS": "scheduled",
              "Q1": "in_progress", "Q2": "in_progress", "Q3": "in_progress",
              "Q4": "in_progress", "OT": "in_progress", "HT": "in_progress",
              "BT": "in_progress", "FT": "final", "AOT": "final"}, **_OFF),
        _codes("Q", 4)),
    "baseball": Sport(
        "baseball", "https://v1.baseball.api-sports.io", "games",
        dict({"NS": "scheduled", "LIVE": "in_progress", "FT": "final"},
             **{code: "in_progress" for code in _codes("IN", 9)}, **_OFF),
        _codes("IN", 9)),
    "hockey": Sport(
        "hockey", "https://v1.hockey.api-sports.io", "games",
        dict({"NS": "scheduled", "P1": "in_progress", "P2": "in_progress",
              "P3": "in_progress", "OT": "in_progress", "PT": "in_progress",
              "BT": "in_progress", "FT": "final", "AOT": "final",
              "AP": "final"}, **_OFF),
        _codes("P", 3)),
    "football": Sport(
        "football", "https://v3.football.api-sports.io", "fixtures",
        dict({"NS": "scheduled", "TBD": "scheduled",
              "1H": "in_progress", "2H": "in_progress", "HT": "in_progress",
              "ET": "in_progress", "BT": "in_progress", "P": "in_progress",
              "LIVE": "in_progress", "FT": "final", "AET": "final",
              "PEN": "final"}, **_OFF),
        {}),
}


@dataclass(frozen=True)
class Served:
    #: The API-Sports product.
    sport: str
    #: API-Sports' own league id in that product.
    league_id: str
    #: How it names a season: `year` (2026) or `span` ("2025-2026").
    season: str = "year"


#: BALLDONTLIE league key -> the API-Sports league that answers for it.
LEAGUES = {
    "nfl": Served("american-football", "1"),
    "ncaaf": Served("american-football", "2"),
    "nba": Served("basketball", "12", "span"),
    "wnba": Served("basketball", "13"),
    "ncaab": Served("basketball", "116", "span"),
    "mlb": Served("baseball", "1"),
    "nhl": Served("hockey", "57"),
    "epl": Served("football", "39"),
    "laliga": Served("football", "140"),
    "seriea": Served("football", "135"),
    "bundesliga": Served("football", "78"),
    "ligue1": Served("football", "61"),
    "ucl": Served("football", "2"),
    "mls": Served("football", "253"),
    "fifa": Served("football", "1"),
}

#: The kinds of request it answers; anything else stays BALLDONTLIE's.
KINDS = ("teams", "games", "standings")


def served() -> frozenset:
    """The leagues API-Sports answers for on this deploy."""
    if not (settings.api_sports_key or "").strip():
        return frozenset()
    named = [k.strip().lower() for k in (settings.api_sports_leagues or "").split(",")
             if k.strip()]
    if named:
        return frozenset(k for k in named if k in LEAGUES)
    if not settings.balldontlie_api_key or settings.balldontlie_tier == "free":
        return frozenset(LEAGUES)
    return frozenset()


def serves(league_key: str) -> bool:
    return league_key in served()


def season_of(league: League, now: Optional[datetime] = None) -> str:
    """The season as API-Sports names it for this league."""
    year = balldontlie.season_for(league, now)
    return f"{year}-{year + 1}" if LEAGUES[league.key].season == "span" else str(year)


def is_ours(team: dict) -> bool:
    """Whether a team row came from here (its id is not BALLDONTLIE's)."""
    return str((team or {}).get("id") or "").startswith(ID_PREFIX)


def _ours(value) -> str:
    return f"{ID_PREFIX}{value}"


def _theirs(value) -> str:
    text = str(value)
    return text[len(ID_PREFIX):] if text.startswith(ID_PREFIX) else text


# --------------------------------------------------------------------------
# Translating a row into BALLDONTLIE's shape
# --------------------------------------------------------------------------
def nickname(full: str, league_key: str) -> str:
    """"Chiefs" from "Kansas City Chiefs": the roster's own short wording
    when it has one ("Maple Leafs"), else the last word. Soccer clubs keep
    their name - "United" names half of England."""
    if LEAGUES[league_key].sport == "football":
        return full
    for name, wordings in live_route.ROSTER.get(league_key, ()):
        if name.lower() == full.lower():
            shorter = [w.strip() for w in wordings[1:]
                       if full.lower().endswith(w.strip())]
            if shorter:
                return full[len(full) - len(shorter[0]):]
    words = full.split()
    return words[-1] if len(words) > 1 else full


def team_row(team: dict, league_key: str) -> dict:
    name = str((team or {}).get("name") or "").strip()
    return {"id": _ours(team.get("id")), "full_name": name,
            "name": nickname(name, league_key) if name else ""}


def _block(row: dict, name: str):
    for holder in ("game", "fixture"):
        block = (row or {}).get(holder)
        if isinstance(block, dict) and block.get(name) is not None:
            return block.get(name)
    return (row or {}).get(name)


def _start(row: dict) -> str:
    date = _block(row, "date")
    stamp = date.get("timestamp") if isinstance(date, dict) else None
    stamp = stamp or _block(row, "timestamp")
    try:
        return datetime.fromtimestamp(int(stamp), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    if isinstance(date, str) and "T" in date:
        return date
    return ""


def _score(row: dict) -> tuple:
    goals = (row or {}).get("goals")
    if isinstance(goals, dict) and goals.get("home") is not None:
        return goals.get("home"), goals.get("away")
    scores = (row or {}).get("scores") or {}
    home, away = scores.get("home"), scores.get("away")
    if isinstance(home, dict) or isinstance(away, dict):
        home = (home or {}).get("total")
        away = (away or {}).get("total")
    return home, away


def game_row(league_key: str, row: dict) -> Optional[dict]:
    """One API-Sports game as BALLDONTLIE shapes one, or None if unreadable."""
    sport = SPORTS[LEAGUES[league_key].sport]
    teams = (row or {}).get("teams") or {}
    home, away = teams.get("home") or {}, teams.get("away") or {}
    game_id = _block(row, "id")
    if game_id is None or home.get("id") is None or away.get("id") is None:
        return None
    status = _block(row, "status") or {}
    short = str(status.get("short") or "").upper() if isinstance(status, dict) else ""
    out = {"id": _ours(game_id), "home_team": team_row(home, league_key),
           "visitor_team": team_row(away, league_key),
           "status_state": sport.states.get(short, ""),
           "datetime": _start(row)}
    hs, as_ = _score(row)
    if hs is not None and as_ is not None:
        out["home_team_score"], out["visitor_team_score"] = hs, as_
    if out["status_state"] == "in_progress":
        timer = str(status.get("timer") or "").strip() if isinstance(status, dict) else ""
        if short in sport.periods:
            out["period"] = sport.periods[short]
            if timer and sport.key != "baseball":
                out["time_remaining"] = timer
        elif short == "HT":
            out["period_detail"] = "half-time" if sport.key == "football" else "halftime"
        elif short == "OT":
            out["period_detail"] = "overtime"
        elif sport.key == "football" and str(status.get("elapsed") or "").isdigit():
            minute = int(status["elapsed"])
            out["period_detail"] = f"the {balldontlie._ordinal(minute)} minute"  # noqa: SLF001
    return out


def standing_rows(league_key: str, response) -> list:
    """API-Sports' table, every sport's shape, as BALLDONTLIE's standings
    rows: the team, its wins and losses, and its place in its group."""
    out: list = []

    def number(value) -> Optional[int]:
        if isinstance(value, dict):
            value = value.get("total")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def walk(node) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        nested = node.get("league")
        if isinstance(nested, dict) and isinstance(nested.get("standings"), list):
            walk(nested["standings"])
            return
        team = node.get("team")
        if not isinstance(team, dict) or team.get("id") is None:
            return
        row: dict = {"team": team_row(team, league_key)}
        games = node.get("all") or node.get("games") or {}
        row["wins"] = number(node.get("won", games.get("win")))
        row["losses"] = number(node.get("lost", games.get("lose")))
        for key, value in (("ties", node.get("ties")), ("draws", games.get("draw"))):
            if number(value):
                row[key] = number(value)
        rank = number(node.get("position", node.get("rank")))
        group = node.get("division") or node.get("group") or node.get("conference")
        if isinstance(group, dict):
            group = group.get("name")
        if rank is not None and group:
            row["rank"], row["group_name"] = rank, str(group).strip()
        if isinstance(node.get("points"), int):
            row["points"] = node["points"]
        if row["wins"] is not None and row["losses"] is not None:
            out.append(row)

    walk(response)
    return out


# --------------------------------------------------------------------------
# The client
# --------------------------------------------------------------------------
#: How long one answer is reused: one lookup asks the same team's season for
#: its window and again for its schedule a second later - one request.
REUSE_SECONDS = 15.0

#: The keys of `errors` that are about the account, not the request. Keys,
#: not words: the season limit says "do not have access" under `plan`.
_ACCOUNT_KEYS = ("access", "token", "key", "apikey", "suspended")


class Client:
    """API-Sports' requests, in `balldontlie.Client`'s shape (see the module
    docstring): counted per sport per UTC day, paced per minute, refusals
    remembered for the day."""

    name = NAME

    def __init__(self) -> None:
        #: (league, kind) -> UTC day it was refused (the season limit).
        self.refused: dict = {}
        #: sport -> (UTC day, requests spent).
        self.spent: dict = {}
        #: sport -> UTC day API-Sports said the day's allowance is gone.
        self.exhausted: dict = {}
        #: The last refusal in words, and its UTC day, for /admin.
        self.account_problem: tuple = ("", "")
        self.plan_problem: tuple = ("", "")
        self._sent: dict = {}
        self._held: dict = {}

    # ---- the day and the minute --------------------------------------------
    @staticmethod
    def _today(now: Optional[float] = None) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(time.time() if now is None else now))

    def used(self, sport: str, now: Optional[float] = None) -> int:
        day, count = self.spent.get(sport, ("", 0))
        return count if day == self._today(now) else 0

    def remaining(self, sport: str, now: Optional[float] = None) -> int:
        if self.exhausted.get(sport) == self._today(now):
            return 0
        return max(0, int(settings.api_sports_daily_requests) - self.used(sport, now))

    def minute_spare(self, sport: str, now: Optional[float] = None) -> int:
        now = time.monotonic() if now is None else now
        sent = self._sent.get(sport) or ()
        return int(settings.api_sports_per_minute) - sum(1 for at in sent if now - at < 60.0)

    def sweep_may(self, league_key: str) -> bool:
        """Whether the myFAM sweep may spend one of this sport's requests:
        never the day's last `API_SPORTS_LOOKUP_RESERVE`, which are kept for
        listeners' questions, nor the minute's last two."""
        sport = LEAGUES[league_key].sport
        return (self.remaining(sport) > int(settings.api_sports_lookup_reserve)
                and self.minute_spare(sport) > 2)

    def _spend(self, sport: str) -> None:
        if self.remaining(sport) <= 0:
            raise RateLimited(
                f"API-Sports' {settings.api_sports_daily_requests} {sport} "
                "requests for today are spent; they come back at 00:00 UTC")
        now = time.monotonic()
        sent = self._sent.setdefault(sport, deque())
        while sent and now - sent[0] >= 60.0:
            sent.popleft()
        if len(sent) >= int(settings.api_sports_per_minute):
            raise RateLimited(
                f"API-Sports allows {settings.api_sports_per_minute} {sport} "
                "requests a minute and this minute's are spent")
        sent.append(now)
        self.spent[sport] = (self._today(), self.used(sport) + 1)

    def problem(self) -> str:
        """Today's refusal in words, or ""."""
        for said, day in (self.account_problem, self.plan_problem):
            if said and day == self._today():
                return said
        return ""

    # ---- one request ---------------------------------------------------------
    async def _ask(self, league: League, kind: str, endpoint: str,
                   params: dict, timeout: float) -> list:
        import live_sources

        served_ = LEAGUES[league.key]
        sport = SPORTS[served_.sport]
        url = f"{sport.host}/{endpoint}"
        key = (url, tuple(sorted(params.items())))
        held = self._held.get(key)
        if held and time.time() - held[0] < REUSE_SECONDS:
            return held[1]
        if self.refused.get((league.key, kind)) == self._today():
            raise TierRefused(f"API-Sports refused {league.label} {kind} today")
        self._spend(sport.key)
        data = await live_sources._json(  # noqa: SLF001
            url, {"x-apisports-key": settings.api_sports_key}, params, float(timeout))
        errors = (data or {}).get("errors")
        if errors:
            self._refusal(league, kind, sport.key, errors)
        rows = [r for r in (data or {}).get("response") or []
                if isinstance(r, (dict, list))]
        now = time.time()
        self._held = {k: v for k, v in self._held.items() if now - v[0] < REUSE_SECONDS}
        self._held[key] = (now, rows)
        return rows

    def _refusal(self, league: League, kind: str, sport: str, errors) -> None:
        """API-Sports answered 200 with the reason in `errors`. Read as data
        that is a day with no games and nothing saying it is wrong."""
        text = str(errors)
        lowered = text.lower()
        if "request" in lowered and "limit" in lowered:
            self.exhausted[sport] = self._today()
            raise RateLimited(f"API-Sports: the {sport} allowance is spent ({text[:160]})")
        keys = [str(k).lower() for k in errors] if isinstance(errors, dict) else []
        if any(k in _ACCOUNT_KEYS for k in keys) or "suspended" in lowered:
            said = f"API-Sports refused the key: {text[:200]}"
            self.account_problem = (said, self._today())
            log.error("live facts: %s", said)
            raise AccountRefused(said)
        if "plan" in keys:
            # The free plan's season limit: not asked again today, and said.
            self.refused[(league.key, kind)] = self._today()
            said = (f"API-Sports' plan refused {league.label} {kind} for season "
                    f"{season_of(league)}: {text[:200]}")
            self.plan_problem = (said, self._today())
            log.warning("live facts: %s; not asked again today", said)
            raise TierRefused(said)
        raise RuntimeError(f"API-Sports refused {league.label} {kind}: {text[:200]}")

    # ---- balldontlie.Client's call -------------------------------------------
    async def get(self, league: League, kind: str, path: str, params: dict,
                  timeout: float = 4.0) -> dict:
        """BALLDONTLIE's `get`, answered from API-Sports. Rows come back in
        BALLDONTLIE's shape under `data`, with no cursor: API-Sports answers
        a season in one page."""
        if league.key not in LEAGUES or kind not in KINDS:
            raise TierRefused(f"API-Sports does not answer {league.label} {kind} here")
        served_ = LEAGUES[league.key]
        sport = SPORTS[served_.sport]
        base = {"league": served_.league_id, "season": season_of(league)}
        if kind == "teams":
            rows = await self._ask(league, kind, "teams", base, timeout)
            teams = [r.get("team") if isinstance(r.get("team"), dict) else r
                     for r in rows if isinstance(r, dict)]
            return {"data": [team_row(t, league.key) for t in teams
                             if isinstance(t, dict) and t.get("id") is not None
                             and t.get("name")]}
        if kind == "standings":
            rows = await self._ask(league, kind, "standings", base, timeout)
            return {"data": standing_rows(league.key, rows)}
        dates = _listed(params, ("dates[]", "dates"))
        teams = [_theirs(t) for t in _listed(params, ("team_ids[]", "team_ids"))]
        if teams:
            # One team's season answers its schedule and any window it is
            # in: one request, where a request per day would be three.
            rows = await self._ask(league, kind, sport.path,
                                   dict(base, team=teams[0]), timeout)
        elif dates:
            rows = []
            for day in dates:
                rows += await self._ask(league, kind, sport.path,
                                        dict(base, date=day), timeout)
        else:
            rows = await self._ask(league, kind, sport.path, base, timeout)
        games = [g for g in (game_row(league.key, r) for r in rows
                             if isinstance(r, dict)) if g is not None]
        if dates:
            games = [g for g in games if g["datetime"][:10] in dates]
        if teams:
            wanted = {_ours(t) for t in teams}
            games = [g for g in games
                     if {g["home_team"]["id"], g["visitor_team"]["id"]} & wanted]
        return {"data": games}

    def report(self) -> dict:
        """Today's count per sport, for /admin and /api/health."""
        return {"served": sorted(served()),
                "daily_requests": int(settings.api_sports_daily_requests),
                "lookup_reserve": int(settings.api_sports_lookup_reserve),
                "used_today": {s: self.used(s) for s in SPORTS if self.used(s)},
                "problem": self.problem()}


def _listed(params: dict, names: tuple) -> list:
    for name in names:
        value = (params or {}).get(name)
        if value is None:
            continue
        return [str(v) for v in (value if isinstance(value, (list, tuple)) else [value])]
    return []


# --------------------------------------------------------------------------
# One client per league
# --------------------------------------------------------------------------
class Router:
    """Sends each league to API-Sports or BALLDONTLIE, never both: the two
    providers' ids mean different things."""

    def __init__(self, bdl, aps) -> None:
        self.bdl = bdl
        self.aps = aps

    def _for(self, league_key: str):
        return self.aps if serves(league_key) else self.bdl

    @property
    def refused(self) -> dict:
        return self.bdl.refused

    async def get(self, league: League, kind: str, path: str, params: dict,
                  timeout: float = 4.0) -> dict:
        if serves(league.key) and kind in KINDS:
            return await self.aps.get(league, kind, path, params, timeout)
        return await self.bdl.get(league, kind, path, params, timeout)

    def source_for(self, league_key: str) -> str:
        return NAME if serves(league_key) else ""

    def spare(self, now: Optional[float] = None) -> int:
        return self.bdl.spare(now)

    def sweep_may(self, league_key: str) -> bool:
        if serves(league_key):
            return self.aps.sweep_may(league_key)
        import balldontlie_scores

        return (balldontlie_scores.sweeps_on_plan()
                and self.bdl.spare() > balldontlie_scores.SWEEP_RESERVE)

    def problem(self) -> str:
        return "; ".join(p for p in (self.bdl.problem(), self.aps.problem()) if p)


#: The one API-Sports client, shared by the scoreboard and the sweep: the
#: day's allowance is the account's, not each source's.
SHARED: Optional[Client] = None


def shared_client() -> Client:
    global SHARED
    if SHARED is None:
        SHARED = Client()
    return SHARED


def answers_for(client) -> bool:
    """Whether `client` reaches API-Sports for the leagues it serves."""
    return isinstance(client, (Router, Client))


def scoreboard_client():
    """The client the scoreboard and the sweep use: BALLDONTLIE's alone, or a
    `Router` when API-Sports serves any league on this deploy."""
    if not served():
        return balldontlie.shared_client()
    return Router(balldontlie.shared_client(), shared_client())


def describe() -> str:
    """One line for the scoreboard's diagnosis."""
    leagues = sorted(served())
    if not leagues:
        return ""
    problem = shared_client().problem()
    return (f"API-Sports (free plans, temporary, §289) answers "
            f"{', '.join(leagues)}: {settings.api_sports_daily_requests} requests a "
            f"day per sport, {settings.api_sports_lookup_reserve} kept for listeners"
            + (f"; REFUSED: {problem}" if problem else ""))


_HOST_SPORT = re.compile(r"^v\d+\.([a-z0-9-]+)\.api-sports\.io$")


def sport_of_host(host: str) -> str:
    """Which product a request went to, for the /admin row per sport."""
    match = _HOST_SPORT.match((host or "").lower())
    return match.group(1) if match else ""


def status_of_short(league_key: str, short: str) -> str:
    """FAM's status for a short code, for tests and the admin page."""
    import balldontlie_scores

    word = SPORTS[LEAGUES[league_key].sport].states.get((short or "").upper(), "")
    return balldontlie_scores.status_of({"status_state": word}) if word else live_facts.UNKNOWN
