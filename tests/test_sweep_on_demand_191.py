"""§191: the outside services are asked when somebody is looking and there
is something to see - not on a timer all day.

At the owner's direction, after the admin page's first day of counts
(1,392 Finnhub quotes, 172 hockey requests, 86 volleyball with no volleyball
on any tile):

* API-Sports sweeps only the leagues the owner named (`SWEPT_LEAGUES`); every
  other league, and volleyball, rugby and AFL entirely, is looked up on demand
  by the episode that asks.
* A followed sport's card is read once a day, then again only while a
  followed game is on or about to start - and only if somebody drew myFAM
  recently.
* Finnhub is asked every two hours, round the clock.
* A team catalogue the provider refused is not asked again that day.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import live_sources  # noqa: E402
import stories  # noqa: E402
import story_sources  # noqa: E402

LS = live_sources
NFL = {"name": "NFL", "country": {"name": "USA"}}


def run(coro):
    return asyncio.run(coro)


def game(home, away, short, kickoff, league=NFL):
    return {"teams": {"home": {"name": home}, "away": {"name": away}},
            "league": league,
            "game": {"status": {"short": short},
                     "date": {"timestamp": int(kickoff)}},
            "scores": {"home": {"total": 7}, "away": {"total": 3}}}


#: Noon UTC on a fixed day, so no test straddles a UTC midnight by accident.
NOON = datetime(2026, 10, 1, 12, tzinfo=timezone.utc).timestamp()


class _Clock:
    @staticmethod
    def time():
        return NOON


@pytest.fixture()
def provider(monkeypatch):
    """API-Sports answering whatever `provider.card` holds, counting calls."""
    calls = []
    monkeypatch.setattr(story_sources, "time", _Clock)

    class Provider:
        card: list = []

    async def _json(url, headers, params, timeout):
        calls.append((url, dict(params)))
        if url.endswith("/teams"):
            return {"response": []}
        return {"response": list(Provider.card)}

    monkeypatch.setattr(LS, "_json", _json)
    monkeypatch.setattr(LS, "CARD", {})
    patched = dataclasses.replace(config.settings, api_sports_key="k",
                                  stories_sports="american-football")
    monkeypatch.setattr(LS, "settings", patched)
    monkeypatch.setattr(story_sources, "settings", patched)
    monkeypatch.setattr(stories, "settings", patched)
    Provider.calls = calls
    return Provider


def card_requests(calls):
    return [c for c in calls if not c[0].endswith("/teams")]


# --------------------------------------------------------------------------
# Which leagues are followed
# --------------------------------------------------------------------------
def test_the_followed_leagues_are_the_owner_s_list():
    f = story_sources.followed
    sport = LS.SPORTS

    def row(name, country=""):
        return {"league": {"name": name, "country": {"name": country}}}

    assert f(sport["american-football"], row("NFL", "USA"))
    assert f(sport["american-football"], row("NCAA", "USA"))
    assert not f(sport["american-football"], row("UFL", "USA"))
    assert f(sport["basketball"], row("WNBA", "USA"))
    assert not f(sport["basketball"], row("Euroleague", "World"))
    assert f(sport["baseball"], row("MLB", "USA"))
    assert f(sport["hockey"], row("NHL", "USA"))
    assert not f(sport["hockey"], row("KHL", "Russia"))
    assert f(sport["football"], row("Premier League", "England"))
    assert not f(sport["football"], row("Premier League", "Egypt"))
    assert f(sport["football"], row("Bundesliga", "Germany"))
    assert not f(sport["football"], row("Bundesliga", "Austria"))
    assert f(sport["football"], row("UEFA Champions League", "World"))
    assert not f(sport["football"], row("Serie A", "Italy"))
    assert f(sport["formula-1"], {"type": "Race"})
    assert not f(sport["formula-1"], {"type": "1st Practice"})
    assert f(sport["mma"], {"slug": "UFC 320: Ankalaev vs Pereira 2"})
    assert not f(sport["mma"], {"slug": "UFC Fight Night: Oliveira vs Gamrot"})
    assert not f(sport["mma"], {"slug": "PFL 7"})
    for niche in ("volleyball", "rugby", "afl"):
        assert niche not in story_sources.SWEPT_LEAGUES
        assert not f(sport[niche], row("Anything"))


def test_a_niche_sport_is_never_swept_even_when_listed(provider, monkeypatch):
    patched = dataclasses.replace(story_sources.settings,
                                  stories_sports="volleyball,rugby,afl")
    monkeypatch.setattr(story_sources, "settings", patched)
    assert run(story_sources.ApiSportsSignals().collect(8)) == []
    assert provider.calls == []
    ok, why = story_sources.ApiSportsSignals().diagnose()
    assert not ok and "on demand" in why


# --------------------------------------------------------------------------
# Only when there is something to watch
# --------------------------------------------------------------------------
def test_a_finished_day_is_read_once_and_not_again(provider):
    now = NOON
    provider.card = [game("Jets", "Bills", "FT", now - 4 * 3600)]
    source = story_sources.ApiSportsSignals()
    run(source.collect(8))
    assert len(card_requests(provider.calls)) == 1
    # Hours later, every followed game is still final: nothing to ask.
    assert source.dates_due("american-football", now + 3 * 3600) == []


def test_a_game_hours_away_waits_and_one_about_to_start_is_watched(provider):
    now = NOON
    kick = now + 5 * 3600
    provider.card = [game("Eagles", "Giants", "NS", kick)]
    source = story_sources.ApiSportsSignals()
    run(source.collect(8))
    key = "american-football"
    assert source.dates_due(key, now + 3600) == []
    # Inside the lead before kick-off, and an interval since the last sweep.
    soon = kick - story_sources.WATCH_LEAD_SECONDS + 60
    assert source.dates_due(key, soon) == [source._days(soon)[0]]


def test_a_game_under_way_is_swept_on_the_interval_not_faster(provider):
    now = NOON
    provider.card = [game("Chiefs", "Broncos", "Q2", now - 3600)]
    source = story_sources.ApiSportsSignals()
    run(source.collect(8))
    key = "american-football"
    gap = story_sources.settings.stories_sports_interval_seconds
    assert source.dates_due(key, now + 60) == []
    assert source.dates_due(key, now + gap) != []


def test_last_night_s_late_game_is_followed_past_midnight(provider):
    source = story_sources.ApiSportsSignals()
    now = NOON
    today, yesterday = source._days(now)
    story_sources.SPORT_CARDS["american-football"] = {
        today: [],
        yesterday: [game("Rams", "Seahawks", "Q4", now - 2 * 3600)],
    }
    assert source.dates_due("american-football", now) == [yesterday]


def test_a_regional_game_under_way_does_not_keep_the_sweep_awake(provider):
    now = NOON
    provider.card = [game("Stallions", "Renegades", "Q2", now - 3600,
                          league={"name": "UFL", "country": {"name": "USA"}})]
    source = story_sources.ApiSportsSignals()
    assert run(source.collect(8)) == []
    gap = story_sources.settings.stories_sports_interval_seconds
    assert source.dates_due("american-football", now + 2 * gap) == []
    # The episode lookup still reads the whole card before spending.
    assert LS.card_rows("american-football", 3600)


# --------------------------------------------------------------------------
# Only on demand
# --------------------------------------------------------------------------
def test_nobody_looking_means_no_sweep(provider):
    stories.reset()
    source = story_sources.ApiSportsSignals()
    assert "on demand" in source.idle(1_000_000.0)
    assert stories.note_demand(1_000_000.0) is True
    assert source.idle(1_000_000.0) == ""
    # A second look inside the window does not wake anything again.
    assert stories.note_demand(1_000_100.0) is False
    window = story_sources.settings.stories_demand_seconds
    assert stories.note_demand(1_000_100.0 + window + 1) is True
    stories.reset()


def test_an_idle_source_is_skipped_and_said(provider, monkeypatch):
    stories.reset()
    stories.register(story_sources.ApiSportsSignals())
    signals, reports = run(stories.collect(now=1_000_000.0))
    assert signals == [] and provider.calls == []
    assert reports[0].outcome == stories.SKIPPED
    assert "on demand" in reports[0].detail
    stories.reset()


# --------------------------------------------------------------------------
# Finnhub and the refused catalogue
# --------------------------------------------------------------------------
def test_finnhub_is_asked_every_two_hours():
    assert config.settings.stories_markets_interval_seconds == 7200.0
    assert story_sources.FinnhubSignals().min_interval_seconds == 7200.0


def test_a_refused_catalogue_is_not_asked_again_that_day(monkeypatch):
    calls = []

    async def _json(url, headers, params, timeout):
        calls.append(url)
        return {"errors": {"plan": "Free plans do not have access to this season"},
                "response": []}

    monkeypatch.setattr(LS, "_json", _json)
    monkeypatch.setattr(LS, "settings", dataclasses.replace(
        config.settings, api_sports_key="k"))
    sport = LS.SPORTS["hockey"]
    for _ in range(3):
        with pytest.raises(RuntimeError):
            run(LS.league_teams(sport))
    assert len(calls) == 1
    assert LS.budget_for("hockey").used == 1
