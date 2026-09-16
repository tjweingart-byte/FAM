"""The motivating case, end to end: a question asked while the game is on.

FAM was asked "Chiefs game" during the third quarter and said *"Kansas City
beat Denver 27-16"*. Every test here is one link in the chain that allowed it,
asserted at the level it actually broke.

The product rule these enforce, which is the one that matters more than any
other in this repository:

    **FAM must never confidently invent a current or recent fact when the
    system does not have authoritative, sufficiently fresh evidence for it.**

Which has a corollary that is easier to get wrong, and is asserted here more
often than the rule itself: **the absence of evidence is not evidence.** Not
having a scores provider is not the game having no score. A search that missed
next week's fixture is not the fixture being unannounced. A provider that fell
over is not an event that did not happen.

Nothing here needs a key, a network or a provider - the fake scoreboard in
`live_sources` is selected explicitly, which is the only way it is ever
reachable.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cache as cache_mod  # noqa: E402
import episode_intelligence as ei  # noqa: E402
import live_facts  # noqa: E402
import live_sources  # noqa: E402
from config import settings  # noqa: E402
from script_generator import ScriptNotes, build_prompt, plan_episode  # noqa: E402

NOW = datetime.now(timezone.utc)


@pytest.fixture
def registry():
    """A clean source registry and clean caches around every test."""
    original = list(live_facts._SOURCES)
    live_facts.clear_caches()
    yield live_facts._SOURCES
    live_facts._SOURCES[:] = original
    live_facts.clear_caches()


def brief(**kw) -> ei.Brief:
    base = dict(query="tell me about the chiefs game",
                subject="the Kansas City Chiefs game against Denver",
                search_query="chiefs broncos", intent="recap",
                structure="sports_recap", live_domain="sports",
                recency_days=2)
    base.update(kw)
    return ei.gate(ei.Brief(**base), base["query"])


def prompt_for(live=None, evidence="", thin_on=(), the_brief=None) -> str:
    plan = dataclasses.replace(
        plan_episode("tell me about the chiefs game", 3),
        brief=the_brief or brief(), evidence=evidence, thin_on=tuple(thin_on),
        live=live)
    return build_prompt(plan)


#: An evidence packet made entirely of pregame writing - which is what Exa
#: returns while a game is being played, because the recap does not exist yet.
PREGAME = """SOURCE 1
Title: Chiefs vs Broncos: how to watch, odds, injury report
Published: 2026-09-14 (yesterday)
Source type: national outlet
Key evidence:
Kansas City opened as two-and-a-half point favorites.
Josh Simmons is out with a back injury; an undrafted rookie is expected to start.
"""


# --------------------------------------------------------------------------
# Case 1: the game is currently being played
# --------------------------------------------------------------------------
def test_the_request_is_recognised_as_wanting_a_result():
    """The one thing EI may decide here, and it decides it from the request."""
    assert brief().outcome_dependent is True


def test_a_live_provider_reporting_in_progress_forbids_a_result(registry):
    live_facts.register(live_sources.FakeSportsSource(live_facts.IN_PROGRESS))
    result = asyncio.run(live_facts.lookup(brief()))

    assert result.outcome == live_facts.FACTS
    assert result.status == live_facts.IN_PROGRESS

    text = prompt_for(live=result, evidence=PREGAME)
    assert "UNDER WAY" in text
    assert "no result, no winner and no final score" in text
    assert "Do not project how it ends" in text
    assert "describe anyone's performance in it as settled" in text


def test_an_in_progress_episode_is_never_written_to_the_shared_cache():
    """`recent()` is the Explore feed, so caching one does not merely re-serve
    it - it publishes a game in progress as a finished episode."""
    assert cache_mod.ttl_for("chiefs game", live_status=live_facts.IN_PROGRESS,
                             outcome_dependent=True) == 0


def test_pregame_evidence_is_named_as_evidence_of_no_result():
    """The packet that produced the original failure. Previews are not thin
    evidence of an outcome; they are evidence there is no outcome yet."""
    text = prompt_for(evidence=PREGAME)
    assert "not thin evidence of an outcome" in text
    for tell in ("odds", "projected line-ups", "how to watch", "expected to"):
        assert tell in text


def test_the_writer_is_given_a_shape_that_does_not_need_a_result():
    """Dropping the result beat from a recap leaves nothing, so the
    alternative has to be named rather than deduced."""
    text = prompt_for(evidence=PREGAME)
    assert "in-progress one instead" in text
    assert "Do not keep the shape and fill the missing beat" in text


# --------------------------------------------------------------------------
# Case 2: pregame
# --------------------------------------------------------------------------
def test_a_scheduled_event_may_use_previews_and_still_has_no_result(registry):
    live_facts.register(live_sources.FakeSportsSource(live_facts.SCHEDULED))
    result = asyncio.run(live_facts.lookup(brief()))
    assert result.status == live_facts.SCHEDULED

    text = prompt_for(live=result, evidence=PREGAME)
    assert "NOT STARTED" in text
    assert "future tense" in text
    # The preview packet is still there to be used - it is the right evidence
    # for a game that has not been played.
    assert "two-and-a-half point favorites" in text


def test_a_scheduled_episode_keeps_for_a_while_but_not_a_day():
    ttl = cache_mod.ttl_for("chiefs game", live_status=live_facts.SCHEDULED,
                            outcome_dependent=True)
    assert 0 < ttl < settings.cache_ttl_seconds


# --------------------------------------------------------------------------
# Case 3: final
# --------------------------------------------------------------------------
def test_a_final_result_may_be_stated_when_evidence_establishes_it(registry):
    live_facts.register(live_sources.FakeSportsSource(live_facts.FINAL))
    result = asyncio.run(live_facts.lookup(brief()))
    assert result.status == live_facts.FINAL

    text = prompt_for(live=result, evidence=PREGAME)
    assert "FINISHED" in text
    assert "You may state it" in text
    assert "twenty-seven to sixteen" in text


def test_a_final_episode_is_cacheable_like_anything_else():
    assert cache_mod.ttl_for(
        "chiefs game", live_status=live_facts.FINAL,
        outcome_dependent=True) == settings.cache_ttl_seconds


# --------------------------------------------------------------------------
# Case 4: no live provider at all - the shipped state
# --------------------------------------------------------------------------
def test_with_no_provider_the_limitation_is_stated_and_nothing_is_invented(registry):
    result = asyncio.run(live_facts.lookup(brief()))
    assert result.outcome == live_facts.NOT_CONFIGURED
    assert result.status == live_facts.UNKNOWN

    text = prompt_for(live=result, evidence=PREGAME)
    flat = " ".join(text.split())
    assert "FAM has no live feed for this domain" in flat
    assert "Not knowing is not the same as nothing having happened" in flat
    # And the sentence that stops the opposite invention - claiming the world
    # is silent because we are.
    assert "do not tell the listener that nothing has been reported" in flat


def test_not_configured_is_never_reported_as_the_event_having_no_result():
    """The distinction this whole subsystem turns on. Our blindness is a fact
    about us."""
    block = live_facts.LiveLookup("sports", live_facts.NOT_CONFIGURED).as_prompt_block()
    flat = " ".join(block.split())
    assert "Nothing here has looked at the actual state" in flat
    assert "what is missing here is our view of it, not the event" in flat


# --------------------------------------------------------------------------
# Case 5: stale provider data
# --------------------------------------------------------------------------
class _StaleSource(live_sources.FakeSportsSource):
    name = "stale test scoreboard"

    async def fetch(self, entity):
        facts = await super().fetch(entity)
        facts.as_of = datetime.now(timezone.utc) - timedelta(hours=2)
        return facts


def test_stale_data_cannot_be_represented_as_current(registry):
    """Stale live data is more dangerous than none: it carries a timestamp and
    outranks the packet. It is withheld, not annotated and passed on."""
    live_facts.register(_StaleSource(live_facts.IN_PROGRESS))
    result = asyncio.run(live_facts.lookup(brief()))

    assert result.outcome == live_facts.STALE
    assert result.facts is None
    assert result.status == live_facts.UNKNOWN

    text = prompt_for(live=result, evidence=PREGAME)
    assert "too old to describe the state now" in " ".join(text.split())
    assert "twenty-one to seven" not in text, "stale figures reached the writer"


def test_a_delayed_feed_says_delayed_rather_than_current():
    facts = live_facts.LiveFacts(
        domain="markets", source="a delayed quotes feed",
        as_of=datetime.now(timezone.utc), facts=["The index is at forty-two ten."],
        status=live_facts.UNKNOWN, delayed_seconds=900)
    block = facts.as_prompt_block()
    assert "DELAYED" in block
    assert "never call it the current one" in block


# --------------------------------------------------------------------------
# Case 6: the provider failed
# --------------------------------------------------------------------------
class _BrokenSource(live_sources.FakeSportsSource):
    name = "broken test scoreboard"

    async def fetch(self, entity):
        raise RuntimeError("upstream 503")


def test_a_provider_failure_is_distinct_from_there_being_no_information(registry):
    live_facts.register(_BrokenSource())
    result = asyncio.run(live_facts.lookup(brief()))

    assert result.outcome == live_facts.PROVIDER_FAILED
    assert "upstream 503" in result.detail

    flat = " ".join(prompt_for(live=result, evidence=PREGAME).split())
    assert "FAM's live feed FAILED when asked" in flat
    assert "says nothing whatever about the world" in flat


def test_a_provider_failure_does_not_stop_the_episode(registry):
    """A live provider improves an episode that is already answerable. It must
    never be able to prevent one."""
    live_facts.register(_BrokenSource())
    result = asyncio.run(live_facts.lookup(brief()))
    text = prompt_for(live=result, evidence=PREGAME)
    assert "two-and-a-half point favorites" in text, (
        "the research packet should still be written from")
    assert text.strip().endswith("Begin.")


# --------------------------------------------------------------------------
# Case 7: a stable fact the live provider does not carry
# --------------------------------------------------------------------------
def test_a_settled_fact_missing_from_one_search_is_not_declared_unknown():
    """The other half of the original failure: FAM said next week's fixture
    "hasn't been pinned down" when the schedule had been public since May.

    One search missing something is a fact about the search. For a thing that
    changes those nearly coincide; for a thing settled months ago they do not
    coincide at all."""
    text = prompt_for(evidence=PREGAME, thin_on=("the Chiefs' week 2 opponent",))
    flat = " ".join(text.split())

    assert "a fact about the search, not about the world" in flat
    assert "not yet reported" not in flat
    # Something already settled may be answered from knowledge, or left out -
    # never announced as missing, and never the last thing heard.
    assert "already settled" in flat
    assert "the search missing it proves nothing" in flat
    assert "never announce the gap" in flat.lower()
    assert "end the episode on one of these" in flat


def test_a_thing_that_changes_is_still_never_supplied_from_memory():
    """The rule must not have been loosened into permission."""
    flat = " ".join(prompt_for(evidence=PREGAME,
                               thin_on=("the final score",)).split())
    assert "Do not supply it from memory" in flat


# --------------------------------------------------------------------------
# the evidence precedence is deterministic and stated
# --------------------------------------------------------------------------
def test_the_writer_is_given_an_order_rather_than_a_judgement_call(registry):
    live_facts.register(live_sources.FakeSportsSource(live_facts.IN_PROGRESS))
    result = asyncio.run(live_facts.lookup(brief()))
    flat = " ".join(prompt_for(live=result, evidence=PREGAME).split())

    assert "this is the order, and it is not a judgement call" in flat
    assert "Your own memory never establishes anything current" in flat
    # And a contradiction is never closed by inventing a reconciliation.
    assert "A contradiction is information; never explain it away" in flat


def test_a_live_block_says_it_outranks_the_articles(registry):
    live_facts.register(live_sources.FakeSportsSource(live_facts.IN_PROGRESS))
    result = asyncio.run(live_facts.lookup(brief()))
    text = prompt_for(live=result, evidence=PREGAME)
    assert "most authoritative thing you have been given" in text
    assert text.index("live_facts") < text.index("<evidence>"), (
        "the live state must be read before the evidence it outranks")


# --------------------------------------------------------------------------
# the live lookup does not add its latency to research
# --------------------------------------------------------------------------
def test_the_live_lookup_and_the_research_run_concurrently(registry, monkeypatch):
    """They read the same brief and neither reads the other's output, so
    running them in series added the whole live latency in front of the first
    word for nothing. Verified as behaviour rather than asserted from the
    source: both are made slow, and the pair must take about as long as one."""
    import time

    import script_generator as sg

    class SlowGenerator(sg.ScriptGenerator):
        def __init__(self):  # noqa: D107 - no client needed; prepare is the seam
            pass

        async def understand(self, plan, notes=None):
            return dataclasses.replace(plan, brief=brief())

        async def live_lookup(self, plan, notes=None):
            await asyncio.sleep(0.25)
            return dataclasses.replace(
                plan, live=live_facts.LiveLookup("sports", live_facts.NOT_CONFIGURED))

        async def research(self, plan, notes=None):
            await asyncio.sleep(0.25)
            return dataclasses.replace(plan, evidence=PREGAME)

    notes = ScriptNotes()
    started = time.monotonic()
    plan = asyncio.run(SlowGenerator().prepare(
        plan_episode("tell me about the chiefs game", 3), notes))
    elapsed = time.monotonic() - started

    assert elapsed < 0.45, f"live and research ran in series ({elapsed:.2f}s)"
    # And the merge kept both halves rather than one overwriting the other.
    assert plan.live is not None and plan.evidence == PREGAME


def test_prepare_sends_the_cache_policy_home_on_the_notes(registry):
    """The caller holds the unprepared plan, so this is the only way the cache
    TTL can know what the episode was built from."""
    import script_generator as sg

    class Generator(sg.ScriptGenerator):
        def __init__(self):  # noqa: D107
            pass

        async def understand(self, plan, notes=None):
            return dataclasses.replace(plan, brief=brief())

        async def research(self, plan, notes=None):
            return plan

    live_facts.register(live_sources.FakeSportsSource(live_facts.IN_PROGRESS))
    notes = ScriptNotes()
    asyncio.run(Generator().prepare(
        plan_episode("tell me about the chiefs game", 3), notes))

    assert notes.live_status == live_facts.IN_PROGRESS
    assert notes.outcome_dependent is True
    assert notes.live_outcome == live_facts.FACTS
    assert cache_mod.ttl_for("tell me about the chiefs game",
                             live_status=notes.live_status,
                             outcome_dependent=notes.outcome_dependent) == 0
