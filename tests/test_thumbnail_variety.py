"""Picture variety and picture attachment (the owner, 11.9).

"This rock is everywhere": one picture per node, a broad node (sports,
football, elections and politics) on every episode filed or borrowed there,
and the facet order alphabetical, so "PGA Tour" - culture by "tour", sport by
the league - wore culture's picture. What these pin:

* a broad node holds several pictures (`node#2`, ...) and an episode wears
  one of them chosen by its words - the same one every time, different ones
  across episodes;
* the sweep paints every node's first picture before any extra, and tells
  the scene writer what a node's other pictures already show;
* the facets a picture falls back to are strongest first, never
  alphabetical, and a sport's shared words ("tour") do not make it culture;
* nothing placed means no picture, never a hashed one;
* headlines' short names ("Celtics", "PGA Tour", "Senate") reach the tree.
"""
from __future__ import annotations

import asyncio
import dataclasses
import io
import os
import sys
from collections import Counter

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories as C  # noqa: E402
import category_seed as S  # noqa: E402
import config  # noqa: E402
import thumbnails as th  # noqa: E402
import topics as T  # noqa: E402


def _png(color=(200, 120, 80)) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (1024, 768), color).save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def tree():
    t = T.category_tree()
    C.apply_seed(t)
    t.reload()
    th._MEMO.clear()
    th._bump_generation()
    return t


def _put(row_id, status=th.STATUS_APPROVED, facet="sports", scene=""):
    th.store().put(th.Thumb(row_id, status, facet=facet, mime="image/png",
                            scene=scene), b"img-" + row_id.encode())


# --- several pictures on one node ------------------------------------------


def test_picture_ids_join_a_node_and_a_number():
    assert th.picture_id("sports", 1) == "sports"
    assert th.picture_id("sports", 3) == "sports#3"
    assert th.node_of("sports#3") == "sports"
    assert th.node_of("college football") == "college football"
    assert th.picture_number("sports#3") == 3
    assert th.picture_number("sports") == 1


def test_extra_pictures_belong_to_their_node(tree):
    _put("sports")
    _put("sports#2")
    _put("sports#3")
    _put("sports#4", status=th.STATUS_REVIEW)
    held = th.store()
    # The node is painted once, whatever number of pictures it has.
    assert set(held.approved()) == {"sports"}
    assert [p[0] for p in held.pictures("sports")] == \
        ["sports", "sports#2", "sports#3"]


def test_an_episode_always_wears_the_same_one_of_its_nodes_pictures(tree):
    for row in ("football", "football#2", "football#3", "football#4"):
        _put(row)
    titles = [f"a football story number {i}" for i in range(60)]
    worn = [th.pick_for_player(t, category="football")["picture"]
            for t in titles]
    # Stable: asked again, every episode wears what it wore.
    th._MEMO.clear()
    assert worn == [th.pick_for_player(t, category="football")["picture"]
                    for t in titles]
    # Spread: every picture is used, none by most episodes.
    counts = Counter(worn)
    assert set(counts) == {"football", "football#2", "football#3",
                           "football#4"}
    assert max(counts.values()) < len(titles) / 2
    # The url is the chosen picture's own.
    one = th.pick_for_player(titles[0], category="football")
    assert one["url"].startswith(th.url_for(one["picture"]).split("?")[0])


def test_the_tile_and_the_player_wear_the_same_picture(tree):
    for row in ("football", "football#2", "football#3"):
        _put(row)
    topic = T.Topic(id="x", title="A derby to remember", subtitle="",
                    query="the north london derby", tags=("sports",),
                    icon="sports", category="football")
    from cache import normalize_query

    tile = topic.as_dict()["thumb"]
    player = th.pick_for_player(f"{topic.query} {topic.title}",
                                key=normalize_query(topic.query),
                                category="football")["url"]
    assert tile == player


def test_a_borrowed_picture_is_one_of_the_ancestors_pictures(tree):
    _put("sports")
    _put("sports#2")
    got = th.pick_for_player("a college football story",
                             category="college football")
    assert got["fallback"] is True and got["node"] == "sports"
    assert got["picture"] in {"sports", "sports#2"}


def test_only_extras_live_still_paints_the_node(tree):
    _put("sports", status=th.STATUS_REJECTED)
    _put("sports#2")
    got = th.pick_for_player("a sports story", category="sports")
    assert got["picture"] == "sports#2"
    assert "sports%232" in got["url"]


def test_the_picture_route_serves_an_extra(tree):
    from fastapi.testclient import TestClient

    import app as appmod

    _put("sports#2")
    response = TestClient(appmod.app).get("/api/thumb/sports%232")
    assert response.status_code == 200
    assert response.content == b"img-sports#2"


# --- painting the extras ----------------------------------------------------


def _set(monkeypatch, **values):
    monkeypatch.setattr(config, "settings",
                        dataclasses.replace(config.settings, **values))


def test_extras_are_wanted_for_broad_nodes_after_every_first_picture(
        tree, monkeypatch):
    _set(monkeypatch, thumbnails_broad_pictures=3)
    for facet in T.FACETS:
        _put(facet, facet=facet)
    _put("football")
    _put("college football")
    todo = th.wanted(tree, th.store())
    extras = [n for n in todo if th.node_of(n) != n]
    firsts = [n for n in todo if th.node_of(n) == n]
    # Every unpainted node's first picture comes before any extra.
    assert todo[:len(firsts)] == firsts
    assert "sports#2" in extras and "sports#3" in extras
    assert "football#2" in extras and "football#3" in extras
    assert "sports#4" not in extras
    # A node deeper than one level under a facet holds one picture.
    assert not any(th.node_of(n) == "college football" for n in extras)
    # A broad node with no first picture yet gets no extras.
    assert not any(th.node_of(n) == "basketball" for n in extras)


