"""Training Mclovin (§440): what the planted-mistake eval found, pinned.

Step 1 plants known mistakes into real sentences in code; step 2 scores
each check on what it catches and what it wrongly flags; step 3 tightens a
rule when it misses or flags a clean sentence, and adds the case here so it
cannot come back. Every sentence below is written for the test - no
provider text is kept in the repository.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from agents import supervisor_cases as cases
from agents import supervisor_checks as checks
from script_generator import ScriptNotes, plan_episode

NOW = datetime(2026, 11, 3, 15, tzinfo=timezone.utc)

EVIDENCE = """SOURCE 1
Title: Fed Holds Rates Steady As Powell Signals Patience
Published: 2026-11-02 (yesterday)
Source type: wire service, paper of record or primary source
Key evidence:
The Federal Reserve held its benchmark rate at 4.25 percent on Wednesday in Washington. Jerome Powell said "we are not in a hurry to cut rates" at a news conference.
There are one hundred and seventy-eight exclusions in place, and the S&P 500 sits near 18.5 times earnings.
Powell repeated the line later: "we are not in a hurry to cut rates," he told reporters.

SOURCE 2
Title: Eagles Beat Cowboys In Philadelphia
Published: 2026-10-14 (three weeks ago)
Source type: established outlet
Key evidence:
Jalen Hurts threw for 312 yards as the Eagles beat the Cowboys 31-17 in Philadelphia.
"""


@pytest.fixture(autouse=True)
def fresh():
    checks.reset()
    yield
    checks.reset()


def checker(evidence=EVIDENCE):
    plan = dataclasses.replace(plan_episode("the fed", 1.5), search=False, evidence=evidence)
    return checks.Checker(plan, ScriptNotes(), now=NOW)


def result(sentence, check, evidence=EVIDENCE):
    return {c.check: c.result for c in checker(evidence).check(sentence).checks}[check]


# ------------------------------------------------------------------ step 2: what enforces
def test_the_enforced_checks_are_the_ones_the_eval_passed():
    assert set(checks.DEFAULT_ENFORCED) == {"quote", "quote_length", "website", "advice",
                                            "superlative", "dates", "mixed"}
    # Figures did not pass (85% caught, 1.5% of clean sentences flagged); the
    # one-sentence quote limit passed and enforces since mclovin.md v2 (§441).
    assert "figures" not in checks.DEFAULT_ENFORCED


# ------------------------------------------------------------------ step 3: what was tightened
@pytest.mark.parametrize("sentence", [
    "It is the most recent decision we have.",
    "On October the first, none of the bills had passed.",
    "The record matters beyond the score.",
    "The first week has a clear date on it.",
    "Monday's close is not yet on record.",
])
def test_ordinary_english_is_not_a_superlative(sentence):
    assert result(sentence, "superlative") == "pass"


@pytest.mark.parametrize("sentence", [
    "The Fed held rates for the first time since the war.",
    "It was the biggest cut ever made in one meeting.",
    "Hurts set a record in Philadelphia.",
])
def test_claims_of_the_unprecedented_need_the_packets(sentence):
    assert result(sentence, "superlative") == "fail"


def test_biggest_is_vouched_only_by_its_own_kind():
    evidence = EVIDENCE.replace("its benchmark rate", "its highest benchmark rate since May")
    assert result("It was the biggest move of its kind ever recorded.", "superlative",
                  evidence) == "fail"


@pytest.mark.parametrize("text,values", [
    ("about four hundred and eighty-five thousand people", [485000.0]),
    ("a hundred and forty in 2027", [140.0]),
    ("ninety-six dollars and sixty cents", [96.0]),
    ("one thousand three hundred and ninety-eight", [1398.0]),
    ("the hundred and seventy-eight exclusions", [178.0]),
    ("near eighteen and a half times earnings", [18.5]),
    ("4.5 billion", [4.5e9]),
    ("eight percent", [8.0]),
    ("two teams met", []),
    ("COP28 found progress slow", []),
    ("on the twenty-eighth of October", []),
    ("on October 28 at 2:00", []),
])
def test_numbers_are_read_as_said(text, values):
    assert [v for v, _d in checks._numbers(text)] == values


def test_a_number_is_vouched_by_sources_about_the_same_thing():
    assert result("There are one hundred and seventy-eight exclusions.", "figures") == "pass"
    # 312 is in the packets - about Hurts, not about the Fed.
    assert result("The Federal Reserve held at 312 basis points in Washington.",
                  "figures") == "fail"


def test_history_is_checked_against_the_archive_too():
    assert result("In 2019 the rate was 2.4 percent.", "figures") == "fail"


@pytest.mark.parametrize("sentence,outcome", [
    ("Today is the third of November.", "pass"),
    ("Contracts are priced on today's tariffs.", "pass"),
    ("The number a runner trains toward today is fixed.", "pass"),
    ("Today the Eagles beat the Cowboys in Philadelphia.", "fail"),
    ("Yesterday Jalen Hurts threw for 312 yards in Philadelphia.", "fail"),
    ("Yesterday the Federal Reserve held its rate, Powell said.", "pass"),
])
def test_relative_days_by_the_listeners_clock(sentence, outcome):
    assert result(sentence, "dates") == outcome


def test_a_live_block_vouches_only_for_its_own_subject():
    @dataclasses.dataclass
    class Live:
        def as_prompt_block(self):
            return "LIVE: the Lakers lead the Kings 54-50 at halftime in Sacramento."

    plan = dataclasses.replace(plan_episode("x", 1.5), search=False, evidence=EVIDENCE,
                               live=Live())
    c = checks.Checker(plan, ScriptNotes(), now=NOW)
    got = {x.check: x.result for x in c.check(
        "Yesterday Jalen Hurts threw for 312 yards in Philadelphia.").checks}
    assert got["dates"] == "fail"


def test_a_quote_s_speaker_is_found_beside_any_place_it_appears():
    assert result('"We are not in a hurry to cut rates," Powell said.', "quote") == "pass"


@pytest.mark.parametrize("sentence", [
    "In the end, Representative Nancy Mace, a Republican, spoke on November the ninth.",
    "In the end, lawmakers return to Washington on Monday.",
])
def test_months_titles_and_parties_are_not_names(sentence):
    assert result(sentence, "mixed") == "pass"


def test_sources_sharing_names_are_one_story_whatever_their_headlines():
    a = checks.Block("x", names=frozenset({"powell", "fed", "washington", "rate"}))
    b = checks.Block("y", names=frozenset({"powell", "fed", "washington"}))
    c = checks.Block("z", names=frozenset({"hurts", "eagle", "cowboy"}))
    assert checks._stories([a, b, c])[0] == checks._stories([a, b, c])[1]
    assert checks._stories([a, b, c])[0] != checks._stories([a, b, c])[2]


def test_names_come_from_the_sources_own_text_never_the_packet_labels():
    blocks = checker().blocks
    for label in ("title", "published", "source", "key", "evidence"):
        assert all(label not in b.names for b in blocks if not b.live)


# ------------------------------------------------------------------ step 1: the planting
def test_planting_is_deterministic_and_labels_every_case():
    sentences = ["The Federal Reserve held its benchmark rate at 4.25 percent on Wednesday.",
                 "Jerome Powell said the committee would wait for more data in Washington.",
                 "There are one hundred and seventy-eight exclusions in place this week."]
    blocks = checker().blocks
    one = cases.plant(blocks, sentences, NOW, seed=3)
    assert one == cases.plant(blocks, sentences, NOW, seed=3)
    kinds = {c.kind for c in one}
    assert {"number", "advice", "website", "quote_own", "quote_speaker"} <= kinds
    for case in one:
        expected = cases.EXPECTS.get(case.kind) or cases.CONTROLS[case.kind]
        assert case.check == expected
        assert case.should_fail == (case.kind in cases.EXPECTS)


def test_every_planted_mistake_here_is_caught():
    sentences = ["The Federal Reserve held its benchmark rate at 4.25 percent on Wednesday.",
                 "Jerome Powell said the committee would wait for more data in Washington."]
    c = checker()
    c.enforce = frozenset()
    for case in cases.plant(c.blocks, sentences, NOW, seed=1):
        got = {x.check: x.result for x in c.check(case.sentence).checks}[case.check]
        assert got == ("fail" if case.should_fail else "pass"), (case.kind, case.sentence)


def test_a_changed_number_is_never_a_date_a_year_or_a_label():
    assert cases.change_number("On October 28 the rate held at 4.25 percent.") \
        == "On October 28 the rate held at 8.82 percent."
    assert cases.change_number("In 2026 two teams met.") is None
    assert cases.change_number("Under Section 301 nothing changed.") is None
