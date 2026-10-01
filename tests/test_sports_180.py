"""§180: ten API-Sports products, a plan per sport, and calls per sport."""

from __future__ import annotations

import asyncio
import dataclasses
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import live_facts  # noqa: E402
import live_sources as LS  # noqa: E402
import provider_usage  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
WANTED = ("american-football", "football", "basketball", "baseball", "hockey",
          "rugby", "volleyball", "formula-1", "mma", "afl")


def settings(monkeypatch, **values):
    patched = dataclasses.replace(config.settings, **values)
    monkeypatch.setattr(LS, "settings", patched)
    monkeypatch.setattr(config, "settings", patched)
    return patched


# --- the ten products --------------------------------------------------------

def test_every_sport_the_owner_asked_for_is_wired_with_its_own_host():
    assert set(WANTED) <= set(LS.SPORTS)
    hosts = [LS.SPORTS[k].host for k in WANTED]
    assert len(set(hosts)) == len(hosts)
    assert all(LS.SPORTS[k].label for k in WANTED)
    assert LS.enabled_sports() == list(LS.SPORTS)


@pytest.mark.parametrize("subject,sport", [
    ("who won the monaco grand prix", "formula-1"),
    ("f1 standings", "formula-1"),
    ("ufc 300 main event", "mma"),
    ("maple leafs nhl game", "hockey"),
    ("six nations rugby", "rugby"),
    ("afl grand final", "afl"),
    ("volleyball superlega", "volleyball"),
    ("premier league table", "football"),
])
def test_a_named_sport_routes_to_its_product(subject, sport):
    assert LS.sport_for(subject).key == sport


def test_sport_words_match_whole_words_only():
    # "afl" inside "waffle" and "f1" inside "f150" are not sports.
    assert LS.sport_for("the waffle house f150").key == "american-football"


def test_a_team_in_a_cached_catalogue_names_its_sport(monkeypatch):
    LS.TEAMS["hockey"] = (1e12, [{"id": 1, "name": "Toronto Maple Leafs",
                                  "city": "Toronto"}])
    assert LS.sport_for("how did the maple leafs do").key == "hockey"


def test_only_enabled_sports_are_routed(monkeypatch):
    settings(monkeypatch, api_sports_sports="american-football,basketball")
    assert LS.enabled_sports() == ["american-football", "basketball"]
    assert LS.sport_for("monaco grand prix").key == "american-football"


def test_seasons_are_named_the_way_each_api_names_them():
    oct_ = datetime(2026, 10, 15, tzinfo=timezone.utc)
    feb = datetime(2027, 2, 15, tzinfo=timezone.utc)
    assert LS.season_of(LS.SPORTS["basketball"], oct_) == "2026-2027"
    assert LS.season_of(LS.SPORTS["basketball"], feb) == "2026-2027"
    assert LS.season_of(LS.SPORTS["american-football"], feb) == "2026"
    assert LS.season_of(LS.SPORTS["baseball"], oct_) == "2026"
    assert LS.season_of(LS.SPORTS["hockey"], feb) == "2026"


# --- a plan per sport ---------------------------------------------------------

def test_everything_is_on_the_free_plan_by_default():
    for key in WANTED:
        assert LS.tier_of(key) == "free"
        assert LS.daily_allowance(key) == 100


def test_a_sport_can_be_moved_to_its_own_plan(monkeypatch):
    settings(monkeypatch, api_sports_tiers="hockey=pro, football = ultra")
    assert LS.tier_of("hockey") == "pro" and LS.daily_allowance("hockey") == 7_500
    assert LS.tier_of("football") == "ultra" and LS.next_tier("football") == "mega"
    assert LS.tier_of("mma") == "free"


def test_an_unknown_plan_or_sport_is_never_read_upward(monkeypatch):
    settings(monkeypatch, api_sports_tiers="hockey=platinum,curling=pro",
             api_sports_tier="gold")
    assert LS.tier_of("hockey") == "free"


def test_a_ceiling_setting_caps_a_sport_below_its_plan(monkeypatch):
    settings(monkeypatch, api_sports_tiers="hockey=pro",
             api_sports_daily_requests=2000)
    assert LS.daily_allowance("hockey") == 2000
    assert LS.daily_allowance("rugby") == 100


