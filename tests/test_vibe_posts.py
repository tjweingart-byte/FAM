"""Vibes as Posts (PROBLEMS.md §544, the owner's option C, "Compact Trio").

A vibe tapped on your own YourFAM grid or a friend's opens as a post: the
day it was vibed, the line the poster picked over the picture, a player
under it with that line marked, likes, the friends who heard it, and
replies under it. These pin the server half - the line on the vibe row,
the like, the replies filed under one vibe and never in the episode's own
thread, who may see a post - and the interface's promises."""

from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
import social as social_mod  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "static" / "index.html").read_text()


def _fn(name):
    return INDEX.split("function " + name + "(", 1)[1].split("\n  }\n", 1)[0]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    with TestClient(appmod.app) as c:
        yield c


def signed_in(client, email, name, handle):
    client.post("/api/auth/signup", json={"email": email, "password": "password12"})
    client.post("/api/me", json={"name": name, "handle": handle})
    return client.get("/api/auth/me").json()["user_id"]


@pytest.fixture
def store(tmp_path):
    s = social_mod.SocialStore(str(tmp_path / "social.db"))
    s.set_person("a", "Ann", "ann")
    s.set_person("b", "Bob", "bob")
    return s


# --- the store ---------------------------------------------------------------

def test_a_vibe_keeps_its_line_and_a_revibe_without_one_keeps_it(store):
    store.echo("a", "how crew 12 comes home", "Crew-12", 2,
               line="  The capsule fires four deorbit burns.  ", line_at=48.3)
    post = store.vibe_posts("a", viewer="a")[0]
    assert post["line"] == {"text": "The capsule fires four deorbit burns.", "at": 48.3}
    # An older client, or a re-vibe whose editor picked nothing: kept.
    store.echo("a", "how crew 12 comes home", "Crew-12", 2, caption="again")
    assert store.vibe_posts("a", viewer="a")[0]["line"]["at"] == 48.3
    # "" takes it off.
    store.echo("a", "how crew 12 comes home", "Crew-12", 2, line="")
    assert store.vibe_posts("a", viewer="a")[0]["line"] is None


def test_a_line_is_cut_and_its_time_clamped(store):
    store.echo("a", "q", "T", 2, line="word " * 200, line_at=99999)
    post = store.vibe_posts("a", viewer="a")[0]
    assert len(post["line"]["text"]) <= social_mod.MAX_LINE
    assert post["line"]["at"] == social_mod.MAX_LINE_AT
    store.echo("a", "q", "T", 2, line="A line", line_at=-4)
    assert store.vibe_posts("a", viewer="a")[0]["line"]["at"] is None


def test_likes_count_per_post_not_per_episode(store):
    a = store.echo("a", "same episode", "T", 2)
    b = store.echo("b", "same episode", "T", 2)
    assert store.like_vibe("b", a.id)["likes"] == 1
    assert store.like_vibe("b", a.id)["likes"] == 1      # once per person
    post_a = store.vibe_posts("a", viewer="b")[0]
    assert post_a["likes"] == 1 and post_a["liked"] is True
    assert store.vibe_posts("b", viewer="a")[0]["likes"] == 0
    assert store.like_vibe("b", a.id, on=False)["likes"] == 0
    with pytest.raises(social_mod.SocialError):
        store.like_vibe("a", b.id + 999)


def test_replies_live_under_the_vibe_never_in_the_episodes_thread(store):
    e = store.echo("a", "why tides turn", "Tides", 3)
    store.add_comment("b", "why tides turn", 3, "On the episode")
    reply = store.add_comment("b", "why tides turn", 3, "On Ann's post", echo_id=e.id)
    episode = store.comments("why tides turn", 3)
    assert [c["text"] for c in episode] == ["On the episode"]
    assert store.comment_counts_many([("why tides turn", 3)]) == {("why tides turn", 3): 1}
    thread = store.comments("why tides turn", 3, echo_id=e.id)
    assert [c["text"] for c in thread] == ["On Ann's post"]
    post = store.vibe_posts("a", viewer="a")[0]
    assert post["replies"] == 1 and post["top_reply"]["text"] == "On Ann's post"
    assert "user_id" not in post["top_reply"]
    # A reply in the post's thread cannot answer the episode's thread.
    with pytest.raises(social_mod.SocialError):
        store.add_comment("a", "why tides turn", 3, "x", parent_id=episode[0]["id"],
                          echo_id=e.id)
    assert store.comment(reply["id"])["echo_id"] == e.id


def test_the_poster_may_take_down_a_reply_on_their_own_post(store):
    e = store.echo("a", "q", "T", 2)
    on_post = store.add_comment("b", "q", 2, "rude", echo_id=e.id)
    on_episode = store.add_comment("b", "q", 2, "rude")
    assert store.delete_comment("a", on_episode["id"]) is False
    assert store.delete_comment("a", on_post["id"]) is True


def test_deleting_a_vibe_takes_its_likes_and_replies(store):
    e = store.echo("a", "q", "T", 2)
    store.like_vibe("b", e.id)
    store.add_comment("b", "q", 2, "nice", echo_id=e.id)
    assert store.unecho("a", "q", 2)
    conn = store._conn()
    assert conn.execute("SELECT COUNT(*) FROM vibe_likes").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0] == 0


def test_a_deleted_account_leaves_no_post_behind(store):
    e = store.echo("a", "q", "T", 2)
    store.like_vibe("b", e.id)
    store.add_comment("b", "q", 2, "nice", echo_id=e.id)
    theirs = store.echo("b", "r", "T", 2)
    store.like_vibe("a", theirs.id)
    store.forget("a")
    conn = store._conn()
    assert conn.execute("SELECT COUNT(*) FROM vibe_likes").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0] == 0


