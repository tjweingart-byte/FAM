"""The pre-launch waitlist (WAITLIST.md).

One database, one account: a waitlist signup is a real account, waitlisted.
The gate is enforced by the server; Viral Loops is never waited on and never
loses a signup; waitlisted accounts are out of discovery except to friends;
profile setup cannot touch status, place or admin fields.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import pathlib
import re
import sqlite3
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import accounts as accounts_mod
import app as appmod
import messages as messages_mod
import preferences as prefs_mod
import social as social_mod
import viral_loops as vl_mod
import waitlist as waitlist_mod

PASSWORD = "correct horse battery"
ROOT = pathlib.Path(__file__).resolve().parent.parent


# --- the store --------------------------------------------------------------

@pytest.fixture
def acc(tmp_path):
    store = accounts_mod.AccountStore(str(tmp_path / "accounts.db"))
    store.new_account_status = "waitlisted"
    return store


@pytest.fixture
def wl(acc):
    return waitlist_mod.Waitlist(acc)


@pytest.mark.sqlite_file
def test_existing_accounts_are_backfilled_active(tmp_path):
    """Widening a file made before the waitlist leaves everybody in it active:
    they were let in before there was a line."""
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE accounts (user_id TEXT PRIMARY KEY, email TEXT NOT NULL,"
                 " password TEXT NOT NULL, created REAL NOT NULL,"
                 " last_login REAL NOT NULL DEFAULT 0)")
    conn.execute("INSERT INTO accounts VALUES ('old', 'old@fam.test', 'x', 1, 1)")
    conn.commit(); conn.close()
    store = accounts_mod.AccountStore(path)
    assert store.account("old")["status"] == "active"
    assert store.listener_of("old").status == "active"


def test_gate_off_new_accounts_start_active(tmp_path):
    store = accounts_mod.AccountStore(str(tmp_path / "a.db"))
    assert store.sign_up("u1", "a@fam.test", PASSWORD).status == "active"


def test_gate_on_every_route_creates_a_waitlisted_account(acc):
    assert acc.sign_up("e", "e@fam.test", PASSWORD).status == "waitlisted"
    assert acc.sign_up_phone("p", "+14155550100", PASSWORD).status == "waitlisted"
    listener, _ = acc.sign_in_with("apple", "sub-1", email="ap@fam.test",
                                   current_user_id="ap")
    assert listener.status == "waitlisted"


def test_referral_sets_inviter_once_and_ignores_bad_codes(acc, wl):
    acc.sign_up("inviter", "i@fam.test", PASSWORD)
    code = wl.join("inviter")["code"]
    acc.sign_up("friend", "f@fam.test", PASSWORD)
    assert wl.join("friend", code.upper())["referrer"] == "inviter"
    # Set once: a second code later changes nothing.
    acc.sign_up("other", "o@fam.test", PASSWORD)
    other = wl.join("other")["code"]
    assert wl.join("friend", other)["referrer"] == "inviter"
    # Own code and unknown codes are ignored, never refused.
    acc.sign_up("solo", "s@fam.test", PASSWORD)
    solo_code = wl.ensure_code("solo")
    assert wl.join("solo", solo_code)["referrer"] == ""
    acc.sign_up("lost", "l@fam.test", PASSWORD)
    assert wl.join("lost", "nosuchcode")["referrer"] == ""


def test_place_orders_by_invites_then_join_time(acc, wl):
    for n, uid in enumerate(["a", "b", "c", "d"]):
        acc.sign_up(uid, f"{uid}@fam.test", PASSWORD, at=100 + n)
        wl.join(uid, at=100 + n)
    assert [r["user_id"] for r in wl.ordered()] == ["a", "b", "c", "d"]
    # d brings one person in and moves ahead of everybody with none.
    acc.sign_up("e", "e@fam.test", PASSWORD, at=200)
    wl.join("e", wl.ensure_code("d"), at=200)
    assert [r["user_id"] for r in wl.ordered()][:2] == ["d", "a"]
    assert wl.place_of("d") == 1


def test_grant_changes_status_and_queues_a_flag(acc, wl):
    for uid in "abc":
        acc.sign_up(uid, f"{uid}@fam.test", PASSWORD)
        wl.join(uid)
    assert wl.grant_top(2) == ["a", "b"]
    assert acc.account("a")["status"] == "active"
    assert wl.status_of("c") == "waitlisted"
    assert wl.grant(["a", "c", "nobody"]) == ["c"]      # granting twice is harmless
    assert [i["action"] for i in wl.due()].count("flag") == 3


def test_cutoff_and_unlocks(wl):
    assert wl.cutoff() == 0
    assert wl.set_cutoff(500) == 500 and wl.cutoff() == 500
    with pytest.raises(waitlist_mod.WaitlistError):
        wl.set_cutoff(-1)
    assert waitlist_mod.parse_unlocks("5, 1,3,x,0,3") == [1, 3, 5]
    assert waitlist_mod.next_unlock(2, [1, 3, 5])["remaining"] == 1
    assert waitlist_mod.next_unlock(9, [1, 3, 5])["remaining"] is None


# --- Viral Loops ------------------------------------------------------------

def _vendor(handler):
    return vl_mod.ViralLoops("secret", "camp-1", transport=httpx.MockTransport(handler))


def test_outbox_delivers_and_keeps_the_vendor_code(acc, wl):
    seen = []

    def handler(request):
        seen.append((request.url.path, request.headers.get("apiToken"),
                     json.loads(request.content)))
        return httpx.Response(200, json={"referralCode": "VL123"})

    acc.sign_up("a", "a@fam.test", PASSWORD)
    wl.join("a")
    wl.enqueue("a", "register")
    result = asyncio.run(vl_mod.drain(wl, _vendor(handler)))
    assert result == {"sent": 1, "failed": 0, "refused": 0, "skipped": 0}
    path, token, body = seen[0]
    assert path.endswith("/campaign/participant") and token == "secret"
    assert body["user"]["email"] == "a@fam.test" and body["campaignId"] == "camp-1"
    assert wl.vendor_view("a")["vl_referral_code"] == "VL123"
    assert wl.due() == []


def test_a_vendor_outage_loses_nothing(acc, wl):
    """WAITLIST.md §3b: the signup still succeeds and the call is retried."""
    acc.sign_up("a", "a@fam.test", PASSWORD)
    wl.enqueue("a", "register")

    def down(request):
        return httpx.Response(503, text="down")

    result = asyncio.run(vl_mod.drain(wl, _vendor(down)))
    assert result["failed"] == 1
    assert wl.outbox_summary()["pending"] == 1
    assert wl.due() == []                        # backed off, not dropped
    assert wl.due(now=10**12)[0]["attempts"] == 1


def test_flag_sends_a_participants_list(acc, wl):
    """Viral Loops refused a bare `email` with "'participants' is required"."""
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={})

    acc.sign_up("a", "a@fam.test", PASSWORD)
    wl.enqueue("a", "flag")
    assert asyncio.run(vl_mod.drain(wl, _vendor(handler)))["sent"] == 1
    assert seen[0]["participants"] == [{"email": "a@fam.test"}]
    assert "email" not in seen[0]


def test_a_refusal_since_fixed_is_sent_again(acc, wl):
    acc.sign_up("a", "a@fam.test", PASSWORD)
    acc.sign_up("b", "b@fam.test", PASSWORD)
    old = wl.enqueue("a", "flag")
    other = wl.enqueue("b", "register")
    wl.mark_given_up(old, "/campaign/participant/flag answered 400: "
                          "(participants) 'participants' is required")
    wl.mark_given_up(other, "answered 400: something else")
    assert wl.outbox_summary()["refused"] == 2
    assert wl.reopen_refused("flag", "'participants' is required") == 1
    assert [i["id"] for i in wl.due()] == [old]
    assert wl.outbox_summary()["refused"] == 1
    assert wl.reopen_refused("flag", "'participants' is required") == 0


def test_unconfigured_sends_nothing(acc, wl):
    acc.sign_up("a", "a@fam.test", PASSWORD)
    wl.enqueue("a", "register")
    assert asyncio.run(vl_mod.drain(wl, vl_mod.ViralLoops())) == {
        "sent": 0, "failed": 0, "refused": 0, "skipped": 0}
    assert wl.outbox_summary()["pending"] == 1


# --- the app ------------------------------------------------------------------

@pytest.fixture
def world(tmp_path, monkeypatch):
    acc = accounts_mod.AccountStore(str(tmp_path / "accounts.db"))
    acc.new_account_status = "waitlisted"
    wl = waitlist_mod.Waitlist(acc)
    monkeypatch.setattr(appmod, "ACCOUNTS", acc)
    monkeypatch.setattr(appmod, "WAITLIST", wl)
    monkeypatch.setattr(appmod, "SOCIAL", social_mod.SocialStore(str(tmp_path / "social.db")))
    monkeypatch.setattr(appmod, "PREFS", prefs_mod.PreferenceStore(str(tmp_path / "prefs.db")))
    monkeypatch.setattr(appmod, "MESSAGES", messages_mod.MessageStore(str(tmp_path / "msgs.db")))
    monkeypatch.setattr(appmod, "VIRAL_LOOPS", vl_mod.ViralLoops())
    monkeypatch.setattr(appmod, "settings",
                        dataclasses.replace(appmod.settings, waitlist=True))
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "admin-secret")
    return appmod


def _join(email, code=""):
    c = TestClient(appmod.app)
    r = c.post("/api/waitlist/join", json={"email": email, "password": PASSWORD,
                                           "referral_code": code})
    assert r.status_code == 200, r.text
    return c, r.json()


def test_guests_and_waitlisted_are_kept_out_of_the_app(world):
    guest = TestClient(appmod.app)
    # Typing the address opens the app's front door, to sign in or sign up
    # (§217, restored in §231): a guest is never sent to the waitlist.
    for page_path in ("/", "/index.html", "/index.html/", "//"):
        r = guest.get(page_path, follow_redirects=False)
        assert r.status_code == 200, page_path
    r = guest.get("/?referralCode=abc", follow_redirects=False)
    assert r.headers["location"] == "/waitlist?referralCode=abc"
    r = guest.get("/api/friends")
    assert r.status_code == 403 and r.headers["X-FAM-Waitlist"] == "/waitlist"
    assert guest.get("/api/v1/friends").status_code == 403
    assert guest.get("/api/health").status_code == 200
    assert guest.get("/waitlist").status_code == 200
    page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert 'd.status === "waitlisted"' in page
    assert 'if(go === "/waitlist/me") location.replace(go);' in page
    assert 'location.replace("/waitlist");' not in page
    # Sign Up is the one door from the app to the waitlist.
    assert 'location.href = "/waitlist";' in page

    member, body = _join("w@fam.test")
    assert body["status"] == "waitlisted" and body["redirect"] == "/waitlist/me"
    r = member.get("/", follow_redirects=False)
    assert r.headers["location"] == "/waitlist/me"
    assert member.get("/api/audio?q=x").status_code == 403
    assert member.get("/m/abc", follow_redirects=False).status_code == 302
    assert member.get("/api/waitlist/me").status_code == 200


def test_granted_accounts_get_in(world):
    member, _ = _join("w@fam.test")
    user = member.get("/api/auth/me").json()["user_id"]
    appmod.WAITLIST.grant([user])
    assert member.get("/", follow_redirects=False).status_code == 200
    assert member.get("/api/friends").status_code == 200


def test_the_apps_sign_in_sends_each_account_where_it_belongs(world):
    """§231: a guest on the front door signs in with the app's own form. An
    account still in line is sent to its status page; one let in is in."""
    waiting, _ = _join("wait@fam.test")
    let_in, _ = _join("in@fam.test")
    appmod.WAITLIST.grant([let_in.get("/api/auth/me").json()["user_id"]])

    door = TestClient(appmod.app)
    assert door.get("/", follow_redirects=False).status_code == 200
    r = door.post("/api/auth/login", json={"email": "wait@fam.test",
                                           "password": PASSWORD})
    assert r.status_code == 200, r.text
    assert door.get("/api/auth/me").json()["status"] == "waitlisted"
    assert door.get("/", follow_redirects=False).headers["location"] == "/waitlist/me"

    door = TestClient(appmod.app)
    r = door.post("/api/auth/login", json={"email": "in@fam.test",
                                           "password": PASSWORD})
    assert r.status_code == 200, r.text
    assert door.get("/", follow_redirects=False).status_code == 200
    assert door.get("/api/friends").status_code == 200


def test_an_admin_with_an_account_can_join_to_see_the_page(world, monkeypatch):
    """§233: an admin who already has an account types it into the join form
    and lands on the status page's admin preview, signed in, never in line.
    A wrong password, or anybody else's existing email, is still refused."""
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", "boss@fam.test")
    _join("boss@fam.test")
    _join("member@fam.test")

    again, body = _join("Boss@fam.test")
    assert body["admin"] is True and body["status"] == "active"
    assert body["redirect"] == "/waitlist/me"
    me = again.get("/api/waitlist/me").json()
    assert me["admin"] is True and me["preview"] is not None
    user = again.get("/api/auth/me").json()["user_id"]
    assert user not in [r["user_id"] for r in appmod.WAITLIST.ordered()]

    c = TestClient(appmod.app)
    for email, password in (("boss@fam.test", "wrong-password"),
                            ("member@fam.test", PASSWORD)):
        r = c.post("/api/waitlist/join", json={"email": email, "password": password,
                                               "accept_terms": True})
        assert r.status_code == 400 and "already registered" in r.text, r.text


