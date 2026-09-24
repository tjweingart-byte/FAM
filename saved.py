"""Save for later: a pointer to an episode, and nothing else.

A saved item is the *question*, the length and a title. It costs one row,
there is no sensible limit on it, and playing one works exactly like every
other episode in FAM - synthesised, or replayed from the shared cache, when it
is tapped.

## What used to be here, and why it is gone

There was a second thing beside it: **download**, the audio kept on the
device so it played with the network off. It was a state of a saved item
rather than a second list, the shelf had a per-tier capacity, and saving
raised a popup asking whether to download too.

All of it is removed, at the owner's direction. What it cost was the thing
this shelf is for: pressing save raised a question instead of saving, so the
one-tap action in the player was a two-tap action with a decision in the
middle. Save is now a toggle - press it, the icon turns green, the episode is
on the shelf; press it again and it is not. The same shape as VIBE!, for the
same reason.

The `downloaded`, `bytes` and `downloaded_at` columns stay in the schema and
are no longer read or written. Dropping a column is a migration with no
benefit, and an existing database is not worth rewriting to delete three
numbers nothing asks for. Nothing here can turn the feature back on: there is
no code path that sets them.

## What is still true

Saving is idempotent on `(question, length)`, which is also the script cache's
key - so two people saving the same episode are pointing at one script, and a
save costs a row rather than a generation.

Folders still exist and are still filed against, though nothing in the
interface currently offers them; that is why an episode filed before the chips
came off the shelf is unfiltered rather than lost.
"""
from __future__ import annotations

import logging
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Optional

from paths import data_path

log = logging.getLogger(__name__)

MAX_NAME = 40
MAX_TITLE = 200
MAX_QUERY = 500
#: How many folders one listener may have. Not a tier feature - it is a guard
#: against a script making a million of them, and nobody has forty folders.
MAX_FOLDERS = 40

#: Where a listened episode can have come from, as the history screen's tabs
#: name them (§142). "other" is everything else that is not Explore - a shared
#: episode, a vibe, a Go Deeper chip - and shows under All only. Explore is
#: deliberately absent: the owner asked that nothing heard there is kept.
HISTORY_SURFACES = ("myfam", "dailyfam", "search", "other")

#: Two weeks, at the owner's direction.
HISTORY_SECONDS = 14 * 86400


class SavedError(ValueError):
    """Something the listener can fix, phrased so it can be shown to them."""


def clean_name(name: str) -> str:
    name = " ".join(str(name or "").split())[:MAX_NAME]
    if not name:
        raise SavedError("Give the folder a name.")
    return name


@dataclass
class SavedItem:
    id: str
    user_id: str
    folder_id: str
    query: str
    minutes: int
    title: str
    source: str
    created: float
    last_played: float

    def as_dict(self) -> dict:
        return {
            "id": self.id, "folder_id": self.folder_id, "query": self.query,
            "minutes": self.minutes, "title": self.title, "source": self.source,
            "created": self.created, "last_played": self.last_played,
        }


class SavedStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = data_path("SAVED_DB", "saved.db", path)
        self._local = threading.local()
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS folders (
                       id      TEXT PRIMARY KEY,
                       user_id TEXT NOT NULL,
                       name    TEXT NOT NULL,
                       created REAL NOT NULL
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS folders_user"
                         " ON folders(user_id, created)")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS items (
                       id            TEXT PRIMARY KEY,
                       user_id       TEXT NOT NULL,
                       folder_id     TEXT NOT NULL DEFAULT '',
                       query         TEXT NOT NULL,
                       minutes       INTEGER NOT NULL DEFAULT 0,
                       title         TEXT NOT NULL DEFAULT '',
                       source        TEXT NOT NULL DEFAULT '',
                       created       REAL NOT NULL,
                       -- Written by nothing. Three columns left from the
                       -- download feature, kept because dropping a column is
                       -- a migration with no benefit and an existing shelf is
                       -- not worth rewriting to delete them.
                       downloaded    INTEGER NOT NULL DEFAULT 0,
                       bytes         INTEGER NOT NULL DEFAULT 0,
                       downloaded_at REAL NOT NULL DEFAULT 0,
                       last_played   REAL NOT NULL DEFAULT 0
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS items_user"
                         " ON items(user_id, created)")
            # Saving the same episode twice is the same statement twice. The
            # unique key is (question, length) because that is also the script
            # cache's key - two saves that differ only in something the cache
            # ignores are one episode.
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS items_once"
                         " ON items(user_id, query, minutes)")
            # How far through an episode somebody got, so "Pick up where you
            # left off" follows the *account* rather than the phone (§127).
            # It used to be localStorage, which is per-device by nature - and
            # so outlived a log-out and showed the next person on that phone
            # somebody else's half-heard episodes. One row per episode, the
            # same (question, length) identity the shelf and the cache use.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS progress (
                       user_id TEXT NOT NULL,
                       query   TEXT NOT NULL,
                       minutes INTEGER NOT NULL,
                       seconds REAL NOT NULL,
                       title   TEXT NOT NULL DEFAULT '',
                       updated REAL NOT NULL,
                       PRIMARY KEY (user_id, query, minutes)
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS progress_user"
                         " ON progress(user_id, updated)")
            # The topic a follow-up was asked from. Part of the episode's
            # cache key, so a resumed follow-up without it would be a
            # different episode - and its card would find no title. Added as
            # a column for a table created before it existed.
            try:
                conn.execute("ALTER TABLE progress ADD COLUMN context"
                             " TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # already there
            # Recent listening history (§142): every episode this account
            # started, on which surface, for two weeks. A pointer like
            # everything else here - the question, the length, the title -
            # so replaying one is an ordinary episode and, while the script
            # is still cached, a cache hit. One row per episode per surface:
            # hearing the same thing twice moves it to the top rather than
            # listing it twice.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS history (
                       user_id TEXT NOT NULL,
                       query   TEXT NOT NULL,
                       minutes INTEGER NOT NULL,
                       surface TEXT NOT NULL,
                       title   TEXT NOT NULL DEFAULT '',
                       context TEXT NOT NULL DEFAULT '',
                       at      REAL NOT NULL,
                       -- `context` is in the key because it is in the
                       -- episode's cache key: two follow-ups asked in the
                       -- same words from different topics are different
                       -- episodes, and merging them would replay the wrong one.
                       PRIMARY KEY (user_id, query, minutes, context, surface)
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS history_user"
                         " ON history(user_id, at)")
            # Go Deeper tiles the listener closed with their X. Keyed on the
            # question alone, not the length: "not shown again" is about the
            # subject, and the same question offered back at another length
            # would read as the X not having worked.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS dismissed (
                       user_id TEXT NOT NULL,
                       query   TEXT NOT NULL,
                       at      REAL NOT NULL,
                       PRIMARY KEY (user_id, query)
                   )"""
            )

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    # --- folders ----------------------------------------------------------

    def folders(self, user_id: str) -> list[dict]:
        """The listener's folders, with a count each.

        There is no "All" row and no implicit default folder in the table: an
        item with an empty `folder_id` is simply unfiled, and the interface
        shows those first. A real default folder would need creating on first
        use, which is a write on a read path and a row for people who never
        make a folder at all.
        """
        try:
            rows = self._conn().execute(
                "SELECT f.id, f.name, f.created, COUNT(i.id)"
                " FROM folders f LEFT JOIN items i ON i.folder_id = f.id"
                " WHERE f.user_id = ? GROUP BY f.id ORDER BY f.created",
                (user_id,)).fetchall()
        except Exception:
            log.exception("could not read folders")
            return []
        return [{"id": r[0], "name": r[1], "created": r[2], "items": int(r[3])}
                for r in rows]

    def create_folder(self, user_id: str, name: str, at: float = 0.0) -> dict:
        name = clean_name(name)
        existing = self.folders(user_id)
        if len(existing) >= MAX_FOLDERS:
            raise SavedError(f"That is the most folders one listener can have "
                             f"({MAX_FOLDERS}). Rename or remove one first.")
        if any(f["name"].lower() == name.lower() for f in existing):
            raise SavedError(f"You already have a folder called {name}.")
        folder_id = "fld_" + secrets.token_urlsafe(8)
        now = at or time.time()
        self._conn().execute(
            "INSERT INTO folders (id, user_id, name, created) VALUES (?, ?, ?, ?)",
            (folder_id, user_id, name, now))
        return {"id": folder_id, "name": name, "created": now, "items": 0}

    def rename_folder(self, user_id: str, folder_id: str, name: str) -> dict:
        name = clean_name(name)
        cur = self._conn().execute(
            "UPDATE folders SET name = ? WHERE id = ? AND user_id = ?",
            (name, folder_id, user_id))
        if not cur.rowcount:
            raise SavedError("No such folder.")
        return {"id": folder_id, "name": name}

    def delete_folder(self, user_id: str, folder_id: str) -> int:
        """Remove a folder. **Its episodes are unfiled, not deleted.**

        Deleting somebody's saved episodes because they tidied up their folders
        is the kind of surprise that stops people using a feature at all.
        """
        moved = self._conn().execute(
            "UPDATE items SET folder_id = '' WHERE folder_id = ? AND user_id = ?",
            (folder_id, user_id)).rowcount or 0
        self._conn().execute("DELETE FROM folders WHERE id = ? AND user_id = ?",
                             (folder_id, user_id))
        return moved

    # --- saving -----------------------------------------------------------

    def save(self, user_id: str, query: str, minutes: int, *, title: str = "",
             source: str = "", folder_id: str = "", at: float = 0.0) -> SavedItem:
        """Save an episode for later. Idempotent on (question, length).

        Saving something already saved moves it into the folder given rather
        than failing: from the listener's side they pressed save and it is
        saved, which is true either way.
        """
        query = " ".join(str(query or "").split())[:MAX_QUERY]
        if not query:
            raise SavedError("There is no episode to save.")
        minutes = max(0, int(minutes or 0))
        title = " ".join(str(title or "").split())[:MAX_TITLE]
        folder_id = self._checked_folder(user_id, folder_id)
        now = at or time.time()

        existing = self.find(user_id, query, minutes)
        if existing:
            if folder_id and folder_id != existing.folder_id:
                self._conn().execute(
                    "UPDATE items SET folder_id = ? WHERE id = ?",
                    (folder_id, existing.id))
                return self.item(user_id, existing.id)
            return existing

        item_id = "sav_" + secrets.token_urlsafe(8)
        self._conn().execute(
            "INSERT INTO items (id, user_id, folder_id, query, minutes, title,"
            " source, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (item_id, user_id, folder_id, query, minutes, title, source[:40], now))
        return self.item(user_id, item_id)

    def _checked_folder(self, user_id: str, folder_id: str) -> str:
        if not folder_id:
            return ""
        row = self._conn().execute(
            "SELECT 1 FROM folders WHERE id = ? AND user_id = ?",
            (folder_id, user_id)).fetchone()
        if not row:
            raise SavedError("No such folder.")
        return folder_id

    def find(self, user_id: str, query: str, minutes: int) -> Optional[SavedItem]:
        row = self._conn().execute(
            "SELECT id FROM items WHERE user_id = ? AND query = ? AND minutes = ?",
            (user_id, query, int(minutes))).fetchone()
        return self.item(user_id, row[0]) if row else None

    def item(self, user_id: str, item_id: str) -> Optional[SavedItem]:
        try:
            row = self._conn().execute(
                "SELECT id, user_id, folder_id, query, minutes, title, source,"
                " created, last_played"
                " FROM items WHERE id = ? AND user_id = ?",
                (item_id, user_id)).fetchone()
        except Exception:
            log.exception("could not read a saved item")
            return None
        if not row:
            return None
        return SavedItem(*row)

    def items(self, user_id: str, folder_id: Optional[str] = None,
              limit: int = 500) -> list[SavedItem]:
        sql = ("SELECT id, user_id, folder_id, query, minutes, title, source,"
               " created, last_played"
               " FROM items WHERE user_id = ?")
        args: list = [user_id]
        if folder_id is not None:
            sql += " AND folder_id = ?"
            args.append(folder_id)
        sql += " ORDER BY created DESC LIMIT ?"
        args.append(int(limit))
        try:
            rows = self._conn().execute(sql, tuple(args)).fetchall()
        except Exception:
            log.exception("could not read saved items")
            return []
        return [SavedItem(*r) for r in rows]

    def remove(self, user_id: str, item_id: str) -> bool:
        """Unsave."""
        cur = self._conn().execute("DELETE FROM items WHERE id = ? AND user_id = ?",
                                   (item_id, user_id))
        return bool(cur.rowcount)

    def unsave(self, user_id: str, query: str, minutes: int) -> bool:
        """Unsave by what the episode *is* rather than by row id.

        The save control in the player is a toggle, and the player knows the
        question and the length - the same pair that is the script cache's key.
        It does not know a row id, and making it fetch one before it could
        un-press a button would put a round trip in front of the second tap
        that the first tap did not pay.
        """
        query = " ".join(str(query or "").split())[:MAX_QUERY]
        cur = self._conn().execute(
            "DELETE FROM items WHERE user_id = ? AND query = ? AND minutes = ?",
            (user_id, query, max(0, int(minutes or 0))))
        return bool(cur.rowcount)

    def move(self, user_id: str, item_id: str, folder_id: str) -> Optional[SavedItem]:
        folder_id = self._checked_folder(user_id, folder_id)
        self._conn().execute(
            "UPDATE items SET folder_id = ? WHERE id = ? AND user_id = ?",
            (folder_id, item_id, user_id))
        return self.item(user_id, item_id)

    def played(self, user_id: str, item_id: str, at: float = 0.0) -> None:
        """Note that a saved episode was played, so the "what to clear" list
        can offer the ones nobody has been back to."""
        self._conn().execute(
            "UPDATE items SET last_played = ? WHERE id = ? AND user_id = ?",
            (at or time.time(), item_id, user_id))

    # --- where somebody got to --------------------------------------------

    #: Heard less than this and it was not really started; nearer the end than
    #: `RESUME_TAIL` and it was finished. Both mean "nothing to resume", and a
    #: card offering the last four seconds of something is clutter.
    RESUME_HEAD = 20.0
    RESUME_TAIL = 30.0

    def note_progress(self, user_id: str, query: str, minutes: int,
                      seconds: float, title: str = "", at: float = 0.0,
                      context: str = "") -> bool:
        """Record how far through an episode this listener is.

        Returns True when a position is being kept and False when the episode
        counts as not started or finished - in which case any old position is
        removed, so a finished episode stops being offered as unfinished.
        """
        query = " ".join(str(query or "").split())[:MAX_QUERY]
        minutes = int(minutes or 0)
        if not user_id or not query or minutes <= 0:
            return False
        total = minutes * 60.0
        seconds = max(0.0, float(seconds or 0))
        try:
            if seconds < self.RESUME_HEAD or seconds > total - self.RESUME_TAIL:
                self._conn().execute(
                    "DELETE FROM progress WHERE user_id = ? AND query = ?"
                    " AND minutes = ?", (user_id, query, minutes))
                return False
            self._conn().execute(
                "INSERT INTO progress (user_id, query, minutes, seconds, title,"
                " updated, context) VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(user_id, query, minutes) DO UPDATE SET"
                "  seconds = excluded.seconds, updated = excluded.updated,"
                "  context = excluded.context,"
                "  title = CASE WHEN excluded.title != '' THEN excluded.title"
                "               ELSE progress.title END",
                (user_id, query, minutes, seconds,
                 " ".join(str(title or "").split())[:MAX_TITLE],
                 at or time.time(), str(context or "")[:300]))
            return True
        except Exception:
            log.exception("could not record progress for %r", user_id)
            return False

    def progress(self, user_id: str, limit: int = 4) -> list[dict]:
        """Part-heard episodes, most recently listened to first."""
        if not user_id:
            return []
        try:
            rows = self._conn().execute(
                "SELECT query, minutes, seconds, title, updated, context"
                " FROM progress"
                " WHERE user_id = ? ORDER BY updated DESC LIMIT ?",
                (user_id, int(limit))).fetchall()
        except Exception:
            log.exception("could not read progress for %r", user_id)
            return []
        return [{"query": r[0], "minutes": int(r[1]), "seconds": float(r[2]),
                 "title": r[3] or "", "at": r[4], "context": r[5] or ""}
                for r in rows]

    # --- Go Deeper tiles closed with their X ---------------------------------

    @staticmethod
    def _dismiss_key(query: str) -> str:
        return " ".join((query or "").lower().split())

    def dismiss(self, user_id: str, query: str, at: float = 0.0) -> bool:
        """Never offer this question under "Pick up where you left off" again."""
        key = self._dismiss_key(query)
        if not user_id or not key:
            return False
        self._conn().execute(
            "INSERT OR REPLACE INTO dismissed (user_id, query, at) VALUES (?, ?, ?)",
            (user_id, key, at or time.time()))
        return True

    def dismissed(self, user_id: str) -> set[str]:
        if not user_id:
            return set()
        try:
            rows = self._conn().execute(
                "SELECT query FROM dismissed WHERE user_id = ?", (user_id,)).fetchall()
        except Exception:
            log.exception("could not read dismissed tiles for %r", user_id)
            return set()
        return {r[0] for r in rows}

    def is_dismissed(self, hidden: set[str], query: str) -> bool:
        return self._dismiss_key(query) in hidden

    # --- what somebody has listened to (§142) ------------------------------

    def note_listen(self, user_id: str, query: str, minutes: int,
                    surface: str, title: str = "", context: str = "",
                    at: float = 0.0) -> bool:
        """Put an episode at the top of this listener's history.

        `surface` must be one of `HISTORY_SURFACES`; anything else - Explore
        above all, which the owner asked to be left out - is refused here
        rather than trusted to every caller. A title arriving later (the
        writer names an episode after its first word is playing) replaces an
        empty or provisional one without moving the row.
        """
        query = " ".join(str(query or "").split())[:MAX_QUERY]
        minutes = int(minutes or 0)
        if (not user_id or not query or minutes <= 0
                or surface not in HISTORY_SURFACES):
            return False
        now = at or time.time()
        title = " ".join(str(title or "").split())[:MAX_TITLE]
        try:
            self._conn().execute(
                "INSERT INTO history (user_id, query, minutes, surface, title,"
                " context, at) VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(user_id, query, minutes, context, surface) DO UPDATE SET"
                "  at = excluded.at,"
                "  title = CASE WHEN excluded.title != '' THEN excluded.title"
                "               ELSE history.title END",
                (user_id, query, minutes, surface, title,
                 str(context or "")[:300], now))
            # Kept for two weeks and no longer, pruned on write so the table
            # never needs a job of its own to stay that size.
            self._conn().execute(
                "DELETE FROM history WHERE user_id = ? AND at < ?",
                (user_id, now - HISTORY_SECONDS))
            return True
        except Exception:
            log.exception("could not record history for %r", user_id)
            return False

    def retitle(self, user_id: str, query: str, minutes: int,
                title: str, context: str = "") -> None:
        """The writer's own title arrived; use it on every row for this
        episode without moving any of them."""
        query = " ".join(str(query or "").split())[:MAX_QUERY]
        title = " ".join(str(title or "").split())[:MAX_TITLE]
        if not user_id or not query or not title:
            return
        try:
            self._conn().execute(
                "UPDATE history SET title = ? WHERE user_id = ? AND query = ?"
                " AND minutes = ? AND context = ?",
                (title, user_id, query, int(minutes or 0), str(context or "")[:300]))
        except Exception:
            log.exception("could not retitle history for %r", user_id)

    def history(self, user_id: str, surface: str = "", limit: int = 200,
                now: float = 0.0) -> list[dict]:
        """The last two weeks, most recent first; one surface or all."""
        if not user_id:
            return []
        since = (now or time.time()) - HISTORY_SECONDS
        sql = ("SELECT query, minutes, surface, title, context, at FROM history"
               " WHERE user_id = ? AND at >= ?")
        args: list = [user_id, since]
        if surface:
            if surface not in HISTORY_SURFACES:
                return []
            sql += " AND surface = ?"
            args.append(surface)
        sql += " ORDER BY at DESC LIMIT ?"
        args.append(int(limit))
        try:
            rows = self._conn().execute(sql, tuple(args)).fetchall()
        except Exception:
            log.exception("could not read history for %r", user_id)
            return []
        return [{"query": r[0], "minutes": int(r[1]), "surface": r[2],
                 "title": r[3] or "", "context": r[4] or "", "at": r[5]}
                for r in rows]

    # --- housekeeping -----------------------------------------------------

    def forget(self, user_id: str) -> int:
        removed = 0
        for table in ("items", "folders", "progress", "history", "dismissed"):
            try:
                cur = self._conn().execute(
                    f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
                removed += cur.rowcount or 0
            except Exception:
                log.exception("could not erase %s for %r", table, user_id)
        return removed