def test_one_picture_turns_extras_off(tree, monkeypatch):
    _set(monkeypatch, thumbnails_broad_pictures=1)
    _put("sports")
    assert not [n for n in th.wanted(tree, th.store())
                if th.node_of(n) != n]


def test_the_writer_is_told_what_the_other_pictures_show(tree):
    _put("football", scene="a floodlit pitch at night, seen from the stands")
    line = th.writer_line(tree, th.store(), "football#2")
    assert line.startswith("sports > football #2")
    assert "a floodlit pitch at night" in line
    assert th.scene_row("football #2 | its other pictures show: x") == \
        "football#2"
    assert th.scene_row("sports > football #2") == "football#2"
    assert th.writer_line(tree, th.store(), "football") == "sports > football"


def test_a_sweep_paints_an_extra_as_its_own_row(tree, monkeypatch):
    _set(monkeypatch, thumbnails_review="none")
    _put("football", scene="a floodlit pitch at night")
    asked = []

    async def writer(lines):
        asked.extend(lines)
        return [th.Scene(line.split(" | ")[0].split(" > ")[-1],
                         "a muddy boot on a training ground bench", False)
                for line in lines]

    async def painter(prompt):
        return th.Painting(_png((20, 90, 160)), "image/png")

    async def checker(image, mime, subject):
        assert subject == "sports > football"
        return ({"logo": False, "text": False, "person": False,
                 "identifiable_product": False, "matches_subject": True,
                 "notes": ""}, 0.0)

    result = asyncio.run(th.backfill(5, only=["football#2"], writer=writer,
                                     painter=painter, checker=checker,
                                     ignore_daily_cap=True))
    assert result["approved"] == 1, result
    assert "a floodlit pitch at night" in asked[0]
    assert [p[0] for p in th.store().pictures("football")] == \
        ["football", "football#2"]


# --- what a picture falls back to -------------------------------------------


def test_pga_tour_is_golf_not_culture(tree):
    assert "golf" in tree.match("PGA Tour today")
    tags = T.tags_for_text("PGA Tour today")
    assert "culture" not in tags and "music" not in tags
    assert T.picture_facets("PGA Tour today")[0] == "sports"
    _put("culture", facet="culture")
    _put("sports")
    got = th.pick_for_player("PGA Tour today")
    assert got["node"] == "sports"


def test_a_concert_tour_is_still_music():
    assert "music" in T.tags_for_text("Taylor Swift tour dates")
    assert T.picture_facets("Taylor Swift tour dates") == ("culture",)


def test_facets_are_strongest_first_never_alphabetical(tree):
    # Culture by a keyword, sports by a named league: sports leads.
    assert T.picture_facets("the nba all star album")[0] == "sports"


def test_the_category_facet_leads_the_fallback(tree):
    _put("business", facet="business")
    _put("world", facet="world")
    got = th.pick_for_player("a company in a war zone",
                             category="elections and politics")
    assert got["node"] == "world"


def test_nothing_placed_wears_nothing(tree):
    # "hobbies" became a node on 11.12; "pastimes" is still nothing.
    for facet in T.FACETS:
        _put(facet, facet=facet)
    assert th.pick_for_player("why gen z took up grandma pastimes") is None


# --- short names reach the tree ---------------------------------------------


@pytest.mark.parametrize("headline,node", [
    ("Lakers beat the Knicks in overtime", "los angeles lakers"),
    ("Yankees hire a new manager", "new york yankees"),
    ("Man City sack their manager", "premier league"),
    ("Senate passes the spending bill", "congress"),
    ("The governor's race in Virginia", "state politics"),
    ("Sabalenka wins the WTA Finals", "tennis"),
])
def test_short_names_reach_their_node(tree, headline, node):
    assert node in tree.match(headline)


def test_an_alias_is_never_a_node_itself(tree):
    found = tree.match("Lakers beat the Knicks")
    assert "lakers" not in found
    assert "nba" in found and "sports" in found


def test_every_alias_can_match_and_names_a_seed_node():
    seeded = {phrase for phrase, _ in S.rows()} | set(T.FACETS)
    for alias, target in S.ALIASES.items():
        assert C.normalise(alias) == alias, alias
        assert C.words_of(alias) == frozenset(alias.split()), \
            f"{alias!r} has a word too short to match"
        assert target in seeded, f"{alias!r} names {target!r}, not a node"
        assert alias not in seeded, f"{alias!r} is a node already"


@pytest.mark.parametrize("question", [
    "the Bank of England governor on rates",
    "meat packers and beef prices",
    "a plant shutdown in Ohio",
    "spurs on a cowboy's boots",
    "how ATP powers your cells",
    "a UCL study on sleep",
    "MLS listings in Austin are up",
    "studying for midterms",
    "a smart house speaker review",
    "the Wimbledon area housing market",
    "Grand Prix figure skating",
])
def test_everyday_words_are_not_aliases(tree, question):
    caught = set(tree.match(question)) & {
        "congress", "state politics", "government shutdown",
        "green bay packers", "premier league", "tennis", "champions league",
        "major league soccer", "elections and politics", "motorsport"}
    assert not caught, f"{question!r} was filed under {caught}"
