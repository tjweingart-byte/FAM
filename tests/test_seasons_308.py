"""§308: a league's time of year chooses examples, worked out in code.

Pinned here: every calendar is a league the tree knows, and every phase it
names is one of the four; a team takes its league's calendar; the year wraps
(the NFL's January is its postseason); an example tagged for one phase is
never shown in another, and one tagged for this phase is preferred; a node
with no league is unchanged; and the phase is kept beside the episode's
quality.
"""
from __future__ import annotations

import datetime
import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories as C  # noqa: E402
import category_seed  # noqa: E402
import episode_intelligence as ei  # noqa: E402
import episode_quality as Q  # noqa: E402
import script_generator as sg  # noqa: E402
import seasons  # noqa: E402
import topics as T  # noqa: E402
from script_generator import plan_episode  # noqa: E402


@pytest.fixture
def tree():
    t = T.category_tree()
    C.apply_seed(t)
    return t


def on(monkeypatch, year, month, day):
    monkeypatch.setattr(seasons, "today", lambda: datetime.date(year, month, day))


def test_every_calendar_is_a_league_the_tree_knows():
    nodes = {child for child, _parent in category_seed.rows()}
    for league, calendar in seasons.CALENDARS.items():
        assert league in nodes, league
        assert {phase for _m, _d, phase in calendar} <= set(seasons.PHASES)
        assert "offseason" in {phase for _m, _d, phase in calendar}, league


@pytest.mark.parametrize("day,phase", [
    ((2026, 1, 5), "regular_season"),      # last year's season, still running
    ((2026, 1, 20), "postseason"),
    ((2026, 4, 1), "offseason"),
    ((2026, 8, 10), "preseason"),
    ((2026, 10, 24), "regular_season"),
])
def test_the_nfl_year_including_its_wrap(day, phase):
    assert seasons.phase_on("nfl", datetime.date(*day)) == phase


def test_a_team_takes_its_leagues_calendar_and_others_have_none(monkeypatch):
    on(monkeypatch, 2026, 4, 1)
    assert seasons.phase_for(("philadelphia eagles", "nfl", "american football",
                              "sports")) == ("nfl", "offseason")
    assert seasons.phase_for(("interest rates", "central banks", "money")) == ("", "")
    assert seasons.phase_for(()) == ("", "")


def _choice(query, subject, shape, minutes=2):
    plan = plan_episode(query, minutes, search=False)
    plan.brief = ei.Brief(query=query, subject=subject, structure=shape)
    return sg.choose_examples(plan)


def test_an_in_season_example_is_never_shown_out_of_season(tree, monkeypatch):
    sprint = "2-singapore-sprint.txt"
    on(monkeypatch, 2026, 1, 20)          # formula one is out of season
    off = _choice("the monaco grand prix", "the Monaco Grand Prix in formula one",
                  "sports_recap")
    assert (off.league, off.phase) == ("formula one", "offseason")
    assert sprint not in off.files
    on(monkeypatch, 2026, 6, 1)           # and in season
    in_season = _choice("the monaco grand prix",
                        "the Monaco Grand Prix in formula one", "sports_recap")
    assert in_season.phase == "regular_season"
    assert sprint in in_season.files


def test_an_example_for_this_phase_is_preferred(tree, monkeypatch):
    on(monkeypatch, 2026, 6, 1)
    # A question no shape singles out still gets the in-season example.
    choice = _choice("formula one this weekend", "formula one", "general")
    assert choice.files and "2-singapore-sprint.txt" in choice.files


def test_a_node_with_no_league_is_unchanged(tree, monkeypatch):
    on(monkeypatch, 2026, 1, 20)
    choice = _choice("what is the federal reserve going to do",
                     "the federal reserve and interest rates", "explainer")
    assert choice.phase == "" and "2-fed-raises-rates.txt" in choice.files


def test_the_phase_reaches_the_notes_and_the_quality_row(tree, monkeypatch):
    on(monkeypatch, 2026, 6, 1)
    plan = plan_episode("the monaco grand prix", 2, search=False)
    plan.brief = ei.Brief(query=plan.query,
                          subject="the Monaco Grand Prix in formula one",
                          structure="sports_recap")
    notes = sg.ScriptNotes()
    sg.ScriptGenerator.__new__(sg.ScriptGenerator)._request_kwargs(plan, notes)
    assert notes.phase == "regular_season"
    key = hashlib.sha256(b"k").hexdigest()
    Q.note(key, 1000, plan.query, 2, "search",
           ["Lando Norris won at Monaco on Sunday."], notes)
    assert Q.store().get(key, 1000)["phase"] == "regular_season"


