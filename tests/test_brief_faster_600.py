"""§600: the framing apart from the routing, finished beside the research,
and a second identical call when a brief part is slow on searchFAM."""
from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import episode_intelligence as ei  # noqa: E402
from tests.test_ei_split_268 import UNDERSTOOD, _reply, _with, model  # noqa: E402,F401

FRAMED = {"why_now": "an NFL Sunday has just been played",
          "why_now_confidence": "high", "structure": "sports_recap",
          "cautions": ["the game may not have finished"]}


# --------------------------------------------------------------------------
# the framing apart
# --------------------------------------------------------------------------
def test_defaults():
    fresh = config.Settings()
    assert fresh.ei_frame_apart is True and fresh.ei_hedge_seconds == 6.0


def test_the_routing_asks_no_framing_and_the_framing_only_it(model):
    asyncio.run(ei.understand("did the eagles win", 2))
    by_part = {part: kw for part, kw in model["calls"]}
    route = by_part["understand"]["output_config"]["format"]["schema"]["properties"]
    frame = by_part["frame"]["output_config"]["format"]["schema"]["properties"]
    assert not set(ei.FRAME_FIELDS) & set(route)
    assert set(frame) == {"why_now", "why_now_confidence", "structure", "cautions"}
    for name in ei.FRAME_FIELDS:
        assert f"- **{name}**" in by_part["frame"]["messages"][0]["content"]
        assert f"- **{name}**" not in by_part["understand"]["messages"][0]["content"]


def test_a_whole_brief_has_the_framing_merged(model):
    model["frame"] = _reply(FRAMED)
    brief = asyncio.run(ei.understand("did the eagles win", 2))
    assert brief.framing_task is None
    assert brief.structure == UNDERSTOOD["structure"]
    assert brief.why_now == FRAMED["why_now"] and brief.why_now_confidence == "high"
    # The result caution code puts first stays first; the model's follow.
    assert "confirmed yet" in brief.cautions[0]
    assert FRAMED["cautions"][0] in brief.cautions


def test_the_search_does_not_wait_for_the_framing(model):
    """The point: a slow framing holds nothing before the evidence."""
    model["delay"] = {"frame": 0.5}
    began = time.monotonic()
    brief = asyncio.run(_deferred_then_finished(model))
    assert brief["understood_after"] < 0.4, "the routing waited for the framing"
    assert brief["brief"].structure == UNDERSTOOD["structure"]
    assert time.monotonic() - began >= 0.5


async def _deferred_then_finished(model):
    began = time.monotonic()
    brief = await ei.understand("did the eagles win", 2, defer_framing=True)
    understood_after = time.monotonic() - began
    assert brief.framing_task is not None
    # Until it is in: the intent's shape, no why-now, the code's cautions.
    assert brief.structure == ei.INTENT_STRUCTURE[brief.intent]
    assert brief.why_now_confidence == "low"
    assert not brief.notes or not any("structure" in n for n in brief.notes)
    await ei.finish(brief)
    assert brief.framing_task is None
    return {"brief": brief, "understood_after": understood_after}


def test_ahead_never_defers(model):
    model["frame"] = _reply(FRAMED)
    brief = asyncio.run(ei.understand("did the eagles win", 2, ahead=True,
                                      defer_framing=True))
    assert brief.framing_task is None and brief.why_now


def test_a_framing_that_fails_never_degrades_the_brief(model):
    model["frame"] = RuntimeError("overloaded")
    brief = asyncio.run(_finished(model))
    assert not brief.degraded
    assert brief.structure == ei.INTENT_STRUCTURE[brief.intent]
    assert any("framing did not answer" in n for n in brief.notes)
    assert "confirmed yet" in brief.cautions[0], "code's caution kept"


async def _finished(model):
    brief = await ei.understand("did the eagles win", 2, defer_framing=True)
    return await ei.finish(brief)


def test_abandon_cancels_a_framing_nobody_reads(model):
    model["delay"] = {"frame": 5}

    async def go():
        brief = await ei.understand("did the eagles win", 2, defer_framing=True)
        task = brief.framing_task
        ei.abandon(brief)
        await asyncio.sleep(0)
        return task

    task = asyncio.run(go())
    assert task.cancelled() or task.done()


def test_framing_together_restores_the_one_understanding_part(model, monkeypatch):
    _with(monkeypatch, ei_frame_apart=False)
    brief = asyncio.run(ei.understand("did the eagles win", 2, defer_framing=True))
    assert "frame" not in [p for p, _ in model["calls"]]
    assert brief.framing_task is None and brief.structure == UNDERSTOOD["structure"]
    props = model["calls"][0][1]["output_config"]["format"]["schema"]["properties"]
    assert "why_now" in props or model["calls"][0][0] != "understand"


def test_as_dict_never_carries_the_task(model):
    async def go():
        brief = await ei.understand("did the eagles win", 2, defer_framing=True)
        out = brief.as_dict()
        await ei.finish(brief)
        return out
    out = asyncio.run(go())
    assert "framing_task" not in out


def test_prepare_awaits_the_framing_before_the_writer():
    import inspect

    import script_generator as sg

    source = inspect.getsource(sg.ScriptGenerator._prepare)
    finish = source.index("episode_intelligence.finish(plan.brief)")
    assert source.index('_mark(notes, "evidence_ready")') < finish
    assert finish < source.index("return await _with_editor(plan, notes)")
    assert "notes.framing_later = True" in source
    assert "episode_intelligence.abandon(" in inspect.getsource(sg.ScriptGenerator.prepare)


# --------------------------------------------------------------------------
# the second call
# --------------------------------------------------------------------------
def test_one_call_when_the_first_is_quick():
    calls = []

    async def make():
        calls.append(1)
        return "first"

    assert asyncio.run(ei._hedged(make, 0.2, "t")) == "first" and len(calls) == 1


def test_a_slow_first_call_is_overtaken_by_the_second():
    ei._HEDGES.clear()
    calls = []

    async def make():
        calls.append(1)
        await asyncio.sleep(1.0 if len(calls) == 1 else 0.01)
        return f"call {len(calls)}"

    began = time.monotonic()
    answer = asyncio.run(ei._hedged(make, 0.05, "understand"))
    assert answer == "call 2" and time.monotonic() - began < 0.5
    assert ei._HEDGES["understand"] == {"started": 1, "second_won": 1}


def test_one_failing_waits_for_the_other_and_both_failing_raises():
    async def make_one_fails():
        make_one_fails.n += 1
        if make_one_fails.n == 1:
            await asyncio.sleep(0.1)
            raise RuntimeError("first")
        await asyncio.sleep(0.2)
        return "second"
    make_one_fails.n = 0
    assert asyncio.run(ei._hedged(make_one_fails, 0.05, "t")) == "second"

    async def both_fail():
        await asyncio.sleep(0.1)
        raise RuntimeError("down")
    import pytest
    with pytest.raises(RuntimeError):
        asyncio.run(ei._hedged(both_fail, 0.05, "t"))


def test_hedged_only_where_a_listener_waits(model, monkeypatch):
    _with(monkeypatch, ei_hedge_seconds=0.05)
    model["delay"] = {"understand": 0.2}
    ei._HEDGES.clear()
    asyncio.run(ei.understand("did the eagles win", 2, ahead=True))
    assert not ei._HEDGES, "ahead nobody waits: one call"
    asyncio.run(ei.understand("did the eagles win", 2))
    assert ei._HEDGES.get("understand", {}).get("started") == 1
    assert "outline" not in ei._HEDGES
    assert "hedges" in ei.report() and ei.report()["frame_apart"] is True
