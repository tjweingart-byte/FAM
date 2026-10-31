"""C3P0's `ResearchPlan`, built in code from the brief and the code routes
(§366): the plan describes what the episode already asks, in one written
decision, before any model improves it; and how often an empty search had
the archive holding something is counted, never waited for.
"""
from __future__ import annotations

import asyncio
import dataclasses
import socket

import pytest

import research
import script_generator as sg
from agents import contextualizer, handoffs, netenyahu
from episode_intelligence import Brief


@pytest.fixture(autouse=True)
def fresh():
    contextualizer.reset()
    yield
    contextualizer.reset()


def plugins(plan):
    return [(c.plugin, c.query) for c in plan.plugins]


def brief(**kw):
    kw.setdefault("query", kw.get("subject", "q"))
    return Brief(**kw)


# ------------------------------------------------------------------ the plan
def test_every_plan_asks_the_search_ladder_with_the_briefs_search():
    plan = contextualizer.from_brief(brief(subject="rents", search_query="why US rents rose 2026"),
                                     "why rents keep rising")
    assert plugins(plan)[0] == ("search", "why US rents rose 2026")


def test_a_degraded_brief_searches_the_question():
    plan = contextualizer.from_brief(Brief(query="why rents keep rising"), "why rents keep rising")
    assert plugins(plan)[0] == ("search", "why rents keep rising")


def test_a_sports_question_asks_the_scoreboard_and_the_players_by_bare_name():
    plan = contextualizer.from_brief(brief(subject="the Eagles' season", live_domain="sports",
                                           live_entity="Philadelphia Eagles"), "eagles")
    assert ("live_facts", "Philadelphia Eagles") in plugins(plan)
    assert ("players", "Philadelphia Eagles") in plugins(plan)
    assert plan.entity == "Philadelphia Eagles" and plan.entity_kind == "sports"


def test_a_code_route_asks_its_provider_too():
    b = brief(subject="NVIDIA", live_route="markets", live_entity="NVIDIA")
    assert ("live_facts", "NVIDIA") in plugins(contextualizer.from_brief(b, "nvidia"))


def test_a_market_question_asks_no_players():
    b = brief(subject="NVIDIA", live_domain="markets", live_entity="NVIDIA")
    assert "players" not in [p for p, _q in plugins(contextualizer.from_brief(b))]


def test_weather_asks_the_weather_and_no_scoreboard():
    b = brief(subject="weather", live_domain="weather", place="Fair Haven, NJ, USA")
    names = [p for p, _q in plugins(contextualizer.from_brief(b))]
    assert "weather" in names and "live_facts" not in names and "archive" not in names


def test_a_towns_news_asks_the_local_ladder_and_not_the_archive():
    b = brief(subject="Fair Haven news", place="Fair Haven, NJ, USA")
    plan = contextualizer.from_brief(b)
    assert ("local_news", "Fair Haven, NJ, USA") in plugins(plan)
    assert plan.archive is None


def test_an_outcome_question_asks_the_forecast_by_its_market_words():
    b = brief(subject="the Fed", outcome_dependent=True, forecast_query="Fed decision October")
    assert ("forecast", "Fed decision October") in plugins(contextualizer.from_brief(b))


def test_sonic_is_asked_under_the_briefs_node():
    b = brief(subject="the Eagles", category_path=["sports", "american football", "nfl",
                                                   "philadelphia eagles"])
    plan = contextualizer.from_brief(b)
    assert plan.node_id == "philadelphia eagles"
    assert plan.archive.node_id == "philadelphia eagles"
    assert plan.archive.lookup == "background"
    assert ("archive", "the Eagles") in plugins(plan)


def test_the_title_is_the_briefs_until_c3p0s_call_writes_one():
    assert contextualizer.from_brief(brief(subject="x", title="Why Rents Rose")).title == "Why Rents Rose"


def test_only_plugins_from_the_closed_list():
    ok, why = handoffs.validate("PluginCall", {"plugin": "twitter", "query": "x"})
    assert ok is None and "refused" in why
    assert set(handoffs.PLUGINS) == {"search", "live_facts", "players", "forecast",
                                     "local_news", "weather", "archive"}


def test_no_brief_no_plan_and_a_broken_brief_never_raises():
    assert contextualizer.from_brief(None) is None

    class Broken:
        @property
        def retrieval(self):
            raise RuntimeError("no")
    assert contextualizer.from_brief(Broken(), "q") is None
    assert contextualizer.SINCE_BOOT["from_brief_failed"] == 1


