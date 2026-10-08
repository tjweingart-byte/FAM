"""Reporting and blocking: what FAM does about people and what they post.

App Store guideline 1.2. FAM has user-generated content in comments,
messages, vibe captions, profiles and photos, and Explore replays episodes
other listeners searched. Apple requires a way to report offensive content, a
way to block an abusive user, terms that say neither is tolerated, published
contact information, and acting on a report within 24 hours by removing the
content and ejecting the user who posted it. This module keeps the records;
`app.py` applies them.

**A report hides the thing from its reporter at once.** Whoever reported a
comment, message or vibe never sees it again, whatever the review decides:
somebody who said "this is abusive" should not have to keep looking at it.

**A block is both ways and total.** Neither person sees the other's comments,
messages, vibes, profile or searched episodes, neither can message, follow or
tag the other, and blocking ends any follow between them. It is silent: the
blocked person is not told, and to them the blocker simply is not there.

**Removing is the reviewer's, never automatic.** A report never removes
anything for anybody but its reporter. The reviewer (`/admin`) decides:
dismiss, remove the content for everyone, or suspend the person who posted it
- whose comments, messages and vibes then reach nobody, and who can post
nothing new.

**A report is a fact about the content, not the reporter.** The reporter's id
is kept so their hide holds, and is never shown to the person reported.
Deleting an account removes its blocks and blanks it as a reporter; the
report itself stays, like a bug report (`feedback.py`).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Optional

from paths import data_path

#: What can be reported, and what `target` names for each.
#:   comment  - the comment id
#:   message  - the message id
#:   vibe     - the echo (vibe) id
#:   person   - their handle
#:   group    - the group chat id (`g:...`)
#:   episode  - "<minutes>:<query>", an Explore or player episode
KINDS = ("comment", "message", "vibe", "person", "group", "episode")

#: The reasons a listener picks from, in the order the menu shows them.
REASONS = (
    ("harassment", "Harassment or bullying"),
    ("hate", "Hate speech or symbols"),
    ("sexual", "Nudity or sexual content"),
    ("violence", "Violence or threats"),
    ("self_harm", "Self-harm"),
    ("spam", "Spam or scams"),
    ("false", "False or misleading"),
    ("other", "Something else"),
)
REASON_IDS = tuple(r[0] for r in REASONS)

#: What a reviewer may do with a report.
ACTIONS = ("dismissed", "removed", "suspended")

#: The promise the terms and the confirmation make (1.2). Said in one place.
REVIEW_HOURS = 24

MAX_NOTE = 1000
MAX_SNAPSHOT = 4000
#: Reports one listener may file an hour: generous for a person, a stop for
#: a loop. Paced in memory like feedback's pace; the table bounds the rest.
PER_LISTENER_HOUR = 40


class ModerationError(ValueError):
    """A request that cannot be kept, with the sentence that says why."""


def episode_target(query: str, minutes: int) -> str:
    return "%d:%s" % (int(minutes), " ".join(str(query).split())[:300])


class ModerationStore:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = data_path("MODERATION_DB", "moderation.db", path)
        self._local = threading.local()
        self._stamps: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        # Read on every comments, messages and Explore page, so held in
        # memory and dropped on any write.
        self._blocks_cache: Optional[dict[str, set]] = None
        self._suspended_cache: Optional[set] = None
        self._hidden_cache: Optional[set] = None
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS reports (
                       id          TEXT PRIMARY KEY,
                       reporter    TEXT NOT NULL DEFAULT '',
                       kind        TEXT NOT NULL,
                       target      TEXT NOT NULL,
                       owner       TEXT NOT NULL DEFAULT '',
                       reason      TEXT NOT NULL,
                       note        TEXT NOT NULL DEFAULT '',
                       snapshot    TEXT NOT NULL DEFAULT '',
                       created     REAL NOT NULL,
                       resolved_at REAL NOT NULL DEFAULT 0,
                       action      TEXT NOT NULL DEFAULT '',
                       resolution  TEXT NOT NULL DEFAULT ''
                   )""")
            conn.execute("CREATE INDEX IF NOT EXISTS reports_open"
                         " ON reports(resolved_at, created)")
            conn.execute("CREATE INDEX IF NOT EXISTS reports_by"
                         " ON reports(reporter, kind)")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS blocks (
                       blocker TEXT NOT NULL,
                       blocked TEXT NOT NULL,
                       at      REAL NOT NULL,
                       PRIMARY KEY (blocker, blocked)
                   )""")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS suspended (
                       user_id TEXT PRIMARY KEY,
                       at      REAL NOT NULL,
                       report  TEXT NOT NULL DEFAULT ''
                   )""")
            # Episodes a reviewer took off Explore (and every other shelf of
            # other people's episodes). Keyed like a report's episode target.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS hidden_episodes (
                       target TEXT PRIMARY KEY,
                       at     REAL NOT NULL,
                       report TEXT NOT NULL DEFAULT ''
                   )""")

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def _forget_caches(self) -> None:
        with self._lock:
            self._blocks_cache = None
            self._suspended_cache = None
            self._hidden_cache = None

    # --- reports -----------------------------------------------------------

    def report(self, reporter: str, kind: str, target: str, reason: str, *,
               owner: str = "", note: str = "", snapshot: Optional[dict] = None) -> dict:
        kind = (kind or "").strip().lower()
        if kind not in KINDS:
            raise ModerationError("That can't be reported.")
        target = str(target or "").strip()[:400]
        if not target:
            raise ModerationError("Nothing to report.")
        reason = (reason or "").strip().lower()
        if reason not in REASON_IDS:
            raise ModerationError("Choose why you're reporting this.")
        if reporter and owner and reporter == owner:
            raise ModerationError("That's yours - you can delete it instead.")
        if reporter:
            now = time.time()
            with self._lock:
                stamps = [t for t in self._stamps.get(reporter, []) if t > now - 3600]
                if len(stamps) >= PER_LISTENER_HOUR:
                    raise ModerationError("You've sent a lot of reports. Try again later.")
                stamps.append(now)
                self._stamps[reporter] = stamps
        row = {
            "id": uuid.uuid4().hex[:16], "reporter": reporter or "", "kind": kind,
            "target": target, "owner": owner or "", "reason": reason,
            "note": " ".join(str(note or "").split())[:MAX_NOTE],
            "snapshot": json.dumps(snapshot or {})[:MAX_SNAPSHOT],
            "created": time.time(),
        }
        self._conn().execute(
            "INSERT INTO reports (id, reporter, kind, target, owner, reason, note,"
            " snapshot, created) VALUES (:id, :reporter, :kind, :target, :owner,"
            " :reason, :note, :snapshot, :created)", row)
        return self._public(row)

    @staticmethod
    def _public(row: dict) -> dict:
        try:
            snapshot = json.loads(row.get("snapshot") or "{}")
        except ValueError:
            snapshot = {}
        return {"id": row["id"], "kind": row["kind"], "target": row["target"],
                "owner": row.get("owner", ""), "reporter": row.get("reporter", ""),
                "reason": row["reason"],
                "reason_label": dict(REASONS).get(row["reason"], row["reason"]),
                "note": row.get("note", ""), "snapshot": snapshot,
                "created": row["created"],
                "resolved_at": row.get("resolved_at", 0) or 0,
                "action": row.get("action", "") or "",
                "resolution": row.get("resolution", "") or ""}

    def reports(self, state: str = "open", limit: int = 200) -> list[dict]:
        where = "resolved_at = 0" if state == "open" else "resolved_at > 0"
        cols = ("id", "reporter", "kind", "target", "owner", "reason", "note",
                "snapshot", "created", "resolved_at", "action", "resolution")
        rows = self._conn().execute(
            "SELECT %s FROM reports WHERE %s ORDER BY created %s LIMIT ?"
            % (", ".join(cols), where, "ASC" if state == "open" else "DESC"),
            (int(limit),)).fetchall()
        return [self._public(dict(zip(cols, r))) for r in rows]

    def get(self, report_id: str) -> Optional[dict]:
        cols = ("id", "reporter", "kind", "target", "owner", "reason", "note",
                "snapshot", "created", "resolved_at", "action", "resolution")
        row = self._conn().execute(
            "SELECT %s FROM reports WHERE id = ?" % ", ".join(cols),
            (report_id,)).fetchone()
        return self._public(dict(zip(cols, row))) if row else None

    def open_count(self) -> int:
        return int(self._conn().execute(
            "SELECT COUNT(*) FROM reports WHERE resolved_at = 0").fetchone()[0])

    def oldest_open_age(self, now: Optional[float] = None) -> Optional[float]:
        """Seconds the oldest open report has waited - the 24-hour promise."""
        row = self._conn().execute(
            "SELECT MIN(created) FROM reports WHERE resolved_at = 0").fetchone()
        if not row or row[0] is None:
            return None
        return (now or time.time()) - float(row[0])

    def resolve(self, report_id: str, action: str, note: str = "") -> dict:
        if action not in ACTIONS:
            raise ModerationError("Unknown action.")
        report = self.get(report_id)
        if report is None:
            raise ModerationError("No such report.")
        now = time.time()
        # Every open report about the same thing is answered by the same
        # decision: ten people reporting one comment is one decision.
        self._conn().execute(
            "UPDATE reports SET resolved_at = ?, action = ?, resolution = ?"
            " WHERE resolved_at = 0 AND kind = ? AND target = ?",
            (now, action, " ".join(str(note or "").split())[:MAX_NOTE],
             report["kind"], report["target"]))
        self._conn().execute(
            "UPDATE reports SET resolved_at = ?, action = ?, resolution = ?"
            " WHERE id = ?", (now, action,
                              " ".join(str(note or "").split())[:MAX_NOTE], report_id))
        return self.get(report_id)

    def reported_by(self, reporter: str, kind: str) -> set:
        """Targets of `kind` this listener reported: hidden from them now."""
        if not reporter:
            return set()
        return {r[0] for r in self._conn().execute(
            "SELECT target FROM reports WHERE reporter = ? AND kind = ?",
            (reporter, kind))}

    # --- blocks ------------------------------------------------------------

    def _blocks(self) -> dict[str, set]:
        with self._lock:
            if self._blocks_cache is not None:
                return self._blocks_cache
        pairs: dict[str, set] = {}
        for blocker, blocked in self._conn().execute(
                "SELECT blocker, blocked FROM blocks"):
            pairs.setdefault(blocker, set()).add(blocked)
        with self._lock:
            self._blocks_cache = pairs
        return pairs

    def block(self, blocker: str, blocked: str) -> bool:
        if not blocker or not blocked:
            raise ModerationError("Nobody to block.")
        if blocker == blocked:
            raise ModerationError("You can't block yourself.")
        cur = self._conn().execute(
            "INSERT OR IGNORE INTO blocks (blocker, blocked, at) VALUES (?, ?, ?)",
            (blocker, blocked, time.time()))
        self._forget_caches()
        return bool(cur.rowcount)

    def unblock(self, blocker: str, blocked: str) -> bool:
        cur = self._conn().execute(
            "DELETE FROM blocks WHERE blocker = ? AND blocked = ?", (blocker, blocked))
        self._forget_caches()
        return bool(cur.rowcount)

    def blocked_by(self, blocker: str) -> list[str]:
        """Whom this listener blocked, newest first - their Settings list."""
        return [r[0] for r in self._conn().execute(
            "SELECT blocked FROM blocks WHERE blocker = ? ORDER BY at DESC", (blocker,))]

    def apart(self, user_id: str) -> set:
        """Everyone this listener must not see or reach: whom they blocked,
        who blocked them, and everyone suspended. One set, both ways."""
        if not user_id:
            return set(self.suspended())
        pairs = self._blocks()
        out = set(pairs.get(user_id, ()))
        out.update(b for b, blocked in pairs.items() if user_id in blocked)
        out.update(self.suspended())
        out.discard(user_id)
        return out

    def is_apart(self, a: str, b: str) -> bool:
        if not a or not b or a == b:
            return False
        pairs = self._blocks()
        return b in pairs.get(a, ()) or a in pairs.get(b, ())

    # --- the reviewer's actions -------------------------------------------

    def suspended(self) -> set:
        with self._lock:
            if self._suspended_cache is not None:
                return self._suspended_cache
        found = {r[0] for r in self._conn().execute("SELECT user_id FROM suspended")}
        with self._lock:
            self._suspended_cache = found
        return found

    def is_suspended(self, user_id: str) -> bool:
        return bool(user_id) and user_id in self.suspended()

    def suspend(self, user_id: str, report_id: str = "") -> None:
        if not user_id:
            raise ModerationError("Nobody to suspend.")
        self._conn().execute(
            "INSERT OR REPLACE INTO suspended (user_id, at, report) VALUES (?, ?, ?)",
            (user_id, time.time(), report_id))
        self._forget_caches()

    def reinstate(self, user_id: str) -> bool:
        cur = self._conn().execute("DELETE FROM suspended WHERE user_id = ?", (user_id,))
        self._forget_caches()
        return bool(cur.rowcount)

    def hide_episode(self, target: str, report_id: str = "") -> None:
        self._conn().execute(
            "INSERT OR REPLACE INTO hidden_episodes (target, at, report) VALUES (?, ?, ?)",
            (target, time.time(), report_id))
        self._forget_caches()

    def hidden_episodes(self) -> set:
        with self._lock:
            if self._hidden_cache is not None:
                return self._hidden_cache
        found = {r[0] for r in self._conn().execute("SELECT target FROM hidden_episodes")}
        with self._lock:
            self._hidden_cache = found
        return found

    # --- account deletion --------------------------------------------------

    def forget(self, user_id: str) -> int:
        """Account deletion: their blocks go both ways, and they are blanked as
        a reporter. Reports about their content stay, as facts."""
        conn = self._conn()
        n = conn.execute("DELETE FROM blocks WHERE blocker = ? OR blocked = ?",
                         (user_id, user_id)).rowcount or 0
        n += conn.execute("UPDATE reports SET reporter = '' WHERE reporter = ?",
                          (user_id,)).rowcount or 0
        n += conn.execute("DELETE FROM suspended WHERE user_id = ?",
                          (user_id,)).rowcount or 0
        self._forget_caches()
        return n

    def report_summary(self) -> dict:
        """For `/api/health`'s admin view and the inbox header."""
        age = self.oldest_open_age()
        return {"open": self.open_count(),
                "oldest_open_hours": None if age is None else round(age / 3600, 1),
                "review_hours": REVIEW_HOURS,
                "overdue": bool(age is not None and age > REVIEW_HOURS * 3600)}
