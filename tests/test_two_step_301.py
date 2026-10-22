"""Two-step sign-in (§301): a code from an authenticator app, or a recovery
code, as well as the password - for any listener who turns it on, and for
every admin at `/admin`.

The properties: a right password alone mints no session on an account that
has it on, by any route (password, phone, Google/Apple, a reset, the admin
dashboard); a code works once; guesses are bounded per challenge and per
account; the secret and recovery codes are never handed back after set-up;
and turning it off needs a code, not just the session.
"""
from __future__ import annotations

import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import accounts as A  # noqa: E402
import app as appmod  # noqa: E402
import mail  # noqa: E402
import second_factor as SF  # noqa: E402

GOOD = "a-long-enough-password"
T0 = 1_800_000_000.0


# --- the arithmetic ---------------------------------------------------------


def test_totp_matches_the_rfc_6238_vectors():
    """RFC 6238 appendix B, SHA-1, secret "12345678901234567890", 8 digits
    truncated to our 6 (the last six digits of the published values)."""
    import base64
    secret = base64.b32encode(b"12345678901234567890").decode()
    for at, eight in ((59, "94287082"), (1111111109, "07081804"),
                      (1234567890, "89005924"), (2000000000, "69279037")):
        assert SF.code_at(secret, SF.step_for(at)) == eight[-6:]


def test_a_code_is_accepted_one_step_either_side_and_no_further():
    secret = SF.new_secret()
    now = SF.step_for(T0)
    for offset, ok in ((-2, False), (-1, True), (0, True), (1, True), (2, False)):
        code = SF.code_at(secret, now + offset)
        assert (SF.matching_step(secret, code, T0) is not None) == ok, offset


def test_the_otpauth_link_names_fam_and_the_account():
    uri = SF.otpauth_uri("ABCDEF", "ian@example.com")
    assert uri.startswith("otpauth://totp/FAM:ian@example.com?")
    assert "secret=ABCDEF" in uri and "issuer=FAM" in uri


def test_recovery_codes_are_distinct_and_typed_loosely():
    codes = SF.new_recovery_codes()
    assert len(set(codes)) == SF.RECOVERY_CODES
    assert all(SF.looks_like_recovery(c) for c in codes)
    assert SF.clean_recovery(codes[0].lower().replace("-", " ")) == \
        SF.clean_recovery(codes[0])
    assert not SF.looks_like_recovery("123456")


# --- the store ----------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    s = A.AccountStore(str(tmp_path / "accounts.db"))
    s.sign_up("u1", "ian@example.com", GOOD)
    return s


def _turn_on(store, user="u1", at=T0):
    secret = store.begin_two_step(user, at=at)
    codes = store.enable_two_step(user, SF.code_at(secret, SF.step_for(at)), at=at)
    return secret, codes


def test_nothing_changes_until_a_code_confirms_the_secret(store):
    secret = store.begin_two_step("u1", at=T0)
    assert not store.has_two_step("u1")
    with pytest.raises(A.AuthError):
        store.enable_two_step("u1", "000000", at=T0)
    assert not store.has_two_step("u1")
    # A set-up left too long is forgotten rather than confirmed late.
    with pytest.raises(A.AuthError, match="expired"):
        store.enable_two_step(
            "u1", SF.code_at(secret, SF.step_for(T0 + 3600)), at=T0 + 3600)


def test_on_it_gives_ten_recovery_codes_and_stores_only_hashes(store):
    secret, codes = _turn_on(store)
    assert store.has_two_step("u1")
    assert store.two_step("u1")["recovery_codes_left"] == 10
    stored = [r[0] for r in store._conn().execute(
        "SELECT code_hash FROM recovery_codes").fetchall()]
    assert not set(SF.clean_recovery(c) for c in codes) & set(stored)
    events = [e["kind"] for e in store.security_events_for("u1")]
    assert events == ["two_step_on"]


def test_an_app_code_works_once(store):
    secret, _ = _turn_on(store)
    later = T0 + 90
    code = SF.code_at(secret, SF.step_for(later))
    assert store.check_second_factor("u1", code, at=later) == "app"
    with pytest.raises(A.AuthError):
        store.check_second_factor("u1", code, at=later)
    # The code used to turn it on is spent too.
    with pytest.raises(A.AuthError):
        store.check_second_factor("u1", SF.code_at(secret, SF.step_for(T0)), at=T0)


def test_a_recovery_code_works_once_and_is_recorded(store):
    _, codes = _turn_on(store)
    assert store.check_second_factor("u1", codes[3].lower(), at=T0 + 5) == "recovery"
    with pytest.raises(A.AuthError):
        store.check_second_factor("u1", codes[3], at=T0 + 6)
    assert store.two_step("u1")["recovery_codes_left"] == 9
    assert "recovery_code_used" in [e["kind"] for e in store.security_events_for("u1")]


