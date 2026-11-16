"""The material check, widened (§622).

Four of 36 real drafts spoke about the writer's own material in ways §288's
check let through: in the plural ("the latest price we have"), and in the
singular after the opening check had stopped looking ("I don't have a
confirmed result", sentence 14). And the step that sets quoted speech aside
took any two apostrophes for a pair of quote marks, so a disclaimer with two
of them matched nothing at all.
"""
from __future__ import annotations

import pathlib
import re

import pytest

import script_generator as sg
from script_generator import ScriptNotes, plan_episode

ROOT = pathlib.Path(__file__).resolve().parent.parent

# What the drafts said, with the particulars changed.
LEAKS = [
    "The most recent price in the reporting we have is from just after six, when it stood at ninety.",
    "That's the most recent full-day price we have.",
    "We don't have a figure for this morning's trading, so I can't tell you where it stands.",
    "The stock has been sliding for weeks, and the latest price we have is from Sunday morning.",
    "I don't have a confirmed result for that return.",
    "The reporting describes the landing as still planned, and nothing I can establish says "
    "whether it was made.",
    "We do not yet have a confirmed result from the count.",
    "I can't confirm the score, and I don't want to guess.",
    # §626: what the writer can do with a figure, from sixteen more scripts.
    "The index was slipping on Monday morning, at ninety dollars at ten GMT, the newest price we can date.",
    "That is the newest price we can establish.",
    "The last figure we can stand behind is Friday's close: ninety dollars.",
    "That is the last price we can put a time on.",
    "The last price we can date, from Sunday morning, is two thousand dollars above that band.",
    "Where the price stands at this moment is not something this can tell you, and both forces are live.",
    "What the shares are doing this afternoon is not something we can see yet.",
    "Where it stands now, on Monday afternoon, is not something we can see.",
    "Where they are trading this afternoon is not a figure I can give you.",
    "The coin trades around the clock, so where it stands on Monday afternoon is not something we have.",
    "Nothing we can establish gives a reason for Friday's fall itself.",
    "What we can date is a sell-off that has been building for weeks.",
]

# Sentences about the world that look like them.
REPORTING = [
    "The best evidence we have suggests the vaccine works.",
    "We don't have a cure for the common cold.",
    "The oldest records we have of the plague come from Egypt.",
    "The latest figures we have from the census are from 2020.",
    "What we do know is that the universe is expanding.",
    "We have known since 1963 that the drug works.",
    "Officials said they don't have a timeline.",
    "The last result was a draw.",
    "It's the latest score in a rivalry that's lasted decades.",
    '"I don\'t have the numbers," the coach said.',
    "'I don't know yet,' the manager said.",
    # §626's look-alikes.
    "The oldest fossils we can date are three and a half billion years old.",
    "The earliest eclipse we can date is from 1375 BC.",
    "Dark matter is not something we can see.",
    "What dark matter is made of is not something we can see directly.",
    "There is nothing we can see beyond the horizon.",
    "Nothing we can find in the fossil record suggests otherwise.",
    "Where the market stands today is not something anyone can know.",
    "What happened this morning is not something we can ignore.",
    "What the court did today is not something we can undo.",
    '"That is the last price we can establish," the analyst said.',
    "That Friday close is the newest settled figure there is.",
    "At a quarter past ten on Sunday morning, Eastern time, it was trading at ninety dollars.",
    # A title is not a disclaimer.
    "The Eagles closed the set with I Can't Tell You Why.",
    "James Ingram's I Don't Have the Heart reached number one in 1990.",
]


@pytest.mark.parametrize("sentence", LEAKS)
def test_talk_of_what_the_writer_has_is_caught_in_either_number(sentence):
    assert sg.is_material_sentence(sentence)


@pytest.mark.parametrize("sentence", REPORTING)
def test_the_world_said_with_we_or_i_is_not(sentence):
    assert not sg.is_material_sentence(sentence)


def test_an_apostrophe_inside_a_word_is_not_a_quote_mark():
    # §94's own sentence: both apostrophes used to be read as a pair of
    # quote marks, and everything between them was set aside.
    dodgers = "I don't have anything reliable on the Dodgers' opener."
    assert sg._QUOTED.sub(" ", dodgers) == dodgers
    assert sg.is_meta_sentence(dodgers) and sg.is_material_sentence(dodgers)
    # Quoted speech is still somebody else's, in either kind of quote mark.
    for said in ('"I don\'t know yet," the manager said.',
                 "'I don't know yet,' the manager said.",
                 "“I don't have the numbers,” she said."):
        assert not sg.is_meta_sentence(said), said
        assert not sg.is_material_sentence(said), said
    # A possessive beside real speech does not close the quote early.
    assert "anything" not in sg._QUOTED.sub(" ", "He said 'anything goes' at the Dodgers' camp.")


def _read(sentences: list) -> tuple:
    notes = ScriptNotes()
    reader = sg._ScriptReader(plan_episode("how is the stock doing", 2, search=False), notes)
    spoken = reader.feed(" ".join(sentences) + " ") + reader.finish()
    return [str(getattr(s, "sentence", s)) for s in spoken], reader


def test_a_late_one_is_dropped_and_nothing_is_said_in_its_place():
    body = [f"On day {i} the company reported another gain." for i in range(1, 15)]
    leak = "I don't have a confirmed result for that return."
    spoken, reader = _read(body + [leak, "The shares closed higher on Friday."])
    assert leak not in spoken and len(spoken) == len(body) + 1
    assert reader.material.dropped == [leak]


def test_a_script_that_is_nothing_else_is_still_spoken():
    only = ["I don't have a confirmed result for that return.",
            "The latest price we have is from Sunday morning."]
    spoken, reader = _read(only)
    # Never silence (§94, §288): the opening check held both, found nothing
    # else, and released them.
    assert " ".join(spoken) == " ".join(only)
    assert reader.guard.dropped == only


def test_at_the_opening_the_sentence_leaning_on_it_goes_too():
    spoken, reader = _read(["The latest price we have is from Sunday morning.",
                            "That is not much to go on.",
                            "The shares closed at ninety dollars on Friday."])
    assert spoken == ["The shares closed at ninety dollars on Friday."]
    assert len(reader.guard.dropped) == 2


def test_no_house_example_or_pair_rewrite_trips_either_check():
    examples = ROOT / "examples"
    texts = [p.read_text(encoding="utf-8").partition("\n\n")[2]
             for folder in (examples, examples / "candidates") for p in folder.glob("*.txt")]
    texts += [sg.parse_pair(p.read_text(encoding="utf-8"))["after"]
              for folder in (examples / "pairs", examples / "candidates" / "pairs")
              for p in folder.glob("*.txt")]
    assert len(texts) >= 12
    for text in texts:
        for sentence in re.split(r"(?<=[.!?])\s+", " ".join(text.split())):
            assert not sg.is_material_sentence(sentence), sentence
