"""EI writes Parallel's inputs (§259).

Parallel reads an objective in plain language beside two or three short
keyword queries. EI used to give it one query and a templated objective that
told an evergreen question to want "recent, dated reporting". Now the brief
carries `search_angles` and `search_objective`, and `research` sends them.
Nothing here reaches the network.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import episode_intelligence as ei  # noqa: E402
import research  # noqa: E402

REPLY = {"results": [{"url": "https://www.reuters.com/a", "title": "Eagles beat Giants",
                      "publish_date": "2026-10-11",
                      "excerpts": ["The Eagles beat the Giants 27-17 on Sunday. "
                                   "Hurts threw two touchdowns."]}]}


@pytest.fixture
def sent(monkeypatch):
    requests: list = []

    def post(url, headers, body):
        requests.append(body)
        return REPLY

    monkeypatch.setattr(research, "_post", post)
    # The key is stubbed rather than set: `credentials` keeps a pool across
    # tests, and one primed here outlived this file.
    monkeypatch.setattr(research, "_key", lambda backend: "par-key")
    monkeypatch.setattr(research, "settings", dataclasses.replace(
        config.settings, research_backend="parallel", research_retry=False,
        gdelt_cross_check=False))
    return requests


def eagles(**fields) -> ei.Brief:
    base = dict(query="did my eagles win", intent="recap", subject="the Philadelphia Eagles",
                search_query="Eagles Giants final score",
                search_fallback="Philadelphia Eagles",
                search_angles=["Jalen Hurts touchdowns Giants", "Eagles Giants box score"],
                search_objective=("The Eagles-Giants NFL game played on or about "
                                  "Sunday 11 October 2026: the final score and who "
                                  "scored, from the league's site or a wire report, "
                                  "not previews or fantasy advice"),
                must_establish=["final score"], recency_days=3)
    base.update(fields)
    return ei.gate(ei.Brief(**base), base["query"])


def test_the_angles_go_beside_the_precise_query_in_one_request(sent):
    brief = eagles()
    asyncio.run(research.retrieve(brief.retrieval, brief=brief))
    assert len(sent) == 1
    assert sent[0]["search_queries"] == [
        "Eagles Giants final score", "Jalen Hurts touchdowns Giants",
        "Eagles Giants box score"]


def test_the_objective_is_eis_own_with_the_window_added_in_code(sent):
    brief = eagles()
    asyncio.run(research.retrieve(brief.retrieval, brief=brief))
    objective = sent[0]["objective"]
    assert objective.startswith("The Eagles-Giants NFL game played")
    assert "A useful source establishes: final score." in objective
    assert "published on or after 20" in objective
    # Never the listener's own words.
    assert "did my eagles win" not in objective


def test_an_evergreen_objective_never_asks_for_recent_reporting():
    brief = eagles(query="how heat pumps work", intent="explainer",
                   subject="heat pumps", search_query="how heat pumps work",
                   search_angles=[], search_objective="", recency_days=0,
                   must_establish=["refrigerant cycle"])
    objective = research.objective_for(brief)
    assert "Recent" not in objective and "published on or after" not in objective
    assert "heat pumps" in objective


def test_the_second_look_drops_the_window_from_the_objective_too():
    brief = eagles()
    assert "on or after" in research.objective_for(brief)
    assert "on or after" not in research.objective_for(brief, 0)


def test_a_broader_or_other_query_gets_no_angles(sent):
    brief = eagles()
    asyncio.run(research.retrieve(brief.broader, brief=brief))
    assert sent[0]["search_queries"] == ["Philadelphia Eagles"]


def test_the_gate_drops_an_angle_on_another_subject_and_caps_them():
    brief = eagles(search_angles=["Fed rate decision", "Eagles Giants final score",
                                  "Eagles Giants box score", "Eagles injury report",
                                  "Hurts passing yards Giants"])
    # The repeat of search_query and the drifted one go; two are kept.
    assert brief.search_angles == ["Eagles Giants box score", "Eagles injury report"]
    assert any("Fed rate decision" in n for n in brief.notes)


def test_a_drifted_search_takes_its_angles_and_objective_with_it():
    brief = eagles(search_query="Federal Reserve rate decision",
                   search_angles=["Fed statement"],
                   search_objective="The Fed's October decision")
    assert brief.search_query == "did my eagles win"
    assert brief.search_angles == [] and brief.search_objective == ""


def test_a_named_slot_is_in_every_angle_and_the_objective():
    brief = ei.gate(ei.Brief(
        query="sunday night football recap", intent="recap", subject="the game",
        search_query="NFL Sunday night recap", search_angles=["prime time box score"],
        search_objective="Last night's prime-time game.", recency_days=3),
        "sunday night football recap")
    assert all("sunday night football" in a.lower() for a in brief.search_angles)
    assert "sunday night football" in brief.search_objective.lower()


def test_the_schema_asks_for_both_and_the_prompt_explains_them():
    assert {"search_angles", "search_objective"} <= set(ei.BRIEF_SCHEMA["required"])
    prompt = ei.build_ei_prompt("did my eagles win", 2, now="Tuesday 13 October 2026")
    assert "**search_angles**" in prompt and "**search_objective**" in prompt
    assert "the moment as dates" in prompt
