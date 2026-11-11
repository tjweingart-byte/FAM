"""What changed in a listener's mixes since they last listened (§538).

The owner's picks from round five of the signal designs: **R5** on the myFAM
mix card - the story's picture with the mix's own cover in its corner, a
ring, one line naming the story and a "Trending" or "Major update" pill where
the arrow was - and **Q5** inside the mix: the changed topic's row shows what
the listener last heard and what is true now, as two bubbles joined by an
arrow, under a "Start with what changed" button.

Two signals, both measurements and never a result (`tile-title-angle`):

* **Trending** - a story that names a subject the mix follows is in this
  slot's Trending edition (the world's most-covered stories, `TRENDING.md`),
  or is in the live story pool carried by at least `TRENDING_MIN_OUTLETS`
  outlets, and appeared after the listener last heard that topic.
* **Major update** - a game the mix follows went `final` (`live_facts`'
  closed vocabulary, from the scoreboard sweep) after the listener last heard
  that topic. `stories.Story.status_at` is the clock; `live_as_of` moves on
  every sweep and could not say it.

"Names a subject the mix follows" is `topics.episode_subject` - the same
reading the player's (+) uses to add an episode's topic to a mix - kept to its
specific answers: a named team or company, a subject named by its label, or
its subtag. Its last resort, the subject standing for a whole facet, is
refused here: "Fed holds rates" is money news, and money news is not news
about "Stocks & Economy" for somebody who follows it (§155's rule that a
field is not a subject). A typed topic gets no signal: nothing short of a
model call can tell a subject from a kind of thing (`mixes.TYPED_HEAD`).

Quiet by default and quiet once heard: an item whose today's episode the
listener has played carries nothing, and a mix shows one signal at most, the
strongest. Everything here is a read of what is already held - the pool and
the edition in memory, the listening history, the script cache - so nothing
on the page-load path waits, and nothing is written.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable, Optional

TRENDING = "trending"
UPDATE = "update"
LABELS = {TRENDING: "Trending", UPDATE: "Major update"}

#: Outlets a live-pool story needs before it counts as trending. A story in
#: the Trending edition needs none: being there is the measurement. A
#: starting value, to be tuned against what the deployment's pool carries.
TRENDING_MIN_OUTLETS = 15
#: How far back a signal reaches for a topic the listener has never heard.
NEVER_HEARD_WINDOW = 24 * 3600.0
#: Days of history searched for the last time a topic was heard - the
#: history's own two weeks, less today.
SINCE_DAYS = 13


@dataclass(frozen=True)
class Subject:
    """What a followed mix item is about: a catalogue id and its focus."""

    subject: str
    focus: tuple = ()


def subject_of(item) -> Optional[Subject]:
    """The followed subject an item is about, or None (bank and typed items)."""
    if not getattr(item, "follow", False):
        return None
    base = getattr(item, "base", "") or ""
    if not base.startswith("f:") or len(base) < 3:
        return None
    return Subject(base[2:], tuple(f.lower() for f in getattr(item, "focus", ()) or ()))


def specific_subject(text: str) -> tuple:
    """`topics.episode_subject`, without its facet-wide last resort.

    ("", "") unless the words name a specific (a team, a company), a
    catalogue subject by its label or id, or a subject's subtag.
    """
    import topics

    subject, focus = topics.episode_subject(text)
    if not subject or focus:
        return subject, focus
    entry = topics.CATALOGUE_BY_ID.get(subject)
    if entry is None:
        return "", ""
    low = " " + " ".join(topics._WORD.findall((text or "").lower())) + " "

    def named(phrase: str) -> bool:
        words = " ".join(topics._WORD.findall(phrase.lower()))
        return bool(words) and (" " + words + " ") in low

    if named(entry.label) or named(entry.id.replace("-", " ")):
        return subject, ""
    if len(entry.tags) > 1 and entry.tags[1] in set(topics.tags_for_text(text)):
        return subject, ""
    return "", ""


def story_text(story) -> str:
    anchors = " ".join(getattr(story, "anchors", ()) or ())
    return " ".join(x for x in (getattr(story, "title", ""), getattr(story, "subject", ""),
                                getattr(story, "query", ""), anchors) if x)


def matches(subject: Subject, story_subject: tuple) -> bool:
    """Whether a story's (subject, focus) is about this followed item.

    A whole subject ("NFL") takes any story about it, a team's included; a
    narrowed one ("NFL · Eagles") takes only stories naming that team.
    """
    sub, focus = story_subject
    if not sub or sub != subject.subject:
        return False
    if not subject.focus:
        return True
    return bool(focus) and focus.lower() in subject.focus


@dataclass
class Found:
    """One signal on one item."""

    kind: str
    item_id: str
    topic: str
    headline: str
    story_id: str = ""
    query: str = ""
    at: float = 0.0
    coverage: int = 0
    picture: str = ""

    @property
    def strength(self) -> tuple:
        # A result in comes before a trend; then wider coverage, then newer.
        return (1 if self.kind == UPDATE else 0, self.coverage, self.at)

    def card(self) -> dict:
        return {"kind": self.kind, "label": LABELS[self.kind], "item_id": self.item_id,
                "topic": self.topic, "headline": self.headline,
                "picture": self.picture}


@dataclass
class Heard:
    """What a listener last heard of one item: when, what it was called."""

    at: float = 0.0
    title: str = ""
    today: bool = False


def item_signal(item, edition: Iterable, pool: Iterable, heard: Heard,
                now: Optional[float] = None) -> Optional[Found]:
    """The strongest signal on one mix item, or None.

    `edition` and `pool` are stories (`stories.Story`-shaped); `heard` is
    what the listener last heard of this item. Pure: no clock but `now`,
    no store, so a test can say exactly what it means.
    """
    now = time.time() if now is None else now
    subject = subject_of(item)
    if subject is None or heard.today:
        return None
    since = heard.at if heard.at else now - NEVER_HEARD_WINDOW
    best: Optional[Found] = None

    def consider(found: Found) -> None:
        nonlocal best
        if best is None or found.strength > best.strength:
            best = found

    topic = getattr(item, "title", "") or ""
    for story in edition or ():
        if float(getattr(story, "first_seen", 0.0) or 0.0) <= since:
            continue
        if not matches(subject, specific_subject(story_text(story))):
            continue
        consider(Found(TRENDING, item.id, topic, story.title, story.id, story.query,
                       story.first_seen, int(story.coverage or 0)))
    for story in pool or ():
        if not matches(subject, specific_subject(story_text(story))):
            continue
        status_at = float(getattr(story, "status_at", 0.0) or 0.0)
        if (getattr(story, "live_status", "") or "") == "final" and status_at > since:
            consider(Found(UPDATE, item.id, topic, story.title, story.id, story.query,
                           status_at, int(story.coverage or 0)))
        elif (int(getattr(story, "coverage", 0) or 0) >= TRENDING_MIN_OUTLETS
              and float(getattr(story, "first_seen", 0.0) or 0.0) > since):
            consider(Found(TRENDING, item.id, topic, story.title, story.id, story.query,
                           story.first_seen, int(story.coverage or 0)))
    return best


def strongest(found: Iterable[Optional[Found]]) -> Optional[Found]:
    """The one signal a mix card shows: the strongest of its items'."""
    best = None
    for f in found:
        if f is not None and (best is None or f.strength > best.strength):
            best = f
    return best


def bubbles(heard: Heard, now_title: str, day: str) -> dict:
    """Q5's two bubbles: what was last heard, and what is true today.

    `then` is None when the listener has never heard this topic - one bubble,
    never an invented "before".
    """
    then = {"day": day, "title": heard.title} if heard.at and heard.title else None
    return {"then": then, "now": {"title": now_title}}
