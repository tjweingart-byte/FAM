"""The story pool, kept on the data disk (§265).

`stories._POOL` and the ledgers beside it (`_FIRST_SEEN`, `_RETIRED`) lived in
memory only, so every redeploy dropped them: the first listener after a push
met a pool the warming sweep had not refilled yet, every held story was
composed again (a model call each), and - the part a rule is written about -
every story's clock started again. `first_seen` is what ages a story, and
**an eviction that forgets is a creation** (`tile-title-angle`): a restart
that forgot the ledger turned yesterday's story into a new one.

So the pool is saved after every refresh and read back at boot. One row per
thing, as JSON: the pool is a few hundred kilobytes at most (`POOL_STORE`
stories) and is always read and written whole, so a table per field would be
ceremony. Opened through `data_path` and `db.connect` like every store
(`storage-durability`), so it is on the mounted disk today and in Postgres
when `DATABASE_URL` is set - which is what lets more than one instance share it.

Never load-bearing: a store that cannot be read or written is logged and the
pool carries on in memory exactly as it did before this existed.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from contextlib import closing
from typing import Optional

import db as store_db
from paths import data_path

log = logging.getLogger(__name__)

#: The rows this store holds.
POOL = "pool"
LEDGERS = "ledgers"


class StoryStore:
    """The saved pool and its ledgers. One JSON payload per name."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = data_path("STORY_POOL_DB", "story_pool.db", path)
        self._lock = threading.Lock()
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS snapshots (
                name TEXT PRIMARY KEY, saved_at REAL NOT NULL,
                payload TEXT NOT NULL)""")

    def _connect(self) -> sqlite3.Connection:
        return store_db.connect(self.path, timeout=10)

    def put(self, name: str, payload: dict, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        text = json.dumps(payload)
        with self._lock, closing(self._connect()) as db, db:
            db.execute("DELETE FROM snapshots WHERE name=?", (name,))
            db.execute("INSERT INTO snapshots (name, saved_at, payload) "
                       "VALUES (?, ?, ?)", (name, now, text))

    def get(self, name: str) -> Optional[dict]:
        with closing(self._connect()) as db:
            row = db.execute("SELECT payload FROM snapshots WHERE name=?",
                             (name,)).fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row[0])
        except (TypeError, ValueError):
            log.warning("story store: the saved %s could not be read; ignored",
                        name)
            return None
        return data if isinstance(data, dict) else None

    def clear(self) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute("DELETE FROM snapshots")


_STORE: list = [None]


def store() -> StoryStore:
    if _STORE[0] is None:
        _STORE[0] = StoryStore()
    return _STORE[0]


def reset_store() -> None:
    """Forget the open store, so a test's `STORY_POOL_DB` is the one used."""
    _STORE[0] = None


def exists() -> bool:
    # Its file, or its schema when the stores are in Postgres (§243).
    return store_db.has_tables(data_path("STORY_POOL_DB", "story_pool.db"))
