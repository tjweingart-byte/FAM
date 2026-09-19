"""The first ten seconds decide whether there is an eleventh.

PROBLEMS.md §94. A real episode - "Dodgers game last night", researched, three
minutes, factually perfect from its fifth sentence onwards - opened like this:

    I don't have anything reliable on last night's specific Dodgers score or
    box score to hand you, and I'm not going to guess a result and dress it up
    as fact. That would be worse than useless if you're about to repeat it to
    a friend. Here's what's actually true and worth knowing, regardless of
    which game you mean.

and then gave the score, the pitcher, both home runs and the magic number.

The writing was not the problem. The *order of events* was: on a researched
episode the opening words were written before the sources landed, so the half
writing them was answering a question it did not yet hold the answer to - and
a lone answerer in that position is right to say so.

§108 removed the order of events rather than the sentence. Nothing writes
before retrieval finishes now, on either backend, and the call that speaks
carries no search tool. These tests hold that - and they keep the guard,
because a packet can still come back thin, and a model handed one can still
reach for a disclaimer.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import script_generator as sg  # noqa: E402
from script_generator import (  # noqa: E402
    OpeningGuard, ScriptNotes, build_prompt, is_meta_sentence, plan_episode,
)

# The reported opener, sentence by sentence, exactly as it was spoken.
DODGERS = [
    "I don't have anything reliable on last night's specific Dodgers score or "
    "box score to hand you, and I'm not going to guess a result and dress it "
    "up as fact.",
    "That would be worse than useless if you're about to repeat it to a friend.",
    "Here's what's actually true and worth knowing, regardless of which game "
    "you mean.",
    "Because it's the stuff that makes any given September Dodgers game make "
    "sense.",
    "Yamamoto worked seven innings in Cincinnati last night and gave up "
    "exactly one hit.",
]


# --------------------------------------------------------------------------
# what counts as meta
# --------------------------------------------------------------------------
@pytest.mark.parametrize("sentence", [
    DODGERS[0],
    DODGERS[2],
    "I can't confirm the final score.",
    "I'm not going to guess who won.",
    "Here's what I can tell you about the Dodgers.",
    "There is no reliable information on last night's game.",
    "Based on what I have, the season is still going.",
    "As of my knowledge cutoff, the roster looked different.",
    "I should be honest: the details are thin.",
])
def test_a_sentence_about_our_own_reading_is_meta(sentence):
    assert is_meta_sentence(sentence)


@pytest.mark.parametrize("sentence", [
    # §88 requires these. A thing unresolved *in the world* is the episode.
    "The game is in the seventh and the Dodgers lead by two.",
    "Nobody knows yet how the arbitration will land.",
    "The count is still going and neither side is claiming it.",
    "Yamamoto worked seven innings and gave up exactly one hit.",
    # Somebody else saying it is reporting, and reporting is the episode.
    'Roberts said afterwards, "I don\'t know how he did it."',
    "The manager doesn't know whether Freeman starts tomorrow.",
])
def test_a_fact_about_the_world_is_never_meta(sentence):
    assert not is_meta_sentence(sentence)


# --------------------------------------------------------------------------
# the guard
# --------------------------------------------------------------------------
def _run(guard, sentences):
    return [s for s in sentences if guard.allow(s)]


def test_the_reported_opener_never_reaches_the_voice():
    """All four sentences, including the two that are only meta because of the
    one before them."""
    kept = _run(OpeningGuard(), DODGERS)
    assert kept == [DODGERS[4]]


def test_the_guard_switches_itself_off_once_the_episode_starts():
    """A piece that names something unresolved halfway through - which §88
    requires it to - must not be edited by a rule written for openings."""
    guard = OpeningGuard()
    later = "I can't confirm that, and neither can anyone else."
    kept = _run(guard, ["Yamamoto worked seven innings in Cincinnati.", later])
    assert kept == ["Yamamoto worked seven innings in Cincinnati.", later]


def test_a_clean_opening_passes_through_untouched():
    clean = [
        "Yamamoto took the mound in Cincinnati with the division two wins away.",
        "That is the whole of the Dodgers' September, compressed into one start.",
    ]
    assert _run(OpeningGuard(), clean) == clean


def test_the_guard_can_never_reach_the_body_of_a_piece():
    """Bounded by sentence count as well as by content: a stream that somehow
    never says anything real still gets its guard turned off."""
    guard = OpeningGuard()
    kept = _run(guard, ["I don't have that."] * (OpeningGuard.WINDOW + 3))
    assert len(kept) == 3, "the window did not close"


def test_a_half_that_is_nothing_but_disclaimer_is_spoken_rather_than_silence():
    """The one case where the guard gives its text back. Silence is the single
    failure this product never accepts - see the no-filler constraint."""
    guard = OpeningGuard()
    assert _run(guard, DODGERS[:3]) == []
    rescued = guard.rescue()
    assert rescued.startswith("I don't have anything reliable")
    assert guard.rescue() == "", "rescued twice"


def test_a_stream_that_said_something_real_is_never_rescued():
    guard = OpeningGuard()
    _run(guard, DODGERS)
    assert guard.rescue() == ""


# --------------------------------------------------------------------------
# it is wired into the one path every entry point goes through
# --------------------------------------------------------------------------
class _Streamed:
    """A model that opens with the reported disclaimer."""

    def __init__(self, text):
        self.text = text

    def messages(self):  # pragma: no cover - not used
        raise AssertionError

    class _Stream:
        def __init__(self, text):
            self.text = text

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        @property
        def text_stream(self):
            async def gen():
                for chunk in self.text.split(" "):
                    yield chunk + " "
            return gen()

        async def get_final_message(self):
            class _Final:
                stop_reason = "end_turn"
                usage = None
            return _Final()


def _generator(text):
    gen = sg.ScriptGenerator.__new__(sg.ScriptGenerator)

    class _Messages:
        def stream(self, **kwargs):
            return _Streamed._Stream(text)

    class _Client:
        messages = _Messages()

    gen.client = _Client()
    return gen


def test_the_pipeline_never_sees_the_dropped_sentences():
    """`stream_sentences` is where every entry point meets the model - the
    pipeline, `write.py`, a top-up - so the guard lives there and nothing
    downstream has to know about it."""
    gen = _generator(" ".join(DODGERS))
    notes = ScriptNotes()
    plan = dataclasses.replace(plan_episode("dodgers game last night", 3),
                               search=False)

    async def collect():
        return [s async for s in gen.stream_sentences(plan, notes)]

    spoken = " ".join(asyncio.run(collect()))
    assert "I don't have anything reliable" not in spoken
    assert spoken.startswith("Yamamoto")
    # Held back, not hidden: a prompt rule that failed is a prompt rule to fix.
    assert len(notes.meta_openings) == 4
    assert notes.meta_openings[0].startswith("I don't have")


# --------------------------------------------------------------------------
# the order of events, which is the half that stops it being written at all
# --------------------------------------------------------------------------
def test_the_call_that_speaks_never_carries_a_search_tool():
    """The whole of §108 in one assertion.

    While the tool was attached to the writing call, the model could - and
    did - emit a sentence before calling it, and that sentence was spoken.
    Retrieval is its own call now, so by the time this one runs the looking
    is over.
    """
    gen = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    for plan in (
        dataclasses.replace(plan_episode("dodgers game last night", 3),
                            search=True),
        dataclasses.replace(plan_episode("dodgers game last night", 3),
                            search=True, evidence="SOURCE 1\nTitle: x"),
        plan_episode("what is the NASDAQ", 3),
    ):
        assert "tools" not in gen._request_kwargs(plan)


def test_nothing_tells_the_writer_to_go_and_search():
    """The `research_now` block went with the tool. A prompt that asks the
    model to search would now be asking it to do again, mid-episode, what has
    already been done - at 10-25 seconds a time, with the listener waiting."""
    plan = dataclasses.replace(plan_episode("dodgers game last night", 3),
                               search=True)
    text = build_prompt(plan)
    for gone in ("Write nothing at all until you have searched",
                 "you have a web search tool",
                 "Search first, then write"):
        assert gone not in text


def test_there_is_no_opening_half_to_brief():
    """`role` and ROLE_BRIEFS are deleted, not defaulted off. A half written
    from nothing cannot be prompted into knowing something."""
    assert not hasattr(sg, "ROLE_BRIEFS")
    assert "role" not in {f.name for f in dataclasses.fields(sg.EpisodePlan)}


def test_the_writer_is_told_to_decide_the_episode_before_it_opens():
    """The positive half of the fix. The opening is still written first and
    heard first; what changed is that everything needed to write it is now in
    front of the model when it does."""
    text = build_prompt(plan_episode("dodgers game last night", 3))
    assert "Decide the whole piece before you write the first word" in text
    assert "would also open an episode about something else" in text


def test_the_house_rules_draw_the_line_where_it_actually_falls():
    """Where something stands in the world is the episode; where it stands in
    our notes never is. Losing that distinction is how a ban on hedging turns
    into an episode that will not say a game is still going."""
    prompt = sg.SYSTEM_PROMPT
    assert "Never say what you do not have" in prompt
    assert "where it stands in your notes never is" in prompt
    assert "the game is in the seventh" in prompt
