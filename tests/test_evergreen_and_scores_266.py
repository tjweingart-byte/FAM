"""§266: no recency window on a question about no particular moment, and an
old score kept as the last known state.

§265 made the window a filter in code, so a window on "who was Abraham
Lincoln" would drop every good biography. These pin the guardrail in
`episode_intelligence.gate` that holds when the model gives one anyway.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import episode_intelligence as ei  # noqa: E402


def gated(query, **kw):
    kw.setdefault("intent", "explainer")
    kw.setdefault("subject", query)
    kw.setdefault("search_query", query)
    kw.setdefault("recency_days", 30)
    return ei.gate(ei.Brief(query=query, **kw), query)


def test_a_person_from_history_gets_no_window_whatever_the_model_said():
    brief = gated("who is abraham lincoln")
    assert brief.recency_days == 0
    assert any("no particular moment" in n for n in brief.notes)


def test_a_why_question_about_history_gets_no_window():
    """`causal` used to default to 14 days on its own."""
    assert gated("why did the roman empire fall", intent="causal",
                 recency_days=0).recency_days == 0
    assert gated("why did the roman empire fall", intent="causal").recency_days == 0


def test_an_old_year_is_history_not_a_moment():
    assert gated("what happened at the 1969 moon landing").recency_days == 0


def test_a_question_about_a_moment_keeps_its_window():
    assert gated("why did Meta's stock rise today", intent="causal").recency_days >= 2
    assert gated("Oracle news this week").recency_days == 30
    assert gated("The latest on Nasdaq as of Tuesday, October 13, 2026",
                 intent="update").recency_days >= 2


def test_a_live_domain_or_an_awaited_outcome_keeps_its_window():
    assert gated("Chiefs Bills", live_domain="sports").recency_days == 30
    assert gated("the election in Ohio", outcome_dependent=True).recency_days == 30
    assert gated("Eagles game", intent="recap").recency_days == 30


def test_an_evergreen_question_with_no_window_stays_without_one():
    assert gated("how do vaccines work", recency_days=0).recency_days == 0
