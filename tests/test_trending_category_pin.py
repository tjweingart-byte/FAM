"""Trending tiles say what they are, and their episodes are about their story.

Two failures a listener reported (§188), each pinned here:

* **The picture and the label were the wrong subject.** A boxing press
  conference wore a laboratory under SCIENCE: its query never said "boxing",
  so the tree matched nothing, and the fallback was the alphabetically first
  keyword tag (`science`, from a headline that said "study"). The composer
  now names the category, it is resolved against the tree in code, and the
  picture and the label are read from it.
* **The episode was about something else.** The composer abstracted the
  story into a theme ("what a chaotic press conference signals about the
  fight") and research, given that and nothing else, found whichever fight
  it liked. The names the headlines share now travel with the signal, the
  composer is told to use them, and `pin_query` adds them when it did not.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories as C  # noqa: E402
import config  # noqa: E402
import news_clusters  # noqa: E402
import stories  # noqa: E402
import thumbnails as th  # noqa: E402
import topics as T  # noqa: E402
import trending_bank  # noqa: E402


@pytest.fixture
def tree():
    t = T.category_tree()
    C.apply_seed(t)
    return t


def _article(title, n):
    return SimpleNamespace(title=title, url=f"https://outlet{n}.example/{n}")


FIGHT = [
    _article("Tyson Fury and Anthony Joshua trade insults at chaotic press "
             "conference", 1),
    _article("Fury v Joshua: heated press conference ends in chaos", 2),
    _article("Joshua Says He Will Knock Out Fury In Riyadh", 3),
]
SPONSOR = [
    _article("Etihad threatens legal action over Manchester City sponsorship "
             "rules", 4),
    _article("Premier League sponsorship rules: Etihad weighs legal action", 5),
]


def _group(articles):
    groups = news_clusters.cluster(articles, min_outlets=1)
    assert len(groups) == 1
    return groups[0]


# --------------------------------------------------------------------------
# anchors: the names that pin a story
# --------------------------------------------------------------------------
def test_anchors_are_whole_names_the_headlines_share():
    assert set(trending_bank.anchors_of(_group(FIGHT))) == {
        "Tyson Fury", "Anthony Joshua"}


def test_a_name_that_opens_a_headline_is_kept_when_it_is_a_run():
    anchors = trending_bank.anchors_of(_group(SPONSOR))
    assert {"Etihad", "Premier League", "Manchester City"} <= set(anchors)
    assert "League" not in anchors


def test_title_case_headlines_alone_still_give_shared_names():
    group = _group([_article("Joshua Says He Will Knock Out Fury", 1),
                    _article("Fury Mocks Joshua Before Riyadh Bout", 2)])
    anchors = trending_bank.anchors_of(group)
    assert {"Fury", "Joshua"} <= {w for a in anchors for w in a.split()}
    assert len(anchors) <= trending_bank.MAX_ANCHORS


def test_anchors_reach_the_composer_as_names_not_headlines():
    signal = stories.Signal(subject="Fury v Joshua", observation="12 articles",
                            anchors=("Tyson Fury", "Anthony Joshua"))
    prompt = stories.build_composer_prompt([signal], "Tuesday")
    assert "named in the headlines: Tyson Fury, Anthony Joshua" in prompt


# --------------------------------------------------------------------------
# pin_query: research searches for this event
# --------------------------------------------------------------------------
def test_a_query_that_names_no_one_is_pinned_to_the_story():
    got = stories.pin_query(
        "What a chaotic press conference signals about the fight",
        ("Tyson Fury", "Anthony Joshua"))
    assert got.endswith("(Tyson Fury, Anthony Joshua)")


def test_a_query_that_already_names_them_is_left_alone():
    query = "What Fury and Joshua's press conference says about their fight"
    assert stories.pin_query(query, ("Tyson Fury", "Anthony Joshua")) == query


def test_a_pinned_query_fits_the_limit_a_story_cuts_to():
    got = stories.pin_query("x" * 250, ("Tyson Fury",), limit=200)
    assert len(got) <= 200 and got.endswith("(Tyson Fury)")


def test_no_anchors_changes_nothing():
    assert stories.pin_query("what is driving it", ()) == "what is driving it"


# --------------------------------------------------------------------------
# the category: resolved in code, read by the picture and the label
# --------------------------------------------------------------------------
def test_a_category_is_resolved_against_the_tree(tree):
    assert stories.resolve_category("Mixed Martial Arts") == "mixed martial arts"
    assert stories.resolve_category("UFC mixed martial arts") == "mixed martial arts"
    assert stories.resolve_category("Sport") == "sports"
    assert stories.resolve_category("boxing") == "boxing"
    assert stories.resolve_category("something the tree has never heard") == ""


def test_a_categorised_story_carries_no_tag_from_another_facet(tree):
    tags = stories.category_tags(
        "boxing", "a new study on Tyson Fury's press conference")
    assert {"boxing", "combat sports", "sports"} <= set(tags)
    assert "science" not in tags


def test_the_composer_is_shown_the_tree_and_asked_for_a_category(tree):
    prompt = stories.build_composer_prompt(
        [stories.Signal(subject="a", observation="b")], "Tuesday")
    assert "combat sports: boxing, mixed martial arts" in prompt
    assert "category" in stories.STORY_SCHEMA["properties"]["stories"][
        "items"]["required"]


def _composer_answer(rows):
    text = json.dumps({"stories": rows})
    response = SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=text)])

    class Messages:
        async def create(self, **_kw):
            return response

    return SimpleNamespace(messages=Messages())


def test_compose_keeps_the_category_and_pins_the_query(tree, monkeypatch):
    monkeypatch.setattr(stories, "settings",
                        dataclasses.replace(config.settings,
                                            stories_compose=True))
    client = _composer_answer([{
        "n": 1, "title": "Fury and Joshua Face Off",
        "angle": "What a chaotic press conference signals",
        "query": "What a chaotic press conference signals about the fight",
        "category": "boxing"}])
    monkeypatch.setattr(stories, "build_async_client", lambda *_a: client)
    signal = stories.Signal(
        subject="Fury v Joshua: heated press conference ends in chaos",
        observation="12 articles", tags=("science", "sports"),
        anchors=("Tyson Fury", "Anthony Joshua"))
    [story] = asyncio.run(stories.compose([signal]))
    assert not story.degraded
    assert story.category == "boxing"
    assert "science" not in story.tags and "boxing" in story.tags
    assert "Tyson Fury" in story.query

    [tile] = T.topics_from_stories([story])
    assert tile.category == "boxing"


def test_the_category_outranks_the_words_for_the_picture(tree):
    th.store().put(th.Thumb("boxing", th.STATUS_APPROVED, facet="sports",
                            mime="image/png"), b"img-boxing")
    th.store().put(th.Thumb("science", th.STATUS_APPROVED, facet="science",
                            mime="image/png"), b"img-science")
    query = "What a chaotic press conference signals about the fight"
    assert th.pick(query, ("science", "sports"))["node"] == "science", \
        "the failure: the alphabetically first tag decided"
    got = th.pick(query, ("science", "sports"), category="boxing")
    assert got["node"] == "boxing" and got["facet"] == "sports"


def test_a_category_without_a_picture_still_names_the_facet(tree):
    tile = T.Topic(id="s", title="Fury and Joshua Face Off", subtitle="",
                   query="a press conference", tags=("boxing", "sports"),
                   icon="", category="mixed martial arts")
    assert tile.as_dict()["thumb_facet"] == "sports"


def test_a_category_never_costs_a_story_its_own_facets_tags(tree):
    """§155: a live game reaches Made for you only when its tags name
    something the listener follows. A category corrects the facet; it must
    not drop the team and league tags that make that match."""
    tags = stories.refine_tags(
        ("cincinnati bengals", "american football", "science", "sports"),
        "american football", "Bengals at Steelers")
    assert {"cincinnati bengals", "american football", "sports"} <= set(tags)
    assert "science" not in tags


def test_a_category_never_refiles_a_game(tree, monkeypatch):
    """A game is filed under sport by its provider (§187); a composed
    category picks its picture and leaves its tags alone."""
    signal = stories.Signal(subject="Bengals at Steelers",
                            observation="kick-off at 8pm",
                            domain=stories.SPORTS,
                            tags=("american football", "sports"))
    story = stories._story_from(signal, "Bengals at Steelers", "What decides it",
                                "what decides Bengals at Steelers", False, 0.0,
                                category="team ownership")
    assert story.tags == ("american football", "sports")
    assert story.category == "team ownership"
