"""The wait in front of the first word, broken into the steps that make it.

`claude_ttft` is marked from `claude_start`, which is before the brief, so on
a researched episode it used to be brief + retrieval + the writer's thinking
in one number - and "some episodes take 45 seconds" could not be traced to any
one of them from a production log. These run the real pipeline and the real
`ScriptGenerator` with every provider stubbed at a known delay, and check that
each step is marked, in order, and that the parts add up to the first audio.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest

import episode_intelligence
import live_facts
import pipeline as pipeline_mod
import research as research_mod
from episode_intelligence import Brief
from episode_marks import EpisodeMarks
from pipeline import GenerationStats, PodcastPipeline
from script_generator import ScriptGenerator, plan_episode
from tts import DebugEngine

BRIEF_DELAY = 0.20
RETRIEVAL_DELAY = 0.10
WRITER_THINKING = 0.15

SCRIPT = ("The committee held rates where they were on Wednesday. "
          "Two officials dissented, both wanting a cut. "
          "The statement kept the door open for September.")


class _Stream:
    def __init__(self):
        self.final = type("Final", (), {"stop_reason": "end_turn",
                                        "usage": None, "content": []})()

    @property
    def text_stream(self):
        async def deltas():
            await asyncio.sleep(WRITER_THINKING)
            for word in SCRIPT.split(" "):
                yield word + " "
        return deltas()

    async def get_final_message(self):
        return self.final


class _Context:
    async def __aenter__(self):
        return _Stream()

    async def __aexit__(self, *exc):
        return False


class _Messages:
    def stream(self, **kwargs):
        return _Context()


class _Client:
    messages = _Messages()


@pytest.fixture
def stubbed(monkeypatch):
    async def understand(query, minutes, context="", notes=None, now=None):
        await asyncio.sleep(BRIEF_DELAY)
        return Brief(query=query, intent="recap", subject="the Fed",
                     search_query="Federal Reserve decision", recency_days=3)

    async def retrieve(query, backend=None, brief=None, **overrides):
        await asyncio.sleep(RETRIEVAL_DELAY)
        return research_mod.Packet(context="Reuters, yesterday: rates held.",
                                   backend="exa", searches=1)

    async def lookup(brief, notes=None):
        return None

    monkeypatch.setattr(episode_intelligence, "understand", understand)
    monkeypatch.setattr(research_mod, "retrieve", retrieve)
    monkeypatch.setattr(live_facts, "lookup", lookup)

    generator = ScriptGenerator.__new__(ScriptGenerator)
    generator.client = _Client()
    return PodcastPipeline(generator=generator, engine=DebugEngine(), cache=None)


def _run(pipe) -> GenerationStats:
    stats = GenerationStats()
    plan = dataclasses.replace(
        plan_episode("what happened with the fed yesterday", 1, search=True))

    async def drain():
        async for _ in pipe.stream_pcm(plan, stats):
            pass

    asyncio.run(drain())
    return stats


def test_every_step_before_the_first_word_is_marked(stubbed):
    marks = _run(stubbed).marks
    for name in ("claude_start", "brief_start", "brief_ready",
                 "evidence_start", "first_rung_ready", "retrieval_ready",
                 "evidence_ready", "writer_request", "claude_first_token",
                 "first_sentence", "first_tts_complete"):
        assert marks.at(name) is not None, name


def test_the_steps_happen_in_the_order_a_listener_waits_through_them(stubbed):
    marks = _run(stubbed).marks
    order = ["claude_start", "brief_start", "brief_ready", "evidence_start",
             "evidence_ready", "writer_request", "claude_first_token",
             "first_sentence", "first_tts_start", "first_tts_complete"]
    times = [marks.at(name) for name in order]
    assert times == sorted(times)


def test_each_stage_is_the_step_it_names(stubbed):
    """The stubbed delays come back out of the right labels."""
    stages = _run(stubbed).marks.stages()
    assert stages["brief"] == pytest.approx(BRIEF_DELAY, abs=0.08)
    assert stages["evidence"] == pytest.approx(RETRIEVAL_DELAY, abs=0.08)
    assert stages["writer thinking"] == pytest.approx(WRITER_THINKING, abs=0.08)


def test_the_parts_add_up_to_the_first_audio(stubbed):
    stages = _run(stubbed).marks.stages()
    total = stages.pop("first audio")
    assert sum(stages.values()) == pytest.approx(total, abs=1e-6)
    assert stages["unaccounted"] < 0.05


def test_the_summary_splits_what_claude_ttft_used_to_swallow(stubbed):
    summary = _run(stubbed).marks.summary()
    parts = (summary["brief_seconds"] + summary["evidence_seconds"]
             + summary["writer_ttft"])
    assert parts <= summary["claude_ttft"] + 1e-6
    assert summary["claude_ttft"] - parts < 0.05


def test_an_absent_stage_is_left_out_rather_than_zero():
    """A cache hit has no brief; "0.00s brief" would read as a free one."""
    marks = EpisodeMarks()
    marks.mark("claude_start")
    marks.mark("first_sentence")
    marks.mark("first_tts_start")
    marks.mark("first_tts_complete")
    stages = marks.stages()
    assert "brief" not in stages and "writer thinking" not in stages
    assert "first audio" in stages
    assert "brief" not in marks.stage_line()


def test_the_log_carries_the_stages(stubbed):
    doc = _run(stubbed).marks.to_dict()
    assert json.loads(json.dumps(doc))["stages"]["brief"] > 0


def test_the_probe_tool_reads_the_same_critical_path():
    import tools.latency_probe as probe

    assert probe._critical_path() is EpisodeMarks.CRITICAL_PATH