def test_admin_credentials_pass_the_gate_and_nobody_else_does(world):
    guest = TestClient(appmod.app)
    assert guest.get("/api/admin/waitlist").status_code == 404
    assert guest.post("/api/admin/waitlist/grant", json={"top": 1}).status_code == 404
    member, _ = _join("w@fam.test")
    assert member.get("/api/admin/waitlist").status_code == 404
    admin = {"X-Admin-Token": "admin-secret"}
    body = guest.get("/api/admin/waitlist", headers=admin).json()
    assert body["summary"]["waitlisted"] == 1 and body["rows"][0]["email"] == "w@fam.test"
    assert guest.get("/api/usage", headers=admin).status_code != 403


def test_an_admin_joining_the_waitlist_is_never_put_in_line(world, monkeypatch):
    """An admin email (FAM_ADMIN_ACCOUNTS) signing up at /waitlist gets an
    active account, passes the gate, and is sent to the status page to see
    it - with a preview of a new member's numbers - never into the line."""
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", "boss@fam.test")
    _join("m1@fam.test")
    admin, body = _join("Boss@fam.test")
    assert body["status"] == "active" and body["admin"] is True
    assert body["redirect"] == "/waitlist/me"
    user = admin.get("/api/auth/me").json()["user_id"]
    assert appmod.WAITLIST.place_of(user) is None
    assert user not in [r["user_id"] for r in appmod.WAITLIST.ordered()]
    # Viral Loops was never told about them, so nothing is queued.
    assert not appmod.WAITLIST.has_action(user, "register")
    assert not appmod.WAITLIST.has_action(user, "flag")
    assert admin.get("/", follow_redirects=False).status_code == 200
    me = admin.get("/api/waitlist/me").json()
    assert me["admin"] is True and me["place"] is None
    assert me["preview"]["place"] == 2  # where the next joiner would land
    # Nobody else is an admin, and nobody else gets a preview.
    other, body = _join("m2@fam.test")
    assert body["status"] == "waitlisted" and body["admin"] is False
    assert other.get("/api/waitlist/me").json()["preview"] is None


