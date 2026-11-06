"""What Consuela is offered is whole sentences of the article, once (§466).

Each case is word for word from production's dry run (2026-11-05)."""
from __future__ import annotations

import pytest

from agents import housekeeper as hk


def _offered(text: str) -> list:
    return hk.sentences_of({"by_provider": {"fam": [text]}})


def test_a_sentence_is_never_cut_at_an_abbreviation():
    got = _offered(
        "Betting prices moved quickly in favor of Democrats after polls began closing "
        "at 6 p.m. ET (2300 GMT), and by 9 p.m. ET (0200 GMT on Wednesday) had priced "
        "a Democratic takeover at close to 100%. The gap has averaged about 7.3 "
        "percentage points since Sept. 1, according to a Yahoo Finance analysis.")
    assert got == [
        "Betting prices moved quickly in favor of Democrats after polls began closing "
        "at 6 p.m. ET (2300 GMT), and by 9 p.m. ET (0200 GMT on Wednesday) had priced "
        "a Democratic takeover at close to 100%.",
        "The gap has averaged about 7.3 percentage points since Sept. 1, according "
        "to a Yahoo Finance analysis."]


def test_a_sentence_the_article_repeats_is_offered_once_in_its_first_words():
    got = _offered(
        "And per Polymarket, there is a 75% chance that Democrats will sweep. ... "
        "And per Polymarket, there is a 75% chance that Democrats will sweep. "
        "Nashville quickly doubled its advantage, but Jackson Blake responded. "
        "Nashville quickly doubled its advantage, but **Jackson Blake** responded.")
    assert got == ["And per Polymarket, there is a 75% chance that Democrats will sweep. ...",
                   "Nashville quickly doubled its advantage, but Jackson Blake responded."]


@pytest.mark.parametrize("clutter", [
    "Add The Sporting News as a* *Preferred Source by clicking here* *.",
    "1st Downs|29|23| 3rd down efficiency|6-14|5-15| Total Yards|459|350|",
    "Sign up for our newsletter to get the latest scores every morning.",
])
def test_the_page_around_the_article_is_never_offered(clutter):
    real = "Curry made 11 of 12 free throws, missing his final attempt late."
    assert _offered(f"{real} {clutter}") == [real]


@pytest.mark.parametrize("text", [
    "After a long meeting the board said no. The vote was held again on Friday.",
    "The company's newsletter reported revenue of $2 billion for the quarter.",
    "Shares of Apple rose 2% on Monday, the most since March.",
])
def test_ordinary_sentences_are_neither_joined_nor_dropped(text):
    assert " ".join(_offered(text)) == text
    if text.startswith("After a long"):
        assert len(_offered(text)) == 2


def test_nothing_is_reworded():
    text = ("Roman Josi and Ross Colton each had a goal and an assist on Tuesday. "
            "“I think we’re building,” Nashville coach Andrew Brunette said.")
    assert " ".join(_offered(text)) == text


@pytest.mark.parametrize("sentence,hedged", [
    ("For the Senate, speculators give Democrats a 78% likelihood of flipping it.", True),
    ("Models put the probability of a Democratic House at 96.2%.", True),
    ("The Senate voted 52-48 on Tuesday to confirm the nominee.", False),
])
def test_likelihood_and_probability_are_forecasts(sentence, hedged):
    assert bool(hk.HEDGE.search(sentence)) is hedged
    assert hk.status_of(sentence, "happened") == ("forecast" if hedged else "outcome")
