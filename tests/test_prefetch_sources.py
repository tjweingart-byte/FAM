"""Where prefetch gets its guesses, against the real bank and the real stores.

`prefetch.py` is the machinery and knows nothing about topics or mixes; this is
the other side of that seam. The tests that matter are about the cost design
rather than about ranking - CLAUDE.md's rule is **one bank for everyone,
personalisation in the ordering, not the inventory**, and it is what makes some
guesses structurally cheaper to warm than others:

* trending is identical for every listener, so one warmed script is taken by
  everybody who taps that tile;
* a mix member is the strongest prediction in the app and a bank one is shared,
  where a typed one is a script a day for exactly one person;
* a feed rail is the most personal and the least shareable.

The other thing pinned here: **every candidate carries a reason**. It is the
contextual-relevance claim being made out loud, it survives to the report, and
without it nobody can tell which kinds of guess are paying for themselves.
"""
from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mixes as mixes_mod  # noqa: E402
import prefetch  # noqa: E402
import prefetch_sources  # noqa: E402
import topics as topics_mod  # noqa: E402


@pytest.fixture
def store(tmp_path):
    """An event log with some listening in it.

    `rank_most_played` stopped topping itself up from the evergreen bank, so
    "what everyone is playing" is now empty until somebody plays something -
    which is the right answer for that rail and means this source genuinely
    has nothing to warm on a silent deployment. Every test here that is about
    the *shape* of a trending guess therefore needs a crowd, and having the
    fixture supply one keeps that a property of the source rather than a
    detail each test repeats.
    """
    store = topics_mod.EventStore(str(tmp_path / "events.db"))
    for rank, topic in enumerate(topics_mod.TOPIC_BANK[:8]):
        for n in range(8 - rank):
            store.record(topics_mod.Event(f"listener-{n}", "play", topic.id,
                                          "", topic.tags, time.time()))
    return store


@pytest.fixture
def mix_store(tmp_path):
    return mixes_mod.MixStore(str(tmp_path / "mixes.db"))


@pytest.fixture(autouse=True)
def clean_registry():
    prefetch.reset_sources()
    yield
    prefetch.reset_sources()


# --------------------------------------------------------------------------
# trending: the cheapest thing in the app to warm
# --------------------------------------------------------------------------
def test_trending_needs_no_listener_at_all(store):
    """It is identical for everyone by construction, so one warmed script is
    taken by everybody who taps that tile. The only source worth running on an
    empty server with nobody signed in."""
    candidates = prefetch_sources.TrendingSource(store).candidates(listener="")
    assert candidates, "trending produced nothing without a listener"
    assert all(c.listener == "" for c in candidates), (
        "a shared guess was tagged to one listener, which would report a "
        "shared win as a personal one")


def test_trending_candidates_are_the_bank_queries_a_tap_would_send(store):
    """The tile's `query` is what gets generated - `Topic.title` is only a
    label. Warming the label would write an episode nobody ever asks for."""
    by_query = {t.query for t in topics_mod.TOPIC_BANK}
    for candidate in prefetch_sources.TrendingSource(store).candidates():
        assert candidate.query in by_query
        assert candidate.topic_id


def test_trending_says_where_in_the_ranking_each_guess_came_from(store):
    first = prefetch_sources.TrendingSource(store).candidates()[0]
    assert "#1 in what FAM can't stop listening to" in first.reason
    assert "everyone" in first.reason


# --------------------------------------------------------------------------
# mixes: the strongest prediction in the app
# --------------------------------------------------------------------------
def test_a_mix_member_is_a_candidate(store, mix_store):
    """A mix holds topic ids rather than audio precisely so it is fresh every
    day - so every member is an episode that will be generated tomorrow and
    does not exist yet."""
    topic = topics_mod.TOPIC_BANK[0]
    mix_store.create("listener-1", "At the gym", [topic.id])

    candidates = prefetch_sources.MixSource(mix_store).candidates("listener-1")
    assert [c.query for c in candidates] == [topic.query]
    assert "At the gym" in candidates[0].reason


def test_a_bank_member_outranks_a_typed_one(store, mix_store):
    """`mixes.MixItem` already says why: a bank topic is shared with everyone
    who has it, a typed one is a script a day for one listener."""
    topic = topics_mod.TOPIC_BANK[0]
    typed = mixes_mod.custom_item("something only they ever ask")
    mix_store.create("listener-1", "Morning", [topic.id])
    mix = mix_store.list_for_user("listener-1")[0]
    mix_store.update("listener-1", mix.id,
                     topic_ids=[topic.id, typed.as_dict()])

    candidates = prefetch_sources.MixSource(mix_store).candidates("listener-1")
    assert candidates[0].query == topic.query
    assert candidates[0].weight > candidates[-1].weight
    assert "shared with nobody" in candidates[-1].reason, (
        "a guess that only one person can ever take must say so, or the cost "
        "design is invisible in the report")


def test_mixes_produce_nothing_without_a_listener(mix_store):
    assert prefetch_sources.MixSource(mix_store).candidates("") == []


