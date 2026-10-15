"""Asking a live provider when the brief named no domain, but the question
names something a provider covers (§265).

`Brief.live_domain` is a model's judgement of what the answer *turns on*, and
"how are the Eagles looking" or "what's going on with Nvidia" can reasonably
come back empty - so the same kind of question reached the scoreboard on one
tap and not the next, and a degraded brief (`fallback_brief`) never reached
it at all. §264's record showed which. This is the floor under that
judgement, decided in code and spending nothing:

* **sports** - the question or the brief names a team in `ROSTER` (the four
  US major leagues), a named broadcast slot (`named_slots`), or a team in a
  league catalogue this server already read (`balldontlie_scores.TEAMS`);
* **markets** - the question says a market word *and* the brief's
  `live_entity` could be a listing (`live_sources.looks_like_a_listing`).

It sets `Brief.live_route`, never `live_domain`: the domain also makes a
question outcome-dependent, screens the packet for sim leagues and decides
the cache window, and none of those should change because code noticed a team
name. The route only decides that a provider is *asked*. What the provider
answers is still resolved from its own catalogue, never from these names, and
a route to a domain nothing on this deployment can serve is not taken.

**The roster is names, never facts**, and only for the four US major leagues
(the scoreboard, `balldontlie_scores`, covers them and more). A full name
always counts. A nickname on its own counts only where it means nothing but
the team ("Steelers", "Maple Leafs") - "Heat", "Bills", "Giants", "Kings" and
the like are everyday words, other teams, or both, and need their city; so do
nicknames a college shares ("Bruins", "Cavaliers", "Broncos") or that mean
something else ("Raptors", "Kraken", "Dodgers", "Trail Blazers"). A team in a
league catalogue already read counts by its full name only, for the same
reason (`full_names_in`). Relocations and renames are rare; the test
`test_roster_has_every_team` keeps the counts.
"""
from __future__ import annotations

import re
from typing import Optional

#: One team a line: its full name, then any nickname that names it alone.
_ROSTER_TEXT = {
    "nfl": """
        Arizona Cardinals
        Atlanta Falcons
        Baltimore Ravens
        Buffalo Bills
        Carolina Panthers
        Chicago Bears
        Cincinnati Bengals | Bengals
        Cleveland Browns
        Dallas Cowboys
        Denver Broncos
        Detroit Lions
        Green Bay Packers | Packers
        Houston Texans
        Indianapolis Colts | Colts
        Jacksonville Jaguars
        Kansas City Chiefs
        Las Vegas Raiders
        Los Angeles Chargers
        Los Angeles Rams
        Miami Dolphins
        Minnesota Vikings
        New England Patriots
        New Orleans Saints
        New York Giants
        New York Jets
        Philadelphia Eagles
        Pittsburgh Steelers | Steelers
        San Francisco 49ers | 49ers | Niners
        Seattle Seahawks | Seahawks
        Tampa Bay Buccaneers
        Tennessee Titans
        Washington Commanders
    """,
    "nba": """
        Atlanta Hawks
        Boston Celtics | Celtics
        Brooklyn Nets
        Charlotte Hornets
        Chicago Bulls
        Cleveland Cavaliers
        Dallas Mavericks | Mavs
        Denver Nuggets
        Detroit Pistons
        Golden State Warriors
        Houston Rockets
        Indiana Pacers
        Los Angeles Clippers
        Los Angeles Lakers | Lakers
        Memphis Grizzlies
        Miami Heat
        Milwaukee Bucks
        Minnesota Timberwolves | Timberwolves
        New Orleans Pelicans
        New York Knicks | Knicks
        Oklahoma City Thunder
        Orlando Magic
        Philadelphia 76ers | 76ers | Sixers
        Phoenix Suns
        Portland Trail Blazers
        Sacramento Kings
        San Antonio Spurs
        Toronto Raptors
        Utah Jazz
        Washington Wizards
    """,
    "mlb": """
        Arizona Diamondbacks | Diamondbacks
        Atlanta Braves
        Baltimore Orioles
        Boston Red Sox | Red Sox
        Chicago Cubs
        Chicago White Sox | White Sox
        Cincinnati Reds
        Cleveland Guardians
        Colorado Rockies
        Detroit Tigers
        Houston Astros | Astros
        Kansas City Royals
        Los Angeles Angels
        Los Angeles Dodgers
        Miami Marlins
        Milwaukee Brewers
        Minnesota Twins
        New York Mets | Mets
        New York Yankees | Yankees
        Oakland Athletics
        Philadelphia Phillies | Phillies
        Pittsburgh Pirates
        San Diego Padres | Padres
        San Francisco Giants
        Seattle Mariners
        St. Louis Cardinals | Saint Louis Cardinals
        Tampa Bay Rays
        Texas Rangers
        Toronto Blue Jays
        Washington Nationals
    """,
    "nhl": """
        Anaheim Ducks
        Boston Bruins
        Buffalo Sabres
        Calgary Flames
        Carolina Hurricanes
        Chicago Blackhawks | Blackhawks
        Colorado Avalanche
        Columbus Blue Jackets | Blue Jackets
        Dallas Stars
        Detroit Red Wings | Red Wings
        Edmonton Oilers | Oilers
        Florida Panthers
        Los Angeles Kings
        Minnesota Wild
        Montreal Canadiens | Canadiens | Habs
        Nashville Predators
        New Jersey Devils
        New York Islanders
        New York Rangers
        Ottawa Senators
        Philadelphia Flyers
        Pittsburgh Penguins
        San Jose Sharks
        Seattle Kraken
        St. Louis Blues | Saint Louis Blues
        Tampa Bay Lightning
        Toronto Maple Leafs | Maple Leafs
        Utah Mammoth
        Vancouver Canucks
        Vegas Golden Knights | Golden Knights
        Washington Capitals
        Winnipeg Jets
    """,
}


