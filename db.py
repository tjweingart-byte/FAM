"""Every store's connection: SQLite as before, or Postgres when DATABASE_URL is set.

Option B (PROBLEMS.md §243). FAM's stores were written against `sqlite3`, one
file each, and a service with a disk runs as one instance on Render - so the
data had to move to a database server before FAM could run on more than one.
This module is the whole of that move as far as a store is concerned: a store
asks `db.connect(path)` where it used to ask `sqlite3.connect(path)`, and gets

* **with no `DATABASE_URL`** - exactly `sqlite3.connect(path, ...)`. Nothing
  changes for a laptop, a test run or a deployment that has not moved.
* **with `DATABASE_URL`** - a connection to Postgres that answers like a
  sqlite3 connection for the subset of SQLite FAM uses, so the 600-odd
  queries in the stores did not have to be rewritten (measured on a trial
  port: 3,513 of 4,063 tests passed before any store changed, §241's
  analysis). What does not translate is fixed in the store, in SQL both
  databases accept, so the two never drift.

How a store maps onto Postgres:

* **One schema per store file**, named after the file and the directory it
  is in (`messages_<8 hex>`): three pairs of stores reuse a table name
  (`reports`, `items`, `spend`), so one namespace would not hold them, and a
  test's temporary directory gets its own schemas for free.
* **One connection per store per thread**, its `search_path` set once to the
  store's schema - the same shape as the per-thread sqlite3 connection the
  stores already keep, so no statement pays for switching schema.
* **Autocommit**, as every store opened SQLite (`isolation_level=None`); an
  explicit `BEGIN`/`COMMIT` works unchanged. A statement that fails inside a
  transaction leaves Postgres refusing everything until a rollback, which
  SQLite never did; the connection rolls that back itself before the next
  statement rather than let one failure poison every later query.

**Staging reaches its own database and nothing else** (`zero-spend-staging`,
amended at the owner's direction, §243). The network guard patches Python's
sockets, and libpq opens its own, so the guard cannot see this connection;
the rule is enforced here instead: on a zero-spend deployment the only host
allowed is the one in `DATABASE_URL`, and it must be a private or loopback
address (Render's private network), never a public one.
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import re
import socket
import sqlite3
import threading
from functools import lru_cache
from typing import Optional
from urllib.parse import unquote, urlparse

log = logging.getLogger("fam.db")

_local = threading.local()
_LOCK = threading.Lock()
#: Every Postgres connection opened, for `/api/health` (count only).
_OPENED = [0]
#: Every live connection, from every thread, so `close_all` can reach them.
_ALL: list = []
#: Schemas known to have tables (`has_tables`); a yes never turns into a no.
_CREATED: set = set()


def url() -> str:
    return (os.environ.get("DATABASE_URL") or "").strip()


def enabled() -> bool:
    """Whether stores live in Postgres in this process."""
    return bool(url())


def host_of(dsn: str) -> tuple[str, int]:
    """(host, port) from a postgres:// URL or a `host=... port=...` string."""
    dsn = (dsn or "").strip()
    if not dsn:
        return "", 0
    if "://" in dsn:
        parsed = urlparse(dsn)
        return unquote(parsed.hostname or ""), int(parsed.port or 5432)
    parts = dict(re.findall(r"(\w+)\s*=\s*('[^']*'|\S+)", dsn))
    host = parts.get("host", "").strip("'")
    return host, int(parts.get("port", "5432").strip("'") or 5432)


def _resolves_private(host: str) -> bool:
    """A Unix socket, loopback, or a name every address of which is private."""
    if not host or host.startswith("/"):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    addresses = {info[4][0] for info in infos}
    for raw in addresses:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])
        if not (ip.is_private or ip.is_loopback):
            return False
    return bool(addresses)


class DatabaseRefused(RuntimeError):
    """Staging was pointed at a database it may not reach."""


