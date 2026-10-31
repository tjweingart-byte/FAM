"""What the second review of Phase 1 found (§362), each pinned."""
from __future__ import annotations

import asyncio
import dataclasses
import pathlib
import sys
import threading
import time

import pytest

import categories as C
import category_vocabulary as V
import episode_intelligence as ei
import metering
import script_generator as sg
import topics
from agents import categorizer, contextualizer, models, netenyahu, tasks
from agents.categorizer import Prior
from tests.test_agents_c3p0_call import _prepare, answer, notes_with
from tests.test_ei_split_268 import model  # noqa: F401

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def tree():
    t = topics.category_tree()
    C.apply_seed(t)
    V.reset()
    yield t
    V.reset()


# ------------------------------------------------------------------ Nando
def test_a_team_whose_name_is_an_everyday_phrase_is_a_clash(tree):
    # §363 reverted the team gate (it unplaced twenty right teams): a seeded
    # full name means the team, and where one is also an everyday phrase it
    # is in the clash table and must win on its cues.
    assert categorizer.suggest(None, "Utah jazz festival lineup").node == ""
    assert categorizer.suggest(None, "Dallas Cowboys news").node == "dallas cowboys"


def test_an_unambiguous_sports_phrase_needs_no_sport_word(tree):
    assert categorizer.suggest(None, "Stanley Cup odds").node == "stanley cup"
    assert categorizer.suggest(None, "Utah Jazz game tonight").node == "utah jazz"


def test_c3p0s_pick_is_not_taken_when_he_said_ambiguous(tree):
    assert categorizer.suggest(None, "Rangers season preview", picked="new york rangers",
                             unsure=True).node == ""
    assert categorizer.suggest(None, "Rangers season preview",
                             picked="new york rangers").node == "new york rangers"


def test_prepare_passes_c3p0s_ambiguity(tree, monkeypatch):
    brief = ei.Brief(query="how did United do",
                     contextualized={"node": "airlines", "ambiguous": True})
    notes = _prepare(monkeypatch, brief, notes_with("categorizer"))
    assert notes.category_placement.node != "airlines"


def test_a_parent_title_is_placed_by_nando_not_by_a_raw_phrase(tree):
    assert categorizer.prior_for("what next", "Utah's jazz festival lineup") == \
        categorizer.NO_PRIOR
    assert categorizer.prior_for("what next", "Philadelphia Eagles' playoff picture") == \
        Prior("philadelphia eagles", "parent")


def test_a_parent_is_overridden_by_the_request_itself(tree):
    placed = categorizer.suggest(ei.Brief(query="q", category="extreme weather"), "what next",
                               prior=Prior("philadelphia eagles", "parent"))
    assert placed.node == "extreme weather"


def test_an_editions_node_the_tree_lost_is_no_prior(tree):
    notes = sg.ScriptNotes()
    notes.category_prior = Prior("merged away", "edition")
    assert categorizer.prior_for("zqxv", notes=notes) == categorizer.NO_PRIOR


# ------------------------------------------------------------------ the tie call
def test_no_tie_call_ahead_of_the_tap(tree, monkeypatch):
    started = []
    monkeypatch.setattr(categorizer, "ask_tie",
                        lambda *a, **k: started.append(1) or asyncio.sleep(0))

    async def understand_(*a, **k):
        return ei.Brief(query="q")
    monkeypatch.setattr(ei, "understand", understand_)
    monkeypatch.setattr(sg.prefetch, "warm_brief", lambda *a, **k: None)
    plan = sg.plan_episode("Rangers game preview", 2)
    for flag in ("ahead", "prefetch"):
        notes = notes_with("categorizer")
        setattr(notes, flag, True)
        asyncio.run(sg.ScriptGenerator.understand(object(), plan, notes))
        assert notes.category_tie_task is None
    assert started == []


def test_the_tie_call_goes_with_a_brief_that_failed(tree, monkeypatch):
    box = {}

    async def slow(*a, **k):
        await asyncio.sleep(10)
        return "nhl"

    def ask_tie(*a, **k):
        box["coro"] = slow()
        return box["coro"]
    monkeypatch.setattr(categorizer, "ask_tie", ask_tie)

    async def understand_(*a, **k):
        await asyncio.sleep(0)
        raise RuntimeError("the brief broke")
    monkeypatch.setattr(ei, "understand", understand_)
    monkeypatch.setattr(sg.prefetch, "warm_brief", lambda *a, **k: None)
    notes = notes_with("categorizer")
    with pytest.raises(RuntimeError):
        asyncio.run(sg.ScriptGenerator.understand(object(), sg.plan_episode(
            "Rangers game preview", 2), notes))
    assert notes.category_tie_task.cancelled()


