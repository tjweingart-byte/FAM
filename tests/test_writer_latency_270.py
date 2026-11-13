"""The writer's wait, shortened where it can be without a rule giving way (§270).

* **The plan of the piece as the brief's third part**: from the question
  alone, beside the other two, never holding a searched brief more than its
  grace, never stating a fact, and read by the writer as a shape to check
  against the evidence - the opening still decided last.
* **The writer's cache warmed at the tap**: only on the search path, only
  when cold, never awaited, matched to the writer's own request.
* **Pronunciations where the voice needs them**: each line just before the
  sentence that first says the name, not all of them in front of the first
  word, and the brief's names never asked for twice.

No key and no network: every client is a stub.
"""
from __future__ import annotations

import asyncio
import collections
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
import writer_warm  # noqa: E402

UNDERSTOOD = {
    "intent": "recap", "subject": "the Philadelphia Eagles' most recent game",
    "why_now": "", "why_now_confidence": "low", "must_establish": ["final score"],
    "recency_days": 3, "structure": "sports_recap", "cautions": [],
    "live_domain": "sports", "live_entity": "Philadelphia Eagles",
    "outcome_dependent": True, "place": "",
}
WORDED = {
    "search_query": "Philadelphia Eagles game result",
    "search_fallback": "Philadelphia Eagles", "search_angles": [],
    "search_objective": "The Eagles' most recent game.",
    "title": "The Eagles' Latest Game", "category": "NFL football", "pronounce": [],
}
OUTLINED = {
    "beats": [
        {"does": "what the result of their latest game was",
         "needs": ["final score", "opponent"]},
        {"does": "the play that decided it", "needs": ["key moment"]},
        {"does": "the Eagles won 24-17 on a late field goal", "needs": []},
    ],
    "opening_who": "the team", "opening_what": "the result of their latest game",
    "opening_when": "the day it was played on 11 October",
    "ending": "the figure that settles the question",
}


def _reply(payload, stop="end_turn"):
    block = types.SimpleNamespace(type="text", text=json.dumps(payload))
    return types.SimpleNamespace(content=[block], stop_reason=stop, usage=None)


def _part(kwargs):
    props = kwargs["output_config"]["format"]["schema"]["properties"]
    if "beats" in props:
        return "outline"
    return "understand" if "intent" in props else "search"


@pytest.fixture
def model(monkeypatch):
    state = {"calls": [], "understand": UNDERSTOOD, "search": WORDED,
             "outline": OUTLINED, "delay": {}}

    class _Messages:
        async def create(self, **kwargs):
            part = _part(kwargs)
            state["calls"].append((part, kwargs))
            await asyncio.sleep(state["delay"].get(part, 0))
            return _reply(state[part])

    monkeypatch.setattr(ei, "build_async_client",
                        lambda key=None: types.SimpleNamespace(messages=_Messages()))
    monkeypatch.setattr(ei.credentials, "active", lambda name: "sk-test")
    monkeypatch.setattr(ei, "_OUTCOMES", collections.Counter())
    monkeypatch.setattr(ei, "_BY_PATH", {"search": collections.Counter(),
                                         "ahead": collections.Counter()})
    monkeypatch.setattr(ei, "_SECONDS", {
        "search": collections.deque(maxlen=ei.SECONDS_KEPT),
        "ahead": collections.deque(maxlen=ei.SECONDS_KEPT)})
    monkeypatch.setattr(ei, "_PART_OUTCOMES", {})
    return state


def _with(monkeypatch, **changes):
    monkeypatch.setattr(ei, "settings", dataclasses.replace(ei.settings, **changes))


# -- the outline ------------------------------------------------------------
def test_the_defaults_and_the_example_file_agree():
    fresh = config.Settings()
    assert fresh.ei_outline is True and fresh.ei_outline_grace_seconds == 0.5
    assert fresh.writer_warm is True and fresh.evidence_digest is False
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(root, ".env.example"), encoding="utf-8").read()
    for line in ("\nEI_OUTLINE=1\n", "\nEI_OUTLINE_GRACE_SECONDS=0.5\n",
                 "\nWRITER_WARM=1\n", "\nEVIDENCE_DIGEST=0\n"):
        assert line in text


def test_the_outline_is_a_third_part_asked_at_the_same_moment(model):
    brief = asyncio.run(ei.understand("did the eagles win", 2))
    assert sorted(p for p, _ in model["calls"]) == ["outline", "search", "understand"]
    outline_call = next(k for p, k in model["calls"] if p == "outline")
    prompt = outline_call["messages"][0]["content"]
    assert "State no facts" in prompt and "<request>did the eagles win</request>" in prompt
    # It asks for its own fields and none of the brief's.
    assert "- **intent**" not in prompt and "- **search_query**" not in prompt
    # The same cached instructions as the other two parts.
    assert outline_call["system"] == model["calls"][0][1]["system"]
    assert not brief.degraded
    assert brief.outline_beats[0] == {"does": "what the result of their latest game was",
                                      "needs": ["final score", "opponent"]}