def check_staging_address(dsn: Optional[str] = None) -> None:
    """Raise unless a zero-spend deployment's database is private (§243)."""
    import spend_guard

    if not spend_guard.enabled():
        return
    host, _ = host_of(dsn if dsn is not None else url())
    if not _resolves_private(host):
        raise DatabaseRefused(
            f"zero spend: staging may reach only its own database on the private "
            f"network, and {host or 'that address'} is not private. Wire "
            f"DATABASE_URL from the staging database (render.yaml), never a "
            f"public address.")


def schema_for(path: str) -> str:
    """The Postgres schema a store file maps to."""
    path = os.path.abspath(str(path))
    stem = re.sub(r"[^a-z0-9]+", "_", os.path.splitext(os.path.basename(path))[0].lower())
    where = hashlib.sha1(os.path.dirname(path).encode()).hexdigest()[:8]
    return f"{stem or 'store'}_{where}"


# --------------------------------------------------------------- the dialect

class Row(tuple):
    """A row that answers row[0] and row["name"], like sqlite3.Row."""

    def __new__(cls, values, names):
        obj = super().__new__(cls, values)
        obj._names = names
        return obj

    def __getitem__(self, key):
        if isinstance(key, str):
            return tuple.__getitem__(self, self._names.index(key))
        return tuple.__getitem__(self, key)

    def keys(self):
        return list(self._names)


def _row_factory(cursor):
    names = [c.name for c in (cursor.description or [])]
    return lambda values: Row(values, names)


_INSERT_INTO = re.compile(r"^\s*INSERT\s+OR\s+(IGNORE|REPLACE)\s+INTO\s+(\w+)\s*(?:\(([^)]*)\))?",
                          re.I | re.S)


@lru_cache(maxsize=8192)
def _scalar_max_min(s: str) -> str:
    """MAX/MIN with two or more arguments are SQLite's scalar greatest/least.

    Scanned with a depth count, not a regex: an argument is often itself a
    call (`MAX(?, CASE ... END)`, `MAX(expires, COALESCE(a, b))`)."""
    out, i = [], 0
    pattern = re.compile(r"\b(MAX|MIN)\(", re.I)
    while True:
        m = pattern.search(s, i)
        if not m:
            out.append(s[i:])
            return "".join(out)
        depth, j, commas, quote = 1, m.end(), 0, False
        while j < len(s) and depth:
            c = s[j]
            if c == "'":
                quote = not quote
            elif not quote:
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                elif c == "," and depth == 1:
                    commas += 1
            j += 1
        name = m.group(1).upper()
        out.append(s[i:m.start()])
        if commas:
            out.append(("GREATEST(" if name == "MAX" else "LEAST(")
                       + _scalar_max_min(s[m.end():j - 1]) + ")")
        else:
            out.append(s[m.start():m.end()] + _scalar_max_min(s[m.end():j - 1]) + ")")
        i = j


