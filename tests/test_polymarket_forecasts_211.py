"""§211: Polymarket asked for every question that turns on an outcome.

It was registered, healthy and keyless, and the admin page counted zero
requests. Two reasons in the code: an episode asked it only when EI labelled
the question `elections`, and even then it scanned the twenty most-traded
markets for one shared word, which matched almost nothing. So:

1. **Every outcome-dependent question gets a forecast** (`live_facts.forecast`)
   - a Fed decision, a final, a war, a ruling - beside the live lookup and
   never in its place. "Forecast, never a result" is unchanged: the facts are
   `unknown`, the block says the articles win, and a forecast alone is never
   evidence enough to write a current-dependent episode.
2. **It searches by the question's subject** (`/public-search`), and needs two
   shared words when the subject has two.
3. **A market tile reaches Made for you only when it names something the
   listener follows** - the bar every live story meets (§155).
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import sys
import time
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import live_facts  # noqa: E402
import live_sources  # noqa: E402
import script_generator as sg  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def brief(**overrides):
    base = dict(subject="the Fed's December rate decision", query="will the fed cut",
                outcome_dependent=True, live_domain="", place="", intent="")
    base.update(overrides)
    return types.SimpleNamespace(**base)


FED_EVENT = {
    "id": "123", "title": "Fed decision in December?", "volume": "48210000",
    "markets": [
        {"question": "Fed decreases rates by 25 bps after December meeting?",
         "groupItemTitle": "25 bps decrease", "outcomes": '["Yes", "No"]',
         "outcomePrices": '["0.71", "0.29"]', "closed": False, "active": True},
        {"question": "No change in Fed rates after December meeting?",
         "groupItemTitle": "No change", "outcomes": '["Yes", "No"]',
         "outcomePrices": '["0.24", "0.76"]', "closed": False, "active": True},
        {"question": "Fed decreases rates by 50+ bps after December meeting?",
         "groupItemTitle": "50+ bps decrease", "outcomes": '["Yes", "No"]',
         "outcomePrices": '["0.04", "0.96"]', "closed": False, "active": True},
        {"question": "Fed increases rates after December meeting?",
         "groupItemTitle": "Increase", "outcomes": '["Yes", "No"]',
         "outcomePrices": '["0.01", "0.99"]', "closed": False, "active": True},
    ],
}
SUPER_BOWL_EVENT = {
    "id": "9", "title": "Super Bowl Champion 2027", "volume": "900000",
    "markets": [{"question": "Will the Philadelphia Eagles win Super Bowl 2027?",
                 "outcomes": '["Yes", "No"]', "outcomePrices": '["0.12", "0.88"]'}],
}


@pytest.fixture
def gamma(monkeypatch):
    """Polymarket's Gamma API, recorded. `asked` is every (path, params)."""
    asked: list = []
    answers = {"/public-search": {"events": [SUPER_BOWL_EVENT, FED_EVENT]},
               "/events/123": FED_EVENT}

    async def fake_json(url, headers, params, timeout):
        path = url.split("polymarket.example", 1)[-1]
        asked.append((path, dict(params)))
        answer = answers.get(path)
        if isinstance(answer, Exception):
            raise answer
        return answer if answer is not None else []

    patched = dataclasses.replace(live_sources.settings,
                                  polymarket_base="https://polymarket.example",
                                  live_facts=True)
    monkeypatch.setattr(live_sources, "settings", patched)
    monkeypatch.setattr(live_facts, "settings", patched)
    monkeypatch.setattr(live_sources, "_json", fake_json)
    live_facts.clear_caches()
    source = live_sources.PolymarketSource()
    live_facts.register(source)
    yield types.SimpleNamespace(asked=asked, answers=answers)
    live_facts.unregister(source.name)
    live_facts.clear_caches()


# --------------------------------------------------------------------------
# 1. Every outcome-dependent question, not only elections
# --------------------------------------------------------------------------
@pytest.mark.parametrize("overrides, wanted", [
    ({}, True),                                            # a Fed decision
    ({"live_domain": "sports", "subject": "the Super Bowl"}, True),
    ({"live_domain": "markets"}, True),
    ({"outcome_dependent": False}, False),                 # how something works
    ({"live_domain": "elections"}, False),                 # already its live lookup
    ({"live_domain": "weather"}, False),                   # weather's own forecaster
    ({"place": "San Anselmo, California, US"}, False),     # one town's news
])
def test_which_questions_get_a_forecast(overrides, wanted):
    assert live_facts.wants_forecast(brief(**overrides)) is wanted