# --------------------------------------------------------------------------
# the feed: most personal, least shareable
# --------------------------------------------------------------------------
def test_the_feed_source_warms_the_rails_the_page_actually_draws(store):
    """The same ranking the page draws itself from, so a warmed tile and a
    shown tile cannot disagree. A second implementation of "what next" is what
    CLAUDE.md already refused once for `rank_next_up`.

    Seeded, because this source is the personal one: the rails it warms are
    "Made for you" and "What your friends are listening to", and both are
    honestly empty
    for a listener the app has never seen. That case is the next test.
    """
    topic = topics_mod.TOPIC_BANK[0]
    for _ in range(3):
        store.record(topics_mod.Event(
            user_id="listener-1", kind="complete", topic_id=topic.id,
            text=topic.title, tags=topic.tags))

    candidates = prefetch_sources.FeedSource(store).candidates("listener-1")
    feed = topics_mod.build_feed(store, "listener-1")
    shown = {t["query"] for s in feed["sections"] for t in s["topics"]}
    assert candidates, "the feed source produced nothing"
    assert all(c.query in shown for c in candidates)
    assert all(c.listener == "listener-1" for c in candidates)


def test_the_feed_source_warms_the_startup_set_for_a_listener_it_knows_nothing_about(store):
    """Reversed, and by the source ordering's own cost argument.

    This used to warm nothing, on the reasoning that guessing for somebody
    with no history is paying for a random tile. That reasoning was right
    while their "Made for you" rail was ranked from an empty taste. It is
    the startup set now (`startup.py`), and the startup set is the *most*
    shareable inventory in the app: eight tiles, identical for every
    cold-start listener in the deployment, so one warmed brief serves all of
    them - which is exactly what makes `TrendingSource` worth asking first.

    It is also the highest-value brief there is. A cold start's first rail is
    the first thing anybody ever taps, and `startup.py`'s questions are
    resolved at the tap, so a warmed brief is the whole of what §105 says
    warming buys.
    """
    candidates = prefetch_sources.FeedSource(store).candidates("brand-new")
    assert candidates, "the first rail a new listener sees is worth warming"
    ids = {c.topic_id for c in candidates}
    assert ids <= set(topics_mod.STARTUP_BY_ID), ids
    # Shared, which is the whole reason it is allowed: nothing here is keyed
    # on who asked, so the second cold-start listener's warm is a skip.
    assert all(c.query for c in candidates)
    other = prefetch_sources.FeedSource(store).candidates("someone-else")
    assert [c.query for c in other] == [c.query for c in candidates]


def test_the_feed_source_warms_no_rail_the_page_does_not_draw(store):
    """Warming a rail nobody is shown spends money on a tile that cannot be
    tapped. Explore New came off the page, and this is what notices if
    anything else does."""
    shown = {k for k, _ in topics_mod.SECTIONS}
    unshown = set(prefetch_sources.FeedSource(store).sections) - shown
    assert not unshown, f"warming rails that are not on myFAM: {unshown}"


def test_the_feed_source_does_not_warm_trending_a_second_time(store):
    """`TrendingSource` already has it, and warming it twice would double-count
    its hits - the number the whole thing is judged on."""
    assert "trending" not in prefetch_sources.FeedSource(store).sections


def test_the_feed_produces_nothing_without_a_listener(store):
    assert prefetch_sources.FeedSource(store).candidates("") == []


# --------------------------------------------------------------------------
# threads: the follow-up already predicted
# --------------------------------------------------------------------------
def test_open_threads_become_candidates(store):
    """`<<NEXT:>>` is already written and already offered as a chip. Warming it
    is the difference between the chip being instant and the chip being an
    ordinary episode."""
    topic = topics_mod.TOPIC_BANK[0]
    store.record(topics_mod.Event(
        user_id="listener-1", kind="complete", topic_id=topic.id,
        text=topic.title, thread="whether the appeal actually gets heard"))

    candidates = prefetch_sources.ThreadSource(store).candidates("listener-1")
    assert [c.query for c in candidates] == ["whether the appeal actually gets heard"]
    assert "predicted" in candidates[0].reason


def test_a_thread_source_that_cannot_read_returns_nothing_rather_than_raising(store):
    """A source that throws would take the whole cycle down with it, and a
    speculative cycle failing must never be able to affect anything else."""
    class Broken:
        def open_threads(self, *_args, **_kw):
            raise RuntimeError("the store fell over")

    assert prefetch_sources.ThreadSource(Broken()).candidates("listener-1") == []


