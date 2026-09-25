"""The admin tracker: every store, live, and a way to ask it questions.

The page at `/admin` used to have no equivalent in this app at all. The nearest
thing was a demo artifact that kept its *own* tables in the artifact's storage,
so its "accounts" count was whatever had been tapped into that page - never the
number of accounts this deployment actually holds. This module reads the real
files, on every request, and nothing else.

Three rules hold it up.

**The list of stores is derived, never written down.** Every store in FAM is
opened through `paths.data_path` with a literal variable name and
filename, and `discover_stores` finds
them by reading those calls out of the source - the same trick
`tests/test_data_paths.py` uses, for the same reason (§107): a hand-written
list of databases has already been wrong twice, and a tracker that silently
omits a store is exactly the "not up to date" this replaces. A store added
tomorrow is on the page tomorrow with nobody editing this file.

**Everything is read-only, three times over.** Each file is attached with
`mode=ro`, the connection is `query_only`, and an authorizer allows `SELECT`,
column reads and a short list of functions - nothing else, so an `ATTACH`, a
`PRAGMA` or a write fails before it runs rather than being filtered by a
regular expression. A progress handler stops a query that runs past its
budget, so a cross join typed by mistake cannot hold a worker.

**An admin sees counts and rows, never secrets.** Password hashes, session
token hashes, provider subjects, message text, extracted attachment text and
every BLOB (audio, avatars, embeddings) read as NULL through the authorizer.
The redaction is in the sandbox rather than in the page, so a question the
model writes cannot reach them either.

Questions go two ways. A catalogue of **recipes** answers the common ones with
no model and no key - "how many accounts have made public mixes", "how many
accounts have over 5 friends" - by matching words and pulling out the number
and the comparison. Anything else goes to Claude, which is given the live
schema and asked for one `SELECT`; the SQL runs in the same sandbox and is
shown beside the answer, so a number is never presented without the query
that produced it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from paths import PROJECT_ROOT, data_path

log = logging.getLogger(__name__)

#: SQLite's compiled-in ceiling on attached databases. A query naming more
#: stores than this is refused with a sentence rather than an obscure error.
MAX_ATTACHED = 10
#: Rows returned by one query. The page is a tracker, not an export.
MAX_ROWS = 500
#: How long one query may run before it is interrupted.
QUERY_SECONDS = 4.0

_CALL = re.compile(r'data_path\(\s*"([A-Z_]+)"\s*,\s*"([^"]+)"')


@dataclass(frozen=True)
class Store:
    alias: str       # how a query names it: `accounts.accounts`
    env: str         # the variable that moves it
    filename: str
    path: str

    @property
    def exists(self) -> bool:
        return os.path.exists(self.path)

    def size(self) -> int:
        total = 0
        for suffix in ("", "-wal"):
            try:
                total += os.path.getsize(self.path + suffix)
            except OSError:
                pass
        return total


def discover_stores(root: Path = PROJECT_ROOT) -> list[Store]:
    """Every database the app opens, read out of the source.

    Resolved through `data_path` itself, so an environment variable pointing a
    store at `/data` is honoured exactly as the store that writes it honours
    it - the tracker cannot end up reading a different file from the app.
    """
    found: dict[str, tuple[str, str]] = {}
    for module in sorted(root.glob("*.py")):
        try:
            text = module.read_text(encoding="utf-8")
        except OSError:
            continue
        for env, filename in _CALL.findall(text):
            found.setdefault(filename, (env, filename))
    stores = []
    for filename, (env, _) in sorted(found.items()):
        alias = re.sub(r"[^a-z0-9_]", "_", Path(filename).stem.lower())
        stores.append(Store(alias, env, filename, data_path(env, filename)))
    return stores


# --------------------------------------------------------------- redaction

#: (store, table, column) that always read as NULL. BLOB columns are added to
#: this per connection from the live schema, so a new binary column is covered
#: without being listed.
SECRET_COLUMNS = {
    ("accounts", "accounts", "password"),
    ("accounts", "sessions", "token_hash"),
    ("accounts", "identities", "subject"),
    ("messages", "messages", "text"),
    ("attachments", "attachments", "body"),
    ("social", "people", "avatar"),
    ("mixes", "mixes", "cover"),
}

#: Functions a question may call. Everything else is refused - including
#: `zeroblob`/`randomblob`, which can allocate as much memory as they are told.
ALLOWED_FUNCTIONS = {
    "count", "sum", "total", "avg", "min", "max", "group_concat",
    "abs", "round", "coalesce", "ifnull", "nullif", "iif", "length", "lower",
    "upper", "trim", "ltrim", "rtrim", "substr", "substring", "replace",
    "instr", "like", "glob", "printf", "format", "cast", "typeof",
    "date", "time", "datetime", "julianday", "strftime", "unixepoch",
    "json_extract", "json_array_length", "json_each", "json_type", "json_valid",
    "row_number", "rank", "dense_rank", "lag", "lead", "ntile",
    "first_value", "last_value", "percent_rank", "cume_dist",
    "max", "min", "char", "hex", "quote", "instr", "sign", "floor", "ceil",
    "ceiling",
}


class QueryError(ValueError):
    """A question that could not be run, phrased so it can be shown."""


def _blob_columns(conn: sqlite3.Connection, alias: str) -> set[tuple[str, str, str]]:
    out = set()
    try:
        tables = [r[0] for r in conn.execute(
            f"SELECT name FROM {alias}.sqlite_master WHERE type='table'")]
        for table in tables:
            for row in conn.execute(f'PRAGMA {alias}.table_info("{table}")'):
                if "BLOB" in (row[2] or "").upper():
                    out.add((alias, table, row[1]))
    except sqlite3.Error:
        pass
    return out


def _open(stores: list[Store], deadline: float) -> sqlite3.Connection:
    """An in-memory connection with `stores` attached read-only and locked."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    secret = set(SECRET_COLUMNS)
    for store in stores:
        uri = "file:" + Path(store.path).as_posix() + "?mode=ro"
        conn.execute("ATTACH DATABASE ? AS " + store.alias, (uri,))
        secret |= _blob_columns(conn, store.alias)
    conn.execute("PRAGMA query_only = 1")

    def authorize(action, arg1, arg2, dbname, _trigger):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            if (dbname, arg1, arg2) in secret:
                return sqlite3.SQLITE_IGNORE
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            return (sqlite3.SQLITE_OK if (arg2 or "").lower() in ALLOWED_FUNCTIONS
                    else sqlite3.SQLITE_DENY)
        if action == getattr(sqlite3, "SQLITE_RECURSIVE", 33):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    conn.set_authorizer(authorize)
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 5000)
    return conn


