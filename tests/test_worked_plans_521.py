"""Shakespeare's worked plans (§521, item 3).

`examples/` showed him finished episodes and never a plan. Each file in
`examples/plans/` is the packet and plan behind one example, in FAM's own
words, checked in code at load, and shown in his `<teaching>` - off with
`EDITOR_WORKED_PLANS=0`.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os

import pytest

import script_generator
from agents import editor, models, worked_plans
from agents.editor import EditorAnswer, _Beat
from script_generator import ScriptNotes, plan_episode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def fresh():
    worked_plans.reset()
    yield
    worked_plans.reset()


def _files():
    return sorted(worked_plans.PLANS.glob("*.json"))


def test_every_worked_plan_passes_his_checks():
    assert len(_files()) >= 8
    for path in _files():
        data = json.loads(path.read_text(encoding="utf-8"))
        assert worked_plans.problems(data, path.stem) == [], path.name
    assert len(worked_plans.plans()) == len(_files())


def test_the_worked_plans_cover_most_kinds_of_story():
    facets = {script_generator.EXAMPLE_FACETS[d["example"]] for d in worked_plans.plans()}
    assert {"sports", "money", "tech", "science", "health", "culture", "world"} <= facets


def test_each_teaches_choosing_not_only_ordering():
    """Every worked plan leaves out at least one packet sentence."""
    for data in worked_plans.plans():
        used = set(data["answer"]["summary"]) | set(data["answer"]["opening"]) \
            | set(data["answer"]["landing"])
        for beat in data["answer"]["beats"]:
            used |= set(beat["sentences"])
        numbered = {f"{s.prefix}.{i}" for s in worked_plans.sources_of(data)
                    for i in range(1, len(s.sentences) + 1)}
        assert numbered - used, data["example"]


def test_a_broken_worked_plan_is_left_out_and_said_why():
    data = json.loads(_files()[0].read_text(encoding="utf-8"))
    data["answer"]["beats"][0]["sentences"] = ["P9.9"]
    wrong = worked_plans.problems(data, _files()[0].stem)
    assert any("drop" in w for w in wrong)
    assert worked_plans.problems({"example": "no-such"}, "no-such")
    assert worked_plans.problems({}) == ["it names no example"]


def test_he_reads_them_numbered_as_his_own_packets(monkeypatch):
    _settings(monkeypatch, example_matching=True)      # the closest line is matching's
    text = worked_plans.teaching("sports")
    assert text.startswith("Worked plans:")
    assert '--- worked plan: "why is the sky blue" (it became the example 1-blue-sky) ---' in text
    assert "[P1.1] Max Verstappen won the Singapore sprint" in text
    assert "This episode is the same kind of story as 2-singapore-sprint." in text
    assert "same kind of story" not in worked_plans.teaching("")
    _settings(monkeypatch, example_matching=False)
    assert "same kind of story" not in worked_plans.teaching("sports")


def _settings(monkeypatch, **changes):
    import config

    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, **changes))


def test_off_he_is_shown_the_examples_alone(monkeypatch):
    _settings(monkeypatch, editor_worked_plans=False)
    assert worked_plans.teaching("sports") == ""


def test_his_teaching_holds_the_examples_then_the_worked_plans(monkeypatch):
    answer = EditorAnswer(answers="x", summary=["P1.1"], opening=["P1.1"],
                          beats=[_Beat(purpose="y", sentences=["P1.1"])],
                          landing=["P1.1"], thin=False, missing=[])

    async def ask(request, notes=None, client=None):
        ask.request = request
        return models.AgentResult(models.OK, "editor", "outline", value=answer, model="m")

    monkeypatch.setattr(models, "ask", ask)
    evidence = ("SOURCE 1\nTitle: t\nPublished: 2026-11-02 (yesterday)\n"
                "Source type: established outlet\nKey evidence:\nThe Fed held rates.\n")
    plan = dataclasses.replace(plan_episode("what did the fed decide", 1.5),
                               search=False, evidence=evidence)
    asyncio.run(editor.outline(plan, ScriptNotes()))
    teaching = dict(ask.request.material)["teaching"]
    assert teaching.index("--- 2-fed-raises-rates ---") < teaching.index("Worked plans:")
    assert "worked plans" in ask.request.instructions


def test_the_checker_runs_with_no_key():
    import subprocess
    import sys

    out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "editor_teaching.py"),
                          "--check"], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "2-singapore-sprint: ok" in out.stdout
