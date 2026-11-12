"""Notifications to a listener's phone (the 10.1 packet's third set; §552).

Three kinds, each told at most once per thing it is about (`push_log`):

* **daily** - "Your myFAM is ready" (the mixes; names swapped for the
  listener, §185). **One per person, never one per mix** (§552, at the
  owner's direction, reversing §184's per-mix listen time). It names the
  most important story across their mixes - the title of an episode the
  05:00 edition already wrote - then how many more across how many mixes.
  *When*: the time they chose in Settings; else a listen time an old client
  set on one of their mixes (the earliest); else **learned** - the median
  time of their first play of the day over the last `LEARN_DAYS`, once
  `LEARN_MIN_DAYS` days have one; else `DEFAULT_DAILY_AT`. Skipped on a day
  they already listened before it. **Backs off when ignored**: after
  `BACKOFF_AFTER` reminders in a row with no play after them it goes every
  other day, and after `GIVE_UP_AFTER` it stops until they play again.
* **breaking** and **final** - a big story, and a followed team's game
  ending. Detected, written and sent by `push_stories.py`; this file only
  delivers them and keeps the per-person rules: the listener's switch for
  each kind, and **quiet hours** (`QUIET_START`-`QUIET_END` in their own zone,
  where a story waits for the morning if it is still big). The daily
  reminder is not held by quiet hours: its time is one they chose or keep.

The rules this file keeps:

* **Ready means written.** The daily reminder goes out at or after its time
  and only once the edition it would play is built (`daily_edition` reports
  the slot `ready`). A time before the edition waits for it, up to
  `GRACE_SECONDS`; an edition that failed sends nothing, because "ready"
  would be false. A person whose mixes are all dormant (`DAILY_EDITION_
  ACTIVE_HOURS` unplayed) has nothing written, so their reminder says what is
  waiting and never that it is ready.
* **Spread over fifteen minutes** (§448, the owner, 11.04). Times cluster on
  the hour, and every "ready" landing at 07:00 sent a crowd of first plays at
  the voice in the same minute. Each person's push goes out a fixed 0-15
  minutes after their time (`spread_offset`, from the person and the day, so
  a restart does not move it), never more than fifteen.
* **Once a day**, by the listener's own calendar day in their own zone
  (`push_log`, keyed by person, kind and local date) - not the server's.
* **Delivery is Web Push** (a service worker's `push` event, `static/sw.js`).
  It needs a VAPID key pair (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`), a
  contact for the push services (`VAPID_SUBJECT`, mailto: or https:) and the
  optional `pywebpush` package (`requirements-push.txt`). Without either,
  `status()` says which, `/api/push` repeats it, and the reminder row says
  notifications are not switched on - every choice is still kept, so nothing
  has to be set again once they are. Like password reset with no mail
  delivery (`ACCOUNTS.md`): no delivery, and it says so.
* **The native app is the next client.** `subscriptions.kind` is `webpush`
  today; an APNs device token is another kind, sent by a different function,
  read from the same table and the same due list.
* Staging never sends (`spend_guard`): nothing leaves that machine anyway, and
  `status()` names it rather than letting every send fail.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3

import db as store_db
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional

from paths import data_path

log = logging.getLogger("fam.push")

#: How long past the reminder's time a "ready" notification is still worth
#: sending. Covers a time set before the 05:00 edition finishes, and a server
#: that was restarting at the minute; past it, the listener has either
#: listened or moved on, and a late "ready" is noise.
GRACE_SECONDS = 3 * 3600
#: §448: the most a "ready" push may land after its time. The owner's
#: ceiling: "don't make it more than 15 minutes".
SPREAD_SECONDS = 15 * 60
#: How often the loop looks for due reminders. A notification lands within
#: this much of the minute chosen.
TICK_SECONDS = 60
#: Push services keep an undelivered message this long (phone off, say).
PUSH_TTL_SECONDS = 2 * 3600
WEBPUSH = "webpush"

#: The three kinds a listener can switch on and off (§552).
DAILY = "daily"
BREAKING = "breaking"
FINAL = "final"
KINDS = (DAILY, BREAKING, FINAL)
#: Which `push_prefs` column switches each kind.
SWITCH = {DAILY: "daily", BREAKING: "breaking", FINAL: "finals"}

#: §552: the daily reminder's time for somebody who chose none and has too
#: little history to learn one from.
DEFAULT_DAILY_AT = "07:30"
#: How far back the learned time reads plays, and on how many distinct days
#: a first play must exist before it is trusted over the default.
LEARN_DAYS = 14
LEARN_MIN_DAYS = 3
#: A first play this soon after a daily reminder answered it (`learned_time`).
PROMPTED_SECONDS = 3600
#: §552 "back off when ignored": this many daily reminders in a row with no
#: play after them, and it goes every other day; this many, and it stops
#: until they play again.
BACKOFF_AFTER = 3
GIVE_UP_AFTER = 8
#: §552 quiet hours, in the listener's own zone: no story or final between
#: these hours. A story still big at `QUIET_END` goes then.
QUIET_START = 22
QUIET_END = 8


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
                          "(no VAPID keys). Your reminder time is kept for when they are."}
    if not s.vapid_subject.startswith(("mailto:", "https://")):
        # Push services (Apple's among them) refuse a key with no contact,
        # and nothing here invents one.
        return {"available": False, "public_key": "",
                "reason": "Notifications are not set up on this server yet "
                          "(VAPID_SUBJECT is not a mailto: or https: address). "
                          "Your reminder time is kept for when they are."}
    try:
        import pywebpush  # noqa: F401
    except ImportError:
        return {"available": False, "public_key": "",
                "reason": "Notifications are not set up on this server yet "
                          "(pywebpush is not installed). Your reminder time is kept for when they are."}
    return {"available": True, "public_key": s.vapid_public_key, "reason": ""}


class PushStore:
    """Where each listener's phone can be reached, what they want told, and
    what was sent.

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
            # §552: one row per person. `daily_at` is "" for "learn it".
            db.execute("""CREATE TABLE IF NOT EXISTS push_prefs (
                user_id TEXT PRIMARY KEY,
                daily INTEGER NOT NULL DEFAULT 1,
                daily_at TEXT NOT NULL DEFAULT '',
                breaking INTEGER NOT NULL DEFAULT 1,
                finals INTEGER NOT NULL DEFAULT 1,
                tz TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL DEFAULT 0,
                daily_chosen INTEGER NOT NULL DEFAULT 0)""")
            # What was told to whom: one row per person, kind and thing (a
            # local date for the daily reminder, a story id for the others).
            db.execute("""CREATE TABLE IF NOT EXISTS push_log (
                user_id TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL,
                sent_at REAL NOT NULL, delivered INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, kind, ref))""")

    @contextmanager
    def _connect(self):
        """One connection per call, committed and closed - the loop runs
        every minute, and `with sqlite3.connect()` alone never closes."""
        db = store_db.connect(self.path, timeout=5.0)
        try:
            with db:
                yield db
        finally:
            db.close()

    # ---- where to deliver ----
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

    def subscribers(self) -> list:
        """Everybody with somewhere to be told, once each."""
        with self._connect() as db:
            rows = db.execute("SELECT DISTINCT user_id FROM push_subscriptions "
                              "ORDER BY user_id").fetchall()
        return [r[0] for r in rows]

    # ---- what they want told (§552) ----
    def prefs(self, user_id: str) -> dict:
        """Their switches, chosen time and zone; the defaults with no row:
        every kind on, the time learned."""
        with self._connect() as db:
            row = db.execute("SELECT daily, daily_at, breaking, finals, tz, daily_chosen "
                             "FROM push_prefs WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            return {"daily": True, "daily_at": "", "breaking": True, "finals": True, "tz": "",
                    "chosen": False}
        # `chosen`: they answered "when" here - a time, or "when I usually
        # listen" - so a listen time an old client set on a mix no longer
        # stands for them.
        return {"daily": bool(row[0]), "daily_at": row[1] or "", "breaking": bool(row[2]),
                "finals": bool(row[3]), "tz": row[4] or "", "chosen": bool(row[5])}

    def set_prefs(self, user_id: str, **changes) -> dict:
        """Changes the given fields and keeps the rest. Raises ValueError with
        a sentence for the listener on a time it cannot read."""
        if not user_id:
            raise ValueError("No listener id.")
        now = self.prefs(user_id)
        for name in ("daily", "breaking", "finals"):
            if changes.get(name) is not None:
                now[name] = bool(changes[name])
        if changes.get("daily_at") is not None:
            now["daily_at"] = clean_listen_at(changes["daily_at"])
            now["chosen"] = True
        if changes.get("tz") is not None:
            now["tz"] = clean_zone(changes["tz"])
        with self._lock, self._connect() as db:
            db.execute("INSERT OR REPLACE INTO push_prefs (user_id, daily, daily_at, "
                       "breaking, finals, tz, updated_at, daily_chosen) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (user_id, int(now["daily"]), now["daily_at"], int(now["breaking"]),
                        int(now["finals"]), now["tz"], time.time(), int(now["chosen"])))
        return now

    # ---- what was told ----
    def was_sent(self, user_id: str, kind: str, ref: str) -> bool:
        with self._connect() as db:
            return db.execute("SELECT 1 FROM push_log WHERE user_id = ? AND kind = ? "
                              "AND ref = ?", (user_id, kind, ref)).fetchone() is not None

    def mark_sent(self, user_id: str, kind: str, ref: str, delivered: int = 0,
                  at: Optional[float] = None) -> bool:
        """Claims this thing for this person. False if it was already claimed,
        so two loops (two workers) never send the same notification twice."""
        with self._lock, self._connect() as db:
            cur = db.execute("INSERT OR IGNORE INTO push_log (user_id, kind, ref, sent_at, "
                             "delivered) VALUES (?, ?, ?, ?, ?)",
                             (user_id, kind, ref, time.time() if at is None else at,
                              int(delivered)))
            return cur.rowcount == 1

    def record_delivered(self, user_id: str, kind: str, ref: str, delivered: int) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE push_log SET delivered = ? WHERE user_id = ? AND kind = ? "
                       "AND ref = ?", (int(delivered), user_id, kind, ref))

    def sent(self, user_id: str, kind: str, limit: int = 20) -> list:
        """[(ref, sent_at)], newest first."""
        with self._connect() as db:
            rows = db.execute("SELECT ref, sent_at FROM push_log WHERE user_id = ? "
                              "AND kind = ? ORDER BY sent_at DESC LIMIT ?",
                              (user_id, kind, int(limit))).fetchall()
        return [(r[0], float(r[1])) for r in rows]

    def sent_count(self, kind: str, ref: str) -> int:
        """How many people were told about this thing (for /admin)."""
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM push_log WHERE kind = ? AND ref = ?",
                                  (kind, ref)).fetchone()[0])

    def forget(self, user_id: str) -> int:
        """An account deleted: where its phone could be reached goes with it,
        and so do its choices and what it was told."""
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM push_prefs WHERE user_id = ?", (user_id,))
            db.execute("DELETE FROM push_log WHERE user_id = ?", (user_id,))
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


