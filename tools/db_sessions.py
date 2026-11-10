"""Who is connected to the Postgres database, and who holds a store's lock.

A deploy that fails at startup with `canceling statement due to lock timeout`
is waiting for a store's writer lock (`db.py`: one advisory lock per store,
held until a write commits). Something holds it: a transaction that started
and never ended - most often a connection whose server died without closing
it, which Postgres keeps (and its locks) until the socket times out.

    DATABASE_URL='<the External URL from the database's Connect menu>' \\
        python tools/db_sessions.py            # look: changes nothing
    DATABASE_URL='...' python tools/db_sessions.py --end   # end the stuck ones

`--end` ends only sessions that hold an advisory lock or sit idle inside a
transaction for more than `--older` seconds (60), never this one. Ending a
session rolls back its unfinished transaction; committed data is untouched.
The URL is read from the environment, never printed.
"""
from __future__ import annotations

import argparse
import os
import sys

SESSIONS = """
SELECT a.pid, a.state, a.application_name, a.client_addr::text,
       EXTRACT(EPOCH FROM now() - a.backend_start)::int AS connected_s,
       EXTRACT(EPOCH FROM now() - a.xact_start)::int AS in_transaction_s,
       EXTRACT(EPOCH FROM now() - a.state_change)::int AS in_state_s,
       a.wait_event_type, left(regexp_replace(a.query, '\\s+', ' ', 'g'), 70) AS query,
       (SELECT count(*) FROM pg_locks l
         WHERE l.pid = a.pid AND l.locktype = 'advisory' AND l.granted) AS store_locks,
       (SELECT count(*) FROM pg_locks l
         WHERE l.pid = a.pid AND NOT l.granted) AS waiting_locks
  FROM pg_stat_activity a
 WHERE a.datname = current_database() AND a.pid <> pg_backend_pid()
 ORDER BY a.xact_start NULLS LAST, a.backend_start
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--end", action="store_true",
                        help="end sessions that hold a store lock or sit idle in a transaction")
    parser.add_argument("--older", type=int, default=60,
                        help="only sessions in that state this many seconds or more (60)")
    args = parser.parse_args()

    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        print("Set DATABASE_URL first (the database's External URL).", file=sys.stderr)
        return 2
    try:
        import psycopg
    except ImportError:
        print('psycopg is not installed: pip install "psycopg[binary]"', file=sys.stderr)
        return 2

    with psycopg.connect(url, autocommit=True, connect_timeout=15) as pg:
        limit = pg.execute("SHOW max_connections").fetchone()[0]
        rows = pg.execute(SESSIONS).fetchall()
        print(f"{len(rows)} other sessions (the database allows {limit} connections)\n")
        print(f"{'pid':>7}  {'state':<20} {'conn s':>7} {'txn s':>7} {'state s':>7} "
              f"{'locks':>5} {'waits':>5}  query")
        stuck = []
        for (pid, state, _app, _addr, conn_s, txn_s, state_s, _wait, query,
             locks, waits) in rows:
            print(f"{pid:>7}  {(state or '-'):<20} {conn_s or 0:>7} {txn_s if txn_s is not None else '-':>7} "
                  f"{state_s if state_s is not None else '-':>7} {locks:>5} {waits:>5}  {query or ''}")
            idle_in_txn = (state or "").startswith("idle in transaction")
            if (locks or idle_in_txn) and (state_s or 0) >= args.older and state != "active":
                stuck.append((pid, state, locks, state_s))

        print()
        if not stuck:
            print("Nothing is stuck: no session holds a store lock idle for "
                  f"{args.older}s or more.")
            return 0
        print(f"{len(stuck)} stuck session(s) - holding a store lock or a transaction, idle:")
        for pid, state, locks, state_s in stuck:
            print(f"  pid {pid}: {state}, {locks} store lock(s), idle {state_s}s")
        if not args.end:
            print("\nRun again with --end to end them.")
            return 0
        for pid, *_ in stuck:
            ended = pg.execute("SELECT pg_terminate_backend(%s)", (pid,)).fetchone()[0]
            print(f"  pid {pid}: {'ended' if ended else 'could not be ended'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
