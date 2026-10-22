"""The server half of what App Review checks and the app cannot do alone.

Two things a reviewer, or Apple's rules, expect of the server:

* **Sign in with Apple is revoked when the account goes** (5.1.1(v)):
  `apple_tokens.py` keeps a refresh token from the app's authorization code
  and revokes it on deletion or unlink - and neither step can fail the
  sign-in or the deletion.
* **A report reaches a person** (1.2's 24 hours): `moderation.ReportAlerts`
  emails one, paced, naming nobody and quoting nothing.

Nothing here reaches Apple or an SMTP server; the network seams are stubbed.
"""
from __future__ import annotations

import dataclasses
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod
import apple_tokens
import mail
import moderation
import oauth
import config


def use(monkeypatch, **values):
    """Settings are frozen; a test swaps in a copy. The modules under test
    read `config.settings` at call time, so the copy is what they see."""
    monkeypatch.setattr(config, "settings",
                        dataclasses.replace(config.settings, **values))


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    monkeypatch.setattr(appmod, "_read_limit", lambda request: None)
    apple_tokens.reset_counts()


@pytest.fixture
def apple_key(monkeypatch):
    """A real P-256 key, so the client secret is a real ES256 JWT."""
    ec = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ec")
    from cryptography.hazmat.primitives import serialization
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    # Stored the way a dashboard stores it: one line, literal \n.
    monkeypatch.setenv("APPLE_PRIVATE_KEY", pem.replace("\n", "\\n"))
    use(monkeypatch, apple_team_id="TEAM123456", apple_key_id="KEY1234567")
    return key


class Answer:
    def __init__(self, status: int, body: dict):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


# --- Apple tokens ------------------------------------------------------------

def test_unconfigured_names_what_to_set(monkeypatch):
    monkeypatch.delenv("APPLE_PRIVATE_KEY", raising=False)
    use(monkeypatch, apple_key_id="", apple_team_id="")
    state = apple_tokens.status()
    assert state["available"] is False
    for name in ("APPLE_TEAM_ID", "APPLE_KEY_ID", "APPLE_PRIVATE_KEY", "5.1.1(v)"):
        assert name in state["reason"]
    # Nothing to call, nothing called: a code is simply not exchanged.
    assert apple_tokens.exchange("code", "net.familiarize.fam") == ""


def test_a_missing_library_is_named_rather_than_failing_later(monkeypatch):
    """The production image once had no PyJWT (§303): configured, and every
    exchange would have failed at the import. It says so up front instead."""
    monkeypatch.setenv("APPLE_PRIVATE_KEY", "-----BEGIN PRIVATE KEY-----")
    use(monkeypatch, apple_team_id="TEAM123456", apple_key_id="KEY1234567")

    def missing():
        raise oauth.OAuthUnavailable("PyJWT is not installed. " + oauth.INSTALL_HINT)
    monkeypatch.setattr(oauth, "_library", missing)
    state = apple_tokens.status()
    assert state["available"] is False
    assert "requirements-oauth.txt" in state["reason"]


def test_the_production_image_installs_the_sign_in_library():
    """Sign in with Apple is required beside Google (4.8); without PyJWT in
    the image neither can verify a token, whatever is configured (§303)."""
    import pathlib
    docker = (pathlib.Path(__file__).resolve().parent.parent / "Dockerfile").read_text()
    install = [l for l in docker.splitlines() if "pip install" in l or l.startswith("    -r")]
    assert any("requirements-oauth.txt" in l for l in install)
    assert "requirements-oauth.txt ./" in docker


def test_the_client_secret_is_what_apple_checks(apple_key):
    import jwt
    secret = apple_tokens.client_secret("net.familiarize.fam", now=1_000_000)
    assert jwt.get_unverified_header(secret) == {
        "alg": "ES256", "kid": "KEY1234567", "typ": "JWT"}
    claims = jwt.decode(secret, apple_key.public_key(), algorithms=["ES256"],
                        audience="https://appleid.apple.com",
                        options={"verify_exp": False})
    assert claims["iss"] == "TEAM123456"
    assert claims["sub"] == "net.familiarize.fam"
    assert claims["exp"] - claims["iat"] == apple_tokens.SECRET_SECONDS


