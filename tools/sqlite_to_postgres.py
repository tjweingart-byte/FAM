#!/usr/bin/env python3
"""Copy every SQLite store into Postgres, for the switch-over (PROBLEMS.md §243).

    python tools/sqlite_to_postgres.py --from ./backup --database-url "$URL"
    python tools/sqlite_to_postgres.py --from ./backup --database-url "$URL" --yes

`--from` is a directory holding copies of the deployment's `.db` files (taken
from `/data` with the service stopped, so nothing is half-written). Without
`--yes` it only reads them and says what it would copy.

For each store:

* **The schema comes from the file itself** - every `CREATE TABLE` and
  `CREATE INDEX` in its `sqlite_master`, translated by `db.translate` exactly
  as the running server translates them, so a column added by a later
  `ALTER TABLE` is there and nothing is retyped by hand.
* **Rows are copied in one transaction per store**: a store is copied whole
  or not at all. Then each self-numbering column's sequence is moved past its
  highest id, or the first new row after the switch would collide.
* **Counts are compared** table by table, and any difference stops the run
  with the store named.

The Postgres schema a store lands in is named from the path the *server* will
use (`--as-dir`, `/data` by default, the Dockerfile's), not from where the
copies are on this machine - so after the switch the server finds exactly
what was copied. It refuses a target table that already has rows unless
`--replace` says to empty it first.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402

#: Rows per INSERT batch.
BATCH = 500


def store_files(source: str) -> list[str]:
    return sorted(f for f in os.listdir(source)
                  if f.endswith(".db") and os.path.isfile(os.path.join(source, f)))


def sqlite_schema(conn: sqlite3.Connection) -> tuple[list, list]:
    """(tables as (name, sql), index statements) as the file states them."""
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL"
        " AND name NOT LIKE 'sqlite_%' ORDER BY type = 'index', rowid").fetchall()
    tables = [(name, sql) for kind, name, sql in rows if kind == "table"]
    indexes = [sql for kind, name, sql in rows if kind == "index"]
    return tables, indexes


def copy_store(pg, path: str, schema: str, *, apply: bool, replace: bool) -> dict:
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    tables, indexes = sqlite_schema(src)
    report = {}
    try:
        if apply:
            pg.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            pg.execute(f'SET LOCAL search_path TO "{schema}"')
            for _, ddl in tables:
                pg.execute(db.translate(_if_not_exists(ddl))[0])
            for ddl in indexes:
                pg.execute(db.translate(_if_not_exists(ddl))[0])
        for name, _ in tables:
            columns = [r[1] for r in src.execute(f'PRAGMA table_info("{name}")')]
            count = src.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            report[name] = count
            if not apply:
                continue
            existing = pg.execute(f'SELECT COUNT(*) FROM "{name.lower()}"').fetchone()[0]
            if existing and not replace:
                raise SystemExit(f"{schema}.{name} already has {existing} rows; "
                                 f"pass --replace to empty it first")
            if existing:
                pg.execute(f'DELETE FROM "{name.lower()}"')
            cols = ", ".join(columns)
            marks = ", ".join(["%s"] * len(columns))
            insert = f'INSERT INTO "{name.lower()}" ({cols}) VALUES ({marks})'
            cursor = src.execute(f'SELECT {cols} FROM "{name}"')
            with pg.cursor() as cur:
                while True:
                    batch = cursor.fetchmany(BATCH)
                    if not batch:
                        break
                    cur.executemany(insert, [tuple(r) for r in batch])
            copied = pg.execute(f'SELECT COUNT(*) FROM "{name.lower()}"').fetchone()[0]
            if copied != count:
                raise SystemExit(f"{schema}.{name}: copied {copied} of {count} rows")
            _advance_identities(pg, schema, name.lower())
    finally:
        src.close()
    return report


def _if_not_exists(ddl: str) -> str:
    for head in ("CREATE TABLE ", "CREATE INDEX ", "CREATE UNIQUE INDEX "):
        if ddl.upper().startswith(head) and "IF NOT EXISTS" not in ddl.upper():
            return ddl[:len(head)] + "IF NOT EXISTS " + ddl[len(head):]
    return ddl


def _advance_identities(pg, schema: str, table: str) -> None:
    for (column,) in pg.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = %s"
            " AND table_name = %s AND is_identity = 'YES'", (schema, table)).fetchall():
        pg.execute(
            f'SELECT setval(pg_get_serial_sequence(%s, %s), '
            f'COALESCE((SELECT MAX({column}) FROM "{schema}"."{table}"), 0) + 1, false)',
            (f'"{schema}"."{table}"', column))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--from", dest="source", required=True,
                        help="directory of .db copies")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--as-dir", default="/data",
                        help="the directory the server opens the stores from")
    parser.add_argument("--yes", action="store_true", help="copy; without it, only read")
    parser.add_argument("--replace", action="store_true",
                        help="empty a target table that already has rows")
    args = parser.parse_args()
    if not args.database_url:
        print("No database: pass --database-url or set DATABASE_URL.", file=sys.stderr)
        return 1
    files = store_files(args.source)
    if not files:
        print(f"No .db files in {args.source}.", file=sys.stderr)
        return 1

    import psycopg

    total = 0
    with psycopg.connect(args.database_url) as pg:
        for filename in files:
            schema = db.schema_for(os.path.join(args.as_dir, filename))
            with pg.transaction():
                report = copy_store(pg, os.path.join(args.source, filename), schema,
                                    apply=args.yes, replace=args.replace)
            rows = sum(report.values())
            total += rows
            print(f"  {filename:<24} -> {schema:<28} {len(report):>3} tables "
                  f"{rows:>9} rows" + ("" if args.yes else "  (not copied)"))
    print(f"\n{'copied' if args.yes else 'would copy'} {total} rows from "
          f"{len(files)} stores." + ("" if args.yes else " Nothing was written; pass --yes."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
