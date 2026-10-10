"""Email delivery: the one capability password reset was waiting for (§240).

Until this file the app had no route to deliver a message to anybody, so a
forgotten password on an email account was a lost account (ACCOUNTS.md said
so out loud). This is that route, and it does exactly one job: hand a short
plain-text message to an SMTP server.

The rules this file keeps:

* **SMTP, not a vendor's SDK.** Resend, Postmark, Amazon SES, Mailgun and a
  Gmail account all accept SMTP, so the provider is a dashboard choice and
  never a code change. Configured by `SMTP_HOST`, `SMTP_PORT` (587 STARTTLS
  by default, 465 implicit TLS), `SMTP_USERNAME`, `SMTP_PASSWORD` and
  `MAIL_FROM` - the address the message says it is from, which the provider
  must have verified. Nothing here invents a sender.
* **No delivery, and it says so.** `status()` is the only thing the
  interface branches on: without a host and a sender the "Forgot password?"
  control is not drawn at all (a control with nothing behind it is worse
  than no control), and `/api/auth/reset` returns the sentence why.
* **Staging never sends** (`spend_guard`): nothing leaves that machine
  anyway, and `status()` names it rather than letting every send fail.
* **A failed send is visible.** It is logged and counted, and the last
  error is in `/api/health` (`mail`), because a reset code that silently
  never arrives is indistinguishable, to the person waiting, from an address
  that has no account.
* **Sending never blocks the request that asked for it** (`send_later`). A
  reset request answers in the same time whether or not the address has an
  account; waiting on SMTP only for the real ones would tell anybody with a
  stopwatch which addresses are registered.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

log = logging.getLogger("fam.mail")

#: How long one SMTP conversation may take before it is given up on.
TIMEOUT_SECONDS = 15.0

_lock = threading.Lock()
_counts = {"sent": 0, "failed": 0}
_last_error = {"at": 0.0, "error": ""}


def _settings():
    from config import settings
    return settings


def _password() -> str:
    """Fetched like every other credential (env > FAM_SECRETS > .env), never
    typed into source."""
    import credentials
    return credentials.active("SMTP_PASSWORD") or ""


def status() -> dict:
    """Whether this server can deliver an email, and if not, why."""
    import spend_guard
    s = _settings()
    if spend_guard.enabled():
        return {"available": False,
                "reason": "This is the staging server; it sends no email."}
    if not s.smtp_host:
        return {"available": False,
                "reason": "Email is not set up on this server yet (no SMTP_HOST)."}
    sender = parseaddr(s.mail_from or "")[1]
    if "@" not in sender:
        return {"available": False,
                "reason": "Email is not set up on this server yet "
                          "(MAIL_FROM is not an address)."}
    return {"available": True, "reason": ""}


def report() -> dict:
    """For `/api/health`: whether it can send, and how sending has gone."""
    with _lock:
        return {**status(), **_counts,
                "last_error": _last_error["error"],
                "last_error_at": _last_error["at"]}


def _message(to: str, subject: str, body: str) -> EmailMessage:
    s = _settings()
    name, address = parseaddr(s.mail_from)
    msg = EmailMessage()
    msg["From"] = formataddr((name or "FAM", address))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def send(to: str, subject: str, body: str) -> bool:
    """Deliver one message now. Never raises; returns whether it was accepted."""
    if not status()["available"]:
        log.warning("mail not sent to a listener: %s", status()["reason"])
        return False
    s = _settings()
    port = int(s.smtp_port or 587)
    try:
        msg = _message(to, subject, body)
        context = ssl.create_default_context()
        if port == 465:
            server = smtplib.SMTP_SSL(s.smtp_host, port, timeout=TIMEOUT_SECONDS,
                                      context=context)
        else:
            server = smtplib.SMTP(s.smtp_host, port, timeout=TIMEOUT_SECONDS)
        with server:
            if port != 465:
                server.starttls(context=context)
            if s.smtp_username:
                server.login(s.smtp_username, _password())
            server.send_message(msg)
    except Exception as exc:  # smtplib raises half a dozen kinds
        with _lock:
            _counts["failed"] += 1
            # The class and the provider's words, never the message or the
            # address it was going to.
            _last_error.update(at=time.time(),
                               error=f"{type(exc).__name__}: {exc}"[:300])
        log.error("mail send failed: %s", _last_error["error"])
        return False
    with _lock:
        _counts["sent"] += 1
    return True


def send_later(to: str, subject: str, body: str) -> None:
    """`send` on its own thread, so the caller answers in constant time."""
    threading.Thread(target=send, args=(to, subject, body),
                     name="fam-mail", daemon=True).start()


def reset_counts() -> None:
    """For tests."""
    with _lock:
        _counts.update(sent=0, failed=0)
        _last_error.update(at=0.0, error="")
