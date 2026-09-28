"""Named broadcast slots: words that look generic and name one specific game.

"Sunday night football recap" reads, word by word, as *a recap of football
played on a Sunday night*. It is not. **Sunday Night Football is a proper
name** - NBC's one prime-time NFL game each Sunday - and a listener who typed
it wants that game and no other. FAM once answered it with Bengals v Steelers,
an afternoon kick-off, because nothing between the typed words and the search
knew the phrase was a name: the model read it as a description, and every NFL
report from that Sunday matched the description.

A model *usually* knows this. "Usually" is the wrong standard for the one
failure a listener spots in the first sentence, so the names FAM is most
likely to be asked about are resolved here, in code, before and after the EI
call - the same reason `research` computes "yesterday" rather than asking a
model to. The EI prompt carries the general rule for everything this table
does not list.

Each slot says what it names in words the writer can use, the words a report
of it would contain, and **when it kicks off** - the last of which is what
lets a scoreboard pick the right row out of a whole Sunday's card
(`live_sources.ApiSportsSource.resolve`) rather than the first row that
happens to contain the word "football".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class NamedSlot:
    key: str
    #: The name as it is written in a report of it.
    name: str
    #: What it names, for the writer and the brief.
    definition: str
    league: str
    #: A regex over the lowercased query.
    pattern: str
    #: Monday is 0, as `datetime.weekday` counts. None means any day.
    weekday: Optional[int] = None
    #: Earliest US Eastern kick-off hour (24h) a game in this slot has.
    earliest_hour_et: Optional[int] = None
    #: The sport key in `live_sources.SPORTS`, when there is one.
    sport: str = ""

    @property
    def when(self) -> str:
        days = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                "Saturday", "Sunday")
        day = days[self.weekday] if self.weekday is not None else "its"
        if self.earliest_hour_et is None:
            return f"{day} slot"
        hour = self.earliest_hour_et % 12 or 12
        return f"{day} night, kick-off from about {hour}pm Eastern"


SLOTS = (
    NamedSlot(
        key="snf", name="Sunday Night Football", league="NFL",
        definition=("NBC's single prime-time NFL game on Sunday night, "
                    "kicking off around 8:20pm Eastern - not the afternoon "
                    "games played earlier the same Sunday"),
        pattern=r"\bsunday[\s-]+night[\s-]+football\b|\bsnf\b",
        weekday=6, earliest_hour_et=19, sport="american-football"),
    NamedSlot(
        key="mnf", name="Monday Night Football", league="NFL",
        definition=("ESPN's prime-time NFL game (sometimes a doubleheader) on "
                    "Monday night, kicking off from around 7:15pm Eastern"),
        pattern=r"\bmonday[\s-]+night[\s-]+football\b|\bmnf\b",
        weekday=0, earliest_hour_et=19, sport="american-football"),
    NamedSlot(
        key="tnf", name="Thursday Night Football", league="NFL",
        definition=("Amazon Prime Video's single prime-time NFL game on "
                    "Thursday night, kicking off around 8:15pm Eastern"),
        pattern=r"\bthursday[\s-]+night[\s-]+football\b|\btnf\b",
        weekday=3, earliest_hour_et=19, sport="american-football"),
    NamedSlot(
        key="snb", name="Sunday Night Baseball", league="MLB",
        definition=("the one nationally televised prime-time MLB game on "
                    "Sunday night - not the afternoon games that day"),
        pattern=r"\bsunday[\s-]+night[\s-]+baseball\b",
        weekday=6, earliest_hour_et=18, sport="baseball"),
)


def find(text: str) -> Optional[NamedSlot]:
    """The slot this text names, if it names one."""
    lowered = " ".join((text or "").lower().split())
    for slot in SLOTS:
        if re.search(slot.pattern, lowered):
            return slot
    return None


def by_key(key: str) -> Optional[NamedSlot]:
    return next((s for s in SLOTS if s.key == key), None)


def _eastern():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/New_York")
    except Exception:  # noqa: BLE001 - no tz database: fixed EDT offset
        from datetime import timedelta
        return timezone(timedelta(hours=-4))


def in_slot(slot: NamedSlot, kickoff_utc: Optional[datetime]) -> bool:
    """Does a game kicking off at this moment belong to this slot?

    Unknown kick-off is **not** in the slot: picking a game whose time we
    cannot read is exactly the guess this module exists to stop.
    """
    if kickoff_utc is None:
        return False
    local = kickoff_utc.astimezone(_eastern())
    if slot.weekday is not None and local.weekday() != slot.weekday:
        return False
    if slot.earliest_hour_et is not None and local.hour < slot.earliest_hour_et:
        return False
    return True
