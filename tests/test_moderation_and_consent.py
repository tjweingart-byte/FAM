"""App Store groundwork: report and block (1.2), and asking before a
listener's words go to a third-party AI (5.1.2(i)). `moderation.py` and
`consent.py` say what each rule is for."""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import consent as consent_mod  # noqa: E402
import moderation as moderation_mod  # noqa: E402

WEB = {"X-FAM-Client": "web/live"}
EPISODE = {"query": "why the ocean is salty", "minutes": 2}


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)


def listener(name: str, handle: str) -> TestClient:
    c = TestClient(appmod.app)
    r = c.post("/api/auth/signup", json={"email": f"{handle}@example.com",
                                         "password": "password12"})
    assert r.status_code == 200, r.text
    assert c.post("/api/me", json={"name": name, "handle": handle}).status_code == 200
    return c


def uid(c: TestClient) -> str:
    return c.get("/api/auth/me").json()["user_id"]


# --- consent ---------------------------------------------------------------

def test_the_notice_names_the_provider_and_starts_unasked():
    c = TestClient(appmod.app)
    ai = c.get("/api/consent").json()["ai"]
    assert ai["provider"] == "Anthropic"
    assert "Anthropic" in ai["notice"]["body"]
    # The search drawn from the question goes to Exa, and the notice says so.
    assert "Exa" in ai["notice"]["body"]
    assert ai["asked"] is False and ai["given"] is False


def test_a_search_from_a_client_that_asks_waits_for_a_yes():
    c = TestClient(appmod.app)
    r = c.get("/api/audio", params={"q": "how do tides work", "surface": "search"},
              headers=WEB)
    assert r.status_code == 403
    assert r.headers.get("X-FAM-Consent") == "ai"
    # A no is kept, and still refuses.
    c.post("/api/consent", json={"allow": False})
    assert c.get("/api/consent").json()["ai"]["asked"] is True
    r = c.get("/api/audio", params={"q": "how do tides work", "surface": "search"},
              headers=WEB)
    assert r.status_code == 403


def test_a_yes_lets_the_words_through(monkeypatch):
    c = TestClient(appmod.app)
    assert c.post("/api/consent", json={"allow": True}).json()["ai"]["given"] is True
    seen = []
    monkeypatch.setattr(appmod, "_reserve",
                        lambda *a, **k: seen.append(1) or (_ for _ in ()).throw(
                            appmod.HTTPException(status_code=418, detail="stop")))
    r = c.get("/api/audio", params={"q": "how do tides work", "surface": "search"},
              headers=WEB)
    # Past the consent gate and on to the allowance, which this test stops.
    assert r.status_code == 418 and seen


def test_an_older_wording_is_not_a_yes_to_this_one(monkeypatch):
    store = appmod.CONSENT
    user = "listener-x"
    store.record(user, True, version=consent_mod.VERSION)
    assert store.given(user)
    monkeypatch.setattr(consent_mod, "VERSION", consent_mod.VERSION + 1)
    assert not store.given(user)


def test_only_the_listeners_own_words_need_it():
    assert appmod._sends_listener_words("search", "", "")
    # A suggested follow-up is FAM's words; a typed one is asked client-side.
    assert not appmod._sends_listener_words("other", "a heard topic", "")
    assert appmod._sends_listener_words("myfam", "", "att1")
    assert not appmod._sends_listener_words("myfam", "", "")
    assert not appmod._sends_listener_words("dailyfam", "", "")
    # The "where you live" tile names the place the listener set (§223).
    assert appmod._sends_listener_words("myfam", "", "", appmod.startup.LOCAL_ID)


def test_a_client_that_cannot_ask_is_not_broken():
    """A kept older web release never learned the question (`old-clients`)."""
    c = TestClient(appmod.app)
    r = c.get("/api/audio", params={"q": "how do tides work", "surface": "search"},
              headers={"X-FAM-Client": "web/2026.09.29"})
    assert r.headers.get("X-FAM-Consent") is None


def test_deleting_an_account_forgets_the_answer():
    c = listener("Ann", "ann")
    c.post("/api/consent", json={"allow": True})
    user = uid(c)
    assert appmod.CONSENT.given(user)
    appmod.erase_listener(user)
    assert appmod.CONSENT.get(user) is None


# --- reporting -------------------------------------------------------------

