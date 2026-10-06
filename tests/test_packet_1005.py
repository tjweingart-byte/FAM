"""The 10.5 implementations packet (PROBLEMS.md §203): the search bar's full
width, whole titles on DailyFAM tiles, reordering a mix, the chosen topics
stacked, the bookshelf, no Explore rail, the exploreFAM pill, Explore as a
reel with comments, and a caption on a vibe."""

from __future__ import annotations

import pathlib
import re
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


def _screen(name):
    return INDEX.split('id="screen-' + name + '"', 1)[1].split("</section>", 1)[0]


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
    s.set_person("a", "Ann", "ann")
    s.set_person("b", "Bob", "bob")
    return s


# --- 1. the search bar's whole width ----------------------------------------

def test_a_long_question_takes_the_whole_bar():
    body = _fn("paintSearchBar")
    assert 'classList.add("tall")' in body and 'classList.remove("tall")' in body
    assert ".fam-input-box.tall textarea{ flex:1 0 100%;" in INDEX


# --- 2. whole titles on DailyFAM's tiles -------------------------------------

def test_tile_titles_are_never_clamped():
    rule = re.search(r"\.seed-card-title\{([^}]*)\}", INDEX).group(1)
    assert "line-clamp" not in rule and "overflow:hidden" not in rule
    assert INDEX.count("seedTitleHTML(t.title)") == 2
    body = _fn("seedTitleHTML")
    assert "longer" in body and "long" in body


# --- 3. dragging a mix's topics into order -----------------------------------

def test_each_topic_in_a_mix_has_a_handle_on_its_left():
    body = _fn("renderMixBody")
    handle = body.index('class="mix-drag"')
    assert handle < body.index('class="mix-topic-body"'), "the handle is not on the left"
    assert 'id="mixOrder"' in body


def test_a_drop_saves_the_new_order():
    body = _fn("moveMixTopic")
    assert 'saveMix("PATCH"' in body and "topic_ids: payload" in body
    assert "items.splice(from, 1)" in body


def test_the_server_keeps_the_order_it_is_sent(client):
    signed_in(client, "order@b.com", "Ord", "ord1")
    made = client.post("/api/mixes", json={
        "name": "Morning", "topic_ids": ["f:nfl~Eagles", "f:nfl", {"query": "Stanford"}]}).json()
    eagles, nfl, typed = made["items"]
    # What `moveMixTopic` sends: ids, and a typed topic as its words.
    moved = client.patch("/api/mixes/" + made["id"], json={
        "topic_ids": [{"query": typed["query"], "title": typed["title"]},
                      eagles["id"], nfl["id"]]}).json()
    assert [i["id"] for i in moved["items"]] == [typed["id"], eagles["id"], nfl["id"]]


# --- 4. the chosen topics stacked together -----------------------------------

def test_edit_topics_stacks_what_is_chosen_and_lists_only_the_rest():
    body = _fn("renderMixPicker")
    assert "In this mix" in body and 'class="pk-chosen"' in body
    # The list under the stack holds only what is not chosen.
    assert 'return selectionIndex("f:" + c.id) === -1' in body


# --- 5. the bookshelf, and whole titles in the catalogue ---------------------

def test_dailyfams_library_button_is_a_bookshelf():
    btn = INDEX.split('id="myfamSearchBtn"', 1)[1].split("</button>", 1)[0]
    assert "<circle" not in btn, "still the magnifying glass"
    assert btn.count("<rect") == 2 and 'd="M2.5 21h19"' in btn


def test_the_catalogue_shows_whole_titles():
    assert re.search(r"#mfsBody \.sv-title\{ white-space:normal;", INDEX)


# --- 6 and 7. no Explore rail; the exploreFAM pill ---------------------------

def test_the_explore_rail_is_gone():
    assert "searchingRailHTML" not in INDEX and "searching-tile" not in INDEX


def test_search_and_dailyfam_carry_the_same_pill():
    btn = INDEX.split('id="homeExploreBtn"', 1)[1].split("</button>", 1)[0]
    assert "xfam-pill" in INDEX.split('id="homeExploreBtn"', 1)[0][-200:]
    assert "explore<span" in btn and 'class="wm-a"' in btn and "xfam-arrow" in btn
    pill = _fn("exploreFamPillHTML")
    assert 'getElementById("homeExploreBtn")' in pill and "openExplore(\\'myfam\\')" in pill
    feed = _fn("renderMyFamFeed")
    assert 'sec.key === "from_history"' in feed and "feed-head-row" in feed


