"""Training Mclovin, steps 4 and 5 (§439): his verdicts on real episodes,
kept and reviewed nightly; and the grounding checker's place - none
installed, so nothing changes until one is.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import datetime, timezone

import pytest

import script_generator as sg
from agents import grounding, models, netenyahu
from agents import supervisor_checks as checks
from agents import supervisor_training as training
from agents.handoffs import FAILURES, ResearchPlan
from script_generator import ScriptNotes, plan_episode

EVIDENCE = """SOURCE 1
Title: Fed holds
Published: 2026-11-02 (yesterday)
Source type: wire service, paper of record or primary source
Key evidence:
The Federal Reserve held its benchmark rate at 4.25 percent on Wednesday in Washington.
"""


@pytest.fixture(autouse=True)
def stores(tmp_path):
    training.reset(training.SupervisorTrainingStore(str(tmp_path / "agents.db")))
    grounding.use(None)
    checks.reset()
    yield
    training.reset(None)
    grounding.use(None)
    checks.reset()


# ------------------------------------------------------------------ step 4: kept
def test_only_what_he_did_not_simply_pass_is_kept_with_the_episode():
    notes = ScriptNotes()
    notes.supervisor = {"rows": [
        {"sentence": 1, "check": "figures", "result": "fail", "enforced": False,
         "why": "the number 9 is not in the packets", "text": "It rose 9 percent."},
        {"sentence": 2, "check": "quote", "result": "unchecked", "enforced": True,
         "why": "out of time", "text": "x"}]}
    training.note("k1", 100, ["a.", "It rose 9 percent.", "x"], notes)
    assert training.store().episodes_since(0)[0]["sentences"] == 3
    rows = training.store().flags_for("k1", 100)
    assert {(r["sentence"], r["check_name"], r["result"]) for r in rows} == {
        (1, "figures", "fail"), (2, "quote", "unchecked")}


def test_an_episode_he_did_not_check_keeps_nothing():
    training.note("k2", 100, ["a."], ScriptNotes())
    assert training.store().episodes_since(0) == []


def test_the_quality_record_keeps_his_verdicts_beside_it(tmp_path, monkeypatch):
    import episode_quality

    episode_quality.reset(episode_quality.QualityStore(str(tmp_path / "q.db")))
    try:
        notes = ScriptNotes()
        notes.written_from = EVIDENCE
        notes.supervisor = {"rows": [{"sentence": 0, "check": "dates", "result": "fail",
                                      "enforced": True, "why": "w", "text": "t"}]}
        episode_quality.note("k3", 200, "q", 2, "search", ["t."], notes)
        assert training.store().flags_for("k3", 200)[0]["check_name"] == "dates"
    finally:
        episode_quality.reset(None)


# ------------------------------------------------------------------ step 4: reviewed
def _episode(tmp_path, sentences, flags):
    import episode_quality

    q = episode_quality.QualityStore(str(tmp_path / "q.db"))
    episode_quality.reset(q)
    import time

    q.record({"key": "k", "sourced": 1, "at": time.time(), "query": "q", "minutes": 2,
              "origin": "search", "structure": "", "category": "", "title": "",
              "writer_model": "", "prompt": "", "words": 10, "word_budget": 0,
              "sentences": json.dumps(sentences), "evidence": EVIDENCE, "measures": "{}"})
    training.store().put_episode("k", 1, len(sentences), flags)


def _reviewer(answers):
    asked = []

    async def ask(request, notes=None, client=None):
        asked.append(request)
        failure = answers.pop(0)
        return models.AgentResult(models.OK, "supervisor", "review", model="claude-opus-5-5",
                                  value=training.Review(failure=failure, evidence="",
                                                        reason="because"))
    return ask, asked


def test_the_review_reads_one_sentence_and_its_sources_never_the_script(tmp_path, monkeypatch):
    import episode_quality

    sentences = ["The Fed held at 4.25 percent.", "It rose 9 percent in Washington.",
                 "Rates are set in Washington."]
    _episode(tmp_path, sentences, [{"sentence": 1, "check": "figures", "result": "fail",
                                    "enforced": False, "why": "w", "text": sentences[1]}])
    ask, asked = _reviewer(["number_not_in_packets", "none"])
    monkeypatch.setattr(models, "ask", ask)
    try:
        out = asyncio.run(training.review_batch(limit=2, seed=1))
    finally:
        episode_quality.reset(None)
    assert out["reviewed"] == 2
    for request in asked:
        material = dict(request.material)
        assert request.model_setting == "supervisor_judge_model"
        assert request.rule_book == "mclovin.md" and request.surface == "background"
        assert material["script"] in sentences            # one sentence, never all
        assert "4.25 percent" in material["evidence"]
    # Each sentence is reviewed once.
    monkeypatch.setattr(models, "ask", _reviewer(["none"] * 5)[0])
    import episode_quality as eq

    eq.reset(eq.QualityStore(str(tmp_path / "q.db")))
    try:
        again = asyncio.run(training.review_batch(limit=10, seed=1))
    finally:
        eq.reset(None)
    assert again["looked"] == 1


def test_agreement_and_disagreements_are_reported():
    s = training.store()
    s.put_review("k", 1, 0, "figures", "number_not_in_packets", "", "r", "a", "opus")
    s.put_review("k", 1, 1, "figures", "none", "", "r", "b", "opus")
    s.put_review("k", 1, 2, "", "wrong_date", "", "r", "c", "opus")
    s.put_review("k", 1, 3, "", "unsure", "", "r", "d", "opus")
    report = training.report()
    assert report["checks"]["figures"] == {"flags_reviewed": 2, "agreed": 1, "missed": 0,
                                           "agreement": 0.5}
    assert report["checks"]["dates"]["missed"] == 1
    kinds = {(d["kind"], d["text"]) for d in training.disagreements()}
    assert kinds == {("too strict", "b"), ("missed", "c")}


def test_the_reviewers_failures_are_the_rule_books():
    for failure in training.CHECK_OF:
        assert failure in FAILURES or failure in checks.PROPOSED_FAILURES


def test_the_review_is_scheduled_nightly_only_while_he_works():
    assert ("supervisor", "review", 3, "supervisor") in netenyahu.SCHEDULE


def test_a_wipe_forgets_it():
    training.store().put_episode("k", 1, 2, [{"sentence": 0, "check": "dates",
                                             "result": "fail", "why": "", "text": "t"}])
    assert training.store().forget() == 1
    assert training.store().episodes_since(0) == []


# ------------------------------------------------------------------ step 5: the grounding checker
def plan():
    return dataclasses.replace(plan_episode("the fed", 1.5), search=False, evidence=EVIDENCE)


def test_with_no_checker_installed_nothing_changes():
    assert grounding.scorer() is None
    v = checks.Checker(plan(), ScriptNotes()).check("The Fed held at 4.25 percent.")
    assert "support" not in {c.check for c in v.checks}


@pytest.mark.parametrize("score,result", [(0.95, "pass"), (0.5, "unsure"), (0.05, "fail")])
def test_the_checker_passes_fails_and_leaves_unsure(score, result):
    grounding.use(lambda premise, hypothesis: score)
    v = checks.Checker(plan(), ScriptNotes()).check("Powell cut rates to 3 percent on Monday.")
    got = {c.check: c for c in v.checks}["support"]
    assert got.result == result
    if result == "fail":
        assert got.failure == "unsupported_changing_fact"


def test_a_settled_fact_with_no_particular_may_stay():
    grounding.use(lambda premise, hypothesis: 0.0)
    v = checks.Checker(plan(), ScriptNotes()).check("Central banks generally move slowly.")
    assert {c.check: c.result for c in v.checks}["support"] == "pass"


def test_a_checker_that_fails_leaves_the_sentence_unchecked():
    grounding.use(lambda premise, hypothesis: 1 / 0)
    v = checks.Checker(plan(), ScriptNotes()).check("The Fed held at 4.25 percent.")
    assert {c.check: c.result for c in v.checks}["support"] == "unchecked"


class _Answer:
    def __init__(self, text):
        self.content = [type("B", (), {"type": "text", "text": text})()]
        self.usage = None


def _generator(rewrites):
    gen = sg.ScriptGenerator.__new__(sg.ScriptGenerator)

    class _Messages:
        async def create(self, **kwargs):
            return _Answer(rewrites.pop(0) if rewrites else "NONE")

    gen.client = type("C", (), {"messages": _Messages()})()
    return gen


def _reader(enforce):
    import config

    notes = ScriptNotes()
    notes.agents = netenyahu.EpisodeRun(("supervisor",), {})
    notes.research_plan = ResearchPlan()
    reader = sg._ScriptReader(plan(), notes)
    reader.mclovin.enforce = frozenset(enforce)
    return reader, notes


@pytest.mark.parametrize("answer,spoken", [("yes", True), ("", True), ("no", False)])
def test_only_an_unsure_sentence_takes_the_second_look(monkeypatch, answer, spoken):
    grounding.use(lambda premise, hypothesis: 0.5)
    looks = []

    async def second_look(sentence, sources, ahead=False):
        looks.append(sentence)
        return answer

    monkeypatch.setattr(grounding, "second_look", second_look)
    reader, notes = _reader({"support"})
    sentence = "Powell cut rates to 3 percent on Monday in Washington."
    item = reader._checked(sentence)
    assert isinstance(item, sg.Repair) and item.second_look
    out = asyncio.run(_generator([])._repair(plan(), notes, item))
    assert looks == [sentence]
    # Supported or unanswered, it plays (decided item 32); "no", it goes back
    # to the writer once - here the writer has no true version, so it is out.
    assert (out == sentence) is spoken
    assert notes.supervisor["second_looks"][0]["answer"] == (answer or "unchecked")


def test_while_support_only_counts_nobody_takes_a_second_look():
    grounding.use(lambda premise, hypothesis: 0.5)
    reader, _notes = _reader(set())
    sentence = "Powell cut rates to 3 percent on Monday in Washington."
    assert reader._checked(sentence) == sentence
