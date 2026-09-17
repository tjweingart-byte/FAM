"""myFAM's story pool: what it promises, and what it must never do.

The pool is what turns live data into things worth offering on the browse
page. Four properties are load-bearing and each one is a rule somebody could
undo without anything looking broken:

1. **It never asserts a fact.** A tile is written before anything is
   researched, so a result on one is a claim nobody checked - PROBLEMS.md §88,
   moved onto the surface more people see.
2. **It degrades rather than disappearing.** No key, a timeout, a refusal: the
   row still fills, from templates, and says it did.
3. **It costs one call per window for everybody.** Per-listener anything here
   would make the cheapest surface in FAM the dearest.
4. **It stops pushing.** A story fades, expires and then cools off, so a busy
   week cannot become one tile shown forever.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import stories  # noqa: E402
import topics as T  # noqa: E402


@pytest.fixture(autouse=True)
def clean():
    """The pool and the registry are global - that is the design - so a leak
    between tests would make one test's sweep satisfy another's assertion."""
    original = list(stories._SOURCES)
    stories.reset()
    stories._SOURCES[:] = []
    yield
    stories.reset()
    stories._SOURCES[:] = original


def signal(subject="undersea cables", domain=stories.ATTENTION, **kw):
    kw.setdefault("observation", "coverage is running about three times normal")
    kw.setdefault("source", "test feed")
    kw.setdefault("tags", ("tech",))
    return stories.Signal(subject=subject, domain=domain, **kw)


class Source(stories.StorySource):
    """A source that returns exactly what it was handed."""

    def __init__(self, rows, name="test feed", domain=stories.ATTENTION,
                 ready=True, blow_up=False, hang=False):
        self.rows = rows
        self.name = name
        self.domain = domain
        self._ready = ready
        self._blow_up = blow_up
        self._hang = hang
        self.calls = 0

    def diagnose(self):
        return (True, "ready") if self._ready else (False, "NOT_CONFIGURED_HERE=0")

    async def collect(self, limit):
        self.calls += 1
        if self._blow_up:
            raise RuntimeError("upstream 500")
        if self._hang:
            await asyncio.sleep(5)
        return list(self.rows)[:limit]


def no_composer(monkeypatch):
    """Run with the model switched off - the templated path."""
    monkeypatch.setattr(stories, "settings", dataclasses.replace(
        config.settings, stories_compose=False))


# --------------------------------------------------------------------------
# nothing here may assert a fact
# --------------------------------------------------------------------------
def test_no_template_states_an_outcome():
    """The degraded path is what a deployment with no key serves *always*, so
    it is the one that has to be safe without anybody checking it."""
    for domain in stories.DOMAINS:
        story = stories.template(signal("Chiefs vs Broncos", domain))
        text = f"{story.title} {story.angle} {story.query}".lower()
        assert stories._safe(text), f"{domain} template asserts an outcome: {text}"
        assert "won" not in text and "beat" not in text


def test_a_composed_tile_that_states_a_result_falls_back_to_its_template():
    """The prompt is what is supposed to hold. This is the check that says it
    did not - the same shape as `OpeningGuard`, and for the same reason: a
    rule that was already written down is a rule that has been broken before.
    """
    assert not stories._safe("Chiefs beat Broncos in overtime")
    assert not stories._safe("Nvidia soared to an all-time high")
    assert stories._safe("What the Chiefs' offensive line changed this week")


def test_the_composer_is_told_it_has_researched_nothing():
    system = stories.COMPOSER_SYSTEM.lower()
    assert "researched nothing" in system
    assert "never state or imply an outcome" in system
    assert "never a headline" in system


def test_an_unfinished_thing_is_named_as_unfinished_in_the_prompt():
    prompt = stories.build_composer_prompt(
        [signal("Chiefs vs Broncos", stories.SPORTS, outcome_pending=True)],
        "Tuesday")
    assert "has not finished" in prompt
    assert "including you" in prompt


def test_every_signal_is_numbered_so_the_reply_can_be_matched_back():
    """A model asked to answer "in the same order" eventually does not, and an
    off-by-one would put a golf angle on a bond story with nothing failing."""
    prompt = stories.build_composer_prompt(
        [signal("a"), signal("b"), signal("c")], "Tuesday")
    assert "[1] subject: a" in prompt
    assert "[3] subject: c" in prompt


# --------------------------------------------------------------------------
# how hard to push, and for how long
# --------------------------------------------------------------------------
def test_a_story_is_loudest_when_it_is_new_and_silent_once_it_expires():
    now = time.time()
    story = stories.template(signal(), now=now)
    fresh = story.push(now)
    half = story.push(now + story.shelf_life / 2)
    assert fresh > half > 0
    assert story.push(now + story.shelf_life) == 0.0
    assert story.expired(now + story.shelf_life)


def test_the_domains_are_pushed_at_the_weights_that_are_written_down():
    """A judgement rather than a measurement, which is exactly why it should
    be readable in one place and asserted rather than emergent."""
    now = time.time()
    pushes = {}
    for domain in stories.DOMAINS:
        story = stories.template(signal("x", domain), now=now)
        pushes[domain] = story.push(now)
    assert pushes[stories.ATTENTION] > pushes[stories.MARKETS]
    assert pushes[stories.MARKETS] > pushes[stories.PREDICTION]


def test_a_story_that_keeps_being_reported_does_not_get_to_be_new_again(monkeypatch):
    """`first_seen` is the clock, not `last_seen`. Otherwise a subject the
    world talks about all week is the top tile all week."""
    no_composer(monkeypatch)
    source = Source([signal()])
    stories.register(source)
    then = time.time() - 6 * 3600
    asyncio.run(stories.refresh(now=then))
    first = stories.pool().stories[0]

    asyncio.run(stories.refresh(now=time.time()))
    again = stories.pool().stories[0]
    assert again.id == first.id
    assert again.first_seen == pytest.approx(first.first_seen)
    assert again.last_seen > first.last_seen


def test_an_expired_subject_is_not_offered_again_until_it_has_cooled_off(monkeypatch):
    no_composer(monkeypatch)
    stories.register(Source([signal()]))
    start = time.time()
    asyncio.run(stories.refresh(now=start))
    assert stories.pool().stories

    # Past its shelf life: gone, and still gone on the next sweep even though
    # the source is still shouting about it.
    late = start + stories.DOMAIN_SHELF_LIFE[stories.ATTENTION] + 60
    asyncio.run(stories.refresh(now=late))
    assert stories.pool().live(late) == []

    # And back once the cooldown has passed.
    later = late + stories.SUBJECT_COOLDOWN + 60
    asyncio.run(stories.refresh(now=later))
    assert [s.subject for s in stories.pool().live(later)] == ["undersea cables"]


def test_a_story_survives_a_sweep_that_forgot_to_mention_it(monkeypatch):
    """Providers are noisy. A theme drops out of a top-fifteen for one window
    and returns, and a tile that vanished between two page loads reads as a
    bug rather than as an editorial decision."""
    no_composer(monkeypatch)
    source = Source([signal()])
    stories.register(source)
    asyncio.run(stories.refresh())
    source.rows = []
    asyncio.run(stories.refresh())
    assert [s.subject for s in stories.pool().live()] == ["undersea cables"]


# --------------------------------------------------------------------------
# variety
# --------------------------------------------------------------------------
def test_one_facet_cannot_take_over_the_pool(monkeypatch):
    no_composer(monkeypatch)
    stories.register(Source([
        signal(f"game {n}", stories.SPORTS, tags=("sports",))
        for n in range(stories.MAX_PER_FACET + 4)
    ]))
    asyncio.run(stories.refresh())
    assert len(stories.pool().stories) == stories.MAX_PER_FACET


# --------------------------------------------------------------------------
# it costs one call per window, for everybody
# --------------------------------------------------------------------------
def test_only_the_new_stories_are_composed(monkeypatch):
    """The steady-state cost. Everything already in the pool keeps the title
    and angle it was written with, so a window composes what arrived rather
    than everything that is showing."""
    composed = {"batches": [], "n": 0}

    async def fake_compose(signals, now=None):
        composed["n"] += 1
        composed["batches"].append([s.subject for s in signals])
        return [stories.template(s, now or time.time()) for s in signals]

    monkeypatch.setattr(stories, "compose", fake_compose)
    source = Source([signal("one")])
    stories.register(source)
    asyncio.run(stories.refresh())
    source.rows = [signal("one"), signal("two", tags=("money",))]
    asyncio.run(stories.refresh())

    assert composed["batches"] == [["one"], ["two"]]
    assert composed["n"] == 2, "one call per window, never one per story"


def test_the_pool_is_read_without_a_network_or_an_await():
    """`stories.pool()` is what `topics.build_feed` calls, and the browse
    page's whole latency guarantee is that this cannot wait on anything."""
    import inspect

    assert not inspect.iscoroutinefunction(stories.pool)
    assert not inspect.iscoroutinefunction(T.build_feed)
    source = inspect.getsource(stories.pool)
    assert "await" not in source and "refresh" not in source


