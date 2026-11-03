"""Phase 2, piece 6: Sonic, the retriever (§403).

Against the SQLite archive and a fake Atlas (no network): read-only by
construction, the lookup chosen and never written, node then family then
meaning then nothing, history only from before the search's window, the
packet oldest first, and today's recall when he cannot run.
"""
from __future__ import annotations

import dataclasses
import inspect
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import history_archive as ha  # noqa: E402
from agents import retriever as sonic  # noqa: E402
from agents.handoffs import ArchiveRequest, ResearchPlan  # noqa: E402

NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)

_WORDS = {"eagles": 1, "giants": 2, "budget": 3, "council": 3, "apple": 4, "stock": 4,
          "score": 5, "last": 5, "won": 5, "meeting": 5, "who": 6, "history": 6}


def _embed(text):
    hot = sorted({d for w, d in _WORDS.items() if w in text.lower()}) or [0]
    v = [0.0] * ha.DIMS
    for i in hot:
        v[i] = 1.0
    norm = sum(x * x for x in v) ** 0.5
    return [x / norm for x in v], f"onnx:{ha.DIMS}"


#: child -> parent
TREE = {"sports": "", "nfl": "sports", "eagles": "nfl", "giants": "nfl",
        "politics": ""}


@pytest.fixture
def archive(monkeypatch, tmp_path):
    import topics

    ha.reset()
    sonic.reset()
    sonic._ROUTER[0] = None
    changed = dataclasses.replace(ha.settings, archive=True, archive_backend="sqlite",
                                  archive_recall_limit=6, archive_min_similarity=0.45)
    monkeypatch.setattr(ha, "settings", changed)
    monkeypatch.setattr(sonic, "settings", changed)
    monkeypatch.setattr(ha, "_embed", _embed)
    nodes = {n: SimpleNamespace(parent_id=p) for n, p in TREE.items()}
    tree = SimpleNamespace(get=nodes.get, nodes=lambda: dict(nodes),
                           ancestors=lambda n: [])
    monkeypatch.setattr(topics, "category_tree", lambda: tree)
    path = str(tmp_path / "archive.db")
    store = ha.SQLiteBackend(path)
    ha.use(store)
    sonic.use(sonic.ReadOnlySQL(path))
    yield store
    ha.reset()
    sonic.reset()
    sonic._ROUTER[0] = None


def _fact(text, published, node="eagles", subject="Eagles", kind="sentence", url=None):
    return ha.add_fact(ha.Fact(url=url or f"https://x.com/{abs(hash(text))}", text=text,
                               title="T", host="x.com", grade="established",
                               published=published, kind=kind, subject=subject,
                               node_id=node))


def _brief(**kw):
    fields = dict(subject="Eagles", live_domain="", live_entity="",
                  outcome_dependent=False, place="", contextualized=None, freshness="")
    fields.update(kw)
    return SimpleNamespace(**fields)


def _plan(node="eagles", lookup="background", entity="Eagles", **kw):
    return ResearchPlan(entity=entity, node_id=node,
                        archive=ArchiveRequest(node_id=node, lookup=lookup, **kw))


def _retrieve(plan, brief, text="eagles", notes=None):
    return sonic.retrieve(plan, brief, text, notes=notes, now=NOW)


# --- read-only, by construction ----------------------------------------------

def test_sonic_s_stores_have_no_way_to_write():
    writes = re.compile(r"add|write|insert|update|delete|supersede|set_|intake|purge|"
                        r"restore|replace|mark|create")
    for cls in (sonic.ReadOnlySQL, sonic.ReadOnlyAtlas):
        methods = [n for n, _ in inspect.getmembers(cls, inspect.isfunction)
                   if not n.startswith("__")]
        assert not [m for m in methods if writes.search(m)], cls


@pytest.mark.parametrize("sql", ["DELETE FROM facts", "UPDATE facts SET active = 0",
                                 "INSERT INTO facts VALUES (1)", "DROP TABLE facts",
                                 "SELECT 1; DELETE FROM facts", "  pragma writable_schema=1"])
def test_the_gate_refuses_anything_but_a_read(sql):
    with pytest.raises(sonic.ReadOnly):
        sonic._gate(sql)


