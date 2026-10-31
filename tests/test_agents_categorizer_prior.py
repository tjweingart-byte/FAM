"""Nando starts from what the tap already knows (§357): a tile's story, a
bank topic, a DailyFAM subject, an edition's story, or the episode a Go
Deeper follows - and chooses the facet first. The question's words may
narrow that node and never move it.
"""
from __future__ import annotations

import asyncio
import dataclasses
import pathlib
import sys
import time

import pytest

import categories as C
import live_captions
import script_generator as sg
import stories
import topics
from agents import categorizer, netenyahu
from agents.categorizer import Prior
from episode_intelligence import Brief

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATE = "Saturday, October 31, 2026"


@pytest.fixture
def tree():
    t = topics.category_tree()
    C.apply_seed(t)
    return t


@pytest.fixture
def pool(monkeypatch):
    held = []

    class Pool:
        def held(self, now=None):
            return list(held)
    monkeypatch.setattr(stories, "pool", lambda: Pool())

    def add(query, category="", tags=(), domain=stories.ATTENTION):
        held.append(stories.Story(subject=query, title=query, angle="", query=query,
                                  category=category, tags=tags, domain=domain,
                                  first_seen=time.time()))
    return add


# ------------------------------------------------------------------ where the prior comes from
def test_a_tiles_story_category(tree, pool):
    pool("Heat grips Miami as records fall", category="extreme weather")
    assert categorizer.prior_for("Heat grips Miami as records fall") == \
        Prior("extreme weather", "tile")


def test_a_tile_with_no_category_gives_its_deepest_tag(tree, pool):
    pool("the Stanley Cup final", tags=("sports", "ice hockey", "stanley cup"))
    assert categorizer.prior_for("the Stanley Cup final") == Prior("stanley cup", "tile")


def test_a_trending_story_counts_as_a_tile(tree, pool, monkeypatch):
    import trending_bank

    story = stories.Story(subject="s", title="t", angle="", query="why the oil price jumped",
                          category="oil price", first_seen=time.time())
    monkeypatch.setattr(trending_bank, "stories_in_memory", lambda *a: [story])
    # Never the store on a listener's path (review, §361).
    monkeypatch.setattr(trending_bank, "stories_now",
                        lambda *a: (_ for _ in ()).throw(AssertionError("store read")))
    assert categorizer.prior_for("why the oil price jumped") == Prior("oil price", "tile")


def test_a_bank_topic_by_its_question(tree, pool):
    topic = topics.TOPIC_BANK[0]
    prior = categorizer.prior_for(topic.query)
    assert prior.source == "bank" and prior.node


def test_a_dailyfam_subject_from_the_catalogue(tree, pool):
    prior = categorizer.prior_for(f"The latest on NFL as of {DATE}: what happened")
    assert prior == Prior("nfl", "dailyfam")


def test_a_narrowed_dailyfam_subject_stays_in_its_subject(tree, pool):
    # "Eagles" alone is not a phrase the tree holds, but it is the
    # Eagles' nickname inside NFL (§359), so it narrows to the team.
    prior = categorizer.prior_for(f"The latest on Eagles (NFL) as of {DATE}. Cover only Eagles")
    assert prior == Prior("philadelphia eagles", "dailyfam")
    # A specific the subject's branch has no word for stays the subject.
    assert categorizer.prior_for(f"The latest on Zqxv (NFL) as of {DATE}") == Prior("nfl", "dailyfam")
    narrowed = categorizer.prior_for(
        f"The latest on Philadelphia Eagles (NFL) as of {DATE}. Cover only them")
    assert narrowed == Prior("philadelphia eagles", "dailyfam")


def test_a_narrowed_specific_from_another_branch_is_not_taken(tree, pool):
    prior = categorizer.prior_for(f"The latest on Oil Price (NFL) as of {DATE}")
    assert prior == Prior("nfl", "dailyfam")


