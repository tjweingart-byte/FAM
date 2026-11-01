"""Nando's small trained model (§385, training piece N6)."""
from __future__ import annotations

import dataclasses
import random

import pytest

import categories as C
import topics
from agents import categorizer, nando_model, netenyahu, training


@pytest.fixture
def tree():
    t = topics.category_tree()
    C.apply_seed(t)
    yield t


def _nodes(tree, n=3):
    return [x for x in sorted(tree.nodes()) if x not in topics.FACETS][:n]


def _rows(nodes, per=60, seed=0):
    """Separable synthetic requests: each node has its own vocabulary."""
    rng = random.Random(seed)
    vocab = {node: [f"{node.replace(' ', '')}word{i}" for i in range(6)] for node in nodes}
    out = []
    for node in nodes:
        for i in range(per):
            words = rng.sample(vocab[node], 3) + [f"filler{rng.randrange(1000)}"]
            out.append({"question": " ".join(words) + f" {i}", "node": node})
    return out


def test_too_little_to_learn_from_trains_nothing():
    assert nando_model.train([{"question": "a b", "node": "x"}] * 5) is None


def test_it_learns_and_trusts_itself_only_when_the_held_out_rows_say_so(tree):
    nodes = _nodes(tree)
    model = nando_model.train(_rows(nodes))
    assert model is not None and model.stats["held_out_accuracy"] >= 0.95
    assert model.trusted_at == nando_model.SURE
    word = nodes[1].replace(" ", "") + "word2"
    assert model.read(f"{word} {nodes[1].replace(' ', '')}word4")[0] == nodes[1]


def test_noisy_labels_are_never_trusted(tree):
    nodes = _nodes(tree)
    rows = _rows(nodes)
    rng = random.Random(1)
    for r in rows:
        r["node"] = rng.choice(nodes)                 # labels say nothing
    model = nando_model.train(rows)
    assert model is not None and model.trusted_at is None


def test_saved_loaded_and_forgotten(tree):
    model = nando_model.train(_rows(_nodes(tree)))
    nando_model.save(model)
    again = nando_model.load()
    assert again.classes == model.classes and again.trusted_at == model.trusted_at
    assert nando_model.forget() == 1 and nando_model.load() is None


def _installed(tree):
    nodes = _nodes(tree)
    nando_model.save(nando_model.train(_rows(nodes)))
    nando_model.reset()
    assert nando_model.current(background=False) is not None
    return nodes


def test_a_trusted_reading_on_the_list_files_and_anything_else_suggests(tree, monkeypatch):
    nodes = _installed(tree)
    query = nodes[0].replace(" ", "") + "word1 " + nodes[0].replace(" ", "") + "word3"
    monkeypatch.setattr(categorizer, "suggest", lambda *a, **k: categorizer.Placement(
        node="", path=(), how="", candidates=(nodes[0],), confidence=0.0))
    placed = categorizer.place(None, query)
    assert placed.node == nodes[0] and placed.how == "model"
    # Not on the list: never filed.
    monkeypatch.setattr(categorizer, "suggest", lambda *a, **k: categorizer.Placement(
        node="", path=(), how="", candidates=(nodes[2],), confidence=0.0))
    assert categorizer.place(None, query).node == ""
    # The brief said unsure: never filed.
    monkeypatch.setattr(categorizer, "suggest", lambda *a, **k: categorizer.Placement(
        node="", path=(), how="", candidates=(nodes[0],), confidence=0.0))
    assert categorizer.place(None, query, unsure=True).node == ""


def test_an_untrusted_model_never_files(tree, monkeypatch):
    nodes = _installed(tree)
    model = nando_model.current(background=False)
    monkeypatch.setattr(model, "trusted_at", None)
    query = nodes[0].replace(" ", "") + "word1"
    monkeypatch.setattr(categorizer, "suggest", lambda *a, **k: categorizer.Placement(
        node="", path=(), how="", candidates=(nodes[0],), confidence=0.0))
    assert categorizer.place(None, query).node == ""


def test_a_solid_source_always_wins_over_the_model(tree):
    _installed(tree)
    placed = categorizer.place(None, "anything", picked="nfl")
    assert placed.node == "nfl" and placed.how == "picked"


def test_its_reading_is_taught_and_listed(tree):
    nodes = _installed(tree)
    query = nodes[2].replace(" ", "") + "word0 " + nodes[2].replace(" ", "") + "word5"
    text = "\n".join(t for _tag, t in categorizer.teaching(query, ()))
    assert "trained model" in text and nodes[2] in text and "guess" in text
    found = categorizer.candidates_for(query)[0]
    # Among code's own matches, before the facets and the tree (§389).
    assert found.index(nodes[2]) < found.index("business")


def test_no_model_no_reading(tree):
    assert nando_model.read("anything") == ("", 0.0, False)


def test_retrain_learns_from_memorys_rows(tree, monkeypatch):
    rows = _rows(_nodes(tree))
    monkeypatch.setattr(nando_model, "training_rows", lambda: rows)
    out = nando_model.retrain()
    assert out["trained"] and out["rows"] == len(rows) and nando_model.load() is not None


def test_netenyahu_trains_it_nightly_after_the_answer_key(monkeypatch):
    assert ("categorizer", "train", 4, "categorizer") in netenyahu.SCHEDULE
    monkeypatch.setattr(netenyahu, "HANDLERS", {})
    training.register(lambda _k: [])
    assert ("categorizer", "train") in netenyahu.HANDLERS


def test_the_wipe_forgets_the_model(tree):
    import demo_data

    nando_model.save(nando_model.train(_rows(_nodes(tree))))
    assert demo_data._forget_what_the_log_taught()["nando_model_dropped"] == 1
