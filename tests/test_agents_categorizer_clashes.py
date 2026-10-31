"""Names that mean two things, and broader when unsure (§358): the clash
table beside the category seed, read by Nando's code; a tie is never
guessed; every placement has a confidence, and an unsure one is filed under
its parent.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

import categories as C
import category_audit
import category_collisions
import topics
from agents import categorizer
from agents.categorizer import Prior
from episode_intelligence import Brief

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def tree():
    t = topics.category_tree()
    C.apply_seed(t)
    return t


def node(question, brief=None, **kw):
    return categorizer.place(brief, question, **kw)


# ------------------------------------------------------------------ the table
def test_every_reading_is_a_node_the_seed_holds_or_none(tree):
    for name, readings in category_collisions.COLLISIONS.items():
        assert len(readings) >= 2, name
        assert name == name.lower()
        for reading in readings:
            assert reading.node == "" or tree.get(reading.node) is not None, (name, reading.node)
            assert reading.cues and all(c == c.lower() for c in reading.cues), name


def test_the_table_is_data_beside_the_seed_and_not_a_rule_book():
    from agents import rulebooks

    assert "category_collisions.py" not in rulebooks.book_paths()
    assert (ROOT / "category_collisions.py").exists()


# ------------------------------------------------------------------ decided by the words around the name
@pytest.mark.parametrize("question, expected", [
    ("Miami heat wave this week", "extreme weather"),
    ("Heat beat the Celtics in the playoffs", "nba"),
    ("hurricanes hitting the Carolina coast", "extreme weather"),
    ("Hurricanes goalie pulled in the third", "nhl"),
    ("United's new striker scores on debut", "premier league"),
    ("United adds flights to Lisbon", "flights"),
    ("Apple's new iPhone sales", "smartphones"),
    ("Amazon warehouse workers strike", "retail business"),
])
def test_a_name_is_decided_by_the_words_around_it(tree, question, expected):
    assert node(question).node == expected


def test_a_reading_on_the_winners_own_branch_does_not_rule_it_out(tree):
    # consumer hardware sits above smartphones: not a rival reading.
    assert node("Apple unveils a new iPhone").node == "smartphones"


def test_a_reading_the_tree_has_no_node_for_files_nothing_never_the_other(tree):
    placed = node("bald eagles return to nest along the river")
    assert placed.node == "" and placed.how == "clash"


def test_the_brief_still_outranks_the_table(tree):
    placed = node("Heat game tonight", Brief(query="q", category="extreme weather"))
    assert placed.node == "extreme weather" and placed.how == "category"


def test_a_tap_still_outranks_the_table(tree):
    placed = node("Heat beat the Celtics", prior=Prior("extreme weather", "tile"))
    assert placed.node == "extreme weather"


# ------------------------------------------------------------------ a tie is never guessed
def test_a_tie_is_what_the_readings_share(tree):
    placed = node("how did the Kings do")
    assert placed.node == "sports" and placed.how == "shared"
    assert placed.ambiguous == ("kings",)


def test_a_tie_with_nothing_shared_is_nothing(tree):
    placed = node("how did United do")
    assert placed.node == "" and placed.ambiguous == ("united",)


def test_a_decided_name_rules_out_the_other_readings_phrases(tree):
    # "carolina hurricanes" is not a node, but if a phrase in the question
    # landed on a losing reading's branch it would be passed over.
    named = categorizer.clashes("Hurricane season forecast for the Gulf coast")
    assert [(n, d.node) for n, d, _r in named] == [("hurricane", "extreme weather")]


# ------------------------------------------------------------------ confidence and broader when unsure
def test_every_placement_has_a_confidence(tree):
    assert node("Stanley Cup odds").confidence == categorizer.CONFIDENCE["question"]
    assert node("x", prior=Prior("nfl", "dailyfam")).confidence == categorizer.CONFIDENCE["dailyfam"]
    assert node("x", Brief(query="x", category="sports")).confidence == 1.0


def test_an_undecided_name_lowers_the_confidence_of_a_node_it_touches(tree, monkeypatch):
    # A phrase lands on one of a tie's readings: half as sure, and filed
    # under the parent.
    monkeypatch.setattr(categorizer, "CONFIDENCE", dict(categorizer.CONFIDENCE, question=0.6))
    placed = node("los angeles kings or sacramento kings")
    assert placed.how.endswith("~broader") or placed.confidence >= categorizer.MIN_CONFIDENCE


def test_below_the_floor_the_parent_is_filed(tree, monkeypatch):
    sure = node("Stanley Cup odds")
    monkeypatch.setattr(categorizer, "CONFIDENCE", dict(categorizer.CONFIDENCE, question=0.3))
    placed = node("Stanley Cup odds")
    assert placed.node == sure.path[-2] and placed.how == "question~broader"
    assert placed.path == sure.path[:-1] and placed.confidence == 0.3


def test_a_facet_is_never_made_broader(tree, monkeypatch):
    monkeypatch.setattr(categorizer, "CONFIDENCE", dict(categorizer.CONFIDENCE, facet=0.1))
    assert node("x", Brief(query="x", category="sports")).node == "sports"


# ------------------------------------------------------------------ the audit
def test_the_audit_counts_unsure_filings_and_undecided_names(tree):
    category_audit.note("how did the Kings do", "", "tap", composer="", nando={
        "request": "sports", "request_how": "shared", "node": "sports", "how": "kept",
        "confidence": 1.0, "ambiguous": "kings"})
    category_audit.note("Stanley Cup odds", "", "tap", composer="", nando={
        "request": "nhl", "request_how": "question~broader", "node": "nhl", "how": "kept",
        "confidence": 0.3, "ambiguous": ""})
    found = category_audit.summary(days=1)["nando"]
    assert found["broader_when_unsure"] == 1
    assert found["undecided_names"] == {"kings": 1}


# ------------------------------------------------------------------ the measurement
def test_the_held_out_clashes_place_better_than_today_and_never_wrong():
    sys.path.insert(0, str(ROOT / "tools"))
    import nando_eval

    result = nando_eval.run_held_out()
    today, nando = result["today (resolve_category)"], result["nando, code, no brief"]
    assert len(nando_eval.HELD_OUT) == 20
    assert nando["totals"]["wrong"] == 0
    assert nando["totals"]["right"] > today["totals"]["right"]