def test_a_reported_comment_leaves_the_reporters_screen_at_once():
    ann, ben = listener("Ann", "ann"), listener("Ben", "ben")
    cid = ben.post("/api/comments", json={**EPISODE, "text": "something vile"}).json()["id"]
    assert [x["id"] for x in ann.get("/api/comments", params={
        "q": EPISODE["query"], "minutes": 2}).json()["comments"]] == [cid]
    r = ann.post("/api/report", json={"kind": "comment", "target": str(cid),
                                      "reason": "harassment"})
    assert r.status_code == 200 and r.json()["hidden"] is True
    assert "24 hours" in r.json()["message"]
    assert ann.get("/api/comments", params={"q": EPISODE["query"], "minutes": 2}).json()["comments"] == []
    # Nobody else's view changes until a reviewer decides.
    assert ben.get("/api/comments", params={"q": EPISODE["query"], "minutes": 2}).json()["comments"]


def test_a_report_needs_a_reason_and_something_real():
    ann = listener("Ann", "ann")
    assert ann.post("/api/report", json={"kind": "comment", "target": "999",
                                         "reason": "spam"}).status_code == 404
    ben = listener("Ben", "ben")
    cid = ben.post("/api/comments", json={**EPISODE, "text": "hi"}).json()["id"]
    assert ann.post("/api/report", json={"kind": "comment", "target": str(cid),
                                         "reason": "because"}).status_code == 400
    # Your own comment is deleted, not reported.
    assert ben.post("/api/report", json={"kind": "comment", "target": str(cid),
                                         "reason": "spam"}).status_code == 400


def test_the_options_come_from_the_server():
    body = TestClient(appmod.app).get("/api/report").json()
    assert [r["id"] for r in body["reasons"]] == list(moderation_mod.REASON_IDS)
    assert body["review_hours"] == 24


def test_a_message_can_be_reported_only_by_somebody_in_the_conversation():
    ann, ben, cat = listener("Ann", "ann"), listener("Ben", "ben"), listener("Cat", "cat")
    ann.post("/api/friends/follow", json={"handle": "ben"})
    ben.post("/api/friends/follow", json={"handle": "ann"})
    sent = ben.post("/api/messages", json={"to": uid(ann), "text": "nasty"})
    assert sent.status_code == 200, sent.text
    mid = sent.json()["message"]["id"] if "message" in sent.json() else sent.json()["id"]
    assert cat.post("/api/report", json={"kind": "message", "target": str(mid),
                                         "reason": "harassment"}).status_code == 404
    assert ann.post("/api/report", json={"kind": "message", "target": str(mid),
                                         "reason": "harassment"}).status_code == 200
    thread = ann.get("/api/messages/thread", params={"with": uid(ben)}).json()
    assert all(m["id"] != mid for m in thread["messages"])


# --- blocking --------------------------------------------------------------

def test_a_block_is_both_ways_and_ends_the_follow():
    ann, ben = listener("Ann", "ann"), listener("Ben", "ben")
    ann.post("/api/friends/follow", json={"handle": "ben"})
    ben.post("/api/friends/follow", json={"handle": "ann"})
    ben.post("/api/comments", json={**EPISODE, "text": "from ben"})
    ann.post("/api/comments", json={**EPISODE, "text": "from ann"})

    assert ann.post("/api/block", json={"handle": "ben"}).json()["blocked"] is True

    def texts(c):
        return [x["text"] for x in c.get("/api/comments", params={
            "q": EPISODE["query"], "minutes": 2}).json()["comments"]]
    assert texts(ann) == ["from ann"]
    assert texts(ben) == ["from ben"]
    # No follow either way, no profile, no messages, no follow back.
    assert ann.get("/api/friends").json()["following"] == []
    assert ben.get("/api/friends").json()["following"] == []
    assert ben.get("/api/person", params={"handle": "ann"}).status_code == 404
    assert ann.get("/api/person", params={"handle": "ben"}).status_code == 404
    assert ben.post("/api/messages", json={"to": uid(ann), "text": "hey"}).status_code == 403
    assert ben.post("/api/friends/follow", json={"handle": "ann"}).status_code == 404
    assert ben.get("/api/people", params={"q": "ann"}).json()["people"] == []
    # Listed for the blocker, and undone from there.
    assert [p["handle"] for p in ann.get("/api/blocks").json()["people"]] == ["ben"]
    assert ann.delete("/api/block", params={"handle": "ben"}).json()["ok"] is True
    assert ann.get("/api/person", params={"handle": "ben"}).status_code == 200


def test_blocking_needs_an_account():
    listener("Ben", "ben")
    guest = TestClient(appmod.app)
    assert guest.post("/api/block", json={"handle": "ben"}).status_code == 401


