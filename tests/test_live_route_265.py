"""Asking the live provider the question needs (§265).

Three fixes to what §264 measured. The routing check: a brief that named no
live domain still asks a provider when the question names a major-league team,
a named game, or a market word with a listed company - in code, spending
nothing, and only where something here can serve. The bare name: providers
resolve `Brief.live_entity` ("Kansas City Chiefs") before the descriptive
subject, once each. And the clock: the lookup may run as long as the search
beside it, but the first word never waits on it longer than it used to.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import time
import types
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import episode_intelligence as ei  # noqa: E402
import live_facts  # noqa: E402
import live_route  # noqa: E402
import live_routing  # noqa: E402
import live_sources  # noqa: E402
import script_generator as sg  # noqa: E402
from config import settings  # noqa: E402


def brief(**overrides):
    base = dict(query="", subject="", live_domain="", live_entity="",
                live_route="", live_route_why="", degraded=False, notes=[],
                intent="", outcome_dependent=False, place="", named_slot="")
    base.update(overrides)
    return types.SimpleNamespace(**base)


class Stub(live_facts.LiveSource):
    """A provider that resolves only the names it is given."""

    def __init__(self, domain, names=(), name="stub"):
        self.domain, self.name, self.names = domain, name, set(names)
        self.asked: list = []

    def diagnose(self):
        return True, "stub"

    async def resolve(self, b):
        text = live_route.entity_of(b)
        self.asked.append(text)
        if text in self.names:
            return live_facts.Entity(domain=self.domain, provider=self.name,
                                     id=text, label=text)
        return None

    async def fetch(self, entity):
        return live_facts.LiveFacts(
            domain=self.domain, source=self.name,
            as_of=datetime.now(timezone.utc), facts=[f"{entity.id} is on"],
            status=live_facts.SCHEDULED)


@pytest.fixture
def provider():
    registered = []

    def add(domain, names=(), name="stub"):
        source = Stub(domain, names, name)
        live_facts.register(source)
        registered.append(source)
        return source

    live_facts.clear_caches()
    yield add
    for source in registered:
        live_facts.unregister(source.name)
    live_facts.clear_caches()


# --- the roster ---------------------------------------------------------------


def test_roster_has_every_team():
    assert {k: len(v) for k, v in live_route.ROSTER.items()} == \
        {"nfl": 32, "nba": 30, "mlb": 30, "nhl": 32}


@pytest.mark.parametrize("text,league", [
    ("how are the steelers looking", "nfl"),
    ("Kansas City Chiefs playoff chances", "nfl"),
    ("did the miami heat win", "nba"),
    ("maple leafs tonight", "nhl"),
    ("yankees lineup", "mlb"),
])
def test_a_team_is_named(text, league):
    assert league in {l for l, _ in live_route.teams_in(text)}


@pytest.mark.parametrize("text", [
    "heat wave in miami", "the giants of tech", "bills in congress",
    "how do kings inherit", "bears and bulls in the market",
    "thunder and lightning", "the history of the jets engine",
])
def test_an_everyday_word_names_no_team(text):
    assert live_route.teams_in(text) == []


def test_a_team_names_its_sport_and_two_leagues_name_none():
    assert live_route.sport_of("Kansas City Chiefs") == "american-football"
    assert live_route.sport_of("Kansas City Chiefs and Kansas City Royals") == ""


def _sports_settings(monkeypatch, default):
    monkeypatch.setattr(live_sources, "settings", dataclasses.replace(
        settings, api_sports_sport=default, api_sports_sports=""))


def test_sport_for_takes_the_roster_over_the_default(monkeypatch):
    _sports_settings(monkeypatch, "basketball")
    assert live_sources.sport_for("Kansas City Chiefs").key == "american-football"
    assert live_sources.sport_for("Boston Bruins").key == "hockey"
    # A sport word still wins, and nothing named still means the default.
    assert live_sources.sport_for("Bruins", "nba trade talk").key == "basketball"
    assert live_sources.sport_for("a game tonight").key == "basketball"


def test_sport_for_reads_the_context_for_a_sport_word(monkeypatch):
    _sports_settings(monkeypatch, "american-football")
    assert live_sources.sport_for("Rangers", "mlb wild card").key == "baseball"


# --- the routing check ----------------------------------------------------------


def test_a_team_routes_to_sports(provider):
    provider("sports")
    b = live_route.apply(brief(query="how are the steelers looking"))
    assert b.live_route == "sports"
    assert "Pittsburgh Steelers" in b.live_route_why


def test_a_named_slot_routes_to_sports(provider):
    provider("sports")
    b = live_route.apply(brief(query="sunday night football recap"))
    assert b.live_route == "sports"


def test_nothing_is_routed_where_nothing_can_serve():
    for source in live_facts.sources_for("sports"):
        assert not source.diagnose()[0]
    b = live_route.apply(brief(query="how are the steelers looking"))
    assert b.live_route == ""


def test_the_briefs_own_domain_is_never_overridden(provider):
    provider("sports")
    b = live_route.apply(brief(query="steelers stock of goodwill",
                               live_domain="markets"))
    assert b.live_route == ""


def test_markets_need_a_market_word_and_a_listing(provider):
    provider("markets")
    yes = live_route.apply(brief(query="why are nvidia shares falling",
                                 live_entity="NVIDIA"))
    assert yes.live_route == "markets"
    no_entity = live_route.apply(brief(query="why are nvidia shares falling"))
    assert no_entity.live_route == ""
    topic = live_route.apply(brief(query="are tech stocks overvalued",
                                   live_entity="technology sector news"))
    assert topic.live_route == ""


def test_a_degraded_brief_is_routed_from_the_raw_query(provider):
    provider("sports")
    fell = ei.fallback_brief("steelers score", "timed out")
    live_route.apply(fell, "steelers score")
    assert fell.live_route == "sports"


def test_lookup_asks_the_routed_domain(provider):
    sports = provider("sports", names={"Pittsburgh Steelers"})
    b = live_route.apply(brief(query="steelers score",
                               live_entity="Pittsburgh Steelers"))
    result = asyncio.run(live_facts.lookup(b))
    assert result.outcome == live_facts.FACTS
    assert sports.asked == ["Pittsburgh Steelers"]


def test_a_route_changes_nothing_but_who_is_asked(provider):
    """`live_domain` makes a question outcome-dependent, screens the packet
    and sets the cache window; a route must do none of that."""
    provider("sports")
    b = live_route.apply(brief(query="how are the steelers looking"))
    assert b.live_domain == "" and b.outcome_dependent is False


# --- the bare name --------------------------------------------------------------


def test_the_brief_asks_for_a_bare_name():
    assert "live_entity" in ei.BRIEF_SCHEMA["properties"]
    assert "live_entity" in ei.BRIEF_SCHEMA["required"]
    assert "**live_entity**" in ei.build_ei_prompt("steelers score", 3)


def test_the_bare_name_is_resolved_first(provider):
    sports = provider("sports", names={"Kansas City Chiefs"})
    b = brief(live_domain="sports", live_entity="Kansas City Chiefs",
              subject="the Kansas City Chiefs' 2026 playoff chances")
    result = asyncio.run(live_facts.lookup(b))
    assert result.outcome == live_facts.FACTS
    assert sports.asked == ["Kansas City Chiefs"]


def test_a_bad_bare_name_falls_back_to_the_subject_once(provider):
    sports = provider("sports", names={"Kansas City Chiefs"})
    b = brief(live_domain="sports", live_entity="KC",
              subject="Kansas City Chiefs")
    result = asyncio.run(live_facts.lookup(b))
    assert result.outcome == live_facts.FACTS
    assert sports.asked == ["KC", "Kansas City Chiefs"]


def test_no_match_names_both_wordings(provider):
    provider("sports")
    b = brief(live_domain="sports", live_entity="KC", subject="the Chiefs")
    result = asyncio.run(live_facts.lookup(b))
    assert result.outcome == live_facts.NO_ENTITY
    assert "'KC' or 'the Chiefs'" in result.detail


def test_without_a_bare_name_the_subject_is_asked_as_before(provider):
    sports = provider("sports", names={"Kansas City Chiefs"})
    b = brief(live_domain="sports", subject="Kansas City Chiefs")
    asyncio.run(live_facts.lookup(b))
    assert sports.asked == ["Kansas City Chiefs"]


# --- the clock ------------------------------------------------------------------


def _prepare(monkeypatch, *, lookup_takes, research_takes, query="steelers score"):
    quick = dataclasses.replace(settings, live_total_timeout_seconds=0.3,
                                live_patient_seconds=3.0)
    monkeypatch.setattr(live_facts, "settings", quick)
    monkeypatch.setattr(sg, "settings", quick)
    seen = {}

    async def lookup(b, notes=None, patience=0.0):
        seen["patience"] = patience
        seen["brief"] = b
        await asyncio.sleep(lookup_takes)
        return live_facts.LiveLookup("sports", live_facts.FACTS, detail="stub")

    async def research(self, plan, notes=None):
        await asyncio.sleep(research_takes)
        return dataclasses.replace(plan, evidence="SOURCE 1\nTitle: x")

    async def understand(self, plan, notes=None):
        return plan

    monkeypatch.setattr(live_facts, "lookup", lookup)
    monkeypatch.setattr(sg.ScriptGenerator, "research", research)
    monkeypatch.setattr(sg.ScriptGenerator, "understand", understand)
    plan = dataclasses.replace(sg.plan_episode(query, 3),
                               brief=brief(query=query, live_domain="sports"))
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    began = time.monotonic()
    prepared = asyncio.run(generator.prepare(plan, sg.ScriptNotes()))
    return prepared, time.monotonic() - began, seen


def test_a_slow_scoreboard_is_kept_while_the_search_runs(monkeypatch):
    prepared, _took, seen = _prepare(monkeypatch, lookup_takes=0.6,
                                     research_takes=0.9)
    assert seen["patience"] == 3.0
    assert prepared.live.outcome == live_facts.FACTS


def test_the_first_word_never_waits_past_the_old_ceiling(monkeypatch):
    prepared, took, _seen = _prepare(monkeypatch, lookup_takes=2.0,
                                     research_takes=0.0)
    assert took < 1.0
    assert prepared.live.outcome == live_facts.TIMEOUT
    assert "evidence was in" in prepared.live.detail


def test_a_quick_search_still_waits_for_the_old_ceiling(monkeypatch):
    prepared, _took, _seen = _prepare(monkeypatch, lookup_takes=0.1,
                                      research_takes=0.0)
    assert prepared.live.outcome == live_facts.FACTS


# --- prepare routes, and the record says so -------------------------------------


def test_prepare_routes_and_records_who_routed(provider, monkeypatch):
    provider("sports")
    asked = {}

    async def lookup(b, notes=None, patience=0.0):
        asked["domain"] = live_route.domain_of(b)
        return live_facts.LiveLookup("sports", live_facts.NO_ENTITY, detail="x",
                                     attempts=[("stub", "no_entity", "x")])

    async def research(self, plan, notes=None):
        return dataclasses.replace(plan, evidence="SOURCE 1\nTitle: x")

    async def understand(self, plan, notes=None):
        return plan

    monkeypatch.setattr(live_facts, "lookup", lookup)
    monkeypatch.setattr(sg.ScriptGenerator, "research", research)
    monkeypatch.setattr(sg.ScriptGenerator, "understand", understand)
    query = "how are the steelers looking"
    original = brief(query=query, subject="the Steelers' season")
    plan = dataclasses.replace(sg.plan_episode(query, 3), brief=original)
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    notes = sg.ScriptNotes()
    prepared = asyncio.run(generator.prepare(plan, notes))
    assert asked["domain"] == "sports"
    assert original.live_route == "", "the caller's brief is not mutated"
    assert notes.live_domain == "", "the cache window still reads the brief's own"
    # A routed miss never reaches the writer: no "no live state" block, no
    # score guard, for a question the brief never said turned on one.
    assert prepared.live is None
    assert notes.live_outcome == ""
    row = live_routing.report()["recent"][0]
    assert row["route"].startswith("code: names Pittsburgh Steelers")
    assert row["domain"] == "sports"
    assert row["verdict"] == live_routing.NO_ANSWER
    assert row["attempts"][0][:2] == ["stub", "no_entity"]


def test_a_routed_answer_reaches_the_writer(provider, monkeypatch):
    provider("sports")

    async def lookup(b, notes=None, patience=0.0):
        return live_facts.LiveLookup("sports", live_facts.FACTS, detail="stub",
                                     facts=None)

    async def research(self, plan, notes=None):
        return dataclasses.replace(plan, evidence="SOURCE 1\nTitle: x")

    async def understand(self, plan, notes=None):
        return plan

    monkeypatch.setattr(live_facts, "lookup", lookup)
    monkeypatch.setattr(sg.ScriptGenerator, "research", research)
    monkeypatch.setattr(sg.ScriptGenerator, "understand", understand)
    query = "steelers score"
    plan = dataclasses.replace(sg.plan_episode(query, 3),
                               brief=brief(query=query))
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    prepared = asyncio.run(generator.prepare(plan, sg.ScriptNotes()))
    assert prepared.live is not None and prepared.live.outcome == live_facts.FACTS


def test_the_score_guard_is_unchanged_for_an_unrouted_question():
    import score_guard

    plan = types.SimpleNamespace(brief=brief(query="how are the steelers looking"),
                                 live=None)
    assert score_guard.ScoreGuard(plan).active is False
