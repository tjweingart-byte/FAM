"""The owner's 10.31 notebook page: lower-case, centred tab headings; more
on View more; "N friends listened" on DailyFAM's cards; the best available
picture; and an All feed on exploreFAM. (The boxed item, a new loading
screen for exploreFAM, was left out at the owner's direction.)"""
from __future__ import annotations

import os
import re
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import categories as C  # noqa: E402
import thumbnails as th  # noqa: E402
import topics as T  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as fh:
    INDEX = fh.read()


def _fn(name):
    return INDEX.split("function " + name + "(", 1)[1].split("\n  }\n", 1)[0]


def _rule(selector):
    return re.search(r"\n  " + re.escape(selector) + r"\{([^}]*)\}", INDEX).group(1)


# --- 1. daily / my / your, one face, centred -----------------------------------

def test_the_three_headings_are_lower_case_like_my():
    assert '<h2 class="wordmark" aria-label="dailyFAM">daily<' in INDEX
    assert '<h2 class="wordmark" aria-label="myFAM">my<' in INDEX
    assert '<h2 class="wordmark" aria-label="yourFAM">your' in _fn("yfWordmark")
    assert ">Daily<span" not in INDEX and "'>Your'" not in INDEX


def test_the_three_headings_share_one_rule_and_are_centred():
    rule = _rule(".myfam-header h2, .yf-head .wordmark")
    assert "font-size:28px" in rule and "font-weight:500" in rule
    # YourFAM's own smaller size is gone, so it cannot drift again.
    assert ".yf-head .wordmark{ font-size:23px" not in INDEX
    centred = _rule(".myfam-header-fixed .wordmark, .yf-head .wordmark")
    assert "left:50%" in centred and "translateX(-50%)" in centred
    assert "position:relative" in _rule(".yf-head")


# --- 2. View more shows more ---------------------------------------------------

def test_view_more_deals_sixteen():
    assert "var SECTION_PAGE = 16;" in INDEX
    assert T.VIEW_MORE_PAGE == 16
    body = {"key": "k", "topics": [{"id": f"t{i}"} for i in range(40)]}
    assert len(T.page_section(body)["topics"]) == 16


# --- 3. "7 friends listened" ---------------------------------------------------

def _play(store, user, topic_id="", text="", at=None):
    store.record(T.Event(user, "play", topic_id, text, at=at or time.time()))


def test_friends_listened_counts_people_not_plays(tmp_path):
    store = T.EventStore(str(tmp_path / "e.db"))
    tile = {"id": "tile-1", "query": "What is happening with the Fed?"}
    _play(store, "ana", "tile-1")
    _play(store, "ana", "tile-1")            # one friend, twice
    _play(store, "ben", "", "what is happening with the fed")  # by question
    _play(store, "stranger", "tile-1")      # not a friend
    got = T.friends_listened(store, ["ana", "ben"], [tile])
    assert got == {"tile-1": 2}


def test_no_friend_listened_is_no_field_and_old_plays_do_not_count(tmp_path):
    store = T.EventStore(str(tmp_path / "e.db"))
    _play(store, "ana", "old", at=time.time() - T.FRIENDS_WINDOW - 60)
    tiles = [{"id": "old", "query": "q one"}, {"id": "none", "query": "q two"}]
    assert T.friends_listened(store, ["ana"], tiles) == {}
    assert T.friends_listened(store, [], tiles) == {}


def test_the_feed_marks_tiles_with_a_count_only(monkeypatch, tmp_path):
    store = T.EventStore(str(tmp_path / "e.db"))
    monkeypatch.setattr(appmod, "EVENTS", store)
    monkeypatch.setattr(appmod.SOCIAL, "friends",
                        lambda user, limit=500: [{"user_id": "ana"}])
    _play(store, "ana", "a")
    tiles = [{"id": "a", "query": "x"}, {"id": "b", "query": "y"}]
    faces = appmod._mark_friends_listened("me", tiles)
    assert tiles[0]["friends_listened"] == 1
    assert "friends_listened" not in tiles[1]
    # Faces since §366: an index into the response's list, and nobody's id
    # rides along - a name, a handle and a picture, never a user id.
    assert tiles[0]["friends_faces"] == [0]
    assert set(tiles[0]) == {"id", "query", "friends_listened", "friends_faces"}
    assert faces == [{"name": "", "handle": "", "avatar": ""}]