def test_an_admin_already_in_line_is_let_out_at_sign_in(world, monkeypatch):
    member, _ = _join("late@fam.test")
    user = member.get("/api/auth/me").json()["user_id"]
    assert appmod.WAITLIST.place_of(user) == 1
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", "late@fam.test")
    c = TestClient(appmod.app)
    r = c.post("/api/auth/login", json={"email": "late@fam.test", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert r.json()["admin"] is True
    assert appmod.WAITLIST.place_of(user) is None
    # They were registered with Viral Loops, so they are flagged off it.
    assert appmod.WAITLIST.has_action(user, "flag")
    assert c.get("/api/friends").status_code == 200


def test_the_status_page_draws_an_admins_preview():
    page = (ROOT / "static" / "waitlist.html").read_text()
    assert 'id="adminNote"' in page and "d.preview" in page
    assert 'd.admin || d.status !== "active" ? "/waitlist/me" : "/"' in page


def test_admin_lets_in_the_ticked_people_and_the_first_n_in_line(world):
    """The admin page's two ways in: the people ticked, and any number from
    the front of the line, in order."""
    admin = TestClient(appmod.app, headers={"X-Admin-Token": "admin-secret"})
    ids = []
    for i in range(5):
        member, _ = _join(f"p{i}@fam.test")
        ids.append(member.get("/api/auth/me").json()["user_id"])
    line = [r["user_id"] for r in admin.get("/api/admin/waitlist").json()["rows"]]
    assert sorted(line) == sorted(ids)
    picked = [line[1], line[3]]
    got = admin.post("/api/admin/waitlist/grant", json={"user_ids": picked}).json()
    assert got["granted"] == 2 and sorted(got["user_ids"]) == sorted(picked)
    left = [r["user_id"] for r in admin.get("/api/admin/waitlist").json()["rows"]]
    assert left == [line[0], line[2], line[4]]
    got = admin.post("/api/admin/waitlist/grant", json={"top": 2}).json()
    assert got["user_ids"] == [line[0], line[2]]
    assert [r["user_id"] for r in admin.get("/api/admin/waitlist").json()["rows"]] == [line[4]]
    # More than are waiting lets in whoever is left.
    assert admin.post("/api/admin/waitlist/grant", json={"top": 50}).json()["granted"] == 1
    assert admin.get("/api/admin/waitlist").json()["rows"] == []


def test_admin_page_has_the_checkboxes_and_the_first_n_control():
    page = (appmod.PROJECT_ROOT / "admin_ui" / "waitlist.html").read_text()
    for needle in ('id="pickAll"', 'data-pick=', 'id="grantPicked"', 'id="topN"',
                   '{ user_ids: ids }', '{ top: top }'):
        assert needle in page, needle


def test_referral_signup_makes_friends_and_counts_the_invite(world):
    inviter, _ = _join("i@fam.test")
    code = inviter.get("/api/waitlist/me").json()["referral_code"]
    friend, _ = _join("f@fam.test", code)
    me = inviter.get("/api/waitlist/me").json()
    assert me["invites"] == 1 and len(me["fam"]) == 1
    assert me["fam"][0]["invited"] and me["fam"][0]["waitlisted"]
    assert me["referral_link"].endswith("/waitlist?referralCode=" + code)
    friend_id = friend.get("/api/auth/me").json()["user_id"]
    inviter_id = inviter.get("/api/auth/me").json()["user_id"]
    assert appmod.SOCIAL.is_following(friend_id, inviter_id)
    assert appmod.SOCIAL.is_following(inviter_id, friend_id)
    # The inviter is now first in line.
    assert me["place"] == 1
    # Registration with the vendor is queued for both, not sent.
    assert appmod.WAITLIST.outbox_summary()["pending"] == 2


def test_status_page_numbers(world):
    members = [_join(f"m{i}@fam.test")[0] for i in range(4)]
    appmod.WAITLIST.set_cutoff(2)
    last = members[3].get("/api/waitlist/me").json()
    assert last["place"] == 4 and last["places_until"] == 2
    assert not last["in_next_batch"]
    first = members[0].get("/api/waitlist/me").json()
    assert first["in_next_batch"] and first["places_until"] == 0
    assert first["unlock"]["remaining"] == 1


def test_profile_setup_cannot_write_status_or_place(world):
    member, _ = _join("w@fam.test")
    user = member.get("/api/auth/me").json()["user_id"]
    # Extra fields are ignored by the schema; nothing reaches status.
    member.post("/api/me", json={"name": "Ian", "handle": "ian", "status": "active",
                                 "is_admin": True, "referred_by": "x"})
    member.post("/api/preferences", json={"interests": ["sports"], "status": "active"})
    assert appmod.WAITLIST.status_of(user) == "waitlisted"
    assert member.get("/", follow_redirects=False).status_code == 302
    profile = member.get("/api/waitlist/me").json()["profile"]
    assert profile["name"] == "Ian" and profile["handle"] == "ian"


def test_waitlisted_are_out_of_discovery_but_friends_still_see_them(world, monkeypatch):
    waiting, _ = _join("w@fam.test")
    waiting.post("/api/me", json={"name": "Wes", "handle": "wes"})
    wes = waiting.get("/api/auth/me").json()["user_id"]

    def member(email):
        """An active account, made while the waitlist runs: joined, then let in."""
        c, _ = _join(email)
        appmod.WAITLIST.grant([c.get("/api/auth/me").json()["user_id"]])
        return c

    stranger = member("s@fam.test")
    assert stranger.get("/api/people?q=wes").json()["people"] == []
    assert stranger.get("/api/person?handle=wes").status_code == 404
    assert stranger.post("/api/friends/follow", json={"handle": "wes"}).status_code == 404
    assert stranger.post("/api/friends/follow", json={"user_id": wes}).status_code == 404
    # A friend made before still sees them, labelled.
    friend = member("f@fam.test")
    fid = friend.get("/api/auth/me").json()["user_id"]
    appmod.SOCIAL.follow(fid, wes); appmod.SOCIAL.follow(wes, fid)
    assert friend.get("/api/person?handle=wes").status_code == 200
    listed = friend.get("/api/friends").json()["friends"]
    assert listed[0]["waitlisted"] is True
    # And nobody messages across the waitlist.
    r = friend.post("/api/messages", json={"to": wes, "text": "hi"})
    assert r.status_code == 403


def test_account_deletion_clears_waitlist_rows(world):
    member, _ = _join("w@fam.test")
    user = member.get("/api/auth/me").json()["user_id"]
    removed = appmod.erase_listener(user)
    assert removed["waitlist"] == 1
    assert appmod.WAITLIST.outbox_summary()["pending"] == 0


def test_anyone_can_listen_to_a_shared_episode(world, monkeypatch):
    """The owner (01/10): a shared episode plays for anybody, waitlist or not
    - and the share page is not a way into the rest of the app."""
    import sharing
    store = sharing.ShareStore(os.path.join(os.path.dirname(
        appmod.WAITLIST.accounts.path), "shares.db"))
    monkeypatch.setattr(appmod, "SHARES", store)
    share = store.create("sharer", "why is the sky blue", 0)
    guest = TestClient(appmod.app)
    # The page itself is not redirected.
    assert guest.get(f"/s/{share['id']}", follow_redirects=False).status_code != 302
    assert guest.post(f"/api/share/{share['id']}/open").status_code != 403
    # Its audio passes the gate (whatever the endpoint then does with it)...
    ok = guest.get("/api/audio", params={"q": "why is the sky blue", "minutes": 1,
                                         "fmt": "pcm", "surface": "share"})
    assert "X-FAM-Waitlist" not in ok.headers
    # ...and nothing else does: another question, another length, another
    # surface, or an attachment riding along.
    for params in ({"q": "something else", "minutes": 1, "surface": "share"},
                   {"q": "why is the sky blue", "minutes": 5, "surface": "share"},
                   {"q": "why is the sky blue", "minutes": 1, "surface": "search"},
                   {"q": "why is the sky blue", "minutes": 1, "surface": "share",
                    "attach": "a1"}):
        r = guest.get("/api/audio", params=params)
        assert r.status_code == 403 and r.headers["X-FAM-Waitlist"] == "/waitlist"


def test_launch_lifts_the_waitlist_rules(world, monkeypatch):
    """WAITLIST=0 is launch: anyone still marked waitlisted is findable and
    can message, rather than hidden for good by a row nobody updated."""
    waiting, _ = _join("w@fam.test")
    waiting.post("/api/me", json={"name": "Wes", "handle": "wes"})
    wes = waiting.get("/api/auth/me").json()["user_id"]
    monkeypatch.setattr(appmod, "settings",
                        dataclasses.replace(appmod.settings, waitlist=False))
    appmod.ACCOUNTS.new_account_status = "active"
    other = TestClient(appmod.app)
    other.post("/api/auth/signup", json={"email": "o@fam.test", "password": PASSWORD})
    assert [p["handle"] for p in other.get("/api/people?q=wes").json()["people"]] == ["wes"]
    assert other.get("/api/person?handle=wes").status_code == 200
    assert other.post("/api/messages", json={"to": wes, "text": "hi"}).status_code == 200
    # And the app itself is open to them.
    assert waiting.get("/", follow_redirects=False).status_code == 200


def test_waitlisted_can_delete_their_account(world):
    member, _ = _join("w@fam.test")
    user = member.get("/api/auth/me").json()["user_id"]
    assert member.get("/api/account").status_code == 200
    assert member.delete("/api/account").status_code == 200
    assert appmod.ACCOUNTS.account(user) is None


def test_the_shell_is_closed_under_every_spelling(world):
    # A guest is let onto the front door (§217); a waitlisted account is not,
    # under any spelling of it.
    member, _ = _join("s@fam.test")
    for path in ("/", "/index.html", "/index.html/", "//"):
        r = member.get(path, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/waitlist/me", path


def test_an_invite_code_credits_a_limited_number_an_hour(acc, wl):
    acc.sign_up("inviter", "i@fam.test", PASSWORD)
    code = wl.join("inviter")["code"]
    results = []
    for n in range(4):
        acc.sign_up(f"f{n}", f"f{n}@fam.test", PASSWORD)
        results.append(wl.join(f"f{n}", code, referrals_per_hour=3))
    assert [r["referrer"] for r in results] == ["inviter"] * 3 + [""]
    assert results[-1]["capped"] is True
    assert len(wl.invites_of("inviter")) == 3


def test_counted_place_matches_the_line(acc, wl):
    for n, uid in enumerate("abcdef"):
        acc.sign_up(uid, f"{uid}@fam.test", PASSWORD, at=100 + n)
        wl.join(uid, at=100 + n)
    acc.sign_up("g", "g@fam.test", PASSWORD, at=200)
    wl.join("g", wl.ensure_code("e"), at=200)
    acc.sign_up("h", "h@fam.test", PASSWORD, at=201)
    wl.join("h", wl.ensure_code("c"), at=201)
    for row in wl.ordered():
        assert wl.place_of(row["user_id"]) == row["place"]
    wl.grant(["a"])
    assert wl.place_of("a") is None


def test_a_refusal_is_not_retried_but_stays_visible(acc, wl):
    acc.sign_up("a", "a@fam.test", PASSWORD)
    wl.enqueue("a", "register")

    def refuse(request):
        return httpx.Response(422, text="email rejected")

    result = asyncio.run(vl_mod.drain(wl, _vendor(refuse)))
    assert result["refused"] == 1 and result["failed"] == 0
    summary = wl.outbox_summary()
    assert summary["pending"] == 0 and summary["refused"] == 1
    assert "email rejected" in summary["last_refused"]


def test_concurrent_drains_send_each_call_once(acc, wl):
    calls = []

    async def handler(request):
        calls.append(json.loads(request.content)["user"]["email"])
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={"referralCode": "X"})

    acc.sign_up("a", "a@fam.test", PASSWORD)
    wl.enqueue("a", "register")
    vendor = _vendor(handler)

    async def both():
        return await asyncio.gather(vl_mod.drain(wl, vendor), vl_mod.drain(wl, vendor))

    asyncio.run(both())
    assert calls == ["a@fam.test"]


def test_a_second_account_can_be_made_from_the_same_browser(world):
    """§190: an account is its credentials, never the device. The owner joined
    once, then tried two fresh addresses from the same computer and was told
    "this listener already has an account" both times."""
    browser, first = _join("first@fam.test")
    r = browser.post("/api/waitlist/join", json={"email": "second@fam.test",
                                                 "password": PASSWORD})
    assert r.status_code == 200, r.text
    second = r.json()
    assert second["user_id"] != first["user_id"]
    assert second["email"] == "second@fam.test"
    # The browser is now the second account; the first still logs in.
    assert browser.get("/api/auth/me").json()["user_id"] == second["user_id"]
    other = TestClient(appmod.app)
    r = other.post("/api/auth/login", json={"email": "first@fam.test",
                                            "password": PASSWORD})
    assert r.status_code == 200 and r.json()["user_id"] == first["user_id"]
    # The app's own sign-up does the same.
    r = browser.post("/api/auth/signup", json={"email": "third@fam.test",
                                               "password": PASSWORD})
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] not in (first["user_id"], second["user_id"])


def test_a_refused_second_signup_logs_nobody_out(world):
    browser, first = _join("first@fam.test")
    r = browser.post("/api/waitlist/join", json={"email": "first@fam.test",
                                                 "password": PASSWORD})
    assert r.status_code == 400 and "already registered" in r.json()["error"]
    assert browser.get("/api/auth/me").json()["user_id"] == first["user_id"]


def test_the_landing_page_plays_the_sign_up_samples_and_nothing_else(world, monkeypatch):
    """§190: the waitlist page rotates the app's three sign-up samples, so
    the gate lets their replays through - exactly those, as replays."""
    sample = {"query": "Why the Eagles lost", "minutes": appmod.BROWSE_MINUTES}
    monkeypatch.setattr(appmod, "_welcome_episodes", lambda: [sample])
    kept = {"yes": True}
    monkeypatch.setattr(appmod, "_audio_is_kept", lambda q, m: kept["yes"])
    guest = TestClient(appmod.app)
    assert guest.get("/api/welcome").status_code == 200
    base = {"q": sample["query"], "minutes": str(appmod.BROWSE_MINUTES),
            "fmt": "pcm", "cached_only": "true"}

    def gated(**extra):
        r = guest.get("/api/audio", params={**base, **extra})
        return r.status_code == 403 and "X-FAM-Waitlist" in r.headers

    assert not gated()
    assert gated(q="Something else entirely")
    assert gated(cached_only="")
    assert gated(minutes="5")
    assert gated(context="go deeper")
    assert gated(voice="ian")
    # Audio evicted since the samples were ranked: refused, never synthesised.
    kept["yes"] = False
    assert gated()
    assert "fam-audio.js" in guest.get("/waitlist").text


# --- 10.2 packet: joining always waitlists; the profile's details ------------

def test_joining_waitlists_even_with_the_gate_off(world, monkeypatch):
    """The owner joined on a server whose switch was not set: the join made
    an active account, skipped the line and dropped them in the app. A join
    is a join, gate or no gate."""
    monkeypatch.setattr(appmod, "settings",
                        dataclasses.replace(appmod.settings, waitlist=False))
    appmod.ACCOUNTS.new_account_status = "active"
    member, body = _join("off@fam.test")
    assert body["status"] == "waitlisted" and body["redirect"] == "/waitlist/me"
    me = member.get("/api/waitlist/me").json()
    assert me["place"] == 1 and me["total"] == 1
    # The app's own sign-up is unchanged: the gate decides that one.
    other = TestClient(appmod.app)
    r = other.post("/api/auth/signup", json={"email": "o@fam.test", "password": PASSWORD})
    assert r.json()["status"] == "active"


def test_the_profile_keeps_birth_date_phone_and_location(world):
    member, _ = _join("p@fam.test")
    assert member.post("/api/account", json={"birth_date": "1994-05-17",
                                             "phone": "+14155550142"}).status_code == 200
    member.post("/api/preferences", json={"country": "United States", "region": "Ohio",
                                          "city": "Cincinnati", "interests": ["sports"]})
    details = member.get("/api/waitlist/me").json()["details"]
    assert details["birth_date"] == "1994-05-17"
    assert details["phone"] == "+14155550142"
    assert details["location"]["city"] == "Cincinnati"
    assert details["location"]["region"] == "Ohio"
    assert details["location"]["country"] == "United States"
    # A day that has not happened yet is refused; "" clears it.
    assert member.post("/api/account", json={"birth_date": "2999-01-01"}).status_code == 400
    assert member.post("/api/account", json={"birth_date": "not a date"}).status_code == 400
    member.post("/api/account", json={"birth_date": ""})
    assert member.get("/api/waitlist/me").json()["details"]["birth_date"] == ""


def test_the_waitlist_page_asks_for_the_password_twice_and_says_the_place():
    page = (ROOT / "static" / "waitlist.html").read_text(encoding="utf-8")
    assert 'id="password2"' in page and "don’t match" in page
    assert 'id="placeBig"' in page and '"#" + fmt(d.place)' in page
    assert 'id="shareLink"' in page
    for field in ("pBirth", "pCountry", "pRegion", "pCity", "pPhone"):
        assert f'id="{field}"' in page
    # The profile's details reach the account and the preferences.
    assert 'api("/api/account", account)' in page
    # A refused number or date stops the save before anything is written.
    save = page.split("window.saveProfile = function", 1)[1]
    assert save.index('api("/api/account"') < save.index('api("/api/me"')


def test_joining_goes_straight_to_the_profile_with_a_skip():
    """§215: the profile is the next screen after the email and password,
    never behind an "Edit your profile" button, and it can be skipped."""
    page = (ROOT / "static" / "waitlist.html").read_text(encoding="utf-8")
    assert 'id="editProfileBtn"' not in page and "to personalize your experience" not in page
    join = page.split('api("/api/waitlist/join"', 1)[1].split(".catch(", 1)[0]
    assert '"?setup=1"' in join
    # The setup bar holds the skip, at the top right, and only in setup.
    bar = page.split('<div class="top setup-only">', 1)[1].split("</div>\n  <div", 1)[0]
    assert bar.index('class="wordmark"') < bar.index('id="skipSetup"')
    assert ">Skip for now<" in bar and 'onclick="finishSetup()"' in bar
    # Every field the app's sign-up asks for is on it.
    profile = page.split('id="profile"', 1)[1].split("</section>", 1)[0]
    for field in ("pName", "pHandle", "photoFile", "pBirth", "pCity", "pRegion", "pCountry", "pPhone", "chips"):
        assert f'id="{field}"' in profile
    # Saving in setup leaves setup, like skipping.
    save = page.split("window.saveProfile = function", 1)[1].split("\n  };", 1)[0]
    assert "if(setupMode){ window.finishSetup();" in save
    assert 'history.replaceState(null, "", "/waitlist/me")' in page


def test_the_status_page_has_a_settings_gear_and_learn_more():
    page = (ROOT / "static" / "waitlist.html").read_text(encoding="utf-8")
    status = page.split('id="status"', 1)[1].split('id="aboutView"', 1)[0]
    # A gear, never a dot or a monogram.
    gear = status.split('id="meBtn"', 1)[1].split("</button>", 1)[0]
    assert 'aria-label="Settings"' in gear and "<svg" in gear and "·" not in gear
    assert '$("meBtn").textContent' not in page
    # "Learn more about FAM" at the top, in Go Deeper's yellow.
    assert status.index('id="learnMore"') < status.index('id="placeCard"')
    assert "Learn more about FAM" in status
    learn = page.split(".learn-btn{", 1)[1].split("}", 1)[0]
    app = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    deeper = re.search(r"--deeper:(#[0-9A-Fa-f]{6})", app).group(1)
    assert f"background:{deeper}" in learn
    # It opens the landing's own explainer, not a copy of it.
    assert page.count('id="about"') == 1
    opener = page.split("window.openAbout = function(){", 1)[1].split("\n  };", 1)[0]
    assert 'view.appendChild($("about"))' in opener and "showAbout(true)" in opener


def test_the_apps_sign_up_goes_to_the_waitlist():
    page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    body = page.split("function openAuth(mode){", 1)[1].split("authMode = mode;", 1)[0]
    assert 'mode === "signup" && signupGoesToWaitlist()' in body
    assert 'location.href = "/waitlist"' in body


def test_the_landing_page_says_what_fam_is_under_the_sign_up():
    page = (ROOT / "static" / "waitlist.html").read_text(encoding="utf-8")
    landing = page.split('id="landing"', 1)[1].split('id="status"', 1)[0]
    # Under the form, in the landing's markup; the status page's "Learn more
    # about FAM" moves this same section into its own view (§215).
    assert landing.index('id="joinForm"') < landing.index('id="about"')
    assert "Ian Solomon &amp; TJ Weingart" in landing
    # The founders' photo is optional: a missing file draws their initials.
    assert 'src="/founders.jpg"' in landing and "classList.add('empty')" in landing
    # The photo ships, small enough to load fast and with nothing in it but
    # the picture (no location from the phone that took it).
    from PIL import Image
    photo = ROOT / "static" / "founders.jpg"
    assert photo.stat().st_size < 400_000
    with Image.open(photo) as im:
        assert im.width <= 1600 and not im.getexif()
    # The reveal moves sections; it never hides them while they wait.
    reveal = page.split(".can-reveal .reveal{", 1)[1].split("}", 1)[0]
    assert "opacity" not in reveal
    assert 'classList.add("can-reveal")' in page


def test_the_what_is_fam_pictures_are_real_screens_that_ship():
    page = (ROOT / "static" / "waitlist.html").read_text(encoding="utf-8")
    about = page.split('id="about"', 1)[1].split("</footer>", 1)[0]
    pictures = re.findall(r'src="/landing/([\w-]+\.jpg)"', about)
    assert pictures == ["dailyfam.jpg", "search.jpg", "myfam-mix.jpg"]
    for name in pictures:
        assert (ROOT / "static" / "landing" / name).stat().st_size > 10_000, name
    # Nothing ships that the page no longer shows.
    assert sorted(p.name for p in (ROOT / "static" / "landing").glob("*.jpg")) == sorted(pictures)
    # Retaken by one script, which writes exactly the files the page names,
    # some in light and some in dark (§248).
    tool = (ROOT / "tools" / "landing_shots.py").read_text(encoding="utf-8")
    shots = dict((n, t) for n, t in re.findall(r'\("([\w-]+)", .*?, "(light|dark)"\)', tool, re.S))
    assert set(shots) == {n[:-4] for n in pictures}
    assert set(shots.values()) == {"light", "dark"}
    assert 'setTheme(t)", theme)' in tool
    # Only painted tiles, made-up friends, a cover on the Morning mix.
    assert "if(!c.querySelector('.seed-img')) c.remove();" in tool
    assert '"Beth Solomon": "Maya Brooks"' in tool
    assert "await page.evaluate(DESIGNED_TILES_ONLY)" in tool
    assert "await page.evaluate(RENAME, names)" in tool
    assert (ROOT / "tools" / "landing" / "morning-cover.jpg").stat().st_size > 10_000
    # The painted cards, one set per rail they were cut from.
    tiles = {p.stem.split("-")[0] for p in (ROOT / "tools" / "landing" / "tiles").glob("*.jpg")}
    assert tiles == {"foryou", "trending", "friends"}
    assert "await page.evaluate(PAINT_TILES, cards)" in tool
    # Every picture says what it shows.
    assert all('alt=""' not in tag for tag in re.findall(r"<img[^>]*>", about))


def test_the_landing_page_tells_why_fam_exists():
    page = (ROOT / "static" / "waitlist.html").read_text(encoding="utf-8")
    landing = page.split('id="landing"', 1)[1].split('id="status"', 1)[0]
    # The sign-up screen names it and the intro says it; the meta description
    # spells it out.
    assert '<p class="tagline label">The social information network</p>' in landing
    # Right under the wordmark, above the headline.
    assert landing.index('class="wordmark"') < landing.index('class="tagline') < landing.index('class="headline"')
    # Drawn as the cure's heading is (§248), not in capitals.
    assert '<h2 class="cure-h">Social <span>information.</span></h2>' in landing
    assert "SOCIAL INFORMATION" not in landing
    assert "social information, not social media" in page.split("</head>", 1)[0]
    foot = landing.split('class="ab-foot"', 1)[1]
    assert foot.index('class="wordmark"') < foot.index("The social information network")
    # The copyright is the last line on the page.
    assert foot.index("The social information network") < foot.index("&copy; 2026 APALI. All rights reserved.") < foot.index("</footer>")
    # The FAM Doctrine (PROBLEMS.md §246, docs/doctrine/), in its order:
    # the problem, its three parts, what they add up to, the cure, the chat.
    order = ["The problem with how information reaches us",
             "What you know is shaped by what you’re shown.",
             "The audio problem you don’t realize you have.",
             "The interests of the people around you are invisible.",
             "Information isolation.", "Social information connects what they know.",
             "Ian Solomon &amp; TJ Weingart", "actually being part of the conversation"]
    at = [landing.index(text) for text in order]
    assert at == sorted(at), "the story is told out of order"
    # The intro is the doctrine's last sentence.
    intro = landing.split('class="ab-wrap ab-intro', 1)[1].split("</section>", 1)[0]
    assert "FAM puts everything you care about in one place" in intro
    assert "so you are never left out of the conversation." in intro
    # The heading and the one problem stay put; the three take turns under it.
    problem = landing.split('class="ab-wrap ab-problem', 1)[1].split("</section>", 1)[0]
    assert (problem.index("Three problems compound into one: <b>information isolation.</b>")
            < problem.index("data-carousel"))
    slides = re.findall(r'<div class="car-slide prob ([\w-]+)"', problem)
    assert slides == ["prob-shown", "prob-audio", "prob-friends"]
    assert [problem.index(f">0{n}<") for n in (1, 2, 3)] == sorted(problem.index(f">0{n}<") for n in (1, 2, 3))
    # Four systems that decide for you; six moments your ears are free.
    assert problem.count('<li><span class="sys-ic">') == 4
    assert problem.split('class="moments"', 1)[1].split("</ul>", 1)[0].count("<li>") == 6
    assert "They share the same lunch table, but\n              not the same information." in problem
    # What they add up to, then the cure, each on its own.
    iso = landing.split('class="ab-wrap ab-isolation', 1)[1].split("</section>", 1)[0]
    assert iso.index("Information isolation.") < iso.index("It doesn’t connect what they know.")
    cure = landing.split('class="ab-wrap ab-cure', 1)[1].split("</section>", 1)[0]
    assert "The cure for information isolation." in cure
    assert cure.index("A new category") < cure.index("Social <span>information.</span>")
    # The old telling is gone: the doctrine replaced it, it did not join it.
    for gone in ("full-time job", 'class="condense"', 'class="pass-card"', "ab-why", "ab-origin",
                 "Venn", "That’s why we built FAM."):
        assert gone not in page, gone


def test_three_sections_are_off_the_page_for_a_video():
    """§248 (the 10.10 packet): "Stay in the loop", "How to get the most out
    of FAM" and "Better together" are becoming a video, so the page no
    longer carries them - nor the styles that only they used. (The comment
    saying so is allowed to name them.)"""
    page = (ROOT / "static" / "waitlist.html").read_text(encoding="utf-8")
    page = re.sub(r"<!--.*?-->", "", page, flags=re.S)
    for gone in ("Stay in the loop", "Don’t be left out of the conversation",
                 "How to get the most out of FAM", "Search. Scroll. Mix.",
                 "Better together", 'class="ab-wrap ab-morning', 'class="ab-wrap ab-how',
                 'class="ab-wrap ab-friends', ".convo{", ".feat{", ".fr-card{", ".shots{"):
        assert gone not in page, gone


def test_the_carousels_turn_only_by_hand():
    page = (ROOT / "static" / "waitlist.html").read_text(encoding="utf-8")
    boxes = page.split(" data-carousel role=")[1:]
    assert len(boxes) == 1  # the problem (how to use FAM went in §248)
    for box in boxes:
        box = box.split("</section>", 1)[0]
        # Without script every slide shows: nothing is hidden in the markup.
        slides = box.count('aria-roledescription="slide"')
        assert slides >= 3
        tags = re.findall(r'<[^>]*aria-roledescription="slide"[^>]*>', box)
        assert len(tags) == slides
        assert not any("data-on" in t or "inert" in t or "aria-hidden" in t for t in tags)
        # Arrows on the left and right, a dot a slide.
        assert box.index('class="car-arrow car-prev"') < box.index('class="car-arrow car-next"')
        assert box.split('class="car-dots"', 1)[1].split("</div>", 1)[0].count("<button") == slides
    # Running, it stacks the slides and shows one; the rest are hidden from
    # sight, screen readers and the keyboard.
    assert ".car:not(.ready) .car-arrow" in page and ".car.ready .car-slide{ grid-area:1/1;" in page
    assert 'el.setAttribute("aria-hidden", "true"); el.inert = true;' in page
    assert 'box.classList.add("ready");' in page
    # Never on a timer: a slide must not move while someone is reading it.
    turn = page.split("function turnCarousel(box){", 1)[1].split("\n  }\n", 1)[0]
    assert "setInterval" not in turn and "setTimeout" not in turn
    assert "CAROUSEL_SECONDS" not in page
    # The arrows are white with black chevrons, in their own row above the
    # slide on every width, so they never cover a picture.
    assert "background:#FFFFFF; color:#000000;" in page
    assert 'grid-template-areas:"p d n" "s s s";' in page
    assert '"p s n"' not in page
    assert 'querySelectorAll("[data-carousel]"), turnCarousel' in page.split("function showAbout(member){", 1)[1]


def test_the_10_7_packet_on_the_waitlist_page():
    """Location and interests say they are optional; "View all topics" opens
    the app's own long list (the catalogue, searchable, anything typed added
    as it is) and saves it as `topics`, as the app does; and the foot of the
    landing page signs a member in."""
    page = (ROOT / "static" / "waitlist.html").read_text(encoding="utf-8")
    assert '<span>Location <em class="opt">(Optional)</em></span>' in page
    assert '<span>Your interests <em class="opt">(Optional)</em></span>' in page
    assert ">View all topics</button>" in page
    assert 'placeholder="Search topics, or type your own"' in page
    assert "prefs.catalogue" in page and "prefs.topics_chosen" in page
    # Sent only once touched: every saved topic is logged as a pick, so an
    # untouched list resent on a name fix would count each one again.
    assert "if(topicsDirty) prefs.topics = chosenTopics.map(function(t){ return t.id; });" in page
    assert "topicsDirty = true;" in page.split("function toggleTopic", 1)[1].split("function ", 1)[0]
    assert "Add <b>' + esc(raw)" in page
    landing = page.split('id="landing"', 1)[1].split('id="status"', 1)[0]
    cta = landing.split('class="ab-wrap ab-cta', 1)[1].split("</section>", 1)[0]
    assert 'id="bottomLogin">Already off the waitlist? Sign in here</button>' in cta
    assert '$("bottomLogin").addEventListener("click"' in page


def test_the_waitlist_profile_saves_topics_off_the_long_list(world):
    member, _ = _join("t@fam.test")
    prefs = member.get("/api/preferences").json()
    assert len(prefs["catalogue"]) > 20
    first = prefs["catalogue"][0]["id"]
    assert member.post("/api/preferences", json={
        "interests": [], "topics": [first, "Formula E"],
        "country": "USA", "region": "CA", "city": "San Francisco"}).status_code == 200
    chosen = member.get("/api/preferences").json()["topics_chosen"]
    assert [t["id"] for t in chosen] == [first, "Formula E"]
    assert chosen[1]["typed"] is True
