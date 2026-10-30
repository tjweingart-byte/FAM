"""The task table: background agent work, claimed so it runs once (§349).

Netenyahu's background work (Consuela's consolidation, dedupe and re-filing;
later Keith's sweeps) must never run twice at once across two servers, never
be lost by a restart, and never repeat with nothing new to work on
(docs/agent_rules/netenyahu.md, "never" 5). The pattern is
`trending_bank.BankStore.claim`'s, which already keeps one edition builder
per slot across workers:

* a task is `agent:kind:key`, queued with a hash of its input;
* `claim` gives it to exactly one caller, in one `BEGIN IMMEDIATE`
  transaction; a claim that went quiet for `STALE_SECONDS` is a crashed
  worker and may be taken over, a live one never;
* a failure is retried once (`MAX_ATTEMPTS`), then the task is **held** -
  held for Netenyahu to report, never retried by itself;
* queueing a task whose input is the same as the run that already finished
  is **nothing new**: it is refused, so a loop (digest triggers dedupe
  triggers digest) stops at the second turn rather than spending forever.

Holds of a whole agent live here too, with why and when; releasing one is
the owner's act, not an agent's. The store holds no listener and no
material - task keys are ids and hashes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
import uuid
from typing import Optional

import db as store_db
from paths import data_path

log = logging.getLogger("fam.agents")

QUEUED, RUNNING, DONE, FAILED, HELD = "queued", "running", "done", "failed", "held"
#: A running claim this quiet is a crashed worker (the trending bank's rule).
STALE_SECONDS = 900.0
#: One run and one retry (the Cost rules: at most one retry).
MAX_ATTEMPTS = 2

#: What `add` says.
ADDED, ALREADY, NOTHING_NEW = "added", "already queued", "nothing new"


def input_hash(data) -> str:
    """A task's input, as a hash: what decides whether a re-run is new."""
    blob = data if isinstance(data, str) else json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


