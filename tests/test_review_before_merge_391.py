"""Phase 1 reviewed before merge (§391): what four independent reviews found,
each pinned so it cannot come back."""
from __future__ import annotations

import asyncio
import dataclasses
import threading

import pytest

import categories as C
import category_audit
import category_examples
import category_vocabulary as V
import config
import episode_intelligence as ei
import script_generator as sg
import taste_vectors
import topics
from agents import categorizer, memory, models, netenyahu, training
from agents.categorizer import Placement, Prior
from tests.test_agents_c3p0_call import _prepare, notes_with


@pytest.fixture
def tree():
    t = topics.category_tree()
    C.apply_seed(t)
    V.reset()
    yield t
    V.reset()


def _leaf(tree):
    nodes = tree.nodes()
    parents = {n.parent_id for n in nodes.values()}
    return next(i for i, n in sorted(nodes.items()) if n.parent_id and i not in parents)


# ------------------------------------------------------------------ settle and confirm
def test_nothing_filed_stores_nothing_and_counts_nothing_as_his(tree):
    leaf = _leaf(tree)
    notes = sg.ScriptNotes()
    notes.category_placement = Placement()            # Nando filed nothing
    notes.category = leaf                             # the writer's words
    assert categorizer.settle(notes, "q") == ""
    settled = notes.category_settled
    assert settled["node"] == "" and settled["how"] == "writer"
    row = category_audit.note("q", leaf, "tap", nando=settled, key="k")
    assert row["nando"] == "" and row["nando_request"] == ""


def test_a_providers_filing_is_never_refiled_by_the_writer(tree):
    leaf = _leaf(tree)
    other = next(n for n in sorted(tree.nodes()) if n not in categorizer._branch(leaf)
                 and leaf not in categorizer._branch(n) and n != leaf)
    assert categorizer.confirm(leaf, other, (), locked=True) == (leaf, "kept")
    assert categorizer.confirm(leaf, other, ())[1] == "refiled"
    notes = sg.ScriptNotes()
    notes.category_placement = Placement(node=leaf, how="provider")
    notes.category = other
    assert categorizer.settle(notes, "q") == leaf


def test_a_meaning_match_never_refiles(tree, monkeypatch):
    import category_near

    leaf = _leaf(tree)
    monkeypatch.setattr(category_near, "nearest", lambda *a, **k: "nfl")
    assert categorizer.confirm(leaf, "words no phrase of the tree holds") == (leaf, "kept")


# ------------------------------------------------------------------ prepare
def test_c3p0s_ambiguous_reaches_the_placement(tree, monkeypatch):
    seen = {}
    real = categorizer.place

    def place(*a, **k):
        seen.update(k)
        return real(*a, **k)

    monkeypatch.setattr(categorizer, "place", place)
    brief = ei.Brief(query="how did United do",
                     contextualized={"node": "airlines", "ambiguous": True})
    _prepare(monkeypatch, brief, notes_with("categorizer", "contextualizer"))
    assert seen.get("unsure") is True


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


def test_his_own_call_runs_when_c3p0_is_on_but_not_running(tree, monkeypatch):
    # C3P0 runs only on the split brief; with EI_SPLIT=0 he chooses nothing.
    monkeypatch.setattr(sg, "settings", dataclasses.replace(sg.settings, ei_split=False))
    assert len(_understand(monkeypatch, notes_with("categorizer", "contextualizer"))) == 1
    monkeypatch.setattr(sg, "settings", dataclasses.replace(sg.settings, ei_split=True))
    assert _understand(monkeypatch, notes_with("categorizer", "contextualizer")) == []


# ------------------------------------------------------------------ the candidate list
def test_no_facet_is_lost_past_the_cut(tree, monkeypatch):
    many = tuple(n for n in sorted(tree.nodes()) if n not in topics.FACETS)[:30]
    with_facets = many[:5] + ("science", "health") + many[5:]
    monkeypatch.setattr(categorizer, "clashes", lambda q: [])
    monkeypatch.setattr(categorizer, "prior_for", lambda *a, **k: categorizer.NO_PRIOR)
    monkeypatch.setattr(V, "match", lambda q, *a, **k: (with_facets, "wording", "x"))
    nodes, _tie = categorizer.candidates_for("a long question")
    assert set(topics.FACETS) <= set(nodes)


def test_examples_are_chosen_for_codes_matches_not_the_whole_tree(tree):
    nodes, _tie = categorizer.candidates_for("is creatine worth taking")
    part = categorizer.code_part(nodes)
    # Code found nothing for it: the facets and the tree follow, and none of
    # them makes an unrelated example "on the list".
    assert part == () and len(nodes) > len(topics.FACETS)
    assert len(category_examples.pick("is creatine worth taking", nodes)) == \
        category_examples.PER_REQUEST                  # what went wrong (§391)
    text = "\n".join(t for _tag, t in categorizer.teaching("is creatine worth taking", nodes))
    assert "Worked examples" not in text