def _edition_report(now: float) -> dict:
    """The current mixes' edition's report, or {}."""
    import daily_edition
    try:
        row = daily_edition.store().status(daily_edition.slot_id(daily_edition.last_slot(now)))
    except Exception:  # noqa: BLE001 - a headline is never worth a failed send
        return {}
    return (row or {}).get("report") or {}


# --------------------------------------------------------------------------
# When: chosen, an old client's listen time, learned, or the default (§552)
# --------------------------------------------------------------------------
def learned_time(plays, zone_name: str, now: float, reminders=()) -> str:
    """'HH:MM': the median time of their first play of the day, over the last
    `LEARN_DAYS`, rounded to five minutes - or '' with fewer than
    `LEARN_MIN_DAYS` days to read. `plays` is play timestamps, any order.

    `reminders` is [(when it was due, when it was sent)] for the daily
    reminders already sent. A first play within `PROMPTED_SECONDS` of one is
    the reminder being answered, not a habit: that day counts at the time the
    reminder was due. Read as a habit, every answered reminder pushed the
    next one later by its own spread and the time crept forward a few
    minutes a day."""
    zone = _zone(zone_name)
    since = now - LEARN_DAYS * 86400
    first: dict = {}
    for at in plays or ():
        at = float(at)
        if not (since <= at <= now):
            continue
        local = datetime.fromtimestamp(at, zone)
        day = local.date().isoformat()
        if day not in first or at < first[day]:
            first[day] = at
    for day, at in list(first.items()):
        for due_at, sent_at in reminders or ():
            if sent_at <= at <= sent_at + PROMPTED_SECONDS:
                first[day] = min(at, float(due_at))
                break
    first = {day: (lambda l: l.hour * 60 + l.minute)(datetime.fromtimestamp(at, zone))
             for day, at in first.items()}
    if len(first) < LEARN_MIN_DAYS:
        return ""
    minutes = sorted(first.values())
    mid = len(minutes) // 2
    median = minutes[mid] if len(minutes) % 2 else (minutes[mid - 1] + minutes[mid]) // 2
    median = int(round(median / 5.0)) * 5 % (24 * 60)
    return f"{median // 60:02d}:{median % 60:02d}"