def test_wrong_codes_lock_the_account_for_a_while(store):
    secret, _ = _turn_on(store)
    for i in range(A.SECOND_FACTOR_MAX_FAILURES):
        with pytest.raises(A.AuthError):
            store.check_second_factor("u1", "000000", at=T0 + 100 + i)
    right = SF.code_at(secret, SF.step_for(T0 + 200))
    with pytest.raises(A.AuthError, match="Too many"):
        store.check_second_factor("u1", right, at=T0 + 200)
    after = T0 + 200 + A.SECOND_FACTOR_FAILURE_WINDOW
    assert store.check_second_factor(
        "u1", SF.code_at(secret, SF.step_for(after)), at=after) == "app"


def test_a_challenge_has_five_tries_and_then_is_spent(store):
    secret, _ = _turn_on(store)
    token = store.new_challenge("u1", at=T0 + 60)
    for _ in range(A.CHALLENGE_MAX_ATTEMPTS):
        with pytest.raises(A.AuthError):
            store.redeem_challenge(token, "000000", at=T0 + 61)
    with pytest.raises(A.AuthError, match="expired"):
        store.redeem_challenge(token, SF.code_at(secret, SF.step_for(T0 + 62)),
                               at=T0 + 62)


def test_a_challenge_is_for_one_purpose_and_expires(store):
    secret, _ = _turn_on(store)
    app_token = store.new_challenge("u1", "app", at=T0 + 60)
    code = SF.code_at(secret, SF.step_for(T0 + 60))
    with pytest.raises(A.AuthError, match="expired"):
        store.redeem_challenge(app_token, code, "admin", at=T0 + 60)
    late = T0 + 60 + A.CHALLENGE_SECONDS + 1
    with pytest.raises(A.AuthError, match="expired"):
        store.redeem_challenge(app_token, SF.code_at(secret, SF.step_for(late)),
                               at=late)
    fresh = store.new_challenge("u1", "app", at=T0 + 600)
    listener, how = store.redeem_challenge(
        fresh, SF.code_at(secret, SF.step_for(T0 + 600)), at=T0 + 600)
    assert listener.user_id == "u1" and how == "app"


def test_turning_it_off_needs_a_code(store):
    secret, _ = _turn_on(store)
    with pytest.raises(A.AuthError):
        store.disable_two_step("u1", "000000", at=T0 + 60)
    assert store.has_two_step("u1")
    store.disable_two_step("u1", SF.code_at(secret, SF.step_for(T0 + 60)), at=T0 + 60)
    assert not store.has_two_step("u1")
    assert store.two_step("u1")["recovery_codes_left"] == 0


def test_new_recovery_codes_void_the_old_ones(store):
    secret, old = _turn_on(store)
    new = store.renew_recovery_codes(
        "u1", SF.code_at(secret, SF.step_for(T0 + 60)), at=T0 + 60)
    with pytest.raises(A.AuthError):
        store.check_second_factor("u1", old[0], at=T0 + 61)
    assert store.check_second_factor("u1", new[0], at=T0 + 62) == "recovery"


def test_deleting_the_account_erases_the_second_factor(store):
    _turn_on(store)
    store.delete_account("u1")
    for table in ("second_factors", "recovery_codes", "security_events"):
        assert store._conn().execute(
            f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table


# --- the endpoints --------------------------------------------------------


@pytest.fixture
def outbox(monkeypatch):
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
    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "")
    monkeypatch.delenv("FAM_ADMIN_ACCOUNTS", raising=False)
    monkeypatch.delenv("FAM_ADMIN_REQUIRE_TWO_STEP", raising=False)
    return TestClient(appmod.app)


def _now_code(secret: str) -> str:
    return SF.code_at(secret, SF.step_for(time.time()))


def _sign_up_and_turn_on(c, email="ian@example.com"):
    assert c.post("/api/auth/signup", json={"email": email, "password": GOOD,
                                            "accept_terms": True}).status_code == 200
    setup = c.post("/api/auth/2fa/setup", json={"password": GOOD}).json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/FAM:")
    on = c.post("/api/auth/2fa/enable", json={"code": _now_code(setup["secret"])})
    assert on.status_code == 200, on.text
    body = on.json()
    assert body["enabled"] and len(body["recovery_codes"]) == 10
    return setup["secret"], body["recovery_codes"]


def test_set_up_is_for_accounts_only(client):
    assert client.post("/api/auth/2fa/setup").status_code in (401, 403)


def test_set_up_needs_the_password_not_just_the_session(client):
    client.post("/api/auth/signup", json={"email": "ian@example.com",
                                          "password": GOOD, "accept_terms": True})
    for body in (None, {}, {"password": "not-the-password"}):
        r = client.post("/api/auth/2fa/setup", json=body)
        assert r.status_code == 400 and "password" in r.json()["error"]
    assert client.get("/api/auth/2fa").json()["needs_password"] is True
    assert client.post("/api/auth/2fa/setup",
                       json={"password": GOOD}).status_code == 200


