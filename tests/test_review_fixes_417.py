"""The pre-merge review of Phase 2 (§417): each finding, pinned.

* the intake UPDATE's CASE runs on Postgres (`? = 1`, never a bare int);
* a figure is merged only word for word - swapped winners, swapped scores
  and "rose"/"fell" share figures, names and wording;
* a fact read again keeps its corroborations and its supersede mark;
* a superseded fact's corroborations move to its keeper, and back on restore;
* a refused provider's live record is not kept once Consuela runs;
* a node id with `_` matches only itself (instr, never LIKE);
* a re-file that fails raises, so the task queue shows it.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import closing

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as store_db  # noqa: E402
import history_archive as ha  # noqa: E402
from agents import dedupe, housekeeping  # noqa: E402

NOW = 1_790_000_000.0


@pytest.fixture
def store(tmp_path):
    return ha.SQLiteBackend(str(tmp_path / "archive.db"))


def _vec():
    return [1.0] + [0.0] * (ha.DIMS - 1)


def _fact(fid, url, text="A fact.", corroborations=(), node="eagles"):
    return ha.Fact(url=url, text=text, id=fid, published="2026-10-01",
                   corroborations=list(corroborations), node_id=node,
                   node_path=["nfl", node] if node else [])


def _row(store, fid, columns="corroborations, active, superseded_by"):
    with closing(store._connect()) as db:
        return db.execute(f"SELECT {columns} FROM facts WHERE id = ?", (fid,)).fetchone()


def test_the_intake_case_compares_so_postgres_can_run_it():
    import inspect

    source = inspect.getsource(ha.SQLiteBackend.intake)
    assert "CASE WHEN ? = 1 THEN 0" in source
    assert "CASE WHEN %s = 1 THEN 0" in store_db.translate(
        "digested = CASE WHEN ? = 1 THEN 0 ELSE digested END")[0]


@pytest.mark.parametrize("a,b", [
    ("The Eagles beat the Giants 24-17.", "The Giants beat the Eagles 24-17."),
    ("Eagles 24, Giants 17.", "Eagles 17, Giants 24."),
    ("Tesla shares rose 3 percent.", "Tesla shares fell 3 percent."),
])
def test_contradictory_figures_are_never_merged_by_code(a, b):
    left = {"text": a, "url": "https://a.com", "published_ts": NOW, "figure": {}}
    right = {"text": b, "url": "https://b.com", "published_ts": NOW, "figure": {}}
    assert dedupe.code_verdict(left, right, 0.99)[0] == dedupe.DIFFERENT


def test_a_fact_read_again_keeps_its_corroborations_and_its_mark(store):
    store.add_fact(_fact("k", "https://a.com"), _vec(), "s", NOW)
    store.add_fact(_fact("r", "https://b.com"), _vec(), "s", NOW)
    assert store.supersede("r", "k", "both models: same", NOW)
    # Consolidation reads both articles again (new passages, §400).
    store.add_fact(_fact("k", "https://a.com"), _vec(), "s", NOW + 1)
    store.add_fact(_fact("r", "https://b.com"), _vec(), "s", NOW + 1)
    links, active, by = _row(store, "k")
    assert [c["url"] for c in json.loads(links)] == ["https://b.com"]
    assert tuple(_row(store, "r")[1:]) == (0, "k")


def test_a_supersede_chain_keeps_every_corroboration(store):
    for fid, url in (("a", "https://a.com"), ("b", "https://b.com"),
                     ("c", "https://c.com")):
        store.add_fact(_fact(fid, url), _vec(), "s", NOW)
    assert store.supersede("c", "b", "same", NOW)       # b holds c
    assert store.supersede("b", "a", "same", NOW)       # a holds b, and c through it
    held = [c["url"] for c in json.loads(_row(store, "a")[0])]
    assert held == ["https://b.com", "https://c.com"]
    assert store.restore("b")
    assert json.loads(_row(store, "a")[0]) == []


def test_a_refused_provider_s_live_record_is_never_kept(monkeypatch):
    records = [{"source": "Finnhub", "lines": ["x"]}, {"source": "Tiingo", "lines": ["y"]},
               {"source": "API-Sports", "lines": ["z"]}]
    # Whether or not Consuela runs (the owner, §422).
    for runs in (False, True):
        monkeypatch.setattr(ha, "consuela_runs", lambda runs=runs: runs)
        assert [r["source"] for r in ha.kept_live(records)] == ["Tiingo"]
    import dataclasses

    monkeypatch.setattr(ha, "settings", dataclasses.replace(
        ha.settings, housekeeper_approved_providers=("tiingo", "finnhub", "api-sports")))
    assert [r["source"] for r in ha.kept_live(records)] == ["Tiingo"]


def test_an_underscore_in_a_node_matches_only_itself(store):
    store.add_fact(_fact("x", "https://x.com", node="nfl_eagles"), _vec(), "s", NOW)
    store.add_fact(_fact("y", "https://y.com", node="nflxeagles"), _vec(), "s", NOW)
    assert [f["id"] for f in store.facts_under("nfl_eagles", 10)] == ["x"]
    from agents.retriever import ReadOnlySQL

    sql, args = ReadOnlySQL._under(["nfl_eagles"])
    assert "LIKE" not in sql and '"nfl_eagles"' in args


def test_a_failed_refile_raises(monkeypatch):
    monkeypatch.setattr(housekeeping, "refile", lambda node: {"why": "OperationalError: x"})
    with pytest.raises(RuntimeError):
        asyncio.run(housekeeping.refile_handler("housekeeper:refile:eagles"))
    monkeypatch.setattr(housekeeping, "refile", lambda node: {"why": "Consuela may not run"})
    asyncio.run(housekeeping.refile_handler("housekeeper:refile:eagles"))