def daily_time(prefs: dict, mixes: list, plays, now: float, reminders=()) -> tuple:
    """(HH:MM, zone name, where it came from: chosen / mix / learned /
    default) for this person's daily reminder. `plays` is a list of play
    timestamps, or a callable returning one - asked only when the time is
    to be learned."""
    zone = prefs.get("tz") or next((m.listen_tz for m in mixes
                                    if getattr(m, "listen_tz", "")), "")
    if prefs.get("daily_at"):
        return prefs["daily_at"], zone, "chosen"
    # A listen time an installed client set on one mix (§184) was a choice
    # too: the earliest one stands for the person until they choose here.
    old = sorted((m.listen_at, m.listen_tz) for m in mixes if getattr(m, "listen_at", ""))
    if old and not prefs.get("chosen"):
        return old[0][0], zone or old[0][1], "mix"
    if callable(plays):
        plays = plays()
    learned = learned_time(plays, zone, now, reminders)
    if learned:
        return learned, zone, "learned"
    return DEFAULT_DAILY_AT, zone, "default"


def moments(hhmm: str, zone_name: str, now: float) -> list:
    """Yesterday's time and today's, each (timestamp, local date).

    Yesterday's is here because a push may land after midnight: a 23:55 time
    plus its spread (§448), or the grace window, crosses into the next local
    day, where "today's" time is still hours away. Asking only about today
    lost those pushes. Each day keeps its own `push_log` key."""
    zone = _zone(zone_name)
    h, m = (int(x) for x in hhmm.split(":"))
    local_now = datetime.fromtimestamp(now, zone)
    today = local_now.replace(hour=h, minute=m, second=0, microsecond=0)
    before = (local_now - timedelta(days=1)).replace(hour=h, minute=m, second=0,
                                                    microsecond=0)
    return [(before.timestamp(), before.date().isoformat()),
            (today.timestamp(), today.date().isoformat())]


