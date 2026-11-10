"""What the §501 comparison changed (§507).

Matched examples are off by default (no gain, more repetition); the plan
tells the writer that its landing is the episode's last sentence (the
writer ended where Shakespeare planned in only 35-40% of episodes); and the
comparison gained `none_low`, the writer at low effort, to measure what high
effort buys.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import os

import script_generator as sg
from agents import editor
from agents.editor import EditorAnswer, _Beat
from episode_intelligence import Brief
from script_generator import ScriptNotes, build_prompt, plan_episode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCE = ("SOURCE 1\nTitle: Fed holds\nPublished: 2026-11-02 (yesterday)\n"
            "Source type: established outlet\nKey evidence:\n"
            "The Fed held rates at 4.25 percent. Two members dissented.\n")


def test_matched_examples_are_off_by_default():
    assert sg.settings.example_matching is False
    plan = dataclasses.replace(plan_episode("how are the eagles doing", 1.5),
                               brief=Brief(category_path=["sports"]))
    assert "closest to this kind of story" not in build_prompt(plan)


def test_the_landing_is_the_last_sentence():
    plan = dataclasses.replace(plan_episode("what did the fed do", 1.5), search=False,
                               evidence=EVIDENCE)
    sources = editor.number_packets(plan)
    answer = EditorAnswer(answers="x", summary=["P1.1"], opening=["P1.1"],
                          beats=[_Beat(purpose="the split", sentences=["P1.2"])],
                          landing=["P1.2"], thin=False, missing=[])
    built, _ = editor.to_outline(answer, sources)
    block = editor.writer_block(built, "x", sources)
    assert "What it lands on is your last sentence: decide that sentence first" in block
    head, _, tail = block.partition("Land on - your last sentence tells this, and the episode "
                                    "ends there:")
    assert tail.lstrip().startswith("- Two members dissented.")


def _eval():
    spec = importlib.util.spec_from_file_location(
        "editor_eval", os.path.join(ROOT, "tools", "editor_eval.py"))
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def test_none_low_writes_at_low_effort_and_the_rest_at_high():
    tool = _eval()
    assert "none_low" in tool.ARMS and "none_low" not in tool.DEFAULT_ARMS
    edition = ScriptNotes(edition=True, ahead=True)
    with tool._Settings("none_low"):
        assert sg.writer_for(edition)[1] == "low"
    with tool._Settings("none"):
        assert sg.writer_for(edition)[1] == "high"
    assert sg.writer_for(edition)[1] == "high"          # taken back after