def test_several_listeners_at_once_produce_one_sweep(monkeypatch):
    """Re-entrancy is the economics. Three page loads must not be three sweeps
    and three model calls."""
    no_composer(monkeypatch)
    source = Source([signal()])
    stories.register(source)

    async def three_at_once():
        await asyncio.gather(stories.refresh(), stories.refresh(),
                             stories.refresh())

    asyncio.run(three_at_once())
    assert source.calls == 1


def test_a_source_with_a_daily_quota_is_not_swept_every_window(monkeypatch):
    """API-Sports allows a hundred requests a day. At fifteen-minute windows a
    source with no floor would spend its quota on a page nobody opened."""
    no_composer(monkeypatch)
    source = Source([signal("a game", stories.SPORTS, tags=("sports",))],
                    name="slow source")
    source.min_interval_seconds = 7200.0
    stories.register(source)
    asyncio.run(stories.refresh())
    asyncio.run(stories.refresh())
    assert source.calls == 1
    outcomes = {r.name: r.outcome for r in stories.pool().sources}
    assert outcomes["slow source"] == stories.SKIPPED


def test_a_skipped_sweep_is_not_reported_as_an_empty_one():
    """Three different sentences for three different things - the same rule
    `trending.py` keeps. "Had nothing" and "was not asked" have different
    fixes, and one of them is not a fix at all."""
    reports = [stories.SourceReport("a", stories.ATTENTION, o)
               for o in stories.OUTCOMES if o != stories.SIGNALS]
    assert len({r.outcome for r in reports}) == len(stories.OUTCOMES) - 1


