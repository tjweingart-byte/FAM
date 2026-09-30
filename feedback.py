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

import json
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
#: Reports kept from everybody together in `WINDOW_SECONDS`. The per-listener
#: pace is keyed on the session, and a script that drops its cookie gets a new
#: session every request (the address is no key either - behind the router
#: every request comes from the proxy, `app._limit_key`). So this is the
#: bound that holds: counted from the table, it survives restarts, and it
#: caps the disk a loop can fill at this many 4 KB rows an hour.
GLOBAL_PER_WINDOW = 500

STATES = ("open", "resolved")

#: What a report filed on the audio player keeps of the episode (9.30 #6):
#: its title, who it was built on, and what it said, copied at filing time -
#: the cache keeps an episode a week, and a report outlives that. Bounded so
#: `GLOBAL_PER_WINDOW` still bounds the disk: a ten-minute script is ~10 KB.
MAX_EPISODE_TRANSCRIPT = 30000
MAX_EPISODE_SOURCES = 30


def episode_snapshot(query: str, minutes: int, title: str = "",
                     sources: Optional[dict] = None,
                     sentences: Optional[list] = None) -> dict:
    """The episode half of a report, in the shape the inbox draws.

    `sources` is `Provenance.as_dict()`; only what the sources panel shows is
    kept (publisher, headline, date, grade, link). `sentences` is the
    transcript, trimmed to `MAX_EPISODE_TRANSCRIPT` characters and said so.
    """
    items = []
    for item in ((sources or {}).get("items") or [])[:MAX_EPISODE_SOURCES]:
        if not isinstance(item, dict):
            continue
        items.append({k: str(item.get(k) or "")[:MAX_FIELD * 2]
                      for k in ("label", "title", "at", "tier", "kind", "url")})
    kept, used, cut = [], 0, False
    for sentence in sentences or []:
        sentence = str(sentence)
        if used + len(sentence) > MAX_EPISODE_TRANSCRIPT:
            cut = True
            break
        kept.append(sentence)
        used += len(sentence) + 1
    return {"query": (query or "")[:500], "minutes": int(minutes or 0),
            "title": (title or "")[:MAX_FIELD], "sources": items,
            "transcript": kept, "transcript_cut": cut}


class FeedbackError(ValueError):
    """A report that cannot be kept, with the sentence that says why."""


class FeedbackStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = data_path("FEEDBACK_DB", "feedback.db", path)
        self._local = threading.local()
        # The pacing ledger lives in memory, per store: it guards a demo
        # button against a runaway loop, and a restart forgetting it costs
        # nothing - `GLOBAL_PER_WINDOW` is the bound that is kept on disk.
        self._stamps: dict[str, list[float]] = {}
        self._stamps_lock = threading.Lock()
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
                       note        TEXT NOT NULL DEFAULT '',
                       episode     TEXT NOT NULL DEFAULT ''
                   )"""
            )
            # Reports filed before 9.30 have no episode column.
            columns = {r[1] for r in conn.execute("PRAGMA table_info(reports)")}
            if "episode" not in columns:
                conn.execute("ALTER TABLE reports ADD COLUMN episode TEXT NOT NULL DEFAULT ''")
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
        try:
            episode = json.loads(r[11]) if r[11] else None
        except ValueError:
            episode = None
        return {
            "id": r[0], "user_id": r[1], "text": r[2], "screen": r[3],
            "build": r[4], "page": r[5], "viewport": r[6], "agent": r[7],
            "created": r[8], "resolved_at": r[9], "note": r[10],
            "state": "resolved" if r[9] else "open",
            # The episode on the player when it was filed, or None.
            "episode": episode if isinstance(episode, dict) else None,
        }

    _COLUMNS = ("id, user_id, text, screen, build, page, viewport, agent,"
                " created, resolved_at, note, episode")

    def add(self, text: str, *, user_id: str = "", screen: str = "",
            build: str = "", page: str = "", viewport: str = "",
            agent: str = "", now: Optional[float] = None,
            throttle_key: str = "", episode: Optional[dict] = None) -> dict:
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
        if throttle_key and not self._admit(throttle_key, at):
            raise FeedbackError(
                "That is a lot of reports in an hour - try again shortly.")
        recent = self._conn().execute(
            "SELECT count(*) FROM reports WHERE created > ?",
            (at - WINDOW_SECONDS,)).fetchone()[0]
        if recent >= GLOBAL_PER_WINDOW:
            raise FeedbackError(
                "The inbox is taking no more reports this hour - try again later.")
        row = (uuid.uuid4().hex[:12], (user_id or "")[:MAX_FIELD], body,
               (screen or "")[:MAX_FIELD], (build or "")[:MAX_FIELD],
               (page or "")[:MAX_FIELD], (viewport or "")[:MAX_FIELD],
               (agent or "")[:MAX_FIELD * 2], at,
               json.dumps(episode) if episode else "")
        conn = self._conn()
        conn.execute(
            "INSERT INTO reports (id, user_id, text, screen, build, page,"
            " viewport, agent, created, episode)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
        return self.get(row[0])

    def _admit(self, key: str, at: float) -> bool:
        """Count one report against `key`, or refuse it. One lock, so two
        requests at once cannot both take the last slot."""
        with self._stamps_lock:
            if len(self._stamps) >= 512:
                # Forget sessions whose every report has aged out, so a
                # cookieless loop cannot grow this without bound.
                for stale in [k for k, v in self._stamps.items()
                              if not v or at - v[-1] >= WINDOW_SECONDS]:
                    del self._stamps[stale]
            kept = [t for t in self._stamps.get(key, []) if at - t < WINDOW_SECONDS]
            if len(kept) >= PER_LISTENER:
                self._stamps[key] = kept
                return False
            kept.append(at)
            self._stamps[key] = kept
            return True

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