def test_a_go_deeper_follows_its_parent(tree, pool):
    assert categorizer.prior_for("what about their defense",
                                 "Philadelphia Eagles' playoff picture") == \
        Prior("philadelphia eagles", "parent")


def test_an_edition_writers_own_prior_comes_first(tree, pool):
    pool("q", category="extreme weather")
    notes = sg.ScriptNotes()
    notes.category_prior = Prior("oil price", "edition")
    assert categorizer.prior_for("q", notes=notes) == Prior("oil price", "edition")


def test_a_typed_search_has_no_prior(tree, pool):
    assert categorizer.prior_for("why rents keep rising") == categorizer.NO_PRIOR


def test_reading_the_prior_never_raises(tree, monkeypatch):
    monkeypatch.setattr(stories, "pool", lambda: (_ for _ in ()).throw(RuntimeError("gone")))
    assert categorizer.prior_for("q") == categorizer.NO_PRIOR


# ------------------------------------------------------------------ the words may narrow, never move
def test_the_words_narrow_a_prior_on_its_branch(tree):
    placed = categorizer.place(Brief(query="q", category="philadelphia eagles"), "q",
                               prior=Prior("nfl", "dailyfam"))
    assert placed.node == "philadelphia eagles" and placed.how == "category"


def test_the_words_never_move_a_prior_to_another_branch(tree):
    placed = categorizer.place(Brief(query="q", category="miami heat"),
                               "Heat grips Miami", prior=Prior("extreme weather", "tile"))
    assert placed.node == "extreme weather" and placed.how == "tile"


def test_a_prior_with_no_words_is_the_node(tree):
    placed = categorizer.place(None, "what about their defense",
                               prior=Prior("philadelphia eagles", "parent"))
    assert placed.node == "philadelphia eagles" and placed.how == "parent"
    assert "philadelphia eagles" in placed.candidates


def test_the_prior_chooses_the_facet_first(tree):
    # A question phrase on another facet is not even a candidate answer.
    placed = categorizer.place(None, "Stanley Cup odds", prior=Prior("stock market", "tile"))
    assert placed.node == "stock market"


# ------------------------------------------------------------------ wired in
def test_prepare_places_from_the_tap(tree, pool, monkeypatch):
    pool("Heat grips Miami as records fall", category="extreme weather")

    async def research_(self, plan, notes=None):
        return dataclasses.replace(plan, evidence="SOURCE 1\nTitle: x")

    async def understand(self, plan, notes=None):
        return plan
    monkeypatch.setattr(sg.ScriptGenerator, "research", research_)
    monkeypatch.setattr(sg.ScriptGenerator, "understand", understand)
    monkeypatch.setattr(live_captions, "publish_category", lambda k, w: None)
    query = "Heat grips Miami as records fall"
    plan = dataclasses.replace(sg.plan_episode(query, 2),
                               brief=Brief(query=query, category="miami heat"))
    notes = sg.ScriptNotes()
    notes.agents = netenyahu.EpisodeRun(("categorizer",), {})
    asyncio.run(sg.ScriptGenerator.__new__(sg.ScriptGenerator).prepare(plan, notes))
    assert notes.category_placement.node == "extreme weather"
    assert notes.category_placement.how == "tile"


def test_the_trending_edition_hands_nando_its_storys_category():
    source = (ROOT / "trending_bank.py").read_text(encoding="utf-8")
    assert 'categorizer.Prior(story.category, "edition")' in source


# ------------------------------------------------------------------ the measurement
def test_taps_with_context_place_better_than_today_and_never_wrong():
    sys.path.insert(0, str(ROOT / "tools"))
    import nando_eval

    result = nando_eval.run_context()
    today, nando = result["today (resolve_category)"], result["nando, from the tap"]
    assert nando["totals"]["wrong"] == 0
    assert nando["totals"]["right"] > today["totals"]["right"]
    assert sum(nando["totals"].values()) == len(nando_eval.CONTEXT_TAPS)