# --- 8. Explore as a reel, with comments -------------------------------------

def test_the_reel_has_the_five_actions_down_the_right():
    explore = _screen("explore")
    rail = explore.split('class="reel-actions"', 1)[1].split('class="reel-progress"', 1)[0]
    order = [rail.index(m) for m in ('id="reelLike"', 'id="reelCommentBtn"',
                                     'id="reelEcho"', "openShareModal()", "saveForLater()")]
    assert order == sorted(order)
    assert ".reel-actions{ flex:none; display:flex; flex-direction:column;" in INDEX


def test_the_reel_shows_the_title_over_the_picture_with_the_players_menu():
    explore = _screen("explore")
    assert 'id="reelBg"' in explore and 'onclick="openReelMenu()"' in explore
    assert "/api/episode/card" in _fn("drawReelPicture")
    menu = _fn("openReelMenu")
    for action in ("openShareModal", "toggleEcho", "saveForLater", "toggleReelCC",
                   "openReelComments", "openQueue"):
        assert action in menu, action
    # No dislike anywhere (the owner, after the packet).
    assert "dislike" not in menu.lower()


def test_the_reel_keeps_both_transport_gestures():
    explore = _screen("explore")
    assert "skipAudio(-15)" in explore and "skipAudio(15)" in explore
    assert 'class="reel-progress"' in explore


def test_captions_slide_up_from_the_bottom_of_the_reel():
    explore = _screen("explore")
    assert 'id="reelCc"' in explore and 'onclick="toggleReelCC()"' in explore
    assert "/api/transcript" in _fn("fetchReelCaptions")


def test_comments_are_read_by_anyone_and_written_by_an_account(client):
    guest = client.get("/api/comments", params={"q": "why tides turn", "minutes": 3})
    assert guest.status_code == 200 and guest.json() == {"comments": [], "count": 0}
    refused = client.post("/api/comments", json={
        "query": "why tides turn", "minutes": 3, "text": "hello"})
    assert refused.status_code == 401
    signed_in(client, "cm@b.com", "Cam", "cam1")
    made = client.post("/api/comments", json={
        "query": "why tides turn", "minutes": 3, "text": "So good"}).json()
    assert made["handle"] == "cam1" and made["mine"] is True
    assert "user_id" not in made
    reply = client.post("/api/comments", json={
        "query": "why tides turn", "minutes": 3, "text": "agreed",
        "parent_id": made["id"]}).json()
    liked = client.post(f"/api/comments/{made['id']}/like", json={"on": True}).json()
    assert liked == {"id": made["id"], "likes": 1, "liked": True}
    page = client.get("/api/comments", params={"q": "why tides turn", "minutes": 3}).json()
    assert page["count"] == 2
    top = page["comments"][0]
    assert top["likes"] == 1 and top["liked"] is True
    assert [r["id"] for r in top["replies"]] == [reply["id"]]
    assert client.delete(f"/api/comments/{made['id']}").json() == {"ok": True}
    assert client.get("/api/comments", params={"q": "why tides turn",
                                               "minutes": 3}).json()["count"] == 0


def test_a_reply_to_a_reply_hangs_from_the_top_comment(store):
    top = store.add_comment("a", "why tides turn", 3, "first")
    reply = store.add_comment("b", "why tides turn", 3, "second", parent_id=top["id"])
    deeper = store.add_comment("a", "why tides turn", 3, "third", parent_id=reply["id"])
    assert deeper["parent_id"] == top["id"]
    rows = store.comments("why tides turn", 3)
    assert len(rows) == 1 and [r["text"] for r in rows[0]["replies"]] == ["second", "third"]


def test_a_reply_to_another_episodes_comment_is_refused(store):
    top = store.add_comment("a", "why tides turn", 3, "first")
    with pytest.raises(social_mod.SocialError):
        store.add_comment("b", "why bonds move", 3, "lost", parent_id=top["id"])


def test_comments_lose_slurs_and_keep_swearing(store):
    import content_filter
    slur = sorted(content_filter.SLURS)[0]
    made = store.add_comment("a", "why tides turn", 3, f"damn that {slur} line")
    assert slur not in made["text"].lower() and "damn" in made["text"]


def test_only_the_author_deletes_and_a_deleted_listener_takes_their_comments(store):
    top = store.add_comment("a", "why tides turn", 3, "first")
    store.add_comment("b", "why tides turn", 3, "reply", parent_id=top["id"])
    store.like_comment("b", top["id"])
    assert store.delete_comment("b", top["id"]) is False
    store.forget("a")
    assert store.comments("why tides turn", 3) == []
    assert store.comment_counts_many([("why tides turn", 3)]) == {("why tides turn", 3): 0}


