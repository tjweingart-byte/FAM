"""Nando's adversarial corpus as a floor (§374): every question three
reviews used to break him must be filed under an acceptable node - the true
one, a true broader one, or nothing - and C3P0's result and opinion checks
must read every listed text the right way. See `tests/nando_corpus.py`."""
from __future__ import annotations

import pytest

import categories as C
import category_vocabulary as V
import topics
from agents import categorizer, contextualizer
from tests import nando_corpus as N


@pytest.fixture
def tree():
    t = topics.category_tree()
    C.apply_seed(t)
    V.reset()
    yield t
    V.reset()


@pytest.mark.parametrize("question, acceptable", N.CASES)
def test_never_a_specific_wrong_node(tree, question, acceptable):
    # What Nando files (§375) and what code suggests to the model: neither
    # may ever be a specific wrong node on the corpus.
    # Filing nothing is always acceptable for `place`: the writer's
    # category then stands, as before Nando. The suggestion keeps the
    # corpus's own lists, so a regression in the evidence still shows.
    filed = categorizer.place(None, question).node
    assert filed == "" or filed in acceptable
    assert categorizer.suggest(None, question).node in acceptable


@pytest.mark.parametrize("title, acceptable", N.PARENTS)
def test_a_go_deeper_parent_is_read_right(tree, title, acceptable):
    assert categorizer.prior_for("what about their defense", title).node in acceptable


@pytest.mark.parametrize("text, result", N.RESULTS)
def test_results_and_non_results(text, result):
    assert contextualizer.has_result(text) is result


@pytest.mark.parametrize("person, query, asked", N.VIEWS)
def test_views_and_non_views(person, query, asked):
    assert contextualizer.asks_for_view(person, query) is asked


# ------------------------------------------------------------------ the lifecycle fixes of §374
def test_a_go_deeper_takes_the_briefs_pick_over_its_parent(tree):
    from agents.categorizer import Prior

    placed = categorizer.suggest(None, "what about their defense",
                                 prior=Prior("philadelphia eagles", "parent"), picked="nfl")
    assert placed.node == "nfl" and placed.how == "picked"


def test_an_unconfirmed_hold_is_not_kept_for_ever(monkeypatch):
    import time

    from agents import netenyahu

    netenyahu.reset()
    try:
        netenyahu._MADE["editor"] = ("old", time.monotonic() - 3 * netenyahu.HELD_REFRESH_SECONDS)
        monkeypatch.setattr(netenyahu, "_read_held", lambda: {})
        assert netenyahu.refresh_held() == {}
        assert "editor" not in netenyahu._MADE
    finally:
        netenyahu.reset()


def test_a_tie_call_goes_with_a_prepare_that_raises(tree, monkeypatch):
    import asyncio
    import dataclasses

    import episode_intelligence as ei
    import script_generator as sg
    from agents import netenyahu

    async def understand_(self, plan, notes=None):
        notes.category_tie_task = asyncio.ensure_future(asyncio.sleep(10))
        return dataclasses.replace(plan, brief=ei.Brief(query=plan.query))
    monkeypatch.setattr(sg.ScriptGenerator, "understand", understand_)
    monkeypatch.setattr(sg.live_route, "apply", lambda *a: 1 / 0)
    notes = sg.ScriptNotes()
    notes.agents = netenyahu.EpisodeRun(("categorizer",), {})
    box = {}

    async def go():
        try:
            await sg.ScriptGenerator.__new__(sg.ScriptGenerator).prepare(
                sg.plan_episode("Rangers game preview", 2), notes)
        finally:
            box["task"] = notes.category_tie_task
            await asyncio.sleep(0)
    with pytest.raises(ZeroDivisionError):
        asyncio.run(go())
    assert box["task"].cancelled()


@pytest.mark.parametrize("text", N.ACCEPTED_FLAGS)
def test_innocent_texts_are_flagged_by_design(text):
    # Missing a guessed result is the worse mistake (the owner, §375).
    assert contextualizer.has_result(text) is True
