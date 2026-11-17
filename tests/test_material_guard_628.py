"""The material check, widened and then reviewed (§628, §632, §633).

Real drafts spoke about the writer's own access in ways §288's check let
through: in the plural ("the latest price we have"), in the singular after
the opening check had stopped looking ("I don't have a confirmed result",
sentence 14), and as what it "can" do ("the last price we can date"). The
step that sets quoted speech aside took any two apostrophes for a pair of
quote marks. An independent review of the first fix then found it dropping
history and science told with "we", the middle of a long quotation, and the
facts after a dropped opening - every one of those is a test here.
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
    "I can't tell you what the judges said about the book, so I won't pretend to.",
    # §632: what the writer can do with a figure.
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
    # §633: the same leaks in the forms the review found still passing.
    "The latest price I have is ninety.",
    "That is the latest we have.",
    "Where it stands right now isn't something we can see.",
    "I can't yet confirm the score.",
    "We can't tell you where it stands this afternoon.",
    "Back in the '90s, I don't have a confirmed result for the Dodgers' opener.",
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
    # §633, the review's: history and science told with "we".
    "What is happening in the Arctic today is not something we have seen in recorded history.",
    "What is happening now is not something we have seen since 1929.",
    "What happened in the first moment after the Big Bang is not something we can see.",
    "Where the comet is now is not something we can see from Earth.",
    "What the Fed does now is not something we can know in advance.",
    "We don't have an exact figure for the death toll at Pompeii.",
    "We do not have any reliable numbers for the population of ancient Rome.",
    "Scientists admit we don't have a number for how many insect species exist.",
    "The last reading we have from Voyager 2's plasma instrument came in 2018.",
    "The most recent results we have from the Large Hadron Collider confirm the Higgs mass.",
    "The last direct quote we have from Shakespeare is his will.",
    "The financial reporting we have today grew out of the crash of 1929.",
    "The last result we can verify from the 1904 Olympics is the marathon.",
    "The last number we can confirm in the Rhind papyrus is seven.",
    "What we can date is the charcoal in the hearth, which is forty thousand years old.",
    "What we can establish is that Richard was in the Tower that night.",
    "Nothing we can establish about the Voynich manuscript tells us who wrote it.",
    # ...and somebody reported without quotation marks.
    "I can't say enough about how good this defense has been, Sirianni told reporters.",
    "Socrates's whole method rests on one admission: I do not have the answer.",
    "In I Don't Have the Heart, Ingram sings I don't have the heart to hurt you.",
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
    # Nor is a dropped letter or a decade the start of a quotation.
    for elided in ("Back in the '90s, I don't have a result for the Dodgers' opener.",
                   "Tell 'em I don't have a result for the traders' guess."):
        assert sg._QUOTED.sub(" ", elided) == elided
    # Quoted speech is still somebody else's, in either kind of quote mark,
    # and however long it runs.
    long_quote = ('"' + "The committee met for two days and looked at every number it had. " * 4
                  + "I can't tell you today what the number will be." + '" the chair said.')
    assert 200 < len(long_quote) < 600
    for said in ('"I don\'t know yet," the manager said.',
                 "'I don't know yet,' the manager said.",
                 "“I don't have the numbers,” she said.", long_quote):
        assert not sg.is_meta_sentence(said), said
        assert not sg.is_material_sentence(said), said


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
    # Never silence (§94, §288).
    only = ["The latest price we have is from Sunday morning.",
            "That is the last price we can put a time on."]
    spoken, reader = _read(only)
    assert " ".join(spoken) == " ".join(only)
    assert reader.material.dropped == only


def test_a_dropped_opening_does_not_take_the_facts_after_it():
    # The review's (§633): "It was three to one" leans on nothing that was
    # dropped for being a disclaimer - the score is in it.
    spoken, reader = _read(["The last score we have is from the seventh inning.",
                            "It was three to one, Dodgers.",
                            "Ohtani had homered twice by then."])
    assert spoken == ["It was three to one, Dodgers.", "Ohtani had homered twice by then."]
    assert reader.material.dropped == ["The last score we have is from the seventh inning."]
    assert reader.guard.dropped == []


def test_a_dropped_opening_is_never_said_again_after_the_episode():
    facts = ["It finished at ninety-five dollars.", "That was up two percent on the day.",
             "This is the highest close since March.", "So the stock is within reach of its record.",
             "It has climbed every week since the earnings report.",
             "Analysts at three banks raised their targets on Monday.",
             "The next earnings report is due in January."]
    spoken, reader = _read(["The latest price we have is from Friday's close."] + facts)
    assert spoken == facts
    # And the fault as it was on Main: six disclaimers, then the episode.
    meta = [f"I should be honest that I do not know detail {i}." for i in range(6)]
    spoken, reader = _read(meta + facts[5:])
    assert spoken == facts[5:] and reader.guard.dropped == meta


def test_the_middle_of_a_long_quotation_is_somebody_elses():
    body = ["The Eagles lost by eight on Sunday.", "Afterwards Sirianni was blunt.",
            '"We got lucky.', "I don't have an explanation for the second quarter.",
            'We will fix it."', "The Cowboys are next."]
    spoken, reader = _read(body)
    assert spoken == body and reader.material.dropped == []
    # A stray mark is forgotten after a few sentences, and the check is back.
    stray = ['The guard is 5\'10" and quick.'] + [f"Fact {i} is a fact." for i in range(5)]
    leak = "I don't have a confirmed result for that return."
    spoken, reader = _read(["The team won on Sunday."] + stray + [leak])
    assert reader.material.dropped == [leak]


def test_no_house_example_or_candidate_trips_either_check():
    examples = ROOT / "examples"
    texts = [p.read_text(encoding="utf-8").partition("\n\n")[2]
             for folder in (examples, examples / "candidates") for p in folder.glob("*.txt")]
    texts += [sg.parse_pair(p.read_text(encoding="utf-8"))["after"]
              for folder in (examples / "pairs", examples / "candidates" / "pairs")
              for p in folder.glob("*.txt")]
    assert len(texts) >= 12
    for text in texts:
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", " ".join(text.split())) if s]
        spoken, reader = _read(sentences)
        assert reader.material.dropped == [] and reader.guard.dropped == [], sentences[0]