# The file's own `mode=ro`. On Postgres the archive is a schema and the gate
# above is what holds Sonic to reads (agents/retriever.py, §243), so this is a
# test of the SQLite file and is skipped there (§424).
@pytest.mark.sqlite_file
def test_the_file_is_opened_read_only(archive):
    _fact("The Eagles won.", "2025-01-10")
    with pytest.raises(sqlite3.OperationalError):
        conn = sonic.ReadOnlySQL(archive.path)._connect()
        conn.execute("DELETE FROM facts")


def test_sonic_never_reads_intake():
    source = inspect.getsource(sonic)
    assert not re.search(r"FROM\s+intake|intake_c|\[\"intake\"\]", source)


def test_on_atlas_he_uses_only_his_own_read_only_user(monkeypatch):
    changed = dataclasses.replace(sonic.settings, archive=True, archive_backend="atlas")
    monkeypatch.setattr(sonic, "settings", changed)
    monkeypatch.setattr(ha, "settings", changed)
    monkeypatch.setattr(ha, "enabled", lambda: True)
    monkeypatch.setenv("MONGODB_URI", "mongodb+srv://writer@x")
    monkeypatch.delenv("MONGODB_READ_URI", raising=False)
    sonic.reset()
    assert sonic.reader() is None
    assert "MONGODB_READ_URI" in sonic.report()["connection"]
    opened = []
    monkeypatch.setattr(sonic, "ReadOnlyAtlas", lambda uri: opened.append(uri) or "store")
    monkeypatch.setenv("MONGODB_READ_URI", "mongodb+srv://reader@x")
    assert sonic.reader() == "store" and opened == ["mongodb+srv://reader@x"]


# --- choosing the lookup --------------------------------------------------------

def test_c3p0_s_named_lookup_comes_first(archive):
    brief = _brief(contextualized={"archive": {"lookup": "series"}})
    assert sonic.choose(_plan(lookup="series"), brief, "who is he") == ("series", "c3p0")


def test_without_c3p0_the_router_then_code_choose(archive, monkeypatch):
    brief = _brief()
    lookup, by = sonic.choose(_plan(), brief, "who won the last meeting")
    assert (lookup, by) == ("result_between", "router")
    monkeypatch.setattr(sonic, "route", lambda text: ("", 0.0))
    markets = _brief(live_domain="markets")
    assert sonic.choose(_plan(), markets, "zzz") == ("series", "code")
    assert sonic.choose(_plan(), brief, "zzz") == ("background", "code")


# --- where he looks ------------------------------------------------------------------

def test_the_node_first_oldest_first(archive):
    _fact("The Eagles won the Super Bowl.", "2025-02-10")
    _fact("The Eagles signed a kicker.", "2025-06-01")
    _fact("The council passed a budget.", "2025-03-01", node="politics", subject="council")
    facts = _retrieve(_plan(), _brief())
    assert [f.text for f in facts] == ["The Eagles won the Super Bowl.",
                                       "The Eagles signed a kicker."]


def test_a_thin_node_reads_its_parent_and_children(archive):
    _fact("The Eagles won the Super Bowl.", "2025-02-10")
    _fact("The NFL changed its kickoff rule.", "2025-04-01", node="nfl", subject="NFL")
    facts = _retrieve(_plan(), _brief())
    assert {f.node_id for f in facts} == {"eagles", "nfl"}


def test_an_empty_node_falls_to_meaning_then_to_nothing(archive):
    _fact("The Giants lost to the Eagles.", "2025-02-10", node="giants", subject="Giants")
    facts = _retrieve(_plan(node="bears"), _brief(), text="eagles giants")
    assert [f.node_id for f in facts] == ["giants"]
    assert _retrieve(_plan(node="bears"), _brief(), text="the weather in nowhere") == []


def test_nothing_from_inside_the_search_s_window(archive):
    _fact("The Eagles signed a kicker yesterday.", "2026-11-01")
    _fact("The Eagles won the Super Bowl.", "2025-02-10")
    # The search's window is the brief's (a week here); inside it is the
    # search's job, never history's.
    facts = _retrieve(_plan(), _brief(recency_days=7))
    assert [f.text for f in facts] == ["The Eagles won the Super Bowl."]


