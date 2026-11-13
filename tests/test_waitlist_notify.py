"""Letting somebody off the waitlist tells them (§587).

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


# --- the free month in the apps (§593) --------------------------------------

def test_offer_only_for_somebody_let_in(acc, wl):
    acc.sign_up("w", "w@fam.test", PASSWORD)
    assert wl.claim_offer("w") is None          # still waiting
    acc.sign_up("old", "old@fam.test", PASSWORD)
    with acc._conn() as c:
        c.execute("UPDATE accounts SET status = 'active' WHERE user_id = 'old'")
    assert wl.claim_offer("old") is None        # let in before notices existed
    assert wl.claim_for("old") is None
    wl.grant(["w"])
    assert wl.claim_offer("w") == {"claimed": False, "starts": None, "ends": None}


def test_claiming_in_the_app_is_the_links_claim(acc, wl):
    acc.sign_up("w", "w@fam.test", PASSWORD)
    wl.grant(["w"])
    assert wl.claim_for("w", at=500.0)["claimed"] is True
    assert acc.account("w")["free_month_earned_at"] == 500.0
    code = wl.notice_view("w")["claim_code"]
    assert wl.claim_free_month(code, at=900.0) == "w"
    assert acc.account("w")["free_month_earned_at"] == 500.0  # still once
    wl.set_free_month_starts(10 * DAY)
    assert wl.claim_offer("w") == {"claimed": True, "starts": 10 * DAY,
                                   "ends": 10 * DAY + waitlist_mod.FREE_MONTH_SECONDS}


def test_auth_me_carries_it_and_the_button_claims(world):
    c = TestClient(appmod.app)
    r = c.post("/api/waitlist/join", json={"email": "m@fam.test", "password": PASSWORD})
    assert r.status_code == 200, r.text
    user = appmod.WAITLIST.ordered()[0]["user_id"]
    assert c.get("/api/auth/me").json().get("free_month") is None
    appmod.WAITLIST.grant([user])
    assert c.get("/api/auth/me").json()["free_month"] == {
        "claimed": False, "starts": None, "ends": None}
    r = c.post("/api/v1/free-month/claim")
    assert r.status_code == 200 and r.json()["free_month"]["claimed"] is True
    assert c.get("/api/v1/auth/me").json()["free_month"]["claimed"] is True
    assert TestClient(appmod.app).post("/api/free-month/claim").status_code in (401, 403)


def test_both_apps_draw_the_card_with_the_same_words():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    web = open(os.path.join(root, "static", "index.html"), encoding="utf-8").read()
    ios = open(os.path.join(root, "ios", "FAM", "FAM", "Views", "DailyView.swift"),
               encoding="utf-8").read()
    api = open(os.path.join(root, "ios", "FAM", "FAM", "API.swift"), encoding="utf-8").read()
    assert 'id="freeMonthCard"' in web and "/api/free-month/claim" in web
    assert "FreeMonthCard()" in ios and "api/v1/free-month/claim" in api
    for words in ("Claim your free month of FAM Plus", "Claim free month",
                  "A month of FAM Plus is yours",
                  "It starts the day the full FAM app is published.",
                  "Nothing to pay, and nothing is billed after."):
        assert words in web and words in ios, words


# --- one text, ever (§597) --------------------------------------------------

def _phone_user(acc, uid="t", number="+14155550199"):
    acc.sign_up(uid, f"{uid}@fam.test", PASSWORD)
    with acc._conn() as c:
        c.execute("UPDATE accounts SET phone = ? WHERE user_id = ?", (number, uid))


def _texts(monkeypatch, *outcomes):
    sent, answers = [], list(outcomes)
    def fake(to, body):
        sent.append(to)
        return answers.pop(0) if answers else waitlist_notify.sms.SENT
    monkeypatch.setattr(waitlist_notify.sms, "send", fake)
    monkeypatch.setattr(waitlist_notify.mail, "send", lambda *a: True)
    return sent


def test_a_texted_account_is_never_texted_again(acc, wl, monkeypatch):
    _phone_user(acc)
    sent = _texts(monkeypatch)
    wl.grant(["t"])
    waitlist_notify.drain(wl, "https://x", "https://x")
    assert sent == ["+14155550199"] and wl.texted("t")
    # Put back in line and let in again: no second text is even queued.
    with acc._conn() as c:
        c.execute("UPDATE accounts SET status = 'waitlisted' WHERE user_id = 't'")
    wl.grant(["t"])
    assert _actions(wl, "t").count("notify_sms") == 1
    # A stray queued row (an older server, a hand-made one) still sends nothing.
    wl.enqueue("t", waitlist_mod.NOTIFY_SMS)
    waitlist_notify.drain(wl, "https://x", "https://x")
    assert sent == ["+14155550199"]


def test_no_answer_from_twilio_is_never_retried(acc, wl, monkeypatch):
    _phone_user(acc)
    sent = _texts(monkeypatch, waitlist_notify.sms.UNKNOWN)
    wl.grant(["t"])
    waitlist_notify.drain(wl, "https://x", "https://x")
    for _ in range(3):
        wl._conn().execute("UPDATE waitlist_outbox SET next_at = 0")
        waitlist_notify.drain(wl, "https://x", "https://x")
    assert sent == ["+14155550199"]
    assert wl.outbox_summary(waitlist_mod.NOTIFY_ACTIONS)["refused"] == 1


def test_a_certain_failure_keeps_the_one_text_owed(acc, wl, monkeypatch):
    _phone_user(acc)
    sent = _texts(monkeypatch, waitlist_notify.sms.NOT_SENT)
    wl.grant(["t"])
    waitlist_notify.drain(wl, "https://x", "https://x")
    assert not wl.texted("t")
    wl._conn().execute("UPDATE waitlist_outbox SET next_at = 0")
    waitlist_notify.drain(wl, "https://x", "https://x")
    assert len(sent) == 2 and wl.texted("t")
    wl._conn().execute("UPDATE waitlist_outbox SET next_at = 0")
    waitlist_notify.drain(wl, "https://x", "https://x")
    assert len(sent) == 2


def test_only_one_claim_on_the_text_wins(acc, wl):
    _phone_user(acc)
    assert wl.take_text("t") is True
    assert wl.take_text("t") is False


def test_sms_sorts_its_failures(monkeypatch):
    import httpx
    import sms
    monkeypatch.setattr(sms, "status", lambda: {"available": True, "reason": ""})
    monkeypatch.setattr(sms, "_token", lambda: "tok")
    monkeypatch.setattr(sms, "_settings", lambda: type("S", (), {
        "twilio_account_sid": "AC1", "twilio_from": "+18885550100"})())

    def raising(exc):
        def post(*a, **k):
            raise exc
        return post

    def answering(code):
        return lambda *a, **k: httpx.Response(code, json={"message": "m"})
    for post, want in ((raising(httpx.ConnectError("x")), sms.NOT_SENT),
                       (raising(httpx.ReadTimeout("x")), sms.UNKNOWN),
                       (answering(400), sms.NOT_SENT),
                       (answering(503), sms.UNKNOWN),
                       (answering(201), sms.SENT)):
        monkeypatch.setattr(httpx, "post", post)
        assert sms.send("+14155550100", "hi") == want


def test_an_email_only_join_is_told_its_link_sets_the_password(acc, wl, outbox):
    """§604: the waitlist asks no password, so the let-in notice says its
    link is where the password is set, and the text stays one segment."""
    acc.sign_up("np", "np@fam.test", "", passwordless=True)
    with acc._conn() as c:
        c.execute("UPDATE accounts SET phone = '+14155550102' WHERE user_id = 'np'")
    wl.grant(["np"])
    view = wl.notice_view("np")
    assert view["can_sign_in"] is False
    waitlist_notify.drain(wl, "https://familiarize.net", "https://familiarize.net")
    (_to, _subject, body), = outbox["mail"]
    assert "set your password" in body and f"/claim/{view['claim_code']}" in body
    assert "Sign in the way you joined" not in body
    (_number, text), = outbox["sms"]
    assert "Set your password" in text and len(text) <= 160


def test_a_google_or_apple_account_keeps_sign_in_the_way_you_joined(acc, wl, outbox):
    """§604: no password hash, but a way in - its notice is not told to set one."""
    acc.sign_up("g", "g@fam.test", "", passwordless=True)
    with acc._conn() as c:
        c.execute("INSERT INTO identities (provider, subject, user_id, email, created, last_login)"
                  " VALUES ('google', 'sub-1', 'g', 'g@fam.test', 1, 1)")
    wl.grant(["g"])
    assert wl.notice_view("g")["can_sign_in"] is True
    waitlist_notify.drain(wl, "https://familiarize.net", "https://familiarize.net")
    (_to, _subject, body), = outbox["mail"]
    assert "Sign in the way you joined" in body and "set your password" not in body
