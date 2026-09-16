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


#: American football: `/games`, `scores.home.total`. The motivating case.
NFL_GAME = {
    "game": {"id": 9, "status": {"short": "Q3", "long": "Third Quarter"}},
    "teams": {"home": {"name": "Kansas City"}, "away": {"name": "Denver"}},
    "scores": {"home": {"total": 21}, "away": {"total": 7}},
}
#: Soccer: `/fixtures`, `goals`. A different host and a different shape.
SOCCER_FIXTURE = {
    "fixture": {"id": 3, "status": {"short": "2H", "elapsed": 67}},
    "teams": {"home": {"name": "Arsenal"}, "away": {"name": "Chelsea"}},
    "goals": {"home": 1, "away": 2},
}


# --------------------------------------------------------------------------
# sports: one API per sport, and they are not interchangeable
# --------------------------------------------------------------------------
@pytest.mark.parametrize("subject, expected", [
    ("the Chiefs game", "american-football"),
    ("who won the NFL game", "american-football"),
    ("the NBA finals", "basketball"),
    ("the Premier League match", "football"),
    ("an MLB game tonight", "baseball"),
])
def test_a_question_reaches_the_right_sport(subject, expected):
    """API-Sports is four separate APIs wearing one brand - different hosts,
    response shapes and status codes. Writing one adapter against the soccer
    host and calling it "sports" is how the Chiefs get looked up on a football
    endpoint, which is exactly the bug this routing exists to prevent."""
    assert ls.sport_for(subject).key == expected


def test_an_unnamed_sport_falls_to_the_configured_default():
    """"Chiefs game" names no sport. Team-name routing would need a
    maintained roster of every team in every league, and a stale one sends an
    NFL question to a soccer endpoint - worse than a default a deployment
    chose on purpose."""
    assert ls.sport_for("the Chiefs game").key == ls.settings.api_sports_sport
    assert ls.sport_for("").key == ls.settings.api_sports_sport


def test_each_sport_has_its_own_host_and_status_vocabulary():
    hosts = {s.host for s in ls.SPORTS.values()}
    assert len(hosts) == len(ls.SPORTS), "two sports share a host"
    assert ls.SPORTS["football"].path == "fixtures"
    assert ls.SPORTS["american-football"].path == "games"
    assert ls.SPORTS["american-football"].unit == "points"
    assert ls.SPORTS["football"].unit == "goals"


@pytest.mark.parametrize("sport_key, short, expected", [
    ("american-football", "NS", live_facts.SCHEDULED),
    ("american-football", "Q3", live_facts.IN_PROGRESS),
    ("american-football", "FT", live_facts.FINAL),
    ("american-football", "1H", live_facts.UNKNOWN),   # a soccer code
    ("football", "1H", live_facts.IN_PROGRESS),
    ("football", "AET", live_facts.FINAL),
    ("football", "Q3", live_facts.UNKNOWN),            # a gridiron code
    ("basketball", "Q4", live_facts.IN_PROGRESS),
    ("baseball", "IN7", live_facts.IN_PROGRESS),
    ("american-football", "WEIRD", live_facts.UNKNOWN),
    ("american-football", "", live_facts.UNKNOWN),
])
def test_status_codes_map_per_sport_or_become_unknown(sport_key, short, expected):
    """Each sport's vocabulary is its own, translated where it arrives. A code
    borrowed from another sport must NOT map - that is the cross-wiring this
    catches. And `unknown` is not "probably fine": it is the state in which no
    result may be spoken."""
    sport = ls.SPORTS[sport_key]
    row = {"game": {"id": 1, "status": {"short": short}},
           "teams": {"home": {"name": "A"}, "away": {"name": "B"}},
           "scores": {"home": {"total": 3}, "away": {"total": 1}}}
    facts = ls.ApiSportsSource().to_facts(row, entity(), sport)
    assert facts is not None
    assert facts.status == expected


def test_both_score_shapes_are_read():
    """`goals` for soccer, `scores.home.total` for everything else. One
    adapter reading only one of them silently returns no score for the other."""
    nfl = ls.ApiSportsSource().to_facts(
        NFL_GAME, entity(), ls.SPORTS["american-football"])
    soccer = ls.ApiSportsSource().to_facts(
        SOCCER_FIXTURE, entity(), ls.SPORTS["football"])
    assert "21 to 7" in nfl.facts[0]
    assert "2 to 1" in soccer.facts[0]
    assert "points" not in soccer.facts[0], "soccer does not score points"


def test_the_entity_id_carries_its_sport():
    """A bare game id is meaningless without knowing which API issued it, and
    `fetch` receives only the entity."""
    assert ls.ApiSportsSource()._game_id(NFL_GAME) == "9"
    assert ls.ApiSportsSource()._game_id(SOCCER_FIXTURE) == "3"


def test_a_game_in_progress_is_not_described_as_beaten():
    facts = ls.ApiSportsSource().to_facts(
        NFL_GAME, entity(), ls.SPORTS["american-football"])
    assert facts.status == live_facts.IN_PROGRESS
    assert "lead" in facts.facts[0]
    assert "beat" not in facts.facts[0]


def test_a_finished_game_may_say_beat():
    row = {**NFL_GAME, "game": {**NFL_GAME["game"], "status": {"short": "FT"}}}
    facts = ls.ApiSportsSource().to_facts(
        row, entity(), ls.SPORTS["american-football"])
    assert facts.status == live_facts.FINAL
    assert "beat" in facts.facts[0]


def test_facts_are_spoken_sentences_rather_than_scorelines():
    """These reach a voice. "KC 21-7 DEN 3Q 10:04" is not a sentence."""
    facts = ls.ApiSportsSource().to_facts(
        NFL_GAME, entity(), ls.SPORTS["american-football"])
    for line in facts.facts:
        assert line.endswith(".")
        assert "-" not in line


def test_a_row_with_no_score_yields_nothing_rather_than_a_blank_fact():
    row = {"game": {"id": 1, "status": {"short": "Q1"}},
           "teams": {"home": {"name": "A"}, "away": {"name": "B"}}, "scores": {}}
    assert ls.ApiSportsSource().to_facts(
        row, entity(), ls.SPORTS["american-football"]) is None


def test_sportsdataio_reads_its_own_nfl_shape():
    """A different vendor entirely - flat rows, PascalCase, its own statuses.
    Subclassing the API-Sports adapter for it was wrong."""
    row = {"GameKey": "1", "Status": "InProgress", "HomeTeam": "KC",
           "AwayTeam": "DEN", "HomeScore": 21, "AwayScore": 7,
           "Quarter": "3", "TimeRemaining": "10:04"}
    facts = ls.SportsDataIOSource().to_facts(row, entity())
    assert facts.status == live_facts.IN_PROGRESS
    assert "KC lead DEN 21 to 7." == facts.facts[0]
    assert "10:04" in facts.facts[1]


def test_sportsdataio_warns_that_a_free_key_returns_scrambled_data(monkeypatch):
    """A trap worth naming: a trial key looks like it works, and `verify`
    cannot tell the difference - so the warning has to travel with the
    configured state rather than waiting to be discovered."""
    import dataclasses

    import config

    monkeypatch.setattr(ls, "settings", dataclasses.replace(
        config.settings, sportsdataio_key="a-key"))
    ok, why = ls.SportsDataIOSource().diagnose()
    assert ok
    assert "scrambled" in why


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