def spread_offset(key: str, day: str) -> int:
    """Seconds after its time this person's push goes out today (§448): 0 to
    `SPREAD_SECONDS`, the same all day and different from day to day, so
    nobody's push is always the last."""
    digest = hashlib.sha256(f"{key}|{day}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % (SPREAD_SECONDS + 1)


def ignored_streak(sends: list, plays) -> int:
    """How many of the most recent daily reminders in a row had no play
    after them (before the next reminder, or now)."""
    plays = sorted(float(p) for p in plays or ())
    streak, until = 0, float("inf")
    for _ref, at in sends:  # newest first
        if any(at <= p < until for p in plays):
            break
        streak += 1
        until = at
    return streak


def in_quiet_hours(zone_name: str, now: float) -> bool:
    """§552: whether it is between `QUIET_START` and `QUIET_END` where they are."""
    hour = datetime.fromtimestamp(now, _zone(zone_name)).hour
    if QUIET_START <= QUIET_END:
        return QUIET_START <= hour < QUIET_END
    return hour >= QUIET_START or hour < QUIET_END


def _played_on(plays, zone_name: str, day: str, before: float) -> bool:
    zone = _zone(zone_name)
    return any(datetime.fromtimestamp(float(p), zone).date().isoformat() == day
               and float(p) <= before for p in plays or ())


def _by_owner(mix_store) -> dict:
    out: dict = {}
    for mix in mix_store.all_mixes():
        if getattr(mix, "items", None):
            out.setdefault(mix.user_id, []).append(mix)
    return out


def due(mix_store, push_store: PushStore, now: Optional[float] = None,
        plays=None, users=None) -> list:
    """People whose daily reminder should go out now: [(user id, local day)].

    `plays(user_id)` returns their play timestamps (the app reads its event
    log); `users`, when given, limits the pass to them (who can be reached)."""
    import daily_edition
    now = time.time() if now is None else now
    out = []
    ready = None
    wanted = set(users) if users is not None else None
    for user_id, mixes in sorted(_by_owner(mix_store).items()):
        if wanted is not None and user_id not in wanted:
            continue
        prefs = push_store.prefs(user_id)
        if not prefs["daily"]:
            continue
        cache: dict = {}

        def heard(uid=user_id, cache=cache) -> list:
            # Read once per person per pass, and only when needed: to learn
            # a time (at most once a day, `_LEARNED`) or once one is due.
            if "plays" not in cache:
                cache["plays"] = list(plays(uid)) if plays is not None else []
            return cache["plays"]

        hhmm, zone, _why = _daily_time_cached(user_id, prefs, mixes, heard, now,
                                              push_store)
        moment = None
        for at, day in moments(hhmm, zone, now):
            if (at + spread_offset(user_id, day) <= now < at + GRACE_SECONDS
                    and not push_store.was_sent(user_id, DAILY, day)):
                moment = (at, day)
                break
        if moment is None:
            continue
        at, day = moment
        if _played_on(heard(), zone, day, now):
            continue  # they already listened today; nothing to remind
        streak = ignored_streak(push_store.sent(user_id, DAILY, GIVE_UP_AFTER + 1), heard())
        if streak >= GIVE_UP_AFTER:
            continue
        if streak >= BACKOFF_AFTER:
            yesterday = (datetime.fromisoformat(day) - timedelta(days=1)).date().isoformat()
            if push_store.was_sent(user_id, DAILY, yesterday):
                continue  # every other day while they are not listening
        active = [m for m in mixes if daily_edition.is_active(m, now)]
        if active:
            if ready is None:
                ready = _edition_ready(now)
            if not ready:
                continue  # still being written; the next tick asks again
        out.append((user_id, day))
    return out


#: (person, local date) -> the daily_time answer, so the per-minute pass
#: reads somebody's plays to learn their time once a day, not every minute.
_LEARNED: dict = {}
_LEARNED_MAX = 50000


def reminders_for(user_id: str, push_store: PushStore) -> list:
    """[(when due, when sent)] for this person's recent daily reminders."""
    return [(at - spread_offset(user_id, ref), at)
            for ref, at in push_store.sent(user_id, DAILY, LEARN_DAYS + 2)]


def _daily_time_cached(user_id: str, prefs: dict, mixes: list, heard, now: float,
                       push_store: PushStore) -> tuple:
    hhmm, zone, why = daily_time(prefs, mixes, (), now)
    if why != "default" or prefs.get("daily_at"):
        return hhmm, zone, why  # chosen, or an old mix time: no plays needed
    key = (user_id, datetime.fromtimestamp(now, _zone(zone)).date().isoformat(), zone)
    if key not in _LEARNED:
        if len(_LEARNED) > _LEARNED_MAX:
            _LEARNED.clear()
        _LEARNED[key] = daily_time(prefs, mixes, heard, now,
                                   reminders_for(user_id, push_store))
    return _LEARNED[key]


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def message_for(user_id: str, mix_store, now: Optional[float] = None) -> dict:
    """What the daily reminder says, and where a tap on it opens.

    Headline first (§552): the title of an episode the edition wrote for
    their most recently played mix, then how many more across how many mixes.
    The mixes are "myFAM" on the listener's screen (§185)."""
    import daily_edition
    now = time.time() if now is None else now
    mixes = [m for m in mix_store.list_for_user(user_id) if m.items]
    mixes.sort(key=lambda m: -float(getattr(m, "played_at", 0.0) or 0.0))
    active = [m for m in mixes if daily_edition.is_active(m, now)]
    if not active:
        names = ", ".join(m.name for m in mixes[:2])
        return {"title": "Your myFAM is waiting",
                "body": (f"Catch up on {names}." if names else "Catch up on today's episodes."),
                "url": "/?mixes=1", "tag": "fam-daily"}
    episodes = (_edition_report(now).get("episodes") or {})
    queries, headline = [], ""
    for mix in active:
        for item in mix.items:
            query = (daily_edition.prompt_for(item, now) or "").strip()
            if not query or query in queries:
                continue
            queries.append(query)
            if not headline:
                written = episodes.get(query) or {}
                headline = (written.get("title") or "").strip()
    count = len(queries)
    where = (f"in {active[0].name}" if len(active) == 1
             else f"across {_count(len(active), 'mix', 'mixes')}")
    if headline:
        rest = count - 1
        body = (f"{headline}, and {_count(rest, 'more', 'more')} {where}." if rest
                else f"{headline}.")
    else:
        body = f"{_count(count, 'episode', 'episodes')} {where}."
    return {"title": "Your myFAM is ready", "body": body,
            "url": "/?mixes=1", "tag": "fam-daily"}


# --------------------------------------------------------------------------
# Delivery
# --------------------------------------------------------------------------
def _send_webpush(sub: dict, payload: dict, push_store: PushStore) -> bool:
    from pywebpush import WebPushException, webpush
    s = _settings()
    try:
        webpush(subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]},
                data=json.dumps(payload),
                vapid_private_key=s.vapid_private_key,
                vapid_claims={"sub": s.vapid_subject},
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


