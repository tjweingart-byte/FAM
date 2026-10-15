"""§281: the review of BALLDONTLIE's scoreboard, sweep and player reports.

Each test is one finding, reproduced first: a swept card reused past the
score's freshness, a sweep that spent the free plan's minute at once, a
player's season figures vouching for any score, a named slot's fallback
outside the slot, injury reports and schedules read one page deep, the World
Cup asked below GOAT, a schedule row stamped "now", and a 401 read as a plan
refusal. Every request goes through `live_sources._json`, stubbed. No network.
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

import balldontlie  # noqa: E402
import balldontlie_scores as bs  # noqa: E402
import config  # noqa: E402
import live_facts  # noqa: E402
import live_sources  # noqa: E402
import named_slots  # noqa: E402
import score_guard  # noqa: E402
import stories  # noqa: E402
import story_sources  # noqa: E402

NOW = datetime.now(timezone.utc)
CHIEFS = {"id": 12, "full_name": "Kansas City Chiefs", "name": "Chiefs"}
BILLS = {"id": 3, "full_name": "Buffalo Bills", "name": "Bills"}
JOKIC = {"id": 246, "first_name": "Nikola", "last_name": "Jokic",
         "position": "C", "team": {"id": 8, "full_name": "Denver Nuggets"}}


def use(monkeypatch, **changes):
    changes.setdefault("balldontlie_api_key", "k")
    changes.setdefault("balldontlie_tier", "goat")
    changes.setdefault("live_sports_provider", "")
    patched = dataclasses.replace(config.settings, **changes)
    for module in (balldontlie, bs, live_sources, story_sources, live_facts):
        monkeypatch.setattr(module, "settings", patched)
    return patched


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
        if isinstance(answer, BaseException):
            raise answer
        return answer if answer is not None else {"data": []}

    monkeypatch.setattr(live_sources, "_json", fake_json)
    return SimpleNamespace(sent=sent, routes=routes)


def game(state, gid=77, start=None, hs=21, as_=14):
    return {"id": gid, "home_team": CHIEFS, "visitor_team": BILLS,
            "home_team_score": hs, "visitor_team_score": as_,
            "status_state": state,
            "date": (start or NOW - timedelta(hours=1)).isoformat()}


def brief(text, entity="", slot=""):
    return SimpleNamespace(live_entity=entity, subject=text, query=text,
                           live_domain="sports", live_route="", category="",
                           named_slot=slot)


def run(coro):
    return asyncio.run(coro)


def games_asked(wire, prefix="/nfl/v1/games"):
    return [p for p, q in wire.sent if p == prefix and "dates[]" in q]


# --------------------------------------------------------------------------
# 1. A swept card answers only while a score off it is still fresh
# --------------------------------------------------------------------------
def test_a_card_older_than_a_score_may_be_is_asked_again(wire, monkeypatch):
    use(monkeypatch)
    assert bs.CARD_REUSE_SECONDS < live_facts.MAX_AGE_SECONDS["sports"]
    day = bs._day_of(game("in_progress"))  # noqa: SLF001
    bs.TEAMS["nfl"] = (time.time(), {12: CHIEFS, 3: BILLS})
    wire.routes["/nfl/v1/games"] = {"data": [game("in_progress")]}

    bs.CARDS["nfl"] = {day: (time.time() - 60, [game("in_progress")])}
    rows, at = run(bs.ScoreBoard()._window_read(  # noqa: SLF001
        balldontlie.LEAGUES["nfl"], [12]))
    assert rows and not games_asked(wire), "a fresh card was not reused"

    # Three minutes old: every score on it would be withheld as stale.
    bs.CARDS["nfl"] = {day: (time.time() - 180, [game("in_progress")])}
    source = bs.ScoreBoard()
    entity = run(source.resolve(brief("Chiefs Bills", entity="Kansas City Chiefs")))
    facts = run(source.fetch(entity))
    assert games_asked(wire), "a stale card answered the lookup"
    assert facts.is_fresh()


# --------------------------------------------------------------------------
# 2. The plan's minute: the sweep, one question, one catalogue
# --------------------------------------------------------------------------
def test_the_free_plan_does_not_sweep_and_says_so(wire, monkeypatch):
    use(monkeypatch, balldontlie_tier="free")
    monkeypatch.setattr(stories, "recent_demand", lambda now: True)
    source = bs.make_signals()(client=balldontlie.Client())
    assert "on demand" in source.idle(time.time())
    ok, why = source.diagnose()
    assert not ok and "on demand only" in why
    assert run(source.collect(10)) == [] and wire.sent == []


def test_a_paid_sweep_asks_one_league_at_a_time_and_keeps_room(wire, monkeypatch):
    use(monkeypatch, balldontlie_tier="all-star")
    monkeypatch.setattr(stories, "recent_demand", lambda now: True)
    monkeypatch.setattr(bs, "SWEPT", ("nfl", "nba", "nhl"))
    in_flight, most = [0], [0]

    async def fake_json(url, headers, params, timeout):
        in_flight[0] += 1
        most[0] = max(most[0], in_flight[0])
        await asyncio.sleep(0)
        in_flight[0] -= 1
        wire.sent.append((url, params))
        return {"data": [game("in_progress")]}

    monkeypatch.setattr(live_sources, "_json", fake_json)
    client = balldontlie.Client()
    out = run(bs.make_signals()(client=client).collect(10))
    assert out and most[0] == 1 and len(wire.sent) == 3

    # A minute nearly spent: the sweep stops while a question still fits,
    # and having read nothing it is an outage, not an empty card.
    for key in bs.SWEPT:
        bs.SWEPT_AT.pop(key, None)
    bs.CARDS.clear()
    busy = balldontlie.Client()
    for _ in range(balldontlie.PER_MINUTE["all-star"] - bs.SWEEP_RESERVE):
        busy.pace()
    with pytest.raises(balldontlie.RateLimited):
        run(bs.make_signals()(client=busy).collect(10))
    assert busy.spare() == bs.SWEEP_RESERVE, "the sweep spent the listeners' room"


def test_one_question_reads_its_window_once(wire, monkeypatch):
    use(monkeypatch, balldontlie_tier="free")
    wire.routes["/nfl/v1/teams"] = {"data": [CHIEFS, BILLS]}
    wire.routes["/nfl/v1/games"] = lambda p: {"data": [game("in_progress")]}
    source = bs.ScoreBoard()
    entity = run(source.resolve(brief("Chiefs against the Bills",
                                      entity="Kansas City Chiefs")))
    facts = run(source.fetch(entity))
    assert facts.facts[0] == "Kansas City Chiefs lead Buffalo Bills 21 to 14."
    assert len(games_asked(wire)) == 1, "fetch asked again for resolve's window"
    # Teams, the window and two schedules: inside the free plan's five.
    assert len(wire.sent) <= balldontlie.PER_MINUTE["free"]


def test_player_reports_read_the_scoreboard_s_catalogue(wire, monkeypatch):
    use(monkeypatch)
    wire.routes["/nfl/v1/teams"] = {"data": [CHIEFS, BILLS]}
    client = balldontlie.Client()
    run(bs.league_teams(client, balldontlie.LEAGUES["nfl"]))
    reports = balldontlie.PlayerReports(client=client)
    teams = run(reports.teams(balldontlie.LEAGUES["nfl"]))
    assert {t["id"] for t in teams} == {12, 3}
    assert [p for p, _ in wire.sent].count("/nfl/v1/teams") == 1


# --------------------------------------------------------------------------
# 3. A player's season figures are not scores
# --------------------------------------------------------------------------
def _guard_with_report(sentences, live=None):
    report = live_facts.LiveFacts(domain="sports", source="x", as_of=NOW,
                                  facts=sentences, kind=live_facts.PLAYER_REPORT)
    plan = SimpleNamespace(
        brief=SimpleNamespace(live_domain="sports"), live=live,
        players=live_facts.LiveLookup("sports", live_facts.FACTS, facts=report))
    return score_guard.ScoreGuard(plan)


def test_a_season_line_never_vouches_for_a_score():
    league = balldontlie.LEAGUES["nba"]
    line = balldontlie.basketball_lines(
        league, "Nikola Jokic", {"games_played": 26, "pts": 29.1, "reb": 12.3,
                                 "ast": 10.2, "fg_pct": 0.582},
        "2025-26", True, averages=True)
    # Tonight's game is under way at 54 to 50: who leads may be said, and
    # only the scoreboard's score.
    board = live_facts.LiveFacts(
        domain="sports", source="x", as_of=NOW, status=live_facts.IN_PROGRESS,
        facts=["Denver Nuggets lead Los Angeles Lakers 54 to 50."])
    guard = _guard_with_report(
        line, live=live_facts.LiveLookup("sports", live_facts.FACTS, facts=board))
    assert guard.why_not("The Nuggets lead the Lakers 54 to 50.") == ""
    assert "score 29-26" in guard.why_not("The Nuggets lead the Lakers 29 to 26.")
    assert guard.why_not("Jokic has averaged 29.1 points over 26 games.") == ""


def test_a_record_the_report_states_is_still_vouched_for():
    guard = _guard_with_report(balldontlie.mma_lines(
        {"name": "Jon Jones", "record_wins": 22, "record_losses": 3}))
    assert guard.why_not("Jones is 22-3 as a professional.") == ""
    assert guard.why_not("Jones is 22-4 as a professional.") != ""


# --------------------------------------------------------------------------
# 4. None in the slot is none, the week's schedule included
# --------------------------------------------------------------------------
def test_a_slot_s_schedule_fallback_stays_in_the_slot(wire, monkeypatch):
    use(monkeypatch)
    afternoon = game("final", gid=5, start=NOW - timedelta(days=3))
    wire.routes["/nfl/v1/games"] = lambda p: {
        "data": [] if "dates[]" in p else [afternoon]}
    monkeypatch.setattr(named_slots, "in_slot", lambda slot, start: False)
    row = run(bs.ScoreBoard()._find_game(  # noqa: SLF001
        balldontlie.LEAGUES["nfl"], [CHIEFS], named_slots.by_key("snf"), {}))
    assert row is None
    # Without a slot the week's schedule still finds it.
    row = run(bs.ScoreBoard()._find_game(  # noqa: SLF001
        balldontlie.LEAGUES["nfl"], [CHIEFS], None, {}))
    assert row is not None and row["id"] == 5


# --------------------------------------------------------------------------
# 5, 6. Every page, to a bound; past the bound, no absence
# --------------------------------------------------------------------------
def _paged(pages):
    def answer(params):
        index = int(params.get("cursor") or 0)
        meta = {"next_cursor": index + 1} if index + 1 < len(pages) else {}
        return {"data": pages[index], "meta": meta}
    return answer


def test_the_nhl_injury_report_is_read_past_its_first_page(wire, monkeypatch):
    use(monkeypatch)
    star = {"id": 8478402, "first_name": "Connor", "last_name": "McDavid",
            "teams": [{"id": 22, "full_name": "Edmonton Oilers", "season": 2026}]}
    other = {"player": {"id": 1}, "status": "Out"}
    wire.routes["/nhl/v1/player_injuries"] = _paged(
        [[other] * 100, [{"player": star, "status": "Day-to-day"}]])
    source = balldontlie.PlayerReports()
    lines = run(source._player_injury(  # noqa: SLF001
        balldontlie.LEAGUES["nhl"], star, NOW))
    assert lines and "day-to-day" in lines[0]


def test_absence_is_not_said_when_the_report_was_not_read_to_its_end(wire, monkeypatch):
    use(monkeypatch)
    other = {"player": {"id": 1}, "status": "Out"}
    pages = [[other] * 100] * (balldontlie.INJURY_PAGES + 2)
    wire.routes["/nhl/v1/player_injuries"] = _paged(pages)
    source = balldontlie.PlayerReports()
    star = {"id": 99, "first_name": "Connor", "last_name": "McDavid"}
    lines = run(source._player_injury(  # noqa: SLF001
        balldontlie.LEAGUES["nhl"], star, NOW))
    assert lines == []
    assert len(wire.sent) == balldontlie.INJURY_PAGES


def test_a_season_longer_than_a_page_is_read_whole(wire, monkeypatch):
    use(monkeypatch)
    first = [game("final", gid=i, start=NOW - timedelta(days=200 - i))
             for i in range(100)]
    last = [game("scheduled", gid=500, start=NOW + timedelta(days=2))]
    wire.routes["/mlb/v1/games"] = _paged([first, last])
    rows = run(bs.team_schedule(balldontlie.Client(), balldontlie.LEAGUES["mlb"], 12))
    assert len(rows) == 101 and rows[-1]["id"] == 500


# --------------------------------------------------------------------------
# 7. What the plan cannot reach is not asked, and is not a failure
# --------------------------------------------------------------------------
def test_the_world_cup_is_not_swept_below_goat(wire, monkeypatch):
    use(monkeypatch, balldontlie_tier="all-star")
    monkeypatch.setattr(stories, "recent_demand", lambda now: True)
    monkeypatch.setattr(bs, "SWEPT", ("nfl", "fifa"))
    source = bs.make_signals()(client=balldontlie.Client())
    assert run(source.collect(10)) == []  # a quiet day, not a raise
    assert not any(p.startswith("/fifa/") for p, _ in wire.sent)
    assert bs.FAILURES == {}
    assert "not swept on the all-star plan: fifa" in source.diagnose()[1]


# --------------------------------------------------------------------------
# 8. A schedule row is as old as the schedule
# --------------------------------------------------------------------------
def test_a_game_off_its_schedule_carries_the_schedule_s_time(wire, monkeypatch):
    use(monkeypatch)
    row = game("final", gid=9, start=NOW - timedelta(days=4))
    bs.TEAMS["nfl"] = (time.time(), {12: CHIEFS, 3: BILLS})
    read = time.time() - 30
    bs.SCHEDULES[("nfl", 12)] = (read, [row])
    source = bs.ScoreBoard()
    entity = live_facts.Entity("sports", source.name, "nfl:game:9", "x")
    source._rows[entity.key] = ("nfl", "game", {"row": row, "teams": [12]})  # noqa: SLF001
    facts = run(source.fetch(entity))
    assert abs(facts.as_of.timestamp() - read) < 1

    # A held schedule older than a score may be is read again, and stamped
    # with that read.
    bs.SCHEDULES[("nfl", 12)] = (time.time() - 600, [row])
    wire.routes["/nfl/v1/games"] = lambda p: {"data": [] if "dates[]" in p else [row]}
    facts = run(source.fetch(entity))
    assert facts.is_fresh()


# --------------------------------------------------------------------------
# 9. A 401 on what the plan covers is the account, said as the account
# --------------------------------------------------------------------------
def test_a_401_on_a_covered_endpoint_is_an_account_problem(wire, monkeypatch):
    use(monkeypatch, balldontlie_tier="all-star")
    wire.routes["/nba/v1/player_injuries"] = live_sources.ProviderHTTPError(
        401, "api.balldontlie.io/nba/v1/player_injuries")
    client = balldontlie.Client()
    nba = balldontlie.LEAGUES["nba"]
    with pytest.raises(balldontlie.AccountRefused):
        run(client.get(nba, "injuries", "player_injuries", {}))
    with pytest.raises(balldontlie.AccountRefused):
        run(client.get(nba, "injuries", "player_injuries", {}))
    assert len(wire.sent) == 1, "a refused key was asked again the same day"
    assert "BALLDONTLIE_TIER" in client.problem()
    assert "ACCOUNT REFUSED" in balldontlie.PlayerReports(client=client).diagnose()[1]
    assert "ACCOUNT REFUSED" in bs.ScoreBoard(client=client).diagnose()[1]


def test_a_403_is_still_the_plan_s_refusal(wire, monkeypatch):
    use(monkeypatch, balldontlie_tier="all-star")
    wire.routes["/nba/v1/player_injuries"] = live_sources.ProviderHTTPError(
        403, "api.balldontlie.io/nba/v1/player_injuries")
    client = balldontlie.Client()
    with pytest.raises(balldontlie.TierRefused):
        run(client.get(balldontlie.LEAGUES["nba"], "injuries", "player_injuries", {}))
    assert client.problem() == ""
