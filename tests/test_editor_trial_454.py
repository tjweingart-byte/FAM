"""Decided item 37's two weeks: editions with and without Shakespeare (§454).

With `EDITOR_TRIAL=1`, half the episodes written ahead of the tap - by a
hash of the question and length - are written without his plan, and each
records its arm on its quality row, so listening can be compared by arm.
Off, nothing changes: he plans every one and no arm is recorded.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import subprocess
import sys
from contextlib import closing

import pytest

import db as store_db
import episode_quality
import script_generator as sg
from agents import editor, netenyahu
from script_generator import ScriptNotes, plan_episode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCE = """SOURCE 1
Title: Fed holds
Published: 2026-11-03 (yesterday)
Key evidence:
The Federal Reserve held its benchmark rate at 4.25 percent.
"""


@pytest.fixture(autouse=True)
def fresh():
    editor.reset()
    yield
    editor.reset()


def trial(monkeypatch, on):
    import config

    changed = dataclasses.replace(config.settings, editor_trial=on)
    monkeypatch.setattr(config, "settings", changed)
    monkeypatch.setattr(sg, "settings", changed)


def plan(query="what did the fed decide"):
    return dataclasses.replace(plan_episode(query, 1.5), search=False, evidence=EVIDENCE)


def run(monkeypatch, p, roles=("editor",), answer="\n<plan>planned</plan>\n"):
    called = []

    async def outline(_p, notes=None, **kw):
        called.append(kw)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(editor, "outline", outline)
    notes = ScriptNotes()
    notes.ahead = True
    notes.agents = netenyahu.EpisodeRun(tuple(roles), {})
    out = asyncio.run(sg._with_editor(p, notes))
    return out, notes, called


def questions(n=200):
    return [f"what is the latest on story number {i}" for i in range(n)]


def test_off_he_plans_every_one_and_no_arm_is_kept(monkeypatch):
    trial(monkeypatch, False)
    for q in questions(20):
        out, notes, called = run(monkeypatch, plan(q))
        assert called and out.editor_outline and notes.editor_arm == ""
        assert sg.editor_trial_arm(plan(q)) == ""


def test_on_half_go_without_and_the_same_story_always_lands_the_same(monkeypatch):
    trial(monkeypatch, True)
    arms = [sg.editor_trial_arm(plan(q)) for q in questions()]
    assert set(arms) == {"with", "without"}
    assert 0.4 <= arms.count("with") / len(arms) <= 0.6
    # Same question and length, same arm - a rewrite never moves it - and
    # spacing or case do not change the question.
    for q in questions(30):
        assert sg.editor_trial_arm(plan(q)) == sg.editor_trial_arm(plan("  " + q.upper()))


def test_without_he_is_not_asked_and_the_writer_reads_the_packets(monkeypatch):
    trial(monkeypatch, True)
    q = next(q for q in questions() if sg.editor_trial_arm(plan(q)) == "without")
    p = plan(q)
    out, notes, called = run(monkeypatch, p)
    assert called == [] and out == p and notes.editor_arm == "without"


def test_with_records_whether_he_planned(monkeypatch):
    trial(monkeypatch, True)
    q = next(q for q in questions() if sg.editor_trial_arm(plan(q)) == "with")
    out, notes, _ = run(monkeypatch, plan(q))
    assert out.editor_outline and notes.editor_arm == "with"
    for answer in ("", RuntimeError("down")):
        out, notes, _ = run(monkeypatch, plan(q), answer=answer)
        assert out.editor_outline == "" and notes.editor_arm == "with_unplanned"


def test_no_arm_when_he_does_not_work_or_not_ahead(monkeypatch):
    trial(monkeypatch, True)
    for q in questions(10):
        _out, notes, called = run(monkeypatch, plan(q), roles=())
        assert called == [] and notes.editor_arm == ""


def _row(key, arm, scores):
    return {"key": key, "sourced": 100, "at": 1_000.0, "query": key, "minutes": 2,
            "origin": "trending", "structure": "", "category": "", "title": "",
            "writer_model": "", "prompt": "", "words": 1, "word_budget": 0,
            "sentences": json.dumps(["A."]), "evidence": "",
            "measures": json.dumps({"scores": scores}), "editor_arm": arm}


def test_the_arm_is_kept_and_compared_by_listening(tmp_path):
    quality = episode_quality.QualityStore(str(tmp_path / "q.db"))
    quality.record(_row("a", "with", {"flow": 0.8}), now=1_000.0)
    quality.record(_row("b", "with_unplanned", {"flow": 0.6}), now=1_000.0)
    quality.record(_row("c", "without", {"flow": 0.4}), now=1_000.0)
    quality.record(_row("d", "", {"flow": 0.1}), now=1_000.0)          # no trial
    for key, listener, heard in (("a", "l1", 115.0), ("a", "l2", 10.0),
                                 ("b", "l3", 60.0), ("c", "l4", 5.0)):
        quality.note_listen(key, 100, listener, heard, 120.0, 2, now=1_000.0)
    out = episode_quality.editor_trial(days=1, now=1_000.0 + 60, quality=quality)
    assert out["unplanned"] == 1
    w, wo = out["arms"]["with"], out["arms"]["without"]
    assert (w["episodes"], w["listens"], wo["episodes"], wo["listens"]) == (2, 3, 1, 1)
    assert w["early_exit"] == round(1 / 3, 3) and w["finished"] == round(1 / 3, 3)
    assert wo["early_exit"] == 1.0 and wo["finished"] == 0.0
    assert w["code"]["flow"] == 0.7 and wo["code"]["flow"] == 0.4
    # A person is never in it.
    assert "l1" not in json.dumps(out)


def test_an_existing_file_is_widened(tmp_path):
    """A quality store from before §454 gains the column, on SQLite or Postgres."""
    path = str(tmp_path / "old.db")
    with closing(store_db.connect(path, isolation_level=None)) as db:
        db.execute("""CREATE TABLE episodes (
                          key TEXT NOT NULL, sourced INTEGER NOT NULL, at REAL NOT NULL,
                          query TEXT NOT NULL DEFAULT '', minutes INTEGER NOT NULL DEFAULT 0,
                          origin TEXT NOT NULL DEFAULT '', structure TEXT NOT NULL DEFAULT '',
                          category TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
                          writer_model TEXT NOT NULL DEFAULT '', prompt TEXT NOT NULL DEFAULT '',
                          words INTEGER NOT NULL DEFAULT 0, word_budget INTEGER NOT NULL DEFAULT 0,
                          sentences TEXT NOT NULL DEFAULT '[]', evidence TEXT NOT NULL DEFAULT '',
                          measures TEXT NOT NULL DEFAULT '{}', grade TEXT NOT NULL DEFAULT '',
                          grade_model TEXT NOT NULL DEFAULT '',
                          grade_dollars REAL NOT NULL DEFAULT 0,
                          graded_at REAL NOT NULL DEFAULT 0, PRIMARY KEY (key, sourced))""")
        db.execute("INSERT INTO episodes (key, sourced, at) VALUES ('old', 1, 1000.0)")
    quality = episode_quality.QualityStore(path)
    episode_quality.QualityStore(path)                     # a second open is a no-op
    quality.record(_row("new", "without", {}), now=1_000.0)
    rows = {r["key"]: r["editor_arm"] for r in quality.episodes_since(0)}
    assert rows == {"old": "", "new": "without"}


def test_note_keeps_the_arm_from_the_notes(tmp_path, monkeypatch):
    quality = episode_quality.QualityStore(str(tmp_path / "q.db"))
    episode_quality.reset(quality)
    monkeypatch.setattr(episode_quality, "_grade_soon", lambda *a, **k: None)
    try:
        notes = ScriptNotes()
        notes.editor_arm = "with"
        episode_quality.note("k", 100, "what happened", 2, "trending",
                             ["The Fed held rates."], notes)
        episode_quality.note("k2", 100, "what happened", 2, "trending",
                             ["The Fed held rates."], ScriptNotes())
        rows = {r["key"]: r["editor_arm"] for r in quality.episodes_since(0)}
        assert rows == {"k": "with", "k2": ""}
    finally:
        episode_quality.reset()


def test_the_setting_is_copied_and_off():
    text = open(os.path.join(ROOT, ".env.example")).read()
    assert "\nEDITOR_TRIAL=0\n" in text
    assert editor.report()["trial"] is False


def test_the_report_tool_runs_with_nothing_to_report(tmp_path):
    env = {**os.environ, "EPISODE_QUALITY_DB": str(tmp_path / "none.db")}
    env.pop("DATABASE_URL", None)
    out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "editor_trial.py")],
                         capture_output=True, text=True, env=env, timeout=120)
    assert out.returncode == 0 and "no trial episodes" in out.stdout
