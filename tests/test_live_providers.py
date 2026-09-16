"""The real providers, against recorded payloads.

**None of these has ever made a live request from this build.** The container's
egress proxy blocks `api-sports.io`, `sportsdata.io`, `finnhub.io`,
`alphavantage.co`, `gamma-api.polymarket.com` and `api.gdeltproject.org`, so
every shape here is written from the vendors' documentation and pinned against
payloads recorded by hand.

That is a real gap and these tests do not close it - §52's rule is that a check
answering a cheaper question than the one being asked is worse than no check.
What they *do* close is the half that is testable offline and that goes wrong
silently: status mapping, spoken-sentence shaping, and the rule that a
forecast can never establish a result.

`python tools/verify_live.py` is what closes the rest, somewhere with network.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gdelt  # noqa: E402
import live_facts  # noqa: E402
import live_sources as ls  # noqa: E402


def entity(domain="sports", provider="p", id="1", label="KC v DEN"):
    return live_facts.Entity(domain=domain, provider=provider, id=id, label=label)


FIXTURE = {
    "fixture": {"id": 1, "status": {"short": "2H", "elapsed": 52}},
    "teams": {"home": {"name": "Kansas City"}, "away": {"name": "Denver"}},
    "goals": {"home": 21, "away": 7},
}


# --------------------------------------------------------------------------
# sports: the status vocabulary is mapped at the boundary
# --------------------------------------------------------------------------
@pytest.mark.parametrize("short, expected", [
    ("NS", live_facts.SCHEDULED),
    ("1H", live_facts.IN_PROGRESS),
    ("HT", live_facts.IN_PROGRESS),
    ("FT", live_facts.FINAL),
    ("AET", live_facts.FINAL),
    ("WEIRD", live_facts.UNKNOWN),
    ("", live_facts.UNKNOWN),
])
def test_api_sports_status_codes_map_or_become_unknown(short, expected):
    """A provider's own vocabulary is its own business and must be translated
    where it arrives. An unmapped code silently missing every comparison
    downstream is the failure the closed vocabulary exists to close - and
    `unknown` is not "probably fine", it is the state in which no result may
    be spoken."""
    row = {**FIXTURE, "fixture": {**FIXTURE["fixture"], "status": {"short": short}}}
    facts = ls.ApiSportsSource().to_facts(row, entity())
    assert facts is not None
    assert facts.status == expected


def test_a_game_in_progress_is_not_described_as_beaten():
    facts = ls.ApiSportsSource().to_facts(FIXTURE, entity())
    assert facts.status == live_facts.IN_PROGRESS
    assert "lead" in facts.facts[0]
    assert "beat" not in facts.facts[0]


def test_a_finished_game_may_say_beat():
    row = {**FIXTURE, "fixture": {**FIXTURE["fixture"], "status": {"short": "FT"}}}
    facts = ls.ApiSportsSource().to_facts(row, entity())
    assert facts.status == live_facts.FINAL
    assert "beat" in facts.facts[0]


def test_facts_are_spoken_sentences_rather_than_scorelines():
    """These reach a voice. "KC 21-7 DEN 3Q 10:04" is not a sentence."""
    facts = ls.ApiSportsSource().to_facts(FIXTURE, entity())
    for line in facts.facts:
        assert line.endswith(".")
        assert "-" not in line


def test_a_row_with_no_score_yields_nothing_rather_than_a_blank_fact():
    row = {"fixture": {"id": 1, "status": {"short": "2H"}},
           "teams": {"home": {"name": "A"}, "away": {"name": "B"}}, "goals": {}}
    assert ls.ApiSportsSource().to_facts(row, entity()) is None


# --------------------------------------------------------------------------
# markets: honest about the delay
# --------------------------------------------------------------------------
def test_finnhub_quotes_carry_their_delay():
    """Finnhub's free tier is about twenty minutes behind. Presented as
    current that is a lie with a timestamp attached."""
    facts = ls.FinnhubSource().to_facts(
        {"c": 231.5, "dp": -1.23, "t": 1789000000},
        entity(domain="markets", label="Apple"))
    assert facts.delayed_seconds > 0
    assert "DELAYED" in facts.as_prompt_block()


def test_a_quote_never_establishes_an_event_status():
    """A price is a price, not an outcome. `unknown` is what stops a market
    question being answered as a result."""
    facts = ls.FinnhubSource().to_facts(
        {"c": 10.0, "dp": 1.0, "t": 1789000000},
        entity(domain="markets", label="X"))
    assert facts.status == live_facts.UNKNOWN


def test_an_empty_quote_is_not_a_price_of_zero():
    assert ls.FinnhubSource().to_facts({"c": 0}, entity(domain="markets")) is None
    assert ls.FinnhubSource().to_facts({}, entity(domain="markets")) is None


# --------------------------------------------------------------------------
# forecast: a market may never close a question
# --------------------------------------------------------------------------
def test_a_prediction_market_never_reports_a_result():
    """The trap worth naming: a live win-probability line moves with the
    score, so it reads like the score. "Chiefs at 94%, so they must be
    winning" is PROBLEMS.md §88 coming back through a side door, and
    `unknown` is what forbids it structurally rather than by asking."""
    facts = ls.PolymarketSource().to_facts(
        {"bestBid": 0.94, "volume": "2.1M"},
        entity(domain="elections", label="a close race"))
    assert facts.status == live_facts.UNKNOWN
    assert facts.kind == "prediction-market"
    assert any("not a reported result" in line for line in facts.facts)


def test_a_market_price_is_spoken_as_a_percentage_a_person_would_say():
    facts = ls.PolymarketSource().to_facts(
        {"lastTradePrice": 0.87}, entity(domain="elections", label="the race"))
    assert "87 percent" in facts.facts[0]


def test_a_market_with_no_price_yields_nothing():
    assert ls.PolymarketSource().to_facts({}, entity(domain="elections")) is None


# --------------------------------------------------------------------------
# elections: the gap is named rather than faked
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["ap", "ddhq"])
def test_quote_only_election_providers_say_what_they_would_need(name):
    """Neither publishes API pricing and neither has an open endpoint, so
    there is nothing to write against. Guessing one would be worse than
    nothing; naming the gap is what makes it fixable."""
    source = ls.BUILDERS["elections"][name]()
    ok, why = source.diagnose()
    assert not ok
    assert "Needs:" in why
    assert "sales-gated" in why


# --------------------------------------------------------------------------
# every provider is honest about not being configured
# --------------------------------------------------------------------------
@pytest.mark.parametrize("domain, name", [
    ("sports", "api-sports"), ("sports", "sportsdataio"),
    ("markets", "finnhub"), ("markets", "alpha-vantage"),
])
def test_a_provider_with_no_credential_refuses_and_says_which_one(domain, name):
    source = ls.BUILDERS[domain][name]()
    ok, why = source.diagnose()
    assert not ok
    assert "_KEY is not set" in why


def test_no_provider_claims_to_have_been_verified_here():
    """The gap this build cannot close, asserted so nobody reads a green
    suite as "the providers work"."""
    for domain in ("sports", "markets"):
        for name, builder in ls.BUILDERS[domain].items():
            if name == "fake":
                continue
            ok, why = builder().diagnose()
            if ok:
                assert "not verified" in why


# --------------------------------------------------------------------------
# GDELT
# --------------------------------------------------------------------------
def test_gdelt_articles_parse_into_the_shape_the_rest_of_fam_reads():
    """Duck-typed to match an Exa result, so `rank_results`, `credibility`,
    `published_at` and `provenance.from_results` all work unchanged. A second
    retriever needing its own branch in each would be four places to forget."""
    import provenance
    import research

    results = gdelt.parse_articles({"articles": [
        {"url": "https://www.reuters.com/a", "title": "A headline",
         "seendate": "20260916T143000Z"},
        {"url": "", "title": "no url, skipped"},
    ]})
    assert len(results) == 1
    assert research.credibility(results[0]) == "primary"
    assert research.published_at(results[0]).date().isoformat() == "2026-09-16"
    assert provenance.from_results(results, "gdelt").items[0].label == "reuters.com"


def test_a_malformed_gdelt_row_does_not_cost_the_whole_second_opinion():
    assert gdelt.parse_articles({"articles": [None, {"nope": 1}]}) == []
    assert gdelt.parse_articles({}) == []
    assert gdelt.parse_articles(None) == []


def test_gdelt_volume_reads_the_latest_point():
    assert gdelt.parse_volume(
        {"timeline": [{"data": [{"value": 1.5}, {"value": 4.25}]}]}) == 4.25
    assert gdelt.parse_volume({"timeline": []}) == 0.0
    assert gdelt.parse_volume({}) == 0.0


def test_gdelt_ships_off_and_says_it_was_never_verified_here():
    report = gdelt.report()
    assert report["enabled"] is False
    assert report["verified_from_this_machine"] is False


def test_gdelt_retrieval_returns_nothing_rather_than_raising_when_off():
    """A cross-check that could break an episode would be worse than no
    cross-check: the first retriever's packet is still real evidence."""
    import asyncio

    assert asyncio.run(gdelt.retrieve("anything")) == []
    assert asyncio.run(gdelt.retrieve("")) == []