def _clean(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(value)} bytes>"
    if isinstance(value, float):
        return round(value, 4)
    return value


def _stores_named(sql: str, stores: list[Store]) -> list[Store]:
    lowered = sql.lower()
    return [s for s in stores
            if s.exists and re.search(r"\b" + re.escape(s.alias) + r"\s*\.", lowered)]


def run_query(sql: str, params: Optional[dict] = None, *,
              stores: Optional[list[Store]] = None,
              limit: int = MAX_ROWS, seconds: float = QUERY_SECONDS) -> dict:
    """Run one read-only SELECT across whichever stores it names."""
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise QueryError("There is no query to run.")
    if ";" in sql:
        raise QueryError("One statement at a time.")
    if not re.match(r"(?is)^(select|with)\b", sql):
        raise QueryError("Only SELECT queries can be run here.")
    stores = discover_stores() if stores is None else stores
    named = _stores_named(sql, stores)
    if len(named) > MAX_ATTACHED:
        raise QueryError(f"A query can read at most {MAX_ATTACHED} stores at once.")
    started = time.monotonic()
    try:
        conn = _open(named, started + seconds)
    except sqlite3.Error as exc:
        raise QueryError(f"Could not open the stores: {exc}") from exc
    try:
        cursor = conn.execute(sql, params or {})
        columns = [d[0] for d in cursor.description or []]
        rows = cursor.fetchmany(limit + 1)
    except sqlite3.OperationalError as exc:
        text = str(exc)
        if "interrupted" in text:
            raise QueryError(f"That query ran past {seconds:g}s and was stopped.") from exc
        if "not authorized" in text:
            raise QueryError("That query tried something other than reading.") from exc
        raise QueryError(text) from exc
    except sqlite3.Error as exc:
        if "not authorized" in str(exc):
            raise QueryError("That query tried something other than reading.") from exc
        raise QueryError(str(exc)) from exc
    finally:
        conn.close()
    truncated = len(rows) > limit
    return {
        "columns": columns,
        "rows": [[_clean(v) for v in row] for row in rows[:limit]],
        "truncated": truncated,
        "stores": [s.alias for s in named],
        "ms": round((time.monotonic() - started) * 1000, 1),
    }


# ------------------------------------------------------------------ schema

