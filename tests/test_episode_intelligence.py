"""The layer between the typed question and the search.

FAM EI's job is to make the one search FAM pays for a search for the right
thing, and to hand the writer what it worked out on the way. Three properties
matter more than any individual behaviour, and most of what is below is one of
them:

1. **It cannot stop an episode.** Every failure path - no key, a timeout, a
   refusal, unreadable JSON, a gate that trips - falls back to the raw query,
   which is exactly the behaviour FAM had before this module existed. A layer
   that adds quality must not be able to subtract availability.
2. **It degrades loudly.** `Brief.degraded` and `/api/health` say so. An EI
   that quietly stopped running would look identical from outside to one that
   is working; the episodes would merely be less relevant. That is the
   "quietly worse than intended" shape this project has lost the most time to.
3. **It never asserts a fact.** Nothing has been retrieved when it runs, so it
   produces cautions and questions, never claims.

Nothing here needs an ANTHROPIC_API_KEY or the network: the client is stubbed
at `build_async_client`, so the real call shape is still exercised.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import episode_intelligence as ei  # noqa: E402
import script_generator as sg  # noqa: E402
from script_generator import ScriptNotes, build_prompt, plan_episode  # noqa: E402


# --------------------------------------------------------------------------
# a stubbed model
# --------------------------------------------------------------------------
GOOD = {
    "intent": "recap",
    "subject": "the San Francisco 49ers game",
    "why_now": "they played last night",
    "why_now_confidence": "high",
    "search_query": "49ers final score result",
    "must_establish": ["final score", "who scored"],
    "recency_days": 3,
    "structure": "sports_recap",
    "cautions": ["the game may not have finished"],
    "live_domain": "sports",
}


class FakeUsage:
    input_tokens = 100
    output_tokens = 50
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


def fake_reply(payload, stop_reason="end_turn"):
    block = types.SimpleNamespace(type="text", text=json.dumps(payload))
    return types.SimpleNamespace(content=[block], usage=FakeUsage(),
                                 stop_reason=stop_reason)


@pytest.fixture
def model(monkeypatch):
    """A stubbed Claude, recording exactly how it was asked."""
    calls: list = []
    state = {"reply": fake_reply(GOOD), "raise": None}

    class FakeMessages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            if state["raise"] is not None:
                raise state["raise"]
            if callable(state["reply"]):
                return state["reply"]()
            return state["reply"]

    monkeypatch.setattr(ei, "build_async_client",
                        lambda key=None: types.SimpleNamespace(messages=FakeMessages()))
    monkeypatch.setattr(ei.credentials, "active", lambda name: "sk-test")
    return calls, state


# --------------------------------------------------------------------------
# the shape of the call
# --------------------------------------------------------------------------
def test_the_brief_is_asked_for_as_a_closed_schema(model):
    """A free-text brief has to be parsed, and a parse that half-works is worse
    than one that fails: it produces a plausible brief with one wrong field and
    nothing downstream can tell."""
    calls, _ = model
    asyncio.run(ei.understand("who won the 49ers game", 3))

    fmt = calls[0]["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["additionalProperties"] is False
    assert set(fmt["schema"]["required"]) == set(fmt["schema"]["properties"])
    assert fmt["schema"]["properties"]["intent"]["enum"] == list(ei.INTENTS)


def test_understanding_runs_at_low_effort_because_the_listener_is_waiting(model):
    calls, _ = model
    asyncio.run(ei.understand("who won the 49ers game", 3))
    assert calls[0]["output_config"]["effort"] == "low"


def test_the_brief_reaches_the_fields_the_rest_of_the_system_reads(model):
    brief = asyncio.run(ei.understand("who won the 49ers game", 3))
    assert brief.degraded is False
    assert brief.intent == "recap"
    assert brief.retrieval == "49ers final score result"
    assert brief.recency_days == 3
    assert brief.structure == "sports_recap"
    assert brief.live_domain == "sports"


def test_what_understanding_cost_lands_in_the_episode_total(model):
    """One episode, one bill. EI is a real model call on the generation path,
    and a call nobody counts makes a researched episode look cheaper than it
    is - on exactly the episodes that cost the most."""
    notes = ScriptNotes()
    asyncio.run(ei.understand("who won the 49ers game", 3, notes=notes))
    assert notes.usage.model_calls == 1
    assert notes.usage.input_tokens == 100


# --------------------------------------------------------------------------
# it cannot stop an episode
# --------------------------------------------------------------------------
@pytest.mark.parametrize("failure, because", [
    (RuntimeError("no key"), "the call raised"),
    (asyncio.TimeoutError(), "the call timed out"),
])
def test_a_failed_call_falls_back_to_the_raw_query(model, failure, because):
    _, state = model
    state["raise"] = failure
    brief = asyncio.run(ei.understand("who won the 49ers game", 3))
    assert brief.degraded is True, because
    assert brief.retrieval == "who won the 49ers game", (
        "a degraded brief must search exactly what FAM searched before EI "
        "existed, not nothing and not something invented")
    assert brief.notes, "it degraded without saying why"


def test_unreadable_json_falls_back_rather_than_half_parsing(model):
    _, state = model
    state["reply"] = types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text="not json at all")],
        usage=FakeUsage(), stop_reason="end_turn")
    brief = asyncio.run(ei.understand("who won the 49ers game", 3))
    assert brief.degraded is True
    assert brief.retrieval == "who won the 49ers game"


def test_a_refusal_is_not_read_as_a_brief(model):
    """`output_config.format` guarantees JSON in the *answer*. A refusal never
    gets that far, and one parsed as a brief would be a silent degradation
    rather than a visible one."""
    _, state = model
    state["reply"] = fake_reply(GOOD, stop_reason="refusal")
    brief = asyncio.run(ei.understand("something declined", 3))
    assert brief.degraded is True


def test_the_switch_turns_it_off_completely(model, monkeypatch):
    calls, _ = model
    monkeypatch.setattr(ei, "settings",
                        dataclasses.replace(config.settings,
                                            episode_intelligence=False))
    brief = asyncio.run(ei.understand("who won the 49ers game", 3))
    assert brief.degraded is True and not calls, (
        "EPISODE_INTELLIGENCE=0 still spent a model call")


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------
def test_a_search_query_that_replaced_the_subject_is_rejected():
    """The failure this catches: the search comes back about something else
    entirely, and every stage after this one is then correct about the wrong
    subject."""
    drifted = ei.Brief(query="how does a heat pump work",
                       search_query="Federal Reserve interest rate decision",
                       intent="explainer", structure="explainer")
    gated = ei.gate(drifted, "how does a heat pump work")
    assert gated.retrieval == "how does a heat pump work"
    assert any("shares no subject" in note for note in gated.notes)


def test_resolving_a_subject_is_not_treated_as_drift():
    """The check has to let this through, and that is what limits it.

    "the fed" becoming "US Federal Reserve rate decision" shares no token with
    what was typed - and is the single most valuable thing this layer does. Any
    test strict enough to catch a subject being *replaced* also rejects one
    being *resolved*, because the two look identical to a token comparison. So
    this stays weak on purpose, and semantic drift is caught downstream where
    there is evidence to catch it with: a search for the wrong subject comes
    back without what `must_establish` asked for, and the retry goes to
    `subject` instead.
    """
    resolved = ei.Brief(query="the fed",
                        search_query="US Federal Reserve interest rate decision",
                        intent="update", structure="breaking")
    gated = ei.gate(resolved, "the fed")
    assert gated.retrieval == "US Federal Reserve interest rate decision"
    assert not any("shares no subject" in note for note in gated.notes)


def test_a_query_with_no_content_words_is_left_alone():
    """"what is it" has nothing to share, so the drift test cannot apply - and
    applying it anyway would throw away every improvement on a vague query,
    which is the case improvement helps most."""
    brief = ei.Brief(query="what is it", search_query="what is a heat pump",
                     intent="explainer", structure="explainer")
    assert ei.gate(brief, "what is it").retrieval == "what is a heat pump"


def test_an_unknown_intent_or_structure_is_mapped_back_into_the_vocabulary():
    brief = ei.Brief(query="x", search_query="x", intent="vibes",
                     structure="freeform")
    gated = ei.gate(brief, "x")
    assert gated.intent in ei.INTENTS
    assert gated.structure in ei.STRUCTURES


def test_confidence_without_a_hypothesis_is_downgraded():
    """The blueprint's "do not infer too much". High confidence with nothing
    behind it is how an invented trigger ends up framing an episode."""
    brief = ei.Brief(query="salesforce", search_query="salesforce", why_now="",
                     why_now_confidence="high", intent="update",
                     structure="breaking")
    assert ei.gate(brief, "salesforce").why_now_confidence == "low"


def test_a_question_about_a_moment_gets_a_recency_window():
    """Without one the retrieval competes against every well-ranked article
    ever written on the subject, which is how a two-year-old explainer becomes
    the evidence for "what happened last night"."""
    brief = ei.Brief(query="what did the fed do", search_query="fed decision",
                     intent="update", structure="breaking", recency_days=0)
    assert ei.gate(brief, "what did the fed do").recency_days == \
        config.settings.ei_default_recency_days


def test_an_evergreen_question_gets_no_window():
    brief = ei.Brief(query="how does a heat pump work",
                     search_query="heat pump mechanism",
                     intent="explainer", structure="explainer", recency_days=0)
    assert ei.gate(brief, "how does a heat pump work").recency_days == 0


def test_a_recap_always_carries_the_caution_that_nothing_is_confirmed_yet():
    """Nothing has been retrieved when the gate runs, so the result of anything
    recent is unknown here *by construction*. Saying so is what stops the
    writer inventing one."""
    brief = ei.Brief(query="who won", search_query="who won", intent="recap",
                     structure="sports_recap", cautions=[])
    assert any("confirmed" in c for c in ei.gate(brief, "who won").cautions)


def test_the_gate_never_refuses():
    """Whatever it is handed, something searchable comes out. A gate that could
    return nothing would be a new way for an episode to fail."""
    for nonsense in (ei.Brief(), ei.Brief(query="x"),
                     ei.Brief(intent="!!", structure="??", recency_days="soon")):
        assert ei.gate(nonsense, "a real question").retrieval


# --------------------------------------------------------------------------
# duration buys depth, not words
# --------------------------------------------------------------------------
def test_each_duration_band_asks_for_something_different():
    goals = [ei.depth_for(m)[0] for m in (1, 3, 5, 10)]
    assert len(set(goals)) == 4, (
        "two durations ask for the same thing, so the slider only changes "
        "length - which is the failure the blueprint names")


def test_depth_is_described_as_content_rather_than_as_a_word_count():
    for minutes in (1, 3, 5, 10):
        _, mix = ei.depth_for(minutes)
        assert "word" not in mix.lower()


# --------------------------------------------------------------------------
# what the writer is told
# --------------------------------------------------------------------------
def test_a_structure_is_offered_as_a_shape_and_never_as_boxes_to_fill():
    """The lesson of the prompt rewrite, pinned. The old prompt imposed five
    fixed beats, so a golf recap had to invent something for "the main debate
    or open question" - the prompt was requesting invention. A shape that
    cannot be departed from is that prompt again under a new name."""
    brief = ei.Brief(query="x", search_query="x", structure="sports_recap")
    note = ei.build_structure_note(brief)
    assert "Drop any part you have nothing real for" in note
    assert "invented" in note


def test_a_degraded_brief_tells_the_writer_nothing(model):
    """It knows nothing the writer does not already know from the query.
    Printing its emptiness as though it were findings would be worse than
    silence."""
    _, state = model
    state["raise"] = RuntimeError("down")
    brief = asyncio.run(ei.understand("who won", 3))
    assert ei.build_brief_block(brief, 3) == ""


def test_low_confidence_tells_the_writer_not_to_manufacture_topicality():
    brief = ei.Brief(query="x", search_query="x", why_now="",
                     why_now_confidence="low", intent="explainer")
    block = ei.build_brief_block(brief, 3)
    assert "standing question" in block
    assert "manufacture" in block


def test_a_high_confidence_why_now_is_still_never_about_the_listener():
    """FAM may form a hypothesis about the news. It may not tell someone why
    they asked - it does not know, and being told is unpleasant when it is
    wrong."""
    brief = ei.Brief(query="x", search_query="x", why_now="the Fed cut rates",
                     why_now_confidence="high", intent="update")
    block = ei.build_brief_block(brief, 3)
    assert "never tell them why they asked" in block


# --------------------------------------------------------------------------
# reaching the prompt
# --------------------------------------------------------------------------
def test_the_brief_reaches_the_writing_prompt():
    plan = dataclasses.replace(
        plan_episode("who won the 49ers game", 3),
        brief=ei.gate(ei.Brief(**{k: v for k, v in GOOD.items()},
                               query="who won the 49ers game"),
                      "who won the 49ers game"),
        evidence="SOURCE 1\nTitle: x\nPublished: 2026-09-13 (yesterday)\n")
    prompt = build_prompt(plan)
    assert "the San Francisco 49ers game" in prompt
    assert "final score" in prompt
    assert "sports recap story usually goes" in prompt


def test_temporal_discipline_is_stated_whenever_there_is_dated_material():
    """The blueprint's temporal table, as an instruction. The failures it names
    - a Wednesday game called "last night", a Sunday final whose winner is
    named on Friday - are not failures of reasoning. Nobody had said that the
    tense is derived from the dates rather than chosen."""
    plan = dataclasses.replace(plan_episode("who won", 3),
                               evidence="SOURCE 1\nPublished: 2026-09-12\n")
    prompt = build_prompt(plan)
    assert "Two days ago is \"two days ago\", not \"last night\"" in prompt
    assert "If something has not happened yet, it has no result" in prompt
    assert "An undated source cannot date anything" in prompt


def test_an_unresearched_episode_is_told_nothing_about_dates():
    """There is nothing dated to reason about, so the rules would be noise -
    and a prompt that grows on every episode is a prompt nobody reads."""
    prompt = build_prompt(plan_episode("how does a heat pump work", 3, search=False))
    assert "An undated source cannot date anything" not in prompt


def test_evidence_that_missed_something_says_so_rather_than_going_quiet():
    """A gap filled from month-old memory arrives in the same confident voice
    as the researched half, and a listener cannot tell them apart. That is the
    one thing the accuracy rules call unforgivable, so the writer is told
    exactly which part is not established."""
    plan = dataclasses.replace(plan_episode("who won", 3),
                               evidence="SOURCE 1\nTitle: x\n",
                               thin_on=("the final score",))
    prompt = build_prompt(plan)
    assert "thin on: the final score" in prompt
    assert "not yet reported" in prompt


# --------------------------------------------------------------------------
# where it runs, and where it must not
# --------------------------------------------------------------------------
def test_the_cover_half_never_waits_for_a_brief(model):
    """`role="opening"` is defined as the half that starts immediately from
    what the model already knows. A model call in front of it is precisely the
    wait it exists to cover."""
    calls, _ = model
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = dataclasses.replace(plan_episode("who won", 3, search=True),
                               role="opening")
    returned = asyncio.run(generator.understand(plan))
    assert returned.brief is None and not calls


def test_an_unresearched_episode_does_not_pay_for_a_brief(model):
    """EI's largest single product is the retrieval query, and an episode that
    retrieves nothing cannot spend it."""
    calls, _ = model
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    plan = plan_episode("how does a heat pump work", 3, search=False)
    assert asyncio.run(generator.understand(plan)).brief is None
    assert not calls


def test_a_brief_that_already_exists_is_not_rebuilt(model):
    """Prefetch builds the brief before the tap - that is the whole point of
    prefetch, and re-deriving it here would throw away the latency that buying
    it early was for."""
    calls, _ = model
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    existing = ei.Brief(query="who won", search_query="who won")
    plan = dataclasses.replace(plan_episode("who won", 3, search=True),
                               brief=existing)
    assert asyncio.run(generator.understand(plan)).brief is existing
    assert not calls


def test_health_says_which_state_a_deploy_is_in():
    """An EI that has been switched off looks identical from outside to one
    that is working; the episodes are merely less relevant."""
    report = ei.report()
    assert report["enabled"] is True
    assert report["model"] and report["timeout_seconds"] > 0
    assert set(report["intents"]) == set(ei.INTENTS)