def test_each_sport_spends_its_own_day(monkeypatch):
    settings(monkeypatch, api_sports_key="k")

    async def fake(url, headers, params, timeout):
        return {"response": []}
    monkeypatch.setattr(LS, "_json", fake)
    asyncio.run(LS.api_sports_json(f"{LS.SPORTS['hockey'].host}/games", {}, 1))
    asyncio.run(LS.api_sports_json(f"{LS.SPORTS['hockey'].host}/games", {}, 1))
    asyncio.run(LS.api_sports_json(f"{LS.SPORTS['mma'].host}/fights", {}, 1))
    assert LS.budget_for("hockey").used == 2
    assert LS.budget_for("mma").used == 1
    assert LS.budget_for("rugby").used == 0
    # A spent sport refuses only itself.
    LS.budget_for("hockey").exhaust()
    with pytest.raises(LS.BudgetSpent, match="Hockey"):
        asyncio.run(LS.api_sports_json(f"{LS.SPORTS['hockey'].host}/games", {}, 1))
    asyncio.run(LS.api_sports_json(f"{LS.SPORTS['rugby'].host}/games", {}, 1))


def test_the_providers_own_count_holds_across_workers():
    budget = LS.budget_for("hockey")
    budget.spend(3)
    # Another worker spent most of the day: API-Sports says 10 are left.
    budget.note_reported(100, 10)
    budget.spend(4)
    assert budget.remaining() == 6
    assert LS.budgets_report()["hockey"]["provider_remaining"] == 10


def test_a_plan_api_sports_does_not_report_is_flagged(monkeypatch):
    settings(monkeypatch, api_sports_tiers="hockey=pro")

    class H(dict):
        pass
    LS._note_quota(f"{LS.SPORTS['hockey'].host}/games",
                   H({"x-ratelimit-requests-limit": "100",
                      "x-ratelimit-requests-remaining": "90"}))
    assert LS.budgets_report()["hockey"]["mismatch"] is True
    assert LS.budgets_report()["rugby"]["mismatch"] is False


# --- calls per sport on /admin -----------------------------------------------

def test_admin_counts_each_sport_under_api_sports(tmp_path):
    provider_usage.reset(provider_usage.UsageStore(str(tmp_path / "u.db")))
    provider_usage.record("api_sports", detail="hockey")
    provider_usage.record("api_sports", detail="hockey")
    provider_usage.record("api_sports", ok=False, detail="mma")
    rows = {r["provider"]: r for r in provider_usage.report()}
    assert rows["api_sports"]["today"] == 3
    per = {b["sport"]: b for b in rows["api_sports"]["breakdown"]}
    assert per["hockey"]["today"] == 2
    assert per["mma"]["today"] == 1 and per["mma"]["failed_today"] == 1
    assert per["rugby"]["today"] == 0
    assert per["hockey"]["limit"] == "Free: 100/day"
    assert "Pro $19/mo" in per["hockey"]["next"]
    provider_usage.reset()


def test_a_request_is_counted_against_its_sport_where_it_goes_out(monkeypatch, tmp_path):
    provider_usage.reset(provider_usage.UsageStore(str(tmp_path / "u.db")))
    import httpx

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            return httpx.Response(200, json={"response": []},
                                  headers={"x-ratelimit-requests-remaining": "42"},
                                  request=httpx.Request("GET", url))
    monkeypatch.setattr(LS.httpx, "AsyncClient", Client)
    asyncio.run(LS._json(f"{LS.SPORTS['afl'].host}/games", {}, {}, 1))
    per = {b["sport"]: b for b in
           {r["provider"]: r for r in provider_usage.report()}["api_sports"]["breakdown"]}
    assert per["afl"]["today"] == 1
    assert LS.budget_for("afl").reported_remaining == 42
    provider_usage.reset()


def test_the_admin_page_draws_a_row_per_sport():
    page = (ROOT / "admin_ui" / "tracker.html").read_text()
    assert "r.breakdown" in page and "subrow" in page
    assert "API_SPORTS_TIERS" in page


# --- shapes: fights, races, and scores that are not `total` ------------------

