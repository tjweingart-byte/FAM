"""The 10.15 packet (PROBLEMS.md §282)."""
from __future__ import annotations

import os
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod
from tests.test_waitlist import PASSWORD, _join, world  # noqa: F401

ROOT = pathlib.Path(__file__).resolve().parent.parent
WAITLIST_PAGE = (ROOT / "static" / "waitlist.html").read_text(encoding="utf-8")


# --- the waitlist's friend search --------------------------------------------

def _uid(client):
    return client.get("/api/auth/me").json()["user_id"]


def test_a_friend_on_the_waitlist_is_found_by_name_email_or_phone(world):
    brady, _ = _join("brady@fam.test")
    brady.post("/api/me", json={"name": "Brady Thompson", "handle": "brady"})
    world.ACCOUNTS._conn().execute(
        "UPDATE accounts SET phone = ? WHERE user_id = ?", ("+15551234567", _uid(brady)))
    me, _ = _join("me@fam.test")

    by_name = me.get("/api/waitlist/people?q=brady th").json()["people"]
    assert [p["name"] for p in by_name] == ["Brady Thompson"]
    assert by_name[0]["waitlisted"] and not by_name[0]["added"]
    found = me.get("/api/waitlist/people?q=BRADY@fam.test").json()["people"]
    assert [p["handle"] for p in found] == ["brady"]
    # A number typed without its country code is tried as a US number.
    found = me.get("/api/waitlist/people?q=(555) 123-4567").json()["people"]
    assert [p["handle"] for p in found] == ["brady"]
    # Only whole: part of an address or a number lists nobody.
    assert me.get("/api/waitlist/people?q=brady@").json()["people"] == []
    assert me.get("/api/waitlist/people?q=5551234").json()["people"] == []
    # What matched is never echoed back.
    assert "phone" not in found[0] and "email" not in found[0]
    # Nobody by that name: the page's invite card.
    assert me.get("/api/waitlist/people?q=nobody here").json()["people"] == []


def test_adding_is_a_follow_and_both_ways_is_your_fam(world):
    brady, _ = _join("brady@fam.test")
    brady.post("/api/me", json={"name": "Brady", "handle": "brady"})
    me, _ = _join("me@fam.test")
    me.post("/api/me", json={"name": "Ian", "handle": "ian"})
    target = me.get("/api/waitlist/people?q=brady").json()["people"][0]
    r = me.post("/api/waitlist/add", json={"user_id": target["user_id"]}).json()
    assert r["ok"] and not r["friend"]
    assert me.get("/api/waitlist/people?q=brady").json()["people"][0]["added"]
    back = brady.get("/api/waitlist/people?q=ian").json()["people"][0]
    assert brady.post("/api/waitlist/add", json={"user_id": back["user_id"]}).json()["friend"]
    assert [p["name"] for p in me.get("/api/waitlist/me").json()["fam"]] == ["Brady"]


def test_adding_nobody_or_yourself_is_refused(world):
    me, _ = _join("me@fam.test")
    assert me.post("/api/waitlist/add", json={"user_id": "nobody"}).status_code == 404
    assert me.post("/api/waitlist/add", json={"user_id": _uid(me)}).status_code == 404
    assert TestClient(appmod.app).get("/api/waitlist/people?q=x").status_code == 401


def test_the_status_page_searches_and_has_no_unlock_row():
    assert 'id="friendSearch"' in WAITLIST_PAGE
    assert "Search friends on the waitlist" in WAITLIST_PAGE
    assert "/api/waitlist/people" in WAITLIST_PAGE and "/api/waitlist/add" in WAITLIST_PAGE
    assert "Invite them and move up the waitlist" in WAITLIST_PAGE
    assert "unlockRow" not in WAITLIST_PAGE and "Until your next unlock" not in WAITLIST_PAGE


# --- forgot password on the waitlist's sign-in -------------------------------

def test_the_waitlist_sign_in_offers_the_same_reset_as_the_app():
    for piece in ('id="forgotLink"', 'id="resetForm"', "/api/auth/reset/start",
                  "/api/auth/reset/finish", 'autocomplete="one-time-code"'):
        assert piece in WAITLIST_PAGE, piece
    # Drawn only when the server can send the code (no-dead-controls).
    assert 'id="forgotLink">' in WAITLIST_PAGE
    assert 'class="quiet-link label hidden" type="button" id="forgotLink"' in WAITLIST_PAGE
    assert '$("forgotLink").classList.toggle("hidden", !(d && d.available))' in WAITLIST_PAGE


def test_the_reset_endpoints_pass_the_waitlist_gate(world):
    guest = TestClient(appmod.app)
    assert guest.get("/api/auth/reset").status_code == 200


# --- private search ------------------------------------------------------------

