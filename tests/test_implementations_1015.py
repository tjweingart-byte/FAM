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
    # It plays as a search - the surface the server keeps private - and is
    # left out of history at the history call, never by changing its surface.
    assert "var historyWhere = t.private ? \"\" : historySurface(t);" in INDEX
    assert "t.private" not in INDEX.split("function historySurface(", 1)[1].split("\n  }\n", 1)[0]
    assert "kind === \"complete\" || kind === \"skip\"" in INDEX
    # A trending search tapped with Private on is private too.
    trend = INDEX.split("function runTrendSearch(", 1)[1].split("\n  }\n", 1)[0]
    assert "private: privateSearch" in trend
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


# --- review fixes -----------------------------------------------------------

def test_the_waitlist_search_is_for_the_people_on_the_list(world):
    """Somebody already in the app has FAM's own search, which keeps the
    waitlist out of discovery; a direct call does not get round it."""
    member, _ = _join("in@fam.test")
    world.WAITLIST.grant([_uid(member)])
    assert member.get("/api/waitlist/people?q=brady").status_code == 403
    assert member.post("/api/waitlist/add", json={"user_id": "x"}).status_code == 403


def test_exact_lookups_are_capped_a_day(world, monkeypatch):
    monkeypatch.setattr(world, "WAITLIST_EXACT_PER_DAY", 2)
    monkeypatch.setattr(world, "_WAITLIST_EXACT", {})
    brady, _ = _join("brady@fam.test")
    brady.post("/api/me", json={"name": "Brady", "handle": "brady"})
    me, _ = _join("me@fam.test")
    me.get("/api/waitlist/people?q=one@fam.test")
    me.get("/api/waitlist/people?q=two@fam.test")
    # The third address is not looked up; a name still is.
    assert me.get("/api/waitlist/people?q=brady@fam.test").json()["people"] == []
    assert [p["handle"] for p in me.get("/api/waitlist/people?q=brady").json()["people"]] == ["brady"]


def test_an_address_alone_finds_nobody_without_a_name(world):
    _join("blank@fam.test")
    me, _ = _join("me@fam.test")
    assert me.get("/api/waitlist/people?q=blank@fam.test").json()["people"] == []


def test_a_reply_to_a_vibe_is_not_a_share(world):
    a, _ = _join("a@fam.test")
    b, _ = _join("b@fam.test")
    world.WAITLIST.grant([_uid(a), _uid(b)])

    def shares():
        return world.EVENTS._conn().execute(
            "SELECT COUNT(*) FROM events WHERE kind = 'share' AND user_id = ?",
            (_uid(a),)).fetchone()[0]
    before = shares()
    a.post("/api/messages", json={"to": _uid(b), "text": "Nice", "query": "why tides turn",
                                  "minutes": 2, "reply": True})
    assert shares() == before
    a.post("/api/messages", json={"to": _uid(b), "query": "why tides turn", "minutes": 2})
    assert shares() == before + 1
    assert "reply: true" in INDEX.split("function postStoryMessage(", 1)[1].split("\n  }\n", 1)[0]


def test_a_word_on_another_facet_never_outranks_the_category(betting_tree):
    """The owner 11.9 ("Rock Lead Theory", §509): a grown `lead` node under
    science - the metal - was two levels deep, and "Scheffler takes a
    two-stroke lead" put a golf episode on its picture. A named subject
    outranks the category only on the category's own facet (§282). `lead`
    itself is never alone now (§510), so another word stands in for it."""
    betting_tree.mint("irons", parent_id="materials science")
    betting_tree.reload()
    _paint("irons", facet="science")
    _paint("golf")
    words = "PGA Tour today: Scheffler's irons keep him two strokes clear"
    assert "irons" in betting_tree.match(words)
    assert th.pick(words, category="golf")["node"] == "golf"
    assert th.pick_for_player(words, category="golf")["node"] == "golf"
    assert th.pick_for_tile(words, category="golf")["node"] == "golf"


def test_lead_is_never_a_category_on_its_own(betting_tree):
    """§510: "lead" is never minted alone, and a `lead` node grown before
    the rule reads as nothing, so the deployed tree needs no migration."""
    import category_seed

    assert "lead" in category_seed.NEVER_ALONE
    assert betting_tree.mint("lead", parent_id="materials science") is None
    # A node already in the table, as on the deployment.
    betting_tree._conn().execute(
        "INSERT INTO categories (id, label, parent_id, depth, source,"
        " first_seen, last_seen, uses, listeners, degraded)"
        " VALUES ('lead', 'lead', 'materials science', 2, 'grown', 0, 0, 3, 3, 0)")
    betting_tree.reload()
    _paint("lead", facet="science")
    words = "Scheffler takes a two-stroke lead in Houston"
    assert "lead" not in betting_tree.match(words)
    got = th.pick_for_player(words)
    assert not got or got["node"] != "lead"
    # §512: nor does a phrase holding it.
    assert betting_tree.mint("lead poisoning", parent_id="materials science") is None


def test_no_everyday_word_is_a_category_on_its_own(betting_tree):
    """§511: every word in `NEVER_ALONE` is refused alone and matches
    nothing when an older tree already holds it; none is a seed node or an
    alias, which the list would otherwise silence."""
    import category_seed

    seeded = set()

    def walk(branch):
        for name, rest in branch.items():
            seeded.add(name)
            walk(rest)

    walk(category_seed.SEED)
    assert not category_seed.NEVER_ALONE & seeded
    assert not category_seed.NEVER_ALONE & set(category_seed.ALIASES)
    assert not category_seed.NEVER_ALONE & set(category_seed.ALIASES.values())
    for word in sorted(category_seed.NEVER_ALONE):
        assert betting_tree.mint(word, parent_id="science") is None, word
        betting_tree._conn().execute(
            "INSERT INTO categories (id, label, parent_id, depth, source,"
            " first_seen, last_seen, uses, listeners, degraded)"
            " VALUES (?, ?, 'science', 1, 'grown', 0, 0, 3, 3, 0)",
            (word, word))
    betting_tree.reload()
    for word in sorted(category_seed.NEVER_ALONE):
        assert word not in betting_tree.match(f"the {word} on Sunday"), word