def test_a_blocked_listeners_searches_leave_explore():
    entries = [{"query": "q one", "minutes": 2, "author": "u-ben"},
               {"query": "q two", "minutes": 2, "author": "u-cat"},
               {"query": "q three", "minutes": 2, "author": "u-ann"}]
    appmod.MODERATION.block("u-ann", "u-ben")
    # Both ways: Ann loses Ben's searches and Ben loses Ann's.
    assert "q one" not in [e["query"] for e in appmod._visible_episodes("u-ann", entries)]
    assert "q three" not in [e["query"] for e in appmod._visible_episodes("u-ben", entries)]
    assert len(appmod._visible_episodes("u-cat", entries)) == 3
    # A reviewer's removal is for everybody.
    appmod.MODERATION.hide_episode(moderation_mod.episode_target("q two", 2))
    assert "q two" not in [e["query"] for e in appmod._visible_episodes("u-cat", entries)]


# --- the reviewer ------------------------------------------------------------

def _as_admin(monkeypatch):
    monkeypatch.setattr(appmod, "_require_admin", lambda request: None)


def test_removing_takes_a_comment_down_for_everybody(monkeypatch):
    _as_admin(monkeypatch)
    ann, ben, cat = listener("Ann", "ann"), listener("Ben", "ben"), listener("Cat", "cat")
    cid = ben.post("/api/comments", json={**EPISODE, "text": "vile"}).json()["id"]
    rid = ann.post("/api/report", json={"kind": "comment", "target": str(cid),
                                        "reason": "hate"}).json()["id"]
    inbox = cat.get("/api/admin/reports").json()
    assert inbox["summary"]["open"] == 1
    row = inbox["reports"][0]
    assert row["posted_by"]["handle"] == "ben"
    assert "reporter" not in row  # never shown, even to the reviewer
    r = cat.post(f"/api/admin/reports/{rid}/resolve", json={"action": "removed"})
    assert r.status_code == 200 and r.json()["report"]["action"] == "removed"
    assert cat.get("/api/comments", params={"q": EPISODE["query"], "minutes": 2}).json()["comments"] == []


def test_suspending_stops_posting_and_hides_the_account(monkeypatch):
    _as_admin(monkeypatch)
    ann, ben = listener("Ann", "ann"), listener("Ben", "ben")
    cid = ben.post("/api/comments", json={**EPISODE, "text": "vile"}).json()["id"]
    rid = ann.post("/api/report", json={"kind": "comment", "target": str(cid),
                                        "reason": "harassment"}).json()["id"]
    ann.post(f"/api/admin/reports/{rid}/resolve", json={"action": "suspended"})
    assert ben.post("/api/comments", json={**EPISODE, "text": "again"}).status_code == 403
    assert ann.get("/api/person", params={"handle": "ben"}).status_code == 404
    # Still listens: suspension is about posting, not hearing.
    assert ben.get("/api/explore").status_code == 200


def test_a_person_is_not_content_to_remove(monkeypatch):
    _as_admin(monkeypatch)
    ann, ben = listener("Ann", "ann"), listener("Ben", "ben")
    rid = ann.post("/api/report", json={"kind": "person", "target": "ben",
                                        "reason": "harassment"}).json()["id"]
    assert ann.post(f"/api/admin/reports/{rid}/resolve",
                    json={"action": "removed"}).status_code == 400


def test_deleting_an_account_removes_its_blocks():
    ann, ben = listener("Ann", "ann"), listener("Ben", "ben")
    ann.post("/api/block", json={"handle": "ben"})
    appmod.erase_listener(uid(ann))
    assert appmod.MODERATION.apart(uid(ben)) == set()


# --- the terms ----------------------------------------------------------------

def test_the_terms_name_the_contact_once_it_is_set():
    page = appmod.terms_page("help@example.com")
    assert "mailto:help@example.com" in page
    assert "Zero tolerance" in page and "24 hours" in page
    assert "{{" not in appmod.terms_page("")


def test_the_terms_are_served_without_an_account():
    r = TestClient(appmod.app).get("/terms")
    assert r.status_code == 200 and "Reporting and blocking" in r.text


def test_the_privacy_and_support_pages_are_served_with_the_contact():
    for name in appmod.LEGAL_PAGES:
        page = appmod.legal_page(name, "help@example.com")
        assert "{{" not in page and "mailto:help@example.com" in page, name
    c = TestClient(appmod.app)
    privacy = c.get("/privacy")
    assert privacy.status_code == 200
    # Every company the policy says receives something is one the code uses.
    for company in ("Anthropic", "Exa", "RunPod", "Render", "Viral Loops"):
        assert company in privacy.text, company
    assert c.get("/support").status_code == 200


# --- §224: the slur filter on everything somebody else reads ----------------

