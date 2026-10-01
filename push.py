"""DailyFAM's "your mix is ready" notification (the 10.1 packet, third set).

A listener can give a mix a **listen time** - when they mean to hear it. That
time changes nothing about when the mix's episodes are written: the DailyFAM
edition writes them at 05:00 Eastern for everybody (§143, `daily_edition.py`).
It decides one thing only: when this server tells the listener's phone that
the mix is ready.

The rules this file keeps:

* **Ready means written.** A notification goes out at or after the listen time
  and only once the edition it would play is built (`daily_edition` reports
  the slot `ready`). A listen time before the edition (a 4am mix) waits for it,
  up to `GRACE_SECONDS`; an edition that failed sends nothing, because "your
  mix is ready" would be false.
* **Once a day per mix**, by the listener's own calendar day in their own zone
  (`sent`, keyed by mix and local date) - not the server's.
* **Delivery is Web Push** (a service worker's `push` event, `static/sw.js`).
  It needs a VAPID key pair (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`) and the
  optional `pywebpush` package (`requirements-push.txt`). Without either,
  `status()` says which, `/api/push` repeats it, and the listen-time row under
  a mix says notifications are not switched on - the time is still kept, so
  nothing has to be set again once they are. Like password reset with no mail
  delivery (`ACCOUNTS.md`): no delivery, and it says so.
* **The native app is the next client.** `subscriptions.kind` is `webpush`
  today; an APNs device token is another kind, sent by a different function,
  read from the same table and the same due list.
* Staging never sends (`spend_guard`): nothing leaves that machine anyway, and
  `status()` names it rather than letting every send fail.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime
from typing import Optional

from paths import data_path

log = logging.getLogger("fam.push")

#: How long past the listen time a "ready" notification is still worth
#: sending. Covers a listen time set before the 05:00 edition finishes, and a
#: server that was restarting at the minute; past it, the listener has either
#: listened or moved on, and a late "ready" is noise.
GRACE_SECONDS = 3 * 3600
#: How often the loop looks for due mixes. A notification lands within this
#: much of the minute chosen.
TICK_SECONDS = 60
#: Push services keep an undelivered message this long (phone off, say).
PUSH_TTL_SECONDS = 2 * 3600
WEBPUSH = "webpush"


def clean_listen_at(value) -> str:
    """'HH:MM' on a 24-hour clock, or '' for no listen time.

    Raises ValueError with a sentence for the listener on anything else."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        hour, minute = text.split(":")[:2]
        h, m = int(hour), int(minute)
    except ValueError:
        raise ValueError("That listen time could not be read.") from None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("That listen time could not be read.")
    return f"{h:02d}:{m:02d}"


def clean_zone(value) -> str:
    """An IANA zone name the server can read, or '' (the edition's zone)."""
    text = str(value or "").strip()[:64]
    if not text:
        return ""
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(text)
    except Exception:
        return ""
    return text


def _zone(name: str):
    from zoneinfo import ZoneInfo
    import daily_edition
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return daily_edition.zone()


def _settings():
    from config import settings
    return settings


def status() -> dict:
    """Whether this server can deliver a notification, and if not, why.

    `available` is the only thing the interface branches on; `reason` is the
    sentence it shows."""
    import spend_guard
    s = _settings()
    if spend_guard.enabled():
        return {"available": False, "public_key": "",
                "reason": "This is the staging server; it sends no notifications."}
    if not getattr(s, "mix_reminders", True):
        return {"available": False, "public_key": "",
                "reason": "Notifications are switched off on this server (MIX_REMINDERS=0)."}
    if not (s.vapid_public_key and s.vapid_private_key):
        return {"available": False, "public_key": "",
                "reason": "Notifications are not set up on this server yet "
                          "(no VAPID keys). Your listen time is kept for when they are."}
    try:
        import pywebpush  # noqa: F401
    except ImportError:
        return {"available": False, "public_key": "",
                "reason": "Notifications are not set up on this server yet "
                          "(pywebpush is not installed). Your listen time is kept for when they are."}
    return {"available": True, "public_key": s.vapid_public_key, "reason": ""}