def _words(text: str) -> str:
    """Lower-case words joined by single spaces, padded for whole-word finds."""
    return " " + " ".join(re.findall(r"[a-z0-9]+", (text or "").lower())) + " "


def _parse() -> dict:
    roster = {}
    for league, block in _ROSTER_TEXT.items():
        teams = []
        for line in block.strip().splitlines():
            names = [n.strip() for n in line.split("|") if n.strip()]
            if names:
                teams.append((names[0], tuple(_words(n) for n in names)))
        roster[league] = teams
    return roster


#: league -> [(full name, (padded wordings that name it alone, ...))]
ROSTER = _parse()


def teams_in(text: str) -> list:
    """`[(league, full name)]` for every roster team `text` names."""
    padded = _words(text)
    found = []
    for league, teams in ROSTER.items():
        for name, wordings in teams:
            if any(w in padded for w in wordings):
                found.append((league, name))
    return found


#: A question about a price says so. Only ever half of a markets route: the
#: other half is an entity that could be a listing.
MARKET_WORDS = re.compile(
    r"\b(stock|stocks|shares|share price|ticker|nasdaq|dow jones|s&p 500|"
    r"market cap|earnings|trading at|price target)\b", re.I)
#: "$NVDA" names a listing outright.
_CASHTAG = re.compile(r"(?<![A-Za-z0-9])\$[A-Za-z]{1,5}\b")


def full_names_in(teams: list, text: str) -> list:
    """Teams from a provider catalogue that `text` names by full name.

    Never by nickname or city alone: a scoreboard's `teams_named` may match a
    short name for `resolve`, where the question is already about
    sports, and that is how "pay my bills" named the Buffalo Bills (§265
    review). Routing decides whether a question is about sports at all."""
    padded = _words(text)
    found = []
    for team in teams or []:
        name = _words(str(team.get("name") or "")).strip()
        if len(name.split()) >= 2 and f" {name} " in padded:
            found.append(team)
    return found


def catalogue_team(text: str) -> str:
    """"<team> (<league>)" for a team in a catalogue this server already
    read that `text` names in full, or ""."""
    try:
        import balldontlie_scores  # §277

        for key, held in list(balldontlie_scores.TEAMS.items()):
            rows = [dict(r, name=r.get("full_name") or r.get("display_name")
                         or r.get("name")) for r in (held[1] if held else {}).values()]
            named = full_names_in(rows, text)
            if named:
                return f"{named[0].get('name', '')} ({key})"
    except Exception:  # noqa: BLE001 - a hint is never load-bearing
        pass
    return ""


def _can_serve(domain: str) -> bool:
    import live_facts

    for source in live_facts.sources_for(domain):
        try:
            ok, _why = source.diagnose()
        except Exception:  # noqa: BLE001 - a source that cannot say is not asked
            ok = False
        if ok:
            return True
    return False


def route(brief, query: str = "") -> tuple:
    """`(domain, why)` to ask a provider on, or `("", "")`.

    Only for a brief that named no domain; spends nothing; never names a
    domain nothing here can serve."""
    if brief is None or (getattr(brief, "live_domain", "") or ""):
        return "", ""
    query = query or getattr(brief, "query", "") or ""
    entity = getattr(brief, "live_entity", "") or ""
    subject = getattr(brief, "subject", "") or ""
    text = " ".join(t for t in (query, entity, subject) if t)

    why = ""
    teams = teams_in(text)
    if teams:
        why = f"names {teams[0][1]}"
    else:
        try:
            import named_slots

            slot = named_slots.find(text)
            if slot is not None:
                why = f"names {slot.name}"
        except Exception:  # noqa: BLE001 - a hint is never load-bearing
            pass
    if not why:
        held = catalogue_team(text)
        if held:
            why = f"names {held}"
    if why and _can_serve("sports"):
        return "sports", why

    cashtag = _CASHTAG.search(query)
    said = MARKET_WORDS.search(query)
    if cashtag or said:
        try:
            import live_sources

            listing = live_sources.looks_like_a_listing(entity)
        except Exception:  # noqa: BLE001
            listing = False
        if listing and _can_serve("markets"):
            word = cashtag.group(0) if cashtag else said.group(0).lower()
            return "markets", f"says {word!r} about {entity}"
    return "", ""


def apply(brief, query: str = ""):
    """Set `brief.live_route` and `live_route_why` from `route`. Returns the brief."""
    domain, why = route(brief, query)
    if domain:
        brief.live_route = domain
        brief.live_route_why = why
    return brief


def domain_of(brief) -> str:
    """The domain a lookup asks: the brief's own, else the code's route."""
    if brief is None:
        return ""
    return (getattr(brief, "live_domain", "") or ""
            or getattr(brief, "live_route", "") or "")


def entity_of(brief) -> str:
    """What a provider resolves: the bare name the brief gave, else its
    subject, else the raw query."""
    if brief is None:
        return ""
    for field in ("live_entity", "subject", "query"):
        text = (getattr(brief, field, "") or "").strip()
        if text:
            return text
    return ""


def without_entity(brief):
    """A copy of `brief` whose providers resolve its subject instead - the
    one retry when the bare name matched nothing (`live_facts._ask`)."""
    import copy

    other = copy.copy(brief)
    try:
        other.live_entity = ""
    except Exception:  # noqa: BLE001 - a frozen brief keeps its entity
        return None
    return other


def context_of(brief) -> str:
    """Every wording of the request, for spotting a sport word or a team."""
    if brief is None:
        return ""
    return " ".join(t for t in (getattr(brief, f, "") or ""
                                for f in ("live_entity", "subject", "query")) if t)
