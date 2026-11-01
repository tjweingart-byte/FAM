"""Nando decides (§379): code files a node only from a solid source - the
tap's own filing, a provider's field, the brief's own category words, the
model's pick - and everything it reads off the raw words is a suggestion
and a candidate for the model. His own call runs whenever nothing solid
will file the request and C3P0 is not choosing for him."""
from __future__ import annotations

import asyncio
import dataclasses

import pytest

import categories as C
import category_vocabulary as V
import episode_intelligence as ei
import script_generator as sg
import topics
from agents import categorizer, netenyahu
from agents.categorizer import Prior
from tests.test_agents_c3p0_call import _prepare, notes_with


@pytest.fixture
def tree():
    t = topics.category_tree()
    C.apply_seed(t)
    V.reset()
    yield t
    V.reset()


def test_raw_words_are_a_suggestion_never_a_filing(tree):
    placed = categorizer.place(None, "Stanley Cup odds")
    assert placed.node == "" and placed.suggestion == "stanley cup"
    assert "stanley cup" in placed.candidates


def test_the_briefs_own_category_words_are_solid(tree):
    placed = categorizer.place(ei.Brief(query="q", category="extreme weather"),
                               "Miami heat wave this week")
    assert placed.node == "extreme weather"


def test_the_facet_the_briefs_words_name_is_solid(tree):
    assert categorizer.place(ei.Brief(query="q", category="sports"), "zqxv").node == "sports"


def test_the_taps_own_filing_is_solid_and_may_be_narrowed(tree):
    assert categorizer.place(None, "x", prior=Prior("nfl", "dailyfam")).node == "nfl"
    narrowed = categorizer.place(ei.Brief(query="q", category="philadelphia eagles"), "q",
                                 prior=Prior("nfl", "dailyfam"))
    assert narrowed.node == "philadelphia eagles"


def test_a_go_deeper_parent_alone_is_a_suggestion(tree):
    placed = categorizer.place(None, "what about their defense",
                               prior=Prior("philadelphia eagles", "parent"))
    assert placed.node == "" and placed.suggestion == "philadelphia eagles"


def test_the_models_pick_is_solid(tree):
    assert categorizer.place(None, "Detroit lions at the zoo", picked="biology").node == "biology"


def _understand(monkeypatch, notes, query="Detroit lions at the zoo"):
    started = []

    async def tie(*a, **k):
        started.append(a)
        return ""
    monkeypatch.setattr(categorizer, "ask_tie", tie)

    async def understand_(*a, **k):
        return ei.Brief(query=query)
    monkeypatch.setattr(ei, "understand", understand_)
    monkeypatch.setattr(sg.prefetch, "warm_brief", lambda *a, **k: None)

    async def go():
        await sg.ScriptGenerator.understand(object(), sg.plan_episode(query, 2), notes)
        await asyncio.sleep(0)
    asyncio.run(go())
    return started


def test_nandos_own_call_runs_whenever_nothing_solid_will_file(tree, monkeypatch):
    assert len(_understand(monkeypatch, notes_with("categorizer"))) == 1


def test_no_call_of_his_own_when_c3p0_chooses_for_him(tree, monkeypatch):
    assert _understand(monkeypatch, notes_with("categorizer", "contextualizer")) == []


def test_no_call_of_his_own_when_the_tap_already_files_it(tree, monkeypatch):
    import trending_bank

    story = __import__("stories").Story(subject="s", title="t", angle="",
                                        query="Detroit lions at the zoo",
                                        category="biology", first_seen=__import__("time").time())
    monkeypatch.setattr(trending_bank, "stories_in_memory", lambda *a: [story])
    assert _understand(monkeypatch, notes_with("categorizer")) == []


def test_ahead_of_the_tap_his_call_is_awaited_in_prepare(tree, monkeypatch):
    asked = []

    async def tie(query, nodes, notes=None):
        asked.append(query)
        return nodes[0]
    monkeypatch.setattr(categorizer, "ask_tie", tie)
    notes = notes_with("categorizer")
    notes.ahead = True
    brief = ei.Brief(query="Stanley Cup odds")
    _prepare(monkeypatch, brief, notes)
    assert asked == ["Stanley Cup odds"]
    assert notes.category_placement.node and notes.category_placement.how == "picked"
