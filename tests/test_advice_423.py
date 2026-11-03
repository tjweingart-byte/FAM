"""Facts, never advice (decided item 17, §423): C3P0's `advice_request`
reaches the writer as one note, and the finished script is checked for a
recommendation in FAM's own voice - counted and logged, never a block.
With C3P0 off the brief carries no flag, so the writer reads what it did.
"""
from __future__ import annotations

import asyncio

import pytest

import episode_intelligence as ei
from agents import contextualizer
from agents.handoffs import ResearchPlan
from pipeline import GenerationStats, PodcastPipeline
from script_generator import ScriptNotes, plan_episode
from tests.test_pipeline import FakeGenerator
from tts import DebugEngine


@pytest.fixture(autouse=True)
def fresh():
    contextualizer.reset()
    yield
    contextualizer.reset()


def brief(**said):
    return ei.Brief(query="should I refinance my mortgage", subject="refinancing",
                    intent="explainer", contextualized=said)


# ------------------------------------------------------------------ the writer's note
def test_the_note_is_pinned():
    assert ei.ADVICE_NOTE.startswith(
        "This asks for medical, legal or financial advice, and FAM never gives it")
    assert "do not tell them what to do" in ei.ADVICE_NOTE
    assert "facts around the decision" in ei.ADVICE_NOTE
    assert "is said as theirs" in ei.ADVICE_NOTE
    assert "never on telling them to go and ask someone" in ei.ADVICE_NOTE


def test_an_advice_request_puts_the_note_in_the_brief():
    block = ei.build_brief_block(brief(advice_request=True), 3)
    assert ei.ADVICE_NOTE in block
    # Said before the cautions, with what changes what the episode is.
    flagged = ei.build_brief_block(
        ei.Brief(query="q", subject="s", contextualized={"advice_request": True},
                 cautions=["rates change daily"]), 3)
    assert flagged.index(ei.ADVICE_NOTE) < flagged.index("Be careful about this")


@pytest.mark.parametrize("said", [{}, {"advice_request": False}, {"node": "money"}])
def test_without_the_flag_the_brief_is_what_it_was(said):
    assert ei.build_brief_block(brief(**said), 3) == ei.build_brief_block(brief(), 3)
    assert ei.ADVICE_NOTE not in ei.build_brief_block(brief(**said), 3)


def test_a_degraded_brief_still_says_nothing():
    b = brief(advice_request=True)
    b.degraded = True
    assert ei.build_brief_block(b, 3) == ""


def test_c3p0s_plan_carries_the_flag_from_his_answer():
    plan = contextualizer.from_brief(brief(advice_request=True, entity="refinancing"),
                                     "should I refinance my mortgage")
    assert plan.advice_request is True
    assert contextualizer.from_brief(brief(), "q").advice_request is False


# ------------------------------------------------------------------ the check after the writer
def notes_with(advice: bool):
    notes = ScriptNotes()
    notes.research_plan = ResearchPlan(advice_request=advice)
    return notes


@pytest.mark.parametrize("line", [
    "So you should lock the rate now.",
    "I'd recommend talking it through.",
    "Your best bet is the fixed rate.",
    "We suggest waiting a year.",
    "The smart move is to wait.",
])
def test_a_recommendation_is_counted_and_logged(line, caplog):
    said = contextualizer.advice_audit(notes_with(True), ["Rates fell in October.", line])
    assert said
    counts = contextualizer.report()["since_boot"]
    assert counts["advice_planned"] == 1 and counts["advice_recommended"] == 1
    assert "recommended" in caplog.text
    assert "refinance" not in caplog.text      # the phrase, never the request


@pytest.mark.parametrize("line", [
    "A refinance resets the clock on a thirty-year loan.",
    "Whether you should refinance depends on how long you stay.",
    "Closing costs run between two and five percent of the loan.",
    "The Federal Reserve cut its rate on 29 October.",
])
def test_facts_are_not_counted(line):
    assert contextualizer.advice_audit(notes_with(True), [line]) is None
    counts = contextualizer.report()["since_boot"]
    assert counts["advice_planned"] == 1 and "advice_recommended" not in counts


@pytest.mark.parametrize("notes", [None, ScriptNotes(), notes_with(False)])
def test_no_flag_no_count(notes):
    assert contextualizer.advice_audit(notes, ["You should buy it."]) is None
    assert contextualizer.report()["since_boot"] == {}


def test_the_check_never_raises():
    class Broken:
        @property
        def research_plan(self):
            raise RuntimeError("boom")
    assert contextualizer.advice_audit(Broken(), ["x"]) is None


class Advising(FakeGenerator):
    """The test writer, with C3P0's plan flagged and a recommendation in it."""

    async def stream_sentences(self, plan, notes=None):
        notes.research_plan = ResearchPlan(advice_request=True)
        yield "Rates fell in October."
        yield "You should refinance before they rise."


def test_the_pipeline_checks_every_finished_script():
    pipeline = PodcastPipeline(generator=Advising(), engine=DebugEngine(), cache=None)

    async def run():
        async for _ in pipeline.stream_pcm(plan_episode("should I refinance", 2),
                                           GenerationStats()):
            pass

    asyncio.run(run())
    counts = contextualizer.report()["since_boot"]
    assert counts["advice_planned"] == 1 and counts["advice_recommended"] == 1
