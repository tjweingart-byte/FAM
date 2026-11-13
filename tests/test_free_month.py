"""The free month (§575): five friends who join with your link earn a month of
"plus", and the month runs from the day the whole app goes live.

Friends count the way invites do (`referred_by`); the month is stamped on the
account when the fifth joins and never taken back; it raises a plan only
inside the launch month, and only ever raises it.
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import accounts as accounts_mod
import waitlist as waitlist_mod
from tests.test_waitlist import PASSWORD, _join, world  # noqa: F401 (fixture)

ROOT = pathlib.Path(__file__).resolve().parent.parent
DAY = 86400.0


@pytest.fixture
def acc(tmp_path):
    store = accounts_mod.AccountStore(str(tmp_path / "accounts.db"))
    store.new_account_status = "waitlisted"
    return store


@pytest.fixture
def wl(acc):
    return waitlist_mod.Waitlist(acc)


def _bring(acc, wl, inviter, n, start=0):
    code = wl.ensure_code(inviter)
    for i in range(start, start + n):
        uid = f"{inviter}-friend{i}"
        acc.sign_up(uid, f"{uid}@fam.test", PASSWORD)
        wl.join(uid, code)


def test_five_friends_earn_it_and_four_do_not(acc, wl):
    acc.sign_up("me", "me@fam.test", PASSWORD)
    wl.join("me")
    _bring(acc, wl, "me", 4)
    fm = wl.free_month("me")
    assert fm == {"needed": 5, "invites": 4, "remaining": 1, "earned": False,
                  "starts": None, "ends": None}
    _bring(acc, wl, "me", 1, start=4)
    fm = wl.free_month("me")
    assert fm["earned"] and fm["remaining"] == 0
    assert [r["free_month"] for r in wl.ordered() if r["user_id"] == "me"] == [True]


def test_a_friend_leaving_does_not_take_the_month_back(acc, wl):
    acc.sign_up("me", "me@fam.test", PASSWORD)
    wl.join("me")
    _bring(acc, wl, "me", 5)
    acc.delete_account("me-friend0")
    wl.forget("me-friend0")
    fm = wl.free_month("me")
    assert fm["invites"] == 4 and fm["earned"] is True


def test_the_month_runs_from_launch_and_only_raises_a_plan(acc, wl):
    acc.sign_up("me", "me@fam.test", PASSWORD)
    acc.sign_up("boss", "boss@fam.test", PASSWORD)
    acc.sign_up("nobody", "n@fam.test", PASSWORD)
    for uid in ("me", "boss", "nobody"):
        wl.join(uid)
    _bring(acc, wl, "me", 5)
    _bring(acc, wl, "boss", 5)
    acc.set_plan("boss", "unlimited")
    # Earned, but the app is not live yet: still free.
    assert acc.account("me")["plan"] == "free"
    import time
    now = time.time()
    wl.set_free_month_starts(now - DAY)
    assert acc.account("me")["plan"] == "plus"
    assert acc.account("boss")["plan"] == "unlimited"     # never lowered
    assert acc.account("nobody")["plan"] == "free"        # never earned
    fm = wl.free_month("me")
    assert fm["ends"] - fm["starts"] == waitlist_mod.FREE_MONTH_SECONDS
    # A month later it is over.
    wl.set_free_month_starts(now - waitlist_mod.FREE_MONTH_SECONDS - DAY)
    assert acc.account("me")["plan"] == "free"
    # The session path reads it too.
    wl.set_free_month_starts(now - DAY)
    token, _ = acc.new_session("me")
    assert acc.listener_for(token).tier == "plus"


def test_accounts_already_at_five_are_stamped_at_start_up(acc):
    """Somebody who reached five before the reward existed has earned it."""
    acc.sign_up("me", "me@fam.test", PASSWORD)
    for i in range(5):
        acc.sign_up(f"f{i}", f"f{i}@fam.test", PASSWORD)
        acc._conn().execute("UPDATE accounts SET referred_by = 'me'"
                            " WHERE user_id = ?", (f"f{i}",))
    wl = waitlist_mod.Waitlist(acc)
    assert wl.free_month("me")["earned"] is True


def test_the_pages_sentence_names_the_number():
    """The waitlist page promises "five friends"; that is FREE_MONTH_INVITES."""
    words = {5: "five"}
    want = (f"Share the waitlist to {words[waitlist_mod.FREE_MONTH_INVITES]}"
            " friends to earn a free month!")
    assert want in (ROOT / "static" / "waitlist.html").read_text()
    assert want in (ROOT / "ios" / "FAM" / "FAM" / "Views" / "FrontDoor.swift").read_text()


def test_status_page_and_admin_carry_it(world):  # noqa: F811
    me, _ = _join("me@fam.test")
    code = me.get("/api/waitlist/me").json()["referral_code"]
    for i in range(5):
        _join(f"f{i}@fam.test", code)
    fm = me.get("/api/waitlist/me").json()["free_month"]
    assert fm["earned"] is True and fm["starts"] is None
    admin = TestClient(world.app)
    hdr = {"X-Admin-Token": "admin-secret"}
    assert admin.post("/api/admin/waitlist/free-month",
                      json={"starts": 1}).status_code == 404     # admins only
    r = admin.post("/api/admin/waitlist/free-month", json={"starts": 2_000_000_000},
                   headers=hdr)
    assert r.status_code == 200 and r.json()["starts"] == 2_000_000_000
    body = admin.get("/api/admin/waitlist", headers=hdr).json()
    assert body["free_month"]["earned"] == 1
    assert body["free_month"]["starts"] == 2_000_000_000
    assert me.get("/api/waitlist/me").json()["free_month"]["starts"] == 2_000_000_000
