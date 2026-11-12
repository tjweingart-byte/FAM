"""§555: one answer to "is this node real?", and the checkers held to what
Nando actually files.

Keith raised an URGENT for six days because his dead-node check asked
`tree.get(node)` of `science` and `world` (§552): the facets are the tree's
roots and never rows in it, and Nando's last step files at one. The same
blind spot sat in the tag sync (a facet's episodes listed as dead nodes,
never repaired) and in the housekeeper's re-file (a fact kept at a facet
unfiled). Its tests never showed it, because their fake trees held the
facets as ordinary rows. So these run on the real seeded tree.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import os

import pytest

import categories as C
import topics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _eval():
    spec = importlib.util.spec_from_file_location(
        "nando_eval", os.path.join(ROOT, "tools", "nando_eval.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def tree(tmp_path, monkeypatch):
    store = C.CategoryStore(str(tmp_path / "categories.db"))
    C.apply_seed(store)
    monkeypatch.setattr(topics, "_CATEGORIES", store)
    topics.reset_topic_tags()
    yield store
    topics.reset_topic_tags()


def _filed(tree) -> set:
    """Everything Nando can file on the real tree: every facet, every node,
    and what his code steps actually return for the eval's questions."""
    from agents import categorizer

    ev = _eval()
    questions = [q for q, *_ in ev.QUESTIONS + ev.HELD_OUT + ev.CONTEXT_TAPS]
    found = set(topics.FACETS) | set(tree.nodes())
    for q in questions:
        for placed in (categorizer.suggest(None, q), categorizer.place(None, q)):
            found |= {placed.node} | set(placed.path) | set(placed.candidates)
    found.discard("")
    return found


# ------------------------------------------------------------ the one answer
def test_a_facet_is_held_though_it_is_never_a_row(tree):
    for facet in topics.FACETS:
        assert tree.get(facet) is None, "a facet became a row"
        assert C.holds(tree, facet)


def test_a_node_is_held_and_a_made_up_one_is_not(tree):
    assert C.holds(tree, "nba") and C.holds(tree, "magic")
    assert not C.holds(tree, "not a node")
    assert not C.holds(tree, "")
    assert not C.holds(None, "nba")


def test_it_takes_a_plain_dict_as_tests_do(tree):
    assert C.holds({"nfl": object()}, "nfl")
    assert C.holds({}, "sports")
    assert not C.holds({}, "nfl")


def test_an_unreadable_tree_still_holds_the_facets():
    assert C.holds(C.EmptyTree(), "world")
    assert not C.holds(C.EmptyTree(), "nba")


# ------------------------------------------ every checker against Nando's output
def test_keith_never_calls_anything_nando_files_dead(tree, monkeypatch):
    import agent_checks as AC
    import config

    monkeypatch.setattr(config, "settings",
                        dataclasses.replace(config.settings, agents=True, categorizer=True))
    monkeypatch.setattr(AC, "_NANDO", {"after": "", "seen": set(), "last_pass": set()})
    filed = sorted(_filed(tree))

    class Cache:
        def tagged(self, after="", limit=500):
            rows = [(f"k{i:05d}", n, 1, []) for i, n in enumerate(filed)]
            return [r for r in rows if r[0] > after][:limit]

    monkeypatch.setattr(AC, "SAMPLE", 10_000)
    assert AC.nando_nodes_exist(Cache()) == []
    # And a node the tree truly lacks is still a breach.
    monkeypatch.setattr(AC, "_NANDO", {"after": "", "seen": set(), "last_pass": set()})
    filed.append("not a node")
    [breach] = AC.nando_nodes_exist(Cache())
    assert breach["detail"]["examples"] == ["not a node"]


def test_the_tag_sync_never_lists_anything_nando_files_as_dead(tree):
    import tag_sync

    for node in sorted(_filed(tree)):
        kinds = [k for k, _ in tag_sync.check_episode(
            "k", node, 1.0, [], bucket=None, archive=None, tree=tree)]
        assert "dead node" not in kinds, node
    kinds = [k for k, _ in tag_sync.check_episode(
        "k", "not a node", 1.0, [], bucket=None, archive=None, tree=tree)]
    assert kinds == ["dead node"]


def test_the_housekeeper_never_unfiles_anything_nando_files(tree):
    from agents import housekeeping

    for node in sorted(_filed(tree)):
        now, path = housekeeping.where_now({"node_id": node, "node_path": [node]}, tree)
        assert now == node, node
        assert path and path[-1] == node, (node, path)


def test_a_fact_whose_node_is_gone_falls_back_to_its_facet(tree):
    """§555: before, a fact at `science` - or one whose branch was pruned
    to the facet - was unfiled, because the facet "was not in the tree"."""
    from agents import housekeeping

    gone = {"node_id": "pruned subject", "node_path": ["science", "pruned subject"]}
    assert housekeeping.where_now(gone, tree)[0] == "science"
    assert housekeeping.surviving(["science", "physics", "gone"], tree) == "physics"
    assert housekeeping.surviving(["gone", "also gone"], tree) == ""
