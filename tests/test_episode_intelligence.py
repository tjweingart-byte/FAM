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
import live_facts  # noqa: E402
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


def test_the_mandatory_caution_survives_the_model_having_its_own():
    """The bug, and it is the smallest and most expensive one in §88.

    The caution was appended `if not brief.cautions` - so the single most
    important instruction in FAM was suppressed by the presence of any other
    caution at all. A model asked for cautions produces some, so in production
    it was suppressed nearly every time, while the source read as though the
    guard were there and the test above passed on an empty list."""
    brief = ei.Brief(query="chiefs game", search_query="chiefs game",
                     intent="recap", structure="sports_recap",
                     cautions=["the Broncos are the reigning division winners"])
    gated = ei.gate(brief, "chiefs game")
    assert any("confirmed yet" in c for c in gated.cautions)
    # And it cannot be pushed off the end by the six-item truncation either.
    crowded = ei.Brief(query="who won", search_query="who won", intent="recap",
                       cautions=[f"caution {n}" for n in range(9)])
    assert "confirmed yet" in ei.gate(crowded, "who won").cautions[0]


def test_the_caution_says_that_started_is_not_finished():
    """The old wording - "nothing has been confirmed yet" - is true of a game
    that has not kicked off and of one in its third quarter, and reads as being
    about the former. The state it had no words for is the one that went
    wrong."""
    gated = ei.gate(ei.Brief(query="who won", search_query="who won",
                             intent="recap"), "who won")
    assert any("has started is not an event that has finished" in c
               for c in gated.cautions)


def test_a_question_whose_answer_is_a_result_is_marked_as_one():
    """A property of the request, never of the world - which is what makes it
    answerable by a layer that has retrieved nothing. EI cannot know whether
    the game finished; it knows perfectly well that "who won" has no answer
    until one does."""
    for intent in ("recap", "update"):
        brief = ei.gate(ei.Brief(query="who won", search_query="who won",
                                 intent=intent), "who won")
        assert brief.outcome_dependent, intent
    # A live domain is the case where the lag between concluding and being
    # reported is longest, so it counts however the intent was labelled.
    live = ei.gate(ei.Brief(query="chiefs game", search_query="chiefs game",
                            intent="explainer", live_domain="sports"),
                   "chiefs game")
    assert live.outcome_dependent
    # And an evergreen question is not dragged into it.
    evergreen = ei.gate(ei.Brief(query="how does a heat pump work",
                                 search_query="heat pump", intent="explainer"),
                        "how does a heat pump work")
    assert not evergreen.outcome_dependent


def test_a_shape_that_needs_an_outcome_names_the_one_that_does_not():
    """Drop-any-beat-you-have-nothing-for was not enough on its own: the
    result *is* the sports-recap shape, so dropping it leaves nothing and
    filling it is the path of least resistance. The alternative has to be
    named."""
    note = ei.build_structure_note(
        ei.Brief(structure="sports_recap", outcome_dependent=True))
    assert "in-progress" in note
    assert ei.STRUCTURES["in_progress"] in note
    assert "Do not keep the shape and fill the missing beat" in note

    # An episode that does not turn on an outcome is told none of this.
    plain = ei.build_structure_note(ei.Brief(structure="explainer"))
    assert "in-progress" not in plain


def test_the_writer_is_told_the_result_may_not_exist_yet():
    """The brief block is where the episode's shape is decided, so this belongs
    above the cautions: it changes what the episode *is*, not how a sentence in
    it is worded."""
    block = ei.build_brief_block(
        ei.Brief(query="chiefs game", subject="the Kansas City Chiefs game",
                 intent="recap", structure="sports_recap",
                 outcome_dependent=True), 3)
    assert "a result only exists once the thing it comes from has finished" in block
    assert "Both are real episodes" in block


