"""§255: the 10.12 implementations packet.

1. The player's collapse button is the Home logo, and still only minimises.
2. exploreFAM has a dropdown under its wordmark: Made for you, Trending,
   Most recent, What people are searching and Explore; the first three run
   out into the shuffled cached catalogue.
3. The episode library's bookshelf is on search.
4. A category's reel ends in "You're all caught up" with four other
   categories; Shuffle play is smaller.
5. A category's Shuffle play holds only that category's episodes.
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import categories as C  # noqa: E402
import topics as T  # noqa: E402
from cache import SqliteScriptCache  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
HOUSE = 'd="M8.5 12.5L12 9l3.5 3.5M8.5 17L12 13.5l3.5 3.5"'


def _fn(name: str) -> str:
    return HTML.split(f"function {name}(", 1)[1].split("\n  }\n", 1)[0]


def _screen(name: str) -> str:
    start = HTML.index(f'<section class="screen" id="screen-{name}">')
    return HTML[start:HTML.index("</section>", start)]


# --- 1. the Home logo on the player ------------------------------------------

def test_the_players_collapse_button_is_the_home_logo():
    btn = HTML.split('id="playerDown"', 1)[1].split("</button>", 1)[0]
    assert HOUSE in btn, "not DailyFAM's house"
    assert 'd="M6 9l6 6 6-6"' not in btn, "still the down arrow"
    assert 'onclick="minimizePlayer()"' in HTML.split('id="playerDown"', 1)[1][:40]


# --- 2. the feed dropdown -----------------------------------------------------

# --- 3. the bookshelf on search -----------------------------------------------

def test_the_library_bookshelf_is_on_search_not_dailyfam():
    home = _screen("home")
    assert 'id="myfamSearchBtn"' in home and 'onclick="openMyFamSearch()"' in home
    assert 'id="myfamSearchBtn"' not in _screen("myfam")
    assert re.search(r"\.myfam-head-btn\.home-library\{ position:absolute; top:8px; left:38px;", HTML)


# --- 4. all caught up; a smaller Shuffle play ---------------------------------

def test_shuffle_play_is_smaller():
    css = HTML.split(".cat-shuffle{", 1)[1].split("}", 1)[0]
    assert "padding:8px 14px" in css and "font-size:11.5px" in css


# --- 5. only that category ----------------------------------------------------

@pytest.fixture
def tree():
    t = T.category_tree()
    C.apply_seed(t)
    return t


def test_an_uncategorised_episode_is_filed_under_one_branch(monkeypatch, tree):
    """Its words touched Sport and Science; it is filed under one of them,
    never both, so it cannot turn up in the other's Shuffle play."""
    monkeypatch.setattr(appmod, "_episode_category", lambda key: "")
    monkeypatch.setattr(T, "tags_for_text", lambda text: ["sports", "science"])
    node, chain = appmod._filed_under({"key": "k", "title": "t", "query": "q"})
    assert node in ("sports", "science")
    assert len({"sports", "science"} & chain) == 1


def test_a_deeper_match_files_with_its_own_ancestors_only(monkeypatch, tree):
    nodes = tree.nodes()
    deep = next(n for n in nodes if tree.ancestors(n)
                and T._root_facet(n) == "sports")
    monkeypatch.setattr(appmod, "_episode_category", lambda key: "")
    monkeypatch.setattr(T, "tags_for_text", lambda text: [deep, "science"])
    node, chain = appmod._filed_under({"key": "k", "title": "t", "query": "q"})
    assert node == deep and "sports" in chain and "science" not in chain


# --- 2, the server: every feed is the cache -----------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", SqliteScriptCache(str(tmp_path / "e.db")))
    return TestClient(appmod.app)


def _put(key, query, origin):
    appmod.SCRIPT_CACHE.put(key, ["A sentence."], 600, query, "", 2,
                            "", "", "someone-else", origin=origin)


def test_searching_is_still_searches_only_and_the_default(client):
    _put("a", "a searched question", "search")
    _put("b", "a warmed question", "prefetch")
    body = client.get("/api/explore").json()
    assert body["feed"] == "searching"
    assert [e["query"] for e in body["episodes"]] == ["a searched question"]


def test_recent_and_the_catalogue_hold_every_surface(client):
    _put("a", "a searched question", "search")
    _put("b", "a warmed question", "prefetch")
    for feed in ("recent", "catalog"):
        got = {e["query"] for e in client.get(f"/api/explore?feed={feed}").json()["episodes"]}
        assert got == {"a searched question", "a warmed question"}, feed


def test_a_rail_feed_keeps_only_its_cached_tiles_in_order(client, monkeypatch):
    _put(appmod._episode_key(appmod._validated_plan("second tile", appmod.BROWSE_MINUTES)),
         "second tile", "myfam")
    _put(appmod._episode_key(appmod._validated_plan("first tile", appmod.BROWSE_MINUTES)),
         "first tile", "myfam")
    tiles = [{"query": "first tile"}, {"query": "never written"}, {"query": "second tile"}]
    monkeypatch.setattr(T, "guest_section", lambda key: {"topics": tiles})
    monkeypatch.setattr(appmod, "_has_account", lambda request: False)
    got = [e["query"] for e in client.get("/api/explore?feed=trending").json()["episodes"]]
    assert got == ["first tile", "second tile"]


def test_an_unknown_feed_is_the_default(client):
    assert client.get("/api/explore?feed=nonsense").json()["feed"] == "searching"


# --- the pre-merge review -----------------------------------------------------

def test_the_catalogue_sends_at_most_limit(client):
    for i in range(8):
        _put(f"k{i}", f"question number {i}", "myfam")
    got = client.get("/api/explore?feed=catalog&limit=3").json()["episodes"]
    assert len(got) == 3


def test_a_rail_tile_is_looked_up_by_key_however_old(client, monkeypatch):
    """Not hoped for among the newest rows: a tile written before a flood of
    newer episodes is still found."""
    old = appmod._episode_key(appmod._validated_plan("an old tile", appmod.BROWSE_MINUTES))
    _put(old, "an old tile", "myfam")
    for i in range(5):
        _put(f"new{i}", f"a newer question {i}", "prefetch")
    monkeypatch.setattr(appmod, "EXPLORE_CATALOG_SCAN", 2)
    monkeypatch.setattr(T, "guest_section", lambda key: {"topics": [{"query": "an old tile"}]})
    monkeypatch.setattr(appmod, "_has_account", lambda request: False)
    got = [e["query"] for e in client.get("/api/explore?feed=made_for_you").json()["episodes"]]
    assert got == ["an old tile"]


@pytest.mark.parametrize("make", ["memory", "sqlite"])
def test_recent_keeps_only_the_keys_asked_for(make, tmp_path):
    from cache import MemoryScriptCache
    store = MemoryScriptCache() if make == "memory" else SqliteScriptCache(str(tmp_path / "r.db"))
    for k in ("a", "b", "c"):
        store.put(k, ["A sentence."], 600, f"question {k}", "", 2, origin="search")
    assert {e["key"] for e in store.recent(10, keys=["a", "c"])} == {"a", "c"}
    assert len(store.recent(10)) == 3

