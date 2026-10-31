"""C3P0 and Nando in the brief's call (§360): the brief's search half, sent
through the agent layer as C3P0's one call when he works on the episode,
carrying Nando's candidate list; the answer held to the rules in code; a
request never sent runs the brief's own search half; with the agents off the
brief is exactly what it was.
"""
from __future__ import annotations

import asyncio
import dataclasses
import pathlib

import pytest

import categories as C
import episode_intelligence as ei
import script_generator as sg
import topics
from agents import categorizer, contextualizer, models, netenyahu
from agents.categorizer import Prior
from tests.test_ei_split_268 import UNDERSTOOD, WORDED, model  # noqa: F401

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def tree():
    t = topics.category_tree()
    C.apply_seed(t)
    return t


@pytest.fixture(autouse=True)
def fresh():
    contextualizer.reset()
    netenyahu.reset()
    yield
    contextualizer.reset()
    netenyahu.reset()


def answer(**changes):
    base = dict(WORDED, forecast_query="", source_kind="news",
                entity="Philadelphia Eagles", entity_kind="team", ambiguous=False,
                readings=[], likeliest="",
                plugins=[{"plugin": "search", "query": "Eagles Giants final score"},
                         {"plugin": "live_facts", "query": "Philadelphia Eagles"}],
                archive={"lookup": "result_between", "what": "the Eagles' recent games"},
                opinion_requested="", private_person=False, advice_request=False,
                node="philadelphia eagles")
    base.update(changes)
    return contextualizer.SearchPlan.model_validate(base)


def result(status="ok", value=None, sent=True):
    return models.AgentResult(status, "contextualizer", "contextualise", value=value,
                              sent=sent, reason="" if status == "ok" else status)


@pytest.fixture
def c3p0(monkeypatch):
    """A faked agent layer that records the request and answers as told."""
    state = {"requests": [], "result": result(value=answer())}

    async def ask(request, notes=None, client=None):
        state["requests"].append(request)
        return state["result"]
    monkeypatch.setattr(models, "ask", ask)
    return state


def notes_with(*roles, ahead=False):
    notes = sg.ScriptNotes()
    notes.agents = netenyahu.EpisodeRun(tuple(roles), {})
    notes.ahead = ahead
    return notes


def understand(notes, query="how did the Eagles do", **kw):
    return asyncio.run(ei.understand(query, 2, notes=notes, **kw))


# ------------------------------------------------------------------ the shape
def test_the_call_carries_the_search_half_field_for_field():
    names = set(contextualizer.SearchPlan.model_fields)
    assert set(ei.SEARCH_SCHEMA["properties"]) <= names
    assert {"entity", "entity_kind", "ambiguous", "readings", "likeliest", "plugins",
            "archive", "opinion_requested", "private_person", "advice_request",
            "node"} <= names
    assert contextualizer.SearchPlan.model_fields["source_kind"].annotation.__args__ == \
        tuple(ei.SOURCE_KINDS)


def test_the_schema_is_one_the_layer_can_send():
    assert models._unsendable_schema(contextualizer.SearchPlan.model_json_schema()) == ""
    sent = models.wire_schema(contextualizer.SearchPlan)
    assert "title" in sent["properties"]


# ------------------------------------------------------------------ in the brief
def test_with_c3p0_working_the_search_half_is_his_call(model, c3p0):
    brief = understand(notes_with("contextualizer"))
    asked = [part for part, _kw in model["calls"]]
    assert "search" not in asked and "understand" in asked
    [request] = c3p0["requests"]
    assert request.agent == "contextualizer" and request.rule_book == "c3p0.md"
    assert request.model_setting == "contextualizer_model"
    assert ("request", "how did the Eagles do") in request.material
    assert brief.search_query == WORDED["search_query"]
    assert brief.contextualized["entity"] == "Philadelphia Eagles"
    assert brief.contextualized["plugins"][1] == {"plugin": "live_facts",
                                                  "query": "Philadelphia Eagles"}


def test_the_effort_is_the_briefs_on_the_tap_and_ahead_effort_before_it(model, c3p0, monkeypatch):
    monkeypatch.setattr(contextualizer, "settings", None, raising=False)
    understand(notes_with("contextualizer"))
    understand(notes_with("contextualizer"), ahead=True)
    on_tap, ahead = c3p0["requests"]
    from config import settings
    assert on_tap.effort == settings.ei_effort and on_tap.surface == "search"
    assert ahead.effort == settings.ei_ahead_effort == "high" and ahead.surface == "ahead"
    assert on_tap.timeout == settings.ei_timeout_seconds
    assert ahead.timeout == settings.ei_ahead_timeout_seconds


def test_nandos_candidates_go_with_the_call_when_he_works_too(tree, model, c3p0):
    understand(notes_with("contextualizer", "categorizer"), query="how did United do")
    [request] = c3p0["requests"]
    listing = [text for tag, text in request.material if text.startswith("Candidate nodes")]
    assert listing and "airlines" in listing[0] and "premier league" in listing[0]


