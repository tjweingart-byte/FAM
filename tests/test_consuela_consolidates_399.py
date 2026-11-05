"""Phase 2, piece 3: Consuela consolidates each article as it enters (§399).

Pinned here, against a fake model (no key, no network):

* the model answers sentence numbers only; code checks them, retries once,
  reads every figure and decides its status;
* an article is filed under Nando's one node, or held - never guessed;
* only an approved provider's passages are read; no hostname is shown;
* the dry run reports and writes nothing to the archive, and asks once;
* live, the chosen sentences become facts verbatim and the intake record is
  marked consolidated, its passages cleared at once (§400);
* with Consuela off nothing runs and nothing is sent.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import sqlite3
import sys
from contextlib import closing
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import history_archive as ha  # noqa: E402
from agents import housekeeper as hk  # noqa: E402
from agents import models  # noqa: E402

NOW = 1_793_000_000.0

SENTENCES = [
    "The Eagles beat the Giants 24-17 on Sunday at the Meadowlands.",
    "Jalen Hurts threw for 310 yards and two touchdowns in the win.",
    "\"We found a way,\" coach Nick Sirianni said after the game.",
    "Tickets for the rematch are expected to cost $250 on average.",
    "Subscribe to our newsletter for more stories like this one.",
]


def _settings(monkeypatch, **values):
    changed = dataclasses.replace(ha.settings, archive=True, archive_backend="sqlite",
                                  **values)
    monkeypatch.setattr(ha, "settings", changed)
    monkeypatch.setattr(hk, "settings", changed)
    return changed


@pytest.fixture
def archive(monkeypatch, tmp_path):
    ha.reset()
    _settings(monkeypatch)
    monkeypatch.setattr(ha, "_embed", lambda text: ([1.0] + [0.0] * (ha.DIMS - 1),
                                                    f"onnx:{ha.DIMS}"))
    monkeypatch.setattr(ha, "consuela_runs", lambda: True)
    monkeypatch.setattr(ha, "node_path", lambda node: ["sports", "nfl", node])
    import topics

    tree = SimpleNamespace(get=lambda node: object() if node in ("eagles", "nfl") else None,
                           ancestors=lambda node: ["nfl", "sports"])
    monkeypatch.setattr(topics, "category_tree", lambda: tree)
    store = ha.SQLiteBackend(str(tmp_path / "archive.db"))
    ha.use(store)
    hk.reset(hk.ReportStore(str(tmp_path / "housekeeper.db")))
    yield store
    ha.reset()
    hk.reset()


def _intake(store, url, node="eagles", provider="perplexity", sentences=SENTENCES):
    finding = ha.Finding(url=url, title="Eagles top Giants", host="espn.com",
                         grade="established", published="2026-10-19",
                         passages=(" ".join(sentences),), provider=provider)
    store.intake([finding], "Eagles", "nfl", NOW, node=node)


class _FakeModel:
    """`models.ask` answering from a list, recording each request."""

    def __init__(self, *answers, status=models.OK):
        self.answers = list(answers)
        self.status = status
        self.requests = []

    async def __call__(self, request, **_kw):
        self.requests.append(request)
        if self.status != models.OK:
            return models.AgentResult(status=self.status, agent=request.agent,
                                      task=request.task)
        value = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return models.AgentResult(status=models.OK, agent=request.agent,
                                  task=request.task, value=value)


def _pick(article=1, key=(1, 2), quotes=(3,), figures=((1, "happened"), (4, "happened"))):
    return hk.ArticlePick(article=article, key=list(key), quotes=list(quotes),
                          figures=[hk.FigureMark(sentence=s, status=st) for s, st in figures])


def _run():
    return asyncio.run(hk.run())


def _facts(store):
    with closing(sqlite3.connect(store.path)) as db:
        return db.execute("SELECT kind, text, figure, node_id, node_path, url FROM facts "
                          "ORDER BY text").fetchall()


def _digested(store):
    with closing(sqlite3.connect(store.path)) as db:
        return dict(db.execute("SELECT url, digested FROM intake").fetchall())


# --- code's half -------------------------------------------------------------

def test_an_answer_is_used_only_when_every_number_is_in_range_and_once():
    batch = [({}, SENTENCES), ({}, SENTENCES[:2])]
    good = hk.Picks(articles=[_pick(1), _pick(2, key=(1,), quotes=(), figures=())])
    assert hk.check(good, batch) == ""
    for bad in (
        hk.Picks(articles=[_pick(1)]),                                     # one missing
        hk.Picks(articles=[_pick(1), _pick(1)]),                           # repeated
        hk.Picks(articles=[_pick(1), _pick(2, key=(3,), quotes=(), figures=())]),  # range
        hk.Picks(articles=[_pick(1, key=(1, 1)), _pick(2, key=(), quotes=(), figures=())]),
        hk.Picks(articles=[_pick(3), _pick(2, key=(), quotes=(), figures=())]),
    ):
        assert hk.check(bad, batch), bad


@pytest.mark.parametrize("sentence,said,status", [
    ("The Eagles won 24-17.", "happened", "outcome"),
    ("The Eagles are expected to win 24-17.", "happened", "forecast"),
    ("Tickets will cost $250.", "happened", "forecast"),
    ("The Eagles won 24-17.", "expected", "forecast"),
    ("The Eagles won 24-17.", "unsure", "unsure"),
])
def test_a_figure_is_an_outcome_only_when_the_model_and_the_wording_agree(sentence, said,
                                                                        status):
    assert hk.status_of(sentence, said) == status


def test_only_approved_providers_passages_are_read():
    record = {"by_provider": {"perplexity": ["Kept."], "parallel": ["Never."],
                              "finnhub": ["Never either."], "gdelt": ["Also kept."]}}
    assert hk.passages_of(record) == ["Kept.", "Also kept."]
    assert hk.passages_of({"by_provider": {}}) == []


def test_an_article_is_filed_under_its_known_nodes_or_held(archive):
    s = ["A sentence."]
    assert hk.held_why({"nodes": ["eagles"]}, s) == ""
    assert "no node" in hk.held_why({"nodes": []}, s)
    # Several nodes are filed under all (§413), never held for Nando.
    assert hk.held_why({"nodes": ["eagles", "nfl"]}, s) == ""
    assert hk.filing_nodes({"nodes": ["eagles", "bears", "nfl", "eagles"]}) == [
        "eagles", "nfl"]
    assert "none of its nodes" in hk.held_why({"nodes": ["bears"]}, s)
    assert "approved provider" in hk.held_why({"nodes": ["eagles"]}, [])


# --- the dry run -------------------------------------------------------------

def test_the_dry_run_reports_what_it_would_keep_and_writes_nothing(archive, monkeypatch):
    fake = _FakeModel(hk.Picks(articles=[_pick(1)]))
    monkeypatch.setattr(models, "ask", fake)
    _intake(archive, "https://espn.com/a")
    _intake(archive, "https://espn.com/unfiled", node="bears")   # not in the tree
    summary = _run()
    assert (summary["chosen"], summary["held"], summary["kept_facts"]) == (1, 1, 0)
    assert summary["dry_run"] is True
    assert _facts(archive) == []
    assert _digested(archive) == {"https://espn.com/a": 0, "https://espn.com/unfiled": 0}
    rows = {r["url"]: r for r in hk.store().recent()}
    chosen = rows["https://espn.com/a"]
    assert chosen["mode"] == "dry_run" and chosen["outcome"] == "chosen"
    # "24-17" is not a figure code reads (money, percent, points): a score in
    # an article is kept as its sentence; scores themselves come from the
    # providers' series (§397).
    assert [k["kind"] for k in chosen["kept"]] == ["sentence", "sentence", "quote", "figure"]
    assert chosen["kept"][3]["figure"]["status"] == "forecast"
    assert rows["https://espn.com/unfiled"]["outcome"] == "held"
    # Asked once: the next run sends nothing for what it already reported.
    _run()
    assert len(fake.requests) == 1


def test_the_model_sees_numbered_sentences_and_never_a_hostname(archive, monkeypatch):
    fake = _FakeModel(hk.Picks(articles=[_pick(1)]))
    monkeypatch.setattr(models, "ask", fake)
    _intake(archive, "https://espn.com/a")
    _run()
    request = fake.requests[0]
    (tag, text), = request.material
    assert tag == "evidence" and "[1] The Eagles beat the Giants" in text
    assert "espn" not in text.lower()
    assert request.agent == "housekeeper" and request.model_setting == "housekeeper_model"
    assert request.surface == "background" and request.rule_book == "consuela.md"


# --- live ----------------------------------------------------------------------

def test_live_the_chosen_sentences_become_facts_verbatim_under_the_node(archive, monkeypatch):
    _settings(monkeypatch, housekeeper_dry_run=False)
    monkeypatch.setattr(models, "ask", _FakeModel(hk.Picks(articles=[_pick(1)])))
    _intake(archive, "https://espn.com/a")
    summary = _run()
    assert summary["kept_facts"] == 4
    facts = _facts(archive)
    assert {f[1] for f in facts} == set(SENTENCES[:4])
    assert all(f[3] == "eagles" and json.loads(f[4]) == ["sports", "nfl", "eagles"]
               for f in facts)
    by_text = {f[1]: (f[0], json.loads(f[2])) for f in facts}
    kind, figure = by_text[SENTENCES[3]]
    assert kind == "figure" and figure["status"] == "forecast"
    assert figure["values"] == [{"value": 250.0, "decimals": 0, "unit": "money"}]
    assert by_text[SENTENCES[2]][0] == "quote"
    # The newsletter line was not chosen and is not kept.
    assert SENTENCES[4] not in by_text
    assert _digested(archive) == {"https://espn.com/a": 1}
    assert archive.undigested(5) == []


def test_a_figure_code_cannot_read_is_kept_as_a_sentence(archive, monkeypatch):
    _settings(monkeypatch, housekeeper_dry_run=False)
    monkeypatch.setattr(models, "ask", _FakeModel(hk.Picks(articles=[
        _pick(1, key=(), quotes=(), figures=((5, "happened"),))])))
    # The fifth sentence names no figure code can read. (Not SENTENCES' own
    # fifth: a "subscribe" line is never offered since §422.)
    _intake(archive, "https://espn.com/a", sentences=SENTENCES[:4] + [
        "Fans stayed long after the final whistle to cheer the comeback."])
    _run()
    assert [(f[0], f[2]) for f in _facts(archive)] == [("sentence", "{}")]


# --- failures ---------------------------------------------------------------------

def test_a_bad_answer_is_retried_once_then_left_for_the_next_run(archive, monkeypatch):
    _settings(monkeypatch, housekeeper_dry_run=False)
    bad = hk.Picks(articles=[_pick(1, key=(99,))])
    fake = _FakeModel(bad, bad)
    monkeypatch.setattr(models, "ask", fake)
    _intake(archive, "https://espn.com/a")
    summary = _run()
    assert len(fake.requests) == 2 and summary["failed"] == 1
    assert _facts(archive) == [] and _digested(archive) == {"https://espn.com/a": 0}


def test_a_fallen_back_call_writes_nothing(archive, monkeypatch):
    _settings(monkeypatch, housekeeper_dry_run=False)
    fake = _FakeModel(status=models.STAGING)
    monkeypatch.setattr(models, "ask", fake)
    _intake(archive, "https://espn.com/a")
    assert _run()["failed"] == 1 and len(fake.requests) == 1
    assert _facts(archive) == []
    with pytest.raises(RuntimeError):
        asyncio.run(hk.handler("housekeeper:consolidate:intake"))


def test_with_consuela_off_nothing_runs_and_nothing_is_sent(archive, monkeypatch):
    monkeypatch.setattr(ha, "consuela_runs", lambda: False)
    fake = _FakeModel(hk.Picks(articles=[_pick(1)]))
    monkeypatch.setattr(models, "ask", fake)
    _intake(archive, "https://espn.com/a")
    assert _run()["why"] == "Consuela may not run"
    assert fake.requests == []


def test_held_articles_never_block_the_queue_behind_them(archive, monkeypatch):
    _settings(monkeypatch, housekeeper_batch_articles=1, housekeeper_batches_per_run=1)
    fake = _FakeModel(hk.Picks(articles=[_pick(1)]))
    monkeypatch.setattr(models, "ask", fake)
    _intake(archive, "https://espn.com/held", node="bears")
    _run()
    _intake(archive, "https://espn.com/fileable")
    _run()
    assert len(fake.requests) == 1
    assert {r["url"]: r["outcome"] for r in hk.store().recent()} == {
        "https://espn.com/held": "held", "https://espn.com/fileable": "chosen"}


# --- Netenyahu's task -----------------------------------------------------------

def test_her_task_is_registered_scheduled_and_queued_by_new_intake(archive, monkeypatch,
                                                                  tmp_path):
    from agents import netenyahu, tasks

    tasks.reset(tasks.TaskStore(str(tmp_path / "tasks.db")))
    try:
        hk.register()
        assert netenyahu.HANDLERS[("housekeeper", "consolidate")] is hk.handler
        assert ("housekeeper", "consolidate", 1, "housekeeper") in netenyahu.SCHEDULE
        monkeypatch.setattr(ha.EXECUTOR, "submit", lambda fn, *a: fn(*a))
        notes = SimpleNamespace(archive={"findings": [ha.Finding(
            url="https://espn.com/a", title="T", passages=("One.",),
            provider="perplexity")], "subject": "Eagles"}, category="nfl",
            category_settled={"node": "eagles"})
        ha.after_episode(notes, "k", 1.0)
        assert tasks.store().get("housekeeper:consolidate:intake")["status"] == tasks.QUEUED
    finally:
        netenyahu.HANDLERS.pop(("housekeeper", "consolidate"), None)
        tasks.reset()


def test_her_settings_default_to_a_dry_run():
    from config import settings

    assert settings.housekeeper_dry_run is True
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(root, ".env.example")).read()
    assert "HOUSEKEEPER_DRY_RUN=1" in text
    assert f"HOUSEKEEPER_BATCH_ARTICLES={settings.housekeeper_batch_articles}" in text
    assert f"HOUSEKEEPER_BATCHES_PER_RUN={settings.housekeeper_batches_per_run}" in text


# --- §400: the passages are cleared at once ------------------------------------

def test_live_the_passages_are_cleared_the_moment_the_article_is_consolidated(
        archive, monkeypatch):
    _settings(monkeypatch, housekeeper_dry_run=False)
    monkeypatch.setattr(models, "ask", _FakeModel(hk.Picks(articles=[_pick(1)])))
    _intake(archive, "https://espn.com/a")
    _run()
    with closing(sqlite3.connect(archive.path)) as db:
        row = db.execute("SELECT passages, by_provider, digested, title, nodes "
                         "FROM intake").fetchone()
    assert row == ("[]", "{}", 1, "Eagles top Giants", '["eagles"]')


def test_new_passages_for_a_consolidated_article_make_it_wait_again(archive, monkeypatch):
    _settings(monkeypatch, housekeeper_dry_run=False)
    monkeypatch.setattr(models, "ask", _FakeModel(hk.Picks(articles=[_pick(1)])))
    _intake(archive, "https://espn.com/a")
    _run()
    assert archive.undigested(5) == []
    _intake(archive, "https://espn.com/a")
    waiting = archive.undigested(5)
    assert [r["url"] for r in waiting] == ["https://espn.com/a"]
    assert waiting[0]["by_provider"]["perplexity"]


def test_in_the_dry_run_nothing_is_cleared(archive, monkeypatch):
    monkeypatch.setattr(models, "ask", _FakeModel(hk.Picks(articles=[_pick(1)])))
    _intake(archive, "https://espn.com/a")
    _run()
    assert archive.undigested(5)[0]["passages"]


def test_atlas_clears_the_passages_with_the_mark():
    calls = []

    class _Intake:
        def update_many(self, query, update):
            calls.append((query, update))
            return SimpleNamespace(modified_count=1)

    store = ha.AtlasBackend(database={"intake": _Intake(), "episode_sources": None,
                                      "facts": None, "live_series": None})
    assert store.mark_digested(["a"]) == 1
    assert calls == [({"_id": {"$in": ["a"]}},
                      {"$set": {"digested": True, "passages": []},
                       "$unset": {"by_provider": ""}})]


def test_an_article_held_for_good_never_fills_the_window(archive, monkeypatch):
    """No node, or no provider recorded (§358), never changes by waiting:
    left in intake but out of what she reads, so 160 of them cannot stand
    in front of everything newer (§417)."""
    _intake(archive, "https://espn.com/unfiled", node="")
    _intake(archive, "https://espn.com/a")
    assert [r["url"] for r in archive.undigested(10)] == ["https://espn.com/a"]
