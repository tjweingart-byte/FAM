"""The bottom of the retrieval ladder, and what FAM does when it gets there.

PROBLEMS.md §109. §108 made every episode wait for its evidence before a word
is written, which left one question unanswered: what happens when the evidence
never comes. The answer was "write it from memory anyway, silently", which is
the failure §89 spent a whole section on - never infer a current-world fact
from the absence of current-world evidence - reached by a different road.

Two halves, and both are tested here: the ladder, which makes the bottom hard
to reach, and the refusal, which is what happens there and only for a question
that actually turns on something current.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import research  # noqa: E402
import script_generator as sg  # noqa: E402
from script_generator import ScriptNotes, plan_episode  # noqa: E402


def backend(monkeypatch, value):
    patched = dataclasses.replace(sg.settings, research_backend=value)
    monkeypatch.setattr(research, "settings", patched)
    monkeypatch.setattr(sg, "settings", patched)


def rungs(monkeypatch, answers: dict):
    """Stub each rung by name. `answers[name]` is the packet it returns."""
    tried: list = []

    async def retrieve(query, backend=None, brief=None):
        tried.append(backend)
        packet = answers.get(backend)
        if isinstance(packet, Exception):
            raise packet
        return packet if packet is not None else research.Packet(backend=backend)

    monkeypatch.setattr(research, "retrieve", retrieve)
    return tried


def found(name):
    return research.Packet(context=f"SOURCE 1\nTitle: from {name}", backend=name,
                           sources=["reuters.com"])


BRIEF_CURRENT = types.SimpleNamespace(
    retrieval="who won", subject="the game", recency_days=1,
    outcome_dependent=True, must_establish=[], degraded=False, live_domain="")
BRIEF_EVERGREEN = types.SimpleNamespace(
    retrieval="how a heat pump works", subject="heat pumps", recency_days=0,
    outcome_dependent=False, must_establish=[], degraded=False, live_domain="")


def research_with(plan, notes=None):
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    return asyncio.run(generator.research(plan, notes))


# --------------------------------------------------------------------------
# the ladder
# --------------------------------------------------------------------------
def test_the_ladder_stops_at_the_first_rung_that_finds_anything(monkeypatch):
    backend(monkeypatch, "exa")
    tried = rungs(monkeypatch, {"exa": found("exa")})
    plan = dataclasses.replace(plan_episode("who won", 3, search=True),
                               brief=BRIEF_CURRENT)
    assert "from exa" in research_with(plan).evidence
    assert tried == ["exa"], "a rung ran after one that had already succeeded"


def test_an_empty_backend_falls_to_the_keyless_one_before_the_expensive_one(
        monkeypatch):
    """Order is cost, not quality. GDELT is one keyless HTTP call; the model's
    own search is 10-25 seconds and a model call, so it goes last."""
    backend(monkeypatch, "exa")
    tried = rungs(monkeypatch, {"gdelt": found("gdelt")})
    plan = dataclasses.replace(plan_episode("who won", 3, search=True),
                               brief=BRIEF_CURRENT)
    notes = ScriptNotes()
    assert "from gdelt" in research_with(plan, notes).evidence
    assert tried == ["exa", "gdelt"], "the expensive rung ran unnecessarily"
    assert notes.research["fell_back_from"] == "exa", "the fallback was silent"


def test_the_model_s_own_search_is_the_last_rung(monkeypatch):
    backend(monkeypatch, "exa")
    tried = rungs(monkeypatch, {"claude": found("claude")})
    plan = dataclasses.replace(plan_episode("who won", 3, search=True),
                               brief=BRIEF_CURRENT)
    assert "from claude" in research_with(plan).evidence
    assert tried == ["exa", "gdelt", "claude"]


def test_a_rung_that_raises_is_a_rung_that_failed(monkeypatch):
    """Not an episode that failed. `research.retrieve` raises whatever the
    vendor raised, and with one stream that would be the whole episode."""
    backend(monkeypatch, "exa")
    tried = rungs(monkeypatch, {"exa": RuntimeError("502"),
                                "gdelt": found("gdelt")})
    plan = dataclasses.replace(plan_episode("who won", 3, search=True),
                               brief=BRIEF_CURRENT)
    assert "from gdelt" in research_with(plan).evidence
    assert tried == ["exa", "gdelt"]


def test_the_configured_backend_is_never_tried_twice(monkeypatch):
    backend(monkeypatch, "gdelt")
    tried = rungs(monkeypatch, {"claude": found("claude")})
    plan = dataclasses.replace(plan_episode("who won", 3, search=True),
                               brief=BRIEF_CURRENT)
    research_with(plan)
    assert tried == ["gdelt", "claude"]


# --------------------------------------------------------------------------
# the refusal
# --------------------------------------------------------------------------
def prepared(plan):
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    return asyncio.run(generator.prepare(plan))


def test_a_question_that_needs_today_is_refused_rather_than_guessed(monkeypatch):
    backend(monkeypatch, "exa")
    rungs(monkeypatch, {})
    monkeypatch.setattr(sg.live_facts, "lookup",
                        lambda brief, notes=None: _none())
    plan = dataclasses.replace(plan_episode("who won", 3, search=True),
                               brief=BRIEF_CURRENT)
    with pytest.raises(research.NoEvidence) as raised:
        prepared(plan)
    said = str(raised.value)
    assert "not going to guess" in said
    assert "Try again" in said, "a refusal with no next step is a dead end"


def test_an_evergreen_question_is_still_written(monkeypatch):
    """The model's own knowledge is accurate here, and a refusal would be a
    worse answer than the episode."""
    backend(monkeypatch, "exa")
    rungs(monkeypatch, {})
    monkeypatch.setattr(sg.live_facts, "lookup",
                        lambda brief, notes=None: _none())
    plan = dataclasses.replace(
        plan_episode("how does a heat pump work", 3, search=True),
        brief=BRIEF_EVERGREEN)
    assert prepared(plan).evidence == ""


def test_a_live_state_is_evidence_even_when_the_index_found_nothing(monkeypatch):
    """A score from a scores provider answers the question whether or not
    anybody has published an article about it yet - which is the whole reason
    `live_facts` exists, and the reason this check is in `prepare` rather than
    in `research`."""
    backend(monkeypatch, "exa")
    rungs(monkeypatch, {})

    facts = types.SimpleNamespace(status="final", as_dict=lambda: {})
    lookup = types.SimpleNamespace(
        domain="sports", outcome="facts", facts=facts, status="final",
        as_prompt_block=lambda: "THE SCORE", attempts=[], detail="")

    async def live(brief, notes=None):
        return lookup

    monkeypatch.setattr(sg.live_facts, "lookup", live)
    plan = dataclasses.replace(plan_episode("who won", 3, search=True),
                               brief=BRIEF_CURRENT)
    prepared_plan = prepared(plan)          # does not raise
    assert prepared_plan.live is lookup


def test_an_unresearched_episode_is_never_refused(monkeypatch):
    backend(monkeypatch, "exa")
    rungs(monkeypatch, {})
    monkeypatch.setattr(sg.live_facts, "lookup",
                        lambda brief, notes=None: _none())
    plan = dataclasses.replace(
        plan_episode("who won", 3, search=False), brief=BRIEF_CURRENT)
    assert prepared(plan).evidence == ""


def test_a_degraded_brief_falls_back_to_the_keyword_floor(monkeypatch):
    """EI is the good signal and it is allowed to be unavailable. The keyword
    heuristic is what `cache.ttl_for` already uses as the floor for paths that
    have nothing better, and this is the same job."""
    backend(monkeypatch, "exa")
    rungs(monkeypatch, {})
    monkeypatch.setattr(sg.live_facts, "lookup",
                        lambda brief, notes=None: _none())
    degraded = types.SimpleNamespace(
        retrieval="", subject="", recency_days=0, outcome_dependent=False,
        must_establish=[], degraded=True, live_domain="")
    volatile = dataclasses.replace(
        plan_episode("latest news on the fed", 3, search=True), brief=degraded)
    with pytest.raises(research.NoEvidence):
        prepared(volatile)

    timeless = dataclasses.replace(
        plan_episode("why the Roman republic fell", 3, search=True),
        brief=degraded)
    assert prepared(timeless).evidence == ""


def test_the_listener_is_told_something_they_can_act_on():
    """The sentence is composed server-side, so the web app and the iOS
    client cannot word the same refusal two ways."""
    import app as app_mod

    said = app_mod.friendly_error(research.NoEvidence(
        "FAM could not reach a single source for this one, and it needs "
        "current information to answer - so it is not going to guess. "
        "Try again in a moment."))
    assert "not going to guess" in said
    assert "See the server log" not in said, "the remedy was thrown away"


def test_the_refusal_is_not_an_unavailable_backend():
    """Two different sentences, because they have two different remedies: one
    is a deployment that is missing a credential, the other is a retrieval
    that found nothing."""
    assert not issubclass(research.NoEvidence, research.ResearchUnavailable)
    assert not issubclass(research.ResearchUnavailable, research.NoEvidence)


async def _none():
    return None
