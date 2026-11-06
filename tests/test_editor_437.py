"""Shakespeare's outline (Phase 3, piece 3, §437).

Extractive by construction: the packets are numbered, his answer names
numbers and a beat's purpose only, code checks every number and builds the
writer's block from the sources' own sentences. Ahead of the tap only, for
now. Off, late or empty, the writer reads the packets as it does today.
"""
from __future__ import annotations

import asyncio
import dataclasses

import pytest

import script_generator as sg
from agents import editor, models, netenyahu
from agents.editor import EditorAnswer, _Beat
from script_generator import ScriptNotes, build_prompt, plan_episode

ARCHIVE = "Source type: earlier reporting from FAM's archive - dated background, never a current state"
EVIDENCE = f"""SOURCE 1
Title: Fed holds rates steady
Published: 2026-11-02 (yesterday)
Source type: wire service, paper of record or primary source
Key evidence:
The Federal Reserve held its benchmark rate at 4.25 percent. Powell said the committee would wait for more data.
Two members dissented.

SOURCE 2
Title: Markets after the decision
Published: 2026-11-02 (yesterday)
Source type: established outlet
Key evidence:
Stocks rose after the announcement.

SOURCE 3
Title: The Fed cut rates in 2025
Published: 2025-09-17 (a year ago)
{ARCHIVE}
Key evidence:
The Fed cut rates by a quarter point in September 2025.
"""


@pytest.fixture(autouse=True)
def fresh():
    editor.reset()
    yield
    editor.reset()


def plan(evidence=EVIDENCE):
    return dataclasses.replace(plan_episode("what did the fed decide", 1.5),
                               search=False, evidence=evidence)


def test_the_packets_are_numbered_by_sentence():
    sources = editor.number_packets(plan())
    assert [s.prefix for s in sources] == ["P1", "P2", "A1"]
    assert sources[0].sentences == [
        "The Federal Reserve held its benchmark rate at 4.25 percent.",
        "Powell said the committee would wait for more data.",
        "Two members dissented."]
    plugin, archive = editor.packets_text(sources)
    assert "[P1.2] Powell said the committee would wait for more data." in plugin
    assert "[A1.1] The Fed cut rates by a quarter point in September 2025." in archive
    assert "A1" not in plugin and "P1" not in archive


def answer(**kw):
    base = dict(answers="what the Fed did", summary=["P1.1", "P1.2"],
                opening=["P1.1"],
                beats=[_Beat(purpose="the decision", sentences=["P1.1", "P1.3"]),
                       _Beat(purpose="how it got here", sentences=["A1.1"]),
                       _Beat(purpose="the reaction", sentences=["P2.1"])],
                landing=["P2.1"], thin=False, missing=[])
    base.update(kw)
    return EditorAnswer(**base)


def test_every_number_is_checked_in_code():
    sources = editor.number_packets(plan())
    built, report = editor.to_outline(answer(
        summary=["P1.1", "P1.9", "P7.1", "Q1.1", "the Fed held"],
        beats=[_Beat(purpose="the decision", sentences=["P1.1", "P1.99"]),
               _Beat(purpose="a beat with nothing", sentences=["P9.1"])]), sources)
    assert len(built.summary) == 1 and len(built.beats) == 1
    assert report == {"bad_number": 6, "empty_beat": 1}


def test_a_now_beat_on_the_archive_alone_is_dropped():
    sources = editor.number_packets(plan())
    built, report = editor.to_outline(answer(beats=[
        _Beat(purpose="where rates stand now", sentences=["A1.1"]),
        _Beat(purpose="how it got here", sentences=["A1.1"])]), sources)
    assert [b.purpose for b in built.beats] == ["how it got here"]
    assert report == {"now_on_archive": 1}


