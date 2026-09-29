"""Instant feedback: bug reports typed during a demo, and the inbox they land in.

The demo page draws an **Instant feedback** button under the phone. Somebody
trying the app types what went wrong, and it arrives here with enough context
to find it again - which screen was showing, which build, when - rather than
as a sentence remembered at the end of the meeting.

Two rules.

**A report is a fact about the app, not about the person.** It records the
listener id only for an account (a guest is a device, `app._remembers`), and
deleting that account blanks the id and keeps the report, the way the cost
ledger is anonymised rather than emptied: a bug that was real stays real after
the person who found it leaves.

**Resolving is a toggle, never a delete.** The inbox shows what is open;
resolved reports stay in the table with when they were resolved, so "we fixed
that" has a date on it and a regression can be matched to the report that
found it the first time.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from typing import Optional

from paths import data_path

log = logging.getLogger(__name__)

#: The longest description kept. A bug report is a paragraph; this is room for
#: a long one, and a ceiling so the endpoint cannot be used as free storage.
MAX_TEXT = 4000
#: Context fields are short labels (a screen name, a build, a viewport).
MAX_FIELD = 200
#: Reports one listener may file in `WINDOW_SECONDS`. Generous for somebody
#: working through a demo, and a stop for a loop that is not a person.
PER_LISTENER = 30
WINDOW_SECONDS = 3600

STATES = ("open", "resolved")


class FeedbackError(ValueError):
    """A report that cannot be kept, with the sentence that says why."""


class FeedbackStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = data_path("FEEDBACK_DB", "feedback.db", path)
        self._local = threading.local()
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS reports (
                       id          TEXT PRIMARY KEY,
                       user_id     TEXT NOT NULL DEFAULT '',
                       text        TEXT NOT NULL,
                       screen      TEXT NOT NULL DEFAULT '',
                       build       TEXT NOT NULL DEFAULT '',
                       page        TEXT NOT NULL DEFAULT '',
                       viewport    TEXT NOT NULL DEFAULT '',
                       agent       TEXT NOT NULL DEFAULT '',
                       created     REAL NOT NULL,
                       resolved_at REAL NOT NULL DEFAULT 0,
                       note        TEXT NOT NULL DEFAULT ''
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS reports_created"
                         " ON reports(resolved_at, created)")

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    @staticmethod
    def _row(r) -> dict:
        return {
            "id": r[0], "user_id": r[1], "text": r[2], "screen": r[3],
            "build": r[4], "page": r[5], "viewport": r[6], "agent": r[7],
            "created": r[8], "resolved_at": r[9], "note": r[10],
            "state": "resolved" if r[9] else "open",
        }

    _COLUMNS = ("id, user_id, text, screen, build, page, viewport, agent,"
                " created, resolved_at, note")

    def add(self, text: str, *, user_id: str = "", screen: str = "",
            build: str = "", page: str = "", viewport: str = "",
            agent: str = "", now: Optional[float] = None,
            throttle_key: str = "") -> dict:
        """File one report. Raises `FeedbackError` with a sentence on refusal.

        `throttle_key` is who is counted against `PER_LISTENER` - the listener
        id, account or not - kept apart from `user_id`, which is only ever an
        account's, so a guest is paced without being recorded.
        """
        body = (text or "").strip()
        if not body:
            raise FeedbackError("Say what went wrong - the report is empty.")
        if len(body) > MAX_TEXT:
            raise FeedbackError(
                f"That is longer than a report can be ({MAX_TEXT} characters). "
                "Trim it and send it again.")
        at = time.time() if now is None else now
        if throttle_key and self._recent(throttle_key, at) >= PER_LISTENER:
            raise FeedbackError(
                "That is a lot of reports in an hour - try again shortly.")
        row = (uuid.uuid4().hex[:12], (user_id or "")[:MAX_FIELD], body,
               (screen or "")[:MAX_FIELD], (build or "")[:MAX_FIELD],
               (page or "")[:MAX_FIELD], (viewport or "")[:MAX_FIELD],
               (agent or "")[:MAX_FIELD * 2], at)
        conn = self._conn()
        conn.execute(
            "INSERT INTO reports (id, user_id, text, screen, build, page,"
            " viewport, agent, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
        if throttle_key:
            self._stamp(throttle_key, at)
        return self.get(row[0])

    # The pacing ledger lives in memory: it guards a demo button against a
    # runaway loop, and a restart forgetting it costs nothing.
    _stamps: dict[str, list[float]] = {}
    _stamps_lock = threading.Lock()

    def _recent(self, key: str, at: float) -> int:
        with self._stamps_lock:
            kept = [t for t in self._stamps.get(key, []) if at - t < WINDOW_SECONDS]
            self._stamps[key] = kept
            return len(kept)

    def _stamp(self, key: str, at: float) -> None:
        with self._stamps_lock:
            self._stamps.setdefault(key, []).append(at)

    def get(self, report_id: str) -> Optional[dict]:
        r = self._conn().execute(
            f"SELECT {self._COLUMNS} FROM reports WHERE id = ?",
            (report_id,)).fetchone()
        return self._row(r) if r else None

    def list(self, state: str = "open", limit: int = 200) -> list[dict]:
        """Newest first. `state` is `open`, `resolved` or `all`."""
        where = {"open": "WHERE resolved_at = 0",
                 "resolved": "WHERE resolved_at > 0",
                 "all": ""}.get(state)
        if where is None:
            raise FeedbackError(f"{state!r} is not one of open, resolved, all.")
        rows = self._conn().execute(
            f"SELECT {self._COLUMNS} FROM reports {where}"
            " ORDER BY created DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
        return [self._row(r) for r in rows]

    def counts(self) -> dict:
        open_, resolved = self._conn().execute(
            "SELECT COALESCE(SUM(resolved_at = 0), 0),"
            " COALESCE(SUM(resolved_at > 0), 0) FROM reports").fetchone()
        return {"open": int(open_), "resolved": int(resolved)}

    def resolve(self, report_id: str, resolved: bool = True, *,
                note: Optional[str] = None,
                now: Optional[float] = None) -> Optional[dict]:
        """Mark a report resolved, or open again. None if there is no such report."""
        if self.get(report_id) is None:
            return None
        at = (time.time() if now is None else now) if resolved else 0
        if note is None:
            self._conn().execute(
                "UPDATE reports SET resolved_at = ? WHERE id = ?", (at, report_id))
        else:
            self._conn().execute(
                "UPDATE reports SET resolved_at = ?, note = ? WHERE id = ?",
                (at, note.strip()[:MAX_TEXT], report_id))
        return self.get(report_id)

    def forget(self, user_id: str) -> int:
        """Anonymise, never delete: the report outlives the account."""
        if not user_id:
            return 0
        cur = self._conn().execute(
            "UPDATE reports SET user_id = '' WHERE user_id = ?", (user_id,))
        return cur.rowcount or 0