def test_a_request_never_sent_runs_the_briefs_own_search_half(model, c3p0):
    c3p0["result"] = result("held", sent=False)
    brief = understand(notes_with("contextualizer"))
    assert "search" in [part for part, _kw in model["calls"]]
    assert brief.search_query == WORDED["search_query"] and brief.contextualized == {}
    assert contextualizer.SINCE_BOOT["call_fell_back_to_brief"] == 1


def test_a_sent_call_that_failed_is_the_search_half_failing(model, c3p0):
    c3p0["result"] = result("timeout", sent=True)
    brief = understand(notes_with("contextualizer"))
    assert "search" not in [part for part, _kw in model["calls"]]
    assert any("search half did not answer" in n for n in brief.notes)
    assert brief.contextualized == {}


def test_every_result_is_reported_to_netenyahu(model, c3p0, monkeypatch):
    seen = []
    monkeypatch.setattr(netenyahu, "report", lambda role, status: seen.append((role, status)))
    understand(notes_with("contextualizer"))
    assert seen == [("contextualizer", "ok")]


def test_with_the_agents_off_the_brief_is_as_before(model, c3p0):
    for notes in (None, notes_with(), sg.ScriptNotes()):
        brief = understand(notes)
        assert brief.contextualized == {}
    assert c3p0["requests"] == []


def test_the_one_call_brief_has_no_search_half_for_c3p0(model, c3p0, monkeypatch):
    monkeypatch.setattr(ei, "settings", dataclasses.replace(ei.settings, ei_split=False))
    understand(notes_with("contextualizer"))
    assert c3p0["requests"] == []


# ------------------------------------------------------------------ held to the rules in code
@pytest.mark.parametrize("text, result_", [
    ("Eagles Giants final score, Sunday 11 October 2026", False),
    ("Eagles beat Giants 24-17 recap", True),
    ("Eagles Crush Giants", True),
    ("who won the Eagles game", False),
    ("Did the Eagles win", False),
    ("Eagles 31-10 win", True),
])
def test_a_result_is_found(text, result_):
    assert contextualizer.has_result(text) is result_


def test_a_title_or_search_with_a_result_is_replaced():
    search, extras, why = contextualizer.checked(
        answer(title="Eagles Crush Giants 31-10", search_query="Eagles beat Giants recap",
               plugins=[{"plugin": "live_facts", "query": "Eagles won 24-17"}]),
        "how did the Eagles do", ("philadelphia eagles",))
    assert search["title"] == "" and extras["title"] == ""
    assert search["search_query"] == ""
    assert extras["plugins"][0]["query"] == "how did the Eagles do"
    assert len(why) == 3


def test_ambiguous_needs_two_readings():
    _s, extras, _w = contextualizer.checked(answer(ambiguous=True, readings=["United Airlines"]),
                                            "q", ())
    assert extras["ambiguous"] is False and extras["readings"] == []
    _s, extras, _w = contextualizer.checked(
        answer(ambiguous=True, readings=["United Airlines", "Manchester United"],
               likeliest="United Airlines"), "q", ())
    assert extras["ambiguous"] and extras["likeliest"] == "United Airlines"


def test_an_opinion_only_when_a_named_persons_view_is_asked_for():
    keep = contextualizer.checked(answer(opinion_requested="Paul Krugman"),
                                  "What does Paul Krugman think about the tariffs?", ())
    assert keep[1]["opinion_requested"] == "Paul Krugman"
    drop = contextualizer.checked(answer(opinion_requested="Paul Krugman"),
                                  "Are the tariffs working?", ())
    assert drop[1]["opinion_requested"] == ""


def test_nandos_node_only_from_the_candidates():
    assert contextualizer.checked(answer(node="nba"), "q", ("nfl",))[1]["node"] == ""
    assert contextualizer.checked(answer(node="none"), "q", ("nfl",))[1]["node"] == ""
    assert contextualizer.checked(answer(node="nfl"), "q", ("nfl",))[1]["node"] == "nfl"


# ------------------------------------------------------------------ Nando: the candidates, the tie call, the pick
def test_candidates_hold_every_reading_and_their_parents(tree):
    nodes, tie = categorizer.candidates_for("how did United do")
    assert tie and {"airlines", "premier league", "business", "football"} <= set(nodes)
    assert len(nodes) <= categorizer.MAX_CANDIDATES


def test_with_nothing_found_the_facets_are_offered(tree):
    nodes, tie = categorizer.candidates_for("zqxv blorp")
    assert set(nodes) == set(topics.FACETS) and not tie


def test_the_tie_call_answers_only_from_the_candidates(tree, monkeypatch):
    async def ask(request, notes=None, client=None):
        assert request.agent == "categorizer" and request.task == "place"
        return models.AgentResult("ok", "categorizer", "place",
                                  value=categorizer.TiePick(node=state["node"]))
    state = {"node": "airlines"}
    monkeypatch.setattr(models, "ask", ask)
    assert asyncio.run(categorizer.ask_tie("q", ("airlines", "premier league"))) == "airlines"
    state["node"] = "nba"
    assert asyncio.run(categorizer.ask_tie("q", ("airlines",))) == ""


