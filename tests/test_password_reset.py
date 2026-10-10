"""Password reset by an emailed code (§244), and what stops it being used on
somebody else's account.

The security property: a reset needs a code that was sent only to the address
already on the account, so knowing an address is not enough. Around it, every
way of guessing, flooding or probing for accounts is bounded or blind.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import accounts as A  # noqa: E402
import app as appmod  # noqa: E402
import mail  # noqa: E402

GOOD = "a-long-enough-password"
NEW = "a-brand-new-password"


@pytest.fixture
def store(tmp_path):
    return A.AccountStore(str(tmp_path / "accounts.db"))


@pytest.fixture
def outbox(monkeypatch):
    """Mail configured, and every message captured instead of sent."""
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(mail, "status", lambda: {"available": True, "reason": ""})
    monkeypatch.setattr(mail, "send_later",
                        lambda to, subject, body: sent.append((to, subject, body)))
    return sent


@pytest.fixture
def client(monkeypatch, tmp_path, outbox):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    monkeypatch.setattr(appmod, "ACCOUNTS", A.AccountStore(str(tmp_path / "a.db")))
    return TestClient(appmod.app)


def _code(outbox) -> str:
    subject = outbox[-1][1]
    return subject.split()[0]


# --- the store ------------------------------------------------------------


def test_a_code_resets_the_password_and_ends_every_session(store):
    store.sign_up("u1", "ian@example.com", GOOD)
    token, _ = store.new_session("u1")
    user, address, code = store.start_reset("Ian@Example.com", at=1000.0)
    assert (user, address) == ("u1", "ian@example.com")
    assert len(code) == 6 and code.isdigit()

    store.finish_reset("ian@example.com", code, NEW, at=1010.0)
    assert store.listener_for(token) is None, "an old session survived the reset"
    store.log_in("ian@example.com", NEW)
    with pytest.raises(A.AuthError):
        store.log_in("ian@example.com", GOOD)


def test_the_code_is_never_stored(store):
    store.sign_up("u1", "ian@example.com", GOOD)
    _, _, code = store.start_reset("ian@example.com", at=1000.0)
    # Through the store's own connection, so this holds on either backend.
    rows = store._conn().execute(
        "SELECT code_hash FROM password_resets").fetchall()
    assert rows and code not in rows[0][0]
    assert rows[0][0] == A._reset_hash("u1", code)


def test_no_account_sends_nothing(store):
    assert store.start_reset("nobody@example.com") is None
    assert store.start_reset("not an address") is None


def test_a_code_is_good_once(store):
    store.sign_up("u1", "ian@example.com", GOOD)
    _, _, code = store.start_reset("ian@example.com", at=1000.0)
    store.finish_reset("ian@example.com", code, NEW, at=1010.0)
    with pytest.raises(A.AuthError):
        store.finish_reset("ian@example.com", code, "yet-another-password", at=1020.0)


def test_a_code_expires(store):
    store.sign_up("u1", "ian@example.com", GOOD)
    _, _, code = store.start_reset("ian@example.com", at=1000.0)
    with pytest.raises(A.AuthError):
        store.finish_reset("ian@example.com", code, NEW,
                           at=1000.0 + A.RESET_CODE_SECONDS + 1)


def test_wrong_guesses_spend_the_code(store):
    store.sign_up("u1", "ian@example.com", GOOD)
    _, _, code = store.start_reset("ian@example.com", at=1000.0)
    wrong = f"{(int(code) + 1) % 10 ** 6:06d}"
    for _ in range(A.RESET_MAX_ATTEMPTS):
        with pytest.raises(A.AuthError):
            store.finish_reset("ian@example.com", wrong, NEW, at=1001.0)
    with pytest.raises(A.AuthError):
        store.finish_reset("ian@example.com", code, NEW, at=1002.0)


def test_a_code_for_one_account_does_not_reset_another(store):
    store.sign_up("u1", "ian@example.com", GOOD)
    store.sign_up("u2", "eve@example.com", GOOD)
    _, _, mine = store.start_reset("eve@example.com", at=1000.0)
    store.start_reset("ian@example.com", at=1000.0)
    with pytest.raises(A.AuthError):
        store.finish_reset("ian@example.com", mine, NEW, at=1001.0)
    store.log_in("ian@example.com", GOOD)


def test_asking_again_cannot_buy_unlimited_guesses(store):
    store.sign_up("u1", "ian@example.com", GOOD)
    assert store.start_reset("ian@example.com", at=1000.0)
    assert store.start_reset("ian@example.com", at=1030.0) is None, "resent within a minute"
    at = 1000.0
    for _ in range(A.RESET_MAX_CODES - 1):
        at += A.RESET_RESEND_SECONDS
        assert store.start_reset("ian@example.com", at=at)
    assert store.start_reset("ian@example.com", at=at + A.RESET_RESEND_SECONDS) is None
    assert store.start_reset("ian@example.com", at=1000.0 + A.RESET_WINDOW_SECONDS + 1)


def test_a_weak_new_password_does_not_spend_an_attempt(store):
    store.sign_up("u1", "ian@example.com", GOOD)
    _, _, code = store.start_reset("ian@example.com", at=1000.0)
    for _ in range(A.RESET_MAX_ATTEMPTS + 1):
        with pytest.raises(A.AuthError):
            store.finish_reset("ian@example.com", code, "short", at=1001.0)
    store.finish_reset("ian@example.com", code, NEW, at=1002.0)


def test_deleting_the_account_takes_its_reset(store):
    store.sign_up("u1", "ian@example.com", GOOD)
    store.start_reset("ian@example.com", at=1000.0)
    store.delete_account("u1")
    rows = store._conn().execute(
        "SELECT COUNT(*) FROM password_resets").fetchone()[0]
    assert rows == 0


# --- the endpoints --------------------------------------------------------


def test_the_whole_flow_signs_this_device_in(client, outbox):
    client.post("/api/auth/signup", json={"email": "ian@example.com", "password": GOOD})
    mine = client.get("/api/auth/me").json()["user_id"]
    elsewhere = TestClient(appmod.app)
    elsewhere.post("/api/auth/login", json={"email": "ian@example.com", "password": GOOD})

    stranger = TestClient(appmod.app)
    r = stranger.post("/api/auth/reset/start", json={"email": "ian@example.com"})
    assert r.status_code == 200
    assert outbox[-1][0] == "ian@example.com", "the code went somewhere else"
    r = stranger.post("/api/auth/reset/finish",
                      json={"email": "ian@example.com", "code": _code(outbox), "new": NEW})
    assert r.status_code == 200
    assert stranger.get("/api/auth/me").json()["user_id"] == mine
    assert elsewhere.get("/api/auth/me").json()["user_id"] != mine
    assert outbox[-1][1] == "Your FAM password was changed"


def test_the_answer_does_not_say_whether_an_account_exists(client, outbox):
    client.post("/api/auth/signup", json={"email": "ian@example.com", "password": GOOD})
    real = client.post("/api/auth/reset/start", json={"email": "ian@example.com"}).json()
    fake = client.post("/api/auth/reset/start", json={"email": "no@example.com"}).json()
    assert real == fake
    assert [to for to, _, _ in outbox] == ["ian@example.com"]


def test_a_wrong_code_is_refused_with_one_sentence(client, outbox):
    client.post("/api/auth/signup", json={"email": "ian@example.com", "password": GOOD})
    client.post("/api/auth/reset/start", json={"email": "ian@example.com"})
    wrong = client.post("/api/auth/reset/finish",
                        json={"email": "ian@example.com", "code": "000000", "new": NEW})
    nobody = client.post("/api/auth/reset/finish",
                         json={"email": "no@example.com", "code": "000000", "new": NEW})
    assert wrong.status_code == nobody.status_code == 400
    assert wrong.json() == nobody.json()


def test_no_mail_means_no_reset_and_says_why(client, monkeypatch):
    monkeypatch.setattr(mail, "status", lambda: {
        "available": False, "reason": "Email is not set up on this server yet (no SMTP_HOST)."})
    assert client.get("/api/auth/reset").json()["available"] is False
    r = client.post("/api/auth/reset/start", json={"email": "ian@example.com"})
    assert r.status_code == 503
    assert "SMTP_HOST" in r.text


def test_staging_sends_no_mail(monkeypatch):
    import spend_guard
    monkeypatch.setattr(spend_guard, "enabled", lambda: True)
    assert mail.status()["available"] is False
    assert "staging" in mail.status()["reason"]


def test_health_reports_mail():
    body = TestClient(appmod.app).get("/api/health").json()
    assert "available" in body["mail"] and "failed" in body["mail"]


def test_a_failed_send_is_counted_and_named(monkeypatch):
    """A code that never arrives must be visible to whoever runs the server."""
    import socket
    from config import settings
    import spend_guard
    monkeypatch.setattr(spend_guard, "enabled", lambda: False)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]
    import dataclasses
    configured = dataclasses.replace(settings, smtp_host="127.0.0.1", smtp_port=closed,
                                     mail_from="FAM <hello@example.com>")
    monkeypatch.setattr(mail, "_settings", lambda: configured)
    mail.reset_counts()
    assert mail.status()["available"] is True
    assert mail.send("ian@example.com", "subject", "body") is False
    report = mail.report()
    assert report["failed"] == 1 and report["last_error"]
    assert "ian@example.com" not in report["last_error"]
    mail.reset_counts()


def test_a_refused_recipient_is_not_named_in_health(monkeypatch):
    """The provider's refusal carries the address; health must not."""
    import dataclasses
    import smtplib
    import spend_guard
    from config import settings
    monkeypatch.setattr(spend_guard, "enabled", lambda: False)
    configured = dataclasses.replace(settings, smtp_host="smtp.invalid",
                                     mail_from="FAM <hello@example.com>")
    monkeypatch.setattr(mail, "_settings", lambda: configured)

    def refuse(*args, **kwargs):
        raise smtplib.SMTPRecipientsRefused({"ian@example.com": (550, b"no such user")})
    monkeypatch.setattr(smtplib, "SMTP", refuse)
    mail.reset_counts()
    assert mail.send("ian@example.com", "subject", "body") is False
    assert "ian@example.com" not in mail.report()["last_error"]
    mail.reset_counts()


def test_every_guess_is_counted_before_it_is_compared(store):
    """The count is one conditional UPDATE taken before the comparison, so
    guesses sent in parallel cannot all pass the check before any adds one."""
    store.sign_up("u1", "ian@example.com", GOOD)
    _, _, code = store.start_reset("ian@example.com", at=1000.0)
    wrong = f"{(int(code) + 1) % 10 ** 6:06d}"
    for _ in range(A.RESET_MAX_ATTEMPTS):
        with pytest.raises(A.AuthError):
            store.finish_reset("ian@example.com", wrong, NEW, at=1001.0)
    attempts = store._conn().execute(
        "SELECT attempts FROM password_resets").fetchone()[0]
    assert attempts == A.RESET_MAX_ATTEMPTS
    with pytest.raises(A.AuthError):
        store.finish_reset("ian@example.com", code, NEW, at=1002.0)
