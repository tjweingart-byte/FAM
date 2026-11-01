"""Nando is taught on every call (§380, training piece N2): worked examples
chosen for the request, code's hints said to be hints, the definitions of
the candidates, and every facet on the list so a broader true answer is
always there."""
from __future__ import annotations

import asyncio
import json
import pathlib

import pytest

import categories as C
import category_examples as E
import category_vocabulary as V
import topics
from agents import categorizer, models

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def tree():
    t = topics.category_tree()
    C.apply_seed(t)
    V.reset()
    E.reset()
    yield t
    V.reset()
    E.reset()


def test_every_example_is_a_seeded_node_or_none(tree):
    data = json.loads((ROOT / "category_examples.json").read_text())
    assert len(data["examples"]) >= 55
    for ex in data["examples"]:
        assert ex["node"] == "" or tree.get(ex["node"]) is not None, ex


def test_no_example_is_one_of_the_measured_questions():
    import sys
    sys.path.insert(0, str(ROOT / "tools"))
    import nando_eval

    measured = {q for q, _l in nando_eval.QUESTIONS} | {q for q, _l in nando_eval.HELD_OUT}
    assert not measured & {e["question"] for e in E.load()}


def test_examples_are_chosen_for_the_request(tree):
    chosen = E.pick("Detroit Lions trade rumors", ("detroit lions", "nfl"))
    assert chosen and chosen[0]["node"] in ("detroit lions", "nfl")
    assert len(chosen) <= E.PER_REQUEST
    assert sum(1 for e in E.pick("kings and queens", ()) if not e["node"]) <= E.NONE_PER_REQUEST


def test_a_request_is_never_shown_its_own_answer(tree):
    assert all(e["question"] != "what happened to the Detroit Lions"
               for e in E.pick("what happened to the Detroit Lions", ("detroit lions",)))


def test_learned_examples_join_in_the_background(tree, monkeypatch):
    E.add_source(lambda: [{"question": "zebra stripes explained", "node": "biology"}])
    try:
        E.examples()
        for thread in __import__("threading").enumerate():
            if thread.name == "category-examples":
                thread.join(5)
        assert any(e["question"] == "zebra stripes explained" for e in E.examples())
    finally:
        E._SOURCES.clear()


def test_hints_say_they_are_hints(tree):
    hints = categorizer.hint_block("Detroit lions at the zoo")
    assert "often wrong" in hints and "detroit lions" in hints
    assert '"kings" can mean' in categorizer.hint_block("Kings game tonight")


def test_every_facet_is_always_a_candidate(tree):
    nodes, _tie = categorizer.candidates_for("Detroit lions at the zoo")
    assert set(topics.FACETS) <= set(nodes)
    assert nodes[0] == "detroit lions"


def test_nandos_own_call_carries_the_teaching(tree, monkeypatch):
    seen = {}

    async def ask(request, notes=None, client=None):
        seen["material"] = request.material
        return models.AgentResult("ok", "categorizer", "place",
                                  value=categorizer.TiePick(node="science"))
    monkeypatch.setattr(models, "ask", ask)
    nodes, _ = categorizer.candidates_for("Detroit lions at the zoo")
    assert asyncio.run(categorizer.ask_tie("Detroit lions at the zoo", nodes)) == "science"
    texts = " ".join(t for _tag, t in seen["material"])
    assert "Worked examples" in texts and "Hints:" in texts


def test_c3p0s_call_carries_the_teaching_when_nando_works(tree, monkeypatch):
    from tests.test_ei_split_268 import UNDERSTOOD  # noqa: F401
    import episode_intelligence as ei
    from agents import contextualizer
    from tests.test_agents_c3p0_call import answer, notes_with

    seen = {}

    async def ask(request, notes=None, client=None):
        seen["material"] = request.material
        return models.AgentResult("ok", "contextualizer", "t", value=answer(), sent=True)
    monkeypatch.setattr(models, "ask", ask)

    async def own(*a, **k):
        return "ok", dict(UNDERSTOOD), ""
    monkeypatch.setattr(ei, "_ask", lambda *a, **k: own())
    monkeypatch.setattr(ei, "build_async_client", lambda key=None: object())
    monkeypatch.setattr(ei.credentials, "active", lambda name: "sk-test")
    asyncio.run(ei.understand("Detroit Lions trade rumors", 2,
                              notes=notes_with("contextualizer", "categorizer")))
    texts = " ".join(t for _tag, t in seen["material"])
    assert "Worked examples" in texts and "Candidate nodes" in texts


def test_teaching_never_raises(tree, monkeypatch):
    monkeypatch.setattr(E, "pick", lambda *a, **k: 1 / 0)
    assert categorizer.hint_block("x") == "" or isinstance(categorizer.hint_block("x"), str)