ENTITY = live_facts.Entity(domain="sports", provider="API-Sports", id="x:1", label="x")


def test_a_finished_fight_names_only_the_marked_winner():
    row = {"id": 5, "status": {"short": "FT"}, "category": "Lightweight",
           "fighters": {"first": {"name": "A Smith", "winner": False},
                        "second": {"name": "B Jones", "winner": True}}}
    facts = LS.ApiSportsSource().to_facts(row, ENTITY, LS.SPORTS["mma"])
    assert facts.facts[0] == "B Jones beat A Smith."
    unmarked = dict(row, fighters={"first": {"name": "A Smith"},
                                   "second": {"name": "B Jones"}})
    text = " ".join(LS.ApiSportsSource().to_facts(
        unmarked, ENTITY, LS.SPORTS["mma"]).facts)
    assert "beat" not in text and "names no winner" in text


def test_a_finished_race_says_its_podium_and_a_future_one_its_start():
    when = datetime(2026, 5, 24, 13, 0, tzinfo=timezone.utc).isoformat()
    row = {"id": 9, "competition": {"name": "Monaco Grand Prix"},
           "circuit": {"name": "Circuit de Monaco"}, "date": when,
           "status": "Completed",
           "podium": [("Driver One", "Team A"), ("Driver Two", "Team B"),
                      ("Driver Three", "Team C")]}
    facts = LS.ApiSportsSource().to_facts(row, ENTITY, LS.SPORTS["formula-1"])
    assert facts.status == live_facts.FINAL
    assert facts.facts[0] == ("Driver One won the Monaco Grand Prix at Circuit de "
                              "Monaco for Team A, ahead of Driver Two and Driver Three.")
    ahead = dict(row, status="Scheduled", podium=[])
    ahead_facts = LS.ApiSportsSource().to_facts(ahead, ENTITY, LS.SPORTS["formula-1"])
    assert ahead_facts.status == live_facts.SCHEDULED
    assert "has not started" in ahead_facts.facts[0]
    # An unknown race status says nothing at all.
    assert LS.ApiSportsSource().to_facts(
        dict(row, status="Postponed"), ENTITY, LS.SPORTS["formula-1"]) is None


def test_a_race_is_found_on_the_season_calendar_by_its_place(monkeypatch):
    now = datetime.now(timezone.utc)
    races = [
        {"id": 1, "type": "Race", "status": "Completed",
         "competition": {"name": "Monaco Grand Prix",
                         "location": {"country": "Monaco", "city": "Monte Carlo"}},
         "date": (now - timedelta(days=30)).isoformat()},
        {"id": 2, "type": "Race", "status": "Scheduled",
         "competition": {"name": "Italian Grand Prix",
                         "location": {"country": "Italy", "city": "Monza"}},
         "date": (now + timedelta(days=5)).isoformat()},
    ]

    async def fake(url, params, timeout):
        assert params.get("type") == "Race"
        return {"response": races}
    monkeypatch.setattr(LS, "api_sports_json", fake)
    source = LS.ApiSportsSource()
    brief = type("B", (), {"subject": "who won in monaco f1", "query": "",
                           "named_slot": ""})()
    assert asyncio.run(source.resolve(brief)).id == "formula-1:1"
    brief.subject = "next f1 race"
    assert asyncio.run(source.resolve(brief)).id == "formula-1:2"


@pytest.mark.parametrize("sport,scores,expected", [
    ("hockey", {"home": 3, "away": 2}, (3, 2)),
    ("afl", {"home": {"score": 85, "goals": 12, "behinds": 13},
             "away": {"score": 70, "goals": 10, "behinds": 10}}, (85, 70)),
    ("volleyball", {"home": 3, "away": 1}, (3, 1)),
])
def test_each_score_shape_is_read(sport, scores, expected):
    assert LS.ApiSportsSource._score({"scores": scores}) == expected


def test_a_hockey_game_in_the_second_period_is_under_way():
    row = {"game": {"id": 1, "status": {"short": "P2"}},
           "teams": {"home": {"name": "Toronto Maple Leafs"},
                     "away": {"name": "Boston Bruins"}},
           "scores": {"home": 2, "away": 1}}
    facts = LS.ApiSportsSource().to_facts(row, ENTITY, LS.SPORTS["hockey"])
    assert facts.status == live_facts.IN_PROGRESS
    assert "lead" in facts.facts[0] and "beat" not in facts.facts[0]


