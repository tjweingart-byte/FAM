"""The 10.6 implementations packet (PROBLEMS.md §213): your own story and its
menu, VIBE! as a story editor with close friends, holding a story to pause it,
and group chats."""

from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
import messages as messages_mod  # noqa: E402
import social as social_mod  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text()


def _fn(name):
    return INDEX.split("function " + name + "(", 1)[1].split("\n  }\n", 1)[0]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    with TestClient(appmod.app) as c:
        yield c


def signed_in(client, email, name, handle):
    client.post("/api/auth/signup", json={"email": email, "password": "password12"})
    client.post("/api/me", json={"name": name, "handle": handle})
    return client.get("/api/auth/me").json()["user_id"]


@pytest.fixture
def store(tmp_path):
    s = social_mod.SocialStore(str(tmp_path / "social.db"))
    for uid, name in (("a", "Ann"), ("b", "Bob"), ("c", "Cy")):
        s.set_person(uid, name, name.lower())
    return s


@pytest.fixture
def mail(tmp_path):
    return messages_mod.MessageStore(str(tmp_path / "messages.db"))


# --- 1. your own story, and its menu ------------------------------------------

def test_your_own_vibes_come_back_as_your_story(client):
    signed_in(client, "own1@b.com", "Ona", "ona1")
    client.post("/api/vibe", json={"query": "why tides turn", "minutes": 3,
                                   "title": "Tides"})
    stories = client.get("/api/profile").json()["stories"]
    assert [s["query"] for s in stories] == ["why tides turn"]
    assert stories[0]["id"]


def test_removing_from_your_story_keeps_the_vibe(client):
    signed_in(client, "own2@b.com", "Ola", "ola2")
    client.post("/api/vibe", json={"query": "why tides turn", "minutes": 3})
    sid = client.get("/api/profile").json()["stories"][0]["id"]
    assert client.delete(f"/api/vibe/story/{sid}").json()["ok"] is True
    body = client.get("/api/profile").json()
    assert body["stories"] == []
    assert body["echo_count"] == 1, "taking it off the story took the vibe too"
    # Vibing it again puts it back up.
    client.post("/api/vibe", json={"query": "why tides turn", "minutes": 3})
    assert len(client.get("/api/profile").json()["stories"]) == 1


def test_only_the_poster_can_take_a_story_down(store):
    echo = store.echo("a", "why tides turn", "Tides", 3)
    assert store.unstory("b", echo.id) is False
    assert store.stories_among(["a"], 0, viewer="b")["a"]


def test_the_profile_picture_rings_and_plays_your_story():
    body = _fn("renderProfile")
    assert 'onclick="openMyStory()"' in body and "yf-photo-btn ring" in body
    assert "storyList = [myStoryPerson]" in _fn("openMyStory")


def test_the_story_menu_has_the_owners_five():
    body = _fn("openStoryMenu")
    for label in ("Save for later", "Share", "Add to Queue", "Add topic to playlist",
                  "Remove from your story"):
        assert label in body, label
    # Remove is only on your own.
    assert "if(person.mine && v.id)" in body
    assert 'class="story-more" onclick="openStoryMenu()"' in INDEX


# --- 2. VIBE! as a story editor -------------------------------------------------

def test_vibe_opens_the_editor_not_a_caption_box():
    assert "openVibeComposer(ep)" in _fn("toggleEcho")
    composer = INDEX.split('id="vibeComposer"', 1)[1].split('id="closeFriendsOverlay"', 1)[0]
    for tool in ("'text'", "'stickers'", "'mention'", "'picture'"):
        assert "vcsTool(" + tool + ")" in composer, tool
    for door in ("postVibeStory('')", "postVibeStory('close')", "closeVibeComposer()"):
        assert door in composer, door
    assert "Add a caption" in composer
    # Not music, not the colour circle, not the "more" arrow.
    for gone in ("Audio", "Music", "Background", "More"):
        assert "<span>" + gone + "</span>" not in composer, gone


def test_a_layout_is_clamped_before_it_is_kept(store):
    store.echo("a", "why tides turn", "Tides", 3, caption="look",
               style={"font": "comic-sans", "size": 900, "cx": -3, "zoom": 99,
                      "frame": "hexagon", "thumb": "https://tracker.example/p.png",
                      "stickers": [{"k": "\U0001F525", "x": 2, "s": 50},
                                   {"k": "<script>", "x": 0.5}] * 20})
    st = store.stories_among(["a"], 0)["a"][0]["style"]
    assert st["font"] == "classic" and st["size"] == 48 and st["cx"] == 0.05
    assert st["zoom"] == 2.5 and st["frame"] == "card"
    assert st["thumb"] == "", "a remote picture is a tracking pixel"
    assert len(st["stickers"]) == social_mod.MAX_STICKERS
    assert all(k["k"] == "\U0001F525" and k["x"] == 1 and k["s"] == 3 for k in st["stickers"])


