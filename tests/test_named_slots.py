""""Sunday Night Football" is the name of one game, never a description.

The reported failure: `sunday night football recap` produced an episode about
Bengals v Steelers, an afternoon kick-off, because nothing knew the phrase was
a proper name. These pin the code half of the fix, which holds whatever the
model makes of the words.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import episode_intelligence as ei  # noqa: E402
import live_sources  # noqa: E402
import named_slots  # noqa: E402


def test_the_phrase_and_its_abbreviation_name_the_slot():
    assert named_slots.find("sunday night football recap").key == "snf"
    assert named_slots.find("Sunday-Night Football").key == "snf"
    assert named_slots.find("who won SNF").key == "snf"
    assert named_slots.find("mnf score").key == "mnf"
    assert named_slots.find("football on sunday") is None
    assert named_slots.find("bengals steelers") is None


def test_a_brief_that_widened_the_name_is_pulled_back_to_the_one_game():
    # What the model did: read the words as a description.
    brief = ei.Brief(query="sunday night football recap", intent="recap",
                     subject="NFL Sunday football", search_query="NFL week 4 recap",
                     search_fallback="NFL scores", recency_days=3,
                     structure="recap", cautions=["x"], must_establish=["y"])
    brief = ei.gate(brief, "sunday night football recap")
    assert brief.named_slot == "snf"
    assert "Sunday Night Football" in brief.search_query
    assert "Sunday Night Football" in brief.search_fallback
    assert "Sunday Night Football" in brief.subject
    assert "not the afternoon" in brief.subject
    assert brief.must_establish[0].startswith("which teams played in Sunday Night Football")
    # The result caution stays first; the slot caution follows it.
    assert "confirmed yet" in brief.cautions[0]
    assert "one specific game" in brief.cautions[1]
    block = ei.build_brief_block(brief, 3)
    assert "never substitute another game" in block


def test_a_degraded_brief_still_searches_for_the_named_game():
    brief = ei.fallback_brief("sunday night football recap", "no key")
    assert brief.named_slot == "snf"
    assert "sunday night football" in brief.search_query.lower()


def test_ordinary_questions_are_untouched():
    brief = ei.fallback_brief("how do interest rates work", "no key")
    assert brief.named_slot == ""
    assert brief.search_query == "how do interest rates work"


def test_the_prompt_teaches_the_general_rule():
    assert "proper names" in ei.EI_SYSTEM
    assert "never widen it to the category" in ei.EI_SYSTEM


def _row(gid, home, away, when):
    return {"game": {"id": gid, "date": {"timestamp": int(when.timestamp())},
                     "status": {"short": "FT"}},
            "teams": {"home": {"name": home}, "away": {"name": away}}}


def test_the_scoreboard_picks_the_game_by_kickoff_not_by_the_word_football(monkeypatch):
    # Sunday 28 Sep 2026: a 1pm ET game and an 8:20pm ET game.
    afternoon = datetime(2026, 9, 27, 17, 0, tzinfo=timezone.utc)
    night = datetime(2026, 9, 28, 0, 20, tzinfo=timezone.utc)
    rows = [_row(1, "Pittsburgh Steelers", "Cincinnati Bengals", afternoon),
            _row(2, "Green Bay Packers", "Atlanta Falcons", night)]
    monkeypatch.setattr(live_sources, "card_rows", lambda *a, **k: rows)

    async def no_call(*a, **k):
        raise AssertionError("the sweep had the card")
    monkeypatch.setattr(live_sources, "api_sports_json", no_call)

    brief = ei.fallback_brief("sunday night football recap", "no key")
    entity = asyncio.run(live_sources.ApiSportsSource().resolve(brief))
    assert entity is not None
    assert entity.label == "Green Bay Packers v Atlanta Falcons"


def test_no_game_in_the_window_is_no_game_rather_than_a_guess(monkeypatch):
    afternoon = datetime(2026, 9, 27, 17, 0, tzinfo=timezone.utc)
    rows = [_row(1, "Pittsburgh Steelers", "Cincinnati Bengals", afternoon)]
    monkeypatch.setattr(live_sources, "card_rows", lambda *a, **k: rows)

    async def same_card(*a, **k):
        return {"response": rows}
    monkeypatch.setattr(live_sources, "api_sports_json", same_card)
    brief = ei.fallback_brief("sunday night football recap", "no key")
    assert asyncio.run(live_sources.ApiSportsSource().resolve(brief)) is None