def test_results_come_from_the_live_series_oldest_first(archive):
    for day, line in ((20, "Eagles 10, Giants 7."), (5, "Eagles 3, Giants 0.")):
        ha.add_reading(ha.Reading(subject="Eagles vs Giants", metric="score",
                                  as_of=NOW - timedelta(days=day), text=line,
                                  status="final", source="balldontlie", domain="sports"))
    brief = _brief(live_domain="sports")
    facts = _retrieve(_plan(lookup="result_between"), brief)
    assert [f.text for f in facts] == ["Eagles 10, Giants 7.", "Eagles 3, Giants 0."]
    assert all(f.kind == "live" and f.grade == "primary" for f in facts)


def test_a_period_after_the_window_is_dropped(archive):
    request = SimpleNamespace(start=(NOW + timedelta(days=3)).date().isoformat(),
                              end=(NOW + timedelta(days=9)).date().isoformat())
    assert sonic._period(request, NOW - timedelta(days=7)) == (None, None)


# --- the packet -----------------------------------------------------------------------

def test_the_packet_for_shakespeare_is_dated_and_oldest_first(archive, monkeypatch):
    _fact("The Eagles signed a kicker.", "2025-06-01")
    _fact("The Eagles won the Super Bowl.", "2025-02-10", kind="figure")
    notes = SimpleNamespace()
    monkeypatch.setattr(sonic, "route", lambda text: ("background", 0.9))
    _retrieve(_plan(), _brief(), notes=notes)
    dates = [item.date for item in notes.archive_packet.items]
    assert dates == ["2025-02-10", "2025-06-01"]
    assert notes.archive_packet.items[0].kind == "figure"
    assert notes.archive_lookup == {"lookup": "background", "chosen_by": "router",
                                    "items": 2}


# --- falling back ------------------------------------------------------------------------

def test_without_a_read_only_connection_it_is_today_s_recall(archive, monkeypatch):
    monkeypatch.setattr(sonic, "reader", lambda: None)
    calls = []
    monkeypatch.setattr(ha, "recall", lambda text, window: calls.append(text) or ["old"])
    assert _retrieve(_plan(), _brief()) == ["old"]
    assert calls == ["eagles"] and sonic.STATS["fell_back"] == 1


def test_weather_and_a_town_s_news_are_never_looked_up(archive):
    assert sonic.look(_plan(), _brief(live_domain="weather"), "rain") is None
    assert sonic.look(_plan(), _brief(place="Exton, PA"), "news") is None


def test_the_search_uses_sonic_only_when_netenyahu_lets_him_run():
    import script_generator

    source = inspect.getsource(script_generator)
    at = source.index('if _agent_runs(notes, "retriever"):')
    block = source[at:at + 700]
    assert "retriever.look(" in block and "history_archive.look(recall_text" in block


# --- the fake Atlas --------------------------------------------------------------------------

class _Cursor(list):
    def sort(self, *a):
        return self

    def limit(self, n):
        return _Cursor(self[:n])


class _Facts:
    def __init__(self):
        self.finds, self.pipelines = [], []

    def find(self, query, projection=None):
        self.finds.append((query, projection))
        return _Cursor([])

    def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        return iter([])


def test_atlas_reads_by_node_before_the_window_and_never_the_vectors():
    facts = _Facts()
    store = sonic.ReadOnlyAtlas(database={"facts": facts, "live_series": _Facts()})
    before = NOW - timedelta(days=7)
    store.node_facts(["eagles", "nfl"], before, 6)
    query, projection = facts.finds[0]
    # Under its own node, or one of the others it is filed under (§413).
    assert query["$or"] == [{"node_id": {"$in": ["eagles", "nfl"]}},
                            {"also_nodes": {"$in": ["eagles", "nfl"]}}]
    assert query["published"] == {"$lt": before} and projection == {"embedding": 0}
    store.by_meaning([1.0] * 4, "onnx:4", before, 6, 0.45, nodes=["eagles"])
    stage = facts.pipelines[0][0]["$vectorSearch"]
    assert stage["filter"]["node_id"] == {"$in": ["eagles"]}
