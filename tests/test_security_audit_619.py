"""The security audit of 2026-11-16 (PROBLEMS.md §619): what it found in
sign-in and sessions, each pinned as the behaviour that closes it.

Every test here failed against the code as the audit found it - that failure
was the reproduction - and passes against the fix.

1. **Guessing a password was not paced at all.** `_rate_limit` keys on the
   session, and a request with no cookie is minted a fresh session, so a
   script that simply never sends a cookie got a new, full bucket every time:
   unlimited tries at any account's password, the admin dashboard's included.
   Wrong tries are now counted against the *address being tried*, whoever
   sends them.
2. **Both cookies went out without `Secure` on the HTTPS site.** Behind
   Render's router the connection the app sees is plain HTTP, so
   `request.url.scheme` never said https.
3. **The reset email's link was whatever host the request named.** A stranger
   could have FAM email somebody a real code beside a link to the stranger's
   own page.
4. **An admin address with no account could be claimed by anybody**, because
   an address is never verified. The server now says so out loud.
5. **Changing an account's email told nobody.** It asks for no password, and
   the next reset code goes to the new address; the old one is now told.
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
EMAIL = "ian@example.com"


@pytest.fixture
def outbox(monkeypatch):
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(mail, "status", lambda: {"available": True, "reason": ""})
    monkeypatch.setattr(mail, "send_later",
                        lambda to, subject, body: sent.append((to, subject, body)))
    return sent


@pytest.fixture
def server(monkeypatch, tmp_path, outbox):
    """The real limiter, an accounts store of its own, and nothing remembered
    from another test."""
    monkeypatch.setattr(appmod, "ACCOUNTS", A.AccountStore(str(tmp_path / "a.db")))
    appmod._read_hits.clear()
    appmod._gen_tokens.clear()
    appmod._auth_failures.clear()
    appmod.ACCOUNTS.sign_up("u1", EMAIL, GOOD)
    return appmod.app


def _stranger(server) -> TestClient:
    """A client that has never been here: no cookie, so a fresh session."""
    return TestClient(server)


def _login(client, password, email=EMAIL):
    return client.post("/api/auth/login", json={"email": email, "password": password})


# --- 1. guessing a password ---------------------------------------------------


def test_wrong_passwords_are_counted_against_the_address_whoever_sends_them(server):
    """Twenty guesses, each from a client with no cookie. Before: twenty 401s,
    and the twenty-first could have been the right one."""
    answers = [_login(_stranger(server), f"wrong-guess-{n}").status_code
               for n in range(20)]
    assert answers[:appmod.AUTH_FREE_FAILURES] == [401] * appmod.AUTH_FREE_FAILURES
    assert 429 in answers, "nothing ever told a guesser to stop"
    assert set(answers[appmod.AUTH_FREE_FAILURES:]) == {429}


def test_the_right_password_does_not_get_through_a_lock(server):
    """Or the lock would slow nothing: the guess that is right would pass."""
    for n in range(appmod.AUTH_FREE_FAILURES):
        assert _login(_stranger(server), f"wrong-guess-{n}").status_code == 401
    refused = _login(_stranger(server), GOOD)
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) >= 1
    assert "fam_session" not in refused.headers.get("set-cookie", "") or \
        refused.json().get("authenticated") is not True


def test_a_lock_reads_the_same_for_an_address_with_no_account(server):
    """The refusal must not say which addresses are registered."""
    def lock(email):
        for n in range(appmod.AUTH_FREE_FAILURES):
            _login(_stranger(server), f"wrong-guess-{n}", email)
        return _login(_stranger(server), "one-more-guess", email)

    known, unknown = lock(EMAIL), lock("nobody@example.com")
    assert (known.status_code, unknown.status_code) == (429, 429)
    assert known.json()["error"] == unknown.json()["error"]


def test_a_few_slips_do_not_lock_anybody_out(server):
    client = _stranger(server)
    for n in range(appmod.AUTH_FREE_FAILURES - 1):
        assert _login(client, f"typo-{n}").status_code == 401
        appmod._gen_tokens.clear()   # the session's own pace is not the subject
    assert _login(client, GOOD).status_code == 200
    # And the count starts again after getting it right.
    assert appmod._auth_failures == {}


def test_a_lock_ends(server, monkeypatch):
    for n in range(appmod.AUTH_FREE_FAILURES):
        _login(_stranger(server), f"wrong-guess-{n}")
    assert _login(_stranger(server), GOOD).status_code == 429
    real = appmod.time.monotonic
    monkeypatch.setattr(appmod, "_auth_clock",
                        lambda: real() + appmod.AUTH_LOCK_SECONDS + 1)
    assert _login(_stranger(server), GOOD).status_code == 200


def test_each_wrong_try_after_the_lock_waits_longer(server, monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(appmod, "_auth_clock", lambda: clock["now"])
    for n in range(appmod.AUTH_FREE_FAILURES):
        _login(_stranger(server), f"wrong-guess-{n}")
    waits = []
    for n in range(8):
        waits.append(appmod._auth_failures[appmod._auth_key("email", EMAIL)][2]
                     - clock["now"])
        clock["now"] += waits[-1] + 1
        assert _login(_stranger(server), f"another-{n}").status_code == 401
    assert waits[0] == appmod.AUTH_LOCK_SECONDS
    assert waits == sorted(waits) and waits[1] > waits[0]
    assert max(waits) <= appmod.AUTH_LOCK_MAX_SECONDS


def test_the_account_itself_is_not_locked_out_of_its_own_devices(server):
    """A lock is something a stranger can cause, so it must not be a way to
    keep the owner out: a browser already signed in to the account (a reset
    code signs one in) passes with the right password."""
    owner = _stranger(server)
    assert _login(owner, GOOD).status_code == 200
    for n in range(appmod.AUTH_FREE_FAILURES + 2):
        _login(_stranger(server), f"wrong-guess-{n}")
    assert _login(_stranger(server), GOOD).status_code == 429
    assert _login(owner, GOOD).status_code == 200


def test_the_admin_sign_in_is_counted_the_same_way(server, monkeypatch):
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", EMAIL)
    answers = [_stranger(server).post("/api/admin/login",
                                      json={"email": EMAIL, "password": f"guess-{n}"}).status_code
               for n in range(appmod.AUTH_FREE_FAILURES + 3)]
    assert answers[:appmod.AUTH_FREE_FAILURES] == [401] * appmod.AUTH_FREE_FAILURES
    assert set(answers[appmod.AUTH_FREE_FAILURES:]) == {429}
    # One count for both doors: the app's sign-in is locked too.
    assert _login(_stranger(server), GOOD).status_code == 429


def test_a_phone_sign_in_is_counted_too(server):
    appmod.ACCOUNTS.sign_up_phone("u2", "+15551234567", GOOD)

    def phone(password):
        return _stranger(server).post("/api/auth/login", json={
            "phone": "+1 (555) 123-4567", "password": password})

    for n in range(appmod.AUTH_FREE_FAILURES):
        assert phone(f"wrong-guess-{n}").status_code == 401
    assert phone(GOOD).status_code == 429


# --- 2. the cookies -----------------------------------------------------------


def test_the_session_cookie_is_secure_behind_the_router(server):
    """Render's router speaks plain HTTP to the app and says the listener's
    side was https in `X-Forwarded-Proto`."""
    answer = _stranger(server).get("/api/auth/me",
                                   headers={"X-Forwarded-Proto": "https"})
    cookie = answer.headers["set-cookie"]
    assert "fam_session=" in cookie
    assert "secure" in cookie.lower()
    assert "httponly" in cookie.lower()


def test_the_admin_cookie_is_secure_behind_the_router(server, monkeypatch):
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", EMAIL)
    answer = _stranger(server).post("/api/admin/login",
                                    json={"email": EMAIL, "password": GOOD},
                                    headers={"X-Forwarded-Proto": "https"})
    assert answer.status_code == 200
    admin = [c for c in answer.headers.get_list("set-cookie") if "fam_admin=" in c]
    assert admin and "secure" in admin[0].lower()


def test_a_plain_http_address_still_gets_a_cookie_it_can_send_back(server):
    """The LAN address a phone tests on is http, and `Secure` there would
    drop the cookie."""
    cookie = _stranger(server).get("/api/auth/me").headers["set-cookie"]
    assert "secure" not in cookie.lower()


# --- 3. the reset email -------------------------------------------------------


def test_the_reset_email_never_links_where_the_request_says(server, outbox, monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    answer = _stranger(server).post(
        "/api/auth/reset/start", json={"email": EMAIL},
        headers={"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https"})
    assert answer.status_code == 200
    body = outbox[-1][2]
    assert "evil.example" not in body
    assert f"{appmod.settings.app_home_url}/reset" in body


def test_the_reset_email_ignores_a_forged_host_header_too(server, outbox, monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    _stranger(server).post("/api/auth/reset/start", json={"email": EMAIL},
                           headers={"Host": "evil.example"})
    assert "evil.example" not in outbox[-1][2]


# --- 4. an admin address nobody holds ----------------------------------------


def test_an_admin_address_with_no_account_is_reported(server, monkeypatch):
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", f"{EMAIL}, boss@example.com, +15550001111")
    assert appmod._unclaimed_admin_entries() == 2


def test_no_admin_entries_unclaimed_when_every_one_has_an_account(server, monkeypatch):
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", f"{EMAIL}, u1")
    assert appmod._unclaimed_admin_entries() == 0


def test_the_admin_page_says_when_an_admin_address_is_unclaimed(server, monkeypatch):
    """Where an admin will see it: the dashboard's own sign-in answer."""
    monkeypatch.setenv("FAM_ADMIN_ACCOUNTS", f"{EMAIL}, boss@example.com")
    answer = _stranger(server).post("/api/admin/login",
                                    json={"email": EMAIL, "password": GOOD})
    assert answer.status_code == 200
    assert answer.json()["unclaimed_admin_entries"] == 1
    assert "boss@example.com" not in answer.text


# --- 5. the address an account leaves ----------------------------------------


def test_the_old_address_is_told_when_the_email_changes(server, outbox):
    client = _stranger(server)
    assert _login(client, GOOD).status_code == 200
    appmod._gen_tokens.clear()
    changed = client.post("/api/account", json={"email": "new@example.com"})
    assert changed.status_code == 200 and changed.json()["email"] == "new@example.com"
    told = [m for m in outbox if m[0] == EMAIL]
    assert len(told) == 1
    assert "changed" in told[0][1]
    assert "new@example.com" in told[0][2]


def test_nothing_is_sent_when_the_email_did_not_change(server, outbox):
    client = _stranger(server)
    assert _login(client, GOOD).status_code == 200
    appmod._gen_tokens.clear()
    assert client.post("/api/account", json={"display_name": "Ian"}).status_code == 200
    assert client.post("/api/account", json={"email": EMAIL}).status_code == 200
    assert outbox == []
