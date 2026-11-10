"""Whether the writer followed Shakespeare's plan (§501, item 1).

§437 left it unmeasured: if the writer does not follow his plan, nothing
done to the plan can show in the episode. Measured in code, per part -
opening, parts used, their order, landing - and kept as numbers beside the
episode's quality row, never his chosen sentences.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest

import episode_quality
from agents import editor, models
from agents.editor import EditorAnswer, _Beat
from script_generator import ScriptNotes, plan_episode

PLAN = {
    "opening": ["The Federal Reserve held its benchmark rate at 4.25 percent on Wednesday."],
    "beats": [["Powell said the committee would wait for more inflation data before moving."],
              ["Two members of the committee dissented and wanted a cut."],
              ["Stocks rose after the announcement, led by bank shares."]],
    "landing": ["Mortgage rates fell to their lowest level since March."],
}

FOLLOWED = [
    "The Federal Reserve held its benchmark rate at 4.25 percent on Wednesday.",
    "Jerome Powell said the committee wants more inflation data before it moves again.",
    "Not everyone agreed: two committee members dissented, wanting a cut.",
    "Stocks rose on the news, with bank shares leading.",
    "And mortgage rates fell to their lowest level since March.",
]


def test_a_script_that_follows_the_plan_scores_one():
    out = episode_quality.plan_following(PLAN, FOLLOWED)
    assert out == {"opening": 1.0, "beats": 1.0, "order": 1.0, "landing": 1.0,
                   "score": 1.0, "beats_planned": 3}


def test_each_part_is_measured_on_its_own():
    # Opens elsewhere, tells the parts out of order, lands elsewhere.
    script = ["Wall Street had a good day.",
              "Stocks rose on the news, with bank shares leading.",
              "Mortgage rates fell to their lowest level since March.",
              "Two committee members dissented, wanting a cut.",
              "Powell said the committee wants more inflation data before it moves.",
              "The Federal Reserve held its benchmark rate at 4.25 percent.",
              "That is where things stand."]
    out = episode_quality.plan_following(PLAN, script)
    assert out["opening"] == 0.0 and out["landing"] == 0.0
    assert out["beats"] == 1.0 and out["order"] == 0.0
    assert out["score"] == 0.25


def test_a_part_never_told_counts_against_the_parts():
    out = episode_quality.plan_following(PLAN, FOLLOWED[:2] + FOLLOWED[4:])
    assert out["beats"] == round(1 / 3, 3)
    assert out["order"] is None                       # one part told: no pair


def test_one_shared_word_is_not_a_use():
    assert episode_quality.plan_following(
        {"beats": [["The Federal Reserve held its benchmark rate."]]},
        ["Rates are a big topic.", "Many people talk about it."])["beats"] == 0.0


def test_in_tandem_the_opening_is_not_his():
    notes = ScriptNotes()
    notes.editor = {"plan": PLAN}
    notes.editor_tandem = "handed_over"
    out = episode_quality._plan_measure(notes, FOLLOWED)
    assert out["opening"] is None and out["surface"] == "tandem"
    notes.editor_tandem = ""
    assert episode_quality._plan_measure(notes, FOLLOWED)["surface"] == "ahead"


def test_nothing_to_measure_is_none():
    assert episode_quality.plan_following({}, FOLLOWED) is None
    assert episode_quality.plan_following(PLAN, []) is None
    assert episode_quality._plan_measure(ScriptNotes(), FOLLOWED) is None


@pytest.fixture
def quality(tmp_path, monkeypatch):
    store = episode_quality.QualityStore(str(tmp_path / "q.db"))
    episode_quality.reset(store)
    monkeypatch.setattr(episode_quality, "_grade_soon", lambda *a, **k: None)
    yield store
    episode_quality.reset()


def test_note_keeps_the_numbers_never_his_sentences(quality):
    notes = ScriptNotes()
    notes.editor = {"plan": PLAN, "beats": 3}
    episode_quality.note("k", 100, "what did the fed do", 2, "trending", FOLLOWED, notes)
    episode_quality.note("k2", 100, "what did the fed do", 2, "trending", FOLLOWED,
                         ScriptNotes())
    rows = {r["key"]: json.loads(r["measures"]) for r in quality.episodes_since(0)}
    assert rows["k"]["plan"]["score"] == 1.0 and rows["k"]["plan"]["surface"] == "ahead"
    assert "plan" not in rows["k2"]
    assert "Mortgage" not in json.dumps(rows["k"]["plan"])


def test_the_report_reads_ahead_and_tandem_apart(quality):
    ahead, tandem = ScriptNotes(), ScriptNotes()
    ahead.editor = tandem.editor = {"plan": PLAN}
    tandem.editor_tandem = "handed_over"
    episode_quality.note("a", 100, "q a", 2, "trending", FOLLOWED, ahead)
    episode_quality.note("b", 100, "q b", 2, "search", FOLLOWED[1:], tandem)
    out = episode_quality.plan_following_report(days=1)
    assert out["surfaces"]["ahead"]["episodes"] == 1
    assert out["surfaces"]["ahead"]["score"] == 1.0
    assert out["surfaces"]["tandem"]["opening"] is None


def test_his_outline_puts_his_chosen_sentences_on_the_notes(monkeypatch):
    evidence = ("SOURCE 1\nTitle: Fed holds\nPublished: 2026-11-02 (yesterday)\n"
                "Source type: wire service, paper of record or primary source\n"
                "Key evidence:\nThe Fed held rates at 4.25 percent. Two members of the committee dissented.\n")
    answer = EditorAnswer(answers="what the Fed did", summary=["P1.1"], opening=["P1.1"],
                          beats=[_Beat(purpose="the split", sentences=["P1.2"])],
                          landing=["P1.2"], thin=False, missing=[])

    async def ask(request, notes=None, client=None):
        return models.AgentResult(models.OK, "editor", "outline", value=answer, model="m")

    monkeypatch.setattr(models, "ask", ask)
    notes = ScriptNotes()
    plan = dataclasses.replace(plan_episode("what did the fed decide", 1.5),
                               search=False, evidence=evidence)
    assert asyncio.run(editor.outline(plan, notes))
    assert notes.editor["plan"] == {"opening": ["The Fed held rates at 4.25 percent."],
                                    "beats": [["Two members of the committee dissented."]],
                                    "landing": ["Two members of the committee dissented."]}
