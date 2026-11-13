"""Text messages: the one route FAM has to a phone number (§587).

Until this file nothing could text anybody; a phone number on an account was
a sign-in name and nothing more (and is still not verified). This does one
job, the way `mail.py` does for email: hand one short message to Twilio.

The rules it keeps are mail's:

* **Twilio's REST API over plain HTTPS**, no SDK. `TWILIO_ACCOUNT_SID` and
  `TWILIO_FROM` (a number in +E.164 form, or a Messaging Service id starting
  `MG`) are settings; `TWILIO_AUTH_TOKEN` is a credential, read through
  `credentials`, never from source.
* **No delivery, and it says so.** `status()` names what is missing, and
  `/api/health` (`sms`) carries it with the last error.
* **Staging never sends** (`spend_guard`): Twilio bills per message.
* **A failed send is visible**, counted, its words kept without the number.
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("fam.sms")

TIMEOUT_SECONDS = 15.0
API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"

_lock = threading.Lock()
_counts = {"sent": 0, "failed": 0}
_last_error = {"at": 0.0, "error": ""}


def _settings():
    from config import settings
    return settings


def _token() -> str:
    import credentials
    return credentials.active("TWILIO_AUTH_TOKEN") or ""


def status() -> dict:
    """Whether this server can send a text, and if not, why."""
    import spend_guard
    s = _settings()
    if spend_guard.enabled():
        return {"available": False,
                "reason": "This is the staging server; it sends no texts."}
    if not s.twilio_account_sid or not s.twilio_from:
        return {"available": False,
                "reason": "Texting is not set up on this server yet "
                          "(no TWILIO_ACCOUNT_SID or TWILIO_FROM)."}
    if not _token():
        return {"available": False,
                "reason": "Texting is not set up on this server yet "
                          "(no TWILIO_AUTH_TOKEN)."}
    return {"available": True, "reason": ""}


def report() -> dict:
    with _lock:
        return {**status(), **_counts,
                "last_error": _last_error["error"],
                "last_error_at": _last_error["at"]}


def _fail(words: str) -> bool:
    with _lock:
        _counts["failed"] += 1
        _last_error.update(at=time.time(), error=words[:300])
    log.error("sms send failed: %s", _last_error["error"])
    return False


def send(to: str, body: str) -> bool:
    """Deliver one text now. Never raises; returns whether Twilio took it."""
    if not status()["available"]:
        log.warning("sms not sent to a listener: %s", status()["reason"])
        return False
    import httpx
    s = _settings()
    sender = s.twilio_from
    form = {"To": to, "Body": body}
    form["MessagingServiceSid" if sender.startswith("MG") else "From"] = sender
    try:
        r = httpx.post(API.format(sid=s.twilio_account_sid), data=form,
                       auth=(s.twilio_account_sid, _token()),
                       timeout=TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - a vendor must never break us
        return _fail(f"{type(exc).__name__}: {exc}".replace(to, "<recipient>"))
    if r.status_code >= 300:
        try:
            said = r.json().get("message") or r.text
        except ValueError:
            said = r.text
        return _fail(f"HTTP {r.status_code}: {said}".replace(to, "<recipient>"))
    with _lock:
        _counts["sent"] += 1
    return True


def reset_counts() -> None:
    """For tests."""
    with _lock:
        _counts.update(sent=0, failed=0)
        _last_error.update(at=0.0, error="")
