"""The brief on Haiku, given the room and the shape it needs (§266).

`/api/health` read 5 timeouts in 11 briefs (10.14): Haiku at medium effort
writes ~1,500 tokens of thinking and JSON, which at its rate is 6-9 s against
an 8 s ceiling, and a brief past the ceiling searches the raw question with no
window and hands the writer nothing. Pinned here:

* briefs nobody waits on (prefetch, the editions) get their own ceiling;
* the brief is two calls at once, each asking for its own half, and a half
  that fails costs what it must and no more;
* every brief is counted, and timed, by who was waiting;
* the instructions travel with worked examples in one cacheable block;
* a team is never routed as a town's news;
* a request that says "today" or "latest" never searches with no window -
  and that floor never reaches how long the episode stays current.

No key and no network: the client is stubbed at `build_async_client`.
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
import research  # noqa: E402
import script_generator as sg  # noqa: E402

UNDERSTOOD = {
    "intent": "recap",
    "subject": "the Philadelphia Eagles' most recent game",
    "why_now": "",
    "why_now_confidence": "low",
    "must_establish": ["final score"],
    "recency_days": 3,
    "structure": "sports_recap",
    "cautions": [],
    "live_domain": "sports",
    "live_entity": "Philadelphia Eagles",
    "outcome_dependent": True,
    "place": "",
}
WORDED = {
    "search_query": "Philadelphia Eagles game result",
    "search_fallback": "Philadelphia Eagles",
    "search_angles": ["Eagles box score"],
    "search_objective": "The Eagles' game on or about Sunday 11 October 2026.",
    "title": "The Eagles' Latest Game",
    "category": "NFL football",
    "pronounce": [],
}


class _Usage:
    input_tokens = 100
    output_tokens = 50
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


def _reply(payload, stop_reason="end_turn"):
    block = types.SimpleNamespace(type="text", text=json.dumps(payload))
    return types.SimpleNamespace(content=[block], usage=_Usage(),
                                 stop_reason=stop_reason)


def _part_of(kwargs) -> str:
    props = kwargs["output_config"]["format"]["schema"]["properties"]
    if "intent" in props and "search_query" in props:
        return "whole"
    return "understand" if "intent" in props else "search"


@pytest.fixture
def model(monkeypatch):
    """A stubbed Claude that answers each half from its own script."""
    state = {"calls": [], "understand": _reply(UNDERSTOOD),
             "search": _reply(WORDED), "whole": _reply({**UNDERSTOOD, **WORDED}),
             "delay": {}}

    class _Messages:
        async def create(self, **kwargs):
            part = _part_of(kwargs)
            state["calls"].append((part, kwargs))
            await asyncio.sleep(state["delay"].get(part, 0))
            answer = state[part]
            if isinstance(answer, Exception):
                raise answer
            return answer

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


# --------------------------------------------------------------------------
# the settings
# --------------------------------------------------------------------------
def test_the_defaults():
    fresh = config.Settings()
    assert fresh.ei_split is True
    assert fresh.ei_examples is True
    assert fresh.ei_ahead_timeout_seconds == 30.0
    assert fresh.ei_timeout_seconds == 8.0, "the search path's ceiling is unchanged"


def test_env_example_agrees_with_config():
    """A setting is settled only where it is copied (CLAUDE.md)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(root, ".env.example"), encoding="utf-8").read()
    for line in ("\nEI_AHEAD_TIMEOUT_SECONDS=30.0\n", "\nEI_SPLIT=1\n",
                 "\nEI_EXAMPLES=1\n"):
        assert line in text


# --------------------------------------------------------------------------
# two halves
# --------------------------------------------------------------------------
def test_the_halves_ask_for_every_field_exactly_once():
    understand = set(ei.UNDERSTAND_SCHEMA["properties"])
    search = set(ei.SEARCH_SCHEMA["properties"])
    assert not understand & search
    assert understand | search == set(ei.BRIEF_SCHEMA["properties"])
    for schema in (ei.UNDERSTAND_SCHEMA, ei.SEARCH_SCHEMA):
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        for name, definition in schema["properties"].items():
            assert definition is ei.BRIEF_SCHEMA["properties"][name], (
                "a half's field is the whole brief's definition, never a copy")


def test_the_one_call_prompt_is_unchanged_and_each_half_reads_only_its_own():
    whole = ei.build_ei_prompt("did the eagles win", 2, now="Monday")
    assert ei.field_guide() in whole
    for name in ei.FIELD_ENTRIES:
        assert f"- **{name}**" in whole
    for part, fields in ei.PARTS.items():
        prompt = ei.build_ei_prompt("did the eagles win", 2, now="Monday",
                                    fields=fields)
        for name in ei.FIELD_ENTRIES:
            assert (f"- **{name}**" in prompt) == (name in fields), (part, name)


