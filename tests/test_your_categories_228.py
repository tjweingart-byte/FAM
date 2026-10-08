"""§228: Your categories on myFAM - follow a category, open it, and scroll
the episodes made in it today, sorted by popularity, A to Z or newest, and
searched by title. Cached only: nothing here ever writes an episode."""
from __future__ import annotations

import os
import pathlib
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import categories as C  # noqa: E402
import mixes as M  # noqa: E402
import preferences as P  # noqa: E402
import topics as T  # noqa: E402
from cache import SqliteScriptCache  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
PASSWORD = "a-long-enough-password"


def _fn(name: str) -> str:
    return HTML.split(f"function {name}(", 1)[1].split("\n  }\n", 1)[0]


@pytest.fixture
def tree():
    t = T.category_tree()
    C.apply_seed(t)
    return t


@pytest.fixture
def client(monkeypatch, tmp_path, tree):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", SqliteScriptCache(str(tmp_path / "c.db")))
    monkeypatch.setattr(appmod, "MIXES", M.MixStore(str(tmp_path / "m.db")))
    monkeypatch.setattr(appmod, "PREFS", P.PreferenceStore(str(tmp_path / "p.db")))
    appmod._CATEGORY_MEMO.clear()
    appmod._CATEGORY_MEMO["at"] = 0.0
    return TestClient(appmod.app)


