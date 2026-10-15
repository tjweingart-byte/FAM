"""§277: BALLDONTLIE is the scoreboard - games, standings, F1, UFC, the sweep.

Every request goes through `live_sources._json`, stubbed: rows here are
shaped as BALLDONTLIE's published specs shape them. No network.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import balldontlie  # noqa: E402
import balldontlie_scores as bs  # noqa: E402
import config  # noqa: E402
import live_facts  # noqa: E402
import live_sources  # noqa: E402
import score_guard  # noqa: E402
import story_sources  # noqa: E402

NOW = datetime.now(timezone.utc)
CHIEFS = {"id": 12, "full_name": "Kansas City Chiefs", "name": "Chiefs"}
BILLS = {"id": 3, "full_name": "Buffalo Bills", "name": "Bills"}


def use(monkeypatch, **changes):
    changes.setdefault("balldontlie_api_key", "bdl-key")
    changes.setdefault("balldontlie_tier", "goat")
    patched = dataclasses.replace(config.settings, **changes)
    for module in (balldontlie, bs, live_sources, story_sources):
        monkeypatch.setattr(module, "settings", patched)
    return patched


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for name in ("TEAMS", "STANDINGS", "SCHEDULES", "CARDS", "SWEPT_AT", "FAILURES"):
        monkeypatch.setattr(bs, name, {})


@pytest.fixture
def wire(monkeypatch):
    sent: list = []
    routes: dict = {}

    async def fake_json(url, headers, params, timeout):
        path = url.split("api.balldontlie.io", 1)[1]
        sent.append((path, dict(params)))
        answer = routes.get(path)
        if callable(answer):
            answer = answer(params)
        return answer if answer is not None else {"data": []}

    monkeypatch.setattr(live_sources, "_json", fake_json)
    return SimpleNamespace(sent=sent, routes=routes)


def game(state, home=CHIEFS, away=BILLS, hs=21, as_=14, start=None, **extra):
    row = {"id": 77, "home_team": home, "visitor_team": away,
           "home_team_score": hs, "visitor_team_score": as_,
           "status_state": state,
           "date": (start or NOW - timedelta(hours=1)).isoformat()}
    row.update(extra)
    return row


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# A row, read
# --------------------------------------------------------------------------
@pytest.mark.parametrize("state,expected", [
    ("in_progress", live_facts.IN_PROGRESS), ("final", live_facts.FINAL),
    ("scheduled", live_facts.SCHEDULED), ("postponed", live_facts.UNKNOWN),
    ("whatever", live_facts.UNKNOWN), ({"state": "final"}, live_facts.FINAL)])
def test_the_status_is_the_provider_s_normalised_state(state, expected):
    assert bs.status_of({"status_state": state}) == expected


def test_a_stuck_feed_is_not_believed():
    shape = bs.SHAPES["nfl"]
    old = game("in_progress", start=NOW - timedelta(hours=9))
    assert bs.believable(live_facts.IN_PROGRESS, old, shape) == live_facts.UNKNOWN
    late = game("scheduled", start=NOW - timedelta(hours=5))
    assert bs.believable(live_facts.SCHEDULED, late, shape) == live_facts.UNKNOWN


def test_the_sentences_are_the_scoreboard_s_and_the_guard_reads_them():
    league = balldontlie.LEAGUES["nba"]
    row = game("in_progress", period=3, time="5:21",
               home={"id": 1, "full_name": "Boston Celtics"},
               away={"id": 2, "full_name": "New York Knicks"}, hs=88, as_=80)
    said = bs.game_facts(league, row, live_facts.IN_PROGRESS)
    assert said == ["Boston Celtics lead New York Knicks 88 to 80.",
                    "They are in the 3rd quarter, 5:21 left."]
    facts = live_facts.LiveFacts(domain="sports", source="x", as_of=NOW,
                                 facts=said, status=live_facts.IN_PROGRESS)
    plan = SimpleNamespace(brief=SimpleNamespace(live_domain="sports"),
                           live=live_facts.LiveLookup("sports", live_facts.FACTS, facts=facts),
                           players=None)
    guard = score_guard.ScoreGuard(plan)
    assert guard.why_not("The Celtics lead 88-80 in the third quarter.") == ""
    assert guard.why_not("The Knicks lead 80-88.") != ""


def test_a_postponed_game_says_so_and_gives_no_result():
    league = balldontlie.LEAGUES["nfl"]
    said = bs.game_facts(league, game("postponed"), live_facts.UNKNOWN)
    assert said == ["The NFL lists the game between Kansas City Chiefs and "
                    "Buffalo Bills as postponed, so there is no result to give."]


def test_soccer_names_its_sides_from_the_catalogue():
    league = balldontlie.LEAGUES["epl"]
    row = {"id": 5, "home_team_id": 40, "away_team_id": 50, "home_score": 2,
           "away_score": 2, "status_state": "final", "date": NOW.isoformat()}
    catalogue = {40: {"id": 40, "name": "Liverpool"}, 50: {"id": 50, "name": "Arsenal"}}
    assert bs.game_facts(league, row, live_facts.FINAL, catalogue) == [
        "Liverpool and Arsenal are level at 2 goals each."]


def test_the_tile_line_is_the_tiles_one_format():
    league = balldontlie.LEAGUES["nfl"]
    assert bs.live_line(league, game("final")) == (
        live_facts.FINAL, "Final · Kansas City Chiefs 21–14 Buffalo Bills")
    start = (NOW + timedelta(hours=2)).replace(second=0, microsecond=0)
    status, line = bs.live_line(league, game("scheduled", start=start))
    assert status == live_facts.SCHEDULED and line == f"Starts {start:%H:%M} UTC"


# --------------------------------------------------------------------------
# Finding and fetching the game
# --------------------------------------------------------------------------
def brief(text, entity="", slot=""):
    return SimpleNamespace(live_entity=entity, subject=text, query=text,
                           live_domain="sports", live_route="", category="",
                           named_slot=slot)


def test_a_named_team_finds_its_game_and_its_seasons(wire, monkeypatch):
    use(monkeypatch)
    wire.routes["/nfl/v1/teams"] = {"data": [CHIEFS, BILLS]}
    wire.routes["/nfl/v1/games"] = lambda p: {"data": [game("in_progress")]}
    wire.routes["/nfl/v1/standings"] = {"data": [
        {"team": CHIEFS, "wins": 5, "losses": 1, "ties": 0, "playoff_seed": 1},
        {"team": BILLS, "wins": 4, "losses": 2, "ties": 0}]}
    source = bs.ScoreBoard()
    entity = run(source.resolve(brief("how are the Chiefs doing against the Bills",
                                      entity="Kansas City Chiefs")))
    assert entity is not None and entity.id == "nfl:game:77"
    games = [p for p, q in wire.sent if p == "/nfl/v1/games"]
    assert games and "dates[]" in wire.sent[1][1]
    facts = run(source.fetch(entity))
    assert facts.status == live_facts.IN_PROGRESS
    assert facts.facts[0] == "Kansas City Chiefs lead Buffalo Bills 21 to 14."
    assert ("The Kansas City Chiefs have won 5 and lost 1 this season, per the "
            "league's standings.") in facts.facts
    assert "The Kansas City Chiefs are seeded 1st." in facts.facts


def test_no_game_in_the_window_or_the_week_is_no_game(wire, monkeypatch):
    use(monkeypatch)
    wire.routes["/nfl/v1/teams"] = {"data": [CHIEFS]}
    entity = run(bs.ScoreBoard().resolve(brief("Kansas City Chiefs", entity="Kansas City Chiefs")))
    assert entity is None


def test_a_named_slot_takes_only_a_game_in_its_slot(wire, monkeypatch):
    use(monkeypatch)
    import named_slots

    wire.routes["/nfl/v1/teams"] = {"data": [CHIEFS, BILLS]}
    afternoon = game("final", start=NOW.replace(hour=17, minute=0))
    wire.routes["/nfl/v1/games"] = {"data": [afternoon]}
    monkeypatch.setattr(named_slots, "in_slot", lambda slot, start: False)
    entity = run(bs.ScoreBoard().resolve(brief("Sunday Night Football recap", slot="snf")))
    assert entity is None


def test_a_formula_one_weekend_and_its_podium(wire, monkeypatch):
    use(monkeypatch)
    event = {"id": 9, "name": "United States Grand Prix", "status_state": "final",
             "start_date": (NOW - timedelta(days=2)).date().isoformat(),
             "end_date": (NOW - timedelta(days=1)).date().isoformat(),
             "circuit": {"name": "Circuit of the Americas"}}
    wire.routes["/f1/v1/events"] = {"data": [event]}
    wire.routes["/f1/v1/sessions"] = {"data": [{"id": 90, "type": "Race"}]}
    wire.routes["/f1/v1/session_results"] = {"data": [
        {"position": 2, "driver": {"first_name": "Lando", "last_name": "Norris"}},
        {"position": 1, "driver": {"first_name": "Max", "last_name": "Verstappen"}},
        {"position": 3, "driver": {"first_name": "Charles", "last_name": "Leclerc"}}]}
    source = bs.ScoreBoard()
    entity = run(source.resolve(brief("who won the F1 race")))
    facts = run(source.fetch(entity))
    assert facts.facts == ["The United States Grand Prix has finished.",
                           "The podium: 1st Max Verstappen, 2nd Lando Norris, "
                           "3rd Charles Leclerc."]


def test_a_ufc_card_and_its_main_event(wire, monkeypatch):
    use(monkeypatch)
    event = {"id": 4, "name": "UFC 320", "status_state": "final",
             "date": NOW.date().isoformat(), "league": {"abbreviation": "UFC"}}
    wire.routes["/mma/v1/events"] = {"data": [event]}
    wire.routes["/mma/v1/fights"] = {"data": [{
        "is_main_event": True, "status_state": "final",
        "fighter1": {"id": 1, "name": "Alex Pereira"},
        "fighter2": {"id": 2, "name": "Magomed Ankalaev"},
        "winner": {"id": 1, "name": "Alex Pereira"},
        "weight_class": {"name": "Light Heavyweight"},
        "result_method": "KO/TKO", "result_round": 1}]}
    source = bs.ScoreBoard()
    entity = run(source.resolve(brief("UFC tonight")))
    facts = run(source.fetch(entity))
    assert "Alex Pereira beat Magomed Ankalaev by ko/tko in round 1." in facts.facts


def test_standings_shapes_say_a_place():
    league = balldontlie.LEAGUES["epl"]
    said = bs.standing_facts(league, {"id": 40, "name": "Liverpool"},
                             {"wins": 6, "losses": 1, "draws": 1, "rank": 1,
                              "points": 19, "group_name": "Premier League"})
    assert said == ["The Liverpool have won 6 and lost 1, with 1 drawn this "
                    "season, per the league's standings.",
                    "The Liverpool are 1st in the Premier League with 19 points."]


# --------------------------------------------------------------------------
# The switch and the sweep
# --------------------------------------------------------------------------
def test_the_balldontlie_key_is_the_sports_provider(monkeypatch):
    use(monkeypatch, live_sports_provider="")
    assert live_sources.configured()["sports"] == "balldontlie"
    # §280: BALLDONTLIE is the only sports provider; no key, no scoreboard.
    use(monkeypatch, balldontlie_api_key="", live_sports_provider="")
    assert live_sources.configured()["sports"] == ""
    assert set(live_sources.BUILDERS["sports"]) == {"fake", "balldontlie"}


def test_one_sports_sweep_the_configured_provider_s(monkeypatch):
    use(monkeypatch, live_sports_provider="")
    assert bs.make_signals()().diagnose()[0]
    assert "balldontlie" in story_sources.BUILDERS
    use(monkeypatch, live_sports_provider="fake")
    ok, why = bs.make_signals()().diagnose()
    assert not ok and "another provider" in why


def test_the_sweep_puts_today_s_games_on_tiles(wire, monkeypatch):
    use(monkeypatch)
    import stories

    monkeypatch.setattr(bs, "SWEPT", ("nfl",))
    monkeypatch.setattr(stories, "recent_demand", lambda now: True)
    wire.routes["/nfl/v1/games"] = {"data": [game("in_progress", period=None)]}
    signals = run(bs.make_signals()().collect(10))
    assert len(signals) == 1
    assert signals[0].live_line == ("Live · Kansas City Chiefs 21–14 "
                                    "Buffalo Bills")
    assert bs.CARDS["nfl"]  # the lookup reads it before spending a request