def translate(sql: str, named: bool = False) -> tuple[str, str, str, tuple]:
    """(postgres sql, kind, table, columns) for one SQLite statement.

    `kind` is "" or "replace" (an INSERT OR REPLACE, which needs the table's
    primary key to become an upsert - done at execute time) or "skip" (a
    statement with no Postgres meaning, such as most PRAGMAs).
    """
    s = sql
    info = re.match(r"\s*PRAGMA\s+table_info\((\w+)\)", s, re.I)
    if info:
        return (("SELECT ordinal_position - 1 AS cid, column_name AS name,"
                 " data_type AS type, (is_nullable = 'NO')::int AS notnull,"
                 " column_default AS dflt_value, 0 AS pk"
                 " FROM information_schema.columns WHERE table_schema = current_schema()"
                 " AND table_name = '%s' ORDER BY ordinal_position" % info.group(1).lower()),
                "", "", ())
    if re.match(r"\s*PRAGMA\b", s, re.I):
        return "", "skip", "", ()
    kind, table, columns = "", "", ()
    m = _INSERT_INTO.match(s)
    if m:
        kind = "ignore" if m.group(1).upper() == "IGNORE" else "replace"
        table = m.group(2)
        columns = tuple(c.strip() for c in (m.group(3) or "").split(",") if c.strip())
        s = re.sub(r"\bINSERT\s+OR\s+(IGNORE|REPLACE)\b", "INSERT", s, count=1, flags=re.I)
    # Types. An INTEGER PRIMARY KEY is SQLite's row id: it numbers itself.
    s = re.sub(r"\bINTEGER\s+PRIMARY\s+KEY(\s+AUTOINCREMENT)?\b",
               "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY", s, flags=re.I)
    # REAL is a 4-byte float in Postgres: an epoch timestamp would lose minutes.
    s = re.sub(r"\bREAL\b", "DOUBLE PRECISION", s)
    s = re.sub(r"\bINTEGER\b", "BIGINT", s)
    s = re.sub(r"\bBLOB\b", "BYTEA", s)
    # SQLite's LIKE ignores case for ASCII; Postgres' does not.
    s = re.sub(r"\bNOT\s+LIKE\b", "NOT ILIKE", s, flags=re.I)
    s = re.sub(r"(?<!NOT )\bLIKE\b", "ILIKE", s, flags=re.I)
    s = re.sub(r"([\w.]+)\s+COLLATE\s+NOCASE\b", r"lower(\1)", s, flags=re.I)
    # Two-argument MAX/MIN are SQLite's scalar greatest/least.
    s = _scalar_max_min(s)
    s = re.sub(r"\bBEGIN\s+(IMMEDIATE|EXCLUSIVE|DEFERRED)\b", "BEGIN", s, flags=re.I)
    # instr(haystack, needle) is strpos in Postgres; a blob literal is bytea.
    s = re.sub(r"\binstr\(", "strpos(", s, flags=re.I)
    s = re.sub(r"\b[xX]'([0-9A-Fa-f]*)'", r"'\\x\1'::bytea", s)
    # The one SQLite date function in use (the admin tracker's sign-ups).
    s = re.sub(r"\bdate\(([^(),]+),\s*'unixepoch'\)",
               r"to_char(to_timestamp(\1) AT TIME ZONE 'UTC', 'YYYY-MM-DD')", s, flags=re.I)
    # Placeholders: `?`, or `:name` when the parameters are a dict (psycopg's
    # are %s and %(name)s). Only then: a colon in a string literal is text.
    s = s.replace("%", "%%")
    if named:
        s = re.sub(r"(?<![:\w]):([A-Za-z_]\w*)", r"%(\1)s", s)
    s = s.replace("?", "%s")
    if kind == "ignore":
        s = s.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return s, kind, table, columns


def _sqlite_error(exc: Exception) -> Exception:
    import psycopg
    from psycopg import errors as pgerr

    if isinstance(exc, pgerr.IntegrityError):
        out = sqlite3.IntegrityError(str(exc))
    elif isinstance(exc, (pgerr.DuplicateColumn, pgerr.DuplicateTable, pgerr.DuplicateObject,
                          pgerr.UndefinedColumn, pgerr.UndefinedTable, pgerr.SyntaxError,
                          psycopg.OperationalError)):
        out = sqlite3.OperationalError(str(exc))
    else:
        out = sqlite3.DatabaseError(str(exc))
    out.__cause__ = exc
    return out


class Cursor:
    def __init__(self, cur, conn: "Connection"):
        self._cur = cur
        self._conn = conn

    def fetchone(self):
        return self._cur.fetchone() if self._cur is not None and self._cur.description else None

    def fetchall(self):
        return self._cur.fetchall() if self._cur is not None and self._cur.description else []

    def fetchmany(self, size: int = 1):
        return self._cur.fetchmany(size) if self._cur is not None and self._cur.description else []

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def rowcount(self):
        return self._cur.rowcount if self._cur is not None else -1

    @property
    def description(self):
        return self._cur.description if self._cur is not None else None

    @property
    def lastrowid(self):
        # The last identity value this session generated - what sqlite3 means.
        return self._conn._lastval()


