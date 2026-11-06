"""C2: "a 94% chance of" is a forecast, in code (§465).

Found in production's dry run: "Democrats have a 94% chance of controlling
the House" was kept as a figure with status *outcome* - a prediction stored
as a result, which consuela.md never 3 forbids."""
from __future__ import annotations

import pytest

from agents import dedupe, dedupe_cases as dc, housekeeper as hk


@pytest.mark.parametrize("sentence", [
    # word for word from production's report, 2026-11-05
    "Democrats have a 94% chance of controlling the House, according to the "
    "platform, with 235 safe seats as opposed to 189 safe seats among the GOP.",
    "And per Polymarket, there is a 75% chance that Democrats will sweep.",
    "Three prediction markets place a 77.3% chance of Democrats taking control, "
    "while two polling-based models put the chances a bit higher at 80.2%.",
    "He missed a chance to tie the game late in the third.",   # the safe direction
])
def test_a_chance_is_a_forecast_whatever_the_model_said(sentence):
    assert hk.HEDGE.search(sentence)
    assert hk.status_of(sentence, "happened") == "forecast"
    assert dedupe.hedged(sentence)


@pytest.mark.parametrize("sentence", [
    "Chance the Rapper performed at halftime on Sunday.",
    "By chance, the two met in Paris in 2019.",
    "The Eagles beat the Giants 24-17 on Sunday.",
])
def test_chance_alone_is_not_a_hedge(sentence):
    assert not hk.HEDGE.search(sentence)
    assert hk.status_of(sentence, "happened") == "outcome"


def test_a_chance_and_its_result_are_never_one_fact():
    a = {"text": "Democrats have a 94% chance of winning the House.",
         "published_ts": 86400.0 * 20000}
    b = {"text": "Democrats have won the House.", "published_ts": 86400.0 * 20000}
    verdict, why = dedupe.code_verdict(a, b, 0.95)
    assert verdict == dedupe.DIFFERENT and why == "a forecast and an outcome"


def test_the_eval_grows_no_hedge_case_from_a_sentence_already_a_chance():
    assert dc._add_hedge("Democrats have a 94% chance of winning the House tonight.") is None
