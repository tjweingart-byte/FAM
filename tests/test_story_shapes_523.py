"""Two new story shapes, and Shakespeare may change the shape (§523).

The owner (2026-11-09), on docs/story_shapes.md: "Add profile and comparison,
let Shakespeare change the shape". The brief may pick `profile` and
`comparison`; a comparison question defaults to `comparison`. Ahead of the
tap, Shakespeare names a shape from the same closed list - `in_progress`
included, since he has read the packets - and the writer's shape note and
the quality row follow him. On searchFAM the brief's shape stands.
"""
from __future__ import annotations

import asyncio
import dataclasses

import episode_intelligence as ei
import script_generator as sg
from agents import editor, models
from agents.editor import EditorAnswer, _Beat
from episode_intelligence import Brief
from script_generator import ScriptNotes, plan_episode

EVIDENCE = ("SOURCE 1\nTitle: Fed holds\nPublished: 2026-11-02 (yesterday)\n"
            "Source type: wire service, paper of record or primary source\n"
            "Key evidence:\nThe Fed held rates at 4.25 percent. Two members of the committee dissented.\n")


def test_profile_and_comparison_are_shapes_the_brief_may_pick():
    for shape in ("profile", "comparison"):
        assert ei.STRUCTURES[shape] and shape in ei.PICKABLE_STRUCTURES
    assert ei.INTENT_STRUCTURE["comparison"] == "comparison"
    assert "never which one the listener should choose" in ei.STRUCTURES["comparison"]
    assert "in_progress" not in ei.PICKABLE_STRUCTURES          # still downstream only
    note = ei.build_structure_note(Brief(structure="profile"))
    assert note.startswith("\nA profile story usually goes:")


def test_his_shapes_are_exactly_the_briefs_vocabulary():
    assert set(editor.SHAPES) == set(ei.STRUCTURES)
    enum = EditorAnswer.model_json_schema()["properties"]["shape"]["enum"]
    assert set(enum) == set(ei.STRUCTURES) | {""}


def _plan(structure="sports_recap"):
    return dataclasses.replace(plan_episode("how did the eagles do", 1.5), search=False,
                               evidence=EVIDENCE, brief=Brief(structure=structure))


def _answer(shape=""):
    return EditorAnswer(answers="x", summary=["P1.1"], opening=["P1.1"],
                        beats=[_Beat(purpose="the split", sentences=["P1.2"])],
                        landing=["P1.2"], thin=False, missing=[], shape=shape)


def _ask(answer):
    async def ask(request, notes=None, client=None):
        ask.request = request
        return models.AgentResult(models.OK, "editor", "outline", value=answer, model="m")
    return ask


def test_he_is_shown_the_briefs_shape_and_every_shape(monkeypatch):
    ask = _ask(_answer())
    monkeypatch.setattr(models, "ask", ask)
    asyncio.run(editor.outline(_plan(), ScriptNotes()))
    evidence = dict(ask.request.material)["evidence"]
    assert "The brief's shape: sports_recap." in evidence
    assert f"- in_progress: {ei.STRUCTURES['in_progress']}" in evidence
    assert "in_progress" in ask.request.instructions


def _ahead(monkeypatch, answer, structure="sports_recap"):
    from agents import netenyahu

    monkeypatch.setattr(models, "ask", _ask(answer))
    notes = ScriptNotes()
    notes.ahead = True
    notes.agents = netenyahu.EpisodeRun(("editor",), {})
    plan = _plan(structure)
    return asyncio.run(sg._with_editor(plan, notes)), notes, plan


def test_ahead_of_the_tap_his_shape_reaches_the_writer(monkeypatch):
    editor.reset()
    out, notes, before = _ahead(monkeypatch, _answer("in_progress"))
    assert out.brief.structure == "in_progress"
    assert before.brief.structure == "sports_recap"             # the shared brief untouched
    assert notes.editor["shape_from"] == "sports_recap"
    assert ei.STRUCTURES["in_progress"] in sg.build_prompt(out)
    assert editor.SINCE_BOOT["shape_changed"] == 1


def test_an_empty_or_same_shape_keeps_the_briefs(monkeypatch):
    for shape in ("", "sports_recap"):
        out, notes, _ = _ahead(monkeypatch, _answer(shape))
        assert out.brief.structure == "sports_recap"
        assert "shape_from" not in notes.editor


def test_on_search_the_briefs_shape_stands(monkeypatch):
    monkeypatch.setattr(models, "ask", _ask(_answer("in_progress")))
    notes = ScriptNotes()
    assert asyncio.run(editor.outline(_plan(), notes, ahead=False))
    assert notes.editor["shape"] == ""


def test_no_brief_no_change():
    notes = ScriptNotes()
    notes.editor = {"shape": "profile"}
    plan = dataclasses.replace(_plan(), brief=None)
    assert sg._with_shape(plan, notes) is plan
