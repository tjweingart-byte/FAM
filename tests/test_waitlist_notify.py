"""Letting somebody off the waitlist tells them (§585).

`grant` queues an email and/or a text - whichever the account has - in the
waitlist outbox; `waitlist_notify.drain` sends them with `/claim/<code>`;
the link stamps the free month, which runs from the day the real app is
published (`free_month_starts`), never in the beta. Viral Loops' drain and
the notices' drain never take each other's rows.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import accounts as accounts_mod
import app as appmod
import waitlist as waitlist_mod
import waitlist_notify
from tests.test_waitlist import PASSWORD, world  # noqa: F401 (fixture)

DAY = 86400.0


@pytest.fixture
def acc(tmp_path):
    store = accounts_mod.AccountStore(str(tmp_path / "accounts.db"))
    store.new_account_status = "waitlisted"
    return store


@pytest.fixture
def wl(acc):
    return waitlist_mod.Waitlist(acc)


@pytest.fixture
def outbox(monkeypatch):
    sent = {"mail": [], "sms": []}
    monkeypatch.setattr(waitlist_notify.mail, "send",
                        lambda to, subject, body: sent["mail"].append((to, subject, body)) or True)
    monkeypatch.setattr(waitlist_notify.sms, "send",
                        lambda to, body: sent["sms"].append((to, body)) or True)
    return sent


def _actions(wl, user):
    return sorted(r[0] for r in wl._conn().execute(
        "SELECT action FROM waitlist_outbox WHERE user_id = ?", (user,)))


def test_granting_queues_an_email_and_a_text_when_there_is_a_phone(acc, wl):
    acc.sign_up("e", "e@fam.test", PASSWORD)
    acc.sign_up_phone("p", "+14155550100", PASSWORD)
    assert wl.grant(["e", "p"]) == ["e", "p"]
    assert _actions(wl, "e") == ["flag", "notify_email"]
    assert _actions(wl, "p") == ["flag", "notify_sms"]
    assert wl.notice_view("e")["claim_code"]
    # Granting again moves nobody and queues nothing.
    assert wl.grant(["e"]) == []
    assert _actions(wl, "e") == ["flag", "notify_email"]


def test_an_admin_let_in_at_sign_up_is_not_told(acc, wl):
    acc.sign_up("adm", "adm@fam.test", PASSWORD)
    wl.grant(["adm"], flag=False, notify=False)
    assert _actions(wl, "adm") == []


def test_each_drain_reads_only_its_own_rows(acc, wl):
    acc.sign_up("e", "e@fam.test", PASSWORD)
    wl.grant(["e"])
    assert [r["action"] for r in wl.due(now=1e12)] == ["flag"]
    assert [r["action"] for r in wl.due(
        now=1e12, actions=waitlist_mod.NOTIFY_ACTIONS)] == ["notify_email"]


def test_drain_sends_both_with_the_claim_link_and_no_launch_date(acc, wl, outbox):
    acc.sign_up("e", "e@fam.test", PASSWORD)
    with acc._conn() as c:
        c.execute("UPDATE accounts SET phone = '+14155550101' WHERE user_id = 'e'")
    wl.grant(["e"])
    code = wl.notice_view("e")["claim_code"]
    result = waitlist_notify.drain(wl, "https://familiarize.net", "https://familiarize.net")
    assert result == {"sent": 2, "failed": 0, "skipped": 0}
    (to, subject, body), = outbox["mail"]
    assert to == "e@fam.test" and "off the" in subject
    assert f"https://familiarize.net/claim/{code}" in body
    assert "the day the full FAM app is published" in body
    (number, text), = outbox["sms"]
    assert number == "+14155550101" and f"/claim/{code}" in text
    assert "from launch day" in text and "STOP" in text
    assert len(text) <= 160  # one segment
    # Sent once: a second drain has nothing to do.
    assert waitlist_notify.drain(wl, "https://x", "https://x")["sent"] == 0


def test_a_failed_send_is_retried_not_lost(acc, wl, monkeypatch):
    acc.sign_up("e", "e@fam.test", PASSWORD)
    wl.grant(["e"])
    monkeypatch.setattr(waitlist_notify.mail, "send", lambda *a: False)
    assert waitlist_notify.drain(wl, "https://x", "https://x")["failed"] == 1
    assert wl.outbox_summary()["pending"] >= 1
    row = wl._conn().execute("SELECT attempts, done_at FROM waitlist_outbox"
                             " WHERE action = 'notify_email'").fetchone()
    assert row[0] == 1 and row[1] == 0


def test_claiming_stamps_the_month_which_waits_for_launch(acc, wl):
    acc.sign_up("e", "e@fam.test", PASSWORD)
    wl.grant(["e"])
    code = wl.notice_view("e")["claim_code"]
    assert wl.claim_free_month("not-a-code") == ""
    assert wl.claim_free_month(code, at=1000.0) == "e"
    assert wl.claim_free_month(code, at=2000.0) == "e"  # stamped once
    assert acc.account("e")["free_month_earned_at"] == 1000.0
    # The beta: no launch date, so the plan is untouched.
    assert acc.account("e")["plan"] == "free"
    wl.set_free_month_starts(10 * DAY)
    acc.forget_free_month_window()
    assert acc._with_free_month("free", 1000.0, now=11 * DAY) == "plus"
    assert acc._with_free_month("free", 1000.0, now=5 * DAY) == "free"


def test_admin_grant_and_claim_page(world, outbox):
    c = TestClient(appmod.app)
    r = c.post("/api/waitlist/join",
               json={"email": "n@fam.test", "password": PASSWORD})
    assert r.status_code == 200, r.text
    user = appmod.WAITLIST.ordered()[0]["user_id"]
    r = TestClient(appmod.app).post(
        "/api/admin/waitlist/grant", json={"user_ids": [user]},
        headers={"Authorization": "Bearer admin-secret"})
    assert r.status_code == 200 and r.json()["granted"] == 1, r.text
    # The grant kicked a drain in the background; finish whatever it left.
    import time
    for _ in range(50):
        waitlist_notify.drain(appmod.WAITLIST, "https://h", "https://h")
        if not appmod.WAITLIST.outbox_summary(waitlist_mod.NOTIFY_ACTIONS)["pending"]:
            break
        time.sleep(0.1)
    code = appmod.WAITLIST.notice_view(user)["claim_code"]
    assert any(f"/claim/{code}" in m[2] for m in outbox["mail"])
    guest = TestClient(appmod.app)
    page = guest.get(f"/claim/{code}")
    assert page.status_code == 200 and "free month is claimed" in page.text
    assert "full FAM app is published" in page.text
    assert appmod.ACCOUNTS.account(user)["free_month_earned_at"] > 0
    assert guest.get("/claim/nope").status_code == 404


def test_two_drains_at_once_send_once(acc, wl, monkeypatch):
    """The grant's kick and the timer can overlap; only one sends."""
    import threading
    acc.sign_up("e", "e@fam.test", PASSWORD)
    wl.grant(["e"])
    started, release, sent = threading.Event(), threading.Event(), []

    def slow_send(*a):
        started.set(); release.wait(5); sent.append(a); return True
    monkeypatch.setattr(waitlist_notify.mail, "send", slow_send)
    first = threading.Thread(target=waitlist_notify.drain, args=(wl, "https://x", "https://x"))
    first.start(); started.wait(5)
    assert waitlist_notify.drain(wl, "https://x", "https://x")["sent"] == 0
    release.set(); first.join(5)
    assert len(sent) == 1


def test_admin_counts_notices_apart_from_viral_loops(acc, wl):
    acc.sign_up("e", "e@fam.test", PASSWORD)
    wl.grant(["e"])
    assert wl.outbox_summary()["pending"] == 1  # the flag
    assert wl.outbox_summary(waitlist_mod.NOTIFY_ACTIONS)["pending"] == 1