# --------------------------------------------------------------------------
# it degrades, and says so
# --------------------------------------------------------------------------
def test_with_the_composer_off_every_story_is_templated_and_marked(monkeypatch):
    no_composer(monkeypatch)
    stories.register(Source([signal()]))
    pool = asyncio.run(stories.refresh())
    assert pool.stories
    assert all(s.degraded for s in pool.stories)
    assert pool.degraded == 1
    assert stories.report()["pool"]["degraded"] == 1


def test_a_composer_that_fails_costs_quality_and_never_availability(monkeypatch):
    """The EI rule, restated: a layer that adds quality must not be able to
    subtract availability."""
    def boom(*_a, **_k):
        raise RuntimeError("no key")

    monkeypatch.setattr(stories, "build_async_client", boom)
    stories.register(Source([signal()]))
    pool = asyncio.run(stories.refresh())
    assert [s.subject for s in pool.stories] == ["undersea cables"]
    assert pool.stories[0].degraded


def test_one_broken_source_does_not_empty_the_pool(monkeypatch):
    no_composer(monkeypatch)
    stories.register(Source([], name="broken", blow_up=True))
    stories.register(Source([signal()], name="working"))
    pool = asyncio.run(stories.refresh())
    assert [s.subject for s in pool.stories] == ["undersea cables"]
    outcomes = {r.name: r.outcome for r in pool.sources}
    assert outcomes["broken"] == stories.SOURCE_FAILED
    assert outcomes["working"] == stories.SIGNALS


def test_a_source_that_hangs_is_bounded(monkeypatch):
    monkeypatch.setattr(stories, "settings", dataclasses.replace(
        config.settings, stories_compose=False, stories_timeout_seconds=0.05))
    stories.register(Source([signal()], name="slow", hang=True))
    pool = asyncio.run(stories.refresh())
    assert {r.outcome for r in pool.sources} == {stories.TIMEOUT}


