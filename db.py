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
* **One pool of connections per process** (`DATABASE_MAX_CONNECTIONS`, 20 by
  default), shared by every store: a statement borrows one, switches its
  `search_path` to the store's schema if it was elsewhere, and gives it
  back. A transaction keeps its connection until it ends. Per-thread
  connections would have been one per store per thread - over a thousand
  for one instance, and never closed when a short-lived thread ended.
* **SQLite's rules for writing, kept.** SQLite lets one writer at a time
  into a file; here every write to a store's schema holds that store's
  advisory lock until it commits. That is what keeps a check-then-spend
  inside `BEGIN IMMEDIATE` from spending twice, and message ids committing
  in the order they were handed out (the client's cursor is an id). A
  statement that fails inside a transaction is undone alone, under a
  savepoint, and the transaction carries on - as in SQLite, where Postgres
  would abandon the whole transaction. A store opened without
  `isolation_level=None` gets sqlite3's implicit transaction: its writes are
  one transaction until it commits.

**Staging reaches its own database and nothing else** (`zero-spend-staging`,
amended at the owner's direction, §243). The network guard patches Python's
sockets, and libpq opens its own, so the guard cannot see this connection;
the rule is enforced here instead, on every way this module connects: on a
zero-spend deployment every host libpq could be sent to - read from the
whole connection string and the PG* environment, as libpq reads them - must
be a private or loopback address (Render's private network), and a string
that does not say where it goes (`service=`, `hostaddr=`) is refused.
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
import time
from functools import lru_cache
from typing import Optional
from urllib.parse import unquote, urlparse

log = logging.getLogger("fam.db")

#: Every Postgres connection opened, for `/api/health` (count only).
_OPENED = [0]
#: Schemas known to have tables (`has_tables`); a yes never turns into a no.
_CREATED: set = set()
#: Schemas this process has created (or found), so CREATE SCHEMA runs once.
_SCHEMAS: set = set()
#: Idle connections kept open beyond this many are closed.
IDLE_KEPT = 8
#: How long a statement waits for a free connection before it fails.
WAIT_SECONDS = 30.0
#: How long a write waits for its store's lock: SQLite's busy timeout.
LOCK_TIMEOUT = "10s"


def max_connections() -> int:
    try:
        return max(2, int(os.environ.get("DATABASE_MAX_CONNECTIONS") or 20))
    except ValueError:
        return 20


def url() -> str:
    return (os.environ.get("DATABASE_URL") or "").strip()


def enabled() -> bool:
    """Whether stores live in Postgres in this process."""
    return bool(url())


def addresses(dsn: str) -> list[tuple[str, int]]:
    """Every (host, port) libpq could connect to for this string, read the way
    libpq reads it: the URL or key=value form, `?host=` in a URL, and the
    PGHOST/PGPORT environment when the string leaves them out. An empty host
    is libpq's local socket. A string that does not say where it goes -
    `hostaddr=` (connects past the host name) or `service=` (a file names
    it) - raises `DatabaseRefused`."""
    from psycopg.conninfo import conninfo_to_dict

    try:
        params = conninfo_to_dict((dsn or "").strip())
    except Exception as exc:  # noqa: BLE001 - psycopg's ProgrammingError
        raise DatabaseRefused(f"DATABASE_URL could not be read: {exc}") from exc
    for key, env in (("hostaddr", "PGHOSTADDR"), ("service", "PGSERVICE"),
                     ("servicefile", "PGSERVICEFILE")):
        if params.get(key) or os.environ.get(env):
            raise DatabaseRefused(
                f"DATABASE_URL may not use {key} ({env}): it would decide the "
                f"address somewhere this check cannot see")
    hosts = str(params.get("host") or os.environ.get("PGHOST") or "").split(",")
    ports = str(params.get("port") or os.environ.get("PGPORT") or "5432").split(",")
    out = []
    for i, host in enumerate(hosts):
        port = ports[i] if i < len(ports) else ports[-1]
        out.append((host.strip(), int(port.strip() or 5432)))
    return out


def host_of(dsn: str) -> tuple[str, int]:
    """(host, port) of the first address, ("", 0) for no string."""
    if not (dsn or "").strip():
        return "", 0
    try:
        return addresses(dsn)[0]
    except DatabaseRefused:
        return "", 0


def _resolves_private(host: str) -> bool:
    """A Unix socket, loopback, or a name every address of which is private."""
    if not host or host.startswith("/"):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    addresses_ = {info[4][0] for info in infos}
    for raw in addresses_:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])
        if not (ip.is_private or ip.is_loopback):
            return False
    return bool(addresses_)


class DatabaseRefused(RuntimeError):
    """Staging was pointed at a database it may not reach."""


def check_staging_address(dsn: Optional[str] = None) -> None:
    """Raise unless every address a zero-spend deployment's database string
    could reach is private (§243). Called before every connection."""
    import spend_guard

    if not spend_guard.enabled():
        return
    for host, _ in addresses(dsn if dsn is not None else url()):
        if not _resolves_private(host):
            raise DatabaseRefused(
                f"zero spend: staging may reach only its own database on the "
                f"private network, and {host} is not private. Wire DATABASE_URL "
                f"from the staging database (render.yaml), never a public address.")


def raw_connect(**kwargs):
    """A psycopg connection outside the pool (the admin tracker's read-only
    queries), after the same address check every pooled one passes."""
    import psycopg

    check_staging_address()
    kwargs.setdefault("connect_timeout", 10)
    return psycopg.connect(url(), **kwargs)


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


def _outside_literals(s: str, change) -> str:
    """Apply `change` to the SQL between single-quoted literals only."""
    parts = re.split(r"('(?:[^']|'')*')", s)
    return "".join(part if i % 2 else change(part) for i, part in enumerate(parts))


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
    # SQLite's LIKE ignores case for ASCII; Postgres' does not. Never inside
    # a string literal: 'I like it' is text.
    s = _outside_literals(s, lambda t: re.sub(r"\bNOT\s+LIKE\b", "NOT ILIKE", t, flags=re.I))
    s = _outside_literals(s, lambda t: re.sub(r"(?<!NOT )\bLIKE\b", "ILIKE", t, flags=re.I))
    if re.match(r"\s*CREATE\b", s, re.I):
        # A column declared NOCASE: Postgres has no such collation, and the
        # stores only sort by it, which the ORDER BY rewrite below covers.
        s = re.sub(r"\s+COLLATE\s+NOCASE\b", "", s, flags=re.I)
    s = _outside_literals(s, lambda t: re.sub(
        r"([\w.]+)\s+COLLATE\s+NOCASE\b", r"lower(\1)", t, flags=re.I))
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
        s = _outside_literals(s, lambda t: re.sub(r"(?<![:\w]):([A-Za-z_]\w*)", r"%(\1)s", t))
    s = _outside_literals(s, lambda t: t.replace("?", "%s"))
    if kind == "ignore":
        s = s.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return s, kind, table, columns


def _sqlite_error(exc: Exception) -> Exception:
    import psycopg
    from psycopg import errors as pgerr

    if isinstance(exc, pgerr.IntegrityError):
        out = sqlite3.IntegrityError(str(exc))
    elif isinstance(exc, pgerr.LockNotAvailable):
        out = sqlite3.OperationalError(f"database is locked ({exc})")
    elif isinstance(exc, (pgerr.DuplicateColumn, pgerr.DuplicateTable, pgerr.DuplicateObject,
                          pgerr.UndefinedColumn, pgerr.UndefinedTable, pgerr.SyntaxError,
                          psycopg.OperationalError)):
        out = sqlite3.OperationalError(str(exc))
    else:
        out = sqlite3.DatabaseError(str(exc))
    out.__cause__ = exc
    return out


class Cursor:
    """A statement's result, read in full before its connection went back to
    the pool - so it can be read on any thread, at any time, like sqlite3's."""

    def __init__(self, rows=None, description=None, rowcount: int = -1, lastrowid=None):
        self._rows = list(rows or [])
        self._at = 0
        self.description = description
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    @classmethod
    def read(cls, cur, returning: bool = False) -> "Cursor":
        if returning:
            # The id an INSERT made, read from its own RETURNING - never the
            # session's lastval(), which a pooled connection shares.
            got = cur.fetchall() if cur.description else []
            return cls(rowcount=cur.rowcount, lastrowid=got[-1][0] if got else None)
        rows = cur.fetchall() if cur.description else []
        return cls(rows, cur.description, cur.rowcount)

    def fetchone(self):
        if self._at >= len(self._rows):
            return None
        self._at += 1
        return self._rows[self._at - 1]

    def fetchall(self):
        out, self._at = self._rows[self._at:], len(self._rows)
        return out

    def fetchmany(self, size: int = 1):
        out = self._rows[self._at:self._at + size]
        self._at += len(out)
        return out

    def __iter__(self):
        return iter(self.fetchall())


def _number_loader():
    """SUM and AVG over integers are NUMERIC in Postgres, which psycopg reads
    as Decimal; SQLite answered int or float, and `Decimal + float` raises."""
    from psycopg.types.numeric import NumericLoader

    class Number(NumericLoader):
        def load(self, data):
            value = super().load(data)
            if not value.is_finite():
                return float(value)
            return int(value) if value == value.to_integral_value() else float(value)

    return Number


class _Slot:
    """One pooled server connection and the schema its search_path names."""

    def __init__(self, pg):
        self.pg = pg
        self.schema: Optional[str] = None


class _Pool:
    def __init__(self):
        self._cond = threading.Condition()
        self._idle: list[_Slot] = []
        self._all: set = set()
        self._count = 0

    def _open(self) -> _Slot:
        import psycopg

        check_staging_address()
        pg = psycopg.connect(url(), autocommit=True, row_factory=_row_factory,
                             prepare_threshold=None, connect_timeout=10)
        pg.adapters.register_loader("numeric", _number_loader())
        pg.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        with _LOCK:
            _OPENED[0] += 1
        slot = _Slot(pg)
        with self._cond:
            self._all.add(slot)
        return slot

    def acquire(self, schema: Optional[str]) -> _Slot:
        """A connection whose search_path is `schema` (None: any)."""
        import psycopg

        for attempt in (1, 2):
            slot = self._take(schema)
            try:
                if schema is not None and slot.schema != schema:
                    if schema not in _SCHEMAS:
                        try:
                            slot.pg.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
                        except psycopg.errors.UniqueViolation:
                            pass  # another process created it in the same moment
                        _SCHEMAS.add(schema)
                    slot.pg.execute(f'SET search_path TO "{schema}"')
                    slot.schema = schema
                return slot
            except psycopg.OperationalError as exc:
                # An idle connection the server dropped (a restart, a
                # failover): thrown away, and a fresh one tried once.
                self.discard(slot)
                if attempt == 2:
                    raise _sqlite_error(exc) from exc
        raise AssertionError("unreachable")

    def _take(self, schema: Optional[str]) -> _Slot:
        deadline = time.monotonic() + WAIT_SECONDS
        with self._cond:
            while True:
                for i in range(len(self._idle) - 1, -1, -1):
                    if schema is None or self._idle[i].schema == schema:
                        return self._idle.pop(i)
                if self._idle:
                    return self._idle.pop()
                if self._count < max_connections():
                    self._count += 1
                    break
                left = deadline - time.monotonic()
                if left <= 0:
                    raise sqlite3.OperationalError(
                        f"database is locked: all {max_connections()} connections "
                        f"are in use (DATABASE_MAX_CONNECTIONS)")
                self._cond.wait(left)
        try:
            return self._open()
        except Exception:
            with self._cond:
                self._count -= 1
                self._cond.notify()
            raise

    def release(self, slot: _Slot) -> None:
        from psycopg.pq import TransactionStatus

        try:
            if slot.pg.closed:
                return self.discard(slot)
            if slot.pg.info.transaction_status != TransactionStatus.IDLE:
                slot.pg.execute("ROLLBACK")
        except Exception:  # noqa: BLE001 - a broken connection is not kept
            return self.discard(slot)
        surplus = None
        with self._cond:
            self._idle.append(slot)
            if len(self._idle) > IDLE_KEPT:
                surplus = self._idle.pop(0)
                self._count -= 1
                self._all.discard(surplus)
            self._cond.notify()
        if surplus is not None:
            try:
                surplus.pg.close()
            except Exception:  # noqa: BLE001
                pass

    def discard(self, slot: _Slot) -> None:
        try:
            slot.pg.close()
        except Exception:  # noqa: BLE001
            pass
        with self._cond:
            if slot in self._all:
                self._all.discard(slot)
                self._count -= 1
            self._cond.notify()

    def close_all(self) -> int:
        with self._cond:
            slots, self._idle = list(self._all), []
            self._all.clear()
            self._count = 0
            self._cond.notify_all()
        for slot in slots:
            try:
                slot.pg.close()
            except Exception:  # noqa: BLE001
                pass
        return len(slots)

    def stats(self) -> dict:
        with self._cond:
            return {"open": self._count, "idle": len(self._idle), "max": max_connections()}


_LOCK = threading.Lock()
_POOL = _Pool()
#: (schema, table) -> the columns an INSERT OR REPLACE conflicts on.
_KEYS: dict = {}
#: (schema, table) -> the self-numbering column, or "" for none.
_IDENTITY: dict = {}

_VERB = re.compile(r"\s*(\w+)")
_DML = {"INSERT", "UPDATE", "DELETE", "REPLACE"}
#: sqlite3 opens its implicit transaction on these only, never on DDL.
_WRITES = _DML | {"CREATE", "ALTER", "DROP"}


def _verb(sql: str) -> str:
    m = _VERB.match(sql)
    word = m.group(1).upper() if m else ""
    if word == "END":
        return "COMMIT"
    if word == "WITH" and re.search(r"\b(INSERT|UPDATE|DELETE)\b", sql, re.I):
        return "INSERT"
    if word == "ROLLBACK" and re.match(r"\s*ROLLBACK\s+(TRANSACTION\s+)?TO\b", sql, re.I):
        return "SAVEPOINT"
    return word


class Connection:
    """Enough of sqlite3.Connection for FAM's stores, over the pool.

    Holds no server connection between statements, except while a
    transaction is open, so a store may keep it, open one per call, or drop
    it on a thread that ends: nothing leaks."""

    def __init__(self, schema: str, implicit: bool = False):
        self.schema = schema
        self.row_factory = None  # rows answer by index and by name already
        # sqlite3's default isolation: a write opens a transaction that stays
        # open until commit. `isolation_level=None` (most stores) is autocommit.
        self._implicit = implicit
        self._held: Optional[_Slot] = None
        self._lock_sql = ("SELECT pg_advisory_xact_lock(%d)" % int.from_bytes(
            hashlib.sha1(schema.encode()).digest()[:8], "big", signed=True))

    # ---------------------------------------------------- the transaction

    def _begin(self) -> None:
        import psycopg

        if self._held is not None:
            raise sqlite3.OperationalError("cannot start a transaction within a transaction")
        slot = _POOL.acquire(self.schema)
        try:
            # The store's writer lock, taken at BEGIN as SQLite's IMMEDIATE
            # takes it: a transaction reads what it will write under it.
            slot.pg.execute("BEGIN; " + self._lock_sql)
        except psycopg.Error as exc:
            _POOL.release(slot)
            raise _sqlite_error(exc) from exc
        self._held = slot

    def _end(self, statement: str) -> None:
        import psycopg
        from psycopg.pq import TransactionStatus

        slot, self._held = self._held, None
        if slot is None:
            return
        try:
            if statement == "COMMIT" and \
                    slot.pg.info.transaction_status == TransactionStatus.INERROR:
                slot.pg.execute("ROLLBACK")
                raise sqlite3.OperationalError(
                    "the transaction had failed and was rolled back, not committed")
            slot.pg.execute(statement)
        except psycopg.Error as exc:
            raise _sqlite_error(exc) from exc
        finally:
            _POOL.release(slot)

    def commit(self):
        self._end("COMMIT")

    def rollback(self):
        self._end("ROLLBACK")

    @property
    def in_transaction(self) -> bool:
        return self._held is not None

    # sqlite3's `with conn:` is a transaction, not a close.
    def __enter__(self):
        return self

    def __exit__(self, kind, value, tb):
        self._end("ROLLBACK" if kind else "COMMIT")
        return False

    def close(self):
        # sqlite3 discards what a closed connection had not committed.
        self._end("ROLLBACK")

    def __del__(self):
        try:
            if self._held is not None:
                self._end("ROLLBACK")
        except Exception:  # noqa: BLE001 - a collector cannot report
            pass

    # ------------------------------------------------------- statements

    def _index_columns(self, slot: _Slot, table: str) -> tuple:
        """The columns of the table's primary key (or first unique index):
        what an INSERT OR REPLACE conflicts on."""
        cached = _KEYS.get((self.schema, table))
        if cached is not None:
            return cached
        rows = slot.pg.execute(
            "SELECT i.indexrelid, a.attname FROM pg_index i JOIN pg_attribute a"
            " ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)"
            " WHERE i.indrelid = %s::regclass AND (i.indisprimary OR i.indisunique)"
            " ORDER BY i.indisprimary DESC, i.indexrelid",
            (f'"{self.schema}".{table.lower()}',)).fetchall()
        first = rows[0][0] if rows else None
        key = tuple(r[1] for r in rows if r[0] == first)
        if key:
            _KEYS[(self.schema, table)] = key
        return key

    def _identity(self, slot: _Slot, table: str) -> str:
        cached = _IDENTITY.get((self.schema, table))
        if cached is not None:
            return cached
        rows = slot.pg.execute(
            "SELECT column_name, is_identity FROM information_schema.columns"
            " WHERE table_schema = current_schema() AND table_name = %s",
            (table.lower(),)).fetchall()
        found = next((r[0] for r in rows if r[1] == "YES"), "")
        if rows:  # only a table that exists is remembered
            _IDENTITY[(self.schema, table)] = found
        return found

    def _name_columns(self, slot: _Slot, sql: str, table: str, columns: tuple):
        """An INSERT with no column list gets the table's own, in order."""
        if columns:
            return sql, columns
        columns = tuple(r[0] for r in slot.pg.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema ="
            " current_schema() AND table_name = %s ORDER BY ordinal_position",
            (table.lower(),)).fetchall())
        sql = re.sub(r"(INSERT\s+INTO\s+" + re.escape(table) + r")\s+VALUES",
                     r"\1 (" + ", ".join(columns) + ") VALUES", sql, count=1, flags=re.I)
        return sql, columns

    def _replace(self, slot: _Slot, sql: str, table: str, columns: tuple) -> str:
        """SQLite's INSERT OR REPLACE as an upsert on the table's key."""
        sql, columns = self._name_columns(slot, sql, table, columns)
        key = self._index_columns(slot, table)
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

    def _run(self, slot: _Slot, sql: str, pg_sql: str, kind: str, table: str,
             columns: tuple, args, many: bool = False) -> Cursor:
        import psycopg

        try:
            if kind == "replace":
                pg_sql = self._replace(slot, pg_sql, table, columns)
            if many:
                with slot.pg.cursor() as cur:
                    cur.executemany(pg_sql, args)
                    return Cursor(rowcount=cur.rowcount)
            returning = False
            into = re.match(r"\s*INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)", sql, re.I)
            if into and not re.search(r"\bRETURNING\b", pg_sql, re.I):
                ident = self._identity(slot, into.group(1))
                if ident:
                    pg_sql = pg_sql.rstrip().rstrip(";") + f" RETURNING {ident}"
                    returning = True
            return Cursor.read(slot.pg.execute(pg_sql, args), returning)
        except psycopg.Error as exc:
            raise _sqlite_error(exc) from exc

    def _statement(self, sql: str, params, many: bool = False) -> Cursor:
        import psycopg

        named = (bool(params) and isinstance(params[0], dict)) if many else isinstance(params, dict)
        pg_sql, kind, table, columns = translate(sql, named)
        if kind == "skip":
            return Cursor()
        verb = _verb(sql)
        if verb == "BEGIN":
            self._begin()
            return Cursor()
        if verb in ("COMMIT", "ROLLBACK"):
            self._end(verb)
            return Cursor()
        if many:
            args = [p if isinstance(p, dict) else tuple(p) for p in params]
        else:
            args = params if isinstance(params, dict) else tuple(params or ())
        write = verb in _WRITES

        if self._held is None and not write:
            slot = _POOL.acquire(self.schema)
            try:
                return self._run(slot, sql, pg_sql, kind, table, columns, args, many)
            finally:
                _POOL.release(slot)

        if self._held is None and (not self._implicit or verb not in _DML):
            # A write on its own: its own transaction, under the store's lock,
            # so ids are committed in the order they were handed out.
            slot = _POOL.acquire(self.schema)
            try:
                try:
                    slot.pg.execute("BEGIN; " + self._lock_sql)
                except psycopg.Error as exc:
                    raise _sqlite_error(exc) from exc
                out = self._run(slot, sql, pg_sql, kind, table, columns, args, many)
                try:
                    slot.pg.execute("COMMIT")
                except psycopg.Error as exc:
                    raise _sqlite_error(exc) from exc
                return out
            finally:
                _POOL.release(slot)  # rolls back whatever did not commit

        if self._held is None:
            self._begin()  # sqlite3's implicit transaction, until commit
        slot = self._held
        if not write:
            return self._run(slot, sql, pg_sql, kind, table, columns, args, many)
        # A failed write is undone alone and the transaction carries on, as in
        # SQLite; Postgres would otherwise abandon all of it.
        try:
            slot.pg.execute("SAVEPOINT fam_statement")
        except psycopg.Error as exc:
            raise _sqlite_error(exc) from exc
        try:
            out = self._run(slot, sql, pg_sql, kind, table, columns, args, many)
        except Exception:
            try:
                slot.pg.execute("ROLLBACK TO SAVEPOINT fam_statement")
            except psycopg.Error:
                pass
            raise
        try:
            slot.pg.execute("RELEASE SAVEPOINT fam_statement")
        except psycopg.Error as exc:
            raise _sqlite_error(exc) from exc
        return out

    def execute(self, sql, params=()):
        return self._statement(sql, params)

    def executemany(self, sql, seq):
        return self._statement(sql, list(seq), many=True)

    def executescript(self, script: str):
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)
        return Cursor()


