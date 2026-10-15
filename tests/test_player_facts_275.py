"""§275: a player's season and the league's injury report, beside the scoreboard.

BALLDONTLIE answers here from a stub that speaks its published field names
(the OpenAPI specs `balldontlie.py` was written from). Nothing reaches the
network: every request goes through `live_sources._json`, which is replaced.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import balldontlie  # noqa: E402
import config  # noqa: E402
import live_facts  # noqa: E402
import live_sources  # noqa: E402
import score_guard  # noqa: E402
import script_generator as sg  # noqa: E402

LEBRON = {"id": 237, "first_name": "LeBron", "last_name": "James",
          "position": "F", "team": {"id": 14, "full_name": "Los Angeles Lakers",
                                    "name": "Lakers"}}
BRONNY = {"id": 999, "first_name": "Bronny", "last_name": "James",
          "position": "G", "team": {"id": 14, "full_name": "Los Angeles Lakers"}}


def use(monkeypatch, **changes):
    changes.setdefault("balldontlie_api_key", "bdl-test-key")
    changes.setdefault("balldontlie_tier", "goat")
    patched = dataclasses.replace(config.settings, **changes)
    for module in (balldontlie, live_facts, live_sources, sg):
        monkeypatch.setattr(module, "settings", patched)
    return patched


@pytest.fixture
def wire(monkeypatch):
    """Requests BALLDONTLIE would receive, answered from `routes`."""
    sent: list = []
    routes: dict = {}

    async def fake_json(url, headers, params, timeout):
        path = url.split("api.balldontlie.io", 1)[1]
        sent.append((path, dict(params), dict(headers)))
        answer = routes.get(path)
        if callable(answer):
            answer = answer(params)
        if isinstance(answer, BaseException):
            raise answer
        return answer if answer is not None else {"data": []}

    monkeypatch.setattr(live_sources, "_json", fake_json)
    return SimpleNamespace(sent=sent, routes=routes)


def brief(entity="", subject="", query="", domain="sports", category=""):
    return SimpleNamespace(live_entity=entity, subject=subject or entity,
                           query=query or entity, live_domain=domain,
                           live_route="", category=category)


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# Resolution: the catalogue's own row, never a guess
# --------------------------------------------------------------------------
def test_a_named_player_resolves_in_the_league_the_question_names(wire, monkeypatch):
    use(monkeypatch)
    wire.routes["/nba/v1/players"] = {"data": [LEBRON, BRONNY]}
    source = balldontlie.PlayerReports()
    entity = run(source.resolve(brief("LeBron James", query="how is LeBron James playing in the NBA")))
    assert entity is not None and entity.id == "nba:player:237"
    # Searched by the last word, in the NBA only - the question named it.
    assert [p for p, _, _ in wire.sent] == ["/nba/v1/teams", "/nba/v1/players"]
    assert wire.sent[1][1]["search"] == "James"
    assert wire.sent[1][2]["Authorization"] == "bdl-test-key"


def test_two_players_who_both_match_are_no_answer(wire, monkeypatch):
    use(monkeypatch)
    wire.routes["/nba/v1/players"] = {"data": [LEBRON, BRONNY]}
    source = balldontlie.PlayerReports()
    assert run(source.resolve(brief("James", query="James in the NBA"))) is None


def test_an_unplaced_name_is_searched_in_the_configured_leagues(wire, monkeypatch):
    use(monkeypatch, balldontlie_leagues="nfl,nba", balldontlie_max_searches=2)
    mahomes = {"id": 7, "first_name": "Patrick", "last_name": "Mahomes",
               "position": "Quarterback",
               "team": {"id": 12, "full_name": "Kansas City Chiefs"}}
    wire.routes["/nfl/v1/players"] = {"data": [mahomes]}
    entity = run(balldontlie.PlayerReports().resolve(brief("Patrick Mahomes")))
    assert entity is not None and entity.id == "nfl:player:7"
    assert {p for p, _, _ in wire.sent} == {"/nfl/v1/players", "/nba/v1/players"}


def test_a_named_team_resolves_to_its_injury_report(wire, monkeypatch):
    use(monkeypatch)
    wire.routes["/nfl/v1/teams"] = {"data": [
        {"id": 12, "full_name": "Kansas City Chiefs", "name": "Chiefs"},
        {"id": 4, "full_name": "Denver Broncos", "name": "Broncos"}]}
    entity = run(balldontlie.PlayerReports().resolve(
        brief("Kansas City Chiefs", query="Chiefs injuries this week")))
    assert entity is not None and entity.id == "nfl:team:12"


def test_a_free_plan_never_asks_for_what_it_cannot_read(wire, monkeypatch):
    use(monkeypatch, balldontlie_tier="free")
    wire.routes["/nba/v1/players"] = {"data": [LEBRON]}
    source = balldontlie.PlayerReports()
    entity = run(source.resolve(brief("LeBron James", query="LeBron James NBA")))
    report = run(source.fetch(entity))
    paths = [p for p, _, _ in wire.sent]
    assert "/nba/v1/player_injuries" not in paths
    assert "/nba/v1/season_averages" not in paths
    assert report.facts == ["LeBron James plays for the Los Angeles Lakers, "
                            "per the NBA's roster."]


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------
def player(source, wire, league, row):
    entity = source._remember(balldontlie.LEAGUES[league], "player", row)  # noqa: SLF001
    return run(source.fetch(entity))


def test_an_injured_player_is_said_as_the_listing(wire, monkeypatch):
    use(monkeypatch)
    wire.routes["/nba/v1/player_injuries"] = {"data": [{
        "player": LEBRON, "status": "Out", "return_date": "2026-11-01",
        "description": "Oct 14: James (sciatica) will miss the opener."}]}
    report = player(balldontlie.PlayerReports(), wire, "nba", LEBRON)
    assert report.kind == live_facts.PLAYER_REPORT
    line = report.facts[1]
    assert line.startswith("LeBron James is listed as out on the NBA injury report.")
    assert "sciatica" in line
    assert "November 1, 2026, which is the team's estimate" in line


def test_absence_is_said_as_absence_from_the_report(wire, monkeypatch):
    use(monkeypatch)
    report = player(balldontlie.PlayerReports(), wire, "nba", LEBRON)
    assert any("is not on the NBA injury report as BALLDONTLIE had it" in f
               for f in report.facts)
    assert not any("healthy" in f for f in report.facts)


def test_nba_season_averages_and_last_season_when_this_one_is_empty(wire, monkeypatch):
    use(monkeypatch)
    seasons = []

    def averages(params):
        seasons.append(params["season"])
        if len(seasons) == 1:
            return {"data": []}
        return {"data": [{"games_played": 71, "pts": 25.7, "reb": 7.3,
                          "ast": 8.3, "fg_pct": 0.54, "min": "35:18"}]}

    wire.routes["/nba/v1/season_averages"] = averages
    report = player(balldontlie.PlayerReports(), wire, "nba", LEBRON)
    current = balldontlie.season_for(balldontlie.LEAGUES["nba"])
    assert seasons == [current, current - 1]
    label = balldontlie.season_label(balldontlie.LEAGUES["nba"], current - 1)
    assert (f"In the {label} NBA season, LeBron James averaged 25.7 points, "
            f"7.3 rebounds and 8.3 assists a game over 71 games, shooting 54 "
            f"percent from the field.") in report.facts


def test_totals_are_turned_into_averages_by_the_minutes(wire, monkeypatch):
    use(monkeypatch)
    clark = {"id": 3, "first_name": "Caitlin", "last_name": "Clark",
             "team": {"id": 1, "full_name": "Indiana Fever"}}
    wire.routes["/wnba/v1/player_season_stats"] = {"data": [{
        "games_played": 10, "min": 340.0, "pts": 192.0, "reb": 50.0, "ast": 85.0}]}
    report = player(balldontlie.PlayerReports(), wire, "wnba", clark)
    assert any("averaged 19.2 points, 5 rebounds and 8.5 assists a game over 10 games" in f
               for f in report.facts)


def test_a_quarterback_season(wire, monkeypatch):
    use(monkeypatch)
    row = {"id": 7, "first_name": "Patrick", "last_name": "Mahomes",
           "position": "Quarterback", "team": {"id": 12, "full_name": "Kansas City Chiefs"}}
    wire.routes["/nfl/v1/season_stats"] = {"data": [{
        "games_played": 6, "passing_attempts": 210, "passing_yards": 1650,
        "passing_touchdowns": 12, "passing_interceptions": 3,
        "rushing_attempts": 25, "rushing_yards": 140, "rushing_touchdowns": 1,
        "postseason": False}]}
    report = player(balldontlie.PlayerReports(), wire, "nfl", row)
    assert report.facts[0] == ("Patrick Mahomes is a quarterback for the Kansas "
                               "City Chiefs, per the NFL's roster.")
    assert any("1650 passing yards, 12 touchdown passes and 3 interceptions; "
               "140 rushing yards and 1 rushing touchdowns in 6 games." in f
               for f in report.facts)


def test_hockey_pairs_are_read_by_name(wire, monkeypatch):
    use(monkeypatch)
    row = {"id": 8478402, "first_name": "Connor", "last_name": "McDavid",
           "full_name": "Connor McDavid",
           "teams": [{"id": 22, "full_name": "Edmonton Oilers", "season": 2026}]}
    wire.routes["/nhl/v1/players/8478402/season_stats"] = {"data": [
        {"name": "gamesPlayed", "value": 5}, {"name": "goals", "value": 4},
        {"name": "assists", "value": 6}, {"name": "points", "value": 10},
        {"name": "faceoffWinPct", "value": 0.51}]}
    report = player(balldontlie.PlayerReports(), wire, "nhl", row)
    assert any("figures are 5 games, 4 goals, 6 assists, 10 points." in f
               for f in report.facts)


def test_a_soccer_season_is_summed_from_its_own_matches(wire, monkeypatch):
    use(monkeypatch)
    row = {"id": 50, "first_name": "Erling", "last_name": "Haaland",
           "display_name": "Erling Haaland"}
    wire.routes["/epl/v2/matches"] = {"data": [{"id": 1}, {"id": 2}]}
    wire.routes["/epl/v2/player_match_stats"] = {"data": [
        {"match_id": 1, "goals": 2, "assists": 0, "minutes_played": 90},
        {"match_id": 2, "goals": 1, "assists": 1, "minutes_played": 80,
         "yellow_cards": 1},
        {"match_id": 99, "goals": 5, "minutes_played": 90}]}  # another season
    report = player(balldontlie.PlayerReports(), wire, "epl", row)
    assert any("has 3 goals and 1 assists in 2 appearances, 170 minutes in "
               "all, with 1 yellow cards." in f for f in report.facts)


@pytest.mark.parametrize("league,row,route,answer,expected", [
    ("atp", {"id": 1, "first_name": "Jannik", "last_name": "Sinner"},
     "/atp/v1/player_career_stats",
     {"data": [{"ytd_wins": 58, "ytd_losses": 6, "ytd_titles": 6, "career_titles": 25}]},
     "This year on the ATP tour, Jannik Sinner has won 58 matches and lost 6, with 6 titles."),
    ("f1", {"id": 1, "first_name": "Lando", "last_name": "Norris"},
     "/f1/v1/driver_standings", {"data": [{"position": 2, "points": 341}]},
     "drivers' championship, Lando Norris is 2nd with 341 points."),
])
def test_tennis_and_formula_one(wire, monkeypatch, league, row, route, answer, expected):
    use(monkeypatch)
    wire.routes[route] = answer
    report = player(balldontlie.PlayerReports(), wire, league, row)
    assert any(expected in f for f in report.facts)


def test_a_fighter_and_a_golfer_need_only_the_catalogue(wire, monkeypatch):
    use(monkeypatch, balldontlie_tier="free")
    fighter = {"id": 1, "name": "Alex Pereira", "first_name": "Alex",
               "last_name": "Pereira", "record_wins": 12, "record_losses": 3,
               "record_draws": 0, "weight_class": {"name": "Light Heavyweight"}}
    report = player(balldontlie.PlayerReports(), wire, "mma", fighter)
    assert "Alex Pereira's professional record is 12 wins, 3 losses and 0 draws." \
        in report.facts
    golfer = {"id": 2, "first_name": "Scottie", "last_name": "Scheffler", "owgr": 1}
    report = player(balldontlie.PlayerReports(), wire, "pga", golfer)
    assert report.facts == ["Scottie Scheffler is ranked 1st in the Official "
                            "World Golf Ranking."]
    assert wire.sent == []


def test_a_team_injury_report(wire, monkeypatch):
    use(monkeypatch)
    team = {"id": 12, "full_name": "Kansas City Chiefs"}
    wire.routes["/nfl/v1/player_injuries"] = {"data": [
        {"player": {"id": 1, "first_name": "Travis", "last_name": "Kelce",
                    "team": {"id": 12}}, "status": "Questionable",
         "comment": "Ankle; limited in practice.", "date": "2026-10-14"}]}
    source = balldontlie.PlayerReports()
    entity = source._remember(balldontlie.LEAGUES["nfl"], "team", team)  # noqa: SLF001
    report = run(source.fetch(entity))
    assert report.facts[0] == ("The NFL injury report lists 1 player for the "
                               "Kansas City Chiefs.")
    assert report.facts[1].startswith(
        "Travis Kelce is listed as questionable on the NFL injury report.")
    assert "dated October 14, 2026" in report.facts[1]
    assert wire.sent[0][1] == {"team_ids[]": [12], "per_page": 100}


def test_a_refused_endpoint_is_not_asked_again_that_day(wire, monkeypatch):
    use(monkeypatch)
    wire.routes["/nba/v1/player_injuries"] = live_sources.ProviderHTTPError(
        401, "api.balldontlie.io/nba/v1/player_injuries")
    source = balldontlie.PlayerReports()
    first = player(source, wire, "nba", LEBRON)
    assert first.facts  # the roster line and the season still come
    source._reports.clear()  # noqa: SLF001
    player(source, wire, "nba", LEBRON)
    assert [p for p, _, _ in wire.sent].count("/nba/v1/player_injuries") == 1


def test_a_refused_key_is_an_account_refusal(wire, monkeypatch):
    use(monkeypatch)
    wire.routes["/nba/v1/players"] = live_sources.ProviderHTTPError(
        401, "api.balldontlie.io/nba/v1/players")
    with pytest.raises(balldontlie.AccountRefused):
        run(balldontlie.PlayerReports().resolve(brief("LeBron James", query="NBA")))


def test_the_plan_s_minute_is_never_overspent(monkeypatch):
    use(monkeypatch, balldontlie_tier="free")
    source = balldontlie.PlayerReports()
    for _ in range(5):
        source._pace(now=100.0)  # noqa: SLF001
    with pytest.raises(balldontlie.RateLimited):
        source._pace(now=101.0)  # noqa: SLF001
    source._pace(now=161.0)  # noqa: SLF001 - a minute later the window has moved


def test_every_league_has_a_season_rule_and_a_known_order():
    assert set(balldontlie.DEFAULT_ORDER) == set(balldontlie.LEAGUES)
    october = datetime(2026, 10, 15, tzinfo=timezone.utc)
    assert balldontlie.season_for(balldontlie.LEAGUES["nba"], october) == 2026
    assert balldontlie.season_for(balldontlie.LEAGUES["mlb"], datetime(
        2026, 1, 5, tzinfo=timezone.utc)) == 2025
    assert balldontlie.season_for(balldontlie.LEAGUES["fifa"], october) == 2026
    assert balldontlie.season_label(balldontlie.LEAGUES["nba"], 2025) == "2025-26"


# --------------------------------------------------------------------------
# The seam: beside the scoreboard, its own rules
# --------------------------------------------------------------------------
class StubPlayers(live_facts.LiveSource):
    name = "stub players"
    domain = "sports"

    def diagnose(self):
        return True, "stub"

    async def resolve(self, brief):
        return live_facts.Entity("sports", self.name, "p1", "LeBron James")

    async def fetch(self, entity):
        return live_facts.LiveFacts(
            domain="sports", source=self.name, as_of=datetime.now(timezone.utc),
            facts=["LeBron James has a record of 3 and 1 in the cup, 3-1."],
            kind=live_facts.PLAYER_REPORT)


@pytest.fixture
def stub_players(monkeypatch):
    monkeypatch.setattr(live_facts, "_PLAYER_SOURCES", [StubPlayers()])
    live_facts.clear_caches()
    yield
    live_facts.clear_caches()


def test_players_answer_only_a_sports_question(stub_players):
    result = run(live_facts.players(brief("LeBron James")))
    assert result is not None and result.outcome == live_facts.FACTS
    assert run(live_facts.players(brief("NVIDIA", domain="markets"))) is None


def test_the_report_block_has_its_own_rules(stub_players):
    block = run(live_facts.players(brief("LeBron James"))).as_prompt_block()
    assert "PLAYER and TEAM facts" in block
    assert "never say they are healthy" in block
    assert "UNDER WAY" not in block and "you do not know that it has" not in block


def test_a_player_report_is_current_for_hours_not_minutes():
    old = live_facts.LiveFacts(
        domain="sports", source="x", facts=["x"], kind=live_facts.PLAYER_REPORT,
        as_of=datetime.now(timezone.utc).replace(microsecond=0)
        - __import__("datetime").timedelta(hours=2))
    assert old.is_fresh()


def test_the_score_guard_takes_the_report_s_numbers(stub_players):
    report = run(live_facts.players(brief("LeBron James")))
    plan = SimpleNamespace(brief=brief("LeBron James"), live=None, players=report)
    guard = score_guard.ScoreGuard(plan)
    assert guard.why_not("He is 3-1 in the cup this year.") == ""
    assert guard.why_not("They won 27-10.") != ""


def test_prepare_puts_the_report_beside_the_scoreboard(stub_players, monkeypatch):
    patched = use(monkeypatch)
    plan = sg.plan_episode("how is LeBron James playing", 2)
    plan = dataclasses.replace(plan, brief=SimpleNamespace(
        live_entity="LeBron James", subject="LeBron James", query=plan.query,
        live_domain="sports", live_route="", category="",
        outcome_dependent=False, place=""))
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)

    async def understand(p, notes=None):
        return p

    async def research(p, notes=None):
        return p

    async def live_lookup(p, notes=None):
        return p

    generator.understand = understand
    generator.research = research
    generator.live_lookup = live_lookup
    prepared = run(generator.prepare(plan))
    assert prepared.players is not None
    assert prepared.players.facts.kind == live_facts.PLAYER_REPORT
    prompt = sg.build_prompt(prepared)
    assert "LeBron James has a record of 3 and 1" in prompt
    assert patched.player_facts


def test_install_registers_on_the_key_and_staging_removes_it(monkeypatch):
    import spend_guard

    use(monkeypatch)
    live_sources.install()
    assert [s.name for s in live_facts._PLAYER_SOURCES] == ["BALLDONTLIE"]  # noqa: SLF001
    use(monkeypatch, balldontlie_api_key="")
    live_sources.install()
    assert live_facts._PLAYER_SOURCES == []  # noqa: SLF001
    assert "BALLDONTLIE_API_KEY" in spend_guard.PAID_CREDENTIALS