def schema(stores: Optional[list[Store]] = None) -> list[dict]:
    """Every store, every table, its columns and its live row count."""
    stores = discover_stores() if stores is None else stores
    out = []
    for store in stores:
        entry = {"alias": store.alias, "file": store.filename, "env": store.env,
                 "exists": store.exists, "bytes": store.size(), "tables": []}
        if store.exists:
            try:
                conn = _open([store], time.monotonic() + QUERY_SECONDS)
                conn.set_authorizer(None)  # our own fixed queries, not a question
                tables = [r[0] for r in conn.execute(
                    f"SELECT name FROM {store.alias}.sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
                for table in tables:
                    cols = [{"name": r[1], "type": r[2]} for r in conn.execute(
                        f'PRAGMA {store.alias}.table_info("{table}")')]
                    rows = conn.execute(
                        f'SELECT COUNT(*) FROM {store.alias}."{table}"').fetchone()[0]
                    entry["tables"].append({"name": table, "rows": rows, "columns": cols})
                conn.close()
            except sqlite3.Error as exc:
                entry["error"] = str(exc)
        out.append(entry)
    return out


def schema_text(described: list[dict]) -> str:
    """The schema as the model reads it: `store.table(col type, ...) -- N rows`."""
    lines = []
    for store in described:
        for table in store["tables"]:
            cols = ", ".join(f"{c['name']} {c['type']}".strip() for c in table["columns"])
            lines.append(f"{store['alias']}.{table['name']}({cols}) -- {table['rows']} rows")
    return "\n".join(lines)


# ---------------------------------------------------------------- snapshot

DAY = 86400.0

#: The dashboard. Each entry is (key, store aliases needed, SQL). A metric
#: whose store does not exist yet is reported as None - "no such file" and
#: "zero rows" are different facts and the page says which.
FRIENDS_CTE = (
    "friends AS (SELECT a.follower AS user_id, COUNT(*) AS n "
    "FROM social.follows a JOIN social.follows b "
    "ON b.follower = a.followee AND b.followee = a.follower GROUP BY a.follower)"
)

METRICS: list[tuple[str, tuple[str, ...], str]] = [
    ("accounts_total", ("accounts",), "SELECT COUNT(*) FROM accounts.accounts"),
    ("accounts_24h", ("accounts",),
     "SELECT COUNT(*) FROM accounts.accounts WHERE created >= :now - 86400"),
    ("accounts_7d", ("accounts",),
     "SELECT COUNT(*) FROM accounts.accounts WHERE created >= :now - 7*86400"),
    ("accounts_30d", ("accounts",),
     "SELECT COUNT(*) FROM accounts.accounts WHERE created >= :now - 30*86400"),
    ("accounts_email", ("accounts",),
     "SELECT COUNT(*) FROM accounts.accounts WHERE email != ''"),
    ("accounts_phone", ("accounts",),
     "SELECT COUNT(*) FROM accounts.accounts WHERE phone != ''"),
    ("accounts_google", ("accounts",),
     "SELECT COUNT(DISTINCT user_id) FROM accounts.identities WHERE provider = 'google'"),
    ("accounts_apple", ("accounts",),
     "SELECT COUNT(DISTINCT user_id) FROM accounts.identities WHERE provider = 'apple'"),
    ("logins_24h", ("accounts",),
     "SELECT COUNT(*) FROM accounts.accounts WHERE last_login >= :now - 86400"),
    ("sessions_live", ("accounts",),
     "SELECT COUNT(*) FROM accounts.sessions WHERE expires > :now"),
    ("listeners_total", ("social",), "SELECT COUNT(*) FROM social.people"),
    ("active_24h", ("social",),
     "SELECT COUNT(*) FROM social.people WHERE last_seen >= :now - 86400"),
    ("active_7d", ("social",),
     "SELECT COUNT(*) FROM social.people WHERE last_seen >= :now - 7*86400"),
    ("handles", ("social",), "SELECT COUNT(*) FROM social.people WHERE handle != ''"),
    ("follows", ("social",), "SELECT COUNT(*) FROM social.follows"),
    ("friendships", ("social",),
     "SELECT COUNT(*) FROM social.follows a JOIN social.follows b "
     "ON b.follower = a.followee AND b.followee = a.follower WHERE a.follower < a.followee"),
    ("accounts_with_friend", ("social", "accounts"),
     f"WITH {FRIENDS_CTE} SELECT COUNT(*) FROM friends f "
     "JOIN accounts.accounts ac ON ac.user_id = f.user_id"),
    ("vibes", ("social",), "SELECT COUNT(*) FROM social.echoes"),
    ("ratings", ("social",), "SELECT COUNT(*) FROM social.ratings"),
    ("mixes_total", ("mixes",), "SELECT COUNT(*) FROM mixes.mixes"),
    ("mixes_public", ("mixes",), "SELECT COUNT(*) FROM mixes.mixes WHERE public = 1"),
    ("mixes_copied", ("mixes",), "SELECT COUNT(*) FROM mixes.mixes WHERE source_id != ''"),
    ("accounts_public_mix", ("mixes", "accounts"),
     "SELECT COUNT(DISTINCT m.user_id) FROM mixes.mixes m "
     "JOIN accounts.accounts ac ON ac.user_id = m.user_id WHERE m.public = 1"),
    ("messages", ("messages",), "SELECT COUNT(*) FROM messages.messages"),
    ("messages_24h", ("messages",),
     "SELECT COUNT(*) FROM messages.messages WHERE at >= :now - 86400"),
    ("threads", ("messages",), "SELECT COUNT(DISTINCT thread) FROM messages.messages"),
    ("events", ("myfam",), "SELECT COUNT(*) FROM myfam.events"),
    ("searches_24h", ("myfam",),
     "SELECT COUNT(*) FROM myfam.events WHERE kind = 'search' AND at >= :now - 86400"),
    ("plays_24h", ("myfam",),
     "SELECT COUNT(*) FROM myfam.events WHERE kind = 'play' AND at >= :now - 86400"),
    ("plays_7d", ("myfam",),
     "SELECT COUNT(*) FROM myfam.events WHERE kind = 'play' AND at >= :now - 7*86400"),
    ("completes_7d", ("myfam",),
     "SELECT COUNT(*) FROM myfam.events WHERE kind = 'complete' AND at >= :now - 7*86400"),
    ("episodes_cached", ("scripts",), "SELECT COUNT(*) FROM scripts.scripts"),
    ("episodes_current", ("scripts",),
     "SELECT COUNT(*) FROM scripts.scripts WHERE fresh_until > :now OR "
     "(fresh_until = 0 AND expires > :now)"),
    ("episodes_24h", ("scripts",),
     "SELECT COUNT(*) FROM scripts.scripts WHERE created >= :now - 86400"),
    ("audio_kept", ("scripts",), "SELECT COUNT(*) FROM scripts.episode_audio"),
    ("audio_bytes", ("scripts",),
     "SELECT COALESCE(SUM(bytes), 0) FROM scripts.episode_audio"),
    ("saved", ("saved",), "SELECT COUNT(*) FROM saved.items"),
    ("shares", ("shares",), "SELECT COUNT(*) FROM shares.shares"),
    ("share_opens", ("shares",), "SELECT COALESCE(SUM(opens), 0) FROM shares.shares"),
    ("generated_30d", ("metering",),
     "SELECT COUNT(*) FROM metering.usage WHERE at >= :now - 30*86400"),
    ("cost_30d", ("metering",),
     "SELECT COALESCE(SUM(cost_usd), 0) FROM metering.usage WHERE at >= :now - 30*86400"),
    ("cost_24h", ("metering",),
     "SELECT COALESCE(SUM(cost_usd), 0) FROM metering.usage WHERE at >= :now - 86400"),
]


def _internal(sql: str, stores: dict[str, Store], names: tuple[str, ...],
              params: dict):
    """One of our own fixed queries, through the same sandbox as a question."""
    if not all(n in stores and stores[n].exists for n in names):
        return None
    result = run_query(sql, params, stores=[stores[n] for n in names], limit=MAX_ROWS)
    return result


def snapshot(now: Optional[float] = None) -> dict:
    """Everything the dashboard draws, read live."""
    now = time.time() if now is None else now
    all_stores = discover_stores()
    by_alias = {s.alias: s for s in all_stores}
    params = {"now": now}
    metrics: dict[str, object] = {}
    errors: dict[str, str] = {}
    for key, names, sql in METRICS:
        try:
            result = _internal(sql, by_alias, names, params)
            metrics[key] = None if result is None else result["rows"][0][0]
        except QueryError as exc:
            metrics[key] = None
            errors[key] = str(exc)

    def table(names, sql):
        try:
            result = _internal(sql, by_alias, names, params)
        except QueryError as exc:
            return {"columns": [], "rows": [], "error": str(exc)}
        return result or {"columns": [], "rows": []}

    signups = table(("accounts",),
        "SELECT date(created, 'unixepoch') AS day, COUNT(*) AS n "
        "FROM accounts.accounts WHERE created >= :now - 30*86400 "
        "GROUP BY day ORDER BY day")
    # Every day in the window, including the empty ones: a chart that skips
    # zero days draws a gap as a line straight across it.
    counts = {row[0]: row[1] for row in signups.get("rows", [])}
    series = []
    for i in range(29, -1, -1):
        day = time.strftime("%Y-%m-%d", time.gmtime(now - i * DAY))
        series.append({"day": day, "n": counts.get(day, 0)})

    recent = table(("accounts", "social"),
        "SELECT ac.created, ac.email, ac.phone, ac.display_name, "
        "COALESCE(p.handle, '') AS handle, ac.plan, ac.last_login, "
        "COALESCE(p.last_seen, 0) AS last_seen, ac.user_id "
        "FROM accounts.accounts ac LEFT JOIN social.people p ON p.user_id = ac.user_id "
        "ORDER BY ac.created DESC LIMIT 25") if (
            by_alias.get("social") and by_alias["social"].exists) else table(
        ("accounts",),
        "SELECT created, email, phone, display_name, '' AS handle, plan, "
        "last_login, 0 AS last_seen, user_id FROM accounts.accounts "
        "ORDER BY created DESC LIMIT 25")

    top_searches = table(("myfam",),
        "SELECT lower(trim(text)) AS search, COUNT(*) AS n FROM myfam.events "
        "WHERE kind = 'search' AND text != '' AND at >= :now - 7*86400 "
        "GROUP BY search ORDER BY n DESC LIMIT 10")

    stores = [{"alias": s.alias, "file": s.filename, "exists": s.exists,
               "bytes": s.size()} for s in all_stores]
    return {"at": now, "metrics": metrics, "errors": errors,
            "signups": series, "recent_accounts": recent,
            "top_searches": top_searches, "stores": stores}


# ----------------------------------------------------------------- recipes

_WORD_NUMBERS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                 "twenty": 20, "fifty": 50, "hundred": 100}

_COMPARATORS = [
    (r"\b(at least|or more|no fewer than|minimum of|>=)\b", ">="),
    (r"\b(at most|or fewer|or less|no more than|maximum of|<=)\b", "<="),
    (r"\b(over|more than|greater than|above|exceeding|>)\b", ">"),
    (r"\b(under|fewer than|less than|below|<)\b", "<"),
    (r"\b(exactly|precisely|=)\b", "="),
]

_OP_WORDS = {">": "more than", ">=": "at least", "<": "fewer than",
             "<=": "at most", "=": "exactly"}


def _number(text: str) -> Optional[int]:
    match = re.search(r"\b(\d+)\b", text)
    if match:
        return int(match.group(1))
    for word, value in _WORD_NUMBERS.items():
        if re.search(r"\b" + word + r"\b", text):
            return value
    return None


def _comparator(text: str) -> tuple[str, bool]:
    for pattern, op in _COMPARATORS:
        if re.search(pattern, text):
            return op, True
    return ">=", False


def _window(text: str) -> tuple[Optional[float], str]:
    """A time window named in the question, as seconds, and how to say it."""
    match = re.search(r"\b(?:last|past)\s+(\d+)\s+(hour|day|week|month)s?\b", text)
    if match:
        n, unit = int(match.group(1)), match.group(2)
        seconds = {"hour": 3600, "day": DAY, "week": 7 * DAY, "month": 30 * DAY}[unit]
        return n * seconds, f"in the last {n} {unit}{'s' if n != 1 else ''}"
    for pattern, seconds, words in (
        (r"\b(today|last 24 hours|past 24 hours|past day|last day)\b", DAY, "in the last 24 hours"),
        (r"\b(this week|last week|past week|last 7 days)\b", 7 * DAY, "in the last 7 days"),
        (r"\b(this month|last month|past month|last 30 days)\b", 30 * DAY, "in the last 30 days"),
        (r"\b(this year|last year|past year)\b", 365 * DAY, "in the last year"),
    ):
        if re.search(pattern, text):
            return seconds, words
    return None, ""


@dataclass(frozen=True)
class Recipe:
    key: str
    example: str
    #: Every group must match somewhere in the question for the recipe to
    #: apply; the recipe with the most matching groups wins.
    needs: tuple[str, ...]
    build: Callable[[str, float], tuple[str, dict, str]]


def _within(column: str, seconds: Optional[float]) -> str:
    return f" AND {column} >= :now - {int(seconds)}" if seconds else ""


def _count_graph(kind: str):
    """accounts with N friends / followers / people they follow."""
    def build(q, now):
        n = _number(q)
        op, said = _comparator(q)
        if n is None:
            n, op = 1, ">="
        if kind == "friends":
            cte = FRIENDS_CTE
        elif kind == "followers":
            cte = ("friends AS (SELECT followee AS user_id, COUNT(*) AS n "
                   "FROM social.follows GROUP BY followee)")
        else:
            cte = ("friends AS (SELECT follower AS user_id, COUNT(*) AS n "
                   "FROM social.follows GROUP BY follower)")
        noun = {"friends": "friends", "followers": "followers",
                "following": "people they follow"}[kind]
        if op in ("<", "<=", "=") and (n == 0 or op != "="):
            # "fewer than 2 friends" includes accounts with none, who have
            # no row in the CTE at all.
            sql = (f"WITH {cte} SELECT COUNT(*) FROM accounts.accounts ac "
                   f"LEFT JOIN friends f ON f.user_id = ac.user_id "
                   f"WHERE COALESCE(f.n, 0) {op} :n")
        else:
            sql = (f"WITH {cte} SELECT COUNT(*) FROM accounts.accounts ac "
                   f"JOIN friends f ON f.user_id = ac.user_id WHERE f.n {op} :n")
        label = f"Accounts with {_OP_WORDS[op]} {n} {noun}"
        if not said:
            label += " (read as 'at least')"
        return sql, {"n": n}, label
    return build


def _simple(sql: str, label: str, time_column: Optional[str] = None):
    def build(q, now):
        seconds, words = _window(q) if time_column else (None, "")
        full = sql + (_within(time_column, seconds) if seconds else "")
        return full, {}, label + (f" {words}" if words else "")
    return build


RECIPES: list[Recipe] = [
    Recipe("accounts_public_mix", "How many accounts have made public mixes?",
           (r"\b(account|user|people|listener|person)s?\b", r"\bpublic\b", r"\b(mix|mixes|playlist)s?\b"),
           _simple("SELECT COUNT(DISTINCT m.user_id) FROM mixes.mixes m "
                   "JOIN accounts.accounts ac ON ac.user_id = m.user_id WHERE m.public = 1",
                   "Accounts with at least one public mix", "m.created_at")),
    Recipe("accounts_any_mix", "How many accounts have made a mix?",
           (r"\b(account|user|people|listener|person)s?\b", r"\b(mix|mixes|playlist)s?\b", r"\b(made|created|have|has|with|own)\b"),
           _simple("SELECT COUNT(DISTINCT m.user_id) FROM mixes.mixes m "
                   "JOIN accounts.accounts ac ON ac.user_id = m.user_id WHERE 1 = 1",
                   "Accounts with at least one mix", "m.created_at")),
    Recipe("public_mixes", "How many public mixes are there?",
           (r"\bpublic\b", r"\b(mix|mixes|playlist)s?\b"),
           _simple("SELECT COUNT(*) FROM mixes.mixes WHERE public = 1",
                   "Public mixes", "created_at")),
    Recipe("mixes", "How many mixes are there?",
           (r"\b(mix|mixes|playlist)s?\b",),
           _simple("SELECT COUNT(*) FROM mixes.mixes WHERE 1 = 1", "Mixes", "created_at")),
    Recipe("friends_n", "How many accounts have over 5 friends?",
           (r"\bfriends?\b",), _count_graph("friends")),
    Recipe("followers_n", "How many accounts have at least 10 followers?",
           (r"\bfollowers?\b",), _count_graph("followers")),
    Recipe("following_n", "How many accounts follow more than 3 people?",
           (r"\b(following|follow)\b", r"\b(people|accounts|users|others)\b"),
           _count_graph("following")),
    Recipe("accounts_google", "How many accounts signed up with Google?",
           (r"\bgoogle\b",),
           _simple("SELECT COUNT(DISTINCT user_id) FROM accounts.identities "
                   "WHERE provider = 'google'", "Accounts that sign in with Google", "created")),
    Recipe("accounts_apple", "How many accounts use Sign in with Apple?",
           (r"\bapple\b",),
           _simple("SELECT COUNT(DISTINCT user_id) FROM accounts.identities "
                   "WHERE provider = 'apple'", "Accounts that sign in with Apple", "created")),
    Recipe("accounts_phone", "How many accounts signed up with a phone number?",
           (r"\b(account|user|signup|sign up|signed up)s?\b", r"\b(phone|number|sms)\b"),
           _simple("SELECT COUNT(*) FROM accounts.accounts WHERE phone != ''",
                   "Accounts with a phone number", "created")),
    Recipe("messages_senders", "How many accounts have sent a message?",
           (r"\b(account|user|people|listener)s?\b", r"\b(sent|send|message|messaged|messages)\b"),
           _simple("SELECT COUNT(DISTINCT m.sender) FROM messages.messages m "
                   "JOIN accounts.accounts ac ON ac.user_id = m.sender WHERE 1 = 1",
                   "Accounts that have sent a message", "m.at")),
    Recipe("messages", "How many messages have been sent?",
           (r"\bmessages?\b",),
           _simple("SELECT COUNT(*) FROM messages.messages WHERE 1 = 1",
                   "Messages sent", "at")),
    Recipe("never_played", "How many accounts have never played an episode?",
           (r"\b(never|not|no|zero)\b", r"\b(play|played|listen|listened)\b"),
           _simple("SELECT COUNT(*) FROM accounts.accounts ac WHERE NOT EXISTS "
                   "(SELECT 1 FROM myfam.events e WHERE e.user_id = ac.user_id "
                   "AND e.kind IN ('play', 'complete'))",
                   "Accounts that have never played an episode")),
    Recipe("finished", "How many accounts have finished an episode?",
           (r"\b(account|user|people|listener)s?\b", r"\b(finish|finished|complete|completed)\b"),
           _simple("SELECT COUNT(DISTINCT e.user_id) FROM myfam.events e "
                   "JOIN accounts.accounts ac ON ac.user_id = e.user_id "
                   "WHERE e.kind = 'complete'",
                   "Accounts that have finished an episode", "e.at")),
    Recipe("plays", "How many plays were there this week?",
           (r"\b(plays|played|listens|listened)\b",),
           _simple("SELECT COUNT(*) FROM myfam.events WHERE kind = 'play'",
                   "Plays", "at")),
    Recipe("searches", "How many searches today?",
           (r"\b(search|searches|searched)\b",),
           _simple("SELECT COUNT(*) FROM myfam.events WHERE kind = 'search'",
                   "Searches", "at")),
    Recipe("vibes", "How many vibes have been sent?",
           (r"\b(vibe|vibes|echo|echoes)\b",),
           _simple("SELECT COUNT(*) FROM social.echoes WHERE 1 = 1", "Vibes", "at")),
    Recipe("episodes", "How many episodes are in the cache?",
           (r"\b(episode|episodes|script|scripts)\b", r"\b(cache|cached|generated|written|made|exist|are there|total)\b"),
           _simple("SELECT COUNT(*) FROM scripts.scripts WHERE 1 = 1",
                   "Episodes in the shared cache", "created")),
    Recipe("active", "How many listeners were active this week?",
           (r"\b(active|online|seen|opened)\b",),
           lambda q, now: (
               "SELECT COUNT(*) FROM social.people WHERE last_seen >= :now - :s",
               {"s": _window(q)[0] or DAY},
               "Listeners active " + (_window(q)[1] or "in the last 24 hours"))),
    Recipe("accounts", "How many accounts have been created?",
           (r"\b(account|accounts|signups|sign ups|signed up|registered|users)\b",),
           _simple("SELECT COUNT(*) FROM accounts.accounts WHERE 1 = 1",
                   "Accounts created", "created")),
]


def match_recipe(question: str) -> Optional[Recipe]:
    q = " " + question.lower() + " "
    best, best_score = None, 0
    for recipe in RECIPES:
        if all(re.search(p, q) for p in recipe.needs):
            # More groups is a more specific recipe; order breaks ties, so
            # the list is written most-specific first.
            score = len(recipe.needs)
            if score > best_score:
                best, best_score = recipe, score
    return best


# ------------------------------------------------------------------- model

ASK_SYSTEM = """You translate an administrator's question about the FAM app's data into ONE read-only SQLite query.

The data is spread over several SQLite files, each attached under an alias. Always qualify tables with the alias: `accounts.accounts`, `social.follows`, `mixes.mixes`. Tables in different files can be joined on `user_id`.

What things mean:
- An *account* is a row in accounts.accounts (email, phone, display_name, plan, created, last_login). A *listener* is anybody with a session, account or not: social.people has one row per listener (name, handle, joined, last_seen).
- Sign-in routes: email != '' or phone != '' on accounts.accounts; Google/Apple are rows in accounts.identities (provider = 'google' / 'apple').
- social.follows(follower, followee) is one-directional. A *friend* is mutual: both (a,b) and (b,a) exist. Followers of X: rows where followee = X.
- mixes.mixes: DailyFAM mixes; public = 1 means public; source_id != '' means it is a copy of somebody else's mix.
- myfam.events: the interaction log of accounts; kind is one of search, play, complete, skip, pick, impression, share, vibe, save. text is what was typed.
- social.echoes are "vibes". messages.messages are direct messages (sender, recipient, thread, at).
- scripts.scripts is the shared episode cache (query, title, minutes, created, plays, origin, author).
- metering.usage has one row per generated episode with cost_usd.
- Every time column (created, at, joined, last_seen, last_login, updated, created_at, updated_at) is Unix seconds. The current time is given as :now; use `:now - 7*86400` for "last week". Format with datetime(x, 'unixepoch') when showing dates.

Rules: one SELECT (or WITH ... SELECT) statement, no semicolons, no PRAGMA, no writes. Prefer a single number when the question asks "how many". Name output columns readably. Add LIMIT 100 to anything that lists rows. Some columns (passwords, tokens, message text, binary data) always read as NULL - never select them.

Reply with JSON: {"sql": "...", "label": "a short noun phrase naming what the result is, e.g. 'Accounts with a public mix'"}."""

ASK_SCHEMA = {
    "type": "object",
    "properties": {"sql": {"type": "string"}, "label": {"type": "string"}},
    "required": ["sql", "label"],
    "additionalProperties": False,
}


def ask_model() -> str:
    from config import settings
    return os.environ.get("ADMIN_ASK_MODEL", "").strip() or settings.ei_model


async def _model_sql(question: str, schema_lines: str, error: str = "",
                     previous: str = "") -> dict:
    import credentials
    from anthropic_client import build_async_client

    key = credentials.active("ANTHROPIC_API_KEY")
    if not key:
        raise QueryError("no-key")
    content = f"Schema (live):\n{schema_lines}\n\nQuestion: {question}"
    if error:
        content += (f"\n\nYour previous query was:\n{previous}\n"
                    f"It failed with: {error}\nWrite a corrected query.")
    client = build_async_client(key)
    response = await asyncio.wait_for(
        client.messages.create(
            model=ask_model(),
            max_tokens=1500,
            system=ASK_SYSTEM,
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": ASK_SCHEMA}},
            messages=[{"role": "user", "content": content}],
        ),
        timeout=30.0,
    )
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def _answer_text(label: str, result: dict) -> str:
    rows, cols = result["rows"], result["columns"]
    if len(rows) == 1 and len(cols) == 1:
        value = rows[0][0]
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return f"{label}: {value if value is not None else 'none'}"
    if not rows:
        return f"{label}: no rows."
    more = " (first 500)" if result.get("truncated") else ""
    return f"{label}: {len(rows)} row{'s' if len(rows) != 1 else ''}{more}."


async def ask(question: str, now: Optional[float] = None) -> dict:
    """Answer a question, by recipe if one fits and by the model otherwise."""
    question = " ".join((question or "").split())[:500]
    if not question:
        raise QueryError("Ask a question first.")
    now = time.time() if now is None else now

    recipe = match_recipe(question)
    if recipe is not None:
        sql, params, label = recipe.build(question.lower(), now)
        params = {"now": now, **params}
        result = await asyncio.to_thread(run_query, sql, params)
        return {"question": question, "answer": _answer_text(label, result),
                "label": label, "sql": sql, "params": params,
                "source": "recipe", "recipe": recipe.key, **result}

    stores = discover_stores()
    lines = schema_text(await asyncio.to_thread(schema, stores))
    try:
        draft = await _model_sql(question, lines)
    except QueryError:
        raise QueryError(
            "No built-in question matches that, and there is no Anthropic key on "
            "this server to write one. Try one of the suggested questions, or "
            "write the SQL yourself below.")
    except Exception as exc:  # noqa: BLE001 - say what happened, never hang
        raise QueryError(f"The model could not be asked ({exc}).") from exc
    sql = draft.get("sql", "")
    try:
        result = await asyncio.to_thread(run_query, sql, {"now": now}, stores=stores)
    except QueryError as first:
        try:
            draft = await _model_sql(question, lines, error=str(first), previous=sql)
            sql = draft.get("sql", "")
            result = await asyncio.to_thread(run_query, sql, {"now": now},
                                             stores=stores)
        except QueryError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise QueryError(f"{first} (and the retry failed: {exc})") from exc
    label = draft.get("label") or "Result"
    return {"question": question, "answer": _answer_text(label, result),
            "label": label, "sql": sql, "params": {"now": now},
            "source": "model", "model": ask_model(), **result}


def suggestions() -> list[str]:
    return [r.example for r in RECIPES]