def test_an_account_without_a_password_needs_a_fresh_sign_in(store):
    store._conn().execute("UPDATE accounts SET password = '' WHERE user_id = 'u1'")
    token, _ = store.new_session("u1", at=T0)
    store.may_start_two_step("u1", "", token, at=T0 + 60)
    with pytest.raises(A.AuthError, match="sign in again"):
        store.may_start_two_step("u1", "", token,
                                 at=T0 + A.TWO_STEP_FRESH_SESSION_SECONDS + 1)


def test_asking_again_keeps_the_secret_already_in_the_app(store):
    first = store.begin_two_step("u1", at=T0)
    assert store.begin_two_step("u1", at=T0 + 60) == first
    later = T0 + A.TWO_STEP_SETUP_SECONDS + 1
    assert store.begin_two_step("u1", at=later) != first


def test_two_confirmations_at_once_turn_it_on_once(store):
    secret = store.begin_two_step("u1", at=T0)
    code = SF.code_at(secret, SF.step_for(T0))
    store.enable_two_step("u1", code, at=T0)
    with pytest.raises(A.AuthError, match="already on"):
        store.enable_two_step("u1", code, at=T0)


def test_a_right_password_alone_mints_no_session(client, outbox):
    secret, _ = _sign_up_and_turn_on(client)
    assert any("Two-step sign-in is on" in m[1] for m in outbox)
    assert client.post("/api/auth/logout").status_code == 200

    r = client.post("/api/auth/login", json={"email": "ian@example.com",
                                             "password": GOOD})
    assert r.status_code == 401
    body = r.json()
    assert body["error"] == appmod.SECOND_STEP_ASK
    assert body["second_factor"]["challenge"]
    assert not client.get("/api/auth/me").json()["authenticated"]

    wrong = client.post("/api/auth/2fa/verify", json={
        "challenge": body["second_factor"]["challenge"], "code": "000000"})
    assert wrong.status_code == 401
    assert not client.get("/api/auth/me").json()["authenticated"]

    # The step after the one used to enable it, so it is not a replay.
    code = SF.code_at(secret, SF.step_for(time.time()) + 1)
    ok = client.post("/api/auth/2fa/verify", json={
        "challenge": body["second_factor"]["challenge"], "code": code})
    assert ok.status_code == 200, ok.text
    assert ok.json()["second_factor_used"] == "app"
    assert client.get("/api/auth/me").json()["authenticated"]


def test_a_recovery_code_signs_in_and_the_owner_is_told(client, outbox):
    _, codes = _sign_up_and_turn_on(client)
    client.post("/api/auth/logout")
    challenge = client.post("/api/auth/login", json={
        "email": "ian@example.com", "password": GOOD}).json()["second_factor"]["challenge"]
    ok = client.post("/api/auth/2fa/verify", json={"challenge": challenge,
                                                   "code": codes[0]})
    assert ok.status_code == 200 and ok.json()["second_factor_used"] == "recovery"
    assert any("recovery code was used" in m[1] for m in outbox)
    assert client.get("/api/auth/2fa").json()["recovery_codes_left"] == 9


def test_a_reset_still_asks_for_the_code(client, outbox):
    _sign_up_and_turn_on(client)
    client.post("/api/auth/logout")
    client.post("/api/auth/reset/start", json={"email": "ian@example.com"})
    code = [m for m in outbox if m[1].endswith("is your FAM code")][-1][1].split()[0]
    r = client.post("/api/auth/reset/finish", json={
        "email": "ian@example.com", "code": code, "new": "another-good-password"})
    assert r.status_code == 401 and r.json()["second_factor"]["challenge"]
    assert not client.get("/api/auth/me").json()["authenticated"]


def test_turning_it_off_needs_a_code_not_just_the_session(client, outbox):
    secret, _ = _sign_up_and_turn_on(client)
    assert client.post("/api/auth/2fa/disable", json={"code": "000000"}).status_code == 400
    assert client.get("/api/auth/2fa").json()["enabled"]
    code = SF.code_at(secret, SF.step_for(time.time()) + 1)
    off = client.post("/api/auth/2fa/disable", json={"code": code})
    assert off.status_code == 200 and not off.json()["enabled"]
    assert any("Two-step sign-in is off" in m[1] for m in outbox)
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={
        "email": "ian@example.com", "password": GOOD}).status_code == 200


def test_the_status_never_returns_the_secret_or_codes(client):
    secret, codes = _sign_up_and_turn_on(client)
    text = client.get("/api/auth/2fa").text + client.get("/api/account").text
    assert secret not in text
    assert not any(code in text for code in codes)
    assert client.get("/api/account").json()["two_step"]["enabled"]