def test_both_card_shapes_draw_the_chip_and_never_a_zero():
    chip = _fn("seedFriendsHtml")
    assert "if(!n || n < 1) return '';" in chip
    # The words survive only as the fallback for a response without faces.
    assert "' friend'" in chip and "' friends'" in chip and "listened" in chip
    assert "seedFriendsHtml(t)" in _fn("renderSection")
    assert INDEX.count("+ seedFriendsHtml(t)") == 2
    assert "right:8px" in _rule(".seed-card .seed-friends")
    # The subject gives way on a narrow card rather than sitting under it.
    assert "has-friends" in _fn("seedThumbOpen")
    assert "text-overflow:ellipsis" in _rule(".seed-thumb.has-friends .seed-tag")


# --- 4. the best available picture ----------------------------------------------

@pytest.fixture
def tree():
    t = T.category_tree()
    C.apply_seed(t)
    return t


def _paint(node, facet="sports"):
    th.store().put(th.Thumb(node, th.STATUS_APPROVED, facet=facet, mime="image/png"),
                   b"img-" + node.encode())


def test_a_painted_subject_beats_another_subjects_league(tree):
    tree.mint("celtics", parent_id="nba")
    words = "Seahawks fans at a Celtics game"
    named = sorted(tree.match(words), key=lambda n: (-th._depth(tree, n, T.FACETS), n))
    assert named[:2] == ["celtics", "seahawks"], named
    _paint("nba")
    _paint("seahawks")
    # The Celtics are named first and unpainted; the Seahawks have their own
    # picture, which says more than the NBA's.
    assert th.pick_for_player(words)["node"] == "seahawks"


def test_a_category_still_walks_its_own_branch_first(tree):
    tree.mint("sports betting", parent_id="sports")
    _paint("sports")
    _paint("seahawks")
    got = th.pick_for_player("odds this weekend", category="sports betting")
    assert got["node"] == "sports" and got["fallback"]


# --- 5. All on exploreFAM -------------------------------------------------------

def test_all_is_the_first_choice_and_a_shuffled_one():
    feeds = INDEX.split("var EXPLORE_FEEDS = [", 1)[1].split("];", 1)[0]
    assert feeds.strip().splitlines()[0].strip() == '{ id: "all", label: "All", shuffled: true },'
    assert "all" in appmod.EXPLORE_FEEDS


class _Cache:
    def __init__(self, rows):
        self.rows = rows

    def recent(self, n, exclude_author="", origin="", keys=None):
        rows = [r for r in self.rows if r.get("author") != exclude_author]
        if origin:
            rows = [r for r in rows if r.get("origin") == origin]
        if keys is not None:
            rows = [r for r in rows if r["key"] in keys]
        return rows[:n]


def test_all_mixes_every_feed_once_each(monkeypatch):
    rows = [{"key": f"k{i}", "query": f"q{i}", "author": "", "minutes": 1.5,
             "origin": "search" if i % 2 else "myfam"} for i in range(30)]
    rows.append({"key": "mine", "query": "mine", "author": "me", "minutes": 1.5})
    monkeypatch.setattr(appmod, "_explore_rail_entries",
                        lambda request, listener, feed, store: rows[:3])
    got = appmod._explore_all_entries(None, "me", _Cache(rows), 20)
    keys = [e["key"] for e in got]
    assert len(keys) == len(set(keys)) == 20
    assert "mine" not in keys
    # A searched episode and an unsearched one both reach the mix.
    origins = {e["origin"] for e in got}
    assert origins == {"search", "myfam"}
