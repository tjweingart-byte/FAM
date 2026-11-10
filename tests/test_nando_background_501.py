"""§501: Nando decides on the background paths as he does on a search.

Before this, a brief warmed at prefetch's default level (`PREFETCH_LEVEL=brief`,
what myFAM's rails warm for likely taps) was built by `understand` alone;
Nando's own call ahead of the tap lived only in `prepare`, which a brief-level
warm never reaches, and the tap that took the warmed brief skipped the brief -
and with it his call beside the brief. So that tap filed only what a solid
source gave, and otherwise nothing.
"""
from __future__ import annotations

import asyncio

import pytest

import categories as C
import category_audit
import category_vocabulary as V
import episode_intelligence as ei
import script_generator as sg
import topics
from agents import categorizer
from tests.test_agents_c3p0_call import _prepare, notes_with

QUERY = "Detroit lions at the zoo"


@pytest.fixture
def tree():
    t = topics.category_tree()
    C.apply_seed(t)
    V.reset()
    yield t
    V.reset()


def _understand(monkeypatch, notes, brief=None, pick="first"):
    asked = []

    async def tie(query, nodes, notes=None, ahead=False):
        asked.append((query, ahead))
        if pick == "raise":
            raise RuntimeError("down")
        return nodes[0] if pick == "first" else pick
    monkeypatch.setattr(categorizer, "ask_tie", tie)

    async def understand_(*a, **k):
        return brief or ei.Brief(query=QUERY)
    monkeypatch.setattr(ei, "understand", understand_)
    monkeypatch.setattr(sg.prefetch, "warm_brief", lambda *a, **k: None)

    async def go():
        return await sg.ScriptGenerator.understand(object(), sg.plan_episode(QUERY, 2), notes)
    return asyncio.run(go()), asked


@pytest.mark.parametrize("flag", ["ahead", "prefetch"])
def test_ahead_of_the_tap_the_brief_carries_his_decision(tree, monkeypatch, flag):
    notes = notes_with("categorizer")
    setattr(notes, flag, True)
    plan, asked = _understand(monkeypatch, notes)
    # Awaited where the brief is built, and asked as an ahead call (§391).
    assert asked == [(QUERY, True)]
    assert plan.brief.category_pick
    assert plan.brief.category_pick in categorizer.candidates_for(QUERY, "", notes)[0]


def test_a_search_is_unchanged_his_call_runs_beside_the_brief(tree, monkeypatch):
    notes = notes_with("categorizer")
    plan, _asked = _understand(monkeypatch, notes)
    assert plan.brief.category_pick == ""
    task = notes.category_tie_task
    assert task is not None
    task.cancel()


def test_not_asked_when_c3p0_chose_or_called_it_ambiguous(tree, monkeypatch):
    for said in ({"node": "biology"}, {"ambiguous": True}):
        notes = notes_with("categorizer", ahead=True)
        plan, asked = _understand(monkeypatch, notes,
                                  brief=ei.Brief(query=QUERY, contextualized=said))
        assert asked == [] and plan.brief.category_pick == ""


def test_not_asked_when_the_tap_already_files_it(tree, monkeypatch):
    notes = notes_with("categorizer", ahead=True)
    notes.category_prior = categorizer.Prior("biology", "edition")
    plan, asked = _understand(monkeypatch, notes)
    assert asked == [] and plan.brief.category_pick == ""


def test_not_asked_when_nando_is_not_working(tree, monkeypatch):
    plan, asked = _understand(monkeypatch, notes_with(ahead=True))
    assert asked == [] and plan.brief.category_pick == ""


def test_a_failed_call_leaves_the_brief_as_it_was(tree, monkeypatch):
    plan, asked = _understand(monkeypatch, notes_with("categorizer", ahead=True), pick="raise")
    assert asked and plan.brief.category_pick == ""