# --- admins -------------------------------------------------------------------


def test_an_admin_without_two_step_is_refused_at_admin(client, monkeypatch):
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", "boss@fam.test")
    client.post("/api/auth/signup", json={"email": "boss@fam.test", "password": GOOD,
                                          "accept_terms": True})
    r = client.post("/api/admin/login", json={"email": "boss@fam.test",
                                              "password": GOOD})
    assert r.status_code == 403 and "two-step" in r.json()["error"]
    assert "fam_admin" not in r.cookies
    assert client.get("/api/admin/tracker").status_code == 404
    # A wrong password is still the one refusal, saying nothing about admins.
    r = client.post("/api/admin/login", json={"email": "boss@fam.test",
                                              "password": "not-the-password"})
    assert r.status_code == 401


def test_an_admin_with_two_step_gets_in_with_the_code(client, monkeypatch):
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", "boss@fam.test")
    secret, _ = _sign_up_and_turn_on(client, "boss@fam.test")
    r = client.post("/api/admin/login", json={"email": "boss@fam.test",
                                              "password": GOOD})
    assert r.status_code == 401 and "fam_admin" not in r.cookies
    challenge = r.json()["second_factor"]["challenge"]
    # An app challenge does not open the dashboard, nor the reverse.
    assert client.post("/api/auth/2fa/verify", json={
        "challenge": challenge, "code": "000000"}).status_code == 401
    ok = client.post("/api/admin/login/verify", json={
        "challenge": challenge, "code": SF.code_at(secret, SF.step_for(time.time()) + 1)})
    assert ok.status_code == 200, ok.text
    assert client.get("/api/admin/tracker").status_code == 200
    kinds = [e["kind"] for e in client.get("/api/auth/2fa").json()["events"]]
    assert "admin_sign_in" in kinds


def test_an_app_session_never_opens_admin(client, monkeypatch):
    """An app session is minted by a password alone; the dashboard's must
    have come through its own sign-in. A token of one kind is never
    accepted as the other."""
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", "boss@fam.test")
    client.post("/api/auth/signup", json={"email": "boss@fam.test", "password": GOOD,
                                          "accept_terms": True})
    app_token = client.post("/api/auth/login", json={
        "email": "boss@fam.test", "password": GOOD,
        "want_token": True}).json()["session_token"]
    client.cookies.set("fam_admin", app_token)
    assert client.get("/api/admin/tracker").status_code == 404
    # Nor with the requirement lifted: the kinds stay apart regardless.
    monkeypatch.setenv("FAM_ADMIN_REQUIRE_TWO_STEP", "0")
    assert client.get("/api/admin/tracker").status_code == 404
    client.cookies.clear()
    admin = client.post("/api/admin/login", json={"email": "boss@fam.test",
                                                  "password": GOOD})
    assert admin.status_code == 200
    admin_token = client.cookies.get("fam_admin")
    assert appmod.ACCOUNTS.listener_for(admin_token, kind="admin") is not None
    # And the dashboard's token is no app session, by cookie or bearer.
    assert appmod.ACCOUNTS.listener_for(admin_token) is None
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
    assert not me.json()["authenticated"]


def test_an_admin_session_ends_when_the_requirement_is_not_met(client, monkeypatch):
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", "boss@fam.test")
    monkeypatch.setenv("FAM_ADMIN_REQUIRE_TWO_STEP", "0")
    client.post("/api/auth/signup", json={"email": "boss@fam.test", "password": GOOD,
                                          "accept_terms": True})
    assert client.post("/api/admin/login", json={
        "email": "boss@fam.test", "password": GOOD}).status_code == 200
    assert client.get("/api/admin/tracker").status_code == 200
    monkeypatch.setenv("FAM_ADMIN_REQUIRE_TWO_STEP", "1")
    assert client.get("/api/admin/tracker").status_code == 404


def test_an_admin_cannot_turn_it_off_while_it_is_required(client, monkeypatch):
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", "boss@fam.test")
    secret, _ = _sign_up_and_turn_on(client, "boss@fam.test")
    code = SF.code_at(secret, SF.step_for(time.time()) + 1)
    r = client.post("/api/auth/2fa/disable", json={"code": code})
    assert r.status_code == 400 and "Admin" in r.json()["error"]
    assert client.get("/api/auth/2fa").json()["enabled"]


def test_the_requirement_can_be_switched_off(client, monkeypatch):
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", "boss@fam.test")
    monkeypatch.setenv("FAM_ADMIN_REQUIRE_TWO_STEP", "0")
    client.post("/api/auth/signup", json={"email": "boss@fam.test", "password": GOOD,
                                          "accept_terms": True})
    assert client.post("/api/admin/login", json={
        "email": "boss@fam.test", "password": GOOD}).status_code == 200
