"""Today's order of a mix: best stories first, a new order every day (§616).

The owner's 11.14 notes: a mix "previews a headline, and automatically
reorders the episodes (and displayed tiles on cover) to be in order of most
trending/relevant stories... so you don't hear the same order every day and
it prioritizes best stories first". `mix_today` decides the order from
measurements; `/api/mixes` serves it as new fields beside the owner's order.
"""
from __future__ import annotations

import os
import pathlib
import sys
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import mix_today as T  # noqa: E402
import mixes as M  # noqa: E402
import saved as saved_mod  # noqa: E402
import signals as S  # noqa: E402

PASSWORD = "a-long-enough-password"
NOW = 1_800_000_000.0
ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text()


def entry(item_id, position, **kw):
    return T.Entry(item_id=item_id, position=position, **kw)


def ids(entries):
    return [e.item_id for e in entries]


# --- the order ----------------------------------------------------------------


def test_a_result_in_beats_a_trend_beats_coverage_beats_ready():
    entries = [entry("ready", 0, written=True),
               entry("covered", 1, coverage=40),
               entry("trend", 2, signal="trending"),
               entry("final", 3, signal="update")]
    assert ids(T.order(entries, day=0)) == ["final", "trend", "covered", "ready"]


def test_heard_today_goes_last_whatever_it_scores():
    entries = [entry("heard", 0, heard_today=True, signal="update", coverage=99),
               entry("quiet", 1)]
    assert ids(T.order(entries, day=0)) == ["quiet", "heard"]


def test_taste_counts_within_the_mix():
    entries = [entry("rarely", 0, affinity=0.1), entry("often", 1, affinity=2.0)]
    assert ids(T.order(entries, day=0)) == ["often", "rarely"]


def test_ties_turn_by_one_place_a_day_and_hold_within_a_day():
    """Nothing to tell them apart: the owner's order, turned a place a day -
    a different first topic each morning, the same order on every reload."""
    entries = [entry(x, n) for n, x in enumerate("abcd")]
    assert ids(T.order(entries, day=0)) == list("abcd")
    assert ids(T.order(entries, day=1)) == list("bcda")
    assert ids(T.order(entries, day=2)) == list("cdab")
    assert ids(T.order(entries, day=1)) == ids(T.order(entries, day=1))
    firsts = {T.order(entries, day=d)[0].item_id for d in range(4)}
    assert firsts == set("abcd")


def test_coverage_is_a_log_scale_capped_at_full():
    assert T.coverage_score(0) == 0
    assert 0 < T.coverage_score(3) < T.coverage_score(12) < T.coverage_score(T.COVERAGE_FULL) == 1
    assert T.coverage_score(500) == 1


def test_why_is_always_a_measurement():
    assert T.why(entry("a", 0, signal="update")) == "update"
    assert T.why(entry("a", 0, signal="trending")) == "trending"
    assert T.why(entry("a", 0, in_edition=True)) == "covered"
    assert T.why(entry("a", 0, coverage=12)) == "covered"
    assert T.why(entry("a", 0, written=True, affinity=3)) == ""


def test_the_headline_is_the_first_unheard_thing_with_words():
    ordered = [entry("a", 0, heard_today=True, headline="Heard it"),
               entry("b", 1), entry("c", 2, headline="Eagles name a starter")]
    assert T.headline(ordered).item_id == "c"
    assert T.headline([entry("a", 0, heard_today=True, headline="Only")]).item_id == "a"
    assert T.headline([entry("a", 0)]) is None


def test_thumbs_follow_the_order_and_never_repeat():
    ordered = [entry("a", 0, picture="/p/1"), entry("b", 1, picture="/p/1"),
               entry("c", 2), entry("d", 3, picture="/p/2"), entry("e", 4, picture="/p/3"),
               entry("f", 5, picture="/p/4"), entry("g", 6, picture="/p/5")]
    assert T.thumbs(ordered) == ["/p/1", "/p/2", "/p/3", "/p/4"]


# --- coverage of a followed topic ---------------------------------------------


def story(title, *, first_seen=NOW - 600, coverage=0):
    return SimpleNamespace(id="s-" + title[:8], title=title, subject=title, query=title,
                           anchors=(), first_seen=first_seen, coverage=coverage,
                           live_status="", status_at=0.0)


EAGLES = M.followed_item("f:nfl~Eagles")


def test_coverage_prefers_the_edition_and_counts_the_widest():
    pool = [story("Eagles lose their left tackle", coverage=30)]
    edition = [story("Eagles name a new starter", coverage=8)]
    found = S.coverage(EAGLES, edition, pool, NOW)
    assert found.in_edition and found.headline == "Eagles name a new starter"
    assert found.outlets == 30


