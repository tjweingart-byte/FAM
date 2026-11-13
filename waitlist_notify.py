"""Telling somebody they are off the waitlist (§587).

Letting a person in used to change one column and tell Viral Loops; the
person found out only by trying to sign in. Now `Waitlist.grant` queues a
notice per channel the account has - `notify_email`, `notify_sms` - in the
waitlist outbox, and this drains them: an email to the address, a text to
the phone, each carrying `/claim/<code>`, the link that claims the free
month.

* **The month starts when the real app is published**, never in the beta:
  claiming stamps the same `free_month_earned_at` five friends earn, and the
  month runs from the admin-set launch day (`free_month_starts`). The words
  say so, and say the dates once they are set.
* **Never lost:** a send that fails (or a server with no mail or texting set
  up yet) is retried on the outbox's delays and stays counted on /admin.
* **Nothing about the account but its own name** goes in a message.
"""
from __future__ import annotations

import logging
import threading
import time

import mail
import sms
import waitlist as waitlist_mod

log = logging.getLogger("fam.waitlist_notify")

SUBJECT = "You're off the FAM waitlist"

#: One drain at a time in this process: the drain kicked by a grant and the
#: five-minute timer would otherwise read the same due rows and both send.
_drain_lock = threading.Lock()


def claim_link(base: str, code: str) -> str:
    return f"{base.rstrip('/')}/claim/{code}"


def _when(starts: float) -> str:
    if not starts:
        return "It starts the day the full FAM app is published."
    t = time.gmtime(starts)
    day = f"{time.strftime('%B', t)} {t.tm_mday}, {t.tm_year}"
    return f"It starts on {day}, the day the full FAM app is published."


def email_body(name: str, link: str, home: str, starts: float) -> str:
    hello = f"Hi {name}," if name else "Hi,"
    return (f"{hello}\n\n"
            "You're off the waitlist - FAM is open to you now. Sign in the "
            f"way you joined:\n{home}\n\n"
            "You also get a free month of FAM Plus. Claim it here:\n"
            f"{link}\n\n"
            f"{_when(starts)} Nothing to pay, and nothing is billed after.\n\n"
            "- FAM\n")


def sms_body(link: str, starts: float) -> str:
    """One SMS segment (160 GSM characters) with today's link length."""
    t = time.gmtime(starts)
    when = "from launch day" if not starts else \
        f"from {time.strftime('%b', t)} {t.tm_mday}"
    return (f"FAM: you're off the waitlist! Claim a free month of Plus "
            f"({when}): {link} Reply STOP to opt out.")


def drain(waitlist, base: str, home: str, limit: int = 50) -> dict:
    """Send whatever notices are due. Never raises. Blocking (SMTP, HTTP):
    callers run it in a thread. A drain already running in this process
    is not joined: whatever it leaves is the next one's."""
    result = {"sent": 0, "failed": 0, "skipped": 0}
    if not _drain_lock.acquire(blocking=False):
        return result
    try:
        return _drain(waitlist, base, home, limit, result)
    finally:
        _drain_lock.release()


def _drain(waitlist, base: str, home: str, limit: int, result: dict) -> dict:
    starts = waitlist.free_month_starts()
    for item in waitlist.due(limit=limit, actions=waitlist_mod.NOTIFY_ACTIONS):
        try:
            view = waitlist.notice_view(item["user_id"])
            if view is None or view["status"] != waitlist_mod.ACTIVE:
                waitlist.mark_done(item["id"])  # deleted, or not in after all
                result["skipped"] += 1
                continue
            code = view["claim_code"] or waitlist.ensure_claim_code(item["user_id"])
            link = claim_link(base, code)
            if item["action"] == waitlist_mod.NOTIFY_EMAIL:
                if not view["email"]:
                    waitlist.mark_done(item["id"])
                    result["skipped"] += 1
                    continue
                ok = mail.send(view["email"], SUBJECT,
                               email_body(view["name"], link, home, starts))
                why = mail.status()["reason"] or "the mail server refused it"
            else:
                if not view["phone"]:
                    waitlist.mark_done(item["id"])
                    result["skipped"] += 1
                    continue
                # One text, ever (§595): taken on the account before it is
                # sent, so a retry, a second process or a second grant finds
                # it gone. Given back only when it certainly did not go.
                if not waitlist.take_text(item["user_id"]):
                    waitlist.mark_done(item["id"])
                    result["skipped"] += 1
                    continue
                outcome = sms.send(view["phone"], sms_body(link, starts))
                if outcome is True or outcome == sms.SENT:
                    ok = True
                elif outcome == sms.UNKNOWN:
                    # Perhaps delivered: never risk a second one.
                    waitlist.mark_given_up(
                        item["id"], "no answer from Twilio; not retried, so "
                        "it is never sent twice")
                    result["failed"] += 1
                    continue
                else:
                    waitlist.give_text_back(item["user_id"])
                    ok = False
                why = sms.status()["reason"] or "Twilio refused it"
            if ok:
                waitlist.mark_done(item["id"])
                result["sent"] += 1
            else:
                waitlist.mark_failed(item["id"], why)
                result["failed"] += 1
        except Exception as exc:  # noqa: BLE001 - one notice never stops the rest
            log.exception("waitlist notice %s failed", item["action"])
            waitlist.mark_failed(item["id"], f"{type(exc).__name__}: {exc}")
            result["failed"] += 1
    return result
