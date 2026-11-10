"""The house examples matched to the story, and new ones proposed (§520, item 4).

Every example has a facet (`examples/facets.json`); the writer's request -
never the cached system block - names the one or two examples of the
episode's facet; `EXAMPLE_MATCHING=0` leaves the request byte for byte as it
was. New examples are proposed from what listeners heard through, for the
owner to approve - never added by code.
"""
from __future__ import annotations

import dataclasses
import json
import os

import pytest

import episode_quality
import script_generator as sg
import topics
from episode_intelligence import Brief
from script_generator import build_prompt, plan_episode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_every_example_has_a_facet():
    assert set(sg.EXAMPLE_STEMS.values()) == set(sg.EXAMPLE_FACETS)
    assert set(sg.EXAMPLE_FACETS.values()) <= topics.FACETS


def _plan(facet="sports", minutes=1.5):
    brief = Brief(subject="the Eagles", category_path=[facet] if facet else [])
    return dataclasses.replace(plan_episode("how are the eagles doing", minutes), brief=brief)


def test_the_facet_comes_from_the_brief_first():
    assert sg.episode_facet(_plan("sports")) == "sports"
    assert sg.episode_facet(_plan("money")) == "money"
    assert sg.episode_facet(plan_episode("why is the sky blue", 1)) in ("", "science")


def test_the_closest_by_length_first():
    assert sg.matched_examples("money", 2) == ["2-fed-raises-rates", "5-financial-crisis"]
    assert sg.matched_examples("money", 5)[0] == "5-financial-crisis"
    assert sg.matched_examples("", 2) == []
    assert len(sg.matched_examples("sports", 2)) <= sg.MATCHED_EXAMPLES


def test_the_request_names_them_and_the_system_block_does_not_change(monkeypatch):
    # On only by choice since §526.
    monkeypatch.setattr(sg, "settings", dataclasses.replace(sg.settings, example_matching=True))
    system = sg.system_prompt()
    prompt = build_prompt(_plan("sports"))
    assert ('the closest to this kind of story are "why is a marathon 26.2 miles" and '
            '"how did max verstappen do in the singapore sprint') in prompt
    assert "never with their facts" in prompt
    assert sg.system_prompt() == system


def test_off_the_request_is_as_it_was(monkeypatch):
    monkeypatch.setattr(sg, "settings", dataclasses.replace(sg.settings, example_matching=False))
    off = build_prompt(_plan("sports"))
    assert "closest to this kind of story" not in off
    assert off == build_prompt(_plan(""))


def test_off_by_default_names_none():
    assert sg.example_hint(_plan("sports")) == ""


def test_a_facet_with_no_example_names_none():
    assert sg.example_hint(_plan("")) == ""


def test_the_settings_are_copied_and_on():
    text = open(os.path.join(ROOT, ".env.example")).read()
    # Matching off since §526 (no gain, more repetition); worked plans on.
    assert "\nEXAMPLE_MATCHING=0\n" in text and "\nEDITOR_WORKED_PLANS=1\n" in text
    assert not sg.settings.example_matching and sg.settings.editor_worked_plans


# ------------------------------------------------------------------ candidates
def _row(key, query, category="nfl"):
    return {"key": key, "sourced": 100, "at": 1_000.0, "query": query, "minutes": 2,
            "origin": "trending", "structure": "", "category": category, "title": "",
            "writer_model": "", "prompt": "", "words": 2, "word_budget": 0,
            "sentences": json.dumps([f"{query} one.", "Two."]), "evidence": "",
            "measures": json.dumps({"scores": {"flow": 0.7}}), "editor_arm": ""}


@pytest.fixture
def quality(tmp_path):
    return episode_quality.QualityStore(str(tmp_path / "q.db"))


def test_candidates_are_the_episodes_heard_through(quality):
    quality.record(_row("a", "how did the eagles win"), now=1_000.0)
    quality.record(_row("b", "what is the eagles' record"), now=1_000.0)
    quality.record(_row("c", "who plays the eagles next"), now=1_000.0)   # too few listens
    for i in range(4):
        quality.note_listen("a", 100, f"a{i}", 115.0, 120.0, 2, now=1_000.0)
        quality.note_listen("b", 100, f"b{i}", 10.0 if i else 115.0, 120.0, 2, now=1_000.0)
    quality.note_listen("c", 100, "c0", 115.0, 120.0, 2, now=1_000.0)
    out = episode_quality.example_candidates(days=1, min_listens=4, now=1_060.0,
                                             quality=quality, facet_of=lambda c: "sports")
    sports = out["facets"]["sports"]
    assert [c["query"] for c in sports["candidates"]] == ["how did the eagles win",
                                                          "what is the eagles' record"]
    assert sports["candidates"][0]["finished"] == 1.0
    assert sports["examples_now"] == 2
    assert sports["candidates"][0]["sentences"] == ["how did the eagles win one.", "Two."]
    assert "a0" not in json.dumps(out)


def test_the_facets_with_fewest_examples_come_first(quality):
    for key, facet in (("a", "money"), ("b", "business")):
        quality.record(_row(key, f"question {key}", category=facet), now=1_000.0)
        quality.note_listen(key, 100, "l", 115.0, 120.0, 2, now=1_000.0)
    out = episode_quality.example_candidates(days=1, min_listens=1, now=1_060.0,
                                             quality=quality, facet_of=lambda c: c)
    assert list(out["facets"]) == ["business", "money"]


def test_a_candidate_is_written_in_the_examples_format(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "example_candidates", os.path.join(ROOT, "tools", "example_candidates.py"))
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    c = {"query": "How did the Eagles win?", "minutes": 1.5,
         "sentences": ["They won.", "By three."]}
    assert tool.file_name(c) == "2-how-did-the-eagles-win.txt"
    assert tool.file_text(c) == "How did the Eagles win?\n\nThey won. By three.\n"