import cache as cache_mod  # noqa: E402
from pipeline import PodcastPipeline  # noqa: E402
from tts import DebugEngine  # noqa: E402

from test_pipeline import FakeGenerator  # noqa: E402

INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
FAM_AUDIO = (ROOT / "static" / "fam-audio.js").read_text(encoding="utf-8")


def test_a_private_row_is_never_listed(tmp_path):
    for store in (cache_mod.MemoryScriptCache(),
                  cache_mod.SqliteScriptCache(str(tmp_path / "c.db"))):
        store.put("open", ["One."], 600, "why tides turn", "", 2, origin="search")
        store.put("hush", ["Two."], 600, "a private question", "", 2,
                  origin=cache_mod.PRIVATE_ORIGIN)
        assert [e["key"] for e in store.recent()] == ["open"]
        assert [e["key"] for e in store.recent(origin="search")] == ["open"]
        assert [e["key"] for e in store.recent(origin=cache_mod.PRIVATE_ORIGIN)] == ["hush"]


@pytest.fixture
def searcher(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "DEMO_MODE", False)
    monkeypatch.setattr(appmod, "SCRIPT_CACHE",
                        cache_mod.SqliteScriptCache(str(tmp_path / "e.db")))
    monkeypatch.setattr(
        appmod, "_make_pipeline",
        lambda voice=None, author="": PodcastPipeline(
            generator=FakeGenerator(ratio=1.0), engine=DebugEngine(),
            cache=appmod.SCRIPT_CACHE, voice=voice, author=author))
    with TestClient(appmod.app) as c:
        c.post("/api/auth/signup", json={"email": "p@b.com", "password": "password12"})
        yield c


def _plays(query):
    return appmod.EVENTS._conn().execute(
        "SELECT COUNT(*) FROM events WHERE kind = 'play' AND text = ?",
        (query,)).fetchone()[0]


def test_a_private_search_is_heard_and_left_nowhere(searcher):
    q = "how deep the mariana trench goes"
    res = searcher.get(f"/api/audio?q={q}&minutes=1&fmt=pcm&surface=search&private=1")
    assert res.status_code == 200
    assert _plays(q) == 0, "a private search's play was recorded"
    store = appmod.SCRIPT_CACHE
    assert all(e["query"] != q for e in store.recent())
    row = store.recent(origin=cache_mod.PRIVATE_ORIGIN)
    assert [e["query"] for e in row] == [q] and row[0]["author"] == ""
    assert searcher.get("/api/explore").json()["episodes"] == []
    # The same question searched in the open is recorded as ever.
    other = "why the mariana trench is so deep"
    searcher.get(f"/api/audio?q={other}&minutes=1&fmt=pcm&surface=search")
    assert _plays(other) == 1


def test_private_is_a_search_only_flag(searcher):
    q = "the history of the paperclip"
    searcher.get(f"/api/audio?q={q}&minutes=2&fmt=pcm&surface=myfam&private=1")
    assert _plays(q) == 1


def test_the_search_page_has_private_search():
    assert 'id="privateBubble"' in INDEX and "setPrivateSearch(" in INDEX
    assert "#screen-home.private-search{" in INDEX
    assert "private: privateSearch" in INDEX
    assert "if(currentPlayPrivate) return;" in INDEX
    assert "if(t.private) return \"\";" in INDEX
    assert '"&private=1"' in FAM_AUDIO


# --- Instant feedback names the APIs an episode used ----------------------------

import json  # noqa: E402

import live_routing  # noqa: E402


class _Reported:
    async def episode_meta(self, plan, current_only=False):
        return {"title": "The Eagles Last Night", "thread": ""}

    async def sources_for(self, plan):
        return json.dumps({"items": [
            {"label": "BALLDONTLIE", "title": "", "at": "", "tier": "scoreboard",
             "kind": "live", "url": "", "private": False},
            {"label": "Wikipedia", "title": "Philadelphia Eagles", "at": "",
             "tier": "", "kind": "article", "url": "", "private": False}],
            "retrievers": ["parallel", "perplexity"]})

    async def captions_for(self, plan):
        return (["They won."], False, True)


