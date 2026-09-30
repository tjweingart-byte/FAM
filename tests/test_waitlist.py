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


# --- the store --------------------------------------------------------------

@pytest.fixture
def acc(tmp_path):
    store = accounts_mod.AccountStore(str(tmp_path / "accounts.db"))
    store.new_account_status = "waitlisted"
    return store


@pytest.fixture
def wl(acc):
    return waitlist_mod.Waitlist(acc)


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
    assert result == {"sent": 1, "failed": 0, "skipped": 0}
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


def test_unconfigured_sends_nothing(acc, wl):
    acc.sign_up("a", "a@fam.test", PASSWORD)
    wl.enqueue("a", "register")
    assert asyncio.run(vl_mod.drain(wl, vl_mod.ViralLoops())) == {
        "sent": 0, "failed": 0, "skipped": 0}
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
    r = guest.get("/", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/waitlist"
    r = guest.get("/?referralCode=abc", follow_redirects=False)
    assert r.headers["location"] == "/waitlist?referralCode=abc"
    r = guest.get("/api/friends")
    assert r.status_code == 403 and r.headers["X-FAM-Waitlist"] == "/waitlist"
    assert guest.get("/api/v1/friends").status_code == 403
    assert guest.get("/api/health").status_code == 200
    assert guest.get("/waitlist").status_code == 200

    member, body = _join("w@fam.test")
    assert body["status"] == "waitlisted" and body["redirect"] == "/waitlist/me"
    r = member.get("/", follow_redirects=False)
    assert r.headers["location"] == "/waitlist/me"
    assert member.get("/api/audio?q=x").status_code == 403
    assert member.get("/s/abc", follow_redirects=False).status_code == 302
    assert member.get("/api/waitlist/me").status_code == 200


def test_granted_accounts_get_in(world):
    member, _ = _join("w@fam.test")
    user = member.get("/api/auth/me").json()["user_id"]
    appmod.WAITLIST.grant([user])
    assert member.get("/", follow_redirects=False).status_code == 200
    assert member.get("/api/friends").status_code == 200


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
    monkeypatch.setattr(appmod, "settings",
                        dataclasses.replace(appmod.settings, waitlist=False))
    appmod.ACCOUNTS.new_account_status = "active"
    stranger = TestClient(appmod.app)
    stranger.post("/api/auth/signup", json={"email": "s@fam.test", "password": PASSWORD})
    assert stranger.get("/api/people?q=wes").json()["people"] == []
    assert stranger.get("/api/person?handle=wes").status_code == 404
    assert stranger.post("/api/friends/follow", json={"handle": "wes"}).status_code == 404
    assert stranger.post("/api/friends/follow", json={"user_id": wes}).status_code == 404
    # A friend made before still sees them, labelled.
    friend = TestClient(appmod.app)
    friend.post("/api/auth/signup", json={"email": "f@fam.test", "password": PASSWORD})
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