def connect(path, *args, **kwargs):
    """`sqlite3.connect(path, ...)`, or the same store in Postgres."""
    dsn = url()
    path = str(path)
    if not dsn or path == ":memory:" or path.startswith("file:"):
        return sqlite3.connect(path, *args, **kwargs)
    check_staging_address(dsn)
    # sqlite3's default isolation_level is "" (implicit transactions); the
    # stores that pass None asked for autocommit.
    isolation = kwargs.get("isolation_level", args[1] if len(args) > 1 else "")
    return Connection(schema_for(path), implicit=isolation is not None)


def close_all() -> int:
    """Close every Postgres connection this process holds.

    `tests/conftest.py` calls this after each test: a run makes stores in new
    directories thousands of times. Returns how many were closed."""
    return _POOL.close_all()


def has_tables(path: str) -> bool:
    """Whether a store has been created yet: its file, or its schema."""
    if not enabled():
        return os.path.exists(str(path))
    schema = schema_for(path)
    if schema in _CREATED:
        return True  # tables are never dropped, so a yes is kept
    import psycopg

    try:
        slot = _POOL.acquire(None)
    except (sqlite3.Error, psycopg.Error, DatabaseRefused):
        return False
    try:
        found = bool(slot.pg.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = %s LIMIT 1",
            (schema,)).fetchone())
    except psycopg.Error:
        return False
    finally:
        _POOL.release(slot)
    if found:
        _CREATED.add(schema)
    return found


