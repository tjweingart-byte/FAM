"""Nando's mistakes become worked examples (§379, training piece N5)."""
from __future__ import annotations

import dataclasses

import pytest

import categories as C
import category_audit
import category_examples
import topics
from agents import netenyahu, training


@pytest.fixture
def tree():
    t = topics.category_tree()
    C.apply_seed(t)
    category_examples.reset()
    yield t
    category_examples.reset()


def _filed(key, query, filed, truth, confidence="high"):
    category_audit.note(query, "", "tap", key=key, nando={"node": filed})
    training.store().put_label(key, query, truth, confidence, "", "m")


def test_a_confident_correction_is_a_mistake_and_a_branch_is_not(tree):
    _filed("a", "Eagles fly south for winter", "nfl", "science")        # wrong
    _filed("b", "Eagles at the Super Bowl", "nfl", "sports")           # branch
    _filed("c", "Eagles schedule", "nfl", "nfl")                       # exact
    _filed("d", "lions at the zoo", "", "science")                     # nothing filed
    _filed("e", "an unsure one", "nba", "science", "medium")           # key unsure
    got = training.mistakes()
    assert [(m["question"], m["node"]) for m in got] == [
        ("lions at the zoo", "science"), ("Eagles fly south for winter", "science")]
    assert {m["source"] for m in got} == {"mistake"}


def test_a_question_is_one_example_however_often_it_was_wrong(tree):
    _filed("a", "Eagles fly south", "nfl", "science")
    _filed("b", "eagles  fly south", "nfl", "science")
    assert len(training.mistakes()) == 1


def test_mistakes_reach_the_examples_and_come_first_among_equals(tree):
    _filed("a", "the Eagles migrate", "nfl", "science")
    category_examples.add_source(training.mistakes)
    category_examples._refresh()
    picked = category_examples.pick("where do the Eagles migrate to", ("science", "nfl"))
    assert picked[0]["question"] == "the Eagles migrate"
    assert picked[0]["node"] == "science"


def test_boot_registers_the_mistakes(monkeypatch):
    added = []
    monkeypatch.setattr(category_examples, "add_source", added.append)
    monkeypatch.setattr(netenyahu, "settings",
                        dataclasses.replace(netenyahu.settings, agents=True))
    netenyahu.prime()
    assert training.mistakes in added


def test_mistakes_never_raise(monkeypatch):
    def boom(_days=0):
        raise RuntimeError("store down")

    monkeypatch.setattr(training, "compared", boom)
    assert training.mistakes() == []
