"""Today's order of a mix: best stories first, a new order every day
(the owner's 11.14 notes, PROBLEMS.md §616).

    "It hints what's in the mix, previews a headline, and automatically
    reorders the episodes (and displayed tiles on cover) to be in order of
    most trending/relevant stories... so you don't hear the same order every
    day and it prioritizes best stories first."

A mix holds topics in the order its owner put them (`mixes.Mix.items`, the
list they drag on the mix's page). That order is the mix's *contents*; this
module decides the order *today's episodes play in*, and the tiles drawn on
its cover, from measurements of today:

* **A result in** - a game the mix follows went final since the listener
  last heard that topic (`signals.UPDATE`, §545).
* **Trending** - a story about the topic is in this slot's Trending edition
  or carried by many outlets, since they last heard it (`signals.TRENDING`).
* **Coverage** - how many outlets carry today's most-covered story about
  the topic, heard or not, on a log scale; a story in the Trending edition
  counts for a little more (it is the world's top stories).
* **Ready** - today's episode is already written, so it starts at once
  (`ready_first`: a sort, never a filter).
* **Taste** - how much this listener plays the topic's subjects, from the
  same profile every rail reads (`topics.taste`), scaled within the mix.

**Heard today goes last**, whatever it scores: the mix still plays it, at
the end, and its picture leaves the front of the cover.

Ties are broken by the owner's order **turned by one place a day**, so a
mix with nothing to tell its topics apart still opens on a different one
each morning - "you don't hear the same order every day" - and the same
day always gives the same order (a reload never reshuffles a listener's
playlist under them).

Every input is a measurement of something held - the story pool, the
Trending edition, the script cache, the listening history, the event log -
never a model call and never a result claimed (`tile-title-angle`).
Pure: no store, no clock but the arguments, so a test can say exactly what
each weight does. `app._mix_today` gathers the inputs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

#: The weights, starting values to tune against what listeners finish.
#: A result in outranks a trend, which outranks wide coverage alone, which
#: outranks a topic merely being ready or liked.
UPDATE_WEIGHT = 3.0
TRENDING_WEIGHT = 2.0
EDITION_WEIGHT = 1.0
COVERAGE_WEIGHT = 1.5
READY_WEIGHT = 0.75
TASTE_WEIGHT = 1.0
#: Outlets at which coverage counts in full; fewer count on a log scale.
COVERAGE_FULL = 40
#: How far back a story counts as today's coverage of a topic.
COVERAGE_WINDOW = 24 * 3600.0
#: Tiles drawn on a cover from today's order.
THUMBS = 4

#: Why an item is where it is, as the card words it - always a measurement.
WHY_UPDATE = "update"
WHY_TRENDING = "trending"
WHY_COVERED = "covered"


@dataclass
class Entry:
    """What is known today about one item of one mix."""

    item_id: str
    #: Where the owner put it (0 first).
    position: int
    heard_today: bool = False
    #: Today's episode is written (its title is in the cache).
    written: bool = False
    #: "update", "trending" or "" (`signals.item_signal`).
    signal: str = ""
    #: Outlets carrying today's most-covered story about it; 0 for none.
    coverage: int = 0
    #: That story is in this slot's Trending edition.
    in_edition: bool = False
    #: The listener's taste for its subjects, as `topics._affinity` scores.
    affinity: float = 0.0
    #: Today's episode title, else the story's headline, else "".
    headline: str = ""
    #: Today's picture for it (the player's own), else the item's.
    picture: str = ""


def affinity(tags: Sequence[str], profile: dict, weight) -> float:
    """A listener's taste for an item's subjects, scored as `topics._affinity`
    scores a tile: a weighted sum over its tags, damped by how many it has.
    `weight` is `topics.tag_weight`, passed in so this module needs no
    vocabulary."""
    if not tags or not profile:
        return 0.0
    total = sum(profile.get(t, 0.0) * weight(t) for t in tags)
    return total / math.sqrt(len(tags))


def coverage_score(outlets: int) -> float:
    """0 for none, 1 at `COVERAGE_FULL` outlets and over, log in between -
    the tenth outlet means more than the fortieth."""
    if outlets <= 0:
        return 0.0
    return min(1.0, math.log1p(outlets) / math.log1p(COVERAGE_FULL))


def score(entry: Entry, taste_scale: float) -> float:
    """One item's score today. `taste_scale` is the largest affinity in the
    mix, so taste counts the same in a mix of liked topics as in a mix of
    ones never played."""
    total = 0.0
    if entry.signal == WHY_UPDATE:
        total += UPDATE_WEIGHT
    elif entry.signal == WHY_TRENDING:
        total += TRENDING_WEIGHT
    if entry.in_edition:
        total += EDITION_WEIGHT
    total += COVERAGE_WEIGHT * coverage_score(entry.coverage)
    if entry.written:
        total += READY_WEIGHT
    if taste_scale > 0:
        total += TASTE_WEIGHT * max(-1.0, min(1.0, entry.affinity / taste_scale))
    return total


def why(entry: Entry) -> str:
    """What the card may say about an item: a result in, trending, widely
    covered - or "" when nothing measured puts it forward."""
    if entry.signal in (WHY_UPDATE, WHY_TRENDING):
        return entry.signal
    if entry.in_edition or entry.coverage >= 10:
        return WHY_COVERED
    return ""


def order(entries: Sequence[Entry], day: int) -> list[Entry]:
    """Today's order: unheard before heard, then best score first, ties in
    the owner's order turned by `day` places (an ordinal: a new day, a new
    first among equals; the same day, the same order)."""
    entries = list(entries)
    n = len(entries) or 1
    scale = max((abs(e.affinity) for e in entries), default=0.0)

    def key(e: Entry):
        return (1 if e.heard_today else 0,
                -round(score(e, scale), 6),
                (e.position - day) % n)

    return sorted(entries, key=key)


def headline(ordered: Iterable[Entry]) -> Optional[Entry]:
    """The item the card previews: the first in today's order that has
    something to say - today's episode title or a story's headline."""
    for e in ordered:
        if e.headline and not e.heard_today:
            return e
    for e in ordered:
        if e.headline:
            return e
    return None


#: Tiles stacked on a card's cover (option A, §616).
STACK = 3


def tiles(ordered: Iterable[Entry], limit: int = STACK) -> list[Entry]:
    """The stories stacked on the card's cover, best on top: the first in
    today's order that have a picture, none twice, heard today included
    only if nothing else has one (it is last in the order anyway)."""
    out: list[Entry] = []
    seen: set = set()
    for e in ordered:
        if e.picture and e.picture not in seen:
            seen.add(e.picture)
            out.append(e)
        if len(out) >= limit:
            break
    return out


def thumbs(ordered: Iterable[Entry], limit: int = THUMBS) -> list[str]:
    """The pictures a cover draws, in today's order, none twice."""
    out: list[str] = []
    for e in ordered:
        if e.picture and e.picture not in out:
            out.append(e.picture)
        if len(out) >= limit:
            break
    return out
