"""The eval's "same" case is a neutral rewording, not an attribution (§419)."""
from __future__ import annotations

import pytest

from agents import dedupe, dedupe_cases as dc


@pytest.mark.parametrize("old,new", dc.NEUTRAL_SWAPS)
def test_no_swap_adds_a_hedge_a_number_a_name_or_an_attribution(old, new):
    text = f"The board {old} the plan for the harbour project."
    other = text.replace(old, new, 1)
    assert dedupe.hedged(other) == dedupe.hedged(text) is False
    assert dedupe.names(other) == dedupe.names(text)
    assert not dc._NUMBER.search(new)
    assert "according" not in new and "reports" not in new


def test_a_rewording_keeps_the_claims_certainty():
    got = dc._reword("The museum bought the painting despite strong objections.")
    assert got == "The museum purchased the painting despite strong objections."
    assert "according to" not in got and not dedupe.hedged(got)


@pytest.mark.parametrize("text", [
    "The Eagles beat the Giants 24-17 after a late drive.",        # a figure: §417
    '"We began here," Smith said after the opening.',               # a quote
    "The talks ended, according to reports, after midnight.",       # an attribution
    "The committee met in the morning to review the plan.",         # no swap applies
])
def test_no_rewording_where_code_would_keep_the_pair_apart_or_nothing_fits(text):
    assert dc._reword(text) is None


def test_only_a_lowercase_whole_word_is_swapped():
    assert dc._reword("After the vote the council adjourned for the evening.") is None
    assert dc._reword("The bigger plan was dropped by the council members.") is None


def test_the_grown_same_case_is_reworded_and_attribution_is_gone():
    cases = dc.grow(["The city council approved the new transit budget after a long debate."],
                    disputed=[])
    same = [c for c in cases if c[1] == "same"]
    assert [c[0].split(":")[1].strip() for c in same] == ["reworded"]
    assert same[0][3][0] == ("The city council approved the new transit budget "
                             "following a long debate.")
    assert not hasattr(dc, "_attribute")
