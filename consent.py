"""Asking before a listener's own words go to a third-party AI.

App Store guideline 5.1.2(i), since November 2025: an app must say, in the
app, where personal data is shared with a third-party AI, and get an explicit
yes before it goes. FAM sends a typed (or spoken) question, an attachment and
a Go Deeper question to Anthropic's API to write the episode. Those are the
listener's own words. Everything else FAM plays - a myFAM or DailyFAM tile, a
Trending story, an Explore replay - was written from FAM's own questions and
carries nothing of the person tapping it, so it needs no answer.

Three rules.

**The answer is the server's, keyed on the listener id**, guest or account,
because the server is what sends the words. A client asks once and posts the
answer; every client reads it back from `/api/consent`.

**The answer is kept with what was asked.** `VERSION` names the wording in
`AI_NOTICE`; a yes to an older wording is not a yes to a newer one, so
changing what is sent or who receives it means a new version and asking
again. A no is kept too, so withdrawing is as easy as agreeing.

**It costs no latency.** The question is asked once, before a first search,
never in front of an episode; the check on the generation path is a lookup in
memory after the first read.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from typing import Optional

from paths import data_path

#: The only scope today. A second recipient (another model provider) is a
#: second scope, asked separately, never folded into this one.
AI = "ai"
SCOPES = (AI,)

#: How many answers are held in memory before the map is dropped and read
#: again from disk; the lookup is a primary-key read either way.
CACHE_LIMIT = 50_000

#: Bump when `AI_NOTICE` changes what is sent or who receives it.
VERSION = 1

#: Who writes the episode from the words. Named in the notice because
#: 5.1.2(i) asks for the third party to be identified, not described. The
#: notice also names Exa, which receives the search the brief writes from
#: them (`research.py`) - PROBLEMS.md §223.
PROVIDER = "Anthropic"

#: What the listener is shown, in one place, so every client says the same
#: thing and the stored version always refers to these words.
AI_NOTICE = {
    "title": "FAM writes your episodes with AI",
    "body": ("To write an episode for something you ask, FAM sends what you "
             "type, say or attach, with the date and time where you are, to "
             "Anthropic, whose Claude model writes it, and a search drawn from "
             "it to Exa to find sources. A suggestion about where you live "
             "sends the place you set. Anthropic does not use any of it to "
             "train its models. Your name, email and account are never sent."),
    "unaffected": ("Without this you can still listen to myFAM, DailyFAM and "
                   "Explore, which FAM writes ahead from its own questions."),
    "allow": "Allow",
    "decline": "Not now",
}


class ConsentStore:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = data_path("CONSENT_DB", "consent.db", path)
        self._local = threading.local()
        self._cache: dict[tuple[str, str], Optional[dict]] = {}
        self._lock = threading.Lock()
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS consents (
                       user_id  TEXT NOT NULL,
                       scope    TEXT NOT NULL,
                       version  INTEGER NOT NULL,
                       allowed  INTEGER NOT NULL,
                       at       REAL NOT NULL,
                       client   TEXT NOT NULL DEFAULT '',
                       PRIMARY KEY (user_id, scope)
                   )""")

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def get(self, user_id: str, scope: str = AI) -> Optional[dict]:
        """The stored answer, or None when this listener was never asked."""
        if not user_id:
            return None
        key = (user_id, scope)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        row = self._conn().execute(
            "SELECT version, allowed, at FROM consents WHERE user_id = ? AND scope = ?",
            (user_id, scope)).fetchone()
        answer = (None if row is None else
                  {"version": int(row[0]), "allowed": bool(row[1]), "at": float(row[2])})
        with self._lock:
            # Bounded (§226): a guest session that never answered is not
            # remembered at all, and the whole map is dropped past a size
            # rather than growing with every session ever minted.
            if answer is not None:
                if len(self._cache) >= CACHE_LIMIT:
                    self._cache.clear()
                self._cache[key] = answer
        return answer

    def given(self, user_id: str, scope: str = AI) -> bool:
        """A yes to the current wording."""
        answer = self.get(user_id, scope)
        return bool(answer and answer["allowed"] and answer["version"] >= VERSION)

    def record(self, user_id: str, allowed: bool, *, scope: str = AI,
               version: int = VERSION, client: str = "") -> dict:
        if scope not in SCOPES:
            raise ValueError("Unknown consent: %r." % scope)
        if not user_id:
            raise ValueError("No listener to record an answer for.")
        now = time.time()
        self._conn().execute(
            "INSERT INTO consents (user_id, scope, version, allowed, at, client)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(user_id, scope) DO UPDATE SET version = excluded.version,"
            " allowed = excluded.allowed, at = excluded.at, client = excluded.client",
            (user_id, scope, int(version), 1 if allowed else 0, now, client[:80]))
        answer = {"version": int(version), "allowed": bool(allowed), "at": now}
        with self._lock:
            if len(self._cache) >= CACHE_LIMIT:
                self._cache.clear()
            self._cache[(user_id, scope)] = answer
        return answer

    def forget(self, user_id: str) -> int:
        """Account deletion (`app.erase_listener`)."""
        cur = self._conn().execute("DELETE FROM consents WHERE user_id = ?", (user_id,))
        with self._lock:
            for key in [k for k in self._cache if k[0] == user_id]:
                del self._cache[key]
        return cur.rowcount or 0


def describe(answer: Optional[dict]) -> dict:
    """The `/api/consent` block for the AI scope."""
    return {
        "scope": AI,
        "provider": PROVIDER,
        "version": VERSION,
        "notice": AI_NOTICE,
        "asked": answer is not None,
        "given": bool(answer and answer["allowed"] and answer["version"] >= VERSION),
        "answered_version": answer["version"] if answer else None,
        "at": answer["at"] if answer else None,
    }