def test_a_named_thing_that_means_two_things_is_refused_by_the_loader(tree, monkeypatch):
    monkeypatch.setattr(V, "load", lambda *a, **k: {"nodes": {}, "entities": {
        "eagles": {"node": "philadelphia eagles", "kind": "team"}}})
    V.reset()
    built = V._built(tree)
    assert "eagles" not in built or built["eagles"][2] != "entity"


# ------------------------------------------------------------------ the model layer
def test_a_cancelled_call_is_recorded_as_cancelled_never_a_failure(monkeypatch):
    rows, errors = [], []
    monkeypatch.setattr(models, "_write", lambda request, result: rows.append(result))
    import pipeline_errors

    monkeypatch.setattr(pipeline_errors, "record", lambda *a, **k: errors.append(a))

    async def slow(*a, **k):
        await asyncio.sleep(10)

    monkeypatch.setattr(models, "_ask", slow)
    request = models.AgentRequest(agent="categorizer", task="place",
                                  switch_setting="categorizer",
                                  model_setting="categorizer_model", rule_book="nando.md",
                                  schema=categorizer.TiePick, instructions="x",
                                  timeout=8.0, max_tokens=10)

    async def go():
        task = asyncio.ensure_future(models.ask(request))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(go())
    assert [r.status for r in rows] == [models.CANCELLED] and errors == []


def test_health_reports_the_judges_batch_size():
    entry = models.health()["agents"]["categorizer"]
    assert entry["judge_per_run"]["value"] == config.settings.nando_judge_per_run


# ------------------------------------------------------------------ the answer key
def _failing(monkeypatch, status="timeout"):
    calls = []

    async def ask(request):
        calls.append(dict(request.material)["request"])
        return models.AgentResult(status=status, agent="categorizer", task="judge")
    monkeypatch.setattr(models, "ask", ask)
    monkeypatch.setattr(training, "allowed", lambda: (True, ""))
    return calls


def test_failed_grades_never_hold_nandos_live_filing(tree, monkeypatch):
    _failing(monkeypatch)
    netenyahu.reset()
    for i in range(5):
        category_audit.note(f"q{i}", "", "tap", key=f"k{i}")
    asyncio.run(training.judge_batch(lambda _k: ["s"], limit=5))
    assert netenyahu.report("categorizer", "ok") is None
    assert "categorizer" not in netenyahu._FAILURES


def test_an_episode_is_asked_about_once_answered_or_not(tree, monkeypatch):
    calls = _failing(monkeypatch)
    category_audit.note("only once", "", "tap", key="k")
    asyncio.run(training.judge_batch(lambda _k: ["s"]))
    asyncio.run(training.judge_batch(lambda _k: ["s"]))
    assert calls == ["only once"]


def test_the_batch_reads_the_stores_off_the_event_loop(tree, monkeypatch):
    loop_thread = []
    _failing(monkeypatch)
    category_audit.note("q", "", "tap", key="k")

    def script_for(_key):
        loop_thread.append(threading.current_thread() is threading.main_thread())
        return ["s"]
    asyncio.run(training.judge_batch(script_for))
    assert loop_thread == [False]


def test_agreeing_that_no_node_fits_is_agreement_never_a_mistake(tree):
    category_audit.note("what is love", "", "tap", key="a", nando={"node": ""})
    training.store().put_label("a", "what is love", "", "high", "", "m")
    assert training.verdict("", "") == "exact"
    assert training.mistakes() == []


def test_the_mistakes_window_is_the_audits():
    assert training.MISTAKE_DAYS <= C.AUDIT_DAYS


def test_an_unreadable_tree_never_retires_every_grade(tree):
    leaf = _leaf(tree)
    training.store().put_label("a", "q", leaf, "high", "", "m",
                               path=training._path_text(leaf))
    training.follow_tree()
    out = training.follow_tree({})
    assert out.get("skipped") and not training.store().labels()[0]["retired_at"]


def test_a_wipe_forgets_the_copies_in_memory_too(tree, monkeypatch):
    import demo_data

    memory._STATE["built"] = ([{"question": "q", "node": "nfl", "source": "graded"}], None)
    category_examples._STATE["learned"] = [{"question": "q", "node": "nfl"}]
    demo_data._forget_what_the_log_taught()
    assert memory._STATE["built"] is None and category_examples._STATE["learned"] == []


def test_c3p0_ahead_embeds_his_memory_off_the_event_loop(tree, monkeypatch):
    from agents import contextualizer

    where = []
    monkeypatch.setattr(memory, "recall", lambda q, k=5, inline=0: where.append(
        (inline, threading.current_thread() is threading.main_thread())) or [])

    async def ask(request, notes=None):
        return models.AgentResult(status="off", agent="contextualizer", task="contextualise")
    monkeypatch.setattr(models, "ask", ask)

    async def fallback():
        return ("ok", {}, "")
    asyncio.run(contextualizer.search_half("q", 2, "", "now", (), True, None,
                                           ("science",), fallback))
    assert (1, False) in where and (1, True) not in where
