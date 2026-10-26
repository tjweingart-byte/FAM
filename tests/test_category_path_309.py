"""EI's contextualising step: the category path of a search (§309).

`Brief.category_path` is where the request sits in FAM's category tree -
the facet, then each subcategory - placed in code from EI's own category,
never by the model, and never at the cost of an episode.
"""
from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import episode_intelligence as ei  # noqa: E402


def test_place_category_walks_the_tree_broadest_first(monkeypatch):
    import stories
    import topics

    tree = types.SimpleNamespace(
        ancestors=lambda node: ["nfl", "american football", "sports"])
    monkeypatch.setattr(stories, "resolve_category", lambda text: (
        "philadelphia eagles" if "Eagles" in text else ""))
    monkeypatch.setattr(topics, "category_tree", lambda: tree)
    brief = ei.Brief(query="eagles", subject="the Philadelphia Eagles",
                     category="NFL team")
    placed = ei.place_category(brief)
    assert placed.category_path == ["sports", "american football", "nfl",
                                    "philadelphia eagles"]
    assert placed.subcategory == "philadelphia eagles"
    assert brief.category_path == []  # a copy: a warmed brief is shared


def test_place_category_never_raises(monkeypatch):
    import stories

    def boom(text):
        raise RuntimeError("no tree")

    monkeypatch.setattr(stories, "resolve_category", boom)
    brief = ei.Brief(query="x", category="y")
    assert ei.place_category(brief) is brief
    assert ei.place_category(ei.fallback_brief("x", "t")).category_path == []

