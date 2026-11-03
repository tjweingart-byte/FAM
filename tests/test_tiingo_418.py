"""§418: Tiingo is the only markets provider; API-Sports and Finnhub are gone.

Tiingo's tests came from §290's file, which covered the free plans used
while developing. Every request goes through `live_sources._json`, stubbed
with rows shaped as Tiingo's documentation shapes them. No network.
"""
from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import live_facts  # noqa: E402
import live_sources  # noqa: E402
import provider_usage  # noqa: E402
import story_sources  # noqa: E402
import tiingo  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULES = (live_sources, story_sources, tiingo)


def use(monkeypatch, **changes):
    patched = dataclasses.replace(config.settings, **changes)
    for module in MODULES:
        monkeypatch.setattr(module, "settings", patched)
    return patched


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(tiingo, "RATION", tiingo.Ration())
    monkeypatch.setattr(live_sources, "QUOTES", {})


@pytest.fixture
def wire(monkeypatch):
    sent: list = []
    routes: dict = {}

    async def fake_json(url, headers, params, timeout):
        sent.append((url, dict(params), dict(headers)))
        for prefix, answer in routes.items():
            if url.startswith(prefix):
                return answer(params) if callable(answer) else answer
        return []

    monkeypatch.setattr(live_sources, "_json", fake_json)
    return SimpleNamespace(sent=sent, routes=routes)


# --------------------------------------------------------------------------
# Tiingo
# --------------------------------------------------------------------------
IEX = "https://api.tiingo.com/iex/"
SEARCH = "https://api.tiingo.com/tiingo/utilities/search"


def iex_row(ticker="AAPL", last=230.0, prev=225.0, at=None):
    at = at or datetime.now(timezone.utc) - timedelta(minutes=2)
    return {"ticker": ticker, "last": last, "tngoLast": last, "prevClose": prev,
            "lastSaleTimestamp": at.isoformat(), "timestamp": at.isoformat()}


def test_a_quote_in_the_shared_shape():
    quote = tiingo.to_quote(iex_row())
    assert quote["c"] == 230.0 and quote["pc"] == 225.0
    assert round(quote["dp"], 2) == 2.22
    assert tiingo.to_quote({"ticker": "X", "last": None}) is None


def test_the_session_is_computed():
    def at(text):
        return datetime.fromisoformat(text).replace(tzinfo=tiingo.EASTERN).timestamp()

    saturday = at("2026-10-17T12:00:00")
    assert tiingo.session(saturday, saturday - 60) is False
    tuesday = at("2026-10-13T11:00:00")
    assert tiingo.session(tuesday, tuesday - 60) is True
    # Inside the hours with no recent sale: a holiday or an early close.
    assert tiingo.session(tuesday, tuesday - 3 * 3600) is None
    assert tiingo.session(at("2026-10-13T17:00:00"), None) is False


def test_tiingo_resolves_by_its_own_search(monkeypatch, wire):
    use(monkeypatch, tiingo_api_key="t")
    wire.routes[SEARCH] = [
        {"ticker": "AAPL.MX", "name": "Apple", "assetType": "Stock", "countryCode": "MX"},
        {"ticker": "AAPL", "name": "Apple Inc", "assetType": "Stock",
         "isActive": True, "countryCode": "US"}]
    brief = SimpleNamespace(live_entity="Apple", entity="Apple", query="apple stock")
    entity = run(tiingo.TiingoSource().resolve(brief))
    assert entity.id == "AAPL"
    _, params, headers = wire.sent[0]
    assert headers == {"Authorization": "Token t"} and "token" not in params


def test_tiingo_reads_a_named_ticker(monkeypatch, wire):
    use(monkeypatch, tiingo_api_key="t")
    wire.routes[SEARCH] = lambda params: [
        {"ticker": "ORCLX", "name": "Oracle fund", "assetType": "Mutual Fund", "countryCode": "US"},
        {"ticker": "ORCL", "name": "Oracle Corp", "assetType": "Stock",
         "isActive": True, "countryCode": "US"}] if params["query"] == "ORCL" else []
    brief = SimpleNamespace(live_entity="Oracle Corporation (NYSE: ORCL)",
                            query="oracle stock")
    entity = run(tiingo.TiingoSource().resolve(brief))
    assert entity.id == "ORCL"
    assert wire.sent[0][1] == {"query": "ORCL"}


def test_tiingo_fetch_says_the_price(monkeypatch, wire):
    use(monkeypatch, tiingo_api_key="t")
    wire.routes[IEX] = [iex_row()]
    entity = live_facts.Entity(domain="markets", provider="Tiingo", id="AAPL", label="Apple")
    facts = run(tiingo.TiingoSource().fetch(entity))
    assert facts.source == "Tiingo"
    assert any("230.00" in s for s in facts.facts)
    assert any("up about 2.2 percent" in s for s in facts.facts)


def test_the_ration_stops_before_the_plan(monkeypatch, wire):
    use(monkeypatch, tiingo_api_key="t", tiingo_hourly_requests=1)
    wire.routes[IEX] = [iex_row()]
    run(tiingo.iex(["AAPL"]))
    with pytest.raises(tiingo.Rationed):
        run(tiingo.iex(["AAPL"]))


