"""§289: API-Sports and Tiingo's free plans while developing (temporary).

Every request goes through `live_sources._json`, stubbed with rows shaped as
each provider's documentation shapes them. No network.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api_sports  # noqa: E402
import balldontlie  # noqa: E402
import balldontlie_scores as bs  # noqa: E402
import config  # noqa: E402
import live_facts  # noqa: E402
import live_sources  # noqa: E402
import provider_usage  # noqa: E402
import story_sources  # noqa: E402
import tiingo  # noqa: E402

NOW = datetime.now(timezone.utc)
MODULES = (api_sports, balldontlie, bs, live_sources, story_sources, tiingo)


def use(monkeypatch, **changes):
    patched = dataclasses.replace(config.settings, **changes)
    for module in MODULES:
        monkeypatch.setattr(module, "settings", patched)
    return patched


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for name in ("TEAMS", "SHADOW_TEAMS", "STANDINGS", "SCHEDULES", "CARDS",
                 "SWEPT_AT", "FAILURES"):
        monkeypatch.setattr(bs, name, {})
    monkeypatch.setattr(api_sports, "SHARED", None)
    monkeypatch.setattr(balldontlie, "SHARED", None)
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
        return {"response": [], "errors": []}

    monkeypatch.setattr(live_sources, "_json", fake_json)
    return SimpleNamespace(sent=sent, routes=routes)


NFL = "https://v1.american-football.api-sports.io"
SOCCER = "https://v3.football.api-sports.io"
CHIEFS = {"id": 17, "name": "Kansas City Chiefs"}
BILLS = {"id": 20, "name": "Buffalo Bills"}


def nfl_game(short="Q3", home=CHIEFS, away=BILLS, hs=21, as_=14, start=None,
             timer="05:12"):
    at = start or NOW - timedelta(hours=1)
    return {"game": {"id": 501, "date": {"timestamp": int(at.timestamp())},
                     "status": {"short": short, "long": "x", "timer": timer}},
            "teams": {"home": home, "away": away},
            "scores": {"home": {"total": hs}, "away": {"total": as_}}}


# --------------------------------------------------------------------------
# Which leagues, and which client
# --------------------------------------------------------------------------
def test_no_key_serves_nothing(monkeypatch):
    use(monkeypatch, api_sports_key="")
    assert api_sports.served() == frozenset()
    assert api_sports.scoreboard_client() is balldontlie.shared_client()


def test_the_key_serves_every_league_while_balldontlie_is_free(monkeypatch):
    use(monkeypatch, api_sports_key="k", balldontlie_api_key="b",
        balldontlie_tier="free")
    assert api_sports.served() == frozenset(api_sports.LEAGUES)
    assert isinstance(api_sports.scoreboard_client(), api_sports.Router)


def test_a_paid_balldontlie_keeps_its_leagues(monkeypatch):
    use(monkeypatch, api_sports_key="k", balldontlie_api_key="b",
        balldontlie_tier="all-star")
    assert api_sports.served() == frozenset()


def test_named_leagues_win(monkeypatch):
    use(monkeypatch, api_sports_key="k", api_sports_leagues="nfl, epl, f1",
        balldontlie_api_key="b", balldontlie_tier="goat")
    assert api_sports.served() == {"nfl", "epl"}


def test_the_key_alone_builds_the_scoreboard(monkeypatch):
    use(monkeypatch, api_sports_key="k", balldontlie_api_key="",
        live_sports_provider="")
    assert live_sources.configured()["sports"] == "balldontlie"
    board = live_sources.BUILDERS["sports"]["balldontlie"]()
    assert isinstance(board.client, api_sports.Router)
    ok, why = board.diagnose()
    assert ok and "API-Sports" in why


# --------------------------------------------------------------------------
# Rows, translated
# --------------------------------------------------------------------------
def test_a_game_in_balldontlies_shape():
    row = api_sports.game_row("nfl", nfl_game())
    assert row["id"] == "aps:501"
    assert row["home_team"] == {"id": "aps:17", "full_name": "Kansas City Chiefs",
                                "name": "Chiefs"}
    assert row["status_state"] == "in_progress"
    assert (row["home_team_score"], row["visitor_team_score"]) == (21, 14)
    assert row["period"] == 3 and row["time_remaining"] == "05:12"
    league = balldontlie.LEAGUES["nfl"]
    said = bs.game_facts(league, row, bs.status_of(row))
    assert said[0] == "Kansas City Chiefs lead Buffalo Bills 21 to 14."


@pytest.mark.parametrize("short,expected", [
    ("NS", live_facts.SCHEDULED), ("Q2", live_facts.IN_PROGRESS),
    ("FT", live_facts.FINAL), ("AOT", live_facts.FINAL),
    ("XYZ", live_facts.UNKNOWN), ("POST", live_facts.UNKNOWN)])
def test_status_from_the_short_code_only(short, expected):
    assert api_sports.status_of_short("nfl", short) == expected


def test_a_postponed_game_is_said_as_postponed():
    row = api_sports.game_row("nfl", nfl_game(short="POST"))
    said = bs.game_facts(balldontlie.LEAGUES["nfl"], row, bs.status_of(row))
    assert "postponed" in said[0]


def test_roster_nicknames():
    assert api_sports.nickname("Toronto Maple Leafs", "nhl") == "Maple Leafs"
    assert api_sports.nickname("Boston Red Sox", "mlb") == "Red Sox"
    assert api_sports.nickname("Manchester United", "epl") == "Manchester United"


def test_a_soccer_fixture():
    at = NOW - timedelta(minutes=70)
    row = api_sports.game_row("epl", {
        "fixture": {"id": 9, "timestamp": int(at.timestamp()),
                    "status": {"short": "2H", "elapsed": 67}},
        "teams": {"home": {"id": 42, "name": "Arsenal"},
                  "away": {"id": 49, "name": "Chelsea"}},
        "goals": {"home": 2, "away": 1}})
    assert row["status_state"] == "in_progress"
    assert row["period_detail"] == "the 67th minute"
    assert (row["home_team_score"], row["visitor_team_score"]) == (2, 1)


def test_standings_every_shape():
    nfl = api_sports.standing_rows("nfl", [
        {"team": CHIEFS, "won": 5, "lost": 1, "ties": 0, "position": 1,
         "division": "AFC West"}])
    assert nfl[0]["wins"] == 5 and nfl[0]["group_name"] == "AFC West"
    nba = api_sports.standing_rows("nba", [[
        {"team": {"id": 1, "name": "Boston Celtics"}, "position": 2,
         "group": {"name": "Eastern Conference"},
         "games": {"win": {"total": 10}, "lose": {"total": 3}}}]])
    assert (nba[0]["wins"], nba[0]["losses"], nba[0]["rank"]) == (10, 3, 2)
    epl = api_sports.standing_rows("epl", [{"league": {"standings": [[
        {"rank": 1, "team": {"id": 42, "name": "Arsenal"}, "points": 19,
         "group": "Premier League", "all": {"win": 6, "draw": 1, "lose": 0}}]]}}])
    said = bs.standing_facts(balldontlie.LEAGUES["epl"], epl[0]["team"], epl[0])
    assert said == ["The Arsenal have won 6 and lost 0, with 1 drawn this season, "
                    "per the league's standings.",
                    "The Arsenal are 1st in the Premier League with 19 points."]


# --------------------------------------------------------------------------
# The client
# --------------------------------------------------------------------------
def test_a_window_with_a_team_is_one_request(monkeypatch, wire):
    use(monkeypatch, api_sports_key="k")
    wire.routes[f"{NFL}/games"] = {"response": [nfl_game()], "errors": []}
    client = api_sports.Client()
    league = balldontlie.LEAGUES["nfl"]
    today = NOW.date()
    days = [(today + timedelta(days=d)).isoformat() for d in (-1, 0, 1)]
    data = run(client.get(league, "games", "games",
                          {"dates[]": days, "team_ids[]": ["aps:17"]}))
    assert [g["id"] for g in data["data"]] == ["aps:501"]
    url, params, headers = wire.sent[0]
    assert params == {"league": "1", "season": str(balldontlie.season_for(league)),
                      "team": "17"}
    assert headers == {"x-apisports-key": "k"}
    # The same team's schedule a second later is the same request.
    run(client.get(league, "games", "games", {"team_ids[]": ["aps:17"], "seasons[]": [2026]}))
    assert len(wire.sent) == 1


def test_basketball_names_its_season_as_a_span(monkeypatch, wire):
    use(monkeypatch, api_sports_key="k")
    client = api_sports.Client()
    run(client.get(balldontlie.LEAGUES["nba"], "teams", "teams", {}))
    year = balldontlie.season_for(balldontlie.LEAGUES["nba"])
    assert wire.sent[0][1]["season"] == f"{year}-{year + 1}"


def test_the_season_limit_is_a_refusal_remembered_for_the_day(monkeypatch, wire):
    use(monkeypatch, api_sports_key="k")
    wire.routes[f"{NFL}/teams"] = {
        "response": [], "errors": {"plan": "Free plans do not have access to this season"}}
    client = api_sports.Client()
    league = balldontlie.LEAGUES["nfl"]
    with pytest.raises(balldontlie.TierRefused):
        run(client.get(league, "teams", "teams", {}))
    with pytest.raises(balldontlie.TierRefused):
        run(client.get(league, "teams", "teams", {}))
    assert len(wire.sent) == 1
    assert "season" in client.problem()


def test_a_spent_day_is_an_outage_not_a_quiet_day(monkeypatch, wire):
    use(monkeypatch, api_sports_key="k")
    wire.routes[f"{NFL}/games"] = {
        "response": [], "errors": {"requests": "You have reached the request limit for the day"}}
    client = api_sports.Client()
    with pytest.raises(balldontlie.RateLimited):
        run(client.get(balldontlie.LEAGUES["nfl"], "games", "games",
                       {"dates[]": [NOW.date().isoformat()]}))
    assert client.remaining("american-football") == 0


def test_a_refused_key_is_an_account_refusal(monkeypatch, wire):
    use(monkeypatch, api_sports_key="k")
    wire.routes[f"{NFL}/teams"] = {"response": [], "errors": {"token": "Error/Missing application key"}}
    with pytest.raises(balldontlie.AccountRefused):
        run(api_sports.Client().get(balldontlie.LEAGUES["nfl"], "teams", "teams", {}))


def test_the_day_is_counted_per_sport(monkeypatch, wire):
    use(monkeypatch, api_sports_key="k", api_sports_daily_requests=3,
        api_sports_lookup_reserve=1)
    client = api_sports.Client()
    league = balldontlie.LEAGUES["nfl"]
    run(client.get(league, "games", "games", {"dates[]": ["2026-10-01"]}))
    assert client.sweep_may("nfl")
    run(client.get(league, "games", "games", {"dates[]": ["2026-10-02"]}))
    # The last request is a listener's, never the sweep's.
    assert not client.sweep_may("nfl")
    run(client.get(league, "games", "games", {"dates[]": ["2026-10-03"]}))
    with pytest.raises(balldontlie.RateLimited):
        run(client.get(league, "games", "games", {"dates[]": ["2026-10-04"]}))
    # Soccer has its own day.
    assert client.remaining("football") == 3


def test_what_it_does_not_answer_stays_balldontlies(monkeypatch):
    use(monkeypatch, api_sports_key="k")
    with pytest.raises(balldontlie.TierRefused):
        run(api_sports.Client().get(balldontlie.LEAGUES["nfl"], "injuries", "player_injuries", {}))


# --------------------------------------------------------------------------
# The scoreboard, end to end
# --------------------------------------------------------------------------
def test_the_scoreboard_answers_from_api_sports(monkeypatch, wire):
    use(monkeypatch, api_sports_key="k", balldontlie_api_key="", balldontlie_tier="free")
    wire.routes[f"{NFL}/teams"] = {"response": [CHIEFS, BILLS], "errors": []}
    wire.routes[f"{NFL}/games"] = {"response": [nfl_game()], "errors": []}
    wire.routes[f"{NFL}/standings"] = {"response": [
        {"team": CHIEFS, "won": 5, "lost": 1, "position": 1, "division": "AFC West"}],
        "errors": []}
    board = bs.ScoreBoard(client=api_sports.scoreboard_client())
    brief = SimpleNamespace(live_entity="", subject="",
                            query="Kansas City Chiefs vs Buffalo Bills score",
                            category="", named_slot="")
    entity = run(board.resolve(brief))
    assert entity is not None and entity.id == "nfl:game:aps:501"
    facts = run(board.fetch(entity))
    assert facts.source == "API-Sports"
    assert facts.status == live_facts.IN_PROGRESS
    assert "Kansas City Chiefs lead Buffalo Bills 21 to 14." in facts.facts
    assert any("AFC West" in s for s in facts.facts)
    assert not any("balldontlie" in url for url, _, _ in wire.sent)


def test_player_reports_never_see_api_sports_ids(monkeypatch, wire):
    use(monkeypatch, api_sports_key="k", balldontlie_api_key="b", balldontlie_tier="free")
    bs.TEAMS["nfl"] = (time.time(), {"aps:17": {"id": "aps:17", "full_name": "Kansas City Chiefs"}})
    assert balldontlie.PlayerReports._held_teams() == [("nfl", [])]
    wire.routes["https://api.balldontlie.io/nfl/v1/teams"] = {
        "data": [{"id": 12, "full_name": "Kansas City Chiefs", "name": "Chiefs"}]}
    teams = run(bs.league_teams(balldontlie.Client(), balldontlie.LEAGUES["nfl"]))
    assert list(teams) == [12]
    assert list(bs.TEAMS["nfl"][1]) == ["aps:17"]


def test_the_sweep_keeps_the_reserve(monkeypatch, wire):
    use(monkeypatch, api_sports_key="k", balldontlie_api_key="", api_sports_leagues="nfl",
        api_sports_daily_requests=3, api_sports_lookup_reserve=2)
    import stories

    monkeypatch.setattr(stories, "recent_demand", lambda now=None: True)
    wire.routes[f"{NFL}/games"] = {"response": [nfl_game()], "errors": []}
    signals = bs.make_signals()()
    assert isinstance(signals.client, api_sports.Router)
    out = run(signals.collect(10))
    assert out and out[0].live_line.startswith("Live")
    # One more request would leave fewer than the reserve: not swept.
    bs.SWEPT_AT.clear()
    bs.CARDS.clear()
    with pytest.raises(balldontlie.RateLimited):
        run(signals.collect(10))


# --------------------------------------------------------------------------
# Tiingo
# --------------------------------------------------------------------------
IEX = "https://api.tiingo.com/iex/"
SEARCH = "https://api.tiingo.com/tiingo/utilities/search"


def iex_row(ticker="AAPL", last=230.0, prev=225.0, at=None):
    at = at or datetime.now(timezone.utc) - timedelta(minutes=2)
    return {"ticker": ticker, "last": last, "tngoLast": last, "prevClose": prev,
            "lastSaleTimestamp": at.isoformat(), "timestamp": at.isoformat()}


def test_a_quote_in_finnhubs_shape():
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


def test_the_key_puts_markets_on_tiingo(monkeypatch):
    use(monkeypatch, tiingo_api_key="t", finnhub_key="f", live_markets_provider="")
    assert live_sources.configured()["markets"] == "tiingo"
    use(monkeypatch, tiingo_api_key="t", finnhub_key="f", live_markets_provider="finnhub")
    assert live_sources.configured()["markets"] == "finnhub"


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


def test_the_sweep_is_one_request_and_only_on_tiingo(monkeypatch, wire):
    use(monkeypatch, tiingo_api_key="t", finnhub_key="f", live_markets_provider="",
        stories_market_move_percent=1.0)
    wire.routes[IEX] = [iex_row("AAPL"), iex_row("NVDA", last=110.0, prev=100.0)]
    out = run(story_sources.TiingoSignals().collect(5))
    assert len(wire.sent) == 1
    assert [s.subject for s in out] == ["Nvidia", "Apple"]
    assert live_sources.swept_quote("NVDA")["data"]["c"] == 110.0
    # Finnhub's sweep stands down while Tiingo is the provider.
    assert run(story_sources.FinnhubSignals().collect(5)) == []
    ok, why = story_sources.FinnhubSignals().diagnose()
    assert not ok and "tiingo" in why


def test_finnhub_alone_is_unchanged(monkeypatch):
    use(monkeypatch, tiingo_api_key="", finnhub_key="f", live_markets_provider="")
    assert live_sources.configured()["markets"] == "finnhub"
    assert story_sources.FinnhubSignals().diagnose()[0]
    assert not story_sources.TiingoSignals().diagnose()[0]


# --------------------------------------------------------------------------
# /admin
# --------------------------------------------------------------------------
def test_requests_are_counted_against_their_provider():
    assert provider_usage.provider_for_host("v1.hockey.api-sports.io") == "api_sports"
    assert provider_usage.provider_for_host("api.tiingo.com") == "tiingo"
    assert api_sports.sport_of_host("v3.football.api-sports.io") == "football"


def test_tiingo_free_is_flagged_non_commercial(monkeypatch):
    patched = dataclasses.replace(config.settings, tiingo_api_key="t", tiingo_plan="free")
    monkeypatch.setattr(config, "settings", patched)
    report = provider_usage.licences()
    assert "tiingo" in report["non_commercial_in_use"]


def test_staging_scrubs_both_keys():
    import spend_guard

    assert {"API_SPORTS_KEY", "TIINGO_API_KEY"} <= set(spend_guard.PAID_CREDENTIALS)