def test_a_report_lists_the_services_by_name_and_never_a_key(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "secret")
    monkeypatch.setattr(appmod, "_make_pipeline", lambda *a, **k: _Reported())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-very-secret")
    live_routing.reset(live_routing.RoutingStore(str(tmp_path / "r.db")))

    class Brief:
        live_domain, subject, intent, degraded = "sports", "Philadelphia Eagles", "recap", False

    class Live:
        domain, outcome, detail = "sports", "facts", ""
        attempts = [("balldontlie", "facts", "")]
    live_routing.record("did the eagles win", Brief(), Live())
    client = TestClient(appmod.app)
    rid = client.post("/api/feedback", json={
        "text": "Wrong score", "screen": "player",
        "episode": {"q": "did the eagles win", "minutes": 2, "context": "",
                    "title": "x"}}).json()["id"]
    report = [r for r in client.get("/api/admin/feedback",
                                    headers={"X-Admin-Token": "secret"})
              .json()["reports"] if r["id"] == rid][0]
    services = report["episode"]["services"]
    pairs = {(s["service"], s["role"]) for s in services}
    assert {("Parallel", "Search"), ("Perplexity", "Search"),
            ("BALLDONTLIE", "Live data"), ("Wikipedia", "Reference"),
            ("BALLDONTLIE", "Live lookup"), ("Anthropic", "Brief"),
            ("Anthropic", "Writer")} <= pairs
    assert "sk-ant" not in json.dumps(report)
    live_routing.reset()


def test_the_inbox_draws_the_apis_used():
    admin = (ROOT / "admin_ui" / "tracker.html").read_text(encoding="utf-8")
    assert "APIs used" in admin and "e.services" in admin


# --- a named team outranks an off-branch category for the picture ------------

import categories as C  # noqa: E402
import thumbnails as th  # noqa: E402
import topics as T  # noqa: E402


@pytest.fixture
def betting_tree():
    t = T.category_tree()
    C.apply_seed(t)
    t.mint("sports betting", parent_id="sports")
    return t


def _paint(node, facet="sports"):
    th.store().put(th.Thumb(node, th.STATUS_APPROVED, facet=facet, mime="image/png"),
                   b"img-" + node.encode())


def test_a_seahawks_episode_filed_under_betting_wears_the_seahawks(betting_tree):
    """The 10.15 packet's screenshot: "Seahawks Thursday Odds and Defensive
    Absences" over the betting node's poker chips."""
    _paint("sports betting")
    _paint("seahawks")
    words = "Seahawks Thursday Odds and Defensive Absences"
    assert th.pick(words, category="sports betting")["node"] == "seahawks"
    got = th.pick_for_player(words, category="sports betting")
    assert got["node"] == "seahawks" and not got["fallback"]


def test_the_subject_unpainted_borrows_up_its_own_branch(betting_tree):
    _paint("sports betting")
    _paint("nfl")
    got = th.pick_for_player("Seahawks Thursday Odds", category="sports betting")
    assert got["node"] == "nfl" and got["fallback"]


def test_a_category_on_the_subjects_branch_keeps_its_picture(betting_tree):
    _paint("nfl")
    _paint("seahawks")
    # The NFL for a Seahawks question: the category's own picture.
    assert th.pick("Seahawks Thursday Odds", category="nfl")["node"] == "nfl"


def test_words_that_name_only_a_facet_never_outrank_the_category(betting_tree):
    """§209's case stands: a press conference whose words name no league."""
    _paint("sports betting")
    _paint("sports")
    assert th.pick("what the sports world said today",
                   category="sports betting")["node"] == "sports betting"


# --- reply, like and share on a friend's vibe ----------------------------------

def _js(name):
    return INDEX.split("function " + name + "(", 1)[1].split("\n  }\n", 1)[0]


def test_a_story_has_instagrams_reply_row():
    viewer = INDEX.split('id="storyOverlay"', 1)[1].split('id="vibeComposer"', 1)[0]
    assert 'id="storyReplyInput"' in viewer and 'placeholder="Send message..."' in viewer
    assert 'onclick="likeStory()"' in viewer and 'onclick="shareStoryToMine()"' in viewer
    # Typing holds the story; the box is not on your own.
    assert 'onfocus="pauseStory()"' in viewer
    assert "row.hidden = !!person.mine" in _js("drawStoryReply")
    # A reply and a like are messages to them carrying the episode.
    post = _js("postStoryMessage")
    assert "to: person.user_id" in post and "query: v.query" in post
    assert "postStoryMessage(person, v, STORY_LIKE)" in _js("likeStory")
    assert '"/api/rate"' in _js("likeStory")
    # Share to your story is VIBE! on this episode, with its picture.
    assert "openVibeComposer(ep)" in _js("shareStoryToMine")
    assert "var thumb = ep.thumb || \"\";" in INDEX
    # The chat says what it was.
    assert "replied to your vibe" in INDEX and "liked your vibe" in INDEX


def test_a_reply_is_one_message_with_its_episode(world):
    a, _ = _join("a@fam.test")
    b, _ = _join("b@fam.test")
    for c in (a, b):
        world.WAITLIST.grant([_uid(c)])
    r = a.post("/api/messages", json={"to": _uid(b), "text": "Great one",
                                      "query": "why tides turn", "minutes": 2,
                                      "title": "Why Tides Turn"})
    assert r.status_code == 200, r.text
    m = r.json()["message"]
    assert m["kind"] == "episode" and m["text"] == "Great one"