# --- review fixes --------------------------------------------------------------

@pytest.mark.parametrize("subject,sport", [
    ("rugby world cup final", "rugby"),
    ("fiba basketball world cup", "basketball"),
    ("volleyball champions league", "volleyball"),
    ("world cup qualifier", "football"),
])
def test_a_shared_event_name_goes_to_the_sport_that_is_named(subject, sport):
    assert LS.sport_for(subject).key == sport


def test_a_practice_session_is_never_the_race():
    row = {"id": 3, "type": "1st Practice", "status": "Completed",
           "competition": {"name": "Monaco Grand Prix"}}
    assert LS.ApiSportsSource.live_line(row, LS.SPORTS["formula-1"]) == \
        (live_facts.UNKNOWN, "")
    race = dict(row, type="Race")
    assert LS.ApiSportsSource.live_line(race, LS.SPORTS["formula-1"])[0] == \
        live_facts.FINAL


def test_the_new_sports_lead_like_major_leagues():
    assert LS.is_major("NHL", "USA") and LS.is_major("AFL", "Australia")
    assert LS.is_major_event(LS.SPORTS["formula-1"], {})
    assert LS.is_major_event(LS.SPORTS["mma"], {"slug": "UFC 320: A vs B"})
    assert not LS.is_major_event(LS.SPORTS["mma"], {"slug": "Regional Cage 4"})


def test_a_fighter_is_matched_by_whole_name_and_yesterday_is_tried(monkeypatch):
    calls = []
    yesterday = [{"id": 7, "status": {"short": "FT"},
                  "fighters": {"first": {"name": "Jon Jones"},
                               "second": {"name": "Max Williams"}}}]

    async def fake(url, params, timeout):
        calls.append(params.get("date"))
        return {"response": [] if len(calls) == 1 else yesterday}
    monkeypatch.setattr(LS, "api_sports_json", fake)
    source = LS.ApiSportsSource()
    found = asyncio.run(source._resolve_fight(LS.SPORTS["mma"], "who won the jones fight"))
    assert found.id == "mma:7" and len(calls) == 2
    calls.clear()
    # "will" is not "Williams".
    assert asyncio.run(source._resolve_fight(LS.SPORTS["mma"], "who will win tonight")) is None


def test_one_failing_sport_is_said_while_others_answer(monkeypatch):
    import story_sources

    settings(monkeypatch, api_sports_key="k",
             stories_sports="american-football,hockey")
    monkeypatch.setattr(story_sources, "settings", config.settings)

    async def fake(url, headers, params, timeout):
        if "hockey" in url:
            raise RuntimeError("not subscribed")
        return {"response": []}
    monkeypatch.setattr(LS, "_json", fake)
    try:
        asyncio.run(story_sources.ApiSportsSignals().collect(8))
    except RuntimeError:
        pass  # nothing answered with a game at all: an outage, raised
    assert "hockey" in story_sources.SPORT_FAILURES
    assert "american-football" not in story_sources.SPORT_FAILURES
    ok, why = story_sources.ApiSportsSignals().diagnose()
    assert "hockey FAILING" in why


def test_a_finished_races_podium_is_read_once(monkeypatch):
    calls = []

    async def fake(url, params, timeout):
        calls.append(url)
        return {"response": [{"position": 1, "driver": {"name": "D1"},
                              "team": {"name": "T1"}}]}
    monkeypatch.setattr(LS, "api_sports_json", fake)
    source = LS.ApiSportsSource()
    asyncio.run(source._podium(LS.SPORTS["formula-1"], "9"))
    asyncio.run(source._podium(LS.SPORTS["formula-1"], "9"))
    assert len(calls) == 1


def test_an_old_daily_setting_above_the_plan_is_explained(monkeypatch, caplog):
    settings(monkeypatch, api_sports_daily_requests=7500)
    with caplog.at_level("ERROR"):
        assert LS.daily_allowance("american-football") == 100
    assert "API_SPORTS_TIERS" in caplog.text