def test_a_plain_vibe_has_no_layout(store):
    store.echo("a", "why tides turn", "Tides", 3)
    story = store.stories_among(["a"], 0)["a"][0]
    assert story["style"] == {} and story["tags"] == [] and story["close"] is False


def test_the_sticker_lists_agree():
    for k in social_mod.STORY_STICKERS:
        if k.isascii():
            assert f'"{k}"' in INDEX.split("var STORY_STICKERS", 1)[1].split("];", 1)[0], k


def test_close_friends_stories_reach_only_close_friends(store):
    store.echo("a", "why tides turn", "Tides", 3, audience="close")
    assert store.stories_among(["a"], 0, viewer="b") == {}
    store.set_close_friend("a", "b")
    assert store.stories_among(["a"], 0, viewer="b")["a"][0]["close"] is True
    assert store.stories_among(["a"], 0, viewer="c") == {}
    # Nor on their profile, nor as a label on somebody else's card.
    assert store.echoes_by("a", viewer="c") == []
    assert len(store.echoes_by("a", viewer="b")) == 1
    assert store.recent_echoes() == {}
    assert store.echoes_among(["a"], viewer="c") == {}


def test_close_friends_are_chosen_in_settings(client):
    me = signed_in(client, "cf1@b.com", "Cal", "cal1")
    other = TestClient(appmod.app)
    with other:
        them = signed_in(other, "cf2@b.com", "Dee", "dee1")
    client.post("/api/friends/follow", json={"user_id": them})
    people = client.get("/api/close-friends").json()["people"]
    assert [p["handle"] for p in people] == ["dee1"] and people[0]["close"] is False
    assert client.post("/api/close-friends",
                       json={"user_id": them, "on": True}).json()["count"] == 1
    # Somebody outside the graph is refused.
    assert client.post("/api/close-friends",
                       json={"user_id": "nobody", "on": True}).status_code == 404
    assert "openCloseFriends()" in _fn("renderSettings")
    assert me


def test_a_tag_reaches_the_person_tagged(client):
    me = signed_in(client, "tg1@b.com", "Tia", "tia1")
    other = TestClient(appmod.app)
    with other:
        them = signed_in(other, "tg2@b.com", "Uri", "uri1")
        other.post("/api/friends/follow", json={"user_id": me})
    client.post("/api/friends/follow", json={"user_id": them})
    client.post("/api/vibe", json={
        "query": "why tides turn", "minutes": 3, "title": "Tides",
        "tags": [{"handle": "@uri1", "x": 0.4, "y": 0.2},
                 {"handle": "stranger", "x": 0.5, "y": 0.5}]})
    story = client.get("/api/profile").json()["stories"][0]
    assert story["tags"] == [{"handle": "uri1", "name": "Uri", "x": 0.4, "y": 0.2}]
    assert "user_id" not in story["tags"][0]
    with other:
        inbox = other.get("/api/messages").json()["threads"]
    assert inbox and inbox[0]["last"]["query"] == "why tides turn"


def test_a_close_friends_story_tags_only_close_friends(client):
    me = signed_in(client, "tg3@b.com", "Vic", "vic3")
    other = TestClient(appmod.app)
    with other:
        them = signed_in(other, "tg4@b.com", "Wen", "wen4")
        other.post("/api/friends/follow", json={"user_id": me})
    client.post("/api/friends/follow", json={"user_id": them})
    client.post("/api/vibe", json={"query": "why tides turn", "minutes": 3,
                                   "audience": "close",
                                   "tags": [{"handle": "wen4", "x": 0.5, "y": 0.5}]})
    story = client.get("/api/profile").json()["stories"][0]
    assert story["close"] is True and story["tags"] == []


def test_the_circle_hides_a_close_friends_story_from_the_rest(client):
    me = signed_in(client, "cr1@b.com", "Xan", "xan1")
    other = TestClient(appmod.app)
    with other:
        them = signed_in(other, "cr2@b.com", "Yul", "yul1")
        other.post("/api/friends/follow", json={"user_id": me})
        other.post("/api/vibe", json={"query": "why bonds move", "minutes": 3,
                                      "audience": "close"})
    client.post("/api/friends/follow", json={"user_id": them})
    row = client.get("/api/circle").json()["circle"][0]
    assert row["stories"] == [] and row["vibed"] is False