def test_a_line_that_states_a_fact_never_reaches_the_writer(model):
    brief = asyncio.run(ei.understand("did the eagles win", 2))
    texts = [b["does"] for b in brief.outline_beats]
    assert "the Eagles won 24-17 on a late field goal" not in texts
    assert "when" not in brief.outline_opening, "a date in a slot is a fact"
    assert brief.outline_opening == {"who": "the team",
                                     "what": "the result of their latest game"}


def test_the_number_of_beats_follows_the_length():
    brief = ei.Brief(query="q")
    many = {"beats": [{"does": f"part {chr(97 + i)}", "needs": []} for i in range(12)],
            "opening_who": "", "opening_what": "", "opening_when": "", "ending": ""}
    ei._outline_from(brief, many, 1)
    assert len(brief.outline_beats) == ei.max_beats(1) == 3
    ei._outline_from(brief, many, 5)
    assert len(brief.outline_beats) == ei.max_beats(5) == 7


def test_a_slow_outline_never_holds_a_searched_brief(model, monkeypatch):
    _with(monkeypatch, ei_outline_grace_seconds=0.05)
    model["delay"]["outline"] = 2.0
    import time

    started = time.monotonic()
    brief = asyncio.run(ei.understand("did the eagles win", 2))
    assert time.monotonic() - started < 1.0
    assert not brief.degraded and brief.outline_beats == []
    assert any("outline did not answer (late)" in n for n in brief.notes)
    assert ei.report()["parts_since_boot"]["outline"] == {"late": 1}


def test_ahead_the_outline_has_the_whole_ceiling(model, monkeypatch):
    _with(monkeypatch, ei_outline_grace_seconds=0.01)
    model["delay"]["outline"] = 0.2
    brief = asyncio.run(ei.understand("did the eagles win", 2, ahead=True))
    assert brief.outline_beats, "nobody is waiting, so it is waited for"


def test_off_it_is_the_two_parts_of_268(model, monkeypatch):
    _with(monkeypatch, ei_outline=False)
    asyncio.run(ei.understand("did the eagles win", 2))
    assert sorted(p for p, _ in model["calls"]) == ["search", "understand"]


def test_the_one_call_brief_is_unchanged(model, monkeypatch):
    _with(monkeypatch, ei_split=False)
    model["whole"] = {**UNDERSTOOD, **WORDED}

    def whole(kwargs):
        return "whole"

    monkeypatch.setattr(sys.modules[__name__], "_part", whole)
    asyncio.run(ei.understand("did the eagles win", 2))
    assert [p for p, _ in model["calls"]] == ["whole"]
    assert "beats" not in ei.BRIEF_SCHEMA["properties"]


def test_the_writer_reads_it_as_a_shape_and_still_opens_last():
    brief = ei.Brief(query="did the eagles win", intent="recap",
                     subject="the Eagles' latest game",
                     outline_beats=[{"does": "what the result was",
                                     "needs": ["final score"]}],
                     outline_opening={"who": "the team"},
                     outline_ending="the figure that settles it")
    block = ei.build_brief_block(brief, 2)
    assert "- what the result was (needs: final score)" in block
    assert "pin down: who - the team" in block
    assert "It should land on: the figure that settles it." in block
    assert "Decide the opening last" in block and "not evidence" in block
    # Without an outline the block is what it was.
    plain = ei.build_brief_block(dataclasses.replace(brief, outline_beats=[]), 2)
    assert "A plan for the piece" not in plain


# -- the writer's cache, warmed at the tap ----------------------------------
class _Recorder:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
        self.messages = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("no key")
        return types.SimpleNamespace(usage=None)


def _settle():
    async def run():
        await asyncio.sleep(0)
        if writer_warm._PENDING:
            await asyncio.gather(*list(writer_warm._PENDING))
    return run


def test_a_cold_writer_is_warmed_once_with_its_own_request(monkeypatch):
    monkeypatch.setattr(writer_warm, "_LAST", {})
    client = _Recorder()

    async def tap():
        first = writer_warm.warm_if_cold(client, sg.ScriptNotes())
        second = writer_warm.warm_if_cold(client, sg.ScriptNotes())
        await _settle()()
        return first, second

    first, second = asyncio.run(tap())
    assert (first, second) == (True, False), "two taps a second apart, one warm-up"
    sent = client.calls[0]
    assert sent["max_tokens"] == 0
    assert sent["model"] == sg.writer_for(None)[0]
    assert sent["output_config"] == {"effort": config.settings.effort}
    assert sent["system"] == sg.writer_system()
    assert "format" not in sent["output_config"] and "stream" not in sent