def test_the_key_puts_markets_on_tiingo(monkeypatch):
    use(monkeypatch, tiingo_api_key="t", live_markets_provider="")
    assert live_sources.configured()["markets"] == "tiingo"
    use(monkeypatch, tiingo_api_key="", live_markets_provider="")
    assert live_sources.configured()["markets"] == ""


def test_the_sweep_is_one_request_and_only_on_tiingo(monkeypatch, wire):
    use(monkeypatch, tiingo_api_key="t", live_markets_provider="",
        stories_market_move_percent=1.0)
    wire.routes[IEX] = [iex_row("AAPL"), iex_row("NVDA", last=110.0, prev=100.0)]
    out = run(story_sources.TiingoSignals().collect(5))
    assert len(wire.sent) == 1
    assert [s.subject for s in out] == ["Nvidia", "Apple"]
    assert live_sources.swept_quote("NVDA")["data"]["c"] == 110.0
    use(monkeypatch, tiingo_api_key="", live_markets_provider="")
    ok, why = story_sources.TiingoSignals().diagnose()
    assert not ok and "TIINGO_API_KEY" in why


def test_the_watchlist_setting(monkeypatch):
    use(monkeypatch, markets_watchlist="aapl, tsla")
    assert story_sources.TiingoSignals().watchlist() == ["AAPL", "TSLA"]
    use(monkeypatch, markets_watchlist="")
    assert story_sources.TiingoSignals().watchlist() == list(story_sources.WATCHLIST)


# --------------------------------------------------------------------------
# FAM's stops: the free plan only (§421)
# --------------------------------------------------------------------------
def test_the_free_plan_stops_below_its_limits(monkeypatch):
    use(monkeypatch, tiingo_plan="free", tiingo_hourly_requests=0, tiingo_daily_requests=0)
    assert (tiingo.hourly_limit(), tiingo.daily_limit()) == (45, 950)


@pytest.mark.parametrize("plan", ["power", "commercial", "redistribution"])
def test_a_paid_plan_has_no_stop_in_code(monkeypatch, wire, plan):
    use(monkeypatch, tiingo_api_key="t", tiingo_plan=plan,
        tiingo_hourly_requests=0, tiingo_daily_requests=0)
    assert (tiingo.hourly_limit(), tiingo.daily_limit()) == (0, 0)
    wire.routes[IEX] = [iex_row()]
    for _ in range(200):
        run(tiingo.iex(["AAPL"]))
    assert tiingo.RATION.report()["hour"] == 200, "still counted for /admin"
    assert "no FAM stop" in provider_usage._plans()["tiingo"][0]


def test_a_stop_set_by_hand_wins(monkeypatch):
    use(monkeypatch, tiingo_plan="power", tiingo_hourly_requests=500,
        tiingo_daily_requests=4000)
    assert (tiingo.hourly_limit(), tiingo.daily_limit()) == (500, 4000)


def test_an_unknown_plan_is_refused(monkeypatch):
    monkeypatch.setenv("TIINGO_PLAN", "gold")
    with pytest.raises(ValueError, match="TIINGO_PLAN"):
        config.Settings()


# --------------------------------------------------------------------------
# The licence: only a redistribution licence lets listeners hear a price
# --------------------------------------------------------------------------
@pytest.mark.parametrize("plan, ok", [("free", False), ("power", False),
                                      ("commercial", False), ("redistribution", True)])
def test_only_redistribution_is_commercial(monkeypatch, plan, ok):
    patched = dataclasses.replace(config.settings, tiingo_api_key="t", tiingo_plan=plan)
    monkeypatch.setattr(config, "settings", patched)
    report = provider_usage.licences()
    assert report["providers"]["tiingo"]["commercial"] is ok
    assert ("tiingo" in report["non_commercial_in_use"]) is (not ok)


def test_requests_are_counted_against_tiingo():
    assert provider_usage.provider_for_host("api.tiingo.com") == "tiingo"


def test_staging_scrubs_the_key():
    import spend_guard

    assert "TIINGO_API_KEY" in set(spend_guard.PAID_CREDENTIALS)


# --------------------------------------------------------------------------
# API-Sports and Finnhub are gone
# --------------------------------------------------------------------------
def test_api_sports_and_finnhub_are_gone():
    assert importlib.util.find_spec("api_sports") is None
    assert "finnhub" not in live_sources.BUILDERS["markets"]
    assert not hasattr(live_sources, "FinnhubSource")
    assert "finnhub" not in story_sources.BUILDERS
    assert not hasattr(story_sources, "FinnhubSignals")
    assert "api_sports" not in provider_usage.PROVIDERS
    assert "finnhub" not in provider_usage.PROVIDERS
    for name in ("finnhub_key", "finnhub_plan", "finnhub_watchlist",
                 "api_sports_key", "api_sports_leagues"):
        assert not hasattr(config.settings, name), name
    with open(os.path.join(ROOT, ".env.example")) as handle:
        example = handle.read()
    assert "FINNHUB_" not in example and "API_SPORTS_" not in example
