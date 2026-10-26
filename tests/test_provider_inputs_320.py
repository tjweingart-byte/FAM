"""§320: EI writes each provider's input in that provider's own terms.

A market-style title for Polymarket, a source kind for Perplexity's `search_mode` and Parallel's objective, and a
town's country for both web backends. The names the catalogues resolve were
already EI's `live_entity` (§265). Nothing here reaches the network.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import episode_intelligence as ei  # noqa: E402
import live_sources as ls  # noqa: E402
import research  # noqa: E402
from test_search_backends_258 import (  # noqa: E402,F401
    _fixed_dates_stay_inside_the_window, brief, use, wire)


def run(coro):
    return asyncio.run(coro)


def test_the_schema_asks_the_search_half_for_both_fields():
    for name in ("forecast_query", "source_kind"):
        assert name in ei.BRIEF_SCHEMA["properties"]
        assert name in ei.BRIEF_SCHEMA["required"]
        assert name in ei.PARTS["search"]
        assert name in ei.SEARCH_SCHEMA["properties"]
    prompt = " ".join(ei.build_ei_prompt("who won", 2).split())
    assert "as a prediction market would title it" in prompt
    assert "two-letter country code" in prompt


def test_the_search_half_s_fields_are_cleaned():
    got = ei._search_fields({"forecast_query": "  Fed   decision October ",
                             "source_kind": "filings"}, "q")
    assert got["forecast_query"] == "Fed decision October"
    assert got["source_kind"] == "filings"
    assert ei._search_fields({"source_kind": "rumours"}, "q")["source_kind"] == "general"


def test_a_degraded_brief_has_neither_and_every_reader_falls_back():
    degraded = ei.fallback_brief("why did meta stock rise today", "test")
    assert degraded.forecast_query == "" and degraded.source_kind == "general"
    assert research.hints_for(degraded) == {"source_kind": "general"}


def test_polymarket_is_searched_with_the_market_s_own_short_forms():
    question = types.SimpleNamespace(
        subject="the Federal Reserve's October interest rate decision",
        live_entity="", forecast_query="Fed decision October")
    words = ls.market_words(question)
    assert words[:3] == ["fed", "decision", "october"], words
    plain = types.SimpleNamespace(subject="the Federal Reserve rate decision",
                                  live_entity="")
    assert "fed" not in ls.market_words(plain), "a subject keeps the old rule"
    assert ls.market_words(plain) == ls.subject_words(plain.subject)


def test_parallel_is_told_the_source_preference_and_the_town_s_country(
        wire, monkeypatch):
    sent, _ = wire
    use(monkeypatch, research_backend="parallel")
    run(research.retrieve("Meta earnings", brief=brief(source_kind="filings"),
                          country="us"))
    body = sent[0]["body"]
    assert "company's own filings" in body["objective"]
    assert body["advanced_settings"]["location"] == "US"


def test_no_country_no_location(wire, monkeypatch):
    sent, _ = wire
    use(monkeypatch, research_backend="parallel")
    run(research.retrieve("Eagles Giants score", brief=brief()))
    assert "location" not in sent[0]["body"]["advanced_settings"]


def test_perplexity_gets_its_sec_mode_and_its_country(wire, monkeypatch):
    sent, _ = wire
    use(monkeypatch, research_backend="perplexity")
    run(research.retrieve("Meta 10-Q", brief=brief(source_kind="filings"),
                          country="GB"))
    body = sent[0]["body"]
    assert body["search_mode"] == "sec"
    assert body["country"] == "GB"
    assert "objective" not in body
    sent.clear()
    run(research.retrieve("Eagles Giants score", brief=brief(source_kind="news")))
    assert "search_mode" not in sent[0]["body"], "news is the default web search"


def test_a_country_is_only_ever_an_iso_code():
    assert research.hints_for(None, "United States") == {"source_kind": "general"}
    assert research.hints_for(None, "gb")["country"] == "GB"