def test_a_fed_question_gets_the_markets_forecast(gamma):
    result = run(live_facts.forecast(brief()))
    assert result is not None and result.outcome == live_facts.FACTS
    facts = result.facts
    assert facts.status == live_facts.UNKNOWN
    assert facts.kind == live_facts.PREDICTION_MARKET
    line = facts.facts[0]
    # The likeliest three outcomes, likeliest first, as a person says them.
    assert "25 bps decrease: around 71 percent" in line
    assert line.index("71 percent") < line.index("24 percent") < line.index("4 percent")
    assert "Increase" not in line, "three outcomes, never the whole board"
    assert any("not a reported result" in f for f in facts.facts)


def test_a_question_that_does_not_turn_on_an_outcome_asks_nobody(gamma):
    assert run(live_facts.forecast(brief(outcome_dependent=False))) is None
    assert gamma.asked == []


def test_no_forecaster_configured_is_no_forecast_and_no_error():
    assert run(live_facts.forecast(brief())) is None


def test_the_forecast_never_takes_the_live_slot(gamma, monkeypatch):
    """A sports final keeps its scoreboard in `live`; the market is a second
    block after it, and the block says the articles win."""
    scoreboard = live_facts.LiveLookup("sports", live_facts.NOT_CONFIGURED,
                                       detail="no sports provider")

    async def lookup(b, notes=None):
        return scoreboard

    async def research(self, plan, notes=None):
        return dataclasses.replace(plan, evidence="SOURCE 1\nTitle: x")

    monkeypatch.setattr(live_facts, "lookup", lookup)
    monkeypatch.setattr(sg.ScriptGenerator, "research", research)
    monkeypatch.setattr(sg.ScriptGenerator, "understand",
                        lambda self, plan, notes=None: _done(plan))
    plan = dataclasses.replace(sg.plan_episode("will the fed cut in december", 3),
                               brief=brief())
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    notes = sg.ScriptNotes()
    prepared = run(generator.prepare(plan, notes))
    assert prepared.live is scoreboard
    assert prepared.forecast is not None
    prompt = sg.build_prompt(prepared)
    assert "THE ARTICLES WIN" in prompt and "25 bps decrease" in prompt
    labels = {(a.label, a.tier) for a in notes.provenance.items}
    assert ("Polymarket", "prediction market - a forecast, not a result") in labels


async def _done(plan):
    return plan


def test_a_forecast_alone_is_never_evidence_enough(gamma):
    """A current-dependent question with no articles and no live state is
    refused (§109) even when a market has a price: a price is what people
    expect, not what happened."""
    plan = dataclasses.replace(sg.plan_episode("who won the fed vote", 3),
                               brief=brief(), evidence="",
                               forecast=run(live_facts.forecast(brief())))
    assert plan.forecast is not None
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    with pytest.raises(Exception) as caught:
        generator._refuse_without_evidence(plan)
    assert type(caught.value).__name__ == "NoEvidence"


# --------------------------------------------------------------------------
# 2. Searched by subject, not a scan of the busiest twenty
# --------------------------------------------------------------------------
def test_the_market_is_found_by_searching_for_the_subject(gamma):
    entity = run(live_sources.PolymarketSource().resolve(brief()))
    assert entity.id == "event:123" and entity.label == "Fed decision in December?"
    path, params = gamma.asked[0]
    assert path == "/public-search"
    assert "december" in params["q"] and "rate" in params["q"]
    assert params["events_status"] == "active"


def test_one_shared_word_is_a_namesake_not_a_match(gamma):
    """"Eagles" alone would match the Super Bowl market for a question about
    an Eagles injury, and the wrong Eagles besides."""
    gamma.answers["/public-search"] = {"events": [SUPER_BOWL_EVENT]}
    found = run(live_sources.PolymarketSource().resolve(
        brief(subject="Eagles injury report")))
    assert found is None
    found = run(live_sources.PolymarketSource().resolve(
        brief(subject="Philadelphia Eagles Super Bowl chances")))
    assert found is not None and found.id == "event:9"


def test_a_host_without_search_still_scans(gamma):
    gamma.answers["/public-search"] = live_sources.ProviderHTTPError(
        404, "polymarket.example/public-search")
    gamma.answers["/markets"] = [
        {"id": "m1", "question": "Will the Fed cut rates in December?",
         "volume": "100"},
        {"id": "m2", "question": "Will it rain in Paris?", "volume": "900"}]
    entity = run(live_sources.PolymarketSource().resolve(
        brief(subject="Fed December rate cut")))
    assert entity is not None and entity.id == "m1"
    assert [p for p, _ in gamma.asked] == ["/public-search", "/markets"]


