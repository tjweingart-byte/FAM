"""11.12: the admin's Categories page draws the whole tree - every node under
its facet, with the kept episodes Nando filed at it and below."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

import cache as cache_mod
import categories as C
import topics

ADMIN = {"X-Admin-Token": "sekrit"}


@pytest.fixture
def store(tmp_path):
    s = C.CategoryStore(str(tmp_path / "categories.db"))
    C.apply_seed(s)
    return s


def _walk(nodes):
    for n in nodes:
        yield n
        yield from _walk(n["children"])


def test_every_node_is_drawn_once_under_its_own_facet(store):
    out = C.outline(store)
    drawn = [n["id"] for f in out["facets"] for n in _walk(f["children"])]
    assert sorted(drawn) == sorted(store.nodes())
    assert [f["id"] for f in out["facets"]] == list(topics.TAG_LABELS)
    assert out["nodes"] == len(store.nodes()) and out["orphans"] == 0
    for f in out["facets"]:
        assert f["size"] == sum(1 for _ in _walk(f["children"]))
        for n in _walk(f["children"]):
            assert store.ancestors(n["id"])[-1] == f["id"], n["id"]


def test_counts_roll_up_and_a_facet_keeps_its_own(store):
    out = C.outline(store, {"nba": 3, "boston celtics": 2, "basketball": 1,
                            "sports": 4, "not a node": 9})
    sports = next(f for f in out["facets"] if f["id"] == "sports")
    basketball = next(n for n in sports["children"] if n["id"] == "basketball")
    nba = next(n for n in basketball["children"] if n["id"] == "nba")
    assert (nba["episodes"], nba["total"]) == (3, 5)
    assert (basketball["episodes"], basketball["total"]) == (1, 6)
    assert (sports["episodes"], sports["total"]) == (4, 10)
    # A filing the tree no longer holds is not invented into it.
    assert out["episodes"] == 10
    # Busiest branch first.
    assert sports["children"][0]["id"] == "basketball"


def test_a_node_whose_parent_is_gone_is_counted_not_hidden(store):
    store.mint("pickleball injuries", parent_id="pickleball", source="search")
    store._conn().execute("DELETE FROM categories WHERE id = 'pickleball'")
    store.reload()
    out = C.outline(store)
    assert out["orphans"] == 1


def test_an_unreadable_tree_draws_eight_empty_facets():
    out = C.outline(C.EmptyTree())
    assert out["nodes"] == 0 and len(out["facets"]) == 8


def test_the_script_cache_counts_filings_per_node(tmp_path):
    for c in (cache_mod.MemoryScriptCache(),
              cache_mod.SqliteScriptCache(os.path.join(tmp_path, "s.db"))):
        for key, node in (("a", "nba"), ("b", "nba"), ("c", "science"), ("d", "")):
            c.put(key, ["One."], 3600, "q " + key, category_node=node)
        assert c.node_counts() == {"nba": 2, "science": 1}


@pytest.fixture
def client(monkeypatch):
    import app as appmod

    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "sekrit")
    return TestClient(appmod.app)


def test_the_tree_is_for_admins_only(client):
    assert client.get("/api/admin/categories/tree").status_code in (401, 403, 404)


def test_the_admin_reads_the_tree(client):
    topics.category_tree().reload()
    body = client.get("/api/admin/categories/tree", headers=ADMIN).json()
    assert [f["id"] for f in body["facets"]] == list(topics.TAG_LABELS)
    assert {"nodes", "orphans", "sources", "episodes", "max_depth"} <= set(body)


def test_the_page_draws_it():
    page = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "admin_ui", "tracker.html"), encoding="utf-8").read()
    assert 'id="treeCard"' in page and "/api/admin/categories/tree" in page