def test_the_writer_reads_the_sources_own_sentences():
    sources = editor.number_packets(plan())
    built, _ = editor.to_outline(answer(missing=["the vote count"]), sources)
    block = editor.writer_block(built, "what the Fed did", sources)
    assert "<plan>" in block and "</plan>" in block
    assert "- The Federal Reserve held its benchmark rate at 4.25 percent." in block
    assert "Part 2 (history): how it got here" in block
    assert "(published 2025-09-17 (a year ago))" in block
    assert "never announce it" in block and "the vote count" in block
    # His wording never stands in for a source's: no number reaches the writer.
    assert "P1.1" not in block and "A1.1" not in block


def test_no_beats_no_block():
    sources = editor.number_packets(plan())
    built, _ = editor.to_outline(answer(beats=[]), sources)
    assert editor.writer_block(built, "x", sources) == ""


def _ask(status, value=None):
    async def ask(request, notes=None, client=None):
        ask.request = request
        return models.AgentResult(status, "editor", "outline", value=value,
                                  model="claude-sonnet-5-5")
    return ask


def test_the_call_is_bounded_and_carries_the_playbook_examples(monkeypatch):
    ask = _ask(models.OK, answer())
    monkeypatch.setattr(models, "ask", ask)
    notes = ScriptNotes()
    block = asyncio.run(editor.outline(plan(), notes))
    request = ask.request
    assert request.agent == "editor" and request.rule_book == "shakespeare.md"
    assert request.model_setting == "editor_model" and request.surface == "ahead"
    assert request.effort == "high" and request.timeout > 0 and request.max_tokens > 0
    tags = dict(request.material)
    assert "[P1.1]" in tags["evidence"] and "[A1.1]" in tags["archive"]
    assert "--- 2-fed-raises-rates ---" in tags["teaching"]
    assert block.startswith("\nAn editor has read the sources")
    assert notes.editor["beats"] == 3
    assert editor.SINCE_BOOT["planned"] == 1


@pytest.mark.parametrize("status", [models.OFF, models.TIMEOUT, models.HELD,
                                    models.INVALID, models.FAILED])
def test_anything_but_an_answer_leaves_the_writer_as_today(monkeypatch, status):
    monkeypatch.setattr(models, "ask", _ask(status))
    assert asyncio.run(editor.outline(plan())) == ""


def test_no_packets_no_call(monkeypatch):
    ask = _ask(models.OK, answer())
    monkeypatch.setattr(models, "ask", ask)
    assert asyncio.run(editor.outline(plan(evidence=""))) == ""
    assert not hasattr(ask, "request")


def test_the_prompt_is_byte_for_byte_without_him():
    p = plan()
    assert build_prompt(p) == build_prompt(dataclasses.replace(p, editor_outline=""))
    with_plan = build_prompt(dataclasses.replace(p, editor_outline="\n<plan>x</plan>\n"))
    assert with_plan.index("</evidence>") < with_plan.index("<plan>x</plan>")


def _outline_runs(monkeypatch, roles, ahead=True, prefetch=False):
    called = []

    async def outline(p, notes=None, **kw):
        called.append(kw)
        return "\n<plan>planned</plan>\n"

    monkeypatch.setattr(editor, "outline", outline)
    notes = ScriptNotes()
    notes.ahead, notes.prefetch = ahead, prefetch
    notes.agents = netenyahu.EpisodeRun(tuple(roles), {})
    out = asyncio.run(sg._with_editor(plan(), notes))
    return out, called


def test_he_plans_only_ahead_of_the_tap_and_only_when_he_works(monkeypatch):
    out, called = _outline_runs(monkeypatch, ("editor",))
    assert out.editor_outline == "\n<plan>planned</plan>\n" and called == [{"ahead": True}]
    for roles, ahead, prefetch in ((("editor",), False, False),   # searchFAM: not yet
                                   (("editor",), True, True),     # a prefetch warm
                                   ((), True, False)):            # he is off
        out, called = _outline_runs(monkeypatch, roles, ahead, prefetch)
        assert out.editor_outline == "" and called == []