def test_a_code_becomes_a_refresh_token(apple_key, monkeypatch):
    sent = []

    def post(url, data):
        sent.append((url, data))
        return Answer(200, {"refresh_token": "rt-1", "id_token": "x"})
    monkeypatch.setattr(apple_tokens, "_post", post)
    assert apple_tokens.exchange("code-1", "net.familiarize.fam") == "rt-1"
    url, data = sent[0]
    assert url == apple_tokens.TOKEN_URL
    assert data["grant_type"] == "authorization_code"
    assert data["code"] == "code-1"
    assert data["client_id"] == "net.familiarize.fam"
    assert apple_tokens.report()["exchanged"] == 1


def test_a_refused_exchange_is_counted_and_never_raises(apple_key, monkeypatch):
    monkeypatch.setattr(apple_tokens, "_post",
                        lambda url, data: Answer(400, {"error": "invalid_grant"}))
    assert apple_tokens.exchange("old-code", "net.familiarize.fam") == ""
    report = apple_tokens.report()
    assert report["exchange_failed"] == 1
    assert "invalid_grant" in report["last_error"]
    assert "old-code" not in report["last_error"]


def test_a_revoke_is_retried_then_counted(apple_key, monkeypatch):
    answers = iter([Answer(503, {}), Answer(200, {})])
    sent = []

    def post(url, data):
        sent.append(data)
        return next(answers)
    monkeypatch.setattr(apple_tokens, "_post", post)
    done = apple_tokens._revoke_with_retries([("rt-1", "net.familiarize.fam")],
                                             pause=lambda s: None)
    assert done == 1
    assert [d["token_type_hint"] for d in sent] == ["refresh_token"] * 2
    report = apple_tokens.report()
    assert report["revoked"] == 1 and report["revoke_failed"] == 1


def test_a_revoke_that_cannot_be_sent_is_visible(monkeypatch):
    monkeypatch.delenv("APPLE_PRIVATE_KEY", raising=False)
    use(monkeypatch, apple_key_id="")
    assert apple_tokens.revoke_later([("rt-1", "net.familiarize.fam")]) == 0
    assert apple_tokens.report()["revoke_failed"] == 1


def _apple_signin(monkeypatch, client: TestClient, code: str = "code-1") -> dict:
    monkeypatch.setattr(appmod.oauth, "verify", lambda p, t, n="": oauth.VerifiedIdentity(
        provider="apple", subject="apple-rev-1", audience="net.familiarize.fam"))
    monkeypatch.setattr(appmod.apple_tokens, "exchange",
                        lambda c, client_id: "rt-1" if c == "code-1" else "")
    r = client.post("/api/auth/provider", json={
        "provider": "apple", "id_token": "tok", "authorization_code": code})
    assert r.status_code == 200, r.text
    return r.json()


def test_deleting_an_apple_account_revokes_its_token(monkeypatch):
    client = TestClient(appmod.app)
    user = _apple_signin(monkeypatch, client)["user_id"]
    assert appmod.ACCOUNTS.provider_tokens(user, "apple") == [
        ("rt-1", "net.familiarize.fam")]
    # Never handed to the client, even in the settings screen.
    assert "rt-1" not in client.get("/api/account").text

    revoked = []
    monkeypatch.setattr(appmod.apple_tokens, "revoke_later",
                        lambda tokens: revoked.extend(tokens) or len(tokens))
    r = client.request("DELETE", "/api/account")
    assert r.status_code == 200, r.text
    assert revoked == [("rt-1", "net.familiarize.fam")]
    assert r.json()["removed"]["apple_revoked"] == 1
    assert "rt-1" not in r.text
    assert appmod.ACCOUNTS.provider_tokens(user, "apple") == []


