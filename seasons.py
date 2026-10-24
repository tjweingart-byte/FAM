"""Where a league is in its year, for choosing examples (PROBLEMS.md §308).

The owner (10.23): "stories about certain topics can change throughout the
year (nfl in season versus out of season), and the example script should
change with it." In season, a question about a team is mostly a recap, a
preview or a game under way; out of season it is a trade, a contract, the
draft. An example built on one is the wrong shape to show the other.

**A phase is worked out in code, from a calendar, never by a model**: each
league below has the dates its phases usually begin, and a node of the
category tree takes the calendar of the nearest league above it - the Eagles
take the NFL's. Four phases: `preseason`, `regular_season`, `postseason`,
`offseason`. A node with no league above it has no phase (`""`), and nothing
about its episodes changes.

**What a phase is for, and what it is not.** It only chooses which examples
a writer is shown (`script_generator.choose_examples`). It is never told to
the writer, never evidence, and never a claim about the world: the calendars
are approximate - a season that starts a week late is in "regular_season" a
week early here - and the cost of that is a slightly less fitting example,
never a wrong fact ([live-facts]). Whether a game is on is still only ever
said by the scoreboard.
"""
from __future__ import annotations

import datetime
from typing import Iterable, Optional

PHASES = ("preseason", "regular_season", "postseason", "offseason")

#: Each league's year as `(month, day, phase)` - the date each phase usually
#: begins, in calendar order; the last one runs round to the first. Keys are
#: category-tree ids (`category_seed.py`). Approximate by design (above):
#: the usual dates, not this season's announced ones.
CALENDARS: dict[str, tuple] = {
    "nfl": ((2, 16, "offseason"), (8, 1, "preseason"),
            (9, 5, "regular_season"), (1, 10, "postseason")),
    "college football": ((1, 21, "offseason"), (8, 1, "preseason"),
                         (8, 25, "regular_season"), (12, 14, "postseason")),
    "nba": ((6, 23, "offseason"), (10, 1, "preseason"),
            (10, 21, "regular_season"), (4, 14, "postseason")),
    "wnba": ((10, 21, "offseason"), (5, 1, "preseason"),
             (5, 16, "regular_season"), (9, 14, "postseason")),
    "mlb": ((11, 6, "offseason"), (2, 20, "preseason"),
            (3, 27, "regular_season"), (10, 1, "postseason")),
    "nhl": ((6, 21, "offseason"), (9, 20, "preseason"),
            (10, 7, "regular_season"), (4, 18, "postseason")),
    "premier league": ((5, 26, "offseason"), (7, 10, "preseason"),
                       (8, 15, "regular_season")),
    "bundesliga": ((5, 26, "offseason"), (7, 10, "preseason"),
                   (8, 22, "regular_season")),
    "champions league": ((6, 2, "offseason"), (9, 15, "regular_season"),
                         (2, 10, "postseason")),
    "major league soccer": ((12, 11, "offseason"), (1, 20, "preseason"),
                            (2, 21, "regular_season"), (10, 21, "postseason")),
    "formula one": ((12, 11, "offseason"), (2, 15, "preseason"),
                    (3, 6, "regular_season")),
    "nascar": ((11, 11, "offseason"), (2, 10, "regular_season"),
               (9, 2, "postseason")),
}


def league_of(path: Iterable[str]) -> str:
    """The nearest league with a calendar on a node's path (nearest first),
    or "" when there is none."""
    for node in path or ():
        if node in CALENDARS:
            return node
    return ""


def phase_on(league: str, day: datetime.date) -> str:
    """The phase `league` is usually in on `day`, or "" for no calendar."""
    calendar = CALENDARS.get(league)
    if not calendar:
        return ""
    starts = sorted(calendar)
    today = (day.month, day.day)
    current = starts[-1][2]   # before the year's first start: last year's last
    for month, dom, phase in starts:
        if (month, dom) <= today:
            current = phase
    return current


def today() -> datetime.date:
    """Today, UTC: a phase is a matter of days, never hours. Its own
    function so a test can pin the date."""
    return datetime.datetime.now(datetime.timezone.utc).date()


def phase_for(path: Iterable[str], now: Optional[float] = None) -> tuple:
    """`(league, phase)` for a node's path (nearest first) on the day `now`
    falls in (UTC; today when None). `("", "")` when no league is above the
    node."""
    league = league_of(path)
    if not league:
        return "", ""
    day = today() if now is None else datetime.datetime.fromtimestamp(
        now, datetime.timezone.utc).date()
    return league, phase_on(league, day)