class TaskStore:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = data_path("AGENTS_DB", "agents.db", path)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, agent TEXT NOT NULL, kind TEXT NOT NULL,
                key TEXT NOT NULL, status TEXT NOT NULL, input_hash TEXT NOT NULL,
                done_hash TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, claimed_at REAL NOT NULL DEFAULT 0,
                finished_at REAL NOT NULL DEFAULT 0, detail TEXT NOT NULL DEFAULT '',
                claim TEXT NOT NULL DEFAULT '')""")
            try:   # a store made before claims carried a token (§353)
                db.execute("ALTER TABLE tasks ADD COLUMN claim TEXT NOT NULL DEFAULT ''")
            except Exception:  # noqa: BLE001 - the column is already there
                pass
            db.execute("CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status, created_at)")
            db.execute("""CREATE TABLE IF NOT EXISTS holds (
                id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT NOT NULL,
                reason TEXT NOT NULL, at REAL NOT NULL,
                released_at REAL NOT NULL DEFAULT 0)""")

    def _connect(self) -> sqlite3.Connection:
        conn = store_db.connect(self.path, timeout=10, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ------------------------------------------------------------ tasks
    def add(self, agent: str, kind: str, key: str, data, now: float = 0.0) -> str:
        """Queue a task. `NOTHING_NEW` when its last finished run had this
        same input - the loop rule."""
        now = now or time.time()
        task = f"{agent}:{kind}:{key}"
        digest = input_hash(data)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT status, done_hash FROM tasks WHERE id = ?",
                                 (task,)).fetchone()
                if row is None:
                    db.execute("INSERT INTO tasks (id, agent, kind, key, status, input_hash,"
                               " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (task, agent, kind, key, QUEUED, digest, now))
                    db.execute("COMMIT")
                    return ADDED
                status, done_hash = row
                if status in (QUEUED, RUNNING):
                    db.execute("ROLLBACK")
                    return ALREADY
                if status == HELD or done_hash == digest:
                    db.execute("ROLLBACK")
                    return NOTHING_NEW
                db.execute("UPDATE tasks SET status = ?, input_hash = ?, attempts = 0,"
                           " created_at = ?, claimed_at = 0, detail = '' WHERE id = ?",
                           (QUEUED, digest, now, task))
                db.execute("COMMIT")
                return ADDED
            except Exception:
                db.execute("ROLLBACK")
                raise

    def claim(self, task: str, now: float = 0.0) -> str:
        """Take the right to run `task`: a claim token for exactly one
        caller, `""` for everyone else. Only that token can then touch,
        finish or fail the task, so a slow worker whose claim was taken over
        cannot overwrite the run that took it (§353). A crashed claim is
        taken over only while a retry is left; after that the task is held."""
        now = now or time.time()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT status, claimed_at, attempts FROM tasks"
                                 " WHERE id = ?", (task,)).fetchone()
                if row is None:
                    db.execute("ROLLBACK")
                    return False
                status, claimed_at, attempts = row
                quiet = now - float(claimed_at or 0.0)
                stale = status == RUNNING and quiet >= STALE_SECONDS
                if stale and attempts >= MAX_ATTEMPTS:
                    db.execute("UPDATE tasks SET status = ?, finished_at = ?, claim = '',"
                               " detail = ? WHERE id = ?",
                               (HELD, now, "its worker stopped answering on every try", task))
                    db.execute("COMMIT")
                    return ""
                allowed = ((status == QUEUED)
                           or (status == FAILED and attempts < MAX_ATTEMPTS)
                           or stale)
                if not allowed:
                    db.execute("ROLLBACK")
                    return ""
                token = uuid.uuid4().hex
                db.execute("UPDATE tasks SET status = ?, claimed_at = ?, claim = ?,"
                           " attempts = attempts + 1 WHERE id = ?", (RUNNING, now, token, task))
                db.execute("COMMIT")
                return token
            except Exception:
                db.execute("ROLLBACK")
                raise

    @staticmethod
    def _mine(token: str) -> tuple:
        """The WHERE clause that keeps a write to the claim it came from."""
        return (" AND status = ? AND claim = ?", (RUNNING, token)) if token else ("", ())

    def touch(self, task: str, now: float = 0.0, token: str = "") -> bool:
        """A worker saying it is still alive, so a long run is not taken over.
        False when the claim is no longer this worker's."""
        where, args = self._mine(token) if token else (" AND status = ?", (RUNNING,))
        with self._lock, self._connect() as db:
            cur = db.execute("UPDATE tasks SET claimed_at = ? WHERE id = ?" + where,
                             (now or time.time(), task, *args))
            return bool(cur.rowcount)

    def finish(self, task: str, detail: str = "", now: float = 0.0, token: str = "") -> bool:
        """Mark it done. False, and nothing written, when `token` is given and
        the claim was taken over."""
        where, args = self._mine(token)
        with self._lock, self._connect() as db:
            cur = db.execute("UPDATE tasks SET status = ?, done_hash = input_hash,"
                             " finished_at = ?, detail = ?, claim = '' WHERE id = ?" + where,
                             (DONE, now or time.time(), str(detail)[:500], task, *args))
            return bool(cur.rowcount)

    def fail(self, task: str, detail: str = "", now: float = 0.0, token: str = "") -> str:
        """Record a failure: `FAILED` (one retry left) or `HELD` (no more);
        `""` when `token` is given and the claim was taken over."""
        where, args = self._mine(token)
        with self._lock, self._connect() as db:
            row = db.execute("SELECT attempts FROM tasks WHERE id = ?" + where,
                             (task, *args)).fetchone()
            if row is None and token:
                return ""
            status = HELD if row is not None and row[0] >= MAX_ATTEMPTS else FAILED
            db.execute("UPDATE tasks SET status = ?, finished_at = ?, detail = ?, claim = ''"
                       " WHERE id = ?" + where,
                       (status, now or time.time(), str(detail)[:500], task, *args))
            return status

    def due(self, limit: int, now: float = 0.0, kinds=None) -> list:
        """Tasks a worker may claim now, oldest first. `kinds`, when given,
        is the `agent:kind` pairs a worker can run: anything else is left
        out here rather than skipped later, so tasks nobody can run never
        fill a tick's share and starve the rest (§353)."""
        now = now or time.time()
        sql = ("SELECT id FROM tasks WHERE (status = ? OR (status = ? AND attempts < ?)"
               " OR (status = ? AND ? - claimed_at >= ?))")
        args = [QUEUED, FAILED, MAX_ATTEMPTS, RUNNING, now, STALE_SECONDS]
        if kinds is not None:
            kinds = sorted(kinds)
            if not kinds:
                return []
            sql += f" AND agent || ':' || kind IN ({', '.join('?' for _ in kinds)})"
            args += kinds
        with self._connect() as db:
            rows = db.execute(sql + " ORDER BY created_at LIMIT ?",
                              (*args, int(limit))).fetchall()
        return [r[0] for r in rows]

    def get(self, task: str) -> Optional[dict]:
        columns = ("id", "agent", "kind", "key", "status", "input_hash", "done_hash",
                   "attempts", "created_at", "claimed_at", "finished_at", "detail")
        with self._connect() as db:
            row = db.execute(f"SELECT {', '.join(columns)} FROM tasks WHERE id = ?",
                             (task,)).fetchone()
        return dict(zip(columns, row)) if row else None

    def counts(self) -> dict:
        with self._connect() as db:
            return {status: n for status, n in db.execute(
                "SELECT status, COUNT(*) FROM tasks GROUP BY status")}

    def forget_tasks(self) -> int:
        """A wipe: the queue is derived from what the wipe empties (§349)."""
        with self._lock, self._connect() as db:
            cur = db.execute("DELETE FROM tasks")
            return cur.rowcount or 0

    # ------------------------------------------------------------ holds
    def hold(self, agent: str, reason: str, now: float = 0.0) -> None:
        with self._lock, self._connect() as db:
            if db.execute("SELECT 1 FROM holds WHERE agent = ? AND released_at = 0",
                          (agent,)).fetchone():
                return
            db.execute("INSERT INTO holds (agent, reason, at) VALUES (?, ?, ?)",
                       (agent, str(reason)[:300], now or time.time()))

    def held(self) -> dict:
        """`{agent: (reason, since)}` for every hold not released."""
        with self._connect() as db:
            return {agent: (reason, at) for agent, reason, at in db.execute(
                "SELECT agent, reason, at FROM holds WHERE released_at = 0 ORDER BY at")}

    def release(self, agent: str, now: float = 0.0) -> int:
        """The owner's act, from /admin when that exists - never an agent's."""
        with self._lock, self._connect() as db:
            cur = db.execute("UPDATE holds SET released_at = ? WHERE agent = ?"
                             " AND released_at = 0", (now or time.time(), agent))
            return cur.rowcount or 0


_STORE: list = [None]


def store() -> TaskStore:
    if _STORE[0] is None:
        _STORE[0] = TaskStore()
    return _STORE[0]


def reset(task_store: Optional[TaskStore] = None) -> None:
    _STORE[0] = task_store


def exists() -> bool:
    """Whether the store has been created - by the first task. A health
    report must not be what creates it (the trending bank's rule, §139).
    Asked of the database, not the disk: under Postgres there is no file, and
    a file check said "no store" after every restart, so holds were forgotten
    on deploy (§353)."""
    return _STORE[0] is not None or store_db.has_tables(data_path("AGENTS_DB", "agents.db"))