def test_a_split_brief_is_two_calls_at_once_and_one_brief(model):
    brief = asyncio.run(ei.understand("did the eagles win", 2))
    assert sorted(part for part, _ in model["calls"]) == ["search", "understand"]
    assert not brief.degraded
    assert brief.intent == "recap" and brief.live_entity == "Philadelphia Eagles"
    assert brief.search_query == "Philadelphia Eagles game result"
    assert brief.title == "The Eagles' Latest Game"
    assert ei.report()["since_boot"] == {"ok": 1}, "one brief, counted once"
    assert ei.report()["parts_since_boot"] == {"understand": {"ok": 1},
                                               "search": {"ok": 1}}


def test_the_halves_run_side_by_side(model):
    """The brief takes as long as its slower half, not the two added up."""
    model["delay"] = {"understand": 0.3, "search": 0.3}
    asyncio.run(ei.understand("did the eagles win", 2))
    assert ei.report()["seconds_by_path"]["search"]["max"] < 0.55


def test_split_off_sends_the_one_call(model, monkeypatch):
    _with(monkeypatch, ei_split=False)
    brief = asyncio.run(ei.understand("did the eagles win", 2))
    assert [part for part, _ in model["calls"]] == ["whole"]
    assert brief.search_query == "Philadelphia Eagles game result"
    assert ei.report()["parts_since_boot"] == {"whole": {"ok": 1}}


def test_a_failed_understanding_half_degrades_but_keeps_the_searches(model):
    model["understand"] = RuntimeError("boom")
    brief = asyncio.run(ei.understand("did the eagles win", 2))
    assert brief.degraded
    assert "EI call failed" in brief.notes[0]
    assert brief.search_query == "Philadelphia Eagles game result", (
        "a resolved search is worth keeping beside a raw-query brief")
    assert ei.report()["since_boot"] == {"failed": 1}
    assert ei.report()["parts_since_boot"]["search"] == {"ok": 1}


def test_a_failed_search_half_leaves_the_brief_standing(model):
    model["search"] = RuntimeError("boom")
    brief = asyncio.run(ei.understand("did the eagles win", 2))
    assert not brief.degraded
    assert brief.intent == "recap"
    assert brief.search_query == "did the eagles win", "the gate searches the question"
    assert any("search half did not answer" in n for n in brief.notes)
    assert ei.report()["since_boot"] == {"ok": 1}


def test_a_half_cut_off_by_its_ceiling_says_so(model):
    model["understand"] = _reply(UNDERSTOOD, stop_reason="max_tokens")
    brief = asyncio.run(ei.understand("did the eagles win", 2))
    assert brief.degraded and "EI_MAX_TOKENS" in brief.notes[0]
    assert ei.report()["since_boot"] == {"out_of_room": 1}


# --------------------------------------------------------------------------
# who is waiting
# --------------------------------------------------------------------------
def test_a_brief_nobody_waits_on_gets_its_own_ceiling(model, monkeypatch):
    _with(monkeypatch, ei_timeout_seconds=0.05, ei_ahead_timeout_seconds=2.0)
    model["delay"] = {"understand": 0.2, "search": 0.2}
    waiting = asyncio.run(ei.understand("did the eagles win", 2))
    ahead = asyncio.run(ei.understand("did the eagles win", 2, ahead=True))
    assert waiting.degraded and "0.05" in waiting.notes[0]
    assert not ahead.degraded
    report = ei.report()
    assert report["since_boot_by_path"] == {"search": {"timeout": 1},
                                            "ahead": {"ok": 1}}
    assert report["seconds_by_path"]["search"]["n"] == 1
    assert report["seconds_by_path"]["ahead"]["n"] == 1
    assert report["ahead_timeout_seconds"] == 2.0


@pytest.mark.parametrize("flag", ["ahead", "prefetch"])
def test_the_generator_passes_ahead_from_the_notes(model, monkeypatch, flag):
    _with(monkeypatch, ei_timeout_seconds=0.05, ei_ahead_timeout_seconds=2.0)
    model["delay"] = {"understand": 0.2, "search": 0.2}
    monkeypatch.setattr(sg.prefetch, "warm_brief", lambda *a, **k: None)
    generator = sg.ScriptGenerator.__new__(sg.ScriptGenerator)
    notes = sg.ScriptNotes()
    setattr(notes, flag, True)
    plan = asyncio.run(generator.understand(
        sg.plan_episode("did the eagles win", 2), notes))
    assert not plan.brief.degraded


def test_the_editions_mark_their_briefs_ahead():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("daily_edition.py", "trending_bank.py"):
        text = open(os.path.join(root, name), encoding="utf-8").read()
        assert "notes.ahead = True" in text, name


