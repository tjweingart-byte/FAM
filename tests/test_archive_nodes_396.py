"""Phase 2, piece 1: the archive filed by node, and live readings as a series (§396).

What is pinned here:

* facts carry Nando's node and its path; intake records the nodes of the
  episodes that used them; nothing changes shape without a node, which is
  every episode while `AGENTS=0`;
* an archive made before §396 is migrated in place;
* a live reading is one record per subject, metric and moment - scores
  through a game are a series, the same reading twice is one record;
* the Atlas backend's indexes, and the vector index updated when it lacks
  the `node_id` filter.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import history_archive as ha  # noqa: E402

NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def _unit(i=1):
    v = [0.0] * ha.DIMS
    v[i] = 1.0
    return v


@pytest.fixture
def archive(monkeypatch, tmp_path):
    ha.reset()
    changed = dataclasses.replace(ha.settings, archive=True,
                                  archive_backend="sqlite", archive_recall=True)
    monkeypatch.setattr(ha, "settings", changed)
    monkeypatch.setattr(ha, "_embed", lambda text: (_unit(1), f"onnx:{ha.DIMS}"))
    monkeypatch.setattr(ha, "node_path",
                        lambda node: ["sports", "nfl", node] if node else [])
    store = ha.SQLiteBackend(str(tmp_path / "archive.db"))
    ha.use(store)
    yield store
    ha.reset()


def _finding(url="https://www.reuters.com/a"):
    return ha.Finding(url=url, title="A title", host="reuters.com",
                      grade="primary", published="2026-10-20",
                      passages=("One.",), provider="perplexity")


def _row(store, sql, *args):
    with closing(sqlite3.connect(store.path)) as db:
        return db.execute(sql, args).fetchone()


# --- node ids on intake and facts -------------------------------------------

def test_intake_keeps_each_episode_s_node_and_none_without_one(archive):
    archive.intake([_finding()], "Eagles", "nfl", NOW.timestamp(), node="eagles")
    archive.intake([_finding()], "Eagles", "nfl", NOW.timestamp(), node="nfl")
    archive.intake([_finding()], "Eagles", "nfl", NOW.timestamp())
    assert json.loads(_row(archive, "SELECT nodes FROM intake")[0]) == ["eagles", "nfl"]
    archive.intake([_finding("https://www.reuters.com/b")], "Fed", "", NOW.timestamp())
    assert json.loads(_row(archive, "SELECT nodes FROM intake WHERE url = ?",
                           "https://www.reuters.com/b")[0]) == []


def test_a_fact_carries_its_node_and_path_and_the_recall_returns_them(archive):
    fact = ha.Fact(url="https://x.com/1", text="The Eagles won.",
                   published="2025-02-10", node_id="eagles",
                   node_path=["sports", "nfl", "eagles"])
    ha.add_fact(fact)
    found = ha.recall("eagles", window_days=7, now=NOW)
    assert found[0].node_id == "eagles"
    assert found[0].node_path == ["sports", "nfl", "eagles"]


def test_a_write_that_knows_no_node_never_forgets_the_filed_one(archive):
    fid = ha.add_fact(ha.Fact(url="https://x.com/1", text="A.", published="2025-02-10",
                              node_id="eagles", node_path=["nfl", "eagles"]))
    ha.add_fact(ha.Fact(url="https://x.com/1", text="A.", published="2025-02-10", id=fid))
    assert _row(archive, "SELECT node_id, node_path FROM facts") == (
        "eagles", json.dumps(["nfl", "eagles"]))


def test_an_archive_made_before_394_is_migrated_in_place(tmp_path):
    path = str(tmp_path / "old.db")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE intake (id TEXT PRIMARY KEY, url TEXT NOT NULL,"
                   " title TEXT NOT NULL, host TEXT NOT NULL, grade TEXT NOT NULL,"
                   " published TEXT NOT NULL, passages TEXT NOT NULL,"
                   " subjects TEXT NOT NULL, categories TEXT NOT NULL,"
                   " first_seen REAL NOT NULL, last_seen REAL NOT NULL,"
                   " seen INTEGER NOT NULL, digested INTEGER NOT NULL DEFAULT 0)")
        db.execute("INSERT INTO intake VALUES ('a', 'https://x.com/a', 't', 'x.com',"
                   " 'primary', '', '[]', '[]', '[]', 1, 1, 1, 0)")
    store = ha.SQLiteBackend(path)
    assert _row(store, "SELECT nodes, by_provider FROM intake") == ("[]", "{}")
    columns = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(facts)")}
    assert {"node_id", "node_path"} <= columns
    indexes = {r[1] for r in sqlite3.connect(path).execute(
        "SELECT * FROM sqlite_master WHERE type = 'index'")}
    assert {"facts_node", "live_series_subject"} <= indexes


def _notes(settled=None, live=()):
    notes = SimpleNamespace(
        archive={"findings": [_finding()], "subject": "Eagles", "live": list(live)},
        category="nfl")
    if settled is not None:
        notes.category_settled = settled
    return notes


def _run_now(monkeypatch):
    """`after_episode`'s background write, run in the test's own thread."""
    monkeypatch.setattr(ha.EXECUTOR, "submit", lambda fn, *a: fn(*a))


def test_after_an_episode_nando_s_node_reaches_the_intake(archive, monkeypatch):
    _run_now(monkeypatch)
    ha.after_episode(_notes({"node": "eagles", "how": "confirmed"}), "k", 1.0)
    assert json.loads(_row(archive, "SELECT nodes FROM intake")[0]) == ["eagles"]


def test_with_the_agents_off_no_node_is_written(archive, monkeypatch):
    # AGENTS=0: Nando never settles, so `category_settled` is never set.
    _run_now(monkeypatch)
    ha.after_episode(_notes(), "k", 1.0)
    assert json.loads(_row(archive, "SELECT nodes FROM intake")[0]) == []


def test_an_episode_s_live_record_is_filed_under_its_node(archive, monkeypatch):
    _run_now(monkeypatch)
    record = {"domain": "sports", "kind": "final", "status": "final",
              "source": "balldontlie", "entity": "Eagles vs Giants",
              "as_of": "2026-10-19T20:00:00+00:00", "lines": ["Eagles 24, Giants 17."]}
    ha.after_episode(_notes({"node": "eagles"}, live=[record]), "k", 1.0)
    assert _row(archive, "SELECT node_id, node_path FROM facts WHERE kind = 'live'") == (
        "eagles", json.dumps(["sports", "nfl", "eagles"]))


def test_a_node_s_path_runs_from_the_root(monkeypatch):
    import topics

    tree = SimpleNamespace(ancestors=lambda node: ["nfl", "sports"])
    monkeypatch.setattr(topics, "category_tree", lambda: tree)
    assert ha.node_path("eagles") == ["sports", "nfl", "eagles"]
    assert ha.node_path("") == []


def test_a_node_s_path_is_never_worth_an_error(monkeypatch):
    import topics

    def broken():
        raise RuntimeError("no tree")

    monkeypatch.setattr(topics, "category_tree", broken)
    assert ha.node_path("eagles") == ["eagles"]


# --- live readings: a series, never duplicates ------------------------------

def _score(minute, text, status="in_progress"):
    return ha.Reading(subject="Eagles vs Giants", metric="score",
                      as_of=NOW + timedelta(minutes=minute), text=text,
                      status=status, source="balldontlie", domain="sports")


def test_scores_through_a_game_are_a_series_in_time_order(archive):
    ha.add_reading(_score(90, "Eagles 24, Giants 17.", "final"))
    ha.add_reading(_score(0, "Eagles 0, Giants 0."))
    ha.add_reading(_score(45, "Eagles 10, Giants 7."))
    series = ha.readings("Eagles vs Giants", "score", NOW, NOW + timedelta(hours=3))
    assert [r.text for r in series] == ["Eagles 0, Giants 0.", "Eagles 10, Giants 7.",
                                        "Eagles 24, Giants 17."]
    assert series[-1].status == "final" and series[0].as_of == NOW


def test_the_same_reading_twice_is_one_record_and_the_first_stays(archive):
    first = ha.add_reading(_score(45, "Eagles 10, Giants 7."))
    again = ha.add_reading(_score(45, "Eagles 10, Giants 7. (resent)"))
    assert first == again
    assert _row(archive, "SELECT COUNT(*), MIN(text) FROM live_series") == (
        1, "Eagles 10, Giants 7.")


def test_a_price_on_two_days_is_two_readings(archive):
    for day in (0, 1):
        ha.add_reading(ha.Reading(subject="Apple", metric="price",
                                  as_of=NOW + timedelta(days=day), value=200.0 + day,
                                  unit="USD", text=f"Apple at {200 + day}.",
                                  source="tiingo", domain="markets"))
    series = ha.readings("Apple", "price", NOW - timedelta(days=1), NOW + timedelta(days=2))
    assert [r.value for r in series] == [200.0, 201.0]
    assert ha.readings("Apple", "price", NOW + timedelta(hours=1), NOW + timedelta(hours=2)) == []


@pytest.mark.parametrize("bad", [
    {"metric": "opinion"},
    {"as_of": datetime(2026, 11, 2, 12)},
    {"subject": " "},
    {"text": ""},
])
def test_a_reading_that_is_not_one_is_refused(archive, bad):
    reading = dataclasses.replace(_score(0, "Eagles 0, Giants 0."), **bad)
    with pytest.raises(ValueError):
        ha.add_reading(reading)


def test_reading_a_series_never_raises(archive, monkeypatch):
    def broken(*a, **k):
        raise sqlite3.OperationalError("disk")

    monkeypatch.setattr(archive, "readings", broken)
    assert ha.readings("Apple", "price", NOW, NOW + timedelta(days=1)) == []
    assert ha.readings("Apple", "price", NOW, NOW - timedelta(days=1)) == []


def test_the_counts_report_filed_facts_and_readings(archive):
    ha.add_fact(ha.Fact(url="https://x.com/1", text="A.", published="2025-02-10",
                        node_id="eagles"))
    ha.add_fact(ha.Fact(url="https://x.com/2", text="B.", published="2025-02-10"))
    ha.add_reading(_score(0, "Eagles 0, Giants 0."))
    counts = archive.counts()
    assert (counts["facts"], counts["facts_filed"], counts["readings"]) == (2, 1, 1)


# --- the Atlas backend, against a fake ---------------------------------------

class _Cursor(list):
    def sort(self, field, way):
        return _Cursor(sorted(self, key=lambda d: d[field], reverse=way < 0))

    def limit(self, n):
        return _Cursor(self[:n])


class _Collection:
    def __init__(self):
        self.calls = []
        self.docs = {}
        self.search_indexes = []

    def create_index(self, *a, **k):
        self.calls.append(("create_index", a))

    def update_one(self, query, update, upsert=False):
        self.calls.append(("update_one", query, update, upsert))
        if "$setOnInsert" in update and query["_id"] not in self.docs:
            self.docs[query["_id"]] = {"_id": query["_id"], **update["$setOnInsert"]}

    def replace_one(self, query, doc, upsert=False):
        self.docs[query["_id"]] = doc

    def find(self, query):
        def match(doc):
            for key, want in query.items():
                if isinstance(want, dict):
                    if not want["$gte"] <= doc[key] <= want["$lte"]:
                        return False
                elif doc.get(key) != want:
                    return False
            return True
        return _Cursor(d for d in self.docs.values() if match(d))

    def list_search_indexes(self, name):
        return iter(self.search_indexes)

    def update_search_index(self, name, definition):
        self.calls.append(("update_search_index", name, definition))


class _Database(dict):
    def __missing__(self, name):
        self[name] = _Collection()
        return self[name]


def _indexes(collection):
    return [c[1][0] for c in collection.calls if c[0] == "create_index"]


def test_atlas_indexes_facts_by_node_and_readings_by_subject_and_moment():
    store = ha.AtlasBackend(database=_Database())
    store.ensure_indexes()
    assert [("node_id", 1), ("published", 1)] in _indexes(store.facts_c)
    assert "nodes" in _indexes(store.intake_c)
    assert _indexes(store.series_c) == [[("subject", 1), ("metric", 1), ("as_of", 1)]]


def test_atlas_adds_a_node_only_when_there_is_one():
    store = ha.AtlasBackend(database=_Database())
    store.intake([_finding()], "Eagles", "nfl", NOW.timestamp(), node="eagles")
    store.intake([_finding()], "Eagles", "nfl", NOW.timestamp())
    with_node, without = [c[2] for c in store.intake_c.calls if c[0] == "update_one"]
    assert with_node["$addToSet"]["nodes"] == "eagles"
    assert "nodes" not in without["$addToSet"]
    filed = store.add_fact(ha.Fact(url="https://x.com/1", text="A.", node_id="eagles",
                                   node_path=["nfl", "eagles"]),
                           _unit(), "onnx:384", NOW.timestamp())
    unfiled = store.add_fact(ha.Fact(url="https://x.com/2", text="B."),
                             _unit(), "onnx:384", NOW.timestamp())
    assert store.facts_c.docs[filed]["node_path"] == ["nfl", "eagles"]
    assert "node_id" not in store.facts_c.docs[unfiled]


def test_atlas_keeps_the_first_reading_of_a_moment_and_reads_in_order():
    store = ha.AtlasBackend(database=_Database())
    for minute, text in ((45, "10-7"), (0, "0-0"), (45, "10-7 again")):
        store.add_reading(_score(minute, text), NOW.timestamp())
    series = store.readings("Eagles vs Giants", "score", NOW, NOW + timedelta(hours=1), 50)
    assert [r.text for r in series] == ["0-0", "10-7"]


def test_atlas_updates_a_vector_index_made_before_node_id_was_a_filter():
    store = ha.AtlasBackend(database=_Database())
    old = [f for f in ha.VECTOR_INDEX_DEFINITION["fields"] if f["path"] != "node_id"]
    store.facts_c.search_indexes = [{"queryable": True, "latestDefinition": {"fields": old}}]
    assert store.ensure_vector_index() == "building"
    assert store.facts_c.calls[-1] == ("update_search_index", ha.VECTOR_INDEX,
                                       ha.VECTOR_INDEX_DEFINITION)
    store.facts_c.calls.clear()
    store.facts_c.search_indexes = [{"queryable": True,
                                     "latestDefinition": ha.VECTOR_INDEX_DEFINITION}]
    assert store.ensure_vector_index() == "ready"
    assert not store.facts_c.calls


# --- /admin's query page opens its stores read-only on any SQLite build -------

def test_a_read_only_attachment_works_from_a_uri_connection(tmp_path):
    import admin_tracker

    source = open(admin_tracker.__file__).read()
    assert 'sqlite3.connect("file::memory:", uri=True' in source
