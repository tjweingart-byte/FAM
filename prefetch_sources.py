"""Where prefetch gets its guesses: the browse surfaces, asked in advance.

`prefetch.py` is the machinery and knows nothing about topics, mixes or feeds.
This is the other side of that seam - the part that reads what myFAM and
DailyFAM already compute and turns it into candidates. Kept separate so the
machinery can be tested without a topic bank, and so adding a surface is a new
class here rather than an edit there.

Every candidate carries a **reason**, and that is the contextual-relevance
claim being made out loud: "second in trending", "in their 'At the gym' mix".
It survives all the way to the report, because the only way to answer
CLAUDE.md's open question - how much to prefetch - is to see which kinds of
guess get taken and which are paid for and dropped.

The ordering principle, which is a cost design and not a ranking one
-------------------------------------------------------------------
CLAUDE.md's rule for the feed is **one bank for everyone, personalisation in
the ordering, not the inventory** - so two people tapping the same tile share
one script. That makes the *shared* guesses structurally cheaper to warm than
the personal ones:

* **Trending** is the same list for every listener, so one warmed script can be
  taken by everybody who taps it. Best value per dollar in the app, and the
  only source that is worth running with no listener in mind at all.
* **A mix member** is the strongest prediction FAM has: somebody wrote down
  that they want this subject, every day. A bank topic in a mix is shared with
  everyone else who has it; a typed one is a script a day for one person, which
  `mixes.MixItem` already says out loud and which is why it is weighted below.
* **A listener's feed** is the most personal and the least shareable, so it
  goes last and is capped hardest.

None of this is a hard ordering: `Prefetcher.plan` interleaves the sources so
one cannot take the whole budget, precisely so there is evidence about each.

**Nothing here calls a model or the network.** A source that costs money to
*ask* turns a speculative saving into a certain spend.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

import config
from prefetch import Candidate

log = logging.getLogger(__name__)

#: The length a warmed episode is written at.
#:
#: Duration is part of the cache key - a 3-minute script is written
#: differently from a 10-minute one, not cut down from it - so a warm at the
#: wrong length is a warm nobody ever finds. It is whatever the interface opens
#: on, read from `config` rather than repeated here; a deployment whose
#: listeners mostly pick something else should change that rather than warm
#: several lengths, which multiplies the spend by the number of lengths and
#: the waste along with it.
DEFAULT_MINUTES = config.DEFAULT_MINUTES


class TrendingSource:
    """What everyone is playing. The cheapest thing in the app to warm.

    Identical for every listener by construction (`topics.rank_most_played`), so
    one warmed script is taken by everybody who taps that tile - the hit rate
    here is per *tile*, not per person, and it is the only source worth running
    on an empty server with nobody signed in.

    **The name is a ledger key, not the rail's name.** The rail it warms is
    "What FAM can't stop listening to"; `trending` now means the world, on a
    different row from a different place. Renaming this would reset every
    recorded warm and take against it, which is the only number that can
    answer "is prefetching this worth it" - so the string stays and the reason
    below says what it actually is.
    """

    name = "trending"

    def __init__(self, store, minutes: int = DEFAULT_MINUTES) -> None:
        self.store = store
        self.minutes = minutes

    def candidates(self, listener: str = "", limit: int = 6) -> list:
        import topics

        out: list = []
        for rank, topic in enumerate(topics.rank_most_played(self.store)[:limit]):
            out.append(Candidate(
                query=topic.query,
                minutes=self.minutes,
                source=self.name,
                reason=(f"#{rank + 1} in what FAM can't stop listening to, "
                        f"the same tile for everyone"),
                topic_id=topic.id,
                weight=float(limit - rank),
                # Deliberately blank: this guess is not about one listener, and
                # marking it with one would make the ledger report a shared win
                # as a personal one.
                listener="",
            ))
        return out


class MixSource:
    """The subjects somebody asked for every morning.

    The strongest prediction FAM has, and the one with a clock on it: a mix
    holds topic ids rather than audio precisely so it is fresh every day, which
    means every member is an episode that will be generated tomorrow and does
    not exist yet.

    Bank members are weighted above typed ones because they are shared -
    `mixes.MixItem` says so in its own docstring, and warming a typed question
    is a script a day for exactly one listener.
    """

    name = "mixes"

    def __init__(self, store, minutes: int = DEFAULT_MINUTES) -> None:
        self.store = store
        self.minutes = minutes

    def candidates(self, listener: str = "", limit: int = 6) -> list:
        if not listener:
            return []
        out: list = []
        for mix in self.store.list_for_user(listener):
            for item in getattr(mix, "items", []):
                custom = bool(getattr(item, "custom", False))
                out.append(Candidate(
                    query=item.query,
                    minutes=self.minutes,
                    source=self.name,
                    reason=f"in their {mix.name!r} mix"
                           + (" (typed, so shared with nobody)" if custom else ""),
                    topic_id="" if custom else item.id,
                    weight=1.0 if custom else 2.0,
                    listener=listener,
                ))
        out.sort(key=lambda c: (-c.weight, c.query))
        return out[:limit]


class FeedSource:
    """What myFAM is about to show this listener.

    The same ranking the page draws itself from (`topics.build_feed`), so a
    warmed tile and a shown tile cannot disagree - the alternative is a second
    implementation of "what next", which CLAUDE.md already refused once for
    `rank_next_up`.

    Most personal and least shareable, so it is asked last and capped hardest.
    """

    name = "feed"

    def __init__(self, store, minutes: int = DEFAULT_MINUTES,
                 sections: Iterable[str] = ("from_history", "followers"),
                 circle_of=None) -> None:
        self.store = store
        self.minutes = minutes
        #: `listener -> the ids whose listening fills their friends rail`.
        #: Passed in rather than looked up, so this module keeps knowing
        #: nothing about the social graph - and `None` is not a fallback to
        #: something else, it is an empty friends rail, which is exactly what
        #: a deployment with no graph shows. Warming a rail this listener will
        #: not be shown is money spent on a tile nobody can tap.
        self.circle_of = circle_of
        #: Which rails are worth warming. Not trending - `TrendingSource`
        #: already has it and warming it twice would double-count its hits.
        #:
        #: It was `from_history` and `might_like`; Explore New came off the
        #: page, and warming a rail nobody is shown is spending money on a
        #: tile that cannot be tapped. `followers` is the next most personal
        #: thing the page actually draws.
        self.sections = tuple(sections)

    def candidates(self, listener: str = "", limit: int = 4,
                   interests: Iterable[str] = ()) -> list:
        if not listener:
            return []
        import topics

        circle = self.circle_of(listener) if self.circle_of else ()
        feed = topics.build_feed(self.store, listener, interests=interests,
                                 circle=circle)
        by_key = {s["key"]: s for s in feed.get("sections", [])}
        out: list = []
        for key in self.sections:
            section = by_key.get(key)
            if not section:
                continue
            for rank, tile in enumerate(section.get("topics", [])):
                out.append(Candidate(
                    query=tile.get("query", ""),
                    minutes=self.minutes,
                    source=self.name,
                    reason=f"#{rank + 1} in their {section.get('title', key)!r} rail",
                    topic_id=tile.get("id", ""),
                    weight=float(len(self.sections) - self.sections.index(key)),
                    listener=listener,
                ))
        out.sort(key=lambda c: -c.weight)
        return out[:limit]


class ThreadSource:
    """The follow-up the last episode predicted.

    `<<NEXT: ...>>` is already written, already stored beside the script, and
    already offered as a one-tap chip - so the prediction exists whether or not
    anything is warmed, and warming it is the difference between the chip being
    instant and the chip being an ordinary episode.

    Per-listener and narrow, so it is capped at the handful of most recent
    threads rather than the whole history.
    """

    name = "threads"

    def __init__(self, store, minutes: int = DEFAULT_MINUTES) -> None:
        self.store = store
        self.minutes = minutes

    def candidates(self, listener: str = "", limit: int = 3) -> list:
        if not listener:
            return []
        try:
            # `open_threads`, not `summary` - the summary reports a *count* of
            # open threads, and counting them tells you nothing about what to
            # warm. This is the same list Go Deeper draws its chips from, so a
            # warmed thread and an offered one are the same thread.
            open_threads = self.store.open_threads(listener, limit=limit)
        except Exception as exc:  # noqa: BLE001 - a source must not break a cycle
            log.warning("prefetch: the thread source could not read open "
                        "threads for %s: %s", listener, exc)
            return []

        out: list = []
        for rank, thread in enumerate(open_threads[:limit]):
            query = (thread.get("thread") or "").strip()
            if not query:
                continue
            out.append(Candidate(
                query=query,
                minutes=self.minutes,
                source=self.name,
                reason=f"the follow-up predicted after {thread.get('from_title') or 'an episode'}",
                weight=float(limit - rank),
                listener=listener,
            ))
        return out


def install(event_store=None, mix_store=None,
            minutes: int = DEFAULT_MINUTES, social_store=None) -> list:
    """Register whichever sources this deployment can actually answer.

    Called at startup, and **idempotent**: it replaces the sources it manages
    rather than adding to them, because startup is the statement "these are
    this deployment's sources" and a statement has to be re-makeable. A second
    startup in one process - every test that opens a TestClient, and any
    in-process reload - would otherwise hit the duplicate guard and take the
    server down at boot. Sources registered by anything else are left alone.

    A store that is not there is not an error - a deployment with no mixes
    simply has no mix source - but it *is* reported, because a prefetcher
    quietly running on one source out of four looks identical from outside to
    one running on all of them.
    """
    import prefetch

    for name in ("trending", "feed", "threads", "mixes"):
        prefetch.unregister(name)

    installed: list = []
    if event_store is not None:
        for source in (TrendingSource(event_store, minutes),
                       FeedSource(event_store, minutes,
                                  circle_of=(social_store.circle_of
                                             if social_store is not None else None)),
                       ThreadSource(event_store, minutes)):
            prefetch.register(source)
            installed.append(source.name)
    if mix_store is not None:
        source = MixSource(mix_store, minutes)
        prefetch.register(source)
        installed.append(source.name)

    missing = []
    if event_store is None:
        missing.append("no event store: trending, feed and threads are unavailable")
    if mix_store is None:
        missing.append("no mix store: mixes are unavailable")
    if missing:
        log.warning("prefetch sources installed partially (%s); %s",
                    ", ".join(installed) or "none", "; ".join(missing))
    else:
        log.info("prefetch sources installed: %s", ", ".join(installed))
    return installed


def report(installed: Optional[list] = None) -> dict:
    """Which surfaces this server can predict for - for /api/health."""
    import prefetch

    live = [getattr(s, "name", "?") for s in prefetch.sources()]
    known = ["trending", "mixes", "feed", "threads"]
    return {
        "available": known,
        "installed": live,
        "missing": [name for name in known if name not in live],
    }