#: (when, result) of the last probe, so a busy health check asks once a while.
_PROBED: list = [0.0, None]
PROBE_SECONDS = 10.0


def probe() -> dict:
    """A real query, not a configuration read (verify-not-inspect): whether
    the database answers, how fast, and whether this role may create tables."""
    now = time.monotonic()
    if _PROBED[1] is not None and now - _PROBED[0] < PROBE_SECONDS:
        return _PROBED[1]
    started = time.monotonic()
    try:
        slot = _POOL.acquire(None)
        try:
            can_create = bool(slot.pg.execute(
                "SELECT has_database_privilege(current_database(), 'CREATE')").fetchone()[0])
        finally:
            _POOL.release(slot)
        result = {"reachable": True, "can_create": can_create,
                  "ms": round((time.monotonic() - started) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        result = {"reachable": False, "error": str(exc)[:200]}
    _PROBED[:] = [now, result]
    return result


def report() -> dict:
    """For `/api/health`: where the stores live, never the address itself."""
    if not enabled():
        return {"backend": "sqlite"}
    try:
        private = all(_resolves_private(h) for h, _ in addresses(url()))
    except DatabaseRefused:
        private = False
    return {"backend": "postgres", "private": private,
            "connections_opened": _OPENED[0], "pool": _POOL.stats(), **probe()}
