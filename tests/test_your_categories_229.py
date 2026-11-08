"""§229: Your categories on myFAM - follow a category, open it, and scroll
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
    # A day that began a day ago, so no test lands either side of midnight.
    monkeypatch.setattr(appmod, "_day_start", lambda: time.time() - 86400)
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


def test_each_episode_carries_what_a_reel_card_draws(client):
    """§253: the category's reel draws the same numbers as Explore's - the
    key for fresh stats and the reaction counts - and still no author."""
    _put("k1", "detroit lions win", "Lions roll on", category="detroit lions")
    ep = _episodes(client, "nfl")["episodes"][0]
    assert ep["key"] == "k1"
    for field in ("vibes", "likes", "comments", "rating", "my_vibe"):
        assert field in ep
    assert "author" not in ep


def test_an_unknown_category_is_a_404(client):
    res = client.get("/api/categories/episodes", params={"id": "not a thing at all"})
    assert res.status_code == 404


def test_the_page_never_writes_an_episode(client, monkeypatch):
    """Cached only: reading a category page never puts anything in the cache."""
    _put("k1", "q1", "Lions", category="detroit lions")

    def refuse(*a, **k):
        raise AssertionError("a category page wrote to the cache")
    monkeypatch.setattr(appmod.SCRIPT_CACHE, "put", refuse)
    for sort in appmod.CATEGORY_SORTS:
        assert _episodes(client, "nfl", sort=sort)["episodes"]
    client.get("/api/categories")


def test_who_made_an_episode_never_leaves_the_server(client):
    _put("k1", "q1", "Lions", category="detroit lions")
    got = _episodes(client, "nfl")["episodes"][0]
    assert "author" not in got and "chain" not in got


def test_a_blocked_listeners_episodes_are_not_shown(client):
    """Screened like every shelf of other people's episodes (§222)."""
    me = _signed_up("blocker@fam.test")
    my_id = me.get("/api/auth/me").json()["user_id"]
    _put("k1", "q1", "Lions", category="detroit lions")       # by "someone"
    _put("k2", "q2", "The deadline", category="nfl")
    appmod.SCRIPT_CACHE.put("k3", ["A."], 600, "q3", "", 2, "", "", "pest",
                            title="Chiefs again", category="nfl", origin="search")
    assert len(_episodes(me, "nfl")["episodes"]) == 3
    appmod.MODERATION.block(my_id, "pest")
    titles = {e["title"] for e in _episodes(me, "nfl")["episodes"]}
    assert titles == {"Lions", "The deadline"}
    # Somebody else still sees it.
    assert len(_episodes(client, "nfl")["episodes"]) == 3


def test_a_taken_down_episode_is_not_shown(client):
    _put("k1", "q1", "Lions", category="detroit lions")
    _put("k2", "q2", "The deadline", category="nfl")
    appmod.MODERATION.hide_episode(appmod._episode_target("q2", 2))
    assert [e["title"] for e in _episodes(client, "nfl")["episodes"]] == ["Lions"]


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
    # "More" on the chip row (the 11.3 packet) finds any node this way.
    assert '(" " + label.toLowerCase()).indexOf(" " + q)' in _fn("renderCategoryFind")


# --- the screens -----------------------------------------------------------


def test_categories_are_a_row_of_chips_above_the_mixes():
    # The 11.3 packet, option 2 of the owner's deck: no grid under the
    # mixes and no category page - a chip row at the top filters in place.
    assert 'id="catChips"' in HTML and 'id="catPanel"' in HTML
    assert HTML.index('id="catChips"') < HTML.index('id="mixesPane"') < HTML.index('id="mixList"')
    assert "loadMyCategories();" in _fn("loadMixes")
    for gone in ('id="catSection"', 'id="catGrid"', 'id="screen-category"',
                 "function openCategory(", "Shuffle play", "screen-catbrowse"):
        assert gone not in HTML, gone


def test_followed_categories_lead_the_chips_with_a_star():
    draw = _fn("renderCategoryChips")
    # All, then followed, then the facets not followed - each chip once -
    # then More.
    assert 'catChipHTML("", "All", true' in draw
    assert draw.index("mine.map(") < draw.index("!followed[f.id]") < draw.index("catchip-more")
    assert "CAT_STAR" in _fn("catChipHTML")


def test_a_chip_filters_the_page_in_place():
    pick = _fn("pickCategory")
    assert "showCategoryPanel(true)" in pick and "navigate(" not in pick
    shown = _fn("showCategoryPanel")
    assert '"catPanel").hidden = !on' in shown and '"mixesPane").hidden = on' in shown
    # Inside a category the row is an X, the category and the level below.
    draw = _fn("renderCategoryChips")
    assert "catPage.subs.map(" in draw and "clearCategory()" in draw
    load = _fn("loadCategoryEpisodes")
    assert "/api/categories/episodes?id=" in load and "&sort=popular" in load
    assert "catPage.subs = d.subcategories" in load


def test_the_panel_is_a_hero_play_all_follow_and_a_list():
    panel = _fn("drawCategoryPanel")
    assert "catp-hero" in panel and "playCategoryEpisode(0)" in panel
    assert "playCategoryAll()" in panel and "toggleFollowCategory()" in panel
    assert "eps.slice(1).map(" in panel


def test_category_episodes_carry_their_picture(monkeypatch):
    import inspect
    assert "_picture_episodes, episodes" in inspect.getsource(appmod.category_episodes)


def test_a_tap_plays_what_is_cached_and_nothing_else():
    # The 11.3 packet: exploreFAM is gone, so a tap plays in the player -
    # replay only, as the catalogue plays one - and the rest of the page
    # follows in the ordinary queue.
    play = _fn("playCategoryEpisode")
    assert "playCategoryList(eps.slice(i))" in play
    listed = _fn("playCategoryList")
    assert "cachedOnly: true" in listed and 'generate("_category")' in listed
    assert "QUEUE.push(categoryQueueEntry(ep))" in listed
    assert "cachedOnly: true" in _fn("categoryQueueEntry")


def test_play_all_plays_the_category_from_the_top():
    assert "playCategoryList(catPage.episodes)" in _fn("playCategoryAll")


def test_no_category_opens_a_reel():
    assert "function openCategoryReel(" not in HTML
    assert 'id="screen-explore"' not in HTML


# --- the §500 review ---------------------------------------------------------

def test_arriving_on_the_screen_opens_on_all():
    body = HTML.split("function openPlayFAM(", 1)[1].split("\n  }\n", 1)[0]
    assert "if(catPage.root || catPage.finding) clearCategory();" in body
    assert body.index("clearCategory()") < body.index("loadMixes()")


def test_a_late_categories_answer_cannot_undo_a_follow():
    load = _fn("loadMyCategories")
    assert "var req = ++catDataReq;" in load and "if(req !== catDataReq) return;" in load
    follow = _fn("toggleFollowCategory")
    assert "++catDataReq" in follow and "req === catDataReq ? d : null" in follow


def test_following_mid_load_does_not_draw_an_empty_category():
    assert "if(!catPage.loading) drawCategoryPanel();" in _fn("toggleFollowCategory")
    load = _fn("loadCategoryEpisodes")
    assert load.count("catPage.loading = false;") == 2 and "catPage.loading = true;" in load


def test_starting_something_else_drops_a_categorys_queued_rest():
    gen = HTML.split("function generate(key, albumContext){", 1)[1][:900]
    assert 'key !== "_category"' in gen and 'indexOf("queue_") !== 0' in gen
    assert "if(QUEUE[qi].fromCategory) QUEUE.splice(qi, 1);" in gen
