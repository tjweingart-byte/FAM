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
    composer = INDEX.split('id="vibeComposer"', 1)[1].split('id="guestGateOverlay"', 1)[0]
    for tool in ("'text'", "'stickers'", "'mention'", "'picture'"):
        assert "vcsTool(" + tool + ")" in composer, tool
    for door in ("postVibeStory('')", "closeVibeComposer()"):
        assert door in composer, door
    # No Close Friends audience (§250, the 10.10 packet): a story goes to
    # everybody who follows.
    assert "Close Friends</button>" not in composer and "postVibeStory('close')" not in INDEX
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


def test_close_friends_still_answer_an_older_client(client):
    """§250 took Close Friends out of the app; the endpoint stays for an
    installed client that still offers it (`old-clients`)."""
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
    assert "openCloseFriends" not in INDEX and "closeFriendsOverlay" not in INDEX
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
    assert "user_id" not in note[-1]["from"], "a group banner names a stranger's id"
    row = others[1].get("/api/messages").json()["threads"][0]
    assert row["group"] and row["name"] == "Pod" and row["last"]["from_name"] == "Zed"
    assert others[0].delete(f"/api/messages/groups/{gid}").json()["ok"] is True
    assert others[0].get("/api/messages/thread", params={"with": gid}).status_code == 400
    for c in others:
        c.__exit__(None, None, None)
    assert me


def test_the_share_sheet_can_share_an_episode_into_a_group(client):
    """The share sheet's Groups row (§236): it reads the group threads off
    the inbox and sends an episode to the group's id, and every member gets
    it as an episode share."""
    signed_in(client, "sg1@b.com", "Sal", "sal1")
    others, ids = [], []
    for n in (2, 3):
        c = TestClient(appmod.app)
        c.__enter__()
        ids.append(signed_in(c, f"sg{n}@b.com", f"Q{n}", f"qq{n}"))
        others.append(c)
    for uid in ids:
        client.post("/api/friends/follow", json={"user_id": uid})
    gid = client.post("/api/messages/groups",
                      json={"user_ids": ids, "name": "Pod"}).json()["group"]["user_id"]
    # What the sheet draws its Groups row from.
    groups = [t for t in client.get("/api/messages").json()["threads"] if t.get("group")]
    assert [(t["with"], t["name"]) for t in groups] == [(gid, "Pod")]
    sent = client.post("/api/messages", json={"to": gid, "query": "why tides turn",
                                              "minutes": 3, "title": "Tides"})
    assert sent.status_code == 200, sent.text
    for c in others:
        last = c.get("/api/messages/thread", params={"with": gid}).json()["messages"][-1]
        assert last["kind"] == "episode" and last["query"] == "why tides turn"
        c.__exit__(None, None, None)


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


def test_typing_into_a_group_needs_membership(client):
    signed_in(client, "ty1@b.com", "Ty", "ty1")
    assert client.post("/api/messages/typing",
                       json={"to": "g:0000000000000000"}).status_code == 404
# --- review fixes ---------------------------------------------------------------

@pytest.mark.parametrize("url", ["/api/audio?q=x&minutes=10", "/api/messages/thread?with=a",
                                 "/static/x.png", "//evil/p.png", "data:image/png;base64,AA"])
def test_a_story_picture_is_only_ever_a_picture_route(url):
    assert social_mod.clean_thumb(url) == ""


def test_a_real_picture_route_is_kept():
    assert social_mod.clean_thumb("/api/thumb/sports%3Anfl?v=17") == "/api/thumb/sports%3Anfl?v=17"


def test_an_old_clients_revibe_keeps_a_close_friends_story_closed(store):
    store.echo("a", "why tides turn", "Tides", 3, audience="close",
               style={"font": "strong"}, tags=[{"user_id": "b"}])
    store.echo("a", "why tides turn", "Tides", 3)   # no audience, style or tags
    story = store.stories_among(["a"], 0, viewer="a")["a"][0]
    assert story["close"] is True and story["style"]["font"] == "strong"
    assert store.tagged_in("a", "why tides turn", 3) == {"b"}
    # A new client saying "everybody" still can.
    store.echo("a", "why tides turn", "Tides", 3, audience="", style={}, tags=[])
    story = store.stories_among(["a"], 0, viewer="a")["a"][0]
    assert story["close"] is False and story["style"] == {}


def test_a_close_friends_vibe_is_not_counted_for_strangers(store):
    store.echo("a", "why tides turn", "Tides", 3, audience="close")
    assert store.episode_counts("why tides turn", 3, user_id="c")["vibes"] == 0
    assert store.episode_counts("why tides turn", 3, user_id="a")["vibes"] == 1
    many = store.episode_counts_many([("why tides turn", 3)], user_id="c")
    assert many[("why tides turn", 3)]["vibes"] == 0


def test_a_deleted_listener_is_untagged_everywhere(store):
    store.echo("a", "why tides turn", "Tides", 3, tags=[{"user_id": "b"}, {"user_id": "c"}])
    store.forget("b")
    assert store.tagged_in("a", "why tides turn", 3) == {"c"}


def test_a_deleted_listener_no_longer_owns_their_group(mail):
    gid = mail.create_group("a", ["b", "c"])["id"]
    mail.forget("a")
    assert mail.group(gid)["created_by"] == ""


def test_a_tag_is_sent_once_however_often_the_story_is_posted(client):
    me = signed_in(client, "tg5@b.com", "Ada", "ada5")
    other = TestClient(appmod.app)
    with other:
        them = signed_in(other, "tg6@b.com", "Bea", "bea6")
        other.post("/api/friends/follow", json={"user_id": me})
    client.post("/api/friends/follow", json={"user_id": them})
    for _ in range(2):
        client.post("/api/vibe", json={"query": "why tides turn", "minutes": 3,
                                       "tags": [{"handle": "bea6", "x": 0.5, "y": 0.5}]})
    with other:
        thread = other.get("/api/messages/thread", params={"with": me}).json()["messages"]
    assert len(thread) == 1


def test_a_stranger_cannot_learn_about_a_group_by_sending_to_it(client):
    signed_in(client, "gs1@b.com", "Cel", "cel1")
    assert client.post("/api/messages", json={"to": "g:0000000000000000",
                                              "text": "hi"}).status_code == 400


def test_coming_back_to_the_app_leaves_a_held_story_held():
    src = INDEX.split("var storyHeldByHide", 1)[1].split("function closeStory(", 1)[0]
    assert "if(!storyHeld){ pauseStory(); storyHeldByHide = true; }" in src
    assert "showStory()" not in src