def test_slurs_come_out_of_names_groups_mixes_and_messages():
    import accounts
    import messages
    import mixes
    import social
    assert "a slur" in accounts.clean_display_name("the gook squad").lower() \
        or "slur" in accounts.clean_display_name("the gook squad").lower()
    assert "wop" not in mixes.clean_name("wop songs").lower()
    assert messages.clean_text("you are a fag") == "you are a slur"
    # Swearing stays everywhere: only slurs are taken out (`slurs-only`).
    assert messages.clean_text("this is fucking great") == "this is fucking great"
    ann = listener("Ann", "ann")
    me = ann.post("/api/me", json={"name": "Ann the kike", "handle": "ann"}).json()
    assert "kike" not in me["name"].lower()


def test_a_handle_with_a_slur_is_refused_not_scrubbed():
    c = TestClient(appmod.app)
    r = c.post("/api/me", json={"name": "X", "handle": "big_spic"})
    assert r.status_code == 400 and "handle" in r.json()["error"].lower()
    # Whole words only: a handle that merely contains the letters is fine.
    assert c.post("/api/me", json={"name": "X", "handle": "dickens_fan"}).status_code == 200


# --- §224: a name that swears is marked, never refused ------------------------

def test_a_swearing_name_is_kept_and_marked():
    ann, ben = listener("Ann", "ann"), listener("Shithead Ben", "ben")
    assert ben.post("/api/comments", json={**EPISODE, "text": "hi"}).status_code == 200
    row = ann.get("/api/comments", params={"q": EPISODE["query"], "minutes": 2}).json()["comments"][0]
    assert row["name"] == "Shithead Ben" and row["explicit"] is True
    assert ann.get("/api/person", params={"handle": "ben"}).json()["explicit"] is True
    assert ben.get("/api/person", params={"handle": "ann"}).json()["explicit"] is False


# --- §224: the photo check ------------------------------------------------------

PHOTO = "data:image/jpeg;base64," + __import__("base64").b64encode(b"\xff\xd8\xff" + b"0" * 40).decode()


class _Text:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Response:
    def __init__(self, text="", stop_reason="end_turn"):
        self.content = [_Text(text)]
        self.stop_reason = stop_reason


def test_a_verdict_is_read_from_the_models_answer():
    import image_check as ic
    assert ic.verdict_from(_Response('{"allowed": true, "category": "ok"}')).allowed
    no = ic.verdict_from(_Response('{"allowed": false, "category": "nudity"}'))
    assert not no.allowed and no.category == "nudity" and no.checked
    # The model declining to look is the picture being the problem.
    assert not ic.verdict_from(_Response("", stop_reason="refusal")).allowed
    # An unreadable answer never costs somebody their photo.
    assert ic.verdict_from(_Response("not json")).allowed


async def _no_check(*a, **k):
    import image_check as ic
    return ic.Verdict(allowed=False, category="nudity", checked=True)


def test_a_refused_photo_is_not_kept(monkeypatch):
    import image_check as ic
    ann = listener("Ann", "ann")
    monkeypatch.setattr(ic, "check", _no_check)
    r = ann.post("/api/me", json={"name": "Ann", "handle": "ann", "avatar": PHOTO})
    assert r.status_code == 400 and r.json()["error"] == ic.REFUSED
    assert appmod.SOCIAL.person(uid(ann))["avatar"] == ""
    r = ann.post("/api/mixes", json={"name": "Mornings", "topic_ids": [], "cover": PHOTO})
    assert r.status_code == 400


def test_with_no_key_a_photo_goes_through_unchecked():
    """Staging has no key (`zero-spend-staging`): the check says it did not
    run, and the photo is kept - Report + Remove still covers it."""
    import asyncio
    import image_check as ic
    verdict = asyncio.run(ic.check(PHOTO))
    assert verdict.allowed and not verdict.checked


def test_an_unchanged_photo_is_not_checked_again(monkeypatch):
    import image_check as ic
    calls = []

    async def counting(*a, **k):
        calls.append(1)
        return ic.Verdict(allowed=True, checked=True)
    monkeypatch.setattr(ic, "check", counting)
    ann = listener("Ann", "ann")
    ann.post("/api/me", json={"name": "Ann", "handle": "ann", "avatar": PHOTO})
    ann.post("/api/me", json={"name": "Ann B", "handle": "ann", "avatar": PHOTO})
    assert len(calls) == 1


def test_the_photo_check_runs_on_the_cheap_model_and_is_costed():
    """§225: not the writer's model, and the ledger can price it."""
    import metering
    from config import settings
    assert settings.image_check_model == "claude-haiku-5-5"
    assert settings.image_check_model in metering.PRICES


