"""§267: EI writes each provider's input in that provider's own terms.

Names for the catalogues (Finnhub, API-Sports), a market-style title for
Polymarket, the named things first for GDELT, a source kind for Perplexity's
`search_mode` and Parallel's objective, and a town's country for both web
backends. Never an id or a ticker: each catalogue resolves the name. Nothing
here reaches the network.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dataclasses  # noqa: E402

import pytest  # noqa: E402

import config  # noqa: E402
import episode_intelligence as ei  # noqa: E402
import live_sources as ls  # noqa: E402
import research  # noqa: E402

REPLY = {"results": [{"url": "https://www.reuters.com/a", "title": "x",
                      "publish_date": "2026-10-11", "date": "2026-10-11",
                      "excerpts": ["Something long enough to be kept as evidence."],
                      "snippet": "Something long enough to be kept as evidence."}]}


@pytest.fixture
def wire(monkeypatch):
    """Every request either backend makes, answered with `REPLY`."""
    sent: list = []

    def post(url, headers, body):
        sent.append({"url": url, "body": body})
        return REPLY

    monkeypatch.setattr(research, "_post", post)
    monkeypatch.setenv("PARALLEL_API_KEY", "par-key")
    monkeypatch.setenv("PERPLEXITY_API_KEY", "ppx-key")
    import credentials

    credentials.reset()
    yield sent, None
    credentials.reset()


def use(monkeypatch, **changes):
    monkeypatch.setattr(research, "settings",
                        dataclasses.replace(config.settings, **changes))


def brief(**fields):
    base = dict(recency_days=3, subject="the Eagles", retrieval="Eagles Giants score",
                broader="Eagles season", must_establish=[], live_domain="")
    base.update(fields)
    return types.SimpleNamespace(**base)


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# The brief
# --------------------------------------------------------------------------
def test_the_schema_asks_for_every_new_field():
    for name in ("entities", "forecast_query", "source_kind"):
        assert name in ei.BRIEF_SCHEMA["properties"]
        assert name in ei.BRIEF_SCHEMA["required"]
    prompt = " ".join(ei.EI_SYSTEM.split()) + " ".join(
        ei.build_ei_prompt("who won", 2).split())
    assert "never a ticker, an id" in prompt
    assert "two-letter country code" in prompt


def test_entities_keep_names_and_drop_tickers():
    got = ei.clean_entities([
        {"name": "Meta Platforms", "kind": "company"},
        {"name": "META", "kind": "company"},          # a ticker, not a name
        {"name": "NFL", "kind": "league"},            # a league's own name
        {"name": "Philadelphia Eagles", "kind": "team"},
        {"name": "x", "kind": "weather"},             # not a kind
        {"name": "meta platforms", "kind": "company"},  # a repeat
        {"name": "US Federal Reserve", "kind": "organisation"},
        {"name": "Jalen Hurts", "kind": "person"},
    ])
    assert [e["name"] for e in got] == ["Meta Platforms", "NFL",
                                        "Philadelphia Eagles", "US Federal Reserve"]
    assert ei.clean_entities(None) == []


def test_a_degraded_brief_has_none_and_every_reader_falls_back():
    degraded = ei.fallback_brief("why did meta stock rise today", "test")
    assert degraded.entities == [] and degraded.forecast_query == ""
    assert degraded.source_kind == "general"
    assert ei.entity_names(degraded) == []


# --------------------------------------------------------------------------
# Finnhub and Polymarket
# --------------------------------------------------------------------------
def test_finnhub_looks_up_the_listed_name_not_the_event(monkeypatch):
    asked = []

    async def _json(url, headers, params, timeout):
        asked.append(params.get("q"))
        return {"result": [{"symbol": "META", "type": "Common Stock",
                            "description": "META PLATFORMS INC-CLASS A"}]}

    monkeypatch.setattr(ls, "_json", _json)
    question = types.SimpleNamespace(
        subject="Meta's stock rise on 13 October 2026",
        entities=[{"name": "Meta Platforms", "kind": "company"}])
    got = run(ls.FinnhubSource().resolve(question))
    assert asked == ["Meta Platforms"], "the event-shaped subject was searched"
    assert got.id == "META", "the symbol comes from Finnhub's own search"


def test_polymarket_is_searched_with_the_market_s_own_short_forms():
    question = types.SimpleNamespace(
        subject="the Federal Reserve's October interest rate decision",
        forecast_query="Fed decision October", entities=[])
    words = ls.market_words(question)
    assert words[:3] == ["fed", "decision", "october"], words
    plain = types.SimpleNamespace(subject="the Federal Reserve rate decision")
    assert "fed" not in ls.market_words(plain), "a subject keeps the old rule"


# --------------------------------------------------------------------------
# Parallel, Perplexity and GDELT
# --------------------------------------------------------------------------
def test_parallel_is_told_the_source_preference_and_the_town_s_country(
        wire, monkeypatch):
    sent, _ = wire
    use(monkeypatch, research_backend="parallel")
    run(research.retrieve("Meta earnings", brief=brief(source_kind="filings"),
                          country="us"))
    body = sent[0]["body"]
    assert "company's own filings" in body["objective"]
    assert body["advanced_settings"]["location"] == "US"


def test_perplexity_gets_its_sec_mode_and_no_dead_objective(wire, monkeypatch):
    sent, _ = wire
    use(monkeypatch, research_backend="perplexity")
    run(research.retrieve("Meta 10-Q", brief=brief(source_kind="filings")))
    body = sent[0]["body"]
    assert body["search_mode"] == "sec"
    assert "objective" not in body
    sent.clear()
    run(research.retrieve("Eagles Giants score", brief=brief(source_kind="news")))
    assert "search_mode" not in sent[0]["body"], "news is the default web search"


def test_a_country_is_only_ever_an_iso_code():
    assert research.hints_for(None, "United States") == {"source_kind": "general"}
    assert research.hints_for(None, "gb")["country"] == "GB"


def test_gdelt_is_searched_with_the_names_first():
    question = types.SimpleNamespace(entities=[
        {"name": "US Federal Reserve", "kind": "organisation"},
        {"name": "Jerome Powell", "kind": "person"}])
    got = research.gdelt_query("interest rate decision vote split october", question)
    assert got.startswith("US Federal Reserve Jerome Powell interest rate")
    assert research.gdelt_query("x y", None) == "x y"