class Connection:
    """Enough of sqlite3.Connection for FAM's stores, over psycopg."""

    def __init__(self, dsn: str, schema: str):
        import psycopg

        self._dsn = dsn
        self._pg = psycopg.connect(dsn, autocommit=True, row_factory=_row_factory)
        self.schema = schema
        self._pg.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        self._pg.execute(f'SET search_path TO "{schema}"')
        self.row_factory = None  # rows answer by index and by name already
        self._keys: dict = {}
        with _LOCK:
            _OPENED[0] += 1
            _ALL.append(self)

    # The transaction a failed statement leaves behind is rolled back before
    # the next one: Postgres refuses everything inside an aborted transaction,
    # SQLite never did, and the stores were written for SQLite.
    def _heal(self) -> None:
        import psycopg
        from psycopg.pq import TransactionStatus

        if self._pg.closed:
            # Closed under a store that kept this object (a test's cleanup, a
            # dropped connection): open again on the same schema.
            self._pg = psycopg.connect(self._dsn, autocommit=True, row_factory=_row_factory)
            self._pg.execute(f'SET search_path TO "{self.schema}"')
            with _LOCK:
                _OPENED[0] += 1
                _ALL.append(self)
            return
        if self._pg.info.transaction_status == TransactionStatus.INERROR:
            log.warning("rolled back a failed transaction in %s", self.schema)
            self._pg.execute("ROLLBACK")

    def _index_columns(self, table: str) -> tuple:
        """The columns of the table's primary key (or first unique index):
        what an INSERT OR REPLACE conflicts on."""
        if table in self._keys:
            return self._keys[table]
        rows = self._pg.execute(
            "SELECT i.indexrelid, a.attname FROM pg_index i JOIN pg_attribute a"
            " ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)"
            " WHERE i.indrelid = %s::regclass AND (i.indisprimary OR i.indisunique)"
            " ORDER BY i.indisprimary DESC, i.indexrelid",
            (f'"{self.schema}".{table.lower()}',)).fetchall()
        first = rows[0][0] if rows else None
        key = tuple(r[1] for r in rows if r[0] == first)
        self._keys[table] = key
        return key

    def _columns(self, table: str) -> tuple:
        """The table's columns in order, for an INSERT that names none."""
        rows = self._pg.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema ="
            " current_schema() AND table_name = %s ORDER BY ordinal_position",
            (table.lower(),)).fetchall()
        return tuple(r[0] for r in rows)

    def _name_columns(self, sql: str, table: str, columns: tuple) -> tuple[str, tuple]:
        """An INSERT with no column list gets the table's own, in order."""
        if columns:
            return sql, columns
        columns = self._columns(table)
        sql = re.sub(r"(INSERT\s+INTO\s+" + re.escape(table) + r")\s+VALUES",
                     r"\1 (" + ", ".join(columns) + ") VALUES", sql, count=1, flags=re.I)
        return sql, columns

    def _replace(self, sql: str, table: str, columns: tuple) -> str:
        """SQLite's INSERT OR REPLACE as an upsert on the table's key."""
        sql, columns = self._name_columns(sql, table, columns)
        key = self._index_columns(table)
        if not key:
            return sql
        updates = [c for c in columns if c not in key]
        target = ", ".join(key)
        # The target gets an alias, so a table that is itself called
        # `excluded` (local news has one) cannot collide with EXCLUDED.
        sql = re.sub(r"(INSERT\s+INTO\s+" + re.escape(table) + r")(\s*\()",
                     r"\1 AS fam_row\2", sql, count=1, flags=re.I)
        if not updates:
            return sql.rstrip().rstrip(";") + f" ON CONFLICT ({target}) DO NOTHING"
        sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in updates)
        return sql.rstrip().rstrip(";") + f" ON CONFLICT ({target}) DO UPDATE SET {sets}"

    def _lastval(self):
        try:
            return self._pg.execute("SELECT lastval()").fetchone()[0]
        except Exception:  # noqa: BLE001 - no sequence used yet in this session
            self._heal()
            return None

    def execute(self, sql, params=()):
        import psycopg

        pg_sql, kind, table, columns = translate(sql, isinstance(params, dict))
        if kind == "skip":
            return Cursor(None, self)
        self._heal()
        try:
            if kind == "replace":
                pg_sql = self._replace(pg_sql, table, columns)
            args = params if isinstance(params, dict) else tuple(params or ())
            return Cursor(self._pg.execute(pg_sql, args), self)
        except psycopg.Error as exc:
            raise _sqlite_error(exc) from exc

    def executemany(self, sql, seq):
        import psycopg

        seq = list(seq)
        pg_sql, kind, table, columns = translate(sql, bool(seq) and isinstance(seq[0], dict))
        if kind == "skip":
            return Cursor(None, self)
        self._heal()
        if kind == "replace":
            pg_sql = self._replace(pg_sql, table, columns)
        cur = self._pg.cursor()
        try:
            cur.executemany(pg_sql, [p if isinstance(p, dict) else tuple(p) for p in seq])
        except psycopg.Error as exc:
            raise _sqlite_error(exc) from exc
        return Cursor(cur, self)

    def executescript(self, script: str):
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)
        return Cursor(None, self)

    # sqlite3's `with conn:` is a transaction, not a close.
    def __enter__(self):
        return self

    def __exit__(self, kind, value, tb):
        from psycopg.pq import TransactionStatus

        status = self._pg.info.transaction_status
        if status in (TransactionStatus.INTRANS, TransactionStatus.INERROR):
            self._pg.execute("ROLLBACK" if kind or status == TransactionStatus.INERROR
                             else "COMMIT")
        return False

    def commit(self):
        from psycopg.pq import TransactionStatus

        if self._pg.info.transaction_status == TransactionStatus.INTRANS:
            self._pg.execute("COMMIT")

    def rollback(self):
        from psycopg.pq import TransactionStatus

        if self._pg.info.transaction_status != TransactionStatus.IDLE:
            self._pg.execute("ROLLBACK")

    def close(self):
        # Kept for the thread and the store; sqlite3 callers close after each
        # use because opening a file is cheap, and a server connection is not.
        pass

    @property
    def in_transaction(self) -> bool:
        from psycopg.pq import TransactionStatus

        return self._pg.info.transaction_status != TransactionStatus.IDLE