def test_coverage_is_today_only_and_never_for_a_typed_topic():
    old = story("Eagles lose their left tackle", first_seen=NOW - 3 * 86400, coverage=50)
    assert S.coverage(EAGLES, [], [old], NOW).outlets == 0
    typed = M.custom_item("what my council is doing about the high street")
    assert S.coverage(typed, [story("Eagles win")], [], NOW).outlets == 0


# --- through /api/mixes -------------------------------------------------------


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "MIXES", M.MixStore(str(tmp_path / "api.db")))
    monkeypatch.setattr(appmod, "SAVED", saved_mod.SavedStore(str(tmp_path / "saved.db")))
    monkeypatch.setattr(appmod, "_picture_url",
                        lambda q, t="", m=0: "/api/thumb/" + (q or t).split()[0].lower())
    monkeypatch.setattr(appmod.trending_bank, "stories_in_memory", lambda n=None: [])
    c = TestClient(appmod.app)
    assert c.post("/api/auth/signup", json={"email": "today@fam.test",
                                            "password": PASSWORD}).status_code == 200
    c.post("/api/mixes", json={"name": "Game day",
                               "topic_ids": ["f:formula1", "f:nfl~Eagles"]})
    return c


def test_the_list_serves_todays_order_beside_the_owners(client, monkeypatch):
    now = time.time()
    monkeypatch.setattr(appmod.trending_bank, "stories_in_memory",
                        lambda n=None: [story("Eagles lose their left tackle",
                                              first_seen=now - 60, coverage=20)])
    mix = client.get("/api/mixes").json()["mixes"][0]
    # The contents keep the owner's order; today's order leads with the story.
    assert [i["id"] for i in mix["items"]] == ["f:formula1", "f:nfl~Eagles"]
    assert mix["today_order"] == ["f:nfl~Eagles", "f:formula1"]
    assert mix["items"][1]["today_why"] == "trending"
    assert "today_why" not in mix["items"][0]
    assert mix["headline"]["item_id"] == "f:nfl~Eagles"
    assert mix["headline"]["topic"] == "Eagles"
    assert mix["headline"]["title"] == "Eagles lose their left tackle"
    assert mix["headline"]["why"] == "trending"
    assert mix["thumbs"][0] == mix["items"][1]["picture"]


def test_every_route_answering_with_a_mix_carries_it(client):
    mix = client.get("/api/mixes").json()["mixes"][0]
    body = client.patch(f"/api/mixes/{mix['id']}", json={"name": "Renamed"}).json()
    assert sorted(body["today_order"]) == sorted(i["id"] for i in body["items"])
    assert "thumbs" in body


def test_a_broken_order_never_takes_the_list_down(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no")
    monkeypatch.setattr(T, "order", boom)
    res = client.get("/api/mixes")
    assert res.status_code == 200
    mix = res.json()["mixes"][0]
    assert "today_order" not in mix and "thumbs" not in mix and len(mix["items"]) == 2


def test_the_list_reads_the_taste_once(client, monkeypatch):
    client.post("/api/mixes", json={"name": "Second", "topic_ids": ["f:nfl"]})
    reads = []
    real = appmod.EVENTS.for_user
    monkeypatch.setattr(appmod.EVENTS, "for_user", lambda *a, **k: reads.append(1) or real(*a, **k))
    assert len(client.get("/api/mixes").json()["mixes"]) == 2
    assert len(reads) == 1


# --- the clients play it ------------------------------------------------------


def test_the_web_plays_todays_order():
    assert "function todayOrder(m)" in INDEX
    start = INDEX.split("function startMixQueue(m, start, listed){", 1)[1].split("\n  }\n", 1)[0]
    assert "todayOrder(m)" in start


def test_the_iphone_plays_todays_order():
    api = (ROOT / "ios" / "FAM" / "FAM" / "API.swift").read_text()
    model = (ROOT / "ios" / "FAM" / "FAM" / "AppModel.swift").read_text()
    assert "let todayOrder: [String]" in api and "signal, todayOrder, thumbs, headline" in api
    assert "mix.playOrder" in model
    # A mix with no photo of its own draws today's lead picture.
    assert "var shownCover: String" in api
    myfam = (ROOT / "ios" / "FAM" / "FAM" / "Views" / "MyFAMView.swift").read_text()
    assert "MixArt(cover: mix.shownCover)" in myfam


def test_the_web_draws_todays_picture_when_there_is_no_cover():
    assert "function coverOf(m)" in INDEX
    style = INDEX.split("function coverStyle(m){", 1)[1].split("\n  }\n", 1)[0]
    assert "coverOf(m)" in style
