"""The demonstration seed: who it invents, and how to take it back out.

Two halves of one decision, in one place because they have to agree.

`tools/seed_demo.py` writes the history the browse surfaces need on a fresh
install - three invented listeners, a handful of really-written episodes in
the shared cache, and plays against them. That is what makes Explore
non-empty and the crowd rails rankable on day one, and it is the right thing
to have while **showing** somebody the product.

It is the wrong thing to have while **measuring** it. Every seeded play is a
vote in `topics.taste`, every seeded script is a tile the crowd rails sort to
the front, and none of it came from a listener - so "is myFAM recommending
the right things" cannot be answered on a deployment that still holds it.
Some unknown share of the answer is three people who do not exist.

## Why this is a module in the root and not in `tools/`

Because `app.py` needs it. `POST /api/admin/wipe` exists for the case where
the wipe is actually needed - a container host, which is the one place there
is no shell - and an app importing from `tools/` reverses the dependency
every other file in the repo keeps. The command line is a tool; deciding what
a wipe *is* is not.

It also puts the listener ids in one place. Two hand-written lists agreeing
with each other is the same mistake made twice and then compared to itself,
which is §107's finding about the Dockerfile's own store list.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: Other people, so co-listener overlap has someone to overlap with and an
#: Explore card can say who sent it. Invented listeners are fine; invented
#: *episodes* would not be, which is why every script the seed writes is
#: really written.
LISTENERS: tuple[tuple[str, str, str], ...] = (
    ("demo-rachel", "Rachel Kim", "rachel"),
    ("demo-tom", "Tom Alvarez", "tomal"),
    ("demo-priya", "Priya Nair", "priya"),
)

#: Just the ids, which is what a wipe works in.
SEED_USER_IDS: tuple[str, ...] = tuple(user_id for user_id, _n, _h in LISTENERS)

#: The two scopes, named rather than passed as a boolean: "seed" and "all"
#: differ by an order of magnitude in what they destroy, and a parameter
#: called `everything=True` is a parameter somebody passes by accident.
SCOPES = ("seed", "all")


def wipe(*, cache, events, erase_listener, scope: str = "seed",
         dry_run: bool = True) -> dict:
    """Remove the seed, or everything. Returns what was (or would be) removed.

    Stores are passed in rather than imported, so this module knows nothing
    about `app.py` and can be exercised against a temporary set in a test.

    ## What each scope removes

    `seed` is exactly what `tools/seed_demo.py` wrote:

    * the three demonstration listeners, through `erase_listener` - the same
      per-store erasure account deletion uses, so events, mixes, social rows,
      preferences, quota counters, messages, saved items and shares all go
      together and a store added later is covered without editing this file;
    * the scripts those listeners authored, by `author`, which is the column
      the cache already keeps so Explore can leave somebody's own episodes
      off their own feed. **A script with no author is left alone**: it was
      not provably seed data, and guessing is how a real listener's episode
      gets deleted.

    `all` additionally empties the whole script cache and the whole event log,
    and with them everything that was derived from that log rather than stored
    beside it - the grown vocabulary, the cached tree and the engagement
    table; see `_forget_what_the_log_taught`. That is the true blank slate -
    "wipe all the fake episode titles completely so we start seeing only new
    ones" - and it is a bigger thing than it looks: real listening goes with
    it and the taste model starts from nothing for everybody. It costs
    nothing that cannot be regenerated (a script is about three cents, audio
    is never stored) and it does cost history that cannot.

    Neither scope touches accounts, credentials or the metering ledger.
    Somebody who signed up stays signed up; what the app spent stays
    reconcilable against an invoice.

    `dry_run` is the default, in both directions. This is not reversible, and
    a request body that forgot a field must not be the one that empties the
    event log.
    """
    if scope not in SCOPES:
        raise ValueError(f"{scope!r} is not one of {SCOPES}")
    report: dict = {"scope": scope, "dry_run": dry_run, "listeners": {}}

    if dry_run:
        # Counted rather than performed. `recent` is the only read the cache
        # offers over its rows and it is enough to say how many the seed
        # wrote, which is the number somebody wants before agreeing.
        seen = cache.recent(limit=500) if cache is not None else []
        report["scripts_by_seed"] = sum(
            1 for row in seen if row.get("author") in SEED_USER_IDS)
        report["scripts_total"] = len(seen)
        report["listeners"] = {u: "would be erased" for u in SEED_USER_IDS}
        if scope == "all":
            report["events_total"] = events.count()
            # Counted in the dry run because it is the one thing on this list
            # nobody expects to be on it. "Scripts and events" is what people
            # picture a wipe being; a vocabulary grown out of those events is
            # not, and a number is how it stops being a surprise.
            try:
                import topics

                report["categories_total"] = len(topics.category_tree().nodes())
            except Exception:  # noqa: BLE001 - a count is never load-bearing
                log.exception("could not count the category tree")
                report["categories_total"] = 0
        return report

    for user_id in SEED_USER_IDS:
        report["listeners"][user_id] = erase_listener(user_id)

    scripts = 0
    if cache is not None:
        if scope == "all":
            scripts = cache.clear()
        else:
            for user_id in SEED_USER_IDS:
                scripts += cache.forget_author(user_id)
    report["scripts_removed"] = scripts

    if scope == "all":
        report["events_removed"] = events.clear()
        # The live story pool is a cache of today's news rather than stored
        # data - the next sweep refills it - but it holds tiles composed
        # before the wipe, and somebody looking for a blank slate should not
        # find six of them still on myFAM.
        try:
            import stories

            report["stories_dropped"] = stories.pool().clear()
        except Exception:  # noqa: BLE001 - a browse cache is never load-bearing
            log.exception("could not drop the story pool")
            report["stories_dropped"] = 0
        report.update(_forget_what_the_log_taught())
    return report


def _forget_what_the_log_taught() -> dict:
    """Everything `all` has to take with the event log, because it came *from*
    the event log.

    This is the half of a blank slate that is not a store anybody thinks of.
    Three things are derived from events rather than stored beside them, and
    each would otherwise go on ranking a feed built from episodes that no
    longer exist - invisibly, because none of them is a row somebody counts:

    * **the grown vocabulary** (`categories.py`), minted from what listeners
      searched for. `taste` re-reads an event's own text against the current
      tree, so a tree that outlived its events is a vocabulary with nothing
      left to say it about;
    * **the cached tree**, which `topics.category_tree` holds per process. A
      cleared table read through a warm handle is a wipe that reports success
      and changes nothing until the next restart - a silent half-failure,
      which is the thing this whole file is written against;
    * **the engagement table** (§121), a click-through rate held in process
      for `ENGAGEMENT_TTL`, computed from the impressions and plays being
      deleted one line above.

    **What a wipe puts back.** The starter vocabulary (`category_seed.py`) is
    re-applied immediately after the clear, and that is this function's own
    rule read the other way round rather than an exception to it: what goes
    is what the *log* taught, and a seed node was never taught by anything -
    it is a declared floor that a deployment with no listening is supposed to
    have, which is precisely what a wiped deployment is. Leaving it out would
    make a wipe quietly destructive of something no listener produced, and
    the next boot would mint it straight back anyway, so the only difference
    would be which page saw the tree half-built.

    Never raises. A wipe that had emptied the log and then failed here would
    be the worst outcome available: the destructive half done, the tidying
    half not, and an exception where the report should be.
    """
    out: dict = {}
    try:
        import categories
        import topics

        out["categories_dropped"] = topics.category_tree().clear()
        # After the clear rather than instead of it: `clear` reloads its own
        # index, and this drops the *module-level* handle so the next reader
        # opens the emptied table rather than inheriting a live object.
        topics.reset_category_tree()
        topics.reset_engagement()
        # The fitted ranking order is dropped by `EventStore.clear` itself,
        # in the same statement batch as the log it was fitted to (§128).
        # And the floor goes back under it. Through `category_tree()` again,
        # deliberately: the handle above has just been dropped, so this opens
        # the emptied table rather than writing through the stale object the
        # line above exists to get rid of.
        out["categories_seeded"] = categories.apply_seed(topics.category_tree())
    except Exception:  # noqa: BLE001 - see the docstring
        log.exception("could not clear what the event log taught")
        out.setdefault("categories_dropped", 0)
        out.setdefault("categories_seeded", 0)
    return out