def connect(path, *args, **kwargs):
    """`sqlite3.connect(path, ...)`, or the same store in Postgres."""
    dsn = url()
    path = str(path)
    if not dsn or path == ":memory:" or path.startswith("file:"):
        return sqlite3.connect(path, *args, **kwargs)
    check_staging_address(dsn)
    schema = schema_for(path)
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = _local.conns = {}
    conn = conns.get(schema)
    if conn is None or conn._pg.closed:
        conn = conns[schema] = Connection(dsn, schema)
    return conn


def close_all() -> int:
    """Close every Postgres connection this process opened, on every thread.

    A long-running server keeps one per store per thread for its life; a test
    run makes new stores in new directories thousands of times, and would run
    Postgres out of connections. `tests/conftest.py` calls this after each
    test. Returns how many were closed."""
    with _LOCK:
        conns, _ALL[:] = list(_ALL), []
    for conn in conns:
        try:
            conn._pg.close()
        except Exception:  # noqa: BLE001 - closing is best effort
            pass
    return len(conns)


def has_tables(path: str) -> bool:
    """Whether a store has been created yet: its file, or its schema."""
    if not enabled():
        return os.path.exists(str(path))
    schema = schema_for(path)
    if schema in _CREATED:
        return True  # tables are never dropped, so a yes is kept
    import psycopg

    try:
        with psycopg.connect(url(), autocommit=True) as conn:
            found = bool(conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema = %s LIMIT 1",
                (schema,)).fetchone())
    except psycopg.Error:
        return False
    if found:
        _CREATED.add(schema)
    return found


def report() -> dict:
    """For `/api/health`: where the stores live, never the address itself."""
    if not enabled():
        return {"backend": "sqlite"}
    host, port = host_of(url())
    return {"backend": "postgres", "private": _resolves_private(host),
            "connections_opened": _OPENED[0]}