def test_health_reports_the_spread():
    assert ei._spread([]) == {"n": 0}
    spread = ei._spread([1.0, 2.0, 3.0, 4.0, 10.0])
    assert spread == {"n": 5, "median": 3.0, "p90": 10.0, "max": 10.0}


# --------------------------------------------------------------------------
# worked examples, cached
# --------------------------------------------------------------------------
def test_the_instructions_are_one_cacheable_block_with_the_examples(model):
    asyncio.run(ei.understand("did the eagles win", 2))
    systems = [kwargs["system"] for _, kwargs in model["calls"]]
    assert systems[0] == systems[1], "both halves share one cached prefix"
    block = systems[0][0]
    assert block["cache_control"]["type"] == "ephemeral"
    assert block["text"].startswith(ei.EI_SYSTEM)
    assert "<example>" in block["text"]


def test_examples_off_and_cache_off(monkeypatch):
    _with(monkeypatch, ei_examples=False, prompt_cache=False)
    assert ei.ei_system() == ei.EI_SYSTEM


@pytest.mark.parametrize("example", ei.EXAMPLE_BRIEFS, ids=lambda e: e[0])
def test_every_example_is_a_brief_the_schema_and_gate_accept(example):
    """An example that breaks a rule teaches the rule's opposite."""
    query, _now, _minutes, brief = example
    assert set(brief) == set(ei.BRIEF_SCHEMA["properties"])
    props = ei.BRIEF_SCHEMA["properties"]
    for name in ("intent", "structure", "live_domain", "why_now_confidence"):
        assert brief[name] in props[name]["enum"], (query, name)
    built = ei.gate(ei.Brief(query=query, **{
        k: v for k, v in brief.items() if k != "pronounce"}), query)
    assert built.search_query == brief["search_query"], "no example drifts"
    assert not any("shares no subject" in n for n in built.notes)
    assert ei.clean_title(brief["title"], query) == brief["title"]
    if brief["recency_days"] == 0:
        assert brief["intent"] in ("explainer", "teaching", "discovery")


# --------------------------------------------------------------------------
# a team is not a town
# --------------------------------------------------------------------------
def test_a_team_is_never_routed_as_its_citys_news():
    brief = ei.Brief(query="did the eagles win",
                     subject="the Philadelphia Eagles' latest game",
                     search_query="Philadelphia Eagles game result",
                     place="Philadelphia, Pennsylvania, US")
    assert ei.gate(brief, "did the eagles win").place == ""
    assert any("a team, not a town" in n for n in brief.notes)


def test_a_towns_own_question_keeps_its_place():
    brief = ei.Brief(query="what is happening in philadelphia",
                     subject="local news in Philadelphia",
                     search_query="Philadelphia local news",
                     place="Philadelphia, Pennsylvania, US")
    assert ei.gate(brief, "what is happening in philadelphia").place


# --------------------------------------------------------------------------
# the search's window floor
# --------------------------------------------------------------------------
@pytest.mark.parametrize("query, expected", [
    ("what happened with the fed today", research.SEARCH_FLOOR_DAY_DAYS),
    ("who won last night", research.SEARCH_FLOOR_DAY_DAYS),
    ("latest on the writers strike", research.SEARCH_FLOOR_WEEK_DAYS),
    ("how do heat pumps work", 0),
    ("the history of the today show", research.SEARCH_FLOOR_DAY_DAYS),
])
def test_a_degraded_brief_still_searches_inside_a_window(query, expected):
    brief = ei.fallback_brief(query, "test")
    assert research.search_window(brief) == expected


def test_the_brief_s_own_window_wins():
    brief = ei.Brief(query="latest on x", recency_days=14)
    assert research.search_window(brief) == 14


def test_an_example_of_a_kind_of_thing_gets_no_floor():
    brief = ei.Brief(query="today's stoic quote", intent="teaching")
    assert research.search_window(brief) == 0
    assert research.search_window(None) == 0


def test_the_search_uses_the_floor(monkeypatch):
    seen = {}

    def fake_blocking(query, num_results, packet_sources, highlights,
                      search_type, recency_days, *rest):
        seen["days"] = recency_days
        return research.Packet(context="SOURCE 1\n", backend="parallel")

    monkeypatch.setattr(research, "_retrieve_blocking", fake_blocking)
    monkeypatch.setattr(research, "settings", dataclasses.replace(
        research.settings, gdelt_cross_check=False))
    brief = ei.fallback_brief("what did the fed do today", "test")
    asyncio.run(research.retrieve(brief.retrieval, backend="parallel",
                                  brief=brief))
    assert seen["days"] == research.SEARCH_FLOOR_DAY_DAYS
    assert brief.recency_days == 0, (
        "the floor is the search's alone: how long the episode stays current "
        "is never decided by the words of the question ([ttl-from-evidence])")