def test_gammas_json_string_prices_are_read():
    row = {"outcomes": '["Yes", "No"]', "outcomePrices": '["0.37", "0.63"]'}
    assert live_sources.market_price(row) == 0.37
    row = {"outcomes": '["No", "Yes"]', "outcomePrices": '["0.8", "0.2"]'}
    assert live_sources.market_price(row) == 0.2, "YES, wherever it is listed"


def test_a_head_to_head_market_names_both_sides():
    event = {"title": "Chiefs vs Bills", "markets": [
        {"question": "Chiefs vs Bills", "outcomes": '["Chiefs", "Bills"]',
         "outcomePrices": '["0.44", "0.56"]'}]}
    entity = live_facts.Entity(domain="elections", provider="Polymarket",
                               id="event:1", label="Chiefs vs Bills")
    line = live_sources.PolymarketSource().to_facts(event, entity).facts[0]
    assert "Bills around 56 percent, Chiefs around 44 percent" in line


# --------------------------------------------------------------------------
# 3. Made for you: a market tile only when it names something followed
# --------------------------------------------------------------------------
import categories as C  # noqa: E402
import stories  # noqa: E402
import story_sources  # noqa: E402
import topics as T  # noqa: E402


@pytest.fixture()
def log(tmp_path, monkeypatch):
    store = C.CategoryStore(str(tmp_path / "categories.db"))
    monkeypatch.setattr(T, "_CATEGORIES", store)
    C.apply_seed(store)
    T.reset_topic_tags()
    monkeypatch.setattr(T, "world_inventory", lambda *a, **k: ([], []))
    stories.seed([])
    yield T.EventStore(str(tmp_path / "events.db"))
    stories.seed([])
    T.reset_topic_tags()


def market(question):
    return stories.Story(
        subject=question, title=question, angle="what would settle it",
        query=f"what would decide whether {question.lower().rstrip('?')}",
        domain=stories.PREDICTION, source="Polymarket",
        tags=story_sources._tags(question, "world"), strength=0.9,
        first_seen=time.time(),
        shelf_life=stories.DOMAIN_SHELF_LIFE[stories.PREDICTION])


def listened(log, text):
    now = time.time()
    tags = T.tags_for_text(text)
    log.record(T.Event("me", "search", "", text, tags, at=now - 500))
    log.record(T.Event("me", "complete", "", text, tags, at=now - 400))


def made_for_you(log):
    feed = T.build_feed(log, "me", has_account=True)
    return [t["title"] for s in feed["sections"] if s["key"] == "from_history"
            for t in s["topics"]]


def test_a_market_naming_what_they_follow_is_offered(log):
    listened(log, "philadelphia eagles playoff chances")
    listened(log, "eagles quarterback injury update")
    stories.seed([market("Will the Philadelphia Eagles win Super Bowl 2027?")])
    assert "Will the Philadelphia Eagles win Super Bowl 2027?" in made_for_you(log)


def test_a_market_on_a_subject_they_never_went_near_is_not(log):
    """A field is not a subject: having listened to world news does not buy
    every betting question about the world."""
    listened(log, "what is happening in world news this week")
    listened(log, "world news headlines explained")
    stories.seed([market("Will Kazakhstan hold a snap election before July?")])
    made = made_for_you(log)
    assert "Will Kazakhstan hold a snap election before July?" not in made
    assert made, "the evergreen inventory still fills the rail"


def test_a_slow_forecast_never_holds_the_first_word(gamma, monkeypatch):
    """Review fix: the forecast was gathered with research, so a slow
    Polymarket held the episode up to the live timeout after the evidence was
    in. It now gets `FORECAST_GRACE_SECONDS` past the evidence and no more."""
    async def slow_forecast(b, notes=None):
        await asyncio.sleep(5)
        return None

    async def lookup(b, notes=None):
        return None

    async def research(self, plan, notes=None):
        return dataclasses.replace(plan, evidence="SOURCE 1\nTitle: x")

    monkeypatch.setattr(live_facts, "forecast", slow_forecast)
    monkeypatch.setattr(live_facts, "lookup", lookup)
    monkeypatch.setattr(sg.ScriptGenerator, "research", research)
    monkeypatch.setattr(sg.ScriptGenerator, "understand",
                        lambda self, plan, notes=None: _done(plan))
    plan = dataclasses.replace(sg.plan_episode("will the fed cut in december", 3),
                               brief=brief())
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    began = time.monotonic()
    prepared = run(generator.prepare(plan, sg.ScriptNotes()))
    assert time.monotonic() - began < sg.FORECAST_GRACE_SECONDS + 1.0
    assert prepared.forecast is None and prepared.evidence
