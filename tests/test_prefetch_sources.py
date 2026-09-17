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

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mixes as mixes_mod  # noqa: E402
import prefetch  # noqa: E402
import prefetch_sources  # noqa: E402
import topics as topics_mod  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return topics_mod.EventStore(str(tmp_path / "events.db"))


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
    assert "#1 in what FAM can't stop playing" in first.reason
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
    "Made for you" and "Your circle is on this", and both are honestly empty
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


def test_the_feed_source_warms_nothing_for_a_listener_it_knows_nothing_about(store):
    """Not a gap. This is the *personal* source, and guessing for somebody
    with no history is paying for a random tile - `TrendingSource` already
    covers the shared case, and it covers it for everybody at once."""
    assert prefetch_sources.FeedSource(store).candidates("brand-new") == []


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
    assert set(installed) == {"trending", "mixes", "feed", "threads"}
    assert prefetch_sources.report()["missing"] == []


def test_a_missing_store_is_named_rather_than_silently_skipped(store):
    """A prefetcher running on three surfaces out of four looks identical from
    outside to one running on all of them."""
    prefetch_sources.install(event_store=store, mix_store=None)
    assert prefetch_sources.report()["missing"] == ["mixes"]


def test_installing_nothing_is_reported_rather_than_looking_healthy():
    prefetch_sources.install(event_store=None, mix_store=None)
    report = prefetch_sources.report()
    assert report["installed"] == []
    assert set(report["missing"]) == set(report["available"])


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
