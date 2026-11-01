"""The drafted vocabulary is cleaned before a person reads it, and the
loader refuses the same wordings whatever the file says (§389)."""
from __future__ import annotations

import json
import sys
import pathlib

import pytest

import categories as C
import category_vocabulary as V
import topics

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_tool():
    # Loaded from its file, never by putting tools/ on the path: tools/ has
    # its own trending_bank.py, which would shadow the real module.
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "draft_category_vocabulary_385", ROOT / "tools" / "draft_category_vocabulary.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


D = _load_tool()


@pytest.fixture
def tree():
    t = topics.category_tree()
    C.apply_seed(t)
    V.reset()
    yield t
    V.reset()


def _parent_and_child(tree):
    for i, n in sorted(tree.nodes().items()):
        if n.parent_id and tree.get(n.parent_id) is not None:
            return n.parent_id, i


def test_a_lone_letter_never_stands_for_a_node(tree):
    assert V.refused("m a", "mergers acquisitions", tree)
    assert V.refused("a s", "oakland athletics", tree)
    # Anchored by a longer word: kept.
    assert not V.refused("e commerce", "ecommerce", tree)
    assert not V.refused("women s health", "womens health", tree)
    assert not V.refused("f1", "formula one", tree)


def test_another_nodes_name_and_two_meaning_names_are_refused(tree):
    parent, child = _parent_and_child(tree)
    assert "own name" in V.refused(child, parent, tree)
    assert "two things" in V.refused("hurricanes", "extreme weather", tree)


def test_im_a_fan_is_not_mergers(tree, monkeypatch):
    monkeypatch.setattr(V, "load", lambda *a, **k: {"nodes": {
        "mergers acquisitions": {"wordings": ["m a", "corporate takeovers"]}}, "entities": {}})
    V.reset()
    assert "mergers acquisitions" not in V.match("I'm a Lakers fan")[0]
    assert V.match("corporate takeovers this year")[0] == ("mergers acquisitions",)


def test_a_shared_wording_goes_to_the_broadest_on_one_branch_or_to_none(tree):
    parent, child = _parent_and_child(tree)
    siblings = [i for i, n in sorted(tree.nodes().items())
                if n.parent_id and i != child and n.parent_id != parent
                and parent not in tree.ancestors(i) and i != parent][:1]
    data = {"nodes": {parent: {"wordings": ["shared branch words", "both branches"]},
                      child: {"wordings": ["shared branch words"]},
                      siblings[0]: {"wordings": ["both branches"]}},
            "entities": {}}
    data, notes = D.clean_draft(data, tree)
    assert data["nodes"][parent]["wordings"] == ["shared branch words"]
    assert data["nodes"][child]["wordings"] == []
    assert data["nodes"][siblings[0]]["wordings"] == []
    assert any("kept on " + parent in n for n in notes)
    assert any("dropped from all" in n for n in notes)


def test_clean_mode_sends_nothing_and_says_what_went(tree, tmp_path, monkeypatch):
    pending = tmp_path / "category_vocabulary.pending.json"
    pending.write_text(json.dumps({"version": 1, "entities": {}, "drafted": {},
                                   "nodes": {"mergers acquisitions": {"wordings": ["m a"]}}}))
    monkeypatch.setattr(V, "PENDING", pending)
    monkeypatch.setattr(sys, "argv", ["draft", "--clean"])

    async def never(*a, **k):
        raise AssertionError("--clean sends nothing")

    monkeypatch.setattr(D, "draft_nodes", never)
    assert D.main() == 0
    out = json.loads(pending.read_text())
    assert out["nodes"]["mergers acquisitions"]["wordings"] == []
    assert out["drafted"]["cleaned"]
