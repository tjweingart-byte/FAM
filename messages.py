"""Sending an episode to somebody, inside the app.

An **echo** (social.py) is a broadcast: you push an episode at everybody who
follows you and you do not choose who. A **share** is directed - one person,
one episode, deliberately - and it is the thing people actually do with
something they liked.

Both rest on the same property, which is what makes the whole social layer
affordable: **a share generates nothing.** It is a row pointing at a query
whose script already exists, so sending an episode to ten people costs ten rows
and not ten episodes. The recipient's tap is what synthesises audio, against
their own allowance, from the same cached script. That is the same design the
browse surfaces use and the reason the cost model survives a social feature at
all.

## Threads are between two listeners, and their id is derived

A thread id is the two user ids sorted and joined. Derived rather than
allocated, so:

* opening a conversation needs no write - the id is knowable from both sides
  before anything has been said;
* two people opening the same conversation simultaneously cannot create two
  threads, which is the classic bug in this shape and produces a split history
  that nobody can merge afterwards.

Group threads are not here. Not because they are hard, but because a group
changes what a share *means* - a share to a group is closer to an echo, and the
right time to decide that is when somebody wants one, not now.

## What a message may be

Three kinds, and the distinction is what the recipient's client renders:

* `episode` - an episode, carried as its query and length. Never as audio, and
  never as a script: the script lives in the shared cache and is fetched at
  play time, so a share stays one small row however long the episode is.
* `text` - what somebody typed alongside it.
* `system` - "Ana followed you", and anything else the app says on its own
  behalf. Kept in the same table so a thread is one ordered list rather than
  two that have to be interleaved on read.

## Reading, and what is deliberately not counted

`read_at` is stamped when a thread is opened, and the unread count is derived
from it. There are no delivery receipts and no typing indicators: both are
promises about somebody else's attention, and this app has a rule against
inventing state it does not have.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Optional

from paths import data_path

log = logging.getLogger(__name__)

MAX_TEXT = 1000
MAX_TITLE = 200
MAX_QUERY = 500

KINDS = ("episode", "text", "system")


#: Blank lines kept in a row, at most. A message may have paragraphs (§142 -
#: the keyboard's return key makes a new line now), but forty empty lines is
#: a wall, not a paragraph break.
MAX_BLANK_LINES = 2


def clean_text(text: str) -> str:
    """Tidy a typed message without flattening it.

    Spaces inside a line are collapsed as before; line breaks are kept,
    because a return key that made a new line in the box and then vanished on
    send would be the same dead key the owner reported, one step later.
    """
    lines = [" ".join(line.split()) for line in str(text or "").splitlines()]
    out: list[str] = []
    blanks = 0
    for line in lines:
        if line:
            blanks = 0
            out.append(line)
        elif out:
            blanks += 1
            if blanks <= MAX_BLANK_LINES:
                out.append("")
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)[:MAX_TEXT].rstrip()


class MessageError(ValueError):
    """Something the sender can fix, phrased so it can be shown to them."""


def thread_id(a: str, b: str) -> str:
    """The conversation between two listeners, derived from who they are.

    Sorted so that both sides compute the same string. This is the whole reason
    there is no threads table: an id nobody has to allocate cannot be allocated
    twice.
    """
    if not a or not b:
        raise MessageError("A conversation needs two people.")
    if a == b:
        raise MessageError("You cannot start a conversation with yourself.")
    return "|".join(sorted((a, b)))


@dataclass
class Message:
    id: int
    thread: str
    sender: str
    recipient: str
    kind: str
    text: str
    query: str
    minutes: int
    title: str
    at: float

    def as_dict(self, me: str = "") -> dict:
        return {
            "id": self.id,
            "thread": self.thread,
            # "mine" rather than the sender's id: the client needs to know
            # which side to draw the bubble on, and does not need somebody
            # else's listener id to do it.
            "mine": bool(me) and self.sender == me,
            "kind": self.kind,
            "text": self.text,
            "query": self.query,
            "minutes": self.minutes,
            "title": self.title,
            "at": self.at,
        }


class MessageStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = data_path("MESSAGES_DB", "messages.db", path)
        self._local = threading.local()
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS messages (
                       id        INTEGER PRIMARY KEY AUTOINCREMENT,
                       thread    TEXT NOT NULL,
                       sender    TEXT NOT NULL,
                       recipient TEXT NOT NULL,
                       kind      TEXT NOT NULL DEFAULT 'text',
                       text      TEXT NOT NULL DEFAULT '',
                       query     TEXT NOT NULL DEFAULT '',
                       minutes   INTEGER NOT NULL DEFAULT 0,
                       title     TEXT NOT NULL DEFAULT '',
                       at        REAL NOT NULL
                   )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS messages_thread"
                         " ON messages(thread, at)")
            conn.execute("CREATE INDEX IF NOT EXISTS messages_recipient"
                         " ON messages(recipient, at)")
            # When each person last opened each thread. One row per person per
            # thread rather than a flag on each message: marking a hundred
            # messages read is one write here and a hundred there.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS reads (
                       thread  TEXT NOT NULL,
                       user_id TEXT NOT NULL,
                       read_at REAL NOT NULL,
                       PRIMARY KEY (thread, user_id)
                   )"""
            )
            # "Delete chat" (§142): where one person's view of a thread now
            # starts. Stored as the highest message id they deleted, per
            # person per thread, so it hides the history for them and for
            # nobody else - the other side's conversation is theirs and is
            # untouched. A later message is above the mark, so writing to
            # them again opens what looks like a new thread.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS clears (
                       thread   TEXT NOT NULL,
                       user_id  TEXT NOT NULL,
                       after_id INTEGER NOT NULL,
                       PRIMARY KEY (thread, user_id)
                   )"""
            )

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    # --- sending ----------------------------------------------------------

    def send(self, sender: str, recipient: str, *, kind: str = "text",
             text: str = "", query: str = "", minutes: int = 0,
             title: str = "", at: float = 0.0) -> Message:
        """Put one message in a thread.

        An episode share carries the *query and the length* and nothing else.
        Not the script - that is in the shared cache, keyed on exactly those
        two things, so the recipient's play is a cache hit and the share cost
        one row. Not the audio, obviously: the whole product is that audio is
        made at play time.
        """
        if kind not in KINDS:
            raise MessageError(f"Unknown message kind {kind!r}.")
        thread = thread_id(sender, recipient)
        text = clean_text(text)
        query = " ".join(str(query or "").split())[:MAX_QUERY]
        title = " ".join(str(title or "").split())[:MAX_TITLE]
        if kind == "episode" and not query:
            raise MessageError("That episode has no question attached to it.")
        if kind == "text" and not text:
            raise MessageError("Nothing to send.")
        now = at or time.time()
        cur = self._conn().execute(
            "INSERT INTO messages (thread, sender, recipient, kind, text,"
            " query, minutes, title, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (thread, sender, recipient, kind, text, query,
             max(0, int(minutes or 0)), title, now),
        )
        return Message(cur.lastrowid, thread, sender, recipient, kind, text,
                       query, max(0, int(minutes or 0)), title, now)

    # --- reading ----------------------------------------------------------

    def thread(self, user_id: str, other_id: str, limit: int = 200,
               after_id: int = 0) -> list[Message]:
        """One conversation, oldest first - which is the order it is read in.

        `after_id` returns only what has arrived since that message, which is
        what makes an open conversation update on its own affordable. The
        client polls with the highest id it holds and almost always gets an
        empty list back, so keeping a chat live costs a primary-key comparison
        rather than the whole history every couple of seconds.

        The cursor is the **row id** rather than a timestamp. Two messages can
        share a timestamp - this app writes `time.time()`, not a monotonic
        counter - and a `> at` cursor silently drops the second of any such
        pair. An id is allocated by the database and cannot collide.
        """
        tid = thread_id(user_id, other_id)
        sql = ("SELECT id, thread, sender, recipient, kind, text, query,"
               " minutes, title, at FROM messages WHERE thread = ?")
        args: list = [tid]
        floor = max(int(after_id or 0), self.cleared_at(user_id, tid))
        if floor:
            sql += " AND id > ?"
            args.append(floor)
        # Newest-first with a LIMIT, then reversed: the limit has to cut the
        # *oldest* messages off a long conversation, not the newest.
        sql += " ORDER BY at DESC, id DESC LIMIT ?"
        args.append(int(limit))
        try:
            rows = self._conn().execute(sql, tuple(args)).fetchall()
        except Exception:
            log.exception("could not read a thread")
            return []
        return [Message(*r) for r in reversed(rows)]

    def arrived_for(self, user_id: str, after_id: int = 0,
                    limit: int = 20) -> list[Message]:
        """Messages sent *to* this listener since `after_id`, oldest first.

        What raises the drop-down. Deliberately narrower than the inbox: this
        answers "has anything new arrived", so it never includes what the
        listener sent themselves - a banner announcing your own message is
        the kind of thing that only looks obviously wrong after it ships.

        Read-state is not consulted. Whether a message has been *seen* is a
        question about a thread somebody opened; whether it has been
        *announced* is a question about a notification this client already
        raised, and the client's own cursor is the only thing that knows it.
        Mixing the two would mean opening a conversation silenced the banners
        for a different conversation.
        """
        if not user_id:
            return []
        try:
            rows = self._conn().execute(
                "SELECT m.id, m.thread, m.sender, m.recipient, m.kind, m.text,"
                " m.query, m.minutes, m.title, m.at FROM messages m"
                " LEFT JOIN clears c ON c.thread = m.thread AND c.user_id = ?"
                " WHERE m.recipient = ? AND m.id > ?"
                # Not a message from a chat this listener has deleted (§142):
                # a banner for it would open an empty conversation.
                "   AND m.id > COALESCE(c.after_id, 0)"
                " ORDER BY m.id ASC LIMIT ?",
                (user_id, user_id, int(after_id), int(limit)),
            ).fetchall()
        except Exception:
            log.exception("could not read new messages")
            return []
        return [Message(*r) for r in rows]

    def latest_id(self, user_id: str) -> int:
        """The newest message id addressed to this listener, or 0.

        The cursor a client starts from. Without it a listener opening the app
        would be shown a banner for every message ever sent to them, which is
        the standard way this feature is got wrong.
        """
        if not user_id:
            return 0
        try:
            row = self._conn().execute(
                "SELECT MAX(id) FROM messages WHERE recipient = ?",
                (user_id,)).fetchone()
        except Exception:
            log.exception("could not read the latest message id")
            return 0
        return int(row[0] or 0)

    def inbox(self, user_id: str, limit: int = 50) -> list[dict]:
        """Every conversation this listener is in, most recent first.

        One row per thread with its last message and unread count - what a
        message list shows. Assembled here rather than by the endpoint so that
        "what does a conversation look like in a list" has one answer.
        """
        try:
            rows = self._conn().execute(
                "SELECT m.thread, m.id, m.sender, m.recipient, m.kind, m.text,"
                "       m.query, m.minutes, m.title, m.at"
                "  FROM messages m"
                # The newest row by *id*, not by timestamp: two rows can
                # share an `at`, and joining on it could pick one from before
                # a Delete chat (§142). An id is unique and only grows.
                "  JOIN (SELECT x.thread, MAX(x.id) AS top FROM messages x"
                "          LEFT JOIN clears c"
                "            ON c.thread = x.thread AND c.user_id = ?"
                "         WHERE (x.sender = ? OR x.recipient = ?)"
                "           AND x.id > COALESCE(c.after_id, 0)"
                "         GROUP BY x.thread) t"
                "    ON t.thread = m.thread AND t.top = m.id"
                " ORDER BY m.at DESC, m.id DESC LIMIT ?",
                (user_id, user_id, user_id, int(limit)),
            ).fetchall()
        except Exception:
            log.exception("could not read the inbox")
            return []

        out = []
        seen = set()
        for r in rows:
            last = Message(r[1], r[0], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9])
            if last.thread in seen:
                continue  # defensive: the join is on a unique id, so one row a thread
            seen.add(last.thread)
            other = last.recipient if last.sender == user_id else last.sender
            out.append({
                "thread": last.thread,
                "with": other,
                "last": last.as_dict(user_id),
                "unread": self.unread_in(user_id, last.thread),
            })
        return out

    def unread_in(self, user_id: str, thread: str) -> int:
        try:
            row = self._conn().execute(
                "SELECT read_at FROM reads WHERE thread = ? AND user_id = ?",
                (thread, user_id)).fetchone()
            since = row[0] if row else 0.0
            return int(self._conn().execute(
                "SELECT COUNT(*) FROM messages WHERE thread = ?"
                " AND recipient = ? AND at > ? AND id > ?",
                (thread, user_id, since,
                 self.cleared_at(user_id, thread))).fetchone()[0])
        except Exception:
            log.exception("could not count unread messages")
            return 0

    def unread_total(self, user_id: str) -> int:
        """What the badge on the Messages button shows."""
        try:
            rows = self._conn().execute(
                "SELECT m.thread, COUNT(*) FROM messages m"
                " LEFT JOIN reads r ON r.thread = m.thread AND r.user_id = ?"
                " LEFT JOIN clears c ON c.thread = m.thread AND c.user_id = ?"
                " WHERE m.recipient = ? AND m.at > COALESCE(r.read_at, 0)"
                "   AND m.id > COALESCE(c.after_id, 0)"
                " GROUP BY m.thread", (user_id, user_id, user_id)).fetchall()
        except Exception:
            log.exception("could not count unread messages")
            return 0
        return sum(int(r[1]) for r in rows)

    def mark_read(self, user_id: str, other_id: str, at: float = 0.0) -> None:
        tid = thread_id(user_id, other_id)
        now = at or time.time()
        try:
            self._conn().execute(
                "INSERT INTO reads (thread, user_id, read_at) VALUES (?, ?, ?)"
                " ON CONFLICT (thread, user_id) DO UPDATE SET read_at = ?"
                " WHERE read_at < ?", (tid, user_id, now, now, now))
        except Exception:
            log.exception("could not mark a thread read")

    # --- deleting a chat, for one side (§142) -------------------------------

    def cleared_at(self, user_id: str, thread: str) -> int:
        """The id this listener's view of `thread` starts after, or 0."""
        try:
            row = self._conn().execute(
                "SELECT after_id FROM clears WHERE thread = ? AND user_id = ?",
                (thread, user_id)).fetchone()
        except Exception:
            log.exception("could not read a chat's clear mark")
            return 0
        return int(row[0]) if row else 0

    def clear(self, user_id: str, other_id: str) -> int:
        """Delete this conversation from `user_id`'s list, and only theirs.

        Nothing is removed from the table: the other person still has the
        whole conversation, and their messages are theirs. What moves is
        where this listener's view begins - after the newest message that
        exists now - so the thread leaves their inbox, stops counting as
        unread, and a message either of them sends later starts a fresh one.
        Returns the mark it set.
        """
        tid = thread_id(user_id, other_id)
        try:
            row = self._conn().execute(
                "SELECT MAX(id) FROM messages WHERE thread = ?",
                (tid,)).fetchone()
            top = int(row[0] or 0)
            self._conn().execute(
                "INSERT INTO clears (thread, user_id, after_id) VALUES (?, ?, ?)"
                " ON CONFLICT (thread, user_id) DO UPDATE SET"
                " after_id = MAX(after_id, excluded.after_id)",
                (tid, user_id, top))
            return top
        except Exception:
            log.exception("could not delete a chat for %r", user_id)
            raise MessageError("Could not delete that chat. Try again.")

    # --- housekeeping -----------------------------------------------------

    def forget(self, user_id: str) -> int:
        """Erase this listener from every conversation they were in.

        Their side of a thread goes; the other person's messages stay, because
        those are that person's words and not this one's data. What the
        remaining side sees is a conversation with somebody who is no longer
        there - which is what actually happened.
        """
        removed = 0
        try:
            cur = self._conn().execute(
                "DELETE FROM messages WHERE sender = ?", (user_id,))
            removed += cur.rowcount or 0
            cur = self._conn().execute(
                "DELETE FROM reads WHERE user_id = ?", (user_id,))
            removed += cur.rowcount or 0
            cur = self._conn().execute(
                "DELETE FROM clears WHERE user_id = ?", (user_id,))
            removed += cur.rowcount or 0
        except Exception:
            log.exception("could not erase messages for %r", user_id)
        return removed
