"""The owner's 11.11 note: a Play all on each myFAM rail, and the faces of
the friends who listened on the player as well as on the DailyFAM card."""
from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import topics as T  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as fh:
    INDEX = fh.read()


def _fn(name):
    return INDEX.split("function " + name + "(", 1)[1].split("\n  }\n", 1)[0]


class _Req:
    def __init__(self):
        self.state = type("S", (), {})()
        self.headers = {}
        self.client = type("C", (), {"host": "127.0.0.1"})()


def test_listened_names_friends_by_the_question(monkeypatch, tmp_path):
    store = T.EventStore(str(tmp_path / "e.db"))
    monkeypatch.setattr(appmod, "EVENTS", store)
    people = [{"user_id": "u1", "name": "Ana", "handle": "ana", "avatar": ""},
              {"user_id": "u2", "name": "Ben", "handle": "ben", "avatar": ""}]
    monkeypatch.setattr(appmod.SOCIAL, "friends", lambda user, limit=500: people)
    monkeypatch.setattr(appmod, "_listener", lambda r: "me")
    monkeypatch.setattr(appmod, "_has_account", lambda r: True)
    monkeypatch.setattr(appmod, "_read_limit", lambda r: None)
    now = time.time()
    for at, who in ((now - 200, "u1"), (now - 100, "u2")):
        store.record(T.Event(who, "play", "", "Why is gold rising", at=at))
        store.mark_heard(who, "Why is gold rising", at + 60)
    got = asyncio.run(appmod.listened(_Req(), q="why is gold rising"))
    assert got["count"] == 2
    assert [f["handle"] for f in got["faces"]] == ["ben", "ana"]
    for face in got["faces"]:
        assert "user_id" not in face   # never an id


def test_listened_is_empty_for_a_guest(monkeypatch):
    monkeypatch.setattr(appmod, "_listener", lambda r: "guest")
    monkeypatch.setattr(appmod, "_has_account", lambda r: False)
    monkeypatch.setattr(appmod, "_read_limit", lambda r: None)
    assert asyncio.run(appmod.listened(_Req(), q="anything")) == {"count": 0, "faces": []}


def test_player_draws_faces_and_never_for_a_private_search():
    assert 'id="playerFriends"' in INDEX
    load = _fn("loadPlayerFriends")
    assert "t.private" in load and "AUTH.authenticated" in load
    assert "/api/listened?q=" in load
    # A tile's own faces first, no request.
    assert "myFamTopics[t.bankTopicId]" in load
    assert "loadPlayerFriends(query, t, myToken);" in _fn("populatePlayer")
    assert 'topicId === "_player" ? playerFriends' in _fn("openFriendsListened")


def test_every_rail_with_episodes_has_play_all():
    feed = _fn("renderMyFamFeed")
    assert "feed-playall" in feed and "playRailAll(" in feed
    # The empty rail returns before the button is drawn.
    assert feed.index("if(!sec.topics.length)") < feed.index("feed-playall")


def test_play_all_queues_the_rest_behind_the_first():
    fn = _fn("playRailAll")
    assert "startBankTopic(tiles[0].id)" in fn
    assert "tiles.slice(1)" in fn and "fromRail: true" in fn
    # A second Play all replaces the first's; the listener's own queue stays.
    assert "QUEUE[i].fromRail" in fn
    # Anything else starting ends it.
    assert "q.fromRail" in _fn("generate")
    # A guest plays only what is made.
    assert "t.playable === false && !AUTH.authenticated" in fn


def test_play_all_never_squeezes_the_heading():
    css = INDEX.split(".feed-head{", 1)[1].split("}", 1)[0]
    assert "flex-wrap:wrap" in css
    assert ".feed-head .feed-title{ flex:1 1 12em;" in INDEX
