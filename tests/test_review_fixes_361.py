"""What the review of Phase 1 found (§361), each pinned: nothing of the
agents' bookkeeping on the listener's path, a tie never guessed, C3P0's
answer held to the rules it claimed, and no second request for one site.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
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
from tests.test_agents_c3p0_call import answer, notes_with
from tests.test_ei_split_268 import model  # noqa: F401

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def tree():
    t = topics.category_tree()
    C.apply_seed(t)
    V.reset()
    yield t
    V.reset()


# ------------------------------------------------------------------ the ledger, off the event loop
def test_the_ledger_row_is_written_off_the_loop_and_flush_waits_for_it(monkeypatch, tmp_path):
    store = metering.MeterStore(str(tmp_path / "metering.db"))
    models.reset(store)
    monkeypatch.setattr(models, "RECORD_OFF_LOOP", True)
    on_loop = []
    real = models._record

    def spy(request, result):
        on_loop.append(threading.current_thread() is threading.main_thread())
        real(request, result)
    monkeypatch.setattr(models, "_record", spy)
    try:
        request = models.AgentRequest(
            agent="contextualizer", task="contextualise", switch_setting="contextualizer",
            model_setting="contextualizer_model", rule_book="c3p0.md",
            schema=contextualizer.SearchPlan, instructions="x", timeout=1.0, max_tokens=10)
        from config import settings
        monkeypatch.setattr(models, "settings", dataclasses.replace(
            settings, agents=True, contextualizer=True))
        result = asyncio.run(models.ask(request))       # held or misconfigured: recorded
        models.flush()
        assert on_loop == [False]
        assert store.agent_summary()["contextualizer"]["requests"] == 1
        assert result.status != models.OK
    finally:
        models.reset(None)


def test_a_failure_after_sending_is_marked_sent(monkeypatch):
    """A layer failure after the request left must say so, so its caller
    never sends a second one (review, §361)."""
    async def boom(request, notes, client, started, sent):
        sent[0] = True
        raise RuntimeError("metering broke after the answer")
    monkeypatch.setattr(models, "_ask", boom)
    req = models.AgentRequest(agent="contextualizer", task="t", switch_setting="contextualizer",
                              model_setting="contextualizer_model", rule_book="c3p0.md",
                              schema=contextualizer.SearchPlan, instructions="", timeout=1,
                              max_tokens=1)
    result = asyncio.run(models.ask(req))
    assert result.status == models.FAILED and result.sent is True


def test_c3p0_never_sends_twice_for_one_brief(model, monkeypatch):  # noqa: F811
    async def ask(request, notes=None, client=None):
        return models.AgentResult(models.FAILED, "contextualizer", "contextualise",
                                  reason="layer failed", sent=True)
    monkeypatch.setattr(models, "ask", ask)
    brief = asyncio.run(ei.understand("how did the Eagles do", 2,
                                      notes=notes_with("contextualizer")))
    assert "search" not in [part for part, _kw in model["calls"]]
    assert any("search half did not answer" in n for n in brief.notes)


def test_a_request_that_cannot_be_built_runs_the_briefs_own_half(model, monkeypatch):  # noqa: F811
    monkeypatch.setattr(contextualizer, "instructions", lambda: 1 / 0)

    async def ask(*_a, **_k):
        raise AssertionError("sent")
    monkeypatch.setattr(models, "ask", ask)
    brief = asyncio.run(ei.understand("how did the Eagles do", 2,
                                      notes=notes_with("contextualizer")))
    assert "search" in [part for part, _kw in model["calls"]]
    assert brief.search_query


# ------------------------------------------------------------------ holds
@pytest.fixture
def signed(monkeypatch):
    from agents import roster, rulebooks
    from tests.test_agents_off import ALL_ON, use

    monkeypatch.setattr(rulebooks, "check", lambda book: rulebooks.Lock(ok=True, version="1"))
    use(monkeypatch, **ALL_ON)
    netenyahu.reset()
    yield
    netenyahu.reset()


def test_a_refresh_racing_a_hold_never_drops_it(signed, monkeypatch):
    netenyahu.prime()

    def read_while_held():
        netenyahu._remember_hold("editor", "made during the read")
        return {}
    monkeypatch.setattr(netenyahu, "_read_held", read_while_held)
    assert netenyahu.refresh_held() == {"editor": "made during the read"}
    assert "editor" not in netenyahu.for_episode("search").agents


def test_a_failed_refresh_waits_a_full_interval(signed, monkeypatch):
    netenyahu.prime()
    before = netenyahu._HELD["at"]
    time.sleep(0.01)
    monkeypatch.setattr(netenyahu, "_read_held", lambda: 1 / 0)
    netenyahu.refresh_held()
    assert netenyahu._HELD["at"] > before


def test_a_hold_from_the_listeners_path_is_written_in_the_background(signed, monkeypatch):
    netenyahu.prime()
    monkeypatch.setattr(netenyahu, "HOLD_OFF_LOOP", True)
    written = threading.Event()
    real = netenyahu.hold

    def hold(role, reason):
        real(role, reason)
        written.set()
    monkeypatch.setattr(netenyahu, "hold", hold)
    for _ in range(netenyahu.HOLD_AFTER):
        netenyahu.report("supervisor", "timeout")
    # Held in the copy at once, before the store is written.
    assert "supervisor" not in netenyahu.for_episode("search").agents
    assert written.wait(5)
    assert "supervisor" in tasks.store().held()


# ------------------------------------------------------------------ the vocabulary, never rebuilt on the request path
def test_once_built_the_vocabulary_answers_without_rebuilding(tree, monkeypatch):
    V.index(tree)
    monkeypatch.setattr(V, "_built", lambda t: (_ for _ in ()).throw(AssertionError("rebuilt")))
    monkeypatch.setattr(V, "_stamp", lambda t: (_ for _ in ()).throw(AssertionError("stat")))
    assert V.match("Lakers game tonight", tree=tree)[0] == ("los angeles lakers",)


def test_a_stale_vocabulary_is_checked_in_the_background(tree, monkeypatch, tmp_path):
    path = tmp_path / "v.json"
    path.write_text(json.dumps({"version": 1, "nodes": {}, "entities": {}}))
    monkeypatch.setattr(V, "PATH", path)
    V.reset()
    V.index(tree)
    path.write_text(json.dumps({"version": 1, "entities": {},
                                "nodes": {"rental market": {"wordings": ["rents"]}}}))
    V._CACHE["checked"] -= V.CHECK_SECONDS + 1
    V.match("why rents keep rising", tree=tree)          # answers from the old copy
    for thread in threading.enumerate():
        if thread.name == "category-vocabulary":
            thread.join(5)
    assert V.match("why rents keep rising", tree=tree)[0] == ("rental market",)


def test_definitions_come_from_memory(tree, monkeypatch):
    V.index(tree)
    monkeypatch.setattr(V, "load", lambda *a: (_ for _ in ()).throw(AssertionError("file read")))
    assert V.definition("rental market") == {}


# ------------------------------------------------------------------ Nando
def test_a_cue_that_holds_the_name_is_read(tree):
    assert categorizer.place(None, "New York Giants roster").node == "nfl"
    assert categorizer.place(None, "Private jets for billionaires").node == "air travel"


def test_meaning_is_never_embedded_on_the_listeners_path(tree, monkeypatch):
    import category_near

    seen = []
    monkeypatch.setattr(category_near, "nearest",
                        lambda text, facet="", inline=1: seen.append(inline) or "")
    categorizer.place(ei.Brief(query="q", category="zqxv blorp"), "q")
    assert seen == [0]


def test_each_candidates_parent_comes_right_after_it(tree):
    nodes, _tie = categorizer.candidates_for("how did United do")
    for i, node in enumerate(nodes):
        path = categorizer.path_for(node)
        if len(path) > 1 and path[-2] in nodes:
            assert nodes.index(path[-2]) <= i + 1 or nodes.index(path[-2]) < i


def test_the_tie_call_is_cancelled_even_when_c3p0_picked(tree, monkeypatch):
    from tests.test_agents_c3p0_call import _prepare

    brief = ei.Brief(query="how did United do", contextualized={"node": "airlines"})
    notes = notes_with("categorizer")
    box = {}

    async def go():
        box["task"] = asyncio.ensure_future(asyncio.sleep(10, result="premier league"))
        notes.category_tie_task = box["task"]
        await asyncio.sleep(0)
    asyncio.run(go())
    _prepare(monkeypatch, brief, notes)
    assert box["task"].cancelled() or box["task"].cancelling()
    assert notes.category_placement.node == "airlines"


def test_trending_in_memory_never_reads_the_store(monkeypatch):
    import trending_bank

    monkeypatch.setattr(trending_bank, "_CURRENT", None)
    monkeypatch.setattr(trending_bank, "store", lambda: 1 / 0)
    assert trending_bank.stories_in_memory() == []


# ------------------------------------------------------------------ the one reader asks the store once
def test_the_reader_asks_the_store_once(tree, monkeypatch, tmp_path):
    import app as appmod
    import cache as cache_mod

    store = cache_mod.SqliteScriptCache(str(tmp_path / "s.db"))
    store.put("k", ["One."], 3600, "q", category="stock market", category_node="nfl")
    calls = []
    real = store._conn

    def counting():
        calls.append(1)
        return real()
    monkeypatch.setattr(store, "_conn", counting)
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", store)
    assert appmod._episode_category("k") == "nfl"
    assert len(calls) == 1
    assert store.category_pair("k") == ("stock market", "nfl")
    assert cache_mod.MemoryScriptCache().category_pair("nothing") == ("", "")


# ------------------------------------------------------------------ C3P0's answer
@pytest.mark.parametrize("text, result", [
    ("Eagles Giants final score 2026-10-11", False),
    ("Ages 18-25 voting", False),
    ("5-7 day forecast", False),
    ("Can the Chiefs beat the Bills?", False),
    ("Will Eagles beat Giants", False),
    ("Chiefs topped Bills", True),
    ("Eagles 24-17", True),
])
def test_dates_ranges_and_questions_are_not_results(text, result):
    assert contextualizer.has_result(text) is result


@pytest.mark.parametrize("person, query, asked", [
    ("Paul Krugman", "What does Paul Krugman think about the tariffs?", True),
    ("Krugman", "Krugman's take on inflation", True),
    ("Taylor Swift", "How many views did Taylor Swift's video get", False),
    ("Biden", "Do people think Biden is too old", False),
])
def test_an_opinion_is_asked_for_only_by_name(person, query, asked):
    assert contextualizer.asks_for_view(person, query) is asked


def test_the_forecast_and_archive_inputs_are_checked():
    search, extras, _why = contextualizer.checked(
        answer(forecast_query="Eagles won 24-17",
               archive={"lookup": "background", "what": "after the Eagles beat the Giants"}),
        "q", ())
    assert search["forecast_query"] == ""
    assert extras["archive"] == {"lookup": "none", "what": ""}


@pytest.mark.parametrize("title, kept", [
    ("How Did The Eagles Do?", False),        # the question handed back
    ("Eagles", False),                        # too short
    ("The Eagles' Long Road Back To The Top Of The NFC East", False),  # too long
    ("The Eagles' Season So Far", True),
])
def test_c3p0s_title_is_cleaned_and_three_to_eight_words(title, kept):
    search, extras, _w = contextualizer.checked(answer(title=title), "how did the Eagles do", ())
    assert bool(extras["title"]) is kept and search["title"] == extras["title"]


# ------------------------------------------------------------------ the drafting tool
def test_drafting_never_writes_the_deployments_tree(monkeypatch, tmp_path, capsys):
    sys.path.insert(0, str(ROOT / "tools"))
    import draft_category_vocabulary as D

    empty = C.CategoryStore(str(tmp_path / "live.db"))
    monkeypatch.setattr(topics, "category_tree", lambda: empty)
    monkeypatch.setattr(sys, "argv", ["draft_category_vocabulary.py", "--dry-run"])
    assert D.main() == 0
    assert empty.nodes() == [] or not list(empty.nodes())


# ------------------------------------------------------------------ with the agents off
def test_off_the_brief_and_every_call_are_what_they_were(model, monkeypatch):  # noqa: F811
    """The same brief and the same model calls with the agents off as with
    C3P0's module unable to run at all."""
    def run(notes):
        model["calls"].clear()
        brief = asyncio.run(ei.understand("how did the Eagles do", 2, notes=notes))
        return brief.as_dict(), [(p, {k: v for k, v in kw.items() if k != "messages"})
                                 for p, kw in sorted(model["calls"], key=lambda c: c[0])]
    off = run(netenyahu.OFF and notes_with())
    monkeypatch.setattr(contextualizer, "search_half",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("C3P0 ran")))
    monkeypatch.setattr(categorizer, "candidates_for",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("Nando ran")))
    for notes in (None, sg.ScriptNotes(), notes_with()):
        assert run(notes) == off