def test_a_tie_call_still_running_is_cancelled_never_waited_for():
    async def go():
        slow = asyncio.ensure_future(asyncio.sleep(10, result="airlines"))
        done = asyncio.ensure_future(asyncio.sleep(0, result="nfl"))
        await asyncio.sleep(0.01)
        return categorizer.tie_answer(slow), slow.cancelled() or slow.cancelling(), \
            categorizer.tie_answer(done)
    late, cancelled, early = asyncio.run(go())
    assert late == "" and cancelled and early == "nfl"


def test_the_pick_outranks_the_code_but_never_the_taps_filing(tree):
    picked = categorizer.place(None, "Heat beat the Celtics", picked="extreme weather")
    assert picked.node == "extreme weather" and picked.how == "picked"
    held = categorizer.place(None, "x", prior=Prior("nfl", "dailyfam"), picked="nba")
    assert held.node == "nfl"
    narrowed = categorizer.place(None, "x", prior=Prior("nfl", "dailyfam"),
                                 picked="philadelphia eagles")
    assert narrowed.node == "philadelphia eagles"


def test_a_pick_the_tree_lacks_is_ignored(tree):
    assert categorizer.place(None, "Stanley Cup odds", picked="made up").how != "picked"


def _prepare(monkeypatch, brief, notes):
    async def research_(self, plan, notes=None):
        return dataclasses.replace(plan, evidence="SOURCE 1\nTitle: x")

    async def understand_(self, plan, notes=None):
        return plan
    monkeypatch.setattr(sg.ScriptGenerator, "research", research_)
    monkeypatch.setattr(sg.ScriptGenerator, "understand", understand_)
    monkeypatch.setattr(sg.live_captions, "publish_category", lambda k, w: None)
    plan = dataclasses.replace(sg.plan_episode(brief.query, 2), brief=brief)
    asyncio.run(sg.ScriptGenerator.__new__(sg.ScriptGenerator).prepare(plan, notes))
    return notes


def test_prepare_files_c3p0s_pick(tree, monkeypatch):
    brief = ei.Brief(query="how did United do", contextualized={"node": "airlines"})
    notes = _prepare(monkeypatch, brief, notes_with("categorizer"))
    assert notes.category_placement.node == "airlines"
    assert notes.category_placement.how == "picked"


def test_prepare_takes_the_tie_call_when_there_is_no_pick(tree, monkeypatch):
    brief = ei.Brief(query="how did United do")
    notes = notes_with("categorizer")

    async def go():
        notes.category_tie_task = asyncio.ensure_future(asyncio.sleep(0, result="airlines"))
        await asyncio.sleep(0)
    asyncio.run(go())
    _prepare(monkeypatch, brief, notes)
    assert notes.category_placement.node == "airlines"


# ------------------------------------------------------------------ the title
def test_c3p0s_title_is_the_episodes_while_he_works(monkeypatch):
    brief = ei.Brief(query="q", contextualized={"title": "The Eagles' Long Season"})
    notes = notes_with("contextualizer")
    sg._take_c3p0_title(notes, brief)
    assert notes.c3p0_title == "The Eagles' Long Season"
    off = sg.ScriptNotes()
    off.agents = netenyahu.OFF
    sg._take_c3p0_title(off, brief)
    assert off.c3p0_title == ""


def test_the_writers_title_does_not_replace_c3p0s():
    source = (ROOT / "script_generator.py").read_text(encoding="utf-8")
    assert ('self.notes.title = (getattr(self.notes, "c3p0_title", "")\n'
            '                                or extract_title(self.buffer))') in source


# ------------------------------------------------------------------ the plan
def test_the_research_plan_is_c3p0s_and_counts_agreement_with_the_code_routes():
    brief = ei.Brief(query="q", subject="the Eagles", live_domain="sports",
                     live_entity="Philadelphia Eagles",
                     contextualized=contextualizer.checked(answer(), "q", ("nfl",))[1])
    plan = contextualizer.from_brief(brief, "q")
    assert plan.entity_kind == "team"
    assert [c.plugin for c in plan.plugins] == ["search", "live_facts"]
    assert plan.archive.lookup == "result_between"
    assert contextualizer.SINCE_BOOT["plugins_differ"] == 1  # code adds players


def test_the_title_switch_keeps_the_writers_title(monkeypatch):
    monkeypatch.setattr(sg, "settings", dataclasses.replace(sg.settings,
                                                            contextualizer_titles=False))
    notes = notes_with("contextualizer")
    sg._take_c3p0_title(notes, ei.Brief(query="q", contextualized={"title": "A Title"}))
    assert notes.c3p0_title == ""


def test_the_title_switch_is_reported():
    assert contextualizer.report()["titles"] is True