def test_the_phase_is_never_told_to_the_writer(tree, monkeypatch):
    """It chooses examples; it is not evidence and not a fact (§308)."""
    on(monkeypatch, 2026, 6, 1)
    plan = plan_episode("the monaco grand prix", 2, search=False)
    plan.brief = ei.Brief(query=plan.query, subject="formula one",
                          structure="sports_recap")
    kwargs = sg.ScriptGenerator.__new__(sg.ScriptGenerator)._request_kwargs(plan)
    sent = str(kwargs["messages"]) + str(kwargs["system"])
    assert "regular_season" not in sent and "regular season" not in sent.lower()
    assert "# phase" not in sent


# --- measuring it: the comparison run and the episodes to score -----------------


def _ep(name, origin, script, labelled=False):
    key = hashlib.sha256(name.encode()).hexdigest()
    notes = sg.ScriptNotes()
    notes.category = "formula one"
    Q.note(key, 1000, "the monaco grand prix", 1, origin, script, notes)
    if labelled:
        assert Q.label(f"{key}.1000", "hook", 4)
    return key


GOOD = ["Lando Norris won the Monaco Grand Prix on Sunday.",
        "He won it because McLaren pitted him a lap before Ferrari blinked."]
WEAK = ["It was a race.", "So, to sum up, it happened?"]


def test_the_two_arms_are_reported_side_by_side():
    _ep("a1", "eval-all", WEAK)
    _ep("a2", "eval-node", GOOD)
    origins = Q.report(resolve=lambda w: w, ancestors=lambda n: ["sports"],
                       label_of=str)["origins"]
    assert set(origins) == {"eval-all", "eval-node"}
    assert origins["eval-node"]["overall"] > origins["eval-all"]["overall"]
    assert origins["eval-all"]["code"]["storytelling"] < 1.0
    assert origins["eval-all"]["grade"]["hook"] is None


def test_the_episodes_to_score_are_the_ones_not_scored_yet():
    done = _ep("b1", "eval-node", GOOD, labelled=True)
    todo = _ep("b2", "eval-all", GOOD)
    _ep("b3", "search", GOOD)
    keys = [r["key"] for r in Q.store().unlabelled(10)]
    assert todo in keys and done not in keys and len(keys) == 2
    only = Q.store().unlabelled(10, ["eval-all", "eval-node"])
    assert [r["key"] for r in only] == [todo]


def test_the_admin_lists_them(monkeypatch):
    from fastapi.testclient import TestClient

    import app as appmod

    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "secret")
    client = TestClient(appmod.app)
    assert client.get("/api/admin/quality/to-score").status_code == 404
    admin = {"X-Admin-Token": "secret"}
    assert client.get("/api/admin/quality/to-score",
                      headers=admin).json() == {"episodes": []}
    key = _ep("c1", "eval-node", GOOD)
    body = client.get("/api/admin/quality/to-score?origin=eval-all,eval-node",
                      headers=admin).json()
    assert [e["episode"] for e in body["episodes"]] == [f"{key}.1000"]
    assert body["episodes"][0]["origin"] == "eval-node"


def test_the_comparison_refuses_without_a_key_and_on_staging(monkeypatch, capsys):
    import asyncio

    import credentials
    import spend_guard

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "tools"))
    import quality_compare

    monkeypatch.setattr(spend_guard, "enabled", lambda: False)
    monkeypatch.setattr(credentials, "active", lambda name: "")
    assert asyncio.run(quality_compare.run(["anything"], 1, False)) == 1
    assert "no ANTHROPIC_API_KEY" in capsys.readouterr().out
    monkeypatch.setattr(spend_guard, "enabled", lambda: True)
    assert asyncio.run(quality_compare.run(["anything"], 1, False)) == 1
    assert "zero-spend" in capsys.readouterr().out
    # Its ids are episode ids the admin can open, never a cache key's text.
    episode = quality_compare._key("run", "eval-all", "q", 1)
    import cache

    assert cache.parse_episode_id(f"{episode}.1") is not None
    assert episode != quality_compare._key("run", "eval-node", "q", 1)
