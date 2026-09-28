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