def test_the_report_lists_grown_one_word_nodes(betting_tree):
    """§511: `categories_report.py --alone` is how the next everyday word
    is found on a deployment: grown one-word nodes, marked when silenced."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "tools"))
    import categories_report

    betting_tree.mint("irons", parent_id="materials science", listeners=4)
    _grown(betting_tree, "lead", "materials science", 2)
    _grown(betting_tree, "record label", "culture", 1)
    betting_tree.reload()
    rows = {r["node"]: r for r in categories_report.alone_rows(betting_tree)}
    assert rows["irons"]["branch"].endswith("materials science > irons")
    assert not rows["irons"]["silenced"]
    assert rows["lead"]["silenced"]
    assert rows["record label"]["silenced"]
    assert rows["record label"]["branch"] == "culture > record label"
    assert "golf" not in rows          # the seed is not a candidate



def _grown(tree, phrase, parent, depth):
    tree._conn().execute(
        "INSERT INTO categories (id, label, parent_id, depth, source,"
        " first_seen, last_seen, uses, listeners, degraded)"
        " VALUES (?, ?, ?, ?, 'grown', 0, 0, 3, 3, 0)",
        (phrase, phrase, parent, depth))


def test_a_grown_phrase_holding_an_everyday_word_is_not_a_category(betting_tree):
    """§512, the owner: "longer phrases can't be categories. These are not
    important categories to have." A grown phrase holding a `NEVER_ALONE`
    word is refused at mint and, already on disk, left out of the tree as
    read: no match, no `get`, not in `nodes()`; listed by `silenced()`."""
    for phrase in ("record label", "power bill", "lead poisoning"):
        assert betting_tree.mint(phrase, parent_id="science") is None, phrase
        _grown(betting_tree, phrase, "science", 1)
    # A child of a silenced node keeps its place, without it.
    _grown(betting_tree, "usain bolt", "record label", 2)
    betting_tree.reload()
    for phrase in ("record label", "power bill", "lead poisoning"):
        assert betting_tree.get(phrase) is None
        assert phrase not in betting_tree.nodes()
        assert phrase not in betting_tree.match(f"the {phrase} story")
        assert phrase in betting_tree.silenced()
    found = betting_tree.match("Usain Bolt")
    assert "usain bolt" in found and "record label" not in found
    # A phrase without a listed word grows as before.
    assert betting_tree.mint("wildfire", parent_id="science")


def test_the_seeds_own_phrases_are_never_silenced(betting_tree):
    """§512: "super bowl", "stock market", "supreme court" are the owner's
    and stay - seeded, refreshed by a sweep, or first grown before the seed
    named them (a grown source)."""
    for phrase in ("super bowl", "world cup", "stanley cup", "stock market",
                   "bond market", "supreme court", "trade policy"):
        assert betting_tree.get(phrase) is not None, phrase
        assert phrase in betting_tree.match(f"the {phrase} this week"), phrase
        assert betting_tree.mint(phrase, source="grown", uses=1), phrase
    betting_tree._conn().execute(
        "UPDATE categories SET source = 'grown' WHERE id = 'world cup'")
    betting_tree.reload()
    assert betting_tree.get("world cup") is not None
    # An alias the seed wrote still answers as its node.
    assert "golf" in betting_tree.match("the Ryder Cup this week")


def test_the_owners_phrases_with_everyday_words_are_seeded(betting_tree):
    """§513: "nfl draft", "trade deadline" and "salary cap" hold
    `NEVER_ALONE` words, so only the seed can hold them (§512)."""
    assert betting_tree.ancestors("nfl draft")[:2] == ["nfl", "american football"]
    assert betting_tree.get("trade deadline").parent_id == "sports"
    assert betting_tree.get("salary cap").parent_id == "sports"
    assert "nfl draft" in betting_tree.match("Who the Bears take in the NFL Draft")
    assert "trade deadline" in betting_tree.match("moves before the trade deadline")
    assert "salary cap" in betting_tree.match("the salary cap next season")
    _paint("nfl draft")
    _paint("nfl")
    got = th.pick_for_player("Who the Bears take in the NFL Draft")
    assert got["node"] == "nfl draft"


def test_world_cup_qualifiers_and_power_outage_are_seeded(betting_tree):
    """§514: both hold `NEVER_ALONE` words ("cup", "power"), so only the
    seed can hold them (§512)."""
    import category_seed

    assert betting_tree.get("world cup qualifiers").parent_id == "world cup"
    assert betting_tree.get("power outage").parent_id == "local news"
    assert "power outage" in category_seed.LOCAL_NODES
    assert "world cup qualifiers" in betting_tree.match(
        "USA's World Cup qualifiers this week")
    assert "power outage" in betting_tree.match("the power outage in Austin")
    _paint("world cup qualifiers")
    _paint("world cup")
    got = th.pick_for_player("Where Italy stands in the World Cup qualifiers")
    assert got["node"] == "world cup qualifiers"



def test_power_grid_and_world_record_are_seeded(betting_tree):
    """§515: both hold `NEVER_ALONE` words, so only the seed holds them."""
    assert betting_tree.get("power grid").parent_id == "energy security"
    assert betting_tree.get("world record").parent_id == "sports"
    assert "power grid" in betting_tree.match("strain on the Texas power grid")
    assert "world record" in betting_tree.match("a new marathon world record")
