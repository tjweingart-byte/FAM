"""§367: a DailyFAM card shows the faces of the friends who listened, drawn
the way the player's sources are - three overlapped, then "+N" - in place of
"N friends listened", at the owner's direction."""
from __future__ import annotations

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


def _play(store, user, topic_id, at):
    store.record(T.Event(user, "play", topic_id, "", at=at))


def test_newest_listener_first_and_one_face_per_person(tmp_path):
    store = T.EventStore(str(tmp_path / "e.db"))
    now = time.time()
    _play(store, "ana", "t", now - 300)
    _play(store, "ben", "t", now - 200)
    _play(store, "ana", "t", now - 100)   # ana again, now the newest
    got = T.friends_who_listened(store, ["ana", "ben"], [{"id": "t", "query": "q"}])
    assert got == {"t": ["ana", "ben"]}
    assert T.friends_listened(store, ["ana", "ben"], [{"id": "t", "query": "q"}]) == {"t": 2}


def test_every_friend_listed_each_picture_once(monkeypatch, tmp_path):
    store = T.EventStore(str(tmp_path / "e.db"))
    monkeypatch.setattr(appmod, "EVENTS", store)
    people = [{"user_id": f"u{i}", "name": f"Name {i}", "handle": f"h{i}",
               "avatar": f"data:image/png;base64,{i}"} for i in range(5)]
    monkeypatch.setattr(appmod.SOCIAL, "friends", lambda user, limit=500: people)
    now = time.time()
    for i in range(5):
        _play(store, f"u{i}", "a", now - 100 + i)
    _play(store, "u4", "b", now - 50)
    tiles = [{"id": "a", "query": "x"}, {"id": "b", "query": "y"}]
    faces = appmod._mark_friends_listened("me", tiles)
    assert tiles[0]["friends_listened"] == 5
    # Every friend since §368 (tapping the faces lists them); the card
    # draws the first three.
    assert [faces[i]["handle"] for i in tiles[0]["friends_faces"]] == [
        "h4", "h3", "h2", "h1", "h0"]
    # u4 is on both cards and listed once.
    assert tiles[1]["friends_faces"] == [tiles[0]["friends_faces"][0]]
    assert len(faces) == 5
    for face in faces:
        assert set(face) == {"name", "handle", "avatar", "explicit"}  # never an id


def test_endpoints_send_the_face_list():
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert 'body["friend_faces"] = _mark_friends_listened(' in src
    assert 'feed["friend_faces"] = _mark_friends_listened(' in src


def test_card_draws_faces_like_the_sources_pill():
    chip = _fn("seedFriendsHtml")
    assert "src-marks" in chip and "src-count" in chip
    assert ".slice(0, 3)" in chip
    assert "src-count\">+' + (n - faces.length)" in chip
    face = _fn("friendFaceHtml")
    assert "src-mark" in face and "personHue(" in face and "deriveInitials(" in face
    # Every response is resolved before it draws: the feed, the remembered
    # feed and View more.
    assert INDEX.count("attachFriendFaces(") == 4
