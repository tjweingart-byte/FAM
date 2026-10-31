"""Nando's answer key (§366, training piece N3): a stronger model grades
his filings in the background, nightly, and the agreement is a number."""
from __future__ import annotations

import asyncio
import dataclasses
import datetime
import pathlib

import pytest

import categories as C
import category_audit
import topics
from agents import models, netenyahu, tasks, training
from config import settings


@pytest.fixture
def tree():
    t = topics.category_tree()
    C.apply_seed(t)
    yield t


def _answer(node, confidence="high", status="ok"):
    value = training.JudgeLabel(node=node, confidence=confidence, reason="because")
    return models.AgentResult(status=status, agent="categorizer", task="judge",
                              value=value if status == "ok" else None,
                              model="claude-opus-5-5")


def test_the_audit_keeps_the_episodes_key(tree):
    category_audit.note("Eagles score", "nfl", "tap", key="k1",
                        nando={"node": "nfl", "how": "confirmed"})
    rows = tree.audit_rows()
    assert rows[0]["key"] == "k1" and rows[0]["nando"] == "nfl"


def test_every_write_path_passes_the_key():
    root = pathlib.Path(__file__).resolve().parent.parent
    for name in ("pipeline.py", "prefetch.py", "daily_edition.py", "trending_bank.py"):
        text = (root / name).read_text()
        start = text.index("category_audit.note(")
        assert "key=key" in text[start:start + 400], name


def test_the_store_round_trips_and_a_wipe_forgets():
    s = training.store()
    s.put_label("k1", "q", "nfl", "high", "r", "claude-opus-5-5")
    s.put_label("k1", "q", "nba", "low", "r2", "claude-opus-5-5")      # re-graded
    assert s.labelled(["k1", "k2"]) == {"k1"}
    assert [l["node"] for l in s.labels()] == ["nba"]
    assert s.forget() == 1 and s.labels() == []


def test_the_judge_is_nandos_background_call_on_its_own_model(tree, monkeypatch):
    seen = []

    async def ask(request):
        seen.append(request)
        return _answer("nfl")

    monkeypatch.setattr(models, "ask", ask)
    label = asyncio.run(training.judge("k", "Eagles score", ["The Eagles won."], "nfl | sports"))
    assert label["node"] == "nfl" and label["judge_model"] == "claude-opus-5-5"
    r = seen[0]
    assert (r.agent, r.task, r.surface) == ("categorizer", "judge", "background")
    assert r.model_setting == "categorizer_judge_model" and r.rule_book == "nando.md"
    assert dict(r.material)["script"] == "The Eagles won."
    assert settings.categorizer_judge_model == "anthropic:claude-opus-5-5"


def test_the_judge_never_files_a_node_the_tree_lacks(tree, monkeypatch):
    async def ask(_request):
        return _answer("made up node")

    monkeypatch.setattr(models, "ask", ask)
    assert asyncio.run(training.judge("k", "q", ["s"], "")) is None


def test_none_of_the_tree_is_an_answer(tree, monkeypatch):
    async def ask(_request):
        return _answer("none", "medium")

    monkeypatch.setattr(models, "ask", ask)
    assert asyncio.run(training.judge("k", "q", ["s"], ""))["node"] == ""


def test_a_failed_call_labels_nothing(tree, monkeypatch):
    async def ask(_request):
        return _answer("nfl", status="timeout")

    monkeypatch.setattr(models, "ask", ask)
    assert asyncio.run(training.judge("k", "q", ["s"], "")) is None


def test_a_batch_grades_each_episode_once_and_never_a_private_one(tree, monkeypatch):
    for key, origin in (("a", "tap"), ("b", "private"), ("c", "prefetch"), ("d", "tap")):
        category_audit.note(f"question {key}", "", origin, key=key)
    category_audit.note("old row", "", "tap")                      # no key: skipped
    asked = []

    async def ask(request):
        asked.append(dict(request.material)["request"])
        return _answer("nfl")

    monkeypatch.setattr(models, "ask", ask)
    scripts = {"a": ["one"], "b": ["two"], "c": ["three"]}       # d has no script
    out = asyncio.run(training.judge_batch(lambda k: scripts.get(k, []), limit=10))
    assert out == {"looked": 3, "labelled": 2, "skipped": 1, "failed": 0}
    assert "question b" not in asked
    again = asyncio.run(training.judge_batch(lambda k: scripts.get(k, []), limit=10))
    assert again["labelled"] == 0 and asked.count("question a") == 1


def test_the_report_compares_nando_with_the_key(tree):
    rows = (("a", "nfl", "nfl"), ("b", "nfl", "sports"), ("c", "nba", "nfl"), ("d", "", "nfl"))
    for key, filed, truth in rows:
        category_audit.note(f"q {key}", "", "tap", key=key, nando={"node": filed})
        training.store().put_label(key, f"q {key}", truth, "high", "", "m")
    out = training.report()
    assert (out["compared"], out["exact"], out["on_branch"], out["wrong"],
            out["filed_nothing"]) == (4, 1, 1, 1, 1)
    assert out["confusions"] == [{"filed": "nba", "truth": "nfl", "episodes": 1}]
    assert category_audit.summary()["answer_key"]["compared"] == 4


def test_the_report_reads_none_before_anything_is_graded(tree):
    assert category_audit.summary()["answer_key"] == {}
    assert training.report()["exact_rate"] is None


def test_netenyahu_queues_the_key_once_a_night_only_when_nando_is_on(monkeypatch):
    handlers = {("categorizer", "judge"): lambda _t: None}
    late = datetime.datetime(2026, 10, 31, 8, 0, tzinfo=datetime.timezone.utc)   # 04:00 ET
    early = datetime.datetime(2026, 10, 31, 6, 0, tzinfo=datetime.timezone.utc)  # 02:00 ET
    monkeypatch.setattr(netenyahu, "settings", dataclasses.replace(settings, categorizer=False))
    assert netenyahu.schedule(late, handlers) == 0
    monkeypatch.setattr(netenyahu, "settings", dataclasses.replace(settings, categorizer=True))
    assert netenyahu.schedule(early, handlers) == 0
    assert netenyahu.schedule(late, handlers) == 1
    assert netenyahu.schedule(late, handlers) == 0
    netenyahu.reset()
    assert netenyahu.schedule(late, handlers) == 0             # already queued: same day
    assert tasks.store().due(10) == ["categorizer:judge:2026-10-31"]
    assert netenyahu.schedule(late, {}) == 0                   # nothing to run it


def test_the_wipe_forgets_the_labels():
    import demo_data

    training.store().put_label("k", "q", "nfl", "high", "", "m")
    out = demo_data._forget_what_the_log_taught()
    assert out["nando_labels_dropped"] == 1


def test_register_gives_netenyahu_the_handler(monkeypatch):
    monkeypatch.setattr(netenyahu, "HANDLERS", {})
    training.register(lambda _k: [])
    assert ("categorizer", "judge") in netenyahu.HANDLERS