# --------------------------------------------------------------------------
# every guess explains itself
# --------------------------------------------------------------------------
def test_every_source_gives_a_reason_and_a_length(store, mix_store):
    """The reason is the contextual-relevance claim being made out loud. It
    survives to the report, and without it nobody can tell which kinds of guess
    are paying for themselves."""
    topic = topics_mod.TOPIC_BANK[0]
    mix_store.create("listener-1", "Morning", [topic.id])
    store.record(topics_mod.Event(
        user_id="listener-1", kind="complete", topic_id=topic.id,
        text=topic.title, thread="what happens to the rate now"))

    for source in (prefetch_sources.TrendingSource(store),
                   prefetch_sources.MixSource(mix_store),
                   prefetch_sources.FeedSource(store),
                   prefetch_sources.ThreadSource(store)):
        for candidate in source.candidates("listener-1"):
            assert candidate.reason.strip(), f"{source.name} gave no reason"
            assert candidate.source == source.name
            assert candidate.minutes == prefetch_sources.DEFAULT_MINUTES, (
                "duration is part of the cache key, so a warm at the wrong "
                "length is a warm nobody ever finds")


# --------------------------------------------------------------------------
# installing
# --------------------------------------------------------------------------
def test_install_registers_every_source_it_can_answer(store, mix_store):
    installed = prefetch_sources.install(event_store=store, mix_store=mix_store)
    assert set(installed) == {"trending", "stories", "mixes", "feed", "threads"}
    assert prefetch_sources.report()["missing"] == []


def test_a_missing_store_is_named_rather_than_silently_skipped(store):
    """A prefetcher running on four surfaces out of five looks identical from
    outside to one running on all of them."""
    prefetch_sources.install(event_store=store, mix_store=None)
    assert prefetch_sources.report()["missing"] == ["mixes"]


def test_installing_nothing_is_reported_rather_than_looking_healthy():
    """With no stores at all, only the story pool - which needs none - is
    left, and everything that is missing has to be named."""
    prefetch_sources.install(event_store=None, mix_store=None)
    report = prefetch_sources.report()
    assert report["installed"] == ["stories"]
    assert set(report["missing"]) == set(report["available"]) - {"stories"}


def test_no_source_calls_a_model_or_the_network():
    """A source that costs money to *ask* turns a speculative saving into a
    certain spend. Checked by reading the module rather than by trusting it:
    the whole budget design assumes asking is free."""
    import pathlib

    text = pathlib.Path(prefetch_sources.__file__).read_text()
    for banned in ("import research", "build_async_client", "messages.create",
                   "httpx", "requests.", "understand("):
        assert banned not in text, (
            f"a candidate source reaches for {banned!r}; asking what somebody "
            "might want must not itself cost anything")


# --------------------------------------------------------------------------
# the live story pool: shared, and the one with most to gain
# --------------------------------------------------------------------------
# It had no source at all until PROBLEMS.md §105, which made it the one myFAM
# inventory where every tap paid for its own brief - and it is the inventory
# where a brief is worth most, because a story tile is a title and an angle and
# the subject still has to be resolved on the tap.
@pytest.fixture
def pool():
    import stories as stories_mod

    stories_mod.reset()
    yield stories_mod
    stories_mod.reset()


def story(subject="the rate decision", domain="markets", **kw):
    import stories as stories_mod

    return stories_mod.Story(
        subject=subject,
        title=kw.pop("title", "The rate decision"),
        angle=kw.pop("angle", "what moved"),
        query=kw.pop("query", f"what is driving {subject}"),
        domain=domain,
        first_seen=kw.pop("first_seen", __import__("time").time() - 3600),
        strength=kw.pop("strength", 0.9),
        **kw)


def test_the_live_story_pool_is_a_source(pool):
    pool.seed([story()])
    candidates = prefetch_sources.StoriesSource().candidates()
    assert [c.query for c in candidates] == ["what is driving the rate decision"]


def test_a_story_guess_is_shared_like_trending(pool):
    """The pool is fetched and composed once for everybody, so one warmed brief
    is used by every listener who taps that tile. Marking it with a listener
    would make the ledger report a shared win as a personal one."""
    pool.seed([story()])
    candidates = prefetch_sources.StoriesSource().candidates(listener="anyone")
    assert candidates and all(c.listener == "" for c in candidates)


def test_a_story_guess_says_which_story_and_how_old(pool):
    """The reason is the contextual-relevance claim: which story, how hot, how
    old - the three things that say whether warming the pool is paying."""
    pool.seed([story()])
    reason = prefetch_sources.StoriesSource().candidates()[0].reason
    assert "story pool" in reason and "markets" in reason and "h old" in reason


def test_an_expired_story_is_never_warmed(pool):
    """`Pool.live` is what the rails draw, so warming anything else would be
    paying for a tile nobody can tap."""
    import time as _time

    pool.seed([story(first_seen=_time.time() - 48 * 3600, shelf_life=3600.0)])
    assert prefetch_sources.StoriesSource().candidates() == []


def test_an_empty_pool_produces_nothing_rather_than_raising(pool):
    """A deployment with no live source configured has no stories, and that is
    an ordinary state rather than a broken one."""
    assert prefetch_sources.StoriesSource().candidates() == []


def test_the_story_source_needs_no_store_to_install(store, mix_store):
    """It reads a module-level pool, so it installs on every deployment - and
    the report has to know it exists or a missing one looks like a healthy one."""
    installed = prefetch_sources.install(event_store=store, mix_store=mix_store)
    assert "stories" in installed
    assert prefetch_sources.report()["missing"] == []
