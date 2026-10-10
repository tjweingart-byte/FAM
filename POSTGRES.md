# The stores in Postgres (option B)

PROBLEMS.md §243. Every store opens through `db.connect(path)`. With no
`DATABASE_URL` that is exactly `sqlite3.connect(path)`, as it always was;
with one, each store is a schema in Postgres, and its SQLite is translated
as it runs (`db.translate`). Nothing about a store's code changes between
the two.

Why: a Render service with a disk runs as one instance, and every store was a
SQLite file on that disk. Postgres is what lets FAM run on more than one
machine, and it brings back zero-downtime deploys (a disk forces a brief
outage on each one).

## What is where

| | SQLite (no `DATABASE_URL`) | Postgres (`DATABASE_URL` set) |
|---|---|---|
| A store | `/data/<name>.db` | schema `<name>_<8 hex of the directory>` |
| Connections | one per store per thread | one pool per process, shared by every store (`DATABASE_MAX_CONNECTIONS`, 20) |
| Writes | one writer per file | one writer per store, by an advisory lock held to commit |
| Kept audio's hot copy | a column in `scripts.db` | the same column in the `scripts` schema; the object is in R2 either way |
| `/api/health` → `database` | `{"backend": "sqlite"}` | `{"backend": "postgres", "private": true, ...}` |
| `/api/health` → `storage` | measured per file (`st_dev`) | `persistence: "database"` for every store |

The schema is named from the path the server opens (`data_path`), so the
Dockerfile's `/data/messages.db` is always `messages_<hash of "/data">`.

## Staging (zero spend)

Staging may reach its own database and nothing else - the owner's amendment
to `zero-spend-staging`. `db.connect` refuses a `DATABASE_URL` whose host is
not a private or loopback address, and the network guard allows exactly that
host and port. So staging's `DATABASE_URL` must be its own database's
**internal** URL, wired by the blueprint (`fromDatabase`), never typed.

To turn it on for staging, in `render.yaml`:

```yaml
databases:
  - name: fam-staging-db
    plan: basic-256mb        # or basic-1gb; staging only needs to work
    postgresMajorVersion: "16"

# and on the fam-staging service:
      - key: DATABASE_URL
        fromDatabase:
          name: fam-staging-db
          property: connectionString
```

Then check `/api/health` → `database.backend` is `postgres` and
`environment.database_allowed` is `true`, and run the load test (`LOAD_TESTING.md`).

## Connections

Each FAM process keeps at most `DATABASE_MAX_CONNECTIONS` (20) open, and
closes idle ones beyond 8. Keep `instances x DATABASE_MAX_CONNECTIONS` (plus a
few for the shell and the copy tool) under the plan's limit: Render's plans
allow from about 100 connections upward - check the plan page before
raising either number.

## Production: the switch-over

Do this in a quiet hour. It takes a maintenance window of roughly ten to
thirty minutes, depending on the size of `/data`.

1. **Create the database** (Render dashboard or the blueprint, *without*
   wiring `DATABASE_URL` to `fam` yet): Postgres 16, Pro-4gb ($55/mo plus
   $0.30/GB) is the size the cost analysis recommends for ~100k listeners;
   Basic-1gb ($19) is enough today.
2. **Stop writes**: suspend the `fam` service, so nothing is half-written.
3. **Copy `/data`** off the disk (Render shell: `tar czf - /data/*.db`), to
   the machine that will run the copy.
4. **Dry run** - reads only, says what it would copy:

       python tools/sqlite_to_postgres.py --from ./data --database-url "$EXTERNAL_URL"

5. **Copy** - one transaction per store, counts checked table by table,
   self-numbering ids moved past their maximum:

       python tools/sqlite_to_postgres.py --from ./data --database-url "$EXTERNAL_URL" --yes

6. **Wire it**: set `DATABASE_URL` on `fam` to the database's *internal*
   URL (`fromDatabase` in `render.yaml`), and resume the service.
7. **Check**: `/api/health` → `database.backend: postgres`,
   `storage.note` says every store is in Postgres; sign in, open myFAM,
   play a kept episode, send a message.

**Going back**: unset `DATABASE_URL` and redeploy. The SQLite files on the
disk are exactly as they were at step 2; anything written to Postgres after
step 6 is not in them.

Keep the disk until the switch has run for a while. After that, removing it
is what allows a second instance - but read the next section first.

## Not yet done

* **More than one instance.** Postgres removes the disk's one-instance
  limit, but eight pieces of state still live in each process's memory: the
  story pool and its clocks, API-Sports' daily budgets, prefetch's ledgers,
  the spend limiter, live captions and search progress, the typing
  indicator, the DailyFAM in-flight guard, and the background jobs, which
  would run once per process. Until they are shared (step A in the cost
  analysis), run **one** instance, on Postgres or not.
* **The admin question box** is off on Postgres: SQLite's authorizer is what
  keeps a question read-only and hides password hashes and tokens, and
  Postgres has no such hook. The dashboard and the schema work. The fix is a
  read-only role with column grants.
* **Per-request queries.** A Postgres query costs ~0.3-1 ms over the network
  against ~0.05 ms for a local file. myFAM still asks one query per candidate
  tile; batch those before production carries real traffic, and run the load
  test against staging on Postgres first.