def test_the_switch_turns_the_whole_thing_off(monkeypatch):
    monkeypatch.setattr(stories, "settings", dataclasses.replace(
        config.settings, stories=False))
    stories.register(Source([signal()]))
    assert asyncio.run(stories.refresh()).stories == []


# --------------------------------------------------------------------------
# an empty pool is never a claim about the world
# --------------------------------------------------------------------------
@pytest.mark.parametrize("outcome", [
    stories.NOT_CONFIGURED, stories.SOURCE_FAILED, stories.TIMEOUT,
    stories.EMPTY, stories.SKIPPED,
])
def test_no_empty_state_says_the_world_is_quiet(outcome):
    pool = stories.Pool(sources=[stories.SourceReport("s", stories.ATTENTION,
                                                      outcome)])
    said = pool.empty_reason.lower()
    assert said
    assert "nothing is trending" not in said
    assert "nothing is happening" not in said
    assert "no news" not in said


def test_health_says_which_sources_are_missing_and_why():
    """An absent capability that says so can be fixed; one that is absent and
    quiet gets shipped."""
    stories.register(Source([], name="absent", ready=False))
    body = stories.report()
    assert body["ready"] == []
    assert "not a statement about the world" in body["detail"]
    assert body["sources"][0]["detail"] == "NOT_CONFIGURED_HERE=0"


def test_an_id_is_hashed_from_the_subject_and_not_the_wording():
    """The id is what impressions, fatigue and the cooldown key on. One that
    churned when a provider rephrased itself would show the same listener the
    same tile forever and never learn it."""
    one = stories.template(signal(observation="coverage is up"))
    two = stories.template(signal(observation="everyone is writing about this"))
    assert one.id == two.id
    assert one.id.startswith("st-")


def test_two_sources_naming_the_same_subject_produce_one_tile(monkeypatch):
    no_composer(monkeypatch)
    stories.register(Source([signal()], name="one"))
    stories.register(Source([signal()], name="two"))
    pool = asyncio.run(stories.refresh())
    assert len(pool.stories) == 1


def test_nothing_is_composed_that_the_variety_cap_would_throw_away(monkeypatch):
    """The cheapest saving here. Composing a tile the pool is about to drop is
    money spent on something nobody will ever read, and a cold pool with every
    source configured is exactly when it would happen most."""
    composed = {"n": 0, "subjects": []}

    async def fake_compose(signals, now=None):
        composed["n"] += 1
        composed["subjects"] = [s.subject for s in signals]
        return [stories.template(s, now or time.time()) for s in signals]

    monkeypatch.setattr(stories, "compose", fake_compose)
    stories.register(Source([
        signal(f"game {n}", stories.SPORTS, tags=("sports",), strength=1.0)
        for n in range(stories.MAX_PER_FACET + 5)
    ], name="one-note source"))
    asyncio.run(stories.refresh())
    assert len(composed["subjects"]) == stories.MAX_PER_FACET
    assert len(stories.pool().stories) == stories.MAX_PER_FACET


def test_the_pool_never_composes_more_than_it_can_hold(monkeypatch):
    seen = {"n": 0}

    async def fake_compose(signals, now=None):
        seen["n"] = len(signals)
        return [stories.template(s, now or time.time()) for s in signals]

    monkeypatch.setattr(stories, "compose", fake_compose)
    # Four sources at their per-source ceiling: thirty-two signals into a pool
    # that holds twenty-four. One source cannot do this on its own, which is
    # what `MAX_PER_SOURCE` is for - the ceiling that bites here is the pool's.
    facets = ("tech", "money", "world", "science", "health", "culture",
              "sports", "business")
    for index in range(4):
        stories.register(Source(
            [signal(f"subject {index}-{n}", tags=(facets[n % len(facets)],))
             for n in range(stories.MAX_PER_SOURCE)],
            name=f"chatty source {index}"))
    asyncio.run(stories.refresh())
    assert seen["n"] > 0
    assert seen["n"] <= stories.POOL_SIZE, (
        f"composed {seen['n']} tiles for a pool that holds "
        f"{stories.POOL_SIZE}")