def test_explore_cards_carry_their_comment_count():
    src = (ROOT / "app.py").read_text()
    assert '"comments": comment_counts[(entry["query"], entry["minutes"])]' in src
    assert 'put("reelCommentN", ep.comments)' in _fn("drawReelStats")


def test_the_comments_sheet_has_the_pictures_parts():
    explore = _screen("explore")
    sheet = explore.split('id="reelComments"', 1)[1]
    assert 'id="rcEmoji"' in sheet and 'id="rcInput"' in sheet and 'id="rcList"' in sheet
    item = _fn("rcItemHTML")
    for part in ("rc-av", "rc-who", "rc-text", "Reply", "rc-like"):
        assert part in item, part
    assert "more replies" in _fn("drawReelComments")


# --- 9. a caption on a vibe --------------------------------------------------

def test_a_vibe_carries_its_caption_into_the_story(client):
    me = signed_in(client, "vc1@b.com", "Vee", "vee1")
    other = TestClient(appmod.app)
    with other:
        them = signed_in(other, "vc2@b.com", "Wes", "wes1")
        other.post("/api/friends/follow", json={"user_id": me})
        made = other.post("/api/vibe", json={
            "query": "why bonds move", "minutes": 3, "title": "Bonds",
            "caption": "  you have to hear this  "})
        assert made.status_code == 200
    client.post("/api/friends/follow", json={"user_id": them})
    story = client.get("/api/circle").json()["circle"][0]["stories"][0]
    assert story["caption"] == "you have to hear this"


def test_a_caption_is_cut_not_refused(store):
    store.echo("a", "why tides turn", "Tides", 3, caption="x" * 400)
    story = store.stories_among(["a"], 0)["a"][0]
    assert len(story["caption"]) == social_mod.MAX_CAPTION


def test_vibing_asks_for_a_caption_first_and_the_story_draws_it():
    assert "openVibeCaption(ep)" in _fn("toggleEcho")
    assert "caption: caption" in _fn("sendVibe")
    assert 'getElementById("storyCaption")' in _fn("showStory")
    assert 'id="vibeCaptionOverlay"' in INDEX


def test_a_long_thread_keeps_its_newest_comments(store):
    for i in range(5):
        store.add_comment("a", "why tides turn", 3, f"comment {i}")
    rows = store.comments("why tides turn", 3, limit=1)   # reads 4 rows
    assert rows[0]["text"] == "comment 4"


def test_deleting_an_account_leaves_no_likes_on_replies_it_hosted(store):
    top = store.add_comment("a", "why tides turn", 3, "first")
    reply = store.add_comment("b", "why tides turn", 3, "reply", parent_id=top["id"])
    store.like_comment("b", reply["id"])
    store.forget("a")
    left = store._conn().execute("SELECT COUNT(*) FROM comment_likes").fetchone()[0]
    assert left == 0
    assert store.comment_counts_many([("why tides turn", 3)]) == {("why tides turn", 3): 0}


# --- the 10.5 packet, checked again (§209) ----------------------------------

def test_a_guests_comment_sheet_hides_the_box_behind_the_sign_up():
    # `.rc-compose{display:flex}` outranked `hidden`, so a guest saw the box
    # drawn over "Sign up to comment".
    assert ".rc-compose[hidden], .rc-emoji[hidden], .rc-gate[hidden]{ display:none; }" in INDEX


@pytest.mark.parametrize("builder", ["build_preview.py", "build_live_preview.py"])
def test_both_previews_answer_comments_the_card_and_a_caption(builder):
    # Without these the owner's preview could not show 10.5 #8 and #9: the
    # sheet said "Could not load the comments", the reel had no picture,
    # and a vibe's caption was dropped.
    src = (ROOT / "preview" / builder).read_text()
    assert 'path === "/api/comments" && method === "GET"' in src
    assert 'path === "/api/comments" && method === "POST"' in src
    assert "\\/like$/" in src
    assert 'path === "/api/episode/card"' in src
    assert "previewPicture(" in src
    assert "caption: String(" in src


def test_the_live_preview_keeps_comments_in_the_shared_db():
    # Everybody who opens the page reads one thread, as on the server.
    src = (ROOT / "preview" / "build_live_preview.py").read_text()
    assert '"comments", "comment_likes"]' in src
