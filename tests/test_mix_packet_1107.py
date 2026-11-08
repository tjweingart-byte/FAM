"""The 11.7 myFAM packet (PROBLEMS.md §498): "Create new mix" as a pill under
the wordmark, step 1 alone makes the mix, a Spotify-style mix page with
"+ Add topics to mix" and recommended topics from what the listener plays,
long typed topics given a short name, joint mixes, and step 2 (the topic
picker) removed."""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
import mixes as mixes_mod  # noqa: E402
import topic_title  # noqa: E402
import topics as topics_mod  # noqa: E402

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


@pytest.fixture
def other(monkeypatch):
    with TestClient(appmod.app) as c:
        yield c


def signed_in(client, email, name, handle):
    client.post("/api/auth/signup", json={"email": email, "password": "password12"})
    client.post("/api/me", json={"name": name, "handle": handle})
    return client.get("/api/auth/me").json()["user_id"]


# --- the short name for a long typed topic -----------------------------------

def test_the_owners_example_is_named_by_rule():
    assert topic_title.heuristic(
        "positive story of the day of things happening in the world") == "Daily positive story"


@pytest.mark.parametrize("prompt", [
    "Stanford football", "AI chips", "the Eagles", "bitcoin",
])
def test_a_short_prompt_is_its_own_title(prompt):
    title, how = asyncio.run(topic_title.short_title(prompt, allow_model=False))
    assert how == "as-typed" and title.lower() == prompt.lower()


@pytest.mark.parametrize("prompt", [
    "what is going on with the Philadelphia Eagles this week",
    "tell me the latest news about AI chips every morning",
    "a summary of the biggest stories in tech today please",
    "news about bitcoin and the crypto markets and what moved them",
])
def test_a_long_prompt_is_never_shown_whole(prompt):
    title = topic_title.heuristic(prompt)
    assert title and title.lower() != prompt.lower()
    assert len(title.split()) <= topic_title.MAX_WORDS
    assert len(title) <= topic_title.MAX_CHARS


def test_names_keep_their_capitals():
    assert "Philadelphia Eagles" in topic_title.heuristic(
        "what is going on with the Philadelphia Eagles this week")
    assert "AI" in topic_title.heuristic("tell me the latest news about AI chips every morning")


def test_with_no_key_the_rule_names_it(monkeypatch):
    import credentials
    monkeypatch.setattr(credentials, "active", lambda name: "")
    title, how = asyncio.run(topic_title.short_title(
        "positive story of the day of things happening in the world"))
    assert (title, how) == ("Daily positive story", "rule")


def _answer(text):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def test_the_models_title_is_checked_before_it_is_kept():
    q = "positive story of the day of things happening in the world"
    assert topic_title.title_from(_answer('{"title": "Daily good news"}'), q) == "Daily good news"
    # The whole prompt back, or a sentence, is not a short name.
    assert topic_title.title_from(_answer(json.dumps({"title": q})), q) is None
    assert topic_title.title_from(_answer('{"title": "one two three four five six seven"}'), q) is None
    assert topic_title.title_from(_answer('{"title": ""}'), q) is None
    assert topic_title.title_from(_answer("not json"), q) is None


def test_a_typed_topic_sent_without_a_title_is_named(client):
    signed_in(client, "t1@b.com", "Tia", "tia1")
    made = client.post("/api/mixes", json={"name": "Mornings", "topic_ids": []}).json()
    assert made["items"] == [] and made["role"] == "owner"
    q = "positive story of the day of things happening in the world"
    saved = client.patch("/api/mixes/" + made["id"], json={"topic_ids": [{"query": q}]}).json()
    item = saved["items"][0]
    assert item["title"] == "Daily positive story"
    # The prompt is still what is asked every morning.
    assert item["query"] == q and q in item["daily_prompt"]


def test_a_topic_re_sent_with_its_title_is_not_renamed(client):
    signed_in(client, "t2@b.com", "Tom", "tom2")
    made = client.post("/api/mixes", json={
        "name": "Mine", "topic_ids": [{"query": "a long question about my own town council and its budget",
                                       "title": "Council budget"}]}).json()
    assert made["items"][0]["title"] == "Council budget"
    again = client.patch("/api/mixes/" + made["id"], json={"topic_ids": [
        {"query": made["items"][0]["query"], "title": made["items"][0]["title"]}]}).json()
    assert again["items"][0]["title"] == "Council budget"


# --- step 1 makes the mix -----------------------------------------------------