def test_posts_page_by_time(store):
    import time
    for i in range(5):
        store.echo("a", f"q{i}", "T", 2)
        time.sleep(0.01)
    first = store.vibe_posts("a", viewer="a", limit=3)
    rest = store.vibe_posts("a", viewer="a", limit=3, before=first[-1]["at"])
    assert [p["query"] for p in first + rest] == ["q4", "q3", "q2", "q1", "q0"]


# --- the API -------------------------------------------------------------------

def test_your_own_posts_and_a_friends(client):
    me = signed_in(client, "vp1@b.com", "Ian", "ianvp")
    client.post("/api/vibe", json={"query": "how crew 12 comes home", "minutes": 2,
                                   "title": "Crew-12", "caption": "Stayed up for it",
                                   "line": "The capsule fires four deorbit burns.",
                                   "line_at": 48})
    own = client.get("/api/vibe/posts").json()
    assert own["mine"] is True and own["person"]["handle"] == "ianvp"
    post = own["posts"][0]
    assert post["caption"] == "Stayed up for it"
    assert post["line"] == {"text": "The capsule fires four deorbit burns.", "at": 48.0}
    assert "thumb" in post and post["heard"] == {"count": 0, "faces": []}
    assert "user_id" not in own["person"]
    friend = TestClient(appmod.app)
    with friend:
        them = signed_in(friend, "vp2@b.com", "Maya", "mayavp")
        friend.post("/api/friends/follow", json={"user_id": me})
        client.post("/api/friends/follow", json={"user_id": them})
        page = friend.get("/api/vibe/posts", params={"handle": "ianvp"}).json()
        assert page["mine"] is False
        eid = page["posts"][0]["id"]
        assert friend.post(f"/api/vibe/{eid}/like", json={"on": True}).json()["likes"] == 1
        reply = friend.post(f"/api/vibe/{eid}/replies", json={"text": "So good"})
        assert reply.status_code == 200 and reply.json()["mine"] is True
        thread = friend.get(f"/api/vibe/{eid}/replies").json()
        assert thread["count"] == 1 and thread["mine"] is False
    again = client.get("/api/vibe/posts").json()["posts"][0]
    assert again["likes"] == 1 and again["replies"] == 1
    assert again["top_reply"]["handle"] == "mayavp"
    # The episode's own thread (Explore's) does not have it.
    assert client.get("/api/comments", params={"q": "how crew 12 comes home",
                                               "minutes": 2}).json()["count"] == 0


def test_a_guest_can_neither_like_nor_reply(client):
    signed_in(client, "vp3@b.com", "Ian", "ianvp3")
    eid = client.post("/api/vibe", json={"query": "q", "minutes": 2}).json()["id"]
    guest = TestClient(appmod.app)
    with guest:
        assert guest.post(f"/api/vibe/{eid}/like", json={"on": True}).status_code == 401
        assert guest.post(f"/api/vibe/{eid}/replies",
                          json={"text": "hi"}).status_code == 401


def test_no_post_for_a_handle_nobody_has_or_a_vibe_that_is_gone(client):
    signed_in(client, "vp4@b.com", "Ian", "ianvp4")
    assert client.get("/api/vibe/posts", params={"handle": "nobody_here"}).status_code == 404
    assert client.get("/api/vibe/987654/replies").status_code == 404
    assert client.post("/api/vibe/987654/like", json={"on": True}).status_code == 404


def test_a_close_friends_vibe_stays_out_of_a_strangers_reach(client):
    me = signed_in(client, "vp5@b.com", "Ian", "ianvp5")
    eid = client.post("/api/vibe", json={"query": "secret", "minutes": 2,
                                         "audience": "close"}).json()["id"]
    other = TestClient(appmod.app)
    with other:
        signed_in(other, "vp6@b.com", "Zed", "zedvp6")
        page = other.get("/api/vibe/posts", params={"handle": "ianvp5"}).json()
        assert all(p["id"] != eid for p in page["posts"])
        assert other.post(f"/api/vibe/{eid}/like", json={"on": True}).status_code == 404
        assert other.get(f"/api/vibe/{eid}/replies").status_code == 404
    assert me


# --- the interface -----------------------------------------------------------

def test_a_tap_on_either_grid_opens_the_posts_not_the_player():
    assert "openVibePosts(" in _fn("playProfileVibe")
    assert "openVibePosts(" in _fn("playPersonVibe")
    assert 'id="screen-vibeposts"' in INDEX


def test_a_post_plays_where_it_sits():
    # The episode starts without leaving the posts, and the line jumps.
    assert "inPlace: true" in _fn("playVibePost")
    assert "inPlace" in _fn("onGenerationStarted")
    assert "pendingResumeSeek" in _fn("playVibePostLine")
    assert "paintVibePostTransport()" in _fn("refreshProgressNow")


def test_the_composer_offers_a_line_and_sends_it():
    assert 'data-tool="line"' in INDEX
    body = _fn("postVibeStory")
    assert "line_at" in body and "line:" in body


def test_a_post_says_the_day_it_was_vibed():
    assert "Vibed " in _fn("vibePostHTML")


def test_both_previews_answer_the_posts():
    for builder in ("build_preview.py", "build_live_preview.py"):
        src = (ROOT / "preview" / builder).read_text()
        assert '"/api/vibe/posts"' in src, builder