def deliver(user_id: str, kind: str, ref: str, payload: dict,
            push_store: PushStore) -> Optional[int]:
    """Claims (person, kind, thing) and sends. None when it was already
    claimed - another worker has it - else how many devices took it."""
    if not push_store.mark_sent(user_id, kind, ref, 0):
        return None
    delivered = send_to(user_id, payload, push_store)
    push_store.record_delivered(user_id, kind, ref, delivered)
    return delivered


def tick(mix_store, push_store: PushStore, now: Optional[float] = None,
         allowed=None, plays=None) -> list:
    """One pass: send every due daily reminder. Returns what it sent, for
    the log and the tests: [(user id, local day, delivered count)].

    `allowed(user_id)` says whether this person may be told anything now -
    the app passes the waitlist's answer, so nobody outside the app while it
    runs is notified about what is inside it."""
    if not status()["available"]:
        return []
    now = time.time() if now is None else now
    reachable = push_store.subscribers()
    sent = []
    for user_id, day in due(mix_store, push_store, now, plays=plays, users=reachable):
        if allowed is not None and not allowed(user_id):
            continue
        delivered = deliver(user_id, DAILY, day, message_for(user_id, mix_store, now),
                            push_store)
        if delivered is not None:
            sent.append((user_id, day, delivered))
    if sent:
        log.info("push: daily reminders sent: %s", sent)
    return sent


async def run_forever(mix_store, push_store: PushStore, allowed=None, plays=None,
                      stories=None) -> None:
    """The daily reminders every `TICK_SECONDS`; `stories`, when given, is an
    async callable for `push_stories` run on the same clock."""
    import asyncio
    while True:
        try:
            await asyncio.to_thread(tick, mix_store, push_store, None, allowed, plays)
        except Exception:
            log.exception("push: reminder pass failed")
        if stories is not None:
            try:
                await stories()
            except Exception:
                log.exception("push: story pass failed")
        await asyncio.sleep(TICK_SECONDS)
