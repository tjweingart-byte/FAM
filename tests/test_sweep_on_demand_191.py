"""§191: the outside services are asked when somebody is looking and there
is something to see - not on a timer all day.

At the owner's direction, after the admin page's first day of counts
(1,392 Finnhub quotes, 172 hockey requests, 86 volleyball with no volleyball
on any tile):

* The sports sweep follows only the leagues the owner named; every other
  league is looked up on demand by the episode that asks. Since §280 the
  sweep is BALLDONTLIE's (`balldontlie_scores.SWEPT`, `Signals`).
* A followed league's card is read once a day, then again only while a
  followed game is on or about to start - and only if somebody drew myFAM
  recently.
* Finnhub is asked every two hours, round the clock.
* An endpoint the provider refused is not asked again that day.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import balldontlie  # noqa: E402
import balldontlie_scores as bs  # noqa: E402
import config  # noqa: E402
import live_sources as LS  # noqa: E402
import stories  # noqa: E402
import story_sources  # noqa: E402

NFL = balldontlie.LEAGUES["nfl"]


def run(coro):
    return asyncio.run(coro)


#: Noon UTC on a fixed day, so no test straddles a UTC midnight by accident.
NOON = datetime(2026, 10, 1, 12, tzinfo=timezone.utc).timestamp()
TODAY = "2026-10-01"
YESTERDAY = "2026-09-30"


def game(state, kickoff, gid=1):
    return {"id": gid, "status_state": state,
            "date": datetime.fromtimestamp(kickoff, tz=timezone.utc).isoformat(),
            "home_team": {"id": 1, "full_name": "Kansas City Chiefs"},
            "visitor_team": {"id": 2, "full_name": "Denver Broncos"},
            "home_team_score": 7, "visitor_team_score": 3}


class _Clock:
    @staticmethod
    def time():
        return NOON


def _settings(monkeypatch, **changes):
    changes.setdefault("balldontlie_api_key", "k")
    changes.setdefault("balldontlie_tier", "goat")
    changes.setdefault("live_sports_provider", "")
    patched = dataclasses.replace(config.settings, **changes)
    for module in (LS, balldontlie, bs, story_sources, stories):
        monkeypatch.setattr(module, "settings", patched)
    return patched


@pytest.fixture()
def provider(monkeypatch):
    """BALLDONTLIE's NFL answering whatever `provider.card` holds, counting
    calls, on a clock fixed at `NOON`."""
    calls = []
    monkeypatch.setattr(bs, "time", _Clock)
    monkeypatch.setattr(bs, "SWEPT", ("nfl",))

    class Provider:
        card: list = []

    async def _json(url, headers, params, timeout):
        calls.append((url, dict(params)))
        return {"data": list(Provider.card)}

    monkeypatch.setattr(LS, "_json", _json)
    _settings(monkeypatch)
    Provider.calls = calls
    return Provider


def signals():
    return bs.make_signals()(client=balldontlie.Client())


# --------------------------------------------------------------------------
# Which leagues are followed
# --------------------------------------------------------------------------
def test_the_followed_leagues_are_the_owner_s_list():
    assert set(bs.SWEPT) == {"nfl", "ncaaf", "nba", "ncaab", "wnba", "mlb",
                             "nhl", "ucl", "epl", "laliga", "bundesliga", "fifa"}
    for niche in ("seriea", "ligue1", "mls", "ncaaw", "cbb", "atp", "wta",
                  "pga", "f1", "mma"):
        assert niche not in bs.SWEPT, niche
    # Every followed league is one the scoreboard can read games for.
    assert set(bs.SWEPT) <= set(bs.SHAPES)


def test_a_league_not_followed_is_never_swept(provider):
    run(signals().collect(8))
    assert provider.calls and all("/nfl/v1/" in url for url, _ in provider.calls)


# --------------------------------------------------------------------------
# Only when there is something to watch
# --------------------------------------------------------------------------
def test_a_finished_day_is_read_once_and_not_again(provider):
    provider.card = [game("final", NOON - 4 * 3600)]
    source = signals()
    run(source.collect(8))
    assert len(provider.calls) == 1
    # Hours later, every followed game is still final: nothing to ask.
    assert source.due("nfl", NOON + 3 * 3600) == []


def test_a_game_hours_away_waits_and_one_about_to_start_is_watched(provider):
    kick = NOON + 5 * 3600
    provider.card = [game("scheduled", kick)]
    source = signals()
    run(source.collect(8))
    assert source.due("nfl", NOON + 3600) == []
    # Inside the fifteen minutes before kick-off, and an interval since the
    # last sweep.
    soon = kick - 900 + 60
    assert TODAY in source.due("nfl", soon)


def test_a_game_under_way_is_swept_on_the_interval_not_faster(provider):
    provider.card = [game("in_progress", NOON - 3600)]
    source = signals()
    run(source.collect(8))
    gap = config.settings.stories_sports_interval_seconds
    assert source.due("nfl", NOON + 60) == []
    assert source.due("nfl", NOON + gap) != []


def test_last_night_s_late_game_is_followed_past_midnight(provider):
    bs.CARDS["nfl"] = {
        TODAY: (NOON, []),
        YESTERDAY: (NOON, [game("in_progress", NOON - 13 * 3600)]),
    }
    assert YESTERDAY in signals().due("nfl", NOON)


def test_watching_is_a_game_on_or_about_to_start():
    assert bs.watching([game("in_progress", NOON - 3600)], NOON)
    assert bs.watching([game("scheduled", NOON + 600)], NOON)
    assert not bs.watching([game("scheduled", NOON + 3 * 3600)], NOON)
    assert not bs.watching([game("final", NOON - 3600)], NOON)
    assert not bs.watching([game("postponed", NOON - 600)], NOON)
    assert not bs.watching([], NOON)


# --------------------------------------------------------------------------
# Only on demand
# --------------------------------------------------------------------------
def test_nobody_looking_means_no_sweep(provider):
    stories.reset()
    source = signals()
    assert "on demand" in source.idle(1_000_000.0)
    assert stories.note_demand(1_000_000.0) is True
    assert source.idle(1_000_000.0) == ""
    # A second look inside the window does not wake anything again.
    assert stories.note_demand(1_000_100.0) is False
    window = config.settings.stories_demand_seconds
    assert stories.note_demand(1_000_100.0 + window + 1) is True
    stories.reset()


def test_an_idle_source_is_skipped_and_said(provider):
    stories.reset()
    stories.register(signals())
    try:
        found, reports = run(stories.collect(now=1_000_000.0))
        assert found == [] and provider.calls == []
        assert reports[0].outcome == stories.SKIPPED
        assert "on demand" in reports[0].detail
    finally:
        stories.reset()
        for source in list(stories._SOURCES):
            stories.unregister(source.name)


# --------------------------------------------------------------------------
# Finnhub and the refused endpoint
# --------------------------------------------------------------------------
def test_finnhub_is_asked_every_two_hours():
    assert config.settings.stories_markets_interval_seconds == 7200.0
    assert story_sources.FinnhubSignals().min_interval_seconds == 7200.0


def test_a_refused_endpoint_is_not_asked_again_that_day(monkeypatch):
    calls = []

    async def _json(url, headers, params, timeout):
        calls.append(url)
        raise LS.ProviderHTTPError(403, "api.balldontlie.io/nhl/v1/standings")

    monkeypatch.setattr(LS, "_json", _json)
    _settings(monkeypatch)
    client = balldontlie.Client()
    for _ in range(3):
        with pytest.raises(balldontlie.TierRefused):
            run(bs.league_standings(client, balldontlie.LEAGUES["nhl"]))
    assert len(calls) == 1


def test_a_catalogue_that_timed_out_is_asked_again(monkeypatch):
    """Only the provider saying no is remembered for the day: a timeout or a
    5xx is the network or the provider unwell, and the next try may work."""
    calls = []

    async def _json(url, headers, params, timeout):
        calls.append(url)
        if len(calls) == 1:
            raise TimeoutError("slow")
        if len(calls) == 2:
            raise LS.ProviderHTTPError(503, "api.balldontlie.io/nhl/v1/teams")
        return {"data": [{"id": 1, "full_name": "Toronto Maple Leafs"}]}

    monkeypatch.setattr(LS, "_json", _json)
    _settings(monkeypatch)
    client = balldontlie.Client()
    nhl = balldontlie.LEAGUES["nhl"]
    with pytest.raises(TimeoutError):
        run(bs.league_teams(client, nhl))
    with pytest.raises(LS.ProviderHTTPError):
        run(bs.league_teams(client, nhl))
    teams = run(bs.league_teams(client, nhl))
    assert [t["full_name"] for t in teams.values()] == ["Toronto Maple Leafs"]
    assert client.refused == {}


def test_a_failed_first_read_waits_the_interval_not_every_tick(provider, monkeypatch):
    async def _json(url, headers, params, timeout):
        provider.calls.append((url, dict(params)))
        raise LS.ProviderHTTPError(500, "api.balldontlie.io/nfl/v1/games")

    monkeypatch.setattr(LS, "_json", _json)
    source = signals()
    with pytest.raises(LS.ProviderHTTPError):
        run(source.collect(8))
    assert "nfl" in bs.FAILURES
    gap = config.settings.stories_sports_interval_seconds
    assert source.due("nfl", NOON + 60) == []
    assert source.due("nfl", NOON + gap) == [TODAY, YESTERDAY]