class PushStore:
    """Where each listener's phone can be reached, and what was sent.

    Beside the mixes in `mixes.db`: a subscription exists to deliver a mix's
    notification, and keeping it there puts it on the same disk with no new
    database to declare."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = data_path("MIXES_DB", "mixes.db", path)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS push_subscriptions (
                endpoint TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'webpush', keys TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS push_user "
                       "ON push_subscriptions(user_id)")
            db.execute("""CREATE TABLE IF NOT EXISTS push_sent (
                mix_id TEXT NOT NULL, day TEXT NOT NULL, sent_at REAL NOT NULL,
                delivered INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (mix_id, day))""")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5.0)

    def subscribe(self, user_id: str, subscription: dict, kind: str = WEBPUSH) -> None:
        endpoint = str((subscription or {}).get("endpoint") or "").strip()
        keys = (subscription or {}).get("keys") or {}
        if not user_id:
            raise ValueError("No listener id.")
        if not endpoint.startswith("https://") or len(endpoint) > 2048:
            raise ValueError("That subscription could not be read.")
        if kind == WEBPUSH and not (keys.get("p256dh") and keys.get("auth")):
            raise ValueError("That subscription could not be read.")
        with self._lock, self._connect() as db:
            # One endpoint is one browser: signing in as somebody else on the
            # same phone moves it, rather than notifying two people.
            db.execute("INSERT OR REPLACE INTO push_subscriptions "
                       "(endpoint, user_id, kind, keys, created_at) VALUES (?, ?, ?, ?, ?)",
                       (endpoint, user_id, kind,
                        json.dumps({"p256dh": str(keys.get("p256dh", "")),
                                    "auth": str(keys.get("auth", ""))}),
                        time.time()))

    def unsubscribe(self, user_id: str, endpoint: str = "") -> int:
        with self._lock, self._connect() as db:
            if endpoint:
                cur = db.execute("DELETE FROM push_subscriptions WHERE user_id = ? "
                                 "AND endpoint = ?", (user_id, endpoint))
            else:
                cur = db.execute("DELETE FROM push_subscriptions WHERE user_id = ?",
                                 (user_id,))
            return cur.rowcount

    def drop_endpoint(self, endpoint: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))

    def subscriptions(self, user_id: str) -> list:
        with self._connect() as db:
            rows = db.execute("SELECT endpoint, kind, keys FROM push_subscriptions "
                              "WHERE user_id = ? ORDER BY created_at", (user_id,)).fetchall()
        out = []
        for endpoint, kind, keys in rows:
            try:
                parsed = json.loads(keys) if keys else {}
            except ValueError:
                parsed = {}
            out.append({"endpoint": endpoint, "kind": kind, "keys": parsed})
        return out

    def was_sent(self, mix_id: str, day: str) -> bool:
        with self._connect() as db:
            return db.execute("SELECT 1 FROM push_sent WHERE mix_id = ? AND day = ?",
                              (mix_id, day)).fetchone() is not None

    def mark_sent(self, mix_id: str, day: str, delivered: int) -> bool:
        """Claims the day for this mix. False if it was already claimed, so
        two loops (two workers) never send the same notification twice."""
        with self._lock, self._connect() as db:
            cur = db.execute("INSERT OR IGNORE INTO push_sent (mix_id, day, sent_at, delivered) "
                             "VALUES (?, ?, ?, ?)", (mix_id, day, time.time(), int(delivered)))
            return cur.rowcount == 1

    def forget(self, user_id: str) -> int:
        """An account deleted: where its phone could be reached goes with it."""
        return self.unsubscribe(user_id)


def _edition_ready(now: float) -> bool:
    """Whether the edition a tap would play right now is written."""
    import daily_edition
    if not _settings().daily_edition:
        # No edition: a tap writes its own episode, as it always did. Nothing
        # is "ready" ahead of time, and nothing says it is.
        return False
    row = daily_edition.store().status(daily_edition.slot_id(daily_edition.last_slot(now)))
    return bool(row) and row.get("status") == daily_edition.READY


def listen_moment(mix, now: float) -> Optional[tuple]:
    """(the listen time today, as a timestamp; the local date) in the mix's
    own zone, or None for a mix with no listen time."""
    listen_at = getattr(mix, "listen_at", "")
    if not listen_at:
        return None
    zone = _zone(getattr(mix, "listen_tz", ""))
    local_now = datetime.fromtimestamp(now, zone)
    h, m = (int(x) for x in listen_at.split(":"))
    moment = local_now.replace(hour=h, minute=m, second=0, microsecond=0)
    return moment.timestamp(), local_now.date().isoformat()


def due(mix_store, push_store: PushStore, now: Optional[float] = None) -> list:
    """Mixes whose notification should go out now: (mix, local day)."""
    now = time.time() if now is None else now
    out = []
    ready = None
    for mix in mix_store.with_listen_time():
        moment = listen_moment(mix, now)
        if moment is None or not mix.items:
            continue
        at, day = moment
        if not (at <= now < at + GRACE_SECONDS):
            continue
        if push_store.was_sent(mix.id, day):
            continue
        if ready is None:
            ready = _edition_ready(now)
        if not ready:
            # Not yet: the edition is still being written. Next tick asks again.
            return []
        out.append((mix, day))
    return out


def message_for(mix) -> dict:
    """What the notification says, and where a tap on it opens."""
    return {"title": f"Your {mix.name} mix is ready",
            "body": "Fresh episodes for today. Tap to listen.",
            "url": f"/?mix={mix.id}", "tag": f"mix-{mix.id}"}


def _send_webpush(sub: dict, payload: dict, push_store: PushStore) -> bool:
    from pywebpush import WebPushException, webpush
    s = _settings()
    try:
        webpush(subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]},
                data=json.dumps(payload),
                vapid_private_key=s.vapid_private_key,
                vapid_claims={"sub": s.vapid_subject or "mailto:hello@fam.example"},
                ttl=PUSH_TTL_SECONDS, timeout=10)
        return True
    except WebPushException as exc:
        code = getattr(getattr(exc, "response", None), "status_code", None)
        if code in (404, 410):
            # The browser threw the subscription away; so do we.
            push_store.drop_endpoint(sub["endpoint"])
            log.info("push: subscription gone (%s); dropped", code)
        else:
            log.warning("push: send failed (%s): %s", code, exc)
        return False
    except Exception as exc:  # network, bad key: visible, never fatal
        log.warning("push: send failed: %s", exc)
        return False


def send_to(user_id: str, payload: dict, push_store: PushStore) -> int:
    """Sends to every place this listener can be reached; how many took it."""
    delivered = 0
    for sub in push_store.subscriptions(user_id):
        if sub["kind"] == WEBPUSH and _send_webpush(sub, payload, push_store):
            delivered += 1
    return delivered


def tick(mix_store, push_store: PushStore, now: Optional[float] = None) -> list:
    """One pass: send every due notification. Returns what it sent, for the
    log and the tests: [(mix id, local day, delivered count)]."""
    if not status()["available"]:
        return []
    sent = []
    for mix, day in due(mix_store, push_store, now):
        if not push_store.subscriptions(mix.user_id):
            continue  # nowhere to send; ask again if they subscribe in the window
        if not push_store.mark_sent(mix.id, day, 0):
            continue
        delivered = send_to(mix.user_id, message_for(mix), push_store)
        with push_store._lock, push_store._connect() as db:
            db.execute("UPDATE push_sent SET delivered = ? WHERE mix_id = ? AND day = ?",
                       (delivered, mix.id, day))
        sent.append((mix.id, day, delivered))
    if sent:
        log.info("push: mix notifications sent: %s", sent)
    return sent


async def run_forever(mix_store, push_store: PushStore) -> None:
    import asyncio
    while True:
        try:
            await asyncio.to_thread(tick, mix_store, push_store)
        except Exception:
            log.exception("push: reminder pass failed")
        await asyncio.sleep(TICK_SECONDS)
