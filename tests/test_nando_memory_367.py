"""Nando's memory of graded answers (§367, training piece N4): past
requests like this one, chosen by meaning, with the node each was truly
about - shown to the model, and on his list."""
from __future__ import annotations

import asyncio
import dataclasses

import pytest

import categories as C
import taste_vectors
import topics
from agents import categorizer, memory, models, training

#: A stand-in sentence model: a text points along its first concept word.
_AXES = {"zoo": 0, "lions": 0, "touchdown": 1, "eagles": 1, "hurricane": 2}


def _encoder(texts):
    out = []
    for text in texts:
        vec = [0.0] * 40
        for word, axis in _AXES.items():
            if word in text.lower():
                vec[axis] = 1.0
                break
        else:
            vec[3 + sum(map(ord, text)) % 36] = 1.0
        out.append(vec)
    return out


@pytest.fixture
def tree():
    t = topics.category_tree()
    C.apply_seed(t)
    yield t


@pytest.fixture
def meaning():
    taste_vectors.reset()
    taste_vectors.set_encoder(_encoder)
    memory.reset()
    yield
    taste_vectors.set_encoder(None)
    taste_vectors.reset()
    memory.reset()


def _grade(query, node, confidence="high", key=None):
    training.store().put_label(key or query, query, node, confidence, "", "m")


def _a_node(tree):
    return next(n for n in sorted(tree.nodes()) if n not in topics.FACETS)


def test_with_no_model_nothing_is_remembered(tree):
    _grade("Detroit lions at the zoo", "science")
    assert memory.recall("lions at the Detroit zoo", inline=1) == []


def test_a_graded_request_is_recalled_by_meaning(tree, meaning):
    _grade("Detroit lions at the zoo", "science")
    found = memory.recall("the zoo got new lions", inline=1)
    assert found and found[0]["node"] == "science" and found[0]["source"] == "graded"
    assert all(r["question"] != "Detroit lions at the zoo"
               for r in memory.recall("a hurricane hits Florida", inline=1))


def test_only_sure_grades_are_remembered_and_none_is_an_answer(tree, meaning):
    _grade("lions at the zoo, unsure", "sports", "low")
    _grade("zoo lions, nothing fits", "")
    found = memory.recall("zoo lions", inline=1)
    assert [r["node"] for r in found if r["source"] == "graded"] == [""]


def test_a_node_the_tree_lost_is_never_recalled(tree, meaning):
    _grade("zoo lions grade", "a node that went away")
    assert all(r["node"] != "a node that went away"
               for r in memory.recall("zoo lions", inline=1))


def test_on_a_search_nothing_is_embedded(tree, meaning, monkeypatch):
    _grade("Detroit lions at the zoo", "science")
    memory.recall("warm", inline=1)                       # the matrix is built
    asked = []
    real = taste_vectors.vectors

    def spy(texts, inline=0):
        asked.append(inline)
        return real(texts, inline=inline)

    monkeypatch.setattr(taste_vectors, "vectors", spy)
    memory.recall("zoo lions today", inline=0)
    assert asked == [0]


def test_recalled_nodes_join_the_candidates_and_the_teaching(tree, meaning):
    node = _a_node(tree)
    _grade("Eagles touchdown last night", node)
    memory.recall("Eagles touchdown", inline=1)             # vector made
    nodes, _tie = categorizer.candidates_for("Eagles touchdown")
    assert node in nodes
    text = "\n".join(t for _tag, t in categorizer.teaching("Eagles touchdown", nodes))
    assert f"Eagles touchdown last night -> {node}" in text


def test_nandos_own_call_embeds_the_request_and_may_choose_a_remembered_node(
        tree, meaning, monkeypatch):
    node = _a_node(tree)
    _grade("zoo lions graded", node)
    sent = []

    async def ask(request, notes=None):
        sent.append(request)
        return models.AgentResult(status="ok", agent="categorizer", task="place",
                                  value=categorizer.TiePick(node=node))

    monkeypatch.setattr(models, "ask", ask)
    picked = asyncio.run(categorizer.ask_tie("lions at the zoo", ("science",)))
    assert picked == node
    assert "zoo lions graded" in "\n".join(t for _tag, t in sent[0].material)


def test_the_store_and_the_examples_are_both_remembered(tree, meaning):
    _grade("zoo lions graded", "science")
    sources = {r["source"] for r in memory.rows()}
    assert sources == {"graded", "example"}
