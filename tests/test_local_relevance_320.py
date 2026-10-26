"""§320: the local ladder asks what the question is about, and an older
article cannot move the listener's date.

From bad episodes on 10.10-10.13: "no airshow over San Francisco Bay" during
Fleet Week, sourced to a Valkyries fan story that only named the city, and
Meta's move on 13 October "corrected" to June 3 by an older article.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import episode_intelligence as ei  # noqa: E402
import local_news  # noqa: E402
import places  # noqa: E402
import research  # noqa: E402
import script_generator as sg  # noqa: E402


SF = places.Place("San Francisco", "San Francisco", "California", "US",
                  37.77, -122.42, "test")
AIRSHOW = types.SimpleNamespace(
    retrieval="Blue Angels airshow San Francisco Bay Fleet Week",
    must_establish=["whether the Blue Angels airshow is flying over the bay today"])
VALKYRIES = research.Packet(
    context="SOURCE 1\nTitle: Berkeley Amputee Becomes Sparkling Heart of "
            "Valkyries' Ballhalla\nKey evidence:\nFans at Chase Center in San "
            "Francisco cheered.", backend="parallel", sources=["hoodline.com"])


def test_a_story_that_only_names_the_place_does_not_answer_the_question():
    assert not local_news.relevant(VALKYRIES, AIRSHOW, SF)
    flyover = research.Packet(context="Blue Angels airshow thrills Fleet Week "
                                      "crowds over the bay in San Francisco today")
    assert local_news.relevant(flyover, AIRSHOW, SF)


def test_a_general_town_question_is_answered_by_any_town_story():
    general = types.SimpleNamespace(must_establish=["what's happening in San Francisco today"])
    assert local_news.relevant(VALKYRIES, general, SF)
    assert local_news.relevant(VALKYRIES, types.SimpleNamespace(), SF)


def test_the_web_rung_never_narrows_to_a_learned_outlet_and_drops_what_misses():
    store = local_news.store()
    store.add_outlet("hoodline.com", "https://hoodline.com/", town="San Francisco",
                     county="San Francisco", region="California",
                     scope=local_news.MENTION, source="learned")
    asked = {}

    async def search(query, domains, must_name):
        asked.update(domains=domains, must_name=must_name)
        return VALKYRIES

    async def nothing(place):
        return None

    import pytest
    mp = pytest.MonkeyPatch()
    mp.setattr(local_news, "refresh_place", nothing)
    mp.setattr(local_news, "town_items", lambda place: [])
    mp.setattr(local_news, "county_items", lambda place: [])
    try:
        got = asyncio.run(local_news.research_local(SF, AIRSHOW, search=search))
    finally:
        mp.undo()
    assert "hoodline.com" not in asked["domains"], \
        "a site an earlier search found narrowed every later search to itself"
    assert asked["must_name"] == "San Francisco"
    assert got.packet is None and got.gap, "an unrelated story was passed as the town's news"


def test_ei_sends_a_specific_happening_to_the_general_search():
    """The prompt line that put "its events" on the local ladder."""
    said = " ".join(ei.build_ei_prompt("Airshow in San Francisco bay today", 2).split())
    assert "airshow in San Francisco Bay today" in said
    assert "that is a news question, searched everywhere" in said


def test_an_older_article_cannot_move_the_listener_s_date_or_disprove_an_event():
    plan = dataclasses.replace(
        sg.plan_episode("Why did Meta's stock rise 3.2 percent on 13 October", 2,
                        search=True),
        evidence="SOURCE 1\nTitle: Meta up 3.2%\nPublished: 2026-06-03",
        thin_on=["whether the airshow is on today"])
    prompt = " ".join(sg.build_prompt(plan).split())
    assert "An article cannot speak for a day it was written before" in prompt
    assert "Never move their date to the day an older article describes" in prompt
    assert "never say it is not happening" in prompt


def test_the_local_block_never_calls_what_was_found_all_there_is():
    plan = dataclasses.replace(
        sg.plan_episode("airshow in San Francisco", 2, search=True),
        local=local_news.LocalResult(place=SF))
    block = " ".join(sg.build_local_block(plan).split())
    assert "not all that is happening there" in block
    assert "is about San Francisco itself" not in block