def test_a_live_question_with_no_feed_says_so_rather_than_going_quiet():
    """`live_facts` declares three domains and has a provider for none, so
    today this fires on every such question. The lag is structural - a
    scoreboard changes instantly and the article saying so is written,
    published and indexed afterwards - and the writer cannot allow for it
    unless it is told."""
    import live_facts

    brief = ei.Brief(query="chiefs game", search_query="chiefs game",
                     intent="recap", live_domain="sports")
    plan = dataclasses.replace(
        plan_episode("chiefs game", 3), brief=brief,
        evidence="SOURCE 1\nTitle: x\n",
        live=live_facts.LiveLookup("sports", live_facts.NOT_CONFIGURED))
    prompt = build_prompt(plan)
    assert "FAM has no live feed for this domain" in prompt
    assert "Not knowing is not the same as nothing having happened" in prompt

    # And an episode that turns on nothing live is not told any of it.
    quiet = dataclasses.replace(plan, brief=ei.Brief(query="x", intent="explainer"),
                                live=None)
    assert "turns on a live" not in build_prompt(quiet)


# --------------------------------------------------------------------------
# what EI may decide, and what only evidence may decide
# --------------------------------------------------------------------------
def test_ei_may_never_report_an_event_status():
    """The line the whole of §88 and §89 turns on.

    EI reads the *request* and may say it wants a result. It has retrieved
    nothing and its knowledge is months old, so it may not say the event is
    under way, finished or scheduled - that is a claim about the world, and
    only evidence makes those. The schema is where this is enforced, because a
    field that does not exist cannot be filled in by a persuasive model."""
    assert "status" not in ei.BRIEF_SCHEMA["properties"]
    assert "outcome_dependent" in ei.BRIEF_SCHEMA["properties"]
    assert not hasattr(ei.Brief(), "status")
    assert not hasattr(ei.Brief(), "live_status")

    # And nothing in the module maps a brief onto a status.
    import inspect
    source = inspect.getsource(ei)
    for word in ("in_progress\"", "\"final\"", "\"scheduled\""):
        assert f"status = {word}" not in source, (
            "episode intelligence is assigning an event status")


def test_a_request_being_outcome_dependent_says_nothing_about_the_event():
    """"Tell me how the Chiefs game is going" is outcome-dependent whether the
    game finished an hour ago, is in its third quarter, or kicks off tonight.
    The brief must carry the first and none of the rest."""
    brief = ei.gate(ei.Brief(query="tell me how the chiefs game is going",
                             search_query="chiefs game",
                             intent="update", live_domain="sports"),
                    "tell me how the chiefs game is going")
    assert brief.outcome_dependent is True
    assert not hasattr(brief, "status")
    # The caution it carries is about not knowing, never about knowing.
    joined = " ".join(brief.cautions)
    assert "confirmed yet" in joined
    assert "has started is not an event that has finished" in joined


def test_live_domain_enum_is_the_routing_vocabulary_not_a_copy_of_it():
    """The blocker this replaced, and the shape of it is worth keeping.

    `live_facts.LIVE_DOMAINS` is the routing vocabulary: a source declares one
    of those and `lookup` dispatches on one of those. The schema held a second,
    hand-written copy, and `elections` was added to the first and never the
    second - so a keyless, healthy, registered Polymarket could be configured,
    pass every check on `/api/health`, and never be asked a single question,
    because the one layer that names a domain was structurally unable to say
    that one. Nothing failed; it just silently did not happen.

    So the schema reads the tuple, and this asserts the reading rather than the
    values - a test listing the domains would be the third copy."""
    enum = ei.BRIEF_SCHEMA["properties"]["live_domain"]["enum"]
    assert enum == [""] + list(live_facts.LIVE_DOMAINS)
    # Empty stays first and stays allowed: most questions turn on no live
    # state at all, and a schema that forced a domain would invent one.
    assert enum[0] == ""
    for domain in live_facts.LIVE_DOMAINS:
        assert domain in enum


def test_every_routable_domain_is_explained_to_the_model():
    """An enum value with no instruction behind it is one the model never
    picks. Adding a domain to the tuple is therefore only half of adding it -
    the prompt has to say what question belongs to it."""
    prompt = ei.build_ei_prompt("who is ahead", 3, "", None)
    for domain in live_facts.LIVE_DOMAINS:
        assert f"`{domain}`" in prompt, (
            f"{domain} is routable but the prompt never tells the model when "
            f"to choose it")


