"""§267: the story pool on the disk, and what a tap carries from it.

Three things are pinned here:

* the pool and its clocks survive a restart (`stories.save` / `restore`,
  `story_store`), because an eviction that forgets is a creation;
* `stories.context_for` is the one place a tap's story context is built, and
  its measurement is dated, bounded and carries no hostname;
* **nothing about the story reaches EI**, so a brief prefetch warmed before
  one sweep is still the brief a tap takes after the next.
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
import story_store  # noqa: E402


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    original = list(stories._SOURCES)
    stories.reset()
    stories._SOURCES[:] = []
    monkeypatch.setattr(stories, "settings", dataclasses.replace(
        config.settings, stories=True, stories_compose=False))
    yield
    stories.reset()
    stories._SOURCES[:] = original


class Source(stories.StorySource):
    def __init__(self, rows):
        self.rows = rows
        self.name = "test feed"
        self.domain = stories.ATTENTION

    def diagnose(self):
        return True, "ready"

    async def collect(self, limit):
        return list(self.rows)[:limit]


def signal(subject="undersea cables", **kw):
    kw.setdefault("observation", "12 different outlets have run this in the "
                  "last 12 hours. What they are saying: cable cut off Taiwan")
    kw.setdefault("source", "test feed")
    kw.setdefault("tags", ("tech",))
    kw.setdefault("urls", ("https://www.reuters.com/a", "https://apnews.com/b",
                           "https://www.reuters.com/c"))
    kw.setdefault("measured", True)       # as GDELT's sweep files it
    kw.setdefault("domain", stories.ATTENTION)
    return stories.Signal(subject=subject, **kw)


def sweep(rows, now):
    stories._SOURCES[:] = [Source(rows)]
    return asyncio.run(stories.refresh(now=now))


# --------------------------------------------------------------------------
# kept on the disk
# --------------------------------------------------------------------------
def test_a_restart_keeps_every_story_and_its_clock():
    t0 = 1_800_000_000.0
    sweep([signal()], t0)
    held = stories.pool().stories[0]
    assert story_store.exists(), "a refresh writes the store"

    stories.reset()                      # the process ends
    assert stories.restore(now=t0 + 600) == 1
    back = stories.pool().stories[0]
    assert back == held, "title, clock, measurement and urls all come back"
    assert stories._FIRST_SEEN[held.id] == held.first_seen
    assert isinstance(back.urls, tuple) and isinstance(back.tags, tuple)


def test_a_restored_story_is_not_new_again():
    """The point of keeping the ledger: after a restart the next sweep finds
    the story already held, so it keeps its first sighting."""
    t0 = 1_800_000_000.0
    sweep([signal()], t0)
    first = stories.pool().stories[0].first_seen
    stories.reset()
    stories.restore(now=t0 + 3600)
    sweep([signal()], t0 + 3600)
    assert stories.pool().stories[0].first_seen == first


def test_what_expired_while_down_is_not_restored():
    t0 = 1_800_000_000.0
    sweep([signal()], t0)
    life = stories.pool().stories[0].shelf_life
    stories.reset()
    assert stories.restore(now=t0 + life + 1) == 0
    assert stories.pool().stories == []


def test_restore_never_overwrites_a_newer_pool():
    t0 = 1_800_000_000.0
    sweep([signal()], t0)
    stories.reset()
    sweep([signal("something newer")], t0 + 60)
    assert stories.restore(now=t0 + 60) == 0
    assert [s.subject for s in stories.pool().stories] == ["something newer"]


def test_a_wipe_empties_the_saved_copy_too():
    t0 = 1_800_000_000.0
    sweep([signal()], t0)
    stories.pool().clear()
    stories.forget_saved()
    stories.reset()
    assert stories.restore(now=t0 + 60) == 0


def test_an_unreadable_store_is_a_boot_that_sweeps_as_before(monkeypatch):
    def broken():
        raise OSError("disk gone")

    monkeypatch.setattr(story_store, "store", broken)
    assert stories.save() is False
    t0 = 1_800_000_000.0
    sweep([signal()], t0)                # refresh survives a failed save
    assert len(stories.pool().stories) == 1


# --------------------------------------------------------------------------
# what a tap carries
# --------------------------------------------------------------------------
def test_context_is_found_by_the_question_a_tile_sends():
    t0 = time.time()
    sweep([signal()], t0)
    story = stories.pool().stories[0]
    ctx = stories.context_for(f"  {story.query.upper()} ")
    assert ctx is not None and ctx.subject == story.subject
    assert ctx.hosts == ("reuters.com", "apnews.com"), "hosts, deduplicated"
    assert stories.context_for("an unrelated question") is None


def test_the_measurement_is_dated_and_carries_no_hostname():
    t0 = time.time()
    sweep([signal()], t0)
    ctx = stories.context_for(stories.pool().stories[0].query)
    block = ctx.as_evidence(now=t0 + 120)
    assert block.startswith("WHAT FAM MEASURED")
    assert "Measured:" in block and "12 different outlets" in block
    assert "reuters" not in block.lower() and "apnews" not in block.lower()
    assert "never says why" in block and "settles" in block


def test_a_stale_measurement_is_left_out():
    """`two-crowd-rows`: a stale blurb must not become a stale episode."""
    t0 = time.time()
    sweep([signal()], t0)
    ctx = stories.context_for(stories.pool().stories[0].query)
    assert ctx.as_evidence(now=t0 + stories.STORY_MEASUREMENT_MAX_AGE + 1) == ""


def test_each_sweep_refreshes_the_measurement():
    t0 = time.time() - 1800
    sweep([signal()], t0)
    sweep([signal(observation="40 different outlets have run this")], t0 + 900)
    story = stories.pool().stories[0]
    assert story.observation.startswith("40 different outlets")
    assert story.observed_at == t0 + 900
    sweep([signal(observation="")], t0 + 1800)
    assert stories.pool().stories[0].observation.startswith("40"), (
        "a sweep that said nothing keeps the last measurement and its time")


# --------------------------------------------------------------------------
# the trap: nothing about the story may reach EI
# --------------------------------------------------------------------------
def test_ei_never_reads_the_story_context():
    """Guardrail 1. If EI read the measurement, a brief warmed before a sweep
    would miss after it, and every miss pays the whole EI call after the tap."""
    import inspect

    import episode_intelligence
    import prefetch
    import script_generator

    assert "context_for" not in inspect.getsource(episode_intelligence)
    assert "context_for" not in inspect.getsource(prefetch)
    understand = inspect.getsource(script_generator.ScriptGenerator.understand)
    assert "context_for" not in understand and "StoryContext" not in understand


def test_a_brief_warmed_before_a_sweep_is_taken_after_it(monkeypatch):
    """Guardrail 4, end to end on the brief store: warm a story's brief, let
    the next sweep change its measurement, then ask as the tap does."""
    import prefetch

    t0 = time.time() - 1800
    sweep([signal()], t0)
    story = stories.pool().stories[0]

    monkeypatch.setattr(prefetch, "settings", dataclasses.replace(
        prefetch.settings, prefetch=True))
    fetcher = prefetch.Prefetcher.__new__(prefetch.Prefetcher)
    fetcher.briefs = prefetch.BriefStore()
    fetcher.ledger = prefetch.Ledger()
    monkeypatch.setattr(prefetch, "_PREFETCHER", fetcher)
    import types

    brief = types.SimpleNamespace(degraded=False)
    fetcher.briefs.put(story.query, 2, brief, source="stories")

    sweep([signal(observation="40 different outlets have run this")], t0 + 900)
    assert stories.pool().stories[0].query == story.query
    assert prefetch.warm_brief(story.query, 2) is brief, (
        "the next sweep changed the measurement and the brief still lands")


# --------------------------------------------------------------------------
# the story's outlets, beside the main search
# --------------------------------------------------------------------------
def _result(url, title="t"):
    import research

    return research.SearchResult(url=url, title=title,
                                 published_date="2026-10-14",
                                 highlights=[f"passage {url.rsplit('/', 1)[-1]}"])


@pytest.fixture
def parallel(monkeypatch):
    """A fake Parallel: the main search and the outlets' search are told
    apart by `include_domains`, and each can be slowed or broken."""
    import research

    calls = {"main": 0, "outlets": 0, "outlet_delay": 0.0,
             "outlet_error": None}

    def search(query, num_results, recency_days=0, include_domains=(),
               objective="", extra_queries=()):
        if include_domains:
            calls["outlets"] += 1
            calls["hosts"] = tuple(include_domains)
            if calls["outlet_delay"]:
                time.sleep(calls["outlet_delay"])
            if calls["outlet_error"]:
                raise calls["outlet_error"]
            return [_result("https://reuters.com/main-1"),
                    _result("https://apnews.com/story-2", "from the outlets")], 0.001
        calls["main"] += 1
        # A real search takes seconds; the outlets' one, started first, is
        # in by the time this is.
        time.sleep(0.3)
        return [_result("https://reuters.com/main-1"),
                _result("https://bbc.co.uk/main-2")], 0.001

    monkeypatch.setattr(research, "_search_parallel", search)
    monkeypatch.setattr(research, "settings", dataclasses.replace(
        research.settings, research_backend="parallel", gdelt_cross_check=False,
        research_retry=False))
    return calls


def test_the_outlets_are_searched_beside_and_merged_without_duplicates(parallel):
    import research

    packet = asyncio.run(research.retrieve(
        "cable cut off taiwan", story_hosts=("reuters.com", "apnews.com")))
    assert parallel["main"] == 1 and parallel["outlets"] == 1
    assert parallel["hosts"] == ("reuters.com", "apnews.com")
    assert "from the outlets" in packet.context
    assert packet.context.count("passage main-1") == 1, (
        "what the main packet already held is not counted twice")
    assert packet.searches == 2
    assert "apnews.com" in packet.sources
    assert "apnews.com" not in packet.context, "never a hostname in the packet"


def test_a_slow_outlet_search_is_never_waited_for(parallel):
    """Same deadline: whatever has not answered when the main packet is done
    is dropped, so the first word is never later than before."""
    import research

    parallel["outlet_delay"] = 1.5

    async def timed():
        started = time.perf_counter()
        packet = await research.retrieve(
            "cable cut off taiwan", story_hosts=("apnews.com",))
        return packet, time.perf_counter() - started

    # Timed inside the loop: `asyncio.run` joins the abandoned thread on its
    # way out, which a server's long-lived loop never does.
    packet, seconds = asyncio.run(timed())
    assert seconds < 1.0
    assert "from the outlets" not in packet.context
    assert packet.searches == 2, "the late search was still sent, so it is metered"
    assert packet.cost == pytest.approx(0.002)


def test_a_broken_outlet_search_costs_the_episode_nothing(parallel):
    import research

    parallel["outlet_error"] = RuntimeError("upstream 500")
    packet = asyncio.run(research.retrieve(
        "cable cut off taiwan", story_hosts=("apnews.com",)))
    assert packet and "from the outlets" not in packet.context


def test_no_outlet_search_without_a_story(parallel):
    import research

    asyncio.run(research.retrieve("cable cut off taiwan"))
    assert parallel["outlets"] == 0


def test_a_tap_carries_the_story_into_the_packet(parallel, monkeypatch):
    """End to end through `ScriptGenerator.research`: the tile's question
    finds its story, its outlets are searched, its measurement is appended."""
    import script_generator as sg

    t0 = time.time()
    sweep([signal()], t0)
    story = stories.pool().stories[0]
    monkeypatch.setattr(sg, "settings", dataclasses.replace(
        sg.settings, research_backend="parallel"))
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = sg.plan_episode(story.query, 2)
    plan = dataclasses.replace(plan, search=True)
    out = asyncio.run(generator.research(plan))
    assert parallel["outlets"] == 1
    assert "from the outlets" in out.evidence
    assert "WHAT FAM MEASURED" in out.evidence
    assert out.evidence.index("SOURCE 1") < out.evidence.index("WHAT FAM MEASURED")

    plain = asyncio.run(generator.research(dataclasses.replace(
        sg.plan_episode("how do undersea cables work", 2), search=True)))
    assert "WHAT FAM MEASURED" not in plain.evidence


# --------------------------------------------------------------------------
# the review (§267): only a measurement is evidence
# --------------------------------------------------------------------------
@pytest.mark.parametrize("domain, observation", [
    # a trending item's own reason (`two-crowd-rows`: why_now is never evidence)
    (stories.ATTENTION, "a new class cleared a late-stage trial"),
    # a betting price (`forecast-beside`: never evidence)
    (stories.PREDICTION, "the contract is trading around 62 percent"),
    # a score (`live-facts` would withhold it within two minutes)
    (stories.SPORTS, "is under way right now: 21-14 (as of 19:02 UTC)"),
])
def test_only_gdelts_measurement_ever_becomes_evidence(domain, observation):
    t0 = time.time()
    sweep([signal(domain=domain, observation=observation, measured=False)], t0)
    ctx = stories.context_for(stories.pool().stories[0].query)
    assert ctx is not None, "the outlets are still searched"
    assert ctx.as_evidence(now=t0 + 60) == ""


def test_gdelts_sweep_is_the_one_source_that_measures():
    import inspect

    import story_sources

    assert "measured=True" in inspect.getsource(story_sources.GdeltSignals)
    for name in ("TrendingRegistrySignals", "FinnhubSignals",
                 "PolymarketSignals"):
        assert "measured=True" not in inspect.getsource(
            getattr(story_sources, name)), name
    import balldontlie_scores

    assert "measured=True" not in inspect.getsource(balldontlie_scores.make_signals)


def test_a_measurement_survives_a_restart_as_a_measurement():
    t0 = time.time()
    sweep([signal()], t0)
    stories.reset()
    stories.restore(now=t0 + 60)
    assert stories.pool().stories[0].measured is True


def test_a_missing_key_is_not_logged_as_an_outage(monkeypatch):
    import provider_usage
    import research

    recorded = []
    monkeypatch.setattr(provider_usage, "record",
                        lambda backend, ok=True: recorded.append(ok))

    def no_key(*_a, **_k):
        raise research.ResearchUnavailable("PARALLEL_API_KEY is not set")

    monkeypatch.setattr(research, "_search_parallel", no_key)
    with pytest.raises(research.ResearchUnavailable):
        research._story_look_blocking("q", 5, ("apnews.com",), 0, "",
                                      "parallel", "", ())
    assert recorded == []