def test_the_tap_that_takes_a_warmed_brief_files_his_decision(tree, monkeypatch):
    asked = []

    async def tie(*a, **k):
        asked.append(a)
        return ""
    monkeypatch.setattr(categorizer, "ask_tie", tie)
    warmed = ei.Brief(query=QUERY, category_pick="biology")
    notes = _prepare(monkeypatch, warmed, notes_with("categorizer"))
    assert notes.category_placement.node == "biology"
    assert notes.category_placement.how == "picked"
    assert asked == []   # decided once, ahead; never asked again at the tap


def test_an_edition_does_not_ask_twice(tree, monkeypatch):
    # The edition's brief already carries his pick, so `prepare`'s own
    # ahead-of-the-tap call is not made again.
    asked = []

    async def tie(*a, **k):
        asked.append(a)
        return ""
    monkeypatch.setattr(categorizer, "ask_tie", tie)
    notes = notes_with("categorizer", ahead=True)
    notes = _prepare(monkeypatch, ei.Brief(query=QUERY, category_pick="biology"), notes)
    assert notes.category_placement.node == "biology" and asked == []


def test_his_pick_is_dropped_when_the_brief_is_unsure(tree, monkeypatch):
    warmed = ei.Brief(query=QUERY, category_pick="biology",
                      contextualized={"ambiguous": True})
    notes = _prepare(monkeypatch, warmed, notes_with("categorizer"))
    assert notes.category_placement.node != "biology"


def test_the_audit_counts_every_write_path_apart():
    rows = [{"origin": "search", "nando": "biology"},
            {"origin": "search", "nando": ""},
            {"origin": "prefetch", "nando": ""},
            {"origin": "dailyfam", "nando": "nfl"}]
    got = category_audit._nando_by_origin(rows)
    assert got["search"] == {"episodes": 2, "filed": 1, "filed_rate": 0.5}
    assert got["prefetch"] == {"episodes": 1, "filed": 0, "filed_rate": 0.0}
    assert got["dailyfam"]["filed_rate"] == 1.0
    assert category_audit._nando_summary(rows, 3)["by_origin"] == got


# ------------------------------------------------------------------ the review before merge
@pytest.mark.parametrize("flag", ["ahead", "prefetch"])
def test_a_none_answer_ahead_is_never_asked_again_in_prepare(tree, monkeypatch, flag):
    # The same notes go from `understand` to `prepare` on an edition or a
    # script-level warm: "none" or a failure there must not buy a second call.
    notes = notes_with("categorizer")
    setattr(notes, flag, True)
    plan, asked = _understand(monkeypatch, notes, pick="")
    assert asked == [(QUERY, True)] and plan.brief.category_pick == ""
    calls = []

    async def tie(*a, **k):
        calls.append(a)
        return ""
    monkeypatch.setattr(categorizer, "ask_tie", tie)
    _prepare(monkeypatch, plan.brief, notes)
    assert calls == []


def test_the_warmed_brief_flows_from_the_warm_to_the_tap(tree, monkeypatch):
    # End to end: a brief built ahead (as prefetch's brief level builds it),
    # then a listener's tap with fresh notes that takes that brief.
    warm = notes_with("categorizer")
    warm.prefetch = True
    plan, asked = _understand(monkeypatch, warm)
    assert asked == [(QUERY, True)] and plan.brief.category_pick
    calls = []

    async def tie(*a, **k):
        calls.append(a)
        return ""
    monkeypatch.setattr(categorizer, "ask_tie", tie)
    tap = _prepare(monkeypatch, plan.brief, notes_with("categorizer"))
    assert tap.category_placement.node == plan.brief.category_pick
    assert tap.category_placement.how == "picked" and calls == []


def test_an_edition_story_it_will_drop_pays_for_no_call(tree, monkeypatch):
    notes = notes_with("categorizer", ahead=True)
    notes.edition = True
    plan, asked = _understand(monkeypatch, notes,
                              brief=ei.Brief(query=QUERY, outcome_dependent=True))
    assert asked == [] and plan.brief.category_pick == ""
