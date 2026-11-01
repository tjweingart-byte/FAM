"""Nando's training follows the tree as it grows (§387, piece N7)."""
from __future__ import annotations

import asyncio

import pytest

import categories as C
import category_audit
import topics
from agents import memory, models, netenyahu, tasks, training


@pytest.fixture
def tree():
    t = topics.category_tree()
    C.apply_seed(t)
    yield t


def _leaf(tree):
    """A seeded leaf with a parent: a node and its path."""
    nodes = tree.nodes()
    parents = {n.parent_id for n in nodes.values()}
    return next(i for i, n in sorted(nodes.items()) if n.parent_id and i not in parents)


def _grade(key, node, confidence="high"):
    training.store().put_label(key, f"question {key}", node, confidence, "", "m",
                               path=training._path_text(node))


def test_the_first_look_only_remembers_the_tree(tree):
    out = training.follow_tree()
    assert out.get("first_look") and training.store().tree_seen()
    assert tasks.store().due(10) == []


def test_a_pruned_node_retires_its_grades_to_the_nearest_ancestor_and_back(tree):
    leaf = _leaf(tree)
    parent = tree.get(leaf).parent_id
    _grade("a", leaf)
    now = training._tree_now()
    training.follow_tree(now)
    gone = {k: v for k, v in now.items() if k != leaf}
    out = training.follow_tree(gone)
    assert out["pruned"] == [leaf] and out["retired"] == 1
    label = training.store().labels()[0]
    assert label["node"] == leaf and label["node_now"] == parent and label["retired_at"]
    # Never compared, never "none" in memory.
    assert training.compared() == []
    back = training.follow_tree(now)
    assert back["restored"] == 1 and not training.store().labels()[0]["retired_at"]


def test_a_pruned_grade_with_nothing_left_is_forgotten_by_memory_not_called_none(tree):
    training.store().put_label("z", "zoo question", "gone node", "high", "", "m",
                               path="gone node")
    training.store().mark("z", retired_at=1.0, node_now="")
    assert all(r["question"] != "zoo question" for r in memory.rows())


def test_a_minted_node_asks_for_fresh_grades_under_its_parent_first(tree, monkeypatch):
    leaf = _leaf(tree)
    parent = tree.get(leaf).parent_id
    _grade("p", parent)
    now = training._tree_now()
    training.follow_tree(now)
    out = training.follow_tree(dict(now, **{"brand new node": parent}))
    assert out["minted"] == ["brand new node"] and out["regrade"] == 1
    assert training.store().to_regrade() == {"p"}
    assert out["undefined"] == ["brand new node"]
    # The answer key grades it again, ahead of new episodes.
    category_audit.note("question p", "", "tap", key="p")
    category_audit.note("question q", "", "tap", key="q")
    monkeypatch.setattr(training, "allowed", lambda: (True, ""))
    asked = []

    async def ask(request):
        asked.append(dict(request.material)["request"])
        return models.AgentResult(status="ok", agent="categorizer", task="judge",
                                  value=training.JudgeLabel(node=parent, confidence="high",
                                                            reason="r"), model="m")

    monkeypatch.setattr(models, "ask", ask)
    asyncio.run(training.judge_batch(lambda _k: ["s"], limit=1))
    assert asked == ["question p"] and training.store().to_regrade() == set()


def test_every_change_is_a_refile_task_for_consuela(tree):
    leaf = _leaf(tree)
    now = training._tree_now()
    training.follow_tree(now)
    other = next(n for n in now if n != leaf and now[n] != now[leaf] and n != now[leaf])
    changed = dict(now, **{"brand new node": now[leaf]})
    changed[leaf] = other                                    # moved
    out = training.follow_tree(changed)
    assert out["moved"] == [leaf] and out["consuela"] == 2
    due = set(tasks.store().due(10))
    assert {f"housekeeper:refile:{leaf}", "housekeeper:refile:brand new node"} <= due


def test_a_grade_is_never_deleted_by_following(tree):
    leaf = _leaf(tree)
    _grade("a", leaf)
    now = training._tree_now()
    training.follow_tree(now)
    training.follow_tree({k: v for k, v in now.items() if k != leaf})
    assert len(training.store().labels()) == 1


def test_netenyahu_follows_the_tree_before_the_answer_key(monkeypatch):
    hours = {kind: hour for _a, kind, hour, _s in netenyahu.SCHEDULE}
    assert hours["follow"] < hours["judge"] < hours["train"]
    monkeypatch.setattr(netenyahu, "HANDLERS", {})
    training.register(lambda _k: [])
    assert ("categorizer", "follow") in netenyahu.HANDLERS


def test_following_never_raises(monkeypatch):
    monkeypatch.setattr(training, "_tree_now", lambda: 1 / 0)
    assert "error" in training.follow_tree()


def test_a_wipe_forgets_the_last_look(tree):
    training.follow_tree()
    training.store().forget()
    assert training.store().tree_seen() == {}