def test_unlinking_apple_revokes_its_token(monkeypatch):
    client = TestClient(appmod.app)
    user = _apple_signin(monkeypatch, client)["user_id"]
    # A password first, or unlinking the only way in is refused.
    appmod.ACCOUNTS.set_password(user, "password12")
    revoked = []
    monkeypatch.setattr(appmod.apple_tokens, "revoke_later",
                        lambda tokens: revoked.extend(tokens) or len(tokens))
    r = client.request("DELETE", "/api/account/identity", params={"provider": "apple"})
    assert r.status_code == 200, r.text
    assert revoked == [("rt-1", "net.familiarize.fam")]


def test_signing_in_without_a_code_is_unchanged(monkeypatch):
    client = TestClient(appmod.app)
    user = _apple_signin(monkeypatch, client, code="")["user_id"]
    assert appmod.ACCOUNTS.provider_tokens(user) == []


# --- report alerts -----------------------------------------------------------

@pytest.fixture
def mailbox(monkeypatch):
    sent = []
    monkeypatch.setattr(mail, "status", lambda: {"available": True, "reason": ""})
    monkeypatch.setattr(mail, "send_later", lambda to, subject, body:
                        sent.append((to, subject, body)))
    return sent


def test_nobody_to_tell_says_so(monkeypatch):
    use(monkeypatch, moderation_email="", support_email="")
    state = moderation.ReportAlerts().status()
    assert state["available"] is False
    assert "MODERATION_EMAIL" in state["reason"]


def test_an_alert_names_nobody_and_quotes_nothing(monkeypatch, mailbox, tmp_path):
    use(monkeypatch, moderation_email="mod@familiarize.net")
    store = moderation.ModerationStore(path=str(tmp_path / "m.db"))
    store.report("reporter-1", "comment", "42", "harassment",
                 owner="author-1", note="he said something vile")
    alerts = moderation.ReportAlerts()
    assert alerts.notify(store, "https://familiarize.net/admin", now=1000.0)
    to, subject, body = mailbox[0]
    assert to == "mod@familiarize.net"
    assert subject == "FAM: 1 report waiting"
    assert "https://familiarize.net/admin" in body and "24 hours" in body
    for secret in ("reporter-1", "author-1", "vile", "42", "harassment"):
        assert secret not in subject + body


def test_a_burst_is_one_email(monkeypatch, mailbox, tmp_path):
    use(monkeypatch, moderation_email="", support_email="help@familiarize.net")
    store = moderation.ModerationStore(path=str(tmp_path / "m.db"))
    alerts = moderation.ReportAlerts(every=900)
    assert alerts.notify(store, "/admin", now=1000.0)
    assert not alerts.notify(store, "/admin", now=1500.0)
    assert alerts.notify(store, "/admin", now=1901.0)
    assert [m[0] for m in mailbox] == ["help@familiarize.net"] * 2


def test_filing_a_report_tells_a_person(monkeypatch, mailbox):
    use(monkeypatch, moderation_email="mod@familiarize.net")
    monkeypatch.setattr(appmod, "REPORT_ALERTS", moderation.ReportAlerts())
    ann, ben = TestClient(appmod.app), TestClient(appmod.app)
    for c, handle in ((ann, "annalert"), (ben, "benalert")):
        assert c.post("/api/auth/signup", json={
            "email": f"{handle}@example.com", "password": "password12"}).status_code == 200
        assert c.post("/api/me", json={"name": handle, "handle": handle}).status_code == 200
    cid = ben.post("/api/comments", json={"query": "why the sky is blue",
                                          "minutes": 2, "text": "rude"}).json()["id"]
    r = ann.post("/api/report", json={"kind": "comment", "target": str(cid),
                                      "reason": "harassment"})
    assert r.status_code == 200, r.text
    assert len(mailbox) == 1 and mailbox[0][2].count("/admin") == 1
    health = TestClient(appmod.app).get("/api/health").json()
    assert health["report_alerts"]["available"] is True
    assert "apple_tokens" in health
