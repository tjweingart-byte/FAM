"""§307: the writer is shown the examples nearest its episode.

Pinned here:

* every example is filed on a node of the tree and a story shape the brief
  can name, and a file without the header lines still loads;
* the episode is placed in the tree before it is written, in code, and that
  placement - with the story shape and the length - chooses the examples;
* an episode nothing singles out - no node, no shape any example has - is
  shown every example, exactly as before;
* the house rules are their own cached block, identical on every call, and
  the chosen examples a block after them;
* the placement and the examples are kept beside the episode's quality, and
  the report says whether the placement matched the writer's own category
  and how each example's episodes scored.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories as C  # noqa: E402
import config  # noqa: E402
import episode_intelligence as ei  # noqa: E402
import episode_quality as Q  # noqa: E402
import script_generator as sg  # noqa: E402
import topics as T  # noqa: E402
from script_generator import plan_episode  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def tree():
    t = T.category_tree()
    C.apply_seed(t)
    return t


@pytest.fixture(autouse=True)
def mid_season(monkeypatch):
    """A day the NFL's and Formula One's calendars put in their regular
    seasons (§308), so these tests choose the same examples in December,
    when Formula One is out of season, as in October."""
    import datetime

    import seasons

    monkeypatch.setattr(seasons, "today", lambda: datetime.date(2026, 11, 20))


def _plan(query, minutes=2, subject="", shape=""):
    plan = plan_episode(query, minutes, search=False)
    plan.brief = ei.Brief(query=query, subject=subject or query, structure=shape)
    return plan


def _with(monkeypatch, **overrides):
    patched = dataclasses.replace(config.settings, **overrides)
    monkeypatch.setattr(config, "settings", patched)
    monkeypatch.setattr(sg, "settings", patched)


# --- the examples, on the tree ---------------------------------------------------


def test_every_example_is_filed_on_a_node_and_a_shape(tree):
    assert sg.STYLE_EXAMPLES
    for query, _script in sg.STYLE_EXAMPLES:
        info = sg.EXAMPLE_INFO[query]
        assert info["node"] and (tree.get(info["node"]) is not None
                                 or info["node"] in T.FACETS), info
        assert info["shape"] in ei.STRUCTURES, info
        # The header is metadata, never part of the question.
        assert not query.startswith("#") and "node:" not in query


def test_the_header_is_optional_and_never_spoken(tmp_path, monkeypatch):
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "1-old.txt").write_text("an old query\n\nOld script.\n")
    (tmp_path / "examples" / "2-new.txt").write_text(
        "# node: physics\n# shape: explainer\na new query\n\nNew script.\n")
    monkeypatch.setattr(sg, "EXAMPLE_MINUTES", {})
    monkeypatch.setattr(sg, "EXAMPLE_INFO", {})
    monkeypatch.setattr(sg, "__file__", str(tmp_path / "script_generator.py"))
    assert sg.load_style_examples() == [("an old query", "Old script."),
                                        ("a new query", "New script.")]
    assert sg.EXAMPLE_INFO["a new query"] == {"file": "2-new.txt",
                                              "node": "physics",
                                              "shape": "explainer",
                                              "phase": ""}
    assert sg.EXAMPLE_INFO["an old query"]["node"] == ""


# --- choosing them ---------------------------------------------------------------


def test_a_race_recap_is_shown_the_race_recap(tree):
    choice = sg.choose_examples(_plan(
        "how did lando norris do at the monaco grand prix", 2,
        "Lando Norris at the Monaco Grand Prix in formula one", "sports_recap"))
    assert choice.node == "formula one"
    assert choice.path[-1] == "sports"
    assert "2-singapore-sprint.txt" in choice.files
    assert len(choice.files) <= config.settings.examples_per_episode


def test_a_rates_question_is_shown_the_rates_example(tree):
    choice = sg.choose_examples(_plan(
        "what is the federal reserve going to do", 2,
        "the federal reserve's next decision on interest rates", "explainer"))
    assert choice.path[-1] == "money"
    assert "2-fed-raises-rates.txt" in choice.files


def test_one_example_at_the_length_asked_for_when_there_is_one(tree):
    choice = sg.choose_examples(_plan(
        "how did lando norris do at the monaco grand prix", 5,
        "formula one at Monaco", "sports_recap"))
    assert any(sg.EXAMPLE_INFO[q]["file"].startswith("5-") for q in choice.queries)


def test_nothing_to_go_on_shows_every_example_as_before(tree):
    choice = sg.choose_examples(_plan("knitting for beginners", 2, "knitting",
                                      "general"))
    assert choice.queries is None
    assert len(choice.files) == len(sg.STYLE_EXAMPLES)
    # A plan with no brief at all is the same.
    assert sg.choose_examples(plan_episode("knitting", 2, search=False)).queries is None


def test_examples_by_node_off_shows_every_example(tree, monkeypatch):
    _with(monkeypatch, examples_by_node=False)
    choice = sg.choose_examples(_plan("the monaco grand prix", 2,
                                      "formula one", "sports_recap"))
    assert choice.queries is None


def test_the_same_episode_is_shown_the_same_block(tree):
    plan = _plan("what is the federal reserve going to do", 2,
                 "the federal reserve and interest rates", "explainer")
    first = sg.writer_system(sg.choose_examples(plan))
    assert first == sg.writer_system(sg.choose_examples(plan))


def test_placing_never_raises(monkeypatch):
    import stories

    monkeypatch.setattr(stories, "resolve_category",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert sg.place_episode(_plan("anything")) == ("", ())


# --- what the writer is sent -------------------------------------------------------


def test_the_rules_come_first_and_alone_then_the_chosen_examples(tree, monkeypatch):
    _with(monkeypatch, prompt_cache=True, prompt_cache_ttl="5m")
    plan = _plan("how did lando norris do at the monaco grand prix", 2,
                 "formula one at Monaco", "sports_recap")
    notes = sg.ScriptNotes()
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    system = generator._request_kwargs(plan, notes)["system"]
    assert system[0]["text"] == sg.SYSTEM_PROMPT
    assert all(b["cache_control"] == {"type": "ephemeral"} for b in system)
    assert "monaco" not in system[1]["text"].lower()   # examples, not the question
    sprint = next(q for q, _ in sg.STYLE_EXAMPLES
                  if sg.EXAMPLE_INFO[q]["file"] == "2-singapore-sprint.txt")
    assert sprint in system[1]["text"]
    assert system[1]["text"].count("<example ") == len(notes.examples)
    assert notes.planned_node == "formula one"
    assert "2-singapore-sprint.txt" in notes.examples
    # Off, the same words as one plain string.
    _with(monkeypatch, prompt_cache=False)
    assert generator._request_kwargs(plan, sg.ScriptNotes())["system"] == (
        system[0]["text"] + system[1]["text"])


# --- kept beside the episode's quality, and reported --------------------------------


def K(name):
    return hashlib.sha256(name.encode()).hexdigest()


def _note(name, planned, written, files, script=None):
    notes = sg.ScriptNotes()
    notes.planned_node = planned
    notes.category = written
    notes.examples = tuple(files)
    Q.note(K(name), 1000, "a question", 2, "search",
           script or ["The Eagles beat Dallas on Sunday in Philadelphia.",
                      "It turned because Hurts ran on fourth down."], notes)


TREE = {"nfl": ["american football", "sports"],
        "american football": ["sports"],
        "dallas cowboys": ["nfl", "american football", "sports"],
        "formula one": ["motorsport", "sports"],
        "motorsport": ["sports"], "sports": [],
        "interest rates": ["central banks", "money"],
        "central banks": ["money"], "money": []}


def _report():
    return Q.report(resolve=lambda w: w if w in TREE else "",
                    ancestors=lambda n: TREE.get(n, []),
                    label_of=lambda n: n.title())


def test_the_placement_and_examples_are_kept_beside_the_quality():
    _note("a", "nfl", "dallas cowboys", ["2-singapore-sprint.txt"])
    row = Q.store().get(K("a"), 1000)
    assert row["planned_node"] == "nfl"
    assert json.loads(row["examples"]) == ["2-singapore-sprint.txt"]


def test_the_report_says_whether_the_placement_matched_the_writer():
    _note("same", "nfl", "dallas cowboys", ["x.txt"])        # beneath it
    _note("up", "dallas cowboys", "nfl", ["x.txt"])          # above it
    _note("heading", "formula one", "nfl", ["y.txt"])        # both sports
    _note("other", "interest rates", "nfl", ["y.txt"])       # money vs sports
    _note("none", "", "nfl", [])
    _note("lost", "nfl", "a category no tree has", ["x.txt"])
    r = _report()
    assert r["placement"] == {"same": 2, "heading": 1, "different": 1,
                              "unplanned": 1, "unknown": 1}
    assert r["misplaced"] == [{"planned": "interest rates", "written": "nfl",
                               "episodes": 1}]
    by = {e["file"]: e for e in r["examples"]}
    assert by["x.txt"]["episodes"] == 3 and by["y.txt"]["episodes"] == 2
    assert "nfl" in by["y.txt"]["nodes"]
    # Rolled up like everything else: the Cowboys episode counts under nfl.
    nfl = {n["node"]: n for n in r["nodes"]}["nfl"]
    assert nfl["placement"]["same"] == 2 and nfl["placement"]["different"] == 1


def test_an_empty_report_has_the_new_sections():
    r = Q.report()
    assert r["placement"] == {} and r["misplaced"] == [] and r["examples"] == []


def test_a_store_from_before_is_widened_not_recreated(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE episodes (key TEXT NOT NULL, sourced INTEGER NOT NULL,
                  at REAL NOT NULL, query TEXT NOT NULL DEFAULT '',
                  minutes INTEGER NOT NULL DEFAULT 0, origin TEXT NOT NULL DEFAULT '',
                  structure TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
                  title TEXT NOT NULL DEFAULT '', writer_model TEXT NOT NULL DEFAULT '',
                  prompt TEXT NOT NULL DEFAULT '', words INTEGER NOT NULL DEFAULT 0,
                  word_budget INTEGER NOT NULL DEFAULT 0, sentences TEXT NOT NULL DEFAULT '[]',
                  evidence TEXT NOT NULL DEFAULT '', measures TEXT NOT NULL DEFAULT '{}',
                  grade TEXT NOT NULL DEFAULT '', grade_model TEXT NOT NULL DEFAULT '',
                  grade_dollars REAL NOT NULL DEFAULT 0, graded_at REAL NOT NULL DEFAULT 0,
                  PRIMARY KEY (key, sourced))""")
    db.execute("INSERT INTO episodes (key, sourced, at) VALUES ('k', 1, 1)")
    db.commit()
    db.close()
    store = Q.QualityStore(str(path))
    row = store.get("k", 1)
    assert row["planned_node"] == "" and row["examples"] == "[]"
    Q.QualityStore(str(path))   # opening it again is harmless


def test_with_room_for_one_the_most_relevant_is_kept(tree, monkeypatch):
    """The length rule never swaps out the only example shown."""
    _with(monkeypatch, examples_per_episode=1)
    choice = sg.choose_examples(_plan(
        "how did lando norris do at the monaco grand prix", 5,
        "the Monaco Grand Prix in formula one", "sports_recap"))
    assert choice.files == ("2-singapore-sprint.txt",)
