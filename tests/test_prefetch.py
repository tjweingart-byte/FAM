"""Writing the episode before anybody asks for it.

The framework, not the policy. It ships off (`PREFETCH=0`), so most of what is
below is about the four properties that have to hold before anybody dares turn
it on:

1. **A warmed script is found.** Prefetch and the tap compute the cache key
   with the same function, so they cannot drift. This is the failure that would
   be silent and total - every speculative script paid for and never read,
   while the feed looks exactly as it did - and it is the first test here.
2. **It never competes with a live listener.** A speculative episode that
   delays a real one has inverted the entire point.
3. **It never spends past its ceiling**, in either currency.
4. **It says whether it is paying.** Warmed and taken are counted separately,
   per source, because "how much to prefetch" cannot be answered by anything
   except the hit rate.

Nothing here needs a key, a network or a voice: the generator is stubbed, and
prefetch never touches a voice by design - the script is the expensive,
cacheable half and audio is made on the tap.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cache as cache_mod  # noqa: E402
import config  # noqa: E402
import episode_intelligence as ei  # noqa: E402
import prefetch  # noqa: E402
import prefetch_sources  # noqa: E402
from script_generator import plan_episode  # noqa: E402


# --------------------------------------------------------------------------
# stubs
# --------------------------------------------------------------------------
class FakeGenerator:
    """A ScriptGenerator that writes without a model.

    Mirrors the three methods prefetch drives - `understand`, `live_lookup`,
    `stream_sentences` - because those are the real seam. A stub of `warm`
    itself would prove only that the stub works.
    """

    client = None

    def __init__(self, sentences=("One.", "Two."), brief=None, explode=False):
        self.sentences = list(sentences)
        self.brief = brief
        self.explode = explode
        self.understood = []
        self.wrote = []

    async def understand(self, plan, notes=None):
        self.understood.append(plan.query)
        brief = self.brief or ei.Brief(query=plan.query, subject=plan.query,
                                       search_query=plan.query)
        return dataclasses.replace(plan, brief=brief)

    async def live_lookup(self, plan, notes=None):
        self.live_lookups = getattr(self, "live_lookups", 0) + 1
        return plan

    async def stream_sentences(self, plan, notes=None):
        if self.explode:
            raise RuntimeError("the model fell over")
        self.wrote.append(plan.query)
        if notes is not None:
            notes.thread = "what happens next"
        for sentence in self.sentences:
            yield sentence


class ListSource:
    def __init__(self, name, candidates):
        self.name = name
        self._candidates = list(candidates)

    def candidates(self, listener="", limit=6):
        return self._candidates[:limit]


def candidate(query="what did the fed do", source="test", minutes=3, **kw):
    return prefetch.Candidate(query=query, minutes=minutes, source=source,
                              reason=kw.pop("reason", "because"), **kw)


@pytest.fixture
def on(monkeypatch):
    """Prefetch switched on, with a clean registry and a fresh instance."""
    prefetch.reset()
    prefetch.reset_sources()
    monkeypatch.setattr(prefetch, "settings",
                        dataclasses.replace(config.settings, prefetch=True,
                                            prefetch_level="script"))
    yield
    prefetch.reset()
    prefetch.reset_sources()


@pytest.fixture
def warmer(on):
    cache = cache_mod.MemoryScriptCache()
    generator = FakeGenerator()
    return prefetch.Prefetcher(
        generator=generator, cache=cache,
        budget=prefetch.Budget(max_episodes=10, max_dollars=1.0)), generator, cache


# --------------------------------------------------------------------------
# 1. a warmed script is actually found
# --------------------------------------------------------------------------
def test_prefetch_and_the_tap_compute_the_same_cache_key():
    """The failure this prevents is silent and total.

    Prefetch writes a script under a key; a tap looks one up. If the two are
    computed by different code they agree today and drift the first time one
    gains a field - and then every speculative script is paid for and never
    read, while the feed looks exactly as it did. `pipeline.key_for` is
    module-level so that both sides run the *same* function, and this pins
    that the pipeline's method really does delegate to it.
    """
    import inspect

    import pipeline

    # The body, not the signature - the method is *called* `_cache_key`, so a
    # naive substring search matches its own name and proves nothing.
    source = inspect.getsource(pipeline.PodcastPipeline._cache_key)
    body = source.split("\n", 1)[1]
    assert "key_for(" in body, (
        "the pipeline computes its own cache key again; prefetch writes under "
        "pipeline.key_for and the two must not be able to disagree")
    assert "cache_key(plan" not in body, (
        "the pipeline calls cache_key directly, which is the second "
        "implementation this is here to prevent")

    bucket = inspect.getsource(pipeline.PodcastPipeline._bucket).split("\n", 1)[1]
    assert "bucket_for(" in bucket and "key_bucket(" not in bucket


def test_a_warmed_episode_lands_under_the_key_a_tap_looks_up(warmer):
    import pipeline

    prefetcher, _generator, cache = warmer
    asyncio.run(prefetcher.warm(candidate("what did the fed do")))

    key = asyncio.run(pipeline.key_for(plan_episode("what did the fed do", 3)))
    assert cache.get(key) == ["One.", "Two."], (
        "a tap on this question would miss the script that was written for it")


def test_the_go_deeper_thread_is_warmed_with_the_script(warmer):
    """A warmed episode has to arrive complete. A script whose predicted
    follow-up was dropped leaves Go Deeper falling back for an episode that
    was generated with every advantage."""
    import pipeline

    prefetcher, _generator, cache = warmer
    asyncio.run(prefetcher.warm(candidate("what did the fed do")))
    key = asyncio.run(pipeline.key_for(plan_episode("what did the fed do", 3)))
    assert cache.thread(key) == "what happens next"


def test_an_episode_already_in_the_cache_is_not_written_again(warmer):
    prefetcher, generator, _cache = warmer
    asyncio.run(prefetcher.warm(candidate("what did the fed do")))
    result = asyncio.run(prefetcher.warm(candidate("what did the fed do")))
    assert result == "cached"
    assert len(generator.wrote) == 1, "the same episode was paid for twice"
    assert prefetcher.ledger.skipped_already_cached == 1, (
        "a skip that is invisible makes the prefetcher look busier than it is")


# --------------------------------------------------------------------------
# 2. it stands aside for real work
# --------------------------------------------------------------------------
def test_a_listener_generating_right_now_stops_speculation(warmer):
    prefetcher, generator, _cache = warmer
    prefetcher.note_live_generation()
    assert asyncio.run(prefetcher.warm(candidate())) == "busy"
    assert not generator.wrote, (
        "a speculative episode was written while a real one was generating")


def test_speculation_resumes_once_the_server_has_been_quiet(warmer, monkeypatch):
    prefetcher, _generator, _cache = warmer
    monkeypatch.setattr(prefetch, "settings",
                        dataclasses.replace(prefetch.settings,
                                            prefetch_quiet_seconds=0.0))
    prefetcher.note_live_generation()
    assert asyncio.run(prefetcher.warm(candidate())) == "script"


def test_the_pipeline_tells_prefetch_when_a_real_listener_starts():
    """The signal has to be sent from the serving path, or the quiet check is
    reading a clock nothing ever sets."""
    import inspect

    import pipeline

    source = inspect.getsource(pipeline.PodcastPipeline.stream_pcm)
    assert "note_live_generation()" in source


def test_only_one_warm_runs_at_a_time(warmer, monkeypatch):
    """Concurrency here would be spending exactly the headroom a waiting
    listener needs."""
    prefetcher, _generator, _cache = warmer
    seen: list = []

    original = prefetcher._warm

    async def watched(cand, level):
        seen.append(("in", len(seen)))
        await asyncio.sleep(0.01)
        result = await original(cand, level)
        seen.append(("out", len(seen)))
        return result

    monkeypatch.setattr(prefetcher, "_warm", watched)

    async def both():
        await asyncio.gather(
            prefetcher.warm(candidate("one question")),
            prefetcher.warm(candidate("another question")),
        )

    asyncio.run(both())
    # in, out, in, out - never in, in.
    assert [kind for kind, _ in seen] == ["in", "out", "in", "out"]


# --------------------------------------------------------------------------
# 3. the ceiling holds
# --------------------------------------------------------------------------
def test_the_episode_ceiling_stops_a_runaway(on):
    prefetcher = prefetch.Prefetcher(
        generator=FakeGenerator(), cache=cache_mod.MemoryScriptCache(),
        budget=prefetch.Budget(max_episodes=2, max_dollars=100.0))
    results = [asyncio.run(prefetcher.warm(candidate(f"question {i}")))
               for i in range(5)]
    assert results.count("script") == 2
    assert results[2:] == ["budget", "budget", "budget"]


def test_the_dollar_ceiling_stops_a_correct_loop_being_expensive(on):
    """The two currencies fail differently. A 10-minute researched episode
    costs several times a 1-minute one, so counting episodes alone does not
    bound the bill."""
    budget = prefetch.Budget(max_episodes=100, max_dollars=0.05)
    budget.spend(0.04)
    assert budget.allows()
    budget.spend(0.02)
    assert not budget.allows()


def test_the_budget_rolls_over_rather_than_ending_for_good():
    budget = prefetch.Budget(max_episodes=1, max_dollars=1.0,
                             window_seconds=100.0)
    now = time.time()
    budget.spend(0.5, now)
    assert not budget.allows(now)
    assert budget.allows(now + 101)


def test_a_budget_reports_what_is_left_for_a_person_to_read():
    budget = prefetch.Budget(max_episodes=10, max_dollars=2.0)
    budget.spend(0.25)
    shown = budget.as_dict()
    assert shown["episodes_left"] == 9
    assert shown["dollars_left"] == pytest.approx(1.75)


def test_nothing_is_warmed_while_prefetch_is_off():
    prefetch.reset()
    generator = FakeGenerator()
    prefetcher = prefetch.Prefetcher(generator=generator,
                                     cache=cache_mod.MemoryScriptCache())
    assert asyncio.run(prefetcher.warm(candidate())) == "off"
    assert not generator.wrote


# --------------------------------------------------------------------------
# 4. it says whether it is paying
# --------------------------------------------------------------------------
def test_a_warmed_script_that_gets_taken_is_counted_once(warmer):
    import pipeline

    prefetcher, _generator, _cache = warmer
    asyncio.run(prefetcher.warm(candidate("what did the fed do", source="trending")))
    key = asyncio.run(pipeline.key_for(plan_episode("what did the fed do", 3)))

    assert prefetcher.ledger.note_consumed(key) is True
    assert prefetcher.ledger.note_consumed(key) is False, (
        "the second listener to take a warmed script is a cache win, which the "
        "cache already counts - not a second prediction coming true")
    assert prefetcher.ledger.as_dict()["by_source"]["trending"]["taken"] == 1


def test_a_hit_on_something_nobody_warmed_is_not_claimed(warmer):
    prefetcher, _generator, _cache = warmer
    assert prefetcher.ledger.note_consumed("a key from somewhere else") is False


def test_the_hit_rate_is_none_rather_than_zero_when_nothing_was_warmed(warmer):
    """Zero out of zero reads as a failing prefetcher and is actually no data
    at all - which is the opposite of what the open question needs."""
    prefetcher, _generator, _cache = warmer
    assert prefetcher.ledger.as_dict()["hit_rate"] is None


def test_the_hit_rate_is_reported_per_source(warmer):
    """Which *kinds* of guess get taken is the whole point. A single blended
    rate cannot tell trending from a personal rail, and those have completely
    different costs and completely different answers."""
    import pipeline

    prefetcher, _generator, _cache = warmer
    asyncio.run(prefetcher.warm(candidate("question one", source="trending")))
    asyncio.run(prefetcher.warm(candidate("question two", source="mixes")))
    key = asyncio.run(pipeline.key_for(plan_episode("question one", 3)))
    prefetcher.ledger.note_consumed(key)

    by_source = prefetcher.ledger.as_dict()["by_source"]
    assert by_source["trending"] == {"warmed": 1, "taken": 1, "dollars": 0.0}
    assert by_source["mixes"]["taken"] == 0


def test_the_serving_path_is_what_records_a_hit():
    """A hit rate inferred anywhere but the place the script is actually
    served is a hit rate nobody should trust."""
    import inspect

    import pipeline

    source = inspect.getsource(pipeline.PodcastPipeline.stream_pcm)
    assert "prefetch.note_consumed(key)" in source


def test_a_failed_warm_is_counted_and_never_raises(on):
    prefetcher = prefetch.Prefetcher(generator=FakeGenerator(explode=True),
                                     cache=cache_mod.MemoryScriptCache())
    assert asyncio.run(prefetcher.warm(candidate())) == "failed"
    assert prefetcher.ledger.failures == 1


# --------------------------------------------------------------------------
# what gets warmed
# --------------------------------------------------------------------------
def test_sources_are_interleaved_so_one_cannot_take_the_whole_budget(on):
    """A prefetcher that spends everything on trending has no evidence about
    whether personalised guesses pay - and per-source hit rate is the number
    the open question needs."""
    prefetch.register(ListSource("a", [candidate(f"a{i}", "a") for i in range(4)]))
    prefetch.register(ListSource("b", [candidate(f"b{i}", "b") for i in range(4)]))
    prefetcher = prefetch.Prefetcher(generator=FakeGenerator(),
                                     cache=cache_mod.MemoryScriptCache())

    chosen = [c.source for c in prefetcher.plan(limit=4)]
    assert chosen == ["a", "b", "a", "b"]


def test_the_same_question_twice_is_planned_once(on):
    prefetch.register(ListSource("a", [candidate("the same thing", "a")]))
    prefetch.register(ListSource("b", [candidate("The Same Thing", "b")]))
    prefetcher = prefetch.Prefetcher(generator=FakeGenerator(),
                                     cache=cache_mod.MemoryScriptCache())
    assert len(prefetcher.plan(limit=6)) == 1


def test_a_question_that_is_nobody_elses_business_is_never_warmed(on, monkeypatch):
    """The same rule a live episode obeys. Warming one would be a script
    nobody can be served, paid for in advance."""
    monkeypatch.setattr(cache_mod, "is_shareable",
                        lambda q: "private" not in q.lower())
    prefetch.register(ListSource("a", [candidate("a private question", "a"),
                                       candidate("a shared question", "a")]))
    prefetcher = prefetch.Prefetcher(generator=FakeGenerator(),
                                     cache=cache_mod.MemoryScriptCache())
    assert [c.query for c in prefetcher.plan()] == ["a shared question"]


def test_a_cycle_stops_the_moment_the_budget_is_gone(on):
    prefetch.register(ListSource("a", [candidate(f"q{i}", "a") for i in range(6)]))
    prefetcher = prefetch.Prefetcher(
        generator=FakeGenerator(), cache=cache_mod.MemoryScriptCache(),
        budget=prefetch.Budget(max_episodes=2, max_dollars=10.0))
    result = asyncio.run(prefetcher.run_once())
    assert result["outcomes"]["script"] == 2
    assert result["outcomes"]["budget"] == 1, (
        "the cycle kept asking after the answer could only be the same")


def test_a_cycle_does_not_run_while_prefetch_is_off():
    prefetch.reset()
    prefetcher = prefetch.Prefetcher(generator=FakeGenerator(),
                                     cache=cache_mod.MemoryScriptCache())
    assert asyncio.run(prefetcher.run_once())["ran"] is False


def test_two_sources_cannot_share_a_name(on):
    """Two would double-count one source's hit rate, which is the number the
    whole thing is judged on."""
    prefetch.register(ListSource("a", []))
    with pytest.raises(ValueError):
        prefetch.register(ListSource("a", []))


def test_an_unnamed_source_is_refused(on):
    with pytest.raises(ValueError):
        prefetch.register(ListSource("", []))


# --------------------------------------------------------------------------
# the brief, warmed ahead of the tap
# --------------------------------------------------------------------------
def test_a_warmed_brief_is_handed_to_the_writing_path(on, monkeypatch):
    monkeypatch.setattr(prefetch, "settings",
                        dataclasses.replace(prefetch.settings,
                                            prefetch_level="brief"))
    brief = ei.Brief(query="what did the fed do", subject="the Fed",
                     search_query="federal reserve decision")
    prefetcher = prefetch.prefetcher(generator=FakeGenerator(brief=brief),
                                     cache=cache_mod.MemoryScriptCache())
    assert asyncio.run(prefetcher.warm(candidate("what did the fed do"))) == "brief"
    assert prefetch.warm_brief("what did the fed do", 3) is brief


def test_warming_a_brief_does_not_write_a_script(on, monkeypatch):
    monkeypatch.setattr(prefetch, "settings",
                        dataclasses.replace(prefetch.settings,
                                            prefetch_level="brief"))
    generator = FakeGenerator()
    prefetcher = prefetch.Prefetcher(generator=generator,
                                     cache=cache_mod.MemoryScriptCache())
    asyncio.run(prefetcher.warm(candidate()))
    assert not generator.wrote, "the cheap level paid for the expensive one"


def test_a_degraded_brief_is_never_kept():
    """Keeping one would mean a tap *skips* contextual relevance and gets the
    pre-EI behaviour, having already paid for a call that failed - worse than
    not warming at all, and invisible."""
    store = prefetch.BriefStore(ttl_seconds=60)
    store.put("q", 3, ei.fallback_brief("q", "the call failed"))
    assert store.get("q", 3) is None


def test_a_brief_goes_stale_rather_than_making_an_episode_about_the_wrong_day():
    """A why-now hypothesis and a recency window built this morning are wrong
    by this evening."""
    store = prefetch.BriefStore(ttl_seconds=60)
    brief = ei.Brief(query="q", search_query="q")
    now = time.time()
    store.put("q", 3, brief, now=now)
    assert store.get("q", 3, now=now + 30) is brief
    assert store.get("q", 3, now=now + 61) is None


def test_a_brief_warmed_at_one_length_is_not_used_at_another():
    """Duration is part of the cache key because a 3-minute script is written
    differently from a 10-minute one. The brief follows the same rule."""
    store = prefetch.BriefStore(ttl_seconds=60)
    brief = ei.Brief(query="q", search_query="q")
    store.put("q", 3, brief)
    assert store.get("q", 3) is brief
    assert store.get("q", 10) is None


def test_the_writing_path_asks_for_a_warmed_brief_before_paying_for_one():
    import inspect

    import script_generator

    source = inspect.getsource(script_generator.ScriptGenerator.understand)
    assert "prefetch.warm_brief(" in source
    assert source.index("prefetch.warm_brief(") < source.index(
        "episode_intelligence.understand("), (
        "the brief is paid for before the warmed one is looked at")


def test_no_warm_brief_is_offered_while_prefetch_is_off():
    prefetch.reset()
    assert prefetch.warm_brief("anything", 3) is None


# --------------------------------------------------------------------------
# what a person can see
# --------------------------------------------------------------------------
def test_health_says_which_state_a_deploy_is_in():
    """A prefetcher that is off looks exactly like one that is on and missing
    everything. Both are reportable and they need different fixes."""
    prefetch.reset()
    prefetch.reset_sources()
    report = prefetch.report()
    assert report["enabled"] is False
    assert report["built"] is False
    assert report["level"] in prefetch.LEVELS


def test_health_reports_the_ledger_once_a_prefetcher_exists(warmer):
    prefetcher, _generator, _cache = warmer
    asyncio.run(prefetcher.warm(candidate()))
    report = prefetcher.report()
    assert report["warmed"] == 1 and report["taken"] == 0
    assert report["budget"]["episodes_used"] == 1


def test_the_source_report_names_what_is_missing(on):
    """A prefetcher running on one surface out of four looks identical from
    outside to one running on all of them."""
    prefetch.register(ListSource("trending", []))
    report = prefetch_sources.report()
    assert report["installed"] == ["trending"]
    assert set(report["missing"]) == {"mixes", "feed", "threads"}


def test_an_unknown_warm_level_is_refused_rather_than_guessed():
    """One that quietly meant `script` would spend a full episode per guess."""
    with pytest.raises(ValueError):
        dataclasses.replace(config.settings, prefetch_level="everything")


# --------------------------------------------------------------------------
# what must never be warmed
# --------------------------------------------------------------------------
def test_prefetch_never_spends_a_provider_call_on_a_guess():
    """A warmed live fact is stale by the time it is tapped - that is what
    "live" means - so warming one buys a score at full price in order to bake
    it into a script served hours later. The live state is fetched on the tap
    path or not at all. PROBLEMS.md §89."""
    import inspect

    source = inspect.getsource(prefetch.Prefetcher._warm)
    assert "live_lookup" not in source, (
        "prefetch is calling live_lookup; a warmed live fact is a stale one "
        "paid for in advance")


def test_a_question_whose_answer_is_a_result_keeps_its_brief_and_not_its_script(on):
    """The brief is a claim about what is being *asked* and keeps. A script
    about a game is a claim about its state and does not. Warming the second
    is §88 with a cache in front of it - so the latency saving is kept and the
    staleness is not."""
    brief = ei.Brief(query="chiefs game", subject="the Chiefs game",
                     search_query="chiefs game", intent="recap",
                     live_domain="sports", outcome_dependent=True)
    generator = FakeGenerator(brief=brief)
    pf = prefetch.Prefetcher(generator=generator,
                             cache=cache_mod.MemoryScriptCache())

    outcome = asyncio.run(pf.warm(candidate("chiefs game", "trending"),
                                  level="script"))

    assert outcome == "volatile"
    assert generator.wrote == [], "a volatile episode was written speculatively"
    assert pf.ledger.skipped_volatile == 1
    assert pf.briefs.get("chiefs game", 3) is not None, (
        "the brief should still be warmed - it is the half that keeps")


def test_an_ordinary_question_is_still_warmed_as_a_script(on):
    """The guard must be narrow. If it stopped warming everything it would
    have traded a correctness bug for the whole feature."""
    generator = FakeGenerator()
    pf = prefetch.Prefetcher(generator=generator,
                             cache=cache_mod.MemoryScriptCache())
    outcome = asyncio.run(pf.warm(candidate("how does a heat pump work", "trending"),
                                  level="script"))
    assert outcome == "script"
    assert generator.wrote == ["how does a heat pump work"]