def test_a_mix_can_be_made_empty(client):
    signed_in(client, "e1@b.com", "Eve", "eve1")
    made = client.post("/api/mixes", json={"name": "Lunch break"})
    assert made.status_code == 200, made.text
    assert made.json()["items"] == [] and made.json()["joint"] is False


# --- recommended topics from what the listener plays ---------------------------

def test_the_catalogue_is_ranked_by_taste_and_never_filtered():
    flat = topics_mod.rank_catalogue({})
    assert [i.id for i in flat] == [i.id for i in topics_mod.INTEREST_CATALOGUE]
    ranked = topics_mod.rank_catalogue({"tech": 3.0})
    assert len(ranked) == len(topics_mod.INTEREST_CATALOGUE)
    assert "tech" in ranked[0].tags


def test_recommended_answers_for_a_guest(client):
    d = client.get("/api/mixes/recommended").json()
    assert d["order"] and all(i.startswith("f:") for i in d["order"])
    assert len(d["order"]) == len(topics_mod.INTEREST_CATALOGUE)


# --- joint mixes ----------------------------------------------------------------

def test_a_joint_mix_end_to_end(client, other):
    owner = signed_in(client, "j1@b.com", "Jo", "jo1")
    made = client.post("/api/mixes", json={"name": "Game day", "topic_ids": ["f:nfl"]}).json()
    invite = client.post("/api/mixes/" + made["id"] + "/invite").json()
    assert "/j/" in invite["url"]
    token = invite["url"].rsplit("/j/", 1)[1]
    # The same link every time.
    assert client.post("/api/mixes/" + made["id"] + "/invite").json()["url"] == invite["url"]

    member = signed_in(other, "j2@b.com", "Max", "max2")
    joined = other.post("/api/mixes/join", json={"token": token})
    assert joined.status_code == 200, joined.text
    body = joined.json()
    assert body["role"] == "member" and body["joint"] is True
    # Faces, never ids; the owner first.
    assert [p["handle"] for p in body["people"]] == ["jo1", "max2"]
    assert owner not in json.dumps(body) and member not in json.dumps(body)
    # A member never sees the owner's listen time.
    assert "listen_at" not in body

    # It is in the member's myFAM beside their own.
    listed = other.get("/api/mixes").json()["mixes"]
    assert [m["id"] for m in listed] == [made["id"]]

    # A member adds a topic; it carries their face.
    added = other.patch("/api/mixes/" + made["id"], json={
        "topic_ids": ["f:nfl", "f:basketball"]}).json()
    assert [i["id"] for i in added["items"]] == ["f:nfl", "f:basketball"]
    assert [i["added_by"] for i in added["items"]] == [0, 1]

    # ...but cannot rename it, make it private, or set its listen time.
    for change in ({"name": "Mine now"}, {"public": False}, {"listen_at": "07:00"}):
        refused = other.patch("/api/mixes/" + made["id"], json=change)
        assert refused.status_code == 403, change

    # The owner sees the member's addition, and who made it.
    mine = client.get("/api/mixes").json()["mixes"][0]
    assert mine["joint"] is True and [i["added_by"] for i in mine["items"]] == [0, 1]

    # A reorder by the owner keeps who added what.
    moved = client.patch("/api/mixes/" + made["id"], json={
        "topic_ids": ["f:basketball", "f:nfl"]}).json()
    assert [i["added_by"] for i in moved["items"]] == [1, 0]

    # The member leaves; what they added stays.
    assert other.post("/api/mixes/" + made["id"] + "/leave").json() == {"ok": True}
    assert other.get("/api/mixes").json()["mixes"] == []
    after = client.get("/api/mixes").json()["mixes"][0]
    assert after["joint"] is False and len(after["items"]) == 2


def test_stopping_a_joint_mix_turns_the_link_off(client, other):
    signed_in(client, "s1@b.com", "Sam", "sam1")
    made = client.post("/api/mixes", json={"name": "Gym"}).json()
    token = client.post("/api/mixes/" + made["id"] + "/invite").json()["url"].rsplit("/j/", 1)[1]
    signed_in(other, "s2@b.com", "Sue", "sue2")
    assert other.post("/api/mixes/join", json={"token": token}).status_code == 200
    client.delete("/api/mixes/" + made["id"] + "/invite")
    assert other.get("/api/mixes").json()["mixes"] == []
    assert other.post("/api/mixes/join", json={"token": token}).status_code == 404