# --- §226: regressions from the branch review ---------------------------------

def test_a_handle_with_an_underscore_can_be_blocked_and_found():
    """`find_people` strips `_` from its LIKE pattern, so an exact handle
    with one never matched itself; blocking it 404'd."""
    ann, _ = listener("Ann", "ann"), listener("John", "john_doe")
    assert ann.post("/api/block", json={"handle": "john_doe"}).status_code == 200
    assert ann.delete("/api/block", params={"handle": "john_doe"}).json()["ok"] is True
    assert ann.get("/api/person", params={"handle": "john_doe"}).status_code == 200


def test_an_unreadable_photo_is_refused_not_waved_through():
    import image_check as ic
    ann = listener("Ann", "ann")
    r = ann.post("/api/me", json={"name": "Ann", "handle": "ann",
                                  "avatar": "data:image/png;base64,iVBO RLG!!"})
    assert r.status_code == 400 and r.json()["error"] == ic.UNREADABLE


def test_a_report_and_the_cache_name_the_same_episode():
    """The player reports what was typed; the cache keeps it normalised."""
    ann = listener("Ann", "ann")
    ann.post("/api/report", json={"kind": "episode", "query": "Eagles  Game",
                                  "minutes": 2, "reason": "false"})
    entries = [{"query": "eagles game", "minutes": 2, "author": ""}]
    assert appmod._visible_episodes(uid(ann), entries) == []


def test_a_removed_episode_plays_for_nobody():
    appmod.MODERATION.hide_episode(appmod._episode_target("why the sky is blue", 2))
    r = TestClient(appmod.app).get("/api/audio", params={
        "q": "Why the sky is blue", "minutes": 2, "cached_only": 1})
    assert r.status_code == 410


def test_the_badge_never_counts_a_blocked_persons_messages():
    ann, ben = listener("Ann", "ann"), listener("Ben", "ben")
    ann.post("/api/friends/follow", json={"handle": "ben"})
    ben.post("/api/friends/follow", json={"handle": "ann"})
    ben.post("/api/messages", json={"to": uid(ann), "text": "one"})
    ben.post("/api/messages", json={"to": uid(ann), "text": "two"})
    assert ann.get("/api/messages").json()["unread"] == 2
    ann.post("/api/block", json={"handle": "ben"})
    assert ann.get("/api/messages").json()["unread"] == 0
    assert ann.get("/api/notifications").json()["unread"] == 0


def test_a_suspended_account_cannot_publish_a_mix_or_rename_itself(monkeypatch):
    ann = listener("Ann", "ann")
    appmod.MODERATION.suspend(uid(ann))
    assert ann.post("/api/mixes", json={"name": "Mine", "topic_ids": []}).status_code == 403
    assert ann.post("/api/me", json={"name": "New", "handle": "ann"}).status_code == 403


def test_an_oversized_report_id_is_a_404_not_a_500():
    ann = listener("Ann", "ann")
    r = ann.post("/api/report", json={"kind": "comment", "target": "9" * 120,
                                      "reason": "spam"})
    assert r.status_code == 404


def test_slurs_are_scrubbed_before_the_cut():
    import content_filter
    line = content_filter.clean_line("x" * 58 + " fag", 60)
    assert len(line) <= 60 and "fag" not in line


def test_a_menu_never_draws_a_name_as_html():
    """The story menu's Block row is HTML (`showActionSheet`); a display name
    in it is escaped like everywhere else."""
    html = (appmod.PROJECT_ROOT / "static" / "index.html").read_text()
    assert '"Block " + firstName(person)' not in html


def test_a_typed_go_deeper_question_waits_for_a_yes():
    """`own=1`: the listener's words, held to their answer like a search;
    the same follow-up without it is FAM's own suggestion."""
    assert appmod._sends_listener_words("other", "a heard topic", "", "", own=True)
    c = TestClient(appmod.app)
    r = c.get("/api/audio", params={"q": "and what about the bond market",
                                    "context": "the fed", "own": 1}, headers=WEB)
    assert r.status_code == 403 and r.headers.get("X-FAM-Consent") == "ai"


def test_a_taken_handle_never_pays_for_a_photo_check(monkeypatch):
    import image_check as ic
    calls = []

    async def counting(*a, **k):
        calls.append(1)
        return ic.Verdict(allowed=True, checked=True)
    monkeypatch.setattr(ic, "check", counting)
    listener("Ann", "ann")
    ben = listener("Ben", "ben")
    r = ben.post("/api/me", json={"name": "Ben", "handle": "ann", "avatar": PHOTO})
    assert r.status_code == 400 and "taken" in r.json()["error"]
    assert calls == []