def test_nobody_waiting_means_no_warm_up(monkeypatch):
    monkeypatch.setattr(writer_warm, "_LAST", {})
    client = _Recorder()
    for flag in ("ahead", "prefetch", "edition"):
        notes = sg.ScriptNotes()
        setattr(notes, flag, True)
        assert writer_warm.warm_if_cold(client, notes) is False
    assert client.calls == []


def test_a_warm_cache_is_left_alone_and_a_failure_is_forgotten(monkeypatch):
    monkeypatch.setattr(writer_warm, "_LAST", {})
    writer_warm.note_used(sg.writer_for(None)[0], config.settings.effort)
    assert not writer_warm.is_cold(sg.writer_for(None)[0], config.settings.effort)
    assert writer_warm.is_cold(sg.writer_for(None)[0], config.settings.effort,
                               now=10_000_000.0)
    monkeypatch.setattr(writer_warm, "_LAST", {})
    client = _Recorder(fail=True)

    async def tap():
        writer_warm.warm_if_cold(client, sg.ScriptNotes())
        await _settle()()

    asyncio.run(tap())
    assert writer_warm.is_cold(sg.writer_for(None)[0], config.settings.effort), (
        "a warm-up that failed warmed nothing, so the next tap tries again")


def test_off_without_the_prompt_cache(monkeypatch):
    monkeypatch.setattr(writer_warm, "_LAST", {})
    monkeypatch.setattr(writer_warm, "settings",
                        dataclasses.replace(config.settings, prompt_cache=False))
    assert writer_warm.warm_if_cold(_Recorder(), sg.ScriptNotes()) is False


# -- pronunciations where the voice needs them ------------------------------
def test_a_name_is_respelled_just_before_the_sentence_that_says_it():
    plan = sg.plan_episode("who is Sagapolutele", 2)
    prompt = sg.build_prompt(plan)
    assert "immediately before the first" in prompt
    assert "just\nbefore the later sentence" in prompt or "just before the later" in prompt


def test_the_reader_takes_a_line_out_in_the_middle_of_the_script():
    plan = sg.plan_episode("who is Sagapolutele", 2)
    notes = sg.ScriptNotes()
    reader = sg._ScriptReader(plan, notes)
    out = reader.feed("The first sentence says nothing hard at all. \n")
    out += reader.feed("<<SAY: Sagapolutele = sah-gah-poh-loo-TEH-leh>>\n")
    out += reader.feed("Then Sagapolutele threw for three touchdowns. ")
    out += reader.finish()
    assert out == ["The first sentence says nothing hard at all.",
                   "Then Sagapolutele threw for three touchdowns."]
    assert ("Sagapolutele", "sah-gah-poh-loo-TEH-leh") in notes.pronunciations


def test_the_briefs_names_are_never_asked_for_twice():
    plan = sg.plan_episode("who is Sagapolutele", 2)
    brief = ei.Brief(query=plan.query, subject="Jayden Sagapolutele",
                     pronounce=[{"name": "Sagapolutele",
                                 "say": "sah-gah-poh-loo-TEH-leh"}])
    prompt = sg.build_prompt(dataclasses.replace(plan, brief=brief))
    assert "already have a pronunciation on file" in prompt
    tail = prompt.split("already have a pronunciation on file", 1)[1][:200]
    assert "Sagapolutele" in tail


def test_a_degraded_brief_does_not_wait_for_the_plan(model, monkeypatch):
    """Review, §273: a degraded brief shows no plan, so it is not waited for."""
    _with(monkeypatch, ei_outline_grace_seconds=5.0)
    model["delay"]["outline"] = 3.0
    import time

    original = ei._ask

    async def ask(client, part, *rest, **kw):
        if part == "understand":
            return "failed", None, "boom"
        return await original(client, part, *rest, **kw)

    monkeypatch.setattr(ei, "_ask", ask)
    started = time.monotonic()
    brief = asyncio.run(ei.understand("did the eagles win", 2, ahead=True))
    assert brief.degraded and time.monotonic() - started < 1.0


def test_a_listener_who_leaves_takes_the_plan_with_them(model, monkeypatch):
    """Review, §273: cancelling the brief cancels the plan, rather than a
    shield keeping it running to its own ceiling."""
    _with(monkeypatch, ei_outline_grace_seconds=5.0)
    model["delay"]["outline"] = 3.0
    seen = {}
    original = ei._outline_by

    async def watched(task, *a):
        seen["task"] = task
        return await original(task, *a)

    monkeypatch.setattr(ei, "_outline_by", watched)

    async def leave():
        brief = asyncio.ensure_future(ei.understand("did the eagles win", 2))
        await asyncio.sleep(0.2)
        brief.cancel()
        try:
            await brief
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0)
        return seen["task"]

    task = asyncio.run(leave())
    assert task.cancelled() or task.done()