def test_building_a_plan_touches_no_socket_or_file(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("I/O building a plan")
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr("builtins.open", refuse)
    plan = contextualizer.from_brief(brief(subject="Eagles", live_domain="sports",
                                           outcome_dependent=True), "eagles")
    assert plan is not None


# ------------------------------------------------------------------ wired into the episode
def _runs(*roles):
    return netenyahu.EpisodeRun(tuple(roles), {})


def test_agent_runs_reads_netenyahus_answer():
    notes = sg.ScriptNotes()
    assert not sg._agent_runs(None, "contextualizer")
    assert not sg._agent_runs(notes, "contextualizer")
    notes.agents = netenyahu.OFF
    assert not sg._agent_runs(notes, "contextualizer")
    notes.agents = _runs("contextualizer")
    assert sg._agent_runs(notes, "contextualizer")


def _prepare(monkeypatch, agents):
    async def research_(self, plan, notes=None):
        return dataclasses.replace(plan, evidence="SOURCE 1\nTitle: x")

    async def understand(self, plan, notes=None):
        return plan
    monkeypatch.setattr(sg.ScriptGenerator, "research", research_)
    monkeypatch.setattr(sg.ScriptGenerator, "understand", understand)
    plan = dataclasses.replace(sg.plan_episode("why rents keep rising", 2),
                               brief=brief(subject="rents", search_query="US rents 2026"))
    notes = sg.ScriptNotes()
    notes.agents = agents
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    asyncio.run(generator.prepare(plan, notes))
    return notes


def test_the_plan_is_kept_on_the_episode_when_c3p0_works_on_it(monkeypatch):
    notes = _prepare(monkeypatch, _runs("contextualizer"))
    assert isinstance(notes.research_plan, handoffs.ResearchPlan)
    assert plugins(notes.research_plan)[0] == ("search", "US rents 2026")


def test_no_plan_when_the_agents_are_off_or_c3p0_is_not_working(monkeypatch):
    assert _prepare(monkeypatch, netenyahu.OFF).research_plan is None
    assert _prepare(monkeypatch, _runs("categorizer")).research_plan is None


# ------------------------------------------------------------------ an empty search and the archive
def test_an_empty_search_counts_whether_the_archive_held_something():
    async def go():
        loop = asyncio.get_running_loop()
        for value in (["a fact"], []):
            fut = loop.create_future()
            fut.set_result(value)
            contextualizer.note_empty_search(fut)
        contextualizer.note_empty_search(None)
        await asyncio.sleep(0)             # done-callbacks run on the loop
    asyncio.run(go())
    report = contextualizer.report()
    assert report["since_boot"]["empty_search"] == 3
    assert report["since_boot"]["empty_search_archive_had"] == 1
    assert report["since_boot"]["empty_search_archive_nothing"] == 1
    assert report["since_boot"]["empty_search_no_recall"] == 1
    assert report["empty_search_archive_had_rate"] == round(1 / 3, 3)


def test_no_empty_search_yet_is_none_not_zero():
    assert contextualizer.report()["empty_search_archive_had_rate"] is None


def _research(monkeypatch, agents, facts):
    import history_archive
    import stories

    async def empty(self, query, brief_, backend, minutes, **kw):
        return research.Packet(backend=backend)
    seen = {}

    def look(text, brief_):
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        loop.call_later(0.05, fut.set_result, facts)
        seen["look"] = fut
        return fut
    monkeypatch.setattr(sg.ScriptGenerator, "_retrieve", empty)
    monkeypatch.setattr(history_archive, "look", look)
    monkeypatch.setattr(research, "ladder", lambda configured=None: [configured])
    monkeypatch.setattr(stories, "context_for", lambda q: None)
    plan = dataclasses.replace(sg.plan_episode("why rents keep rising", 2),
                               brief=brief(subject="rents"), search=True)
    notes = sg.ScriptNotes()
    notes.agents = agents

    async def go():
        out = await sg.ScriptGenerator.__new__(sg.ScriptGenerator).research(plan, notes)
        await asyncio.sleep(0.1)           # the recall ends after the episode moved on
        return out
    asyncio.run(go())
    return notes, seen


def test_an_empty_search_with_c3p0_on_counts_the_archive_without_waiting_for_it(monkeypatch):
    notes, seen = _research(monkeypatch, _runs("contextualizer"), ["an earlier report"])
    assert not seen["look"].cancelled()
    assert contextualizer.SINCE_BOOT["empty_search_archive_had"] == 1
    assert notes.research.get("archive") == "no web sources"


def test_an_empty_search_with_c3p0_off_is_as_before(monkeypatch):
    notes, seen = _research(monkeypatch, netenyahu.OFF, ["an earlier report"])
    assert seen["look"].cancelled()
    assert contextualizer.SINCE_BOOT["empty_search"] == 0
    assert notes.research.get("archive") == "no web sources"


def test_the_health_block_reports_the_plans():
    from agents import models

    contextualizer.note_empty_search(None)
    assert models.health()["contextualizer"]["since_boot"]["empty_search"] == 1