def _signed_up(email: str) -> TestClient:
    c = TestClient(appmod.app)
    res = c.post("/api/auth/signup", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return c


def _put(key, query, title, category="", plays=0, age=0.0):
    cache = appmod.SCRIPT_CACHE
    cache.put(key, ["A sentence."], 600, query, "", 2, "", "", "someone",
              title=title, category=category, origin="search")
    with cache._conn() as conn:
        conn.execute("UPDATE scripts SET plays = ?, created = ? WHERE key = ?",
                     (plays, time.time() - age, key))


def _episodes(client, node, **params):
    res = client.get("/api/categories/episodes", params={"id": node, **params})
    assert res.status_code == 200, res.text
    return res.json()


# --- the episodes ----------------------------------------------------------


def test_a_category_holds_its_own_episodes_and_everything_under_it(client):
    _put("k1", "detroit lions win", "Lions roll on", category="detroit lions")
    _put("k2", "nfl trade deadline", "The trade deadline", category="nfl")
    _put("k3", "fed rates", "The Fed holds", category="federal reserve")
    got = _episodes(client, "nfl")
    assert {e["title"] for e in got["episodes"]} == {"Lions roll on", "The trade deadline"}
    sport = _episodes(client, "sports")
    assert {e["title"] for e in sport["episodes"]} == {"Lions roll on", "The trade deadline"}
    money = _episodes(client, "money")
    assert [e["title"] for e in money["episodes"]] == ["The Fed holds"]


def test_only_todays_episodes(client):
    _put("k1", "nfl today", "Today in the NFL", category="nfl")
    _put("k2", "nfl old", "Two days ago in the NFL", category="nfl", age=2 * 86400)
    got = _episodes(client, "nfl")
    assert [e["title"] for e in got["episodes"]] == ["Today in the NFL"]


def test_the_three_orders(client):
    _put("k1", "q one", "Bravo", category="nfl", plays=1, age=30)
    _put("k2", "q two", "alpha", category="nfl", plays=5, age=20)
    _put("k3", "q three", "Charlie", category="nfl", plays=3, age=10)
    titles = lambda sort: [e["title"] for e in _episodes(client, "nfl", sort=sort)["episodes"]]  # noqa: E731
    assert titles("popular") == ["alpha", "Charlie", "Bravo"]
    assert titles("az") == ["alpha", "Bravo", "Charlie"]
    assert titles("recent") == ["Charlie", "alpha", "Bravo"]
    # Anything else is the default rather than an error.
    assert _episodes(client, "nfl", sort="sideways")["sort"] == "popular"


def test_popularity_counts_the_mixes_that_follow_it(client):
    """An edition episode nobody has tapped yet is ranked by the mixes it
    lives in: listens plus instances in a mix."""
    import daily_edition

    owner = "listener-1"
    mix = appmod.MIXES.create(owner, "Sunday", ["f:nfl~Lions"])
    item = mix.items[0]
    words = daily_edition.prompt_for(item)
    _put("edition", words, "The Lions this week", category="detroit lions", plays=0)
    _put("played", "nfl other", "Another NFL story", category="nfl", plays=0)
    got = _episodes(client, "nfl")["episodes"]
    assert got[0]["title"] == "The Lions this week"
    assert got[0]["mixes"] == 1 and got[0]["plays"] == 0 and got[0]["popularity"] == 1


def test_search_inside_a_category_reads_the_title(client):
    _put("k1", "q1", "Detroit Lions stay unbeaten", category="detroit lions")
    _put("k2", "q2", "Chiefs hold off the Bills", category="nfl")
    got = _episodes(client, "nfl", q="detroit lio")["episodes"]
    assert [e["title"] for e in got] == ["Detroit Lions stay unbeaten"]
    assert _episodes(client, "nfl", q="eagles")["episodes"] == []


def test_subcategories_are_the_level_below_that_has_something_today(client):
    _put("k1", "q1", "Lions", category="detroit lions")
    got = _episodes(client, "american football")
    assert [s["id"] for s in got["subcategories"]] == ["nfl"]
    assert got["subcategories"][0]["count"] == 1


def test_an_episode_without_a_written_category_is_filed_by_its_words(client):
    _put("k1", "what the federal reserve did", "Rates on hold")
    got = _episodes(client, "money")["episodes"]
    assert [e["title"] for e in got] == ["Rates on hold"]


def test_an_unknown_category_is_a_404(client):
    res = client.get("/api/categories/episodes", params={"id": "not a thing at all"})
    assert res.status_code == 404


def test_the_page_never_writes_an_episode(client, monkeypatch):
    """Cached only: no pipeline is built and nothing is put in the cache."""
    monkeypatch.setattr(appmod, "_pipeline_for", None, raising=False)
    _put("k1", "q1", "Lions", category="detroit lions")
    before = len(appmod.SCRIPT_CACHE.recent(100))
    _episodes(client, "nfl")
    assert len(appmod.SCRIPT_CACHE.recent(100)) == before


# --- following -------------------------------------------------------------


def test_following_needs_an_account(client):
    res = client.post("/api/categories/mine", json={"categories": ["money"]})
    assert res.status_code == 401


def test_follow_and_read_back_with_todays_count(client):
    me = _signed_up("cats@fam.test")
    _put("k1", "q1", "Lions", category="detroit lions")
    res = me.post("/api/categories/mine", json={"categories": ["nfl", "money", "nfl"]})
    assert res.status_code == 200, res.text
    assert res.json()["categories"] == ["nfl", "money"]
    got = me.get("/api/categories").json()
    assert got["saved"] is True
    assert [(m["id"], m["today"]) for m in got["mine"]] == [("nfl", 1), ("money", 0)]
    assert [f["id"] for f in got["facets"]] == list(T.TAG_LABELS)
    nfl = next(n for n in got["all"] if n["id"] == "nfl")
    assert nfl["label"] == "NFL"
    assert nfl["path"] == ["Sport", "American Football"]


def test_an_unknown_category_is_refused_not_stored(client):
    me = _signed_up("cats2@fam.test")
    res = me.post("/api/categories/mine", json={"categories": ["no such node here"]})
    assert res.status_code == 400


def test_following_is_not_a_taste_signal(client, monkeypatch):
    me = _signed_up("cats3@fam.test")
    recorded = []
    monkeypatch.setattr(appmod.EVENTS, "record", lambda e: recorded.append(e))
    me.post("/api/categories/mine", json={"categories": ["money"]})
    assert recorded == []


def test_preferences_keep_the_categories(tmp_path, tree):
    store = P.PreferenceStore(str(tmp_path / "p.db"))
    store.save("u", categories=["tech", "nfl"])
    assert store.get("u").categories == ("tech", "nfl")
    store.save("u", interests=["sports"])
    assert store.get("u").categories == ("tech", "nfl")
    with pytest.raises(P.PreferenceError):
        P.clean_categories(["no such node here"])


def test_labels_read_as_headings():
    assert appmod._category_label("nfl") == "NFL"
    assert appmod._category_label("elections and politics") == "Elections and Politics"
    assert appmod._category_label("money") == "Money & markets"


def test_finding_a_category_matches_from_the_start_of_a_word():
    assert '(" " + label.toLowerCase()).indexOf(" " + q)' in _fn("drawCategoryBrowse")


# --- the screens -----------------------------------------------------------


def test_the_mixes_screen_draws_your_categories():
    assert 'id="catSection"' in HTML
    assert "loadMyCategories();" in _fn("loadMixes")
    head = _fn("categoryHeadHTML")
    assert "Your categories" in head and "Browse all" in head
    assert "categoryHeadHTML()" in _fn("renderMyCategories")


def test_the_category_page_sorts_and_searches_on_the_server():
    load = _fn("loadCategoryEpisodes")
    assert "/api/categories/episodes?id=" in load
    assert "&sort=" in load and "&q=" in load
    for sort in ("popular", "az", "recent"):
        assert f'data-sort="{sort}"' in HTML


def test_a_tap_plays_what_is_cached_and_nothing_else():
    play = _fn("playCategoryEpisode")
    assert "cachedOnly: true" in play
