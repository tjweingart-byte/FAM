"""Sign in with Apple, the server half: keeping the link so it can be cut.

`oauth.py` verifies an identity token and needs no secret, and that is still
how somebody signs in. This file exists for the one thing verification cannot
do. App Store guideline 5.1.1(v): an app that offers Sign in with Apple must
**revoke the person's Apple tokens when they delete their account**, through
Apple's REST API - deleting our own rows is not enough, because the app stays
listed under "Apps using Apple ID" in their settings as if nothing happened.

Revoking needs a token to revoke, and the identity token is not one. So:

1. At sign-in the iOS app also sends the **authorization code** Apple handed
   it (`ProviderRequest.authorization_code`). This exchanges the code once,
   at `appleid.apple.com/auth/token`, for a **refresh token**, and
   `accounts.py` keeps it beside the identity.
2. When the account is deleted (`erase_listener`) or the Apple link removed
   (`/api/account/identity`), the refresh token is sent to
   `appleid.apple.com/auth/revoke`.

Both calls are signed with a **client secret** - a short-lived ES256 JWT made
from the team's Sign in with Apple key (`APPLE_KEY_ID`, `APPLE_PRIVATE_KEY`,
`APPLE_TEAM_ID`). It is made here, on the server, for each call: the secret
`oauth.py` warns about shipping inside an app is never in the app. A stored
refresh token is useless without that key, which is why keeping it in the
accounts store beside the password hashes is acceptable.

The rules:

* **A layer that adds must not subtract.** A failed exchange never fails a
  sign-in, and a failed revoke never stops a deletion - the person's right to
  leave does not wait on Apple. Both are logged and counted in `/api/health`
  (`apple_tokens`), because failures must be visible.
* **A revoke is tried three times, off the request** (`revoke_later`), and the
  token is gone from our side either way: the account is.
* **Staging sends nothing** (`spend_guard`), like everything that leaves the
  machine, and says so.
* **Unconfigured says so.** No key, and `report()` names the missing setting;
  sign-in still works, and deletion still deletes our side.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Iterable, Optional

log = logging.getLogger("fam.apple_tokens")

AUDIENCE = "https://appleid.apple.com"
TOKEN_URL = "https://appleid.apple.com/auth/token"
REVOKE_URL = "https://appleid.apple.com/auth/revoke"

#: One call's wait. Sign-in waits on the exchange, so it is short.
TIMEOUT_SECONDS = 10.0
#: How long a client secret lives. Apple allows six months; a fresh one per
#: call means nothing long-lived is ever minted.
SECRET_SECONDS = 300
#: Revoke attempts and the pause before each retry.
REVOKE_ATTEMPTS = 3
RETRY_PAUSES = (2.0, 8.0)

_lock = threading.Lock()
_counts = {"exchanged": 0, "exchange_failed": 0,
           "revoked": 0, "revoke_failed": 0}
_last_error = {"at": 0.0, "error": ""}


def _settings():
    from config import settings
    return settings


def _private_key() -> str:
    """The .p8 key's PEM. Fetched like every other credential; an environment
    variable that holds literal `\\n` (how most dashboards store a PEM) is
    turned back into lines."""
    import credentials
    raw = credentials.active("APPLE_PRIVATE_KEY") or ""
    return raw.replace("\\n", "\n").strip()


def status() -> dict:
    """Whether this server can exchange and revoke Apple tokens, and why not."""
    import spend_guard
    s = _settings()
    if spend_guard.enabled():
        return {"available": False,
                "reason": "This is the staging server; it calls nothing outside."}
    missing = [name for name, value in (
        ("APPLE_TEAM_ID", s.apple_team_id), ("APPLE_KEY_ID", s.apple_key_id),
        ("APPLE_PRIVATE_KEY", _private_key())) if not value]
    if missing:
        return {"available": False,
                "reason": "%s not set, so a deleted account's Apple sign-in is "
                          "not revoked (App Store 5.1.1(v))." % ", ".join(missing)}
    # The key is set but cannot be used without PyJWT: the same refusal, in
    # the same words, as `oauth.available` (requirements-oauth.txt).
    try:
        import oauth
        oauth._library()
    except Exception as exc:  # noqa: BLE001 - OAuthUnavailable, said as is
        return {"available": False, "reason": str(exc)}
    return {"available": True, "reason": ""}


def report() -> dict:
    """For `/api/health`."""
    with _lock:
        return {**status(), **_counts, "last_error": _last_error["error"],
                "last_error_at": _last_error["at"]}


def _failed(kind: str, words: str) -> None:
    with _lock:
        _counts[kind] += 1
        _last_error.update(at=time.time(), error=words[:300])
    log.error("apple %s: %s", kind.replace("_", " "), words[:300])


def client_secret(client_id: str, now: Optional[float] = None) -> str:
    """The ES256 JWT Apple wants as `client_secret` for this client id."""
    import jwt  # noqa: PLC0415 - lazy, as in oauth.py
    s = _settings()
    issued = int(now or time.time())
    return jwt.encode(
        {"iss": s.apple_team_id, "iat": issued, "exp": issued + SECRET_SECONDS,
         "aud": AUDIENCE, "sub": client_id},
        _private_key(), algorithm="ES256", headers={"kid": s.apple_key_id})


def _post(url: str, data: dict):
    import httpx  # noqa: PLC0415
    return httpx.post(url, data=data, timeout=TIMEOUT_SECONDS,
                      headers={"Accept": "application/json"})


def exchange(code: str, client_id: str) -> str:
    """Trade an authorization code for a refresh token. "" when it cannot.

    Never raises: a sign-in that verified must not fail here.
    """
    code = (code or "").strip()
    if not code or not client_id or not status()["available"]:
        return ""
    try:
        resp = _post(TOKEN_URL, {
            "client_id": client_id, "client_secret": client_secret(client_id),
            "code": code, "grant_type": "authorization_code"})
        if resp.status_code != 200:
            # Apple's error is a word ("invalid_grant"), never the code.
            try:
                why = str(resp.json().get("error") or "")
            except ValueError:
                why = ""
            _failed("exchange_failed", "HTTP %s %s" % (resp.status_code, why))
            return ""
        token = str(resp.json().get("refresh_token") or "")
    except Exception as exc:  # noqa: BLE001 - network, JSON or key trouble
        _failed("exchange_failed", type(exc).__name__)
        return ""
    if not token:
        _failed("exchange_failed", "no refresh_token in Apple's answer")
        return ""
    with _lock:
        _counts["exchanged"] += 1
    return token


def revoke(token: str, client_id: str) -> bool:
    """Revoke one refresh token now. Never raises."""
    if not token or not client_id or not status()["available"]:
        return False
    try:
        resp = _post(REVOKE_URL, {
            "client_id": client_id, "client_secret": client_secret(client_id),
            "token": token, "token_type_hint": "refresh_token"})
    except Exception as exc:  # noqa: BLE001
        _failed("revoke_failed", type(exc).__name__)
        return False
    if resp.status_code != 200:
        _failed("revoke_failed", "HTTP %s" % resp.status_code)
        return False
    with _lock:
        _counts["revoked"] += 1
    return True


def _revoke_with_retries(tokens: list[tuple[str, str]], pause=time.sleep) -> int:
    done = 0
    for token, client_id in tokens:
        for attempt in range(REVOKE_ATTEMPTS):
            if revoke(token, client_id):
                done += 1
                break
            if attempt < len(RETRY_PAUSES):
                pause(RETRY_PAUSES[attempt])
    return done


def revoke_later(tokens: Iterable[tuple[str, str]]) -> int:
    """Revoke (token, client_id) pairs on their own thread. Returns how many
    were handed over, so the caller can say so without waiting on Apple."""
    tokens = [(t, c) for t, c in tokens if t and c]
    if not tokens:
        return 0
    if not status()["available"]:
        _failed("revoke_failed",
                "%d token(s) not revoked: %s" % (len(tokens), status()["reason"]))
        return 0
    threading.Thread(target=_revoke_with_retries, args=(tokens,),
                     name="fam-apple-revoke", daemon=True).start()
    return len(tokens)


def reset_counts() -> None:
    """For tests."""
    with _lock:
        for key in _counts:
            _counts[key] = 0
        _last_error.update(at=0.0, error="")