def test_only_the_owner_can_invite_or_delete(client, other):
    signed_in(client, "o1@b.com", "Ola", "ola1")
    made = client.post("/api/mixes", json={"name": "Commute"}).json()
    token = client.post("/api/mixes/" + made["id"] + "/invite").json()["url"].rsplit("/j/", 1)[1]
    signed_in(other, "o2@b.com", "Oli", "oli2")
    other.post("/api/mixes/join", json={"token": token})
    assert other.post("/api/mixes/" + made["id"] + "/invite").status_code == 404
    assert other.delete("/api/mixes/" + made["id"]).status_code == 404
    assert client.get("/api/mixes").json()["mixes"][0]["id"] == made["id"]


def test_an_invite_needs_an_account(other):
    assert other.post("/api/mixes/join", json={"token": "x"}).status_code == 401


def test_the_invite_link_lands_in_the_app(client):
    r = client.get("/j/abc123", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/?join=abc123"


def test_a_copy_carries_nobodys_name(tmp_path):
    store = mixes_mod.MixStore(str(tmp_path / "mixes.db"))
    src = store.create("owner", "Src", ["f:nfl"])
    assert src.items[0].added_by == "owner"
    copy = store.add_copy("someone", src, "@owner")
    assert copy.items[0].added_by == ""
    # And `added_by` is stored, never served.
    assert "added_by" not in json.dumps(src.as_dict())


def test_an_erased_member_leaves_their_topics_under_nobodys_name(tmp_path):
    store = mixes_mod.MixStore(str(tmp_path / "mixes.db"))
    mix = store.create("owner", "Joint", ["f:nfl"])
    store.join("member", store.invite_token("owner", mix.id))
    store.update_items("member", mix.id, ["f:nfl", "f:basketball"])
    store.forget("member")
    kept = store.get("owner", mix.id)
    assert [i.added_by for i in kept.items] == ["owner", mixes_mod.FORMER_MEMBER]
    assert store.members(mix.id) == []
    # The owner's erasure takes the membership rows with the mix.
    store.join("member2", store.invite_token("owner", mix.id))
    store.forget("owner")
    assert store.members(mix.id) == []


# --- the interface --------------------------------------------------------------

def test_create_new_mix_is_a_pill_under_the_wordmark():
    page = _screen("playfam")
    head = page.index('class="myfam-header myfam-header-fixed"')
    pill = page.index('id="newMixBtn"')
    assert head < page.index('class="wordmark"') < pill < page.index('<div class="scroll"')
    tag = re.search(r'<button[^>]*id="newMixBtn"[^>]*>', page).group(0)
    assert "new-mix-pill" in tag and " hidden" in tag
    assert "Create new mix" in page.split('id="newMixBtn"', 1)[1].split("</button>", 1)[0]
    assert "new-mix-plus" not in INDEX


def test_step_one_is_the_only_step():
    page = _screen("newmix")
    assert "Step 1 of 2" not in page and "Quick picks" not in page
    assert 'id="nmInput"' in page and 'id="nmCreate"' in page and 'id="nmFolder"' in page
    body = _fn("confirmNewMix")
    assert 'saveMix("POST", "/api/mixes"' in body and "topic_ids: []" in body
    assert "openMix(" in body


def test_step_two_is_gone():
    for gone in ('id="screen-mixpicker"', "function renderMixPicker", "function saveMixPicker",
                 "function editMixTopics", "function focusPanelHTML", "pickerPayload",
                 "Anything specific in "):
        assert gone not in INDEX, gone


def test_the_mix_page_is_spotifys():
    body = _fn("renderMixBody")
    assert "Add topics to mix" in body and "openAddTopics()" in body
    assert "recommendedHTML(m)" in body
    assert 'class="mix-change"' in body, "no Change pill beside the name"
    rec = _fn("recommendedHTML")
    assert "Recommended topics" in rec and "addRecTopic(" in rec
    assert "openRecFocus" not in INDEX


def test_recommendations_use_the_servers_listening_order():
    assert '"/api/mixes/recommended"' in INDEX
    assert "recServerOrder" in _fn("recRanked")


def test_add_topics_sends_a_typed_topic_without_a_title():
    body = _fn("addTypedTopicToMix")
    assert "{ query: text }" in body
    assert "title" not in body.split("{ query: text }", 1)[0].rsplit("\n", 1)[1]


def test_the_player_still_adds_to_a_myfam_playlist():
    assert "Add to \" + myFamMark() + \" Playlist" in _fn("addToPlaylistLabel")


def test_a_joint_mix_can_be_made_and_joined_from_the_page():
    assert "/invite" in _fn("inviteCollaborators")
    assert "joinPendingMix()" in _fn("openLinkedJoin")
    assert '"/api/mixes/join"' in _fn("joinPendingMix")
    assert ".then(openLinkedJoin)" in INDEX
    # Signed out, the invite waits for the account.
    assert "joinPendingMix" in _fn("submitAuthForm") and "joinPendingMix" in _fn("finishEntry")


# --- the double-check's fixes -----------------------------------------------------

def test_a_member_who_left_and_then_erased_is_not_left_in_the_mix(tmp_path):
    store = mixes_mod.MixStore(str(tmp_path / "mixes.db"))
    mix = store.create("owner", "Joint", ["f:nfl"])
    store.join("member", store.invite_token("owner", mix.id))
    store.update_items("member", mix.id, ["f:nfl", "f:basketball"])
    store.leave("member", mix.id)
    store.forget("member")
    kept = store.get("owner", mix.id)
    assert [i.added_by for i in kept.items] == ["owner", mixes_mod.FORMER_MEMBER]


def test_the_owner_opening_their_own_invite_is_not_joint(client):
    signed_in(client, "own1@b.com", "Ona", "ona1")
    made = client.post("/api/mixes", json={"name": "Solo"}).json()
    token = client.post("/api/mixes/" + made["id"] + "/invite").json()["url"].rsplit("/j/", 1)[1]
    body = client.post("/api/mixes/join", json={"token": token}).json()
    assert body["role"] == "owner" and body["joint"] is False


def test_somebody_blocked_cannot_join(client, other):
    signed_in(client, "bl1@b.com", "Bea", "bea1")
    made = client.post("/api/mixes", json={"name": "Private club"}).json()
    token = client.post("/api/mixes/" + made["id"] + "/invite").json()["url"].rsplit("/j/", 1)[1]
    blocked = signed_in(other, "bl2@b.com", "Ben", "ben2")
    owner = client.get("/api/auth/me").json()["user_id"]
    appmod.MODERATION.block(owner, blocked)
    assert other.post("/api/mixes/join", json={"token": token}).status_code == 404
    assert other.get("/api/mixes").json()["mixes"] == []


def test_one_save_names_only_a_few_by_model(client, monkeypatch):
    asked = []

    async def fake(query, *, allow_model=True, usage=None):
        asked.append(allow_model)
        return topic_title.heuristic(query), "rule"

    monkeypatch.setattr(topic_title, "short_title", fake)
    signed_in(client, "cap1@b.com", "Cap", "cap1")
    long_ones = [{"query": "tell me every day about the news in town number %d please" % n}
                 for n in range(6)]
    client.post("/api/mixes", json={"name": "Many", "topic_ids": long_ones})
    assert asked.count(True) == topic_title.MAX_MODEL_PER_SAVE


@pytest.mark.parametrize("prompt, title", [
    ("what is going on with the New York Knicks this season", "New York Knicks"),
    ("the latest from the World Health Organization on bird flu", "World Health Organization bird flu"),
    ("give me a quick rundown of new music releases every friday", "Weekly new music releases"),
    ("how the world cup qualifiers are going for the USMNT every day", "Daily world cup qualifiers USMNT"),
    ("what is happening with iPhone 18 rumors lately", "iPhone 18 rumors"),
])
def test_names_survive_the_rule(prompt, title):
    """The second double-check: "world" and "new" were filler, so "New York"
    lost its first word; a capital typed mid-prompt is never filler now."""
    assert topic_title.heuristic(prompt) == title


def test_the_previews_rule_is_the_servers(tmp_path):
    """Both previews name typed topics with a JS copy of `heuristic`; its
    word lists come from this module, and its answers must match."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("no node")
    sys.path.insert(0, str(ROOT / "preview"))
    import build_preview as bp
    prompts = ["positive story of the day of things happening in the world",
               "what is going on with the New York Knicks this season",
               "give me a quick rundown of new music releases every friday",
               "what is happening with iPhone 18 rumors lately",
               "news about bitcoin and the crypto markets and what moved them",
               "Stanford football"]
    prog = ("var MIX_RULES; " + bp.mix_items_js()
            + "\nconsole.log(JSON.stringify(%s.map(mixShortTitle)));" % json.dumps(prompts))
    out = subprocess.run([node, "-e", prog], capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == [topic_title.heuristic(p) for p in prompts]
