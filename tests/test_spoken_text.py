"""Numbers and abbreviations as they are said, before the voice (27/09 packet)."""
import inspect

import pytest

import pipeline
from spoken_text import cardinal, ordinal, speakable, year


@pytest.mark.parametrize("written, said", [
    # The four the owner heard read digit by digit.
    ("He threw for 612 yards.", "He threw for six hundred twelve yards."),
    ("A 10:00 kickoff.", "A ten o'clock kickoff."),
    ("They carry a 62-41 record.", "They carry a sixty-two and forty-one record."),
    ("The final score was 37-14.", "The final score was thirty-seven to fourteen."),
    ("The final score was 37 to 14.", "The final score was thirty-seven to fourteen."),
    # Abbreviations are letters, unless people say them as a word.
    ("The NFL and the U.S. economy.", "The N F L and the U S economy."),
    ("NASA's CEO.", "NASA's C E O."),
    # The rest of what a script is made of.
    ("In 1998, and by 2026.", "In nineteen ninety-eight, and by twenty twenty-six."),
    ("About 2000 people.", "About two thousand people."),
    ("Up 3.5% to $4.5 billion.", "Up three point five percent to four point five billion dollars."),
    ("It starts at 7:30 p.m.", "It starts at seven thirty P M."),
    ("The 23rd pick in the 1990s.", "The twenty-third pick in the nineteen nineties."),
    ("They went 10-6-1 on the season.", "They went ten, six and one on the season."),
    ("No. 1 vs. Texas.", "number one versus Texas."),
    ("It cost $20.", "It cost twenty dollars."),
    ("1,234 votes.", "one thousand two hundred thirty-four votes."),
    # Found reviewing §161: each of these came out wrong before.
    ("In 2024 his team won.", "In twenty twenty-four his team won."),
    ("2020 was strange.", "twenty twenty was strange."),
    ("Q3 2024 results.", "Q three twenty twenty-four results."),
    ("The 2010s and the 2000s.", "The twenty tens and the two thousands."),
    ("At 7:30pm, and 8:15am.", "At seven thirty P M, and eight fifteen A M."),
    ("A 3.5mm jack at 1.5x.", "A three point five mm jack at one point five x."),
    ("World War II and Super Bowl LX.", "World War Two and Super Bowl Sixty."),
    ("Henry VIII and Washington DC.", "Henry the Eighth and Washington D C."),
    ("They beat Arsenal 3-1 this season.", "They beat Arsenal three to one this season."),
    ("A 12-5 run.", "A twelve to five run."),
    ("Call 555-123-4567.", "Call five five five, one two three, four five six seven."),
    ("9/11 and 24/7.", "nine eleven and twenty-four seven."),
    ("On 9/27/2026.", "On September twenty-seventh, twenty twenty-six."),
    ("The 2024-25 season.", "The twenty twenty-four to twenty-five season."),
    ("It was -5 degrees.", "It was minus five degrees."),
    ("COVID-19 and I-95.", "COVID-nineteen and I-ninety-five."),
    ("A 401(k) at AT&T.", "A four oh one K at A T and T."),
    ("The 49ers and the 76ers.", "The forty-niners and the seventy-sixers."),
    ("A 1-2-3 inning.", "A one, two, three inning."),
    ("About 2000 people.", "About two thousand people."),
    ("In 1999 fans rioted.", "In nineteen ninety-nine fans rioted."),
])
def test_speakable(written, said):
    assert speakable(written) == said


def test_words_and_empty_text_are_left_alone():
    plain = "I said a word, and the Eagles won it."
    assert speakable(plain) == plain
    assert speakable("") == ""


def test_the_number_readers():
    assert cardinal(1_000_612) == "one million six hundred twelve"
    assert ordinal(12) == "twelfth" and ordinal(40) == "fortieth" and ordinal(101) == "one hundred first"
    assert year(2005) == "two thousand five" and year(1905) == "nineteen oh five"


def test_the_voice_is_handed_speakable_text():
    """Every synthesis call in the pipeline goes through `speakable`, and only
    the voice's input - the captions and the cache keep the digits."""
    source = inspect.getsource(pipeline)
    calls = [line for line in source.splitlines() if "self.engine.synth(" in line]
    assert calls and all("speakable(" in line for line in calls)


def test_a_device_is_told_whether_it_may_keep_the_audio():
    """Offline listening keeps only what the server marks keepable: a
    production voice, never a placeholder tone (found reviewing §161)."""
    import app as appmod

    source = inspect.getsource(appmod)
    assert '"X-FAM-Keepable"' in source
    expose = source[source.index("expose_headers="):]
    assert '"X-FAM-Keepable"' in expose[:240]
    audio = open("static/fam-audio.js").read()
    assert 'X-FAM-Keepable' in audio and "!keepable" in audio