def test_a_cancelled_call_still_leaves_a_ledger_row(monkeypatch, tmp_path):
    store = metering.MeterStore(str(tmp_path / "m.db"))
    models.reset(store)

    async def slow(request, notes, client, started, sent):
        sent[0] = True
        await asyncio.sleep(10)
    monkeypatch.setattr(models, "_ask", slow)
    req = models.AgentRequest(agent="categorizer", task="place", switch_setting="categorizer",
                              model_setting="categorizer_model", rule_book="nando.md",
                              schema=categorizer.TiePick, instructions="", timeout=20,
                              max_tokens=10)

    async def go():
        task = asyncio.ensure_future(models.ask(req))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    try:
        asyncio.run(go())
        assert store.agent_summary()["categorizer"]["requests"] == 1
    finally:
        models.reset(None)


def test_nandos_candidates_are_listed_once(tree, model, monkeypatch):  # noqa: F811
    calls = []
    real = categorizer.candidates_for
    monkeypatch.setattr(categorizer, "candidates_for",
                        lambda *a, **k: calls.append(1) or real(*a, **k))

    async def ask(request, notes=None, client=None):
        return models.AgentResult(models.OK, "contextualizer", "t", value=answer(), sent=True)
    monkeypatch.setattr(models, "ask", ask)
    monkeypatch.setattr(sg.prefetch, "warm_brief", lambda *a, **k: None)
    notes = notes_with("contextualizer", "categorizer")

    async def go():
        gen = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
        await gen.understand(sg.plan_episode("how did the Eagles do", 2), notes)
        if notes.category_tie_task is not None:
            notes.category_tie_task.cancel()
    asyncio.run(go())
    assert len(calls) == 1


# ------------------------------------------------------------------ holds
@pytest.fixture
def signed(monkeypatch):
    from agents import rulebooks
    from tests.test_agents_off import ALL_ON, use

    monkeypatch.setattr(rulebooks, "check", lambda book: rulebooks.Lock(ok=True, version="1"))
    use(monkeypatch, **ALL_ON)
    netenyahu.reset()
    yield
    netenyahu.reset()


def test_a_hold_whose_write_lands_after_the_read_is_kept(signed, monkeypatch):
    netenyahu.prime()
    netenyahu._remember_hold("editor", "written later")      # before the read began
    monkeypatch.setattr(netenyahu, "_read_held", lambda: {})  # the store has not got it yet
    assert netenyahu.refresh_held() == {"editor": "written later"}
    monkeypatch.setattr(netenyahu, "_read_held", lambda: {"editor": "written later"})
    netenyahu.refresh_held()
    assert "editor" not in netenyahu._MADE                   # the store has it now


def test_a_store_down_since_boot_is_asked_once_an_interval(signed, monkeypatch):
    starts = []
    monkeypatch.setattr(netenyahu, "_read_held", lambda: 1 / 0)
    real = netenyahu.threading.Thread

    def spy(*a, **k):
        starts.append(1)
        return real(*a, **k)
    monkeypatch.setattr(netenyahu.threading, "Thread", spy)
    netenyahu.for_episode("search")
    for thread in threading.enumerate():
        if thread.name == "netenyahu-holds":
            thread.join(5)
    for _ in range(5):
        netenyahu.for_episode("search")
    assert len(starts) == 1


# ------------------------------------------------------------------ C3P0's answer
@pytest.mark.parametrize("text, result", [
    # §364: caught again - missing a guessed result is the worse mistake,
    # and "Windows 11, Office 365" being flagged is the accepted cost.
    ("Eagles 24, Giants 17", True), ("Eagles lost on Sunday", True),
    ("Chiefs clinched the division", True), ("October 10-11", False),
    ("Oct 10-12 schedule", False), ("top 10-15 players", False), ("9-11 attacks", False),
    ("a 50-50 chance", False), ("the 4-3 defense", False), ("Boeing 737-800", False),
    ("rated 4-5 stars", False), ("won't", False),
    ("how many games have the Eagles won", False), ("Windows 11 Office 365", False),
])
def test_more_results_and_non_results(text, result):
    assert contextualizer.has_result(text) is result