def test_ei_may_not_pick_the_in_progress_shape_itself():
    """Choosing it would be EI saying the event is under way, which is the same
    unknowable claim `recap` used to smuggle in pointing the other way. The
    writer reaches that shape from the evidence; EI never hands it over."""
    assert "in_progress" in ei.STRUCTURES
    assert "in_progress" not in ei.PICKABLE_STRUCTURES
    assert "in_progress" not in ei.BRIEF_SCHEMA["properties"]["structure"]["enum"]

    # And a model that names it anyway is mapped back rather than obeyed.
    brief = ei.gate(ei.Brief(query="who won", search_query="who won",
                             intent="recap", structure="in_progress"), "who won")
    assert brief.structure == ei.INTENT_STRUCTURE["recap"]


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
    assert "the final score" in prompt
    assert "a fact about the search, not about the world" in prompt


def test_a_thin_packet_is_never_reported_as_the_world_being_silent():
    """PROBLEMS.md §88's smallest failure and its clearest one.

    This block used to say "say plainly that that part is not yet reported".
    One search missing something is a fact about the search; "not yet reported"
    is a claim about the world, and the two only coincide for things that
    change. Next week's fixture had been public since May, and FAM told a
    listener it "hasn't been pinned down" - and ended the episode on it."""
    plan = dataclasses.replace(plan_episode("chiefs game", 3),
                               evidence="SOURCE 1\nTitle: x\n",
                               thin_on=("the Chiefs' week 2 opponent",))
    prompt = build_prompt(plan)

    # The claim it must no longer make on the strength of one search.
    assert "not yet reported" not in prompt

    # The distinction that replaced it, both halves of it.
    assert "changes" in prompt and "already settled" in prompt

    # And the rule that stops the gap becoming the last thing the listener
    # hears, which is what the system prompt has always said about endings.
    assert "never announce the gap" in prompt.lower()
    assert "end the episode on one of these" in prompt


def test_an_event_under_way_is_a_state_the_prompt_has_a_name_for():
    """The whole of §88 in one assertion.

    The temporal block had two states - not started, and finished-but-unclear -
    and a game in its third quarter is neither. Every story shape available
    needed an outcome, so the writer supplied one: a 27-16 final for a game
    that stood at 21-7."""
    plan = dataclasses.replace(plan_episode("chiefs game", 3),
                               evidence="SOURCE 1\nPublished: 2026-09-14\n")
    prompt = build_prompt(plan)
    assert "not started, under way, finished" in prompt
    assert "A result exists only where a source reports it as a result" in prompt
    assert "say so and say where it stands" in prompt


def test_pregame_evidence_is_named_as_evidence_there_is_no_result():
    """The specific misreading that produced the episode. The packet was made
    entirely of previews - the spread, the projected left tackle, the pass rush
    "expected to be" a test - and every one of those was treated as material
    for a recap rather than as proof the game had not been played."""
    plan = dataclasses.replace(plan_episode("chiefs game", 3),
                               evidence="SOURCE 1\nPublished: 2026-09-14\n")
    prompt = build_prompt(plan)
    for marker in ("odds", "projected line-ups", "how to watch", "expected to"):
        assert marker in prompt, f"{marker!r} is a pregame tell and should be named"
    assert "not thin evidence of an outcome" in prompt


def test_a_contradiction_is_never_resolved_by_inventing_a_reason_for_it():
    """The tell that the episode knew something was wrong and talked itself
    out of it: it read a standings table that its own invented result would
    have changed, and called the disagreement "a rounding artifact of how
    early it is in the season"."""
    plan = dataclasses.replace(plan_episode("chiefs game", 3),
                               evidence="SOURCE 1\nPublished: 2026-09-14\n")
    prompt = build_prompt(plan)
    assert "A contradiction is information; never explain it away" in prompt
    assert "Take the smaller true" in prompt and "reading every time" in prompt


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