# --- 3. holding a story pauses it -------------------------------------------------

def test_holding_the_screen_holds_the_story():
    viewer = INDEX.split("var STORY_HOLD_MS", 1)[1].split("function closeStory(", 1)[0]
    assert "setTimeout(function(){\n        pauseStory();" in viewer
    assert "resumeStory();" in viewer
    pause = _fn("pauseStory")
    assert "clearTimeout(storyTimer)" in pause and "storyLeft" in pause
    assert "storyRun()" in _fn("resumeStory")
    # The bar stops with it.
    assert ".story.paused .story-bar.on i{ animation-play-state:paused; }" in INDEX


# --- 4. group chats -----------------------------------------------------------------

def test_a_group_is_one_thread_for_everybody_in_it(mail):
    group = mail.create_group("a", ["b", "c"], "Crew")
    gid = group["id"]
    assert messages_mod.is_group(gid) and group["members"] == ["a", "b", "c"]
    mail.send("b", gid, text="morning")
    for who in ("a", "c"):
        assert [m.text for m in mail.thread(who, gid)] == ["started the group", "morning"]
        assert mail.inbox(who)[0]["with"] == gid and mail.inbox(who)[0]["group"] is True
    assert mail.unread_total("c") == 2 and mail.unread_total("b") == 1
    assert [m.text for m in mail.arrived_for("a")] == ["morning"]
    # Told they were added; never about their own message.
    assert [m.text for m in mail.arrived_for("b")] == ["started the group"]


def test_a_group_needs_two_others_and_keeps_strangers_out(mail):
    with pytest.raises(messages_mod.MessageError):
        mail.create_group("a", ["b"])
    gid = mail.create_group("a", ["b", "c"])["id"]
    with pytest.raises(messages_mod.MessageError):
        mail.thread("d", gid)
    with pytest.raises(messages_mod.MessageError):
        mail.send("d", gid, text="let me in")


def test_leaving_a_group_stops_its_messages(mail):
    gid = mail.create_group("a", ["b", "c"])["id"]
    mail.leave_group("c", gid)
    mail.send("a", gid, text="after")
    assert mail.inbox("c") == [] and mail.unread_total("c") == 0
    assert mail.arrived_for("c") == []
    assert [m.text for m in mail.thread("b", gid)][-2:] == ["left the group", "after"]


def test_groups_over_the_api(client):
    me = signed_in(client, "gc1@b.com", "Zed", "zed1")
    others, ids = [], []
    for n in (2, 3):
        c = TestClient(appmod.app)
        c.__enter__()
        ids.append(signed_in(c, f"gc{n}@b.com", f"P{n}", f"pp{n}"))
        others.append(c)
    for uid in ids:
        client.post("/api/friends/follow", json={"user_id": uid})
    # Only people in the graph.
    assert client.post("/api/messages/groups",
                       json={"user_ids": ids + ["stranger"]}).status_code == 400
    made = client.post("/api/messages/groups", json={"user_ids": ids, "name": "Pod"}).json()
    gid = made["group"]["user_id"]
    assert made["group"]["name"] == "Pod" and len(made["group"]["members"]) == 3
    assert all("user_id" not in m for m in made["group"]["members"])
    assert client.post("/api/messages", json={"to": gid, "text": "hi all"}).status_code == 200
    seen = others[0].get("/api/messages/thread", params={"with": gid}).json()
    assert seen["with"]["group"] is True
    assert seen["messages"][-1]["from"]["name"] == "Zed"
    note = others[1].get("/api/notifications", params={"since": 0}).json()["messages"]
    assert note[-1]["group"]["user_id"] == gid
    row = others[1].get("/api/messages").json()["threads"][0]
    assert row["group"] and row["name"] == "Pod" and row["last"]["from_name"] == "Zed"
    assert others[0].delete(f"/api/messages/groups/{gid}").json()["ok"] is True
    assert others[0].get("/api/messages/thread", params={"with": gid}).status_code == 400
    for c in others:
        c.__exit__(None, None, None)
    assert me


def test_the_new_chat_picker_picks_several():
    assert "newChatPicked.push(i)" in _fn("startChatWith")
    body = _fn("confirmNewChat")
    assert "newChatPicked.length >= 2" in body and "startGroupChat()" in body
    assert '"/api/messages/groups"' in _fn("startGroupChat")
    assert 'id="newChatGroupName"' in INDEX


def test_a_deleted_listener_leaves_their_groups(mail):
    gid = mail.create_group("a", ["b", "c"])["id"]
    mail.forget("c")
    assert mail.members(gid) == ["a", "b"]