@pytest.mark.parametrize("person, query, asked", [
    ("Ron", "what does Byron think", False),
    ("Biden", "Biden said GDP grew 3%, true?", False),
    ("Biden", "Biden says he will veto the bill", False),
    ("Taylor Swift", "Taylor Swift's views count on YouTube", False),
    ("Krugman", "Krugman's position in the rankings", False),
    # §363: C3P0 writes the name as the request has it; the last name alone
    # also matched "Jordan thinks about peace with Israel" for Michael Jordan.
    ("Paul Krugman", "What does Krugman think", False),
])
def test_more_views_and_non_views(person, query, asked):
    assert contextualizer.asks_for_view(person, query) is asked


def test_a_title_over_eighty_characters_is_refused_not_cut():
    long = "The " + "Very " * 18 + "Long Title"
    assert contextualizer.checked(answer(title=long), "q", ())[1]["title"] == ""


def test_an_answer_that_cannot_be_read_fails_once_and_never_falls_back(model, monkeypatch):  # noqa: F811
    async def ask(request, notes=None, client=None):
        return models.AgentResult(models.OK, "contextualizer", "t", value=answer(), sent=True)
    monkeypatch.setattr(models, "ask", ask)
    monkeypatch.setattr(contextualizer, "checked", lambda *a: 1 / 0)
    brief = asyncio.run(ei.understand("how did the Eagles do", 2,
                                      notes=notes_with("contextualizer")))
    assert "search" not in [part for part, _kw in model["calls"]]
    assert any("search half did not answer" in n for n in brief.notes)


def test_any_failure_before_sending_falls_back(model, monkeypatch):  # noqa: F811
    async def ask(request, notes=None, client=None):
        return models.AgentResult(models.FAILED, "contextualizer", "t", sent=False)
    monkeypatch.setattr(models, "ask", ask)
    asyncio.run(ei.understand("how did the Eagles do", 2, notes=notes_with("contextualizer")))
    assert "search" in [part for part, _kw in model["calls"]]


# ------------------------------------------------------------------ the tile, the vocabulary, the probe
def test_a_written_tile_is_drawn_from_nandos_node(tree, monkeypatch):
    import app as appmod

    class Cache:
        def title(self, key):
            return "A Title"

        def category_pair(self, key):
            return "miami heat", "extreme weather"
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", Cache())
    monkeypatch.setattr(appmod, "_episode_key", lambda plan: "k")
    drawn = []
    monkeypatch.setattr(appmod, "_categorise_written_tile",
                        lambda tile, words: drawn.append(words))
    tile = {"id": "story-1", "source": "pool", "cached": True, "query": "q"}
    appmod._name_written_tiles([tile], 2)
    assert drawn == ["extreme weather"]


def test_a_new_tree_object_is_built_in_the_background(tree, monkeypatch):
    V.index(tree)
    V._CACHE["checked"] -= V.CHECK_SECONDS          # past the throttle (§363)
    other = C.CategoryStore(str(pathlib.Path(tree.path).with_name("other.db")))
    C.apply_seed(other)
    monkeypatch.setattr(V, "_built", lambda t: (_ for _ in ()).throw(AssertionError("inline")))
    started = []
    real = V.threading.Thread
    monkeypatch.setattr(V.threading, "Thread", lambda *a, **k: started.append(1) or real(
        target=lambda: None))
    assert V.index(other) is V._CACHE["index"]
    assert started == [1]


def test_the_probe_meters_itself(monkeypatch, tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import agent_probe
    from agents import adapters

    rows = []
    monkeypatch.setattr(metering.MeterStore, "record_agent_call",
                        lambda self, **kw: rows.append(kw))

    class Fake:
        async def send(self, **kw):
            return adapters.Answer(text="{}", stop_reason="end_turn",
                                   served_model="claude-haiku-5-5", usage=None, raw=None)
    monkeypatch.setattr(adapters, "adapter_for", lambda vendor: Fake())
    asyncio.run(agent_probe.probe("anthropic", "claude-haiku-5-5"))
    assert rows and rows[0]["agent"] == "probe" and rows[0]["status"] == "ok"
