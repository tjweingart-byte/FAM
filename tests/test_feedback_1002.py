"""10.2 feedback: every episode has a picture on the player and the mini
player, the picture centred between GO DEEPER and the title, and Search
DailyFAM opening on an A to Z catalogue."""
from __future__ import annotations

import os
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import categories as C  # noqa: E402
import thumbnails as th  # noqa: E402
import topics as T  # noqa: E402
from cache import SqliteScriptCache  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    return HTML.split(f"function {name}(", 1)[1].split("\n  }\n", 1)[0]


@pytest.fixture
def tree():
    t = T.category_tree()
    C.apply_seed(t)
    return t


def _put(node, facet="sports"):
    th.store().put(th.Thumb(node, th.STATUS_APPROVED, facet=facet, mime="image/png"),
                   b"img-" + node.encode())


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "SCRIPT_CACHE", SqliteScriptCache(str(tmp_path / "f.db")))
    return TestClient(appmod.app)


# --- 1. a searched episode has a picture -----------------------------------


def test_the_player_uses_the_tiles_own_picture_first(tree):
    _put("sports")
    _put("college football")
    got = th.pick_for_player("how college football money changed")
    assert got["node"] == "college football" and got["fallback"] is False


def test_the_player_borrows_the_branch_when_its_node_is_unpainted(tree):
    """Unlike a rail (9.30 #7), the player borrows: one episode at a time
    cannot put one picture on every tile under a branch."""
    _put("sports")
    assert th.pick("how college football money changed", ()) is None
    got = th.pick_for_player("how college football money changed")
    assert got["node"] == "sports" and got["fallback"] is True


def test_a_question_the_tree_knows_nothing_about_still_gets_a_picture(tree):
    _put("tech", facet="tech")
    _put("health", facet="health")
    words = "why gen z took up grandma hobbies"
    got = th.pick_for_player(words)
    assert got["node"] in {"tech", "health"} and got["fallback"] is True
    # The same episode always wears the same one.
    assert th.pick_for_player(words) == got


def test_nothing_approved_means_no_picture(tree):
    assert th.pick_for_player("why gen z took up grandma hobbies") is None


def test_the_card_says_when_its_picture_was_borrowed(client, tree):
    _put("sports")
    out = client.get("/api/episode/card",
                     params={"q": "why gen z took up grandma hobbies"}).json()
    assert out["thumb"].startswith("/api/thumb/sports")
    assert out["fallback"] is True


def test_a_borrowed_picture_is_asked_for_again_when_the_title_lands():
    assert "refreshPlayerCard(title);" in _fn("applyEpisodeTitle")
    refresh = _fn("refreshPlayerCard")
    # Every title landing asks again (§247) - but never over the picture of
    # the tile that opened the player - and a borrowed picture is still
    # replaced by any answer.
    assert "PLAYER_CARD.fromTile) return;" in refresh
    assert "PLAYER_CARD.borrowed && !PLAYER_CARD.placed" in _fn("askPlayerCard")


# --- 2. the picture is centred between GO DEEPER and the title -------------


def test_the_picture_sits_in_the_stage_centred():
    stage = HTML.split('<div class="mini-stage" id="playerStage">', 1)[1] \
        .split('<div class="p-titlerow">', 1)[0]
    assert 'id="playerBg"' in stage
    css = HTML.split("  #screen-player .player-bg{", 1)[1].split("}", 1)[0]
    assert "top:50%" in css and "translateY(-50%)" in css
    # The top row stays over a picture taller than the stage.
    assert "#screen-player > .player-top{ z-index:2; }" in HTML


# --- 3. the mini player shows the picture, never cut top or bottom ---------


def test_the_mini_player_shows_the_players_picture():
    assert "paintNowThumb();" in _fn("showNowBar")
    assert "if(nowBarState) paintNowThumb();" in _fn("drawPlayerPicture")
    assert "PLAYER_CARD.thumb" in _fn("paintNowThumb")
    css = HTML.split("  .nowbar-thumb.has-img{", 1)[1].split("}", 1)[0]
    # Height fits the box; only the sides are cropped.
    assert "auto 100%" in css and "cover" not in css


# --- 4. Search DailyFAM opens on an A to Z catalogue ------------------------


def test_the_catalogue_is_other_peoples_episodes_a_to_z(client):
    me = client.get("/api/auth/me").json()["user_id"]
    put = appmod.SCRIPT_CACHE.put
    put("k1", ["A."], 600, "why volcanoes erupt", "", 2, "", "", "other", origin="search")
    put("k2", ["A."], 600, "apples in autumn", "", 2, "", "", "other", origin="myfam")
    put("k3", ["A."], 600, "the bond market", "", 2, "", "", "other", origin="search")
    put("k4", ["A."], 600, "1999 in film", "", 2, "", "", "other", origin="search")
    put("mine", ["A."], 600, "aardvarks", "", 2, "", "", me, origin="search")
    got = client.get("/api/myfam/catalog").json()["episodes"]
    assert [e["title"] for e in got] == [
        "Apples in autumn", "The bond market", "Why volcanoes erupt", "1999 in film"]
    assert [e["letter"] for e in got] == ["A", "T", "W", "#"]


def test_the_screen_opens_on_the_catalogue_with_letters():
    assert "loadMyFamCatalog();" in _fn("openMyFamSearch")
    assert 'fetch("/api/myfam/catalog")' in _fn("loadMyFamCatalog")
    draw = _fn("drawMyFamSearch")
    assert "mfsResults = mfsCatalog || [];" in draw
    assert 'class="mfs-letter"' in draw


def test_the_writers_title_never_swaps_one_borrowed_picture_for_another(tree):
    """Review: the stable choice hashes the question only, so asking again
    with the writer's title cannot land on a different borrowed picture."""
    for facet in ("tech", "health", "culture", "money"):
        _put(facet, facet=facet)
    q = "why gen z took up grandma hobbies"
    first = th.pick_for_player(q, key=q)
    for title in ("The Knitting Comeback", "Crochet, Quietly", "Slow Hands"):
        later = th.pick_for_player(f"{q} {title}", key=q)
        assert later == first


def test_a_catalogue_that_failed_says_so():
    load = _fn("loadMyFamCatalog")
    assert "Could not load the episodes right now." in load
    assert "mfsCatalog = []" not in load
